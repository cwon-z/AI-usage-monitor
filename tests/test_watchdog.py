"""Host-side watchdog: configuration, URL handling and state, never real alerts."""

import importlib.util
import json
import os
import socket
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/auth-watchdog.py"


def load(monkeypatch, tmp_path, **environment):
    """Import the script against an isolated state directory and environment."""
    monkeypatch.setenv("PATH", os.environ["PATH"])  # the script overwrites it
    for name in ("AI_USAGE_API", "AI_USAGE_SSH_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AI_USAGE_STATE_DIR", str(tmp_path))
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location("auth_watchdog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_defaults_name_no_particular_deployment(monkeypatch, tmp_path):
    watchdog = load(monkeypatch, tmp_path)
    assert watchdog.API == "http://127.0.0.1:8000/api/v1"
    assert watchdog.SSH_HOST == socket.gethostname()
    assert watchdog.CLAUDE_LOGIN.startswith(f"ssh -t {socket.gethostname()} ")


def test_deployment_values_come_from_state_then_environment(monkeypatch, tmp_path):
    (tmp_path / "api-url").write_text("https://usage.example.ts.net/api/v1/\n")
    (tmp_path / "ssh-host").write_text("homelab\n")
    watchdog = load(monkeypatch, tmp_path)
    assert watchdog.API == "https://usage.example.ts.net/api/v1"
    assert watchdog.SSH_HOST == "homelab"
    watchdog = load(monkeypatch, tmp_path, AI_USAGE_API="http://localhost:9010/api/v1")
    assert watchdog.API == "http://localhost:9010/api/v1"


def test_only_http_urls_are_opened(monkeypatch, tmp_path):
    watchdog = load(monkeypatch, tmp_path)
    opened = []
    monkeypatch.setattr(
        watchdog.urllib.request, "urlopen", lambda *a, **kw: opened.append(a)
    )
    for value in ("file:///etc/passwd", "ftp://example.com/x", "example.com/push", ""):
        (tmp_path / "kuma-push-url").write_text(value)
        with pytest.raises(RuntimeError, match="kuma-push-url must be"):
            watchdog.push("up", "ok")
    # Sign-in codes travel through the webhook, so it must also be encrypted.
    (tmp_path / "discord-webhook").write_text("http://example.com/api/webhooks/1/x")
    with pytest.raises(RuntimeError, match="discord-webhook must be an https URL"):
        watchdog.discord("test")
    with pytest.raises(RuntimeError, match="usage API base must be"):
        watchdog.http_url("file:///etc/api/v1/usage/compact", "the usage API base")
    assert opened == []


def test_assess_separates_dead_logins_from_stale_and_transient(monkeypatch, tmp_path):
    watchdog = load(monkeypatch, tmp_path)
    ok = {"status": "ok", "stale": False, "error": None}
    assert watchdog.assess({"claude": ok, "openai": ok})[:2] == (True, False)
    dead = {"status": "error", "stale": True, "error": "authentication_expired"}
    healthy, codex_dead, message = watchdog.assess({"claude": ok, "openai": dead})
    assert (healthy, codex_dead) == (False, True)
    assert "{codex_hint}" in message
    healthy, codex_dead, message = watchdog.assess({"claude": dead, "openai": ok})
    assert (healthy, codex_dead) == (False, False)
    assert "claude-login.sh" in message
    stale = {"status": "ok", "stale": True, "error": None}
    assert watchdog.assess({"claude": stale, "openai": ok})[0] is False
    transient = {"status": "error", "stale": False, "error": "timeout"}
    assert watchdog.assess({"claude": transient, "openai": ok})[0] is True


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")
def test_state_file_is_owner_only_even_under_a_permissive_umask(monkeypatch, tmp_path):
    watchdog = load(monkeypatch, tmp_path)
    ok = {"status": "ok", "stale": False, "error": None}
    monkeypatch.setattr(watchdog, "read_compact", lambda state: {"claude": ok})
    pushed = []
    monkeypatch.setattr(watchdog, "push", lambda *args: pushed.append(args))
    monkeypatch.setattr(watchdog.sys, "argv", ["auth-watchdog.py"])
    stale_temporary = tmp_path / "watchdog.tmp"
    stale_temporary.write_text("left by an interrupted run")
    stale_temporary.chmod(0o644)
    previous = os.umask(0o000)
    try:
        assert watchdog.main() == 0
    finally:
        os.umask(previous)
    state_file = tmp_path / "watchdog.json"
    assert state_file.stat().st_mode & 0o777 == 0o600
    assert json.loads(state_file.read_text())["last_status"] == "up"
    assert pushed == [("up", "claude: ok")]
    assert not stale_temporary.exists()
