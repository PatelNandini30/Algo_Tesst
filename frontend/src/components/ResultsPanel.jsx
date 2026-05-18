import React, { useMemo, useState, useEffect } from 'react';
import {
  AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine
} from 'recharts';
import { Download, X } from 'lucide-react';
import ExcelJS from 'exceljs';

const EXIT_REASON_COLORS = {
  Expiry:                           { bg: '#f1f5f9', text: '#475569', border: '#cbd5e1' },
  STOP_LOSS:                        { bg: 'var(--loss-bg)', text: 'var(--loss)', border: 'var(--loss-border)' },
  TARGET:                           { bg: 'var(--profit-bg)', text: 'var(--profit)', border: 'var(--profit-border)' },
  TRAIL_SL:                         { bg: 'var(--warning-bg)', text: 'var(--warning)', border: 'rgba(245, 158, 11, 0.3)' },
  STR_Exit:                         { bg: 'rgba(147, 51, 234, 0.1)', text: '#7e22ce', border: 'rgba(147, 51, 234, 0.25)' },
  FILTER_END:                       { bg: 'var(--accent-bg)', text: 'var(--accent)', border: 'rgba(37, 99, 235, 0.25)' },
  FUT_ROLL_ON_EXPIRY:               { bg: '#e0f2fe', text: '#0369a1', border: '#bae6fd' },
  FUT_ROLL_N_DAYS_BEFORE_EXPIRY:    { bg: '#e0f2fe', text: '#0369a1', border: '#bae6fd' },
  FUT_ROLL_LAST_WEEK_BEFORE_EXPIRY: { bg: '#e0f2fe', text: '#0369a1', border: '#bae6fd' },
  default:                          { bg: 'var(--bg-hover)', text: 'var(--text-secondary)', border: 'var(--border-strong)' }
};

const renderExitReasonBadge = (reason) => {
  if (!reason) {
    return <span className="text-xs text-muted">—</span>;
  }
  const colorInfo = EXIT_REASON_COLORS[reason] || EXIT_REASON_COLORS.default;
  return (
    <span
      className="px-2 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wide border"
      style={{ backgroundColor: colorInfo.bg, color: colorInfo.text, borderColor: colorInfo.border }}
    >
      {reason}
    </span>
  );
};

const formatDateToDdMmYyyy = (value) => {
  if (!value && value !== 0) return value;
  const str = String(value).trim();
  if (!str) return value;
  if (str.includes('/')) {
    const [day, month, year] = str.split('/');
    if (year && month && day && year.length === 4) {
      return `${day.padStart(2, '0')}-${month.padStart(2, '0')}-${year}`;
    }
  }
  if (str.includes('-')) {
    const parts = str.split('-');
    if (parts.length === 3) {
      const [p0, p1, p2] = parts;
      if (p0.length === 4) {
        return `${p2.padStart(2, '0')}-${p1.padStart(2, '0')}-${p0}`;
      }
      if (p2.length === 4) {
        return `${p0.padStart(2, '0')}-${p1.padStart(2, '0')}-${p2}`;
      }
    }
  }
  const parsed = new Date(str);
  if (!Number.isNaN(parsed.getTime())) {
    const d = parsed.getDate().toString().padStart(2, '0');
    const m = (parsed.getMonth() + 1).toString().padStart(2, '0');
    const y = parsed.getFullYear();
    return `${d}-${m}-${y}`;
  }
  return value;
};

