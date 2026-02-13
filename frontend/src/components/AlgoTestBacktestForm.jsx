import React, { useState } from 'react';
import AlgoTestLegBuilder from './AlgoTestLegBuilder';

const AlgoTestBacktestForm = ({ onRunBacktest }) => {
  const [config, setConfig] = useState({
    index: 'NIFTY',
    from_date: '2024-01-01',
    to_date: '2024-12-31',
    expiry_type: 'WEEKLY',
    entry_dte: 2,
    exit_dte: 0,
    legs: []
  });
  
  const handleSubmit = (e) => {
    e.preventDefault();
    onRunBacktest(config);
  };
  
  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* Index Selection */}
      <div>
        <label className="block text-sm font-medium mb-1">Index</label>
        <select
          value={config.index}
          onChange={(e) => setConfig({...config, index: e.target.value})}
          className="w-full p-2 border rounded"
        >
          <option value="NIFTY">NIFTY 50</option>
          <option value="BANKNIFTY">BANK NIFTY</option>
          <option value="FINNIFTY">FIN NIFTY</option>
          <option value="SENSEX">SENSEX</option>
        </select>
      </div>
      
      {/* Date Range */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium mb-1">From Date</label>
          <input
            type="date"
            value={config.from_date}
            onChange={(e) => setConfig({...config, from_date: e.target.value})}
            className="w-full p-2 border rounded"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">To Date</label>
          <input
            type="date"
            value={config.to_date}
            onChange={(e) => setConfig({...config, to_date: e.target.value})}
            className="w-full p-2 border rounded"
          />
        </div>
      </div>
      
      {/* Expiry Type */}
      <div>
        <label className="block text-sm font-medium mb-1">Expiry Type</label>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setConfig({...config, expiry_type: 'WEEKLY'})}
            className={`flex-1 py-2 px-4 rounded ${
              config.expiry_type === 'WEEKLY' 
                ? 'bg-blue-600 text-white' 
                : 'bg-gray-200'
            }`}
          >
            Weekly
          </button>
          <button
            type="button"
            onClick={() => setConfig({...config, expiry_type: 'MONTHLY'})}
            className={`flex-1 py-2 px-4 rounded ${
              config.expiry_type === 'MONTHLY' 
                ? 'bg-blue-600 text-white' 
                : 'bg-gray-200'
            }`}
          >
            Monthly
          </button>
        </div>
      </div>
      
      {/* Entry DTE */}
      <div>
        <label className="block text-sm font-medium mb-1">
          Entry (Days Before Expiry)
        </label>
        <select
          value={config.entry_dte}
          onChange={(e) => setConfig({...config, entry_dte: parseInt(e.target.value)})}
          className="w-full p-2 border rounded"
        >
          {config.expiry_type === 'WEEKLY' ? (
            <>
              <option value="0">0 (Expiry Day)</option>
              <option value="1">1</option>
              <option value="2">2</option>
              <option value="3">3</option>
              <option value="4">4</option>
            </>
          ) : (
            Array.from({length: 25}, (_, i) => (
              <option key={i} value={i}>{i}{i === 0 ? ' (Expiry Day)' : ''}</option>
            ))
          )}
        </select>
      </div>
      
      {/* Exit DTE */}
      <div>
        <label className="block text-sm font-medium mb-1">
          Exit (Days Before Expiry)
        </label>
        <select
          value={config.exit_dte}
          onChange={(e) => setConfig({...config, exit_dte: parseInt(e.target.value)})}
          className="w-full p-2 border rounded"
        >
          {config.expiry_type === 'WEEKLY' ? (
            <>
              <option value="0">0 (Expiry Day)</option>
              <option value="1">1</option>
              <option value="2">2</option>
              <option value="3">3</option>
              <option value="4">4</option>
            </>
          ) : (
            Array.from({length: 25}, (_, i) => (
              <option key={i} value={i}>{i}{i === 0 ? ' (Expiry Day)' : ''}</option>
            ))
          )}
        </select>
      </div>
      
      {/* Leg Builder */}
      <AlgoTestLegBuilder 
        legs={config.legs}
        onLegsChange={(legs) => setConfig({...config, legs})}
      />
      
      {/* Submit */}
      <button
        type="submit"
        className="w-full py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium"
      >
        Run Backtest
      </button>
    </form>
  );
};

export default AlgoTestBacktestForm;