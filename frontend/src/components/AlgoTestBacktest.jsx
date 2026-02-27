import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { Play, Plus, Trash2, Info, Save, AlertTriangle } from 'lucide-react';
import ResultsPanel from './ResultsPanel';

const getLotSize = (index, tradeDate) => {
  const d = new Date(tradeDate);
  if (index === 'NIFTY') {
    if (d < new Date('2010-10-01')) return 200;
    if (d < new Date('2015-10-29')) return 50;
    if (d < new Date('2019-11-01')) return 75;
    return 65;
  }
  if (index === 'BANKNIFTY') {
    if (d < new Date('2010-10-01')) return 50;
    if (d < new Date('2015-10-29')) return 25;
    if (d < new Date('2019-11-01')) return 20;
    return 15;
  }
  if (index === 'FINNIFTY')   return 40;
  if (index === 'MIDCPNIFTY') return 75;
  if (index === 'SENSEX')     return 10;
  return 1;
};

const Toggle = ({ enabled, onToggle, size = 'md' }) => {
  const sizeClasses = size === 'sm' ? 'h-4 w-7' : 'h-5 w-9';
  const dotClasses = size === 'sm' ? 'h-3 w-3' : 'h-3.5 w-3.5';
  const translateClasses = size === 'sm' ? (enabled ? 'translate-x-3' : 'translate-x-0.5') : (enabled ? 'translate-x-4' : 'translate-x-0.5');
  
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`relative inline-flex ${sizeClasses} flex-shrink-0 items-center rounded-full transition-colors focus:outline-none ${
        enabled ? 'bg-blue-600' : 'bg-gray-300'
      }`}
    >
      <span className={`inline-block ${dotClasses} transform rounded-full bg-white shadow transition-transform ${translateClasses}`} />
    </button>
  );
};

const Tooltip = ({ text }) => {
  const [show, setShow] = useState(false);
  return (
    <span className="relative inline-flex">
      <button
        type="button"
        className="text-gray-400 hover:text-gray-600 focus:outline-none"
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
      >
        <Info size={12} />
      </button>
      {show && (
        <span className="absolute left-5 top-0 z-50 w-56 rounded bg-gray-900 p-2.5 text-xs text-white shadow-xl whitespace-normal leading-relaxed">
          {text}
        </span>
      )}
    </span>
  );
};

