"""Export access-token-only snapshots; never log in, refresh, or modify source auth."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

from ai_usage_monitor.providers.base import ProviderError
from ai_usage_monitor.providers.credentials import (
    claude_credentials,
    codex_credentials,
    read_secret,
    secret_object,
)


def atomic_secret(path: Path, value: str, *, replace: bool) -> None:
    """Atomically replace only operator-owned exports, never symlink targets."""
    if path.is_symlink() or (path.exists() and not replace):
        raise ValueError("output exists; explicit replacement required")
    fd, temporary = tempfile.mkstemp(prefix=".export-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_claude_source(path: Path, *, keychain_service: str | None) -> dict:
    if keychain_service:
        if sys.platform != "darwin":
            raise ValueError("Keychain export is available only on macOS")
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                keychain_service,
                "-w",
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0 or len(result.stdout) > 1_000_000:
            raise ProviderError("authentication_missing")
        return secret_object(result.stdout.decode("utf-8"))
    return secret_object(read_secret(path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("secrets"))
    parser.add_argument(
        "--claude-credentials",
        type=Path,
        default=Path.home() / ".claude/.credentials.json",
    )
    parser.add_argument(
        "--claude-keychain-service",
        help="macOS only: usually 'Claude Code-credentials'",
    )
    parser.add_argument(
        "--codex-auth", type=Path, default=Path.home() / ".codex/auth.json"
    )
    parser.add_argument("--skip-claude", action="store_true")
    parser.add_argument("--skip-codex", action="store_true")
    parser.add_argument("--replace-provider-snapshots", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.expanduser().absolute()
    try:
        sources = [
            args.claude_credentials.expanduser().resolve(),
            args.codex_auth.expanduser().resolve(),
        ]
        destinations = [output / "claude-credentials.json", output / "codex-auth.json"]
        if any(destination.resolve() in sources for destination in destinations):
            raise ValueError("output must not overwrite an authentication source")
        exports = {}
        if not args.skip_claude:
            oauth = claude_credentials(
                read_claude_source(
                    args.claude_credentials.expanduser(),
                    keychain_service=args.claude_keychain_service,
                )
            )
            exports["claude-credentials.json"] = {"claudeAiOauth": oauth}
        if not args.skip_codex:
            tokens = codex_credentials(
                secret_object(read_secret(args.codex_auth.expanduser()))
            )
            tokens.pop("refresh_token", None)
            exports["codex-auth.json"] = {"tokens": tokens}
        if output.is_symlink():
            raise ValueError("output directory must not be a symlink")
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        output.chmod(0o700)
        for name in exports:
            path = output / name
            if path.is_symlink() or (
                path.exists() and not args.replace_provider_snapshots
            ):
                raise ValueError("provider output already exists")
        widget = output / "widget-api-token"
        if widget.is_symlink():
            raise ValueError("widget token must not be a symlink")
        for name, data in exports.items():
            atomic_secret(
                output / name, json.dumps(data), replace=args.replace_provider_snapshots
            )
        if not widget.exists():
            atomic_secret(widget, secrets.token_urlsafe(32), replace=False)
        print(
            "Access-token snapshots exported; source credentials unchanged. Existing widget token preserved."
        )
        print(
            f"For native Linux Docker, set APP_UID={os.getuid()} and APP_GID={os.getgid()} in .env before building."
        )
    except (ProviderError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        code = (
            exc.code
            if isinstance(exc, ProviderError)
            else "export_failed_check_paths_permissions_and_existing_outputs"
        )
        parser.exit(1, f"Export failed: {code}\n")


if __name__ == "__main__":
    main()
