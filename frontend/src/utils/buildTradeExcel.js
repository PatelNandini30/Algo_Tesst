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

/** Compute Net MAE 1, Net MAE 2, Final MAE for a group of legs. */
const calcTradeMae = (legs) => {
  const futureLegs = legs.filter(isFutureRow);
  const optionLegs = legs.filter(isOptionRow);
  if (optionLegs.length === 0) return null;

  const optionMae = sumRequired(optionLegs, 'MAE');
  const optionMfe = sumRequired(optionLegs, 'MFE');
  if ([optionMae, optionMfe].some(v => v == null)) return null;

  if (futureLegs.length > 0) {
    const futureMfe = sumRequired(futureLegs, 'MFE');
    const futureMae = sumRequired(futureLegs, 'MAE');
    if ([futureMfe, futureMae].some(v => v == null)) return null;
    const netMae1 = futureMfe + optionMae;
    const netMae2 = optionMfe + futureMae;
    return { netMae1: roundMae(netMae1), netMae2: roundMae(netMae2), finalMae: roundMae(Math.min(netMae1, netMae2)) };
  }

  const buyOptionLegs  = optionLegs.filter(isBuyLeg);
  const sellOptionLegs = optionLegs.filter(isSellLeg);
  if (buyOptionLegs.length > 0 && sellOptionLegs.length > 0) {
    const buyMae  = sumRequired(buyOptionLegs,  'MAE');
    const buyMfe  = sumRequired(buyOptionLegs,  'MFE');
    const sellMae = sumRequired(sellOptionLegs, 'MAE');
    const sellMfe = sumRequired(sellOptionLegs, 'MFE');
    if ([buyMae, buyMfe, sellMae, sellMfe].some(v => v == null)) return null;
    const netMae1 = sellMae + buyMfe;
    const netMae2 = sellMfe + buyMae;
    return { netMae1: roundMae(netMae1), netMae2: roundMae(netMae2), finalMae: roundMae(Math.min(netMae1, netMae2)) };
  }

  // All option legs same side (all BUY or all SELL) — naive sum
  return {
    netMae1:  roundMae(optionMae),
    netMae2:  roundMae(optionMfe),
    finalMae: roundMae(Math.min(optionMae, optionMfe)),
  };
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
  const { comboLabel = '', fromDate = '', toDate = '', runConfig = null, comboValues = {} } = opts;
  const sourceTrades = trades || [];

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

  // ── Per-trade aggregates ────────────────────────────────────────────────────
  const TRADE_COLS = new Set([
    'Net MAE 1', 'Net MAE 2', 'Final MAE',
    'Net P&L', '% P&L', 'Cumulative', 'Peak', 'DD', '%DD',
    'Lowest NAV', 'Actual Live DD',
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
    const tradeMae   = calcTradeMae(legs);
    tm[k] = {
      net,
      pct:        spot > 0 ? (net / spot) * 100 : 0,
      netMae1:    tradeMae?.netMae1  ?? '',
      netMae2:    tradeMae?.netMae2  ?? '',
      finalMae:   tradeMae?.finalMae ?? '',
      cumulative: '',
      peak:       '',
      dd:         '',
      pctDd:      '',
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
    sortedTmKeys.forEach(k => {
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
      t.pctDd = peak !== 0 ? dd / peak : 0;
    });

    // Lowest NAV and Actual Live DD
    // Excel formula: AS2=AN2 (first trade), AS_n=AN_(n-1)*(1+AR_n%) thereafter
    let prevCum = 100;
    let firstTradeDone = false;
    sortedTmKeys.forEach(k => {
      const t    = tm[k];
      const mae  = (t.finalMae  !== '' && t.finalMae  != null) ? t.finalMae  : null;
      const peak = (t.peak      !== '' && t.peak      != null) ? t.peak      : null;
      const cum  = (t.cumulative !== '' && t.cumulative != null) ? t.cumulative : null;
      if (mae != null && peak != null && peak !== 0) {
        // Research-team formula (matches column BE of research workbook):
        //   Trade 1: lowestNav = cumulative
        //   Trade N: lowestNav = prev_cumulative * (1 + Final MAE_N / 100)
        // Store full float precision so the chain doesn't accumulate rounding
        // error — Excel numFmt '#,##0.00' on the cell handles 2-decimal display.
        const lowestNav = (!firstTradeDone && cum != null)
          ? cum
          : prevCum * (1 + mae / 100);
        const actualLiveDD = (lowestNav / peak - 1) * 100;
        t.lowestNav    = lowestNav;
        t.actualLiveDD = actualLiveDD;
        firstTradeDone = true;
      } else {
        t.lowestNav    = '';
        t.actualLiveDD = '';
        firstTradeDone = true;
      }
      if (cum != null) prevCum = cum;
    });
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
    'Exit Price', 'MAE', 'MFE', 'Net MAE 1', 'Net MAE 2', 'Final MAE',
    ...(hasCalls    ? ['CE P&L', 'CE P&L %']  : []),
    ...(hasPuts     ? ['PE P&L', 'PE P&L %']  : []),
    ...(hasFutures  ? ['FUT P&L'] : []),
    'Net P&L', '% P&L', 'Cumulative', 'Peak', 'DD', '%DD', 'Lowest NAV', 'Actual Live DD',
    'Exit Reason',
    ...(hasStrikeShift ? ['Strike Shift Reason'] : []),
    ...(hasStr       ? ['STR Segment']     : []),
    ...(hasFilterSeg ? ['Filter Segment']  : []),
  ];

  // ── Build cleaned trade rows ────────────────────────────────────────────────
  const DATE_COLS  = new Set(['Entry Date', 'Exit Date', 'Expiry', 'Leg Exit Date', 'Lazy Entry Date', 'Lazy Exit Date']);
  const TRUE_PCT_COLS = new Set(['Spot P&L %', 'CE P&L %', 'PE P&L %', '%DD']);
  const MAE_COLS   = new Set(['MAE', 'MFE', 'Net MAE 1', 'Net MAE 2', 'Final MAE']);

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
        cell.numFmt = TRUE_PCT_COLS.has(colKey)
          ? '0.00%'
          : MAE_COLS.has(colKey)
          ? '#,##0.0000'
          : (Number.isInteger(cell.value) ? '0' : '#,##0.00');
      }
    });

    // Color Net P&L and % P&L
    if (net !== null) {
      const col1 = keyOrder.indexOf('Net P&L') + 1;
      const col2 = keyOrder.indexOf('% P&L')   + 1;
      [col1, col2].filter(c => c > 0).forEach(c => {
        const cell = r.getCell(c);
        cell.font = boldFont(10, net >= 0 ? C.greenTx : C.redTx);
        cell.fill = { type: 'pattern', pattern: 'solid', fgColor: net >= 0 ? C.greenBg : C.redBg };
      });
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

  // Trade-level stats only from first-leg rows (those with a numeric Net P&L)
  for (const t of cleanedTrades) {
    const p = t['% P&L']; const n = t['Net P&L'];
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
    const cum = t['Cumulative'];
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
  const _maxDDPctJS      = toNumber(S.max_dd_pct) ?? 0;
  const _maxDDPtsJS      = toNumber(S.max_dd_pts) ?? 0;
  const _carMddJS        = _maxDDPctJS !== 0 ? (_optCagrPctJS / 100) / Math.abs(_maxDDPctJS) : 0;
  const _maxWinStreakJS  = toNumber(S.max_win_streak)  ?? 0;
  const _maxLossStreakJS = toNumber(S.max_loss_streak) ?? 0;
  const mddStartDate     = S.mdd_start_date || '';
  const mddEndDate       = S.mdd_end_date   || '';
  const mddDuration      = toNumber(S.mdd_duration_days) ?? '';

  const _optionsSumJS = (hasCalls && hasPuts) ? (_ceSumJS + _peSumJS)
    : (hasPuts ? _peSumJS : (hasCalls ? _ceSumJS : (hasFutures ? _futSumJS : _sumNetJS)));
  const _roiPctJS = _spotSumGatedJS !== 0 ? (_optionsSumJS / _spotSumGatedJS) * 100 : 0;

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
    const pct = (typeof t.pct === 'number' && Number.isFinite(t.pct)) ? t.pct : null;
    const ldd = (typeof t.actualLiveDD === 'number' && Number.isFinite(t.actualLiveDD)) ? t.actualLiveDD : null;
    if (pct !== null) _tradePairsDD.push({ pct, ldd, idx: _tradePairsDD.length });
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
    const filtered = _tradePairsDD.filter(p => !excIdx.has(p.idx) && p.ldd !== null);
    if (filtered.length === 0) return { min: 0, avg: 0 };
    const ldds = filtered.map(p => p.ldd);
    return { min: +Math.min(...ldds).toFixed(2), avg: +(ldds.reduce((s, v) => s + v, 0) / ldds.length).toFixed(2) };
  };
  const _allLDDs        = _tradePairsDD.filter(p => p.ldd !== null).map(p => p.ldd);
  const _liveDDMin      = _allLDDs.length > 0 ? +Math.min(..._allLDDs).toFixed(2) : 0;
  const _liveDDAvg      = _allLDDs.length > 0 ? +(_allLDDs.reduce((s, v) => s + v, 0) / _allLDDs.length).toFixed(2) : 0;
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
  _roiVal.value     = _fmtPct(_roiPctJS);
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
  const mthHdr  = ['Year', ...MONTHS, 'Total', 'Max DD', 'DD Days', 'R/MDD'];
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
    const dd = toNumber(t['%DD']);
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
    const net  = toNumber(t['Net P&L']);
    if (net == null) return;
    const spot = toNumber(t['Entry Spot']) || 0;
    const pct  = spot > 0 ? (net / spot) * 100 : 0;
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
      const maxDD = byYearMaxDD[yr] != null ? +byYearMaxDD[yr].toFixed(2) : '';
      const rMdd  = (typeof maxDD === 'number' && maxDD !== 0 && total !== 0)
        ? +(Math.abs(total) / Math.abs(maxDD)).toFixed(2)
        : '';
      const rowData = [yr, ...mos.map(v => +v.toFixed(2)), +total.toFixed(2), maxDD, '', rMdd];
      const r2 = ws2.getRow(row);
      rowData.forEach((val, ci) => {
        const cell = r2.getCell(ci + 1);
        const isValCol   = ci >= 1 && ci <= 12;
        const isTotalCol = ci === 13;
        if (isPercent && (isValCol || isTotalCol)) {
          cell.value  = typeof val === 'number' ? val / 100 : val;
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

  // CE + PE + P&L % Without Top N Outliers
  // Formula: totalSum - posOutlierSum - negOutlierSum  (matches Excel C3-R3-Q3, C3-V3-U3, C3-Z3-Y3)
  row++;
  [
    ['CE + PE + P&L % Without Top 1 Outliers', _pctNoO1],
    ['CE + PE + P&L % Without Top 2 Outliers', _pctNoO2],
    ['CE + PE + P&L % Without Top 3 Outliers', _pctNoO3],
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
  // SHEET 3 — CONFIGURATION  (optimizer run parameters + this combo's values)
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

  // ── Serialize ──────────────────────────────────────────────────────────────
  const buf  = await wb.xlsx.writeBuffer();
  return new Blob([buf], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
}
