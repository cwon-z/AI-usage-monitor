# Deployment verification — 2026-09-10

The debug-sweep findings have been patched. This records executed checks, not a claim that every possible defect or upstream change is covered.

## Verified

- 69 automated tests passed on macOS/Python 3.12 and inside the built Linux ARM64 image.
- Ruff lint, formatting, and Python compilation passed.
- The installed Python environment's vulnerability scan reported no known advisories after updating pytest. The project itself is local and not in the vulnerability database; OS packages and proprietary CLI binaries are not covered by this scan.
- ARM64 and AMD64 Docker builds passed, including both pinned CLI executable checks and Python dependency consistency.
- All six HTTP routes, unauthenticated rejection, no-store headers, disabled-provider behavior, and non-root runtime passed offline container smoke tests on both ARM64 and emulated AMD64.
- Compose startup/preflight and authentication through a read-only mounted fake secret passed in a unique disposable test project.
- Actual Claude and Codex collection plus SQLite caching succeeded inside the hardened container using access-token-only snapshots: both providers returned ok, stale=false, error=null.
- The source Codex auth file hash was unchanged. Claude credentials were read from Keychain, stripped in memory, and never refreshed or written back by the monitor.

## Changes addressing the audit

1. Corrected the packaged Claude executable path and made both CLI version checks part of the Docker build.
2. Replaced Claude's unreliable isolated control-channel collection with the verified, read-only OAuth usage GET. It is explicitly classified as undocumented. Inference-only setup tokens are no longer recommended.
3. Added bounded, read-only credential extraction and an access-token-only exporter. Codex receives no usable refresh token; Claude sends only its access token in a fixed-origin GET. Expired credentials fail closed.
4. Added configurable non-root UID/GID, restrictive secret export permissions, directory-mounted snapshots for atomic replacement, and deployment preflight. Missing secret directories fail immediately.
5. Separated storage errors from provider retry logic, moved SQLite work off the event loop, preserved peer collection, coalesced simultaneous polls, and kept scheduling alive after transient cycle failures.
6. Health returns 503 when storage or scheduling is degraded. Added a sanitized storage-error response and no-store API headers.
7. Restricted Codex convenience windows to the same canonical bucket. Independently labeled model quotas are never substituted into it.
8. Removed the incorrect Claude consumed-credit-to-balance mapping. Added strict Codex credit validation and additional malformed-payload tests.
9. Fixed JSONL stream limits, continuous stderr draining, total exchange deadlines, process-group cleanup, and bounded termination waits.
10. Distinguished successful collection timestamps from attempts, marked reset-expired samples stale, excluded disabled providers from aggregate staleness, and made pacing sample-time-based.
11. Allowlisted child environment variables and isolated the Codex working/configuration directories.
12. Fixed SQLite in-memory cross-thread behavior and serialized repository operations. Added stable history cursor pagination.
13. Updated the vulnerable test dependency, locked runtime constraints for Docker, and added a CI workflow for tests, lint, lock consistency, and container/Compose smoke checks.

## Deployment requirements and remaining boundaries

- Follow the README export/setup steps on the destination host and run preflight before startup. Real deployment credentials and a production .env were deliberately not created in the repository.
- Exported access tokens expire. Re-export after your normal provider client renews its session; the service intentionally never refreshes that session itself.
- Claude's endpoint and provider credential formats may change upstream. These remain isolated adapter boundaries.
- Run one worker/replica per database. Multi-replica scheduling is not implemented.
- Existing data volumes may need ownership migration if runtime UID/GID changes. Do not delete account history to fix permissions.
- Two upstream test-client deprecation warnings remain; no failing tests were suppressed.
- The CI workflow was added locally; it has not been pushed or executed on GitHub.
- Long-duration production load/retention behavior was not soak-tested. Retention and failure/recovery behavior are covered by deterministic tests.

This is a historical backend verification record, not a claim that a current
deployment is running. See [the publication review](publication-review.md) for
the latest checks and limitations. No service was published to the internet.
