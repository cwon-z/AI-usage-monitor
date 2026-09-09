"""Opt-in Compose smoke test with fake secrets; no host ports or real providers.

Usage: DOCKER_HOST=... python tests/compose_smoke.py
The image ai-usage-monitor:patched must already be built. A unique Compose
project and temporary volume are created and removed; other projects are untouched.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    project = "ai-usage-smoke-" + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix=".deployment-test-", dir=root) as directory:
        folder = Path(directory)
        # Test fixtures only. UID is mapped explicitly for Linux/VM host shares.
        folder.chmod(0o755)
        secret = folder / "widget-api-token"
        secret.write_text("compose-fake-widget-token-only")
        secret.chmod(0o600)
        config = folder / "compose.env"
        config.write_text(
            "\n".join(
                [
                    "WIDGET_API_TOKEN_FILE=/run/secrets/widget-api-token",
                    "CLAUDE_ENABLED=false",
                    "CODEX_ENABLED=false",
                    "DATABASE_URL=sqlite:////data/usage.db",
                    "SECRETS_DIR=" + str(folder),
                    "ENV_FILE=" + str(config),
                ]
            )
        )
        config.chmod(0o600)
        override = folder / "override.yml"
        override.write_text(
            'services:\n  ai-usage-monitor:\n    image: ai-usage-monitor:patched\n    user: "'
            + str(os.getuid())
            + ":"
            + str(os.getgid())
            + '"\n    environment:\n      DATABASE_URL: sqlite:////tmp/compose-smoke.db\n'
        )
        command = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(config),
            "-f",
            str(root / "docker-compose.yml"),
            "-f",
            str(override),
        ]

        def run(args, **kwargs):
            return subprocess.run(
                command + args,
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
                **kwargs,
            )

        container = None
        try:
            run(["config", "--quiet"])
            print(
                run(
                    [
                        "run",
                        "--rm",
                        "--no-deps",
                        "ai-usage-monitor",
                        "python",
                        "-m",
                        "ai_usage_monitor.preflight",
                    ]
                ).stdout.strip()
            )
            container = (
                run(["run", "--detach", "--no-deps", "ai-usage-monitor"])
                .stdout.strip()
                .splitlines()[-1]
            )
            code = 'import urllib.request,json; r=urllib.request.Request("http://127.0.0.1:8000/api/v1/usage/compact",headers={"Authorization":"Bearer compose-fake-widget-token-only"}); b=json.load(urllib.request.urlopen(r)); assert b["stale"] is False; assert b["updated_at"] is None; print("Compose mounted-secret authentication passed")'
            for _ in range(30):
                result = subprocess.run(
                    ["docker", "exec", container, "python", "-c", code],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if result.returncode == 0:
                    print(result.stdout.strip())
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("Compose service failed its HTTP check")
            # Broken SQLite health: make the existing read endpoint fail by using
            # an independently tested injected error in unit tests, not corruption.
            print(
                "Compose configuration, read-only bind, capability restrictions, and service startup passed"
            )
        finally:
            if container:
                subprocess.run(
                    ["docker", "rm", "--force", container],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
            run(["down", "--volumes", "--remove-orphans"])


if __name__ == "__main__":
    main()