const IntradayFullReport = ({ rows, onClose, showCloseButton }) => {
  const [sortDir, setSortDir] = useState('asc');
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 10;

  if (rows.length === 0) {
    return (
      <div className="bg-surface rounded-lg border border-default shadow-sm p-6 text-center text-secondary text-sm">
        {showCloseButton && onClose && (
          <button onClick={onClose} className="float-right text-muted hover:text-primary"><X size={16} /></button>
        )}
        No trades found for the selected date range and strategy.
      </div>
    );
  }

  // Group by date for AlgoTest-style hierarchical Index
  const byDate = useMemo(() => {
    const m = new Map();
    rows.forEach(r => {
      const d = r.date;
      if (!m.has(d)) m.set(d, []);
      m.get(d).push(r);
    });
    return m;
  }, [rows]);

  const sortedDates = useMemo(() => {
    const dates = Array.from(byDate.keys()).sort();
    return sortDir === 'asc' ? dates : dates.reverse();
  }, [byDate, sortDir]);

  // Day-level P&L aggregates (stats + charts are per trade-day, not per leg)
  const dayPnls = useMemo(() => {
    const dMap = new Map();
    rows.forEach(r => {
      if (!dMap.has(r.date)) dMap.set(r.date, 0);
      dMap.set(r.date, dMap.get(r.date) + (Number(r.pnl) || 0));
    });
    return Array.from(dMap.values());
  }, [rows]);

  const totalPnl = rows.reduce((s, r) => s + (Number(r.pnl) || 0), 0);
  const tradeDays = byDate.size;
  const winners = dayPnls.filter(p => p > 0);
  const losers  = dayPnls.filter(p => p <= 0);
  const winRate = tradeDays > 0 ? (winners.length / tradeDays * 100).toFixed(1) : '0';
  const avgWin  = winners.length > 0 ? (winners.reduce((s, p) => s + p, 0) / winners.length).toFixed(2) : '0';
  const avgLoss = losers.length  > 0 ? (losers.reduce( (s, p) => s + p, 0) / losers.length ).toFixed(2) : '0';

  // Equity curve + drawdown — one point per trade day (date on x-axis)
  const dayChartData = useMemo(() => {
    const dMap = new Map();
    rows.forEach(r => {
      if (!dMap.has(r.date)) dMap.set(r.date, 0);
      dMap.set(r.date, dMap.get(r.date) + (Number(r.pnl) || 0));
    });
    const dates = Array.from(dMap.keys()).sort();
    let cum = 0, pk = 0;
    return dates.map(date => {
      const net = Math.round(dMap.get(date) * 100) / 100;
      cum = Math.round((cum + net) * 100) / 100;
      if (cum > pk) pk = cum;
      const ddPct = pk > 0 ? parseFloat(((pk - cum) / pk * 100).toFixed(2)) : 0;
      return { date, net, cumulative: cum, drawdown: -ddPct };
    });
  }, [rows]);

  const maxDDPct = Math.max(0, ...dayChartData.map(d => -d.drawdown));

  // Monthly returns
  const monthlyData = useMemo(() => {
    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const byYM = {};
    rows.forEach(r => {
      const parts = (r.date || '').split('-');
      if (parts.length !== 3) return;
      const yr = parts[0]; const mm = parseInt(parts[1], 10) - 1;
      if (!byYM[yr]) byYM[yr] = Array(12).fill(0);
      byYM[yr][mm] = Math.round((byYM[yr][mm] + (Number(r.pnl) || 0)) * 100) / 100;
    });
    return Object.entries(byYM).sort().map(([yr, mos]) => ({
      year: yr, months: mos,
      total: Math.round(mos.reduce((s, v) => s + v, 0) * 100) / 100,
    }));
  }, [rows]);

  const equityDomain = useMemo(() => {
    const vals = dayChartData.map(d => d.cumulative);
    if (!vals.length) return ['auto', 'auto'];
    const min = Math.min(...vals); const max = Math.max(...vals);
    const pad = (max - min) * 0.05 || 10;
    return [parseFloat((min - pad).toFixed(2)), parseFloat((max + pad).toFixed(2))];
  }, [dayChartData]);

  const fmtDateShort = (d) => {
    if (!d) return '';
    const p = String(d).split('-');
    if (p.length !== 3) return d;
    const M = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return `${M[parseInt(p[1], 10) - 1] || p[1]} '${p[0].slice(2)}`;
  };

  const IntradayTooltip = ({ active, payload, label }) => {
    if (!active || !payload || !payload.length) return null;
    return (
      <div className="bg-base border border-strong rounded-lg p-3 shadow-xl text-xs">
        <p className="text-muted mb-1">{label}</p>
        {payload.map((e, i) => (
          <p key={i} style={{ color: e.stroke || e.fill }} className="font-semibold">
            {e.name}: {e.dataKey === 'drawdown' ? `${Number(e.value).toFixed(2)}%` : `₹${Number(e.value).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
          </p>
        ))}
      </div>
    );
  };

  // Build hierarchical report rows: day-summary + leg-detail
  // Index numbering ALWAYS starts at 1 for first day, regardless of sort
  const reportRows = useMemo(() => {
    const out = [];
    sortedDates.forEach((date, dayIdx) => {
      const legs = byDate.get(date);
      const dayPnl = legs.reduce((s, l) => s + (Number(l.pnl) || 0), 0);
      const dayQty = legs.reduce((s, l) => s + (Number(l.quantity) || 0), 0);
      const earliestEntry = legs.reduce((acc, l) => (!acc || (l.entry_time && l.entry_time < acc) ? l.entry_time : acc), null);
      const latestExit    = legs.reduce((acc, l) => (!acc || (l.exit_time  && l.exit_time  > acc) ? l.exit_time  : acc), null);
      out.push({
        kind: 'day',
        index: `${dayIdx + 1}`,
        entryDate: date, exitDate: date,
        entryTime: earliestEntry, exitTime: latestExit,
        qty: dayQty,
        pnl: dayPnl,
      });
      legs.forEach((leg, legIdx) => {
        out.push({
          kind: 'leg',
          index: `${dayIdx + 1}.${legIdx + 1}`,
          entryDate: date, exitDate: date,
          entryTime: leg.entry_time, exitTime: leg.exit_time,
          type: leg.opt_type,
          strike: leg.strike,
          bs: leg.action,
          qty: leg.quantity,
          entryPrice: leg.entry_price,
          exitPrice: leg.exit_price,
          exitReason: leg.exit_reason,
          pnl: leg.pnl,
        });
      });
    });
    return out;
  }, [sortedDates, byDate]);

  // Pagination operates over DAYS, not rows — so a multi-leg day stays together
  const totalDays = sortedDates.length;
  const totalPages = Math.max(1, Math.ceil(totalDays / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const startDayIdx = (safePage - 1) * PAGE_SIZE;
  const endDayIdx = Math.min(startDayIdx + PAGE_SIZE, totalDays);
  const visibleDates = new Set(sortedDates.slice(startDayIdx, endDayIdx));
  const visibleRows = reportRows.filter(r => visibleDates.has(r.entryDate));

  const tradeCountStart = startDayIdx + 1;
  const tradeCountEnd = endDayIdx;

  const exportToExcel = async () => {
    if (rows.length === 0) return;

    const sorted = [...rows].sort((a, b) => {
      const d = String(a.date || '').localeCompare(String(b.date || ''));
      return d !== 0 ? d : String(a.entry_time || '').localeCompare(String(b.entry_time || ''));
    });

    const dateMap = new Map();
    sorted.forEach(r => {
      if (!dateMap.has(r.date)) dateMap.set(r.date, []);
      dateMap.get(r.date).push(r);
    });

    const sortedDates = Array.from(dateMap.keys()).sort();
    let cumPnl = 0, runPeak = 0;
    const dayStats = sortedDates.map((date, dayIdx) => {
      const legs = dateMap.get(date);
      const netPnl = Math.round(legs.reduce((s, r) => s + (Number(r.pnl) || 0), 0) * 100) / 100;
      cumPnl = cumPnl + netPnl;
      if (cumPnl > runPeak) runPeak = cumPnl;
      const dd    = runPeak - cumPnl;
      const pctDd = runPeak > 0 ? (dd / runPeak) * 100 : 0;
      return { date, dayIdx, legs, netPnl, cumPnl, peak: runPeak, dd, pctDd };
    });

    // ─── Palette ────────────────────────────────────────────────────────────
    const C = {
      navyBg:   { argb: 'FF1F3864' }, navyText: { argb: 'FFFFFFFF' },
      sectionBg:{ argb: 'FF2C5F8A' }, sectionTx:{ argb: 'FFFFFFFF' },
      headerBg: { argb: 'FF34495E' }, headerTx: { argb: 'FFFFFFFF' },
      subHdrBg: { argb: 'FFD6E4F7' }, subHdrTx: { argb: 'FF1F3864' },
      greenBg:  { argb: 'FFD4EFDF' }, greenTx:  { argb: 'FF1E7E34' },
      redBg:    { argb: 'FFFDE8E8' }, redTx:    { argb: 'FFC0392B' },
      labelBg:  { argb: 'FFF2F6FA' }, altRow:   { argb: 'FFF9FBFD' },
      border:   { argb: 'FFB0C4D8' }, white:    { argb: 'FFFFFFFF' },
    };
    const thinBorder = (color = C.border) => ({
      top: { style: 'thin', color }, left: { style: 'thin', color },
      bottom: { style: 'thin', color }, right: { style: 'thin', color },
    });
    const boldFont = (sz = 11, color = { argb: 'FF000000' }) => ({ bold: true,  size: sz, color, name: 'Calibri' });
    const normFont = (sz = 10, color = { argb: 'FF000000' }) => ({ bold: false, size: sz, color, name: 'Calibri' });
    const centerAlign = { horizontal: 'center', vertical: 'middle' };
    const leftAlign   = { horizontal: 'left',   vertical: 'middle' };

    const wb = new ExcelJS.Workbook();
    wb.creator = 'AlgoTest Backtest';
    wb.created = new Date();
    wb.calcProperties = { fullCalcOnLoad: true };

    // ════ SHEET 1: TRADE SHEET ════
    const ws1 = wb.addWorksheet('Trade Sheet', { views: [{ state: 'frozen', ySplit: 1 }] });
    const colDefs = [
      { header: 'Index',       key: 'idx',        width: 10 },
      { header: 'Entry Date',  key: 'entryDate',  width: 13 },
      { header: 'Entry Time',  key: 'entryTime',  width: 10 },
      { header: 'Exit Date',   key: 'exitDate',   width: 13 },
      { header: 'Exit Time',   key: 'exitTime',   width: 10 },
      { header: 'Expiry',      key: 'expiry',     width: 12 },
      { header: 'Type',        key: 'type',       width:  8 },
      { header: 'Strike',      key: 'strike',     width: 10 },
      { header: 'B/S',         key: 'bs',         width:  7 },
      { header: 'Qty',         key: 'qty',        width:  7 },
      { header: 'Entry Price', key: 'entryPrice', width: 12 },
      { header: 'Exit Price',  key: 'exitPrice',  width: 11 },
      { header: 'MAE',         key: 'mae',        width:  9 },
      { header: 'MFE',         key: 'mfe',        width:  9 },
      { header: 'P&L',         key: 'legPnl',     width: 10 },
      { header: 'Net P&L',     key: 'netPnl',     width: 10 },
      { header: 'Cumulative',  key: 'cumulative', width: 12 },
      { header: 'Peak',        key: 'peak',       width: 10 },
      { header: 'DD',          key: 'dd',         width: 10 },
      { header: '%DD',         key: 'pctDd',      width:  9 },
      { header: 'Exit Reason', key: 'exitReason', width: 14 },
    ];
    ws1.columns = colDefs.map(c => ({ key: c.key, width: c.width }));

    const hdrRow = ws1.addRow(colDefs.map(c => c.header));
    hdrRow.eachCell(cell => {
      cell.font = boldFont(10, C.navyText);
      cell.fill = { type: 'pattern', pattern: 'solid', fgColor: C.headerBg };
      cell.alignment = centerAlign;
      cell.border = thinBorder();
    });
    hdrRow.height = 22;

    const legPnlCol = colDefs.findIndex(c => c.key === 'legPnl') + 1;
    const netPnlCol = colDefs.findIndex(c => c.key === 'netPnl') + 1;
    const n = v => (v != null && v !== '' && Number.isFinite(Number(v))) ? Math.round(Number(v) * 100) / 100 : '';

    let rowIdx = 0;
    dayStats.forEach(({ date, dayIdx, legs, netPnl, cumPnl: cum, peak: pk, dd, pctDd }) => {
      legs.forEach((leg, legIdx) => {
        const first = legIdx === 0;
        const pnlV = n(leg.pnl);
        const r = ws1.addRow([
          `${dayIdx + 1}.${legIdx + 1}`,
          date,
          leg.entry_time  || '',
          date,
          leg.exit_time   || '',
          leg.expiry      || '',
          leg.opt_type    || '',
          n(leg.strike),
          leg.action      || '',
          leg.quantity != null ? Number(leg.quantity) : '',
          n(leg.entry_price),
          n(leg.exit_price),
          n(leg.mae),
          n(leg.mfe),
          pnlV,
          first ? netPnl : '',
          first ? cum    : '',
          first ? pk     : '',
          first ? dd     : '',
          first ? pctDd  : '',
          leg.exit_reason || '',
        ]);
        const bg = rowIdx % 2 === 0 ? C.white : C.altRow;
        r.eachCell(cell => {
          cell.font   = normFont(10);
          cell.fill   = { type: 'pattern', pattern: 'solid', fgColor: bg };
          cell.border = thinBorder();
          cell.alignment = { vertical: 'middle' };
          if (typeof cell.value === 'number') {
            cell.numFmt = Number.isInteger(cell.value) ? '0' : '#,##0.00';
          }
        });
        if (typeof pnlV === 'number') {
          const c = r.getCell(legPnlCol);
          c.font = boldFont(10, pnlV >= 0 ? C.greenTx : C.redTx);
          c.fill = { type: 'pattern', pattern: 'solid', fgColor: pnlV >= 0 ? C.greenBg : C.redBg };
        }
        if (first && typeof netPnl === 'number') {
          const c = r.getCell(netPnlCol);
          c.font = boldFont(10, netPnl >= 0 ? C.greenTx : C.redTx);
          c.fill = { type: 'pattern', pattern: 'solid', fgColor: netPnl >= 0 ? C.greenBg : C.redBg };
        }
        rowIdx++;
      });
    });

    // ════ SHEET 2: SUMMARY ════
    const ws2 = wb.addWorksheet('Summary');
    ws2.columns = [{ width: 30 }, { width: 20 }, { width: 4 }, { width: 30 }, { width: 20 }];

    const addTitle2 = (text, rn) => {
      ws2.mergeCells(`A${rn}:E${rn}`);
      const cell = ws2.getCell(`A${rn}`);
      cell.value = text; cell.font = boldFont(13, C.navyText);
      cell.fill = { type: 'pattern', pattern: 'solid', fgColor: C.navyBg };
      cell.alignment = centerAlign; ws2.getRow(rn).height = 26;
    };
    const addSection2 = (text, rn) => {
      ws2.mergeCells(`A${rn}:E${rn}`);
      const cell = ws2.getCell(`A${rn}`);
      cell.value = '  ' + text; cell.font = boldFont(11, C.sectionTx);
      cell.fill = { type: 'pattern', pattern: 'solid', fgColor: C.sectionBg };
      cell.alignment = leftAlign; ws2.getRow(rn).height = 20;
    };
    const addKv2 = (label, value, rn, col = 'A', alt = false, vc = null) => {
      const vCol = String.fromCharCode(col.charCodeAt(0) + 1);
      const lCell = ws2.getCell(`${col}${rn}`);
      const vCell = ws2.getCell(`${vCol}${rn}`);
      lCell.value = label; vCell.value = value;
      lCell.font  = boldFont(10, { argb: 'FF2C3E50' });
      lCell.fill  = { type: 'pattern', pattern: 'solid', fgColor: alt ? C.altRow : C.labelBg };
      lCell.alignment = leftAlign; lCell.border = thinBorder(C.border);
      const numVal = typeof value === 'number' ? value : parseFloat(String(value || '').replace(/[+%₹,]/g, ''));
      const autoColor = vc || (isNaN(numVal) ? null : numVal >= 0 ? C.greenTx : C.redTx);
      vCell.font  = boldFont(10, autoColor || { argb: 'FF1A1A2E' });
      vCell.fill  = { type: 'pattern', pattern: 'solid', fgColor: alt ? C.altRow : C.white };
      vCell.alignment = leftAlign; vCell.border = thinBorder(C.border);
      ws2.getRow(rn).height = 18;
    };

    const winners  = dayStats.filter(d => d.netPnl > 0);
    const losers   = dayStats.filter(d => d.netPnl <= 0);
    const totalPnl2 = Math.round(dayStats.reduce((s, d) => s + d.netPnl, 0) * 100) / 100;
    const winRateN  = dayStats.length > 0 ? +(winners.length / dayStats.length * 100).toFixed(2) : 0;
    const lossPctN  = dayStats.length > 0 ? +(losers.length  / dayStats.length * 100).toFixed(2) : 0;
    const avgWinN   = winners.length > 0 ? +(winners.reduce((s, d) => s + d.netPnl, 0) / winners.length).toFixed(2) : 0;
    const avgLossN  = losers.length  > 0 ? +(losers.reduce( (s, d) => s + d.netPnl, 0) / losers.length ).toFixed(2) : 0;
    const maxWinN   = dayStats.length > 0 ? Math.max(...dayStats.map(d => d.netPnl)) : 0;
    const maxLossN  = dayStats.length > 0 ? Math.min(...dayStats.map(d => d.netPnl)) : 0;
    const maxDdN    = dayStats.length > 0 ? Math.max(...dayStats.map(d => d.dd))     : 0;
    const maxDdPctN = dayStats.length > 0 ? Math.max(...dayStats.map(d => d.pctDd))  : 0;
    let maxWinStreak = 0, maxLossStreak = 0, curW = 0, curL = 0;
    dayStats.forEach(d => {
      if (d.netPnl > 0) { curW++; maxWinStreak  = Math.max(maxWinStreak,  curW); curL = 0; }
      else               { curL++; maxLossStreak = Math.max(maxLossStreak, curL); curW = 0; }
    });

    addTitle2('  INTRADAY BACKTEST SUMMARY', 1);
    ws2.mergeCells('A2:E2');
    const subCell2 = ws2.getCell('A2');
    subCell2.value = `Generated: ${new Date().toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}`;
    subCell2.font  = normFont(10, { argb: 'FF555555' });
    subCell2.alignment = centerAlign;
    subCell2.fill  = { type: 'pattern', pattern: 'solid', fgColor: C.subHdrBg };
    ws2.getRow(2).height = 16;

    let sRow = 4;
    addSection2('PERFORMANCE OVERVIEW', sRow++);
    addKv2('Total P&L',             `₹${totalPnl2.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`, sRow, 'A', false, totalPnl2 >= 0 ? C.greenTx : C.redTx);
    addKv2('No. of Trade Days',     dayStats.length,   sRow++, 'D', false, { argb: 'FF1A1A2E' });
    addKv2('Win %',                 `${winRateN}%`,    sRow,   'A', true,  C.greenTx);
    addKv2('Loss %',                `${lossPctN}%`,    sRow++, 'D', true,  C.redTx);
    addKv2('Avg Profit on Winners', `₹${avgWinN.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`,  sRow,   'A', false, C.greenTx);
    addKv2('Avg Loss on Losers',    `₹${avgLossN.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`, sRow++, 'D', false, C.redTx);
    addKv2('Max Profit (Single Day)', `₹${maxWinN.toLocaleString('en-IN',  { minimumFractionDigits: 2 })}`, sRow,   'A', true, C.greenTx);
    addKv2('Max Loss (Single Day)',   `₹${maxLossN.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`, sRow++, 'D', true, C.redTx);
    sRow++;

    addSection2('RISK METRICS', sRow++);
    addKv2('Max Drawdown',          `₹${maxDdN.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`, sRow,   'A', false, C.redTx);
    addKv2('Max Drawdown %',        `${maxDdPctN.toFixed(2)}%`, sRow++, 'D', false, C.redTx);
    sRow++;

    addSection2('CONSISTENCY & STREAKS', sRow++);
    addKv2('Max Win Streak',        `${maxWinStreak} days`,  sRow,   'A', false, C.greenTx);
    addKv2('Max Losing Streak',     `${maxLossStreak} days`, sRow++, 'D', false, C.redTx);
    sRow++;

    addSection2('MONTHLY RETURNS (₹ Net P&L)', sRow++);
    const MONTHS2 = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const mthHdr2 = ['Year', ...MONTHS2, 'Total'];
    for (let ci = 0; ci < mthHdr2.length; ci++) {
      ws2.getColumn(ci + 1).width = ci === 0 ? 8 : ci <= 12 ? 9 : 10;
    }
    const mHdrRow2 = ws2.getRow(sRow);
    mthHdr2.forEach((h, ci) => {
      const cell = mHdrRow2.getCell(ci + 1);
      cell.value = h; cell.font = boldFont(10, C.navyText);
      cell.fill = { type: 'pattern', pattern: 'solid', fgColor: C.headerBg };
      cell.alignment = centerAlign; cell.border = thinBorder();
    });
    mHdrRow2.height = 20;
    sRow++;

    const byYM = {};
    dayStats.forEach(({ date, netPnl: np }) => {
      const parts = date.split('-');
      if (parts.length !== 3) return;
      const yr = parts[0].length === 4 ? parts[0] : parts[2];
      const mm = parseInt(parts[0].length === 4 ? parts[1] : parts[1], 10) - 1;
      if (!byYM[yr]) byYM[yr] = Array(12).fill(0);
      byYM[yr][mm] = Math.round((byYM[yr][mm] + np) * 100) / 100;
    });
    Object.entries(byYM).sort().forEach(([yr, mos], ri) => {
      const total = Math.round(mos.reduce((s, v) => s + v, 0) * 100) / 100;
      const r2 = ws2.getRow(sRow);
      [yr, ...mos, total].forEach((val, ci) => {
        const cell = r2.getCell(ci + 1);
        cell.value = val;
        const num = typeof val === 'number' ? val : parseFloat(String(val || '').replace(/[%,]/g, ''));
        const isValCol = ci >= 1 && ci <= 13;
        if (isValCol && !isNaN(num) && num !== 0) {
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
        cell.border = thinBorder();
      });
      r2.height = 18;
      sRow++;
    });

    const buffer = await wb.xlsx.writeBuffer();
    const blob = new Blob([buffer], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `intraday_backtest_${new Date().toISOString().split('T')[0]}.xlsx`;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="animate-results">
      {showCloseButton && onClose && (
        <button onClick={onClose} className="float-right p-1 rounded transition-colors" style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
          onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}><X size={15} /></button>
      )}
      <div className="mb-4 flex items-center gap-2">
        <span style={{ fontFamily: 'Outfit, sans-serif', fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)', borderLeft: '2px solid var(--accent)', paddingLeft: '8px' }}>Intraday Results</span>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-3 gap-2 mb-5 sm:grid-cols-6">
        {[
          { label: 'Total P&L',  value: `₹${totalPnl.toFixed(2)}`, color: totalPnl >= 0 ? 'var(--profit)' : 'var(--loss)' },
          { label: 'Trade Days', value: tradeDays, color: 'var(--text-primary)' },
          { label: 'Win Rate',   value: `${winRate}%`, color: Number(winRate) >= 50 ? 'var(--profit)' : 'var(--loss)' },
          { label: 'Avg Win',    value: `₹${avgWin}`, color: 'var(--profit)' },
          { label: 'Avg Loss',   value: `₹${avgLoss}`, color: 'var(--loss)' },
          { label: 'Max DD',     value: `${maxDDPct.toFixed(2)}%`, color: maxDDPct > 0 ? 'var(--loss)' : 'var(--text-secondary)' },
        ].map(({ label, value, color }) => (
          <div key={label} className="stat-tile">
            <div className="s-label">{label}</div>
            <div className="s-value" style={{ color }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Equity Curve */}
      <div className="chart-panel mb-4">
        <h3 className="chart-panel-title">Equity Curve (Cumulative P&L)</h3>
        <ResponsiveContainer width="100%" height={280}>
          <AreaChart data={dayChartData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
            <defs>
              <linearGradient id="intradayEquityGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="var(--chart-equity)" stopOpacity={0.15} />
                <stop offset="95%" stopColor="var(--chart-equity)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" vertical={false} />
            <XAxis dataKey="date" stroke="var(--border-default)"
              tick={{ fontSize: 10, fill: 'var(--text-secondary)' }} tickLine={false}
              tickFormatter={fmtDateShort} interval="preserveStartEnd" minTickGap={50} />
            <YAxis stroke="var(--border-default)"
              tick={{ fontSize: 10, fill: 'var(--text-secondary)' }} tickLine={false}
              tickFormatter={v => v.toFixed(0)} domain={equityDomain} />
            <Tooltip content={<IntradayTooltip />} />
            <ReferenceLine y={0} stroke="var(--border-strong)" strokeWidth={1} />
            <Area type="monotone" dataKey="cumulative" name="Cumulative P&L"
              stroke="var(--chart-equity)" strokeWidth={2}
              fill="url(#intradayEquityGrad)"
              isAnimationActive={false} connectNulls dot={false}
              baseValue={equityDomain[0]} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Drawdown Chart */}
      <div className="chart-panel mb-4">
        <h3 className="chart-panel-title">Drawdown (%)</h3>
        <ResponsiveContainer width="100%" height={220}>
          <AreaChart data={dayChartData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" vertical={false} />
            <XAxis dataKey="date" stroke="var(--border-default)"
              tick={{ fontSize: 10, fill: 'var(--text-secondary)' }} tickLine={false}
              tickFormatter={fmtDateShort} interval="preserveStartEnd" minTickGap={50} />
            <YAxis stroke="var(--border-default)"
              tick={{ fontSize: 10, fill: 'var(--text-secondary)' }} tickLine={false}
              tickFormatter={v => `${v.toFixed(1)}%`} />
            <Tooltip content={<IntradayTooltip />} />
            <ReferenceLine y={0} stroke="var(--border-strong)" strokeWidth={1} />
            <Area type="monotone" dataKey="drawdown" name="Drawdown"
              stroke="var(--chart-drawdown)" strokeWidth={1.5}
              fill="var(--loss-bg)"
              isAnimationActive={false} connectNulls dot={false} baseValue={0} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Monthly Returns */}
      {monthlyData.length > 0 && (() => {
        const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        return (
          <div className="chart-panel mb-4 overflow-x-auto">
            <h3 className="chart-panel-title">Monthly Returns (₹ Net P&L)</h3>
            <table className="w-full text-xs border-collapse" style={{ minWidth: 700 }}>
              <thead>
                <tr className="bg-base">
                  <th className="px-2 py-1.5 text-center font-semibold text-secondary border border-default">Year</th>
                  {MONTHS.map(m => (
                    <th key={m} className="px-2 py-1.5 text-center font-semibold text-secondary border border-default">{m}</th>
                  ))}
                  <th className="px-2 py-1.5 text-center font-semibold text-secondary border border-default">Total</th>
                </tr>
              </thead>
              <tbody>
                {monthlyData.map(({ year, months, total }) => (
                  <tr key={year}>
                    <td className="px-2 py-1.5 text-center font-semibold text-primary border border-default bg-hover">{year}</td>
                    {months.map((v, i) => (
                      <td key={i}
                        className={`px-2 py-1.5 text-right border border-default ${v > 0 ? 'text-profit' : v < 0 ? 'text-loss' : 'text-muted'}`}
                        style={v > 0 ? { background: 'var(--profit-bg)' } : v < 0 ? { background: 'var(--loss-bg)' } : {}}>
                        {v !== 0 ? v.toFixed(0) : '—'}
                      </td>
                    ))}
                    <td className={`px-2 py-1.5 text-right font-semibold border border-default ${total > 0 ? 'text-profit' : total < 0 ? 'text-loss' : 'text-muted'}`}>
                      {total.toFixed(0)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })()}

      {/* Full Report header — sort + range + download */}
      <div className="flex items-center justify-between flex-wrap gap-3 mb-2 pt-2 border-t border-default">
        <span className="text-base font-semibold text-primary">Full Report</span>
        <div className="flex items-center gap-3 flex-wrap text-xs">
          <span className="text-secondary">Sort By:</span>
          <span className="px-2 py-1 rounded bg-base text-primary">Entry date</span>
          <div className="inline-flex rounded border border-default overflow-hidden">
            <button
              type="button"
              onClick={() => setSortDir('asc')}
              className={`px-3 py-1 text-xs ${sortDir === 'asc' ? 'bg-accent text-white' : 'bg-surface text-secondary hover:bg-hover'}`}
            >Asc</button>
            <button
              type="button"
              onClick={() => setSortDir('desc')}
              className={`px-3 py-1 text-xs ${sortDir === 'desc' ? 'bg-accent text-white' : 'bg-surface text-secondary hover:bg-hover'}`}
            >Desc</button>
          </div>
          <span className="text-secondary">
            Showing <strong className="text-primary">{tradeCountStart}-{tradeCountEnd}</strong> trade days out of <strong className="text-primary">{totalDays}</strong>
          </span>
        </div>
      </div>

      {/* Full Report table */}
      <div className="overflow-x-auto border border-default rounded-lg">
        <table className="w-full border-collapse trading-table">
          <thead>
            <tr>
              <th className="text-center">Index</th>
              <th className="text-left">Entry Date</th>
              <th className="text-left">Entry Time</th>
              <th className="text-left">Exit Date</th>
              <th className="text-left">Exit Time</th>
              <th className="text-center">Type</th>
              <th className="text-right">Strike</th>
              <th className="text-center">B/S</th>
              <th className="text-right">Qty</th>
              <th className="text-right">Entry ₹</th>
              <th className="text-right">Exit ₹</th>
              <th className="text-right">P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((r) => {
              const isDay = r.kind === 'day';
              const pnlVal = Number(r.pnl);
              return (
                <tr key={r.index} className={isDay ? 'day-row' : ''}>
                  <td className="text-center" style={{ color: isDay ? 'var(--accent)' : 'var(--text-muted)' }}>{r.index}</td>
                  <td>{r.entryDate}</td>
                  <td>{r.entryTime || '—'}</td>
                  <td>{r.exitDate}</td>
                  <td>{r.exitTime || '—'}</td>
                  <td className="text-center" style={{ color: r.type === 'CE' ? 'var(--chart-equity)' : r.type === 'PE' ? 'var(--loss)' : 'var(--text-secondary)' }}>{!isDay ? r.type : ''}</td>
                  <td className="text-right">{!isDay && r.strike != null ? Number(r.strike).toFixed(0) : ''}</td>
                  <td className="text-center" style={{ color: r.bs === 'SELL' ? 'var(--loss)' : 'var(--profit)' }}>{!isDay ? r.bs : ''}</td>
                  <td className="text-right">{r.qty != null ? r.qty : ''}</td>
                  <td className="text-right">{!isDay && r.entryPrice != null ? Number(r.entryPrice).toFixed(2) : ''}</td>
                  <td className="text-right">{!isDay && r.exitPrice != null ? Number(r.exitPrice).toFixed(2) : ''}</td>
                  <td className="text-right font-semibold" style={{ color: pnlVal >= 0 ? 'var(--profit)' : 'var(--loss)' }}>
                    {pnlVal.toFixed(2)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination + Download */}
      <div className="mt-3 flex items-center justify-between flex-wrap gap-2">
        <button
          type="button"
          onClick={exportToExcel}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold transition-all"
          style={{ fontFamily: 'Outfit, sans-serif', letterSpacing: '0.06em', color: 'var(--accent)', border: '1px solid var(--accent-bg)', background: 'var(--accent-bg)' }}
          onMouseEnter={e => e.currentTarget.style.boxShadow = '0 0 12px var(--accent-glow)'}
          onMouseLeave={e => e.currentTarget.style.boxShadow = 'none'}
        >
          <Download size={12} /> DOWNLOAD XLSX
        </button>
        <div className="flex items-center gap-1 text-xs">
          <button type="button" disabled={safePage <= 1} onClick={() => setPage(1)}
            className="px-2 py-1 rounded border border-default text-secondary disabled:opacity-40">«</button>
          <button type="button" disabled={safePage <= 1} onClick={() => setPage(p => Math.max(1, p - 1))}
            className="px-2 py-1 rounded border border-default text-secondary disabled:opacity-40">‹</button>
          {Array.from({ length: Math.min(6, totalPages) }, (_, i) => {
            // Always show first page, current ±2, last page
            const p = i + 1;
            return (
              <button key={p} type="button" onClick={() => setPage(p)}
                className={`px-2.5 py-1 rounded border ${p === safePage ? 'border-accent bg-accent text-white' : 'border-default text-secondary hover:bg-hover'}`}>{p}</button>
            );
          })}
          <button type="button" disabled={safePage >= totalPages} onClick={() => setPage(p => Math.min(totalPages, p + 1))}
            className="px-2 py-1 rounded border border-default text-secondary disabled:opacity-40">›</button>
          <button type="button" disabled={safePage >= totalPages} onClick={() => setPage(totalPages)}
            className="px-2 py-1 rounded border border-default text-secondary disabled:opacity-40">»</button>
        </div>
      </div>
    </div>
  );
};

const buildExcelFileName = (config) => {
  if (!config) return `backtest.xlsx`;

  const parts = [config.instrument || 'backtest'];

  (config.legs || []).forEach(leg => {
    if (leg.segment === 'futures') {
      parts.push((leg.position || 'sell').toUpperCase());
      parts.push('FUT');
      const exp = (leg.expiry || '').toUpperCase().replace('_', '');
      if (exp) parts.push(exp);
    } else {
      parts.push((leg.position || 'sell').toUpperCase());
      const opt = (() => {
        const o = (leg.option_type || '').toLowerCase();
        if (o === 'call') return 'CE';
        if (o === 'put') return 'PE';
        return o.toUpperCase();
      })();
      if (opt) parts.push(opt);
      const criteria = leg.strike_criteria || 'strike_type';
      if (criteria === 'pct_of_atm') {
        parts.push('PCT');
      } else if (criteria === 'atm_straddle_prem_pct') {
        parts.push('STRADDLE');
      } else {
        parts.push((leg.strike_type || 'atm').toUpperCase());
      }
      const exp = (leg.expiry || '').toUpperCase().replace('_', '');
      if (exp) parts.push(exp);
    }
  });

  const entry = config.entryDaysBefore != null ? `T${config.entryDaysBefore}` : null;
  const exit  = config.exitDaysBefore  != null ? `T${config.exitDaysBefore}`  : null;
  if (entry) parts.push(entry);
  if (exit)  parts.push(exit);

  if (config.spotAdjustmentEnabled) {
    parts.push('SA');
    parts.push((config.spotAdjustmentDirection || 'rise').toUpperCase());
  }

  return parts.join('_') + '.xlsx';
};

const ResultsPanel = ({ results, onClose, showCloseButton = true, filterInfo, showStrSegment = false, strategyConfig }) => {
  if (!results) return null;

  // Intraday backtest returns a flat array of trade objects with entry_time / exit_time fields.
  if (Array.isArray(results)) {
    return <IntradayFullReport rows={results} onClose={onClose} showCloseButton={showCloseButton} />;
  }

  useEffect(() => {
    console.log('[ResultsPanel] mounted (single instance check)');
    return () => {
      console.log('[ResultsPanel] unmounted');
    };
  }, []);
  
  console.log('[ResultsPanel] results:', JSON.stringify(results, null, 2).slice(0, 500));
  const { trades = [], summary = {}, pivot = {} } = results;
  const slippagePct = Number(results?.meta?.slippage_pct || 0);
  const chargesEnabled = Boolean(results?.meta?.charges_enabled);
  const bufferStrikeEnabled = Boolean(
    results?.meta?.buffer_strike_enabled ||
    trades.some(trade => trade?.buffer_ref_price != null || Number(trade?.buffer_strike_offset))
  );
  const warnings = results?.warnings || [];
  const filteredWarnings = warnings.filter(Boolean);
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 25;
  const getVisibleExitDate = (trade) => {
    return formatDateToDdMmYyyy(trade?.['Exit Date']);
  };
  const tradesWithFormattedDates = useMemo(() => trades.map(trade => ({
    ...trade,
    'Entry Date': formatDateToDdMmYyyy(trade['Entry Date']),
    'Exit Date': getVisibleExitDate(trade),
    'Leg Exit Date': formatDateToDdMmYyyy(trade['Leg Exit Date']),
    'Expiry': formatDateToDdMmYyyy(trade['Expiry']),
  })), [trades]);

  const isLazyLegRow = (row) => (
    row?.['Is Lazy Leg'] === true ||
    String(row?.['Is Lazy Leg'] || '').toLowerCase() === 'true' ||
    Boolean(row?.['Lazy Leg Name'])
  );

  // Two-pass grouping:
  // Pass 1 — find the canonical (first-seen) Entry Date per Trade number.
  // Pass 2 — rows whose Entry Date matches the canonical go into the main
  //           trade group; rows with a DIFFERENT Entry Date are rolled/
  //           continuation legs and get their own display group so they are
  //           never merged with the original trade.
  // Within each group, duplicate Leg numbers are deduplicated (last row wins).
  const groupedTrades = useMemo(() => {
    if (!tradesWithFormattedDates || tradesWithFormattedDates.length === 0) return [];

    // Pass 1: canonical entry date per Trade number
    const canonicalEntryByTrade = new Map();
    tradesWithFormattedDates.forEach(trade => {
      const tradeNum = String(trade.Trade || trade.trade || 1);
      const entryDate = trade['Entry Date'] || '';
      const isReEntryRow = Boolean(trade['ReEntryIndex'] || trade['ReEntryTrigger'] || trade['ReEntryMode'] || isLazyLegRow(trade));
      if (!isReEntryRow && !canonicalEntryByTrade.has(tradeNum)) {
        canonicalEntryByTrade.set(tradeNum, entryDate);
      }
    });

    const parseRowNetPnl = (row) => {
      const rawNet = row?.['Net P&L'];
      const net = typeof rawNet === 'number' ? rawNet : parseFloat(rawNet);
      if (Number.isFinite(net)) return net;
      return ['CE P&L', 'PE P&L', 'FUT P&L'].reduce((subtotal, key) => {
        const raw = row?.[key];
        const num = typeof raw === 'number' ? raw : parseFloat(raw);
        return subtotal + (Number.isFinite(num) ? num : 0);
      }, 0);
    };

    // Pass 2: build groups; keep main legs and re-entry sub-rows separately.
    const groupMap = new Map(); // groupKey → { mainLegs: Map, reEntryRows: Map, firstRow, tradeNum }
    tradesWithFormattedDates.forEach(trade => {
      const tradeNum = String(trade.Trade || trade.trade || 1);
      const legNum   = String(trade.Leg  || trade.leg  || 1);
      const entryDate = trade['Entry Date'] || '';
      const canonical = canonicalEntryByTrade.get(tradeNum) || entryDate;
      const isReEntryRow = Boolean(
        trade['ReEntryIndex'] ||
        trade['ReEntryTrigger'] ||
        trade['ReEntryMode'] ||
        isLazyLegRow(trade) ||
        String(trade['Index'] || '').includes('.')
      );

      const groupKey = tradeNum;

      if (!groupMap.has(groupKey)) {
        groupMap.set(groupKey, { mainLegs: new Map(), reEntryRows: new Map(), firstRow: trade, tradeNum });
      }
      const group = groupMap.get(groupKey);

      if (isReEntryRow) {
        const baseLegNum = String(parseInt(String(legNum).split('.')[0], 10) || legNum || 1);
        if (!group.reEntryRows.has(baseLegNum)) {
          group.reEntryRows.set(baseLegNum, []);
        }
        group.reEntryRows.get(baseLegNum).push(trade);
        return;
      }

      // Skip rolled/continuation trades - only include main trades with canonical entry date
      if (entryDate !== canonical) {
        return;
      }

      // Last row for a given Leg number wins (deduplication)
      group.mainLegs.set(legNum, trade);
    });

    const result = [];
    for (const [groupKey, { mainLegs, reEntryRows, firstRow, tradeNum }] of groupMap.entries()) {
      const legsArr = Array.from(mainLegs.values());

      // Leg 1 is the only row the backend populates Cumulative/Peak/DD/%DD on.
      // firstRow is the first-encountered row which may be Leg 2+ if the trades
      // array arrives out of leg order — so always look up Leg '1' explicitly.
      // Try string '1', integer 1, then sort by leg number ascending and pick
      // the row that actually has Cumulative populated (most reliable signal).
      const sortedLegsAsc = legsArr.slice().sort((a, b) =>
        parseInt(a.Leg || a.leg || 1, 10) - parseInt(b.Leg || b.leg || 1, 10)
      );
      // Prefer the row that has Cumulative populated, fallback to lowest leg number
      const leg1Row =
        sortedLegsAsc.find(r =>
          r['Cumulative'] !== '' && r['Cumulative'] != null && !isNaN(parseFloat(r['Cumulative']))
        ) ||
        mainLegs.get('1') ||
        mainLegs.get(1) ||
        sortedLegsAsc[0] ||
        firstRow;

      const displayRows = [];
      sortedLegsAsc.forEach(mainLeg => {
        const mainLegNum = String(mainLeg.Leg || mainLeg.leg || 1);
        displayRows.push(mainLeg);
        const reRows = (reEntryRows.get(mainLegNum) || []).slice().sort((a, b) => {
          const aIdx = parseInt(String(a.ReEntryIndex || a['ReEntryIndex'] || a.Index || 0).split('.').pop(), 10) || 0;
          const bIdx = parseInt(String(b.ReEntryIndex || b['ReEntryIndex'] || b.Index || 0).split('.').pop(), 10) || 0;
          return aIdx - bIdx;
        });
        reRows.forEach(r => displayRows.push(r));
      });

      // Include any orphan re-entry rows that do not match an existing main leg.
      Array.from(reEntryRows.entries())
        .filter(([legNum]) => !sortedLegsAsc.some(mainLeg => String(mainLeg.Leg || mainLeg.leg || 1) === legNum))
        .forEach(([, rows]) => {
          rows.slice().sort((a, b) => {
            const aIdx = parseInt(String(a.ReEntryIndex || a['ReEntryIndex'] || a.Index || 0).split('.').pop(), 10) || 0;
            const bIdx = parseInt(String(b.ReEntryIndex || b['ReEntryIndex'] || b.Index || 0).split('.').pop(), 10) || 0;
            return aIdx - bIdx;
          }).forEach(r => displayRows.push(r));
        });

      const totalPnl = parseRowNetPnl(leg1Row);

      const rawCumulative = leg1Row['Cumulative'];
      const rawPeak       = leg1Row['Peak'];
      const rawDd         = leg1Row['DD'];
      const rawPctDd      = leg1Row['%DD'];

      result.push({
        tradeNumber: parseInt(tradeNum, 10) || 0,
        groupKey,
        legs: legsArr,
        displayRows,
        hasReEntries: displayRows.some(row => Boolean(row['ReEntryIndex'] || row['ReEntryTrigger'] || row['ReEntryMode'] || isLazyLegRow(row))),
        entryDate:  leg1Row['Entry Date'],
        exitDate:    leg1Row['Exit Date'],
        entrySpot:  parseFloat(leg1Row['Entry Spot']) || 0,
        exitSpot:   parseFloat(leg1Row['Exit Spot'])  || 0,
        totalPnl,
        cumulative: (rawCumulative !== '' && rawCumulative != null && !isNaN(parseFloat(rawCumulative)))
          ? parseFloat(rawCumulative)
          : null,   // null so connectNulls can bridge it; equityData fallbacks to 100.0
        peak:   (rawPeak !== '' && rawPeak != null && !isNaN(parseFloat(rawPeak)))
          ? parseFloat(rawPeak) : null,
        dd:     (rawDd !== '' && rawDd != null && !isNaN(parseFloat(rawDd)))
          ? parseFloat(rawDd) : null,
        pct_dd: (rawPctDd !== '' && rawPctDd != null && !isNaN(parseFloat(rawPctDd)))
          ? parseFloat(rawPctDd) : null,
      });
    }

    result.sort((a, b) => {
      if (a.tradeNumber !== b.tradeNumber) {
        return a.tradeNumber - b.tradeNumber;
      }
      const dateA = a.entryDate || '';
      const dateB = b.entryDate || '';
      if (dateA < dateB) return -1;
      if (dateA > dateB) return 1;
      return 0;
    });
    return result;
  }, [tradesWithFormattedDates]);

  // Prepare chart data - USE GROUPED TRADES (one point per trade, not per leg)
  // Use Series B (compound index starting from 100) for equity curve
  const equityData = useMemo(() => {
    if (!groupedTrades || groupedTrades.length === 0) return [];

    let lastCumulative = 100;

    return groupedTrades.map((group, index) => {
      // Scan ALL legs for the first populated cumulative value —
      // the backend stamps it on Leg 1 but may arrive on any leg when
      // multiple legs are present and data is unsorted.
      let cum = group.cumulative;
      if (cum === null || cum === undefined) {
        for (const leg of group.legs) {
          const raw = leg['Cumulative'];
          if (raw !== '' && raw != null && !isNaN(parseFloat(raw))) {
            cum = parseFloat(raw);
            break;
          }
        }
      }
      // Carry forward previous cumulative value to maintain chart continuity
      if (cum === null || cum === undefined) {
        cum = lastCumulative;
      } else {
        lastCumulative = cum;
      }
      return {
        index: index + 1,
        date: group.exitDate || `Trade ${index + 1}`,
        cumulative: cum,
        pnl: group.totalPnl ?? 0,
      };
    });
  }, [groupedTrades]);

  const drawdownData = useMemo(() => {
    if (!groupedTrades || groupedTrades.length === 0) return [];

    let lastDd = null;

    return groupedTrades.map((group, index) => {
      // Scan ALL legs for the first populated %DD value — same as equityData fix.
      // Fall back to null (not 0) so connectNulls bridges gaps instead of
      // injecting fake zero spikes that visually break the chart.
      let dd = group.pct_dd;
      if (dd === null || dd === undefined) {
        for (const leg of group.legs) {
          const raw = leg['%DD'];
          if (raw !== '' && raw != null && !isNaN(parseFloat(raw))) {
            dd = parseFloat(raw);
            break;
          }
        }
      }
      // Carry forward previous drawdown value to maintain chart continuity
      if (dd === null || dd === undefined) {
        dd = lastDd;
      } else {
        lastDd = dd;
      }
      // pct_dd from backend is a percentage (e.g. -0.31), never multiply by 100 here.
      return {
        index: index + 1,
        date: group.exitDate || `Trade ${index + 1}`,
        drawdown: (dd !== null && dd !== undefined) ? dd : null,
      };
    });
  }, [groupedTrades]);

  // equityCurveData — use backend equity_curve if available, else fall back to grouped trades data
  const equityCurveData = useMemo(() => {
    if (results.equity_curve && results.equity_curve.length) return results.equity_curve;
    return equityData.map(point => ({
      date: point.date,
      net_cumulative: point.cumulative,
      gross_cumulative: point.cumulative,
    }));
  }, [results.equity_curve, equityData]);

  // Pre-compute Y-axis domains so they are stable across renders
  const equityDomain = useMemo(() => {
    const vals = equityData.map(d => d.cumulative).filter(v => v != null && !isNaN(v));
    if (vals.length === 0) return [90, 110];
    const lo = Math.max(0, Math.floor(Math.min(...vals) - 5));
    const hi = Math.ceil(Math.max(...vals) + 2);
    return [lo, hi];
  }, [equityData]);

  const drawdownDomain = useMemo(() => {
    const vals = drawdownData.map(d => d.drawdown).filter(v => v != null && !isNaN(v));
    const ddMin = vals.length > 0 ? Math.min(...vals) : -1;
    // Add 10% headroom below the minimum; keep max at 0.5 so the zero line
    // is slightly below the top edge and always visible.
    const lo = ddMin >= 0 ? -1 : Math.floor(ddMin * 1.15 * 10) / 10;
    return [lo, 0.5];
  }, [drawdownData]);

  // Calculate stats
  const stats = useMemo(() => {
    const finalCumulative = groupedTrades.length > 0
      ? (groupedTrades[groupedTrades.length - 1]?.cumulative ?? 100)
      : 100;
    const totalPnLPct = finalCumulative - 100;
    const totalTrades = groupedTrades.length;

    return {
      totalPnLPct,
      totalTrades,
      winRate: summary.win_pct || 0,
      lossPct: summary.loss_pct || 0,
      cagr: summary.cagr_options || 0,
      maxDDPct: summary.max_dd_pct ?? 0,
      maxDDPts: summary.max_dd_pts || 0,
      carMdd: summary.car_mdd || 0,
      recoveryFactor: summary.recovery_factor || 0,
      expectancy: summary.expectancy || 0,
      rewardToRisk: summary.reward_to_risk || 0,
      avgWinPct: summary.avg_win_pct || 0,
      avgLossPct: summary.avg_loss_pct || 0,
      avgWin: summary.avg_win || 0,
      avgLoss: summary.avg_loss || 0,
      maxWin: summary.max_win || 0,
      maxLoss: summary.max_loss || 0,
      avgProfitPerTrade: summary.avg_profit_per_trade || 0,
      maxWinStreak: summary.max_win_streak || 0,
      maxLossStreak: summary.max_loss_streak || 0,
      mddDuration: summary.mdd_duration_days || 0,
      mddStartDate: summary.mdd_start_date || '',
      mddEndDate: summary.mdd_end_date || '',
      mddTradeNumber: summary.mdd_trade_number || null,
      cagrSpot: summary.cagr_spot || 0,
    };
  }, [summary, groupedTrades]);


  // Export Excel — Sheet 1: Trades, Sheet 2: Formatted Summary
  const exportToCSV = async () => {
    if (!tradesWithFormattedDates || tradesWithFormattedDates.length === 0) return;
    const sourceTrades = tradesWithFormattedDates;

    // ─── Palette ────────────────────────────────────────────────────────────
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
      gold:      { argb: 'FFFFF3CD' },
      goldTx:    { argb: 'FF856404' },
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
    const rightAlign  = { horizontal: 'right',  vertical: 'middle' };

    // ─── Prepare trade data ──────────────────────────────────────────────────
    const hasCalls   = sourceTrades.some(t => ['CE','CALL'].includes((t['Type']||'').toUpperCase()));
    const hasPuts    = sourceTrades.some(t => ['PE','PUT'].includes((t['Type']||'').toUpperCase()));
    const hasFutures = sourceTrades.some(t => (t['Type']||'').toUpperCase() === 'FUT');
    const hasStr          = showStrSegment && sourceTrades.some(t => t['STR Segment']);
    const hasFilterSegment = showStrSegment && sourceTrades.some(t => t['Filter Segment']);
    const hasBuffer  = bufferStrikeEnabled;
    const hasSpotAdj = Boolean(results?.meta?.spot_adjustment_enabled);
    const hasReEntry = sourceTrades.some(t => (
      Boolean(t['ReEntryIndex'] || t['ReEntryTrigger'] || t['ReEntryMode']) || isLazyLegRow(t)
    ));

    const getReEntryType = (trade) => {
      if (isLazyLegRow(trade)) return 'Lazy';
      const mode = String(trade?.['ReEntryMode'] || '').trim();
      if (mode) return mode;
      const trigger = String(trade?.['ReEntryTrigger'] || '').trim();
      if (trigger) return trigger;
      return trade?.['ReEntryIndex'] ? 'Re-Entry' : '';
    };

    const sortedTrades = [...sourceTrades].sort((a, b) => {
      const tA = parseInt(a.Trade||a.trade||1,10), tB = parseInt(b.Trade||b.trade||1,10);
      if (tA !== tB) return tA - tB;
      return parseInt(a.Leg||a.leg||1,10) - parseInt(b.Leg||b.leg||1,10);
    });

    const groupedByTrade = {};
    sortedTrades.forEach(t => {
      const k = String(t.Trade||t.trade||1);
      if (!groupedByTrade[k]) groupedByTrade[k] = [];
      groupedByTrade[k].push(t);
    });

    const toNumber = (value) => {
      if (typeof value === 'number') return Number.isFinite(value) ? value : null;
      if (value == null || value === '') return null;
      const parsed = parseFloat(String(value).replace(/[,%₹\s]/g, ''));
      return Number.isFinite(parsed) ? parsed : null;
    };

    const isFutureRow = (row) => String(row?.['Type'] || '').toUpperCase() === 'FUT';
    const isOptionRow = (row) => ['CE','CALL','PE','PUT'].includes(String(row?.['Type'] || '').toUpperCase());
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

    const isBuyLeg  = (row) => String(row?.['B/S'] || '').toUpperCase() === 'BUY';
    const isSellLeg = (row) => String(row?.['B/S'] || '').toUpperCase() === 'SELL';

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
        return {
          netMae1: roundMae(netMae1),
          netMae2: roundMae(netMae2),
          finalMae: roundMae(Math.min(netMae1, netMae2)),
        };
      }

      // Options-only trade with at least one BUY and at least one SELL option leg:
      // pair the SELL-side MAE with the BUY-side MFE (and vice versa) to model
      // the two legs as directionally hedging each other. (Per user spec.)
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
        return {
          netMae1: roundMae(netMae1),
          netMae2: roundMae(netMae2),
          finalMae: roundMae(Math.min(netMae1, netMae2)),
        };
      }

      // All option legs same side (all BUY or all SELL) — naive sum.
      const netMae1 = optionMae;
      const netMae2 = optionMfe;
      return {
        netMae1: roundMae(netMae1),
        netMae2: roundMae(netMae2),
        finalMae: roundMae(Math.min(netMae1, netMae2)),
      };
    };

    const hasTradeMae = Object.values(groupedByTrade).some(legs => calcTradeMae(legs));

    const TRADE_COLS = new Set([
      'Net MAE 1','Net MAE 2','Final MAE',
      'Net P&L','% P&L','Cumulative','Peak','DD','%DD',
      'Lowest NAV','Actual Live DD',
    ]);
    const keyOrder = [
      'Trade','Leg','Index','Entry Date','Exit Date','Expiry',
      'Entry Spot','Exit Spot','Spot P&L',
      'Type','Strike',
      ...(hasBuffer ? ['buffer_ref_price', 'buffer_strike_offset'] : []),
      'B/S',
      ...(hasReEntry ? ['Re-Entry Type'] : []),
      'Qty',
      ...(hasSpotAdj ? ['Raw Entry Price'] : []),
      'Entry Price',
      ...(hasSpotAdj ? ['Raw Exit Price'] : []),
      'Exit Price','MAE','MFE','Net MAE 1','Net MAE 2','Final MAE',
      ...(hasCalls   ? ['CE P&L']  : []),
      ...(hasPuts    ? ['PE P&L']  : []),
      ...(hasFutures ? ['FUT P&L'] : []),
      'Net P&L','% P&L','Cumulative','Peak','DD','%DD','Lowest NAV','Actual Live DD',
      'Exit Reason',
      ...(hasStr ? ['STR Segment'] : []),
      ...(hasFilterSegment ? ['Filter Segment'] : []),
    ];

    const tm = {};
    Object.entries(groupedByTrade).forEach(([k, legs]) => {
      const mainRow = legs.find(l => !l['ReEntryIndex'] && !l['ReEntryTrigger'] && !l['ReEntryMode'] && !isLazyLegRow(l)) || legs[0];
      const spot = parseFloat(mainRow?.['Entry Spot']) || 0;
      const rawNet = mainRow?.['Net P&L'];
      const net = Number.isFinite(typeof rawNet === 'number' ? rawNet : parseFloat(rawNet))
        ? (typeof rawNet === 'number' ? rawNet : parseFloat(rawNet))
        : legs.reduce((s,l) => s+(parseFloat(l['CE P&L'])||0)+(parseFloat(l['PE P&L'])||0)+(parseFloat(l['FUT P&L'])||0), 0);
      const toN  = v => (v!=null&&v!==''&&!isNaN(parseFloat(v))) ? parseFloat(v) : '';
      const r    = mainRow || legs[0];
      const tradeMae = calcTradeMae(legs);
      tm[k] = { net, pct:(spot>0?(net/spot)*100:0),
                netMae1:tradeMae?.netMae1 ?? '', netMae2:tradeMae?.netMae2 ?? '', finalMae:tradeMae?.finalMae ?? '',
                cumulative:toN(r['Cumulative']), peak:toN(r['Peak']),
                dd:toN(r['DD']), pctDd:toN(r['%DD']) };
    });

    // Compute Lowest NAV and Actual Live DD in trade order.
    // Lowest NAV = prev_closing_NAV × (1 + finalMae / 100)  — worst equity point during the trade
    // Actual Live DD = (lowestNav / peak − 1) × 100         — % drawdown from peak using that intra-trade low
    {
      const sortedTmKeys = Object.keys(tm).sort((a, b) => parseInt(a, 10) - parseInt(b, 10));
      let prevCum = 100;
      sortedTmKeys.forEach(k => {
        const t = tm[k];
        const mae  = (t.finalMae !== '' && t.finalMae != null) ? t.finalMae  : null;
        const peak = (t.peak      !== '' && t.peak      != null) ? t.peak     : null;
        if (mae != null && peak != null && peak !== 0) {
          const lowestNav   = Math.round(prevCum * (1 + mae / 100) * 100) / 100;
          const actualLiveDD = Math.round((lowestNav / peak - 1) * 10000) / 100;
          t.lowestNav    = lowestNav;
          t.actualLiveDD = actualLiveDD;
        } else {
          t.lowestNav    = '';
          t.actualLiveDD = '';
        }
        if (t.cumulative !== '' && t.cumulative != null) prevCum = t.cumulative;
      });
    }

    const written = new Set();
    const cleanedTrades = sortedTrades.map(trade => {
      const k = String(trade.Trade||trade.trade||1);
      const first = !written.has(k); if (first) written.add(k);
      const m = tm[k]||{};
      const row = {};
      for (const key of keyOrder) {
        let val;
        if (TRADE_COLS.has(key)) {
          if (!first) val='';
          else if (key==='Net MAE 1') val=m.netMae1;
          else if (key==='Net MAE 2') val=m.netMae2;
          else if (key==='Final MAE') val=m.finalMae;
          else if (key==='Net P&L') val=m.net;
          else if (key==='% P&L')  val=m.pct;
          else if (key==='Cumulative') val=m.cumulative;
          else if (key==='Peak')  val=m.peak;
          else if (key==='DD')    val=m.dd;
          else if (key==='%DD')   val=m.pctDd;
          else if (key==='Lowest NAV') val=m.lowestNav;
          else if (key==='Actual Live DD') val=m.actualLiveDD;
        } else if (key==='Leg' && isLazyLegRow(trade)) val=trade['Lazy Leg Name'] || trade[key];
        else if (key==='Re-Entry Type') val=getReEntryType(trade);
        else if (key==='Index') val=parseInt(trade.Trade||trade.trade||1,10);
        else if (key==='Exit Date') val=getVisibleExitDate(trade);
        else if (key==='Expiry') val=formatDateToDdMmYyyy(
          trade['Future Expiry'] || trade['futures_expiry'] || trade['Expiry']
        );
        else val=trade[key];
        if (val==null||(typeof val==='number'&&isNaN(val))||val==='NaN') val='';
        row[key]=val;
      }
      return row;
    });

    // ─── Build Workbook ──────────────────────────────────────────────────────
    const wb = new ExcelJS.Workbook();
    wb.creator = 'AlgoTest Backtest';
    wb.created = new Date();
    wb.calcProperties = { fullCalcOnLoad: true };

    // ════════════════════════════════════════════════════════════════════════
    // SHEET 1 — TRADE SHEET
    // ════════════════════════════════════════════════════════════════════════
    const ws1 = wb.addWorksheet('Trade Sheet', { views: [{ state:'frozen', ySplit:1 }] });

    // Column widths
    const colWidths = { 'Leg':12,'Entry Date':13,'Exit Date':13,'Entry Spot':12,'Exit Spot':12,
      'buffer_ref_price':12,'buffer_strike_offset':10,'Re-Entry Type':14,'Raw Entry Price':12,'Entry Price':12,'Raw Exit Price':12,'Exit Price':12,'MAE':9,'MFE':9,'Net MAE 1':10,'Net MAE 2':10,'Final MAE':10,'Net P&L':10,'% P&L':8,'Cumulative':11,'Peak':10,'DD':9,'%DD':8,'Lowest NAV':13,'Actual Live DD':15,
      'Exit Reason':14,'Expiry':12,'STR Segment':14,'Filter Segment':18 };
    ws1.columns = keyOrder.map(k => ({ key: k, width: colWidths[k] || 10 }));

    // Header row style
    // Add header row explicitly (no auto-header from column definition)
    const headerDataRow = ws1.addRow(keyOrder);
    headerDataRow.eachCell(cell => {
      cell.font  = boldFont(10, C.navyText);
      cell.fill  = { type:'pattern', pattern:'solid', fgColor: C.headerBg };
      cell.alignment = centerAlign;
      cell.border = thinBorder();
    });
    headerDataRow.height = 22;

    // Data rows
    cleanedTrades.forEach((row, i) => {
      const r   = ws1.addRow(keyOrder.map(k => row[k]??''));
      const net = typeof row['Net P&L']==='number' ? row['Net P&L'] : null;
      const bg  = i%2===0 ? C.white : C.altRow;
      r.eachCell((cell, colNum) => {
        cell.font   = normFont(10);
        cell.fill   = { type:'pattern', pattern:'solid', fgColor: bg };
        cell.border = thinBorder();
        cell.alignment = { vertical:'middle' };
        // Coerce string numbers to actual numbers so Excel formulas (VLOOKUP etc.) work
        const _colKey = keyOrder[colNum - 1];
        const _dateColsSet = new Set(['Entry Date','Exit Date','Expiry','Leg Exit Date','Lazy Entry Date','Lazy Exit Date']);
        if (_dateColsSet.has(_colKey) && typeof cell.value === 'string' && cell.value !== '') {
          const _dp = cell.value.split('-');
          if (_dp.length === 3) {
            const _d = new Date(Date.UTC(parseInt(_dp[2], 10), parseInt(_dp[1], 10) - 1, parseInt(_dp[0], 10)));
            if (!isNaN(_d.getTime())) { cell.value = _d; cell.numFmt = 'DD-MMM-YYYY'; }
          }
        } else if (typeof cell.value === 'string' && cell.value !== '') {
          const n = Number(cell.value);
          if (!isNaN(n)) cell.value = n;
        }
        if (typeof cell.value === 'number') {
          cell.numFmt = Number.isInteger(cell.value) ? '0' : '#,##0.00';
        }
      });
      // Color Net P&L and % P&L
      if (net !== null) {
        const col1 = keyOrder.indexOf('Net P&L')+1;
        const col2 = keyOrder.indexOf('% P&L')+1;
        [col1,col2].filter(c=>c>0).forEach(c => {
          const cell = r.getCell(c);
          cell.font = boldFont(10, net>=0 ? C.greenTx : C.redTx);
          cell.fill = { type:'pattern', pattern:'solid', fgColor: net>=0 ? C.greenBg : C.redBg };
        });
      }
    });

    // ════════════════════════════════════════════════════════════════════════
    // SHEET 2 — SUMMARY
    // ════════════════════════════════════════════════════════════════════════
    const ws2 = wb.addWorksheet('Summary');
    ws2.columns = [
      { width: 30 },{ width: 20 },{ width: 4 },{ width: 30 },{ width: 20 },
    ];

    const addTitle = (text, rowNum, cols = 'A:E', bgColor = C.navyBg) => {
      ws2.mergeCells(`${cols.split(':')[0]}${rowNum}:${cols.split(':')[1]}${rowNum}`);
      const cell = ws2.getCell(`${cols.split(':')[0]}${rowNum}`);
      cell.value = text;
      cell.font  = boldFont(13, C.navyText);
      cell.fill  = { type:'pattern', pattern:'solid', fgColor: bgColor };
      cell.alignment = { horizontal:'center', vertical:'middle' };
      ws2.getRow(rowNum).height = 26;
    };

    const addSectionHeader = (text, rowNum) => {
      ws2.mergeCells(`A${rowNum}:E${rowNum}`);
      const cell = ws2.getCell(`A${rowNum}`);
      cell.value = '  ' + text;
      cell.font  = boldFont(11, C.sectionTx);
      cell.fill  = { type:'pattern', pattern:'solid', fgColor: C.sectionBg };
      cell.alignment = leftAlign;
      ws2.getRow(rowNum).height = 20;
    };

    const addKvRow = (label, value, rowNum, col='A', isAlt=false, valColor=null) => {
      const lCol = col; const vCol = String.fromCharCode(col.charCodeAt(0)+1);
      const lCell = ws2.getCell(`${lCol}${rowNum}`);
      const vCell = ws2.getCell(`${vCol}${rowNum}`);
      lCell.value = label;
      vCell.value = value;
      lCell.font  = boldFont(10, { argb:'FF2C3E50' });
      lCell.fill  = { type:'pattern', pattern:'solid', fgColor: isAlt ? C.altRow : C.labelBg };
      lCell.alignment = leftAlign;
      lCell.border = thinBorder(C.border);
      const numVal = typeof value === 'number' ? value : parseFloat(String(value||'').replace(/[+%₹,]/g,''));
      const autoColor = valColor || (isNaN(numVal) ? null : numVal>=0 ? C.greenTx : C.redTx);
      vCell.font  = boldFont(10, autoColor || { argb:'FF1A1A2E' });
      vCell.fill  = { type:'pattern', pattern:'solid', fgColor: isAlt ? C.altRow : C.white };
      vCell.alignment = leftAlign;
      vCell.border = thinBorder(C.border);
      ws2.getRow(rowNum).height = 18;
    };

    // ── Row 1: Report title ─────────────────────────────────────────────────
    addTitle('  BACKTEST SUMMARY REPORT', 1, 'A:E', C.navyBg);

    // Sub-title: date
    ws2.mergeCells('A2:E2');
    const subCell = ws2.getCell('A2');
    subCell.value = `Generated: ${new Date().toLocaleDateString('en-IN', { day:'2-digit', month:'short', year:'numeric' })}`;
    subCell.font  = normFont(10, { argb:'FF555555' });
    subCell.alignment = centerAlign;
    subCell.fill  = { type:'pattern', pattern:'solid', fgColor: C.subHdrBg };
    ws2.getRow(2).height = 16;

    let row = 4;

    // ── SECTION 1: Performance Overview ─────────────────────────────────────
    addSectionHeader('PERFORMANCE OVERVIEW', row++);

    const kv = (l,v,r,col='A',alt=false,vc=null) => addKvRow(l,v,r,col,alt,vc);

    // ── Research team's formulas, computed in JS ─────────────────────────────
    // % P&L per trade (Trade Sheet column) is in percentage points (e.g., 0.23 = 0.23%).
    // Net P&L per trade is in ₹. These mirror the research team's SUM/AVG/COUNTIF
    // formulas from Summary Sample.xlsx, but written as plain values.
    let _sumPctJS = 0, _sumPosPctJS = 0, _sumNegPctJS = 0;
    let _winCntJS = 0, _lossCntJS = 0, _totalCntJS = 0;
    let _sumNetJS = 0, _maxNetJS = -Infinity, _minNetJS = Infinity;
    let _finalCumJS = 100, _spotCumJS = 100;
    let _minEntryMs = null, _maxExitMs = null;
    const _parseDate = (s) => {
      if (s instanceof Date) return s.getTime();
      if (typeof s !== 'string' || !s) return null;
      const parts = s.includes('/') ? s.split('/') : s.split('-');
      if (parts.length !== 3) return null;
      let y, m, d;
      if (parts[0].length === 4) { y = +parts[0]; m = +parts[1]-1; d = +parts[2]; }
      else { d = +parts[0]; m = +parts[1]-1; y = +parts[2]; }
      const t = Date.UTC(y, m, d);
      return Number.isFinite(t) ? t : null;
    };
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
      }
      const cum = t['Cumulative'];
      if (typeof cum === 'number' && Number.isFinite(cum)) _finalCumJS = cum;
      const eS = +t['Entry Spot'], xS = +t['Exit Spot'];
      if (typeof n === 'number' && Number.isFinite(n) && Number.isFinite(eS) && Number.isFinite(xS) && eS > 0) {
        _spotCumJS *= (xS / eS);
      }
      const eD = _parseDate(t['Entry Date']);
      const xD = _parseDate(t['Exit Date']);
      if (eD != null && (_minEntryMs == null || eD < _minEntryMs)) _minEntryMs = eD;
      if (xD != null && (_maxExitMs == null || xD > _maxExitMs)) _maxExitMs = xD;
    }
    if (!Number.isFinite(_maxNetJS)) _maxNetJS = 0;
    if (!Number.isFinite(_minNetJS)) _minNetJS = 0;
    const _avgWinPctJS  = _winCntJS  > 0 ? (_sumPosPctJS / _winCntJS)  : 0;
    const _avgLossPctJS = _lossCntJS > 0 ? (_sumNegPctJS / _lossCntJS) : 0;
    const _winRateJS    = _totalCntJS > 0 ? (_winCntJS  / _totalCntJS) * 100 : 0;
    const _lossRateJS   = _totalCntJS > 0 ? (_lossCntJS / _totalCntJS) * 100 : 0;
    const _avgNetJS     = _totalCntJS > 0 ? (_sumNetJS  / _totalCntJS) : 0;
    // Expectancy = (Win% × AvgWin - Loss% × |AvgLoss|) / |AvgLoss|, using decimals
    const _expectancyJS = _avgLossPctJS !== 0
      ? (((_winRateJS/100) * _avgWinPctJS - (_lossRateJS/100) * Math.abs(_avgLossPctJS)) / Math.abs(_avgLossPctJS))
      : 0;
    const _yearsJS = (_minEntryMs != null && _maxExitMs != null)
      ? (_maxExitMs - _minEntryMs) / (365.25 * 86400000)
      : 0;
    const _optCagrPctJS = _yearsJS > 0 && _finalCumJS > 0
      ? (Math.pow(_finalCumJS / 100, 1 / _yearsJS) - 1) * 100 : 0;
    const _spotCagrPctJS = _yearsJS > 0 && _spotCumJS > 0
      ? (Math.pow(_spotCumJS / 100, 1 / _yearsJS) - 1) * 100 : 0;
    // ROI vs Spot uses spot sum gated by Net P&L being a number (first-leg rows only)
    let _spotSumGatedJS = 0;
    for (const t of cleanedTrades) {
      const np = t['Net P&L'];
      if (typeof np === 'number' && Number.isFinite(np)) {
        const sp = +t['Spot P&L']; if (Number.isFinite(sp)) _spotSumGatedJS += sp;
      }
    }

    const profitColor = _sumPctJS >= 0 ? C.greenTx : C.redTx;
    const _fmtPct = (v, signed=true) => `${signed && v>=0?'+':''}${(+v).toFixed(2)}%`;
    const _fmtCurrency = (v) => `₹${(+v).toLocaleString('en-IN',{minimumFractionDigits:2})}`;

    kv('Overall Profit', _fmtPct(_sumPctJS), row, 'A', false, profitColor);
    kv('No. of Trades',  _totalCntJS,        row++, 'D', false, { argb:'FF1A1A2E' });

    kv('Win %',  `${_winRateJS.toFixed(2)}%`,  row, 'A', true, C.greenTx);
    kv('Loss %', `${_lossRateJS.toFixed(2)}%`, row++, 'D', true, C.redTx);

    kv('Avg Profit on Winners', `${_avgWinPctJS.toFixed(2)}%`,  row, 'A', false, C.greenTx);
    kv('Avg Loss on Losers',    `${_avgLossPctJS.toFixed(2)}%`, row++, 'D', false, C.redTx);

    kv('Avg Profit per Trade', `${_avgNetJS>=0?'+':''}${_avgNetJS.toFixed(2)}`, row, 'A', true,
       _avgNetJS>=0?C.greenTx:C.redTx);
    kv('Expectancy Ratio', _expectancyJS.toFixed(4), row++, 'D', true,
       _expectancyJS>=0?C.greenTx:C.redTx);

    kv('Max Profit (Single Trade)', _fmtCurrency(_maxNetJS), row, 'A', false, C.greenTx);
    kv('Max Loss (Single Trade)',   _fmtCurrency(_minNetJS), row++, 'D', false, C.redTx);

    kv('CAGR (Options)', _fmtPct(_optCagrPctJS),  row, 'A', true, _optCagrPctJS>=0?C.greenTx:C.redTx);
    kv('CAGR (Spot)',    _fmtPct(_spotCagrPctJS), row++, 'D', true, _spotCagrPctJS>=0?C.greenTx:C.redTx);

    // ── ROI vs SPOT block (Type / Sum / ROI vs Spot, research team layout) ───
    if (cleanedTrades.length > 0) {
      row++; // spacer

      // Per-side sums (research team's =SUM(...) formulas, computed in JS)
      let _ceSumJS = 0, _peSumJS = 0, _futSumJS = 0;
      for (const t of cleanedTrades) {
        const ce = +t['CE P&L']; if (Number.isFinite(ce)) _ceSumJS += ce;
        const pe = +t['PE P&L']; if (Number.isFinite(pe)) _peSumJS += pe;
        const fu = +t['FUT P&L']; if (Number.isFinite(fu)) _futSumJS += fu;
      }

      // Header: Type | Sum | (gap) | ROI vs Spot
      const _hdr = (col, txt) => {
        const c = ws2.getCell(`${col}${row}`);
        c.value = txt;
        c.font = boldFont(10, C.navyText);
        c.fill = { type:'pattern', pattern:'solid', fgColor: C.headerBg };
        c.alignment = centerAlign;
        c.border = thinBorder();
      };
      _hdr('A','Type'); _hdr('B','Sum');
      ws2.mergeCells(`D${row}:E${row}`);
      _hdr('D','ROI vs Spot');
      ws2.getCell(`D${row}`).alignment = centerAlign;
      ws2.getRow(row).height = 20;
      const _hdrRow = row; row++;

      const _addTypeRow = (label, value) => {
        const lC = ws2.getCell(`A${row}`);
        const vC = ws2.getCell(`B${row}`);
        lC.value = label;
        lC.font = boldFont(10, { argb:'FF2C3E50' });
        lC.fill = { type:'pattern', pattern:'solid', fgColor: C.labelBg };
        lC.alignment = leftAlign;
        lC.border = thinBorder(C.border);
        vC.value = (+value).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        vC.font = boldFont(10, value >= 0 ? C.greenTx : C.redTx);
        vC.fill = { type:'pattern', pattern:'solid', fgColor: C.white };
        vC.alignment = leftAlign;
        vC.border = thinBorder(C.border);
        ws2.getRow(row).height = 18;
      };

      // Determine numerator for ROI: CE+PE if both, else single side, else FUT, else Net
      const _optionsSumJS = (hasCalls && hasPuts) ? (_ceSumJS + _peSumJS)
        : (hasPuts ? _peSumJS : (hasCalls ? _ceSumJS : (hasFutures ? _futSumJS : _sumNetJS)));
      const _roiPctJS = _spotSumGatedJS !== 0 ? (_optionsSumJS / _spotSumGatedJS) * 100 : 0;

      // ROI value in D-E of the first data row (Spot P&L row)
      const _spotRow = row;
      ws2.mergeCells(`D${_spotRow}:E${_spotRow}`);
      const _roiVal = ws2.getCell(`D${_spotRow}`);
      _roiVal.value = _fmtPct(_roiPctJS);
      _roiVal.font = boldFont(11, _roiPctJS >= 0 ? C.greenTx : C.redTx);
      _roiVal.fill = { type:'pattern', pattern:'solid', fgColor: C.white };
      _roiVal.alignment = centerAlign;
      _roiVal.border = thinBorder(C.border);

      // Rows
      _addTypeRow('Spot P&L', _spotSumGatedJS); row++;
      if (hasCalls) { _addTypeRow('CE P&L', _ceSumJS); row++; }
      if (hasPuts)  { _addTypeRow('PE P&L', _peSumJS); row++; }
      if (hasFutures) { _addTypeRow('FUT P&L', _futSumJS); row++; }
      if (hasCalls && hasPuts) { _addTypeRow('CE + PE P&L', _ceSumJS + _peSumJS); row++; }
      _addTypeRow('Net P&L', _sumNetJS); row++;
    }

    row++; // blank

    // ── SECTION 2: Risk Metrics ─────────────────────────────────────────────
    addSectionHeader('RISK METRICS', row++);

    const mddColor = C.redTx;
    const _maxDDPctJS = stats.maxDDPct ?? 0; // already in percent points
    kv('Max Drawdown', `${_maxDDPctJS.toFixed(2)}%`, row, 'A', false, mddColor);
    kv('Max DD Days', stats.mddDuration, row++, 'D', false, mddColor);

    // Full-width DD period
    const ddPeriod = (stats.mddStartDate && stats.mddEndDate) ? `${stats.mddStartDate}  →  ${stats.mddEndDate}` : '—';
    ws2.mergeCells(`A${row}:E${row}`);
    const ddCell = ws2.getCell(`A${row}`);
    ddCell.value = `Drawdown Period:  ${ddPeriod}`;
    ddCell.font  = boldFont(10, C.redTx);
    ddCell.fill  = { type:'pattern', pattern:'solid', fgColor: C.redBg };
    ddCell.alignment = centerAlign;
    ddCell.border = thinBorder(C.border);
    ws2.getRow(row).height = 18;
    row++;

    // Return / MaxDD = ABS(Options CAGR / Max Drawdown)  — research team's formula
    // Reward to Risk = ABS(AvgWin / AvgLoss)
    const _carMddJS = _maxDDPctJS !== 0 ? Math.abs(_optCagrPctJS / _maxDDPctJS) : 0;
    const _rewardJS = _avgLossPctJS !== 0 ? Math.abs(_avgWinPctJS / _avgLossPctJS) : 0;
    kv('Return / MaxDD', _carMddJS.toFixed(2),  row, 'A', true);
    kv('Reward to Risk', _rewardJS.toFixed(2),  row++, 'D', true);

    row++;

    // ── SECTION 3: Consistency ──────────────────────────────────────────────
    addSectionHeader('CONSISTENCY & STREAKS', row++);

    kv('Max Win Streak',    `${stats.maxWinStreak} trades`,   row, 'A', false, C.greenTx);
    kv('Max Losing Streak', `${stats.maxLossStreak} trades`,  row++, 'D', false, C.redTx);

    row++;

    // ── SECTION 4: Monthly Returns ──────────────────────────────────────────
    addSectionHeader('MONTHLY RETURNS (₹ Net P&L)', row++);

    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const mthHdr = ['Year',...MONTHS,'Total','Max DD','DD Days','R/MDD'];

    // Set wide columns for monthly table (columns A–R)
    const mthCols = mthHdr.length;
    for (let ci = 0; ci < mthCols; ci++) {
      ws2.getColumn(ci+1).width = ci===0 ? 8 : ci<=12 ? 9 : ci===13 ? 10 : ci===14 ? 18 : 10;
    }

    // Header row
    const hdrRow = ws2.getRow(row);
    mthHdr.forEach((h, ci) => {
      const cell = hdrRow.getCell(ci+1);
      cell.value = h;
      cell.font  = boldFont(10, C.navyText);
      cell.fill  = { type:'pattern', pattern:'solid', fgColor: C.headerBg };
      cell.alignment = centerAlign;
      cell.border = thinBorder();
    });
    hdrRow.height = 20;
    row++;

    // Data rows: compute monthly Net P&L from trade-level totals grouped by Exit Date month.
    // If backend pivot rows exist, preserve the last 3 columns (Max DD / DD Days / R/MDD) per year.
    const pivotExtrasByYear = {};
    if (pivot && pivot.rows && pivot.rows.length > 0) {
      pivot.rows.forEach(r => {
        const yr = String(r?.[0] ?? '');
        if (!yr) return;
        pivotExtrasByYear[yr] = [r?.[14] ?? '', r?.[15] ?? '', r?.[16] ?? ''];
      });
    }

    const parseToYearMonth = (d) => {
      if (!d && d !== 0) return null;
      const s = String(d).trim();
      if (!s) return null;
      // supports DD-MM-YYYY, DD/MM/YYYY, YYYY-MM-DD
      const parts = s.includes('/') ? s.split('/') : s.split('-');
      if (parts.length !== 3) return null;
      let dd, mm, yy;
      if (parts[0].length === 4) { // YYYY-MM-DD
        yy = parts[0]; mm = parts[1]; dd = parts[2];
      } else { // DD-MM-YYYY or DD/MM/YYYY
        dd = parts[0]; mm = parts[1]; yy = parts[2];
      }
      const year = String(yy);
      const monthIdx = parseInt(mm, 10) - 1;
      if (!year || !Number.isFinite(monthIdx) || monthIdx < 0 || monthIdx > 11) return null;
      return { year, monthIdx };
    };

    const byYM = {};
    const byYMPct = {};
    groupedTrades.forEach(group => {
      const exitDate = group?.exitDate || '';
      const ym = parseToYearMonth(exitDate);
      if (!ym) return;
      const net  = Number(group?.totalPnl ?? 0) || 0;
      const spot = Number(group?.entrySpot ?? 0) || 0;
      const pct  = spot > 0 ? (net / spot) * 100 : 0;
      if (!byYM[ym.year])    byYM[ym.year]    = Array(12).fill(0);
      if (!byYMPct[ym.year]) byYMPct[ym.year] = Array(12).fill(0);
      byYM[ym.year][ym.monthIdx]    = (byYM[ym.year][ym.monthIdx]    || 0) + net;
      byYMPct[ym.year][ym.monthIdx] = (byYMPct[ym.year][ym.monthIdx] || 0) + pct;
    });

    const mthData = Object.entries(byYM).sort().map(([yr, mos]) => {
      const total = mos.reduce((s, v) => s + v, 0);
      const extras = pivotExtrasByYear[yr] || ['', '', ''];
      return [yr, ...mos.map(v => +v.toFixed(2)), +total.toFixed(2), ...extras];
    });

    mthData.forEach((dataRow, ri) => {
      const r2 = ws2.getRow(row);
      dataRow.forEach((val, ci) => {
        const cell = r2.getCell(ci+1);
        cell.value = val;
        const num  = typeof val==='number' ? val : parseFloat(String(val||'').replace(/[%,]/g,''));
        const isValCol = ci>=1 && ci<=12; // month columns
        const isTotalCol = ci===13;
        if ((isValCol||isTotalCol) && !isNaN(num) && num!==0) {
          cell.font = boldFont(10, num>=0 ? C.greenTx : C.redTx);
          cell.fill = { type:'pattern', pattern:'solid', fgColor: num>=0 ? C.greenBg : C.redBg };
        } else if (ci===0) {
          cell.font = boldFont(10, C.subHdrTx);
          cell.fill = { type:'pattern', pattern:'solid', fgColor: C.subHdrBg };
        } else {
          cell.font = normFont(10);
          cell.fill = { type:'pattern', pattern:'solid', fgColor: ri%2===0?C.white:C.altRow };
        }
        cell.alignment = centerAlign;
        cell.border = thinBorder();
      });
      r2.height = 18;
      row++;
    });

    // ── SECTION 4b: Monthly Returns (% P&L) ─────────────────────────────────
    row++;
    addSectionHeader('MONTHLY RETURNS (% Net P&L)', row++);

    const mthHdrPct = ['Year',...MONTHS,'Total'];
    const mthColsPct = mthHdrPct.length;
    for (let ci = 0; ci < mthColsPct; ci++) {
      ws2.getColumn(ci+1).width = ci===0 ? 8 : 9;
    }

    const hdrRowPct = ws2.getRow(row);
    mthHdrPct.forEach((h, ci) => {
      const cell = hdrRowPct.getCell(ci+1);
      cell.value = h;
      cell.font  = boldFont(10, C.navyText);
      cell.fill  = { type:'pattern', pattern:'solid', fgColor: C.headerBg };
      cell.alignment = centerAlign;
      cell.border = thinBorder();
    });
    hdrRowPct.height = 20;
    row++;

    const mthDataPct = Object.entries(byYMPct).sort().map(([yr, mos]) => {
      const total = mos.reduce((s, v) => s + v, 0);
      return [yr, ...mos.map(v => +v.toFixed(2)), +total.toFixed(2)];
    });

    mthDataPct.forEach((dataRow, ri) => {
      const r2 = ws2.getRow(row);
      dataRow.forEach((val, ci) => {
        const cell = r2.getCell(ci+1);
        const isValCol   = ci >= 1 && ci <= 12;
        const isTotalCol = ci === 13;
        if (isValCol || isTotalCol) {
          cell.value = typeof val === 'number' ? val / 100 : val;
          cell.numFmt = '0.00%';
        } else {
          cell.value = val;
        }
        const num = typeof val === 'number' ? val : parseFloat(String(val || ''));
        if ((isValCol || isTotalCol) && !isNaN(num) && num !== 0) {
          cell.font = boldFont(10, num >= 0 ? C.greenTx : C.redTx);
          cell.fill = { type:'pattern', pattern:'solid', fgColor: num >= 0 ? C.greenBg : C.redBg };
        } else if (ci === 0) {
          cell.font = boldFont(10, C.subHdrTx);
          cell.fill = { type:'pattern', pattern:'solid', fgColor: C.subHdrBg };
        } else {
          cell.font = normFont(10);
          cell.fill = { type:'pattern', pattern:'solid', fgColor: ri % 2 === 0 ? C.white : C.altRow };
        }
        cell.alignment = centerAlign;
        cell.border = thinBorder();
      });
      r2.height = 18;
      row++;
    });

    // ── SECTION 5: Live DD & Outlier Analysis ─────────────────────────────────
    row++;
    addSectionHeader('LIVE DD & OUTLIER ANALYSIS', row++);

    // Build per-trade (% P&L, Actual Live DD) pairs — one entry per unique trade
    const _tradePairs = [];
    Object.keys(tm).sort((a, b) => parseInt(a, 10) - parseInt(b, 10)).forEach(k => {
      const t = tm[k];
      const pct = (typeof t.pct === 'number' && Number.isFinite(t.pct)) ? t.pct : null;
      const ldd = (typeof t.actualLiveDD === 'number' && Number.isFinite(t.actualLiveDD)) ? t.actualLiveDD : null;
      if (pct !== null) _tradePairs.push({ pct, ldd, idx: _tradePairs.length });
    });
    const _nTrades = _tradePairs.length;
    const _byPctDesc = [..._tradePairs].sort((a, b) => b.pct - a.pct);

    // Outlier P&L accumulations (top/bottom N by % P&L)
    const _posOutlier1Pct = _nTrades > 0 ? _byPctDesc[0].pct : 0;
    const _posOutlier2Pct = _nTrades > 1 ? _byPctDesc[0].pct + _byPctDesc[1].pct : _posOutlier1Pct;
    const _posOutlier3Pct = _nTrades > 2 ? _byPctDesc[0].pct + _byPctDesc[1].pct + _byPctDesc[2].pct : _posOutlier2Pct;
    const _negOutlier1Pct = _nTrades > 0 ? _byPctDesc[_nTrades - 1].pct : 0;
    const _negOutlier2Pct = _nTrades > 1 ? _byPctDesc[_nTrades - 1].pct + _byPctDesc[_nTrades - 2].pct : _negOutlier1Pct;
    const _negOutlier3Pct = _nTrades > 2 ? _byPctDesc[_nTrades - 1].pct + _byPctDesc[_nTrades - 2].pct + _byPctDesc[_nTrades - 3].pct : _negOutlier2Pct;

    // Live DD min/avg excluding top excTop and bottom excBot trades by % P&L rank
    const _liveDDExcStats = (excTop, excBot) => {
      const excIdx = new Set([
        ..._byPctDesc.slice(0, excTop).map(p => p.idx),
        ..._byPctDesc.slice(Math.max(0, _nTrades - excBot)).map(p => p.idx),
      ]);
      const filtered = _tradePairs.filter(p => !excIdx.has(p.idx) && p.ldd !== null);
      if (filtered.length === 0) return { min: 0, avg: 0 };
      const ldds = filtered.map(p => p.ldd);
      return {
        min: +Math.min(...ldds).toFixed(2),
        avg: +(ldds.reduce((s, v) => s + v, 0) / ldds.length).toFixed(2),
      };
    };

    const _allLDDs = _tradePairs.filter(p => p.ldd !== null).map(p => p.ldd);
    const _actualLiveDDMin = _allLDDs.length > 0 ? +Math.min(..._allLDDs).toFixed(2) : 0;
    const _actualLiveDDAvg = _allLDDs.length > 0
      ? +(_allLDDs.reduce((s, v) => s + v, 0) / _allLDDs.length).toFixed(2) : 0;
    const _liveDDNoO1 = _liveDDExcStats(1, 1);
    const _liveDDNoO2 = _liveDDExcStats(2, 2);
    const _liveDDNoO3 = _liveDDExcStats(3, 3);
    const _carMddLiveJS = _actualLiveDDMin !== 0
      ? +(_optCagrPctJS / Math.abs(_actualLiveDDMin)).toFixed(2) : 0;

    // KV summary rows
    kv('Actual Live DD (min)', `${_actualLiveDDMin.toFixed(2)}%`, row, 'A', false, C.redTx);
    kv('Avg Actual Live DD',   `${_actualLiveDDAvg.toFixed(2)}%`, row++, 'D', false, C.redTx);
    kv('CAR/MDD (Booked)',     _carMddJS.toFixed(2),              row, 'A', true,
       _carMddJS >= 0 ? C.greenTx : C.redTx);
    kv('CAR/MDD Live',         _carMddLiveJS.toFixed(2),          row++, 'D', true,
       _carMddLiveJS >= 0 ? C.greenTx : C.redTx);

    row++;

    // Outlier table — 5 columns
    ws2.getColumn(1).width = 28;
    ws2.getColumn(2).width = 22;
    ws2.getColumn(3).width = 22;
    ws2.getColumn(4).width = 22;
    ws2.getColumn(5).width = 24;

    const _oHdrs = ['Outlier Set', '+ve Outlier (% P&L)', '-ve Outlier (% P&L)', 'Actual Live DD (%)', 'Avg Actual Live DD (%)'];
    const _oHdrRow = ws2.getRow(row);
    _oHdrs.forEach((h, ci) => {
      const cell = _oHdrRow.getCell(ci + 1);
      cell.value = h;
      cell.font  = boldFont(10, C.navyText);
      cell.fill  = { type: 'pattern', pattern: 'solid', fgColor: C.headerBg };
      cell.alignment = centerAlign;
      cell.border = thinBorder();
    });
    _oHdrRow.height = 20;
    row++;

    const _oRows = [
      ['All Trades',         _fmtPct(_posOutlier1Pct), _fmtPct(_negOutlier1Pct), `${_actualLiveDDMin.toFixed(2)}%`, `${_actualLiveDDAvg.toFixed(2)}%`],
      ['Without Outlier 1',  _fmtPct(_posOutlier1Pct), _fmtPct(_negOutlier1Pct), `${_liveDDNoO1.min.toFixed(2)}%`, `${_liveDDNoO1.avg.toFixed(2)}%`],
      ['Without Outlier 2',  _fmtPct(_posOutlier2Pct), _fmtPct(_negOutlier2Pct), `${_liveDDNoO2.min.toFixed(2)}%`, `${_liveDDNoO2.avg.toFixed(2)}%`],
      ['Without Outlier 3',  _fmtPct(_posOutlier3Pct), _fmtPct(_negOutlier3Pct), `${_liveDDNoO3.min.toFixed(2)}%`, `${_liveDDNoO3.avg.toFixed(2)}%`],
    ];

    _oRows.forEach((dataRow, ri) => {
      const r2 = ws2.getRow(row);
      dataRow.forEach((val, ci) => {
        const cell = r2.getCell(ci + 1);
        cell.value = val;
        const bg = ri % 2 === 0 ? C.white : C.altRow;
        if (ci === 0) {
          cell.font = boldFont(10, { argb: 'FF2C3E50' });
          cell.fill = { type: 'pattern', pattern: 'solid', fgColor: C.labelBg };
        } else if (ci === 1) {
          cell.font = boldFont(10, C.greenTx);
          cell.fill = { type: 'pattern', pattern: 'solid', fgColor: ri === 0 ? C.greenBg : bg };
        } else {
          cell.font = boldFont(10, C.redTx);
          cell.fill = { type: 'pattern', pattern: 'solid', fgColor: ri === 0 ? C.redBg : bg };
        }
        cell.alignment = centerAlign;
        cell.border = thinBorder();
      });
      r2.height = 18;
      row++;
    });

    // ── Download ─────────────────────────────────────────────────────────────
    const buffer = await wb.xlsx.writeBuffer();
    const blob = new Blob([buffer], { type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = buildExcelFileName(strategyConfig);
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const CustomTooltip = ({ active, payload }) => {
    if (active && payload && payload.length) {
      return (
        <div className="bg-base border border-strong rounded-lg p-3 shadow-xl">
          <p className="text-xs text-muted mb-1">{payload[0]?.payload?.date}</p>
          {payload.map((entry, index) => {
            const dataKey = entry.dataKey || '';
            const value = entry.value;
            let formatted;
            if (dataKey === 'drawdown') {
              formatted = `${value?.toFixed(2)}%`;
            } else if (dataKey === 'cumulative') {
              formatted = `${value?.toFixed(2)}`;
            } else {
              formatted = `₹${value?.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
            }
            return (
              <p key={index} className="text-sm font-medium" style={{ color: entry.color }}>
                {entry.name}: {formatted}
              </p>
            );
          })}
        </div>
      );
    }
    return null;
  };

  const formatDateShort = (dateStr) => {
    if (!dateStr) return '';
    try {
      // Handle dd-mm-yyyy format from backend
      const dashParts = dateStr.split('-');
      if (dashParts.length === 3) {
        const day = parseInt(dashParts[0]);
        const month = parseInt(dashParts[1]) - 1;
        const year = parseInt(dashParts[2]);
        const date = new Date(year, month, day);
        const monthName = date.toLocaleString('en-US', { month: 'short' });
        return `${day} ${monthName} ${year}`;
      }
      // Handle dd/mm/yyyy format from backend
      const slashParts = dateStr.split('/');
      if (slashParts.length === 3) {
        const day = parseInt(slashParts[0]);
        const month = parseInt(slashParts[1]) - 1;
        const year = parseInt(slashParts[2]);
        const date = new Date(year, month, day);
        const monthName = date.toLocaleString('en-US', { month: 'short' });
        return `${day} ${monthName} ${year}`;
      }
      // Fallback for other formats
      const date = new Date(dateStr);
      const day = date.getDate();
      const month = date.toLocaleString('en-US', { month: 'short' });
      const year = date.getFullYear();
      return `${day} ${month} ${year}`;
    } catch {
      return dateStr;
    }
  };

  return (
    <div className={showCloseButton 
      ? "fixed inset-0 bg-black bg-opacity-60 z-50"
      : "relative w-full"
    }>
      <div className="h-full overflow-y-auto">
        <div
          className={`${showCloseButton ? "max-w-[1400px] mx-auto min-h-screen px-4 py-6" : "w-full mx-auto"} bg-surface rounded-xl shadow-2xl`}
        >
          {/* Header */}
          <div className="results-header">
            <div>
              <h2 className="results-title">Backtest Results</h2>
              <p className="results-meta mt-1">
                {stats.totalTrades} trades · {results.meta?.date_range || ''}
                {slippagePct > 0 ? ` · ${slippagePct}% slippage` : ''}
                {chargesEnabled ? ' · Zerodha charges applied' : ''}
              </p>
              {filterInfo && (
                <span className="mt-2 inline-flex items-center rounded-full px-3 py-0.5 text-xs font-semibold"
                  style={{ background: 'var(--accent-bg)', color: 'var(--accent)', border: '1px solid var(--border-accent)' }}>
                  {filterInfo}
                </span>
              )}
            </div>
            <div className="flex gap-2 items-center">
              <button onClick={exportToCSV} className="run-btn px-4 py-2" style={{ fontSize: '0.7rem', borderRadius: '7px' }}>
                <Download size={14} /> Export Excel
              </button>
              {showCloseButton && (
                <button onClick={onClose} className="p-2 rounded-lg transition-colors"
                  style={{ color: 'var(--text-muted)', background: 'transparent' }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-hover)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                  <X size={18} />
                </button>
              )}
            </div>
          </div>

          {/* Summary Cards */}
          {filteredWarnings.length > 0 && (
            <div className="mb-3 px-4 py-2 rounded-lg text-xs" style={{ border: '1px solid var(--warning-border, rgba(245,158,11,0.4))', background: 'var(--warning-bg)', color: 'var(--warning)' }}>
              {filteredWarnings.map((msg, idx) => (
                <p key={`${msg}-${idx}`} className="leading-tight">
                  ⚠️ {msg}
                </p>
              ))}
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4 p-6" style={{ background: 'var(--bg-base)' }}>
            <div className="bg-surface rounded-xl p-4 shadow-sm border border-default">
              <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">Total P&L</p>
              <p className={`text-2xl font-bold ${stats.totalPnLPct >= 0 ? 'text-profit' : 'text-loss'}`}>
                {stats.totalPnLPct >= 0 ? '+' : ''}{stats.totalPnLPct.toFixed(2)}%
              </p>
            </div>
            
            <div className="bg-surface rounded-xl p-4 shadow-sm border border-default">
              <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">Win Rate</p>
              <p className="text-2xl font-bold text-accent">
                {stats.winRate.toFixed(1)}%
              </p>
            </div>

            <div className="bg-surface rounded-xl p-4 shadow-sm border border-default">
              <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">CAGR</p>
              <p className={`text-2xl font-bold ${stats.cagr >= 0 ? 'text-profit' : 'text-loss'}`}>
                {stats.cagr.toFixed(1)}%
              </p>
            </div>
            
            <div className="bg-surface rounded-xl p-4 shadow-sm border border-default">
              <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">Max DD</p>
              <p className="text-2xl font-bold text-loss">
                {stats.maxDDPct.toFixed(2)}%
              </p>
            </div>
            
            <div className="bg-surface rounded-xl p-4 shadow-sm border border-default">
              <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">Trades</p>
              <p className="text-2xl font-bold text-secondary">
                {stats.totalTrades}
              </p>
            </div>
          </div>

          {/* Charts */}
          <div className="p-6 space-y-6" style={{ background: 'var(--bg-elevated)' }}>
            {/* Equity Curve */}
            <div className="chart-panel">
              <h3 className="chart-panel-title">Equity Curve (Cumulative P&L)</h3>
              <div style={{ position: 'relative', zIndex: 0 }}>
              <ResponsiveContainer width="100%" height={300}>
                <AreaChart data={equityData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
                  <defs>
                    <linearGradient id="colorEquity" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="var(--chart-equity)" stopOpacity={0.15}/>
                      <stop offset="95%" stopColor="var(--chart-equity)" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" vertical={false} />
                  <XAxis
                    dataKey="date"
                    stroke="var(--border-default)"
                    tick={{ fontSize: 10, fill: 'var(--text-secondary)' }}
                    tickLine={false}
                    tickFormatter={formatDateShort}
                    interval="preserveStartEnd"
                    minTickGap={50}
                  />
                  <YAxis
                    stroke="var(--border-default)"
                    tick={{ fontSize: 11, fill: 'var(--text-secondary)' }}
                    tickLine={false}
                    tickFormatter={(value) => value.toFixed(1)}
                    domain={equityDomain}
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <Area
                    type="monotone"
                    dataKey="cumulative"
                    stroke="var(--chart-equity)"
                    strokeWidth={2}
                    fill="url(#colorEquity)"
                    name="Cumulative P&L"
                    isAnimationActive={false}
                    connectNulls={true}
                    baseValue={equityDomain[0]}
                    dot={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
              </div>
            </div>

            {/* Drawdown */}
            <div className="chart-panel">
              <h3 className="chart-panel-title">Drawdown</h3>
              <div style={{ position: 'relative', zIndex: 0 }}>
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart data={drawdownData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" vertical={false} />
                  <XAxis
                    dataKey="date"
                    stroke="var(--border-default)"
                    tick={{ fontSize: 10, fill: 'var(--text-secondary)' }}
                    tickLine={false}
                    tickFormatter={formatDateShort}
                    interval="preserveStartEnd"
                    minTickGap={50}
                  />
                  <YAxis
                    stroke="var(--border-default)"
                    tick={{ fontSize: 11, fill: 'var(--text-secondary)' }}
                    tickLine={false}
                    tickFormatter={(value) => `${value.toFixed(1)}%`}
                    domain={drawdownDomain}
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <ReferenceLine y={0} stroke="var(--border-strong)" strokeWidth={1} />
                  <Area
                    type="monotone"
                    dataKey="drawdown"
                    stroke="var(--chart-drawdown)"
                    strokeWidth={1.5}
                    fill="var(--loss-bg)"
                    name="Drawdown"
                    isAnimationActive={false}
                    connectNulls={true}
                    baseValue={0}
                    dot={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
              </div>
            </div>

            {/* Monthly Returns */}
            {(() => {
              const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
              const headers = pivot.headers && pivot.headers.length > 0
                ? pivot.headers
                : ['Year', ...MONTHS, 'Total', 'Max DD', 'DD Days', 'R/MDD'];

              const extrasByYear = {};
              (pivot.rows || []).forEach(r => {
                const yr = String(r?.[0] ?? '');
                if (!yr) return;
                extrasByYear[yr] = [r?.[14] ?? '', r?.[15] ?? '', r?.[16] ?? ''];
              });

              const parseToYearMonth = (d) => {
                if (!d && d !== 0) return null;
                const s = String(d).trim();
                if (!s) return null;
                const parts = s.includes('/') ? s.split('/') : s.split('-');
                if (parts.length !== 3) return null;
                let dd, mm, yy;
                if (parts[0].length === 4) { yy = parts[0]; mm = parts[1]; dd = parts[2]; }
                else { dd = parts[0]; mm = parts[1]; yy = parts[2]; }
                const year = String(yy);
                const monthIdx = parseInt(mm, 10) - 1;
                if (!year || !Number.isFinite(monthIdx) || monthIdx < 0 || monthIdx > 11) return null;
                return { year, monthIdx };
              };

              const byYM = {};
              groupedTrades.forEach(group => {
                const exitDate = group.exitDate || '';
                const ym = parseToYearMonth(exitDate);
                if (!ym) return;
                const net = Number(group.totalPnl ?? 0) || 0;
                if (!byYM[ym.year]) byYM[ym.year] = Array(12).fill(0);
                byYM[ym.year][ym.monthIdx] = (byYM[ym.year][ym.monthIdx] || 0) + net;
              });

              const rows = Object.entries(byYM).sort().map(([yr, mos]) => {
                const total = mos.reduce((s, v) => s + v, 0);
                const extras = extrasByYear[yr] || ['', '', ''];
                return [yr, ...mos.map(v => +v.toFixed(2)), +total.toFixed(2), ...extras];
              });

              if (!rows || rows.length === 0) return null;

              return (
              <div className="chart-panel">
                <h3 className="chart-panel-title">Monthly Returns</h3>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="border-b-2 border-strong">
                        {headers.map((header, idx) => (
                          <th key={idx} className="px-4 py-3 text-center text-xs font-bold text-secondary uppercase tracking-wider">
                            {header}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row, rowIdx) => (
                        <tr key={rowIdx} className="border-b border-default">
                          {row.map((cell, cellIdx) => {
                            const isNumeric = typeof cell === 'number';
                            const isPositive = isNumeric && cell > 0;
                            const isNegative = isNumeric && cell < 0;
                            
                            return (
                              <td 
                                key={cellIdx} 
                                className={`px-4 py-3 text-center ${
                                  cellIdx === 0 ? 'font-bold text-primary' : ''
                                } ${
                                  isPositive ? 'text-profit font-semibold' : 
                                  isNegative ? 'text-loss font-semibold' : 
                                  'text-muted'
                                }`}
                              >
                                {isNumeric ? cell.toFixed(2) : cell || '-'}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              );
            })()}

            {/* Detailed Statistics Summary */}
            <div className="bg-surface rounded-xl p-4 shadow-sm border border-default">
              <h3 className="chart-panel-title">Detailed Statistics</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-2 text-xs">
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Overall Profit</p>
                  <p className="font-normal text-primary">
                    {stats.totalPnLPct >= 0 ? '+' : ''}{stats.totalPnLPct.toFixed(2)}%
                  </p>
                </div>

                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">CAGR</p>
                  <p className="font-normal text-primary">
                    {stats.cagr >= 0 ? '+' : ''}{stats.cagr.toFixed(2)}%
                  </p>
                </div>

                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">No. of Trades</p>
                  <p className="font-normal text-primary">{stats.totalTrades}</p>
                </div>
                
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Average Profit per Trade</p>
                  <p className="font-normal text-primary">
                    {stats.avgWinPct >= 0 ? '+' : ''}{Math.abs(stats.avgWinPct).toFixed(2)}%
                  </p>
                </div>
                
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Win %</p>
                  <p className="font-normal text-primary">{stats.winRate.toFixed(2)}</p>
                </div>
                
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Loss %</p>
                  <p className="font-normal text-primary">{stats.lossPct.toFixed(2)}</p>
                </div>
                
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Average Profit on Winning Trades</p>
                  <p className="font-normal text-primary">+{Math.abs(stats.avgWinPct).toFixed(2)}%</p>
                </div>
                
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Average Loss on Losing Trades</p>
                  <p className="font-normal text-primary">{stats.avgLossPct.toFixed(2)}%</p>
                </div>
                
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Max Profit in Single Trade</p>
                  <p className="font-normal text-primary">₹{stats.maxWin.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</p>
                </div>
                
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Max Loss in Single Trade</p>
                  <p className="font-normal text-primary">₹{stats.maxLoss.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</p>
                </div>
                
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Max Drawdown</p>
                  <p className="font-normal text-primary">{stats.maxDDPct.toFixed(2)}%</p>
                </div>
                
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Max DD Days</p>
                  <p className="font-normal text-primary">
                    {stats.mddDuration > 0 ? stats.mddDuration : 'N/A'}
                  </p>
                </div>
                
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Return/MaxDD</p>
                  <p className="font-normal text-primary">{stats.recoveryFactor.toFixed(2)}</p>
                </div>
                
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Reward to Risk Ratio</p>
                  <p className="font-normal text-primary">{stats.rewardToRisk.toFixed(2)}</p>
                </div>
                
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Expectancy Ratio</p>
                  <p className="font-normal text-primary">{stats.expectancy.toFixed(2)}</p>
                </div>
                
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Max Win Streak (trades)</p>
                  <p className="font-normal text-primary">{stats.maxWinStreak}</p>
                </div>
                
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Max Losing Streak (trades)</p>
                  <p className="font-normal text-primary">{stats.maxLossStreak}</p>
                </div>
                
                <div className="border-b border-default pb-2">
                  <p className="font-bold text-primary mb-0.5">Max trades in any drawdown</p>
                  <p className="font-normal text-primary">{stats.mddTradeNumber || 'N/A'}</p>
                </div>
              </div>
            </div>

            {/* Full Report Table */}
            <div className="chart-panel">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-base font-bold text-primary">Full Report</h3>
                <div className="text-sm text-secondary">
                  Showing <span className="font-semibold">{((currentPage - 1) * itemsPerPage) + 1} - {Math.min(currentPage * itemsPerPage, groupedTrades.length)}</span> trades out of <span className="font-semibold">{groupedTrades.length}</span>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-base border-b-2 border-strong">
                      <th className="px-3 py-3 text-left text-xs font-bold text-primary">Index</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-primary">Entry Date</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-primary">Exit Date</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-primary">Entry Spot</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-primary">Exit Spot</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-primary">Type</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-primary">Strike</th>
                      {bufferStrikeEnabled && (
                        <th className="px-3 py-3 text-right text-xs font-bold text-primary">Buffer Ref</th>
                      )}
                      <th className="px-3 py-3 text-left text-xs font-bold text-primary">B/S</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-primary">Qty</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-primary">Entry Price</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-primary">Exit Price</th>
                      {chargesEnabled && (
                        <th className="px-3 py-3 text-right text-xs font-bold text-primary">Charges ₹</th>
                      )}
                      <th className="px-3 py-3 text-right text-xs font-bold text-primary">Net P&L</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-primary">% P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {groupedTrades.slice((currentPage - 1) * itemsPerPage, currentPage * itemsPerPage).map((group, groupIdx) => {
                      const rowsToRender = group.displayRows || group.legs;
                      const hasReEntries = Boolean(group.hasReEntries);

                      return (
                        <React.Fragment key={group.groupKey}>
                          {hasReEntries ? rowsToRender.map((leg, rowIdx) => {
                            const isLazy = isLazyLegRow(leg);
                            const isReEntryRow = Boolean(leg['ReEntryIndex'] || leg['ReEntryTrigger'] || leg['ReEntryMode'] || isLazy);
                            const optionType = leg['Type'] || leg['Leg_1_Type'] || 'CE';
                            const strike = leg['Strike'] || leg['Leg_1_Strike'] || leg['Leg 1 Strike'] || 0;
                            const bufferRef = parseFloat(leg.buffer_ref_price);
                            const position = leg['B/S'] || leg['Leg_1_Position'] || 'Sell';
                            const qty = parseInt(leg['Qty']) || parseInt(leg.qty) || parseInt(leg.quantity) || 65;
                            const rawEntryPrice = parseFloat(leg['Raw Entry Price']);
                            const entryPrice = parseFloat(leg['Entry Price']) || parseFloat(leg['Leg_1_EntryPrice']) || parseFloat(leg['Leg 1 Entry']) || 0;
                            const rawExitPrice = parseFloat(leg['Raw Exit Price']);
                            const exitPrice = parseFloat(leg['Exit Price']) || parseFloat(leg['Leg_1_ExitPrice']) || parseFloat(leg['Leg 1 Exit']) || 0;
                            const isFutLeg = (leg['Type'] || '').toUpperCase() === 'FUT';
                            const legNetPnlPoints = isFutLeg
                              ? (parseFloat(leg['FUT P&L']) || 0)
                              : (parseFloat(leg['CE P&L']) || parseFloat(leg['PE P&L']) || 0);
                            const entrySpotForPct = parseFloat(leg['Entry Spot']) || 0;
                            const legPercentPnl = entrySpotForPct > 1000
                              ? (legNetPnlPoints / entrySpotForPct) * 100
                              : 0;
                            const rowBg = rowIdx % 2 === 0 ? '' : 'bg-elevated';
                            const exitDateValue = (() => {
                              const ownExit = getVisibleExitDate(leg);
                              if (ownExit && String(ownExit).trim() !== '') return ownExit;
                              return group.exitDate || '-';
                            })();

                            return (
                              <tr key={`${group.tradeNumber}-${rowIdx}`} className={`border-b border-default ${rowBg}`}>
                                <td className="px-3 py-2 text-xs text-primary">{leg['Index'] || group.tradeNumber}</td>
                                <td className="px-3 py-2 text-xs text-primary">{leg['Entry Date'] || group.entryDate || '-'}</td>
                                <td className="px-3 py-2 text-xs text-primary">{exitDateValue}</td>
                                <td className="px-3 py-2 text-xs text-right text-primary">{Number.isFinite(parseFloat(leg['Entry Spot'])) ? parseFloat(leg['Entry Spot']).toFixed(2) : (group.entrySpot || 0).toFixed(2)}</td>
                                <td className="px-3 py-2 text-xs text-right text-primary">{Number.isFinite(parseFloat(leg['Exit Spot'])) ? parseFloat(leg['Exit Spot']).toFixed(2) : (group.exitSpot || 0).toFixed(2)}</td>
                                <td className="px-3 py-2 text-xs text-secondary">{optionType}</td>
                                <td className="px-3 py-2 text-xs text-right text-secondary">{parseFloat(strike).toFixed(0)}</td>
                                {bufferStrikeEnabled && (
                                  <td className="px-3 py-2 text-xs text-right text-muted">
                                    {Number.isFinite(bufferRef) ? bufferRef.toFixed(2) : '—'}
                                  </td>
                                )}
                                <td className="px-3 py-2 text-xs text-secondary">
                                  {position}
                                  {isReEntryRow && (
                                    <span className="ml-2 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold" style={{ background: 'var(--accent-bg)', color: 'var(--accent)', border: '1px solid var(--accent)' }}>
                                      {isLazy ? `LAZY-${leg['Lazy Leg Name'] || 'LEG'}` : `RE-${leg['ReEntryTrigger']}`}
                                    </span>
                                  )}
                                </td>
                                <td className="px-3 py-2 text-xs text-right text-secondary">{qty}</td>
                                <td className="px-3 py-2 text-xs text-right text-secondary">{entryPrice.toFixed(2)}</td>
                                <td className="px-3 py-2 text-xs text-right text-secondary">{exitPrice.toFixed(2)}</td>
                                {chargesEnabled && (
                                  <td className="px-3 py-2 text-xs text-right text-warning">
                                    {(() => {
                                      const c = parseFloat(leg['Charges']);
                                      return Number.isFinite(c) ? `₹${c.toFixed(2)}` : '—';
                                    })()}
                                  </td>
                                )}
                                <td className={`px-3 py-2 text-xs text-right ${legNetPnlPoints >= 0 ? 'text-profit' : 'text-loss'}`}>
                                  {legNetPnlPoints >= 0 ? '+' : ''}{legNetPnlPoints.toFixed(2)}
                                </td>
                                <td className={`px-3 py-2 text-xs text-right ${legPercentPnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                                  {legPercentPnl >= 0 ? '+' : ''}{legPercentPnl.toFixed(2)}%
                                </td>
                              </tr>
                            );
                          }) : group.legs.map((leg, legIdx) => {
                            const isFirstLeg = legIdx === 0;
                            
                            const optionType = leg['Type'] || leg['Leg_1_Type'] || 'CE';
                            const strike = leg['Strike'] || leg['Leg_1_Strike'] || leg['Leg 1 Strike'] || 0;
                            const bufferRef = parseFloat(leg.buffer_ref_price);
                            const bufferOffsetRaw = leg.buffer_strike_offset;
                            const bufferOffset = Number.isFinite(Number(bufferOffsetRaw)) ? Number(bufferOffsetRaw) : null;
                            const position = leg['B/S'] || leg['Leg_1_Position'] || 'Sell';
                            const qty = parseInt(leg['Qty']) || parseInt(leg.qty) || parseInt(leg.quantity) || 65;
                            const rawEntryPrice = parseFloat(leg['Raw Entry Price']);
                            const entryPrice = parseFloat(leg['Entry Price']) || parseFloat(leg['Leg_1_EntryPrice']) || parseFloat(leg['Leg 1 Entry']) || 0;
                            const rawExitPrice = parseFloat(leg['Raw Exit Price']);
                            const exitPrice = parseFloat(leg['Exit Price']) || parseFloat(leg['Leg_1_ExitPrice']) || parseFloat(leg['Leg 1 Exit']) || 0;
                            const isFutLeg = (leg['Type'] || '').toUpperCase() === 'FUT';
                            const legNetPnlPoints = isFutLeg
                              ? (parseFloat(leg['FUT P&L']) || 0)
                              : (parseFloat(leg['CE P&L']) || parseFloat(leg['PE P&L']) || 0);
                            const entrySpotForPct = parseFloat(leg['Entry Spot']) || 0;
                            const legPercentPnl = entrySpotForPct > 1000
                              ? (legNetPnlPoints / entrySpotForPct) * 100
                              : 0;
                            
                            return (
                              <tr key={`${group.tradeNumber}-${legIdx}`} className={legIdx % 2 === 0 ? 'border-b border-default' : 'border-b border-default bg-elevated'}>
                                {isFirstLeg ? (
                                  <>
                                    <td className="px-3 py-2 text-xs text-primary" rowSpan={group.legs.length}>{group.legs[0]['Index'] || group.tradeNumber}</td>
                                    <td className="px-3 py-2 text-xs text-primary" rowSpan={group.legs.length}>{group.entryDate || '-'}</td>
                                  </>
                                ) : null}
                                <td className="px-3 py-2 text-xs text-primary">
                                  {(() => {
                                    const ownExit = getVisibleExitDate(leg);
                                    if (ownExit && String(ownExit).trim() !== '') return ownExit;
                                    return legIdx === 0 ? (group.exitDate || '-') : '-';
                                  })()}
                                </td>
                                {isFirstLeg ? (
                                  <>
                                    <td className="px-3 py-2 text-xs text-right text-primary" rowSpan={group.legs.length}>{(group.entrySpot || 0).toFixed(2)}</td>
                                    <td className="px-3 py-2 text-xs text-right text-primary" rowSpan={group.legs.length}>{(group.exitSpot || 0).toFixed(2)}</td>
                                  </>
                                ) : null}
                                <td className="px-3 py-2 text-xs text-secondary">{optionType}</td>
                                <td className="px-3 py-2 text-xs text-right text-secondary">{parseFloat(strike).toFixed(0)}</td>
                                {bufferStrikeEnabled && (
                                  <td className="px-3 py-2 text-xs text-right text-muted">
                                    {Number.isFinite(bufferRef) ? bufferRef.toFixed(2) : '—'}
                                  </td>
                                )}
                                <td className="px-3 py-2 text-xs text-secondary">{position}</td>
                                <td className="px-3 py-2 text-xs text-right text-secondary">{qty}</td>
                                <td className="px-3 py-2 text-xs text-right text-secondary">{entryPrice.toFixed(2)}</td>
                                <td className="px-3 py-2 text-xs text-right text-secondary">{exitPrice.toFixed(2)}</td>
                                {chargesEnabled && (
                                  <td className="px-3 py-2 text-xs text-right text-warning">
                                    {(() => {
                                      const c = parseFloat(leg['Charges']);
                                      return Number.isFinite(c) ? `₹${c.toFixed(2)}` : '—';
                                    })()}
                                  </td>
                                )}
                                <td className={`px-3 py-2 text-xs text-right ${legNetPnlPoints >= 0 ? 'text-profit' : 'text-loss'}`}>
                                  {legNetPnlPoints >= 0 ? '+' : ''}{legNetPnlPoints.toFixed(2)}
                                </td>
                                <td className={`px-3 py-2 text-xs text-right ${legPercentPnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                                  {legPercentPnl >= 0 ? '+' : ''}{legPercentPnl.toFixed(2)}%
                                </td>
                              </tr>
                            );
                          })}
                          
                          {/* Summary Row - Only show for multi-leg trades */}
                          {rowsToRender.length > 1 && (() => {
                            const tradeNetPnlPoints = Number.isFinite(Number(group.totalPnl))
                              ? Number(group.totalPnl)
                              : rowsToRender.reduce((sum, leg) => {
                                  const raw = parseFloat(leg['Net P&L']);
                                  return sum + (Number.isFinite(raw) ? raw : 0);
                                }, 0);
                            const tradePctPnl = group.entrySpot > 1000
                              ? (tradeNetPnlPoints / group.entrySpot) * 100
                              : 0;
                            const totalChargesInr = chargesEnabled
                              ? rowsToRender.reduce((sum, leg) => sum + (parseFloat(leg['Charges']) || 0), 0)
                              : 0;
                            // 11 always-present columns before Net P&L and % P&L:
                            // Index, Entry Date, Exit Date, Entry Spot, Exit Spot, Type, Strike, B/S, Qty, Entry Price, Exit Price
                            // + 1 if bufferStrikeEnabled (Buffer Ref column)
                            // Charges column is handled as its own <td> below, so excluded here.
                            const emptyCellSpan = 11 + (bufferStrikeEnabled ? 1 : 0);
                            return (
                              <tr className={groupIdx % 2 === 0 ? 'border-b-2 border-strong' : 'border-b-2 border-strong bg-elevated'}>
                                <td colSpan={emptyCellSpan}></td>
                                {chargesEnabled && (
                                  <td className="px-3 py-2 text-right text-xs font-bold text-warning">
                                    ₹{totalChargesInr.toFixed(2)}
                                  </td>
                                )}
                                <td className={`px-3 py-2 text-right text-xs font-bold ${tradeNetPnlPoints >= 0 ? 'text-profit' : 'text-loss'}`}>
                                  {tradeNetPnlPoints >= 0 ? '+' : ''}{tradeNetPnlPoints.toFixed(2)}
                                </td>
                                <td className={`px-3 py-2 text-right text-xs font-bold ${tradePctPnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                                  {tradePctPnl >= 0 ? '+' : ''}{tradePctPnl.toFixed(2)}%
                                </td>
                              </tr>
                            );
                          })()}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              
              {/* Pagination */}
              <div className="flex justify-center items-center gap-2 mt-4 pt-4 border-t">
                <button 
                  onClick={() => setCurrentPage(1)} 
                  disabled={currentPage === 1}
                  className="px-2 py-1 text-sm text-secondary hover:text-primary disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  ≪
                </button>
                <button 
                  onClick={() => setCurrentPage(p => Math.max(1, p - 1))} 
                  disabled={currentPage === 1}
                  className="px-2 py-1 text-sm text-secondary hover:text-primary disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  ‹
                </button>
                {[...Array(Math.min(6, Math.ceil(groupedTrades.length / itemsPerPage)))].map((_, i) => {
                  const totalPages = Math.ceil(groupedTrades.length / itemsPerPage);
                  let pageNum = i + 1;
                  if (totalPages > 6) {
                    if (currentPage <= 3) pageNum = i + 1;
                    else if (currentPage >= totalPages - 2) pageNum = totalPages - 5 + i;
                    else pageNum = currentPage - 2 + i;
                  }
                  return (
                    <button
                      key={i}
                      onClick={() => setCurrentPage(pageNum)}
                      className={`px-3 py-1 text-sm rounded ${
                        currentPage === pageNum 
                          ? 'bg-accent text-inverse text-white' 
                          : 'bg-surface text-secondary hover:bg-base border border-strong'
                      }`}
                    >
                      {pageNum}
                    </button>
                  );
                })}
                <button 
                  onClick={() => setCurrentPage(p => Math.min(Math.ceil(groupedTrades.length / itemsPerPage), p + 1))} 
                  disabled={currentPage === Math.ceil(groupedTrades.length / itemsPerPage)}
                  className="px-2 py-1 text-sm text-secondary hover:text-primary disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  ›
                </button>
                <button 
                  onClick={() => setCurrentPage(Math.ceil(groupedTrades.length / itemsPerPage))} 
                  disabled={currentPage === Math.ceil(groupedTrades.length / itemsPerPage)}
                  className="px-2 py-1 text-sm text-secondary hover:text-primary disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  ≫
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ResultsPanel;
