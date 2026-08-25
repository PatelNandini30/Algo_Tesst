use std::{collections::{BTreeMap, BTreeSet}, sync::Arc};

use chrono::{Datelike, NaiveDate};

use algotest_domain::{ComboOverride, LegConfig, StrategyConfig};

use crate::{
    canonical_parent_rows, summarize_parent_rows, EngineError, EngineResult, MarketData, Ohlc,
    OptionKey, StrategyEngine, TradeRow,
};

/// Pure-Rust positional EOD engine. Unsupported strike/risk branches fail
/// explicitly; they never silently drop a leg or return a partial combination.
pub struct NativeEngine<M: MarketData> {
    market: Arc<M>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct PerLegRollSlot {
    contract: NaiveDate,
    own_boundary: bool,
    refresh_boundary: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct PerLegRollRow {
    entry: NaiveDate,
    exit: NaiveDate,
    slots: Vec<PerLegRollSlot>,
}

/// Builds the union calendar for independent leg rollover.  This function is
/// deliberately free of pricing and row-order concerns: each emitted interval
/// is complete (every leg owns a contract), and a slot says whether that leg
/// rolls or merely carries at the interval start.
fn build_per_leg_roll_schedule(
    run_start: NaiveDate,
    leg_cycles: &[Vec<(NaiveDate, NaiveDate)>],
    leg_exit_dte: &[usize],
    trading_days: &[NaiveDate],
    extra_boundaries: &[NaiveDate],
    leg_refresh_boundaries: &[BTreeSet<NaiveDate>],
) -> Result<Vec<PerLegRollRow>, EngineError> {
    if leg_cycles.is_empty()
        || leg_cycles.len() != leg_exit_dte.len()
        || leg_cycles.len() != leg_refresh_boundaries.len()
    {
        return Err(EngineError::InvalidStrategy(
            "per-leg rollover requires one expiry calendar and exit_dte per leg".into(),
        ));
    }
    let mut segments = Vec::with_capacity(leg_cycles.len());
    for (cycles, exit_dte) in leg_cycles.iter().zip(leg_exit_dte) {
        let mut calendar = cycles.clone();
        calendar.sort_unstable();
        calendar.dedup();
        let mut leg_segments = Vec::new();
        let mut start = run_start;
        for (exit_anchor, contract) in calendar {
            let eligible = trading_days
                .iter()
                .copied()
                .filter(|day| *day <= exit_anchor)
                .collect::<Vec<_>>();
            if eligible.len() <= *exit_dte {
                continue;
            }
            let exit = eligible[eligible.len() - 1 - *exit_dte];
            if exit <= start {
                continue;
            }
            leg_segments.push((start, exit, contract));
            start = exit;
        }
        if leg_segments.is_empty() {
            return Err(EngineError::MissingMarketData(
                "per-leg rollover has no complete contract segments".into(),
            ));
        }
        segments.push(leg_segments);
    }

    let mut boundaries = BTreeSet::new();
    for leg in &segments {
        for (entry, exit, _) in leg {
            boundaries.insert(*entry);
            boundaries.insert(*exit);
        }
    }
    boundaries.extend(extra_boundaries.iter().copied().filter(|day| *day > run_start));
    for refreshes in leg_refresh_boundaries {
        boundaries.extend(refreshes.iter().copied().filter(|day| *day > run_start));
    }
    let boundaries = boundaries.into_iter().collect::<Vec<_>>();
    let mut rows = Vec::new();
    for pair in boundaries.windows(2) {
        let entry = pair[0];
        let exit = pair[1];
        // The live rollover contract skips the arbitrary opening stub and starts
        // on the first real rollover boundary.
        if entry == run_start {
            continue;
        }
        let mut slots = Vec::with_capacity(segments.len());
        for (leg_index, leg) in segments.iter().enumerate() {
            let Some((own_entry, _, contract)) = leg
                .iter()
                .find(|(start, end, _)| *start <= entry && entry < *end)
            else {
                slots.clear();
                break;
            };
            slots.push(PerLegRollSlot {
                contract: *contract,
                own_boundary: *own_entry == entry,
                refresh_boundary: leg_refresh_boundaries[leg_index].contains(&entry),
            });
        }
        if slots.len() == segments.len() {
            rows.push(PerLegRollRow { entry, exit, slots });
        }
    }
    Ok(rows)
}

impl<M: MarketData> NativeEngine<M> {
    pub fn new(market: Arc<M>) -> Self {
        Self { market }
    }

    fn run_multi_index(
        &self,
        strategy: &StrategyConfig,
        from: NaiveDate,
        to: NaiveDate,
    ) -> Result<Vec<TradeRow>, EngineError> {
        let default_symbol = strategy.index.trim().to_ascii_uppercase();
        let default_expiry = strategy
            .expiry_type
            .as_deref()
            .unwrap_or("MONTHLY")
            .to_ascii_uppercase();
        let mut groups: BTreeMap<(String, String), Vec<(usize, LegConfig)>> = BTreeMap::new();
        for (index, leg) in strategy.legs.iter().cloned().enumerate() {
            let symbol = leg
                .index
                .as_deref()
                .unwrap_or(&default_symbol)
                .trim()
                .to_ascii_uppercase();
            let expiry = leg
                .expiry
                .as_deref()
                .unwrap_or(&default_expiry)
                .trim()
                .to_ascii_uppercase();
            groups.entry((symbol, expiry)).or_default().push((index, leg));
        }
        let mut rows = Vec::new();
        let mut trade_offset = 0u64;
        for ((symbol, expiry), indexed_legs) in groups {
            let original_ids = indexed_legs
                .iter()
                .map(|(index, _)| *index as u32 + 1)
                .collect::<Vec<_>>();
            let mut subgroup = strategy.clone();
            subgroup.index = symbol.clone();
            subgroup.expiry_type = Some(expiry.clone());
            subgroup.multi_index_mode = false;
            subgroup.midcap_legs.clear();
            subgroup.legs = indexed_legs.into_iter().map(|(_, leg)| leg).collect();
            let mut group_rows = self.run_options(&subgroup, from, to)?;
            let group_max_trade = group_rows.iter().map(|row| row.trade_id).max().unwrap_or(0);
            for row in &mut group_rows {
                row.trade_id += trade_offset;
                if let Some(original_id) = original_ids.get(row.leg_id.saturating_sub(1) as usize) {
                    row.leg_id = *original_id;
                }
                row.annotations.insert("group_index".into(), symbol.clone());
                row.annotations.insert("group_expiry".into(), expiry.clone());
            }
            rows.extend(group_rows);
            trade_offset = trade_offset.saturating_add(group_max_trade);
        }
        rows.sort_by(|left, right| {
            left.entry_date
                .cmp(&right.entry_date)
                .then(left.trade_id.cmp(&right.trade_id))
                .then(left.leg_id.cmp(&right.leg_id))
        });
        let mut renumbered = BTreeMap::new();
        let mut next_trade_id = 0u64;
        for row in &mut rows {
            let key = (
                row.annotations.get("group_index").cloned().unwrap_or_default(),
                row.annotations.get("group_expiry").cloned().unwrap_or_default(),
                row.trade_id,
            );
            let id = *renumbered.entry(key).or_insert_with(|| {
                next_trade_id += 1;
                next_trade_id
            });
            row.trade_id = id;
        }
        Ok(rows)
    }

    fn apply_midcap_overlay(
        &self,
        strategy: &StrategyConfig,
        rows: &mut Vec<TradeRow>,
    ) -> Result<(), EngineError> {
        if strategy.midcap_legs.is_empty() || rows.is_empty() {
            return Ok(());
        }
        let symbol = strategy
            .extra
            .get("midcap_symbol")
            .and_then(|value| value.as_str())
            .unwrap_or("NIFTYMIDCAP100")
            .trim()
            .to_ascii_uppercase();
        let adjustment = strategy.extra.get("midcap_spot_adjustment");
        let parents = canonical_parent_rows(rows);
        let mut overlays = Vec::new();
        for parent in parents {
            let entry = parse_row_date(&parent.entry_date)?;
            let scheduled_exit = parse_row_date(&parent.exit_date)?;
            let entry_ohlc = self.market.spot(&symbol, entry).ok_or_else(|| {
                EngineError::MissingMarketData(format!("overlay spot {symbol} {entry}"))
            })?;
            let mut exit = scheduled_exit;
            if adjustment
                .and_then(|value| value.get("enabled"))
                .and_then(|value| value.as_bool())
                .unwrap_or(false)
            {
                let value = adjustment
                    .and_then(|config| config.get("pct").or_else(|| config.get("value")))
                    .and_then(|value| value.as_f64())
                    .unwrap_or(0.0);
                let units = adjustment
                    .and_then(|config| config.get("units"))
                    .and_then(|value| value.as_str())
                    .unwrap_or("percent");
                let direction = adjustment
                    .and_then(|config| config.get("direction"))
                    .and_then(|value| value.as_str())
                    .unwrap_or("rise")
                    .to_ascii_lowercase();
                if value > 0.0 {
                    let rise = if units.eq_ignore_ascii_case("points") {
                        entry_ohlc.close + value
                    } else {
                        entry_ohlc.close * (1.0 + value / 100.0)
                    };
                    let fall = if units.eq_ignore_ascii_case("points") {
                        entry_ohlc.close - value
                    } else {
                        entry_ohlc.close * (1.0 - value / 100.0)
                    };
                    if let Some(day) = self
                        .market
                        .trading_days(&symbol, entry, scheduled_exit)
                        .into_iter()
                        .filter(|day| entry < *day && *day <= scheduled_exit)
                        .find(|day| {
                            self.market.spot(&symbol, *day).is_some_and(|spot| {
                                (matches!(direction.as_str(), "rise" | "both") && spot.close >= rise)
                                    || (matches!(direction.as_str(), "fall" | "both") && spot.close <= fall)
                            })
                        })
                    {
                        exit = day;
                    }
                }
            }
            let exit_ohlc = self.market.spot(&symbol, exit).ok_or_else(|| {
                EngineError::MissingMarketData(format!("overlay spot {symbol} {exit}"))
            })?;
            for (overlay_index, config) in strategy.midcap_legs.iter().enumerate() {
                let position = config
                    .get("position")
                    .and_then(|value| value.as_str())
                    .unwrap_or("BUY")
                    .to_ascii_uppercase();
                let lots = config.get("lots").and_then(|value| value.as_f64()).unwrap_or(1.0);
                let mode = config
                    .get("midcap_mode")
                    .and_then(|value| value.as_str())
                    .unwrap_or("spot")
                    .to_ascii_lowercase();
                let days = (exit - entry).num_days().max(0) as f64;
                let cost_fraction = if mode == "hypothetical" {
                    config
                        .get("cost_pct_per_month")
                        .and_then(|value| value.as_f64())
                        .unwrap_or(0.0)
                        / 100.0
                        * days
                        / 30.0
                } else {
                    0.0
                };
                let direction = if position.starts_with('S') { -1.0 } else { 1.0 };
                let spot_points = (exit_ohlc.close - entry_ohlc.close) * direction;
                let pnl = round4((spot_points - cost_fraction * entry_ohlc.close) * lots);
                let monthly_cost = if mode == "hypothetical" {
                    config
                        .get("cost_pct_per_month")
                        .and_then(|value| value.as_f64())
                        .unwrap_or(0.0)
                        / 100.0
                } else {
                    0.0
                };
                let effective_entry = entry_ohlc.close * (1.0 + monthly_cost * days / 30.0);
                let mut path_values = self
                    .market
                    .trading_days(&symbol, entry, exit)
                    .into_iter()
                    .filter(|day| *day > entry)
                    .flat_map(|day| {
                        self.market.spot(&symbol, day).map_or_else(Vec::new, |ohlc| {
                            let remaining = (exit - day).num_days().max(0) as f64;
                            let carry_factor = 1.0 + monthly_cost * remaining / 30.0;
                            [ohlc.low, ohlc.high]
                                .into_iter()
                                .map(|price| {
                                    (price * carry_factor - effective_entry) * direction
                                        / effective_entry
                                        * 100.0
                                })
                                .collect()
                        })
                    })
                    .collect::<Vec<_>>();
                if path_values.is_empty() {
                    path_values.push(0.0);
                }
                let mut annotations = BTreeMap::new();
                annotations.insert("overlay_symbol".into(), symbol.clone());
                annotations.insert("midcap_mode".into(), mode.to_ascii_uppercase());
                annotations.insert("rollover_cost_pct".into(), format!("{:.6}", cost_fraction * 100.0));
                overlays.push(TradeRow {
                    trade_id: parent.trade_id,
                    leg_id: strategy.legs.len() as u32 + overlay_index as u32 + 1,
                    leg_label: Some(format!("midcap{}", overlay_index + 1)),
                    entry_date: entry.to_string(),
                    exit_date: exit.to_string(),
                    expiry: String::new(),
                    strike: 0.0,
                    instrument: "MIDCAP100".into(),
                    option_type: "SPOT".into(),
                    position: if position.starts_with('S') { "SELL".into() } else { "BUY".into() },
                    entry_price: entry_ohlc.close,
                    exit_price: exit_ohlc.close,
                    entry_spot: entry_ohlc.close,
                    exit_spot: exit_ohlc.close,
                    net_pnl: pnl,
                    leg_pnl: pnl,
                    exit_reason: if exit < scheduled_exit { "MIDCAP_SPOT_ADJ".into() } else { parent.exit_reason.clone() },
                    mae: path_values.iter().copied().reduce(f64::min).map(round4),
                    mfe: path_values.iter().copied().reduce(f64::max).map(round4),
                    annotations,
                });
            }
        }
        rows.extend(overlays);
        let totals = canonical_parent_rows(rows)
            .into_iter()
            .map(|row| (row.trade_id, row.net_pnl))
            .collect::<BTreeMap<_, _>>();
        let mut seen = BTreeSet::new();
        for row in rows.iter_mut() {
            if seen.insert(row.trade_id) {
                if let Some(total) = totals.get(&row.trade_id) {
                    row.net_pnl = *total;
                }
            }
        }
        rows.sort_by(|left, right| {
            left.entry_date
                .cmp(&right.entry_date)
                .then(left.trade_id.cmp(&right.trade_id))
                .then(left.leg_id.cmp(&right.leg_id))
        });
        Ok(())
    }

    fn run_options(
        &self,
        strategy: &StrategyConfig,
        from: NaiveDate,
        to: NaiveDate,
    ) -> Result<Vec<TradeRow>, EngineError> {
        let symbol = strategy.index.trim().to_ascii_uppercase();
        let trading_days = self.market.trading_days(&symbol, from, to);
        if trading_days.is_empty() {
            return Err(EngineError::MissingMarketData(format!(
                "no spot calendar for {symbol} {from}..{to}"
            )));
        }
        let entry_dte = strategy.entry_dte.unwrap_or(1) as usize;
        let exit_dte = strategy.exit_dte.unwrap_or(0) as usize;
        if exit_dte > entry_dte {
            return Err(EngineError::InvalidStrategy(
                "exit_dte cannot exceed entry_dte".into(),
            ));
        }
        let listed_expiries = self
            .market
            .expiries(&symbol, from, to + chrono::Duration::days(1830));
        let all_expiries = listed_expiries
            .iter()
            .copied()
            .filter(|expiry| *expiry <= to + chrono::Duration::days(14))
            .collect::<Vec<_>>();
        let mut expiries = all_expiries.clone();
        let cadence = strategy
            .expiry_type
            .as_deref()
            .or_else(|| {
                strategy
                    .extra
                    .get("expiry_window")
                    .and_then(|value| value.as_str())
            })
            .unwrap_or("WEEKLY")
            .to_ascii_uppercase();
        if cadence.contains("MONTH") {
            let mut monthly = std::collections::BTreeMap::new();
            for expiry in expiries {
                monthly.insert((expiry.year(), expiry.month()), expiry);
            }
            expiries = monthly.into_values().collect();
        }
        let uses_next_weekly = strategy.legs.iter().any(|leg| {
            leg.expiry
                .as_deref()
                .is_some_and(|value| value.eq_ignore_ascii_case("NEXT_WEEKLY"))
        });
        let rollover_active = strategy.rollover_toggle
            && !strategy
                .extra
                .get("no_rollover")
                .and_then(|value| value.as_bool())
                .unwrap_or(false)
            && strategy.expiry_type.as_deref().is_some_and(|value| {
                matches!(value.to_ascii_uppercase().as_str(), "WEEKLY" | "MONTHLY")
            });
        let rollover_min_days = strategy
            .extra
            .get("rollover_min_days_to_expiry")
            .and_then(|value| value.as_u64())
            .unwrap_or(0) as usize;
        let fixed_entry_mode = strategy
            .extra
            .get("filter_entry_mode")
            .and_then(|value| value.as_str())
            .is_some_and(|value| value.eq_ignore_ascii_case("fixed"));
        // Folder-based filter key (any `filter_key` in `filter_date_sets`, or
        // legacy 5x1/5x2/base2) resolved dynamically — no hardcoded whitelist.
        // `custom`/uploaded windows arrive via `strategy.filter_segments`.
        let filter_config = strategy.filter_key();
        let mut filter_segments = filter_config
            .map(|config| self.market.filter_segments(config))
            .unwrap_or_default();
        if !strategy.filter_segments.is_empty() {
            filter_segments = strategy
                .filter_segments
                .iter()
                .map(|segment| (segment.start, segment.end))
                .collect();
            filter_segments.sort_unstable();
        }
        if filter_config.is_some() && filter_segments.is_empty() {
            return Err(EngineError::MissingMarketData(format!(
                "filter segments for {}",
                filter_config.unwrap_or_default()
            )));
        }
        if strategy.per_leg_rollover || strategy_has_yearly(strategy) {
            return self.run_options_per_leg(
                strategy,
                from,
                to,
                &symbol,
                &trading_days,
                &all_expiries,
                &listed_expiries,
                &filter_segments,
            );
        }
        let mut rows = Vec::new();
        let mut trade_id = 0u64;
        let mut fixed_strikes = vec![None; strategy.legs.len()];
        let mut previous_scheduled_exit = None;
        for (expiry_index, expiry) in expiries.iter().copied().enumerate() {
            if expiry > to {
                break;
            }
            let exit_reference = if uses_next_weekly {
                let Some(next) = expiries.get(expiry_index + 1).copied() else {
                    break;
                };
                next
            } else {
                expiry
            };
            let entry_eligible: Vec<_> = trading_days
                .iter()
                .copied()
                .filter(|day| *day <= expiry)
                .collect();
            let exit_eligible: Vec<_> = trading_days
                .iter()
                .copied()
                .filter(|day| *day <= exit_reference)
                .collect();
            if entry_eligible.len() <= entry_dte || exit_eligible.len() <= exit_dte {
                continue;
            }
            let scheduled_entry = if fixed_entry_mode {
                if expiry_index == 0 {
                    trading_days
                        .iter()
                        .copied()
                        .find(|day| *day >= from)
                        .unwrap_or(from)
                } else {
                    expiries[expiry_index - 1]
                }
            } else {
                entry_eligible[entry_eligible.len() - 1 - entry_dte]
            };
            let scheduled_exit = exit_eligible[exit_eligible.len() - 1 - exit_dte];
            let entry_date = if rollover_active {
                previous_scheduled_exit.unwrap_or(scheduled_entry)
            } else {
                scheduled_entry
            };
            if rollover_active {
                // The live rollover chain anchors the next entry to the prior
                // *scheduled* exit, even when min-DTE extends the prior hold.
                previous_scheduled_exit = Some(scheduled_exit);
            }
            if entry_date >= scheduled_exit {
                continue;
            }
            let mut exit_date = scheduled_exit;
            let mut rollover_contract = expiry;
            if rollover_active && rollover_min_days > 0 {
                let strict_gap = trading_days
                    .iter()
                    .filter(|day| entry_date < **day && **day <= expiry)
                    .count();
                if strict_gap <= rollover_min_days {
                    if let Some(next_expiry) = expiries.get(expiry_index + 1).copied() {
                        exit_date = next_expiry;
                        rollover_contract = next_expiry;
                    }
                }
            }
            let active_segment = if !filter_segments.is_empty() {
                let segment = filter_segments
                    .iter()
                    .copied()
                    .find(|(start, end)| *start <= entry_date && entry_date <= *end);
                let Some((start, end)) = segment else {
                    continue;
                };
                if exit_date > end {
                    let Some(clamped) =
                        trading_days.iter().copied().filter(|day| *day <= end).max()
                    else {
                        continue;
                    };
                    exit_date = clamped;
                }
                Some((start, end))
            } else {
                None
            };
            if entry_date < from || exit_date > to || entry_date >= exit_date {
                continue;
            }
            let leg_expiries = strategy
                .legs
                .iter()
                .map(|leg| {
                    if leg
                        .expiry
                        .as_deref()
                        .is_some_and(|value| value.eq_ignore_ascii_case("NEXT_WEEKLY"))
                    {
                        exit_reference
                    } else {
                        rollover_contract
                    }
                })
                .collect::<Vec<_>>();
            let individual_boundaries = snapped_leg_filter_boundaries(
                &strategy.legs,
                &trading_days,
                entry_date,
                exit_date,
            );
            let mut window_points = Vec::with_capacity(individual_boundaries.len() + 2);
            window_points.push(entry_date);
            window_points.extend(individual_boundaries.iter().copied());
            window_points.push(exit_date);
            for (window_index, pair) in window_points.windows(2).enumerate() {
                let window_entry = pair[0];
                let window_exit = pair[1];
                let active_legs = strategy
                    .legs
                    .iter()
                    .map(|leg| leg_active_for_window(leg, window_entry, window_exit))
                    .collect::<Vec<_>>();
                if !active_legs.iter().any(|active| *active) {
                    continue;
                }
                let entry_spot = self
                    .market
                    .spot(&symbol, window_entry)
                    .map(|value| value.close)
                    .ok_or_else(|| {
                        EngineError::MissingMarketData(format!("spot {symbol} {window_entry}"))
                    })?;
                let exit_spot = self
                    .market
                    .spot(&symbol, window_exit)
                    .map(|value| value.close)
                    .ok_or_else(|| {
                        EngineError::MissingMarketData(format!("spot {symbol} {window_exit}"))
                    })?;
                let (effective_exit, effective_exit_spot) = self.spot_adjusted_exit(
                    strategy,
                    &symbol,
                    window_entry,
                    window_exit,
                    entry_spot,
                    exit_spot,
                    &trading_days,
                )?;
                let synthetic_entry = window_index > 0;
                let synthetic_exit = window_index + 1 < window_points.len() - 1;
                let carry = strategy
                    .legs
                    .iter()
                    .enumerate()
                    .map(|(index, leg)| {
                        synthetic_entry && active_legs[index] && leg.filter_segments.is_empty()
                    })
                    .collect::<Vec<_>>();
                let suppress_entry = carry.clone();
                let suppress_exit = strategy
                    .legs
                    .iter()
                    .enumerate()
                    .map(|(index, leg)| {
                        synthetic_exit && active_legs[index] && leg.filter_segments.is_empty()
                    })
                    .collect::<Vec<_>>();
                trade_id += 1;
                let mut group = self.price_trade_group(
                    strategy,
                    &symbol,
                    trade_id,
                    window_entry,
                    effective_exit,
                    &leg_expiries,
                    entry_spot,
                    effective_exit_spot,
                    &trading_days,
                    &mut fixed_strikes,
                    &carry,
                    &active_legs,
                    &suppress_entry,
                    &suppress_exit,
                    active_segment,
                )?;
                if synthetic_exit && effective_exit == window_exit {
                    for row in &mut group {
                        if row.exit_date == window_exit.to_string() && row.exit_reason == "EXPIRY" {
                            row.exit_reason = "LEG_FILTER_END".into();
                        }
                    }
                }
                rows.extend(group);
            }
        }
        // Canonical output order matches the current live exporter and prevents
        // incidental insertion order from becoming an analytics input.
        rows.sort_by(|left, right| {
            left.entry_date
                .cmp(&right.entry_date)
                .then(left.trade_id.cmp(&right.trade_id))
                .then(left.leg_id.cmp(&right.leg_id))
                .then(left.leg_label.cmp(&right.leg_label))
        });
        Ok(rows)
    }

    #[allow(clippy::too_many_arguments)]
    fn run_options_per_leg(
        &self,
        strategy: &StrategyConfig,
        from: NaiveDate,
        to: NaiveDate,
        symbol: &str,
        trading_days: &[NaiveDate],
        all_expiries: &[NaiveDate],
        listed_expiries: &[NaiveDate],
        filter_segments: &[(NaiveDate, NaiveDate)],
    ) -> Result<Vec<TradeRow>, EngineError> {
        let run_start = trading_days
            .iter()
            .copied()
            .find(|day| *day >= from)
            .ok_or_else(|| EngineError::MissingMarketData("per-leg rollover start".into()))?;
        let monthly = || {
            let mut values = std::collections::BTreeMap::new();
            for expiry in all_expiries {
                values.insert((expiry.year(), expiry.month()), *expiry);
            }
            values.into_values().collect::<Vec<_>>()
        };
        let mut cycles = Vec::with_capacity(strategy.legs.len());
        let mut exit_dtes = Vec::with_capacity(strategy.legs.len());
        let mut refresh_boundaries = Vec::with_capacity(strategy.legs.len());
        let mut yearly_legs = Vec::with_capacity(strategy.legs.len());
        let mut attribution_boundaries = filter_segments
            .iter()
            .flat_map(|(start, end)| [*start, *end])
            .collect::<Vec<_>>();
        let mut individual_boundaries = BTreeSet::new();
        let mut leg_individual_boundaries = Vec::with_capacity(strategy.legs.len());
        for leg in &strategy.legs {
            let mut own = BTreeSet::new();
            for segment in &leg.filter_segments {
                if let Some(start) = trading_days.iter().copied().find(|day| *day >= segment.start)
                {
                    own.insert(start);
                    individual_boundaries.insert(start);
                    attribution_boundaries.push(start);
                }
                if let Some(end) = trading_days
                    .iter()
                    .copied()
                    .filter(|day| *day <= segment.end)
                    .max()
                {
                    own.insert(end);
                    individual_boundaries.insert(end);
                    attribution_boundaries.push(end);
                }
            }
            leg_individual_boundaries.push(own);
        }
        for (leg_index, leg) in strategy.legs.iter().enumerate() {
            let cadence = leg
                .expiry
                .as_deref()
                .or(strategy.expiry_type.as_deref())
                .unwrap_or("WEEKLY")
                .to_ascii_uppercase();
            let (calendar, leg_exit_dte, refreshes, is_yearly) = if cadence.contains("YEAR") {
                let mut december = std::collections::BTreeMap::new();
                for expiry in listed_expiries.iter().copied().filter(|date| date.month() == 12) {
                    december.insert(expiry.year(), expiry);
                }
                let months_before = leg
                    .extra
                    .get("yearly_exit_months_before")
                    .or_else(|| strategy.extra.get("yearly_exit_months_before"))
                    .and_then(|value| value.as_u64())
                    .unwrap_or(0) as u32;
                let calendar = december
                    .into_values()
                    .filter_map(|contract| {
                        let raw_exit = subtract_months_clamped(contract, months_before)?;
                        let exit = trading_days
                            .iter()
                            .copied()
                            .filter(|day| *day <= raw_exit)
                            .max()?;
                        (exit > run_start).then_some((exit, contract))
                    })
                    .collect::<Vec<_>>();
                let roll_cadence = leg
                    .extra
                    .get("rollover_cadence")
                    .or_else(|| strategy.extra.get("rollover_cadence"))
                    .and_then(|value| value.as_str())
                    .unwrap_or("monthly")
                    .to_ascii_lowercase();
                let cadence_dates = if roll_cadence.starts_with("week") {
                    all_expiries.to_vec()
                } else {
                    monthly()
                };
                let monthly_refresh = monthly().into_iter().collect::<BTreeSet<_>>();
                // Cadence dates create attribution rows. Fresh yearly strikes
                // refresh only on monthly expiries; fixed mode ignores refresh.
                attribution_boundaries.extend(cadence_dates);
                (calendar, 0usize, monthly_refresh, true)
            } else if cadence.contains("MONTH")
                || leg
                    .segment
                    .as_deref()
                    .is_some_and(|value| value.eq_ignore_ascii_case("FUTURES"))
            {
                (monthly().into_iter().map(|date| (date, date)).collect(),
                 leg.extra.get("exit_dte").and_then(|value| value.as_u64()).unwrap_or_else(|| strategy.exit_dte.unwrap_or(0) as u64) as usize,
                 BTreeSet::new(), false)
            } else if cadence.contains("NEXT_WEEK") {
                (all_expiries.iter().copied().skip(1).map(|date| (date, date)).collect(),
                 leg.extra.get("exit_dte").and_then(|value| value.as_u64()).unwrap_or_else(|| strategy.exit_dte.unwrap_or(0) as u64) as usize,
                 BTreeSet::new(), false)
            } else {
                (all_expiries.iter().copied().map(|date| (date, date)).collect(),
                 leg.extra.get("exit_dte").and_then(|value| value.as_u64()).unwrap_or_else(|| strategy.exit_dte.unwrap_or(0) as u64) as usize,
                 BTreeSet::new(), false)
            };
            if calendar.is_empty() {
                return Err(EngineError::MissingMarketData(format!(
                    "per-leg rollover expiry calendar for leg {}",
                    leg_index + 1
                )));
            }
            cycles.push(calendar);
            exit_dtes.push(leg_exit_dte);
            refresh_boundaries.push(refreshes);
            yearly_legs.push(is_yearly);
        }
        let schedule = build_per_leg_roll_schedule(
            run_start,
            &cycles,
            &exit_dtes,
            trading_days,
            &attribution_boundaries,
            &refresh_boundaries,
        )?;
        let mut rows = Vec::new();
        let mut trade_id = 0u64;
        let mut epoch_strikes = vec![None; strategy.legs.len()];
        let mut spot_anchors = vec![None; strategy.legs.len()];
        let mut last_yearly_contracts = vec![None; strategy.legs.len()];
        for (schedule_index, scheduled) in schedule.iter().enumerate() {
            if scheduled.entry < from || scheduled.exit > to || scheduled.entry >= scheduled.exit {
                continue;
            }
            let active_segment = if filter_segments.is_empty() {
                None
            } else {
                filter_segments
                    .iter()
                    .copied()
                    .find(|(start, end)| *start <= scheduled.entry && scheduled.entry <= *end)
            };
            if !filter_segments.is_empty() && active_segment.is_none() {
                continue;
            }
            let scheduled_exit = active_segment
                .map(|(_, end)| scheduled.exit.min(end))
                .unwrap_or(scheduled.exit);
            if scheduled.entry >= scheduled_exit {
                continue;
            }
            let leg_expiries = scheduled
                .slots
                .iter()
                .map(|slot| slot.contract)
                .collect::<Vec<_>>();
            let active_legs = strategy
                .legs
                .iter()
                .map(|leg| leg_active_for_window(leg, scheduled.entry, scheduled_exit))
                .collect::<Vec<_>>();
            if !active_legs.iter().any(|active| *active) {
                continue;
            }
            let mut entry_date = scheduled.entry;
            let mut carry = scheduled
                .slots
                .iter()
                .map(|slot| !(slot.own_boundary || slot.refresh_boundary))
                .collect::<Vec<_>>();
            for (leg_index, own) in leg_individual_boundaries.iter().enumerate() {
                if own.contains(&scheduled.entry) {
                    carry[leg_index] = false;
                }
            }
            for (leg_index, slot) in scheduled.slots.iter().enumerate() {
                if yearly_legs[leg_index] && slot.own_boundary {
                    // Fixed is scoped to one pinned December contract, not the
                    // whole backtest. A yearly handoff always resolves fresh.
                    epoch_strikes[leg_index] = None;
                }
            }
            if active_segment.is_some_and(|(start, _)| start == scheduled.entry) {
                carry.fill(false);
            }
            let schedule_entry_spot = self
                .market
                .spot(symbol, scheduled.entry)
                .map(|value| value.close)
                .ok_or_else(|| {
                    EngineError::MissingMarketData(format!("spot {symbol} {}", scheduled.entry))
                })?;
            for (leg_index, slot) in scheduled.slots.iter().enumerate() {
                if slot.own_boundary || slot.refresh_boundary || spot_anchors[leg_index].is_none() {
                    spot_anchors[leg_index] = Some(schedule_entry_spot);
                }
            }
            loop {
                let entry_spot = self
                    .market
                    .spot(symbol, entry_date)
                    .map(|value| value.close)
                    .ok_or_else(|| {
                        EngineError::MissingMarketData(format!("spot {symbol} {entry_date}"))
                    })?;
                let exit_spot = self
                    .market
                    .spot(symbol, scheduled_exit)
                    .map(|value| value.close)
                    .ok_or_else(|| {
                        EngineError::MissingMarketData(format!("spot {symbol} {scheduled_exit}"))
                    })?;
                let (effective_exit, effective_exit_spot, breached_legs) = self
                    .per_leg_spot_adjusted_exit(
                    strategy,
                    symbol,
                    entry_date,
                    scheduled_exit,
                    entry_spot,
                    exit_spot,
                    trading_days,
                    &spot_anchors,
                    &leg_expiries,
                )?;
                let suppress_entry = strategy
                    .legs
                    .iter()
                    .enumerate()
                    .map(|(leg_index, leg)| {
                        active_legs[leg_index]
                            && individual_boundaries.contains(&entry_date)
                            && (leg.filter_segments.is_empty()
                                || !leg_individual_boundaries[leg_index].contains(&entry_date))
                    })
                    .collect::<Vec<_>>();
                let suppress_exit = strategy
                    .legs
                    .iter()
                    .enumerate()
                    .map(|(leg_index, leg)| {
                        active_legs[leg_index]
                            && individual_boundaries.contains(&effective_exit)
                            && (leg.filter_segments.is_empty()
                                || !leg_individual_boundaries[leg_index].contains(&effective_exit))
                    })
                    .collect::<Vec<_>>();
                trade_id += 1;
                let mut group = self.price_trade_group(
                    strategy,
                    symbol,
                    trade_id,
                    entry_date,
                    effective_exit,
                    &leg_expiries,
                    entry_spot,
                    effective_exit_spot,
                    trading_days,
                    &mut epoch_strikes,
                    &carry,
                    &active_legs,
                    &suppress_entry,
                    &suppress_exit,
                    active_segment,
                )?;
                let breached_labels = breached_legs
                    .iter()
                    .enumerate()
                    .filter(|(_, breached)| **breached)
                    .map(|(index, _)| format!("L{}", index + 1))
                    .collect::<Vec<_>>();
                let roll_labels = schedule
                    .get(schedule_index + 1)
                    .filter(|next| next.entry == scheduled_exit)
                    .map(|next| {
                        next.slots
                            .iter()
                            .enumerate()
                            .filter(|(_, slot)| slot.own_boundary || slot.refresh_boundary)
                            .map(|(index, _)| format!("L{}", index + 1))
                            .collect::<Vec<_>>()
                    })
                    .unwrap_or_default();
                let reason = match (
                    breached_labels.is_empty(),
                    effective_exit == scheduled_exit,
                    roll_labels.is_empty(),
                ) {
                    (false, true, false) => format!(
                        "SPOT_ADJ:{} + SCHEDULED_EXIT:{}",
                        breached_labels.join("+"),
                        roll_labels.join("+")
                    ),
                    (false, _, _) => format!("SPOT_ADJ:{}", breached_labels.join("+")),
                    (true, true, false) => {
                        format!("SCHEDULED_EXIT:{}", roll_labels.join("+"))
                    }
                    _ => "SCHEDULED_EXIT".into(),
                };
                let mut yearly_reason_written = vec![false; strategy.legs.len()];
                for row in &mut group {
                    if row.exit_date == effective_exit.to_string() && row.exit_reason == "EXPIRY" {
                        row.exit_reason = if individual_boundaries.contains(&effective_exit) {
                            "LEG_FILTER_END".into()
                        } else {
                            reason.clone()
                        };
                    }
                    row.annotations
                        .insert("per_leg_rollover".into(), "true".into());
                    let leg_index = row.leg_id.saturating_sub(1) as usize;
                    if yearly_legs.get(leg_index) == Some(&true)
                        && last_yearly_contracts[leg_index] != Some(leg_expiries[leg_index])
                        && !yearly_reason_written[leg_index]
                    {
                        if let Some((gap, adjustment)) = yearly_contract_override(
                            &strategy.legs[leg_index],
                            leg_expiries[leg_index],
                        ) {
                            row.annotations.insert(
                                "strike_shift_reason".into(),
                                format!(
                                    "YEARLY_ROLL -> {} (gap {}, adj {})",
                                    leg_expiries[leg_index].format("%b-%Y"),
                                    display_optional_number(gap),
                                    display_optional_number(adjustment)
                                ),
                            );
                            yearly_reason_written[leg_index] = true;
                        }
                    }
                }
                rows.extend(group);
                for (leg_index, is_yearly) in yearly_legs.iter().copied().enumerate() {
                    if is_yearly {
                        last_yearly_contracts[leg_index] = Some(leg_expiries[leg_index]);
                    }
                }
                if effective_exit >= scheduled_exit {
                    break;
                }
                entry_date = effective_exit;
                // Only the leg(s) that breached re-strike. Every sibling carries
                // its contract and epoch strike across the foreign boundary.
                for (leg_index, breached) in breached_legs.iter().copied().enumerate() {
                    carry[leg_index] = !breached;
                    if breached {
                        spot_anchors[leg_index] = Some(effective_exit_spot);
                    }
                }
            }
        }
        rows.sort_by(|left, right| {
            left.entry_date
                .cmp(&right.entry_date)
                .then(left.trade_id.cmp(&right.trade_id))
                .then(left.leg_id.cmp(&right.leg_id))
                .then(left.leg_label.cmp(&right.leg_label))
        });
        Ok(rows)
    }

    #[allow(clippy::too_many_arguments)]
    fn price_trade_group(
        &self,
        strategy: &StrategyConfig,
        symbol: &str,
        trade_id: u64,
        entry_date: NaiveDate,
        effective_exit: NaiveDate,
        leg_expiries: &[NaiveDate],
        entry_spot: f64,
        effective_exit_spot: f64,
        trading_days: &[NaiveDate],
        epoch_strikes: &mut [Option<f64>],
        carry_strike: &[bool],
        active_legs: &[bool],
        suppress_entry_slippage: &[bool],
        suppress_exit_slippage: &[bool],
        active_segment: Option<(NaiveDate, NaiveDate)>,
    ) -> Result<Vec<TradeRow>, EngineError> {
        if leg_expiries.len() != strategy.legs.len()
            || epoch_strikes.len() != strategy.legs.len()
            || carry_strike.len() != strategy.legs.len()
            || active_legs.len() != strategy.legs.len()
            || suppress_entry_slippage.len() != strategy.legs.len()
            || suppress_exit_slippage.len() != strategy.legs.len()
        {
            return Err(EngineError::Calculation(
                "scheduled leg vectors are not index-aligned".into(),
            ));
        }
        let mut rows = Vec::with_capacity(strategy.legs.len());
        let mut resolved_strikes = Vec::<Option<f64>>::with_capacity(strategy.legs.len());
        let mut resolved_premiums = Vec::<Option<f64>>::with_capacity(strategy.legs.len());
        for (leg_index, configured_leg) in strategy.legs.iter().enumerate() {
            if !active_legs[leg_index] {
                continue;
            }
            let leg_expiry = leg_expiries[leg_index];
            let mut effective = configured_leg.clone();
            if !resolved_strikes.is_empty() {
                effective.extra.insert(
                    "_resolved_strikes".into(),
                    serde_json::Value::Array(
                        resolved_strikes
                            .iter()
                            .map(|value| value.map(json_number).unwrap_or(serde_json::Value::Null))
                            .collect(),
                    ),
                );
                effective.extra.insert(
                    "_resolved_premiums".into(),
                    serde_json::Value::Array(
                        resolved_premiums
                            .iter()
                            .map(|value| value.map(json_number).unwrap_or(serde_json::Value::Null))
                            .collect(),
                    ),
                );
            }
            if suppress_entry_slippage[leg_index] {
                effective
                    .extra
                    .insert("_suppress_entry_slippage".into(), serde_json::Value::Bool(true));
            }
            if suppress_exit_slippage[leg_index] {
                effective
                    .extra
                    .insert("_suppress_exit_slippage".into(), serde_json::Value::Bool(true));
            }
            if configured_leg
                .expiry
                .as_deref()
                .is_some_and(|value| value.to_ascii_uppercase().contains("YEAR"))
            {
                if let Some((Some(gap), _)) =
                    yearly_contract_override(configured_leg, leg_expiry)
                {
                    effective.extra.insert("strike_interval".into(), json_number(gap));
                }
            }
            let leg = &effective;
            let segment = leg
                .segment
                .as_deref()
                .unwrap_or("OPTIONS")
                .to_ascii_uppercase();
            if segment == "FUTURES" || leg.option_type.as_deref() == Some("FUT") {
                rows.push(self.price_future_leg(
                    strategy,
                    leg,
                    trade_id,
                    leg_index,
                    entry_date,
                    effective_exit,
                    leg_expiry,
                    entry_spot,
                    effective_exit_spot,
                )?);
                resolved_strikes.push(None);
                resolved_premiums.push(Some(rows.last().map(|row| row.entry_price).unwrap_or(0.0)));
                continue;
            }

            let fixed_mode = leg
                .extra
                .get("rollover_strike_mode")
                .and_then(|value| value.as_str())
                .is_some_and(|value| value.eq_ignore_ascii_case("fixed"));
            let forced_strike = if fixed_mode || carry_strike[leg_index] {
                epoch_strikes[leg_index]
            } else {
                None
            };
            let forced_strike = forced_strike
                .map(|strike| {
                    self.revalidate_carried_strike(
                        strategy,
                        leg,
                        symbol,
                        entry_date,
                        leg_expiry,
                        strike,
                    )
                })
                .transpose()?;
            let priced = self.price_option_leg(
                strategy,
                leg,
                trade_id,
                leg_index,
                entry_date,
                effective_exit,
                leg_expiry,
                entry_spot,
                effective_exit_spot,
                trading_days,
                forced_strike,
            )?;
            if !fixed_mode || epoch_strikes[leg_index].is_none() {
                epoch_strikes[leg_index] = Some(priced.strike);
            }
            let trigger = priced.exit_reason.clone();
            let reentry_strike = priced.strike;
            resolved_strikes.push(Some(priced.strike));
            resolved_premiums.push(Some(priced.entry_price));
            let reentry_start = parse_row_date(&priced.exit_date)?;
            rows.push(priced);
            let reentry = if trigger == "SL" {
                leg.reentry_on_sl.as_ref().map(|config| (config, "SL"))
            } else if trigger == "TARGET" {
                leg.reentry_on_target
                    .as_ref()
                    .map(|config| (config, "TARGET"))
            } else {
                None
            };
            if let Some((config, trigger_name)) = reentry {
                let count = config.count.unwrap_or(1).min(20);
                let mode = config
                    .mode
                    .as_deref()
                    .unwrap_or("RE_ASAP")
                    .to_ascii_uppercase();
                let mut next_entry = reentry_start;
                for reentry_index in 1..=count {
                    if next_entry >= effective_exit {
                        break;
                    }
                    if matches!(mode.as_str(), "RE_MOMENTUM" | "RE_MOMENTUM_REV") {
                        let Some(momentum_date) = self.momentum_reentry_date(
                            symbol,
                            next_entry,
                            effective_exit,
                            leg_expiry,
                            reentry_strike,
                            leg.option_type.as_deref().unwrap_or("CE"),
                            leg.position.as_deref().unwrap_or("SELL"),
                            trigger_name == "SL",
                            trading_days,
                        ) else {
                            break;
                        };
                        next_entry = momentum_date;
                    }
                    let mut reentry_leg = if mode == "LAZY_LEG" {
                        let Some(lazy) = config.lazy_leg_config.as_deref() else {
                            break;
                        };
                        lazy.clone()
                    } else {
                        leg.clone()
                    };
                    reentry_leg.extra.remove("_suppress_entry_slippage");
                    reentry_leg.extra.remove("_suppress_exit_slippage");
                    if mode.ends_with("_REV") {
                        reentry_leg.position = Some(
                            if leg
                                .position
                                .as_deref()
                                .is_some_and(|value| value.eq_ignore_ascii_case("SELL"))
                            {
                                "BUY"
                            } else {
                                "SELL"
                            }
                            .into(),
                        );
                    }
                    let mut row = self.price_option_reentry(
                        strategy,
                        &reentry_leg,
                        trade_id,
                        leg_index,
                        next_entry,
                        effective_exit,
                        leg_expiry,
                        trading_days,
                    )?;
                    row.annotations
                        .insert("reentry_index".into(), reentry_index.to_string());
                    row.annotations
                        .insert("reentry_trigger".into(), trigger_name.into());
                    row.annotations.insert("reentry_mode".into(), mode.clone());
                    if mode == "LAZY_LEG" {
                        row.leg_label = Some(format!("lazy{reentry_index}"));
                        row.annotations.insert("lazy_leg".into(), "true".into());
                        row.annotations
                            .insert("lazy_leg_name".into(), format!("lazy{reentry_index}"));
                    }
                    let repeat = (row.exit_reason == "SL" && trigger_name == "SL")
                        || (row.exit_reason == "TARGET" && trigger_name == "TARGET");
                    next_entry = parse_row_date(&row.exit_date)?;
                    rows.push(row);
                    if mode == "LAZY_LEG" || !repeat {
                        break;
                    }
                }
            }
        }
        if rows.is_empty() {
            return Ok(rows);
        }
        self.apply_overall_risk(
            strategy,
            symbol,
            0,
            &mut rows,
            entry_date,
            effective_exit,
            trading_days,
        )?;
        let group_total = rows.iter().map(|row| row.leg_pnl).sum();
        let Some(parent) = rows.first_mut() else {
            return Err(EngineError::Calculation(
                "scheduled trade produced no legs".into(),
            ));
        };
        parent.net_pnl = group_total;
        if let Some((start, end)) = active_segment {
            for row in &mut rows {
                row.annotations
                    .insert("filter_segment".into(), format!("{start} -> {end}"));
            }
        }
        Ok(rows)
    }

    #[allow(clippy::too_many_arguments)]
    fn momentum_reentry_date(
        &self,
        symbol: &str,
        trigger_date: NaiveDate,
        cycle_exit: NaiveDate,
        expiry: NaiveDate,
        strike: f64,
        option_type: &str,
        original_position: &str,
        stop_trigger: bool,
        trading_days: &[NaiveDate],
    ) -> Option<NaiveDate> {
        let trigger_price = self
            .option_ohlc_tolerant(symbol, trigger_date, expiry, strike, option_type)?
            .close;
        let sell = original_position.eq_ignore_ascii_case("SELL");
        trading_days
            .iter()
            .copied()
            .filter(|day| trigger_date < *day && *day <= cycle_exit)
            .find(|day| {
                self.option_ohlc_tolerant(symbol, *day, expiry, strike, option_type)
                    .is_some_and(|ohlc| {
                        if stop_trigger {
                            if sell {
                                ohlc.close >= trigger_price
                            } else {
                                ohlc.close <= trigger_price
                            }
                        } else if sell {
                            ohlc.close <= trigger_price
                        } else {
                            ohlc.close >= trigger_price
                        }
                    })
            })
    }

    #[allow(clippy::too_many_arguments)]
    fn apply_overall_risk(
        &self,
        strategy: &StrategyConfig,
        symbol: &str,
        group_start: usize,
        rows: &mut [TradeRow],
        entry_date: NaiveDate,
        scheduled_exit: NaiveDate,
        trading_days: &[NaiveDate],
    ) -> Result<(), EngineError> {
        let stop = strategy.overall_sl_value.filter(|value| *value > 0.0);
        let target = strategy.overall_target_value.filter(|value| *value > 0.0);
        if stop.is_none() && target.is_none() {
            return Ok(());
        }
        let group = &rows[group_start..];
        let stop_mode = overall_mode(strategy.extra.get("overall_sl_type"));
        let target_mode = overall_mode(strategy.extra.get("overall_target_type"));
        let stop_is_underlying = matches!(stop_mode, "underlying_pts" | "underlying_pct");
        let target_is_underlying = matches!(target_mode, "underlying_pts" | "underlying_pct");
        let weighted_entry_premium = group
            .iter()
            .map(|row| {
                let leg = &strategy.legs[row.leg_id.saturating_sub(1) as usize];
                row.entry_price * leg_multiplier(leg)
            })
            .sum::<f64>();
        let threshold = |value: f64, mode: &str| {
            if mode == "total_premium_pct" {
                weighted_entry_premium * value / 100.0
            } else {
                value
            }
        };
        let stop_threshold = stop.map(|value| threshold(value, stop_mode));
        let target_threshold = target.map(|value| threshold(value, target_mode));
        let mut trigger = None;
        for day in trading_days
            .iter()
            .copied()
            .filter(|day| *day > entry_date && *day <= scheduled_exit)
        {
            let mut combined = 0.0;
            let mut complete = true;
            for row in group {
                let leg = &strategy.legs[row.leg_id.saturating_sub(1) as usize];
                let weight = leg_multiplier(leg);
                let row_exit = parse_row_date(&row.exit_date)?;
                if row_exit < day {
                    combined += row.leg_pnl;
                    continue;
                }
                let expiry = parse_row_date(&row.expiry)?;
                let ohlc = if row.instrument == "FUTURES" {
                    self.market.future_ohlc(symbol, day, expiry)
                } else {
                    self.option_ohlc_tolerant(symbol, day, expiry, row.strike, &row.option_type)
                };
                let Some(ohlc) = ohlc else {
                    complete = false;
                    break;
                };
                let slippage = leg.slippage_pct.unwrap_or_else(|| {
                    strategy
                        .extra
                        .get("slippage_pct")
                        .and_then(|value| value.as_f64())
                        .unwrap_or(0.0)
                });
                let sell = row.position == "SELL";
                let exit = round2(
                    ohlc.close
                        * if sell {
                            1.0 + slippage / 100.0
                        } else {
                            1.0 - slippage / 100.0
                        },
                );
                combined += if sell {
                    row.entry_price - exit
                } else {
                    exit - row.entry_price
                } * weight;
            }
            if !complete {
                continue;
            }
            let spot_move = self
                .market
                .spot(symbol, day)
                .map(|spot| spot.close - group[0].entry_spot);
            let adverse_spot = spot_move.map(|movement| {
                let rising_is_adverse = if group[0].instrument == "FUTURES" {
                    group[0].position == "SELL"
                } else {
                    (group[0].option_type == "CE" && group[0].position == "SELL")
                        || (group[0].option_type == "PE" && group[0].position == "BUY")
                };
                if rising_is_adverse {
                    movement
                } else {
                    -movement
                }
            });
            let underlying_value = |mode: &str, adverse: f64| {
                if mode == "underlying_pct" {
                    if group[0].entry_spot == 0.0 {
                        0.0
                    } else {
                        adverse / group[0].entry_spot * 100.0
                    }
                } else {
                    adverse
                }
            };
            let stop_hit = if stop_is_underlying {
                stop_threshold
                    .zip(adverse_spot)
                    .is_some_and(|(threshold, adverse)| {
                        underlying_value(stop_mode, adverse) >= threshold
                    })
            } else {
                stop_threshold.is_some_and(|value| combined <= -value)
            };
            if stop_hit {
                trigger = Some((day, "OVERALL_SL"));
                break;
            }
            let target_hit = if target_is_underlying {
                target_threshold
                    .zip(adverse_spot)
                    .is_some_and(|(threshold, adverse)| {
                        underlying_value(target_mode, -adverse) >= threshold
                    })
            } else {
                target_threshold.is_some_and(|value| combined >= value)
            };
            if target_hit {
                trigger = Some((day, "OVERALL_TARGET"));
                break;
            }
        }
        let Some((day, reason)) = trigger else {
            return Ok(());
        };
        let exit_spot = self
            .market
            .spot(symbol, day)
            .map(|value| value.close)
            .ok_or_else(|| EngineError::MissingMarketData(format!("spot {symbol} {day}")))?;
        for row in &mut rows[group_start..] {
            if parse_row_date(&row.exit_date)? < day {
                continue;
            }
            let leg = &strategy.legs[row.leg_id.saturating_sub(1) as usize];
            let expiry = parse_row_date(&row.expiry)?;
            let ohlc = if row.instrument == "FUTURES" {
                self.market.future_ohlc(symbol, day, expiry)
            } else {
                self.option_ohlc_tolerant(symbol, day, expiry, row.strike, &row.option_type)
            }
            .ok_or_else(|| {
                EngineError::MissingMarketData(format!(
                    "overall exit {symbol} {day} {expiry} {} {}",
                    row.strike, row.option_type
                ))
            })?;
            let slippage = leg.slippage_pct.unwrap_or_else(|| {
                strategy
                    .extra
                    .get("slippage_pct")
                    .and_then(|value| value.as_f64())
                    .unwrap_or(0.0)
            });
            let sell = row.position == "SELL";
            row.exit_price = round2(
                ohlc.close
                    * if sell {
                        1.0 + slippage / 100.0
                    } else {
                        1.0 - slippage / 100.0
                    },
            );
            let points_pnl = if sell {
                row.entry_price - row.exit_price
            } else {
                row.exit_price - row.entry_price
            };
            row.net_pnl = round4(points_pnl * leg_multiplier(leg));
            row.leg_pnl = row.net_pnl;
            row.exit_date = day.to_string();
            row.exit_spot = exit_spot;
            row.exit_reason = reason.into();
            (row.mae, row.mfe) = if row.instrument == "FUTURES" {
                self.future_mae_mfe(
                    symbol,
                    entry_date,
                    day,
                    expiry,
                    &row.position,
                    row.entry_price,
                    row.entry_spot,
                )
            } else {
                self.option_mae_mfe(
                    symbol,
                    entry_date,
                    day,
                    expiry,
                    row.strike,
                    &row.option_type,
                    &row.position,
                    row.entry_price,
                    row.entry_spot,
                    trading_days,
                )
            };
            scale_excursions(row, leg);
        }
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    fn spot_adjusted_exit(
        &self,
        strategy: &StrategyConfig,
        symbol: &str,
        entry_date: NaiveDate,
        scheduled_exit: NaiveDate,
        entry_spot: f64,
        scheduled_exit_spot: f64,
        trading_days: &[NaiveDate],
    ) -> Result<(NaiveDate, f64), EngineError> {
        if !strategy.spot_adjustment_enabled {
            return Ok((scheduled_exit, scheduled_exit_spot));
        }
        let threshold = strategy.spot_adjustment_pct.unwrap_or(0.0).abs();
        if threshold <= 0.0 {
            return Ok((scheduled_exit, scheduled_exit_spot));
        }
        let direction = strategy
            .spot_adjustment_direction
            .as_deref()
            .unwrap_or("both")
            .trim()
            .to_ascii_lowercase();
        let units = strategy
            .extra
            .get("spot_adjustment_units")
            .and_then(|value| value.as_str())
            .unwrap_or("percent")
            .trim()
            .to_ascii_lowercase();
        for day in trading_days
            .iter()
            .copied()
            .filter(|day| *day > entry_date && *day <= scheduled_exit)
        {
            let current = self
                .market
                .spot(symbol, day)
                .map(|value| value.close)
                .ok_or_else(|| EngineError::MissingMarketData(format!("spot {symbol} {day}")))?;
            let movement = if units.starts_with("point") {
                current - entry_spot
            } else {
                (current - entry_spot) / entry_spot * 100.0
            };
            let hit = match direction.as_str() {
                "up" | "upside" => movement >= threshold,
                "down" | "downside" => movement <= -threshold,
                _ => movement.abs() >= threshold,
            };
            if hit {
                return Ok((day, current));
            }
        }
        Ok((scheduled_exit, scheduled_exit_spot))
    }

    #[allow(clippy::too_many_arguments)]
    fn per_leg_spot_adjusted_exit(
        &self,
        strategy: &StrategyConfig,
        symbol: &str,
        entry_date: NaiveDate,
        scheduled_exit: NaiveDate,
        entry_spot: f64,
        scheduled_exit_spot: f64,
        trading_days: &[NaiveDate],
        anchors: &[Option<f64>],
        leg_expiries: &[NaiveDate],
    ) -> Result<(NaiveDate, f64, Vec<bool>), EngineError> {
        let (strategy_exit, strategy_spot) = self.spot_adjusted_exit(
            strategy,
            symbol,
            entry_date,
            scheduled_exit,
            entry_spot,
            scheduled_exit_spot,
            trading_days,
        )?;
        let mut earliest = strategy_exit;
        let mut earliest_spot = strategy_spot;
        let mut breached = vec![strategy_exit < scheduled_exit; strategy.legs.len()];
        for day in trading_days
            .iter()
            .copied()
            .filter(|day| *day > entry_date && *day <= earliest)
        {
            let current = self
                .market
                .spot(symbol, day)
                .map(|value| value.close)
                .ok_or_else(|| EngineError::MissingMarketData(format!("spot {symbol} {day}")))?;
            let mut day_breaches = vec![false; strategy.legs.len()];
            for (leg_index, leg) in strategy.legs.iter().enumerate() {
                let Some(config) = leg.spot_adjustment.as_ref().filter(|value| value.enabled)
                else {
                    continue;
                };
                let threshold = yearly_contract_override(
                    leg,
                    leg_expiries.get(leg_index).copied().unwrap_or(scheduled_exit),
                )
                .and_then(|(_, adjustment)| adjustment)
                .or(config.pct)
                .unwrap_or(0.0)
                .abs();
                let Some(anchor) = anchors.get(leg_index).copied().flatten() else {
                    continue;
                };
                if threshold <= 0.0 || anchor == 0.0 {
                    continue;
                }
                let movement = if config
                    .units
                    .as_deref()
                    .unwrap_or("percent")
                    .to_ascii_lowercase()
                    .starts_with("point")
                {
                    current - anchor
                } else {
                    (current - anchor) / anchor * 100.0
                };
                let direction = config
                    .direction
                    .as_deref()
                    .unwrap_or("both")
                    .to_ascii_lowercase();
                day_breaches[leg_index] = match direction.as_str() {
                    "up" | "rise" | "upside" => movement >= threshold,
                    "down" | "fall" | "downside" => movement <= -threshold,
                    _ => movement.abs() >= threshold,
                };
            }
            if day_breaches.iter().any(|value| *value) {
                if day < earliest {
                    earliest = day;
                    earliest_spot = current;
                    breached.fill(false);
                }
                if day == earliest {
                    for (target, source) in breached.iter_mut().zip(day_breaches) {
                        *target |= source;
                    }
                }
                break;
            }
        }
        Ok((earliest, earliest_spot, breached))
    }

    #[allow(clippy::too_many_arguments)]
    fn price_option_leg(
        &self,
        strategy: &StrategyConfig,
        leg: &LegConfig,
        trade_id: u64,
        leg_index: usize,
        entry_date: NaiveDate,
        scheduled_exit: NaiveDate,
        expiry: NaiveDate,
        entry_spot: f64,
        scheduled_exit_spot: f64,
        trading_days: &[NaiveDate],
        forced_strike: Option<f64>,
    ) -> Result<TradeRow, EngineError> {
        let symbol = leg
            .index
            .as_deref()
            .unwrap_or(&strategy.index)
            .trim()
            .to_ascii_uppercase();
        let option_type = leg
            .option_type
            .as_deref()
            .unwrap_or("CE")
            .to_ascii_uppercase();
        let position = leg.position.as_deref().unwrap_or("").to_ascii_uppercase();
        let interval = leg
            .extra
            .get("strike_interval")
            .and_then(|value| value.as_f64())
            .unwrap_or_else(|| default_strike_interval(&symbol));
        let strike = match forced_strike {
            Some(strike) => strike,
            None => self.resolve_strike_for_date(
                leg,
                &symbol,
                entry_date,
                expiry,
                entry_spot,
                interval,
                &option_type,
            )?,
        };
        let entry = self
            .option_ohlc_tolerant(&symbol, entry_date, expiry, strike, &option_type)
            .ok_or_else(|| {
                EngineError::MissingMarketData(format!(
                    "option {symbol} {entry_date} {expiry} {strike} {option_type}"
                ))
            })?;
        let (exit_date, exit, exit_reason) = self.scan_option_exit(
            leg,
            &symbol,
            entry_date,
            scheduled_exit,
            expiry,
            strike,
            &option_type,
            &position,
            entry.close,
            trading_days,
        )?;
        let exit_spot = if exit_date == scheduled_exit {
            scheduled_exit_spot
        } else {
            self.market
                .spot(&symbol, exit_date)
                .map(|value| value.close)
                .ok_or_else(|| {
                    EngineError::MissingMarketData(format!("spot {symbol} {exit_date}"))
                })?
        };
        let mut row = build_row(
            strategy,
            leg,
            trade_id,
            leg_index,
            entry_date,
            exit_date,
            expiry,
            strike,
            "OPTIONS",
            &option_type,
            &position,
            entry.close,
            exit.close,
            entry_spot,
            exit_spot,
            &exit_reason,
        );
        (row.mae, row.mfe) = self.option_mae_mfe(
            &symbol,
            entry_date,
            exit_date,
            expiry,
            strike,
            &option_type,
            &position,
            entry.close,
            entry_spot,
            trading_days,
        );
        scale_excursions(&mut row, leg);
        Ok(row)
    }

    #[allow(clippy::too_many_arguments)]
    fn price_option_reentry(
        &self,
        strategy: &StrategyConfig,
        leg: &LegConfig,
        trade_id: u64,
        leg_index: usize,
        entry_date: NaiveDate,
        scheduled_exit: NaiveDate,
        expiry: NaiveDate,
        trading_days: &[NaiveDate],
    ) -> Result<TradeRow, EngineError> {
        let symbol = leg
            .index
            .as_deref()
            .unwrap_or(&strategy.index)
            .trim()
            .to_ascii_uppercase();
        let option_type = leg
            .option_type
            .as_deref()
            .unwrap_or("CE")
            .to_ascii_uppercase();
        let position = leg.position.as_deref().unwrap_or("").to_ascii_uppercase();
        let entry_spot = self
            .market
            .spot(&symbol, entry_date)
            .map(|value| value.close)
            .ok_or_else(|| EngineError::MissingMarketData(format!("spot {symbol} {entry_date}")))?;
        let interval = leg
            .extra
            .get("strike_interval")
            .and_then(|value| value.as_f64())
            .unwrap_or_else(|| default_strike_interval(&symbol));
        let strike = self.resolve_strike_for_date(
            leg,
            &symbol,
            entry_date,
            expiry,
            entry_spot,
            interval,
            &option_type,
        )?;
        let entry = self
            .option_ohlc_tolerant(&symbol, entry_date, expiry, strike, &option_type)
            .ok_or_else(|| {
                EngineError::MissingMarketData(format!(
                    "re-entry option {symbol} {entry_date} {expiry} {strike} {option_type}"
                ))
            })?;
        let (exit_date, exit, exit_reason) = self.scan_option_exit(
            leg,
            &symbol,
            entry_date,
            scheduled_exit,
            expiry,
            strike,
            &option_type,
            &position,
            entry.close,
            trading_days,
        )?;
        let exit_spot = self
            .market
            .spot(&symbol, exit_date)
            .map(|value| value.close)
            .ok_or_else(|| EngineError::MissingMarketData(format!("spot {symbol} {exit_date}")))?;
        let mut row = build_row(
            strategy,
            leg,
            trade_id,
            leg_index,
            entry_date,
            exit_date,
            expiry,
            strike,
            "OPTIONS",
            &option_type,
            &position,
            entry.close,
            exit.close,
            entry_spot,
            exit_spot,
            &exit_reason,
        );
        (row.mae, row.mfe) = self.option_mae_mfe(
            &symbol,
            entry_date,
            exit_date,
            expiry,
            strike,
            &option_type,
            &position,
            entry.close,
            entry_spot,
            trading_days,
        );
        scale_excursions(&mut row, leg);
        Ok(row)
    }

    #[allow(clippy::too_many_arguments)]
    fn option_mae_mfe(
        &self,
        symbol: &str,
        entry_date: NaiveDate,
        exit_date: NaiveDate,
        expiry: NaiveDate,
        strike: f64,
        option_type: &str,
        position: &str,
        entry_price: f64,
        entry_spot: f64,
        trading_days: &[NaiveDate],
    ) -> (Option<f64>, Option<f64>) {
        if entry_spot <= 0.0 {
            return (None, None);
        }
        let mut maximum = f64::NEG_INFINITY;
        let mut minimum = f64::INFINITY;
        for day in trading_days
            .iter()
            .copied()
            .filter(|day| *day > entry_date && *day <= exit_date)
        {
            let Some(ohlc) = self.option_ohlc_tolerant(symbol, day, expiry, strike, option_type)
            else {
                continue;
            };
            let high = if ohlc.high > 0.0 {
                ohlc.high
            } else {
                ohlc.settled.unwrap_or(ohlc.close)
            };
            let low = if ohlc.low > 0.0 {
                ohlc.low
            } else {
                ohlc.settled.unwrap_or(ohlc.close)
            };
            maximum = maximum.max(high);
            minimum = minimum.min(low);
        }
        if !maximum.is_finite() || !minimum.is_finite() {
            return (None, None);
        }
        let (mae, mfe) = if position.eq_ignore_ascii_case("SELL") {
            (entry_price - maximum, entry_price - minimum)
        } else {
            (minimum - entry_price, maximum - entry_price)
        };
        (
            Some(round4(mae / entry_spot * 100.0)),
            Some(round4(mfe / entry_spot * 100.0)),
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn price_future_leg(
        &self,
        strategy: &StrategyConfig,
        leg: &LegConfig,
        trade_id: u64,
        leg_index: usize,
        entry_date: NaiveDate,
        exit_date: NaiveDate,
        cycle_expiry: NaiveDate,
        entry_spot: f64,
        exit_spot: f64,
    ) -> Result<TradeRow, EngineError> {
        let symbol = leg.index.as_deref().unwrap_or(&strategy.index);
        let contract_expiry = self
            .market
            .futures_expiries(symbol, entry_date, add_months(entry_date, 2))
            .into_iter()
            .filter(|expiry| *expiry >= entry_date)
            .min()
            .unwrap_or(cycle_expiry);
        let entry = self
            .market
            .future_ohlc(symbol, entry_date, contract_expiry)
            .ok_or_else(|| {
                EngineError::MissingMarketData(format!(
                    "future {symbol} {entry_date} {contract_expiry}"
                ))
            })?;
        let pricing_exit_date = cycle_expiry.min(exit_date);
        let exit = if pricing_exit_date <= contract_expiry {
            self.market
                .future_ohlc(symbol, pricing_exit_date, contract_expiry)
                .ok_or_else(|| {
                    EngineError::MissingMarketData(format!(
                        "future {symbol} {pricing_exit_date} {contract_expiry}"
                    ))
                })?
        } else {
            let sell = leg
                .position
                .as_deref()
                .unwrap_or("")
                .eq_ignore_ascii_case("SELL");
            let mut held_expiry = contract_expiry;
            let mut held_entry = entry.close;
            let mut total_pnl = 0.0;
            loop {
                let segment_exit_date = pricing_exit_date.min(held_expiry);
                let segment_exit = self
                    .market
                    .future_ohlc(symbol, segment_exit_date, held_expiry)
                    .ok_or_else(|| {
                        EngineError::MissingMarketData(format!(
                            "future rollover {symbol} {segment_exit_date} {held_expiry}"
                        ))
                    })?;
                total_pnl += if sell {
                    held_entry - segment_exit.close
                } else {
                    segment_exit.close - held_entry
                };
                if pricing_exit_date <= held_expiry {
                    break;
                }
                let next_expiry = self
                    .market
                    .futures_expiries(
                        symbol,
                        held_expiry + chrono::Duration::days(1),
                        add_months(held_expiry, 2),
                    )
                    .into_iter()
                    .min()
                    .ok_or_else(|| {
                        EngineError::MissingMarketData(format!(
                            "next future contract after {held_expiry}"
                        ))
                    })?;
                held_entry = self
                    .market
                    .future_ohlc(symbol, held_expiry, next_expiry)
                    .ok_or_else(|| {
                        EngineError::MissingMarketData(format!(
                            "future roll entry {symbol} {held_expiry} {next_expiry}"
                        ))
                    })?
                    .close;
                held_expiry = next_expiry;
            }
            Ohlc {
                close: if sell {
                    entry.close - total_pnl
                } else {
                    entry.close + total_pnl
                },
                ..Default::default()
            }
        };
        let mut row = build_row(
            strategy,
            leg,
            trade_id,
            leg_index,
            entry_date,
            exit_date,
            contract_expiry,
            0.0,
            "FUTURES",
            "FUT",
            leg.position.as_deref().unwrap_or(""),
            entry.close,
            exit.close,
            entry_spot,
            exit_spot,
            "EXPIRY",
        );
        (row.mae, row.mfe) = self.future_mae_mfe(
            symbol,
            entry_date,
            exit_date,
            contract_expiry,
            leg.position.as_deref().unwrap_or(""),
            entry.close,
            entry_spot,
        );
        scale_excursions(&mut row, leg);
        Ok(row)
    }

    #[allow(clippy::too_many_arguments)]
    fn future_mae_mfe(
        &self,
        symbol: &str,
        entry_date: NaiveDate,
        exit_date: NaiveDate,
        expiry: NaiveDate,
        position: &str,
        entry_price: f64,
        entry_spot: f64,
    ) -> (Option<f64>, Option<f64>) {
        if entry_spot <= 0.0 {
            return (None, None);
        }
        let mut maximum = f64::NEG_INFINITY;
        let mut minimum = f64::INFINITY;
        for day in self
            .market
            .trading_days(symbol, entry_date, exit_date)
            .into_iter()
            .filter(|day| *day > entry_date && *day <= exit_date)
        {
            let Some(ohlc) = self.market.future_ohlc(symbol, day, expiry) else {
                continue;
            };
            maximum = maximum.max(if ohlc.high > 0.0 {
                ohlc.high
            } else {
                ohlc.settled.unwrap_or(ohlc.close)
            });
            minimum = minimum.min(if ohlc.low > 0.0 {
                ohlc.low
            } else {
                ohlc.settled.unwrap_or(ohlc.close)
            });
        }
        if !maximum.is_finite() || !minimum.is_finite() {
            return (None, None);
        }
        let (mae, mfe) = if position.eq_ignore_ascii_case("SELL") {
            (entry_price - maximum, entry_price - minimum)
        } else {
            (minimum - entry_price, maximum - entry_price)
        };
        (
            Some(round4(mae / entry_spot * 100.0)),
            Some(round4(mfe / entry_spot * 100.0)),
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn scan_option_exit(
        &self,
        leg: &LegConfig,
        symbol: &str,
        entry_date: NaiveDate,
        scheduled_exit: NaiveDate,
        expiry: NaiveDate,
        strike: f64,
        option_type: &str,
        position: &str,
        entry_price: f64,
        trading_days: &[NaiveDate],
    ) -> Result<(NaiveDate, Ohlc, String), EngineError> {
        let stop = risk_threshold(leg.stop_loss.as_ref());
        let target = risk_threshold(leg.target_profit.as_ref());
        let trail = leg.trail_sl.as_ref().and_then(|rule| {
            let mode = normalized_mode(rule.mode.as_deref());
            let trigger = rule.trigger?;
            let movement = rule.move_value?;
            let x = if mode == "pct" {
                entry_price.abs() * trigger / 100.0
            } else {
                trigger
            };
            let y = if mode == "pct" {
                entry_price.abs() * movement / 100.0
            } else {
                movement
            };
            (x > 0.0 && y > 0.0).then_some((x, y))
        });
        let initial_sl_points = stop
            .as_ref()
            .map(|(value, mode)| {
                if mode == "pct" {
                    entry_price.abs() * value.abs() / 100.0
                } else {
                    value.abs()
                }
            })
            .or_else(|| trail.map(|(trigger, _)| trigger));
        let mut trail_best = entry_price;
        let mut trail_stop = initial_sl_points.map(|points| {
            if position.eq_ignore_ascii_case("SELL") {
                entry_price + points
            } else {
                entry_price - points
            }
        });
        let mut trail_triggers = 0i64;
        for day in trading_days
            .iter()
            .copied()
            .filter(|day| *day > entry_date && *day <= scheduled_exit)
        {
            let Some(ohlc) = self.option_ohlc_tolerant(symbol, day, expiry, strike, option_type)
            else {
                continue;
            };
            let pnl_pct = directional_move(entry_price, ohlc.close, position, "pct");
            let pnl_points = directional_move(entry_price, ohlc.close, position, "points");
            // Live semantics use a configured plain SL as Trail-SL's initial
            // stop. Without one, X itself is the initial stop distance.
            if trail.is_none() {
                if let Some((value, mode)) = stop.as_ref() {
                    let pnl = if mode == "pct" { pnl_pct } else { pnl_points };
                    if pnl <= -*value {
                        return Ok((day, ohlc, "SL".into()));
                    }
                }
            }
            if let Some((value, mode)) = target.as_ref() {
                let pnl = if mode == "pct" { pnl_pct } else { pnl_points };
                if pnl >= *value {
                    return Ok((day, ohlc, "TARGET".into()));
                }
            }
            if let Some(rule) = leg.sl_with_buffer.as_ref() {
                if rule.enabled != Some(false) {
                    if let Some(value) = rule.value.filter(|value| *value > 0.0) {
                        let mode = normalized_mode(rule.mode.as_deref());
                        let buffer_pct = rule
                            .extra
                            .get("buffer_pct")
                            .and_then(|value| value.as_f64())
                            .unwrap_or(0.0)
                            .max(0.0);
                        let sell = position.eq_ignore_ascii_case("SELL");
                        let stop_price = if mode == "pct" {
                            entry_price
                                * if sell {
                                    1.0 + value.abs() / 100.0
                                } else {
                                    1.0 - value.abs() / 100.0
                                }
                        } else if sell {
                            entry_price + value.abs()
                        } else {
                            entry_price - value.abs()
                        };
                        let hit =
                            (sell && ohlc.high >= stop_price) || (!sell && ohlc.low <= stop_price);
                        if hit {
                            let gap = (sell && ohlc.open > stop_price)
                                || (!sell && ohlc.open < stop_price);
                            let fill = if gap && sell {
                                (ohlc.open * (1.0 + buffer_pct / 100.0)).min(ohlc.high)
                            } else if gap {
                                (ohlc.open * (1.0 - buffer_pct / 100.0)).max(ohlc.low)
                            } else {
                                stop_price
                            };
                            let mut filled = ohlc;
                            filled.close = round2(fill);
                            return Ok((
                                day,
                                filled,
                                if gap {
                                    "STOP_LOSS_BUFFER_GAP".into()
                                } else {
                                    "STOP_LOSS_BUFFER".into()
                                },
                            ));
                        }
                    }
                }
            }
            if let (Some((trigger_points, move_points)), Some(current_stop)) =
                (trail, trail_stop.as_mut())
            {
                let sell = position.eq_ignore_ascii_case("SELL");
                if (sell && ohlc.close < trail_best) || (!sell && ohlc.close > trail_best) {
                    trail_best = ohlc.close;
                }
                let favorable = if sell {
                    entry_price - trail_best
                } else {
                    trail_best - entry_price
                };
                let new_triggers = (favorable / trigger_points).floor() as i64;
                if new_triggers > trail_triggers {
                    let delta = new_triggers - trail_triggers;
                    trail_triggers = new_triggers;
                    if sell {
                        *current_stop -= delta as f64 * move_points;
                    } else {
                        *current_stop += delta as f64 * move_points;
                    }
                }
                if (sell && ohlc.close >= *current_stop) || (!sell && ohlc.close <= *current_stop) {
                    return Ok((day, ohlc, "TRAIL_SL".into()));
                }
            }
        }
        let exit = self
            .option_ohlc_tolerant(symbol, scheduled_exit, expiry, strike, option_type)
            .ok_or_else(|| {
                EngineError::MissingMarketData(format!(
                    "option exit {symbol} {scheduled_exit} {expiry} {strike} {option_type}"
                ))
            })?;
        Ok((scheduled_exit, exit, "EXPIRY".into()))
    }

    fn option_ohlc_tolerant(
        &self,
        symbol: &str,
        date: NaiveDate,
        expiry: NaiveDate,
        strike: f64,
        option_type: &str,
    ) -> Option<Ohlc> {
        [-1i64, 0, 1].into_iter().find_map(|offset| {
            let candidate = expiry.checked_add_signed(chrono::Duration::days(offset))?;
            self.market.option_ohlc(&OptionKey {
                symbol: symbol.into(),
                date,
                expiry: candidate,
                strike_minor: (strike * 100.0).round() as i64,
                option_type: option_type.into(),
            })
        })
    }

    #[allow(clippy::too_many_arguments)]
    fn revalidate_carried_strike(
        &self,
        strategy: &StrategyConfig,
        leg: &LegConfig,
        symbol: &str,
        entry_date: NaiveDate,
        expiry: NaiveDate,
        carried: f64,
    ) -> Result<f64, EngineError> {
        let option_type = leg.option_type.as_deref().unwrap_or("CE");
        if self
            .option_ohlc_tolerant(symbol, entry_date, expiry, carried, option_type)
            .is_some()
        {
            return Ok(carried);
        }
        let interval = leg
            .extra
            .get("strike_interval")
            .and_then(|value| value.as_f64())
            .unwrap_or_else(|| default_strike_interval(symbol));
        let maximum_shifts = leg
            .extra
            .get("strike_shift_max")
            .or_else(|| strategy.extra.get("strike_shift_max"))
            .and_then(|value| value.as_u64())
            .unwrap_or(10) as f64;
        let maximum_distance = interval * maximum_shifts;
        self.market
            .option_chain(symbol, entry_date, expiry, option_type)
            .into_iter()
            .filter(|(strike, _)| (*strike - carried).abs() <= maximum_distance)
            .min_by(|(left, _), (right, _)| {
                (left - carried)
                    .abs()
                    .total_cmp(&(right - carried).abs())
                    .then(left.total_cmp(right))
            })
            .map(|(strike, _)| strike)
            .ok_or_else(|| {
                EngineError::MissingMarketData(format!(
                    "carried strike unavailable within {maximum_shifts:.0} shifts: {symbol} {entry_date} {expiry} {carried} {option_type}"
                ))
            })
    }

    #[allow(clippy::too_many_arguments)]
    fn resolve_strike_for_date(
        &self,
        leg: &LegConfig,
        symbol: &str,
        entry_date: NaiveDate,
        expiry: NaiveDate,
        entry_spot: f64,
        interval: f64,
        option_type: &str,
    ) -> Result<f64, EngineError> {
        let mode = leg.strike_selection.kind.trim().to_ascii_lowercase();
        if mode.is_empty()
            || mode == "strike_type"
            || mode == "atm"
            || mode.starts_with("itm")
            || mode.starts_with("otm")
        {
            return resolve_strike(leg, entry_spot, interval, option_type);
        }
        let atm = (entry_spot / interval).round() * interval;
        let is_call = option_type.eq_ignore_ascii_case("CE");
        let chain = || {
            self.market
                .option_chain(symbol, entry_date, expiry, option_type)
                .into_iter()
                .filter(|(_, ohlc)| ohlc.close > 0.0)
                .map(|(strike, ohlc)| (strike, ohlc.close))
                .collect::<Vec<_>>()
        };
        let selected = match mode.as_str() {
            "synthetic_future" => Some(atm),
            "rel_leg" => {
                let reference = leg
                    .strike_selection
                    .extra
                    .get("ref_leg")
                    .and_then(|value| value.as_u64())
                    .or_else(|| leg.strike_selection.extra.get("ref_leg").and_then(|value| value.as_f64()).map(|value| value as u64))
                    .unwrap_or(0) as usize;
                let offset = leg
                    .strike_selection
                    .extra
                    .get("offset")
                    .and_then(|value| value.as_f64())
                    .unwrap_or(0.0);
                let parent = leg
                    .extra
                    .get("_resolved_strikes")
                    .and_then(|value| value.as_array())
                    .and_then(|values| values.get(reference.saturating_sub(1)))
                    .and_then(|value| value.as_f64());
                parent.map(|value| {
                    let shift = offset * interval;
                    if is_call { value + shift } else { value - shift }
                })
            }
            "rel_leg_premium" => {
                let reference = leg
                    .strike_selection
                    .extra
                    .get("ref_leg")
                    .and_then(|value| value.as_u64())
                    .unwrap_or(0) as usize;
                let target = leg
                    .extra
                    .get("_resolved_premiums")
                    .and_then(|value| value.as_array())
                    .and_then(|values| values.get(reference.saturating_sub(1)))
                    .and_then(|value| value.as_f64());
                target.and_then(|target| {
                    let candidates = chain();
                    pick_by_premium(&candidates, target, atm, is_call).map(|(strike, _)| strike)
                })
            }
            "pct_of_atm" => {
                let value = leg.strike_selection.value.unwrap_or(0.0);
                let direction = leg
                    .strike_selection
                    .extra
                    .get("direction")
                    .and_then(|value| value.as_str())
                    .unwrap_or("")
                    .to_ascii_uppercase();
                let raw = if matches!(direction.as_str(), "OTM" | "ITM" | "ATM") {
                    if direction == "ATM" || value == 0.0 {
                        entry_spot
                    } else {
                        let shift = entry_spot * value.abs() / 100.0;
                        let above =
                            (direction == "OTM" && is_call) || (direction == "ITM" && !is_call);
                        if above {
                            entry_spot + shift
                        } else {
                            entry_spot - shift
                        }
                    }
                } else if direction == "-" {
                    entry_spot - entry_spot * value / 100.0
                } else {
                    entry_spot + entry_spot * value / 100.0
                };
                Some((raw / interval).round() * interval)
            }
            "closest_premium" | "premium_gte" | "premium_lte" => {
                let target = leg.strike_selection.premium.unwrap_or(0.0);
                let mut candidates = chain();
                if mode == "premium_gte" {
                    candidates.retain(|(_, premium)| *premium >= target);
                } else if mode == "premium_lte" {
                    candidates.retain(|(_, premium)| *premium <= target);
                }
                pick_by_premium(&candidates, target, atm, is_call).map(|(strike, _)| strike)
            }
            "premium_range" => {
                let lower = leg
                    .strike_selection
                    .extra
                    .get("lower")
                    .and_then(|value| value.as_f64())
                    .unwrap_or(0.0);
                let upper = leg
                    .strike_selection
                    .extra
                    .get("upper")
                    .and_then(|value| value.as_f64())
                    .unwrap_or(0.0);
                let candidates = chain()
                    .into_iter()
                    .filter(|(_, premium)| *premium >= lower && *premium <= upper)
                    .collect::<Vec<_>>();
                pick_by_premium(&candidates, upper, atm, is_call).map(|(strike, _)| strike)
            }
            "time_value" | "time_value_gte" | "time_value_lte" => {
                let target = leg
                    .strike_selection
                    .extra
                    .get("time_value")
                    .and_then(|value| value.as_f64())
                    .or(leg.strike_selection.premium)
                    .unwrap_or(0.0);
                let side = leg
                    .strike_selection
                    .extra
                    .get("moneyness")
                    .and_then(|value| value.as_str())
                    .unwrap_or("ATM")
                    .to_ascii_uppercase();
                let cap = leg
                    .strike_selection
                    .extra
                    .get("tv_range_pct")
                    .and_then(|value| value.as_f64())
                    .unwrap_or(0.0)
                    .abs();
                let percent_units = leg
                    .strike_selection
                    .extra
                    .get("tv_units")
                    .and_then(|value| value.as_str())
                    .is_some_and(|value| matches!(value.to_ascii_lowercase().as_str(), "percent" | "pct" | "%"));
                let mut candidates = chain()
                    .into_iter()
                    .filter(|(strike, _)| {
                        interval <= 0.0 || (strike / interval - (strike / interval).round()).abs() < 1e-9
                    })
                    .filter_map(|(strike, premium)| {
                        let intrinsic = if is_call {
                            (entry_spot - strike).max(0.0)
                        } else {
                            (strike - entry_spot).max(0.0)
                        };
                        if (side == "OTM" && intrinsic > 0.0)
                            || (side == "ITM" && intrinsic <= 0.0)
                            || (cap > 0.0
                                && (strike / entry_spot - 1.0).abs() * 100.0 > cap + 1e-9)
                        {
                            return None;
                        }
                        let mut time_value = premium - intrinsic;
                        if percent_units && entry_spot > 0.0 {
                            time_value = time_value / entry_spot * 100.0;
                        }
                        Some((strike, time_value))
                    })
                    .collect::<Vec<_>>();
                candidates.sort_by(|left, right| {
                    (left.0 - atm)
                        .abs()
                        .total_cmp(&(right.0 - atm).abs())
                        .then_with(|| if is_call { right.0.total_cmp(&left.0) } else { left.0.total_cmp(&right.0) })
                });
                let qualifying = candidates
                    .iter()
                    .copied()
                    .filter(|(_, value)| {
                        mode == "time_value"
                            || (mode == "time_value_gte" && *value >= target)
                            || (mode == "time_value_lte" && *value <= target)
                    })
                    .collect::<Vec<_>>();
                let pool = if qualifying.is_empty() { &candidates } else { &qualifying };
                if mode == "time_value" || qualifying.is_empty() {
                    pool.iter()
                        .copied()
                        .min_by(|left, right| {
                            (left.1 - target)
                                .abs()
                                .total_cmp(&(right.1 - target).abs())
                                .then_with(|| ((left.1 < 0.0) as u8).cmp(&((right.1 < 0.0) as u8)))
                                .then_with(|| (left.0 - atm).abs().total_cmp(&(right.0 - atm).abs()))
                        })
                        .map(|(strike, _)| strike)
                } else {
                    // GTE/LTE take the first qualifying strike reached while
                    // walking outward from ATM; the sorted order above is the walk.
                    pool.first().map(|(strike, _)| *strike)
                }
            }
            "delta" => {
                let target = leg.strike_selection.delta.unwrap_or(0.30).abs().clamp(0.01, 0.99);
                let years = ((expiry - entry_date).num_days().max(1) as f64) / 365.0;
                let sigma = 0.20f64;
                let mut candidates = chain();
                candidates.retain(|(strike, _)| {
                    interval <= 0.0 || (strike / interval - (strike / interval).round()).abs() < 1e-9
                });
                candidates
                    .into_iter()
                    .min_by(|(left, _), (right, _)| {
                        let left_delta = option_delta(entry_spot, *left, years, sigma, is_call);
                        let right_delta = option_delta(entry_spot, *right, years, sigma, is_call);
                        (left_delta.abs() - target)
                            .abs()
                            .total_cmp(&((right_delta.abs() - target).abs()))
                            .then_with(|| (left - atm).abs().total_cmp(&(right - atm).abs()))
                    })
                    .map(|(strike, _)| strike)
            }
            "straddle_width" | "atm_straddle_prem_pct" => {
                let ce = self
                    .option_ohlc_tolerant(symbol, entry_date, expiry, atm, "CE")
                    .map(|value| value.close);
                let pe = self
                    .option_ohlc_tolerant(symbol, entry_date, expiry, atm, "PE")
                    .map(|value| value.close);
                let (Some(ce), Some(pe)) = (ce, pe) else {
                    return Err(EngineError::MissingMarketData(format!(
                        "ATM straddle {symbol} {entry_date} {expiry}"
                    )));
                };
                if mode == "straddle_width" {
                    let multiplier = leg
                        .strike_selection
                        .extra
                        .get("straddle_multiplier")
                        .and_then(|value| value.as_f64())
                        .unwrap_or(0.5);
                    let direction = leg
                        .strike_selection
                        .extra
                        .get("straddle_direction")
                        .and_then(|value| value.as_str())
                        .unwrap_or("+");
                    let shift = multiplier * (ce + pe);
                    let raw = if direction.trim() == "-" {
                        atm - shift
                    } else {
                        atm + shift
                    };
                    Some((raw / interval).round() * interval)
                } else {
                    let target = leg.strike_selection.value.unwrap_or(0.0) / 100.0 * (ce + pe);
                    pick_by_premium(&chain(), target, atm, is_call).map(|(strike, _)| strike)
                }
            }
            _ => None,
        };
        selected.ok_or_else(|| {
            EngineError::MissingMarketData(format!(
                "no strike for {mode} {symbol} {entry_date} {expiry} {option_type}"
            ))
        })
    }
}

impl<M: MarketData + 'static> StrategyEngine for NativeEngine<M> {
    fn validate(&self, strategy: &StrategyConfig) -> Result<(), EngineError> {
        strategy
            .validate()
            .map_err(|error| EngineError::InvalidStrategy(error.to_string()))?;
        for (index, leg) in strategy.legs.iter().enumerate() {
            let kind = leg.strike_selection.kind.to_ascii_lowercase();
            if !matches!(
                kind.as_str(),
                "" | "strike_type"
                    | "atm"
                    | "itm"
                    | "otm"
                    | "pct_of_atm"
                    | "closest_premium"
                    | "premium_gte"
                    | "premium_lte"
                    | "premium_range"
                    | "time_value"
                    | "time_value_gte"
                    | "time_value_lte"
                    | "delta"
                    | "rel_leg"
                    | "rel_leg_premium"
                    | "synthetic_future"
                    | "straddle_width"
                    | "atm_straddle_prem_pct"
            ) && !kind.starts_with("otm")
                && !kind.starts_with("itm")
            {
                return Err(EngineError::FeatureNotPorted(format!(
                    "legs[{index}].strike_selection.type={kind}"
                )));
            }
            for config in [leg.reentry_on_sl.as_ref(), leg.reentry_on_target.as_ref()]
                .into_iter()
                .flatten()
            {
                let mode = config
                    .mode
                    .as_deref()
                    .unwrap_or("RE_ASAP")
                    .to_ascii_uppercase();
                if !matches!(
                    mode.as_str(),
                    "RE_ASAP" | "RE_ASAP_REV" | "RE_MOMENTUM" | "RE_MOMENTUM_REV" | "LAZY_LEG"
                ) {
                    return Err(EngineError::FeatureNotPorted(format!(
                        "legs[{index}] re-entry mode {mode}"
                    )));
                }
                if mode == "LAZY_LEG" && config.lazy_leg_config.is_none() {
                    return Err(EngineError::InvalidStrategy(format!(
                        "legs[{index}] LAZY_LEG requires lazyLegConfig"
                    )));
                }
            }
        }
        Ok(())
    }

    fn run(
        &self,
        strategy: &StrategyConfig,
        _combo: &ComboOverride,
    ) -> Result<EngineResult, EngineError> {
        self.validate(strategy)?;
        let from = parse_strategy_date(strategy.from_date.as_deref(), "from_date")?;
        let to = parse_strategy_date(strategy.to_date.as_deref(), "to_date")?;
        let mut trades = if strategy.multi_index_mode {
            self.run_multi_index(strategy, from, to)?
        } else {
            self.run_options(strategy, from, to)?
        };
        self.apply_midcap_overlay(strategy, &mut trades)?;
        let summary = summarize_trade_groups(&trades);
        Ok(EngineResult { trades, summary })
    }
}

fn summarize_trade_groups(rows: &[TradeRow]) -> crate::SummaryMetrics {
    let parent_rows = canonical_parent_rows(rows);
    summarize_parent_rows(&parent_rows)
}

#[allow(clippy::too_many_arguments)]
fn build_row(
    strategy: &StrategyConfig,
    leg: &LegConfig,
    trade_id: u64,
    leg_index: usize,
    entry_date: NaiveDate,
    exit_date: NaiveDate,
    expiry: NaiveDate,
    strike: f64,
    instrument: &str,
    option_type: &str,
    position: &str,
    raw_entry: f64,
    raw_exit: f64,
    entry_spot: f64,
    exit_spot: f64,
    exit_reason: &str,
) -> TradeRow {
    let slippage = leg.slippage_pct.unwrap_or_else(|| {
        strategy
            .extra
            .get("slippage_pct")
            .and_then(|value| value.as_f64())
            .unwrap_or(0.0)
    });
    let sell = position.eq_ignore_ascii_case("SELL");
    let suppress_entry = leg
        .extra
        .get("_suppress_entry_slippage")
        .and_then(|value| value.as_bool())
        .unwrap_or(false);
    let suppress_exit = leg
        .extra
        .get("_suppress_exit_slippage")
        .and_then(|value| value.as_bool())
        .unwrap_or(false);
    let entry_factor = if suppress_entry {
        1.0
    } else if sell {
        1.0 - slippage / 100.0
    } else {
        1.0 + slippage / 100.0
    };
    let exit_factor = if suppress_exit {
        1.0
    } else if sell {
        1.0 + slippage / 100.0
    } else {
        1.0 - slippage / 100.0
    };
    let entry_price = round2((raw_entry * entry_factor).max(0.0));
    let exit_price = round2((raw_exit * exit_factor).max(0.0));
    let points_pnl = if sell {
        entry_price - exit_price
    } else {
        exit_price - entry_price
    };
    let net_pnl = round4(points_pnl * leg_multiplier(leg));
    TradeRow {
        trade_id,
        leg_id: leg_index as u32 + 1,
        leg_label: None,
        entry_date: entry_date.to_string(),
        exit_date: exit_date.to_string(),
        expiry: expiry.to_string(),
        strike,
        instrument: instrument.into(),
        option_type: option_type.into(),
        position: position.to_ascii_uppercase(),
        entry_price,
        exit_price,
        entry_spot,
        exit_spot,
        net_pnl,
        leg_pnl: net_pnl,
        exit_reason: exit_reason.into(),
        mae: None,
        mfe: None,
        annotations: Default::default(),
    }
}

fn leg_multiplier(leg: &LegConfig) -> f64 {
    leg.quantity.or(leg.lots).unwrap_or(1.0).max(0.0)
}

fn scale_excursions(row: &mut TradeRow, leg: &LegConfig) {
    let multiplier = leg_multiplier(leg);
    row.mae = row.mae.map(|value| round4(value * multiplier));
    row.mfe = row.mfe.map(|value| round4(value * multiplier));
}

fn resolve_strike(
    leg: &LegConfig,
    spot: f64,
    interval: f64,
    option_type: &str,
) -> Result<f64, EngineError> {
    if interval <= 0.0 {
        return Err(EngineError::InvalidStrategy(
            "strike_interval must be positive".into(),
        ));
    }
    let atm = (spot / interval).round() * interval;
    let mut strike_type = leg
        .strike_selection
        .strike_type
        .as_deref()
        .unwrap_or_else(|| {
            let kind = leg.strike_selection.kind.as_str();
            if kind.is_empty() || kind.eq_ignore_ascii_case("strike_type") {
                "ATM"
            } else {
                kind
            }
        })
        .trim()
        .to_ascii_uppercase();
    if matches!(strike_type.as_str(), "ITM" | "OTM") {
        let steps = leg.strike_selection.value.unwrap_or(0.0).round().max(0.0) as i64;
        strike_type.push_str(&steps.to_string());
    }
    if strike_type == "ATM" {
        return Ok(atm);
    }
    let (direction, suffix) = if let Some(value) = strike_type.strip_prefix("OTM") {
        (1.0, value)
    } else if let Some(value) = strike_type.strip_prefix("ITM") {
        (-1.0, value)
    } else {
        return Err(EngineError::FeatureNotPorted(format!(
            "strike_type={strike_type}"
        )));
    };
    let steps: f64 = suffix
        .parse()
        .map_err(|_| EngineError::InvalidStrategy(format!("invalid strike_type={strike_type}")))?;
    let call_sign = if option_type == "CE" { 1.0 } else { -1.0 };
    Ok(atm + interval * steps * direction * call_sign)
}

fn pick_by_premium(
    candidates: &[(f64, f64)],
    target: f64,
    atm: f64,
    is_call: bool,
) -> Option<(f64, f64)> {
    candidates.iter().copied().min_by(|a, b| {
        (a.1 - target)
            .abs()
            .partial_cmp(&(b.1 - target).abs())
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                (a.0 - atm)
                    .abs()
                    .partial_cmp(&(b.0 - atm).abs())
                    .unwrap_or(std::cmp::Ordering::Equal)
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

fn risk_threshold(rule: Option<&algotest_domain::RiskRule>) -> Option<(f64, String)> {
    let rule = rule?;
    if rule.enabled == Some(false) {
        return None;
    }
    let value = rule.value?;
    (value > 0.0).then(|| (value, normalized_mode(rule.mode.as_deref())))
}

fn normalized_mode(mode: Option<&str>) -> String {
    match mode
        .unwrap_or("PERCENT")
        .trim()
        .to_ascii_uppercase()
        .as_str()
    {
        "PERCENT" | "PCT" | "%" | "PERCENTAGE" => "pct".into(),
        _ => "points".into(),
    }
}

fn overall_mode(value: Option<&serde_json::Value>) -> &'static str {
    let normalized = value
        .and_then(|value| value.as_str())
        .unwrap_or("PERCENT")
        .trim()
        .to_ascii_uppercase()
        .replace([' ', '-'], "_");
    match normalized.as_str() {
        "PERCENT" | "PCT" | "%" | "TOTAL_PREMIUM_PERCENT" | "TOTAL_PREMIUM_PCT" => {
            "total_premium_pct"
        }
        "POINTS" | "PTS" | "POINT" | "PT" => "points",
        "UNDERLYING_POINTS" | "UNDERLYING_PTS" | "INDEX_POINTS" | "SPOT_POINTS" => "underlying_pts",
        "UNDERLYING_PCT" | "UNDERLYING_PERCENT" | "INDEX_PCT" | "SPOT_PCT" => "underlying_pct",
        _ => "fixed",
    }
}

fn directional_move(entry: f64, current: f64, position: &str, mode: &str) -> f64 {
    let raw = if mode == "pct" {
        if entry == 0.0 {
            0.0
        } else {
            (current - entry) / entry * 100.0
        }
    } else {
        current - entry
    };
    if position.eq_ignore_ascii_case("SELL") {
        -raw
    } else {
        raw
    }
}

fn parse_strategy_date(value: Option<&str>, name: &str) -> Result<NaiveDate, EngineError> {
    value
        .and_then(|value| NaiveDate::parse_from_str(&value[..value.len().min(10)], "%Y-%m-%d").ok())
        .ok_or_else(|| EngineError::InvalidStrategy(format!("{name} must be YYYY-MM-DD")))
}

fn parse_row_date(value: &str) -> Result<NaiveDate, EngineError> {
    NaiveDate::parse_from_str(value, "%Y-%m-%d")
        .map_err(|error| EngineError::Calculation(error.to_string()))
}

fn default_strike_interval(symbol: &str) -> f64 {
    match symbol {
        "BANKNIFTY" | "SENSEX" => 100.0,
        "MIDCPNIFTY" => 25.0,
        _ => 50.0,
    }
}

fn option_delta(spot: f64, strike: f64, years: f64, sigma: f64, call: bool) -> f64 {
    if spot <= 0.0 || strike <= 0.0 || years <= 0.0 || sigma <= 0.0 {
        return if call { 0.5 } else { -0.5 };
    }
    let d1 = ((spot / strike).ln() + 0.5 * sigma * sigma * years) / (sigma * years.sqrt());
    let probability = normal_cdf(d1);
    if call { probability } else { probability - 1.0 }
}

fn normal_cdf(value: f64) -> f64 {
    // Abramowitz-Stegun 7.1.26, max error about 7.5e-8; adequate for EOD
    // strike selection where the final choice is a listed strike grid.
    let sign = if value < 0.0 { -1.0 } else { 1.0 };
    let x = value.abs() / 2.0_f64.sqrt();
    let t = 1.0 / (1.0 + 0.3275911 * x);
    let polynomial = (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
        - 0.284496736)
        * t
        + 0.254829592)
        * t;
    let erf = sign * (1.0 - polynomial * (-x * x).exp());
    0.5 * (1.0 + erf)
}

fn strategy_has_yearly(strategy: &StrategyConfig) -> bool {
    strategy
        .expiry_type
        .as_deref()
        .is_some_and(|value| value.to_ascii_uppercase().contains("YEAR"))
        || strategy.legs.iter().any(|leg| {
            leg.expiry
                .as_deref()
                .is_some_and(|value| value.to_ascii_uppercase().contains("YEAR"))
        })
}

fn leg_active_for_window(leg: &LegConfig, entry: NaiveDate, exit: NaiveDate) -> bool {
    leg.filter_segments.is_empty()
        || leg
            .filter_segments
            .iter()
            .any(|segment| segment.start <= entry && exit <= segment.end)
}

fn snapped_leg_filter_boundaries(
    legs: &[LegConfig],
    trading_days: &[NaiveDate],
    entry: NaiveDate,
    exit: NaiveDate,
) -> Vec<NaiveDate> {
    let mut boundaries = BTreeSet::new();
    for segment in legs.iter().flat_map(|leg| &leg.filter_segments) {
        if let Some(start) = trading_days.iter().copied().find(|day| *day >= segment.start) {
            if entry < start && start < exit {
                boundaries.insert(start);
            }
        }
        if let Some(end) = trading_days
            .iter()
            .copied()
            .filter(|day| *day <= segment.end)
            .max()
        {
            if entry < end && end < exit {
                boundaries.insert(end);
            }
        }
    }
    boundaries.into_iter().collect()
}

fn round2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn round4(value: f64) -> f64 {
    (value * 10_000.0).round() / 10_000.0
}

fn add_months(date: NaiveDate, count: u32) -> NaiveDate {
    let month0 = date.month0() + count;
    let year = date.year() + (month0 / 12) as i32;
    let month = month0 % 12 + 1;
    NaiveDate::from_ymd_opt(year, month, 1).unwrap_or(date)
}

fn subtract_months_clamped(date: NaiveDate, count: u32) -> Option<NaiveDate> {
    let total = i64::from(date.year()) * 12 + i64::from(date.month0()) - i64::from(count);
    let year = i32::try_from(total.div_euclid(12)).ok()?;
    let month = u32::try_from(total.rem_euclid(12)).ok()? + 1;
    let next_month = if month == 12 {
        NaiveDate::from_ymd_opt(year.checked_add(1)?, 1, 1)?
    } else {
        NaiveDate::from_ymd_opt(year, month + 1, 1)?
    };
    let last_day = next_month.pred_opt()?.day();
    NaiveDate::from_ymd_opt(year, month, date.day().min(last_day))
}

fn yearly_contract_override(
    leg: &LegConfig,
    contract: NaiveDate,
) -> Option<(Option<f64>, Option<f64>)> {
    let rows = leg.extra.get("yearly_contract_schedule")?.as_array()?;
    let year = contract.year();
    rows.iter().find_map(|row| {
        let object = row.as_object()?;
        let matches = object.get("contract").is_some_and(|value| {
            value.as_i64() == Some(i64::from(year))
                || value.as_str().is_some_and(|text| {
                    text.trim().parse::<i32>() == Ok(year)
                        || text.trim().starts_with(&year.to_string())
                })
        });
        matches.then(|| {
            (
                object.get("strike_gap").and_then(|value| value.as_f64()),
                object.get("spot_adj_pct").and_then(|value| value.as_f64()),
            )
        })
    })
}

fn json_number(value: f64) -> serde_json::Value {
    serde_json::Number::from_f64(value)
        .map(serde_json::Value::Number)
        .unwrap_or(serde_json::Value::Null)
}

fn display_optional_number(value: Option<f64>) -> String {
    value.map_or_else(|| "default".into(), |number| {
        if number.fract() == 0.0 {
            format!("{number:.0}")
        } else {
            number.to_string()
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use algotest_domain::{DateSegment, SpotAdjustment, StrikeSelection};

    struct SpotRiskMarket;

    impl MarketData for SpotRiskMarket {
        fn option_ohlc(&self, _key: &OptionKey) -> Option<Ohlc> {
            Some(Ohlc {
                close: 12.0,
                ..Default::default()
            })
        }
        fn spot(&self, _symbol: &str, date: NaiveDate) -> Option<Ohlc> {
            Some(Ohlc {
                close: if date.day() == 1 { 100.0 } else { 111.0 },
                ..Default::default()
            })
        }
        fn future_ohlc(&self, _: &str, _: NaiveDate, _: NaiveDate) -> Option<Ohlc> {
            None
        }
        fn trading_days(&self, _: &str, _: NaiveDate, _: NaiveDate) -> Vec<NaiveDate> {
            vec![]
        }
        fn expiries(&self, _: &str, _: NaiveDate, _: NaiveDate) -> Vec<NaiveDate> {
            vec![]
        }
    }

    struct OverlayMarket;

    impl MarketData for OverlayMarket {
        fn option_ohlc(&self, _: &OptionKey) -> Option<Ohlc> { None }
        fn spot(&self, symbol: &str, date: NaiveDate) -> Option<Ohlc> {
            if symbol != "NIFTYMIDCAP100" { return None; }
            let close = match date.to_string().as_str() {
                "2019-02-28" => 16_721.10,
                "2019-03-05" => 16_850.00,
                "2019-03-15" => 17_400.00,
                "2019-03-28" => 18_083.45,
                _ => return None,
            };
            Some(Ohlc { open: close, high: close, low: close, close, settled: None })
        }
        fn future_ohlc(&self, _: &str, _: NaiveDate, _: NaiveDate) -> Option<Ohlc> { None }
        fn trading_days(&self, _: &str, from: NaiveDate, to: NaiveDate) -> Vec<NaiveDate> {
            ["2019-02-28", "2019-03-05", "2019-03-15", "2019-03-28"]
                .into_iter()
                .filter_map(|value| NaiveDate::parse_from_str(value, "%Y-%m-%d").ok())
                .filter(|day| from <= *day && *day <= to)
                .collect()
        }
        fn expiries(&self, _: &str, _: NaiveDate, _: NaiveDate) -> Vec<NaiveDate> { vec![] }
    }

    struct StrikeChainMarket;

    impl MarketData for StrikeChainMarket {
        fn option_ohlc(&self, key: &OptionKey) -> Option<Ohlc> {
            let premium = match key.strike_minor as f64 / 100.0 {
                95.0 => 10.0,
                100.0 => 8.0,
                105.0 => 5.0,
                110.0 => 3.0,
                _ => return None,
            };
            Some(Ohlc { open: premium, high: premium, low: premium, close: premium, settled: None })
        }
        fn option_chain(&self, _: &str, _: NaiveDate, _: NaiveDate, _: &str) -> Vec<(f64, Ohlc)> {
            [95.0, 100.0, 105.0, 110.0]
                .into_iter()
                .map(|strike| {
                    let premium = match strike { 95.0 => 10.0, 100.0 => 8.0, 105.0 => 5.0, _ => 3.0 };
                    (strike, Ohlc { open: premium, high: premium, low: premium, close: premium, settled: None })
                })
                .collect()
        }
        fn spot(&self, _: &str, _: NaiveDate) -> Option<Ohlc> {
            Some(Ohlc { open: 100.0, high: 100.0, low: 100.0, close: 100.0, settled: None })
        }
        fn future_ohlc(&self, _: &str, _: NaiveDate, _: NaiveDate) -> Option<Ohlc> { None }
        fn trading_days(&self, _: &str, from: NaiveDate, to: NaiveDate) -> Vec<NaiveDate> {
            [from, to].into_iter().filter(|day| *day <= to).collect()
        }
        fn expiries(&self, _: &str, _: NaiveDate, _: NaiveDate) -> Vec<NaiveDate> { vec![] }
    }

    #[test]
    fn strike_direction_is_option_aware() {
        let leg = LegConfig {
            strike_selection: StrikeSelection {
                strike_type: Some("OTM2".into()),
                ..Default::default()
            },
            ..Default::default()
        };
        assert_eq!(
            resolve_strike(&leg, 21_517.0, 50.0, "CE").unwrap(),
            21_600.0
        );
        assert_eq!(
            resolve_strike(&leg, 21_517.0, 50.0, "PE").unwrap(),
            21_400.0
        );
    }

    #[test]
    fn leg_pnl_scales_by_lots_or_explicit_quantity_once() {
        let day = NaiveDate::from_ymd_opt(2025, 1, 1).unwrap();
        let expiry = NaiveDate::from_ymd_opt(2025, 1, 30).unwrap();
        let strategy = StrategyConfig::default();
        let with_lots = LegConfig {
            lots: Some(3.0),
            ..Default::default()
        };
        let row = build_row(
            &strategy, &with_lots, 1, 0, day, expiry, expiry, 100.0, "OPTIONS", "CE", "SELL", 10.0,
            8.0, 100.0, 101.0, "EXPIRY",
        );
        assert_eq!(row.leg_pnl, 6.0);
        let with_quantity = LegConfig {
            lots: Some(3.0),
            quantity: Some(5.0),
            ..Default::default()
        };
        let row = build_row(
            &strategy,
            &with_quantity,
            1,
            0,
            day,
            expiry,
            expiry,
            100.0,
            "OPTIONS",
            "CE",
            "SELL",
            10.0,
            8.0,
            100.0,
            101.0,
            "EXPIRY",
        );
        assert_eq!(row.leg_pnl, 10.0);
    }

    #[test]
    fn trade_summary_does_not_depend_on_leg_sequence() {
        let make = |leg_id, entry: &str, own_pnl| TradeRow {
            trade_id: 1,
            leg_id,
            entry_date: entry.into(),
            exit_date: "2025-01-10".into(),
            entry_spot: 20_000.0,
            exit_spot: 20_100.0,
            net_pnl: own_pnl,
            leg_pnl: own_pnl,
            ..Default::default()
        };
        let first = vec![make(1, "2025-01-01", 75.0), make(2, "2025-01-05", -25.0)];
        let second = vec![make(1, "2025-01-05", -25.0), make(2, "2025-01-01", 75.0)];
        assert_eq!(
            summarize_trade_groups(&first),
            summarize_trade_groups(&second)
        );
        assert_eq!(summarize_trade_groups(&first).total_pnl, 50.0);
    }

    #[test]
    fn midcap_hypothetical_overlay_matches_reference_math() {
        let engine = NativeEngine::new(Arc::new(OverlayMarket));
        let mut strategy = StrategyConfig::default();
        strategy.legs.push(LegConfig::default());
        strategy.midcap_legs.push(serde_json::json!({
            "midcap_mode":"hypothetical", "position":"buy",
            "cost_pct_per_month":0.5, "lots":1
        }));
        let mut rows = vec![TradeRow {
            trade_id: 1,
            leg_id: 1,
            entry_date: "2019-02-28".into(),
            exit_date: "2019-03-28".into(),
            leg_pnl: -603.25,
            net_pnl: -603.25,
            exit_reason: "EXPIRY".into(),
            ..Default::default()
        }];
        engine.apply_midcap_overlay(&strategy, &mut rows).unwrap();
        let overlay = rows.iter().find(|row| row.instrument == "MIDCAP100").unwrap();
        assert_eq!(overlay.entry_price, 16_721.10);
        assert_eq!(overlay.exit_price, 18_083.45);
        assert!((overlay.leg_pnl - 1_284.3183).abs() < 0.001);
        assert!((rows[0].net_pnl - 681.0683).abs() < 0.001);
    }

    #[test]
    fn advanced_strike_modes_resolve_against_native_chain() {
        let engine = NativeEngine::new(Arc::new(StrikeChainMarket));
        let entry = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();
        let expiry = NaiveDate::from_ymd_opt(2025, 1, 9).unwrap();
        let resolve = |value| {
            let leg: LegConfig = serde_json::from_value(value).unwrap();
            engine.resolve_strike_for_date(&leg, "NIFTY", entry, expiry, 100.0, 5.0, "CE")
        };
        assert_eq!(resolve(serde_json::json!({"strike_selection":{"type":"time_value","premium":5}})).unwrap(), 105.0);
        assert_eq!(resolve(serde_json::json!({"strike_selection":{"type":"time_value_gte","premium":7}})).unwrap(), 100.0);
        assert_eq!(resolve(serde_json::json!({"strike_selection":{"type":"time_value_lte","premium":4}})).unwrap(), 110.0);
        assert_eq!(resolve(serde_json::json!({"strike_selection":{"type":"delta","delta":0.5}})).unwrap(), 100.0);
        let relative: LegConfig = serde_json::from_value(serde_json::json!({
            "strike_selection":{"type":"rel_leg","ref_leg":1,"offset":2},
            "_resolved_strikes":[100]
        })).unwrap();
        assert_eq!(engine.resolve_strike_for_date(&relative, "NIFTY", entry, expiry, 100.0, 5.0, "CE").unwrap(), 110.0);
        let relative_premium: LegConfig = serde_json::from_value(serde_json::json!({
            "strike_selection":{"type":"rel_leg_premium","ref_leg":1},
            "_resolved_premiums":[5]
        })).unwrap();
        assert_eq!(engine.resolve_strike_for_date(&relative_premium, "NIFTY", entry, expiry, 100.0, 5.0, "CE").unwrap(), 105.0);
    }

    #[test]
    fn overall_underlying_points_uses_first_leg_direction() {
        let engine = NativeEngine::new(Arc::new(SpotRiskMarket));
        let mut strategy = StrategyConfig {
            index: "NIFTY".into(),
            overall_sl_value: Some(10.0),
            legs: vec![LegConfig {
                position: Some("SELL".into()),
                option_type: Some("CE".into()),
                ..Default::default()
            }],
            ..Default::default()
        };
        strategy.extra.insert(
            "overall_sl_type".into(),
            serde_json::json!("underlying_pts"),
        );
        let mut rows = vec![TradeRow {
            trade_id: 1,
            leg_id: 1,
            entry_date: "2025-01-01".into(),
            exit_date: "2025-01-03".into(),
            expiry: "2025-01-30".into(),
            strike: 100.0,
            instrument: "OPTIONS".into(),
            option_type: "CE".into(),
            position: "SELL".into(),
            entry_price: 10.0,
            entry_spot: 100.0,
            ..Default::default()
        }];
        let days = [
            NaiveDate::from_ymd_opt(2025, 1, 1).unwrap(),
            NaiveDate::from_ymd_opt(2025, 1, 2).unwrap(),
            NaiveDate::from_ymd_opt(2025, 1, 3).unwrap(),
        ];
        engine
            .apply_overall_risk(&strategy, "NIFTY", 0, &mut rows, days[0], days[2], &days)
            .unwrap();
        assert_eq!(rows[0].exit_date, "2025-01-02");
        assert_eq!(rows[0].exit_reason, "OVERALL_SL");
    }

    #[test]
    fn per_leg_spot_adjustment_only_restrikes_breaching_leg() {
        let engine = NativeEngine::new(Arc::new(SpotRiskMarket));
        let mut strategy = StrategyConfig {
            index: "NIFTY".into(),
            legs: vec![LegConfig::default(), LegConfig::default()],
            ..Default::default()
        };
        strategy.legs[0].spot_adjustment = Some(SpotAdjustment {
            enabled: true,
            pct: Some(5.0),
            direction: Some("rise".into()),
            units: Some("percent".into()),
            ..Default::default()
        });
        strategy.legs[1].spot_adjustment = Some(SpotAdjustment {
            enabled: true,
            pct: Some(20.0),
            direction: Some("both".into()),
            units: Some("percent".into()),
            ..Default::default()
        });
        let days = [
            NaiveDate::from_ymd_opt(2025, 1, 1).unwrap(),
            NaiveDate::from_ymd_opt(2025, 1, 2).unwrap(),
            NaiveDate::from_ymd_opt(2025, 1, 3).unwrap(),
        ];
        let (exit, _, breached) = engine
            .per_leg_spot_adjusted_exit(
                &strategy,
                "NIFTY",
                days[0],
                days[2],
                100.0,
                111.0,
                &days,
                &[Some(100.0), Some(100.0)],
                &[days[2], days[2]],
            )
            .unwrap();
        assert_eq!(exit, days[1]);
        assert_eq!(breached, vec![true, false]);
    }

    #[test]
    fn per_leg_rollover_uses_union_and_carries_foreign_leg() {
        let d = |day| NaiveDate::from_ymd_opt(2025, 1, day).unwrap();
        let trading_days = (1..=24).filter_map(|day| NaiveDate::from_ymd_opt(2025, 1, day)).collect::<Vec<_>>();
        let rows = build_per_leg_roll_schedule(
            d(1),
            &[
                vec![(d(8), d(8)), (d(15), d(15)), (d(22), d(22))],
                vec![(d(17), d(17)), (d(24), d(24))],
            ],
            &[0, 0],
            &trading_days,
            &[],
            &[BTreeSet::new(), BTreeSet::new()],
        )
        .unwrap();

        let monthly_boundary = rows.iter().find(|row| row.entry == d(17)).unwrap();
        assert_eq!(monthly_boundary.exit, d(22));
        assert!(!monthly_boundary.slots[0].own_boundary);
        assert!(monthly_boundary.slots[1].own_boundary);
        assert_eq!(monthly_boundary.slots[0].contract, d(22));
        assert_eq!(monthly_boundary.slots[1].contract, d(24));
    }

    #[test]
    fn per_leg_rollover_is_independent_of_leg_sequence() {
        let d = |day| NaiveDate::from_ymd_opt(2025, 2, day).unwrap();
        let trading_days = (1..=28).filter_map(|day| NaiveDate::from_ymd_opt(2025, 2, day)).collect::<Vec<_>>();
        let a = vec![(d(7), d(7)), (d(14), d(14)), (d(21), d(21)), (d(28), d(28))];
        let b = vec![(d(20), d(20)), (d(28), d(28))];
        let forward = build_per_leg_roll_schedule(
            d(1),
            &[a.clone(), b.clone()],
            &[1, 2],
            &trading_days,
            &[],
            &[BTreeSet::new(), BTreeSet::new()],
        )
        .unwrap();
        let reverse = build_per_leg_roll_schedule(
            d(1),
            &[b, a],
            &[2, 1],
            &trading_days,
            &[],
            &[BTreeSet::new(), BTreeSet::new()],
        )
        .unwrap();

        assert_eq!(
            forward.iter().map(|row| (row.entry, row.exit)).collect::<Vec<_>>(),
            reverse.iter().map(|row| (row.entry, row.exit)).collect::<Vec<_>>()
        );
    }

    #[test]
    fn per_leg_rollover_inserts_filter_boundary_without_rolling_contracts() {
        let d = |day| NaiveDate::from_ymd_opt(2025, 3, day).unwrap();
        let trading_days = (1..=28)
            .filter_map(|day| NaiveDate::from_ymd_opt(2025, 3, day))
            .collect::<Vec<_>>();
        let rows = build_per_leg_roll_schedule(
            d(1),
            &[
                vec![(d(7), d(7)), (d(14), d(14)), (d(21), d(21))],
                vec![(d(20), d(20)), (d(28), d(28))],
            ],
            &[0, 0],
            &trading_days,
            &[d(10)],
            &[BTreeSet::new(), BTreeSet::new()],
        )
        .unwrap();
        let split = rows.iter().find(|row| row.entry == d(10)).unwrap();
        assert_eq!(split.exit, d(14));
        assert!(split.slots.iter().all(|slot| !slot.own_boundary));
        assert_eq!(split.slots[0].contract, d(14));
        assert_eq!(split.slots[1].contract, d(20));
    }

    #[test]
    fn yearly_contract_is_pinned_while_monthly_refreshes_split_rows() {
        let run_start = NaiveDate::from_ymd_opt(2025, 1, 1).unwrap();
        let contract = NaiveDate::from_ymd_opt(2025, 12, 31).unwrap();
        let exit = NaiveDate::from_ymd_opt(2025, 11, 28).unwrap();
        let refresh = NaiveDate::from_ymd_opt(2025, 2, 28).unwrap();
        let trading_days = (0..365)
            .filter_map(|offset| run_start.checked_add_signed(chrono::Duration::days(offset)))
            .collect::<Vec<_>>();
        let rows = build_per_leg_roll_schedule(
            run_start,
            &[vec![(exit, contract)]],
            &[0],
            &trading_days,
            &[refresh],
            &[BTreeSet::from([refresh])],
        )
        .unwrap();
        let row = rows.iter().find(|row| row.entry == refresh).unwrap();
        assert_eq!(row.slots[0].contract, contract);
        assert!(!row.slots[0].own_boundary);
        assert!(row.slots[0].refresh_boundary);
    }

    #[test]
    fn yearly_month_subtraction_clamps_end_of_month() {
        let date = NaiveDate::from_ymd_opt(2024, 3, 31).unwrap();
        assert_eq!(
            subtract_months_clamped(date, 1),
            NaiveDate::from_ymd_opt(2024, 2, 29)
        );
    }

    #[test]
    fn yearly_contract_schedule_switches_gap_and_adjustment_by_held_contract() {
        let leg: LegConfig = serde_json::from_value(serde_json::json!({
            "yearly_contract_schedule":[
                {"contract":"2022","strike_gap":500,"spot_adj_pct":5},
                {"contract":2023,"strike_gap":1000,"spot_adj_pct":10}
            ]
        }))
        .unwrap();
        assert_eq!(
            yearly_contract_override(
                &leg,
                NaiveDate::from_ymd_opt(2023, 12, 28).unwrap()
            ),
            Some((Some(1000.0), Some(10.0)))
        );
        assert_eq!(
            yearly_contract_override(
                &leg,
                NaiveDate::from_ymd_opt(2024, 12, 26).unwrap()
            ),
            None
        );
    }

    #[test]
    fn individual_filter_presence_is_exactly_subtractive() {
        let leg: LegConfig = serde_json::from_value(serde_json::json!({
            "filter_segments":[{"start":"2025-01-06","end":"2025-01-20"}]
        }))
        .unwrap();
        let d = |day| NaiveDate::from_ymd_opt(2025, 1, day).unwrap();
        assert!(!leg_active_for_window(&leg, d(2), d(6)));
        assert!(leg_active_for_window(&leg, d(6), d(9)));
        assert!(leg_active_for_window(&leg, d(9), d(20)));
        assert!(!leg_active_for_window(&leg, d(20), d(23)));
    }

    #[test]
    fn individual_filter_boundaries_snap_and_only_split_inside_window() {
        let d = |day| NaiveDate::from_ymd_opt(2025, 1, day).unwrap();
        let trading_days = vec![d(2), d(3), d(6), d(7), d(8), d(9), d(10)];
        let leg = LegConfig {
            filter_segments: vec![DateSegment {
                // Saturday/Sunday snap to Monday for the start and Friday for
                // the end. The outer exact endpoints must not create zero rows.
                start: d(4),
                end: d(10),
                extra: std::collections::BTreeMap::new(),
            }],
            ..Default::default()
        };
        assert_eq!(
            snapped_leg_filter_boundaries(&[leg], &trading_days, d(2), d(10)),
            vec![d(6)]
        );
    }

    #[test]
    fn synthetic_filter_split_conserves_carried_leg_pnl_with_slippage() {
        let strategy = StrategyConfig::default();
        let base = LegConfig {
            position: Some("SELL".into()),
            slippage_pct: Some(1.0),
            ..Default::default()
        };
        let d = |day| NaiveDate::from_ymd_opt(2025, 1, day).unwrap();
        let unsplit = build_row(
            &strategy, &base, 1, 0, d(2), d(9), d(30), 100.0, "OPTIONS", "CE", "SELL",
            100.0, 80.0, 20_000.0, 20_100.0, "EXPIRY",
        );
        let mut first_leg = base.clone();
        first_leg
            .extra
            .insert("_suppress_exit_slippage".into(), serde_json::json!(true));
        let first = build_row(
            &strategy, &first_leg, 1, 0, d(2), d(6), d(30), 100.0, "OPTIONS", "CE", "SELL",
            100.0, 90.0, 20_000.0, 20_050.0, "LEG_FILTER_END",
        );
        let mut second_leg = base;
        second_leg
            .extra
            .insert("_suppress_entry_slippage".into(), serde_json::json!(true));
        let second = build_row(
            &strategy, &second_leg, 2, 0, d(6), d(9), d(30), 100.0, "OPTIONS", "CE", "SELL",
            90.0, 80.0, 20_050.0, 20_100.0, "EXPIRY",
        );
        assert_eq!(round4(first.leg_pnl + second.leg_pnl), unsplit.leg_pnl);
        assert_eq!(first.exit_price, second.entry_price);
    }
}
