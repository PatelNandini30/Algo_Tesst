/**
 * buildRulesSheet — produce a leg-wise, comprehensive "Rules" layout for the
 * backtest workbook's first sheet. Pure function of the strategy payload
 * (StrategyBuilder.buildPayload() output) + the applied filter name, so it
 * reflects the exact configuration that was run: strategy-level rules plus one
 * section per leg (options CE/PE, futures, or midcap overlay), each with that
 * leg's own strike/SL/target/slippage.
 *
 * Returns an array of typed rows the backend renderer (_write_rules_sheet)
 * understands — every element is a plain array of strings so it is JSON-safe:
 *   ["title", text]          — top banner
 *   ["section", text]        — section header
 *   ["kv", label, value]     — label/value pair
 *   ["spacer"]               — blank gap
 */

const EXPIRY_LABELS = {
  WEEKLY: 'Weekly',
  MONTHLY: 'Monthly',
  NEXT_WEEKLY: 'Next Weekly',
  NEXT_MONTHLY: 'Next Monthly',
  YEARLY: 'Yearly (December)',
};

const SA_DIR = { rise: 'Rise', fall: 'Fall', both: 'Rise or Fall' };

function _num(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v ?? '');
  return Number.isInteger(n) ? String(n) : String(parseFloat(n.toFixed(4)));
}

function _side(pos) {
  const p = String(pos || '').toLowerCase();
  return (p === 'sell' || p === 'short') ? 'Sell' : 'Buy';
}

// POINTS/PERCENT (any casing / '%'/'PCT') → a short unit suffix.
function _unit(mode) {
  const m = String(mode || '').toUpperCase();
  return (m.includes('PERCENT') || m.includes('PCT') || m === '%') ? '%' : ' pts';
}

function _expiry(v) {
  return EXPIRY_LABELS[String(v || '').toUpperCase()] || v || '—';
}

function _strikeLabel(ss, optType) {
  ss = ss || {};
  const type = String(ss.type || '').toUpperCase();
  if (type === 'PCT_OF_ATM') {
    const dir = ss.direction === '-' ? '-' : '+';
    return `% of ATM: ${dir}${_num(ss.value)}%`;
  }
  if (type === 'ATM_STRADDLE_PREM_PCT') {
    return `ATM Straddle Premium: ${_num(ss.value)}%`;
  }
  if (type === 'STRADDLE_WIDTH') {
    const dir = String(ss.straddle_direction || '+');
    return `Straddle Width: ${dir}${_num(ss.straddle_multiplier ?? 0.5)}x`;
  }
  if (type === 'REL_LEG') {
    return `Relative to Leg ${_num(ss.ref_leg ?? 1)}, offset ${_num(ss.offset ?? 0)} gap(s)`;
  }
  if (type === 'PREMIUM' || type === 'CLOSEST_PREMIUM') {
    if (ss.premium != null && ss.premium !== '') return `Closest Premium: ${_num(ss.premium)}`;
    if (ss.lower != null || ss.upper != null) return `Premium Range: ${_num(ss.lower)}–${_num(ss.upper)}`;
    return 'Premium';
  }
  // Default: fixed strike type (ATM / ITMn / OTMn).
  return String(ss.strike_type || 'ATM');
}

