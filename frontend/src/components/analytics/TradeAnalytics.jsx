import React, { useMemo } from 'react';
import { Pie, Bar } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  ArcElement,
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend
} from 'chart.js';

ChartJS.register(
  ArcElement,
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend
);

const TradeAnalytics = ({ trades }) => {
  const analytics = useMemo(() => {
    const winningTrades = trades.filter(t => (t.pnl || 0) > 0);
    const losingTrades = trades.filter(t => (t.pnl || 0) < 0);
    
    // Holding period analysis
    const holdingPeriods = trades.map(t => {
      const entry = new Date(t.entry_date);
      const exit = new Date(t.exit_date);
      return Math.ceil((exit - entry) / (1000 * 60 * 60 * 24));
    });
    
    // P&L distribution
    const pnlBuckets = {
      'Loss > 10k': trades.filter(t => t.pnl < -10000).length,
      'Loss 5k-10k': trades.filter(t => t.pnl >= -10000 && t.pnl < -5000).length,
      'Loss 0-5k': trades.filter(t => t.pnl >= -5000 && t.pnl < 0).length,
      'Profit 0-5k': trades.filter(t => t.pnl >= 0 && t.pnl < 5000).length,
      'Profit 5k-10k': trades.filter(t => t.pnl >= 5000 && t.pnl < 10000).length,
      'Profit > 10k': trades.filter(t => t.pnl >= 10000).length,
    };
    
    return {
      winCount: winningTrades.length,
      lossCount: losingTrades.length,
      avgHoldingPeriod: holdingPeriods.reduce((a, b) => a + b, 0) / holdingPeriods.length,
      pnlBuckets
    };
  }, [trades]);
  
  const winLossData = {
    labels: ['Winning Trades', 'Losing Trades'],
    datasets: [{
      data: [analytics.winCount, analytics.lossCount],
      backgroundColor: ['rgba(34, 197, 94, 0.8)', 'rgba(239, 68, 68, 0.8)'],
      borderColor: ['rgb(34, 197, 94)', 'rgb(239, 68, 68)'],
      borderWidth: 2,
    }]
  };
  
  const pnlDistributionData = {
    labels: Object.keys(analytics.pnlBuckets),
    datasets: [{
      label: 'Number of Trades',
      data: Object.values(analytics.pnlBuckets),
      backgroundColor: [
        'rgba(239, 68, 68, 0.8)',
        'rgba(239, 68, 68, 0.6)',
        'rgba(239, 68, 68, 0.4)',
        'rgba(34, 197, 94, 0.4)',
        'rgba(34, 197, 94, 0.6)',
        'rgba(34, 197, 94, 0.8)',
      ],
    }]
  };
  
  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        labels: {
          color: '#cbd5e1'
        }
      },
      tooltip: {
        backgroundColor: 'rgba(30, 41, 59, 0.95)',
        titleColor: '#f1f5f9',
        bodyColor: '#cbd5e1',
        borderColor: '#475569',
        borderWidth: 1,
      }
    }
  };
  
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div className="bg-dark-card border border-dark-border rounded-xl p-6">
        <h4 className="text-sm font-semibold text-gray-100 mb-4">Win/Loss Distribution</h4>
        <div className="h-[300px]">
          <Pie data={winLossData} options={chartOptions} />
        </div>
        <div className="mt-4 text-center">
          <p className="text-sm text-gray-400">
            Win Rate: <span className="text-profit font-semibold">
              {((analytics.winCount / (analytics.winCount + analytics.lossCount)) * 100).toFixed(1)}%
            </span>
          </p>
        </div>
      </div>
      
      <div className="bg-dark-card border border-dark-border rounded-xl p-6">
        <h4 className="text-sm font-semibold text-gray-100 mb-4">P&L Distribution</h4>
        <div className="h-[300px]">
          <Bar data={pnlDistributionData} options={{
            ...chartOptions,
            scales: {
              y: {
                grid: {
                  color: 'rgba(71, 85, 105, 0.3)'
                },
                ticks: {
                  color: '#94a3b8'
                }
              },
              x: {
                grid: {
                  display: false
                },
                ticks: {
                  color: '#94a3b8',
                  font: {
                    size: 10
                  }
                }
              }
            }
          }} />
        </div>
      </div>
      
      <div className="lg:col-span-2 bg-dark-card border border-dark-border rounded-xl p-6">
        <h4 className="text-sm font-semibold text-gray-100 mb-4">Trade Statistics</h4>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="text-center p-4 bg-dark-hover rounded-lg">
            <p className="text-2xl font-bold text-gray-100">{analytics.avgHoldingPeriod.toFixed(1)}</p>
            <p className="text-xs text-gray-400 mt-1">Avg Holding Period (days)</p>
          </div>
          <div className="text-center p-4 bg-dark-hover rounded-lg">
            <p className="text-2xl font-bold text-profit">{analytics.winCount}</p>
            <p className="text-xs text-gray-400 mt-1">Winning Trades</p>
          </div>
          <div className="text-center p-4 bg-dark-hover rounded-lg">
            <p className="text-2xl font-bold text-loss">{analytics.lossCount}</p>
            <p className="text-xs text-gray-400 mt-1">Losing Trades</p>
          </div>
          <div className="text-center p-4 bg-dark-hover rounded-lg">
            <p className="text-2xl font-bold text-gray-100">{trades.length}</p>
            <p className="text-xs text-gray-400 mt-1">Total Trades</p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default TradeAnalytics;
