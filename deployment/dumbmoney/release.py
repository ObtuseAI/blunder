from __future__ import annotations

import base64
import binascii
import re
import subprocess
from pathlib import Path, PureWindowsPath
from typing import Any

from .common import (
    HEX_40_RE,
    ValidationReport,
    canonical_json_bytes,
    is_link_like,
    load_json,
    looks_unresolved,
    parse_utc,
    path_traverses_link,
    resolve_beneath,
    safe_relative_path,
    sanitized_child_environment,
    sha256_bytes,
    sha256_file,
    validate_hex_digest,
)
from .core_config import validate_core_runner_config
from .service_plan import load_services_manifest, validate_services_manifest


REQUIRED_REPOSITORIES = frozenset(
    {"blunder", "dummy", "dopey", "doofus", "waterboy", "nimrod", "dimwit"}
)
INTERNAL_AUTH_COMPONENTS = REQUIRED_REPOSITORIES
REQUIRED_CONTRACT_ASSETS = {
    "signed-envelope-schema": (
        "blunder",
        "configs/dumbmoney/schemas/signed-envelope.v1.schema.json",
    ),
    "core-runner-config-schema": (
        "blunder",
        "configs/dumbmoney/schemas/core-runner-config.v1.schema.json",
    ),
    "model-gateway-runner-config-schema": (
        "blunder",
        "configs/dumbmoney/schemas/model-gateway-runner-config.v1.schema.json",
    ),
    "research-mesh-runner-config-schema": (
        "blunder",
        "configs/dumbmoney/schemas/research-mesh-runner-config.v1.schema.json",
    ),
    "dopey-robinhood-runner-config-schema": (
        "dopey",
        "configs/dumbmoney/schemas/dopey-robinhood-runner.v1.schema.json",
    ),
    "dummy-kalshi-runner-config-schema": (
        "dummy",
        "configs/dumbmoney/schemas/dummy-kalshi-runner.v1.schema.json",
    ),
    "capital-envelope-schema": (
        "blunder",
        "configs/dumbmoney/schemas/capital-envelope.v1.schema.json",
    ),
    "capital-request-schema": (
        "blunder",
        "configs/dumbmoney/schemas/capital-request.v1.schema.json",
    ),
    "venue-risk-snapshot-schema": (
        "blunder",
        "configs/dumbmoney/schemas/venue-risk-snapshot.v1.schema.json",
    ),
    "cell-command-page-schema": (
        "blunder",
        "configs/dumbmoney/schemas/cell-command-page.v1.schema.json",
    ),
    "execution-intent-schema": (
        "blunder",
        "configs/dumbmoney/schemas/execution-intent.v1.schema.json",
    ),
    "cell-contract-resolution-schema": (
        "blunder",
        "configs/dumbmoney/schemas/cell-contract-resolution.v1.schema.json",
    ),
    "cell-journal-head-anchor-schema": (
        "blunder",
        "configs/dumbmoney/schemas/cell-journal-head-anchor.v1.schema.json",
    ),
    "signed-capital-envelope-conformance-fixture": (
        "blunder",
        "configs/dumbmoney/fixtures/signed-capital-envelope.v1.json",
    ),
}
EXPECTED_COMMAND_COMPONENTS = {
    "DUMBMONEY_CORE_COMMAND": "blunder",
    "DUMBMONEY_RESEARCH_MESH_COMMAND": "blunder",
    "DUMBMONEY_MODEL_GATEWAY_COMMAND": "blunder",
    "DUMBMONEY_DUMMY_KALSHI_COMMAND": "dummy",
    "DUMBMONEY_DOPEY_ROBINHOOD_COMMAND": "dopey",
}
EXPECTED_COMMAND_EXECUTABLES = {
    "DUMBMONEY_CORE_COMMAND": "DumbMoneyCore.exe",
    "DUMBMONEY_RESEARCH_MESH_COMMAND": "DumbMoneyResearchMesh.exe",
    "DUMBMONEY_MODEL_GATEWAY_COMMAND": "DumbMoneyModelGateway.exe",
    "DUMBMONEY_DUMMY_KALSHI_COMMAND": "DumbMoneyDummyKalshi.exe",
    "DUMBMONEY_DOPEY_ROBINHOOD_COMMAND": "DumbMoneyDopeyRobinhood.exe",
}
CORE_RUNNER_CONFIG_INSTALL_PATH = r"C:\ProgramData\DumbMoney\config\core-runner.v1.json"
CORE_PUBLIC_KEY_INSTALL_PATH = r"C:\ProgramData\DumbMoney\core\keys\core-ed25519.pub"
MODEL_GATEWAY_CONFIG_INSTALL_PATH = (
    r"C:\ProgramData\DumbMoney\config\model-gateway-runner.v1.json"
)
MODEL_GATEWAY_PUBLIC_KEY_INSTALL_PATH = (
    r"C:\ProgramData\DumbMoney\model-gateway\keys\gateway-ed25519.pub"
)
RESEARCH_MESH_CONFIG_INSTALL_PATH = (
    r"C:\ProgramData\DumbMoney\config\research-mesh-runner.v1.json"
)
RESEARCH_MESH_PUBLIC_KEY_INSTALL_PATH = (
    r"C:\ProgramData\DumbMoney\research\keys\research-mesh-ed25519.pub"
)
RESEARCH_MESH_ALLOCATOR_PUBLIC_KEY_INSTALL_PATH = (
    r"C:\ProgramData\DumbMoney\research\keys\allocator-ed25519.pub"
)
DOPEY_ROBINHOOD_CONFIG_INSTALL_PATH = (
    r"C:\ProgramData\DumbMoney\config\dopey-robinhood-runner.v1.json"
)
DOPEY_CODEX_EXECUTABLE_RELATIVE_PATH = r"tools\codex\codex.exe"
DOPEY_OLLAMA_EXECUTABLE_RELATIVE_PATH = r"tools\ollama\ollama.exe"
DOPEY_OLLAMA_RUNTIME_EVIDENCE_INSTALL_PATH = (
    r"C:\ProgramData\DumbMoney\dopey-robinhood\evidence"
    r"\ollama-runtime-evidence.v1.json"
)
DUMMY_KALSHI_CONFIG_INSTALL_PATH = (
    r"C:\ProgramData\DumbMoney\config\dummy-kalshi-runner.v1.json"
)
DESKTOP_EXECUTABLE_RELATIVE_PATH = r"desktop\DumbMoney.exe"
REQUIRED_INSTALLED_ARTIFACTS = frozenset(
    {
        "core-runner-config",
        "risk-policy",
        "core-public-key",
        "role-public-key-bundle",
        "research-mesh-runner-config",
        "research-mesh-public-key",
        "research-mesh-allocator-public-key",
        "model-gateway-runner-config",
        "model-gateway-public-key",
        "dopey-robinhood-runner-config",
        "dopey-codex-executable",
        "dopey-ollama-executable",
        "dopey-ollama-runtime-evidence",
        "dummy-kalshi-runner-config",
        "desktop-executable",
    }
)
EXPECTED_INSTALLED_ARTIFACT_COMPONENTS = {
    "core-runner-config": "blunder",
    "risk-policy": "blunder",
    "core-public-key": "blunder",
    "role-public-key-bundle": "blunder",
    "research-mesh-runner-config": "blunder",
    "research-mesh-public-key": "blunder",
    "research-mesh-allocator-public-key": "blunder",
    "model-gateway-runner-config": "blunder",
    "model-gateway-public-key": "blunder",
    "dopey-robinhood-runner-config": "dopey",
    "dopey-codex-executable": "dopey",
    "dopey-ollama-executable": "dopey",
    "dopey-ollama-runtime-evidence": "dopey",
    "dummy-kalshi-runner-config": "dummy",
    "desktop-executable": "blunder",
}
EXPECTED_COMMAND_ARGUMENTS: dict[str, tuple[str, ...]] = {}
SECRET_VALUE_RE = re.compile(
    r"(?i)(?:sk-(?:proj-)?[a-z0-9_-]{8,}|bearer\s+\S+|"
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----)"
)
SENSITIVE_ARGUMENT_MARKERS = (
    "api-key",
    "api_key",
    "access-token",
    "access_token",
    "refresh-token",
    "refresh_token",
    "client-secret",
    "client_secret",
    "password",
    "authorization",
    "bearer",
    "private-key",
    "private_key",
)
LEGACY_LAUNCH_SIDECAR_SUFFIXES = frozenset(
    {".bat", ".cmd", ".lnk", ".ps1", ".py", ".pyw"}
)
ABSOLUTE_ARGUMENT_PATH_RE = re.compile(r"(?i)(?:^|=)(?:[a-z]:[\\/]|\\\\|/(?![-/]))")
REQUIRED_RELEASE_FILES = frozenset(
    {
        "deployment/dumbmoney/services.v1.json",
        "deployment/dumbmoney/migration-policy.v1.json",
        "deployment/dumbmoney/schemas/readiness-descriptor.v1.schema.json",
        "deployment/dumbmoney/schemas/windows-services.v1.schema.json",
        "deployment/dumbmoney/schemas/fund-lock.v1.schema.json",
        "deployment/dumbmoney/schemas/release-manifest.v1.schema.json",
        "deployment/dumbmoney/schemas/dopey-migration-manifest.v1.schema.json",
        "deployment/dumbmoney/schemas/binary-secret-review.v1.schema.json",
        "deployment/dumbmoney/schemas/dummy-legacy-epoch-plan.v1.schema.json",
        "deployment/dumbmoney/schemas/windows-runner-build.v1.schema.json",
        ("deployment/dumbmoney/schemas/windows-runner-build-provenance.v1.schema.json"),
        "deployment/dumbmoney/schemas/ollama-runtime-evidence.v1.schema.json",
        "deployment/dumbmoney/core-runner.v1.template.json",
        "deployment/dumbmoney/role-public-keys.v1.template.json",
        "deployment/dumbmoney/research-mesh-runner.v1.template.json",
        "deployment/dumbmoney/model-gateway-runner.v1.template.json",
        "deployment/dumbmoney/ollama-runtime-evidence.v1.template.json",
        "deployment/dumbmoney/windows-build.v1.template.json",
        "deployment/dumbmoney/__init__.py",
        "deployment/dumbmoney/common.py",
        "deployment/dumbmoney/core_config.py",
        "deployment/dumbmoney/package_windows.py",
        "deployment/dumbmoney/readiness.py",
        "deployment/dumbmoney/release.py",
        "deployment/dumbmoney/service_plan.py",
        "scripts/dumbmoney/build_windows_release.py",
        "scripts/dumbmoney/plan_windows_services.py",
        "scripts/dumbmoney/validate_readiness.py",
        "scripts/dumbmoney/validate_release.py",
        "scripts/dumbmoney/DumbMoney.DesktopLauncher.psm1",
        "scripts/dumbmoney/Launch-DumbMoneyDesktop.ps1",
        "docs/DUMBMONEY_DESKTOP_LAUNCHER.md",
        "docs/DUMBMONEY_INTERNAL_USE_AUTHORIZATION_TEMPLATE.md",
        "docs/DUMBMONEY_ARCHITECTURE.md",
        "docs/DUMBMONEY_MODEL_GATEWAY.md",
        "docs/DUMBMONEY_RELEASE_FORMAT.md",
        "docs/DUMBMONEY_WINDOWS_BUILD.md",
        "docs/DUMBMONEY_WINDOWS_RUNBOOK.md",
        "configs/dumbmoney/risk-policy.v1.json",
        "configs/dumbmoney/dummy-kalshi-runner.v1.template.json",
        "configs/dumbmoney/dopey-robinhood-runner.v1.template.json",
        "configs/dumbmoney/schemas/signed-envelope.v1.schema.json",
        "configs/dumbmoney/schemas/core-runner-config.v1.schema.json",
        "configs/dumbmoney/schemas/research-mesh-runner-config.v1.schema.json",
        "configs/dumbmoney/schemas/model-gateway-runner-config.v1.schema.json",
        "configs/dumbmoney/schemas/dummy-kalshi-runner.v1.schema.json",
        "configs/dumbmoney/schemas/dopey-robinhood-runner.v1.schema.json",
        "configs/dumbmoney/schemas/capital-envelope.v1.schema.json",
        "configs/dumbmoney/schemas/capital-request.v1.schema.json",
        "configs/dumbmoney/schemas/venue-risk-snapshot.v1.schema.json",
        "configs/dumbmoney/schemas/cell-command-page.v1.schema.json",
        "configs/dumbmoney/schemas/execution-intent.v1.schema.json",
        "configs/dumbmoney/schemas/cell-contract-resolution.v1.schema.json",
        "configs/dumbmoney/schemas/cell-journal-head-anchor.v1.schema.json",
        "configs/dumbmoney/fixtures/signed-capital-envelope.v1.json",
        "deployment/dumbmoney/winsw/DumbMoneyCore.xml.template",
        "deployment/dumbmoney/winsw/DumbMoneyResearchMesh.xml.template",
        "deployment/dumbmoney/winsw/DumbMoneyModelGateway.xml.template",
        "deployment/dumbmoney/winsw/DumbMoneyDummyKalshi.xml.template",
        "deployment/dumbmoney/winsw/DumbMoneyDopeyRobinhood.xml.template",
    }
)
PACKAGED_EXECUTABLE_PROVENANCE_FILES = frozenset(
    {
        "provenance/windows-runner-build.v1.json",
        "provenance/windows-runner-build-provenance.v1.json",
    }
)


def _reject_extra_fields(
    report: ValidationReport,
    value: dict[str, Any],
    allowed: set[str] | frozenset[str],
    *,
    code: str,
    path: str,
) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        report.error(code, f"unexpected fields: {extra}", path)


