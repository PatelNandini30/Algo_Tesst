import React, { useState } from 'react';
import { Play, Plus, Trash2, ChevronDown, ChevronUp } from 'lucide-react';
import ResultsPanel from './ResultsPanel';

// Lot sizes for different indices
const LOT_SIZES = {
  NIFTY: 75,
  BANKNIFTY: 15,
  FINNIFTY: 40,
  MIDCPNIFTY: 75,
  SENSEX: 10
};

const AlgoTestBacktest = () => {
  // Instrument Settings
  const [instrument, setInstrument] = useState('NIFTY');
  const [underlying, setUnderlying] = useState('cash');
  
  // Strategy Type
  const [strategyType, setStrategyType] = useState('positional');
  const [expiryBasis, setExpiryBasis] = useState('weekly');
  
  // Entry/Exit Settings
  const [entryDaysBefore, setEntryDaysBefore] = useState(2);
  const [exitDaysBefore, setExitDaysBefore] = useState(0);
  
  // Legs
  const [legs, setLegs] = useState([]);
  const [expandedLeg, setExpandedLeg] = useState(null);
  
  // Overall Settings
  const [overallStopLoss, setOverallStopLoss] = useState(null);
  const [overallTarget, setOverallTarget] = useState(null);
  
  // Backtest Period
  const [startDate, setStartDate] = useState('2020-01-01');
  const [endDate, setEndDate] = useState('2023-12-31');
  
  // UI State
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState(null);

  // Get days options based on expiry type
  const getDaysOptions = () => {
    if (expiryBasis === 'weekly') {
      return [0, 1, 2, 3, 4];
    } else {
      return Array.from({ length: 25 }, (_, i) => i); // 0-24
    }
  };

  // Add new leg
  const addLeg = () => {
    const newLeg = {
      id: Date.now(),
      segment: 'options',
      position: 'sell',
      option_type: 'call',
      expiry: 'weekly',
      lot: 1,
      strike_selection: {
        type: 'strike_type',
        strike_type: 'atm',
        strikes_away: 0
      }
    };
    setLegs([...legs, newLeg]);
    setExpandedLeg(legs.length);
  };

  // Remove leg
  const removeLeg = (index) => {
    setLegs(legs.filter((_, i) => i !== index));
    if (expandedLeg === index) setExpandedLeg(null);
  };

  // Update leg
  const updateLeg = (index, field, value) => {
    const newLegs = [...legs];
    newLegs[index] = { ...newLegs[index], [field]: value };
    setLegs(newLegs);
  };

  // Update strike selection
  const updateStrikeSelection = (index, field, value) => {
    const newLegs = [...legs];
    newLegs[index].strike_selection = {
      ...newLegs[index].strike_selection,
      [field]: value
    };
    setLegs(newLegs);
  };

  // Build payload
  const buildPayload = () => {
    // Get lot size for the selected index
    const lotSize = LOT_SIZES[instrument.toUpperCase()] || 1;
    
    // Multiply each leg's lot by the lot size
    const legsWithLotSize = legs.map(leg => ({
      ...leg,
      lots: (leg.lot || leg.lots || 1) * lotSize
    }));
    
    console.log(`Lot size for ${instrument}: ${lotSize}`);
    
    return {
      index: instrument,
      underlying,
      strategy_type: strategyType,
      expiry_window: expiryBasis === 'weekly' ? 'weekly_expiry' : 'monthly_expiry',
      entry_dte: entryDaysBefore,
      exit_dte: exitDaysBefore,
      legs: legsWithLotSize,
      overall_settings: {
        stop_loss: overallStopLoss,
        target: overallTarget
      },
      date_from: startDate,
      date_to: endDate,
      expiry_type: expiryBasis.toUpperCase()
    };
  };

  // Run backtest
  const runBacktest = async () => {
    if (legs.length === 0) {
      setError('Please add at least one leg');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const payload = buildPayload();
      const response = await fetch('/api/dynamic-backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (response.ok) {
        const data = await response.json();
        setResults(data);
      } else {
        const errorData = await response.json();
        setError(errorData.detail || 'Backtest failed');
      }
    } catch (err) {
      setError('Network error. Check if backend is running.');
    } finally {
      setLoading(false);
    }
  };

  // Render strike criteria fields
  const renderStrikeCriteria = (leg, index) => {
    const { strike_selection } = leg;

    switch (strike_selection.type) {
      case 'strike_type':
        return (
          <div className="space-y-3">
            <div>
              <label className="block text-sm font-medium mb-1">Strike Type</label>
              <select
                value={strike_selection.strike_type || 'atm'}
                onChange={(e) => updateStrikeSelection(index, 'strike_type', e.target.value)}
                className="w-full p-2 border rounded"
              >
                <option value="atm">ATM</option>
                <option value="itm">ITM</option>
                <option value="otm">OTM</option>
              </select>
            </div>
            {(strike_selection.strike_type === 'itm' || strike_selection.strike_type === 'otm') && (
              <div>
                <label className="block text-sm font-medium mb-1">Strikes Away</label>
                <input
                  type="number"
                  min="1"
                  value={strike_selection.strikes_away || 1}
                  onChange={(e) => updateStrikeSelection(index, 'strikes_away', parseInt(e.target.value))}
                  className="w-full p-2 border rounded"
                />
              </div>
            )}
          </div>
        );

      case 'premium_range':
        return (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium mb-1">Lower</label>
              <input
                type="number"
                value={strike_selection.lower || 0}
                onChange={(e) => updateStrikeSelection(index, 'lower', parseFloat(e.target.value))}
                className="w-full p-2 border rounded"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Upper</label>
              <input
                type="number"
                value={strike_selection.upper || 0}
                onChange={(e) => updateStrikeSelection(index, 'upper', parseFloat(e.target.value))}
                className="w-full p-2 border rounded"
              />
            </div>
          </div>
        );

      case 'closest_premium':
        return (
          <div>
            <label className="block text-sm font-medium mb-1">Target Premium</label>
            <input
              type="number"
              value={strike_selection.premium || 0}
              onChange={(e) => updateStrikeSelection(index, 'premium', parseFloat(e.target.value))}
              className="w-full p-2 border rounded"
            />
          </div>
        );

      case 'premium_gte':
        return (
          <div>
            <label className="block text-sm font-medium mb-1">Premium ≥</label>
            <input
              type="number"
              value={strike_selection.premium || 0}
              onChange={(e) => updateStrikeSelection(index, 'premium', parseFloat(e.target.value))}
              className="w-full p-2 border rounded"
            />
          </div>
        );

      case 'premium_lte':
        return (
          <div>
            <label className="block text-sm font-medium mb-1">Premium ≤</label>
            <input
              type="number"
              value={strike_selection.premium || 0}
              onChange={(e) => updateStrikeSelection(index, 'premium', parseFloat(e.target.value))}
              className="w-full p-2 border rounded"
            />
          </div>
        );

      case 'straddle_width':
        return (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-sm">ATM Strike</span>
              <select
                value={strike_selection.operator || '+'}
                onChange={(e) => updateStrikeSelection(index, 'operator', e.target.value)}
                className="p-2 border rounded"
              >
                <option value="+">+</option>
                <option value="-">-</option>
              </select>
              <span className="text-sm">(</span>
              <input
                type="number"
                step="0.1"
                value={strike_selection.multiplier || 0.5}
                onChange={(e) => updateStrikeSelection(index, 'multiplier', parseFloat(e.target.value))}
                className="w-20 p-2 border rounded"
              />
              <span className="text-sm">× ATM Straddle Price )</span>
            </div>
          </div>
        );

      case 'pct_of_atm':
        return (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-sm">ATM</span>
              <select
                value={strike_selection.operator || '+'}
                onChange={(e) => updateStrikeSelection(index, 'operator', e.target.value)}
                className="p-2 border rounded"
              >
                <option value="+">+</option>
                <option value="-">-</option>
              </select>
              <input
                type="number"
                step="0.1"
                value={strike_selection.percentage || 0}
                onChange={(e) => updateStrikeSelection(index, 'percentage', parseFloat(e.target.value))}
                className="w-20 p-2 border rounded"
              />
              <span className="text-sm">% of ATM</span>
            </div>
          </div>
        );

      case 'atm_straddle_premium_pct':
        return (
          <div>
            <label className="block text-sm font-medium mb-1">% of ATM Straddle Premium</label>
            <input
              type="number"
              step="1"
              value={strike_selection.percentage || 50}
              onChange={(e) => updateStrikeSelection(index, 'percentage', parseFloat(e.target.value))}
              className="w-full p-2 border rounded"
            />
          </div>
        );

      default:
        return null;
    }
  };

  if (results) {
    return <ResultsPanel results={results} onClose={() => setResults(null)} />;
  }

  const daysOptions = getDaysOptions();

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <h1 className="text-2xl font-bold">Backtest</h1>
          <p className="text-sm text-gray-500">Build and test your strategy</p>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Left Column */}
          <div className="space-y-6">
            {/* Instrument Settings */}
            <div className="bg-white rounded-lg shadow p-6">
              <h3 className="font-semibold mb-4">Instrument settings</h3>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium mb-2">Index</label>
                  <select
                    value={instrument}
                    onChange={(e) => setInstrument(e.target.value)}
                    className="w-full p-2 border rounded"
                  >
                    <option value="NIFTY">NIFTY</option>
                    <option value="SENSEX">SENSEX</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium mb-2">Underlying from</label>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setUnderlying('cash')}
                      className={`flex-1 py-2 rounded border ${underlying === 'cash' ? 'bg-blue-600 text-white' : 'bg-white'}`}
                    >
                      Cash
                    </button>
                    <button
                      onClick={() => setUnderlying('futures')}
                      className={`flex-1 py-2 rounded border ${underlying === 'futures' ? 'bg-blue-600 text-white' : 'bg-white'}`}
                    >
                      Futures
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {/* Leg Builder */}
            <div className="bg-white rounded-lg shadow p-6">
              <div className="flex justify-between items-center mb-4">
                <h3 className="font-semibold">Leg Builder</h3>
                <button className="text-sm text-blue-600">Collapse</button>
              </div>

              {legs.map((leg, index) => (
                <div key={leg.id} className="border rounded mb-3">
                  <div
                    className="flex justify-between items-center p-3 bg-gray-50 cursor-pointer"
                    onClick={() => setExpandedLeg(expandedLeg === index ? null : index)}
                  >
                    <span className="font-medium text-sm">
                      Leg {index + 1}: {leg.segment === 'futures' ? 'Future' : `${leg.option_type.toUpperCase()}`} {leg.position.toUpperCase()}
                    </span>
                    <div className="flex items-center gap-2">
                      <button onClick={(e) => { e.stopPropagation(); removeLeg(index); }} className="text-red-500">
                        <Trash2 size={16} />
                      </button>
                      {expandedLeg === index ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
                    </div>
                  </div>

                  {expandedLeg === index && (
                    <div className="p-4 space-y-4">
                      {/* Segment Selection */}
                      <div>
                        <label className="block text-sm font-medium mb-2">Select segments</label>
                        <div className="flex gap-2">
                          <button
                            onClick={() => updateLeg(index, 'segment', 'futures')}
                            className={`flex-1 py-2 rounded border ${leg.segment === 'futures' ? 'bg-blue-600 text-white' : ''}`}
                          >
                            Futures
                          </button>
                          <button
                            onClick={() => updateLeg(index, 'segment', 'options')}
                            className={`flex-1 py-2 rounded border ${leg.segment === 'options' ? 'bg-blue-600 text-white' : ''}`}
                          >
                            Options
                          </button>
                        </div>
                      </div>

                      {/* Total Lot */}
                      <div>
                        <label className="block text-sm font-medium mb-1">Total Lot</label>
                        <input
                          type="number"
                          min="1"
                          value={leg.lot}
                          onChange={(e) => updateLeg(index, 'lot', parseInt(e.target.value))}
                          className="w-full p-2 border rounded"
                        />
                      </div>

                      {/* Position */}
                      <div>
                        <label className="block text-sm font-medium mb-1">Position</label>
                        <div className="flex gap-2">
                          <button
                            onClick={() => updateLeg(index, 'position', 'buy')}
                            className={`flex-1 py-2 rounded border ${leg.position === 'buy' ? 'bg-green-600 text-white' : ''}`}
                          >
                            Buy
                          </button>
                          <button
                            onClick={() => updateLeg(index, 'position', 'sell')}
                            className={`flex-1 py-2 rounded border ${leg.position === 'sell' ? 'bg-red-600 text-white' : ''}`}
                          >
                            Sell
                          </button>
                        </div>
                      </div>

                      {/* Option Type (only for options) */}
                      {leg.segment === 'options' && (
                        <div>
                          <label className="block text-sm font-medium mb-1">Option Type</label>
                          <div className="flex gap-2">
                            <button
                              onClick={() => updateLeg(index, 'option_type', 'call')}
                              className={`flex-1 py-2 rounded border ${leg.option_type === 'call' ? 'bg-blue-600 text-white' : ''}`}
                            >
                              Call
                            </button>
                            <button
                              onClick={() => updateLeg(index, 'option_type', 'put')}
                              className={`flex-1 py-2 rounded border ${leg.option_type === 'put' ? 'bg-purple-600 text-white' : ''}`}
                            >
                              Put
                            </button>
                          </div>
                        </div>
                      )}

                      {/* Expiry */}
                      <div>
                        <label className="block text-sm font-medium mb-1">Expiry</label>
                        <select
                          value={leg.expiry}
                          onChange={(e) => updateLeg(index, 'expiry', e.target.value)}
                          className="w-full p-2 border rounded"
                        >
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

                      {/* Strike Criteria (only for options) */}
                      {leg.segment === 'options' && (
                        <div>
                          <label className="block text-sm font-medium mb-2">Strike Criteria</label>
                          <select
                            value={leg.strike_selection.type}
                            onChange={(e) => updateStrikeSelection(index, 'type', e.target.value)}
                            className="w-full p-2 border rounded mb-3"
                          >
                            <option value="strike_type">Strike Type</option>
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
                      )}
                    </div>
                  )}
                </div>
              ))}

              <button
                onClick={addLeg}
                className="w-full py-3 bg-blue-600 text-white rounded flex items-center justify-center gap-2"
              >
                <Plus size={18} />
                Add Leg
              </button>
            </div>
          </div>

          {/* Right Column */}
          <div className="space-y-6">
            {/* Entry Settings */}
            <div className="bg-white rounded-lg shadow p-6">
              <h3 className="font-semibold mb-4">Entry settings</h3>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium mb-2">Strategy Type</label>
                  <div className="flex gap-2">
                    {['intraday', 'btst', 'positional'].map(type => (
                      <button
                        key={type}
                        onClick={() => setStrategyType(type)}
                        className={`flex-1 py-2 rounded border text-sm ${strategyType === type ? 'bg-blue-600 text-white' : ''}`}
                      >
                        {type.charAt(0).toUpperCase() + type.slice(1)}
                      </button>
                    ))}
                  </div>
                </div>

                {strategyType === 'positional' && (
                  <>
                    <div>
                      <label className="block text-sm font-medium mb-2">Positional expires on</label>
                      <div className="flex gap-2">
                        <select
                          value={expiryBasis}
                          onChange={(e) => setExpiryBasis(e.target.value)}
                          className="flex-1 p-2 border rounded"
                        >
                          <option value="weekly">Weekly Expiry</option>
                          <option value="monthly">Monthly Expiry</option>
                        </select>
                        <button className="px-3 py-2 border rounded text-sm">basis</button>
                      </div>
                    </div>

                    <div>
                      <label className="block text-sm font-medium mb-2">Entry</label>
                      <div className="flex items-center gap-2">
                        <select
                          value={entryDaysBefore}
                          onChange={(e) => setEntryDaysBefore(parseInt(e.target.value))}
                          className="w-20 p-2 border rounded"
                        >
                          {daysOptions.map(d => <option key={d} value={d}>{d}</option>)}
                        </select>
                        <span className="text-sm">trading days before expiry</span>
                      </div>
                    </div>
                  </>
                )}
              </div>
            </div>

            {/* Exit Settings */}
            {strategyType === 'positional' && (
              <div className="bg-white rounded-lg shadow p-6">
                <h3 className="font-semibold mb-4">Exit settings</h3>
                <div>
                  <label className="block text-sm font-medium mb-2">Exit</label>
                  <div className="flex items-center gap-2">
                    <select
                      value={exitDaysBefore}
                      onChange={(e) => setExitDaysBefore(parseInt(e.target.value))}
                      className="w-20 p-2 border rounded"
                    >
                      {daysOptions.map(d => <option key={d} value={d}>{d}</option>)}
                    </select>
                    <span className="text-sm">trading days before expiry</span>
                  </div>
                </div>
              </div>
            )}

            {/* Backtest Duration */}
            <div className="bg-white rounded-lg shadow p-6">
              <h3 className="font-semibold mb-4">Enter the duration of your backtest</h3>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Start Date</label>
                  <input
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                    className="w-full p-2 border rounded"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">End Date</label>
                  <input
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    className="w-full p-2 border rounded"
                  />
                </div>
              </div>
            </div>

            {error && (
              <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-700">
                {error}
              </div>
            )}

            <button
              onClick={runBacktest}
              disabled={loading || legs.length === 0}
              className={`w-full py-3 rounded font-medium flex items-center justify-center gap-2 ${
                loading || legs.length === 0
                  ? 'bg-gray-300 text-gray-500'
                  : 'bg-green-600 text-white hover:bg-green-700'
              }`}
            >
              {loading ? (
                <>
                  <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-white"></div>
                  Running...
                </>
              ) : (
                <>
                  <Play size={18} />
                  Start Backtest
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AlgoTestBacktest;
