/**
 * Human-readable "Rules" info for an optimize run — used to build the ZIP
 * folder naming, the Optimization Summary's "Rules" block/filename, and (via
 * AutoDownloadQueue's system-wide job discovery) an equivalent rules summary
 * for a job that a DIFFERENT browser/PC queued. Pure functions of
 * (base_payload, filterName, param_specs) — the exact three things persisted
 * in the job's Redis meta (see backend/services/optimizer/runner.py
 * init_job(... extra={...})), so any browser that discovers a job by ID can
 * call these and get the same output the originating browser would have.
 *
 * Moved out of OptimizePanel.jsx (which still imports buildZipNaming /
 * buildRulesInfo from here) so there is exactly one implementation — no risk
 * of the enqueue-time and discovery-time rules text ever diverging.
 */

function _getSLType(legs, selectedList) {
  for (const leg of (legs || [])) if (leg.trailSL) return 'trail';
  for (const leg of (legs || [])) if (leg.slWithBuffer?.value) return 'buffer';
  for (const leg of (legs || [])) if (leg.stopLoss?.value) return 'sl';
  const paths = (selectedList || []).map(s => s.path);
  if (paths.some(p => p.includes('trailSL'))) return 'trail';
  if (paths.some(p => p.includes('slWithBuffer'))) return 'buffer';
  if (paths.some(p => p.includes('stopLoss'))) return 'sl';
  return null;
}

function _getRolloverStrikeMode(legs) {
  for (const leg of (legs || [])) {
    if ((leg.segment || 'options') === 'options') return leg.rollover_strike_mode || 'fresh';
  }
  return 'fresh';
}

export function buildZipNaming(basePayload, filterName, selectedList = []) {
  if (!filterName) return null;
  const legs = basePayload.legs || [];
  const sweepsAdjustment = selectedList.some(s => String(s.path || '').startsWith('spot_adjustment'));

  const order = ['CE', 'PE'];
  const _side = (p) => {
    const s = String(p || '').toLowerCase();
    return (s === 'sell' || s === 'short') ? 'Sell' : 'Buy';
  };
  const _isFut = (l) => String(l.segment || '').toUpperCase() === 'FUTURES';
  const legDesc = (leg) => `${(leg.option_type || '').toUpperCase()} ${_side(leg.position)}`;
  const optLegsStr = order
    .flatMap(t => legs.filter(l => (l.option_type || '').toUpperCase() === t).map(legDesc))
    .join(' ');
  // Futures legs have no option_type, so append them explicitly — otherwise they
  // were dropped from the ZIP folder name (and the summary filename below).
  const futLegsStr = legs.filter(_isFut).map(l => `FUT ${_side(l.position)}`).join(' ');
  const legsStr = [optLegsStr, futLegsStr].filter(Boolean).join(' ');

  const slType = _getSLType(legs, selectedList);
  const slLevel2 = { trail: 'Trail SL', buffer: 'SL With Buffer', sl: 'SL' }[slType] || null;
  const slLevel3 = { trail: 'With Trail SL', buffer: 'With SL Buffer', sl: 'With SL' }[slType] || null;

  // Was `Boolean(basePayload.spot_adjustment_enabled)` alone — the STRATEGY-
  // level toggle only. A leg can carry its OWN spot_adjustment.enabled with
  // the strategy-level toggle off (engine_rust.py's _resolve_leg_sa applies
  // it regardless — the leg's own threshold is live and changes trades), so
  // a per-leg-only adjusted sweep's ZIP folder tree read "No Adj"/"No
  // Adjustment" while the combo workbooks inside it were named
  // "..._L1RiseBy1000pts_...". Folder and contents must agree.
  const legHasOwnAdj = legs.some(l => l && l.spot_adjustment && l.spot_adjustment.enabled);
  const adjEnabled = Boolean(basePayload.spot_adjustment_enabled) || legHasOwnAdj;
  const adjLevel2 = adjEnabled ? 'With Adj' : 'No Adj';
  const adjLevel3 = adjEnabled ? 'Adjustment' : 'No Adjustment';

  const hasRollover   = Boolean(basePayload.rollover_toggle);
  const hasNoRollover = Boolean(basePayload.no_rollover);
  const rolloverPart  = hasRollover ? 'Rollover' : hasNoRollover ? 'No Rollover' : null;
  const strikePart    = (hasRollover || hasNoRollover)
    ? (_getRolloverStrikeMode(legs) === 'fixed' ? 'Fixed' : 'Fresh')
    : null;

  const entryDte = Number(basePayload.entry_dte || 0);
  const exitDte  = Number(basePayload.exit_dte  || 0);
  const dteLevel2 = `T-${entryDte} to T-${exitDte}`;
  const dteLevel3 = `T-${entryDte} to T-${exitDte}`;

  const level2 = [slLevel2, sweepsAdjustment ? null : adjLevel2, rolloverPart, strikePart, dteLevel2].filter(Boolean).join(' ');
  const level3Parts = ['Tradesheet of', legsStr, `In ${filterName}`];
  if (!sweepsAdjustment) level3Parts.push(adjLevel3);
  if (slLevel3) level3Parts.push(slLevel3);
  level3Parts.push(dteLevel3);

  return { level1: filterName, level2, level3: level3Parts.join(' ') };
}

