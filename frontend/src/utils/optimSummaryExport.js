/**
 * Shared "Optimization Summary" Excel builder — used by BOTH the manual
 * Export-XLSX button (OptimizationResults.jsx) and the auto-download queue
 * (AutoDownloadQueue.jsx) so the two paths can never produce different
 * numbers/layout. Pure client-side computation from already-fetched rows.
 */
import { resolveDownloadBase } from './downloadBase';
import { MASTER_SUMMARY_COLUMNS } from './strategyParamSchema';
import { buildRulesSheet } from './backtestRulesSheet';

const VARIES = 'Varies — see column below';

/** Which leg types are present, for hiding conditional columns. */
export function computeLegPresence(rows) {
  let hasCE = false;
  let hasPE = false;
  let hasSpot = false;
  let hasMidcap = false;
  for (const r of rows) {
    const s = r.summary || {};
    if (!hasCE && s.ce_pnl_total != null && Math.abs(Number(s.ce_pnl_total)) > 0.01) hasCE = true;
    if (!hasPE && s.pe_pnl_total != null && Math.abs(Number(s.pe_pnl_total)) > 0.01) hasPE = true;
    if (!hasSpot && s.long_spot_pnl != null && Math.abs(Number(s.long_spot_pnl)) > 0.01) hasSpot = true;
    if (!hasMidcap && s.has_midcap) hasMidcap = true;
    if (hasCE && hasPE && hasSpot && hasMidcap) break;
  }
  return { hasCE, hasPE, hasSpot, hasMidcap, notMidcap: !hasMidcap };
}

export function visibleColumnsFor(rows) {
  const legPresence = computeLegPresence(rows);
  return MASTER_SUMMARY_COLUMNS.filter((c) => {
    if (!c.conditional) return true;
    return legPresence[c.conditional] === true;
  });
}

