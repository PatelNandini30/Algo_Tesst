# PostgreSQL Schema Design (CSV Replacement)

## CSV inventory

| Family | File count | Header variants | Actively used in runtime |
|---|---:|---:|---|
| `cleaned_csvs/` | 6380 | 1 | Yes (`load_bhavcopy`) |
| `expiryData/` | 6 | 2 | Yes (`load_expiry`) |
| `strikeData/` | 15 | 3 | Yes (`get_strike_data` for `*_strike_data.csv`) |
| `Filter/` | 3 | 1 | STR yes (`load_super_trend_dates`), base2 currently disabled in backtest |
| `Output/` | 1 | 1 | No (sample artifact only) |

## Semantic meaning of each CSV family

### 1) `cleaned_csvs/YYYY-MM-DD.csv`
- Purpose: daily bhavcopy-like option/future EOD data used for premium, strike, expiry, and future-price lookups.
- Used by:
  - `load_bhavcopy()`
  - option lookup cache (`get_option_premium_from_db`)
  - future lookup cache (`get_future_price_from_db`)
  - premium-range and closest-premium strike selection.
- Key columns:
  - identity: `Date`, `Symbol`, `Instrument`, `ExpiryDate`, `OptionType`, `StrikePrice`
  - prices: `Open`, `High`, `Low`, `Close`, `SettledPrice`
  - liquidity: `Contracts`, `TurnOver`, `OpenInterest`

### 2) `strikeData/*_strike_data.csv` and `strikeData/index_strike_data.csv`
- Purpose: spot/index close history (and optionally OHLC).
- Used by:
  - `get_strike_data()` for date-range spot series and entry/exit spot.
- Key columns:
  - identity: `Ticker`, `Date`
  - price: `Close` (+ optional `Open`, `High`, `Low`)

### 3) `expiryData/*.csv`
- Purpose: weekly/monthly expiry mapping (`Previous Expiry`, `Current Expiry`, `Next Expiry`) per index.
- Used by:
  - `load_expiry()`
  - expiry selection logic (`get_expiry_for_selection`, engine expiry flow).

### 4) `Filter/base2.csv`
- Purpose: date ranges of unavailable/filtered periods.
- Current runtime status:
  - backtest path has `load_base2()` disabled.
  - still relevant for data governance and migration.

### 5) `Filter/STR5,1_5,1.csv`, `Filter/STR5,2_5,2.csv`
- Purpose: SuperTrend enablement segments (start/end windows).
- Used by:
  - `load_super_trend_dates()`
  - `get_active_str_segment()`

### 6) `Output/...csv`
- Purpose: generated strategy output sample (trade sheet style), not currently loaded by backend.
- Useful as target shape for result tables.

## Raw / result / intermediate columns

### Raw input columns
- `cleaned_csvs`: `Date`, `ExpiryDate`, `Instrument`, `Symbol`, `StrikePrice`, `OptionType`, `Open`, `High`, `Low`, `Close`, `SettledPrice`, `Contracts`, `TurnOver`, `OpenInterest`
- `strikeData`: `Ticker`, `Date`, `Close` (+ optional `Open`, `High`, `Low`, `Time`, `Quantity`, `Average`, `STR-1..3`)
- `expiryData`: `Symbol` (weekly only), `Previous Expiry`, `Current Expiry`, `Next Expiry`
- `Filter`: `Start`, `End`

### Calculation / intermediate columns (from engines)
- `Index`, `Trade`, `Leg`, `Entry Date`, `Exit Date`
- `Entry Spot`, `Exit Spot`, `Spot P&L`
- `Type`, `Strike`, `B/S`, `Qty`, `Entry Price`, `Exit Price`, `Future Expiry`
- `% P&L`, `Exit Reason`, `STR Segment`
- `Cumulative`, `Peak`, `DD`, `%DD`

### Result/output columns
- API trades: detailed per-leg rows (engine output).
- Summary metrics from `compute_analytics()`:
  - `total_pnl`, `count`, `win_pct`, `loss_pct`, `avg_win`, `avg_loss`, `max_dd_pct`, `car_mdd`, etc.
- Pivot output from `build_pivot()`:
  - per-year month columns + `Total`, `Max Drawdown`, `Days for MDD`, `R/MDD`.
- CSV exports:
  - trade sheet (`generate_trade_sheet`)
  - summary report (`generate_summary_report`).

## Relationships between files/tables

- `option_data.symbol` joins `spot_data.symbol`.
- `option_data.expiry_date` aligns with `expiry_calendar.current_expiry`.
- backtest run scope (`index_symbol`, `date_from`, `date_to`) filters all source tables.
- `super_trend_segments` and `trading_holidays` are calendar overlays.

## Proposed PostgreSQL schema

Implemented in:
- `migrations/003_postgres_csv_replacement_schema.sql`

### Source/raw tables
- `option_data`
- `spot_data`
- `expiry_calendar`
- `trading_holidays`
- `super_trend_segments`

### Processed/result tables
- `backtest_runs`
- `backtest_trade_legs`
- `backtest_run_summary`
- `backtest_run_pivot_yearly`