const SPOT_ADJ_DIRECTION_LABEL = { rise: 'Rise', fall: 'Fall', both: 'Rise or Fall' };

function _rangeValues(spec, maxList = 8) {
  const { min, max, step } = spec;
  if (![min, max, step].every(Number.isFinite) || step <= 0 || max < min) return null;
  const values = [];
  for (let v = min; v <= max + 1e-9 && values.length <= maxList; v += step) {
    values.push(Math.round(v * 1000) / 1000);
  }
  return values;
}

function buildSpotAdjustmentSweepLabel(selectedList) {
  const dirSpec = (selectedList || []).find(s => s.path === 'spot_adjustment_direction');
  const pctSpec = (selectedList || []).find(s => s.path === 'spot_adjustment_pct');
  const enabledSpec = (selectedList || []).find(s => s.path === 'spot_adjustment_enabled');

  const directionNames = [];
  const enabledValues = (enabledSpec && enabledSpec.kind === 'enum') ? (enabledSpec.values || []) : null;
  if (enabledValues && enabledValues.includes(false)) directionNames.push('No Adjustment');
  if (dirSpec && dirSpec.kind === 'enum') {
    directionNames.push(...(dirSpec.values || []).map(v => SPOT_ADJ_DIRECTION_LABEL[v] || v));
  } else if (enabledValues && enabledValues.includes(true)) {
    directionNames.push('With Adjustment');
  }

  const parts = [];
  if (directionNames.length) parts.push(directionNames.join(', '));
  if (pctSpec) {
    // The threshold is swept in percent OR absolute index points — the spec's
    // own unit says which, so the rules line must not hardcode '%'.
    const u = pctSpec.unit || '%';
    const join = (arr) => `${arr.join(`${u}, `)}${u}`;
    if (pctSpec.kind === 'enum') {
      parts.push(join(pctSpec.values || []));
    } else if (pctSpec.kind === 'range') {
      const vals = _rangeValues(pctSpec);
      parts.push(vals && vals.length <= 8
        ? join(vals)
        : `${pctSpec.min}${u}–${pctSpec.max}${u} (step ${pctSpec.step}${u})`);
    }
  }
  return parts.length ? parts.join(' @ ') : null;
}

