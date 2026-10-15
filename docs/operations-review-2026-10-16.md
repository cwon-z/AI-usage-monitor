# Operations review — 2026-10-16

## Evidence from the deployed service

The sampled seven-day application log contained 3,339 collection cycles, with
3,339 successes for each provider and no recorded terminal provider failures.
This establishes successful final outcomes, not absence of intermediate retries:
the previous implementation did not log retries. The live interval was 180 seconds.

The host watchdog nevertheless repeatedly reported Claude as having had no
collection for 30 minutes. Its assessment translated every `stale` flag to that
message, even though the API also marks samples stale when a quota reset expires.
Thus that alert text was not reliable evidence of a stalled collector or dead login.

The service used unbounded Docker json-file logs and had no configured memory,
CPU, or process ceiling. Successful provider collection also produced a duplicate
snapshot event, while failures used INFO severity. There were no cycle identifiers,
attempt counts, or timings to explain slow cycles and recovered failures.

Deployment-specific credential renewal and watchdog scripts exist outside the
repository. The running service uses a persistent CLI home for independent server
logins. Those operational integrations were retained. The monitor itself still
never refreshes provider credentials or submits model requests.

## Changes

- Added optional `/mcp` using the official SDK, a separate bearer token, host and
  origin checks, stateless HTTP, and a 64 KiB body limit. Reads and refreshes use
  the same application service, repository, and refresh limiter as REST.
- Added quota and conservative budget tools that explicitly report sample age,
  uncertainty, reset times, and decision eligibility. Unavailable or stale data
  does not become an affirmative capacity decision.
- Added `stale_reasons`, compact `last_attempt_at`, and authenticated `/api/v1/ready`.
  Infrastructure liveness remains independent of provider readiness.
- Corrected the deployed watchdog's assessment: reset-only stale data after a
  successful collection means quota reset pending, not dead authentication. Other
  failures remain unhealthy; authentication failures retain their existing flow.
  Assessment tests were run without invoking notification/login functions.
- Added cycle IDs, duration/attempt counts, retry warnings, terminal failure
  warnings, storage-error severity, and completion totals; removed duplicate
  snapshot success events.
- Bounded Docker logs to three 10 MiB files, memory to 768 MiB, CPU to one core,
  and processes to 128; enabled init for child reaping.
- Fixed Windows secret reads/export reporting and subprocess cleanup portability.
  Made widget packaging use stable ZIP platform metadata and normalized license
  text; reproducibility checks compare exact payload/metadata across zlib versions
  and exact bytes for repeated builds on one runtime.

## Verification

- Windows: 170 passed, two OS-specific skips (FIFO and symlink privilege).
- Linux Python 3.12 container: all 172 tests passed with networking disabled.
- Official SDK client: initialization, tool discovery, and quota read round trip.
- Authentication separation, bad host/origin, oversized requests, invalid tool
  arguments, missing/failed/old/reset data, sanitized storage errors, and shared
  REST/MCP refresh throttling covered by regression tests.
- Ruff lint/format and publication hygiene passed.
- Python runtime dependency audit: no known vulnerabilities reported. Bandit:
  no medium/high severity findings. This is not a full OS/CLI vulnerability audit.
- Production image build, dependency consistency, credential/storage preflight,
  and non-root/offline REST smoke checks passed.
- Live deployment: both providers collected successfully; official MCP client
  read fresh decision-eligible data via the private network address. Resource/log
  bounds and container health were checked on the running container.
  The next scheduled cycle also succeeded for both providers in 940 ms; the
  container remained healthy with zero restarts.

One existing dependency deprecation warning remains in Starlette's test client
(`anyio.abc.BlockingPortal`); tests pass.

## Recovery and remaining boundaries

The previous image is retained as `ai-usage-monitor:rollback-20261016`. A private
host backup contains the old configuration, source, watchdog script, and an online
SQLite backup. No database schema migration was required.

To roll back, restore the backed-up application/configuration/watchdog files,
tag the rollback image as `ai-usage-monitor:local`, validate Compose, and run
`docker compose up -d --no-build`. Preserve the current database unless separately
recovering corrupted data; restoring a snapshot unnecessarily loses newer history.

Keep one worker/replica per database. The collector and refresh limiter are still
process-local. Bearer-only MCP clients need private network reachability; an
OAuth-only cloud connector needs a separate gateway. Cached percentage checks do
not reserve quota or predict task cost. Provider interfaces, especially Claude's
undocumented endpoint, remain compatibility boundaries.

Connection and agent decision instructions are in [MCP setup](mcp.md).
