from __future__ import annotations

import copy
import codecs
import fnmatch
import hashlib
import mimetypes
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import (
    HEX_64_RE,
    ValidationReport,
    canonical_json_bytes,
    is_link_like,
    load_json,
    looks_unresolved,
    parse_utc,
    resolve_beneath,
    safe_relative_path,
    sha256_bytes,
    sha256_file,
    write_new_json,
)


MANIFEST_NAME = "migration-manifest.json"
TEXT_SCAN_CHUNK_BYTES = 1024 * 1024
TEXT_SCAN_OVERLAP_BYTES = 2048


def load_migration_policy(path: Path | None = None) -> dict[str, Any]:
    source = path or Path(__file__).resolve().parent / "migration-policy.v1.json"
    return load_json(source)


def _matches(path: str, patterns: list[str]) -> bool:
    normalized = path.replace("\\", "/").lower()
    basename = normalized.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatchcase(normalized, pattern.lower())
        or fnmatch.fnmatchcase(basename, pattern.lower())
        for pattern in patterns
    )


def _classify_tier(relative: str, policy: dict[str, Any]) -> str:
    for tier in ("tier-1-operational", "tier-2-recent", "tier-3-cold"):
        patterns = policy.get("tier_rules", {}).get(tier, [])
        if _matches(relative, patterns):
            return tier
    return "tier-3-cold"


def _compile_secret_patterns(
    policy: dict[str, Any],
) -> list[tuple[str, re.Pattern[str]]]:
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for item in policy.get("secret_content_patterns", []):
        if not isinstance(item, dict):
            raise ValueError("secret_content_patterns entries must be objects")
        name = item.get("id")
        expression = item.get("regex")
        if not isinstance(name, str) or not isinstance(expression, str):
            raise ValueError("secret content patterns require id and regex")
        compiled.append((name, re.compile(expression)))
    return compiled


def _hash_and_scan(
    path: Path,
    *,
    patterns: list[tuple[str, re.Pattern[str]]],
) -> tuple[str, list[str], dict[str, Any]]:
    digest = hashlib.sha256()
    findings: set[str] = set()
    overlap = b""
    nul_bytes_detected = False
    strict_utf8_valid = True
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    first_bytes = b""
    with path.open("rb") as handle:
        while chunk := handle.read(TEXT_SCAN_CHUNK_BYTES):
            digest.update(chunk)
            if not first_bytes:
                first_bytes = chunk[:4]
            if b"\x00" in chunk:
                nul_bytes_detected = True
            if strict_utf8_valid:
                try:
                    decoder.decode(chunk, final=False)
                except UnicodeDecodeError:
                    strict_utf8_valid = False
            window = overlap + chunk
            samples = (
                window.decode("utf-8", errors="ignore"),
                window.replace(b"\x00", b"").decode("ascii", errors="ignore"),
            )
            for pattern_id, pattern in patterns:
                if any(pattern.search(sample) for sample in samples):
                    findings.add(pattern_id)
            overlap = window[-TEXT_SCAN_OVERLAP_BYTES:]
    if strict_utf8_valid:
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            strict_utf8_valid = False
    bom = "NONE"
    if first_bytes.startswith(codecs.BOM_UTF16_LE):
        bom = "UTF16_LE"
    elif first_bytes.startswith(codecs.BOM_UTF16_BE):
        bom = "UTF16_BE"
    elif first_bytes.startswith(codecs.BOM_UTF8):
        bom = "UTF8"
    scan_facts = {
        "utf8_pattern_scan": True,
        "nul_stripped_ascii_pattern_scan": True,
        "strict_utf8_valid": strict_utf8_valid,
        "nul_bytes_detected": nul_bytes_detected,
        "bom": bom,
    }
    return digest.hexdigest(), sorted(findings), scan_facts


def _manifest_digest(manifest: dict[str, Any]) -> str:
    unsigned = copy.deepcopy(manifest)
    unsigned.pop("manifest_sha256", None)
    return sha256_bytes(canonical_json_bytes(unsigned))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _exclusion_record(
    relative: str,
    reason_codes: list[str],
    *,
    patterns: list[tuple[str, re.Pattern[str]]],
) -> dict[str, Any]:
    secret_path_rules = [
        pattern_id for pattern_id, pattern in patterns if pattern.search(relative)
    ]
    recorded_path = relative
    recorded_reasons = list(reason_codes)
    if secret_path_rules:
        recorded_path = f"_redacted/{sha256_bytes(relative.encode('utf-8'))}"
        if "SECRET_PATH_VALUE_REDACTED" not in recorded_reasons:
            recorded_reasons.append("SECRET_PATH_VALUE_REDACTED")
    result: dict[str, Any] = {
        "source_path": recorded_path,
        "reason_codes": recorded_reasons,
    }
    if secret_path_rules:
        result["matched_rule_ids"] = secret_path_rules
    return result