export function buildRulesInfo(basePayload, filterName, selectedList = []) {
  const paths = (selectedList || []).map(s => String(s.path || ''));
  const sweeps = (prefix) => paths.some(p => p.startsWith(prefix));

  const legs = basePayload.legs || [];
  const order = ['CE', 'PE'];
  const legStrikeLabel = (leg, idx) => {
    const typeSpec = selectedList.find(s => s.path === `legs[${idx}].strike_selection.strike_type`);
    const valSpec = selectedList.find(s => s.path === `legs[${idx}].strike_selection.value`);
    const offSpec = selectedList.find(s => s.path === `legs[${idx}].strike_selection.offset`);
    const parts = [];
    const _ss = leg.strike_selection || {};
    const ssType = String(_ss.type || '').toLowerCase();
    if (ssType === 'rel_leg') {
      const ref = Number(_ss.ref_leg) || 1;
      if (offSpec && offSpec.kind === 'range') {
        const vals = _rangeValues(offSpec);
        parts.push(`Rel L${ref} ${vals && vals.length <= 8 ? vals.join('/') : `${offSpec.min}–${offSpec.max}`}G`);
      } else if (offSpec && offSpec.kind === 'enum') {
        parts.push(`Rel L${ref} ${(offSpec.values || []).join('/')}G`);
      } else {
        parts.push(`Rel L${ref} ${Number(_ss.offset) || 0}G`);
      }
      return parts.join(' ');
    }
    if (ssType === 'straddle_width') {
      const multSpec = selectedList.find(s => s.path === `legs[${idx}].strike_selection.straddle_multiplier`);
      const dirSpec = selectedList.find(s => s.path === `legs[${idx}].strike_selection.straddle_direction`);
      let multLabel;
      if (multSpec && multSpec.kind === 'range') {
        const vals = _rangeValues(multSpec);
        multLabel = vals && vals.length <= 8 ? vals.join('/') : `${multSpec.min}–${multSpec.max}`;
      } else if (multSpec && multSpec.kind === 'enum') {
        multLabel = (multSpec.values || []).join('/');
      } else {
        multLabel = `${Number(_ss.straddle_multiplier ?? 0.5)}`;
      }
      // Translate the engine's raw +/- sign into ITM/OTM per option_type,
      // same convention as combo_labeler.py, so the Rules block / ZIP
      // folder name reads unambiguously instead of a bare "+"/"-":
      //   CE '+' (above ATM) = OTM   |   CE '-' (below ATM) = ITM
      //   PE '-' (below ATM) = OTM   |   PE '+' (above ATM) = ITM
      const _isCall = String(leg.option_type || '').toUpperCase().startsWith('C');
      const _toMoneyness = (sign) => {
        const above = String(sign).trim() !== '-';
        return above ? (_isCall ? 'OTM' : 'ITM') : (_isCall ? 'ITM' : 'OTM');
      };
      const dirLabel = (dirSpec && dirSpec.kind === 'enum')
        ? [...new Set((dirSpec.values || []).map(_toMoneyness))].join('/')
        : _toMoneyness(_ss.straddle_direction || '+');
      parts.push(`Straddle ${multLabel}x ${dirLabel}`);
      return parts.join(' ');
    }
    if (ssType.startsWith('time_value')) {
      // Target (+unit), side and range cap — each shown as its swept range when
      // the optimizer is sweeping it, otherwise as the fixed base value.
      const tgtSpec = selectedList.find(s => s.path === `legs[${idx}].strike_selection.premium`);
      const sideSpec = selectedList.find(s => s.path === `legs[${idx}].strike_selection.moneyness`);
      const capSpec = selectedList.find(s => s.path === `legs[${idx}].strike_selection.tv_range_pct`);
      const unitSpec = selectedList.find(s => s.path === `legs[${idx}].strike_selection.tv_units`);
      const spread = (spec, fallback) => {
        if (spec && spec.kind === 'range') {
          const vals = _rangeValues(spec);
          return vals && vals.length <= 8 ? vals.join('/') : `${spec.min}–${spec.max}`;
        }
        if (spec && spec.kind === 'enum') return (spec.values || []).join('/');
        return fallback;
      };
      const op = ssType === 'time_value_gte' ? '>=' : ssType === 'time_value_lte' ? '<=' : '';
      const unit = unitSpec
        ? spread(unitSpec, '').split('/').map(u => (u === 'percent' ? '%' : 'pts')).join('/')
        : (String(_ss.tv_units || 'points') === 'percent' ? '%' : 'pts');
      const tgt = spread(tgtSpec, `${_ss.time_value ?? _ss.premium ?? 0}`);
      const side = spread(sideSpec, String(_ss.moneyness || 'ATM').toUpperCase());
      const cap = spread(capSpec, `${Number(_ss.tv_range_pct) || 0}`);
      parts.push(`TV ${op}${op ? ' ' : ''}${tgt}${unit} ${side}`);
      if (cap && cap !== '0') parts.push(`range ${cap}%`);
      return parts.join(' ');
    }
    if (ssType === 'atm_straddle_prem_pct') {
      if (valSpec) {
        if (valSpec.kind === 'range') {
          const vals = _rangeValues(valSpec);
          parts.push(`Straddle ${vals && vals.length <= 8 ? vals.join('%, ') : `${valSpec.min}–${valSpec.max}`}%`);
        } else if (valSpec.kind === 'enum') {
          parts.push(`Straddle ${(valSpec.values || []).join('%, ')}%`);
        }
      } else if (_ss.value != null) {
        parts.push(`Straddle ${_ss.value}%`);
      }
      return parts.join(' ');
    }
    if (typeSpec && typeSpec.kind === 'enum') {
      const vals = typeSpec.values || [];
      parts.push(vals.length <= 8 ? vals.join(', ') : `${vals.length} strike types (see column below)`);
    } else if (leg.strike_selection && leg.strike_selection.strike_type) {
      parts.push(leg.strike_selection.strike_type);
    }
    if (valSpec) {
      if (valSpec.kind === 'range') {
        const vals = _rangeValues(valSpec);
        parts.push(vals && vals.length <= 8
          ? `${vals.join('%, ')}%`
          : `${valSpec.min}%–${valSpec.max}% (step ${valSpec.step}%)`);
      } else if (valSpec.kind === 'enum') {
        parts.push(`${(valSpec.values || []).join('%, ')}%`);
      }
    } else if (leg.strike_selection && leg.strike_selection.type === 'PCT_OF_ATM' && leg.strike_selection.value != null) {
      parts.push(`${leg.strike_selection.value}%`);
    }
    return parts.join(' ');
  };
  const _side = (p) => {
    const s = String(p || '').toLowerCase();
    return (s === 'sell' || s === 'short') ? 'Sell' : 'Buy';
  };
  const _isFut = (l) => String(l.segment || '').toUpperCase() === 'FUTURES';
  const _FUT_EXP = { WEEKLY: 'Weekly', MONTHLY: 'Monthly', NEXT_WEEKLY: 'Next Weekly', NEXT_MONTHLY: 'Next Monthly' };
  const legDesc = (leg, idx) => {
    const t = (leg.option_type || '').toUpperCase();
    const side = _side(leg.position);
    const strikeLabel = legStrikeLabel(leg, idx);
    return strikeLabel ? `${t} ${side} (${strikeLabel})` : `${t} ${side}`;
  };
  const futDesc = (leg) => {
    const side = _side(leg.position);
    const exp = _FUT_EXP[String(leg.expiry || '').toUpperCase()] || leg.expiry || '';
    return exp ? `FUT ${side} (${exp})` : `FUT ${side}`;
  };
  const legsWithIdx = legs.map((l, i) => ({ l, i }));
  // Options legs first (CE then PE), then any futures legs. Previously futures
  // were dropped entirely from the Rules block (they carry no option_type).
  const legLines = [
    ...order.flatMap(t => legsWithIdx
      .filter(({ l }) => (l.option_type || '').toUpperCase() === t)
      .map(({ l, i }) => legDesc(l, i))),
    ...legs.filter(_isFut).map(futDesc),
  ];
  const optionTypes = [...new Set(legs.map(l => (l.option_type || '').toUpperCase()).filter(Boolean))];
  if (legs.some(_isFut)) optionTypes.push('FUT');

  // Per-leg breakdown so the Rules block can say exactly which leg carries which
  // strike / gap / own spot-adjustment — the flat "Legs" line alone can't (e.g.
  // "Strike Gap: per-leg (100, 1000)" doesn't say which leg is 100 vs 1000).
  // Find a leg's swept spec and render it the way the optimizer writes ranges:
  // "0.5 to 2%" for a range, "rise/fall/both" (or "Off/On") for an enum, so a
  // swept per-leg setting shows its actual from→to instead of a bare "swept".
  const _specFor = (path) => (selectedList || []).find(s => String(s.path || '') === path);
  const _specRange = (spec, unit) => {
    if (!spec) return null;
    const u = unit || '';
    if (spec.kind === 'range') {
      return spec.min === spec.max
        ? `${spec.min}${u}`
        : `${spec.min} to ${spec.max}${u}${spec.step ? ` (step ${spec.step})` : ''}`;
    }
    if (spec.kind === 'enum') {
      return (spec.values || []).map(v => v === true ? 'On' : v === false ? 'Off' : v).join('/');
    }
    return null;
  };
  const _slUnit = (mode) => {
    const m = String(mode || '').toUpperCase();
    return (m.includes('PERCENT') || m.includes('PCT') || m === '%') ? '%' : ' pts';
  };
  const _legOwnAdj = (leg, i) => {
    const sa = leg.spot_adjustment || {};
    const unit = sa.units === 'points' ? ' pts' : '%';
    const pctSpec = _specFor(`legs[${i}].spot_adjustment.pct`);
    const dirSpec = _specFor(`legs[${i}].spot_adjustment.direction`);
    const enSpec = _specFor(`legs[${i}].spot_adjustment.enabled`);
    if (pctSpec || dirSpec || enSpec) {
      const bits = [];
      if (pctSpec) bits.push(_specRange(pctSpec, unit));
      else if (sa.pct != null) bits.push(`${sa.pct}${unit}`);
      if (dirSpec) bits.push(_specRange(dirSpec));
      else if (sa.direction) bits.push(sa.direction);
      if (enSpec) bits.push(_specRange(enSpec));
      return bits.filter(Boolean).join(', ');
    }
    if (sa.enabled && Number(sa.pct) > 0) {
      const dir = sa.direction === 'fall' ? 'Fall' : sa.direction === 'both' ? 'Rise or Fall' : 'Rise';
      return `${dir} ${sa.pct}${unit}`;
    }
    return null;
  };
  const legDetails = legs.map((leg, i) => {
    const seg = String(leg.segment || 'OPTIONS').toUpperCase();
    const side = _side(leg.position);
    const parts = [];
    let head;
    if (seg === 'FUTURES') {
      head = `FUT ${side}`;
      const exp = _FUT_EXP[String(leg.expiry || '').toUpperCase()] || leg.expiry || '';
      if (exp) parts.push(exp);
    } else {
      head = `${(leg.option_type || '').toUpperCase()} ${side}`;
      // legStrikeLabel already renders a swept strike as its range / value list.
      const strike = legStrikeLabel(leg, i);
      if (strike) parts.push(`Strike ${strike}`);
      const gap = Number(leg.strike_interval ?? (leg.strike_selection || {}).strike_interval) || 0;
      if (gap > 0) parts.push(`Gap ${gap}`);
    }
    // Whatever risk settings the leg carries — SL / buffer / trail / target /
    // re-entry / slippage — so the reader sees the full per-leg configuration.
    if (leg.stopLoss) parts.push(`SL ${Number(leg.stopLoss.value)}${_slUnit(leg.stopLoss.mode)}`);
    if (leg.slWithBuffer) parts.push(`SL Buf ${Number(leg.slWithBuffer.value)}${_slUnit(leg.slWithBuffer.mode)}/${Number(leg.slWithBuffer.buffer_pct)}%`);
    if (leg.trailSL) parts.push(`Trail ${Number(leg.trailSL.trigger)}→${Number(leg.trailSL.move)}${_slUnit(leg.trailSL.mode)}`);
    if (leg.targetProfit) parts.push(`Target ${Number(leg.targetProfit.value)}${_slUnit(leg.targetProfit.mode)}`);
    if (leg.reEntryOnSL) parts.push(`RE-SL ${leg.reEntryOnSL.mode}×${leg.reEntryOnSL.count}`);
    if (leg.reEntryOnTarget) parts.push(`RE-Tgt ${leg.reEntryOnTarget.mode}×${leg.reEntryOnTarget.count}`);
    const _slp = Number(leg.slippage_pct) || 0;
    if (_slp > 0) parts.push(`Slippage ${_slp}%`);
    const own = _legOwnAdj(leg, i);
    if (own) parts.push(`Own Adj ${own}`);
    return { label: `Leg ${i + 1}`, value: parts.length ? `${head} — ${parts.join(', ')}` : head };
  });

  const EXPIRY_LABELS = {
    WEEKLY: 'Weekly',
    MONTHLY: 'Monthly',
    NEXT_WEEKLY: 'Next Weekly',
    NEXT_MONTHLY: 'Next Monthly',
    YEARLY: 'Yearly (December)',
  };
  const legExpiries = [...new Set(legs.map(l => String(l.expiry || '').toUpperCase()).filter(Boolean))];
  let expiryLabel = legExpiries.length
    ? legExpiries.map(e => EXPIRY_LABELS[e] || e).join(' / ')
    : (EXPIRY_LABELS[String(basePayload.expiry_type || '').toUpperCase()] || basePayload.expiry_type || '—');
  // YEARLY: surface the roll-through months + cadence + T-n so the optim rules
  // reflect the same settings as the backtest (Dec-only vs Dec+Mar etc).
  if (String(basePayload.expiry_type || '').toUpperCase() === 'YEARLY') {
    const _mon = { '03': 'Mar', '06': 'Jun', '09': 'Sep', '12': 'Dec' };
    const _rm = [...new Set(['12', ...(basePayload.yearly_roll_months || ['12']).map(String)])].sort();
    const _cad = basePayload.rollover_cadence === 'weekly' ? 'Weekly' : 'Monthly';
    const _n = Number(basePayload.yearly_exit_months_before || 0);
    expiryLabel = `Yearly [${_rm.map(m => _mon[m] || m).join('+')}] · ${_cad} · T-${_n}`;
  }

  const slType = _getSLType(legs, selectedList);
  const slLabel = { trail: 'Trailing SL', buffer: 'SL With Buffer', sl: 'Stop Loss' }[slType] || 'No SL';

  const adjEnabled = Boolean(basePayload.spot_adjustment_enabled);
  const rolloverOn = Boolean(basePayload.rollover_toggle);
  const noRolloverOn = Boolean(basePayload.no_rollover);

  // Slippage is per-leg now (no strategy-level slippage_pct) — show it only
  // when at least one leg actually has a nonzero value.
  const legSlippages = legs.map(l => Number(l.slippage_pct) || 0).filter(v => v > 0);
  const slippageUniform = legSlippages.length > 0 && legSlippages.every(v => v === legSlippages[0]);

  // Strike gap is per-leg and options-only (futures carry no strike). Show the
  // single value when every option leg shares one, otherwise list the distinct
  // gaps — mirrors the per-leg slippage treatment above. Not a sweepable param,
  // so there is no "Varies" case.
  const legGaps = legs
    .filter(l => String(l.segment || 'OPTIONS').toUpperCase() !== 'FUTURES')
    .map(l => Number(l.strike_interval ?? (l.strike_selection || {}).strike_interval) || 0)
    .filter(v => v > 0);
  const uniqueGaps = [...new Set(legGaps)].sort((a, b) => a - b);

  return {
    index: basePayload.index || basePayload.underlying || '—',
    optionTypes: optionTypes.length ? optionTypes.join(' & ') : '—',
    legs: legLines.length ? legLines.join(', ') : '—',
    legDetails,
    expiry: expiryLabel,
    entryDte: Number(basePayload.entry_dte || 0),
    exitDte: Number(basePayload.exit_dte || 0),
    slLabel,
    spotAdjustment: adjEnabled
      ? `Yes (${basePayload.spot_adjustment_direction || ''} ${basePayload.spot_adjustment_pct || ''}${basePayload.spot_adjustment_units === 'points' ? 'pts' : '%'})`.trim()
      : 'No',
    rollover: rolloverOn ? 'Rollover' : noRolloverOn ? 'No Rollover' : '—',
    filter: filterName || 'No Filter',
    slippage: legSlippages.length === 0
      ? 'No Slippage'
      : slippageUniform
        ? `With Slippage (${legSlippages[0]}%)`
        : 'With Slippage (per-leg)',
    strikeGap: uniqueGaps.length === 0
      ? '—'
      : uniqueGaps.length === 1
        ? String(uniqueGaps[0])
        : `per-leg (${uniqueGaps.join(', ')})`,
    cost: basePayload.charges_enabled ? 'With Cost' : 'No Cost',
    dateFrom: basePayload.date_from || '',
    dateTo: basePayload.date_to || '',
    spotAdjustmentSweepLabel: buildSpotAdjustmentSweepLabel(selectedList),
    swept: {
      expiry: sweeps('expiry_type') || paths.some(p => /^legs\[\d+\]\.expiry$/.test(p)),
      slLabel: paths.some(p => /^legs\[\d+\]\.(stopLoss|trailSL|slWithBuffer)/.test(p)),
      spotAdjustment: sweeps('spot_adjustment'),
      rollover: sweeps('rollover') || paths.includes('no_rollover'),
      slippage: paths.some(p => /^legs\[\d+\]\.slippage_pct$/.test(p)),
      cost: paths.includes('charges_enabled'),
      legs: paths.some(p => /^legs\[\d+\]\.(option_type|position)$/.test(p)),
    },
  };
}
