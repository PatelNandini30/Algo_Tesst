import React, { useMemo } from 'react';
import { Bar } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend
} from 'chart.js';

ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend
);

const DrawdownChart = ({ trades }) => {
  const drawdownData = useMemo(() => {
    let peak = 0;
    let cumulativePnL = 0;
    
    return trades.map(trade => {
      cumulativePnL += (trade.pnl || 0);
      peak = Math.max(peak, cumulativePnL);
      const drawdown = ((cumulativePnL - peak) / peak) * 100;
      
      return {
        date: trade.entry_date,
        drawdown: drawdown || 0
      };
    });
  }, [trades]);
  
  const data = {
    labels: drawdownData.map(d => d.date),
    datasets: [
      {
        label: 'Drawdown %',
        data: drawdownData.map(d => d.drawdown),
        backgroundColor: drawdownData.map(d => 
          d.drawdown < -10 ? 'rgba(239, 68, 68, 0.8)' : 'rgba(239, 68, 68, 0.5)'
        ),
        borderColor: 'rgb(239, 68, 68)',
        borderWidth: 1,
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
        backgroundColor: 'rgba(30, 41, 59, 0.95)',
        titleColor: '#f1f5f9',
        bodyColor: '#cbd5e1',
        borderColor: '#475569',
        borderWidth: 1,
        padding: 12,
        callbacks: {
          label: (context) => `Drawdown: ${context.parsed.y.toFixed(2)}%`
        }
      }
    },
    scales: {
      x: {
        grid: {
          display: false
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
          callback: (value) => `${value.toFixed(0)}%`
        }
      }
    }
  };
  
  return (
    <div className="h-[300px]">
      <Bar data={data} options={options} />
    </div>
  );
};

export default DrawdownChart;
