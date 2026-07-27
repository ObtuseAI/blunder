"""Private Windows service runner for the DumbMoney control plane.

The only command-line input is an absolute path to a public configuration
file. Private signing material and bearer tokens are read from Windows
Credential Manager by opaque target name. No broker or model API is called.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib
import json
import os
import signal
import sys
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, Literal, Mapping, Protocol, Sequence, cast

from blunder.fund.bootstrap import (
    FundApplication,
    RolePublicKeys,
    build_fund_application,
    make_loopback_server,
)
from blunder.fund.canonical import (
    canonical_json_bytes,
    canonical_sha256,
    loads_strict_json,
    require_digest,
    require_identifier,
)
from blunder.fund.contracts import ReadinessDescriptorV1, SignedEnvelopeV1
from blunder.fund.crypto import Ed25519Signer, decode_base64url
from blunder.fund.policy import RiskPolicyV1
from blunder.fund.runtime import DeploymentAuthorityBindings


CONFIG_SCHEMA = "dumbmoney.core-runner-config.v1"
READINESS_SOURCE: Literal["DumbMoneyCore"] = "DumbMoneyCore"
DEFAULT_CONFIG_PATH = Path(
    r"C:\ProgramData\DumbMoney\config\core-runner.v1.json"
)


class CredentialProviderError(RuntimeError):
    """An OS-protected credential could not be loaded safely."""


class CredentialProvider(Protocol):
    def read_bytes(self, target: str) -> bytes:
        """Read one generic credential blob without logging it."""


@dataclass
class DataRootLease:
    """Process-lifetime exclusive lock preventing two Core writers."""

    path: Path
    handle: BinaryIO

    @classmethod
    def acquire(cls, data_root: Path) -> "DataRootLease":
        data_root.mkdir(parents=True, exist_ok=True)
        path = data_root / "core.instance.lock"
        handle = path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(
                    handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
        except OSError as exc:
            handle.close()
            raise RuntimeError(
                "another DumbMoney Core already owns the configured data root"
            ) from exc
        return cls(path=path, handle=handle)

    def close(self) -> None:
        if self.handle.closed:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()


class WindowsCredentialManager:
    """Minimal read-only adapter for Windows generic credentials."""

    CRED_TYPE_GENERIC = 1

    def read_bytes(self, target: str) -> bytes:
        require_identifier(target, "credential target")
        if os.name != "nt":
            raise CredentialProviderError(
                "Windows Credential Manager is required for the production runner"
            )

        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD),
            ]

        class CREDENTIAL_ATTRIBUTEW(ctypes.Structure):
            _fields_ = [
                ("Keyword", wintypes.LPWSTR),
                ("Flags", wintypes.DWORD),
                ("ValueSize", wintypes.DWORD),
                ("Value", ctypes.POINTER(ctypes.c_ubyte)),
            ]

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.POINTER(CREDENTIAL_ATTRIBUTEW)),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        credential_pointer = ctypes.POINTER(CREDENTIALW)()
        advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        cred_read = advapi32.CredReadW
        cred_read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
        ]
        cred_read.restype = wintypes.BOOL
        cred_free = advapi32.CredFree
        cred_free.argtypes = [ctypes.c_void_p]
        cred_free.restype = None
        if not cred_read(
            target,
            self.CRED_TYPE_GENERIC,
            0,
            ctypes.byref(credential_pointer),
        ):
            error_code = ctypes.get_last_error()
            raise CredentialProviderError(
                f"credential target could not be read; winerror={error_code}"
            )
        try:
            credential = credential_pointer.contents
            if credential.CredentialBlobSize <= 0:
                raise CredentialProviderError("credential target contains an empty blob")
            return ctypes.string_at(
                credential.CredentialBlob,
                credential.CredentialBlobSize,
            )
        finally:
            cred_free(credential_pointer)


@dataclass(frozen=True)
class CoreRunnerConfig:
    data_root: Path
    policy_path: Path
    readiness_path: Path
    core_public_key_path: Path
    fund_lock_path: Path
    service_manifest_path: Path
    release_manifest_path: Path
    bind_port: int
    release_id: str
    risk_policy_sha256: str
    core_public_key_sha256: str
    fund_lock_sha256: str
    service_manifest_sha256: str
    runner_config_sha256: str
    role_public_keys_sha256: str
    readiness_ttl_seconds: int
    processing_interval_milliseconds: int
    credential_targets: Mapping[str, str]
    role_public_keys: RolePublicKeys

    @staticmethod
    def _absolute_path(value: object, context: str) -> Path:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{context} must be a non-empty absolute path")
        path = Path(value)
        if not path.is_absolute():
            raise ValueError(f"{context} must be absolute")
        return path.resolve()

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CoreRunnerConfig":
        expected = {
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
            "risk_policy_sha256",
            "core_public_key_sha256",
            "fund_lock_sha256",
            "service_manifest_sha256",
            "readiness_ttl_seconds",
            "processing_interval_milliseconds",
            "credential_targets",
            "role_public_keys_base64url",
        }
        if set(value) != expected:
            raise ValueError(
                "core runner config keys are invalid; "
                f"missing={sorted(expected - set(value))}; "
                f"unknown={sorted(set(value) - expected)}"
            )
        if value["schema"] != CONFIG_SCHEMA:
            raise ValueError(f"unsupported core runner config schema: {value['schema']}")
        bind_port = value["bind_port"]
        if (
            isinstance(bind_port, bool)
            or not isinstance(bind_port, int)
            or not 0 <= bind_port <= 65_535
        ):
            raise ValueError("bind_port must be an integer from 0 through 65535")
        readiness_ttl = value["readiness_ttl_seconds"]
        if (
            isinstance(readiness_ttl, bool)
            or not isinstance(readiness_ttl, int)
            or not 10 <= readiness_ttl <= 120
        ):
            raise ValueError("readiness_ttl_seconds must be from 10 through 120")
        processing_interval = value["processing_interval_milliseconds"]
        if (
            isinstance(processing_interval, bool)
            or not isinstance(processing_interval, int)
            or not 100 <= processing_interval <= 60_000
        ):
            raise ValueError(
                "processing_interval_milliseconds must be from 100 through 60000"
            )
        release_id = value["release_id"]
        if not isinstance(release_id, str) or not release_id.strip():
            raise ValueError("release_id must be non-empty")
        risk_policy_sha256 = cast(str, value["risk_policy_sha256"])
        core_public_key_sha256 = cast(str, value["core_public_key_sha256"])
        fund_lock_sha256 = cast(str, value["fund_lock_sha256"])
        service_manifest_sha256 = cast(str, value["service_manifest_sha256"])
        require_digest(risk_policy_sha256, "risk_policy_sha256")
        require_digest(core_public_key_sha256, "core_public_key_sha256")
        require_digest(fund_lock_sha256, "fund_lock_sha256")
        require_digest(service_manifest_sha256, "service_manifest_sha256")

        raw_targets = value["credential_targets"]
        required_targets = {
            "core_signing_seed",
            "desktop_read_token",
            "operator_bearer_token",
            "allocator_bearer_token",
            "dummy_cell_bearer_token",
            "dopey_cell_bearer_token",
        }
        if not isinstance(raw_targets, dict) or set(raw_targets) != required_targets:
            raise ValueError(
                "credential_targets must define exactly the six protected targets"
            )
        targets: dict[str, str] = {}
        for name, target in raw_targets.items():
            if not isinstance(target, str):
                raise TypeError(f"credential_targets.{name} must be a string")
            targets[name] = require_identifier(
                target,
                f"credential_targets.{name}",
            )
        if len(set(targets.values())) != len(targets):
            raise ValueError("credential target names must be distinct")

        raw_roles = value["role_public_keys_base64url"]
        role_names = {
            "operator",
            "evaluator",
            "promoter",
            "allocator",
            "research",
            "dummy_venue",
            "dopey_venue",
            "migration",
        }
        if not isinstance(raw_roles, dict) or set(raw_roles) != role_names:
            raise ValueError(
                "role_public_keys_base64url must define every canonical role"
            )
        decoded_roles: dict[str, tuple[bytes, ...]] = {}
        for role, raw_keys in raw_roles.items():
            if (
                not isinstance(raw_keys, list)
                or not all(isinstance(item, str) for item in raw_keys)
            ):
                raise TypeError(
                    f"role_public_keys_base64url.{role} must be an array of strings"
                )
            decoded = tuple(
                decode_base64url(item, f"role_public_keys_base64url.{role}")
                for item in raw_keys
            )
            if len(set(decoded)) != len(decoded):
                raise ValueError(f"role {role} contains duplicate public keys")
            decoded_roles[role] = decoded
        for required_role in (
            "operator",
            "evaluator",
            "promoter",
            "allocator",
            "research",
            "dummy_venue",
            "dopey_venue",
        ):
            if not decoded_roles[required_role]:
                raise ValueError(f"production role {required_role} must not be empty")

        return cls(
            data_root=cls._absolute_path(value["data_root"], "data_root"),
            policy_path=cls._absolute_path(value["policy_path"], "policy_path"),
            readiness_path=cls._absolute_path(
                value["readiness_path"],
                "readiness_path",
            ),
            core_public_key_path=cls._absolute_path(
                value["core_public_key_path"],
                "core_public_key_path",
            ),
            fund_lock_path=cls._absolute_path(
                value["fund_lock_path"],
                "fund_lock_path",
            ),
            service_manifest_path=cls._absolute_path(
                value["service_manifest_path"],
                "service_manifest_path",
            ),
            release_manifest_path=cls._absolute_path(
                value["release_manifest_path"],
                "release_manifest_path",
            ),
            bind_port=bind_port,
            release_id=release_id,
            risk_policy_sha256=risk_policy_sha256,
            core_public_key_sha256=core_public_key_sha256,
            fund_lock_sha256=fund_lock_sha256,
            service_manifest_sha256=service_manifest_sha256,
            runner_config_sha256=canonical_sha256(value),
            role_public_keys_sha256=canonical_sha256(raw_roles),
            readiness_ttl_seconds=readiness_ttl,
            processing_interval_milliseconds=processing_interval,
            credential_targets=MappingProxyType(targets),
            role_public_keys=RolePublicKeys(
                operator=decoded_roles["operator"],
                evaluator=decoded_roles["evaluator"],
                promoter=decoded_roles["promoter"],
                allocator=decoded_roles["allocator"],
                research=decoded_roles["research"],
                dummy_venue=decoded_roles["dummy_venue"],
                dopey_venue=decoded_roles["dopey_venue"],
                migration=decoded_roles["migration"],
            ),
        )

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        expected_file_sha256: str,
    ) -> "CoreRunnerConfig":
        if not path.is_absolute():
            raise ValueError("--config must be an absolute path")
        require_digest(expected_file_sha256, "--config-sha256")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError("core runner config cannot be read") from exc
        observed = hashlib.sha256(raw).hexdigest()
        if observed != expected_file_sha256:
            raise ValueError(
                "core runner config does not match --config-sha256"
            )
        parsed = loads_strict_json(raw, "core runner config")
        return replace(
            cls.from_dict(parsed),
            runner_config_sha256=observed,
        )


def _load_token(provider: CredentialProvider, target: str, context: str) -> str:
    raw = provider.read_bytes(target)
    try:
        token = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CredentialProviderError(f"{context} must contain UTF-8 bytes") from exc
    if len(token) < 32 or len(token) > 512 or any(character.isspace() for character in token):
        raise CredentialProviderError(
            f"{context} must be 32-512 non-whitespace UTF-8 characters"
        )
    return token


def _expected_core_public_key(raw: bytes) -> bytes:
    try:
        value = raw.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("core public-key file must contain ASCII bytes") from exc
    return decode_base64url(value, "core public-key file")


def _verified_file_bytes(
    path: Path,
    expected_digest: str,
    context: str,
) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{context} cannot be read") from exc
    observed = hashlib.sha256(raw).hexdigest()
    if observed != expected_digest:
        raise ValueError(f"{context} does not match its pinned SHA-256 digest")
    return raw


def _release_manifest_digest(
    raw: bytes,
    config: CoreRunnerConfig,
) -> str:
    manifest = loads_strict_json(raw, "sealed release manifest")
    if manifest.get("schema_version") != "dumbmoney.release-manifest.v1":
        raise ValueError("release manifest schema is unsupported")
    if manifest.get("release_state") != "SEALED" or manifest.get("immutable") is not True:
        raise ValueError("release manifest must be immutable and SEALED")
    if manifest.get("release_id") != config.release_id:
        raise ValueError("release manifest release_id does not match Core config")
    safe_release_fields = {
        "broker_actions_authorized": False,
        "distribution": "PRIVATE_LOCAL_ONLY",
        "hosted_control_plane": False,
        "remote_updates_authorized": False,
        "cloud_secret_storage_authorized": False,
        "telemetry_publication": False,
    }
    if any(
        manifest.get(field) != expected
        for field, expected in safe_release_fields.items()
    ):
        raise ValueError(
            "release manifest widens private-local or broker authority"
        )
    for field, expected_digest in (
        ("fund_lock", config.fund_lock_sha256),
        ("services_manifest", config.service_manifest_sha256),
    ):
        reference = manifest.get(field)
        if (
            not isinstance(reference, Mapping)
            or reference.get("sha256") != expected_digest
        ):
            raise ValueError(
                f"release manifest {field} digest does not match Core config"
            )
    artifacts = manifest.get("installed_artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("release manifest installed_artifacts must be an array")
    core_records = [
        item
        for item in artifacts
        if isinstance(item, Mapping)
        and item.get("name") == "core-runner-config"
    ]
    if (
        len(core_records) != 1
        or core_records[0].get("state") != "SEALED"
        or core_records[0].get("sha256") != config.runner_config_sha256
    ):
        raise ValueError(
            "release manifest must bind the exact SEALED Core runner config"
        )
    return hashlib.sha256(raw).hexdigest()


@dataclass
class CoreService:
    """One bound Core instance and its supervised lifecycle resources."""

    config: CoreRunnerConfig
    application: FundApplication
    server: ThreadingHTTPServer
    signer: Ed25519Signer
    instance_id: str
    data_root_lease: DataRootLease
    _readiness_sequence: int = 0

    @property
    def endpoint(self) -> tuple[str, int]:
        host, port = cast(tuple[str, int], self.server.server_address)
        return host, port

    def _readiness_envelope(self, observed_at: datetime) -> SignedEnvelopeV1:
        now = observed_at.astimezone(timezone.utc)
        valid_until = now + timedelta(seconds=self.config.readiness_ttl_seconds)
        snapshot = self.application.control_plane.control_snapshot(observed_at=now)
        ledger = cast(Mapping[str, object], snapshot["ledger"])
        ledger_sequence = cast(int, ledger["last_global_sequence"])
        ledger_head = cast(str, ledger["chain_head"])
        status = "READY" if snapshot["status"] == "LIVE_READY" else "DEGRADED"
        host, port = self.endpoint
        body = ReadinessDescriptorV1(
            service_name=READINESS_SOURCE,
            release_id=self.config.release_id,
            instance_id=self.instance_id,
            process_id=os.getpid(),
            generation=0,
            observed_at=now,
            valid_until=valid_until,
            endpoint={
                "transport": "http",
                "host": host,
                "port": port,
                "base_path": "/",
            },
            fund_lock_sha256=self.config.fund_lock_sha256,
            service_manifest_sha256=self.config.service_manifest_sha256,
            authority={
                "broker": "NONE",
                "mode": "OFFLINE",
                "execution_enabled": False,
            },
            health={
                "status": status,
                "control_status": snapshot["status"],
                "reason_codes": snapshot["reason_codes"],
                "ledger_head_sequence": ledger_sequence,
                "ledger_chain_head": ledger_head,
                "runner_config_sha256": self.config.runner_config_sha256,
                "risk_policy_sha256": self.config.risk_policy_sha256,
                "core_public_key_sha256": self.config.core_public_key_sha256,
                "role_public_keys_sha256": self.config.role_public_keys_sha256,
            },
            capabilities=(
                "allocator-signed-capital-requests",
                "cell-command-checkpoints",
                "cell-journal-head-anchors",
                "control-snapshot",
                "event-ledger",
            ),
        )
        self._readiness_sequence += 1
        return SignedEnvelopeV1.issue(
            body,
            source_id=READINESS_SOURCE,
            source_sequence=self._readiness_sequence,
            correlation_id=f"readiness-{self.instance_id}",
            causation_id=None,
            nonce=canonical_sha256(
                [
                    "readiness",
                    self.instance_id,
                    self._readiness_sequence,
                    now.isoformat(),
                ]
            ),
            not_before=now,
            expires_at=valid_until,
            signer=self.signer,
        )

    def write_readiness(self, observed_at: datetime | None = None) -> SignedEnvelopeV1:
        envelope = self._readiness_envelope(
            observed_at or datetime.now(timezone.utc)
        )
        destination = self.config.readiness_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        payload = canonical_json_bytes(envelope.to_dict()) + b"\n"
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return envelope

    def serve(self, stop_event: threading.Event) -> None:
        self.server.timeout = 0.25
        next_processing = 0.0
        next_readiness = 0.0
        processing_interval = self.config.processing_interval_milliseconds / 1000
        readiness_interval = max(1.0, self.config.readiness_ttl_seconds / 2)
        while not stop_event.is_set():
            monotonic = time.monotonic()
            if monotonic >= next_processing:
                self.application.control_plane.process_pending_capital_requests()
                next_processing = monotonic + processing_interval
            if monotonic >= next_readiness:
                self.write_readiness()
                next_readiness = monotonic + readiness_interval
            self.server.handle_request()

    def close(self) -> None:
        try:
            self.server.server_close()
        finally:
            try:
                self.application.close()
            finally:
                self.data_root_lease.close()

    def __enter__(self) -> "CoreService":
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        self.close()


def build_core_service(
    config: CoreRunnerConfig,
    credential_provider: CredentialProvider,
) -> CoreService:
    seed = credential_provider.read_bytes(
        config.credential_targets["core_signing_seed"]
    )
    if len(seed) != 32:
        raise CredentialProviderError(
            "core signing credential must contain exactly one raw 32-byte Ed25519 seed"
        )
    signer = Ed25519Signer.from_private_bytes(seed)
    core_public_key_raw = _verified_file_bytes(
        config.core_public_key_path,
        config.core_public_key_sha256,
        "core public key",
    )
    if signer.public_key_bytes != _expected_core_public_key(
        core_public_key_raw
    ):
        raise CredentialProviderError(
            "core signing credential does not match the pinned public key"
        )
    policy_raw = _verified_file_bytes(
        config.policy_path,
        config.risk_policy_sha256,
        "risk policy",
    )
    verified_policy = RiskPolicyV1.from_dict(
        loads_strict_json(policy_raw, "pinned risk policy")
    )
    _verified_file_bytes(
        config.fund_lock_path,
        config.fund_lock_sha256,
        "fund lock",
    )
    _verified_file_bytes(
        config.service_manifest_path,
        config.service_manifest_sha256,
        "service manifest",
    )
    try:
        release_manifest_raw = config.release_manifest_path.read_bytes()
    except OSError as exc:
        raise ValueError("release manifest cannot be read") from exc
    release_manifest_sha256 = _release_manifest_digest(
        release_manifest_raw,
        config,
    )
    deployment_bindings = DeploymentAuthorityBindings(
        release_manifest_sha256=release_manifest_sha256,
        core_runner_config_sha256=config.runner_config_sha256,
        fund_lock_sha256=config.fund_lock_sha256,
    )
    desktop_token = _load_token(
        credential_provider,
        config.credential_targets["desktop_read_token"],
        "desktop read token credential",
    )
    operator_token = _load_token(
        credential_provider,
        config.credential_targets["operator_bearer_token"],
        "operator bearer token credential",
    )
    allocator_token = _load_token(
        credential_provider,
        config.credential_targets["allocator_bearer_token"],
        "allocator bearer token credential",
    )
    dummy_token = _load_token(
        credential_provider,
        config.credential_targets["dummy_cell_bearer_token"],
        "Dummy cell bearer token credential",
    )
    dopey_token = _load_token(
        credential_provider,
        config.credential_targets["dopey_cell_bearer_token"],
        "Dopey cell bearer token credential",
    )
    data_root_lease = DataRootLease.acquire(config.data_root)
    try:
        application = build_fund_application(
            data_root=config.data_root,
            policy_path=config.policy_path,
            core_signer=signer,
            role_public_keys=config.role_public_keys,
            verified_policy=verified_policy,
            deployment_bindings=deployment_bindings,
        )
        try:
            stored_manifest = application.cas.put_bytes(release_manifest_raw)
            if stored_manifest != release_manifest_sha256:
                raise RuntimeError(
                    "release manifest CAS digest differs from verified startup bytes"
                )
        except BaseException:
            application.close()
            raise
    except BaseException:
        data_root_lease.close()
        raise
    try:
        server = make_loopback_server(
            application,
            port=config.bind_port,
            operator_token_sha256=hashlib.sha256(
                operator_token.encode("utf-8")
            ).hexdigest(),
            desktop_read_token_sha256=hashlib.sha256(
                desktop_token.encode("utf-8")
            ).hexdigest(),
            allocator_token_sha256=hashlib.sha256(
                allocator_token.encode("utf-8")
            ).hexdigest(),
            cell_token_sha256={
                "dummy_kalshi": hashlib.sha256(
                    dummy_token.encode("utf-8")
                ).hexdigest(),
                "dopey_robinhood": hashlib.sha256(
                    dopey_token.encode("utf-8")
                ).hexdigest(),
            },
        )
    except BaseException:
        application.close()
        data_root_lease.close()
        raise
    return CoreService(
        config=config,
        application=application,
        server=server,
        signer=signer,
        instance_id=str(uuid.uuid4()),
        data_root_lease=data_root_lease,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blunder-fund-core",
        description="Run the private loopback-only DumbMoney Core service.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Absolute public core-runner config path; never contains secret values.",
    )
    parser.add_argument(
        "--config-sha256",
        required=True,
        help="Pinned SHA-256 of the exact public runner config bytes.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    try:
        config = CoreRunnerConfig.load(
            args.config,
            expected_file_sha256=args.config_sha256,
        )
        with build_core_service(
            config,
            WindowsCredentialManager(),
        ) as service:
            readiness = service.write_readiness()
            host, port = service.endpoint
            print(
                json.dumps(
                    {
                        "schema": "dumbmoney.core-service-ready.v1",
                        "service_name": READINESS_SOURCE,
                        "host": host,
                        "port": port,
                        "process_id": os.getpid(),
                        "instance_id": service.instance_id,
                        "readiness_path": str(config.readiness_path),
                        "signer_key_id": readiness.signer_key_id,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                flush=True,
            )
            service.serve(stop_event)
        return 0
    except (CredentialProviderError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(
            f"blunder-fund-core startup failed safely: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
