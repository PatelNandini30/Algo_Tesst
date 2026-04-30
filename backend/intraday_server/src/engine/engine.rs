use crate::engine::data_queries::{load_expiry_map, pick_expiry_idx, strike_step, time_to_idx};
use crate::engine::snapshot::Snapshot;
use crate::engine::types::{ExpiryMode, LegSpec, StrategySpec, TradeRecord};
use chrono::NaiveDate;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

const SESSION_START: u32 = 9 * 60 + 15;

fn idx_to_time(idx: usize) -> String {
    let abs = SESSION_START + idx as u32;
    format!("{:02}:{:02}", abs / 60, abs % 60)
}

fn compute_thresholds(leg: &LegSpec, entry_x100: i32) -> (Option<i32>, Option<i32>) {
    let sl = leg.sl.as_ref().map(|c| {
        let delta = match c.kind.as_str() {
            "percent" => ((entry_x100 as f64) * c.value / 100.0).round() as i32,
            _ => (c.value * 100.0).round() as i32,
        };
        if leg.action == "SELL" { entry_x100 + delta } else { entry_x100 - delta }
    });
    let tgt = leg.target.as_ref().map(|c| {
        let delta = match c.kind.as_str() {
            "percent" => ((entry_x100 as f64) * c.value / 100.0).round() as i32,
            _ => (c.value * 100.0).round() as i32,
        };
        if leg.action == "SELL" { entry_x100 - delta } else { entry_x100 + delta }
    });
    (sl, tgt)
}

fn mae_mfe(snap: &Snapshot, e: usize, s: usize, t: usize, entry_idx: usize, exit_idx: usize, is_sell: bool) -> (f64, f64) {
    let ep = snap.chain_val(e, s, t, 0, entry_idx) as f64;
    let (mut min_px, mut max_px) = (ep, ep);
    for m in (entry_idx + 1)..=exit_idx {
        let lo = snap.chain_val(e, s, t, 2, m) as f64;
        let hi = snap.chain_val(e, s, t, 1, m) as f64;
        if lo < min_px { min_px = lo; }
        if hi > max_px { max_px = hi; }
    }
    if is_sell {
        ((max_px - ep) / 100.0, (ep - min_px) / 100.0)
    } else {
        ((ep - min_px) / 100.0, (max_px - ep) / 100.0)
    }
}

fn run_day(
    snap: &Snapshot,
    expiry_map: &HashMap<i16, NaiveDate>,
    spec: &StrategySpec,
    date_str: &str,
    trade_date: NaiveDate,
) -> Vec<TradeRecord> {
    let step = strike_step(&spec.symbol);
    // time_to_idx now returns Result; skip the day if times are invalid
    let entry_idx = match time_to_idx(&spec.entry_time) {
        Ok(idx) => idx.min(snap.minute_count.saturating_sub(1)),
        Err(_) => return vec![],
    };
    let sqoff_idx = match time_to_idx(&spec.square_off_time) {
        Ok(idx) => idx.min(snap.minute_count.saturating_sub(1)),
        Err(_) => return vec![],
    };

    let mut records = Vec::new();
    for leg in &spec.legs {
        let expiry_mode = match leg.expiry.as_str() {
            "MONTHLY" | "NEXT_MONTHLY" => ExpiryMode::Monthly,
            _ => ExpiryMode::Weekly,
        };
        let expiry_idx = match pick_expiry_idx(trade_date, expiry_mode, expiry_map) {
            Some(idx) => idx,
            None => continue,
        };
        let e = match snap.find_expiry_e(expiry_idx) {
            Some(e) => e,
            None => continue,
        };

        let atm = snap.atm_x100(e, entry_idx);
        let strike_x100 = atm + leg.strike_selection.value * step;
        // Anchor is based on ATM at minute 0: chain layout is fixed at session open
        let anchor = snap.atm_x100(e, 0) - 5 * step;
        let s_raw = (strike_x100 - anchor) / step;
        if s_raw < 0 || s_raw >= 11 { continue; }
        let s = s_raw as usize;
        let t: usize = if leg.opt_type == "CE" { 0 } else { 1 };

        let entry_px = snap.chain_val(e, s, t, 0, entry_idx);
        if entry_px <= 0 { continue; }

        let (sl_thr, tgt_thr) = compute_thresholds(leg, entry_px);
        let is_sell = leg.action == "SELL";

        let mut exit_idx = sqoff_idx;
        let mut exit_reason = "SQOFF";

        for m in (entry_idx + 1)..=sqoff_idx {
            let px = snap.chain_val(e, s, t, 0, m);
            let hit_sl  = sl_thr.map_or(false, |thr| if is_sell { px >= thr } else { px <= thr });
            let hit_tgt = tgt_thr.map_or(false, |thr| if is_sell { px <= thr } else { px >= thr });
            if hit_sl  { exit_idx = m; exit_reason = "SL";     break; }
            if hit_tgt { exit_idx = m; exit_reason = "TARGET"; break; }
        }

        let exit_px = snap.chain_val(e, s, t, 0, exit_idx);
        let (mae, mfe) = mae_mfe(snap, e, s, t, entry_idx, exit_idx, is_sell);
        let pnl = if is_sell {
            (entry_px - exit_px) as f64 / 100.0
        } else {
            (exit_px - entry_px) as f64 / 100.0
        } * leg.quantity as f64;

        let expiry_date_str = expiry_map.get(&expiry_idx)
            .map(|d| d.format("%Y-%m-%d").to_string())
            .unwrap_or_default();

        records.push(TradeRecord {
            date: date_str.to_string(),
            symbol: spec.symbol.clone(),
            expiry: expiry_date_str,
            strike: strike_x100 as f64 / 100.0,
            opt_type: leg.opt_type.clone(),
            action: leg.action.clone(),
            entry_time: idx_to_time(entry_idx),
            entry_price: entry_px as f64 / 100.0,
            exit_time: idx_to_time(exit_idx),
            exit_price: exit_px as f64 / 100.0,
            exit_reason: exit_reason.to_string(),
            quantity: leg.quantity,
            pnl,
            mae,
            mfe,
        });
    }
    records
}

