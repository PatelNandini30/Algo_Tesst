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
  const legDesc = (leg) => {
    const t = (leg.option_type || '').toUpperCase();
    const p = (leg.position || '').toLowerCase();
    return `${t} ${(p === 'sell' || p === 'short') ? 'Sell' : 'Buy'}`;
  };
  const legsStr = order
    .flatMap(t => legs.filter(l => (l.option_type || '').toUpperCase() === t).map(legDesc))
    .join(' ');

  const slType = _getSLType(legs, selectedList);
  const slLevel2 = { trail: 'Trail SL', buffer: 'SL With Buffer', sl: 'SL' }[slType] || null;
  const slLevel3 = { trail: 'With Trail SL', buffer: 'With SL Buffer', sl: 'With SL' }[slType] || null;

  const adjEnabled = Boolean(basePayload.spot_adjustment_enabled);
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
    if (pctSpec.kind === 'enum') {
      parts.push(`${(pctSpec.values || []).join('%, ')}%`);
    } else if (pctSpec.kind === 'range') {
      const vals = _rangeValues(pctSpec);
      parts.push(vals && vals.length <= 8
        ? `${vals.join('%, ')}%`
        : `${pctSpec.min}%–${pctSpec.max}% (step ${pctSpec.step}%)`);
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
      const dirLabel = (dirSpec && dirSpec.kind === 'enum')
        ? (dirSpec.values || []).join('/')
        : String(_ss.straddle_direction || '+').trim();
      parts.push(`Straddle ${dirLabel}${multLabel}x`);
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
  const legDesc = (leg, idx) => {
    const t = (leg.option_type || '').toUpperCase();
    const p = (leg.position || '').toLowerCase();
    const side = (p === 'sell' || p === 'short') ? 'Sell' : 'Buy';
    const strikeLabel = legStrikeLabel(leg, idx);
    return strikeLabel ? `${t} ${side} (${strikeLabel})` : `${t} ${side}`;
  };
  const legsWithIdx = legs.map((l, i) => ({ l, i }));
  const legLines = order.flatMap(t => legsWithIdx
    .filter(({ l }) => (l.option_type || '').toUpperCase() === t)
    .map(({ l, i }) => legDesc(l, i)));
  const optionTypes = [...new Set(legs.map(l => (l.option_type || '').toUpperCase()).filter(Boolean))];

  const EXPIRY_LABELS = {
    WEEKLY: 'Weekly',
    MONTHLY: 'Monthly',
    NEXT_WEEKLY: 'Next Weekly',
    NEXT_MONTHLY: 'Next Monthly',
  };
  const legExpiries = [...new Set(legs.map(l => String(l.expiry || '').toUpperCase()).filter(Boolean))];
  const expiryLabel = legExpiries.length
    ? legExpiries.map(e => EXPIRY_LABELS[e] || e).join(' / ')
    : (EXPIRY_LABELS[String(basePayload.expiry_type || '').toUpperCase()] || basePayload.expiry_type || '—');

  const slType = _getSLType(legs, selectedList);
  const slLabel = { trail: 'Trailing SL', buffer: 'SL With Buffer', sl: 'Stop Loss' }[slType] || 'No SL';

  const adjEnabled = Boolean(basePayload.spot_adjustment_enabled);
  const rolloverOn = Boolean(basePayload.rollover_toggle);
  const noRolloverOn = Boolean(basePayload.no_rollover);

  const slippagePct = Number(basePayload.slippage_pct) || 0;

  return {
    index: basePayload.index || basePayload.underlying || '—',
    optionTypes: optionTypes.length ? optionTypes.join(' & ') : '—',
    legs: legLines.length ? legLines.join(', ') : '—',
    expiry: expiryLabel,
    entryDte: Number(basePayload.entry_dte || 0),
    exitDte: Number(basePayload.exit_dte || 0),
    slLabel,
    spotAdjustment: adjEnabled
      ? `Yes (${basePayload.spot_adjustment_direction || ''} ${basePayload.spot_adjustment_pct || ''}${basePayload.spot_adjustment_units === 'percent' ? '%' : ''})`.trim()
      : 'No',
    rollover: rolloverOn ? 'Rollover' : noRolloverOn ? 'No Rollover' : '—',
    filter: filterName || 'No Filter',
    slippage: slippagePct > 0 ? `With Slippage (${slippagePct}%)` : 'No Slippage',
    cost: basePayload.charges_enabled ? 'With Cost' : 'No Cost',
    dateFrom: basePayload.date_from || '',
    dateTo: basePayload.date_to || '',
    spotAdjustmentSweepLabel: buildSpotAdjustmentSweepLabel(selectedList),
    swept: {
      expiry: sweeps('expiry_type') || paths.some(p => /^legs\[\d+\]\.expiry$/.test(p)),
      slLabel: paths.some(p => /^legs\[\d+\]\.(stopLoss|trailSL|slWithBuffer)/.test(p)),
      spotAdjustment: sweeps('spot_adjustment'),
      rollover: sweeps('rollover') || paths.includes('no_rollover'),
      slippage: paths.includes('slippage_pct'),
      cost: paths.includes('charges_enabled'),
      legs: paths.some(p => /^legs\[\d+\]\.(option_type|position)$/.test(p)),
    },
  };
}
