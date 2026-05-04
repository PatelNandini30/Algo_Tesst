use crate::intraday::snapshot::Snapshot;
use crate::intraday::types::{LegSpec, StrategySpec, TradeRecord};
use std::collections::HashMap;

const SESSION_START: u32 = 9 * 60 + 15; // 09:15 in minutes-since-midnight

/// Convert "HH:MM" to 0-based minute index within session (0 = 09:15)
fn time_to_idx(hhmm: &str) -> usize {
    let parts: Vec<u32> = hhmm.splitn(2, ':').map(|s| s.parse().unwrap_or(0)).collect();
    let abs_min = parts[0] * 60 + parts[1];
    (abs_min.saturating_sub(SESSION_START)) as usize
}

fn idx_to_time(idx: usize) -> String {
    let abs = SESSION_START + idx as u32;
    format!("{:02}:{:02}", abs / 60, abs % 60)
}

/// strike step in strike_x100 units per symbol
fn strike_step(symbol: &str) -> i32 {
    match symbol {
        "BANKNIFTY" => 10000,
        "MIDCPNIFTY" => 2500,
        _ => 5000, // NIFTY, FINNIFTY
    }
}

/// Pick expiry_e index in snapshot for a given expiry type.
/// WEEKLY → expiry_e=0 (nearest), MONTHLY → expiry_e=1 (or last of month).
/// Simple heuristic for now; full calendar logic added in Plan E.
fn pick_expiry_e(expiry_str: &str) -> usize {
    match expiry_str {
        "WEEKLY" | "NEXT_WEEKLY" => 0,
        "MONTHLY" | "NEXT_MONTHLY" => 1,
        _ => 0,
    }
}

fn compute_thresholds(leg: &LegSpec, entry_x100: i32) -> (Option<i32>, Option<i32>) {
    let sl_x100 = leg.sl.as_ref().map(|c| {
        let delta = match c.kind.as_str() {
            "percent" => ((entry_x100 as f64) * c.value / 100.0).round() as i32,
            _ => (c.value * 100.0).round() as i32,
        };
        if leg.action == "SELL" { entry_x100 + delta } else { entry_x100 - delta }
    });
    let tgt_x100 = leg.target.as_ref().map(|c| {
        let delta = match c.kind.as_str() {
            "percent" => ((entry_x100 as f64) * c.value / 100.0).round() as i32,
            _ => (c.value * 100.0).round() as i32,
        };
        if leg.action == "SELL" { entry_x100 - delta } else { entry_x100 + delta }
    });
    (sl_x100, tgt_x100)
}

