# DumbMoney Release Evidence Format

The tracked deployment directory is a release scaffold, not an installer and
not trading authorization. Its Python commands perform validation, planning,
hashing, copying into a new migration package, or copying into quarantine. They
contain no broker client and no Windows service-control operation.

## Private local boundary

DumbMoney v1 is a private, local-only Windows repository and runtime. Release
assembly does not create or update a public GitHub repository, branch, pull
request, release, Pages site, hosted dashboard, hosted control plane, public
artifact store, remote updater, cloud secret store, or telemetry collector.
Runtime logs, evidence, readiness, migrations, backups, and telemetry remain on
operator-controlled local storage and are not published.

Repository pins use exact `private-local://<component>` identifiers. Remote
source URLs are rejected; immutable commit, tree, dependency-lock, package, and
contract digests provide source provenance without turning a remote host into a
distribution or control channel. The only declared runtime egress is the
bounded model gateway and each venue service's official endpoint.
When repository roots are supplied, validation also requires the absolute
non-link path and SHA-256 of `git.exe`; it re-hashes and invokes that executable
under a sanitized allowlist environment rather than trusting ambient `PATH`.

## Release evidence

A deployable release has three independent layers:

1. `fund.lock.json` pins full Git object IDs, dependency-lock digests, package
   digests, contract schemas and fixtures, and a signed internal-use grant.
2. `services.v1.json` describes exactly five services, their dependencies,
   indirect command bindings, OS-protected credential target references,
   broker authority, and dynamic readiness output.
3. `release-manifest.json` pins the fund lock, service manifest, schemas,
   rendered WinSW configuration, release artifacts, and the exact bytes copied
   to mutable installation locations by SHA-256 and byte size.

Those three filenames are materialized at the staged release root. The
repository-layout scaffold copies remain pinned tooling inputs, but Core
strict-loads the root-level bytes so its configured paths and manifest
references cannot diverge.

The inventory also pins the local release/readiness/service-plan validator
modules and their CLIs. A staged candidate therefore carries the exact
fail-closed validator code used to assess it instead of depending on mutable
source beside the staging directory.

The internal-use grant covers every bundled repository—Blunder, Dummy, Dopey,
Doofus, Waterboy, Nimrod, and Dimwit—because the release lock represents all
seven as one local system. The grant does not replace or relax any third-party
license, model, data, or broker obligation.

The `installed_artifacts` set is exactly fifteen entries: Core runner config,
risk policy, Core Ed25519 public key, role-public-key bundle, Research Mesh
runner config, Research Mesh public key, Research allocator public key, Model
Gateway runner config, Model Gateway public key, Dummy Kalshi runner config,
Dopey Robinhood runner config, pinned Codex executable, pinned local Ollama
executable, verified Ollama runtime/model-store evidence, and the prebuilt
signed desktop executable. Each entry binds a release-relative source, exact
install path, state, digest, byte size, and component. The source must also be
present in the release file inventory. Unresolved config, key, account,
authorization, dependency, executable, Authenticode, process-owner,
listener-owner, model-digest, or model-store ACL evidence keeps the candidate
`PARTIAL`.

Each service command binding is an object with a release-relative
`executable`, its `sha256`, byte size, source `component`, and an ordered
`arguments` array. The executable must use the exact service runner name and
also appear in the release file inventory with the same digest, size, and
component. Arguments cannot contain secrets, environment expansion, absolute
or broad root paths, unresolved values, or shell-composition syntax. A sealed
release therefore cannot point a service at a mutable laptop checkout, generic
shell, or unpinned launcher. The Dopey binding additionally rejects the inherited
`C:\Users\CH\dopey` / `operator_live_branch --execute` launch path.

The WinSW `*_ARGUMENTS` placeholder is rendered only from this validated
ordered array. Unknown, additional, reordered, or omitted arguments fail
validation. Core retains only the production runner's exact four-item argv:

```text
--config C:\ProgramData\DumbMoney\config\core-runner.v1.json --config-sha256 <exact-config-bytes-sha256>
```

The digest must equal
`release-manifest.json#/installed_artifacts/core-runner-config/sha256`; it is
not read from the config itself or from a mutable sidecar. The strict public
config contains credential target IDs, role public-key bundle, pinned fund
lock/service manifest paths and hashes, policy and Core-public-key hashes,
endpoint, and readiness settings. Its schema is independently hash-pinned.
Research Mesh and Model Gateway use the same exact config-path/config-digest
shape:

```text
--config <fixed ProgramData path> --config-sha256 <exact-config-bytes-sha256>
```

