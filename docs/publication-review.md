# Publication hygiene and security review

Reviewed 2026-09-10. **No exposed credentials or known vulnerable locked Python
packages were found in the checks below.** This is not a penetration test or a
security guarantee. Public source does not imply a public-facing deployment.

## Changes

- Standalone launcher now binds to loopback and disables access logs. Docker
  retains its explicit container interface binding and loopback host port.
- Expanded Git exclusions for credential copies, database sidecars, private
  keys, build outputs, and workstation artifacts. Docker context is allowlisted
  to required source/build inputs only.
- Removed 49 obsolete widget deliverables/design notes from the publication
  candidates, retaining the current R7 preset, previews, licensed source assets,
  and legacy source-level regression tests. Files were moved to a local temporary
  recovery folder, not permanently deleted. No credential files were changed.
- Consolidated widget instructions around R7 and made it the default CLI build.
  Preview generation no longer requires an APK for bundled-font revisions.
- Added repository-local lint configuration and formatted the widget/test code.
- Added a security policy, contribution guide, publication guard and regression
  tests. CI actions use full commit SHAs and do not persist checkout credentials.
  Security CI scans dependencies and secrets on changes and weekly; Dependabot
  covers uv dependencies, CI actions, and Docker base tags.

## Checks and coverage

| Check | Result |
|---|---|
| Automated tests | 159 passed; 2 upstream deprecation warnings |
| Ruff 0.16.6 lint and format | Passed for source, tests, scripts, widget tools |
| Gitleaks 8.30.1 | No findings in non-ignored/tracked file copy, embedded R7 JSON, or all existing Git refs |
| Known local credential comparison | No matches in publication files or expanded widget archive contents; values were never printed |
| Secret permissions | Directory 0700; all three credential files 0600; ignored by Git |
| pip-audit 2.10.1 | No known vulnerabilities in locked runtime and dev/test Python dependencies queried via PyPI |
| Bandit 1.9.4 | Zero medium/high findings; six low findings reviewed below |
| Lock consistency | `uv lock --check` passes; runtime export matches requirements.lock, excluding its command comment |
| Publication guard | Private paths, unsafe archives, and configured widget/cache globals rejected |
| Current widget artifact | Tested against a deterministic rebuild; font and logo licenses retained |

Gitleaks scanned the working publication candidates separately because the
repository initially had only LICENSE in its single commit. Ignored local secrets
were not copied to the scanner's candidate directory or uploaded to a service.
Dependency auditing transmits public package identifiers/versions, not source or
credentials. No remote repository settings, commits, staging, or publishing were
performed by this review.

The six low Bandit findings are three assertions of subprocess PIPE objects
created immediately beforehand (not authentication checks), an intentionally
empty refresh-token field that prevents renewal, and two subprocess warnings
around the fixed macOS Keychain executable with an argument list and no shell.
These were reviewed, not blanket-suppressed; CI fails at medium/high severity.

## Remaining pre-release work

The operator subsequently confirmed on 2026-09-10 that the container and widget
work on Android and authorized structured local commits. This confirms reported
functionality, not a container vulnerability scan or broad device compatibility.

- Run CI after committing. Local checks do not prove the GitHub workflows have
  run successfully. Review the staged file list before the first source commit.
- Enable secret scanning/push protection and private vulnerability reporting
  where supported, plus required checks in repository settings.
- Scan the final container. Docker's daemon/socket was unavailable
  during this review, so current Docker/Compose smoke tests and OS/Node/provider
  CLI vulnerability scanning were **not** performed. Prior backend container
  checks are documented separately in [verification.md](verification.md). The
  operator's subsequent working-container confirmation does not replace this scan.
- Extend [Android verification](widget-verification.md) to recorded device/app
  versions and long-running background refresh; functional operation is now
  operator-confirmed, but the widget should remain labeled experimental.
- Audit releases and any external copies independently. The history check covers
  local reachable refs, not deleted unreachable objects, unknown forks, previous
  uploads, or other machines. Rotate any credential exposed elsewhere.

## Tools and supporting references

- [Gitleaks source and usage](https://github.com/gitleaks/gitleaks)
- [pip-audit scope and limitations](https://github.com/pypa/pip-audit)
- [Bandit](https://bandit.readthedocs.io/)
- [Pinned checkout release](https://github.com/actions/checkout/releases/tag/v4.3.1)
- [Pinned setup-uv release](https://github.com/astral-sh/setup-uv/releases/tag/v6.8.0)
