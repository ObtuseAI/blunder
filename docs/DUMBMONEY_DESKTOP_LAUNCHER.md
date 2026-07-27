# DumbMoney private-local desktop launcher

`scripts/dumbmoney/Launch-DumbMoneyDesktop.ps1` is the production desktop
attachment boundary. It verifies a sealed immutable release, the installed
Core runner configuration, and the exact desktop executable before any local
process can be started. Verification is the default. A process effect requires
the explicit `-Start` switch.

The launcher does not install or update anything, manage services, contact a
hosted control plane, publish telemetry, resolve secrets, or start a second
Core. It starts only the local desktop executable already present inside the
specified immutable release. The child receives a clean environment containing
exactly one non-secret launcher value:

```text
DUMBMONEY_CORE_CONFIG_SHA256=<manifest-pinned lowercase SHA-256>
```

## Required evidence

Pass both paths explicitly:

1. The immutable
   `C:\Program Files\DumbMoney\releases\<release-id>\release-manifest.json`.
2. A separately controlled operator-approved desktop deployment receipt.

The receipt is strict JSON with no additional properties:

```json
{
  "schema_version": "dumbmoney.desktop-deployment-receipt.v1",
  "receipt_id": "operator-change-record-or-equivalent",
  "release_id": "dumbmoney-windows-2026-07-26.1",
  "release_root": "C:\\Program Files\\DumbMoney\\releases\\dumbmoney-windows-2026-07-26.1",
  "release_manifest_path": "C:\\Program Files\\DumbMoney\\releases\\dumbmoney-windows-2026-07-26.1\\release-manifest.json",
  "release_manifest_sha256": "<64-lowercase-hex>",
  "desktop_executable_path": "C:\\Program Files\\DumbMoney\\releases\\dumbmoney-windows-2026-07-26.1\\desktop\\DumbMoney.exe",
  "desktop_executable_sha256": "<64-lowercase-hex>",
  "approved": true
}
```

`approved=true` records the external operator decision; it does not create or
replace that approval. Store and approve the receipt through the private local
change-control process. The launcher verifies its bindings but does not claim
that an unsigned JSON file is cryptographic authorization.

The launcher cross-binds the receipt release ID, canonical release root,
canonical manifest path, raw manifest digest, desktop path, and desktop digest.
It rejects duplicate JSON fields, unexpected receipt fields, non-local paths,
alternate data streams, path traversal, reparse points, and artifacts outside
the immutable release. It verifies both the immutable source and installed
bytes of `installed_artifacts[name=core-runner-config]`. For the desktop, the
receipt path and digest must also equal the exact
`installed_artifacts[name=desktop-executable]` install/source path and digest,
and that artifact must have an identical `files[path=desktop/DumbMoney.exe]`
digest and byte count.

## Verify, then explicitly start

Run verification first:

```powershell
pwsh -NoProfile -File .\scripts\dumbmoney\Launch-DumbMoneyDesktop.ps1 `
  -ReleaseManifestPath 'C:\Program Files\DumbMoney\releases\<release-id>\release-manifest.json' `
  -DeploymentReceiptPath 'C:\private-change-control\<receipt>.json' `
  -DryRun
```

Only after reviewing the `VERIFIED` result should the operator repeat the same
command with `-Start` in place of `-DryRun`. Omitting both switches remains a
verification-only run.

## Packaging boundary

`DumbMoney.exe` is a prebuilt, content-addressed release artifact, not a sixth
Windows service. Release assembly stages it only after its supplied SHA-256 and
byte count match the pinned desktop build input. A sealed manifest records it
at the exact immutable path
`C:\Program Files\DumbMoney\releases\<release-id>\desktop\DumbMoney.exe` in both
`installed_artifacts` and `files`.

The launcher rejects missing, duplicate, inconsistent, or receipt-only desktop
evidence. It does not build, copy, update, or otherwise provision the
executable itself.
