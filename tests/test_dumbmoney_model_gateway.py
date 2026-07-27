from __future__ import annotations

import sqlite3
import tempfile
import unittest
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from blunder.fund.model_gateway import (
    AttestationError,
    BudgetCategory,
    BudgetExceeded,
    CATEGORY_LIMITS_MICROUSD,
    CHAT_COMPLETIONS_URL,
    ConfigurationError,
    DuplicateRequest,
    GENERATION_URL,
    GatewayConfig,
    GatewayPaused,
    GatewayRequest,
    HttpResponse,
    KEY_STATUS_URL,
    ModelRoute,
    OpenRouterModelGateway,
    ReconciliationError,
    RequestState,
    SensitivePayloadRejected,
    StdlibHttpsTransport,
    TransportError,
)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeTransport:
    def __init__(self, responses: list[HttpResponse | Exception] | None = None) -> None:
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
            raise AssertionError("unexpected HTTP request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeRawHttpResponse:
    status = 200

    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def read(self, maximum: int) -> bytes:
        return self.raw[:maximum]

    def geturl(self) -> str:
        return KEY_STATUS_URL

    def __enter__(self) -> "FakeRawHttpResponse":
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        return None


class FakeRawOpener:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def open(
        self,
        _request: object,
        *,
        timeout: int,
    ) -> FakeRawHttpResponse:
        assert timeout == 5
        return FakeRawHttpResponse(self.raw)


class StdlibHttpsTransportTests(unittest.TestCase):
    @staticmethod
    def decode(raw: bytes) -> HttpResponse:
        transport = StdlibHttpsTransport()
        transport._opener = cast(Any, FakeRawOpener(raw))
        return transport.request_json(
            method="GET",
            url=KEY_STATUS_URL,
            headers={"accept": "application/json"},
            body=None,
            timeout_seconds=5,
            max_response_bytes=4_096,
        )

    def test_response_rejects_duplicate_keys_at_any_depth(self) -> None:
        with self.assertRaisesRegex(TransportError, "invalid JSON"):
            self.decode(b'{"data":{"limit":10,"limit":9}}')

    def test_response_rejects_nonfinite_numeric_constants(self) -> None:
        for constant in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(constant=constant):
                with self.assertRaisesRegex(TransportError, "invalid JSON"):
                    self.decode(b'{"data":{"limit":' + constant + b"}}")

    def test_response_preserves_decimal_number_parsing(self) -> None:
        response = self.decode(
            b'{"count":1,"value":0.1234567890123456789012345678}'
        )

        self.assertEqual(1, response.body["count"])
        self.assertIsInstance(response.body["value"], Decimal)
        self.assertEqual(
            Decimal("0.1234567890123456789012345678"),
            response.body["value"],
        )


class ModelGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.clock = MutableClock(datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc))
        self.route = ModelRoute(
            model="deepseek/deepseek-r1",
            provider="deepinfra/turbo",
            provider_name="DeepInfra",
            max_output_tokens=1_024,
            max_price={"prompt": 1, "completion": 2},
        )
        self.config = GatewayConfig(
            database_path=self.root / "gateway.db",
            expected_key_label="DumbMoney-Dedicated",
            routes={category: self.route for category in BudgetCategory},
        )
        self.secret = "test-secret-that-must-never-be-persisted"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def key_status(
        self,
        *,
        usage: str = "0",
        byok_usage: str = "0",
        limit: str = "10",
        reset: str = "daily",
        include_byok: bool = True,
        label: str = "DumbMoney-Dedicated",
    ) -> HttpResponse:
        return HttpResponse(
            status=200,
            body={
                "data": {
                    "label": label,
                    "limit": Decimal(limit),
                    "limit_reset": reset,
                    "limit_remaining": Decimal("10") - Decimal(usage),
                    "include_byok_in_limit": include_byok,
                    "usage_daily": Decimal(usage),
                    "byok_usage_daily": Decimal(byok_usage),
                }
            },
            received_bytes=256,
        )

    def completion(
        self,
        generation_id: str = "gen-1",
        cost: str = "0.001234",
        completion_tokens: int = 12,
    ) -> HttpResponse:
        return HttpResponse(
            status=200,
            body={
                "id": generation_id,
                "model": self.route.model,
                "provider": self.route.provider_name,
                "usage": {
                    "cost": Decimal(cost),
                    "prompt_tokens": 20,
                    "completion_tokens": completion_tokens,
                },
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "result"}}],
                "openrouter_metadata": {
                    "requested": self.route.model,
                    "strategy": "direct",
                    "attempt": 1,
                    "endpoints": {
                        "total": 1,
                        "available": [
                            {
                                "provider": self.route.provider_name,
                                "model": self.route.model,
                                "selected": True,
                            }
                        ],
                    },
                    "attempts": [
                        {
                            "provider": self.route.provider_name,
                            "model": self.route.model,
                            "status": 200,
                        }
                    ],
                    "pipeline": [],
                },
            },
            received_bytes=512,
        )

    def generation(self, generation_id: str = "gen-1", cost: str = "0.001234") -> HttpResponse:
        return HttpResponse(
            status=200,
            body={
                "data": {
                    "id": generation_id,
                    "model": self.route.model,
                    "provider_name": self.route.provider_name,
                    "total_cost": Decimal(cost),
                }
            },
            received_bytes=128,
        )

    @staticmethod
    def request(
        request_id: str = "req-1",
        *,
        category: BudgetCategory = BudgetCategory.RESEARCH,
        reserve_microusd: int = 10_000,
        content: str = "Evaluate a public macroeconomic forecasting hypothesis.",
        max_output_tokens: int = 200,
    ) -> GatewayRequest:
        return GatewayRequest(
            request_id=request_id,
            category=category,
            messages=({"role": "user", "content": content},),
            reserve_microusd=reserve_microusd,
            max_output_tokens=max_output_tokens,
        )

    def gateway(self, transport: FakeTransport, **kwargs: Any) -> OpenRouterModelGateway:
        return OpenRouterModelGateway(
            self.config,
            transport=transport,
            secret_loader=lambda: self.secret,
            clock=self.clock,
            **kwargs,
        )

    def test_fixed_ten_dollar_budget_and_60_20_20_allocations(self) -> None:
        self.assertEqual(
            dict(CATEGORY_LIMITS_MICROUSD),
            {
                BudgetCategory.RESEARCH: 6_000_000,
                BudgetCategory.EVALUATION: 2_000_000,
                BudgetCategory.OPERATIONS: 2_000_000,
            },
        )
        with self.assertRaises(ConfigurationError):
            GatewayConfig(
                database_path=self.root / "bad.db",
                expected_key_label="DumbMoney-Dedicated",
                routes={BudgetCategory.RESEARCH: self.route},
            )
        with self.assertRaises(ConfigurationError):
            ModelRoute(
                model="openrouter/auto",
                provider="deepinfra/turbo",
                provider_name="DeepInfra",
                max_output_tokens=100,
                max_price={"prompt": 1},
            )

    def test_success_uses_one_strict_post_and_reconciles_decimal_cost(self) -> None:
        transport = FakeTransport(
            [self.key_status(), self.completion(), self.generation()]
        )
        with self.gateway(transport) as gateway:
            result = gateway.complete(self.request())
            self.assertEqual(result.state, RequestState.SETTLED)
            self.assertEqual(result.actual_cost_microusd, 1_234)
            response = cast(Mapping[str, Any], result.response)
            self.assertEqual(response["choices"][0]["message"]["content"], "result")
            self.assertFalse(result.replayed)
            status = gateway.status()
            self.assertEqual(status.settled_microusd, 1_234)
            self.assertEqual(status.remaining_microusd, 9_998_766)

        self.assertEqual(
            [(call["method"], call["url"].split("?")[0]) for call in transport.calls],
            [
                ("GET", KEY_STATUS_URL),
                ("POST", CHAT_COMPLETIONS_URL),
                ("GET", GENERATION_URL),
            ],
        )
        post = transport.calls[1]
        self.assertEqual(post["body"]["model"], self.route.model)
        self.assertFalse(post["body"]["stream"])
        self.assertNotIn("tools", post["body"])
        self.assertNotIn("plugins", post["body"])
        self.assertNotIn("files", post["body"])
        provider = post["body"]["provider"]
        self.assertEqual(provider["only"], [self.route.provider])
        self.assertEqual(provider["order"], [self.route.provider])
        self.assertFalse(provider["allow_fallbacks"])
        self.assertTrue(provider["require_parameters"])
        self.assertEqual(provider["data_collection"], "deny")
        self.assertTrue(provider["zdr"])
        self.assertEqual(provider["max_price"], {"prompt": 1, "completion": 2})

    def test_settled_request_id_is_idempotent_and_never_resends(self) -> None:
        transport = FakeTransport(
            [self.key_status(), self.completion(), self.generation()]
        )
        with self.gateway(transport) as gateway:
            first = gateway.complete(self.request())
            second = gateway.complete(self.request())
            self.assertFalse(first.replayed)
            self.assertTrue(second.replayed)
            self.assertIsNone(second.response)
            self.assertEqual(second.generation_id, "gen-1")
            with self.assertRaises(DuplicateRequest):
                gateway.complete(
                    self.request(content="Different content with the same id.")
                )
        self.assertEqual(sum(call["method"] == "POST" for call in transport.calls), 1)

    def test_post_timeout_is_not_retried_and_restart_never_resends(self) -> None:
        transport = FakeTransport(
            [self.key_status(), TransportError("simulated ambiguous timeout")]
        )
        gateway = self.gateway(transport)
        with self.assertRaises(TransportError):
            gateway.complete(self.request())
        status = gateway.status()
        self.assertTrue(status.paused)
        self.assertEqual(status.unresolved_requests, ("req-1",))
        self.assertEqual(sum(call["method"] == "POST" for call in transport.calls), 1)
        gateway.close()

        restart_transport = FakeTransport()
        with self.gateway(restart_transport) as restarted:
            with self.assertRaises(DuplicateRequest):
                restarted.complete(self.request())
            with self.assertRaises(GatewayPaused):
                restarted.complete(self.request("req-2"))
        self.assertEqual(restart_transport.calls, [])

    def test_cost_mismatch_locks_reservation_and_pauses(self) -> None:
        transport = FakeTransport(
            [
                self.key_status(),
                self.completion(cost="0.001"),
                self.generation(cost="0.002"),
            ]
        )
        with self.gateway(transport) as gateway:
            with self.assertRaisesRegex(ReconciliationError, "does not equal"):
                gateway.complete(self.request())
            status = gateway.status()
            self.assertTrue(status.paused)
            self.assertEqual(status.reserved_microusd, 10_000)
            self.assertEqual(status.settled_microusd, 0)
            self.assertEqual(status.unresolved_requests, ("req-1",))

    def test_missing_usage_cost_locks_reservation_and_pauses(self) -> None:
        completion = self.completion()
        del completion.body["usage"]["cost"]
        transport = FakeTransport([self.key_status(), completion])
        with self.gateway(transport) as gateway:
            with self.assertRaisesRegex(ReconciliationError, "usage.cost"):
                gateway.complete(self.request())
            self.assertTrue(gateway.status().paused)
        self.assertEqual(sum(call["method"] == "POST" for call in transport.calls), 1)

    def test_invalid_key_attestation_pauses_without_posting(self) -> None:
        transport = FakeTransport([self.key_status(include_byok=False)])
        with self.gateway(transport) as gateway:
            with self.assertRaisesRegex(AttestationError, "BYOK"):
                gateway.complete(self.request())
            self.assertTrue(gateway.status().paused)
        self.assertEqual([call["method"] for call in transport.calls], ["GET"])

    def test_server_usage_drift_pauses_before_second_post(self) -> None:
        transport = FakeTransport(
            [
                self.key_status(),
                self.completion(cost="1"),
                self.generation(cost="1"),
                self.key_status(usage="0.25"),
            ]
        )
        with self.gateway(transport) as gateway:
            gateway.complete(self.request(reserve_microusd=1_000_000))
            with self.assertRaisesRegex(AttestationError, "drift"):
                gateway.complete(self.request("req-2"))
            self.assertTrue(gateway.status().paused)
        self.assertEqual(sum(call["method"] == "POST" for call in transport.calls), 1)

    def test_duplicate_generation_id_is_ambiguous_and_never_retried(self) -> None:
        transport = FakeTransport(
            [
                self.key_status(),
                self.completion(generation_id="gen-duplicate", cost="0.001"),
                self.generation(generation_id="gen-duplicate", cost="0.001"),
                self.key_status(usage="0.001"),
                self.completion(generation_id="gen-duplicate", cost="0.001"),
                self.generation(generation_id="gen-duplicate", cost="0.001"),
            ]
        )
        with self.gateway(transport) as gateway:
            gateway.complete(self.request())
            with self.assertRaisesRegex(ReconciliationError, "already used"):
                gateway.complete(self.request("req-2"))
            self.assertTrue(gateway.status().paused)
            self.assertEqual(gateway.status().unresolved_requests, ("req-2",))
        self.assertEqual(sum(call["method"] == "POST" for call in transport.calls), 2)

    def test_router_reported_plugin_or_tool_is_rejected(self) -> None:
        completion = self.completion()
        completion.body["openrouter_metadata"]["pipeline"] = [
            {"type": "plugin", "name": "web-search"}
        ]
        transport = FakeTransport([self.key_status(), completion])
        with self.gateway(transport) as gateway:
            with self.assertRaisesRegex(ReconciliationError, "plugin or tool"):
                gateway.complete(self.request())
            self.assertTrue(gateway.status().paused)

    def test_sensitive_prompt_is_rejected_before_secret_or_transport(self) -> None:
        secret_loads = 0

        def loader() -> str:
            nonlocal secret_loads
            secret_loads += 1
            return self.secret

        transport = FakeTransport()
        with OpenRouterModelGateway(
            self.config,
            transport=transport,
            secret_loader=loader,
            clock=self.clock,
        ) as gateway:
            for content in (
                "Here is my Robinhood portfolio.",
                "Use this API key to fetch data.",
                "My brokerage account number is 123.",
                "Authorization: Bearer abcdefghijklmnop",
                "Opaque value: qP7_lN2vK9sR4xT8mW1bC6dF0hJ3uY5zA+E=",
                "PEM: " + "-----BEGIN " + "PRIVATE KEY-----",
            ):
                with self.subTest(content=content):
                    with self.assertRaises(SensitivePayloadRejected):
                        gateway.complete(self.request(content=content))
        self.assertEqual(secret_loads, 0)
        self.assertEqual(transport.calls, [])

    def test_redacted_control_snapshot_exposes_budget_only_core_adapter(self) -> None:
        transport = FakeTransport(
            [self.key_status(), self.completion(), self.generation()]
        )
        with self.gateway(transport) as gateway:
            gateway.complete(self.request())
            snapshot = gateway.redacted_control_snapshot()
        self.assertEqual(snapshot["schema"], "dumbmoney.openrouter-budget-status.v1")
        self.assertEqual(snapshot["daily_budget_microusd"], 10_000_000)
        self.assertEqual(snapshot["daily_budget_cents"], 1_000)
        self.assertEqual(snapshot["spent_microusd"], 1_234)
        self.assertEqual(snapshot["spent_cents"], 1)
        self.assertEqual(snapshot["remaining_cents"], 999)
        self.assertEqual(snapshot["resets_at"], "2030-01-03T00:00:00Z")
        self.assertFalse(snapshot["research_paused"])
        self.assertEqual(snapshot["unresolved_count"], 0)
        rendered = repr(snapshot)
        for forbidden in (
            "req-1",
            "gen-1",
            self.route.model,
            self.route.provider,
            self.config.expected_key_label,
            self.secret,
            "result",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_journal_and_database_contain_only_digests_not_payloads_or_secret(self) -> None:
        prompt = "Unique harmless prompt marker 928374."
        transport = FakeTransport(
            [self.key_status(), self.completion(), self.generation()]
        )
        with self.gateway(transport) as gateway:
            gateway.complete(self.request(content=prompt))
            events = gateway.journal_entries()
            self.assertEqual([item["state"] for item in events], [
                "RESERVED",
                "DISPATCHING",
                "SETTLED",
            ])
            rendered = repr(events)
            self.assertNotIn(prompt, rendered)
            self.assertNotIn("result", rendered)
            self.assertNotIn(self.secret, rendered)
        database_bytes = self.config.database_path.read_bytes()
        self.assertNotIn(prompt.encode(), database_bytes)
        self.assertNotIn(b"result", database_bytes)
        self.assertNotIn(self.secret.encode(), database_bytes)

    def test_near_midnight_fence_and_category_ceiling_do_not_reserve(self) -> None:
        self.clock.value = datetime(2030, 1, 2, 23, 57, tzinfo=timezone.utc)
        transport = FakeTransport()
        with self.gateway(transport) as gateway:
            with self.assertRaisesRegex(GatewayPaused, "midnight"):
                gateway.complete(self.request())
            with self.assertRaises(BudgetExceeded):
                gateway.complete(
                    self.request(
                        reserve_microusd=CATEGORY_LIMITS_MICROUSD[
                            BudgetCategory.RESEARCH
                        ]
                        + 1
                    )
                )
            self.assertEqual(gateway.status().unresolved_requests, ())
        self.assertEqual(transport.calls, [])

    def test_category_allocation_is_enforced_after_settlement(self) -> None:
        transport = FakeTransport(
            [
                self.key_status(),
                self.completion(cost="6"),
                self.generation(cost="6"),
            ]
        )
        with self.gateway(transport) as gateway:
            gateway.complete(
                self.request(reserve_microusd=6_000_000)
            )
            with self.assertRaises(BudgetExceeded):
                gateway.complete(
                    self.request(
                        "req-2",
                        reserve_microusd=1,
                    )
                )
        self.assertEqual(sum(call["method"] == "POST" for call in transport.calls), 1)

    def test_sqlite_uses_full_synchronous_durability(self) -> None:
        transport = FakeTransport()
        with self.gateway(transport) as gateway:
            synchronous = gateway._db.execute("PRAGMA synchronous").fetchone()[0]
            journal_mode = gateway._db.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(synchronous, 2)
            self.assertEqual(str(journal_mode).lower(), "wal")
        database = sqlite3.connect(self.config.database_path)
        try:
            states = {
                row[0]
                for row in database.execute(
                    "SELECT DISTINCT state FROM model_requests"
                ).fetchall()
            }
        finally:
            database.close()
        self.assertEqual(states, set())


if __name__ == "__main__":
    unittest.main()
