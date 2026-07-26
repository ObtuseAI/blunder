from __future__ import annotations

from collections import deque
from typing import Mapping, Sequence

from blunder.creative_direction import canonical_digest, required_text


AUTOMATION_SCHEMA = "obtuse.blunder.automation-graph.v1"
NODE_KINDS = frozenset({"trigger", "transform", "decision", "ai", "action", "human", "checkpoint", "terminal"})
RISK_LEVELS = frozenset({"none", "low", "medium", "high", "critical"})


def object_sequence(value: object, key: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"Automation {key} must be a non-empty object list.")
    return tuple(item for item in value if isinstance(item, dict))


def parse_node(value: Mapping[str, object]) -> dict[str, object]:
    node_id = required_text(value, "id", "Automation node", 80)
    kind = str(value.get("kind") or "").strip().lower()
    risk = str(value.get("risk") or "").strip().lower()
    if kind not in NODE_KINDS:
        raise ValueError(f"Automation node {node_id} has unsupported kind {kind}.")
    if risk not in RISK_LEVELS:
        raise ValueError(f"Automation node {node_id} has unsupported risk {risk}.")
    idempotency_key = str(value.get("idempotencyKey") or "").strip()
    compensation = str(value.get("compensation") or "").strip()
    timeout_seconds = value.get("timeoutSeconds")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds < 1 or timeout_seconds > 86_400:
        raise ValueError(f"Automation node {node_id} requires timeoutSeconds from 1 through 86400.")
    if kind in {"action", "ai"} and not idempotency_key:
        raise ValueError(f"Automation node {node_id} requires an idempotencyKey.")
    if risk in {"high", "critical"} and not compensation:
        raise ValueError(f"Automation node {node_id} requires an explicit compensation strategy.")
    return {
        "id": node_id,
        "kind": kind,
        "label": required_text(value, "label", f"Automation node {node_id}", 120),
        "risk": risk,
        "timeoutSeconds": timeout_seconds,
        "idempotencyKey": idempotency_key or None,
        "compensation": compensation or None,
        "inputSchema": value.get("inputSchema") if isinstance(value.get("inputSchema"), dict) else {"type": "object"},
        "outputSchema": value.get("outputSchema") if isinstance(value.get("outputSchema"), dict) else {"type": "object"},
    }


def parse_edge(value: Mapping[str, object]) -> dict[str, str]:
    return {
        "from": required_text(value, "from", "Automation edge", 80),
        "to": required_text(value, "to", "Automation edge", 80),
        "condition": str(value.get("condition") or "always").strip(),
    }


def topological_order(nodes: Sequence[Mapping[str, object]], edges: Sequence[Mapping[str, str]]) -> tuple[str, ...]:
    identifiers = tuple(str(item["id"]) for item in nodes)
    known = frozenset(identifiers)
    if len(known) != len(identifiers):
        raise ValueError("Automation graph contains duplicate node ids.")
    unknown = sorted({edge[side] for edge in edges for side in ("from", "to")}.difference(known))
    if unknown:
        raise ValueError(f"Automation edges reference unknown nodes: {', '.join(unknown)}")
    edge_keys = tuple((edge["from"], edge["to"], edge["condition"]) for edge in edges)
    if len(set(edge_keys)) != len(edge_keys):
        raise ValueError("Automation graph contains duplicate edges.")
    incoming = {identifier: 0 for identifier in identifiers}
    outgoing: dict[str, list[str]] = {identifier: [] for identifier in identifiers}
    for edge in edges:
        incoming[edge["to"]] += 1
        outgoing[edge["from"]].append(edge["to"])
    ready = deque(identifier for identifier in identifiers if incoming[identifier] == 0)
    order: list[str] = []
    while ready:
        current = ready.popleft()
        order.append(current)
        for target in outgoing[current]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    if len(order) != len(identifiers):
        raise ValueError("Automation graph must be acyclic; model retries as bounded node policy, not graph cycles.")
    return tuple(order)


