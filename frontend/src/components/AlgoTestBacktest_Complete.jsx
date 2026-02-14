import React, { useState } from 'react';
import { Play, Save, Copy, Settings, Calendar, TrendingUp } from 'lucide-react';

const AlgoTestBacktest = () => {
  const [config, setConfig] = useState({
    index: 'NIFTY',
    from_date: '2024-01-01',
    to_date: '2024-12-31',
    expiry_type: 'WEEKLY',
    entry_dte: 2,
    exit_dte: 0,
    legs: []
  });

  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Add a leg
  const addLeg = () => {
    const newLeg = {
      id: Date.now(),
      segment: 'options',
      option_type: 'call',
      position: 'sell',
      lot: 1,
      expiry: 'weekly',
      strike_selection: {
        type: 'strike_type',
        strike_type: 'atm',
        strikes_away: 0
      }
    };
    setConfig({ ...config, legs: [...config.legs, newLeg] });
  };

  // Remove a leg
  const removeLeg = (index) => {
    const newLegs = config.legs.filter((_, i) => i !== index);
    setConfig({ ...config, legs: newLegs });
  };

  // Update leg
  const updateLeg = (index, field, value) => {
    const newLegs = [...config.legs];
    if (field.includes('.')) {
      // Nested field like strike_selection.strike_type
      const [parent, child] = field.split('.');
      newLegs[index][parent] = {
        ...newLegs[index][parent],
        [child]: value
      };
    } else {
      newLegs[index][field] = value;
    }
    setConfig({ ...config, legs: newLegs });
  };

  // Run backtest
  const runBacktest = async () => {
    if (config.legs.length === 0) {
      alert('Please add at least one leg to your strategy');
      return;
    }

    setLoading(true);
    setError(null);
    
    try {
      console.log('🚀 Sending backtest request:', config);
      
      const response = await fetch('http://localhost:8000/api/dynamic-backtest', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(config)
      });

      console.log('📡 Response status:', response.status);

      if (!response.ok) {
        const errorText = await response.text();
        console.error('❌ Error response:', errorText);
        throw new Error(`HTTP ${response.status}: ${errorText}`);
      }

      const data = await response.json();
      console.log('✅ Backend response:', data);
      console.log('📊 Trades count:', data.trades?.length);
      console.log('📈 Summary:', data.summary);
      console.log('🎯 First trade:', data.trades?.[0]);

      setResults(data);
    } catch (err) {
      console.error('💥 Backtest error:', err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="bg-white rounded-lg shadow-sm p-6 mb-6">
          <div className="flex justify-between items-center">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">Options Backtesting</h1>
              <p className="text-sm text-gray-500 mt-1">Build and test your options strategies</p>
            </div>
            <div className="flex gap-3">
              <button className="flex items-center gap-2 px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200">
                <Save size={16} />
                Save Strategy
              </button>
              <button className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
                <Copy size={16} />
                Load Template
              </button>
            </div>
          </div>
        </div>

        {/* Configuration */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
          {/* Left Panel - Basic Settings */}
          <div className="lg:col-span-1 space-y-6">
            {/* Index & Date Settings */}
            <div className="bg-white rounded-lg shadow-sm p-6">
              <h3 className="font-semibold text-gray-900 mb-4 flex items-center gap-2">
                <Settings size={18} />
                Basic Settings
              </h3>
              
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Index</label>
                  <select
                    value={config.index}
                    onChange={(e) => setConfig({ ...config, index: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                  >
                    <option value="NIFTY">NIFTY 50</option>
                    <option value="BANKNIFTY">BANK NIFTY</option>
                    <option value="FINNIFTY">FIN NIFTY</option>
                    <option value="MIDCPNIFTY">MIDCAP NIFTY</option>
                    <option value="SENSEX">SENSEX</option>
                  </select>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">From Date</label>
                  <input
                    type="date"
                    value={config.from_date}
                    onChange={(e) => setConfig({ ...config, from_date: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">To Date</label>
                  <input
                    type="date"
                    value={config.to_date}
                    onChange={(e) => setConfig({ ...config, to_date: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                  />
                </div>
              </div>
            </div>

            {/* DTE Settings */}
            <div className="bg-white rounded-lg shadow-sm p-6">
              <h3 className="font-semibold text-gray-900 mb-4 flex items-center gap-2">
                <Calendar size={18} />
                DTE Filter
              </h3>
              
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Expiry Type</label>
                  <div className="grid grid-cols-2 gap-2">
                    <button
                      onClick={() => setConfig({ ...config, expiry_type: 'WEEKLY' })}
                      className={`py-2 px-4 rounded-lg font-medium ${
                        config.expiry_type === 'WEEKLY'
                          ? 'bg-blue-600 text-white'
                          : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                      }`}
                    >
                      Weekly
                    </button>
                    <button
                      onClick={() => setConfig({ ...config, expiry_type: 'MONTHLY' })}
                      className={`py-2 px-4 rounded-lg font-medium ${
                        config.expiry_type === 'MONTHLY'
                          ? 'bg-blue-600 text-white'
                          : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                      }`}
                    >
                      Monthly
                    </button>
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Entry DTE (Days Before Expiry)
                  </label>
                  <select
                    value={config.entry_dte}
                    onChange={(e) => setConfig({ ...config, entry_dte: parseInt(e.target.value) })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                  >
                    {[...Array(config.expiry_type === 'WEEKLY' ? 5 : 25)].map((_, i) => (
                      <option key={i} value={i}>
                        {i === 0 ? '0 (Expiry Day)' : i}
                      </option>
                    ))}
                  </select>
                  <p className="mt-1 text-xs text-gray-500">
                    Entry {config.entry_dte} trading day(s) before expiry
                  </p>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Exit DTE (Days Before Expiry)
                  </label>
                  <select
                    value={config.exit_dte}
                    onChange={(e) => setConfig({ ...config, exit_dte: parseInt(e.target.value) })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                  >
                    {[...Array(config.expiry_type === 'WEEKLY' ? 5 : 25)].map((_, i) => (
                      <option key={i} value={i}>
                        {i === 0 ? '0 (At Expiry)' : i}
                      </option>
                    ))}
                  </select>
                  <p className="mt-1 text-xs text-gray-500">
                    Exit {config.exit_dte} trading day(s) before expiry
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Right Panel - Leg Builder */}
          <div className="lg:col-span-2">
            <div className="bg-white rounded-lg shadow-sm p-6">
              <div className="flex justify-between items-center mb-4">
                <h3 className="font-semibold text-gray-900">Strategy Legs</h3>
                <button
                  onClick={addLeg}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center gap-2"
                >
                  + Add Leg
                </button>
              </div>

              {config.legs.length === 0 ? (
                <div className="text-center py-12 text-gray-500">
                  <p className="text-lg mb-2">No legs added yet</p>
                  <p className="text-sm">Click "Add Leg" to start building your strategy</p>
                </div>
              ) : (
                <div className="space-y-4">
                  {config.legs.map((leg, index) => (
                    <div key={leg.id} className="border border-gray-200 rounded-lg p-4">
                      <div className="flex justify-between items-center mb-4">
                        <h4 className="font-medium text-gray-900">Leg {index + 1}</h4>
                        <button
                          onClick={() => removeLeg(index)}
                          className="px-3 py-1 bg-red-100 text-red-600 rounded hover:bg-red-200 text-sm"
                        >
                          Remove
                        </button>
                      </div>

                      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                        {/* Segment */}
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">Segment</label>
                          <select
                            value={leg.segment}
                            onChange={(e) => updateLeg(index, 'segment', e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                          >
                            <option value="options">Options</option>
                            <option value="futures">Futures</option>
                          </select>
                        </div>

                        {/* Position */}
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">Position</label>
                          <select
                            value={leg.position}
                            onChange={(e) => updateLeg(index, 'position', e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                          >
                            <option value="buy">Buy</option>
                            <option value="sell">Sell</option>
                          </select>
                        </div>

                        {/* Lot */}
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">Lot</label>
                          <input
                            type="number"
                            min="1"
                            value={leg.lot}
                            onChange={(e) => updateLeg(index, 'lot', parseInt(e.target.value))}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                          />
                        </div>

                        {/* Options-specific fields */}
                        {leg.segment === 'options' && (
                          <>
                            <div>
                              <label className="block text-sm font-medium text-gray-700 mb-1">Option Type</label>
                              <select
                                value={leg.option_type}
                                onChange={(e) => updateLeg(index, 'option_type', e.target.value)}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                              >
                                <option value="call">Call (CE)</option>
                                <option value="put">Put (PE)</option>
                              </select>
                            </div>

                            <div>
                              <label className="block text-sm font-medium text-gray-700 mb-1">Expiry</label>
                              <select
                                value={leg.expiry}
                                onChange={(e) => updateLeg(index, 'expiry', e.target.value)}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                              >
                                <option value="weekly">Weekly</option>
                                <option value="monthly">Monthly</option>
                              </select>
                            </div>

                            <div>
                              <label className="block text-sm font-medium text-gray-700 mb-1">Strike</label>
                              <select
                                value={leg.strike_selection.strike_type}
                                onChange={(e) => updateLeg(index, 'strike_selection.strike_type', e.target.value)}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                              >
                                <optgroup label="In The Money">
                                  {[5,4,3,2,1].map(i => (
                                    <option key={`itm${i}`} value={`itm${i}`}>ITM {i}</option>
                                  ))}
                                </optgroup>
                                <option value="atm">ATM</option>
                                <optgroup label="Out of The Money">
                                  {[1,2,3,4,5,6,7,8,9,10].map(i => (
                                    <option key={`otm${i}`} value={`otm${i}`}>OTM {i}</option>
                                  ))}
                                </optgroup>
                              </select>
                            </div>
                          </>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Run Backtest Button */}
        <div className="bg-white rounded-lg shadow-sm p-6 mb-6">
          <button
            onClick={runBacktest}
            disabled={loading || config.legs.length === 0}
            className={`w-full py-4 rounded-lg font-semibold text-lg flex items-center justify-center gap-3 ${
              loading || config.legs.length === 0
                ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                : 'bg-blue-600 text-white hover:bg-blue-700'
            }`}
          >
            {loading ? (
              <>
                <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-white"></div>
                Running Backtest...
              </>
            ) : (
              <>
                <Play size={24} />
                Run Backtest
              </>
            )}
          </button>
          {config.legs.length === 0 && (
            <p className="text-center text-sm text-gray-500 mt-2">
              Add at least one leg to run backtest
            </p>
          )}
        </div>

        {/* Error Display */}
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
            <p className="text-red-800 font-medium">Error:</p>
            <p className="text-red-600 text-sm mt-1">{error}</p>
          </div>
        )}

        {/* Results Display */}
        {results && (
          <ResultsDisplay results={results} onClose={() => setResults(null)} />
        )}
      </div>
    </div>
  );
};

// Results Display Component
const ResultsDisplay = ({ results, onClose }) => {
  const { trades = [], summary = {}, pivot = {} } = results;

  // Helper functions
  const getTradePnL = (trade) => {
    return trade.net_pnl || trade.NetPnL || trade.pnl || trade.PnL || 0;
  };

  const getCumulative = (trade) => {
    return trade.cumulative || trade.Cumulative || 0;
  };

  // Calculate stats
  const stats = {
    totalPnL: summary.total_pnl || 0,
    totalTrades: summary.count || trades.length,
    winRate: summary.win_pct || 0,
    cagr: summary.cagr_options || 0,
    maxDD: summary.max_dd_pct || 0,
    carMdd: summary.car_mdd || 0,
    avgWin: summary.avg_win || 0,
    avgLoss: summary.avg_loss || 0
  };

  // Export to CSV
  const exportCSV = () => {
    if (!trades || trades.length === 0) return;
    
    const headers = Object.keys(trades[0]);
    const csv = [
      headers.join(','),
      ...trades.map(trade => 
        headers.map(h => trade[h]).join(',')
      )
    ].join('\n');
    
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `backtest_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
  };

  return (
    <div className="bg-white rounded-lg shadow-lg p-6">
      {/* Header */}
      <div className="flex justify-between items-center mb-6 pb-4 border-b">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Backtest Results</h2>
          <p className="text-sm text-gray-500 mt-1">
            {stats.totalTrades} trades from {results.meta?.date_range}
          </p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={exportCSV}
            className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700"
          >
            Export CSV
          </button>
          <button
            onClick={onClose}
            className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300"
          >
            Close
          </button>
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4 mb-6">
        <KPICard 
          title="Total P&L" 
          value={`₹${stats.totalPnL.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`}
          positive={stats.totalPnL >= 0}
        />
        <KPICard 
          title="Win Rate" 
          value={`${stats.winRate.toFixed(1)}%`}
        />
        <KPICard 
          title="CAGR" 
          value={`${stats.cagr.toFixed(1)}%`}
        />
        <KPICard 
          title="Max DD" 
          value={`${Math.abs(stats.maxDD).toFixed(1)}%`}
          positive={false}
        />
        <KPICard 
          title="CAR/MDD" 
          value={stats.carMdd.toFixed(2)}
        />
        <KPICard 
          title="Total Trades" 
          value={stats.totalTrades}
        />
      </div>

      {/* Trade Table */}
      <div className="overflow-x-auto">
        <h3 className="font-semibold text-gray-900 mb-3">Trade Log (Latest 50)</h3>
        <table className="min-w-full border border-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">#</th>
              {trades.length > 0 && Object.keys(trades[0]).slice(0, 10).map(key => (
                <th key={key} className="px-4 py-2 text-left text-xs font-medium text-gray-500">
                  {key.replace(/_/g, ' ').toUpperCase()}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-200">
            {trades.slice(0, 50).map((trade, idx) => (
              <tr key={idx} className="hover:bg-gray-50">
                <td className="px-4 py-2 text-sm text-gray-500">{idx + 1}</td>
                {Object.values(trade).slice(0, 10).map((value, cellIdx) => (
                  <td key={cellIdx} className="px-4 py-2 text-sm text-gray-900">
                    {typeof value === 'number' ? value.toFixed(2) : value || '-'}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Full Summary */}
      <div className="mt-6 pt-6 border-t">
        <h3 className="font-semibold text-gray-900 mb-4">Complete Statistics</h3>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          {Object.entries(summary).map(([key, value]) => (
            <div key={key} className="border-l-4 border-blue-500 pl-3 py-2">
              <p className="text-xs text-gray-500 uppercase">{key.replace(/_/g, ' ')}</p>
              <p className="text-lg font-semibold text-gray-900">
                {typeof value === 'number' ? value.toFixed(2) : value}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

// KPI Card Component
const KPICard = ({ title, value, positive = true }) => {
  const colorClass = positive 
    ? (parseFloat(value) >= 0 ? 'text-green-600' : 'text-red-600')
    : 'text-gray-900';

  return (
    <div className="bg-gradient-to-br from-white to-gray-50 border border-gray-200 rounded-lg p-4">
      <p className="text-xs text-gray-500 font-medium mb-1">{title}</p>
      <p className={`text-xl font-bold ${colorClass}`}>{value}</p>
    </div>
  );
};

export default AlgoTestBacktest;
