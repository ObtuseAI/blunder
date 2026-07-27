from __future__ import annotations

import base64
import copy
import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from deployment.dumbmoney import release as release_validation
from deployment.dumbmoney.common import load_json
from deployment.dumbmoney.common import ValidationReport, canonical_json_bytes
from deployment.dumbmoney.core_config import validate_core_runner_config
from deployment.dumbmoney.readiness import (
    EXPECTED_SERVICES,
    make_ed25519_verifier,
    validate_readiness_descriptor,
)
from blunder.fund.crypto import Ed25519Signer
from deployment.dumbmoney.release import (
    validate_fund_lock,
    validate_ollama_runtime_evidence,
    validate_release_bundle,
    validate_release_manifest,
)
from deployment.dumbmoney.service_plan import (
    build_windows_service_plan,
    load_services_manifest,
    validate_services_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = ROOT / "deployment" / "dumbmoney"


def _readiness_descriptor(
    service_name: str = "DumbMoneyCore",
) -> dict[str, object]:
    broker = {
        "DumbMoneyDummyKalshi": "KALSHI",
        "DumbMoneyDopeyRobinhood": "ROBINHOOD",
    }.get(service_name, "NONE")
    body = {
        "schema": "dumbmoney.readiness-descriptor.v1",
        "service_name": service_name,
        "release_id": "dumbmoney-test",
        "instance_id": "8c2df19e-a2dc-4ad4-9b37-c3f03b6c02c6",
        "process_id": 42,
        "generation": 7,
        "observed_at": "2026-07-26T12:00:00Z",
        "valid_until": "2026-07-26T12:01:30Z",
        "endpoint": {
            "transport": "http",
            "host": "127.0.0.1",
            "port": 49152,
            "base_path": "/",
        },
        "fund_lock_sha256": "a" * 64,
        "service_manifest_sha256": "b" * 64,
        "authority": {
            "broker": broker,
            "mode": "RECONCILIATION_ONLY",
            "execution_enabled": False,
        },
        "health": {"status": "READY"},
        "capabilities": ["health", "reconcile"],
    }
    common = {
        "schema": "dumbmoney.signed-envelope.v1",
        "source_id": service_name,
        "source_sequence": 1,
        "correlation_id": "readiness-test-001",
        "causation_id": None,
        "nonce": "readiness-nonce-001",
        "not_before": "2026-07-26T12:00:00Z",
        "expires_at": "2026-07-26T12:01:30Z",
        "body_schema": "dumbmoney.readiness-descriptor.v1",
        "body_digest": hashlib.sha256(canonical_json_bytes(body)).hexdigest(),
        "body": body,
        "signature_algorithm": "Ed25519",
        "signer_key_id": "c" * 64,
    }
    event_id = hashlib.sha256(canonical_json_bytes(common)).hexdigest()
    return {
        **common,
        "event_id": event_id,
        "signature": base64.urlsafe_b64encode(bytes(64)).rstrip(b"=").decode("ascii"),
    }


def test_service_manifest_is_exact_and_plan_only() -> None:
    manifest = load_services_manifest()
    report = validate_services_manifest(manifest)

    assert report.status == "PASS"
    assert manifest["deployment_scope"] == "PRIVATE_LOCAL_WINDOWS"
    assert manifest["telemetry_publication"] == "DISABLED"
    assert {service["name"] for service in manifest["services"]} == EXPECTED_SERVICES
    authority = {
        service["name"]: service["broker_authority"] for service in manifest["services"]
    }
    assert authority["DumbMoneyDummyKalshi"] == "KALSHI"
    assert authority["DumbMoneyDopeyRobinhood"] == "ROBINHOOD"
    assert all(
        authority[name] == "NONE"
        for name in EXPECTED_SERVICES
        - {"DumbMoneyDummyKalshi", "DumbMoneyDopeyRobinhood"}
    )
    by_name = {service["name"]: service for service in manifest["services"]}
    assert set(by_name["DumbMoneyCore"]["secret_refs"]) == {
        "secret-ref:dumbmoney-core-ed25519-seed",
        "secret-ref:dumbmoney-desktop-read-token",
        "secret-ref:dumbmoney-operator-token",
        "secret-ref:dumbmoney-allocator-token",
        "secret-ref:dumbmoney-dummy-cell-token",
        "secret-ref:dumbmoney-dopey-cell-token",
    }
    assert "secret-ref:dumbmoney-dummy-cell-token" in set(
        by_name["DumbMoneyDummyKalshi"]["secret_refs"]
    )
    assert "secret-ref:dumbmoney-dopey-cell-token" in set(
        by_name["DumbMoneyDopeyRobinhood"]["secret_refs"]
    )
    assert manifest["research_worker_boundary"] == {
        "identity": "DumbMoneyResearchWorker",
        "identity_kind": "NON_SERVICE_LOCAL_ACCOUNT",
        "windows_service": False,
        "credential_targets": [],
        "secret_refs": [],
        "network_allowlist": [],
        "research_artifact_root": (
            r"C:\ProgramData\DumbMoney\research-worker\artifacts\objects"
        ),
        "write_roots": [
            r"%PROGRAMDATA%\DumbMoney\spool\model\requests",
            r"%PROGRAMDATA%\DumbMoney\research-worker\artifacts\objects",
        ],
        "read_roots": [
            r"%PROGRAMDATA%\DumbMoney\spool\model\responses",
            r"%PROGRAMDATA%\DumbMoney\spool\model\outcomes",
            r"%PROGRAMDATA%\DumbMoney\research-worker\artifacts\objects",
        ],
        "artifact_write_mode": "CONTENT_ADDRESSED_CREATE_ONCE_CONFLICT_FAIL",
        "broker_authority": "NONE",
        "model_access": "TYPED_FILE_SPOOL_ONLY",
        "command_execution": "UNTRUSTED_OFFLINE_ONLY",
        "bundle_handoff": "IMMUTABLE_HASH_PINNED_ONLY",
        "forbidden_host_identities": [
            "DumbMoneyCore",
            "DumbMoneyResearchMesh",
            "DumbMoneyModelGateway",
            "DumbMoneyDummyKalshi",
            "DumbMoneyDopeyRobinhood",
        ],
    }
    assert by_name["DumbMoneyCore"]["config_bindings"] == {
        "core_signer_target": "credential-target:DumbMoney/CoreSigner",
        "role_public_key_bundle": "config-ref:DumbMoney/RolePublicKeys",
        "desktop_read_token_target": "credential-target:DumbMoney/DesktopReadToken",
        "operator_token_target": "credential-target:DumbMoney/OperatorToken",
        "allocator_token_target": "credential-target:DumbMoney/AllocatorToken",
        "dummy_cell_token_target": "credential-target:DumbMoney/DummyCellToken",
        "dopey_cell_token_target": "credential-target:DumbMoney/DopeyCellToken",
        "core_runner_config": (
            "path-ref:C:\\ProgramData\\DumbMoney\\config\\core-runner.v1.json"
        ),
        "core_runner_config_sha256": (
            "config-ref:DumbMoney/SealedReleaseManifest#"
            "installed_artifacts/core-runner-config/sha256"
        ),
        "core_endpoint": "endpoint-ref:DumbMoneyCore",
        "readiness_descriptor": "readiness-ref:DumbMoneyCore",
    }
    assert (
        by_name["DumbMoneyModelGateway"]["config_bindings"][
            "openrouter_credential_target"
        ]
        == "credential-target:DumbMoney/OpenRouterApiKey"
    )
    assert set(by_name["DumbMoneyModelGateway"]["secret_refs"]) == {
        "secret-ref:openrouter-api-key",
        "secret-ref:dumbmoney-model-gateway-ed25519-seed",
        "secret-ref:dumbmoney-model-gateway-client-token",
    }
    assert by_name["DumbMoneyModelGateway"]["config_bindings"] == {
        "openrouter_credential_target": "credential-target:DumbMoney/OpenRouterApiKey",
        "gateway_signer_target": "credential-target:DumbMoney/ModelGatewaySigner",
        "client_bearer_token_target": (
            "credential-target:DumbMoney/ModelGatewayClientToken"
        ),
        "model_gateway_runner_config": (
            "path-ref:C:\\ProgramData\\DumbMoney\\config\\model-gateway-runner.v1.json"
        ),
        "model_gateway_runner_config_sha256": (
            "config-ref:DumbMoney/SealedReleaseManifest#"
            "installed_artifacts/model-gateway-runner-config/sha256"
        ),
        "readiness_descriptor": "readiness-ref:DumbMoneyModelGateway",
    }
    assert set(by_name["DumbMoneyResearchMesh"]["secret_refs"]) == {
        "secret-ref:dumbmoney-research-mesh-ed25519-seed",
        "secret-ref:dumbmoney-research-mesh-allocator-ed25519-seed",
        "secret-ref:dumbmoney-allocator-token",
        "secret-ref:dumbmoney-model-gateway-client-token",
    }
    assert by_name["DumbMoneyResearchMesh"]["config_bindings"] == {
        "research_mesh_signer_target": (
            "credential-target:DumbMoney/ResearchMeshSigner"
        ),
        "allocator_signer_target": (
            "credential-target:DumbMoney/ResearchMeshAllocator"
        ),
        "allocator_token_target": "credential-target:DumbMoney/AllocatorToken",
        "model_gateway_client_token_target": (
            "credential-target:DumbMoney/ModelGatewayClientToken"
        ),
        "research_mesh_runner_config": (
            "path-ref:C:\\ProgramData\\DumbMoney\\config\\research-mesh-runner.v1.json"
        ),
        "research_mesh_runner_config_sha256": (
            "config-ref:DumbMoney/SealedReleaseManifest#"
            "installed_artifacts/research-mesh-runner-config/sha256"
        ),
        "core_endpoint": "endpoint-ref:DumbMoneyCore",
        "core_readiness_descriptor": "readiness-ref:DumbMoneyCore",
        "readiness_descriptor": "readiness-ref:DumbMoneyResearchMesh",
    }
    assert by_name["DumbMoneyDopeyRobinhood"]["config_bindings"] == {
        "core_endpoint": "endpoint-ref:DumbMoneyCore",
        "core_cell_token_target": "credential-target:DumbMoney/DopeyCellToken",
        "robinhood_profile_target": "credential-target:DumbMoney/RobinhoodProfile",
        "dopey_runner_config": (
            "path-ref:C:\\ProgramData\\DumbMoney\\config\\"
            "dopey-robinhood-runner.v1.json"
        ),
        "dopey_runner_config_sha256": (
            "config-ref:DumbMoney/SealedReleaseManifest#"
            "installed_artifacts/dopey-robinhood-runner-config/sha256"
        ),
        "dopey_codex_executable": (
            "config-ref:DumbMoney/SealedReleaseManifest#"
            "installed_artifacts/dopey-codex-executable/install_path"
        ),
        "dopey_codex_executable_sha256": (
            "config-ref:DumbMoney/SealedReleaseManifest#"
            "installed_artifacts/dopey-codex-executable/sha256"
        ),
        "dopey_ollama_executable": (
            "config-ref:DumbMoney/SealedReleaseManifest#"
            "installed_artifacts/dopey-ollama-executable/install_path"
        ),
        "dopey_ollama_executable_sha256": (
            "config-ref:DumbMoney/SealedReleaseManifest#"
            "installed_artifacts/dopey-ollama-executable/sha256"
        ),
        "dopey_ollama_runtime_evidence": (
            "path-ref:C:\\ProgramData\\DumbMoney\\dopey-robinhood\\evidence\\"
            "ollama-runtime-evidence.v1.json"
        ),
        "dopey_ollama_runtime_evidence_sha256": (
            "config-ref:DumbMoney/SealedReleaseManifest#"
            "installed_artifacts/dopey-ollama-runtime-evidence/sha256"
        ),
        "readiness_descriptor": "readiness-ref:DumbMoneyDopeyRobinhood",
    }
    assert set(by_name["DumbMoneyDummyKalshi"]["secret_refs"]) == {
        "secret-ref:kalshi-api-key-id",
        "secret-ref:kalshi-private-key-pem",
        "secret-ref:dumbmoney-dummy-cell-token",
        "secret-ref:dumbmoney-dummy-readiness-ed25519",
    }
    assert by_name["DumbMoneyDummyKalshi"]["config_bindings"] == {
        "core_endpoint": "endpoint-ref:DumbMoneyCore",
        "core_cell_token_target": "credential-target:DumbMoney/DummyCellToken",
        "kalshi_key_id_target": "credential-target:DumbMoney/KalshiApiKeyId",
        "kalshi_private_key_target": (
            "credential-target:DumbMoney/KalshiPrivateKeyPem"
        ),
        "readiness_signing_key_target": (
            "credential-target:DumbMoney/DummyReadinessEd25519"
        ),
        "dummy_kalshi_runner_config": (
            "path-ref:C:\\ProgramData\\DumbMoney\\config\\dummy-kalshi-runner.v1.json"
        ),
        "dummy_kalshi_runner_config_sha256": (
            "config-ref:DumbMoney/SealedReleaseManifest#"
            "installed_artifacts/dummy-kalshi-runner-config/sha256"
        ),
        "readiness_descriptor": "readiness-ref:DumbMoneyDummyKalshi",
    }

    plan = build_windows_service_plan(manifest, release_root=ROOT)
    assert plan["status"] == "PLAN_ONLY"
    assert plan["installation_authority"] is False
    assert plan["service_control_mutations_performed"] == []
    assert plan["broker_actions_performed"] == []
    assert plan["deployment_scope"] == "PRIVATE_LOCAL_WINDOWS"
    assert plan["telemetry_publication"] is False
    assert plan["research_worker_boundary"] == manifest["research_worker_boundary"]
    core_plan = next(
        service for service in plan["services"] if service["name"] == "DumbMoneyCore"
    )
    assert (
        core_plan["config_bindings"]["desktop_read_token_target"]
        == "credential-target:DumbMoney/DesktopReadToken"
    )


def test_service_manifest_rejects_missing_credential_target_ref() -> None:
    manifest = copy.deepcopy(load_services_manifest())
    core = next(
        service
        for service in manifest["services"]
        if service["name"] == "DumbMoneyCore"
    )
    core["secret_refs"].remove("secret-ref:dumbmoney-desktop-read-token")
    del core["config_bindings"]["operator_token_target"]
    core["network_allowlist"].append("https://telemetry.example.invalid")
    core["legacy_launch_command"] = "forbidden"
    manifest["research_worker_boundary"]["credential_targets"].append(
        "credential-target:DumbMoney/AllocatorToken"
    )

    report = validate_services_manifest(manifest)

    assert report.status == "FAIL"
    assert {
        "SERVICE_SECRET_REF_SET_INVALID",
        "SERVICE_CONFIG_BINDING_SET_INVALID",
        "SERVICE_NETWORK_ALLOWLIST_INVALID",
        "SERVICE_FIELDS_FORBIDDEN",
        "SERVICE_RESEARCH_WORKER_BOUNDARY_INVALID",
    } <= {issue.code for issue in report.issues}


def test_winsw_templates_pass_only_credential_target_identifiers() -> None:
    expected_arguments = {
        "DumbMoneyCore": (
            "DUMBMONEY_CORE_COMMAND",
            "{{DUMBMONEY_CORE_ARGUMENTS}}",
            ("--config", "--config-sha256"),
        ),
        "DumbMoneyResearchMesh": (
            "DUMBMONEY_RESEARCH_MESH_COMMAND",
            "{{DUMBMONEY_RESEARCH_MESH_ARGUMENTS}}",
            ("--config", "--config-sha256"),
        ),
        "DumbMoneyModelGateway": (
            "DUMBMONEY_MODEL_GATEWAY_COMMAND",
            "{{DUMBMONEY_MODEL_GATEWAY_ARGUMENTS}}",
            ("--config", "--config-sha256"),
        ),
        "DumbMoneyDummyKalshi": (
            "DUMBMONEY_DUMMY_KALSHI_COMMAND",
            "{{DUMBMONEY_DUMMY_KALSHI_ARGUMENTS}}",
            (
                "--core-endpoint-ref",
                "--core-cell-token-target",
                "--kalshi-key-id-target",
                "--kalshi-private-key-target",
                "--readiness-signing-key-target",
                "--readiness-ref",
                "--config-sha256",
            ),
        ),
        "DumbMoneyDopeyRobinhood": (
            "DUMBMONEY_DOPEY_ROBINHOOD_COMMAND",
            "{{DUMBMONEY_DOPEY_ROBINHOOD_ARGUMENTS}}",
            (
                "--core-endpoint-ref",
                "--core-cell-token-target",
                "--robinhood-profile-target",
                "--readiness-ref",
                "--config-sha256",
            ),
        ),
    }
    release = load_json(DEPLOYMENT / "release-manifest.template.json")
    for service_name, (command_ref, placeholder, flags) in expected_arguments.items():
        root = ET.parse(DEPLOYMENT / "winsw" / f"{service_name}.xml.template").getroot()
        assert root.findtext("arguments", default="") == placeholder
        arguments = release["command_bindings"][command_ref]["arguments"]
        assert all(flag in arguments for flag in flags)
        assert not any("sk-proj-" in argument for argument in arguments)
        assert not any("secret-ref:" in argument for argument in arguments)
    research_root = ET.parse(
        DEPLOYMENT / "winsw" / "DumbMoneyResearchMesh.xml.template"
    ).getroot()
    assert [element.text for element in research_root.findall("depend")] == [
        "DumbMoneyCore",
        "DumbMoneyModelGateway",
    ]
    assert research_root.findtext("description") == (
        "Immutable candidate intake and capital-request relay only."
    )


def test_scripts_contain_no_service_control_or_broker_primitive() -> None:
    script_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "scripts" / "dumbmoney").glob("*.py"))
    ).lower()

    for forbidden in (
        "new-service",
        "start-service",
        "stop-service",
        "create_service(",
        "sc.exe",
        "place_order",
        "cancel_order",
        "agent.robinhood.com",
        "external-api.kalshi.com",
        "git push",
        "gh pr",
        "gh release",
    ):
        assert forbidden not in script_text


