/**
 * buildTradeExcel.js
 *
 * Shared utility that produces the exact same formatted .xlsx as the
 * ResultsPanel "Export Excel" button, but accepts plain JS arrays and
 * a pre-computed summary dict (instead of React state).
 *
 * Usage:
 *   import buildTradeExcel from './buildTradeExcel';
 *   const blob = await buildTradeExcel(trades, summary, { comboLabel, fromDate, toDate });
 *   // blob is an xlsx Blob ready to be downloaded.
 *
 * trades   — array of objects (keys = CSV column names)
 * summary  — backend summary dict (total_pnl, count, win_pct, …)
 * opts     — { comboLabel?, fromDate?, toDate? }
 */
import ExcelJS from 'exceljs';
import { writeWowMomSheet, buildWowMomTitle } from './wowMomSheet';

// ─── Palette (identical to ResultsPanel) ────────────────────────────────────
const C = {
  navyBg:    { argb: 'FF1F3864' },
  navyText:  { argb: 'FFFFFFFF' },
  sectionBg: { argb: 'FF2C5F8A' },
  sectionTx: { argb: 'FFFFFFFF' },
  headerBg:  { argb: 'FF34495E' },
  headerTx:  { argb: 'FFFFFFFF' },
  subHdrBg:  { argb: 'FFD6E4F7' },
  subHdrTx:  { argb: 'FF1F3864' },
  greenBg:   { argb: 'FFD4EFDF' },
  greenTx:   { argb: 'FF1E7E34' },
  redBg:     { argb: 'FFFDE8E8' },
  redTx:     { argb: 'FFC0392B' },
  labelBg:   { argb: 'FFF2F6FA' },
  altRow:    { argb: 'FFF9FBFD' },
  border:    { argb: 'FFB0C4D8' },
  white:     { argb: 'FFFFFFFF' },
};

const thinBorder = (color = C.border) => ({
  top:    { style: 'thin', color },
  left:   { style: 'thin', color },
  bottom: { style: 'thin', color },
  right:  { style: 'thin', color },
});
const boldFont  = (sz = 11, color = { argb: 'FF000000' }) => ({ bold: true,  size: sz, color, name: 'Calibri' });
const normFont  = (sz = 10, color = { argb: 'FF000000' }) => ({ bold: false, size: sz, color, name: 'Calibri' });
const centerAlign = { horizontal: 'center', vertical: 'middle' };
const leftAlign   = { horizontal: 'left',   vertical: 'middle' };

// ─── Small helpers ───────────────────────────────────────────────────────────

const toNumber = (value) => {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (value == null || value === '') return null;
  const parsed = parseFloat(String(value).replace(/[,%₹\s]/g, ''));
  return Number.isFinite(parsed) ? parsed : null;
};

const pctOfBase = (pnlValue, baseValue) => {
  const pnl  = toNumber(pnlValue);
  const base = toNumber(baseValue);
  if (pnl == null || base == null || base === 0) return '';
  return pnl / base;
};

/** Parse a YYYY-MM-DD (or DD-MM-YYYY / DD/MM/YYYY) date string to UTC ms. */
const parseDateMs = (value) => {
  if (value instanceof Date) return value.getTime();
  if (value == null || value === '') return null;
  const str = String(value).trim();
  const parts = str.includes('/') ? str.split('/') : str.split('-');
  if (parts.length !== 3) return null;
  let year, month, day;
  if (parts[0].length === 4) {
    year  = parseInt(parts[0], 10);
    month = parseInt(parts[1], 10) - 1;
    day   = parseInt(parts[2], 10);
  } else {
    day   = parseInt(parts[0], 10);
    month = parseInt(parts[1], 10) - 1;
    year  = parseInt(parts[2], 10);
  }
  const ms = Date.UTC(year, month, day);
  return Number.isFinite(ms) ? ms : null;
};

/** Convert a YYYY-MM-DD string to an Excel Date object (UTC midnight). */
const toExcelDate = (value) => {
  if (!value) return null;
  const ms = parseDateMs(value);
  if (ms == null) return null;
  return new Date(ms);
};

const isLazyLegRow = (row) => (
  row?.['Is Lazy Leg'] === true ||
  String(row?.['Is Lazy Leg'] || '').toLowerCase() === 'true' ||
  Boolean(row?.['Lazy Leg Name'])
);

const isFutureRow = (row) => String(row?.['Type'] || '').toUpperCase() === 'FUT';
const isOptionRow = (row) => ['CE', 'CALL', 'PE', 'PUT'].includes(String(row?.['Type'] || '').toUpperCase());
const isBuyLeg   = (row) => String(row?.['B/S'] || '').toUpperCase() === 'BUY';
const isSellLeg  = (row) => String(row?.['B/S'] || '').toUpperCase() === 'SELL';

// Bearish leg: profits when market falls  → CE SELL, PE BUY or FUT SELL
// Bullish leg: profits when market rises  → CE BUY,  PE SELL or FUT BUY
const isBearishLeg = (row) => {
  const t  = String(row?.['Type'] || '').toUpperCase();
  const bs = String(row?.['B/S']  || '').toUpperCase();
  return ((t === 'CE' || t === 'CALL') && bs === 'SELL') ||
         ((t === 'PE' || t === 'PUT')  && bs === 'BUY')  ||
         (t === 'FUT' && bs === 'SELL');
};
const isBullishLeg = (row) => {
  const t  = String(row?.['Type'] || '').toUpperCase();
  const bs = String(row?.['B/S']  || '').toUpperCase();
  return ((t === 'CE' || t === 'CALL') && bs === 'BUY')  ||
         ((t === 'PE' || t === 'PUT')  && bs === 'SELL') ||
         (t === 'FUT' && bs === 'BUY');
};

const sumRequired = (rows, key) => {
  let total = 0;
  for (const row of rows) {
    const value = toNumber(row?.[key]);
    if (value == null) return null;
    total += value;
  }
  return total;
};

const roundMae = (value) => Math.round(value * 10000) / 10000;

/** Compute Net MAE 1, Net MAE 2, Final MAE for a group of legs.
 *
 * Every leg (option or future) is classified by market direction:
 *   Bullish (CE BUY / PE SELL / FUT BUY)  — adverse when market falls, favorable when rises.
 *   Bearish (CE SELL / PE BUY / FUT SELL) — adverse when market rises, favorable when falls.
 *
 * Unified rule (single-leg, multi-leg, options and futures alike):
 *   Net MAE 1 = sum(bullish MAE) + sum(bearish MFE)
 *   Net MAE 2 = sum(bullish MFE) + sum(bearish MAE)
 *   Final MAE = min(Net MAE 1, Net MAE 2)                     (single directional leg)
 *   Final MAE = min(Net MAE 1, Net MAE 2, Net P&L %)          (>1 directional leg)
 *
 * When every leg shares one direction this collapses to "all MAE" vs
 * "all MFE"; mixed directions cross automatically. For MULTI-leg trades the
 * realized Net P&L % is folded into the min so the reconstructed combined
 * excursion can never read better than what the trade actually booked
 * (single-leg keeps min(nm1, nm2) — its own MAE already bounds the loss).
 */
const calcTradeMae = (legs, netPnlPct = null) => {
  const dirLegs = legs.filter(r => isOptionRow(r) || isFutureRow(r));
  if (dirLegs.length === 0) return null;

  const allMae = sumRequired(dirLegs, 'MAE');
  const allMfe = sumRequired(dirLegs, 'MFE');
  if ([allMae, allMfe].some(v => v == null)) return null;

  const bullishLegs = dirLegs.filter(isBullishLeg);
  const bearishLegs = dirLegs.filter(isBearishLeg);

  const bullishMae = sumRequired(bullishLegs, 'MAE');
  const bullishMfe = sumRequired(bullishLegs, 'MFE');
  const bearishMae = sumRequired(bearishLegs, 'MAE');
  const bearishMfe = sumRequired(bearishLegs, 'MFE');
  if ([bullishMae, bullishMfe, bearishMae, bearishMfe].some(v => v == null)) return null;

  const netMae1 = bullishMae + bearishMfe;
  const netMae2 = bullishMfe + bearishMae;
  const finalMae = (dirLegs.length > 1 && Number.isFinite(netPnlPct))
    ? Math.min(netMae1, netMae2, netPnlPct)
    : Math.min(netMae1, netMae2);
  return { netMae1: roundMae(netMae1), netMae2: roundMae(netMae2), finalMae: roundMae(finalMae) };
};

const getReEntryType = (trade) => {
  if (isLazyLegRow(trade)) return 'Lazy';
  const mode    = String(trade?.['ReEntryMode']    || '').trim();
  if (mode)    return mode;
  const trigger = String(trade?.['ReEntryTrigger'] || '').trim();
  if (trigger) return trigger;
  return trade?.['ReEntryIndex'] ? 'Re-Entry' : '';
};

// ─── Main export ─────────────────────────────────────────────────────────────

/**
 * @param {Object[]} trades   — array of trade row objects (keys = CSV columns)
 * @param {Object}   summary  — backend summary dict
 * @param {Object}   opts
 * @param {string}   [opts.comboLabel]  — label shown in the subtitle row
 * @param {string}   [opts.fromDate]    — date range start (display only)
 * @param {string}   [opts.toDate]      — date range end (display only)
 * @returns {Promise<Blob>}
 */
