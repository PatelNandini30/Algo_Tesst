/**
 * Shared "Optimization Summary" Excel builder — used by BOTH the manual
 * Export-XLSX button (OptimizationResults.jsx) and the auto-download queue
 * (AutoDownloadQueue.jsx) so the two paths can never produce different
 * numbers/layout. Pure client-side computation from already-fetched rows.
 */
import { resolveDownloadBase } from './downloadBase';
import { MASTER_SUMMARY_COLUMNS } from './strategyParamSchema';

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
export async function buildSummaryWorkbookBlob(rows, ruleConfig, summaryByCombo, jobId) {
  // The WORKBOOK is built on the backend (services/optimizer/summary_workbook.py), so
  // every .xlsx this product emits comes from one builder — openpyxl, server-side:
  //   tradesheet -> excel_builder.build_combo_xlsx
  //   WOW & MOM  -> wow_mom.write_merged_wow_mom
  //   this sheet -> summary_workbook.build_summary_workbook
  // We still resolve the per-combo summary and derive the Rules block here, because
  // that reads the sweep config the client already holds; re-deriving it in Python
  // would create a second implementation of the sweep-label logic, which is exactly
  // the duplication this consolidation removes.
  const payloadRows = rows.map((row) => ({
    summary:
      (summaryByCombo && summaryByCombo.get(String(row.combo_id))) ||
      row.summary ||
      {},
    combo_columns: row.combo_columns || {},
  }));
  const base = await resolveDownloadBase(jobId);
  const res = await fetch(`${base}/api/optimize/jobs/${jobId}/summary.xlsx`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows: payloadRows, rule_rows: buildRuleRows(ruleConfig) }),
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
