from __future__ import annotations

import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from blunder.fund.canonical import canonical_json_bytes, loads_strict_json
from blunder.fund.contracts import ReadinessDescriptorV1
from blunder.fund.crypto import Ed25519Keyring, Ed25519Signer
from blunder.fund.model_gateway import HttpResponse, TransportError
from blunder.fund.model_gateway_entrypoint import (
    COMPLETION_PATH,
    CONFIG_SCHEMA,
    HEALTH_READY_PATH,
    REQUEST_SCHEMA,
    STATUS_PATH,
    CredentialProviderError,
    ModelGatewayRunnerConfig,
    build_model_gateway_service,
)


OPENROUTER_TARGET = "credential-target:DumbMoney/OpenRouterApiKey"
SIGNER_TARGET = "credential-target:DumbMoney/ModelGatewaySigner"
CLIENT_TARGET = "credential-target:DumbMoney/ModelGatewayClientToken"
OPENROUTER_SECRET = b"test-openrouter-key-never-persisted"
CLIENT_TOKEN = b"test-local-client-token-at-least-thirty-two-bytes"


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeCredentialProvider:
    def __init__(self, values: Mapping[str, bytes]) -> None:
        self.values = dict(values)
        self.calls: list[str] = []

    def read_bytes(self, target: str) -> bytes:
        self.calls.append(target)
        try:
            return self.values[target]
        except KeyError as exc:
            raise CredentialProviderError(
                "required fake credential is unavailable"
            ) from exc


