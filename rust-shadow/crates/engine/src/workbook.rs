//! Trade Sheet workbook column port — an exact Rust mirror of the live
//! `backend/services/optimizer/excel_builder.py` (`_build_key_order`,
//! `_aggregate_trades`, `_calc_trade_mae`, `_build_cleaned_rows`).
//!
//! Produces the full dynamic column set (Index, Spot P&L/%, Type, B/S, Qty,
//! CE/PE/FUT P&L/%, Net MAE 1/2/Final, Net P&L, % P&L, Cumulative, Peak, DD,
//! %DD, Lowest NAV, Actual Live DD, Exit Reason …) plus the chronological
//! cleaned rows the sheet writer consumes. Column presence is data-driven
//! (has_calls/puts/futures/reentry/filter/spot_adj) — never a fixed layout —
//! so it stays correct across every leg/index/filter combination.
//!
//! Scope: single-index / non-midcap / non-straddle configs, which is what the
//! shadow engine currently emits. Midcap-combined, straddle-width, buffer and
//! STR columns are gated off until those engine paths exist in the shadow.

use std::collections::{BTreeMap, BTreeSet};

use chrono::NaiveDate;
use serde_json::{Map, Value};

use crate::{py_round, TradeRow};

/// Columns written only on a trade's FIRST chronological row (blank on the
/// other leg rows), mirroring `excel_builder._TRADE_COLS`.
const TRADE_COLS: &[&str] = &[
    "Net MAE 1",
    "Net MAE 2",
    "Final MAE",
    "Net P&L",
    "% P&L",
    "Cumulative",
    "Peak",
    "DD",
    "%DD",
    "Lowest NAV",
    "Actual Live DD",
];

fn round4(v: f64) -> f64 {
    (v * 10_000.0).round() / 10_000.0
}

fn is_reentry(row: &TradeRow) -> bool {
    ["reentry_index", "reentry_trigger", "reentry_mode"]
        .iter()
        .any(|k| row.annotations.get(*k).is_some_and(|v| !v.trim().is_empty()))
}

fn is_lazy(row: &TradeRow) -> bool {
    row.annotations
        .get("lazy_leg_name")
        .is_some_and(|v| !v.trim().is_empty())
}

fn is_bullish(option_type: &str, position: &str) -> bool {
    let t = option_type.to_uppercase();
    let bs = position.to_uppercase();
    (matches!(t.as_str(), "CE" | "CALL") && bs == "BUY")
        || (matches!(t.as_str(), "PE" | "PUT") && bs == "SELL")
        || (t == "FUT" && bs == "BUY")
}

fn is_bearish(option_type: &str, position: &str) -> bool {
    let t = option_type.to_uppercase();
    let bs = position.to_uppercase();
    (matches!(t.as_str(), "CE" | "CALL") && bs == "SELL")
        || (matches!(t.as_str(), "PE" | "PUT") && bs == "BUY")
        || (t == "FUT" && bs == "SELL")
}

fn is_directional(option_type: &str) -> bool {
    matches!(option_type.to_uppercase().as_str(), "CE" | "CALL" | "PE" | "PUT" | "FUT")
}

/// The trade's anchor leg — latest entry date, ties to lowest leg id — among
/// non-reentry, non-lazy legs; falls back to the first leg. Mirrors
/// `excel_builder._main_leg`.
fn main_leg<'a>(legs: &[&'a TradeRow]) -> &'a TradeRow {
    legs.iter()
        .filter(|l| !is_reentry(l) && !is_lazy(l))
        .max_by(|a, b| {
            a.entry_date
                .cmp(&b.entry_date)
                .then_with(|| b.leg_id.cmp(&a.leg_id))
        })
        .or_else(|| legs.first())
        .copied()
        .expect("trade group is never empty")
}

/// `_calc_trade_mae`: Net MAE 1/2/Final from per-leg MAE/MFE by market
/// direction. Returns None when any directional leg lacks MAE/MFE.
fn calc_trade_mae(legs: &[&TradeRow], net_pct: Option<f64>) -> Option<(f64, f64, f64)> {
    let dir: Vec<&&TradeRow> = legs
        .iter()
        .filter(|l| is_directional(&l.option_type))
        .collect();
    if dir.is_empty() {
        return None;
    }
    let mut bull_mae = 0.0;
    let mut bull_mfe = 0.0;
    let mut bear_mae = 0.0;
    let mut bear_mfe = 0.0;
    for l in &dir {
        let (mae, mfe) = (l.mae?, l.mfe?);
        if is_bullish(&l.option_type, &l.position) {
            bull_mae += mae;
            bull_mfe += mfe;
        } else if is_bearish(&l.option_type, &l.position) {
            bear_mae += mae;
            bear_mfe += mfe;
        }
    }
    let nm1 = bull_mae + bear_mfe;
    let nm2 = bull_mfe + bear_mae;
    let final_mae = if dir.len() > 1 {
        match net_pct {
            Some(p) => nm1.min(nm2).min(p),
            None => nm1.min(nm2),
        }
    } else {
        nm1.min(nm2)
    };
    Some((round4(nm1), round4(nm2), round4(final_mae)))
}

/// Per-trade aggregates keyed by trade_id, in canonical chronological order.
#[derive(Debug, Clone, Default)]
struct Tm {
    net: f64,
    pct: f64,
    net_mae1: Option<f64>,
    net_mae2: Option<f64>,
    final_mae: Option<f64>,
    cumulative: f64,
    peak: f64,
    dd: f64,
    pct_dd: f64,
    lowest_nav: Option<f64>,
    actual_ldd: Option<f64>,
    exit_reason: String,
}

fn ends_a_patch(reason: &str) -> bool {
    reason
        .to_uppercase()
        .split('+')
        .any(|p| p == "FILTER_END")
}

/// Port of `_aggregate_trades` (single-index / non-midcap path). Returns the
/// per-trade metric map and the canonical chronological trade order.
fn aggregate_trades(
    grouped: &BTreeMap<u64, Vec<&TradeRow>>,
    order: &[u64],
    patchwise: bool,
) -> BTreeMap<u64, Tm> {
    let mut tm: BTreeMap<u64, Tm> = BTreeMap::new();
    for (&tid, legs) in grouped {
        let main = main_leg(legs);
        let spot = main.entry_spot;
        let raw_net: f64 = legs.iter().map(|l| l.leg_pnl).sum();
        let pct = if spot != 0.0 { raw_net / spot * 100.0 } else { 0.0 };
        let mae = calc_trade_mae(legs, Some(pct));
        let exit_reason = legs
            .iter()
            .map(|l| l.exit_reason.trim())
            .filter(|r| !r.is_empty())
            .collect::<Vec<_>>()
            .join("+");
        tm.insert(
            tid,
            Tm {
                net: raw_net,
                pct,
                net_mae1: mae.map(|m| m.0),
                net_mae2: mae.map(|m| m.1),
                final_mae: mae.map(|m| m.2),
                exit_reason,
                ..Default::default()
            },
        );
    }

    // Booked equity curve: Cumulative/Peak/DD/%DD compounding % P&L.
    let mut cumulative = 100.0f64;
    let mut peak = 100.0f64;
    let mut prev: Option<u64> = None;
    for &k in order {
        if patchwise {
            if let Some(p) = prev {
                if ends_a_patch(&tm[&p].exit_reason) {
                    cumulative = 100.0;
                    peak = 100.0;
                }
            }
        }
        prev = Some(k);
        let pct = tm[&k].pct;
        cumulative *= 1.0 + pct / 100.0;
        peak = peak.max(cumulative);
        let dd = if peak > cumulative { cumulative - peak } else { 0.0 };
        let entry = tm.get_mut(&k).unwrap();
        entry.cumulative = cumulative;
        entry.peak = peak;
        entry.dd = dd;
        entry.pct_dd = if peak != 0.0 { dd / peak } else { 0.0 };
    }

    // Lowest NAV / Actual Live DD: anchored to the PREVIOUS cumulative pushed
    // down by Final MAE, divided by the PREVIOUS trade's peak.
    let mut prev_cum = 100.0;
    let mut prev_peak = 100.0;
    prev = None;
    for &k in order {
        if patchwise {
            if let Some(p) = prev {
                if ends_a_patch(&tm[&p].exit_reason) {
                    prev_cum = 100.0;
                    prev_peak = 100.0;
                }
            }
        }
        prev = Some(k);
        let (mae, peak_v, cum_v) = {
            let t = &tm[&k];
            (t.final_mae, t.peak, t.cumulative)
        };
        if let Some(mae) = mae {
            if prev_peak != 0.0 {
                let lowest_nav = py_round(prev_cum * (1.0 + mae / 100.0), 2);
                let actual_ldd = py_round((lowest_nav / prev_peak - 1.0) * 100.0, 2);
                let t = tm.get_mut(&k).unwrap();
                t.lowest_nav = Some(lowest_nav);
                t.actual_ldd = Some(actual_ldd);
            }
        }
        prev_cum = cum_v;
        prev_peak = peak_v;
    }
    tm
}

