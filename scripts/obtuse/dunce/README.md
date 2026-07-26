# Dunce Subsystem (`scripts/obtuse/dunce/`)

This directory is part of the **OBTUSE_BLUNDER_DUNCE_IMPORT_AND_BENCHMARK_APPLIANCE_MODE_BUNDLE_V1** Phase 2 in-repo import.

## Scope

The Dunce subsystem is a bounded, read-only-style import of existing Dunce proof-case, regression, bridge, and utility scripts already present in `scripts/obtuse`. It does **not** create new runtime controllers, does **not** launch benchmarks, and does **not** modify historical proof bundles.

## What lives here

- `README.md` — this file.
- The canonical copies of the Dunce scripts are imported into the top-level `dunce/` directory at repository root.
- Original scripts under `scripts/obtuse/blunder-dunce-*.ps1` remain untouched.

## Key Dunce scripts imported

| Script | Purpose |
|--------|---------|
| `blunder-dunce-proof-regression-pack.ps1` | Core Dunce proof regression pack. |
| `blunder-dunce-to-self-coding-queue-bridge.ps1` | Bridge from Dunce proof work to the self-coding queue. |
| `blunder-dunce-regression-substrate-idle-cycle.ps1` | Regression substrate that hooks into idle cycles. |
| `blunder-dunce-cross-machine-proof-regression-verify.ps1` | Cross-machine portable regression verification. |
| `blunder-dunce-proof-case-catalog.ps1` | Proof-case catalog management. |
| `blunder-dunce-proof-metadata-sidecars.ps1` | Metadata sidecar generation for proof artifacts. |
| `blunder-dunce-utility-scoring.ps1` | Utility scoring helpers. |
| `blunder-dunce-inject-artifact-candidates.ps1` | Artifact candidate injection. |
| `blunder-dunce-hypothesis-promotion-check.ps1` | Hypothesis promotion checks. |
| `blunder-dunce-capability-hypothesis-synthesis.ps1` | Capability hypothesis synthesis. |
| `blunder-dunce-supersession-ledger-and-hypothesis-check.ps1` | Supersession ledger and hypothesis tracking. |
| `blunder-dunce-autonomy-loop.ps1` | Dunce autonomy loop (historical). |

## Naming boundary

All files in this subsystem use **Dunce** naming. Historical labels are excluded from active source and generated interfaces.

## Safety notes

- Do **not** use this subsystem to start autonomous idle runs, launch benchmarks, or modify production boundaries.
- Any runtime evidence window beyond the already-consumed 60-minute tier requires explicit operator approval.
