# Algo_Tesst

EOD options and futures backtesting platform. FastAPI coordinates Celery workers,
PostgreSQL stores market data, Redis handles queues and result caching, and
Vite/React provides the client. Rust integrations retain their existing paths.

## Repository map

| Path | Purpose |
| --- | --- |
| `backend/` | API, engines, services, migrations, workers and tests |
| `frontend/` | React source and the existing deployment bundle |
| `remote-worker/` | Remote worker runtime |
| `rust-shadow/` | Rust engine workspace |
| `scripts/` | Data import, maintenance and analysis tools |
| `docs/` | Architecture, operations, product and reference documentation |
| `research/` | Historical strategy research and filter snapshots |
| `reports/` | Existing schema and performance reports |

See [repository structure](docs/REPOSITORY_STRUCTURE.md) and the
[cleanup audit](docs/REPOSITORY_CLEANUP.md) for retained compatibility files.

## Run locally

Provision PostgreSQL/Redis and market data using the
[Docker guide](docs/operations/README-DOCKER.md) and
[migration guide](docs/csv_to_postgres_migration_README.md).
Copy `.env.example` to `.env` and configure the values for your machine.

```bash
cd frontend
npm ci
npm run build
cd ..
docker compose up -d --build
```

The frontend Dockerfile consumes `frontend/build/`. Generate it before building
the image directly. `start.sh` also attempts this build as part of its existing
startup sequence. Backend-only development uses `python start_backend.py`;
frontend development uses `cd frontend && npm run dev`.

## Data and caches

Production uses PostgreSQL with Parquet/Arrow caches under the configured cache
directories. Parquet is a cache, not the only source: expired, incomplete or
incompatible cache files can trigger PostgreSQL loads. CSV fallback/import paths
remain `cleaned_csvs/`, `expiryData/`, `strikeData/` and `Filter/`.
`cleaned_csvs/` is local and ignored by Git.

The unreferenced `NFO_PREPARED/` export was removed from the cleanup branch's
tracked tree. Its original files remain in `archive/pre-cleanup-2026-10-01` and
Git history. This change does not remove files from an existing local PC or
alter database, cache, ingestion or deployment configuration.

## Verification

```bash
python -m unittest discover backend/tests
cd frontend && npm run build
```

Backend checks require the project dependencies; some additionally require
market/reference data and the compiled Rust extension. The cleanup audit records
the observed baseline failures and environment limitations. The cleanup PR stays
draft; `main` and its archive snapshot are unchanged.