export default async function buildTradeExcel(trades, summary, opts = {}) {
  const { comboLabel = '', fromDate = '', toDate = '', runConfig = null, comboValues = {}, midcap = null, filterName = '', patchwise = false, filterSegments = null } = opts;
  const sourceTrades = trades || [];

  // ── Midcap cross-index overlay (additive — present only when the caller passes
  // opts.midcap = {available, byTrade, summary, legs}, fetched from the SAME
  // /api/midcap-overlay native engine the backtest uses). Mirrors exportToCSV.
  const midcapByTrade  = midcap?.byTrade || {};
  const midcapSummaryO = midcap?.summary || null;
  const midcapLegs     = midcap?.legs || [];
  const hasMidcap      = Boolean(midcap?.available) && Object.keys(midcapByTrade).length > 0;
  const MIDCAP_COLS = [
    'Midcap Entry Spot', 'Midcap Exit Spot', 'Midcap Spot P&L', 'Midcap Spot P&L %',
    'Midcap No Of Days', 'Midcap Rollover Cost %', 'Midcap Hypo P&L', 'Midcap Hypo P&L %',
    'Midcap MAE', 'Midcap MFE',
    'Combined Net P&L', 'Combined Net P&L %', 'Combined Cumulative', 'Combined Peak',
    'Combined DD', 'Combined %DD', 'Combined Net MAE 1', 'Combined Net MAE 2',
    'Combined Final MAE', 'Combined Lowest NAV', 'Combined Actual Live DD',
  ];
  // Combined Net MAE: NIFTY MAE/MFE are already % of spot (summed directly),
  // paired with Midcap MAE/MFE. Net MAE 1 = Midcap MFE + NIFTY MAE; Net MAE 2 =
  // Midcap MAE + NIFTY MFE. (Final floor with Combined Net P&L % done in chain.)
  const calcCombinedFinalMaePct = (legs, mc) => {
    let niftyMae = 0, niftyMfe = 0;
    const dirLegs = (legs || []).filter(r => isOptionRow(r) || isFutureRow(r));
    for (const r of dirLegs) {
      const mae = toNumber(r['MAE']); const mfe = toNumber(r['MFE']);
      if (mae == null || mfe == null) return null;
      niftyMae += mae; niftyMfe += mfe;
    }
    const midMae = mc ? (toNumber(mc['Midcap MAE']) || 0) : 0;
    const midMfe = mc ? (toNumber(mc['Midcap MFE']) || 0) : 0;
    const netMae1 = midMfe + niftyMae;
    const netMae2 = midMae + niftyMfe;
    return { netMae1: roundMae(netMae1), netMae2: roundMae(netMae2), finalMae: roundMae(Math.min(netMae1, netMae2)) };
  };

  // ── Column detection ────────────────────────────────────────────────────────
  const hasCalls   = sourceTrades.some(t => ['CE', 'CALL'].includes((t['Type'] || '').toUpperCase()));
  const hasPuts    = sourceTrades.some(t => ['PE', 'PUT'].includes((t['Type'] || '').toUpperCase()));
  const hasFutures = sourceTrades.some(t => (t['Type'] || '').toUpperCase() === 'FUT');
  const hasBuffer  = sourceTrades.some(t =>
    t['buffer_ref_price'] != null &&
    t['buffer_ref_price'] !== '' &&
    t['buffer_ref_price'] !== 'False' &&
    t['buffer_ref_price'] !== false
  );
  const hasSpotAdj = sourceTrades.some(t =>
    t['Raw Entry Price'] != null &&
    t['Raw Entry Price'] !== '' &&
    +t['Raw Entry Price'] !== +t['Entry Price']
  );
  const hasReEntry = sourceTrades.some(t =>
    t['ReEntryIndex'] || t['ReEntryTrigger'] || t['ReEntryMode'] || isLazyLegRow(t)
  );
  const hasStrikeShift = sourceTrades.some(t => Boolean(t['Strike Shift Reason']));
  const hasStr          = sourceTrades.some(t => t['STR Segment']     && t['STR Segment']     !== '');
  const hasFilterSeg    = sourceTrades.some(t => t['Filter Segment'] && t['Filter Segment'] !== '');

  // ── Sort and group trades ──────────────────────────────────────────────────
  // Primary sort: Entry Date so cascade re-entries (which have NEW higher
  // trade IDs but earlier entry dates than later originals) appear right
  // after their parent trade in chronological order.  Trade/Leg are
  // tiebreakers so legs of the same trade stay grouped together.
  const parseTradeDate = (raw) => {
    if (!raw) return Infinity;
    if (raw instanceof Date) return raw.getTime();
    const s = String(raw).trim();
    // Accept DD-MM-YYYY, DD/MM/YYYY, YYYY-MM-DD
    const m1 = s.match(/^(\d{1,2})[-/](\d{1,2})[-/](\d{4})/);
    if (m1) {
      const [, d, mo, y] = m1;
      return Date.UTC(+y, +mo - 1, +d);
    }
    const m2 = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
    if (m2) {
      const [, y, mo, d] = m2;
      return Date.UTC(+y, +mo - 1, +d);
    }
    const t = Date.parse(s);
    return Number.isNaN(t) ? Infinity : t;
  };
  const sortedTrades = [...sourceTrades].sort((a, b) => {
    const dA = parseTradeDate(a['Entry Date'] || a.entry_date);
    const dB = parseTradeDate(b['Entry Date'] || b.entry_date);
    if (dA !== dB) return dA - dB;
    const tA = parseInt(a.Trade || a.trade || 1, 10);
    const tB = parseInt(b.Trade || b.trade || 1, 10);
    if (tA !== tB) return tA - tB;
    return parseInt(a.Leg || a.leg || 1, 10) - parseInt(b.Leg || b.leg || 1, 10);
  });

  const groupedByTrade = {};
  sortedTrades.forEach(t => {
    const k = String(t.Trade || t.trade || 1);
    if (!groupedByTrade[k]) groupedByTrade[k] = [];
    groupedByTrade[k].push(t);
  });

  // Patchwise reset boundaries — shared by the per-trade combined chain, the Max DD
  // scan, and the outlier Live DD scan so they all reset at the SAME points. Prefer
  // the uploaded filter's segment START dates (reset when a trade's entry crosses
  // into a new segment) so spot-adjustment runs reset too (they never emit a
  // FILTER_END exit reason). Falls back to FILTER_END when no segments are passed.
  const _pwSegStarts = (Array.isArray(filterSegments) ? filterSegments : [])
    .map(s => parseDateMs(s && (s.start || s.Start || s.from || s.start_date || s.startdt)))
    .filter(ms => Number.isFinite(ms)).sort((a, b) => a - b);
  const _pwSegIdxByKey = (key) => {
    const lg = groupedByTrade[key] || [];
    const mr = lg.find(l => !l['ReEntryIndex'] && !l['ReEntryTrigger'] && !l['ReEntryMode'] && !isLazyLegRow(l)) || lg[0] || {};
    const em = parseDateMs(mr['Entry Date']); let i = -1;
    for (let j = 0; j < _pwSegStarts.length; j++) { if (_pwSegStarts[j] <= em) i = j; else break; }
    return i;
  };

  // ── Per-trade aggregates ────────────────────────────────────────────────────
  const TRADE_COLS = new Set([
    'Net MAE 1', 'Net MAE 2', 'Final MAE',
    'Net P&L', '% P&L', 'Cumulative', 'Peak', 'DD', '%DD',
    'Lowest NAV', 'Actual Live DD',
    ...(hasMidcap ? MIDCAP_COLS : []),
  ]);

  const tm = {};
  Object.entries(groupedByTrade).forEach(([k, legs]) => {
    const mainRow = legs.find(l =>
      !l['ReEntryIndex'] && !l['ReEntryTrigger'] && !l['ReEntryMode'] && !isLazyLegRow(l)
    ) || legs[0];
    const spot   = parseFloat(mainRow?.['Entry Spot']) || 0;
    const rawNet = mainRow?.['Net P&L'];
    const net    = Number.isFinite(typeof rawNet === 'number' ? rawNet : parseFloat(rawNet))
      ? (typeof rawNet === 'number' ? rawNet : parseFloat(rawNet))
      : legs.reduce((s, l) =>
          s + (parseFloat(l['CE P&L']) || 0) + (parseFloat(l['PE P&L']) || 0) + (parseFloat(l['FUT P&L']) || 0),
        0);
    const pct        = spot > 0 ? (net / spot) * 100 : 0;
    const tradeMae   = calcTradeMae(legs, pct);
    tm[k] = {
      net,
      pct,
      netMae1:    tradeMae?.netMae1  ?? '',
      netMae2:    tradeMae?.netMae2  ?? '',
      finalMae:   tradeMae?.finalMae ?? '',
      cumulative: '',
      peak:       '',
      dd:         '',
      pctDd:      '',
      midcap:     hasMidcap ? (midcapByTrade[k] || null) : null,
      exitReason: mainRow?.['Exit Reason'] || '',
    };
  });

  {
    // Iterate trades in CHRONOLOGICAL order (not trade_id order) so cascade
    // re-entries — which have higher trade IDs but earlier entry dates than
    // later originals — are processed in time sequence.  Use sortedTrades
    // (already sorted by Entry Date earlier) and extract trade IDs in
    // first-appearance order.
    const _seenTm = new Set();
    const sortedTmKeys = [];
    sortedTrades.forEach(t => {
      const k = String(t.Trade || t.trade || 1);
      if (!_seenTm.has(k)) {
        _seenTm.add(k);
        if (tm[k]) sortedTmKeys.push(k);
      }
    });

    // Recompute the booked equity curve exactly like the research-sheet
    // formulas: cumulative compounds from prior visible trade rows using
    // `% P&L`; peak is the running max; DD is blank at equity highs; %DD is
    // the Excel ratio DD / Peak.
    let cumulative = 100;
    let peak = 100;
    let _prevPwKey = null;
    sortedTmKeys.forEach(k => {
      if (patchwise && !hasMidcap && _prevPwKey !== null) {
        const newPatch = _pwSegStarts.length
          ? (_pwSegIdxByKey(k) !== _pwSegIdxByKey(_prevPwKey))
          : ((tm[_prevPwKey].exitReason || '').toUpperCase().split('+').includes('FILTER_END'));
        if (newPatch) { cumulative = 100; peak = 100; }
      }
      _prevPwKey = k;
      const t = tm[k];
      const pct = Number.isFinite(t.pct) ? t.pct : 0;
      cumulative *= (1 + pct / 100);
      peak = Math.max(peak, cumulative);
      // At equity highs, drawdown is 0 (not blank).  Previously this used ''
      // which left empty cells in the DD column for every winning streak row.
      const dd = peak > cumulative ? cumulative - peak : 0;
      t.cumulative = cumulative;
      t.peak = peak;
      t.dd = dd;
      t.pctDd = peak !== 0 ? (dd / peak) * 100 : 0;
    });

    // Lowest NAV and Actual Live DD
    // Excel formula: AS2=AN2 (first trade), AS_n=AN_(n-1)*(1+AR_n%) thereafter
    let prevCum = 100;
    let prevPeak = 100;
    let firstTradeDone = false;
    let _prevPwKeyLN = null;
    sortedTmKeys.forEach(k => {
      if (patchwise && !hasMidcap && _prevPwKeyLN !== null) {
        const newPatch = _pwSegStarts.length
          ? (_pwSegIdxByKey(k) !== _pwSegIdxByKey(_prevPwKeyLN))
          : ((tm[_prevPwKeyLN].exitReason || '').toUpperCase().split('+').includes('FILTER_END'));
        if (newPatch) { prevCum = 100; prevPeak = 100; }
      }
      _prevPwKeyLN = k;
      const t    = tm[k];
      const mae  = (t.finalMae  !== '' && t.finalMae  != null) ? t.finalMae  : null;
      const peak = (t.peak      !== '' && t.peak      != null) ? t.peak      : null;
      const cum  = (t.cumulative !== '' && t.cumulative != null) ? t.cumulative : null;
      if (mae != null && peak != null && prevPeak !== 0) {
        // Research-team formula (revised): EVERY trade — including the first,
        // where prevCum = 100 — anchors the low to prev_cumulative * (1 + Final
        // MAE_N / 100):  AW = AU_prev * (1 + AM%). Live DD divides by the
        // PREVIOUS trade's peak (AV_prev), not this trade's peak.
        // Store full float precision so the chain doesn't accumulate rounding
        // error — Excel numFmt '#,##0.00' on the cell handles 2-decimal display.
        const lowestNav = prevCum * (1 + mae / 100);
        const actualLiveDD = (lowestNav / prevPeak - 1) * 100;
        t.lowestNav    = lowestNav;
        t.actualLiveDD = actualLiveDD;
        firstTradeDone = true;
      } else {
        t.lowestNav    = '';
        t.actualLiveDD = '';
        firstTradeDone = true;
      }
      if (cum != null) prevCum = cum;
      if (peak != null) prevPeak = peak;
    });

    // Combined NAV / Peak / DD / Net MAE / Lowest NAV chain (Midcap only).
    // Final MAE = min(Net MAE 1, Net MAE 2, Combined Net P&L %) per the backtest.
    if (hasMidcap) {
      let nav = 100, peak = 100, prevNav = 100, prevPeak = 100, firstDone = false;
      sortedTmKeys.forEach((k, idx) => {
        if (patchwise && idx > 0) {
          const prevKey = sortedTmKeys[idx - 1];
          const newPatch = _pwSegStarts.length
            ? (_pwSegIdxByKey(k) !== _pwSegIdxByKey(prevKey))
            : ((tm[prevKey].exitReason || '').toUpperCase().split('+').includes('FILTER_END'));
          if (newPatch) { nav = 100; peak = 100; prevNav = 100; prevPeak = 100; }
        }
        const t = tm[k];
        const mc = t.midcap;
        const cpct = mc ? Number(mc['Combined Net P&L %']) : NaN;
        if (Number.isFinite(cpct)) {
          prevNav = nav;
          prevPeak = peak;
          nav = nav * (1 + cpct / 100);
          peak = Math.max(peak, nav);
          t.combinedPct = cpct;
          t.combinedCum = Number(nav.toFixed(4));
          t.combinedPeak = Number(peak.toFixed(4));
          t.combinedDd = Number((nav - peak).toFixed(4));
          t.combinedPctDd = peak !== 0 ? Number(((nav / peak - 1) * 100).toFixed(4)) : '';
          const cm = calcCombinedFinalMaePct(groupedByTrade[k], mc);
          if (cm) {
            const fmae = Number(Math.min(cm.netMae1, cm.netMae2, cpct).toFixed(4));
            t.combinedNetMae1 = cm.netMae1;
            t.combinedNetMae2 = cm.netMae2;
            t.combinedFinalMae = fmae;
            // Revised rule: every trade (incl. first, prevNav = 100) anchors the
            // low to prevNav * (1 + FinalMAE%) — AW = AU_prev * (1 + AM%).
            const lowestNav = prevNav * (1 + fmae / 100);
            t.combinedLowestNav = Number(lowestNav.toFixed(4));
            // Live DD divides by the PREVIOUS trade's peak (AV_prev), not this
            // trade's peak — AX = AW / AV_prev - 1.
            t.combinedActualLiveDd = prevPeak !== 0 ? Number(((lowestNav / prevPeak - 1) * 100).toFixed(4)) : '';
          } else {
            t.combinedNetMae1 = t.combinedNetMae2 = ''; t.combinedFinalMae = '';
            t.combinedLowestNav = ''; t.combinedActualLiveDd = '';
          }
          firstDone = true;
        } else {
          t.combinedPct = null;
          t.combinedCum = t.combinedPeak = t.combinedDd = t.combinedPctDd = '';
          t.combinedNetMae1 = t.combinedNetMae2 = ''; t.combinedFinalMae = '';
          t.combinedLowestNav = ''; t.combinedActualLiveDd = '';
        }
      });
    }
  }

  // ── Key order ───────────────────────────────────────────────────────────────
  const keyOrder = [
    'Trade', 'Leg', 'Index', 'Entry Date', 'Exit Date', 'Expiry',
    'Entry Spot', 'Exit Spot', 'Spot P&L', 'Spot P&L %',
    'Type', 'Strike',
    ...(hasBuffer   ? ['buffer_ref_price', 'buffer_strike_offset'] : []),
    'B/S',
    ...(hasReEntry  ? ['Re-Entry Type'] : []),
    'Qty',
    ...(hasSpotAdj  ? ['Raw Entry Price'] : []),
    'Entry Price',
    ...(hasSpotAdj  ? ['Raw Exit Price'] : []),
    'Exit Price', 'MAE', 'MFE',
    ...(hasMidcap ? [] : ['Net MAE 1', 'Net MAE 2', 'Final MAE']),
    ...(hasCalls    ? ['CE P&L', 'CE P&L %']  : []),
    ...(hasPuts     ? ['PE P&L', 'PE P&L %']  : []),
    ...(hasFutures  ? ['FUT P&L'] : []),
    ...(hasMidcap ? [] : ['Net P&L', '% P&L', 'Cumulative', 'Peak', 'DD', '%DD', 'Lowest NAV', 'Actual Live DD']),
    ...(hasMidcap ? MIDCAP_COLS : []),
    'Exit Reason',
    ...(hasStrikeShift ? ['Strike Shift Reason'] : []),
    ...(hasStr       ? ['STR Segment']     : []),
    ...(hasFilterSeg ? ['Filter Segment']  : []),
  ];

  // ── Build cleaned trade rows ────────────────────────────────────────────────
  const DATE_COLS  = new Set(['Entry Date', 'Exit Date', 'Expiry', 'Leg Exit Date', 'Lazy Entry Date', 'Lazy Exit Date']);
  const TRUE_PCT_COLS = new Set(['Spot P&L %', 'CE P&L %', 'PE P&L %']);
  const PCT_APPEND_COLS = new Set(['%DD', 'Combined %DD']);
  const MAE_COLS   = new Set(['MAE', 'MFE', 'Net MAE 1', 'Net MAE 2', 'Final MAE',
    'Midcap MAE', 'Midcap MFE', 'Combined Net MAE 1', 'Combined Net MAE 2', 'Combined Final MAE']);

  // Build engine_tid → sequential display number (1, 2, 3, ...) based on
  // FIRST appearance in chronological order.  This is what the "Index"
  // column shows the user, so cascade trades (engine_tid=71+) get the
  // correct sequential number for their chronological position
  // (e.g. Trade=71 enters right after Trade=5, so its display Index is 6).
  const _tidToIndexNo = {};
  let _seqNo = 0;
  for (const _t of sortedTrades) {
    const _ek = String(_t.Trade || _t.trade || 1);
    if (!(_ek in _tidToIndexNo)) {
      _seqNo += 1;
      _tidToIndexNo[_ek] = _seqNo;
    }
  }

  const written = new Set();
  const cleanedTrades = sortedTrades.map(trade => {
    const k     = String(trade.Trade || trade.trade || 1);
    const first = !written.has(k);
    if (first) written.add(k);
    const m   = tm[k] || {};
    const mc  = hasMidcap ? (m.midcap || null) : null;
    const row = {};

    for (const key of keyOrder) {
      let val;
      if (TRADE_COLS.has(key)) {
        if (!first) { val = ''; }
        else if (key === 'Net MAE 1')      val = m.netMae1;
        else if (key === 'Net MAE 2')      val = m.netMae2;
        else if (key === 'Final MAE')      val = m.finalMae;
        else if (key === 'Net P&L')        val = m.net;
        else if (key === '% P&L')          val = m.pct;
        else if (key === 'Cumulative')     val = m.cumulative;
        else if (key === 'Peak')           val = m.peak;
        else if (key === 'DD')             val = m.dd;
        else if (key === '%DD')            val = m.pctDd;
        else if (key === 'Lowest NAV')     val = m.lowestNav;
        else if (key === 'Actual Live DD') val = m.actualLiveDD;
        else if (key === 'Midcap Hypo P&L')   val = mc ? (mc['Midcap Leg P&L'] ?? '') : '';
        else if (key === 'Midcap Hypo P&L %') val = mc ? (mc['Midcap Leg P&L %'] ?? '') : '';
        else if (key === 'Combined Cumulative')     val = m.combinedCum;
        else if (key === 'Combined Peak')           val = m.combinedPeak;
        else if (key === 'Combined DD')             val = m.combinedDd;
        else if (key === 'Combined %DD')            val = m.combinedPctDd;
        else if (key === 'Combined Net MAE 1')      val = m.combinedNetMae1;
        else if (key === 'Combined Net MAE 2')      val = m.combinedNetMae2;
        else if (key === 'Combined Final MAE')      val = m.combinedFinalMae;
        else if (key === 'Combined Lowest NAV')     val = m.combinedLowestNav;
        else if (key === 'Combined Actual Live DD') val = m.combinedActualLiveDd;
        else if (MIDCAP_COLS.includes(key))         val = mc ? (mc[key] ?? '') : '';
      } else if (key === 'Leg' && isLazyLegRow(trade)) {
        val = trade['Lazy Leg Name'] || trade[key];
      } else if (key === 'Re-Entry Type') {
        val = getReEntryType(trade);
      } else if (key === 'Trade') {
        // Show user-friendly sequential trade number instead of the engine's
        // internal trade_id (which jumps to 71+ for cascade mini-trades).
        // Same value as the "Index" column.
        val = _tidToIndexNo[k] ?? parseInt(trade.Trade || trade.trade || 1, 10);
      } else if (key === 'Index') {
        val = _tidToIndexNo[k] ?? parseInt(trade.Trade || trade.trade || 1, 10);
      } else if (key === 'Spot P&L %') {
        // Spot P&L is a trade-level quantity written only on Leg 1.  Leave
        // Spot P&L % blank on Leg 2+ rows (matches Net P&L convention) so
        // column SUM(W) yields the trade-level total without double-counting.
        const spotPnl = trade['Spot P&L'];
        val = (toNumber(spotPnl) == null) ? '' : pctOfBase(spotPnl, trade['Entry Spot']);
      } else if (key === 'CE P&L %') {
        val = pctOfBase(trade['CE P&L'], trade['Entry Spot']);
      } else if (key === 'PE P&L %') {
        val = pctOfBase(trade['PE P&L'], trade['Entry Spot']);
      } else {
        val = trade[key];
      }

      if (val == null || (typeof val === 'number' && isNaN(val)) || val === 'NaN') val = '';
      row[key] = val;
    }
    // With a Midcap leg the NIFTY trade-level Net P&L / % P&L / Cumulative are
    // dropped from the sheet but the Summary still needs them (ROI gating);
    // attach as hidden first-row props (not in keyOrder → not written as columns).
    if (hasMidcap && first) {
      if (row['Net P&L'] === undefined)    row['Net P&L']    = m.net ?? '';
      if (row['% P&L'] === undefined)      row['% P&L']      = m.pct ?? '';
      if (row['Cumulative'] === undefined) row['Cumulative'] = m.cumulative ?? '';
    }
    return row;
  });

  // ════════════════════════════════════════════════════════════════════════════
  // BUILD WORKBOOK
  // ════════════════════════════════════════════════════════════════════════════
  const wb = new ExcelJS.Workbook();
  wb.creator  = 'AlgoTest Backtest';
  wb.created  = new Date();
  wb.calcProperties = { fullCalcOnLoad: true };

  // ════════════════════════════════════════════════════════════════════════════
  // SHEET 1 — TRADE SHEET
  // ════════════════════════════════════════════════════════════════════════════
  const ws1 = wb.addWorksheet('Trade Sheet', { views: [{ state: 'frozen', ySplit: 1 }] });

  const colWidths = {
    'Leg': 12, 'Entry Date': 13, 'Exit Date': 13,
    'Entry Spot': 12, 'Exit Spot': 12,
    'buffer_ref_price': 12, 'buffer_strike_offset': 10,
    'Re-Entry Type': 14,
    'Raw Entry Price': 12, 'Entry Price': 12,
    'Raw Exit Price': 12, 'Exit Price': 12,
    'MAE': 9, 'MFE': 9, 'Net MAE 1': 10, 'Net MAE 2': 10, 'Final MAE': 10,
    'Net P&L': 10, '% P&L': 8, 'Cumulative': 11, 'Peak': 10, 'DD': 9, '%DD': 8,
    'Lowest NAV': 13, 'Actual Live DD': 15,
    'Spot P&L %': 10, 'CE P&L %': 10, 'PE P&L %': 10,
    'Exit Reason': 14, 'Strike Shift Reason': 40, 'Expiry': 12, 'STR Segment': 14, 'Filter Segment': 22,
    'Midcap Entry Spot': 15, 'Midcap Exit Spot': 15, 'Midcap Spot P&L': 14, 'Midcap Spot P&L %': 15,
    'Midcap No Of Days': 15, 'Midcap Rollover Cost %': 18, 'Midcap Hypo P&L': 15, 'Midcap Hypo P&L %': 16,
    'Midcap MAE': 12, 'Midcap MFE': 12,
    'Combined Net P&L': 15, 'Combined Net P&L %': 16, 'Combined Cumulative': 17, 'Combined Peak': 13,
    'Combined DD': 12, 'Combined %DD': 12, 'Combined Net MAE 1': 16, 'Combined Net MAE 2': 16,
    'Combined Final MAE': 15, 'Combined Lowest NAV': 16, 'Combined Actual Live DD': 18,
  };

  ws1.columns = keyOrder.map(k => ({ key: k, width: colWidths[k] || 10 }));

  // Header row
  const headerRow = ws1.addRow(keyOrder);
  headerRow.eachCell(cell => {
    cell.font      = boldFont(10, C.navyText);
    cell.fill      = { type: 'pattern', pattern: 'solid', fgColor: C.headerBg };
    cell.alignment = centerAlign;
    cell.border    = thinBorder();
  });
  headerRow.height = 22;

  // Data rows
  cleanedTrades.forEach((row, i) => {
    const r   = ws1.addRow(keyOrder.map(k => row[k] ?? ''));
    const net = typeof row['Net P&L'] === 'number' ? row['Net P&L'] : null;
    const bg  = i % 2 === 0 ? C.white : C.altRow;

    r.eachCell((cell, colNum) => {
      cell.font   = normFont(10);
      cell.fill   = { type: 'pattern', pattern: 'solid', fgColor: bg };
      cell.border = thinBorder();
      cell.alignment = { vertical: 'middle' };

      const colKey = keyOrder[colNum - 1];

      // Convert date strings to Excel Date objects
      if (DATE_COLS.has(colKey) && typeof cell.value === 'string' && cell.value !== '') {
        const d = toExcelDate(cell.value);
        if (d) { cell.value = d; cell.numFmt = 'DD-MMM-YYYY'; }
      } else if (typeof cell.value === 'string' && cell.value !== '') {
        const n = Number(cell.value);
        if (!isNaN(n)) cell.value = n;
      }

      if (typeof cell.value === 'number') {
        cell.numFmt = PCT_APPEND_COLS.has(colKey)
          ? '0.00"%"'
          : TRUE_PCT_COLS.has(colKey)
          ? '0.00%'
          : MAE_COLS.has(colKey)
          ? '#,##0.0000'
          : (Number.isInteger(cell.value) ? '0' : '#,##0.00');
      }
    });

    // Color Net P&L and % P&L (NIFTY-only sheet)
    if (net !== null) {
      const col1 = keyOrder.indexOf('Net P&L') + 1;
      const col2 = keyOrder.indexOf('% P&L')   + 1;
      [col1, col2].filter(c => c > 0).forEach(c => {
        const cell = r.getCell(c);
        cell.font = boldFont(10, net >= 0 ? C.greenTx : C.redTx);
        cell.fill = { type: 'pattern', pattern: 'solid', fgColor: net >= 0 ? C.greenBg : C.redBg };
      });
    }
    // Color Combined Net P&L / % (Midcap sheet)
    if (hasMidcap) {
      const cNet = typeof row['Combined Net P&L'] === 'number' ? row['Combined Net P&L'] : null;
      if (cNet !== null) {
        const c1 = keyOrder.indexOf('Combined Net P&L') + 1;
        const c2 = keyOrder.indexOf('Combined Net P&L %') + 1;
        [c1, c2].filter(c => c > 0).forEach(c => {
          const cell = r.getCell(c);
          cell.font = boldFont(10, cNet >= 0 ? C.greenTx : C.redTx);
          cell.fill = { type: 'pattern', pattern: 'solid', fgColor: cNet >= 0 ? C.greenBg : C.redBg };
        });
      }
    }
  });

  // ════════════════════════════════════════════════════════════════════════════
  // SHEET 2 — SUMMARY  (mirrors ResultsPanel.jsx exportToCSV exactly)
  // ════════════════════════════════════════════════════════════════════════════

  // ── Compute all stats from cleanedTrades (same formulas as ResultsPanel) ──
  const S = summary || {};
  const _fmtPct      = (v, signed = true) => `${signed && v >= 0 ? '+' : ''}${(+v).toFixed(2)}%`;
  const _fmtCurrency = (v) => `₹${(+v).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;

  let _sumPctJS = 0, _sumPosPctJS = 0, _sumNegPctJS = 0;
  let _winCntJS = 0, _lossCntJS = 0, _totalCntJS = 0;
  let _sumNetJS = 0, _maxNetJS = -Infinity, _minNetJS = Infinity;
  let _finalCumJS = 100, _spotCumJS = 100;
  let _minEntryMs = null, _maxExitMs = null;
  let _spotSumGatedJS = 0;
  let _ceSumJS = 0, _peSumJS = 0, _futSumJS = 0;
  let _cePctJS = 0, _pePctJS = 0, _spotPctJS = 0;

  const _parseDate2 = (s) => {
    if (s instanceof Date) return s.getTime();
    if (typeof s !== 'string' || !s) return null;
    const parts = s.includes('/') ? s.split('/') : s.split('-');
    if (parts.length !== 3) return null;
    let y, m, d;
    if (parts[0].length === 4) { y = +parts[0]; m = +parts[1] - 1; d = +parts[2]; }
    else                       { d = +parts[0]; m = +parts[1] - 1; y = +parts[2]; }
    const t = Date.UTC(y, m, d);
    return Number.isFinite(t) ? t : null;
  };

  // Per-type P&L from ALL leg rows (not just first-leg)
  for (const t of cleanedTrades) {
    const ce = toNumber(t['CE P&L']);  if (ce  !== null) _ceSumJS  += ce;
    const pe = toNumber(t['PE P&L']);  if (pe  !== null) _peSumJS  += pe;
    const fu = toNumber(t['FUT P&L']); if (fu  !== null) _futSumJS += fu;
    const cep = toNumber(t['CE P&L %']); if (cep !== null) _cePctJS += cep;
    const pep = toNumber(t['PE P&L %']); if (pep !== null) _pePctJS += pep;
    const spp = toNumber(t['Spot P&L %']); if (spp !== null) _spotPctJS += spp;
  }

  // Trade-level stats only from first-leg rows (those with a numeric Net P&L).
  // With a Midcap leg, ALL Performance Overview stats run on the COMBINED P&L.
  const _gp = (t) => (hasMidcap ? t['Combined Net P&L %'] : t['% P&L']);
  const _gn = (t) => (hasMidcap ? t['Combined Net P&L']   : t['Net P&L']);
  const _gc = (t) => (hasMidcap ? t['Combined Cumulative'] : t['Cumulative']);
  for (const t of cleanedTrades) {
    const p = _gp(t); const n = _gn(t);
    if (typeof p === 'number' && Number.isFinite(p)) {
      _sumPctJS += p; _totalCntJS++;
      if (p > 0) { _sumPosPctJS += p; _winCntJS++; }
      else if (p < 0) { _sumNegPctJS += p; _lossCntJS++; }
    }
    if (typeof n === 'number' && Number.isFinite(n)) {
      _sumNetJS += n;
      if (n > _maxNetJS) _maxNetJS = n;
      if (n < _minNetJS) _minNetJS = n;
      const sp = toNumber(t['Spot P&L']); if (sp !== null) _spotSumGatedJS += sp;
    }
    const cum = _gc(t);
    if (typeof cum === 'number' && Number.isFinite(cum)) _finalCumJS = cum;
    const eS = toNumber(t['Entry Spot']), xS = toNumber(t['Exit Spot']);
    if (typeof n === 'number' && Number.isFinite(n) && eS !== null && xS !== null && eS > 0) {
      _spotCumJS *= (xS / eS);
    }
    const eD = _parseDate2(String(t['Entry Date'] || ''));
    const xD = _parseDate2(String(t['Exit Date']  || ''));
    if (eD != null && (_minEntryMs == null || eD < _minEntryMs)) _minEntryMs = eD;
    if (xD != null && (_maxExitMs  == null || xD > _maxExitMs))  _maxExitMs  = xD;
  }
  if (!Number.isFinite(_maxNetJS)) _maxNetJS = 0;
  if (!Number.isFinite(_minNetJS)) _minNetJS = 0;

  const _avgWinPctJS  = _winCntJS  > 0 ? (_sumPosPctJS / _winCntJS)  : 0;
  const _avgLossPctJS = _lossCntJS > 0 ? (_sumNegPctJS / _lossCntJS) : 0;
  const _winRateJS    = _totalCntJS > 0 ? (_winCntJS  / _totalCntJS) * 100 : 0;
  const _lossRateJS   = _totalCntJS > 0 ? (_lossCntJS / _totalCntJS) * 100 : 0;
  const _avgNetJS     = _totalCntJS > 0 ? (_sumNetJS  / _totalCntJS) : 0;
  const _expectancyJS = _avgLossPctJS !== 0
    ? ((_winRateJS / 100) * _avgWinPctJS / Math.abs(_avgLossPctJS) - (1 - _winRateJS / 100))
    : 0;
  const _yearsJS = (_minEntryMs != null && _maxExitMs != null)
    ? (_maxExitMs - _minEntryMs) / (365.25 * 86400000) : 0;
  const _optCagrPctJS  = _yearsJS > 0 && _finalCumJS > 0
    ? (Math.pow(_finalCumJS / 100, 1 / _yearsJS) - 1) * 100 : 0;
  const _spotCagrPctJS = _yearsJS > 0 && _spotCumJS > 0
    ? (Math.pow(_spotCumJS  / 100, 1 / _yearsJS) - 1) * 100 : 0;
  // Max DD / streaks / DD-period: from the COMBINED NAV with a Midcap leg
  // (the NIFTY S.max_dd_pct is wrong); otherwise the backend summary (unchanged).
  // Max Drawdown = the single worst %DD on the equity curve — Combined NAV with a
  // Midcap leg, NIFTY NAV otherwise — i.e. min over trades of (Cumulative/Peak-1)*100.
  // Read straight from Cumulative/Peak (units-safe) so the Summary == the per-trade
  // %DD column, both overall and patchwise, for midcap AND non-midcap alike.
  let _maxDDPctJS, _maxWinStreakJS, _maxLossStreakJS, mddStartDate, mddEndDate, mddDuration;
  {
    const cumKey  = hasMidcap ? 'Combined Cumulative' : 'Cumulative';
    const peakKey = hasMidcap ? 'Combined Peak'       : 'Peak';
    const pctKey  = hasMidcap ? 'Combined Net P&L %'  : '% P&L';
    let peakMs = null, worstDD = 0, worstPeakMs = null, worstTroughMs = null;
    let winRun = 0, lossRun = 0, mxWin = 0, mxLoss = 0;
    for (const t of cleanedTrades) {
      const pct = t[pctKey];
      if (typeof pct === 'number' && Number.isFinite(pct)) {
        if (pct > 0) { winRun++; lossRun = 0; if (winRun > mxWin) mxWin = winRun; }
        else if (pct < 0) { lossRun++; winRun = 0; if (lossRun > mxLoss) mxLoss = lossRun; }
      }
      const cum = t[cumKey];
      const pk = t[peakKey];
      const xD = _parseDate2(String(t['Exit Date'] || ''));
      if (typeof cum === 'number' && typeof pk === 'number' && Number.isFinite(cum) && pk !== 0) {
        if (cum >= pk - 1e-9) { peakMs = xD; }
        else { const ddp = (cum / pk - 1) * 100; if (ddp < worstDD) { worstDD = ddp; worstTroughMs = xD; worstPeakMs = peakMs; } }
      }
    }
    _maxDDPctJS = worstDD;
    // Streaks: midcap from the combined chain; non-midcap keep the backend engine value.
    _maxWinStreakJS  = hasMidcap ? mxWin  : (toNumber(S.max_win_streak)  ?? 0);
    _maxLossStreakJS = hasMidcap ? mxLoss : (toNumber(S.max_loss_streak) ?? 0);
    const _fms = (ms) => { const d = new Date(ms); return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`; };
    if (worstPeakMs != null && worstTroughMs != null) { mddDuration = Math.round((worstTroughMs - worstPeakMs) / 86400000); mddStartDate = _fms(worstPeakMs); mddEndDate = _fms(worstTroughMs); }
    else { mddDuration = 0; mddStartDate = ''; mddEndDate = ''; }
  }
  const _maxDDPtsJS      = toNumber(S.max_dd_pts) ?? 0;
  const _carMddJS        = _maxDDPctJS !== 0 ? (_optCagrPctJS / 100) / Math.abs(_maxDDPctJS) : 0;

  const _optionsSumJS = (hasCalls && hasPuts) ? (_ceSumJS + _peSumJS)
    : (hasPuts ? _peSumJS : (hasCalls ? _ceSumJS : (hasFutures ? _futSumJS : _sumNetJS)));
  // ROI vs Spot: with Midcap = Combined % / Spot % (C17/C14, shown as raw ratio);
  // otherwise the existing options-sum / spot-sum × 100 (unchanged).
  let _roiPctJS;
  if (hasMidcap) {
    const _spotPctDen = (typeof S.spot_change_pct === 'number' && Number.isFinite(S.spot_change_pct))
      ? S.spot_change_pct : (_spotPctJS * 100);
    _roiPctJS = Math.abs(_spotPctDen) > 0 ? _sumPctJS / Math.abs(_spotPctDen) : 0;
  } else {
    _roiPctJS = _spotSumGatedJS !== 0 ? (_optionsSumJS / _spotSumGatedJS) * 100 : 0;
  }

  // Live DD outlier analysis (computed from tm).  Iterate in chronological
  // order so cascade trade IDs (which entered between earlier trade_ids) are
  // placed in correct time sequence — same as the Live DD computation above.
  const _tradePairsDD = [];
  const _chronTmKeys = [];
  const _seenChron = new Set();
  sortedTrades.forEach(t => {
    const k = String(t.Trade || t.trade || 1);
    if (!_seenChron.has(k)) {
      _seenChron.add(k);
      if (tm[k]) _chronTmKeys.push(k);
    }
  });
  _chronTmKeys.forEach(k => {
    const t = tm[k];
    // Combined per-trade values when a Midcap leg is present; NIFTY otherwise.
    const _p = hasMidcap ? t.combinedPct       : t.pct;
    const _l = hasMidcap ? t.combinedActualLiveDd : t.actualLiveDD;
    const _m = hasMidcap ? t.combinedFinalMae  : t.finalMae;
    const pct = (typeof _p === 'number' && Number.isFinite(_p)) ? _p : null;
    const ldd = (typeof _l === 'number' && Number.isFinite(_l)) ? _l : null;
    const mae = (typeof _m === 'number' && Number.isFinite(_m)) ? _m : null;
    if (pct !== null) _tradePairsDD.push({ pct, ldd, mae, idx: _tradePairsDD.length, exitReason: (t.exitReason || '').toUpperCase(), segIdx: _pwSegIdxByKey(k) });
  });
  const _nTrades    = _tradePairsDD.length;
  const _byPctDesc  = [..._tradePairsDD].sort((a, b) => b.pct - a.pct);
  const _posO1 = _nTrades > 0 ? _byPctDesc[0].pct : 0;
  const _posO2 = _nTrades > 1 ? _byPctDesc[0].pct + _byPctDesc[1].pct : _posO1;
  const _posO3 = _nTrades > 2 ? _byPctDesc[0].pct + _byPctDesc[1].pct + _byPctDesc[2].pct : _posO2;
  const _negO1 = _nTrades > 0 ? _byPctDesc[_nTrades - 1].pct : 0;
  const _negO2 = _nTrades > 1 ? _byPctDesc[_nTrades - 1].pct + _byPctDesc[_nTrades - 2].pct : _negO1;
  const _negO3 = _nTrades > 2 ? _byPctDesc[_nTrades - 1].pct + _byPctDesc[_nTrades - 2].pct + _byPctDesc[_nTrades - 3].pct : _negO2;

  const _totalPctSum = _tradePairsDD.reduce((s, p) => s + p.pct, 0);
  const _pctNoO1 = _totalPctSum - _posO1 - _negO1;
  const _pctNoO2 = _totalPctSum - _posO2 - _negO2;
  const _pctNoO3 = _totalPctSum - _posO3 - _negO3;

  const _liveDDExcStats = (excTop, excBot) => {
    const excIdx  = new Set([
      ..._byPctDesc.slice(0, excTop).map(p => p.idx),
      ..._byPctDesc.slice(Math.max(0, _nTrades - excBot)).map(p => p.idx),
    ]);
    const filtered = _tradePairsDD.filter(p => !excIdx.has(p.idx));
    if (filtered.length === 0) return { min: 0, avg: 0 };
    let cumulative = 100;
    let peak = 100;
    let prevCum = 100;
    let prevPeak = 100;
    let prevExitReason = '';
    let prevSegIdx = null;
    const ldds = [];
    filtered.forEach(p => {
      const _reset = patchwise && (_pwSegStarts.length
        ? (prevSegIdx !== null && p.segIdx !== prevSegIdx)
        : ((prevExitReason || '').split('+').includes('FILTER_END')));
      if (_reset) {
        cumulative = 100; peak = 100; prevCum = 100; prevPeak = 100;
      }
      prevSegIdx = p.segIdx;
      prevPeak = peak;
      cumulative *= (1 + p.pct / 100);
      peak = Math.max(peak, cumulative);
      if (p.mae !== null && prevPeak !== 0) {
        const lowestNav = Math.round(prevCum * (1 + p.mae / 100) * 100) / 100;
        const actualLiveDD = Math.round((lowestNav / prevPeak - 1) * 10000) / 100;
        ldds.push(actualLiveDD);
      }
      prevCum = cumulative;
      prevExitReason = p.exitReason || '';
    });
    if (ldds.length === 0) return { min: 0, avg: 0 };
    return { min: +Math.min(...ldds).toFixed(2), avg: +(ldds.reduce((s, v) => s + v, 0) / ldds.length).toFixed(2) };
  };
  const _allLDDs        = _tradePairsDD.filter(p => p.ldd !== null).map(p => p.ldd);
  const _liveDDMin      = _allLDDs.length > 0 ? +Math.min(..._allLDDs).toFixed(2) : 0;
  const _liveDDAvg      = _allLDDs.length > 0 ? +(_allLDDs.reduce((s, v) => s + v, 0) / _allLDDs.length).toFixed(2) : 0;
  // Avg (Combined) Final MAE — mean of each trade's Final MAE (Combined when a Midcap
  // leg is present, NIFTY otherwise; p.mae already holds the right one).
  const _finalMaes      = _tradePairsDD.filter(p => p.mae !== null).map(p => p.mae);
  const _avgFinalMaeJS  = _finalMaes.length > 0 ? +(_finalMaes.reduce((s, v) => s + v, 0) / _finalMaes.length).toFixed(2) : 0;
  const _liveDDNoO1     = _liveDDExcStats(1, 1);
  const _liveDDNoO2     = _liveDDExcStats(2, 2);
  const _liveDDNoO3     = _liveDDExcStats(3, 3);
  const _carMddLiveJS   = _liveDDMin !== 0 ? (_optCagrPctJS / 100) / Math.abs(_liveDDMin) : 0;

  const ws2 = wb.addWorksheet('Summary');
  ws2.columns = [
    { width: 30 }, { width: 20 }, { width: 12 }, { width: 30 }, { width: 20 },
  ];

  const addTitle = (text, rowNum, cols = 'A:E', bgColor = C.navyBg) => {
    ws2.mergeCells(`${cols.split(':')[0]}${rowNum}:${cols.split(':')[1]}${rowNum}`);
    const cell = ws2.getCell(`${cols.split(':')[0]}${rowNum}`);
    cell.value = text;
    cell.font  = boldFont(13, C.navyText);
    cell.fill  = { type: 'pattern', pattern: 'solid', fgColor: bgColor };
    cell.alignment = { horizontal: 'center', vertical: 'middle' };
    ws2.getRow(rowNum).height = 26;
  };

  const addSectionHeader = (text, rowNum) => {
    ws2.mergeCells(`A${rowNum}:E${rowNum}`);
    const cell = ws2.getCell(`A${rowNum}`);
    cell.value = '  ' + text;
    cell.font  = boldFont(11, C.sectionTx);
    cell.fill  = { type: 'pattern', pattern: 'solid', fgColor: C.sectionBg };
    cell.alignment = leftAlign;
    ws2.getRow(rowNum).height = 20;
  };

  const addKvRow = (label, value, rowNum, col = 'A', isAlt = false, valColor = null) => {
    const lCol = col;
    const vCol = String.fromCharCode(col.charCodeAt(0) + 1);
    const lCell = ws2.getCell(`${lCol}${rowNum}`);
    const vCell = ws2.getCell(`${vCol}${rowNum}`);
    lCell.value = label;
    vCell.value = value;
    lCell.font  = boldFont(10, { argb: 'FF2C3E50' });
    lCell.fill  = { type: 'pattern', pattern: 'solid', fgColor: isAlt ? C.altRow : C.labelBg };
    lCell.alignment = leftAlign;
    lCell.border = thinBorder(C.border);
    const numVal    = typeof value === 'number' ? value : parseFloat(String(value || '').replace(/[+%₹,]/g, ''));
    const autoColor = valColor || (isNaN(numVal) ? null : numVal >= 0 ? C.greenTx : C.redTx);
    vCell.font  = boldFont(10, autoColor || { argb: 'FF1A1A2E' });
    vCell.fill  = { type: 'pattern', pattern: 'solid', fgColor: isAlt ? C.altRow : C.white };
    vCell.alignment = leftAlign;
    vCell.border = thinBorder(C.border);
    ws2.getRow(rowNum).height = 18;
  };

  const kv = (l, v, r, col = 'A', alt = false, vc = null) => addKvRow(l, v, r, col, alt, vc);

  // ── Row 1: Report title ────────────────────────────────────────────────────
  addTitle('  BACKTEST SUMMARY REPORT', 1, 'A:E', C.navyBg);

  // ── Row 2: subtitle ────────────────────────────────────────────────────────
  ws2.mergeCells('A2:E2');
  const subCell = ws2.getCell('A2');
  const subtitleParts = [];
  if (comboLabel) subtitleParts.push(comboLabel);
  if (fromDate || toDate) subtitleParts.push(`${fromDate || ''}${fromDate && toDate ? ' → ' : ''}${toDate || ''}`);
  subtitleParts.push(`Generated: ${new Date().toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}`);
  subCell.value     = subtitleParts.join('   ·   ');
  subCell.font      = normFont(10, { argb: 'FF555555' });
  subCell.alignment = centerAlign;
  subCell.fill      = { type: 'pattern', pattern: 'solid', fgColor: C.subHdrBg };
  ws2.getRow(2).height = 16;

  const _recoveryJS = toNumber(S.recovery_factor) ?? 0;

  let row = 4;

  // ── SECTION 1: Performance Overview ──────────────────────────────────────
  addSectionHeader('PERFORMANCE OVERVIEW', row++);

  kv('Overall Profit', _fmtPct(_sumPctJS), row, 'A', false, _sumPctJS >= 0 ? C.greenTx : C.redTx);
  kv('No. of Trades',  _totalCntJS,        row++, 'D', false, { argb: 'FF1A1A2E' });

  kv('Win %',  `${_winRateJS.toFixed(2)}%`,  row, 'A', true, C.greenTx);
  kv('Loss %', `${_lossRateJS.toFixed(2)}%`, row++, 'D', true, C.redTx);

  kv('Avg Profit on Winners', `${_avgWinPctJS.toFixed(2)}%`,  row, 'A', false, C.greenTx);
  kv('Avg Loss on Losers',    `${_avgLossPctJS.toFixed(2)}%`, row++, 'D', false, C.redTx);

  kv('Avg Profit per Trade', `${_avgNetJS >= 0 ? '+' : ''}${_avgNetJS.toFixed(2)}`, row, 'A', true,
    _avgNetJS >= 0 ? C.greenTx : C.redTx);
  // Expectancy Ratio: store the raw float so downstream Excel formulas see
  // full precision; numFmt renders 2 decimals in the cell display.
  const _expRow_bte = row++;
  kv('Expectancy Ratio', _expectancyJS, _expRow_bte, 'D', true,
    _expectancyJS >= 0 ? C.greenTx : C.redTx);
  ws2.getCell(`E${_expRow_bte}`).numFmt = '0.00';

  kv('Max Profit (Single Trade)', _fmtCurrency(_maxNetJS), row, 'A', false, C.greenTx);
  kv('Max Loss (Single Trade)',   _fmtCurrency(_minNetJS), row++, 'D', false, C.redTx);

  kv('CAGR (Options)', _fmtPct(_optCagrPctJS),  row, 'A', true, _optCagrPctJS >= 0 ? C.greenTx : C.redTx);
  kv('CAGR (Spot)',    _fmtPct(_spotCagrPctJS), row++, 'D', true, _spotCagrPctJS >= 0 ? C.greenTx : C.redTx);

  // ── ROI vs Spot block ─────────────────────────────────────────────────────
  row++;

  const _hdrCell = (col, txt, rowN) => {
    const c = ws2.getCell(`${col}${rowN}`);
    c.value = txt;
    c.font  = boldFont(10, C.navyText);
    c.fill  = { type: 'pattern', pattern: 'solid', fgColor: C.headerBg };
    c.alignment = centerAlign;
    c.border = thinBorder();
  };
  _hdrCell('A', 'Type', row);
  _hdrCell('B', 'Sum',  row);
  _hdrCell('C', '%',    row);
  ws2.mergeCells(`D${row}:E${row}`);
  _hdrCell('D', 'ROI vs Spot', row);
  ws2.getCell(`D${row}`).alignment = centerAlign;
  ws2.getRow(row).height = 20;
  const _spotRow = row; row++;

  ws2.mergeCells(`D${_spotRow + 1}:E${_spotRow + 1}`);
  const _roiVal = ws2.getCell(`D${_spotRow + 1}`);
  if (hasMidcap) { _roiVal.value = _roiPctJS; _roiVal.numFmt = 'General'; }  // raw C17/C14 ratio
  else { _roiVal.value = _fmtPct(_roiPctJS); }
  _roiVal.font      = boldFont(11, _roiPctJS >= 0 ? C.greenTx : C.redTx);
  _roiVal.fill      = { type: 'pattern', pattern: 'solid', fgColor: C.white };
  _roiVal.alignment = centerAlign;
  _roiVal.border    = thinBorder(C.border);

  const _addTypeRow = (label, value, pct) => {
    const lC = ws2.getCell(`A${row}`);
    const vC = ws2.getCell(`B${row}`);
    const pC = ws2.getCell(`C${row}`);
    lC.value = label;
    lC.font  = boldFont(10, { argb: 'FF2C3E50' });
    lC.fill  = { type: 'pattern', pattern: 'solid', fgColor: C.labelBg };
    lC.alignment = leftAlign;
    lC.border = thinBorder(C.border);
    vC.value = (+value).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    vC.font  = boldFont(10, value >= 0 ? C.greenTx : C.redTx);
    vC.fill  = { type: 'pattern', pattern: 'solid', fgColor: C.white };
    vC.alignment = leftAlign;
    vC.border = thinBorder(C.border);
    if (pct != null) {
      pC.value = `${pct >= 0 ? '+' : ''}${(+pct).toFixed(2)}%`;
      pC.font  = boldFont(10, pct >= 0 ? C.greenTx : C.redTx);
      pC.fill  = { type: 'pattern', pattern: 'solid', fgColor: C.white };
      pC.alignment = leftAlign;
      pC.border = thinBorder(C.border);
    }
    ws2.getRow(row).height = 18;
  };

  // Use backend summary for Spot P&L sum and Spot P&L % (single source of truth).
  // After the engine fix that writes Spot P&L only on first-leg rows the local
  // sums equal the backend, but reading from `summary.*` keeps all three
  // download paths (ResultsPanel, buildTradeExcel, excel_builder) consistent.
  const _spotSumSummary = (typeof summary?.spot_change === 'number' && Number.isFinite(summary.spot_change))
    ? summary.spot_change : _spotSumGatedJS;
  const _spotPctSummary = (typeof summary?.spot_change_pct === 'number' && Number.isFinite(summary.spot_change_pct))
    ? summary.spot_change_pct : (_spotPctJS * 100);
  _addTypeRow('Spot P&L', _spotSumSummary, _spotPctSummary); row++;
  if (hasCalls)            { _addTypeRow('CE P&L',      _ceSumJS,               _cePctJS * 100);              row++; }
  if (hasPuts)             { _addTypeRow('PE P&L',       _peSumJS,               _pePctJS * 100);              row++; }
  if (hasFutures)          { _addTypeRow('FUT P&L',      _futSumJS,              null);                        row++; }
  if (hasCalls && hasPuts) { _addTypeRow('CE + PE P&L', _ceSumJS + _peSumJS,    (_cePctJS + _pePctJS) * 100); row++; }
  // Midcap leg P&L + Combined rows (matches the backtest Summary Type block).
  if (hasMidcap) {
    const mcs = midcapSummaryO || {};
    const isHypo = midcapLegs.some(l => String(l.midcap_mode || l.mode || '').toLowerCase() === 'hypothetical');
    const sym = mcs.symbol || 'NIFTYMIDCAP100';
    const modeLbl = isHypo ? 'Hypothetical Future' : 'Spot';
    const niftyPrefix = ['CE', 'PE', 'FUT'].filter((_, i) => [hasCalls, hasPuts, hasFutures][i]).join(' + ') || 'NIFTY';
    _addTypeRow(`${sym} ${modeLbl} P&L`, mcs.midcap_leg_pnl_sum, mcs.midcap_leg_pnl_pct_sum); row++;
    _addTypeRow(`${niftyPrefix} + ${sym} ${modeLbl} P&L`, mcs.combined_pnl_sum, mcs.combined_pnl_pct_sum); row++;
  }
  _addTypeRow('Net P&L', _sumNetJS, _sumPctJS); row++;

  row++;

  // ── SECTION 2: Risk Metrics ───────────────────────────────────────────────
  addSectionHeader('RISK METRICS', row++);

  kv('Max Drawdown',   `${_maxDDPctJS.toFixed(2)}%`, row, 'A', false, C.redTx);
  kv('Max DD Days',    mddDuration,                   row++, 'D', false, C.redTx);

  const ddPeriod = (mddStartDate && mddEndDate) ? `${mddStartDate}  →  ${mddEndDate}` : '—';
  ws2.mergeCells(`A${row}:E${row}`);
  const ddCell = ws2.getCell(`A${row}`);
  ddCell.value     = `Drawdown Period:  ${ddPeriod}`;
  ddCell.font      = boldFont(10, C.redTx);
  ddCell.fill      = { type: 'pattern', pattern: 'solid', fgColor: C.redBg };
  ddCell.alignment = centerAlign;
  ddCell.border    = thinBorder(C.border);
  ws2.getRow(row).height = 18;
  row++;

  kv('Return / MaxDD',  _carMddJS.toFixed(4),  row++, 'A', true, _carMddJS >= 0 ? C.greenTx : C.redTx);

  row++;

  // ── SECTION 3: Consistency & Streaks ─────────────────────────────────────
  addSectionHeader('CONSISTENCY & STREAKS', row++);

  kv('Max Win Streak',    `${_maxWinStreakJS} trades`,  row, 'A', false, C.greenTx);
  kv('Max Losing Streak', `${_maxLossStreakJS} trades`, row++, 'D', false, C.redTx);

  row++;

  // ── SECTION 4: Monthly Returns (₹ Net P&L) ───────────────────────────────
  addSectionHeader('MONTHLY RETURNS (₹ Net P&L)', row++);

  const MONTHS  = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const mthHdr  = ['Year', ...MONTHS, 'Total', 'Max DD', 'R/MDD'];
  for (let ci = 0; ci < mthHdr.length; ci++) {
    ws2.getColumn(ci + 1).width = ci === 0 ? 8 : ci <= 12 ? 9 : ci === 13 ? 10 : ci === 14 ? 18 : 10;
  }

  const parseToYearMonth = (d) => {
    if (!d && d !== 0) return null;
    const s = String(d).trim();
    if (!s) return null;
    const parts = s.includes('/') ? s.split('/') : s.split('-');
    if (parts.length !== 3) return null;
    let dd2, mm2, yy2;
    if (parts[0].length === 4) { yy2 = parts[0]; mm2 = parts[1]; dd2 = parts[2]; }
    else                       { dd2 = parts[0]; mm2 = parts[1]; yy2 = parts[2]; }
    const year     = String(yy2);
    const monthIdx = parseInt(mm2, 10) - 1;
    if (!year || !Number.isFinite(monthIdx) || monthIdx < 0 || monthIdx > 11) return null;
    return { year, monthIdx };
  };

  // Per-year max %DD (most negative running-DD value among first-leg rows in that year)
  const byYearMaxDD = {};
  cleanedTrades.forEach(t => {
    const dd = toNumber(hasMidcap ? t['Combined %DD'] : t['%DD']);
    if (dd == null) return;
    const ym = parseToYearMonth(t['Exit Date']);
    if (!ym) return;
    if (byYearMaxDD[ym.year] == null || dd < byYearMaxDD[ym.year]) {
      byYearMaxDD[ym.year] = dd;
    }
  });

  const hdrRow = ws2.getRow(row);
  mthHdr.forEach((h, ci) => {
    const cell = hdrRow.getCell(ci + 1);
    cell.value = h;
    cell.font  = boldFont(10, C.navyText);
    cell.fill  = { type: 'pattern', pattern: 'solid', fgColor: C.headerBg };
    cell.alignment = centerAlign;
    cell.border = thinBorder();
  });
  hdrRow.height = 20;
  row++;

  const byYM    = {};
  const byYMPct = {};
  cleanedTrades.forEach(t => {
    // Combined Net P&L / % when a Midcap leg is present, else NIFTY.
    let net, pct;
    if (hasMidcap) {
      net = toNumber(t['Combined Net P&L']);
      if (net == null) return;
      pct = toNumber(t['Combined Net P&L %']) || 0;
    } else {
      net = toNumber(t['Net P&L']);
      if (net == null) return;
      const spot = toNumber(t['Entry Spot']) || 0;
      pct  = spot > 0 ? (net / spot) * 100 : 0;
    }
    const ym   = parseToYearMonth(t['Exit Date']);
    if (!ym) return;
    if (!byYM[ym.year])    byYM[ym.year]    = Array(12).fill(0);
    if (!byYMPct[ym.year]) byYMPct[ym.year] = Array(12).fill(0);
    byYM[ym.year][ym.monthIdx]    += net;
    byYMPct[ym.year][ym.monthIdx] += pct;
  });

  const renderMonthRows = (dataMap, isPercent) => {
    Object.entries(dataMap).sort().forEach(([yr, mos], ri) => {
      const total = mos.reduce((s, v) => s + v, 0);
      const maxDD = byYearMaxDD[yr] != null ? byYearMaxDD[yr] : '';
      const rMdd  = (typeof maxDD === 'number' && maxDD !== 0 && total !== 0)
        ? +(total / Math.abs(maxDD)).toFixed(2)
        : '';
      const rowData = [yr, ...mos.map(v => +v.toFixed(2)), +total.toFixed(2), maxDD, rMdd];
      const r2 = ws2.getRow(row);
      rowData.forEach((val, ci) => {
        const cell = r2.getCell(ci + 1);
        const isValCol   = ci >= 1 && ci <= 12;
        const isTotalCol = ci === 13;
        const isMaxDdCol = ci === 14;
        if (isPercent && (isValCol || isTotalCol)) {
          cell.value  = typeof val === 'number' ? val / 100 : val;
          cell.numFmt = '0.00%';
        } else if (isMaxDdCol && typeof val === 'number') {
          cell.value  = val / 100;
          cell.numFmt = '0.00%';
        } else {
          cell.value = val;
        }
        const num = typeof val === 'number' ? val : parseFloat(String(val || ''));
        if ((isValCol || isTotalCol) && !isNaN(num) && num !== 0) {
          cell.font = boldFont(10, num >= 0 ? C.greenTx : C.redTx);
          cell.fill = { type: 'pattern', pattern: 'solid', fgColor: num >= 0 ? C.greenBg : C.redBg };
        } else if (ci === 0) {
          cell.font = boldFont(10, C.subHdrTx);
          cell.fill = { type: 'pattern', pattern: 'solid', fgColor: C.subHdrBg };
        } else {
          cell.font = normFont(10);
          cell.fill = { type: 'pattern', pattern: 'solid', fgColor: ri % 2 === 0 ? C.white : C.altRow };
        }
        cell.alignment = centerAlign;
        cell.border    = thinBorder();
      });
      r2.height = 18;
      row++;
    });
  };

  renderMonthRows(byYM, false);

  // ── SECTION 4b: Monthly Returns (% Net P&L) ──────────────────────────────
  row++;
  addSectionHeader('MONTHLY RETURNS (% Net P&L)', row++);

  const hdrRowPct = ws2.getRow(row);
  mthHdr.forEach((h, ci) => {
    const cell = hdrRowPct.getCell(ci + 1);
    cell.value = h;
    cell.font  = boldFont(10, C.navyText);
    cell.fill  = { type: 'pattern', pattern: 'solid', fgColor: C.headerBg };
    cell.alignment = centerAlign;
    cell.border = thinBorder();
  });
  hdrRowPct.height = 20;
  row++;
  renderMonthRows(byYMPct, true);

  // ── SECTION 5: Live DD & Outlier Analysis ────────────────────────────────
  row++;
  addSectionHeader('LIVE DD & OUTLIER ANALYSIS', row++);

  kv('Actual Live DD (min)', `${_liveDDMin.toFixed(2)}%`, row, 'A', false, C.redTx);
  kv('Avg Actual Live DD',   `${_liveDDAvg.toFixed(2)}%`, row++, 'D', false, C.redTx);
  kv(hasMidcap ? 'Avg Combined Final MAE' : 'Avg Final MAE',
     `${_avgFinalMaeJS.toFixed(2)}%`, row++, 'A', false, C.redTx);
  kv('CAR/MDD (Booked)',     _carMddJS.toFixed(4),         row, 'A', true,  _carMddJS    >= 0 ? C.greenTx : C.redTx);
  kv('CAR/MDD Live',         _carMddLiveJS.toFixed(4),     row++, 'D', true, _carMddLiveJS >= 0 ? C.greenTx : C.redTx);

  row++;

  // Outlier rows — exact Excel column names (Q/R/S/T, U/V/W/X, Y/Z/AA/AB)
  kv('+ve Outlier 1',                        _fmtPct(_posO1),                  row, 'A', false, C.greenTx);
  kv('-ve Outlier 1',                        _fmtPct(_negO1),                  row++, 'D', false, C.redTx);
  kv('Actual Live DD Without Outlier 1',     `${_liveDDNoO1.min.toFixed(2)}%`, row, 'A', true,  C.redTx);
  kv('Avg Actual Live DD Without Outlier 1', `${_liveDDNoO1.avg.toFixed(2)}%`, row++, 'D', true,  C.redTx);
  kv('+ve Outlier 2',                        _fmtPct(_posO2),                  row, 'A', false, C.greenTx);
  kv('-ve Outlier 2',                        _fmtPct(_negO2),                  row++, 'D', false, C.redTx);
  kv('Actual Live DD Without Outlier 2',     `${_liveDDNoO2.min.toFixed(2)}%`, row, 'A', true,  C.redTx);
  kv('Avg Actual Live DD Without Outlier 2', `${_liveDDNoO2.avg.toFixed(2)}%`, row++, 'D', true,  C.redTx);
  kv('+ve Outlier 3',                        _fmtPct(_posO3),                  row, 'A', false, C.greenTx);
  kv('-ve Outlier 3',                        _fmtPct(_negO3),                  row++, 'D', false, C.redTx);
  kv('Actual Live DD Without Outlier 3',     `${_liveDDNoO3.min.toFixed(2)}%`, row, 'A', true,  C.redTx);
  kv('Avg Actual Live DD Without Outlier 3', `${_liveDDNoO3.avg.toFixed(2)}%`, row++, 'D', true,  C.redTx);

  // "… P&L % Without Top N Outliers" — label reflects the leg configuration:
  // with Midcap it matches the Type rows; otherwise the existing "CE + PE + P&L %".
  row++;
  let _outlierBase = 'CE + PE + P&L %';
  if (hasMidcap) {
    const mcs = midcapSummaryO || {};
    const isHypo = midcapLegs.some(l => String(l.midcap_mode || l.mode || '').toLowerCase() === 'hypothetical');
    const sym = mcs.symbol || 'NIFTYMIDCAP100';
    const modeLbl = isHypo ? 'Hypothetical Future' : 'Spot';
    const niftyPrefix = ['CE', 'PE', 'FUT'].filter((_, i) => [hasCalls, hasPuts, hasFutures][i]).join(' + ') || 'NIFTY';
    _outlierBase = `${niftyPrefix} + ${sym} ${modeLbl} P&L %`;
  }
  [
    [`${_outlierBase} Without Top 1 Outliers`, _pctNoO1],
    [`${_outlierBase} Without Top 2 Outliers`, _pctNoO2],
    [`${_outlierBase} Without Top 3 Outliers`, _pctNoO3],
  ].forEach(([label, val], ri) => {
    const r2 = ws2.getRow(row);
    ws2.mergeCells(`A${row}:D${row}`);
    const lCell = r2.getCell(1);
    lCell.value = label;
    lCell.font = boldFont(10, { argb: 'FF2C3E50' });
    lCell.fill = { type: 'pattern', pattern: 'solid', fgColor: ri % 2 === 0 ? C.labelBg : C.altRow };
    lCell.alignment = leftAlign;
    lCell.border = thinBorder();
    const vCell = r2.getCell(5);
    vCell.value = _fmtPct(val);
    vCell.font = boldFont(10, val >= 0 ? C.greenTx : C.redTx);
    vCell.fill = { type: 'pattern', pattern: 'solid', fgColor: ri % 2 === 0 ? C.white : C.altRow };
    vCell.alignment = centerAlign;
    vCell.border = thinBorder();
    r2.height = 18;
    row++;
  });

  // ════════════════════════════════════════════════════════════════════════════
  // SHEET 3 — PATCH WISE  (phase-wise distribution per filter patch)
  // Identical to ResultsPanel Sheet 3. Active whenever a filter is present.
  // ════════════════════════════════════════════════════════════════════════════
  if (filterName) {
    const _seenP = new Set(); const orderedKeys = [];
    sortedTrades.forEach(_tr => {
      const _k = String(_tr.Trade || _tr.trade || 1);
      if (!_seenP.has(_k)) { _seenP.add(_k); if (tm[_k]) orderedKeys.push(_k); }
    });
    const tdata = orderedKeys.map(k => {
      const legs = groupedByTrade[k] || [];
      const mainRow = legs.find(l => !l['ReEntryIndex'] && !l['ReEntryTrigger'] && !l['ReEntryMode'] && !isLazyLegRow(l)) || legs[0] || {};
      const spot = toNumber(mainRow['Entry Spot']) || 0;
      const mc = (tm[k] && tm[k].midcap) || {};
      // NIFTY phase uses whatever option leg(s) are present (CE and/or PE), not
      // just CE — so SELL PE / BUY PE / CE+PE all work. Sum option-leg P&L + MAE.
      const optLegs = legs.filter(l => ['CE','CALL','PE','PUT'].includes((l['Type']||'').toUpperCase()));
      const niftyPnl = optLegs.reduce((s,l) => s + (toNumber(l['CE P&L'])||0) + (toNumber(l['PE P&L'])||0), 0);
      const niftyMaeSum = optLegs.length ? optLegs.reduce((s,l) => s + (toNumber(l['MAE'])||0), 0) : null;
      const cfm = tm[k] ? tm[k].combinedFinalMae : '';
      return {
        entry: mainRow['Entry Date'], exit: mainRow['Exit Date'],
        entryMs: parseDateMs(mainRow['Entry Date']), exitMs: parseDateMs(mainRow['Exit Date']),
        midcapPct: toNumber(mc['Midcap Leg P&L %']), midcapMae: toNumber(mc['Midcap MAE']), midcapClose: toNumber(mc['Midcap Entry Spot']),
        callPct: (optLegs.length && spot > 0) ? (niftyPnl / spot) * 100 : null, callMae: niftyMaeSum,
        combinedPct: toNumber(mc['Combined Net P&L %']), combinedMae: (cfm !== '' && cfm != null) ? Number(cfm) : null,
      };
    });

    // Patches from the UPLOADED FILTER's segment START dates: a new patch begins
    // (equity resets to 100) when a trade's entry reaches the next segment start.
    // Boundary = next start, so spot-adj cascades that run past a window's end stay
    // in that patch until the next segment begins. Falls back to 30-day gap
    // detection only when no filter segments are available.
    const _segStarts = (Array.isArray(filterSegments) ? filterSegments : [])
      .map(s => parseDateMs(s && (s.start || s.Start || s.from || s.start_date || s.startdt)))
      .filter(ms => Number.isFinite(ms))
      .sort((a, b) => a - b);
    const patches = [];
    if (_segStarts.length) {
      let _curIdx = -2;
      tdata.forEach(td => {
        let i = 0;
        for (let j = 0; j < _segStarts.length; j++) { if (_segStarts[j] <= td.entryMs) i = j; else break; }
        if (i !== _curIdx) { patches.push([]); _curIdx = i; }
        patches[patches.length - 1].push(td);
      });
    } else {
      const GAP_MS = 30 * 86400000;
      let _lastExitMs = null;
      tdata.forEach(td => {
        const gap = (_lastExitMs != null && Number.isFinite(td.entryMs)) ? (td.entryMs - _lastExitMs) : 0;
        if (patches.length === 0 || gap > GAP_MS) patches.push([]);
        patches[patches.length - 1].push(td);
        if (Number.isFinite(td.exitMs)) _lastExitMs = td.exitMs;
      });
    }

    if (patches.length) {
      const buildChain = (trades, driveOf, maeOf) => {
        let prevCumm = 100, peak = 100, prevPeak = 100; const rows = []; let pnlSum = 0, liveDDMin = Infinity;
        trades.forEach(td => {
          const dr = driveOf(td); const d = Number.isFinite(dr) ? dr : 0;
          const cumm = prevCumm * (1 + d / 100);
          peak = Math.max(peak, cumm);
          const dd = (peak > cumm) ? (cumm - peak) : '';
          const pctDd = (typeof dd === 'number' && peak !== 0) ? (dd / peak) * 100 : 0;
          const mv = maeOf(td); const m = Number.isFinite(mv) ? mv : 0;
          const lowestNav = prevCumm * (1 + m / 100);
          const liveDD = prevPeak !== 0 ? (lowestNav / prevPeak - 1) * 100 : 0;
          rows.push({ td, drive: d, cumm, peak, dd, pctDd, mae: m, lowestNav, liveDD });
          pnlSum += d; if (liveDD < liveDDMin) liveDDMin = liveDD; prevCumm = cumm; prevPeak = peak;
        });
        const last = rows[rows.length - 1]; const f = trades[0], l = trades[trades.length - 1];
        const days = (Number.isFinite(f.entryMs) && Number.isFinite(l.exitMs)) ? (l.exitMs - f.entryMs) / 86400000 : null;
        const cagr = (days && days > 0 && last && last.cumm > 0) ? (Math.pow(last.cumm / 100, 365 / days) - 1) * 100 : null;
        return { rows, entry: f.entry, exit: l.exit, cagr, pnlSum, liveDDMin: (liveDDMin === Infinity ? null : liveDDMin) };
      };

      const _opt = hasCalls && hasPuts ? 'CE+PE' : hasCalls ? 'CE' : hasPuts ? 'PE' : 'Options';
      const niftyTitle = `Nifty ${_opt}`;
      const _niftyPhase = { title: niftyTitle, kind: 'std', dates: false, drive: td => td.callPct, mae: td => td.callMae,
        detailHdr: ['Net P&L %','Cumulative','Peak','DD','%DD','MAE','Lowest NAV','Actual Live DD'],
        sideHdr: ['Entry','Exit','CAGR','Net P&L %','Live DD'] };
      const PHASES = hasMidcap ? [
        { title: 'Midcap Future', kind: 'midcap', dates: true, drive: td => td.midcapPct, mae: td => td.midcapMae,
          detailHdr: ['Entry Date','Exit Date','Midcap Hypo P&L %','cumm','Peak','Close','Hypo MAE','Lowest NAV','Live DD'],
          sideHdr: ['Entry','Exit','CAGR','Future P&L %','Live DD'] },
        _niftyPhase,
        { title: `${niftyTitle} + Midcap Future`, kind: 'std', dates: false, drive: td => td.combinedPct, mae: td => td.combinedMae,
          detailHdr: ['Net P&L %','Cumulative','Peak','DD','%DD','MAE','Lowest NAV','Actual Live DD'],
          sideHdr: ['Entry','Exit','CAGR','Net P&L %','Live DD'] },
      ] : [_niftyPhase];

      const wsP = wb.addWorksheet('Patch wise', { views: [{ state: 'frozen', ySplit: 4 }] });
      const hdrCell = (r, c, val, o = {}) => {
        const cell = wsP.getRow(r).getCell(c);
        cell.value = val;
        cell.font = boldFont(o.size || 10, o.tx || C.headerTx);
        cell.fill = { type: 'pattern', pattern: 'solid', fgColor: o.bg || C.headerBg };
        cell.alignment = o.align || centerAlign;
        cell.border = thinBorder();
        return cell;
      };
      const valCell = (r, c, val, fmt) => {
        const cell = wsP.getRow(r).getCell(c);
        cell.value = (val == null ? '' : val);
        cell.font = normFont(10);
        if (typeof val === 'number') cell.numFmt = fmt || '0.00';
        cell.alignment = centerAlign;
        cell.border = thinBorder();
        return cell;
      };

      let col = 1;
      PHASES.forEach(phase => {
        const chains = patches.map(p => buildChain(p, phase.drive, phase.mae));
        const dW = phase.detailHdr.length;
        const detailStart = col;
        const sideStart = col + dW + 1;

        const tCell = wsP.getRow(1).getCell(detailStart);
        tCell.value = phase.title; tCell.font = boldFont(11, C.navyText);
        tCell.fill = { type: 'pattern', pattern: 'solid', fgColor: C.navyBg }; tCell.alignment = leftAlign;
        wsP.mergeCells(1, detailStart, 1, detailStart + dW - 1);

        const sCell = wsP.getRow(2).getCell(detailStart);
        sCell.value = 'Phase wise Distribution'; sCell.font = boldFont(9, C.subHdrTx);
        sCell.fill = { type: 'pattern', pattern: 'solid', fgColor: C.subHdrBg }; sCell.alignment = leftAlign;
        wsP.mergeCells(2, detailStart, 2, detailStart + dW - 1);

        phase.detailHdr.forEach((h, i) => hdrCell(4, detailStart + i, h));

        let rr = 5;
        chains.forEach(ch => {
          ch.rows.forEach(rw => {
            let c2 = detailStart;
            if (phase.dates) { valCell(rr, c2++, rw.td.entry); valCell(rr, c2++, rw.td.exit); }
            if (phase.kind === 'midcap') {
              valCell(rr, c2++, rw.drive); valCell(rr, c2++, rw.cumm); valCell(rr, c2++, rw.peak);
              valCell(rr, c2++, rw.td.midcapClose); valCell(rr, c2++, rw.mae, '0.00"%"');
              valCell(rr, c2++, rw.lowestNav); valCell(rr, c2++, rw.liveDD, '0.00"%"');
            } else {
              valCell(rr, c2++, rw.drive); valCell(rr, c2++, rw.cumm); valCell(rr, c2++, rw.peak);
              valCell(rr, c2++, rw.dd); valCell(rr, c2++, rw.pctDd, '0.00"%"'); valCell(rr, c2++, rw.mae);
              valCell(rr, c2++, rw.lowestNav); valCell(rr, c2++, rw.liveDD);
            }
            rr++;
          });
        });

        phase.sideHdr.forEach((h, i) => hdrCell(4, sideStart + i, h, { bg: C.sectionBg, tx: C.sectionTx }));
        chains.forEach((ch, i) => {
          const sr = 5 + i; let c3 = sideStart;
          valCell(sr, c3++, ch.entry); valCell(sr, c3++, ch.exit);
          valCell(sr, c3++, ch.cagr, '0.00"%"'); valCell(sr, c3++, ch.pnlSum); valCell(sr, c3++, ch.liveDDMin);
        });

        for (let i = 0; i < dW; i++) wsP.getColumn(detailStart + i).width = 12;
        for (let i = 0; i < phase.sideHdr.length; i++) wsP.getColumn(sideStart + i).width = 12;
        col = sideStart + phase.sideHdr.length + 1;
      });
    }
  }

  // ════════════════════════════════════════════════════════════════════════════
  // SHEET 4 — CONFIGURATION  (optimizer run parameters + this combo's values)
  // ════════════════════════════════════════════════════════════════════════════
  if (runConfig) {
    const ws3 = wb.addWorksheet('Configuration');
    ws3.columns = [
      { width: 32 }, { width: 18 }, { width: 14 }, { width: 14 }, { width: 14 }, { width: 18 },
    ];

    const cfg3TitleRow = (text, rowN, cols = 'A:F', bgColor = C.navyBg) => {
      ws3.mergeCells(`A${rowN}:F${rowN}`);
      const cell = ws3.getCell(`A${rowN}`);
      cell.value = text;
      cell.font  = boldFont(13, C.navyText);
      cell.fill  = { type: 'pattern', pattern: 'solid', fgColor: bgColor };
      cell.alignment = { horizontal: 'center', vertical: 'middle' };
      ws3.getRow(rowN).height = 26;
    };

    const cfg3SectionRow = (text, rowN) => {
      ws3.mergeCells(`A${rowN}:F${rowN}`);
      const cell = ws3.getCell(`A${rowN}`);
      cell.value = '  ' + text;
      cell.font  = boldFont(11, C.sectionTx);
      cell.fill  = { type: 'pattern', pattern: 'solid', fgColor: C.sectionBg };
      cell.alignment = { horizontal: 'left', vertical: 'middle' };
      ws3.getRow(rowN).height = 20;
    };

    const cfg3KvRow = (label, value, rowN, isAlt = false, valColor = null) => {
      ws3.mergeCells(`A${rowN}:C${rowN}`);
      ws3.mergeCells(`D${rowN}:F${rowN}`);
      const lCell = ws3.getCell(`A${rowN}`);
      const vCell = ws3.getCell(`D${rowN}`);
      lCell.value = label;
      vCell.value = value;
      lCell.font  = boldFont(10, { argb: 'FF2C3E50' });
      lCell.fill  = { type: 'pattern', pattern: 'solid', fgColor: isAlt ? C.altRow : C.labelBg };
      lCell.alignment = { horizontal: 'left', vertical: 'middle' };
      lCell.border = thinBorder(C.border);
      const autoColor = valColor || { argb: 'FF1A1A2E' };
      vCell.font  = boldFont(10, autoColor);
      vCell.fill  = { type: 'pattern', pattern: 'solid', fgColor: isAlt ? C.altRow : C.white };
      vCell.alignment = { horizontal: 'left', vertical: 'middle' };
      vCell.border = thinBorder(C.border);
      ws3.getRow(rowN).height = 18;
    };

    let r3 = 1;

    cfg3TitleRow('OPTIMIZER RUN CONFIGURATION', r3++);

    // Subtitle — combo label + generated timestamp
    ws3.mergeCells(`A${r3}:F${r3}`);
    const sub3 = ws3.getCell(`A${r3}`);
    const sub3Parts = [];
    if (comboLabel) sub3Parts.push(comboLabel);
    sub3Parts.push(`Generated: ${new Date().toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}`);
    sub3.value     = sub3Parts.join('   ·   ');
    sub3.font      = normFont(10, { argb: 'FF555555' });
    sub3.alignment = { horizontal: 'center', vertical: 'middle' };
    sub3.fill      = { type: 'pattern', pattern: 'solid', fgColor: C.subHdrBg };
    ws3.getRow(r3).height = 16;
    r3++;

    r3++; // blank
    cfg3SectionRow('RUN SETTINGS', r3++);
    cfg3KvRow('Search Method',    runConfig.methodLabel    || runConfig.method    || '—', r3++, false);
    cfg3KvRow('Ranking Objective', runConfig.objectiveLabel || runConfig.objective || '—', r3++, true);
    cfg3KvRow('Total Combinations', (runConfig.totalCombos || 0).toLocaleString(), r3++, false);
    if (runConfig.sampleN != null) {
      cfg3KvRow('Sample / Budget N', String(runConfig.sampleN), r3++, true);
    }
    if (runConfig.algorithm) {
      cfg3KvRow('Algorithm', runConfig.algorithm.toUpperCase(), r3++, false);
    }

    // ── Parameter sweep ranges ─────────────────────────────────────────────
    if (runConfig.paramSpecs && runConfig.paramSpecs.length > 0) {
      r3++;
      cfg3SectionRow('PARAMETER RANGES SWEPT', r3++);

      // Header row
      const pHdrCols = ['Parameter', 'Type', 'Min', 'Max', 'Step / Values', 'This Combo Value'];
      const pHdr = ws3.getRow(r3);
      pHdrCols.forEach((h, ci) => {
        const cell = pHdr.getCell(ci + 1);
        cell.value = h;
        cell.font  = boldFont(10, C.navyText);
        cell.fill  = { type: 'pattern', pattern: 'solid', fgColor: C.headerBg };
        cell.alignment = { horizontal: 'center', vertical: 'middle' };
        cell.border = thinBorder();
      });
      pHdr.height = 20;
      r3++;

      // Normalize path for lookup in comboValues (paramSpecs use legs[0], combo uses legs.0)
      const normPath = (p) => String(p || '').replace(/\[(\d+)\]/g, '.$1');

      runConfig.paramSpecs.forEach((spec, si) => {
        const isAlt = si % 2 !== 0;
        const comboVal = comboValues[normPath(spec.path)] ?? comboValues[spec.path];
        const comboValDisplay = comboVal != null ? String(comboVal) : '—';

        const pRow = ws3.getRow(r3);
        const rowData = [
          spec.label || spec.path,
          spec.kind === 'enum' ? 'Enum' : 'Range',
          spec.kind === 'range' ? spec.min : '',
          spec.kind === 'range' ? spec.max : '',
          spec.kind === 'range'
            ? `step ${spec.step}${spec.unit ? ' ' + spec.unit : ''}`
            : (spec.values || []).join(', '),
          comboValDisplay,
        ];
        rowData.forEach((val, ci) => {
          const cell = pRow.getCell(ci + 1);
          cell.value = val;
          const bg = isAlt ? C.altRow : C.white;
          if (ci === 0) {
            cell.font = boldFont(10, { argb: 'FF2C3E50' });
            cell.fill = { type: 'pattern', pattern: 'solid', fgColor: isAlt ? C.altRow : C.labelBg };
          } else if (ci === 5) {
            // "This Combo Value" — highlight in accent
            cell.font = boldFont(10, { argb: 'FF1E3A8A' });
            cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFD6E4F7' } };
          } else {
            cell.font = normFont(10);
            cell.fill = { type: 'pattern', pattern: 'solid', fgColor: bg };
          }
          cell.alignment = { horizontal: ci === 0 ? 'left' : 'center', vertical: 'middle' };
          cell.border = thinBorder(C.border);
        });
        pRow.height = 18;
        r3++;
      });
    }
  }

  // ── WOW & MOM Summary (shared util; identical to backtest export) ──────────
  // Optimizer combos use the combo label as the block title so the per-combo
  // tradesheet and the merged WOW/MOM summary match exactly; fall back to the
  // strategy-derived title for a plain backtest with no combo label.
  // Both %DD and Combined %DD are computed here as (dd/peak)*100 — a
  // percentage NUMBER, not a decimal fraction (unlike ResultsPanel.jsx's
  // non-midcap %DD) — so tell the writer to always divide by 100.
  writeWowMomSheet(wb, cleanedTrades, {
    hasMidcap,
    title: comboLabel || buildWowMomTitle(runConfig),
    ddIsPercent: true,
  });

  // ── Serialize ──────────────────────────────────────────────────────────────
  const buf  = await wb.xlsx.writeBuffer();
  return new Blob([buf], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
}
