# AlgoTest Rust Shadow

This directory is an isolated, zero-Python replacement under development. It is
not imported by, mounted into, or launched by the production Compose stack.

Safety invariants:

- Production ports, containers, queues, Redis keys, databases, caches, and
  artifacts are never written by this project.
- Production remains authoritative until the full parity matrix is clean.
- A shadow result is always marked `authoritative: false`.
- A job is successful only when every planned combination succeeded.
- Unsupported or malformed requests fail during validation, before computation.

Proposed isolated endpoints:

- Rust API: `127.0.0.1:18200`
- Shadow frontend: `127.0.0.1:3200`
- Shadow PostgreSQL: `127.0.0.1:55432`
- Shadow Redis: `127.0.0.1:6380`

See `ISOLATION.md` and `COMPLETION_MATRIX.md` before running anything.

The shadow stack has its own Compose project and is intentionally not included
by the repository's production `docker-compose.yml`. It must only be started
explicitly from this directory:

```bash
docker compose -f compose.shadow.yml up
```

That command is documentation only; implementation and parity work does not
start it. The runtime image contains the Rust API binary and OS certificates/
health-check tooling, with no Python interpreter or Python package layer.

Memory safety is enforced in layers: request admission limits, streaming
combination chunks, bounded market-data rows, a 2 GiB application budget, and a
4 GiB isolated container ceiling. Completed chunks stream into the shadow-only
SQLite state volume instead of an unbounded result vector. A job that cannot fit
is rejected/queued before allocation.

Implemented shadow API routes:

- `POST /api/backtest`
- `POST /api/optimize/preview`
- `GET|POST /api/optimize/jobs`
- `GET|DELETE /api/optimize/jobs/{job_id}`
- `GET /api/optimize/jobs/{job_id}/results?offset=0&limit=100`
- `GET /api/optimize/jobs/{job_id}/failures?offset=0&limit=100`
- `POST /api/optimize/jobs/{job_id}/resume` for deterministic exhaustive/random jobs and zero-result smart jobs
- `POST /api/optimize/jobs/{job_id}/summary.xlsx` for bounded native XLSX export
- `GET /api/optimize/jobs/{job_id}/combo/{combo_id}/tradesheet.xlsx`
- `GET /api/optimize/jobs/{job_id}/tradesheets.zip`
- `GET /api/optimize/jobs/{job_id}/wow_mom.xlsx`
- `POST /api/backtest/tradesheet.xlsx`

Port `18200` is the isolated Compose and local-verification default because
port `8200` is occupied by an unrelated process on this host.
