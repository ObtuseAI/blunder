from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from blunder.fund.canonical import (
    canonical_json_bytes,
    canonical_sha256,
    format_utc,
    loads_strict_json,
)
from blunder.fund.contracts import (
    AlphaPassportV1,
    EvidenceClass,
    EvidenceDecision,
    EvidenceVerdictV1,
    ReadinessDescriptorV1,
    SignedEnvelopeV1,
    Venue,
)
from blunder.fund.crypto import Ed25519Keyring, Ed25519Signer, encode_base64url
from blunder.fund.entrypoint import CredentialProviderError
from blunder.fund.research_entrypoint import (
    CONFIG_SCHEMA,
    ResearchMeshRunnerConfig,
    build_research_mesh_service,
)
from blunder.fund.research_mesh import (
    ALLOCATION_PLAN_SCHEMA,
    ALLOCATOR_APPEND_PATH,
    DOOFUS_BUNDLE_SCHEMA,
    WATERBOY_FORECAST_SCHEMA,
    AllocationPlanError,
    AllocatorCoreClient,
    AmbiguousCoreSubmission,
    CandidateValidationError,
    CoreHttpResponse,
    CoreIdentity,
    CoreReadinessError,
    CoreReadinessVerifier,
    CoreTransport,
    ResearchMesh,
    ResearchMeshState,
    canonical_research_json_bytes,
    canonical_research_sha256,
    loads_research_json,
    parse_allocation_plan,
    validate_candidate,
)


UTC = timezone.utc


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeCredentialProvider:
    def __init__(self, values: Mapping[str, bytes]) -> None:
        self.values = dict(values)
        self.reads: list[str] = []

    def read_bytes(self, target: str) -> bytes:
        self.reads.append(target)
        try:
            return self.values[target]
        except KeyError as exc:
            raise CredentialProviderError("test credential is missing") from exc


