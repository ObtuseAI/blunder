# DumbMoney Windows Install, Update, Backup, Restore, and Rollback Runbook

## Safety state

The repository contains plan and validation tooling only. It does not install
WinSW, register services, change ACLs or firewall policy, contact Kalshi or
Robinhood, or activate execution.

This is a private local-only Windows deployment. Do not create or push a public
DumbMoney repository, open a public pull request, publish a release or Pages
site, upload evidence or backups, expose a hosted control plane, or export
runtime telemetry. Do not enable a remote updater, hosted runtime, or cloud
secret store. Logs, readiness descriptors, receipts, migration packages,
dashboards, and backups remain on operator-controlled local storage. Fund-lock
sources use private-local identifiers, never public repository URLs.

Every installation and restart begins in `RECONCILIATION_ONLY`. A green process
health check is not permission to trade. The venue cell must independently
verify its account, orders, positions, capital envelope, generation, kill state,
and broker reconciliation before accepting new exposure.

## Required Windows layout

```text
C:\Program Files\DumbMoney\
  tools\<winsw-version>\
  releases\<release-id>\
  current -> releases\<release-id>

C:\ProgramData\DumbMoney\
  config\
  core\
    keys\
  research\
  model-gateway\
  dummy-kalshi\
  dopey-robinhood\
  objects\
  readiness\
  backups\
  recovery\
```

Use a separate virtual service identity for:

- `DumbMoneyCore`
- `DumbMoneyResearchMesh`
- `DumbMoneyModelGateway`
- `DumbMoneyDummyKalshi`
- `DumbMoneyDopeyRobinhood`

Doofus mutation and arbitrary-command workers are a sixth identity boundary,
not a sixth service. They must run only as the unprivileged local account
`DumbMoneyResearchWorker`, with no Credential Manager targets, secrets, network
allowlist, broker authority, direct model/provider access, service registration,
or inherited service token. Its only generative path is the bounded typed file
spool consumed by `DumbMoneyResearchMesh`, which ingests immutable hash-pinned
candidate bundles and relays capital requests; it must never execute a Doofus
worker or arbitrary candidate command.

Activation is blocked until an operator records ACL, token, process-owner, and
egress evidence proving that no Doofus worker runs as Core, Research Mesh,
Model Gateway, Dummy, or Dopey. The worker's input/output directories must be
the only spool handoff. Its exact writable roots are the model request inbox
and `C:\ProgramData\DumbMoney\research-worker\artifacts\objects`; the latter
allows only content-addressed create-once writes and fails on a conflicting
object. Its exact readable roots are model responses, model outcomes, and that
same object store. Research Mesh must reject any bundle whose digest or
immutable metadata differs after the worker exits.

Only the venue identity receives its own venue credential reference.
`DumbMoneyResearchMesh` receives no broker or OpenRouter secret; it receives
only its mesh signer, allocator signer, allocator token, and Model Gateway
client-token targets.
`DumbMoneyModelGateway` receives distinct OpenRouter, gateway signer, and
client-token targets, with the $10/day hard budget.

Dopey receives no OpenRouter credential or remote model-auth token. Its pinned
Codex executable must use the local Ollama provider only. Ollama is not a sixth
DumbMoney Windows service: activation requires a separate verified evidence
record binding the pinned Ollama executable to the dedicated
`DumbMoneyOllama` process identity and machine-local Windows SID, the owner of
`127.0.0.1:11434`, the exact model tag and digest, and the owner, ACL digest,
and immutable inventory of
`C:\ProgramData\DumbMoney\ollama\models`. The attestation contains no live PID
and cannot go stale merely because the process restarts. Dopey must re-resolve
the current PID, listener owner, TokenUser SID, image path/hash, model tag, and
model digest against the static attestation immediately before every Codex
spawn.

There is exactly one production `DumbMoneyCore`. The desktop attaches to that
service's signed loopback endpoint and never launches a second legacy Core.
Core resolves its signer seed, read-only desktop token, operator token, and
allocator token plus distinct Dummy/Dopey cell bearer tokens from OS-protected
credential targets.
The role public-key bundle, Core endpoint reference, and readiness descriptor
are non-secret configuration bindings. Each venue cell receives only its own
cell token target and venue credential/profile targets. Model Gateway resolves
its three exact local Credential Manager targets.

