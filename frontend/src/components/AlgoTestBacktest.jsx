import React, { useState } from 'react';
import { Play, Plus, Trash2, ChevronDown, ChevronUp } from 'lucide-react';
import ResultsPanel from './ResultsPanel';

// ─── CHANGE 1 ───────────────────────────────────────────────────────────────
// REMOVED: static LOT_SIZES = { NIFTY: 75, BANKNIFTY: 15, ... }
//   Problem: NIFTY:75 was applied to ALL years but NSE changed lot sizes 4x.
//   For 2000-2026 data this was wrong for every period except Oct2015-Oct2019.
// ADDED: date-aware getLotSize(index, tradeDate) — mirrors backend exactly.
//   NIFTY:     2000–Sep2010=200 | Oct2010–Oct2015=50 | Oct2015–Oct2019=75 | Nov2019+=65
//   BANKNIFTY: 2000–Sep2010=50  | Oct2010–Oct2015=25 | Oct2015–Oct2019=20 | Nov2019+=15
// ────────────────────────────────────────────────────────────────────────────
const getLotSize = (index, tradeDate) => {
  const d = new Date(tradeDate);
  if (index === 'NIFTY') {
    if (d < new Date('2010-10-01')) return 200;
    if (d < new Date('2015-10-29')) return 50;
    if (d < new Date('2019-11-01')) return 75;
    return 65;  // Updated to match AlgoTest
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

const AlgoTestBacktest = () => {
  const [instrument, setInstrument] = useState('NIFTY');
  const [underlying, setUnderlying] = useState('cash');
  const [strategyType, setStrategyType] = useState('positional');
  const [expiryBasis, setExpiryBasis] = useState('weekly');
  const [entryDaysBefore, setEntryDaysBefore] = useState(2);
  const [exitDaysBefore, setExitDaysBefore] = useState(0);
  const [legs, setLegs] = useState([]);
  const [expandedLeg, setExpandedLeg] = useState(null);
  const [overallStopLoss, setOverallStopLoss] = useState(null);
  const [overallTarget, setOverallTarget] = useState(null);
  const [startDate, setStartDate] = useState('2020-01-01');
  const [endDate, setEndDate] = useState('2023-12-31');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState(null);

  // ─── CHANGE 2 ─────────────────────────────────────────────────────────────
  // ADDED: currentLotSize derived from startDate so header badge and summary
  // always show the correct lot size for the selected backtest period.
  // Old: LOT_SIZES[instrument] → always 75 for NIFTY regardless of date.
  // ────────────────────────────────────────────────────────────────────────────
  const currentLotSize = getLotSize(instrument, startDate);

  const getDaysOptions = () =>
    expiryBasis === 'weekly'
      ? [0, 1, 2, 3, 4]
      : Array.from({ length: 25 }, (_, i) => i);
  const daysOptions = getDaysOptions();

  const addLeg = () => {
    const newLeg = {
      id: Date.now(),
      segment: 'options',
      position: 'sell',
      option_type: 'call',
      expiry: 'weekly',
      lot: 1,
      strike_selection: { type: 'strike_type', strike_type: 'atm', strikes_away: 0 }
    };
    setLegs([...legs, newLeg]);
    setExpandedLeg(legs.length);
  };

  const removeLeg = (index) => {
    setLegs(legs.filter((_, i) => i !== index));
    if (expandedLeg === index) setExpandedLeg(null);
  };

  const updateLeg = (index, field, value) => {
    const newLegs = [...legs];
    newLegs[index] = { ...newLegs[index], [field]: value };
    setLegs(newLegs);
  };

  const updateStrikeSelection = (index, field, value) => {
    const newLegs = [...legs];
    newLegs[index].strike_selection = { ...newLegs[index].strike_selection, [field]: value };
    setLegs(newLegs);
  };

  const buildPayload = () => ({
    index: instrument,
    underlying,
    strategy_type: strategyType,
    expiry_window: expiryBasis === 'weekly' ? 'weekly_expiry' : 'monthly_expiry',
    entry_dte: entryDaysBefore,
    exit_dte: exitDaysBefore,
    legs: legs.map(leg => ({ ...leg, lot: leg.lot || 1 })),
    overall_settings: { stop_loss: overallStopLoss, target: overallTarget },
    date_from: startDate,
    date_to: endDate,
    expiry_type: expiryBasis.toUpperCase()
  });

  const runBacktest = async () => {
    if (legs.length === 0) { setError('Please add at least one leg'); return; }
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/dynamic-backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload())
      });
      if (response.ok) {
        setResults(await response.json());
      } else {
        const e = await response.json();
        setError(e.detail || 'Backtest failed');
      }
    } catch (err) {
      setError('Network error. Check if backend is running.');
    } finally {
      setLoading(false);
    }
  };

  const renderStrikeCriteria = (leg, index) => {
    const { strike_selection: ss } = leg;
    switch (ss.type) {
      case 'strike_type':
        return (
          <div>
            <label className="block text-xs font-medium mb-1">Strike Type</label>
            <select
              value={ss.strike_type || 'atm'}
              onChange={(e) => updateStrikeSelection(index, 'strike_type', e.target.value)}
              className="w-full p-1.5 border rounded text-sm"
            >
              <option value="itm20">ITM 20</option>
              <option value="itm19">ITM 19</option>
              <option value="itm18">ITM 18</option>
              <option value="itm17">ITM 17</option>
              <option value="itm16">ITM 16</option>
              <option value="itm15">ITM 15</option>
              <option value="itm14">ITM 14</option>
              <option value="itm13">ITM 13</option>
              <option value="itm12">ITM 12</option>
              <option value="itm11">ITM 11</option>
              <option value="itm10">ITM 10</option>
              <option value="itm9">ITM 9</option>
              <option value="itm8">ITM 8</option>
              <option value="itm7">ITM 7</option>
              <option value="itm6">ITM 6</option>
              <option value="itm5">ITM 5</option>
              <option value="itm4">ITM 4</option>
              <option value="itm3">ITM 3</option>
              <option value="itm2">ITM 2</option>
              <option value="itm1">ITM 1</option>
              <option value="atm">ATM</option>
              <option value="otm1">OTM 1</option>
              <option value="otm2">OTM 2</option>
              <option value="otm3">OTM 3</option>
              <option value="otm4">OTM 4</option>
              <option value="otm5">OTM 5</option>
              <option value="otm6">OTM 6</option>
              <option value="otm7">OTM 7</option>
              <option value="otm8">OTM 8</option>
              <option value="otm9">OTM 9</option>
              <option value="otm10">OTM 10</option>
              <option value="otm11">OTM 11</option>
              <option value="otm12">OTM 12</option>
              <option value="otm13">OTM 13</option>
              <option value="otm14">OTM 14</option>
              <option value="otm15">OTM 15</option>
              <option value="otm16">OTM 16</option>
              <option value="otm17">OTM 17</option>
              <option value="otm18">OTM 18</option>
              <option value="otm19">OTM 19</option>
              <option value="otm20">OTM 20</option>
            </select>
          </div>
        );
      case 'premium_range':
        return (
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-xs font-medium mb-1">Lower (₹)</label>
              <input type="number" value={ss.lower || 0}
                onChange={(e) => updateStrikeSelection(index, 'lower', parseFloat(e.target.value))}
                className="w-full p-1.5 border rounded text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1">Upper (₹)</label>
              <input type="number" value={ss.upper || 0}
                onChange={(e) => updateStrikeSelection(index, 'upper', parseFloat(e.target.value))}
                className="w-full p-1.5 border rounded text-sm" />
            </div>
          </div>
        );
      case 'closest_premium':
      case 'premium_gte':
      case 'premium_lte':
        return (
          <div>
            <label className="block text-xs font-medium mb-1">Premium (₹)</label>
            <input type="number" value={ss.premium || 0}
              onChange={(e) => updateStrikeSelection(index, 'premium', parseFloat(e.target.value))}
              className="w-full p-1.5 border rounded text-sm" />
          </div>
        );
      case 'straddle_width':
        return (
          <div>
            <label className="block text-xs font-medium mb-1">Width (%)</label>
            <input type="number" value={ss.width || 0}
              onChange={(e) => updateStrikeSelection(index, 'width', parseFloat(e.target.value))}
              className="w-full p-1.5 border rounded text-sm" />
          </div>
        );
      case 'pct_of_atm':
        return (
          <div>
            <label className="block text-xs font-medium mb-1">% of ATM</label>
            <input type="number" value={ss.pct || 0}
              onChange={(e) => updateStrikeSelection(index, 'pct', parseFloat(e.target.value))}
              className="w-full p-1.5 border rounded text-sm" />
          </div>
        );
      default: return null;
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 shadow-sm sticky top-0 z-10">
        <div className="max-w-full mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex justify-between items-center">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">Options Backtest</h1>
              <p className="text-xs text-gray-600 mt-1">Build and test your options strategy</p>
            </div>
            <div className="flex items-center gap-4">
              <div className="text-right bg-blue-50 px-3 py-1.5 rounded-lg border border-blue-200">
                <p className="text-xs text-blue-600 font-medium">Lot Size ({startDate.slice(0, 4)})</p>
                <p className="text-sm font-bold text-blue-700">{instrument}: {currentLotSize}</p>
              </div>
              <div className="text-right bg-gray-50 px-3 py-1.5 rounded-lg border border-gray-200">
                <p className="text-xs text-gray-500 font-medium">Strategy</p>
                <p className="text-sm font-bold text-gray-700">{legs.length} Leg{legs.length !== 1 ? 's' : ''}</p>
              </div>
              {results && (
                <button onClick={() => setResults(null)}
                  className="px-4 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700 text-sm font-medium">
                  New Backtest
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-full mx-auto px-4 sm:px-6 lg:px-8 py-4">
        {/* Configuration Section - Always full width at top */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">

          {/* Left — Leg Builder */}
          <div className="lg:col-span-2 space-y-3">
            <div className="bg-white rounded-lg shadow-md p-4 border border-gray-200">
              <div className="flex justify-between items-center mb-4">
                <div>
                  <h3 className="text-lg font-bold text-gray-900">Strategy Legs</h3>
                  <p className="text-xs text-gray-500 mt-0.5">Configure positions</p>
                </div>
                <button onClick={addLeg} disabled={legs.length >= 4}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-gradient-to-r from-blue-600 to-blue-700 text-white rounded-lg hover:from-blue-700 hover:to-blue-800 disabled:from-gray-300 disabled:to-gray-400 disabled:cursor-not-allowed shadow-md transition-all text-sm">
                  <Plus size={16} /> Add Leg
                </button>
              </div>

              {legs.length === 0 ? (
                <div className="text-center py-12 border-2 border-dashed border-gray-300 rounded-xl bg-gray-50">
                  <div className="text-gray-400 mb-2">
                    <svg className="mx-auto h-12 w-12" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
                    </svg>
                  </div>
                  <p className="text-sm font-semibold text-gray-700 mb-1">No legs configured</p>
                  <p className="text-xs text-gray-500">Click "Add Leg" to start</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {legs.map((leg, index) => (
                    <div key={leg.id} className="border-2 border-gray-200 rounded-lg overflow-hidden hover:border-blue-300 transition-colors">
                      {/* Leg header row */}
                      <div className="flex justify-between items-center p-2 bg-gray-50 cursor-pointer"
                        onClick={() => setExpandedLeg(expandedLeg === index ? null : index)}>
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-medium text-gray-700 text-sm">Leg {index + 1}</span>
                          <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${
                            leg.position === 'sell' ? 'bg-red-100 text-red-700' : 'bg-green-100 text-green-700'}`}>
                            {leg.position?.toUpperCase()}
                          </span>
                          <span className="text-xs text-gray-500">
                            {leg.segment === 'options'
                              ? `${leg.option_type?.toUpperCase()} · ${leg.strike_selection?.strike_type?.toUpperCase()}`
                              : 'FUT'} · {leg.lot} lot
                          </span>
                          <span className="text-xs text-blue-500 font-medium">
                            = {leg.lot * getLotSize(instrument, startDate)} units
                          </span>
                        </div>
                        <div className="flex items-center gap-2">
                          <button onClick={(e) => { e.stopPropagation(); removeLeg(index); }}
                            className="p-1 text-red-400 hover:text-red-600">
                            <Trash2 size={14} />
                          </button>
                          {expandedLeg === index ? <ChevronUp size={14} className="text-gray-400" /> : <ChevronDown size={14} className="text-gray-400" />}
                        </div>
                      </div>

                      {expandedLeg === index && (
                        <div className="p-3 grid grid-cols-2 gap-3">
                          <div>
                            <label className="block text-xs font-medium mb-1">Segment</label>
                            <select value={leg.segment} onChange={(e) => updateLeg(index, 'segment', e.target.value)}
                              className="w-full p-1.5 border rounded text-sm">
                              <option value="options">Options</option>
                              <option value="futures">Futures</option>
                            </select>
                          </div>

                          <div>
                            <label className="block text-xs font-medium mb-1">Position</label>
                            <div className="flex gap-1">
                              <button onClick={() => updateLeg(index, 'position', 'buy')}
                                className={`flex-1 py-1.5 rounded border text-xs font-medium ${leg.position === 'buy' ? 'bg-green-600 text-white border-green-600' : 'border-gray-300'}`}>Buy</button>
                              <button onClick={() => updateLeg(index, 'position', 'sell')}
                                className={`flex-1 py-1.5 rounded border text-xs font-medium ${leg.position === 'sell' ? 'bg-red-600 text-white border-red-600' : 'border-gray-300'}`}>Sell</button>
                            </div>
                          </div>

                          <div>
                            <label className="block text-xs font-medium mb-1">Lots</label>
                            <input type="number" min="1" value={leg.lot}
                              onChange={(e) => updateLeg(index, 'lot', parseInt(e.target.value) || 1)}
                              className="w-full p-1.5 border rounded text-sm" />
                          </div>

                          {leg.segment === 'options' && (
                            <>
                              <div>
                                <label className="block text-xs font-medium mb-1">Option Type</label>
                                <div className="flex gap-1">
                                  <button onClick={() => updateLeg(index, 'option_type', 'call')}
                                    className={`flex-1 py-1.5 rounded border text-xs ${leg.option_type === 'call' ? 'bg-blue-600 text-white' : ''}`}>Call</button>
                                  <button onClick={() => updateLeg(index, 'option_type', 'put')}
                                    className={`flex-1 py-1.5 rounded border text-xs ${leg.option_type === 'put' ? 'bg-purple-600 text-white' : ''}`}>Put</button>
                                </div>
                              </div>

                              <div className="col-span-2">
                                <label className="block text-xs font-medium mb-1">Expiry</label>
                                <select value={leg.expiry} onChange={(e) => updateLeg(index, 'expiry', e.target.value)}
                                  className="w-full p-1.5 border rounded text-sm">
                                  <option value="weekly">Weekly</option>
                                  <option value="next_weekly">Next Weekly</option>
                                  <option value="monthly">Monthly</option>
                                  <option value="next_monthly">Next Monthly</option>
                                </select>
                              </div>

                              <div className="col-span-2">
                                <label className="block text-xs font-medium mb-1">Strike Criteria</label>
                                <select value={leg.strike_selection.type}
                                  onChange={(e) => updateStrikeSelection(index, 'type', e.target.value)}
                                  className="w-full p-1.5 border rounded mb-2 text-sm">
                                  <option value="strike_type">Strike Type (ATM/ITM/OTM)</option>
                                  <option value="premium_range">Premium Range</option>
                                  <option value="closest_premium">Closest Premium</option>
                                  <option value="premium_gte">Premium ≥</option>
                                  <option value="premium_lte">Premium ≤</option>
                                  <option value="straddle_width">Straddle Width</option>
                                  <option value="pct_of_atm">% of ATM</option>
                                  <option value="atm_straddle_premium_pct">ATM Straddle Premium %</option>
                                </select>
                                {renderStrikeCriteria(leg, index)}
                              </div>
                            </>
                          )}

                          {leg.segment === 'futures' && (
                            <div className="col-span-2">
                              <label className="block text-xs font-medium mb-1">Expiry</label>
                              <select value={leg.expiry} onChange={(e) => updateLeg(index, 'expiry', e.target.value)}
                                className="w-full p-1.5 border rounded text-sm">
                                <option value="monthly">Monthly</option>
                                <option value="next_monthly">Next Monthly</option>
                              </select>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Right — Settings */}
          <div className="space-y-3">
            <div className="bg-white rounded-lg shadow p-4">
              <h3 className="font-semibold mb-3 text-sm">Entry Settings</h3>
              <div className="space-y-3">
                <div>
                  <label className="block text-xs font-medium mb-1">Instrument</label>
                  <select value={instrument} onChange={(e) => setInstrument(e.target.value)}
                    className="w-full p-1.5 border rounded text-sm">
                    <option value="NIFTY">NIFTY</option>
                    <option value="BANKNIFTY">BANKNIFTY</option>
                    <option value="FINNIFTY">FINNIFTY</option>
                    <option value="MIDCPNIFTY">MIDCPNIFTY</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1">Strategy Type</label>
                  <div className="flex gap-1">
                    {['intraday', 'btst', 'positional'].map(type => (
                      <button key={type} onClick={() => setStrategyType(type)}
                        className={`flex-1 py-1.5 rounded border text-xs ${strategyType === type ? 'bg-blue-600 text-white' : ''}`}>
                        {type.charAt(0).toUpperCase() + type.slice(1)}
                      </button>
                    ))}
                  </div>
                </div>
                {strategyType === 'positional' && (
                  <>
                    <div>
                      <label className="block text-xs font-medium mb-1">Positional expires on</label>
                      <select value={expiryBasis} onChange={(e) => setExpiryBasis(e.target.value)}
                        className="w-full p-1.5 border rounded text-sm">
                        <option value="weekly">Weekly Expiry</option>
                        <option value="monthly">Monthly Expiry</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-xs font-medium mb-1">Entry</label>
                      <div className="flex items-center gap-2">
                        <select value={entryDaysBefore} onChange={(e) => setEntryDaysBefore(parseInt(e.target.value))}
                          className="w-16 p-1.5 border rounded text-sm">
                          {daysOptions.map(d => <option key={d} value={d}>{d}</option>)}
                        </select>
                        <span className="text-xs">days before expiry</span>
                      </div>
                    </div>
                  </>
                )}
              </div>
            </div>

            {strategyType === 'positional' && (
              <div className="bg-white rounded-lg shadow p-4">
                <h3 className="font-semibold mb-3 text-sm">Exit Settings</h3>
                <div className="flex items-center gap-2">
                  <select value={exitDaysBefore} onChange={(e) => setExitDaysBefore(parseInt(e.target.value))}
                    className="w-16 p-1.5 border rounded text-sm">
                    {daysOptions.map(d => <option key={d} value={d}>{d}</option>)}
                  </select>
                  <span className="text-xs">days before expiry</span>
                </div>
              </div>
            )}

            <div className="bg-white rounded-lg shadow p-4">
              <h3 className="font-semibold mb-3 text-sm">Backtest Duration</h3>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium mb-1">Start Date</label>
                  <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)}
                    className="w-full p-1.5 border rounded text-sm" />
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1">End Date</label>
                  <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)}
                    className="w-full p-1.5 border rounded text-sm" />
                </div>
              </div>
            </div>

            {error && (
              <div className="bg-red-50 border border-red-200 rounded p-2 text-xs text-red-700">{error}</div>
            )}

            <button onClick={runBacktest} disabled={loading || legs.length === 0}
              className={`w-full py-2.5 rounded font-medium flex items-center justify-center gap-2 text-sm ${
                loading || legs.length === 0 ? 'bg-gray-300 text-gray-500 cursor-not-allowed' : 'bg-green-600 text-white hover:bg-green-700'}`}>
              {loading ? (
                <><div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>Running...</>
              ) : (
                <><Play size={16} />Start Backtest</>
              )}
            </button>

            {legs.length > 0 && (
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
                <h4 className="font-medium text-blue-900 mb-2 text-sm">Strategy Summary</h4>
                <div className="text-xs text-blue-800 space-y-0.5">
                  <p>• Index: {instrument}</p>
                  <p>• Period: {startDate} → {endDate}</p>
                  <p>• Lot size ({startDate.slice(0, 4)}): {currentLotSize} units/lot</p>
                  <p>• Entry: {entryDaysBefore} day(s) before {expiryBasis} expiry</p>
                  <p>• Exit: {exitDaysBefore} day(s) before expiry</p>
                  {legs.map((leg, i) => (
                    <p key={leg.id} className="ml-2">
                      - Leg {i + 1}: {leg.segment === 'futures' ? 'FUT' : leg.option_type?.toUpperCase()}{' '}
                      {leg.position?.toUpperCase()} · {leg.lot} lot
                      ({leg.lot * getLotSize(instrument, startDate)} units)
                    </p>
                  ))}
                </div>
              </div>
            )}
          </div>

        </div>

        {/* Results Section - Full width below configuration */}
        {results && (
          <div className="w-full">
            <ResultsPanel results={results} onClose={() => setResults(null)} showCloseButton={false} />
          </div>
        )}
      </div>
    </div>
  );
};

export default AlgoTestBacktest;