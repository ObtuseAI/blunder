from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from blunder.sovereign_observatory import (
    EVENT_KINDS,
    PROJECTION_KINDS,
    append_event,
    audit_observatory,
    build_event,
    build_projection,
    dunce_consumer_view,
    load_events,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def span_id(index: int) -> str:
    return f"{index + 1:016x}"


def controlled_events(count: int):
    events = []
    started = datetime(2026, 7, 14, tzinfo=timezone.utc)
    for index in range(count):
        previous = events[-1] if events else None
        effectful = index < len(EVENT_KINDS)
        event = build_event(
            sequence=index,
            previous_digest=previous.event_digest if previous is not None else None,
            recorded_at=started + timedelta(microseconds=index),
            monotonic_ns=index,
            trace_id="1" * 32,
            span_id=span_id(index),
            parent_span_id=previous.span_id if previous is not None else None,
            kind=EVENT_KINDS[index % len(EVENT_KINDS)],
            retention_class="audit",
            redaction_class="internal",
            resource={"service.name": "blunder"},
            attributes={"telemetryPresent": True, "value": index},
            evidence_digest=digest(f"evidence-{index}"),
            effectful=effectful,
            policy_event_digest=digest(f"policy-{index}") if effectful else None,
        )
        events.append(event)
    return tuple(events)


class BlunderSovereignObservatoryTests(unittest.TestCase):
    def test_hash_linked_ledger_round_trips_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            events = controlled_events(3)
            for event in events:
                append_event(path, event)
            self.assertEqual(load_events(path), events)
            rows = path.read_text(encoding="utf-8").splitlines()
            tampered = json.loads(rows[1])
            tampered["kind"] = "rollback"
            rows[1] = json.dumps(tampered)
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_events(path)

    def test_effectful_events_require_policy_evidence_and_raw_reasoning_is_rejected(self) -> None:
        arguments = dict(
            sequence=0,
            previous_digest=None,
            recorded_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
            monotonic_ns=0,
            trace_id="2" * 32,
            span_id="3" * 16,
            parent_span_id=None,
            kind="operator-action",
            retention_class="constitutional",
            redaction_class="secret-prohibited",
            resource={"service.name": "blunder"},
            evidence_digest=digest("evidence"),
            effectful=True,
            policy_event_digest=None,
        )
        with self.assertRaises(ValueError):
            build_event(attributes={"telemetryPresent": True}, **arguments)
        arguments["effectful"] = False
        with self.assertRaises(PermissionError):
            build_event(attributes={"telemetryPresent": True, "chainOfThought": "prohibited"}, **arguments)

    def test_sensitive_values_are_redacted(self) -> None:
        event = build_event(
            sequence=0,
            previous_digest=None,
            recorded_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
            monotonic_ns=0,
            trace_id="4" * 32,
            span_id="5" * 16,
            parent_span_id=None,
            kind="federation",
            retention_class="audit",
            redaction_class="sensitive",
            resource={"service.name": "blunder"},
            attributes={"telemetryPresent": True, "credential": "must-not-survive", "value": 1},
            evidence_digest=digest("redaction"),
            effectful=False,
            policy_event_digest=None,
        )
        self.assertEqual(event.attributes["credential"], "[REDACTED]")
        self.assertEqual(event.redaction_count, 1)

    def test_controlled_run_meets_coverage_and_reports_one_explicit_gap(self) -> None:
        events = controlled_events(214)
        non_effect = events[len(EVENT_KINDS) :]
        expected = [event.span_id for event in non_effect] + ["f" * 16]
        audit = audit_observatory(events, expected)
        self.assertEqual(audit["status"], "PASS")
        self.assertEqual(audit["effectPolicyCausalCoverage"], 1.0)
        self.assertGreaterEqual(audit["nonEffectTraceCompleteness"], 0.995)
        self.assertEqual(audit["missingSpanIds"], ["f" * 16])

    def test_every_projection_drills_to_evidence_and_deletion_preserves_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            events = controlled_events(4)
            for event in events:
                append_event(path, event)
            for projection_kind in PROJECTION_KINDS:
                projection = build_projection(events, projection_kind)
                self.assertTrue(projection["allClaimsEvidenceLinked"])
                self.assertFalse(projection["sourceOfTruth"])
                self.assertFalse(projection["deletionAffectsEvidence"])
            del projection
            self.assertEqual(load_events(path), events)

    def test_dunce_is_a_read_only_projection_consumer(self) -> None:
        view = dunce_consumer_view(build_projection(controlled_events(2), "authority"))
        self.assertEqual(view["status"], "READ_ONLY_ANALYSIS_VIEW")
        self.assertFalse(view["policyMutationAuthorized"])
        self.assertFalse(view["truthMutationAuthorized"])
        self.assertFalse(view["fitnessMutationAuthorized"])
        self.assertFalse(view["promotionMutationAuthorized"])


if __name__ == "__main__":
    unittest.main()
