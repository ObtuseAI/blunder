# DumbMoney Dopey Migration Runbook

This process moves nonsecret Dopey configuration, strategy/model state,
calibration, evidence, paper ledgers, and history from the laptop into an
inactive quarantine. It never treats imported account state as Robinhood truth.
The transfer stays private and local: do not use a public repository, public
artifact host, shared telemetry service, or public cloud bucket as the channel.

The migration package contains data only. It must not include or recreate
Windows Scheduled Tasks, startup entries, launcher commands, or triggers. An
inherited task that references `C:\Users\CH\dopey`, `operator_live_branch`, or
`--execute` has no DumbMoney authority; retire it through the separate Windows
activation procedure before the new Dopey venue service starts.

## Laptop export

Use an operator-controlled BitLocker, EFS, or equivalently encrypted transfer
destination. First run the scanner without writing:

```powershell
python scripts/dumbmoney/dopey_migration.py export `
  --source C:\path\to\dopey
```

Review:

- every included path, digest, byte count, tier, and scan mode;
- every excluded path and rule ID;
- any `BINARY_BEST_EFFORT_SECRET_SCAN` database, Parquet, archive, or other
  uncertain file. Every byte stream is scanned both as UTF-8 and in a
  NUL-stripped ASCII domain, so ASCII credential tokens embedded in UTF-16LE or
  UTF-16BE are detected. Any NUL, UTF-16 BOM, invalid UTF-8, non-text suffix,
  compression, encryption, or other encoding uncertainty forces an exact
  operator review;
- tier totals and the package size.

Materialize only after that review:

```powershell
python scripts/dumbmoney/dopey_migration.py export `
  --source C:\path\to\dopey `
  --destination E:\DumbMoneyTransfer `
  --write `
  --encrypted-transfer-ack
```

The exporter:

- excludes hidden paths, browser state, OAuth, cookies, DPAPI material, key
  files, token-named paths, and configured secret-content matches;
- records rule IDs but never matching secret values;
- hashes and dual-domain secret-scans every included byte stream before
  copying, then verifies and rescans the copied bytes during import validation;
- writes `migration-manifest.json` last;
- renames its incomplete marker to `EXPORT_COMPLETE`;
- never overwrites an existing package.

If export fails, retain the `EXPORT_INCOMPLETE` directory for diagnosis or move
it to an operator-selected quarantine. Never import it.

## Target validation and quarantine import

Validate before copying:

```powershell
python scripts/dumbmoney/dopey_migration.py validate `
  --package E:\DumbMoneyTransfer\dopey-<migration-id>

python scripts/dumbmoney/dopey_migration.py import `
  --package E:\DumbMoneyTransfer\dopey-<migration-id>
```

If the package contains any binary entry, validation intentionally returns
`PARTIAL` with `BINARY_SECRET_REVIEW_REQUIRED`. Emit an unsigned review
template:

```powershell
python scripts/dumbmoney/dopey_migration.py review-template `
  --package E:\DumbMoneyTransfer\dopey-<migration-id> `
  --write-plan E:\DumbMoneyTransfer\binary-review.json
```

Review every exact path and hash using appropriate database/schema inspection,
archive listing or extraction in an isolated workspace, string/credential
scanning, and provenance checks. Complete the reviewer, UTC time, methodology,
limitation acknowledgement, status, and per-file results. An agent must not
self-attest this evidence.

Revalidate with the completed attestation:

```powershell
python scripts/dumbmoney/dopey_migration.py validate `
  --package E:\DumbMoneyTransfer\dopey-<migration-id> `
  --binary-review E:\DumbMoneyTransfer\binary-review.json
```

The import dry run must use the same attestation. After a `PASS`, copy only into
a new quarantine:

```powershell
python scripts/dumbmoney/dopey_migration.py import `
  --package E:\DumbMoneyTransfer\dopey-<migration-id> `
  --target C:\ProgramData\DumbMoney\migration `
  --binary-review E:\DumbMoneyTransfer\binary-review.json `
  --write
```

The resulting path is:

```text
C:\ProgramData\DumbMoney\migration\quarantine\<migration-id>
```

It contains `IMPORT_QUARANTINED` and a hash-bound receipt with
`promotion_authority=false`.

## Tiered admission

Nothing is activated directly from quarantine.

1. **Tier 1 — operational:** validate schema versions, configuration keys,
   model identities, timestamps, and source provenance. Resolve every secret
   reference from the target machine’s secret store instead of migrated data.
2. **Tier 2 — recent:** verify monotonic event time, point-in-time availability,
   calibration lineage, deduplication, and replay against Tier 1.
3. **Tier 3 — cold:** index asynchronously only after restore and resource
   limits are demonstrated. Cold data cannot block reconciliation or execution.

For SQLite inputs, open a copy read-only, inventory tables and columns, scan
text fields for credentials, run `PRAGMA integrity_check`, and record row counts
and timestamp ranges. Never attach a quarantined database to a live process.

The target machine must separately reauthorize the official Robinhood Agentic
MCP profile. A fresh Robinhood account, order, position, and settled-cash
snapshot overrides every imported local value. Promotion requires:

- schema and integrity receipt;
- source/provenance and timestamp receipt;
- secret-rescan receipt;
- duplicate/lineage reconciliation receipt;
- PaperTwin replay receipt;
- fresh broker reconciliation and exact target-account attestation.

Imported cookies, OAuth state, keys, account authority, open-order claims, and
position claims are never accepted.

## Dummy legacy ledger

Dummy uses a separate non-executable planner:

```powershell
python scripts/dumbmoney/plan_dummy_legacy_epoch.py
```

It reads at most the first MiB and file metadata, does not connect to SQLite,
and cannot checkpoint, prune, copy, cancel, or start anything. Its emitted
operator gates require a maintenance-window tool and fresh broker truth to be
reviewed separately.