class FakeTransport:
    def __init__(
        self,
        responses: list[HttpResponse | Exception] | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> HttpResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout_seconds": timeout_seconds,
                "max_response_bytes": max_response_bytes,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected provider call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ModelGatewayRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.clock = MutableClock(datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc))
        self.signer = Ed25519Signer.from_private_bytes(bytes(range(32)))
        self.public_key_path = self.root / "gateway-ed25519.pub"
        self.public_key_path.write_text(
            self.signer.public_key_base64url + "\n",
            encoding="ascii",
        )
        self.fund_lock_path = self.root / "fund.lock.json"
        self.fund_lock_path.write_bytes(
            canonical_json_bytes(
                {
                    "schema_version": "dumbmoney.fund-lock.v1",
                    "deployment_scope": "PRIVATE_LOCAL_WINDOWS",
                    "public_distribution": False,
                }
            )
        )
        self.service_manifest_path = self.root / "services.v1.json"
        self.service_manifest_path.write_bytes(
            canonical_json_bytes(
                {
                    "services": [
                        {
                            "name": "DumbMoneyModelGateway",
                            "broker_authority": "NONE",
                            "network_allowlist": [
                                "loopback",
                                "https://openrouter.ai",
                            ],
                            "config_bindings": {
                                "openrouter_credential_target": OPENROUTER_TARGET,
                                "gateway_signer_target": SIGNER_TARGET,
                                "client_bearer_token_target": CLIENT_TARGET,
                            },
                        }
                    ]
                }
            )
        )
        self.config_path = self.root / "model-gateway-runner.json"
        self.config_value = self._config_value()
        self.config = self._write_and_load_config(self.config_value)
        self.credentials = FakeCredentialProvider(
            {
                OPENROUTER_TARGET: OPENROUTER_SECRET,
                SIGNER_TARGET: bytes(range(32)),
                CLIENT_TARGET: CLIENT_TOKEN,
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _config_value(self) -> dict[str, object]:
        route = {
            "model": "deepseek/deepseek-r1",
            "provider": "deepinfra/turbo",
            "provider_name": "DeepInfra",
            "max_output_tokens": 1_024,
            "max_price": {"prompt": 1, "completion": 2},
        }
        return {
            "schema": CONFIG_SCHEMA,
            "data_root": str(self.root / "state"),
            "readiness_path": str(self.root / "readiness" / "gateway.json"),
            "gateway_public_key_path": str(self.public_key_path),
            "fund_lock_path": str(self.fund_lock_path),
            "service_manifest_path": str(self.service_manifest_path),
            "bind_port": 0,
            "release_id": "dumbmoney-test-release",
            "fund_lock_sha256": self._file_sha256(self.fund_lock_path),
            "service_manifest_sha256": self._file_sha256(self.service_manifest_path),
            "gateway_public_key_sha256": self._file_sha256(self.public_key_path),
            "readiness_ttl_seconds": 90,
            "request_body_max_bytes": 262_144,
            "max_active_requests": 4,
            "client_socket_timeout_seconds": 5,
            "request_timeout_seconds": 15,
            "attestation_max_age_seconds": 15,
            "near_midnight_fence_seconds": 300,
            "max_prompt_characters": 100_000,
            "max_response_bytes": 1_000_000,
            "expected_key_label": "DumbMoney-Dedicated",
            "credential_targets": {
                "openrouter_api_key": OPENROUTER_TARGET,
                "gateway_signing_seed": SIGNER_TARGET,
                "client_bearer_token": CLIENT_TARGET,
            },
            "routes": {
                "research": dict(route),
                "evaluation": dict(route),
                "operations": dict(route),
            },
        }

    def _write_and_load_config(
        self,
        value: Mapping[str, object],
    ) -> ModelGatewayRunnerConfig:
        raw = canonical_json_bytes(value) + b"\n"
        self.config_path.write_bytes(raw)
        return ModelGatewayRunnerConfig.load(
            self.config_path,
            expected_file_sha256=hashlib.sha256(raw).hexdigest(),
        )

    @staticmethod
    def _key_status() -> HttpResponse:
        return HttpResponse(
            status=200,
            body={
                "data": {
                    "label": "DumbMoney-Dedicated",
                    "limit": Decimal("10"),
                    "limit_reset": "daily",
                    "limit_remaining": Decimal("10"),
                    "include_byok_in_limit": True,
                    "usage_daily": Decimal("0"),
                    "byok_usage_daily": Decimal("0"),
                }
            },
            received_bytes=256,
        )

    @staticmethod
    def _completion() -> HttpResponse:
        return HttpResponse(
            status=200,
            body={
                "id": "gen-runner-1",
                "model": "deepseek/deepseek-r1",
                "provider": "DeepInfra",
                "usage": {
                    "cost": Decimal("0.001234"),
                    "prompt_tokens": 20,
                    "completion_tokens": 12,
                },
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "bounded result",
                        },
                    }
                ],
                "openrouter_metadata": {
                    "requested": "deepseek/deepseek-r1",
                    "strategy": "direct",
                    "attempt": 1,
                    "endpoints": {
                        "total": 1,
                        "available": [
                            {
                                "provider": "DeepInfra",
                                "model": "deepseek/deepseek-r1",
                                "selected": True,
                            }
                        ],
                    },
                    "attempts": [
                        {
                            "provider": "DeepInfra",
                            "model": "deepseek/deepseek-r1",
                            "status": 200,
                        }
                    ],
                    "pipeline": [],
                },
            },
            received_bytes=512,
        )

    @staticmethod
    def _generation() -> HttpResponse:
        return HttpResponse(
            status=200,
            body={
                "data": {
                    "id": "gen-runner-1",
                    "model": "deepseek/deepseek-r1",
                    "provider_name": "DeepInfra",
                    "total_cost": Decimal("0.001234"),
                }
            },
            received_bytes=128,
        )

    @staticmethod
    def _completion_request(request_id: str = "runner-request-1") -> bytes:
        return canonical_json_bytes(
            {
                "schema": REQUEST_SCHEMA,
                "request_id": request_id,
                "category": "research",
                "messages": [
                    {
                        "role": "user",
                        "content": "Evaluate a public economic hypothesis.",
                    }
                ],
                "reserve_microusd": 10_000,
                "max_output_tokens": 200,
            }
        )

    def _start_service(
        self,
        transport: FakeTransport,
    ) -> tuple[Any, threading.Event, threading.Thread]:
        service = build_model_gateway_service(
            self.config,
            self.credentials,
            transport=transport,
            clock=self.clock,
        )
        stop_event = threading.Event()
        thread = threading.Thread(
            target=service.serve,
            args=(stop_event,),
            name="fake-model-gateway-service",
        )
        thread.start()
        return service, stop_event, thread

    def _request(
        self,
        service: Any,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        authenticated: bool = False,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, Mapping[str, object], Mapping[str, str]]:
        request_headers = dict(headers or {})
        if authenticated:
            request_headers["Authorization"] = f"Bearer {CLIENT_TOKEN.decode()}"
        if body is not None:
            request_headers.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection(
            service.endpoint[0],
            service.endpoint[1],
            timeout=5,
        )
        try:
            connection.request(
                method,
                path,
                body=body,
                headers=request_headers,
            )
            response = connection.getresponse()
            response_body = loads_strict_json(
                response.read(),
                "test HTTP response",
            )
            response_headers = {
                name.casefold(): value for name, value in response.getheaders()
            }
            return response.status, response_body, response_headers
        finally:
            connection.close()

    @staticmethod
    def _stop_service(
        service: Any,
        stop_event: threading.Event,
        thread: threading.Thread,
    ) -> None:
        stop_event.set()
        thread.join(timeout=5)
        if thread.is_alive():
            raise AssertionError("model gateway service did not stop")
        service.close()

    def test_config_requires_exact_raw_digest_and_rejects_unknown_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match --config-sha256"):
            ModelGatewayRunnerConfig.load(
                self.config_path,
                expected_file_sha256="f" * 64,
            )

        modified = dict(self.config_value)
        modified["unexpected"] = "public-but-unsupported"
        with self.assertRaisesRegex(ValueError, "unknown=.*unexpected"):
            self._write_and_load_config(modified)

        placeholder = dict(self.config_value)
        placeholder["release_id"] = "TO_BE_RESOLVED"
        with self.assertRaisesRegex(ValueError, "unresolved placeholder"):
            self._write_and_load_config(placeholder)

        wrong_target = dict(self.config_value)
        targets = dict(cast(Mapping[str, object], wrong_target["credential_targets"]))
        targets["openrouter_api_key"] = "sk-or-v1-not-a-public-target"
        wrong_target["credential_targets"] = targets
        with self.assertRaisesRegex(ValueError, "dedicated DumbMoney service target"):
            self._write_and_load_config(wrong_target)

    def test_startup_is_credential_and_public_key_fail_closed(self) -> None:
        no_openrouter = FakeCredentialProvider(
            {
                SIGNER_TARGET: bytes(range(32)),
                CLIENT_TARGET: CLIENT_TOKEN,
            }
        )
        with self.assertRaises(CredentialProviderError):
            build_model_gateway_service(
                self.config,
                no_openrouter,
                transport=FakeTransport(),
                clock=self.clock,
            )

        wrong_signer = FakeCredentialProvider(
            {
                OPENROUTER_TARGET: OPENROUTER_SECRET,
                SIGNER_TARGET: b"z" * 32,
                CLIENT_TARGET: CLIENT_TOKEN,
            }
        )
        with self.assertRaisesRegex(
            CredentialProviderError,
            "does not match the pinned public key",
        ):
            build_model_gateway_service(
                self.config,
                wrong_signer,
                transport=FakeTransport(),
                clock=self.clock,
            )

        # Both failed starts released the exclusive data-root lease.
        transport = FakeTransport()
        lease_probe = build_model_gateway_service(
            self.config,
            self.credentials,
            transport=transport,
            clock=self.clock,
        )
        self.assertEqual([], transport.calls)
        lease_probe.close()

    def test_signed_readiness_is_short_lived_separate_and_has_no_broker_authority(
        self,
    ) -> None:
        transport = FakeTransport()
        with build_model_gateway_service(
            self.config,
            self.credentials,
            transport=transport,
            clock=self.clock,
        ) as service:
            envelope = service.write_readiness(self.clock())
            keyring = Ed25519Keyring({self.signer.key_id: self.signer.public_key_bytes})
            verified = envelope.verify(keyring, self.clock())
            self.assertIsInstance(verified, ReadinessDescriptorV1)
            body = verified
            assert isinstance(body, ReadinessDescriptorV1)
            self.assertLessEqual(
                (body.valid_until - body.observed_at).total_seconds(),
                120,
            )
            self.assertEqual("DumbMoneyModelGateway", body.service_name)
            self.assertEqual(
                {
                    "broker": "NONE",
                    "mode": "OFFLINE",
                    "execution_enabled": False,
                },
                dict(body.authority),
            )
            self.assertEqual(self.signer.key_id, envelope.signer_key_id)
            self.assertEqual(
                self.config.gateway_public_key_sha256,
                body.health["gateway_public_key_sha256"],
            )
            self.assertEqual(10_000_000, body.health["daily_budget_microusd"])
            persisted = self.config.readiness_path.read_bytes()
            self.assertNotIn(OPENROUTER_SECRET, persisted)
            self.assertNotIn(CLIENT_TOKEN, persisted)
            self.assertEqual([], transport.calls)

    def test_authenticated_loopback_completion_is_typed_bounded_and_idempotent(
        self,
    ) -> None:
        transport = FakeTransport(
            [self._key_status(), self._completion(), self._generation()]
        )
        service, stop_event, thread = self._start_service(transport)
        try:
            status, body, headers = self._request(
                service,
                "POST",
                COMPLETION_PATH,
                body=self._completion_request(),
                authenticated=False,
            )
            self.assertEqual(401, status)
            self.assertEqual("UNAUTHORIZED", body["code"])
            self.assertEqual("false", headers["x-dumbmoney-retryable"])
            self.assertEqual([], transport.calls)

            status, body, _headers = self._request(
                service,
                "POST",
                COMPLETION_PATH,
                body=self._completion_request(),
                authenticated=True,
            )
            self.assertEqual(200, status)
            self.assertEqual("dumbmoney.model-completion-result.v1", body["schema"])
            self.assertEqual("SETTLED", body["state"])
            self.assertEqual(1_234, body["actual_cost_microusd"])
            self.assertEqual(
                [{"content": "bounded result", "role": "assistant"}],
                body["outputs"],
            )
            self.assertFalse(body["replayed"])
            self.assertEqual(
                ["GET", "POST", "GET"], [call["method"] for call in transport.calls]
            )

            # A caller repeat is handled from durable settlement metadata. The
            # completion is deliberately unavailable and no provider call repeats.
            status, body, _headers = self._request(
                service,
                "POST",
                COMPLETION_PATH,
                body=self._completion_request(),
                authenticated=True,
            )
            self.assertEqual(200, status)
            self.assertTrue(body["replayed"])
            self.assertFalse(body["output_available"])
            self.assertEqual([], body["outputs"])
            self.assertEqual(3, len(transport.calls))

            status, status_body, _headers = self._request(
                service,
                "GET",
                STATUS_PATH,
                authenticated=True,
            )
            self.assertEqual(200, status)
            authority = status_body["authority"]
            self.assertIsInstance(authority, dict)
            authority_mapping = cast(Mapping[str, object], authority)
            self.assertEqual("NONE", authority_mapping["broker"])
            budget = status_body["budget"]
            self.assertIsInstance(budget, dict)
            budget_mapping = cast(Mapping[str, object], budget)
            self.assertEqual(
                {
                    "evaluation": {
                        "committed_microusd": 0,
                        "limit_microusd": 2_000_000,
                    },
                    "operations": {
                        "committed_microusd": 0,
                        "limit_microusd": 2_000_000,
                    },
                    "research": {
                        "committed_microusd": 1_234,
                        "limit_microusd": 6_000_000,
                    },
                },
                budget_mapping["categories"],
            )
        finally:
            self._stop_service(service, stop_event, thread)

    def test_ambiguous_post_is_not_retried_and_blocks_readiness(self) -> None:
        transport = FakeTransport(
            [
                self._key_status(),
                TransportError("fake ambiguous provider outcome"),
            ]
        )
        service, stop_event, thread = self._start_service(transport)
        try:
            status, body, _headers = self._request(
                service,
                "POST",
                COMPLETION_PATH,
                body=self._completion_request("ambiguous-request"),
                authenticated=True,
            )
            self.assertEqual(503, status)
            self.assertEqual("PROVIDER_OUTCOME_UNCERTAIN", body["code"])

            status, body, _headers = self._request(
                service,
                "POST",
                COMPLETION_PATH,
                body=self._completion_request("ambiguous-request"),
                authenticated=True,
            )
            self.assertEqual(409, status)
            self.assertEqual("REQUEST_UNRESOLVED", body["code"])
            self.assertEqual(
                1, sum(call["method"] == "POST" for call in transport.calls)
            )

            status, body, _headers = self._request(
                service,
                "GET",
                HEALTH_READY_PATH,
            )
            self.assertEqual(503, status)
            self.assertEqual("BLOCKED", body["status"])
            envelope = service.write_readiness(self.clock())
            readiness = ReadinessDescriptorV1.from_dict(envelope.body)
            self.assertEqual("BLOCKED", readiness.health["status"])
            self.assertEqual("NONE", readiness.authority["broker"])
            self.assertFalse(readiness.authority["execution_enabled"])
        finally:
            self._stop_service(service, stop_event, thread)

    def test_http_surface_rejects_unknown_fields_and_spoofed_host(self) -> None:
        transport = FakeTransport()
        service, stop_event, thread = self._start_service(transport)
        try:
            invalid = json.loads(self._completion_request())
            invalid["unknown"] = True
            status, body, _headers = self._request(
                service,
                "POST",
                COMPLETION_PATH,
                body=canonical_json_bytes(invalid),
                authenticated=True,
            )
            self.assertEqual(400, status)
            self.assertEqual("INVALID_REQUEST", body["code"])
            self.assertEqual([], transport.calls)

            connection = http.client.HTTPConnection(
                service.endpoint[0],
                service.endpoint[1],
                timeout=5,
            )
            try:
                connection.putrequest("GET", STATUS_PATH, skip_host=True)
                connection.putheader("Host", "example.invalid")
                connection.putheader(
                    "Authorization",
                    f"Bearer {CLIENT_TOKEN.decode()}",
                )
                connection.endheaders()
                response = connection.getresponse()
                body = loads_strict_json(response.read(), "spoofed-host response")
                self.assertEqual(421, response.status)
                self.assertEqual("LOOPBACK_HOST_REQUIRED", body["code"])
            finally:
                connection.close()
        finally:
            self._stop_service(service, stop_event, thread)


if __name__ == "__main__":
    unittest.main()
