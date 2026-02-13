import React from 'react';
import { TrendingUp, TrendingDown } from 'lucide-react';

const MetricCard = ({ 
  title, 
  value, 
  change, 
  changeType = 'neutral',
  icon: Icon,
  format = 'number',
  subtitle
}) => {
  const formatValue = (val) => {
    if (format === 'currency') return `₹${val.toLocaleString('en-IN')}`;
    if (format === 'percent') return `${val.toFixed(2)}%`;
    return val.toLocaleString('en-IN');
  };
  
  const changeColors = {
    positive: 'text-profit',
    negative: 'text-loss',
    neutral: 'text-gray-400'
  };
  
  return (
    <div className="bg-dark-card border border-dark-border rounded-xl p-6 hover:shadow-card-hover transition-shadow">
      <div className="flex items-start justify-between mb-4">
        <div className="flex-1">
          <p className="text-sm text-gray-400 mb-1">{title}</p>
          <p className="text-2xl font-bold text-gray-100">{formatValue(value)}</p>
          {subtitle && <p className="text-xs text-gray-500 mt-1">{subtitle}</p>}
        </div>
        {Icon && (
          <div className="p-3 bg-primary-900/20 rounded-lg">
            <Icon className="h-6 w-6 text-primary-400" />
          </div>
        )}
      </div>
      
      {change !== undefined && (
        <div className={`flex items-center gap-1 text-sm ${changeColors[changeType]}`}>
          {changeType === 'positive' && <TrendingUp className="h-4 w-4" />}
          {changeType === 'negative' && <TrendingDown className="h-4 w-4" />}
          <span>{change > 0 ? '+' : ''}{change.toFixed(2)}%</span>
          <span className="text-gray-500 ml-1">vs previous</span>
        </div>
      )}
    </div>
  );
};

export default MetricCard;
