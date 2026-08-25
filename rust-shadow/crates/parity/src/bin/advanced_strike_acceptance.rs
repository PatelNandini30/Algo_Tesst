use std::path::PathBuf;
use std::sync::Arc;

use algotest_domain::{ComboOverride, StrategyConfig};
use algotest_engine::market_data::CsvMarketData;
use algotest_engine::native::NativeEngine;
use algotest_engine::StrategyEngine;
use chrono::NaiveDate;
use serde_json::{json, Value};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args().collect::<Vec<_>>();
    if args.len() < 2 { return Err("usage: advanced_strike_acceptance <repository-root> [memory-mb]".into()); }
    let root = PathBuf::from(&args[1]);
    let memory = args.get(2).and_then(|value| value.parse::<usize>().ok()).unwrap_or(768);
    let from = NaiveDate::from_ymd_opt(2024, 1, 1).unwrap();
    let to = NaiveDate::from_ymd_opt(2024, 3, 31).unwrap();
    let market = Arc::new(CsvMarketData::load(&root, "NIFTY", from, to, memory * 1024 * 1024)?);
    let engine = NativeEngine::new(market);
    let cases = [
        ("time_value", json!({"type":"time_value","premium":50})),
        ("time_value_gte", json!({"type":"time_value_gte","premium":50})),
        ("time_value_lte", json!({"type":"time_value_lte","premium":50})),
        ("delta", json!({"type":"delta","delta":0.5})),
        ("synthetic_future", json!({"type":"synthetic_future","strike_type":"ATM"})),
    ];
    let mut reports = Vec::new();
    for (name, selection) in cases {
        let strategy: StrategyConfig = serde_json::from_value(json!({
            "index":"NIFTY","from_date":"2024-01-01","to_date":"2024-03-31",
            "expiry_type":"WEEKLY","entry_dte":1,"exit_dte":0,
            "legs":[{"segment":"OPTIONS","option_type":"CE","position":"SELL","expiry":"WEEKLY","strike_interval":50,"strike_selection":selection}]
        }))?;
        match engine.run(&strategy, &ComboOverride { combo_id: 1, values: Default::default() }) {
            Ok(result) => reports.push(json!({"mode":name,"status":"ok","rows":result.trades.len(),"total_pnl":result.summary.total_pnl})),
            Err(error) => reports.push(json!({"mode":name,"status":"error","error":error.to_string()})),
        }
    }
    for (name, second_selection) in [
        ("rel_leg", json!({"type":"rel_leg","ref_leg":1,"offset":2})),
        ("rel_leg_premium", json!({"type":"rel_leg_premium","ref_leg":1})),
    ] {
        let strategy: StrategyConfig = serde_json::from_value(json!({
            "index":"NIFTY","from_date":"2024-01-01","to_date":"2024-03-31",
            "expiry_type":"WEEKLY","entry_dte":1,"exit_dte":0,
            "legs":[
                {"segment":"OPTIONS","option_type":"CE","position":"SELL","expiry":"WEEKLY","strike_interval":50,"strike_selection":{"type":"strike_type","strike_type":"ATM"}},
                {"segment":"OPTIONS","option_type":"PE","position":"BUY","expiry":"WEEKLY","strike_interval":50,"strike_selection":second_selection}
            ]
        }))?;
        match engine.run(&strategy, &ComboOverride { combo_id: 1, values: Default::default() }) {
            Ok(result) => reports.push(json!({"mode":name,"status":"ok","rows":result.trades.len(),"total_pnl":result.summary.total_pnl})),
            Err(error) => reports.push(json!({"mode":name,"status":"error","error":error.to_string()})),
        }
    }
    let failed = reports.iter().filter(|report| report["status"] == Value::String("error".into())).count();
    println!("{}", serde_json::to_string_pretty(&json!({"clean":failed == 0,"failed":failed,"cases":reports}))?);
    if failed > 0 { std::process::exit(2); }
    Ok(())
}
