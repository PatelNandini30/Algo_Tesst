# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Stack at a glance

- **Scope**: This is the **EOD (end-of-day) options backtester only**. The intraday product is a separate codebase/repo — do not re-introduce intraday workers, routers, an `intraday-api`, or `services/intraday_*` here. (Note: `'INTRADAY'` as an SL-exit *kind* tag in the engine refers to a stop touched within a daily bar — that is core EOD logic, unrelated to the intraday product.)
- **Backend**: FastAPI (uvloop) + Celery (Redis broker) + PostgreSQL 15 + optional Rust extension (`backend/native/`, PyO3 + Arrow IPC).
- **Frontend**: Vite + React (`frontend/src/`); production build served via nginx in Docker.
- **Data sources**: PostgreSQL is primary (`USE_POSTGRESQL=true`). CSV fallback is read-only mounts of `cleaned_csvs/`, `expiryData/`, `strikeData/`, `Filter/`. Toggle is `ALLOW_CSV_FALLBACK`.
- **Hardware target** (encoded in `docker-compose.yml`): 16 GB RAM (fixed — won't be upgraded), **NVMe SSD** (not HDD). Memory limits and Postgres tuning are deliberately conservative — do not raise `shared_buffers`, `work_mem`, or worker counts without revisiting the budget comment at the top of `docker-compose.yml`. RAM, not disk, is the binding constraint; the box keeps ~24 GB SSD swap as the OOM cushion. (The Postgres `random_page_cost`/`effective_io_concurrency` values are historically HDD-tuned but left conservative for the RAM budget — disk is actually SSD.)

## Common commands

```bash
# Full stack (preferred — handles port conflicts, kills system postgres/redis, waits for health)
./start.sh

# Manual Docker
docker compose up -d --build
docker compose logs -f backend worker-backtests
docker compose down

# Backend without Docker (uses local Python venv at .venv/)
python start_backend.py

# Frontend dev
cd frontend && npm install && npm run dev
cd frontend && npm run build      # outputs to frontend/dist/, do NOT hand-edit

# IMPORTANT: this network sits behind a SonicWALL DPI-SSL firewall that re-signs
# TLS, so a bare `npm install`/`npm ci` fails with SELF_SIGNED_CERT_IN_CHAIN and
# then dies with the misleading "Exit handler never called!" (NOT an OOM/HDD issue).
# Disable strict TLS for the install. The dist is also root-owned, so build off the
# host in a node container and copy dist back (no host sudo):
#   docker run --rm -e NODE_TLS_REJECT_UNAUTHORIZED=0 -v "$PWD/frontend":/src \
#     node:22-bookworm-slim sh -c 'npm config set strict-ssl false; \
#       mkdir -p /build && cp -r /src/. /build/ && cd /build && rm -rf node_modules dist && \
#       npm install --no-audit --no-fund && npm run build && \
#       rm -rf /src/dist && cp -r /build/dist /src/dist'
# Then publish: docker compose up -d --build frontend  (the image just COPYs dist).

# Tests (unittest, not pytest — even though pytest is in .pytest_cache)
python -m unittest discover backend/tests
python -m unittest backend.tests.test_resolve_leg_exit       # single module
python -m unittest backend.tests.test_resolve_leg_exit.TestX.test_y  # single test

# Postgres migrations are plain SQL files run in numeric order
docker compose exec -T postgres psql -U algotest -d algotest < backend/migrations/003_postgres_csv_replacement_schema.sql
```

Endpoints: frontend `http://localhost:3000`, backend `http://localhost:8000`, OpenAPI `/docs`, health `/health`, pool/cache stats `/health/db`, `/health/stats`, `/cache/stats`.

## Architecture (the parts you can't see from one file)

This is a **multi-process pipeline**, not a single-server app. A backtest request flows:

```
Frontend → FastAPI (routers/backtest.py)
        → Celery enqueue (worker.tasks.run_algotest_job)
        → Worker picks queue based on date range:
            · backtests        (long runs, BACKTEST_FAST_QUEUE_MAX_DAYS+ days)
            · backtests_fast   (short runs, separate worker so they don't wait)
        → services/algotest_job.execute_algotest_job
            · bulk_load_options() pulls range from Postgres into Polars
            · services/fast_lookup builds an O(1) in-memory dict ONCE per run
            · engines/generic_algotest_engine runs the strategy
        → services/backtest_cache (Redis) caches the result by request hash
```

Key implications:

- **Backtests run in workers, not in the API process.** The FastAPI process should never call the engine directly except for trivial paths. New endpoints that do real work go through Celery.
- **Two backtest queues exist by design.** `BACKTEST_FAST_QUEUE_MAX_DAYS` (default 550) routes small ranges to `worker-backtests-fast` so a 7-year run doesn't block a 1-month run. Don't collapse them.
- **`fast_lookup` is the hot path.** It converts the bulk Polars DataFrame into Python dicts keyed by `(symbol, expiry, strike, type, date)` — every option price lookup during a backtest hits this dict, not Polars or Postgres. If you change the loading logic in `services/data_loader.py` or `base.py`, you must keep the keys consistent or tests in `test_fast_lookup_golden.py` will fail.
- **Optional Rust fast path.** `backend/native/` is a PyO3 extension that memory-maps Arrow IPC files for the lookup. It's gated by `FAST_LOOKUP_MODE` (`auto`/`rust`/`python`) and `services/rust_fast_path.py`. Falling back to Python is always safe; never make the Rust path mandatory.
- **Caches are layered**: process-local (`services/data_memory_cache.py`) → Parquet on disk (`PARQUET_CACHE_DIR=/data/cache/parquet`) → Arrow on disk (`ALGO_RUST_CACHE_DIR=/data/cache/arrow`) → Redis result cache (`services/backtest_cache.py`). The `algo_cache` Docker volume is what makes warm caches survive container recreates — don't `docker compose down -v` casually.

## Data model

- Source-of-truth schema is `backend/migrations/003_postgres_csv_replacement_schema.sql`. Subsequent migrations only add indexes (`004`, `005`, `006`).
- `option_data` is the main fact table (one row per symbol/expiry/strike/type/date), denormalized for read speed. The repo predates the convention of using a separate dimension/fact split — match the existing shape rather than introducing one.
- Spot/futures and option rows live in the same table differentiated by `instrument` (`OPTIDX`/`FUTIDX`/etc). Strike and option_type are NULL for futures — checks in the schema enforce this.
- `import_batches` / `import_files` track CSV ingestion. Re-running an import with the same SHA256 is a no-op by design.
- The Postgres tuning is conservative (`random_page_cost=4.0`, `effective_io_concurrency=2`, `max_parallel_workers=2`) — historically HDD-tuned but kept for the tight RAM budget even though the disk is NVMe SSD. More parallel workers makes things **slower** on this hardware (RAM/core-bound, not disk-bound).

## Working with the engine

- `engines/generic_algotest_engine.py` is the entry point (`run_algotest_backtest`). `engines/generic_multi_leg.py` handles multi-leg strategies. They share `base.py` for option/spot loading.
- `routers/backtest.py` does request normalization (`_normalize_payload_dates`, `_normalize_request`) before enqueueing — payloads from the frontend may use `date_from`/`from_date`/`dd/mm/yyyy`/etc., and the engine expects normalized ISO dates.
- Strategy types live in `backend/strategies/strategy_types.py`. New leg/exit conditions go there, then are wired through the engine.
- MAE/MFE is included in the tradesheet by default (`BACKTEST_INCLUDE_MAE_MFE=1`). Disable only for bulk perf tests.
- Pandas 2.x compatibility shims live at the top of `main.py` (patches `DataFrame.sort_values` and `Series.sort_values`). Don't remove — the codebase still uses the legacy `by=` keyword in places.

## Response style (user preference)

- **Be fast and concise.** The user wants quick responses. Keep explanations short, skip long preambles/recaps, avoid repeating what was already said, and act instead of deliberating out loud. Prefer doing the work and reporting the result briefly over narrating options. Only expand when the user asks for detail.
- **Limit token usage; do not cross boundaries.** Keep total token usage bounded — read only the specific file regions you need (not whole large files), avoid dumping large logs/command output into context, prefer targeted `grep`/`sed -n` over broad reads, and don't re-fetch or re-verify things already established in the conversation. Stay well within context limits.

## Working with prompts

- **Always refine the prompt before acting.** Before starting any task, restate the user's request as a clearer, more precise prompt — resolve ambiguity, fill in implied scope, and confirm the refined intent (or proceed with it explicitly stated) rather than acting on the raw wording.

## Conventions

- Python: 4-space, snake_case functions/modules, PascalCase classes. No formatter or linter is wired up — match surrounding style.
- React: PascalCase component files (`StrategyBuilder.jsx`), 2-space indent, semicolons. Helpers in lower case (`constants.js`).
- Commit style: free-form is accepted; prefer Conventional Commits (`feat:`, `fix:`, `chore:`) when practical.
- **Never** edit `frontend/dist/` by hand. It is generated.
- **Never** raise Docker memory limits without updating the budget header in `docker-compose.yml`. The total must fit in 16 GB RAM with the ~24 GB SSD swap as headroom.

## Graphify workflow

This project has a graphify knowledge graph at `graphify-out/`.

- Before answering architecture or codebase questions, read `graphify-out/GRAPH_REPORT.md` for god nodes and community structure.
- If `graphify-out/wiki/index.md` exists, navigate it instead of reading raw files.
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost).
