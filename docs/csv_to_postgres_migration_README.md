# CSV To PostgreSQL Migration README

This project now uses `backend/migrate_data.py` as the primary CSV import utility.

## What it migrates

- Raw/source CSVs:
  - `cleaned_csvs/*.csv` -> `option_data`
  - `strikeData/*.csv` -> `spot_data`
  - `expiryData/*.csv` -> `expiry_calendar`
  - `Filter/base2.csv` -> `trading_holidays`
  - `Filter/STR*.csv` -> `super_trend_segments`

## Behavior and safety

- Idempotent where practical:
  - Uses update-then-insert key matching for source tables.
  - Re-running does not create duplicate key rows for source tables.
- Validation and visibility:
  - Tracks `rows_read`, `rows_valid`, `rows_rejected`, `duplicate_rows_in_file`, `rows_inserted`, `rows_updated`.
  - Writes JSON report with per-file details.
  - Optional DB validation (`--validate`) includes row counts and duplicate-key-group checks.
- Data handling:
  - Multi-encoding CSV read (`utf-8-sig`, `utf-8`, `latin-1`)
  - Robust date parsing (`YYYY-MM-DD`, `DD-MM-YYYY`, `DD-Mon-YYYY`, then day-first fallback)
  - Numeric parsing strips commas and treats empty strings as null
  - Empty/null-like strings normalized

## Prerequisites

1. PostgreSQL must be up.
2. Schema migration must be applied (especially `migrations/003_postgres_csv_replacement_schema.sql` for source/audit tables).
3. `DATABASE_URL` must point to your PostgreSQL instance.

## Commands (host machine)

From repo root:

```bash
python backend/migrate_data.py --all
python backend/migrate_data.py --table option_data
python backend/migrate_data.py --table spot_data
python backend/migrate_data.py --table expiry_calendar
python backend/migrate_data.py --table trading_holidays
python backend/migrate_data.py --table super_trend_segments
python backend/migrate_data.py --file cleaned_csvs/2025-06-12.csv
python backend/migrate_data.py --validate
```

Dry-run (parse/validate only, no DB writes):

```bash
python backend/migrate_data.py --all --dry-run --limit 10
```

Legacy flags remain supported:

```bash
python backend/migrate_data.py --option-data
python backend/migrate_data.py --spot-data
python backend/migrate_data.py --expiry-data
python backend/migrate_data.py --holiday-data
python backend/migrate_data.py --str-data
```

## Commands (Docker Compose)

From repo root:

```bash
docker compose up -d postgres backend
docker compose exec backend python migrate_data.py --all
docker compose exec backend python migrate_data.py --validate
docker compose exec backend python migrate_data.py --table option_data
docker compose exec backend python migrate_data.py --file /data/cleaned_csvs/2025-06-12.csv
```

Notes:
- In the backend container, script path is `/app/migrate_data.py`.
- CSV mounts are under `/data/...` in Docker.

## Report output

Default report file:

- `reports/csv_import_last_report.json`

Override path:

```bash
python backend/migrate_data.py --all --report-json reports/my_import_report.json
```

## Data integrity checks

- During import:
  - required-field validation
  - invalid date-range checks for `trading_holidays` and `super_trend_segments`
  - duplicate detection inside each file (before upsert)
- After import:
  - table row counts
  - duplicate key-group checks:
    - `option_data`: `(trade_date, symbol, instrument, expiry_date, coalesce(option_type,''), strike_price)`
    - `spot_data`: `(trade_date, symbol)`
    - `expiry_calendar`: `(symbol, expiry_type, current_expiry)`

## Manual cleanup candidates

Current known semantic ambiguities (not dropped automatically):

1. `Filter/STR*.csv` has no explicit symbol/trend direction. Import sets `symbol='NIFTY'`, `trend=NULL`.
2. `Filter/base2.csv` has no explicit reason category. Import sets `reason='data_unavailable'`.
3. `strikeData/DailyNC*.csv` carries `STR-1/2/3` and `Time`; runtime backtest currently does not consume these fields.
4. `Output/` folder import is intentionally disabled in `migrate_data.py`.

For row-level cleanup, use the generated JSON report and filter entries where:

- `status != "completed"` or
- `rows_rejected > 0` or
- `duplicate_rows_in_file > 0`
