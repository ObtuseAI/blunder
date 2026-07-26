from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from blunder.cognitive_events import (
    ConstitutionalPolicyError,
    build_parity_report,
    load_decision_events,
    record_fail_closed_decision,
    record_shadow_decision,
)
from blunder.constitutional_adapters import record_provider_boundary, requested_hard_denials
from blunder.constitutional_amendments import (
    EffectAmendment,
    amendment_proposal_to_dict,
    build_amendment_proposal,
    parse_amendment_proposal,
    write_amendment_proposal,
)
from blunder.constitutional_kernel import (
    AuthorityEnvelope,
    EffectPolicy,
    build_authority_envelope,
    build_causal_identity,
    evaluate_authority,
    load_runtime_constitution,
    load_strict_json,
    parse_authority_envelope,
)
from blunder.effect_surface_registry import audit_effect_surface_coverage, load_effect_surface_registry
from blunder.native_agent import run
from blunder.shell.server import JobManager
from blunder.validation_broker import brokered_run


class BlunderConstitutionalKernelTests(unittest.TestCase):
    def valid_authority(self, observed_at: datetime) -> AuthorityEnvelope:
        constitution = load_runtime_constitution()
        return build_authority_envelope(
            "test-authority",
            "test-actor",
            "test-subject",
            "self-coding.launch",
            ("bounded_self_coding",),
            ("operator-authorization",),
            constitution.immutable_denials,
            (),
            observed_at + timedelta(minutes=5),
        )

    def test_denial_first_policy_table_preserves_all_immutable_boundaries(self) -> None:
        observed_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
        constitution = load_runtime_constitution()
        self.assertEqual(
            set(constitution.immutable_denials),
            {
                "windows_permission_or_acl_bypass",
                "secret_printing",
                "live_benchmark_launch",
                "destructive_filesystem_cleanup",
                "git_push",
                "safety_boundary_weakening",
                "production_deploy",
                "public_submission",
            },
        )
        valid = self.valid_authority(observed_at)
        self.assertEqual(evaluate_authority(constitution, valid, observed_at).status, "ALLOW")
        cases = (
            (
                replace(valid, requested_capabilities=("git_push",)),
                "REQUESTED_IMMUTABLE_DENIAL",
            ),
            (
                replace(valid, preserved_denials=tuple(item for item in valid.preserved_denials if item != "git_push")),
                "IMMUTABLE_DENIAL_NOT_PRESERVED",
            ),
            (replace(valid, effect="unregistered.effect"), "UNKNOWN_EFFECT"),
            (replace(valid, expires_at=observed_at), "AUTHORITY_EXPIRED"),
            (replace(valid, scopes=()), "MISSING_REQUIRED_SCOPE"),
            (replace(valid, approvals=()), "MISSING_REQUIRED_APPROVAL"),
            (
                replace(
                    valid,
                    effect="git.push",
                    scopes=("git:push",),
                    approvals=("operator-push-approval",),
                ),
                "EFFECT_ALWAYS_DENIED",
            ),
        )
        for envelope, reason in cases:
            with self.subTest(reason=reason):
                decision = evaluate_authority(constitution, envelope, observed_at)
                self.assertEqual(decision.status, "DENY")
                self.assertIn(reason, decision.reason_codes)

        self.assertEqual(
            requested_hard_denials("Weaken Windows permission validation and git push the result"),
            ("git_push", "safety_boundary_weakening", "windows_permission_or_acl_bypass"),
        )
        self.assertEqual(requested_hard_denials("Optimize the provider token budget"), ())

    def test_contract_parsing_rejects_unknown_duplicate_and_naive_values(self) -> None:
        observed_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
        envelope = self.valid_authority(observed_at)
        raw: dict[str, object] = {
            "schema": envelope.schema,
            "authorityId": envelope.authority_id,
            "actorId": envelope.actor_id,
            "subjectId": envelope.subject_id,
            "effect": envelope.effect,
            "scopes": list(envelope.scopes),
            "approvals": list(envelope.approvals),
            "preservedDenials": list(envelope.preserved_denials),
            "requestedCapabilities": list(envelope.requested_capabilities),
            "expiresAt": "2026-07-14T12:05:00Z",
        }
        with self.assertRaisesRegex(ValueError, "unknown"):
            parse_authority_envelope({**raw, "unexpected": True})
        with self.assertRaisesRegex(ValueError, "explicit timezone"):
            parse_authority_envelope({**raw, "expiresAt": "2026-07-14T12:05:00"})
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "duplicate.json"
            duplicate.write_text('{"schema":"one","schema":"two"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_strict_json(duplicate)

    def test_causal_identity_is_deterministic_for_explicit_inputs(self) -> None:
        first = build_causal_identity("actor", "epoch", "revision", "mission", None, "nonce")
        second = build_causal_identity("actor", "epoch", "revision", "mission", None, "nonce")
        changed = build_causal_identity("actor", "epoch", "revision", "mission", None, "other-nonce")
        self.assertEqual(first, second)
        self.assertNotEqual(first.trace_id, changed.trace_id)
        self.assertEqual(len(first.trace_id), 32)
        self.assertEqual(len(first.span_id), 16)

    def test_append_only_event_is_redacted_integrity_checked_and_reports_disagreement(self) -> None:
        observed_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
        constitution = load_runtime_constitution()
        envelope = build_authority_envelope(
            "sensitive-authority",
            "sensitive-actor",
            "sensitive-subject",
            "provider.call",
            ("provider:model-use",),
            ("operator-provider-approval",),
            constitution.immutable_denials,
            (),
            observed_at + timedelta(minutes=5),
        )
        identity = build_causal_identity(
            "sensitive-actor",
            constitution.policy_epoch,
            constitution.source_revision,
            "sensitive-mission",
            None,
            "test-nonce",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record_shadow_decision(root, envelope, identity, False, ("raw current-behavior detail",), observed_at)
            events = load_decision_events(root)
            report = build_parity_report(events)
            ledger_text = (
                root / "artifacts" / "obtuse" / "blunder-constitutional-kernel-v1" / "decisions.jsonl"
            ).read_text(encoding="utf-8")
            for sensitive in (
                "sensitive-authority",
                "sensitive-actor",
                "sensitive-subject",
                "sensitive-mission",
                "raw current-behavior detail",
            ):
                self.assertNotIn(sensitive, ledger_text)
            self.assertEqual(report["status"], "SHADOW_DISAGREEMENT")
            self.assertEqual(report["disagreementCount"], 1)
            self.assertEqual(report["disagreements"][0]["eventId"], events[0]["eventId"])

    def test_provider_denial_records_matching_shadow_event_before_current_behavior_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "explicit provider approval"):
                run(root, "Analyze without a provider call", False)
            events = load_decision_events(root)
            self.assertEqual(events[-1]["effect"], "provider.call")
            self.assertEqual(events[-1]["parity"], "MATCH_DENY")
            self.assertEqual(events[-1]["decision"]["status"], "DENY")

    def test_provider_allow_adapter_records_matching_shadow_without_calling_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = record_provider_boundary(Path(directory), True, datetime.now(timezone.utc))
            self.assertEqual(summary["status"], "ALLOW")
            self.assertEqual(summary["parity"], "MATCH_ALLOW")
            self.assertEqual(summary["mode"], "fail_closed")

    def test_fail_closed_disagreement_is_recorded_before_effect_is_blocked(self) -> None:
        observed_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
        constitution = load_runtime_constitution()
        envelope = replace(
            self.valid_authority(observed_at),
            preserved_denials=tuple(item for item in constitution.immutable_denials if item != "git_push"),
        )
        identity = build_causal_identity(
            "test-actor",
            constitution.policy_epoch,
            constitution.source_revision,
            None,
            None,
            "fail-closed-test",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ConstitutionalPolicyError, "IMMUTABLE_DENIAL_NOT_PRESERVED"):
                record_fail_closed_decision(root, envelope, identity, True, (), observed_at)
            events = load_decision_events(root)
            self.assertEqual(events[-1]["parity"], "CURRENT_ALLOW_CONSTITUTION_DENY")
            self.assertEqual(events[-1]["decision"]["status"], "DENY")

    def test_amendment_proposal_is_content_addressed_preserves_root_and_cannot_activate(self) -> None:
        constitution = load_runtime_constitution()
        observed_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
        proposal = build_amendment_proposal(
            constitution,
            "constitutional-researcher",
            "ccos-epoch-1-proposed-2",
            "Require a second approval for bounded provider calls.",
            ("a" * 64,),
            (
                EffectAmendment(
                    "REPLACE",
                    "provider.call",
                    EffectPolicy(
                        ("provider:model-use",),
                        ("operator-provider-approval", "independent-provider-review"),
                        False,
                    ),
                ),
            ),
            observed_at,
            "proposal-test",
        )
        raw = amendment_proposal_to_dict(proposal)
        self.assertEqual(parse_amendment_proposal(raw, constitution), proposal)
        with self.assertRaisesRegex(ValueError, "cannot carry activation authority"):
            parse_amendment_proposal({**raw, "activationAuthorized": True}, constitution)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = write_amendment_proposal(root, proposal, observed_at)
            self.assertTrue(path.is_file())
            event = load_decision_events(root)[-1]
            self.assertEqual(event["effect"], "policy.root-mutate")
            self.assertEqual(event["parity"], "MATCH_DENY")

    def test_effect_surface_registry_matches_monitored_source_sinks(self) -> None:
        registry = load_effect_surface_registry()
        report = audit_effect_surface_coverage(Path(__file__).resolve().parents[1])
        self.assertEqual(report["status"], "PASS", report["errors"])
        self.assertEqual(report["surfaceCount"], len(registry.surfaces))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for module in registry.covered_modules:
                source = Path(__file__).resolve().parents[1] / Path(*module.split(".")).with_suffix(".py")
                destination = root / Path(*module.split(".")).with_suffix(".py")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            drifted = root / "blunder" / "validation_broker.py"
            drifted.write_text(
                drifted.read_text(encoding="utf-8") + "\nsubprocess.run(['unexpected-effect'])\n",
                encoding="utf-8",
            )
            drift_report = audit_effect_surface_coverage(root)
            self.assertEqual(drift_report["status"], "FAIL")
            self.assertTrue(any("inventory drifted" in str(item) for item in drift_report["errors"]))

    def test_validation_broker_emits_process_allow_and_external_network_denial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = brokered_run(
                (sys.executable, "-c", "print('bounded')"),
                root,
                dict(os.environ),
                30,
                128 * 1024 * 1024,
            )
            self.assertEqual(result["returncode"], 0)
            self.assertEqual(result["telemetry"]["processDurationNanoseconds"]["state"], "MEASURED")
            decisions = result["isolation"]["constitutionalDecision"]
            self.assertEqual(decisions["process"]["status"], "ALLOW")
            self.assertEqual(decisions["externalNetwork"]["status"], "DENY")

    def test_authenticated_shell_records_immutable_denial_before_dispatch_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = JobManager(root, lambda value: None)
            with self.assertRaisesRegex(ValueError, "live benchmark control"):
                manager.start(
                    "run_task",
                    {"task": "Launch the benchmark now", "providerApproved": True},
                )
            events = load_decision_events(root)
            self.assertEqual(events[-1]["effect"], "benchmark.live-launch")
            self.assertEqual(events[-1]["parity"], "MATCH_DENY")


if __name__ == "__main__":
    unittest.main()
