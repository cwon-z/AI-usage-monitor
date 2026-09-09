# Widget verification boundaries

Current distributed preset: **Quota Panel 3×2 R7**.

Offline checks cover archive integrity, credential-free defaults, native Flow
parameter types, HTTPS/header constraints, formula parsing, empty/stale/error
states, bundled font/license references, embedded vector paths, and responsive
bounds across several viewport sizes. Preview PNGs use illustrative values.

The R7 layout uses compact provider rows with secondary quotas in a bottom panel.
Earlier revisions had import, child-positioning, Flow serialization, and bitmap
loading problems. Those revisions remain only as source-level regression fixtures,
not recommended downloads. R7 removes bitmap file references for provider logos.

## Operator verification

On 2026-09-10, the operator confirmed that the container and widget work on
Android. This is an operator-reported functional check, not an independently
observed device test. Device, launcher, Android, and KWGT versions were not
recorded, so compatibility across other devices is not established.

## Remaining coverage

Long-running background refresh across launcher/app restarts and battery
restrictions was not explicitly confirmed. No Android device or emulator was
available for independent verification during the repository review.

For future device checks, record KWGT/Android/launcher versions, applied-widget
screenshots, and sanitized state transitions. Never include the token or raw
authorization headers in public issues.
