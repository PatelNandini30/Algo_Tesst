import React, { useState } from 'react';
import { Play, Save, Copy, Settings, Calendar, TrendingUp } from 'lucide-react';

// ─── CHANGE 1 ───────────────────────────────────────────────────────────────
// REMOVED: static LOT_SIZES = { NIFTY: 75, BANKNIFTY: 15, ... }
//   Problem: NIFTY:75 was applied to ALL years but NSE changed lot sizes 4x.
//   For 2000-2026 data this was wrong for every period except Oct2015–Oct2019.
// ADDED: date-aware getLotSize(index, tradeDate) — mirrors backend get_lot_size()
//   NIFTY:     2000–Sep2010=200 | Oct2010–Oct2015=50 | Oct2015–Oct2019=75 | Nov2019+=50
//   BANKNIFTY: 2000–Sep2010=50  | Oct2010–Oct2015=25 | Oct2015–Oct2019=20 | Nov2019+=15
// ────────────────────────────────────────────────────────────────────────────
const getLotSize = (index, tradeDate) => {
  const d = new Date(tradeDate);
  if (index === 'NIFTY') {
    if (d < new Date('2010-10-01')) return 200;
    if (d < new Date('2015-10-29')) return 50;
    if (d < new Date('2019-11-01')) return 75;
    return 50;
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

  // ─── CHANGE 2 ─────────────────────────────────────────────────────────────
  // ADDED: currentLotSize derived from from_date so all display calculations
  // use the correct lot size for the selected period.
  // Old: LOT_SIZES[config.index] → always 75 for NIFTY regardless of date.
  // ────────────────────────────────────────────────────────────────────────────
  const currentLotSize = getLotSize(config.index, config.from_date);

  const addLeg = () => {
    const newLeg = {
      id: Date.now(),
      segment: 'options',
      option_type: 'call',
      position: 'sell',
      lot: 1,
      expiry: 'weekly',
      strike_selection: { type: 'strike_type', strike_type: 'atm', strikes_away: 0 }
    };
    setConfig({ ...config, legs: [...config.legs, newLeg] });
  };

  const removeLeg = (index) => {
    setConfig({ ...config, legs: config.legs.filter((_, i) => i !== index) });
  };

  const updateLeg = (index, field, value) => {
    const newLegs = [...config.legs];
    if (field.includes('.')) {
      const [parent, child] = field.split('.');
      newLegs[index][parent] = { ...newLegs[index][parent], [child]: value };
    } else {
      newLegs[index][field] = value;
    }
    setConfig({ ...config, legs: newLegs });
  };

  const runBacktest = async () => {
    if (config.legs.length === 0) {
      alert('Please add at least one leg to your strategy');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const configForBackend = {
        ...config,
        legs: config.legs.map(leg => ({ ...leg, lots: leg.lot || leg.lots || 1 }))
      };
      const response = await fetch('http://localhost:8000/api/dynamic-backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(configForBackend)
      });
      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`HTTP ${response.status}: ${errorText}`);
      }
      setResults(await response.json());
    } catch (err) {
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
              {/* ─── CHANGE 3 ─────────────────────────────────────────────────
                  ADDED: lot-size badge in header using getLotSize(from_date).
                  Old: nothing / wrong static value shown. */}
              <div className="text-right mr-2">
                <p className="text-xs text-gray-400">Lot size for {config.from_date.slice(0, 4)}</p>
                <p className="text-sm font-semibold text-blue-700">{config.index}: {currentLotSize} units/lot</p>
              </div>
              <button className="flex items-center gap-2 px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200">
                <Save size={16} /> Save Strategy
              </button>
              <button className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
                <Copy size={16} /> Load Template
              </button>
            </div>
          </div>
        </div>

        {/* Configuration */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
          {/* Left Panel */}
          <div className="lg:col-span-1 space-y-6">
            {/* Basic Settings */}
            <div className="bg-white rounded-lg shadow-sm p-6">
              <h3 className="font-semibold text-gray-900 mb-4 flex items-center gap-2">
                <Settings size={18} /> Basic Settings
              </h3>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Index</label>
                  <select value={config.index}
                    onChange={(e) => setConfig({ ...config, index: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500">
                    <option value="NIFTY">NIFTY 50</option>
                    <option value="BANKNIFTY">BANK NIFTY</option>
                    <option value="FINNIFTY">FIN NIFTY</option>
                    <option value="MIDCPNIFTY">MIDCAP NIFTY</option>
                    <option value="SENSEX">SENSEX</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">From Date</label>
                  <input type="date" value={config.from_date}
                    onChange={(e) => setConfig({ ...config, from_date: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">To Date</label>
                  <input type="date" value={config.to_date}
                    onChange={(e) => setConfig({ ...config, to_date: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500" />
                </div>
              </div>
            </div>

            {/* DTE Settings */}
            <div className="bg-white rounded-lg shadow-sm p-6">
              <h3 className="font-semibold text-gray-900 mb-4 flex items-center gap-2">
                <Calendar size={18} /> DTE Filter
              </h3>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Expiry Type</label>
                  <div className="grid grid-cols-2 gap-2">
                    <button onClick={() => setConfig({ ...config, expiry_type: 'WEEKLY' })}
                      className={`py-2 px-4 rounded-lg font-medium ${config.expiry_type === 'WEEKLY' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
                      Weekly
                    </button>
                    <button onClick={() => setConfig({ ...config, expiry_type: 'MONTHLY' })}
                      className={`py-2 px-4 rounded-lg font-medium ${config.expiry_type === 'MONTHLY' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
                      Monthly
                    </button>
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Entry DTE</label>
                  <select value={config.entry_dte}
                    onChange={(e) => setConfig({ ...config, entry_dte: parseInt(e.target.value) })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500">
                    {[...Array(config.expiry_type === 'WEEKLY' ? 5 : 25)].map((_, i) => (
                      <option key={i} value={i}>{i === 0 ? '0 (Expiry Day)' : i}</option>
                    ))}
                  </select>
                  <p className="mt-1 text-xs text-gray-500">Entry {config.entry_dte} trading day(s) before expiry</p>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Exit DTE</label>
                  <select value={config.exit_dte}
                    onChange={(e) => setConfig({ ...config, exit_dte: parseInt(e.target.value) })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500">
                    {[...Array(config.expiry_type === 'WEEKLY' ? 5 : 25)].map((_, i) => (
                      <option key={i} value={i}>{i === 0 ? '0 (At Expiry)' : i}</option>
                    ))}
                  </select>
                  <p className="mt-1 text-xs text-gray-500">Exit {config.exit_dte} trading day(s) before expiry</p>
                </div>
              </div>
            </div>
          </div>

          {/* Right Panel — Leg Builder */}
          <div className="lg:col-span-2">
            <div className="bg-white rounded-lg shadow-sm p-6">
              <div className="flex justify-between items-center mb-4">
                <h3 className="font-semibold text-gray-900">Strategy Legs</h3>
                <button onClick={addLeg}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center gap-2">
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
                        <div className="flex items-center gap-3">
                          <h4 className="font-medium text-gray-900">Leg {index + 1}</h4>
                          {/* ─── CHANGE 4 ───────────────────────────────────
                              ADDED: per-leg unit count getLotSize(from_date).
                              Old: nothing shown / wrong static value. */}
                          <span className="text-xs text-blue-600 font-medium">
                            {leg.lot * getLotSize(config.index, config.from_date)} units
                          </span>
                        </div>
                        <button onClick={() => removeLeg(index)}
                          className="px-3 py-1 bg-red-100 text-red-600 rounded hover:bg-red-200 text-sm">
                          Remove
                        </button>
                      </div>

                      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">Segment</label>
                          <select value={leg.segment} onChange={(e) => updateLeg(index, 'segment', e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm">
                            <option value="options">Options</option>
                            <option value="futures">Futures</option>
                          </select>
                        </div>

                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">Position</label>
                          <select value={leg.position} onChange={(e) => updateLeg(index, 'position', e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm">
                            <option value="buy">Buy</option>
                            <option value="sell">Sell</option>
                          </select>
                        </div>

                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">Lot</label>
                          <input type="number" min="1" value={leg.lot}
                            onChange={(e) => updateLeg(index, 'lot', parseInt(e.target.value) || 1)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" />
                        </div>

                        {leg.segment === 'options' && (
                          <>
                            <div>
                              <label className="block text-sm font-medium text-gray-700 mb-1">Option Type</label>
                              <select value={leg.option_type} onChange={(e) => updateLeg(index, 'option_type', e.target.value)}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm">
                                <option value="call">Call (CE)</option>
                                <option value="put">Put (PE)</option>
                              </select>
                            </div>

                            <div>
                              <label className="block text-sm font-medium text-gray-700 mb-1">Expiry</label>
                              <select value={leg.expiry} onChange={(e) => updateLeg(index, 'expiry', e.target.value)}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm">
                                <option value="weekly">Weekly</option>
                                <option value="monthly">Monthly</option>
                              </select>
                            </div>

                            <div>
                              <label className="block text-sm font-medium text-gray-700 mb-1">Strike</label>
                              <select value={leg.strike_selection.strike_type}
                                onChange={(e) => updateLeg(index, 'strike_selection.strike_type', e.target.value)}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm">
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
          <button onClick={runBacktest} disabled={loading || config.legs.length === 0}
            className={`w-full py-4 rounded-lg font-semibold text-lg flex items-center justify-center gap-3 ${
              loading || config.legs.length === 0 ? 'bg-gray-300 text-gray-500 cursor-not-allowed' : 'bg-blue-600 text-white hover:bg-blue-700'}`}>
            {loading ? (
              <><div className="animate-spin rounded-full h-6 w-6 border-b-2 border-white"></div>Running Backtest...</>
            ) : (
              <><Play size={24} />Run Backtest</>
            )}
          </button>
          {config.legs.length === 0 && (
            <p className="text-center text-sm text-gray-500 mt-2">Add at least one leg to run backtest</p>
          )}
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
            <p className="text-red-800 font-medium">Error:</p>
            <p className="text-red-600 text-sm mt-1">{error}</p>
          </div>
        )}

        {results &&         const ResultsDisplay = ({ results, index, fromDate, onClose }) => {
          const { trades = [], summary = {} } = results;
          const [currentPage, setCurrentPage] = useState(1);
          const itemsPerPage = 10;

          const totalPages = Math.ceil(trades.length / itemsPerPage);
          const startIndex = (currentPage - 1) * itemsPerPage;
          const currentTrades = trades.slice(startIndex, startIndex + itemsPerPage);

          const exportCSV = () => {
            if (!trades || trades.length === 0) return;
            const headers = Object.keys(trades[0]);
            const csv = [headers.join(','), ...trades.map(t => headers.map(h => t[h]).join(','))].join('\n');
            const blob = new Blob([csv], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `backtest_${new Date().toISOString().split('T')[0]}.csv`;
            a.click();
          };

          return (
            <div className="bg-white rounded-lg shadow-lg">
              {/* Header */}
              <div className="flex justify-between items-center px-6 py-4 border-b bg-gray-50">
                <h2 className="text-xl font-bold text-gray-900">Full Report</h2>
                <div className="flex items-center gap-4">
                  <div className="flex items-center gap-2">
                    <label className="text-sm text-gray-600">Sort By</label>
                    <select className="px-3 py-1 border border-gray-300 rounded text-sm">
                      <option>Entry date</option>
                    </select>
                  </div>
                  <button className="px-4 py-1 bg-blue-600 text-white rounded text-sm hover:bg-blue-700">Asc</button>
                  <button className="px-4 py-1 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300">Desc</button>
                  <div className="text-sm text-gray-600">
                    Showing <span className="font-semibold">{startIndex + 1} - {Math.min(startIndex + itemsPerPage, trades.length)}</span> trades out of <span className="font-semibold">{trades.length}</span>
                  </div>
                </div>
              </div>

              {/* Main Trade Table */}
              <div className="overflow-x-auto">
                <table className="min-w-full">
                  <thead>
                    <tr className="bg-gray-200 border-b-2 border-gray-400">
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Index</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Entry Date</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Entry Time</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Exit Date</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Exit Time</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Type</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Strike</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">B/S</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Qty</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Entry Price</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Exit Price</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Via</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">P/L</th>
                    </tr>
                  </thead>
                  <tbody className="bg-white">
                    {currentTrades.map((trade, idx) => {
                      const pnlValue = trade.pnl || trade.net_pnl || 0;
                      const isProfit = pnlValue >= 0;
                      return (
                        <tr key={idx} className={`border-b border-gray-200 ${idx % 2 === 0 ? 'bg-white' : 'bg-gray-50'}`}>
                          <td className="px-3 py-2 text-sm text-gray-900">{startIndex + idx + 1}</td>
                          <td className="px-3 py-2 text-sm text-gray-900">{trade.entry_date || '-'}</td>
                          <td className="px-3 py-2 text-sm text-gray-900">{trade.entry_time || '-'}</td>
                          <td className="px-3 py-2 text-sm text-gray-900">{trade.exit_date || '-'}</td>
                          <td className="px-3 py-2 text-sm text-gray-900">{trade.exit_time || '-'}</td>
                          <td className="px-3 py-2 text-sm text-gray-900">{trade.type || trade.option_type || '-'}</td>
                          <td className="px-3 py-2 text-sm text-gray-900">{trade.strike || '-'}</td>
                          <td className="px-3 py-2 text-sm text-gray-900">{trade.position || trade.bs || '-'}</td>
                          <td className="px-3 py-2 text-sm text-gray-900">{trade.qty || trade.quantity || '-'}</td>
                          <td className="px-3 py-2 text-sm text-gray-900">{typeof trade.entry_price === 'number' ? trade.entry_price.toFixed(2) : '-'}</td>
                          <td className="px-3 py-2 text-sm text-gray-900">{typeof trade.exit_price === 'number' ? trade.exit_price.toFixed(2) : '-'}</td>
                          <td className="px-3 py-2 text-sm text-gray-900">{trade.via || '-'}</td>
                          <td className={`px-3 py-2 text-sm font-semibold ${isProfit ? 'text-green-600' : 'text-red-600'}`}>
                            {pnlValue.toFixed(2)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              <div className="flex justify-center items-center gap-2 px-6 py-4 border-t bg-gray-50">
                <button onClick={() => setCurrentPage(1)} disabled={currentPage === 1} className="px-2 py-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-30">≪</button>
                <button onClick={() => setCurrentPage(p => Math.max(1, p - 1))} disabled={currentPage === 1} className="px-2 py-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-30">‹</button>
                {[...Array(Math.min(6, totalPages))].map((_, i) => {
                  let pageNum = i + 1;
                  if (totalPages > 6) {
                    if (currentPage <= 3) pageNum = i + 1;
                    else if (currentPage >= totalPages - 2) pageNum = totalPages - 5 + i;
                    else pageNum = currentPage - 2 + i;
                  }
                  return (
                    <button key={i} onClick={() => setCurrentPage(pageNum)} className={`px-3 py-1 text-sm rounded ${currentPage === pageNum ? 'bg-blue-600 text-white' : 'bg-white text-gray-700 hover:bg-gray-100 border border-gray-300'}`}>
                      {pageNum}
                    </button>
                  );
                })}
                <button onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))} disabled={currentPage === totalPages} className="px-2 py-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-30">›</button>
                <button onClick={() => setCurrentPage(totalPages)} disabled={currentPage === totalPages} className="px-2 py-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-30">≫</button>
              </div>

              {/* Trade Log Section - Detailed View */}
              <div className="px-6 py-6 border-t bg-white">
                <h3 className="text-lg font-bold text-gray-900 mb-4">Trade Log (Latest 50)</h3>
                <div className="overflow-x-auto">
                  <table className="min-w-full">
                    <thead>
                      <tr className="bg-gray-200 border-b-2 border-gray-400">
                        <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Entry Date</th>
                        <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Exit Date</th>
                        <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Entry Spot Price</th>
                        <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Exit Spot Price</th>
                        <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Spot P&L</th>
                        <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Future Expiry</th>
                        <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Net P&L</th>
                        <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Option Type</th>
                        <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Strike Price</th>
                        <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Entry Premium</th>
                      </tr>
                    </thead>
                    <tbody className="bg-white">
                      {trades.slice(0, 50).map((trade, idx) => {
                        const netPnl = trade.net_pnl || trade.pnl || 0;
                        const isProfit = netPnl >= 0;
                        return (
                          <tr key={idx} className={`border-b border-gray-200 ${idx % 2 === 0 ? 'bg-white' : 'bg-gray-50'}`}>
                            <td className="px-3 py-2 text-sm text-gray-900">{trade.entry_date || '-'}</td>
                            <td className="px-3 py-2 text-sm text-gray-900">{trade.exit_date || '-'}</td>
                            <td className="px-3 py-2 text-sm text-gray-900">{trade.entry_spot || '-'}</td>
                            <td className="px-3 py-2 text-sm text-gray-900">{trade.exit_spot || '-'}</td>
                            <td className="px-3 py-2 text-sm text-gray-900">{trade.spot_pnl || '-'}</td>
                            <td className="px-3 py-2 text-sm text-gray-900">{trade.future_expiry || '-'}</td>
                            <td className={`px-3 py-2 text-sm font-semibold ${isProfit ? 'text-green-600' : 'text-red-600'}`}>
                              {netPnl.toFixed(2)}
                            </td>
                            <td className="px-3 py-2 text-sm text-gray-900">{trade.leg1_type || trade.type || '-'}</td>
                            <td className="px-3 py-2 text-sm text-gray-900">{trade.leg1_strike || trade.strike || '-'}</td>
                            <td className="px-3 py-2 text-sm text-gray-900">{trade.leg1_entry || trade.entry_price || '-'}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex justify-between items-center px-6 py-4 border-t bg-gray-50">
                <button className="px-4 py-2 bg-white border border-gray-300 text-gray-700 rounded hover:bg-gray-50">Download Report</button>
                <div className="flex gap-3">
                  <button onClick={exportCSV} className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700">Export CSV</button>
                  <button onClick={onClose} className="px-4 py-2 bg-gray-600 text-white rounded hover:bg-gray-700">Close</button>
                </div>
              </div>
            </div>
          );
        }
}
      </div>
    </div>
  );
};

const ResultsDisplay = ({ results, index, fromDate, onClose }) => {
  const { trades = [], summary = {} } = results;
  const [currentPage, setCurrentPage] = useState(1);
  const [sortConfig, setSortConfig] = useState({ key: null, direction: 'asc' });
  const itemsPerPage = 10;

  const stats = {
    totalPnL:    summary.total_pnl    || 0,
    totalTrades: summary.count        || trades.length,
    winRate:     summary.win_pct      || 0,
    cagr:        summary.cagr_options || 0,
    maxDD:       summary.max_dd_pct   || 0,
    carMdd:      summary.car_mdd      || 0,
    avgWin:      summary.avg_win      || 0,
    avgLoss:     summary.avg_loss     || 0,
    avgProfitPerTrade: summary.avg_profit_per_trade || 0,
    rewardToRisk:      summary.reward_to_risk       || 0,
    maxWinStreak:      summary.max_win_streak       || 0,
    maxLossStreak:     summary.max_loss_streak      || 0,
  };

  const sortedTrades = React.useMemo(() => {
    let sortable = [...trades];
    if (sortConfig.key) {
      sortable.sort((a, b) => {
        if (a[sortConfig.key] < b[sortConfig.key]) return sortConfig.direction === 'asc' ? -1 : 1;
        if (a[sortConfig.key] > b[sortConfig.key]) return sortConfig.direction === 'asc' ? 1 : -1;
        return 0;
      });
    }
    return sortable;
  }, [trades, sortConfig]);

  const totalPages = Math.ceil(sortedTrades.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const currentTrades = sortedTrades.slice(startIndex, startIndex + itemsPerPage);

  const requestSort = (key) => {
    let direction = 'asc';
    if (sortConfig.key === key && sortConfig.direction === 'asc') {
      direction = 'desc';
    }
    setSortConfig({ key, direction });
  };

  const exportCSV = () => {
    if (!trades || trades.length === 0) return;
    const headers = Object.keys(trades[0]);
    const csv = [headers.join(','), ...trades.map(t => headers.map(h => t[h]).join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `backtest_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
  };

  return (
    <div className="bg-white rounded-lg shadow-lg">
      {/* Header */}
      <div className="flex justify-between items-center px-6 py-4 border-b bg-gray-50">
        <div>
          <h2 className="text-xl font-bold text-gray-900">Full Report</h2>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <label className="text-sm text-gray-600">Sort By</label>
            <select className="px-3 py-1 border border-gray-300 rounded text-sm">
              <option>Entry date</option>
            </select>
          </div>
          <button className="px-4 py-1 bg-blue-600 text-white rounded text-sm hover:bg-blue-700">Add</button>
          <button className="px-4 py-1 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300">Desc</button>
          <div className="text-sm text-gray-600">
            Showing <span className="font-semibold">{startIndex + 1} - {Math.min(startIndex + itemsPerPage, sortedTrades.length)}</span> trades out of <span className="font-semibold">{sortedTrades.length}</span>
          </div>
        </div>
      </div>

      {/* Trade Table - Stadium Style */}
      <div className="overflow-x-auto">
        <table className="min-w-full">
          <thead>
            <tr className="bg-gray-100 border-b border-gray-300">
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 cursor-pointer" onClick={() => requestSort('index')}>Index</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 cursor-pointer" onClick={() => requestSort('entry_date')}>Entry Date</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 cursor-pointer" onClick={() => requestSort('entry_time')}>Entry Time</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 cursor-pointer" onClick={() => requestSort('exit_date')}>Exit Date</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 cursor-pointer" onClick={() => requestSort('exit_time')}>Exit Time</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 cursor-pointer" onClick={() => requestSort('type')}>Type</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 cursor-pointer" onClick={() => requestSort('strike')}>Strike</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 cursor-pointer" onClick={() => requestSort('position')}>B/S</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 cursor-pointer" onClick={() => requestSort('qty')}>Qty</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 cursor-pointer" onClick={() => requestSort('entry_price')}>Entry Price</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 cursor-pointer" onClick={() => requestSort('exit_price')}>Exit Price</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 cursor-pointer" onClick={() => requestSort('via')}>Via</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 cursor-pointer" onClick={() => requestSort('pnl')}>P/L</th>
            </tr>
          </thead>
          <tbody className="bg-white">
            {currentTrades.map((trade, idx) => {
              const isProfit = (trade.pnl || trade.net_pnl || 0) >= 0;
              const pnlValue = trade.pnl || trade.net_pnl || 0;
              return (
                <tr key={idx} className={`border-b border-gray-200 ${idx % 2 === 0 ? 'bg-white' : 'bg-gray-50'} hover:bg-blue-50`}>
                  <td className="px-4 py-3 text-sm text-gray-900">{startIndex + idx + 1}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{trade.entry_date || '-'}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{trade.entry_time || '-'}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{trade.exit_date || '-'}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{trade.exit_time || '-'}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{trade.type || trade.option_type || '-'}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{trade.strike || '-'}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{trade.position || trade.bs || '-'}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{trade.qty || trade.quantity || '-'}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{typeof trade.entry_price === 'number' ? trade.entry_price.toFixed(2) : '-'}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{typeof trade.exit_price === 'number' ? trade.exit_price.toFixed(2) : '-'}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{trade.via || '-'}</td>
                  <td className={`px-4 py-3 text-sm font-semibold ${isProfit ? 'text-green-600' : 'text-red-600'}`}>
                    {isProfit ? '' : '-'}{Math.abs(pnlValue).toFixed(2)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex justify-center items-center gap-2 px-6 py-4 border-t bg-gray-50">
        <button 
          onClick={() => setCurrentPage(1)} 
          disabled={currentPage === 1}
          className="px-2 py-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-30"
        >
          ≪
        </button>
        <button 
          onClick={() => setCurrentPage(p => Math.max(1, p - 1))} 
          disabled={currentPage === 1}
          className="px-2 py-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-30"
        >
          ‹
        </button>
        {[...Array(Math.min(6, totalPages))].map((_, i) => {
          let pageNum;
          if (totalPages <= 6) {
            pageNum = i + 1;
          } else if (currentPage <= 3) {
            pageNum = i + 1;
          } else if (currentPage >= totalPages - 2) {
            pageNum = totalPages - 5 + i;
          } else {
            pageNum = currentPage - 2 + i;
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
          onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))} 
          disabled={currentPage === totalPages}
          className="px-2 py-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-30"
        >
          ›
        </button>
        <button 
          onClick={() => setCurrentPage(totalPages)} 
          disabled={currentPage === totalPages}
          className="px-2 py-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-30"
        >
          ≫
        </button>
      </div>

      {/* Trade Log Section */}
      <div className="px-6 py-4 border-t">
        <h3 className="text-lg font-bold text-gray-900 mb-4">Trade Log (Latest 50)</h3>
        <div className="overflow-x-auto">
          <table className="min-w-full">
            <thead>
              <tr className="bg-gray-100 border-b border-gray-300">
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700">ENTRY DATE</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700">EXIT DATE</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700">ENTRY SPOT</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700">EXIT SPOT</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700">SPOT P&L</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700">FUTURE EXPIRY</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700">NET P&L</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700">LEG 1 TYPE</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700">LEG 1 STRIKE</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700">LEG 1 ENTRY</th>
              </tr>
            </thead>
            <tbody className="bg-white">
              {sortedTrades.slice(0, 50).map((trade, idx) => {
                const netPnl = trade.net_pnl || trade.pnl || 0;
                const isProfit = netPnl >= 0;
                return (
                  <tr key={idx} className={`border-b border-gray-200 ${idx % 2 === 0 ? 'bg-white' : 'bg-gray-50'}`}>
                    <td className="px-4 py-3 text-sm text-gray-900">{trade.entry_date || '-'}</td>
                    <td className="px-4 py-3 text-sm text-gray-900">{trade.exit_date || '-'}</td>
                    <td className="px-4 py-3 text-sm text-gray-900">{trade.entry_spot || '-'}</td>
                    <td className="px-4 py-3 text-sm text-gray-900">{trade.exit_spot || '-'}</td>
                    <td className="px-4 py-3 text-sm text-gray-900">{trade.spot_pnl || '-'}</td>
                    <td className="px-4 py-3 text-sm text-gray-900">{trade.future_expiry || '-'}</td>
                    <td className={`px-4 py-3 text-sm font-semibold ${isProfit ? 'text-green-600' : 'text-red-600'}`}>
                      {isProfit ? '' : '-'}{Math.abs(netPnl).toFixed(2)}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-900">{trade.leg1_type || trade.type || '-'}</td>
                    <td className="px-4 py-3 text-sm text-gray-900">{trade.leg1_strike || trade.strike || '-'}</td>
                    <td className="px-4 py-3 text-sm text-gray-900">{trade.leg1_entry || trade.entry_price || '-'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex justify-between items-center px-6 py-4 border-t bg-gray-50">
        <button className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300">Download Report</button>
        <div className="flex gap-3">
          <button onClick={exportCSV} className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700">Export CSV</button>
          <button onClick={onClose} className="px-4 py-2 bg-gray-600 text-white rounded hover:bg-gray-700">Close</button>
        </div>
      </div>
    </div>
  );
};

const KPICard = ({ title, value, positive = true }) => {
  const colorClass = positive
    ? (String(value).replace(/[₹,%]/g, '').trim() >= 0 ? 'text-green-600' : 'text-red-600')
    : 'text-red-600';
  return (
    <div className="bg-gradient-to-br from-white to-gray-50 border border-gray-200 rounded-lg p-4">
      <p className="text-xs text-gray-500 font-medium mb-1">{title}</p>
      <p className={`text-xl font-bold ${colorClass}`}>{value}</p>
    </div>
  );
};

export default AlgoTestBacktest; 