Core WinSW arguments contain only the exact absolute public runner-config path
and the exact SHA-256 of those config bytes:

```text
--config C:\ProgramData\DumbMoney\config\core-runner.v1.json --config-sha256 <64-lowercase-hex>
```

The renderer takes the digest from the sealed, immutable
`C:\Program Files\DumbMoney\releases\<release-id>\release-manifest.json`
entry named `core-runner-config`. It does not calculate the expected value from
the mutable ProgramData copy. The same entry is the production source for an
independent desktop verifier; its containing release-manifest digest must come
from the operator-approved deployment receipt. The desktop then verifies the
actual ProgramData config bytes against that value and separately verifies
signed Core readiness.

For a production desktop attach, set `DUMBMONEY_CORE_CONFIG_SHA256` to that
manifest-pinned value. The production config path remains fixed at
`C:\ProgramData\DumbMoney\config\core-runner.v1.json`; do not supply a path
override. Development spawn is a separate trust domain: it may use only
`DUMBMONEY_DESKTOP_DEV_CONFIG_PATH` plus
`DUMBMONEY_DESKTOP_DEV_CONFIG_SHA256`, must resolve only
`credential-target:DumbMoneyDev/*` targets, and must reject the production
ProgramData path. Development variables and targets are forbidden in a sealed
Windows service or production desktop launcher.

Research Mesh and Model Gateway also receive only a fixed ProgramData config
path plus the exact sealed-manifest digest of those config bytes. Dopey uses
its fixed ProgramData config path internally; its exact reference/mode argv ends
with the sealed-manifest digest of the same bytes.

Research Mesh relays model work only through
`C:\ProgramData\DumbMoney\spool\model\{requests,responses,outcomes}`. It verifies
the Model Gateway signed readiness, public-key file digest, raw runner-config
digest, fixed request/response/prompt/token/reserve limits, and its dedicated
local client token. It receives no OpenRouter key. The unprivileged Doofus
worker has no network or credential access and can interact only through the
hash-pinned spool/object protocol.

Those strict configs contain target identifiers such as
`credential-target:DumbMoney/CoreSigner`; it never contains a resolved secret
and Core verifies the config argv digest, pinned fund lock, service manifest,
risk policy, and Core public key byte-for-byte before readiness. Other service
arguments likewise contain target IDs, never values.
Resolve identifiers under the exact virtual service identity through
Credential Manager, DPAPI, or an equivalently reviewed provider.

## Assemble and validate

1. Assemble from clean, detached or release-specific worktrees.
2. Complete and sign a separate
   `docs/DUMBMONEY_INTERNAL_USE_AUTHORIZATION.md`; never treat the tracked
   template as authorization. The signed artifact and fund lock must list all
   seven bundled repositories: Blunder, Dummy, Dopey, Doofus, Waterboy,
   Nimrod, and Dimwit.
3. Resolve every Git, dependency, package, schema, fixture, service-command,
   installed-artifact, internal-use artifact, and file digest. Generate the
   Core public key from the protected signing seed; never generate or store the
   private seed in the release.
4. Copy the resolved lock and manifest into a new release directory. Never
   modify an installed release in place.
5. Materialize all fifteen exact installed artifacts: the five public runner
   configs; risk policy; role-key bundle; the Core, Research Mesh, Research
   allocator, and Model Gateway public keys; the pinned Codex and Ollama
   executables; verified Ollama runtime/model-store evidence; and the prebuilt
   signed desktop executable. Verify every destination's digest and byte size
   before applying read-only ACLs. Source and installed bytes must be
   identical. Stop if the desktop Authenticode status is not valid or any
   Ollama process/listener/model/store observation is unresolved.
6. Render Core, Research Mesh, and Model Gateway argv as their exact `--config`
   and `--config-sha256` pairs. Render Dopey with its exact reference/mode
   sequence and final config digest. Do not use a config-adjacent digest file,
   PATH lookup, runtime override, or sidecar.
7. Run `validate_release.py` with every canonical repository root plus the
   absolute non-link `git.exe` path and exact executable SHA-256, then emit the
   Windows service plan. The validator does not trust ambient `PATH`. Stop
   unless its exact `research_worker_boundary` validates and the activation
   record proves the worker account has zero credentials and zero egress.
