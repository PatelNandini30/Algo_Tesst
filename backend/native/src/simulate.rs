//! Phase 2b — Rust trade simulation (first slice).
//!
//! Scope today
//! -----------
//! `simulate_trades_batch(trade_specs)` takes a list of pre-resolved trade
//! specifications and returns the priced + P&L'd trade rows. The
//! orchestration (entry-date computation, strike selection, exit-date
//! resolution) stays in Python for now — only the per-trade price lookup
//! and slippage / P&L arithmetic moves to Rust.
//!
//! Why incremental
//! ---------------
//! The Python engine is ~3,000 lines with rollover, lazy legs, filters, STR,
//! re-entry, futures legs, etc. A "rip-and-replace" Rust port would take 1–2
//! weeks. Instead we move the smallest verifiable slice now, gated by the
//! parity tests in `backend/tests/test_engine_parity.py`. Subsequent slices
//! migrate more of the orchestration into Rust:
//!
//!   slice 1 (this file): per-trade lookup + slippage + P&L      ← now
//!   slice 2:              expiry-date iteration + entry/exit DTE
//!   slice 3:              strike selection (pct_of_atm, ATM offsets)
//!   slice 4:              SL/Target/Trail per-leg
//!   slice 5:              overall SL/Target
//!   slice 6:              rollover + re-entry
//!   slice 7:              filters (STR, custom segments)
//!
//! Input shape
//! -----------
//! A Python list of dicts, each describing one trade:
//!     {
//!       "trade_id":     int,
//!       "leg_id":       int,
//!       "index":        "NIFTY",
//!       "entry_date":   "2024-01-08",   // YYYY-MM-DD
//!       "exit_date":    "2024-01-10",
//!       "expiry":       "2024-01-11",
//!       "strike":       21500.0,
//!       "option_type":  "CE" | "PE",
//!       "position":     "SELL" | "BUY",
//!       "lots":         1,
//!       "lot_size":     50,
//!       "slippage_pct": 0.0
//!     }
//!
//! Output shape
//! ------------
//! A Python list of dicts, one per input, with these keys added:
//!     {
//!       ...input fields preserved...,
//!       "entry_price":     f64,    // post-slippage
//!       "exit_price":      f64,    // post-slippage
//!       "raw_entry_price": f64,    // pre-slippage
//!       "raw_exit_price":  f64,    // pre-slippage
//!       "entry_spot":      f64,
//!       "exit_spot":       f64,
//!       "net_pnl":         f64,    // sign-correct for SELL vs BUY
//!       "missing":         bool    // true if any lookup failed
//!     }
//!
//! Failed lookups (missing price for that date/strike/expiry) produce a row
//! with `missing=true` and zeros — caller decides whether to drop or report.

use std::collections::HashMap;

use chrono::NaiveDate;
use once_cell::sync::Lazy;
use rayon::prelude::*;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use crate::{
    apply_slippage, lookup_option_high, lookup_option_low, lookup_option_open,
    lookup_option_price, lookup_option_price_tradeable, lookup_spot_price,
    lookup_strikes_for_date, round2,
};

// ── Slice 2 helpers ─────────────────────────────────────────────────────────

/// Python-`:g`-style compact number formatting for strike-gap values (always
/// whole numbers in practice — 25, 50, 100, 150...): no trailing ".0".
fn fmt_g(v: f64) -> String {
    if v.fract() == 0.0 {
        format!("{}", v as i64)
    } else {
        format!("{}", v)
    }
}

/// Mirrors `base.calculate_trading_days_before_expiry` exactly.
///
/// * `days_before == 0` → most-recent trading day with date ≤ expiry
/// * `days_before  > 0` → the `days_before`-th trading day strictly before
///   expiry (1-based: days_before=1 means the day right before expiry).
///
/// `trading_days` MUST be sorted ascending (caller's responsibility) and
/// contain dates as ISO `YYYY-MM-DD` strings.
fn trading_day_before(
    expiry: &str,
    days_before: u32,
    trading_days: &[String],
) -> Option<String> {
    if trading_days.is_empty() {
        return None;
    }
    // Find rightmost index where day < expiry (or ≤ expiry for days_before=0).
    // Trading days are ascending, so binary search by string compare works
    // because ISO dates sort lexicographically.
    let cutoff = expiry.to_string();
    let idx_after = if days_before == 0 {
        // Want the last day ≤ expiry → search for first day > expiry.
        trading_days.partition_point(|d| d.as_str() <= cutoff.as_str())
    } else {
        // Want trading days strictly before expiry.
        trading_days.partition_point(|d| d.as_str() < cutoff.as_str())
    };
    if idx_after == 0 {
        return None;
    }
    if days_before == 0 {
        return Some(trading_days[idx_after - 1].clone());
    }
    let want = days_before as usize;
    if idx_after < want {
        return None;
    }
    Some(trading_days[idx_after - want].clone())
}

fn day_before_expiry(
    expiry: &str,
    days_before: u32,
    trading_days: &[String],
    calendar_days: bool,
) -> Option<String> {
    if !calendar_days {
        return trading_day_before(expiry, days_before, trading_days);
    }
    let expiry_date = NaiveDate::parse_from_str(expiry, "%Y-%m-%d").ok()?;
    let target = expiry_date - chrono::Duration::days(days_before as i64);
    let target_iso = target.format("%Y-%m-%d").to_string();
    let idx_after = trading_days.partition_point(|d| d.as_str() <= target_iso.as_str());
    (idx_after > 0).then(|| trading_days[idx_after - 1].clone())
}

/// Mirrors `base.calculate_strike_from_selection` exactly.
///
/// Supported selections: `ATM`, `ITM1..N`, `OTM1..N`. Other modes
/// (PCT_OF_ATM, premium-based) belong to a future slice — return None here
/// and the caller falls back to the Python path.
fn atm_offset_strike(
    spot_price: f64,
    strike_interval: f64,
    selection: &str,
    option_type: &str,
) -> Option<f64> {
    if strike_interval <= 0.0 {
        return None;
    }
    let atm = (spot_price / strike_interval).round() * strike_interval;
    let sel = selection.trim().to_uppercase();
    if sel == "ATM" {
        return Some(atm);
    }
    let is_call = matches!(option_type.trim().to_uppercase().as_str(), "CE" | "CALL" | "C");
    if let Some(rest) = sel.strip_prefix("ITM") {
        let n: i32 = rest.parse().ok()?;
        let offset = (n as f64) * strike_interval;
        return Some(if is_call { atm - offset } else { atm + offset });
    }
    if let Some(rest) = sel.strip_prefix("OTM") {
        let n: i32 = rest.parse().ok()?;
        let offset = (n as f64) * strike_interval;
        return Some(if is_call { atm + offset } else { atm - offset });
    }
    None
}

// ── Slice 2 PyO3 entry point ────────────────────────────────────────────────

/// Every strike-selection mode the Python engine supports. New modes are
/// added here, and `extract_leg_cfgs` is the single place that recognises
/// new payload shapes.
#[derive(Debug, Clone)]
enum StrikeSel {
    Fixed(String),                  // ATM, ITM1..N, OTM1..N
    // Strike defined RELATIVE to an earlier leg's resolved strike:
    //   wing = parent_strike ± offset*interval  (+ for CALL, − for PUT).
    // `ref_leg` is the 1-based leg number (== leg_id) of the parent, which
    // MUST appear before this leg. Powers Iron Condor / vertical-spread wings.
    RelToLeg { ref_leg: i64, offset: f64 },
    PctOfAtm { value: f64, direction: String },  // e.g. 0.5% OTM
    AtmStraddlePremPct(f64),        // value in percent — uses ATM straddle premium
    StraddleWidth { multiplier: f64, direction: String },
    ClosestPremium(f64),
    PremiumGte(f64),
    PremiumLte(f64),
    PremiumRange { lower: f64, upper: f64 },
    /// Strike picked by TIME VALUE (close - intrinsic, intrinsic floored at 0).
    /// Same chain + same tie-break as ClosestPremium; only the compared number
    /// differs. Negative time values (deep ITM, close < intrinsic) stay in the
    /// candidate set deliberately — target 0 with candidates -1 and 5 must pick -1.
    TimeValue { target: f64, cmp: TvCmp, side: TvSide, range_pct: f64, pct_units: bool },
    /// Absolute Black-Scholes Delta target (0 < delta < 1). EOD mode derives
    /// Delta from spot, strike, calendar DTE and fixed per-index IV assumptions.
    Delta(f64),
}

fn delta_iv(index: &str) -> f64 {
    match index.trim().to_uppercase().as_str() {
        "NIFTY" => 0.13,
        "BANKNIFTY" => 0.16,
        "FINNIFTY" => 0.14,
        "MIDCPNIFTY" => 0.16,
        "SENSEX" => 0.14,
        "BANKEX" => 0.16,
        _ => 0.15,
    }
}

fn norm_cdf(x: f64) -> f64 {
    const INV_SQRT_2PI: f64 = 0.3989422804014327;
    let t = 1.0 / (1.0 + 0.2316419 * x.abs());
    let p = 1.0 - INV_SQRT_2PI * (-0.5 * x * x).exp() * t
        * (0.319381530 + t * (-0.356563782 + t * (1.781477937
            + t * (-1.821255978 + t * 1.330274429))));
    if x >= 0.0 { p } else { 1.0 - p }
}

fn bs_abs_delta(spot: f64, strike: f64, calendar_dte: f64, sigma: f64, is_call: bool) -> f64 {
    if spot <= 0.0 || strike <= 0.0 || sigma <= 0.0 {
        return 0.5;
    }
    let t = calendar_dte.max(0.5) / 365.0;
    let d1 = ((spot / strike).ln() + 0.5 * sigma * sigma * t) / (sigma * t.sqrt());
    let call_delta = norm_cdf(d1);
    if is_call { call_delta } else { 1.0 - call_delta }
}

/// How a TimeValue selection filters the chain before picking the nearest.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum TvCmp {
    Nearest,
    Gte,
    Lte,
}

/// Which side of the money a TimeValue selection may pick from. Time value is
/// not monotonic in strike (it peaks near ATM and decays both ways), so without
/// this a target below the peak has an ITM and an OTM answer and the pick flips
/// between them. `Any` = whole chain, the default.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum TvSide {
    Any,
    Otm,
    Itm,
}

/// Features that the engine supports but Phase 2b slice 2 does not yet.
/// If any leg/payload sets one of these, `resolve_trade_specs` returns an
/// empty list and the Python engine handles the run — guaranteeing no
/// silently-wrong numbers reach the user.
#[derive(Debug, Clone)]
struct UnsupportedReason(pub String);

#[derive(Debug, Clone)]
struct LegCfg {
    option_type: String,
    position: String,
    lots: i64,
    strike_interval: f64,
    strike: StrikeSel,
    // Only meaningful when `strike` is StraddleWidth: true when another leg
    // in the SAME trade shares this leg's multiplier+direction (so they
    // resolve to the same strike and must shift together on joint CE+PE
    // liquidity). False when this leg's strike is its own — shift on this
    // leg's own option_type liquidity only, like every other strike mode.
    straddle_use_joint: bool,
    // Read ONLY on the YEARLY (pinned) path. Weekly/monthly rollover applies
    // rollover_strike_mode in Python via _apply_fixed_rollover_strike, which is
    // entangled with segment resolution and a documented post-buffer ordering —
    // moving that is a separate slice. Under YEARLY that post-process cannot be
    // used (it would carry one strike across every year), so the epoch lives here.
    rollover_strike_mode: StrikeMode,
    /// This leg's OWN expiry is YEARLY. Only such a leg holds the pinned December
    /// contract; weekly/monthly legs in the same strategy keep trading their
    /// cadence contract, which is what makes a mixed basket (CE weekly + PE
    /// yearly) work. False on every non-yearly path, where it is never read.
    is_yearly: bool,
    /// Per-December-contract strike-gap schedule: (contract_year, strike_gap),
    /// mirroring engine_rust.py::_yearly_schedule_row. Empty for every leg
    /// without a schedule (all existing configs), where it is never read — so
    /// this is opt-in and byte-identical when absent.
    yearly_schedule: Vec<(i32, f64)>,
}

/// Per-leg strike carry policy. Both modes are the same mechanism — resolve a
/// fresh strike at the start of an epoch, reuse it within — differing only in
/// what resets the epoch:
///   * Fresh: a new yearly cycle OR a new month
///   * Fixed: a new yearly cycle only
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum StrikeMode {
    Fresh,
    Fixed,
}

/// True when this entry must resolve a FRESH strike rather than carry the
/// epoch's. The reset trigger is PER LEG:
///
///   * FIXED (any leg)      — never, except at a new yearly cycle.
///   * FRESH, weekly/monthly leg — EVERY entry.
///   * FRESH, yearly leg    — first entry of each calendar MONTH.
///
/// The two Fresh rules look inconsistent but describe two verified sheets that
/// were previously in conflict, and both are right about their own leg:
///
///   * The short-dated leg tracks spot. NIFTY Oct-Nov 2022 (verified column):
///     17300 → 17100 → 17300 → 17500 → 17700 → 18000 WITHIN October alone.
///     One rule for both legs held it at 17300 for the whole month while ATM
///     ran to 17700, i.e. selling a 300-point ITM call nobody configured.
///   * The long-dated leg is month-wise. Mar-2019 sheet: 11000 held across
///     every weekly roll in March, re-struck on the first roll of April.
///     Re-striking it weekly would churn a contract that is deliberately
///     coarse (1000-point gap) and long-dated.
fn opens_new_epoch(
    mode: StrikeMode,
    prev_entry: &str,
    entry: &str,
    new_cycle: bool,
    leg_is_yearly: bool,
) -> bool {
    if new_cycle {
        return true;
    }
    if mode != StrikeMode::Fresh {
        return false; // FIXED holds its anchor for the whole cycle.
    }
    if leg_is_yearly {
        // The long-dated leg re-strikes MONTH-WISE: one strike per calendar
        // month, held across every roll inside it. `[0..7]` is the YYYY-MM
        // prefix — no date parsing needed.
        return entry.get(0..7) != prev_entry.get(0..7);
    }
    // A weekly/monthly leg re-strikes at EVERY entry.
    true
}

/// Sticky per-December-contract strike gap for a yearly leg. Mirrors
/// engine_rust.py::_yearly_schedule_row: the applicable row is the one with the
/// greatest contract-year <= the held contract's December year; None before the
/// first row (caller then keeps the leg's base gap — the opt-in / fallback
/// guarantee). `expiry` is the pinned December date (e.g. "2023-12-28").
fn yearly_gap(leg: &LegCfg, expiry: &str) -> Option<f64> {
    if leg.yearly_schedule.is_empty() {
        return None;
    }
    let held: i32 = expiry.get(0..4)?.parse().ok()?;
    let mut best: Option<(i32, f64)> = None;
    for &(yr, gap) in &leg.yearly_schedule {
        if yr <= held {
            match best {
                Some((by, _)) if by >= yr => {}
                _ => best = Some((yr, gap)),
            }
        }
    }
    best.map(|(_, g)| g)
}

fn extract_strike_sel(leg: &PyDict) -> Option<StrikeSel> {
    let sel_obj = leg.get_item("strike_selection").ok().flatten()?;
    let sel = sel_obj.downcast::<PyDict>().ok()?;
    let mode = sel
        .get_item("type").ok().flatten()
        .and_then(|v| v.extract::<String>().ok())
        .unwrap_or_else(|| "strike_type".to_string())
        .to_lowercase();
    let read_f64 = |k: &str| -> Option<f64> {
        sel.get_item(k).ok().flatten().and_then(|v| v.extract::<f64>().ok())
    };
    let read_str = |k: &str| -> Option<String> {
        sel.get_item(k).ok().flatten().and_then(|v| v.extract::<String>().ok())
    };
    match mode.as_str() {
        "strike_type" | "" => {
            let st = read_str("strike_type").unwrap_or_else(|| "ATM".to_string());
            Some(StrikeSel::Fixed(st.to_uppercase()))
        }
        // UI's compact form: {type: "ATM"} | {type: "ITM1"} | {type: "OTM2"} | …
        // Treat any token starting with ATM/ITM/OTM as a direct Fixed selector.
        // Long form: {type: "itm"/"otm", value: N} — the offset lives in `value`,
        // so fold it into the token (ITM5). Without this, `atm_offset_strike`
        // parses the empty suffix of a bare "ITM"/"OTM", returns None, and the
        // whole leg (hence every trade) drops silently — empty specs. The DTE /
        // per-leg-rollover Rust path is the only one that hits this branch with
        // the long form; the fixed-entry Python builder resolves it separately,
        // which is why a filtered run worked while the no-filter run returned 0.
        "atm" => Some(StrikeSel::Fixed("ATM".to_string())),
        m if m == "itm" || m == "otm" => {
            let n = read_f64("value").unwrap_or(0.0).round() as i64;
            Some(StrikeSel::Fixed(format!("{}{}", mode.to_uppercase(), n)))
        }
        m if m.starts_with("atm") || m.starts_with("itm") || m.starts_with("otm") => {
            Some(StrikeSel::Fixed(mode.to_uppercase()))
        }
        "rel_leg" => {
            let ref_leg = sel
                .get_item("ref_leg").ok().flatten()
                .and_then(|v| v.extract::<i64>().ok())
                .unwrap_or(0);
            Some(StrikeSel::RelToLeg {
                ref_leg,
                offset: read_f64("offset").unwrap_or(0.0),
            })
        }
        "pct_of_atm" => Some(StrikeSel::PctOfAtm {
            value: read_f64("value").unwrap_or(0.0),
            // Default direction is empty (sign convention).  Previously this
            // defaulted to "OTM" which discards the sign of `value` and always
            // places strikes above spot for CE (or below for PE), making the
            // negative half of an optimizer sweep collapse onto the positive
            // half.  Empty direction lets the signed-offset branch run so
            // negative values produce ITM strikes for CE / OTM for PE, and
            // positive values produce the opposite — matching trader intent.
            direction: read_str("direction").unwrap_or_default(),
        }),
        "atm_straddle_prem_pct" => {
            Some(StrikeSel::AtmStraddlePremPct(read_f64("value").unwrap_or(0.0)))
        }
        "straddle_width" => Some(StrikeSel::StraddleWidth {
            multiplier: read_f64("straddle_multiplier").unwrap_or(0.5),
            direction: read_str("straddle_direction").unwrap_or_else(|| "+".to_string()),
        }),
        "closest_premium" => Some(StrikeSel::ClosestPremium(read_f64("premium").unwrap_or(0.0))),
        "premium_gte" => Some(StrikeSel::PremiumGte(read_f64("premium").unwrap_or(0.0))),
        "premium_lte" => Some(StrikeSel::PremiumLte(read_f64("premium").unwrap_or(0.0))),
        "premium_range" => Some(StrikeSel::PremiumRange {
            lower: read_f64("lower").unwrap_or(0.0),
            upper: read_f64("upper").unwrap_or(0.0),
        }),
        // Target reads `time_value`, falling back to `premium` — the UI reuses the
        // existing premium input box, so no new payload field is introduced.
        "time_value" | "time_value_gte" | "time_value_lte" => Some(StrikeSel::TimeValue {
            target: read_f64("time_value").or_else(|| read_f64("premium")).unwrap_or(0.0),
            cmp: match mode.as_str() {
                "time_value_gte" => TvCmp::Gte,
                "time_value_lte" => TvCmp::Lte,
                _ => TvCmp::Nearest,
            },
            side: match read_str("moneyness").unwrap_or_default().to_uppercase().as_str() {
                "OTM" => TvSide::Otm,
                "ITM" => TvSide::Itm,
                _ => TvSide::Any,
            },
            // Range cap: |strike/entry_spot - 1| in percent. 0 = uncapped.
            range_pct: read_f64("tv_range_pct").unwrap_or(0.0).abs(),
            // Target unit: "points" (default) or "percent" -> TV/entry_spot*100.
            pct_units: matches!(
                read_str("tv_units").unwrap_or_default().to_lowercase().as_str(),
                "percent" | "pct" | "%"
            ),
        }),
        "delta" => Some(StrikeSel::Delta(read_f64("delta").unwrap_or(0.30))),
        _ => None,
    }
}

