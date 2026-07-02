/**
 * wowMomSheet.js — shared ExcelJS writer for the "WOW & MOM Summary" sheet.
 *
 * Used by BOTH the backtest export (ResultsPanel.jsx) and the optimizer
 * per-combo export (buildTradeExcel.js) so the two are byte-identical. The
 * Python paths (excel_builder.py ZIP combos + the merged all-combos summary)
 * mirror this exactly — keep them in sync.
 *
 * Computation lives in wowMom.js (buildWowMom / computeRatios / wowYearDrawdown).
 * Styling is self-contained here so callers don't need to pass a palette.
 */
import {
  MONTHS as WM_MONTHS, WOW_RF, MOM_RF, WOW_NANN, MOM_NANN,
  buildWowMom, computeRatios, flatWeekly, flatMonthly, getMonthMaps, wowYearDrawdown,
} from './wowMom';

// ── palette / fonts (mirror ResultsPanel C) ───────────────────────────────
const C = {
  navyBg:    { argb: 'FF1F3864' }, navyText: { argb: 'FFFFFFFF' },
  sectionBg: { argb: 'FF2C5F8A' }, headerBg: { argb: 'FF34495E' }, headerTx: { argb: 'FFFFFFFF' },
  subHdrBg:  { argb: 'FFD6E4F7' }, subHdrTx: { argb: 'FF1F3864' },
  greenBg:   { argb: 'FFD4EFDF' }, greenTx:  { argb: 'FF1E7E34' },
  redBg:     { argb: 'FFFDE8E8' }, redTx:    { argb: 'FFC0392B' },
  labelBg:   { argb: 'FFF2F6FA' }, border:   { argb: 'FFB0C4D8' },
};
const thinBorder = (color = C.border) => ({
  top: { style: 'thin', color }, left: { style: 'thin', color },
  bottom: { style: 'thin', color }, right: { style: 'thin', color },
});
const boldFont = (sz = 11, color = { argb: 'FF000000' }) => ({ bold: true, size: sz, color, name: 'Calibri' });
const normFont = (sz = 10, color = { argb: 'FF000000' }) => ({ bold: false, size: sz, color, name: 'Calibri' });
const centerAlign = { horizontal: 'center', vertical: 'middle' };
const leftAlign   = { horizontal: 'left',   vertical: 'middle' };

/**
 * Human block title in the research-team style "CE ATM | No Adj" /
 * "CE 2% ITM | Rise 1%". Left = primary option leg (CE/PE + strike);
 * right = NIFTY spot-adjustment label.
 */
export const buildWowMomTitle = (config) => {
  if (!config) return 'Strategy';
  const legs = config.legs || [];
  const optLeg = legs.find(l => l.segment !== 'midcap100' && l.segment !== 'futures' && l.option_type) || legs[0];
  let cepe = '', strike = '';
  if (optLeg) {
    const o = (optLeg.option_type || '').toLowerCase();
    cepe = o === 'call' ? 'CE' : o === 'put' ? 'PE' : (o ? o.toUpperCase() : '');
    const criteria = optLeg.strike_criteria || 'strike_type';
    if (criteria === 'pct_of_atm') {
      const pctVal = optLeg.pct_value != null ? parseFloat(optLeg.pct_value) : 0;
      if (!pctVal) strike = 'ATM';
      else {
        const moneyness = (optLeg.pct_atm_moneyness || 'OTM').toUpperCase();
        const pctStr = Number.isInteger(pctVal) ? String(pctVal) : parseFloat(pctVal.toFixed(2)).toString();
        strike = `${pctStr}% ${moneyness}`;
      }
    } else if (criteria === 'atm_straddle_prem_pct') {
      strike = 'STRADDLE';
    } else {
      strike = (optLeg.strike_type || 'ATM').toUpperCase();
    }
  }
  let adj = 'No Adj';
  if (config.spotAdjustmentEnabled) {
    const dir = config.spotAdjustmentDirection || 'rise';
    const val = config.spotAdjustmentValue;
    const unit = (config.spotAdjustmentUnits || 'percent') === 'percent' ? '%' : 'pts';
    const valStr = val != null ? (Number.isInteger(val) ? String(val) : parseFloat(Number(val).toFixed(2)).toString()) : '';
    const word = dir === 'both' ? 'Rise or Fall' : dir === 'fall' ? 'Fall' : 'Rise';
    adj = `${word}${valStr ? ` ${valStr}${unit}` : ''}`;
  }
  const left = [cepe, strike].filter(Boolean).join(' ') || 'Strategy';
  return `${left} | ${adj}`;
};

