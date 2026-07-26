# Blunder Frontier Control Plane: P13-P20

## Outcome

Blunder's frontier layer is an operational first-party control plane for semantic scope, agent evaluation, parallel mission proof, bounded self-coding, language intelligence, evidence-led discovery, reliability campaigns, and private release governance. These capabilities share the existing repository intelligence, capability manifests, Mission Control, validation boundary, artifact conventions, and localhost IDE rather than operating as sidecar tools.

The implementation preserves one authority rule across all eight priorities:

> Evidence can narrow a decision, but it cannot grant integration, commit, push, release, credential, benchmark, destructive, or safety-bypass authority.

## Capability map

| Priority | First-party module | IDE or operator surface | Terminal proof |
| --- | --- | --- | --- |
| P13 | `blunder/intent_guard.py` | Native-agent preflight court | `APPROVE` or `REJECT` with per-hunk evidence |
| P14 | `blunder/evaluation_arena.py` | `blundercode arena` | correctness, false-accept, intervention, usage, and provenance policy |
| P15 | `blunder/mission_campaign.py` | Frontier mission-campaign pulse | disjoint claims, budgets, synthesis, and primary-tree gates |
| P16 | `blunder/autonomy_lane.py` | Frontier self-coding preflight | blocked or isolated-launch authorization; all downstream authority remains false |
| P17 | `blunder/language_intelligence.py` | Frontier language panel and editor APIs | references, completions, preview-only rename, diagnostics, structural diff |
| P18 | `blunder/discovery_engine.py` | Frontier discovery panel and `blundercode research` | source paths, digests, excerpts, contradictions, and four hypotheses |
| P19 | `blunder/reliability_campaign.py` | `blundercode reliability` | live-cycle gates and four deterministic recovery drills |
| P20 | `blunder/release_governance.py` | release CLI plus safe-merge script | exact commit/tree/schema/check/proof gates and signed manifest |

## P13: Intent Diff Firewall

The firewall executes after the selected-plan path ceiling and before deterministic preflight, independent model review, snapshots, validation, or writes. It compares proposed content to the actual workspace source and records line-level hunks, matched task terms, requested scope tokens, semantic alignment, and findings.

Adjacent equal-length replacements are split into individual hunks. This is important because a valid P10 edit and a collateral punctuation change on P9 must not become one broadly aligned diff block. Exclusive tasks such as “change only P10” therefore reject the unrelated P9 hunk even when the intended line is correct.

## P14: Agent Evaluation Arena

The arena uses a versioned five-case suite spanning collateral intent, strict parsing, honest recovery, security boundaries, and research provenance. A candidate measurement is evaluated for:

- expected-path recall and precision;
- forbidden or undeclared paths;
- required semantic evidence;
- validation, reviewer, and court verdicts;
- honest terminal state;
- elapsed time, tokens, and cost;
- operator intervention and false acceptance.

No candidate record means `NO_CANDIDATES`, not a fabricated pass. Competitor baselines are accepted only as supplied records; the report explicitly states that source and measurement provenance are required.

## P15: Mission Program Canaries

A completed program passes only when all of these conditions hold:

- two or three specialists exist;
- every specialist owns at least one path;
- ownership sets are disjoint;
- every specialist completed and is `READY_TO_APPLY`;
- measured elapsed time, calls, tokens, and cost fit configured ceilings;
- synthesis completed, is `READY_TO_APPLY`, and reports `SYNTHESIZED_AND_VALIDATED`;
- no specialist was applied to the primary review tree.

The result is review evidence, never automatic integration.

## P16: Bounded self-coding

Self-coding reads the ignored operator-owned `configs/obtuse/blunder-operator-autonomy-authorization.json`. The checked-in example remains disabled. A preflight requires:

- the exact authorization schema;
- `enabled: true` and a non-placeholder authorization ID;
- an explicit operator statement and the `bounded_self_coding` scope;
- an explicit future expiry;
- preservation of all hard denials;
- one valid first-party target;
- a task that does not request push, publication, deployment, secrets, benchmarks, destructive operations, or safety weakening;
- at most 900 seconds, three provider calls, 60,000 tokens, and $0.25;
- a clean review tree when the caller requires it.

Passing preflight can start only one claimed isolated mission. It cannot integrate, commit, push, deploy, or expand permissions.

## P17-P18: Code intelligence and discovery

Language Intelligence parses Python source directly and uses Repository Intelligence for symbol-aware completions. Rename remains a conflict-aware preview. Structural diff reports added, removed, and preserved top-level symbols alongside changed line regions. Workspace diagnostics surface syntax errors, trailing whitespace, and missing return annotations without modifying source.

The Discovery Engine uses ranked local repository records. Every evidence row retains its path, SHA-256 digest, relevance, symbols, imports, and a bounded excerpt. It then checks contradiction pairs and constructs precision, systems, adversarial, and synthesis hypotheses. Each hypothesis has a falsifiable prediction, experiment, proof cost, novelty, uncertainty, evidence paths, and deterministic score.

## P19: Reliability campaign

The campaign samples the real owned idle-runtime status command. Each cycle gates active state, PID identity, heartbeat age, failed/blocked/quarantined counts, and the final operator recommendation. It also executes four provider-free fault drills:

1. atomic replacement preserves one complete generation;
2. truncated JSON is rejected;
3. a stale process identity is detected;
4. budget exhaustion produces `INCONCLUSIVE_BUDGET_EXCEEDED`.

The completed review-tree campaign observed three real cycles, all healthy, and all four fault drills passed. It did not synthesize cycles, mutate source, or call a provider.

## P20: Private release governance

The release manifest binds:

- semantic project version;
- exact Git commit;
- deterministic tracked-tree digest;
- tracked file count and schema inventory;
- clean-worktree state;
- private-readiness status and private visibility;
- continuous-proof status and matching commit;
- named required-check conclusions;
- HMAC-SHA256 signature from an operator-supplied environment key.

The key must contain at least 32 characters and is never written into the manifest. Manifest verification recomputes both signature and unsigned-payload digest. Any changed field invalidates verification.

`scripts/obtuse/blunder-safe-merge.ps1` independently rechecks private visibility, pull-request state, exact head SHA, checks, and mergeability. It requires `MERGE <pr-number> <sha>` and remains report-only unless its explicit execution switch is true.

## Verified review-tree evidence

| Proof | Result |
| --- | --- |
| Full native integration suite | 78/78 passed |
| P13-P20 focused integration suite | 8/8 passed |
| Dependency-free shell smoke | `BLUNDER_SHELL_SMOKE_PASS` |
| Evaluation arena without measurements | `NO_CANDIDATES`, zero fabricated baselines |
| Local discovery canary | 12 evidence sources, four hypotheses, zero provider calls |
| Live reliability campaign | 3/3 healthy cycles, four of four fault drills passed |
| Capability registry | six first-party packs, 20 skills, 29 tools, six hooks |
| S-tier report-only audit | 184/184, zero critical failures, zero failed gates |
| Private repository readiness | ready for private GitHub review, private visibility verified, zero secret findings |
| Autonomous self-coding | operational preflight; live launch remains blocked until the operator-owned authorization file exists and passes |
| Release signing | signed-manifest round trip and tamper rejection passed in a real temporary Git repository |

The P13-P20 implementation is committed on its review branch. Private release preparation must remain blocked until that exact committed SHA receives continuous proof and an operator-owned signing key is supplied.
