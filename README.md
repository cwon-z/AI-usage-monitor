# AI Usage Monitor

A small self-hosted FastAPI backend for Claude and OpenAI/Codex subscription quotas. Android, Tasker/KWGT, and dashboards receive only normalized usage and use a separate widget API token.

AI clients can query remaining quota through the optional authenticated
[MCP server](docs/mcp.md), using a dedicated token and the same cached data.

Experimental, single-user software. Keep the running API private even if the
source is public. Read [security boundaries](SECURITY.md),
[contributing/checks](CONTRIBUTING.md), and the latest
[security audit](docs/security-audit-2026-11-26.md).

## Architecture and interfaces

```text
Claude OAuth usage GET ──┐
                        ├─ adapters ─ async collector ─ SQLite ─ authenticated API
Codex app-server ───────┘                 every 10 min
```

Widget reads never query providers. The collector persists snapshots, prunes history after 30 days by default, retries transient provider errors, and retains successful values after failures. Concurrent refresh requests share the active collection cycle. Storage errors do not stop subsequent scheduled cycles.

- **Claude:** a read-only GET to the **undocumented** OAuth usage endpoint used by Claude Code 2.1.266, verified against the installed CLI and live subscription data. Its response is isolated in [the Claude adapter](src/ai_usage_monitor/providers/claude.py). Collection does not launch Claude Code, generate model requests, follow redirects, use environment proxies, or refresh credentials. The CLI's experimental control channel returned null quota data under isolated authentication, so it is not used. The image includes the pinned Claude CLI for diagnostics only.
- **Codex:** the [documented app-server interface](https://learn.chatgpt.com/docs/app-server), specifically `account/rateLimits/read`. A private temporary `CODEX_HOME` receives only access/ID tokens, account ID, an empty refresh-token field, and fresh cache metadata. An isolated working directory and allowlisted environment prevent inherited auth/routing overrides. No prompt or model turn is submitted.
- Credentials are read server-side only, and the adapters strip refresh credentials before any child process or request. Expired access tokens fail closed; the monitor itself never renews a session. Renewal, where you want it, is delegated to the provider's own CLI holding its own login — see [Durable alternative](#durable-alternative-give-the-deployment-its-own-login).
- Claude's five-hour window is exposed as `session`; `capabilities.five_hour` means that session has a known five-hour duration. Codex's `five_hour` and `weekly` both refer to the canonical bucket, never a mixture of model buckets. Model-specific Codex limits remain labeled under `additional_windows`. Missing quotas stay null.
- Claude consumed extra-usage credits are **not** a remaining balance; Claude `credits` is null. Codex balances remain decimal strings in the units reported by Codex, not an inferred currency.

## Authentication: read this before deploying

Use an existing **subscription** login, not a platform API key. Claude requires profile-capable OAuth credentials from its normal login. **Do not use `claude setup-token` for this service:** those tokens only support model requests, as described in [Anthropic's authentication documentation](https://code.claude.com/docs/en/authentication#generate-a-long-lived-token).

The export utility reads your existing authenticated state and creates narrow, access-token-only snapshots. It does not run login/logout, regenerate credentials, or write to the source files/Keychain. Default sources are:

- Claude on Linux: `~/.claude/.credentials.json`.
- Claude on macOS: explicitly select the Keychain service below. Custom Claude configuration directories may use a different service name.
- Codex: `~/.codex/auth.json` from an existing ChatGPT login. Keyring-only Codex storage is not automatically extracted; supply a supported auth file.

**Access-token snapshots expire, and can die before their stated expiry.** When the source CLI renews its normal session, rerun the export with `--replace-provider-snapshots` and securely transfer the updated snapshots to the homelab if needed. Directory mounting means atomic replacements are picked up at the next poll without restarting. Re-exporting an expired source does not renew it: use your normal provider client to restore its session first. This operational requirement is intentional to preserve read-only authentication.

Do not rely on `expiresAt` as the deadline. Refreshing **rotates** the token, so the moment the source CLI renews, every exported copy of the previous access token stops working — in practice a snapshot can fail hours early, with no warning beyond the provider's `status` going to `error`. Snapshots suit a short-lived or supervised deployment. For anything unattended, prefer the durable path below.

### Durable alternative: give the deployment its own login

Instead of copying your credentials, let the deployment hold an **independent grant**. Log in once inside the running container:

```bash
docker compose exec ai-usage-monitor claude auth login --claudeai
```

then point the adapter at that credential file rather than the export:

```bash
CLAUDE_CREDENTIALS_FILE=/home/app/.claude/.credentials.json
```

This needs a persistent home so the login survives container recreation; `docker-compose.override.yml.example` shows the named volume (`app-home` at `/home/app`). The container's root filesystem is read-only, so without that volume the login fails outright rather than working until the next recreation. Claude Code writes both `~/.claude/.credentials.json` and `~/.claude.json`, so the whole home is persisted, not just `.claude/`. No adapter change is involved: the file has the same shape as an export, and refresh credentials are still stripped before any request.

Renewal has to be driven deliberately, because of how the pinned CLI behaves. Measured against Claude Code 2.1.266:

| Situation | Result |
|---|---|
| `claude auth status` | **Never refreshes** — reports the stored file only |
| Authenticated call, ~30 min remaining | Declined |
| Authenticated call, ~5 min remaining | Refreshed, ~8h granted |
| Authenticated call, already expired | Refreshed, ~8h granted |

So `scripts/refresh-claude-auth.sh` reads the expiry cheaply (no quota, no request) and only inside that window issues one trivial Haiku prompt purely as a refresh trigger — one or two calls per 8-hour cycle. Run it from cron on the deployment host:

```cron
*/5 * * * * /srv/data/ai-usage-monitor/scripts/refresh-claude-auth.sh >> /srv/data/ai-usage-monitor/refresh-auth.log 2>&1
```

It stays silent while healthy, so the log holds only refreshes and failures. `THRESHOLD` overrides the 600-second window when testing.

**Never copy a refresh token to a second machine.** Rotation means two holders of one grant will fight: whichever refreshes second presents a superseded token and is locked out — including, if you copy from your workstation, your own CLI. Give each deployment its own login. `refreshTokenExpiresAt` runs about 30 days and is renewed on each rotation, so only a deployment offline longer than that needs an interactive login again.

A grant can also be revoked upstream (signing out of all sessions, for example). The access token then returns 401 hours before its `expiresAt`, and the next refresh attempt is rejected, after which the CLI blanks the credential file. The script reports that as `logged out` rather than a refresh failure; the only fix is another interactive login.

**Codex** works the same way. Log in once with device authorization, which needs no browser on the server, and point the adapter at the file it writes into the same persisted home:

```bash
docker compose exec ai-usage-monitor codex login --device-auth
CODEX_AUTH_FILE=/home/app/.codex/auth.json
```

The adapter still copies only the access and ID tokens into a private runtime home with an empty refresh token and fresh metadata, so collection never rotates the grant. Measured against Codex CLI 0.150.1:

| Situation | Result |
|---|---|
| `codex login status` | **Never refreshes** |
| `account/rateLimits/read`, even with `last_refresh` 9 days old | **Never refreshes** |
| app-server `account/read` with `refreshToken: true` | Refreshed **unconditionally**, both tokens rotated, ~10-day access token |

Nothing renews the ~10-day access token on its own, so `scripts/refresh-codex-auth.sh` reads its expiry locally and only under three days remaining makes that one explicit refresh call — no model turn, no quota:

```cron
23 * * * * /srv/data/ai-usage-monitor/scripts/refresh-codex-auth.sh >> /srv/data/ai-usage-monitor/refresh-auth.log 2>&1
```

Exported Codex snapshots carry no refresh token at all and still expire after roughly ten days; use them only where the durable path is not possible.

### Alerting and fast re-login

Renewal cannot survive a revoked grant, and nothing in the service reports one: `/api/v1/health` stays `ok`, the container stays healthy, and `provider_auth_expired` logs at INFO. `scripts/auth-watchdog.py` closes that gap. From cron on the deployment host, it reads `/usage/compact` with the widget token, which never leaves the host, and pushes up/down to an [Uptime Kuma](https://github.com/louislam/uptime-kuma) push monitor. Kuma then handles notification, and marks the monitor down on its own if the pushes stop:

```cron
*/5 * * * * /srv/data/ai-usage-monitor/scripts/auth-watchdog.py >> /srv/data/ai-usage-monitor/watchdog.log 2>&1
```

It needs `state/kuma-push-url` and, optionally, `state/discord-webhook` (both mode 600, ignored by Git). A provider counts as down when its error is `authentication_*`, or when it is stale for any other reason.

The watchdog calls the API on the base Compose file's loopback port, `http://127.0.0.1:8000/api/v1`. If your deployment publishes a different address, for example through the override file, put its base URL in `state/api-url` (or `AI_USAGE_API`), or the watchdog will report the API unreachable. The widget token is sent there, so use `https` unless the address is loopback or inside an encrypted private network. `state/ssh-host` (or `AI_USAGE_SSH_HOST`) sets the host name shown in the re-login commands; it defaults to the machine's hostname. Only `http(s)` URLs are opened, and the Discord webhook must be `https`.

When the Codex login is dead, the watchdog also starts a device login and posts the link and code to Discord, so re-login is one approval from a phone. Without a webhook, the code rides in the Kuma alert instead. A fresh code is offered every 3 hours (`CODEX_LOGIN_REOFFER_SECONDS`) while the login stays dead, or on demand with `--codex-login`. The login runs in a staging `CODEX_HOME` and replaces the live `auth.json` only once approved, because `codex login` deletes the existing file the moment it starts. Logging in in place would destroy a working grant on a false alarm. On approval it requests an immediate collection.

Claude has no device flow, so its alert carries one command instead: `ssh -t <host> …/scripts/claude-login.sh`, which runs the login and then `auth-watchdog.py --kick` to collect at once. `--test-alert` sends a labelled DOWN/UP pair through Kuma and a test post through the webhook.

## Docker deployment

Requires Docker with Compose v2; Python 3.12+ and uv on the trusted machine used to export credentials. Do not run the exporter as root.

1. Install the project and export narrow secrets on your authenticated machine:

   ```bash
   uv sync --frozen
   # Linux:
   uv run --frozen ai-usage-monitor-secrets --output-dir ./secrets

   # macOS (instead of the command above):
   uv run --frozen ai-usage-monitor-secrets --output-dir ./secrets \
     --claude-keychain-service 'Claude Code-credentials'
   ```

   The exporter creates a directory with mode 0700 and three mode-0600 files:
   `widget-api-token`, `claude-credentials.json`, and `codex-auth.json`.
   It never prints credential values. Existing exports require explicit
   `--replace-provider-snapshots`; the widget token is preserved.

   To disable a provider, export with `--skip-claude` or `--skip-codex` and set its `*_ENABLED=false` below. No dummy provider file is required.

2. On the deployment host:

   ```bash
   cp .env.example .env
   chmod 600 .env
   id -u
   id -g
   ```

   Set `APP_UID` and `APP_GID` in `.env` to the **non-root owner of the secrets directory on the Linux deployment host**. Typical values are 1000, but use the actual IDs. These are image build arguments, so rebuild after changing them. The default is 10001. For Docker Desktop/Colima, host-share permission translation differs; verify readability with the preflight below.

   If exporting on another computer, transfer only the narrow `secrets/` directory securely and set its ownership for the destination operator. Do not copy complete provider homes, refresh-token files, or your macOS Keychain. Never make secret files world-readable.

3. Build and run a credential-readability preflight:

   ```bash
   docker compose build
   docker compose run --rm --no-deps ai-usage-monitor python -m ai_usage_monitor.preflight
   docker compose up -d
   docker compose ps
   docker compose logs --tail=100 ai-usage-monitor
   ```

   Preflight checks the widget token, SQLite connectivity, and enabled providers' credential shape/scope/expiry without a provider network request. All must pass. An absent secret directory fails the bind mount immediately rather than being silently created.

4. Verify health and usage:

   ```bash
   curl --fail http://localhost:8000/api/v1/health
   # Read only the backend widget token on this trusted machine:
   export WIDGET_API_TOKEN="$(cat secrets/widget-api-token)"
   curl --fail -H "Authorization: Bearer $WIDGET_API_TOKEN" \
     http://localhost:8000/api/v1/usage/compact
   curl -X POST -H "Authorization: Bearer $WIDGET_API_TOKEN" \
     http://localhost:8000/api/v1/usage/refresh
   unset WIDGET_API_TOKEN
   ```

   Initial collection is asynchronous. Check each enabled provider's `status`, `error`, and `collected_at`; HTTP 200 from a cached read alone does not prove provider authentication works.

Compose publishes only `127.0.0.1:8000`, drops all capabilities, sets `no-new-privileges`, mounts the root filesystem read-only with a bounded temporary filesystem, and persists SQLite in a named volume. The image carries the Python runtime, Node and the two pinned CLIs, without pip or npm. It mounts only the dedicated export directory read-only, never your home. Use Tailscale or a carefully configured TLS reverse proxy for phone access; public exposure is not configured.

**Existing volume ownership:** if you change runtime UID/GID on a previously deployed instance, migrate ownership of that deployment's data volume before starting the new image. Back it up first. Do not delete the volume to fix permissions.

## Configuration

| Variable | Default / meaning |
|---|---|
| `WIDGET_API_TOKEN_FILE` | Compose: `/run/secrets/widget-api-token`; preferred |
| `WIDGET_API_TOKEN` | Alternative direct token, minimum 16 characters; takes precedence over file |
| `DATABASE_URL` | Local: `sqlite:///./data/usage.db`; Compose: `sqlite:////data/usage.db` |
| `COLLECTION_INTERVAL_SECONDS` | 600 |
| `RETENTION_DAYS` | 30 |
| `STALE_AFTER_SECONDS` | 1800; reset expiration also makes data stale |
| `MANUAL_REFRESH_MIN_INTERVAL_SECONDS` | 60, per-process monotonic throttle |
| `PROVIDER_TIMEOUT_SECONDS` | 20; bounded subprocess cleanup can add up to 2 seconds |
| `PROVIDER_RETRY_ATTEMPTS` | 3 |
| `PROVIDER_RETRY_BASE_SECONDS` | 0.5, exponential backoff |
| `CLAUDE_ENABLED` / `CODEX_ENABLED` | true |
| `CLAUDE_CREDENTIALS_FILE` | Claude credential/export JSON. Narrow export (`/run/secrets/...`) or the deployment's own login (`/home/app/.claude/.credentials.json`) |
| `CODEX_AUTH_FILE` | Local Codex auth/export JSON; Compose uses narrow export |
| `CLAUDE_OAUTH_TOKEN_FILE` / `CLAUDE_OAUTH_TOKEN` | Advanced raw access-token alternatives; must already have profile and inference scopes |
| `CLAUDE_OAUTH_SCOPES` | `user:profile user:inference`; declaring scopes does not grant them |
| `CODEX_CLI_PATH` | `codex` |
| `LOG_LEVEL` | INFO; HTTP library traces remain suppressed |
| `APP_UID` / `APP_GID` | Docker build-time non-root identity, default 10001 |
| `SECRETS_DIR` | Compose host directory, default `./secrets` |

Use **one application worker/replica per database**. Collection scheduling and manual-refresh throttling are in-process, not a distributed lock.

## API contract

Every `/api/v1` endpoint except health requires `Authorization: Bearer <widget-token>`.
MCP uses its own dedicated token. Responses are `Cache-Control: no-store`;
interactive docs and unauthenticated OpenAPI are disabled.

| Endpoint | Behavior |
|---|---|
| `GET /api/v1/usage` | Full normalized cached state, capabilities, source windows and pacing |
| `GET /api/v1/usage/compact` | Rounded **remaining** percentages, reset countdowns, per-provider timestamps/errors |
| `GET /api/v1/usage/history` | Snapshot history with filters and cursor pagination |
| `GET /api/v1/providers` | Enabled/configured state and interface classification; no paths |
| `POST /api/v1/usage/refresh` | 200 on all successes, 207 on provider failures, 429 with Retry-After when throttled, 503 for storage failure |
| `GET /api/v1/health` | Public infrastructure health; 503 for database/scheduler failure |
| `GET /api/v1/ready` | Authenticated quota readiness; 503 when an enabled provider is unavailable or stale |
| `/mcp` | Optional Streamable HTTP MCP; separate bearer token, see [setup](docs/mcp.md) |

Quota fields include `used_percent`, `remaining_percent`, `reset_at`, `window_minutes`, `source`, and optional `pace`. Utilization may exceed 100 if upstream reports an overage; remaining percentage is clamped at zero. All timestamps are timezone-aware UTC.

**Compact percentages are quota remaining, not quota spent.** `session`, `weekly`, and `five_hour` on `/usage/compact` are `remaining_percent` rounded to a whole number, so they count **down** toward 0 as the window is consumed and are clamped at 0 during an overage. The full `/usage` endpoint reports both directions per window; read `used_percent` there if you want consumption instead.

Full provider results include `status` (ok/error/unavailable), `stale`, `error`, `collected_at` (last successful sample), and `last_attempt_at`. Never-collected timestamps are null. Disabled providers have `error: disabled`, null data, and do not make aggregate stale true.

`updated_at` is the latest successful collection across enabled providers, not the latest failed poll. It is null before any success. Use **per-provider** `collected_at` to display each sample's age. Failed/aged/reset-expired samples remain available but stale. Stale samples have no pace projection.

Example compact response (illustrative, not live data):

```json
{
  "claude": {
    "status": "ok", "stale": false, "error": null,
    "session": 63, "weekly": 39, "five_hour": null,
    "session_reset_in": "2h 11m", "weekly_reset_in": "3d 7h",
    "five_hour_reset_in": null, "collected_at": "2026-09-10T00:00:00Z"
  },
  "openai": {
    "status": "ok", "stale": false, "error": null,
    "session": null, "five_hour": 82, "weekly": 74,
    "session_reset_in": null, "five_hour_reset_in": "4h 13m",
    "weekly_reset_in": "5d 2h", "collected_at": "2026-09-10T00:00:00Z"
  },
  "updated_at": "2026-09-10T00:00:00Z", "stale": false
}
```

History supports `provider`, `quota_type`, timezone-aware `since`, and `limit` (1–1000). Pass the returned `next_before_id` as `before_id` for the next page until null. Rows are newest insertion first with stable unique IDs; keep the same filters across pages. Canonical windows also appear as labeled additional windows in Codex history: filter `quota_type` instead of summing aliases.

```bash
curl -H "Authorization: Bearer $WIDGET_API_TOKEN" \
  'http://localhost:8000/api/v1/usage/history?provider=claude&quota_type=session&limit=100'
curl -H "Authorization: Bearer $WIDGET_API_TOKEN" http://localhost:8000/api/v1/providers
```

## Pacing

For known duration/reset windows, infer start = reset − duration and compare usage with elapsed time **at collection**. A ±5-point difference is on_pace; otherwise under_budget or over_budget. Linear exhaustion projection is descriptive, not a forecast. It does not silently change as a cached sample ages and is omitted for stale or expired windows.

## Tasker / KWGT and future Android

**Importable widget:** [AI 사용량 — Quota Panel 3×2 R7](widgets/kwgt/AI_Usage_OneUI_3x2_R7.kwgt)
is a standalone KWGT preset with built-in authenticated fetching; Tasker is not
required. See [phone setup, requirements, and verification limits](widgets/kwgt/README.md).
It ships without credentials; configure the HTTPS server origin and widget token
on your phone. The manual Tasker alternative remains documented below.

For the supplied dark Korean reference style, use [AI 사용량 — Quota Panel 3×2 R7](widgets/kwgt/AI_Usage_OneUI_3x2_R7.kwgt):
compact provider rows, a secondary-quota panel, native vector marks, bundled Korean fonts, and a full-space squircle-corner card. Resize the KWGT
home-screen container to three columns by two rows before loading it.

Every 10 minutes: Tasker HTTP GETs the compact endpoint with the widget bearer token, parses JSON, updates globals, and sends them to KWGT.

| Tasker global | JSON path |
|---|---|
| `%CLAUDE_SESSION` | `claude.session` |
| `%CLAUDE_WEEKLY` | `claude.weekly` |
| `%CLAUDE_SESSION_RESET` | `claude.session_reset_in` |
| `%CLAUDE_WEEKLY_RESET` | `claude.weekly_reset_in` |
| `%GPT_5H` | `openai.five_hour` |
| `%GPT_WEEKLY` | `openai.weekly` |
| `%GPT_5H_RESET` | `openai.five_hour_reset_in` |
| `%GPT_WEEKLY_RESET` | `openai.weekly_reset_in` |

These values are **remaining** quota: 100 is a fresh window and 0 is exhausted, so a gauge or bar should fill as the number falls. Handle nulls as unavailable, not zero. Show a stale warning and the last sample's age when collection fails. Store only the widget token on Android. A future native widget can use this same contract and WorkManager; no native Android app is included.

## Operations and troubleshooting

- `authentication_missing`: check the mounted file and UID/GID using preflight.
- `authentication_invalid`: credential shape is unsupported or corrupt; re-export the correct subscription session.
- `authentication_expired`: export a fresh access-token snapshot after the normal client has renewed its session. Recurring daily means the snapshot is being rotated out from under you — move that provider to its own login instead. Note this is logged at `info`, and neither container health nor `/api/v1/health` covers provider auth, so grepping logs for error levels will report a false all-clear; check each provider's `status` or run `python -m ai_usage_monitor.preflight`.
- `authentication_scope_missing`: use a profile-capable Claude login, not an inference-only setup token. A 403 may also indicate account policy restrictions.
- `upstream_error` / `timeout`: bounded retries and future scheduled cycles continue; cached data remains available. Raw upstream errors and headers are not exposed.
- `cli_unavailable`: verify Codex is installed/executable inside the runtime.
- `storage_unavailable` / health 503: check disk space, volume permissions, and SQLite access. The next scheduled cycle retries. Docker's unhealthy state alone does not restart a container.
- Missing Codex five-hour data can be legitimate. Inspect labeled `additional_windows`; the canonical account bucket may only report weekly data.

To update snapshots, rerun the export command with `--replace-provider-snapshots`. To rotate the widget token, replace its file securely and restart the service: the backend token is loaded at startup. If credentials are exposed, revoke them through the provider's normal security controls.

A newly created database, its WAL files and a newly created data directory are readable by their owner only; existing ones keep their modes. Back up SQLite with its online backup API or stop the service before copying the database and associated WAL files. Pruning deletes old snapshots, not the last successful cached provider state. If you need to erase retained account data, remove it through a deliberate database maintenance operation after backup.

## Development and verification

```bash
uv sync --frozen --extra dev
uv run --frozen pytest
```

For a local server, use the exported secrets with local paths; do not reuse Compose's /run/secrets paths:

```bash
WIDGET_API_TOKEN_FILE=./secrets/widget-api-token \
CLAUDE_CREDENTIALS_FILE=./secrets/claude-credentials.json \
CODEX_AUTH_FILE=./secrets/codex-auth.json \
DATABASE_URL=sqlite:///./data/usage.db \
uv run --frozen uvicorn ai_usage_monitor.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

The default local credential file paths can also be read directly: the adapters strip refresh credentials before any child/request. Prefer narrow exports in deployment. The service does not auto-read macOS Keychain; the explicit exporter does.

The test suite covers normal and malformed payloads, real subprocess pipes/deadlines, missing/expired credentials, no-refresh export, secret permissions, SQLite threading/history pagination, scheduler recovery, refresh coalescing, cache semantics, authentication, health, and sanitized storage failures. Network calls use mocks in automated tests.

Runtime dependencies are frozen in `uv.lock` and exported, with hashes, to `requirements.lock`; Docker installs exactly those versions and rejects any download whose hash does not match. After an intentional dependency update, regenerate both:

```bash
uv lock
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file requirements.lock
uv sync --frozen --extra dev
uv run --frozen pytest
docker compose build
```

To update provider integrations: pin the new CLI version, compare Codex's generated app-server schema and Claude's current usage response, update only the affected adapter, add sanitized fixtures, rerun the tests, and verify read-only live collection. Treat both credential formats and Claude's endpoint as compatibility boundaries, not permanent contracts.
