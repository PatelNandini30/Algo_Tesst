use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs;
use std::path::Path;

use chrono::{Datelike, NaiveDate};
use csv::{Reader, StringRecord};
use thiserror::Error;

use crate::{MarketData, Ohlc, OptionKey};

const ESTIMATED_ROW_BYTES: usize = 256;

#[derive(Debug, Error)]
pub enum MarketDataError {
    #[error("invalid market-data path: {0}")]
    InvalidPath(String),
    #[error("market-data I/O failed for {path}: {message}")]
    Io { path: String, message: String },
    #[error("market-data schema error in {path}: {message}")]
    Schema { path: String, message: String },
    #[error("market-data memory budget exceeded after {rows} rows (budget {budget_bytes} bytes)")]
    MemoryBudgetExceeded { rows: usize, budget_bytes: usize },
    #[error("market-data allocation refused: {0}")]
    Allocation(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
struct CompactOptionKey {
    date: i32,
    expiry: i32,
    strike_minor: i64,
    option_type: u8,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
struct CompactFutureKey {
    date: i32,
    expiry: i32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
struct CompactChainKey {
    date: i32,
    expiry: i32,
    option_type: u8,
}

/// A single-symbol, read-only cache built directly by Rust from the repository
/// CSV inputs. Rows are streamed from disk and admitted under a hard row budget;
/// source files are never loaded into one giant byte/string buffer.
pub struct CsvMarketData {
    symbol: String,
    options: HashMap<CompactOptionKey, Ohlc>,
    chains: HashMap<CompactChainKey, Vec<(i64, Ohlc)>>,
    futures: HashMap<CompactFutureKey, Ohlc>,
    spot: HashMap<i32, Ohlc>,
    trading_days: BTreeSet<i32>,
    expiries: BTreeSet<i32>,
    filters: HashMap<String, Vec<(i32, i32)>>,
    budget_bytes: usize,
    admitted_rows: usize,
}

/// Read-only, budget-partitioned market cache for strategies whose legs span
/// more than one index. Each symbol keeps its native expiry/strike namespace;
/// no cross-index fallback is allowed.
pub struct CsvMarketDataSet {
    markets: HashMap<String, CsvMarketData>,
    budget_bytes: usize,
}

impl CsvMarketDataSet {
    pub fn load(
        repository_root: impl AsRef<Path>,
        symbols: &[String],
        from: NaiveDate,
        to: NaiveDate,
        budget_bytes: usize,
    ) -> Result<Self, MarketDataError> {
        let mut normalized = symbols
            .iter()
            .map(|symbol| symbol.trim().to_ascii_uppercase())
            .filter(|symbol| !symbol.is_empty())
            .collect::<Vec<_>>();
        normalized.sort_unstable();
        normalized.dedup();
        if normalized.is_empty() {
            return Err(MarketDataError::InvalidPath("no market symbols requested".into()));
        }
        let per_symbol_budget = budget_bytes / normalized.len();
        if per_symbol_budget < ESTIMATED_ROW_BYTES {
            return Err(MarketDataError::MemoryBudgetExceeded {
                rows: 0,
                budget_bytes,
            });
        }
        let mut markets = HashMap::with_capacity(normalized.len());
        for symbol in normalized {
            let market = CsvMarketData::load(
                repository_root.as_ref(),
                &symbol,
                from,
                to,
                per_symbol_budget,
            )?;
            markets.insert(symbol, market);
        }
        Ok(Self {
            markets,
            budget_bytes,
        })
    }

    /// Same as [`CsvMarketDataSet::load`] but each symbol is built from
    /// PostgreSQL. `repository_root` is still used for the CSV filter mirror.
    pub fn load_from_postgres(
        connection_string: &str,
        repository_root: impl AsRef<Path>,
        symbols: &[String],
        from: NaiveDate,
        to: NaiveDate,
        budget_bytes: usize,
    ) -> Result<Self, MarketDataError> {
        let mut normalized = symbols
            .iter()
            .map(|symbol| symbol.trim().to_ascii_uppercase())
            .filter(|symbol| !symbol.is_empty())
            .collect::<Vec<_>>();
        normalized.sort_unstable();
        normalized.dedup();
        if normalized.is_empty() {
            return Err(MarketDataError::InvalidPath("no market symbols requested".into()));
        }
        let per_symbol_budget = budget_bytes / normalized.len();
        if per_symbol_budget < ESTIMATED_ROW_BYTES {
            return Err(MarketDataError::MemoryBudgetExceeded {
                rows: 0,
                budget_bytes,
            });
        }
        let mut markets = HashMap::with_capacity(normalized.len());
        for symbol in normalized {
            let market = CsvMarketData::load_from_postgres(
                connection_string,
                repository_root.as_ref(),
                &symbol,
                from,
                to,
                per_symbol_budget,
            )?;
            markets.insert(symbol, market);
        }
        Ok(Self {
            markets,
            budget_bytes,
        })
    }

    pub fn admitted_rows(&self) -> usize {
        self.markets.values().map(CsvMarketData::admitted_rows).sum()
    }

    pub fn estimated_resident_bytes(&self) -> usize {
        self.markets
            .values()
            .map(CsvMarketData::estimated_resident_bytes)
            .sum()
    }

    pub fn budget_bytes(&self) -> usize {
        self.budget_bytes
    }

    fn market(&self, symbol: &str) -> Option<&CsvMarketData> {
        self.markets.get(&symbol.trim().to_ascii_uppercase())
    }
}

impl MarketData for CsvMarketDataSet {
    fn option_ohlc(&self, key: &OptionKey) -> Option<Ohlc> {
        self.market(&key.symbol)?.option_ohlc(key)
    }

    fn spot(&self, symbol: &str, date: NaiveDate) -> Option<Ohlc> {
        self.market(symbol)?.spot(symbol, date)
    }

    fn future_ohlc(&self, symbol: &str, date: NaiveDate, expiry: NaiveDate) -> Option<Ohlc> {
        self.market(symbol)?.future_ohlc(symbol, date, expiry)
    }

    fn trading_days(&self, symbol: &str, from: NaiveDate, to: NaiveDate) -> Vec<NaiveDate> {
        self.market(symbol)
            .map(|market| market.trading_days(symbol, from, to))
            .unwrap_or_default()
    }

    fn expiries(&self, symbol: &str, from: NaiveDate, to: NaiveDate) -> Vec<NaiveDate> {
        self.market(symbol)
            .map(|market| market.expiries(symbol, from, to))
            .unwrap_or_default()
    }

    fn option_chain(
        &self,
        symbol: &str,
        date: NaiveDate,
        expiry: NaiveDate,
        option_type: &str,
    ) -> Vec<(f64, Ohlc)> {
        self.market(symbol)
            .map(|market| market.option_chain(symbol, date, expiry, option_type))
            .unwrap_or_default()
    }

    fn futures_expiries(&self, symbol: &str, from: NaiveDate, to: NaiveDate) -> Vec<NaiveDate> {
        self.market(symbol)
            .map(|market| market.futures_expiries(symbol, from, to))
            .unwrap_or_default()
    }

    fn filter_segments(&self, config: &str) -> Vec<(NaiveDate, NaiveDate)> {
        self.markets
            .values()
            .next()
            .map(|market| market.filter_segments(config))
            .unwrap_or_default()
    }
}

impl CsvMarketData {
    pub fn load(
        repository_root: impl AsRef<Path>,
        symbol: &str,
        from: NaiveDate,
        to: NaiveDate,
        budget_bytes: usize,
    ) -> Result<Self, MarketDataError> {
        let root = repository_root.as_ref();
        if !root.is_dir() {
            return Err(MarketDataError::InvalidPath(root.display().to_string()));
        }
        if from > to || budget_bytes < ESTIMATED_ROW_BYTES {
            return Err(MarketDataError::InvalidPath(
                "invalid date range or memory budget".into(),
            ));
        }
        let mut cache = Self {
            symbol: symbol.trim().to_ascii_uppercase(),
            options: HashMap::new(),
            chains: HashMap::new(),
            futures: HashMap::new(),
            spot: HashMap::new(),
            trading_days: BTreeSet::new(),
            expiries: BTreeSet::new(),
            filters: HashMap::new(),
            budget_bytes,
            admitted_rows: 0,
        };
        cache.load_derivatives(&root.join("cleaned_csvs"), from, to)?;
        cache.load_spot_sources(&root.join("strikeData"), from, to)?;
        cache.load_filter_sources(&root.join("Filter"))?;
        Ok(cache)
    }

    /// Build the same single-symbol cache directly from PostgreSQL (`option_data`
    /// + `spot_data`), reusing the identical row inserts as the CSV path so the
    /// resulting maps are byte-identical for equal source data. Read-only: a
    /// single SELECT per table, no writes, no schema access.
    pub fn load_from_postgres(
        connection_string: &str,
        repository_root: impl AsRef<Path>,
        symbol: &str,
        from: NaiveDate,
        to: NaiveDate,
        budget_bytes: usize,
    ) -> Result<Self, MarketDataError> {
        if from > to || budget_bytes < ESTIMATED_ROW_BYTES {
            return Err(MarketDataError::InvalidPath(
                "invalid date range or memory budget".into(),
            ));
        }
        let mut cache = Self {
            symbol: symbol.trim().to_ascii_uppercase(),
            options: HashMap::new(),
            chains: HashMap::new(),
            futures: HashMap::new(),
            spot: HashMap::new(),
            trading_days: BTreeSet::new(),
            expiries: BTreeSet::new(),
            filters: HashMap::new(),
            budget_bytes,
            admitted_rows: 0,
        };
        let mut client = postgres::Client::connect(connection_string, postgres::NoTls)
            .map_err(|error| pg_error("connect", error))?;

        // Resolve column names at runtime, mirroring the Python repository's
        // `_pick`: the live DB may use the migration-003 long names
        // (`trade_date`/`close_price`) or the short bhavcopy names
        // (`date`/`close`). Identifiers come from a fixed whitelist, not input.
        let opt = pg_columns(&mut client, "option_data")?;
        let d = pick(&opt, "trade_date", "date");
        let e = pick(&opt, "expiry_date", "expiry");
        let o = pick(&opt, "open_price", "open");
        let h = pick(&opt, "high_price", "high");
        let l = pick(&opt, "low_price", "low");
        let c = pick(&opt, "close_price", "close");

        // Derivatives (options + futures) for this symbol/range.
        let sql = format!(
            "SELECT {d}, {e}, instrument, strike_price::float8, option_type, \
             {o}::float8, {h}::float8, {l}::float8, {c}::float8, settled_price::float8 \
             FROM option_data \
             WHERE symbol = $1 AND {d} BETWEEN $2 AND $3 \
             ORDER BY {d}, {e}"
        );
        let rows = client
            .query(sql.as_str(), &[&cache.symbol, &from, &to])
            .map_err(|error| pg_error("query option_data", error))?;
        for row in &rows {
            let close = pg_f64(row.get::<_, Option<f64>>(8));
            if close <= 0.0 {
                continue;
            }
            let date: NaiveDate = row.get(0);
            let expiry: NaiveDate = row.get(1);
            let instrument: String = row.get::<_, String>(2).trim().to_ascii_uppercase();
            let is_future = instrument.starts_with("FUT");
            let option_type = if is_future {
                0
            } else {
                option_type_id(&row.get::<_, Option<String>>(4).unwrap_or_default())
            };
            let strike = pg_f64(row.get::<_, Option<f64>>(3));
            let settled = pg_f64(row.get::<_, Option<f64>>(9));
            let ohlc = Ohlc {
                open: pg_f64(row.get::<_, Option<f64>>(5)),
                high: pg_f64(row.get::<_, Option<f64>>(6)),
                low: pg_f64(row.get::<_, Option<f64>>(7)),
                close,
                settled: (settled > 0.0).then_some(settled),
            };
            cache.insert_derivative(date, expiry, is_future, option_type, strike, ohlc)?;
        }

        // Spot for this symbol/range (same runtime column resolution).
        let sp = pg_columns(&mut client, "spot_data")?;
        let sd = pick(&sp, "trade_date", "date");
        let so = pick(&sp, "open_price", "open");
        let sh = pick(&sp, "high_price", "high");
        let sl = pick(&sp, "low_price", "low");
        let sc = pick(&sp, "close_price", "close");
        let sql = format!(
            "SELECT {sd}, {so}::float8, {sh}::float8, {sl}::float8, {sc}::float8 \
             FROM spot_data \
             WHERE symbol = $1 AND {sd} BETWEEN $2 AND $3 \
             ORDER BY {sd}"
        );
        let rows = client
            .query(sql.as_str(), &[&cache.symbol, &from, &to])
            .map_err(|error| pg_error("query spot_data", error))?;
        let mut found = false;
        for row in &rows {
            let date: NaiveDate = row.get(0);
            let close = pg_f64(row.get::<_, Option<f64>>(4));
            if cache.insert_spot(
                date,
                from,
                to,
                Some(pg_f64(row.get::<_, Option<f64>>(1))),
                Some(pg_f64(row.get::<_, Option<f64>>(2))),
                Some(pg_f64(row.get::<_, Option<f64>>(3))),
                close,
            )? {
                found = true;
            }
        }
        if !found {
            return Err(MarketDataError::InvalidPath(format!(
                "no spot rows for {} in spot_data",
                cache.symbol
            )));
        }

        // Folder-based filters from the live `filter_date_sets` table (keyed
        // verbatim by filter_key), mirroring `get_filter_date_segments`.
        cache.load_filter_date_sets(&mut client)?;
        // Legacy 5x1/5x2 still come from the CSV mirror if present (not in the
        // read schema); harmless when the folder is absent.
        let _ = cache.load_filter_sources(repository_root.as_ref().join("Filter").as_path());
        Ok(cache)
    }

    pub fn admitted_rows(&self) -> usize {
        self.admitted_rows
    }

    pub fn estimated_resident_bytes(&self) -> usize {
        self.admitted_rows.saturating_mul(ESTIMATED_ROW_BYTES)
    }

    fn admit(&mut self) -> Result<(), MarketDataError> {
        let next = self.admitted_rows.saturating_add(1);
        if next.saturating_mul(ESTIMATED_ROW_BYTES) > self.budget_bytes {
            return Err(MarketDataError::MemoryBudgetExceeded {
                rows: self.admitted_rows,
                budget_bytes: self.budget_bytes,
            });
        }
        self.admitted_rows = next;
        Ok(())
    }

    fn load_derivatives(
        &mut self,
        directory: &Path,
        from: NaiveDate,
        to: NaiveDate,
    ) -> Result<(), MarketDataError> {
        let entries = fs::read_dir(directory).map_err(|error| io_error(directory, error))?;
        let mut files = Vec::new();
        for entry in entries {
            let entry = entry.map_err(|error| io_error(directory, error))?;
            let path = entry.path();
            let Some(stem) = path.file_stem().and_then(|v| v.to_str()) else {
                continue;
            };
            let Ok(date) = NaiveDate::parse_from_str(stem, "%Y-%m-%d") else {
                continue;
            };
            if from <= date && date <= to {
                files.push((date, path));
            }
        }
        files.sort_by_key(|(date, _)| *date);
        for (date, path) in files {
            self.load_derivative_day(date, &path)?;
        }
        Ok(())
    }

    fn load_derivative_day(&mut self, date: NaiveDate, path: &Path) -> Result<(), MarketDataError> {
        let mut reader = Reader::from_path(path).map_err(|error| io_error(path, error))?;
        let headers = reader
            .headers()
            .map_err(|error| schema_error(path, error))?
            .clone();
        let column = |name: &str| {
            headers
                .iter()
                .position(|header| header.trim().eq_ignore_ascii_case(name))
                .ok_or_else(|| MarketDataError::Schema {
                    path: path.display().to_string(),
                    message: format!("missing column {name}"),
                })
        };
        let symbol_col = column("Symbol")?;
        let instrument_col = column("Instrument")?;
        let expiry_col = column("ExpiryDate")?;
        let strike_col = column("StrikePrice")?;
        let type_col = column("OptionType")?;
        let open_col = column("Open")?;
        let high_col = column("High")?;
        let low_col = column("Low")?;
        let close_col = column("Close")?;
        let settled_col = headers
            .iter()
            .position(|header| header.trim().eq_ignore_ascii_case("SettledPrice"));

        for row in reader.records() {
            let row = row.map_err(|error| schema_error(path, error))?;
            if !field(&row, symbol_col).eq_ignore_ascii_case(&self.symbol) {
                continue;
            }
            let Some(expiry) = parse_date(field(&row, expiry_col)) else {
                continue;
            };
            let ohlc = Ohlc {
                open: parse_f64(field(&row, open_col)),
                high: parse_f64(field(&row, high_col)),
                low: parse_f64(field(&row, low_col)),
                close: parse_f64(field(&row, close_col)),
                settled: settled_col
                    .map(|column| parse_f64(field(&row, column)))
                    .filter(|value| *value > 0.0),
            };
            if ohlc.close <= 0.0 {
                continue;
            }
            let instrument = field(&row, instrument_col).trim().to_ascii_uppercase();
            let is_future = instrument.starts_with("FUT");
            let option_type = if is_future {
                0
            } else {
                option_type_id(field(&row, type_col))
            };
            let strike = parse_f64(field(&row, strike_col));
            self.insert_derivative(date, expiry, is_future, option_type, strike, ohlc)?;
        }
        Ok(())
    }

    /// Single row-level insert shared by the CSV day loader and the Postgres
    /// loader, so both fill the compact maps identically (parity by construction).
    fn insert_derivative(
        &mut self,
        date: NaiveDate,
        expiry: NaiveDate,
        is_future: bool,
        option_type: u8,
        strike: f64,
        ohlc: Ohlc,
    ) -> Result<(), MarketDataError> {
        let date_days = days(date);
        let expiry_days = days(expiry);
        self.admit()?;
        if is_future {
            self.futures.insert(
                CompactFutureKey {
                    date: date_days,
                    expiry: expiry_days,
                },
                ohlc,
            );
        } else {
            if option_type == 0 {
                self.admitted_rows -= 1;
                return Ok(());
            }
            let strike_minor = (strike * 100.0).round() as i64;
            self.options.insert(
                CompactOptionKey {
                    date: date_days,
                    expiry: expiry_days,
                    strike_minor,
                    option_type,
                },
                ohlc,
            );
            self.chains
                .entry(CompactChainKey {
                    date: date_days,
                    expiry: expiry_days,
                    option_type,
                })
                .or_default()
                .push((strike_minor, ohlc));
        }
        self.expiries.insert(expiry_days);
        Ok(())
    }

    fn load_spot_sources(
        &mut self,
        directory: &Path,
        from: NaiveDate,
        to: NaiveDate,
    ) -> Result<(), MarketDataError> {
        let mut paths = vec![directory.join("index_strike_data.csv")];
        paths.extend([
            directory.join(format!("{}_strike_data.csv", self.symbol)),
            directory.join(format!(
                "{}_strike_data.csv",
                self.symbol.to_ascii_lowercase()
            )),
            directory.join(format!(
                "{}_strike_data.csv",
                title_case_ascii(&self.symbol)
            )),
            directory.join("Sensex.csv"),
        ]);
        if self.symbol == "NIFTYMIDCAP100" {
            paths.push(directory.join("MIDCAP100.csv"));
        }
        paths.sort();
        paths.dedup();
        let mut found = false;
        for path in paths {
            if !path.is_file() {
                continue;
            }
            found |= self.load_spot_file(&path, from, to)?;
        }
        if !found {
            return Err(MarketDataError::InvalidPath(format!(
                "no spot rows for {} in {}",
                self.symbol,
                directory.display()
            )));
        }
        Ok(())
    }

    fn load_spot_file(
        &mut self,
        path: &Path,
        from: NaiveDate,
        to: NaiveDate,
    ) -> Result<bool, MarketDataError> {
        let mut reader = Reader::from_path(path).map_err(|error| io_error(path, error))?;
        let headers = reader
            .headers()
            .map_err(|error| schema_error(path, error))?
            .clone();
        let date_col = headers
            .iter()
            .position(|h| h.trim().eq_ignore_ascii_case("Date"))
            .or_else(|| {
                headers
                    .iter()
                    .position(|h| h.trim().eq_ignore_ascii_case("Date/Time"))
            });
        let close_col = headers
            .iter()
            .position(|h| h.trim().eq_ignore_ascii_case("Close"));
        let open_col = headers
            .iter()
            .position(|h| h.trim().eq_ignore_ascii_case("Open"));
        let high_col = headers
            .iter()
            .position(|h| h.trim().eq_ignore_ascii_case("High"));
        let low_col = headers
            .iter()
            .position(|h| h.trim().eq_ignore_ascii_case("Low"));
        let ticker_col = headers.iter().position(|h| {
            h.trim().eq_ignore_ascii_case("Ticker") || h.trim().eq_ignore_ascii_case("Symbol")
        });
        let (Some(date_col), Some(close_col)) = (date_col, close_col) else {
            return Ok(false);
        };
        let mut found = false;
        for row in reader.records() {
            let row = row.map_err(|error| schema_error(path, error))?;
            if ticker_col.is_some_and(|column| {
                !field(&row, column)
                    .trim()
                    .eq_ignore_ascii_case(&self.symbol)
            }) {
                continue;
            }
            let Some(date) = parse_date(field(&row, date_col)) else {
                continue;
            };
            if date < from || date > to {
                continue;
            }
            let close = parse_f64(field(&row, close_col));
            let open = open_col.map(|column| parse_f64(field(&row, column)));
            let high = high_col.map(|column| parse_f64(field(&row, column)));
            let low = low_col.map(|column| parse_f64(field(&row, column)));
            if self.insert_spot(date, from, to, open, high, low, close)? {
                found = true;
            }
        }
        Ok(found)
    }

    /// Row-level spot insert shared by the CSV and Postgres loaders. Returns
    /// whether the row was admitted (in range and priced).
    fn insert_spot(
        &mut self,
        date: NaiveDate,
        from: NaiveDate,
        to: NaiveDate,
        open: Option<f64>,
        high: Option<f64>,
        low: Option<f64>,
        close: f64,
    ) -> Result<bool, MarketDataError> {
        if date < from || date > to || close <= 0.0 {
            return Ok(false);
        }
        let date_days = days(date);
        if !self.spot.contains_key(&date_days) {
            self.admit()?;
        }
        self.spot.insert(
            date_days,
            Ohlc {
                open: open.filter(|value| *value > 0.0).unwrap_or(close),
                high: high.filter(|value| *value > 0.0).unwrap_or(close),
                low: low.filter(|value| *value > 0.0).unwrap_or(close),
                close,
                settled: None,
            },
        );
        self.trading_days.insert(date_days);
        Ok(true)
    }

    /// Load folder-based filters from the live `filter_date_sets` table, keyed
    /// verbatim by `filter_key` and ordered by `seq`, mirroring the Python
    /// `get_filter_date_segments`. A missing table (undefined_table) is treated
    /// as "no folder filters" — harmless, same as the Python try/except.
    fn load_filter_date_sets(
        &mut self,
        client: &mut postgres::Client,
    ) -> Result<(), MarketDataError> {
        let rows = match client.query(
            "SELECT filter_key, start_date, end_date \
             FROM filter_date_sets ORDER BY filter_key, seq",
            &[],
        ) {
            Ok(rows) => rows,
            Err(_) => return Ok(()),
        };
        for row in &rows {
            let key: String = row.get(0);
            let start: NaiveDate = row.get(1);
            let end: NaiveDate = row.get(2);
            if end >= start {
                self.filters
                    .entry(key)
                    .or_default()
                    .push((days(start), days(end)));
            }
        }
        for segments in self.filters.values_mut() {
            segments.sort();
        }
        Ok(())
    }

    fn load_filter_sources(&mut self, directory: &Path) -> Result<(), MarketDataError> {
        for (config, filename) in [("5x1", "STR5,1_5,1.csv"), ("5x2", "STR5,2_5,2.csv")] {
            let path = directory.join(filename);
            if !path.is_file() {
                continue;
            }
            let mut reader = Reader::from_path(&path).map_err(|error| io_error(&path, error))?;
            let headers = reader
                .headers()
                .map_err(|error| schema_error(&path, error))?
                .clone();
            let start_col = headers
                .iter()
                .position(|header| header.trim().eq_ignore_ascii_case("Start"));
            let end_col = headers
                .iter()
                .position(|header| header.trim().eq_ignore_ascii_case("End"));
            let (Some(start_col), Some(end_col)) = (start_col, end_col) else {
                continue;
            };
            let mut segments = Vec::new();
            for row in reader.records() {
                let row = row.map_err(|error| schema_error(&path, error))?;
                if let (Some(start), Some(end)) = (
                    parse_date(field(&row, start_col)),
                    parse_date(field(&row, end_col)),
                ) {
                    segments.push((days(start), days(end)));
                }
            }
            segments.sort();
            self.filters.insert(config.into(), segments);
        }
        Ok(())
    }
}

impl MarketData for CsvMarketData {
    fn option_ohlc(&self, key: &OptionKey) -> Option<Ohlc> {
        if !key.symbol.eq_ignore_ascii_case(&self.symbol) {
            return None;
        }
        self.options
            .get(&CompactOptionKey {
                date: days(key.date),
                expiry: days(key.expiry),
                strike_minor: key.strike_minor,
                option_type: option_type_id(&key.option_type),
            })
            .copied()
    }

    fn spot(&self, symbol: &str, date: NaiveDate) -> Option<Ohlc> {
        symbol
            .eq_ignore_ascii_case(&self.symbol)
            .then(|| self.spot.get(&days(date)).copied())
            .flatten()
    }

    fn future_ohlc(&self, symbol: &str, date: NaiveDate, expiry: NaiveDate) -> Option<Ohlc> {
        if !symbol.eq_ignore_ascii_case(&self.symbol) {
            return None;
        }
        self.futures
            .get(&CompactFutureKey {
                date: days(date),
                expiry: days(expiry),
            })
            .copied()
    }

    fn trading_days(&self, symbol: &str, from: NaiveDate, to: NaiveDate) -> Vec<NaiveDate> {
        if !symbol.eq_ignore_ascii_case(&self.symbol) {
            return Vec::new();
        }
        self.trading_days
            .range(days(from)..=days(to))
            .filter_map(|value| NaiveDate::from_num_days_from_ce_opt(*value))
            .collect()
    }

    fn expiries(&self, symbol: &str, from: NaiveDate, to: NaiveDate) -> Vec<NaiveDate> {
        if !symbol.eq_ignore_ascii_case(&self.symbol) {
            return Vec::new();
        }
        self.expiries
            .range(days(from)..=days(to))
            .filter_map(|value| NaiveDate::from_num_days_from_ce_opt(*value))
            .collect()
    }

    fn option_chain(
        &self,
        symbol: &str,
        date: NaiveDate,
        expiry: NaiveDate,
        option_type: &str,
    ) -> Vec<(f64, Ohlc)> {
        if !symbol.eq_ignore_ascii_case(&self.symbol) {
            return Vec::new();
        }
        for offset in [0, 1, -1] {
            let Some(expiry) = expiry.checked_add_signed(chrono::Duration::days(offset)) else {
                continue;
            };
            if let Some(chain) = self.chains.get(&CompactChainKey {
                date: days(date),
                expiry: days(expiry),
                option_type: option_type_id(option_type),
            }) {
                return chain
                    .iter()
                    .map(|(strike, ohlc)| (*strike as f64 / 100.0, *ohlc))
                    .collect();
            }
        }
        Vec::new()
    }

    fn futures_expiries(&self, symbol: &str, from: NaiveDate, to: NaiveDate) -> Vec<NaiveDate> {
        if !symbol.eq_ignore_ascii_case(&self.symbol) {
            return Vec::new();
        }
        self.futures
            .keys()
            .map(|key| key.expiry)
            .filter(|expiry| days(from) <= *expiry && *expiry <= days(to))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .filter_map(NaiveDate::from_num_days_from_ce_opt)
            .collect()
    }

    fn filter_segments(&self, config: &str) -> Vec<(NaiveDate, NaiveDate)> {
        self.filters
            .get(config)
            .into_iter()
            .flatten()
            .filter_map(|(start, end)| {
                Some((
                    NaiveDate::from_num_days_from_ce_opt(*start)?,
                    NaiveDate::from_num_days_from_ce_opt(*end)?,
                ))
            })
            .collect()
    }
}

fn field(row: &StringRecord, column: usize) -> &str {
    row.get(column).unwrap_or("")
}

fn parse_f64(value: &str) -> f64 {
    value.trim().parse().unwrap_or(0.0)
}

fn pg_f64(value: Option<f64>) -> f64 {
    value.unwrap_or(0.0)
}

/// Cheap best-effort data version for cache invalidation: the newest logged
/// import id (`data_import_log`). Absent/empty/unreachable → 0, so a warm cache
/// then relies on process restart (the migrate + restart data-refresh workflow).
/// Read-only, single indexed max over a tiny table (~2 ms).
pub fn data_version(database_url: &str) -> i64 {
    let Ok(mut client) = postgres::Client::connect(database_url, postgres::NoTls) else {
        return 0;
    };
    match client.query_one(
        "SELECT COALESCE(MAX(id), 0)::bigint FROM data_import_log",
        &[],
    ) {
        Ok(row) => row.try_get::<_, i64>(0).unwrap_or(0),
        Err(_) => 0,
    }
}

/// Actual column names of a table (read-only information_schema query), so the
/// loader adapts to either schema naming exactly like the Python repository.
fn pg_columns(client: &mut postgres::Client, table: &str) -> Result<HashSet<String>, MarketDataError> {
    let rows = client
        .query(
            "SELECT column_name FROM information_schema.columns \
             WHERE table_schema='public' AND table_name=$1",
            &[&table],
        )
        .map_err(|error| pg_error("introspect columns", error))?;
    Ok(rows.iter().map(|row| row.get::<_, String>(0)).collect())
}

/// Mirror of the Python `_pick`: preferred name if present, else the fallback.
fn pick<'a>(cols: &HashSet<String>, preferred: &'a str, fallback: &'a str) -> &'a str {
    if cols.contains(preferred) {
        preferred
    } else {
        fallback
    }
}

fn pg_error(stage: &str, error: postgres::Error) -> MarketDataError {
    // The top-level Display is often just "db error"; surface the DbError detail.
    let detail = error
        .as_db_error()
        .map(|db| format!("{}: {}", db.code().code(), db.message()))
        .unwrap_or_else(|| error.to_string());
    MarketDataError::Io {
        path: format!("postgres:{stage}"),
        message: detail,
    }
}

fn parse_date(value: &str) -> Option<NaiveDate> {
    let value = value.trim();
    [
        "%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y", "%d-%b-%Y", "%d-%b-%y", "%m/%d/%Y",
    ]
    .iter()
    .find_map(|format| NaiveDate::parse_from_str(value, format).ok())
}

fn days(date: NaiveDate) -> i32 {
    date.num_days_from_ce()
}

fn option_type_id(value: &str) -> u8 {
    match value.trim().to_ascii_uppercase().as_str() {
        "CE" | "CALL" => 1,
        "PE" | "PUT" => 2,
        _ => 0,
    }
}

fn title_case_ascii(value: &str) -> String {
    let mut chars = value.chars();
    match chars.next() {
        Some(first) => {
            first.to_ascii_uppercase().to_string() + &chars.as_str().to_ascii_lowercase()
        }
        None => String::new(),
    }
}

fn io_error(path: &Path, error: impl std::fmt::Display) -> MarketDataError {
    MarketDataError::Io {
        path: path.display().to_string(),
        message: error.to_string(),
    }
}

fn schema_error(path: &Path, error: impl std::fmt::Display) -> MarketDataError {
    MarketDataError::Schema {
        path: path.display().to_string(),
        message: error.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_repository_date_formats() {
        assert_eq!(parse_date("2024-01-02").unwrap().to_string(), "2024-01-02");
        assert_eq!(parse_date("02-Jan-2024").unwrap().to_string(), "2024-01-02");
        assert_eq!(parse_date("1/2/2024").unwrap().to_string(), "2024-01-02");
    }

    #[test]
    fn row_budget_fails_closed() {
        let mut cache = CsvMarketData {
            symbol: "NIFTY".into(),
            options: HashMap::new(),
            chains: HashMap::new(),
            futures: HashMap::new(),
            spot: HashMap::new(),
            trading_days: BTreeSet::new(),
            expiries: BTreeSet::new(),
            filters: HashMap::new(),
            budget_bytes: ESTIMATED_ROW_BYTES,
            admitted_rows: 0,
        };
        assert!(cache.admit().is_ok());
        assert!(matches!(
            cache.admit(),
            Err(MarketDataError::MemoryBudgetExceeded { .. })
        ));
    }
}
