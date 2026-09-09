# Contributing and local checks

Use Python 3.12+ and uv. Tests use synthetic credentials and mocked provider
responses; do not add real authentication files, live account fixtures, or personal
KWGT exports. See [SECURITY.md](SECURITY.md) for private vulnerability reporting.

```bash
uv sync --frozen --extra dev
uv run --frozen pytest
uvx ruff==0.16.6 check src tests scripts widgets/kwgt
uvx ruff==0.16.6 format --check src tests scripts widgets/kwgt
uv run --frozen python scripts/check_publication.py
uvx bandit==1.9.4 -r src -ll
uvx pip-audit==2.10.1 -r requirements.lock --no-deps --disable-pip
```

The publication check scans **tracked and non-ignored untracked files**, including
embedded KWGT globals. It is a hygiene check, not a general secret detector.
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
runtime-only export used by Docker. Update both together:

```bash
uv lock
uv export --frozen --no-dev --no-emit-project --no-hashes \
  --format requirements-txt --output-file requirements.lock
```

Dependabot checks uv dependencies, CI actions, and Docker base tags. Keep Actions
pinned to full commit SHAs and CI permissions read-only. Provider CLI version
arguments need manual compatibility review. Advisory checks require network
access and send package names/versions, not credentials or source files.

Build and smoke-test on a host with Docker available, then audit the final image
with an up-to-date container vulnerability scanner (including OS/Node/CLI packages):

```bash
docker build -t ai-usage-monitor:check .
docker run --rm -i --network none --cap-drop ALL --security-opt no-new-privileges \
  ai-usage-monitor:check python - < tests/container_smoke.py
uv run --frozen python tests/compose_smoke.py
```

Do not run live provider checks in pull-request CI or provide it with personal
credentials. Before committing, review `git status --short` and the staged diff.
Before publication, enable private security reporting, secret scanning/push
protection where available, and required CI checks in repository settings.
