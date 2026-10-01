# Repository Structure

This document describes where files should live without changing the current runtime architecture.

## Runtime source

- `backend/` — FastAPI backend, services, strategy logic, workers, migrations, tests, and native integration.
- `frontend/` — Vite/React frontend.
- `remote-worker/` — remote/distributed worker support.
- `rust-shadow/` — Rust research/engine work that is still kept under its existing path to avoid breaking runtime assumptions.

These paths remain unchanged throughout the repository cleanup. The root README
provides setup links; `docs/REPOSITORY_CLEANUP.md` records decisions and verification.

## Documentation and research

- `docs/architecture/` — system and Rust architecture documents.
- `docs/operations/` — Docker, backup, restore, and operational documentation.
- `docs/product/` — optimizer/product guides and roadmap material.
- `docs/reference/` — reference workbooks and documents.
- `docs/archive/` — historical prompts and assistant-session material retained for reference.
- `research/` — exploratory strategy and market-logic research that is not production runtime code.
- `research/filters/` — preserved historical filter snapshots.
- `reports/` — canonical existing schema-discovery and performance reports.

## Data and generated content

Large market datasets, database files, build caches, optimizer downloads, logs, and generated artifacts should not be added to Git unless they are deliberately small, stable test fixtures.

`cleaned_csvs/` is local and ignored; its fallback/import paths remain intact.
The unreferenced `NFO_PREPARED/` export is removed from this branch's tracked tree
and recoverable from the archive. Referenced CSV directories, database settings,
Parquet/Arrow caches, test fixtures and parity snapshots are unchanged.

`frontend/build/` is retained as a deployment compatibility exception because the
current Dockerfile copies a prebuilt bundle. All retained manual validation
workbooks and diagnostic scripts remain at their existing backend paths.

## Root policy

The root should contain only project entry points and top-level configuration such as:

- `AGENTS.md`
- `README.md`
- `CLAUDE.md`
- `.env.example`
- `.gitignore`
- Docker Compose files
- package/requirements files
- startup entry points
- major source directories

Temporary files, installers, generated outputs, research notes, and long-form documentation should not be placed in the root.

## Cleanup safety

Repository cleanup is performed on `chore/repository-cleanup`.

The untouched pre-cleanup state is preserved on:

`archive/pre-cleanup-2026-10-01`

Do not merge structural source-directory changes into `main` until imports, Docker paths, scripts, tests, and runtime behavior have been verified.
