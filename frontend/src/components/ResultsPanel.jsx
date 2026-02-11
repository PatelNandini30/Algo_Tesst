import React, { useMemo } from 'react';
import { 
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  AreaChart, Area, Line, ComposedChart
} from 'recharts';
import { TrendingUp, TrendingDown, DollarSign, Percent, Calendar, Calculator } from 'lucide-react';

const ResultsPanel = ({ results, onClose }) => {
  if (!results) return null;

  const { trades, summary, pivot } = results;

  // Format data for equity curve chart
  const equityChartData = useMemo(() => 
    trades?.map(trade => ({
      date: trade.exit_date || trade.ExitDate || trade.exitDate,
      equity: trade.cumulative || trade.Cumulative || 0,
      spot: trade.spot_equity || 0
    })) || [], [trades]
  );

  // Format data for drawdown chart
  const drawdownChartData = useMemo(() => 
    trades?.map(trade => ({
      date: trade.exit_date || trade.ExitDate || trade.exitDate,
      dd: trade.dd || trade.DD || trade['%DD'] || 0
    })) || [], [trades]
  );

  // Format pivot data for heatmap
  const pivotRows = pivot?.rows || [];
  const pivotHeaders = pivot?.headers || [];

  // Calculate year-wise returns from pivot data
  const yearWiseReturns = useMemo(() => {
    if (!pivotRows || pivotRows.length === 0) return [];
    
    return pivotRows.map(row => {
      const year = row[0]; // First column is year
      const yearlyTotal = row.slice(1).reduce((sum, val) => {
        return sum + (typeof val === 'number' ? val : 0);
      }, 0);
      
      return {
        year: year,
        return: yearlyTotal,
        returnPct: yearlyTotal // Can be converted to % if needed
      };
    }).filter(item => item.year); // Remove any invalid entries
  }, [pivotRows]);

  // Format pivot data for chart
  const pivotChartData = pivotRows.map(row => {
    const obj = {};
    pivotHeaders.forEach((header, idx) => {
      obj[header.toLowerCase()] = row[idx];
    });
    return obj;
  });

  // KPI Summary Data
  const kpiCards = [
    {
      title: "Total P&L",
      value: summary?.total_pnl?.toFixed(2) || summary?.Sum?.toFixed(2) || 0,
      icon: <DollarSign className="h-6 w-6" />,
      color: "text-green-600"
    },
    {
      title: "Win Rate",
      value: `${summary?.win_pct?.toFixed(2) || 0}%`,
      icon: <Percent className="h-6 w-6" />,
      color: "text-blue-600"
    },
    {
      title: "Total Trades",
      value: summary?.count || 0,
      icon: <Calendar className="h-6 w-6" />,
      color: "text-purple-600"
    },
    {
      title: "CAGR",
      value: `${summary?.cagr_options?.toFixed(2) || 0}%`,
      icon: <TrendingUp className="h-6 w-6" />,
      color: "text-indigo-600"
    },
    {
      title: "Max Drawdown",
      value: `${summary?.max_dd_pct?.toFixed(2) || 0}%`,
      icon: <TrendingDown className="h-6 w-6" />,
      color: "text-red-600"
    },
    {
      title: "CAR/MDD",
      value: summary?.car_mdd?.toFixed(2) || 0,
      icon: <Calculator className="h-6 w-6" />,
      color: "text-yellow-600"
    }
  ];

  // Render a single trade row for the trade log
  const renderTradeRow = (trade, index) => {
    const keys = Object.keys(trade);
    return (
      <tr key={index} className={index % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
        {keys.map((key, idx) => {
          const value = trade[key];
          let cellClass = 'px-4 py-2 text-sm text-gray-900';
          
          // Color code Net P&L
          if (key.toLowerCase().includes('pnl') && key.toLowerCase().includes('net')) {
            cellClass += value > 0 ? ' text-green-600 font-medium' : ' text-red-600 font-medium';
          }
          
          // Color code %DD when negative
          if (key.toLowerCase().includes('dd') && typeof value === 'number' && value < 0) {
            cellClass += ' text-red-600';
          }
          
          return (
            <td key={idx} className={cellClass}>
              {typeof value === 'number' ? value.toFixed(2) : value}
            </td>
          );
        })}
      </tr>
    );
  };

  return (
    <div className="mt-6 bg-white rounded-lg shadow p-6">
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-xl font-bold text-gray-800">Backtest Results</h2>
        <button
          onClick={onClose}
          className="px-4 py-2 bg-gray-200 hover:bg-gray-300 rounded-md text-sm font-medium text-gray-700"
        >
          Close Results
        </button>
      </div>

      {/* KPI Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4 mb-8">
        {kpiCards.map((card, index) => (
          <div key={index} className="bg-white border border-gray-200 rounded-lg p-4 shadow-sm">
            <div className="flex items-center">
              <div className={`p-2 rounded-full ${index % 2 === 0 ? 'bg-blue-100' : 'bg-green-100'}`}>
                <span className={card.color}>{card.icon}</span>
              </div>
              <div className="ml-4">
                <p className="text-sm font-medium text-gray-600">{card.title}</p>
                <p className={`text-lg font-semibold ${card.color}`}>{card.value}</p>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Equity Curve Chart */}
      <div className="mb-8">
        <h3 className="text-lg font-semibold mb-4">Equity Curve</h3>
        <div className="h-80">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={equityChartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" />
              <YAxis />
              <Tooltip />
              <Legend />
              <Area
                type="monotone"
                dataKey="equity"
                stroke="#1A56DB"
                fill="url(#colorUv)"
                fillOpacity={0.3}
                name="Strategy"
              />
              <Line
                type="monotone"
                dataKey="spot"
                stroke="#9CA3AF"
                dot={false}
                name="NIFTY Spot Equivalent"
              />
              <defs>
                <linearGradient id="colorUv" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#1A56DB" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#1A56DB" stopOpacity={0} />
                </linearGradient>
              </defs>
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Drawdown Chart */}
      <div className="mb-8">
        <h3 className="text-lg font-semibold mb-4">Drawdown Chart</h3>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={drawdownChartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" />
              <YAxis />
              <Tooltip />
              <Area
                type="monotone"
                dataKey="dd"
                stroke="#EF4444"
                fill="#FEF2F2"
                name="% Drawdown"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Monthly P&L Heatmap */}
      <div className="mb-8">
        <h3 className="text-lg font-semibold mb-4">Yearly Return</h3>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {pivotHeaders.map((header, idx) => (
                  <th
                    key={idx}
                    scope="col"
                    className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
                  >
                    {header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {pivotRows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => {
                    // Skip rendering if it's the year cell (first column)
                    if (cellIndex === 0) {
                      return (
                        <td key={cellIndex} className="px-4 py-2 text-sm font-medium text-gray-900 whitespace-nowrap">
                          {cell}
                        </td>
                      );
                    }

                    // Calculate color based on value
                    let bgColor = 'bg-white'; // Default for null/undefined
                    if (typeof cell === 'number') {
                      if (cell > 0) {
                        // Positive values - green shades
                        const maxPositive = Math.max(...pivotRows.flatMap(r => r.slice(1)).filter(c => typeof c === 'number'));
                        const intensity = Math.min(1, Math.abs(cell) / maxPositive);
                        const shade = Math.round(90 - intensity * 40); // 90 to 50
                        bgColor = `bg-green-${Math.round(100 + intensity * 400)}`;
                      } else if (cell < 0) {
                        // Negative values - red shades
                        const minNegative = Math.min(...pivotRows.flatMap(r => r.slice(1)).filter(c => typeof c === 'number'));
                        const intensity = Math.min(1, Math.abs(cell) / Math.abs(minNegative));
                        const shade = Math.round(90 - intensity * 40); // 90 to 50
                        bgColor = `bg-red-${Math.round(100 + intensity * 400)}`;
                      }
                    }

                    return (
                      <td
                        key={cellIndex}
                        className={`px-4 py-2 text-sm text-gray-900 whitespace-nowrap ${bgColor} ${
                          typeof cell === 'number' ? (cell > 0 ? 'text-green-700' : 'text-red-700') : ''
                        }`}
                      >
                        {typeof cell === 'number' ? cell.toFixed(2) : cell}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Trade-by-Trade Log */}
      <div className="mb-8">
        <h3 className="text-lg font-semibold mb-4">Trade Log</h3>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {trades && trades.length > 0 && Object.keys(trades[0]).map((key, idx) => (
                  <th
                    key={idx}
                    scope="col"
                    className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
                  >
                    {key}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {trades?.slice(0, 50).map((trade, index) => renderTradeRow(trade, index))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Full Summary Statistics Table */}
      <div>
        <h3 className="text-lg font-semibold mb-4">Full Summary Statistics</h3>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Metric</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Value</th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {summary && Object.entries(summary).map(([key, value], index) => (
                <tr key={key}>
                  <td className="px-4 py-2 text-sm font-medium text-gray-900 whitespace-nowrap">{key}</td>
                  <td className="px-4 py-2 text-sm text-gray-500">
                    {typeof value === 'number' ? value.toFixed(2) : String(value)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default ResultsPanel;