function _legSection(rows, leg, n) {
  const push = (l, v) =>
    rows.push(['kv', l, (v === null || v === undefined || v === '') ? '—' : String(v)]);
  const seg = String(leg.segment || 'OPTIONS').toUpperCase();
  const side = _side(leg.position);

  const head = seg === 'FUTURES'
    ? `FUT ${side}`
    : `${String(leg.option_type || '').toUpperCase()} ${side}`;
  rows.push(['section', `Leg ${n} — ${head} (${seg === 'FUTURES' ? 'Futures' : 'Options'})`]);

  push('Position', side);
  push('Lots', leg.lots ?? 1);
  if (leg.index) push('Index', leg.index);
  push('Expiry', _expiry(leg.expiry));

  if (seg === 'FUTURES') {
    push('Exit Mode', leg.fut_exit_mode);
    if (String(leg.fut_exit_mode || '').toUpperCase().includes('N_DAY') || leg.fut_n_days != null) {
      push('Exit After N Days', leg.fut_n_days);
    }
    push('Apply Filter', leg.fut_with_filter !== false ? 'Yes' : 'No');
    push('Apply Overall SL', leg.fut_sl_override !== false ? 'Yes' : 'No');
    push('Apply Overall Target', leg.fut_target_override !== false ? 'Yes' : 'No');
    push('Apply Spot Adjustment', leg.fut_with_spot_adj !== false ? 'Yes' : 'No');
  } else {
    push('Option Type', String(leg.option_type || '').toUpperCase());
    push('Strike Selection', _strikeLabel(leg.strike_selection, leg.option_type));
    if (leg.strike_interval) push('Strike Gap', leg.strike_interval);
    if (leg.rollover_strike_mode) {
      push('Rollover Strike Mode', leg.rollover_strike_mode === 'fixed' ? 'Fixed' : 'Fresh');
    }
  }

  // Per-leg slippage — buildPayload sends 0 when the leg's slippage is toggled off.
  const sl = Number(leg.slippage_pct) || 0;
  push('Slippage', sl > 0 ? `Yes (${_num(sl)}%)` : 'No');

  if (leg.stopLoss) push('Stop Loss', `${_num(leg.stopLoss.value)}${_unit(leg.stopLoss.mode)}`);
  if (leg.slWithBuffer) {
    push('SL with Buffer',
      `${_num(leg.slWithBuffer.value)}${_unit(leg.slWithBuffer.mode)} (buffer ${_num(leg.slWithBuffer.buffer_pct)}%)`);
  }
  if (leg.trailSL) {
    push('Trailing SL',
      `trigger ${_num(leg.trailSL.trigger)}, move ${_num(leg.trailSL.move)}${_unit(leg.trailSL.mode)}`);
  }
  if (leg.targetProfit) push('Target Profit', `${_num(leg.targetProfit.value)}${_unit(leg.targetProfit.mode)}`);
  if (leg.reEntryOnSL) push('Re-entry on SL', `${leg.reEntryOnSL.mode} × ${_num(leg.reEntryOnSL.count)}`);
  if (leg.reEntryOnTarget) push('Re-entry on Target', `${leg.reEntryOnTarget.mode} × ${_num(leg.reEntryOnTarget.count)}`);
  if (leg.simpleMomentum) push('Simple Momentum', `${leg.simpleMomentum.mode}: ${_num(leg.simpleMomentum.value)}`);
}

function _midcapLegSection(rows, leg, n, payload) {
  const push = (l, v) =>
    rows.push(['kv', l, (v === null || v === undefined || v === '') ? '—' : String(v)]);
  const side = _side(leg.position);
  rows.push(['section', `Leg ${n} — ${leg.symbol || 'NIFTYMIDCAP100'} ${side} (Midcap Overlay)`]);
  push('Position', side);
  push('Lots', leg.lots ?? 1);
  push('Pricing Mode', leg.midcap_mode === 'hypothetical' ? 'Hypothetical Future' : (leg.midcap_mode || '—'));
  if (leg.cost_pct_per_month != null) push('Cost % / month', _num(leg.cost_pct_per_month));
  const mcsa = payload.midcap_spot_adjustment;
  if (mcsa && mcsa.enabled) {
    push('Midcap Spot Adjustment',
      `Yes (${SA_DIR[mcsa.direction] || mcsa.direction || ''} ${_num(mcsa.pct)}${mcsa.units === 'percent' ? '%' : ' pts'})`.replace(/\s+/g, ' ').trim());
  }
  const sl = Number(leg.slippage_pct) || 0;
  push('Slippage', sl > 0 ? `Yes (${_num(sl)}%)` : 'No');
}

