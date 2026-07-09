import React, { useMemo, useState, useEffect } from 'react';
import {
  AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine
} from 'recharts';
import { Download, X } from 'lucide-react';
import ExcelJS from 'exceljs';
import { writeWowMomSheet, buildWowMomTitle } from '../utils/wowMomSheet';

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

const buildExcelFileName = (config) => {
  if (!config) return `backtest.xlsx`;

  const parts = [config.instrument || 'backtest'];

  const _stratIdx = String(config.instrument || '').toUpperCase();
  (config.legs || []).forEach(leg => {
    // Multi-index: tag a cross-index leg with its own index so the export name
    // reflects it (e.g. ...+MIDCPNIFTY-BUY-FUT-MONTHLY). Same-index legs unchanged.
    const _legIdx = String(leg.index || _stratIdx).toUpperCase();
    if (leg.segment !== 'midcap100' && _legIdx && _legIdx !== _stratIdx) {
      parts.push(_legIdx);
    }
    if (leg.segment === 'midcap100') {
      // Cross-index Midcap leg — name it by position + symbol + pricing mode,
      // e.g. BUY_MIDCAP100_Hypothetical_Future  /  SELL_MIDCAP100_Spot.
      parts.push((leg.position || 'buy').toUpperCase());
      const sym = (leg.symbol || 'NIFTYMIDCAP100').toUpperCase().replace('NIFTY', '');
      parts.push(sym || 'MIDCAP100');
      const mode = (leg.midcap_mode || 'hypothetical').toLowerCase();
      parts.push(mode === 'hypothetical' ? 'Hypothetical_Future' : 'Spot');
      return;
    }
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
        const pctVal = leg.pct_value != null ? parseFloat(leg.pct_value) : 0;
        const moneyness = (leg.pct_atm_moneyness || 'OTM').toUpperCase();
        const pctStr = Number.isInteger(pctVal) ? String(pctVal) : parseFloat(pctVal.toFixed(2)).toString();
        parts.push(`ATM_${pctStr}PCT_${moneyness}`);
      } else if (criteria === 'atm_straddle_prem_pct') {
        parts.push('STRADDLE');
      } else if (criteria === 'straddle_width') {
        const mult = leg.straddle_multiplier != null ? parseFloat(leg.straddle_multiplier) : 0.5;
        const multStr = Number.isInteger(mult) ? String(mult) : parseFloat(mult.toFixed(2)).toString();
        // Raw +/- sign, applied identically to every leg (no CE/PE meaning).
        const sign = (leg.straddle_direction || '+').trim() === '-' ? '-' : '+';
        parts.push(`SW_${multStr}X_${sign}`);
      } else if (criteria === 'rel_leg') {
        // Relative-to-Leg (Iron Condor wing): reflect parent + offset in gaps,
        // e.g. REL_L1_2G  /  REL_L3_-1G — instead of a misleading "ATM".
        const ref = Number(leg.ref_leg) || 1;
        const off = Number(leg.offset) || 0;
        parts.push(`REL_L${ref}_${off}G`);
      } else {
        parts.push((leg.strike_type || 'atm').toUpperCase());
      }
      const exp = (leg.expiry || '').toUpperCase().replace('_', '');
      if (exp) parts.push(exp);
    }
    const fmtVal = v => Number.isInteger(v) ? String(v) : parseFloat(Number(v).toFixed(2)).toString();
    if (leg.sl_buffer_enabled && leg.sl_buffer_value > 0 && leg.sl_buffer_pct > 0) {
      const modeSuffix = (leg.sl_buffer_mode || 'POINTS').includes('PERCENT') ? '%' : 'PTS';
      parts.push(`SL_${fmtVal(leg.sl_buffer_value)}${modeSuffix}_Buffer_${fmtVal(leg.sl_buffer_pct)}%`);
    } else if (leg.stop_loss_enabled && leg.stop_loss_value > 0) {
      const modeSuffix = (leg.stop_loss_mode || 'POINTS').includes('PERCENT') ? '%' : 'PTS';
      parts.push(`SL_${fmtVal(leg.stop_loss_value)}${modeSuffix}`);
    }
  });

  const entry = config.entryDaysBefore != null ? `T${config.entryDaysBefore}` : null;
  const exit  = config.exitDaysBefore  != null ? `T${config.exitDaysBefore}`  : null;
  if (entry) parts.push(entry);
  if (exit)  parts.push(exit);

  if (config.spotAdjustmentEnabled) {
    const dir = (config.spotAdjustmentDirection || 'rise');
    const val = config.spotAdjustmentValue;
    const unit = (config.spotAdjustmentUnits || 'percent') === 'percent' ? 'PCT' : 'PTS';
    const valStr = val != null
      ? (Number.isInteger(val) ? String(val) : parseFloat(val.toFixed(2)).toString())
      : '';
    const saDir = dir === 'both' ? 'BOTH' : dir === 'fall' ? 'FALL' : 'RISE';
    parts.push(`Spot_Adjustment_${saDir}${valStr ? `_${valStr}${unit}` : ''}`);
  }

  // Midcap cross-index spot adjustment (only when enabled).
  if (config.midcapSpotAdjustmentEnabled) {
    const dir = (config.midcapSpotAdjustmentDirection || 'rise');
    const val = config.midcapSpotAdjustmentValue;
    const unit = (config.midcapSpotAdjustmentUnits || 'percent') === 'percent' ? 'PCT' : 'PTS';
    const valStr = val != null
      ? (Number.isInteger(val) ? String(val) : parseFloat(Number(val).toFixed(2)).toString())
      : '';
    const saDir = dir === 'both' ? 'BOTH' : dir === 'fall' ? 'FALL' : 'RISE';
    parts.push(`Midcap_Spot_Adjustment_${saDir}${valStr ? `_${valStr}${unit}` : ''}`);
  }

  return parts.join('_') + '.xlsx';
};


