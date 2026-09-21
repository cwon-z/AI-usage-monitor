#!/usr/bin/env python3
"""Tell Uptime Kuma whether this deployment's provider logins work; offer Codex re-login.

Nothing else notices a dead login: /api/v1/health stays ok, the container stays
healthy, provider_auth_expired logs at INFO, and the widget keeps rendering the
last values. On 2026-09-12 that hid a revoked Claude grant for nine days.

Run from cron every 5 minutes on the deployment host. Each run reads
/api/v1/usage/compact with the widget token (which never leaves this host) and
pushes up/down to a Kuma push monitor. Kuma owns the alerting: it notifies on
state changes, and marks the monitor down by itself if these pushes stop.

When Codex's login is dead, the run also starts a device-code login inside the
container and posts the link and code to Discord, so re-login is one approval
from a phone. Codes last 15 minutes; a fresh one is offered every 3 hours while
the login stays dead, or on demand with --codex-login. Claude has no device
flow (its login needs a code pasted back into a terminal), so its alert carries
the one command to run instead: scripts/claude-login.sh.

Files under state/ (mode 600, not in the repo):
  kuma-push-url    the monitor's push URL, which embeds its token
  discord-webhook  where sign-in codes are posted
  watchdog.json    what the previous run saw
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"  # cron
ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
API = os.environ.get("AI_USAGE_API", "http://100.127.116.72:9010/api/v1")
SERVICE = "ai-usage-monitor"
REOFFER_SECONDS = int(os.environ.get("CODEX_LOGIN_REOFFER_SECONDS", 3 * 3600))
CLAUDE_LOGIN = f"ssh -t core-apps {ROOT}/scripts/claude-login.sh"
KST = timezone(timedelta(hours=9))
UA = "DiscordBot (https://github.com/cwon-z/AI-usage-monitor, 1.0)"

# Runs inside the container. Kills any earlier device login, so only one code
# is ever live, then starts a new one. On approval it asks the API to collect
# at once instead of waiting out the 3-minute poll.
#
# The login runs in a STAGING CODEX_HOME and is moved over the live file only
# once approved: `codex login` deletes the existing auth.json the moment it
# starts (measured on 0.150.1), so logging in in place would destroy a working
# grant on any false alarm, or leave none at all if the code simply expired.
CODEX_LOGIN = r"""
python - <<'EOF'
import os, signal
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
    except OSError:
        continue
    if b"--device-auth" in argv:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass
EOF
cd /tmp
rm -f /tmp/codex-login.out
rm -rf /home/app/.codex-login && mkdir -m 700 /home/app/.codex-login
CODEX_HOME=/home/app/.codex-login \
    codex login --device-auth -c 'cli_auth_credentials_store="file"' > /tmp/codex-login.out 2>&1 &&
mv /home/app/.codex-login/auth.json /home/app/.codex/auth.json &&
python -c "
import urllib.request as u
t = open('/run/secrets/widget-api-token').read().strip()
u.urlopen(u.Request('http://127.0.0.1:8000/api/v1/usage/refresh', method='POST',
                    headers={'Authorization': 'Bearer ' + t}), timeout=60)
