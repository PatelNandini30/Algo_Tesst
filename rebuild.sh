#!/bin/bash
# Safe rebuild script — stops containers before building to free RAM,
# then restarts them. Prevents OOM crashes during Docker builds on 16 GB HDD machines.

set -e
cd "$(dirname "$0")"

echo "=============================================="
echo "  AlgoTest - Safe Rebuild"
echo "=============================================="

echo ""
echo "[1/4] Stopping running containers to free RAM..."
docker compose stop
echo "  Done. RAM freed from running containers (~3-4 GB)."

echo ""
echo "[2/4] Building images one group at a time..."
echo "  Building backend + workers (Rust compile — slowest step)..."
docker compose build backend worker-backtests worker-backtests-fast

echo ""
echo "  Building remaining services..."
docker compose build worker-uploads frontend

echo ""
echo "[3/4] Starting all containers..."
docker compose up -d

echo ""
echo "[4/4] Waiting for health checks..."
sleep 10
docker compose ps --format "table {{.Name}}\t{{.Status}}"

echo ""
echo "=============================================="
echo "  Rebuild complete."
echo "  Frontend: http://localhost:3000"
echo "  Backend:  http://localhost:8000"
echo "=============================================="
