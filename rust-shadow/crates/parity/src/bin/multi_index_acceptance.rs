use std::path::PathBuf;
use std::sync::Arc;

use algotest_domain::{ComboOverride, StrategyConfig};
use algotest_engine::market_data::CsvMarketDataSet;
use algotest_engine::native::NativeEngine;
use algotest_engine::{StrategyEngine, TradeRow};
use chrono::NaiveDate;
use serde_json::json;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let arguments = std::env::args().collect::<Vec<_>>();
    if arguments.len() < 2 {
        return Err("usage: multi_index_acceptance <repository-root> [memory-mb]".into());
    }
    let root = PathBuf::from(&arguments[1]);
    let memory_mb = arguments
        .get(2)
        .map(|value| value.parse::<usize>())
        .transpose()?
        .unwrap_or(768);
    let mut strategy: StrategyConfig = serde_json::from_value(json!({
        "index":"NIFTY",
        "from_date":"2024-01-01",
        "to_date":"2024-03-31",
        "expiry_type":"WEEKLY",
        "entry_dte":1,
        "exit_dte":0,
        "multi_index_mode":true,
        "legs":[
            {"index":"NIFTY","segment":"OPTIONS","option_type":"CE","position":"SELL","lots":1,"expiry":"WEEKLY","strike_interval":50,"strike_selection":{"type":"strike_type","strike_type":"ATM"}},
            {"index":"MIDCPNIFTY","segment":"FUTURES","option_type":"FUT","position":"BUY","lots":1,"expiry":"MONTHLY","strike_interval":25}
        ]
    }))?;
    let from = NaiveDate::from_ymd_opt(2024, 1, 1).unwrap();
    let to = NaiveDate::from_ymd_opt(2024, 3, 31).unwrap();
    let market = Arc::new(CsvMarketDataSet::load(
        &root,
        &["NIFTY".into(), "MIDCPNIFTY".into()],
        from,
        to,
        memory_mb * 1024 * 1024,
    )?);
    let admitted_rows = market.admitted_rows();
    let estimated_bytes = market.estimated_resident_bytes();
    let engine = NativeEngine::new(market);
    let combo = ComboOverride { combo_id: 1, values: Default::default() };
    let forward = engine.run(&strategy, &combo)?;
    strategy.legs.reverse();
    let reverse = engine.run(&strategy, &combo)?;
    let mut forward_values = forward.trades.iter().map(row_value).collect::<Vec<_>>();
    let mut reverse_values = reverse.trades.iter().map(row_value).collect::<Vec<_>>();
    forward_values.sort_unstable();
    reverse_values.sort_unstable();
    let forward_ids = forward.trades.iter().map(|row| row.trade_id).collect::<Vec<_>>();
    let chronological_ids = forward_ids.windows(2).all(|pair| pair[0] <= pair[1]);
    let clean = forward.summary == reverse.summary
        && forward_values == reverse_values
        && chronological_ids
        && forward.trades.iter().all(|row| row.annotations.contains_key("group_index"));
    println!("{}", serde_json::to_string_pretty(&json!({
        "clean":clean,
        "forward_rows":forward.trades.len(),
        "reverse_rows":reverse.trades.len(),
        "total_pnl":forward.summary.total_pnl,
        "summary_order_invariant":forward.summary == reverse.summary,
        "trade_values_order_invariant":forward_values == reverse_values,
        "chronological_trade_ids":chronological_ids,
        "market_rows":admitted_rows,
        "estimated_market_bytes":estimated_bytes
    }))?);
    if !clean {
        std::process::exit(2);
    }
    Ok(())
}

fn row_value(row: &TradeRow) -> String {
    format!(
        "{}|{}|{}|{}|{}|{}|{:.4}|{:.4}|{:.4}",
        row.annotations.get("group_index").map(String::as_str).unwrap_or(""),
        row.annotations.get("group_expiry").map(String::as_str).unwrap_or(""),
        row.entry_date,
        row.exit_date,
        row.instrument,
        row.position,
        row.strike,
        row.entry_price,
        row.leg_pnl
    )
}
