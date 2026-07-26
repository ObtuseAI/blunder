# Blunder isolated mission branches

Status: operational in the active localhost-only Blunder runtime.

## Outcome

Mission Control can supervise up to three first-party coding agents in parallel. Every mission receives an independent Python process, native context build, routed capability contract, `codex/mission-*` Git branch, and external Git worktree. The current uncommitted review state is seeded into each worktree without creating a commit.

The seeded review state is staged only inside the mission worktree. This makes the final binary patch mission-only. Before integration, Blunder compares Git object identities for every changed path against the primary review tree and refuses drifted patches. Integration requires the exact phrase `INTEGRATE <mission-id>`, re-runs the drift guard and `git apply --check`, and applies no commit or push.

## First-party capability fabric

- 6 sealed Blunder capability packs
- 18 on-demand or isolated skills
- 21 bounded tools, including three source-mutation tools available only to coding-capable routes
- 6 deterministic lifecycle hooks
- No external plugins and no arbitrary command surface

## P0-P6 operational model

- **P0 — validation boundary:** generated Python executes only in a child validation process with a credential-free allowlisted environment. A protected audit hook denies external DNS and network access, outside-worktree file access, child processes, and native-library loading from changed or untrusted repository frames. Pre-mutation hashes preserve legitimate behavior for unchanged trusted tests. This is a Python process boundary, not a claim of kernel or virtual-machine isolation.
- **P1 — mission DAG:** repository intelligence partitions relevant paths into as many as three non-overlapping specialist claims. A synthesis node depends on every specialist and produces a separate, validated mission-only patch.
- **P2 — independent review:** a third provider stage receives a separate read-only context, the proposed complete replacements, the selected plan, and bounded source evidence. It can approve or reject but cannot write. Machine checks reject empty content, duplicate paths, and paths outside the selected plan before mutation.
- **P3 — repository intelligence:** a cached first-party index maps source files, symbols, imports, tests, Git history, and current changes. Task-aware retrieval strips mission-contract boilerplate, expands relevant dependencies, and preserves context breadth within hard byte ceilings.
- **P4 — budgets and telemetry:** elapsed time, provider calls, attempts, tokens, and cost are enforced and emitted to JSONL. Provider attempts have a 120-second total wall-clock deadline in addition to socket timeouts; missing usage evidence fails closed.
- **P5 — lifecycle:** workers use command identity nonces rather than PID-only reattachment. Deadlines cancel over-budget missions. `RECONCILE <id>` creates a new worktree and performs per-file three-way reconciliation. `CLEAN <id>` removes only recorded terminal worktrees and `codex/*` branches after exact confirmation. Retention marks old evidence as prunable; it does not silently delete it.
- **P6 — outcome learning:** accepted, integrated, reverted, and rejected outcomes append to a JSONL ledger with score, metrics, files, skills, and budget. Skill signals bias later routing toward high-scoring evidence and away from reverted or low-scoring outcomes without widening permissions.

## Proof

- 58 repository integration and boundary tests passed in both the Git review tree and active runtime.
- The same 58 tests passed inside the P0 validation sandbox with credentials removed and external network, outside-worktree access, and generated subprocesses denied.
- The real-worktree suite proved review-state seeding, mission-only patch generation, primary drift rejection, exact-confirmation integration, reconciliation, cleanup, three concurrent worker processes, three distinct worktrees, and fourth-worker refusal.
- Real-provider mission `m-20260711-062853-ee918d` completed strategy, implementation, independent review, and sandbox validation on one isolated documentation file: 3 provider calls, 76,489 tokens, $0.008767, 234.481 seconds, and 58 passing tests. Its patch was not integrated; operator evidence review rejected two imprecise claims and recorded the result through P6.
- JavaScript syntax validation passed, and browser verification found no console warnings or errors.
- Canonical audit run `20260711-012901`: `BLUNDER_S_TIER_AUTONOMOUS_CAPABILITY_READY`, 184/184, zero critical failures, zero failed gates.
- Idle autonomy remained active, PID-valid, reliability-green, with no failed, blocked, or quarantined queue work.

## Active configuration

The active non-Git runtime reads a local ignored configuration at `artifacts/obtuse/blunder-shell-v2/mission-branches.json`. It points branch creation at the private review repository and places disposable worktrees outside that repository. The portable source example is `configs/obtuse/blunder-mission-branches.example.json`.
