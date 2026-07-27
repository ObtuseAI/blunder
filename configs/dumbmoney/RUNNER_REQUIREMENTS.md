# DumbMoney Core runner requirements

**Deployment boundary:** DumbMoney is a private, local Windows system. The
control plane is never hosted on a public interface, published as a service,
or automatically pushed/published from this runtime.

The production entrypoint is `blunder-fund-core --config
C:\ProgramData\DumbMoney\config\core-runner.v1.json --config-sha256
<release-pinned-sha256>`. Both arguments are public; the config path must be
absolute and its exact bytes must match the pinned digest before parsing. Its public schema is
`schemas/core-runner-config.v1.schema.json`; secret values are forbidden.
The configured Credential Manager target identifiers are exactly:

- `core_signing_seed`
- `desktop_read_token`
- `operator_bearer_token`
- `allocator_bearer_token`
- `dummy_cell_bearer_token`
- `dopey_cell_bearer_token`

The fund subsystem does not auto-generate authority. The supervised runner:

1. Load the core Ed25519 signer from an OS-protected secret provider and pass
   only public keys for the disjoint operator, evaluator, promoter, research,
   venue, and migration roles.
2. Requires absolute data, policy, core-public-key, readiness, sealed fund-lock,
   and sealed service-manifest paths. It hashes the two sealed artifacts and
   refuses startup unless they, the risk policy, and the Core public-key file
   match their release-pinned configuration digests.
   Tokens are loaded only from Credential Manager, hashed in memory, and are
   pairwise distinct.
3. Construct the runtime with `build_fund_application`, then construct the
   listener with `make_loopback_server`. The listener is hard-bound to
   `127.0.0.1`; the runner owns lifecycle, structured redacted logging, Windows
   service integration, backups, and clean shutdown.
   Dummy and Dopey poll `/v1/cells/{cell_id}/commands` using their own token
   and persist the returned `(next_sequence, next_digest)` cursor atomically.
   Every request carries a fresh cryptographically random 32-byte lowercase
   hex `request_nonce`; the cell requires an exact echo in both the page and
   the Core-signed checkpoint. A captured stationary page therefore cannot
   satisfy a later poll.
   Missing, ahead-of-head, or digest-mismatched cursors fail closed. The
   explicit first cursor is sequence `0` with 64 zeroes as its digest.
   Each page includes a Core-signed checkpoint over the request nonce and cursor,
   ordered command identities/digests, next cursor, ledger head, observation
   time, and required action. Cells verify the checkpoint plus every event.
   Historical signatures remain verifiable, but expired/future LIVE,
   kill-clear, and capital grants are `HISTORICAL_ONLY`; historical kill/pause
   events remain fail-closed and generations/revisions stay monotonic.
   Each capital lease contains exactly one strategy, passport, and promotion
   digest, forming a singleton authority tuple. A cell resolves the promotion
   and passport through
   `/v1/cells/{cell_id}/contracts/{body_digest}?capital_envelope_digest={sha256}&request_nonce={64-hex}`.
   Resolution is venue-scoped, limited to those two schemas, and returns the
   original signed envelope and ledger proof inside a nonce-bound Core-signed
   checkpoint bound to the current maximum fencing generation. Resolution is
   serialized with authority appends and re-evaluates the mandate, LIVE mode,
   clear kill generation, policy epoch, singleton capital fence, passport,
   promotion, and every referenced PASS verdict at use time. Later records for
   the same passport lineage, promotion/passport pair, or verdict
   passport/court identity supersede the earlier record, so revocation is
   fail-closed without waiting for an existing lease to expire. Both the
   response and signed checkpoint contain the identical `authority_state`,
   including every authority event digest, the minimum validity deadline, and
   the ledger head at which the decision was linearized. The cell verifies the
   Core checkpoint, sealed evaluator/promoter/research signer roles, current
   time before `authority_valid_until`, and exact
   promotion-to-passport-to-strategy-to-instrument lineage immediately before
   accepting an intent; it never treats a prior resolution as durable
   authority.
4. Treat `/health/live` as process liveness only. `/health/ready` remains
   fail-closed until the signed mandate, kill state, desired modes, current
   reconciliations, account bindings, and deployment receipts resolve.
5. Never pass private keys, bearer tokens, OAuth material, or broker credentials
   through JSON configuration, environment variables, command-line arguments,
   events, prompts, migration manifests, or logs.
6. Continuously process only allocator-signed `CapitalRequestV1` ledger events.
   Each result is idempotently linked by request body digest and derives
   portfolio state only from current per-venue signed risk snapshots. There is
   no unsigned capital-sizing HTTP endpoint.

The runner atomically refreshes a short-lived signed
`dumbmoney.readiness-descriptor.v1` at
`C:\ProgramData\DumbMoney\readiness\DumbMoneyCore.json`. It carries the
loopback endpoint, Core instance, signer identity, sealed artifact digests,
and ledger head—never a bearer token. Production desktop attach uses:

- `DUMBMONEY_CORE_READINESS_PATH`
- `DUMBMONEY_CORE_PUBLIC_KEY_PATH`
- `DUMBMONEY_DESKTOP_TOKEN_TARGET`

The desktop retrieves its read-only token from Credential Manager and may send
it as `X-Blunder-Token` only to `/api/v1/fund/control-snapshot`. Operator and
allocator writes use distinct Bearer tokens. Production is attach-only;
`DUMBMONEY_DESKTOP_DEV_SPAWN=1` may start only an explicitly isolated
development Core with a separate config, pinned config digest, credential
targets, data root, and readiness path. The runner also holds an exclusive
process-lifetime `core.instance.lock` inside its data root, so a second Core
cannot open the same ledger even if a launcher is misconfigured.

Constructing this sidecar does not authorize a broker call. Dummy and Dopey
remain the only venue execution cells and must independently verify every
capital envelope and their local risk limits.