Dopey retains its exact Core/token/profile/mode/readiness references and appends
`--config-sha256 <exact-installed-dopey-config-bytes-sha256>`; the config path
is fixed in code. Its Codex dependency is hash-pinned and forced to the local
Ollama provider; Dopey receives no OpenRouter credential. The matching evidence
must statically attest the exact Ollama executable, dedicated non-service
process identity and Windows SID, expected loopback-listener owner, model tag
and digest, and ACL-protected model-store inventory. It contains no
restart-volatile PID. Before every Codex spawn, Dopey must re-resolve the live
process ID, listener owner and TokenUser SID, image path/hash, model tag, and
model digest against those static pins. The $10/day OpenRouter budget is
enforced only by the sealed risk
policy and Model Gateway config/runtime, not by a mutable command line. Venue
cells retain `--start-mode RECONCILIATION_ONLY`. Static `--execute` or live-mode
launch arguments are forbidden because launch configuration is not capital
authority.

The Core service config binding identifies the same sealed-manifest field.
Production desktop launchers expose that exact value as
`DUMBMONEY_CORE_CONFIG_SHA256`; development-only path and digest variables are
not release inputs.

Tracked files named `*.template.json` are intentionally unresolved. They must
return `PARTIAL`. The tracked Core config digest is the exact digest of the
unresolved template bytes so path-to-digest wiring can be validated, but those
bytes remain non-runnable. Converting a template or installed artifact to
`SEALED` without resolving every pin is invalid.

Validation status means:

- `PASS`: every declared artifact exists, every digest and Git SHA matches,
  all required repositories are clean, the service set is exact, contract
  fixtures are present, the internal-use grant is signed and hash-pinned, and
  the private-local/no-telemetry invariants match.
- `PARTIAL`: a template, clean checkout, parallel contract component, command
  binding, package digest, or signed owner grant is missing. It is not
  deployable.
- `FAIL`: evidence is malformed or contradictory, a path escapes the release,
  or a supplied source/artifact differs from its pin.

The validator never upgrades `PARTIAL`, starts a service, contacts a broker, or
creates authorization.

## Readiness descriptors

Every service writes a short-lived descriptor to:

```text
C:\ProgramData\DumbMoney\readiness\<service-name>.json
```

The signed payload carries the dynamic loopback port, process ID, release and
fund-lock digests, fencing generation, authority mode, health, capabilities,
observation time, and expiry. The TTL may not exceed 120 seconds. Structural
validation without a trusted Ed25519 public key returns `PARTIAL`; production
readiness requires cryptographic verification and the matching fund contract.

The descriptor is discovery evidence only. `READY` is not a capital envelope
and cannot authorize a broker call.

## Credential target bindings

`services.v1.json` contains opaque `secret-ref:` identities plus exact,
non-secret `credential-target:`, `config-ref:`, `endpoint-ref:`, and
`readiness-ref:` bindings. The service validator rejects a missing or additional
binding. These identifiers tell the operator and runner which OS-protected
credential or local configuration to resolve; they are never the secret value.

Core uses separate targets for its Ed25519 signer, read-only desktop token,
operator token, allocator token, Dummy cell token, and Dopey cell token. Role
public keys remain a non-secret bundle. Research receives its mesh signing
seed, allocator signing seed, allocator bearer-token, and Model Gateway client
token targets, but no OpenRouter key. Model Gateway receives distinct
OpenRouter, gateway-signing, and client bearer-token targets.

The unprivileged Doofus worker has no token, credential target, network
allowlist, broker authority, direct model/provider connection, or service
identity. Its exact `model_access` is `TYPED_FILE_SPOOL_ONLY`: it may write only
the model request spool and the content-addressed create-once research object
store, and may read only model responses, model outcomes, and that object
store. Research Mesh—not the worker—authenticates the local Model Gateway and
performs the bounded relay.
Each venue cell has only its own Core bearer-token target and venue
credential/profile targets.

## Commands

From the repository root:

```powershell
python scripts/dumbmoney/plan_windows_services.py

python scripts/dumbmoney/validate_release.py `
  --git-executable 'C:\Program Files\Git\cmd\git.exe' `
  --git-executable-sha256 <exact-git-executable-sha256> `
  --repo blunder=C:\src\engine\blunder-dumbmoney `
  --repo dummy=C:\src\engine\dummy-dumbmoney `
  --repo dopey=C:\path\to\clean-dopey `
  --repo doofus=C:\path\to\clean-doofus `
  --repo waterboy=C:\path\to\clean-waterboy `
  --repo nimrod=C:\path\to\clean-nimrod `
  --repo dimwit=C:\path\to\clean-dimwit

python scripts/dumbmoney/validate_readiness.py `
  C:\ProgramData\DumbMoney\readiness\DumbMoneyCore.json `
  --expected-service DumbMoneyCore `
  --public-key C:\ProgramData\DumbMoney\keys\readiness-public.pem `
  --key-id dumbmoney-readiness-v1 `
  --require-parallel-contract
```

Exit code `0` is `PASS`, `1` is `FAIL`, and `2` is `PARTIAL` for release and
readiness validation.
