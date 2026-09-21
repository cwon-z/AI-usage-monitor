#!/bin/sh
# Keep this server's OWN Codex (ChatGPT) grant alive.
#
# Verified behaviour of Codex CLI 0.150.1 (2026-09-21):
#   * A login grants a ~10-day access token plus a refresh token.
#   * Nothing refreshes on its own: `codex login status` and
#     `account/rateLimits/read` leave even a 9-day-old grant untouched, so an
#     unattended login would simply lapse at the access token's expiry.
#   * app-server `account/read` with `refreshToken: true` refreshes
#     UNCONDITIONALLY and ROTATES both tokens (new ~10-day access token).
#     Like Claude, the grant must therefore have exactly one holder.
#
# So: read the access token's expiry locally (no request), and only under
# THRESHOLD make the one explicit refresh call. No model turn, no quota.
set -eu
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$(dirname "$0")/.."

AUTH=/home/app/.codex/auth.json
THRESHOLD=${THRESHOLD:-259200}   # refresh under 3 days remaining

remaining() {
    docker compose exec -T ai-usage-monitor python -c "
import base64, json, time
t = json.load(open('$AUTH'))['tokens']['access_token'].split('.')[1]
exp = json.loads(base64.urlsafe_b64decode(t + '=' * (-len(t) % 4)))['exp']
print(int(exp - time.time()))
" 2>/dev/null | tr -d '\r'
}

# Drives the documented app-server over stdio against the REAL CODEX_HOME,
# unlike the collector, which copies the tokens with the refresh token removed.
refresh() {
    timeout 90 docker compose exec -T ai-usage-monitor sh -c 'cd /tmp && python -' <<'EOF'
import json, subprocess, sys
p = subprocess.Popen(["codex", "app-server", "--disable", "plugins", "--stdio"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True)
for m in ({"id": 1, "method": "initialize", "params": {
              "clientInfo": {"name": "refresh-codex-auth", "version": "0.1.0"},
              "capabilities": {"experimentalApi": True}}},
          {"method": "initialized"},
          {"id": 2, "method": "account/read", "params": {"refreshToken": True}}):
    p.stdin.write(json.dumps(m) + "\n")
p.stdin.flush()
ok = False
try:
    for line in p.stdout:
        try:
            v = json.loads(line)
        except ValueError:
            continue
        if v.get("id") == 2:
            ok = "result" in v
            break
finally:
    p.stdin.close()
    p.terminate()
    p.wait(10)
sys.exit(0 if ok else 1)
EOF
}

REMAIN=$(remaining || true)
if [ -z "${REMAIN:-}" ]; then
    echo "$(date -Is) ERROR: cannot read $AUTH -- container down, or logged out:"
    echo "    docker compose exec ai-usage-monitor codex login --device-auth"
    exit 1
fi

if [ "$REMAIN" -gt "$THRESHOLD" ]; then
    exit 0   # healthy; stay quiet so the log only records real events
fi

if ! refresh; then
    echo "$(date -Is) ERROR: codex refresh call failed at ${REMAIN}s remaining"
    exit 1
fi

# The refresh is unconditional, so a call that did not extend the expiry failed.
AFTER=$(remaining || true)
if [ -z "${AFTER:-}" ] || [ "$AFTER" -le "$REMAIN" ]; then
    echo "$(date -Is) ERROR: codex at ${AFTER:-?}s remaining and not refreshed -- re-login may be needed:"
    echo "    docker compose exec ai-usage-monitor codex login --device-auth"
    exit 1
fi
echo "$(date -Is) codex refreshed: ${REMAIN}s -> ${AFTER}s"