" >/dev/null 2>&1
"""


def log(message: str) -> None:
    print(f"{datetime.now(KST).isoformat(timespec='seconds')} {message}", flush=True)


def secret(name: str) -> str:
    return (STATE / name).read_text().strip()


def compose(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"docker compose {args[0]} failed: {result.stderr.strip()[:200]}")
    return result.stdout


def api(path: str, method: str = "GET") -> dict:
    token = (ROOT / "secrets" / "widget-api-token").read_text().strip()
    request = urllib.request.Request(
        f"{API}{path}", method=method, headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def push(status: str, message: str) -> None:
    query = urllib.parse.urlencode({"status": status, "msg": message[:500]})
    urllib.request.urlopen(f"{secret('kuma-push-url')}?{query}", timeout=15).read()


def discord(text: str) -> None:
    body = json.dumps(
        {"content": text, "username": "ai-usage-monitor", "allowed_mentions": {"parse": []}}
    ).encode()
    request = urllib.request.Request(
        secret("discord-webhook"), data=body,
        headers={"Content-Type": "application/json", "User-Agent": UA},
    )
    urllib.request.urlopen(request, timeout=15).read()


def offer_codex_login() -> tuple[str, str]:
    """Start a device login in the container and return (url, code)."""
    compose("exec", "-d", SERVICE, "sh", "-c", CODEX_LOGIN)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        time.sleep(3)
        out = compose("exec", "-T", SERVICE, "cat", "/tmp/codex-login.out", check=False)
        out = re.sub(r"\x1b\[[0-9;]*m", "", out)
        url = re.search(r"https://auth\.openai\.com/\S+", out)
        code = re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b", out)
        if url and code:
            return url.group(0), code.group(0)
    raise RuntimeError("codex device login printed no code within 45s")


def offer_and_announce(state: dict) -> str:
    """Mint a Codex sign-in code; return where it went, for the Kuma message.

    It goes to Discord through state/discord-webhook when that exists. Without
    one, it rides in the Kuma alert itself, which Kuma sends only when the
    monitor turns DOWN, so later re-offers then reach the log and Kuma's page only.
    """
    url, code = offer_codex_login()
    state["codex_offered_at"] = time.time()
    until = (datetime.now(KST) + timedelta(minutes=15)).strftime("%H:%M")
    log(f"codex sign-in offered: {url} code {code} (until {until} KST)")
    try:
        post_codex_code(url, code, until)
        return f"sign-in code {code} posted to Discord (until {until} KST)"
    except OSError as exc:
        log(f"Discord post skipped ({exc}); the code rides in the Kuma alert")
        return f"sign in at {url} with code {code} (until {until} KST)"


def post_codex_code(url: str, code: str, until: str) -> None:
    discord(
        "🔑 **ai-usage-monitor: the Codex login is dead.** "
        f"Open {url} and enter **`{code}`** (valid until {until} KST). "
        "Collection resumes by itself once you approve. "
        f"Unused, a new code follows in {REOFFER_SECONDS // 3600} h; "
        f"for one now, run `{ROOT}/scripts/auth-watchdog.py --codex-login` on core-apps."
    )


def read_compact(state: dict) -> dict | None:
    """The usage snapshot, or None after one tolerated failure (e.g. a redeploy)."""
    for attempt in range(3):
        try:
            data = api("/usage/compact")
            state["api_failures"] = 0
            return data
        except (OSError, ValueError) as exc:
            error = exc
            time.sleep(5 * (attempt + 1))
    state["api_failures"] = state.get("api_failures", 0) + 1
    if state["api_failures"] < 2:
        return None  # stay silent once; Kuma's own 15-minute window still guards us
    raise RuntimeError(f"usage API unreachable: {error}")


def assess(data: dict) -> tuple[bool, bool, str]:
    """(healthy, codex_login_dead, message) from a compact snapshot."""
    healthy, codex_dead, parts = True, False, []
    for key, label in (("claude", "claude"), ("openai", "codex")):
        provider = data.get(key)
        if not provider:
            continue  # provider disabled
        error = provider.get("error") or ""
        if error.startswith("authentication"):
            healthy = False
            if key == "openai":
                codex_dead = True
                parts.append(f"codex: {error} - {{codex_hint}}")
            else:
                parts.append(f"claude: {error} - run: {CLAUDE_LOGIN}")
        elif provider.get("stale"):
            healthy = False
            parts.append(f"{label}: stale ({error or 'no collection for 30 min'})")
        elif provider.get("status") == "ok":
            parts.append(f"{label}: ok")
        else:
            parts.append(f"{label}: {error} (transient, retrying)")
    return healthy, codex_dead, " · ".join(parts)


def main() -> int:
    STATE.mkdir(mode=0o700, exist_ok=True)
    state_file = STATE / "watchdog.json"
    try:
        state = json.loads(state_file.read_text())
    except (OSError, ValueError):
        state = {}
    flags = set(sys.argv[1:])
    now = time.time()
    exit_code = 0

    if "--test-alert" in flags:
        push("down", "TEST from auth-watchdog - checking the alert path, ignore")
        try:
            discord("🧪 ai-usage-monitor: test of the sign-in notifier - no action needed.")
        except OSError as exc:
            log(f"Discord test skipped: {exc}")
        log("test alert pushed down; this run pushes the real state next")
        time.sleep(3)
    if "--kick" in flags:
        try:
            api("/usage/refresh", method="POST")
        except (OSError, ValueError) as exc:
            log(f"refresh request failed: {exc}")
    if "--codex-login" in flags:
        state["codex_hint"] = offer_and_announce(state)

    try:
        data = read_compact(state)
        if data is None:
            log("usage API unreachable once; not pushing this run")
            status, message = None, ""
        else:
            healthy, codex_dead, message = assess(data)
            if codex_dead:
                if now - state.get("codex_offered_at", 0) > REOFFER_SECONDS:
                    try:
                        state["codex_hint"] = offer_and_announce(state)
                    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                        state["codex_hint"] = (
                            f"auto sign-in failed ({exc}); "
                            f"run {ROOT}/scripts/auth-watchdog.py --codex-login"
                        )
                        log(f"ERROR: codex sign-in offer failed: {exc}")
                message = message.replace("{codex_hint}", state.get("codex_hint", ""))
            else:
                state["codex_offered_at"] = 0
                state.pop("codex_hint", None)
            status = "up" if healthy else "down"
    except RuntimeError as exc:
        status, message = "down", str(exc)

    if status:
        if status != state.get("last_status"):
            log(f"{state.get('last_status', 'unknown')} -> {status}: {message}")
            state["last_status"] = status
        try:
            push(status, message)
        except OSError as exc:
            log(f"ERROR: Kuma push failed: {exc}")
            exit_code = 1

    tmp = state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.chmod(0o600)
    tmp.replace(state_file)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
