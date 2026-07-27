# DumbMoney model-gateway service runner

`blunder.fund.model_gateway_entrypoint` turns the existing fail-closed
`OpenRouterModelGateway` into a private Windows service without turning it into
a general model proxy. It binds only `127.0.0.1`, has no broker imports or
broker authority, accepts text completions only, and keeps all three credentials
out of configuration, environment variables, command lines, readiness files,
HTTP responses, and SQLite.

This runner does not install a service, resolve release placeholders, provision
credentials, call a broker, or make an OpenRouter request during startup.
OpenRouter is contacted only after an authenticated, valid completion request
has acquired the gateway's durable reservation.

## Authority and budget boundary

The runner preserves the gateway's immutable UTC-day policy:

| Lane | Daily limit |
|---|---:|
| `research` | 6,000,000 micro-USD (60%) |
| `evaluation` | 2,000,000 micro-USD (20%) |
| `operations` | 2,000,000 micro-USD (20%) |
| **All lanes** | **10,000,000 micro-USD ($10.00)** |

There is no configuration field that can change those allocations. The
readiness descriptor always declares:

```json
{
  "broker": "NONE",
  "mode": "OFFLINE",
  "execution_enabled": false
}
```

The model gateway must run under its own low-privilege Windows service identity.
That identity receives no Dummy, Dopey, Kalshi, Robinhood, account, portfolio,
order, or market-data credentials and no read access to their state roots.

## Three separate protected credentials

The public runner config contains only Windows Credential Manager target names:

| Target purpose | Template target | Required value |
|---|---|---|
| OpenRouter | `credential-target:DumbMoney/OpenRouterApiKey` | Dedicated API key, 16-512 non-whitespace UTF-8 bytes |
| Readiness signer | `credential-target:DumbMoney/ModelGatewaySigner` | Exactly one raw 32-byte Ed25519 private seed |
| Local caller authentication | `credential-target:DumbMoney/ModelGatewayClientToken` | Independent 32-512 character random bearer token |

The signer is distinct from the Core signer. Its public key is stored as
canonical unpadded base64url in
`C:\ProgramData\DumbMoney\model-gateway\keys\gateway-ed25519.pub`. Both that
file's exact byte SHA-256 and the public key derived from the protected seed
must match before the socket is opened.

Provision the credentials using an operator-controlled Windows mechanism that
does not put values in shell history or process arguments. Confirm that they
are readable only by the model-gateway service identity.

## Seal the public config

Start from
`deployment/dumbmoney/model-gateway-runner.v1.template.json`. The tracked
template is deliberately non-runnable: unresolved strings and all-zero digests
are rejected.

Resolve and independently review:

1. the release ID and absolute release paths;
2. exact raw-byte SHA-256 values for the fund lock, service manifest, and
   gateway public-key file;
3. the dedicated OpenRouter key label;
4. one concrete model, provider endpoint, reported provider name, token bound,
   and maximum price map for each lane;
5. an unused loopback port.

The parser rejects unknown or missing keys, duplicate JSON keys, floats,
relative paths, unresolved placeholders, duplicate credential targets,
wildcard/router model identifiers, incomplete lanes, and invalid bounds.

Compute the digest after the final byte is written:

```powershell
$configPath = 'C:\ProgramData\DumbMoney\config\model-gateway-runner.v1.json'
$configSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $configPath).Hash.ToLowerInvariant()
python -m blunder.fund.model_gateway_entrypoint `
  --config $configPath `
  --config-sha256 $configSha256
```

The command accepts no secret-bearing options and has no environment-variable
fallback. Any byte change, including whitespace, invalidates
`--config-sha256`.

The future packaging entry, intentionally not added while Core owns
`pyproject.toml`, is:

```toml
dumbmoney-model-gateway = "blunder.fund.model_gateway_entrypoint:main"
```

## Local API

The HTTP server:

- binds literal `127.0.0.1`, never `0.0.0.0`;
- requires the exact literal `Host` header including the bound port;
- accepts no CORS preflight or browser credential mode;
- caps active handler threads, socket time, content length, prompt characters,
  provider response bytes, and output tokens;