def test_readiness_descriptor_accepts_dynamic_loopback_with_verifier() -> None:
    calls: list[tuple[bytes, bytes, str, str]] = []

    def verifier(payload: bytes, signature: bytes, key_id: str, algorithm: str) -> bool:
        calls.append((payload, signature, key_id, algorithm))
        return True

    report = validate_readiness_descriptor(
        _readiness_descriptor(),
        expected_service="DumbMoneyCore",
        verifier=verifier,
        require_parallel_contract=True,
        now_utc=datetime(2026, 7, 26, 12, 0, 30, tzinfo=timezone.utc),
    )

    assert report.status == "PASS"
    assert len(calls) == 1
    assert calls[0][2:] == ("c" * 64, "Ed25519")


def test_readiness_descriptor_rejects_external_endpoint_and_wrong_authority() -> None:
    descriptor = _readiness_descriptor("DumbMoneyDummyKalshi")
    descriptor["body"]["endpoint"]["host"] = "192.0.2.10"
    descriptor["body"]["authority"]["broker"] = "ROBINHOOD"

    report = validate_readiness_descriptor(descriptor, verifier=lambda *_: True)

    assert report.status == "FAIL"
    codes = {issue.code for issue in report.issues}
    assert "READINESS_HOST_NOT_LOOPBACK" in codes
    assert "READINESS_BROKER_AUTHORITY_MISMATCH" in codes


