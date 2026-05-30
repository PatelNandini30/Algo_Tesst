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

# ── Optional optimizer profile ───────────────────────────────────────────────
# `./start.sh --optimize` also brings up the profile-gated worker-optimize
# container (7000M mem_limit). Omitted by default to protect the 16 GB budget.
COMPOSE_PROFILE_ARGS=""
START_OPTIMIZE=false
for arg in "$@"; do
    case "$arg" in
        --optimize) START_OPTIMIZE=true; COMPOSE_PROFILE_ARGS="--profile optimize" ;;
    esac
done
if [ "$START_OPTIMIZE" = "true" ]; then
    echo ""
    echo "[OPT] Optimizer profile enabled — worker-optimize will be started."
fi

# ── Rust wheel (compiled once, outside Docker, cached in named volumes) ──────
# Rust is compiled by the maturin Docker image and the .whl is saved to
# backend/prebuilt/. Docker never compiles Rust — it just copies the wheel.
# Named volumes (algo_cargo_*) persist the Cargo cache so only changed Rust
# files recompile on subsequent runs (seconds, not minutes).
echo ""
echo "[RUST] Checking native extension wheel..."
mkdir -p backend/prebuilt

RUST_HASH=$(find backend/native/src -name "*.rs" 2>/dev/null | sort | xargs md5sum 2>/dev/null; \
            md5sum backend/native/Cargo.toml backend/native/Cargo.lock 2>/dev/null)
RUST_HASH=$(echo "$RUST_HASH" | md5sum | cut -d' ' -f1)
STORED_RUST_HASH=$(cat .rust_wheel_hash 2>/dev/null || echo "")
WHEEL_EXISTS=$(ls backend/prebuilt/*.whl 2>/dev/null | wc -l)

if [ "$RUST_HASH" != "$STORED_RUST_HASH" ] || [ "$WHEEL_EXISTS" -eq 0 ]; then
    echo "  Native code changed — compiling Rust wheel..."
    rm -f backend/prebuilt/*.whl
    docker run --rm \
        --entrypoint sh \
        -v "$(pwd)/backend/certs/sonicwall-dpi-ssl.crt:/tmp/sonicwall-dpi-ssl.crt:ro" \
        -v "$(pwd)/backend/native:/project" \
        -v "$(pwd)/backend/prebuilt:/output" \
        -v "algo_cargo_registry:/root/.cargo/registry" \
        -v "algo_cargo_git:/root/.cargo/git" \
        -v "algo_native_target:/project/target" \
        -e CARGO_BUILD_JOBS=4 \
        -e CARGO_REGISTRIES_CRATES_IO_PROTOCOL=git \
        -e CARGO_NET_GIT_FETCH_WITH_CLI=true \
        ghcr.io/pyo3/maturin:latest \
        -c "cat /etc/pki/tls/certs/ca-bundle.crt /tmp/sonicwall-dpi-ssl.crt > /tmp/ca.crt && \
               export SSL_CERT_FILE=/tmp/ca.crt CARGO_HTTP_CAINFO=/tmp/ca.crt && \
               git config --global http.sslVerify false && \
               maturin build --release \
                 --interpreter /opt/python/cp311-cp311/bin/python3 \
                 --manifest-path /project/Cargo.toml \
                 --out /output"
    if [ $? -ne 0 ]; then
        echo "ERROR: Rust wheel build failed."
        exit 1
    fi
    echo "$RUST_HASH" > .rust_wheel_hash
    echo "  Wheel ready: $(ls backend/prebuilt/*.whl | xargs basename)"
else
    echo "  Rust wheel up to date — skipping compilation."
    echo "  Wheel: $(ls backend/prebuilt/*.whl 2>/dev/null | xargs basename 2>/dev/null)"
fi
# ────────────────────────────────────────────────────────────────────────────

# ── Base image (pip + system packages only — no Rust) ────────────────────────
# Rebuilds only when requirements.txt or the Rust wheel changes.
echo ""
echo "[BASE] Checking backend base image..."
DEPS_HASH=$(md5sum backend/requirements.txt $(ls backend/prebuilt/*.whl 2>/dev/null) 2>/dev/null | md5sum | cut -d' ' -f1)
STORED_HASH=$(cat .deps_build_hash 2>/dev/null || echo "")

if ! docker image inspect algo-backend-base:latest > /dev/null 2>&1 || [ "$DEPS_HASH" != "$STORED_HASH" ]; then
    echo "  Rebuilding base image (pip + system packages)..."
    docker build -f backend/Dockerfile.base -t algo-backend-base:latest ./backend
    if [ $? -ne 0 ]; then
        echo "ERROR: Base image build failed. Check output above."
        exit 1
    fi
    echo "$DEPS_HASH" > .deps_build_hash
    echo "  Base image ready."
else
    echo "  Base image is current — skipping rebuild."
fi
# ────────────────────────────────────────────────────────────────────────────

# Check if containers are already healthy
echo ""
echo "[0/5] Checking existing containers..."
ALL_HEALTHY=true
for container in backend frontend postgres redis worker-backtests worker-backtests-fast worker-uploads; do
    status=$(docker compose ps -q $container 2>/dev/null)
    if [ ! -z "$status" ]; then
        health=$(docker inspect --format='{{.State.Health.Status}}' $container 2>/dev/null)
        if [ "$health" = "healthy" ]; then
            echo "  $container: healthy"
        else
            echo "  $container: not healthy (will restart)"
            ALL_HEALTHY=false
        fi
    else
        echo "  $container: not running (will start)"
        ALL_HEALTHY=false
    fi
done

# Only stop containers if not all are healthy
if [ "$ALL_HEALTHY" = "true" ]; then
    echo ""
    echo "All containers are healthy! Skipping restart."
    if [ "$START_OPTIMIZE" = "true" ]; then
        echo "Ensuring optimizer container is up..."
        docker compose --profile optimize up -d worker-optimize
    fi
    echo "To force restart, run: docker compose down && docker compose up -d"
    exit 0
fi

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

# Build the frontend bundle on the host BEFORE docker compose builds the
# image.  The frontend Dockerfile only COPYs ./frontend/dist (npm install
# OOM-crashes inside the container on this 16GB HDD host — see the comment
# at the top of frontend/Dockerfile).  Running `npm run build` here keeps
# source changes in frontend/src/ flowing through to the deployed bundle.
echo ""
echo "[1.7/5] Building frontend bundle (vite)..."
if command -v npm >/dev/null 2>&1; then
    if [ ! -d "frontend/node_modules" ]; then
        echo "  Installing frontend dependencies (one-time)..."
        (cd frontend && npm install --no-audit --no-fund) || {
            echo "  WARNING: npm install failed — bundle may be stale"
        }
    fi
    (cd frontend && npm run build) || {
        echo "  WARNING: npm run build failed — using existing frontend/dist"
    }
else
    echo "  WARNING: npm not found on host — skipping frontend build"
    echo "  (Docker image will use whatever is already in frontend/dist)"
fi

# Build and start services
echo ""
echo "[2/5] Building and starting Docker services..."
docker compose $COMPOSE_PROFILE_ARGS up -d --build

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
LOG_SERVICES=(
  backend
  worker-backtests
  worker-backtests-fast
  worker-uploads
  frontend
  postgres
  redis
)
if [ "$START_OPTIMIZE" = "true" ]; then
  LOG_SERVICES+=(worker-optimize)
fi
docker compose $COMPOSE_PROFILE_ARGS logs -f "${LOG_SERVICES[@]}"