const ResultsPanel = ({ results, onClose, showCloseButton = true, filterInfo, showStrSegment = false, strategyConfig, filterSegments = null }) => {
  if (!results) return null;

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
  const [showDownloadMenu, setShowDownloadMenu] = useState(false);
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

    // Parse dd-mm-yyyy or dd/mm/yyyy into a comparable timestamp.
    const parseDateMs = (dateStr) => {
      if (!dateStr) return 0;
      const parts = dateStr.split(/[-\/]/);
      if (parts.length === 3) {
        const [d, m, y] = parts;
        return new Date(parseInt(y, 10), parseInt(m, 10) - 1, parseInt(d, 10)).getTime();
      }
      return new Date(dateStr).getTime() || 0;
    };

    result.sort((a, b) => {
      const tA = parseDateMs(a.entryDate);
      const tB = parseDateMs(b.entryDate);
      if (tA !== tB) return tA - tB;
      // Tie-break by trade number so legs of same-day trades stay stable.
      return a.tradeNumber - b.tradeNumber;
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
        date: group.entryDate || group.exitDate || `Trade ${index + 1}`,
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
        date: group.entryDate || group.exitDate || `Trade ${index + 1}`,
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

  // Yearly x-axis ticks: pick the first data point in each calendar year.
  // Used by both equity and drawdown charts so tick positions are consistent.
  const yearlyTicks = useMemo(() => {
    if (!equityData.length) return [];
    const getYear = (dateStr) => {
      if (!dateStr) return null;
      const parts = dateStr.split(/[-\/]/);
      if (parts.length === 3) return parseInt(parts[2], 10);
      return null;
    };
    const seen = new Set();
    const ticks = [];
    equityData.forEach(d => {
      const yr = getYear(d.date);
      if (yr && !seen.has(yr)) { seen.add(yr); ticks.push(d.date); }
    });
    return ticks;
  }, [equityData]);

  // Calculate stats
  const stats = useMemo(() => {
    // Total P&L = arithmetic SUM of each trade's % P&L, to match the tradesheet
    // "Overall Profit" (Excel _sumPctJS = Σ Net P&L / Entry Spot per trade).
    // Previously this was the compounded final-cumulative NAV (finalCumulative - 100),
    // which differed from the tradesheet for any mix of wins/losses.
    const totalPnLPct = groupedTrades.reduce((sum, g) => {
      const net = Number(g.totalPnl);
      return sum + (g.entrySpot > 1000 && Number.isFinite(net) ? (net / g.entrySpot) * 100 : 0);
    }, 0);
    const totalTrades = groupedTrades.length;

    const out = {
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

    // ── Midcap: the backend `summary` is NIFTY/base-only, but the tradesheet
    //    aggregates COMBINED (base + Midcap overlay). Recompute the displayed
    //    stats on the combined per-trade data so the UI matches the tradesheet
    //    (default Overall export). Mirrors the Excel formulas exactly. UI-only.
    if (results?.midcap?.available ||
        (results?.midcap?.byTrade && Object.keys(results.midcap.byTrade).length > 0)) {
      const byTrade = results.midcap.byTrade || {};
      const mcs = results.midcap.summary || {};
      const parseD = (s) => {
        if (typeof s !== 'string' || !s) return null;
        const p = s.includes('/') ? s.split('/') : s.split('-');
        if (p.length !== 3) return null;
        let y, m, d;
        if (p[0].length === 4) { y = +p[0]; m = +p[1] - 1; d = +p[2]; }
        else { d = +p[0]; m = +p[1] - 1; y = +p[2]; }
        const t = Date.UTC(y, m, d);
        return Number.isFinite(t) ? t : null;
      };
      let sumPos = 0, sumNeg = 0, winCnt = 0, lossCnt = 0, cnt = 0;
      let maxNet = -Infinity, minNet = Infinity;
      let winRun = 0, lossRun = 0, maxWinStk = 0, maxLossStk = 0;
      // Combined equity NAV is COMPOUNDED from the per-trade combined % (the tradesheet
      // computes it the same way: nav *= 1 + cpct/100). It is NOT a field on byTrade,
      // so we build it here to drive Max DD and CAGR.
      let nav = 100, peak = 100, worstDD = 0, finalCum = 100;
      let peakMs = null, worstPeakMs = null, worstTroughMs = null;
      let minEntry = null, maxExit = null;
      for (const g of groupedTrades) {
        const mc = byTrade[g.groupKey];
        if (!mc) continue;
        const pct = Number(mc['Combined Net P&L %']);
        const net = Number(mc['Combined Net P&L']);
        const eD = parseD(g.entryDate); const xD = parseD(g.exitDate);
        if (Number.isFinite(pct)) {
          cnt++;
          if (pct > 0) { sumPos += pct; winCnt++; winRun++; lossRun = 0; if (winRun > maxWinStk) maxWinStk = winRun; }
          else if (pct < 0) { sumNeg += pct; lossCnt++; lossRun++; winRun = 0; if (lossRun > maxLossStk) maxLossStk = lossRun; }
          nav = nav * (1 + pct / 100);
          finalCum = nav;
          if (nav >= peak) { peak = nav; peakMs = xD; }
          else {
            const dd = peak !== 0 ? (nav / peak - 1) * 100 : 0;
            if (dd < worstDD) { worstDD = dd; worstPeakMs = peakMs; worstTroughMs = xD; }
          }
        }
        if (Number.isFinite(net)) { if (net > maxNet) maxNet = net; if (net < minNet) minNet = net; }
        if (eD != null && (minEntry == null || eD < minEntry)) minEntry = eD;
        if (xD != null && (maxExit == null || xD > maxExit)) maxExit = xD;
      }
      const mddDuration = (worstPeakMs != null && worstTroughMs != null)
        ? Math.round((worstTroughMs - worstPeakMs) / 86400000) : 0;
      if (!Number.isFinite(maxNet)) maxNet = 0;
      if (!Number.isFinite(minNet)) minNet = 0;
      const winRate = cnt > 0 ? (winCnt / cnt) * 100 : 0;
      const avgWin = winCnt > 0 ? sumPos / winCnt : 0;
      const avgLoss = lossCnt > 0 ? sumNeg / lossCnt : 0;
      const years = (minEntry != null && maxExit != null) ? (maxExit - minEntry) / (365.25 * 86400000) : 0;
      const cagr = years > 0 && finalCum > 0 ? (Math.pow(finalCum / 100, 1 / years) - 1) * 100 : 0;
      const combinedTotal = Number(mcs.combined_pnl_pct_sum);

      out.totalPnLPct = Number.isFinite(combinedTotal) ? combinedTotal : (sumPos + sumNeg);
      out.winRate = winRate;
      out.lossPct = cnt > 0 ? (lossCnt / cnt) * 100 : 0;
      out.cagr = cagr;
      out.maxDDPct = worstDD;
      out.avgWinPct = avgWin;
      out.avgLossPct = avgLoss;
      out.maxWin = maxNet;
      out.maxLoss = minNet;
      out.expectancy = avgLoss !== 0
        ? ((winRate / 100) * avgWin / Math.abs(avgLoss) - (1 - winRate / 100))
        : 0;
      out.maxWinStreak = maxWinStk;
      out.maxLossStreak = maxLossStk;
      out.mddDuration = mddDuration;
    } else {
      // No Midcap: override Max DD with min %DD on the NIFTY equity (compound the
      // per-trade %), so the on-screen Max DD == the tradesheet %DD column (overall),
      // identical to the download/optim. Mirrors algotest_job's pct = Net P&L/Entry Spot.
      const _pd = (s) => {
        if (typeof s !== 'string' || !s) return null;
        const p = s.includes('/') ? s.split('/') : s.split('-');
        if (p.length !== 3) return null;
        let y, m, d;
        if (p[0].length === 4) { y = +p[0]; m = +p[1] - 1; d = +p[2]; }
        else { d = +p[0]; m = +p[1] - 1; y = +p[2]; }
        const t = Date.UTC(y, m, d);
        return Number.isFinite(t) ? t : null;
      };
      let nav = 100, peak = 100, worstDD = 0, peakMs = null, wPeak = null, wTrough = null;
      for (const g of groupedTrades) {
        const net = Number(g.totalPnl); const es = Number(g.entrySpot);
        const pct = (es > 1000 && Number.isFinite(net)) ? (net / es) * 100 : NaN;
        const xD = _pd(g.exitDate);
        if (Number.isFinite(pct)) {
          nav = nav * (1 + pct / 100);
          if (nav >= peak) { peak = nav; peakMs = xD; }
          else { const dd = (nav / peak - 1) * 100; if (dd < worstDD) { worstDD = dd; wPeak = peakMs; wTrough = xD; } }
        }
      }
      out.maxDDPct = worstDD;
      const _fmtMs = (ms) => { const d = new Date(ms); return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`; };
      if (wPeak != null && wTrough != null) {
        out.mddDuration = Math.round((wTrough - wPeak) / 86400000);
        out.mddStartDate = _fmtMs(wPeak); out.mddEndDate = _fmtMs(wTrough);
      }
    }

    return out;
  }, [summary, groupedTrades, results]);


  // Export Excel — Sheet 1: Trades, Sheet 2: Formatted Summary
  const exportToCSV = async (patchwise = false) => {
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
    // Midcap cross-index overlay (additive — present only when a Midcap leg ran).
    const midcapByTrade = results?.midcap?.byTrade || {};
    const hasMidcap = Boolean(results?.midcap?.available);
    // Midcap Trade-Sheet block (present only when a Midcap leg ran). With a Midcap
    // leg the NIFTY trade-level columns (Net MAE 1/2/Final, Net P&L, % P&L,
    // Cumulative, Peak, DD, %DD, Lowest NAV, Actual Live DD) are dropped from the
    // sheet — see keyOrder — and the COMBINED versions below take their place. The
    // leg P&L columns are labelled "Midcap Hypo P&L" / "Midcap Hypo P&L %".
    const MIDCAP_COLS = [
      'Midcap Entry Spot', 'Midcap Exit Spot', 'Midcap Spot P&L', 'Midcap Spot P&L %',
      'Midcap No Of Days', 'Midcap Rollover Cost %', 'Midcap Hypo P&L', 'Midcap Hypo P&L %',
      'Midcap MAE', 'Midcap MFE',
      'Combined Net P&L', 'Combined Net P&L %', 'Combined Cumulative', 'Combined Peak',
      'Combined DD', 'Combined %DD', 'Combined Net MAE 1', 'Combined Net MAE 2',
      'Combined Final MAE', 'Combined Lowest NAV', 'Combined Actual Live DD',
    ];
    const hasReEntry = sourceTrades.some(t => (
      Boolean(t['ReEntryIndex'] || t['ReEntryTrigger'] || t['ReEntryMode']) || isLazyLegRow(t)
    ));
    const hasStrikeShift = sourceTrades.some(t => Boolean(t['Strike Shift Reason']));

    const getReEntryType = (trade) => {
      if (isLazyLegRow(trade)) return 'Lazy';
      const mode = String(trade?.['ReEntryMode'] || '').trim();
      if (mode) return mode;
      const trigger = String(trade?.['ReEntryTrigger'] || '').trim();
      if (trigger) return trigger;
      return trade?.['ReEntryIndex'] ? 'Re-Entry' : '';
    };

    // Primary sort: Entry Date so spot-adj re-entries (which carry NEW higher
    // trade IDs but earlier entry dates than later originals) appear in
    // chronological position instead of being dumped at the bottom.
    const parseTradeDate = (raw) => {
      if (!raw) return Infinity;
      if (raw instanceof Date) return raw.getTime();
      const s = String(raw).trim();
      const m1 = s.match(/^(\d{1,2})[-/](\d{1,2})[-/](\d{4})/);
      if (m1) { const [, d, mo, y] = m1; return Date.UTC(+y, +mo - 1, +d); }
      const m2 = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
      if (m2) { const [, y, mo, d] = m2; return Date.UTC(+y, +mo - 1, +d); }
      const t = Date.parse(s);
      return Number.isNaN(t) ? Infinity : t;
    };
    const sortedTrades = [...sourceTrades].sort((a, b) => {
      const dA = parseTradeDate(a['Entry Date'] || a.entry_date);
      const dB = parseTradeDate(b['Entry Date'] || b.entry_date);
      if (dA !== dB) return dA - dB;
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

    // Patchwise reset boundaries — shared by the per-trade combined chain, the Max DD
    // scan, and the outlier Live DD scan so they all reset at the SAME points. Prefer
    // the uploaded filter's segment START dates (reset when a trade's entry crosses
    // into a new segment) so spot-adjustment runs reset too (they never emit a
    // FILTER_END exit reason). Falls back to FILTER_END when no segments are passed.
    const _pwSegSrc = (Array.isArray(results?.meta?.filter_segments) && results.meta.filter_segments.length)
      ? results.meta.filter_segments
      : (Array.isArray(filterSegments) ? filterSegments : []);
    const _pwSegStarts = _pwSegSrc
      .map(s => parseTradeDate(s && (s.start || s.Start || s.from || s.start_date || s.startdt)))
      .filter(ms => Number.isFinite(ms)).sort((a, b) => a - b);
    const _pwSegIdxByKey = (key) => {
      const lg = groupedByTrade[key] || [];
      const mr = lg.find(l => !l['ReEntryIndex'] && !l['ReEntryTrigger'] && !l['ReEntryMode'] && !isLazyLegRow(l)) || lg[0] || {};
      const em = parseTradeDate(mr['Entry Date']); let i = -1;
      for (let j = 0; j < _pwSegStarts.length; j++) { if (_pwSegStarts[j] <= em) i = j; else break; }
      return i;
    };

    const toNumber = (value) => {
      if (typeof value === 'number') return Number.isFinite(value) ? value : null;
      if (value == null || value === '') return null;
      const parsed = parseFloat(String(value).replace(/[,%₹\s]/g, ''));
      return Number.isFinite(parsed) ? parsed : null;
    };
    const pctOfBase = (pnlValue, baseValue) => {
      const pnl = toNumber(pnlValue);
      const base = toNumber(baseValue);
      if (pnl == null || base == null || base === 0) return '';
      return pnl / base;
    };
    const parseExportDateMs = (value) => {
      if (value instanceof Date) return value.getTime();
      if (value == null || value === '') return null;
      const str = String(value).trim();
      const parts = str.includes('/') ? str.split('/') : str.split('-');
      if (parts.length !== 3) return null;
      let year, month, day;
      if (parts[0].length === 4) {
        year = parseInt(parts[0], 10);
        month = parseInt(parts[1], 10) - 1;
        day = parseInt(parts[2], 10);
      } else {
        day = parseInt(parts[0], 10);
        month = parseInt(parts[1], 10) - 1;
        year = parseInt(parts[2], 10);
      }
      const ms = Date.UTC(year, month, day);
      return Number.isFinite(ms) ? ms : null;
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

    // Combined Net MAE (NIFTY legs + Midcap leg) — reproduces the reference
    // workbook EXACTLY (cols BE/BF/BG), present ONLY when a Midcap leg ran:
    //   NIFTY MAE/MFE per trade = Σ over option/future legs of the leg's MAE/MFE.
    //     The engine already stores MAE/MFE as PERCENT OF SPOT (= (entry-high)/spot*100,
    //     same scale as % P&L), so they are summed DIRECTLY — do NOT divide by spot again.
    //   Midcap MAE/MFE          = mc['Midcap MAE'|'Midcap MFE']  (also % , of f_entry)
    //   Net MAE 1 = Midcap MFE + NIFTY MAE
    //   Net MAE 2 = Midcap MAE + NIFTY MFE
    //   Final MAE = min(Net MAE 1, Net MAE 2)
    // mc = the per-trade midcap fields. Returns null if NIFTY MAE/MFE incomplete.
    const calcCombinedFinalMaePct = (legs, mc) => {
      let niftyMae = 0, niftyMfe = 0;
      const dirLegs = (legs || []).filter(r => isOptionRow(r) || isFutureRow(r));
      for (const r of dirLegs) {
        const mae = toNumber(r['MAE']);
        const mfe = toNumber(r['MFE']);
        if (mae == null || mfe == null) return null; // incomplete → skip
        niftyMae += mae;   // already % of spot
        niftyMfe += mfe;
      }
      const midMae = mc ? (toNumber(mc['Midcap MAE']) || 0) : 0;
      const midMfe = mc ? (toNumber(mc['Midcap MFE']) || 0) : 0;
      const netMae1 = midMfe + niftyMae;
      const netMae2 = midMae + niftyMfe;
      return {
        netMae1: roundMae(netMae1),
        netMae2: roundMae(netMae2),
        finalMae: roundMae(Math.min(netMae1, netMae2)),
      };
    };

    // Every leg (option or future) is classified by market direction:
    //   Bullish (CE BUY / PE SELL / FUT BUY)  — adverse when market falls, favorable when rises.
    //   Bearish (CE SELL / PE BUY / FUT SELL) — adverse when market rises, favorable when falls.
    // Unified rule (single-leg, multi-leg, options and futures alike):
    //   Net MAE 1 = sum(bullish MAE) + sum(bearish MFE)
    //   Net MAE 2 = sum(bullish MFE) + sum(bearish MAE)
    //   Final MAE = min(Net MAE 1, Net MAE 2)                  (single directional leg)
    //   Final MAE = min(Net MAE 1, Net MAE 2, Net P&L %)       (>1 directional leg)
    // For MULTI-leg trades the realized Net P&L % floors Final MAE so the
    // reconstructed combined excursion can never read better than what the trade
    // actually booked (single-leg keeps min(nm1, nm2) — its MAE already bounds it).
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
      return {
        netMae1: roundMae(netMae1),
        netMae2: roundMae(netMae2),
        finalMae: roundMae(finalMae),
      };
    };

    const hasTradeMae = Object.values(groupedByTrade).some(legs => calcTradeMae(legs));

    const TRADE_COLS = new Set([
      'Net MAE 1','Net MAE 2','Final MAE',
      'Net P&L','% P&L','Cumulative','Peak','DD','%DD',
      'Lowest NAV','Actual Live DD',
      ...(hasMidcap ? MIDCAP_COLS : []),
    ]);
    const keyOrder = [
      'Trade','Leg','Index','Entry Date','Exit Date','Expiry',
      'Entry Spot','Exit Spot','Spot P&L','Spot P&L %',
      'Type','Strike',
      ...(hasBuffer ? ['buffer_ref_price', 'buffer_strike_offset'] : []),
      'B/S',
      ...(hasReEntry ? ['Re-Entry Type'] : []),
      'Qty',
      ...(hasSpotAdj ? ['Raw Entry Price'] : []),
      'Entry Price',
      ...(hasSpotAdj ? ['Raw Exit Price'] : []),
      'Exit Price','MAE','MFE',
      // NIFTY trade-level Net MAE — dropped from the Midcap sheet (Combined
      // versions replace them); unchanged for non-Midcap exports.
      ...(hasMidcap ? [] : ['Net MAE 1','Net MAE 2','Final MAE']),
      ...(hasCalls   ? ['CE P&L', 'CE P&L %']  : []),
      ...(hasPuts    ? ['PE P&L', 'PE P&L %']  : []),
      ...(hasFutures ? ['FUT P&L'] : []),
      // NIFTY trade-level P&L / NAV / drawdown — dropped from the Midcap sheet
      // (Combined versions in MIDCAP_COLS replace them); unchanged otherwise.
      ...(hasMidcap ? [] : ['Net P&L','% P&L','Cumulative','Peak','DD','%DD','Lowest NAV','Actual Live DD']),
      ...(hasMidcap ? MIDCAP_COLS : []),
      'Exit Reason',
      ...(hasStrikeShift ? ['Strike Shift Reason'] : []),
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
      const pct  = spot>0?(net/spot)*100:0;
      const tradeMae = calcTradeMae(legs, pct);
      // Net MAE 1/2/Final columns stay NIFTY-only (existing behaviour). With a
      // Midcap leg these columns are dropped from the sheet; the COMBINED cross
      // is shown in the Combined Net MAE columns instead (computed in the chain).
      tm[k] = { net, pct,
                netMae1:tradeMae?.netMae1 ?? '', netMae2:tradeMae?.netMae2 ?? '', finalMae:tradeMae?.finalMae ?? '',
                cumulative:toN(r['Cumulative']), peak:toN(r['Peak']),
                dd:toN(r['DD']), pctDd: (() => { const v = toN(r['%DD']); return v !== '' ? v : ''; })(),
                midcap: hasMidcap ? (midcapByTrade[k] || null) : null,
                exitReason: mainRow?.['Exit Reason'] || '' };
    });

    // NIFTY-only patchwise mode: recompute cumulative/peak/DD with patch resets so
    // the Trade Sheet equity matches the Patch wise tab (mirrors what Midcap does via
    // the Combined chain). Only runs when patchwise=true and no Midcap leg.
    if (patchwise && !hasMidcap) {
      const _seen = new Set(); const _pwKeys = [];
      sortedTrades.forEach(_tr => {
        const _k = String(_tr.Trade||_tr.trade||1);
        if (!_seen.has(_k)) { _seen.add(_k); if (tm[_k]) _pwKeys.push(_k); }
      });
      let nav = 100, peak = 100;
      _pwKeys.forEach((k, idx) => {
        if (idx > 0) {
          const prevKey = _pwKeys[idx - 1];
          const newPatch = _pwSegStarts.length
            ? (_pwSegIdxByKey(k) !== _pwSegIdxByKey(prevKey))
            : ((tm[prevKey].exitReason || '').toUpperCase().split('+').includes('FILTER_END'));
          if (newPatch) { nav = 100; peak = 100; }
        }
        const pct = tm[k].pct;
        if (Number.isFinite(pct)) {
          nav = nav * (1 + pct / 100);
          peak = Math.max(peak, nav);
          tm[k].cumulative = Number(nav.toFixed(4));
          tm[k].peak       = Number(peak.toFixed(4));
          tm[k].dd         = Number((nav - peak).toFixed(4));
          tm[k].pctDd      = peak !== 0 ? Number(((nav / peak - 1) * 100).toFixed(4)) : '';
        }
      });
    }

    // Combined NAV / Peak / DD / Net MAE / Lowest NAV chain (from Combined Net
    // P&L %) — only when a Midcap leg ran. These become the Combined Trade-Sheet
    // columns AND feed the Summary's combined Performance Overview / Risk Metrics.
    if (hasMidcap) {
      const _seen = new Set();
      const orderKeys = [];
      sortedTrades.forEach(_tr => {
        const _k = String(_tr.Trade || _tr.trade || 1);
        if (!_seen.has(_k)) { _seen.add(_k); if (tm[_k]) orderKeys.push(_k); }
      });
      let nav = 100, peak = 100, prevNav = 100, prevPeak = 100, firstDone = false;
      orderKeys.forEach((k, idx) => {
        // Patchwise mode: reset the equity chain to 100 at the start of each new patch.
        if (patchwise && idx > 0) {
          const prevKey = orderKeys[idx - 1];
          const newPatch = _pwSegStarts.length
            ? (_pwSegIdxByKey(k) !== _pwSegIdxByKey(prevKey))
            : ((tm[prevKey].exitReason || '').toUpperCase().split('+').includes('FILTER_END'));
          if (newPatch) { nav = 100; peak = 100; prevNav = 100; prevPeak = 100; }
        }
        const mc = tm[k].midcap;
        const cpct = mc ? Number(mc['Combined Net P&L %']) : NaN;
        if (Number.isFinite(cpct)) {
          prevNav = nav;
          prevPeak = peak;
          nav = nav * (1 + cpct / 100);
          peak = Math.max(peak, nav);
          tm[k].combinedCum = Number(nav.toFixed(4));
          tm[k].combinedPeak = Number(peak.toFixed(4));
          tm[k].combinedDd = Number((nav - peak).toFixed(4));
          tm[k].combinedPctDd = peak !== 0 ? Number(((nav / peak - 1) * 100).toFixed(4)) : '';
          // Combined Net MAE 1/2 (Midcap + NIFTY legs) → Final MAE → Lowest NAV
          // → Actual Live DD, driven by the COMBINED NAV.
          // Combined Final MAE = min(Net MAE 1, Net MAE 2, Combined Net P&L %):
          // the realized combined loss is included as a floor so a trade that
          // closed worse than its intra-trade excursion is captured (Midcap only).
          const cm = calcCombinedFinalMaePct(groupedByTrade[k], mc);
          const fmae = cm ? Math.min(cm.netMae1, cm.netMae2, cpct) : null;
          tm[k].combinedNetMae1 = cm ? cm.netMae1 : '';
          tm[k].combinedNetMae2 = cm ? cm.netMae2 : '';
          tm[k].combinedFinalMae = (fmae == null) ? '' : Number(fmae.toFixed(4));
          if (fmae != null) {
            // Revised rule: every trade (incl. first, prevNav = 100) anchors the
            // low to prevNav * (1 + FinalMAE%) — AW = AU_prev * (1 + AM%).
            const lowestNav = prevNav * (1 + fmae / 100);
            tm[k].combinedLowestNav = Number(lowestNav.toFixed(4));
            // Live DD divides by the PREVIOUS trade's peak (AV_prev), not this
            // trade's peak — AX = AW / AV_prev - 1.
            tm[k].combinedActualLiveDd = prevPeak !== 0 ? Number(((lowestNav / prevPeak - 1) * 100).toFixed(4)) : '';
          } else {
            tm[k].combinedLowestNav = ''; tm[k].combinedActualLiveDd = '';
          }
          firstDone = true;
        } else {
          tm[k].combinedCum = ''; tm[k].combinedPeak = ''; tm[k].combinedDd = ''; tm[k].combinedPctDd = '';
          tm[k].combinedNetMae1 = ''; tm[k].combinedNetMae2 = '';
          tm[k].combinedFinalMae = ''; tm[k].combinedLowestNav = ''; tm[k].combinedActualLiveDd = '';
        }
      });
    }

    // Lowest NAV and Actual Live DD
    // Excel formula: AS2=AN2 (first trade), AS_n=AN_(n-1)*(1+AR_n%) for all subsequent trades
    // Walk in CHRONOLOGICAL (Entry Date) order, not by raw Trade ID — cascade
    // re-entry mini-trades carry high engine IDs but appear chronologically
    // between earlier originals, so sorting by parseInt(Trade ID) breaks the chain.
    {
      const _seenTm = new Set();
      const sortedTmKeys = [];
      sortedTrades.forEach(_tr => {
        const _k = String(_tr.Trade || _tr.trade || 1);
        if (!_seenTm.has(_k)) {
          _seenTm.add(_k);
          if (tm[_k]) sortedTmKeys.push(_k);
        }
      });
      let prevCum = 100;
      let prevPeak = 100;
      let firstTradeDone = false;
      let _prevTmKey = null;
      sortedTmKeys.forEach(k => {
        // For NIFTY-only patchwise: reset prevCum/prevPeak at patch boundaries so
        // LowestNAV and Actual Live DD anchor correctly to each patch's start (100).
        if (patchwise && !hasMidcap && _prevTmKey !== null) {
          const newPatch = _pwSegStarts.length
            ? (_pwSegIdxByKey(k) !== _pwSegIdxByKey(_prevTmKey))
            : ((tm[_prevTmKey].exitReason || '').toUpperCase().split('+').includes('FILTER_END'));
          if (newPatch) { prevCum = 100; prevPeak = 100; }
        }
        _prevTmKey = k;
        const t    = tm[k];
        const mae  = (t.finalMae  !== '' && t.finalMae  != null) ? t.finalMae  : null;
        const peak = (t.peak      !== '' && t.peak      != null) ? t.peak      : null;
        const cum  = (t.cumulative !== '' && t.cumulative != null) ? t.cumulative : null;
        if (mae != null && peak != null && prevPeak !== 0) {
          // Research-team formula (revised): EVERY trade — including the first,
          // where prevCum = 100 — anchors the low to prev_cumulative * (1 + Final
          // MAE_N / 100):  AW = AU_prev * (1 + AM%). Live DD divides by the
          // PREVIOUS trade's peak (AV_prev), not this trade's peak.
          // Store full precision; cell numFmt '#,##0.00' renders 2 decimals.
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
    }

    // engine_tid → sequential display number based on first chronological
    // appearance. Spot-adj re-entries get higher engine IDs (380+) but should
    // show the user-friendly position they occupy in the sorted tradesheet.
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
          // Renamed Midcap leg P&L columns (backend still keys them "Midcap Leg P&L").
          else if (key==='Midcap Hypo P&L')   val = m.midcap ? (m.midcap['Midcap Leg P&L']   ?? '') : '';
          else if (key==='Midcap Hypo P&L %') val = m.midcap ? (m.midcap['Midcap Leg P&L %'] ?? '') : '';
          // Combined NAV/DD/Net MAE chain (frontend-computed).
          else if (key==='Combined Cumulative') val=m.combinedCum;
          else if (key==='Combined Peak') val=m.combinedPeak;
          else if (key==='Combined DD') val=m.combinedDd;
          else if (key==='Combined %DD') val=m.combinedPctDd;
          else if (key==='Combined Net MAE 1') val=m.combinedNetMae1;
          else if (key==='Combined Net MAE 2') val=m.combinedNetMae2;
          else if (key==='Combined Final MAE') val=m.combinedFinalMae;
          else if (key==='Combined Lowest NAV') val=m.combinedLowestNav;
          else if (key==='Combined Actual Live DD') val=m.combinedActualLiveDd;
          // Remaining Midcap + Combined Net P&L columns come straight from the backend.
          else if (MIDCAP_COLS.includes(key)) val = m.midcap ? (m.midcap[key] ?? '') : '';
        } else if (key==='Leg' && isLazyLegRow(trade)) val=trade['Lazy Leg Name'] || trade[key];
        else if (key==='Re-Entry Type') val=getReEntryType(trade);
        else if (key==='Index') val=_tidToIndexNo[k] ?? parseInt(trade.Trade||trade.trade||1,10);
        else if (key==='Trade') val=_tidToIndexNo[k] ?? parseInt(trade.Trade||trade.trade||1,10);
        else if (key==='Exit Date') val=getVisibleExitDate(trade);
        else if (key==='Spot P&L %') {
          // Spot P&L is a trade-level quantity written only on Leg 1 rows.
          // Leave Spot P&L % blank on Leg 2+ (matches Net P&L convention) so
          // column sums give the trade-level total without double-counting.
          const spotPnl = trade['Spot P&L'];
          val = (toNumber(spotPnl) == null) ? '' : pctOfBase(spotPnl, trade['Entry Spot']);
        }
        else if (key==='CE P&L %') val=pctOfBase(trade['CE P&L'], trade['Entry Spot']);
        else if (key==='PE P&L %') val=pctOfBase(trade['PE P&L'], trade['Entry Spot']);
        else if (key==='Expiry') val=formatDateToDdMmYyyy(
          trade['Future Expiry'] || trade['futures_expiry'] || trade['Expiry']
        );
        else val=trade[key];
        if (val==null||(typeof val==='number'&&isNaN(val))||val==='NaN') val='';
        row[key]=val;
      }
      // With a Midcap leg the NIFTY trade-level Net P&L / % P&L / Cumulative are
      // dropped from the sheet, but the Summary still needs them (e.g. ROI-vs-Spot
      // gating). Attach as hidden first-row props — ExcelJS writes only keyOrder
      // columns, so these never appear in the Trade Sheet.
      if (hasMidcap && first) {
        if (row['Net P&L'] === undefined)    row['Net P&L']    = m.net ?? '';
        if (row['% P&L'] === undefined)      row['% P&L']      = m.pct ?? '';
        if (row['Cumulative'] === undefined) row['Cumulative'] = m.cumulative ?? '';
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
      'Spot P&L %':10,'CE P&L %':10,'PE P&L %':10,'Exit Reason':14,'Strike Shift Reason':40,'Expiry':12,'STR Segment':14,'Filter Segment':18,
      'Midcap Entry Spot':15,'Midcap Exit Spot':15,'Midcap Spot P&L':14,'Midcap Spot P&L %':15,'Midcap No Of Days':15,'Midcap Rollover Cost %':18,'Midcap Hypo P&L':15,'Midcap Hypo P&L %':16,'Midcap MAE':12,'Midcap MFE':12,
      'Combined Net P&L':15,'Combined Net P&L %':16,'Combined Cumulative':17,'Combined Peak':13,'Combined DD':12,'Combined %DD':12,'Combined Net MAE 1':16,'Combined Net MAE 2':16,'Combined Final MAE':15,'Combined Lowest NAV':16,'Combined Actual Live DD':18 };
    const truePctCols = new Set(['Spot P&L %', 'CE P&L %', 'PE P&L %']);
    const pctAppendCols = new Set(['%DD', 'Combined %DD']);
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
          const _maeCol = ['MAE','MFE','Net MAE 1','Net MAE 2','Final MAE',
            'Midcap MAE','Midcap MFE','Combined Net MAE 1','Combined Net MAE 2','Combined Final MAE'].includes(_colKey);
          cell.numFmt = pctAppendCols.has(_colKey)
            ? '0.00"%"'
            : truePctCols.has(_colKey)
            ? '0.00%'
            : _maeCol
            ? '#,##0.0000'
            : Number.isInteger(cell.value) ? '0' : '#,##0.00';
        }
      });
      // Color Net P&L and % P&L (NIFTY-only sheet) or Combined Net P&L (Midcap sheet).
      if (net !== null) {
        const col1 = keyOrder.indexOf('Net P&L')+1;
        const col2 = keyOrder.indexOf('% P&L')+1;
        [col1,col2].filter(c=>c>0).forEach(c => {
          const cell = r.getCell(c);
          cell.font = boldFont(10, net>=0 ? C.greenTx : C.redTx);
          cell.fill = { type:'pattern', pattern:'solid', fgColor: net>=0 ? C.greenBg : C.redBg };
        });
      }
      if (hasMidcap) {
        const cNet = typeof row['Combined Net P&L'] === 'number' ? row['Combined Net P&L'] : null;
        if (cNet !== null) {
          const c1 = keyOrder.indexOf('Combined Net P&L')+1;
          const c2 = keyOrder.indexOf('Combined Net P&L %')+1;
          [c1,c2].filter(c=>c>0).forEach(c => {
            const cell = r.getCell(c);
            cell.font = boldFont(10, cNet>=0 ? C.greenTx : C.redTx);
            cell.fill = { type:'pattern', pattern:'solid', fgColor: cNet>=0 ? C.greenBg : C.redBg };
          });
        }
      }
    });

    // ════════════════════════════════════════════════════════════════════════
    // SHEET 2 — SUMMARY
    // ════════════════════════════════════════════════════════════════════════
    const ws2 = wb.addWorksheet('Summary');
    ws2.columns = [
      { width: 30 },{ width: 20 },{ width: 12 },{ width: 30 },{ width: 20 },
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
    // When a Midcap leg is present, ALL Performance Overview stats run on the
    // COMBINED (NIFTY + Midcap) per-trade P&L; otherwise on NIFTY (unchanged).
    const _tPct = (t) => hasMidcap ? t['Combined Net P&L %'] : t['% P&L'];
    const _tNet = (t) => hasMidcap ? t['Combined Net P&L'] : t['Net P&L'];
    const _tCum = (t) => hasMidcap ? t['Combined Cumulative'] : t['Cumulative'];
    for (const t of cleanedTrades) {
      const p = _tPct(t); const n = _tNet(t);
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
      const cum = _tCum(t);
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
    const _expectancyJS = _avgLossPctJS !== 0
      ? ((_winRateJS / 100) * _avgWinPctJS / Math.abs(_avgLossPctJS) - (1 - _winRateJS / 100))
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
    // Store the raw float so Excel formulas referencing it get full precision;
    // numFmt '0.00' renders the cell to 2 decimals for display.
    const _expRow_rp = row++;
    const _avgPctJS = _totalCntJS > 0 ? (_sumPctJS / _totalCntJS) : 0;
    kv('Net P/L Avg %', `${_avgPctJS>=0?'+':''}${_avgPctJS.toFixed(4)}%`, row++, 'A', false,
       _avgPctJS>=0?C.greenTx:C.redTx);
    kv('Expectancy Ratio', _expectancyJS, _expRow_rp, 'D', true,
       _expectancyJS>=0?C.greenTx:C.redTx);
    ws2.getCell(`E${_expRow_rp}`).numFmt = '0.00';

    kv('Max Profit (Single Trade)', _fmtCurrency(_maxNetJS), row, 'A', false, C.greenTx);
    kv('Max Loss (Single Trade)',   _fmtCurrency(_minNetJS), row++, 'D', false, C.redTx);

    kv('CAGR (Options)', _fmtPct(_optCagrPctJS),  row, 'A', true, _optCagrPctJS>=0?C.greenTx:C.redTx);
    kv('CAGR (Spot)',    _fmtPct(_spotCagrPctJS), row++, 'D', true, _spotCagrPctJS>=0?C.greenTx:C.redTx);

    // ── ROI vs SPOT block (Type / Sum / ROI vs Spot, research team layout) ───
    if (cleanedTrades.length > 0) {
      row++; // spacer

      // Per-side sums (research team's =SUM(...) formulas, computed in JS)
      let _ceSumJS = 0, _peSumJS = 0, _futSumJS = 0;
      let _cePctJS = 0, _pePctJS = 0, _spotPctJS = 0;
      for (const t of cleanedTrades) {
        const ce = +t['CE P&L']; if (Number.isFinite(ce)) _ceSumJS += ce;
        const pe = +t['PE P&L']; if (Number.isFinite(pe)) _peSumJS += pe;
        const fu = +t['FUT P&L']; if (Number.isFinite(fu)) _futSumJS += fu;
        const cep = +t['CE P&L %']; if (Number.isFinite(cep)) _cePctJS += cep;
        const pep = +t['PE P&L %']; if (Number.isFinite(pep)) _pePctJS += pep;
        const spp = +t['Spot P&L %']; if (Number.isFinite(spp)) _spotPctJS += spp;
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
      _hdr('A','Type'); _hdr('B','Sum'); _hdr('C','%');
      ws2.mergeCells(`D${row}:E${row}`);
      _hdr('D','ROI vs Spot');
      ws2.getCell(`D${row}`).alignment = centerAlign;
      ws2.getRow(row).height = 20;
      const _hdrRow = row; row++;

      const _addTypeRow = (label, value, pct) => {
        const lC = ws2.getCell(`A${row}`);
        const vC = ws2.getCell(`B${row}`);
        const pC = ws2.getCell(`C${row}`);
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
        if (pct != null) {
          pC.value = `${pct >= 0 ? '+' : ''}${(+pct).toFixed(2)}%`;
          pC.font = boldFont(10, pct >= 0 ? C.greenTx : C.redTx);
          pC.fill = { type:'pattern', pattern:'solid', fgColor: C.white };
          pC.alignment = leftAlign;
          pC.border = thinBorder(C.border);
        }
        ws2.getRow(row).height = 18;
      };

      // ROI vs Spot = Net P&L % / |Spot %|  (workbook C17/C14). With a Midcap leg
      // the numerator is the COMBINED net % (Overall Profit), not the NIFTY-only
      // total — and it's shown as the raw ratio (e.g. 1.5007), not a percent.
      const _netPctForRoi = hasMidcap
        ? _sumPctJS
        : ((typeof summary?.total_pnl_pct === 'number' && Number.isFinite(summary.total_pnl_pct))
            ? summary.total_pnl_pct : _sumPctJS);
      const _spotPctForRoi = (typeof summary?.spot_change_pct === 'number' && Number.isFinite(summary.spot_change_pct))
        ? summary.spot_change_pct : (_spotPctJS * 100);
      const _roiPctJS = Math.abs(_spotPctForRoi) > 0 ? _netPctForRoi / Math.abs(_spotPctForRoi) : 0;

      // ROI value in D-E of the first data row (Spot P&L row)
      const _spotRow = row;
      ws2.mergeCells(`D${_spotRow}:E${_spotRow}`);
      const _roiVal = ws2.getCell(`D${_spotRow}`);
      if (hasMidcap) { _roiVal.value = _roiPctJS; _roiVal.numFmt = 'General'; }
      else { _roiVal.value = _fmtPct(_roiPctJS); }
      _roiVal.font = boldFont(11, _roiPctJS >= 0 ? C.greenTx : C.redTx);
      _roiVal.fill = { type:'pattern', pattern:'solid', fgColor: C.white };
      _roiVal.alignment = centerAlign;
      _roiVal.border = thinBorder(C.border);

      // Rows — use backend summary for Spot P&L (single source of truth across
      // the regular-backtest, optimizer-combo, and optimizer-ZIP downloads).
      const _spotSumSummary = (typeof summary?.spot_change === 'number' && Number.isFinite(summary.spot_change))
        ? summary.spot_change : _spotSumGatedJS;
      const _spotPctSummary = (typeof summary?.spot_change_pct === 'number' && Number.isFinite(summary.spot_change_pct))
        ? summary.spot_change_pct : (_spotPctJS * 100);
      _addTypeRow('Spot P&L', _spotSumSummary, _spotPctSummary); row++;
      if (hasCalls) { _addTypeRow('CE P&L', _ceSumJS, _cePctJS * 100); row++; }
      if (hasPuts)  { _addTypeRow('PE P&L', _peSumJS, _pePctJS * 100); row++; }
      if (hasFutures) { _addTypeRow('FUT P&L', _futSumJS, null); row++; }
      if (hasCalls && hasPuts) { _addTypeRow('CE + PE P&L', _ceSumJS + _peSumJS, (_cePctJS + _pePctJS) * 100); row++; }
      // Midcap overlay rows (matches the sample Summary): leg P&L and combined.
      if (hasMidcap) {
        const mcs = results?.midcap?.summary || {};
        const mcLegs = results?.midcap?.legs || [];
        const isHypo = mcLegs.some(l => String(l.midcap_mode || l.mode || '').toLowerCase() === 'hypothetical');
        const sym = mcs.symbol || 'NIFTYMIDCAP100';
        const modeLbl = isHypo ? 'Hypothetical Future' : 'Spot';
        const niftyPrefix = ['CE', 'PE', 'FUT'].filter((_, i) => [hasCalls, hasPuts, hasFutures][i]).join(' + ') || 'NIFTY';
        _addTypeRow(`${sym} ${modeLbl} P&L`, mcs.midcap_leg_pnl_sum, mcs.midcap_leg_pnl_pct_sum); row++;
        _addTypeRow(`${niftyPrefix} + ${sym} ${modeLbl} P&L`, mcs.combined_pnl_sum, mcs.combined_pnl_pct_sum); row++;
      }
      _addTypeRow('Net P&L', _sumNetJS, _sumPctJS); row++;
    }

    row++; // blank

    // ── SECTION 2: Risk Metrics ─────────────────────────────────────────────
    addSectionHeader('RISK METRICS', row++);

    const mddColor = C.redTx;
    // With a Midcap leg, derive risk/streak metrics from the COMBINED NAV;
    // otherwise use the NIFTY-based `stats` (unchanged).
    // Max Drawdown = min over trades of (Cumulative/Peak-1)*100 on the equity curve —
    // Combined NAV with a Midcap leg, NIFTY NAV otherwise. Read straight from the
    // Cumulative/Peak columns (units-safe, already patch-reset when patchwise) so the
    // export Summary == the per-trade %DD column, both overall AND patchwise, midcap
    // AND non-midcap.
    let _maxDDPctJS, _mddDurationJS, _mddStartJS, _mddEndJS, _maxWinStreakJS, _maxLossStreakJS;
    {
      const cumKey  = hasMidcap ? 'Combined Cumulative' : 'Cumulative';
      const peakKey = hasMidcap ? 'Combined Peak'       : 'Peak';
      const pctKey  = hasMidcap ? 'Combined Net P&L %'  : '% P&L';
      let peakMs = null, worstDD = 0, worstPeakMs = null, worstTroughMs = null;
      let winRun = 0, lossRun = 0, maxWin = 0, maxLoss = 0;
      for (const t of cleanedTrades) {
        const pct = t[pctKey];
        if (typeof pct === 'number' && Number.isFinite(pct)) {
          if (pct > 0) { winRun++; lossRun = 0; if (winRun > maxWin) maxWin = winRun; }
          else if (pct < 0) { lossRun++; winRun = 0; if (lossRun > maxLoss) maxLoss = lossRun; }
        }
        const cum = t[cumKey];
        const pk = t[peakKey];
        const xD = _parseDate(t['Exit Date']);
        if (typeof cum === 'number' && typeof pk === 'number' && Number.isFinite(cum) && pk !== 0) {
          if (cum >= pk - 1e-9) { peakMs = xD; }
          else { const ddp = (cum / pk - 1) * 100; if (ddp < worstDD) { worstDD = ddp; worstTroughMs = xD; worstPeakMs = peakMs; } }
        }
      }
      _maxDDPctJS = worstDD;
      _maxWinStreakJS  = hasMidcap ? maxWin  : (stats.maxWinStreak ?? 0);
      _maxLossStreakJS = hasMidcap ? maxLoss : (stats.maxLossStreak ?? 0);
      const _fmtMs = (ms) => { const d = new Date(ms); return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`; };
      if (worstPeakMs != null && worstTroughMs != null) {
        _mddDurationJS = Math.round((worstTroughMs - worstPeakMs) / 86400000);
        _mddStartJS = _fmtMs(worstPeakMs); _mddEndJS = _fmtMs(worstTroughMs);
      } else { _mddDurationJS = 0; _mddStartJS = null; _mddEndJS = null; }
    }
    kv('Max Drawdown', `${_maxDDPctJS.toFixed(2)}%`, row, 'A', false, mddColor);
    kv('Max DD Days', _mddDurationJS, row++, 'D', false, mddColor);

    // Full-width DD period
    const ddPeriod = (_mddStartJS && _mddEndJS) ? `${_mddStartJS}  →  ${_mddEndJS}` : '—';
    ws2.mergeCells(`A${row}:E${row}`);
    const ddCell = ws2.getCell(`A${row}`);
    ddCell.value = `Drawdown Period:  ${ddPeriod}`;
    ddCell.font  = boldFont(10, C.redTx);
    ddCell.fill  = { type:'pattern', pattern:'solid', fgColor: C.redBg };
    ddCell.alignment = centerAlign;
    ddCell.border = thinBorder(C.border);
    ws2.getRow(row).height = 18;
    row++;

    const _carMddJS = _maxDDPctJS !== 0 ? (_optCagrPctJS / 100) / Math.abs(_maxDDPctJS) : 0;
    kv('Return / MaxDD', _carMddJS.toFixed(4),  row++, 'A', true);

    row++;

    // ── SECTION 3: Consistency ──────────────────────────────────────────────
    addSectionHeader('CONSISTENCY & STREAKS', row++);

    kv('Max Win Streak',    `${_maxWinStreakJS} trades`,   row, 'A', false, C.greenTx);
    kv('Max Losing Streak', `${_maxLossStreakJS} trades`,  row++, 'D', false, C.redTx);

    row++;

    // ── SECTION 4: Monthly Returns ──────────────────────────────────────────
    addSectionHeader('MONTHLY RETURNS (₹ Net P&L)', row++);

    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const mthHdr = ['Year',...MONTHS,'Total','Max DD','R/MDD'];

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
    const byYMSpot = {};
    const byYMSpotPct = {};
    groupedTrades.forEach(group => {
      const exitDate = group?.exitDate || '';
      const ym = parseToYearMonth(exitDate);
      if (!ym) return;
      const eSpot   = Number(group?.entrySpot ?? 0) || 0;
      const xSpot   = Number(group?.exitSpot  ?? 0) || 0;
      let net = Number(group?.totalPnl ?? 0) || 0;
      let pct = eSpot > 0 ? (net / eSpot) * 100 : 0;
      if (hasMidcap) {  // monthly Net P&L on COMBINED when a Midcap leg is present
        const _mc = midcapByTrade[group.groupKey];
        if (_mc && typeof _mc['Combined Net P&L'] === 'number') {
          net = _mc['Combined Net P&L'];
          pct = Number(_mc['Combined Net P&L %']) || 0;
        }
      }
      const spotPnl = xSpot - eSpot;
      const spotPct = eSpot > 0 ? (spotPnl / eSpot) * 100 : 0;
      if (!byYM[ym.year])       byYM[ym.year]       = Array(12).fill(0);
      if (!byYMPct[ym.year])    byYMPct[ym.year]    = Array(12).fill(0);
      if (!byYMSpot[ym.year])   byYMSpot[ym.year]   = Array(12).fill(0);
      if (!byYMSpotPct[ym.year])byYMSpotPct[ym.year]= Array(12).fill(0);
      byYM[ym.year][ym.monthIdx]       = (byYM[ym.year][ym.monthIdx]       || 0) + net;
      byYMPct[ym.year][ym.monthIdx]    = (byYMPct[ym.year][ym.monthIdx]    || 0) + pct;
      byYMSpot[ym.year][ym.monthIdx]   = (byYMSpot[ym.year][ym.monthIdx]   || 0) + spotPnl;
      byYMSpotPct[ym.year][ym.monthIdx]= (byYMSpotPct[ym.year][ym.monthIdx]|| 0) + spotPct;
    });

    // Per-year Max DD for the % Net P&L table: worst %DD (or Combined %DD with
    // Midcap) among trades exiting that year — full precision, kept as a raw
    // fraction-of-100 number so the cell can carry a '0.00%' display format
    // instead of baking the 2-decimal rounding into the stored value.
    const byYearMaxDDPct = {};
    groupedTrades.forEach(group => {
      const ym = parseToYearMonth(group?.exitDate || '');
      if (!ym) return;
      // group.pct_dd is the RAW (non-patchwise, never-reset) %DD fixed at render
      // time. In patchwise mode, tm[k].pctDd was already recomputed above with
      // patch resets (matches the Trade Sheet's own %DD column) — use that
      // instead, or this table silently reports the global drawdown.
      let dd = hasMidcap
        ? Number(midcapByTrade[group.groupKey]?.['Combined %DD'])
        : (patchwise ? Number(tm[group.groupKey]?.pctDd) : Number(group?.pct_dd));
      if (!Number.isFinite(dd)) return;
      if (byYearMaxDDPct[ym.year] == null || dd < byYearMaxDDPct[ym.year]) {
        byYearMaxDDPct[ym.year] = dd;
      }
    });

    const mthData = Object.entries(byYM).sort().map(([yr, mos]) => {
      const total = mos.reduce((s, v) => s + v, 0);
      const extras = pivotExtrasByYear[yr] || ['', '', ''];
      // Max DD here mirrors the optimizer sheets: the %DD-based percentage
      // (same value as the % table below), not the backend pivot's rupee /
      // date-range string. R/MDD (extras[2]) still comes from the pivot.
      const maxDdPct = byYearMaxDDPct[yr] != null ? byYearMaxDDPct[yr] : '';
      return [yr, ...mos.map(v => +v.toFixed(2)), +total.toFixed(2), maxDdPct, extras[2]];
    });

    mthData.forEach((dataRow, ri) => {
      const r2 = ws2.getRow(row);
      dataRow.forEach((val, ci) => {
        const cell = r2.getCell(ci+1);
        cell.value = val;
        const num  = typeof val==='number' ? val : parseFloat(String(val||'').replace(/[%,]/g,''));
        const isValCol = ci>=1 && ci<=12; // month columns
        const isTotalCol = ci===13;
        const isMaxDdCol = ci===14;
        if (isMaxDdCol && typeof val === 'number') {
          cell.value = val / 100;
          cell.numFmt = '0.00%';
        }
        if ((isValCol||isTotalCol) && !isNaN(num) && num!==0) {
          cell.font = boldFont(10, num>=0 ? C.greenTx : C.redTx);
          cell.fill = { type:'pattern', pattern:'solid', fgColor: num>=0 ? C.greenBg : C.redBg };
        } else if (isMaxDdCol && !isNaN(num) && num!==0) {
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

    const mthHdrPct = ['Year',...MONTHS,'Total','Max DD','R/MDD'];
    const mthColsPct = mthHdrPct.length;
    for (let ci = 0; ci < mthColsPct; ci++) {
      ws2.getColumn(ci+1).width = ci===0 ? 8 : ci<=12 ? 9 : ci===13 ? 10 : ci===14 ? 10 : 10;
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
      const maxDd = byYearMaxDDPct[yr] != null ? byYearMaxDDPct[yr] : '';
      // R/MDD is %Total / %MaxDD — both already percentage-points here, so no
      // extra scaling is needed (matches the optimizer sheets' convention).
      const rMdd = (typeof maxDd === 'number' && maxDd !== 0 && total !== 0)
        ? +(total / Math.abs(maxDd)).toFixed(2)
        : '';
      return [yr, ...mos, total, maxDd, rMdd];
    });

    mthDataPct.forEach((dataRow, ri) => {
      const r2 = ws2.getRow(row);
      dataRow.forEach((val, ci) => {
        const cell = r2.getCell(ci+1);
        const isValCol    = ci >= 1 && ci <= 12;
        const isTotalCol  = ci === 13;
        const isMaxDdCol  = ci === 14;
        if (isValCol || isTotalCol) {
          cell.value = typeof val === 'number' ? val / 100 : val;
          cell.numFmt = '0.00%';
        } else if (isMaxDdCol) {
          cell.value = typeof val === 'number' ? val / 100 : val;
          if (typeof val === 'number') cell.numFmt = '0.00%';
        } else {
          cell.value = val;
        }
        const num = typeof val === 'number' ? val : parseFloat(String(val || ''));
        if ((isValCol || isTotalCol || isMaxDdCol) && !isNaN(num) && num !== 0) {
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

    // ── SECTION 4c: Monthly Returns (₹ Spot P&L) ─────────────────────────────
    row++;
    addSectionHeader('MONTHLY RETURNS (₹ Spot P&L)', row++);

    const mthHdrSpot = ['Year',...MONTHS,'Total'];
    for (let ci = 0; ci < mthHdrSpot.length; ci++) {
      ws2.getColumn(ci+1).width = ci===0 ? 8 : 9;
    }
    const hdrRowSpot = ws2.getRow(row);
    mthHdrSpot.forEach((h, ci) => {
      const cell = hdrRowSpot.getCell(ci+1);
      cell.value = h;
      cell.font  = boldFont(10, C.navyText);
      cell.fill  = { type:'pattern', pattern:'solid', fgColor: C.headerBg };
      cell.alignment = centerAlign;
      cell.border = thinBorder();
    });
    hdrRowSpot.height = 20;
    row++;

    const mthDataSpot = Object.entries(byYMSpot).sort().map(([yr, mos]) => {
      const total = mos.reduce((s, v) => s + v, 0);
      return [yr, ...mos.map(v => +v.toFixed(2)), +total.toFixed(2)];
    });
    mthDataSpot.forEach((dataRow, ri) => {
      const r2 = ws2.getRow(row);
      dataRow.forEach((val, ci) => {
        const cell = r2.getCell(ci+1);
        cell.value = val;
        const num = typeof val==='number' ? val : parseFloat(String(val||'').replace(/[%,]/g,''));
        const isValCol = ci>=1 && ci<=12;
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

    // ── SECTION 4d: Monthly Returns (% Spot P&L) ─────────────────────────────
    row++;
    addSectionHeader('MONTHLY RETURNS (% Spot P&L)', row++);

    const mthHdrSpotPct = ['Year',...MONTHS,'Total'];
    for (let ci = 0; ci < mthHdrSpotPct.length; ci++) {
      ws2.getColumn(ci+1).width = ci===0 ? 8 : 9;
    }
    const hdrRowSpotPct = ws2.getRow(row);
    mthHdrSpotPct.forEach((h, ci) => {
      const cell = hdrRowSpotPct.getCell(ci+1);
      cell.value = h;
      cell.font  = boldFont(10, C.navyText);
      cell.fill  = { type:'pattern', pattern:'solid', fgColor: C.headerBg };
      cell.alignment = centerAlign;
      cell.border = thinBorder();
    });
    hdrRowSpotPct.height = 20;
    row++;

    const mthDataSpotPct = Object.entries(byYMSpotPct).sort().map(([yr, mos]) => {
      const total = mos.reduce((s, v) => s + v, 0);
      return [yr, ...mos.map(v => +v.toFixed(2)), +total.toFixed(2)];
    });
    mthDataSpotPct.forEach((dataRow, ri) => {
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
        const num = typeof val === 'number' ? val : parseFloat(String(val||''));
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

    // ── SECTION 5: Live DD & Outlier Analysis ─────────────────────────────────
    row++;
    addSectionHeader('LIVE DD & OUTLIER ANALYSIS', row++);

    // Build per-trade (% P&L, Actual Live DD) pairs — one entry per unique trade
    const _tradePairs = [];
    const _seenChron = new Set();
    sortedTrades.forEach(tRow => {
      const k = String(tRow.Trade || tRow.trade || 1);
      if (_seenChron.has(k) || !tm[k]) return;
      _seenChron.add(k);
      const t = tm[k];
      // With a Midcap leg the Live DD & Outlier analysis runs on the COMBINED
      // per-trade P&L % / Final MAE / Actual Live DD; otherwise NIFTY (unchanged).
      let pct, ldd, mae;
      if (hasMidcap) {
        const cp = t.midcap ? Number(t.midcap['Combined Net P&L %']) : NaN;
        pct = Number.isFinite(cp) ? cp : null;
        ldd = (typeof t.combinedActualLiveDd === 'number' && Number.isFinite(t.combinedActualLiveDd)) ? t.combinedActualLiveDd : null;
        mae = (typeof t.combinedFinalMae === 'number' && Number.isFinite(t.combinedFinalMae)) ? t.combinedFinalMae : null;
      } else {
        pct = (typeof t.pct === 'number' && Number.isFinite(t.pct)) ? t.pct : null;
        ldd = (typeof t.actualLiveDD === 'number' && Number.isFinite(t.actualLiveDD)) ? t.actualLiveDD : null;
        mae = (typeof t.finalMae === 'number' && Number.isFinite(t.finalMae)) ? t.finalMae : null;
      }
      if (pct !== null) _tradePairs.push({ pct, ldd, mae, idx: _tradePairs.length, exitReason: (t.exitReason || '').toUpperCase(), segIdx: _pwSegIdxByKey(k) });
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

    const _totalPctSumO = _tradePairs.reduce((s, p) => s + p.pct, 0);
    const _pctNoO1 = _totalPctSumO - _posOutlier1Pct - _negOutlier1Pct;
    const _pctNoO2 = _totalPctSumO - _posOutlier2Pct - _negOutlier2Pct;
    const _pctNoO3 = _totalPctSumO - _posOutlier3Pct - _negOutlier3Pct;

    // Live DD min/avg excluding top excTop and bottom excBot trades by % P&L rank
    const _liveDDExcStats = (excTop, excBot) => {
      const excIdx = new Set([
        ..._byPctDesc.slice(0, excTop).map(p => p.idx),
        ..._byPctDesc.slice(Math.max(0, _nTrades - excBot)).map(p => p.idx),
      ]);
      const filtered = _tradePairs.filter(p => !excIdx.has(p.idx));
      if (filtered.length === 0) return { min: 0, avg: 0 };
      let cumulative = 100;
      let peak = 100;
      let prevCum = 100;
      let prevPeak = 100;
      let firstTradeDone = false;
      let prevSegIdx = null, prevExitReason = '';
      const ldds = [];
      filtered.forEach(p => {
        // Reset the chain at each patch boundary (same boundary as the combined chain).
        const _reset = patchwise && (_pwSegStarts.length
          ? (prevSegIdx !== null && p.segIdx !== prevSegIdx)
          : ((prevExitReason || '').split('+').includes('FILTER_END')));
        if (_reset) { cumulative = 100; peak = 100; prevCum = 100; prevPeak = 100; }
        prevSegIdx = p.segIdx; prevExitReason = p.exitReason || '';
        prevPeak = peak;
        cumulative *= (1 + p.pct / 100);
        peak = Math.max(peak, cumulative);
        if (p.mae !== null && prevPeak !== 0) {
          // Revised rule: every trade (incl. first, prevCum = 100) anchors the
          // low to prevCum * (1 + FinalMAE%); Live DD divides by the PREVIOUS
          // trade's peak (AV_prev), not this trade's peak.
          const lowestNav = Math.round(prevCum * (1 + p.mae / 100) * 100) / 100;
          const actualLiveDD = Math.round((lowestNav / prevPeak - 1) * 10000) / 100;
          ldds.push(actualLiveDD);
          firstTradeDone = true;
        } else {
          firstTradeDone = true;
        }
        prevCum = cumulative;
      });
      if (ldds.length === 0) return { min: 0, avg: 0 };
      return {
        min: +Math.min(...ldds).toFixed(2),
        avg: +(ldds.reduce((s, v) => s + v, 0) / ldds.length).toFixed(2),
      };
    };

    const _allLDDs = _tradePairs.filter(p => p.ldd !== null).map(p => p.ldd);
    const _actualLiveDDMin = _allLDDs.length > 0 ? +Math.min(..._allLDDs).toFixed(2) : 0;
    const _actualLiveDDAvg = _allLDDs.length > 0
      ? +(_allLDDs.reduce((s, v) => s + v, 0) / _allLDDs.length).toFixed(2) : 0;
    // Avg (Combined) Final MAE — mean of each trade's Final MAE. With a Midcap leg
    // this uses the Combined Final MAE; otherwise the NIFTY Final MAE. (p.mae already
    // holds combinedFinalMae for Midcap, finalMae otherwise.)
    const _finalMaes = _tradePairs.filter(p => p.mae !== null).map(p => p.mae);
    const _avgFinalMaeJS = _finalMaes.length > 0
      ? +(_finalMaes.reduce((s, v) => s + v, 0) / _finalMaes.length).toFixed(2) : 0;
    const _liveDDNoO1 = _liveDDExcStats(1, 1);
    const _liveDDNoO2 = _liveDDExcStats(2, 2);
    const _liveDDNoO3 = _liveDDExcStats(3, 3);
    const _carMddLiveJS = _actualLiveDDMin !== 0
      ? (_optCagrPctJS / 100) / Math.abs(_actualLiveDDMin) : 0;

    // KV summary rows
    kv('Actual Live DD (min)', `${_actualLiveDDMin.toFixed(2)}%`, row, 'A', false, C.redTx);
    kv('Avg Actual Live DD',   `${_actualLiveDDAvg.toFixed(2)}%`, row++, 'D', false, C.redTx);
    kv(hasMidcap ? 'Avg Combined Final MAE' : 'Avg Final MAE',
       `${_avgFinalMaeJS.toFixed(2)}%`, row++, 'A', false, C.redTx);
    kv('CAR/MDD (Booked)',     _carMddJS.toFixed(4),              row, 'A', true,
       _carMddJS >= 0 ? C.greenTx : C.redTx);
    kv('CAR/MDD Live',         _carMddLiveJS.toFixed(4),          row++, 'D', true,
       _carMddLiveJS >= 0 ? C.greenTx : C.redTx);

    row++;

    // Outlier table — 5 columns
    // Outlier rows — exact Excel column names (Q/R/S/T, U/V/W/X, Y/Z/AA/AB)
    kv('+ve Outlier 1',                        _fmtPct(_posOutlier1Pct),          row, 'A', false, C.greenTx);
    kv('-ve Outlier 1',                        _fmtPct(_negOutlier1Pct),          row++, 'D', false, C.redTx);
    kv('Actual Live DD Without Outlier 1',     `${_liveDDNoO1.min.toFixed(2)}%`,  row, 'A', true,  C.redTx);
    kv('Avg Actual Live DD Without Outlier 1', `${_liveDDNoO1.avg.toFixed(2)}%`,  row++, 'D', true,  C.redTx);
    kv('+ve Outlier 2',                        _fmtPct(_posOutlier2Pct),          row, 'A', false, C.greenTx);
    kv('-ve Outlier 2',                        _fmtPct(_negOutlier2Pct),          row++, 'D', false, C.redTx);
    kv('Actual Live DD Without Outlier 2',     `${_liveDDNoO2.min.toFixed(2)}%`,  row, 'A', true,  C.redTx);
    kv('Avg Actual Live DD Without Outlier 2', `${_liveDDNoO2.avg.toFixed(2)}%`,  row++, 'D', true,  C.redTx);
    kv('+ve Outlier 3',                        _fmtPct(_posOutlier3Pct),          row, 'A', false, C.greenTx);
    kv('-ve Outlier 3',                        _fmtPct(_negOutlier3Pct),          row++, 'D', false, C.redTx);
    kv('Actual Live DD Without Outlier 3',     `${_liveDDNoO3.min.toFixed(2)}%`,  row, 'A', true,  C.redTx);
    kv('Avg Actual Live DD Without Outlier 3', `${_liveDDNoO3.avg.toFixed(2)}%`,  row++, 'D', true,  C.redTx);

    // "… P&L % Without Top N Outliers" — label reflects the leg configuration.
    // With a Midcap leg it matches the Type rows (e.g. "CE + NIFTYMIDCAP100
    // Hypothetical Future P&L %"); otherwise the existing "CE + PE + P&L %".
    // Formula: totalSum - posOutlierSum - negOutlierSum.
    row++;
    let _outlierBase = 'CE + PE + P&L %';
    if (hasMidcap) {
      const _mcs = results?.midcap?.summary || {};
      const _mcLegs = results?.midcap?.legs || [];
      const _isHypo = _mcLegs.some(l => String(l.midcap_mode || l.mode || '').toLowerCase() === 'hypothetical');
      const _sym = _mcs.symbol || 'NIFTYMIDCAP100';
      const _modeLbl = _isHypo ? 'Hypothetical Future' : 'Spot';
      const _niftyPrefix = ['CE', 'PE', 'FUT'].filter((_, i) => [hasCalls, hasPuts, hasFutures][i]).join(' + ') || 'NIFTY';
      _outlierBase = `${_niftyPrefix} + ${_sym} ${_modeLbl} P&L %`;
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

    // ── SHEET 4: Patch wise (phase-wise distribution) ─────────────────────────
    // Additive — only when a Midcap leg ran AND a filter split the run into
    // "patches" (one per Filter Segment). Three phase blocks side-by-side:
    // Midcap-only, NIFTY-only, Combined. Per phase the equity curve RESETS to
    // 100 at the start of every patch; a side table summarises CAGR / P&L% /
    // min Live DD per patch. Skipped entirely when no Midcap or no filter.
    if (Boolean(filterInfo)) {
      const numP = v => { const n = toNumber(v); return n == null ? null : n; };
      // chronological trade order (cascade re-entries placed by entry date)
      const _seenP = new Set(); const orderedKeys = [];
      sortedTrades.forEach(_tr => { const _k = String(_tr.Trade||_tr.trade||1); if (!_seenP.has(_k)) { _seenP.add(_k); if (tm[_k]) orderedKeys.push(_k); } });
      // extract per-trade drivers for all three phases
      const tdata = orderedKeys.map(k => {
        const legs = groupedByTrade[k] || [];
        const mainRow = legs.find(l => !l['ReEntryIndex'] && !l['ReEntryTrigger'] && !l['ReEntryMode'] && !isLazyLegRow(l)) || legs[0] || {};
        const spot = numP(mainRow['Entry Spot']) || 0;
        const mc = (tm[k] && tm[k].midcap) || {};
        // NIFTY phase uses whatever option leg(s) are present (CE and/or PE), not
        // just CE — so SELL PE / BUY PE / CE+PE all work. Sum option-leg P&L + MAE.
        const optLegs = legs.filter(l => ['CE','CALL','PE','PUT'].includes((l['Type']||'').toUpperCase()));
        const niftyPnl = optLegs.reduce((s,l) => s + (numP(l['CE P&L'])||0) + (numP(l['PE P&L'])||0), 0);
        const niftyMaeSum = optLegs.length ? optLegs.reduce((s,l) => s + (numP(l['MAE'])||0), 0) : null;
        const cfm = tm[k] ? tm[k].combinedFinalMae : '';
        return {
          entry: mainRow['Entry Date'], exit: mainRow['Exit Date'],
          entryMs: parseTradeDate(mainRow['Entry Date']), exitMs: parseTradeDate(mainRow['Exit Date']),
          // Phase 1 — Midcap only
          midcapPct: numP(mc['Midcap Leg P&L %']), midcapMae: numP(mc['Midcap MAE']), midcapClose: numP(mc['Midcap Entry Spot']),
          // Phase 2 — NIFTY only (option-leg P&L % + that leg's own MAE; CE or PE)
          callPct: (optLegs.length && spot > 0) ? (niftyPnl/spot)*100 : null, callMae: niftyMaeSum,
          // Phase 3 — Combined (Net P&L % already includes Midcap; Combined Final MAE)
          combinedPct: numP(mc['Combined Net P&L %']), combinedMae: (cfm !== '' && cfm != null) ? Number(cfm) : null,
        };
      });
      // Group into patches from the UPLOADED FILTER's segment START dates: a new
      // patch begins (equity resets to 100) when a trade's entry reaches the next
      // segment start. The boundary is the NEXT start (not the segment's end), so
      // spot-adjustment cascades that re-enter past a window's end still belong to
      // that patch until the next segment begins. Falls back to 30-day gap
      // detection only when no filter segments are available.
      // Prefer the backend-resolved segments (results.meta.filter_segments — present
      // for BOTH uploaded-CSV and named filters); fall back to the strFilter prop.
      const _segSrc = (Array.isArray(results?.meta?.filter_segments) && results.meta.filter_segments.length)
        ? results.meta.filter_segments
        : (Array.isArray(filterSegments) ? filterSegments : []);
      const _segStarts = _segSrc
        .map(s => parseTradeDate(s && (s.start || s.Start || s.from || s.start_date || s.startdt)))
        .filter(ms => Number.isFinite(ms))
        .sort((a, b) => a - b);
      const patches = [];
      if (_segStarts.length) {
        let _curIdx = -2;
        tdata.forEach(td => {
          let i = 0; // index of greatest segment start <= entry (clamp pre-first-start to patch 0)
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
        // reconstruct one patch's equity chain (resets to 100) for a given driver/MAE
        const buildChain = (trades, driveOf, maeOf) => {
          let prevCumm = 100, peak = 100, prevPeak = 100; const rows = []; let pnlSum = 0, liveDDMin = Infinity;
          trades.forEach((td, idx) => {
            const dr = driveOf(td); const d = Number.isFinite(dr) ? dr : 0;
            const cumm = prevCumm * (1 + d/100);
            // Peak resets to 100 at each patch start and updates from the first trade
            // via MAX(cumm, 100) — matching reference formula AR = MAX(AQ, 100).
            peak = Math.max(peak, cumm);
            // DD blank when at/above the peak (=IF(Peak>Cumm, Cumm-Peak, "")); %DD=0 then.
            const dd = (peak > cumm) ? (cumm - peak) : ''; const pctDd = (typeof dd === 'number' && peak !== 0) ? (dd/peak)*100 : 0;
            const mv = maeOf(td); const m = Number.isFinite(mv) ? mv : 0;
            // Lowest NAV anchors to the PREVIOUS trade's cumm; Live DD divides by
            // the PREVIOUS trade's peak (AV_prev) — matching the revised main
            // tradesheet rule (AX = AW / AV_prev - 1), not this trade's peak.
            const lowestNav = prevCumm * (1 + m/100); const liveDD = prevPeak !== 0 ? (lowestNav/prevPeak - 1)*100 : 0;
            rows.push({ td, drive: d, cumm, peak, dd, pctDd, mae: m, lowestNav, liveDD });
            pnlSum += d; if (liveDD < liveDDMin) liveDDMin = liveDD; prevCumm = cumm; prevPeak = peak;
          });
          const last = rows[rows.length-1]; const f = trades[0], l = trades[trades.length-1];
          const days = (Number.isFinite(f.entryMs) && Number.isFinite(l.exitMs)) ? (l.exitMs - f.entryMs)/86400000 : null;
          const cagr = (days && days > 0 && last && last.cumm > 0) ? (Math.pow(last.cumm/100, 365/days) - 1)*100 : null;
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
        const hdrCell = (r, c, val, o={}) => { const cell = wsP.getRow(r).getCell(c); cell.value = val; cell.font = boldFont(o.size||10, o.tx||C.headerTx); cell.fill = { type:'pattern', pattern:'solid', fgColor: o.bg||C.headerBg }; cell.alignment = o.align||centerAlign; cell.border = thinBorder(); return cell; };
        const valCell = (r, c, val, fmt) => { const cell = wsP.getRow(r).getCell(c); cell.value = (val == null ? '' : val); cell.font = normFont(10); if (typeof val === 'number') cell.numFmt = fmt || '0.00'; cell.alignment = centerAlign; cell.border = thinBorder(); return cell; };

        let col = 1;
        PHASES.forEach(phase => {
          const chains = patches.map(p => buildChain(p, phase.drive, phase.mae));
          const dW = phase.detailHdr.length;
          const detailStart = col;
          const sideStart = col + dW + 1;
          // block title + subtitle
          const tCell = wsP.getRow(1).getCell(detailStart); tCell.value = phase.title; tCell.font = boldFont(11, C.navyText); tCell.fill = { type:'pattern', pattern:'solid', fgColor: C.navyBg }; tCell.alignment = leftAlign;
          wsP.mergeCells(1, detailStart, 1, detailStart + dW - 1);
          const sCell = wsP.getRow(2).getCell(detailStart); sCell.value = 'Phase wise Distribution'; sCell.font = boldFont(9, C.subHdrTx); sCell.fill = { type:'pattern', pattern:'solid', fgColor: C.subHdrBg }; sCell.alignment = leftAlign;
          wsP.mergeCells(2, detailStart, 2, detailStart + dW - 1);
          // detail header (row 4)
          phase.detailHdr.forEach((h, i) => hdrCell(4, detailStart + i, h));
          // detail rows (row 5+), continuous across patches; cumm resets per patch
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
          // side table header (row 4) + one row per patch (row 5+)
          phase.sideHdr.forEach((h, i) => hdrCell(4, sideStart + i, h, { bg: C.sectionBg, tx: C.sectionTx }));
          chains.forEach((ch, i) => {
            const sr = 5 + i; let c3 = sideStart;
            valCell(sr, c3++, ch.entry); valCell(sr, c3++, ch.exit);
            valCell(sr, c3++, ch.cagr, '0.00"%"'); valCell(sr, c3++, ch.pnlSum); valCell(sr, c3++, ch.liveDDMin);
          });
          // column widths
          for (let i = 0; i < dW; i++) wsP.getColumn(detailStart + i).width = (phase.dates && i < 2) ? 12 : 12;
          for (let i = 0; i < phase.sideHdr.length; i++) wsP.getColumn(sideStart + i).width = 12;
          col = sideStart + phase.sideHdr.length + 1; // advance past side table + 1-col gap
        });
      }
    }

    // ════════════════════════════════════════════════════════════════════════
    // SHEET — WOW & MOM Summary (shared util; identical for backtest + optim).
    // %DD here is m.pctDd — sourced from the engine's row_pct_dd, which the
    // engine already converts decimal→percentage (generic_algotest_engine.py
    // "row_pct_dd = trade.get('pct_dd', 0.0) * 100"); Combined %DD is the same
    // scale (combinedPctDd = (nav/peak-1)*100). Both need /100 for Excel's %.
    writeWowMomSheet(wb, cleanedTrades, {
      hasMidcap,
      title: buildWowMomTitle(strategyConfig),
      ddIsPercent: true,
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

  const formatYearOnly = (dateStr) => {
    if (!dateStr) return '';
    const parts = dateStr.split(/[-\/]/);
    if (parts.length === 3) return parts[2];
    return dateStr;
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
              {filterInfo ? (
                <div className="relative">
                  <button
                    onClick={() => setShowDownloadMenu(v => !v)}
                    className="run-btn px-4 py-2"
                    style={{ fontSize: '0.7rem', borderRadius: '7px' }}
                  >
                    <Download size={14} /> Export Excel ▾
                  </button>
                  {showDownloadMenu && (
                    <div
                      className="absolute right-0 top-full mt-1 z-50 rounded-lg shadow-lg overflow-hidden"
                      style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', minWidth: '180px' }}
                    >
                      <button
                        className="w-full px-4 py-2 text-left hover:opacity-80"
                        style={{ fontSize: '0.7rem', display: 'block' }}
                        onClick={() => { setShowDownloadMenu(false); exportToCSV(false); }}
                      >
                        Overall System DD
                      </button>
                      <button
                        className="w-full px-4 py-2 text-left hover:opacity-80"
                        style={{ fontSize: '0.7rem', display: 'block', borderTop: '1px solid var(--border)' }}
                        onClick={() => { setShowDownloadMenu(false); exportToCSV(true); }}
                      >
                        Patchwise DD
                      </button>
                    </div>
                  )}
                </div>
              ) : (
                <button onClick={() => exportToCSV(false)} className="run-btn px-4 py-2" style={{ fontSize: '0.7rem', borderRadius: '7px' }}>
                  <Download size={14} /> Export Excel
                </button>
              )}
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
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4 px-6 pt-2 pb-6" style={{ background: 'var(--bg-elevated)' }}>
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
                    tick={{ fontSize: 11, fill: 'var(--text-secondary)' }}
                    tickLine={false}
                    ticks={yearlyTicks}
                    tickFormatter={formatYearOnly}
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
                    tick={{ fontSize: 11, fill: 'var(--text-secondary)' }}
                    tickLine={false}
                    ticks={yearlyTicks}
                    tickFormatter={formatYearOnly}
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
                  <p className="font-bold text-primary mb-0.5">CAR/MDD</p>
                  <p className="font-normal text-primary">{(Math.abs(stats.maxDDPct) > 0 ? (stats.cagr / 100) / Math.abs(stats.maxDDPct) : 0).toFixed(4)}</p>
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
                <table className="min-w-full text-sm stryk-report-table">
                  <thead>
                    <tr className="bg-base border-b-2 border-strong">
                      <th className="px-3 py-3 text-center text-xs font-bold text-primary">Index</th>
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
                                <td className="px-3 py-2 text-xs text-center text-primary">{((currentPage - 1) * itemsPerPage) + groupIdx + 1}</td>
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
                                    <td className="px-3 py-2 text-xs text-center text-primary" rowSpan={group.legs.length}>{((currentPage - 1) * itemsPerPage) + groupIdx + 1}</td>
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
