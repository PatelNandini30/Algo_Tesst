//! CSV-vs-Postgres data-source parity.
//!
//! Runs one strategy through the native engine twice: once with the CSV mirror
//! loader and once with the PostgreSQL loader, then diffs trades + summary.
//! The CSV path is already snapshot-parity-verified against the live Python
//! system, so CSV == Postgres here transitively proves Postgres == Python for
//! equal source data. Read-only: a single SELECT per table, no writes.
//!
//! Usage:
//!   postgres_parity <repository-root> <DATABASE_URL> [index] [from] [to] [memory-mb]

use std::{path::PathBuf, sync::Arc};

use algotest_domain::{ComboOverride, StrategyConfig};
use algotest_engine::{market_data::CsvMarketData, native::NativeEngine, StrategyEngine};
use chrono::NaiveDate;
use serde::Serialize;
use serde_json::json;

#[derive(Serialize)]
struct Report {
    clean: bool,
    index: String,
    from: String,
    to: String,
    csv_rows: usize,
    postgres_rows: usize,
    csv_admitted_rows: usize,
    postgres_admitted_rows: usize,
    differences: Vec<String>,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let root = args.next().map(PathBuf::from).ok_or(
        "usage: postgres_parity <repository-root> <DATABASE_URL> [index] [from] [to] [memory-mb]",
    )?;
    let database_url = args.next().ok_or("missing DATABASE_URL argument")?;
    let index = args.next().unwrap_or_else(|| "NIFTY".into());
    let from_str = args.next().unwrap_or_else(|| "2024-01-01".into());
    let to_str = args.next().unwrap_or_else(|| "2024-03-31".into());
    let memory_mb: usize = args.next().map(|v| v.parse()).transpose()?.unwrap_or(768);
    let from = NaiveDate::parse_from_str(&from_str, "%Y-%m-%d")?;
    let to = NaiveDate::parse_from_str(&to_str, "%Y-%m-%d")?;
    let budget = memory_mb * 1024 * 1024;

    let csv = CsvMarketData::load(&root, &index, from, to, budget)?;
    let csv_admitted = csv.admitted_rows();
    let pg = CsvMarketData::load_from_postgres(&database_url, &root, &index, from, to, budget)?;
    let pg_admitted = pg.admitted_rows();

    let payload: StrategyConfig = serde_json::from_value(json!({
        "index": index,
        "from_date": from_str,
        "to_date": to_str,
        "expiry_type": "WEEKLY",
        "entry_dte": 1,
        "exit_dte": 0,
        "legs": [{
            "segment": "OPTIONS",
            "position": "SELL",
            "option_type": "CE",
            "expiry": "WEEKLY",
            "lots": 1,
            "strike_selection": {"type": "strike_type", "strike_type": "ATM"}
        }]
    }))?;
    let combo = ComboOverride {
        combo_id: 1,
        values: Default::default(),
    };

    let csv_result = NativeEngine::new(Arc::new(csv)).run(&payload, &combo)?;
    let pg_result = NativeEngine::new(Arc::new(pg)).run(&payload, &combo)?;

    let mut differences = Vec::new();
    if csv_result.trades.len() != pg_result.trades.len() {
        differences.push(format!(
            "row count: csv={} postgres={}",
            csv_result.trades.len(),
            pg_result.trades.len()
        ));
    }
    for (i, (c, p)) in csv_result
        .trades
        .iter()
        .zip(pg_result.trades.iter())
        .enumerate()
    {
        if c != p {
            differences.push(format!(
                "trade[{i}] differs: csv entry={}@{} exit={}@{} pnl={} | pg entry={}@{} exit={}@{} pnl={}",
                c.entry_date, c.entry_price, c.exit_date, c.exit_price, c.net_pnl,
                p.entry_date, p.entry_price, p.exit_date, p.exit_price, p.net_pnl,
            ));
        }
    }
    if csv_result.summary != pg_result.summary {
        differences.push("summary differs between CSV and Postgres".into());
    }

    let report = Report {
        clean: differences.is_empty(),
        index,
        from: from_str,
        to: to_str,
        csv_rows: csv_result.trades.len(),
        postgres_rows: pg_result.trades.len(),
        csv_admitted_rows: csv_admitted,
        postgres_admitted_rows: pg_admitted,
        differences,
    };
    println!("{}", serde_json::to_string_pretty(&report)?);
    if !report.clean {
        std::process::exit(2);
    }
    Ok(())
}
