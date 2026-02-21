import React, { useState } from 'react';
import { Play, Save, AlertCircle, Loader2, TrendingUp, TrendingDown, Calendar, Target, Shield } from 'lucide-react';
import LegBuilder from '../components/LegBuilder';
import { api, createLegPayload, validateLegs } from '../services/legBuilderApi';

const Toggle = ({ enabled, onToggle }) => (
  <button
    type="button"
    onClick={onToggle}
    className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus:outline-none ${
      enabled ? 'bg-blue-600' : 'bg-gray-300'
    }`}
  >
    <span
      className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition-transform ${
        enabled ? 'translate-x-4' : 'translate-x-0'
      }`}
    />
  </button>
);

const SegBtn = ({ options, value, onChange }) => (
  <div className="inline-flex rounded-md overflow-hidden border border-gray-300">
    {options.map((opt, i) => (
      <button
        key={opt.value}
        type="button"
        onClick={() => onChange(opt.value)}
        className={`px-3 py-1.5 text-xs font-medium transition-colors ${
          i < options.length - 1 ? 'border-r border-gray-300' : ''
        } ${
          value === opt.value
            ? 'bg-blue-600 text-white'
            : 'bg-white text-gray-700 hover:bg-gray-50'
        }`}
      >
        {opt.label}
      </button>
    ))}
  </div>
);

