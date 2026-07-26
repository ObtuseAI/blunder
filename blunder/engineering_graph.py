from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

from blunder.api.contracts import canonical_sha256
from blunder.api.events import TaskEventLedger
from blunder.environment_automation import detect_project_stacks
from blunder.promotion_policy import PromotionLedger
from blunder.repository_intelligence import build_repository_index, import_matches


ROUTE_PATTERN = re.compile(r'parsed\.path\s*(?:==|\.startswith\()\s*["\']([^"\']+)')
HTML_ID_PATTERN = re.compile(r'\bid=["\']([^"\']+)["\']')
HEADING_PATTERN = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
TERM_PATTERN = re.compile(r"[a-z0-9]+")
COMPONENT_PATTERN = re.compile(r"(?:const|function)\s+([A-Z][A-Za-z0-9]+)")
CSS_TOKEN_PATTERN = re.compile(r"--([a-z0-9-]+)\s*:")


@dataclass(frozen=True)
class GraphNodeV1:
    node_id: str
    layer: str
    kind: str
    label: str
    source_path: str
    evidence_digest: str
    attributes_json: str


@dataclass(frozen=True)
class GraphEdgeV1:
    source_id: str
    target_id: str
    kind: str
    evidence_digest: str


def node_id(layer: str, kind: str, identity: str) -> str:
    return f"node-{canonical_sha256({'layer': layer, 'kind': kind, 'identity': identity})[:24].lower()}"


def source_nodes(index: Mapping[str, object]) -> tuple[GraphNodeV1, ...]:
    raw_records = index.get("records")
    records = [item for item in raw_records if isinstance(item, dict)] if isinstance(raw_records, list) else []
    nodes: list[GraphNodeV1] = []
    for record in records:
        path = str(record.get("path") or "")
        digest = str(record.get("sha256") or "")
        kind = "test" if record.get("test_file") is True else "source"
        layer = "test" if kind == "test" else "source"
        attributes = {
            "bytes": record.get("bytes"),
            "lines": record.get("lines"),
            "symbols": record.get("symbols"),
            "imports": record.get("imports"),
            "gitChangeCount": record.get("git_change_count"),
            "currentlyChanged": record.get("currently_changed"),
        }
        nodes.append(
            GraphNodeV1(
                node_id(layer, kind, path),
                layer,
                kind,
                path,
                path,
                digest,
                json.dumps(attributes, separators=(",", ":"), sort_keys=True),
            )
        )
    return tuple(nodes)


def interface_nodes(repository_root: Path) -> tuple[GraphNodeV1, ...]:
    nodes: list[GraphNodeV1] = []
    server = repository_root / "blunder" / "shell" / "server.py"
    if server.is_file():
        text = server.read_text(encoding="utf-8")
        digest = canonical_sha256(text)
        for route in sorted(set(ROUTE_PATTERN.findall(text))):
            nodes.append(
                GraphNodeV1(
                    node_id("interface", "http-route", route),
                    "interface",
                    "http-route",
                    route,
                    server.relative_to(repository_root).as_posix(),
                    digest,
                    "{}",
                )
            )
    html = repository_root / "blunder" / "shell" / "static" / "index.html"
    if html.is_file():
        text = html.read_text(encoding="utf-8")
        digest = canonical_sha256(text)
        for element_id in sorted(set(HTML_ID_PATTERN.findall(text))):
            nodes.append(
                GraphNodeV1(
                    node_id("interface", "ui-element", element_id),
                    "interface",
                    "ui-element",
                    element_id,
                    html.relative_to(repository_root).as_posix(),
                    digest,
                    "{}",
                )
            )
    return tuple(nodes)


