import { useMemo } from 'react';
import { 
  AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer 
} from 'recharts';
import { Download, X } from 'lucide-react';

const ResultsPanel = ({ results, onClose }) => {
  if (!results) return null;

  const { trades = [], summary = {}, pivot = {} } = results;

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
    return trades.map((trade, index) => ({
      index: index + 1,
      date: trade['Exit Date'] || trade.exit_date || `Trade ${index + 1}`,
      drawdown: trade['%DD'] || trade.pct_dd || 0
    }));
  }, [trades]);

  // Calculate stats
  const stats = useMemo(() => ({
    totalPnL: summary.total_pnl || 0,
    totalTrades: summary.count || trades.length || 0,
    winRate: summary.win_pct || 0,
    cagr: summary.cagr_options || 0,
    maxDD: summary.max_dd_pct || 0,
    carMdd: summary.car_mdd || 0,
    avgWin: summary.avg_win || 0,
    avgLoss: summary.avg_loss || 0,
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
          {payload.map((entry, index) => (
            <p key={index} className="text-sm font-medium" style={{ color: entry.color }}>
              {entry.name}: {entry.name.includes('%') 
                ? `${entry.value?.toFixed(2)}%`
                : `₹${entry.value?.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`}
            </p>
          ))}
        </div>
      );
    }
    return null;
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
                {stats.maxDD.toFixed(1)}%
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
                    dataKey="index" 
                    stroke="#9ca3af"
                    tick={{ fontSize: 11, fill: '#6b7280' }}
                    tickLine={false}
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
                      <stop offset="5%" stopColor="#ef4444" stopOpacity={0.8}/>
                      <stop offset="95%" stopColor="#ef4444" stopOpacity={0.1}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" vertical={false} />
                  <XAxis 
                    dataKey="index" 
                    stroke="#9ca3af"
                    tick={{ fontSize: 11, fill: '#6b7280' }}
                    tickLine={false}
                  />
                  <YAxis 
                    stroke="#9ca3af"
                    tick={{ fontSize: 11, fill: '#6b7280' }}
                    tickLine={false}
                    tickFormatter={(value) => `${value.toFixed(1)}%`}
                    domain={['dataMin', 0]}
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <Area 
                    type="monotone"
                    dataKey="drawdown" 
                    stroke="#dc2626" 
                    strokeWidth={2}
                    fill="url(#colorDrawdown)" 
                    name="Drawdown %"
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

            {/* Trade Log */}
            <div className="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
              <h3 className="text-base font-bold text-gray-800 mb-4">Trade Log (Latest 50)</h3>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b-2 border-gray-300">
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-700 uppercase">Entry Date</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-700 uppercase">Exit Date</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-700 uppercase">Entry Spot</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-700 uppercase">Exit Spot</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-700 uppercase">Spot P&L</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-700 uppercase">Future Expiry</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-700 uppercase">Net P&L</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-700 uppercase">Leg 1 Type</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-700 uppercase">Leg 1 Strike</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-700 uppercase">Leg 1 Entry</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.slice(0, 50).map((trade, idx) => {
                      const netPnl = trade['Net P&L'] || trade.net_pnl || 0;
                      return (
                        <tr key={idx} className="border-b border-gray-200 hover:bg-gray-50 transition-colors">
                          <td className="px-3 py-2 text-gray-900">{trade['Entry Date'] || '-'}</td>
                          <td className="px-3 py-2 text-gray-900">{trade['Exit Date'] || '-'}</td>
                          <td className="px-3 py-2 text-right text-gray-900">{(trade['Entry Spot'] || 0).toFixed(2)}</td>
                          <td className="px-3 py-2 text-right text-gray-900">{(trade['Exit Spot'] || 0).toFixed(2)}</td>
                          <td className="px-3 py-2 text-right text-gray-900">{(trade['Spot P&L'] || 0).toFixed(2)}</td>
                          <td className="px-3 py-2 text-gray-900">{trade['Future Expiry'] || '-'}</td>
                          <td className={`px-3 py-2 text-right font-semibold ${netPnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                            {netPnl.toFixed(2)}
                          </td>
                          <td className="px-3 py-2 text-gray-900">{trade['Leg_1_Type'] || '-'}</td>
                          <td className="px-3 py-2 text-right text-gray-900">{(trade['Leg_1_Strike'] || 0).toFixed(2)}</td>
                          <td className="px-3 py-2 text-right text-gray-900">{(trade['Leg_1_EntryPrice'] || 0).toFixed(2)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ResultsPanel;
