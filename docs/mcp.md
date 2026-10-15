# AI quota checks through MCP

The application serves the official MCP Python SDK's Streamable HTTP transport
at `/mcp`, in the same process as the REST API and collector. It supports legacy
initialization as well as the SDK's current protocol. There is no second database
or polling loop. MCP is opt-in and uses its own bearer token; widget credentials
cannot access MCP, and MCP credentials cannot access REST.

## Enable on the server

Create a random token in the dedicated secrets directory without printing it:

```bash
python - <<'PY'
import os, secrets
fd = os.open('secrets/mcp-api-token', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write(secrets.token_urlsafe(32) + '\n')
PY
```

Configure `.env` (replace the hostname with the one clients actually use):

```dotenv
MCP_ENABLED=true
MCP_API_TOKEN_FILE=/run/secrets/mcp-api-token
MCP_ALLOWED_HOSTS=["localhost:*","127.0.0.1:*","usage.example.ts.net"]
MCP_ALLOWED_ORIGINS=[]
```

Rebuild and restart with `docker compose up -d --build`. Keep the existing private
network binding or TLS reverse proxy. For a Tailscale IP URL with a port, include
that exact `IP:port` in the allowlist. Empty origins allow non-browser clients but
reject requests carrying an Origin header. Add exact trusted browser origins only
when needed; this does not enable CORS. Do not turn off host/origin validation.

## Connect an AI client

Use the private server URL plus `/mcp`, **Streamable HTTP**, and
`Authorization: Bearer <MCP token>`. Obtain the token from the protected server
file through your existing secure SSH/secret-management channel. Do not put it in
a repository or use provider OAuth credentials here. Restart after token rotation.

Example client configuration for clients that support HTTP headers and environment
interpolation (the exact interpolation syntax depends on the client):

```json
{
  "mcpServers": {
    "ai-usage-monitor": {
      "type": "http",
      "url": "https://usage.example.ts.net/mcp",
      "headers": {"Authorization": "Bearer ${AI_USAGE_MCP_TOKEN}"}
    }
  }
}
```

This private service uses pre-shared bearer authentication, not OAuth discovery.
Clients that require OAuth-only remote connectors need an authentication gateway.
Clients must be able to reach the private network; adding MCP does not make the
server reachable from cloud-hosted AIs automatically.

## Tools and decision rules

| Tool | Purpose |
|---|---|
| `get_usage_limits(provider?, max_age_seconds=300)` | Cached quotas, reset times, pacing, per-provider age and decision blockers |
| `check_usage_budget(provider, minimum_remaining_percent=10, max_age_seconds=300)` | Conservative reserve check across **all reported** canonical and model-specific windows |
| `refresh_usage()` | One shared, throttled collection; returns `retry_after_seconds` when throttled |

Provider values are `claude` and `openai` (Codex). Returned percentages count down
to zero. Null is unknown, never an unlimited balance. `check_usage_budget` returns
`unknown` for unavailable/stale/old data or absent windows, `insufficient` when any
reported window is at or below the reserve, and `available` otherwise. It cannot
estimate a task's cost, reserve quota, guarantee future availability, or account
for limits the provider does not report. Checking all model-specific windows is
deliberately conservative; inspect individual windows when choosing a model.

Suggested agent instruction:

> Before a large task, check get_usage_limits. Use samples only when
> usable_for_decisions is true. Consider every window applicable to the selected
> model. Keep a remaining-quota reserve. If a sample is old, refresh once and
> respect retry_after_seconds. If data remains unknown, do not assume capacity.

The client age limit cannot override the server's stale policy. With the default
10-minute collection interval, a 5-minute maximum age will deliberately reject
older samples; set an appropriate client age or poll interval for your workflow.
Tool calls do not generate model requests. Manual refresh and REST refresh share
the same limiter, so many AI clients cannot each start their own polling loop.

## Monitoring and security

`GET /api/v1/health` remains public infrastructure liveness. Authenticated
`GET /api/v1/ready` returns 503 when an enabled provider has unusable quota data.
Full and compact responses now include `stale_reasons` and compact includes
`last_attempt_at`. Reasons distinguish `reset_elapsed`, `sample_too_old`,
`collection_failed`, `not_collected`, `cached_data_invalid`, and `clock_skew`.
An elapsed reset does not prove that collection stopped or authentication expired.
Keep authentication alerts separate from quota-readiness alerts.

MCP authenticates before protocol dispatch, limits request bodies to 64 KiB,
validates hosts/origins, returns no-store responses, and uses stateless transport.
Collection events carry a cycle ID, duration, attempts and success/failure totals.
Failures and retries use warning/error severity and contain sanitized error codes.
Compose rotates logs (three 10 MiB files), reaps child processes, and caps CPU,
memory, and process count. Increase those limits deliberately if other operational
CLI work inside the container requires more resources.

Implementation follows the [official SDK ASGI integration](https://py.sdk.modelcontextprotocol.io/run/asgi/).