export function buildRulesSheet(payload, filterName) {
  if (!payload) return null;
  const rows = [];
  const push = (l, v) =>
    rows.push(['kv', l, (v === null || v === undefined || v === '') ? '—' : String(v)]);

  rows.push(['title', 'STRATEGY RULES']);

  // ── Strategy-level ────────────────────────────────────────────────────────
  rows.push(['section', 'Strategy']);
  push('Index', payload.index || payload.underlying);
  if (payload.date_from || payload.date_to) {
    push('Backtest Date Range', `${payload.date_from || ''} → ${payload.date_to || ''}`);
  }
  push('Expiry', _expiry(payload.expiry_type));
  push('Entry / Exit DTE', `T-${payload.entry_dte ?? 0} to T-${payload.exit_dte ?? 0}`);
  if (payload.square_off_mode) push('Square-off Mode', payload.square_off_mode);

  // YEARLY holds the long-dated December contract while the position re-books on
  // a separate weekly/monthly cadence — two different calendars, so both must be
  // on the sheet or the run isn't reproducible from it.
  const _isYearly = String(payload.expiry_type || '').toUpperCase() === 'YEARLY';
  if (_isYearly) {
    push('Roll Cadence', payload.rollover_cadence === 'weekly' ? 'Weekly' : 'Monthly');
    const _n = Number(payload.yearly_exit_months_before ?? 0);
    push(
      'Yearly Exit',
      _n === 0 ? 'T-0 (hold to long-dated expiry)' : `T-${_n} (${_n} month${_n === 1 ? '' : 's'} before the long-dated expiry)`
    );
    // Which long-dated expiries it rolls through — December alone (default) or
    // alternating with March/June/September. Without this row a Dec+Mar run and
    // a Dec-only run would look identical on the sheet.
    const _monName = { '03': 'March', '06': 'June', '09': 'September', '12': 'December' };
    const _rm = Array.from(new Set(['12', ...(payload.yearly_roll_months || ['12']).map(String)])).sort();
    push('Roll Through', _rm.length === 1 ? 'December only' : _rm.map(m => _monName[m] || m).join(' + '));
  }

  if (_isYearly && payload.rollover_toggle && !payload.no_rollover) {
    // min-DTE is rejected by the engine under YEARLY (it would advance the
    // contract to the next cadence element), so don't imply it applies.
    push('Rollover', `Yes (roll ${payload.rollover_cadence === 'weekly' ? 'weekly' : 'monthly'} within the December contract)`);
  } else if (payload.rollover_toggle && !payload.no_rollover) {
    push('Rollover', `Yes (min ${payload.rollover_min_days_to_expiry ?? 0} days to expiry)`);
  } else if (payload.no_rollover) {
    push('Rollover', `No Rollover (min ${payload.no_rollover_min_days ?? 0} days)`);
  } else {
    push('Rollover', 'None');
  }

  if (payload.spot_adjustment_enabled) {
    const unit = payload.spot_adjustment_units === 'percent' ? '%' : ' pts';
    push('Spot Adjustment',
      `Yes (${SA_DIR[payload.spot_adjustment_direction] || payload.spot_adjustment_direction || ''} ${_num(payload.spot_adjustment_pct)}${unit})`.replace(/\s+/g, ' ').trim());
  } else {
    push('Spot Adjustment', 'No');
  }

  if (payload.buffer_strike_enabled) {
    const unit = payload.buffer_strike_unit === 'percent' ? '%' : ' pts';
    push('Buffer Strike', `Yes (${_num(payload.buffer_strike_value)}${unit}, apply to ${payload.buffer_strike_apply_to || 'both'})`);
  }
  if (Number(payload.strike_shift_max_steps) > 0) {
    push('Strike Shift Fallback', `${payload.strike_shift_max_steps} step(s)`);
  }
  if (payload.overall_sl_type) push('Overall Stop Loss', `${payload.overall_sl_type}: ${_num(payload.overall_sl_value)}`);
  if (payload.overall_target_type) push('Overall Target', `${payload.overall_target_type}: ${_num(payload.overall_target_value)}`);
  push('Cost / Charges', payload.charges_enabled ? 'Enabled' : 'Disabled');
  push('Filter', filterName || 'No Filter');

  rows.push(['spacer']);

  // ── Per-leg (options + futures) ───────────────────────────────────────────
  const legs = Array.isArray(payload.legs) ? payload.legs : [];
  legs.forEach((leg, i) => _legSection(rows, leg, i + 1));

  // ── Midcap overlay legs ───────────────────────────────────────────────────
  const mcLegs = Array.isArray(payload.midcap_legs) ? payload.midcap_legs : [];
  mcLegs.forEach((leg, i) => _midcapLegSection(rows, leg, legs.length + i + 1, payload));

  return rows;
}

export default buildRulesSheet;