fn extract_leg_cfgs(payload: &PyDict) -> PyResult<(Vec<LegCfg>, Option<UnsupportedReason>)> {
    let mut out: Vec<LegCfg> = Vec::new();
    let legs_obj = payload.get_item("legs").ok().flatten();
    let legs = match legs_obj {
        Some(v) => v.downcast::<PyList>()?,
        None => return Ok((out, None)),
    };
    for item in legs.iter() {
        let leg = item.downcast::<PyDict>()?;

        // Reject features we don't yet support. The Python engine handles them.
        // The list is kept in sync with the engine's per-leg config keys.
        // SL / Target / Trail SL are slice 4 — Python wrapper calls the
        // existing check_leg_stop_loss_target. SL-with-Buffer is slice 4b —
        // Python wrapper calls apply_sl_with_buffer_batch in this module.
        // simpleMomentum: not implemented in any engine (Python or Rust) — ignore safely.
        // rollover_strike_mode='fixed': handled by _apply_fixed_rollover_strike (Slice 9b) — no blocker.

        // Slice 6: re-entry. RE_ASAP and RE_ASAP_REV modes are orchestrated in
        // Python (engine_rust.py) using Rust for the inner pricing + SL check calls.
        // Reject any other mode (RE_MOMENTUM, etc.) — the orchestrator doesn't handle them.
        for key in ["reEntryOnSL", "reEntryOnTarget"] {
            if let Ok(Some(v)) = leg.get_item(key) {
                if let Ok(d) = v.downcast::<PyDict>() {
                    let mode_str = d.get_item("mode").ok().flatten()
                        .and_then(|m| m.extract::<String>().ok())
                        .unwrap_or_default()
                        .to_uppercase();
                    // RE_ASAP, RE_ASAP_REV, LAZY_LEG, RE_MOMENTUM, and RE_MOMENTUM_REV
                    // are all handled by the Python orchestrator in engine_rust.py.
                    // Rust only builds the base schedule; re-entry is post-processing.
                    if !mode_str.is_empty()
                        && mode_str != "RE_ASAP"
                        && mode_str != "RE_ASAP_REV"
                        && mode_str != "LAZY_LEG"
                        && mode_str != "RE_MOMENTUM"
                        && mode_str != "RE_MOMENTUM_REV"
                    {
                        return Ok((vec![], Some(UnsupportedReason(format!(
                            "leg has '{}' with mode '{}' — unsupported re-entry mode in Rust path", key, mode_str
                        )))));
                    }
                    // LAZY_LEG and RE_MOMENTUM handled in Python engine_rust.py.
                }
            }
        }

        // Futures legs use a totally different code path.
        let segment = leg
            .get_item("segment").ok().flatten()
            .and_then(|v| v.extract::<String>().ok())
            .unwrap_or_default()
            .to_uppercase();
        if segment == "FUTURES" {
            return Ok((vec![], Some(UnsupportedReason(
                "futures legs not yet supported in Rust path".to_string()
            ))));
        }

        let strike = match extract_strike_sel(leg) {
            Some(s) => s,
            None => return Ok((vec![], Some(UnsupportedReason(
                "unknown strike_selection.type".to_string()
            )))),
        };

        let strike_interval = leg
            .get_item("strike_interval").ok().flatten()
            .and_then(|v| v.extract::<f64>().ok())
            .unwrap_or(50.0);
        // Set by run_rust_engine_pipeline (Python) on payload["legs"] before
        // this same payload reaches resolve_trade_specs — true only when a
        // sibling straddle_width leg shares this leg's multiplier+direction.
        let straddle_use_joint = leg
            .get_item("_straddle_use_joint_shift").ok().flatten()
            .and_then(|v| v.extract::<bool>().ok())
            .unwrap_or(false);

        // Per-December-contract schedule: parse (contract_year, strike_gap) pairs
        // so a yearly leg re-strikes on the scheduled gap for each December it
        // holds. `contract` arrives as a "YYYY" string (frontend) or an int.
        let yearly_schedule: Vec<(i32, f64)> = leg
            .get_item("yearly_contract_schedule").ok().flatten()
            .and_then(|v| v.downcast::<PyList>().ok().map(|lst| {
                lst.iter().filter_map(|row| {
                    let d = row.downcast::<PyDict>().ok()?;
                    let yr = d.get_item("contract").ok().flatten().and_then(|c| {
                        c.extract::<String>().ok()
                            .and_then(|s| s.get(0..4).and_then(|y| y.parse::<i32>().ok()))
                            .or_else(|| c.extract::<i64>().ok().map(|n| n as i32))
                    })?;
                    let gap = d.get_item("strike_gap").ok().flatten().and_then(|g| {
                        g.extract::<f64>().ok()
                            .or_else(|| g.extract::<i64>().ok().map(|n| n as f64))
                    })?;
                    if gap > 0.0 { Some((yr, gap)) } else { None }
                }).collect()
            }))
            .unwrap_or_default();

        out.push(LegCfg {
            option_type: leg
                .get_item("option_type").ok().flatten()
                .and_then(|v| v.extract::<String>().ok())
                .unwrap_or_else(|| "CE".to_string()),
            position: leg
                .get_item("position").ok().flatten()
                .and_then(|v| v.extract::<String>().ok())
                .unwrap_or_else(|| "SELL".to_string()),
            lots: leg
                .get_item("lots").ok().flatten()
                .and_then(|v| v.extract::<i64>().ok())
                .unwrap_or(1),
            strike_interval,
            strike,
            straddle_use_joint,
            is_yearly: leg
                .get_item("expiry").ok().flatten()
                .and_then(|v| v.extract::<String>().ok())
                .map(|e| e.eq_ignore_ascii_case("YEARLY"))
                .unwrap_or(false),
            rollover_strike_mode: match leg
                .get_item("rollover_strike_mode").ok().flatten()
                .and_then(|v| v.extract::<String>().ok())
                .unwrap_or_default()
                .to_ascii_lowercase()
                .as_str()
            {
                "fixed" => StrikeMode::Fixed,
                _ => StrikeMode::Fresh, // matches the Python default
            },
            yearly_schedule,
        });
    }
    Ok((out, None))
}

/// Strategy-level feature gate. Returns Some(reason) to block payloads whose
/// top-level fields are not yet handled. Leg-level checks are in extract_leg_cfgs.
/// Currently no top-level blockers remain — all features handled or delegated to
/// Python post-processing steps in engine_rust.py.
fn check_strategy_blockers(_payload: &PyDict) -> Option<UnsupportedReason> {
    // All top-level strategy features are now handled by either:
    //   • the Rust resolve_trade_specs loop directly, or
    //   • Python post-processing in engine_rust.py.
    // Individual leg-level blockers live in extract_leg_cfgs.
    None
}

/// Trading-day gap from `from_date` (exclusive) to `to_date` (inclusive),
/// matching the Python engine's `idx_target - idx_entry` with side='right'
/// searchsorted (generic_algotest_engine.py:3696-3704, 3977-3979).
///
/// For `from = Mon Jan 6, to = Thu Jan 9` (both trading days): gap = 3.
/// For `from = to`: gap = 0.
fn trading_day_gap(from_date: &str, to_date: &str, trading_days: &[String]) -> u32 {
    // side='right' equivalent: partition_point(|d| d <= target)
    let idx_from = trading_days.partition_point(|d| d.as_str() <= from_date);
    let idx_to   = trading_days.partition_point(|d| d.as_str() <= to_date);
    if idx_to > idx_from { (idx_to - idx_from) as u32 } else { 0 }
}

/// One YEARLY cycle: `contract` is the pinned December expiry, held by every
/// cadence segment whose CHAINED entry falls in the half-open window
/// [`start`, `end`). `end` is the T-n exit (T=0 ⇒ `end == contract`).
///
/// Cycle N's `start` is cycle N-1's `end`, so the yearly roll (exit + same-day
/// fresh re-entry on the next December) falls out of the existing same-day
/// chain with no special case.
#[derive(Clone, Debug)]
pub(crate) struct YearlyCycle {
    pub(crate) contract: String,
    pub(crate) start: String,
    pub(crate) end: String,
}

/// The December contract a segment ending on `exit_date` should hold: the FIRST
/// cycle whose T-n boundary (`end`) is at or after that exit.
///
/// T-n is a THRESHOLD, not an exact exit date. A segment must be holdable for
/// its WHOLE cadence period without breaching T-n, so if the current December
/// would force a mid-segment exit the segment opens on the NEXT December
/// instead. The yearly roll therefore always lands on a real cadence boundary
/// (a monthly/weekly expiry) and never produces a 1-day stub.
///
/// Keying on the EXIT (not the entry) is the whole trick: at the 26-Nov monthly
/// roll the schedule looks ahead to the 31-Dec exit, sees Dec-2020's T-1
/// boundary (27-Nov) falls before it, and opens on Dec-2021 right there.
fn cycle_for_exit<'a>(cycles: &'a [YearlyCycle], exit_date: &str) -> Option<&'a YearlyCycle> {
    cycles.iter().find(|c| c.end.as_str() >= exit_date)
}

/// Build a rollover trade schedule mirroring the Python engine's behavior when
/// `rollover_toggle = True` and `expiry_type in ('WEEKLY','MONTHLY','YEARLY')`.
///
/// Returns a Vec of (trade_id, entry_date, exit_date, leg_expiry,
/// original_expiry, new_cycle) where:
///   * trade_id: 1-based sequence
///   * entry_date: same-day chain from previous trade's SCHEDULED exit (not extended)
///   * exit_date: original scheduled exit, possibly extended to next_expiry when
///                trading-day gap (entry → original_expiry) ≤ rollover_min_days
///   * leg_expiry: the CONTRACT — cur_expiry (advanced to next_expiry when
///                 min-DTE triggers), or the pinned December when `cycles` is set
///   * original_expiry: the cur_expiry from the schedule (used as the chain anchor)
///   * new_cycle: this entry opens a new contract cycle (YEARLY only; see below)
///
/// Python references:
///   - Schedule construction:   engines/generic_algotest_engine.py:3441-3502
///   - Same-day chain:           engines/generic_algotest_engine.py:3841-3902
///   - Trade-level min-DTE:      engines/generic_algotest_engine.py:3971-3991
///   - Per-leg contract advance: engines/generic_algotest_engine.py:4350-4366
///
/// Cadence-decoupled: `expiry_dates` is always the CADENCE list (weekly/monthly
/// expiries) — its meaning is unchanged. `cycles` pins the CONTRACT:
///   * `None`  ⇒ legacy: the contract IS the cadence element (`cur_exp`).
///   * `Some`  ⇒ YEARLY: the contract is the December expiry of the cycle that
///               owns the chained entry; the cadence only drives entry/exit.
///
/// The 6th tuple element is `new_cycle` — "this entry opens a new contract
/// cycle". It must NOT be derived from "leg_expiry changed": with min-DTE
/// active, trade N extends to `next_exp` and trade N+1's `cur_exp` IS that same
/// `next_exp`, so `leg_expiry` repeats across two consecutive trades. It is
/// never read on the unpinned path.
fn build_rollover_schedule_pinned(
    expiry_dates: &[String],
    trading_days: &[String],
    entry_dte: u32,
    exit_dte: u32,
    rollover_min_days: u32,
    cycles: Option<&[YearlyCycle]>,
    calendar_days: bool,
) -> Vec<(i64, String, String, String, String, bool)> {
    let mut out: Vec<(i64, String, String, String, String, bool)> = Vec::new();
    if expiry_dates.is_empty() || trading_days.is_empty() {
        return out;
    }

    // Step 1: build initial schedule records (entry/exit relative to current expiry).
    // expiry_dates must be sorted ascending; the caller passes them sorted.
    let mut sched: Vec<(String, String, String, String)> = Vec::new(); // (entry, exit, cur_exp, next_exp)
    for (i, cur_exp) in expiry_dates.iter().enumerate() {
        let entry = match day_before_expiry(cur_exp, entry_dte, trading_days, calendar_days) {
            Some(v) => v,
            None => continue,
        };
        let scheduled_exit = match day_before_expiry(cur_exp, exit_dte, trading_days, calendar_days) {
            Some(v) => v,
            None => continue,
        };
        let next_exp = if i + 1 < expiry_dates.len() {
            expiry_dates[i + 1].clone()
        } else {
            cur_exp.clone()
        };
        sched.push((entry, scheduled_exit, cur_exp.clone(), next_exp));
    }

    // Step 2: walk records, applying same-day chain (entry N = prev scheduled exit).
    // Trade 1 keeps its scheduled entry (no prior anchor).
    let mut trade_id: i64 = 0;
    let mut prev_scheduled_exit: Option<String> = None;
    let mut prev_contract: Option<String> = None;
    for (sched_entry, sched_exit_c, cur_exp, next_exp) in &sched {
        let actual_entry = match prev_scheduled_exit.as_ref() {
            Some(prev) => prev.clone(),
            None => sched_entry.clone(),
        };

        // ── PIN (YEARLY only) ────────────────────────────────────────────────
        // When `cycles` is None `contract == cur_exp`, so the unpinned path is
        // value-identical to the pre-change code by construction.
        //
        // Keys on the segment's EXIT, not its entry: T-n is a threshold, so a
        // segment opens on whichever December it can hold for its WHOLE cadence
        // period. That makes the yearly roll land on a real cadence boundary and
        // removes the need to truncate (and therefore to split records).
        let sched_exit = sched_exit_c.clone();
        let mut contract = cur_exp.clone();
        if let Some(cycles) = cycles {
            match cycle_for_exit(cycles, &sched_exit) {
                Some(c) => contract = c.contract.clone(),
                None => {
                    // Past the last December we can pin — stop rather than
                    // silently fall back to the cadence contract.
                    prev_scheduled_exit = Some(sched_exit);
                    continue;
                }
            }
        }

        // Skip 0-day trades (entry >= exit). For T-0/T-0 first record this is
        // common; the chain takes over from the next record. Python: 3886-3890.
        // Seed the chain anchor even if we don't emit, so the chain continues.
        if actual_entry >= sched_exit {
            prev_scheduled_exit = Some(sched_exit);
            continue;
        }

        // Step 3: apply min-DTE extension to determine actual exit + leg expiry.
        // Trading-day gap from actual_entry to cur_exp (the original target).
        //
        // Pinned ⇒ no-op. Under a pin this would set leg_expiry = next_exp, i.e.
        // advance the contract to the next CADENCE element — swapping the
        // Dec-2020 contract for a Jan-2020 weekly. Its predicate also measures
        // the distance to the cadence date, not to the pinned contract (which
        // has ~1y DTE for nearly every segment), so the condition it guards
        // cannot arise. YEARLY + rollover_min_days is rejected upstream rather
        // than silently ignored here.
        let mut actual_exit = sched_exit.clone();
        let mut leg_expiry = contract.clone();
        if rollover_min_days > 0 && cycles.is_none() {
            let gap = trading_day_gap(&actual_entry, cur_exp, trading_days);
            if gap <= rollover_min_days {
                actual_exit = next_exp.clone();
                leg_expiry = next_exp.clone();
            }
        }

        let new_cycle = prev_contract.as_deref() != Some(contract.as_str());
        trade_id += 1;
        out.push((
            trade_id,
            actual_entry,
            actual_exit,
            leg_expiry,
            cur_exp.clone(),
            new_cycle,
        ));
        // Chain uses SCHEDULED exit (pre-extension) per Python engine semantics.
        prev_scheduled_exit = Some(sched_exit);
        prev_contract = Some(contract);
    }

    out
}

/// One leg's slot within a per-leg-rollover trade row (see
/// `build_rollover_schedule_per_leg`).
#[allow(dead_code)]
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct PerLegSlot {
    /// Contract this leg holds for this row — the pinned December for a YEARLY
    /// leg, otherwise its own cadence expiry.
    contract: String,
    /// True when this row's start IS this leg's own roll boundary → the leg
    /// re-picks (subject to its Fresh/Fixed epoch rule). False ⇒ the leg carries
    /// its epoch strike, marked-to-market over this sub-segment.
    own_boundary: bool,
    /// New yearly cycle for this leg — only meaningful together with
    /// `own_boundary`.
    new_cycle: bool,
    /// PER-LEG SPOT-ADJUSTMENT (Phase 4): this row's START is one of THIS leg's
    /// breach dates ⇒ force a FRESH re-strike here regardless of the epoch rule.
    /// False on every non-spot-adj leg ⇒ the No-Adjustment path is unchanged.
    breach_spot_adj: bool,
    /// FILTER SEGMENT START: this row's START is a filter segment open that fell
    /// MID-HOLD (not on any leg's own roll). The non-per-leg path enters the
    /// active contract FRESH at the segment start; per-leg must mirror that, so
    /// force a fresh ATM re-strike here even though it is not this leg's own
    /// boundary. Empty seg_starts (no filter) ⇒ always false ⇒ path unchanged.
    seg_start_entry: bool,
}

