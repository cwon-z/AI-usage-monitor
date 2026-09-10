#!/bin/sh
# Keep this server's OWN Claude OAuth grant alive.
#
# Verified behaviour of Claude Code 2.1.266 (2026-09-10):
#   * `claude auth status` does NOT refresh -- it only reports the stored file.
#   * An authenticated API call refreshes, but ONLY when the token is nearly
#     expired: declined at 30 min remaining, refreshed at 5 min and when
#     already expired. Each refresh buys ~8h and ROTATES the refresh token,
#     which is why this server holds its own grant and never shares the Mac's.
#
# So: poll cheaply (a file read, no quota), and only when inside the CLI's own
# refresh window make one trivial Haiku call purely as the trigger.
set -eu
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd "$(dirname "$0")/.."

CRED=/home/app/.claude/.credentials.json
THRESHOLD=${THRESHOLD:-600}   # attempt a refresh under 10 minutes remaining
FLOOR=60                      # below this, a non-refresh is a real failure

remaining() {
    docker compose exec -T ai-usage-monitor python -c "
import json, datetime
o = json.load(open('$CRED'))['claudeAiOauth']
print(int(o['expiresAt']/1000 - datetime.datetime.now(datetime.UTC).timestamp()))
" 2>/dev/null | tr -d '\r'
}

REMAIN=$(remaining || true)
if [ -z "${REMAIN:-}" ]; then
    echo "$(date -Is) ERROR: cannot read $CRED -- container down, or logged out"
    exit 1
fi

if [ "$REMAIN" -gt "$THRESHOLD" ]; then
    exit 0   # healthy; stay quiet so the log only records real events
fi

if ! docker compose exec -T ai-usage-monitor sh -c \
        'cd /tmp && claude -p "ok" --model claude-haiku-4-5-20251001' >/dev/null 2>&1; then
    echo "$(date -Is) ERROR: trigger call failed at ${REMAIN}s remaining"
    exit 1
fi

AFTER=$(remaining || true)
if [ -z "${AFTER:-}" ]; then
    echo "$(date -Is) ERROR: credentials unreadable after trigger call"
    exit 1
fi

if [ "$AFTER" -gt "$REMAIN" ]; then
    echo "$(date -Is) refreshed: ${REMAIN}s -> ${AFTER}s"
elif [ "$AFTER" -le "$FLOOR" ]; then
    echo "$(date -Is) ERROR: ${AFTER}s remaining and the CLI did not refresh -- re-login may be needed:"
    echo "    docker compose exec ai-usage-monitor claude auth login --claudeai"
    exit 1
fi
# Otherwise the CLI judged it too early to refresh; the next run will retry.