fn has_type(rows: &[TradeRow], want: &str) -> bool {
    rows.iter()
        .any(|r| r.option_type.eq_ignore_ascii_case(want))
}

/// `_build_key_order` (single-index path): the dynamic column sequence.
fn build_key_order(rows: &[TradeRow], has_filter: bool, has_spot_adj: bool) -> Vec<String> {
    let has_calls = has_type(rows, "CE");
    let has_puts = has_type(rows, "PE");
    let has_futures = rows.iter().any(|r| r.instrument == "FUTURES");
    let has_reentry = rows.iter().any(is_reentry);

    let mut order: Vec<String> = [
        "Trade", "Leg", "Index", "Entry Date", "Exit Date", "Expiry", "Entry Spot", "Exit Spot",
        "Spot P&L", "Spot P&L %", "Type", "Strike",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    order.push("B/S".into());
    if has_reentry {
        order.push("Re-Entry Type".into());
    }
    order.push("Qty".into());
    if has_spot_adj {
        order.push("Raw Entry Price".into());
    }
    order.push("Entry Price".into());
    if has_spot_adj {
        order.push("Raw Exit Price".into());
    }
    order.push("Exit Price".into());
    order.push("MAE".into());
    order.push("MFE".into());
    order.push("Net MAE 1".into());
    order.push("Net MAE 2".into());
    order.push("Final MAE".into());
    if has_calls {
        order.push("CE P&L".into());
        order.push("CE P&L %".into());
    }
    if has_puts {
        order.push("PE P&L".into());
        order.push("PE P&L %".into());
    }
    if has_futures {
        order.push("FUT P&L".into());
        order.push("FUT P&L %".into());
    }
    for c in [
        "Net P&L", "% P&L", "Cumulative", "Peak", "DD", "%DD", "Lowest NAV", "Actual Live DD",
    ] {
        order.push(c.into());
    }
    order.push("Exit Reason".into());
    if has_filter {
        order.push("Filter Segment".into());
    }
    order
}

fn num(v: f64) -> Value {
    Value::Number(serde_json::Number::from_f64(v).unwrap_or_else(|| 0.into()))
}

/// Columns rendered as a true percentage (stored as a fraction; Excel ×100).
pub const TRUE_PCT_COLS: &[&str] = &["Spot P&L %", "CE P&L %", "PE P&L %", "FUT P&L %", "%DD"];

/// Build the full Trade Sheet: `(cleaned_rows_in_chronological_order, key_order)`.
/// Each cleaned row is a column→value map; trade-level columns appear on the
/// trade's first chronological row only, matching the live builder.
pub fn workbook_tradesheet(
    rows: &[TradeRow],
    has_filter: bool,
    has_spot_adj: bool,
    patchwise: bool,
) -> (Vec<Map<String, Value>>, Vec<String>) {
    let key_order = build_key_order(rows, has_filter, has_spot_adj);
    let trade_cols: BTreeSet<&str> = TRADE_COLS.iter().copied().collect();

    let mut grouped: BTreeMap<u64, Vec<&TradeRow>> = BTreeMap::new();
    for r in rows {
        grouped.entry(r.trade_id).or_default().push(r);
    }
    // Lowest present leg per trade — the one row Spot P&L is written on.
    let lowest_leg: BTreeMap<u64, u32> = grouped
        .iter()
        .map(|(&t, legs)| (t, legs.iter().map(|l| l.leg_id).min().unwrap_or(1)))
        .collect();

    // Chronological order: (entry_date, trade_id, leg_id).
    let mut sorted: Vec<&TradeRow> = rows.iter().collect();
    sorted.sort_by(|a, b| {
        a.entry_date
            .cmp(&b.entry_date)
            .then(a.trade_id.cmp(&b.trade_id))
            .then(a.leg_id.cmp(&b.leg_id))
    });

    // trade_id → sequential display number (1-based, first appearance).
    let mut tid_to_no: BTreeMap<u64, i64> = BTreeMap::new();
    let mut trade_order: Vec<u64> = Vec::new();
    for r in &sorted {
        if !tid_to_no.contains_key(&r.trade_id) {
            tid_to_no.insert(r.trade_id, (tid_to_no.len() + 1) as i64);
            trade_order.push(r.trade_id);
        }
    }

    let tm = aggregate_trades(&grouped, &trade_order, patchwise);

    let mut written: BTreeSet<u64> = BTreeSet::new();
    let mut cleaned = Vec::with_capacity(sorted.len());
    for r in &sorted {
        let first = written.insert(r.trade_id);
        let seq = *tid_to_no.get(&r.trade_id).unwrap_or(&1);
        let t = &tm[&r.trade_id];
        let is_fut = r.instrument == "FUTURES";
        let entry_spot = r.entry_spot;
        // Per-leg P&L split routed by option type (leg_pnl already ×lots).
        let ce = if r.option_type.eq_ignore_ascii_case("CE") { r.leg_pnl } else { 0.0 };
        let pe = if r.option_type.eq_ignore_ascii_case("PE") { r.leg_pnl } else { 0.0 };
        let fut = if is_fut { r.leg_pnl } else { 0.0 };
        // Spot P&L is a trade-level value: only on the lowest present leg.
        let on_lowest = lowest_leg.get(&r.trade_id) == Some(&r.leg_id);
        let spot_pnl = if on_lowest {
            Some(py_round(r.exit_spot - entry_spot, 2))
        } else {
            None
        };
        // Qty: recover lots from leg P&L / points (ponytail: live multiplies by
        // the index lot_size for a true contract count; the shadow's own P&L
        // scaling is lots-only, so this reports lots).
        let points = if r.position.eq_ignore_ascii_case("SELL") {
            r.entry_price - r.exit_price
        } else {
            r.exit_price - r.entry_price
        };
        let qty = if points.abs() > 1e-9 {
            (r.leg_pnl / points).round()
        } else {
            1.0
        };

        let mut row = Map::new();
        for key in &key_order {
            let val: Value = match key.as_str() {
                _ if trade_cols.contains(key.as_str()) => {
                    if !first {
                        Value::String(String::new())
                    } else {
                        match key.as_str() {
                            "Net MAE 1" => t.net_mae1.map(num).unwrap_or(Value::String(String::new())),
                            "Net MAE 2" => t.net_mae2.map(num).unwrap_or(Value::String(String::new())),
                            "Final MAE" => t.final_mae.map(num).unwrap_or(Value::String(String::new())),
                            "Net P&L" => num(t.net),
                            "% P&L" => num(t.pct),
                            "Cumulative" => num(t.cumulative),
                            "Peak" => num(t.peak),
                            "DD" => num(t.dd),
                            "%DD" => num(t.pct_dd),
                            "Lowest NAV" => t.lowest_nav.map(num).unwrap_or(Value::String(String::new())),
                            "Actual Live DD" => t.actual_ldd.map(num).unwrap_or(Value::String(String::new())),
                            _ => Value::String(String::new()),
                        }
                    }
                }
                "Trade" | "Index" => Value::Number(seq.into()),
                "Leg" => r
                    .leg_label
                    .clone()
                    .map(Value::String)
                    .unwrap_or_else(|| Value::Number(r.leg_id.into())),
                "Entry Date" => Value::String(r.entry_date.clone()),
                "Exit Date" => Value::String(r.exit_date.clone()),
                "Expiry" => Value::String(r.expiry.clone()),
                "Entry Spot" => num(entry_spot),
                "Exit Spot" => num(r.exit_spot),
                "Spot P&L" => spot_pnl.map(num).unwrap_or(Value::String(String::new())),
                "Spot P&L %" => match spot_pnl {
                    Some(s) if entry_spot != 0.0 => num(s / entry_spot),
                    _ => Value::String(String::new()),
                },
                "Type" => Value::String(r.option_type.clone()),
                "Strike" => {
                    if is_fut {
                        Value::String(String::new())
                    } else {
                        num(r.strike)
                    }
                }
                "B/S" => Value::String(r.position.clone()),
                "Re-Entry Type" => Value::String(
                    r.annotations
                        .get("reentry_mode")
                        .or_else(|| r.annotations.get("reentry_trigger"))
                        .cloned()
                        .unwrap_or_default(),
                ),
                "Qty" => num(qty),
                "Raw Entry Price" => num(r.entry_price),
                "Raw Exit Price" => num(r.exit_price),
                "Entry Price" => num(r.entry_price),
                "Exit Price" => num(r.exit_price),
                "MAE" => r.mae.map(num).unwrap_or(Value::String(String::new())),
                "MFE" => r.mfe.map(num).unwrap_or(Value::String(String::new())),
                "CE P&L" => num(ce),
                "PE P&L" => num(pe),
                "FUT P&L" => num(fut),
                "CE P&L %" if entry_spot != 0.0 => num(ce / entry_spot),
                "PE P&L %" if entry_spot != 0.0 => num(pe / entry_spot),
                "FUT P&L %" if entry_spot != 0.0 => num(fut / entry_spot),
                "Exit Reason" => Value::String(r.exit_reason.clone()),
                "Filter Segment" => Value::String(
                    r.annotations.get("filter_segment").cloned().unwrap_or_default(),
                ),
                _ => Value::String(String::new()),
            };
            row.insert(key.clone(), val);
        }
        cleaned.push(row);
    }
    (cleaned, key_order)
}

// ── Summary formatted report (port of excel_builder._summary_layout) ──────────

/// Cell style class — the api crate maps these to concrete rust_xlsxwriter
/// Formats, keeping the engine crate free of the xlsx dependency.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CellStyle {
    Title,
    Subtitle,
    Section,
    Label,
    ValuePos,
    ValueNeg,
    ValueNeutral,
    Header,
    Plain,
}