def test_readiness_descriptor_rejects_identity_split_and_nested_extensions() -> None:
    descriptor = _readiness_descriptor("DumbMoneyDummyKalshi")
    descriptor["source_id"] = "DumbMoneyDopeyRobinhood"
    descriptor["body"]["endpoint"]["public_fallback"] = "https://example.invalid"
    descriptor["body"]["authority"]["execution_enabled_override"] = True

    report = validate_readiness_descriptor(descriptor, verifier=lambda *_: True)

    assert report.status == "FAIL"
    assert {
        "READINESS_SOURCE_SERVICE_MISMATCH",
        "READINESS_ENDPOINT_FIELDS_FORBIDDEN",
        "READINESS_AUTHORITY_FIELDS_FORBIDDEN",
    } <= {issue.code for issue in report.issues}


def test_readiness_descriptor_rejects_noncanonical_float_without_crashing() -> None:
    descriptor = _readiness_descriptor()
    descriptor["body"]["health"]["latency_seconds"] = 0.25

    report = validate_readiness_descriptor(descriptor, verifier=lambda *_: True)

    assert report.status == "FAIL"
    assert "READINESS_BODY_NOT_CANONICAL" in {issue.code for issue in report.issues}


def test_readiness_descriptor_verifies_with_fund_ed25519_signer(
    tmp_path: Path,
) -> None:
    signer = Ed25519Signer.generate()
    descriptor = _readiness_descriptor()
    descriptor["signer_key_id"] = signer.key_id
    event_identity = dict(descriptor)
    event_identity.pop("event_id")
    event_identity.pop("signature")
    descriptor["event_id"] = hashlib.sha256(
        canonical_json_bytes(event_identity)
    ).hexdigest()
    unsigned = dict(descriptor)
    unsigned.pop("signature")
    descriptor["signature"] = signer.sign(canonical_json_bytes(unsigned))
    public_key = tmp_path / "readiness-public.key"
    public_key.write_bytes(signer.public_key_bytes)

    report = validate_readiness_descriptor(
        descriptor,
        verifier=make_ed25519_verifier(
            public_key,
            expected_key_id=signer.key_id,
        ),
        require_parallel_contract=True,
        now_utc=datetime(2026, 7, 26, 12, 0, 30, tzinfo=timezone.utc),
    )

    assert report.status == "PASS"


