# Blunder P41-P50 Creation Forge

## Outcome

Blunder now has a first-party creation and intelligent-systems layer for designing distinctive products, human-centered AI behavior, and durable automations. It produces portable, content-addressed contracts and does not inherit authority to run providers, install tools, schedule workflows, mutate source, publish, or create external effects.

The implementation adds six native modules, one CLI, one capability pack, authenticated loopback APIs, a responsive Creation Forge IDE surface, an example creative brief, and integration coverage. It adds no Python or JavaScript package dependency.

## Research translated into architecture

The work used primary specifications, official technical guidance, and original research as design input:

- OpenAI's agent guidance separates orchestration, tools, state, guardrails, human review, tracing, and evaluation. Blunder translates those concepts into provider-independent first-party contracts rather than taking an SDK dependency: <https://developers.openai.com/api/docs/guides/agents>
- OpenAI's evaluation guidance recommends debugging complete traces before turning known-good behavior into repeatable datasets and eval runs. Blunder's Trace Observatory grades the complete decision path and emits promotion eligibility: <https://developers.openai.com/api/docs/guides/agent-evals>
- Structured Outputs use JSON Schema to make model output contracts machine-checkable. Blunder applies the same principle to prompt programs, tools, AI outputs, and automation nodes without requiring one provider: <https://developers.openai.com/api/docs/guides/structured-outputs>
- MCP defines interoperable resources, prompts, and tools. Blunder's Open Tool Foundry emits an MCP-compatible view while its own closed permission vocabulary remains authoritative: <https://modelcontextprotocol.io/specification/2025-11-25>
- OpenTelemetry semantic conventions provide shared meaning for spans and attributes. Blunder uses compatible trace concepts while keeping its trace record deliberately small and first-party: <https://opentelemetry.io/docs/specs/semconv/>
- The W3C Design Tokens Community Group defines portable design-token formats. Creation Forge identifies DTCG-compatible JSON as its interchange target instead of requiring a proprietary design tool: <https://www.w3.org/community/reports/#Design-Tokens>
- WCAG 2.2 makes accessibility criteria testable and technology-independent. Creation Forge requires keyboard, semantic, focus, reduced-motion, reflow, and non-color state rules while explicitly preserving manual review: <https://www.w3.org/TR/WCAG22/>
- Google's People + AI Guidebook organizes AI product design around user value, mental models, explainability, feedback and control, data and evaluation, and graceful failure. AI System Architect makes each area explicit in the architecture contract: <https://pair.withgoogle.com/guidebook-v2/chapters>
- MAP-Elites demonstrates why a collection of diverse high-performing solutions can reveal more of a design space than early convergence on one optimum. Blunder's Creative Quality-Diversity Arena preserves the highest evidenced candidate in each novelty/coherence niche: <https://arxiv.org/abs/1504.04909>

## Capability line

| Priority | Capability | Primary artifact |
| --- | --- | --- |
| P41 | Creative Compass | `blunder/creative_direction.py` |
| P42 | Experience Storyboard | `blunder/creative_direction.py` |
| P43 | Generative Interface Contract | `blunder/creative_direction.py` |
| P44 | AI System Architect | `blunder/ai_architect.py` |
| P45 | Automation Graph Compiler | `blunder/automation_fabric.py` |
| P46 | Open Tool Foundry | `blunder/open_tool_foundry.py` |
| P47 | Prompt and Context Programs | `blunder/prompt_program.py` |
| P48 | Trace Observatory | `blunder/trace_creativity_lab.py` |
| P49 | Creative Quality-Diversity Arena | `blunder/trace_creativity_lab.py` |
| P50 | Creation Forge Control Plane | `blunder/shell`, CLI, capability pack, tests, and this report |

## Hard behavioral boundaries

- Creative direction generation does not declare a champion.
- An archive with no candidates reports `NO_CANDIDATES`.
- A candidate needs explicit evidence before entering the creative archive.
- Accessibility below the champion threshold blocks champion status even when novelty is high.
- AI product decisions remain owned by the user.
- External content and tool output remain untrusted data, not instructions or authority.
- Action and AI automation nodes require idempotency keys.
- High-risk automation nodes require compensation and a direct preceding human gate.
- Automation cycles are rejected; retries are bounded node policy, not invisible graph loops.
- External-effect tools require `approval=always`.
- Trace promotion fails on orphaned spans, unapproved tools, ungrounded outputs, missing successful output, or errors.
- The IDE, API, and CLI compile reports only. Execution, source mutation, scheduling, provider use, and publication remain false.

## Verification

The focused Creation Forge, Product Studio token, capability-registry, and shell/API selection passes 50 tests. The complete native suite passes 105 of 105 tests. Python compilation and Node syntax checks pass. The S-tier capability audit passes 184 of 184 points with 8 first-party packs sealed. Private readiness reports `READY_FOR_PRIVATE_GITHUB_REVIEW`, zero blockers, zero secret findings, and verified private visibility for `ObtuseAI/blunder`.

Live in-app browser proof exercised every Creation Forge control: creative compilation returned four rendered directions; AI architecture returned `READY_FOR_AI_SYSTEM_REVIEW`; automation returned `READY_FOR_DRY_RUN`; prompt compilation returned `READY_FOR_EVALUATION`; trace grading returned `PASS`; the creative archive returned `CHAMPION_AVAILABLE_FOR_REVIEW`; and the browser console remained free of warnings and errors. A dedicated 390 by 844 viewport check found and repaired a navigation overflow, then verified `scrollWidth == clientWidth`.

The published private branch is also verified by GitHub's `release-proof` workflow, which materializes the exact remote SHA before running the Blunder proof court.
