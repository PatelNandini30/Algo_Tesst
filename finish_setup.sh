#!/usr/bin/env bash
###############################################################################
# AlgoTest — autonomous finish-setup script (runs as root via sudo)
# Restores PostgreSQL, restores algo_cache volume, builds & starts all
# containers, then verifies. Idempotent-ish and safe to re-run.
# Progress -> /home/aff34/Downloads/Algo_Test_Software/setup.log
###############################################################################
set -o pipefail

APP="/home/aff34/Downloads/Algo_Test_Software"
STAGE="/home/aff34/Downloads/algo_backup"
LOG="$APP/setup.log"
PROJECT="algo_test_software"            # compose project name (dir basename)
CACHE_VOL="${PROJECT}_algo_cache"

cd "$APP" || { echo "FATAL: cannot cd to $APP"; exit 1; }

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

DOCKER="docker"                         # script runs as root → socket access OK
DC="$DOCKER compose"

if ! $DOCKER info >/dev/null 2>&1; then
    log "FATAL: docker not reachable even as root. Aborting."
    exit 1
fi
log "############################################################"
log "Starting finish_setup.sh as $(id -un). docker server $($DOCKER version --format '{{.Server.Version}}' 2>/dev/null)"

###############################################################################
# 1. Start ONLY postgres, wait until healthy
###############################################################################
log "=== STEP 1: starting postgres ==="
$DC up -d postgres >>"$LOG" 2>&1

log "Waiting for postgres to accept connections (up to ~5 min)..."
ok=0
for i in $(seq 1 60); do
    if $DOCKER exec algotest-postgres pg_isready -U algotest >/dev/null 2>&1; then
        ok=1; log "postgres ready after ~$((i*10))s"; break
    fi
    sleep 10
done
[ "$ok" = "1" ] || { log "FATAL: postgres never became ready"; $DC ps >>"$LOG" 2>&1; exit 1; }

###############################################################################
# 2. Restore the database (skip if option_data already populated)
###############################################################################
log "=== STEP 2: restoring PostgreSQL database ==="
EXISTING=$($DOCKER exec algotest-postgres psql -U algotest -d algotest -tAc \
    "SELECT to_regclass('public.option_data') IS NOT NULL;" 2>/dev/null | tr -d '[:space:]')
ROWS=0
if [ "$EXISTING" = "t" ]; then
    ROWS=$($DOCKER exec algotest-postgres psql -U algotest -d algotest -tAc \
        "SELECT count(*) FROM public.option_data;" 2>/dev/null | tr -d '[:space:]')
    [ -z "$ROWS" ] && ROWS=0
fi

if [ "${ROWS:-0}" -gt 0 ]; then
    log "option_data already has $ROWS rows — skipping restore."
else
    log "Restoring from $STAGE/pgdump.sql.gz (2.9GB; 10-30 min)..."
    gunzip -c "$STAGE/pgdump.sql.gz" \
        | $DOCKER exec -i algotest-postgres psql -v ON_ERROR_STOP=0 -U algotest -d algotest \
        > "$APP/db_restore.log" 2>&1
    rc=$?
    log "DB restore finished (rc=$rc). Tail of db_restore.log:"
    tail -4 "$APP/db_restore.log" | tee -a "$LOG"
    NEW=$($DOCKER exec algotest-postgres psql -U algotest -d algotest -tAc \
        "SELECT count(*) FROM public.option_data;" 2>/dev/null | tr -d '[:space:]')
    log "option_data now has ${NEW:-unknown} rows."
fi

###############################################################################
# 3. Restore algo_cache volume (skip if non-empty)
###############################################################################
log "=== STEP 3: restoring algo_cache volume ($CACHE_VOL) ==="
$DOCKER volume create "$CACHE_VOL" >/dev/null 2>&1
CACHE_FILES=$($DOCKER run --rm -v "$CACHE_VOL":/data alpine sh -c 'ls -A /data 2>/dev/null | wc -l' 2>/dev/null | tr -d '[:space:]')
if [ "${CACHE_FILES:-0}" -gt 0 ]; then
    log "algo_cache already populated ($CACHE_FILES entries) — skipping."
else
    log "Extracting algo_cache.tar.gz into volume..."
    $DOCKER run --rm -v "$CACHE_VOL":/data -v "$STAGE":/backup \
        alpine tar xzf /backup/algo_cache.tar.gz -C /data >>"$LOG" 2>&1
    log "algo_cache restore done."
fi

###############################################################################
# 4. Build & start the whole stack (default profile; NOT worker-optimize)
###############################################################################
log "=== STEP 4: building & starting all containers (first build ~10-15 min) ==="
$DC up -d --build >>"$LOG" 2>&1
log "compose up returned rc=$?."

###############################################################################
# 5. Wait for backend health, then verify
###############################################################################
log "=== STEP 5: waiting for backend /health ==="
bok=0
for i in $(seq 1 90); do
    if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
        bok=1; log "backend /health OK after ~$((i*10))s"; break
    fi
    sleep 10
done
[ "$bok" = "1" ] || log "WARNING: backend /health not up yet — see 'docker compose logs backend'"

log "=== FINAL STATE ==="
$DC ps >>"$LOG" 2>&1
$DC ps
log "option_data rows: $($DOCKER exec algotest-postgres psql -U algotest -d algotest -tAc 'SELECT count(*) FROM public.option_data;' 2>/dev/null | tr -d '[:space:]')"
FR=$(curl -fsS -o /dev/null -w '%{http_code}' http://localhost:3000 2>/dev/null)
BK=$(curl -fsS -o /dev/null -w '%{http_code}' http://localhost:8000/health 2>/dev/null)
log "Frontend http://localhost:3000 -> HTTP ${FR:-down} ; Backend /health -> HTTP ${BK:-down}"
log "=== finish_setup.sh COMPLETE ==="
