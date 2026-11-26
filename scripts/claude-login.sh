#!/bin/sh
# Re-create this server's own Claude login. Interactive: run it as
#   ssh -t <host> /path/to/ai-usage-monitor/scripts/claude-login.sh
#
# Claude Code 2.1.266 has no device-code flow: open the printed URL, sign in,
# and paste the code it shows back here. Afterwards the watchdog collects at
# once and turns the Kuma monitor green, instead of waiting up to 5 minutes.
set -eu
cd "$(dirname "$0")/.."
docker compose exec ai-usage-monitor claude auth login --claudeai
exec python3 scripts/auth-watchdog.py --kick
