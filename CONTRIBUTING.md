# Contributing and local checks

Use Python 3.12+ and uv. Tests use synthetic credentials and mocked provider
responses; do not add real authentication files, live account fixtures, or personal
KWGT exports. See [SECURITY.md](SECURITY.md) for private vulnerability reporting.

```bash
uv sync --frozen --extra dev
uv run --frozen pytest
uvx ruff==0.17.6 check src tests scripts widgets/kwgt
uvx ruff==0.17.6 format --check src tests scripts widgets/kwgt
uv run --frozen python scripts/check_publication.py
uvx bandit==1.9.4 -r src scripts -ll
uvx pip-audit==2.10.1 -r requirements.lock --no-deps --disable-pip
```

The publication check scans **tracked and non-ignored untracked files**, including
embedded KWGT globals, and rejects private network addresses outside `tests/`:
a deployment's own address belongs in `.env` or `state/`, never in a default.
It is a hygiene check, not a general secret detector.
Install [Gitleaks](https://github.com/gitleaks/gitleaks) from a trusted release,
then scan working files and all Git refs with values redacted:

```bash
gitleaks dir . --redact --max-archive-depth 2
gitleaks git . --redact --log-opts="--all"
```

The directory scanner can flag ignored private files if run in a configured
working copy. **Do not upload scanner logs or reports** without review. Ignore
rules are not a reason to dismiss a match in publication candidates or history.
Never add a broad allowlist to silence an unexplained finding.

## Dependencies and CI

`uv.lock` includes runtime and test dependencies; `requirements.lock` is the
runtime-only export, with hashes, that Docker installs with `--require-hashes`.
Update both together:

```bash
uv lock
uv export --frozen --no-dev --no-emit-project \
  --format requirements-txt --output-file requirements.lock
```

Dependabot checks uv dependencies, CI actions, and Docker base tags weekly. It
groups each ecosystem into one pull request and waits a week after a release
before proposing it; security updates are not delayed. Keep Actions pinned to
full commit SHAs and CI permissions read-only. Three pins are outside its reach
and need a manual bump: the provider CLI versions in the Dockerfile (after the
compatibility review in the README), the Gitleaks release and checksum, and the
Trivy image digest in the Security workflow. Advisory checks require network
access and send package names/versions, not credentials or source files.

CI runs each check as its own job, so one failure does not hide the others.
Tests run on the oldest supported Python and on the version the image ships.
Build and smoke-test on a host with Docker available, then scan the final image
(OS, Node and CLI packages included) the way the Security workflow does:

```bash
docker build -t ai-usage-monitor:patched .
docker run --rm -i --network none --cap-drop ALL --security-opt no-new-privileges \
  --read-only --tmpfs /tmp:size=64m,mode=1777 \
  ai-usage-monitor:patched python - < tests/container_smoke.py
uv run --frozen python tests/compose_smoke.py
docker save ai-usage-monitor:patched --output image.tar
docker run --rm --volume "$PWD/image.tar:/image.tar:ro" \
  aquasec/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb \
  image --input /image.tar --scanners vuln --ignore-unfixed --severity HIGH,CRITICAL --exit-code 1
rm image.tar
```

A finding inside a provider CLI is cleared by bumping that CLI's pin. Do not
add a blanket ignore to make the scan pass.

Do not run live provider checks in pull-request CI or provide it with personal
credentials. Before committing, review `git status --short` and the staged diff.
Before publication, enable private security reporting, secret scanning/push
protection where available, and required CI checks in repository settings.