### Audit/import metadata
- `import_batches`
- `import_files`

## Table-by-table explanation

### `option_data`
- Replaces daily files under `cleaned_csvs/`.
- Unique quote identity includes `trade_date + symbol + instrument + expiry_date + option_type + strike_price`.
- Indexed for the actual lookups in `base.py` (date/symbol/option/expiry/strike and futures lookup path).

### `spot_data`
- Replaces strike/index spot files.
- Supports both compact (`Ticker,Date,Close`) and richer OHLC style rows.
- Unique `(trade_date, symbol)`; indexed by `(symbol, trade_date)` for date-range scans.

### `expiry_calendar`
- Normalizes weekly/monthly expiry timelines.
- Unique `(symbol, expiry_type, current_expiry)` and index for fast selection.

### `trading_holidays`
- Keeps base2 periods as structured date ranges.
- Stored even if currently not enforced by runtime filters.

### `super_trend_segments`
- Stores STR windows by `symbol` + `config`.
- Designed to directly support `trade_date BETWEEN start_date AND end_date`.

### `backtest_runs`
- Request envelope + status for each execution.
- Enables deterministic cache key via `request_hash`.

### `backtest_trade_legs`
- Persistent version of engine leg rows.
- Includes analytics columns (`cumulative`, `peak`, `drawdown`, `drawdown_pct`) for deterministic replay/export.

### `backtest_run_summary`
- 1:1 run summary snapshot from `compute_analytics()`.

### `backtest_run_pivot_yearly`
- Persistent pivot rows as returned by `build_pivot()`.

### `import_batches`, `import_files`
- Track source file lineage, load counts, failures, and idempotency keys (hash).

## CSV-to-table mapping

| CSV path pattern | Target table(s) | Notes |
|---|---|---|
| `cleaned_csvs/*.csv` | `option_data` | one row per option/future quote |
| `strikeData/*_strike_data.csv` | `spot_data` | primary runtime spot source |
| `strikeData/index_strike_data.csv` | `spot_data` | multi-symbol spot source |
| `strikeData/DailyNC*.csv` | `spot_data` | optional richer OHLC/STR columns (currently unused) |
| `expiryData/*.csv` | `expiry_calendar` | infer `expiry_type` from filename |
| `Filter/base2.csv` | `trading_holidays` | semantic reason currently inferred |
| `Filter/STR*.csv` | `super_trend_segments` | config from filename (`5x1`, `5x2`) |
| `Output/**/*.csv` | `backtest_trade_legs` / `backtest_run_summary` (optional import) | output artifact, not runtime input |

## Index strategy

- `option_data`
  - unique quote key
  - option lookup index: `(trade_date, symbol, option_type, expiry_date, strike_price)` filtered to option instruments
  - futures lookup index: `(trade_date, symbol, expiry_date)` filtered to future instruments
- `spot_data`
  - unique `(trade_date, symbol)`
  - lookup `(symbol, trade_date)`
- `expiry_calendar`
  - unique `(symbol, expiry_type, current_expiry)`
  - lookup `(symbol, expiry_type, current_expiry)`
- `super_trend_segments`
  - lookup `(symbol, config, start_date, end_date)`
- `backtest_trade_legs`
  - `(run_id, trade_no, leg_no)` and `(run_id, entry_date, exit_date)`

Partitioning recommendation:
- Not mandatory now.
- Consider range partitioning `option_data` by `trade_date` only after table growth causes measurable vacuum/query pressure (for example >100M rows or sustained heavy concurrent backtests).

## Migration considerations and data-cleaning rules

1. Date parsing:
- Existing code accepts multiple formats (`%Y-%m-%d`, `%d-%m-%Y`, `%d-%b-%Y`, etc.).
- Migration loader should use robust parser + reject log for malformed rows.

2. Symbol normalization:
- `UPPER(TRIM(symbol))`.
- Unify aliases (`Nifty 50` vs `NIFTY`) via deterministic mapping table in loader.

3. Option/future normalization:
- `option_type` normalized to `CE|PE|NULL`.
- Validate `instrument` domain and enforce option/future consistency.

4. Numeric sanitization:
- parse blanks as NULL, strip commas, enforce non-negative checks where applicable.

5. Idempotent loading:
- use `ON CONFLICT DO UPDATE/NOTHING` on natural unique keys.
- track each source file in `import_files`.

6. STR ambiguity:
- CSV has only `Start,End`; no explicit symbol/trend.
- Current code assumes `symbol='NIFTY'`; keep as default unless source is expanded.

7. base2 ambiguity:
- semantics (holiday vs data outage) are not explicit.
- keep `reason` default `data_unavailable` until authoritative classification exists.

## Unclear columns/semantics flagged

- `strikeData/DailyNC*.csv`:
  - `STR-1`, `STR-2`, `STR-3` not consumed by runtime backtest path.
  - `Time` often empty.
- `Filter/STR*.csv`:
  - no trend direction and no symbol in file.
- `Filter/base2.csv`:
  - no explicit reason/category.

