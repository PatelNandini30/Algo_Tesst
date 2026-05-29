#!/bin/bash
set -euo pipefail

BACKUP_BASE="/run/user/1000/gvfs/smb-share:server=192.168.4.50,share=share/Nandini Patel/Algo_Test_Backup"
BACKUP_DIR="$BACKUP_BASE/$(date +%Y-%m-%d)"
PROJECT="/home/user/Algo_Test_Software"

echo ""
echo "======================================================"
echo "  Algo Test Software — Full Backup"
echo "  Destination: $BACKUP_DIR"
echo "======================================================"
echo ""

mkdir -p "$BACKUP_DIR"

# ── 1. PostgreSQL dump ─────────────────────────────────────
echo "[1/5] PostgreSQL dump (this is the big one, ~5-10 min)..."
docker compose -f "$PROJECT/docker-compose.yml" exec -T postgres \
    pg_dump -U algotest --no-password algotest \
    | gzip > "$BACKUP_DIR/pgdump.sql.gz"
echo "      Done: $(du -sh "$BACKUP_DIR/pgdump.sql.gz" | cut -f1)"

# ── 2. Source code ─────────────────────────────────────────
echo "[2/5] Source code (excluding node_modules, build artifacts)..."
tar czf "$BACKUP_DIR/source_code.tar.gz" \
    --exclude="$PROJECT/node_modules" \
    --exclude="$PROJECT/frontend/node_modules" \
    --exclude="$PROJECT/__pycache__" \
    --exclude="$PROJECT/backend/native/target" \
    --exclude="$PROJECT/cleaned_csvs" \
    --exclude="$PROJECT/.git" \
    --exclude="$PROJECT/graphify-out" \
    --exclude="$PROJECT/Output" \
    -C "$(dirname "$PROJECT")" "$(basename "$PROJECT")"
echo "      Done: $(du -sh "$BACKUP_DIR/source_code.tar.gz" | cut -f1)"

# ── 3. cleaned_csvs ────────────────────────────────────────
echo "[3/5] cleaned_csvs (14 GB raw → ~1-2 GB compressed, ~15 min)..."
tar czf "$BACKUP_DIR/cleaned_csvs.tar.gz" \
    -C "$PROJECT" cleaned_csvs/
echo "      Done: $(du -sh "$BACKUP_DIR/cleaned_csvs.tar.gz" | cut -f1)"

# ── 4. Small data directories ──────────────────────────────
echo "[4/5] Small data dirs (expiryData, strikeData, Filter, reports, sample data)..."
tar czf "$BACKUP_DIR/other_data.tar.gz" \
    -C "$PROJECT" \
    expiryData/ strikeData/ Filter/ reports/ "sample data/" 2>/dev/null || \
tar czf "$BACKUP_DIR/other_data.tar.gz" \
    -C "$PROJECT" \
    expiryData/ strikeData/ Filter/ reports/
echo "      Done: $(du -sh "$BACKUP_DIR/other_data.tar.gz" | cut -f1)"

# ── 5. algo_cache Docker volume ────────────────────────────
echo "[5/5] algo_cache volume (Arrow/feather cache)..."
docker run --rm \
    -v algo_test_software_algo_cache:/data \
    alpine tar czf - -C /data . \
    > "$BACKUP_DIR/algo_cache.tar.gz"
echo "      Done: $(du -sh "$BACKUP_DIR/algo_cache.tar.gz" | cut -f1)"

# ── Summary ────────────────────────────────────────────────
echo ""
echo "======================================================"
echo "  Backup complete!"
echo "  Location: $BACKUP_DIR"
echo ""
du -sh "$BACKUP_DIR"/* | sort -h
echo "======================================================"