/// PER-LEG ROLLOVER scheduler — the N-cadence generalization of
/// `build_rollover_schedule_pinned`. Each leg rolls on its OWN expiry cadence +
/// OWN exit T-n; trade-row boundaries are the sorted UNION of every leg's roll
/// dates. For each row, each leg is tagged with the contract it holds and whether
/// this row's start is that leg's own boundary (roll) or not (carry).
///
/// Semantics locked with the user (worked Feb–Mar 2026 example):
///   * Every leg is HELD from `run_start`; its first pick happens at run_start.
///   * Thereafter a leg rolls only at its OWN exit T-n (`trading_day_before(exp,
///     exit_dte)`). Entry is always the previous roll — so per-leg ENTRY T-n is
///     moot under rollover and only per-leg EXIT T-n is an input.
///   * A leg mid-hold when ANOTHER leg rolls simply CARRIES (own_boundary=false),
///     exactly like a pinned yearly leg carries across weekly rows today.
///
/// Rows are emitted only while ALL legs are active (a complete multi-leg trade);
/// tail rows where a leg has run out of listed expiries are dropped, matching the
/// atomic-commit rule in `resolve_trade_specs_core`.
///
/// This function is pure and independently unit-tested; it is wired into the
/// resolve path behind the `per_leg_rollover` flag (OFF ⇒ never called).
#[allow(dead_code)]
fn build_rollover_schedule_per_leg(
    run_start: &str,
    leg_expiries: &[Vec<String>],
    leg_exit_dte: &[u32],
    leg_cycles: &[Option<Vec<YearlyCycle>>],
    leg_breach_dates: &[Vec<String>],
    seg_starts: &[String],
    trading_days: &[String],
) -> Vec<(i64, String, String, Vec<PerLegSlot>)> {
    let n = leg_expiries.len();
    let mut out: Vec<(i64, String, String, Vec<PerLegSlot>)> = Vec::new();
    if n == 0 || trading_days.is_empty() {
        return out;
    }

    // 1. Per-leg contiguous segments (entry, exit, contract, new_cycle), built
    //    directly from each leg's own exit T-n. The segment ending at exit_k
    //    trades the contract expiring at exp_k; the first segment holds the leg
    //    from run_start on its first tradeable contract.
    let mut leg_segs: Vec<Vec<(String, String, String, bool)>> = Vec::with_capacity(n);
    for i in 0..n {
        let mut exps = leg_expiries[i].clone();
        exps.sort();
        // (exit_date, contract) for each expiry whose exit falls after run_start.
        let mut exits: Vec<(String, String)> = Vec::new();
        for exp in &exps {
            let exit = match trading_day_before(exp, leg_exit_dte[i], trading_days) {
                Some(v) => v,
                None => continue,
            };
            if exit.as_str() <= run_start {
                continue;
            }
            let contract = match &leg_cycles[i] {
                Some(cyc) => match cycle_for_exit(cyc, &exit) {
                    Some(c) => c.contract.clone(),
                    None => continue, // past the last pinnable December
                },
                None => exp.clone(),
            };
            exits.push((exit, contract));
        }
        exits.sort();
        exits.dedup_by(|a, b| a.0 == b.0);

        // NOTE: the first segment [run_start, exit_1) is the FRONT-MONTH stub.
        // It is retained here so every leg stays ACTIVE from run_start — that
        // coverage is what makes a foreign leg's early rows "complete" in the
        // multi-leg union. It must NOT, however, be emitted as its own trade row
        // (see the run_start-row skip in the emission loop below): under rollover
        // no leg enters at an arbitrary run_start; the pinned same-day chain
        // 0-DTE-skips the front-month and enters on exit_1 holding the NEXT
        // contract. Emitting the stub as a trade invented an extra opening trade
        // that shifted every downstream row and broke single-leg per-leg == normal.
        let mut segs: Vec<(String, String, String, bool)> = Vec::new();
        let mut prev = run_start.to_string();
        let mut prev_contract: Option<String> = None;
        for (exit, contract) in exits {
            if exit.as_str() <= prev.as_str() {
                continue;
            }
            let new_cycle = prev_contract.as_deref() != Some(contract.as_str());
            segs.push((prev.clone(), exit.clone(), contract.clone(), new_cycle));
            prev = exit;
            prev_contract = Some(contract);
        }
        leg_segs.push(segs);
    }

    // 2. Union of all segment start dates = candidate row boundaries, plus a
    //    terminal boundary (the latest last-exit) so the final row has an end.
    let mut bounds: Vec<String> = leg_segs
        .iter()
        .flat_map(|s| s.iter().map(|seg| seg.0.clone()))
        .collect();
    if let Some(term) = leg_segs
        .iter()
        .filter_map(|s| s.last().map(|seg| seg.1.clone()))
        .max()
    {
        bounds.push(term);
    }
    // PER-LEG SPOT-ADJUSTMENT: each leg's breach dates become extra union
    // boundaries (a breach on leg A starts a new row; other legs carry there).
    // Only keep breaches strictly inside (run_start, term); a breach at or past
    // the terminal boundary has no row to open. Empty for every non-spot-adj leg
    // ⇒ these merges are inert on the No-Adjustment path.
    for bd in leg_breach_dates {
        for d in bd {
            if d.as_str() > run_start {
                bounds.push(d.clone());
            }
        }
    }
    // FILTER SEGMENT STARTS: each segment open becomes an extra union boundary so
    // a row starts ON the segment open holding whatever contract is active there
    // — mirroring the non-per-leg path, which enters the active contract fresh at
    // the segment start. Only keep starts strictly inside (run_start, term); one
    // at/before run_start (the run's own first day) or at/after the terminal
    // boundary opens no coverable row. A start that already coincides with a leg
    // roll/breach dedups away and stays that leg's OWN boundary. Empty (no filter)
    // ⇒ inert. Callers pass starts already snapped to trading days.
    for d in seg_starts {
        if d.as_str() > run_start {
            bounds.push(d.clone());
        }
    }
    bounds.sort();
    bounds.dedup();
    if bounds.len() < 2 {
        return out;
    }
    // O(1) "does this row START on a filter segment open" lookup. A leg whose own
    // segment covers this row but does NOT own this boundary must still re-strike
    // fresh here (the position is re-entered at the filter open), so this flag
    // forces Fresh in the resolver just like a breach does.
    let seg_start_set: std::collections::HashSet<&str> =
        seg_starts.iter().map(|s| s.as_str()).collect();

    // Per-leg breach sets for O(1) "does this row START breach for leg i" lookup.
    let breach_sets: Vec<std::collections::HashSet<&str>> = (0..n)
        .map(|i| {
            leg_breach_dates
                .get(i)
                .map(|v| v.iter().map(|s| s.as_str()).collect())
                .unwrap_or_default()
        })
        .collect();

    // 3. Emit a row per [bound, next_bound) where ALL legs are active.
    let mut trade_id: i64 = 0;
    for w in bounds.windows(2) {
        let (start, end) = (&w[0], &w[1]);
        // Skip the FRONT-MONTH opening window [run_start, first_bound). No leg
        // enters at run_start under rollover (the pinned same-day chain 0-DTE-
        // skips the front-month and enters on the first roll instead); emitting
        // it invented a spurious opening trade and shifted every downstream row,
        // breaking single-leg per-leg == normal. The front segment is still in
        // `leg_segs` so every leg stays ACTIVE from run_start — that only affects
        // COVERAGE (foreign legs' early rows), never adds a run_start trade.
        // Breach dates are always > run_start (see the merge above), so no
        // spot-adj boundary is dropped here.
        if start.as_str() == run_start {
            continue;
        }
        let mut slots: Vec<PerLegSlot> = Vec::with_capacity(n);
        let mut complete = true;
        for (li, segs) in leg_segs.iter().enumerate() {
            match segs
                .iter()
                .find(|(e, x, _, _)| e.as_str() <= start.as_str() && start.as_str() < x.as_str())
            {
                Some((e, _x, contract, nc)) => {
                    let own = e.as_str() == start.as_str();
                    slots.push(PerLegSlot {
                        contract: contract.clone(),
                        own_boundary: own,
                        new_cycle: *nc && own,
                        breach_spot_adj: breach_sets[li].contains(start.as_str()),
                        // A segment-open boundary that is NOT this leg's own roll
                        // still re-enters the active contract fresh (mirrors the
                        // non-per-leg fixed-entry segment start). When it DOES
                        // coincide with the leg's own roll, own_boundary already
                        // drives the (Fresh/Fixed) re-strike, so only flag the
                        // mid-hold case to avoid double semantics.
                        seg_start_entry: !own && seg_start_set.contains(start.as_str()),
                    });
                }
                None => {
                    complete = false;
                    break;
                }
            }
        }
        if complete {
            trade_id += 1;
            out.push((trade_id, start.clone(), end.clone(), slots));
        }
    }
    out
}

/// Closest-premium picker matching the Python engine's tie-breaking.
///
/// Primary key: |premium - target| (closer wins)
/// Tie-break 1: |strike - atm_strike| (closer to ATM wins)
/// Tie-break 2: for CE prefer HIGHER strike, for PE prefer LOWER strike
fn pick_by_premium<'a>(
    candidates: &'a [(f64, f64)],
    target: f64,
    atm: f64,
    is_call: bool,
) -> Option<&'a (f64, f64)> {
    candidates.iter().min_by(|a, b| {
        let da = (a.1 - target).abs();
        let db = (b.1 - target).abs();
        da.partial_cmp(&db)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                let sa = (a.0 - atm).abs();
                let sb = (b.0 - atm).abs();
                sa.partial_cmp(&sb).unwrap_or(std::cmp::Ordering::Equal)
            })
            .then_with(|| {
                if is_call {
                    b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal)
                } else {
                    a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal)
                }
            })
    })
}

/// Step size the liquidity-shift walk uses to find a tradeable strike.
///
/// For the legacy 25/50/100 gaps the walk steps by the gap itself, so existing
/// tradesheets stay byte-identical. A COARSE gap (500) would jump a whole
/// 500-pt gap per step and skip every liquid listed strike in between, so the
/// walk instead uses a finer per-index step: NIFTY→100, MIDCPNIFTY→50 (others
/// default to their native liquid grid, 100). The 500 gap still governs ATM
/// snap and offset stepping — only the liquidity search is fine-grained.
/// Returns `(walk_step, is_coarse)`.
fn liquidity_walk_step(index: &str, interval: f64) -> (f64, bool) {
    if interval <= 100.0 {
        return (interval, false);
    }
    let step = match index.to_ascii_uppercase().as_str() {
        "NIFTY" => 100.0,
        "MIDCPNIFTY" | "FINNIFTY" => 50.0,
        "BANKNIFTY" | "SENSEX" | "BANKEX" => 100.0,
        _ => 100.0,
    };
    (step, true)
}

/// Joint CE+PE liquidity validation/shift — used ONLY for `straddle_width`
/// legs, which share the same requested strike across CE and PE by design
/// (see `StrikeSel::StraddleWidth`). If either side is illiquid there, BOTH
/// legs must shift TOGETHER to the first strike where CE AND PE are
/// simultaneously tradeable — never leaving one leg alone at the original
/// strike while the other moves elsewhere. Mirrors `validate_or_shift_strike`
/// but the acceptance test is joint rather than per-option-type.
fn validate_or_shift_straddle_strike(
    strike: f64,
    atm: f64,
    interval: f64,
    entry_date: &str,
    expiry: &str,
    index: &str,
) -> Option<(f64, i32)> {
    use crate::OptionDataStatus;
    // Coarse gaps (500) walk by a finer per-index step so BOTH legs land on a
    // liquid listed strike instead of jumping a whole 500-pt gap. Legacy gaps:
    // walk_step == interval (unchanged). Shadow `interval` so every walk below
    // uses the fine step; the initial joint check does not use `interval`.
    let (walk_step, _coarse) = liquidity_walk_step(index, interval);
    let interval = walk_step;
    let is_tradeable = |s: f64, opt_type: &str| -> bool {
        matches!(
            crate::lookup_option_status(entry_date, index, s, opt_type, expiry),
            OptionDataStatus::Tradeable(px) if px > 0.0
        )
    };
    let is_joint_tradeable = |s: f64| -> bool { is_tradeable(s, "CE") && is_tradeable(s, "PE") };

    if is_joint_tradeable(strike) {
        return Some((strike, 0));
    }

    let direction: f64 = if strike > atm + 1e-6 {
        -1.0
    } else if strike < atm - 1e-6 {
        1.0
    } else {
        // Requested strike IS atm and jointly illiquid — no single CE/PE-
        // favored direction applies to both legs, so walk outward alternating
        // +/- until a jointly-liquid strike is found.
        let mut step = 1i32;
        while step <= 500 {
            for candidate in [strike + (step as f64) * interval, strike - (step as f64) * interval] {
                if candidate > 0.0 && is_joint_tradeable(candidate) {
                    return Some((candidate, step));
                }
            }
            step += 1;
        }
        return None;
    };

    let dist = ((strike - atm).abs() / interval).round() as i32;
    let max_walk = dist.max(1);
    for step in 1..=max_walk {
        let candidate = strike + direction * (step as f64) * interval;
        if candidate <= 0.0 {
            break;
        }
        if is_joint_tradeable(candidate) {
            return Some((candidate, step));
        }
    }
    None
}

/// Validate that `strike` has a tradeable contract on `entry_date` for the
/// given (expiry, opt_type).  If the contract has zero turnover (stale close
/// price), walk TOWARD ATM by `strike_interval` until a tradeable strike is
/// found or ATM is reached.  Returns `(final_strike, shift_steps)` where
/// shift_steps is 0 when the original strike was already tradeable.  Returns
/// None if no tradeable strike exists within the walk window (rare).
///
/// Shift direction (toward ATM — more liquid strikes):
///   - ITM CE (strike < ATM): walk UP toward ATM
///   - OTM CE (strike > ATM): walk DOWN toward ATM
///   - ITM PE (strike > ATM): walk DOWN toward ATM
///   - OTM PE (strike < ATM): walk UP toward ATM
///   - ATM (strike == ATM): no shift possible (already at ATM)
fn validate_or_shift_strike(
    strike: f64,
    atm: f64,
    interval: f64,
    is_call: bool,
    entry_date: &str,
    expiry: &str,
    index: &str,
    opt_type: &str,
    _max_shifts: i32, // retained for API compat; cap is now distance-to-ATM
) -> Option<(f64, i32)> {
    use crate::OptionDataStatus;
    // Coarse gaps (500) walk by a finer per-index step so the search lands on a
    // liquid listed strike instead of jumping a whole 500-pt gap. Legacy gaps:
    // walk_step == interval (unchanged). Shadow `interval` so the direction cap
    // and both walk loops below use the fine step; the status check does not.
    let (walk_step, _coarse) = liquidity_walk_step(index, interval);
    let interval = walk_step;
    // First check the requested strike's data status.
    let status = crate::lookup_option_status(entry_date, index, strike, opt_type, expiry);
    match status {
        OptionDataStatus::Tradeable(px) if px > 0.0 => {
            return Some((strike, 0)); // real price, no shift
        }
        // Any other status is "no liquidity" and shifts toward a tradeable
        // strike, for EVERY gap (25/50/100/500) and every strike-selection mode:
        //   · ZeroContracts        — record exists but zero turnover (stale px)
        //   · Missing              — strike not listed by NSE that day
        //   · Tradeable(px <= 0.0) — listed but no usable price
        // The walk below finds the nearest tradeable strike toward ATM (or
        // outward in the OTM direction when the ATM strike itself is dead).
        _ => {}
    }
    // Walk TOWARD ATM. Direction is opposite of (requested - atm).
    let direction: f64 = if strike > atm + 1e-6 {
        -1.0 // requested is above ATM → walk DOWN toward ATM
    } else if strike < atm - 1e-6 {
        1.0  // requested is below ATM → walk UP toward ATM
    } else {
        // ATM strike itself is zero-turnover. Walk OUTWARD in the OTM direction
        // (CALL: up, PUT: down) to the first strike WITH turnover, instead of
        // skipping. Stop at the chain edge (Missing) or a safety cap.
        let otm_dir: f64 = if is_call { 1.0 } else { -1.0 };
        let mut step = 1i32;
        while step <= 500 {
            let candidate = strike + otm_dir * (step as f64) * interval;
            if candidate <= 0.0 {
                break;
            }
            match crate::lookup_option_status(entry_date, index, candidate, opt_type, expiry) {
                OptionDataStatus::Tradeable(px) if px > 0.0 => return Some((candidate, step)),
                OptionDataStatus::Missing => break,
                _ => {}
            }
            step += 1;
        }
        return None;
    };
    // Cap at distance to ATM (inclusive) — never walk past ATM.
    let dist = ((strike - atm).abs() / interval).round() as i32;
    let max_walk = dist.max(1);
    for step in 1..=max_walk {
        let candidate = strike + direction * (step as f64) * interval;
        if candidate <= 0.0 {
            break;
        }
        if let OptionDataStatus::Tradeable(px) = crate::lookup_option_status(entry_date, index, candidate, opt_type, expiry) {
            if px > 0.0 {
                return Some((candidate, step));
            }
        }
    }
    None
}

/// Re-validate a CARRIED (Fixed-mode held) strike on a break/foreign-boundary
/// re-entry. A held strike that is merely `ZeroContracts` — LISTED and priced,
/// just no turnover that one session (e.g. a deep-OTM yearly hedge on a thin
/// day) — must be HELD and marked at its close, NOT shifted toward ATM: shifting
/// silently drifts a Fixed hedge permanently off its strike. Only a genuinely
/// `Missing` (unlisted) carried strike walks, which is the case the carry
/// re-validation was written for. Fresh strike SELECTION never comes here, so
/// the universal liquidity shift on entry/roll/spot-adj is unchanged.
fn revalidate_carried_strike(
    carried: f64,
    atm: f64,
    interval: f64,
    is_call: bool,
    entry_date: &str,
    expiry: &str,
    index: &str,
    opt_type: &str,
    max_shifts: i32,
) -> Option<(f64, i32)> {
    if let crate::OptionDataStatus::ZeroContracts =
        crate::lookup_option_status(entry_date, index, carried, opt_type, expiry)
    {
        return Some((carried, 0)); // priced-but-thin held strike → hold, don't shift
    }
    validate_or_shift_strike(
        carried, atm, interval, is_call, entry_date, expiry, index, opt_type, max_shifts,
    )
}

/// ATM CE + PE prices for straddle_width / atm_straddle_prem_pct, using the
/// TRADEABLE price (filters zero-turnover/stale closes) — a straddle price
/// built from a dead, untraded contract's stale close silently corrupts the
/// shift formula (seen on MIDCPNIFTY: a 0-contract PE's stale close of
/// 1223.85 vs a real ~330 nearby). If either side is illiquid at the leg's
/// own gap, widen the gap used ONLY to source a liquid price (gap, 2x, 3x,
/// 4x) — the leg's own strike gap for the formula/final snap is untouched.
/// Returns `(ce, pe, source_reason)` — source_reason is empty when the base
/// gap was already liquid. Mirrors
/// engine_rust.py::_compute_strike_for_leg_python / _atm_straddle_prices.
fn straddle_atm_prices(
    atm: f64,
    interval: f64,
    entry_spot: f64,
    entry_date: &str,
    expiry: &str,
    index: &str,
) -> Option<(f64, f64, String)> {
    if let (Some(c), Some(p)) = (
        lookup_option_price_tradeable(entry_date, index, atm, "CE", expiry),
        lookup_option_price_tradeable(entry_date, index, atm, "PE", expiry),
    ) {
        return Some((c, p, String::new()));
    }
    let missing_ce = lookup_option_price_tradeable(entry_date, index, atm, "CE", expiry).is_none();
    let missing_pe = lookup_option_price_tradeable(entry_date, index, atm, "PE", expiry).is_none();
    let missing = format!(
        "{}{}",
        if missing_ce { "CE" } else { "" },
        if missing_pe { "PE" } else { "" },
    );
    for mult_n in [2.0, 3.0, 4.0] {
        let w_gap = interval * mult_n;
        let w_atm = (entry_spot / w_gap).round() * w_gap;
        if let (Some(wc), Some(wp)) = (
            lookup_option_price_tradeable(entry_date, index, w_atm, "CE", expiry),
            lookup_option_price_tradeable(entry_date, index, w_atm, "PE", expiry),
        ) {
            let source = format!(
                "{}→{} (ATM {} zero turnover)",
                fmt_g(interval), fmt_g(w_gap), missing,
            );
            return Some((wc, wp, source));
        }
    }
    // All 4 fixed anchors are dead (base gap + 3 widened roundings, all still
    // clustered within a few gap-widths of spot). Do NOT give up here — walk
    // outward strike-by-strike, in BOTH directions from ATM, at the leg's own
    // gap, until a strike is found where CE AND PE are BOTH tradeable at once
    // (a straddle needs both sides at the SAME strike). This is the case that
    // motivated the fix: on 2020-03-25 the near-ATM strikes were all
    // zero-turnover, but real NSE volume existed ~700 points out (strike 9000
    // vs spot ~8318) — the fixed anchors above never look that far because
    // each is just a coarser rounding of the SAME spot, not an outward search.
    // Each direction walks independently until it hits the real chain edge
    // (Missing) or a generous safety cap — never stops early just because the
    // near side is thin, matching validate_or_shift_strike's own outward walk.
    use crate::OptionDataStatus;
    let mut up_alive = true;
    let mut down_alive = true;
    let mut step = 1i32;
    while step <= 2000 && (up_alive || down_alive) {
        for dir in [1.0f64, -1.0f64] {
            let alive = if dir > 0.0 { &mut up_alive } else { &mut down_alive };
            if !*alive {
                continue;
            }
            let candidate = atm + dir * (step as f64) * interval;
            if candidate <= 0.0 {
                *alive = false;
                continue;
            }
            let ce_status = crate::lookup_option_status(entry_date, index, candidate, "CE", expiry);
            let pe_status = crate::lookup_option_status(entry_date, index, candidate, "PE", expiry);
            if let (OptionDataStatus::Tradeable(cpx), OptionDataStatus::Tradeable(ppx)) =
                (&ce_status, &pe_status)
            {
                if *cpx > 0.0 && *ppx > 0.0 {
                    let source = format!(
                        "{}→walk{:+.0}pt (ATM {} zero turnover)",
                        fmt_g(interval), dir * (step as f64) * interval, missing,
                    );
                    return Some((*cpx, *ppx, source));
                }
            }
            // Chain edge: neither side is even listed any further out this way.
            if matches!(ce_status, OptionDataStatus::Missing)
                && matches!(pe_status, OptionDataStatus::Missing)
            {
                *alive = false;
            }
        }
        step += 1;
    }
    None
}