pub fn run_backtest(spec: &StrategySpec, data_dir: &Path) -> Result<Vec<TradeRecord>, crate::error::AppError> {
    let symbol_dir = data_dir.join(&spec.symbol);
    let snaps_dir = symbol_dir.join("snapshots");
    let expiry_map = load_expiry_map(&symbol_dir)
        .map_err(crate::error::AppError::Io)?;

    let date_from = NaiveDate::parse_from_str(&spec.date_from, "%Y-%m-%d")
        .map_err(|e| crate::error::AppError::BadRequest(e.to_string()))?;
    let date_to = NaiveDate::parse_from_str(&spec.date_to, "%Y-%m-%d")
        .map_err(|e| crate::error::AppError::BadRequest(e.to_string()))?;

    let mut all_records = Vec::new();
    let mut current = date_from;
    while current <= date_to {
        let date_str = current.format("%Y-%m-%d").to_string();
        let snap_path = snaps_dir.join(format!("{date_str}.arrow"));
        if snap_path.exists() {
            match Snapshot::open(&snap_path) {
                Ok(snap) => {
                    all_records.extend(run_day(&snap, &expiry_map, spec, &date_str, current));
                }
                Err(e) => tracing::warn!("skip {date_str}: {e}"),
            }
        }
        current = current.succ_opt().unwrap_or(current);
    }
    Ok(all_records)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::snapshot::test_helpers::synthetic_snapshot;
    use crate::engine::types::{ExitCond, LegSpec, StrikeSelection};
    use tempfile::TempDir;

    fn setup_backtest_dir() -> TempDir {
        let dir = TempDir::new().unwrap();
        let sym_dir = dir.path().join("NIFTY");
        std::fs::create_dir_all(sym_dir.join("snapshots")).unwrap();
        std::fs::write(sym_dir.join("expiries.json"), r#"{"0": "2024-01-04"}"#).unwrap();
        let bytes = synthetic_snapshot("2024-01-01", 2400000, 20000, 10000, 5);
        let path = sym_dir.join("snapshots").join("2024-01-01.arrow");
        std::fs::write(path, bytes).unwrap();
        dir
    }

    #[test]
    fn test_sell_atm_ce_hits_target() {
        let dir = setup_backtest_dir();
        let spec = StrategySpec {
            symbol: "NIFTY".into(),
            date_from: "2024-01-01".into(),
            date_to: "2024-01-01".into(),
            entry_time: "09:20".into(),
            square_off_time: "15:15".into(),
            legs: vec![LegSpec {
                opt_type: "CE".into(),
                action: "SELL".into(),
                strike_selection: StrikeSelection { mode: "ATM".into(), value: 0 },
                expiry: "WEEKLY".into(),
                quantity: 1,
                sl: None,
                target: Some(ExitCond { kind: "percent".into(), value: 50.0 }),
            }],
        };
        let records = run_backtest(&spec, dir.path()).unwrap();
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].exit_reason, "TARGET");
        assert!((records[0].entry_price - 200.0).abs() < 0.01);
        assert!((records[0].exit_price - 100.0).abs() < 0.01);
        assert!((records[0].pnl - 100.0).abs() < 0.01);
    }
}