def plan_dopey_export(
    source_root: Path,
    *,
    policy: dict[str, Any] | None = None,
    migration_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    source = source_root.resolve()
    if not source.is_dir():
        raise ValueError(f"Dopey source root is not a directory: {source}")
    selected_policy = policy or load_migration_policy()
    if selected_policy.get("schema_version") != "dumbmoney.migration-policy.v1":
        raise ValueError("unsupported migration policy schema")
    patterns = _compile_secret_patterns(selected_policy)
    text_suffixes = {
        str(value).lower() for value in selected_policy.get("text_suffixes", [])
    }
    path_exclusions = [
        str(value) for value in selected_policy.get("excluded_path_patterns", [])
    ]
    entries: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []

    def raise_walk_error(error: OSError) -> None:
        raise error

    for current, directory_names, file_names in os.walk(
        source,
        topdown=True,
        onerror=raise_walk_error,
        followlinks=False,
    ):
        current_path = Path(current)
        retained_directories: list[str] = []
        for name in sorted(directory_names, key=str.casefold):
            path = current_path / name
            relative = path.relative_to(source).as_posix()
            hidden = any(part.startswith(".") for part in Path(relative).parts)
            excluded_by_policy = _matches(relative, path_exclusions)
            secret_path = any(pattern.search(relative) for _, pattern in patterns)
            if is_link_like(path) or hidden or excluded_by_policy or secret_path:
                reasons = ["EXCLUDED_DIRECTORY"]
                if is_link_like(path):
                    reasons.append("SYMLINK_FORBIDDEN")
                if hidden:
                    reasons.append("HIDDEN_PATH")
                if excluded_by_policy:
                    reasons.append("SECRET_PATH_PATTERN")
                exclusions.append(
                    _exclusion_record(relative, reasons, patterns=patterns)
                )
                continue
            retained_directories.append(name)
        directory_names[:] = retained_directories

        for name in sorted(file_names, key=str.casefold):
            path = current_path / name
            relative = path.relative_to(source).as_posix()
            if is_link_like(path):
                exclusions.append(
                    _exclusion_record(
                        relative,
                        ["SYMLINK_FORBIDDEN"],
                        patterns=patterns,
                    )
                )
                continue
            secret_path = any(pattern.search(relative) for _, pattern in patterns)
            if secret_path:
                exclusions.append(_exclusion_record(relative, [], patterns=patterns))
                continue
            hidden = any(part.startswith(".") for part in Path(relative).parts)
            if hidden or _matches(relative, path_exclusions):
                exclusions.append(
                    _exclusion_record(
                        relative,
                        ["HIDDEN_PATH" if hidden else "SECRET_PATH_PATTERN"],
                        patterns=patterns,
                    )
                )
                continue
            scan_text = path.suffix.lower() in text_suffixes
            stat_before = path.stat()
            digest, secret_findings, encoding_scan = _hash_and_scan(
                path,
                patterns=patterns,
            )
            stat_after = path.stat()
            if (
                stat_before.st_size != stat_after.st_size
                or stat_before.st_mtime_ns != stat_after.st_mtime_ns
            ):
                raise RuntimeError(f"source changed during export planning: {relative}")
            if secret_findings:
                exclusions.append(
                    {
                        "source_path": relative,
                        "reason_codes": ["SECRET_CONTENT_PATTERN"],
                        "matched_rule_ids": secret_findings,
                    }
                )
                continue
            tier = _classify_tier(relative, selected_policy)
            text_scan_is_sufficient = (
                scan_text
                and encoding_scan["strict_utf8_valid"]
                and not encoding_scan["nul_bytes_detected"]
                and encoding_scan["bom"] in {"NONE", "UTF8"}
            )
            entries.append(
                {
                    "source_path": relative,
                    "packaged_path": f"payload/{tier}/{relative}",
                    "tier": tier,
                    "sha256": digest,
                    "bytes": stat_after.st_size,
                    "mtime_ns": stat_after.st_mtime_ns,
                    "content_scan": (
                        "TEXT_SECRET_SCAN"
                        if text_scan_is_sufficient
                        else "BINARY_BEST_EFFORT_SECRET_SCAN"
                    ),
                    "encoding_scan": encoding_scan,
                    "media_type": mimetypes.guess_type(path.name)[0]
                    or "application/octet-stream",
                }
            )

    counts = {
        tier: sum(1 for entry in entries if entry["tier"] == tier)
        for tier in ("tier-1-operational", "tier-2-recent", "tier-3-cold")
    }
    total_bytes = sum(entry["bytes"] for entry in entries)
    binary_entry_count = sum(
        entry["content_scan"] == "BINARY_BEST_EFFORT_SECRET_SCAN" for entry in entries
    )
    manifest = {
        "schema_version": "dumbmoney.dopey-migration-manifest.v1",
        "migration_id": migration_id or str(uuid.uuid4()),
        "created_at": created_at or _utc_now(),
        "source_product": "Dopey",
        "destination_product": "DumbMoney",
        "source_root_disclosed": False,
        "policy_id": selected_policy.get("policy_id"),
        "state": "PLANNED",
        "broker_state_authoritative": False,
        "promotion_authority": False,
        "secret_scan": {
            "all_payload_bytes_scanned_in_utf8_and_nul_stripped_ascii_domains": True,
            "encoding_uncertainty_forces_binary_review": True,
            "compressed_or_non_utf8_limitations_acknowledged": True,
            "matching_files_excluded": sum(
                "SECRET_CONTENT_PATTERN" in exclusion["reason_codes"]
                for exclusion in exclusions
            ),
            "binary_entry_count": binary_entry_count,
            "binary_review_status": (
                "REQUIRED" if binary_entry_count else "NOT_APPLICABLE"
            ),
        },
        "entries": entries,
        "exclusions": exclusions,
        "counts_by_tier": counts,
        "included_bytes": total_bytes,
    }
    manifest["manifest_sha256"] = _manifest_digest(manifest)
    return manifest


def validate_migration_manifest(manifest: dict[str, Any]) -> ValidationReport:
    report = ValidationReport("dopey-migration-manifest")
    allowed_manifest_fields = {
        "schema_version",
        "migration_id",
        "created_at",
        "source_product",
        "destination_product",
        "source_root_disclosed",
        "policy_id",
        "state",
        "broker_state_authoritative",
        "promotion_authority",
        "secret_scan",
        "entries",
        "exclusions",
        "counts_by_tier",
        "included_bytes",
        "manifest_sha256",
    }
    extra_manifest_fields = sorted(set(manifest) - allowed_manifest_fields)
    if extra_manifest_fields:
        report.error(
            "MIGRATION_FIELDS_FORBIDDEN",
            f"unexpected manifest fields: {extra_manifest_fields}",
            "manifest",
        )
    if manifest.get("schema_version") != "dumbmoney.dopey-migration-manifest.v1":
        report.error(
            "MIGRATION_SCHEMA_UNSUPPORTED",
            "schema_version must be dumbmoney.dopey-migration-manifest.v1",
            "schema_version",
        )
    try:
        uuid.UUID(str(manifest.get("migration_id")))
    except (ValueError, TypeError, AttributeError):
        report.error(
            "MIGRATION_ID_INVALID",
            "migration_id must be a UUID",
            "migration_id",
        )
    try:
        parse_utc(manifest.get("created_at"))
    except (TypeError, ValueError) as exc:
        report.error(
            "MIGRATION_TIME_INVALID",
            str(exc),
            "created_at",
        )
    if not isinstance(manifest.get("policy_id"), str) or not manifest.get("policy_id"):
        report.error(
            "MIGRATION_POLICY_ID_INVALID",
            "policy_id must be a non-empty string",
            "policy_id",
        )
    if manifest.get("source_product") != "Dopey":
        report.error(
            "MIGRATION_SOURCE_INVALID",
            "source_product must be Dopey",
            "source_product",
        )
    if manifest.get("destination_product") != "DumbMoney":
        report.error(
            "MIGRATION_DESTINATION_INVALID",
            "destination_product must be DumbMoney",
            "destination_product",
        )
    if manifest.get("state") not in {"PLANNED", "EXPORTED"}:
        report.error(
            "MIGRATION_STATE_INVALID",
            "state must be PLANNED or EXPORTED",
            "state",
        )
    for forbidden_true in (
        "source_root_disclosed",
        "broker_state_authoritative",
        "promotion_authority",
    ):
        if manifest.get(forbidden_true) is not False:
            report.error(
                "MIGRATION_AUTHORITY_OR_SECRET_FLAG_INVALID",
                f"{forbidden_true} must remain false",
                forbidden_true,
            )

    expected_manifest_digest = manifest.get("manifest_sha256")
    if (
        not isinstance(expected_manifest_digest, str)
        or HEX_64_RE.fullmatch(expected_manifest_digest) is None
    ):
        report.error(
            "MIGRATION_MANIFEST_DIGEST_INVALID",
            "manifest_sha256 must be a SHA-256 digest",
            "manifest_sha256",
        )
    else:
        try:
            observed_manifest_digest = _manifest_digest(manifest)
        except (TypeError, ValueError) as exc:
            report.error(
                "MIGRATION_MANIFEST_NOT_CANONICAL",
                str(exc),
                "manifest",
            )
        else:
            if observed_manifest_digest != expected_manifest_digest:
                report.error(
                    "MIGRATION_MANIFEST_DIGEST_MISMATCH",
                    "manifest contents differ from manifest_sha256",
                    "manifest_sha256",
                )

    entries = manifest.get("entries")
    if not isinstance(entries, list):
        report.error("MIGRATION_ENTRIES_MISSING", "entries must be an array", "entries")
        entries = []
    source_paths: set[str] = set()
    packaged_paths: set[str] = set()
    computed_counts = {
        "tier-1-operational": 0,
        "tier-2-recent": 0,
        "tier-3-cold": 0,
    }
    computed_bytes = 0
    for index, entry in enumerate(entries):
        prefix = f"entries[{index}]"
        if not isinstance(entry, dict):
            report.error(
                "MIGRATION_ENTRY_INVALID",
                "migration entries must be objects",
                prefix,
            )
            continue
        allowed_entry_fields = {
            "source_path",
            "packaged_path",
            "tier",
            "sha256",
            "bytes",
            "mtime_ns",
            "content_scan",
            "encoding_scan",
            "media_type",
        }
        extra_entry_fields = sorted(set(entry) - allowed_entry_fields)
        if extra_entry_fields:
            report.error(
                "MIGRATION_ENTRY_FIELDS_FORBIDDEN",
                f"unexpected entry fields: {extra_entry_fields}",
                prefix,
            )
        try:
            source_path = safe_relative_path(entry.get("source_path")).as_posix()
            packaged_path = safe_relative_path(entry.get("packaged_path")).as_posix()
        except ValueError as exc:
            report.error("MIGRATION_PATH_INVALID", str(exc), prefix)
            continue
        tier = entry.get("tier")
        if tier not in computed_counts:
            report.error(
                "MIGRATION_TIER_INVALID",
                "entry tier is not recognized",
                f"{prefix}.tier",
            )
            continue
        expected_packaged = f"payload/{tier}/{source_path}"
        if packaged_path != expected_packaged:
            report.error(
                "MIGRATION_PACKAGED_PATH_INVALID",
                "packaged_path must be derived from tier and source_path",
                f"{prefix}.packaged_path",
            )
        if source_path in source_paths or packaged_path in packaged_paths:
            report.error(
                "MIGRATION_PATH_DUPLICATED",
                "source and packaged paths must be unique",
                prefix,
            )
        source_paths.add(source_path)
        packaged_paths.add(packaged_path)
        digest = entry.get("sha256")
        if not isinstance(digest, str) or HEX_64_RE.fullmatch(digest) is None:
            report.error(
                "MIGRATION_ENTRY_DIGEST_INVALID",
                "entry sha256 must be a SHA-256 digest",
                f"{prefix}.sha256",
            )
        size = entry.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            report.error(
                "MIGRATION_ENTRY_SIZE_INVALID",
                "entry bytes must be a non-negative integer",
                f"{prefix}.bytes",
            )
        else:
            computed_bytes += size
        mtime_ns = entry.get("mtime_ns")
        if not isinstance(mtime_ns, int) or isinstance(mtime_ns, bool) or mtime_ns < 0:
            report.error(
                "MIGRATION_ENTRY_MTIME_INVALID",
                "entry mtime_ns must be a non-negative integer",
                f"{prefix}.mtime_ns",
            )
        if not isinstance(entry.get("media_type"), str) or not entry.get("media_type"):
            report.error(
                "MIGRATION_ENTRY_MEDIA_TYPE_INVALID",
                "entry media_type must be a non-empty string",
                f"{prefix}.media_type",
            )
        if entry.get("content_scan") not in {
            "TEXT_SECRET_SCAN",
            "BINARY_BEST_EFFORT_SECRET_SCAN",
        }:
            report.error(
                "MIGRATION_CONTENT_SCAN_INVALID",
                "content_scan must describe text or best-effort binary scanning",
                f"{prefix}.content_scan",
            )
        encoding_scan = entry.get("encoding_scan")
        expected_encoding_keys = {
            "utf8_pattern_scan",
            "nul_stripped_ascii_pattern_scan",
            "strict_utf8_valid",
            "nul_bytes_detected",
            "bom",
        }
        if not isinstance(encoding_scan, dict):
            report.error(
                "MIGRATION_ENCODING_SCAN_MISSING",
                "encoding_scan must be an object",
                f"{prefix}.encoding_scan",
            )
        else:
            if set(encoding_scan) != expected_encoding_keys:
                report.error(
                    "MIGRATION_ENCODING_SCAN_FIELDS_INVALID",
                    "encoding_scan fields do not match the v1 contract",
                    f"{prefix}.encoding_scan",
                )
            for boolean_field in (
                "utf8_pattern_scan",
                "nul_stripped_ascii_pattern_scan",
                "strict_utf8_valid",
                "nul_bytes_detected",
            ):
                if not isinstance(encoding_scan.get(boolean_field), bool):
                    report.error(
                        "MIGRATION_ENCODING_SCAN_FLAG_INVALID",
                        f"{boolean_field} must be a boolean",
                        f"{prefix}.encoding_scan.{boolean_field}",
                    )
            if (
                encoding_scan.get("utf8_pattern_scan") is not True
                or encoding_scan.get("nul_stripped_ascii_pattern_scan") is not True
            ):
                report.error(
                    "MIGRATION_ENCODING_SCAN_DOMAIN_INCOMPLETE",
                    "both required secret-scan domains must be recorded",
                    f"{prefix}.encoding_scan",
                )
            if encoding_scan.get("bom") not in {
                "NONE",
                "UTF8",
                "UTF16_LE",
                "UTF16_BE",
            }:
                report.error(
                    "MIGRATION_ENCODING_SCAN_BOM_INVALID",
                    "encoding_scan.bom is not recognized",
                    f"{prefix}.encoding_scan.bom",
                )
            uncertainty = (
                encoding_scan.get("strict_utf8_valid") is not True
                or encoding_scan.get("nul_bytes_detected") is True
                or encoding_scan.get("bom") in {"UTF16_LE", "UTF16_BE"}
            )
            if uncertainty and entry.get("content_scan") != (
                "BINARY_BEST_EFFORT_SECRET_SCAN"
            ):
                report.error(
                    "MIGRATION_ENCODING_UNCERTAINTY_NOT_QUARANTINED",
                    "encoding uncertainty must force binary review",
                    f"{prefix}.content_scan",
                )
        computed_counts[tier] += 1

    if manifest.get("counts_by_tier") != computed_counts:
        report.error(
            "MIGRATION_TIER_COUNTS_MISMATCH",
            "counts_by_tier does not match entries",
            "counts_by_tier",
        )
    if manifest.get("included_bytes") != computed_bytes:
        report.error(
            "MIGRATION_BYTE_COUNT_MISMATCH",
            "included_bytes does not match entries",
            "included_bytes",
        )
    secret_scan = manifest.get("secret_scan")
    binary_count = sum(
        isinstance(entry, dict)
        and entry.get("content_scan") == "BINARY_BEST_EFFORT_SECRET_SCAN"
        for entry in entries
    )
    if not isinstance(secret_scan, dict):
        report.error(
            "MIGRATION_SECRET_SCAN_EVIDENCE_MISSING",
            "secret_scan evidence must be an object",
            "secret_scan",
        )
    else:
        allowed_secret_scan_fields = {
            "all_payload_bytes_scanned_in_utf8_and_nul_stripped_ascii_domains",
            "encoding_uncertainty_forces_binary_review",
            "compressed_or_non_utf8_limitations_acknowledged",
            "matching_files_excluded",
            "binary_entry_count",
            "binary_review_status",
        }
        extra_secret_scan_fields = sorted(set(secret_scan) - allowed_secret_scan_fields)
        if extra_secret_scan_fields:
            report.error(
                "MIGRATION_SECRET_SCAN_FIELDS_FORBIDDEN",
                f"unexpected secret_scan fields: {extra_secret_scan_fields}",
                "secret_scan",
            )
        if (
            secret_scan.get(
                "all_payload_bytes_scanned_in_utf8_and_nul_stripped_ascii_domains"
            )
            is not True
        ):
            report.error(
                "MIGRATION_BYTE_SCAN_NOT_ATTESTED",
                "manifest must record both required byte-stream scan domains",
                "secret_scan",
            )
        if secret_scan.get("encoding_uncertainty_forces_binary_review") is not True:
            report.error(
                "MIGRATION_ENCODING_REVIEW_POLICY_MISSING",
                "encoding uncertainty must force binary review",
                "secret_scan",
            )
        if (
            secret_scan.get("compressed_or_non_utf8_limitations_acknowledged")
            is not True
        ):
            report.error(
                "MIGRATION_BINARY_LIMITATION_NOT_ACKNOWLEDGED",
                "manifest must acknowledge compressed and non-UTF8 scan limitations",
                "secret_scan",
            )
        if secret_scan.get("binary_entry_count") != binary_count:
            report.error(
                "MIGRATION_BINARY_COUNT_MISMATCH",
                "binary_entry_count does not match entries",
                "secret_scan.binary_entry_count",
            )
        expected_review = "REQUIRED" if binary_count else "NOT_APPLICABLE"
        if secret_scan.get("binary_review_status") != expected_review:
            report.error(
                "MIGRATION_BINARY_REVIEW_STATE_INVALID",
                f"binary_review_status must be {expected_review}",
                "secret_scan.binary_review_status",
            )
        excluded_secret_matches = sum(
            isinstance(exclusion, dict)
            and "SECRET_CONTENT_PATTERN" in exclusion.get("reason_codes", [])
            for exclusion in manifest.get("exclusions", [])
        )
        if secret_scan.get("matching_files_excluded") != excluded_secret_matches:
            report.error(
                "MIGRATION_SECRET_EXCLUSION_COUNT_MISMATCH",
                "matching_files_excluded does not match exclusions",
                "secret_scan.matching_files_excluded",
            )

    exclusions = manifest.get("exclusions")
    if not isinstance(exclusions, list):
        report.error(
            "MIGRATION_EXCLUSIONS_MISSING",
            "exclusions must be an array, even when empty",
            "exclusions",
        )
    else:
        exclusion_paths: set[str] = set()
        for index, exclusion in enumerate(exclusions):
            if not isinstance(exclusion, dict):
                report.error(
                    "MIGRATION_EXCLUSION_INVALID",
                    "exclusion entries must be objects",
                    f"exclusions[{index}]",
                )
                continue
            allowed = {"source_path", "reason_codes", "matched_rule_ids"}
            extra = sorted(set(exclusion) - allowed)
            if extra:
                report.error(
                    "MIGRATION_EXCLUSION_DATA_FORBIDDEN",
                    f"exclusions may not record content or secret values: {extra}",
                    f"exclusions[{index}]",
                )
            try:
                exclusion_path = safe_relative_path(
                    exclusion.get("source_path")
                ).as_posix()
            except ValueError as exc:
                report.error(
                    "MIGRATION_EXCLUSION_PATH_INVALID",
                    str(exc),
                    f"exclusions[{index}].source_path",
                )
            else:
                if exclusion_path in exclusion_paths:
                    report.error(
                        "MIGRATION_EXCLUSION_PATH_DUPLICATED",
                        "excluded paths must be unique",
                        f"exclusions[{index}].source_path",
                    )
                if exclusion_path in source_paths:
                    report.error(
                        "MIGRATION_PATH_INCLUDED_AND_EXCLUDED",
                        "a path cannot be both included and excluded",
                        f"exclusions[{index}].source_path",
                    )
                exclusion_paths.add(exclusion_path)
            reason_codes = exclusion.get("reason_codes")
            if (
                not isinstance(reason_codes, list)
                or not reason_codes
                or any(
                    not isinstance(reason, str) or not reason for reason in reason_codes
                )
            ):
                report.error(
                    "MIGRATION_EXCLUSION_REASON_INVALID",
                    "reason_codes must contain non-empty identifiers",
                    f"exclusions[{index}].reason_codes",
                )
            matched_rule_ids = exclusion.get("matched_rule_ids", [])
            if not isinstance(matched_rule_ids, list) or any(
                not isinstance(rule_id, str) or not rule_id
                for rule_id in matched_rule_ids
            ):
                report.error(
                    "MIGRATION_EXCLUSION_RULE_IDS_INVALID",
                    "matched_rule_ids must contain identifiers only",
                    f"exclusions[{index}].matched_rule_ids",
                )

    report.facts.update(
        {
            "migration_id": manifest.get("migration_id"),
            "entry_count": len(entries),
            "excluded_count": len(exclusions) if isinstance(exclusions, list) else 0,
            "counts_by_tier": computed_counts,
        }
    )
    return report


def build_binary_review_template(manifest: dict[str, Any]) -> dict[str, Any]:
    binary_entries = [
        entry
        for entry in manifest.get("entries", [])
        if isinstance(entry, dict)
        and entry.get("content_scan") == "BINARY_BEST_EFFORT_SECRET_SCAN"
    ]
    return {
        "schema_version": "dumbmoney.binary-secret-review.v1",
        "migration_id": manifest.get("migration_id"),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "status": "REVIEW_REQUIRED",
        "reviewer": "REQUIRED_VALUE",
        "reviewed_at": "REQUIRED_VALUE",
        "methodology": [],
        "compressed_or_non_utf8_limitations_acknowledged": False,
        "reviewed_files": [
            {
                "packaged_path": entry["packaged_path"],
                "sha256": entry["sha256"],
                "result": "REVIEW_REQUIRED",
            }
            for entry in binary_entries
        ],
    }


def validate_binary_review_attestation(
    attestation: dict[str, Any],
    manifest: dict[str, Any],
) -> ValidationReport:
    report = ValidationReport("binary-secret-review")
    allowed_attestation_fields = {
        "schema_version",
        "migration_id",
        "manifest_sha256",
        "status",
        "reviewer",
        "reviewed_at",
        "methodology",
        "compressed_or_non_utf8_limitations_acknowledged",
        "reviewed_files",
    }
    extra_attestation_fields = sorted(set(attestation) - allowed_attestation_fields)
    if extra_attestation_fields:
        report.error(
            "BINARY_REVIEW_FIELDS_FORBIDDEN",
            f"unexpected attestation fields: {extra_attestation_fields}",
            "attestation",
        )
    if attestation.get("schema_version") != "dumbmoney.binary-secret-review.v1":
        report.error(
            "BINARY_REVIEW_SCHEMA_UNSUPPORTED",
            "schema_version must be dumbmoney.binary-secret-review.v1",
            "schema_version",
        )
    if attestation.get("migration_id") != manifest.get("migration_id"):
        report.error(
            "BINARY_REVIEW_MIGRATION_MISMATCH",
            "attestation migration_id does not match the package",
            "migration_id",
        )
    if attestation.get("manifest_sha256") != manifest.get("manifest_sha256"):
        report.error(
            "BINARY_REVIEW_MANIFEST_MISMATCH",
            "attestation does not bind the exact exported manifest",
            "manifest_sha256",
        )
    if attestation.get("status") != "REVIEWED_NO_SECRETS":
        report.error(
            "BINARY_REVIEW_NOT_APPROVED",
            "status must be REVIEWED_NO_SECRETS after an operator review",
            "status",
        )
    reviewer = attestation.get("reviewer")
    if not isinstance(reviewer, str) or looks_unresolved(reviewer):
        report.error(
            "BINARY_REVIEW_REVIEWER_MISSING",
            "reviewer must identify the human or controlled review process",
            "reviewer",
        )
    try:
        parse_utc(attestation.get("reviewed_at"))
    except (TypeError, ValueError) as exc:
        report.error(
            "BINARY_REVIEW_TIME_INVALID",
            str(exc),
            "reviewed_at",
        )
    methodology = attestation.get("methodology")
    if (
        not isinstance(methodology, list)
        or not methodology
        or any(not isinstance(item, str) or not item.strip() for item in methodology)
    ):
        report.error(
            "BINARY_REVIEW_METHODOLOGY_MISSING",
            "methodology must list at least one completed review technique",
            "methodology",
        )
    if attestation.get("compressed_or_non_utf8_limitations_acknowledged") is not True:
        report.error(
            "BINARY_REVIEW_LIMITATION_NOT_ACKNOWLEDGED",
            "attestation must acknowledge binary and compressed-data limitations",
            "compressed_or_non_utf8_limitations_acknowledged",
        )

    expected = {
        entry["packaged_path"]: entry["sha256"]
        for entry in manifest.get("entries", [])
        if isinstance(entry, dict)
        and entry.get("content_scan") == "BINARY_BEST_EFFORT_SECRET_SCAN"
    }
    reviewed_files = attestation.get("reviewed_files")
    if not isinstance(reviewed_files, list):
        report.error(
            "BINARY_REVIEW_FILES_MISSING",
            "reviewed_files must be an array",
            "reviewed_files",
        )
        reviewed_files = []
    observed: dict[str, str] = {}
    for index, item in enumerate(reviewed_files):
        if not isinstance(item, dict):
            report.error(
                "BINARY_REVIEW_FILE_INVALID",
                "reviewed file entries must be objects",
                f"reviewed_files[{index}]",
            )
            continue
        extra_file_fields = sorted(set(item) - {"packaged_path", "sha256", "result"})
        if extra_file_fields:
            report.error(
                "BINARY_REVIEW_FILE_FIELDS_FORBIDDEN",
                f"unexpected reviewed file fields: {extra_file_fields}",
                f"reviewed_files[{index}]",
            )
        path = item.get("packaged_path")
        digest = item.get("sha256")
        if item.get("result") != "NO_SECRET_MATERIAL_FOUND":
            report.error(
                "BINARY_REVIEW_FILE_NOT_CLEARED",
                "every binary file must be explicitly cleared",
                f"reviewed_files[{index}].result",
            )
        if isinstance(path, str) and isinstance(digest, str):
            if path in observed:
                report.error(
                    "BINARY_REVIEW_FILE_DUPLICATED",
                    "reviewed file paths must be unique",
                    f"reviewed_files[{index}]",
                )
            observed[path] = digest
    if observed != expected:
        report.error(
            "BINARY_REVIEW_COVERAGE_MISMATCH",
            "attestation must cover every binary path and exact payload digest",
            "reviewed_files",
        )
    report.facts["binary_file_count"] = len(expected)
    return report


def write_export_package(
    source_root: Path,
    destination_root: Path,
    manifest: dict[str, Any],
) -> Path:
    validation = validate_migration_manifest(manifest)
    if validation.status != "PASS":
        raise ValueError(f"migration manifest is not writable: {validation.status}")
    source = source_root.resolve()
    destination = destination_root.resolve()
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("export destination may not be inside the source tree")

    package_root = destination / f"dopey-{manifest['migration_id']}"
    package_root.mkdir(parents=True, exist_ok=False)
    incomplete = package_root / "EXPORT_INCOMPLETE"
    incomplete.write_text(
        "This package is incomplete and must not be imported.\n",
        encoding="utf-8",
    )
    for entry in manifest["entries"]:
        source_path = resolve_beneath(source, entry["source_path"])
        if not source_path.is_file() or is_link_like(source_path):
            raise ValueError(f"source changed during export: {entry['source_path']}")
        if sha256_file(source_path) != entry["sha256"]:
            raise ValueError(
                f"source hash changed during export: {entry['source_path']}"
            )
        destination_path = resolve_beneath(package_root, entry["packaged_path"])
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with (
            source_path.open("rb") as source_handle,
            destination_path.open("xb") as destination_handle,
        ):
            shutil.copyfileobj(source_handle, destination_handle, 1024 * 1024)
        if sha256_file(destination_path) != entry["sha256"]:
            raise ValueError(
                f"copied payload failed verification: {entry['packaged_path']}"
            )
    written_manifest = copy.deepcopy(manifest)
    written_manifest["state"] = "EXPORTED"
    written_manifest["manifest_sha256"] = _manifest_digest(written_manifest)
    write_new_json(package_root / MANIFEST_NAME, written_manifest)
    incomplete.rename(package_root / "EXPORT_COMPLETE")
    return package_root


def validate_export_package(
    package_root: Path,
    *,
    policy: dict[str, Any] | None = None,
    binary_review_attestation: dict[str, Any] | None = None,
) -> ValidationReport:
    package = package_root.resolve()
    report = ValidationReport("dopey-migration-package")
    if (package / "EXPORT_INCOMPLETE").exists():
        report.error(
            "MIGRATION_EXPORT_INCOMPLETE",
            "package contains an incomplete-export marker",
            str(package),
        )
    if not (package / "EXPORT_COMPLETE").is_file():
        report.error(
            "MIGRATION_EXPORT_COMPLETE_MARKER_MISSING",
            "package lacks an export-complete marker",
            str(package),
        )
    manifest_path = package / MANIFEST_NAME
    try:
        manifest = load_json(manifest_path)
    except (OSError, ValueError) as exc:
        report.error(
            "MIGRATION_MANIFEST_UNREADABLE",
            f"{type(exc).__name__}: {exc}",
            str(manifest_path),
        )
        return report
    manifest_report = validate_migration_manifest(manifest)
    report.extend(manifest_report.issues)
    if manifest_report.status == "FAIL":
        return report

    selected_policy = policy or load_migration_policy()
    patterns = _compile_secret_patterns(selected_policy)
    for entry in manifest["entries"]:
        try:
            path = resolve_beneath(package, entry["packaged_path"])
        except ValueError as exc:
            report.error(
                "MIGRATION_PAYLOAD_PATH_INVALID",
                str(exc),
                str(entry.get("packaged_path")),
            )
            continue
        if not path.is_file() or is_link_like(path):
            report.error(
                "MIGRATION_PAYLOAD_MISSING",
                "package payload is missing or is a symlink",
                str(path),
            )
            continue
        if path.stat().st_size != entry["bytes"]:
            report.error(
                "MIGRATION_PAYLOAD_SIZE_MISMATCH",
                "package payload size differs from manifest",
                str(path),
            )
        digest, findings, encoding_scan = _hash_and_scan(
            path,
            patterns=patterns,
        )
        if digest != entry["sha256"]:
            report.error(
                "MIGRATION_PAYLOAD_HASH_MISMATCH",
                "package payload differs from manifest",
                str(path),
            )
        if encoding_scan != entry.get("encoding_scan"):
            report.error(
                "MIGRATION_ENCODING_SCAN_MISMATCH",
                "payload encoding evidence differs from the manifest",
                str(path),
            )
        if findings:
            report.error(
                "MIGRATION_SECRET_RESCAN_FAILED",
                f"payload matched secret rules during import: {findings}",
                str(path),
            )
    binary_count = manifest["secret_scan"]["binary_entry_count"]
    if binary_count:
        if binary_review_attestation is None:
            report.partial(
                "BINARY_SECRET_REVIEW_REQUIRED",
                (
                    "binary or compressed payloads require an exact path-and-hash "
                    "review attestation before import or release"
                ),
                "binary-secret-review",
            )
        else:
            attestation_report = validate_binary_review_attestation(
                binary_review_attestation,
                manifest,
            )
            report.extend(attestation_report.issues)
    report.facts.update(manifest_report.facts)
    report.facts["package_root"] = str(package)
    report.facts["binary_secret_review_supplied"] = (
        binary_review_attestation is not None
    )
    return report


def write_quarantine_import(
    package_root: Path,
    target_root: Path,
    *,
    policy: dict[str, Any] | None = None,
    binary_review_attestation: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    package = package_root.resolve()
    target = target_root.resolve()
    validation = validate_export_package(
        package,
        policy=policy,
        binary_review_attestation=binary_review_attestation,
    )
    if validation.status != "PASS":
        raise ValueError(f"migration package is not importable: {validation.status}")
    try:
        target.relative_to(package)
    except ValueError:
        pass
    else:
        raise ValueError("quarantine target may not be inside the export package")
    manifest = load_json(package / MANIFEST_NAME)
    quarantine = target / "quarantine" / manifest["migration_id"]
    quarantine.mkdir(parents=True, exist_ok=False)
    marker = quarantine / "IMPORT_INCOMPLETE"
    marker.write_text(
        "This import is incomplete and has no promotion authority.\n",
        encoding="utf-8",
    )
    for entry in manifest["entries"]:
        source_path = resolve_beneath(package, entry["packaged_path"])
        destination_path = resolve_beneath(quarantine, entry["packaged_path"])
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with (
            source_path.open("rb") as source_handle,
            destination_path.open("xb") as destination_handle,
        ):
            shutil.copyfileobj(source_handle, destination_handle, 1024 * 1024)
        if sha256_file(destination_path) != entry["sha256"]:
            raise ValueError(
                f"quarantine copy failed verification: {entry['packaged_path']}"
            )
    write_new_json(quarantine / "source-migration-manifest.json", manifest)
    if binary_review_attestation is not None:
        write_new_json(
            quarantine / "binary-secret-review.json",
            binary_review_attestation,
        )

    receipt = {
        "schema_version": "dumbmoney.dopey-migration-receipt.v1",
        "migration_id": manifest["migration_id"],
        "imported_at": _utc_now(),
        "status": "QUARANTINED",
        "source_manifest_sha256": manifest["manifest_sha256"],
        "entry_count": len(manifest["entries"]),
        "counts_by_tier": manifest["counts_by_tier"],
        "included_bytes": manifest["included_bytes"],
        "excluded_count": len(manifest["exclusions"]),
        "broker_state_authoritative": False,
        "promotion_authority": False,
        "secret_review_status": (
            "BINARY_REVIEW_ATTESTED"
            if manifest["secret_scan"]["binary_entry_count"]
            else "NO_CONFIGURED_PATTERN_MATCHES"
        ),
        "binary_review_sha256": (
            sha256_bytes(canonical_json_bytes(binary_review_attestation))
            if binary_review_attestation is not None
            else None
        ),
        "required_next_gate": "SCHEMA_PROVENANCE_TIMESTAMP_AND_BROKER_RECONCILIATION",
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    write_new_json(quarantine / "migration-receipt.json", receipt)
    marker.rename(quarantine / "IMPORT_QUARANTINED")
    return quarantine, receipt