/// Compute the strike price for one leg on one entry date.
///
/// Falls back to None (caller skips the trade or falls back to Python) when
/// the entry_date has no usable option chain data for the requested mode.
/// Returns `(final_strike, requested_strike)` where requested_strike is the
/// strike computed from the user's selection BEFORE any zero-turnover shift.
/// If no shift was applied, both values are equal.
fn compute_strike_for_leg(
    leg: &LegCfg,
    entry_date: &str,
    expiry: &str,
    index: &str,
    entry_spot: f64,
    strike_shift_max: i32,
    resolved: &HashMap<i64, f64>,
) -> Option<(f64, f64)> {
    // Per-December-contract schedule: a yearly leg re-strikes on the schedule's
    // gap for the contract it holds (keyed by the spec's December year in
    // `expiry`), so every spot-adj / breach / roll re-strike stays on the
    // scheduled grid — the same rule the Python builders apply, now native so
    // OPTIMIZE_RUST_LOOP=1 matches. Absent schedule → keeps the leg's base gap.
    let leg_owned;
    let leg = if let Some(g) = yearly_gap(leg, expiry) {
        leg_owned = LegCfg { strike_interval: g, ..leg.clone() };
        &leg_owned
    } else {
        leg
    };
    let atm = (entry_spot / leg.strike_interval).round() * leg.strike_interval;
    let is_call = leg.option_type.eq_ignore_ascii_case("CE")
        || leg.option_type.eq_ignore_ascii_case("CALL")
        || leg.option_type.eq_ignore_ascii_case("C");

    let computed: Option<f64> = match &leg.strike {
        StrikeSel::Fixed(sel) => {
            atm_offset_strike(entry_spot, leg.strike_interval, sel, &leg.option_type)
        }
        StrikeSel::RelToLeg { ref_leg, offset } => {
            // Wing strike = parent leg's ALREADY-RESOLVED strike shifted by
            // `offset` gaps in the leg's OTM direction (+ for CALL / − for PUT),
            // matching the ITM/OTM convention. `resolved` holds the final
            // (post zero-turnover-shift) strike of every earlier leg in THIS
            // trade, keyed by 1-based leg number. Missing parent → skip leg.
            let parent = *resolved.get(ref_leg)?;
            let shift = offset * leg.strike_interval;
            Some(if is_call { parent + shift } else { parent - shift })
        }
        StrikeSel::PctOfAtm { value, direction } => {
            // Match Python exactly. Semantic directions (OTM/ITM) are moneyness
            // labels and use abs(value), while raw +/- keeps signed-offset
            // behavior for callers that intentionally sweep across zero.
            let dir = direction.trim();
            let dir_upper = dir.to_uppercase();
            let raw = if dir_upper == "OTM" || dir_upper == "ITM" || dir_upper == "ATM" {
                if dir_upper == "ATM" || *value == 0.0 {
                    entry_spot
                } else {
                    let shift = entry_spot * value.abs() / 100.0;
                    let above_spot = (dir_upper == "OTM" && is_call) || (dir_upper == "ITM" && !is_call);
                    if above_spot {
                        entry_spot + shift
                    } else {
                        entry_spot - shift
                    }
                }
            } else if dir == "-" {
                let shift = entry_spot * value / 100.0;
                entry_spot - shift
            } else {
                let shift = entry_spot * value / 100.0;
                entry_spot + shift
            };
            Some((raw / leg.strike_interval).round() * leg.strike_interval)
        }
        StrikeSel::ClosestPremium(target) => {
            let chain = lookup_strikes_for_date(entry_date, index, expiry, &leg.option_type)?;
            pick_by_premium(&chain, *target, atm, is_call).map(|(s, _)| *s)
        }
        StrikeSel::PremiumGte(target) => {
            let chain = lookup_strikes_for_date(entry_date, index, expiry, &leg.option_type)?;
            let qualifying: Vec<(f64, f64)> = chain
                .into_iter()
                .filter(|(_, p)| *p >= *target)
                .collect();
            if qualifying.is_empty() {
                return None;
            }
            pick_by_premium(&qualifying, *target, atm, is_call).map(|(s, _)| *s)
        }
        StrikeSel::PremiumLte(target) => {
            let chain = lookup_strikes_for_date(entry_date, index, expiry, &leg.option_type)?;
            let qualifying: Vec<(f64, f64)> = chain
                .into_iter()
                .filter(|(_, p)| *p <= *target)
                .collect();
            if qualifying.is_empty() {
                return None;
            }
            pick_by_premium(&qualifying, *target, atm, is_call).map(|(s, _)| *s)
        }
        StrikeSel::PremiumRange { lower, upper } => {
            // Mirrors base.calculate_strike_from_premium_range exactly:
            // pick the in-range strike whose premium is closest to the
            // UPPER bound (equivalently: highest premium in-range), with
            // ATM-distance and direction tie-breakers.
            let _ = lower;  // lower is only used to filter, not to pick
            let chain = lookup_strikes_for_date(entry_date, index, expiry, &leg.option_type)?;
            let qualifying: Vec<(f64, f64)> = chain
                .into_iter()
                .filter(|(_, p)| *p >= *lower && *p <= *upper)
                .collect();
            if qualifying.is_empty() {
                return None;
            }
            pick_by_premium(&qualifying, *upper, atm, is_call).map(|(s, _)| *s)
        }
        StrikeSel::TimeValue { target, cmp, side, range_pct, pct_units } => {
            // Python mirror: engine_rust.py::_compute_strike_for_leg_python,
            // sel_type in ("time_value", "time_value_gte", "time_value_lte").
            let chain = lookup_strikes_for_date(entry_date, index, expiry, &leg.option_type)?;
            // (1) leg's OWN strike gap — the listed chain is finer (50) than a
            // leg asking for 100, and ATM/ITMn/OTMn never see that because they
            // snap through round(spot/interval)*interval.
            let iv = leg.strike_interval;
            let on_grid: Vec<(f64, f64)> = if iv > 0.0 {
                chain.iter().copied()
                    .filter(|(s, _)| ((s / iv) - (s / iv).round()).abs() < 1e-9)
                    .collect()
            } else {
                Vec::new()
            };
            let source = if on_grid.is_empty() { chain } else { on_grid };

            let mut cands: Vec<(f64, f64, bool)> = Vec::with_capacity(source.len());
            for (s, p) in source.into_iter() {
                let intrinsic =
                    (if is_call { entry_spot - s } else { s - entry_spot }).max(0.0);
                match side {
                    TvSide::Otm if intrinsic > 0.0 => continue,
                    TvSide::Itm if intrinsic <= 0.0 => continue,
                    _ => {}
                }
                // (1b) range cap: |strike/entry_spot - 1| in percent, 0 = uncapped.
                if *range_pct > 0.0 && entry_spot > 0.0
                    && ((s / entry_spot) - 1.0).abs() * 100.0 > *range_pct + 1e-9
                {
                    continue;
                }
                let mut tv = p - intrinsic;
                if *pct_units && entry_spot > 0.0 {
                    tv = tv / entry_spot * 100.0;
                }
                // (2) NEGATIVE time value stays a candidate, ranked on ABSOLUTE
                // distance (target 20: TV -50 is 70 away, TV +100 is 80 away,
                // so -50 wins). See the Python mirror for why this is safe only
                // alongside the untradeable filter and the stepwise walk.
                // (3) an untraded strike's stale close makes its "time value"
                // fiction — see the Python comment for the measured 2019 case.
                if lookup_option_price_tradeable(entry_date, index, s, &leg.option_type, expiry)
                    .is_none()
                {
                    continue;
                }
                cands.push((s, tv, intrinsic > 0.0));
            }
            if cands.is_empty() {
                return None;
            }
            // (4) stepwise outward from ATM, PER SIDE — the decay is monotonic
            // only within one side, so interleaving branches makes a single walk
            // quit on the first cross-branch step (measured 09-Mar-2026: returned
            // 23900 / TV 340.55 against a target of 75 instead of 24700 / 63.40).
            let order = |v: &mut Vec<(f64, f64, bool)>| {
                v.sort_by(|a, b| {
                    (a.0 - atm).abs().partial_cmp(&(b.0 - atm).abs())
                        .unwrap_or(std::cmp::Ordering::Equal)
                        .then_with(|| if is_call {
                            b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal)
                        } else {
                            a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal)
                        })
                });
            };
            // Distances are compared ROUNDED so a genuine tie is detectable: the
            // intrinsic carries float error (25300 - 25202.35 = 97.65000000000146),
            // so a "+1 vs -1" pair really differs by ~3e-12 and a strict compare
            // silently picks one. 1e-6 is far below any real price increment.
            let q = |d: f64| (d * 1.0e6).round() / 1.0e6;
            // Returns (dist, strike, tv).
            let walk = |seq: &Vec<(f64, f64, bool)>, force_nearest: bool| -> Option<(f64, f64, f64)> {
                let eff = if force_nearest { &TvCmp::Nearest } else { cmp };
                match eff {
                    TvCmp::Lte => {
                        for (s, tv, _) in seq.iter() {
                            if *tv <= *target { return Some((q((*tv - *target).abs()), *s, *tv)); }
                        }
                        None
                    }
                    TvCmp::Gte => {
                        // NEAREST QUALIFYING, mirroring Lte: first strike meeting
                        // the floor when walking outward from ATM. See the Python
                        // mirror for the 04-Aug-2022 case this fixed.
                        for (s, tv, _) in seq.iter() {
                            if *tv >= *target { return Some((q((*tv - *target).abs()), *s, *tv)); }
                        }
                        None
                    }
                    TvCmp::Nearest => {
                        let mut best: Option<(f64, f64, f64)> = None;
                        for (s, tv, _) in seq.iter() {
                            let d = q((*tv - *target).abs());
                            match best {
                                None => best = Some((d, *s, *tv)),
                                Some((bd, _, btv)) => {
                                    if d < bd {
                                        best = Some((d, *s, *tv));
                                    } else {
                                        // EXACT TIE -> a non-negative time value
                                        // beats a negative one, ahead of ATM
                                        // distance. Then stop: still the crossing.
                                        if d == bd && *tv >= 0.0 && btv < 0.0 {
                                            best = Some((d, *s, *tv));
                                        }
                                        break;
                                    }
                                }
                            }
                        }
                        best
                    }
                }
            };
            let hit = if matches!(side, TvSide::Any) {
                let mut itm: Vec<(f64, f64, bool)> =
                    cands.iter().copied().filter(|c| c.2).collect();
                let mut otm: Vec<(f64, f64, bool)> =
                    cands.iter().copied().filter(|c| !c.2).collect();
                order(&mut itm);
                order(&mut otm);
                let mut hits: Vec<(f64, f64, f64)> =
                    [walk(&otm, false), walk(&itm, false)].into_iter().flatten().collect();
                if matches!(cmp, TvCmp::Nearest) {
                    // NEAREST: smaller |TV-target| wins across the two sides; on
                    // a tie a non-negative time value beats a negative one, and
                    // ATM distance decides only after that. (09-Mar-2026, target
                    // 75, 2% cap: OTM 23600 is 180.85 off, ITM 24500 is 74.75 off
                    // -> 24500.)
                    hits.sort_by(|a, b| {
                        a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal)
                            .then_with(|| (a.2 < 0.0).cmp(&(b.2 < 0.0)))
                            .then_with(|| (a.1 - atm).abs().partial_cmp(&(b.1 - atm).abs())
                                .unwrap_or(std::cmp::Ordering::Equal))
                    });
                } else {
                    // GTE / LTE: whichever side the outward walk REACHES FIRST
                    // wins (closest to ATM); |TV-target| only breaks a tie. Side
                    // choice only — the walk inside a side still honours the
                    // floor/ceiling.
                    hits.sort_by(|a, b| {
                        (a.1 - atm).abs().partial_cmp(&(b.1 - atm).abs())
                            .unwrap_or(std::cmp::Ordering::Equal)
                            .then_with(|| a.0.partial_cmp(&b.0)
                                .unwrap_or(std::cmp::Ordering::Equal))
                    });
                }
                hits.into_iter().next()
            } else {
                order(&mut cands);
                walk(&cands, false)
            };
            // CAP FALLBACK: with a tight range cap no in-band strike may satisfy
            // the floor/ceiling at all (09-Mar-2026, target 75, 2% cap: lowest
            // in-band TV is 149.75, so "TV <= 75" matched nothing). Take the
            // CLOSEST available inside the band rather than dropping the trade.
            let hit = match hit {
                Some(h) => Some(h),
                None if !matches!(cmp, TvCmp::Nearest) => {
                    if matches!(side, TvSide::Any) {
                        let mut itm: Vec<(f64, f64, bool)> =
                            cands.iter().copied().filter(|c| c.2).collect();
                        let mut otm: Vec<(f64, f64, bool)> =
                            cands.iter().copied().filter(|c| !c.2).collect();
                        order(&mut itm);
                        order(&mut otm);
                        let mut fb: Vec<(f64, f64, f64)> =
                            [walk(&otm, true), walk(&itm, true)].into_iter().flatten().collect();
                        fb.sort_by(|a, b| {
                            a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal)
                                .then_with(|| (a.2 < 0.0).cmp(&(b.2 < 0.0)))
                                .then_with(|| (a.1 - atm).abs().partial_cmp(&(b.1 - atm).abs())
                                    .unwrap_or(std::cmp::Ordering::Equal))
                        });
                        fb.into_iter().next()
                    } else {
                        order(&mut cands);
                        walk(&cands, true)
                    }
                }
                None => None,
            };
            hit.map(|(_, s, _)| s)
        }
        StrikeSel::Delta(target) => {
            let chain = lookup_strikes_for_date(entry_date, index, expiry, &leg.option_type)?;
            let iv = leg.strike_interval;
            let on_grid: Vec<(f64, f64)> = if iv > 0.0 {
                chain.iter().copied()
                    .filter(|(s, _)| ((s / iv) - (s / iv).round()).abs() < 1e-9)
                    .collect()
            } else {
                Vec::new()
            };
            let source = if on_grid.is_empty() { chain } else { on_grid };
            let dte = match (
                NaiveDate::parse_from_str(entry_date, "%Y-%m-%d"),
                NaiveDate::parse_from_str(expiry, "%Y-%m-%d"),
            ) {
                (Ok(entry), Ok(exp)) => (exp - entry).num_days().max(0) as f64,
                _ => 7.0,
            };
            let sigma = delta_iv(index);
            source.into_iter()
                .filter(|(strike, _)| {
                    lookup_option_price_tradeable(
                        entry_date, index, *strike, &leg.option_type, expiry,
                    ).is_some()
                })
                .min_by(|a, b| {
                    let da = ((bs_abs_delta(entry_spot, a.0, dte, sigma, is_call) - *target)
                        .abs() * 1.0e6).round() / 1.0e6;
                    let db = ((bs_abs_delta(entry_spot, b.0, dte, sigma, is_call) - *target)
                        .abs() * 1.0e6).round() / 1.0e6;
                    da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal)
                        .then_with(|| (a.0 - atm).abs().partial_cmp(&(b.0 - atm).abs())
                            .unwrap_or(std::cmp::Ordering::Equal))
                        .then_with(|| if is_call {
                            b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal)
                        } else {
                            a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal)
                        })
                })
                .map(|(strike, _)| strike)
        }
        StrikeSel::StraddleWidth { multiplier, direction } => {
            // shift = multiplier × (ATM CE + ATM PE), then snap. direction is a
            // raw +/- sign applied identically to every leg — "+" always adds
            // the shift to ATM, "-" always subtracts — regardless of the leg's
            // option_type or Buy/Sell (both legs of a straddle land on the
            // same strike for the same direction setting; no CE/PE mirroring).
            let (ce, pe, _source) = straddle_atm_prices(
                atm, leg.strike_interval, entry_spot, entry_date, expiry, index,
            )?;
            let shift = *multiplier * (ce + pe);
            let raw = if direction.trim() == "-" {
                atm - shift
            } else {
                atm + shift
            };
            Some((raw / leg.strike_interval).round() * leg.strike_interval)
        }
        StrikeSel::AtmStraddlePremPct(pct) => {
            // target premium = pct% × (ATM CE + ATM PE), then closest-premium
            // for the leg's option type.
            let (ce, pe, _source) = straddle_atm_prices(
                atm, leg.strike_interval, entry_spot, entry_date, expiry, index,
            )?;
            let target = (pct / 100.0) * (ce + pe);
            let chain = lookup_strikes_for_date(entry_date, index, expiry, &leg.option_type)?;
            pick_by_premium(&chain, target, atm, is_call).map(|(s, _)| *s)
        }
    };
    let raw_strike = computed?;
    // Strike-shift fallback for zero-turnover contracts: walk TOWARD ATM
    // until a liquid strike is found (capped at ATM itself). Always-on; the
    // legacy `strike_shift_max` arg is retained for ABI compat but ignored.
    // straddle_width legs share the same requested strike across CE/PE ONLY
    // when a sibling leg has the same multiplier+direction (straddle_use_joint)
    // — then they use the JOINT liquidity walk (both must move together).
    // Otherwise (different multiplier, or no sibling straddle_width leg at
    // all) this leg's strike is its own, so it uses the per-option-type walk
    // every other mode uses.
    let (final_strike, _shift_steps) = if matches!(leg.strike, StrikeSel::StraddleWidth { .. }) && leg.straddle_use_joint {
        validate_or_shift_straddle_strike(raw_strike, atm, leg.strike_interval, entry_date, expiry, index)?
    } else {
        validate_or_shift_strike(
            raw_strike, atm, leg.strike_interval, is_call,
            entry_date, expiry, index, &leg.option_type, strike_shift_max,
        )?
    };
    Some((final_strike, raw_strike))
}

/// Parse a single leg dict into a `LegCfg` — a minimal counterpart to
/// `extract_leg_cfgs` for single-leg ad-hoc strike computation (no
/// unsupported-feature rejection; that's only relevant to the full
/// schedule-building path, not a bare strike lookup).
fn leg_cfg_from_dict(leg: &PyDict) -> Option<LegCfg> {
    let strike = extract_strike_sel(leg)?;
    let strike_interval = leg
        .get_item("strike_interval").ok().flatten()
        .and_then(|v| v.extract::<f64>().ok())
        .unwrap_or(50.0);
    // Set by run_rust_engine_pipeline (Python) before this leg dict reaches
    // Rust — true only when a sibling straddle_width leg shares this leg's
    // multiplier+direction. See LegCfg.straddle_use_joint.
    let straddle_use_joint = leg
        .get_item("_straddle_use_joint_shift").ok().flatten()
        .and_then(|v| v.extract::<bool>().ok())
        .unwrap_or(false);
    Some(LegCfg {
        option_type: leg
            .get_item("option_type").ok().flatten()
            .and_then(|v| v.extract::<String>().ok())
            .unwrap_or_else(|| "CE".to_string()),
        position: leg
            .get_item("position").ok().flatten()
            .and_then(|v| v.extract::<String>().ok())
            .unwrap_or_else(|| "SELL".to_string()),
        lots: leg
            .get_item("lots").ok().flatten()
            .and_then(|v| v.extract::<i64>().ok())
            .unwrap_or(1),
        strike_interval,
        strike,
        straddle_use_joint,
        // Irrelevant here: the carry policy is only read by the pinned rollover
        // loop, which builds its LegCfgs via extract_leg_cfgs. A bare one-off
        // strike lookup has no epoch to carry from.
        rollover_strike_mode: StrikeMode::Fresh,
        is_yearly: false,
        // Empty: this one-off lookup is driven by the Python schedule builders,
        // which pass the already-resolved per-contract gap in `strike_interval`,
        // so re-resolving here would double-apply. The batched resolve_trade_specs
        // path (extract_leg_cfgs) carries the real schedule.
        yearly_schedule: Vec::new(),
    })
}

