import { useMemo, useState } from 'react';
import { 
  AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer 
} from 'recharts';
import { Download, X } from 'lucide-react';

const ResultsPanel = ({ results, onClose, showCloseButton = true }) => {
  if (!results) return null;

  const { trades = [], summary = {}, pivot = {} } = results;
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 25;

  // Group trades by Trade number for display (AlgoTest style)
  const groupedTrades = useMemo(() => {
    if (!trades || trades.length === 0) return [];
    
    const groups = {};
    trades.forEach(trade => {
      const tradeNum = trade.Trade || trade.trade || 1;
      if (!groups[tradeNum]) {
        groups[tradeNum] = [];
      }
      groups[tradeNum].push(trade);
    });
    
    // Convert to array and sort by trade number
    return Object.entries(groups)
      .map(([tradeNum, legs]) => ({
        tradeNumber: parseInt(tradeNum),
        legs: legs,
        // Use first leg's data for trade-level info
        entryDate: legs[0]['Entry Date'],
        exitDate: legs[0]['Exit Date'],
        entrySpot: legs[0]['Entry Spot'],
        exitSpot: legs[0]['Exit Spot'],
        // Sum P&L across all legs for this trade
        totalPnl: legs.reduce((sum, leg) => sum + (leg['Net P&L'] || 0), 0),
        cumulative: legs[0].Cumulative || 0,
      }))
      .sort((a, b) => a.tradeNumber - b.tradeNumber);
  }, [trades]);

  // Prepare chart data - USE GROUPED TRADES (one point per trade, not per leg)
  const equityData = useMemo(() => {
    if (!groupedTrades || groupedTrades.length === 0) return [];
    
    return groupedTrades.map((group, index) => ({
      index: index + 1,
      date: group.exitDate || `Trade ${index + 1}`,
      cumulative: group.cumulative || 0,
      pnl: group.totalPnl || 0
    }));
  }, [groupedTrades]);

  const drawdownData = useMemo(() => {
    if (!groupedTrades || groupedTrades.length === 0) return [];
    
    return groupedTrades.map((group, index) => {
      // Get DD from first leg (all legs in a trade have same DD value)
      const dd = group.legs[0]?.DD || group.legs[0]?.dd || 0;
      return {
        index: index + 1,
        date: group.exitDate || `Trade ${index + 1}`,
        drawdown: dd
      };
    });
  }, [groupedTrades]);

  // Calculate stats
  const stats = useMemo(() => {
    const totalPnL = summary.total_pnl || 0;
    const initialCapital = 100000; // Default initial capital used in backend
    const totalPnLPct = (totalPnL / initialCapital) * 100;
    
    return {
      totalPnL: totalPnL,
      totalPnLPct: totalPnLPct,
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
      mddTradeNumber: summary.mdd_trade_number || null,
      cagrSpot: summary.cagr_spot || 0,
      recoveryFactor: summary.recovery_factor || 0
    };
  }, [summary, trades]);


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
      // Handle dd-mm-yyyy format from backend
      const parts = dateStr.split('-');
      if (parts.length === 3) {
        const day = parseInt(parts[0]);
        const month = parseInt(parts[1]) - 1;
        const year = parseInt(parts[2]);
        const date = new Date(year, month, day);
        const monthName = date.toLocaleString('en-US', { month: 'short' });
        return `${day} ${monthName} ${year}`;
      }
      // Fallback for other formats
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
    <div className={showCloseButton ? "fixed inset-0 bg-black bg-opacity-60 z-50 overflow-y-auto" : ""}>
      <div className={showCloseButton ? "min-h-screen px-4 py-6" : ""}>
        <div className={showCloseButton ? "max-w-[1400px] mx-auto bg-white rounded-xl shadow-2xl" : "bg-white rounded-xl shadow-md"}>
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
              {showCloseButton && (
                <button
                  onClick={onClose}
                  className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
                >
                  <X size={22} className="text-gray-600" />
                </button>
              )}
            </div>
          </div>

          {/* Summary Cards */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4 p-6 bg-gradient-to-br from-gray-50 to-gray-100">
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-200">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Total P&L</p>
              <p className={`text-2xl font-bold ${stats.totalPnLPct >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                {stats.totalPnLPct >= 0 ? '+' : ''}{stats.totalPnLPct.toFixed(2)}%
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
              <p className={`text-2xl font-bold ${stats.cagr >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                {stats.cagr.toFixed(1)}%
              </p>
            </div>
            
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-200">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Max DD</p>
              <p className="text-2xl font-bold text-red-600">
                ₹{stats.maxDD.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
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
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.4}/>
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.1}/>
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
                    strokeWidth={2}
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
                      <stop offset="5%" stopColor="#ef4444" stopOpacity={0.4}/>
                      <stop offset="95%" stopColor="#ef4444" stopOpacity={0.1}/>
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
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <Area 
                    type="monotone"
                    dataKey="drawdown" 
                    stroke="#ef4444" 
                    strokeWidth={2}
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
            <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-200">
              <h3 className="text-sm font-bold text-gray-800 mb-3">Detailed Statistics</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-2 text-xs">
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Overall Profit</p>
                  <p className="font-normal text-gray-900">₹{stats.totalPnL.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">No. of Trades</p>
                  <p className="font-normal text-gray-900">{stats.totalTrades}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Average Profit per Trade</p>
                  <p className="font-normal text-gray-900">₹{stats.avgProfitPerTrade.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Win %</p>
                  <p className="font-normal text-gray-900">{stats.winRate.toFixed(2)}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Loss %</p>
                  <p className="font-normal text-gray-900">{stats.lossPct.toFixed(2)}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Average Profit on Winning Trades</p>
                  <p className="font-normal text-gray-900">₹{Math.abs(stats.avgWin).toLocaleString('en-IN', { maximumFractionDigits: 2 })}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Average Loss on Losing Trades</p>
                  <p className="font-normal text-gray-900">₹{stats.avgLoss.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Max Profit in Single Trade</p>
                  <p className="font-normal text-gray-900">₹{stats.maxWin.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Max Loss in Single Trade</p>
                  <p className="font-normal text-gray-900">₹{stats.maxLoss.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Max Drawdown</p>
                  <p className="font-normal text-gray-900">₹{stats.maxDD.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Max DD Days</p>
                  <p className="font-normal text-gray-900">
                    {stats.mddDuration > 0 ? stats.mddDuration : 'N/A'}
                  </p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Return/MaxDD</p>
                  <p className="font-normal text-gray-900">{stats.recoveryFactor.toFixed(2)}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Reward to Risk Ratio</p>
                  <p className="font-normal text-gray-900">{stats.rewardToRisk.toFixed(2)}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Expectancy Ratio</p>
                  <p className="font-normal text-gray-900">{stats.expectancy.toFixed(2)}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Max Win Streak (trades)</p>
                  <p className="font-normal text-gray-900">{stats.maxWinStreak}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Max Losing Streak (trades)</p>
                  <p className="font-normal text-gray-900">{stats.maxLossStreak}</p>
                </div>
                
                <div className="border-b border-gray-200 pb-2">
                  <p className="font-bold text-gray-900 mb-0.5">Max trades in any drawdown</p>
                  <p className="font-normal text-gray-900">{stats.mddTradeNumber || 'N/A'}</p>
                </div>
              </div>
            </div>

            {/* Full Report Table */}
            <div className="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-base font-bold text-gray-800">Full Report</h3>
                <div className="text-sm text-gray-600">
                  Showing <span className="font-semibold">{((currentPage - 1) * itemsPerPage) + 1} - {Math.min(currentPage * itemsPerPage, groupedTrades.length)}</span> trades out of <span className="font-semibold">{groupedTrades.length}</span>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-gray-200 border-b-2 border-gray-400">
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Index</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Entry Date</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Exit Date</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Entry Spot</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Exit Spot</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Spot P&L</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">Type</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Strike</th>
                      <th className="px-3 py-3 text-left text-xs font-bold text-gray-800">B/S</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Qty</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Entry Price</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Exit Price</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">P&L</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">Points P&L</th>
                      <th className="px-3 py-3 text-right text-xs font-bold text-gray-800">% P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {groupedTrades.slice((currentPage - 1) * itemsPerPage, currentPage * itemsPerPage).map((group, groupIdx) => {
                      const actualTradeNum = (currentPage - 1) * itemsPerPage + groupIdx + 1;
                      
                      return group.legs.map((leg, legIdx) => {
                        const isFirstLeg = legIdx === 0;
                        
                        const optionType = leg['Type'] || leg['Leg_1_Type'] || 'CE';
                        const strike = leg['Strike'] || leg['Leg_1_Strike'] || leg['Leg 1 Strike'] || 0;
                        const position = leg['B/S'] || leg['Leg_1_Position'] || 'Sell';
                        const qty = parseInt(leg['Qty']) || parseInt(leg.qty) || parseInt(leg.quantity) || 65;
                        const entryPrice = parseFloat(leg['Entry Price']) || parseFloat(leg['Leg_1_EntryPrice']) || parseFloat(leg['Leg 1 Entry']) || 0;
                        const exitPrice = parseFloat(leg['Exit Price']) || parseFloat(leg['Leg_1_ExitPrice']) || parseFloat(leg['Leg 1 Exit']) || 0;
                        
                        const spotPnl = leg['Spot P&L'] || (group.exitSpot - group.entrySpot) || 0;
                        
                        // Calculate Points P&L based on position (Buy/Sell)
                        const pointsPnl = position.toLowerCase() === 'sell' 
                          ? entryPrice - exitPrice
                          : exitPrice - entryPrice;
                        
                        // Calculate actual P&L = Points P&L × Qty
                        const actualPnl = pointsPnl * qty;
                        
                        // Calculate Percent P&L
                        const percentPnl = entryPrice !== 0 
                          ? (pointsPnl / entryPrice) * 100
                          : 0;
                        
                        return (
                          <tr key={`${group.tradeNumber}-${legIdx}`} className={`border-b border-gray-200 ${groupIdx % 2 === 0 ? 'bg-white' : 'bg-gray-50'} hover:bg-gray-100 transition-colors`}>
                            {/* Show trade number and dates only on first leg */}
                            {isFirstLeg ? (
                              <>
                                <td className="px-3 py-2 text-gray-900 font-semibold" rowSpan={group.legs.length}>{actualTradeNum}</td>
                                <td className="px-3 py-2 text-gray-900" rowSpan={group.legs.length}>{group.entryDate || '-'}</td>
                                <td className="px-3 py-2 text-gray-900" rowSpan={group.legs.length}>{group.exitDate || '-'}</td>
                                <td className="px-3 py-2 text-right text-gray-900" rowSpan={group.legs.length}>{(group.entrySpot || 0).toFixed(2)}</td>
                                <td className="px-3 py-2 text-right text-gray-900" rowSpan={group.legs.length}>{(group.exitSpot || 0).toFixed(2)}</td>
                                <td className="px-3 py-2 text-right text-gray-900" rowSpan={group.legs.length}>
                                  {spotPnl.toFixed(2)}
                                </td>
                              </>
                            ) : null}
                            {/* Leg-specific data */}
                            <td className="px-3 py-2 text-gray-700 text-xs">{optionType}</td>
                            <td className="px-3 py-2 text-right text-gray-700 text-xs">{parseFloat(strike).toFixed(0)}</td>
                            <td className="px-3 py-2 text-gray-700 text-xs">{position}</td>
                            <td className="px-3 py-2 text-right text-gray-700 text-xs">{qty}</td>
                            <td className="px-3 py-2 text-right text-gray-700 text-xs">{entryPrice.toFixed(2)}</td>
                            <td className="px-3 py-2 text-right text-gray-700 text-xs">{exitPrice.toFixed(2)}</td>
                            <td className={`px-3 py-2 text-right text-xs font-semibold ${actualPnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                              {actualPnl >= 0 ? '+' : ''}{actualPnl.toFixed(2)}
                            </td>
                            <td className={`px-3 py-2 text-right text-xs ${pointsPnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                              {pointsPnl.toFixed(2)}
                            </td>
                            <td className={`px-3 py-2 text-right text-xs ${percentPnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                              {percentPnl.toFixed(2)}%
                            </td>
                          </tr>
                        );
                      });
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
                {[...Array(Math.min(6, Math.ceil(groupedTrades.length / itemsPerPage)))].map((_, i) => {
                  const totalPages = Math.ceil(groupedTrades.length / itemsPerPage);
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
                  onClick={() => setCurrentPage(p => Math.min(Math.ceil(groupedTrades.length / itemsPerPage), p + 1))} 
                  disabled={currentPage === Math.ceil(groupedTrades.length / itemsPerPage)}
                  className="px-2 py-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  ›
                </button>
                <button 
                  onClick={() => setCurrentPage(Math.ceil(groupedTrades.length / itemsPerPage))} 
                  disabled={currentPage === Math.ceil(groupedTrades.length / itemsPerPage)}
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
