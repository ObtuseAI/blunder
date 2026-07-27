# DumbMoney model gateway

`blunder.fund.model_gateway` is the only DumbMoney component permitted to hold
an OpenRouter API key. It is a narrow text-completion boundary, not a general
OpenAI-compatible proxy. It has no broker credentials, account data, tools,
plugins, file inputs, streaming, model routers, or fallback routes.

## Budget contract

The gateway has one immutable UTC-day ceiling: **$10.00
(10,000,000 micro-USD)**. The three request categories are fixed:

| Category | Share | Hard daily ceiling |
|---|---:|---:|
| `research` | 60% | 6,000,000 micro-USD |
| `evaluation` | 20% | 2,000,000 micro-USD |
| `operations` | 20% | 2,000,000 micro-USD |

Every request supplies a positive integer micro-USD reservation. Category and
global budget checks count reservations before dispatch. An actual charge may
not exceed its reservation.

OpenRouter remains a second, independent ceiling. Before every POST, the
gateway performs a fresh `GET /api/v1/key` and requires all of:

- the exact dedicated-key label configured locally;
- `limit == 10`;
- `limit_reset == "daily"`;
- `include_byok_in_limit == true`;
- present, non-negative `usage_daily` and `byok_usage_daily` values.

The gateway reconciles consecutive server usage attestations against its local
settlements. Material drift pauses the UTC day. This means the key must be
dedicated to DumbMoney; using it outside this gateway intentionally trips the
drift detector.

## Dispatch and uncertainty

SQLite runs with WAL, `synchronous=FULL`, and `BEGIN IMMEDIATE` transactions.
There is globally one in-flight request:

```text
RESERVED -> DISPATCHING -> SETTLED
                         \-> AMBIGUOUS
```

`DISPATCHING` is committed before the HTTP POST. The POST is made exactly once
and is never retried. A timeout, malformed response, non-success response,
missing cost, usage disagreement, provider/model disagreement, duplicate
generation id, UTC rollover, or restart with an unresolved request converts
the reservation to `AMBIGUOUS` and pauses dispatch. Neither `DISPATCHING` nor
`AMBIGUOUS` is resent on restart.

For a successful response, Decimal `usage.cost` must equal Decimal
`generation.total_cost` from `GET /api/v1/generation?id=...`. The reconciled
cost is conservatively rounded up to integer micro-USD. Reusing a settled
request id returns settlement metadata without making another HTTP call; the
completion itself is not persisted.

New reservations are fenced near UTC midnight so key attestation, dispatch,
and generation reconciliation cannot knowingly straddle budget days.

## Routing and privacy

Each category maps to one operator-reviewed concrete model slug, one concrete
provider endpoint slug, and the provider name OpenRouter reports in router and
generation metadata. A request always sends:

```json
{
  "stream": false,
  "provider": {
    "only": ["THE_EXACT_PROVIDER_ENDPOINT"],
    "order": ["THE_EXACT_PROVIDER_ENDPOINT"],
    "allow_fallbacks": false,
    "require_parameters": true,
    "data_collection": "deny",
    "zdr": true,
    "max_price": {
      "prompt": 1,
      "completion": 2
    }
  }
}
```

The values above illustrate the shape only. Release configuration must use
currently verified model/provider slugs and deliberate price ceilings. The
gateway rejects router/wildcard/fallback identifiers.

The request opts into router metadata and disables OpenRouter response caching.
Provider-side implicit in-memory prompt caching can still exist on eligible
ZDR endpoints, so ZDR and the response-cache header are not a sensitive-data
boundary.
Settlement requires a direct strategy, one attempt, one selected endpoint whose
model/provider match the allowlist, and matching generation `provider_name`.
Any reported plugin, file-parser, web-search, or server-tool pipeline stage
pauses the gateway.

