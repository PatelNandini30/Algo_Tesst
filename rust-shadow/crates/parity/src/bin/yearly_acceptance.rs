use std::{path::PathBuf, sync::Arc};

use algotest_domain::{ComboOverride, StrategyConfig};
use algotest_engine::{market_data::CsvMarketData, native::NativeEngine, StrategyEngine};
use chrono::NaiveDate;
use serde::Serialize;
use serde_json::json;

#[derive(Serialize)]
struct Report {
    clean: bool,
    fixed_rows: usize,
    fresh_rows: usize,
    contract: String,
    fixed_strike: f64,
    implicit_yearly_matches: bool,
    differences: Vec<String>,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let root = std::env::args()
        .nth(1)
        .map(PathBuf::from)
        .ok_or("usage: yearly_acceptance <repository-root> [memory-mb]")?;
    let memory_mb = std::env::args()
        .nth(2)
        .map(|value| value.parse())
        .transpose()?
        .unwrap_or(768usize);
    let from = NaiveDate::from_ymd_opt(2019, 2, 1).unwrap();
    let to = NaiveDate::from_ymd_opt(2019, 11, 26).unwrap();
    let market = Arc::new(CsvMarketData::load(
        root,
        "NIFTY",
        from,
        to,
        memory_mb * 1024 * 1024,
    )?);
    let engine = NativeEngine::new(market);
    let payload = |mode: &str| {
        serde_json::from_value::<StrategyConfig>(json!({
            "index":"NIFTY",
            "from_date":"2019-02-01",
            "to_date":"2019-11-26",
            "expiry_type":"YEARLY",
            "entry_dte":0,
            "exit_dte":0,
            "per_leg_rollover":true,
            "rollover_toggle":true,
            "rollover_cadence":"monthly",
            "yearly_exit_months_before":1,
            "legs":[{
                "segment":"OPTIONS",
                "position":"BUY",
                "option_type":"PE",
                "expiry":"YEARLY",
                "lots":1,
                "strike_interval":1000,
                "rollover_strike_mode":mode,
                "strike_selection":{"type":"strike_type","strike_type":"ATM"}
            }]
        }))
    };
    let combo = ComboOverride {
        combo_id: 1,
        values: Default::default(),
    };
    let fixed = engine.run(&payload("fixed")?, &combo)?;
    let fresh = engine.run(&payload("fresh")?, &combo)?;
    let mut implicit = payload("fixed")?;
    implicit.per_leg_rollover = false;
    let implicit = engine.run(&implicit, &combo)?;
    let expected_dates = [
        "2019-02-28",
        "2019-03-28",
        "2019-04-25",
        "2019-05-30",
        "2019-06-27",
        "2019-07-25",
        "2019-08-29",
        "2019-09-26",
        "2019-10-31",
    ];
    let expected_fixed_entries = [625.0, 293.9, 290.5, 177.9, 121.6, 197.9, 291.45, 112.85, 64.2];
    let expected_fresh_strikes = [11000.0, 12000.0, 12000.0, 12000.0, 12000.0, 11000.0, 11000.0, 12000.0, 12000.0];
    let mut differences = Vec::new();
    if implicit.trades != fixed.trades || implicit.summary != fixed.summary {
        differences.push("YEARLY output changed when per_leg_rollover was omitted".into());
    }
    if fixed.trades.len() != expected_dates.len() {
        differences.push(format!("fixed rows: expected {}, actual {}", expected_dates.len(), fixed.trades.len()));
    }
    if fresh.trades.len() != expected_dates.len() {
        differences.push(format!("fresh rows: expected {}, actual {}", expected_dates.len(), fresh.trades.len()));
    }
    for (index, row) in fixed.trades.iter().enumerate() {
        if row.entry_date != expected_dates[index] {
            differences.push(format!("fixed[{index}] entry date: {}", row.entry_date));
        }
        if row.expiry != "2019-12-26" || row.strike != 11000.0 {
            differences.push(format!("fixed[{index}] contract/strike: {}/{}", row.expiry, row.strike));
        }
        if (row.entry_price - expected_fixed_entries[index]).abs() > 0.01 {
            differences.push(format!("fixed[{index}] entry price: {}", row.entry_price));
        }
        if index + 1 < fixed.trades.len()
            && (row.exit_price - fixed.trades[index + 1].entry_price).abs() > 0.01
        {
            differences.push(format!("fixed[{index}] MTM continuity"));
        }
    }
    for (index, row) in fresh.trades.iter().enumerate() {
        if row.entry_date != expected_dates[index] || row.strike != expected_fresh_strikes[index] {
            differences.push(format!("fresh[{index}] date/strike: {}/{}", row.entry_date, row.strike));
        }
        if row.expiry != "2019-12-26" {
            differences.push(format!("fresh[{index}] contract: {}", row.expiry));
        }
    }
    let report = Report {
        clean: differences.is_empty(),
        fixed_rows: fixed.trades.len(),
        fresh_rows: fresh.trades.len(),
        contract: "2019-12-26".into(),
        fixed_strike: 11000.0,
        implicit_yearly_matches: implicit.trades == fixed.trades && implicit.summary == fixed.summary,
        differences,
    };
    println!("{}", serde_json::to_string_pretty(&report)?);
    if !report.clean {
        std::process::exit(2);
    }
    Ok(())
}
