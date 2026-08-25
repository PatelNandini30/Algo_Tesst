use std::fs;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;

use algotest_domain::StrategyConfig;
use algotest_engine::market_data::CsvMarketData;
use algotest_engine::native::NativeEngine;
use algotest_optimizer::{run_optimization_streaming, BatchLimits, ParameterSpec};
use chrono::NaiveDate;
use serde::Serialize;
use serde_json::{json, Value};

#[derive(Serialize)]
struct BenchmarkReport {
    planned: u64,
    processed: u64,
    succeeded: u64,
    failed: u64,
    load_seconds: f64,
    preflight_seconds: f64,
    compute_seconds: f64,
    total_seconds: f64,
    combinations_per_second: f64,
    estimated_market_bytes: usize,
    peak_rss_kb: Option<u64>,
    retained_results: usize,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.len() < 3 {
        return Err(
            "usage: benchmark_30k <repository-root> <base-snapshot.json> [memory-mb]".into(),
        );
    }
    let root = PathBuf::from(&arguments[1]);
    let snapshot_path = PathBuf::from(&arguments[2]);
    let memory_mb: usize = arguments
        .get(3)
        .map(|value| value.parse())
        .transpose()?
        .unwrap_or(768);
    let source = fs::read_to_string(snapshot_path)?;
    let snapshot: Value = serde_json::from_str(&source.replace(": NaN", ": null"))?;
    let mut payload = snapshot["payload"].clone();
    payload["legs"][0]["stopLoss"] = json!({"mode":"PERCENT","value":20});
    payload["legs"][0]["targetProfit"] = json!({"mode":"PERCENT","value":20});
    let strategy: StrategyConfig = serde_json::from_value(payload)?;
    let from = parse_date(strategy.from_date.as_deref())?;
    let to = parse_date(strategy.to_date.as_deref())?;

    let started = Instant::now();
    let market = Arc::new(CsvMarketData::load(
        &root,
        &strategy.index,
        from,
        to,
        memory_mb.saturating_mul(1024 * 1024),
    )?);
    let estimated_market_bytes = market.estimated_resident_bytes();
    let loaded_at = Instant::now();
    let engine = Arc::new(NativeEngine::new(market));
    let specs = benchmark_grid();
    let limits = BatchLimits {
        max_combinations: 30_000,
        chunk_size: 256,
        memory_budget_bytes: 256 * 1024 * 1024,
        estimated_bytes_per_combo: 32 * 1024,
    };
    let preflight_started = Instant::now();
    let preflight_processed = run_optimization_streaming(
        engine.clone(),
        &strategy,
        &specs,
        "total_pnl",
        limits,
        |results| {
            if let Some(failed) = results.iter().find(|result| !result.is_success()) {
                return Err(algotest_optimizer::OptimizerError::InvalidParameter(
                    format!("preflight combo {} failed", failed.combo_id),
                ));
            }
            Ok(())
        },
    )?;
    if preflight_processed != 30_000 {
        return Err("strict preflight did not cover every combination".into());
    }
    let preflight_finished = Instant::now();
    let mut succeeded = 0u64;
    let mut failed = 0u64;
    let processed =
        run_optimization_streaming(engine, &strategy, &specs, "total_pnl", limits, |results| {
            for result in results {
                if result.is_success() {
                    succeeded += 1;
                } else {
                    failed += 1;
                }
            }
            Ok(())
        })?;
    let finished = Instant::now();
    let preflight_seconds = (preflight_finished - preflight_started).as_secs_f64();
    let compute_seconds = (finished - preflight_finished).as_secs_f64();
    let report = BenchmarkReport {
        planned: 30_000,
        processed,
        succeeded,
        failed,
        load_seconds: (loaded_at - started).as_secs_f64(),
        preflight_seconds,
        compute_seconds,
        total_seconds: (finished - started).as_secs_f64(),
        combinations_per_second: processed as f64 / compute_seconds.max(f64::EPSILON),
        estimated_market_bytes,
        peak_rss_kb: peak_rss_kb(),
        retained_results: 0,
    };
    println!("{}", serde_json::to_string_pretty(&report)?);
    if processed != 30_000 || failed != 0 {
        std::process::exit(2);
    }
    Ok(())
}

fn benchmark_grid() -> Vec<ParameterSpec> {
    vec![
        ParameterSpec::Values {
            path: "entry_dte".into(),
            values: (1..=10).map(Value::from).collect(),
        },
        ParameterSpec::Enum {
            path: "legs[0].strike_selection.strike_type".into(),
            values: ["ATM", "OTM1", "OTM2", "ITM1", "ITM2"]
                .into_iter()
                .map(Value::from)
                .collect(),
        },
        ParameterSpec::Values {
            path: "legs[0].stopLoss.value".into(),
            values: (10..=105).step_by(5).map(Value::from).collect(),
        },
        ParameterSpec::Values {
            path: "legs[0].targetProfit.value".into(),
            values: (10..=55).step_by(5).map(Value::from).collect(),
        },
        ParameterSpec::Values {
            path: "slippage_pct".into(),
            values: vec![json!(0.0), json!(0.05), json!(0.1)],
        },
    ]
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

fn peak_rss_kb() -> Option<u64> {
    fs::read_to_string("/proc/self/status")
        .ok()?
        .lines()
        .find(|line| line.starts_with("VmHWM:"))?
        .split_whitespace()
        .nth(1)?
        .parse()
        .ok()
}
