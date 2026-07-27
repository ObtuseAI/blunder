# DumbMoney Deterministic Windows Runner Build

The Windows runner builder is private-local release tooling. It never installs
or starts a service, writes to Program Files or ProgramData, signs an
executable, contacts a broker, reads a credential, or publishes an artifact.
Dry-run is the default and performs no filesystem mutation.

## Exact runner set

Every build specification contains exactly these bindings:

| Service | Component | Executable |
|---|---|---|
| `DumbMoneyCore` | `blunder` | `DumbMoneyCore.exe` |
| `DumbMoneyResearchMesh` | `blunder` | `DumbMoneyResearchMesh.exe` |
| `DumbMoneyModelGateway` | `blunder` | `DumbMoneyModelGateway.exe` |
| `DumbMoneyDummyKalshi` | `dummy` | `DumbMoneyDummyKalshi.exe` |
| `DumbMoneyDopeyRobinhood` | `dopey` | `DumbMoneyDopeyRobinhood.exe` |

The tracked `windows-build.v1.template.json` is deliberately incomplete. All
five services have exact entrypoints. The absolute Git and Windows PowerShell
executable paths and SHA-256 digests, Python, PyInstaller, component
dependency-lock, prebuilt signed desktop, Dopey Codex/Ollama executable, and
local Ollama runtime-evidence pins remain unresolved. The tool therefore
returns `PARTIAL` and creates no staging directory.

A resolved spec must identify a synchronous importable callable with no
required injected arguments for every service. Its module and dependency lock
must be tracked in the supplied clean Git repository. A `.cmd`, `.bat`, `.ps1`,
Python script, shortcut, legacy scheduled-task launcher, or generic shell can
never substitute for the exact `.exe`.

## Deterministic inputs and provenance

The build pins and records:

- exact Python patch version and executable SHA-256;
- exact PyInstaller version and installed-distribution digest;
- absolute, non-link Git and Windows PowerShell executable paths, SHA-256
  digests, and observed versions;
- fixed `SOURCE_DATE_EPOCH`;
- repository commit and tree IDs;
- one tracked dependency lock and digest per component;
- entry module, callable, hidden imports, component, and executable name;
- each resulting executable's SHA-256 and byte size;
- the build spec, release template, and build-tool digests.

Git, Windows PowerShell, and Python are invoked by their absolute pinned paths
and re-hashed immediately before use. PyInstaller runs without a shell using a
generated minimal bootstrap, `--onefile`, `--clean`, `--noupx`, fixed output
paths, deterministic Python environment variables, and a sanitized allowlist
environment that neither inherits ambient `PATH` nor application credentials.
Outputs must have a Windows PE `MZ` header. Build provenance records all three
executable identities and the `SANITIZED_ALLOWLIST_V1` environment policy.

Build provenance explicitly records that no Authenticode signing, service
control, installation, or broker action occurred. Signing and installation
remain separate reviewed operator steps.

## Commands

Dry-run:

```powershell
python scripts\dumbmoney\build_windows_release.py `
  --staging-root C:\DumbMoneyStaging\candidate-001 `
  --release-id candidate-001 `
  --desktop-executable C:\reviewed\DumbMoney.exe `
  --repo blunder=C:\clean\blunder `
  --repo dummy=C:\clean\dummy `
  --repo dopey=C:\clean\dopey
```

The staging path must not exist, its parent must already exist, and it must be
outside every source repository, Program Files, and ProgramData.

After resolving and reviewing every build pin, add `--perform-build`. The tool
creates only the new staging root:

```text
<staging-root>\
  .build\
  release-candidate\
    fund.lock.json
    release-manifest.json
    services.v1.json
    desktop\
      DumbMoney.exe
    runners\
      DumbMoneyCore.exe
      DumbMoneyResearchMesh.exe
      DumbMoneyModelGateway.exe
      DumbMoneyDummyKalshi.exe
      DumbMoneyDopeyRobinhood.exe
    provenance\
      windows-runner-build.v1.json
      windows-runner-build-provenance.v1.json
```

The renderer copies the hash-pinned release scaffold and its local validation
modules/CLIs. It also materializes the exact fund lock and service manifest
bytes at the release root, matching the three root-level paths Core verifies at
use time. It adds the five exact runner bindings, copies the separately built
Authenticode-valid
`DumbMoney.exe`, records all six executable byte pins and provenance, and
invokes the existing release validator against the candidate. It preserves
`release_state: TEMPLATE`; executable packaging does not resolve public
configs, public keys, repository/package pins, the Dopey Codex/Ollama
dependencies, verified local Ollama process/model-store evidence, signed
internal-use authorization, desktop deployment receipt, or operational
deployment receipts. The candidate must therefore remain `PARTIAL` until
those independent inputs are genuinely supplied and a separate sealing review
occurs.

The build has no publication, updater, hosted-control-plane, cloud-secret,
telemetry, service-install, or broker authority. The desktop launcher scripts
and documentation are pinned release files, but `DumbMoney.exe` is not one of
the five service executables and is never fabricated or signed by this builder.

Re-run `validate_staged_runner_provenance` immediately before any later sealing
step. A missing, changed, linked, renamed, non-PE, or sidecar-confused runner
fails closed.
