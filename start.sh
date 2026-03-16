#!/bin/bash
# AlgoTest - Start All Services

echo "=============================================="
echo "  AlgoTest - Starting All Services"
echo "=============================================="

# Check if docker is running
if ! docker info > /dev/null 2>&1; then
    echo "Error: Docker is not running. Please start Docker first."
    exit 1
fi

# Navigate to project directory
cd "$(dirname "$0")"

# Stop any existing containers
echo ""
echo "[1/5] Stopping existing containers..."
docker compose down 2>/dev/null

# Build and start services
echo ""
echo "[2/5] Building and starting Docker services..."
docker compose up -d --build

# Wait for services to be healthy
echo ""
echo "[3/5] Waiting for services to become healthy..."
sleep 5

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