/// Scan one leg's price path and return (exit_offset, reason).
/// Handles: fixed SL, target, trailing SL, breakeven SL.
/// `prices` is the close-price slice from entry+1 to sqoff (inclusive).
/// `sqoff_offset` is the index within `prices` that corresponds to square-off.
pub fn scan_exit_stateful(
    leg: &LegSpec,
    entry_x100: i32,
    prices: &[i32],
    sqoff_offset: usize,
) -> (usize, &'static str) {
    let is_sell = leg.action == "SELL";

    let mut sl_thr = leg.sl.as_ref().map(|c| {
        let delta = match c.kind.as_str() {
            "percent" => ((entry_x100 as f64) * c.value / 100.0).round() as i32,
            _ => (c.value * 100.0).round() as i32,
        };
        if is_sell { entry_x100 + delta } else { entry_x100 - delta }
    });

    let tgt_thr = leg.target.as_ref().map(|c| {
        let delta = match c.kind.as_str() {
            "percent" => ((entry_x100 as f64) * c.value / 100.0).round() as i32,
            _ => (c.value * 100.0).round() as i32,
        };
        if is_sell { entry_x100 - delta } else { entry_x100 + delta }
    });

    let mut best_seen = entry_x100;
    let mut trailing_active = false;

    for (offset, &px) in prices.iter().enumerate().take(sqoff_offset + 1) {
        if is_sell { if px < best_seen { best_seen = px; } }
        else       { if px > best_seen { best_seen = px; } }

        if let Some(ref ts) = leg.trailing_sl {
            if !trailing_active {
                let profit_pct = if is_sell {
                    (entry_x100 - px) as f64 / entry_x100 as f64 * 100.0
                } else {
                    (px - entry_x100) as f64 / entry_x100 as f64 * 100.0
                };
                if profit_pct >= ts.trigger_pct { trailing_active = true; }
            }
            if trailing_active {
                let new_sl = if is_sell {
                    ((best_seen as f64) * (1.0 + ts.trail_pct / 100.0)).round() as i32
                } else {
                    ((best_seen as f64) * (1.0 - ts.trail_pct / 100.0)).round() as i32
                };
                if let Some(ref mut sl) = sl_thr {
                    if is_sell && new_sl < *sl { *sl = new_sl; }
                    if !is_sell && new_sl > *sl { *sl = new_sl; }
                } else {
                    sl_thr = Some(new_sl);
                }
            }
        }

        if let Some(ref be) = leg.breakeven {
            let profit_pct = if is_sell {
                (entry_x100 - px) as f64 / entry_x100 as f64 * 100.0
            } else {
                (px - entry_x100) as f64 / entry_x100 as f64 * 100.0
            };
            if profit_pct >= be.trigger_pct {
                if let Some(ref mut sl) = sl_thr {
                    if is_sell && entry_x100 < *sl { *sl = entry_x100; }
                    if !is_sell && entry_x100 > *sl { *sl = entry_x100; }
                }
            }
        }

        if let Some(sl) = sl_thr {
            if (is_sell && px >= sl) || (!is_sell && px <= sl) {
                let reason = if trailing_active { "TRAIL_SL" } else { "SL" };
                return (offset, reason);
            }
        }

        if let Some(tgt) = tgt_thr {
            if (is_sell && px <= tgt) || (!is_sell && px >= tgt) {
                return (offset, "TARGET");
            }
        }
    }

    (sqoff_offset, "SQOFF")
}

fn mae_mfe(snap: &Snapshot, e: usize, s: usize, t: usize, entry_idx: usize, exit_idx: usize, is_sell: bool) -> (f64, f64) {
    let entry_px = snap.chain_val(e, s, t, 0, entry_idx) as f64;
    let (mut min_px, mut max_px) = (entry_px, entry_px);
    for m in (entry_idx + 1)..=exit_idx {
        let lo = snap.chain_val(e, s, t, 2, m) as f64;
        let hi = snap.chain_val(e, s, t, 1, m) as f64;
        if lo < min_px { min_px = lo; }
        if hi > max_px { max_px = hi; }
    }
    if is_sell {
        ((max_px - entry_px) / 100.0, (entry_px - min_px) / 100.0)
    } else {
        ((entry_px - min_px) / 100.0, (max_px - entry_px) / 100.0)
    }
}

