# Repository Guidelines

## Project Structure & Module Organization
`backend/` contains the FastAPI API, strategy engines, services, routers, workers, migrations, and the current backend test suite in `backend/tests/`. `frontend/` is a Vite + React app; UI code lives in `frontend/src/components/`, with entry files in `frontend/src/`. Operational scripts live in `scripts/`, `start.sh`, and `start_backend.py`. SQL and performance notes live under `reports/` and `docs/`. Large local data inputs are mounted from `cleaned_csvs/`, `expiryData/`, `strikeData/`, and `Filter/`.

## Build, Test, and Development Commands
Use Docker for the full stack:

```bash
docker compose up -d --build   # start frontend, backend, postgres, redis, workers
docker compose logs -f backend # follow backend logs
./start.sh                     # restart the full stack with health checks
```

For local development outside Docker:

```bash
python start_backend.py
cd frontend && npm install && npm run dev
cd frontend && npm run build
python -m unittest backend.tests.test_resolve_leg_exit
```

## Coding Style & Naming Conventions
Python uses 4-space indentation, snake_case for functions/modules, and PascalCase for classes. React files use PascalCase component names such as `StrategyBuilder.jsx`; helper modules use lower-case names like `constants.js`. Match the surrounding style: backend files commonly use explicit imports and small service modules, while frontend files use semicolons and 2-space indentation. No formatter or linter is configured, so keep diffs tight and consistent with nearby code. Do not edit `frontend/dist/` by hand.

## Testing Guidelines
Backend tests currently use `unittest` and live in `backend/tests/test_*.py`. Add regression tests for engine, router, or data-loading fixes whenever behavior changes. Run the focused module you touched first, then broader checks if needed. Frontend has `frontend/src/main.test.jsx`, but no test runner is wired into `package.json`; if you add frontend tests, also add the command needed to run them.

## Commit & Pull Request Guidelines
Recent history mixes free-form messages with Conventional Commit prefixes. Prefer short, imperative commits, and use `feat:`, `fix:`, or `chore:` when practical. Pull requests should describe the user-visible change, list affected areas (`backend/engine`, `frontend/components`, migrations, Docker), note any data or env prerequisites, and include screenshots for UI changes.

## Graphify Workflow
If `graphify-out/GRAPH_REPORT.md` exists, read it before answering architecture questions. Prefer `graphify-out/wiki/index.md` over raw graph files when available. After changing code files, run `graphify update .` to refresh the repository graph.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)

## Repository Hygiene Rules
- Keep the repository root limited to entry-point configuration, top-level documentation, and launch files.
- Do not create scratch, temporary, generated, benchmark-output, or assistant-session files in the repository root.
- Put long-form documentation under `docs/`, exploratory work under `research/`, and generated outputs under an ignored artifact/output directory.
- Never commit `.env`, credentials, tokens, database dumps, large raw market datasets, build caches, or generated frontend bundles.
- Keep stable test fixtures small and place them with the tests that require them.
- Use descriptive permanent filenames; avoid names such as `NEW.py`, `test2.py`, `rf5b.py`, or numbered scratch variants for production code.
- Before adding a new top-level directory, document its purpose in `docs/REPOSITORY_STRUCTURE.md`.
- Repository-organization work must be done on a dedicated branch and verified before merging into `main`.