8. Stop if either result is `PARTIAL` or `FAIL`.
9. Independently verify the pinned WinSW executable and record its upstream
   version, origin, Authenticode result, and SHA-256 in the sealed release.
10. Render all five XML templates into staging. A second reviewer confirms there
   are no inline secrets and all executable paths remain inside the immutable
   release. The desktop launcher remains a separately pinned local artifact and
   does not become a sixth Windows service.

`validate_release.py` performs no installation and should be the only command
used until the result is `PASS`.

## Retire inherited Dopey launch authority

The laptop's inherited scheduled launcher is not a DumbMoney service and has no
DumbMoney mandate, capital envelope, fencing generation, or reconciliation
authority. In particular, a task action that references `C:\Users\CH\dopey`,
`operator_live_branch`, or an unconditional `--execute` flag is a release
blocker. Never copy that task definition, command, or trigger into a release.

During target activation, the operator must:

1. Export the matching scheduled-task definitions and current task/process
   state into the private deployment evidence directory.
2. Disable every task whose action or arguments reference the inherited Dopey
   launcher, then verify that no matching task instance or launcher process is
   running.
3. Remove those disabled legacy tasks before starting
   `DumbMoneyDopeyRobinhood`; retain only their hash-pinned evidence export.
4. Install the new Dopey venue service only from a `SEALED` command binding
   whose executable, SHA-256, byte size, and source component exactly match
   its release file inventory entry.
5. Start the new cell in `RECONCILIATION_ONLY` and reconcile Robinhood before
   considering any separately authorized execution mode.

This repository does not enumerate, disable, stop, or remove scheduled tasks.
Those mutations belong to the reviewed local operator activation window.

## Operator-owned first installation

The following is an operator procedure, not an automated script:

1. Create the versioned `Program Files` release and `ProgramData` directories.
2. Apply NTFS ACLs before copying credentials. Core can write only Core and
   readiness state; each cell can write only its journal and readiness file.
3. Copy one verified WinSW executable beside each rendered XML using the exact
   service name.
4. Review each rendered executable, exact arguments, working directory,
   dependency, stop timeout, restart policy, and release digest. Re-hash the
   installed Core config and compare it to both Core argv and the sealed
   release-manifest entry.
5. Register services one at a time using the pinned WinSW binary’s documented
   `install` operation. Do not start them yet.
6. Configure each service to its exact `NT SERVICE\<service-name>` virtual
   account and re-audit ACLs. A service still configured as LocalSystem is a
   blocker.
7. Apply reviewed outbound policy: Core is loopback-only; Research reaches only
   Core and the local Model Gateway; Model Gateway alone reaches OpenRouter;
   Dummy reaches only the pinned Kalshi environment; Dopey reaches only Core,
   local Ollama, and the official Robinhood Agentic endpoint. The dedicated
   Ollama process accepts only Dopey loopback traffic and has no external
   egress. DNS-name policy requires a controlled egress proxy because Windows
   executable firewall rules alone do not reliably enforce domain names.
   No service receives a telemetry collector, public dashboard, or hosted
   control-plane endpoint.
8. Before loading the Model Gateway client token, prove that
   `127.0.0.1:8788` is owned by the pinned Model Gateway image under
   `NT SERVICE\DumbMoneyModelGateway`, and apply a service-SID/program-scoped
   inbound firewall rule. Record the PID, TokenUser SID, image path/hash,
   listener owner, and rule digest. Signed readiness alone is not sufficient:
   a local port rebind during its TTL is a blocker.
9. Load secret references through DPAPI or an approved Windows credential
   provider under the service identity. Never put secret values in XML,
   environment files, manifests, logs, readiness descriptors, or command lines.
10. Start Core, Model Gateway, Research Mesh, Dummy, then Dopey.
11. Cryptographically validate every short-lived readiness descriptor.
12. Keep both venue cells in `RECONCILIATION_ONLY` until their independent live
    mechanical gates are complete.

Record a `DeploymentReceiptV1` containing the release digest, service binary and
configuration digests, identities, ACL evidence, firewall/egress evidence,
readiness key ID, boot/restart results, and broker-reconciliation receipts.

## Offline Dummy rollback and no-write drill

This drill needs neither Dopey state nor broker credentials and must make no
network request:

