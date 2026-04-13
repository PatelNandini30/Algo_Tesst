import React, { useMemo, useState, useEffect } from 'react';
import {
  AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine
} from 'recharts';
import { Download, X } from 'lucide-react';
import EquityCurve from './EquityCurve';
import PerformanceMetrics from './PerformanceMetrics';
import MonthlyPnlHeatmap from './MonthlyPnlHeatmap';
import TradeLog from './TradeLog';

const ResultsPanel = ({ results, onClose, showCloseButton = true, filterInfo, showStrSegment = false }) => {
  if (!results) return null;

  useEffect(() => {
    console.log('[ResultsPanel] mounted (single instance check)');
    return () => {
      console.log('[ResultsPanel] unmounted');
    };
  }, []);
  
  console.log('[ResultsPanel] results:', JSON.stringify(results, null, 2).slice(0, 500));
  const { trades = [], summary = {}, pivot = {} } = results;
  const returns = results?.returns || {};
  const warnings = results?.warnings || [];
  const costBreakdown = returns.cost_breakdown || {};
  const filteredWarnings = warnings.filter(Boolean);
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 25;

  // Two-pass grouping:
  // Pass 1 — find the canonical (first-seen) Entry Date per Trade number.
  // Pass 2 — rows whose Entry Date matches the canonical go into the main
  //           trade group; rows with a DIFFERENT Entry Date are rolled/
  //           continuation legs and get their own display group so they are
  //           never merged with the original trade.
  // Within each group, duplicate Leg numbers are deduplicated (last row wins).
  const groupedTrades = useMemo(() => {
    if (!trades || trades.length === 0) return [];

    // Pass 1: canonical entry date per Trade number
    const canonicalEntryByTrade = new Map();
    trades.forEach(trade => {
      const tradeNum = String(trade.Trade || trade.trade || 1);
      const entryDate = trade['Entry Date'] || '';
      if (!canonicalEntryByTrade.has(tradeNum)) {
        canonicalEntryByTrade.set(tradeNum, entryDate);
      }
    });

    // Pass 2: build groups (only main trades, skip rolled/continuation trades)
    const groupMap = new Map(); // groupKey → { legs: Map(legNum → row), firstRow, tradeNum }
    trades.forEach(trade => {
      const tradeNum = String(trade.Trade || trade.trade || 1);
      const legNum   = String(trade.Leg  || trade.leg  || 1);
      const entryDate = trade['Entry Date'] || '';
      const canonical = canonicalEntryByTrade.get(tradeNum) || entryDate;

      // Skip rolled/continuation trades - only include main trades with canonical entry date
      if (entryDate !== canonical) {
        return;
      }

      const groupKey = tradeNum;

      if (!groupMap.has(groupKey)) {
        groupMap.set(groupKey, { legs: new Map(), firstRow: trade, tradeNum });
      }
      // Last row for a given Leg number wins (deduplication)
      groupMap.get(groupKey).legs.set(legNum, trade);
    });

    const result = [];
    for (const [groupKey, { legs, firstRow, tradeNum }] of groupMap.entries()) {
      const legsArr = Array.from(legs.values());

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
        legs.get('1') ||
        legs.get(1) ||
        sortedLegsAsc[0] ||
        firstRow;

      const rawCumulative = leg1Row['Cumulative'];
      const rawPeak       = leg1Row['Peak'];
      const rawDd         = leg1Row['DD'];
      const rawPctDd      = leg1Row['%DD'];

      result.push({
        tradeNumber: parseInt(tradeNum, 10) || 0,
        groupKey,
        legs: legsArr,
        entryDate:  leg1Row['Entry Date'],
        exitDate:   leg1Row['Exit Date'],
        entrySpot:  parseFloat(leg1Row['Entry Spot']) || 0,
        exitSpot:   parseFloat(leg1Row['Exit Spot'])  || 0,
        totalPnl:   parseFloat(leg1Row['Net P&L']) || 0,
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
  }, [trades]);

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


  // Export CSV
  const exportToCSV = () => {
    if (!trades || trades.length === 0) return;

    const hasCalls = trades.some(t => (t['Type'] || '').toUpperCase() === 'CALL');
    const hasPuts = trades.some(t => (t['Type'] || '').toUpperCase() === 'PUT');
    const hasFutures = trades.some(t => (t['Type'] || '').toUpperCase() === 'FUT');
    const hasStrSegment = showStrSegment && trades.some(t => t['STR Segment'] && t['STR Segment'] !== '');

    const TRADE_LEVEL_COLS = new Set(['Net P&L', '% P&L', 'Cumulative', 'Peak', 'DD', '%DD']);

    const keyOrder = [
      'Trade', 'Leg', 'Index',
      'Entry Date', 'Exit Date',
      'Entry Spot', 'Exit Spot', 'Spot P&L',
      'Type', 'Strike', 'B/S', 'Qty',
      'Entry Price', 'Exit Price',
      ...(hasCalls ? ['CE P&L'] : []),
      ...(hasPuts ? ['PE P&L'] : []),
      ...(hasFutures ? ['FUT P&L'] : []),
      'Net P&L', '% P&L',
      'Cumulative', 'Peak', 'DD', '%DD',
      'Exit Reason', 'Expiry',
      ...(hasStrSegment ? ['STR Segment'] : []),
    ];

    const sortedTrades = [...trades].sort((a, b) => {
      const tA = parseInt(a.Trade || a.trade || 1, 10);
      const tB = parseInt(b.Trade || b.trade || 1, 10);
      if (tA !== tB) return tA - tB;
      const lA = parseInt(a.Leg || a.leg || 1, 10);
      const lB = parseInt(b.Leg || b.leg || 1, 10);
      return lA - lB;
    });

    const groupedByTrade = {};
    sortedTrades.forEach(trade => {
      const key = String(trade.Trade || trade.trade || 1);
      if (!groupedByTrade[key]) groupedByTrade[key] = [];
      groupedByTrade[key].push(trade);
    });

    const tradeMetrics = {};
    Object.entries(groupedByTrade).forEach(([key, legs]) => {
      const entrySpot = parseFloat(legs[0]['Entry Spot']) || 0;
      const net = legs.reduce((sum, leg) => {
        return sum +
          (parseFloat(leg['CE P&L']) || 0) +
          (parseFloat(leg['PE P&L']) || 0) +
          (parseFloat(leg['FUT P&L']) || 0);
      }, 0);
      const pct = entrySpot > 0 ? (net / entrySpot) * 100 : 0;
      const rawCumulative = legs[0]['Cumulative'];
      const rawPeak       = legs[0]['Peak'];
      const rawDd         = legs[0]['DD'];
      const rawPctDd      = legs[0]['%DD'];
      tradeMetrics[key] = {
        net: +net.toFixed(2),
        pct: +pct.toFixed(2),
        cumulative: rawCumulative !== '' && rawCumulative != null && !isNaN(parseFloat(rawCumulative))
          ? parseFloat(rawCumulative)
          : '',
        peak: rawPeak !== '' && rawPeak != null && !isNaN(parseFloat(rawPeak))
          ? parseFloat(rawPeak)
          : '',
        dd: rawDd !== '' && rawDd != null && !isNaN(parseFloat(rawDd))
          ? parseFloat(rawDd)
          : '',
        pctDd: rawPctDd !== '' && rawPctDd != null && !isNaN(parseFloat(rawPctDd))
          ? parseFloat(rawPctDd)
          : '',
      };
    });

    const writtenTrades = new Set();

    const cleanedTrades = sortedTrades.map(trade => {
      const tradeKey = String(trade.Trade || trade.trade || 1);
      const isFirstLeg = !writtenTrades.has(tradeKey);
      if (isFirstLeg) writtenTrades.add(tradeKey);

      const metrics = tradeMetrics[tradeKey] || {};
      const row = {};

      for (const key of keyOrder) {
        let val;

        if (TRADE_LEVEL_COLS.has(key)) {
          if (!isFirstLeg) {
            val = '';
          } else {
            if (key === 'Net P&L') val = metrics.net;
            else if (key === '% P&L') val = metrics.pct;
            else if (key === 'Cumulative') val = metrics.cumulative;
            else if (key === 'Peak') val = metrics.peak;
            else if (key === 'DD') val = metrics.dd;
            else if (key === '%DD') val = metrics.pctDd;
          }
        } else if (key === 'Index') {
          val = parseInt(trade.Trade || trade.trade || 1, 10);
        } else {
          val = trade[key];
        }

        if (
          val === null ||
          val === undefined ||
          (typeof val === 'number' && isNaN(val)) ||
          val === 'NaN'
        ) {
          val = '';
        }
        if (typeof val === 'number' && !Number.isInteger(val)) {
          val = Math.round(val * 100) / 100;
        }

        row[key] = val;
      }

      return row;
    });

    const escape = (value) => {
      const text = String(value ?? '');
      if (text.includes(',') || text.includes('"') || text.includes('\n')) {
        return `"${text.replace(/"/g, '""')}"`;
      }
      return text;
    };

    const headers = keyOrder.join(',');
    const rows = cleanedTrades.map(row => keyOrder.map(key => escape(row[key])).join(','));
    const csv = [headers, ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `backtest_${new Date().toISOString().split('T')[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  };

  const CustomTooltip = ({ active, payload }) => {
    if (active && payload && payload.length) {
      return (
        <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 shadow-xl">
          <p className="text-xs text-gray-400 mb-1">{payload[0]?.payload?.date}</p>
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
          className={`${showCloseButton ? "max-w-[1400px] mx-auto min-h-screen px-4 py-6" : "w-full mx-auto"} bg-white rounded-xl shadow-2xl`}
        >
          {/* Header */}
          <div className="flex justify-between items-center px-6 py-5 border-b border-gray-200">
          <div>
            <h2 className="text-2xl font-bold text-gray-900">Backtest Results</h2>
            <p className="text-sm text-gray-600 mt-1">
              {stats.totalTrades} trades • {results.meta?.date_range || ''}
            </p>
            {filterInfo && (
              <span className="mt-2 inline-flex items-center rounded-full border border-blue-100 bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
                {filterInfo}
              </span>
            )}
          </div>
            <div className="flex gap-3">
              <button
                onClick={exportToCSV}
                className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium text-sm"
              >
                <Download size={16} />
                Export CSV
              </button>
              {showCloseButton && (
                <button
                  onClick={onClose}
                  className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
                >
                  <X size={22} className="text-gray-600" />
                </button>
              )}
            </div>
          </div>

          {/* Summary Cards */}
          {filteredWarnings.length > 0 && (
            <div className="mb-3 px-4 py-2 rounded-lg border border-yellow-300 bg-yellow-50 text-xs text-yellow-800">
              {filteredWarnings.map((msg, idx) => (
                <p key={`${msg}-${idx}`} className="leading-tight">
                  ⚠️ {msg}
                </p>
              ))}
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4 p-6 bg-gradient-to-br from-gray-50 to-gray-100">
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-200">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Total P&L</p>
              <p className={`text-2xl font-bold ${stats.totalPnLPct >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                {stats.totalPnLPct >= 0 ? '+' : ''}{stats.totalPnLPct.toFixed(2)}%
              </p>
            </div>
            
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-200">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Win Rate</p>
              <p className="text-2xl font-bold text-blue-600">
                {stats.winRate.toFixed(1)}%
              </p>
            </div>

            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-200">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">CAGR</p>
              <p className={`text-2xl font-bold ${stats.cagr >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                {stats.cagr.toFixed(1)}%
              </p>
            </div>
            
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-200">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Max DD</p>
              <p className="text-2xl font-bold text-red-600">
                {stats.maxDDPct.toFixed(2)}%
              </p>
            </div>
            
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-200">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Trades</p>
              <p className="text-2xl font-bold text-gray-700">
                {stats.totalTrades}
              </p>
            </div>
          </div>

          {returns.total_cost !== undefined && results.meta?.cost_model_enabled && (
            <div className="mt-3 bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              <div className="flex flex-wrap items-center gap-2 mb-3">
                <h3 className="text-sm font-semibold text-gray-800">Cost report</h3>
                <span className="text-[11px] text-gray-500">(backend-imposed)</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div>
                  <p className="text-[11px] text-gray-500 uppercase">Gross P&L</p>
                  <p className="text-lg font-semibold text-gray-800">₹{(returns.gross_pnl || 0).toFixed(2)}</p>
                </div>
                <div>
                  <p className="text-[11px] text-gray-500 uppercase">Net P&L</p>
                  <p className="text-lg font-semibold text-gray-800">₹{(returns.net_pnl ?? 0).toFixed(2)}</p>
                </div>
                <div>
                  <p className="text-[11px] text-gray-500 uppercase">Total cost</p>
                  <p className="text-lg font-semibold text-gray-800">₹{(returns.total_cost || 0).toFixed(2)}</p>
                </div>
                <div>
                  <p className="text-[11px] text-gray-500 uppercase">Cost drag</p>
                  <p className="text-lg font-semibold text-gray-800">{(returns.cost_drag_pct || 0).toFixed(2)}%</p>
                </div>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mt-4 text-xs">
                {['brokerage', 'stt', 'exchange', 'gst', 'sebi', 'stamp_duty', 'slippage'].map((key) => (
                  <div key={key} className="bg-gray-50 rounded-lg p-2">
                    <p className="text-[10px] text-gray-500 uppercase">{key.replace('_', ' ')}</p>
                    <p className="text-sm font-semibold text-gray-800">₹{(costBreakdown[key] || 0).toFixed(2)}</p>
                  </div>
                ))}
              </div>
              <p className="mt-2 text-[11px] text-gray-500">
                Cost reconciliation is enforced: gross − total_cost = net to ₹0.01 tolerance.
              </p>
            </div>
          )}

          {/* Charts */}
          <div className="p-6 space-y-6 bg-gray-50">
            {/* Equity Curve */}
            <div className="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
              <h3 className="text-base font-bold text-gray-800 mb-4">Equity Curve (Cumulative P&L)</h3>
              <div style={{ position: 'relative', zIndex: 0 }}>
              <ResponsiveContainer width="100%" height={300}>
                <AreaChart data={equityData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
                  <defs>
                    <linearGradient id="colorEquity" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.4}/>
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.1}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" vertical={false} />
                  <XAxis
                    dataKey="date"
                    stroke="#9ca3af"
                    tick={{ fontSize: 10, fill: '#6b7280' }}
                    tickLine={false}
                    tickFormatter={formatDateShort}
                    interval="preserveStartEnd"
                    minTickGap={50}
                  />
                  <YAxis
                    stroke="#9ca3af"
                    tick={{ fontSize: 11, fill: '#6b7280' }}
                    tickLine={false}
                    tickFormatter={(value) => value.toFixed(1)}
                    domain={equityDomain}
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <Area
                    type="monotone"
                    dataKey="cumulative"
                    stroke="#3b82f6"
                    strokeWidth={2}
                    fill="url(#colorEquity)"
                    name="Cumulative P&L"
                    isAnimationActive={false}
                    connectNulls={true}
                    baseValue={equityDomain[0]}
                  />
                </AreaChart>
              </ResponsiveContainer>
              </div>
            </div>

            {/* Drawdown */}
            <div className="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
              <h3 className="text-base font-bold text-gray-800 mb-4">Drawdown</h3>
              <div style={{ position: 'relative', zIndex: 0 }}>
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart data={drawdownData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
                  <defs>
                    <linearGradient id="colorDrawdown" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#ef4444" stopOpacity={0.4}/>
                      <stop offset="95%" stopColor="#ef4444" stopOpacity={0.1}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" vertical={false} />
                  <XAxis
                    dataKey="date"
                    stroke="#9ca3af"
                    tick={{ fontSize: 10, fill: '#6b7280' }}
                    tickLine={false}
                    tickFormatter={formatDateShort}
                    interval="preserveStartEnd"
                    minTickGap={50}
                  />
                  <YAxis
                    stroke="#9ca3af"
                    tick={{ fontSize: 11, fill: '#6b7280' }}
                    tickLine={false}
                    tickFormatter={(value) => `${value.toFixed(1)}%`}
                    domain={drawdownDomain}
                  />
                  <Tooltip content={<CustomTooltip />} />
                  {/* Zero baseline — visible even during periods of zero drawdown */}
                  <ReferenceLine y={0} stroke="#9ca3af" strokeWidth={1} />
                  <Area
                    type="monotone"
                    dataKey="drawdown"
                    stroke="#ef4444"
                    strokeWidth={2}
                    fill="url(#colorDrawdown)"
                    name="Drawdown"
                    isAnimationActive={false}
                    connectNulls={true}
                    baseValue={0}
                  />
                </AreaChart>
              </ResponsiveContainer>
              </div>
            </div>

            {/* Monthly Returns */}
            {pivot.rows && pivot.rows.length > 0 && (
              <div className="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
                <h3 className="text-base font-bold text-gray-800 mb-4">Monthly Returns</h3>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="border-b-2 border-gray-300">
                        {pivot.headers?.map((header, idx) => (
                          <th key={idx} className="px-4 py-3 text-center text-xs font-bold text-gray-700 uppercase tracking-wider">
                            {header}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {pivot.rows?.map((row, rowIdx) => (
                        <tr key={rowIdx} className="border-b border-gray-200 hover:bg-gray-50 transition-colors">
                          {row.map((cell, cellIdx) => {
                            const isNumeric = typeof cell === 'number';
                            const isPositive = isNumeric && cell > 0;
                            const isNegative = isNumeric && cell < 0;
                            
                            return (
                              <td 
                                key={cellIdx} 
                                className={`px-4 py-3 text-center ${
                                  cellIdx === 0 ? 'font-bold text-gray-900' : ''
                                } ${
                                  isPositive ? 'text-green-600 font-semibold' : 
                                  isNegative ? 'text-red-600 font-semibold' : 
                                  'text-gray-500'
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
            )}

            {/* Detailed Statistics Summary */}
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-200">
              <h3 className="text-sm font-bold text-gray-800 mb-3">Detailed Statistics</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-2 text-xs">
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Overall Profit</p>
                  <p className="font-normal text-gray-900">
                    {stats.totalPnLPct >= 0 ? '+' : ''}{stats.totalPnLPct.toFixed(2)}%
                  </p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">No. of Trades</p>
                  <p className="font-normal text-gray-900">{stats.totalTrades}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Average Profit per Trade</p>
                  <p className="font-normal text-gray-900">
                    {stats.avgWinPct >= 0 ? '+' : ''}{Math.abs(stats.avgWinPct).toFixed(2)}%
                  </p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Win %</p>
                  <p className="font-normal text-gray-900">{stats.winRate.toFixed(2)}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Loss %</p>
                  <p className="font-normal text-gray-900">{stats.lossPct.toFixed(2)}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Average Profit on Winning Trades</p>
                  <p className="font-normal text-gray-900">+{Math.abs(stats.avgWinPct).toFixed(2)}%</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Average Loss on Losing Trades</p>
                  <p className="font-normal text-gray-900">{stats.avgLossPct.toFixed(2)}%</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Max Profit in Single Trade</p>
                  <p className="font-normal text-gray-900">₹{stats.maxWin.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Max Loss in Single Trade</p>
                  <p className="font-normal text-gray-900">₹{stats.maxLoss.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Max Drawdown</p>
                  <p className="font-normal text-gray-900">{stats.maxDDPct.toFixed(2)}%</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Max DD Days</p>
                  <p className="font-normal text-gray-900">
                    {stats.mddDuration > 0 ? stats.mddDuration : 'N/A'}
                  </p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Return/MaxDD</p>
                  <p className="font-normal text-gray-900">{stats.recoveryFactor.toFixed(2)}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Reward to Risk Ratio</p>
                  <p className="font-normal text-gray-900">{stats.rewardToRisk.toFixed(2)}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Expectancy Ratio</p>
                  <p className="font-normal text-gray-900">{stats.expectancy.toFixed(2)}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Max Win Streak (trades)</p>
                  <p className="font-normal text-gray-900">{stats.maxWinStreak}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Max Losing Streak (trades)</p>
                  <p className="font-normal text-gray-900">{stats.maxLossStreak}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Max trades in any drawdown</p>
                  <p className="font-normal text-gray-900">{stats.mddTradeNumber || 'N/A'}</p>
                </div>
              </div>
            </div>

            <div className="space-y-4">
              <PerformanceMetrics summary={summary} />
              <div className="grid gap-4 lg:grid-cols-2">
                <EquityCurve data={equityCurveData} />
                <MonthlyPnlHeatmap pivot={pivot} />
              </div>
            </div>

            {/* Full Report Table */}
            <div className="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-base font-bold text-gray-800">Full Report</h3>
                <div className="text-sm text-gray-600">
                  Showing <span className="font-semibold">{((currentPage - 1) * itemsPerPage) + 1} - {Math.min(currentPage * itemsPerPage, groupedTrades.length)}</span> trades out of <span className="font-semibold">{groupedTrades.length}</span>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-gray-200 border-b-2 border-gray-400">
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Index</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Entry Date</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Exit Date</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Entry Spot</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Exit Spot</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Type</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Strike</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">B/S</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Qty</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Entry Price</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Exit Price</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Net P&L</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">% P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {groupedTrades.slice((currentPage - 1) * itemsPerPage, currentPage * itemsPerPage).map((group, groupIdx) => {
                      
                      // Calculate totals for summary row
                      return (
                        <React.Fragment key={group.groupKey}>
                          {/* Leg rows */}
                          {group.legs.map((leg, legIdx) => {
                            const isFirstLeg = legIdx === 0;
                            
                            const optionType = leg['Type'] || leg['Leg_1_Type'] || 'CE';
                            const strike = leg['Strike'] || leg['Leg_1_Strike'] || leg['Leg 1 Strike'] || 0;
                            const position = leg['B/S'] || leg['Leg_1_Position'] || 'Sell';
                            const qty = parseInt(leg['Qty']) || parseInt(leg.qty) || parseInt(leg.quantity) || 65;
                            const entryPrice = parseFloat(leg['Entry Price']) || parseFloat(leg['Leg_1_EntryPrice']) || parseFloat(leg['Leg 1 Entry']) || 0;
                            const exitPrice = parseFloat(leg['Exit Price']) || parseFloat(leg['Leg_1_ExitPrice']) || parseFloat(leg['Leg 1 Exit']) || 0;
                            // Per-leg P&L: CE P&L for options, FUT P&L for futures.
                            // Net P&L and % P&L columns hold the trade-level total
                            // (stamped on every row) — must NOT be used per leg.
                            const isFutLeg = (leg['Type'] || '').toUpperCase() === 'FUT';
                            const legNetPnlPoints = isFutLeg
                              ? (parseFloat(leg['FUT P&L']) || 0)
                              : (parseFloat(leg['CE P&L']) || parseFloat(leg['PE P&L']) || 0);
                            const entrySpotForPct = parseFloat(leg['Entry Spot']) || 0;
                            const legPercentPnl = entrySpotForPct > 1000
                              ? (legNetPnlPoints / entrySpotForPct) * 100
                              : 0;
                            
                            return (
                              <tr key={`${group.tradeNumber}-${legIdx}`} className={`border-b border-gray-200 ${groupIdx % 2 === 0 ? 'bg-white' : 'bg-gray-50'} hover:bg-gray-100 transition-colors`}>
                                {isFirstLeg ? (
                                  <>
                                    <td className="px-3 py-2 text-xs text-gray-900" rowSpan={group.legs.length}>{group.legs[0]['Index'] || group.tradeNumber}</td>
                                    <td className="px-3 py-2 text-xs text-gray-900" rowSpan={group.legs.length}>{group.entryDate || '-'}</td>
                                  </>
                                ) : null}
                                {/* Show individual exit date for each leg */}
                                <td className="px-3 py-2 text-xs text-gray-900">{leg['Exit Date'] || group.exitDate || '-'}</td>
                                {isFirstLeg ? (
                                  <>
                                    <td className="px-3 py-2 text-xs text-right text-gray-900" rowSpan={group.legs.length}>{(group.entrySpot || 0).toFixed(2)}</td>
                                    <td className="px-3 py-2 text-xs text-right text-gray-900" rowSpan={group.legs.length}>{(group.exitSpot || 0).toFixed(2)}</td>
                                  </>
                                ) : null}
                                <td className="px-3 py-2 text-xs text-gray-700">{optionType}</td>
                                <td className="px-3 py-2 text-xs text-right text-gray-700">{parseFloat(strike).toFixed(0)}</td>
                                <td className="px-3 py-2 text-xs text-gray-700">{position}</td>
                                <td className="px-3 py-2 text-xs text-right text-gray-700">{qty}</td>
                                <td className="px-3 py-2 text-xs text-right text-gray-700">{entryPrice.toFixed(2)}</td>
                                <td className="px-3 py-2 text-xs text-right text-gray-700">{exitPrice.toFixed(2)}</td>
                                <td className={`px-3 py-2 text-xs text-right ${legNetPnlPoints >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                                  {legNetPnlPoints >= 0 ? '+' : ''}{legNetPnlPoints.toFixed(2)}
                                </td>
                                <td className={`px-3 py-2 text-xs text-right ${legPercentPnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                                  {legPercentPnl >= 0 ? '+' : ''}{legPercentPnl.toFixed(2)}%
                                </td>
                              </tr>
                            );
                          })}
                          
                          {/* Summary Row - Only show for multi-leg trades */}
                          {group.legs.length > 1 && (() => {
                            // Sum per-leg individual P&L columns.
                            // DO NOT sum Net P&L — it is the trade total repeated on every row.
                            const tradeNetPnlPoints = group.legs.reduce((sum, leg) => {
                              return sum +
                                (parseFloat(leg['CE P&L'])  || 0) +
                                (parseFloat(leg['PE P&L'])  || 0) +
                                (parseFloat(leg['FUT P&L']) || 0);
                            }, 0);
                            const tradePctPnl = group.entrySpot > 1000
                              ? (tradeNetPnlPoints / group.entrySpot) * 100
                              : 0;
                            // Summary row comes AFTER the rowSpan ends, so ALL 13 columns
                            // must be filled. Empty span = 13 total - 2 P&L cols = 11.
                            // (The old value of 7 wrongly subtracted the 4 rowSpanned cols,
                            // which pushed Net P&L into the B/S column and % P&L into Qty.)
                            const emptyCellSpan = 11;
                            return (
                              <tr className="border-b-2 border-gray-300 bg-slate-100">
                                <td colSpan={emptyCellSpan}></td>
                                <td className={`px-3 py-2 text-right text-xs font-bold ${tradeNetPnlPoints >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                                  {tradeNetPnlPoints >= 0 ? '+' : ''}{tradeNetPnlPoints.toFixed(2)}
                                </td>
                                <td className={`px-3 py-2 text-right text-xs font-bold ${tradePctPnl >= 0 ? 'text-green-700' : 'text-red-700'}`}>
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
                  className="px-2 py-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  ≪
                </button>
                <button 
                  onClick={() => setCurrentPage(p => Math.max(1, p - 1))} 
                  disabled={currentPage === 1}
                  className="px-2 py-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-30 disabled:cursor-not-allowed"
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
                          ? 'bg-blue-600 text-white' 
                          : 'bg-white text-gray-700 hover:bg-gray-100 border border-gray-300'
                      }`}
                    >
                      {pageNum}
                    </button>
                  );
                })}
                <button 
                  onClick={() => setCurrentPage(p => Math.min(Math.ceil(groupedTrades.length / itemsPerPage), p + 1))} 
                  disabled={currentPage === Math.ceil(groupedTrades.length / itemsPerPage)}
                  className="px-2 py-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  ›
                </button>
                <button 
                  onClick={() => setCurrentPage(Math.ceil(groupedTrades.length / itemsPerPage))} 
                  disabled={currentPage === Math.ceil(groupedTrades.length / itemsPerPage)}
                  className="px-2 py-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-30 disabled:cursor-not-allowed"
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
