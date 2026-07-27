from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "dumbmoney" / "DumbMoney.DesktopLauncher.psm1"
WRAPPER = ROOT / "scripts" / "dumbmoney" / "Launch-DumbMoneyDesktop.ps1"
HARNESS = (
    ROOT
    / "tests"
    / "powershell"
    / "Invoke-DumbMoneyDesktopLauncherValidation.ps1"
)
PWSH = shutil.which("pwsh")
WINDOWS_PWSH = os.name == "nt" and PWSH is not None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def launcher_fixture(tmp_path: Path) -> dict[str, Any]:
    release_id = "dumbmoney-test-release"
    releases_root = tmp_path / "Program Files" / "DumbMoney" / "releases"
    release_root = releases_root / release_id
    release_root.mkdir(parents=True)

    core_source = release_root / "artifacts" / "core-runner.v1.json"
    core_source.parent.mkdir()
    core_source.write_text(
        '{"schema_version":"dumbmoney.core-runner.v1","test_only":true}\n',
        encoding="utf-8",
    )
    installed_core = (
        tmp_path
        / "ProgramData"
        / "DumbMoney"
        / "config"
        / "core-runner.v1.json"
    )
    installed_core.parent.mkdir(parents=True)
    installed_core.write_bytes(core_source.read_bytes())

    desktop = release_root / "desktop" / "DumbMoney.exe"
    desktop.parent.mkdir()
    desktop.write_bytes(b"MZ\x90\x00DumbMoney-private-local-test-image")

    manifest_path = release_root / "release-manifest.json"
    manifest = {
        "schema_version": "dumbmoney.release-manifest.v1",
        "release_id": release_id,
        "release_state": "SEALED",
        "immutable": True,
        "broker_actions_authorized": False,
        "distribution": "PRIVATE_LOCAL_ONLY",
        "hosted_control_plane": False,
        "telemetry_publication": False,
        "installed_artifacts": [
            {
                "name": "core-runner-config",
                "state": "SEALED",
                "source_path": "artifacts/core-runner.v1.json",
                "install_path": str(installed_core.resolve()),
                "sha256": _sha256(core_source),
                "bytes": core_source.stat().st_size,
                "component": "blunder",
            },
            {
                "name": "desktop-executable",
                "state": "SEALED",
                "source_path": "desktop/DumbMoney.exe",
                "install_path": str(desktop.resolve()),
                "sha256": _sha256(desktop),
                "bytes": desktop.stat().st_size,
                "component": "blunder",
            }
        ],
        "files": [
            {
                "path": "desktop/DumbMoney.exe",
                "sha256": _sha256(desktop),
                "bytes": desktop.stat().st_size,
                "component": "blunder",
            }
        ],
    }
    _write_json(manifest_path, manifest)

    receipt_path = tmp_path / "operator-approved.desktop-receipt.json"
    receipt = {
        "schema_version": "dumbmoney.desktop-deployment-receipt.v1",
        "receipt_id": "desktop-launch-test-receipt",
        "release_id": release_id,
        "release_root": str(release_root.resolve()),
        "release_manifest_path": str(manifest_path.resolve()),
        "release_manifest_sha256": _sha256(manifest_path),
        "desktop_executable_path": str(desktop.resolve()),
        "desktop_executable_sha256": _sha256(desktop),
        "approved": True,
    }
    _write_json(receipt_path, receipt)

    return {
        "desktop": desktop,
        "installed_core": installed_core,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "receipt": receipt,
        "receipt_path": receipt_path,
        "release_root": release_root,
        "releases_root": releases_root,
    }


