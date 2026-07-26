"""Native Blunder task execution with bounded mutation and rollback."""

from __future__ import annotations

import hashlib
import json
import os
import py_compile
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from blunder.cognition import (
    EvidenceItem,
    LearningRecord,
    PlanDecision,
    Strategy,
    build_evidence,
    mutation_lens,
    normalized_terms,
    parse_strategy,
    select_plan,
    strategy_report,
)
from blunder.chronos import start_chronos_context
from blunder.constitutional_adapters import record_provider_boundary, record_source_mutation_boundary
from blunder.constitutional_kernel import load_runtime_constitution
from blunder.validation_policy import (
    SENSITIVE_ENVIRONMENT_MARKERS,
    VALIDATION_ENVIRONMENT_ALLOWLIST,
    VALIDATION_GUARD_SOURCE,
)
from blunder.context_compiler import compile_context, semantic_slice
from blunder.execution_budget import ExecutionBudget, optional_environment_int
from blunder.intent_guard import intent_diff_firewall, require_intent_approval
from blunder.resource_accounting import build_resource_charge
from blunder.validation_broker import BrokerTimeoutExpired, brokered_run
from blunder.verification_court import require_approval, verification_court


OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
MAX_CONTEXT_BYTES = 76_000
INITIAL_CONTEXT_BYTES = 60_000
MAX_CONTEXT_FILE_CHARS = 9_000
MAX_REVIEW_EVIDENCE_BYTES = 52_000
MAX_REVIEW_FILE_CHARS = 16_000
MAX_REVIEW_PACKET_BYTES = 200_000
PROVIDER_ATTEMPT_TIMEOUT_SECONDS = 120
MAX_CHANGE_BYTES = 100_000
MAX_CHANGED_FILES = 6
MAX_LEARNING_RECORDS = 40
ALLOWED_ROOTS: frozenset[str] = frozenset(
    {"android", "apps", "assets", "blunder", "docs", "game", "games", "ios", "packages", "public", "scripts", "src", "tests"}
)
ALLOWED_ROOT_FILES: frozenset[str] = frozenset(
    {
        "README.md", "SECURITY.md", "app.json", "eas.json", "export_presets.cfg", "index.html",
        "next.config.js", "next.config.mjs", "next.config.ts", "package.json", "project.godot",
        "pubspec.yaml", "pyproject.toml", "tsconfig.json", "vite.config.js", "vite.config.ts",
    }
)
ALLOWED_SUFFIXES: frozenset[str] = frozenset(
    {
        ".cfg", ".cjs", ".cs", ".css", ".dart", ".gd", ".glsl", ".godot", ".gradle", ".hlsl", ".html",
        ".ini", ".js", ".json", ".jsx", ".kt", ".kts", ".md", ".mjs", ".properties",
        ".ps1", ".py", ".sass", ".scss", ".shader", ".svelte", ".svg", ".swift", ".toml",
        ".ts", ".tsx", ".vue", ".wgsl", ".xml", ".yaml", ".yml",
    }
)
BLOCKED_PARTS: frozenset[str] = frozenset(
    {".git", ".venv", ".venv-blunder-shell", "__pycache__", "artifacts", "dist", "node_modules"}
)
@dataclass(frozen=True)
class ProposedChange:
    path: str
    content: str
    reason: str


@dataclass(frozen=True)
class Proposal:
    summary: str
    changes: tuple[ProposedChange, ...]
    validation_notes: tuple[str, ...]


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def process_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def validate_task(task: str) -> str:
    normalized = task.strip()
    if len(normalized) < 3:
        raise ValueError("Task must contain at least three characters.")
    if len(normalized) > 4000:
        raise ValueError("Task exceeds the 4,000-character native-agent limit.")
    return normalized


def validate_change_path(root: Path, relative: str) -> Path:
    normalized = relative.replace("\\", "/").strip("/")
    if not normalized:
        raise ValueError("A proposed change has an empty path.")
    candidate = root / normalized
    resolved_candidate = candidate.resolve()
    resolved_root = root.resolve()
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError(f"Proposed path escapes the workspace: {relative}")
    path = Path(normalized)
    if set(path.parts).intersection(BLOCKED_PARTS):
        raise ValueError(f"Proposed path targets a protected area: {relative}")
    if normalized not in ALLOWED_ROOT_FILES and (not path.parts or path.parts[0] not in ALLOWED_ROOTS):
        raise ValueError(f"Proposed path is outside native Blunder source: {relative}")
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(f"Proposed path has an unsupported source type: {relative}")
    return candidate


