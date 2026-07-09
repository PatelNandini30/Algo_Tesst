import React, { useMemo, useState, useEffect } from 'react';
import {
  AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine
} from 'recharts';
import { Download, X } from 'lucide-react';
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
    // Download the SAME styled workbook the optimizer ZIP uses — built on the
    // backend by excel_builder.build_combo_xlsx (openpyxl) with force_patch_wise
    // so the phase-wise "Patch wise" tab always appears. One builder for the ZIP
    // and every download → identical styling + numbers, no client ExcelJS rebuild.
    try {
      const meta = results?.meta || {};
      const _segs = (Array.isArray(meta.filter_segments) && meta.filter_segments.length)
        ? meta.filter_segments
        : (Array.isArray(filterSegments) && filterSegments.length ? filterSegments : null);
      const _base = buildExcelFileName(strategyConfig).replace(/\.xlsx$/i, '');
      const body = {
        trades: results?.trades || [],
        summary: results?.summary || {},
        combo_label: _base,
        from_date: meta.from_date || '',
        to_date: meta.to_date || '',
        index: meta.index || 'NIFTY',
        midcap_legs: meta.midcap_legs || null,
        midcap_spot_adjustment: meta.midcap_spot_adjustment || null,
        filter_segments: _segs,
        patchwise: !!patchwise,
      };
      const r = await fetch('/api/backtest/tradesheet.xlsx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert(err.detail || 'Failed to build tradesheet');
        return;
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${_base}${patchwise ? '_patchwise' : ''}.xlsx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(`Failed to download tradesheet: ${e?.message || e}`);
    }
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