const SegBtn = ({ options, value, onChange, size = 'md' }) => {
  const sizeClasses = size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-3 py-1 text-xs';
  
  return (
    <div className="inline-flex rounded border border-gray-300 overflow-hidden">
      {options.map((opt, i) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={`${sizeClasses} font-medium transition-colors ${
            i < options.length - 1 ? 'border-r border-gray-300' : ''
          } ${
            value === opt.value
              ? 'bg-blue-600 text-white border-blue-600'
              : 'bg-white text-gray-700 hover:bg-gray-50'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
};

const AlgoTestBacktest = () => {
  const [instrument, setInstrument] = useState('NIFTY');
  const [underlying, setUnderlying] = useState('cash');
  const [strategyType, setStrategyType] = useState('positional');
  const [expiryBasis, setExpiryBasis] = useState('weekly');
  const [entryDaysBefore, setEntryDaysBefore] = useState(0);
  const [exitDaysBefore, setExitDaysBefore] = useState(0);
  const [delayRestart, setDelayRestart] = useState(false);
  const [delayTime, setDelayTime] = useState('09:15');
  const [overallMomentum, setOverallMomentum] = useState(false);
  const [momentumType, setMomentumType] = useState('points_up');
  const [momentumValue, setMomentumValue] = useState(0);
  const [squareOffMode, setSquareOffMode] = useState('partial');
  const [trailSLBreakeven, setTrailSLBreakeven] = useState(false);
  const [trailSLTarget, setTrailSLTarget] = useState('all_legs');
  const [legs, setLegs] = useState([]);

  const [draftLeg, setDraftLeg] = useState({
    segment: 'options',
    position: 'sell',
    lot: 1,
    option_type: 'call',
    expiry: 'weekly',
    strike_criteria: 'strike_type',
    strike_type: 'atm',
    premium_value: 0,
    premium_min: 0,
    premium_max: 0,
  });

  const [overallSLEnabled, setOverallSLEnabled] = useState(false);
  const [overallSLType, setOverallSLType] = useState('max_loss');
  const [overallSLValue, setOverallSLValue] = useState(0);
  const [overallTgtEnabled, setOverallTgtEnabled] = useState(false);
  const [overallTgtType, setOverallTgtType] = useState('max_profit');
  const [overallTgtValue, setOverallTgtValue] = useState(0);
  const [trailingEnabled, setTrailingEnabled] = useState(false);
  const [trailingType, setTrailingType] = useState('lock');
  const [trailingIfProfit, setTrailingIfProfit] = useState(0);
  const [trailingLockProfit, setTrailingLockProfit] = useState(1);
  const [reentryOnSL, setReentryOnSL] = useState(false);
  const [reentryOnSLMode, setReentryOnSLMode] = useState('re_asap');
  const [reentryOnSLCount, setReentryOnSLCount] = useState(1);
  const [reentryOnTgt, setReentryOnTgt] = useState(false);
  const [reentryOnTgtMode, setReentryOnTgtMode] = useState('re_asap');
  const [reentryOnTgtCount, setReentryOnTgtCount] = useState(1);
  const [startDate, setStartDate] = useState('2025-02-20');
  const [endDate, setEndDate] = useState('2026-02-20');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState(null);
  const [validationError, setValidationError] = useState(null);
  const abortRef = useRef(null);  // tracks in-flight fetch for cancellation

  // Memoize static derived values so they don't rebuild on every keystroke
  const daysOptions = useMemo(
    () => expiryBasis === 'weekly' ? [0, 1, 2, 3, 4] : Array.from({ length: 25 }, (_, i) => i),
    [expiryBasis]
  );

  const strikeTypeOpts = useMemo(() => [
    ...Array.from({ length: 20 }, (_, i) => ({ value: `itm${20 - i}`, label: `ITM ${20 - i}` })),
    { value: 'atm', label: 'ATM' },
    ...Array.from({ length: 20 }, (_, i) => ({ value: `otm${i + 1}`, label: `OTM ${i + 1}` })),
  ], []);

  // Validate expiry mismatch
  const validateExpiry = () => {
    if (expiryBasis === 'monthly') {
      const weeklyLegs = legs.filter(l => l.expiry === 'weekly');
      if (weeklyLegs.length > 0) {
        const legNumbers = weeklyLegs.map((_, i) => i + 1).join(', ');
        setValidationError(`Cannot enter on monthly expiry basis - Leg(s) ${legNumbers} have weekly expiry selected`);
        return false;
      }
    }
    setValidationError(null);
    return true;
  };

  const canRunBacktest = legs.length > 0 && !loading;

  const addLegFromDraft = () => {
    if (legs.length >= 6) return;
    setLegs(prev => [...prev, {
      id: Date.now(),
      ...draftLeg,
      target_enabled: false, target_mode: 'POINTS', target_value: 0,
      stop_loss_enabled: false, stop_loss_mode: 'POINTS', stop_loss_value: 0,
      trail_sl_enabled: false, trail_sl_mode: 'POINTS', trail_sl_trigger: 0, trail_sl_move: 0,
      re_entry_target_enabled: false, re_entry_target_mode: 'RE_ASAP', re_entry_target_count: 1,
      re_entry_sl_enabled: false, re_entry_sl_mode: 'RE_ASAP', re_entry_sl_count: 1,
      simple_momentum_enabled: false, simple_momentum_mode: 'POINTS_UP', simple_momentum_value: 0,
    }]);
    setDraftLeg(prev => ({ ...prev, strike_type: 'atm', premium_value: 0, premium_min: 0, premium_max: 0 }));
  };
  const addLeg = addLegFromDraft;

  const removeLeg = (id) => setLegs(prev => prev.filter(l => l.id !== id));
  const updateLeg = (id, field, value) => setLegs(prev => prev.map(l => l.id === id ? { ...l, [field]: value } : l));

  const buildPayload = () => {
    const legsPayload = legs.map(l => {
      const leg = {
        segment: l.segment.toUpperCase(),
        position: l.position.toUpperCase(),
        lots: l.lot || 1,
        option_type: l.option_type.toUpperCase(),
        expiry: l.expiry.toUpperCase(),
        strike_selection: {
          type: l.strike_criteria.toUpperCase(),
          strike_type: l.strike_type.toUpperCase(),
          premium: l.premium_value,
          lower: l.premium_min,
          upper: l.premium_max,
        },
      };

      // Target Profit - only send if enabled AND value is set
      if (l.target_enabled && l.target_value != null && l.target_value > 0) {
        leg.targetProfit = {
          mode: l.target_mode,
          value: l.target_value,
        };
      }

      // Stop Loss - only send if enabled AND value is set
      if (l.stop_loss_enabled && l.stop_loss_value != null && l.stop_loss_value > 0) {
        leg.stopLoss = {
          mode: l.stop_loss_mode,
          value: l.stop_loss_value,
        };
      }

      // Trail SL - only send if enabled
      if (l.trail_sl_enabled) {
        leg.trailSL = {
          mode: l.trail_sl_mode,
          trigger: l.trail_sl_trigger,
          move: l.trail_sl_move,
        };
      }

      // Re-entry on Target - only send if enabled
      if (l.re_entry_target_enabled) {
        leg.reEntryOnTarget = {
          mode: l.re_entry_target_mode,
          count: l.re_entry_target_count,
        };
      }

      // Re-entry on SL - only send if enabled
      if (l.re_entry_sl_enabled) {
        leg.reEntryOnSL = {
          mode: l.re_entry_sl_mode,
          count: l.re_entry_sl_count,
        };
      }

      // Simple Momentum - only send if enabled
      if (l.simple_momentum_enabled) {
        leg.simpleMomentum = {
          mode: l.simple_momentum_mode,
          value: l.simple_momentum_value,
        };
      }

      return leg;
    });

    return {
      index: instrument,
      underlying,
      strategy_type: strategyType,
      expiry_window: expiryBasis === 'weekly' ? 'weekly_expiry' : 'monthly_expiry',
      entry_dte: entryDaysBefore,
      exit_dte: exitDaysBefore,
      square_off_mode: squareOffMode,
      trail_sl_breakeven: trailSLBreakeven,
      trail_sl_target: trailSLTarget,
      legs: legsPayload,
      // Overall SL/TGT - send flat structure with correct field names expected by backend
      overall_sl_type: overallSLEnabled ? overallSLType : null,
      overall_sl_value: overallSLEnabled ? (overallSLValue === '' ? 0 : overallSLValue) : null,
      overall_target_type: overallTgtEnabled ? overallTgtType : null,
      overall_target_value: overallTgtEnabled ? (overallTgtValue === '' ? 0 : overallTgtValue) : null,
      date_from: startDate,
      date_to: endDate,
      expiry_type: expiryBasis.toUpperCase(),
    };
  };

  const runBacktest = useCallback(async () => {
    if (legs.length === 0) { setError('Please add at least one leg'); return; }
    if (loading) return;  // guard: ignore clicks while already running
    
    // Check for same-day expiry entry (Entry DTE = 0 and Exit DTE = 0)
    if (entryDaysBefore === 0 && exitDaysBefore === 0) {
      setError('Expiry day entry requires same-day spot data for accurate ATM/spot strike selection; results may differ when using previous close data.');
      setTimeout(() => setError(null), 1000);
      return;
    }
    
    if (expiryBasis === 'monthly') {
      const weeklyLegs = legs.filter(l => l.expiry === 'weekly');
      if (weeklyLegs.length > 0) {
        const legNumbers = weeklyLegs.map((_, i) => i + 1).join(', ');
        const msg = `Cannot enter on monthly expiry basis - Leg(s) ${legNumbers} have weekly expiry selected`;
        setError(msg);
        return;
      }
    }

    // Cancel any previous in-flight request
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true); setError(null);
    try {
      const res = await fetch('/api/dynamic-backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload()),
        signal: controller.signal,
      });
      if (res.ok) setResults(await res.json());
      else { const e = await res.json(); setError(e.detail || 'Backtest failed'); }
    } catch (err) {
      if (err.name !== 'AbortError') {
        setError('Network error. Check if backend is running.');
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  }, [legs, loading, expiryBasis, buildPayload]);

  return (
    <div className="min-h-screen bg-gray-50" style={{ fontFamily: 'Inter, sans-serif' }}>
      {/* Header */}
      <div className="bg-white border-b border-gray-200 px-6 py-3">
        <div className="max-w-screen-2xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-blue-600 rounded flex items-center justify-center">
                <span className="text-white font-bold text-sm">SL</span>
              </div>
              <span className="font-bold text-lg text-gray-800">StrategyLab</span>
            </div>
            <span className="text-xs text-gray-500 bg-gray-100 px-2 py-1 rounded">Backtest Builder</span>
          </div>
          <div className="flex items-center gap-4 text-sm">
            <span className="text-gray-600">{instrument}</span>
            <span className="text-gray-400">•</span>
            <span className="text-gray-600">{legs.length} Legs Active</span>
            <button className="flex items-center gap-1.5 px-3 py-1.5 text-blue-600 hover:bg-blue-50 rounded text-sm font-medium transition-colors">
              <Save size={14} />
              Save
            </button>
          </div>
        </div>
      </div>

      <div className="max-w-screen-2xl mx-auto px-6 py-4">
        <div className="grid grid-cols-12 gap-4">

          {/* LEFT COLUMN - Configuration */}
          <div className="col-span-5 space-y-3">
            {/* Configuration Card */}
            <div className="bg-white rounded-lg border border-gray-200 shadow-sm">
              <div className="px-4 py-3 border-b border-gray-100">
                <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Configuration</h3>
              </div>
              <div className="p-4 space-y-4">
                {/* Strategy Type */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-2">Strategy</label>
                  <SegBtn
                    options={[
                      { value: 'intraday', label: 'Intraday' },
                      { value: 'btst', label: 'BTST' },
                      { value: 'positional', label: 'Positional' },
                    ]}
                    value={strategyType}
                    onChange={setStrategyType}
                  />
                </div>

                {/* Instrument */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-2">Instrument</label>
                  <select
                    value={instrument}
                    onChange={e => setInstrument(e.target.value)}
                    className="w-full h-9 px-3 border border-gray-300 rounded text-sm bg-white"
                  >
                    <option>NIFTY</option>
                    <option>BANKNIFTY</option>
                    <option>FINNIFTY</option>
                    <option>MIDCPNIFTY</option>
                    <option>SENSEX</option>
                  </select>
                </div>

                {/* Underlying */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-2">Underlying</label>
                  <SegBtn
                    options={[{ value: 'cash', label: 'Cash' }, { value: 'futures', label: 'Futures' }]}
                    value={underlying}
                    onChange={setUnderlying}
                  />
                </div>

                {/* Expiry Basis */}
                {strategyType === 'positional' && (
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-2">Expires on</label>
                    <select
                      value={expiryBasis}
                      onChange={e => setExpiryBasis(e.target.value)}
                      className="w-full h-9 px-3 border border-gray-300 rounded text-sm bg-white"
                    >
                      <option value="weekly">Weekly Expiry</option>
                      <option value="monthly">Monthly Expiry</option>
                    </select>
                  </div>
                )}

                {/* Entry/Exit Days */}
                {strategyType === 'positional' && (
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-2">Entry (days before expiry)</label>
                      <select
                        value={entryDaysBefore}
                        onChange={e => setEntryDaysBefore(+e.target.value)}
                        className="w-full h-9 px-3 border border-gray-300 rounded text-sm bg-white"
                      >
                        {daysOptions.map(d => <option key={d}>{d}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-2">Exit (days before expiry)</label>
                      <select
                        value={exitDaysBefore}
                        onChange={e => setExitDaysBefore(+e.target.value)}
                        className="w-full h-9 px-3 border border-gray-300 rounded text-sm bg-white"
                      >
                        {daysOptions.map(d => <option key={d}>{d}</option>)}
                      </select>
                    </div>
                  </div>
                )}

                {/* Delay Restart */}
                <div className="flex items-center justify-between py-2">
                  <span className="text-xs text-gray-600">Delay Restart</span>
                  <Toggle enabled={delayRestart} onToggle={() => setDelayRestart(v => !v)} size="sm" />
                </div>

                {/* Overall Momentum */}
                <div className="flex items-center justify-between py-2">
                  <div className="flex items-center gap-1">
                    <span className="text-xs text-gray-600">Overall Momentum</span>
                    <Tooltip text="Entry only when market momentum matches the selected direction and threshold." />
                  </div>
                  <Toggle enabled={overallMomentum} onToggle={() => setOverallMomentum(v => !v)} size="sm" />
                </div>
              </div>
            </div>

            {/* Legwise Controls Card */}
            <div className="bg-white rounded-lg border border-gray-200 shadow-sm">
              <div className="px-4 py-3 border-b border-gray-100">
                <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Legwise Controls</h3>
              </div>
              <div className="p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-gray-600">Square Off Mode</span>
                  <SegBtn
                    options={[{ value: 'partial', label: 'Partial' }, { value: 'complete', label: 'Complete' }]}
                    value={squareOffMode}
                    onChange={setSquareOffMode}
                    size="sm"
                  />
                </div>

                <div className="flex items-center gap-2 py-2">
                  <input
                    type="checkbox"
                    id="trailSL"
                    checked={trailSLBreakeven}
                    onChange={e => setTrailSLBreakeven(e.target.checked)}
                    className="h-3.5 w-3.5 rounded border-gray-300 accent-blue-600"
                  />
                  <label htmlFor="trailSL" className="text-xs text-gray-600 cursor-pointer flex-1">
                    Trail Stop Loss to Break-even
                  </label>
                  <SegBtn
                    options={[{ value: 'all_legs', label: 'All Legs' }, { value: 'sl_legs', label: 'SL Legs' }]}
                    value={trailSLTarget}
                    onChange={setTrailSLTarget}
                    size="sm"
                  />
                </div>
              </div>
            </div>

            {/* Overall Settings Card */}
            <div className="bg-white rounded-lg border border-gray-200 shadow-sm">
              <div className="px-4 py-3 border-b border-gray-100">
                <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Overall Settings</h3>
              </div>
              <div className="p-4 space-y-4">
                {/* Overall Stop Loss */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium text-gray-600">Overall Stop Loss</span>
                    <Toggle enabled={overallSLEnabled} onToggle={() => setOverallSLEnabled(v => !v)} size="sm" />
                  </div>
                  {overallSLEnabled && (
                    <div className="flex gap-2">
                      <select
                        value={overallSLType}
                        onChange={e => setOverallSLType(e.target.value)}
                        className="flex-1 h-8 px-2 border border-gray-300 rounded text-xs bg-white"
                      >
                        <option value="max_loss">Max Loss </option>
                        <option value="total_premium_pct"> Total Premium %</option>
                      </select>
                      <input
                        type="number"
                        value={overallSLValue}
                        onChange={e => setOverallSLValue(e.target.value === '' ? '' : +e.target.value)}
                        className="w-20 h-8 px-2 border border-gray-300 rounded text-xs text-center"
                        placeholder={overallSLType === 'max_loss' ? '₹' : '%'}
                      />
                    </div>
                  )}
                </div>

                {/* Overall Target */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium text-gray-600">Overall Target</span>
                    <Toggle enabled={overallTgtEnabled} onToggle={() => setOverallTgtEnabled(v => !v)} size="sm" />
                  </div>
                  {overallTgtEnabled && (
                    <div className="flex gap-2">
                      <select
                        value={overallTgtType}
                        onChange={e => setOverallTgtType(e.target.value)}
                        className="flex-1 h-8 px-2 border border-gray-300 rounded text-xs bg-white"
                      >
                        <option value="max_profit">Max Profit</option>
                        <option value="total_premium_pct">% of Premium</option>
                      </select>
                      <input
                        type="number"
                        value={overallTgtValue}
                        onChange={e => setOverallTgtValue(e.target.value === '' ? '' : +e.target.value)}
                        className="w-20 h-8 px-2 border border-gray-300 rounded text-xs text-center"
                        placeholder={overallTgtType === 'max_profit' ? '₹' : '%'}
                      />
                    </div>
                  )}
                </div>

                {/* Trailing */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium text-gray-600">Trailing</span>
                    <Toggle enabled={trailingEnabled} onToggle={() => setTrailingEnabled(v => !v)} size="sm" />
                  </div>
                  {trailingEnabled && (
                    <div className="space-y-2">
                      <select
                        value={trailingType}
                        onChange={e => setTrailingType(e.target.value)}
                        className="w-full h-8 px-2 border border-gray-300 rounded text-xs bg-white"
                      >
                        <option value="lock">Lock</option>
                        <option value="lock_and_trail">Lock & Trail</option>
                      </select>
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">If profit reaches</label>
                          <input
                            type="number"
                            value={trailingIfProfit}
                            onChange={e => setTrailingIfProfit(+e.target.value)}
                            className="w-full h-8 px-2 border border-gray-300 rounded text-xs text-center"
                          />
                        </div>
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Lock profit at</label>
                          <input
                            type="number"
                            value={trailingLockProfit}
                            onChange={e => setTrailingLockProfit(+e.target.value)}
                            className="w-full h-8 px-2 border border-gray-300 rounded text-xs text-center"
                          />
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* RIGHT COLUMN - Leg Builder (AlgoTest style) */}
          <div className="col-span-7 space-y-3">

            {/* ── Top configurator panel ── */}
            <div className="bg-white rounded-lg border border-gray-200 shadow-sm">
              <div className="px-4 py-2.5 border-b border-gray-100 flex items-center gap-2">
                <h3 className="text-xs font-bold text-gray-700 uppercase tracking-wide">Leg Builder</h3>
                <Tooltip text="Configure your leg settings then click Add Leg." />
              </div>
              <div className="px-4 py-3 flex flex-wrap items-end gap-3">

                {/* Segment */}
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Select segments</label>
                  <SegBtn
                    options={[{ value: 'futures', label: 'Futures' }, { value: 'options', label: 'Options' }]}
                    value={draftLeg.segment}
                    onChange={v => setDraftLeg(prev => ({ ...prev, segment: v }))}
                  />
                </div>

                {/* Total Lot */}
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Total Lot</label>
                  <input type="number" min={1} value={draftLeg.lot}
                    onChange={e => setDraftLeg(prev => ({ ...prev, lot: Math.max(1, parseInt(e.target.value) || 1) }))}
                    className="w-16 h-8 px-2 border border-gray-300 rounded text-xs text-center bg-white focus:outline-none focus:ring-2 focus:ring-blue-400"
                  />
                </div>

                {/* Position */}
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Position</label>
                  <SegBtn
                    options={[{ value: 'buy', label: 'Buy' }, { value: 'sell', label: 'Sell' }]}
                    value={draftLeg.position}
                    onChange={v => setDraftLeg(prev => ({ ...prev, position: v }))}
                  />
                </div>

                {/* Option Type */}
                {draftLeg.segment === 'options' && (
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Option Type</label>
                    <SegBtn
                      options={[{ value: 'call', label: 'Call' }, { value: 'put', label: 'Put' }]}
                      value={draftLeg.option_type}
                      onChange={v => setDraftLeg(prev => ({ ...prev, option_type: v }))}
                    />
                  </div>
                )}

                {/* Expiry */}
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Expiry</label>
                  <select value={draftLeg.expiry}
                    onChange={e => setDraftLeg(prev => ({ ...prev, expiry: e.target.value }))}
                    className="h-8 px-2 border border-gray-300 rounded text-xs bg-white focus:outline-none focus:ring-2 focus:ring-blue-400 w-36">
                    {draftLeg.segment === 'options' ? (
                      <>
                        <option value="weekly">Weekly</option>
                        <option value="next_weekly">Next Weekly</option>
                        <option value="monthly">Monthly</option>
                        <option value="next_monthly">Next Monthly</option>
                      </>
                    ) : (
                      <>
                        <option value="monthly">Monthly</option>
                        <option value="next_monthly">Next Monthly</option>
                      </>
                    )}
                  </select>
                </div>

                {/* Strike Criteria */}
                {draftLeg.segment === 'options' && (
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Strike Criteria</label>
                    <select value={draftLeg.strike_criteria}
                      onChange={e => setDraftLeg(prev => ({ ...prev, strike_criteria: e.target.value }))}
                      className="h-8 px-2 border border-gray-300 rounded text-xs bg-white focus:outline-none focus:ring-2 focus:ring-blue-400 w-44">
                      <option value="strike_type">Strike Type</option>
                      <option value="premium_range">Premium Range</option>
                      <option value="closest_premium">Closest Premium</option>
                      <option value="premium_gte">Premium &gt;=</option>
                      <option value="premium_lte">Premium &lt;=</option>
                      <option value="straddle_width">Straddle Width</option>
                      <option value="pct_of_atm">% of ATM</option>
                      <option value="synthetic_future">Synthetic Future</option>
                      <option value="atm_straddle_prem_pct">ATM Straddle Premium %</option>
                      <option value="closest_delta">Closest Delta</option>
                      <option value="delta_range">Delta Range</option>
                    </select>
                  </div>
                )}

                {/* Strike Type / Premium */}
                {draftLeg.segment === 'options' && (
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Strike Type</label>
                    {draftLeg.strike_criteria === 'strike_type' ? (
                      <select value={draftLeg.strike_type}
                        onChange={e => setDraftLeg(prev => ({ ...prev, strike_type: e.target.value }))}
                        className="h-8 px-2 border border-gray-300 rounded text-xs bg-white focus:outline-none focus:ring-2 focus:ring-blue-400 w-28">
                        {strikeTypeOpts.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                    ) : draftLeg.strike_criteria === 'premium_range' ? (
                      <div className="flex gap-1">
                        <input type="number" min={0} placeholder="Min" value={draftLeg.premium_min || ''}
                          onChange={e => setDraftLeg(prev => ({ ...prev, premium_min: +e.target.value }))}
                          className="w-20 h-8 px-2 border border-gray-300 rounded text-xs text-center" />
                        <input type="number" min={0} placeholder="Max" value={draftLeg.premium_max || ''}
                          onChange={e => setDraftLeg(prev => ({ ...prev, premium_max: +e.target.value }))}
                          className="w-20 h-8 px-2 border border-gray-300 rounded text-xs text-center" />
                      </div>
                    ) : (
                      <input type="number" min={0} placeholder="Value" value={draftLeg.premium_value || ''}
                        onChange={e => setDraftLeg(prev => ({ ...prev, premium_value: +e.target.value }))}
                        className="w-24 h-8 px-2 border border-gray-300 rounded text-xs text-center" />
                    )}
                  </div>
                )}

                {/* Add Leg */}
                <div className="ml-auto">
                  <button type="button" onClick={addLegFromDraft} disabled={legs.length >= 6}
                    className="h-9 px-6 bg-blue-700 hover:bg-blue-800 disabled:opacity-40 text-white text-sm font-semibold rounded transition-colors shadow-sm">
                    Add Leg
                  </button>
                </div>
              </div>
            </div>

            {/* ── Added legs list ── */}
            {legs.length > 0 && (
              <div className="bg-white rounded-lg border border-gray-200 shadow-sm">
                <div className="px-4 py-2.5 border-b border-gray-100">
                  <h3 className="text-xs font-bold text-gray-700 uppercase tracking-wide">Legs <span className="font-normal text-gray-400 ml-1">({legs.length}/6)</span></h3>
                </div>
                <div className="p-3 space-y-3">
                  {legs.map((leg, idx) => (
                    <div key={leg.id} className="border border-gray-200 rounded-lg overflow-hidden">
                      <div className="bg-gradient-to-r from-blue-50 to-blue-100 px-3 py-2 flex items-center justify-between border-b border-blue-200">
                        <span className="text-xs font-bold text-blue-900">
                          Leg {idx + 1} | {leg.segment === 'options' ? `${leg.position.toUpperCase()} ${leg.option_type.toUpperCase()}` : 'FUTURE'} | {leg.expiry}
                        </span>
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-blue-600 font-medium">{leg.lot * getLotSize(instrument, startDate)} units</span>
                          <button onClick={() => removeLeg(leg.id)} className="p-1 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded transition-colors">
                            <Trash2 size={13} />
                          </button>
                        </div>
                      </div>

                      <div className="p-3 space-y-3">
                        {/* Basic fields */}
                        <div className="flex flex-wrap items-end gap-3">
                          <div>
                            <label className="block text-xs text-gray-500 mb-1">Segment</label>
                            <SegBtn size="sm"
                              options={[{ value: 'options', label: 'Options' }, { value: 'futures', label: 'Futures' }]}
                              value={leg.segment} onChange={v => updateLeg(leg.id, 'segment', v)} />
                          </div>
                          <div>
                            <label className="block text-xs text-gray-500 mb-1">Lots</label>
                            <input type="number" min={1} value={leg.lot}
                              onChange={e => updateLeg(leg.id, 'lot', parseInt(e.target.value) || 1)}
                              className="w-16 h-7 px-2 border border-gray-300 rounded text-xs text-center bg-white" />
                          </div>
                          <div>
                            <label className="block text-xs text-gray-500 mb-1">Position</label>
                            <SegBtn size="sm"
                              options={[{ value: 'buy', label: 'Buy' }, { value: 'sell', label: 'Sell' }]}
                              value={leg.position} onChange={v => updateLeg(leg.id, 'position', v)} />
                          </div>
                          {leg.segment === 'options' && (
                            <div>
                              <label className="block text-xs text-gray-500 mb-1">Option Type</label>
                              <SegBtn size="sm"
                                options={[{ value: 'call', label: 'Call' }, { value: 'put', label: 'Put' }]}
                                value={leg.option_type} onChange={v => updateLeg(leg.id, 'option_type', v)} />
                            </div>
                          )}
                          <div>
                            <label className="block text-xs text-gray-500 mb-1">Expiry</label>
                            <select value={leg.expiry} onChange={e => updateLeg(leg.id, 'expiry', e.target.value)}
                              className="h-7 px-2 border border-gray-300 rounded text-xs bg-white w-28">
                              {leg.segment === 'options' ? (
                                <>
                                  <option value="weekly">Weekly</option>
                                  <option value="next_weekly">Next Weekly</option>
                                  <option value="monthly">Monthly</option>
                                  <option value="next_monthly">Next Monthly</option>
                                </>
                              ) : (
                                <>
                                  <option value="monthly">Monthly</option>
                                  <option value="next_monthly">Next Monthly</option>
                                </>
                              )}
                            </select>
                          </div>
                          {leg.segment === 'options' && (
                            <>
                              <div>
                                <label className="block text-xs text-gray-500 mb-1">Strike Criteria</label>
                                <select value={leg.strike_criteria} onChange={e => updateLeg(leg.id, 'strike_criteria', e.target.value)}
                                  className="h-7 px-2 border border-gray-300 rounded text-xs bg-white w-36">
                                  <option value="strike_type">Strike Type</option>
                                  <option value="premium_range">Premium Range</option>
                                  <option value="closest_premium">Closest Premium</option>
                                  <option value="premium_gte">Premium &gt;=</option>
                                  <option value="premium_lte">Premium &lt;=</option>
                                  <option value="straddle_width">Straddle Width</option>
                                  <option value="pct_of_atm">% of ATM</option>
                                  <option value="synthetic_future">Synthetic Future</option>
                                  <option value="atm_straddle_prem_pct">ATM Straddle Premium %</option>
                                  <option value="closest_delta">Closest Delta</option>
                                  <option value="delta_range">Delta Range</option>
                                </select>
                              </div>
                              <div>
                                <label className="block text-xs text-gray-500 mb-1">Strike Type</label>
                                {leg.strike_criteria === 'strike_type' ? (
                                  <select value={leg.strike_type} onChange={e => updateLeg(leg.id, 'strike_type', e.target.value)}
                                    className="h-7 px-2 border border-gray-300 rounded text-xs bg-white w-24">
                                    {strikeTypeOpts.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                                  </select>
                                ) : leg.strike_criteria === 'premium_range' ? (
                                  <div className="flex gap-1">
                                    <input type="number" min={0} placeholder="Min" value={leg.premium_min || ''}
                                      onChange={e => updateLeg(leg.id, 'premium_min', +e.target.value)}
                                      className="w-16 h-7 px-1 border border-gray-300 rounded text-xs text-center" />
                                    <input type="number" min={0} placeholder="Max" value={leg.premium_max || ''}
                                      onChange={e => updateLeg(leg.id, 'premium_max', +e.target.value)}
                                      className="w-16 h-7 px-1 border border-gray-300 rounded text-xs text-center" />
                                  </div>
                                ) : (
                                  <input type="number" min={0} placeholder="Value" value={leg.premium_value || ''}
                                    onChange={e => updateLeg(leg.id, 'premium_value', +e.target.value)}
                                    className="w-20 h-7 px-1 border border-gray-300 rounded text-xs text-center" />
                                )}
                              </div>
                            </>
                          )}
                        </div>

                        {/* Advanced controls */}
                        <div className="pt-2 border-t border-gray-100 space-y-2">
                          <div className="flex flex-wrap gap-x-4 gap-y-2">
                            <div className="flex items-center gap-2">
                              <Toggle enabled={leg.target_enabled} onToggle={() => updateLeg(leg.id, 'target_enabled', !leg.target_enabled)} size="sm" />
                              <span className="text-xs font-medium text-gray-600 whitespace-nowrap">Target Profit</span>
                              {leg.target_enabled && (<>
                                <select value={leg.target_mode} onChange={e => updateLeg(leg.id, 'target_mode', e.target.value)} className="h-6 px-1 border border-gray-300 rounded text-xs bg-white">
                                  <option value="POINTS">Points (Pts)</option>
                                  <option value="UNDERLYING_POINTS">Underlying Pts</option>
                                  <option value="PERCENT">Percent (%)</option>
                                  <option value="UNDERLYING_PERCENT">Underlying %</option>
                                </select>
                                <input type="number" min={0} value={leg.target_value ?? ''} onChange={e => updateLeg(leg.id, 'target_value', e.target.value === '' ? null : +e.target.value)} className="w-14 h-6 px-1 border border-gray-300 rounded text-xs text-center" />
                              </>)}
                            </div>
                            <div className="flex items-center gap-2">
                              <Toggle enabled={leg.stop_loss_enabled} onToggle={() => updateLeg(leg.id, 'stop_loss_enabled', !leg.stop_loss_enabled)} size="sm" />
                              <span className="text-xs font-medium text-gray-600 whitespace-nowrap">Stop Loss</span>
                              {leg.stop_loss_enabled && (<>
                                <select value={leg.stop_loss_mode} onChange={e => updateLeg(leg.id, 'stop_loss_mode', e.target.value)} className="h-6 px-1 border border-gray-300 rounded text-xs bg-white">
                                  <option value="POINTS">Points (Pts)</option>
                                  <option value="UNDERLYING_POINTS">Underlying Pts</option>
                                  <option value="PERCENT">Percent (%)</option>
                                  <option value="UNDERLYING_PERCENT">Underlying %</option>
                                </select>
                                <input type="number" min={0} value={leg.stop_loss_value ?? ''} onChange={e => updateLeg(leg.id, 'stop_loss_value', e.target.value === '' ? null : +e.target.value)} className="w-14 h-6 px-1 border border-gray-300 rounded text-xs text-center" />
                              </>)}
                            </div>
                            <div className="flex items-center gap-2">
                              <Toggle enabled={leg.trail_sl_enabled} onToggle={() => updateLeg(leg.id, 'trail_sl_enabled', !leg.trail_sl_enabled)} size="sm" />
                              <span className="text-xs font-medium text-gray-600 whitespace-nowrap">Trail SL</span>
                              <Tooltip text="For every X profit, trail SL by Y." />
                              {leg.trail_sl_enabled && (<>
                                <select value={leg.trail_sl_mode} onChange={e => updateLeg(leg.id, 'trail_sl_mode', e.target.value)} className="w-16 h-6 px-1 border border-gray-300 rounded text-xs bg-white">
                                  <option value="POINTS">Points</option>
                                  <option value="PERCENT">Percent</option>
                                </select>
                                <input type="number" min={0} placeholder="X" value={leg.trail_sl_trigger ?? ''} onChange={e => updateLeg(leg.id, 'trail_sl_trigger', e.target.value === '' ? null : +e.target.value)} className="w-12 h-6 px-1 border border-gray-300 rounded text-xs text-center" />
                                <input type="number" min={0} placeholder="Y" value={leg.trail_sl_move ?? ''} onChange={e => updateLeg(leg.id, 'trail_sl_move', e.target.value === '' ? null : +e.target.value)} className="w-12 h-6 px-1 border border-gray-300 rounded text-xs text-center" />
                              </>)}
                            </div>
                          </div>
                          <div className="flex flex-wrap gap-x-4 gap-y-2">
                            <div className="flex items-center gap-2">
                              <Toggle enabled={leg.re_entry_target_enabled} onToggle={() => updateLeg(leg.id, 're_entry_target_enabled', !leg.re_entry_target_enabled)} size="sm" />
                              <span className="text-xs font-medium text-gray-600 whitespace-nowrap">Re-entry on Tgt</span>
                              {leg.re_entry_target_enabled && (<>
                                <select value={leg.re_entry_target_mode} onChange={e => updateLeg(leg.id, 're_entry_target_mode', e.target.value)} className="h-6 px-1 border border-gray-300 rounded text-xs bg-white">
                                  <option value="RE_ASAP">RE ASAP</option>
                                  <option value="RE_ASAP_REV">RE ASAP &#8629;</option>
                                  <option value="RE_MOMENTUM">RE MOMENTUM</option>
                                  <option value="RE_MOMENTUM_REV">RE MOMENTUM &#8629;</option>
                                  <option value="RE_COST">RE COST</option>
                                  <option value="RE_COST_REV">RE COST &#8629;</option>
                                  <option value="LAZY_LEG">Lazy Leg</option>
                                </select>
                                <select value={leg.re_entry_target_count} onChange={e => updateLeg(leg.id, 're_entry_target_count', +e.target.value)} className="w-10 h-6 px-1 border border-gray-300 rounded text-xs bg-white">
                                  {[1,2,3,4,5].map(n => <option key={n} value={n}>{n}</option>)}
                                </select>
                              </>)}
                            </div>
                            <div className="flex items-center gap-2">
                              <Toggle enabled={leg.re_entry_sl_enabled} onToggle={() => updateLeg(leg.id, 're_entry_sl_enabled', !leg.re_entry_sl_enabled)} size="sm" />
                              <span className="text-xs font-medium text-gray-600 whitespace-nowrap">Re-entry on SL</span>
                              {leg.re_entry_sl_enabled && (<>
                                <select value={leg.re_entry_sl_mode} onChange={e => updateLeg(leg.id, 're_entry_sl_mode', e.target.value)} className="h-6 px-1 border border-gray-300 rounded text-xs bg-white">
                                  <option value="RE_ASAP">RE ASAP</option>
                                  <option value="RE_ASAP_REV">RE ASAP &#8629;</option>
                                  <option value="RE_MOMENTUM">RE MOMENTUM</option>
                                  <option value="RE_MOMENTUM_REV">RE MOMENTUM &#8629;</option>
                                  <option value="RE_COST">RE COST</option>
                                  <option value="RE_COST_REV">RE COST &#8629;</option>
                                  <option value="LAZY_LEG">Lazy Leg</option>
                                </select>
                                <select value={leg.re_entry_sl_count} onChange={e => updateLeg(leg.id, 're_entry_sl_count', +e.target.value)} className="w-10 h-6 px-1 border border-gray-300 rounded text-xs bg-white">
                                  {[1,2,3,4,5].map(n => <option key={n} value={n}>{n}</option>)}
                                </select>
                              </>)}
                            </div>
                            <div className="flex items-center gap-2">
                              <Toggle enabled={leg.simple_momentum_enabled} onToggle={() => updateLeg(leg.id, 'simple_momentum_enabled', !leg.simple_momentum_enabled)} size="sm" />
                              <span className="text-xs font-medium text-gray-600 whitespace-nowrap">Simple Momentum</span>
                              {leg.simple_momentum_enabled && (<>
                                <select value={leg.simple_momentum_mode} onChange={e => updateLeg(leg.id, 'simple_momentum_mode', e.target.value)} className="h-6 px-1 border border-gray-300 rounded text-xs bg-white">
                                  <option value="POINTS_UP">Points (Pts) &#8593;</option>
                                  <option value="POINTS_DOWN">Points (Pts) &#8595;</option>
                                  <option value="PERCENT_UP">Percent (%) &#8593;</option>
                                  <option value="PERCENT_DOWN">Percent (%) &#8595;</option>
                                  <option value="UNDERLYING_POINTS_UP">Underlying Pts &#8593;</option>
                                  <option value="UNDERLYING_POINTS_DOWN">Underlying Pts &#8595;</option>
                                  <option value="UNDERLYING_PERCENT_UP">Underlying % &#8593;</option>
                                  <option value="UNDERLYING_PERCENT_DOWN">Underlying % &#8595;</option>
                                </select>
                                <input type="number" min={0} value={leg.simple_momentum_value ?? ''} onChange={e => updateLeg(leg.id, 'simple_momentum_value', e.target.value === '' ? null : +e.target.value)} className="w-14 h-6 px-1 border border-gray-300 rounded text-xs text-center" />
                              </>)}
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Date Range Bar */}
        <div className="mt-4 bg-white rounded-lg border border-gray-200 shadow-sm px-5 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <label className="text-xs text-gray-600">Start Date</label>
                <input
                  type="date"
                  value={startDate}
                  onChange={e => setStartDate(e.target.value)}
                  className="h-8 px-2 border border-gray-300 rounded text-xs"
                />
              </div>
              <div className="flex items-center gap-2">
                <label className="text-xs text-gray-600">End Date</label>
                <input
                  type="date"
                  value={endDate}
                  onChange={e => setEndDate(e.target.value)}
                  className="h-8 px-2 border border-gray-300 rounded text-xs"
                />
              </div>
            </div>
          </div>
        </div>

        {/* Results */}
        {results && (
          <div className="mt-4">
            <ResultsPanel results={results} onClose={() => setResults(null)} showCloseButton={false} />
          </div>
        )}

        {/* Error Alert - Above Button */}
        {error && (
          <div className="fixed bottom-24 left-1/2 transform -translate-x-1/2 z-50 flex items-center gap-2 px-4 py-2 bg-red-50 border border-red-200 rounded-lg shadow-lg">
            <AlertTriangle size={16} className="text-red-600 flex-shrink-0" />
            <span className="text-sm text-red-700">{error}</span>
          </div>
        )}

        {/* Run Backtest Button - Bottom */}
        <div className="fixed bottom-6 left-1/2 transform -translate-x-1/2 z-50">
          <button
            onClick={runBacktest}
            disabled={!canRunBacktest}
            className="flex items-center gap-2 px-8 py-3 bg-green-600 hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-full text-base font-semibold shadow-lg transition-all hover:shadow-xl"
          >
            {loading ? (
              <><div className="h-5 w-5 border-2 border-white border-t-transparent rounded-full animate-spin" />Running Backtest...</>
            ) : (
              <><Play size={18} />Run Backtest</>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};

export default AlgoTestBacktest;