def test_unsigned_fund_lock_is_explicitly_partial() -> None:
    fund_lock = load_json(DEPLOYMENT / "fund.lock.template.json")
    assert set(fund_lock["internal_use_authorization"]["components"]) == {
        "blunder",
        "dummy",
        "dopey",
        "doofus",
        "waterboy",
        "nimrod",
        "dimwit",
    }

    report = validate_fund_lock(
        fund_lock,
        release_root=ROOT,
        repo_roots={},
    )

    assert report.status == "PARTIAL"
    codes = {issue.code for issue in report.issues}
    assert "MISSING_SIGNED_INTERNAL_USE_AUTHORIZATION" in codes
    assert "PARALLEL_CONTRACT_COMPONENT_MISSING" in codes


def test_repository_checks_without_pinned_git_are_partial_and_do_not_execute(
    monkeypatch,
) -> None:
    def unexpected_run(*args, **kwargs):
        raise AssertionError(f"unexpected subprocess execution: {args!r} {kwargs!r}")

    monkeypatch.setattr(release_validation.subprocess, "run", unexpected_run)
    fund_lock = load_json(DEPLOYMENT / "fund.lock.template.json")

    report = validate_fund_lock(
        fund_lock,
        release_root=ROOT,
        repo_roots={"blunder": ROOT},
    )

    assert report.status == "PARTIAL"
    assert "REPOSITORY_GIT_TOOL_NOT_SUPPLIED" in {issue.code for issue in report.issues}
    assert report.facts["git_tool_checked"] is False


def test_repository_checks_reject_non_git_executable_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / "arbitrary.exe"
    executable.write_bytes(b"not-git")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()

    def unexpected_run(*args, **kwargs):
        raise AssertionError(f"unexpected subprocess execution: {args!r} {kwargs!r}")

    monkeypatch.setattr(release_validation.subprocess, "run", unexpected_run)
    report = validate_fund_lock(
        load_json(DEPLOYMENT / "fund.lock.template.json"),
        release_root=ROOT,
        repo_roots={"blunder": ROOT},
        git_executable=executable,
        git_executable_sha256=digest,
    )

    assert report.status == "FAIL"
    assert "REPOSITORY_GIT_EXECUTABLE_NAME_INVALID" in {
        issue.code for issue in report.issues
    }
    assert report.facts["git_tool_checked"] is False