/// Single-leg strike resolution callable directly from Python — consolidates
/// what `_compute_strike_for_leg_python` previously did as a CHAIN of many
/// small Python↔Rust FFI calls (price lookups, tradeable checks, the
/// zero-turnover walk, and for straddle_width/atm_straddle_prem_pct the
/// gap-widening fallback) into ONE call. Used by the Python schedule
/// builders that can't use the batched `resolve_trade_specs` path (Fixed
/// Entry mode, NEXT_WEEKLY, re-entry, spot-adjustment, etc.) — those still
/// build the entry/exit schedule in Python, but now resolve each leg's
/// strike in Rust instead of many round-trips per leg.
///
/// Returns `(final_strike, requested_strike, atm_strike, ce_price, pe_price,
/// straddle_price_source)` or `None` if the leg is unresolvable (missing
/// data, unknown strike_selection.type, etc — caller falls back to the
/// Python mirror). `ce_price`/`pe_price`/`straddle_price_source` are only
/// populated for straddle_width / atm_straddle_prem_pct (display columns);
/// blank/None for every other strike mode.
#[pyfunction]
pub fn compute_straddle_leg_strike(
    leg: &PyDict,
    entry_date: String,
    expiry: String,
    index: String,
    entry_spot: f64,
    strike_shift_max: i32,
    resolved_strikes: HashMap<i64, f64>,
) -> PyResult<Option<(f64, f64, f64, Option<f64>, Option<f64>, String)>> {
    let cfg = match leg_cfg_from_dict(leg) {
        Some(c) => c,
        None => return Ok(None),
    };
    let result = compute_strike_for_leg(
        &cfg, &entry_date, &expiry, &index, entry_spot, strike_shift_max, &resolved_strikes,
    );
    let (final_strike, requested_strike) = match result {
        Some(v) => v,
        None => return Ok(None),
    };
    let atm = (entry_spot / cfg.strike_interval).round() * cfg.strike_interval;
    let is_straddle_type = matches!(
        cfg.strike,
        StrikeSel::StraddleWidth { .. } | StrikeSel::AtmStraddlePremPct(_)
    );
    let mut ce_out = None;
    let mut pe_out = None;
    let mut source = String::new();
    if is_straddle_type {
        if let Some((ce, pe, src)) = straddle_atm_prices(
            atm, cfg.strike_interval, entry_spot, &entry_date, &expiry, &index,
        ) {
            ce_out = Some(ce);
            pe_out = Some(pe);
            source = src;
        }
    }
    Ok(Some((final_strike, requested_strike, atm, ce_out, pe_out, source)))
}

/// Slice 2 — resolve every (entry_date, exit_date, strike, leg) tuple for
/// the given strategy payload and trading calendar.
///
/// Inputs
/// ------
/// payload         strategy config (uses `index`, `entry_dte`, `exit_dte`,
///                 `legs[]`, `slippage_pct`, etc.)
/// expiry_dates    list of ISO YYYY-MM-DD weekly/monthly expiries in range
/// trading_days    list of ISO YYYY-MM-DD trading days in range
/// lot_size        contract lot size for the index
///
/// Output
/// ------
/// A `PyList` of trade-spec dicts. The list is **empty** if the payload
/// uses any feature this slice does not yet handle — the caller MUST detect
/// the empty result and fall back to the Python engine.
///
/// Currently supported:
///   * Strike modes: ATM, ITM1..N, OTM1..N, pct_of_atm, rel_leg (Iron Condor wing)
///   * Single or multi-leg strategies with same entry/exit DTE
///   * Slippage
/// Not yet supported (caller falls back to Python):
///   * Per-leg SL / Target / Trail SL / SL-with-Buffer / Re-entry
///   * Strategy-level Overall SL / Target
///   * Rollover, No-Rollover, Spot Adjustment, Buffer Strike
///   * STR filter, custom date filter
///   * Futures legs
///   * Strike modes: atm_straddle_prem_pct, straddle_width, premium_*
/// Payload-derived config for `resolve_trade_specs_core` — all pure Rust so the
/// core carries no PyO3 dependency and can be driven straight from the Rust
/// combo loop (Phase 1).
pub(crate) struct ResolveCfg {
    legs: Vec<LegCfg>,
    index: String,
    entry_dte: u32,
    exit_dte: u32,
    calendar_days: bool,
    slippage_pct: f64,
    strike_shift_max: i32,
    rollover_active: bool,
    rollover_min_days: u32,
    lot_size: i64,
    /// YEARLY only: the pinned December contract per cycle. `None` on every
    /// existing (weekly/monthly) path — the contract is then the cadence
    /// element, exactly as before.
    yearly_cycles: Option<Vec<YearlyCycle>>,
    /// PER-LEG ROLLOVER (opt-in). False on every existing path ⇒ the four fields
    /// below are empty and `resolve_trade_specs_core` never takes the union
    /// branch, so behaviour is byte-identical to today.
    per_leg_rollover: bool,
    /// Index-aligned to `legs`: each leg's OWN cadence expiry list.
    leg_rollover_expiries: Vec<Vec<String>>,
    /// Index-aligned to `legs`: each leg's OWN exit T-n.
    leg_exit_dte: Vec<u32>,
    /// Index-aligned to `legs`: pinned December cycles for a YEARLY leg, else None.
    leg_cycles: Vec<Option<Vec<YearlyCycle>>>,
    /// PER-LEG SPOT-ADJUSTMENT (opt-in). Index-aligned to `legs`: each leg's own
    /// breach dates (Python-computed). Empty per leg ⇒ no forced re-strike, so the
    /// No-Adjustment path is byte-identical.
    leg_breach_dates: Vec<Vec<String>>,
    /// PER-LEG ROLLOVER + FILTER: filter segment START dates (trading-day-snapped
    /// by Python), injected as extra union boundaries so a filter that opens
    /// mid-contract enters the active contract fresh at the open — mirroring the
    /// non-per-leg path. Empty with no filter ⇒ inert.
    seg_starts: Vec<String>,
}

/// PER-LEG ROLLOVER resolve loop. Drives trade rows off the union scheduler
/// (`build_rollover_schedule_per_leg`); for each row, each leg either ROLLS at
/// its own boundary (re-pick subject to its Fresh/Fixed epoch rule) or CARRIES
/// its epoch strike (re-validated against this entry, marked-to-market by the
/// sim over the sub-segment). Reuses the exact same helpers as the pinned path
/// — `opens_new_epoch`, `compute_strike_for_leg`, `validate_or_shift_strike` —
/// so there is no new strike/pricing logic; only the schedule shape is new.
fn resolve_per_leg_core(
    cfg: &ResolveCfg,
    td: &[String],
    spot_by_date: &HashMap<String, f64>,
) -> Vec<TradeSpec> {
    let mut out: Vec<TradeSpec> = Vec::new();
    if cfg.legs.is_empty() || td.is_empty() {
        return out;
    }
    // All legs are held from the first trading day; each rolls on its own T-n.
    let run_start = td[0].clone();
    let schedule = build_rollover_schedule_per_leg(
        &run_start,
        &cfg.leg_rollover_expiries,
        &cfg.leg_exit_dte,
        &cfg.leg_cycles,
        &cfg.leg_breach_dates,
        &cfg.seg_starts,
        td,
    );

    // Per-leg strike epochs + per-leg previous OWN-entry anchor (the pinned path
    // keeps a single shared anchor because it has one cadence; here every leg
    // rolls on its own schedule, so both maps are keyed by leg_id).
    let mut epoch_strike: HashMap<i64, f64> = HashMap::new();
    let mut prev_own_entry: HashMap<i64, String> = HashMap::new();

    'row: for (trade_id, entry_date, exit_date, slots) in &schedule {
        let entry_spot = match spot_by_date.get(entry_date) {
            Some(&v) if v > 0.0 => v,
            _ => continue,
        };
        // Defensive: the scheduler only emits rows where every leg is active.
        if slots.len() != cfg.legs.len() {
            continue;
        }
        let mut buf: Vec<TradeSpec> = Vec::with_capacity(cfg.legs.len());
        let mut resolved: HashMap<i64, f64> = HashMap::with_capacity(cfg.legs.len());
        for (leg_idx, leg) in cfg.legs.iter().enumerate() {
            let leg_id = (leg_idx + 1) as i64;
            let slot = &slots[leg_idx];
            let leg_expiry = &slot.contract;

            // Roll (own boundary) ⇒ epoch rule decides Fresh vs carry.
            // Carry (foreign boundary, i.e. a row created because ANOTHER leg
            // rolled) ⇒ reuse the epoch strike, marked-to-market: a non-rolling
            // leg HOLDS its strike between its OWN rolls (the per-leg-rollover
            // carry spec / calendar-spread convention), re-striking only on its
            // own roll/breach — never because a foreign leg re-booked.
            // PER-LEG SPOT-ADJUSTMENT: a breach at this row's START forces a FRESH
            // re-strike (ATM at the breach-day spot) regardless of own_boundary or
            // the epoch/Fresh-Fixed rule. False on every non-spot-adj leg.
            let fresh = if slot.breach_spot_adj || slot.seg_start_entry {
                true
            } else if slot.own_boundary {
                match prev_own_entry.get(&leg_id) {
                    None => true, // first own-entry always resolves fresh
                    Some(prev) => opens_new_epoch(
                        leg.rollover_strike_mode, prev, entry_date, slot.new_cycle, leg.is_yearly,
                    ),
                }
            } else {
                false
            };

            let (strike, requested_strike) = match (fresh, epoch_strike.get(&leg_id).copied()) {
                (false, Some(carried)) => {
                    // Carry-over is RE-VALIDATED against this entry date + this
                    // contract, never blindly reused (long-dated strikes can go
                    // unlisted/illiquid). Same as the pinned carry.
                    let atm = (entry_spot / leg.strike_interval).round() * leg.strike_interval;
                    let is_call = matches!(
                        leg.option_type.to_ascii_uppercase().as_str(), "CE" | "CALL" | "C"
                    );
                    match revalidate_carried_strike(
                        carried, atm, leg.strike_interval, is_call, entry_date, leg_expiry,
                        &cfg.index, &leg.option_type, cfg.strike_shift_max,
                    ) {
                        Some((s, _shifts)) => (s, carried),
                        None => continue 'row,
                    }
                }
                _ => match compute_strike_for_leg(
                    leg, entry_date, leg_expiry, &cfg.index, entry_spot, cfg.strike_shift_max, &resolved,
                ) {
                    Some(v) => v,
                    None => continue 'row,
                },
            };

            epoch_strike.insert(leg_id, strike);
            resolved.insert(leg_id, strike);
            buf.push(TradeSpec {
                trade_id: *trade_id,
                leg_id,
                index: cfg.index.clone(),
                entry_date: entry_date.clone(),
                exit_date: exit_date.clone(),
                expiry: leg_expiry.clone(),
                strike: round2(strike),
                requested_strike: round2(requested_strike),
                strike_interval: leg.strike_interval,
                option_type: leg.option_type.clone(),
                position: leg.position.clone(),
                lots: leg.lots,
                lot_size: cfg.lot_size,
                slippage_pct: cfg.slippage_pct,
            });
        }
        out.extend(buf);
        // Advance the OWN-entry anchor only for legs that rolled this row, and
        // only for a trade that actually emitted (a `continue 'row` skips this).
        for (leg_idx, slot) in slots.iter().enumerate() {
            if slot.own_boundary {
                prev_own_entry.insert((leg_idx + 1) as i64, entry_date.clone());
            }
        }
    }
    out
}

/// Pure-Rust core of `resolve_trade_specs` — no PyO3, no GIL. Enumerates
/// (trade, leg) tuples (rollover schedule when active, else one trade per
/// expiry in the given order), resolves each leg's strike via
/// `compute_strike_for_leg`, and commits a trade only when ALL its legs resolve
/// — a missing strike skips just that trade (matching the Python engine's
/// per-trade tolerance for NSE data holes), not the whole run. Returns the
/// resolved specs in output order; the `#[pyfunction]` wrapper serialises them
/// to the same dicts as before, and `simulate_trades_batch_core` consumes them
/// directly (Phase 1) without a PyO3 round-trip.
pub(crate) fn resolve_trade_specs_core(
    cfg: &ResolveCfg,
    expiry_dates: &[String],
    trading_days: &[String],
    spot_by_date: &HashMap<String, f64>,
) -> Vec<TradeSpec> {
    let mut out: Vec<TradeSpec> = Vec::new();

    let mut td: Vec<String> = trading_days.to_vec();
    td.sort();

    // PER-LEG ROLLOVER — opt-in union scheduler. Gated: false on every existing
    // path, so nothing below the branch is reachable for legacy runs.
    if cfg.per_leg_rollover {
        return resolve_per_leg_core(cfg, &td, spot_by_date);
    }

    let mut expiries_sorted: Vec<String> = expiry_dates.to_vec();
    expiries_sorted.sort();

    if cfg.rollover_active {
        let schedule = build_rollover_schedule_pinned(
            &expiries_sorted, &td, cfg.entry_dte, cfg.exit_dte, cfg.rollover_min_days,
            cfg.yearly_cycles.as_deref(), cfg.calendar_days,
        );
        // YEARLY strike epochs. Only touched when the contract is pinned — on
        // every existing weekly/monthly run `epoch_strike` is never read or
        // written, and rollover_strike_mode keeps being applied by Python's
        // _apply_fixed_rollover_strike exactly as before.
        let pinned = cfg.yearly_cycles.is_some();
        let mut epoch_strike: HashMap<i64, f64> = HashMap::new();
        let mut prev_entry: Option<String> = None;

        'rollover_trade: for (trade_id, entry_date, exit_date, leg_expiry, _orig_expiry, new_cycle) in &schedule {
            let entry_spot = match spot_by_date.get(entry_date) {
                Some(&v) if v > 0.0 => v,
                _ => continue,
            };
            let mut buf: Vec<TradeSpec> = Vec::with_capacity(cfg.legs.len());
            let mut resolved: HashMap<i64, f64> = HashMap::with_capacity(cfg.legs.len());
            for (leg_idx, leg) in cfg.legs.iter().enumerate() {
                let leg_id = (leg_idx + 1) as i64;
                // PER-LEG contract. Pinned runs hand the December contract ONLY
                // to legs whose own expiry is YEARLY; a weekly/monthly leg in the
                // same basket takes the cadence element instead, so a mixed
                // strategy (CE weekly + PE yearly) holds two different contracts
                // while sharing one roll cadence. Unpinned runs are untouched:
                // `pinned` is false, so this is always `leg_expiry` — including
                // the min-DTE-advanced value, which `_orig_expiry` would lose.
                let leg_expiry: &String = if pinned && !leg.is_yearly {
                    _orig_expiry
                } else {
                    leg_expiry
                };
                let (strike, requested_strike) = if !pinned {
                    // UNPINNED — literally the pre-change call. Do not route this
                    // through the epoch logic: if `epoch_strike` were ever
                    // consulted here, Fresh would silently start carrying strikes
                    // across every existing run.
                    match compute_strike_for_leg(
                        leg, entry_date, leg_expiry, &cfg.index, entry_spot, cfg.strike_shift_max, &resolved,
                    ) {
                        Some(v) => v,
                        None => continue 'rollover_trade,
                    }
                } else {
                    let fresh = match prev_entry.as_deref() {
                        None => true, // first emitted trade always resolves fresh
                        Some(prev) => opens_new_epoch(
                            leg.rollover_strike_mode, prev, entry_date, *new_cycle, leg.is_yearly,
                        ),
                    };
                    match (fresh, epoch_strike.get(&leg_id).copied()) {
                        (false, Some(carried)) => {
                            // Carry-over is RE-VALIDATED against this entry date
                            // and this December contract — never blindly reused.
                            // Python's carry skips the zero-turnover shift, which
                            // is harmless over a few weeks on a short-dated
                            // contract but not over ~12 months on a long-dated
                            // one, where the strike can go unlisted/illiquid.
                            let atm = (entry_spot / leg.strike_interval).round() * leg.strike_interval;
                            let is_call = matches!(leg.option_type.to_ascii_uppercase().as_str(), "CE" | "CALL" | "C");
                            match revalidate_carried_strike(
                                carried, atm, leg.strike_interval, is_call, entry_date, leg_expiry,
                                &cfg.index, &leg.option_type, cfg.strike_shift_max,
                            ) {
                                Some((s, _shifts)) => (s, carried),
                                None => continue 'rollover_trade,
                            }
                        }
                        _ => match compute_strike_for_leg(
                            leg, entry_date, leg_expiry, &cfg.index, entry_spot, cfg.strike_shift_max, &resolved,
                        ) {
                            Some(v) => v,
                            None => continue 'rollover_trade,
                        },
                    }
                };
                if pinned {
                    epoch_strike.insert(leg_id, strike);
                }
                resolved.insert((leg_idx + 1) as i64, strike);
                buf.push(TradeSpec {
                    trade_id: *trade_id,
                    leg_id: (leg_idx + 1) as i64,
                    index: cfg.index.clone(),
                    entry_date: entry_date.clone(),
                    exit_date: exit_date.clone(),
                    expiry: leg_expiry.clone(),
                    strike: round2(strike),
                    requested_strike: round2(requested_strike),
                    strike_interval: leg.strike_interval,
                    option_type: leg.option_type.clone(),
                    position: leg.position.clone(),
                    lots: leg.lots,
                    lot_size: cfg.lot_size,
                    slippage_pct: cfg.slippage_pct,
                });
            }
            out.extend(buf);
            // Advance the epoch anchor only for trades that actually emitted —
            // a trade dropped by `continue 'rollover_trade` (unresolvable leg)
            // must not consume the month boundary, or the next real trade in a
            // new month would wrongly carry the previous month's strike.
            if pinned {
                prev_entry = Some(entry_date.clone());
            }
        }
        return out;
    }

    let mut next_trade_id: i64 = 1;
    'dte_trade: for expiry in expiry_dates {
        let entry_date = match day_before_expiry(expiry, cfg.entry_dte, &td, cfg.calendar_days) {
            Some(v) => v,
            None => continue,
        };
        let exit_date = match day_before_expiry(expiry, cfg.exit_dte, &td, cfg.calendar_days) {
            Some(v) => v,
            None => continue,
        };
        if entry_date > exit_date {
            continue;
        }
        // Spot is supplied by Python — it has the authoritative data path
        // (Postgres, parquet, feather) and may be wider than the Rust feather.
        let entry_spot = match spot_by_date.get(&entry_date) {
            Some(&v) if v > 0.0 => v,
            _ => continue,
        };

        let trade_id = next_trade_id;
        let mut buf: Vec<TradeSpec> = Vec::with_capacity(cfg.legs.len());
        let mut resolved: HashMap<i64, f64> = HashMap::with_capacity(cfg.legs.len());
        for (leg_idx, leg) in cfg.legs.iter().enumerate() {
            let (strike, requested_strike) = match compute_strike_for_leg(
                leg, &entry_date, expiry, &cfg.index, entry_spot, cfg.strike_shift_max, &resolved,
            ) {
                Some(v) => v,
                None => continue 'dte_trade,
            };
            resolved.insert((leg_idx + 1) as i64, strike);
            buf.push(TradeSpec {
                trade_id,
                leg_id: (leg_idx + 1) as i64,
                index: cfg.index.clone(),
                entry_date: entry_date.clone(),
                exit_date: exit_date.clone(),
                expiry: expiry.clone(),
                strike: round2(strike),
                requested_strike: round2(requested_strike),
                strike_interval: leg.strike_interval,
                option_type: leg.option_type.clone(),
                position: leg.position.clone(),
                lots: leg.lots,
                lot_size: cfg.lot_size,
                slippage_pct: cfg.slippage_pct,
            });
        }
        out.extend(buf);
        next_trade_id += 1;
    }
    out
}

