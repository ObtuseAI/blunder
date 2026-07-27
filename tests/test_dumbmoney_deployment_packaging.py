from __future__ import annotations

import copy
import json
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from deployment.dumbmoney import package_windows
from deployment.dumbmoney.common import (
    load_json,
    sanitized_child_environment,
    sha256_file,
)
from deployment.dumbmoney.package_windows import (
    SERVICE_ORDER,
    build_windows_release_candidate,
    plan_windows_runner_build,
    validate_staged_runner_provenance,
)


ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = ROOT / "deployment" / "dumbmoney"


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _make_source_repository(
    root: Path,
    modules: dict[str, str],
) -> Path:
    root.mkdir()
    package = root / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    for leaf, body in modules.items():
        (package / f"{leaf}.py").write_text(body, encoding="utf-8")
    (root / "requirements.lock").write_text(
        "cryptography==45.0.6 --hash=sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )
    _git(root, "init")
    _git(root, "config", "user.email", "dumbmoney-tests@example.invalid")
    _git(root, "config", "user.name", "DumbMoney Tests")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "sealed test source")
    return root


@pytest.fixture
def complete_build_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    raw_git_executable = shutil.which("git.exe") or shutil.which("git")
    if raw_git_executable is None:
        pytest.skip("packaging validation tests require Git")
    git_executable = Path(raw_git_executable).resolve(strict=True)
    trusted_tools = tmp_path / "trusted-tools"
    trusted_tools.mkdir()
    powershell_executable = trusted_tools / "powershell.exe"
    powershell_executable.write_bytes(b"MZtest-pinned-windows-powershell")
    entrypoint = "def main(argv=None):\n    return 0\n"
    repos = {
        "blunder": _make_source_repository(
            tmp_path / "blunder",
            {
                "core": entrypoint,
                "research": entrypoint,
                "gateway": entrypoint,
            },
        ),
        "dummy": _make_source_repository(
            tmp_path / "dummy",
            {"dummy": entrypoint},
        ),
        "dopey": _make_source_repository(
            tmp_path / "dopey",
            {"dopey": entrypoint},
        ),
    }
    module_by_service = {
        "DumbMoneyCore": "pkg.core",
        "DumbMoneyResearchMesh": "pkg.research",
        "DumbMoneyModelGateway": "pkg.gateway",
        "DumbMoneyDummyKalshi": "pkg.dummy",
        "DumbMoneyDopeyRobinhood": "pkg.dopey",
    }
    spec = copy.deepcopy(load_json(DEPLOYMENT / "windows-build.v1.template.json"))
    spec["builder"].update(
        {
            "pyinstaller_version": "6.0.0",
            "python_version": platform.python_version(),
            "python_executable_sha256": sha256_file(
                Path(package_windows.sys.executable)
            ),
            "git_executable_path": str(git_executable),
            "git_executable_sha256": sha256_file(git_executable),
            "powershell_executable_path": str(powershell_executable),
            "powershell_executable_sha256": sha256_file(powershell_executable),
        }
    )
    desktop = tmp_path / "DumbMoney.exe"
    desktop.write_bytes(b"MZDumbMoney-desktop-test-pe")
    spec["desktop_artifact"].update(
        {
            "sha256": sha256_file(desktop),
            "bytes": desktop.stat().st_size,
        }
    )
    for runner in spec["runners"]:
        runner["entry_module"] = module_by_service[runner["service_name"]]
        runner["entry_callable"] = "main"
        runner["dependency_lock_path"] = "requirements.lock"
        runner["dependency_lock_sha256"] = sha256_file(
            repos[runner["component"]] / "requirements.lock"
        )
    spec_path = tmp_path / "resolved-build-spec.json"
    spec_path.write_text(
        json.dumps(spec, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        package_windows,
        "_distribution_digest",
        lambda _: ("6.0.0", "d" * 64),
    )
    real_pinned_version = package_windows._pinned_executable_version
    monkeypatch.setattr(
        package_windows,
        "_pinned_executable_version",
        lambda kind, executable, digest: (
            "5.1-test"
            if kind == "powershell"
            else real_pinned_version(kind, executable, digest)
        ),
    )
    monkeypatch.setattr(
        package_windows,
        "_authenticode_status",
        lambda *_: "Valid",
    )
    return {
        "spec": spec,
        "spec_path": spec_path,
        "repos": repos,
        "tmp_path": tmp_path,
        "desktop": desktop,
        "git_executable": git_executable,
        "powershell_executable": powershell_executable,
    }


def _fake_pe_builder(runner: dict[str, object], output: Path) -> None:
    output.write_bytes(
        b"MZ"
        + str(runner["service_name"]).encode("ascii")
        + b"\x00deterministic-test-pe"
    )


def _build(
    inputs: dict[str, object],
    staging_root: Path,
    *,
    build_runner=_fake_pe_builder,
):
    return build_windows_release_candidate(
        spec_path=inputs["spec_path"],
        release_template_path=DEPLOYMENT / "release-manifest.template.json",
        release_root=ROOT,
        staging_root=staging_root,
        release_id="dumbmoney-test-release",
        repo_roots=inputs["repos"],
        perform_build=True,
        desktop_executable_path=inputs["desktop"],
        build_runner=build_runner,
    )


def test_tracked_build_template_is_partial_and_dry_run_has_zero_mutations(
    tmp_path: Path,
) -> None:
    staging_root = tmp_path / "must-not-exist"
    before = sorted(path.name for path in tmp_path.iterdir())

    report = build_windows_release_candidate(
        spec_path=DEPLOYMENT / "windows-build.v1.template.json",
        release_template_path=DEPLOYMENT / "release-manifest.template.json",
        release_root=ROOT,
        staging_root=staging_root,
        release_id="dumbmoney-dry-run",
        repo_roots={},
        perform_build=False,
    )

    assert report.status == "PARTIAL"
    assert not staging_root.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == before
    assert report.facts["build_performed"] is False
    assert report.facts["service_control_mutations_performed"] == []
    assert report.facts["broker_actions_performed"] == []
    assert report.facts["installation_mutations_performed"] == []
    assert "WINDOWS_BUILD_DESKTOP_EXECUTABLE_UNRESOLVED" in {
        issue.code for issue in report.issues
    }


def test_complete_plan_rejects_stage_path_escape(
    complete_build_inputs: dict[str, object],
) -> None:
    repos = complete_build_inputs["repos"]
    spec = complete_build_inputs["spec"]
    release_template = load_json(DEPLOYMENT / "release-manifest.template.json")

    report, _ = plan_windows_runner_build(
        spec,
        release_template=release_template,
        release_root=ROOT,
        staging_root=repos["blunder"] / "escaped-stage",
        release_id="dumbmoney-test-release",
        repo_roots=repos,
    )

    assert report.status == "FAIL"
    assert "WINDOWS_BUILD_STAGE_INSIDE_SOURCE_FORBIDDEN" in {
        issue.code for issue in report.issues
    }


def test_runner_name_cannot_escape_or_substitute_legacy_launcher(
    complete_build_inputs: dict[str, object],
) -> None:
    spec = copy.deepcopy(complete_build_inputs["spec"])
    spec["runners"][0]["executable_name"] = "../DumbMoneyCore.cmd"

    report, _ = plan_windows_runner_build(
        spec,
        release_template=load_json(DEPLOYMENT / "release-manifest.template.json"),
        release_root=ROOT,
        staging_root=complete_build_inputs["tmp_path"] / "stage-name-escape",
        release_id="dumbmoney-test-release",
        repo_roots=complete_build_inputs["repos"],
    )

    assert report.status == "FAIL"
    assert {
        "WINDOWS_BUILD_RUNNER_BINDING_INVALID",
        "WINDOWS_BUILD_EXECUTABLE_NAME_INVALID",
    } <= {issue.code for issue in report.issues}


def test_missing_executable_is_rejected(
    complete_build_inputs: dict[str, object],
) -> None:
    def missing_builder(_: dict[str, object], __: Path) -> None:
        return

    report = _build(
        complete_build_inputs,
        complete_build_inputs["tmp_path"] / "stage-missing",
        build_runner=missing_builder,
    )

    assert report.status == "FAIL"
    assert report.facts["release_candidate_created"] is False
    assert "WINDOWS_BUILD_EXECUTABLE_MISSING" in {issue.code for issue in report.issues}


def test_legacy_sidecar_cannot_satisfy_executable(
    complete_build_inputs: dict[str, object],
) -> None:
    def sidecar_builder(_: dict[str, object], output: Path) -> None:
        output.with_suffix(".cmd").write_text(
            "python -m legacy.operator_live_branch --execute\n",
            encoding="utf-8",
        )

    report = _build(
        complete_build_inputs,
        complete_build_inputs["tmp_path"] / "stage-sidecar",
        build_runner=sidecar_builder,
    )

    assert report.status == "FAIL"
    assert {
        "WINDOWS_BUILD_LEGACY_SIDECAR_FORBIDDEN",
        "WINDOWS_BUILD_EXECUTABLE_MISSING",
    } <= {issue.code for issue in report.issues}


def test_exact_five_runner_candidate_is_deterministic_and_remains_partial(
    complete_build_inputs: dict[str, object],
) -> None:
    first_root = complete_build_inputs["tmp_path"] / "stage-one"
    second_root = complete_build_inputs["tmp_path"] / "stage-two"

    first = _build(complete_build_inputs, first_root)
    second = _build(complete_build_inputs, second_root)

    assert first.status == "PARTIAL"
    assert second.status == "PARTIAL"
    assert not any(issue.severity == "ERROR" for issue in first.issues)
    assert first.facts["build_performed"] is True
    assert first.facts["release_candidate_created"] is True
    assert first.facts["release_candidate_status"] == "PARTIAL"
    manifest = load_json(first_root / "release-candidate" / "release-manifest.json")
    assert manifest["release_state"] == "TEMPLATE"
    assert manifest["fund_lock"]["path"] == "fund.lock.json"
    assert manifest["services_manifest"]["path"] == "services.v1.json"
    assert (
        sha256_file(first_root / "release-candidate" / "fund.lock.json")
        == (manifest["fund_lock"]["sha256"])
    )
    assert (
        sha256_file(first_root / "release-candidate" / "services.v1.json")
        == (manifest["services_manifest"]["sha256"])
    )
    assert {
        binding["executable"] for binding in manifest["command_bindings"].values()
    } == {f"runners/{service}.exe" for service in SERVICE_ORDER}
    first_provenance = load_json(
        first_root
        / "release-candidate"
        / "provenance"
        / "windows-runner-build-provenance.v1.json"
    )
    second_provenance = load_json(
        second_root
        / "release-candidate"
        / "provenance"
        / "windows-runner-build-provenance.v1.json"
    )
    assert first_provenance["build_id"] == second_provenance["build_id"]
    assert len(first_provenance["runners"]) == 5
    assert first_provenance["authenticode_signing_performed"] is False
    assert first_provenance["desktop"] == {
        "artifact_name": "desktop-executable",
        "component": "blunder",
        "executable_path": "desktop/DumbMoney.exe",
        "sha256": sha256_file(
            first_root / "release-candidate" / "desktop" / "DumbMoney.exe"
        ),
        "bytes": (first_root / "release-candidate" / "desktop" / "DumbMoney.exe")
        .stat()
        .st_size,
        "authenticode_status": "Valid",
    }
    assert first_provenance["service_control_mutations_performed"] == []
    assert first_provenance["broker_actions_performed"] == []
    assert first_provenance["installation_mutations_performed"] == []
    assert first_provenance["source_control"] == {
        "name": "Git",
        "path": str(complete_build_inputs["git_executable"]),
        "version": first_provenance["source_control"]["version"],
        "sha256": sha256_file(complete_build_inputs["git_executable"]),
    }
    assert first_provenance["signature_verifier"] == {
        "name": "Windows PowerShell",
        "path": str(complete_build_inputs["powershell_executable"]),
        "version": "5.1-test",
        "sha256": sha256_file(complete_build_inputs["powershell_executable"]),
    }
    assert first_provenance["python"]["path"] == str(
        Path(package_windows.sys.executable).resolve(strict=True)
    )
    assert first_provenance["child_environment_policy"] == ("SANITIZED_ALLOWLIST_V1")


def test_pinned_tools_ignore_hostile_path_and_child_environment_drops_secrets(
    complete_build_inputs: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile = complete_build_inputs["tmp_path"] / "hostile-path"
    hostile.mkdir()
    (hostile / "git.exe").write_bytes(b"MZhostile-git")
    (hostile / "powershell.exe").write_bytes(b"MZhostile-powershell")
    monkeypatch.setenv("PATH", str(hostile))
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-reach-child")

    environment = sanitized_child_environment(
        complete_build_inputs["git_executable"],
    )
    assert str(hostile) not in environment["PATH"]
    assert "OPENROUTER_API_KEY" not in environment

    report = _build(
        complete_build_inputs,
        complete_build_inputs["tmp_path"] / "stage-hostile-path",
    )

    assert report.status == "PARTIAL"
    assert not any(issue.severity == "ERROR" for issue in report.issues)
    assert report.facts["release_candidate_created"] is True


def test_pinned_git_digest_mismatch_fails_before_repository_inspection(
    complete_build_inputs: dict[str, object],
) -> None:
    spec = copy.deepcopy(complete_build_inputs["spec"])
    spec["builder"]["git_executable_sha256"] = "f" * 64

    report, _ = plan_windows_runner_build(
        spec,
        release_template=load_json(DEPLOYMENT / "release-manifest.template.json"),
        release_root=ROOT,
        staging_root=complete_build_inputs["tmp_path"] / "stage-git-mismatch",
        release_id="dumbmoney-test-release",
        repo_roots=complete_build_inputs["repos"],
    )

    assert report.status == "FAIL"
    assert "WINDOWS_BUILD_TOOL_DIGEST_MISMATCH" in {
        issue.code for issue in report.issues
    }


def test_staged_executable_tamper_is_detected(
    complete_build_inputs: dict[str, object],
) -> None:
    staging_root = complete_build_inputs["tmp_path"] / "stage-tamper"
    report = _build(complete_build_inputs, staging_root)
    assert report.status == "PARTIAL"
    core = staging_root / "release-candidate" / "runners" / "DumbMoneyCore.exe"
    core.write_bytes(core.read_bytes() + b"tampered")

    verification = validate_staged_runner_provenance(staging_root)

    assert verification.status == "FAIL"
    assert "WINDOWS_BUILD_EXECUTABLE_TAMPERED" in {
        issue.code for issue in verification.issues
    }


def test_staged_desktop_tamper_is_detected(
    complete_build_inputs: dict[str, object],
) -> None:
    staging_root = complete_build_inputs["tmp_path"] / "stage-desktop-tamper"
    report = _build(complete_build_inputs, staging_root)
    assert report.status == "PARTIAL"
    desktop = staging_root / "release-candidate" / "desktop" / "DumbMoney.exe"
    desktop.write_bytes(desktop.read_bytes() + b"tampered")

    verification = validate_staged_runner_provenance(staging_root)

    assert verification.status == "FAIL"
    assert "WINDOWS_BUILD_DESKTOP_TAMPERED" in {
        issue.code for issue in verification.issues
    }