class FakeCoreTransport(CoreTransport):
    def __init__(self, responses: list[CoreHttpResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> CoreHttpResponse:
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
            raise AssertionError("unexpected fake Core request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if response.body == b"__DYNAMIC_ACCEPT__":
            envelope = SignedEnvelopeV1.from_dict(
                loads_strict_json(body, "allocator request")
            )
            payload = {
                "schema": "dumbmoney.event-append-result.v1",
                "event_id": envelope.event_id,
                "global_sequence": 41,
                "event_digest": canonical_sha256(
                    ["fake-core-event", envelope.event_id]
                ),
                "duplicate": response.status == 200,
            }
            return CoreHttpResponse(
                status=response.status,
                body=canonical_json_bytes(payload),
            )
        return response


class ResearchMeshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.now = datetime(2030, 1, 2, 12, 0, tzinfo=UTC)
        self.clock = MutableClock(self.now)
        self.core_signer = Ed25519Signer.generate()
        self.readiness_signer = Ed25519Signer.generate()
        self.allocator_signer = Ed25519Signer.generate()
        self.model_gateway_signer = Ed25519Signer.generate()
        self.fund_lock_sha256 = canonical_sha256(["fund-lock"])
        self.service_manifest_sha256 = canonical_sha256(["service-manifest"])
        self.core_readiness_path = self.root / "core-readiness.json"
        self.core_port = 18_731
        self.release_id = "dumbmoney-test-release"
        self.write_core_readiness()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def digest(label: str) -> str:
        return canonical_sha256(["test", label])

    def write_core_readiness(
        self,
        *,
        observed_at: datetime | None = None,
        health_status: str = "READY",
        control_status: str = "LIVE_READY",
        port: int | None = None,
        signer: Ed25519Signer | None = None,
    ) -> SignedEnvelopeV1:
        now = observed_at or self.now
        body = ReadinessDescriptorV1(
            service_name="DumbMoneyCore",
            release_id=self.release_id,
            instance_id="67498f68-0143-48e9-a756-287b374753e1",
            process_id=100,
            generation=0,
            observed_at=now,
            valid_until=now + timedelta(seconds=60),
            endpoint={
                "transport": "http",
                "host": "127.0.0.1",
                "port": port or self.core_port,
                "base_path": "/",
            },
            fund_lock_sha256=self.fund_lock_sha256,
            service_manifest_sha256=self.service_manifest_sha256,
            authority={
                "broker": "NONE",
                "mode": "OFFLINE",
                "execution_enabled": False,
            },
            health={
                "status": health_status,
                "control_status": control_status,
            },
            capabilities=("allocator-signed-capital-requests",),
        )
        envelope = SignedEnvelopeV1.issue(
            body,
            source_id="DumbMoneyCore",
            source_sequence=1,
            correlation_id="core-readiness-test",
            causation_id=None,
            nonce=self.digest("core-readiness-nonce"),
            not_before=now,
            expires_at=now + timedelta(seconds=60),
            signer=signer or self.core_signer,
        )
        self.core_readiness_path.write_bytes(
            canonical_json_bytes(envelope.to_dict()) + b"\n"
        )
        return envelope

    def doofus_bundle(
        self,
        *,
        instrument: str = "event_contract:KXTEST-2030",
    ) -> tuple[dict[str, object], AlphaPassportV1]:
        passport = AlphaPassportV1(
            passport_id="doofus-candidate-2030",
            strategy_lineage_id="doofus-lineage-2030",
            venue=Venue.DUMMY_KALSHI,
            strategy_hash=self.digest("strategy"),
            artifact_hashes=tuple(
                sorted(
                    (
                        self.digest("doofus-source"),
                        self.digest("doofus-trials"),
                    )
                )
            ),
            evidence_verdict_hashes=(),
            intended_instruments=(instrument,),
            maximum_loss_cents=250,
            evidence_class=EvidenceClass.REPLAY,
            created_at=self.now - timedelta(minutes=5),
            expires_at=self.now + timedelta(minutes=30),
        )
        passport_digest = canonical_sha256(passport.to_dict())
        verdicts = []
        for court in (
            "integrity",
            "statistics",
            "economics",
            "adversarial_operations",
        ):
            verdict = EvidenceVerdictV1(
                verdict_id=f"candidate-{court}-2030",
                passport_digest=passport_digest,
                evaluator_id=self.digest("unsigned-candidate-evaluator"),
                court=court,  # type: ignore[arg-type]
                decision=EvidenceDecision.PASS,
                evidence_class=EvidenceClass.REPLAY,
                artifact_hashes=(self.digest(f"evidence-{court}"),),
                reason_codes=(f"DOOFUS_{court.upper()}_CANDIDATE",),
                effective_trial_count=50,
                evaluated_at=self.now - timedelta(minutes=2),
                expires_at=passport.expires_at,
            )
            verdicts.append(verdict.to_dict())
        return (
            {
                "schema_id": DOOFUS_BUNDLE_SCHEMA,
                "alpha_passport": passport.to_dict(),
                "evidence_verdicts": verdicts,
                "signed": False,
                "grants_live_authority": False,
                "grants_capital_authority": False,
            },
            passport,
        )

    def waterboy_forecast(self) -> dict[str, object]:
        as_of = self.now - timedelta(minutes=1)
        expires = self.now + timedelta(minutes=5)
        starts = self.now + timedelta(hours=1)
        body: dict[str, object] = {
            "schema": WATERBOY_FORECAST_SCHEMA,
            "candidate_only": True,
            "order_authority": False,
            "capital_authority": False,
            "as_of": as_of.isoformat(),
            "expires_at": expires.isoformat(),
            "observation_set_digest": self.digest("observation-set"),
            "target": {
                "game_id": "game-2030",
                "league": "nba",
                "starts_at": starts.isoformat(),
                "home_team_id": "HOME",
                "away_team_id": "AWAY",
                "home_team_name": "Home",
                "away_team_name": "Away",
                "neutral_site": False,
            },
            "model": {
                "model_id": "waterboy.projection-engine",
                "model_version": "projection-v1",
                "champion_id": "waterboy.projection-incumbent",
                "champion_version": "projection-v1",
                "seed": 7,
                "draws": 100,
                "fidelity": "F0",
            },
            "distributions": {
                "home_score": {
                    "mean": 110.2,
                    "lower": 90,
                    "upper": 130,
                    "interval_mass": 0.8,
                },
                "away_score": {
                    "mean": 105.1,
                    "lower": 88,
                    "upper": 125,
                    "interval_mass": 0.8,
                },
                "total_score": {
                    "mean": 215.3,
                    "lower": 190,
                    "upper": 245,
                    "interval_mass": 0.8,
                },
                "expected_margin": 5.1,
                "outcome": {
                    "home_win": 0.60,
                    "away_win": 0.39,
                    "tie": 0.01,
                },
            },
            "confidence_status": "MEDIUM_DATA_COVERAGE",
            "calibration_status": "provisional_not_independently_calibrated",
            "evidence_status": "historical_form_provisional_not_calibrated",
            "freshness_status": "fresh_as_of_cutoff",
            "provenance": {
                "target_revision_id": self.digest("target-revision"),
                "observation_count": 20,
                "sources": [
                    {
                        "source": "licensed_fixture",
                        "observation_count": 20,
                        "latest_observed_at": (
                            as_of - timedelta(minutes=1)
                        ).isoformat(),
                        "source_urls": ["https://example.test/terms"],
                        "payload_set_digest": self.digest("payload-set"),
                    }
                ],
            },
            "data_rights": {
                "status": "source_terms_and_local_authorization_must_be_verified",
                "source_terms_apply": True,
                "redistribution_status": "not_granted_by_this_envelope",
                "model_training_status": "not_granted_by_this_envelope",
                "references": ["https://example.test/terms"],
            },
            "limitations": [
                "Candidate-only research artifact; it grants no order or capital authority."
            ],
        }
        return {
            **body,
            "canonical_digest": canonical_research_sha256(body),
        }

    def allocation_plan(
        self,
        *,
        candidate_digest: str,
        passport_digest: str,
        supporting: tuple[str, ...] = (),
        plan_id: str = "allocation-plan-2030",
    ) -> dict[str, object]:
        return {
            "schema": ALLOCATION_PLAN_SCHEMA,
            "plan_id": plan_id,
            "candidate_digest": candidate_digest,
            "supporting_candidate_digests": list(supporting),
            "mandate_id": self.digest("mandate"),
            "venue": Venue.DUMMY_KALSHI.value,
            "account_hash": self.digest("account"),
            "passport_digest": passport_digest,
            "promotion_digest": self.digest("promotion"),
            "authorized_instruments": ["event_contract:KXTEST-2030"],
            "correlation_cluster": "sports-event-cluster",
            "max_order_risk_cents": 100,
            "max_open_risk_cents": 300,
            "max_correlated_risk_cents": 200,
            "max_daily_loss_cents": 500,
            "max_open_orders": 2,
            "policy_epoch": 1,
            "created_at": format_utc(self.now - timedelta(minutes=1)),
            "expires_at": format_utc(self.now + timedelta(minutes=10)),
        }

    def core_identity(self) -> CoreIdentity:
        return CoreIdentity(
            readiness_path=self.core_readiness_path,
            public_key=self.core_signer.public_key_bytes,
            release_id=self.release_id,
            fund_lock_sha256=self.fund_lock_sha256,
            service_manifest_sha256=self.service_manifest_sha256,
            host="127.0.0.1",
        )

    def mesh(
        self,
        transport: FakeCoreTransport,
    ) -> tuple[ResearchMesh, ResearchMeshState]:
        identity = self.core_identity()
        client = AllocatorCoreClient(
            identity=identity,
            readiness_verifier=CoreReadinessVerifier(identity),
            transport=transport,
            bearer_token="allocator-bearer-token-0123456789abcdef",
        )
        state = ResearchMeshState((self.root / "mesh.db").resolve())
        mesh = ResearchMesh(
            candidate_inbox=(self.root / "candidate-inbox").resolve(),
            allocation_inbox=(self.root / "allocation-inbox").resolve(),
            state=state,
            allocator_signer=self.allocator_signer,
            core_client=client,
            clock=self.clock,
        )
        return mesh, state

    def test_candidate_validators_preserve_non_authority_and_pit(self) -> None:
        doofus, passport = self.doofus_bundle()
        validated = validate_candidate(doofus, observed_at=self.now)
        self.assertEqual(validated.source_kind, "doofus")
        self.assertEqual(
            validated.passport_digest,
            canonical_sha256(passport.to_dict()),
        )
        self.assertEqual(validated.strategy_hash, passport.strategy_hash)

        waterboy = self.waterboy_forecast()
        sports = validate_candidate(waterboy, observed_at=self.now)
        self.assertEqual(sports.source_kind, "waterboy")
        self.assertIsNone(sports.passport_digest)
        self.assertIsNone(sports.strategy_hash)

        widened = copy.deepcopy(waterboy)
        widened["capital_authority"] = True
        with self.assertRaises(CandidateValidationError):
            validate_candidate(widened, observed_at=self.now)

        future_source = copy.deepcopy(waterboy)
        future_source["provenance"]["sources"][0]["latest_observed_at"] = (
            self.now + timedelta(minutes=1)
        ).isoformat()
        body = dict(future_source)
        body.pop("canonical_digest")
        future_source["canonical_digest"] = canonical_research_sha256(body)
        with self.assertRaisesRegex(
            CandidateValidationError,
            "crosses the as-of cutoff",
        ):
            validate_candidate(future_source, observed_at=self.now)

        mismatched = copy.deepcopy(doofus)
        mismatched["evidence_verdicts"][0]["passport_digest"] = self.digest(
            "other-passport"
        )
        with self.assertRaisesRegex(
            CandidateValidationError,
            "does not bind",
        ):
            validate_candidate(mismatched, observed_at=self.now)

    def test_allocation_plan_has_no_caller_supplied_strategy(self) -> None:
        doofus, passport = self.doofus_bundle()
        candidate = validate_candidate(doofus, observed_at=self.now)
        plan = self.allocation_plan(
            candidate_digest=candidate.candidate_digest,
            passport_digest=canonical_sha256(passport.to_dict()),
        )
        parsed = parse_allocation_plan(plan)
        self.assertFalse(hasattr(parsed, "strategy_hash"))
        widened = {**plan, "strategy_hash": self.digest("caller-selected-strategy")}
        with self.assertRaisesRegex(AllocationPlanError, "keys are invalid"):
            parse_allocation_plan(widened)

    def test_core_readiness_is_signed_current_and_exact(self) -> None:
        identity = self.core_identity()
        verifier = CoreReadinessVerifier(identity)
        body = verifier.verify(self.now)
        self.assertEqual(body.service_name, "DumbMoneyCore")

        self.write_core_readiness(health_status="DEGRADED")
        with self.assertRaisesRegex(CoreReadinessError, "not LIVE_READY"):
            verifier.verify(self.now)
        self.write_core_readiness(signer=Ed25519Signer.generate())
        with self.assertRaises(CoreReadinessError):
            verifier.verify(self.now)

    def test_cycle_submits_one_singleton_request_and_never_promotes(self) -> None:
        transport = FakeCoreTransport([CoreHttpResponse(201, b"__DYNAMIC_ACCEPT__")])
        mesh, state = self.mesh(transport)
        try:
            doofus, passport = self.doofus_bundle()
            waterboy = self.waterboy_forecast()
            candidate = validate_candidate(doofus, observed_at=self.now)
            sports = validate_candidate(waterboy, observed_at=self.now)
            (mesh.candidate_inbox / "doofus.json").write_bytes(
                canonical_research_json_bytes(doofus)
            )
            (mesh.candidate_inbox / "waterboy.json").write_bytes(
                canonical_research_json_bytes(waterboy)
            )
            plan = self.allocation_plan(
                candidate_digest=candidate.candidate_digest,
                passport_digest=canonical_sha256(passport.to_dict()),
                supporting=(sports.candidate_digest,),
            )
            (mesh.allocation_inbox / "plan.json").write_bytes(
                canonical_json_bytes(plan)
            )

            result = mesh.process_cycle()
            self.assertEqual(result.candidates_accepted, 2)
            self.assertEqual(result.plans_accepted, 1)
            self.assertEqual(result.requests_core_accepted, 1)
            self.assertEqual(len(transport.calls), 1)
            call = transport.calls[0]
            self.assertEqual(call["method"], "POST")
            self.assertTrue(str(call["url"]).endswith(ALLOCATOR_APPEND_PATH))
            self.assertNotIn(
                "dummy",
                str(call["headers"]).lower().replace("dumbmoney", ""),
            )
            envelope = SignedEnvelopeV1.from_dict(
                loads_strict_json(
                    call["body"],
                    "captured allocator envelope",
                )
            )
            keyring = Ed25519Keyring()
            keyring.register(self.allocator_signer.public_key_bytes)
            request = envelope.verify(keyring, self.now)
            self.assertEqual(len(request.strategy_hashes), 1)
            self.assertEqual(len(request.passport_hashes), 1)
            self.assertEqual(len(request.promotion_hashes), 1)
            self.assertEqual(request.strategy_hashes, (passport.strategy_hash,))
            self.assertEqual(
                request.passport_hashes,
                (canonical_sha256(passport.to_dict()),),
            )
            self.assertNotIn(sports.candidate_digest, repr(request.to_dict()))
            self.assertEqual(
                mesh.status_snapshot()["authority"],
                {
                    "candidate_intake": True,
                    "evaluation": False,
                    "promotion": False,
                    "capital_grant": False,
                    "broker": False,
                    "model_provider": False,
                },
            )
            second = mesh.process_cycle()
            self.assertEqual(second.requests_core_accepted, 0)
            self.assertEqual(len(transport.calls), 1)
            counts = state.status_counts()
            self.assertEqual(counts["plans_core_accepted_request"], 1)
        finally:
            state.close()

    def test_waterboy_cannot_anchor_allocation(self) -> None:
        transport = FakeCoreTransport([])
        mesh, state = self.mesh(transport)
        try:
            waterboy = self.waterboy_forecast()
            sports = validate_candidate(waterboy, observed_at=self.now)
            (mesh.candidate_inbox / "waterboy.json").write_bytes(
                canonical_research_json_bytes(waterboy)
            )
            plan = self.allocation_plan(
                candidate_digest=sports.candidate_digest,
                passport_digest=self.digest("invented-passport"),
            )
            (mesh.allocation_inbox / "plan.json").write_bytes(
                canonical_json_bytes(plan)
            )
            result = mesh.process_cycle()
            self.assertEqual(result.requests_core_accepted, 0)
            self.assertEqual(result.plans_rejected, 1)
            self.assertEqual(transport.calls, [])
            self.assertEqual(state.status_counts()["plans_rejected_local"], 1)
        finally:
            state.close()

    def test_ambiguous_post_is_durably_never_retried(self) -> None:
        transport = FakeCoreTransport(
            [TimeoutError("fake timeout after possible Core receipt")]
        )
        mesh, state = self.mesh(transport)
        try:
            doofus, passport = self.doofus_bundle()
            candidate = validate_candidate(doofus, observed_at=self.now)
            (mesh.candidate_inbox / "doofus.json").write_bytes(
                canonical_research_json_bytes(doofus)
            )
            plan = self.allocation_plan(
                candidate_digest=candidate.candidate_digest,
                passport_digest=canonical_sha256(passport.to_dict()),
            )
            (mesh.allocation_inbox / "plan.json").write_bytes(
                canonical_json_bytes(plan)
            )
            first = mesh.process_cycle()
            self.assertEqual(first.requests_ambiguous, 1)
            self.assertEqual(len(transport.calls), 1)
            second = mesh.process_cycle()
            self.assertEqual(second.requests_ambiguous, 0)
            self.assertEqual(len(transport.calls), 1)
            self.assertTrue(mesh.status_snapshot()["blocked_by_ambiguous_submission"])
            self.assertEqual(
                state.status_counts()["plans_ambiguous_no_retry"],
                1,
            )
        finally:
            state.close()

    def test_restart_converts_orphaned_dispatch_to_ambiguous_no_retry(self) -> None:
        transport = FakeCoreTransport([])
        mesh, state = self.mesh(transport)
        database_path = state.path
        doofus, passport = self.doofus_bundle()
        candidate = validate_candidate(doofus, observed_at=self.now)
        raw_plan = self.allocation_plan(
            candidate_digest=candidate.candidate_digest,
            passport_digest=canonical_sha256(passport.to_dict()),
        )
        plan = parse_allocation_plan(raw_plan)
        state.record_plan(
            file_sha256=hashlib.sha256(canonical_json_bytes(raw_plan)).hexdigest(),
            plan=plan,
            raw_plan=raw_plan,
            observed_at=self.now,
        )
        envelope = state.reserve_dispatch(
            plan=plan,
            envelope_factory=lambda sequence: mesh._request_envelope(
                plan,
                candidate,
                source_sequence=sequence,
                observed_at=self.now,
            ),
            observed_at=self.now,
        )
        self.assertIsNotNone(envelope)
        self.assertEqual(state.status_counts()["plans_dispatching"], 1)
        state.close()

        recovered = ResearchMeshState(database_path)
        try:
            self.assertEqual(
                recovered.status_counts()["plans_ambiguous_no_retry"],
                1,
            )
            self.assertEqual(recovered.pending_plans(), ())
        finally:
            recovered.close()

    def test_stale_core_readiness_never_reaches_transport(self) -> None:
        stale = self.now - timedelta(minutes=5)
        self.write_core_readiness(observed_at=stale)
        transport = FakeCoreTransport([])
        identity = self.core_identity()
        client = AllocatorCoreClient(
            identity=identity,
            readiness_verifier=CoreReadinessVerifier(identity),
            transport=transport,
            bearer_token="allocator-bearer-token-0123456789abcdef",
        )
        doofus, passport = self.doofus_bundle()
        candidate = validate_candidate(doofus, observed_at=self.now)
        plan = parse_allocation_plan(
            self.allocation_plan(
                candidate_digest=candidate.candidate_digest,
                passport_digest=canonical_sha256(passport.to_dict()),
            )
        )
        request = ResearchMesh(
            candidate_inbox=(self.root / "unused-candidates").resolve(),
            allocation_inbox=(self.root / "unused-plans").resolve(),
            state=ResearchMeshState((self.root / "unused.db").resolve()),
            allocator_signer=self.allocator_signer,
            core_client=client,
            clock=self.clock,
        )
        try:
            envelope = request._request_envelope(
                plan,
                candidate,
                source_sequence=1,
                observed_at=self.now,
            )
            with self.assertRaises(CoreReadinessError):
                client.submit(envelope, observed_at=self.now)
            self.assertEqual(transport.calls, [])
        finally:
            request.state.close()

    def config_files(self) -> tuple[dict[str, object], FakeCredentialProvider]:
        data_root = (self.root / "service-data").resolve()
        candidate_inbox = (self.root / "service-candidates").resolve()
        allocation_inbox = (self.root / "service-plans").resolve()
        model_request_inbox = (self.root / "model-requests").resolve()
        model_response_outbox = (self.root / "model-responses").resolve()
        model_outcome_outbox = (self.root / "model-outcomes").resolve()
        readiness_path = (self.root / "mesh-readiness.json").resolve()
        model_gateway_readiness_path = (
            self.root / "model-gateway-readiness.json"
        ).resolve()
        fund_lock = self.root / "fund.lock.json"
        service_manifest = self.root / "services.json"
        fund_lock.write_bytes(b'{"release":"test"}\n')
        service_manifest.write_bytes(b'{"services":[]}\n')
        key_files: dict[str, tuple[Path, bytes]] = {
            "mesh": (
                self.root / "mesh.pub",
                self.readiness_signer.public_key_bytes,
            ),
            "allocator": (
                self.root / "allocator.pub",
                self.allocator_signer.public_key_bytes,
            ),
            "core": (
                self.root / "core.pub",
                self.core_signer.public_key_bytes,
            ),
            "model_gateway": (
                self.root / "model-gateway.pub",
                self.model_gateway_signer.public_key_bytes,
            ),
        }
        for path, public_key in key_files.values():
            path.write_text(
                encode_base64url(public_key) + "\n",
                encoding="ascii",
            )
        config: dict[str, object] = {
            "schema": CONFIG_SCHEMA,
            "data_root": str(data_root),
            "candidate_inbox": str(candidate_inbox),
            "allocation_inbox": str(allocation_inbox),
            "readiness_path": str(readiness_path),
            "research_mesh_public_key_path": str(key_files["mesh"][0]),
            "allocator_public_key_path": str(key_files["allocator"][0]),
            "core_public_key_path": str(key_files["core"][0]),
            "core_readiness_path": str(self.core_readiness_path),
            "model_request_inbox": str(model_request_inbox),
            "model_response_outbox": str(model_response_outbox),
            "model_outcome_outbox": str(model_outcome_outbox),
            "model_gateway_readiness_path": str(model_gateway_readiness_path),
            "model_gateway_public_key_path": str(key_files["model_gateway"][0]),
            "fund_lock_path": str(fund_lock),
            "service_manifest_path": str(service_manifest),
            "bind_port": 0,
            "release_id": self.release_id,
            "research_mesh_public_key_sha256": hashlib.sha256(
                key_files["mesh"][0].read_bytes()
            ).hexdigest(),
            "allocator_public_key_sha256": hashlib.sha256(
                key_files["allocator"][0].read_bytes()
            ).hexdigest(),
            "core_public_key_sha256": hashlib.sha256(
                key_files["core"][0].read_bytes()
            ).hexdigest(),
            "model_gateway_public_key_sha256": hashlib.sha256(
                key_files["model_gateway"][0].read_bytes()
            ).hexdigest(),
            "model_gateway_runner_config_sha256": self.digest(
                "model-gateway-runner-config"
            ),
            "fund_lock_sha256": hashlib.sha256(fund_lock.read_bytes()).hexdigest(),
            "service_manifest_sha256": hashlib.sha256(
                service_manifest.read_bytes()
            ).hexdigest(),
            "readiness_ttl_seconds": 30,
            "processing_interval_milliseconds": 250,
            "core_timeout_seconds": 2,
            "model_gateway_timeout_seconds": 10,
            "max_input_bytes": 1_048_576,
            "model_request_max_bytes": 1_048_576,
            "model_response_max_bytes": 1_000_000,
            "model_prompt_max_characters": 100_000,
            "model_max_output_tokens": 1_024,
            "model_max_reserve_microusd": 6_000_000,
            "credential_targets": {
                "research_mesh_signing_seed": "DumbMoney/ResearchMesh/SigningSeed",
                "allocator_signing_seed": "DumbMoney/ResearchMesh/AllocatorSeed",
                "allocator_bearer_token": "DumbMoney/Core/AllocatorBearerToken",
                "model_gateway_client_token": (
                    "DumbMoney/ModelGateway/ClientBearerToken"
                ),
            },
        }
        provider = FakeCredentialProvider(
            {
                "DumbMoney/ResearchMesh/SigningSeed": (
                    self.readiness_signer._private_key.private_bytes_raw()
                ),
                "DumbMoney/ResearchMesh/AllocatorSeed": (
                    self.allocator_signer._private_key.private_bytes_raw()
                ),
                "DumbMoney/Core/AllocatorBearerToken": (
                    b"allocator-bearer-token-0123456789abcdef"
                ),
                "DumbMoney/ModelGateway/ClientBearerToken": (
                    b"model-gateway-client-token-0123456789abcdef"
                ),
            }
        )
        return config, provider

    def test_config_is_exact_digest_pinned_and_has_no_broker_or_model_secret(
        self,
    ) -> None:
        value, provider = self.config_files()
        config_path = (self.root / "research-config.json").resolve()
        raw = canonical_json_bytes(value) + b"\n"
        config_path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        config = ResearchMeshRunnerConfig.load(
            config_path,
            expected_file_sha256=digest,
        )
        self.assertEqual(config.runner_config_sha256, digest)
        with self.assertRaisesRegex(ValueError, "does not match"):
            ResearchMeshRunnerConfig.load(
                config_path,
                expected_file_sha256=self.digest("wrong-config"),
            )
        widened = {
            **value,
            "credential_targets": {
                **value["credential_targets"],
                "openrouter_api_key": "DumbMoney/OpenRouter/Key",
                "robinhood_password": "DumbMoney/Robinhood/Password",
            },
        }
        with self.assertRaisesRegex(ValueError, "exactly the four"):
            ResearchMeshRunnerConfig.from_dict(widened)

        fake_transport = FakeCoreTransport([])
        with build_research_mesh_service(
            config,
            provider,
            transport=fake_transport,
            clock=self.clock,
        ) as service:
            envelope = service.write_readiness(self.now)
            self.assertNotEqual(
                envelope.signer_key_id,
                self.allocator_signer.key_id,
            )
            keyring = Ed25519Keyring()
            keyring.register(self.readiness_signer.public_key_bytes)
            body = envelope.verify(keyring, self.now)
            self.assertEqual(body.service_name, "DumbMoneyResearchMesh")
            self.assertEqual(
                dict(body.authority),
                {
                    "broker": "NONE",
                    "mode": "OFFLINE",
                    "execution_enabled": False,
                },
            )
            self.assertFalse(body.health["evaluation_authority"])
            self.assertFalse(body.health["promotion_authority"])
            self.assertFalse(body.health["capital_grant_authority"])
            self.assertEqual(
                set(provider.reads),
                {
                    "DumbMoney/ResearchMesh/SigningSeed",
                    "DumbMoney/ResearchMesh/AllocatorSeed",
                    "DumbMoney/Core/AllocatorBearerToken",
                    "DumbMoney/ModelGateway/ClientBearerToken",
                },
            )

    def test_invalid_core_response_after_dispatch_is_ambiguous(self) -> None:
        transport = FakeCoreTransport(
            [
                CoreHttpResponse(
                    201,
                    canonical_json_bytes(
                        {
                            "schema": "dumbmoney.event-append-result.v1",
                            "event_id": self.digest("wrong-event"),
                            "global_sequence": 1,
                            "event_digest": self.digest("event"),
                            "duplicate": False,
                        }
                    ),
                )
            ]
        )
        identity = self.core_identity()
        client = AllocatorCoreClient(
            identity=identity,
            readiness_verifier=CoreReadinessVerifier(identity),
            transport=transport,
            bearer_token="allocator-bearer-token-0123456789abcdef",
        )
        doofus, passport = self.doofus_bundle()
        candidate = validate_candidate(doofus, observed_at=self.now)
        plan = parse_allocation_plan(
            self.allocation_plan(
                candidate_digest=candidate.candidate_digest,
                passport_digest=canonical_sha256(passport.to_dict()),
            )
        )
        state = ResearchMeshState((self.root / "response.db").resolve())
        mesh = ResearchMesh(
            candidate_inbox=(self.root / "response-candidates").resolve(),
            allocation_inbox=(self.root / "response-plans").resolve(),
            state=state,
            allocator_signer=self.allocator_signer,
            core_client=client,
            clock=self.clock,
        )
        try:
            envelope = mesh._request_envelope(
                plan,
                candidate,
                source_sequence=1,
                observed_at=self.now,
            )
            with self.assertRaises(AmbiguousCoreSubmission):
                client.submit(envelope, observed_at=self.now)
            self.assertEqual(len(transport.calls), 1)
        finally:
            state.close()

    def test_research_json_rejects_duplicates_and_nonfinite_numbers(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            loads_research_json(b'{"schema":"x","schema":"y"}', "test")
        with self.assertRaisesRegex(ValueError, "non-finite"):
            loads_research_json(b'{"value":NaN}', "test")


if __name__ == "__main__":
    unittest.main()
