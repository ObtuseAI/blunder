# Blunder CCOS operational runtime

## Current posture

Blunder operates as a private `CCOS_RESEARCH_PROTOTYPE`. The seven constitutional, physiological, organism, evolution, ecology, observatory, and self-expansion epochs are implemented, and their evidence types now have one durable local runtime. The runtime is deliberately observation-only: persistence, fitness, consensus, reputation, a canary action, or a recovery record cannot activate a candidate or grant authority.

The tracked configuration is `configs/obtuse/blunder-ccos-runtime.json`. It requires all effect authorities to remain false, confines runtime data beneath `artifacts/`, and sets explicit per-record and ledger-count ceilings. Missing configuration, unknown fields, duplicate keys, path escape, non-finite numbers, oversized or structurally excessive payloads, exhausted capacity, and authority-bearing payload fields fail closed.

## Durable evidence plane

`blunder/ccos_runtime.py` stores eight record kinds:

| Kind | Durable subject |
| --- | --- |
| `genome` | Content-addressed cognitive genome identity and policy |
| `evaluation` | Held-out, adversarial, replay, fitness, and no-champion evidence |
| `memory` | Memory identity, tier, evidence, and reversible transition |
| `lineage` | Parent, mutation, compatibility, and rollback identities |
| `observatory` | Redacted causal telemetry and evidence links |
| `quarantine` | Capsule or candidate admission result and rejection reasons |
| `recovery` | Last-known-good and rollback evidence |
| `canary` | Provider-free operational proof result |

Every record contains a canonical payload digest, sequence, previous-record digest, UTC timestamp, and record digest. Immutable record files are written and flushed before an atomically replaced index can reference them. The index has its own canonical digest and is accepted only after the complete referenced chain is reloaded and verified.

A process-local lock prevents Windows thread races while exclusive-create writer and recovery lock files coordinate independent processes. A crashed writer cannot silently advance the index. Recovery blocks new writers, verifies the recorded owner PID is no longer active, and quarantines only a stale dead-writer lock. A long-running live writer is never stolen; normal writes and recovery time out explicitly.

## Recovery and retention

Before replacing an existing index, the runtime preserves its fully verified form as the last-known-good index. Recovery requires the literal confirmation `RECOVER_CCOS_LAST_KNOWN_GOOD`. The current index is preserved as recovery evidence, the prior index is restored atomically, and later immutable records remain on disk as orphan evidence for inspection.

Recovery inspection is read-only. It reports index validity, valid orphan identities, invalid records, interrupted temporary files, writer-lock age, and whether review is required. It never accepts an orphan automatically.

Retention is also review-only. Records older than the configured class policy are reported as `TOMBSTONE_PROJECTION_ONLY` candidates. Canonical evidence deletion remains zero; deleting a projection cannot rewrite the source ledger.

## Operator surfaces

The localhost Shell authenticates every CCOS route with the per-process session token:

| Route | Contract |
| --- | --- |
| `GET /api/ccos` | Verify runtime state, identities, counts, retention, and closed authorities |
| `GET /api/ccos/records?kind=&offset=&limit=` | Read a bounded page of immutable verified records; limit is capped at 200 |
| `POST /api/ccos/records` | Append one bounded evidence object |
| `GET /api/ccos/retention` | Read non-destructive retention recommendations |
| `GET /api/ccos/recovery` | Inspect interrupted-write evidence |
| `POST /api/ccos/recover` | Restore the last-known-good index after exact confirmation |
| `POST /api/ccos/canary` | Run the explicitly confirmed provider-free operational canary |

The Evolve view shows the verified generation, shortened head identity, provider-call count, activation posture, retention and recovery state, evidence append form, canary action, and exact recovery control. `scripts/obtuse/blundercode.ps1 ccos ...` provides the same operator workflow without opening the browser.

## Operational canary

`blunder/ccos_canary.py` compiles one deterministic genome and one sealed mutation lineage, evaluates all seven realities, deliberately contaminates and weakens the fresh holdout, and requires the result to remain `NO_CHAMPION`. It creates an evidence-backed memory transition, rejects an invalid remote capsule into quarantine, records a redacted observatory event, and reloads every durable plane.

The canary requires both `SIMULATE_CCOS_ACTIVATION_GATE` and `VERIFY_CCOS_ROLLBACK_GATE`. These phrases authorize only the simulation. Runtime configuration still blocks activation, no live source changes, and rollback reports that there was no effect to reverse.

`scripts/obtuse/blunder_ccos_operationalization_proof.py` separately corrupts a test index, verifies fail-closed inspection, restores the exact last-known-good generation, proves orphan preservation, ages only the canary record into retention review, and compares canonical source identity before and after.

## Verification contract

The native suite currently contains 169 passing tests. Runtime-specific coverage includes:

- deterministic content identity across mapping order;
- strict configuration and repository path confinement;
- prohibited authority, credential, secret, and private-reasoning fields;
- duplicate-key, non-finite-number, payload-size, tamper, and invalid-chain rejection;
- repeated thread concurrency and multi-process coordination;
- exact round-trip and sequence identity;
- corrupt-index inspection and explicit recovery;
- live-writer preservation and stale dead-writer quarantine;
- non-destructive retention;
- held-out no-champion, quarantine, and authority-closed canaries;
- authenticated HTTP boundaries and security headers.

The private GitHub workflow adds a Windows and Ubuntu matrix over Python 3.11 and 3.13. The complete Windows proof now has 14 stages and includes CCOS operationalization before secret hygiene, private readiness, capability audit, and exact-source provenance.

## Claims and remaining limits

The runtime proves local durability and governance boundaries; it does not prove long-term cognitive improvement. Blunder must continue to report `NO_CHAMPION`, `INSUFFICIENT_HOLDOUT_EVIDENCE`, or `INSUFFICIENT_LONGITUDINAL_EVIDENCE` until fresh fixed-budget comparisons, multiple promotion generations, cross-model/domain transfer, ablations, and independent reproduction satisfy the research gate.

Provider-backed operation, federation, automatic activation, live self-modification, and meta-evolution remain disabled. A runtime record is evidence, not a signature or an operator decision. Filesystem access by a hostile local administrator is outside the trust boundary; content addressing detects accidental or uncoordinated mutation but is not a substitute for host security or signed release provenance.