def _arguments_contain_inline_secret(arguments: list[str]) -> bool:
    for argument in arguments:
        lowered = argument.lower()
        if SECRET_VALUE_RE.search(argument):
            return True
        if any(marker in lowered for marker in SENSITIVE_ARGUMENT_MARKERS) and not any(
            safe_marker in lowered
            for safe_marker in (
                "-target",
                "_target",
                "-ref",
                "_ref",
                "credential-target:",
                "secret-ref:",
            )
        ):
            return True
    return False


def _extend_with_prefix(
    target: ValidationReport, source: ValidationReport, prefix: str
) -> None:
    for issue in source.issues:
        path = f"{prefix}.{issue.path}" if issue.path else prefix
        if issue.severity == "ERROR":
            target.error(issue.code, issue.message, path)
        elif issue.severity == "PARTIAL":
            target.partial(issue.code, issue.message, path)
        else:
            target.info(issue.code, issue.message, path)


def _validate_internal_authorization(
    report: ValidationReport,
    authorization: Any,
    *,
    release_root: Path,
) -> None:
    if not isinstance(authorization, dict):
        report.partial(
            "MISSING_SIGNED_INTERNAL_USE_AUTHORIZATION",
            (
                "a signed internal-use grant covering every bundled DumbMoney "
                "repository is required"
            ),
            "internal_use_authorization",
        )
        return
    _reject_extra_fields(
        report,
        authorization,
        {"status", "components", "artifact_path", "artifact_sha256"},
        code="INTERNAL_USE_AUTHORIZATION_FIELDS_FORBIDDEN",
        path="internal_use_authorization",
    )

    components = authorization.get("components")
    if not isinstance(components, list) or any(
        not isinstance(component, str) or not component.strip()
        for component in components
    ):
        report.error(
            "INTERNAL_USE_COMPONENTS_INVALID",
            "authorization components must be an array of non-empty strings",
            "internal_use_authorization.components",
        )
        components = []
    declared_component_list = [component.lower() for component in components]
    declared_components = set(declared_component_list)
    if len(declared_components) != len(declared_component_list):
        report.error(
            "INTERNAL_USE_COMPONENTS_DUPLICATED",
            "authorization components must be unique",
            "internal_use_authorization.components",
        )
    missing_components = sorted(INTERNAL_AUTH_COMPONENTS - declared_components)
    extra_components = sorted(declared_components - INTERNAL_AUTH_COMPONENTS)
    if missing_components:
        report.partial(
            "INTERNAL_USE_COMPONENTS_INCOMPLETE",
            f"internal-use grant is missing components: {missing_components}",
            "internal_use_authorization.components",
        )
    if extra_components:
        report.error(
            "INTERNAL_USE_COMPONENTS_UNKNOWN",
            f"internal-use grant names unbundled components: {extra_components}",
            "internal_use_authorization.components",
        )

    status = authorization.get("status")
    if status not in {"SIGNED", "UNSIGNED_REQUIRED"}:
        report.error(
            "INTERNAL_USE_AUTHORIZATION_STATUS_INVALID",
            "authorization status must be SIGNED or UNSIGNED_REQUIRED",
            "internal_use_authorization.status",
        )
        return
    is_signed = status == "SIGNED"
    if not is_signed:
        report.partial(
            "MISSING_SIGNED_INTERNAL_USE_AUTHORIZATION",
            "the tracked authorization is an unsigned template, not a grant",
            "internal_use_authorization.status",
        )

    artifact_path = authorization.get("artifact_path")
    try:
        artifact = resolve_beneath(release_root, artifact_path)
    except (TypeError, ValueError) as exc:
        report.error(
            "INTERNAL_USE_AUTHORIZATION_PATH_INVALID",
            str(exc),
            "internal_use_authorization.artifact_path",
        )
        return
    if not artifact.is_file():
        code = (
            "SIGNED_INTERNAL_USE_AUTHORIZATION_MISSING"
            if is_signed
            else "INTERNAL_USE_AUTHORIZATION_TEMPLATE_MISSING"
        )
        report.error(
            code, "hash-pinned authorization artifact is missing", str(artifact)
        )
        return
    if artifact.is_symlink():
        report.error(
            "INTERNAL_USE_AUTHORIZATION_SYMLINK_FORBIDDEN",
            "authorization artifacts may not be symlinks",
            str(artifact),
        )
        return

    expected_digest = authorization.get("artifact_sha256")
    if validate_hex_digest(
        report,
        expected_digest,
        field_path="internal_use_authorization.artifact_sha256",
        unresolved_is_partial=not is_signed,
    ):
        actual_digest = sha256_file(artifact)
        if actual_digest != expected_digest:
            report.error(
                "INTERNAL_USE_AUTHORIZATION_HASH_MISMATCH",
                "signed authorization hash does not match the fund lock",
                str(artifact),
            )

    try:
        content = artifact.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        report.error(
            "INTERNAL_USE_AUTHORIZATION_UNREADABLE",
            f"{type(exc).__name__}: {exc}",
            str(artifact),
        )
        return
    if not is_signed:
        if not re.search(
            r"(?im)^\s*\*\*Authorization status:\s*UNSIGNED\*\*\s*$", content
        ):
            report.error(
                "INTERNAL_USE_TEMPLATE_STATUS_INVALID",
                "unsigned authorization template must state its status prominently",
                str(artifact),
            )
        return

    placeholder_markers = (
        "[[",
        "<SIGNATURE",
        "UNSIGNED TEMPLATE",
        "REQUIRED —",
        "REQUIRED -",
    )
    if any(marker in content.upper() for marker in placeholder_markers):
        report.error(
            "INTERNAL_USE_AUTHORIZATION_HAS_PLACEHOLDERS",
            "authorization declares SIGNED but still contains template markers",
            str(artifact),
        )
    if not re.search(r"(?im)^\s*Authorization status:\s*SIGNED\s*$", content):
        report.error(
            "INTERNAL_USE_AUTHORIZATION_STATUS_NOT_ATTESTED",
            "signed artifact must contain 'Authorization status: SIGNED'",
            str(artifact),
        )
    signature_match = re.search(
        r"(?im)^\s*Owner signature:\s*(?P<signature>.+?)\s*$", content
    )
    if not signature_match or looks_unresolved(signature_match.group("signature")):
        report.error(
            "INTERNAL_USE_OWNER_SIGNATURE_MISSING",
            "signed artifact must contain a completed owner signature",
            str(artifact),
        )
    date_match = re.search(
        r"(?im)^\s*Date \(UTC\):\s*(?P<date>\d{4}-\d{2}-\d{2})\s*$", content
    )
    if not date_match:
        report.error(
            "INTERNAL_USE_DATE_MISSING",
            "signed artifact must contain a UTC signature date",
            str(artifact),
        )
    for component in sorted(INTERNAL_AUTH_COMPONENTS):
        if re.search(rf"(?im)^\s*-\s*{re.escape(component)}\s*$", content) is None:
            report.error(
                "INTERNAL_USE_COMPONENT_NOT_ATTESTED",
                (
                    "signed authorization artifact must list every bundled "
                    f"component; missing {component}"
                ),
                str(artifact),
            )


def _validate_git_tool_pin(
    report: ValidationReport,
    *,
    repo_roots: dict[str, Path],
    git_executable: Path | None,
    git_executable_sha256: str | None,
) -> bool:
    if git_executable is None or git_executable_sha256 is None:
        if repo_roots:
            report.partial(
                "REPOSITORY_GIT_TOOL_NOT_SUPPLIED",
                (
                    "repository checkouts cannot be inspected until both an "
                    "absolute Git executable path and its SHA-256 are supplied"
                ),
                "git_executable",
            )
        return False
    if not isinstance(git_executable, Path) or not git_executable.is_absolute():
        report.error(
            "REPOSITORY_GIT_EXECUTABLE_PATH_INVALID",
            "git_executable must be an absolute filesystem path",
            "git_executable",
        )
        return False
    if git_executable.name.casefold() not in {"git", "git.exe"}:
        report.error(
            "REPOSITORY_GIT_EXECUTABLE_NAME_INVALID",
            "the pinned Git executable basename must be git or git.exe",
            str(git_executable),
        )
        return False
    if not git_executable.is_file():
        report.error(
            "REPOSITORY_GIT_EXECUTABLE_MISSING",
            "the pinned Git executable is not a regular file",
            str(git_executable),
        )
        return False
    if path_traverses_link(git_executable):
        report.error(
            "REPOSITORY_GIT_EXECUTABLE_LINK_FORBIDDEN",
            "the pinned Git executable and its existing ancestors must not be links",
            str(git_executable),
        )
        return False
    if not validate_hex_digest(
        report,
        git_executable_sha256,
        field_path="git_executable_sha256",
        unresolved_is_partial=False,
    ):
        return False
    try:
        actual_digest = sha256_file(git_executable)
    except OSError as exc:
        report.error(
            "REPOSITORY_GIT_EXECUTABLE_UNREADABLE",
            f"{type(exc).__name__}: {exc}",
            str(git_executable),
        )
        return False
    if actual_digest != git_executable_sha256:
        report.error(
            "REPOSITORY_GIT_EXECUTABLE_HASH_MISMATCH",
            "the Git executable does not match its pinned SHA-256",
            str(git_executable),
        )
        return False
    return True