def _run_launcher(
    fixture: dict[str, Any],
) -> subprocess.CompletedProcess[str]:
    assert PWSH is not None
    return subprocess.run(
        [
            PWSH,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(HARNESS),
            "-ModulePath",
            str(MODULE),
            "-ReleaseManifestPath",
            str(fixture["manifest_path"]),
            "-DeploymentReceiptPath",
            str(fixture["receipt_path"]),
            "-ExpectedReleasesRoot",
            str(fixture["releases_root"]),
            "-ExpectedCoreConfigPath",
            str(fixture["installed_core"]),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _write_manifest_and_rebind_receipt(fixture: dict[str, Any]) -> None:
    _write_json(fixture["manifest_path"], fixture["manifest"])
    fixture["receipt"]["release_manifest_sha256"] = _sha256(
        fixture["manifest_path"]
    )
    _write_json(fixture["receipt_path"], fixture["receipt"])


@pytest.mark.skipif(not WINDOWS_PWSH, reason="requires Windows PowerShell 7.4+")
def test_verified_default_is_dry_run_with_one_nonsecret_child_binding(
    launcher_fixture: dict[str, Any],
) -> None:
    completed = _run_launcher(launcher_fixture)

    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["Schema"] == "dumbmoney.desktop-launch-plan.v1"
    assert plan["Status"] == "VERIFIED"
    assert plan["WouldLaunch"] is False
    assert plan["ReleaseManifestSha256"] == _sha256(
        launcher_fixture["manifest_path"]
    )
    assert plan["DesktopExecutableSha256"] == _sha256(
        launcher_fixture["desktop"]
    )
    assert plan["CoreRunnerConfigSha256"] == _sha256(
        launcher_fixture["installed_core"]
    )
    assert plan["ChildEnvironment"] == {
        "DUMBMONEY_CORE_CONFIG_SHA256": _sha256(
            launcher_fixture["installed_core"]
        )
    }


@pytest.mark.skipif(not WINDOWS_PWSH, reason="requires Windows PowerShell 7.4+")
@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("unknown-field", "fields are invalid"),
        ("not-approved", "approved=true"),
        ("duplicate-field", "duplicate JSON property"),
    ],
)
def test_receipt_parser_is_strict_and_fail_closed(
    launcher_fixture: dict[str, Any],
    mutation: str,
    expected_error: str,
) -> None:
    receipt_path = launcher_fixture["receipt_path"]
    receipt = copy.deepcopy(launcher_fixture["receipt"])
    if mutation == "unknown-field":
        receipt["unexpected"] = "not-accepted"
        _write_json(receipt_path, receipt)
    elif mutation == "not-approved":
        receipt["approved"] = False
        _write_json(receipt_path, receipt)
    else:
        text = json.dumps(receipt, separators=(",", ":"))
        receipt_path.write_text(
            text[:-1] + ',"approved":true}',
            encoding="utf-8",
        )

    completed = _run_launcher(launcher_fixture)

    assert completed.returncode == 2
    assert expected_error in completed.stderr


@pytest.mark.skipif(not WINDOWS_PWSH, reason="requires Windows PowerShell 7.4+")
def test_manifest_tamper_breaks_operator_receipt_hash_relationship(
    launcher_fixture: dict[str, Any],
) -> None:
    with launcher_fixture["manifest_path"].open("ab") as stream:
        stream.write(b" ")

    completed = _run_launcher(launcher_fixture)

    assert completed.returncode == 2
    assert "do not match the operator-approved deployment receipt" in completed.stderr


@pytest.mark.skipif(not WINDOWS_PWSH, reason="requires Windows PowerShell 7.4+")
@pytest.mark.parametrize(
    ("target", "expected_error"),
    [
        (
            "installed_core",
            "installed Core runner config differs from its immutable release pin",
        ),
        (
            "desktop",
            "desktop executable differs from its manifest and operator-approved receipt pins",
        ),
    ],
)
def test_pinned_launch_artifact_tamper_is_rejected(
    launcher_fixture: dict[str, Any],
    target: str,
    expected_error: str,
) -> None:
    with launcher_fixture[target].open("ab") as stream:
        stream.write(b"tampered")

    completed = _run_launcher(launcher_fixture)

    assert completed.returncode == 2
    assert expected_error in completed.stderr


@pytest.mark.skipif(not WINDOWS_PWSH, reason="requires Windows PowerShell 7.4+")
def test_desktop_path_cannot_escape_immutable_release(
    launcher_fixture: dict[str, Any],
    tmp_path: Path,
) -> None:
    escaped_desktop = tmp_path / "outside" / "DumbMoney.exe"
    escaped_desktop.parent.mkdir()
    escaped_desktop.write_bytes(b"MZ\x90\x00escaped-test-image")
    receipt = copy.deepcopy(launcher_fixture["receipt"])
    receipt["desktop_executable_path"] = str(escaped_desktop.resolve())
    receipt["desktop_executable_sha256"] = _sha256(escaped_desktop)
    _write_json(launcher_fixture["receipt_path"], receipt)

    completed = _run_launcher(launcher_fixture)

    assert completed.returncode == 2
    assert "strictly beneath the immutable release root" in completed.stderr


