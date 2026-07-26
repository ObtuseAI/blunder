from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

from blunder.repository_intelligence import build_repository_index, import_matches


GRAPH_SCHEMA = "obtuse.blunder.quorum-semantic-graph.v1"


@dataclass(frozen=True)
class SemanticNode:
    node_id: str
    kind: str
    label: str
    path: str
    content_sha256: str
    attributes: dict[str, object]


@dataclass(frozen=True)
class SemanticEdge:
    edge_id: str
    source: str
    target: str
    relation: str
    propagation: str


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def stable_id(kind: str, identity: str) -> str:
    digest = hashlib.sha256(f"{kind}\0{identity}".encode("utf-8")).hexdigest().upper()[:20]
    return f"{kind}-{digest}"


def object_list(value: object) -> list[dict[str, object]]:
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def string_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, (list, tuple)) else []


def file_node(record: Mapping[str, object]) -> SemanticNode:
    path = str(record.get("path") or "")
    return SemanticNode(
        node_id=stable_id("file", path),
        kind="file",
        label=Path(path).name,
        path=path,
        content_sha256=str(record.get("sha256") or ""),
        attributes={
            "bytes": int(record.get("bytes") or 0),
            "lines": int(record.get("lines") or 0),
            "testFile": record.get("test_file") is True,
            "currentlyChanged": record.get("currently_changed") is True,
            "gitChangeCount": int(record.get("git_change_count") or 0),
        },
    )


def symbol_nodes(record: Mapping[str, object]) -> tuple[SemanticNode, ...]:
    path = str(record.get("path") or "")
    content_sha256 = str(record.get("sha256") or "")
    return tuple(
        SemanticNode(
            node_id=stable_id("symbol", f"{path}#{symbol}"),
            kind="symbol",
            label=symbol,
            path=path,
            content_sha256=content_sha256,
            attributes={"ownerFile": stable_id("file", path)},
        )
        for symbol in sorted(set(string_list(record.get("symbols"))))
        if symbol
    )


def semantic_edge(source: str, target: str, relation: str, propagation: str) -> SemanticEdge:
    return SemanticEdge(
        edge_id=stable_id("edge", f"{source}\0{relation}\0{target}"),
        source=source,
        target=target,
        relation=relation,
        propagation=propagation,
    )


def import_edges(records: Sequence[Mapping[str, object]]) -> tuple[SemanticEdge, ...]:
    paths = tuple(sorted(str(record.get("path") or "") for record in records if record.get("path")))
    edges: list[SemanticEdge] = []
    observed: set[tuple[str, str]] = set()
    for record in records:
        source_path = str(record.get("path") or "")
        for import_name in sorted(set(string_list(record.get("imports")))):
            matches = tuple(path for path in paths if path != source_path and import_matches(import_name, path))
            for target_path in matches:
                identity = (source_path, target_path)
                if identity in observed:
                    continue
                observed.add(identity)
                edges.append(
                    semantic_edge(
                        stable_id("file", source_path),
                        stable_id("file", target_path),
                        "imports",
                        "target-change-invalidates-source",
                    )
                )
    return tuple(edges)


def build_quorum_graph(root: Path, persist_repository_index: bool) -> dict[str, object]:
    resolved = root.resolve()
    repository = build_repository_index(resolved, persist_repository_index)
    records = object_list(repository.get("records"))
    nodes: list[SemanticNode] = []
    edges: list[SemanticEdge] = []
    for record in records:
        parent = file_node(record)
        nodes.append(parent)
        for symbol in symbol_nodes(record):
            nodes.append(symbol)
            edges.append(semantic_edge(parent.node_id, symbol.node_id, "contains", "source-change-invalidates-symbol"))
    edges.extend(import_edges(records))
    ordered_nodes = sorted(nodes, key=lambda item: (item.kind, item.path, item.label, item.node_id))
    ordered_edges = sorted(edges, key=lambda item: (item.relation, item.source, item.target, item.edge_id))
    fingerprint_payload = {
        "nodes": [asdict(item) for item in ordered_nodes],
        "edges": [asdict(item) for item in ordered_edges],
    }
    changed = [item.path for item in ordered_nodes if item.kind == "file" and item.attributes.get("currentlyChanged") is True]
    return {
        "schema": GRAPH_SCHEMA,
        "status": "PASS",
        "fingerprint": canonical_sha256(fingerprint_payload),
        "repositoryFingerprint": repository.get("fingerprint"),
        "nodes": fingerprint_payload["nodes"],
        "edges": fingerprint_payload["edges"],
        "counts": {
            "nodes": len(ordered_nodes),
            "files": sum(1 for item in ordered_nodes if item.kind == "file"),
            "symbols": sum(1 for item in ordered_nodes if item.kind == "symbol"),
            "edges": len(ordered_edges),
            "imports": sum(1 for item in ordered_edges if item.relation == "imports"),
            "changedFiles": len(changed),
        },
        "changedPaths": changed,
        "authority": {
            "sourceFilesRemainCanonical": True,
            "graphIsDerived": True,
            "sourceMutation": False,
            "runtimeReplacement": False,
        },
    }


