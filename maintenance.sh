#!/usr/bin/env bash
# Show / clear the full-screen maintenance overlay in the UI.
#
# The overlay is now ONLY shown when this flag is set. It is never inferred from
# slow or failed health checks — a heavy sweep used to starve /health past the
# client timeout, and users got a "Server is restarting…" screen while nothing
# had restarted and their jobs were running fine.
#
#   ./maintenance.sh on  ["Rebuilding backend — back in ~10 min"]
#   ./maintenance.sh off
#   ./maintenance.sh status
#
# Takes effect within ~2s (the UI polls /health at that interval). Note this
# only blocks NEW submissions from the UI; jobs already running are unaffected.
set -euo pipefail
cd "$(dirname "$0")"

KEY="algotest:maintenance"
action="${1:-status}"

case "$action" in
  on)
    msg="${2:-The system is being updated — please wait.}"
    docker compose exec -T redis redis-cli set "$KEY" "$msg" >/dev/null
    echo "maintenance ON  — UI will show: $msg"
    ;;
  off)
    docker compose exec -T redis redis-cli del "$KEY" >/dev/null
    echo "maintenance OFF — UI unlocked"
    ;;
  status)
    cur=$(docker compose exec -T redis redis-cli get "$KEY" 2>/dev/null | tr -d '\r')
    if [ -z "$cur" ]; then echo "maintenance OFF"; else echo "maintenance ON: $cur"; fi
    ;;
  *)
    echo "usage: $0 {on [message]|off|status}" >&2; exit 2
    ;;
esac
