#!/bin/bash
# AlgoTest - Start All Services

echo "=============================================="
echo "  AlgoTest - Starting All Services"
echo "=============================================="

# Check if docker is running, start if not
if ! docker info > /dev/null 2>&1; then
    echo "Docker is not running. Attempting to start Docker..."
    # Clean stale PID if exists
    if [ -f /var/run/docker.pid ]; then
        pid=$(cat /var/run/docker.pid)
        if ! ps -p $pid > /dev/null 2>&1; then
            sudo rm -f /var/run/docker.pid
        fi
    fi
    sudo systemctl reset-failed docker.service 2>/dev/null
    sudo systemctl start docker
    sleep 3
    if ! docker info > /dev/null 2>&1; then
        echo "Error: Could not start Docker. Please start it manually."
        exit 1
    fi
    echo "Docker started successfully!"
fi

# Navigate to project directory
cd "$(dirname "$0")"

# Stop any existing containers
echo ""
echo "[1/5] Stopping existing containers..."
docker compose down --remove-orphans 2>/dev/null

# Free up required ports
echo ""
echo "[1.5/5] Freeing up required ports..."
for port in 5432 6379 8000 3000; do
    pid=$(sudo lsof -t -i :$port 2>/dev/null)
    if [ ! -z "$pid" ]; then
        echo "  Killing process on port $port (PID: $pid)"
        sudo kill -9 $pid 2>/dev/null
    else
        echo "  Port $port is free"
    fi
done

# Stop system services that conflict with Docker
echo ""
echo "  Stopping conflicting system services..."
sudo systemctl stop postgresql 2>/dev/null && echo "  Stopped system PostgreSQL" || true
sudo systemctl stop redis 2>/dev/null && echo "  Stopped system Redis" || true
sudo systemctl stop redis-server 2>/dev/null && echo "  Stopped system Redis-server" || true
sleep 2

# Verify ports are free
echo ""
echo "  Verifying ports are free..."
for port in 5432 6379 8000 3000; do
    pid=$(sudo lsof -t -i :$port 2>/dev/null)
    if [ ! -z "$pid" ]; then
        echo "  WARNING: Port $port still in use by PID $pid"
    else
        echo "  Port $port OK"
    fi
done

# Build and start services
echo ""
echo "[2/5] Building and starting Docker services..."
docker compose up -d --build

if [ $? -ne 0 ]; then
    echo ""
    echo "ERROR: Failed to start services. Check logs above."
    exit 1
fi

# Wait for services to be healthy
echo ""
echo "[3/5] Waiting for services to become healthy..."
sleep 10

# Check service status
echo ""
echo "[4/5] Service Status:"
docker compose ps

# Show logs
echo ""
echo "[5/5] Showing logs (Ctrl+C to stop watching)..."
echo "=============================================="
echo ""
docker compose logs -f