The typed request accepts only `system`, `user`, and `assistant` messages with
string content and a bounded `max_output_tokens`. A defense-in-depth content
filter rejects known broker/account/credential phrases, common vendor key
shapes, PEM/JWT/hex key shapes, and mixed-alphabet high-entropy tokens. This
heuristic cannot prove that a prompt is non-sensitive and must never be treated
as a data-loss-prevention boundary.

The primary boundary is process and data separation: run the model gateway as
its own Windows service identity; give that identity only its dedicated
OpenRouter secret and gateway state directory; never provide it the Dummy or
Dopey service environments, venue credential stores, broker databases, account
snapshots, order journals, or unrestricted paths containing those artifacts.
Research callers must send de-identified derived features, not raw venue or
account records.

Prompts, completions, API keys, and account data are never written to SQLite or
the state journal. Journal entries contain canonical SHA-256 digests, state,
integer cost, UTC timestamps, and a hash-chain link. Generation ids are needed
for durable idempotency in the request table; the journal contains only their
digest.

## Construction

Production uses `StdlibHttpsTransport`, which permits HTTPS requests only to
`openrouter.ai`, refuses cross-origin redirects, bounds response bytes, and
does not retry. The API secret is supplied through an injected callable.
`WindowsCredentialSecretLoader("DumbMoney/OpenRouter")` reads a generic
Windows Credential Manager entry under the dedicated service identity; the key
is loaded only after a durable reservation exists. Environment-variable,
command-line, and JSON-file secret loading are not production-supported.

The service should construct `GatewayConfig` with all three
`BudgetCategory` routes, instantiate `OpenRouterModelGateway`, and expose a
loopback-only application protocol. Do not expose the class as an unauthenticated
HTTP proxy.

Signed readiness does not by itself reserve the advertised loopback port. A
local process could rebind that port during the readiness TTL and receive the
client bearer token. Production activation therefore also requires target-side
evidence that `127.0.0.1:8788` is owned by the pinned Model Gateway image running
as `NT SERVICE\DumbMoneyModelGateway`, plus a service-SID/program-scoped inbound
firewall rule. Research verifies readiness and process identity before and after
one non-retried request, but those checks do not replace the OS ownership proof.

There is deliberately no automatic ambiguous-spend release. Recovery requires
an operator-reviewed reconciliation tool that can prove the reservation's
provider outcome before changing durable state. Until that tool exists, an
ambiguous request remains a fail-closed stop.

## Core status adapter

`OpenRouterModelGateway.redacted_control_snapshot(at=None)` returns a read-only
mapping that Core can place under its `openrouter` snapshot key. Its exact
schema id is `dumbmoney.openrouter-budget-status.v1`. It contains:

- UTC day and reset timestamp;
- fixed daily limit in micro-USD and cents;
- spent, reserved, committed, and remaining budget;
- conservative cents projections for the existing desktop fields;
- pause code and unresolved count;
- each category's fixed limit and committed micro-USD.

It intentionally omits request/generation identifiers, prompts, completions,
key label, model/provider route, secret values, and raw errors. In particular,
the compatibility fields are `daily_budget_cents`, `spent_cents`,
`remaining_cents`, `resets_at`, and `research_paused`.

## Validation

The hermetic suite uses an injected fake transport and makes no network calls:

```powershell
python -m unittest tests.test_dumbmoney_model_gateway -v
```

It covers the hard allocations, strict routing payload, dedicated-key
attestation, one-POST behavior, restart idempotency, cost reconciliation,
server-usage drift, sensitive prompt rejection, midnight fencing, full SQLite
durability, and journal data minimization.

OpenRouter contracts used by this implementation:

- [Authentication](https://openrouter.ai/docs/api_reference/authentication)
- [Key limits and daily usage](https://openrouter.ai/docs/api_reference/limits)
- [Usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting)
- [Provider selection](https://openrouter.ai/docs/guides/routing/provider-selection)
- [Router metadata](https://openrouter.ai/docs/guides/features/router-metadata)
- [Zero Data Retention](https://openrouter.ai/docs/guides/features/zdr)
- [Errors and debugging](https://openrouter.ai/docs/api_reference/errors-and-debugging)