def reachable_nodes(start: str, edges: Sequence[Mapping[str, str]], reverse: bool) -> frozenset[str]:
    source_key, target_key = ("to", "from") if reverse else ("from", "to")
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge[source_key], []).append(edge[target_key])
    pending = deque((start,))
    observed: set[str] = set()
    while pending:
        current = pending.popleft()
        if current in observed:
            continue
        observed.add(current)
        pending.extend(adjacency.get(current, ()))
    return frozenset(observed)


def graph_flow_findings(
    nodes: Sequence[Mapping[str, object]],
    edges: Sequence[Mapping[str, str]],
    trigger: str,
    terminals: Sequence[str],
) -> tuple[str, ...]:
    identifiers = frozenset(str(node["id"]) for node in nodes)
    from_trigger = reachable_nodes(trigger, edges, False)
    to_terminal = frozenset().union(*(reachable_nodes(terminal, edges, True) for terminal in terminals))
    findings = [f"unreachable-from-trigger:{item}" for item in sorted(identifiers.difference(from_trigger))]
    findings.extend(f"cannot-reach-terminal:{item}" for item in sorted(identifiers.difference(to_terminal)))
    incoming_trigger = tuple(edge for edge in edges if edge["to"] == trigger)
    outgoing_terminal = tuple(edge for edge in edges if edge["from"] in terminals)
    if incoming_trigger:
        findings.append("trigger-has-incoming-edge")
    if outgoing_terminal:
        findings.append("terminal-has-outgoing-edge")
    return tuple(findings)


def high_risk_gate_findings(nodes: Sequence[Mapping[str, object]], edges: Sequence[Mapping[str, str]]) -> tuple[str, ...]:
    by_id = {str(node["id"]): node for node in nodes}
    predecessors: dict[str, tuple[str, ...]] = {
        identifier: tuple(edge["from"] for edge in edges if edge["to"] == identifier)
        for identifier in by_id
    }
    return tuple(
        str(node["id"])
        for node in nodes
        if str(node["risk"]) in {"high", "critical"}
        and not any(str(by_id[source]["kind"]) == "human" for source in predecessors[str(node["id"])])
    )


def compile_automation(value: Mapping[str, object]) -> dict[str, object]:
    name = required_text(value, "name", "Automation", 100)
    outcome = required_text(value, "outcome", "Automation", 1200)
    nodes = tuple(parse_node(item) for item in object_sequence(value.get("nodes"), "nodes"))
    edges = tuple(parse_edge(item) for item in object_sequence(value.get("edges"), "edges"))
    order = topological_order(nodes, edges)
    triggers = tuple(str(node["id"]) for node in nodes if node["kind"] == "trigger")
    terminals = tuple(str(node["id"]) for node in nodes if node["kind"] == "terminal")
    if len(triggers) != 1 or not terminals:
        raise ValueError("Automation graph requires exactly one trigger and at least one terminal node.")
    flow_findings = graph_flow_findings(nodes, edges, triggers[0], terminals)
    if flow_findings:
        raise ValueError(f"Automation graph flow is invalid: {', '.join(flow_findings)}")
    ungated = high_risk_gate_findings(nodes, edges)
    report: dict[str, object] = {
        "schema": AUTOMATION_SCHEMA,
        "status": "BLOCKED_HIGH_RISK_GATE_MISSING" if ungated else "READY_FOR_DRY_RUN",
        "name": name,
        "outcome": outcome,
        "nodes": list(nodes),
        "edges": list(edges),
        "topologicalOrder": list(order),
        "stateMachine": ["pending", "ready", "running", "waiting-human", "retrying", "compensating", "completed", "failed", "cancelled"],
        "runtimeContract": {
            "durableState": True,
            "resumable": True,
            "boundedRetries": True,
            "idempotencyRequired": True,
            "compensationRequiredForHighRisk": True,
            "traceEveryTransition": True,
        },
        "ungatedHighRiskNodes": list(ungated),
        "authority": {"execute": False, "externalEffects": False, "schedule": False},
        "providerCalls": 0,
        "sourceMutation": False,
    }
    report["digest"] = canonical_digest(report)
    return report
