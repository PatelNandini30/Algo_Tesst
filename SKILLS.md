# AlgoTest Clone — Infrastructure SKILLS.md
# Senior Engineering Playbook | 16 GB Ubuntu Desktop | 1000+ Concurrent Users
# No Prometheus / No Grafana | Single-Machine Server
# ─────────────────────────────────────────────────────────────────────────────
# SKILLS COVERED IN THIS DOCUMENT
#   SKILL-01  Dockerfile.backend   — Multi-stage Python build
#   SKILL-02  Dockerfile.frontend  — Multi-stage Node + nginx build
#   SKILL-03  nginx.conf           — Production-hardened reverse proxy
#   SKILL-04  docker-compose.yml   — Memory-limited orchestration
#   SKILL-05  Ubuntu OS Tuning     — Swap, cgroups, sysctl
#   SKILL-06  FastAPI Backend      — Performance configuration
#   SKILL-07  Celery Workers       — Memory-safe concurrency
#   SKILL-08  Redis                — Capped cache with LRU eviction
#   SKILL-09  PostgreSQL           — Right-sized for 16 GB machine
#   SKILL-10  Monitoring           — Zero-overhead built-in stats
#   SKILL-11  Deployment Runbook   — Ordered steps, verification
# ─────────────────────────────────────────────────────────────────────────────


# ═══════════════════════════════════════════════════════════════════════════════
# MEMORY BUDGET — NON-NEGOTIABLE
# 16 GB Total RAM
#   - Ubuntu Desktop GUI + kernel:   1.5 GB (always reserved)
#   - Free safety buffer:            3.0 GB (NEVER touch swap)
#   - Available for Docker:         11.5 GB
#   - Allocated to Docker below:     9.3 GB
#   - Remaining headroom:            2.2 GB
# ═══════════════════════════════════════════════════════════════════════════════

# Service          | Memory Limit | CPU Limit | Rationale
# ─────────────────|──────────────|───────────|──────────────────────────────
# postgres         |    3500 MB   |   2.0     | shared_buffers=512MB (not 2GB)
# redis            |     512 MB   |   0.5     | maxmemory 450mb + allkeys-lru
# backend          |    1024 MB   |   1.5     | 2 uvicorn workers × ~400MB
# worker-backtests |    3000 MB   |   4.0     | 3 Celery procs × ~700MB peak
# worker-uploads   |    1000 MB   |   1.0     | 2 Celery procs × ~400MB
# frontend         |     256 MB   |   0.5     | nginx serving static files
# ─────────────────|──────────────|───────────|──────────────────────────────
# TOTAL DOCKER     |    9292 MB   |   9.5     |
# OS + Buffer      |    4708 MB   |   2.5     | 3 GB safe, never swaps


# ═══════════════════════════════════════════════════════════════════════════════
# SKILL-01: Dockerfile.backend
# File location in repo: backend/Dockerfile
# ═══════════════════════════════════════════════════════════════════════════════

## WHY MULTI-STAGE?

A single-stage Python build ships gcc, g++, libpq-dev, and all compile
artifacts into the final image. These add 300-500 MB to image size and
increase the attack surface. Multi-stage solves this:

  Stage 1 (builder):  python:3.11-slim + compilers → builds .whl files
  Stage 2 (runtime):  python:3.11-slim + no compilers → installs from .whl

The runtime image has ZERO compile tools. It cannot compile or install
arbitrary packages at runtime — critical for production security.

## BASE IMAGE: python:3.11-slim vs python:3.11

  python:3.11       → 1.1 GB   (Debian full)
  python:3.11-slim  → 150 MB   (Debian slim, no locale, docs, extras)

On a 16 GB machine, Docker stores all image layers in the overlay filesystem
backed by RAM (page cache). Smaller images = less RAM consumed for image data.

## LAYER CACHING STRATEGY

  # CORRECT order — stable layers first, volatile layers last
  COPY requirements.txt .       ← rarely changes → cached most of the time
  RUN pip wheel ... -r reqs.txt ← only re-runs when requirements.txt changes
  COPY . .                      ← changes every commit → always re-runs
  RUN mkdir ...                 ← changes with COPY . . → always re-runs

  # WRONG order — invalidates cache on every code change
  COPY . .
  COPY requirements.txt .
  RUN pip install ...   ← re-runs every time ANY file changes

## KEY ENV VARS EXPLAINED

  MALLOC_ARENA_MAX=2
    glibc default: creates up to 8 memory arenas per CPU core
    On 12-core machine: up to 96 arenas, each holding fragmented blocks
    These blocks are never returned to OS → RSS grows indefinitely
    Fix: cap at 2 arenas → memory is returned to OS between backtests

  PYTHONOPTIMIZE=1
    Strips assert statements from bytecode
    Reduces interpreter overhead in tight loops (backtest computation)

  PYTHONDONTWRITEBYTECODE=1
    Prevents .pyc file creation inside container
    Avoids unnecessary disk writes on every import

  UVICORN_WORKERS=2
    2 workers × ~400 MB = ~800 MB — fits within 1024 MB container limit
    Doubles throughput for lightweight endpoints without doubling memory
    Backtest CPU work always goes to Celery — never uvicorn workers