@pytest.mark.skipif(not WINDOWS_PWSH, reason="requires Windows PowerShell 7.4+")
@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_manifest_requires_exactly_one_desktop_executable_artifact(
    launcher_fixture: dict[str, Any],
    mutation: str,
) -> None:
    artifacts = launcher_fixture["manifest"]["installed_artifacts"]
    desktop_artifact = next(
        item for item in artifacts if item["name"] == "desktop-executable"
    )
    if mutation == "missing":
        artifacts.remove(desktop_artifact)
    else:
        artifacts.append(copy.deepcopy(desktop_artifact))
    _write_manifest_and_rebind_receipt(launcher_fixture)

    completed = _run_launcher(launcher_fixture)

    assert completed.returncode == 2
    assert (
        "must contain exactly one desktop-executable artifact"
        in completed.stderr
    )


@pytest.mark.skipif(not WINDOWS_PWSH, reason="requires Windows PowerShell 7.4+")
@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        (
            "source-escape",
            "desktop-executable source_path contains an unsafe path segment",
        ),
        (
            "install-mismatch",
            "install_path must equal the receipt desktop path",
        ),
        (
            "digest-mismatch",
            "digest differs between manifest and receipt",
        ),
        (
            "bytes-mismatch",
            "release files record differs from the installed artifact pin",
        ),
        (
            "files-digest-mismatch",
            "release files record differs from the installed artifact pin",
        ),
        (
            "files-record-missing",
            "must contain exactly one files record for the desktop executable",
        ),
    ],
)
def test_desktop_manifest_artifact_and_files_record_are_cross_bound(
    launcher_fixture: dict[str, Any],
    mutation: str,
    expected_error: str,
) -> None:
    artifact = next(
        item
        for item in launcher_fixture["manifest"]["installed_artifacts"]
        if item["name"] == "desktop-executable"
    )
    file_record = launcher_fixture["manifest"]["files"][0]
    if mutation == "source-escape":
        artifact["source_path"] = "../desktop/DumbMoney.exe"
    elif mutation == "install-mismatch":
        artifact["install_path"] = str(
            launcher_fixture["release_root"] / "desktop" / "Other.exe"
        )
    elif mutation == "digest-mismatch":
        artifact["sha256"] = "0" * 64
    elif mutation == "bytes-mismatch":
        artifact["bytes"] += 1
    elif mutation == "files-digest-mismatch":
        file_record["sha256"] = "0" * 64
    else:
        launcher_fixture["manifest"]["files"] = []
    _write_manifest_and_rebind_receipt(launcher_fixture)

    completed = _run_launcher(launcher_fixture)

    assert completed.returncode == 2
    assert expected_error in completed.stderr


def test_launcher_source_has_one_argument_safe_effect_and_no_remote_or_install_path() -> None:
    module_source = MODULE.read_text(encoding="utf-8")
    wrapper_source = WRAPPER.read_text(encoding="utf-8")
    combined = module_source + "\n" + wrapper_source

    effect_calls = re.findall(
        r"(?m)^\s*return Microsoft\.PowerShell\.Management\\Start-Process\s*`",
        module_source,
    )
    assert len(effect_calls) == 1
    assert "-ArgumentList" not in module_source
    assert "Invoke-Expression" not in combined
    assert re.search(r"(?im)\b(?:New|Set|Remove)-Service\b", combined) is None
    assert re.search(r"(?im)\bInvoke-(?:WebRequest|RestMethod)\b", combined) is None
    assert re.search(r"(?i)https?://", combined) is None
    assert "-Environment $environment" in module_source
    assert "-UseNewEnvironment" in module_source
    assert "-Start:$Start" in wrapper_source


@pytest.mark.skipif(not WINDOWS_PWSH, reason="requires Windows PowerShell 7.4+")
def test_wrapper_rejects_conflicting_effect_modes_before_reading_files() -> None:
    assert PWSH is not None
    completed = subprocess.run(
        [
            PWSH,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WRAPPER),
            "-ReleaseManifestPath",
            r"C:\does-not-exist\release-manifest.json",
            "-DeploymentReceiptPath",
            r"C:\does-not-exist\receipt.json",
            "-DryRun",
            "-Start",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode != 0
    assert "mutually exclusive" in completed.stderr
