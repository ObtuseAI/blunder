from __future__ import annotations

import ast
import copy
import importlib.metadata
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import (
    HEX_40_RE,
    HEX_64_RE,
    ValidationReport,
    canonical_json_bytes,
    is_link_like,
    load_json,
    looks_unresolved,
    path_traverses_link,
    resolve_beneath,
    safe_relative_path,
    sanitized_child_environment,
    sha256_bytes,
    sha256_file,
    validate_hex_digest,
    write_new_json,
)
from .release import (
    EXPECTED_COMMAND_COMPONENTS,
    EXPECTED_COMMAND_EXECUTABLES,
    validate_release_bundle,
    validate_release_manifest,
)
from .service_plan import EXPECTED_COMMAND_REFS


SERVICE_ORDER = (
    "DumbMoneyCore",
    "DumbMoneyResearchMesh",
    "DumbMoneyModelGateway",
    "DumbMoneyDummyKalshi",
    "DumbMoneyDopeyRobinhood",
)
EXPECTED_RUNNERS = {
    service_name: {
        "command_ref": EXPECTED_COMMAND_REFS[service_name],
        "executable_name": EXPECTED_COMMAND_EXECUTABLES[
            EXPECTED_COMMAND_REFS[service_name]
        ],
        "component": EXPECTED_COMMAND_COMPONENTS[EXPECTED_COMMAND_REFS[service_name]],
    }
    for service_name in SERVICE_ORDER
}
REQUIRED_COMPONENTS = frozenset(
    value["component"] for value in EXPECTED_RUNNERS.values()
)
BUILD_SPEC_FIELDS = {
    "schema_version",
    "product",
    "deployment_scope",
    "build_authority",
    "service_control_mutations_authorized",
    "broker_actions_authorized",
    "builder",
    "desktop_artifact",
    "runners",
}
BUILDER_FIELDS = {
    "backend",
    "pyinstaller_version",
    "python_version",
    "python_executable_sha256",
    "git_executable_path",
    "git_executable_sha256",
    "powershell_executable_path",
    "powershell_executable_sha256",
    "source_date_epoch",
}
RUNNER_FIELDS = {
    "service_name",
    "command_ref",
    "executable_name",
    "component",
    "import_root",
    "entry_module",
    "entry_callable",
    "dependency_lock_path",
    "dependency_lock_sha256",
    "hidden_imports",
}
DESKTOP_ARTIFACT_FIELDS = {
    "artifact_name",
    "executable_name",
    "component",
    "sha256",
    "bytes",
    "authenticode_required",
}
MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
CALLABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
LEGACY_SIDECAR_SUFFIXES = frozenset({".bat", ".cmd", ".lnk", ".ps1", ".py", ".pyw"})
BUILD_TOOL_RELATIVE_PATH = "deployment/dumbmoney/package_windows.py"


BuildRunner = Callable[[dict[str, Any], Path], None]


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


def _extend_with_prefix(
    target: ValidationReport,
    source: ValidationReport,
    prefix: str,
    *,
    include_partial: bool = True,
) -> None:
    for issue in source.issues:
        if issue.severity == "PARTIAL" and not include_partial:
            continue
        path = f"{prefix}.{issue.path}" if issue.path else prefix
        if issue.severity == "ERROR":
            target.error(issue.code, issue.message, path)
        elif issue.severity == "PARTIAL":
            target.partial(issue.code, issue.message, path)
        else:
            target.info(issue.code, issue.message, path)


def _pinned_executable_matches(executable: Path, expected_digest: str) -> bool:
    if (
        not executable.is_absolute()
        or not isinstance(expected_digest, str)
        or HEX_64_RE.fullmatch(expected_digest) is None
    ):
        return False
    try:
        return (
            executable.is_file()
            and not is_link_like(executable)
            and not path_traverses_link(executable)
            and sha256_file(executable) == expected_digest
        )
    except OSError:
        return False


def _pinned_executable_version(
    kind: str,
    executable: Path,
    expected_digest: str,
) -> str | None:
    if not _pinned_executable_matches(executable, expected_digest):
        return None
    if kind == "git":
        command = [str(executable), "--version"]
    elif kind == "powershell":
        command = [
            str(executable),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[Console]::Out.Write($PSVersionTable.PSVersion.ToString())",
        ]
    else:
        raise ValueError(f"unsupported pinned tool kind: {kind}")
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=sanitized_child_environment(executable),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode:
        return None
    version = completed.stdout.strip()
    return version or None


def _validate_pinned_executable(
    report: ValidationReport,
    builder: dict[str, Any],
    *,
    kind: str,
    display_name: str,
    expected_names: frozenset[str],
) -> dict[str, str] | None:
    path_field = f"{kind}_executable_path"
    digest_field = f"{kind}_executable_sha256"
    raw_path = builder.get(path_field)
    digest_valid = validate_hex_digest(
        report,
        builder.get(digest_field),
        field_path=f"builder.{digest_field}",
    )
    if looks_unresolved(raw_path):
        report.partial(
            "WINDOWS_BUILD_TOOL_PATH_UNRESOLVED",
            f"{display_name} executable path must be explicitly resolved",
            f"builder.{path_field}",
        )
        return None
    if not isinstance(raw_path, str) or not raw_path:
        report.error(
            "WINDOWS_BUILD_TOOL_PATH_INVALID",
            f"{display_name} executable path must be a non-empty string",
            f"builder.{path_field}",
        )
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        report.error(
            "WINDOWS_BUILD_TOOL_PATH_NOT_ABSOLUTE",
            f"{display_name} executable path must be absolute",
            f"builder.{path_field}",
        )
        return None
    executable = candidate.resolve(strict=False)
    if executable.name.casefold() not in expected_names:
        report.error(
            "WINDOWS_BUILD_TOOL_NAME_INVALID",
            f"{display_name} executable has an unexpected basename",
            str(executable),
        )
        return None
    if (
        not executable.is_file()
        or is_link_like(candidate)
        or path_traverses_link(candidate)
    ):
        report.error(
            "WINDOWS_BUILD_TOOL_EXECUTABLE_INVALID",
            f"{display_name} must be a real non-link executable",
            str(executable),
        )
        return None
    observed_digest = sha256_file(executable)
    if not digest_valid:
        return None
    if observed_digest != builder.get(digest_field):
        report.error(
            "WINDOWS_BUILD_TOOL_DIGEST_MISMATCH",
            f"{display_name} executable differs from its build-spec digest",
            str(executable),
        )
        return None
    version = _pinned_executable_version(kind, executable, observed_digest)
    if version is None:
        report.error(
            "WINDOWS_BUILD_TOOL_VERSION_UNREADABLE",
            f"{display_name} version could not be read from the pinned executable",
            str(executable),
        )
        return None
    return {
        "name": display_name,
        "path": str(executable),
        "version": version,
        "sha256": observed_digest,
    }