def _run_git(
    git_executable: Path,
    git_executable_sha256: str,
    repo_root: Path,
    *args: str,
) -> tuple[int, str]:
    try:
        child_environment = sanitized_child_environment(git_executable)
        if (
            not git_executable.is_absolute()
            or git_executable.name.casefold() not in {"git", "git.exe"}
            or not git_executable.is_file()
            or path_traverses_link(git_executable)
        ):
            return -1, "pinned Git executable became unavailable or link-backed"
        if sha256_file(git_executable) != git_executable_sha256:
            return -1, "pinned Git executable SHA-256 changed before invocation"
        completed = subprocess.run(
            [
                str(git_executable),
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-C",
                str(repo_root),
                *args,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=child_environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    output = completed.stdout.strip() or completed.stderr.strip()
    return completed.returncode, output


def _validate_repository_pin(
    report: ValidationReport,
    pin: dict[str, Any],
    *,
    repo_roots: dict[str, Path],
    git_executable: Path | None,
    git_executable_sha256: str | None,
) -> None:
    _reject_extra_fields(
        report,
        pin,
        {
            "name",
            "url",
            "commit",
            "dependency_lock_sha256",
            "package_sha256",
            "contract_schema_version",
        },
        code="REPOSITORY_PIN_FIELDS_FORBIDDEN",
        path="repositories",
    )
    raw_name = pin.get("name")
    name = raw_name.lower() if isinstance(raw_name, str) else ""
    prefix = f"repositories[{name or '?'}]"
    if not name or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", name) is None:
        report.error(
            "REPOSITORY_NAME_INVALID",
            "repository name must be a lowercase release identifier",
            f"{prefix}.name",
        )
    url = pin.get("url")
    expected_url = f"private-local://{name}" if name else None
    if url != expected_url:
        report.error(
            "REPOSITORY_URL_INVALID",
            (
                "repository source must be the exact private-local identifier; "
                "public GitHub and remote source URLs are forbidden"
            ),
            f"{prefix}.url",
        )
    commit = pin.get("commit")
    if looks_unresolved(commit):
        report.partial(
            "REPOSITORY_COMMIT_UNRESOLVED",
            f"{name or 'repository'} commit must be resolved during release assembly",
            f"{prefix}.commit",
        )
    elif not isinstance(commit, str) or HEX_40_RE.fullmatch(commit) is None:
        report.error(
            "REPOSITORY_COMMIT_INVALID",
            "commit must be a full 40-character lowercase Git object ID",
            f"{prefix}.commit",
        )

    for digest_name in ("dependency_lock_sha256", "package_sha256"):
        validate_hex_digest(
            report,
            pin.get(digest_name),
            field_path=f"{prefix}.{digest_name}",
        )
    contract_schema_version = pin.get("contract_schema_version")
    if (
        not isinstance(contract_schema_version, str)
        or not contract_schema_version.strip()
    ):
        report.error(
            "REPOSITORY_CONTRACT_SCHEMA_INVALID",
            "contract_schema_version must be a non-empty string",
            f"{prefix}.contract_schema_version",
        )

    root = repo_roots.get(name)
    if root is None:
        report.partial(
            "REPOSITORY_ROOT_NOT_SUPPLIED",
            f"no current checkout was supplied for {name}",
            prefix,
        )
        return
    if not root.is_dir():
        report.partial(
            "REPOSITORY_ROOT_MISSING",
            f"checkout for {name} is unavailable: {root}",
            str(root),
        )
        return
    if git_executable is None or git_executable_sha256 is None:
        return
    returncode, head = _run_git(
        git_executable,
        git_executable_sha256,
        root,
        "rev-parse",
        "HEAD",
    )
    if returncode:
        report.error(
            "REPOSITORY_GIT_READ_FAILED",
            f"could not read {name} HEAD: {head}",
            str(root),
        )
        return
    if isinstance(commit, str) and HEX_40_RE.fullmatch(commit) and head != commit:
        report.error(
            "REPOSITORY_COMMIT_MISMATCH",
            f"{name} HEAD {head} does not match pinned {commit}",
            str(root),
        )
    returncode, dirty = _run_git(
        git_executable,
        git_executable_sha256,
        root,
        "status",
        "--porcelain",
    )
    if returncode:
        report.error(
            "REPOSITORY_STATUS_READ_FAILED",
            f"could not inspect {name} worktree: {dirty}",
            str(root),
        )
    elif dirty:
        report.partial(
            "REPOSITORY_WORKTREE_DIRTY",
            f"{name} has uncommitted or untracked files and is not an immutable source",
            str(root),
        )


def _validate_contract_asset(
    report: ValidationReport,
    asset: Any,
    *,
    repo_roots: dict[str, Path],
) -> None:
    if not isinstance(asset, dict):
        report.error(
            "CONTRACT_ASSET_INVALID",
            "contract asset entries must be objects",
            "contract_assets",
        )
        return
    _reject_extra_fields(
        report,
        asset,
        {"name", "repository", "path", "sha256"},
        code="CONTRACT_ASSET_FIELDS_FORBIDDEN",
        path="contract_assets",
    )
    name = str(asset.get("name", "unnamed"))
    repository = str(asset.get("repository", "")).lower()
    relative = asset.get("path")
    prefix = f"contract_assets[{name}]"
    root = repo_roots.get(repository)
    if root is None or not root.is_dir():
        report.partial(
            "PARALLEL_CONTRACT_COMPONENT_MISSING",
            f"{name} cannot be checked until repository {repository} is supplied",
            prefix,
        )
        return
    try:
        path = resolve_beneath(root, relative)
    except (TypeError, ValueError) as exc:
        report.error(
            "CONTRACT_ASSET_PATH_INVALID",
            str(exc),
            f"{prefix}.path",
        )
        return
    if not path.is_file():
        report.partial(
            "PARALLEL_CONTRACT_COMPONENT_MISSING",
            f"{name} is not present yet",
            str(path),
        )
        return
    expected = asset.get("sha256")
    if not validate_hex_digest(
        report,
        expected,
        field_path=f"{prefix}.sha256",
    ):
        return
    actual = sha256_file(path)
    if actual != expected:
        report.error(
            "CONTRACT_ASSET_HASH_MISMATCH",
            f"{name} differs from the pinned contract fixture",
            str(path),
        )


def validate_fund_lock(
    fund_lock: dict[str, Any],
    *,
    release_root: Path,
    repo_roots: dict[str, Path] | None = None,
    git_executable: Path | None = None,
    git_executable_sha256: str | None = None,
) -> ValidationReport:
    report = ValidationReport("fund-lock")
    repo_roots = {key.lower(): value for key, value in (repo_roots or {}).items()}
    git_tool_is_valid = _validate_git_tool_pin(
        report,
        repo_roots=repo_roots,
        git_executable=git_executable,
        git_executable_sha256=git_executable_sha256,
    )
    checked_git_executable = git_executable if git_tool_is_valid else None
    checked_git_digest = git_executable_sha256 if git_tool_is_valid else None
    _reject_extra_fields(
        report,
        fund_lock,
        {
            "schema_version",
            "lock_id",
            "generated_at",
            "operating_mode",
            "deployment_scope",
            "public_distribution",
            "immutable",
            "repositories",
            "contract_assets",
            "internal_use_authorization",
        },
        code="FUND_LOCK_FIELDS_FORBIDDEN",
        path="fund_lock",
    )
    if fund_lock.get("schema_version") != "dumbmoney.fund-lock.v1":
        report.error(
            "FUND_LOCK_SCHEMA_UNSUPPORTED",
            "schema_version must be dumbmoney.fund-lock.v1",
            "schema_version",
        )
    if (
        not isinstance(fund_lock.get("lock_id"), str)
        or not fund_lock["lock_id"].strip()
    ):
        report.error(
            "FUND_LOCK_ID_INVALID",
            "lock_id must be a non-empty string",
            "lock_id",
        )
    if fund_lock.get("operating_mode") != "PERSONAL_PROP":
        report.error(
            "FUND_LOCK_MODE_INVALID",
            "v1 release lock must remain PERSONAL_PROP",
            "operating_mode",
        )
    if fund_lock.get("deployment_scope") != "PRIVATE_LOCAL_WINDOWS":
        report.error(
            "FUND_LOCK_DEPLOYMENT_SCOPE_INVALID",
            "v1 releases must remain private local Windows deployments",
            "deployment_scope",
        )
    if fund_lock.get("public_distribution") is not False:
        report.error(
            "FUND_LOCK_PUBLIC_DISTRIBUTION_FORBIDDEN",
            "fund lock may not authorize public distribution",
            "public_distribution",
        )
    if fund_lock.get("immutable") is not True:
        report.error(
            "FUND_LOCK_NOT_IMMUTABLE",
            "fund lock must declare immutable=true",
            "immutable",
        )
    try:
        parse_utc(fund_lock.get("generated_at"))
    except (TypeError, ValueError) as exc:
        report.error("FUND_LOCK_TIME_INVALID", str(exc), "generated_at")

    repositories = fund_lock.get("repositories")
    if not isinstance(repositories, list):
        report.error(
            "FUND_LOCK_REPOSITORIES_MISSING",
            "repositories must be an array",
            "repositories",
        )
        repositories = []
    names = {
        str(pin.get("name", "")).lower()
        for pin in repositories
        if isinstance(pin, dict)
    }
    repository_name_list = [
        str(pin.get("name", "")).lower()
        for pin in repositories
        if isinstance(pin, dict)
    ]
    duplicate_names = sorted(
        {name for name in repository_name_list if repository_name_list.count(name) > 1}
    )
    if duplicate_names:
        report.error(
            "FUND_LOCK_REPOSITORY_DUPLICATED",
            f"repository pins must be unique: {duplicate_names}",
            "repositories",
        )
    missing = sorted(REQUIRED_REPOSITORIES - names)
    if missing:
        report.partial(
            "FUND_LOCK_REQUIRED_REPOSITORIES_MISSING",
            f"required source pins are missing: {missing}",
            "repositories",
        )
    for pin in repositories:
        if isinstance(pin, dict):
            _validate_repository_pin(
                report,
                pin,
                repo_roots=repo_roots,
                git_executable=checked_git_executable,
                git_executable_sha256=checked_git_digest,
            )
        else:
            report.error(
                "REPOSITORY_PIN_INVALID",
                "repository pins must be objects",
                "repositories",
            )

    contract_assets = fund_lock.get("contract_assets")
    if not isinstance(contract_assets, list):
        report.error(
            "FUND_LOCK_CONTRACT_ASSETS_MISSING",
            "contract_assets must be an array",
            "contract_assets",
        )
        contract_assets = []
    contract_asset_names = [
        str(asset.get("name", ""))
        for asset in contract_assets
        if isinstance(asset, dict)
    ]
    duplicate_asset_names = sorted(
        {name for name in contract_asset_names if contract_asset_names.count(name) > 1}
    )
    if duplicate_asset_names:
        report.error(
            "FUND_LOCK_CONTRACT_ASSET_DUPLICATED",
            f"contract asset names must be unique: {duplicate_asset_names}",
            "contract_assets",
        )
    actual_asset_names = set(contract_asset_names)
    required_asset_names = set(REQUIRED_CONTRACT_ASSETS)
    if actual_asset_names != required_asset_names:
        report.error(
            "FUND_LOCK_CONTRACT_ASSET_SET_INVALID",
            (
                f"missing={sorted(required_asset_names - actual_asset_names)}; "
                f"extra={sorted(actual_asset_names - required_asset_names)}"
            ),
            "contract_assets",
        )
    for asset in contract_assets:
        if isinstance(asset, dict):
            asset_name = asset.get("name")
            expected = REQUIRED_CONTRACT_ASSETS.get(str(asset_name))
            if (
                expected is not None
                and (
                    asset.get("repository"),
                    asset.get("path"),
                )
                != expected
            ):
                report.error(
                    "FUND_LOCK_CONTRACT_ASSET_BINDING_INVALID",
                    (
                        f"{asset_name} must bind repository/path "
                        f"{expected[0]}:{expected[1]}"
                    ),
                    f"contract_assets[{asset_name}]",
                )
        _validate_contract_asset(report, asset, repo_roots=repo_roots)

    _validate_internal_authorization(
        report,
        fund_lock.get("internal_use_authorization"),
        release_root=release_root,
    )
    report.facts["repository_names"] = sorted(names)
    report.facts["supplied_repo_roots"] = sorted(repo_roots)
    report.facts["git_tool_checked"] = git_tool_is_valid
    return report


def _validate_manifest_file(
    report: ValidationReport,
    item: Any,
    *,
    release_root: Path,
    sealed: bool,
) -> None:
    if not isinstance(item, dict):
        report.error(
            "RELEASE_FILE_ENTRY_INVALID",
            "release file entries must be objects",
            "files",
        )
        return
    _reject_extra_fields(
        report,
        item,
        {"path", "sha256", "bytes", "component"},
        code="RELEASE_FILE_FIELDS_FORBIDDEN",
        path="files",
    )
    relative = item.get("path")
    component = item.get("component")
    if component not in REQUIRED_REPOSITORIES:
        report.error(
            "RELEASE_FILE_COMPONENT_INVALID",
            "release file component must name a fund-lock repository",
            f"files[{relative}].component",
        )
    try:
        path = resolve_beneath(release_root, relative)
    except (TypeError, ValueError) as exc:
        report.error(
            "RELEASE_FILE_PATH_INVALID",
            str(exc),
            "files.path",
        )
        return
    if not path.is_file():
        method = report.error if sealed else report.partial
        method(
            "RELEASE_FILE_MISSING",
            "hash-pinned release file is missing",
            str(path),
        )
        return
    if is_link_like(path):
        report.error(
            "RELEASE_FILE_SYMLINK_FORBIDDEN",
            "immutable release files may not be symlinks or junctions",
            str(path),
        )
        return
    expected = item.get("sha256")
    if not validate_hex_digest(
        report,
        expected,
        field_path=f"files[{relative}].sha256",
    ):
        return
    actual = sha256_file(path)
    if actual != expected:
        report.error(
            "RELEASE_FILE_HASH_MISMATCH",
            "release file does not match its immutable digest",
            str(path),
        )
    expected_bytes = item.get("bytes")
    if (
        not isinstance(expected_bytes, int)
        or isinstance(expected_bytes, bool)
        or expected_bytes < 0
    ):
        report.error(
            "RELEASE_FILE_SIZE_INVALID",
            "release file bytes must be a non-negative integer",
            f"files[{relative}].bytes",
        )
    elif path.stat().st_size != expected_bytes:
        report.error(
            "RELEASE_FILE_SIZE_MISMATCH",
            "release file size differs from the manifest",
            str(path),
        )
    if path.suffix.casefold() == ".exe":
        try:
            with path.open("rb") as handle:
                magic = handle.read(2)
        except OSError as exc:
            report.error(
                "RELEASE_EXECUTABLE_UNREADABLE",
                f"{type(exc).__name__}: {exc}",
                str(path),
            )
        else:
            if magic != b"MZ":
                report.error(
                    "RELEASE_EXECUTABLE_NOT_PE",
                    "release executables must have a Windows PE MZ header",
                    str(path),
                )
        try:
            sidecars = sorted(
                (
                    candidate.name
                    for candidate in path.parent.iterdir()
                    if candidate.is_file()
                    and candidate.stem.casefold() == path.stem.casefold()
                    and candidate.suffix.casefold() in LEGACY_LAUNCH_SIDECAR_SUFFIXES
                ),
                key=str.casefold,
            )
        except OSError as exc:
            report.error(
                "RELEASE_EXECUTABLE_SIDECAR_SCAN_FAILED",
                f"{type(exc).__name__}: {exc}",
                str(path.parent),
            )
            sidecars = []
        if sidecars:
            report.error(
                "RELEASE_EXECUTABLE_LEGACY_SIDECAR_FORBIDDEN",
                f"legacy launch sidecars cannot accompany an executable: {sidecars}",
                str(path.parent),
            )


def _normalize_absolute_windows_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = PureWindowsPath(value)
    if (
        not path.is_absolute()
        or path.drive.casefold() != "c:"
        or any(part in {".", ".."} for part in path.parts)
    ):
        return None
    return str(path)


def validate_ollama_runtime_evidence(
    evidence: dict[str, Any],
    *,
    release_state: str,
    release_id: Any,
    executable_record: dict[str, Any] | None,
    evidence_record: dict[str, Any] | None,
    dopey_config: dict[str, Any] | None,
) -> ValidationReport:
    report = ValidationReport("dumbmoney-ollama-runtime-evidence")
    sealed = release_state == "SEALED"
    allowed_fields = {
        "schema",
        "evidence_kind",
        "state",
        "evidence_id",
        "release_id",
        "verified_at",
        "verified_by",
        "verification_status",
        "process_identity",
        "process_identity_kind",
        "process_identity_sid",
        "windows_service",
        "credential_targets",
        "secret_refs",
        "broker_authority",
        "remote_model_auth",
        "executable_path",
        "executable_sha256",
        "executable_bytes",
        "listener_host",
        "listener_port",
        "listener_owner_identity",
        "listener_owner_executable_path",
        "listener_owner_executable_sha256",
        "model_provider",
        "model_tag",
        "model_digest",
        "model_store_path",
        "model_store_owner_identity",
        "model_store_owner_sid",
        "model_store_acl_sddl",
        "model_store_acl_sha256",
        "model_store_manifest",
        "model_store_manifest_sha256",
        "network_allowlist",
        "external_egress",
        "use_time_revalidation_required",
        "use_time_revalidation_fields",
    }
    _reject_extra_fields(
        report,
        evidence,
        allowed_fields,
        code="OLLAMA_EVIDENCE_FIELDS_FORBIDDEN",
        path="evidence",
    )
    missing = sorted(allowed_fields - set(evidence))
    if missing:
        report.error(
            "OLLAMA_EVIDENCE_FIELDS_MISSING",
            f"required evidence fields are missing: {missing}",
            "evidence",
        )

    fixed_values = {
        "schema": "dumbmoney.ollama-runtime-evidence.v1",
        "evidence_kind": "STATIC_INSTALLATION_ATTESTATION",
        "process_identity": "DumbMoneyOllama",
        "process_identity_kind": "NON_SERVICE_LOCAL_ACCOUNT",
        "windows_service": False,
        "credential_targets": [],
        "secret_refs": [],
        "broker_authority": "NONE",
        "remote_model_auth": "NONE",
        "listener_host": "127.0.0.1",
        "listener_port": 11434,
        "listener_owner_identity": "DumbMoneyOllama",
        "model_provider": "ollama",
        "model_store_path": r"C:\ProgramData\DumbMoney\ollama\models",
        "model_store_owner_identity": "DumbMoneyOllama",
        "network_allowlist": ["loopback:dopey"],
        "external_egress": False,
        "use_time_revalidation_required": True,
        "use_time_revalidation_fields": [
            "process_id",
            "process_identity",
            "process_identity_sid",
            "executable_path",
            "executable_sha256",
            "listener_owner_process_id",
            "listener_owner_identity",
            "listener_owner_process_identity_sid",
            "listener_owner_executable_path",
            "listener_owner_executable_sha256",
            "model_provider",
            "model_tag",
            "model_digest",
        ],
    }
    for field, expected in fixed_values.items():
        if evidence.get(field) != expected:
            report.error(
                "OLLAMA_EVIDENCE_BOUNDARY_MISMATCH",
                f"{field} must equal the fixed private-local Ollama boundary",
                field,
            )

    expected_evidence_state = "VERIFIED" if sealed else "TEMPLATE"
    if evidence.get("state") != expected_evidence_state:
        report.error(
            "OLLAMA_EVIDENCE_STATE_MISMATCH",
            f"evidence state must be {expected_evidence_state}",
            "state",
        )
    if evidence.get("verification_status") != "VERIFIED":
        method = report.error if sealed else report.partial
        method(
            "OLLAMA_EVIDENCE_NOT_VERIFIED",
            "runtime evidence must be independently VERIFIED before sealing",
            "verification_status",
        )

    for field in (
        "evidence_id",
        "verified_by",
        "process_identity_sid",
        "model_store_owner_sid",
    ):
        value = evidence.get(field)
        if not isinstance(value, str) or not value or looks_unresolved(value):
            method = report.error if sealed else report.partial
            method(
                "OLLAMA_EVIDENCE_IDENTITY_UNRESOLVED",
                f"{field} must be resolved before sealing",
                field,
            )
    process_identity_sid = evidence.get("process_identity_sid")
    if (
        isinstance(process_identity_sid, str)
        and not looks_unresolved(process_identity_sid)
        and re.fullmatch(r"S-1-[0-9]+(?:-[0-9]+)+", process_identity_sid) is None
    ):
        report.error(
            "OLLAMA_EVIDENCE_PROCESS_SID_INVALID",
            "process_identity_sid must be a canonical Windows SID",
            "process_identity_sid",
        )
    verified_at = evidence.get("verified_at")
    if looks_unresolved(verified_at):
        method = report.error if sealed else report.partial
        method(
            "OLLAMA_EVIDENCE_TIME_UNRESOLVED",
            "verified_at must be resolved before sealing",
            "verified_at",
        )
    else:
        try:
            parse_utc(verified_at)
        except (TypeError, ValueError) as exc:
            report.error(
                "OLLAMA_EVIDENCE_TIME_INVALID",
                str(exc),
                "verified_at",
            )

    def check_relation(field: str, expected: Any) -> None:
        observed = evidence.get(field)
        if observed == expected:
            return
        method = (
            report.partial
            if not sealed and (looks_unresolved(observed) or looks_unresolved(expected))
            else report.error
        )
        method(
            "OLLAMA_EVIDENCE_RELEASE_RELATION_MISMATCH",
            f"{field} must equal its independently pinned release value",
            field,
        )

    check_relation("model_store_owner_sid", process_identity_sid)
    check_relation("release_id", release_id)
    if executable_record is None:
        method = report.error if sealed else report.partial
        method(
            "OLLAMA_EXECUTABLE_PIN_MISSING",
            "the Ollama evidence requires a pinned executable artifact",
            "executable_path",
        )
    else:
        for field, record_field in (
            ("executable_path", "install_path"),
            ("executable_sha256", "sha256"),
            ("executable_bytes", "bytes"),
            ("listener_owner_executable_path", "install_path"),
            ("listener_owner_executable_sha256", "sha256"),
        ):
            check_relation(field, executable_record.get(record_field))

    for field in (
        "executable_sha256",
        "listener_owner_executable_sha256",
        "model_digest",
        "model_store_acl_sha256",
        "model_store_manifest_sha256",
    ):
        validate_hex_digest(
            report,
            evidence.get(field),
            field_path=field,
            unresolved_is_partial=not sealed,
        )
    executable_bytes = evidence.get("executable_bytes")
    if (
        not isinstance(executable_bytes, int)
        or isinstance(executable_bytes, bool)
        or executable_bytes <= 0
    ):
        method = (
            report.partial if not sealed and executable_bytes == 0 else report.error
        )
        method(
            "OLLAMA_EVIDENCE_EXECUTABLE_SIZE_UNRESOLVED",
            "executable_bytes must be positive before sealing",
            "executable_bytes",
        )

    acl_sddl = evidence.get("model_store_acl_sddl")
    acl_digest = evidence.get("model_store_acl_sha256")
    if (
        isinstance(acl_sddl, str)
        and acl_sddl
        and not looks_unresolved(acl_sddl)
        and isinstance(acl_digest, str)
        and not looks_unresolved(acl_digest)
        and sha256_bytes(acl_sddl.encode("utf-8")) != acl_digest
    ):
        report.error(
            "OLLAMA_EVIDENCE_ACL_DIGEST_MISMATCH",
            "model-store ACL digest must hash the exact UTF-8 SDDL bytes",
            "model_store_acl_sha256",
        )
    if (
        isinstance(acl_sddl, str)
        and not looks_unresolved(acl_sddl)
        and isinstance(process_identity_sid, str)
        and not looks_unresolved(process_identity_sid)
    ):
        if process_identity_sid not in acl_sddl:
            report.error(
                "OLLAMA_EVIDENCE_ACL_IDENTITY_MISSING",
                "model-store SDDL must name the dedicated Ollama account SID",
                "model_store_acl_sddl",
            )
        trustees = {
            match.group(1) for match in re.finditer(r"\([^)]*;;;([^)]+)\)", acl_sddl)
        }
        broad_trustees = sorted(trustees & {"AN", "AU", "BU", "IU", "NU", "SU", "WD"})
        if broad_trustees:
            report.error(
                "OLLAMA_EVIDENCE_ACL_BROAD_TRUSTEE_FORBIDDEN",
                (f"model-store SDDL grants a broad principal: {broad_trustees}"),
                "model_store_acl_sddl",
            )

    manifest = evidence.get("model_store_manifest")
    if not isinstance(manifest, list):
        report.error(
            "OLLAMA_EVIDENCE_MODEL_MANIFEST_INVALID",
            "model_store_manifest must be an array",
            "model_store_manifest",
        )
    else:
        if not manifest:
            method = report.error if sealed else report.partial
            method(
                "OLLAMA_EVIDENCE_MODEL_MANIFEST_EMPTY",
                "a sealed runtime requires a non-empty model-store inventory",
                "model_store_manifest",
            )
        normalized_paths: list[str] = []
        for index, item in enumerate(manifest):
            prefix = f"model_store_manifest[{index}]"
            if not isinstance(item, dict) or set(item) != {"path", "sha256", "bytes"}:
                report.error(
                    "OLLAMA_EVIDENCE_MODEL_FILE_INVALID",
                    "each model file must contain exactly path, sha256, and bytes",
                    prefix,
                )
                continue
            try:
                relative = safe_relative_path(item.get("path")).as_posix()
            except ValueError as exc:
                report.error(
                    "OLLAMA_EVIDENCE_MODEL_PATH_INVALID",
                    str(exc),
                    f"{prefix}.path",
                )
            else:
                normalized_paths.append(relative)
            validate_hex_digest(
                report,
                item.get("sha256"),
                field_path=f"{prefix}.sha256",
            )
            size = item.get("bytes")
            if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                report.error(
                    "OLLAMA_EVIDENCE_MODEL_SIZE_INVALID",
                    "model file bytes must be a positive integer",
                    f"{prefix}.bytes",
                )
        folded_paths = [path.casefold() for path in normalized_paths]
        if len(set(folded_paths)) != len(folded_paths):
            report.error(
                "OLLAMA_EVIDENCE_MODEL_PATH_DUPLICATED",
                "model-store paths must be unique under Windows path semantics",
                "model_store_manifest",
            )
        if normalized_paths != sorted(normalized_paths, key=str.casefold):
            report.error(
                "OLLAMA_EVIDENCE_MODEL_MANIFEST_UNSORTED",
                "model-store inventory must be deterministically sorted by path",
                "model_store_manifest",
            )
        manifest_digest = evidence.get("model_store_manifest_sha256")
        if (
            manifest
            and isinstance(manifest_digest, str)
            and not looks_unresolved(manifest_digest)
            and sha256_bytes(canonical_json_bytes(manifest)) != manifest_digest
        ):
            report.error(
                "OLLAMA_EVIDENCE_MODEL_MANIFEST_DIGEST_MISMATCH",
                "model-store manifest digest must hash its canonical JSON bytes",
                "model_store_manifest_sha256",
            )

    if dopey_config is not None:
        config_relations = {
            "executable_path": dopey_config.get("ollama_executable_path"),
            "executable_sha256": dopey_config.get("ollama_executable_sha256"),
            "model_tag": dopey_config.get("codex_local_model"),
            "model_digest": dopey_config.get("codex_local_model_digest"),
            "process_identity_sid": dopey_config.get("ollama_process_identity_sid"),
            "model_provider": dopey_config.get("codex_local_provider"),
        }
        for field, expected in config_relations.items():
            check_relation(field, expected)
        if evidence_record is not None:
            observed_evidence_digest = dopey_config.get(
                "ollama_runtime_evidence_sha256"
            )
            expected_evidence_digest = evidence_record.get("sha256")
            if observed_evidence_digest != expected_evidence_digest:
                method = (
                    report.partial
                    if not sealed
                    and (
                        looks_unresolved(observed_evidence_digest)
                        or looks_unresolved(expected_evidence_digest)
                    )
                    else report.error
                )
                method(
                    "OLLAMA_EVIDENCE_CONFIG_DIGEST_MISMATCH",
                    (
                        "Dopey config must pin the exact installed Ollama "
                        "runtime-evidence bytes"
                    ),
                    "ollama_runtime_evidence_sha256",
                )

    return report


def _expected_installed_path(
    name: str,
    *,
    release_id: Any,
    sealed: bool,
) -> str | None:
    if name == "core-runner-config":
        return CORE_RUNNER_CONFIG_INSTALL_PATH
    if name == "core-public-key":
        return CORE_PUBLIC_KEY_INSTALL_PATH
    if name == "model-gateway-runner-config":
        return MODEL_GATEWAY_CONFIG_INSTALL_PATH
    if name == "model-gateway-public-key":
        return MODEL_GATEWAY_PUBLIC_KEY_INSTALL_PATH
    if name == "research-mesh-runner-config":
        return RESEARCH_MESH_CONFIG_INSTALL_PATH
    if name == "research-mesh-public-key":
        return RESEARCH_MESH_PUBLIC_KEY_INSTALL_PATH
    if name == "research-mesh-allocator-public-key":
        return RESEARCH_MESH_ALLOCATOR_PUBLIC_KEY_INSTALL_PATH
    if name == "dopey-robinhood-runner-config":
        return DOPEY_ROBINHOOD_CONFIG_INSTALL_PATH
    if name == "dopey-ollama-runtime-evidence":
        return DOPEY_OLLAMA_RUNTIME_EVIDENCE_INSTALL_PATH
    if name == "dummy-kalshi-runner-config":
        return DUMMY_KALSHI_CONFIG_INSTALL_PATH
    if name not in {
        "risk-policy",
        "role-public-key-bundle",
        "dopey-codex-executable",
        "dopey-ollama-executable",
        "desktop-executable",
    }:
        return None
    release_directory = (
        release_id
        if sealed and isinstance(release_id, str) and release_id
        else "TO_BE_RESOLVED"
    )
    leaf_by_name = {
        "risk-policy": r"configs\dumbmoney\risk-policy.v1.json",
        "role-public-key-bundle": (r"configs\dumbmoney\role-public-keys.v1.json"),
        "dopey-codex-executable": DOPEY_CODEX_EXECUTABLE_RELATIVE_PATH,
        "dopey-ollama-executable": DOPEY_OLLAMA_EXECUTABLE_RELATIVE_PATH,
        "desktop-executable": DESKTOP_EXECUTABLE_RELATIVE_PATH,
    }
    leaf = leaf_by_name[name]
    return rf"C:\Program Files\DumbMoney\releases\{release_directory}\{leaf}"


def _versioned_release_file_path(
    relative: Any,
    *,
    release_id: Any,
    sealed: bool,
) -> str | None:
    try:
        safe = safe_relative_path(relative)
    except (TypeError, ValueError):
        return None
    release_directory = (
        release_id
        if sealed
        and isinstance(release_id, str)
        and release_id
        and not looks_unresolved(release_id)
        else "TO_BE_RESOLVED"
    )
    tail = str(PureWindowsPath(*safe.parts))
    return rf"C:\Program Files\DumbMoney\releases\{release_directory}\{tail}"


def _validate_runner_config_relations(
    report: ValidationReport,
    *,
    config: dict[str, Any],
    relations: dict[str, Any],
    artifact_name: str,
    sealed: bool,
) -> None:
    for field, expected in relations.items():
        observed = config.get(field)
        if observed == expected:
            continue
        method = report.error if sealed else report.partial
        method(
            "RELEASE_RUNNER_CONFIG_RELATION_MISMATCH",
            (
                f"{artifact_name} field {field} must equal its independently "
                "pinned release value"
            ),
            f"installed_artifacts[{artifact_name}].{field}",
        )


def _validate_runner_config_resolution(
    report: ValidationReport,
    *,
    config: dict[str, Any],
    artifact_name: str,
    sealed: bool,
) -> None:
    def visit(value: Any, path: str) -> None:
        if isinstance(value, str):
            if looks_unresolved(value):
                method = report.error if sealed else report.partial
                method(
                    "RELEASE_RUNNER_CONFIG_VALUE_UNRESOLVED",
                    f"{artifact_name} contains an unresolved value",
                    f"installed_artifacts[{artifact_name}].{path}",
                )
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")
            return
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, f"{path}.{key}" if path else str(key))

    visit(config, "")


def _decode_public_key_base64url(value: Any) -> bytes | None:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is None:
        return None
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error):
        return None
    if len(decoded) != 32:
        return None
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    return decoded if canonical == value else None


