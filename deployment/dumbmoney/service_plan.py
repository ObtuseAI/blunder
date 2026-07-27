from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import ValidationReport, load_json, safe_relative_path
from .readiness import EXPECTED_SERVICES


EXPECTED_BROKER_AUTHORITY = {
    "DumbMoneyCore": "NONE",
    "DumbMoneyResearchMesh": "NONE",
    "DumbMoneyModelGateway": "NONE",
    "DumbMoneyDummyKalshi": "KALSHI",
    "DumbMoneyDopeyRobinhood": "ROBINHOOD",
}
EXPECTED_ROLES = {
    "DumbMoneyCore": "control-ledger-capital-governor",
    "DumbMoneyResearchMesh": "candidate-intake-and-capital-request-relay",
    "DumbMoneyModelGateway": "openrouter-budget-and-data-classification-gateway",
    "DumbMoneyDummyKalshi": "sovereign-kalshi-venue-cell",
    "DumbMoneyDopeyRobinhood": "sovereign-robinhood-venue-cell",
}
EXPECTED_RESEARCH_WORKER_BOUNDARY = {
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
EXPECTED_COMMAND_REFS = {
    "DumbMoneyCore": "DUMBMONEY_CORE_COMMAND",
    "DumbMoneyResearchMesh": "DUMBMONEY_RESEARCH_MESH_COMMAND",
    "DumbMoneyModelGateway": "DUMBMONEY_MODEL_GATEWAY_COMMAND",
    "DumbMoneyDummyKalshi": "DUMBMONEY_DUMMY_KALSHI_COMMAND",
    "DumbMoneyDopeyRobinhood": "DUMBMONEY_DOPEY_ROBINHOOD_COMMAND",
}
EXPECTED_DEPENDENCIES = {
    "DumbMoneyCore": [],
    "DumbMoneyResearchMesh": ["DumbMoneyCore", "DumbMoneyModelGateway"],
    "DumbMoneyModelGateway": ["DumbMoneyCore"],
    "DumbMoneyDummyKalshi": ["DumbMoneyCore"],
    "DumbMoneyDopeyRobinhood": ["DumbMoneyCore"],
}
EXPECTED_NETWORK_ALLOWLIST = {
    "DumbMoneyCore": ["loopback"],
    "DumbMoneyResearchMesh": ["loopback:core", "loopback:model-gateway"],
    "DumbMoneyModelGateway": ["loopback", "https://openrouter.ai"],
    "DumbMoneyDummyKalshi": ["loopback:core", "https://external-api.kalshi.com"],
    "DumbMoneyDopeyRobinhood": [
        "loopback:core",
        "loopback:ollama",
        "https://agent.robinhood.com",
    ],
}
EXPECTED_WRITE_ROOTS = {
    "DumbMoneyCore": [
        r"%PROGRAMDATA%\DumbMoney\core",
        r"%PROGRAMDATA%\DumbMoney\readiness",
    ],
    "DumbMoneyResearchMesh": [
        r"%PROGRAMDATA%\DumbMoney\research",
        r"%PROGRAMDATA%\DumbMoney\spool\model",
        r"%PROGRAMDATA%\DumbMoney\readiness",
    ],
    "DumbMoneyModelGateway": [
        r"%PROGRAMDATA%\DumbMoney\model-gateway",
        r"%PROGRAMDATA%\DumbMoney\readiness",
    ],
    "DumbMoneyDummyKalshi": [
        r"%PROGRAMDATA%\DumbMoney\dummy-kalshi",
        r"%PROGRAMDATA%\DumbMoney\readiness",
    ],
    "DumbMoneyDopeyRobinhood": [
        r"%PROGRAMDATA%\DumbMoney\dopey-robinhood",
        r"%PROGRAMDATA%\DumbMoney\readiness",
    ],
}
EXPECTED_SECRET_REFS = {
    "DumbMoneyCore": {
        "secret-ref:dumbmoney-core-ed25519-seed",
        "secret-ref:dumbmoney-desktop-read-token",
        "secret-ref:dumbmoney-operator-token",
        "secret-ref:dumbmoney-allocator-token",
        "secret-ref:dumbmoney-dummy-cell-token",
        "secret-ref:dumbmoney-dopey-cell-token",
    },
    "DumbMoneyResearchMesh": {
        "secret-ref:dumbmoney-research-mesh-ed25519-seed",
        "secret-ref:dumbmoney-research-mesh-allocator-ed25519-seed",
        "secret-ref:dumbmoney-allocator-token",
        "secret-ref:dumbmoney-model-gateway-client-token",
    },
    "DumbMoneyModelGateway": {
        "secret-ref:openrouter-api-key",
        "secret-ref:dumbmoney-model-gateway-ed25519-seed",
        "secret-ref:dumbmoney-model-gateway-client-token",
    },
    "DumbMoneyDummyKalshi": {
        "secret-ref:kalshi-api-key-id",
        "secret-ref:kalshi-private-key-pem",
        "secret-ref:dumbmoney-dummy-cell-token",
        "secret-ref:dumbmoney-dummy-readiness-ed25519",
    },
    "DumbMoneyDopeyRobinhood": {
        "secret-ref:robinhood-agentic-mcp-profile",
        "secret-ref:dumbmoney-dopey-cell-token",
    },
}
EXPECTED_CONFIG_BINDINGS = {
    "DumbMoneyCore": {
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
    },
    "DumbMoneyResearchMesh": {
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
    },
    "DumbMoneyModelGateway": {
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
    },
    "DumbMoneyDummyKalshi": {
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
    },
    "DumbMoneyDopeyRobinhood": {
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
    },
}


def load_services_manifest(path: Path | None = None) -> dict[str, Any]:
    source = path or Path(__file__).resolve().parent / "services.v1.json"
    return load_json(source)


def validate_services_manifest(manifest: dict[str, Any]) -> ValidationReport:
    report = ValidationReport("windows-services-manifest")
    allowed_manifest_fields = {
        "schema_version",
        "product",
        "installation_authority",
        "deployment_scope",
        "telemetry_publication",
        "readiness_schema",
        "readiness_ttl_seconds",
        "research_worker_boundary",
        "services",
    }
    extra_manifest_fields = sorted(set(manifest) - allowed_manifest_fields)
    if extra_manifest_fields:
        report.error(
            "SERVICE_MANIFEST_FIELDS_FORBIDDEN",
            f"unexpected service-manifest fields: {extra_manifest_fields}",
            "manifest",
        )
    if manifest.get("schema_version") != "dumbmoney.windows-services.v1":
        report.error(
            "SERVICE_SCHEMA_UNSUPPORTED",
            "schema_version must be dumbmoney.windows-services.v1",
            "schema_version",
        )
    if manifest.get("product") != "DumbMoney":
        report.error(
            "SERVICE_PRODUCT_INVALID",
            "product must be DumbMoney",
            "product",
        )
    if manifest.get("installation_authority") != "PLAN_ONLY":
        report.error(
            "SERVICE_INSTALLATION_AUTHORITY_FORBIDDEN",
            "the tracked service manifest must remain PLAN_ONLY",
            "installation_authority",
        )
    if manifest.get("deployment_scope") != "PRIVATE_LOCAL_WINDOWS":
        report.error(
            "SERVICE_DEPLOYMENT_SCOPE_INVALID",
            "tracked services must remain private and local to Windows",
            "deployment_scope",
        )
    if manifest.get("telemetry_publication") != "DISABLED":
        report.error(
            "SERVICE_TELEMETRY_PUBLICATION_FORBIDDEN",
            "tracked services may not publish telemetry",
            "telemetry_publication",
        )
    if manifest.get("research_worker_boundary") != EXPECTED_RESEARCH_WORKER_BOUNDARY:
        report.error(
            "SERVICE_RESEARCH_WORKER_BOUNDARY_INVALID",
            (
                "Doofus command execution requires the exact non-service, "
                "credential-free, network-free, hash-spool/object-store "
                "DumbMoneyResearchWorker boundary"
            ),
            "research_worker_boundary",
        )
    try:
        safe_relative_path(manifest.get("readiness_schema"))
    except ValueError as exc:
        report.error(
            "SERVICE_READINESS_SCHEMA_PATH_INVALID",
            str(exc),
            "readiness_schema",
        )
    readiness_ttl = manifest.get("readiness_ttl_seconds")
    if (
        not isinstance(readiness_ttl, int)
        or isinstance(readiness_ttl, bool)
        or not 1 <= readiness_ttl <= 120
    ):
        report.error(
            "SERVICE_READINESS_TTL_INVALID",
            "readiness_ttl_seconds must be an integer from 1 through 120",
            "readiness_ttl_seconds",
        )

    services = manifest.get("services")
    if not isinstance(services, list):
        report.error(
            "SERVICES_MISSING",
            "services must be an array",
            "services",
        )
        return report
    names = [service.get("name") for service in services if isinstance(service, dict)]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        report.error(
            "SERVICE_NAMES_DUPLICATED",
            f"duplicate service names: {', '.join(duplicates)}",
            "services",
        )
    actual = set(names)
    if actual != EXPECTED_SERVICES:
        missing = sorted(EXPECTED_SERVICES - actual)
        extra = sorted(actual - EXPECTED_SERVICES)
        report.error(
            "SERVICE_SET_INVALID",
            f"missing={missing}; extra={extra}",
            "services",
        )

    by_name = {
        service.get("name"): service
        for service in services
        if isinstance(service, dict) and isinstance(service.get("name"), str)
    }
    command_refs = [
        service.get("command_ref")
        for service in services
        if isinstance(service, dict) and isinstance(service.get("command_ref"), str)
    ]
    if len(command_refs) != len(set(command_refs)):
        report.error(
            "SERVICE_COMMAND_REFS_DUPLICATED",
            "each service must use a distinct indirect command reference",
            "services",
        )
    for name in sorted(EXPECTED_SERVICES):
        service = by_name.get(name)
        if service is None:
            continue
        prefix = f"services[{name}]"
        allowed_service_fields = {
            "name",
            "display_name",
            "role",
            "command_ref",
            "dependencies",
            "broker_authority",
            "secret_refs",
            "config_bindings",
            "network_allowlist",
            "write_roots",
            "readiness_descriptor",
        }
        extra_service_fields = sorted(set(service) - allowed_service_fields)
        if extra_service_fields:
            report.error(
                "SERVICE_FIELDS_FORBIDDEN",
                f"unexpected service fields: {extra_service_fields}",
                prefix,
            )
        if service.get("broker_authority") != EXPECTED_BROKER_AUTHORITY[name]:
            report.error(
                "SERVICE_BROKER_AUTHORITY_INVALID",
                f"{name} must declare {EXPECTED_BROKER_AUTHORITY[name]}",
                f"{prefix}.broker_authority",
            )
        if service.get("role") != EXPECTED_ROLES[name]:
            report.error(
                "SERVICE_ROLE_INVALID",
                f"{name} role must be {EXPECTED_ROLES[name]}",
                f"{prefix}.role",
            )
        command_ref = service.get("command_ref")
        if command_ref != EXPECTED_COMMAND_REFS[name]:
            report.error(
                "SERVICE_COMMAND_REF_INVALID",
                f"{name} must use {EXPECTED_COMMAND_REFS[name]}",
                f"{prefix}.command_ref",
            )
        if "command" in service or "arguments" in service:
            report.error(
                "SERVICE_INLINE_COMMAND_FORBIDDEN",
                "tracked service templates may not embed executable commands",
                prefix,
            )
        dependencies = service.get("dependencies")
        if not isinstance(dependencies, list) or any(
            not isinstance(dependency, str) or dependency not in EXPECTED_SERVICES
            for dependency in dependencies
        ):
            report.error(
                "SERVICE_DEPENDENCY_INVALID",
                "dependencies must name only DumbMoney services",
                f"{prefix}.dependencies",
            )
        elif len(dependencies) != len(set(dependencies)):
            report.error(
                "SERVICE_DEPENDENCY_DUPLICATED",
                "service dependencies must be unique",
                f"{prefix}.dependencies",
            )
        elif dependencies != EXPECTED_DEPENDENCIES[name]:
            report.error(
                "SERVICE_DEPENDENCY_SET_INVALID",
                f"{name} dependencies must be {EXPECTED_DEPENDENCIES[name]}",
                f"{prefix}.dependencies",
            )
        if service.get("network_allowlist") != EXPECTED_NETWORK_ALLOWLIST[name]:
            report.error(
                "SERVICE_NETWORK_ALLOWLIST_INVALID",
                f"{name} network_allowlist must be exact",
                f"{prefix}.network_allowlist",
            )
        if service.get("write_roots") != EXPECTED_WRITE_ROOTS[name]:
            report.error(
                "SERVICE_WRITE_ROOTS_INVALID",
                f"{name} write_roots must be exact",
                f"{prefix}.write_roots",
            )
        expected_readiness = rf"%PROGRAMDATA%\DumbMoney\readiness\{name}.json"
        if service.get("readiness_descriptor") != expected_readiness:
            report.error(
                "SERVICE_READINESS_DESCRIPTOR_INVALID",
                f"{name} readiness descriptor must use its exact ProgramData path",
                f"{prefix}.readiness_descriptor",
            )
        secret_refs = service.get("secret_refs", [])
        if not isinstance(secret_refs, list) or any(
            not isinstance(ref, str) or not ref.startswith("secret-ref:")
            for ref in secret_refs
        ):
            report.error(
                "SERVICE_SECRET_REF_INVALID",
                "secret_refs may contain identifiers only and must use secret-ref:",
                f"{prefix}.secret_refs",
            )
        elif set(secret_refs) != EXPECTED_SECRET_REFS[name]:
            report.error(
                "SERVICE_SECRET_REF_SET_INVALID",
                (
                    f"{name} secret references must be exact; "
                    f"missing={sorted(EXPECTED_SECRET_REFS[name] - set(secret_refs))}; "
                    f"extra={sorted(set(secret_refs) - EXPECTED_SECRET_REFS[name])}"
                ),
                f"{prefix}.secret_refs",
            )
        config_bindings = service.get("config_bindings")
        if not isinstance(config_bindings, dict) or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not value.startswith(
                (
                    "credential-target:",
                    "config-ref:",
                    "endpoint-ref:",
                    "readiness-ref:",
                    "path-ref:",
                )
            )
            for key, value in (
                config_bindings.items() if isinstance(config_bindings, dict) else []
            )
        ):
            report.error(
                "SERVICE_CONFIG_BINDINGS_INVALID",
                "config_bindings must contain only non-secret target identifiers",
                f"{prefix}.config_bindings",
            )
        elif config_bindings != EXPECTED_CONFIG_BINDINGS[name]:
            report.error(
                "SERVICE_CONFIG_BINDING_SET_INVALID",
                (
                    f"{name} config bindings must be exact; "
                    f"missing={sorted(set(EXPECTED_CONFIG_BINDINGS[name]) - set(config_bindings))}; "
                    f"extra={sorted(set(config_bindings) - set(EXPECTED_CONFIG_BINDINGS[name]))}"
                ),
                f"{prefix}.config_bindings",
            )
        serialized = repr(service).lower()
        if any(token in serialized for token in ("password=", "api_key=", "bearer ")):
            report.error(
                "SERVICE_INLINE_SECRET_FORBIDDEN",
                "service definition appears to contain inline secret material",
                prefix,
            )

    report.facts["service_names"] = sorted(actual)
    report.facts["installation_authority"] = manifest.get("installation_authority")
    return report


