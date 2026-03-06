#!/bin/bash
# wait-for.sh - Wait for service to be available
# Usage: ./wait-for.sh host:port [-- timeout] [-- command]

set -e

host="$1"
port="$2"
shift 2
cmd="$@"

timeout=60
if [ "$1" = "--timeout" ]; then
    timeout="$2"
    shift 2
fi

echo "Waiting for $host:$port (timeout: ${timeout}s)..."

for i in $(seq 1 $timeout); do
    if nc -z "$host" "$port" 2>/dev/null; then
        echo "$host:$port is available"
        if [ -n "$cmd" ]; then
            exec $cmd
        fi
        exit 0
    fi
    echo "Waiting... ($i/$timeout)"
    sleep 1
done

echo "Timeout waiting for $host:$port"
exit 1
