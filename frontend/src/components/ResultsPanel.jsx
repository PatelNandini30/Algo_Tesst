import { useMemo, useState } from 'react';
import { 
  AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer 
} from 'recharts';
import { Download, X } from 'lucide-react';

const ResultsPanel = ({ results, onClose }) => {
  if (!results) return null;

  const { trades = [], summary = {}, pivot = {} } = results;
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 25;

  // Prepare chart data
  const equityData = useMemo(() => {
    return trades.map((trade, index) => ({
      index: index + 1,
      date: trade['Exit Date'] || trade.exit_date || `Trade ${index + 1}`,
      cumulative: trade.Cumulative || trade.cumulative || 0,
      pnl: trade['Net P&L'] || trade.net_pnl || 0
    }));
  }, [trades]);

  const drawdownData = useMemo(() => {
    // Show absolute rupee drawdown on Y-axis (like your bottom chart)
    return trades.map((trade, index) => ({
      index: index + 1,
      date: trade['Exit Date'] || trade.exit_date || `Trade ${index + 1}`,
      drawdown: trade['DD'] || trade.dd || 0  // Absolute rupee drawdown for both Y-axis and tooltip
    }));
  }, [trades]);

  // Calculate stats
  const stats = useMemo(() => ({
    totalPnL: summary.total_pnl || 0,
    totalTrades: summary.count || trades.length || 0,
    winRate: summary.win_pct || 0,
    lossPct: summary.loss_pct || 0,
    cagr: summary.cagr_options || 0,
    maxDD: summary.max_dd_pts || 0,
    carMdd: summary.car_mdd || 0,
    avgWin: summary.avg_win || 0,
    avgLoss: summary.avg_loss || 0,
    maxWin: summary.max_win || 0,
    maxLoss: summary.max_loss || 0,
    avgProfitPerTrade: summary.avg_profit_per_trade || 0,
    expectancy: summary.expectancy || 0,
    rewardToRisk: summary.reward_to_risk || 0,
    maxWinStreak: summary.max_win_streak || 0,
    maxLossStreak: summary.max_loss_streak || 0,
    mddDuration: summary.mdd_duration_days || 0,
    mddStartDate: summary.mdd_start_date || '',
    mddEndDate: summary.mdd_end_date || '',
    cagrSpot: summary.cagr_spot || 0,
    recoveryFactor: summary.recovery_factor || 0
  }), [summary, trades]);

  // Export CSV
  const exportToCSV = () => {
    if (!trades || trades.length === 0) return;
    const headers = Object.keys(trades[0]).join(',');
    const rows = trades.map(trade => 
      Object.values(trade).map(val => 
        typeof val === 'string' && val.includes(',') ? `"${val}"` : val
      ).join(',')
    );
    const csv = [headers, ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `backtest_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
  };

  const CustomTooltip = ({ active, payload }) => {
    if (active && payload && payload.length) {
      return (
        <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 shadow-xl">
          <p className="text-xs text-gray-400 mb-1">{payload[0]?.payload?.date}</p>
          {payload.map((entry, index) => (
            <p key={index} className="text-sm font-medium" style={{ color: entry.color }}>
              {entry.name}: {entry.name.includes('%') 
                ? `${entry.value?.toFixed(2)}%`
                : `₹${entry.value?.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`}
            </p>
          ))}
        </div>
      );
    }
    return null;
  };

  const formatDateShort = (dateStr) => {
    if (!dateStr) return '';
    try {
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
    <div className="fixed inset-0 bg-black bg-opacity-60 z-50 overflow-y-auto">
      <div className="min-h-screen px-4 py-6">
        <div className="max-w-[1400px] mx-auto bg-white rounded-xl shadow-2xl">
          {/* Header */}
          <div className="flex justify-between items-center px-6 py-5 border-b border-gray-200">
            <div>
              <h2 className="text-2xl font-bold text-gray-900">Backtest Results</h2>
              <p className="text-sm text-gray-600 mt-1">
                {stats.totalTrades} trades • {results.meta?.date_range || ''}
              </p>
            </div>
            <div className="flex gap-3">
              <button
                onClick={exportToCSV}
                className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium text-sm"
              >
                <Download size={16} />
                Export CSV
              </button>
              <button
                onClick={onClose}
                className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
              >
                <X size={22} className="text-gray-600" />
              </button>
            </div>
          </div>

          {/* Summary Cards */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 p-6 bg-gradient-to-br from-gray-50 to-gray-100">
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-200">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Total P&L</p>
              <p className={`text-2xl font-bold ${stats.totalPnL >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                ₹{stats.totalPnL.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
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
              <p className="text-2xl font-bold text-purple-600">
                {stats.cagr.toFixed(1)}%
              </p>
            </div>
            
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-200">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Max DD</p>
              <p className="text-2xl font-bold text-red-600">
                ₹{Math.abs(stats.maxDD).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
              </p>
            </div>
            
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-200">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">CAR/MDD</p>
              <p className="text-2xl font-bold text-indigo-600">
                {stats.carMdd.toFixed(2)}
              </p>
            </div>
            
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-200">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Trades</p>
              <p className="text-2xl font-bold text-gray-700">
                {stats.totalTrades}
              </p>
            </div>
          </div>

          {/* Charts */}
          <div className="p-6 space-y-6 bg-gray-50">
            {/* Equity Curve */}
            <div className="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
              <h3 className="text-base font-bold text-gray-800 mb-4">Equity Curve (Cumulative P&L)</h3>
              <ResponsiveContainer width="100%" height={300}>
                <AreaChart data={equityData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
                  <defs>
                    <linearGradient id="colorEquity" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.05}/>
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
                    tickFormatter={(value) => `₹${(value/1000).toFixed(0)}k`}
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <Area 
                    type="monotone" 
                    dataKey="cumulative" 
                    stroke="#3b82f6" 
                    strokeWidth={2.5}
                    fill="url(#colorEquity)" 
                    name="Cumulative P&L"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>

            {/* Drawdown */}
            <div className="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
              <h3 className="text-base font-bold text-gray-800 mb-4">Drawdown</h3>
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart data={drawdownData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
                  <defs>
                    <linearGradient id="colorDrawdown" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#ef4444" stopOpacity={0.6}/>
                      <stop offset="95%" stopColor="#ef4444" stopOpacity={0.2}/>
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
                    tickFormatter={(value) => `₹${(value/1000).toFixed(0)}k`}
                    domain={['dataMin', 0]}
                    reversed={false}
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <Area 
                    type="monotone"
                    dataKey="drawdown" 
                    stroke="#dc2626" 
                    strokeWidth={1.5}
                    fill="url(#colorDrawdown)" 
                    name="Drawdown"
                  />
                </AreaChart>
              </ResponsiveContainer>
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
            <div className="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
              <h3 className="text-base font-bold text-gray-800 mb-4">Detailed Statistics</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Left Column */}
                <div className="space-y-3">
                  <div className="flex justify-between items-center py-2 border-b border-gray-200">
                    <span className="text-sm font-medium text-gray-600">Overall Profit</span>
                    <span className={`text-sm font-bold ${stats.totalPnL >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      ₹{stats.totalPnL.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-gray-200">
                    <span className="text-sm font-medium text-gray-600">No. of Trades</span>
                    <span className="text-sm font-bold text-gray-900">{stats.totalTrades}</span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-gray-200">
                    <span className="text-sm font-medium text-gray-600">Average Profit per Trade</span>
                    <span className={`text-sm font-bold ${stats.avgProfitPerTrade >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      ₹{stats.avgProfitPerTrade.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-gray-200">
                    <span className="text-sm font-medium text-gray-600">Win %</span>
                    <span className="text-sm font-bold text-blue-600">{stats.winRate.toFixed(2)}%</span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-gray-200">
                    <span className="text-sm font-medium text-gray-600">Loss %</span>
                    <span className="text-sm font-bold text-red-600">{stats.lossPct.toFixed(2)}%</span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-gray-200">
                    <span className="text-sm font-medium text-gray-600">Average Profit on Winning Trades</span>
                    <span className="text-sm font-bold text-green-600">
                      ₹{Math.abs(stats.avgWin).toLocaleString('en-IN', { maximumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-gray-200">
                    <span className="text-sm font-medium text-gray-600">Average Loss on Losing Trades</span>
                    <span className="text-sm font-bold text-red-600">
                      ₹{stats.avgLoss.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
                    </span>
                  </div>

                </div>

                {/* Right Column */}
                <div className="space-y-3">
                  <div className="flex justify-between items-center py-2 border-b border-gray-200">
                    <span className="text-sm font-medium text-gray-600">Max Drawdown</span>
                    <span className="text-sm font-bold text-red-600">
                      ₹{Math.abs(stats.maxDD).toLocaleString('en-IN', { maximumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-gray-200">
                    <span className="text-sm font-medium text-gray-600">Duration of Max Drawdown</span>
                    <span className="text-sm font-bold text-gray-900">
                      {stats.mddDuration} days
                      {stats.mddStartDate && stats.mddEndDate && (
                        <span className="block text-xs text-gray-500 mt-1">
                          [{stats.mddStartDate} to {stats.mddEndDate}]
                        </span>
                      )}
                    </span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-gray-200">
                    <span className="text-sm font-medium text-gray-600">Return/MaxDD</span>
                    <span className={`text-sm font-bold ${stats.recoveryFactor >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {stats.recoveryFactor.toFixed(2)}
                    </span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-gray-200">
                    <span className="text-sm font-medium text-gray-600">Reward to Risk Ratio</span>
                    <span className="text-sm font-bold text-indigo-600">
                      {stats.rewardToRisk.toFixed(2)}
                    </span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-gray-200">
                    <span className="text-sm font-medium text-gray-600">Expectancy Ratio</span>
                    <span className={`text-sm font-bold ${stats.expectancy >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {stats.expectancy.toFixed(2)}
                    </span>
                  </div>

                  <div className="flex justify-between items-center py-2 border-b border-gray-200">
                    <span className="text-sm font-medium text-gray-600">CAGR (Options)</span>
                    <span className={`text-sm font-bold ${stats.cagr >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {stats.cagr.toFixed(2)}%
                    </span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-gray-200">
                    <span className="text-sm font-medium text-gray-600">CAR/MDD</span>
                    <span className={`text-sm font-bold ${stats.carMdd >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {stats.carMdd.toFixed(2)}
                    </span>
                  </div>
                </div>
              </div>
            </div>

            {/* Full Report Table */}
            <div className="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-base font-bold text-gray-800">Full Report</h3>
                <div className="text-sm text-gray-600">
                  Showing <span className="font-semibold">{((currentPage - 1) * itemsPerPage) + 1} - {Math.min(currentPage * itemsPerPage, trades.length)}</span> of <span className="font-semibold">{trades.length}</span> trades
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-gray-200 border-b-2 border-gray-400">
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Index</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Entry Date</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Exit Date</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Type</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Strike</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">B/S</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Qty</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Entry Price</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Exit Price</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">P/L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.slice((currentPage - 1) * itemsPerPage, currentPage * itemsPerPage).map((trade, idx) => {
                      const netPnl = trade['Net P&L'] || trade.net_pnl || trade.pnl || 0;
                      const actualIdx = (currentPage - 1) * itemsPerPage + idx;
                      
                      // Extract option type from Leg_1_Type (e.g., "Option_CE_SELL" -> "CE")
                      const legType = trade['Leg_1_Type'] || trade['Leg 1 Type'] || '';
                      let optionType = 'CE';
                      let position = 'Sell';
                      
                      if (legType.includes('_PE_')) {
                        optionType = 'PE';
                      } else if (legType.includes('_CE_')) {
                        optionType = 'CE';
                      }
                      
                      if (legType.includes('_BUY')) {
                        position = 'Buy';
                      } else if (legType.includes('_SELL')) {
                        position = 'Sell';
                      }
                      
                      return (
                        <tr key={actualIdx} className={`border-b border-gray-200 ${idx % 2 === 0 ? 'bg-white' : 'bg-gray-50'} hover:bg-gray-100 transition-colors`}>
                          <td className="px-3 py-2 text-gray-900">{actualIdx + 1}</td>
                          <td className="px-3 py-2 text-gray-900">{trade['Entry Date'] || '-'}</td>
                          <td className="px-3 py-2 text-gray-900">{trade['Exit Date'] || '-'}</td>
                          <td className="px-3 py-2 text-gray-900">{optionType}</td>
                          <td className="px-3 py-2 text-right text-gray-900">{(trade['Leg_1_Strike'] || trade['Leg 1 Strike'] || 0).toFixed(2)}</td>
                          <td className="px-3 py-2 text-gray-900">{position}</td>
                          <td className="px-3 py-2 text-right text-gray-900">{trade.qty || trade.quantity || 65}</td>
                          <td className="px-3 py-2 text-right text-gray-900">{(trade['Leg_1_EntryPrice'] || trade['Leg 1 Entry'] || 0).toFixed(2)}</td>
                          <td className="px-3 py-2 text-right text-gray-900">{(trade['Leg_1_ExitPrice'] || trade['Leg 1 Exit'] || 0).toFixed(2)}</td>
                          <td className={`px-3 py-2 text-right font-bold ${netPnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                            {netPnl.toFixed(2)}
                          </td>
                        </tr>
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
                {[...Array(Math.min(6, Math.ceil(trades.length / itemsPerPage)))].map((_, i) => {
                  const totalPages = Math.ceil(trades.length / itemsPerPage);
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
                  onClick={() => setCurrentPage(p => Math.min(Math.ceil(trades.length / itemsPerPage), p + 1))} 
                  disabled={currentPage === Math.ceil(trades.length / itemsPerPage)}
                  className="px-2 py-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  ›
                </button>
                <button 
                  onClick={() => setCurrentPage(Math.ceil(trades.length / itemsPerPage))} 
                  disabled={currentPage === Math.ceil(trades.length / itemsPerPage)}
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