def build_windows_service_plan(
    manifest: dict[str, Any],
    *,
    release_root: Path,
    program_data_root: str = r"C:\ProgramData\DumbMoney",
) -> dict[str, Any]:
    validation = validate_services_manifest(manifest)
    services: list[dict[str, Any]] = []
    for service in manifest.get("services", []):
        if not isinstance(service, dict):
            continue
        services.append(
            {
                "name": service.get("name"),
                "display_name": service.get("display_name"),
                "command_binding_required": service.get("command_ref"),
                "dependencies": service.get("dependencies", []),
                "broker_authority": service.get("broker_authority"),
                "secret_refs_required": service.get("secret_refs", []),
                "config_bindings": service.get("config_bindings", {}),
                "service_identity": rf"NT SERVICE\{service.get('name')}",
                "winsw_template": str(
                    release_root
                    / "deployment"
                    / "dumbmoney"
                    / "winsw"
                    / f"{service.get('name')}.xml.template"
                ),
                "readiness_descriptor": (
                    rf"{program_data_root}\readiness\{service.get('name')}.json"
                ),
            }
        )
    return {
        "schema_version": "dumbmoney.windows-service-plan.v1",
        "status": "PLAN_ONLY",
        "validation_status": validation.status,
        "installation_authority": False,
        "deployment_scope": "PRIVATE_LOCAL_WINDOWS",
        "telemetry_publication": False,
        "research_worker_boundary": manifest.get("research_worker_boundary"),
        "service_control_mutations_performed": [],
        "broker_actions_performed": [],
        "release_root": str(release_root.resolve()),
        "program_data_root": program_data_root,
        "services": services,
        "operator_sequence": [
            "resolve and validate the immutable release and signed internal-use grant",
            "bind each indirect command reference to a hash-pinned release executable",
            "render WinSW files into a new immutable staging directory",
            "review service identities, NTFS ACLs, dependencies, and loopback policy",
            (
                "prove Doofus workers run only as the non-service "
                "DumbMoneyResearchWorker with no credentials or network"
            ),
            (
                "prove the Model Gateway loopback port owner, TokenUser SID, "
                "pinned image hash, and service-SID/program firewall rule"
            ),
            (
                "verify the static Ollama installation attestation and keep "
                "DumbMoneyOllama a credential-free non-service local account"
            ),
            (
                "require Dopey to revalidate the Ollama PID, listener owner "
                "SID, image hash, and model digest before every Codex spawn"
            ),
            "perform installation only through the separately reviewed operator runbook",
        ],
        "issues": [issue.as_dict() for issue in validation.issues],
    }
