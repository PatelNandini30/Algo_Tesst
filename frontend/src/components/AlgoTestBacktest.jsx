import React, { useState, useEffect } from 'react';
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

  const daysOptions = expiryBasis === 'weekly' ? [0, 1, 2, 3, 4] : Array.from({ length: 25 }, (_, i) => i);

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

  // Run validation when expiryBasis or legs change
  useEffect(() => {
    validateExpiry();
  }, [expiryBasis, legs]);

  const canRunBacktest = !validationError && legs.length > 0 && !loading;

  const addLeg = () => {
    if (legs.length >= 6) return;
    setLegs(prev => [...prev, {
      id: Date.now(),
      segment: 'options',
      lot: 1,
      position: 'sell',
      option_type: 'call',
      expiry: 'weekly',
      strike_criteria: 'strike_type',
      strike_type: 'atm',
      premium_value: 0,
      premium_min: 0,
      premium_max: 0,
      stop_loss_enabled: false,
      stop_loss: null,
      stop_loss_type: 'pct',
      target_enabled: false,
      target: null,
      target_type: 'pct',
    }]);
  };

  const removeLeg = (id) => setLegs(prev => prev.filter(l => l.id !== id));
  const updateLeg = (id, field, value) => setLegs(prev => prev.map(l => l.id === id ? { ...l, [field]: value } : l));

  const buildPayload = () => ({
    index: instrument,
    underlying,
    strategy_type: strategyType,
    expiry_window: expiryBasis === 'weekly' ? 'weekly_expiry' : 'monthly_expiry',
    entry_dte: entryDaysBefore,
    exit_dte: exitDaysBefore,
    square_off_mode: squareOffMode,
    trail_sl_breakeven: trailSLBreakeven,
    trail_sl_target: trailSLTarget,
    legs: legs.map(l => ({
      ...l,
      lot: l.lot || 1,
      strike_selection: {
        type: l.strike_criteria,
        strike_type: l.strike_type,
        premium: l.premium_value,
        lower: l.premium_min,
        upper: l.premium_max,
      },
    })),
    overall_settings: {
      stop_loss: overallSLEnabled ? overallSLValue : null,
      stop_loss_type: overallSLType,
      target: overallTgtEnabled ? overallTgtValue : null,
      target_type: overallTgtType,
    },
    date_from: startDate,
    date_to: endDate,
    expiry_type: expiryBasis.toUpperCase(),
  });

  const runBacktest = async () => {
    if (legs.length === 0) { setError('Please add at least one leg'); return; }
    if (!validateExpiry()) { setError(validationError); return; }
    setLoading(true); setError(null);
    try {
      const res = await fetch('/api/dynamic-backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload()),
      });
      if (res.ok) setResults(await res.json());
      else { const e = await res.json(); setError(e.detail || 'Backtest failed'); }
    } catch {
      setError('Network error. Check if backend is running.');
    } finally {
      setLoading(false);
    }
  };

  const strikeTypeOpts = [
    ...Array.from({ length: 20 }, (_, i) => ({ value: `itm${20 - i}`, label: `ITM ${20 - i}` })),
    { value: 'atm', label: 'ATM' },
    ...Array.from({ length: 20 }, (_, i) => ({ value: `otm${i + 1}`, label: `OTM ${i + 1}` })),
  ];

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
            <button
              onClick={runBacktest}
              disabled={!canRunBacktest}
              className="flex items-center gap-1.5 px-4 py-1.5 bg-green-600 hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded text-sm font-medium transition-colors"
            >
              {loading ? (
                <><div className="h-3.5 w-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />Running</>
              ) : (
                <><Play size={14} />Run Backtest</>
              )}
            </button>
          </div>
        </div>

        {/* Validation Error Alert */}
        {validationError && (
          <div className="mx-6 mt-2 flex items-center gap-2 px-4 py-2 bg-red-50 border border-red-200 rounded-lg">
            <AlertTriangle size={16} className="text-red-600 flex-shrink-0" />
            <span className="text-sm text-red-700">{validationError}</span>
          </div>
        )}
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
                        <option value="max_loss">Max Loss</option>
                        <option value="pct">%</option>
                        <option value="points">Points</option>
                      </select>
                      <input
                        type="number"
                        value={overallSLValue}
                        onChange={e => setOverallSLValue(+e.target.value)}
                        className="w-20 h-8 px-2 border border-gray-300 rounded text-xs text-center"
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
                        <option value="pct">%</option>
                        <option value="points">Points</option>
                      </select>
                      <input
                        type="number"
                        value={overallTgtValue}
                        onChange={e => setOverallTgtValue(+e.target.value)}
                        className="w-20 h-8 px-2 border border-gray-300 rounded text-xs text-center"
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

          {/* RIGHT COLUMN - Leg Builder */}
          <div className="col-span-7">
            <div className="bg-white rounded-lg border border-gray-200 shadow-sm">
              <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Leg Builder</h3>
                  <Tooltip text="Build your strategy by adding individual option or futures legs." />
                </div>
                <button
                  onClick={addLeg}
                  disabled={legs.length >= 6}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-40 text-white rounded text-xs font-medium transition-colors"
                >
                  <Plus size={13} />
                  Add Leg
                </button>
              </div>

              <div className="p-4">
                {legs.length === 0 ? (
                  <div className="text-center py-12 border-2 border-dashed border-gray-200 rounded-lg">
                    <p className="text-sm text-gray-400 mb-3">No legs added yet</p>
                    <button
                      onClick={addLeg}
                      className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm font-medium transition-colors"
                    >
                      <Plus size={14} />
                      Add Your First Leg
                    </button>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {legs.map((leg, idx) => (
                      <div key={leg.id} className="border border-gray-200 rounded-lg overflow-hidden">
                        {/* Leg Header */}
                        <div className="bg-gradient-to-r from-blue-50 to-blue-100 px-3 py-2 flex items-center justify-between border-b border-blue-200">
                          <span className="text-xs font-bold text-blue-900">
                            Leg {idx + 1} | {leg.segment === 'options' ? `${leg.position.toUpperCase()} ${leg.option_type.toUpperCase()}` : 'FUTURE'} | {leg.expiry}
                          </span>
                          <button
                            onClick={() => removeLeg(leg.id)}
                            className="p-1 text-red-500 hover:text-red-700 hover:bg-red-50 rounded transition-colors"
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>

                        {/* Leg Content */}
                        <div className="p-3 space-y-3">
                          {/* Row 1: Segment, Position, Lots */}
                          <div className="grid grid-cols-3 gap-3">
                            <div>
                              <label className="block text-xs text-gray-500 mb-1">Segment</label>
                              <SegBtn
                                options={[{ value: 'options', label: 'Options' }, { value: 'futures', label: 'Futures' }]}
                                value={leg.segment}
                                onChange={v => updateLeg(leg.id, 'segment', v)}
                                size="sm"
                              />
                            </div>
                            <div>
                              <label className="block text-xs text-gray-500 mb-1">Position</label>
                              <SegBtn
                                options={[{ value: 'buy', label: 'Buy' }, { value: 'sell', label: 'Sell' }]}
                                value={leg.position}
                                onChange={v => updateLeg(leg.id, 'position', v)}
                                size="sm"
                              />
                            </div>
                            <div>
                              <label className="block text-xs text-gray-500 mb-1">Lots</label>
                              <input
                                type="number"
                                min={1}
                                value={leg.lot}
                                onChange={e => updateLeg(leg.id, 'lot', parseInt(e.target.value) || 1)}
                                className="w-full h-7 px-2 border border-gray-300 rounded text-xs text-center bg-white"
                              />
                            </div>
                          </div>

                          {leg.segment === 'options' && (
                            <>
                              {/* Row 2: Option Type, Expiry */}
                              <div className="grid grid-cols-2 gap-3">
                                <div>
                                  <label className="block text-xs text-gray-500 mb-1">Option Type</label>
                                  <SegBtn
                                    options={[{ value: 'call', label: 'Call' }, { value: 'put', label: 'Put' }]}
                                    value={leg.option_type}
                                    onChange={v => updateLeg(leg.id, 'option_type', v)}
                                    size="sm"
                                  />
                                </div>
                                <div>
                                  <label className="block text-xs text-gray-500 mb-1">Expiry</label>
                                  <select
                                    value={leg.expiry}
                                    onChange={e => updateLeg(leg.id, 'expiry', e.target.value)}
                                    className="w-full h-7 px-2 border border-gray-300 rounded text-xs bg-white"
                                  >
                                    <option value="weekly">Weekly</option>
                                    <option value="next_weekly">Next Weekly</option>
                                    <option value="monthly">Monthly</option>
                                    <option value="next_monthly">Next Monthly</option>
                                  </select>
                                </div>
                              </div>

                              {/* Row 3: Strike Criteria */}
                              <div>
                                <label className="block text-xs text-gray-500 mb-1">Strike Criteria</label>
                                <select
                                  value={leg.strike_criteria}
                                  onChange={e => updateLeg(leg.id, 'strike_criteria', e.target.value)}
                                  className="w-full h-8 px-2 border border-gray-300 rounded text-xs bg-white"
                                >
                                  <option value="strike_type">Strike Type</option>
                                  <option value="premium_range">Premium Range</option>
                                  <option value="closest_premium">Closest Premium</option>
                                  <option value="premium_gte">Premium ≥</option>
                                  <option value="premium_lte">Premium ≤</option>
                                </select>
                              </div>

                              {/* Row 4: Strike Value */}
                              <div>
                                {leg.strike_criteria === 'strike_type' ? (
                                  <div>
                                    <label className="block text-xs text-gray-500 mb-1">Strike Type</label>
                                    <select
                                      value={leg.strike_type}
                                      onChange={e => updateLeg(leg.id, 'strike_type', e.target.value)}
                                      className="w-full h-8 px-2 border border-gray-300 rounded text-xs bg-white"
                                    >
                                      {strikeTypeOpts.map(o => (
                                        <option key={o.value} value={o.value}>{o.label}</option>
                                      ))}
                                    </select>
                                  </div>
                                ) : leg.strike_criteria === 'premium_range' ? (
                                  <div className="grid grid-cols-2 gap-2">
                                    <div>
                                      <label className="block text-xs text-gray-500 mb-1">Min Premium</label>
                                      <input
                                        type="number"
                                        placeholder="Min"
                                        value={leg.premium_min || ''}
                                        onChange={e => updateLeg(leg.id, 'premium_min', +e.target.value)}
                                        className="w-full h-8 px-2 border border-gray-300 rounded text-xs text-center"
                                      />
                                    </div>
                                    <div>
                                      <label className="block text-xs text-gray-500 mb-1">Max Premium</label>
                                      <input
                                        type="number"
                                        placeholder="Max"
                                        value={leg.premium_max || ''}
                                        onChange={e => updateLeg(leg.id, 'premium_max', +e.target.value)}
                                        className="w-full h-8 px-2 border border-gray-300 rounded text-xs text-center"
                                      />
                                    </div>
                                  </div>
                                ) : (
                                  <div>
                                    <label className="block text-xs text-gray-500 mb-1">Premium Value</label>
                                    <input
                                      type="number"
                                      placeholder="Premium ₹"
                                      value={leg.premium_value || ''}
                                      onChange={e => updateLeg(leg.id, 'premium_value', +e.target.value)}
                                      className="w-full h-8 px-2 border border-gray-300 rounded text-xs text-center"
                                    />
                                  </div>
                                )}
                              </div>
                            </>
                          )}

                          {leg.segment === 'futures' && (
                            <div>
                              <label className="block text-xs text-gray-500 mb-1">Expiry</label>
                              <select
                                value={leg.expiry}
                                onChange={e => updateLeg(leg.id, 'expiry', e.target.value)}
                                className="w-full h-8 px-2 border border-gray-300 rounded text-xs bg-white"
                              >
                                <option value="monthly">Monthly</option>
                                <option value="next_monthly">Next Monthly</option>
                              </select>
                            </div>
                          )}

                          {/* Stop Loss & Target */}
                          <div className="pt-3 border-t border-gray-100">
                            <div className="grid grid-cols-2 gap-3">
                              {/* Stop Loss Toggle */}
                              <div>
                                <div className="flex items-center justify-between mb-2">
                                  <label className="text-xs font-medium text-gray-600 flex items-center gap-1.5">
                                    <input
                                      type="checkbox"
                                      checked={leg.stop_loss_enabled}
                                      onChange={e => updateLeg(leg.id, 'stop_loss_enabled', e.target.checked)}
                                      className="w-3.5 h-3.5 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                                    />
                                    Stop Loss
                                  </label>
                                  {leg.stop_loss_enabled && (
                                    <SegBtn
                                      options={[{ value: 'pct', label: '%' }, { value: 'points', label: 'Pts' }]}
                                      value={leg.stop_loss_type}
                                      onChange={v => updateLeg(leg.id, 'stop_loss_type', v)}
                                      size="sm"
                                    />
                                  )}
                                </div>
                                {leg.stop_loss_enabled && (
                                  <input
                                    type="number"
                                    min={0}
                                    placeholder="—"
                                    value={leg.stop_loss ?? ''}
                                    onChange={e => updateLeg(leg.id, 'stop_loss', e.target.value === '' ? null : +e.target.value)}
                                    className="w-full h-8 px-2 border border-gray-300 rounded text-xs text-center"
                                  />
                                )}
                              </div>
                              {/* Target Toggle */}
                              <div>
                                <div className="flex items-center justify-between mb-2">
                                  <label className="text-xs font-medium text-gray-600 flex items-center gap-1.5">
                                    <input
                                      type="checkbox"
                                      checked={leg.target_enabled}
                                      onChange={e => updateLeg(leg.id, 'target_enabled', e.target.checked)}
                                      className="w-3.5 h-3.5 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                                    />
                                    Target
                                  </label>
                                  {leg.target_enabled && (
                                    <SegBtn
                                      options={[{ value: 'pct', label: '%' }, { value: 'points', label: 'Pts' }]}
                                      value={leg.target_type}
                                      onChange={v => updateLeg(leg.id, 'target_type', v)}
                                      size="sm"
                                    />
                                  )}
                                </div>
                                {leg.target_enabled && (
                                  <input
                                    type="number"
                                    min={0}
                                    placeholder="—"
                                    value={leg.target ?? ''}
                                    onChange={e => updateLeg(leg.id, 'target', e.target.value === '' ? null : +e.target.value)}
                                    className="w-full h-8 px-2 border border-gray-300 rounded text-xs text-center"
                                  />
                                )}
                              </div>
                            </div>
                          </div>

                          {/* Units Display */}
                          <div className="text-right">
                            <span className="text-xs text-blue-600 font-medium">
                              {leg.lot * getLotSize(instrument, startDate)} units
                            </span>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
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
            {error && <span className="text-xs text-red-600">{error}</span>}
          </div>
        </div>

        {/* Results */}
        {results && (
          <div className="mt-4">
            <ResultsPanel results={results} onClose={() => setResults(null)} showCloseButton={false} />
          </div>
        )}
      </div>
    </div>
  );
};

export default AlgoTestBacktest;