def decision_nodes(repository_root: Path) -> tuple[GraphNodeV1, ...]:
    nodes: list[GraphNodeV1] = []
    for path in sorted((repository_root / "docs").glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        digest = canonical_sha256(text)
        relative = path.relative_to(repository_root).as_posix()
        for marker, heading in HEADING_PATTERN.findall(text):
            label = heading.strip()
            kind = "decision" if any(term in label.lower() for term in ("decision", "boundary", "assumption", "roadmap")) else "requirement"
            layer = "decision" if kind == "decision" else "requirement"
            identity = f"{relative}#{label}"
            nodes.append(
                GraphNodeV1(
                    node_id(layer, kind, identity),
                    layer,
                    kind,
                    label,
                    relative,
                    digest,
                    json.dumps({"headingLevel": len(marker)}, separators=(",", ":")),
                )
            )
    return tuple(nodes)


def product_nodes(repository_root: Path) -> tuple[GraphNodeV1, ...]:
    nodes: list[GraphNodeV1] = []
    desktop_root = repository_root / "desktop" / "src"
    for path in sorted(desktop_root.glob("*.tsx")):
        text = path.read_text(encoding="utf-8")
        digest = canonical_sha256(text)
        relative = path.relative_to(repository_root).as_posix()
        for component in sorted(set(COMPONENT_PATTERN.findall(text))):
            nodes.append(
                GraphNodeV1(
                    node_id("product", "react-component", f"{relative}:{component}"),
                    "product",
                    "react-component",
                    component,
                    relative,
                    digest,
                    "{}",
                )
            )
    styles = desktop_root / "styles.css"
    if styles.is_file():
        text = styles.read_text(encoding="utf-8")
        digest = canonical_sha256(text)
        relative = styles.relative_to(repository_root).as_posix()
        for token in sorted(set(CSS_TOKEN_PATTERN.findall(text))):
            nodes.append(
                GraphNodeV1(
                    node_id("product", "design-token", token),
                    "product",
                    "design-token",
                    token,
                    relative,
                    digest,
                    "{}",
                )
            )
    return tuple(nodes)


def runtime_nodes(repository_root: Path) -> tuple[GraphNodeV1, ...]:
    detection = detect_project_stacks(repository_root)
    evidence_digest = canonical_sha256(asdict(detection))
    nodes = [
        GraphNodeV1(
            node_id("runtime", "stack", stack),
            "runtime",
            "stack",
            stack,
            next((marker for marker in detection.lockfiles if stack.split("-", 1)[0] in marker.lower()), detection.lockfiles[0] if detection.lockfiles else "."),
            evidence_digest,
            json.dumps({"lockfiles": list(detection.lockfiles)}, separators=(",", ":"), sort_keys=True),
        )
        for stack in detection.stacks
    ]
    return tuple(nodes)


def activity_nodes(repository_root: Path) -> tuple[GraphNodeV1, ...]:
    nodes: list[GraphNodeV1] = []
    event_page = TaskEventLedger(repository_root).public_events_after(0, None, 500)
    for raw in event_page["events"]:
        if not isinstance(raw, dict):
            continue
        event_digest = str(raw.get("eventDigest") or "")
        event_id = str(raw.get("eventId") or "")
        nodes.append(
            GraphNodeV1(
                node_id("activity", "task-event", event_id),
                "activity",
                "task-event",
                str(raw.get("kind") or "task-event"),
                "artifacts/obtuse/blunder-vnext-v1/events/index.json",
                event_digest,
                json.dumps(
                    {"taskId": raw.get("taskId"), "sequence": raw.get("sequence"), "state": raw.get("state")},
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        )
    for record in PromotionLedger(repository_root).records()[-500:]:
        decision = record.get("decision")
        if not isinstance(decision, dict):
            continue
        decision_digest = str(decision.get("decisionDigest") or "")
        nodes.append(
            GraphNodeV1(
                node_id("evidence", "promotion-decision", decision_digest),
                "evidence",
                "promotion-decision",
                str(decision.get("status") or "promotion-decision"),
                "artifacts/obtuse/blunder-vnext-v1/promotions/index.json",
                str(record.get("recordDigest") or ""),
                json.dumps(
                    {"scope": decision.get("scope"), "candidateIdentity": decision.get("candidateIdentity")},
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        )
    return tuple(nodes)


def graph_edges(index: Mapping[str, object], nodes: Sequence[GraphNodeV1]) -> tuple[GraphEdgeV1, ...]:
    by_path = {node.source_path: node for node in nodes if node.kind in {"source", "test"}}
    raw_records = index.get("records")
    records = [item for item in raw_records if isinstance(item, dict)] if isinstance(raw_records, list) else []
    edges: set[GraphEdgeV1] = set()
    for record in records:
        path = str(record.get("path") or "")
        source = by_path.get(path)
        if source is None:
            continue
        imports = record.get("imports")
        import_names = [str(item) for item in imports] if isinstance(imports, list) else []
        for target_path, target in by_path.items():
            if target_path == path:
                continue
            if any(import_matches(name, target_path) for name in import_names):
                digest = canonical_sha256({"source": source.node_id, "target": target.node_id, "kind": "imports"})
                edges.add(GraphEdgeV1(source.node_id, target.node_id, "imports", digest))
        if source.kind == "test":
            lowered = path.lower()
            for target_path, target in by_path.items():
                if target.kind == "source" and Path(target_path).stem.lower().removeprefix("test_") in lowered:
                    digest = canonical_sha256({"source": source.node_id, "target": target.node_id, "kind": "tests"})
                    edges.add(GraphEdgeV1(source.node_id, target.node_id, "tests", digest))
    source_by_path = {node.source_path: node for node in nodes if node.kind in {"source", "test"}}
    for node in nodes:
        source = source_by_path.get(node.source_path)
        if source is not None and source.node_id != node.node_id:
            digest = canonical_sha256({"source": node.node_id, "target": source.node_id, "kind": "defined-in"})
            edges.add(GraphEdgeV1(node.node_id, source.node_id, "defined-in", digest))
    return tuple(sorted(edges, key=lambda edge: (edge.source_id, edge.target_id, edge.kind)))


def projection_path(repository_root: Path) -> Path:
    return repository_root / "artifacts" / "obtuse" / "blunder-vnext-v1" / "engineering-graph.sqlite3"


def build_engineering_graph(repository_root: Path) -> dict[str, object]:
    resolved = repository_root.resolve()
    index = build_repository_index(resolved, True)
    nodes = tuple(
        sorted(
            (
                *source_nodes(index),
                *interface_nodes(resolved),
                *product_nodes(resolved),
                *runtime_nodes(resolved),
                *decision_nodes(resolved),
                *activity_nodes(resolved),
            ),
            key=lambda item: item.node_id,
        )
    )
    edges = graph_edges(index, nodes)
    snapshot_payload = {
        "sourceFingerprint": index.get("fingerprint"),
        "nodes": [asdict(item) for item in nodes],
        "edges": [asdict(item) for item in edges],
    }
    snapshot_digest = canonical_sha256(snapshot_payload)
    path = projection_path(resolved)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY,
                layer TEXT NOT NULL,
                kind TEXT NOT NULL,
                label TEXT NOT NULL,
                source_path TEXT NOT NULL,
                evidence_digest TEXT NOT NULL,
                attributes_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS edges (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                evidence_digest TEXT NOT NULL,
                PRIMARY KEY (source_id, target_id, kind),
                FOREIGN KEY (source_id) REFERENCES nodes(node_id),
                FOREIGN KEY (target_id) REFERENCES nodes(node_id)
            );
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            DELETE FROM edges;
            DELETE FROM nodes;
            DELETE FROM metadata;
            """
        )
        connection.executemany(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)",
            ((item.node_id, item.layer, item.kind, item.label, item.source_path, item.evidence_digest, item.attributes_json) for item in nodes),
        )
        connection.executemany(
            "INSERT INTO edges VALUES (?, ?, ?, ?)",
            ((item.source_id, item.target_id, item.kind, item.evidence_digest) for item in edges),
        )
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            (
                ("schema", "obtuse.blunder.engineering-graph.v1"),
                ("sourceFingerprint", str(index.get("fingerprint") or "")),
                ("snapshotDigest", snapshot_digest),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    layers: dict[str, int] = {}
    for node in nodes:
        layers[node.layer] = layers.get(node.layer, 0) + 1
    return {
        "schema": "obtuse.blunder.engineering-graph.v1",
        "status": "REBUILT_FROM_CANONICAL_SOURCE",
        "sourceFingerprint": index.get("fingerprint"),
        "snapshotDigest": snapshot_digest,
        "projectionPath": path.relative_to(resolved).as_posix(),
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "layers": dict(sorted(layers.items())),
    }


def query_engineering_graph(repository_root: Path, query: str, limit: int) -> dict[str, object]:
    if limit < 1 or limit > 200:
        raise ValueError("Engineering Graph query limit must be between 1 and 200.")
    summary = build_engineering_graph(repository_root)
    terms = set(TERM_PATTERN.findall(query.lower()))
    connection = sqlite3.connect(projection_path(repository_root.resolve()))
    try:
        rows = connection.execute(
            "SELECT node_id, layer, kind, label, source_path, evidence_digest, attributes_json FROM nodes"
        ).fetchall()
    finally:
        connection.close()
    ranked: list[tuple[int, tuple[object, ...]]] = []
    for row in rows:
        haystack = f"{row[1]} {row[2]} {row[3]} {row[4]}".lower()
        score = sum(5 for term in terms if term in haystack)
        if not terms or score > 0:
            ranked.append((score, row))
    ranked.sort(key=lambda item: (-item[0], str(item[1][4]), str(item[1][3])))
    results = [
        {
            "nodeId": row[0],
            "layer": row[1],
            "kind": row[2],
            "label": row[3],
            "sourcePath": row[4],
            "evidenceDigest": row[5],
            "attributes": json.loads(str(row[6])),
            "score": score,
        }
        for score, row in ranked[:limit]
    ]
    return summary | {"query": query, "results": results}
