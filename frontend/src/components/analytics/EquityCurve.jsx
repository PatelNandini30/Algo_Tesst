import React from 'react';
import { Line } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler
} from 'chart.js';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler
);

const EquityCurve = ({ trades }) => {
  // Use backend's Cumulative if available, else calculate
  const hasBackendCumulative = trades.length > 0 && (
    trades[0].Cumulative !== undefined || 
    trades[0].cumulative !== undefined
  );
  
  const cumulativePnL = hasBackendCumulative 
    ? trades.map((trade, index) => ({
        date: trade.entry_date || trade.EntryDate || trade.exit_date || `Trade ${index + 1}`,
        value: trade.Cumulative || trade.cumulative || 0
      }))
    : trades.reduce((acc, trade, index) => {
        const prevValue = index > 0 ? acc[index - 1].value : 0;
        acc.push({
          date: trade.entry_date || trade.EntryDate || `Trade ${index + 1}`,
          value: prevValue + (trade.pnl || trade.NetPnL || trade.net_pnl || 0)
        });
        return acc;
      }, []);
  
  const data = {
    labels: cumulativePnL.map(d => d.date),
    datasets: [
      {
        label: 'Cumulative P&L',
        data: cumulativePnL.map(d => d.value),
        borderColor: 'rgb(59, 130, 246)',
        backgroundColor: 'rgba(59, 130, 246, 0.1)',
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 6,
        borderWidth: 2,
      }
    ]
  };
  
  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        display: false
      },
      tooltip: {
        mode: 'index',
        intersect: false,
        backgroundColor: 'rgba(30, 41, 59, 0.95)',
        titleColor: '#f1f5f9',
        bodyColor: '#cbd5e1',
        borderColor: '#475569',
        borderWidth: 1,
        padding: 12,
        displayColors: false,
        callbacks: {
          label: (context) => `P&L: ₹${context.parsed.y.toLocaleString('en-IN')}`
        }
      }
    },
    scales: {
      x: {
        grid: {
          color: 'rgba(71, 85, 105, 0.3)',
          drawBorder: false
        },
        ticks: {
          color: '#94a3b8',
          maxTicksLimit: 10
        }
      },
      y: {
        grid: {
          color: 'rgba(71, 85, 105, 0.3)',
          drawBorder: false
        },
        ticks: {
          color: '#94a3b8',
          callback: (value) => `₹${(value / 1000).toFixed(0)}k`
        }
      }
    },
    interaction: {
      mode: 'nearest',
      axis: 'x',
      intersect: false
    }
  };
  
  return (
    <div className="h-[400px]">
      <Line data={data} options={options} />
    </div>
  );
};

export default EquityCurve;