## NON-ROOT USER

  Running as root inside Docker is a security anti-pattern.
  If an attacker achieves RCE, they get root inside the container.
  The appuser (uid 1001) has no shell, no sudo, no home directory writes.
  Files in /app are readable by appuser but owned by root — cannot be modified.

## HEALTHCHECK

  CMD curl --fail --silent http://localhost:8000/health || exit 1

  Docker marks container "healthy" only after this passes.
  The frontend service has depends_on: backend: condition: service_healthy
  → Frontend never starts until backend is confirmed running.
  --fail: returns exit code 1 on HTTP 4xx/5xx (not just connection errors)

## COMPLETE FILE → backend/Dockerfile
## (see Dockerfile.backend in this repo)


# ═══════════════════════════════════════════════════════════════════════════════
# SKILL-02: Dockerfile.frontend
# File location in repo: frontend/Dockerfile
# ═══════════════════════════════════════════════════════════════════════════════

## WHY MULTI-STAGE?

Node build process:
  - npm ci installs node_modules: 200-500 MB
  - vite build compiles React to /dist: 1-5 MB of static files
  - node_modules are never needed at runtime

Without multi-stage: image ships 500 MB of node_modules to production.
With multi-stage: image ships only the 1-5 MB /dist output.

  Stage 1 (node-builder):  node:20-alpine → npm ci → vite build → /dist
  Stage 2 (nginx-runtime): nginx:1.25-alpine → copy /dist → serve

Final image size: ~45 MB (vs ~600 MB without multi-stage)

## BASE IMAGES

  node:20-alpine   → 180 MB  (Alpine musl libc, no systemd, minimal)
  nginx:1.25-alpine → 40 MB  (nginx on Alpine)

Alpine uses musl instead of glibc. All npm packages and nginx work correctly.
Do NOT use Alpine for Python (musl incompatibilities with many C extensions).

## npm ci vs npm install

  npm install  → can modify package-lock.json, skips missing deps silently
  npm ci       → uses package-lock.json exactly, fails if out of sync

In CI/CD and Docker, always use npm ci for reproducible builds.
--prefer-offline: use cached packages if available (speeds up rebuilds)
--no-audit: skip vulnerability scan during build (run separately in CI)
--no-fund: suppress funding messages in build logs

## VITE BUILD OUTPUT

Vite generates:
  /dist/index.html                      ← entry point (never cached)
  /dist/assets/main.a3f8c2d1.js        ← hashed bundle (cached forever)
  /dist/assets/vendor.b9e12345.css     ← hashed vendor styles (cached forever)
  /dist/assets/logo.4f1e2c3d.svg       ← hashed asset (cached forever)

Content hashing: filename changes when content changes.
This enables aggressive browser caching (Cache-Control: immutable) while
guaranteeing fresh assets after every deployment.

## NGINX DAEMON OFF

  CMD ["nginx", "-g", "daemon off;"]

By default nginx forks and runs as a background daemon.
In Docker, PID 1 must be the main process.
If PID 1 exits, Docker considers the container dead.
"daemon off" keeps nginx as PID 1 — Docker correctly detects crashes.

## COMPLETE FILE → frontend/Dockerfile
## (see Dockerfile.frontend in this repo)


# ═══════════════════════════════════════════════════════════════════════════════
# SKILL-03: nginx.conf
# File location in repo: frontend/nginx.conf
# ═══════════════════════════════════════════════════════════════════════════════

## WORKER CONFIGURATION

  worker_processes auto;        → 12 workers on 12-core machine
  worker_connections 4096;      → 12 × 4096 = 49,152 max connections
  worker_rlimit_nofile 65536;   → matches /etc/docker/daemon.json ulimit

  12 × 4096 = 49,152 simultaneous connections
  1000 users × 4 connections each = 4,000 connections
  49,152 >> 4,000 → massive headroom, never a bottleneck

## epoll EVENT MODEL

  use epoll;

  poll/select: O(n) scan of all file descriptors per event loop tick
  epoll:       O(1) event notification regardless of descriptor count

  At 1000+ connections, poll would spend 80% of CPU scanning descriptors.
  epoll processes all events in constant time — mandatory for high concurrency.

