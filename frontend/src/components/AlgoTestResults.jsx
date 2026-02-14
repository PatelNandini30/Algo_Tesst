import React, { useMemo, useState } from 'react';
import {
  LineChart, Line, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  ComposedChart
} from 'recharts';
import { Download, X, TrendingUp, TrendingDown, DollarSign, Calendar } from 'lucide-react';

const AlgoTestResults = ({ results, onClose }) => {
  const [activeTab, setActiveTab] = useState('overview');

  if (!results) return null;

  const { trades = [], summary = {}, pivot = {}, meta = {} } = results;

  // Helper functions to extract data safely
  const getTradePnL = (trade) => {
    return trade.net_pnl || trade.NetPnL || trade.pnl || trade.PnL || 0;
  };

  const getCumulative = (trade) => {
    return trade.cumulative || trade.Cumulative || 0;
  };

  const getDrawdown = (trade) => {
    return trade.pct_dd || trade['%DD'] || trade.dd || trade.DD || 0;
  };

  const getEntryDate = (trade) => {
    return trade.entry_date || trade.EntryDate || trade.entryDate || '';
  };

  const getExitDate = (trade) => {
    return trade.exit_date || trade.ExitDate || trade.exitDate || '';
  };

  // Format date for display
  const formatDate = (dateStr) => {
    if (!dateStr) return '';
    try {
      const date = new Date(dateStr);
      return date.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
    } catch {
      return dateStr;
    }
  };

  // Prepare chart data
  const chartData = useMemo(() => {
    let cumulativePnL = 0;
    let peak = 0;
    
    return trades.map((trade, index) => {
      const pnl = getTradePnL(trade);
      cumulativePnL += pnl;
      peak = Math.max(peak, cumulativePnL);
      const dd = peak > 0 ? ((cumulativePnL - peak) / peak) * 100 : 0;

      return {
        index: index + 1,
        date: formatDate(getExitDate(trade)),
        cumulative: cumulativePnL,
        pnl: pnl,
        drawdown: dd,
        peak: peak
      };
    });
  }, [trades]);

  // Calculate statistics
  const stats = useMemo(() => {
    const winningTrades = trades.filter(t => getTradePnL(t) > 0);
    const losingTrades = trades.filter(t => getTradePnL(t) < 0);
    
    const totalPnL = summary.total_pnl || chartData[chartData.length - 1]?.cumulative || 0;
    const totalTrades = summary.count || trades.length;
    const winRate = summary.win_pct || (winningTrades.length / totalTrades * 100) || 0;
    const avgWin = summary.avg_win || (winningTrades.reduce((sum, t) => sum + getTradePnL(t), 0) / winningTrades.length) || 0;
    const avgLoss = summary.avg_loss || Math.abs(losingTrades.reduce((sum, t) => sum + getTradePnL(t), 0) / losingTrades.length) || 0;
    
    return {
      totalPnL,
      totalTrades,
      winningTrades: winningTrades.length,
      losingTrades: losingTrades.length,
      winRate,
      avgWin,
      avgLoss,
      cagr: summary.cagr_options || 0,
      maxDD: summary.max_dd_pct || Math.min(...chartData.map(d => d.drawdown)) || 0,
      carMdd: summary.car_mdd || 0,
      expectancy: summary.expectancy || 0,
      recoveryFactor: summary.recovery_factor || 0
    };
  }, [trades, summary, chartData]);

  // Export functionality
  const exportToCSV = () => {
    if (!trades || trades.length === 0) return;
    
    const headers = Object.keys(trades[0]);
    const csvContent = [
      headers.join(','),
      ...trades.map(trade => 
        headers.map(h => {
          const val = trade[h];
          return typeof val === 'string' && val.includes(',') ? `"${val}"` : val;
        }).join(',')
      )
    ].join('\n');
    
    const blob = new Blob([csvContent], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `backtest_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Custom Tooltip
  const CustomTooltip = ({ active, payload }) => {
    if (!active || !payload || !payload.length) return null;
    
    return (
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 shadow-lg text-sm">
        {payload.map((entry, index) => (
          <div key={index} className="flex items-center justify-between gap-4">
            <span className="text-gray-400">{entry.name}:</span>
            <span className="font-semibold" style={{ color: entry.color }}>
              {entry.name.includes('%') 
                ? `${Number(entry.value).toFixed(2)}%`
                : `₹${Number(entry.value).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
              }
            </span>
          </div>
        ))}
      </div>
    );
  };

  return (
    <div className="fixed inset-0 bg-black/50 z-50 overflow-y-auto">
      <div className="min-h-screen px-4 py-8">
        <div className="max-w-[1400px] mx-auto bg-white rounded-xl shadow-2xl">
          {/* Header */}
          <div className="sticky top-0 bg-white border-b z-10 rounded-t-xl">
            <div className="flex justify-between items-center px-6 py-4">
              <div>
                <h2 className="text-2xl font-bold text-gray-900">Backtest Results</h2>
                <p className="text-sm text-gray-500 mt-1">
                  {meta.strategy || 'Strategy'} • {meta.index || 'NIFTY'} • {meta.date_range || 'Date range'}
                </p>
              </div>
              <div className="flex gap-3">
                <button
                  onClick={exportToCSV}
                  className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
                >
                  <Download size={16} />
                  Export
                </button>
                <button
                  onClick={onClose}
                  className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
                >
                  <X size={20} />
                </button>
              </div>
            </div>

            {/* Tabs */}
            <div className="flex gap-4 px-6 border-t">
              {['overview', 'trades', 'analytics'].map(tab => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`py-3 px-4 font-medium capitalize transition-colors ${
                    activeTab === tab
                      ? 'text-blue-600 border-b-2 border-blue-600'
                      : 'text-gray-600 hover:text-gray-900'
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>
          </div>

          {/* Content */}
          <div className="p-6">
            {activeTab === 'overview' && (
              <div className="space-y-6">
                {/* KPI Cards */}
                <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
                  <MetricCard 
                    title="Total P&L" 
                    value={stats.totalPnL}
                    format="currency"
                    icon={DollarSign}
                    positive={stats.totalPnL >= 0}
                  />
                  <MetricCard 
                    title="Win Rate" 
                    value={stats.winRate}
                    format="percent"
                    icon={TrendingUp}
                  />
                  <MetricCard 
                    title="CAGR" 
                    value={stats.cagr}
                    format="percent"
                    icon={TrendingUp}
                  />
                  <MetricCard 
                    title="Max DD" 
                    value={Math.abs(stats.maxDD)}
                    format="percent"
                    icon={TrendingDown}
                    positive={false}
                  />
                  <MetricCard 
                    title="CAR/MDD" 
                    value={stats.carMdd}
                    format="number"
                    icon={Calendar}
                  />
                  <MetricCard 
                    title="Total Trades" 
                    value={stats.totalTrades}
                    format="number"
                    icon={Calendar}
                  />
                </div>

                {/* Equity Curve */}
                <div className="bg-white border rounded-xl p-6">
                  <h3 className="text-lg font-semibold mb-4">Equity Curve</h3>
                  <ResponsiveContainer width="100%" height={400}>
                    <ComposedChart data={chartData}>
                      <defs>
                        <linearGradient id="colorEquity" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#10b981" stopOpacity={0.3}/>
                          <stop offset="95%" stopColor="#10b981" stopOpacity={0}/>
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis 
                        dataKey="index" 
                        stroke="#9ca3af"
                        tick={{ fontSize: 11 }}
                        label={{ value: 'Trade #', position: 'insideBottom', offset: -5 }}
                      />
                      <YAxis 
                        yAxisId="left"
                        stroke="#9ca3af"
                        tick={{ fontSize: 11 }}
                        tickFormatter={(value) => `₹${(value/1000).toFixed(0)}k`}
                      />
                      <Tooltip content={<CustomTooltip />} />
                      <Legend />
                      <Area 
                        yAxisId="left"
                        type="monotone" 
                        dataKey="cumulative" 
                        stroke="#10b981" 
                        strokeWidth={2.5}
                        fill="url(#colorEquity)" 
                        name="Cumulative P&L"
                      />
                      <Line
                        yAxisId="left"
                        type="monotone"
                        dataKey="peak"
                        stroke="#94a3b8"
                        strokeWidth={1}
                        strokeDasharray="5 5"
                        dot={false}
                        name="Peak"
                      />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>

                {/* Drawdown Chart */}
                <div className="bg-white border rounded-xl p-6">
                  <h3 className="text-lg font-semibold mb-4">Drawdown Chart</h3>
                  <ResponsiveContainer width="100%" height={300}>
                    <AreaChart data={chartData}>
                      <defs>
                        <linearGradient id="colorDD" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3}/>
                          <stop offset="95%" stopColor="#ef4444" stopOpacity={0}/>
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis 
                        dataKey="index" 
                        stroke="#9ca3af"
                        tick={{ fontSize: 11 }}
                      />
                      <YAxis 
                        stroke="#9ca3af"
                        tick={{ fontSize: 11 }}
                        tickFormatter={(value) => `${value.toFixed(1)}%`}
                      />
                      <Tooltip content={<CustomTooltip />} />
                      <Area 
                        type="monotone" 
                        dataKey="drawdown" 
                        stroke="#ef4444" 
                        strokeWidth={2}
                        fill="url(#colorDD)" 
                        name="Drawdown %"
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>

                {/* Monthly Heatmap */}
                {pivot.rows && pivot.rows.length > 0 && (
                  <div className="bg-white border rounded-xl p-6">
                    <h3 className="text-lg font-semibold mb-4">Year-wise Returns</h3>
                    <div className="overflow-x-auto">
                      <table className="min-w-full">
                        <thead>
                          <tr className="border-b bg-gray-50">
                            {pivot.headers?.map((header, idx) => (
                              <th key={idx} className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase">
                                {header}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {pivot.rows?.map((row, rowIdx) => (
                            <tr key={rowIdx} className="border-b hover:bg-gray-50">
                              {row.map((cell, cellIdx) => {
                                const isNumeric = typeof cell === 'number';
                                const isPositive = isNumeric && cell > 0;
                                const isNegative = isNumeric && cell < 0;
                                
                                let bgColor = '';
                                if (isNumeric && cellIdx > 0) {
                                  const intensity = Math.min(100, Math.abs(cell) / 100);
                                  if (isPositive) {
                                    bgColor = `rgba(16, 185, 129, ${intensity * 0.2})`;
                                  } else if (isNegative) {
                                    bgColor = `rgba(239, 68, 68, ${intensity * 0.2})`;
                                  }
                                }
                                
                                return (
                                  <td 
                                    key={cellIdx} 
                                    className={`px-4 py-3 text-sm ${
                                      cellIdx === 0 ? 'font-semibold text-gray-900' : ''
                                    } ${
                                      isPositive ? 'text-green-700 font-medium' : 
                                      isNegative ? 'text-red-700 font-medium' : 
                                      'text-gray-900'
                                    }`}
                                    style={{ backgroundColor: bgColor }}
                                  >
                                    {isNumeric ? cell.toFixed(2) : cell}
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

                {/* Summary Statistics */}
                <div className="bg-white border rounded-xl p-6">
                  <h3 className="text-lg font-semibold mb-4">Performance Metrics</h3>
                  <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                    <StatRow label="Winning Trades" value={stats.winningTrades} />
                    <StatRow label="Losing Trades" value={stats.losingTrades} />
                    <StatRow label="Avg Win" value={`₹${stats.avgWin.toFixed(2)}`} positive />
                    <StatRow label="Avg Loss" value={`₹${stats.avgLoss.toFixed(2)}`} negative />
                    <StatRow label="Expectancy" value={stats.expectancy.toFixed(2)} />
                    <StatRow label="Recovery Factor" value={stats.recoveryFactor.toFixed(2)} />
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'trades' && (
              <div className="bg-white border rounded-xl p-6">
                <h3 className="text-lg font-semibold mb-4">Trade Log</h3>
                <div className="overflow-x-auto">
                  <table className="min-w-full">
                    <thead>
                      <tr className="border-b bg-gray-50">
                        <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600">#</th>
                        {trades.length > 0 && Object.keys(trades[0]).slice(0, 15).map((key, idx) => (
                          <th key={idx} className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase">
                            {key.replace(/_/g, ' ')}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {trades.map((trade, idx) => (
                        <tr key={idx} className="border-b hover:bg-gray-50">
                          <td className="px-4 py-3 text-sm text-gray-500">{idx + 1}</td>
                          {Object.values(trade).slice(0, 15).map((value, cellIdx) => {
                            const isNumeric = typeof value === 'number';
                            const isPnL = Object.keys(trade)[cellIdx]?.toLowerCase().includes('pnl');
                            const isPositive = isNumeric && value > 0 && isPnL;
                            const isNegative = isNumeric && value < 0 && isPnL;
                            
                            return (
                              <td 
                                key={cellIdx} 
                                className={`px-4 py-3 text-sm ${
                                  isPositive ? 'text-green-600 font-medium' : 
                                  isNegative ? 'text-red-600 font-medium' : 
                                  'text-gray-900'
                                }`}
                              >
                                {isNumeric ? value.toFixed(2) : value || '-'}
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

            {activeTab === 'analytics' && (
              <div className="space-y-6">
                <div className="bg-white border rounded-xl p-6">
                  <h3 className="text-lg font-semibold mb-4">Complete Statistics</h3>
                  <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                    {Object.entries(summary).map(([key, value]) => (
                      <div key={key} className="border-l-4 border-blue-500 pl-4 py-2">
                        <p className="text-xs text-gray-500 uppercase mb-1">{key.replace(/_/g, ' ')}</p>
                        <p className="text-lg font-semibold text-gray-900">
                          {typeof value === 'number' ? value.toFixed(2) : value}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

// Helper Components
const MetricCard = ({ title, value, format, icon: Icon, positive = true }) => {
  const formatValue = (val) => {
    if (format === 'currency') return `₹${val.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
    if (format === 'percent') return `${val.toFixed(2)}%`;
    return val.toLocaleString('en-IN', { maximumFractionDigits: 2 });
  };

  const colorClass = positive 
    ? value >= 0 ? 'text-green-600' : 'text-red-600'
    : 'text-gray-900';

  return (
    <div className="bg-gradient-to-br from-white to-gray-50 border rounded-xl p-4 hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between mb-2">
        <p className="text-xs text-gray-500 font-medium">{title}</p>
        {Icon && <Icon size={18} className="text-blue-500" />}
      </div>
      <p className={`text-2xl font-bold ${colorClass}`}>
        {formatValue(value)}
      </p>
    </div>
  );
};

const StatRow = ({ label, value, positive, negative }) => {
  const colorClass = positive 
    ? 'text-green-600' 
    : negative 
    ? 'text-red-600' 
    : 'text-gray-900';

  return (
    <div className="flex justify-between items-center py-2 border-b">
      <span className="text-sm text-gray-600">{label}</span>
      <span className={`text-sm font-semibold ${colorClass}`}>{value}</span>
    </div>
  );
};

export default AlgoTestResults;