def test_pinned_git_runner_ignores_ambient_path_and_rehashes_before_use(
    tmp_path: Path,
    monkeypatch,
) -> None:
    git_executable = tmp_path / "git.exe"
    git_executable.write_bytes(b"pinned-git-test-binary")
    git_digest = hashlib.sha256(git_executable.read_bytes()).hexdigest()
    hostile_path = tmp_path / "hostile-path"
    hostile_path.mkdir()
    monkeypatch.setenv("PATH", str(hostile_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-reach-child")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return release_validation.subprocess.CompletedProcess(
            command,
            0,
            stdout="0123456789abcdef0123456789abcdef01234567\n",
            stderr="",
        )

    monkeypatch.setattr(release_validation.subprocess, "run", fake_run)

    returncode, output = release_validation._run_git(
        git_executable,
        git_digest,
        ROOT,
        "rev-parse",
        "HEAD",
    )

    assert returncode == 0
    assert output == "0123456789abcdef0123456789abcdef01234567"
    assert len(calls) == 1
    command, options = calls[0]
    assert command == [
        str(git_executable),
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-C",
        str(ROOT),
        "rev-parse",
        "HEAD",
    ]
    child_environment = options["env"]
    assert isinstance(child_environment, dict)
    assert str(hostile_path) not in child_environment["PATH"]
    assert "OPENROUTER_API_KEY" not in child_environment

    git_executable.write_bytes(b"changed-after-validation")
    returncode, output = release_validation._run_git(
        git_executable,
        git_digest,
        ROOT,
        "status",
        "--porcelain",
    )

    assert returncode == -1
    assert "SHA-256 changed" in output
    assert len(calls) == 1


def test_fund_lock_threads_validated_git_pin_to_repository_checks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    git_executable = tmp_path / "git.exe"
    git_executable.write_bytes(b"validated-git-test-binary")
    git_digest = hashlib.sha256(git_executable.read_bytes()).hexdigest()
    calls: list[tuple[Path, str, Path, tuple[str, ...]]] = []

    def fake_run_git(
        executable: Path,
        executable_sha256: str,
        repo_root: Path,
        *args: str,
    ) -> tuple[int, str]:
        calls.append((executable, executable_sha256, repo_root, args))
        if args == ("rev-parse", "HEAD"):
            return 0, "0123456789abcdef0123456789abcdef01234567"
        return 0, ""

    monkeypatch.setattr(release_validation, "_run_git", fake_run_git)
    fund_lock = load_json(DEPLOYMENT / "fund.lock.template.json")

    report = validate_fund_lock(
        fund_lock,
        release_root=ROOT,
        repo_roots={"blunder": ROOT},
        git_executable=git_executable,
        git_executable_sha256=git_digest,
    )

    assert report.facts["git_tool_checked"] is True
    assert calls == [
        (
            git_executable,
            git_digest,
            ROOT,
            ("rev-parse", "HEAD"),
        ),
        (
            git_executable,
            git_digest,
            ROOT,
            ("status", "--porcelain"),
        ),
    ]


def test_fund_lock_requires_exact_interservice_contract_set() -> None:
    for mutation in ("missing", "rebound"):
        fund_lock = copy.deepcopy(load_json(DEPLOYMENT / "fund.lock.template.json"))
        if mutation == "missing":
            fund_lock["contract_assets"] = [
                asset
                for asset in fund_lock["contract_assets"]
                if asset["name"] != "cell-command-page-schema"
            ]
            expected_code = "FUND_LOCK_CONTRACT_ASSET_SET_INVALID"
        else:
            asset = next(
                item
                for item in fund_lock["contract_assets"]
                if item["name"] == "capital-request-schema"
            )
            asset["path"] = "configs/dumbmoney/schemas/capital-envelope.v1.schema.json"
            expected_code = "FUND_LOCK_CONTRACT_ASSET_BINDING_INVALID"

        report = validate_fund_lock(
            fund_lock,
            release_root=ROOT,
            repo_roots={},
        )

        assert report.status == "FAIL"
        assert expected_code in {issue.code for issue in report.issues}


def test_release_template_reports_partial_not_pass() -> None:
    manifest = load_json(DEPLOYMENT / "release-manifest.template.json")
    assert manifest["distribution"] == "PRIVATE_LOCAL_ONLY"
    assert manifest["hosted_control_plane"] is False
    assert manifest["remote_updates_authorized"] is False
    assert manifest["cloud_secret_storage_authorized"] is False
    assert manifest["telemetry_publication"] is False
    report = validate_release_bundle(
        release_manifest_path=DEPLOYMENT / "release-manifest.template.json",
        fund_lock_path=DEPLOYMENT / "fund.lock.template.json",
        release_root=ROOT,
        repo_roots={},
    )

    assert report.status == "PARTIAL"
    codes = {issue.code for issue in report.issues}
    assert "RELEASE_TEMPLATE_NOT_SEALED" in codes
    assert "MISSING_SIGNED_INTERNAL_USE_AUTHORIZATION" in codes
    assert "RELEASE_COMMAND_BINDING_UNRESOLVED" in codes
    assert "CORE_RUNNER_CONFIG_TEMPLATE_NOT_SEALED" in codes
    core_binding = manifest["command_bindings"]["DUMBMONEY_CORE_COMMAND"]
    core_artifact = next(
        artifact
        for artifact in manifest["installed_artifacts"]
        if artifact["name"] == "core-runner-config"
    )
    assert core_binding["arguments"] == [
        "--config",
        core_artifact["install_path"],
        "--config-sha256",
        core_artifact["sha256"],
    ]


def test_release_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    manifest = copy.deepcopy(load_json(DEPLOYMENT / "release-manifest.template.json"))
    manifest["files"][0]["sha256"] = "f" * 64
    altered_manifest = tmp_path / "release-manifest.json"
    altered_manifest.write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    report = validate_release_bundle(
        release_manifest_path=altered_manifest,
        fund_lock_path=DEPLOYMENT / "fund.lock.template.json",
        release_root=ROOT,
        repo_roots={},
    )

    assert report.status == "FAIL"
    assert "RELEASE_FILE_HASH_MISMATCH" in {issue.code for issue in report.issues}


def test_release_and_fund_lock_reject_unknown_authority_fields() -> None:
    manifest = copy.deepcopy(load_json(DEPLOYMENT / "release-manifest.template.json"))
    manifest["broker_actions_authorized_override"] = True
    manifest["command_bindings"]["DUMBMONEY_CORE_COMMAND"] = 42
    manifest["files"][0]["mutable"] = True

    release_report = validate_release_manifest(manifest, release_root=ROOT)

    assert release_report.status == "FAIL"
    assert {
        "RELEASE_MANIFEST_FIELDS_FORBIDDEN",
        "RELEASE_COMMAND_BINDING_INVALID",
        "RELEASE_FILE_FIELDS_FORBIDDEN",
    } <= {issue.code for issue in release_report.issues}

    fund_lock = copy.deepcopy(load_json(DEPLOYMENT / "fund.lock.template.json"))
    fund_lock["public_distribution_override"] = True
    fund_lock["repositories"][0]["floating_branch"] = "main"
    fund_lock["contract_assets"][0]["mutable"] = True
    fund_lock["internal_use_authorization"]["delegate"] = "unknown"

    fund_report = validate_fund_lock(
        fund_lock,
        release_root=ROOT,
        repo_roots={},
    )

    assert fund_report.status == "FAIL"
    assert {
        "FUND_LOCK_FIELDS_FORBIDDEN",
        "REPOSITORY_PIN_FIELDS_FORBIDDEN",
        "CONTRACT_ASSET_FIELDS_FORBIDDEN",
        "INTERNAL_USE_AUTHORIZATION_FIELDS_FORBIDDEN",
    } <= {issue.code for issue in fund_report.issues}


def test_release_rejects_remote_updates_and_cloud_secret_storage() -> None:
    manifest = copy.deepcopy(load_json(DEPLOYMENT / "release-manifest.template.json"))
    manifest["remote_updates_authorized"] = True
    manifest["cloud_secret_storage_authorized"] = True

    report = validate_release_manifest(manifest, release_root=ROOT)

    assert report.status == "FAIL"
    assert {
        "RELEASE_REMOTE_UPDATES_FORBIDDEN",
        "RELEASE_CLOUD_SECRET_STORAGE_FORBIDDEN",
    } <= {issue.code for issue in report.issues}


def test_sealed_runner_config_relation_tamper_fails_closed() -> None:
    expected_evaluator_keys = {"a" * 64: "AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI"}
    config = {
        "release_id": "dumbmoney-test-release",
        "evaluator_public_keys_base64url": {
            "b" * 64: "AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM"
        },
    }
    report = ValidationReport("sealed-runner-config-relations")

    release_validation._validate_runner_config_relations(
        report,
        config=config,
        relations={
            "release_id": "dumbmoney-test-release",
            "evaluator_public_keys_base64url": expected_evaluator_keys,
        },
        artifact_name="dummy-kalshi-runner-config",
        sealed=True,
    )

    assert report.status == "FAIL"
    assert [issue.code for issue in report.issues] == [
        "RELEASE_RUNNER_CONFIG_RELATION_MISMATCH"
    ]


def _verified_ollama_evidence() -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    executable = {
        "install_path": (
            r"C:\Program Files\DumbMoney\releases\test-release"
            r"\tools\ollama\ollama.exe"
        ),
        "sha256": "a" * 64,
        "bytes": 4096,
    }
    evidence_record = {"sha256": "b" * 64}
    model_manifest = [
        {
            "path": "blobs/sha256-cccccccc",
            "sha256": "c" * 64,
            "bytes": 2048,
        }
    ]
    acl_sddl = "D:P(A;;FA;;;S-1-5-21-1-2-3-1001)"
    evidence = copy.deepcopy(
        load_json(DEPLOYMENT / "ollama-runtime-evidence.v1.template.json")
    )
    evidence.update(
        {
            "state": "VERIFIED",
            "evidence_id": "ollama-installation-test-001",
            "release_id": "test-release",
            "verified_at": "2026-07-26T12:00:00Z",
            "verified_by": "DumbMoneyReleaseReviewer",
            "verification_status": "VERIFIED",
            "process_identity_sid": "S-1-5-21-1-2-3-1001",
            "model_store_owner_sid": "S-1-5-21-1-2-3-1001",
            "executable_path": executable["install_path"],
            "executable_sha256": executable["sha256"],
            "executable_bytes": executable["bytes"],
            "listener_owner_executable_path": executable["install_path"],
            "listener_owner_executable_sha256": executable["sha256"],
            "model_tag": "qwen3:8b",
            "model_digest": "d" * 64,
            "model_store_acl_sddl": acl_sddl,
            "model_store_acl_sha256": hashlib.sha256(
                acl_sddl.encode("utf-8")
            ).hexdigest(),
            "model_store_manifest": model_manifest,
            "model_store_manifest_sha256": hashlib.sha256(
                canonical_json_bytes(model_manifest)
            ).hexdigest(),
        }
    )
    dopey_config = {
        "ollama_executable_path": executable["install_path"],
        "ollama_executable_sha256": executable["sha256"],
        "codex_local_provider": "ollama",
        "codex_local_model": evidence["model_tag"],
        "codex_local_model_digest": evidence["model_digest"],
        "ollama_runtime_evidence_sha256": evidence_record["sha256"],
        "ollama_process_identity_sid": evidence["process_identity_sid"],
    }
    return evidence, executable, evidence_record, dopey_config


def test_static_ollama_installation_attestation_validates() -> None:
    evidence, executable, evidence_record, dopey_config = _verified_ollama_evidence()

    report = validate_ollama_runtime_evidence(
        evidence,
        release_state="SEALED",
        release_id="test-release",
        executable_record=executable,
        evidence_record=evidence_record,
        dopey_config=dopey_config,
    )

    assert report.status == "PASS"


def test_ollama_attestation_rejects_tamper_and_path_escape() -> None:
    evidence, executable, evidence_record, dopey_config = _verified_ollama_evidence()
    evidence["listener_owner_executable_sha256"] = "e" * 64
    evidence["model_store_manifest"][0]["path"] = "../outside-model"
    evidence["model_store_acl_sddl"] += "(A;;FR;;;WD)"
    evidence["model_store_acl_sha256"] = hashlib.sha256(
        evidence["model_store_acl_sddl"].encode("utf-8")
    ).hexdigest()

    report = validate_ollama_runtime_evidence(
        evidence,
        release_state="SEALED",
        release_id="test-release",
        executable_record=executable,
        evidence_record=evidence_record,
        dopey_config=dopey_config,
    )

    assert report.status == "FAIL"
    assert {
        "OLLAMA_EVIDENCE_RELEASE_RELATION_MISMATCH",
        "OLLAMA_EVIDENCE_MODEL_PATH_INVALID",
        "OLLAMA_EVIDENCE_ACL_BROAD_TRUSTEE_FORBIDDEN",
    } <= {issue.code for issue in report.issues}


def test_release_rejects_inherited_dopey_live_launcher() -> None:
    manifest = copy.deepcopy(load_json(DEPLOYMENT / "release-manifest.template.json"))
    binding = manifest["command_bindings"]["DUMBMONEY_DOPEY_ROBINHOOD_COMMAND"]
    binding["executable"] = "runners/DumbMoneyDopeyRobinhood.exe"
    binding["sha256"] = "a" * 64
    binding["bytes"] = 1024
    binding["arguments"] = [
        "-m",
        "dopey.operator_live_branch",
        "--execute",
        r"--root=C:\Users\CH\dopey",
    ]

    report = validate_release_manifest(manifest, release_root=ROOT)

    assert report.status == "FAIL"
    assert "RELEASE_LEGACY_DOPEY_LAUNCH_FORBIDDEN" in {
        issue.code for issue in report.issues
    }


def test_release_rejects_calc_and_outside_root_command_bindings() -> None:
    for executable in (
        r"C:\Windows\System32\calc.exe",
        "../DumbMoneyCore.exe",
    ):
        manifest = copy.deepcopy(
            load_json(DEPLOYMENT / "release-manifest.template.json")
        )
        binding = manifest["command_bindings"]["DUMBMONEY_CORE_COMMAND"]
        binding["executable"] = executable
        binding["sha256"] = "a" * 64
        binding["bytes"] = 1024
        binding["arguments"] = ["--untracked"]

        report = validate_release_manifest(manifest, release_root=ROOT)

        assert report.status == "FAIL"
        assert "RELEASE_COMMAND_BINDING_PATH_INVALID" in {
            issue.code for issue in report.issues
        }


def test_release_rejects_non_pe_executable_and_legacy_sidecar(
    tmp_path: Path,
) -> None:
    runner_directory = tmp_path / "runners"
    runner_directory.mkdir()
    runner = runner_directory / "DumbMoneyCore.exe"
    runner.write_bytes(b"not-a-windows-pe")
    (runner_directory / "DumbMoneyCore.cmd").write_text(
        "@echo off\r\n",
        encoding="utf-8",
    )
    manifest = copy.deepcopy(load_json(DEPLOYMENT / "release-manifest.template.json"))
    manifest["files"].append(
        {
            "path": "runners/DumbMoneyCore.exe",
            "sha256": hashlib.sha256(runner.read_bytes()).hexdigest(),
            "bytes": runner.stat().st_size,
            "component": "blunder",
        }
    )

    report = validate_release_manifest(manifest, release_root=tmp_path)

    assert report.status == "FAIL"
    assert {
        "RELEASE_EXECUTABLE_NOT_PE",
        "RELEASE_EXECUTABLE_LEGACY_SIDECAR_FORBIDDEN",
        "RELEASE_EXECUTABLE_PROVENANCE_MISSING",
    } <= {issue.code for issue in report.issues}


def test_release_rejects_command_inventory_tamper() -> None:
    manifest = copy.deepcopy(load_json(DEPLOYMENT / "release-manifest.template.json"))
    binding = manifest["command_bindings"]["DUMBMONEY_CORE_COMMAND"]
    binding["executable"] = "deployment/dumbmoney/services.v1.json"
    binding["sha256"] = "f" * 64
    binding["bytes"] = 1
    binding["component"] = "blunder"

    report = validate_release_manifest(manifest, release_root=ROOT)

    assert report.status == "FAIL"
    assert {
        "RELEASE_COMMAND_EXECUTABLE_NAME_INVALID",
        "RELEASE_COMMAND_ARTIFACT_DIGEST_MISMATCH",
        "RELEASE_COMMAND_ARTIFACT_SIZE_MISMATCH",
        "RELEASE_EXECUTABLE_PROVENANCE_MISSING",
    } <= {issue.code for issue in report.issues}


def test_release_rejects_secret_broad_path_and_shell_arguments() -> None:
    manifest = copy.deepcopy(load_json(DEPLOYMENT / "release-manifest.template.json"))
    binding = manifest["command_bindings"]["DUMBMONEY_CORE_COMMAND"]
    binding["executable"] = "runners/DumbMoneyCore.exe"
    binding["sha256"] = "a" * 64
    binding["bytes"] = 1024
    binding["arguments"] = [
        r"--config=C:\Users\CH\config.json",
        "--api-key=sk-proj-forbidden",
        "--mode=serve & calc.exe",
    ]

    report = validate_release_manifest(manifest, release_root=ROOT)

    assert report.status == "FAIL"
    assert {
        "RELEASE_COMMAND_ARGUMENT_SECRET_FORBIDDEN",
        "RELEASE_COMMAND_ARGUMENT_BROAD_PATH_FORBIDDEN",
        "RELEASE_COMMAND_ARGUMENT_SHELL_SYNTAX_FORBIDDEN",
    } <= {issue.code for issue in report.issues}


def test_release_rejects_missing_target_pair_and_static_live_mode() -> None:
    manifest = copy.deepcopy(load_json(DEPLOYMENT / "release-manifest.template.json"))
    binding = manifest["command_bindings"]["DUMBMONEY_DUMMY_KALSHI_COMMAND"]
    arguments = binding["arguments"]
    index = arguments.index("--core-cell-token-target")
    del arguments[index : index + 2]
    arguments.append("--execute")

    report = validate_release_manifest(manifest, release_root=ROOT)

    assert report.status == "FAIL"
    assert {
        "RELEASE_COMMAND_REQUIRED_ARGUMENTS_MISSING",
        "RELEASE_COMMAND_ARGUMENT_SET_INVALID",
        "RELEASE_COMMAND_STATIC_LIVE_MODE_FORBIDDEN",
    } <= {issue.code for issue in report.issues}


def test_release_rejects_unknown_or_reordered_command_arguments() -> None:
    for mutation in ("extra", "reordered"):
        manifest = copy.deepcopy(
            load_json(DEPLOYMENT / "release-manifest.template.json")
        )
        arguments = manifest["command_bindings"]["DUMBMONEY_RESEARCH_MESH_COMMAND"][
            "arguments"
        ]
        if mutation == "extra":
            arguments.extend(["--extra-benign", "true"])
        else:
            arguments[0:4] = arguments[2:4] + arguments[0:2]

        report = validate_release_manifest(manifest, release_root=ROOT)

        assert report.status == "FAIL"
        assert "RELEASE_COMMAND_ARGUMENT_SET_INVALID" in {
            issue.code for issue in report.issues
        }


def test_core_config_digest_binding_and_installed_artifact_are_fail_closed() -> None:
    manifest = copy.deepcopy(load_json(DEPLOYMENT / "release-manifest.template.json"))
    core_binding = manifest["command_bindings"]["DUMBMONEY_CORE_COMMAND"]
    core_binding["arguments"][-1] = "f" * 64

    report = validate_release_manifest(manifest, release_root=ROOT)

    assert report.status == "FAIL"
    assert {
        "RELEASE_COMMAND_ARGUMENT_SET_INVALID",
        "RELEASE_CORE_CONFIG_DIGEST_ARGUMENT_MISMATCH",
    } <= {issue.code for issue in report.issues}


def test_all_public_runner_config_digest_bindings_are_fail_closed() -> None:
    cases = {
        "DUMBMONEY_RESEARCH_MESH_COMMAND": (
            "RELEASE_RESEARCH_MESH_CONFIG_DIGEST_ARGUMENT_MISMATCH"
        ),
        "DUMBMONEY_MODEL_GATEWAY_COMMAND": (
            "RELEASE_MODEL_GATEWAY_CONFIG_DIGEST_ARGUMENT_MISMATCH"
        ),
        "DUMBMONEY_DUMMY_KALSHI_COMMAND": (
            "RELEASE_DUMMY_CONFIG_DIGEST_ARGUMENT_MISMATCH"
        ),
        "DUMBMONEY_DOPEY_ROBINHOOD_COMMAND": (
            "RELEASE_DOPEY_CONFIG_DIGEST_ARGUMENT_MISMATCH"
        ),
    }
    for command_ref, relation_code in cases.items():
        manifest = copy.deepcopy(
            load_json(DEPLOYMENT / "release-manifest.template.json")
        )
        manifest["command_bindings"][command_ref]["arguments"][-1] = "f" * 64

        report = validate_release_manifest(manifest, release_root=ROOT)

        assert report.status == "FAIL"
        assert {
            "RELEASE_COMMAND_ARGUMENT_SET_INVALID",
            relation_code,
        } <= {issue.code for issue in report.issues}


def test_fund_lock_rejects_remote_repository_sources() -> None:
    fund_lock = copy.deepcopy(load_json(DEPLOYMENT / "fund.lock.template.json"))
    fund_lock["repositories"][0]["url"] = "https://github.com/example/public-source.git"

    report = validate_fund_lock(
        fund_lock,
        release_root=ROOT,
        repo_roots={},
    )

    assert report.status == "FAIL"
    assert "REPOSITORY_URL_INVALID" in {issue.code for issue in report.issues}


def test_installed_artifact_must_match_file_inventory() -> None:
    manifest = copy.deepcopy(load_json(DEPLOYMENT / "release-manifest.template.json"))
    role_bundle = next(
        artifact
        for artifact in manifest["installed_artifacts"]
        if artifact["name"] == "role-public-key-bundle"
    )
    role_bundle["bytes"] += 1

    report = validate_release_manifest(manifest, release_root=ROOT)

    assert report.status == "FAIL"
    assert {
        "RELEASE_INSTALLED_ARTIFACT_SIZE_MISMATCH",
        "RELEASE_INSTALLED_ARTIFACT_FILE_SIZE_MISMATCH",
    } <= {issue.code for issue in report.issues}


def test_core_runner_config_template_and_role_bundle_validate_partial() -> None:
    config = load_json(DEPLOYMENT / "core-runner.v1.template.json")
    role_bundle = load_json(DEPLOYMENT / "role-public-keys.v1.template.json")

    report = validate_core_runner_config(
        config,
        state="TEMPLATE",
        role_bundle=role_bundle,
    )

    assert report.status == "PARTIAL"
    assert not any(issue.severity == "ERROR" for issue in report.issues)
    assert {
        "CORE_RUNNER_CONFIG_TEMPLATE_NOT_SEALED",
        "CORE_RUNNER_RELEASE_ID_UNRESOLVED",
        "UNRESOLVED_DIGEST",
    } <= {issue.code for issue in report.issues}


def test_core_runner_config_rejects_cross_role_key_reuse() -> None:
    config = copy.deepcopy(load_json(DEPLOYMENT / "core-runner.v1.template.json"))
    role_bundle = copy.deepcopy(
        load_json(DEPLOYMENT / "role-public-keys.v1.template.json")
    )
    duplicate = config["role_public_keys_base64url"]["operator"][0]
    config["role_public_keys_base64url"]["allocator"] = [duplicate]
    role_bundle["role_public_keys_base64url"]["allocator"] = [duplicate]

    report = validate_core_runner_config(
        config,
        state="TEMPLATE",
        role_bundle=role_bundle,
    )

    assert report.status == "FAIL"
    assert "CORE_RUNNER_ROLE_KEYS_NOT_DISJOINT" in {
        issue.code for issue in report.issues
    }


def test_core_runner_config_cannot_seal_tracked_template_role_keys() -> None:
    config = load_json(DEPLOYMENT / "core-runner.v1.template.json")
    role_bundle = load_json(DEPLOYMENT / "role-public-keys.v1.template.json")
    role_bundle["state"] = "SEALED"

    report = validate_core_runner_config(
        config,
        state="SEALED",
        role_bundle=role_bundle,
    )

    assert report.status == "FAIL"
    assert "CORE_RUNNER_TEMPLATE_PUBLIC_KEY_FORBIDDEN" in {
        issue.code for issue in report.issues
    }
