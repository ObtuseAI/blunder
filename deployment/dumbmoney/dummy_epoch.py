from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import canonical_json_bytes, is_link_like, sha256_bytes, write_new_json


SQLITE_HEADER = b"SQLite format 3\x00"
HEADER_READ_BYTES = 1024 * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_dummy_legacy_epoch_plan(
    ledger_path: Path,
    *,
    planned_epoch_root: Path,
    plan_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Inspect file metadata and emit a non-executable maintenance plan.

    The function does not connect to SQLite, request a checkpoint, create an
    epoch database, inspect a broker, or write beside the legacy ledger.
    """

    requested_ledger = ledger_path.absolute()
    if is_link_like(requested_ledger):
        raise ValueError("legacy Dummy ledger may not be a symlink or junction")
    ledger = requested_ledger.resolve()
    epoch_root = planned_epoch_root.resolve()
    if not ledger.is_file():
        raise ValueError(f"legacy Dummy ledger is not a file: {ledger}")
    if epoch_root == ledger or ledger in epoch_root.parents:
        raise ValueError("planned epoch root may not be inside the ledger file")
    stat_before = ledger.stat()
    header_digest = hashlib.sha256()
    with ledger.open("rb") as handle:
        header = handle.read(HEADER_READ_BYTES)
        header_digest.update(header)
    stat_after = ledger.stat()
    if (
        stat_before.st_size != stat_after.st_size
        or stat_before.st_mtime_ns != stat_after.st_mtime_ns
    ):
        raise RuntimeError("legacy ledger changed while the plan was being prepared")

    normalized_parts = {
        part.lower() for path in (ledger, requested_ledger) for part in path.parts
    }
    live_path_detected = (
        ledger.name.lower() == "ledger.db" and "autonomy" in normalized_parts
    )
    generated_plan_id = plan_id or str(uuid.uuid4())
    plan = {
        "schema_version": "dumbmoney.dummy-legacy-epoch-plan.v1",
        "plan_id": generated_plan_id,
        "created_at": created_at or _utc_now(),
        "status": "PLAN_ONLY",
        "source": {
            "path": str(ledger),
            "bytes": stat_before.st_size,
            "mtime_ns": stat_before.st_mtime_ns,
            "sqlite_header_present": header.startswith(SQLITE_HEADER),
            "first_megabyte_sha256": header_digest.hexdigest(),
            "live_path_detected": live_path_detected,
        },
        "planned_epoch": {
            "root": str(epoch_root),
            "operational_journal": str(epoch_root / "execution.db"),
            "content_store": str(epoch_root / "objects"),
            "legacy_mount_mode": "READ_ONLY",
            "high_volume_research_allowed": False,
        },
        "inspection": {
            "sqlite_connection_opened": False,
            "checkpoint_requested": False,
            "wal_modified": False,
            "broker_contacted": False,
            "network_contacted": False,
            "source_files_written": False,
        },
        "operator_gates": [
            {
                "gate": "BROKER_TRUTH_RECEIPT",
                "requirement": (
                    "fresh GET-only Kalshi orders and positions are mapped to an "
                    "InitialReconciliationReceiptV1 or InheritedExposureReceiptV1"
                ),
            },
            {
                "gate": "WRITER_QUIESCENCE",
                "requirement": (
                    "all legacy-ledger writers are enumerated, stopped in a reviewed "
                    "maintenance window, and independently observed stopped"
                ),
            },
            {
                "gate": "CHECKPOINT_BACKUP",
                "requirement": (
                    "a separately reviewed maintenance utility checkpoints SQLite, "
                    "copies database and sidecars, hashes them, and leaves originals intact"
                ),
            },
            {
                "gate": "RESTORE_PROOF",
                "requirement": (
                    "the hashed backup restores to a different path and passes integrity, "
                    "row-count, and application-level reconciliation checks"
                ),
            },
            {
                "gate": "NEW_OPERATIONAL_EPOCH",
                "requirement": (
                    "a compact journal is created from broker truth, not copied local "
                    "position caches, and starts in RECONCILIATION_ONLY"
                ),
            },
        ],
        "prohibited_automatic_actions": [
            "PRAGMA wal_checkpoint",
            "VACUUM",
            "DELETE or retention pruning",
            "copying a database while writers are active",
            "canceling or amending broker orders",
            "flattening or adopting positions",
            "starting a live executor",
        ],
        "next_step": (
            "operator reviews this plan and separately authorizes an exact maintenance "
            "window; this tool has no execute mode"
        ),
    }
    unsigned = dict(plan)
    plan["plan_sha256"] = sha256_bytes(canonical_json_bytes(unsigned))
    return plan


def write_dummy_epoch_plan(path: Path, plan: dict[str, Any]) -> None:
    """Persist only the plan evidence; never touch its declared ledger or epoch."""

    source_path = Path(plan["source"]["path"]).resolve()
    output = path.resolve()
    if output == source_path:
        raise ValueError("plan output may not overwrite the legacy ledger")
    write_new_json(output, plan)
