#!/usr/bin/env bash
# Touch all DaySnapshot files for the current and prior year into OS page cache.
# Run at 06:00 IST (00:30 UTC) before market open.
set -euo pipefail

INTRADAY_DIR="${INTRADAY_DATA_DIR:-/data/intraday}"
CURRENT_YEAR=$(date +%Y)
PRIOR_YEAR=$((CURRENT_YEAR - 1))
SYMBOLS="NIFTY BANKNIFTY FINNIFTY MIDCPNIFTY"

echo "[vmtouch] warming snapshot cache at $(date)"
for SYMBOL in $SYMBOLS; do
    for YEAR in $CURRENT_YEAR $PRIOR_YEAR; do
        SNAP_DIR="$INTRADAY_DIR/$SYMBOL/snapshots"
        if [ -d "$SNAP_DIR" ]; then
            FILES=$(find "$SNAP_DIR" -maxdepth 1 -name "${YEAR}-*.arrow" 2>/dev/null | wc -l)
            if [ "$FILES" -gt 0 ]; then
                # shellcheck disable=SC2086
                vmtouch -t "$SNAP_DIR"/${YEAR}-*.arrow 2>/dev/null || true
                echo "[vmtouch] $SYMBOL $YEAR: $FILES files touched"
            fi
        fi
    done
done
echo "[vmtouch] done at $(date)"