/**
 * Append a "WOW & MOM Summary" worksheet (WOW table on top, MOM below) to `wb`.
 * @param {ExcelJS.Workbook} wb
 * @param {Array<Object>} cleanedTrades  per-leg trade rows (trade-level values
 *                                       on the first leg only).
 * @param {Object} opts {hasMidcap:boolean, title:string, ddIsPercent?:boolean}
 *   ddIsPercent — whether the dd field (%DD / Combined %DD) is a percentage
 *   NUMBER (e.g. -9.20 meaning "-9.20%") rather than a decimal fraction
 *   (-0.092). Both frontend callers' %DD / Combined %DD are percent-number
 *   scale (engine's row_pct_dd = pct_dd*100; buildTradeExcel.js's
 *   (dd/peak)*100), so this defaults to true — pass false only if a future
 *   caller's cleaned rows genuinely hold a decimal fraction.
 * @returns {boolean} true if a sheet was written (≥1 trade), false otherwise.
 */
export function writeWowMomSheet(wb, cleanedTrades, { hasMidcap = false, title = 'Strategy', ddIsPercent = true } = {}) {
  const wmRetField = hasMidcap ? 'Combined Net P&L %' : '% P&L';
  const wmDdField  = hasMidcap ? 'Combined %DD'       : '%DD';
  const wmLiveField = hasMidcap ? 'Combined Actual Live DD' : 'Actual Live DD';
  const wmDdIsPercent = ddIsPercent;
  const wm = buildWowMom(cleanedTrades, { retField: wmRetField, ddField: wmDdField, ddIsPercent: wmDdIsPercent, liveField: wmLiveField });
  if (!(wm.nTrades > 0)) return false;

  const PCT = '0.00%', RAT = '0.00', K = '0.0000', INT = '0';
  const STAT_LBL = ['Win %', 'Win avg', 'Loss %', 'Loss Avg', 'Expectancy',
                    'No. of Trades', 'Sharpe', 'Sortino', 'K1', 'K2', 'K3', 'SQN', 'CAGR'];
  const STAT_FMT = [PCT, PCT, PCT, PCT, K, INT, RAT, RAT, K, K, K, RAT, PCT];
  const statVals = (m, n) => (m
    ? [m.wp, m.wa, m.lp, m.la, m.exp, n, m.sh, m.so, m.k1, m.k2, m.k3, m.sqn, m.cg]
    : [null, null, null, null, null, n, null, null, null, null, null, null, null]);
  const wmTitle = title;
  const signFill = v => (v == null ? null : (v >= 0 ? C.greenBg : C.redBg));
  const signTx   = v => (v == null ? undefined : (v >= 0 ? C.greenTx : C.redTx));
  const BLK = { style: 'medium', color: { argb: 'FF000000' } };

  const ws = wb.addWorksheet('WOW & MOM Summary', { views: [{ state: 'frozen', xSplit: 1 }] });
  const hCell = (r, c, val, o = {}) => {
    const cell = ws.getRow(r).getCell(c);
    cell.value = val;
    cell.font = boldFont(o.size || 9, o.tx || C.headerTx);
    cell.fill = { type: 'pattern', pattern: 'solid', fgColor: o.bg || C.headerBg };
    cell.alignment = o.align || centerAlign;
    cell.border = thinBorder();
    return cell;
  };
  const vCell = (r, c, val, fmt, o = {}) => {
    const cell = ws.getRow(r).getCell(c);
    if (val && typeof val === 'object' && 'formula' in val) cell.value = val;
    else cell.value = (val == null ? '' : val);
    cell.font = normFont(o.size || 9, o.tx);
    if (fmt) cell.numFmt = fmt;
    cell.alignment = centerAlign;
    cell.border = thinBorder();
    if (o.bg) cell.fill = { type: 'pattern', pattern: 'solid', fgColor: o.bg };
    return cell;
  };
  const addr = (r, c) => ws.getRow(r).getCell(c).address;

  const writeStatHeader = (baseRow, titleMergeCols, statCol0, stats) => {
    hCell(baseRow, 1, wmTitle, { bg: C.navyBg, tx: C.navyText, size: 10, align: leftAlign });
    ws.mergeCells(baseRow, 1, baseRow, titleMergeCols);
    hCell(baseRow + 1, 1, '', { bg: C.navyBg });
    ws.mergeCells(baseRow + 1, 1, baseRow + 1, titleMergeCols);
    const vals = statVals(stats, wm.nTrades);
    STAT_LBL.forEach((lbl, i) => hCell(baseRow, statCol0 + i, lbl, { bg: C.sectionBg }));
    vals.forEach((v, i) => vCell(baseRow + 1, statCol0 + i, v, STAT_FMT[i], { bg: C.labelBg }));
  };
  const avg = a => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);

  // ══ WOW table (top) ════════════════════════════════════════════════════
  const nw = wm.nWeeks;
  const wTotCol = 2 + nw, wMddCol = 3 + nw, wLiveCol = 4 + nw, wRmddCol = 5 + nw;
  const { ew: mew } = getMonthMaps(wm.wowYears, nw);
  const { sw: msw } = getMonthMaps(wm.wowYears, nw);
  const monthEnds = new Set(Object.values(mew));
  const withMonthEdge = (w) => monthEnds.has(w) ? { ...thinBorder(), right: BLK } : null;

  writeStatHeader(1, 5, 7, computeRatios(flatWeekly(wm.wow), WOW_RF, WOW_NANN));
  const wHdr = 3, wMonthRow = 4, wData0 = 5;

  hCell(wHdr, 1, 'Year');
  for (let w = 1; w <= nw; w++) {
    const c = hCell(wHdr, 1 + w, `W${w}`, { size: 7 });
    const e = withMonthEdge(w); if (e) c.border = e;
  }
  [[wTotCol, 'Total'], [wMddCol, 'Max DD'], [wLiveCol, 'Live DD %'], [wRmddCol, 'R/MDD']].forEach(([c, h]) => {
    hCell(wHdr, c, h); hCell(wMonthRow, c, ''); ws.mergeCells(wHdr, c, wMonthRow, c);
  });

  hCell(wMonthRow, 1, 'Month', { bg: C.subHdrBg, tx: C.subHdrTx, size: 8 });
  for (let w = 1; w <= nw; w++) {
    const c = hCell(wMonthRow, 1 + w, '', { bg: C.subHdrBg });
    const e = withMonthEdge(w); if (e) c.border = e;
  }
  WM_MONTHS.forEach((mn, mi) => {
    const startW = msw[mi + 1] || 1;
    if (startW >= 1 && startW <= nw) {
      const cell = ws.getRow(wMonthRow).getCell(1 + startW);
      cell.value = mn; cell.font = boldFont(8, C.subHdrTx); cell.alignment = centerAlign;
    }
  });

  let r = wData0;
  const wTots = [], wMdds = [], wRmdds = [], wLives = [];
  wm.wowYears.forEach(yr => {
    hCell(r, 1, yr, { bg: C.labelBg, tx: C.subHdrTx });
    let tot = 0, cnt = 0;
    for (let w = 1; w <= nw; w++) {
      const val = (wm.wow[yr] || {})[w];
      const cell = (val != null)
        ? vCell(r, 1 + w, Number(val.toFixed(6)), PCT, { size: 7, bg: signFill(val), tx: signTx(val) })
        : vCell(r, 1 + w, '', PCT, { size: 7 });
      if (val != null) { tot += val; cnt += 1; }
      const e = withMonthEdge(w); if (e) cell.border = e;
    }
    const first = addr(r, 2), last = addr(r, 1 + nw);
    vCell(r, wTotCol, cnt
      ? { formula: `IF(COUNT(${first}:${last})=0,"",SUM(${first}:${last}))`, result: Number(tot.toFixed(6)) }
      : '', PCT, { bg: C.labelBg, tx: signTx(cnt ? tot : null) });

    const dd = wowYearDrawdown(wm.wow[yr] || {}, nw);
    vCell(r, wMddCol, dd.maxdd != null ? dd.maxdd : '', PCT, { bg: C.redBg, tx: C.redTx });
    const live = (wm.wowLive || {})[yr];
    vCell(r, wLiveCol, live != null ? Number(live.toFixed(6)) : '', PCT,
      { bg: (live != null && live < 0) ? C.redBg : C.labelBg, tx: signTx(live) });
    const tAddr = addr(r, wTotCol), dAddr = addr(r, wMddCol);
    const rmdd = (cnt && dd.maxdd) ? tot / Math.abs(dd.maxdd) : null;
    vCell(r, wRmddCol, rmdd != null
      ? { formula: `IFERROR(IF(${dAddr}=0,"",${tAddr}/${dAddr}*-1),"")`, result: Number(rmdd.toFixed(2)) }
      : '', RAT, { bg: signFill(rmdd), tx: signTx(rmdd) });
    if (cnt) wTots.push(tot);
    if (dd.maxdd != null) wMdds.push(dd.maxdd);
    if (rmdd != null) wRmdds.push(rmdd);
    if (live != null) wLives.push(live);
    if (dd.startWeek != null) {
      for (let w = dd.startWeek; w <= dd.endWeek; w++) {
        const cell = ws.getRow(r).getCell(1 + w);
        const b = { top: BLK, bottom: BLK };
        if (w === dd.startWeek) b.left = BLK;
        if (w === dd.endWeek) b.right = BLK;
        cell.border = b;
      }
    }
    r += 1;
  });

  const wDataR1 = r - 1;
  hCell(r, 1, 'Total');
  if (wDataR1 >= wData0) {
    const tSum = wTots.reduce((x, y) => x + y, 0);
    vCell(r, wTotCol, { formula: `IFERROR(SUM(${addr(wData0, wTotCol)}:${addr(wDataR1, wTotCol)}),"")`, result: Number(tSum.toFixed(6)) }, PCT, { bg: signFill(tSum), tx: signTx(tSum) });
    vCell(r, wMddCol, { formula: `IFERROR(AVERAGE(${addr(wData0, wMddCol)}:${addr(wDataR1, wMddCol)}),"")`, result: Number((avg(wMdds) || 0).toFixed(6)) }, PCT, { bg: C.redBg, tx: C.redTx });
    const wLiveAvg = avg(wLives);
    vCell(r, wLiveCol, wLiveAvg != null
      ? { formula: `IFERROR(AVERAGE(${addr(wData0, wLiveCol)}:${addr(wDataR1, wLiveCol)}),"")`, result: Number(wLiveAvg.toFixed(6)) }
      : '', PCT, { bg: (wLiveAvg != null && wLiveAvg < 0) ? C.redBg : C.labelBg, tx: signTx(wLiveAvg) });
    vCell(r, wRmddCol, { formula: `IFERROR(AVERAGE(${addr(wData0, wRmddCol)}:${addr(wDataR1, wRmddCol)}),"")`, result: Number((avg(wRmdds) || 0).toFixed(2)) }, RAT, { bg: signFill(avg(wRmdds)), tx: signTx(avg(wRmdds)) });
  }

  // ══ MOM table (below, 2-row gap) ═══════════════════════════════════════
  const momBase = r + 2;
  writeStatHeader(momBase, 4, 6, computeRatios(flatMonthly(wm.mom), MOM_RF, MOM_NANN));
  const mHdr = momBase + 2, mData0 = momBase + 3;
  ['Year', ...WM_MONTHS, 'Total', 'Max DD', 'Live DD %', 'R/MDD'].forEach((h, i) => hCell(mHdr, 1 + i, h, { size: 8 }));

  let mr = mData0;
  wm.momYears.forEach(yr => {
    const yd = wm.mom[yr] || { months: {}, total: null, maxdd: null, livedd: null };
    hCell(mr, 1, yr, { bg: C.labelBg, tx: C.subHdrTx });
    WM_MONTHS.forEach((mn, mi) => {
      const val = yd.months[mn];
      if (val != null) vCell(mr, 2 + mi, Number(val.toFixed(6)), PCT, { bg: signFill(val), tx: signTx(val) });
      else vCell(mr, 2 + mi, '', PCT);
    });
    const tot = yd.total, mdd = yd.maxdd, live = yd.livedd;
    const bAddr = addr(mr, 2), mAddr = addr(mr, 13);
    vCell(mr, 14, tot != null
      ? { formula: `IF(COUNT(${bAddr}:${mAddr})=0,"",SUM(${bAddr}:${mAddr}))`, result: Number(tot.toFixed(6)) }
      : '', PCT, { bg: signFill(tot), tx: signTx(tot) });
    vCell(mr, 15, mdd != null ? Number(mdd.toFixed(6)) : '', PCT, { bg: C.redBg, tx: C.redTx });
    vCell(mr, 16, live != null ? Number(live.toFixed(6)) : '', PCT,
      { bg: (live != null && live < 0) ? C.redBg : C.labelBg, tx: signTx(live) });
    const nAddr = addr(mr, 14), oAddr = addr(mr, 15);
    const rmdd = (tot != null && mdd) ? tot / Math.abs(mdd) : null;
    vCell(mr, 17, (tot != null && mdd)
      ? { formula: `IFERROR(IF(${oAddr}=0,"",${nAddr}/${oAddr}*-1),"")`, result: Number(rmdd.toFixed(2)) }
      : '', RAT, { bg: signFill(rmdd), tx: signTx(rmdd) });
    mr += 1;
  });

  const mDataR1 = mr - 1;
  hCell(mr, 1, 'Total');
  for (let mi = 0; mi < 12; mi++) hCell(mr, 2 + mi, '', { bg: C.headerBg });
  if (mDataR1 >= mData0) {
    const totsArr = wm.momYears.map(y => wm.mom[y]?.total).filter(v => v != null);
    const mddArr  = wm.momYears.map(y => wm.mom[y]?.maxdd).filter(v => v != null);
    const liveArr = wm.momYears.map(y => wm.mom[y]?.livedd).filter(v => v != null);
    const rmddArr = wm.momYears.map(y => { const t = wm.mom[y]?.total, d = wm.mom[y]?.maxdd; return (t != null && d) ? t / Math.abs(d) : null; }).filter(v => v != null);
    const nSum = totsArr.reduce((x, y) => x + y, 0);
    vCell(mr, 14, { formula: `IFERROR(SUM(${addr(mData0, 14)}:${addr(mDataR1, 14)}),"")`, result: Number(nSum.toFixed(6)) }, PCT, { bg: signFill(nSum), tx: signTx(nSum) });
    vCell(mr, 15, { formula: `IFERROR(AVERAGE(${addr(mData0, 15)}:${addr(mDataR1, 15)}),"")`, result: Number((avg(mddArr) || 0).toFixed(6)) }, PCT, { bg: C.redBg, tx: C.redTx });
    const mLiveAvg = avg(liveArr);
    vCell(mr, 16, mLiveAvg != null
      ? { formula: `IFERROR(AVERAGE(${addr(mData0, 16)}:${addr(mDataR1, 16)}),"")`, result: Number(mLiveAvg.toFixed(6)) }
      : '', PCT, { bg: (mLiveAvg != null && mLiveAvg < 0) ? C.redBg : C.labelBg, tx: signTx(mLiveAvg) });
    vCell(mr, 17, { formula: `IFERROR(AVERAGE(${addr(mData0, 17)}:${addr(mDataR1, 17)}),"")`, result: Number((avg(rmddArr) || 0).toFixed(2)) }, RAT, { bg: signFill(avg(rmddArr)), tx: signTx(avg(rmddArr)) });
  }

  ws.getColumn(1).width = 16;
  for (let w = 1; w <= nw; w++) ws.getColumn(1 + w).width = 7;
  ws.getColumn(wTotCol).width = 8;
  ws.getColumn(wMddCol).width = 8;
  ws.getColumn(wLiveCol).width = 8;
  ws.getColumn(wRmddCol).width = 8;
  return true;
}