/// One rendered Summary cell. `number`/`num_fmt` win over `text` when present.
#[derive(Debug, Clone)]
pub struct SummaryCell {
    pub row: u32,
    pub col: u16,
    pub text: String,
    pub number: Option<f64>,
    pub num_fmt: Option<&'static str>,
    pub merge_to: Option<u16>,
    pub style: CellStyle,
}

fn cell_num(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

fn s_num(summary: &Value, key: &str) -> Option<f64> {
    summary.get(key).and_then(cell_num)
}

fn value_style(v: f64) -> CellStyle {
    if v >= 0.0 {
        CellStyle::ValuePos
    } else {
        CellStyle::ValueNeg
    }
}

/// Build the full Summary sheet as a flat cell list. `summary` is the
/// `summary_flat` object (authoritative core stats); `cleaned` is the Trade
/// Sheet output from `workbook_tradesheet` (drives per-trade sums / monthly /
/// outliers), so the Summary can never diverge from the detail rows.
///
/// ponytail: exact hex colours/merges/row-heights of the live sheet are a
/// cosmetic ceiling — this emits the same labels, values, sections and number
/// formats (the parity that matters), styled by class not by pixel.
pub fn build_summary_cells(
    cleaned: &[Map<String, Value>],
    summary: &Value,
    combo_label: &str,
    from_date: &str,
    to_date: &str,
) -> Vec<SummaryCell> {
    let mut out = Vec::new();
    let push = |cells: &mut Vec<SummaryCell>, row: u32, col: u16, text: String,
                number: Option<f64>, num_fmt: Option<&'static str>, merge_to: Option<u16>,
                style: CellStyle| {
        cells.push(SummaryCell { row, col, text, number, num_fmt, merge_to, style });
    };

    let get = |t: &Map<String, Value>, k: &str| -> Option<f64> { t.get(k).and_then(cell_num) };

    // ── Per-trade accumulation over the cleaned (chronological) rows ──────────
    let (mut sum_pct, mut sum_pos_pct, mut sum_neg_pct) = (0.0, 0.0, 0.0);
    let (mut win_cnt, mut loss_cnt, mut total_cnt) = (0u32, 0u32, 0u32);
    let (mut sum_net, mut max_net, mut min_net) = (0.0, f64::NEG_INFINITY, f64::INFINITY);
    let (mut ce_sum, mut pe_sum, mut fut_sum) = (0.0, 0.0, 0.0);
    let (mut ce_pct, mut pe_pct, mut fut_pct) = (0.0, 0.0, 0.0);
    let mut spot_sum = 0.0;
    let mut worst_dd = 0.0f64;
    let (mut win_run, mut loss_run, mut mx_win, mut mx_loss) = (0i64, 0i64, 0i64, 0i64);
    let mut live_dds: Vec<f64> = Vec::new();
    let mut final_maes: Vec<f64> = Vec::new();
    let mut trade_pcts: Vec<f64> = Vec::new();
    // year -> [12 months] rupee + pct, plus per-year worst %DD.
    let mut by_ym_rs: BTreeMap<i32, [f64; 12]> = BTreeMap::new();
    let mut by_ym_pct: BTreeMap<i32, [f64; 12]> = BTreeMap::new();
    let mut by_yr_dd: BTreeMap<i32, f64> = BTreeMap::new();

    for t in cleaned {
        // Per-leg sums (every row contributes its own CE/PE/FUT split).
        if let Some(v) = get(t, "CE P&L") { ce_sum += v; }
        if let Some(v) = get(t, "PE P&L") { pe_sum += v; }
        if let Some(v) = get(t, "FUT P&L") { fut_sum += v; }
        if let Some(v) = get(t, "CE P&L %") { ce_pct += v; }
        if let Some(v) = get(t, "PE P&L %") { pe_pct += v; }
        if let Some(v) = get(t, "FUT P&L %") { fut_pct += v; }
        if let Some(v) = get(t, "Spot P&L") { spot_sum += v; }

        // Trade-level values live on the trade's first row only (% P&L present).
        let Some(pct) = get(t, "% P&L") else { continue };
        sum_pct += pct;
        total_cnt += 1;
        trade_pcts.push(pct);
        if pct > 0.0 {
            sum_pos_pct += pct;
            win_cnt += 1;
            win_run += 1;
            loss_run = 0;
            mx_win = mx_win.max(win_run);
        } else if pct < 0.0 {
            sum_neg_pct += pct;
            loss_cnt += 1;
            loss_run += 1;
            win_run = 0;
            mx_loss = mx_loss.max(loss_run);
        }
        if let Some(n) = get(t, "Net P&L") {
            sum_net += n;
            max_net = max_net.max(n);
            min_net = min_net.min(n);
        }
        if let Some(l) = get(t, "Actual Live DD") { live_dds.push(l); }
        if let Some(m) = get(t, "Final MAE") { final_maes.push(m); }
        // Worst booked %DD (Cumulative/Peak already carry the right basis).
        if let (Some(cum), Some(peak)) = (get(t, "Cumulative"), get(t, "Peak")) {
            if peak != 0.0 && cum < peak {
                worst_dd = worst_dd.min((cum / peak - 1.0) * 100.0);
            }
        }
        // Monthly buckets keyed by Exit Date year/month.
        if let Some(exit) = t.get("Exit Date").and_then(Value::as_str) {
            if let Ok(d) = NaiveDate::parse_from_str(exit, "%Y-%m-%d") {
                use chrono::Datelike;
                let (yr, mi) = (d.year(), (d.month0()) as usize);
                let net = get(t, "Net P&L").unwrap_or(0.0);
                by_ym_rs.entry(yr).or_insert([0.0; 12])[mi] += net;
                by_ym_pct.entry(yr).or_insert([0.0; 12])[mi] += pct;
                if let Some(dd) = get(t, "%DD") {
                    let e = by_yr_dd.entry(yr).or_insert(0.0);
                    if dd < *e {
                        *e = dd;
                    }
                }
            }
        }
    }
    if !max_net.is_finite() { max_net = 0.0; }
    if !min_net.is_finite() { min_net = 0.0; }

    let avg_win_pct = if win_cnt > 0 { sum_pos_pct / win_cnt as f64 } else { 0.0 };
    let avg_loss_pct = if loss_cnt > 0 { sum_neg_pct / loss_cnt as f64 } else { 0.0 };
    let win_rate = if total_cnt > 0 { win_cnt as f64 / total_cnt as f64 * 100.0 } else { 0.0 };
    let loss_rate = if total_cnt > 0 { loss_cnt as f64 / total_cnt as f64 * 100.0 } else { 0.0 };
    let avg_net = if total_cnt > 0 { sum_net / total_cnt as f64 } else { 0.0 };
    let avg_pct = if total_cnt > 0 { sum_pct / total_cnt as f64 } else { 0.0 };
    let expectancy = if avg_loss_pct != 0.0 {
        ((win_rate / 100.0) * avg_win_pct - (loss_rate / 100.0) * avg_loss_pct.abs())
            / avg_loss_pct.abs()
    } else {
        0.0
    };

    // Authoritative core stats from the engine summary (single source of truth).
    let opt_cagr = s_num(summary, "cagr_options").unwrap_or(0.0);
    let spot_cagr = s_num(summary, "cagr_spot").unwrap_or(0.0);
    let max_dd_pct = s_num(summary, "max_dd_pct").unwrap_or(worst_dd);
    let car_mdd = s_num(summary, "car_mdd").unwrap_or(0.0);
    let spot_change = s_num(summary, "spot_change").unwrap_or(spot_sum);
    let spot_change_pct = s_num(summary, "spot_change_pct").unwrap_or(0.0);
    let live_dd_min = live_dds.iter().copied().fold(0.0_f64, f64::min);
    let live_dd_avg = if live_dds.is_empty() {
        0.0
    } else {
        live_dds.iter().sum::<f64>() / live_dds.len() as f64
    };
    let avg_final_mae = if final_maes.is_empty() {
        0.0
    } else {
        final_maes.iter().sum::<f64>() / final_maes.len() as f64
    };
    let car_mdd_live = if live_dd_min != 0.0 { opt_cagr / live_dd_min.abs() } else { 0.0 };

    // Outliers: cumulative top/bottom % P&L, and P&L% with them stripped.
    let mut desc = trade_pcts.clone();
    desc.sort_by(|a, b| b.partial_cmp(a).unwrap_or(std::cmp::Ordering::Equal));
    let n = desc.len();
    let top = |k: usize| -> f64 { desc.iter().take(k).sum() };
    let bot = |k: usize| -> f64 { desc.iter().rev().take(k).sum() };
    let (p1, p2, p3) = (top(1), top(2), top(3));
    let (nn1, nn2, nn3) = (bot(1), bot(2), bot(3));
    let total_pct_sum: f64 = trade_pcts.iter().sum();
    let pct_no_o1 = total_pct_sum - p1 - nn1;
    let pct_no_o2 = total_pct_sum - p2 - nn2;
    let pct_no_o3 = total_pct_sum - p3 - nn3;
    let _ = n;

    // ── Layout ────────────────────────────────────────────────────────────────
    let fpct = |v: f64| format!("{}{:.2}%", if v >= 0.0 { "+" } else { "" }, v);
    let fcur = |v: f64| format!("₹{:.2}", v);
    let mut r = 0u32;

    push(&mut out, r, 0, "BACKTEST SUMMARY REPORT".into(), None, None, Some(4), CellStyle::Title);
    r += 1;
    let mut sub = Vec::new();
    if !combo_label.is_empty() { sub.push(combo_label.to_string()); }
    if !from_date.is_empty() || !to_date.is_empty() {
        sub.push(format!("{from_date} → {to_date}"));
    }
    push(&mut out, r, 0, sub.join("   ·   "), None, None, Some(4), CellStyle::Subtitle);
    r += 2;

    // Two-column KV helper: A/B on the left, D/E on the right.
    let kv = |cells: &mut Vec<SummaryCell>, r: u32, left_col: u16,
                  label: &str, text: String, style: CellStyle| {
        cells.push(SummaryCell { row: r, col: left_col, text: label.into(), number: None,
            num_fmt: None, merge_to: None, style: CellStyle::Label });
        cells.push(SummaryCell { row: r, col: left_col + 1, text, number: None,
            num_fmt: None, merge_to: None, style });
    };

    push(&mut out, r, 0, "PERFORMANCE OVERVIEW".into(), None, None, Some(4), CellStyle::Section);
    r += 1;
    kv(&mut out, r, 0, "Overall Profit", fpct(sum_pct), value_style(sum_pct));
    kv(&mut out, r, 3, "No. of Trades", total_cnt.to_string(), CellStyle::ValueNeutral);
    r += 1;
    kv(&mut out, r, 0, "Win %", format!("{win_rate:.2}%"), CellStyle::ValuePos);
    kv(&mut out, r, 3, "Loss %", format!("{loss_rate:.2}%"), CellStyle::ValueNeg);
    r += 1;
    kv(&mut out, r, 0, "Avg Profit on Winners", format!("{avg_win_pct:.2}%"), CellStyle::ValuePos);
    kv(&mut out, r, 3, "Avg Loss on Losers", format!("{avg_loss_pct:.2}%"), CellStyle::ValueNeg);
    r += 1;
    kv(&mut out, r, 0, "Avg Profit per Trade", format!("{}{avg_net:.2}", if avg_net >= 0.0 { "+" } else { "" }), value_style(avg_net));
    kv(&mut out, r, 3, "Expectancy Ratio", format!("{expectancy:.4}"), value_style(expectancy));
    r += 1;
    kv(&mut out, r, 0, "Net P/L Avg %", format!("{}{avg_pct:.4}%", if avg_pct >= 0.0 { "+" } else { "" }), value_style(avg_pct));
    r += 1;
    kv(&mut out, r, 0, "Max Profit (Single Trade)", fcur(max_net), CellStyle::ValuePos);
    kv(&mut out, r, 3, "Max Loss (Single Trade)", fcur(min_net), CellStyle::ValueNeg);
    r += 1;
    kv(&mut out, r, 0, "CAGR (Options)", fpct(opt_cagr), value_style(opt_cagr));
    kv(&mut out, r, 3, "CAGR (Spot)", fpct(spot_cagr), value_style(spot_cagr));
    r += 2;

    // ROI vs Spot table.
    for (c, h) in [(0u16, "Type"), (1, "Sum"), (2, "%")] {
        push(&mut out, r, c, h.into(), None, None, None, CellStyle::Header);
    }
    push(&mut out, r, 3, "ROI vs Spot".into(), None, None, Some(4), CellStyle::Header);
    let roi_pct = if spot_change_pct != 0.0 { sum_pct / spot_change_pct.abs() } else { 0.0 };
    push(&mut out, r + 1, 3, fpct(roi_pct), None, None, Some(4), value_style(roi_pct));
    r += 1;
    let type_row = |cells: &mut Vec<SummaryCell>, r: u32, label: &str, sum: f64, pct: Option<f64>| {
        cells.push(SummaryCell { row: r, col: 0, text: label.into(), number: None, num_fmt: None, merge_to: None, style: CellStyle::Label });
        cells.push(SummaryCell { row: r, col: 1, text: format!("{sum:.2}"), number: None, num_fmt: None, merge_to: None, style: value_style(sum) });
        if let Some(p) = pct {
            cells.push(SummaryCell { row: r, col: 2, text: format!("{}{p:.2}%", if p >= 0.0 { "+" } else { "" }), number: None, num_fmt: None, merge_to: None, style: value_style(p) });
        }
    };
    let has_calls = cleaned.iter().any(|t| t.contains_key("CE P&L"));
    let has_puts = cleaned.iter().any(|t| t.contains_key("PE P&L"));
    let has_futures = cleaned.iter().any(|t| t.contains_key("FUT P&L"));
    type_row(&mut out, r, "Spot P&L", spot_change, Some(spot_change_pct));
    r += 1;
    if has_calls { type_row(&mut out, r, "CE P&L", ce_sum, Some(ce_pct * 100.0)); r += 1; }
    if has_puts { type_row(&mut out, r, "PE P&L", pe_sum, Some(pe_pct * 100.0)); r += 1; }
    if has_futures { type_row(&mut out, r, "FUT P&L", fut_sum, Some(fut_pct * 100.0)); r += 1; }
    if has_calls && has_puts {
        type_row(&mut out, r, "CE + PE P&L", ce_sum + pe_sum, Some((ce_pct + pe_pct) * 100.0));
        r += 1;
    }
    type_row(&mut out, r, "Net P&L", sum_net, Some(sum_pct));
    r += 2;

    push(&mut out, r, 0, "RISK METRICS".into(), None, None, Some(4), CellStyle::Section);
    r += 1;
    kv(&mut out, r, 0, "Max Drawdown", format!("{max_dd_pct:.2}%"), CellStyle::ValueNeg);
    kv(&mut out, r, 3, "Return / MaxDD", format!("{car_mdd:.2}%"), value_style(car_mdd));
    r += 2;

    push(&mut out, r, 0, "CONSISTENCY & STREAKS".into(), None, None, Some(4), CellStyle::Section);
    r += 1;
    kv(&mut out, r, 0, "Max Win Streak", format!("{mx_win} trades"), CellStyle::ValuePos);
    kv(&mut out, r, 3, "Max Losing Streak", format!("{mx_loss} trades"), CellStyle::ValueNeg);
    r += 2;

    // Monthly Returns (₹ and %). Header: Year, 12 months, Total, Max DD.
    const MONTHS: [&str; 12] = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    let month_block = |cells: &mut Vec<SummaryCell>, start: u32, title: &str,
                           data: &BTreeMap<i32, [f64; 12]>, is_pct: bool| -> u32 {
        let mut r = start;
        cells.push(SummaryCell { row: r, col: 0, text: title.into(), number: None, num_fmt: None, merge_to: Some(14), style: CellStyle::Section });
        r += 1;
        cells.push(SummaryCell { row: r, col: 0, text: "Year".into(), number: None, num_fmt: None, merge_to: None, style: CellStyle::Header });
        for (i, m) in MONTHS.iter().enumerate() {
            cells.push(SummaryCell { row: r, col: (i + 1) as u16, text: (*m).into(), number: None, num_fmt: None, merge_to: None, style: CellStyle::Header });
        }
        cells.push(SummaryCell { row: r, col: 13, text: "Total".into(), number: None, num_fmt: None, merge_to: None, style: CellStyle::Header });
        cells.push(SummaryCell { row: r, col: 14, text: "Max DD".into(), number: None, num_fmt: None, merge_to: None, style: CellStyle::Header });
        r += 1;
        for (yr, mos) in data {
            cells.push(SummaryCell { row: r, col: 0, text: yr.to_string(), number: None, num_fmt: None, merge_to: None, style: CellStyle::Header });
            let mut total = 0.0;
            for (i, v) in mos.iter().enumerate() {
                total += *v;
                let (num, fmt) = if is_pct { (*v / 100.0, Some("0.00%")) } else { (*v, None) };
                cells.push(SummaryCell { row: r, col: (i + 1) as u16, text: String::new(), number: Some(num), num_fmt: fmt, merge_to: None, style: value_style(*v) });
            }
            let (tnum, tfmt) = if is_pct { (total / 100.0, Some("0.00%")) } else { (total, None) };
            cells.push(SummaryCell { row: r, col: 13, text: String::new(), number: Some(tnum), num_fmt: tfmt, merge_to: None, style: value_style(total) });
            if let Some(dd) = by_yr_dd.get(yr) {
                cells.push(SummaryCell { row: r, col: 14, text: String::new(), number: Some(*dd), num_fmt: Some("0.00%"), merge_to: None, style: CellStyle::ValueNeg });
            }
            r += 1;
        }
        r
    };
    r = month_block(&mut out, r, "MONTHLY RETURNS (₹ Net P&L)", &by_ym_rs, false);
    r += 1;
    r = month_block(&mut out, r, "MONTHLY RETURNS (% Net P&L)", &by_ym_pct, true);
    r += 1;

    push(&mut out, r, 0, "LIVE DD & OUTLIER ANALYSIS".into(), None, None, Some(4), CellStyle::Section);
    r += 1;
    kv(&mut out, r, 0, "Actual Live DD (min)", format!("{live_dd_min:.2}%"), CellStyle::ValueNeg);
    kv(&mut out, r, 3, "Avg Actual Live DD", format!("{live_dd_avg:.2}%"), CellStyle::ValueNeg);
    r += 1;
    kv(&mut out, r, 0, "Avg Final MAE", format!("{avg_final_mae:.2}%"), CellStyle::ValueNeg);
    r += 1;
    kv(&mut out, r, 0, "CAR/MDD (Booked)", format!("{car_mdd:.2}%"), value_style(car_mdd));
    kv(&mut out, r, 3, "CAR/MDD Live", format!("{car_mdd_live:.2}%"), value_style(car_mdd_live));
    r += 2;
    for (i, (p, nv, no)) in [
        (p1, nn1, pct_no_o1),
        (p2, nn2, pct_no_o2),
        (p3, nn3, pct_no_o3),
    ]
    .iter()
    .enumerate()
    {
        let k = i + 1;
        kv(&mut out, r, 0, &format!("+ve Outlier {k}"), fpct(*p), CellStyle::ValuePos);
        kv(&mut out, r, 3, &format!("-ve Outlier {k}"), fpct(*nv), CellStyle::ValueNeg);
        r += 1;
        push(&mut out, r, 0, format!("P&L % Without Top {k} Outliers"), None, None, Some(3), CellStyle::Label);
        push(&mut out, r, 4, fpct(*no), None, None, None, value_style(*no));
        r += 1;
    }
    out
}

// ── Patch wise sheet (port of excel_builder._patch_wise_layout, non-midcap) ───

fn parse_ymd(s: &str) -> Option<NaiveDate> {
    NaiveDate::parse_from_str(s.trim(), "%Y-%m-%d").ok()
}

struct PatchTrade {
    entry: String,
    exit: String,
    entry_date: Option<NaiveDate>,
    exit_date: Option<NaiveDate>,
    call_pct: Option<f64>,
    call_mae: Option<f64>,
}

struct ChainRow {
    drive: f64,
    cumm: f64,
    peak: f64,
    dd: Option<f64>,
    pct_dd: f64,
    mae: f64,
    lowest_nav: f64,
    live_dd: f64,
}

struct Chain {
    rows: Vec<ChainRow>,
    entry: Option<String>,
    exit: Option<String>,
    cagr: Option<f64>,
    pnl_sum: f64,
    live_dd_min: Option<f64>,
}

fn build_chain(trades: &[&PatchTrade]) -> Chain {
    let (mut prev_cumm, mut peak, mut prev_peak) = (100.0_f64, 100.0_f64, 100.0_f64);
    let mut rows = Vec::new();
    let mut pnl_sum = 0.0;
    let mut live_dd_min = f64::INFINITY;
    for td in trades {
        let d = td.call_pct.filter(|v| v.is_finite()).unwrap_or(0.0);
        let cumm = prev_cumm * (1.0 + d / 100.0);
        peak = peak.max(cumm);
        let dd = if peak > cumm { Some(cumm - peak) } else { None };
        let pct_dd = match dd {
            Some(v) if peak != 0.0 => v / peak,
            _ => 0.0,
        };
        let m = td.call_mae.filter(|v| v.is_finite()).unwrap_or(0.0);
        let lowest_nav = prev_cumm * (1.0 + m / 100.0);
        let live_dd = if prev_peak != 0.0 {
            (lowest_nav / prev_peak - 1.0) * 100.0
        } else {
            0.0
        };
        rows.push(ChainRow { drive: d, cumm, peak, dd, pct_dd, mae: m, lowest_nav, live_dd });
        pnl_sum += d;
        live_dd_min = live_dd_min.min(live_dd);
        prev_cumm = cumm;
        prev_peak = peak;
    }
    if rows.is_empty() {
        return Chain { rows, entry: None, exit: None, cagr: None, pnl_sum: 0.0, live_dd_min: None };
    }
    let (f, l) = (trades[0], trades[trades.len() - 1]);
    let days = match (f.entry_date, l.exit_date) {
        (Some(a), Some(b)) => Some((b - a).num_days() as f64),
        _ => None,
    };
    let last_cumm = rows.last().unwrap().cumm;
    let cagr = match days {
        Some(d) if d > 0.0 && last_cumm > 0.0 => {
            Some(((last_cumm / 100.0).powf(365.0 / d) - 1.0) * 100.0)
        }
        _ => None,
    };
    Chain {
        rows,
        entry: Some(f.entry.clone()),
        exit: Some(l.exit.clone()),
        cagr,
        pnl_sum,
        live_dd_min: live_dd_min.is_finite().then_some(live_dd_min),
    }
}

/// Patch wise phase-distribution sheet (single "Nifty {…}" block; the shadow is
/// non-midcap). Patches split on a 30-day entry-gap (the live seg-start split
/// needs filter_segments this endpoint does not carry — same fallback the live
/// builder uses). Empty vec when there are no trades (no sheet).
pub fn build_patchwise_cells(rows: &[TradeRow]) -> Vec<SummaryCell> {
    let mut grouped: BTreeMap<u64, Vec<&TradeRow>> = BTreeMap::new();
    for r in rows {
        grouped.entry(r.trade_id).or_default().push(r);
    }
    let mut order: Vec<u64> = grouped.keys().copied().collect();
    order.sort_by(|a, b| {
        let ma = main_leg(&grouped[a]);
        let mb = main_leg(&grouped[b]);
        ma.entry_date.cmp(&mb.entry_date).then(a.cmp(b))
    });

    let has_calls = rows.iter().any(|r| r.option_type.eq_ignore_ascii_case("CE"));
    let has_puts = rows.iter().any(|r| r.option_type.eq_ignore_ascii_case("PE"));
    let has_futures = rows.iter().any(|r| r.instrument == "FUTURES");
    let opt = ["CE", "PE", "FUT"]
        .iter()
        .zip([has_calls, has_puts, has_futures])
        .filter(|(_, h)| *h)
        .map(|(n, _)| *n)
        .collect::<Vec<_>>()
        .join(" + ");
    let title = format!("Nifty {}", if opt.is_empty() { "Options" } else { &opt });

    let mut tdata: Vec<PatchTrade> = Vec::new();
    for tid in &order {
        let legs = &grouped[tid];
        let main = main_leg(legs);
        let spot = main.entry_spot;
        let dir: Vec<&&TradeRow> = legs.iter().filter(|l| is_directional(&l.option_type)).collect();
        let nifty_pnl: f64 = dir.iter().map(|l| l.leg_pnl).sum();
        let call_pct = if !dir.is_empty() && spot != 0.0 {
            Some(nifty_pnl / spot * 100.0)
        } else {
            None
        };
        let call_mae = if dir.is_empty() {
            None
        } else {
            Some(dir.iter().filter_map(|l| l.mae).sum())
        };
        tdata.push(PatchTrade {
            entry: main.entry_date.clone(),
            exit: main.exit_date.clone(),
            entry_date: parse_ymd(&main.entry_date),
            exit_date: parse_ymd(&main.exit_date),
            call_pct,
            call_mae,
        });
    }
    if tdata.is_empty() {
        return Vec::new();
    }

    // 30-day gap patch split.
    let mut patches: Vec<Vec<&PatchTrade>> = Vec::new();
    let mut last_exit: Option<NaiveDate> = None;
    for td in &tdata {
        let gap = match (last_exit, td.entry_date) {
            (Some(le), Some(en)) => (en - le).num_days(),
            _ => 0,
        };
        if patches.is_empty() || gap > 30 {
            patches.push(Vec::new());
        }
        patches.last_mut().unwrap().push(td);
        if let Some(x) = td.exit_date {
            last_exit = Some(x);
        }
    }

    let detail_hdr = ["Net P&L %", "Cumulative", "Peak", "DD", "%DD", "MAE", "Lowest NAV", "Actual Live DD"];
    let side_hdr = ["Entry", "Exit", "CAGR", "Net P&L %", "Live DD"];
    let dw = detail_hdr.len() as u16;
    let detail_start = 0u16;
    let side_start = detail_start + dw + 1;

    let chains: Vec<Chain> = patches.iter().map(|p| build_chain(p)).collect();

    let mut out = Vec::new();
    fn num(row: u32, col: u16, v: Option<f64>, fmt: Option<&'static str>) -> SummaryCell {
        SummaryCell {
            row,
            col,
            text: String::new(),
            number: v.filter(|x| x.is_finite()),
            num_fmt: fmt,
            merge_to: None,
            style: v.map(value_style).unwrap_or(CellStyle::Plain),
        }
    }
    // Live rows 1/2/4/5 → 0-based 0/1/3/4.
    out.push(SummaryCell { row: 0, col: detail_start, text: title, number: None, num_fmt: None,
        merge_to: Some(detail_start + dw - 1), style: CellStyle::Title });
    out.push(SummaryCell { row: 1, col: detail_start, text: "Phase wise Distribution".into(),
        number: None, num_fmt: None, merge_to: Some(detail_start + dw - 1), style: CellStyle::Subtitle });
    for (i, h) in detail_hdr.iter().enumerate() {
        out.push(SummaryCell { row: 3, col: detail_start + i as u16, text: (*h).into(),
            number: None, num_fmt: None, merge_to: None, style: CellStyle::Header });
    }
    for (i, h) in side_hdr.iter().enumerate() {
        out.push(SummaryCell { row: 3, col: side_start + i as u16, text: (*h).into(),
            number: None, num_fmt: None, merge_to: None, style: CellStyle::Section });
    }
    let mut rr = 4u32;
    for ch in &chains {
        for rw in &ch.rows {
            out.push(num(rr, detail_start, Some(rw.drive), None));
            out.push(num(rr, detail_start + 1, Some(rw.cumm), None));
            out.push(num(rr, detail_start + 2, Some(rw.peak), None));
            out.push(num(rr, detail_start + 3, rw.dd, None));
            out.push(num(rr, detail_start + 4, Some(rw.pct_dd), None));
            out.push(num(rr, detail_start + 5, Some(rw.mae), None));
            out.push(num(rr, detail_start + 6, Some(rw.lowest_nav), None));
            out.push(num(rr, detail_start + 7, Some(rw.live_dd), None));
            rr += 1;
        }
    }
    for (i, ch) in chains.iter().enumerate() {
        let sr = 4 + i as u32;
        out.push(SummaryCell { row: sr, col: side_start, text: ch.entry.clone().unwrap_or_default(),
            number: None, num_fmt: None, merge_to: None, style: CellStyle::Plain });
        out.push(SummaryCell { row: sr, col: side_start + 1, text: ch.exit.clone().unwrap_or_default(),
            number: None, num_fmt: None, merge_to: None, style: CellStyle::Plain });
        out.push(num(sr, side_start + 2, ch.cagr, Some("0.00\"%\"")));
        out.push(num(sr, side_start + 3, Some(ch.pnl_sum), None));
        out.push(num(sr, side_start + 4, ch.live_dd_min, None));
    }
    out
}

// ── WOW & MOM Summary (port of wow_mom.py core: bucketing + compute_ratios) ───

const WOW_RF: f64 = 0.06 / 52.0;
const MOM_RF: f64 = 0.06 / 12.0;
const MONTHS: [&str; 12] = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

fn std_dev(arr: &[f64], mean: f64) -> f64 {
    if arr.is_empty() {
        return 0.0;
    }
    (arr.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / arr.len() as f64).sqrt()
}

/// Port of `compute_ratios` — Sharpe/Sortino/SQN/CAGR/expectancy + slope stats
/// over a per-period return series. None when fewer than 3 periods.
struct Ratios {
    n: usize,
    win_pct: f64,
    sharpe: f64,
    sortino: f64,
    sqn: f64,
    cagr: f64,
    expectancy: f64,
}

fn compute_ratios(returns: &[f64], rf: f64, n_ann: f64) -> Option<Ratios> {
    let n = returns.len();
    if n < 3 {
        return None;
    }
    let avg = returns.iter().sum::<f64>() / n as f64;
    let sd = std_dev(returns, avg);
    let pos: Vec<f64> = returns.iter().copied().filter(|v| *v > 0.0).collect();
    let neg: Vec<f64> = returns.iter().copied().filter(|v| *v < 0.0).collect();
    let wp = pos.len() as f64 / n as f64;
    let wa = if pos.is_empty() { 0.0 } else { pos.iter().sum::<f64>() / pos.len() as f64 };
    let lp = neg.len() as f64 / n as f64;
    let la = if neg.is_empty() { 0.0 } else { neg.iter().sum::<f64>() / neg.len() as f64 };
    let exp = if la != 0.0 { (wp / la.abs()) * wa - lp } else { 0.0 };
    let sd_neg = if neg.is_empty() { 0.0 } else { std_dev(&neg, neg.iter().sum::<f64>() / neg.len() as f64) };
    let sharpe = if sd != 0.0 { ((avg - rf) / sd) * n_ann.sqrt() } else { 0.0 };
    let sortino = if sd_neg != 0.0 { ((avg - rf) / sd_neg) * n_ann.sqrt() } else { 0.0 };
    let sqn = if sd != 0.0 { (avg / sd) * (n as f64).sqrt() } else { 0.0 };
    let c: f64 = returns.iter().fold(1.0, |acc, v| acc * (1.0 + v));
    let cagr = c.powf(n_ann / n as f64) - 1.0;
    Some(Ratios { n, win_pct: wp, sharpe, sortino, sqn, cagr, expectancy: exp })
}

/// WOW & MOM Summary sheet. WOW buckets by Expiry ISO-week, MOM by Exit month;
/// values are per-trade `% P&L` as decimals. Emits both year×period grids plus
/// the `compute_ratios` stat block for each axis.
///
/// ponytail: ports the DATA (grids + ratios) faithfully; the live sheet's MIN-of
/// -MAE/Live-DD grids, colour ramp and merged week-bands are cosmetic chrome
/// deferred until a live reference workbook exists to diff against. Yearly week
/// identity (Exit-Date bucketing) not handled — non-yearly only.
pub fn build_wow_mom_cells(cleaned: &[Map<String, Value>]) -> Vec<SummaryCell> {
    let getf = |t: &Map<String, Value>, k: &str| -> Option<f64> { t.get(k).and_then(cell_num) };

    // WOW: year -> week -> Σdec ; MOM: year -> [12] Σdec, plus per-year dd/live.
    let mut wow: BTreeMap<i32, BTreeMap<u32, f64>> = BTreeMap::new();
    let mut mom: BTreeMap<i32, [f64; 12]> = BTreeMap::new();
    let mut mom_dd: BTreeMap<i32, Vec<f64>> = BTreeMap::new();
    let mut mom_live: BTreeMap<i32, Vec<f64>> = BTreeMap::new();
    let mut any = false;

    for t in cleaned {
        let Some(ret) = getf(t, "% P&L") else { continue };
        any = true;
        let dec = ret / 100.0;
        // WOW week identity: Cadence Expiry or Expiry.
        let wk_src = t
            .get("Cadence Expiry")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .or_else(|| t.get("Expiry").and_then(Value::as_str));
        if let Some(d) = wk_src.and_then(parse_ymd) {
            use chrono::Datelike;
            let iso = d.iso_week();
            *wow.entry(iso.year()).or_default().entry(iso.week()).or_insert(0.0) += dec;
        }
        if let Some(x) = t.get("Exit Date").and_then(Value::as_str).and_then(parse_ymd) {
            use chrono::Datelike;
            let (yr, mi) = (x.year(), (x.month0()) as usize);
            mom.entry(yr).or_insert([0.0; 12])[mi] += dec;
            if let Some(dd) = getf(t, "%DD") {
                mom_dd.entry(yr).or_default().push(dd);
            }
            if let Some(l) = getf(t, "Actual Live DD") {
                mom_live.entry(yr).or_default().push(l / 100.0);
            }
        }
    }
    if !any {
        return Vec::new();
    }
    // Drop near-zero WOW weeks (mirrors the live cleanup).
    for weeks in wow.values_mut() {
        weeks.retain(|_, v| v.abs() > 1e-9);
    }
    wow.retain(|_, w| !w.is_empty());

    let mut out = Vec::new();
    let hdr = |cells: &mut Vec<SummaryCell>, r: u32, c: u16, s: &str, style: CellStyle| {
        cells.push(SummaryCell { row: r, col: c, text: s.into(), number: None, num_fmt: None, merge_to: None, style });
    };
    let val = |cells: &mut Vec<SummaryCell>, r: u32, c: u16, v: f64, fmt: Option<&'static str>| {
        cells.push(SummaryCell { row: r, col: c, text: String::new(), number: Some(v), num_fmt: fmt, merge_to: None, style: value_style(v) });
    };

    out.push(SummaryCell { row: 0, col: 0, text: "WOW & MOM Summary".into(), number: None, num_fmt: None, merge_to: Some(13), style: CellStyle::Title });
    let mut r = 2u32;

    // ── MOM block: Year | Jan..Dec | Total | Max DD | Live DD ────────────────
    hdr(&mut out, r, 0, "MOM (Month-on-Month, by Exit month)", CellStyle::Section);
    r += 1;
    hdr(&mut out, r, 0, "Year", CellStyle::Header);
    for (i, m) in MONTHS.iter().enumerate() {
        hdr(&mut out, r, (i + 1) as u16, m, CellStyle::Header);
    }
    hdr(&mut out, r, 13, "Total", CellStyle::Header);
    hdr(&mut out, r, 14, "Max DD", CellStyle::Header);
    hdr(&mut out, r, 15, "Live DD", CellStyle::Header);
    r += 1;
    let mut flat_monthly: Vec<f64> = Vec::new();
    for (yr, mos) in &mom {
        hdr(&mut out, r, 0, &yr.to_string(), CellStyle::Header);
        let mut total = 0.0;
        for (i, v) in mos.iter().enumerate() {
            if v.abs() > 1e-9 {
                val(&mut out, r, (i + 1) as u16, *v, Some("0.00%"));
                total += *v;
                flat_monthly.push(*v);
            }
        }
        val(&mut out, r, 13, total, Some("0.00%"));
        if let Some(dds) = mom_dd.get(yr) {
            if let Some(mn) = dds.iter().copied().reduce(f64::min) {
                val(&mut out, r, 14, mn, Some("0.00%"));
            }
        }
        if let Some(ls) = mom_live.get(yr) {
            if let Some(mn) = ls.iter().copied().reduce(f64::min) {
                val(&mut out, r, 15, mn, Some("0.00%"));
            }
        }
        r += 1;
    }
    r += 1;
    r = emit_ratios(&mut out, r, "MOM Statistics", compute_ratios(&flat_monthly, MOM_RF, 12.0));
    r += 1;

    // ── WOW block: Year | W1..W53 ────────────────────────────────────────────
    hdr(&mut out, r, 0, "WOW (Week-on-Week, by Expiry week)", CellStyle::Section);
    r += 1;
    hdr(&mut out, r, 0, "Year", CellStyle::Header);
    for w in 1..=53u16 {
        hdr(&mut out, r, w, &format!("W{w}"), CellStyle::Header);
    }
    r += 1;
    let mut flat_weekly: Vec<f64> = Vec::new();
    for (yr, weeks) in &wow {
        hdr(&mut out, r, 0, &yr.to_string(), CellStyle::Header);
        for (w, v) in weeks {
            val(&mut out, r, *w as u16, *v, Some("0.00%"));
            flat_weekly.push(*v);
        }
        r += 1;
    }
    r += 1;
    emit_ratios(&mut out, r, "WOW Statistics", compute_ratios(&flat_weekly, WOW_RF, 52.0));

    out
}