def _run_git(
    git_executable: Path,
    git_executable_sha256: str,
    repo_root: Path,
    *arguments: str,
) -> tuple[int, str]:
    if not _pinned_executable_matches(
        git_executable,
        git_executable_sha256,
    ):
        return -1, "pinned Git executable changed or became link-like"
    try:
        completed = subprocess.run(
            [
                str(git_executable),
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-C",
                str(repo_root),
                *arguments,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=sanitized_child_environment(git_executable),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    output = completed.stdout.strip() or completed.stderr.strip()
    return completed.returncode, output


def _distribution_digest(distribution_name: str) -> tuple[str, str] | None:
    try:
        distribution = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return None
    records: list[dict[str, Any]] = []
    for relative in sorted(distribution.files or (), key=lambda item: str(item)):
        path = Path(distribution.locate_file(relative))
        if not path.is_file() or is_link_like(path):
            continue
        records.append(
            {
                "path": str(relative).replace("\\", "/"),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    if not records:
        return None
    return distribution.version, sha256_bytes(canonical_json_bytes(records))


def _normalize_stage_root(
    report: ValidationReport,
    staging_root: Path,
    *,
    repo_roots: dict[str, Path],
) -> Path | None:
    if not staging_root.is_absolute():
        report.error(
            "WINDOWS_BUILD_STAGE_ROOT_NOT_ABSOLUTE",
            "staging root must be an explicit absolute path",
            "staging_root",
        )
        return None
    resolved = staging_root.resolve(strict=False)
    if staging_root.exists():
        report.error(
            "WINDOWS_BUILD_STAGE_ROOT_EXISTS",
            "staging root must be new and must not overwrite prior evidence",
            str(resolved),
        )
    if not resolved.parent.is_dir():
        report.error(
            "WINDOWS_BUILD_STAGE_PARENT_MISSING",
            "the explicit staging parent must already exist",
            str(resolved.parent),
        )
    cursor = resolved.parent
    while cursor != cursor.parent:
        if cursor.exists() and is_link_like(cursor):
            report.error(
                "WINDOWS_BUILD_STAGE_LINK_FORBIDDEN",
                "staging paths may not traverse symlinks or Windows junctions",
                str(cursor),
            )
            break
        cursor = cursor.parent

    protected_roots = [
        Path(r"C:\Program Files\DumbMoney"),
        Path(r"C:\ProgramData\DumbMoney"),
    ]
    for protected in protected_roots:
        try:
            resolved.relative_to(protected.resolve(strict=False))
        except ValueError:
            continue
        report.error(
            "WINDOWS_BUILD_INSTALL_PATH_FORBIDDEN",
            "the builder may not stage inside Program Files or ProgramData",
            str(resolved),
        )
    for component, root in repo_roots.items():
        try:
            resolved.relative_to(root.resolve(strict=False))
        except ValueError:
            continue
        report.error(
            "WINDOWS_BUILD_STAGE_INSIDE_SOURCE_FORBIDDEN",
            "staging inside a source repository would dirty the build input",
            f"{component}:{resolved}",
        )
    return resolved


def _validate_repository(
    report: ValidationReport,
    component: str,
    root: Path | None,
    *,
    git_executable: Path | None,
    git_executable_sha256: str | None,
) -> dict[str, Any] | None:
    prefix = f"repositories[{component}]"
    if root is None:
        report.partial(
            "WINDOWS_BUILD_REPOSITORY_ROOT_MISSING",
            f"an explicit clean repository root is required for {component}",
            prefix,
        )
        return None
    if git_executable is None or git_executable_sha256 is None:
        report.partial(
            "WINDOWS_BUILD_REPOSITORY_GIT_TOOL_UNRESOLVED",
            f"{component} cannot be inspected until the pinned Git tool resolves",
            prefix,
        )
        return None
    root = root.resolve(strict=False)
    if not root.is_dir() or is_link_like(root):
        report.error(
            "WINDOWS_BUILD_REPOSITORY_ROOT_INVALID",
            "repository root must be a real directory, not a link",
            str(root),
        )
        return None
    returncode, top_level = _run_git(
        git_executable,
        git_executable_sha256,
        root,
        "rev-parse",
        "--show-toplevel",
    )
    if returncode:
        report.error(
            "WINDOWS_BUILD_GIT_ROOT_UNREADABLE",
            f"could not resolve repository root: {top_level}",
            str(root),
        )
        return None
    if Path(top_level).resolve(strict=False) != root:
        report.error(
            "WINDOWS_BUILD_REPOSITORY_ROOT_MISMATCH",
            "supplied root must be the exact Git top-level directory",
            str(root),
        )
    returncode, commit = _run_git(
        git_executable,
        git_executable_sha256,
        root,
        "rev-parse",
        "HEAD",
    )
    if returncode or HEX_40_RE.fullmatch(commit) is None:
        report.error(
            "WINDOWS_BUILD_COMMIT_UNREADABLE",
            "repository HEAD must resolve to a full Git object ID",
            str(root),
        )
        return None
    returncode, tree = _run_git(
        git_executable,
        git_executable_sha256,
        root,
        "rev-parse",
        "HEAD^{tree}",
    )
    if returncode or HEX_40_RE.fullmatch(tree) is None:
        report.error(
            "WINDOWS_BUILD_TREE_UNREADABLE",
            "repository tree must resolve to a full Git object ID",
            str(root),
        )
        return None
    returncode, dirty = _run_git(
        git_executable,
        git_executable_sha256,
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if returncode:
        report.error(
            "WINDOWS_BUILD_GIT_STATUS_UNREADABLE",
            f"could not inspect source cleanliness: {dirty}",
            str(root),
        )
    elif dirty:
        report.partial(
            "WINDOWS_BUILD_SOURCE_NOT_CLEAN",
            f"{component} has tracked or untracked changes and cannot be packaged",
            str(root),
        )
    return {
        "component": component,
        "root": str(root),
        "commit": commit,
        "tree": tree,
        "clean": not bool(dirty),
    }


def _resolve_import_root(repo_root: Path, value: Any) -> Path:
    if value == ".":
        return repo_root
    return resolve_beneath(repo_root, value)


def _module_source(import_root: Path, module: str) -> Path | None:
    parts = module.split(".")
    module_file = import_root.joinpath(*parts).with_suffix(".py")
    package_file = import_root.joinpath(*parts, "__init__.py")
    candidates = [path for path in (module_file, package_file) if path.is_file()]
    if len(candidates) != 1:
        return None
    return candidates[0]


def _callable_is_service_entrypoint(path: Path, callable_name: str) -> tuple[bool, str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == callable_name
    ]
    if len(definitions) != 1:
        return False, "callable must be defined exactly once at module scope"
    definition = definitions[0]
    if isinstance(definition, ast.AsyncFunctionDef):
        return False, "async callables require an explicit synchronous service wrapper"
    positional = [*definition.args.posonlyargs, *definition.args.args]
    required_positional = len(positional) - len(definition.args.defaults)
    required_keyword_only = sum(
        default is None for default in definition.args.kw_defaults
    )
    if required_positional or required_keyword_only:
        return False, "service entry callable cannot require injected arguments"
    return True, ""


def _validate_runner(
    report: ValidationReport,
    runner: Any,
    *,
    repositories: dict[str, dict[str, Any]],
    git_executable: Path | None,
    git_executable_sha256: str | None,
) -> dict[str, Any] | None:
    if not isinstance(runner, dict):
        report.error(
            "WINDOWS_BUILD_RUNNER_INVALID",
            "runner entries must be objects",
            "runners",
        )
        return None
    _reject_extra_fields(
        report,
        runner,
        RUNNER_FIELDS,
        code="WINDOWS_BUILD_RUNNER_FIELDS_FORBIDDEN",
        path="runners",
    )
    service_name = runner.get("service_name")
    prefix = f"runners[{service_name or '?'}]"
    expected = EXPECTED_RUNNERS.get(str(service_name))
    if expected is None:
        report.error(
            "WINDOWS_BUILD_RUNNER_NAME_INVALID",
            f"runner must name one of {list(SERVICE_ORDER)}",
            f"{prefix}.service_name",
        )
        return None
    for field in ("command_ref", "executable_name", "component"):
        if runner.get(field) != expected[field]:
            report.error(
                "WINDOWS_BUILD_RUNNER_BINDING_INVALID",
                f"{field} must equal {expected[field]}",
                f"{prefix}.{field}",
            )
    executable_name = runner.get("executable_name")
    if (
        not isinstance(executable_name, str)
        or Path(executable_name).name != executable_name
        or not executable_name.lower().endswith(".exe")
    ):
        report.error(
            "WINDOWS_BUILD_EXECUTABLE_NAME_INVALID",
            "executable_name must be the exact basename ending in .exe",
            f"{prefix}.executable_name",
        )

    module = runner.get("entry_module")
    callable_name = runner.get("entry_callable")
    unresolved_entrypoint = looks_unresolved(module) or looks_unresolved(callable_name)
    if unresolved_entrypoint:
        report.partial(
            "WINDOWS_BUILD_RUNNER_ENTRYPOINT_UNRESOLVED",
            f"{service_name} has no packageable supervised service entrypoint",
            prefix,
        )
    elif (
        not isinstance(module, str)
        or MODULE_RE.fullmatch(module) is None
        or not isinstance(callable_name, str)
        or CALLABLE_RE.fullmatch(callable_name) is None
    ):
        report.error(
            "WINDOWS_BUILD_RUNNER_ENTRYPOINT_INVALID",
            "entrypoint must be an importable module and simple callable name",
            prefix,
        )

    hidden_imports = runner.get("hidden_imports")
    if (
        not isinstance(hidden_imports, list)
        or any(
            not isinstance(item, str) or MODULE_RE.fullmatch(item) is None
            for item in hidden_imports
        )
        or hidden_imports != sorted(set(hidden_imports))
    ):
        report.error(
            "WINDOWS_BUILD_HIDDEN_IMPORTS_INVALID",
            "hidden_imports must be a sorted unique module-name array",
            f"{prefix}.hidden_imports",
        )

    component = str(runner.get("component", ""))
    repository = repositories.get(component)
    if repository is not None:
        assert git_executable is not None
        assert git_executable_sha256 is not None
    dependency_lock_path: str | None = None
    dependency_digest = runner.get("dependency_lock_sha256")
    lock_digest_valid = validate_hex_digest(
        report,
        dependency_digest,
        field_path=f"{prefix}.dependency_lock_sha256",
    )
    if looks_unresolved(runner.get("dependency_lock_path")):
        report.partial(
            "WINDOWS_BUILD_DEPENDENCY_LOCK_UNRESOLVED",
            f"{service_name} requires an exact dependency lock",
            f"{prefix}.dependency_lock_path",
        )
    elif repository is not None:
        repo_root = Path(repository["root"])
        try:
            dependency_lock_path = safe_relative_path(
                runner.get("dependency_lock_path")
            ).as_posix()
            lock_file = resolve_beneath(repo_root, dependency_lock_path)
        except (TypeError, ValueError) as exc:
            report.error(
                "WINDOWS_BUILD_DEPENDENCY_LOCK_PATH_INVALID",
                str(exc),
                f"{prefix}.dependency_lock_path",
            )
        else:
            if not lock_file.is_file():
                report.error(
                    "WINDOWS_BUILD_DEPENDENCY_LOCK_MISSING",
                    "dependency lock file is missing",
                    str(lock_file),
                )
            else:
                returncode, _ = _run_git(
                    git_executable,
                    git_executable_sha256,
                    repo_root,
                    "ls-files",
                    "--error-unmatch",
                    dependency_lock_path,
                )
                if returncode:
                    report.error(
                        "WINDOWS_BUILD_DEPENDENCY_LOCK_UNTRACKED",
                        "dependency lock must be tracked in the clean source commit",
                        str(lock_file),
                    )
                if lock_digest_valid and sha256_file(lock_file) != dependency_digest:
                    report.error(
                        "WINDOWS_BUILD_DEPENDENCY_LOCK_HASH_MISMATCH",
                        "dependency lock differs from its build-spec digest",
                        str(lock_file),
                    )

    import_root_path: Path | None = None
    module_path: Path | None = None
    if repository is not None and not unresolved_entrypoint:
        repo_root = Path(repository["root"])
        try:
            import_root_path = _resolve_import_root(
                repo_root,
                runner.get("import_root"),
            )
        except (TypeError, ValueError) as exc:
            report.error(
                "WINDOWS_BUILD_IMPORT_ROOT_INVALID",
                str(exc),
                f"{prefix}.import_root",
            )
        else:
            if not import_root_path.is_dir() or is_link_like(import_root_path):
                report.error(
                    "WINDOWS_BUILD_IMPORT_ROOT_MISSING",
                    "import_root must be a real directory inside the repository",
                    str(import_root_path),
                )
            else:
                module_path = _module_source(import_root_path, str(module))
                if module_path is None:
                    report.partial(
                        "WINDOWS_BUILD_RUNNER_MODULE_MISSING",
                        f"{module} does not resolve to exactly one Python source",
                        str(import_root_path),
                    )
                else:
                    try:
                        tracked_relative = module_path.relative_to(repo_root).as_posix()
                    except ValueError:
                        report.error(
                            "WINDOWS_BUILD_RUNNER_MODULE_ESCAPES_SOURCE",
                            "entry module escapes its declared repository",
                            str(module_path),
                        )
                    else:
                        returncode, _ = _run_git(
                            git_executable,
                            git_executable_sha256,
                            repo_root,
                            "ls-files",
                            "--error-unmatch",
                            tracked_relative,
                        )
                        if returncode:
                            report.error(
                                "WINDOWS_BUILD_RUNNER_MODULE_UNTRACKED",
                                "entry module must be tracked in the clean source commit",
                                str(module_path),
                            )
                        valid_callable, reason = _callable_is_service_entrypoint(
                            module_path,
                            str(callable_name),
                        )
                        if not valid_callable:
                            report.partial(
                                "WINDOWS_BUILD_RUNNER_CALLABLE_NOT_SERVICE_SAFE",
                                reason,
                                str(module_path),
                            )

    return {
        **runner,
        "import_root_path": (
            str(import_root_path) if import_root_path is not None else None
        ),
        "module_path": str(module_path) if module_path is not None else None,
        "dependency_lock_path": dependency_lock_path,
    }


def _authenticode_status(
    path: Path,
    powershell_executable: Path,
    powershell_executable_sha256: str,
) -> str | None:
    if not _pinned_executable_matches(
        powershell_executable,
        powershell_executable_sha256,
    ):
        return None
    command = (
        "$signature = Get-AuthenticodeSignature -LiteralPath $args[0]; "
        "[Console]::Out.Write($signature.Status.ToString())"
    )
    try:
        completed = subprocess.run(
            [
                str(powershell_executable),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=sanitized_child_environment(powershell_executable),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode:
        return None
    status = completed.stdout.strip()
    return status or None


def _validate_desktop_artifact(
    report: ValidationReport,
    value: Any,
    *,
    desktop_executable_path: Path | None,
    powershell_executable: Path | None,
    powershell_executable_sha256: str | None,
) -> dict[str, Any] | None:
    prefix = "desktop_artifact"
    if not isinstance(value, dict):
        report.error(
            "WINDOWS_BUILD_DESKTOP_ARTIFACT_MISSING",
            "desktop_artifact must be an object",
            prefix,
        )
        return None
    _reject_extra_fields(
        report,
        value,
        DESKTOP_ARTIFACT_FIELDS,
        code="WINDOWS_BUILD_DESKTOP_ARTIFACT_FIELDS_FORBIDDEN",
        path=prefix,
    )
    exact = {
        "artifact_name": "desktop-executable",
        "executable_name": "DumbMoney.exe",
        "component": "blunder",
        "authenticode_required": True,
    }
    valid = True
    for field, expected in exact.items():
        if value.get(field) != expected:
            report.error(
                "WINDOWS_BUILD_DESKTOP_ARTIFACT_INVALID",
                f"{field} must equal {expected!r}",
                f"{prefix}.{field}",
            )
            valid = False
    digest_valid = validate_hex_digest(
        report,
        value.get("sha256"),
        field_path=f"{prefix}.sha256",
    )
    size = value.get("bytes")
    size_valid = isinstance(size, int) and not isinstance(size, bool) and size > 0
    if size == 0:
        report.partial(
            "WINDOWS_BUILD_DESKTOP_SIZE_UNRESOLVED",
            "prebuilt DumbMoney.exe byte size must resolve before staging",
            f"{prefix}.bytes",
        )
    elif not size_valid:
        report.error(
            "WINDOWS_BUILD_DESKTOP_SIZE_INVALID",
            "desktop artifact bytes must be a positive integer",
            f"{prefix}.bytes",
        )
    if desktop_executable_path is None:
        report.partial(
            "WINDOWS_BUILD_DESKTOP_EXECUTABLE_UNRESOLVED",
            (
                "a signed prebuilt DumbMoney.exe must be supplied explicitly; "
                "it is not synthesized by the five-runner builder"
            ),
            prefix,
        )
        return None
    if not desktop_executable_path.is_absolute():
        report.error(
            "WINDOWS_BUILD_DESKTOP_PATH_NOT_ABSOLUTE",
            "desktop executable path must be explicit and absolute",
            str(desktop_executable_path),
        )
        return None
    source = desktop_executable_path.resolve(strict=False)
    if (
        source.name.casefold() != "dumbmoney.exe"
        or not source.is_file()
        or is_link_like(desktop_executable_path)
        or path_traverses_link(desktop_executable_path)
    ):
        report.error(
            "WINDOWS_BUILD_DESKTOP_EXECUTABLE_INVALID",
            "desktop input must be a real file named DumbMoney.exe",
            str(source),
        )
        return None
    sidecars = [
        path
        for path in source.parent.iterdir()
        if path.is_file()
        and path.stem.casefold() == "dumbmoney"
        and path.suffix.casefold() in LEGACY_SIDECAR_SUFFIXES
    ]
    if sidecars:
        report.error(
            "WINDOWS_BUILD_DESKTOP_SIDECAR_FORBIDDEN",
            f"legacy launch sidecars cannot accompany DumbMoney.exe: {sidecars}",
            str(source.parent),
        )
        valid = False
    if not _has_pe_magic(source):
        report.error(
            "WINDOWS_BUILD_DESKTOP_NOT_PE",
            "desktop input must have a Windows PE MZ header",
            str(source),
        )
        valid = False
    observed_digest = sha256_file(source)
    observed_size = source.stat().st_size
    if digest_valid and observed_digest != value.get("sha256"):
        report.error(
            "WINDOWS_BUILD_DESKTOP_DIGEST_MISMATCH",
            "prebuilt DumbMoney.exe differs from its build-spec digest",
            str(source),
        )
        valid = False
    if size_valid and observed_size != size:
        report.error(
            "WINDOWS_BUILD_DESKTOP_SIZE_MISMATCH",
            "prebuilt DumbMoney.exe differs from its build-spec byte size",
            str(source),
        )
        valid = False
    signature_status = (
        _authenticode_status(
            source,
            powershell_executable,
            powershell_executable_sha256,
        )
        if powershell_executable is not None
        and powershell_executable_sha256 is not None
        else None
    )
    if signature_status is None:
        report.partial(
            "WINDOWS_BUILD_DESKTOP_SIGNATURE_UNAVAILABLE",
            "Authenticode status could not be read for the desktop input",
            str(source),
        )
    elif signature_status != "Valid":
        report.partial(
            "WINDOWS_BUILD_DESKTOP_SIGNATURE_NOT_VALID",
            (
                "DumbMoney.exe is blocked until a local Authenticode signature "
                f"reports Valid; observed {signature_status}"
            ),
            str(source),
        )
    if not (valid and digest_valid and size_valid and signature_status == "Valid"):
        return None
    return {
        **value,
        "source_path": str(source),
        "sha256": observed_digest,
        "bytes": observed_size,
        "authenticode_status": signature_status,
    }


def plan_windows_runner_build(
    spec: dict[str, Any],
    *,
    release_template: dict[str, Any],
    release_root: Path,
    staging_root: Path,
    release_id: str,
    repo_roots: dict[str, Path],
    python_executable: Path | None = None,
    desktop_executable_path: Path | None = None,
) -> tuple[ValidationReport, dict[str, Any]]:
    report = ValidationReport("dumbmoney-windows-runner-build-plan")
    python_executable = python_executable or Path(sys.executable)
    normalized_repos = {
        component.lower(): root.resolve(strict=False)
        for component, root in repo_roots.items()
    }
    _reject_extra_fields(
        report,
        spec,
        BUILD_SPEC_FIELDS,
        code="WINDOWS_BUILD_SPEC_FIELDS_FORBIDDEN",
        path="build_spec",
    )
    exact_top_level = {
        "schema_version": "dumbmoney.windows-runner-build.v1",
        "product": "DumbMoney",
        "deployment_scope": "PRIVATE_LOCAL_WINDOWS",
        "build_authority": "PLAN_ONLY",
        "service_control_mutations_authorized": False,
        "broker_actions_authorized": False,
    }
    for field, expected in exact_top_level.items():
        if spec.get(field) != expected:
            report.error(
                "WINDOWS_BUILD_SPEC_BOUNDARY_INVALID",
                f"{field} must equal {expected!r}",
                field,
            )
    if (
        not isinstance(release_id, str)
        or RELEASE_ID_RE.fullmatch(release_id) is None
        or looks_unresolved(release_id)
    ):
        report.error(
            "WINDOWS_BUILD_RELEASE_ID_INVALID",
            "release_id must be a resolved filesystem-safe identifier",
            "release_id",
        )

    normalized_stage = _normalize_stage_root(
        report,
        staging_root,
        repo_roots=normalized_repos,
    )
    base_report = validate_release_manifest(
        release_template,
        release_root=release_root,
    )
    _extend_with_prefix(
        report,
        base_report,
        "release_template",
        include_partial=False,
    )

    builder = spec.get("builder")
    builder_plan: dict[str, Any] = {}
    if not isinstance(builder, dict):
        report.error(
            "WINDOWS_BUILD_BUILDER_MISSING",
            "builder must be an object",
            "builder",
        )
        builder = {}
    else:
        _reject_extra_fields(
            report,
            builder,
            BUILDER_FIELDS,
            code="WINDOWS_BUILD_BUILDER_FIELDS_FORBIDDEN",
            path="builder",
        )
    if builder.get("backend") != "PYINSTALLER_ONEFILE":
        report.error(
            "WINDOWS_BUILD_BACKEND_INVALID",
            "backend must be PYINSTALLER_ONEFILE",
            "builder.backend",
        )
    source_date_epoch = builder.get("source_date_epoch")
    if (
        not isinstance(source_date_epoch, int)
        or isinstance(source_date_epoch, bool)
        or not 946684800 <= source_date_epoch <= 4102444800
    ):
        report.error(
            "WINDOWS_BUILD_SOURCE_DATE_EPOCH_INVALID",
            "source_date_epoch must be a bounded integer",
            "builder.source_date_epoch",
        )

    git_tool = _validate_pinned_executable(
        report,
        builder,
        kind="git",
        display_name="Git",
        expected_names=frozenset({"git", "git.exe"}),
    )
    powershell_tool = _validate_pinned_executable(
        report,
        builder,
        kind="powershell",
        display_name="Windows PowerShell",
        expected_names=frozenset({"powershell.exe"}),
    )

    runtime_python = Path(sys.executable).resolve(strict=True)
    if not python_executable.is_absolute():
        report.error(
            "WINDOWS_BUILD_PYTHON_PATH_NOT_ABSOLUTE",
            "Python executable path must be absolute",
            str(python_executable),
        )
    python_path_invalid = (
        not python_executable.is_file()
        or is_link_like(python_executable)
        or path_traverses_link(python_executable)
    )
    if python_path_invalid:
        report.error(
            "WINDOWS_BUILD_PYTHON_EXECUTABLE_INVALID",
            "Python executable must be a real non-link file",
            str(python_executable),
        )
        resolved_python = python_executable.resolve(strict=False)
    else:
        resolved_python = python_executable.resolve(strict=True)
        if resolved_python != runtime_python:
            report.error(
                "WINDOWS_BUILD_PYTHON_RUNTIME_MISMATCH",
                (
                    "the pinned Python must be the current interpreter so "
                    "version and distribution provenance describe the process "
                    "that actually runs PyInstaller"
                ),
                str(resolved_python),
            )

    actual_python_version = platform.python_version()
    expected_python_version = builder.get("python_version")
    if looks_unresolved(expected_python_version):
        report.partial(
            "WINDOWS_BUILD_PYTHON_VERSION_UNRESOLVED",
            "an exact Python patch version is required",
            "builder.python_version",
        )
    elif expected_python_version != actual_python_version:
        report.error(
            "WINDOWS_BUILD_PYTHON_VERSION_MISMATCH",
            f"expected Python {expected_python_version}, observed {actual_python_version}",
            "builder.python_version",
        )
    if python_path_invalid:
        python_digest = None
    else:
        python_digest = sha256_file(resolved_python)
        if validate_hex_digest(
            report,
            builder.get("python_executable_sha256"),
            field_path="builder.python_executable_sha256",
        ) and python_digest != builder.get("python_executable_sha256"):
            report.error(
                "WINDOWS_BUILD_PYTHON_DIGEST_MISMATCH",
                "Python executable differs from the build-spec pin",
                str(resolved_python),
            )
    distribution = _distribution_digest("pyinstaller")
    expected_pyinstaller_version = builder.get("pyinstaller_version")
    if looks_unresolved(expected_pyinstaller_version):
        report.partial(
            "WINDOWS_BUILD_PYINSTALLER_VERSION_UNRESOLVED",
            "an exact PyInstaller version is required",
            "builder.pyinstaller_version",
        )
    if distribution is None:
        report.partial(
            "WINDOWS_BUILD_PYINSTALLER_MISSING",
            "PyInstaller is not installed in the pinned Python environment",
            "builder",
        )
        actual_pyinstaller_version = None
        pyinstaller_digest = None
    else:
        actual_pyinstaller_version, pyinstaller_digest = distribution
        if (
            not looks_unresolved(expected_pyinstaller_version)
            and actual_pyinstaller_version != expected_pyinstaller_version
        ):
            report.error(
                "WINDOWS_BUILD_PYINSTALLER_VERSION_MISMATCH",
                (
                    f"expected PyInstaller {expected_pyinstaller_version}, "
                    f"observed {actual_pyinstaller_version}"
                ),
                "builder.pyinstaller_version",
            )
    builder_plan = {
        "backend": builder.get("backend"),
        "source_date_epoch": source_date_epoch,
        "python_executable": str(resolved_python),
        "python_version": actual_python_version,
        "python_executable_sha256": python_digest,
        "pyinstaller_version": actual_pyinstaller_version,
        "pyinstaller_distribution_sha256": pyinstaller_digest,
        "git": git_tool,
        "windows_powershell": powershell_tool,
    }

    extra_repo_roots = sorted(set(normalized_repos) - REQUIRED_COMPONENTS)
    if extra_repo_roots:
        report.error(
            "WINDOWS_BUILD_REPOSITORY_SET_INVALID",
            f"unexpected repository roots: {extra_repo_roots}",
            "repositories",
        )
    repositories: dict[str, dict[str, Any]] = {}
    for component in sorted(REQUIRED_COMPONENTS):
        evidence = _validate_repository(
            report,
            component,
            normalized_repos.get(component),
            git_executable=(Path(git_tool["path"]) if git_tool is not None else None),
            git_executable_sha256=(
                git_tool["sha256"] if git_tool is not None else None
            ),
        )
        if evidence is not None:
            repositories[component] = evidence

    raw_runners = spec.get("runners")
    runner_plans: list[dict[str, Any]] = []
    if not isinstance(raw_runners, list):
        report.error(
            "WINDOWS_BUILD_RUNNERS_MISSING",
            "runners must be an array",
            "runners",
        )
        raw_runners = []
    names = [
        runner.get("service_name") for runner in raw_runners if isinstance(runner, dict)
    ]
    duplicates = sorted({str(name) for name in names if names.count(name) > 1})
    if duplicates:
        report.error(
            "WINDOWS_BUILD_RUNNER_DUPLICATED",
            f"runner names must be unique: {duplicates}",
            "runners",
        )
    if set(names) != set(SERVICE_ORDER) or len(raw_runners) != len(SERVICE_ORDER):
        report.error(
            "WINDOWS_BUILD_RUNNER_SET_INVALID",
            (
                f"missing={sorted(set(SERVICE_ORDER) - set(names))}; "
                f"extra={sorted(set(names) - set(SERVICE_ORDER))}"
            ),
            "runners",
        )
    for runner in raw_runners:
        runner_plan = _validate_runner(
            report,
            runner,
            repositories=repositories,
            git_executable=(Path(git_tool["path"]) if git_tool is not None else None),
            git_executable_sha256=(
                git_tool["sha256"] if git_tool is not None else None
            ),
        )
        if runner_plan is not None:
            runner_plans.append(runner_plan)

    locks_by_component: dict[str, set[tuple[Any, Any]]] = {}
    for runner in runner_plans:
        locks_by_component.setdefault(runner["component"], set()).add(
            (
                runner.get("dependency_lock_path"),
                runner.get("dependency_lock_sha256"),
            )
        )
    for component, locks in locks_by_component.items():
        if len(locks) > 1:
            report.error(
                "WINDOWS_BUILD_COMPONENT_LOCK_CONFLICT",
                f"{component} runners must use one exact dependency lock: {locks}",
                f"repositories[{component}]",
            )
        elif component in repositories:
            path, digest = next(iter(locks))
            repositories[component]["dependency_lock_path"] = path
            repositories[component]["dependency_lock_sha256"] = digest

    desktop_plan = _validate_desktop_artifact(
        report,
        spec.get("desktop_artifact"),
        desktop_executable_path=desktop_executable_path,
        powershell_executable=(
            Path(powershell_tool["path"]) if powershell_tool is not None else None
        ),
        powershell_executable_sha256=(
            powershell_tool["sha256"] if powershell_tool is not None else None
        ),
    )

    order = {service: index for index, service in enumerate(SERVICE_ORDER)}
    runner_plans.sort(key=lambda item: order.get(str(item["service_name"]), 999))
    plan = {
        "schema_version": "dumbmoney.windows-runner-build-plan.v1",
        "release_id": release_id,
        "release_root": str(release_root.resolve(strict=False)),
        "staging_root": str(normalized_stage) if normalized_stage else None,
        "build_performed": False,
        "release_candidate_created": False,
        "service_control_mutations_performed": [],
        "broker_actions_performed": [],
        "installation_mutations_performed": [],
        "builder": builder_plan,
        "repositories": repositories,
        "runners": runner_plans,
        "desktop_artifact": desktop_plan,
    }
    report.facts["plan"] = plan
    report.facts["runner_count"] = len(runner_plans)
    report.facts["build_performed"] = False
    return report, plan


def _write_bootstrap(path: Path, module: str, callable_name: str) -> None:
    payload = (
        "from __future__ import annotations\n\n"
        f"from {module} import {callable_name} as _service_main\n\n"
        'if __name__ == "__main__":\n'
        "    _result = _service_main()\n"
        "    if _result is not None and not isinstance(_result, int):\n"
        '        raise TypeError("service entrypoint must return int or None")\n'
        "    raise SystemExit(_result or 0)\n"
    )
    path.write_text(payload, encoding="utf-8", newline="\n")


def _run_pyinstaller(
    plan: dict[str, Any], runner: dict[str, Any], output: Path
) -> None:
    stage_root = Path(plan["staging_root"])
    service = runner["service_name"]
    build_root = stage_root / ".build" / service
    bootstrap = build_root / "bootstrap.py"
    dist_path = output.parent
    work_path = build_root / "work"
    spec_path = build_root / "spec"
    for path in (dist_path, work_path, spec_path):
        path.mkdir(parents=True, exist_ok=False)
    _write_bootstrap(bootstrap, runner["entry_module"], runner["entry_callable"])
    name = Path(runner["executable_name"]).stem
    builder = plan["builder"]
    python_executable = Path(builder["python_executable"])
    command = [
        str(python_executable),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--noupx",
        "--name",
        name,
        "--distpath",
        str(dist_path),
        "--workpath",
        str(work_path),
        "--specpath",
        str(spec_path),
        "--paths",
        runner["import_root_path"],
    ]
    for module in runner["hidden_imports"]:
        command.extend(["--hidden-import", module])
    command.append(str(bootstrap))
    temporary_directory = build_root / "temp"
    temporary_directory.mkdir(parents=True, exist_ok=False)
    if not _pinned_executable_matches(
        python_executable,
        builder["python_executable_sha256"],
    ):
        raise RuntimeError("pinned Python executable changed or became link-like")
    pyinstaller_distribution = _distribution_digest("pyinstaller")
    if pyinstaller_distribution != (
        builder["pyinstaller_version"],
        builder["pyinstaller_distribution_sha256"],
    ):
        raise RuntimeError("pinned PyInstaller distribution changed before execution")
    environment = sanitized_child_environment(
        python_executable,
        temporary_directory=temporary_directory,
    )
    environment.update(
        {
            "PYTHONPYCACHEPREFIX": str(build_root / "pycache"),
            "SOURCE_DATE_EPOCH": str(builder["source_date_epoch"]),
        }
    )
    completed = subprocess.run(
        command,
        cwd=plan["repositories"][runner["component"]]["root"],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if completed.returncode:
        tail = (completed.stderr or completed.stdout)[-2000:]
        raise RuntimeError(
            f"PyInstaller failed for {service} with {completed.returncode}: {tail}"
        )


def _legacy_sidecars(directory: Path, executable_name: str) -> list[Path]:
    stem = Path(executable_name).stem.casefold()
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.stem.casefold() == stem
            and path.suffix.casefold() in LEGACY_SIDECAR_SUFFIXES
        ),
        key=lambda path: path.name.casefold(),
    )


def _has_pe_magic(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(2) == b"MZ"
    except OSError:
        return False


def _copy_pinned_file(
    source_root: Path,
    destination_root: Path,
    record: dict[str, Any],
    *,
    source_date_epoch: int,
) -> None:
    relative = safe_relative_path(record["path"]).as_posix()
    source = resolve_beneath(source_root, relative)
    destination = destination_root.joinpath(*safe_relative_path(relative).parts)
    if (
        sha256_file(source) != record["sha256"]
        or source.stat().st_size != record["bytes"]
    ):
        raise ValueError(f"source release file changed after validation: {relative}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"candidate file already exists: {destination}")
    shutil.copyfile(source, destination)
    os.utime(destination, (source_date_epoch, source_date_epoch))


def _render_release_candidate(
    report: ValidationReport,
    *,
    plan: dict[str, Any],
    spec_path: Path,
    release_template_path: Path,
    runner_records: list[dict[str, Any]],
    desktop_record: dict[str, Any],
    repo_roots: dict[str, Path],
) -> Path | None:
    stage_root = Path(plan["staging_root"])
    release_root = Path(plan["release_root"])
    candidate_root = stage_root / "release-candidate"
    candidate_root.mkdir(parents=False, exist_ok=False)
    template = load_json(release_template_path)
    source_date_epoch = plan["builder"]["source_date_epoch"]
    try:
        for record in template["files"]:
            _copy_pinned_file(
                release_root,
                candidate_root,
                record,
                source_date_epoch=source_date_epoch,
            )
        fund_ref = template["fund_lock"]
        if fund_ref["path"] not in {record["path"] for record in template["files"]}:
            fund_source = resolve_beneath(release_root, fund_ref["path"])
            fund_destination = candidate_root.joinpath(
                *safe_relative_path(fund_ref["path"]).parts
            )
            fund_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fund_source, fund_destination)
            os.utime(
                fund_destination,
                (source_date_epoch, source_date_epoch),
            )
        source_fund_lock = load_json(resolve_beneath(release_root, fund_ref["path"]))
        authorization = source_fund_lock.get("internal_use_authorization")
        if isinstance(authorization, dict):
            authorization_relative = safe_relative_path(
                authorization.get("artifact_path")
            ).as_posix()
            authorization_destination = candidate_root.joinpath(
                *safe_relative_path(authorization_relative).parts
            )
            if not authorization_destination.exists():
                authorization_source = resolve_beneath(
                    release_root,
                    authorization_relative,
                )
                authorization_destination.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                shutil.copyfile(
                    authorization_source,
                    authorization_destination,
                )
                os.utime(
                    authorization_destination,
                    (source_date_epoch, source_date_epoch),
                )
        for source_relative, installed_name in (
            (fund_ref["path"], "fund.lock.json"),
            (template["services_manifest"]["path"], "services.v1.json"),
        ):
            installed_path = candidate_root / installed_name
            if installed_path.exists():
                raise FileExistsError(
                    f"candidate root contract already exists: {installed_path}"
                )
            shutil.copyfile(
                resolve_beneath(release_root, source_relative),
                installed_path,
            )
            os.utime(installed_path, (source_date_epoch, source_date_epoch))
    except (OSError, TypeError, ValueError) as exc:
        report.error(
            "WINDOWS_BUILD_RELEASE_SCAFFOLD_COPY_FAILED",
            f"{type(exc).__name__}: {exc}",
            str(candidate_root),
        )
        return None

    candidate_runner_records: list[dict[str, Any]] = []
    for runner in runner_records:
        source = Path(runner["built_path"])
        relative = f"runners/{runner['executable_name']}"
        destination = candidate_root / "runners" / runner["executable_name"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        os.utime(destination, (source_date_epoch, source_date_epoch))
        candidate_runner_records.append(
            {
                **runner,
                "executable_path": relative,
                "built_path": None,
            }
        )

    desktop_relative = "desktop/DumbMoney.exe"
    desktop_source = Path(desktop_record["source_path"])
    desktop_destination = candidate_root / "desktop" / "DumbMoney.exe"
    desktop_destination.parent.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(desktop_source, desktop_destination)
    os.utime(desktop_destination, (source_date_epoch, source_date_epoch))

    provenance_root = candidate_root / "provenance"
    provenance_root.mkdir(parents=True, exist_ok=False)
    resolved_spec_path = provenance_root / "windows-runner-build.v1.json"
    write_new_json(resolved_spec_path, load_json(spec_path))
    os.utime(resolved_spec_path, (source_date_epoch, source_date_epoch))

    repository_records = []
    for component in sorted(plan["repositories"]):
        repository = plan["repositories"][component]
        repository_records.append(
            {
                "component": component,
                "commit": repository["commit"],
                "tree": repository["tree"],
                "dependency_lock_path": repository["dependency_lock_path"],
                "dependency_lock_sha256": repository["dependency_lock_sha256"],
            }
        )
    provenance_without_id = {
        "schema_version": "dumbmoney.windows-runner-build-provenance.v1",
        "release_id": plan["release_id"],
        "created_at": datetime.fromtimestamp(
            source_date_epoch,
            tz=timezone.utc,
        )
        .isoformat()
        .replace("+00:00", "Z"),
        "deployment_scope": "PRIVATE_LOCAL_WINDOWS",
        "source_date_epoch": source_date_epoch,
        "python": {
            "name": platform.python_implementation(),
            "path": plan["builder"]["python_executable"],
            "version": plan["builder"]["python_version"],
            "sha256": plan["builder"]["python_executable_sha256"],
        },
        "builder": {
            "name": "PyInstaller",
            "version": plan["builder"]["pyinstaller_version"],
            "sha256": plan["builder"]["pyinstaller_distribution_sha256"],
        },
        "build_tool": {
            "name": BUILD_TOOL_RELATIVE_PATH,
            "version": "dumbmoney.windows-runner-build.v1",
            "sha256": sha256_file(
                resolve_beneath(release_root, BUILD_TOOL_RELATIVE_PATH)
            ),
        },
        "source_control": plan["builder"]["git"],
        "signature_verifier": plan["builder"]["windows_powershell"],
        "child_environment_policy": "SANITIZED_ALLOWLIST_V1",
        "build_spec_sha256": sha256_file(spec_path),
        "release_template_sha256": sha256_file(release_template_path),
        "repositories": repository_records,
        "runners": [
            {
                "service_name": runner["service_name"],
                "command_ref": runner["command_ref"],
                "component": runner["component"],
                "entrypoint": (f"{runner['entry_module']}:{runner['entry_callable']}"),
                "executable_path": runner["executable_path"],
                "sha256": runner["sha256"],
                "bytes": runner["bytes"],
                "repository_commit": runner["repository_commit"],
                "dependency_lock_sha256": runner["dependency_lock_sha256"],
                "authenticode_status": "NOT_PERFORMED",
            }
            for runner in candidate_runner_records
        ],
        "desktop": {
            "artifact_name": "desktop-executable",
            "component": desktop_record["component"],
            "executable_path": desktop_relative,
            "sha256": desktop_record["sha256"],
            "bytes": desktop_record["bytes"],
            "authenticode_status": desktop_record["authenticode_status"],
        },
        "service_control_mutations_performed": [],
        "broker_actions_performed": [],
        "installation_mutations_performed": [],
        "authenticode_signing_performed": False,
    }
    provenance = {
        **provenance_without_id,
        "build_id": sha256_bytes(canonical_json_bytes(provenance_without_id)),
    }
    provenance_path = provenance_root / "windows-runner-build-provenance.v1.json"
    write_new_json(provenance_path, provenance)
    os.utime(provenance_path, (source_date_epoch, source_date_epoch))

    candidate = copy.deepcopy(template)
    candidate["release_id"] = plan["release_id"]
    candidate["created_at"] = provenance["created_at"]
    candidate["release_state"] = "TEMPLATE"
    candidate["fund_lock"]["path"] = "fund.lock.json"
    candidate["services_manifest"]["path"] = "services.v1.json"
    files_by_path = {
        record["path"]: record
        for record in candidate["files"]
        if isinstance(record, dict) and isinstance(record.get("path"), str)
    }
    for relative in ("fund.lock.json", "services.v1.json"):
        path = candidate_root / relative
        files_by_path[relative] = {
            "path": relative,
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "component": "blunder",
        }
    for runner in candidate_runner_records:
        relative = runner["executable_path"]
        record = {
            "path": relative,
            "sha256": runner["sha256"],
            "bytes": runner["bytes"],
            "component": runner["component"],
        }
        files_by_path[relative] = record
        binding = candidate["command_bindings"][runner["command_ref"]]
        binding.update(
            {
                "executable": relative,
                "sha256": runner["sha256"],
                "bytes": runner["bytes"],
                "component": runner["component"],
            }
        )
    desktop_file_record = {
        "path": desktop_relative,
        "sha256": desktop_record["sha256"],
        "bytes": desktop_record["bytes"],
        "component": desktop_record["component"],
    }
    files_by_path[desktop_relative] = desktop_file_record
    installed_desktop = next(
        (
            artifact
            for artifact in candidate["installed_artifacts"]
            if artifact.get("name") == "desktop-executable"
        ),
        None,
    )
    if not isinstance(installed_desktop, dict):
        report.error(
            "WINDOWS_BUILD_DESKTOP_RELEASE_ARTIFACT_MISSING",
            "release template must declare desktop-executable",
            "installed_artifacts",
        )
        return None
    installed_desktop.update(
        {
            "source_path": desktop_relative,
            "sha256": desktop_record["sha256"],
            "bytes": desktop_record["bytes"],
            "component": desktop_record["component"],
        }
    )
    for relative, path in (
        ("provenance/windows-runner-build.v1.json", resolved_spec_path),
        (
            "provenance/windows-runner-build-provenance.v1.json",
            provenance_path,
        ),
    ):
        files_by_path[relative] = {
            "path": relative,
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "component": "blunder",
        }
    candidate["files"] = [files_by_path[path] for path in sorted(files_by_path)]
    candidate_path = candidate_root / "release-manifest.json"
    write_new_json(candidate_path, candidate)
    os.utime(candidate_path, (source_date_epoch, source_date_epoch))

    fund_lock_path = resolve_beneath(candidate_root, candidate["fund_lock"]["path"])
    candidate_report = validate_release_bundle(
        release_manifest_path=candidate_path,
        fund_lock_path=fund_lock_path,
        release_root=candidate_root,
        repo_roots=repo_roots,
        git_executable=Path(plan["builder"]["git"]["path"]),
        git_executable_sha256=plan["builder"]["git"]["sha256"],
    )
    _extend_with_prefix(report, candidate_report, "release_candidate")
    report.facts["release_candidate_status"] = candidate_report.status
    report.facts["release_candidate_manifest"] = str(candidate_path)
    return candidate_path


def validate_staged_runner_provenance(staging_root: Path) -> ValidationReport:
    report = ValidationReport("dumbmoney-staged-runner-provenance")
    candidate_root = staging_root.resolve(strict=False) / "release-candidate"
    provenance_path = (
        candidate_root / "provenance" / "windows-runner-build-provenance.v1.json"
    )
    try:
        provenance = load_json(provenance_path)
    except (OSError, ValueError) as exc:
        report.error(
            "WINDOWS_BUILD_PROVENANCE_UNREADABLE",
            f"{type(exc).__name__}: {exc}",
            str(provenance_path),
        )
        return report
    runners = provenance.get("runners")
    if not isinstance(runners, list) or len(runners) != len(SERVICE_ORDER):
        report.error(
            "WINDOWS_BUILD_PROVENANCE_RUNNER_SET_INVALID",
            "provenance must contain exactly five runner records",
            "runners",
        )
        return report
    names = {
        runner.get("service_name") for runner in runners if isinstance(runner, dict)
    }
    if names != set(SERVICE_ORDER):
        report.error(
            "WINDOWS_BUILD_PROVENANCE_RUNNER_SET_INVALID",
            "provenance runner names do not match the exact service set",
            "runners",
        )
    for runner in runners:
        if not isinstance(runner, dict):
            report.error(
                "WINDOWS_BUILD_PROVENANCE_RUNNER_INVALID",
                "runner records must be objects",
                "runners",
            )
            continue
        name = runner.get("service_name")
        expected = EXPECTED_RUNNERS.get(str(name))
        if expected is None:
            continue
        relative = runner.get("executable_path")
        try:
            normalized = safe_relative_path(relative).as_posix()
            executable = resolve_beneath(candidate_root, normalized)
        except (TypeError, ValueError) as exc:
            report.error(
                "WINDOWS_BUILD_PROVENANCE_PATH_INVALID",
                str(exc),
                f"runners[{name}].executable_path",
            )
            continue
        if executable.name != expected["executable_name"]:
            report.error(
                "WINDOWS_BUILD_PROVENANCE_EXECUTABLE_INVALID",
                f"{name} must bind {expected['executable_name']}",
                str(executable),
            )
        sidecars = _legacy_sidecars(executable.parent, executable.name)
        if sidecars:
            report.error(
                "WINDOWS_BUILD_LEGACY_SIDECAR_FORBIDDEN",
                f"legacy launch sidecars cannot accompany a runner: {sidecars}",
                str(executable.parent),
            )
        if not executable.is_file() or is_link_like(executable):
            report.error(
                "WINDOWS_BUILD_EXECUTABLE_MISSING",
                "pinned runner executable is missing or link-like",
                str(executable),
            )
            continue
        if not _has_pe_magic(executable):
            report.error(
                "WINDOWS_BUILD_EXECUTABLE_NOT_PE",
                "runner must have a Windows PE MZ header",
                str(executable),
            )
        if sha256_file(executable) != runner.get(
            "sha256"
        ) or executable.stat().st_size != runner.get("bytes"):
            report.error(
                "WINDOWS_BUILD_EXECUTABLE_TAMPERED",
                "runner bytes differ from build provenance",
                str(executable),
            )
    desktop = provenance.get("desktop")
    if not isinstance(desktop, dict):
        report.error(
            "WINDOWS_BUILD_DESKTOP_PROVENANCE_MISSING",
            "provenance must contain the pinned desktop artifact",
            "desktop",
        )
    else:
        try:
            desktop_relative = safe_relative_path(
                desktop.get("executable_path")
            ).as_posix()
            desktop_executable = resolve_beneath(
                candidate_root,
                desktop_relative,
            )
        except (TypeError, ValueError) as exc:
            report.error(
                "WINDOWS_BUILD_DESKTOP_PROVENANCE_PATH_INVALID",
                str(exc),
                "desktop.executable_path",
            )
        else:
            if (
                desktop.get("artifact_name") != "desktop-executable"
                or desktop.get("component") != "blunder"
                or desktop_relative != "desktop/DumbMoney.exe"
                or desktop.get("authenticode_status") != "Valid"
            ):
                report.error(
                    "WINDOWS_BUILD_DESKTOP_PROVENANCE_INVALID",
                    "desktop provenance does not match its exact release contract",
                    "desktop",
                )
            sidecars = _legacy_sidecars(
                desktop_executable.parent,
                desktop_executable.name,
            )
            if sidecars:
                report.error(
                    "WINDOWS_BUILD_DESKTOP_SIDECAR_FORBIDDEN",
                    f"legacy sidecars cannot accompany DumbMoney.exe: {sidecars}",
                    str(desktop_executable.parent),
                )
            if not desktop_executable.is_file() or is_link_like(desktop_executable):
                report.error(
                    "WINDOWS_BUILD_DESKTOP_EXECUTABLE_MISSING",
                    "pinned desktop executable is missing or link-like",
                    str(desktop_executable),
                )
            elif (
                not _has_pe_magic(desktop_executable)
                or sha256_file(desktop_executable) != desktop.get("sha256")
                or desktop_executable.stat().st_size != desktop.get("bytes")
            ):
                report.error(
                    "WINDOWS_BUILD_DESKTOP_TAMPERED",
                    "desktop bytes differ from build provenance",
                    str(desktop_executable),
                )
    report.facts["runner_count"] = len(runners)
    report.facts["desktop_artifact_present"] = isinstance(desktop, dict)
    return report


def build_windows_release_candidate(
    *,
    spec_path: Path,
    release_template_path: Path,
    release_root: Path,
    staging_root: Path,
    release_id: str,
    repo_roots: dict[str, Path],
    perform_build: bool = False,
    python_executable: Path | None = None,
    desktop_executable_path: Path | None = None,
    build_runner: BuildRunner | None = None,
) -> ValidationReport:
    report = ValidationReport("dumbmoney-windows-release-candidate-build")
    release_root = release_root.resolve(strict=False)
    if not release_root.is_dir() or is_link_like(release_root):
        report.error(
            "WINDOWS_BUILD_RELEASE_ROOT_INVALID",
            "release_root must be a real directory",
            str(release_root),
        )
        return report
    try:
        release_template_relative = (
            release_template_path.resolve(strict=False)
            .relative_to(release_root)
            .as_posix()
        )
        resolved_release_template = resolve_beneath(
            release_root,
            release_template_relative,
        )
    except ValueError as exc:
        report.error(
            "WINDOWS_BUILD_RELEASE_TEMPLATE_PATH_INVALID",
            str(exc),
            str(release_template_path),
        )
        return report
    if not resolved_release_template.is_file() or is_link_like(
        resolved_release_template
    ):
        report.error(
            "WINDOWS_BUILD_RELEASE_TEMPLATE_INVALID",
            "release template must be a real file inside release_root",
            str(resolved_release_template),
        )
        return report
    spec_path = spec_path.resolve(strict=False)
    if not spec_path.is_file() or is_link_like(spec_path):
        report.error(
            "WINDOWS_BUILD_SPEC_PATH_INVALID",
            "build spec must be a real file, not a link",
            str(spec_path),
        )
        return report
    try:
        spec = load_json(spec_path)
        release_template = load_json(resolved_release_template)
    except (OSError, ValueError) as exc:
        report.error(
            "WINDOWS_BUILD_INPUT_UNREADABLE",
            f"{type(exc).__name__}: {exc}",
            str(spec_path),
        )
        return report
    plan_report, plan = plan_windows_runner_build(
        spec,
        release_template=release_template,
        release_root=release_root,
        staging_root=staging_root,
        release_id=release_id,
        repo_roots=repo_roots,
        python_executable=python_executable,
        desktop_executable_path=desktop_executable_path,
    )
    _extend_with_prefix(report, plan_report, "build_plan")
    report.facts.update(plan_report.facts)
    report.facts["requested_mode"] = "BUILD" if perform_build else "DRY_RUN"
    report.facts["build_performed"] = False
    report.facts["service_control_mutations_performed"] = []
    report.facts["broker_actions_performed"] = []
    report.facts["installation_mutations_performed"] = []
    if not perform_build:
        return report
    if plan_report.status != "PASS":
        report.partial(
            "WINDOWS_BUILD_BLOCKED_BY_PLAN",
            "no staging mutation occurred because the build plan is not PASS",
            "build_plan",
        )
        return report

    normalized_stage = Path(plan["staging_root"])
    try:
        normalized_stage.mkdir(parents=False, exist_ok=False)
        (normalized_stage / ".build").mkdir(parents=False, exist_ok=False)
    except OSError as exc:
        report.error(
            "WINDOWS_BUILD_STAGE_CREATE_FAILED",
            f"{type(exc).__name__}: {exc}",
            str(normalized_stage),
        )
        return report
    report.facts["build_performed"] = True
    plan["build_performed"] = True
    runner_records: list[dict[str, Any]] = []
    selected_runner = build_runner
    for runner in plan["runners"]:
        output_dir = normalized_stage / ".build" / runner["service_name"] / "dist"
        output = output_dir / runner["executable_name"]
        try:
            if selected_runner is None:
                _run_pyinstaller(plan, runner, output)
            else:
                output_dir.mkdir(parents=True, exist_ok=False)
                selected_runner(runner, output)
        except Exception as exc:  # noqa: BLE001 - converted to bounded evidence
            report.error(
                "WINDOWS_BUILD_RUNNER_FAILED",
                f"{runner['service_name']}: {type(exc).__name__}: {exc}",
                str(output),
            )
            break
        sidecars = _legacy_sidecars(output_dir, runner["executable_name"])
        if sidecars:
            report.error(
                "WINDOWS_BUILD_LEGACY_SIDECAR_FORBIDDEN",
                f"legacy launch sidecars cannot satisfy packaging: {sidecars}",
                str(output_dir),
            )
        if not output.is_file() or is_link_like(output):
            report.error(
                "WINDOWS_BUILD_EXECUTABLE_MISSING",
                "builder did not produce the exact required .exe",
                str(output),
            )
            break
        if not _has_pe_magic(output):
            report.error(
                "WINDOWS_BUILD_EXECUTABLE_NOT_PE",
                "builder output is not a Windows PE executable",
                str(output),
            )
            break
        repository = plan["repositories"][runner["component"]]
        runner_records.append(
            {
                **runner,
                "built_path": str(output),
                "sha256": sha256_file(output),
                "bytes": output.stat().st_size,
                "repository_commit": repository["commit"],
                "dependency_lock_sha256": runner["dependency_lock_sha256"],
            }
        )
    if report.status == "FAIL" or len(runner_records) != len(SERVICE_ORDER):
        report.facts["release_candidate_created"] = False
        return report

    desktop_record = plan.get("desktop_artifact")
    if not isinstance(desktop_record, dict):
        report.partial(
            "WINDOWS_BUILD_DESKTOP_STAGING_BLOCKED",
            "signed prebuilt DumbMoney.exe is unresolved; no candidate was created",
            "desktop_artifact",
        )
        report.facts["release_candidate_created"] = False
        return report
    candidate_path = _render_release_candidate(
        report,
        plan=plan,
        spec_path=spec_path,
        release_template_path=resolved_release_template,
        runner_records=runner_records,
        desktop_record=desktop_record,
        repo_roots=repo_roots,
    )
    if candidate_path is not None:
        report.facts["release_candidate_created"] = True
        staged_report = validate_staged_runner_provenance(normalized_stage)
        _extend_with_prefix(report, staged_report, "staged_provenance")
    return report
