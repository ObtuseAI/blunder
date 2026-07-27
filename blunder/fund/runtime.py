"""Deterministic authority runtime for DumbMoney.

The runtime resolves signed evidence and issues short-lived capital capability
envelopes.  It does not construct orders, hold broker credentials, or call a
venue.
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, cast

from blunder.fund.canonical import (
    canonical_json_bytes,
    canonical_sha256,
    format_utc,
    require_digest,
    require_identifier,
)
from blunder.fund.cas import ContentIntegrityError
from blunder.fund.contracts import (
    AlphaPassportV1,
    CapitalEnvelopeV1,
    CapitalRequestV1,
    CanonicalContract,
    CellJournalHeadAnchorV1,
    DesiredMode,
    DesiredModeV1,
    EvidenceDecision,
    EvidenceVerdictV1,
    ExecutionIntentV1,
    InheritedExposureReceiptV1,
    InitialReconciliationReceiptV1,
    KillStateV1,
    MigrationManifestV1,
    MigrationReceiptV1,
    OperatingMandateV1,
    OrderLifecycleEventV1,
    OutcomeSettlementV1,
    DeploymentReceiptV1,
    PromotionCertificateV1,
    PromotionStage,
    SignedEnvelopeV1,
    Venue,
    VenueRiskSnapshotV1,
    parse_contract,
    validate_authorized_instrument,
)
from blunder.fund.crypto import Ed25519Keyring, EnvelopeSigner
from blunder.fund.ledger import EventLedger, EventRecord, LedgerConflictError
from blunder.fund.policy import (
    MAX_FUTURE_SKEW,
    CapitalRequest,
    PolicyViolation,
    PortfolioSnapshot,
    RiskPolicyEngine,
)


class AuthorityError(PermissionError):
    """Raised when a signed event is authentic but lacks the required role."""


class EvidenceResolutionError(PermissionError):
    """Raised when a capital request references unresolved or invalid evidence."""


@dataclass(frozen=True)
class DeploymentAuthorityBindings:
    """Release identities a deployment receipt must bind exactly."""

    release_manifest_sha256: str
    core_runner_config_sha256: str
    fund_lock_sha256: str

    def __post_init__(self) -> None:
        require_digest(self.release_manifest_sha256, "release_manifest_sha256")
        require_digest(
            self.core_runner_config_sha256,
            "core_runner_config_sha256",
        )
        require_digest(self.fund_lock_sha256, "fund_lock_sha256")


@dataclass(frozen=True)
class RuntimeState:
    mandate: OperatingMandateV1 | None
    desired_modes: Mapping[Venue, DesiredModeV1]
    kill_state: KillStateV1 | None
    capital_envelopes: Mapping[Venue, CapitalEnvelopeV1]
    reconciliation_receipts: Mapping[Venue, InitialReconciliationReceiptV1]
    deployment_receipts: Mapping[Venue, DeploymentReceiptV1]
    venue_risk_snapshots: Mapping[Venue, VenueRiskSnapshotV1]
    evidence_counts: Mapping[str, int]

    @property
    def kill_active(self) -> bool:
        # Missing kill-state evidence fails closed.
        return self.kill_state is None or self.kill_state.active


@dataclass(frozen=True)
class LiveAuthorityResolution:
    """One atomically resolved, use-time live authority tuple."""

    mandate: OperatingMandateV1
    mandate_record: EventRecord
    desired_mode: DesiredModeV1
    desired_mode_record: EventRecord
    kill_state: KillStateV1
    kill_state_record: EventRecord
    capital: CapitalEnvelopeV1
    capital_record: EventRecord
    passport: AlphaPassportV1
    passport_record: EventRecord
    promotion: PromotionCertificateV1
    promotion_record: EventRecord
    verdicts: tuple[tuple[EvidenceVerdictV1, EventRecord], ...]
    valid_until: datetime
    ledger_head_sequence: int
    ledger_head_digest: str


class FundControlPlane:
    """Resolve signed contracts, enforce roles, and mint fenced capital leases."""

    CORE_SOURCE_ID = "dumbmoney-core"

    def __init__(
        self,
        ledger: EventLedger,
        keyring: Ed25519Keyring,
        signer: EnvelopeSigner,
        policy_engine: RiskPolicyEngine,
        *,
        operator_key_ids: frozenset[str],
        evaluator_key_ids: frozenset[str] = frozenset(),
        promoter_key_ids: frozenset[str] = frozenset(),
        allocator_key_ids: frozenset[str] = frozenset(),
        research_key_ids: frozenset[str] = frozenset(),
        venue_key_ids: Mapping[Venue, frozenset[str]] | None = None,
        migration_key_ids: frozenset[str] = frozenset(),
        deployment_bindings: DeploymentAuthorityBindings | None = None,
        clock: Callable[[], datetime] | None = None,
        model_status_provider: Callable[[datetime], Mapping[str, object]] | None = None,
    ) -> None:
        venue_roles = {
            venue: frozenset(key_ids)
            for venue, key_ids in (
                venue_key_ids or {venue: frozenset() for venue in Venue}
            ).items()
        }
        if set(venue_roles) != set(Venue):
            raise ValueError("venue signer roles must define exactly Dummy and Dopey")
        if venue_roles[Venue.DUMMY_KALSHI] & venue_roles[Venue.DOPEY_ROBINHOOD]:
            raise ValueError("a signer key cannot be assigned to both venue roles")
        role_sets = {
            "operator": operator_key_ids,
            "evaluator": evaluator_key_ids,
            "promoter": promoter_key_ids,
            "allocator": allocator_key_ids,
            "research": research_key_ids,
            "venue:dummy_kalshi": venue_roles[Venue.DUMMY_KALSHI],
            "venue:dopey_robinhood": venue_roles[Venue.DOPEY_ROBINHOOD],
            "migration": migration_key_ids,
        }
        role_names = tuple(role_sets)
        for index, first_name in enumerate(role_names):
            if signer.key_id in role_sets[first_name]:
                raise ValueError(f"core capital signer cannot also be a {first_name} signer")
            for second_name in role_names[index + 1 :]:
                if role_sets[first_name] & role_sets[second_name]:
                    raise ValueError(f"{first_name} and {second_name} signer roles must be disjoint")
        self.ledger = ledger
        self.keyring = keyring
        self.signer = signer
        self.policy_engine = policy_engine
        self.operator_key_ids = operator_key_ids
        self.evaluator_key_ids = evaluator_key_ids
        self.promoter_key_ids = promoter_key_ids
        self.allocator_key_ids = allocator_key_ids
        self.research_key_ids = research_key_ids
        self.venue_key_ids = venue_roles
        self.migration_key_ids = migration_key_ids
        self.deployment_bindings = deployment_bindings
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.model_status_provider = model_status_provider
        self._issue_lock = threading.RLock()
        self._audit_persisted_signer_roles()

    def _parse_verified(self, envelope: SignedEnvelopeV1, now: datetime) -> CanonicalContract:
        return envelope.verify(self.keyring, now)

    def accept_envelope(self, envelope: SignedEnvelopeV1) -> EventRecord:
        """Authorize and append an externally supplied signed contract."""

        with self._issue_lock:
            return self._accept_envelope_locked(envelope)

    def _accept_envelope_locked(self, envelope: SignedEnvelopeV1) -> EventRecord:
        now = self.clock()
        contract = self._parse_verified(envelope, now)
        self._reject_future_contract_time(contract, now)
        if isinstance(contract, OperatingMandateV1):
            self._require_operator(envelope)
            self.policy_engine.validate_mandate(contract, now)
            if self._find_mandate(contract.mandate_id) is not None:
                existing = self._find_mandate(contract.mandate_id)
                if existing is not None and existing.digest() != contract.digest():
                    raise AuthorityError("mandate ID is already bound to a different mandate")
        elif isinstance(contract, DesiredModeV1):
            self._require_operator(envelope)
            self._validate_desired_mode(contract)
        elif isinstance(contract, KillStateV1):
            self._require_operator(envelope)
            self._validate_kill_generation(contract)
        elif isinstance(contract, EvidenceVerdictV1):
            self._require_evaluator(envelope)
            self._validate_evidence_verdict(contract, envelope, now)
        elif isinstance(contract, PromotionCertificateV1):
            self._require_role(envelope, self.promoter_key_ids, "promoter")
            self._validate_promotion(contract, now)
        elif isinstance(contract, AlphaPassportV1):
            # Research signers can submit immutable passports, but passports
            # convey no authority without independently signed verdicts and promotion.
            self._require_role(envelope, self.research_key_ids, "research")
            self._require_cas_artifacts(contract.artifact_hashes, "alpha passport")
        elif isinstance(contract, CapitalRequestV1):
            self._require_role(envelope, self.allocator_key_ids, "capital allocator")
            self._validate_capital_request_contract(contract, now)
        elif isinstance(contract, CapitalEnvelopeV1):
            if envelope.signer_key_id != self.signer.key_id:
                raise AuthorityError("capital envelope was not signed by the configured core signer")
        elif isinstance(contract, CellJournalHeadAnchorV1):
            if envelope.signer_key_id != self.signer.key_id:
                raise AuthorityError(
                    "cell journal anchor was not signed by the configured core signer"
                )
        elif isinstance(contract, ExecutionIntentV1):
            self._require_venue(envelope, contract.venue)
            self._validate_execution_intent(contract, now)
        elif isinstance(
            contract,
            (OrderLifecycleEventV1, OutcomeSettlementV1),
        ):
            self._require_venue(envelope, contract.venue)
        elif isinstance(contract, InitialReconciliationReceiptV1):
            self._require_venue(envelope, contract.venue)
            self._require_cas_artifacts((contract.broker_snapshot_digest,), "reconciliation receipt")
        elif isinstance(contract, VenueRiskSnapshotV1):
            self._require_venue(envelope, contract.venue)
            self._validate_venue_risk_snapshot(contract)
        elif isinstance(contract, InheritedExposureReceiptV1):
            self._require_venue(envelope, contract.venue)
            receipt = self._find_body_digest(
                contract.reconciliation_receipt_digest,
                InitialReconciliationReceiptV1,
            )
            if not isinstance(receipt, InitialReconciliationReceiptV1) or receipt.venue is not contract.venue:
                raise EvidenceResolutionError("inherited exposure references an unresolved reconciliation receipt")
        elif isinstance(contract, (MigrationManifestV1, MigrationReceiptV1)):
            self._require_role(envelope, self.migration_key_ids, "migration")
        elif isinstance(contract, DeploymentReceiptV1):
            self._require_operator(envelope)
            self._validate_deployment_receipt(contract)
        else:
            raise AuthorityError(f"contract schema has no authorized signer role: {contract.SCHEMA}")
        return self.ledger.append(envelope)

    @staticmethod
    def _contract_evidence_times(contract: CanonicalContract) -> tuple[datetime, ...]:
        if isinstance(contract, OperatingMandateV1):
            return (contract.not_before,)
        if isinstance(contract, DesiredModeV1):
            return (contract.not_before,)
        if isinstance(contract, KillStateV1):
            return (contract.changed_at,)
        if isinstance(contract, CapitalRequestV1):
            return (contract.not_before,)
        if isinstance(contract, CapitalEnvelopeV1):
            return (contract.not_before,)
        if isinstance(contract, CellJournalHeadAnchorV1):
            return (contract.anchored_at,)
        if isinstance(contract, AlphaPassportV1):
            return (contract.created_at,)
        if isinstance(contract, EvidenceVerdictV1):
            return (contract.evaluated_at,)
        if isinstance(contract, PromotionCertificateV1):
            return (contract.not_before,)
        if isinstance(contract, ExecutionIntentV1):
            return (contract.created_at,)
        if isinstance(contract, OrderLifecycleEventV1):
            values = [contract.received_at]
            if contract.broker_event_at is not None:
                values.append(contract.broker_event_at)
            return tuple(values)
        if isinstance(contract, OutcomeSettlementV1):
            return (contract.settled_at,)
        if isinstance(contract, MigrationManifestV1):
            return (contract.created_at,)
        if isinstance(contract, MigrationReceiptV1):
            return (contract.completed_at,)
        if isinstance(contract, InitialReconciliationReceiptV1):
            return (contract.observed_at,)
        if isinstance(contract, VenueRiskSnapshotV1):
            return (contract.observed_at,)
        if isinstance(contract, InheritedExposureReceiptV1):
            return (contract.accepted_at,)
        if isinstance(contract, DeploymentReceiptV1):
            return (contract.completed_at,)
        return ()

    def _reject_future_contract_time(self, contract: CanonicalContract, now: datetime) -> None:
        ceiling = now.astimezone(timezone.utc) + MAX_FUTURE_SKEW
        if any(value.astimezone(timezone.utc) > ceiling for value in self._contract_evidence_times(contract)):
            raise AuthorityError("contract evidence timestamp exceeds the allowed future clock skew")

    def _require_operator(self, envelope: SignedEnvelopeV1) -> None:
        if envelope.signer_key_id not in self.operator_key_ids:
            raise AuthorityError("contract requires an authorized operator signer")

    def _require_evaluator(self, envelope: SignedEnvelopeV1) -> None:
        self._require_role(envelope, self.evaluator_key_ids, "independent evaluator")

    @staticmethod
    def _require_role(envelope: SignedEnvelopeV1, key_ids: frozenset[str], role: str) -> None:
        if envelope.signer_key_id not in key_ids:
            raise AuthorityError(f"contract requires an authorized {role} signer")

    def _require_venue(self, envelope: SignedEnvelopeV1, venue: Venue) -> None:
        self._require_role(
            envelope,
            self.venue_key_ids[venue],
            f"{venue.value} venue",
        )

    def _audit_persisted_signer_roles(self) -> None:
        """Reject authentic ledger records signed outside their sealed role."""

        for record in self.ledger.iter_events():
            contract = parse_contract(record.envelope.body)
            if isinstance(
                contract,
                (
                    OperatingMandateV1,
                    DesiredModeV1,
                    KillStateV1,
                    DeploymentReceiptV1,
                ),
            ):
                self._require_operator(record.envelope)
            elif isinstance(contract, EvidenceVerdictV1):
                self._require_evaluator(record.envelope)
            elif isinstance(contract, PromotionCertificateV1):
                self._require_role(
                    record.envelope,
                    self.promoter_key_ids,
                    "promoter",
                )
            elif isinstance(contract, AlphaPassportV1):
                self._require_role(
                    record.envelope,
                    self.research_key_ids,
                    "research",
                )
            elif isinstance(contract, CapitalRequestV1):
                self._require_role(
                    record.envelope,
                    self.allocator_key_ids,
                    "capital allocator",
                )
            elif isinstance(contract, CapitalEnvelopeV1):
                if record.signer_key_id != self.signer.key_id:
                    raise AuthorityError(
                        "persisted capital envelope signer is not configured Core"
                    )
            elif isinstance(contract, CellJournalHeadAnchorV1):
                if record.signer_key_id != self.signer.key_id:
                    raise AuthorityError(
                        "persisted cell journal anchor signer is not configured Core"
                    )
            elif isinstance(
                contract,
                (
                    ExecutionIntentV1,
                    OrderLifecycleEventV1,
                    OutcomeSettlementV1,
                    InitialReconciliationReceiptV1,
                    VenueRiskSnapshotV1,
                    InheritedExposureReceiptV1,
                ),
            ):
                self._require_venue(record.envelope, contract.venue)
            elif isinstance(contract, (MigrationManifestV1, MigrationReceiptV1)):
                self._require_role(
                    record.envelope,
                    self.migration_key_ids,
                    "migration",
                )
            else:
                raise AuthorityError(
                    f"persisted contract schema has no signer role: {contract.SCHEMA}"
                )

    def _validate_desired_mode(self, desired: DesiredModeV1) -> None:
        if desired.policy_epoch != self.policy_engine.policy.policy_epoch:
            raise AuthorityError("desired mode policy epoch mismatch")
        expected_revision = self._maximum_mode_revision(desired.venue) + 1
        if desired.revision != expected_revision:
            raise AuthorityError(
                f"desired mode revision is not contiguous: expected={expected_revision}; observed={desired.revision}"
            )

    def _maximum_mode_revision(self, venue: Venue) -> int:
        maximum = 0
        for record in self.ledger.iter_events(schema=DesiredModeV1.SCHEMA):
            desired = DesiredModeV1.from_dict(record.envelope.body)
            if desired.venue is venue:
                maximum = max(maximum, desired.revision)
        return maximum

    def _validate_kill_generation(self, kill: KillStateV1) -> None:
        if kill.policy_epoch != self.policy_engine.policy.policy_epoch:
            raise AuthorityError("kill-state policy epoch mismatch")
        expected_generation = self._maximum_kill_generation() + 1
        if kill.generation != expected_generation:
            raise AuthorityError(
                f"kill-state generation is not contiguous: expected={expected_generation}; observed={kill.generation}"
            )

    def _maximum_kill_generation(self) -> int:
        """Return the maximum ever generation, including historical clears."""

        maximum = 0
        for record in self.ledger.iter_events(schema=KillStateV1.SCHEMA):
            kill = KillStateV1.from_dict(record.envelope.body)
            maximum = max(maximum, kill.generation)
        return maximum

    def _unresolved_cas_artifacts(self, digests: tuple[str, ...]) -> list[str]:
        """Return missing or corrupt content addresses without leaking paths."""

        unresolved: list[str] = []
        for digest in digests:
            try:
                self.ledger.cas.get_bytes(digest)
            except (FileNotFoundError, ContentIntegrityError, OSError):
                unresolved.append(digest)
        return unresolved

    def _cas_artifacts_are_current(self, digests: tuple[str, ...]) -> bool:
        return not self._unresolved_cas_artifacts(digests)

    def _require_cas_artifacts(self, digests: tuple[str, ...], context: str) -> None:
        unresolved = self._unresolved_cas_artifacts(digests)
        if unresolved:
            raise EvidenceResolutionError(
                f"{context} references unresolved CAS artifacts: {unresolved}"
            )

    @staticmethod
    def _deployment_artifact_digests(
        contract: DeploymentReceiptV1,
    ) -> tuple[str, ...]:
        return (
            contract.release_manifest_digest,
            contract.place_cancel_proof_digest,
            contract.restart_proof_digest,
            contract.kill_proof_digest,
            contract.restore_proof_digest,
        )

    def _validate_evidence_verdict(
        self,
        contract: EvidenceVerdictV1,
        envelope: SignedEnvelopeV1,
        now: datetime,
    ) -> None:
        if contract.expires_at <= now.astimezone(timezone.utc):
            raise EvidenceResolutionError("evaluator contract is expired")
        if contract.evaluator_id != envelope.signer_key_id:
            raise EvidenceResolutionError("evaluator_id must equal the signed evaluator key ID")
        passport = self._find_body_digest(
            contract.passport_digest,
            AlphaPassportV1,
        )
        if not isinstance(passport, AlphaPassportV1):
            raise EvidenceResolutionError("evidence verdict references an unresolved alpha passport")
        self._require_cas_artifacts(
            passport.artifact_hashes,
            "evidence verdict alpha passport",
        )
        self._require_cas_artifacts(contract.artifact_hashes, "evidence verdict")

    def _verdict_record(self, digest: str) -> tuple[EvidenceVerdictV1, EventRecord] | None:
        found: tuple[EvidenceVerdictV1, EventRecord] | None = None
        for record in self.ledger.iter_events(schema=EvidenceVerdictV1.SCHEMA):
            if record.payload_digest == digest:
                verdict = EvidenceVerdictV1.from_dict(record.envelope.body)
                found = verdict, record
        return found

    def _latest_passport_identity(
        self,
        passport: AlphaPassportV1,
    ) -> tuple[AlphaPassportV1, EventRecord] | None:
        found: tuple[AlphaPassportV1, EventRecord] | None = None
        for record in self.ledger.iter_events(schema=AlphaPassportV1.SCHEMA):
            candidate = AlphaPassportV1.from_dict(record.envelope.body)
            if (
                candidate.passport_id == passport.passport_id
                or candidate.strategy_lineage_id == passport.strategy_lineage_id
            ):
                found = candidate, record
        return found

    def _latest_promotion_identity(
        self,
        promotion: PromotionCertificateV1,
    ) -> tuple[PromotionCertificateV1, EventRecord] | None:
        found: tuple[PromotionCertificateV1, EventRecord] | None = None
        for record in self.ledger.iter_events(
            schema=PromotionCertificateV1.SCHEMA
        ):
            candidate = PromotionCertificateV1.from_dict(record.envelope.body)
            if (
                candidate.certificate_id == promotion.certificate_id
                or candidate.passport_digest == promotion.passport_digest
            ):
                found = candidate, record
        return found

    def _latest_verdict_identity(
        self,
        verdict: EvidenceVerdictV1,
    ) -> tuple[EvidenceVerdictV1, EventRecord] | None:
        found: tuple[EvidenceVerdictV1, EventRecord] | None = None
        for record in self.ledger.iter_events(schema=EvidenceVerdictV1.SCHEMA):
            candidate = EvidenceVerdictV1.from_dict(record.envelope.body)
            if candidate.verdict_id == verdict.verdict_id or (
                candidate.passport_digest == verdict.passport_digest
                and candidate.court == verdict.court
            ):
                found = candidate, record
        return found

    def _validate_promotion(self, contract: PromotionCertificateV1, now: datetime) -> None:
        if contract.expires_at <= now.astimezone(timezone.utc):
            raise EvidenceResolutionError("promotion certificate is expired")
        if contract.policy_epoch != self.policy_engine.policy.policy_epoch:
            raise EvidenceResolutionError("promotion policy epoch mismatch")
        resolved_passport = self._find_record_by_body_digest(
            contract.passport_digest,
            AlphaPassportV1,
        )
        if resolved_passport is None:
            raise EvidenceResolutionError("promotion references an unresolved alpha passport")
        passport, passport_record = resolved_passport
        if not isinstance(passport, AlphaPassportV1):
            raise EvidenceResolutionError("promotion references an invalid alpha passport")
        latest_passport = self._latest_passport_identity(passport)
        if (
            passport_record.signer_key_id not in self.research_key_ids
            or not passport_record.envelope.is_valid_at(now)
            or not passport.created_at <= now < passport.expires_at
            or latest_passport is None
            or latest_passport[1].payload_digest != contract.passport_digest
        ):
            raise EvidenceResolutionError(
                "promotion references a stale or superseded alpha passport"
            )
        self._require_cas_artifacts(
            passport.artifact_hashes,
            "promotion alpha passport",
        )
        if contract.venue is not passport.venue:
            raise EvidenceResolutionError("promotion venue differs from alpha passport")
        if not set(contract.instruments) <= set(passport.intended_instruments):
            raise EvidenceResolutionError("promotion instruments exceed alpha passport authority")
        if contract.maximum_loss_cents > passport.maximum_loss_cents:
            raise EvidenceResolutionError("promotion maximum loss exceeds alpha passport authority")
        if contract.stage in {
            PromotionStage.EXPLORATORY_LIVE,
            PromotionStage.AGGRESSIVE_BOUNDED,
        }:
            for index, instrument in enumerate(contract.instruments):
                try:
                    validate_authorized_instrument(
                        contract.venue,
                        instrument,
                        f"promotion.instruments[{index}]",
                    )
                except (TypeError, ValueError) as exc:
                    raise EvidenceResolutionError(
                        "live promotion requires exact venue instrument IDs"
                    ) from exc
        courts: set[str] = set()
        for verdict_digest in contract.verdict_digests:
            resolved = self._verdict_record(verdict_digest)
            if resolved is None:
                raise EvidenceResolutionError("promotion references an unresolved evidence verdict")
            verdict, record = resolved
            if record.signer_key_id not in self.evaluator_key_ids:
                raise EvidenceResolutionError("promotion references a verdict without evaluator authority")
            if verdict.evaluator_id != record.signer_key_id:
                raise EvidenceResolutionError("verdict evaluator identity does not match its signer")
            if verdict.passport_digest != contract.passport_digest:
                raise EvidenceResolutionError("evidence verdict is bound to a different passport")
            latest_verdict = self._latest_verdict_identity(verdict)
            if (
                latest_verdict is None
                or latest_verdict[1].payload_digest != verdict_digest
                or not record.envelope.is_valid_at(now)
            ):
                raise EvidenceResolutionError(
                    "promotion references a stale or superseded evidence verdict"
                )
            if verdict.decision is not EvidenceDecision.PASS:
                raise EvidenceResolutionError("promotion references a non-passing evidence verdict")
            if verdict.expires_at <= now.astimezone(timezone.utc):
                raise EvidenceResolutionError("promotion references an expired evidence verdict")
            self._require_cas_artifacts(
                verdict.artifact_hashes,
                "promotion evidence verdict",
            )
            if verdict.court in courts:
                raise EvidenceResolutionError(f"promotion contains duplicate evidence court: {verdict.court}")
            courts.add(verdict.court)
        if contract.stage is PromotionStage.EXPLORATORY_LIVE:
            required = {"integrity", "adversarial_operations"}
        elif contract.stage is PromotionStage.AGGRESSIVE_BOUNDED:
            required = {"integrity", "statistics", "economics", "adversarial_operations"}
        else:
            required = set()
        missing = sorted(required - courts)
        if missing:
            raise EvidenceResolutionError(f"promotion is missing required evidence courts: {missing}")

    def _validate_deployment_receipt(self, contract: DeploymentReceiptV1) -> None:
        if not self._deployment_receipt_matches_current_release(contract):
            raise EvidenceResolutionError(
                "deployment receipt does not bind the current sealed release"
            )
        reconciliation = self._find_body_digest(
            contract.reconciliation_receipt_digest,
            InitialReconciliationReceiptV1,
        )
        if not isinstance(reconciliation, InitialReconciliationReceiptV1):
            raise EvidenceResolutionError("deployment references an unresolved reconciliation receipt")
        if reconciliation.venue is not contract.venue:
            raise EvidenceResolutionError("deployment and reconciliation venue mismatch")
        self._require_cas_artifacts(
            self._deployment_artifact_digests(contract),
            "deployment receipt",
        )

    def _deployment_receipt_matches_current_release(
        self,
        contract: DeploymentReceiptV1,
    ) -> bool:
        bindings = self.deployment_bindings
        return (
            bindings is not None
            and contract.release_manifest_digest
            == bindings.release_manifest_sha256
            and contract.core_runner_config_sha256
            == bindings.core_runner_config_sha256
            and contract.fund_lock_sha256 == bindings.fund_lock_sha256
        )

    @staticmethod
    def _policy_request(contract: CapitalRequestV1) -> CapitalRequest:
        return CapitalRequest(
            venue=contract.venue,
            account_hash=contract.account_hash,
            strategy_hashes=contract.strategy_hashes,
            passport_hashes=contract.passport_hashes,
            promotion_hashes=contract.promotion_hashes,
            authorized_instruments=contract.authorized_instruments,
            correlation_cluster=contract.correlation_cluster,
            max_order_risk_cents=contract.max_order_risk_cents,
            max_open_risk_cents=contract.max_open_risk_cents,
            max_correlated_risk_cents=contract.max_correlated_risk_cents,
            max_daily_loss_cents=contract.max_daily_loss_cents,
            max_open_orders=contract.max_open_orders,
        )

    def _validate_capital_request_contract(
        self,
        contract: CapitalRequestV1,
        observed_at: datetime,
    ) -> None:
        now = observed_at.astimezone(timezone.utc)
        if contract.policy_epoch != self.policy_engine.policy.policy_epoch:
            raise AuthorityError("capital request policy epoch mismatch")
        if not contract.not_before <= now < contract.expires_at:
            raise AuthorityError("capital request is not current")
        if (
            contract.expires_at - contract.not_before
            > timedelta(seconds=self.policy_engine.policy.capital_envelope_ttl_seconds)
        ):
            raise AuthorityError("capital request validity exceeds the capital lease TTL")
        state = self.replay_state(now)
        if state.mandate is None or state.mandate.mandate_id != contract.mandate_id:
            raise AuthorityError(
                "capital request must reference the one current, non-superseded mandate"
            )
        self._resolve_passports(self._policy_request(contract), now)

    def _validate_venue_risk_snapshot(self, contract: VenueRiskSnapshotV1) -> None:
        reconciliation = self._find_body_digest(
            contract.reconciliation_receipt_digest,
            InitialReconciliationReceiptV1,
        )
        if not isinstance(reconciliation, InitialReconciliationReceiptV1):
            raise EvidenceResolutionError("venue risk references an unresolved reconciliation receipt")
        if reconciliation.venue is not contract.venue:
            raise EvidenceResolutionError("venue risk and reconciliation venue mismatch")
        if reconciliation.account_hash != contract.account_hash:
            raise EvidenceResolutionError("venue risk and reconciliation account mismatch")
        if reconciliation.broker_snapshot_digest != contract.broker_snapshot_digest:
            raise EvidenceResolutionError("venue risk and reconciliation broker snapshot mismatch")
        if reconciliation.open_orders != contract.open_orders:
            raise EvidenceResolutionError("venue risk and reconciliation open-order count mismatch")
        if reconciliation.open_positions != contract.open_positions:
            raise EvidenceResolutionError("venue risk and reconciliation open-position count mismatch")
        if contract.observed_at < reconciliation.observed_at - MAX_FUTURE_SKEW:
            raise EvidenceResolutionError("venue risk predates its reconciliation receipt")

    def _validate_execution_intent(
        self,
        contract: ExecutionIntentV1,
        observed_at: datetime,
    ) -> None:
        now = observed_at.astimezone(timezone.utc)
        authority = self._resolve_live_authority_tuple(
            venue=contract.venue,
            capital_envelope_digest=contract.capital_envelope_digest,
            observed_at=now,
        )
        capital = authority.capital
        capital_record = authority.capital_record
        if capital.venue is not contract.venue:
            raise EvidenceResolutionError("execution intent and capital venue mismatch")
        if capital.account_hash != contract.account_hash:
            raise EvidenceResolutionError("execution intent and capital account mismatch")
        if (
            not capital_record.envelope.is_valid_at(now)
            or not capital.not_before <= now < capital.expires_at
        ):
            raise EvidenceResolutionError("execution intent capital envelope is not current")
        current_capital = self.replay_state(now).capital_envelopes.get(contract.venue)
        if (
            current_capital is None
            or current_capital.digest() != contract.capital_envelope_digest
            or capital.fencing_generation
            != self._maximum_fencing_generation(contract.venue)
        ):
            raise EvidenceResolutionError(
                "execution intent does not reference the current maximum capital fence"
            )
        if not (
            capital.not_before
            <= contract.created_at
            <= now
            < contract.expires_at
            <= capital.expires_at
        ):
            raise EvidenceResolutionError(
                "execution intent validity must be contained by current capital authority"
            )
        if contract.authorized_instrument not in capital.authorized_instruments:
            raise EvidenceResolutionError(
                "execution intent instrument is outside capital authority"
            )
        if contract.instrument_type == "option":
            expected_instrument_id = contract.authorized_instrument.split(":", 2)[2]
        else:
            expected_instrument_id = contract.authorized_instrument.split(":", 1)[1]
        if contract.instrument_id != expected_instrument_id:
            raise EvidenceResolutionError(
                "execution intent instrument_id does not match exact authorized instrument"
            )
        if contract.passport_digest not in capital.passport_hashes:
            raise EvidenceResolutionError(
                "execution intent passport is outside capital authority"
            )
        if contract.promotion_digest not in capital.promotion_hashes:
            raise EvidenceResolutionError(
                "execution intent promotion is outside capital authority"
            )
        if contract.maximum_loss_cents > capital.max_order_risk_cents:
            raise EvidenceResolutionError(
                "execution intent maximum loss exceeds capital order authority"
            )
        if contract.strategy_hash != capital.strategy_hashes[0]:
            raise EvidenceResolutionError(
                "execution intent strategy is outside its singleton capital authority"
            )
        resolved_passport = self._find_record_by_body_digest(
            contract.passport_digest,
            AlphaPassportV1,
        )
        resolved_promotion = self._find_record_by_body_digest(
            contract.promotion_digest,
            PromotionCertificateV1,
        )
        if resolved_passport is None or resolved_promotion is None:
            raise EvidenceResolutionError(
                "execution intent authority tuple cannot be resolved"
            )
        passport, passport_record = resolved_passport
        promotion, promotion_record = resolved_promotion
        if not isinstance(passport, AlphaPassportV1) or not isinstance(
            promotion,
            PromotionCertificateV1,
        ):
            raise EvidenceResolutionError(
                "execution intent authority tuple has invalid contract types"
            )
        if (
            not passport_record.envelope.is_valid_at(now)
            or not passport.created_at <= now < passport.expires_at
            or not promotion_record.envelope.is_valid_at(now)
            or not promotion.not_before <= now < promotion.expires_at
        ):
            raise EvidenceResolutionError(
                "execution intent authority tuple is not current"
            )
        if (
            passport_record.signer_key_id not in self.research_key_ids
            or promotion_record.signer_key_id not in self.promoter_key_ids
        ):
            raise EvidenceResolutionError(
                "execution intent authority tuple has an unauthorized signer role"
            )
        if promotion.stage not in {
            PromotionStage.EXPLORATORY_LIVE,
            PromotionStage.AGGRESSIVE_BOUNDED,
        }:
            raise EvidenceResolutionError(
                "execution intent promotion is not a live stage"
            )
        if (
            passport.venue is not contract.venue
            or promotion.venue is not contract.venue
            or promotion.passport_digest != contract.passport_digest
            or passport.strategy_hash != contract.strategy_hash
            or contract.authorized_instrument not in passport.intended_instruments
            or contract.authorized_instrument not in promotion.instruments
        ):
            raise EvidenceResolutionError(
                "execution intent does not match one signed authority tuple"
            )
        if (
            contract.maximum_loss_cents > passport.maximum_loss_cents
            or contract.maximum_loss_cents > promotion.maximum_loss_cents
        ):
            raise EvidenceResolutionError(
                "execution intent maximum loss exceeds its signed authority tuple"
            )

    def replay_state(self, observed_at: datetime | None = None) -> RuntimeState:
        now = (observed_at or self.clock()).astimezone(timezone.utc)
        mandate: OperatingMandateV1 | None = None
        modes: dict[Venue, DesiredModeV1] = {}
        kill: KillStateV1 | None = None
        envelopes: dict[Venue, CapitalEnvelopeV1] = {}
        reconciliation_receipts: dict[Venue, InitialReconciliationReceiptV1] = {}
        deployment_receipts: dict[Venue, DeploymentReceiptV1] = {}
        venue_risk_snapshots: dict[Venue, VenueRiskSnapshotV1] = {}
        evidence_counts: Counter[str] = Counter()
        for record in self.ledger.iter_events():
            body = parse_contract(record.envelope.body)
            if isinstance(body, OperatingMandateV1):
                # A newer mandate supersedes every older mandate. Expiry never
                # rolls authority back to an earlier, broader mandate.
                mandate = (
                    body
                    if record.envelope.is_valid_at(now)
                    and body.not_before <= now < body.expires_at
                    else None
                )
            elif isinstance(body, DesiredModeV1):
                # Likewise, an expired newer mode cannot reveal an older LIVE
                # event. Historical pause/read-only controls remain fail-closed;
                # absence also resolves to PAUSED.
                modes.pop(body.venue, None)
                body_current = body.not_before <= now < body.expires_at
                transport_current = record.envelope.is_valid_at(now)
                if body_current and (
                    body.mode is not DesiredMode.LIVE or transport_current
                ):
                    modes[body.venue] = body
            elif isinstance(body, KillStateV1):
                # A historical kill remains authoritative. A kill clear is a
                # positive grant and therefore requires a current transport
                # signature; once it expires, absence fails closed.
                if body.active:
                    kill = body
                else:
                    kill = (
                        body
                        if record.envelope.is_valid_at(now)
                        and body.changed_at <= now + MAX_FUTURE_SKEW
                        else None
                    )
            elif isinstance(body, CapitalEnvelopeV1):
                envelopes.pop(body.venue, None)
                if (
                    record.envelope.is_valid_at(now)
                    and body.not_before <= now < body.expires_at
                ):
                    envelopes[body.venue] = body
            elif isinstance(body, InitialReconciliationReceiptV1):
                # The newest receipt supersedes first. Evidence loss must not
                # reveal an older receipt whose blob happens to remain.
                reconciliation_receipts.pop(body.venue, None)
                if self._cas_artifacts_are_current(
                    (body.broker_snapshot_digest,)
                ):
                    reconciliation_receipts[body.venue] = body
            elif isinstance(body, DeploymentReceiptV1):
                # A receipt from another release must not survive a restart or
                # reveal an older matching receipt. The latest record for a
                # venue supersedes first, then qualifies against this process's
                # immutable release bindings.
                deployment_receipts.pop(body.venue, None)
                if (
                    self._deployment_receipt_matches_current_release(body)
                    and self._cas_artifacts_are_current(
                        self._deployment_artifact_digests(body)
                    )
                ):
                    deployment_receipts[body.venue] = body
            elif isinstance(body, VenueRiskSnapshotV1):
                venue_risk_snapshots.pop(body.venue, None)
                if self._cas_artifacts_are_current(
                    (body.broker_snapshot_digest,)
                ):
                    venue_risk_snapshots[body.venue] = body
            if isinstance(body, (AlphaPassportV1, EvidenceVerdictV1, PromotionCertificateV1)):
                evidence_counts[body.SCHEMA] += 1
        return RuntimeState(
            mandate=mandate,
            desired_modes=dict(modes),
            kill_state=kill,
            capital_envelopes=dict(envelopes),
            reconciliation_receipts=dict(reconciliation_receipts),
            deployment_receipts=dict(deployment_receipts),
            venue_risk_snapshots=dict(venue_risk_snapshots),
            evidence_counts=dict(evidence_counts),
        )

    def _find_mandate(self, mandate_id: str) -> OperatingMandateV1 | None:
        found: OperatingMandateV1 | None = None
        for record in self.ledger.iter_events(schema=OperatingMandateV1.SCHEMA):
            mandate = OperatingMandateV1.from_dict(record.envelope.body)
            if mandate.mandate_id == mandate_id:
                found = mandate
        return found

    def _find_body_digest(
        self,
        digest: str,
        contract_type: type[CanonicalContract],
    ) -> CanonicalContract | None:
        resolved = self._find_record_by_body_digest(digest, contract_type)
        return None if resolved is None else resolved[0]

    def _find_record_by_body_digest(
        self,
        digest: str,
        contract_type: type[CanonicalContract],
    ) -> tuple[CanonicalContract, EventRecord] | None:
        found: tuple[CanonicalContract, EventRecord] | None = None
        for record in self.ledger.iter_events(schema=contract_type.SCHEMA):
            if record.payload_digest == digest:
                found = parse_contract(record.envelope.body), record
        return found

    def _resolve_passports(
        self,
        request: CapitalRequest,
        observed_at: datetime,
    ) -> None:
        now = observed_at.astimezone(timezone.utc)
        resolved_strategy_hashes: set[str] = set()
        for passport_digest in request.passport_hashes:
            resolved_passport = self._find_record_by_body_digest(
                passport_digest,
                AlphaPassportV1,
            )
            if resolved_passport is None:
                raise EvidenceResolutionError(f"unresolved alpha passport: {passport_digest}")
            raw_passport, passport_record = resolved_passport
            if not isinstance(raw_passport, AlphaPassportV1):
                raise EvidenceResolutionError(f"unresolved alpha passport: {passport_digest}")
            if raw_passport.venue is not request.venue:
                raise EvidenceResolutionError("alpha passport venue mismatch")
            self._require_cas_artifacts(
                raw_passport.artifact_hashes,
                "alpha passport",
            )
            if (
                not passport_record.envelope.is_valid_at(now)
                or not raw_passport.created_at <= now < raw_passport.expires_at
            ):
                raise EvidenceResolutionError("alpha passport is not current")
            if passport_record.signer_key_id not in self.research_key_ids:
                raise EvidenceResolutionError(
                    "alpha passport signer lacks research authority"
                )
            latest_passport = self._latest_passport_identity(raw_passport)
            if (
                latest_passport is None
                or latest_passport[1].payload_digest != passport_digest
            ):
                raise EvidenceResolutionError(
                    "alpha passport has been superseded or revoked"
                )
            resolved_strategy_hashes.add(raw_passport.strategy_hash)
            promotions_by_digest: dict[
                str,
                tuple[PromotionCertificateV1, EventRecord],
            ] = {}
            for record in self.ledger.iter_events(
                schema=PromotionCertificateV1.SCHEMA
            ):
                if (
                    cast(str, record.envelope.body.get("passport_digest"))
                    != passport_digest
                ):
                    continue
                promotions_by_digest[record.payload_digest] = (
                    cast(
                        PromotionCertificateV1,
                        parse_contract(record.envelope.body),
                    ),
                    record,
                )
            eligible = [
                (promotion, digest, record)
                for digest, (promotion, record) in promotions_by_digest.items()
                if promotion.venue is request.venue
                and record.signer_key_id in self.promoter_key_ids
                and record.envelope.is_valid_at(now)
                and promotion.not_before <= now < promotion.expires_at
                and promotion.stage in {PromotionStage.EXPLORATORY_LIVE, PromotionStage.AGGRESSIVE_BOUNDED}
                and promotion.policy_epoch == self.policy_engine.policy.policy_epoch
                and digest in request.promotion_hashes
            ]
            if len(eligible) != 1:
                raise EvidenceResolutionError(
                    "alpha passport must resolve to exactly one requested live promotion certificate"
                )
            # Resolve every verdict on the newest eligible certificate again at
            # use time; an unresolved digest never becomes a boolean assertion.
            promotion, promotion_digest, _promotion_record = eligible[0]
            latest_promotion = self._latest_promotion_identity(promotion)
            if (
                latest_promotion is None
                or latest_promotion[1].payload_digest != promotion_digest
            ):
                raise EvidenceResolutionError(
                    "promotion certificate has been superseded or revoked"
                )
            if not set(request.authorized_instruments) <= set(raw_passport.intended_instruments):
                raise EvidenceResolutionError("request instruments exceed alpha passport authority")
            if not set(request.authorized_instruments) <= set(promotion.instruments):
                raise EvidenceResolutionError("request instruments exceed promotion authority")
            if request.max_order_risk_cents > raw_passport.maximum_loss_cents:
                raise EvidenceResolutionError("request order risk exceeds alpha passport maximum loss")
            if request.max_order_risk_cents > promotion.maximum_loss_cents:
                raise EvidenceResolutionError("request order risk exceeds promotion maximum loss")
            for verdict_digest in promotion.verdict_digests:
                resolved = self._verdict_record(verdict_digest)
                if resolved is None:
                    raise EvidenceResolutionError("promotion verdict is unresolved")
                verdict, record = resolved
                if record.signer_key_id not in self.evaluator_key_ids:
                    raise EvidenceResolutionError("promotion verdict signer lacks evaluator authority")
                if verdict.evaluator_id != record.signer_key_id:
                    raise EvidenceResolutionError("promotion verdict evaluator identity mismatch")
                latest_verdict = self._latest_verdict_identity(verdict)
                if (
                    latest_verdict is None
                    or latest_verdict[1].payload_digest != verdict_digest
                ):
                    raise EvidenceResolutionError(
                        "promotion verdict has been superseded or revoked"
                    )
                if (
                    not record.envelope.is_valid_at(now)
                    or verdict.decision is not EvidenceDecision.PASS
                    or verdict.expires_at <= now
                ):
                    raise EvidenceResolutionError("promotion verdict is not a current PASS")
                self._require_cas_artifacts(
                    verdict.artifact_hashes,
                    "promotion verdict",
                )
            self._validate_promotion(promotion, now)
        if resolved_strategy_hashes != set(request.strategy_hashes):
            raise EvidenceResolutionError("request strategy hashes do not exactly match resolved passports")
        if {
            record.payload_digest
            for record in self.ledger.iter_events(schema=PromotionCertificateV1.SCHEMA)
            if record.payload_digest in request.promotion_hashes
        } != set(request.promotion_hashes):
            raise EvidenceResolutionError("request contains unresolved or unused promotion hashes")

    def _resolve_live_authority_tuple(
        self,
        *,
        venue: Venue,
        capital_envelope_digest: str,
        observed_at: datetime,
    ) -> LiveAuthorityResolution:
        """Re-evaluate every positive authority input at point of use.

        Supersession is ledger-native and fail-closed: the latest event for a
        passport identity/lineage, promotion passport, or verdict
        passport/court is authoritative. A later non-live promotion or
        non-PASS verdict therefore revokes an older referenced digest without
        inventing a separate unsigned revocation channel.
        """

        now = observed_at.astimezone(timezone.utc)
        state = self.replay_state(now)
        policy_epoch = self.policy_engine.policy.policy_epoch
        capital = state.capital_envelopes.get(venue)
        if (
            capital is None
            or capital.digest() != capital_envelope_digest
            or capital.fencing_generation != self._maximum_fencing_generation(venue)
        ):
            raise EvidenceResolutionError(
                "use-time authority requires the current maximum capital fence"
            )
        if capital.policy_epoch != policy_epoch:
            raise EvidenceResolutionError(
                "capital envelope policy epoch is not current"
            )
        mandate = state.mandate
        if (
            mandate is None
            or mandate.mandate_id != capital.mandate_id
            or mandate.policy_epoch != policy_epoch
            or mandate.account_hashes.get(venue.value) != capital.account_hash
        ):
            raise EvidenceResolutionError(
                "capital envelope mandate is not current and account-bound"
            )
        desired = state.desired_modes.get(venue)
        if (
            desired is None
            or desired.mode is not DesiredMode.LIVE
            or desired.policy_epoch != policy_epoch
        ):
            raise EvidenceResolutionError(
                "venue desired mode is not current LIVE authority"
            )
        kill = state.kill_state
        if (
            state.kill_active
            or kill is None
            or kill.active
            or kill.policy_epoch != policy_epoch
        ):
            raise EvidenceResolutionError(
                "global kill is not currently cleared under this policy epoch"
            )

        resolved_capital = self._find_record_by_body_digest(
            capital_envelope_digest,
            CapitalEnvelopeV1,
        )
        resolved_mandate = self._find_record_by_body_digest(
            mandate.digest(),
            OperatingMandateV1,
        )
        resolved_desired = self._find_record_by_body_digest(
            desired.digest(),
            DesiredModeV1,
        )
        resolved_kill = self._find_record_by_body_digest(
            kill.digest(),
            KillStateV1,
        )
        if (
            resolved_capital is None
            or resolved_mandate is None
            or resolved_desired is None
            or resolved_kill is None
        ):
            raise EvidenceResolutionError(
                "current operating authority lacks immutable ledger provenance"
            )
        capital_body, capital_record = resolved_capital
        mandate_body, mandate_record = resolved_mandate
        desired_body, desired_record = resolved_desired
        kill_body, kill_record = resolved_kill
        if not (
            isinstance(capital_body, CapitalEnvelopeV1)
            and isinstance(mandate_body, OperatingMandateV1)
            and isinstance(desired_body, DesiredModeV1)
            and isinstance(kill_body, KillStateV1)
        ):
            raise EvidenceResolutionError(
                "current operating authority provenance has invalid contract types"
            )
        if (
            capital_record.signer_key_id != self.signer.key_id
            or mandate_record.signer_key_id not in self.operator_key_ids
            or desired_record.signer_key_id not in self.operator_key_ids
            or kill_record.signer_key_id not in self.operator_key_ids
        ):
            raise EvidenceResolutionError(
                "current operating authority signer roles are invalid"
            )

        use_time_request = CapitalRequest(
            venue=capital.venue,
            account_hash=capital.account_hash,
            strategy_hashes=capital.strategy_hashes,
            passport_hashes=capital.passport_hashes,
            promotion_hashes=capital.promotion_hashes,
            authorized_instruments=capital.authorized_instruments,
            correlation_cluster="use-time-authority",
            max_order_risk_cents=capital.max_order_risk_cents,
            max_open_risk_cents=capital.max_open_risk_cents,
            max_correlated_risk_cents=capital.max_correlated_risk_cents,
            max_daily_loss_cents=capital.max_daily_loss_cents,
            max_open_orders=capital.max_open_orders,
        )
        self._require_operational_evidence(
            state,
            mandate,
            use_time_request,
            now,
        )
        self._resolve_passports(use_time_request, now)
        resolved_passport = self._find_record_by_body_digest(
            capital.passport_hashes[0],
            AlphaPassportV1,
        )
        resolved_promotion = self._find_record_by_body_digest(
            capital.promotion_hashes[0],
            PromotionCertificateV1,
        )
        if resolved_passport is None or resolved_promotion is None:
            raise EvidenceResolutionError(
                "current capital authority tuple cannot be resolved"
            )
        passport_body, passport_record = resolved_passport
        promotion_body, promotion_record = resolved_promotion
        if not isinstance(passport_body, AlphaPassportV1) or not isinstance(
            promotion_body,
            PromotionCertificateV1,
        ):
            raise EvidenceResolutionError(
                "current capital authority tuple has invalid contract types"
            )
        verdicts: list[tuple[EvidenceVerdictV1, EventRecord]] = []
        for verdict_digest in promotion_body.verdict_digests:
            resolved_verdict = self._verdict_record(verdict_digest)
            if resolved_verdict is None:
                raise EvidenceResolutionError(
                    "current promotion verdict cannot be resolved"
                )
            verdict, verdict_record = resolved_verdict
            latest_verdict = self._latest_verdict_identity(verdict)
            if (
                latest_verdict is None
                or latest_verdict[1].payload_digest != verdict_digest
                or verdict.decision is not EvidenceDecision.PASS
                or verdict.expires_at <= now
                or not verdict_record.envelope.is_valid_at(now)
            ):
                raise EvidenceResolutionError(
                    "current promotion verdict is revoked, expired, or not PASS"
                )
            verdicts.append((verdict, verdict_record))

        valid_until = min(
            capital.expires_at,
            capital_record.envelope.expires_at,
            mandate.expires_at,
            mandate_record.envelope.expires_at,
            desired.expires_at,
            desired_record.envelope.expires_at,
            kill_record.envelope.expires_at,
            passport_body.expires_at,
            passport_record.envelope.expires_at,
            promotion_body.expires_at,
            promotion_record.envelope.expires_at,
            *(
                expiry
                for verdict, verdict_record in verdicts
                for expiry in (
                    verdict.expires_at,
                    verdict_record.envelope.expires_at,
                )
            ),
        )
        if valid_until <= now:
            raise EvidenceResolutionError(
                "use-time authority has no remaining positive validity"
            )
        head_sequence, head_digest = self.ledger.head()
        return LiveAuthorityResolution(
            mandate=mandate,
            mandate_record=mandate_record,
            desired_mode=desired,
            desired_mode_record=desired_record,
            kill_state=kill,
            kill_state_record=kill_record,
            capital=capital,
            capital_record=capital_record,
            passport=passport_body,
            passport_record=passport_record,
            promotion=promotion_body,
            promotion_record=promotion_record,
            verdicts=tuple(verdicts),
            valid_until=valid_until,
            ledger_head_sequence=head_sequence,
            ledger_head_digest=head_digest,
        )

    def _maximum_fencing_generation(self, venue: Venue) -> int:
        """Return the maximum ever ledgered fence, including expired leases."""

        maximum = 0
        for record in self.ledger.iter_events(schema=CapitalEnvelopeV1.SCHEMA):
            capital = CapitalEnvelopeV1.from_dict(record.envelope.body)
            if capital.venue is venue:
                maximum = max(maximum, capital.fencing_generation)
        return maximum

    def _require_operational_evidence(
        self,
        state: RuntimeState,
        mandate: OperatingMandateV1,
        request: CapitalRequest,
        observed_at: datetime,
    ) -> None:
        now = observed_at.astimezone(timezone.utc)
        for venue in Venue:
            receipt = state.reconciliation_receipts.get(venue)
            if receipt is None:
                raise EvidenceResolutionError(f"missing signed reconciliation receipt: {venue.value}")
            self._require_cas_artifacts(
                (receipt.broker_snapshot_digest,),
                f"reconciliation receipt {venue.value}",
            )
            if receipt.observed_at > now + MAX_FUTURE_SKEW:
                raise EvidenceResolutionError(
                    f"signed reconciliation receipt is from the future: {venue.value}"
                )
            if now - receipt.observed_at > timedelta(seconds=60):
                raise EvidenceResolutionError(f"signed reconciliation receipt is stale: {venue.value}")
            if receipt.status.value != "RECONCILED" or receipt.unknown_outcomes:
                raise EvidenceResolutionError(f"signed reconciliation is unresolved: {venue.value}")
            if mandate.account_hashes.get(venue.value) != receipt.account_hash:
                raise EvidenceResolutionError(f"signed reconciliation account mismatch: {venue.value}")
            risk = state.venue_risk_snapshots.get(venue)
            if risk is None:
                raise EvidenceResolutionError(f"missing signed venue-risk snapshot: {venue.value}")
            if risk.observed_at > now + MAX_FUTURE_SKEW:
                raise EvidenceResolutionError(f"signed venue-risk snapshot is from the future: {venue.value}")
            if now - risk.observed_at > timedelta(seconds=60):
                raise EvidenceResolutionError(f"signed venue-risk snapshot is stale: {venue.value}")
            if risk.reconciliation_receipt_digest != receipt.digest():
                raise EvidenceResolutionError(f"venue-risk snapshot is not bound to latest reconciliation: {venue.value}")
            if risk.broker_snapshot_digest != receipt.broker_snapshot_digest:
                raise EvidenceResolutionError(f"venue-risk broker digest mismatch: {venue.value}")
            if risk.open_orders != receipt.open_orders or risk.open_positions != receipt.open_positions:
                raise EvidenceResolutionError(f"venue-risk counts mismatch reconciliation: {venue.value}")
            if risk.account_hash != receipt.account_hash:
                raise EvidenceResolutionError(f"venue-risk account mismatch: {venue.value}")
        deployment = state.deployment_receipts.get(request.venue)
        if deployment is None:
            raise EvidenceResolutionError(f"missing signed deployment receipt: {request.venue.value}")
        self._require_cas_artifacts(
            self._deployment_artifact_digests(deployment),
            f"deployment receipt {request.venue.value}",
        )
        initial = self._find_body_digest(
            deployment.reconciliation_receipt_digest,
            InitialReconciliationReceiptV1,
        )
        if not isinstance(initial, InitialReconciliationReceiptV1) or initial.venue is not request.venue:
            raise EvidenceResolutionError("deployment receipt does not resolve to the requested venue")

    @staticmethod
    def _aggregate_portfolio_snapshot(
        state: RuntimeState,
    ) -> PortfolioSnapshot:
        risks = state.venue_risk_snapshots
        if set(risks) != set(Venue):
            raise EvidenceResolutionError("signed venue-risk snapshots are incomplete")
        correlated: dict[str, int] = {}
        for risk in risks.values():
            for cluster, amount in risk.correlated_open_risk_cents.items():
                correlated[cluster] = correlated.get(cluster, 0) + amount
        return PortfolioSnapshot(
            nav_cents=sum(risk.nav_cents for risk in risks.values()),
            combined_open_risk_cents=sum(risk.open_risk_cents for risk in risks.values()),
            venue_open_risk_cents={
                venue.value: risks[venue].open_risk_cents for venue in Venue
            },
            correlated_open_risk_cents=correlated,
            combined_daily_loss_cents=sum(risk.daily_loss_cents for risk in risks.values()),
            venue_daily_loss_cents={
                venue.value: risks[venue].daily_loss_cents for venue in Venue
            },
            high_water_drawdown_cents=sum(
                risk.high_water_drawdown_cents for risk in risks.values()
            ),
            venue_open_orders={
                venue.value: risks[venue].open_orders for venue in Venue
            },
            venue_open_positions={
                venue.value: risks[venue].open_positions for venue in Venue
            },
            reconciled_venues=tuple(sorted(venue.value for venue in Venue)),
            observed_at=min(risk.observed_at for risk in risks.values()),
        )

    def issue_capital_envelope(
        self,
        *,
        mandate_id: str,
        request: CapitalRequest,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> SignedEnvelopeV1:
        """Issue and append one 60-second, monotonically fenced capital lease."""

        with self._issue_lock:
            now = self.clock().astimezone(timezone.utc)
            if causation_id is not None:
                require_digest(causation_id, "causation_id")
                for record in self.ledger.iter_events(schema=CapitalEnvelopeV1.SCHEMA):
                    if record.envelope.causation_id == causation_id:
                        return record.envelope
            state = self.replay_state(now)
            mandate = state.mandate
            if mandate is None or mandate.mandate_id != mandate_id:
                raise AuthorityError(
                    "capital request must reference the one current, non-superseded mandate"
                )
            self._require_operational_evidence(state, mandate, request, now)
            snapshot = self._aggregate_portfolio_snapshot(state)
            desired = state.desired_modes.get(request.venue)
            desired_mode = DesiredMode.PAUSED if desired is None else desired.mode
            decision = self.policy_engine.evaluate(
                mandate,
                request,
                snapshot,
                now,
                kill_active=state.kill_active,
                desired_mode=desired_mode,
            )
            decision.require_allowed()
            self._resolve_passports(request, now)
            fencing_generation = self._maximum_fencing_generation(request.venue) + 1
            expires_at = now + timedelta(seconds=self.policy_engine.policy.capital_envelope_ttl_seconds)
            envelope_id = canonical_sha256(
                {
                    "mandate_id": mandate_id,
                    "venue": request.venue.value,
                    "account_hash": request.account_hash,
                    "strategy_hashes": list(request.strategy_hashes),
                    "passport_hashes": list(request.passport_hashes),
                    "promotion_hashes": list(request.promotion_hashes),
                    "authorized_instruments": list(request.authorized_instruments),
                    "fencing_generation": fencing_generation,
                    "not_before": format_utc(now),
                    "expires_at": format_utc(expires_at),
                    "policy_digest": self.policy_engine.policy.digest,
                }
            )
            capital = CapitalEnvelopeV1(
                envelope_id=envelope_id,
                mandate_id=mandate_id,
                venue=request.venue,
                account_hash=request.account_hash,
                strategy_hashes=request.strategy_hashes,
                passport_hashes=request.passport_hashes,
                promotion_hashes=request.promotion_hashes,
                authorized_instruments=request.authorized_instruments,
                authorized_mode=DesiredMode.LIVE,
                max_order_risk_cents=request.max_order_risk_cents,
                max_open_risk_cents=request.max_open_risk_cents,
                max_correlated_risk_cents=request.max_correlated_risk_cents,
                max_daily_loss_cents=request.max_daily_loss_cents,
                max_open_orders=request.max_open_orders,
                fencing_generation=fencing_generation,
                policy_epoch=self.policy_engine.policy.policy_epoch,
                not_before=now,
                expires_at=expires_at,
            )
            source_sequence = self.ledger.last_source_sequence(self.CORE_SOURCE_ID) + 1
            nonce = canonical_sha256(["capital-envelope", envelope_id, source_sequence])[:64]
            signed = SignedEnvelopeV1.issue(
                capital,
                source_id=self.CORE_SOURCE_ID,
                source_sequence=source_sequence,
                correlation_id=correlation_id,
                causation_id=causation_id,
                nonce=nonce,
                not_before=now,
                expires_at=expires_at,
                signer=self.signer,
            )
            self.ledger.append(signed)
            return signed

    def process_pending_capital_requests(
        self,
        *,
        limit: int = 100,
    ) -> tuple[Mapping[str, object], ...]:
        """Idempotently turn current allocator-signed requests into leases.

        The processor has no broker access. It derives risk exclusively from
        current venue-signed snapshots and links the emitted envelope to the
        request body digest through ``causation_id``.
        """

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("capital request processing limit must be between 1 and 1000")
        outcomes: list[Mapping[str, object]] = []
        with self._issue_lock:
            processed = 0
            issued_request_digests = {
                record.envelope.causation_id
                for record in self.ledger.iter_events(
                    schema=CapitalEnvelopeV1.SCHEMA
                )
                if record.envelope.causation_id is not None
            }
            for record in self.ledger.iter_events(schema=CapitalRequestV1.SCHEMA):
                request_digest = record.payload_digest
                if request_digest in issued_request_digests:
                    continue
                request_contract = CapitalRequestV1.from_dict(record.envelope.body)
                now = self.clock().astimezone(timezone.utc)
                if (
                    not record.envelope.is_valid_at(now)
                    or not request_contract.not_before <= now < request_contract.expires_at
                ):
                    continue
                processed += 1
                try:
                    signed = self.issue_capital_envelope(
                        mandate_id=request_contract.mandate_id,
                        request=self._policy_request(request_contract),
                        correlation_id=request_contract.request_id,
                        causation_id=request_digest,
                    )
                except (AuthorityError, EvidenceResolutionError, PolicyViolation) as exc:
                    outcomes.append(
                        {
                            "request_digest": request_digest,
                            "status": "DENIED",
                            "reason_type": type(exc).__name__,
                        }
                    )
                else:
                    issued_request_digests.add(request_digest)
                    outcomes.append(
                        {
                            "request_digest": request_digest,
                            "status": "ISSUED",
                            "capital_event_id": signed.event_id,
                            "capital_body_digest": signed.body_digest,
                        }
                    )
                if processed >= limit:
                    break
        return tuple(outcomes)

    def _openrouter_status(self, observed_at: datetime) -> dict[str, object]:
        """Return a fixed, redacted model-budget projection from one provider."""

        budget = self.policy_engine.policy.openrouter_daily_budget_cents
        unavailable: dict[str, object] = {
            "schema": "dumbmoney.openrouter-budget-status.v1",
            "provider": "OpenRouter",
            "utc_day": observed_at.date().isoformat(),
            "daily_budget_cents": budget,
            "spent_cents": None,
            "remaining_cents": None,
            "resets_at": None,
            "research_paused": True,
            "pause_reason_code": "STATUS_UNAVAILABLE",
            "unresolved_count": None,
        }
        if self.model_status_provider is None:
            return unavailable
        try:
            raw = self.model_status_provider(observed_at)
            if not isinstance(raw, Mapping):
                raise TypeError("model status provider must return a mapping")
            if raw.get("schema") != "dumbmoney.openrouter-budget-status.v1":
                raise ValueError("model status provider schema mismatch")
            if raw.get("provider") != "OpenRouter":
                raise ValueError("model status provider identity mismatch")
            if raw.get("utc_day") != observed_at.date().isoformat():
                raise ValueError("model status provider UTC day is stale")

            def nonnegative_int(name: str) -> int:
                value = raw.get(name)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f"model status {name} must be a non-negative integer")
                return value

            observed_budget = nonnegative_int("daily_budget_cents")
            if observed_budget != budget:
                raise ValueError("model status daily budget differs from signed policy")
            spent = nonnegative_int("spent_cents")
            remaining = nonnegative_int("remaining_cents")
            paused = raw.get("research_paused")
            if not isinstance(paused, bool):
                raise ValueError("model status research_paused must be a boolean")
            unresolved = nonnegative_int("unresolved_count")
            reset = raw.get("resets_at")
            if not isinstance(reset, str) or not reset.endswith("Z"):
                raise ValueError("model status resets_at must be a canonical UTC timestamp")
            reason = raw.get("pause_reason_code")
            if reason is not None and (
                not isinstance(reason, str)
                or not reason
                or len(reason) > 80
                or not all(character.isupper() or character.isdigit() or character == "_" for character in reason)
            ):
                raise ValueError("model status pause reason is not a redacted reason code")
            return {
                "schema": raw["schema"],
                "provider": raw["provider"],
                "utc_day": raw["utc_day"],
                "daily_budget_cents": observed_budget,
                "spent_cents": spent,
                "remaining_cents": remaining,
                "resets_at": reset,
                "research_paused": paused,
                "pause_reason_code": reason,
                "unresolved_count": unresolved,
            }
        except Exception:
            # Provider failures never leak transport/key details into control
            # telemetry and never manufacture a numeric spend value.
            return unavailable

    def control_snapshot(
        self,
        *,
        observed_at: datetime | None = None,
    ) -> dict[str, object]:
        """Return an UNKNOWN-safe, non-authoritative cockpit projection."""

        now = (observed_at or self.clock()).astimezone(timezone.utc)
        # Capture replay and its reported head at one linearization point.
        # HTTP append handlers run on separate threads, so reading these
        # independently could otherwise sign READY against a different head.
        with self._issue_lock:
            state = self.replay_state(now)
            sequence, head = self.ledger.head()
        kill = state.kill_state
        mandate = state.mandate
        venue_values: dict[str, object] = {}
        for venue in Venue:
            desired = state.desired_modes.get(venue)
            envelope = state.capital_envelopes.get(venue)
            reconciliation = state.reconciliation_receipts.get(venue)
            risk = state.venue_risk_snapshots.get(venue)
            reconciliation_age = (
                None
                if reconciliation is None
                else max(0, int((now - reconciliation.observed_at).total_seconds()))
            )
            account_pinned = (
                None
                if reconciliation is None or mandate is None
                else mandate.account_hashes.get(venue.value) == reconciliation.account_hash
            )
            venue_values[venue.value] = {
                "mode": (desired.mode.value if desired is not None else DesiredMode.PAUSED.value),
                "envelope_expires_at": (format_utc(envelope.expires_at) if envelope is not None else None),
                "fencing_generation": (envelope.fencing_generation if envelope is not None else None),
                "open_risk_cents": None if risk is None else risk.open_risk_cents,
                "open_orders": None if risk is None else risk.open_orders,
                "open_positions": None if risk is None else risk.open_positions,
                "unknown_outcomes": None if reconciliation is None else reconciliation.unknown_outcomes,
                "outbox_depth": None,
                "account_pinned": account_pinned,
                "reconciliation_status": (
                    "UNKNOWN" if reconciliation is None else reconciliation.status.value
                ),
                "reconciliation_age_seconds": reconciliation_age,
                "risk_snapshot_age_seconds": (
                    None
                    if risk is None
                    else max(0, int((now - risk.observed_at).total_seconds()))
                ),
            }
        openrouter_status = self._openrouter_status(now)
        kill_active = state.kill_active
        reasons: list[str] = []
        if kill is None:
            reasons.append("KILL_STATE_UNINITIALIZED")
        elif kill.active:
            reasons.append("GLOBAL_KILL_ACTIVE")
        if mandate is None:
            reasons.append("NO_CURRENT_MANDATE")
        for venue in Venue:
            if venue not in state.desired_modes:
                reasons.append(f"MODE_UNINITIALIZED:{venue.value}")
            reconciliation = state.reconciliation_receipts.get(venue)
            if reconciliation is None:
                reasons.append(f"RECONCILIATION_RECEIPT_MISSING:{venue.value}")
            else:
                age = (now - reconciliation.observed_at).total_seconds()
                if reconciliation.observed_at > now + MAX_FUTURE_SKEW:
                    reasons.append(f"RECONCILIATION_FROM_FUTURE:{venue.value}")
                if age > 60:
                    reasons.append(f"RECONCILIATION_STALE:{venue.value}")
                if reconciliation.status.value != "RECONCILED":
                    reasons.append(f"RECONCILIATION_UNRESOLVED:{venue.value}")
                if reconciliation.unknown_outcomes:
                    reasons.append(f"UNKNOWN_OUTCOMES:{venue.value}")
                if mandate is None or mandate.account_hashes.get(venue.value) != reconciliation.account_hash:
                    reasons.append(f"ACCOUNT_NOT_PINNED:{venue.value}")
            risk = state.venue_risk_snapshots.get(venue)
            if risk is None:
                reasons.append(f"VENUE_RISK_SNAPSHOT_MISSING:{venue.value}")
            else:
                if risk.observed_at > now + MAX_FUTURE_SKEW:
                    reasons.append(f"VENUE_RISK_FROM_FUTURE:{venue.value}")
                if (now - risk.observed_at).total_seconds() > 60:
                    reasons.append(f"VENUE_RISK_STALE:{venue.value}")
                if reconciliation is None or risk.reconciliation_receipt_digest != reconciliation.digest():
                    reasons.append(f"VENUE_RISK_RECONCILIATION_MISMATCH:{venue.value}")
            if venue not in state.deployment_receipts:
                reasons.append(f"DEPLOYMENT_RECEIPT_MISSING:{venue.value}")
        status = "LIVE_READY" if not reasons else ("PAUSED" if kill_active else "DEGRADED")
        return {
            "schema": "dumbmoney.control-snapshot.v1",
            "observed_at": format_utc(now),
            "status": status,
            "reason_codes": sorted(reasons) if reasons else ["CONTROL_PLANE_READY"],
            "policy_epoch": self.policy_engine.policy.policy_epoch,
            "policy_digest": self.policy_engine.policy.digest,
            "desired_modes": {
                venue.value: (
                    state.desired_modes[venue].mode.value if venue in state.desired_modes else DesiredMode.PAUSED.value
                )
                for venue in Venue
            },
            "kill_state": {
                "active": kill_active,
                "generation": None if kill is None else kill.generation,
                "reason": "UNINITIALIZED_FAIL_CLOSED" if kill is None else kill.reason,
                "changed_at": None if kill is None else format_utc(kill.changed_at),
            },
            "ledger": {
                "last_global_sequence": sequence,
                "chain_head": head,
            },
            "venues": venue_values,
            "portfolio": {
                "combined_envelope_limit_pct": self.policy_engine.policy.combined_capital_bps / 100,
                "utilized_pct_of_nav": None,
                "utilization_pct_of_envelope": None,
                "remaining_pct_of_nav": None,
                "dummy_allocated_pct_of_nav": None,
                "dopey_allocated_pct_of_nav": None,
            },
            "loss": {
                "daily_loss_limit_pct": self.policy_engine.policy.combined_daily_loss_bps / 100,
                "per_venue_daily_loss_limit_pct": {
                    venue: basis_points / 100
                    for venue, basis_points in self.policy_engine.policy.per_venue_daily_loss_bps.items()
                },
                "daily_loss_used_pct": None,
                "high_water_drawdown_kill_pct": self.policy_engine.policy.high_water_drawdown_bps / 100,
                "current_drawdown_pct": None,
                "stopped": kill_active,
            },
            "openrouter": openrouter_status,
            "services": {
                "DumbMoneyCore": {
                    "status": "READY",
                    "observed_at": format_utc(now),
                },
                **{
                    service: {"status": "UNKNOWN", "observed_at": None}
                    for service in (
                        "DumbMoneyResearchMesh",
                        "DumbMoneyModelGateway",
                        "DumbMoneyDummyKalshi",
                        "DumbMoneyDopeyRobinhood",
                    )
                },
            },
            "evidence": {
                "stage_counts": dict(state.evidence_counts),
            },
            "backup": {
                "last_backup_at": None,
                "last_restore_at": None,
                "restore_status": "UNKNOWN",
            },
        }

    @staticmethod
    def _ledger_event_proof(record: EventRecord) -> dict[str, object]:
        return {
            "schema": "dumbmoney.ledger-event-proof.v1",
            "global_sequence": record.global_sequence,
            "event_id": record.event_id,
            "source_id": record.source_id,
            "source_sequence": record.source_sequence,
            "signer_key_id": record.signer_key_id,
            "nonce": record.nonce,
            "event_schema": record.event_schema,
            "observed_at": format_utc(record.observed_at),
            "received_at": format_utc(record.received_at),
            "correlation_id": record.correlation_id,
            "causation_id": record.causation_id,
            "payload_digest": record.payload_digest,
            "previous_source_digest": record.previous_source_digest,
            "previous_global_digest": record.previous_global_digest,
            "event_digest": record.event_digest,
        }

    def anchor_cell_journal_head(
        self,
        *,
        cell_id: str,
        account_hash: str,
        journal_name: str,
        journal_schema: str,
        journal_stream_id: str,
        journal_sequence: int,
        journal_head_sha256: str,
        request_nonce: str,
        observed_at: datetime | None = None,
    ) -> dict[str, object]:
        """Persist and acknowledge a monotonic venue-local state-store head.

        The checkpoint lives in Core's independently persisted ledger. A
        byte-for-byte rollback of the venue database therefore presents a
        lower sequence, or a conflicting head at the same sequence, and is
        rejected before the venue can regain execution readiness.
        """

        with self._issue_lock:
            try:
                venue = Venue(cell_id)
            except ValueError as exc:
                raise ValueError(
                    f"unsupported DumbMoney cell ID: {cell_id}"
                ) from exc
            require_digest(account_hash, "account_hash")
            require_identifier(journal_name, "journal_name")
            require_identifier(journal_schema, "journal_schema")
            require_digest(journal_stream_id, "journal_stream_id")
            if (
                isinstance(journal_sequence, bool)
                or not isinstance(journal_sequence, int)
                or journal_sequence < 0
            ):
                raise ValueError(
                    "journal_sequence must be a non-negative integer"
                )
            require_digest(journal_head_sha256, "journal_head_sha256")
            require_digest(request_nonce, "request_nonce")
            now = (observed_at or self.clock()).astimezone(timezone.utc)

            state = self.replay_state(now)
            if (
                state.mandate is not None
                and state.mandate.account_hashes.get(venue.value)
                != account_hash
            ):
                raise AuthorityError(
                    "journal anchor account differs from the operating mandate"
                )

            prior_record: EventRecord | None = None
            prior_anchor: CellJournalHeadAnchorV1 | None = None
            for record in self.ledger.iter_events(
                schema=CellJournalHeadAnchorV1.SCHEMA
            ):
                candidate = parse_contract(record.envelope.body)
                if (
                    isinstance(candidate, CellJournalHeadAnchorV1)
                    and candidate.venue is venue
                    and candidate.journal_name == journal_name
                ):
                    prior_record = record
                    prior_anchor = candidate

            reused = False
            if prior_anchor is not None:
                if prior_anchor.account_hash != account_hash:
                    raise LedgerConflictError(
                        "journal anchor account identity changed"
                    )
                if prior_anchor.journal_schema != journal_schema:
                    raise LedgerConflictError(
                        "journal anchor schema identity changed"
                    )
                if prior_anchor.journal_stream_id != journal_stream_id:
                    raise LedgerConflictError(
                        "journal anchor stream identity changed"
                    )
                if journal_sequence < prior_anchor.journal_sequence:
                    raise LedgerConflictError(
                        "journal head sequence regressed"
                    )
                if journal_sequence == prior_anchor.journal_sequence:
                    if (
                        journal_head_sha256
                        != prior_anchor.journal_head_sha256
                    ):
                        raise LedgerConflictError(
                            "journal head conflicts at the anchored sequence"
                        )
                    assert prior_record is not None
                    record = prior_record
                    reused = True
            if not reused:
                previous_anchor_body_digest = (
                    "0" * 64
                    if prior_anchor is None
                    else prior_anchor.digest()
                )
                anchor_id = canonical_sha256(
                    {
                        "schema": CellJournalHeadAnchorV1.SCHEMA,
                        "venue": venue.value,
                        "account_hash": account_hash,
                        "journal_name": journal_name,
                        "journal_schema": journal_schema,
                        "journal_stream_id": journal_stream_id,
                        "journal_sequence": journal_sequence,
                        "journal_head_sha256": journal_head_sha256,
                        "previous_anchor_body_digest": (
                            previous_anchor_body_digest
                        ),
                    }
                )
                anchor = CellJournalHeadAnchorV1(
                    anchor_id=anchor_id,
                    venue=venue,
                    account_hash=account_hash,
                    journal_name=journal_name,
                    journal_schema=journal_schema,
                    journal_stream_id=journal_stream_id,
                    journal_sequence=journal_sequence,
                    journal_head_sha256=journal_head_sha256,
                    previous_anchor_body_digest=(
                        previous_anchor_body_digest
                    ),
                    anchored_at=now,
                )
                source_sequence = (
                    self.ledger.last_source_sequence(self.CORE_SOURCE_ID)
                    + 1
                )
                envelope = SignedEnvelopeV1.issue(
                    anchor,
                    source_id=self.CORE_SOURCE_ID,
                    source_sequence=source_sequence,
                    correlation_id=(
                        f"journal-anchor:{venue.value}:{journal_name}"
                    ),
                    causation_id=None,
                    nonce=canonical_sha256(
                        [
                            "cell-journal-anchor",
                            anchor_id,
                            source_sequence,
                        ]
                    ),
                    not_before=now,
                    expires_at=now + timedelta(seconds=120),
                    signer=self.signer,
                )
                record = self.ledger.append(envelope)

            proof = self._ledger_event_proof(record)
            checkpoint: dict[str, object] = {
                "schema": "dumbmoney.cell-journal-anchor-checkpoint.v1",
                "cell_id": venue.value,
                "request_nonce": request_nonce,
                "account_hash": account_hash,
                "journal_name": journal_name,
                "journal_schema": journal_schema,
                "journal_stream_id": journal_stream_id,
                "journal_sequence": journal_sequence,
                "journal_head_sha256": journal_head_sha256,
                "anchor_body_digest": record.payload_digest,
                "anchor_event_digest": record.event_digest,
                "reused": reused,
                "observed_at": format_utc(now),
            }
            checkpoint_signature = {
                "algorithm": self.signer.algorithm,
                "signer_key_id": self.signer.key_id,
                "signature": self.signer.sign(
                    canonical_json_bytes(checkpoint)
                ),
            }
            return {
                "schema": "dumbmoney.cell-journal-anchor-response.v1",
                "cell_id": venue.value,
                "request_nonce": request_nonce,
                "observed_at": format_utc(now),
                "reused": reused,
                "anchor_envelope": record.envelope.to_dict(),
                "ledger_proof": proof,
                "checkpoint": checkpoint,
                "checkpoint_signature": checkpoint_signature,
            }

    def cell_contract_resolution(
        self,
        *,
        cell_id: str,
        body_digest: str,
        capital_envelope_digest: str,
        request_nonce: str,
        observed_at: datetime | None = None,
    ) -> dict[str, object]:
        """Resolve one venue-scoped authority input with signed freshness."""

        with self._issue_lock:
            return self._cell_contract_resolution_locked(
                cell_id=cell_id,
                body_digest=body_digest,
                capital_envelope_digest=capital_envelope_digest,
                request_nonce=request_nonce,
                observed_at=observed_at,
            )

    def _cell_contract_resolution_locked(
        self,
        *,
        cell_id: str,
        body_digest: str,
        capital_envelope_digest: str,
        request_nonce: str,
        observed_at: datetime | None,
    ) -> dict[str, object]:
        try:
            venue = Venue(cell_id)
        except ValueError as exc:
            raise ValueError(f"unsupported DumbMoney cell ID: {cell_id}") from exc
        require_digest(body_digest, "body_digest")
        require_digest(capital_envelope_digest, "capital_envelope_digest")
        require_digest(request_nonce, "request_nonce")
        now = (observed_at or self.clock()).astimezone(timezone.utc)
        authority = self._resolve_live_authority_tuple(
            venue=venue,
            capital_envelope_digest=capital_envelope_digest,
            observed_at=now,
        )
        current_capital = authority.capital
        if body_digest == current_capital.passport_hashes[0]:
            body: AlphaPassportV1 | PromotionCertificateV1 = authority.passport
            record = authority.passport_record
        elif body_digest == current_capital.promotion_hashes[0]:
            body = authority.promotion
            record = authority.promotion_record
        else:
            raise EvidenceResolutionError(
                "authority contract is outside the current singleton capital tuple"
            )
        transport_window_current = record.envelope.is_valid_at(now)
        if isinstance(body, AlphaPassportV1):
            body_window_current = body.created_at <= now < body.expires_at
            exact_instruments = True
            for index, instrument in enumerate(body.intended_instruments):
                try:
                    validate_authorized_instrument(
                        body.venue,
                        instrument,
                        f"intended_instruments[{index}]",
                    )
                except (TypeError, ValueError):
                    exact_instruments = False
                    break
            eligible_live_input = (
                transport_window_current
                and body_window_current
                and exact_instruments
            )
        else:
            body_window_current = body.not_before <= now < body.expires_at
            eligible_live_input = (
                transport_window_current
                and body_window_current
                and body.stage
                in {
                    PromotionStage.EXPLORATORY_LIVE,
                    PromotionStage.AGGRESSIVE_BOUNDED,
                }
            )
        if not eligible_live_input:
            raise EvidenceResolutionError(
                "requested authority material is not a current live input"
            )
        authority_state: dict[str, object] = {
            "schema": "dumbmoney.cell-authority-state.v1",
            "evaluated_at": format_utc(now),
            "authority_valid_until": format_utc(authority.valid_until),
            "policy_epoch": self.policy_engine.policy.policy_epoch,
            "mandate_id": authority.mandate.mandate_id,
            "mandate_event_digest": authority.mandate_record.event_digest,
            "kill_clear": True,
            "kill_generation": authority.kill_state.generation,
            "kill_event_digest": authority.kill_state_record.event_digest,
            "desired_mode": authority.desired_mode.mode.value,
            "desired_mode_revision": authority.desired_mode.revision,
            "desired_mode_event_digest": authority.desired_mode_record.event_digest,
            "capital_envelope_digest": authority.capital.digest(),
            "capital_event_digest": authority.capital_record.event_digest,
            "fencing_generation": authority.capital.fencing_generation,
            "strategy_hash": authority.capital.strategy_hashes[0],
            "passport_digest": authority.passport.digest(),
            "passport_event_digest": authority.passport_record.event_digest,
            "promotion_digest": authority.promotion.digest(),
            "promotion_event_digest": authority.promotion_record.event_digest,
            "verdicts": [
                {
                    "verdict_digest": verdict_record.payload_digest,
                    "verdict_event_digest": verdict_record.event_digest,
                    "verdict_id": verdict.verdict_id,
                    "court": verdict.court,
                    "decision": verdict.decision.value,
                    "signer_key_id": verdict_record.signer_key_id,
                    "evaluated_at": format_utc(verdict.evaluated_at),
                    "expires_at": format_utc(verdict.expires_at),
                    "transport_expires_at": format_utc(
                        verdict_record.envelope.expires_at
                    ),
                }
                for verdict, verdict_record in authority.verdicts
            ],
            "ledger_head_sequence": authority.ledger_head_sequence,
            "ledger_head_digest": authority.ledger_head_digest,
        }
        ledger_proof = self._ledger_event_proof(record)
        checkpoint: dict[str, object] = {
            "schema": "dumbmoney.cell-contract-resolution-checkpoint.v1",
            "cell_id": venue.value,
            "request_nonce": request_nonce,
            "requested_body_digest": body_digest,
            "capital_envelope_digest": capital_envelope_digest,
            "fencing_generation": current_capital.fencing_generation,
            "observed_at": format_utc(now),
            "contract_schema": body.SCHEMA,
            "transport_window_current": transport_window_current,
            "body_window_current": body_window_current,
            "eligible_live_input": eligible_live_input,
            "authority_state": authority_state,
            "ledger_proof": ledger_proof,
            "envelope": record.envelope.to_dict(),
        }
        checkpoint_signature = {
            "algorithm": self.signer.algorithm,
            "signer_key_id": self.signer.key_id,
            "signature": self.signer.sign(canonical_json_bytes(checkpoint)),
        }
        return {
            "schema": "dumbmoney.cell-contract-resolution.v1",
            "cell_id": venue.value,
            "request_nonce": request_nonce,
            "requested_body_digest": body_digest,
            "capital_envelope_digest": capital_envelope_digest,
            "fencing_generation": current_capital.fencing_generation,
            "observed_at": format_utc(now),
            "contract_schema": body.SCHEMA,
            "transport_window_current": transport_window_current,
            "body_window_current": body_window_current,
            "eligible_live_input": eligible_live_input,
            "authority_state": authority_state,
            "ledger_proof": ledger_proof,
            "envelope": record.envelope.to_dict(),
            "checkpoint": checkpoint,
            "checkpoint_signature": checkpoint_signature,
        }

    def cell_commands(
        self,
        *,
        cell_id: str,
        after_sequence: int,
        after_digest: str,
        request_nonce: str,
        limit: int = 250,
        observed_at: datetime | None = None,
    ) -> dict[str, object]:
        """Project durable signed control events through a hash-bound cursor."""

        with self._issue_lock:
            return self._cell_commands_locked(
                cell_id=cell_id,
                after_sequence=after_sequence,
                after_digest=after_digest,
                request_nonce=request_nonce,
                limit=limit,
                observed_at=observed_at,
            )

    def _cell_commands_locked(
        self,
        *,
        cell_id: str,
        after_sequence: int,
        after_digest: str,
        request_nonce: str,
        limit: int,
        observed_at: datetime | None,
    ) -> dict[str, object]:
        try:
            venue = Venue(cell_id)
        except ValueError as exc:
            raise ValueError(f"unsupported DumbMoney cell ID: {cell_id}") from exc
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        require_digest(after_digest, "after_digest")
        require_digest(request_nonce, "request_nonce")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("command page limit must be between 1 and 1000")
        expected_digest = self.ledger.cursor_digest(after_sequence)
        if expected_digest != after_digest:
            raise LedgerConflictError("cursor digest does not match the requested ledger sequence")
        now = (observed_at or self.clock()).astimezone(timezone.utc)
        head_sequence, head_digest = self.ledger.head()
        scanned = list(self.ledger.iter_events(after_sequence=after_sequence, limit=limit))
        next_sequence = after_sequence if not scanned else scanned[-1].global_sequence
        next_digest = self.ledger.cursor_digest(next_sequence)
        commands: list[dict[str, object]] = []
        command_checkpoints: list[dict[str, object]] = []
        for record in scanned:
            body = parse_contract(record.envelope.body)
            relevant = isinstance(body, KillStateV1)
            relevant = relevant or (
                isinstance(body, DesiredModeV1) and body.venue is venue
            )
            relevant = relevant or (
                isinstance(body, CapitalEnvelopeV1) and body.venue is venue
            )
            if not relevant:
                continue
            transport_window_current = record.envelope.is_valid_at(now)
            body_current = True
            if isinstance(body, (DesiredModeV1, CapitalEnvelopeV1)):
                body_current = body.not_before <= now < body.expires_at
            elif isinstance(body, KillStateV1):
                body_current = body.changed_at <= now + MAX_FUTURE_SKEW
            positive_grant = (
                isinstance(body, CapitalEnvelopeV1)
                or (isinstance(body, DesiredModeV1) and body.mode is DesiredMode.LIVE)
                or (isinstance(body, KillStateV1) and not body.active)
            )
            if not positive_grant:
                authority_effect = "APPLY_FAIL_CLOSED"
            elif transport_window_current and body_current:
                authority_effect = "APPLY_POSITIVE"
            else:
                authority_effect = "HISTORICAL_ONLY"
            valid_now = transport_window_current and body_current
            ledger_proof = self._ledger_event_proof(record)
            commands.append(
                {
                    "global_sequence": record.global_sequence,
                    "event_id": record.event_id,
                    "event_digest": record.event_digest,
                    "body_schema": record.event_schema,
                    "valid_now": valid_now,
                    "transport_window_current": transport_window_current,
                    "authority_effect": authority_effect,
                    "ledger_proof": ledger_proof,
                    "envelope": record.envelope.to_dict(),
                }
            )
            command_checkpoints.append(
                {
                    "global_sequence": record.global_sequence,
                    "event_id": record.event_id,
                    "event_digest": record.event_digest,
                    "body_schema": record.event_schema,
                    "valid_now": valid_now,
                    "transport_window_current": transport_window_current,
                    "authority_effect": authority_effect,
                    "ledger_proof": ledger_proof,
                }
            )
        effective_kill = True
        effective_live = False
        for record in self.ledger.iter_events():
            body = parse_contract(record.envelope.body)
            if isinstance(body, KillStateV1):
                if body.active:
                    effective_kill = True
                else:
                    effective_kill = (
                        record.envelope.is_valid_at(now)
                        and body.changed_at <= now + MAX_FUTURE_SKEW
                    )
                    effective_kill = not effective_kill
            elif isinstance(body, DesiredModeV1) and body.venue is venue:
                if body.mode is not DesiredMode.LIVE:
                    effective_live = False
                else:
                    effective_live = (
                        record.envelope.is_valid_at(now)
                        and body.not_before <= now < body.expires_at
                    )
        if effective_kill:
            required_action = "CANCEL_AND_RECONCILE"
        elif not effective_live:
            required_action = "PAUSE_NEW_RISK"
        else:
            required_action = "APPLY_SIGNED_CONTROLS"
        checkpoint: dict[str, object] = {
            "schema": "dumbmoney.cell-command-checkpoint.v1",
            "cell_id": venue.value,
            "request_nonce": request_nonce,
            "after_sequence": after_sequence,
            "after_digest": after_digest,
            "ordered_commands": command_checkpoints,
            "next_sequence": next_sequence,
            "next_digest": next_digest,
            "ledger_head_sequence": head_sequence,
            "ledger_head_digest": head_digest,
            "observed_at": format_utc(now),
            "required_action": required_action,
        }
        checkpoint_signature = {
            "algorithm": self.signer.algorithm,
            "signer_key_id": self.signer.key_id,
            "signature": self.signer.sign(canonical_json_bytes(checkpoint)),
        }
        return {
            "schema": "dumbmoney.cell-command-page.v1",
            "cell_id": venue.value,
            "request_nonce": request_nonce,
            "observed_at": format_utc(now),
            "after_sequence": after_sequence,
            "after_digest": after_digest,
            "next_sequence": next_sequence,
            "next_digest": next_digest,
            "ledger_head_sequence": head_sequence,
            "ledger_head_digest": head_digest,
            "has_more": next_sequence < head_sequence,
            "required_action": required_action,
            "commands": commands,
            "checkpoint": checkpoint,
            "checkpoint_signature": checkpoint_signature,
        }