fn emit_ratios(out: &mut Vec<SummaryCell>, start: u32, title: &str, ratios: Option<Ratios>) -> u32 {
    let mut r = start;
    out.push(SummaryCell { row: r, col: 0, text: title.into(), number: None, num_fmt: None, merge_to: Some(3), style: CellStyle::Section });
    r += 1;
    let Some(x) = ratios else {
        out.push(SummaryCell { row: r, col: 0, text: "(need ≥3 periods)".into(), number: None, num_fmt: None, merge_to: Some(3), style: CellStyle::Plain });
        return r + 1;
    };
    let rows: [(&str, f64, Option<&'static str>); 7] = [
        ("Periods", x.n as f64, None),
        ("Win %", x.win_pct, Some("0.00%")),
        ("CAGR", x.cagr, Some("0.00%")),
        ("Sharpe", x.sharpe, None),
        ("Sortino", x.sortino, None),
        ("SQN", x.sqn, None),
        ("Expectancy", x.expectancy, None),
    ];
    for (label, v, fmt) in rows {
        out.push(SummaryCell { row: r, col: 0, text: label.into(), number: None, num_fmt: None, merge_to: None, style: CellStyle::Label });
        out.push(SummaryCell { row: r, col: 1, text: String::new(), number: Some(v), num_fmt: fmt, merge_to: None, style: value_style(v) });
        r += 1;
    }
    r
}

#[cfg(test)]
mod tests {
    use super::*;

    fn leg(tid: u64, leg: u32, date: &str, ot: &str, bs: &str, ep: f64, xp: f64, spot: f64) -> TradeRow {
        let pts = if bs == "SELL" { ep - xp } else { xp - ep };
        TradeRow {
            trade_id: tid,
            leg_id: leg,
            entry_date: date.into(),
            exit_date: date.into(),
            expiry: date.into(),
            option_type: ot.into(),
            position: bs.into(),
            entry_price: ep,
            exit_price: xp,
            entry_spot: spot,
            exit_spot: spot,
            net_pnl: pts,
            leg_pnl: pts,
            ..Default::default()
        }
    }

    #[test]
    fn key_order_is_dynamic_on_leg_types() {
        // CE-only strategy → CE P&L present, PE/FUT absent, 32 columns.
        let rows = vec![leg(1, 1, "2020-01-01", "CE", "BUY", 100.0, 120.0, 20000.0)];
        let order = build_key_order(&rows, false, false);
        assert!(order.contains(&"CE P&L".to_string()));
        assert!(!order.contains(&"PE P&L".to_string()));
        assert!(!order.contains(&"FUT P&L".to_string()));
        assert_eq!(order.len(), 32, "CE-only NIFTY sheet is 32 columns");
        // Adding a filter appends Filter Segment.
        assert_eq!(build_key_order(&rows, true, false).len(), 33);
    }

    #[test]
    fn spot_pnl_only_on_lowest_leg_and_pct_is_fraction() {
        let mut c = leg(1, 1, "2020-01-01", "CE", "SELL", 100.0, 80.0, 20000.0);
        c.exit_spot = 20100.0;
        let mut p = leg(1, 2, "2020-01-01", "PE", "SELL", 90.0, 70.0, 20000.0);
        p.exit_spot = 20100.0;
        let (cleaned, _) = workbook_tradesheet(&[c, p], false, false, false);
        // leg 1 carries Spot P&L = 100; leg 2 is blank.
        assert_eq!(cleaned[0]["Spot P&L"], num(100.0));
        assert_eq!(cleaned[1]["Spot P&L"], Value::String(String::new()));
        // Spot P&L % is a fraction 100/20000.
        assert_eq!(cleaned[0]["Spot P&L %"], num(100.0 / 20000.0));
    }

    #[test]
    fn net_mae_unified_direction_rule() {
        // CE SELL (bearish) MAE -1.0 / MFE +0.5 ; PE SELL (bullish) MAE -0.3 / MFE +0.8.
        let mut ce = leg(1, 1, "2020-01-01", "CE", "SELL", 100.0, 90.0, 20000.0);
        ce.mae = Some(-1.0);
        ce.mfe = Some(0.5);
        let mut pe = leg(1, 2, "2020-01-01", "PE", "SELL", 100.0, 95.0, 20000.0);
        pe.mae = Some(-0.3);
        pe.mfe = Some(0.8);
        // bull = PE SELL: mae -0.3, mfe 0.8 ; bear = CE SELL: mae -1.0, mfe 0.5
        // nm1 = bull_mae + bear_mfe = -0.3 + 0.5 = 0.2
        // nm2 = bull_mfe + bear_mae = 0.8 + -1.0 = -0.2
        let m = calc_trade_mae(&[&ce, &pe], Some(1.0)).unwrap();
        assert!((m.0 - 0.2).abs() < 1e-9);
        assert!((m.1 - (-0.2)).abs() < 1e-9);
        // >1 directional leg → final = min(nm1, nm2, net_pct) = min(0.2,-0.2,1.0) = -0.2
        assert!((m.2 - (-0.2)).abs() < 1e-9);
    }

    #[test]
    fn live_dd_uses_prev_peak_and_final_mae() {
        // Two winning trades so peak advances; Final MAE drags Lowest NAV.
        let mut a = leg(1, 1, "2020-01-01", "CE", "BUY", 100.0, 300.0, 20000.0); // +200 pts, +1%
        a.mae = Some(-0.5);
        a.mfe = Some(1.0);
        let mut b = leg(2, 1, "2020-02-01", "CE", "BUY", 100.0, 300.0, 20000.0);
        b.mae = Some(-0.4);
        b.mfe = Some(1.0);
        let (cleaned, _) = workbook_tradesheet(&[a, b], false, false, false);
        // Trade 1: pct = 200/20000*100 = 1.0 ; cumulative = 101.0 ; peak 101.
        // finalMae single-leg = min(nm1,nm2). bull=CE BUY: mae -0.5, mfe 1.0.
        // nm1 = bull_mae + bear_mfe = -0.5 ; nm2 = bull_mfe + 0 = 1.0 ; final = -0.5.
        // lowest_nav = prev_cum(100)*(1-0.5/100) = 99.5 ; ldd = 99.5/100-1 = -0.5%.
        assert_eq!(cleaned[0]["Lowest NAV"], num(99.5));
        assert_eq!(cleaned[0]["Actual Live DD"], num(-0.5));
        // Trade 2: prev_cum=101, prev_peak=101, final=-0.4.
        // lowest = 101*(1-0.4/100)=100.596→100.6 ; ldd = 100.6/101-1 = -0.396%→-0.4.
        assert_eq!(cleaned[1]["Lowest NAV"], num(100.6));
        assert_eq!(cleaned[1]["Actual Live DD"], num(-0.4));
    }
}
