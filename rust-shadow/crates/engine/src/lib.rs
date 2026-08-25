use std::collections::BTreeMap;

use chrono::NaiveDate;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use thiserror::Error;

use algotest_domain::{ComboOverride, StrategyConfig};

pub mod market_data;
pub mod native;
pub mod workbook;

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct OptionKey {
    pub symbol: String,
    pub date: NaiveDate,
    pub expiry: NaiveDate,
    pub strike_minor: i64,
    pub option_type: String,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Default)]
pub struct Ohlc {
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub settled: Option<f64>,
}

pub trait MarketData: Send + Sync {
    fn option_ohlc(&self, key: &OptionKey) -> Option<Ohlc>;
    fn spot(&self, symbol: &str, date: NaiveDate) -> Option<Ohlc>;
    fn future_ohlc(&self, symbol: &str, date: NaiveDate, expiry: NaiveDate) -> Option<Ohlc>;
    fn trading_days(&self, symbol: &str, from: NaiveDate, to: NaiveDate) -> Vec<NaiveDate>;
    fn expiries(&self, symbol: &str, from: NaiveDate, to: NaiveDate) -> Vec<NaiveDate>;
    fn option_chain(
        &self,
        _symbol: &str,
        _date: NaiveDate,
        _expiry: NaiveDate,
        _option_type: &str,
    ) -> Vec<(f64, Ohlc)> {
        Vec::new()
    }
    fn futures_expiries(&self, _symbol: &str, _from: NaiveDate, _to: NaiveDate) -> Vec<NaiveDate> {
        Vec::new()
    }
    fn filter_segments(&self, _config: &str) -> Vec<(NaiveDate, NaiveDate)> {
        Vec::new()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct TradeRow {
    pub trade_id: u64,
    pub leg_id: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub leg_label: Option<String>,
    pub entry_date: String,
    pub exit_date: String,
    pub expiry: String,
    pub strike: f64,
    pub instrument: String,
    pub option_type: String,
    pub position: String,
    pub entry_price: f64,
    pub exit_price: f64,
    pub entry_spot: f64,
    pub exit_spot: f64,
    pub net_pnl: f64,
    /// This row's own leg P&L. `net_pnl` on the displayed anchor row retains
    /// the legacy whole-trade total for wire compatibility.
    #[serde(default)]
    pub leg_pnl: f64,
    pub exit_reason: String,
    pub mae: Option<f64>,
    pub mfe: Option<f64>,
    #[serde(default)]
    pub annotations: BTreeMap<String, String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct SummaryMetrics {
    pub count: u64,
    pub total_pnl: f64,
    pub average_pnl: f64,
    pub win_pct: f64,
    pub max_dd_pct: f64,
    pub cagr_options: f64,
    pub car_mdd: f64,
    #[serde(default)]
    pub extra: BTreeMap<String, f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct EngineResult {
    pub trades: Vec<TradeRow>,
    pub summary: SummaryMetrics,
}

#[derive(Debug, Error, Clone, Serialize, Deserialize, PartialEq)]
pub enum EngineError {
    #[error("invalid strategy: {0}")]
    InvalidStrategy(String),
    #[error("market data missing: {0}")]
    MissingMarketData(String),
    #[error("feature not yet ported: {0}")]
    FeatureNotPorted(String),
    #[error("calculation failed: {0}")]
    Calculation(String),
}

pub trait StrategyEngine: Send + Sync {
    fn validate(&self, strategy: &StrategyConfig) -> Result<(), EngineError>;
    fn run(
        &self,
        strategy: &StrategyConfig,
        combo: &ComboOverride,
    ) -> Result<EngineResult, EngineError>;
}

/// Canonical summary over completed parent trade rows. This deliberately has no
/// Python/pandas dependency and preserves input order for the equity curve.
pub fn summarize_parent_rows(rows: &[TradeRow]) -> SummaryMetrics {
    if rows.is_empty() {
        return SummaryMetrics::default();
    }
    let mut chronological = rows.iter().collect::<Vec<_>>();
    chronological.sort_by(|left, right| {
        left.entry_date
            .cmp(&right.entry_date)
            .then(left.trade_id.cmp(&right.trade_id))
            .then(left.leg_id.cmp(&right.leg_id))
    });
    let mut pnl = 0.0;
    let mut wins = 0u64;
    let mut losses = 0u64;
    let mut gross_profit = 0.0;
    let mut gross_loss = 0.0;
    let mut maximum_win = f64::NEG_INFINITY;
    let mut maximum_loss = f64::INFINITY;
    let mut current_win_streak = 0u64;
    let mut current_loss_streak = 0u64;
    let mut maximum_win_streak = 0u64;
    let mut maximum_loss_streak = 0u64;
    let mut pnl_pct_total = 0.0;
    let mut spot_change = 0.0;
    let mut spot_change_pct = 0.0;
    let mut win_pcts = Vec::new();
    let mut loss_pcts = Vec::new();
    let mut cumulative_pnl = 0.0f64;
    let mut peak_pnl = 0.0f64;
    let mut max_dd_points = 0.0f64;
    let mut final_maes = Vec::new();
    let mut nav = 100.0f64;
    let mut peak = 100.0f64;
    let mut max_dd = 0.0f64;
    for row in &chronological {
        pnl += row.net_pnl;
        if row.net_pnl > 0.0 {
            wins += 1;
            gross_profit += row.net_pnl;
            maximum_win = maximum_win.max(row.net_pnl);
            current_win_streak += 1;
            current_loss_streak = 0;
            maximum_win_streak = maximum_win_streak.max(current_win_streak);
        } else if row.net_pnl < 0.0 {
            losses += 1;
            gross_loss += row.net_pnl.abs();
            maximum_loss = maximum_loss.min(row.net_pnl);
            current_loss_streak += 1;
            current_win_streak = 0;
            maximum_loss_streak = maximum_loss_streak.max(current_loss_streak);
        } else {
            current_win_streak = 0;
            current_loss_streak = 0;
        }
        let pct = if row.entry_spot != 0.0 {
            row.net_pnl / row.entry_spot * 100.0
        } else {
            0.0
        };
        if row.net_pnl > 0.0 {
            win_pcts.push(pct);
        } else if row.net_pnl < 0.0 {
            loss_pcts.push(pct);
        }
        pnl_pct_total += pct;
        spot_change += row.exit_spot - row.entry_spot;
        if row.entry_spot != 0.0 {
            spot_change_pct += (row.exit_spot - row.entry_spot) / row.entry_spot * 100.0;
        }
        cumulative_pnl += row.net_pnl;
        peak_pnl = peak_pnl.max(cumulative_pnl);
        max_dd_points = max_dd_points.min(cumulative_pnl - peak_pnl);
        if let Some(mae) = row.mae.filter(|value| value.is_finite()) {
            final_maes.push(mae);
        }
        nav *= 1.0 + pct / 100.0;
        peak = peak.max(nav);
        if peak != 0.0 {
            max_dd = max_dd.min((nav / peak - 1.0) * 100.0);
        }
    }
    let count = rows.len() as u64;
    let total_pnl = py_round(pnl, 2);
    let average_pnl = py_round(total_pnl / count as f64, 2);
    let win_pct = py_round(wins as f64 / count as f64 * 100.0, 2);
    let loss_pct = py_round(losses as f64 / count as f64 * 100.0, 2);
    let average_win = if wins > 0 {
        py_round(gross_profit / wins as f64, 2)
    } else {
        0.0
    };
    let average_loss = if losses > 0 {
        py_round(-gross_loss / losses as f64, 2)
    } else {
        0.0
    };
    let profit_factor = if gross_loss == 0.0 {
        if gross_profit > 0.0 {
            999.99
        } else {
            0.0
        }
    } else {
        py_round(gross_profit / gross_loss, 2)
    };
    let first_entry = rows
        .iter()
        .filter_map(|row| NaiveDate::parse_from_str(&row.entry_date, "%Y-%m-%d").ok())
        .min();
    let last_exit = rows
        .iter()
        .filter_map(|row| NaiveDate::parse_from_str(&row.exit_date, "%Y-%m-%d").ok())
        .max();
    let years = match (first_entry, last_exit) {
        (Some(first), Some(last)) => ((last - first).num_days() as f64 / 365.0).max(0.01),
        _ => 0.01,
    };
    let cagr_options = if nav > 0.0 {
        py_round(
            (100.0 * ((nav / 100.0).powf(1.0 / years) - 1.0)).clamp(-99_999.0, 99_999.0),
            2,
        )
    } else {
        -100.0
    };
    let max_dd_pct = py_round(max_dd, 6);
    let car_mdd = if max_dd_pct != 0.0 {
        py_round((cagr_options / max_dd_pct.abs()).min(99_999.0), 4)
    } else {
        0.0
    };
    let average_win_pct = if win_pcts.is_empty() {
        0.0
    } else {
        win_pcts.iter().sum::<f64>() / win_pcts.len() as f64
    };
    let average_loss_pct = if loss_pcts.is_empty() {
        0.0
    } else {
        loss_pcts.iter().sum::<f64>() / loss_pcts.len() as f64
    };
    let expectancy = if average_loss_pct != 0.0 {
        (average_win_pct / average_loss_pct.abs()) * (win_pct / 100.0) - (1.0 - win_pct / 100.0)
    } else {
        0.0
    };
    let first_spot = chronological
        .first()
        .map(|row| row.entry_spot)
        .unwrap_or(0.0);
    let final_spot = chronological.last().map(|row| row.exit_spot).unwrap_or(0.0);
    let cagr_spot = if first_spot > 0.0 && final_spot > 0.0 {
        py_round(
            100.0 * ((final_spot / first_spot).powf(1.0 / years) - 1.0),
            2,
        )
    } else {
        0.0
    };
    let mut extra = BTreeMap::new();
    extra.insert("loss_pct".into(), loss_pct);
    extra.insert("avg_win".into(), average_win);
    extra.insert("avg_loss".into(), average_loss);
    extra.insert(
        "max_win".into(),
        if wins > 0 {
            py_round(maximum_win, 2)
        } else {
            0.0
        },
    );
    extra.insert(
        "max_loss".into(),
        if losses > 0 {
            py_round(maximum_loss, 2)
        } else {
            0.0
        },
    );
    extra.insert("profit_factor".into(), profit_factor);
    extra.insert("max_win_streak".into(), maximum_win_streak as f64);
    extra.insert("max_loss_streak".into(), maximum_loss_streak as f64);
    extra.insert("total_pnl_pct".into(), py_round(pnl_pct_total, 4));
    extra.insert(
        "avg_profit_per_trade_pct".into(),
        py_round(pnl_pct_total / count as f64, 4),
    );
    extra.insert("spot_change".into(), py_round(spot_change, 2));
    extra.insert("spot_change_pct".into(), py_round(spot_change_pct, 4));
    extra.insert("avg_win_pct".into(), py_round(average_win_pct, 4));
    extra.insert("avg_loss_pct".into(), py_round(average_loss_pct, 4));
    extra.insert("expectancy".into(), py_round(expectancy, 6));
    extra.insert("cagr_spot".into(), cagr_spot);
    extra.insert("max_dd_pts".into(), py_round(max_dd_points, 2));
    extra.insert(
        "recovery_factor".into(),
        if max_dd_points != 0.0 {
            py_round((total_pnl / max_dd_points.abs()).min(99_999.0), 2)
        } else {
            0.0
        },
    );
    extra.insert(
        "roi_vs_spot".into(),
        if spot_change != 0.0 {
            py_round(total_pnl / spot_change, 4)
        } else {
            0.0
        },
    );
    extra.insert(
        "avg_final_mae".into(),
        if final_maes.is_empty() {
            0.0
        } else {
            py_round(final_maes.iter().sum::<f64>() / final_maes.len() as f64, 4)
        },
    );
    SummaryMetrics {
        count,
        total_pnl,
        average_pnl,
        win_pct,
        max_dd_pct,
        cagr_options,
        car_mdd,
        extra,
    }
}

/// Collapse leg/re-entry rows to one deterministic parent row per trade.
/// The anchor is the latest entry date and then the lowest leg id, while P&L
/// is always the sum of each row's own already-scaled `leg_pnl`.
pub fn canonical_parent_rows(rows: &[TradeRow]) -> Vec<TradeRow> {
    let mut grouped = BTreeMap::<u64, Vec<&TradeRow>>::new();
    for row in rows {
        grouped.entry(row.trade_id).or_default().push(row);
    }
    let mut parents = grouped
        .into_values()
        .filter_map(|group| {
            let anchor = group.iter().copied().max_by(|left, right| {
                left.entry_date
                    .cmp(&right.entry_date)
                    .then_with(|| right.leg_id.cmp(&left.leg_id))
            })?;
            let mut parent = anchor.clone();
            parent.net_pnl = group.iter().map(|row| row.leg_pnl).sum();
            let maes = group.iter().filter_map(|row| row.mae).collect::<Vec<_>>();
            parent.mae = if maes.is_empty() {
                None
            } else {
                Some(maes.iter().sum::<f64>() / maes.len() as f64)
            };
            let mfes = group.iter().filter_map(|row| row.mfe).collect::<Vec<_>>();
            parent.mfe = if mfes.is_empty() {
                None
            } else {
                Some(mfes.iter().sum::<f64>() / mfes.len() as f64)
            };
            Some(parent)
        })
        .collect::<Vec<_>>();
    parents.sort_by(|left, right| {
        left.entry_date
            .cmp(&right.entry_date)
            .then(left.trade_id.cmp(&right.trade_id))
    });
    parents
}

/// Per-trade equity-curve analytics (Cumulative/Peak/DD/%DD/% P&L), computed
/// with the SAME chronological nav/peak pass as `summarize_parent_rows` over the
/// canonical parent rows, so the per-row curve and the summary `max_dd_pct`
/// never diverge (guarded by `analytics_min_dd_matches_summary`). Keyed by
/// trade_id; only each trade's canonical parent (Leg 1) carries them, mirroring
/// the live engine which populates these columns on Leg 1 only.
///
/// ponytail: this is the shadow's own committed NAV curve (base-100 compounded,
/// %DD = nav/peak-1). Exact match to the live Python `Cumulative` convention
/// (points-cumsum branch, Live-DD prev-peak, first-trade MAE NAV) is the
/// separate artifact-parity gate, not this UI-wiring step.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RowAnalytics {
    pub pct_pnl: f64,
    pub cumulative: f64,
    pub peak: f64,
    pub dd: f64,
    pub pct_dd: f64,
}

pub fn parent_analytics(rows: &[TradeRow]) -> BTreeMap<u64, RowAnalytics> {
    let parents = canonical_parent_rows(rows); // already chronologically sorted
    let mut out = BTreeMap::new();
    let mut nav = 100.0f64;
    let mut peak = 100.0f64;
    for parent in &parents {
        let pct = if parent.entry_spot != 0.0 {
            parent.net_pnl / parent.entry_spot * 100.0
        } else {
            0.0
        };
        nav *= 1.0 + pct / 100.0;
        peak = peak.max(nav);
        let pct_dd = if peak != 0.0 {
            (nav / peak - 1.0) * 100.0
        } else {
            0.0
        };
        out.insert(
            parent.trade_id,
            RowAnalytics {
                pct_pnl: pct,
                cumulative: nav,
                peak,
                dd: nav - peak,
                pct_dd,
            },
        );
    }
    out
}

/// Project a `TradeRow` to the live engine's Title-Case tradesheet columns the
/// frontend consumes. Analytics columns are added separately for anchor rows.
fn legacy_row_value(row: &TradeRow) -> Value {
    let display_strike = if row.instrument == "FUTURES" {
        Value::String(String::new())
    } else {
        json!(row.strike)
    };
    json!({
        "Trade": row.trade_id.to_string(),
        "Leg": row.leg_label.as_ref().map_or_else(|| json!(row.leg_id), |label| json!(label)),
        "Entry Date": row.entry_date,
        "Exit Date": row.exit_date,
        "Type": row.option_type,
        "Strike": display_strike,
        "B/S": row.position,
        "Entry Price": row.entry_price,
        "Exit Price": row.exit_price,
        "Entry Spot": row.entry_spot,
        "Exit Spot": row.exit_spot,
        "Expiry": row.expiry,
        "Net P&L": row.net_pnl,
        "MAE": row.mae,
        "MFE": row.mfe,
    })
}

/// One display-keyed tradesheet row per input row (the Title-Case contract the
/// frontend's `ResultsPanel` reads). Each trade's canonical parent (Leg 1)
/// additionally carries the equity-curve columns and the whole-trade `Net P&L`;
/// re-entry/grouping annotations pass through as columns.
pub fn legacy_tradesheet(rows: &[TradeRow]) -> Vec<Value> {
    let analytics = parent_analytics(rows);
    let anchors: BTreeMap<u64, (u32, String)> = canonical_parent_rows(rows)
        .into_iter()
        .map(|parent| (parent.trade_id, (parent.leg_id, parent.entry_date)))
        .collect();
    rows.iter()
        .map(|row| {
            let mut value = legacy_row_value(row);
            let obj = value.as_object_mut().expect("row projects to a JSON object");
            for (key, val) in &row.annotations {
                obj.insert(key.clone(), Value::String(val.clone()));
            }
            if let Some(mode) = row.annotations.get("reentry_mode") {
                obj.insert("ReEntryMode".into(), Value::String(mode.clone()));
            }
            let is_anchor = anchors
                .get(&row.trade_id)
                .is_some_and(|(leg, date)| *leg == row.leg_id && date == &row.entry_date);
            if is_anchor {
                if let Some(a) = analytics.get(&row.trade_id) {
                    obj.insert("Cumulative".into(), json!(a.cumulative));
                    obj.insert("Peak".into(), json!(a.peak));
                    obj.insert("DD".into(), json!(a.dd));
                    obj.insert("%DD".into(), json!(a.pct_dd));
                    obj.insert("% P&L".into(), json!(a.pct_pnl));
                }
            }
            value
        })
        .collect()
}

/// Flatten `SummaryMetrics` (top-level fields + `extra`) into a single flat
/// snake_case object, since the frontend reads e.g. `summary.loss_pct` and
/// `summary.max_dd_pts` directly rather than under `summary.extra`.
pub fn summary_flat(summary: &SummaryMetrics) -> Value {
    let mut map = serde_json::Map::new();
    map.insert("count".into(), json!(summary.count));
    map.insert("total_pnl".into(), json!(summary.total_pnl));
    map.insert("average_pnl".into(), json!(summary.average_pnl));
    map.insert("win_pct".into(), json!(summary.win_pct));
    map.insert("max_dd_pct".into(), json!(summary.max_dd_pct));
    map.insert("cagr_options".into(), json!(summary.cagr_options));
    map.insert("car_mdd".into(), json!(summary.car_mdd));
    for (key, val) in &summary.extra {
        map.insert(key.clone(), json!(val));
    }
    Value::Object(map)
}

pub(crate) fn py_round(value: f64, digits: usize) -> f64 {
    format!("{value:.digits$}").parse().unwrap_or(value)
}

#[cfg(test)]
mod summary_tests {
    use super::*;

    fn row(trade_id: u64, leg_id: u32, date: &str, pnl: f64) -> TradeRow {
        TradeRow {
            trade_id,
            leg_id,
            entry_date: date.into(),
            exit_date: date.into(),
            entry_spot: 20_000.0,
            exit_spot: 20_000.0,
            net_pnl: pnl,
            ..Default::default()
        }
    }

    #[test]
    fn summary_is_invariant_to_input_row_sequence() {
        let chronological = vec![
            row(1, 1, "2025-01-01", 100.0),
            row(2, 1, "2025-01-02", -50.0),
        ];
        let mut reversed = chronological.clone();
        reversed.reverse();
        assert_eq!(
            summarize_parent_rows(&chronological),
            summarize_parent_rows(&reversed)
        );
    }

    #[test]
    fn canonical_parent_excursions_are_invariant_to_leg_ids() {
        let mut call = row(1, 1, "2025-01-01", 75.0);
        call.leg_pnl = 75.0;
        call.mae = Some(-0.8);
        call.mfe = Some(1.2);
        let mut put = row(1, 2, "2025-01-01", -25.0);
        put.leg_pnl = -25.0;
        put.mae = Some(-0.2);
        put.mfe = Some(0.4);
        let forward = canonical_parent_rows(&[call.clone(), put.clone()]);
        call.leg_id = 2;
        put.leg_id = 1;
        let reversed = canonical_parent_rows(&[put, call]);
        assert_eq!(forward[0].net_pnl, reversed[0].net_pnl);
        assert_eq!(forward[0].mae, Some(-0.5));
        assert_eq!(forward[0].mae, reversed[0].mae);
        assert_eq!(forward[0].mfe, reversed[0].mfe);
    }

    fn parent_row(trade_id: u64, date: &str, pnl: f64) -> TradeRow {
        let mut r = row(trade_id, 1, date, pnl);
        r.leg_pnl = pnl; // canonical parent net_pnl = Σleg_pnl
        r
    }

    #[test]
    fn analytics_min_dd_matches_summary() {
        let rows = vec![
            parent_row(1, "2025-01-01", 400.0),
            parent_row(2, "2025-01-02", -1200.0),
            parent_row(3, "2025-01-03", 200.0),
        ];
        let parents = canonical_parent_rows(&rows);
        let summary = summarize_parent_rows(&parents);
        let analytics = parent_analytics(&rows);
        assert_eq!(analytics.len(), 3);
        let min_dd = analytics
            .values()
            .map(|a| a.pct_dd)
            .fold(f64::INFINITY, f64::min);
        assert!((py_round(min_dd, 6) - summary.max_dd_pct).abs() < 1e-6);
    }

    #[test]
    fn tradesheet_puts_analytics_on_leg1_only() {
        let mut leg1 = row(1, 1, "2025-01-01", 400.0);
        leg1.leg_pnl = 400.0;
        let mut leg2 = row(1, 2, "2025-01-01", -100.0);
        leg2.leg_pnl = -100.0;
        let sheet = legacy_tradesheet(&[leg1, leg2]);
        assert_eq!(sheet.len(), 2);
        // Leg 1 (anchor) carries the equity-curve columns; Leg 2 does not.
        assert!(sheet[0].get("Cumulative").is_some());
        assert!(sheet[0].get("%DD").is_some());
        assert!(sheet[1].get("Cumulative").is_none());
        assert_eq!(sheet[0]["Entry Date"], "2025-01-01");
        assert!(sheet[0].get("Net P&L").is_some());
    }
}
