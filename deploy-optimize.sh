#!/usr/bin/env bash
# Guarded deploy for worker-optimize: refuses to recreate/restart the container
# while any optim is active or queued, so a code deploy can never SIGTERM a
# running 30k-combo sweep out from under it.
#
#   ./deploy-optimize.sh                 # up -d worker-optimize, only if idle
#   ./deploy-optimize.sh restart         # restart instead of up -d
#   ./deploy-optimize.sh --force up -d   # skip the guard (you accept the kill)
#
# The in-container supervisor's idle-guard can't see an external `docker compose
# up -d`; this wrapper is the only thing that can. Same check it uses: no active
# task on the worker AND an empty optimize queue.
set -euo pipefail
cd "$(dirname "$0")"

FORCE=0
if [ "${1:-}" = "--force" ]; then FORCE=1; shift; fi
# Default action if none given.
ACTION=("${@:-up -d worker-optimize}")
[ $# -eq 0 ] && ACTION=(up -d worker-optimize)

active=$(docker compose exec -T worker-optimize celery -A worker inspect active 2>/dev/null | grep -c run_optimize_job || true)
queued=$(docker compose exec -T redis redis-cli LLEN optimize 2>/dev/null | tr -d '\r' || echo 0)
active=${active:-0}; queued=${queued:-0}

if [ "$FORCE" -ne 1 ] && { [ "$active" -gt 0 ] || [ "$queued" -gt 0 ]; }; then
  echo "REFUSING deploy: $active optim(s) active, $queued queued."
  echo "A recreate/restart would SIGTERM them. Wait until both are 0, or re-run with --force."
  exit 1
fi

echo "worker-optimize idle (active=$active queued=$queued) — deploying: docker compose ${ACTION[*]}"
exec docker compose "${ACTION[@]}"
