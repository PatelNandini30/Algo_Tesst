import React, { useMemo } from 'react';
import { TrendingUp, TrendingDown, Activity, Target } from 'lucide-react';
import MetricCard from './MetricCard';

const RiskMetrics = ({ trades, summary }) => {
  const metrics = useMemo(() => {
    const returns = [];
    let cumulativePnL = 0;
    let peak = 0;
    let maxDrawdown = 0;
    
    trades.forEach(trade => {
      cumulativePnL += (trade.pnl || 0);
      peak = Math.max(peak, cumulativePnL);
      const drawdown = peak - cumulativePnL;
      maxDrawdown = Math.max(maxDrawdown, drawdown);
      
      if (trade.pnl) {
        returns.push(trade.pnl);
      }
    });
    
    // Calculate Sharpe Ratio (simplified)
    const avgReturn = returns.reduce((a, b) => a + b, 0) / returns.length;
    const variance = returns.reduce((sum, ret) => sum + Math.pow(ret - avgReturn, 2), 0) / returns.length;
    const stdDev = Math.sqrt(variance);
    const sharpeRatio = stdDev !== 0 ? (avgReturn / stdDev) * Math.sqrt(252) : 0;
    
    // Calculate Sortino Ratio (downside deviation)
    const negativeReturns = returns.filter(r => r < 0);
    const downsideVariance = negativeReturns.reduce((sum, ret) => sum + Math.pow(ret, 2), 0) / negativeReturns.length;
    const downsideDev = Math.sqrt(downsideVariance);
    const sortinoRatio = downsideDev !== 0 ? (avgReturn / downsideDev) * Math.sqrt(252) : 0;
    
    // Calculate Calmar Ratio
    const totalReturn = cumulativePnL;
    const calmarRatio = maxDrawdown !== 0 ? totalReturn / maxDrawdown : 0;
    
    // Win/Loss Ratio
    const winningTrades = trades.filter(t => (t.pnl || 0) > 0);
    const losingTrades = trades.filter(t => (t.pnl || 0) < 0);
    const avgWin = winningTrades.length > 0 
      ? winningTrades.reduce((sum, t) => sum + t.pnl, 0) / winningTrades.length 
      : 0;
    const avgLoss = losingTrades.length > 0 
      ? Math.abs(losingTrades.reduce((sum, t) => sum + t.pnl, 0) / losingTrades.length)
      : 0;
    const winLossRatio = avgLoss !== 0 ? avgWin / avgLoss : 0;
    
    return {
      sharpeRatio,
      sortinoRatio,
      calmarRatio,
      maxDrawdown,
      winLossRatio,
      avgWin,
      avgLoss
    };
  }, [trades]);
  
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
      <MetricCard
        title="Sharpe Ratio"
        value={metrics.sharpeRatio}
        format="number"
        icon={TrendingUp}
        subtitle="Risk-adjusted return"
        changeType={metrics.sharpeRatio > 1 ? 'positive' : 'neutral'}
      />
      
      <MetricCard
        title="Sortino Ratio"
        value={metrics.sortinoRatio}
        format="number"
        icon={Target}
        subtitle="Downside risk-adjusted"
        changeType={metrics.sortinoRatio > 1 ? 'positive' : 'neutral'}
      />
      
      <MetricCard
        title="Calmar Ratio"
        value={metrics.calmarRatio}
        format="number"
        icon={Activity}
        subtitle="Return / Max Drawdown"
        changeType={metrics.calmarRatio > 1 ? 'positive' : 'neutral'}
      />
      
      <MetricCard
        title="Win/Loss Ratio"
        value={metrics.winLossRatio}
        format="number"
        icon={TrendingDown}
        subtitle={`Avg Win: ₹${metrics.avgWin.toFixed(0)} / Avg Loss: ₹${metrics.avgLoss.toFixed(0)}`}
        changeType={metrics.winLossRatio > 1.5 ? 'positive' : 'neutral'}
      />
      
      <div className="col-span-full bg-dark-card border border-dark-border rounded-xl p-6">
        <h4 className="text-sm font-semibold text-gray-100 mb-4">Risk Metrics Interpretation</h4>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
          <div>
            <p className="text-gray-400 mb-2">
              <span className="font-medium text-gray-300">Sharpe Ratio:</span> {' '}
              {metrics.sharpeRatio > 2 ? '🟢 Excellent' : 
               metrics.sharpeRatio > 1 ? '🟡 Good' : 
               metrics.sharpeRatio > 0 ? '🟠 Fair' : '🔴 Poor'}
            </p>
            <p className="text-xs text-gray-500">
              Measures return per unit of risk. Above 1 is good, above 2 is excellent.
            </p>
          </div>
          
          <div>
            <p className="text-gray-400 mb-2">
              <span className="font-medium text-gray-300">Max Drawdown:</span> {' '}
              ₹{metrics.maxDrawdown.toLocaleString('en-IN')}
            </p>
            <p className="text-xs text-gray-500">
              Largest peak-to-trough decline. Lower is better.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default RiskMetrics;