def _role_key_maps(
    report: ValidationReport,
    role_bundle: dict[str, Any] | None,
    *,
    sealed: bool,
) -> tuple[str | None, dict[str, dict[str, str]]]:
    if not isinstance(role_bundle, dict):
        return None, {}
    raw_roles = role_bundle.get("role_public_keys_base64url")
    if not isinstance(raw_roles, dict):
        method = report.error if sealed else report.partial
        method(
            "RELEASE_ROLE_BUNDLE_INVALID",
            "role public-key bundle must contain its exact role map",
            "installed_artifacts[role-public-key-bundle]",
        )
        return None, {}
    try:
        role_digest = sha256_bytes(canonical_json_bytes(raw_roles))
    except (TypeError, ValueError) as exc:
        report.error(
            "RELEASE_ROLE_BUNDLE_NOT_CANONICAL",
            str(exc),
            "installed_artifacts[role-public-key-bundle]",
        )
        return None, {}
    maps: dict[str, dict[str, str]] = {}
    for role, values in raw_roles.items():
        if not isinstance(role, str) or not isinstance(values, list):
            report.error(
                "RELEASE_ROLE_BUNDLE_INVALID",
                "each role must map to an array of public keys",
                f"installed_artifacts[role-public-key-bundle].{role}",
            )
            continue
        mapped: dict[str, str] = {}
        for value in values:
            decoded = _decode_public_key_base64url(value)
            if decoded is None:
                report.error(
                    "RELEASE_ROLE_PUBLIC_KEY_INVALID",
                    "role public keys must be canonical raw Ed25519 base64url",
                    f"installed_artifacts[role-public-key-bundle].{role}",
                )
                continue
            key_id = sha256_bytes(decoded)
            mapped[key_id] = value
        maps[role] = mapped
    return role_digest, maps


