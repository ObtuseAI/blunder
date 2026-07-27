from __future__ import annotations

import base64
import binascii
import re
from pathlib import PureWindowsPath
from typing import Any

from .common import ValidationReport, looks_unresolved, validate_hex_digest


CORE_CONFIG_FIELDS = {
    "schema",
    "data_root",
    "policy_path",
    "readiness_path",
    "core_public_key_path",
    "fund_lock_path",
    "service_manifest_path",
    "release_manifest_path",
    "bind_port",
    "release_id",
    "fund_lock_sha256",
    "service_manifest_sha256",
    "risk_policy_sha256",
    "core_public_key_sha256",
    "readiness_ttl_seconds",
    "processing_interval_milliseconds",
    "credential_targets",
    "role_public_keys_base64url",
}
EXPECTED_CREDENTIAL_TARGETS = {
    "core_signing_seed": "credential-target:DumbMoney/CoreSigner",
    "desktop_read_token": "credential-target:DumbMoney/DesktopReadToken",
    "operator_bearer_token": "credential-target:DumbMoney/OperatorToken",
    "allocator_bearer_token": "credential-target:DumbMoney/AllocatorToken",
    "dummy_cell_bearer_token": "credential-target:DumbMoney/DummyCellToken",
    "dopey_cell_bearer_token": "credential-target:DumbMoney/DopeyCellToken",
}
ROLE_NAMES = {
    "operator",
    "evaluator",
    "promoter",
    "allocator",
    "research",
    "dummy_venue",
    "dopey_venue",
    "migration",
}
REQUIRED_NONEMPTY_ROLES = ROLE_NAMES - {"migration"}
PUBLIC_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
TEMPLATE_PUBLIC_KEYS = {
    "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE",
    "AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI",
    "AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM",
    "BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ",
    "BQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQU",
    "BgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgYGBgY",
    "BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc",
}
EXPECTED_FIXED_PATHS = {
    "data_root": r"C:\ProgramData\DumbMoney\core",
    "readiness_path": (r"C:\ProgramData\DumbMoney\readiness\DumbMoneyCore.json"),
    "core_public_key_path": (r"C:\ProgramData\DumbMoney\core\keys\core-ed25519.pub"),
}
RELEASE_PATH_SUFFIXES = {
    "policy_path": r"configs\dumbmoney\risk-policy.v1.json",
    "fund_lock_path": "fund.lock.json",
    "service_manifest_path": "services.v1.json",
    "release_manifest_path": "release-manifest.json",
}


def _normalize_windows(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = PureWindowsPath(value)
    if not path.is_absolute() or path.drive.casefold() != "c:":
        return None
    return str(path)


def _decode_public_key(value: str) -> bytes | None:
    if PUBLIC_KEY_RE.fullmatch(value) is None:
        return None
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error):
        return None
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != value or len(decoded) != 32:
        return None
    return decoded