#[pyfunction]
pub fn resolve_trade_specs(
    payload: &PyDict,
    expiry_dates: Vec<String>,
    trading_days: Vec<String>,
    lot_size: i64,
    spot_by_date: HashMap<String, f64>,
) -> PyResult<PyObject> {
    let py = payload.py();
    let out = PyList::empty(py);

    if check_strategy_blockers(payload).is_some() {
        return Ok(out.into());
    }
    let (legs, leg_blocker) = extract_leg_cfgs(payload)?;
    if legs.is_empty() || leg_blocker.is_some() {
        return Ok(out.into());
    }
    // User-configurable strike-shift fallback for missing/illiquid contracts.
    // Default 1: if the requested strike has no contract or zero close, shift
    // ONE strike-interval further from ATM in the requested direction.
    let strike_shift_max: i32 = payload
        .get_item("strike_shift_max_steps").ok().flatten()
        .and_then(|v| v.extract::<i64>().ok())
        .map(|n| n.clamp(0, 50) as i32)
        .unwrap_or(1);
    let index = payload
        .get_item("index").ok().flatten()
        .and_then(|v| v.extract::<String>().ok())
        .unwrap_or_else(|| "NIFTY".to_string());
    let entry_dte: u32 = payload
        .get_item("entry_dte").ok().flatten()
        .and_then(|v| v.extract::<u32>().ok())
        .unwrap_or(1);
    let exit_dte: u32 = payload
        .get_item("exit_dte").ok().flatten()
        .and_then(|v| v.extract::<u32>().ok())
        .unwrap_or(0);
    let calendar_days = payload
        .get_item("dte_day_basis").ok().flatten()
        .and_then(|v| v.extract::<String>().ok())
        .map(|v| v.eq_ignore_ascii_case("calendar"))
        .unwrap_or(false);
    let slippage_pct = payload
        .get_item("slippage_pct").ok().flatten()
        .and_then(|v| v.extract::<f64>().ok())
        .unwrap_or(0.0);
    // Slice 6: rollover_toggle support for WEEKLY/MONTHLY. When active, use
    // the rollover schedule builder which handles same-day chain + min-DTE
    // extension. Otherwise use the simple "one trade per expiry" path.
    let etype = payload
        .get_item("expiry_type").ok().flatten()
        .and_then(|v| v.extract::<String>().ok())
        .unwrap_or_default()
        .to_uppercase();
    let rollover_active = {
        let truthy = payload
            .get_item("rollover_toggle").ok().flatten()
            .and_then(|v| v.extract::<bool>().ok())
            .unwrap_or(false);
        truthy && matches!(etype.as_str(), "WEEKLY" | "MONTHLY" | "YEARLY")
    };
    let rollover_min_days: u32 = if rollover_active {
        payload
            .get_item("rollover_min_days_to_expiry").ok().flatten()
            .and_then(|v| v.extract::<u32>().ok())
            .unwrap_or(0)
    } else {
        0
    };

    // ── PER-LEG ROLLOVER extraction (opt-in) ─────────────────────────────────
    // Python injects each leg's OWN cadence expiry list (`_rollover_expiries`),
    // its own exit T-n (`exit_dte`, falling back to the global one), and — for a
    // YEARLY leg — its pinned December cycles (`_rollover_cycles`). Empty on every
    // existing path (flag false), so the union branch is unreachable there.
    let per_leg_rollover = payload
        .get_item("per_leg_rollover").ok().flatten()
        .and_then(|v| v.extract::<bool>().ok())
        .unwrap_or(false);
    let (leg_rollover_expiries, leg_exit_dte, leg_cycles, leg_breach_dates): (
        Vec<Vec<String>>, Vec<u32>, Vec<Option<Vec<YearlyCycle>>>, Vec<Vec<String>>,
    ) = if per_leg_rollover {
        let legs_any = payload.get_item("legs").ok().flatten().ok_or_else(|| {
            pyo3::exceptions::PyValueError::new_err("per_leg_rollover set but payload has no 'legs'")
        })?;
        let legs_list = legs_any.downcast::<PyList>()?;
        let mut exps: Vec<Vec<String>> = Vec::with_capacity(legs_list.len());
        let mut xdtes: Vec<u32> = Vec::with_capacity(legs_list.len());
        let mut cycs: Vec<Option<Vec<YearlyCycle>>> = Vec::with_capacity(legs_list.len());
        let mut brs: Vec<Vec<String>> = Vec::with_capacity(legs_list.len());
        for (i, item) in legs_list.iter().enumerate() {
            let leg = item.downcast::<PyDict>()?;
            let e: Vec<String> = leg
                .get_item("_rollover_expiries").ok().flatten()
                .and_then(|v| v.extract::<Vec<String>>().ok())
                .ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!(
                    "per_leg_rollover: leg {} missing '_rollover_expiries' (Python must inject \
                     each leg's own cadence expiry list)", i)))?;
            let x: u32 = leg
                .get_item("exit_dte").ok().flatten()
                .and_then(|v| v.extract::<u32>().ok())
                .unwrap_or(exit_dte);
            let c: Option<Vec<YearlyCycle>> = match leg.get_item("_rollover_cycles").ok().flatten() {
                Some(v) if !v.is_none() => {
                    let list = v.downcast::<PyList>().map_err(|_| {
                        pyo3::exceptions::PyValueError::new_err(format!(
                            "per_leg_rollover: leg {} '_rollover_cycles' must be a list", i))
                    })?;
                    let mut cc: Vec<YearlyCycle> = Vec::with_capacity(list.len());
                    for row in list.iter() {
                        let d = row.downcast::<PyDict>()?;
                        let contract = extract_str(d, "contract");
                        let start = extract_str(d, "start");
                        let end = extract_str(d, "end");
                        if contract.is_empty() || start.is_empty() || end.is_empty() {
                            continue;
                        }
                        cc.push(YearlyCycle { contract, start, end });
                    }
                    if cc.is_empty() { None } else { Some(cc) }
                }
                _ => None,
            };
            // PER-LEG SPOT-ADJUSTMENT: Python-computed breach dates. Absent/None
            // ⇒ empty vec ⇒ no forced re-strike (No-Adjustment path unchanged).
            let br: Vec<String> = leg
                .get_item("_spot_adj_breaches").ok().flatten()
                .and_then(|v| v.extract::<Vec<String>>().ok())
                .unwrap_or_default();
            exps.push(e);
            xdtes.push(x);
            cycs.push(c);
            brs.push(br);
        }
        (exps, xdtes, cycs, brs)
    } else {
        (Vec::new(), Vec::new(), Vec::new(), Vec::new())
    };

    // YEARLY pins the contract to a December expiry; the cadence list only
    // drives entry/exit. Python resolves the cycles (they are expiry_calendar
    // rows + a T-n month offset against the trading calendar — neither of which
    // Rust carries on the EOD path) exactly as it already resolves expiry_dates.
    //
    // Hard-fail rather than fall through: admitting YEARLY without the pin would
    // leave leg_expiry = cur_exp, producing a plausible-but-wrong tradesheet
    // that trades the CADENCE contract. That is worse than the silent disable
    // this gate used to do. Never guess the pin.
    // Per-leg rollover carries its cycles PER LEG (`leg_cycles`), so the
    // strategy-level YEARLY pin/gate does not apply — a per-leg run's top-level
    // expiry_type is a mix, not a single YEARLY.
    let yearly_cycles: Option<Vec<YearlyCycle>> = if etype == "YEARLY" && !per_leg_rollover {
        match extract_yearly_cycles(payload)? {
            Some(c) if !c.is_empty() => Some(c),
            _ => {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "expiry_type=YEARLY requires a non-empty 'yearly_cycles' payload key \
                     (list of {contract, start, end}); refusing to guess the pinned contract",
                ))
            }
        }
    } else {
        None
    };
    if yearly_cycles.is_some() && rollover_min_days > 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "expiry_type=YEARLY is incompatible with rollover_min_days_to_expiry: the min-DTE \
             extension advances the contract to the next CADENCE element, which would swap the \
             pinned December contract for a weekly. Use yearly_exit_months_before (T-n) instead",
        ));
    }

    // PER-LEG ROLLOVER + FILTER: Python injects `_per_leg_seg_starts` — the filter
    // segment opens already snapped to trading days. Only read under per_leg_rollover
    // (absent/empty otherwise ⇒ inert), so no existing path is touched.
    let seg_starts: Vec<String> = if per_leg_rollover {
        payload
            .get_item("_per_leg_seg_starts").ok().flatten()
            .and_then(|v| v.extract::<Vec<String>>().ok())
            .unwrap_or_default()
    } else {
        Vec::new()
    };

    let cfg = ResolveCfg {
        legs, index, entry_dte, exit_dte, calendar_days, slippage_pct, strike_shift_max,
        rollover_active, rollover_min_days, lot_size, yearly_cycles,
        per_leg_rollover, leg_rollover_expiries, leg_exit_dte, leg_cycles,
        leg_breach_dates, seg_starts,
    };

    // Pure-Rust resolution — no Python objects touched, so release the GIL.
    let specs = py.allow_threads(|| {
        resolve_trade_specs_core(&cfg, &expiry_dates, &trading_days, &spot_by_date)
    });

    for s in &specs {
        let d = PyDict::new(py);
        d.set_item("trade_id", s.trade_id)?;
        d.set_item("leg_id", s.leg_id)?;
        d.set_item("index", &s.index)?;
        d.set_item("entry_date", &s.entry_date)?;
        d.set_item("exit_date", &s.exit_date)?;
        d.set_item("expiry", &s.expiry)?;
        d.set_item("strike", s.strike)?;
        d.set_item("requested_strike", s.requested_strike)?;
        d.set_item("strike_interval", s.strike_interval)?;
        d.set_item("option_type", &s.option_type)?;
        d.set_item("position", &s.position)?;
        d.set_item("lots", s.lots)?;
        d.set_item("lot_size", s.lot_size)?;
        d.set_item("slippage_pct", s.slippage_pct)?;
        out.append(d)?;
    }
    Ok(out.into())
}

#[derive(Debug, Clone)]
pub(crate) struct TradeSpec {
    trade_id: i64,
    leg_id: i64,
    index: String,
    entry_date: String,
    exit_date: String,
    expiry: String,
    strike: f64,
    requested_strike: f64,
    strike_interval: f64,
    option_type: String,
    position: String,
    lots: i64,
    lot_size: i64,
    slippage_pct: f64,
}

#[derive(Debug, Clone)]
pub(crate) struct TradeResult {
    entry_price: f64,
    exit_price: f64,
    raw_entry_price: f64,
    raw_exit_price: f64,
    entry_spot: f64,
    exit_spot: f64,
    net_pnl: f64,
    missing: bool,
}

/// Read `payload["yearly_cycles"]` → `[{contract, start, end}, ...]`.
///
/// Python-injected (never user-supplied): `contract` is a December row of the
/// monthly expiry_calendar and `end` is the T-n exit already snapped to a
/// trading day. Returns `None` when the key is absent; errors on a malformed
/// entry rather than dropping it — a silently-skipped cycle would leave its
/// segments unpinned and trading the cadence contract.
fn extract_yearly_cycles(payload: &PyDict) -> PyResult<Option<Vec<YearlyCycle>>> {
    let item = match payload.get_item("yearly_cycles").ok().flatten() {
        Some(v) => v,
        None => return Ok(None),
    };
    if item.is_none() {
        return Ok(None);
    }
    let list = item.downcast::<PyList>().map_err(|_| {
        pyo3::exceptions::PyValueError::new_err("yearly_cycles must be a list of dicts")
    })?;
    let mut out: Vec<YearlyCycle> = Vec::with_capacity(list.len());
    for (i, row) in list.iter().enumerate() {
        let d = row.downcast::<PyDict>().map_err(|_| {
            pyo3::exceptions::PyValueError::new_err(format!("yearly_cycles[{}] must be a dict", i))
        })?;
        let contract = extract_str(d, "contract");
        let start = extract_str(d, "start");
        let end = extract_str(d, "end");
        if contract.is_empty() || start.is_empty() || end.is_empty() {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "yearly_cycles[{}] needs non-empty contract/start/end (got {:?}/{:?}/{:?})",
                i, contract, start, end
            )));
        }
        if start >= end {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "yearly_cycles[{}] has start >= end ({} >= {}); the window is half-open [start, end)",
                i, start, end
            )));
        }
        out.push(YearlyCycle { contract, start, end });
    }
    Ok(Some(out))
}

fn extract_str(dict: &PyDict, key: &str) -> String {
    dict.get_item(key)
        .ok()
        .flatten()
        .and_then(|v| v.extract::<String>().ok())
        .unwrap_or_default()
}

fn extract_f64(dict: &PyDict, key: &str) -> f64 {
    dict.get_item(key)
        .ok()
        .flatten()
        .and_then(|v| v.extract::<f64>().ok())
        .unwrap_or(0.0)
}

fn extract_i64(dict: &PyDict, key: &str) -> i64 {
    dict.get_item(key)
        .ok()
        .flatten()
        .and_then(|v| v.extract::<i64>().ok())
        .unwrap_or(0)
}

fn dict_to_spec(dict: &PyDict) -> TradeSpec {
    let strike = extract_f64(dict, "strike");
    // Default requested_strike to strike when absent (no shift was tracked).
    let requested_strike = {
        let v = extract_f64(dict, "requested_strike");
        if v > 0.0 { v } else { strike }
    };
    TradeSpec {
        trade_id: extract_i64(dict, "trade_id"),
        leg_id: extract_i64(dict, "leg_id"),
        index: extract_str(dict, "index"),
        entry_date: extract_str(dict, "entry_date"),
        exit_date: extract_str(dict, "exit_date"),
        expiry: extract_str(dict, "expiry"),
        strike,
        requested_strike,
        strike_interval: extract_f64(dict, "strike_interval"),
        option_type: extract_str(dict, "option_type"),
        position: extract_str(dict, "position"),
        lots: extract_i64(dict, "lots"),
        lot_size: extract_i64(dict, "lot_size"),
        slippage_pct: extract_f64(dict, "slippage_pct"),
    }
}

fn simulate_one(s: &TradeSpec) -> TradeResult {
    let raw_entry = lookup_option_price(
        &s.entry_date,
        &s.index,
        s.strike,
        &s.option_type,
        &s.expiry,
    );
    let raw_exit = lookup_option_price(
        &s.exit_date,
        &s.index,
        s.strike,
        &s.option_type,
        &s.expiry,
    );

    let entry_spot = lookup_spot_price(&s.entry_date, &s.index).unwrap_or(0.0);
    let exit_spot = lookup_spot_price(&s.exit_date, &s.index).unwrap_or(0.0);

    let missing = raw_entry.is_none() || raw_exit.is_none();
    if missing {
        return TradeResult {
            entry_price: 0.0,
            exit_price: 0.0,
            raw_entry_price: 0.0,
            raw_exit_price: 0.0,
            entry_spot: round2(entry_spot),
            exit_spot: round2(exit_spot),
            net_pnl: 0.0,
            missing: true,
        };
    }

    let raw_entry = raw_entry.unwrap();
    let raw_exit = raw_exit.unwrap();

    let entry_px = apply_slippage(raw_entry, &s.position, "entry", s.slippage_pct);
    let exit_px = apply_slippage(raw_exit, &s.position, "exit", s.slippage_pct);

    // Engine convention: Net P&L is in PREMIUM POINTS scaled by LOTS, not rupees.
    // For SELL: net = (entry - exit) * lots   (we receive entry, pay exit)
    // For BUY : net = (exit - entry) * lots
    // lot_size is NOT part of P&L — it is informational and downstream uses it
    // for the display Qty column (lots × lot_size) and Turnover only. This
    // mirrors the intraday engine (iengine/src/engine.rs:2549).
    // simulate_trades_batch_core sums these already-scaled per-leg values into
    // the trade total, so the multiplier must NOT be re-applied there.
    let is_sell = s.position.trim().eq_ignore_ascii_case("SELL");
    let lots = s.lots as f64;
    let net_pnl = if is_sell {
        round2((entry_px - exit_px) * lots)
    } else {
        round2((exit_px - entry_px) * lots)
    };

    TradeResult {
        entry_price: entry_px,
        exit_price: exit_px,
        raw_entry_price: round2(raw_entry),
        raw_exit_price: round2(raw_exit),
        entry_spot: round2(entry_spot),
        exit_spot: round2(exit_spot),
        net_pnl,
        missing: false,
    }
}

fn result_to_dict(py: Python<'_>, spec: &TradeSpec, r: &TradeResult) -> PyResult<PyObject> {
    let d = PyDict::new(py);
    d.set_item("trade_id", spec.trade_id)?;
    d.set_item("leg_id", spec.leg_id)?;
    d.set_item("index", &spec.index)?;
    d.set_item("entry_date", &spec.entry_date)?;
    d.set_item("exit_date", &spec.exit_date)?;
    d.set_item("expiry", &spec.expiry)?;
    d.set_item("strike", spec.strike)?;
    d.set_item("requested_strike", spec.requested_strike)?;
    d.set_item("strike_interval", spec.strike_interval)?;
    d.set_item("option_type", &spec.option_type)?;
    d.set_item("position", &spec.position)?;
    d.set_item("lots", spec.lots)?;
    d.set_item("lot_size", spec.lot_size)?;
    d.set_item("slippage_pct", spec.slippage_pct)?;
    d.set_item("entry_price", r.entry_price)?;
    d.set_item("exit_price", r.exit_price)?;
    d.set_item("raw_entry_price", r.raw_entry_price)?;
    d.set_item("raw_exit_price", r.raw_exit_price)?;
    d.set_item("entry_spot", r.entry_spot)?;
    d.set_item("exit_spot", r.exit_spot)?;
    d.set_item("net_pnl", r.net_pnl)?;
    d.set_item("missing", r.missing)?;
    Ok(d.into())
}

/// Per-process rayon thread pool, initialized lazily on first use.
///
/// Using a local (non-global) pool means the parent Celery process never
/// initialises it. After fork(), each child creates its own fresh pool with
/// live threads — no inherited dead workers, no futex deadlock.
/// RAYON_NUM_THREADS controls the size (default: min(cpu_count, 4)).
fn sim_pool() -> &'static rayon::ThreadPool {
    static POOL: Lazy<rayon::ThreadPool> = Lazy::new(|| {
        // RUST_SIM_THREADS controls the local pool independently of the global
        // rayon pool (which Polars may have already claimed via RAYON_NUM_THREADS).
        let n = std::env::var("RUST_SIM_THREADS")
            .ok()
            .and_then(|v| v.parse::<usize>().ok())
            .filter(|&v| v > 0)
            .unwrap_or_else(|| (num_cpus::get()).min(4));
        rayon::ThreadPoolBuilder::new()
            .num_threads(n)
            .build()
            .expect("sim rayon pool init failed")
    });
    &POOL
}

