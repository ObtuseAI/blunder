from __future__ import annotations

import tempfile
import threading
import unittest
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from blunder.fund.canonical import (
    canonical_json_bytes,
    canonical_sha256,
    load_strict_json,
)
from blunder.fund.cas import ContentAddressedStore
from blunder.fund.contracts import (
    AlphaPassportV1,
    CapitalRequestV1,
    CellJournalHeadAnchorV1,
    DesiredMode,
    DesiredModeV1,
    EvidenceClass,
    EvidenceDecision,
    EvidenceVerdictV1,
    ExecutionIntentV1,
    DeploymentReceiptV1,
    InitialReconciliationReceiptV1,
    KillStateV1,
    OperatingMandateV1,
    OperatingMode,
    PromotionCertificateV1,
    PromotionStage,
    ReconciliationStatus,
    SignedEnvelopeV1,
    Venue,
    VenueRiskSnapshotV1,
)
from blunder.fund.crypto import Ed25519Keyring, Ed25519Signer, SignatureVerificationError
from blunder.fund.ledger import EventLedger
from blunder.fund.policy import (
    CapitalRequest,
    PolicyViolation,
    RiskPolicyEngine,
    RiskPolicyV1,
)
from blunder.fund.runtime import (
    AuthorityError,
    DeploymentAuthorityBindings,
    EvidenceResolutionError,
    FundControlPlane,
)
from blunder.fund.service import (
    CONTROL_SNAPSHOT_ALIAS,
    CONTROL_SNAPSHOT_PATH,
    ApiResponse,
    FundApi,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FundRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
        self.clock = MutableClock(self.now)
        self.operator = Ed25519Signer.generate()
        self.core = Ed25519Signer.generate()
        self.evaluator = Ed25519Signer.generate()
        self.promoter = Ed25519Signer.generate()
        self.allocator = Ed25519Signer.generate()
        self.research = Ed25519Signer.generate()
        self.dummy_venue_signer = Ed25519Signer.generate()
        self.dopey_venue_signer = Ed25519Signer.generate()
        self.keyring = Ed25519Keyring()
        for signer in (
            self.operator,
            self.core,
            self.evaluator,
            self.promoter,
            self.allocator,
            self.research,
            self.dummy_venue_signer,
            self.dopey_venue_signer,
        ):
            self.keyring.register(signer.public_key_bytes)
        self.policy = RiskPolicyV1.load(
            REPO_ROOT / "configs" / "dumbmoney" / "risk-policy.v1.json"
        )
        self.cas = ContentAddressedStore(self.root / "objects")
        self.release_manifest_sha256 = self.cas.put_json(
            {"schema": "test.sealed-release.v1", "release_id": "runtime-tests"}
        )
        self.deployment_bindings = DeploymentAuthorityBindings(
            release_manifest_sha256=self.release_manifest_sha256,
            core_runner_config_sha256=canonical_sha256(
                ["runtime-test", "core-runner-config"]
            ),
            fund_lock_sha256=canonical_sha256(
                ["runtime-test", "fund-lock"]
            ),
        )
        self.ledger = EventLedger(
            self.root / "control.db",
            self.cas,
            self.keyring,
            clock=self.clock,
        )
        self.control = FundControlPlane(
            self.ledger,
            self.keyring,
            self.core,
            RiskPolicyEngine(self.policy),
            operator_key_ids=frozenset({self.operator.key_id}),
            evaluator_key_ids=frozenset({self.evaluator.key_id}),
            promoter_key_ids=frozenset({self.promoter.key_id}),
            allocator_key_ids=frozenset({self.allocator.key_id}),
            research_key_ids=frozenset({self.research.key_id}),
            venue_key_ids={
                Venue.DUMMY_KALSHI: frozenset({self.dummy_venue_signer.key_id}),
                Venue.DOPEY_ROBINHOOD: frozenset({self.dopey_venue_signer.key_id}),
            },
            deployment_bindings=self.deployment_bindings,
            clock=self.clock,
        )
        self.sequence: defaultdict[str, int] = defaultdict(int)
        self.accounts = {
            Venue.DUMMY_KALSHI.value: canonical_sha256(["account", "dummy"]),
            Venue.DOPEY_ROBINHOOD.value: canonical_sha256(["account", "dopey"]),
        }
        self.mandate_id = canonical_sha256(["mandate", "owner", 1])
        self.reconciliations: dict[Venue, InitialReconciliationReceiptV1] = {}

    def control_for_bindings(
        self,
        bindings: DeploymentAuthorityBindings,
    ) -> FundControlPlane:
        return FundControlPlane(
            self.ledger,
            self.keyring,
            self.core,
            RiskPolicyEngine(self.policy),
            operator_key_ids=frozenset({self.operator.key_id}),
            evaluator_key_ids=frozenset({self.evaluator.key_id}),
            promoter_key_ids=frozenset({self.promoter.key_id}),
            allocator_key_ids=frozenset({self.allocator.key_id}),
            research_key_ids=frozenset({self.research.key_id}),
            venue_key_ids={
                Venue.DUMMY_KALSHI: frozenset(
                    {self.dummy_venue_signer.key_id}
                ),
                Venue.DOPEY_ROBINHOOD: frozenset(
                    {self.dopey_venue_signer.key_id}
                ),
            },
            deployment_bindings=bindings,
            clock=self.clock,
        )

    def tearDown(self) -> None:
        self.ledger.close()
        self.temporary.cleanup()

    def signed(
        self,
        body,
        signer: Ed25519Signer,
        source: str,
        *,
        envelope_expires_at: datetime | None = None,
    ) -> SignedEnvelopeV1:
        self.sequence[source] += 1
        return SignedEnvelopeV1.issue(
            body,
            source_id=source,
            source_sequence=self.sequence[source],
            correlation_id="runtime-test",
            causation_id=None,
            nonce=f"{source}-nonce-{self.sequence[source]}",
            not_before=self.now,
            expires_at=envelope_expires_at or self.now + timedelta(minutes=10),
            signer=signer,
        )

    def mandate(self, **overrides) -> OperatingMandateV1:
        values = dict(
            mandate_id=self.mandate_id,
            operating_mode=OperatingMode.PERSONAL_PROP,
            account_hashes=self.accounts,
            allowed_venues=tuple(sorted(self.accounts)),
            allowed_instruments=("equity", "event_contract", "option"),
            nav_cents=100_000,
            combined_capital_bps=2500,
            per_venue_capital_bps={
                Venue.DUMMY_KALSHI.value: 1250,
                Venue.DOPEY_ROBINHOOD.value: 1250,
            },
            per_idea_loss_bps=100,
            correlated_loss_bps=300,
            combined_daily_loss_bps=300,
            per_venue_daily_loss_bps={
                Venue.DUMMY_KALSHI.value: 150,
                Venue.DOPEY_ROBINHOOD.value: 150,
            },
            high_water_drawdown_bps=1000,
            openrouter_daily_budget_cents=1000,
            policy_epoch=1,
            not_before=self.now,
            expires_at=self.now + timedelta(days=30),
        )
        values.update(overrides)
        return OperatingMandateV1(**values)

    def request(
        self,
        passport_digest: str,
        strategy_digest: str,
        promotion_digest: str,
        **overrides,
    ) -> CapitalRequest:
        values = dict(
            venue=Venue.DUMMY_KALSHI,
            account_hash=self.accounts[Venue.DUMMY_KALSHI.value],
            strategy_hashes=(strategy_digest,),
            passport_hashes=(passport_digest,),
            promotion_hashes=(promotion_digest,),
            authorized_instruments=("event_contract:TEST-MARKET",),
            correlation_cluster="alpha",
            max_order_risk_cents=1000,
            max_open_risk_cents=12_500,
            max_correlated_risk_cents=3000,
            max_daily_loss_cents=1500,
            max_open_orders=5,
        )
        values.update(overrides)
        return CapitalRequest(**values)

    def arm_authority(self, **mandate_overrides) -> None:
        self.control.accept_envelope(
            self.signed(self.mandate(**mandate_overrides), self.operator, "operator")
        )
        self.control.accept_envelope(
            self.signed(
                DesiredModeV1(
                    venue=Venue.DUMMY_KALSHI,
                    mode=DesiredMode.LIVE,
                    revision=1,
                    reason="owner aggressive bounded authorization",
                    policy_epoch=1,
                    not_before=self.now,
                    expires_at=self.now + timedelta(days=30),
                ),
                self.operator,
                "operator",
            )
        )
        self.control.accept_envelope(
            self.signed(
                KillStateV1(
                    active=False,
                    generation=1,
                    reason="owner armed after reconciliation",
                    changed_by="owner",
                    policy_epoch=1,
                    changed_at=self.now,
                ),
                self.operator,
                "operator",
            )
        )

    def publish_promoted_passport(
        self,
        *,
        passport_ttl: timedelta = timedelta(days=7),
        verdict_ttl: timedelta = timedelta(days=7),
        promotion_ttl: timedelta = timedelta(days=7),
    ) -> tuple[str, str, str]:
        strategy_digest = canonical_sha256(["strategy", "dummy", 1])
        artifact_digest = self.cas.put_json(["artifact", 1])
        passport = AlphaPassportV1(
            passport_id="passport-dummy-1",
            strategy_lineage_id="lineage-dummy-1",
            venue=Venue.DUMMY_KALSHI,
            strategy_hash=strategy_digest,
            artifact_hashes=(artifact_digest,),
            evidence_verdict_hashes=(),
            intended_instruments=("event_contract:TEST-MARKET",),
            maximum_loss_cents=1000,
            evidence_class=EvidenceClass.FORWARD,
            created_at=self.now,
            expires_at=self.now + passport_ttl,
        )
        self.control.accept_envelope(self.signed(passport, self.research, "research"))
        passport_digest = passport.digest()
        verdicts = []
        for index, court in enumerate(("integrity", "adversarial_operations"), start=1):
            verdict_artifact = self.cas.put_json(["verdict-artifact", index])
            verdict = EvidenceVerdictV1(
                verdict_id=f"verdict-dummy-{index}",
                passport_digest=passport_digest,
                evaluator_id=self.evaluator.key_id,
                court=court,
                decision=EvidenceDecision.PASS,
                evidence_class=EvidenceClass.FORWARD,
                artifact_hashes=(verdict_artifact,),
                reason_codes=(f"{court.upper()}_PASS",),
                effective_trial_count=3,
                evaluated_at=self.now,
                expires_at=self.now + verdict_ttl,
            )
            self.control.accept_envelope(self.signed(verdict, self.evaluator, "evaluator"))
            verdicts.append(verdict)
        promotion = PromotionCertificateV1(
            certificate_id="promotion-dummy-1",
            passport_digest=passport_digest,
            verdict_digests=tuple(sorted(verdict.digest() for verdict in verdicts)),
            stage=PromotionStage.EXPLORATORY_LIVE,
            venue=Venue.DUMMY_KALSHI,
            instruments=("event_contract:TEST-MARKET",),
            maximum_loss_cents=1000,
            rollback_triggers=("AMBIGUOUS_OUTCOME", "KILL_ACTIVE"),
            policy_epoch=1,
            not_before=self.now,
            expires_at=self.now + promotion_ttl,
        )
        self.control.accept_envelope(self.signed(promotion, self.promoter, "promoter"))
        return passport_digest, strategy_digest, promotion.digest()

    def issue_resolver_authority(
        self,
        *,
        passport_ttl: timedelta = timedelta(days=7),
        verdict_ttl: timedelta = timedelta(days=7),
        promotion_ttl: timedelta = timedelta(days=7),
    ) -> tuple[SignedEnvelopeV1, CapitalRequest, str, str, str]:
        self.arm_authority()
        self.publish_operational_receipts()
        passport_digest, strategy_digest, promotion_digest = (
            self.publish_promoted_passport(
                passport_ttl=passport_ttl,
                verdict_ttl=verdict_ttl,
                promotion_ttl=promotion_ttl,
            )
        )
        request = self.request(
            passport_digest,
            strategy_digest,
            promotion_digest,
        )
        capital = self.control.issue_capital_envelope(
            mandate_id=self.mandate_id,
            request=request,
            correlation_id="resolver-capital",
        )
        return (
            capital,
            request,
            passport_digest,
            strategy_digest,
            promotion_digest,
        )

    def resolve_contract(
        self,
        capital: SignedEnvelopeV1,
        body_digest: str,
        *,
        nonce: str = "a" * 64,
    ) -> ApiResponse:
        return FundApi(self.control).handle(
            "GET",
            (
                f"/v1/cells/dummy_kalshi/contracts/{body_digest}"
                f"?capital_envelope_digest={capital.body_digest}"
                f"&request_nonce={nonce}"
            ),
        )

    def test_core_anchors_cell_journal_monotonically_and_idempotently(
        self,
    ) -> None:
        self.control.accept_envelope(
            self.signed(self.mandate(), self.operator, "operator")
        )
        api = FundApi(self.control)
        base = {
            "schema": "dumbmoney.cell-journal-anchor-request.v1",
            "cell_id": Venue.DUMMY_KALSHI.value,
            "account_hash": self.accounts[Venue.DUMMY_KALSHI.value],
            "journal_name": "capital-operational",
            "journal_schema": "dummy.sqlite-operational-journal.v1",
            "journal_stream_id": canonical_sha256(
                ["dummy", "capital-operational"]
            ),
            "journal_sequence": 4,
            "journal_head_sha256": canonical_sha256(
                ["capital-head", 4]
            ),
            "request_nonce": "1" * 64,
        }
        first = api.handle(
            "POST",
            "/v1/cells/dummy_kalshi/journal-heads:anchor",
            canonical_json_bytes(base),
        )

        self.assertEqual(first.status, 200)
        first_body = first.json()
        self.assertFalse(first_body["reused"])
        envelope = SignedEnvelopeV1.from_dict(
            first_body["anchor_envelope"]
        )
        anchor = envelope.verify(self.keyring, self.now)
        self.assertIsInstance(anchor, CellJournalHeadAnchorV1)
        self.assertEqual(anchor.journal_sequence, 4)
        self.assertEqual(
            first_body["ledger_proof"]["payload_digest"],
            anchor.digest(),
        )

        replay = api.handle(
            "POST",
            "/v1/cells/dummy_kalshi/journal-heads:anchor",
            canonical_json_bytes(
                {**base, "request_nonce": "2" * 64}
            ),
        )
        self.assertEqual(replay.status, 200)
        replay_body = replay.json()
        self.assertTrue(replay_body["reused"])
        self.assertEqual(
            replay_body["anchor_envelope"]["event_id"],
            first_body["anchor_envelope"]["event_id"],
        )

        regressed = api.handle(
            "POST",
            "/v1/cells/dummy_kalshi/journal-heads:anchor",
            canonical_json_bytes(
                {
                    **base,
                    "request_nonce": "3" * 64,
                    "journal_sequence": 3,
                    "journal_head_sha256": canonical_sha256(
                        ["capital-head", 3]
                    ),
                }
            ),
        )
        self.assertEqual(regressed.status, 409)
        self.assertEqual(
            regressed.json()["code"],
            "LEDGER_CONFLICT",
        )

        conflicting = api.handle(
            "POST",
            "/v1/cells/dummy_kalshi/journal-heads:anchor",
            canonical_json_bytes(
                {
                    **base,
                    "request_nonce": "4" * 64,
                    "journal_head_sha256": canonical_sha256(
                        ["different-head", 4]
                    ),
                }
            ),
        )
        self.assertEqual(conflicting.status, 409)

        advanced = api.handle(
            "POST",
            "/v1/cells/dummy_kalshi/journal-heads:anchor",
            canonical_json_bytes(
                {
                    **base,
                    "request_nonce": "5" * 64,
                    "journal_sequence": 5,
                    "journal_head_sha256": canonical_sha256(
                        ["capital-head", 5]
                    ),
                }
            ),
        )
        self.assertEqual(advanced.status, 200)
        advanced_anchor = SignedEnvelopeV1.from_dict(
            advanced.json()["anchor_envelope"]
        ).verify(self.keyring, self.now)
        self.assertIsInstance(
            advanced_anchor,
            CellJournalHeadAnchorV1,
        )
        self.assertEqual(
            advanced_anchor.previous_anchor_body_digest,
            anchor.digest(),
        )

    def publish_risk_snapshot(
        self,
        venue: Venue,
        **overrides,
    ) -> VenueRiskSnapshotV1:
        reconciliation = self.reconciliations[venue]
        values = dict(
            snapshot_id=canonical_sha256(
                ["venue-risk", venue.value, self.now.isoformat(), len(list(self.ledger.iter_events()))]
            ),
            venue=venue,
            account_hash=self.accounts[venue.value],
            reconciliation_receipt_digest=reconciliation.digest(),
            broker_snapshot_digest=reconciliation.broker_snapshot_digest,
            nav_cents=50_000,
            open_risk_cents=0,
            daily_loss_cents=0,
            high_water_drawdown_cents=0,
            open_orders=reconciliation.open_orders,
            open_positions=reconciliation.open_positions,
            correlated_open_risk_cents={},
            observed_at=self.now,
        )
        values.update(overrides)
        risk = VenueRiskSnapshotV1(**values)
        signer = (
            self.dummy_venue_signer
            if venue is Venue.DUMMY_KALSHI
            else self.dopey_venue_signer
        )
        self.control.accept_envelope(
            self.signed(risk, signer, f"venue-{venue.value}")
        )
        return risk

    def publish_operational_receipts(self) -> None:
        reconciliations = {}
        for venue in Venue:
            snapshot_digest = self.cas.put_json(["broker-snapshot", venue.value, self.now.isoformat()])
            receipt = InitialReconciliationReceiptV1(
                receipt_id=f"reconciliation-{venue.value}",
                venue=venue,
                account_hash=self.accounts[venue.value],
                broker_snapshot_digest=snapshot_digest,
                open_orders=0,
                open_positions=0,
                unknown_outcomes=0,
                status=ReconciliationStatus.RECONCILED,
                observed_at=self.now,
            )
            signer = (
                self.dummy_venue_signer
                if venue is Venue.DUMMY_KALSHI
                else self.dopey_venue_signer
            )
            self.control.accept_envelope(
                self.signed(receipt, signer, f"venue-{venue.value}")
            )
            reconciliations[venue] = receipt
            self.reconciliations[venue] = receipt
        for venue in Venue:
            self.publish_risk_snapshot(venue)
        for venue in Venue:
            proof_digests = [
                self.cas.put_json([proof, venue.value])
                for proof in ("place-cancel", "restart", "kill", "restore")
            ]
            receipt = DeploymentReceiptV1(
                deployment_id=f"deployment-{venue.value}",
                release_manifest_digest=self.release_manifest_sha256,
                core_runner_config_sha256=(
                    self.deployment_bindings.core_runner_config_sha256
                ),
                fund_lock_sha256=self.deployment_bindings.fund_lock_sha256,
                venue=venue,
                reconciliation_receipt_digest=reconciliations[venue].digest(),
                place_cancel_proof_digest=proof_digests[0],
                restart_proof_digest=proof_digests[1],
                kill_proof_digest=proof_digests[2],
                restore_proof_digest=proof_digests[3],
                completed_at=self.now,
            )
            self.control.accept_envelope(self.signed(receipt, self.operator, "operator"))

    def test_deployment_receipt_must_bind_current_sealed_release(self) -> None:
        self.publish_operational_receipts()
        matching = next(
            DeploymentReceiptV1.from_dict(record.envelope.body)
            for record in self.ledger.iter_events(
                schema=DeploymentReceiptV1.SCHEMA
            )
            if DeploymentReceiptV1.from_dict(record.envelope.body).venue
            is Venue.DUMMY_KALSHI
        )
        mismatches = {
            "release_manifest_digest": "1" * 64,
            "core_runner_config_sha256": "2" * 64,
            "fund_lock_sha256": "3" * 64,
        }
        for field, value in mismatches.items():
            with self.subTest(field=field):
                receipt = replace(
                    matching,
                    deployment_id=f"deployment-mismatch-{field}",
                    **{field: value},
                )
                with self.assertRaisesRegex(
                    EvidenceResolutionError,
                    "current sealed release",
                ):
                    self.control.accept_envelope(
                        self.signed(
                            receipt,
                            self.operator,
                            f"operator-{field}",
                        )
                    )

    def test_new_release_receipt_revokes_old_receipt_across_replay(self) -> None:
        self.publish_operational_receipts()
        old_receipt = next(
            DeploymentReceiptV1.from_dict(record.envelope.body)
            for record in self.ledger.iter_events(
                schema=DeploymentReceiptV1.SCHEMA
            )
            if DeploymentReceiptV1.from_dict(record.envelope.body).venue
            is Venue.DUMMY_KALSHI
        )
        new_release_digest = self.cas.put_json(
            {"schema": "test.sealed-release.v1", "release_id": "runtime-tests-2"}
        )
        new_bindings = DeploymentAuthorityBindings(
            release_manifest_sha256=new_release_digest,
            core_runner_config_sha256=canonical_sha256(
                ["runtime-test", "core-runner-config", 2]
            ),
            fund_lock_sha256=self.deployment_bindings.fund_lock_sha256,
        )
        new_control = self.control_for_bindings(new_bindings)
        new_receipt = replace(
            old_receipt,
            deployment_id="deployment-dummy-new-release",
            release_manifest_digest=new_release_digest,
            core_runner_config_sha256=new_bindings.core_runner_config_sha256,
        )
        new_control.accept_envelope(
            self.signed(new_receipt, self.operator, "operator")
        )

        restarted_old = self.control_for_bindings(self.deployment_bindings)
        state = restarted_old.replay_state()
        self.assertNotIn(Venue.DUMMY_KALSHI, state.deployment_receipts)
        snapshot = restarted_old.control_snapshot()
        self.assertIn(
            "DEPLOYMENT_RECEIPT_MISSING:dummy_kalshi",
            snapshot["reason_codes"],
        )

    def test_initial_state_is_paused_and_unknown_never_rendered_as_zero(self) -> None:
        snapshot = self.control.control_snapshot()
        self.assertEqual(snapshot["status"], "PAUSED")
        self.assertTrue(snapshot["kill_state"]["active"])
        self.assertEqual(snapshot["kill_state"]["reason"], "UNINITIALIZED_FAIL_CLOSED")
        for venue in snapshot["venues"].values():
            self.assertIsNone(venue["open_risk_cents"])
            self.assertEqual(venue["reconciliation_status"], "UNKNOWN")
        self.assertEqual(snapshot["openrouter"]["daily_budget_cents"], 1000)
        self.assertIsNone(snapshot["openrouter"]["spent_cents"])
        self.assertEqual(snapshot["loss"]["per_venue_daily_loss_limit_pct"]["dummy_kalshi"], 1.5)
        self.assertEqual(snapshot["services"]["DumbMoneyCore"]["status"], "READY")
        self.assertEqual(
            snapshot["services"]["DumbMoneyCore"]["observed_at"],
            snapshot["observed_at"],
        )
        self.assertEqual(
            snapshot["services"]["DumbMoneyResearchMesh"],
            {"status": "UNKNOWN", "observed_at": None},
        )

    def test_operator_roles_revisions_and_policy_caps_are_enforced(self) -> None:
        with self.assertRaisesRegex(AuthorityError, "operator"):
            self.control.accept_envelope(self.signed(self.mandate(), self.research, "research"))
        over_budget = self.mandate(openrouter_daily_budget_cents=1001)
        with self.assertRaisesRegex(PolicyViolation, "MODEL_BUDGET"):
            self.control.accept_envelope(self.signed(over_budget, self.operator, "bad-operator"))
        under_budget = self.mandate(
            mandate_id=canonical_sha256(["mandate", "under-budget"]),
            openrouter_daily_budget_cents=999,
        )
        with self.assertRaisesRegex(PolicyViolation, "MODEL_BUDGET"):
            self.control.accept_envelope(
                self.signed(under_budget, self.operator, "under-budget-operator")
            )
        self.control.accept_envelope(self.signed(self.mandate(), self.operator, "operator"))
        bad_revision = DesiredModeV1(
            venue=Venue.DUMMY_KALSHI,
            mode=DesiredMode.LIVE,
            revision=2,
            reason="skip revision",
            policy_epoch=1,
            not_before=self.now,
            expires_at=self.now + timedelta(days=1),
        )
        with self.assertRaisesRegex(AuthorityError, "contiguous"):
            self.control.accept_envelope(self.signed(bad_revision, self.operator, "operator"))

    def test_concurrent_semantic_mutations_are_serialized(self) -> None:
        first = self.signed(
            KillStateV1(
                active=True,
                generation=1,
                reason="concurrent fail closed",
                changed_by="owner",
                policy_epoch=1,
                changed_at=self.now,
            ),
            self.operator,
            "operator-a",
        )
        second = self.signed(
            KillStateV1(
                active=False,
                generation=1,
                reason="conflicting concurrent arm",
                changed_by="owner",
                policy_epoch=1,
                changed_at=self.now,
            ),
            self.operator,
            "operator-b",
        )

        def append(envelope: SignedEnvelopeV1) -> str:
            try:
                self.control.accept_envelope(envelope)
            except AuthorityError:
                return "DENIED"
            return "ACCEPTED"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(append, (first, second)))
        self.assertEqual(sorted(outcomes), ["ACCEPTED", "DENIED"])
        self.assertEqual(self.ledger.verify_chain().event_count, 1)

    def test_venue_signers_cannot_impersonate_the_other_venue(self) -> None:
        broker_digest = self.cas.put_json(["dummy-broker-snapshot"])
        receipt = InitialReconciliationReceiptV1(
            receipt_id="wrong-venue-signer",
            venue=Venue.DUMMY_KALSHI,
            account_hash=self.accounts[Venue.DUMMY_KALSHI.value],
            broker_snapshot_digest=broker_digest,
            open_orders=0,
            open_positions=0,
            unknown_outcomes=0,
            status=ReconciliationStatus.RECONCILED,
            observed_at=self.now,
        )
        with self.assertRaisesRegex(AuthorityError, "dummy_kalshi venue"):
            self.control.accept_envelope(
                self.signed(receipt, self.dopey_venue_signer, "venue-dopey")
            )

    def test_future_venue_evidence_is_rejected(self) -> None:
        broker_digest = self.cas.put_json(["future-broker-snapshot"])
        receipt = InitialReconciliationReceiptV1(
            receipt_id="future-reconciliation",
            venue=Venue.DUMMY_KALSHI,
            account_hash=self.accounts[Venue.DUMMY_KALSHI.value],
            broker_snapshot_digest=broker_digest,
            open_orders=0,
            open_positions=0,
            unknown_outcomes=0,
            status=ReconciliationStatus.RECONCILED,
            observed_at=self.now + timedelta(seconds=6),
        )
        with self.assertRaisesRegex(AuthorityError, "future clock skew"):
            self.control.accept_envelope(
                self.signed(receipt, self.dummy_venue_signer, "venue-dummy")
            )

    def test_core_operator_evaluator_role_confusion_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "core capital signer.*operator"):
            FundControlPlane(
                self.ledger,
                self.keyring,
                self.core,
                RiskPolicyEngine(self.policy),
                operator_key_ids=frozenset({self.core.key_id}),
                evaluator_key_ids=frozenset(),
                clock=self.clock,
            )

    def test_restart_rejects_authentic_event_from_wrong_signer_role(self) -> None:
        bypassed_kill = KillStateV1(
            active=True,
            generation=1,
            reason="wrong-role ledger injection",
            changed_by="research",
            policy_epoch=1,
            changed_at=self.now,
        )
        self.ledger.append(
            self.signed(
                bypassed_kill,
                self.research,
                "wrong-role-research",
            )
        )
        with self.assertRaisesRegex(AuthorityError, "operator"):
            self.control_for_bindings(self.deployment_bindings)

    def test_expired_desired_mode_and_restart_do_not_reset_revision(self) -> None:
        first = DesiredModeV1(
            venue=Venue.DUMMY_KALSHI,
            mode=DesiredMode.PAUSED,
            revision=1,
            reason="short control epoch",
            policy_epoch=1,
            not_before=self.now,
            expires_at=self.now + timedelta(seconds=60),
        )
        self.control.accept_envelope(self.signed(first, self.operator, "operator"))
        self.now += timedelta(seconds=61)
        self.clock.value = self.now
        restarted = FundControlPlane(
            self.ledger,
            self.keyring,
            self.core,
            RiskPolicyEngine(self.policy),
            operator_key_ids=frozenset({self.operator.key_id}),
            evaluator_key_ids=frozenset({self.evaluator.key_id}),
            promoter_key_ids=frozenset({self.promoter.key_id}),
            allocator_key_ids=frozenset({self.allocator.key_id}),
            research_key_ids=frozenset({self.research.key_id}),
            venue_key_ids={
                Venue.DUMMY_KALSHI: frozenset({self.dummy_venue_signer.key_id}),
                Venue.DOPEY_ROBINHOOD: frozenset({self.dopey_venue_signer.key_id}),
            },
            deployment_bindings=self.deployment_bindings,
            clock=self.clock,
        )
        second = DesiredModeV1(
            venue=Venue.DUMMY_KALSHI,
            mode=DesiredMode.READ_ONLY,
            revision=2,
            reason="renewed control epoch",
            policy_epoch=1,
            not_before=self.now,
            expires_at=self.now + timedelta(seconds=60),
        )
        restarted.accept_envelope(self.signed(second, self.operator, "operator"))
        self.assertEqual(restarted.replay_state().desired_modes[Venue.DUMMY_KALSHI].revision, 2)

    def test_expired_newer_mode_never_reveals_older_live_authority(self) -> None:
        live = DesiredModeV1(
            venue=Venue.DUMMY_KALSHI,
            mode=DesiredMode.LIVE,
            revision=1,
            reason="old broad live mode",
            policy_epoch=1,
            not_before=self.now,
            expires_at=self.now + timedelta(days=1),
        )
        paused = DesiredModeV1(
            venue=Venue.DUMMY_KALSHI,
            mode=DesiredMode.PAUSED,
            revision=2,
            reason="newer short pause epoch",
            policy_epoch=1,
            not_before=self.now,
            expires_at=self.now + timedelta(seconds=60),
        )
        self.control.accept_envelope(self.signed(live, self.operator, "operator"))
        self.control.accept_envelope(self.signed(paused, self.operator, "operator"))
        self.now += timedelta(seconds=61)
        self.clock.value = self.now
        self.assertNotIn(
            Venue.DUMMY_KALSHI,
            self.control.replay_state().desired_modes,
        )

    def test_expired_transport_revokes_positive_controls_without_resetting_counters(
        self,
    ) -> None:
        transport_expiry = self.now + timedelta(seconds=1)
        mandate = self.mandate()
        self.control.accept_envelope(
            self.signed(
                mandate,
                self.operator,
                "operator",
                envelope_expires_at=transport_expiry,
            )
        )
        self.control.accept_envelope(
            self.signed(
                DesiredModeV1(
                    venue=Venue.DUMMY_KALSHI,
                    mode=DesiredMode.LIVE,
                    revision=1,
                    reason="short transport live authorization",
                    policy_epoch=1,
                    not_before=self.now,
                    expires_at=self.now + timedelta(days=1),
                ),
                self.operator,
                "operator",
                envelope_expires_at=transport_expiry,
            )
        )
        self.control.accept_envelope(
            self.signed(
                KillStateV1(
                    active=False,
                    generation=1,
                    reason="short transport kill clear",
                    changed_by="owner",
                    policy_epoch=1,
                    changed_at=self.now,
                ),
                self.operator,
                "operator",
                envelope_expires_at=transport_expiry,
            )
        )
        self.now += timedelta(seconds=2)
        self.clock.value = self.now
        expired = self.control.replay_state()
        self.assertIsNone(expired.mandate)
        self.assertNotIn(Venue.DUMMY_KALSHI, expired.desired_modes)
        self.assertIsNone(expired.kill_state)
        self.assertTrue(expired.kill_active)

        self.control.accept_envelope(
            self.signed(mandate, self.operator, "operator")
        )
        self.assertEqual(self.control.replay_state().mandate, mandate)
        self.control.accept_envelope(
            self.signed(
                DesiredModeV1(
                    venue=Venue.DUMMY_KALSHI,
                    mode=DesiredMode.PAUSED,
                    revision=2,
                    reason="fail-closed renewal",
                    policy_epoch=1,
                    not_before=self.now,
                    expires_at=self.now + timedelta(days=1),
                ),
                self.operator,
                "operator",
            )
        )
        self.control.accept_envelope(
            self.signed(
                KillStateV1(
                    active=True,
                    generation=2,
                    reason="fail-closed renewal",
                    changed_by="owner",
                    policy_epoch=1,
                    changed_at=self.now,
                ),
                self.operator,
                "operator",
            )
        )
        self.now += timedelta(minutes=11)
        self.clock.value = self.now
        historical_negative = self.control.replay_state()
        self.assertEqual(
            historical_negative.desired_modes[Venue.DUMMY_KALSHI].revision,
            2,
        )
        self.assertEqual(historical_negative.kill_state.generation, 2)
        self.assertTrue(historical_negative.kill_active)

    def test_evaluator_cannot_self_promote_and_live_stage_requires_court_coverage(self) -> None:
        artifact_digest = self.cas.put_json(["candidate", "artifact"])
        passport = AlphaPassportV1(
            passport_id="passport-court-test",
            strategy_lineage_id="lineage-court-test",
            venue=Venue.DUMMY_KALSHI,
            strategy_hash=canonical_sha256(["court-strategy"]),
            artifact_hashes=(artifact_digest,),
            evidence_verdict_hashes=(),
            intended_instruments=("event_contract:COURT-TEST",),
            maximum_loss_cents=100,
            evidence_class=EvidenceClass.FORWARD,
            created_at=self.now,
            expires_at=self.now + timedelta(days=1),
        )
        self.control.accept_envelope(self.signed(passport, self.research, "research"))
        verdict_artifact = self.cas.put_json(["court-verdict"])
        verdict = EvidenceVerdictV1(
            verdict_id="verdict-court-test",
            passport_digest=passport.digest(),
            evaluator_id=self.evaluator.key_id,
            court="integrity",
            decision=EvidenceDecision.PASS,
            evidence_class=EvidenceClass.FORWARD,
            artifact_hashes=(verdict_artifact,),
            reason_codes=("INTEGRITY_PASS",),
            effective_trial_count=1,
            evaluated_at=self.now,
            expires_at=self.now + timedelta(days=1),
        )
        self.control.accept_envelope(self.signed(verdict, self.evaluator, "evaluator"))
        promotion = PromotionCertificateV1(
            certificate_id="promotion-court-test",
            passport_digest=passport.digest(),
            verdict_digests=(verdict.digest(),),
            stage=PromotionStage.EXPLORATORY_LIVE,
            venue=Venue.DUMMY_KALSHI,
            instruments=("event_contract:COURT-TEST",),
            maximum_loss_cents=100,
            rollback_triggers=("KILL_ACTIVE",),
            policy_epoch=1,
            not_before=self.now,
            expires_at=self.now + timedelta(days=1),
        )
        with self.assertRaisesRegex(AuthorityError, "promoter"):
            self.control.accept_envelope(self.signed(promotion, self.evaluator, "evaluator"))
        with self.assertRaisesRegex(EvidenceResolutionError, "adversarial_operations"):
            self.control.accept_envelope(self.signed(promotion, self.promoter, "promoter"))
        with self.assertRaisesRegex(ValueError, "operator and evaluator.*disjoint"):
            FundControlPlane(
                self.ledger,
                self.keyring,
                self.core,
                RiskPolicyEngine(self.policy),
                operator_key_ids=frozenset({self.operator.key_id}),
                evaluator_key_ids=frozenset({self.operator.key_id}),
                clock=self.clock,
            )

    def test_live_promotion_cannot_widen_passport_instruments_or_loss(self) -> None:
        artifact_digest = self.cas.put_json(["promotion-boundary-artifact"])
        passport = AlphaPassportV1(
            passport_id="passport-promotion-boundary",
            strategy_lineage_id="lineage-promotion-boundary",
            venue=Venue.DUMMY_KALSHI,
            strategy_hash=canonical_sha256(["promotion-boundary-strategy"]),
            artifact_hashes=(artifact_digest,),
            evidence_verdict_hashes=(),
            intended_instruments=("event_contract:BOUNDARY-A",),
            maximum_loss_cents=100,
            evidence_class=EvidenceClass.FORWARD,
            created_at=self.now,
            expires_at=self.now + timedelta(days=1),
        )
        self.control.accept_envelope(
            self.signed(passport, self.research, "research")
        )
        common = dict(
            passport_digest=passport.digest(),
            verdict_digests=("a" * 64,),
            stage=PromotionStage.EXPLORATORY_LIVE,
            venue=Venue.DUMMY_KALSHI,
            rollback_triggers=("KILL_ACTIVE",),
            policy_epoch=1,
            not_before=self.now,
            expires_at=self.now + timedelta(days=1),
        )
        widened_instrument = PromotionCertificateV1(
            certificate_id="promotion-widened-instrument",
            instruments=("event_contract:BOUNDARY-B",),
            maximum_loss_cents=100,
            **common,
        )
        with self.assertRaisesRegex(
            EvidenceResolutionError,
            "instruments exceed alpha passport",
        ):
            self.control.accept_envelope(
                self.signed(widened_instrument, self.promoter, "promoter")
            )
        widened_loss = PromotionCertificateV1(
            certificate_id="promotion-widened-loss",
            instruments=("event_contract:BOUNDARY-A",),
            maximum_loss_cents=101,
            **common,
        )
        with self.assertRaisesRegex(
            EvidenceResolutionError,
            "maximum loss exceeds alpha passport",
        ):
            self.control.accept_envelope(
                self.signed(widened_loss, self.promoter, "promoter")
            )

    def test_capital_lease_is_60_seconds_fenced_and_evidence_resolved(self) -> None:
        self.arm_authority()
        self.publish_operational_receipts()
        passport_digest, strategy_digest, promotion_digest = self.publish_promoted_passport()
        signed = self.control.issue_capital_envelope(
            mandate_id=self.mandate_id,
            request=self.request(passport_digest, strategy_digest, promotion_digest),
            correlation_id="capital-issue-1",
        )
        capital = signed.verify(self.keyring, self.now)
        self.assertEqual(capital.expires_at - capital.not_before, timedelta(seconds=60))
        self.assertEqual(capital.fencing_generation, 1)
        self.assertEqual(capital.max_daily_loss_cents, 1500)
        second = self.control.issue_capital_envelope(
            mandate_id=self.mandate_id,
            request=self.request(passport_digest, strategy_digest, promotion_digest),
            correlation_id="capital-issue-2",
        )
        self.assertEqual(second.verify(self.keyring, self.now).fencing_generation, 2)
        self.assertEqual(self.ledger.verify_chain().event_count, 15)
        self.now += timedelta(seconds=61)
        self.clock.value = self.now
        page = self.control.cell_commands(
            cell_id=Venue.DUMMY_KALSHI.value,
            after_sequence=0,
            after_digest="0" * 64,
            request_nonce="a" * 64,
            observed_at=self.now,
        )
        expired_capital = [
            command
            for command in page["commands"]
            if command["body_schema"] == "dumbmoney.capital-envelope.v1"
        ]
        self.assertEqual(len(expired_capital), 2)
        self.assertTrue(
            all(
                command["authority_effect"] == "HISTORICAL_ONLY"
                and not command["valid_now"]
                for command in expired_capital
            )
        )

    def test_allocator_signed_request_is_processed_once_without_unsigned_risk_input(self) -> None:
        self.arm_authority()
        self.publish_operational_receipts()
        passport_digest, strategy_digest, promotion_digest = self.publish_promoted_passport()
        request = self.request(passport_digest, strategy_digest, promotion_digest)
        contract = CapitalRequestV1(
            request_id="allocator-request-001",
            mandate_id=self.mandate_id,
            venue=request.venue,
            account_hash=request.account_hash,
            strategy_hashes=request.strategy_hashes,
            passport_hashes=request.passport_hashes,
            promotion_hashes=request.promotion_hashes,
            authorized_instruments=request.authorized_instruments,
            correlation_cluster=request.correlation_cluster,
            max_order_risk_cents=request.max_order_risk_cents,
            max_open_risk_cents=request.max_open_risk_cents,
            max_correlated_risk_cents=request.max_correlated_risk_cents,
            max_daily_loss_cents=request.max_daily_loss_cents,
            max_open_orders=request.max_open_orders,
            policy_epoch=1,
            not_before=self.now,
            expires_at=self.now + timedelta(seconds=60),
        )
        with self.assertRaisesRegex(AuthorityError, "capital allocator"):
            self.control.accept_envelope(
                self.signed(contract, self.research, "research")
            )
        signed_request = self.signed(contract, self.allocator, "allocator")
        self.control.accept_envelope(signed_request)
        outcomes = self.control.process_pending_capital_requests()
        self.assertEqual([outcome["status"] for outcome in outcomes], ["ISSUED"])
        self.assertEqual(self.control.process_pending_capital_requests(), ())
        capital_records = list(
            self.ledger.iter_events(schema="dumbmoney.capital-envelope.v1")
        )
        self.assertEqual(len(capital_records), 1)
        self.assertEqual(
            capital_records[0].envelope.causation_id,
            contract.digest(),
        )

    def test_execution_intent_is_exactly_contained_by_capital_authority(self) -> None:
        self.arm_authority()
        self.publish_operational_receipts()
        passport_digest, strategy_digest, promotion_digest = self.publish_promoted_passport()
        capital = self.control.issue_capital_envelope(
            mandate_id=self.mandate_id,
            request=self.request(passport_digest, strategy_digest, promotion_digest),
            correlation_id="intent-capital",
        )
        values = dict(
            intent_id="intent-contained-001",
            idempotency_key="intent-contained-idempotency-001",
            venue=Venue.DUMMY_KALSHI,
            account_hash=self.accounts[Venue.DUMMY_KALSHI.value],
            instrument_id="TEST-MARKET",
            instrument_type="event_contract",
            authorized_instrument="event_contract:TEST-MARKET",
            side="BUY",
            quantity=1,
            limit_price_minor=40,
            time_in_force="GTC",
            maximum_loss_cents=100,
            correlation_cluster="alpha",
            strategy_hash=strategy_digest,
            passport_digest=passport_digest,
            promotion_digest=promotion_digest,
            capital_envelope_digest=capital.body_digest,
            created_at=self.now,
            expires_at=self.now + timedelta(seconds=30),
        )
        intent = ExecutionIntentV1(**values)
        self.control.accept_envelope(
            self.signed(intent, self.dummy_venue_signer, "venue-dummy_kalshi")
        )
        widened = ExecutionIntentV1(
            **{
                **values,
                "intent_id": "intent-widened-001",
                "idempotency_key": "intent-widened-idempotency-001",
                "instrument_id": "OTHER-MARKET",
                "authorized_instrument": "event_contract:OTHER-MARKET",
            }
        )
        with self.assertRaisesRegex(
            EvidenceResolutionError,
            "outside capital authority",
        ):
            self.control.accept_envelope(
                self.signed(
                    widened,
                    self.dummy_venue_signer,
                    "venue-dummy_kalshi",
                )
            )
        mixed_strategy = ExecutionIntentV1(
            **{
                **values,
                "intent_id": "intent-mixed-strategy-001",
                "idempotency_key": "intent-mixed-strategy-idempotency-001",
                "strategy_hash": canonical_sha256(["different-strategy"]),
            }
        )
        with self.assertRaisesRegex(
            EvidenceResolutionError,
            "singleton capital authority",
        ):
            self.control.accept_envelope(
                self.signed(
                    mixed_strategy,
                    self.dummy_venue_signer,
                    "venue-dummy-mixed-strategy",
                )
            )
        self.control.issue_capital_envelope(
            mandate_id=self.mandate_id,
            request=self.request(passport_digest, strategy_digest, promotion_digest),
            correlation_id="intent-newer-capital",
        )
        stale_fence = ExecutionIntentV1(
            **{
                **values,
                "intent_id": "intent-stale-fence-001",
                "idempotency_key": "intent-stale-fence-idempotency-001",
            }
        )
        with self.assertRaisesRegex(
            EvidenceResolutionError,
            "current maximum capital fence",
        ):
            self.control.accept_envelope(
                self.signed(
                    stale_fence,
                    self.dummy_venue_signer,
                    "venue-dummy-stale-fence",
                )
            )

    def test_cell_resolves_nonce_bound_singleton_authority_material(self) -> None:
        (
            capital,
            request,
            passport_digest,
            strategy_digest,
            promotion_digest,
        ) = self.issue_resolver_authority()
        api = FundApi(self.control)
        nonce = "6" * 64
        response = api.handle(
            "GET",
            (
                f"/v1/cells/dummy_kalshi/contracts/{promotion_digest}"
                f"?capital_envelope_digest={capital.body_digest}"
                f"&request_nonce={nonce}"
            ),
        )
        self.assertEqual(response.status, 200)
        resolution = response.json()
        self.assertEqual(
            resolution["schema"],
            "dumbmoney.cell-contract-resolution.v1",
        )
        self.assertEqual(resolution["request_nonce"], nonce)
        self.assertEqual(resolution["requested_body_digest"], promotion_digest)
        self.assertEqual(
            resolution["capital_envelope_digest"],
            capital.body_digest,
        )
        self.assertEqual(resolution["fencing_generation"], 1)
        self.assertEqual(
            resolution["contract_schema"],
            PromotionCertificateV1.SCHEMA,
        )
        self.assertTrue(resolution["eligible_live_input"])
        self.assertEqual(
            resolution["envelope"]["body"]["passport_digest"],
            passport_digest,
        )
        checkpoint = resolution["checkpoint"]
        self.assertEqual(checkpoint["request_nonce"], nonce)
        self.assertEqual(checkpoint["envelope"], resolution["envelope"])
        self.assertEqual(
            checkpoint["authority_state"],
            resolution["authority_state"],
        )
        authority_state = resolution["authority_state"]
        self.assertEqual(
            authority_state["schema"],
            "dumbmoney.cell-authority-state.v1",
        )
        self.assertTrue(authority_state["kill_clear"])
        self.assertEqual(authority_state["kill_generation"], 1)
        self.assertEqual(authority_state["desired_mode"], "LIVE")
        self.assertEqual(authority_state["desired_mode_revision"], 1)
        self.assertEqual(authority_state["policy_epoch"], 1)
        self.assertEqual(
            authority_state["capital_envelope_digest"],
            capital.body_digest,
        )
        self.assertEqual(authority_state["strategy_hash"], strategy_digest)
        self.assertEqual(authority_state["passport_digest"], passport_digest)
        self.assertEqual(authority_state["promotion_digest"], promotion_digest)
        self.assertEqual(
            {verdict["decision"] for verdict in authority_state["verdicts"]},
            {"PASS"},
        )
        self.assertEqual(len(authority_state["verdicts"]), 2)
        self.assertEqual(
            authority_state["promotion_event_digest"],
            resolution["ledger_proof"]["event_digest"],
        )
        checkpoint_signature = resolution["checkpoint_signature"]
        self.keyring.verify(
            checkpoint_signature["signer_key_id"],
            checkpoint_signature["algorithm"],
            canonical_json_bytes(checkpoint),
            checkpoint_signature["signature"],
        )
        original = SignedEnvelopeV1.from_dict(resolution["envelope"])
        promotion = original.verify(self.keyring, self.now)
        self.assertEqual(promotion.passport_digest, passport_digest)
        proof = dict(resolution["ledger_proof"])
        event_digest = proof.pop("event_digest")
        proof.pop("schema")
        self.assertEqual(canonical_sha256(proof), event_digest)

        passport_response = api.handle(
            "GET",
            (
                f"/v1/cells/dummy_kalshi/contracts/{passport_digest}"
                f"?capital_envelope_digest={capital.body_digest}"
                f"&request_nonce={'7' * 64}"
            ),
        )
        self.assertEqual(passport_response.status, 200)
        passport = SignedEnvelopeV1.from_dict(
            passport_response.json()["envelope"]
        ).verify(self.keyring, self.now)
        self.assertEqual(passport.strategy_hash, strategy_digest)
        cross_venue = api.handle(
            "GET",
            (
                f"/v1/cells/dopey_robinhood/contracts/{promotion_digest}"
                f"?capital_envelope_digest={capital.body_digest}"
                f"&request_nonce={'8' * 64}"
            ),
        )
        self.assertEqual(cross_venue.status, 403)
        missing_nonce = api.handle(
            "GET",
            (
                f"/v1/cells/dummy_kalshi/contracts/{promotion_digest}"
                f"?capital_envelope_digest={capital.body_digest}"
            ),
        )
        self.assertEqual(missing_nonce.status, 400)
        newer_capital = self.control.issue_capital_envelope(
            mandate_id=self.mandate_id,
            request=request,
            correlation_id="resolver-capital-2",
        )
        stale_fence = api.handle(
            "GET",
            (
                f"/v1/cells/dummy_kalshi/contracts/{promotion_digest}"
                f"?capital_envelope_digest={capital.body_digest}"
                f"&request_nonce={'9' * 64}"
            ),
        )
        self.assertEqual(stale_fence.status, 403)
        renewed = api.handle(
            "GET",
            (
                f"/v1/cells/dummy_kalshi/contracts/{promotion_digest}"
                f"?capital_envelope_digest={newer_capital.body_digest}"
                f"&request_nonce={'a' * 64}"
            ),
        )
        self.assertEqual(renewed.status, 200)
        self.assertEqual(renewed.json()["fencing_generation"], 2)
        self.assertEqual(
            renewed.json()["checkpoint"]["capital_envelope_digest"],
            newer_capital.body_digest,
        )

    def test_resolver_denies_kill_or_pause_transition_after_capital_grant(
        self,
    ) -> None:
        capital, _request, _passport, _strategy, promotion = (
            self.issue_resolver_authority()
        )
        self.control.accept_envelope(
            self.signed(
                KillStateV1(
                    active=True,
                    generation=2,
                    reason="post-grant emergency stop",
                    changed_by="owner",
                    policy_epoch=1,
                    changed_at=self.now,
                ),
                self.operator,
                "operator",
            )
        )
        killed = self.resolve_contract(capital, promotion)
        self.assertEqual(killed.status, 403)
        self.assertIn("kill", killed.json()["message"])

    def test_resolver_denies_pause_transition_after_capital_grant(self) -> None:
        capital, _request, _passport, _strategy, promotion = (
            self.issue_resolver_authority()
        )
        self.control.accept_envelope(
            self.signed(
                DesiredModeV1(
                    venue=Venue.DUMMY_KALSHI,
                    mode=DesiredMode.PAUSED,
                    revision=2,
                    reason="post-grant operator pause",
                    policy_epoch=1,
                    not_before=self.now,
                    expires_at=self.now + timedelta(days=1),
                ),
                self.operator,
                "operator",
            )
        )
        paused = self.resolve_contract(capital, promotion)
        self.assertEqual(paused.status, 403)
        self.assertIn("LIVE", paused.json()["message"])

    def test_resolver_denies_policy_epoch_transition_after_capital_grant(
        self,
    ) -> None:
        capital, _request, _passport, _strategy, promotion = (
            self.issue_resolver_authority()
        )
        policy_values = dict(
            load_strict_json(
                REPO_ROOT / "configs" / "dumbmoney" / "risk-policy.v1.json"
            )
        )
        policy_values["policy_epoch"] = 2
        self.control.policy_engine = RiskPolicyEngine(
            RiskPolicyV1.from_dict(policy_values)
        )
        stale_policy = self.resolve_contract(capital, promotion)
        self.assertEqual(stale_policy.status, 403)
        self.assertIn("policy epoch", stale_policy.json()["message"])

    def test_resolver_denies_passport_promotion_or_verdict_expiry(self) -> None:
        for expiry_target in ("passport", "promotion", "verdict"):
            with self.subTest(expiry_target=expiry_target):
                self.tearDown()
                self.setUp()
                ttl = timedelta(seconds=10)
                capital, _request, passport, _strategy, promotion = (
                    self.issue_resolver_authority(
                        passport_ttl=(
                            ttl
                            if expiry_target == "passport"
                            else timedelta(days=7)
                        ),
                        promotion_ttl=(
                            ttl
                            if expiry_target == "promotion"
                            else timedelta(days=7)
                        ),
                        verdict_ttl=(
                            ttl
                            if expiry_target == "verdict"
                            else timedelta(days=7)
                        ),
                    )
                )
                self.now += timedelta(seconds=11)
                self.clock.value = self.now
                digest = passport if expiry_target == "passport" else promotion
                expired = self.resolve_contract(capital, digest)
                self.assertEqual(expired.status, 403)

    def test_resolver_denies_superseding_promotion_or_nonpass_verdict(self) -> None:
        capital, request, passport_digest, _strategy, promotion_digest = (
            self.issue_resolver_authority()
        )
        promotion_record = next(
            record
            for record in self.ledger.iter_events(
                schema=PromotionCertificateV1.SCHEMA
            )
            if record.payload_digest == promotion_digest
        )
        promotion = PromotionCertificateV1.from_dict(
            promotion_record.envelope.body
        )
        paper_revocation = PromotionCertificateV1(
            certificate_id="promotion-dummy-paper-revocation",
            passport_digest=passport_digest,
            verdict_digests=promotion.verdict_digests,
            stage=PromotionStage.PAPER,
            venue=Venue.DUMMY_KALSHI,
            instruments=promotion.instruments,
            maximum_loss_cents=promotion.maximum_loss_cents,
            rollback_triggers=promotion.rollback_triggers,
            policy_epoch=1,
            not_before=self.now,
            expires_at=self.now + timedelta(days=1),
        )
        self.control.accept_envelope(
            self.signed(paper_revocation, self.promoter, "promoter")
        )
        superseded = self.resolve_contract(capital, promotion_digest)
        self.assertEqual(superseded.status, 403)
        self.assertIn("superseded or revoked", superseded.json()["message"])

        self.tearDown()
        self.setUp()
        capital, request, passport_digest, _strategy, promotion_digest = (
            self.issue_resolver_authority()
        )
        promotion_record = next(
            record
            for record in self.ledger.iter_events(
                schema=PromotionCertificateV1.SCHEMA
            )
            if record.payload_digest == promotion_digest
        )
        promotion = PromotionCertificateV1.from_dict(
            promotion_record.envelope.body
        )
        original_verdict_record = next(
            record
            for record in self.ledger.iter_events(schema=EvidenceVerdictV1.SCHEMA)
            if record.payload_digest == promotion.verdict_digests[0]
        )
        original_verdict = EvidenceVerdictV1.from_dict(
            original_verdict_record.envelope.body
        )
        revocation_artifact = self.cas.put_json(["verdict-revocation"])
        failed_verdict = EvidenceVerdictV1(
            verdict_id=f"{original_verdict.verdict_id}-revoked",
            passport_digest=passport_digest,
            evaluator_id=self.evaluator.key_id,
            court=original_verdict.court,
            decision=EvidenceDecision.FAIL,
            evidence_class=EvidenceClass.FORWARD,
            artifact_hashes=(revocation_artifact,),
            reason_codes=("CURRENT_EVIDENCE_FAILED",),
            effective_trial_count=4,
            evaluated_at=self.now,
            expires_at=self.now + timedelta(days=1),
        )
        self.control.accept_envelope(
            self.signed(failed_verdict, self.evaluator, "evaluator")
        )
        revoked = self.resolve_contract(capital, promotion_digest)
        self.assertEqual(revoked.status, 403)
        self.assertIn("superseded or revoked", revoked.json()["message"])
        self.assertEqual(request.promotion_hashes, (promotion_digest,))

    def test_resolver_and_kill_append_are_lock_linearized(self) -> None:
        capital, _request, _passport, _strategy, promotion = (
            self.issue_resolver_authority()
        )
        kill_envelope = self.signed(
            KillStateV1(
                active=True,
                generation=2,
                reason="concurrent kill",
                changed_by="owner",
                policy_epoch=1,
                changed_at=self.now,
            ),
            self.operator,
            "operator",
        )
        entered = threading.Event()
        release_resolution = threading.Event()
        kill_started = threading.Event()
        original_resolver = self.control._resolve_live_authority_tuple

        def delayed_resolver(**kwargs):
            entered.set()
            self.assertTrue(release_resolution.wait(timeout=5))
            return original_resolver(**kwargs)

        self.control._resolve_live_authority_tuple = delayed_resolver

        def append_kill():
            kill_started.set()
            return self.control.accept_envelope(kill_envelope)

        with ThreadPoolExecutor(max_workers=2) as executor:
            resolution_future = executor.submit(
                self.resolve_contract,
                capital,
                promotion,
            )
            self.assertTrue(entered.wait(timeout=5))
            kill_future = executor.submit(append_kill)
            self.assertTrue(kill_started.wait(timeout=5))
            self.assertFalse(kill_future.done())
            release_resolution.set()
            self.assertEqual(resolution_future.result(timeout=5).status, 200)
            kill_future.result(timeout=5)
        after_kill = self.resolve_contract(
            capital,
            promotion,
            nonce="b" * 64,
        )
        self.assertEqual(after_kill.status, 403)

    def test_mandate_instrument_allowlist_cannot_be_bypassed(self) -> None:
        self.arm_authority(allowed_instruments=("equity",))
        self.publish_operational_receipts()
        passport_digest, strategy_digest, promotion_digest = self.publish_promoted_passport()
        with self.assertRaisesRegex(PolicyViolation, "INSTRUMENT_NOT_IN_MANDATE:event_contract"):
            self.control.issue_capital_envelope(
                mandate_id=self.mandate_id,
                request=self.request(passport_digest, strategy_digest, promotion_digest),
                correlation_id="mandate-instrument-denial",
            )

    def test_older_looser_mandate_cannot_be_selected_after_supersession(self) -> None:
        self.arm_authority()
        self.publish_operational_receipts()
        passport_digest, strategy_digest, promotion_digest = self.publish_promoted_passport()
        tighter_id = canonical_sha256(["mandate", "tighter"])
        tighter = self.mandate(mandate_id=tighter_id, nav_cents=10_000)
        self.control.accept_envelope(self.signed(tighter, self.operator, "operator"))
        request = self.request(passport_digest, strategy_digest, promotion_digest)
        with self.assertRaisesRegex(AuthorityError, "non-superseded"):
            self.control.issue_capital_envelope(
                mandate_id=self.mandate_id,
                request=request,
                correlation_id="old-mandate-rejected",
            )
        with self.assertRaisesRegex(PolicyViolation, "REQUESTED_VENUE_RISK"):
            self.control.issue_capital_envelope(
                mandate_id=tighter_id,
                request=request,
                correlation_id="tight-mandate-enforced",
            )

    def test_expired_lease_and_control_restart_never_reset_fencing_generation(self) -> None:
        self.arm_authority()
        self.publish_operational_receipts()
        passport_digest, strategy_digest, promotion_digest = self.publish_promoted_passport()
        first = self.control.issue_capital_envelope(
            mandate_id=self.mandate_id,
            request=self.request(passport_digest, strategy_digest, promotion_digest),
            correlation_id="fence-before-restart",
        )
        self.assertEqual(first.verify(self.keyring, self.now).fencing_generation, 1)
        self.now += timedelta(seconds=61)
        self.clock.value = self.now
        for venue in Venue:
            snapshot_digest = self.cas.put_json(["broker-snapshot-renewal", venue.value, self.now.isoformat()])
            receipt = InitialReconciliationReceiptV1(
                receipt_id=f"reconciliation-renewal-{venue.value}",
                venue=venue,
                account_hash=self.accounts[venue.value],
                broker_snapshot_digest=snapshot_digest,
                open_orders=0,
                open_positions=0,
                unknown_outcomes=0,
                status=ReconciliationStatus.RECONCILED,
                observed_at=self.now,
            )
            signer = (
                self.dummy_venue_signer
                if venue is Venue.DUMMY_KALSHI
                else self.dopey_venue_signer
            )
            self.control.accept_envelope(
                self.signed(receipt, signer, f"venue-{venue.value}")
            )
            self.reconciliations[venue] = receipt
            self.publish_risk_snapshot(venue)
        restarted = FundControlPlane(
            self.ledger,
            self.keyring,
            self.core,
            RiskPolicyEngine(self.policy),
            operator_key_ids=frozenset({self.operator.key_id}),
            evaluator_key_ids=frozenset({self.evaluator.key_id}),
            promoter_key_ids=frozenset({self.promoter.key_id}),
            allocator_key_ids=frozenset({self.allocator.key_id}),
            research_key_ids=frozenset({self.research.key_id}),
            venue_key_ids={
                Venue.DUMMY_KALSHI: frozenset({self.dummy_venue_signer.key_id}),
                Venue.DOPEY_ROBINHOOD: frozenset({self.dopey_venue_signer.key_id}),
            },
            deployment_bindings=self.deployment_bindings,
            clock=self.clock,
        )
        second = restarted.issue_capital_envelope(
            mandate_id=self.mandate_id,
            request=self.request(passport_digest, strategy_digest, promotion_digest),
            correlation_id="fence-after-restart",
        )
        self.assertEqual(second.verify(self.keyring, self.now).fencing_generation, 2)

    def test_unknown_passport_kill_and_per_venue_daily_loss_deny_capital(self) -> None:
        self.arm_authority()
        self.publish_operational_receipts()
        unknown = self.request("a" * 64, "b" * 64, "c" * 64)
        with self.assertRaisesRegex(EvidenceResolutionError, "unresolved"):
            self.control.issue_capital_envelope(
                mandate_id=self.mandate_id,
                request=unknown,
                correlation_id="unknown-evidence",
            )
        passport_digest, strategy_digest, promotion_digest = self.publish_promoted_passport()
        self.publish_risk_snapshot(
            Venue.DUMMY_KALSHI,
            daily_loss_cents=1500,
        )
        with self.assertRaisesRegex(PolicyViolation, "VENUE_DAILY_LOSS_STOP"):
            self.control.issue_capital_envelope(
                mandate_id=self.mandate_id,
                request=self.request(passport_digest, strategy_digest, promotion_digest),
                correlation_id="venue-loss",
            )
        self.control.accept_envelope(
            self.signed(
                KillStateV1(
                    active=True,
                    generation=2,
                    reason="operator kill test",
                    changed_by="owner",
                    policy_epoch=1,
                    changed_at=self.now,
                ),
                self.operator,
                "operator",
            )
        )
        with self.assertRaisesRegex(PolicyViolation, "GLOBAL_KILL_ACTIVE"):
            self.control.issue_capital_envelope(
                mandate_id=self.mandate_id,
                request=self.request(passport_digest, strategy_digest, promotion_digest),
                correlation_id="kill-test",
            )

    def test_fresh_reconciliation_and_combined_caps_are_required(self) -> None:
        self.arm_authority()
        self.publish_operational_receipts()
        passport_digest, strategy_digest, promotion_digest = self.publish_promoted_passport()
        self.now += timedelta(seconds=61)
        self.clock.value = self.now
        with self.assertRaisesRegex(EvidenceResolutionError, "stale"):
            self.control.issue_capital_envelope(
                mandate_id=self.mandate_id,
                request=self.request(passport_digest, strategy_digest, promotion_digest),
                correlation_id="stale",
            )
        self.publish_operational_receipts()
        self.publish_risk_snapshot(
            Venue.DOPEY_ROBINHOOD,
            open_risk_cents=12_501,
            correlated_open_risk_cents={"other": 12_501},
        )
        with self.assertRaisesRegex(PolicyViolation, "PROJECTED_COMBINED"):
            self.control.issue_capital_envelope(
                mandate_id=self.mandate_id,
                request=self.request(passport_digest, strategy_digest, promotion_digest),
                correlation_id="combined",
            )

    def test_live_ready_requires_signed_current_reconciliation_and_deployment(self) -> None:
        self.arm_authority()
        before = self.control.control_snapshot()
        self.assertEqual(before["status"], "DEGRADED")
        self.assertIn(
            "RECONCILIATION_RECEIPT_MISSING:dummy_kalshi",
            before["reason_codes"],
        )
        self.publish_operational_receipts()
        self.control.accept_envelope(
            self.signed(
                DesiredModeV1(
                    venue=Venue.DOPEY_ROBINHOOD,
                    mode=DesiredMode.LIVE,
                    revision=1,
                    reason="owner aggressive bounded authorization",
                    policy_epoch=1,
                    not_before=self.now,
                    expires_at=self.now + timedelta(days=30),
                ),
                self.operator,
                "operator",
            )
        )
        ready = self.control.control_snapshot()
        self.assertEqual(ready["status"], "LIVE_READY")
        for venue in ready["venues"].values():
            self.assertEqual(venue["reconciliation_status"], "RECONCILED")
            self.assertTrue(venue["account_pinned"])

    def test_corrupt_operational_cas_proof_revokes_readiness_and_use_time_authority(
        self,
    ) -> None:
        capital, request, _passport, _strategy, promotion = (
            self.issue_resolver_authority()
        )
        deployment = next(
            DeploymentReceiptV1.from_dict(record.envelope.body)
            for record in self.ledger.iter_events(
                schema=DeploymentReceiptV1.SCHEMA
            )
            if DeploymentReceiptV1.from_dict(record.envelope.body).venue
            is Venue.DUMMY_KALSHI
        )
        self.cas.path_for(deployment.kill_proof_digest).write_bytes(
            b"corrupt-after-acceptance"
        )

        snapshot = self.control.control_snapshot()
        self.assertEqual(snapshot["status"], "DEGRADED")
        self.assertIn(
            "DEPLOYMENT_RECEIPT_MISSING:dummy_kalshi",
            snapshot["reason_codes"],
        )
        denied = self.resolve_contract(capital, promotion)
        self.assertEqual(denied.status, 403)
        self.assertIn("deployment receipt", denied.json()["message"])
        with self.assertRaisesRegex(
            EvidenceResolutionError,
            "deployment receipt",
        ):
            self.control.issue_capital_envelope(
                mandate_id=self.mandate_id,
                request=request,
                correlation_id="corrupt-operational-proof",
            )

    def test_missing_passport_or_verdict_cas_artifact_revokes_authority(
        self,
    ) -> None:
        for target in ("passport", "verdict"):
            with self.subTest(target=target):
                self.tearDown()
                self.setUp()
                capital, request, passport_digest, _strategy, promotion_digest = (
                    self.issue_resolver_authority()
                )
                if target == "passport":
                    passport_record = next(
                        record
                        for record in self.ledger.iter_events(
                            schema=AlphaPassportV1.SCHEMA
                        )
                        if record.payload_digest == passport_digest
                    )
                    passport = AlphaPassportV1.from_dict(
                        passport_record.envelope.body
                    )
                    artifact_digest = passport.artifact_hashes[0]
                    body_digest = passport_digest
                else:
                    promotion_record = next(
                        record
                        for record in self.ledger.iter_events(
                            schema=PromotionCertificateV1.SCHEMA
                        )
                        if record.payload_digest == promotion_digest
                    )
                    promotion = PromotionCertificateV1.from_dict(
                        promotion_record.envelope.body
                    )
                    verdict_record = next(
                        record
                        for record in self.ledger.iter_events(
                            schema=EvidenceVerdictV1.SCHEMA
                        )
                        if record.payload_digest == promotion.verdict_digests[0]
                    )
                    verdict = EvidenceVerdictV1.from_dict(
                        verdict_record.envelope.body
                    )
                    artifact_digest = verdict.artifact_hashes[0]
                    body_digest = promotion_digest
                self.cas.path_for(artifact_digest).unlink()

                denied = self.resolve_contract(capital, body_digest)
                self.assertEqual(denied.status, 403)
                self.assertIn("CAS artifacts", denied.json()["message"])
                with self.assertRaisesRegex(
                    EvidenceResolutionError,
                    "CAS artifacts",
                ):
                    self.control.issue_capital_envelope(
                        mandate_id=self.mandate_id,
                        request=request,
                        correlation_id=f"missing-{target}-artifact",
                    )

    def test_openrouter_status_is_provider_owned_and_fail_closed(self) -> None:
        unavailable = self.control.control_snapshot()["openrouter"]
        self.assertIsNone(unavailable["spent_cents"])
        self.assertTrue(unavailable["research_paused"])
        self.assertEqual(unavailable["pause_reason_code"], "STATUS_UNAVAILABLE")
        self.control.model_status_provider = lambda observed: {
            "schema": "dumbmoney.openrouter-budget-status.v1",
            "provider": "OpenRouter",
            "utc_day": observed.date().isoformat(),
            "daily_budget_cents": 1000,
            "spent_cents": 321,
            "remaining_cents": 679,
            "resets_at": "2030-01-02T00:00:00Z",
            "research_paused": False,
            "pause_reason_code": None,
            "unresolved_count": 0,
        }
        observed = self.control.control_snapshot()["openrouter"]
        self.assertEqual(observed["spent_cents"], 321)
        self.assertEqual(observed["remaining_cents"], 679)
        self.control.model_status_provider = lambda _observed: {
            "schema": "unexpected",
        }
        invalid = self.control.control_snapshot()["openrouter"]
        self.assertIsNone(invalid["spent_cents"])
        self.assertTrue(invalid["research_paused"])

    def test_api_exposes_compatibility_snapshot_and_signed_append(self) -> None:
        api = FundApi(self.control)
        primary = api.handle("GET", CONTROL_SNAPSHOT_PATH)
        alias = api.handle("GET", CONTROL_SNAPSHOT_ALIAS)
        self.assertEqual(primary.status, 200)
        self.assertEqual(primary.body, alias.body)
        self.assertEqual(primary.json()["schema"], "dumbmoney.control-snapshot.v1")
        health = api.handle("GET", "/health/ready")
        self.assertEqual(health.status, 503)
        envelope = self.signed(self.mandate(), self.operator, "operator")
        appended = api.handle("POST", "/v1/events:append", canonical_json_bytes(envelope.to_dict()))
        self.assertEqual(appended.status, 201)
        duplicate = api.handle("POST", "/v1/events:append", canonical_json_bytes(envelope.to_dict()))
        self.assertEqual(duplicate.status, 200)
        self.assertTrue(duplicate.json()["duplicate"])
        invalid = api.handle("POST", "/v1/operator/desired-mode", canonical_json_bytes(envelope.to_dict()))
        self.assertEqual(invalid.status, 400)

    def test_cell_command_cursor_delivers_signed_kill_and_mode_fail_closed(self) -> None:
        self.arm_authority()
        api = FundApi(self.control)
        missing = api.handle("GET", "/v1/cells/dummy_kalshi/commands")
        self.assertEqual(missing.status, 400)
        mismatched = api.handle(
            "GET",
            (
                f"/v1/cells/dummy_kalshi/commands?after=0&cursor={'f' * 64}"
                f"&request_nonce={'a' * 64}"
            ),
        )
        self.assertEqual(mismatched.status, 409)
        first = api.handle(
            "GET",
            (
                f"/v1/cells/dummy_kalshi/commands?after=0&cursor={'0' * 64}"
                f"&request_nonce={'b' * 64}"
            ),
        )
        self.assertEqual(first.status, 200)
        page = first.json()
        self.assertEqual(page["cell_id"], "dummy_kalshi")
        self.assertEqual(page["request_nonce"], "b" * 64)
        self.assertEqual(page["required_action"], "APPLY_SIGNED_CONTROLS")
        self.assertEqual(
            [command["body_schema"] for command in page["commands"]],
            ["dumbmoney.desired-mode.v1", "dumbmoney.kill-state.v1"],
        )
        for command in page["commands"]:
            SignedEnvelopeV1.from_dict(command["envelope"]).verify(self.keyring, self.now)
            proof = dict(command["ledger_proof"])
            expected_event_digest = proof.pop("event_digest")
            proof.pop("schema")
            self.assertEqual(
                canonical_sha256(proof),
                expected_event_digest,
            )
            self.assertEqual(
                command["ledger_proof"]["payload_digest"],
                command["envelope"]["body_digest"],
            )
        checkpoint = page["checkpoint"]
        checkpoint_signature = page["checkpoint_signature"]
        self.assertEqual(checkpoint["request_nonce"], "b" * 64)
        self.assertEqual(checkpoint_signature["signer_key_id"], self.core.key_id)
        self.keyring.verify(
            checkpoint_signature["signer_key_id"],
            checkpoint_signature["algorithm"],
            canonical_json_bytes(checkpoint),
            checkpoint_signature["signature"],
        )
        tampered = dict(checkpoint)
        tampered["next_sequence"] = int(tampered["next_sequence"]) + 1
        with self.assertRaises(SignatureVerificationError):
            self.keyring.verify(
                checkpoint_signature["signer_key_id"],
                checkpoint_signature["algorithm"],
                canonical_json_bytes(tampered),
                checkpoint_signature["signature"],
            )
        next_page = api.handle(
            "GET",
            (
                "/v1/cells/dummy_kalshi/commands"
                f"?after={page['next_sequence']}&cursor={page['next_digest']}"
                f"&request_nonce={'c' * 64}"
            ),
        )
        self.assertEqual(next_page.status, 200)
        self.assertEqual(next_page.json()["commands"], [])
        self.assertEqual(next_page.json()["request_nonce"], "c" * 64)
        self.assertNotEqual(
            next_page.json()["checkpoint_signature"]["signature"],
            page["checkpoint_signature"]["signature"],
        )
        stale = api.handle(
            "GET",
            (
                "/v1/cells/dummy_kalshi/commands"
                f"?after={page['next_sequence']}&cursor={'0' * 64}"
                f"&request_nonce={'d' * 64}"
            ),
        )
        self.assertEqual(stale.status, 409)

    def test_cell_command_page_and_kill_append_are_lock_linearized(self) -> None:
        self.arm_authority()
        head_sequence, head_digest = self.ledger.head()
        kill_envelope = self.signed(
            KillStateV1(
                active=True,
                generation=2,
                reason="concurrent command-page kill",
                changed_by="owner",
                policy_epoch=1,
                changed_at=self.now,
            ),
            self.operator,
            "operator",
        )
        entered = threading.Event()
        release_page = threading.Event()
        kill_started = threading.Event()
        original_cursor_digest = self.ledger.cursor_digest

        def delayed_cursor_digest(sequence: int) -> str:
            if sequence == head_sequence and not entered.is_set():
                entered.set()
                self.assertTrue(release_page.wait(timeout=5))
            return original_cursor_digest(sequence)

        self.ledger.cursor_digest = delayed_cursor_digest

        def append_kill():
            kill_started.set()
            return self.control.accept_envelope(kill_envelope)

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                page_future = executor.submit(
                    self.control.cell_commands,
                    cell_id=Venue.DUMMY_KALSHI.value,
                    after_sequence=head_sequence,
                    after_digest=head_digest,
                    request_nonce="f" * 64,
                )
                self.assertTrue(entered.wait(timeout=5))
                kill_future = executor.submit(append_kill)
                self.assertTrue(kill_started.wait(timeout=5))
                self.assertFalse(kill_future.done())
                release_page.set()
                page = page_future.result(timeout=5)
                kill_future.result(timeout=5)
        finally:
            self.ledger.cursor_digest = original_cursor_digest
            release_page.set()

        self.assertEqual(page["ledger_head_sequence"], head_sequence)
        self.assertEqual(page["ledger_head_digest"], head_digest)
        self.assertEqual(page["required_action"], "APPLY_SIGNED_CONTROLS")
        after_kill = self.control.cell_commands(
            cell_id=Venue.DUMMY_KALSHI.value,
            after_sequence=head_sequence,
            after_digest=head_digest,
            request_nonce="0" * 64,
        )
        self.assertEqual(after_kill["required_action"], "CANCEL_AND_RECONCILE")

    def test_control_snapshot_and_kill_append_are_lock_linearized(self) -> None:
        self.arm_authority()
        head_sequence, head_digest = self.ledger.head()
        kill_envelope = self.signed(
            KillStateV1(
                active=True,
                generation=2,
                reason="concurrent readiness kill",
                changed_by="owner",
                policy_epoch=1,
                changed_at=self.now,
            ),
            self.operator,
            "operator",
        )
        entered = threading.Event()
        release_snapshot = threading.Event()
        kill_started = threading.Event()
        original_replay = self.control.replay_state

        def delayed_replay(observed_at=None):
            state = original_replay(observed_at)
            entered.set()
            self.assertTrue(release_snapshot.wait(timeout=5))
            return state

        self.control.replay_state = delayed_replay

        def append_kill():
            kill_started.set()
            return self.control.accept_envelope(kill_envelope)

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                snapshot_future = executor.submit(self.control.control_snapshot)
                self.assertTrue(entered.wait(timeout=5))
                kill_future = executor.submit(append_kill)
                self.assertTrue(kill_started.wait(timeout=5))
                self.assertFalse(kill_future.done())
                release_snapshot.set()
                snapshot = snapshot_future.result(timeout=5)
                kill_future.result(timeout=5)
        finally:
            self.control.replay_state = original_replay
            release_snapshot.set()

        self.assertEqual(
            snapshot["ledger"],
            {
                "last_global_sequence": head_sequence,
                "chain_head": head_digest,
            },
        )
        self.assertFalse(snapshot["kill_state"]["active"])
        self.assertTrue(self.control.control_snapshot()["kill_state"]["active"])

    def test_historical_command_replay_authenticates_without_reactivating_grants(self) -> None:
        self.arm_authority()
        self.now += timedelta(days=31)
        self.clock.value = self.now
        page = self.control.cell_commands(
            cell_id=Venue.DUMMY_KALSHI.value,
            after_sequence=0,
            after_digest="0" * 64,
            request_nonce="e" * 64,
            observed_at=self.now,
        )
        self.assertEqual(
            [command["authority_effect"] for command in page["commands"]],
            ["HISTORICAL_ONLY", "HISTORICAL_ONLY"],
        )
        self.assertEqual(page["required_action"], "CANCEL_AND_RECONCILE")
        for command in page["commands"]:
            historical = SignedEnvelopeV1.from_dict(command["envelope"])
            historical.verify_authenticity(self.keyring)
            self.assertFalse(historical.is_valid_at(self.now))


if __name__ == "__main__":
    unittest.main()