def validate_core_runner_config(
    config: dict[str, Any],
    *,
    state: str,
    role_bundle: dict[str, Any] | None = None,
) -> ValidationReport:
    report = ValidationReport("dumbmoney-core-runner-config")
    extra = sorted(set(config) - CORE_CONFIG_FIELDS)
    missing = sorted(CORE_CONFIG_FIELDS - set(config))
    if extra or missing:
        report.error(
            "CORE_RUNNER_CONFIG_FIELDS_INVALID",
            f"missing={missing}; extra={extra}",
            "config",
        )
    if config.get("schema") != "dumbmoney.core-runner-config.v1":
        report.error(
            "CORE_RUNNER_CONFIG_SCHEMA_UNSUPPORTED",
            "schema must be dumbmoney.core-runner-config.v1",
            "schema",
        )
    if state not in {"TEMPLATE", "SEALED"}:
        report.error(
            "CORE_RUNNER_CONFIG_STATE_INVALID",
            "release reference state must be TEMPLATE or SEALED",
            "state",
        )
    elif state == "TEMPLATE":
        report.partial(
            "CORE_RUNNER_CONFIG_TEMPLATE_NOT_SEALED",
            "the tracked Core runner configuration is a non-runnable template",
            "state",
        )

    release_id = config.get("release_id")
    if not isinstance(release_id, str) or not release_id.strip():
        report.error(
            "CORE_RUNNER_RELEASE_ID_INVALID",
            "release_id must be a non-empty string",
            "release_id",
        )
    elif looks_unresolved(release_id):
        method = report.error if state == "SEALED" else report.partial
        method(
            "CORE_RUNNER_RELEASE_ID_UNRESOLVED",
            "release_id must be resolved before sealing",
            "release_id",
        )

    for field, expected in EXPECTED_FIXED_PATHS.items():
        observed = _normalize_windows(config.get(field))
        if observed is None or observed.casefold() != expected.casefold():
            report.error(
                "CORE_RUNNER_FIXED_PATH_INVALID",
                f"{field} must equal {expected}",
                field,
            )
    for field, suffix in RELEASE_PATH_SUFFIXES.items():
        observed = _normalize_windows(config.get(field))
        if observed is None:
            report.error(
                "CORE_RUNNER_RELEASE_PATH_INVALID",
                f"{field} must be an absolute C: path",
                field,
            )
            continue
        expected_prefix = r"C:\Program Files\DumbMoney\releases"
        relative = observed[len(expected_prefix) :].lstrip("\\")
        parts = PureWindowsPath(relative).parts
        if (
            not observed.casefold().startswith((expected_prefix + "\\").casefold())
            or len(parts) < 2
            or str(PureWindowsPath(*parts[1:])).casefold() != suffix.casefold()
        ):
            report.error(
                "CORE_RUNNER_RELEASE_PATH_INVALID",
                f"{field} must be inside the versioned release and end in {suffix}",
                field,
            )
        if looks_unresolved(observed):
            method = report.error if state == "SEALED" else report.partial
            method(
                "CORE_RUNNER_RELEASE_PATH_UNRESOLVED",
                f"{field} contains an unresolved release directory",
                field,
            )

    if config.get("bind_port") != 0:
        report.error(
            "CORE_RUNNER_STATIC_PORT_FORBIDDEN",
            "bind_port must be zero so readiness advertises a dynamic loopback port",
            "bind_port",
        )
    if config.get("readiness_ttl_seconds") != 90:
        report.error(
            "CORE_RUNNER_READINESS_TTL_INVALID",
            "readiness_ttl_seconds must equal the service contract value 90",
            "readiness_ttl_seconds",
        )
    processing_interval = config.get("processing_interval_milliseconds")
    if (
        not isinstance(processing_interval, int)
        or isinstance(processing_interval, bool)
        or not 100 <= processing_interval <= 60_000
    ):
        report.error(
            "CORE_RUNNER_INTERVAL_INVALID",
            "processing_interval_milliseconds must be from 100 through 60000",
            "processing_interval_milliseconds",
        )

    for field in (
        "fund_lock_sha256",
        "service_manifest_sha256",
        "risk_policy_sha256",
        "core_public_key_sha256",
    ):
        validate_hex_digest(
            report,
            config.get(field),
            field_path=field,
            unresolved_is_partial=state != "SEALED",
        )

    targets = config.get("credential_targets")
    if targets != EXPECTED_CREDENTIAL_TARGETS:
        report.error(
            "CORE_RUNNER_CREDENTIAL_TARGETS_INVALID",
            "credential_targets must exactly match the six OS-protected target IDs",
            "credential_targets",
        )

    roles = config.get("role_public_keys_base64url")
    decoded_by_role: dict[str, set[bytes]] = {}
    if not isinstance(roles, dict) or set(roles) != ROLE_NAMES:
        report.error(
            "CORE_RUNNER_ROLE_KEYS_INVALID",
            "role_public_keys_base64url must contain the exact role set",
            "role_public_keys_base64url",
        )
    else:
        for role in sorted(ROLE_NAMES):
            values = roles.get(role)
            if (
                not isinstance(values, list)
                or (role in REQUIRED_NONEMPTY_ROLES and not values)
                or any(not isinstance(value, str) for value in values)
            ):
                report.error(
                    "CORE_RUNNER_ROLE_KEYS_INVALID",
                    f"{role} public keys are invalid or missing",
                    f"role_public_keys_base64url.{role}",
                )
                continue
            decoded = [_decode_public_key(value) for value in values]
            if any(value is None for value in decoded):
                report.error(
                    "CORE_RUNNER_PUBLIC_KEY_INVALID",
                    f"{role} contains a noncanonical Ed25519 public key",
                    f"role_public_keys_base64url.{role}",
                )
                continue
            decoded_set = {value for value in decoded if value is not None}
            if len(decoded_set) != len(decoded):
                report.error(
                    "CORE_RUNNER_PUBLIC_KEY_DUPLICATED",
                    f"{role} contains duplicate public keys",
                    f"role_public_keys_base64url.{role}",
                )
            if state == "SEALED" and any(
                value in TEMPLATE_PUBLIC_KEYS for value in values
            ):
                report.error(
                    "CORE_RUNNER_TEMPLATE_PUBLIC_KEY_FORBIDDEN",
                    f"{role} still contains a tracked template public key",
                    f"role_public_keys_base64url.{role}",
                )
            decoded_by_role[role] = decoded_set
        assignments: dict[bytes, list[str]] = {}
        for role, keys in decoded_by_role.items():
            for key in keys:
                assignments.setdefault(key, []).append(role)
        overlaps = {
            key.hex(): sorted(roles_for_key)
            for key, roles_for_key in assignments.items()
            if len(roles_for_key) > 1
        }
        if overlaps:
            report.error(
                "CORE_RUNNER_ROLE_KEYS_NOT_DISJOINT",
                f"public keys cross authority roles: {overlaps}",
                "role_public_keys_base64url",
            )

    if role_bundle is not None:
        if role_bundle.get("schema") != "dumbmoney.role-public-keys.v1":
            report.error(
                "CORE_RUNNER_ROLE_BUNDLE_SCHEMA_INVALID",
                "role-key bundle schema is unsupported",
                "role_bundle.schema",
            )
        if role_bundle.get("state") != state:
            report.error(
                "CORE_RUNNER_ROLE_BUNDLE_STATE_MISMATCH",
                "role-key bundle state must match Core config reference state",
                "role_bundle.state",
            )
        if role_bundle.get("role_public_keys_base64url") != roles:
            report.error(
                "CORE_RUNNER_ROLE_BUNDLE_MISMATCH",
                "Core config role keys differ from the sealed role-key bundle",
                "role_public_keys_base64url",
            )

    report.facts.update(
        {
            "state": state,
            "release_id": release_id,
            "credential_target_count": (
                len(targets) if isinstance(targets, dict) else 0
            ),
            "role_count": len(roles) if isinstance(roles, dict) else 0,
        }
    )
    return report