def graph_parts(graph: Mapping[str, object]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if graph.get("schema") != GRAPH_SCHEMA:
        raise ValueError("Impact analysis requires a supported Quorum semantic graph.")
    return object_list(graph.get("nodes")), object_list(graph.get("edges"))


def propagation_adjacency(edges: Sequence[Mapping[str, object]]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        relation = str(edge.get("relation") or "")
        if relation == "contains":
            adjacency.setdefault(source, set()).add(target)
        elif relation == "imports":
            adjacency.setdefault(target, set()).add(source)
    return adjacency


def impact_frontier(
    graph: Mapping[str, object],
    changed_paths: Sequence[str],
    maximum_depth: int,
) -> dict[str, object]:
    if maximum_depth < 1 or maximum_depth > 8:
        raise ValueError("Impact maximumDepth must be from one through eight.")
    nodes, edges = graph_parts(graph)
    path_to_file = {
        str(node.get("path") or ""): str(node.get("node_id") or "")
        for node in nodes
        if node.get("kind") == "file"
    }
    normalized_paths = tuple(dict.fromkeys(path.replace("\\", "/").strip("/") for path in changed_paths if path.strip()))
    if not normalized_paths:
        raise ValueError("Impact analysis requires at least one changed path.")
    missing = tuple(path for path in normalized_paths if path not in path_to_file)
    if missing:
        raise ValueError(f"Impact paths are absent from the semantic graph: {', '.join(missing)}")
    node_by_id = {str(node.get("node_id") or ""): node for node in nodes}
    adjacency = propagation_adjacency(edges)
    current = {path_to_file[path] for path in normalized_paths}
    visited = set(current)
    layers: list[dict[str, object]] = []
    for depth in range(maximum_depth + 1):
        if not current:
            break
        layer_nodes = [node_by_id[node_id] for node_id in sorted(current) if node_id in node_by_id]
        layers.append(
            {
                "depth": depth,
                "nodes": [
                    {
                        "nodeId": node.get("node_id"),
                        "kind": node.get("kind"),
                        "path": node.get("path"),
                        "label": node.get("label"),
                    }
                    for node in layer_nodes
                ],
            }
        )
        next_nodes = {target for node_id in current for target in adjacency.get(node_id, set())}.difference(visited)
        visited.update(next_nodes)
        current = next_nodes
    impacted_files = sorted(
        {
            str(node_by_id[node_id].get("path") or "")
            for node_id in visited
            if node_id in node_by_id and node_by_id[node_id].get("kind") == "file"
        }
    )
    impacted_symbols = sorted(
        {
            f"{node_by_id[node_id].get('path')}#{node_by_id[node_id].get('label')}"
            for node_id in visited
            if node_id in node_by_id and node_by_id[node_id].get("kind") == "symbol"
        }
    )
    return {
        "schema": "obtuse.blunder.quorum-impact-frontier.v1",
        "status": "PASS",
        "graphFingerprint": graph.get("fingerprint"),
        "changedPaths": list(normalized_paths),
        "maximumDepth": maximum_depth,
        "layers": layers,
        "impactedFiles": impacted_files,
        "impactedSymbols": impacted_symbols,
        "counts": {"files": len(impacted_files), "symbols": len(impacted_symbols), "nodes": len(visited)},
        "sourceMutation": False,
    }


def graph_delta(before: Mapping[str, object], after: Mapping[str, object]) -> dict[str, object]:
    before_nodes, _ = graph_parts(before)
    after_nodes, _ = graph_parts(after)
    before_files = {str(item.get("path") or ""): item for item in before_nodes if item.get("kind") == "file"}
    after_files = {str(item.get("path") or ""): item for item in after_nodes if item.get("kind") == "file"}
    added = sorted(set(after_files).difference(before_files))
    removed = sorted(set(before_files).difference(after_files))
    modified = sorted(
        path
        for path in set(before_files).intersection(after_files)
        if before_files[path].get("content_sha256") != after_files[path].get("content_sha256")
    )
    return {
        "schema": "obtuse.blunder.quorum-graph-delta.v1",
        "status": "CHANGED" if added or removed or modified else "UNCHANGED",
        "beforeFingerprint": before.get("fingerprint"),
        "afterFingerprint": after.get("fingerprint"),
        "addedPaths": added,
        "removedPaths": removed,
        "modifiedPaths": modified,
        "sourceMutation": False,
    }
