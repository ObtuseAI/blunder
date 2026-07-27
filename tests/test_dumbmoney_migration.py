from __future__ import annotations

import codecs
import copy
import json
from pathlib import Path

import pytest

from deployment.dumbmoney.dummy_epoch import (
    build_dummy_legacy_epoch_plan,
    write_dummy_epoch_plan,
)
from deployment.dumbmoney.migration import (
    build_binary_review_template,
    load_json,
    plan_dopey_export,
    validate_export_package,
    validate_migration_manifest,
    write_export_package,
    write_quarantine_import,
)


def _fake_project_key(label: str) -> str:
    """Build a secret-shaped test canary without storing one in source."""

    return "".join(("sk", "-proj-", label))


def _complete_binary_review(package: Path) -> dict[str, object]:
    manifest = load_json(package / "migration-manifest.json")
    review = build_binary_review_template(manifest)
    review["status"] = "REVIEWED_NO_SECRETS"
    review["reviewer"] = "test-controlled-review"
    review["reviewed_at"] = "2026-07-26T12:05:00Z"
    review["methodology"] = [
        "isolated file-type and strings scan",
        "schema and provenance review",
    ]
    review["compressed_or_non_utf8_limitations_acknowledged"] = True
    for item in review["reviewed_files"]:
        item["result"] = "NO_SECRET_MATERIAL_FOUND"
    return review


