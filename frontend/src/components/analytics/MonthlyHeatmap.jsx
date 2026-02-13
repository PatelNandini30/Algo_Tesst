import React, { useMemo } from 'react';

const MonthlyHeatmap = ({ trades }) => {
  const monthlyData = useMemo(() => {
    const data = {};
    
    trades.forEach(trade => {
      const date = new Date(trade.entry_date);
      const year = date.getFullYear();
      const month = date.getMonth();
      const key = `${year}-${month}`;
      
      if (!data[key]) {
        data[key] = { year, month, pnl: 0, trades: 0 };
      }
      
      data[key].pnl += (trade.pnl || 0);
      data[key].trades += 1;
    });
    
    return Object.values(data);
  }, [trades]);
  
  const years = [...new Set(monthlyData.map(d => d.year))].sort();
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  
  const getColor = (pnl) => {
    if (pnl > 50000) return 'bg-green-600';
    if (pnl > 20000) return 'bg-green-500';
    if (pnl > 0) return 'bg-green-400';
    if (pnl === 0) return 'bg-gray-600';
    if (pnl > -20000) return 'bg-red-400';
    if (pnl > -50000) return 'bg-red-500';
    return 'bg-red-600';
  };
  
  return (
    <div className="overflow-x-auto">
      <div className="inline-block min-w-full">
        <div className="grid gap-2" style={{ gridTemplateColumns: `auto repeat(12, 1fr)` }}>
          {/* Header */}
          <div className="text-xs text-gray-400 font-medium"></div>
          {months.map(month => (
            <div key={month} className="text-xs text-gray-400 font-medium text-center">
              {month}
            </div>
          ))}
          
          {/* Rows */}
          {years.map(year => (
            <React.Fragment key={year}>
              <div className="text-xs text-gray-400 font-medium py-2">{year}</div>
              {months.map((_, monthIndex) => {
                const cell = monthlyData.find(d => d.year === year && d.month === monthIndex);
                const pnl = cell?.pnl || 0;
                
                return (
                  <div
                    key={`${year}-${monthIndex}`}
                    className={`
                      aspect-square rounded ${getColor(pnl)}
                      flex items-center justify-center
                      cursor-pointer hover:ring-2 hover:ring-primary-400
                      transition-all group relative
                    `}
                    title={`${months[monthIndex]} ${year}: ₹${pnl.toLocaleString('en-IN')}`}
                  >
                    <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                      <span className="text-xs font-bold text-white drop-shadow">
                        {(pnl / 1000).toFixed(0)}k
                      </span>
                    </div>
                  </div>
                );
              })}
            </React.Fragment>
          ))}
        </div>
        
        {/* Legend */}
        <div className="flex items-center gap-4 mt-6 text-xs text-gray-400">
          <span>Less</span>
          <div className="flex gap-1">
            <div className="w-4 h-4 bg-red-600 rounded"></div>
            <div className="w-4 h-4 bg-red-500 rounded"></div>
            <div className="w-4 h-4 bg-red-400 rounded"></div>
            <div className="w-4 h-4 bg-gray-600 rounded"></div>
            <div className="w-4 h-4 bg-green-400 rounded"></div>
            <div className="w-4 h-4 bg-green-500 rounded"></div>
            <div className="w-4 h-4 bg-green-600 rounded"></div>
          </div>
          <span>More</span>
        </div>
      </div>
    </div>
  );
};

export default MonthlyHeatmap;