- rejects chunked transfer encoding and non-JSON completion bodies;
- returns `Cache-Control: no-store` and security headers;
- suppresses default request logging;
- marks every response `X-DumbMoney-Retryable: false`.

Endpoints:

| Method and path | Authentication | Purpose |
|---|---|---|
| `GET /health/live` | none | Process/socket liveness only |
| `GET /health/ready` | none | Redacted ready or blocked state |
| `GET /v1/status` | local bearer | Redacted fixed-budget and authority status |
| `POST /v1/completions` | local bearer | One bounded text completion |

The completion request is exact and closed:

```json
{
  "schema": "dumbmoney.model-completion-request.v1",
  "request_id": "research-job-immutable-id",
  "category": "research",
  "messages": [
    {
      "role": "user",
      "content": "Evaluate this de-identified public hypothesis."
    }
  ],
  "reserve_microusd": 10000,
  "max_output_tokens": 200
}
```

The response returns only settlement metadata and validated assistant text. It
does not return routing metadata, key labels, prompts, credentials, or raw
provider accounting. A repeat of an already settled request ID performs no
provider call and returns metadata with `output_available=false`, because
completions are never persisted.

Local bearer authentication prevents the loopback port from becoming an
unauthenticated spending proxy. It is not a substitute for the dedicated
service identity, filesystem ACLs, and outbound firewall restriction to
`https://openrouter.ai`.

## Ambiguous POST rule

The underlying gateway commits `DISPATCHING` before its one OpenRouter POST.
Neither the transport nor runner retries that POST. Timeout, disconnect,
malformed response, routing disagreement, cost disagreement, or restart with
an unresolved dispatch leaves the request `AMBIGUOUS`, pauses the UTC day, and
makes readiness `BLOCKED`.

Do not resend an ambiguous request under a new ID. Preserve the database and
journal for operator reconciliation. Automatic ambiguous-spend release remains
intentionally unsupported.

## Signed readiness

The runner atomically writes a `SignedEnvelopeV1` carrying
`ReadinessDescriptorV1` to the configured readiness path. It refreshes at half
the configured TTL; the contract and parser cap validity at 120 seconds.

Consumers must:

1. pin the model-gateway public key independently from the Core key;
2. verify the Ed25519 signature, signer key ID, event and body digests;
3. enforce the current validity window;
4. require service name `DumbMoneyModelGateway`;
5. require broker `NONE` and `execution_enabled=false`;
6. compare the fund-lock, service-manifest, runner-config, and gateway-key
   digests to their sealed release.

An expired, missing, malformed, mismatched, or blocked descriptor grants no
authority.

## Remaining release wiring

Before installing the WinSW service, the release owner still needs to:

- add the separate signer and local-client targets to the model-gateway service
  manifest secret references and the `gateway_signer_target` /
  `client_bearer_token_target` config bindings;
- add the new config schema, sealed runner config, and public key to the release
  manifest and fund-lock artifact inventory;
- set `DUMBMONEY_MODEL_GATEWAY_COMMAND` to the module command or the future
  console script with the exact config digest;
- apply service-account ACLs and outbound firewall policy;
- run a fake-provider package proof and a supervised dedicated-key attestation
  drill without any broker processes or credentials present.

Until those release artifacts are sealed, this is runnable service code and
hermetic proof—not an installed or activated production service.

## Hermetic validation

The runner test suite injects both the credential provider and OpenRouter
transport. It makes no real model or broker call:

```powershell
python -m unittest tests.test_dumbmoney_model_gateway_runner -v
python -m ruff check blunder/fund/model_gateway_entrypoint.py tests/test_dumbmoney_model_gateway_runner.py
python -m mypy --strict blunder/fund/model_gateway_entrypoint.py tests/test_dumbmoney_model_gateway_runner.py
```

It covers exact config-byte pinning, strict config fields, missing credentials,
signer/public-key mismatch, signed short-lived readiness, broker authority
denial, authenticated loopback completion, fixed budget reporting, durable
settled replay, ambiguous POST non-retry, spoofed Host rejection, and unknown
request-field rejection.
