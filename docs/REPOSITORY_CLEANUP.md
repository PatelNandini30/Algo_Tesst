# Repository cleanup audit

Date: 2026-10-01. Scope: organize the tracked tree while preserving runtime behavior and all existing development history.

## Safety and history

- Original `main`: `a90441675cf78894dd952dd71e45cf0fd1bdb141`.
- Archive: `archive/pre-cleanup-2026-10-01`, at the same original commit.
- Working branch: `chore/repository-cleanup`; PR #1 remains draft.
- This continuation starts at `76e56278e8f52bb0aa0bbb5d76efde8c3c3c5617` and adds child commits without rewriting history or force-pushing.
- All Claude commits reachable from the original main remain ancestors. The complete remote branch listing contained main, archive and cleanup; there was no separate divergent Claude branch.
- No files on the user's PC, PostgreSQL databases, Docker volumes or local Parquet/Arrow caches were accessed or deleted. Switching an existing checkout to this branch can remove previously tracked exports from that checkout; the archive remains the recovery source.

## Inventory and decisions

The starting cleanup tree contained **24,886 tracked files**. The continuation removes **24,298 CSV exports** and **35 disposable files**, moves **2 reference files without changing their blob hashes**, and adds this audit and a root README. The final tree contains **555 tracked files**.

### Unused prepared market exports

Remove `NFO_PREPARED/**`: 24,298 CSV files, **15,397,369,529 bytes** (15.40 GB decimal). An exhaustive search of tracked source/configuration found no literal path references to this folder, and the deployment configurations do not mount it.

This is distinct from `cleaned_csvs/`, which has **zero tracked files** and was already ignored. Its local ingestion/fallback use is retained. Parquet reads in `backend/services/data_loader.py` use `PARQUET_CACHE_DIR` (Compose: `/data/cache/parquet`). The loader can expire a cache after the default 30-day TTL, invalidate missing columns, or load uncovered dates from PostgreSQL; Parquet is not an unconditional replacement for the database/source data.

Only the current tracked tree is reduced. Git history and the archive still hold the CSV blobs, so this does **not** shrink a full-history clone by 15 GB.

### Disposable files removed

The spreadsheet exports below have no tracked source read/import references. The simulation dump and alternate wheel are also unreferenced; the deployed native wheel is built through the existing `start.sh`/`backend/prebuilt/` process. Both removed SQLite placeholders are zero bytes and contain no schema/data. The root discovery reports duplicate the unchanged canonical files under `reports/` byte for byte.

- `[backend`
- `[backend]`
- `[frontend`
- `[frontend]`
- `[internal]`
- `[intraday-api`
- `[intraday-api]`
- `backend/A.xlsx`
- `backend/AGAIN.xlsx`
- `backend/B.xlsx`
- `backend/E2.xlsx`
- `backend/E3.xlsx`
- `backend/NEW.xlsx`
- `backend/NEW9.xlsx`
- `backend/OV.xlsx`
- `backend/OWN.xlsx`
- `backend/T165.xlsx`
- `backend/YA.xlsx`
- `backend/YE.xlsx`
- `backend/_err6.xlsx`
- `backend/_m_only.xlsx`
- `backend/_r2.xlsx`
- `backend/_sim3m.json`
- `backend/_t31.xlsx`
- `backend/_t42.xlsx`
- `backend/_t70.xlsx`
- `backend/_t9.xlsx`
- `backend/_wm.xlsx`
- `backend/bhavcopy_data.db`
- `backend/prebuilt-next/algotest_native-0.1.0-cp311-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`
- `bhavcopy_data.db`
- `csv_schema_discovery_report.json`
- `csv_schema_discovery_report.md`
- `docker`
- `exporting`

### Reference moves

| Original | Current | Reason |
| --- | --- | --- |
| `relleg_proof.png` | `docs/reference/relleg_proof.png` | Preserve manual proof with reference assets; no source references |
| `Filter/377FCA33.tmp` | `research/filters/base2-2025-08-29.csv` | Contains a distinct historical filter snapshot; preserve rather than delete |

### Deliberately retained

- All **240 Python files**, **23 Rust files**, frontend source, configuration, Dockerfiles, migrations and startup/operational scripts retain their existing paths and blob hashes.
- All **70 backend test modules**, **40 parity files**, other fixtures, and `backend/tools/_rustport_baseline/` remain intact.
- `backend/_f.xlsx` is an explicit input to four retained diagnostic scripts. `MOM.xlsx`, `MIXED_EXPIRY_JUN2025.xlsx`, `patch_wise_test.xlsx` and `research_lownav.xlsx` are retained as possible manual validation/research baselines.
- Numbered diagnostic scripts and named parity/validation utilities stay at their original paths to preserve historical usage; lack of an import alone does not establish that a manual checker is disposable.
- `frontend/build/` remains byte-identical in Git because `frontend/Dockerfile` performs `COPY build` and the existing launcher falls back to that bundle. Removing it safely requires a separate deployment change. The locally generated verification build is not committed.
- `Filter/` CSVs, `expiryData/`, `strikeData/`, `Output/`, small sample data, design references and useful document-generation/export tools remain intact.

## Verification

| Check | Result |
| --- | --- |
| Initial main/archive tips and remote branches | Confirmed original SHA and isolated cleanup branch |
| Initial cleanup runtime roots against original main | Matching hashes across source/configuration/entry paths |
| All removed-path references | Exhaustive tracked text-source/configuration scan; referenced baselines retained |
| Duplicate discovery reports | Matching original blob hashes at root and under reports |
| Final Git tree | Exhaustive path/hash/mode comparison against the explicit removal/move/doc allowlist |
| Python syntax | All 240 Python source files parsed successfully |
| Shell syntax | All root/scripts shell launchers passed bash syntax checks |
| Frontend | npm ci and npm run build passed (2,498 modules); existing bundle-size warning |
| Focused backend baseline | 61 tests; 60 passed, 1 existing MIDCPNIFTY weekly-validation failure |
| Full backend before cleanup | 719 tests; 24 failures, 66 errors, 24 skips |
| Full backend after cleanup | Same 719 tests and identical failure/error names; zero newly failing checks |
| Docker/Rust/service integration | Not run: no Docker/Cargo or live PostgreSQL/Redis/native extension here |

The backend runs used Python 3.12 with project packages installed into an isolated directory and other available runtime dependencies. Full market/reference data and services were not provisioned. Errors include missing native/data/dependency prerequisites and existing test/code expectation mismatches. The unchanged before/after outcome supports cleanup isolation; it is **not** a claim that the production test suite is green. Run it on the existing provisioned development machine before merging.

## Guardrails and recovery

Ignore rules now cover the prepared CSV directory, alternate wheel outputs, loose backend workbook/debug outputs, database/cache files, and root generated schema reports. Intentional fixtures under `backend/tests/fixtures/` have cache/database-format exceptions. Existing retained tracked workbooks remain tracked despite the new loose-output pattern. Do not ignore `frontend/build/` piecemeal while it remains a tracked deployment input.

To inspect removed files, open the archive branch. For recovery in a checkout, restore **only the needed path** from `archive/pre-cleanup-2026-10-01`; do not reset working changes. Removing ignore rules or force-adding recovered raw data is unnecessary for local use. Never delete Docker volumes or rewrite history as part of this cleanup.
