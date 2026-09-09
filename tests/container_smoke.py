"""Run via docker run -i ... python - < tests/container_smoke.py (no real secrets)."""

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

with tempfile.TemporaryDirectory() as directory:
    env = os.environ.copy()
    env.update(
        WIDGET_API_TOKEN="audit-fake-widget-token",
        CLAUDE_ENABLED="false",
        CODEX_ENABLED="false",
        DATABASE_URL="sqlite:///" + directory + "/usage.db",
    )
    env.pop("WIDGET_API_TOKEN_FILE", None)
    process = subprocess.Popen(
        [
            "uvicorn",
            "ai_usage_monitor.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8099",
            "--no-access-log",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        # Cross-architecture emulation can take substantially longer to import.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(
                    "Server exited during startup: "
                    + process.stderr.read(4096).decode()
                )
            try:
                urllib.request.urlopen(
                    "http://127.0.0.1:8099/api/v1/health", timeout=1
                ).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("Server readiness deadline exceeded")
        for route in [
            "health",
            "usage",
            "usage/compact",
            "usage/history",
            "providers",
            "usage/refresh",
        ]:
            request = urllib.request.Request(
                "http://127.0.0.1:8099/api/v1/" + route,
                headers={"Authorization": "Bearer audit-fake-widget-token"},
                method="POST" if route.endswith("refresh") else "GET",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                body = json.load(response)
                assert response.status == 200
                if "stale" in body:
                    assert body["stale"] is False
                    assert body["updated_at"] is None
                assert response.headers["cache-control"] == "no-store"
                print(route, "passed")
        try:
            urllib.request.urlopen("http://127.0.0.1:8099/api/v1/usage")
            raise AssertionError("Unauthenticated request accepted")
        except urllib.error.HTTPError as error:
            assert error.code == 401
        assert os.getuid() != 0
        print("Authentication, response privacy, and non-root runtime passed")
    finally:
        process.terminate()
        process.wait(timeout=5)