/// Simulate a batch of pre-resolved trades. See module docstring.
///
/// Lookups hit the shared Rust market cache (CACHE in lib.rs); slippage and
/// P&L computation are pure functions. Uses a process-local rayon pool so
/// forked optimizer workers each create their own fresh thread pool rather
/// than inheriting the parent's (which would deadlock after fork).
/// Pure-Rust core of `simulate_trades_batch` — no PyO3, no GIL, so the Rust
/// combo loop (Phase 1) can call it directly on Rust-built specs instead of
/// crossing the PyO3 boundary per combo. The `#[pyfunction]` wrapper below does
/// the dict↔struct conversion and calls this unchanged.
///
/// Prices a slice of pre-resolved specs (Rayon-parallel over the process-local
/// pool) then applies the Python engine's row-level post-processing:
///   1. Drop EVERY leg of any trade where at least one leg is `missing` — the
///      Python engine skips the whole trade if it can't price one leg, so a
///      partial row would diverge from the snapshot.
///   2. For surviving trades, the FIRST row matching the LOWEST leg_id reports
///      the SUM of all per-leg net_pnl as its own net_pnl; other legs keep
///      their per-leg net_pnl. Subsequent rows with the same (trade_id, leg_id)
///      are re-entries (slice 6) and keep their own per-leg P&L. This mirrors
///      the engine's "Trade Net P&L" column layout the parity tests check.
///
/// Returns the priced results (same order/length as `specs`) plus the set of
/// `trade_id`s that had a missing leg — callers drop every row of those trades.
pub(crate) fn simulate_trades_batch_core(
    specs: &[TradeSpec],
) -> (Vec<TradeResult>, std::collections::HashSet<i64>) {
    // lookup_option_price uses RwLock::read() — safe from multiple threads.
    let mut results: Vec<TradeResult> =
        sim_pool().install(|| specs.par_iter().map(simulate_one).collect());

    let mut bad_trades: std::collections::HashSet<i64> = std::collections::HashSet::new();
    for (s, r) in specs.iter().zip(results.iter()) {
        if r.missing {
            bad_trades.insert(s.trade_id);
        }
    }

    let mut trade_totals: std::collections::HashMap<i64, (f64, i64)> = std::collections::HashMap::new();
    for (s, r) in specs.iter().zip(results.iter()) {
        if bad_trades.contains(&s.trade_id) {
            continue;
        }
        let entry = trade_totals.entry(s.trade_id).or_insert((0.0, i64::MAX));
        entry.0 += r.net_pnl;
        if s.leg_id < entry.1 {
            entry.1 = s.leg_id;
        }
    }
    let mut total_assigned: std::collections::HashSet<i64> = std::collections::HashSet::new();
    for (s, r) in specs.iter().zip(results.iter_mut()) {
        if let Some(&(total, lowest_leg)) = trade_totals.get(&s.trade_id) {
            if s.leg_id == lowest_leg && !total_assigned.contains(&s.trade_id) {
                r.net_pnl = round2(total);
                total_assigned.insert(s.trade_id);
            }
        }
    }

    (results, bad_trades)
}

#[pyfunction]
pub fn simulate_trades_batch(trades: &PyList) -> PyResult<PyObject> {
    let py = trades.py();

    let mut specs: Vec<TradeSpec> = Vec::with_capacity(trades.len());
    for obj in trades.iter() {
        let dict = obj.downcast::<PyDict>()?;
        specs.push(dict_to_spec(dict));
    }

    // Release the GIL for the whole pure-Rust core (pricing + post-processing);
    // none of it touches Python objects.
    let (results, bad_trades) = py.allow_threads(|| simulate_trades_batch_core(&specs));

    let out = PyList::empty(py);
    for (spec, result) in specs.iter().zip(results.iter()) {
        if bad_trades.contains(&spec.trade_id) {
            continue;
        }
        out.append(result_to_dict(py, spec, result)?)?;
    }
    Ok(out.into())
}

// ── Slice 4b: SL-with-Buffer ───────────────────────────────────────────────
//
// Mirrors the Python algorithm at engines/generic_algotest_engine.py
// (`_compute_sl_buffer_exit` + the per-leg loop in `check_leg_stop_loss_target`).
//
// Two branches by mode:
//
//   pct / points (option-price level):
//     1. Compute SL_price from entry_premium + sl_buffer_value.
//          SELL pct  : entry * (1 + v/100)        SELL points : entry + v
//          BUY  pct  : entry * (1 - v/100)        BUY  points : entry - v
//     2. Each holding day, fetch day Open/High/Low for the option.
//     3. If day_open is known AND on the gap side of SL_price (open>SL for SELL,
//        open<SL for BUY): GAP. Exit = open*(1±buf%/100) capped at day_high/low.
//     4. Else if day_high>=SL (SELL) or day_low<=SL (BUY): intraday hit. Exit at
//        SL_price exactly (NO buffer applied).
//     5. Else: no SL hit today.
//
//   underlying_pts / underlying_pct (spot-anchored):
//     Detect via adverse-vs-threshold on spot. On the day of hit, look back one
//     day: if yesterday's spot move was below threshold, treat as a gap and
//     apply buffer to today's option open (capped). Otherwise no override —
//     caller's normal exit-price logic uses today's close.

use pyo3::types::PyTuple;

#[derive(Debug, Clone)]
struct SlBufferCfg {
    enabled: bool,
    value: f64,
    mode: String,        // pct / points / underlying_pts / underlying_pct
    buffer_pct: f64,
    entry_premium: f64,
    entry_spot: f64,
}

fn extract_sl_buffer_cfg(leg: &PyDict, entry_premium: f64, entry_spot: f64) -> SlBufferCfg {
    let sl_buf = match leg.get_item("slWithBuffer").ok().flatten() {
        Some(v) => match v.downcast::<PyDict>() {
            Ok(d) => d,
            Err(_) => return SlBufferCfg {
                enabled: false, value: 0.0, mode: String::new(), buffer_pct: 0.0,
                entry_premium, entry_spot,
            },
        },
        None => return SlBufferCfg {
            enabled: false, value: 0.0, mode: String::new(), buffer_pct: 0.0,
            entry_premium, entry_spot,
        },
    };
    let value = sl_buf.get_item("value").ok().flatten()
        .and_then(|v| v.extract::<f64>().ok()).unwrap_or(0.0);
    let mode = sl_buf.get_item("mode").ok().flatten()
        .and_then(|v| v.extract::<String>().ok())
        .map(|s| {
            let s = s.to_uppercase().replace(' ', "_").replace('-', "_");
            match s.as_str() {
                "PERCENT" | "PCT" | "%" => "pct".to_string(),
                "POINTS" | "PT" | "PTS" => "points".to_string(),
                "UNDERLYING_POINTS" | "UNDERLYING_PTS" => "underlying_pts".to_string(),
                "UNDERLYING_PCT" | "UNDERLYING_PERCENT" => "underlying_pct".to_string(),
                _ => s.to_lowercase(),
            }
        })
        .unwrap_or_else(|| "pct".to_string());
    let buffer_pct = sl_buf.get_item("buffer_pct").ok().flatten()
        .and_then(|v| v.extract::<f64>().ok()).unwrap_or(0.0);
    let enabled = value != 0.0;
    SlBufferCfg { enabled, value, mode, buffer_pct, entry_premium, entry_spot }
}

/// Compute the adverse move on `check_date` against entry, per the engine's
/// mode-specific formula. Returns None if data is missing.
fn adverse_value(
    cfg: &SlBufferCfg,
    check_date: &str,
    expiry: &str,
    index: &str,
    strike: f64,
    opt_type: &str,
    position: &str,
) -> Option<f64> {
    let is_sell = position.eq_ignore_ascii_case("SELL");
    let is_call = opt_type.eq_ignore_ascii_case("CE")
        || opt_type.eq_ignore_ascii_case("CALL");
    match cfg.mode.as_str() {
        "pct" | "points" => {
            let cp = lookup_option_price(check_date, index, strike, opt_type, expiry)?;
            let move_pts = cp - cfg.entry_premium;
            let adverse_pts = if is_sell { move_pts } else { -move_pts };
            if cfg.mode == "points" {
                Some(adverse_pts)
            } else if cfg.entry_premium.abs() > 0.0 {
                Some(adverse_pts / cfg.entry_premium * 100.0)
            } else {
                None
            }
        }
        "underlying_pts" | "underlying_pct" => {
            let spot = lookup_spot_price(check_date, index)?;
            let spot_move = spot - cfg.entry_spot;
            // CE+SELL: adverse if spot up; CE+BUY: adverse if spot down
            // PE+SELL: adverse if spot down; PE+BUY: adverse if spot up
            let adverse_spot = if is_call {
                if is_sell { spot_move } else { -spot_move }
            } else if is_sell { -spot_move } else { spot_move };
            if cfg.mode == "underlying_pts" {
                Some(adverse_spot)
            } else if cfg.entry_spot.abs() > 0.0 {
                Some(adverse_spot / cfg.entry_spot * 100.0)
            } else {
                None
            }
        }
        _ => None,
    }
}

/// Python signature:
///   apply_sl_with_buffer_batch(
///       specs: List[dict],           # priced trades (have entry_price, entry_spot)
///       legs_payload: List[dict],    # original payload legs (1 per spec.leg_id)
///       trading_days: List[str],
///   ) -> List[Optional[Tuple[str, float]]]
///
/// Returns one entry per spec: `None` if SL-with-Buffer didn't fire (or isn't
/// enabled), else `(trigger_date, override_price)`. Caller applies the
/// override directly — no re-pricing needed.
#[pyfunction]
pub fn apply_sl_with_buffer_batch(
    specs: &PyList,
    legs_payload: &PyList,
    trading_days: Vec<String>,
) -> PyResult<PyObject> {
    let py = specs.py();
    let mut td = trading_days;
    td.sort();

    let out = PyList::empty(py);
    for spec_obj in specs.iter() {
        let spec = spec_obj.downcast::<PyDict>()?;
        let leg_id = spec.get_item("leg_id").ok().flatten()
            .and_then(|v| v.extract::<i64>().ok()).unwrap_or(1);
        let leg_idx = (leg_id - 1).max(0) as usize;
        let leg_src = if leg_idx < legs_payload.len() {
            legs_payload.get_item(leg_idx)?.downcast::<PyDict>()?
        } else {
            out.append(py.None())?;
            continue;
        };
        let entry_premium = spec.get_item("entry_price").ok().flatten()
            .and_then(|v| v.extract::<f64>().ok()).unwrap_or(0.0);
        let entry_spot = spec.get_item("entry_spot").ok().flatten()
            .and_then(|v| v.extract::<f64>().ok()).unwrap_or(0.0);
        let cfg = extract_sl_buffer_cfg(leg_src, entry_premium, entry_spot);
        if !cfg.enabled {
            out.append(py.None())?;
            continue;
        }

        let entry_date = spec.get_item("entry_date").ok().flatten()
            .and_then(|v| v.extract::<String>().ok()).unwrap_or_default();
        let exit_date = spec.get_item("exit_date").ok().flatten()
            .and_then(|v| v.extract::<String>().ok()).unwrap_or_default();
        let expiry = spec.get_item("expiry").ok().flatten()
            .and_then(|v| v.extract::<String>().ok()).unwrap_or_default();
        let index = spec.get_item("index").ok().flatten()
            .and_then(|v| v.extract::<String>().ok()).unwrap_or_else(|| "NIFTY".to_string());
        let strike = spec.get_item("strike").ok().flatten()
            .and_then(|v| v.extract::<f64>().ok()).unwrap_or(0.0);
        let opt_type = spec.get_item("option_type").ok().flatten()
            .and_then(|v| v.extract::<String>().ok()).unwrap_or_else(|| "CE".to_string());
        let position = spec.get_item("position").ok().flatten()
            .and_then(|v| v.extract::<String>().ok()).unwrap_or_else(|| "SELL".to_string());

        // Holding window: trading days STRICTLY AFTER entry, up to AND including exit.
        let holding: Vec<&String> = td.iter()
            .filter(|d| d.as_str() > entry_date.as_str() && d.as_str() <= exit_date.as_str())
            .collect();
        if holding.is_empty() {
            out.append(py.None())?;
            continue;
        }

        let abs_thr = cfg.value.abs();
        let is_sell = position.eq_ignore_ascii_case("SELL");
        let buffer_pct = cfg.buffer_pct;
        let mut triggered: Option<(String, f64)> = None;

        // Precompute the option-price SL level for pct/points modes.
        let sl_price_opt: Option<f64> = match cfg.mode.as_str() {
            "pct" => {
                if cfg.entry_premium > 0.0 {
                    Some(if is_sell {
                        cfg.entry_premium * (1.0 + abs_thr / 100.0)
                    } else {
                        cfg.entry_premium * (1.0 - abs_thr / 100.0)
                    })
                } else { None }
            }
            "points" => {
                if cfg.entry_premium > 0.0 {
                    Some(if is_sell {
                        cfg.entry_premium + abs_thr
                    } else {
                        cfg.entry_premium - abs_thr
                    })
                } else { None }
            }
            _ => None,
        };

        if let Some(sl_price) = sl_price_opt {
            // pct / points: option-price level. Each day, check OHLC against SL.
            for day in holding.iter() {
                let day_str = day.as_str();
                let day_open = lookup_option_open(day_str, &index, strike, &opt_type, &expiry);
                let day_high = lookup_option_high(day_str, &index, strike, &opt_type, &expiry);
                let day_low  = lookup_option_low (day_str, &index, strike, &opt_type, &expiry);

                if is_sell {
                    // Gap: open strictly above SL_price.
                    if let Some(op) = day_open {
                        if op > sl_price {
                            let buf = op * (1.0 + buffer_pct / 100.0);
                            let override_price = match day_high {
                                Some(h) => buf.min(h),
                                None => buf,
                            };
                            triggered = Some((day.to_string(), round2(override_price.max(0.0))));
                            break;
                        }
                    }
                    // Intraday hit needs day_high >= SL.
                    if let Some(h) = day_high {
                        if h >= sl_price {
                            triggered = Some((day.to_string(), round2(sl_price.max(0.0))));
                            break;
                        }
                    }
                } else {
                    if let Some(op) = day_open {
                        if op < sl_price {
                            let buf = op * (1.0 - buffer_pct / 100.0);
                            let override_price = match day_low {
                                Some(l) => buf.max(l),
                                None => buf,
                            };
                            triggered = Some((day.to_string(), round2(override_price.max(0.0))));
                            break;
                        }
                    }
                    if let Some(l) = day_low {
                        if l <= sl_price {
                            triggered = Some((day.to_string(), round2(sl_price.max(0.0))));
                            break;
                        }
                    }
                }
            }
        } else {
            // underlying_pts / underlying_pct: spot-anchored detection. The
            // exit price is anchored to today's option HIGH (SELL) / LOW (BUY)
            // — NEVER close. Apply buffer to today's open, then cap at high/low
            // so the fill price reflects the worst-case realised intraday print.
            for day in holding.iter() {
                let day_str = day.as_str();
                let current_adverse = adverse_value(
                    &cfg, day_str, &expiry, &index, strike, &opt_type, &position,
                );
                let Some(cur) = current_adverse else { continue };
                if cur < abs_thr {
                    continue;
                }
                let day_open = lookup_option_open(day_str, &index, strike, &opt_type, &expiry);
                let override_opt = if is_sell {
                    let day_high = lookup_option_high(day_str, &index, strike, &opt_type, &expiry);
                    match (day_open, day_high) {
                        (Some(op), Some(h)) => Some((op * (1.0 + buffer_pct / 100.0)).min(h)),
                        (Some(op), None)    => Some(op * (1.0 + buffer_pct / 100.0)),
                        (None, Some(h))     => Some(h),
                        (None, None)        => None,
                    }
                } else {
                    let day_low = lookup_option_low(day_str, &index, strike, &opt_type, &expiry);
                    match (day_open, day_low) {
                        (Some(op), Some(l)) => Some((op * (1.0 - buffer_pct / 100.0)).max(l)),
                        (Some(op), None)    => Some(op * (1.0 - buffer_pct / 100.0)),
                        (None, Some(l))     => Some(l),
                        (None, None)        => None,
                    }
                };
                if let Some(price) = override_opt {
                    triggered = Some((day.to_string(), round2(price.max(0.0))));
                    break;
                }
                // No OHLC at all — skip (don't fire on bad data).
            }
        }

        match triggered {
            Some((d, p)) => {
                let t = PyTuple::new(py, &[d.into_py(py), p.into_py(py)]);
                out.append(t)?;
            }
            None => {
                out.append(py.None())?;
            }
        }
    }
    Ok(out.into())
}

#[cfg(test)]
mod rollover_schedule_tests {
    use super::*;