def _installed_public_key_map(
    report: ValidationReport,
    *,
    release_root: Path,
    record: dict[str, Any] | None,
    artifact_name: str,
    sealed: bool,
) -> dict[str, str] | None:
    if record is None or not record.get("source_path"):
        return None
    path = resolve_beneath(release_root, record["source_path"])
    if not path.is_file():
        return None
    try:
        encoded = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        report.error(
            "RELEASE_PUBLIC_KEY_UNREADABLE",
            f"{type(exc).__name__}: {exc}",
            str(path),
        )
        return None
    decoded = _decode_public_key_base64url(encoded)
    if decoded is None:
        method = report.error if sealed else report.partial
        method(
            "RELEASE_PUBLIC_KEY_INVALID",
            f"{artifact_name} must contain one canonical Ed25519 public key",
            str(path),
        )
        return None
    return {sha256_bytes(decoded): encoded}


def _validate_installed_artifact(
    report: ValidationReport,
    item: Any,
    *,
    release_root: Path,
    release_state: Any,
    release_id: Any,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        report.error(
            "RELEASE_INSTALLED_ARTIFACT_INVALID",
            "installed artifact entries must be objects",
            "installed_artifacts",
        )
        return None
    _reject_extra_fields(
        report,
        item,
        {
            "name",
            "state",
            "source_path",
            "install_path",
            "sha256",
            "bytes",
            "component",
        },
        code="RELEASE_INSTALLED_ARTIFACT_FIELDS_FORBIDDEN",
        path="installed_artifacts",
    )
    name = item.get("name")
    prefix = f"installed_artifacts[{name or '?'}]"
    if name not in REQUIRED_INSTALLED_ARTIFACTS:
        report.error(
            "RELEASE_INSTALLED_ARTIFACT_NAME_INVALID",
            f"installed artifact name must be one of {sorted(REQUIRED_INSTALLED_ARTIFACTS)}",
            f"{prefix}.name",
        )
        return None

    state = item.get("state")
    if state not in {"TEMPLATE", "SEALED"}:
        report.error(
            "RELEASE_INSTALLED_ARTIFACT_STATE_INVALID",
            "installed artifact state must be TEMPLATE or SEALED",
            f"{prefix}.state",
        )
    elif state != release_state:
        report.error(
            "RELEASE_INSTALLED_ARTIFACT_STATE_MISMATCH",
            "installed artifact state must equal release_state",
            f"{prefix}.state",
        )
    sealed = release_state == "SEALED"

    component = item.get("component")
    expected_component = EXPECTED_INSTALLED_ARTIFACT_COMPONENTS[name]
    if component != expected_component:
        report.error(
            "RELEASE_INSTALLED_ARTIFACT_COMPONENT_INVALID",
            f"{name} must be supplied by component {expected_component}",
            f"{prefix}.component",
        )

    install_path = _normalize_absolute_windows_path(item.get("install_path"))
    expected_install_path = _expected_installed_path(
        name,
        release_id=release_id,
        sealed=sealed,
    )
    if install_path is None:
        report.error(
            "RELEASE_INSTALLED_ARTIFACT_INSTALL_PATH_INVALID",
            "install_path must be a traversal-free absolute path on C:",
            f"{prefix}.install_path",
        )
    elif (
        expected_install_path is None
        or install_path.casefold() != expected_install_path.casefold()
    ):
        report.error(
            "RELEASE_INSTALLED_ARTIFACT_INSTALL_PATH_MISMATCH",
            f"{name} install_path must equal {expected_install_path}",
            f"{prefix}.install_path",
        )
    if sealed and looks_unresolved(install_path):
        report.error(
            "RELEASE_INSTALLED_ARTIFACT_INSTALL_PATH_UNRESOLVED",
            "sealed installed artifact paths cannot contain placeholders",
            f"{prefix}.install_path",
        )

    source_path: str | None = None
    source = item.get("source_path")
    expected_digest = item.get("sha256")
    digest_valid = validate_hex_digest(
        report,
        expected_digest,
        field_path=f"{prefix}.sha256",
        unresolved_is_partial=not sealed,
    )
    if looks_unresolved(source):
        method = report.error if sealed else report.partial
        method(
            "RELEASE_INSTALLED_ARTIFACT_SOURCE_UNRESOLVED",
            f"{name} source_path must resolve before sealing",
            f"{prefix}.source_path",
        )
    else:
        try:
            source_path = safe_relative_path(source).as_posix()
            source_file = resolve_beneath(release_root, source_path)
        except (TypeError, ValueError) as exc:
            report.error(
                "RELEASE_INSTALLED_ARTIFACT_SOURCE_PATH_INVALID",
                str(exc),
                f"{prefix}.source_path",
            )
        else:
            if not source_file.is_file():
                method = report.error if sealed else report.partial
                method(
                    "RELEASE_INSTALLED_ARTIFACT_SOURCE_MISSING",
                    "installed artifact source is missing",
                    str(source_file),
                )
            else:
                if digest_valid and sha256_file(source_file) != expected_digest:
                    report.error(
                        "RELEASE_INSTALLED_ARTIFACT_HASH_MISMATCH",
                        "installed artifact source differs from its digest",
                        str(source_file),
                    )
                expected_bytes = item.get("bytes")
                if (
                    isinstance(expected_bytes, int)
                    and not isinstance(expected_bytes, bool)
                    and expected_bytes > 0
                    and source_file.stat().st_size != expected_bytes
                ):
                    report.error(
                        "RELEASE_INSTALLED_ARTIFACT_SIZE_MISMATCH",
                        "installed artifact source differs from its byte size",
                        str(source_file),
                    )
    size = item.get("bytes")
    if size == 0 and not sealed:
        report.partial(
            "RELEASE_INSTALLED_ARTIFACT_SIZE_UNRESOLVED",
            f"{name} byte size must resolve before sealing",
            f"{prefix}.bytes",
        )
    elif not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        report.error(
            "RELEASE_INSTALLED_ARTIFACT_SIZE_INVALID",
            "installed artifact bytes must be a positive integer",
            f"{prefix}.bytes",
        )

    return {
        **item,
        "source_path": source_path,
        "install_path": install_path,
    }


def _validate_command_binding(
    report: ValidationReport,
    name: str,
    binding: Any,
    *,
    expected_arguments: tuple[str, ...] | None,
) -> tuple[str, str, int, str] | None:
    prefix = f"command_bindings.{name}"
    if not isinstance(binding, dict):
        report.error(
            "RELEASE_COMMAND_BINDING_INVALID",
            "command bindings must be hash-pinned artifact objects",
            prefix,
        )
        return None
    _reject_extra_fields(
        report,
        binding,
        {"executable", "sha256", "bytes", "component", "arguments"},
        code="RELEASE_COMMAND_BINDING_FIELDS_FORBIDDEN",
        path=prefix,
    )
    raw_executable = binding.get("executable")
    if looks_unresolved(raw_executable):
        report.partial(
            "RELEASE_COMMAND_BINDING_UNRESOLVED",
            f"{name} executable must resolve to a release artifact",
            f"{prefix}.executable",
        )
        executable = None
    else:
        try:
            executable = safe_relative_path(raw_executable).as_posix()
        except ValueError as exc:
            report.error(
                "RELEASE_COMMAND_BINDING_PATH_INVALID",
                str(exc),
                f"{prefix}.executable",
            )
            executable = None
        else:
            expected_name = EXPECTED_COMMAND_EXECUTABLES.get(name)
            if (
                expected_name is not None
                and safe_relative_path(executable).name.casefold()
                != expected_name.casefold()
            ):
                report.error(
                    "RELEASE_COMMAND_EXECUTABLE_NAME_INVALID",
                    f"{name} must bind {expected_name}, not a generic or shell runner",
                    f"{prefix}.executable",
                )

    digest = binding.get("sha256")
    digest_valid = validate_hex_digest(
        report,
        digest,
        field_path=f"{prefix}.sha256",
    )
    size = binding.get("bytes")
    size_valid = isinstance(size, int) and not isinstance(size, bool) and size > 0
    if size == 0:
        report.partial(
            "RELEASE_COMMAND_SIZE_UNRESOLVED",
            f"{name} executable size must be resolved before sealing",
            f"{prefix}.bytes",
        )
    elif not size_valid:
        report.error(
            "RELEASE_COMMAND_SIZE_INVALID",
            "command executable bytes must be a positive integer",
            f"{prefix}.bytes",
        )
    component = binding.get("component")
    expected_component = EXPECTED_COMMAND_COMPONENTS.get(name)
    component_valid = component == expected_component
    if not component_valid:
        report.error(
            "RELEASE_COMMAND_COMPONENT_INVALID",
            f"{name} must be supplied by component {expected_component}",
            f"{prefix}.component",
        )

    arguments = binding.get("arguments")
    arguments_valid = isinstance(arguments, list) and not any(
        not isinstance(argument, str)
        or "\x00" in argument
        or "\r" in argument
        or "\n" in argument
        for argument in arguments
    )
    if not arguments_valid:
        report.error(
            "RELEASE_COMMAND_ARGUMENTS_INVALID",
            "arguments must be an array of single-line strings without NUL bytes",
            f"{prefix}.arguments",
        )
    else:
        if any(looks_unresolved(argument) or "%" in argument for argument in arguments):
            report.partial(
                "RELEASE_COMMAND_ARGUMENT_UNRESOLVED",
                "command arguments may not contain placeholders or environment expansion",
                f"{prefix}.arguments",
            )
        if _arguments_contain_inline_secret(arguments):
            report.error(
                "RELEASE_COMMAND_ARGUMENT_SECRET_FORBIDDEN",
                "credentials and credential flags must not appear in command arguments",
                f"{prefix}.arguments",
            )
        approved_argument_values = set(expected_arguments or ())
        if any(
            (
                ABSOLUTE_ARGUMENT_PATH_RE.search(argument)
                and argument not in approved_argument_values
            )
            or argument.rsplit("=", 1)[-1].strip() in {".", "..", "~", "/", "\\"}
            for argument in arguments
        ):
            report.error(
                "RELEASE_COMMAND_ARGUMENT_BROAD_PATH_FORBIDDEN",
                "command arguments may not contain absolute, home, or broad root paths",
                f"{prefix}.arguments",
            )
        if any(
            any(marker in argument for marker in ("&", "|", ">", "<", "`", "$(", ";"))
            for argument in arguments
        ):
            report.error(
                "RELEASE_COMMAND_ARGUMENT_SHELL_SYNTAX_FORBIDDEN",
                "command arguments must not contain shell composition syntax",
                f"{prefix}.arguments",
            )
        if expected_arguments is None:
            report.error(
                "RELEASE_COMMAND_EXPECTATION_UNAVAILABLE",
                f"{name} cannot be checked without its pinned installed artifacts",
                f"{prefix}.arguments",
            )
        elif tuple(arguments) != expected_arguments:
            report.error(
                "RELEASE_COMMAND_ARGUMENT_SET_INVALID",
                (
                    f"{name} arguments must exactly equal the release contract; "
                    "additional, reordered, or omitted flags are forbidden"
                ),
                f"{prefix}.arguments",
            )
            missing_pairs: list[tuple[str, str]] = []
            for flag, value in zip(
                expected_arguments[::2],
                expected_arguments[1::2],
                strict=True,
            ):
                matching_indexes = [
                    index
                    for index, argument in enumerate(arguments)
                    if argument == flag
                ]
                if (
                    len(matching_indexes) != 1
                    or matching_indexes[0] + 1 >= len(arguments)
                    or arguments[matching_indexes[0] + 1] != value
                ):
                    missing_pairs.append((flag, value))
            if missing_pairs:
                report.error(
                    "RELEASE_COMMAND_REQUIRED_ARGUMENTS_MISSING",
                    (
                        f"{name} lacks exact required target/config arguments: "
                        f"{missing_pairs}"
                    ),
                    f"{prefix}.arguments",
                )
        if name in {
            "DUMBMONEY_DUMMY_KALSHI_COMMAND",
            "DUMBMONEY_DOPEY_ROBINHOOD_COMMAND",
        } and any(
            argument.casefold()
            in {"--execute", "--live", "--enable-live", "live", "aggressive_bounded"}
            for argument in arguments
        ):
            report.error(
                "RELEASE_COMMAND_STATIC_LIVE_MODE_FORBIDDEN",
                "venue launch arguments cannot grant live execution authority",
                f"{prefix}.arguments",
            )

    if name == "DUMBMONEY_DOPEY_ROBINHOOD_COMMAND":
        normalized_path = (
            raw_executable.replace("\\", "/").lower()
            if isinstance(raw_executable, str)
            else ""
        )
        normalized_arguments = (
            [
                argument.replace("\\", "/").lower()
                for argument in arguments
                if isinstance(argument, str)
            ]
            if isinstance(arguments, list)
            else []
        )
        legacy_launch_detected = (
            "c:/users/ch/dopey" in normalized_path
            or any("c:/users/ch/dopey" in item for item in normalized_arguments)
            or any("operator_live_branch" in item for item in normalized_arguments)
            or any(item.strip() == "--execute" for item in normalized_arguments)
        )
        if legacy_launch_detected:
            report.error(
                "RELEASE_LEGACY_DOPEY_LAUNCH_FORBIDDEN",
                (
                    "Dopey must use the sealed DumbMoney venue-cell launcher; "
                    "the legacy laptop schedule and operator_live_branch --execute "
                    "path have no DumbMoney authority"
                ),
                prefix,
            )
    if (
        executable is None
        or not digest_valid
        or not size_valid
        or not component_valid
        or not arguments_valid
    ):
        return None
    return executable, str(digest), size, str(component)


def validate_release_manifest(
    manifest: dict[str, Any],
    *,
    release_root: Path,
) -> ValidationReport:
    report = ValidationReport("release-manifest")
    _reject_extra_fields(
        report,
        manifest,
        {
            "schema_version",
            "release_id",
            "created_at",
            "release_state",
            "immutable",
            "broker_actions_authorized",
            "distribution",
            "hosted_control_plane",
            "remote_updates_authorized",
            "cloud_secret_storage_authorized",
            "telemetry_publication",
            "fund_lock",
            "services_manifest",
            "installed_artifacts",
            "command_bindings",
            "files",
        },
        code="RELEASE_MANIFEST_FIELDS_FORBIDDEN",
        path="release_manifest",
    )
    if manifest.get("schema_version") != "dumbmoney.release-manifest.v1":
        report.error(
            "RELEASE_SCHEMA_UNSUPPORTED",
            "schema_version must be dumbmoney.release-manifest.v1",
            "schema_version",
        )
    if (
        not isinstance(manifest.get("release_id"), str)
        or not manifest["release_id"].strip()
    ):
        report.error(
            "RELEASE_ID_INVALID",
            "release_id must be a non-empty string",
            "release_id",
        )
    state = manifest.get("release_state")
    if state not in {"TEMPLATE", "SEALED"}:
        report.error(
            "RELEASE_STATE_INVALID",
            "release_state must be TEMPLATE or SEALED",
            "release_state",
        )
    sealed = state == "SEALED"
    if not sealed:
        report.partial(
            "RELEASE_TEMPLATE_NOT_SEALED",
            "template validation cannot authorize deployment",
            "release_state",
        )
    if manifest.get("immutable") is not True:
        report.error(
            "RELEASE_NOT_IMMUTABLE",
            "release manifest must declare immutable=true",
            "immutable",
        )
    if manifest.get("broker_actions_authorized") is not False:
        report.error(
            "RELEASE_MANIFEST_BROKER_AUTHORITY_FORBIDDEN",
            "release manifests cannot authorize broker actions",
            "broker_actions_authorized",
        )
    if manifest.get("distribution") != "PRIVATE_LOCAL_ONLY":
        report.error(
            "RELEASE_DISTRIBUTION_INVALID",
            "release distribution must remain PRIVATE_LOCAL_ONLY",
            "distribution",
        )
    if manifest.get("hosted_control_plane") is not False:
        report.error(
            "RELEASE_HOSTED_CONTROL_PLANE_FORBIDDEN",
            "the v1 control plane must remain local",
            "hosted_control_plane",
        )
    if manifest.get("remote_updates_authorized") is not False:
        report.error(
            "RELEASE_REMOTE_UPDATES_FORBIDDEN",
            "release manifests may not authorize remote updates or an updater",
            "remote_updates_authorized",
        )
    if manifest.get("cloud_secret_storage_authorized") is not False:
        report.error(
            "RELEASE_CLOUD_SECRET_STORAGE_FORBIDDEN",
            "release secrets must remain in local OS-protected credential storage",
            "cloud_secret_storage_authorized",
        )
    if manifest.get("telemetry_publication") is not False:
        report.error(
            "RELEASE_TELEMETRY_PUBLICATION_FORBIDDEN",
            "release telemetry publication must remain disabled",
            "telemetry_publication",
        )

    installed_artifacts = manifest.get("installed_artifacts")
    installed_records: dict[str, dict[str, Any]] = {}
    if not isinstance(installed_artifacts, list):
        report.error(
            "RELEASE_INSTALLED_ARTIFACTS_MISSING",
            "installed_artifacts must be an array",
            "installed_artifacts",
        )
    else:
        names = [
            item.get("name") for item in installed_artifacts if isinstance(item, dict)
        ]
        duplicate_names = sorted({str(name) for name in names if names.count(name) > 1})
        if duplicate_names:
            report.error(
                "RELEASE_INSTALLED_ARTIFACT_DUPLICATED",
                f"installed artifact names must be unique: {duplicate_names}",
                "installed_artifacts",
            )
        actual_names = {name for name in names if isinstance(name, str)}
        if actual_names != REQUIRED_INSTALLED_ARTIFACTS:
            report.error(
                "RELEASE_INSTALLED_ARTIFACT_SET_INVALID",
                (
                    f"missing={sorted(REQUIRED_INSTALLED_ARTIFACTS - actual_names)}; "
                    f"extra={sorted(actual_names - REQUIRED_INSTALLED_ARTIFACTS)}"
                ),
                "installed_artifacts",
            )
        for item in installed_artifacts:
            record = _validate_installed_artifact(
                report,
                item,
                release_root=release_root,
                release_state=state,
                release_id=manifest.get("release_id"),
            )
            if record is not None:
                installed_records[str(record["name"])] = record

    command_bindings = manifest.get("command_bindings")
    resolved_command_bindings: dict[str, tuple[str, str, int, str]] = {}
    if not isinstance(command_bindings, dict):
        report.error(
            "RELEASE_COMMAND_BINDINGS_MISSING",
            "command_bindings must be an object",
            "command_bindings",
        )
    else:
        expected_bindings = {
            "DUMBMONEY_CORE_COMMAND",
            "DUMBMONEY_RESEARCH_MESH_COMMAND",
            "DUMBMONEY_MODEL_GATEWAY_COMMAND",
            "DUMBMONEY_DUMMY_KALSHI_COMMAND",
            "DUMBMONEY_DOPEY_ROBINHOOD_COMMAND",
        }
        actual_bindings = set(command_bindings)
        if actual_bindings != expected_bindings:
            report.error(
                "RELEASE_COMMAND_BINDING_SET_INVALID",
                (
                    f"missing={sorted(expected_bindings - actual_bindings)}; "
                    f"extra={sorted(actual_bindings - expected_bindings)}"
                ),
                "command_bindings",
            )
        for name, binding in command_bindings.items():
            expected_arguments = EXPECTED_COMMAND_ARGUMENTS.get(name)
            if name == "DUMBMONEY_CORE_COMMAND":
                core_config_record = installed_records.get("core-runner-config")
                core_config_digest = (
                    core_config_record.get("sha256")
                    if core_config_record is not None
                    else None
                )
                expected_arguments = (
                    (
                        "--config",
                        CORE_RUNNER_CONFIG_INSTALL_PATH,
                        "--config-sha256",
                        str(core_config_digest),
                    )
                    if isinstance(core_config_digest, str)
                    else None
                )
            elif name == "DUMBMONEY_RESEARCH_MESH_COMMAND":
                research_config_record = installed_records.get(
                    "research-mesh-runner-config"
                )
                research_config_digest = (
                    research_config_record.get("sha256")
                    if research_config_record is not None
                    else None
                )
                expected_arguments = (
                    (
                        "--config",
                        RESEARCH_MESH_CONFIG_INSTALL_PATH,
                        "--config-sha256",
                        str(research_config_digest),
                    )
                    if isinstance(research_config_digest, str)
                    else None
                )
            elif name == "DUMBMONEY_MODEL_GATEWAY_COMMAND":
                gateway_config_record = installed_records.get(
                    "model-gateway-runner-config"
                )
                gateway_config_digest = (
                    gateway_config_record.get("sha256")
                    if gateway_config_record is not None
                    else None
                )
                expected_arguments = (
                    (
                        "--config",
                        MODEL_GATEWAY_CONFIG_INSTALL_PATH,
                        "--config-sha256",
                        str(gateway_config_digest),
                    )
                    if isinstance(gateway_config_digest, str)
                    else None
                )
            elif name == "DUMBMONEY_DUMMY_KALSHI_COMMAND":
                dummy_config_record = installed_records.get(
                    "dummy-kalshi-runner-config"
                )
                dummy_config_digest = (
                    dummy_config_record.get("sha256")
                    if dummy_config_record is not None
                    else None
                )
                expected_arguments = (
                    (
                        "--core-endpoint-ref",
                        "endpoint-ref:DumbMoneyCore",
                        "--core-cell-token-target",
                        "credential-target:DumbMoney/DummyCellToken",
                        "--kalshi-key-id-target",
                        "credential-target:DumbMoney/KalshiApiKeyId",
                        "--kalshi-private-key-target",
                        "credential-target:DumbMoney/KalshiPrivateKeyPem",
                        "--readiness-signing-key-target",
                        "credential-target:DumbMoney/DummyReadinessEd25519",
                        "--start-mode",
                        "RECONCILIATION_ONLY",
                        "--readiness-ref",
                        "readiness-ref:DumbMoneyDummyKalshi",
                        "--config-sha256",
                        str(dummy_config_digest),
                    )
                    if isinstance(dummy_config_digest, str)
                    else None
                )
            elif name == "DUMBMONEY_DOPEY_ROBINHOOD_COMMAND":
                dopey_config_record = installed_records.get(
                    "dopey-robinhood-runner-config"
                )
                dopey_config_digest = (
                    dopey_config_record.get("sha256")
                    if dopey_config_record is not None
                    else None
                )
                expected_arguments = (
                    (
                        "--core-endpoint-ref",
                        "endpoint-ref:DumbMoneyCore",
                        "--core-cell-token-target",
                        "credential-target:DumbMoney/DopeyCellToken",
                        "--robinhood-profile-target",
                        "credential-target:DumbMoney/RobinhoodProfile",
                        "--start-mode",
                        "RECONCILIATION_ONLY",
                        "--readiness-ref",
                        "readiness-ref:DumbMoneyDopeyRobinhood",
                        "--config-sha256",
                        str(dopey_config_digest),
                    )
                    if isinstance(dopey_config_digest, str)
                    else None
                )
            resolved = _validate_command_binding(
                report,
                name,
                binding,
                expected_arguments=expected_arguments,
            )
            if resolved is not None:
                resolved_command_bindings[name] = resolved
    try:
        parse_utc(manifest.get("created_at"))
    except (TypeError, ValueError) as exc:
        report.error("RELEASE_TIME_INVALID", str(exc), "created_at")

    files = manifest.get("files")
    if not isinstance(files, list):
        report.error(
            "RELEASE_FILES_MISSING",
            "files must be an array",
            "files",
        )
        files = []
    seen: set[str] = set()
    file_records: dict[str, dict[str, Any]] = {}
    for item in files:
        if isinstance(item, dict):
            try:
                relative = safe_relative_path(item.get("path")).as_posix()
            except ValueError:
                relative = str(item.get("path", ""))
            if relative in seen:
                report.error(
                    "RELEASE_FILE_DUPLICATED",
                    "release file paths must be unique",
                    relative,
                )
            seen.add(relative)
            file_records[relative] = item
        _validate_manifest_file(
            report,
            item,
            release_root=release_root,
            sealed=sealed,
        )
    missing_required_files = sorted(REQUIRED_RELEASE_FILES - seen)
    if missing_required_files:
        report.error(
            "RELEASE_REQUIRED_FILES_MISSING",
            f"release manifest omits required files: {missing_required_files}",
            "files",
        )
    desktop_record = installed_records.get("desktop-executable")
    packaged_executable_present = (
        any(Path(path).suffix.casefold() == ".exe" for path in seen)
        or bool(resolved_command_bindings)
        or (
            desktop_record is not None and desktop_record.get("source_path") is not None
        )
    )
    if packaged_executable_present:
        missing_provenance = sorted(PACKAGED_EXECUTABLE_PROVENANCE_FILES - seen)
        if missing_provenance:
            report.error(
                "RELEASE_EXECUTABLE_PROVENANCE_MISSING",
                (
                    "a release containing packaged executable bytes must pin "
                    f"its build provenance: {missing_provenance}"
                ),
                "files",
            )
    for (
        name,
        (command_path, command_digest, command_size, command_component),
    ) in resolved_command_bindings.items():
        if command_path not in seen:
            report.error(
                "RELEASE_COMMAND_ARTIFACT_NOT_PINNED",
                f"{name} path must also appear in release files",
                f"command_bindings.{name}.executable",
            )
        else:
            file_record = file_records[command_path]
            if file_record.get("sha256") != command_digest:
                report.error(
                    "RELEASE_COMMAND_ARTIFACT_DIGEST_MISMATCH",
                    f"{name} digest must equal its release file digest",
                    f"command_bindings.{name}.sha256",
                )
            if file_record.get("bytes") != command_size:
                report.error(
                    "RELEASE_COMMAND_ARTIFACT_SIZE_MISMATCH",
                    f"{name} size must equal its release file size",
                    f"command_bindings.{name}.bytes",
                )
            if file_record.get("component") != command_component:
                report.error(
                    "RELEASE_COMMAND_ARTIFACT_COMPONENT_MISMATCH",
                    f"{name} component must equal its release file component",
                    f"command_bindings.{name}.component",
                )

    for name, record in installed_records.items():
        source_path = record.get("source_path")
        if source_path is None:
            continue
        if source_path not in seen:
            report.error(
                "RELEASE_INSTALLED_ARTIFACT_NOT_PINNED",
                f"{name} source_path must also appear in release files",
                f"installed_artifacts[{name}].source_path",
            )
            continue
        file_record = file_records[source_path]
        for field, code in (
            ("sha256", "RELEASE_INSTALLED_ARTIFACT_FILE_DIGEST_MISMATCH"),
            ("bytes", "RELEASE_INSTALLED_ARTIFACT_FILE_SIZE_MISMATCH"),
            ("component", "RELEASE_INSTALLED_ARTIFACT_FILE_COMPONENT_MISMATCH"),
        ):
            if record.get(field) != file_record.get(field):
                report.error(
                    code,
                    (f"{name} {field} must equal its release file {field}"),
                    f"installed_artifacts[{name}].{field}",
                )

    core_config_record = installed_records.get("core-runner-config")
    role_bundle_record = installed_records.get("role-public-key-bundle")
    research_config_record = installed_records.get("research-mesh-runner-config")
    gateway_config_record = installed_records.get("model-gateway-runner-config")
    dummy_config_record = installed_records.get("dummy-kalshi-runner-config")
    dopey_config_record = installed_records.get("dopey-robinhood-runner-config")
    ollama_executable_record = installed_records.get("dopey-ollama-executable")
    ollama_evidence_record = installed_records.get("dopey-ollama-runtime-evidence")
    core_config: dict[str, Any] | None = None
    role_bundle: dict[str, Any] | None = None
    research_config: dict[str, Any] | None = None
    gateway_config: dict[str, Any] | None = None
    dummy_config: dict[str, Any] | None = None
    dopey_config: dict[str, Any] | None = None
    ollama_evidence: dict[str, Any] | None = None
    if core_config_record is not None and core_config_record.get("source_path"):
        core_config_path = resolve_beneath(
            release_root,
            core_config_record["source_path"],
        )
        if core_config_path.is_file():
            try:
                core_config = load_json(core_config_path)
            except (OSError, ValueError) as exc:
                report.error(
                    "RELEASE_CORE_RUNNER_CONFIG_UNREADABLE",
                    f"{type(exc).__name__}: {exc}",
                    str(core_config_path),
                )
    if role_bundle_record is not None and role_bundle_record.get("source_path"):
        role_bundle_path = resolve_beneath(
            release_root,
            role_bundle_record["source_path"],
        )
        if role_bundle_path.is_file():
            try:
                role_bundle = load_json(role_bundle_path)
            except (OSError, ValueError) as exc:
                report.error(
                    "RELEASE_ROLE_PUBLIC_KEY_BUNDLE_UNREADABLE",
                    f"{type(exc).__name__}: {exc}",
                    str(role_bundle_path),
                )
    for artifact_name, record, error_code in (
        (
            "research-mesh-runner-config",
            research_config_record,
            "RELEASE_RESEARCH_MESH_RUNNER_CONFIG_UNREADABLE",
        ),
        (
            "model-gateway-runner-config",
            gateway_config_record,
            "RELEASE_MODEL_GATEWAY_RUNNER_CONFIG_UNREADABLE",
        ),
        (
            "dummy-kalshi-runner-config",
            dummy_config_record,
            "RELEASE_DUMMY_RUNNER_CONFIG_UNREADABLE",
        ),
    ):
        if record is None or not record.get("source_path"):
            continue
        config_path = resolve_beneath(release_root, record["source_path"])
        if not config_path.is_file():
            continue
        try:
            loaded_config = load_json(config_path)
        except (OSError, ValueError) as exc:
            report.error(
                error_code,
                f"{type(exc).__name__}: {exc}",
                str(config_path),
            )
            continue
        if artifact_name == "research-mesh-runner-config":
            research_config = loaded_config
        elif artifact_name == "model-gateway-runner-config":
            gateway_config = loaded_config
        else:
            dummy_config = loaded_config
    if dopey_config_record is not None and dopey_config_record.get("source_path"):
        dopey_config_path = resolve_beneath(
            release_root,
            dopey_config_record["source_path"],
        )
        if dopey_config_path.is_file():
            try:
                dopey_config = load_json(dopey_config_path)
            except (OSError, ValueError) as exc:
                report.error(
                    "RELEASE_DOPEY_RUNNER_CONFIG_UNREADABLE",
                    f"{type(exc).__name__}: {exc}",
                    str(dopey_config_path),
                )
    if ollama_evidence_record is not None and ollama_evidence_record.get("source_path"):
        ollama_evidence_path = resolve_beneath(
            release_root,
            ollama_evidence_record["source_path"],
        )
        if ollama_evidence_path.is_file():
            try:
                ollama_evidence = load_json(ollama_evidence_path)
            except (OSError, ValueError) as exc:
                report.error(
                    "RELEASE_OLLAMA_RUNTIME_EVIDENCE_UNREADABLE",
                    f"{type(exc).__name__}: {exc}",
                    str(ollama_evidence_path),
                )
    if ollama_evidence is not None:
        ollama_report = validate_ollama_runtime_evidence(
            ollama_evidence,
            release_state=str(state),
            release_id=manifest.get("release_id"),
            executable_record=ollama_executable_record,
            evidence_record=ollama_evidence_record,
            dopey_config=dopey_config,
        )
        _extend_with_prefix(
            report,
            ollama_report,
            "installed_artifacts[dopey-ollama-runtime-evidence]",
        )

    fund_reference = manifest.get("fund_lock")
    service_reference = manifest.get("services_manifest")
    release_id = manifest.get("release_id")
    fund_lock_digest = (
        fund_reference.get("sha256") if isinstance(fund_reference, dict) else None
    )
    service_manifest_digest = (
        service_reference.get("sha256") if isinstance(service_reference, dict) else None
    )
    fund_lock_install_path = _versioned_release_file_path(
        fund_reference.get("path") if isinstance(fund_reference, dict) else None,
        release_id=release_id,
        sealed=sealed,
    )
    service_manifest_install_path = _versioned_release_file_path(
        (
            service_reference.get("path")
            if isinstance(service_reference, dict)
            else None
        ),
        release_id=release_id,
        sealed=sealed,
    )
    release_manifest_install_path = _versioned_release_file_path(
        "release-manifest.json",
        release_id=release_id,
        sealed=sealed,
    )
    role_public_keys_digest, role_key_maps = _role_key_maps(
        report,
        role_bundle,
        sealed=sealed,
    )
    core_public_key_map = _installed_public_key_map(
        report,
        release_root=release_root,
        record=installed_records.get("core-public-key"),
        artifact_name="core-public-key",
        sealed=sealed,
    )

    def sole_role_key_id(role: str) -> str | None:
        keys = role_key_maps.get(role)
        if keys is None:
            return None
        if len(keys) != 1:
            method = report.error if sealed else report.partial
            method(
                "RELEASE_VENUE_READINESS_KEY_SET_INVALID",
                f"role {role} must contain exactly one readiness public key",
                f"installed_artifacts[role-public-key-bundle].{role}",
            )
            return None
        return next(iter(keys))

    if core_config is not None:
        core_report = validate_core_runner_config(
            core_config,
            state=str(state),
            role_bundle=role_bundle,
        )
        _extend_with_prefix(
            report,
            core_report,
            "installed_artifacts[core-runner-config]",
        )

        relation_checks = {
            "fund_lock_sha256": fund_lock_digest,
            "service_manifest_sha256": service_manifest_digest,
            "risk_policy_sha256": (
                installed_records.get("risk-policy", {}).get("sha256")
            ),
            "core_public_key_sha256": (
                installed_records.get("core-public-key", {}).get("sha256")
            ),
            "policy_path": (
                installed_records.get("risk-policy", {}).get("install_path")
            ),
            "core_public_key_path": (
                installed_records.get("core-public-key", {}).get("install_path")
            ),
            "fund_lock_path": fund_lock_install_path,
            "service_manifest_path": service_manifest_install_path,
            "release_manifest_path": release_manifest_install_path,
            "release_id": release_id,
        }
        for field, expected in relation_checks.items():
            observed = core_config.get(field)
            if observed == expected:
                continue
            method = (
                report.partial
                if not sealed
                and (looks_unresolved(observed) or looks_unresolved(expected))
                else report.error
            )
            method(
                "RELEASE_CORE_RUNNER_RELATION_MISMATCH",
                (
                    f"Core config {field} must equal its independently pinned "
                    "release value"
                ),
                f"installed_artifacts[core-runner-config].{field}",
            )

    risk_policy_record = installed_records.get("risk-policy", {})
    core_runner_record = installed_records.get("core-runner-config", {})
    role_bundle_record = installed_records.get("role-public-key-bundle", {})
    gateway_public_key_record = installed_records.get("model-gateway-public-key", {})
    research_public_key_record = installed_records.get("research-mesh-public-key", {})
    allocator_public_key_record = installed_records.get(
        "research-mesh-allocator-public-key",
        {},
    )

    if gateway_config is not None:
        _validate_runner_config_resolution(
            report,
            config=gateway_config,
            artifact_name="model-gateway-runner-config",
            sealed=sealed,
        )
        _validate_runner_config_relations(
            report,
            config=gateway_config,
            relations={
                "release_id": release_id,
                "fund_lock_path": fund_lock_install_path,
                "fund_lock_sha256": fund_lock_digest,
                "service_manifest_path": service_manifest_install_path,
                "service_manifest_sha256": service_manifest_digest,
                "bind_port": 8788,
                "gateway_public_key_path": gateway_public_key_record.get(
                    "install_path"
                ),
                "gateway_public_key_sha256": gateway_public_key_record.get("sha256"),
                "credential_targets": {
                    "openrouter_api_key": (
                        "credential-target:DumbMoney/OpenRouterApiKey"
                    ),
                    "gateway_signing_seed": (
                        "credential-target:DumbMoney/ModelGatewaySigner"
                    ),
                    "client_bearer_token": (
                        "credential-target:DumbMoney/ModelGatewayClientToken"
                    ),
                },
            },
            artifact_name="model-gateway-runner-config",
            sealed=sealed,
        )

    if research_config is not None:
        _validate_runner_config_resolution(
            report,
            config=research_config,
            artifact_name="research-mesh-runner-config",
            sealed=sealed,
        )
        _validate_runner_config_relations(
            report,
            config=research_config,
            relations={
                "release_id": release_id,
                "fund_lock_path": fund_lock_install_path,
                "fund_lock_sha256": fund_lock_digest,
                "service_manifest_path": service_manifest_install_path,
                "service_manifest_sha256": service_manifest_digest,
                "research_mesh_public_key_path": research_public_key_record.get(
                    "install_path"
                ),
                "research_mesh_public_key_sha256": research_public_key_record.get(
                    "sha256"
                ),
                "allocator_public_key_path": allocator_public_key_record.get(
                    "install_path"
                ),
                "allocator_public_key_sha256": allocator_public_key_record.get(
                    "sha256"
                ),
                "core_public_key_path": installed_records.get(
                    "core-public-key", {}
                ).get("install_path"),
                "core_public_key_sha256": installed_records.get(
                    "core-public-key", {}
                ).get("sha256"),
                "model_gateway_public_key_path": gateway_public_key_record.get(
                    "install_path"
                ),
                "model_gateway_public_key_sha256": gateway_public_key_record.get(
                    "sha256"
                ),
                "model_gateway_runner_config_sha256": (
                    gateway_config_record.get("sha256")
                    if gateway_config_record is not None
                    else None
                ),
                "model_request_inbox": (
                    r"C:\ProgramData\DumbMoney\spool\model\requests"
                ),
                "model_response_outbox": (
                    r"C:\ProgramData\DumbMoney\spool\model\responses"
                ),
                "model_outcome_outbox": (
                    r"C:\ProgramData\DumbMoney\spool\model\outcomes"
                ),
                "model_gateway_readiness_path": (
                    r"C:\ProgramData\DumbMoney\readiness"
                    r"\DumbMoneyModelGateway.json"
                ),
                "model_gateway_timeout_seconds": 10,
                "model_request_max_bytes": 1_048_576,
                "model_response_max_bytes": 1_000_000,
                "model_prompt_max_characters": 100_000,
                "model_max_output_tokens": 1_024,
                "model_max_reserve_microusd": 6_000_000,
                "credential_targets": {
                    "research_mesh_signing_seed": (
                        "credential-target:DumbMoney/ResearchMeshSigner"
                    ),
                    "allocator_signing_seed": (
                        "credential-target:DumbMoney/ResearchMeshAllocator"
                    ),
                    "allocator_bearer_token": (
                        "credential-target:DumbMoney/AllocatorToken"
                    ),
                    "model_gateway_client_token": (
                        "credential-target:DumbMoney/ModelGatewayClientToken"
                    ),
                },
            },
            artifact_name="research-mesh-runner-config",
            sealed=sealed,
        )

    common_venue_relations = {
        "release_id": release_id,
        "fund_lock_sha256": fund_lock_digest,
        "service_manifest_sha256": service_manifest_digest,
        "role_public_keys_sha256": role_public_keys_digest,
        "core_runner_config_sha256": core_runner_record.get("sha256"),
        "risk_policy_sha256": risk_policy_record.get("sha256"),
    }
    if dummy_config is not None:
        _validate_runner_config_resolution(
            report,
            config=dummy_config,
            artifact_name="dummy-kalshi-runner-config",
            sealed=sealed,
        )
        _validate_runner_config_relations(
            report,
            config=dummy_config,
            relations={
                **common_venue_relations,
                "core_public_keys_base64url": core_public_key_map,
                "operator_public_keys_base64url": role_key_maps.get("operator"),
                "evaluator_public_keys_base64url": role_key_maps.get("evaluator"),
                "promoter_public_keys_base64url": role_key_maps.get("promoter"),
                "research_public_keys_base64url": role_key_maps.get("research"),
                "readiness_signer_public_key_sha256": sole_role_key_id("dummy_venue"),
            },
            artifact_name="dummy-kalshi-runner-config",
            sealed=sealed,
        )

    if dopey_config is not None:
        _validate_runner_config_resolution(
            report,
            config=dopey_config,
            artifact_name="dopey-robinhood-runner-config",
            sealed=sealed,
        )
        core_public_key = (
            next(iter(core_public_key_map.values()))
            if core_public_key_map is not None and len(core_public_key_map) == 1
            else None
        )
        _validate_runner_config_relations(
            report,
            config=dopey_config,
            relations={
                **common_venue_relations,
                "core_public_key_base64url": core_public_key,
                "operator_public_keys_base64url": role_key_maps.get("operator"),
                "evaluator_public_keys_base64url": role_key_maps.get("evaluator"),
                "promoter_public_keys_base64url": role_key_maps.get("promoter"),
                "research_public_keys_base64url": role_key_maps.get("research"),
                "readiness_signer_public_key_sha256": sole_role_key_id("dopey_venue"),
                "codex_executable_path": installed_records.get(
                    "dopey-codex-executable", {}
                ).get("install_path"),
                "codex_executable_sha256": installed_records.get(
                    "dopey-codex-executable", {}
                ).get("sha256"),
                "codex_local_provider": "ollama",
                "ollama_executable_path": installed_records.get(
                    "dopey-ollama-executable", {}
                ).get("install_path"),
                "ollama_executable_sha256": installed_records.get(
                    "dopey-ollama-executable", {}
                ).get("sha256"),
                "ollama_runtime_evidence_sha256": (
                    ollama_evidence_record.get("sha256")
                    if ollama_evidence_record is not None
                    else None
                ),
                "ollama_process_identity_sid": (
                    ollama_evidence.get("process_identity_sid")
                    if ollama_evidence is not None
                    else None
                ),
            },
            artifact_name="dopey-robinhood-runner-config",
            sealed=sealed,
        )

    core_binding = (
        command_bindings.get("DUMBMONEY_CORE_COMMAND")
        if isinstance(command_bindings, dict)
        else None
    )
    if isinstance(core_binding, dict) and core_config_record is not None:
        expected_core_arguments = [
            "--config",
            core_config_record.get("install_path"),
            "--config-sha256",
            core_config_record.get("sha256"),
        ]
        if core_binding.get("arguments") != expected_core_arguments:
            report.error(
                "RELEASE_CORE_CONFIG_DIGEST_ARGUMENT_MISMATCH",
                (
                    "Core argv must receive the installed config path and the "
                    "exact SHA-256 of those config bytes"
                ),
                "command_bindings.DUMBMONEY_CORE_COMMAND.arguments",
            )

    gateway_binding = (
        command_bindings.get("DUMBMONEY_MODEL_GATEWAY_COMMAND")
        if isinstance(command_bindings, dict)
        else None
    )
    gateway_config_record = installed_records.get("model-gateway-runner-config")
    if isinstance(gateway_binding, dict) and gateway_config_record is not None:
        expected_gateway_arguments = [
            "--config",
            gateway_config_record.get("install_path"),
            "--config-sha256",
            gateway_config_record.get("sha256"),
        ]
        if gateway_binding.get("arguments") != expected_gateway_arguments:
            report.error(
                "RELEASE_MODEL_GATEWAY_CONFIG_DIGEST_ARGUMENT_MISMATCH",
                (
                    "Model Gateway argv must receive the installed config path "
                    "and exact SHA-256 of those config bytes"
                ),
                "command_bindings.DUMBMONEY_MODEL_GATEWAY_COMMAND.arguments",
            )

    research_binding = (
        command_bindings.get("DUMBMONEY_RESEARCH_MESH_COMMAND")
        if isinstance(command_bindings, dict)
        else None
    )
    research_config_record = installed_records.get("research-mesh-runner-config")
    if isinstance(research_binding, dict) and research_config_record is not None:
        expected_research_arguments = [
            "--config",
            research_config_record.get("install_path"),
            "--config-sha256",
            research_config_record.get("sha256"),
        ]
        if research_binding.get("arguments") != expected_research_arguments:
            report.error(
                "RELEASE_RESEARCH_MESH_CONFIG_DIGEST_ARGUMENT_MISMATCH",
                (
                    "Research Mesh argv must receive the installed config path "
                    "and exact SHA-256 of those config bytes"
                ),
                "command_bindings.DUMBMONEY_RESEARCH_MESH_COMMAND.arguments",
            )

    dummy_binding = (
        command_bindings.get("DUMBMONEY_DUMMY_KALSHI_COMMAND")
        if isinstance(command_bindings, dict)
        else None
    )
    dummy_config_record = installed_records.get("dummy-kalshi-runner-config")
    if isinstance(dummy_binding, dict) and dummy_config_record is not None:
        expected_dummy_arguments = [
            "--core-endpoint-ref",
            "endpoint-ref:DumbMoneyCore",
            "--core-cell-token-target",
            "credential-target:DumbMoney/DummyCellToken",
            "--kalshi-key-id-target",
            "credential-target:DumbMoney/KalshiApiKeyId",
            "--kalshi-private-key-target",
            "credential-target:DumbMoney/KalshiPrivateKeyPem",
            "--readiness-signing-key-target",
            "credential-target:DumbMoney/DummyReadinessEd25519",
            "--start-mode",
            "RECONCILIATION_ONLY",
            "--readiness-ref",
            "readiness-ref:DumbMoneyDummyKalshi",
            "--config-sha256",
            dummy_config_record.get("sha256"),
        ]
        if dummy_binding.get("arguments") != expected_dummy_arguments:
            report.error(
                "RELEASE_DUMMY_CONFIG_DIGEST_ARGUMENT_MISMATCH",
                (
                    "Dummy argv must preserve its exact target/mode sequence and "
                    "append the exact SHA-256 of the fixed installed public config"
                ),
                "command_bindings.DUMBMONEY_DUMMY_KALSHI_COMMAND.arguments",
            )

    dopey_binding = (
        command_bindings.get("DUMBMONEY_DOPEY_ROBINHOOD_COMMAND")
        if isinstance(command_bindings, dict)
        else None
    )
    dopey_config_record = installed_records.get("dopey-robinhood-runner-config")
    if isinstance(dopey_binding, dict) and dopey_config_record is not None:
        expected_dopey_arguments = [
            "--core-endpoint-ref",
            "endpoint-ref:DumbMoneyCore",
            "--core-cell-token-target",
            "credential-target:DumbMoney/DopeyCellToken",
            "--robinhood-profile-target",
            "credential-target:DumbMoney/RobinhoodProfile",
            "--start-mode",
            "RECONCILIATION_ONLY",
            "--readiness-ref",
            "readiness-ref:DumbMoneyDopeyRobinhood",
            "--config-sha256",
            dopey_config_record.get("sha256"),
        ]
        if dopey_binding.get("arguments") != expected_dopey_arguments:
            report.error(
                "RELEASE_DOPEY_CONFIG_DIGEST_ARGUMENT_MISMATCH",
                (
                    "Dopey argv must preserve its exact refs and mode and append "
                    "the exact SHA-256 of the fixed installed public config"
                ),
                "command_bindings.DUMBMONEY_DOPEY_ROBINHOOD_COMMAND.arguments",
            )

    report.facts["release_state"] = state
    report.facts["file_count"] = len(files)
    report.facts["installed_artifact_names"] = sorted(installed_records)
    return report


def validate_release_bundle(
    *,
    release_manifest_path: Path,
    fund_lock_path: Path,
    release_root: Path,
    repo_roots: dict[str, Path] | None = None,
    git_executable: Path | None = None,
    git_executable_sha256: str | None = None,
) -> ValidationReport:
    report = ValidationReport("dumbmoney-release-bundle")
    try:
        manifest = load_json(release_manifest_path)
    except (OSError, ValueError) as exc:
        report.error(
            "RELEASE_MANIFEST_UNREADABLE",
            f"{type(exc).__name__}: {exc}",
            str(release_manifest_path),
        )
        return report
    try:
        fund_lock = load_json(fund_lock_path)
    except (OSError, ValueError) as exc:
        report.error(
            "FUND_LOCK_UNREADABLE",
            f"{type(exc).__name__}: {exc}",
            str(fund_lock_path),
        )
        return report

    manifest_report = validate_release_manifest(manifest, release_root=release_root)
    _extend_with_prefix(report, manifest_report, "release_manifest")
    fund_report = validate_fund_lock(
        fund_lock,
        release_root=release_root,
        repo_roots=repo_roots,
        git_executable=git_executable,
        git_executable_sha256=git_executable_sha256,
    )
    _extend_with_prefix(report, fund_report, "fund_lock")

    fund_ref = manifest.get("fund_lock")
    if not isinstance(fund_ref, dict):
        report.error(
            "RELEASE_FUND_LOCK_REFERENCE_MISSING",
            "release manifest must hash-pin its fund lock",
            "fund_lock",
        )
    else:
        _reject_extra_fields(
            report,
            fund_ref,
            {"path", "sha256"},
            code="RELEASE_FUND_LOCK_REFERENCE_FIELDS_FORBIDDEN",
            path="fund_lock",
        )
        expected = fund_ref.get("sha256")
        if validate_hex_digest(
            report,
            expected,
            field_path="fund_lock.sha256",
        ):
            actual = sha256_file(fund_lock_path)
            if actual != expected:
                report.error(
                    "RELEASE_FUND_LOCK_HASH_MISMATCH",
                    "supplied fund lock differs from release manifest pin",
                    str(fund_lock_path),
                )
        try:
            expected_path = safe_relative_path(fund_ref.get("path"))
        except ValueError as exc:
            report.error(
                "RELEASE_FUND_LOCK_PATH_INVALID",
                str(exc),
                "fund_lock.path",
            )
        else:
            declared = release_root.joinpath(*expected_path.parts).resolve(strict=False)
            if declared != fund_lock_path.resolve(strict=False):
                report.error(
                    "RELEASE_FUND_LOCK_PATH_MISMATCH",
                    "supplied fund lock does not match the manifest path",
                    str(fund_lock_path),
                )

    service_ref = manifest.get("services_manifest")
    if not isinstance(service_ref, dict):
        report.error(
            "RELEASE_SERVICES_REFERENCE_MISSING",
            "release manifest must hash-pin services.v1.json",
            "services_manifest",
        )
    else:
        _reject_extra_fields(
            report,
            service_ref,
            {"path", "sha256"},
            code="RELEASE_SERVICES_REFERENCE_FIELDS_FORBIDDEN",
            path="services_manifest",
        )
        try:
            services_path = resolve_beneath(release_root, service_ref.get("path"))
        except (TypeError, ValueError) as exc:
            report.error(
                "RELEASE_SERVICES_PATH_INVALID",
                str(exc),
                "services_manifest.path",
            )
        else:
            if not services_path.is_file():
                report.error(
                    "RELEASE_SERVICES_MISSING",
                    "services manifest is missing",
                    str(services_path),
                )
            else:
                expected = service_ref.get("sha256")
                if validate_hex_digest(
                    report,
                    expected,
                    field_path="services_manifest.sha256",
                ):
                    if sha256_file(services_path) != expected:
                        report.error(
                            "RELEASE_SERVICES_HASH_MISMATCH",
                            "services manifest differs from release pin",
                            str(services_path),
                        )
                try:
                    service_manifest = load_services_manifest(services_path)
                except (OSError, ValueError) as exc:
                    report.error(
                        "RELEASE_SERVICES_UNREADABLE",
                        str(exc),
                        str(services_path),
                    )
                else:
                    service_report = validate_services_manifest(service_manifest)
                    _extend_with_prefix(report, service_report, "services_manifest")

    report.facts.update(
        {
            "release_manifest": str(release_manifest_path.resolve()),
            "fund_lock": str(fund_lock_path.resolve()),
            "release_root": str(release_root.resolve()),
            "repo_roots_checked": sorted((repo_roots or {}).keys()),
        }
    )
    return report
