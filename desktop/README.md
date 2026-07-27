# DumbMoney private-local desktop

The DumbMoney desktop is a read-only Tauri 2 cockpit for the single supervised
`DumbMoneyCore` Windows service. It does not bundle, launch, or own a production
Core process. The renderer has no shell, filesystem, credential, or direct
network authority. Its only native command accepts the exact bodyless request
`GET /api/v1/fund/control-snapshot`.

Before attaching, the Rust host:

- reads the fixed production config at
  `C:\ProgramData\DumbMoney\config\core-runner.v1.json`;
- requires `DUMBMONEY_CORE_CONFIG_SHA256` to equal the release-pinned digest of
  those exact config bytes;
- verifies the risk policy, Core public key, role-key bundle, fund lock, and
  service manifest digests;
- verifies the short-lived Ed25519-signed Core readiness descriptor and binds
  its loopback endpoint to those same digests; and
- resolves the desktop read token from the configured Windows Credential
  Manager target. The token is never exposed to the renderer.

The private release launcher must supply the non-secret config digest from its
sealed release binding. A missing or mismatched pin fails closed. The tracked
release template intentionally cannot launch production.

## Validation

From `desktop`:

```powershell
npm ci
npm run typecheck
npm run build
cargo fmt --check --manifest-path src-tauri\Cargo.toml
cargo check --locked --manifest-path src-tauri\Cargo.toml
cargo test --locked --manifest-path src-tauri\Cargo.toml --lib
```

## Isolated development spawn

Development spawn is opt-in and cannot reuse the production config, data, or
readiness roots:

```powershell
$env:DUMBMONEY_DESKTOP_DEV_SPAWN = '1'
$env:DUMBMONEY_FUND_CORE_DEV_EXE = 'C:\DumbMoneyDev\bin\blunder-fund-core.exe'
$env:DUMBMONEY_DESKTOP_DEV_CONFIG_PATH = 'C:\DumbMoneyDev\config\core-runner.v1.json'
$env:DUMBMONEY_DESKTOP_DEV_CONFIG_SHA256 = '<exact-lowercase-sha256>'
npm run tauri:dev
```

The dev config must use distinct
`credential-target:DumbMoneyDev/*` targets. Production paths or the default
production config are rejected before a process is spawned. Dev spawn passes
both `--config` and `--config-sha256` to the exact DumbMoney fund runner and
owns it in a kill-on-close Windows Job Object.

Service packaging, credential provisioning, and venue activation remain
governed by `docs/DUMBMONEY_WINDOWS_RUNBOOK.md`; a successful desktop build is
not live-trading authorization.
