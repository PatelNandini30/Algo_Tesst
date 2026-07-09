#!/usr/bin/env bash
# Auto-update the remote-worker image tar whenever the backend code changes.
#
# The remotes run a BAKED image (no bind-mount), so every backend edit needs the
# image re-exported to remote-worker/algotest-worker-image.tar before the remotes
# can pick it up. This watches backend/ and does that automatically — you still
# copy the tar to each remote (or the version guard flags them stale until you
# do), but you never have to remember to `docker save` again.
#
# Poll-based (no inotify dependency). Debounced: only rebuilds after edits have
# settled for one poll interval, so a burst of saves triggers ONE rebuild, not many.
#
# Run in the background from the repo root:
#   nohup ./remote-worker/watch-and-export.sh > /tmp/remote-tar-watch.log 2>&1 &
# Watch it:      tail -f /tmp/remote-tar-watch.log
# Stop it:       pkill -f watch-and-export.sh
#
# Env: WATCH_INTERVAL (seconds between polls, default 10).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1   # repo root

INTERVAL="${WATCH_INTERVAL:-10}"
TAR="remote-worker/algotest-worker-image.tar"

fingerprint() {
  # mtime+path of every backend source file (py + Dockerfiles), excluding caches
  # and Rust build artifacts (those aren't in the image build context anyway).
  find backend -type f \( -name '*.py' -o -name 'Dockerfile*' \) \
    -not -path '*/__pycache__/*' -not -path '*/native/target/*' \
    -printf '%T@ %p\n' 2>/dev/null | sort | sha256sum | cut -d' ' -f1
}

rebuild() {
  echo "[watch] $(date '+%F %T') change settled — rebuilding image + tar..."
  if ! docker compose build backend > /tmp/remote-tar-build.log 2>&1; then
    echo "[watch] $(date '+%F %T') BUILD FAILED — keeping previous tar. See /tmp/remote-tar-build.log"
    return 1
  fi
  docker save algotest-backend-app:latest -o "$TAR"
  local ver
  ver="$(docker run --rm --entrypoint python3 algotest-backend-app:latest \
        -c 'from services.code_version import compute_code_version; print(compute_code_version())' 2>/dev/null | tail -1)"
  echo "[watch] $(date '+%F %T') tar updated — version ${ver} — size $(du -h "$TAR" | cut -f1)"
  echo "[watch]   → copy $TAR to each remote, then 'docker load' + 'docker compose up -d' there"
  return 0
}

echo "[watch] started $(date '+%F %T'); polling backend/ every ${INTERVAL}s (debounced)."
LAST=""       # fingerprint of the last successfully built tar
PENDING=""    # fingerprint seen on the previous poll (for debounce)
while true; do
  CUR="$(fingerprint)"
  # Rebuild only once the code has been STABLE for a full interval (CUR==PENDING)
  # and differs from what's already in the tar (CUR!=LAST).
  if [ "$CUR" = "$PENDING" ] && [ "$CUR" != "$LAST" ]; then
    if rebuild; then LAST="$CUR"; fi
  fi
  PENDING="$CUR"
  sleep "$INTERVAL"
done