def parse_proposal(value: Mapping[str, object]) -> Proposal:
    summary = str(value.get("summary") or "").strip()
    if not summary:
        raise ValueError("Native agent response is missing a summary.")
    raw_changes = value.get("changes")
    if not isinstance(raw_changes, list):
        raise ValueError("Native agent response changes must be a list.")
    if not raw_changes:
        raise ValueError("Native agent response must contain at least one bounded change.")
    if len(raw_changes) > MAX_CHANGED_FILES:
        raise ValueError(f"Native agent proposed more than {MAX_CHANGED_FILES} files.")
    changes: list[ProposedChange] = []
    for raw in raw_changes:
        if not isinstance(raw, dict):
            raise ValueError("Every native agent change must be an object.")
        path = str(raw.get("path") or "").replace("\\", "/").strip("/")
        content = str(raw.get("content") or "")
        reason = str(raw.get("reason") or "").strip()
        if not path or not content.strip() or not reason:
            raise ValueError("Every native agent change requires a path, complete non-empty content, and reason.")
        changes.append(
            ProposedChange(
                path=path,
                content=content,
                reason=reason,
            )
        )
    if len({change.path.lower() for change in changes}) != len(changes):
        raise ValueError("Native agent response contains duplicate change paths.")
    if sum(len(change.content.encode("utf-8")) for change in changes) > MAX_CHANGE_BYTES:
        raise ValueError("Native agent response exceeds the bounded change budget.")
    notes_value = value.get("validationNotes")
    notes = tuple(str(item) for item in notes_value) if isinstance(notes_value, list) else ()
    return Proposal(summary=summary, changes=tuple(changes), validation_notes=notes)


def preserve_terminal_newlines(root: Path, proposal: Proposal) -> Proposal:
    normalized_changes: list[ProposedChange] = []
    for change in proposal.changes:
        path = validate_change_path(root, change.path)
        if not path.is_file():
            normalized_changes.append(change)
            continue
        current = path.read_text(encoding="utf-8", errors="replace")
        current_suffix = current[len(current.rstrip("\r\n")) :]
        proposed_body = change.content.rstrip("\r\n")
        proposed_suffix = change.content[len(proposed_body) :]
        content = proposed_body + current_suffix if proposed_suffix != current_suffix else change.content
        normalized_changes.append(ProposedChange(change.path, content, change.reason))
    return Proposal(proposal.summary, tuple(normalized_changes), proposal.validation_notes)


def extract_json_object(text: str) -> dict[str, object]:
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last < first:
        raise ValueError("Native agent response did not contain a JSON object.")
    value = json.loads(text[first : last + 1])
    if not isinstance(value, dict):
        raise ValueError("Native agent response must be a JSON object.")
    return value


def normalize_context_path(value: str, known_paths: frozenset[str]) -> str:
    normalized = value.replace("\\", "/").strip()
    if normalized in known_paths:
        return normalized
    annotated = next(
        (path for path in sorted(known_paths, key=len, reverse=True) if normalized.startswith(f"{path}:")),
        None,
    )
    if annotated is not None:
        return annotated
    base = re.sub(r":(?:line\s*)?\d+(?:-\d+)?$", "", normalized, flags=re.IGNORECASE)
    return base if base in known_paths else normalized


def source_candidates(root: Path) -> Iterable[Path]:
    for name in sorted(ALLOWED_ROOT_FILES):
        path = root / name
        if path.is_file() and path.stat().st_size <= 128 * 1024:
            yield path
    for base in (root / "blunder", root / "scripts" / "obtuse", root / "tests", root / "docs"):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            if set(path.relative_to(root).parts).intersection(BLOCKED_PARTS):
                continue
            if path.stat().st_size <= 128 * 1024:
                yield path


def retrieval_task(task: str) -> str:
    return task.split("\n\nMISSION BRANCH CONTRACT\n", 1)[0].strip()


def task_terms(task: str) -> frozenset[str]:
    return normalized_terms(retrieval_task(task)).difference({"blunder", "change", "file", "source"})


def build_context_sources(root: Path, task: str) -> dict[str, str]:
    result = compile_context(root, retrieval_task(task), MAX_CONTEXT_BYTES, MAX_CONTEXT_FILE_CHARS)
    return result["sources"]


def format_context(sources: Mapping[str, str]) -> str:
    return "".join(f"\n--- {path} ---\n{content}\n" for path, content in sources.items())


def build_context(root: Path, task: str) -> str:
    return format_context(build_context_sources(root, task))