1. In the Dummy checkout, run
   `python -m pytest -q tests/test_dumbmoney_journal_anchor.py
   tests/test_dumbmoney_execution_cycle.py
   tests/test_dumbmoney_windows_service.py
   tests/test_sqlite_operational_journal.py
   tests/test_live_firewall_exposure_truth.py`.
2. In the Blunder checkout, run
   `python -m pytest -q tests/test_dumbmoney_contracts.py
   tests/test_dumbmoney_runtime.py tests/test_dumbmoney_runner.py`.
3. Require proof that an exposure-file rollback presents the prior revision,
   Core rejects a lower sequence or same-sequence conflicting digest, the
   service blocks before broker contact when an anchor fails, and the installed
   execution harness returns zero submissions with no broker write surface.
4. Treat a pass as local control-path evidence only. It does not replace
   target-account read-only validation, an attended broker canary, or
   profitability evidence.

## Backup

Never copy an active SQLite database and its WAL as unrelated files.

1. Request a service-owned online backup or reviewed quiesced maintenance
   backup. It must return a receipt binding the SQLite snapshot, content objects,
   event sequence, release, and fund-lock digest.
2. Verify SQLite integrity on the snapshot, never the only active file.
3. Hash and inventory Core, Dummy, and Dopey journal snapshots plus referenced
   content-addressed objects.
4. Preserve operating mandates, capital envelopes, reconciliations, deployment
   receipts, migration receipts, schemas, and the sealed release.
5. Exclude transient readiness files, caches, temporary research worktrees, and
   plaintext secrets.
6. Back up credential material only through its Windows-supported protected
   export and recovery process, separately from application data.
7. Copy the complete backup set to a new date-stamped destination and verify it
   there.
8. Weekly, restore into a new isolated recovery root and replay to the recorded
   event sequence.
9. Keep backup destinations private and operator-controlled. No public or
   hosted upload is implied or automated by this runbook.

## Restore

1. Leave existing data untouched and restore into
   `C:\ProgramData\DumbMoney\recovery\<receipt-id>`.
2. Validate every manifest, digest, SQLite integrity result, schema version,
   event sequence, and content-object reference.
3. Point an offline recovery configuration at the new root and replay all
   projections.
4. Confirm calculated cash, open orders, positions, reservations, daily loss,
   drawdown, and capital usage against the backup receipt.
5. Start services in `RECONCILIATION_ONLY` with execution disabled.
6. Obtain fresh broker snapshots. Broker truth overrides restored local order or
   position state; ambiguity freezes only the affected venue.
7. Issue a `RestoreReceiptV1`. A restore never recreates an expired mandate or
   capital envelope and never clears `DUMBMONEY_KILL.v1`.

## Update

1. Assemble and validate a new immutable release in a different directory.
2. Confirm forward and backward journal/schema compatibility and complete a
   restore drill.
3. Stop new research admission. Move venue desired modes to
   `RECONCILIATION_ONLY`; wait for all submissions to become accepted,
   rejected, canceled, or explicitly ambiguous.
4. Take and verify a service-owned backup.
5. Stop venue cells, Research, Model Gateway, then Core.
6. Change the service XML and current pointer to the new release while keeping
   the prior immutable release.
7. Start Core, Model Gateway, Research, Dummy, and Dopey.
8. Validate signed readiness, replay, and fresh broker reconciliation.
9. Do not restore `AGGRESSIVE_BOUNDED` automatically. A current operating
   mandate and fresh capital envelope must re-establish it.

## Rollback

Rollback is allowed only when the previous release can read the current journal
schema or a verified restore point predates the incompatible migration.

1. Freeze new exposure and reconcile both venues.
2. Preserve the failed release, logs, journal, readiness descriptors, and crash
   evidence.
3. Stop services in update order.
4. Select the prior immutable release; never copy old databases over current
   broker-derived state.
5. If schemas are incompatible, restore the verified snapshot into a new
   recovery root instead of rewriting current data.
6. Start in `RECONCILIATION_ONLY`, replay, and obtain fresh broker truth.
7. Record a `RollbackReceiptV1` with the cause, source and destination release
   digests, restore receipt if used, unresolved orders or positions, and exact
   authority state.

If cancellation or reconciliation is unavailable, keep the affected venue
frozen and preserve cancel-only capability through the last known-good cell.