def _build_dopey_source(root: Path) -> None:
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "strategy.json").write_text(
        '{"strategy":"defined-risk","paper_only":true}\n',
        encoding="utf-8",
    )
    (root / "configs" / "private.json").write_text(
        json.dumps(
            {"api_key": _fake_project_key("this-value-must-never-migrate")}
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "paper").mkdir()
    (root / "paper" / "trades.csv").write_text(
        "id,pnl\n1,4.25\n",
        encoding="utf-8",
    )
    (root / "archive").mkdir()
    (root / "archive" / "evidence.bin").write_bytes(b"\x00\x01historical")
    (root / ".env").write_text(
        "OPENROUTER_API_KEY=must-not-migrate\n",
        encoding="utf-8",
    )


def test_export_plan_is_hash_manifested_tiered_and_secret_excluding(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dopey"
    source.mkdir()
    _build_dopey_source(source)

    manifest = plan_dopey_export(
        source,
        migration_id="7cf2e0e2-643f-44c3-a660-858c9e378633",
        created_at="2026-07-26T12:00:00Z",
    )
    report = validate_migration_manifest(manifest)

    assert report.status == "PASS"
    included = {entry["source_path"]: entry for entry in manifest["entries"]}
    assert included["configs/strategy.json"]["tier"] == "tier-1-operational"
    assert included["paper/trades.csv"]["tier"] == "tier-2-recent"
    assert included["archive/evidence.bin"]["tier"] == "tier-3-cold"
    excluded = {entry["source_path"]: entry for entry in manifest["exclusions"]}
    assert ".env" in excluded
    assert "configs/private.json" in excluded
    assert excluded["configs/private.json"]["reason_codes"] == [
        "SECRET_CONTENT_PATTERN"
    ]
    assert "sk-proj" not in repr(manifest)
    assert manifest["broker_state_authoritative"] is False
    assert manifest["promotion_authority"] is False
    assert manifest["secret_scan"]["binary_review_status"] == "REQUIRED"


def test_export_and_import_only_to_quarantine(tmp_path: Path) -> None:
    source = tmp_path / "dopey"
    source.mkdir()
    _build_dopey_source(source)
    manifest = plan_dopey_export(
        source,
        migration_id="594cd1a2-ac9c-44ca-a673-ef33f021bce6",
        created_at="2026-07-26T12:00:00Z",
    )

    package = write_export_package(source, tmp_path / "transfer", manifest)
    validation = validate_export_package(package)
    assert validation.status == "PARTIAL"
    assert "BINARY_SECRET_REVIEW_REQUIRED" in {
        issue.code for issue in validation.issues
    }
    binary_review = _complete_binary_review(package)
    assert (
        validate_export_package(
            package,
            binary_review_attestation=binary_review,
        ).status
        == "PASS"
    )
    assert (package / "EXPORT_COMPLETE").is_file()
    assert not (package / "EXPORT_INCOMPLETE").exists()

    quarantine, receipt = write_quarantine_import(
        package,
        tmp_path / "target",
        binary_review_attestation=binary_review,
    )
    assert (
        quarantine
        == (
            tmp_path / "target" / "quarantine" / "594cd1a2-ac9c-44ca-a673-ef33f021bce6"
        ).resolve()
    )
    assert (quarantine / "IMPORT_QUARANTINED").is_file()
    assert receipt["status"] == "QUARANTINED"
    assert receipt["promotion_authority"] is False
    assert receipt["broker_state_authoritative"] is False
    assert receipt["secret_review_status"] == "BINARY_REVIEW_ATTESTED"
    assert (quarantine / "source-migration-manifest.json").is_file()
    assert (quarantine / "binary-secret-review.json").is_file()
    assert not (tmp_path / "target" / "active").exists()


def test_tampered_export_package_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "dopey"
    source.mkdir()
    _build_dopey_source(source)
    manifest = plan_dopey_export(source)
    package = write_export_package(source, tmp_path / "transfer", manifest)
    payload = package / manifest["entries"][0]["packaged_path"]
    payload.write_bytes(payload.read_bytes() + b"tamper")

    report = validate_export_package(package)

    assert report.status == "FAIL"
    assert {
        "MIGRATION_PAYLOAD_SIZE_MISMATCH",
        "MIGRATION_PAYLOAD_HASH_MISMATCH",
    } & {issue.code for issue in report.issues}


def test_api_token_embedded_in_binary_payload_is_excluded_with_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dopey"
    source.mkdir()
    (source / "history.db").write_bytes(
        b"SQLite format 3\x00\x00\x00api_key="
        + _fake_project_key(
            "binary-token-that-must-not-migrate"
        ).encode("ascii")
        + b"\x00\xff"
    )
    (source / "safe.bin").write_bytes(b"\x00\xffmarket evidence\x00")

    manifest = plan_dopey_export(source)

    excluded = {item["source_path"]: item for item in manifest["exclusions"]}
    assert excluded["history.db"]["reason_codes"] == ["SECRET_CONTENT_PATTERN"]
    assert "openai-style-key" in excluded["history.db"]["matched_rule_ids"]
    included = {item["source_path"]: item for item in manifest["entries"]}
    assert included["safe.bin"]["content_scan"] == "BINARY_BEST_EFFORT_SECRET_SCAN"
    assert "sk-proj-binary-token" not in repr(manifest)


@pytest.mark.parametrize(
    ("encoding", "bom"),
    [
        ("utf-16-le", codecs.BOM_UTF16_LE),
        ("utf-16-be", codecs.BOM_UTF16_BE),
    ],
)
def test_utf16_secret_is_excluded_during_export_planning(
    tmp_path: Path,
    encoding: str,
    bom: bytes,
) -> None:
    source = tmp_path / "dopey"
    source.mkdir()
    secret = json.dumps(
        {"api_key": _fake_project_key("utf16-secret-must-not-migrate")}
    )
    (source / f"settings-{encoding}.json").write_bytes(bom + secret.encode(encoding))

    manifest = plan_dopey_export(source)

    assert manifest["entries"] == []
    assert manifest["exclusions"][0]["reason_codes"] == ["SECRET_CONTENT_PATTERN"]
    assert "openai-style-key" in manifest["exclusions"][0]["matched_rule_ids"]
    assert "sk-proj-utf16-secret" not in repr(manifest)


@pytest.mark.parametrize(
    ("encoding", "bom"),
    [
        ("utf-16-le", codecs.BOM_UTF16_LE),
        ("utf-16-be", codecs.BOM_UTF16_BE),
    ],
)
def test_utf16_secret_is_detected_again_during_import_rescan(
    tmp_path: Path,
    encoding: str,
    bom: bytes,
) -> None:
    source = tmp_path / "dopey"
    source.mkdir()
    path = source / "settings.json"
    path.write_bytes(bom + '{"label":"safe-history"}'.encode(encoding))
    manifest = plan_dopey_export(source)
    assert manifest["entries"][0]["content_scan"] == "BINARY_BEST_EFFORT_SECRET_SCAN"
    assert manifest["entries"][0]["encoding_scan"]["nul_bytes_detected"] is True
    package = write_export_package(source, tmp_path / "transfer", manifest)
    payload = package / manifest["entries"][0]["packaged_path"]
    payload.write_bytes(
        bom
        + json.dumps(
            {"api_key": _fake_project_key("import-rescan-secret")}
        ).encode(encoding)
    )

    report = validate_export_package(package)

    assert report.status == "FAIL"
    assert "MIGRATION_SECRET_RESCAN_FAILED" in {issue.code for issue in report.issues}


def test_excluded_hidden_directory_is_pruned_without_reading_its_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dopey"
    hidden = source / ".git" / "objects"
    hidden.mkdir(parents=True)
    (hidden / "object").write_bytes(
        b"api_key="
        + _fake_project_key(
            "hidden-value-that-must-not-be-read"
        ).encode("ascii")
    )
    (source / "strategy.json").write_text('{"safe":true}', encoding="utf-8")

    manifest = plan_dopey_export(source)

    assert {entry["source_path"] for entry in manifest["entries"]} == {"strategy.json"}
    assert any(
        exclusion["source_path"] == ".git"
        and "EXCLUDED_DIRECTORY" in exclusion["reason_codes"]
        for exclusion in manifest["exclusions"]
    )
    assert "sk-proj-hidden-value" not in repr(manifest)


def test_manifest_path_traversal_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "dopey"
    source.mkdir()
    _build_dopey_source(source)
    manifest = copy.deepcopy(plan_dopey_export(source))
    manifest["entries"][0]["packaged_path"] = "../escape"

    report = validate_migration_manifest(manifest)

    assert report.status == "FAIL"
    assert "MIGRATION_PATH_INVALID" in {issue.code for issue in report.issues}


def test_manifest_rejects_unexpected_fields_that_could_carry_secrets(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dopey"
    source.mkdir()
    _build_dopey_source(source)
    manifest = plan_dopey_export(source)
    manifest["credential_dump"] = "forbidden"

    report = validate_migration_manifest(manifest)

    assert report.status == "FAIL"
    assert "MIGRATION_FIELDS_FORBIDDEN" in {issue.code for issue in report.issues}


def test_dummy_epoch_planner_does_not_modify_ledger_or_create_epoch(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "runtime" / "autonomy" / "ledger.db"
    ledger.parent.mkdir(parents=True)
    original = b"SQLite format 3\x00" + bytes(4096)
    ledger.write_bytes(original)
    before = ledger.stat()
    planned_epoch = tmp_path / "new-operational-epoch"

    plan = build_dummy_legacy_epoch_plan(
        ledger,
        planned_epoch_root=planned_epoch,
        plan_id="e3793b40-00db-49f9-b571-23a8579df1d6",
        created_at="2026-07-26T12:00:00Z",
    )

    after = ledger.stat()
    assert ledger.read_bytes() == original
    assert before.st_size == after.st_size
    assert before.st_mtime_ns == after.st_mtime_ns
    assert not planned_epoch.exists()
    assert plan["status"] == "PLAN_ONLY"
    assert plan["source"]["live_path_detected"] is True
    assert plan["inspection"] == {
        "sqlite_connection_opened": False,
        "checkpoint_requested": False,
        "wal_modified": False,
        "broker_contacted": False,
        "network_contacted": False,
        "source_files_written": False,
    }
    assert "PRAGMA wal_checkpoint" in plan["prohibited_automatic_actions"]


def test_dummy_epoch_schema_rejects_unknown_and_missing_inspection_fields(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.db"
    ledger.write_bytes(b"SQLite format 3\x00" + bytes(128))
    plan = build_dummy_legacy_epoch_plan(
        ledger,
        planned_epoch_root=tmp_path / "epoch",
        plan_id="e3793b40-00db-49f9-b571-23a8579df1d6",
        created_at="2026-07-26T12:00:00Z",
    )
    schema_path = (
        Path(__file__).parents[1]
        / "deployment"
        / "dumbmoney"
        / "schemas"
        / "dummy-legacy-epoch-plan.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(plan)
    assert set(schema["required"]) == set(plan)

    inspection_schema = schema["properties"]["inspection"]
    inspection_fields = set(plan["inspection"])
    assert inspection_schema["additionalProperties"] is False
    assert set(inspection_schema["properties"]) == inspection_fields
    assert set(inspection_schema["required"]) == inspection_fields
    assert all(
        definition == {"const": False}
        for definition in inspection_schema["properties"].values()
    )

    def inspection_shape_is_accepted(candidate: dict[str, bool]) -> bool:
        fields = set(candidate)
        required = set(inspection_schema["required"])
        declared = set(inspection_schema["properties"])
        return required <= fields and fields <= declared

    unknown = copy.deepcopy(plan["inspection"])
    unknown["unreviewed_side_effect"] = False
    missing = copy.deepcopy(plan["inspection"])
    missing.pop("network_contacted")

    assert not inspection_shape_is_accepted(unknown)
    assert not inspection_shape_is_accepted(missing)


def test_dummy_epoch_plan_write_is_new_evidence_only(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.db"
    ledger.write_bytes(b"SQLite format 3\x00" + bytes(128))
    planned_epoch = tmp_path / "epoch"
    plan = build_dummy_legacy_epoch_plan(
        ledger,
        planned_epoch_root=planned_epoch,
    )
    output = tmp_path / "evidence" / "plan.json"

    write_dummy_epoch_plan(output, plan)

    assert output.is_file()
    assert ledger.is_file()
    assert not planned_epoch.exists()
