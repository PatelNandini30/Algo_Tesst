# AlgoTest Clone - Docker Setup Guide

## Quick Start

### Prerequisites
- Docker Desktop (Windows/Mac) or Docker Engine (Linux)
- Docker Compose v2+

### Start the Stack

```bash
# Build and start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Check status
docker-compose ps
```

### Access the Application

- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs

### Stop the Stack

```bash
docker-compose down
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Docker Network                           │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐                      │
│  │   Frontend  │    │   Backend    │                      │
│  │   :3000     │───▶│   :8000      │                      │
│  └──────────────┘    └──────┬───────┘                      │
│                             │                                │
│                      ┌─────┴─────┐                        │
│                      │  PostgreSQL │ (optional)             │
│                      │   :5432     │                        │
│                      └─────────────┘                        │
└─────────────────────────────────────────────────────────────┘
```

## Configuration

Environment variables can be configured in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| POSTGRES_DB | algotest | Database name |
| POSTGRES_USER | algotest | Database user |
| POSTGRES_PASSWORD | algotest_password | Database password |
| USE_POSTGRESQL | false | Use PostgreSQL instead of CSV |
| BACKEND_PORT | 8000 | Backend port |
| FRONTEND_PORT | 3000 | Frontend port |

## Services

### Backend
- FastAPI application running on port 8000
- Health check at `/health`
- API docs at `/docs`
- Reads from CSV files by default (see Data section)

### Frontend  
- React + Vite application
- Built and served with nginx

### PostgreSQL (Optional)
- Used when `USE_POSTGRESQL=true`
- Schema must be initialized manually

### Redis (Optional)
- For future queue/worker functionality
- Not required for basic operation

## Data

The backend reads from CSV files mounted as volumes:

- `./cleaned_csvs` → `/data/cleaned_csvs`
- `./expiryData` → `/data/expiryData`
- `./strikeData` → `/data/strikeData`

These are mounted read-only (`ro`) for security.

## PostgreSQL Migration (Optional)

### 1. Initialize Database Schema

```bash
# Connect to postgres container
docker-compose exec postgres psql -U algotest -d algotest

# Run migrations
docker-compose exec -T postgres psql -U algotest -d algotest < migrations/002_create_data_tables.sql
```

### 2. Migrate Data (Optional - can run while using CSV)

```bash
# Run migration script
docker-compose exec backend python migrate_data.py --all
```

### 3. Enable PostgreSQL

Edit `.env`:
```
USE_POSTGRESQL=true
```

Restart backend:
```bash
docker-compose restart backend
```

## Common Commands

```bash
# Build images
docker-compose build

# Start services
docker-compose up -d

# View logs
docker-compose logs -f backend
docker-compose logs -f frontend

# Restart a service
docker-compose restart backend

# Stop all services
docker-compose down

# Remove volumes ( WARNING: deletes all data)
docker-compose down -v
```

## Troubleshooting

### Backend won't start
```bash
# Check logs
docker-compose logs backend

# Verify CSV files exist
ls -la cleaned_csvs/
```

### Frontend can't connect to backend
```bash
# Check backend is healthy
curl http://localhost:8000/health

# Check network
docker network ls
```

### Database connection issues
```bash
# Check postgres is running
docker-compose ps

# Check postgres logs
docker-compose logs postgres
```

## Development

For development with hot-reload:

```bash
# Start services without building
docker-compose up

# Backend code changes require rebuild
docker-compose build backend
```

## Worker/Queue (Future)

To enable background processing with Celery:

1. Uncomment the worker section in `docker-compose.yml`
2. Add celery config to environment
3. Rebuild with `docker-compose build worker`