## UPSTREAM KEEPALIVE — MOST IMPORTANT SETTING

  upstream backend_pool {
      server backend:8000;
      keepalive 32;
  }

  Without keepalive: every request to /api/* creates a new TCP connection
    1. TCP SYN → backend
    2. TCP SYN-ACK ← backend
    3. TCP ACK → backend (3-way handshake complete)
    4. HTTP request → backend
    5. HTTP response ← backend
    6. TCP FIN → backend (connection teardown)
  Cost: 3-5ms per request just for connection setup

  With keepalive=32: connection is reused
    4. HTTP request → backend (connection already open)
    5. HTTP response ← backend
  Cost: <0.1ms per request

  At 1000 req/sec: saves 3-5 seconds of pure TCP overhead per second.
  keepalive 32: nginx caches 32 idle connections to backend per worker
  12 workers × 32 = 384 cached connections — covers all backend workers

## RATE LIMITING ZONES

  Three separate zones for three risk levels:

  api_general:  30 req/sec burst=100  → all /api/ routes
  api_backtest:  5 req/sec burst=20   → POST /api/backtest (CPU-heavy)
  api_upload:    2 req/sec burst=5    → /api/upload (bandwidth-heavy)

  Why separate zones?
  A user uploading a file should not consume their general API quota.
  A backtest submission should not block strategy listing.
  Each zone is independent — fair throttling per action type.

  nodelay: burst requests are served immediately (not delayed/queued).
  If burst is exceeded → 429 Too Many Requests with JSON body.

## GZIP COMPRESSION

  Backtest result JSON: 50-500 KB uncompressed
  After gzip level 4:  5-50 KB
  Saving: 80-90% bandwidth per response

  gzip_comp_level 4: sweet spot between speed and compression
    Level 1: fastest, ~60% compression
    Level 4: balanced, ~85% compression, 2x CPU vs level 1
    Level 9: slowest, ~90% compression, 10x CPU vs level 1

  At 1000 req/sec × 200 KB average = 200 MB/sec uncompressed
  After gzip level 4:                  20 MB/sec → 10x bandwidth reduction

## STATIC ASSET CACHING STRATEGY

  location ~* \.(js|css|woff|woff2|...)$ {
      expires 1y;
      add_header Cache-Control "public, immutable";
  }

  Vite content hashing ensures filenames change when code changes.
  "immutable" tells browser: this URL will NEVER change content.
  Browser loads JS/CSS from disk cache on every page load — zero server hits.

  location / {
      add_header Cache-Control "no-cache, no-store, must-revalidate";
  }

  index.html must NEVER be browser-cached.
  If cached, users get stale JS bundle URLs after deployment.
  no-store: don't save to disk cache at all.

## SPA ROUTING (CRITICAL)

  location / {
      try_files $uri $uri/ /index.html;
  }

  Without this: user navigates to /strategies/NIFTY → nginx looks for
  /usr/share/nginx/html/strategies/NIFTY → file doesn't exist → 404

  With try_files /index.html fallback: nginx serves index.html → React
  Router reads window.location → renders the correct component.

  This is mandatory for ANY React app using react-router-dom.

## COMPLETE FILE → frontend/nginx.conf
## (see nginx.conf in this repo)


# ═══════════════════════════════════════════════════════════════════════════════
# SKILL-04: docker-compose.yml
# Memory-limited orchestration — no Prometheus, no Grafana
# ═══════════════════════════════════════════════════════════════════════════════

## COMPLETE FILE (replace your existing docker-compose.yml entirely)

version: '3.8'

# ── YAML anchors prevent duplication ──────────────────────────────────────
# x-backend-env and x-backend-volumes are referenced by all 3 backend services
# Change once → applies everywhere

x-backend-env: &backend-env
  DATABASE_URL: postgresql://${POSTGRES_USER:-algotest}:${POSTGRES_PASSWORD:-algotest_password}@postgres:5432/${POSTGRES_DB:-algotest}
  REDIS_URL: redis://redis:6379/0
  DATA_DIR: /data
  PYTHONUNBUFFERED: "1"
  MALLOC_ARENA_MAX: "2"
  DB_POOL_SIZE: "8"
  DB_POOL_MAX_OVERFLOW: "4"
  DB_POOL_TIMEOUT: "30"
  DB_POOL_RECYCLE: "3600"

x-backend-volumes: &backend-volumes
  - ./cleaned_csvs:/data/cleaned_csvs:ro
  - ./expiryData:/data/expiryData:ro
  - ./strikeData:/data/strikeData:ro
  - ./Filter:/data/Filter:ro
  - parquet_cache:/tmp/parquet_cache

services:

  # ── PostgreSQL ─────────────────────────────────────────────────────────
  postgres:
    image: postgres:15-bullseye
    container_name: algotest-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-algotest}
      POSTGRES_USER: ${POSTGRES_USER:-algotest}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-algotest_password}
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./reports/sql:/data:ro
    command: >
      postgres
      -c max_connections=100
      -c shared_buffers=512MB
      -c effective_cache_size=2GB
      -c work_mem=8MB
      -c maintenance_work_mem=128MB
      -c random_page_cost=1.1
      -c effective_io_concurrency=100
      -c max_worker_processes=4
      -c max_parallel_workers_per_gather=2
      -c max_parallel_workers=4
      -c checkpoint_completion_target=0.9
      -c wal_buffers=8MB
      -c log_min_duration_statement=1000
      -c idle_in_transaction_session_timeout=30000
    deploy:
      resources:
        limits:
          memory: 3500M
          cpus: "2.0"
        reservations:
          memory: 1024M
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-algotest}"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - algotest-network

  # ── Redis ──────────────────────────────────────────────────────────────
  redis:
    image: redis:7-bullseye
    container_name: algotest-redis
    restart: unless-stopped
    command: >
      redis-server
      --maxmemory 450mb
      --maxmemory-policy allkeys-lru
      --save ""
      --appendonly no
      --tcp-keepalive 60
      --timeout 300
    ports:
      - "${REDIS_PORT:-6379}:6379"
    volumes:
      - redis_data:/data
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "0.5"
        reservations:
          memory: 128M
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - algotest-network

  # ── Backend (FastAPI) ──────────────────────────────────────────────────
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: algotest-backend
    restart: unless-stopped
    ports:
      - "${BACKEND_PORT:-8000}:8000"
    environment:
      <<: *backend-env
      DB_STATEMENT_TIMEOUT: "1800000"
      USE_POSTGRESQL: "true"
      ALLOW_CSV_FALLBACK: "false"
      POSTGRES_HOST: postgres
      POSTGRES_PORT: "5432"
      POSTGRES_DB: ${POSTGRES_DB:-algotest}
      POSTGRES_USER: ${POSTGRES_USER:-algotest}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-algotest_password}
      UVICORN_WORKERS: "2"
    volumes: *backend-volumes
    deploy:
      resources:
        limits:
          memory: 1024M
          cpus: "1.5"
        reservations:
          memory: 256M
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "--fail", "--silent", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s
    networks:
      - algotest-network

  # ── Celery: Backtest Worker ────────────────────────────────────────────
  worker-backtests:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: algotest-worker-backtests
    restart: unless-stopped
    command: >
      celery -A worker.celery worker
      -Q backtests
      -c 3
      -l info
      --max-memory-per-child=600000
      --without-gossip
      --without-mingle
      --without-heartbeat
    environment: *backend-env
    volumes: *backend-volumes
    deploy:
      resources:
        limits:
          memory: 3000M
          cpus: "4.0"
        reservations:
          memory: 512M
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "celery -A worker.celery inspect ping --timeout 5"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    networks:
      - algotest-network

  # ── Celery: Upload Worker ──────────────────────────────────────────────
  worker-uploads:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: algotest-worker-uploads
    restart: unless-stopped
    command: >
      celery -A worker.celery worker
      -Q uploads
      -c 2
      -l info
      --max-memory-per-child=400000
      --without-gossip
      --without-mingle
      --without-heartbeat
    environment: *backend-env
    volumes: *backend-volumes
    deploy:
      resources:
        limits:
          memory: 1000M
          cpus: "1.0"
        reservations:
          memory: 256M
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "celery -A worker.celery inspect ping --timeout 5"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    networks:
      - algotest-network

  # ── Frontend (nginx) ───────────────────────────────────────────────────
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    container_name: algotest-frontend
    restart: unless-stopped
    ports:
      - "${FRONTEND_PORT:-3000}:3000"
    deploy:
      resources:
        limits:
          memory: 256M
          cpus: "0.5"
        reservations:
          memory: 64M
    depends_on:
      backend:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "--quiet", "--spider", "http://localhost:3000/"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    networks:
      - algotest-network

volumes:
  pgdata:
  redis_data:
  parquet_cache:

networks:
  algotest-network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/16


## CELERY FLAGS EXPLAINED

  --without-gossip:     Disables Celery cluster gossip protocol
                        Gossip broadcasts worker status between workers.
                        On single-machine: pointless overhead.
                        Saves ~20 MB RAM and reduces Redis chatter.

  --without-mingle:     Disables state sync on worker startup.
                        Workers don't need to learn each other's state.
                        Speeds up worker startup by 2-5 seconds.

  --without-heartbeat:  Disables Celery's internal heartbeat thread.
                        Heartbeat sends a Redis ping every 2 seconds per worker.
                        At 5 workers: 5 × 30 heartbeats/min = 150 Redis ops/min.
                        On single machine with no failover, this is pure waste.

  --max-memory-per-child=600000:
                        Worker subprocess restarts after using 600 MB (600,000 KB).
                        Prevents memory leaks from pandas/polars DataFrame
                        allocations that don't get garbage collected.
                        Fresh process = clean memory slate.


# ═══════════════════════════════════════════════════════════════════════════════
# SKILL-05: Ubuntu OS Tuning
# Run on the HOST machine (not inside Docker)
# ═══════════════════════════════════════════════════════════════════════════════

## APPLY THESE SETTINGS (run once, survive reboots)

  # Reduce swap aggression
  # Default 60 = kernel swaps when 40% RAM free (too aggressive for a server)
  # Setting 10 = kernel only swaps when <10% RAM remains
  echo 'vm.swappiness=10'            | sudo tee -a /etc/sysctl.conf

  # Reduce kernel's tendency to reclaim page cache too aggressively
  # Default 100 = kernel reclaims dentries/inodes aggressively
  # Setting 50 = keep more filesystem cache in RAM (faster file reads)
  echo 'vm.vfs_cache_pressure=50'    | sudo tee -a /etc/sysctl.conf

  # Prevent processes from over-committing memory
  # Default 0 = heuristic-based overcommit (allows 2x RAM allocation)
  # Setting 2 = total allocations capped at RAM × overcommit_ratio
  echo 'vm.overcommit_memory=2'      | sudo tee -a /etc/sysctl.conf
  echo 'vm.overcommit_ratio=95'      | sudo tee -a /etc/sysctl.conf

  # Increase max open file descriptors system-wide
  echo 'fs.file-max=2097152'         | sudo tee -a /etc/sysctl.conf

  # TCP tuning for high-concurrency
  echo 'net.core.somaxconn=65535'    | sudo tee -a /etc/sysctl.conf
  echo 'net.ipv4.tcp_tw_reuse=1'     | sudo tee -a /etc/sysctl.conf

  sudo sysctl -p

## CGROUPS V2 — REQUIRED FOR MEMORY LIMITS

  Docker deploy.resources.limits.memory ONLY works when cgroups v2 is active.
  Without cgroups v2, memory limits are silently ignored.

  # Verify:
  cat /sys/fs/cgroup/cgroup.controllers
  # Should contain: cpu io memory pids
  # If empty or missing memory, enable cgroups v2:

  sudo nano /etc/default/grub
  # Change: GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"
  # To:     GRUB_CMDLINE_LINUX_DEFAULT="quiet splash systemd.unified_cgroup_hierarchy=1"
  sudo update-grub && sudo reboot

## DOCKER DAEMON — /etc/docker/daemon.json

  sudo tee /etc/docker/daemon.json <<'EOF'
  {
    "default-ulimits": {
      "nofile": { "Name": "nofile", "Hard": 65536, "Soft": 65536 }
    },
    "log-driver": "json-file",
    "log-opts": {
      "max-size": "10m",
      "max-file": "3"
    },
    "storage-driver": "overlay2"
  }
  EOF

  sudo systemctl restart docker

  # log rotation prevents logs from filling disk:
  # max-size 10m × max-file 3 = max 30 MB per container
  # 6 containers × 30 MB = 180 MB max total log storage


# ═══════════════════════════════════════════════════════════════════════════════
# SKILL-06: FastAPI Backend Configuration
# File: backend/main.py (code snippets to add/verify)
# ═══════════════════════════════════════════════════════════════════════════════

## REQUIRED PACKAGES — add to backend/requirements.txt

  # Drop-in pandas replacement — 5-10x faster, 50% less RAM
  polars==0.20.31
  pyarrow==15.0.0

  # Rust-based JSON serializer — 5-10x faster than stdlib json
  # Handles numpy arrays, datetime objects natively
  orjson==3.9.15

  # System metrics for /health/stats endpoint
  psutil==5.9.8

  # Fast async event loop (already likely present, confirm version)
  uvloop==0.19.0

  # Async PostgreSQL driver
  asyncpg==0.29.0

  # HTTP client for health checks
  httpx==0.27.0

## FASTAPI APP SETUP (main.py)

  import os
  from fastapi import FastAPI
  from fastapi.responses import ORJSONResponse
  from fastapi.middleware.gzip import GZipMiddleware
  import psutil
  import redis as redis_lib

  # ORJSONResponse as default:
  # - 5-10x faster JSON serialization vs stdlib json
  # - Handles numpy int64/float64 without manual conversion
  # - Handles datetime objects without strftime()
  app = FastAPI(default_response_class=ORJSONResponse)

  # GZip for responses > 1000 bytes
  # Backtest JSON is 50-500 KB → compressed to 5-50 KB
  # nginx also gzips, but adding here handles direct backend access too
  app.add_middleware(GZipMiddleware, minimum_size=1000)

## DATABASE CONNECTION POOL (backend/database.py)

  from sqlalchemy import create_engine
  from sqlalchemy.pool import QueuePool

  engine = create_engine(
      os.environ["DATABASE_URL"],
      poolclass=QueuePool,
      pool_size=int(os.environ.get("DB_POOL_SIZE", 8)),
      max_overflow=int(os.environ.get("DB_POOL_MAX_OVERFLOW", 4)),
      pool_timeout=int(os.environ.get("DB_POOL_TIMEOUT", 30)),
      pool_pre_ping=True,       # verify connection alive before use
      pool_recycle=int(os.environ.get("DB_POOL_RECYCLE", 3600)),
      connect_args={
          "options": f"-c statement_timeout={os.environ.get('DB_STATEMENT_TIMEOUT', 1800000)}"
      }
  )

  # Connection math:
  # backend:         2 workers × pool_size 8 = 16 connections
  # worker-backtests: 3 workers × pool_size 5 = 15 connections  (set lower in worker)
  # worker-uploads:  2 workers × pool_size 4 =  8 connections
  # Total:                                      39 connections
  # PostgreSQL max_connections=100 → 61 connections headroom for admin/monitoring

## /health/stats ENDPOINT (replaces Grafana + Prometheus)

  @app.get("/health/stats")
  async def health_stats():
      r = redis_lib.Redis.from_url(os.environ["REDIS_URL"])
      redis_info = r.info("memory")
      mem  = psutil.virtual_memory()
      swap = psutil.swap_memory()
      return {
          "host": {
              "ram_total_gb":  round(mem.total    / 1e9, 2),
              "ram_used_gb":   round(mem.used     / 1e9, 2),
              "ram_free_gb":   round(mem.available / 1e9, 2),
              "ram_percent":   mem.percent,
              "swap_used_gb":  round(swap.used    / 1e9, 2),
              "swap_percent":  swap.percent,
              "cpu_percent":   psutil.cpu_percent(interval=0.1),
          },
          "redis": {
              "used_memory_mb":       round(redis_info["used_memory"] / 1e6, 1),
              "max_memory_mb":        round(redis_info.get("maxmemory", 0) / 1e6, 1),
              "backtest_queue_depth": r.llen("backtests"),
              "upload_queue_depth":   r.llen("uploads"),
          },
          "status": "healthy"
      }

  @app.get("/health")
  async def health():
      return {"status": "ok"}

## POLARS MIGRATION (replacing pandas)

  # BEFORE (pandas):
  import pandas as pd
  df = pd.read_sql(query, engine)          # loads all rows into RAM as NumPy
  result = df.groupby("expiry").sum()      # single-threaded

  # AFTER (polars):
  import polars as pl
  df = pl.read_database(query, connection) # columnar Arrow format, 50% less RAM
  result = df.group_by("expiry").sum()     # multi-threaded automatically

  # Polars lazy evaluation (even better for backtests):
  df = (
      pl.scan_database(query, connection)  # builds query plan, no data loaded yet
        .filter(pl.col("date") >= start_date)
        .select(["date", "expiry", "strike", "close", "oi"])
        .collect()                         # executes plan, loads only needed columns
  )

## BACKTEST CACHE (L1 memory + L2 Redis)

  import orjson
  from collections import OrderedDict
  import redis, os

  CACHE_TTL   = 1800   # 30 minutes — backtest results older than this are cold
  L1_MAX_KEYS = 50     # max 50 results in process memory

  class BacktestCache:
      def __init__(self):
          self.redis = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=False)
          self._l1: OrderedDict = OrderedDict()

      def get(self, key: str):
          if key in self._l1:                    # L1 hit: 0ms
              self._l1.move_to_end(key)
              return self._l1[key]
          val = self.redis.get(key)              # L2 hit: ~1ms
          if val:
              if len(self._l1) >= L1_MAX_KEYS:
                  self._l1.popitem(last=False)   # evict LRU entry
              self._l1[key] = orjson.loads(val)
              return self._l1[key]
          return None                            # cache miss: run backtest

      def set(self, key: str, value: dict):
          self.redis.setex(key, CACHE_TTL, orjson.dumps(value))
          if len(self._l1) >= L1_MAX_KEYS:
              self._l1.popitem(last=False)
          self._l1[key] = value


# ═══════════════════════════════════════════════════════════════════════════════
# SKILL-07: Celery Worker Configuration
# ═══════════════════════════════════════════════════════════════════════════════

## WHY THESE CONCURRENCY NUMBERS?

  worker-backtests: -c 3
    3 Celery worker processes × ~700 MB peak each = 2.1 GB
    Container limit is 3000 MB → 900 MB headroom for OS overhead
    More than 3 on 16 GB machine causes memory pressure across all services

  worker-uploads: -c 2
    2 processes × ~400 MB each = 800 MB
    Container limit is 1000 MB → 200 MB headroom
    Uploads are I/O bound (disk write + PostgreSQL insert) — 2 is sufficient

## max-memory-per-child CALCULATION

  --max-memory-per-child=600000  (units: KILOBYTES)
  600000 KB = 585 MB

  When a Celery worker subprocess hits 585 MB RSS, it:
  1. Finishes the current task
  2. Exits cleanly
  3. Celery spawns a fresh replacement process
  4. Fresh process starts at ~80 MB baseline RSS

  This prevents:
  - Pandas/Polars DataFrames not being garbage collected between tasks
  - Redis connection objects accumulating
  - Python allocator fragmentation growing unboundedly

## CELERY WORKER TASK FILE (backend/worker/tasks.py snippet)

  from celery import Celery
  import os

  celery = Celery(
      "algotest",
      broker=os.environ["REDIS_URL"],
      backend=os.environ["REDIS_URL"],
  )

  celery.conf.update(
      task_serializer="json",
      result_serializer="json",
      accept_content=["json"],
      result_expires=1800,          # task results expire after 30 min
      task_acks_late=True,          # acknowledge task only after completion
                                    # prevents lost tasks if worker crashes mid-task
      task_reject_on_worker_lost=True,  # requeue task if worker dies unexpectedly
      worker_prefetch_multiplier=1,     # fetch only 1 task at a time per worker
                                        # prevents one worker hoarding the queue
                                        # while others are idle
      broker_transport_options={
          "visibility_timeout": 3600  # task requeued if not completed within 1 hour
      },
  )


# ═══════════════════════════════════════════════════════════════════════════════
# SKILL-08: Redis Configuration
# ═══════════════════════════════════════════════════════════════════════════════

## REDIS FLAGS IN docker-compose.yml

  --maxmemory 450mb
    Hard cap: Redis never exceeds 450 MB RAM.
    Container limit is 512 MB → 62 MB for Redis process overhead.

  --maxmemory-policy allkeys-lru
    When maxmemory is reached: evict the Least Recently Used key.
    "allkeys" = evicts any key (not just keys with TTL set).
    LRU correctly evicts old backtest results before recent ones.
    Alternative policies:
      volatile-lru: only evict keys WITH TTL (dangerous if Celery task keys lack TTL)
      allkeys-lfu:  evict Least Frequently Used (better for stable hot data)
      noeviction:   refuse new writes (causes Celery task queue failures)

  --save ""
    Disables RDB persistence (Redis snapshot to disk).
    Backtest cache is ephemeral — losing it on restart is acceptable.
    Disk saves cause 100-500ms write latency spikes during snapshots.
    On a high-throughput server these spikes cause request timeouts.

  --appendonly no
    Disables AOF (Append Only File) persistence.
    Same rationale as --save "": cache doesn't need durability.
    AOF writes every Redis command to disk — too much I/O at 1000 req/sec.

  --tcp-keepalive 60
    Sends TCP keepalive probes every 60 seconds on idle connections.
    Detects dead connections from crashed Celery workers.
    Without this: dead connections accumulate until Redis runs out of
    connection slots (default 10,000 — rarely hit, but good practice).

  --timeout 300
    Closes idle client connections after 300 seconds of inactivity.
    Prevents connection leaks from workers that crash without cleanup.


# ═══════════════════════════════════════════════════════════════════════════════
# SKILL-09: PostgreSQL Configuration
# ═══════════════════════════════════════════════════════════════════════════════

## KEY PARAMETERS EXPLAINED

  max_connections=100
    Previous config: 200 connections
    Each idle connection: ~5 MB shared memory
    200 connections: 1 GB reserved even when mostly idle
    100 connections: 500 MB reserved
    Math: backend(16) + backtests(15) + uploads(8) + admin(5) = 44 active
    100 allows 56 connections headroom for future scaling

  shared_buffers=512MB
    Previous config: 2 GB — WRONG for this machine
    shared_buffers is PostgreSQL's private buffer pool.
    Rule of thumb: 25% of RAM BUT only if PostgreSQL is the primary workload.
    On this machine PostgreSQL competes with Celery workers for RAM.
    512 MB is sufficient — PostgreSQL also uses the OS page cache (effective_cache_size).

  effective_cache_size=2GB
    This is a PLANNER HINT — tells PostgreSQL how much RAM is available
    for OS page caching. It does NOT allocate memory.
    Setting 2 GB makes the query planner prefer index scans over seq scans
    for large tables. Correct value = RAM - shared_buffers - other services.

  work_mem=8MB
    Previous config: 16 MB
    work_mem is allocated PER SORT/HASH operation PER connection.
    At 100 connections, worst case: 100 × 8 MB = 800 MB just for sorts.
    8 MB is sufficient for backtest queries (date range filters, aggregates).

  log_min_duration_statement=1000
    Log any query taking longer than 1000ms (1 second).
    Helps identify missing indexes without logging every query.
    At 1000 users, logging all queries fills disk rapidly.

  idle_in_transaction_session_timeout=30000
    Kill connections that have been idle INSIDE a transaction for 30 seconds.
    Prevents hung transactions from holding row locks and blocking backtests.

## INDEXES TO ADD (run after your schema is created)

  -- Backtest data query pattern: filter by symbol + date range
  CREATE INDEX CONCURRENTLY idx_options_symbol_date
      ON options_data (symbol, date)
      INCLUDE (expiry, strike, close, oi);

  -- Upload query pattern: check for existing data before insert
  CREATE INDEX CONCURRENTLY idx_options_symbol_date_expiry_strike
      ON options_data (symbol, date, expiry, strike);

  -- CONCURRENTLY: builds index without locking the table
  -- Never run CREATE INDEX without CONCURRENTLY on a live table


# ═══════════════════════════════════════════════════════════════════════════════
# SKILL-10: Monitoring (No Prometheus / Grafana)
# ═══════════════════════════════════════════════════════════════════════════════

## WHAT YOU GET

  http://localhost:8000/health/stats → live JSON stats (see SKILL-06 above)

  ~/monitor.sh → terminal dashboard, refreshes every 10 seconds

## monitor.sh (save to ~/monitor.sh, chmod +x)

#!/bin/bash
# AlgoTest Real-Time Monitor
# Usage: chmod +x ~/monitor.sh && ./monitor.sh

while true; do
    clear
    printf '\033[1;34m╔══════════════════════════════════════════════════════╗\033[0m\n'
    printf '\033[1;34m║         AlgoTest Real-Time Monitor                   ║\033[0m\n'
    printf '\033[1;34m╚══════════════════════════════════════════════════════╝\033[0m\n\n'

    printf '\033[1;33m── HOST MEMORY ──────────────────────────────────────────\033[0m\n'
    free -h
    echo

    printf '\033[1;33m── SWAP ─────────────────────────────────────────────────\033[0m\n'
    SWAP_USED=$(free -m | awk '/Swap:/ {print $3}')
    if [ "$SWAP_USED" -gt 500 ]; then
        printf '\033[1;31mWARNING: Swap used: %s MB — risk of instability!\033[0m\n' "$SWAP_USED"
    else
        printf '\033[1;32mSwap OK: %s MB used\033[0m\n' "$SWAP_USED"
    fi
    echo

    printf '\033[1;33m── DOCKER CONTAINERS ─────── MEM USED / LIMIT ───────────\033[0m\n'
    docker stats --no-stream --format \
        'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}'
    echo

    printf '\033[1;33m── REDIS ────────────────────────────────────────────────\033[0m\n'
    docker exec algotest-redis redis-cli info memory 2>/dev/null \
        | grep -E 'used_memory_human|maxmemory_human|mem_fragmentation_ratio'
    echo

    printf '\033[1;33m── CELERY QUEUES ────────────────────────────────────────\033[0m\n'
    BT=$(docker exec algotest-redis redis-cli llen backtests 2>/dev/null || echo "0")
    UP=$(docker exec algotest-redis redis-cli llen uploads  2>/dev/null || echo "0")
    printf "  Backtest queue : \033[1;32m%s\033[0m pending tasks\n" "$BT"
    printf "  Upload queue   : \033[1;32m%s\033[0m pending tasks\n" "$UP"
    echo

    printf '\033[1;33m── BACKEND STATS ────────────────────────────────────────\033[0m\n'
    curl -s --max-time 3 http://localhost:8000/health/stats 2>/dev/null \
        | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    h = d['host']
    r = d['redis']
    print(f'  RAM: {h[\"ram_used_gb\"]} / {h[\"ram_total_gb\"]} GB ({h[\"ram_percent\"]}%)')
    print(f'  Swap: {h[\"swap_used_gb\"]} GB ({h[\"swap_percent\"]}%)')
    print(f'  CPU: {h[\"cpu_percent\"]}%')
    print(f'  Redis: {r[\"used_memory_mb\"]} / {r[\"max_memory_mb\"]} MB')
    print(f'  Backtest queue: {r[\"backtest_queue_depth\"]}')
    print(f'  Upload queue:   {r[\"upload_queue_depth\"]}')
except: print('  backend not ready')
" 2>/dev/null || echo "  backend not reachable"
    echo

    printf "  \033[2mRefreshing every 10s — Ctrl+C to exit\033[0m\n"
    sleep 10
done


# ═══════════════════════════════════════════════════════════════════════════════
# SKILL-11: Deployment Runbook
# Follow in exact order. Do not skip steps.
# ═══════════════════════════════════════════════════════════════════════════════

## PHASE 1: OS PREPARATION (one-time, on Ubuntu host)

  Step 1: Apply sysctl settings
    sudo tee -a /etc/sysctl.conf <<'EOF'
    vm.swappiness=10
    vm.vfs_cache_pressure=50
    vm.overcommit_memory=2
    vm.overcommit_ratio=95
    fs.file-max=2097152
    net.core.somaxconn=65535
    net.ipv4.tcp_tw_reuse=1
    EOF
    sudo sysctl -p

  Step 2: Verify cgroups v2
    cat /sys/fs/cgroup/cgroup.controllers
    # Must show: cpuset cpu io memory hugetlb pids
    # If not: add systemd.unified_cgroup_hierarchy=1 to grub and reboot

  Step 3: Configure Docker daemon
    sudo tee /etc/docker/daemon.json <<'EOF'
    {
      "default-ulimits": {
        "nofile": { "Name": "nofile", "Hard": 65536, "Soft": 65536 }
      },
      "log-driver": "json-file",
      "log-opts": { "max-size": "10m", "max-file": "3" },
      "storage-driver": "overlay2"
    }
    EOF
    sudo systemctl restart docker

## PHASE 2: CODE CHANGES (in your repo)

  Step 4: backend/Dockerfile
    Replace with Dockerfile.backend (multi-stage, slim, appuser, MALLOC_ARENA_MAX)

  Step 5: frontend/Dockerfile
    Replace with Dockerfile.frontend (multi-stage, node-builder + nginx-runtime)

  Step 6: frontend/nginx.conf
    Replace with nginx.conf (epoll, gzip, rate limiting, keepalive pool)

  Step 7: docker-compose.yml
    Replace with SKILL-04 docker-compose.yml (memory limits, no Prometheus/Grafana)

  Step 8: backend/requirements.txt — add:
    polars==0.20.31
    pyarrow==15.0.0
    orjson==3.9.15
    psutil==5.9.8
    uvloop==0.19.0

  Step 9: backend/main.py — add:
    - ORJSONResponse as default_response_class
    - GZipMiddleware
    - /health endpoint
    - /health/stats endpoint
    - BacktestCache class

  Step 10: backend/database.py — update:
    - SQLAlchemy QueuePool with env-var-driven pool_size

## PHASE 3: BUILD AND DEPLOY

  Step 11:
    # Stop all services cleanly
    docker compose down

    # Remove old images to force fresh build with slim base
    docker compose build --no-cache --parallel

    # Start all services
    docker compose up -d

    # Watch startup
    docker compose logs -f --tail=50

## PHASE 4: VERIFICATION

  Step 12: Verify all 6 containers are healthy
    docker compose ps
    # Expected: all services show "(healthy)"

  Step 13: Check memory limits are enforced (cgroups v2)
    docker stats --no-stream
    # Each container should show LIMIT matching docker-compose.yml values
    # If LIMIT shows full RAM (16 GB): cgroups v2 not enabled — go back to Step 2

  Step 14: Verify swap is low
    free -h
    # Swap "used" column should be < 100 MB after 5 minutes

  Step 15: Test /health/stats
    curl http://localhost:8000/health/stats | python3 -m json.tool

  Step 16: Run terminal monitor in a second terminal
    chmod +x ~/monitor.sh
    ./monitor.sh

  Step 17: Run a test backtest and verify
    - Submit via frontend
    - Monitor queue depth in monitor.sh
    - Verify result returns correctly
    - Check docker stats — no container near its memory limit

## EXPECTED RESULTS AFTER FULL DEPLOYMENT

  Metric                    | Before          | After
  ─────────────────────────|─────────────────|──────────────────
  RAM at idle              | 12.2 GB / 16 GB | 5-6 GB / 16 GB
  RAM at 100 users active  | >16 GB (crash)  | 9-10 GB / 16 GB
  RAM at 1000 users active | PC reboots      | 10-11 GB (queued)
  Swap used                | 2.4 GB          | <100 MB
  Worker crashes           | Frequent        | Zero (limits enforced)
  Backtest speed           | baseline        | 3-5x faster (Polars + cache)
  JSON response time       | baseline        | 5-10x faster (orjson + gzip)
  Static asset load time   | baseline        | <5ms (browser cached)
  Containers running       | 8               | 6 (Prometheus + Grafana removed)
