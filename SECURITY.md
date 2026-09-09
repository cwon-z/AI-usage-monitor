# Security policy

This is an experimental, single-user self-hosted service. A public source
repository does **not** mean its running API should be public.

## Reporting a vulnerability

Use GitHub's **Security → Report a vulnerability** if private reporting is
enabled for this repository. If it is unavailable, open an issue requesting a
private contact channel, without exploit details, credentials, or account data.
Do not post authentication files, configured KWGT exports, database snapshots,
or unredacted logs. There is no guaranteed response SLA or formal support window.

## Deployment boundaries

- Keep the API on loopback or a private network; prefer authenticated private
  connectivity with HTTPS. Never use a public tunnel without access controls.
- The widget token grants usage/history access **and** manual refresh. It is not
  a read-only credential. Generate a random token; rotate it after exposure.
- Only health is unauthenticated. This app is not a multi-tenant service and
  does not implement per-user roles, distributed rate limiting, or Internet-scale
  denial-of-service protection. Run one worker/replica per SQLite database.
- Provider snapshots remain server-side. They are sensitive access credentials,
  even without refresh tokens. Restrict directory/file permissions to 0700/0600.
- Protect the phone and its backups: a saved/configured KWGT preset contains its
  widget token and cached account usage. Never publish an exported personal preset.
- Provider credential formats and Claude's undocumented endpoint may change.
  CLI binaries are trusted dependencies, not sandboxed untrusted plugins.

## Before publishing or releasing

1. Run the checks in [CONTRIBUTING.md](CONTRIBUTING.md), including Gitleaks on
   both the working files and all Git history. Review staged files before commit.
2. Keep `.env`, `secrets/`, provider homes, databases, logs, and personal exports
   out of Git, release attachments, and Docker build contexts. `.gitignore` does
   not protect files already committed or uploaded elsewhere.
3. Enable repository secret scanning/push protection and private vulnerability
   reporting where available. Enable required CI checks and review dependency PRs.
4. Audit the final container, including OS packages, Node and provider CLIs;
   Python dependency auditing alone does not cover the image.
5. If a secret was committed, revoke/rotate it first. Removing a file from the
   latest commit does not remove it from history, forks, caches, or downloads.

Automated checks are defense in depth, not proof that the code is vulnerability-free.
