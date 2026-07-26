from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from blunder.native_agent import MAX_CHANGED_FILES, MAX_CONTEXT_BYTES, build_context_sources, task_terms


CHECKPOINT_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}(?:[0-9]{0,6})?Z?$")
SEARCH_SUFFIXES: frozenset[str] = frozenset(
    {".css", ".html", ".js", ".json", ".md", ".ps1", ".py", ".toml", ".ts", ".tsx", ".yaml", ".yml"}
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def task_briefing(root: Path, task: str) -> dict[str, object]:
    sources = build_context_sources(root, task)
    terms = task_terms(task)
    evidence: list[dict[str, object]] = []
    total_bytes = 0
    for relative, content in sources.items():
        encoded_bytes = len(content.encode("utf-8"))
        total_bytes += encoded_bytes
        lowered = content.lower()
        matched_terms = sorted(term for term in terms if term in lowered)
        evidence.append(
            {
                "path": relative,
                "bytes": encoded_bytes,
                "lines": content.count("\n") + 1,
                "matchedTerms": matched_terms[:8],
                "termHitCount": sum(lowered.count(term) for term in matched_terms),
            }
        )
    evidence.sort(key=lambda item: (-int(item["termHitCount"]), str(item["path"])))
    return {
        "schema": "obtuse.blunder.task-briefing.v1",
        "generatedAt": utc_now(),
        "task": task,
        "context": {
            "sources": evidence,
            "sourceCount": len(evidence),
            "bytes": total_bytes,
            "budgetBytes": MAX_CONTEXT_BYTES,
            "utilizationPercent": round(total_bytes / MAX_CONTEXT_BYTES * 100, 2),
        },
        "contract": {
            "maximumChangedFiles": MAX_CHANGED_FILES,
            "snapshotRequired": True,
            "compileChangedPython": True,
            "integrationTestsRequired": True,
            "rollbackOnFailure": True,
            "planTournamentLenses": ["precision", "systems", "adversarial", "synthesis"],
            "providerCallsBeforeApproval": 0,
        },
        "gates": [
            {"name": "Evidence boundary", "status": "READY" if evidence else "NEEDS_CONTEXT"},
            {"name": "Mutation ceiling", "status": f"{MAX_CHANGED_FILES}_FILES_MAX"},
            {"name": "Validation", "status": "COMPILE_AND_INTEGRATION"},
            {"name": "Recovery", "status": "HASH_GUARDED_CHECKPOINT"},
        ],
    }


def process_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def workspace_search(root: Path, query: str, limit: int) -> list[dict[str, object]]:
    normalized = query.strip()
    if len(normalized) < 2:
        return []
    completed = subprocess.run(
        [
            "rg",
            "--line-number",
            "--column",
            "--smart-case",
            "--fixed-strings",
            "--color",
            "never",
            "--glob",
            "!artifacts/**",
            "--glob",
            "!**/.git/**",
            "--glob",
            "!**/node_modules/**",
            "--glob",
            "!**/__pycache__/**",
            normalized,
            str(root),
        ],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
        creationflags=process_flags(),
    )
    if completed.returncode not in (0, 1):
        raise RuntimeError(f"workspace search failed: {completed.stderr[-500:]}")
    results: list[dict[str, object]] = []
    pattern = re.compile(r"^(.*?):(\d+):(\d+):(.*)$")
    for raw in completed.stdout.splitlines():
        match = pattern.match(raw)
        if match is None:
            continue
        absolute = Path(match.group(1))
        try:
            relative = absolute.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if absolute.suffix.lower() not in SEARCH_SUFFIXES:
            continue
        results.append(
            {
                "kind": "match",
                "path": relative,
                "line": int(match.group(2)),
                "column": int(match.group(3)),
                "preview": match.group(4).strip()[:240],
            }
        )
        if len(results) >= limit:
            break
    return results


def checkpoint_root(root: Path) -> Path:
    return root / "artifacts" / "obtuse" / "blunder-native-agent"


def checkpoint_artifact(root: Path, checkpoint_id: str) -> Path:
    if CHECKPOINT_ID_PATTERN.fullmatch(checkpoint_id) is None:
        raise ValueError("Checkpoint identifier is invalid.")
    artifact = (checkpoint_root(root) / checkpoint_id).resolve()
    if not artifact.is_relative_to(checkpoint_root(root).resolve()):
        raise ValueError("Checkpoint escapes the Blunder artifact boundary.")
    if not artifact.is_dir():
        raise ValueError("Checkpoint does not exist.")
    return artifact


def load_checkpoint_report(root: Path, checkpoint_id: str) -> dict[str, object]:
    path = checkpoint_artifact(root, checkpoint_id) / "report.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read checkpoint report {checkpoint_id}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("Checkpoint report must be a JSON object.")
    return value


def checkpoint_files(report: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    checkpoint = report.get("checkpoint")
    files = checkpoint.get("files") if isinstance(checkpoint, dict) else None
    return tuple(item for item in files if isinstance(item, dict)) if isinstance(files, list) else ()


def checkpoint_is_current(root: Path, report: Mapping[str, object]) -> bool:
    files = checkpoint_files(report)
    if not files:
        return False
    for item in files:
        relative = str(item.get("path") or "")
        target = (root / relative).resolve()
        expected = str(item.get("afterSha256") or "")
        if not target.is_relative_to(root.resolve()) or not target.is_file() or not expected:
            return False
        if file_sha256(target) != expected:
            return False
    return True


def checkpoint_summary(root: Path, path: Path) -> dict[str, object] | None:
    try:
        report = load_checkpoint_report(root, path.name)
    except (ValueError, RuntimeError):
        return None
    proposal = report.get("proposal") if isinstance(report.get("proposal"), dict) else {}
    cognition = report.get("cognition") if isinstance(report.get("cognition"), dict) else {}
    tournament = cognition.get("tournament") if isinstance(cognition.get("tournament"), dict) else {}
    selected = tournament.get("selected") if isinstance(tournament.get("selected"), dict) else {}
    manifest = report.get("checkpoint") if isinstance(report.get("checkpoint"), dict) else {}
    return {
        "id": path.name,
        "status": report.get("status"),
        "task": report.get("task"),
        "summary": proposal.get("summary"),
        "lens": selected.get("lens"),
        "files": [str(item.get("path")) for item in checkpoint_files(report)],
        "createdAt": manifest.get("createdAt"),
        "restorable": manifest.get("restorable") is True,
        "current": checkpoint_is_current(root, report),
    }


def list_checkpoints(root: Path, limit: int) -> list[dict[str, object]]:
    base = checkpoint_root(root)
    if not base.is_dir():
        return []
    values: list[dict[str, object]] = []
    for path in sorted((item for item in base.iterdir() if item.is_dir()), reverse=True):
        summary = checkpoint_summary(root, path)
        if summary is not None:
            values.append(summary)
        if len(values) >= limit:
            break
    return values


def unified_diff(before: str, after: str, relative: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"checkpoint/{relative}",
            tofile=f"workspace/{relative}",
            n=3,
        )
    )


def checkpoint_detail(root: Path, checkpoint_id: str) -> dict[str, object]:
    artifact = checkpoint_artifact(root, checkpoint_id)
    report = load_checkpoint_report(root, checkpoint_id)
    diffs: list[dict[str, object]] = []
    for item in checkpoint_files(report):
        relative = str(item.get("path") or "")
        target = (root / relative).resolve()
        before_path = artifact / "snapshot" / relative
        before = before_path.read_text(encoding="utf-8", errors="replace") if before_path.is_file() else ""
        after = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        diff = unified_diff(before, after, relative)
        diffs.append({"path": relative, "diff": diff[:80_000], "changed": bool(diff)})
    return {
        "schema": "obtuse.blunder.checkpoint-detail.v1",
        "checkpoint": checkpoint_summary(root, artifact),
        "diffs": diffs,
        "restoreConfirmation": f"RESTORE {checkpoint_id}",
    }


def restore_checkpoint(root: Path, checkpoint_id: str, confirmation: str) -> dict[str, object]:
    expected_confirmation = f"RESTORE {checkpoint_id}"
    if confirmation != expected_confirmation:
        raise ValueError(f"Checkpoint restore requires exact confirmation: {expected_confirmation}")
    artifact = checkpoint_artifact(root, checkpoint_id)
    report = load_checkpoint_report(root, checkpoint_id)
    if not checkpoint_is_current(root, report):
        raise ValueError("Checkpoint restore refused because one or more target files drifted after the checkpoint.")
    files = checkpoint_files(report)
    for item in files:
        if item.get("existedBefore") is not True:
            continue
        relative = str(item.get("path") or "")
        snapshot = artifact / "snapshot" / relative
        if not snapshot.is_file() or file_sha256(snapshot) != str(item.get("beforeSha256") or ""):
            raise RuntimeError(f"Checkpoint snapshot integrity failed for {relative}.")
    restored: list[str] = []
    for item in files:
        relative = str(item.get("path") or "")
        target = (root / relative).resolve()
        existed_before = item.get("existedBefore") is True
        if existed_before:
            snapshot = artifact / "snapshot" / relative
            target.write_bytes(snapshot.read_bytes())
        else:
            target.unlink()
        restored.append(relative)
    event = {"schema": "obtuse.blunder.checkpoint-restore.v1", "restoredAt": utc_now(), "files": restored}
    (artifact / "restore.json").write_text(json.dumps(event, indent=2) + "\n", encoding="utf-8")
    return {"status": "RESTORED", "checkpointId": checkpoint_id, "files": restored}


def job_history_path(root: Path) -> Path:
    return root / "artifacts" / "obtuse" / "blunder-shell-v2" / "job-history.jsonl"


def append_job_history(root: Path, job: Mapping[str, object]) -> None:
    path = job_history_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = {key: value for key, value in job.items() if key not in {"output", "outputTruncated"}}
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(summary, separators=(",", ":"), ensure_ascii=False) + "\n")


def read_job_history(root: Path, limit: int) -> list[dict[str, object]]:
    path = job_history_path(root)
    if not path.is_file():
        return []
    rows: list[dict[str, object]] = []
    for line in reversed(path.read_text(encoding="utf-8-sig").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
        if len(rows) >= limit:
            break
    return rows


def mission_control_state(
    root: Path,
    active_jobs: Sequence[Mapping[str, object]],
    runtime: Mapping[str, object],
) -> dict[str, object]:
    running = [job for job in active_jobs if job.get("status") in {"queued", "running"}]
    autonomous = {
        "id": "idle-autonomy",
        "label": "Autonomous compound runtime",
        "status": "running" if runtime.get("active") is True else "idle",
        "pidValid": runtime.get("pidValid"),
        "lastAction": runtime.get("lastAction"),
    }
    return {
        "schema": "obtuse.blunder.mission-control.v1",
        "generatedAt": utc_now(),
        "sessions": [autonomous, *running],
        "history": read_job_history(root, 40),
        "checkpoints": list_checkpoints(root, 30),
    }
