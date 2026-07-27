from __future__ import annotations

import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from blunder.fund.contracts import ReadinessDescriptorV1, SignedEnvelopeV1
from blunder.fund.crypto import Ed25519Keyring, Ed25519Signer
from blunder.fund.entrypoint import (
    CoreRunnerConfig,
    build_core_service,
)
from blunder.fund.policy import RiskPolicyV1


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeCredentialProvider:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values

    def read_bytes(self, target: str) -> bytes:
        return self.values[target]


class CoreRunnerTests(unittest.TestCase):
    def _config(
        self,
        root: Path,
    ) -> tuple[
        CoreRunnerConfig,
        FakeCredentialProvider,
        Ed25519Signer,
        dict[str, object],
    ]:
        core = Ed25519Signer.from_private_bytes(bytes(range(32)))
        roles = {
            name: Ed25519Signer.generate()
            for name in (
                "operator",
                "evaluator",
                "promoter",
                "allocator",
                "research",
                "dummy_venue",
                "dopey_venue",
            )
        }
        public_key_path = root / "core-public.key"
        public_key_path.write_text(
            core.public_key_base64url + "\n",
            encoding="ascii",
        )
        fund_lock = root / "fund.lock.json"
        fund_lock.write_bytes(b'{"sealed":"fund-lock"}\n')
        service_manifest = root / "services.v1.json"
        service_manifest.write_bytes(b'{"sealed":"service-manifest"}\n')
        policy_path = root / "risk-policy.v1.json"
        policy_path.write_bytes(
            (
                REPO_ROOT
                / "configs"
                / "dumbmoney"
                / "risk-policy.v1.json"
            ).read_bytes()
        )
        release_manifest = root / "release-manifest.json"
        targets = {
            "core_signing_seed": "DumbMoney/Core/SigningSeed",
            "desktop_read_token": "DumbMoney/Core/DesktopReadToken",
            "operator_bearer_token": "DumbMoney/Core/OperatorBearerToken",
            "allocator_bearer_token": "DumbMoney/Core/AllocatorBearerToken",
            "dummy_cell_bearer_token": "DumbMoney/Core/DummyCellBearerToken",
            "dopey_cell_bearer_token": "DumbMoney/Core/DopeyCellBearerToken",
        }
        token_values = {
            "desktop_read_token": b"desktop-read-token-0123456789abcdef",
            "operator_bearer_token": b"operator-bearer-token-0123456789abcdef",
            "allocator_bearer_token": b"allocator-bearer-token-0123456789abcdef",
            "dummy_cell_bearer_token": b"dummy-cell-bearer-token-0123456789abcdef",
            "dopey_cell_bearer_token": b"dopey-cell-bearer-token-0123456789abcdef",
        }
        credentials = {
            targets["core_signing_seed"]: bytes(range(32)),
            **{
                targets[name]: value
                for name, value in token_values.items()
            },
        }
        raw = {
            "schema": "dumbmoney.core-runner-config.v1",
            "data_root": str((root / "data").resolve()),
            "policy_path": str(policy_path.resolve()),
            "readiness_path": str((root / "readiness" / "DumbMoneyCore.json").resolve()),
            "core_public_key_path": str(public_key_path.resolve()),
            "fund_lock_path": str(fund_lock.resolve()),
            "service_manifest_path": str(service_manifest.resolve()),
            "release_manifest_path": str(release_manifest.resolve()),
            "bind_port": 0,
            "release_id": "runner-test-release",
            "risk_policy_sha256": hashlib.sha256(
                policy_path.read_bytes()
            ).hexdigest(),
            "core_public_key_sha256": hashlib.sha256(
                public_key_path.read_bytes()
            ).hexdigest(),
            "fund_lock_sha256": hashlib.sha256(fund_lock.read_bytes()).hexdigest(),
            "service_manifest_sha256": hashlib.sha256(
                service_manifest.read_bytes()
            ).hexdigest(),
            "readiness_ttl_seconds": 30,
            "processing_interval_milliseconds": 100,
            "credential_targets": targets,
            "role_public_keys_base64url": {
                **{
                    role: [signer.public_key_base64url]
                    for role, signer in roles.items()
                },
                "migration": [],
            },
        }
        rendered = json.dumps(raw)
        for secret in token_values.values():
            self.assertNotIn(secret.decode(), rendered)
        config = CoreRunnerConfig.from_dict(raw)
        release_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "dumbmoney.release-manifest.v1",
                    "release_id": config.release_id,
                    "release_state": "SEALED",
                    "immutable": True,
                    "broker_actions_authorized": False,
                    "distribution": "PRIVATE_LOCAL_ONLY",
                    "hosted_control_plane": False,
                    "remote_updates_authorized": False,
                    "cloud_secret_storage_authorized": False,
                    "telemetry_publication": False,
                    "fund_lock": {"sha256": config.fund_lock_sha256},
                    "services_manifest": {
                        "sha256": config.service_manifest_sha256
                    },
                    "installed_artifacts": [
                        {
                            "name": "core-runner-config",
                            "state": "SEALED",
                            "sha256": config.runner_config_sha256,
                        }
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return (
            config,
            FakeCredentialProvider(credentials),
            core,
            raw,
        )

    def test_runner_emits_signed_attach_descriptor_and_serves_real_snapshot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, credentials, core, _raw = self._config(root)
            stop = threading.Event()
            with build_core_service(config, credentials) as service:
                expected_release_digest = hashlib.sha256(
                    config.release_manifest_path.read_bytes()
                ).hexdigest()
                bindings = service.application.control_plane.deployment_bindings
                self.assertIsNotNone(bindings)
                assert bindings is not None
                self.assertEqual(
                    bindings.release_manifest_sha256,
                    expected_release_digest,
                )
                self.assertTrue(
                    service.application.cas.has(expected_release_digest)
                )
                service.write_readiness()
                raw_readiness = json.loads(config.readiness_path.read_bytes())
                self.assertEqual(
                    raw_readiness["schema"],
                    "dumbmoney.signed-envelope.v1",
                )
                self.assertNotEqual(
                    raw_readiness["schema"],
                    "obtuse.blunder.core-ready.v1",
                )
                envelope = SignedEnvelopeV1.from_dict(raw_readiness)
                keyring = Ed25519Keyring()
                keyring.register(core.public_key_bytes)
                body = envelope.verify_authenticity(keyring)
                self.assertIsInstance(body, ReadinessDescriptorV1)
                self.assertEqual(body.service_name, "DumbMoneyCore")
                self.assertEqual(body.authority["broker"], "NONE")
                self.assertFalse(body.authority["execution_enabled"])
                self.assertIn(
                    "cell-journal-head-anchors",
                    body.capabilities,
                )
                self.assertEqual(
                    body.health["ledger_chain_head"],
                    "0" * 64,
                )
                self.assertEqual(
                    body.health["runner_config_sha256"],
                    config.runner_config_sha256,
                )
                self.assertEqual(
                    body.health["risk_policy_sha256"],
                    config.risk_policy_sha256,
                )
                self.assertEqual(
                    body.health["core_public_key_sha256"],
                    config.core_public_key_sha256,
                )
                self.assertEqual(
                    body.health["role_public_keys_sha256"],
                    config.role_public_keys_sha256,
                )
                with self.assertRaisesRegex(
                    RuntimeError,
                    "already owns",
                ):
                    build_core_service(config, credentials)

                thread = threading.Thread(
                    target=service.serve,
                    args=(stop,),
                    daemon=True,
                )
                thread.start()
                try:
                    host, port = service.endpoint
                    connection = http.client.HTTPConnection(host, port, timeout=5)
                    desktop_token = credentials.values[
                        config.credential_targets["desktop_read_token"]
                    ].decode()
                    operator_token = credentials.values[
                        config.credential_targets["operator_bearer_token"]
                    ].decode()
                    connection.request(
                        "GET",
                        "/api/v1/fund/control-snapshot",
                        headers={"X-Blunder-Token": desktop_token},
                    )
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    snapshot = json.loads(response.read())
                    self.assertEqual(
                        snapshot["schema"],
                        "dumbmoney.control-snapshot.v1",
                    )
                    connection.request(
                        "GET",
                        "/api/v1/fund/control-snapshot",
                        headers={"X-Blunder-Token": operator_token},
                    )
                    response = connection.getresponse()
                    self.assertEqual(response.status, 401)
                    response.read()

                    anchor_body = json.dumps(
                        {
                            "schema": (
                                "dumbmoney.cell-journal-anchor-request.v1"
                            ),
                            "cell_id": "dummy_kalshi",
                            "account_hash": "a" * 64,
                            "journal_name": "capital-operational",
                            "journal_schema": (
                                "dummy.sqlite-operational-journal.v1"
                            ),
                            "journal_stream_id": "b" * 64,
                            "journal_sequence": 0,
                            "journal_head_sha256": "0" * 64,
                            "request_nonce": "c" * 64,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                    connection.request(
                        "POST",
                        (
                            "/v1/cells/dummy_kalshi/"
                            "journal-heads:anchor"
                        ),
                        body=anchor_body,
                        headers={
                            "Authorization": f"Bearer {operator_token}",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    self.assertEqual(response.status, 401)
                    response.read()

                    dummy_token = credentials.values[
                        config.credential_targets[
                            "dummy_cell_bearer_token"
                        ]
                    ].decode()
                    connection.request(
                        "POST",
                        (
                            "/v1/cells/dummy_kalshi/"
                            "journal-heads:anchor"
                        ),
                        body=anchor_body,
                        headers={
                            "Authorization": f"Bearer {dummy_token}",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    anchored = json.loads(response.read())
                    self.assertEqual(
                        anchored["schema"],
                        "dumbmoney.cell-journal-anchor-response.v1",
                    )

                    dopey_token = credentials.values[
                        config.credential_targets[
                            "dopey_cell_bearer_token"
                        ]
                    ].decode()
                    dopey_anchor_body = json.dumps(
                        {
                            "schema": (
                                "dumbmoney.cell-journal-anchor-request.v1"
                            ),
                            "cell_id": "dopey_robinhood",
                            "account_hash": "d" * 64,
                            "journal_name": "live-branch-ledger",
                            "journal_schema": (
                                "dopey-live-branch-ledger-v1"
                            ),
                            "journal_stream_id": "e" * 64,
                            "journal_sequence": 0,
                            "journal_head_sha256": "0" * 64,
                            "request_nonce": "f" * 64,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                    connection.request(
                        "POST",
                        (
                            "/v1/cells/dopey_robinhood/"
                            "journal-heads:anchor"
                        ),
                        body=dopey_anchor_body,
                        headers={
                            "Authorization": f"Bearer {dopey_token}",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    dopey_anchor = json.loads(response.read())
                    self.assertEqual(
                        dopey_anchor["anchor_envelope"]["body"]["venue"],
                        "dopey_robinhood",
                    )
                    connection.close()
                finally:
                    stop.set()
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())

    def test_runner_refuses_to_attest_mutated_release_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, credentials, _core, _raw = self._config(root)
            config.fund_lock_path.write_bytes(b"mutated")
            with self.assertRaisesRegex(ValueError, "fund lock.*pinned SHA-256"):
                build_core_service(config, credentials)

    def test_runner_rejects_release_manifest_binding_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, credentials, _core, _raw = self._config(root)
            manifest = json.loads(config.release_manifest_path.read_bytes())
            manifest["fund_lock"]["sha256"] = "0" * 64
            config.release_manifest_path.write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "fund_lock digest does not match",
            ):
                build_core_service(config, credentials)

    def test_runner_rejects_release_manifest_authority_widening(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, credentials, _core, _raw = self._config(root)
            manifest = json.loads(config.release_manifest_path.read_bytes())
            manifest["hosted_control_plane"] = True
            config.release_manifest_path.write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "widens private-local or broker authority",
            ):
                build_core_service(config, credentials)

    def test_verified_policy_bytes_are_not_reopened_after_hash_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, credentials, _core, _raw = self._config(root)
            expected_policy_digest = RiskPolicyV1.load(
                config.policy_path
            ).digest
            from blunder.fund import entrypoint

            original_read = entrypoint._verified_file_bytes

            def read_then_mutate(
                path: Path,
                expected_digest: str,
                context: str,
            ) -> bytes:
                raw = original_read(path, expected_digest, context)
                if context == "risk policy":
                    path.write_bytes(b'{"mutated_after_verified_read":true}')
                return raw

            with patch(
                "blunder.fund.entrypoint._verified_file_bytes",
                side_effect=read_then_mutate,
            ):
                with build_core_service(config, credentials) as service:
                    self.assertEqual(
                        service.application.control_plane.policy_engine.policy.digest,
                        expected_policy_digest,
                    )
                    self.assertEqual(
                        config.policy_path.read_bytes(),
                        b'{"mutated_after_verified_read":true}',
                    )

    def test_config_bytes_are_verified_before_authority_is_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _config, _credentials, _core, raw = self._config(root)
            config_path = root / "core-runner.v1.json"
            config_path.write_text(
                json.dumps(raw, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
            loaded = CoreRunnerConfig.load(
                config_path.resolve(),
                expected_file_sha256=digest,
            )
            self.assertEqual(loaded.runner_config_sha256, digest)
            with self.assertRaisesRegex(ValueError, "config-sha256"):
                CoreRunnerConfig.load(
                    config_path.resolve(),
                    expected_file_sha256="0" * 64,
                )


if __name__ == "__main__":
    unittest.main()