def read_provider_response(request: urllib.request.Request, timeout_seconds: int) -> dict[str, object]:
    result: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def execute() -> None:
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
            result.put((True, body))
        except Exception as error:
            result.put((False, error))

    worker = threading.Thread(target=execute, name="blunder-provider-request", daemon=True)
    worker.start()
    try:
        succeeded, value = result.get(timeout=timeout_seconds)
    except queue.Empty as error:
        raise TimeoutError(f"Provider attempt exceeded the hard {timeout_seconds}-second deadline.") from error
    if not succeeded:
        if isinstance(value, Exception):
            raise value
        raise RuntimeError("Provider attempt failed without an exception payload.")
    if not isinstance(value, dict):
        raise ValueError("Provider response must be one JSON object.")
    return value


def provider_json(
    api_key: str,
    model: str,
    system: str,
    user: str,
    temperature: float,
    budget: ExecutionBudget,
    stage: str,
) -> dict[str, object]:
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    cache_key = hashlib.sha256(
        json.dumps({"stage": stage, "payload": payload}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest().upper()
    cache_path = budget.cache_root / f"{cache_key}.json" if budget.cache_root is not None else None
    if cache_path is not None and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Provider cache record is invalid: {cache_path}: {error}") from error
        value = cached.get("value") if isinstance(cached, dict) else None
        if not isinstance(value, dict) or cached.get("schema") != "obtuse.blunder.provider-cache.v1":
            raise ValueError(f"Provider cache record has an invalid contract: {cache_path}")
        budget.record("provider.cache-hit", {"stage": stage, "cacheKey": cache_key})
        return value
    request = urllib.request.Request(
        OPENROUTER_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    last_error: Exception | None = None
    budget.begin_provider(stage)
    for attempt in range(1, 4):
        budget.provider_attempt(stage, attempt)
        try:
            body = read_provider_response(request, PROVIDER_ATTEMPT_TIMEOUT_SECONDS)
            content = body["choices"][0]["message"]["content"]
            usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
            budget.complete_provider(stage, usage)
            value = extract_json_object(str(content))
            if cache_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = cache_path.with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps({"schema": "obtuse.blunder.provider-cache.v1", "stage": stage, "model": model, "value": value}, separators=(",", ":"), ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                temporary.replace(cache_path)
            return value
        except (KeyError, IndexError, json.JSONDecodeError, TimeoutError, urllib.error.URLError, ValueError) as error:
            last_error = error
            print(json.dumps({"level": "warning", "attempt": attempt, "error": str(error)}), file=sys.stderr)
            if attempt < 3:
                time.sleep(attempt)
    raise RuntimeError(f"Native Blunder provider request failed after three attempts: {last_error}")


def request_strategy(root: Path, api_key: str, model: str, task: str, context: str, budget: ExecutionBudget) -> Strategy:
    system = (
        "You are Blunder's research and strategy engine. Analyze only the supplied workspace evidence. Return exactly "
        "one JSON object with findings, candidates, and discoveryGaps. Every finding has claim, evidencePaths, and "
        "uncertainty. Return exactly four genuinely different candidates, one for each lens: precision, systems, "
        "adversarial, synthesis. Every candidate has id, lens, thesis, targetFiles, steps, evidencePaths, validation, "
        "rollback, risks, riskControls, prediction, and novelty. Cite paths exactly as shown. Use no more than six "
        "target files and pair concrete risks with controls. "
        "Novelty must solve a demonstrated problem and every prediction must be falsifiable. Return no Markdown."
    )
    value = provider_json(
        api_key,
        model,
        system,
        f"TASK\n{task}\n\nWORKSPACE EVIDENCE\n{context}",
        0.55,
        budget,
        "strategy",
    )
    normalized_fields: list[str] = []
    known_paths = frozenset(path.relative_to(root).as_posix() for path in source_candidates(root))
    list_fields = ("evidencePaths", "targetFiles", "steps", "validation", "risks", "riskControls")
    list_caps = {"targetFiles": MAX_CHANGED_FILES, "steps": 8, "validation": 6, "risks": 6, "riskControls": 6}
    raw_findings = value.get("findings")
    raw_candidates = value.get("candidates")
    collections = [item for item in raw_findings if isinstance(item, dict)] if isinstance(raw_findings, list) else []
    collections += [item for item in raw_candidates if isinstance(item, dict)] if isinstance(raw_candidates, list) else []
    for item in collections:
        for field in list_fields:
            field_value = item.get(field)
            if isinstance(field_value, str) and field_value.strip():
                item[field] = [
                    normalize_context_path(field_value, known_paths)
                    if field in {"evidencePaths", "targetFiles"}
                    else field_value.strip()
                ]
                normalized_fields.append(field)
            elif isinstance(field_value, list):
                unique_values = list(
                    dict.fromkeys(
                        normalize_context_path(str(value), known_paths)
                        if field in {"evidencePaths", "targetFiles"}
                        else str(value).strip()
                        for value in field_value
                        if str(value).strip()
                    )
                )
                maximum = list_caps.get(field)
                if maximum is not None:
                    unique_values = unique_values[:maximum]
                if unique_values != field_value:
                    item[field] = unique_values
                    normalized_fields.append(field)
    gaps = value.get("discoveryGaps")
    if isinstance(gaps, str) and gaps.strip():
        value["discoveryGaps"] = [gaps.strip()]
        normalized_fields.append("discoveryGaps")
    elif isinstance(gaps, list):
        unique_gaps = list(dict.fromkeys(str(gap).strip() for gap in gaps if str(gap).strip()))
        if unique_gaps != gaps:
            value["discoveryGaps"] = unique_gaps
            normalized_fields.append("discoveryGaps")
    if normalized_fields:
        budget.record("provider.contract-normalized", {"stage": "strategy", "fields": sorted(set(normalized_fields))})
    return parse_strategy(value)


def hydrate_strategy_sources(
    root: Path,
    task: str,
    sources: Mapping[str, str],
    strategy: Strategy,
) -> dict[str, str]:
    """Verify and load provider-discovered repository evidence within the context ceiling."""
    hydrated = dict(sources)
    cited_paths = tuple(
        dict.fromkeys(
            path
            for paths in (
                *(finding.evidence_paths for finding in strategy.findings),
                *(candidate.evidence_paths for candidate in strategy.candidates),
            )
            for path in paths
        )
    )
    consumed = len(format_context(hydrated).encode("utf-8"))
    terms = task_terms(task)
    for relative in cited_paths:
        if relative in hydrated:
            continue
        path = validate_change_path(root, relative)
        if not path.is_file():
            continue
        content, _ = semantic_slice(
            path.read_text(encoding="utf-8", errors="replace"),
            terms,
            MAX_CONTEXT_FILE_CHARS,
        )
        block_size = len(f"\n--- {relative} ---\n{content}\n".encode("utf-8"))
        if consumed + block_size > MAX_CONTEXT_BYTES:
            continue
        hydrated[relative] = content
        consumed += block_size
    return hydrated


def request_proposal(
    api_key: str,
    model: str,
    task: str,
    context: str,
    strategy: Strategy,
    decision: PlanDecision,
    budget: ExecutionBudget,
) -> Proposal:
    system = (
        "You are the native Blunder coding engine. Return exactly one JSON object with keys summary, changes, and "
        "validationNotes. changes is a non-empty list of objects with path, complete non-empty replacement content, "
        "and reason. Never return placeholder or omitted content. Use at most six files. "
        "Implement only the supplied selected plan and only its declared target files. Prefer small coherent edits. "
        "Never target generated data, credentials, dependencies, Git metadata, or paths outside blunder, scripts, "
        "tests, docs, README.md, SECURITY.md, and pyproject.toml. Return no Markdown fences."
    )
    plan_context = "\n\nSELECTED PLAN\n" + json.dumps(
        strategy_report(strategy, decision, ())["tournament"], separators=(",", ":")
    )
    value = provider_json(
        api_key,
        model,
        system,
        f"TASK\n{task}{plan_context}\n\nWORKSPACE CONTEXT\n{context}",
        0.12,
        budget,
        "implementation",
    )
    proposal = parse_proposal(value)
    declared_targets = {path.replace("\\", "/").strip("/").lower() for path in decision.selected.target_files}
    escaped_targets = sorted(change.path for change in proposal.changes if change.path.lower() not in declared_targets)
    if escaped_targets:
        raise ValueError(f"Native agent proposed files outside the selected plan targets: {escaped_targets}")
    return proposal


def request_review(
    api_key: str,
    model: str,
    task: str,
    proposal: Proposal,
    decision: PlanDecision,
    evidence: Mapping[str, str],
    budget: ExecutionBudget,
) -> dict[str, object]:
    system = (
        "You are Blunder's independent read-only patch judge. You cannot edit files. Return exactly one JSON object "
        "with verdict, confidence, findings, and requiredChecks. verdict is APPROVE or REJECT. findings is a list "
        "of objects with severity, claim, and evidencePath. Reject correctness defects, boundary escapes, unproven "
        "behavior, missing rollback, factual claims unsupported by workspaceEvidence, or changes outside the selected "
        "targets. Treat absent evidence as uncertainty and reject material uncertainty. Do not approve based on style."
    )
    packet = {
        "task": task,
        "selectedTargets": list(decision.selected.target_files),
        "validation": list(decision.selected.validation),
        "rollback": decision.selected.rollback,
        "proposalSummary": proposal.summary,
        "workspaceEvidence": dict(evidence),
        "changes": [
            {"path": change.path, "reason": change.reason, "content": change.content}
            for change in proposal.changes
        ],
    }
    encoded = json.dumps(packet, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_REVIEW_PACKET_BYTES:
        raise ValueError("Independent review packet exceeds the bounded context ceiling.")
    value = provider_json(api_key, model, system, encoded, 0.0, budget, "independent-review")
    verdict = str(value.get("verdict") or "").upper()
    findings = value.get("findings")
    checks = value.get("requiredChecks")
    if verdict not in {"APPROVE", "REJECT"} or not isinstance(findings, list) or not isinstance(checks, list):
        raise ValueError("Independent reviewer returned an invalid decision contract.")
    result = {
        "verdict": verdict,
        "confidence": str(value.get("confidence") or "unspecified"),
        "findings": findings,
        "requiredChecks": [str(item) for item in checks],
        "readOnly": True,
        "separateContext": True,
        "model": model,
    }
    if verdict != "APPROVE":
        raise ValueError(f"Independent reviewer rejected the proposal: {json.dumps(findings, ensure_ascii=False)}")
    return result


def build_review_evidence(
    root: Path,
    strategy: Strategy,
    decision: PlanDecision,
    context_sources: Mapping[str, str],
) -> dict[str, str]:
    strategy_evidence = tuple(
        path
        for paths in (
            *(finding.evidence_paths for finding in strategy.findings),
            *(candidate.evidence_paths for candidate in strategy.candidates),
        )
        for path in paths
    )
    paths = tuple(
        dict.fromkeys(
            (
                *decision.selected.target_files,
                *decision.selected.evidence_paths,
                *strategy_evidence,
                *context_sources.keys(),
            )
        )
    )
    evidence: dict[str, str] = {}
    consumed = 0
    for relative in paths:
        path = validate_change_path(root, relative)
        if not path.is_file():
            continue
        content = (
            path.read_text(encoding="utf-8", errors="replace")
            if relative in decision.selected.target_files
            else context_sources.get(relative, path.read_text(encoding="utf-8", errors="replace"))
        )
        if len(content) > MAX_REVIEW_FILE_CHARS:
            half = MAX_REVIEW_FILE_CHARS // 2
            content = content[:half] + "\n<...bounded middle omitted...>\n" + content[-half:]
        encoded = content.encode("utf-8")
        if consumed + len(encoded) > MAX_REVIEW_EVIDENCE_BYTES:
            continue
        evidence[relative] = content
        consumed += len(encoded)
    if not evidence:
        raise ValueError("Independent review requires workspace evidence from the selected plan.")
    return evidence


def load_native_learning_history(root: Path) -> tuple[LearningRecord, ...]:
    artifact_root = root / "artifacts" / "obtuse" / "blunder-native-agent"
    records: list[LearningRecord] = []
    if not artifact_root.is_dir():
        return ()
    for path in sorted(artifact_root.glob("*/report.json"), reverse=True):
        if len(records) >= MAX_LEARNING_RECORDS:
            break
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            raise RuntimeError(f"Cannot read native-agent learning record {path}: {error}") from error
        if not isinstance(value, dict) or value.get("schema") != "obtuse.blunder.native-agent.v2":
            continue
        cognition = value.get("cognition")
        if not isinstance(cognition, dict):
            raise ValueError(f"Native-agent learning record has invalid cognition data: {path}")
        tournament = cognition.get("tournament")
        if not isinstance(tournament, dict):
            raise ValueError(f"Native-agent learning record has invalid tournament data: {path}")
        selected = tournament.get("selected")
        if not isinstance(selected, dict):
            raise ValueError(f"Native-agent learning record has no selected plan: {path}")
        target_files = selected.get("target_files")
        lens = str(selected.get("lens") or "")
        status = str(value.get("status") or "")
        if not isinstance(target_files, list) or not lens or not status:
            raise ValueError(f"Native-agent learning record is incomplete: {path}")
        records.append(
            LearningRecord(status=status, target_files=tuple(str(item) for item in target_files), lens=lens)
        )
    return tuple(records)


def ledger_target(root: Path, value: str) -> str | None:
    path = Path(value)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(root.resolve())
        except ValueError:
            return None
    normalized = path.as_posix().strip("/")
    try:
        target = validate_change_path(root, normalized)
    except ValueError:
        return None
    return normalized if target.is_file() else None


def mutation_targets(root: Path, value: Mapping[str, object]) -> tuple[str, ...]:
    raw_files = value.get("filesChanged")
    candidates = tuple(str(item) for item in raw_files) if isinstance(raw_files, list) else ()
    if not candidates:
        snapshot = value.get("rollbackSnapshot")
        items = snapshot.get("items") if isinstance(snapshot, dict) else None
        if isinstance(items, list):
            candidates = tuple(
                str(item.get("source") or "") for item in items if isinstance(item, dict) and item.get("source")
            )
    normalized = tuple(target for item in candidates if (target := ledger_target(root, item)) is not None)
    return tuple(dict.fromkeys(normalized))


def load_mutation_learning_history(root: Path) -> tuple[LearningRecord, ...]:
    path = (
        root
        / "artifacts"
        / "obtuse"
        / "blunder-flash-full-autonomy-compounding-bundle-v1"
        / "self-coding"
        / "self_coding_patch_ledger.jsonl"
    )
    if not path.is_file():
        return ()
    records: list[LearningRecord] = []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Cannot read mutation learning record {path} line {line_number}: {error}") from error
        if not isinstance(value, dict):
            raise ValueError(f"Mutation learning record must be an object at {path} line {line_number}.")
        targets = mutation_targets(root, value)
        if not targets:
            continue
        applied = value.get("patchApplied") is True and int(value.get("sourceLinesChanged") or 0) > 0
        status = "APPLIED_AND_VALIDATED" if applied and str(value.get("result") or "") == "PASS" else "ROLLED_BACK"
        records.append(
            LearningRecord(
                status=status,
                target_files=targets,
                lens=mutation_lens(f"{str(value.get('patchType') or '')} {' '.join(targets)}"),
            )
        )
    return tuple(records[-MAX_LEARNING_RECORDS:])


def load_learning_history(root: Path) -> tuple[LearningRecord, ...]:
    native = load_native_learning_history(root)
    mutations = load_mutation_learning_history(root)
    half = MAX_LEARNING_RECORDS // 2
    return tuple((*native[:half], *mutations[-half:]))


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False, dir=path.parent) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def checkpoint_manifest(
    root: Path,
    artifact: Path,
    existence: Mapping[str, bool],
    changes: Sequence[ProposedChange],
) -> dict[str, object]:
    files: list[dict[str, object]] = []
    snapshot = artifact / "snapshot"
    for change in changes:
        target = validate_change_path(root, change.path)
        relative = target.relative_to(root).as_posix()
        existed_before = existence[relative]
        before = snapshot / relative
        files.append(
            {
                "path": relative,
                "existedBefore": existed_before,
                "beforeSha256": file_sha256(before) if existed_before else None,
                "afterSha256": file_sha256(target),
            }
        )
    return {
        "schema": "obtuse.blunder.checkpoint.v1",
        "id": artifact.name,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "restorable": bool(files),
        "files": files,
    }


def snapshot_changes(root: Path, artifact: Path, changes: Sequence[ProposedChange]) -> dict[str, bool]:
    existence: dict[str, bool] = {}
    snapshot = artifact / "snapshot"
    for change in changes:
        target = validate_change_path(root, change.path)
        relative = target.relative_to(root).as_posix()
        existence[relative] = target.is_file()
        if target.is_file():
            copy = snapshot / relative
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, copy)
    return existence


def restore_snapshot(root: Path, artifact: Path, existence: Mapping[str, bool]) -> None:
    snapshot = artifact / "snapshot"
    for relative, existed in existence.items():
        target = validate_change_path(root, relative)
        if existed:
            copy = snapshot / relative
            if not copy.is_file():
                raise RuntimeError(f"Rollback snapshot is missing: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(copy, target)
        elif target.exists():
            target.unlink()


def trusted_python_callers(root: Path, changes: Sequence[ProposedChange]) -> dict[str, str]:
    changed = {change.path.replace("\\", "/") for change in changes}
    trusted: dict[str, str] = {}
    for path in source_candidates(root):
        relative = path.relative_to(root).as_posix()
        if path.suffix.lower() == ".py" and relative not in changed:
            trusted[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    return trusted


def validation_environment(root: Path, artifact: Path, trusted: Mapping[str, str]) -> dict[str, str]:
    environment = {
        str(key): str(value)
        for key, value in os.environ.items()
        if key.upper() in VALIDATION_ENVIRONMENT_ALLOWLIST
        and not any(marker in key.upper() for marker in SENSITIVE_ENVIRONMENT_MARKERS)
    }
    guard_root = artifact / "validation-guard"
    guard_root.mkdir(parents=True, exist_ok=False)
    (guard_root / "sitecustomize.py").write_text(VALIDATION_GUARD_SOURCE, encoding="utf-8")
    environment.update(
        {
            "BLUNDER_VALIDATION_WORKSPACE": str(root.resolve()),
            "BLUNDER_VALIDATION_TRUSTED_CALLERS": json.dumps(trusted, separators=(",", ":")),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": os.pathsep.join((str(guard_root), str(root.resolve()))),
            "PYTHONSAFEPATH": "1",
        }
    )
    return environment


def run_validation(
    root: Path,
    changes: Sequence[ProposedChange],
    artifact: Path,
    trusted: Mapping[str, str],
) -> dict[str, object]:
    validation_context = start_chronos_context(load_runtime_constitution().policy_epoch, 0)
    compiled: list[str] = []
    for change in changes:
        path = validate_change_path(root, change.path)
        if path.suffix.lower() == ".py":
            py_compile.compile(str(path), doraise=True)
            compiled.append(path.relative_to(root).as_posix())
    try:
        completed = brokered_run(
            (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"),
            root,
            validation_environment(root, artifact, trusted),
            300,
            768 * 1024 * 1024,
        )
        timed_out = False
    except BrokerTimeoutExpired as error:
        completed = {
            "returncode": 124,
            "output": str(error.output or ""),
            "isolation": error.isolation,
            "telemetry": error.telemetry,
        }
        timed_out = True
    validation_charge = build_resource_charge(
        "native-agent:validation",
        "validation-shard",
        validation_context,
        completed["telemetry"],
        {"providerCalls": 0, "totalTokens": 0, "costUsd": 0.0},
        "validation-authority",
        None,
        None,
        datetime.now(timezone.utc),
    )
    return {
        "compiled": compiled,
        "testsPassed": completed["returncode"] == 0,
        "testExitCode": completed["returncode"],
        "timedOut": timed_out,
        "testOutput": completed["output"][-20_000:],
        "isolation": completed["isolation"],
        "resourceCharge": validation_charge,
    }


def apply_proposal(
    root: Path,
    task: str,
    model: str,
    proposal: Proposal,
    cognition: Mapping[str, object] | None,
    review: Mapping[str, object],
    intent_court: Mapping[str, object],
    preflight_court: Mapping[str, object],
    budget: ExecutionBudget,
    provider_shadow: Mapping[str, object],
) -> dict[str, object]:
    source_mutation_shadow = record_source_mutation_boundary(
        root,
        review,
        intent_court,
        preflight_court,
        datetime.now(timezone.utc),
    )
    artifact = root / "artifacts" / "obtuse" / "blunder-native-agent" / utc_stamp()
    artifact.mkdir(parents=True, exist_ok=False)
    trusted = trusted_python_callers(root, proposal.changes)
    existence = snapshot_changes(root, artifact, proposal.changes)
    for change in proposal.changes:
        write_atomic(validate_change_path(root, change.path), change.content)
    failure: str | None = None
    validation: dict[str, object] = {}
    try:
        validation = run_validation(root, proposal.changes, artifact, trusted)
        if validation["testsPassed"] is not True:
            raise RuntimeError("Native Blunder validation failed.")
        postflight_court = verification_court(root, proposal.changes, tuple(change.path for change in proposal.changes), validation, "postflight")
        require_approval(postflight_court)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, py_compile.PyCompileError) as error:
        restore_snapshot(root, artifact, existence)
        validation = dict(validation) | {"testsPassed": False, "error": str(error)}
        failure = str(error)
    cognition_record = dict(cognition or {})
    tournament = cognition_record.get("tournament")
    selected = tournament.get("selected") if isinstance(tournament, dict) else None
    prediction = str(selected.get("prediction") or "") if isinstance(selected, dict) else ""
    status = "ROLLED_BACK" if failure else ("APPLIED_AND_VALIDATED" if proposal.changes else "ANALYSIS_COMPLETE")
    prediction_assessment = (
        "FALSIFIED_BY_VALIDATION"
        if failure
        else ("SUPPORTED_BY_VALIDATION" if proposal.changes else "NOT_TESTED")
    )
    cognition_record["reflection"] = {
        "prediction": prediction,
        "observed": status,
        "predictionAssessment": prediction_assessment,
        "boundaryRetained": True,
        "nextSelectionSignal": (
            "penalize-repeat" if failure else ("eligible-for-reuse" if proposal.changes else "none")
        ),
    }
    checkpoint = checkpoint_manifest(root, artifact, existence, proposal.changes) if not failure else {
        "schema": "obtuse.blunder.checkpoint.v1",
        "id": artifact.name,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "restorable": False,
        "files": [],
    }
    report = {
        "schema": "obtuse.blunder.native-agent.v2",
        "status": status,
        "task": task,
        "model": model,
        "providerCalls": budget.provider_calls,
        "budget": budget.public(),
        "resourceCharge": budget.resource_charge(
            "native-agent:mission",
            "native-mission",
            optional_environment_int("BLUNDER_MISSION_QUEUE_DEPTH"),
            optional_environment_int("BLUNDER_MISSION_QUEUE_CAPACITY"),
        ),
        "independentReview": dict(review),
        "verificationCourt": {
            "intent": dict(intent_court),
            "preflight": dict(preflight_court),
            "postflight": postflight_court if not failure else None,
        },
        "cognition": cognition_record,
        "proposal": {"summary": proposal.summary, "changes": [asdict(item) | {"content": "<stored-in-worktree>"} for item in proposal.changes], "validationNotes": list(proposal.validation_notes)},
        "validation": validation,
        "rollbackAvailable": bool(proposal.changes),
        "checkpoint": checkpoint,
        "artifact": str(artifact),
        "constitutionalShadow": {
            "mode": "fail_closed",
            "provider": dict(provider_shadow),
            "sourceMutation": source_mutation_shadow,
        },
    }
    (artifact / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if failure:
        raise RuntimeError(f"Native Blunder rolled back the proposal: {failure}")
    return report


def run(root: Path, task: str, provider_approved: bool) -> dict[str, object]:
    provider_shadow = record_provider_boundary(root, provider_approved, datetime.now(timezone.utc))
    if not provider_approved:
        raise ValueError("Native Blunder requires explicit provider approval for a model-backed task.")
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not available to the native Blunder process.")
    normalized_task = validate_task(task)
    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    review_model = os.environ.get("OPENROUTER_REVIEW_MODEL", model).strip() or model
    budget = ExecutionBudget.from_environment()
    configured_cache = os.environ.get("BLUNDER_PROVIDER_CACHE_ROOT", "").strip()
    budget.cache_root = (
        Path(configured_cache).resolve()
        if configured_cache
        else root / "artifacts" / "obtuse" / "blunder-provider-cache-v1"
    )
    budget.record("mission.started", {"task": normalized_task, "model": model, "reviewModel": review_model})
    compiled_context = compile_context(
        root,
        retrieval_task(normalized_task),
        INITIAL_CONTEXT_BYTES,
        MAX_CONTEXT_FILE_CHARS,
    )
    sources = compiled_context["sources"]
    budget.record("context.compiled", compiled_context["manifest"])
    context = format_context(sources)
    strategy = request_strategy(root, api_key, model, normalized_task, context, budget)
    sources = hydrate_strategy_sources(root, normalized_task, sources, strategy)
    context = format_context(sources)
    evidence: tuple[EvidenceItem, ...] = build_evidence(retrieval_task(normalized_task), sources, 800)
    decision = select_plan(strategy, evidence, load_learning_history(root), MAX_CHANGED_FILES)
    for target_file in decision.selected.target_files:
        validate_change_path(root, target_file)
    proposal = preserve_terminal_newlines(
        root,
        request_proposal(api_key, model, normalized_task, context, strategy, decision, budget),
    )
    for change in proposal.changes:
        validate_change_path(root, change.path)
    selected_targets = frozenset(decision.selected.target_files)
    unexpected = frozenset(change.path for change in proposal.changes).difference(selected_targets)
    if unexpected:
        raise ValueError(f"Implementation escaped the selected plan: {', '.join(sorted(unexpected))}")
    intent_court = intent_diff_firewall(root, normalized_task, proposal.changes)
    require_intent_approval(intent_court)
    preflight_court = verification_court(root, proposal.changes, decision.selected.target_files, None, "preflight")
    require_approval(preflight_court)
    review = request_review(
        api_key,
        review_model,
        normalized_task,
        proposal,
        decision,
        build_review_evidence(root, strategy, decision, sources),
        budget,
    )
    cognition = strategy_report(strategy, decision, evidence)
    cognition["contextCompiler"] = compiled_context["manifest"]
    report = apply_proposal(
        root,
        normalized_task,
        model,
        proposal,
        cognition,
        review,
        intent_court,
        preflight_court,
        budget,
        provider_shadow,
    )
    budget.record("mission.completed", {"status": report.get("status"), **budget.public()})
    return report