const StrategyPage = () => {
  // Core Configuration
  const [instrument, setInstrument] = useState('NIFTY');
  const [underlying, setUnderlying] = useState('cash');
  const [strategyType, setStrategyType] = useState('positional');
  const [expiryBasis, setExpiryBasis] = useState('weekly');
  const [entryDaysBefore, setEntryDaysBefore] = useState(0);
  const [exitDaysBefore, setExitDaysBefore] = useState(0);

  // Legs
  const [legs, setLegs] = useState([]);

  // Risk Management
  const [overallSLEnabled, setOverallSLEnabled] = useState(false);
  const [overallSLType, setOverallSLType] = useState('max_loss');
  const [overallSLValue, setOverallSLValue] = useState(0);
  const [overallTgtEnabled, setOverallTgtEnabled] = useState(false);
  const [overallTgtType, setOverallTgtType] = useState('max_profit');
  const [overallTgtValue, setOverallTgtValue] = useState(0);

  // Date Range
  const [startDate, setStartDate] = useState('2025-02-01');
  const [endDate, setEndDate] = useState('2026-02-01');

  // State
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState(null);

  const instruments = [
    { value: 'NIFTY', label: 'NIFTY 50' },
    { value: 'BANKNIFTY', label: 'BANKNIFTY' },
    { value: 'FINNIFTY', label: 'FINNIFTY' },
    { value: 'MIDCPNIFTY', label: 'MIDCPNIFTY' },
    { value: 'SENSEX', label: 'SENSEX' },
  ];

  const daysOptions = expiryBasis === 'weekly' 
    ? [0, 1, 2, 3, 4] 
    : Array.from({ length: 25 }, (_, i) => i);

  const handleRunBacktest = async () => {
    setError(null);
    
    // Validate legs
    const validationErrors = validateLegs(legs, expiryBasis);
    if (validationErrors.length > 0) {
      setError(validationErrors.join('; '));
      return;
    }

    setLoading(true);
    
    try {
      const payload = createLegPayload({
        index: instrument,
        underlying,
        strategyType,
        expiryBasis,
        entryDaysBefore,
        exitDaysBefore,
        legs,
        overallSettings: {
          stop_loss_enabled: overallSLEnabled,
          stop_loss: overallSLValue,
          stop_loss_type: overallSLType,
          target_enabled: overallTgtEnabled,
          target: overallTgtValue,
          target_type: overallTgtType,
        },
        dateFrom: startDate,
        dateTo: endDate,
      });

      const response = await api.runBacktest(payload);
      setResults(response);
    } catch (err) {
      setError(err.message || 'Backtest failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-3 sticky top-0 z-50">
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
            <span className={`font-medium ${legs.length > 0 ? 'text-green-600' : 'text-gray-500'}`}>
              {legs.length} Leg{legs.length !== 1 ? 's' : ''} Active
            </span>
            <button className="flex items-center gap-1.5 px-3 py-1.5 text-blue-600 hover:bg-blue-50 rounded text-sm font-medium transition-colors">
              <Save size={14} />
              Save
            </button>
          </div>
        </div>
      </header>

      <div className="max-w-screen-2xl mx-auto px-6 py-4">
        <div className="grid grid-cols-12 gap-4">
          {/* Left Column - Configuration */}
          <div className="col-span-12 lg:col-span-5 space-y-4">
            {/* Strategy Configuration */}
            <div className="bg-white rounded-lg border border-gray-200 shadow-sm">
              <div className="px-4 py-3 border-b border-gray-100 flex items-center gap-2">
                <Calendar size={16} className="text-gray-400" />
                <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Configuration</h3>
              </div>
              <div className="p-4 space-y-4">
                {/* Strategy Type */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-2">Strategy Type</label>
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
                    {instruments.map(opt => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </div>

                {/* Underlying */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-2">Underlying</label>
                  <SegBtn
                    options={[
                      { value: 'cash', label: 'Cash' },
                      { value: 'futures', label: 'Futures' },
                    ]}
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
                        {daysOptions.map(d => <option key={d} value={d}>{d}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-2">Exit (days before expiry)</label>
                      <select
                        value={exitDaysBefore}
                        onChange={e => setExitDaysBefore(+e.target.value)}
                        className="w-full h-9 px-3 border border-gray-300 rounded text-sm bg-white"
                      >
                        {daysOptions.map(d => <option key={d} value={d}>{d}</option>)}
                      </select>
                    </div>
                  </div>
                )}

                {/* Date Range */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-2">From Date</label>
                    <input
                      type="date"
                      value={startDate}
                      onChange={e => setStartDate(e.target.value)}
                      className="w-full h-9 px-3 border border-gray-300 rounded text-sm bg-white"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-2">To Date</label>
                    <input
                      type="date"
                      value={endDate}
                      onChange={e => setEndDate(e.target.value)}
                      className="w-full h-9 px-3 border border-gray-300 rounded text-sm bg-white"
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Risk Management */}
            <div className="bg-white rounded-lg border border-gray-200 shadow-sm">
              <div className="px-4 py-3 border-b border-gray-100 flex items-center gap-2">
                <Shield size={16} className="text-gray-400" />
                <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Risk Management</h3>
              </div>
              <div className="p-4 space-y-4">
                {/* Stop Loss */}
                <div className="flex items-center justify-between">
                  <div>
                    <span className="text-sm font-medium text-gray-700">Overall Stop Loss</span>
                    <p className="text-xs text-gray-500">Exit all legs when total loss exceeds</p>
                  </div>
                  <Toggle enabled={overallSLEnabled} onToggle={() => setOverallSLEnabled(!overallSLEnabled)} />
                </div>
                
                {overallSLEnabled && (
                  <div className="grid grid-cols-2 gap-3">
                    <select
                      value={overallSLType}
                      onChange={e => setOverallSLType(e.target.value)}
                      className="h-9 px-3 border border-gray-300 rounded text-sm bg-white"
                    >
                      <option value="max_loss">Max Loss (₹)</option>
                      <option value="max_loss_pct">Max Loss (%)</option>
                    </select>
                    <input
                      type="number"
                      min="0"
                      value={overallSLValue}
                      onChange={e => setOverallSLValue(parseFloat(e.target.value))}
                      className="h-9 px-3 border border-gray-300 rounded text-sm"
                      placeholder="Enter value"
                    />
                  </div>
                )}

                {/* Target */}
                <div className="flex items-center justify-between pt-3 border-t border-gray-100">
                  <div>
                    <span className="text-sm font-medium text-gray-700">Overall Target</span>
                    <p className="text-xs text-gray-500">Exit all legs when total profit reaches</p>
                  </div>
                  <Toggle enabled={overallTgtEnabled} onToggle={() => setOverallTgtEnabled(!overallTgtEnabled)} />
                </div>

                {overallTgtEnabled && (
                  <div className="grid grid-cols-2 gap-3">
                    <select
                      value={overallTgtType}
                      onChange={e => setOverallTgtType(e.target.value)}
                      className="h-9 px-3 border border-gray-300 rounded text-sm bg-white"
                    >
                      <option value="max_profit">Max Profit (₹)</option>
                      <option value="max_profit_pct">Max Profit (%)</option>
                    </select>
                    <input
                      type="number"
                      min="0"
                      value={overallTgtValue}
                      onChange={e => setOverallTgtValue(parseFloat(e.target.value))}
                      className="h-9 px-3 border border-gray-300 rounded text-sm"
                      placeholder="Enter value"
                    />
                  </div>
                )}
              </div>
            </div>

            {/* Run Button */}
            <button
              onClick={handleRunBacktest}
              disabled={loading || legs.length === 0}
              className={`w-full flex items-center justify-center gap-2 py-3 px-4 rounded-lg font-medium transition-colors ${
                loading || legs.length === 0
                  ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                  : 'bg-blue-600 text-white hover:bg-blue-700'
              }`}
            >
              {loading ? (
                <>
                  <Loader2 size={18} className="animate-spin" />
                  Running Backtest...
                </>
              ) : (
                <>
                  <Play size={18} />
                  Run Backtest
                </>
              )}
            </button>

            {/* Error Display */}
            {error && (
              <div className="flex items-start gap-2 p-4 bg-red-50 border border-red-200 rounded-lg">
                <AlertCircle size={18} className="text-red-500 flex-shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-medium text-red-700">Error</p>
                  <p className="text-xs text-red-600 mt-1">{error}</p>
                </div>
              </div>
            )}
          </div>

          {/* Right Column - Leg Builder & Results */}
          <div className="col-span-12 lg:col-span-7 space-y-4">
            {/* Leg Builder */}
            <LegBuilder
              legs={legs}
              onLegsChange={setLegs}
              maxLegs={6}
              showAdvanced={true}
            />

            {/* Results Summary */}
            {results && (
              <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-sm font-semibold text-gray-700">Backtest Results</h3>
                  <span className="text-xs text-gray-500">{results.trades?.length || 0} trades</span>
                </div>
                
                {results.summary && (
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div className="p-3 bg-gray-50 rounded-lg">
                      <p className="text-xs text-gray-500">Total P&L</p>
                      <p className={`text-lg font-bold ${results.summary.total_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                        ₹{results.summary.total_pnl?.toLocaleString() || 0}
                      </p>
                    </div>
                    <div className="p-3 bg-gray-50 rounded-lg">
                      <p className="text-xs text-gray-500">Win Rate</p>
                      <p className="text-lg font-bold text-gray-800">
                        {results.summary.win_pct?.toFixed(1) || 0}%
                      </p>
                    </div>
                    <div className="p-3 bg-gray-50 rounded-lg">
                      <p className="text-xs text-gray-500">Max Drawdown</p>
                      <p className="text-lg font-bold text-red-600">
                        ₹{results.summary.max_dd_pts?.toLocaleString() || 0}
                      </p>
                    </div>
                    <div className="p-3 bg-gray-50 rounded-lg">
                      <p className="text-xs text-gray-500">Expectancy</p>
                      <p className={`text-lg font-bold ${results.summary.expectancy >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                        ₹{results.summary.expectancy?.toFixed(0) || 0}
                      </p>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Trades Table */}
            {results && results.trades && results.trades.length > 0 && (
              <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
                <div className="px-4 py-3 border-b border-gray-100">
                  <h3 className="text-sm font-semibold text-gray-700">Trade History</h3>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 text-gray-600">
                      <tr>
                        <th className="px-4 py-2 text-left font-medium">Entry Date</th>
                        <th className="px-4 py-2 text-left font-medium">Exit Date</th>
                        <th className="px-4 py-2 text-right font-medium">Entry Spot</th>
                        <th className="px-4 py-2 text-right font-medium">Exit Spot</th>
                        <th className="px-4 py-2 text-right font-medium">P&L</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {results.trades.slice(0, 10).map((trade, idx) => (
                        <tr key={idx} className="hover:bg-gray-50">
                          <td className="px-4 py-2 text-gray-700">{trade['Entry Date'] || '-'}</td>
                          <td className="px-4 py-2 text-gray-700">{trade['Exit Date'] || '-'}</td>
                          <td className="px-4 py-2 text-right text-gray-700">{trade['Entry Spot']?.toFixed(2) || '-'}</td>
                          <td className="px-4 py-2 text-right text-gray-700">{trade['Exit Spot']?.toFixed(2) || '-'}</td>
                          <td className={`px-4 py-2 text-right font-medium ${
                            (trade['Net P&L'] || 0) >= 0 ? 'text-green-600' : 'text-red-600'
                          }`}>
                            ₹{(trade['Net P&L'] || 0).toFixed(0)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {results.trades.length > 10 && (
                  <div className="px-4 py-2 text-center text-xs text-gray-500 border-t border-gray-100">
                    Showing 10 of {results.trades.length} trades
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default StrategyPage;
