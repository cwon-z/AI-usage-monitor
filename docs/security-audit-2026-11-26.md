# Security audit and maintenance update — 2026-11-26

A source, dependency, container and CI review of the whole repository, with the
fixes applied in the same change. **No exposed credentials and no remotely
exploitable flaw in the API were found.** The most serious finding was
operational: both CI workflows had been failing on `main`, so the security scans
were not running. This is a review of what is in the repository, not a
penetration test of a running deployment.

## State before and after

| | Before | After |
|---|---|---|
| CI on `main` | Both workflows failing; scans behind the first failure skipped | Every check is its own job |
| Locked Python dependencies | 1 known advisory | 0 |
| Built image, fixable high/critical advisories | 12 | 0 |
| Automated tests | 174 | 190 |

## Findings

Severity reflects this project's setting: a single-user service kept on a
private network.

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | High | Security scans silently not running | Fixed |
| 2 | Medium | Known advisory in a locked dependency | Fixed |
| 3 | Medium | Fixable advisories and unused tooling in the image | Fixed |
| 4 | Medium | Watchdog defaulted to one deployment's private address | Fixed in the tree; remains in history |
| 5 | Low | MCP authentication layer: three gaps | Fixed |
| 6 | Low | Database files world-readable outside Docker | Fixed for new databases |
| 7 | Low | Host scripts: URL schemes, state file mode, dated model ID | Fixed |
| 8 | Hardening | Unverified dependency downloads, writable root filesystem | Fixed |
| 9 | Maintenance | Outdated bases, actions and tools | Updated, except the provider CLIs |

### 1. Security scans silently not running

Each workflow was a single job, which stops at its first failing step.

- **Security** has failed since its scheduled run on 2026-11-16, at its first
  step, the dependency audit (finding 2). Bandit, Gitleaks and the publication
  check after it did not run.
- **Verify** has failed since the pushes of 2026-11-23, at the formatting check
  on four files those pushes brought in. The lock check, image build and both
  smoke tests after it did not run.

The commits that reached `main` on 2026-11-23, which include the host scripts
and the Cloudflare Access verification, were therefore not secret-scanned or
built in CI. A Gitleaks scan of every commit on every branch, run for this
audit, is clean. Each check is now a separate job, so one failure cannot hide
another.

### 2. Known advisory in a locked dependency

`pip-audit` reports CVE-2026-126360 against `opentelemetry-api` 1.45.1, fixed in
1.45.2. It is a dependency of the MCP SDK; this application does not configure
OpenTelemetry itself. Exploitability here was not assessed. The lock was
refreshed, which moves every package to its current release within the existing
`pyproject.toml` ranges.

### 3. Fixable advisories and unused tooling in the image

The image had never been scanned; earlier reviews list that as outstanding.
Trivy found twelve high-severity advisories with fixes available, none critical
and none in code the service executes:

- six in npm's own dependencies. The image copied the whole `node_modules` tree
  from the build stage, including npm and corepack, though it only runs the two
  provider CLIs;
- five in libraries vendored inside pip, which is not needed after the build;
- one in a Debian package that had a fix newer than the base tag.

The image now copies only the two CLIs, removes pip after installing, and applies
pending Debian updates at build time. The same scan reports no fixable finding
at any severity. Advisories that Debian has not yet fixed remain in base
packages (the util-linux family, SQLite, OpenSSL and others); nothing in this
repository can clear those, and rebuilding picks the fixes up as they ship.

### 4. Watchdog defaulted to one deployment's private address

`scripts/auth-watchdog.py` defaulted its API base to a specific overlay-network
address over plain HTTP, and named a specific SSH host. For anyone else running
the script unconfigured, the widget bearer token would have been sent to that
address. It also published details of one private deployment.

The default is now the loopback port the base Compose file publishes. A
deployment's own values come from `state/api-url` and `state/ssh-host`, or the
`AI_USAGE_API` and `AI_USAGE_SSH_HOST` variables. The publication check now
rejects private network addresses in committed files, so this fails CI in future.

The old values are still in Git history. The address is in a range that is not
reachable from the internet, so history was left alone; rewriting it is the
owner's call.

### 5. MCP authentication layer

- Requests that were not HTTP were passed to the MCP application without
  authentication. The SDK currently closes websockets, so this was not
  exploitable, but the layer now refuses them itself.
- Cloudflare Access verification runs before authentication and can wait on a
  signing-key download for up to five seconds when Cloudflare is unreachable. It
  shared the worker threads used by collection and storage, so requests carrying
  a JWT-shaped header could stall collection during such an outage. At most two
  verifications now run at once; the rest receive 401.
- `MCP_PUBLIC_HOST` was compared exactly. A value with capitals, a trailing dot,
  a scheme or a port never matched, so the rule that the public host requires
  Access did not apply to it. The value is normalized and impossible values are
  rejected at startup.

### 6. Database files world-readable outside Docker

A new database, its WAL files and a new data directory took the process umask,
typically 0644 and 0755, exposing retained usage and plan data to other local
users on a shared machine. New files are created 0600 and new directories 0700.
Existing files keep the modes the operator chose.

