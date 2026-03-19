#!/bin/bash
# Save as:   ~/monitor.sh
# Make exec: chmod +x ~/monitor.sh
# Run:       ./monitor.sh
while true; do
    clear
    echo '╔══════════════════════════════════════════════╗'
    echo '║      AlgoTest Real-Time Monitor              ║'
    echo '╚══════════════════════════════════════════════╝'
    echo ''
    echo '── HOST MEMORY ──────────────────────────────────'
    free -h
    echo ''
    echo '── DOCKER CONTAINERS ───── (MEM USAGE / LIMIT) ──'
    docker stats --no-stream --format \
        'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}'
    echo ''
    echo '── REDIS ────────────────────────────────────────'
    docker exec algotest-redis redis-cli info memory 2>/dev/null \
        | grep -E 'used_memory_human|maxmemory_human'
    echo ''
    echo '── CELERY QUEUE DEPTH ───────────────────────────'
    BT=$(docker exec algotest-redis redis-cli llen backtests 2>/dev/null)
    UP=$(docker exec algotest-redis redis-cli llen uploads 2>/dev/null)
    echo "  Backtest queue : ${BT:-0} pending tasks"
    echo "  Upload queue   : ${UP:-0} pending tasks"
    echo ''
    echo '── LIVE STATS ENDPOINT ──────────────────────────'
    curl -s http://localhost:8000/health/stats 2>/dev/null \
        | python3 -m json.tool 2>/dev/null | head -20 || echo '  backend not ready'
    echo ''
    echo '  Refreshing in 10s — Ctrl+C to exit'
    sleep 10
done