/// Run all legs for a single trading day. Returns one TradeRecord per leg.
pub fn run_day(
    snap: &Snapshot,
    expiry_map: &HashMap<i16, String>,
    spec: &StrategySpec,
    date_str: &str,
) -> Vec<TradeRecord> {
    let step = strike_step(&spec.symbol);
    let entry_idx = time_to_idx(&spec.entry_time).min(snap.minute_count - 1);
    let sqoff_idx = time_to_idx(&spec.square_off_time).min(snap.minute_count - 1);

    let mut records = Vec::new();
    for leg in &spec.legs {
        let e = pick_expiry_e(&leg.expiry);
        if e >= snap.expiry_count { continue; }

        // ATM at entry minute
        let atm = snap.atm_x100(e, entry_idx);
        let strike = atm + leg.strike_selection.value * step;

        // Find chain offset s for this strike
        let anchor = snap.atm_x100(e, 0); // day-open ATM as chain anchor
        let s_raw = (strike - (anchor - 5 * step)) / step;
        if s_raw < 0 || s_raw >= 11 { continue; } // outside ATM±5 chain
        let s = s_raw as usize;
        let t: usize = if leg.opt_type == "CE" { 0 } else { 1 };

        let entry_px = snap.chain_val(e, s, t, 0, entry_idx);
        if entry_px <= 0 { continue; }

        let is_sell = leg.action == "SELL";

        let prices: Vec<i32> = ((entry_idx + 1)..=sqoff_idx)
            .map(|m| snap.chain_val(e, s, t, 0, m))
            .collect();
        let sqoff_offset = prices.len().saturating_sub(1);
        let (exit_offset, exit_reason) = scan_exit_stateful(leg, entry_px, &prices, sqoff_offset);
        let exit_idx = entry_idx + 1 + exit_offset;

        let exit_px = snap.chain_val(e, s, t, 0, exit_idx);
        let (mae, mfe) = mae_mfe(snap, e, s, t, entry_idx, exit_idx, is_sell);

        let raw_pnl = if is_sell {
            (entry_px - exit_px) as f64 / 100.0
        } else {
            (exit_px - entry_px) as f64 / 100.0
        };
        let pnl = raw_pnl * leg.quantity as f64;

        let expiry_str = expiry_map
            .get(&snap.expiry_idx(e))
            .cloned()
            .unwrap_or_else(|| "?".to_string());

        records.push(TradeRecord {
            date: date_str.to_string(),
            symbol: spec.symbol.clone(),
            expiry: expiry_str,
            strike: strike as f64 / 100.0,
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_time_to_idx() {
        assert_eq!(time_to_idx("09:15"), 0);
        assert_eq!(time_to_idx("09:20"), 5);
        assert_eq!(time_to_idx("15:15"), 360);
        assert_eq!(time_to_idx("15:29"), 374);
    }

    #[test]
    fn test_idx_to_time() {
        assert_eq!(idx_to_time(0), "09:15");
        assert_eq!(idx_to_time(5), "09:20");
        assert_eq!(idx_to_time(360), "15:15");
    }

    #[test]
    fn test_strike_step() {
        assert_eq!(strike_step("NIFTY"), 5000);
        assert_eq!(strike_step("BANKNIFTY"), 10000);
        assert_eq!(strike_step("MIDCPNIFTY"), 2500);
    }

    #[test]
    fn test_trailing_sl_activates_and_trails() {
        use crate::intraday::types::{ExitCond, LegSpec, StrikeSelection, TrailingSlSpec};
        let leg = LegSpec {
            opt_type: "CE".into(),
            action: "SELL".into(),
            strike_selection: StrikeSelection { mode: "ATM".into(), value: 0 },
            expiry: "WEEKLY".into(),
            quantity: 1,
            sl: Some(ExitCond { kind: "percent".into(), value: 100.0 }),
            target: None,
            trailing_sl: Some(TrailingSlSpec { trigger_pct: 30.0, trail_pct: 30.0 }),
            breakeven: None,
        };
        // entry=10000. trigger fires when price <= 10000*(1-0.30)=7000 (idx 3)
        // min seen at trigger = 7000 → trail SL = 7000*1.30 = 9100
        // at idx 4 price hits 6000 → new min → trail SL = 6000*1.30 = 7800
        // at idx 8 price = 7800 → trail SL hit
        let prices: Vec<i32> = vec![10000, 9000, 8000, 7000, 6000, 6500, 7000, 7500, 7800, 8500];
        let (exit_idx, reason) = scan_exit_stateful(&leg, 10000, &prices, 9);
        assert_eq!(exit_idx, 8);
        assert_eq!(reason, "TRAIL_SL");
    }

    #[test]
    fn test_compute_thresholds_sell_percent() {
        use crate::intraday::types::{ExitCond, LegSpec, StrikeSelection};
        let leg = LegSpec {
            opt_type: "CE".into(),
            action: "SELL".into(),
            strike_selection: StrikeSelection { mode: "ATM".into(), value: 0 },
            expiry: "WEEKLY".into(),
            quantity: 1,
            sl: Some(ExitCond { kind: "percent".into(), value: 50.0 }),
            target: Some(ExitCond { kind: "percent".into(), value: 50.0 }),
            trailing_sl: None,
            breakeven: None,
        };
        let (sl, tgt) = compute_thresholds(&leg, 10000); // entry = 100.00
        assert_eq!(sl, Some(15000));   // 100 + 50% = 150
        assert_eq!(tgt, Some(5000));   // 100 - 50% = 50
    }
}