    // Real NIFTY 2019 monthly expiries (the roll anchor agreed for YEARLY).
    fn monthly_2019() -> Vec<String> {
        [
            "2019-02-28", "2019-03-28", "2019-04-25", "2019-05-30", "2019-06-27", "2019-07-25",
            "2019-08-29", "2019-09-26", "2019-10-31", "2019-11-28", "2019-12-26",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect()
    }

    // Trading days: the expiries themselves plus the T-1 exit date. Enough for
    // trading_day_before(exp, 0) == exp, which is what the T0/T0 chain uses.
    fn trading_days() -> Vec<String> {
        let mut v = monthly_2019();
        v.push("2019-11-26".to_string());
        v.sort();
        v
    }

    fn cyc(contract: &str, start: &str, end: &str) -> YearlyCycle {
        YearlyCycle { contract: contract.into(), start: start.into(), end: end.into() }
    }

    #[test]
    fn calendar_dte_subtracts_calendar_days_then_snaps_to_market_day() {
        let td = ["2026-03-26", "2026-03-27", "2026-03-30"]
            .iter().map(|s| s.to_string()).collect::<Vec<_>>();
        // 30-Mar T-2 calendar = Saturday 28-Mar, so execution snaps to Fri 27-Mar.
        assert_eq!(
            day_before_expiry("2026-03-30", 2, &td, true).as_deref(),
            Some("2026-03-27"),
        );
        // Legacy trading basis counts two sessions back from Monday => Thursday.
        assert_eq!(
            day_before_expiry("2026-03-30", 2, &td, false).as_deref(),
            Some("2026-03-26"),
        );
    }

    /// UNPINNED (every existing weekly/monthly run): contract == cadence element.
    /// The T0/T0 same-day chain yields entry = prev expiry, exit = cur expiry.
    #[test]
    fn unpinned_contract_is_the_cadence_element() {
        let out = build_rollover_schedule_pinned(
            &monthly_2019(), &trading_days(), 0, 0, 0, None, false,
        );
        // Record 1 is skipped (entry == exit) and seeds the chain.
        assert_eq!(out.len(), 10);
        assert_eq!(out[0].1, "2019-02-28");   // entry = prev expiry
        assert_eq!(out[0].2, "2019-03-28");   // exit  = cur expiry
        assert_eq!(out[0].3, "2019-03-28");   // leg_expiry = cadence element
        assert_eq!(out[1].1, "2019-03-28");
        assert_eq!(out[1].2, "2019-04-25");
        assert_eq!(out[1].3, "2019-04-25");
        // leg_expiry always tracks cur_exp when unpinned.
        for r in &out {
            assert_eq!(r.3, r.4, "unpinned: leg_expiry must equal cur_exp");
        }
    }

    // Feb–Mar 2026 weekdays — dense enough for weekly T-1 and monthly T-7.
    fn weekdays_feb_mar_2026() -> Vec<String> {
        let mut v: Vec<String> = [
            "2026-02-02","2026-02-03","2026-02-04","2026-02-05","2026-02-06",
            "2026-02-09","2026-02-10","2026-02-11","2026-02-12","2026-02-13",
            "2026-02-16","2026-02-17","2026-02-18","2026-02-19","2026-02-20",
            "2026-02-23","2026-02-24","2026-02-25","2026-02-26","2026-02-27",
            "2026-03-02","2026-03-03","2026-03-04","2026-03-05","2026-03-06",
            "2026-03-09","2026-03-10","2026-03-11","2026-03-12","2026-03-13",
            "2026-03-16","2026-03-17","2026-03-18","2026-03-19","2026-03-20",
            "2026-03-23","2026-03-24","2026-03-25","2026-03-26","2026-03-27",
            "2026-03-30","2026-03-31",
        ].iter().map(|s| s.to_string()).collect();
        v.sort();
        v
    }

    fn find_row<'a>(
        rows: &'a [(i64, String, String, Vec<PerLegSlot>)],
        start: &str,
    ) -> &'a (i64, String, String, Vec<PerLegSlot>) {
        rows.iter().find(|r| r.1 == start).unwrap_or_else(|| panic!("no row starting {start}"))
    }

    /// PER-LEG ROLLOVER: weekly leg (T-1) + monthly leg (T-7). Trade boundaries
    /// are the UNION of both legs' rolls; the weekly leg CARRIES across the
    /// monthly boundary (17-Feb) while the monthly leg rolls, exactly as agreed.
    #[test]
    fn per_leg_union_carry_across_foreign_boundary() {
        let td = weekdays_feb_mar_2026();
        let weekly = vec![
            "2026-02-05","2026-02-12","2026-02-19","2026-02-26",
            "2026-03-05","2026-03-12","2026-03-19","2026-03-26",
        ].iter().map(|s| s.to_string()).collect::<Vec<_>>();
        let monthly = vec!["2026-02-26".to_string(), "2026-03-26".to_string()];
        let rows = build_rollover_schedule_per_leg(
            "2026-02-02",
            &[weekly, monthly],
            &[1, 7],          // weekly T-1, monthly T-7
            &[None, None],
            &[Vec::new(), Vec::new()],  // no spot-adj breaches
            &[],                        // no filter segments
            &td,
        );

        // 17-Feb (monthly T-7) is a boundary even though no weekly roll is there.
        let r = find_row(&rows, "2026-02-17");
        assert_eq!(r.3[0].own_boundary, false, "weekly carries across the monthly boundary");
        assert_eq!(r.3[0].contract, "2026-02-19", "weekly keeps its held contract");
        assert_eq!(r.3[1].own_boundary, true, "monthly rolls on its own T-7");
        assert_eq!(r.3[1].contract, "2026-03-26", "monthly rolled to the next contract");

        // 04-Feb (weekly T-1) — weekly rolls, monthly carries its Feb contract.
        let r = find_row(&rows, "2026-02-04");
        assert_eq!(r.3[0].own_boundary, true, "weekly rolls on its own T-1");
        assert_eq!(r.3[0].contract, "2026-02-12");
        assert_eq!(r.3[1].own_boundary, false, "monthly carries across the weekly boundary");
        assert_eq!(r.3[1].contract, "2026-02-26");

        // Run start — the FRONT-MONTH opening window is NOT emitted as a trade.
        // Under rollover no leg enters at an arbitrary run_start (the pinned
        // same-day chain 0-DTE-skips the front-month and enters on the first
        // roll), so there is no row starting at run_start. Each leg still makes
        // its FIRST real pick at its own first roll boundary below.
        assert!(rows.iter().all(|r| r.1 != "2026-02-02"),
            "no trade is emitted for the front-month opening window at run_start");
        // Weekly's first real entry is its first roll (04-Feb), holding the next
        // weekly contract; monthly's first real entry is its first roll (17-Feb).
        let r = find_row(&rows, "2026-02-04");
        assert!(r.3[0].own_boundary, "weekly's first emitted row is its own first roll");
    }

    /// REGRESSION (money path): a SINGLE monthly leg under per_leg_rollover must
    /// produce the SAME schedule as the normal (pinned/unpinned) monthly path —
    /// one leg has no foreign boundaries, so the union collapses to the plain
    /// monthly chain. Before the front-month-stub fix, per-leg invented an extra
    /// opening trade [run_start, first_expiry) on the front-month and shifted
    /// every downstream row by one contract. Assert (entry, exit, contract) match
    /// the pinned builder row-for-row (T0/T0).
    #[test]
    fn single_leg_per_leg_equals_normal_monthly_schedule() {
        // Mirror a real backtest: run_start is a trading day strictly BEFORE the
        // first in-range expiry (as when from_date lands between expiries). Both
        // builders then 0-DTE-skip the front-month (MARCH) and enter on it via
        // the chain, so trade 1 = 28-Mar → 25-Apr holding APRIL for BOTH paths.
        // Expiries seen by the run start at the March expiry (Feb-28 out of range).
        let expiries: Vec<String> = [
            "2019-03-28", "2019-04-25", "2019-05-30", "2019-06-27", "2019-07-25",
            "2019-08-29", "2019-09-26", "2019-10-31", "2019-11-28", "2019-12-26",
        ].iter().map(|s| s.to_string()).collect();
        let mut td = expiries.clone();
        td.push("2019-03-01".to_string()); // run_start (before first expiry)
        td.sort();
        let run_start = "2019-03-01".to_string();

        // Normal (unpinned) monthly T0/T0 — the reference. Its same-day chain
        // 0-DTE-skips MARCH and emits [28-Mar, 25-Apr] holding APRIL first.
        let normal = build_rollover_schedule_pinned(&expiries, &td, 0, 0, 0, None, false);
        // Single monthly leg, exit T-0, no cycles, no breaches.
        let per_leg = build_rollover_schedule_per_leg(
            &run_start, &[expiries.clone()], &[0], &[None], &[Vec::new()], &[], &td,
        );

        assert_eq!(
            per_leg.len(), normal.len(),
            "single-leg per-leg must emit the same number of trades as normal \
             (no extra front-month opening trade)"
        );
        for (i, (pl, nm)) in per_leg.iter().zip(normal.iter()).enumerate() {
            // pl = (trade_id, start, end, [slot]); nm = (trade_id, entry, exit, leg_expiry, orig, new_cycle)
            assert_eq!(pl.1, nm.1, "trade {i}: entry date must match normal");
            assert_eq!(pl.2, nm.2, "trade {i}: exit date must match normal");
            assert_eq!(pl.3[0].contract, nm.3, "trade {i}: contract must match normal");
        }
        // Explicitly: first trade is 28-Mar → 25-Apr holding APRIL, NOT the
        // dropped 01-Mar → 28-Mar front-month stub.
        assert_eq!((per_leg[0].1.as_str(), per_leg[0].2.as_str()), ("2019-03-28", "2019-04-25"));
        assert_eq!(per_leg[0].3[0].contract, "2019-04-25");
    }

    /// REGRESSION (money path): the long-form strike token that `extract_strike_sel`
    /// now composes for {type:"itm"/"otm", value:N} must resolve to a real strike.
    /// A bare "ITM"/"OTM" (the pre-fix behaviour) parses an empty suffix → None →
    /// every trade drops → empty specs (the no-filter per-leg-rollover bug).
    #[test]
    fn composed_itm_otm_tokens_resolve() {
        let spot = 20_040.0;
        let gap = 100.0; // ATM = 20_000
        // CE: ITM below ATM, OTM above.
        assert_eq!(atm_offset_strike(spot, gap, "ITM5", "CE"), Some(19_500.0));
        assert_eq!(atm_offset_strike(spot, gap, "OTM2", "CE"), Some(20_200.0));
        assert_eq!(atm_offset_strike(spot, gap, "ITM0", "CE"), Some(20_000.0));
        // PE mirrors.
        assert_eq!(atm_offset_strike(spot, gap, "ITM5", "PE"), Some(20_500.0));
        // Bare token (what the bug produced) is unresolvable — guards the fix.
        assert_eq!(atm_offset_strike(spot, gap, "ITM", "CE"), None);
    }

    /// REGRESSION (money path): a filter segment opening MID-CONTRACT must emit a
    /// row entering the ACTIVE contract fresh at the segment start — mirroring the
    /// non-per-leg fixed-entry path. Without seg_starts the union only breaks at
    /// rolls, so a segment opening between two monthly rolls has no row and the
    /// active contract is dropped by the downstream entry-in-window gate.
    #[test]
    fn seg_start_opens_row_on_active_contract() {
        // Single monthly leg, T0/T0. run_start before first expiry.
        let expiries: Vec<String> = [
            "2019-03-28", "2019-04-25", "2019-05-30", "2019-06-27",
        ].iter().map(|s| s.to_string()).collect();
        let mut td = expiries.clone();
        td.push("2019-03-01".to_string());
        td.push("2019-04-01".to_string()); // a trading day mid-APRIL-contract
        td.sort();

        // No seg_starts → normal schedule: first row 28-Mar → 25-Apr (APRIL).
        let base = build_rollover_schedule_per_leg(
            "2019-03-01", &[expiries.clone()], &[0], &[None], &[Vec::new()], &[], &td,
        );
        assert_eq!(base[0].1.as_str(), "2019-03-28");

        // Segment opens 01-Apr, MID the APRIL contract (28-Mar..25-Apr). A row must
        // now START at 01-Apr holding APRIL (2019-04-25), flagged seg_start_entry
        // (it is NOT the leg's own roll), forcing a fresh re-strike there.
        let with_seg = build_rollover_schedule_per_leg(
            "2019-03-01", &[expiries.clone()], &[0], &[None], &[Vec::new()],
            &["2019-04-01".to_string()], &td,
        );
        let r = with_seg.iter().find(|r| r.1 == "2019-04-01")
            .expect("a row must open at the mid-contract segment start");
        assert_eq!(r.3[0].contract, "2019-04-25", "row holds the ACTIVE (APRIL) contract");
        assert!(r.3[0].seg_start_entry, "mid-hold segment start forces fresh re-strike");
        assert!(!r.3[0].own_boundary, "segment start is NOT the leg's own roll");
        // A seg_start that coincides with a leg's own roll stays own_boundary and
        // is NOT double-flagged.
        let with_roll_seg = build_rollover_schedule_per_leg(
            "2019-03-01", &[expiries.clone()], &[0], &[None], &[Vec::new()],
            &["2019-04-25".to_string()], &td,
        );
        let r = with_roll_seg.iter().find(|r| r.1 == "2019-04-25")
            .expect("the own-roll row still exists");
        assert!(r.3[0].own_boundary, "own roll stays own_boundary");
        assert!(!r.3[0].seg_start_entry, "own roll is not double-flagged as seg_start_entry");
    }

    /// PINNED, T=0: every segment holds the December contract while entry/exit
    /// still follow the monthly cadence — the reference fixture.
    #[test]
    fn pinned_holds_december_across_every_cadence_segment() {
        let cycles = vec![cyc("2019-12-26", "2019-01-01", "2019-12-26")];
        let out = build_rollover_schedule_pinned(
            &monthly_2019(), &trading_days(), 0, 0, 0, Some(&cycles), false,
        );
        assert_eq!(out.len(), 10);
        for r in &out {
            assert_eq!(r.3, "2019-12-26", "pinned: leg_expiry must be the December contract");
        }
        // Cadence drives entry/exit — matches the fixture table exactly.
        assert_eq!((out[0].1.as_str(), out[0].2.as_str()), ("2019-02-28", "2019-03-28"));
        assert_eq!((out[1].1.as_str(), out[1].2.as_str()), ("2019-03-28", "2019-04-25"));
        assert_eq!((out[2].1.as_str(), out[2].2.as_str()), ("2019-04-25", "2019-05-30"));
        // Only the first trade opens the cycle.
        assert!(out[0].5, "first trade opens the cycle");
        assert!(out[1..].iter().all(|r| !r.5), "no later trade opens a new cycle");
    }

    /// THE CHAINED-ENTRY TRAP. For cadence element 2019-12-26 the step-1 entry
    /// (dte=0) is 2019-12-26, which is NOT in cycle 1's half-open window and
    /// would drop or mis-pin the trade. The CHAINED entry is 2019-11-28, which
    /// is — so the last segment of the year must still hold Dec-2019.
    #[test]
    fn pinned_last_segment_of_year_holds_the_current_december() {
        let cycles = vec![cyc("2019-12-26", "2019-01-01", "2019-12-26")];
        let out = build_rollover_schedule_pinned(
            &monthly_2019(), &trading_days(), 0, 0, 0, Some(&cycles), false,
        );
        let last = out.last().unwrap();
        assert_eq!(last.1, "2019-11-28", "chained entry, not the step-1 entry");
        assert_eq!(last.2, "2019-12-26");
        assert_eq!(last.3, "2019-12-26", "must NOT roll to the next December early");
    }

    /// T-1 (snap-to-cadence): the yearly roll lands ON a cadence boundary, never
    /// mid-segment. A segment holds the first December it can keep for its WHOLE
    /// cadence period, so no 1-day stub is produced.
    #[test]
    fn pinned_tn_rolls_on_a_cadence_boundary_without_a_stub() {
        // T-1 for Dec-2019 (26-Dec) = 26-Nov. Cadence: ... 31-Oct, 28-Nov, 26-Dec.
        let cycles = vec![
            cyc("2019-12-26", "2019-01-01", "2019-11-26"),
            cyc("2020-12-31", "2019-11-26", "2020-11-26"),
        ];
        let out = build_rollover_schedule_pinned(
            &monthly_2019(), &trading_days(), 0, 0, 0, Some(&cycles), false,
        );
        // 31-Oct -> 28-Nov exits AFTER the T-1 boundary (26-Nov), so it must open
        // on the NEXT December rather than truncate.
        let t = out.iter().find(|r| r.1 == "2019-10-31").expect("31-Oct segment");
        assert_eq!(t.2, "2019-11-28", "exit stays on the cadence boundary (no truncation)");
        assert_eq!(t.3, "2020-12-31", "must roll to the next December at the cadence boundary");
        assert!(t.5, "opens a new cycle");

        // No stub: every segment runs cadence-to-cadence, and no exit lands on
        // the raw T-n date.
        assert!(out.iter().all(|r| r.2 != "2019-11-26"), "no 1-day stub at the T-n date");
        for w in out.windows(2) {
            assert_eq!(w[0].2, w[1].1, "gap between {:?} and {:?}", w[0], w[1]);
        }
    }

    /// REGRESSION: no cadence boundary may be skipped across a yearly roll.
    /// Every monthly expiry in the window must appear as a segment exit.
    #[test]
    fn no_cadence_boundary_is_skipped_across_the_roll() {
        let cycles = vec![
            cyc("2019-12-26", "2019-01-01", "2019-11-26"),
            cyc("2020-12-31", "2019-11-26", "2020-11-26"),
        ];
        let out = build_rollover_schedule_pinned(
            &monthly_2019(), &trading_days(), 0, 0, 0, Some(&cycles), false,
        );
        for e in ["2019-03-28", "2019-11-28", "2019-12-26"] {
            assert!(out.iter().any(|r| r.2 == e), "cadence boundary {e} missing from exits");
        }
        for w in out.windows(2) {
            assert_eq!(w[0].2, w[1].1, "hole between {:?} and {:?}", w[0], w[1]);
        }
    }

    /// min-DTE must be inert when pinned — otherwise it would advance
    /// leg_expiry to the next CADENCE element, swapping December for a weekly.
    #[test]
    fn min_dte_is_inert_when_pinned() {
        let cycles = vec![cyc("2019-12-26", "2019-01-01", "2019-12-26")];
        let with_min = build_rollover_schedule_pinned(
            &monthly_2019(), &trading_days(), 0, 0, 5, Some(&cycles), false,
        );
        let without = build_rollover_schedule_pinned(
            &monthly_2019(), &trading_days(), 0, 0, 0, Some(&cycles), false,
        );
        assert_eq!(with_min, without, "min-DTE must not perturb the pinned schedule");
        for r in &with_min {
            assert_eq!(r.3, "2019-12-26");
        }
    }
}

#[cfg(test)]
mod strike_epoch_tests {
    use super::*;

    // Fresh re-strikes at MONTH-END only, regardless of roll cadence; Fixed only
    // at a yearly-cycle boundary. Both are the same mechanism with a different
    // reset trigger, so one predicate covers all four combos.

    const WEEKLY_LEG: bool = false; // leg.is_yearly — a short-dated (weekly/monthly) leg
    const YEARLY_LEG: bool = true;  // leg.is_yearly — the long-dated December leg

    #[test]
    fn fresh_short_dated_leg_restrikes_every_entry() {
        // Verified NIFTY Oct-Nov 2022 column: the weekly leg tracks spot on
        // every entry, INCLUDING several inside the same month.
        assert!(opens_new_epoch(StrikeMode::Fresh, "2022-10-04", "2022-10-12", false, WEEKLY_LEG));
        assert!(opens_new_epoch(StrikeMode::Fresh, "2022-10-12", "2022-10-17", false, WEEKLY_LEG));
        assert!(opens_new_epoch(StrikeMode::Fresh, "2022-10-25", "2022-10-31", false, WEEKLY_LEG));
        // …and across a month boundary, obviously.
        assert!(opens_new_epoch(StrikeMode::Fresh, "2022-10-31", "2022-11-02", false, WEEKLY_LEG));
    }

    #[test]
    fn fresh_yearly_leg_holds_strike_within_a_month() {
        // The Mar-2019 sheet: 11000 held across every weekly roll in March,
        // re-struck on the first roll of April. That sheet describes the
        // LONG-DATED leg, which stays month-wise.
        assert!(!opens_new_epoch(StrikeMode::Fresh, "2019-03-07", "2019-03-14", false, YEARLY_LEG));
        assert!(!opens_new_epoch(StrikeMode::Fresh, "2019-03-14", "2019-03-21", false, YEARLY_LEG));
        assert!(!opens_new_epoch(StrikeMode::Fresh, "2019-03-21", "2019-03-28", false, YEARLY_LEG));
        assert!(opens_new_epoch(StrikeMode::Fresh, "2019-03-28", "2019-04-04", false, YEARLY_LEG));
    }

    #[test]
    fn monthly_cadence_fresh_restrikes_every_entry_on_both_legs() {
        // Consecutive monthly entries land in different months, so BOTH legs
        // re-strike — the two Fresh rules agree here.
        for leg in [WEEKLY_LEG, YEARLY_LEG] {
            assert!(opens_new_epoch(StrikeMode::Fresh, "2019-02-28", "2019-03-28", false, leg));
            assert!(opens_new_epoch(StrikeMode::Fresh, "2019-03-28", "2019-04-25", false, leg));
        }
    }

    #[test]
    fn fixed_never_restrikes_within_a_cycle_on_either_leg() {
        for leg in [WEEKLY_LEG, YEARLY_LEG] {
            for (a, b) in [("2019-03-07", "2019-03-14"), ("2019-03-28", "2019-04-25"),
                           ("2019-02-28", "2019-11-28")] {
                assert!(!opens_new_epoch(StrikeMode::Fixed, a, b, false, leg),
                        "Fixed must hold its strike across {a} -> {b}");
            }
        }
    }

    #[test]
    fn a_new_yearly_cycle_always_restrikes_even_for_fixed() {
        // Fixed means fixed WITHIN a yearly cycle: the roll into the next
        // December re-enters at a fresh strike from that day's spot.
        assert!(opens_new_epoch(StrikeMode::Fixed, "2019-10-31", "2019-11-26", true, YEARLY_LEG));
        assert!(opens_new_epoch(StrikeMode::Fresh, "2019-10-31", "2019-11-26", true, YEARLY_LEG));
        // ...even when the roll day is in the same month as the previous entry.
        assert!(opens_new_epoch(StrikeMode::Fixed, "2019-11-05", "2019-11-26", true, YEARLY_LEG));
    }

    #[test]
    fn year_boundary_is_a_month_change_for_the_yearly_leg() {
        assert!(opens_new_epoch(StrikeMode::Fresh, "2019-12-26", "2020-01-02", false, YEARLY_LEG));
    }
}