### 7. Host scripts

- URLs read from `state/` were opened with any scheme `urllib` supports,
  including `file://`. Only `http(s)` is accepted now, and `https` for the
  webhook that carries sign-in codes.
- `watchdog.json`, which can hold a live sign-in code, was written with default
  permissions and restricted afterwards. It is now created 0600.
- Claude renewal named a dated model ID. Once that snapshot is retired the
  trigger call fails and renewal stops. It now uses the CLI's `haiku` alias,
  which resolves to the same model on the pinned CLI.

### 8. Hardening

- The image installed dependencies by version only. `requirements.lock` now
  carries hashes and the build uses `--require-hashes`.
- Compose now mounts the root filesystem read-only. `/data`, `/tmp` and the
  optional `app-home` volume remain writable.
- The `Server` response header is no longer sent.

### 9. Maintenance updates

| Component | From | To |
|---|---|---|
| Debian base | 12 (bookworm) | 13 (trixie) |
| Python in the image | 3.12 | 3.14 |
| Node in the image | 22 | 24 LTS |
| FastAPI / Starlette | 0.141.1 / 1.6.0 | 0.165.0 / 1.10.0 |
| Pydantic / pydantic-settings | 2.13.5 / 2.15.0 | 2.14.4 / 2.16.3 |
| SQLAlchemy | 2.0.52 | 2.1.9 |
| Uvicorn | 0.52.4 | 0.54.0 |
| actions/checkout | 4.3.1 | 7.0.1 |
| astral-sh/setup-uv | 6.8.0 | 10.4.0 |
| Ruff | 0.16.6 | 0.17.6 |

The two old action pins targeted the deprecated Node 20 action runtime.
Dependabot now opens one grouped pull request per ecosystem and waits a week
after a release before proposing it.

## Reviewed and found sound

No change was needed in these areas:

- Bearer comparison is constant-time and length-bounded; all `/api/v1` routes
  except health require the token; widget and MCP tokens cannot be interchanged.
- Access JWT verification pins the algorithm, issuer and audience, requires
  expiry and identity claims, and uses an explicit email allowlist. Missing
  configuration fails closed.
- Provider adapters use a fixed origin with redirects and environment proxies
  disabled, bound response sizes, and strip refresh credentials before any
  request or child process. Child processes get an allowlisted environment.
- Secret reads are bounded and reject non-regular files; the exporter writes
  atomically with 0600/0700 and refuses symlinks.
- Queries are parameterized; error responses and logs carry sanitized codes only.
- No credential file, database or environment file appears in any commit on
  any branch of the repository.

## Verification

- 190 tests pass on Python 3.12, 3.13 and 3.14. The code fixes have regression
  tests.
- Ruff lint and format, Bandit on `src` and `scripts`, `pip-audit`, the
  publication check and `uv lock --check` pass.
- Gitleaks 8.30.1: no findings in tracked files or in any commit on any branch.
- `npm audit` on the pinned provider CLI packages: no known advisories.
- The image builds; the container and Compose smoke tests pass with a read-only
  root filesystem; the real Codex CLI starts and answers the adapter inside that
  container.
- Both workflows pass on GitHub for this change, all eight jobs, including the
  build of the unmodified Dockerfile, its smoke tests and the image scan.

Not exercised, because no provider credentials or deployment were available:
live Claude and Codex collection, the renewal and re-login scripts, Cloudflare
Access end to end, and the Android widget.

## Left for the owner

**When deploying this change**

1. If the API is published anywhere other than `127.0.0.1:8000`, write its base
   URL to `state/api-url` on the host. Otherwise the watchdog reports the API
   unreachable and alerts.
2. Rebuild with `docker compose build --pull`. CLI logins inside the container
   need the `app-home` volume from the override example now that the root
   filesystem is read-only.
3. If `MCP_PUBLIC_HOST` is set, make sure it is a bare hostname.

**Repository settings**, which cannot be inspected or changed from the source
tree. `main` is currently unprotected.

- Protect `main` and require the Verify and Security checks.
- Enable secret scanning with push protection, private vulnerability reporting,
  Dependabot security updates, and CodeQL default setup.

**Provider CLI pins** were not changed: Claude Code 2.1.266 (2.1.332 is current)
and Codex 0.150.1 (0.172.0 is current). The renewal and re-login behaviour the
scripts depend on can only be confirmed with a live login. Evidence gathered for
that review: Codex 0.172.0 keeps `account/rateLimits/read` and only adds optional
response fields, which the adapter ignores.

**Smaller items**

- The Gitleaks pin was not updated; its release page was not reachable from the
  review environment to verify a newer checksum.
- Base images are pinned by tag, not digest. Digest pins only stay current if
  Dependabot pull requests are merged promptly, and nine were open at the time
  of this audit.
- Failed authentication attempts are neither logged nor rate-limited. With
  generated 256-bit tokens on a private network this is acceptable; reconsider
  it before any wider exposure.