export function rulesFilename(ruleConfig, jobId, suffix) {
  if (!ruleConfig) return `optimize_${jobId}${suffix}.xlsx`;
  const swept = ruleConfig.swept || {};
  const clean = (s) => String(s || '').replace(/[\\/:*?"<>|]/g, '').trim();
  const dateRange = (ruleConfig.dateFrom && ruleConfig.dateTo)
    ? `${ruleConfig.dateFrom}_to_${ruleConfig.dateTo}`
    : null;
  const parts = [
    clean(ruleConfig.index),
    !swept.legs ? clean(ruleConfig.optionTypes) : null,
    !swept.expiry ? clean(ruleConfig.expiry) : null,
    ruleConfig.filter && ruleConfig.filter !== 'No Filter' ? clean(ruleConfig.filter) : null,
    (!swept.slippage && ruleConfig.slippage !== 'No Slippage') ? clean(ruleConfig.slippage) : null,
    (!swept.cost && ruleConfig.cost !== 'No Cost') ? clean(ruleConfig.cost) : null,
    dateRange,
  ].filter(Boolean);
  return `optimize_summary_${parts.join('_')}${suffix}.xlsx`;
}

export function buildRuleRows(ruleConfig) {
  if (!ruleConfig) return [];
  const swept = ruleConfig.swept || {};
  const rows = [];
  const push = (label, value) => rows.push([label, value]);
  const pushOrVaries = (label, value, isSwept) => push(label, isSwept ? VARIES : value);

  push('Index', ruleConfig.index);
  pushOrVaries('Option Type(s)', ruleConfig.optionTypes, swept.legs);
  pushOrVaries('Legs', ruleConfig.legs, swept.legs);
  // Per-leg breakdown — which leg carries which strike / gap / own adjustment.
  (ruleConfig.legDetails || []).forEach(({ label, value }) => push(label, value));
  // Per-leg, options-only; omitted when unknown (e.g. a futures-only strategy).
  if (ruleConfig.strikeGap && ruleConfig.strikeGap !== '—') push('Strike Gap', ruleConfig.strikeGap);
  pushOrVaries('Expiry', ruleConfig.expiry, swept.expiry);
  push('Entry / Exit DTE', `T-${ruleConfig.entryDte} to T-${ruleConfig.exitDte}`);
  if (swept.slLabel) push('Stop Loss', VARIES);
  else if (ruleConfig.slLabel && ruleConfig.slLabel !== 'No SL') push('Stop Loss', ruleConfig.slLabel);
  if (swept.spotAdjustment) push('Spot Adjustment', ruleConfig.spotAdjustmentSweepLabel || VARIES);
  else if (ruleConfig.spotAdjustment && ruleConfig.spotAdjustment !== 'No') push('Spot Adjustment', ruleConfig.spotAdjustment);
  if (swept.rollover) push('Rollover', VARIES);
  else if (ruleConfig.rollover === 'Rollover') push('Rollover', ruleConfig.rollover);
  if (ruleConfig.filter && ruleConfig.filter !== 'No Filter') push('Filter', ruleConfig.filter);
  if (swept.slippage) push('Slippage', VARIES);
  else if (ruleConfig.slippage && ruleConfig.slippage !== 'No Slippage') push('Slippage', ruleConfig.slippage);
  if (swept.cost) push('Cost / Charges', VARIES);
  else if (ruleConfig.cost && ruleConfig.cost !== 'No Cost') push('Cost / Charges', ruleConfig.cost);
  push('Backtest Date Range', (ruleConfig.dateFrom && ruleConfig.dateTo)
    ? `${ruleConfig.dateFrom} to ${ruleConfig.dateTo}` : '—');
  return rows;
}

/**
 * Build the "Optimization Summary" workbook exactly as the manual Export-XLSX
 * button does. `summaryByCombo` (Map<comboId,summary>) is optional — pass the
 * patchwise-recomputed metrics (from GET .../summary?patchwise=true) to match
 * the patchwise ZIP; omit it to use each row's own (overall) summary.
 */
// ── "Optimized Parameters" section for the master-summary Rules sheet ────────
// Turns the sweep specs (param_specs) into readable rows: which axis was
// optimized, its values/range, and how many. So the master summary shows the
// COMBINATION space the run explored (the per-combo tradesheets show concretes).
function _humanParamLabel(path) {
  const p = String(path || '');
  const m = p.match(/^legs\[(\d+)\]\.(.+)$/);
  const legPrefix = m ? `Leg ${Number(m[1]) + 1} ` : '';
  const key = m ? m[2] : p;
  const LEG = {
    'strike_selection.strike_type': 'Strike Type',
    'strike_selection.value': 'Strike %',
    'strike_selection.offset': 'Strike Offset (gaps)',
    'strike_selection.straddle_multiplier': 'Straddle Width',
    'strike_selection.straddle_direction': 'Straddle Direction',
    'strike_selection.premium': 'Time Value / Premium',
    'strike_selection.moneyness': 'Time Value Side',
    'strike_selection.tv_range_pct': 'Time Value Range %',
    'strike_selection.tv_units': 'Time Value Unit',
    'spot_adjustment.pct': 'Own Spot Adjustment',
    'spot_adjustment.direction': 'Own Spot Adj Direction',
    'spot_adjustment.enabled': 'Own Spot Adj On/Off',
    'spot_adjustment.units': 'Own Spot Adj Unit',
    'stopLoss.value': 'Stop Loss',
    'slWithBuffer.value': 'SL Buffer',
    'trailSL.trigger': 'Trail SL Trigger',
    'trailSL.move': 'Trail SL Move',
    'targetProfit.value': 'Target',
    'slippage_pct': 'Slippage %',
    'expiry': 'Expiry',
  };
  const TOP = {
    'spot_adjustment_pct': 'Spot Adjustment',
    'spot_adjustment_direction': 'Spot Adjustment Direction',
    'spot_adjustment_enabled': 'Spot Adjustment On/Off',
    'expiry_type': 'Expiry',
    'rollover_toggle': 'Rollover On/Off',
    'no_rollover': 'No-Rollover On/Off',
    'slippage_pct': 'Slippage %',
    'charges_enabled': 'Cost / Charges',
    'entry_dte': 'Entry DTE',
    'exit_dte': 'Exit DTE',
  };
  return m ? legPrefix + (LEG[key] || key) : (TOP[key] || key);
}

function _specValuesLabel(spec) {
  if (!spec) return '';
  if (spec.kind === 'enum') {
    const vals = (spec.values || []).map(v => v === true ? 'On' : v === false ? 'Off' : String(v));
    return { text: vals.join(', '), count: vals.length };
  }
  if (spec.kind === 'range') {
    const { min, max, step } = spec;
    const nums = [];
    if ([min, max, step].every(Number.isFinite) && step > 0 && max >= min) {
      for (let v = min; v <= max + 1e-9 && nums.length <= 64; v += step) nums.push(Math.round(v * 1000) / 1000);
    }
    if (min === max) return { text: String(min), count: 1 };
    const text = (nums.length && nums.length <= 8)
      ? nums.join(', ')
      : `${min} to ${max}${step ? ` (step ${step})` : ''}`;
    return { text, count: nums.length || null };
  }
  return { text: '', count: null };
}

export function buildOptimizedParamsRows(paramSpecs, comboCount) {
  const specs = Array.isArray(paramSpecs) ? paramSpecs.filter(s => s && s.path) : [];
  if (!specs.length) return [];
  const rows = [['section', `OPTIMIZED PARAMETERS${comboCount ? `  (${comboCount} combinations)` : ''}`]];
  for (const spec of specs) {
    const { text, count } = _specValuesLabel(spec);
    const value = count ? `${text}  (${count})` : text;
    rows.push(['kv', _humanParamLabel(spec.path), value || '—']);
  }
  return rows;
}

export async function buildSummaryWorkbookBlob(ruleConfig, jobId, basePayload, patchwise = true, sort = null) {
  // The WORKBOOK is built entirely on the backend (services/optimizer/
  // summary_workbook.py + get_optim_summary's patchwise overlay) — the browser
  // used to relay every row (and, for patchwise, a SECOND full fetch of every
  // row's summary) back up as the POST body, so an export held 2-3 complete
  // copies of the sweep in tab memory at once and could OOM the tab well before
  // any table-render cost. The server already has the rows (result_store) and
  // already applies the identical patchwise overlay (get_optim_summary), so
  // there is nothing left for the client to supply except the Rules block,
  // which genuinely does live only in the browser's sweep-config state.
  const base = await resolveDownloadBase(jobId);

  // Leg-wise "Rules" sheet — the SAME buildRulesSheet the backtest download uses,
  // so the optim master summary gets an identical first "Rules" sheet (per-leg
  // strike/gap/own-adjustment, Midcap leg, per-leg slippage, fresh/fixed). Needs
  // the raw base_payload; the caller passes it, else fetch it from the job meta.
  let bp = basePayload;
  let paramSpecs = [];
  if (jobId) {
    // Always fetch the meta so we have param_specs (the sweep axes) for the
    // "Optimized Parameters" section — and use its base_payload when the caller
    // didn't pass one.
    let comboCount = 0;
    try {
      const mr = await fetch(`${base}/api/optimize/jobs/${jobId}`);
      if (mr.ok) {
        const md = await mr.json();
        const meta = md?.meta || md || {};
        if (!bp) bp = meta.base_payload || md?.base_payload || null;
        paramSpecs = meta.param_specs || md?.param_specs || [];
        comboCount = Number(meta.total ?? md?.total ?? 0);
      }
    } catch { /* fall back to no Rules sheet / no optimized-params section */ }
  }
  const filterName = (ruleConfig?.filter && ruleConfig.filter !== 'No Filter') ? ruleConfig.filter : null;
  let rulesSheet = null;
  try { if (bp) rulesSheet = buildRulesSheet(bp, filterName); } catch { rulesSheet = null; }
  // Append the swept combination space (which axes were optimized + their values).
  try {
    if (rulesSheet && paramSpecs && paramSpecs.length) {
      const optRows = buildOptimizedParamsRows(paramSpecs, comboCount);
      if (optRows.length) rulesSheet = [...rulesSheet, ['spacer'], ...optRows];
    }
  } catch { /* optimized-params section is best-effort */ }

  const res = await fetch(`${base}/api/optimize/jobs/${jobId}/summary.xlsx`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // Rows are NOT sent for large sweeps. Each trimmed row is ~1,888 bytes, so
    // 26,500 combos already exceeds nginx's client_max_body_size (50 MB) and the
    // export died with a 413 that looked like a broken download — 63,504 combos
    // is 120 MB, 100,000 is 189 MB. Above the threshold the backend reads the
    // same stored rows itself (and applies the same patchwise override), so the
    // workbook is identical without shipping the data back to the server.
    // Small sweeps keep posting rows: that path is well-tested and lets the
    // client's already-resolved per-combo summary win.
    body: JSON.stringify({
      // No `rows`: the server loads them itself (result_store.get_all_results)
      // and applies the identical patchwise overlay — see the note above.
      // MUST be sent explicitly. Omitting it let the backend default
      // (payload.get("patchwise", True)) win, so an Overall-mode sweep got a
      // patchwise workbook saved under an _overall filename — the same
      // inversion as the download buttons.
      patchwise: Boolean(patchwise),
      ...(sort && sort.by ? { sort_by: sort.by, order: sort.order || 'desc' } : {}),
      rule_rows: buildRuleRows(ruleConfig),
      rules_sheet: rulesSheet,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Summary build failed (HTTP ${res.status})`);
  }
  return res.blob();
}

/**
 * Fetch a URL that may return 202 (still building) before 200 (ready) — same
 * poll contract as the tradesheets.zip endpoint. Used by both the manual ZIP
 * button and the auto-download queue so retry/backoff behavior is identical.
 * Returns { blob, filename } or throws.
 */
/**
 * Same 202-poll contract, but once the file is ready the BROWSER downloads it
 * natively (plain navigation) instead of buffering the body into a Blob. A
 * tradesheets ZIP is ~300 MB — `await r.blob()` holds all of that in tab memory
 * and silently fails on a constrained browser, which is why big ZIP downloads
 * did nothing. Navigation streams straight to disk and uses the response's
 * Content-Disposition filename. Use this for the ZIP; small files (WOW/MOM,
 * summary) can stay on the Blob path.
 */
export async function downloadWhenReady(url, { maxWaitMs = 20 * 60 * 1000, onProgress } = {}) {
  const start = Date.now();
  while (true) {
    // fetch() resolves once the HEADERS arrive; cancel the body so the probe
    // never transfers the ~300 MB payload (the endpoint ignores Range, so a
    // ranged GET would download the whole file).
    const r = await fetch(url);
    if (r.status === 200) {
      r.body?.cancel?.();
      const a = document.createElement('a');
      a.href = url;
      a.download = '';           // let Content-Disposition name it
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      return r.headers.get('x-filename') || null;
    }
    if (r.status === 202) {
      if (onProgress) onProgress(await r.json().catch(() => ({})));
      if (Date.now() - start > maxWaitMs) throw new Error('Build is taking longer than expected — try again later.');
      await new Promise((res) => setTimeout(res, 2000));
      continue;
    }
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || err.error || `HTTP ${r.status}`);
  }
}

export async function fetchBlobWithPoll(url, { maxWaitMs = 20 * 60 * 1000, onProgress } = {}) {
  const start = Date.now();
  while (true) {
      const r = await fetch(url);
      if (r.status === 200) {
        const blob = await r.blob();
        const filename = r.headers.get('x-filename') || null;
      return { blob, filename };
    }
    if (r.status === 202) {
      if (onProgress) {
        const info = await r.json().catch(() => ({}));
        onProgress(info);
      }
      if (Date.now() - start > maxWaitMs) {
        throw new Error('Build is taking longer than expected — try again later.');
      }
      await new Promise((res) => setTimeout(res, 2000));
      continue;
    }
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || err.error || `HTTP ${r.status}`);
  }
}

export function triggerBlobDownload(blob, filename) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
}
