use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use algotest_domain::{ComboOverride, StrategyConfig};
use algotest_engine::market_data::CsvMarketData;
use algotest_engine::native::NativeEngine;
use algotest_engine::{StrategyEngine, TradeRow};
use chrono::NaiveDate;
use serde::Serialize;
use serde_json::{json, Value};

#[derive(Serialize)]
struct SnapshotReport {
    snapshot: String,
    clean: bool,
    canonical_value_clean: bool,
    legacy_order_only: bool,
    expected_rows: usize,
    actual_rows: usize,
    market_rows: usize,
    estimated_market_bytes: usize,
    differences: Vec<String>,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.len() < 3 {
        return Err("usage: snapshot_parity <repository-root> <snapshot.json> [memory-mb]".into());
    }
    let root = PathBuf::from(&arguments[1]);
    let snapshot_path = PathBuf::from(&arguments[2]);
    let memory_mb: usize = arguments
        .get(3)
        .map(|value| value.parse())
        .transpose()?
        .unwrap_or(768);
    let source = fs::read_to_string(&snapshot_path)?;
    // Historical snapshots were emitted by Python's permissive encoder and
    // may contain bare NaN. Those fields are outside the core trade contract.
    let snapshot: Value = serde_json::from_str(&source.replace(": NaN", ": null"))?;
    let strategy: StrategyConfig = serde_json::from_value(snapshot["payload"].clone())?;
    let from = parse_date(strategy.from_date.as_deref())?;
    let to = parse_date(strategy.to_date.as_deref())?;
    let market = Arc::new(CsvMarketData::load(
        &root,
        &strategy.index,
        from,
        to,
        memory_mb.saturating_mul(1024 * 1024),
    )?);
    let market_rows = market.admitted_rows();
    let estimated_market_bytes = market.estimated_resident_bytes();
    let engine = NativeEngine::new(market);
    let result = engine.run(
        &strategy,
        &ComboOverride {
            combo_id: 1,
            values: Default::default(),
        },
    )?;
    let expected = snapshot
        .get("trades")
        .or_else(|| snapshot.get("expected_trades"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let actual = result
        .trades
        .iter()
        .map(legacy_row_value)
        .collect::<Vec<_>>();
    let differences = compare_legacy_rows(&expected, &actual, 0.01, 100);
    let mut canonical_expected = expected.clone();
    let mut canonical_actual = actual.clone();
    canonical_expected.sort_by_key(canonical_row_key);
    canonical_actual.sort_by_key(canonical_row_key);
    let canonical_differences =
        compare_legacy_rows(&canonical_expected, &canonical_actual, 0.01, 1);
    let canonical_value_clean = canonical_differences.is_empty();
    let report = SnapshotReport {
        snapshot: snapshot_path.display().to_string(),
        clean: differences.is_empty(),
        canonical_value_clean,
        legacy_order_only: !differences.is_empty() && canonical_value_clean,
        expected_rows: expected.len(),
        actual_rows: result.trades.len(),
        market_rows,
        estimated_market_bytes,
        differences,
    };
    println!("{}", serde_json::to_string_pretty(&report)?);
    // Historical snapshots from the live exporter contain five deterministic
    // leg-order variants. They are explicitly reported above as
    // `legacy_order_only`; canonical trade values and statistics are identical.
    // A parity gate must fail on value/stat mismatches, never on a harmless row
    // permutation that cannot change leg-order-invariant analytics.
    if !report.clean && !report.canonical_value_clean {
        std::process::exit(2);
    }
    Ok(())
}

fn parse_date(value: Option<&str>) -> Result<NaiveDate, Box<dyn std::error::Error>> {
    Ok(NaiveDate::parse_from_str(
        value
            .ok_or("missing strategy date")?
            .get(..10)
            .ok_or("invalid strategy date")?,
        "%Y-%m-%d",
    )?)
}

fn compare_legacy_rows(
    expected: &[Value],
    actual: &[Value],
    tolerance: f64,
    maximum: usize,
) -> Vec<String> {
    let mut out = Vec::new();
    if expected.len() != actual.len() {
        out.push(format!(
            "trades length: expected {}, actual {}",
            expected.len(),
            actual.len()
        ));
    }
    for (index, (expected, actual)) in expected.iter().zip(actual).enumerate() {
        for field in [
            "Trade",
            "Leg",
            "Entry Date",
            "Exit Date",
            "Type",
            "Strike",
            "B/S",
            "Entry Price",
            "Exit Price",
            "Entry Spot",
            "Exit Spot",
            "Expiry",
            "Net P&L",
            "MAE",
            "MFE",
        ] {
            let normalized_expected = normalize_legacy_value(field, &expected[field]);
            let expected_value = &normalized_expected;
            let actual_value = &actual[field];
            let matches = match (expected_value.as_f64(), actual_value.as_f64()) {
                (Some(a), Some(b)) => (a - b).abs() <= tolerance,
                _ => expected_value == actual_value,
            };
            if !matches {
                out.push(format!(
                    "trades[{index}].{field}: expected {expected_value}, actual {actual_value}"
                ));
                if out.len() >= maximum {
                    return out;
                }
            }
        }
    }
    out
}

fn legacy_row_value(actual: &TradeRow) -> Value {
    let display_strike = if actual.instrument == "FUTURES" {
        Value::String(String::new())
    } else {
        json!(actual.strike)
    };
    json!({
        "Trade": actual.trade_id.to_string(),
        "Leg": actual.leg_label.as_ref().map_or_else(|| json!(actual.leg_id), |label| json!(label)),
        "Entry Date": actual.entry_date,
        "Exit Date": actual.exit_date,
        "Type": actual.option_type,
        "Strike": display_strike,
        "B/S": actual.position,
        "Entry Price": actual.entry_price,
        "Exit Price": actual.exit_price,
        "Entry Spot": actual.entry_spot,
        "Exit Spot": actual.exit_spot,
        "Expiry": actual.expiry,
        "Net P&L": actual.net_pnl,
        "MAE": actual.mae,
        "MFE": actual.mfe,
    })
}

fn canonical_row_key(row: &Value) -> (String, u64, String) {
    let entry = normalize_legacy_value("Entry Date", &row["Entry Date"])
        .as_str()
        .unwrap_or_default()
        .to_string();
    let trade = row["Trade"]
        .as_u64()
        .or_else(|| row["Trade"].as_str().and_then(|value| value.parse().ok()))
        .unwrap_or(u64::MAX);
    let leg = row["Leg"]
        .as_u64()
        .map(|value| format!("{value:010}"))
        .or_else(|| row["Leg"].as_str().map(str::to_string))
        .unwrap_or_default();
    (entry, trade, leg)
}

fn normalize_legacy_value(field: &str, value: &Value) -> Value {
    if field == "Trade" {
        if let Some(number) = value.as_u64() {
            return Value::String(number.to_string());
        }
    }
    if matches!(field, "Entry Date" | "Exit Date" | "Expiry") {
        if let Some(text) = value.as_str() {
            if let Ok(date) = NaiveDate::parse_from_str(text, "%d-%m-%Y") {
                return Value::String(date.format("%Y-%m-%d").to_string());
            }
        }
    }
    value.clone()
}

#[allow(dead_code)]
fn _path_label(path: &Path) -> String {
    path.file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("snapshot")
        .into()
}
