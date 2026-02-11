import React, { useState } from 'react';
import { Calendar, CreditCard, User, DollarSign, TrendingUp, Settings, Zap } from 'lucide-react';
import LegBuilder from './LegBuilder';
import ResultsPanel from './ResultsPanel';

const ConfigPanel = () => {
  const [strategyConfig, setStrategyConfig] = useState({
    strategy_version: "v1",
    expiry_window: "weekly_expiry",
    spot_adjustment_type: 0,
    spot_adjustment: 1.0,
    call_sell_position: 0.0,
    put_sell_position: 0.0,
    put_strike_pct_below: 1.0,
    protection: false,
    protection_pct: 1.0,
    call_premium: true,
    put_premium: true,
    premium_multiplier: 1.0,
    call_sell: true,
    put_sell: true,
    call_hsl_pct: 100,
    put_hsl_pct: 100,
    max_put_spot_pct: 0.04,
    pct_diff: 0.3,
    from_date: "2019-01-01",
    to_date: "2026-01-02",
    index: "NIFTY"
  });

  const [showResults, setShowResults] = useState(false);
  const [results, setResults] = useState(null);

  const handleInputChange = (field, value) => {
    setStrategyConfig(prev => ({
      ...prev,
      [field]: value
    }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      const response = await fetch('/api/backtest', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(strategyConfig),
      });

      if (response.ok) {
        const data = await response.json();
        setResults(data);
        setShowResults(true);
      } else {
        console.error('Backtest failed:', await response.text());
      }
    } catch (error) {
      console.error('Error calling backtest API:', error);
    }
  };

  const presetStrategies = [
    { name: "CE Sell + FUT Buy (V1)", version: "v1", description: "Sell Call + Buy Future" },
    { name: "PE Sell + FUT Buy (V2)", version: "v2", description: "Sell Put + Buy Future" },
    { name: "Short Strangle (V4)", version: "v4", description: "Sell Call + Sell Put" },
    { name: "Hedged Bull (V8)", version: "v8_ce_pe_fut", description: "CE Sell + PE Buy + FUT Buy" },
    { name: "Protected CE Sell (V5 Call)", version: "v5_call", description: "CE Sell + Protective CE Buy" },
    { name: "Protected PE Sell (V5 Put)", version: "v5_put", description: "PE Sell + Protective PE Buy" },
    { name: "Premium-Based Strangle (V7)", version: "v7", description: "Premium-based strike selection" },
    { name: "Counter-Expiry (V9)", version: "v9", description: "Dynamic put expiry based on week" }
  ];

  const applyPreset = (preset) => {
    const newConfig = { ...strategyConfig, strategy_version: preset.version };
    
    // Apply specific preset values
    switch(preset.version) {
      case "v8_ce_pe_fut":
        newConfig.call_sell_position = 1.0;
        newConfig.put_strike_pct_below = 1.0;
        break;
      case "v4":
        newConfig.call_sell_position = 1.0;
        newConfig.put_sell_position = -1.0;
        break;
      default:
        break;
    }
    
    setStrategyConfig(newConfig);
  };

  return (
    <div className="flex flex-col min-h-screen">
      {/* Top Navigation Bar */}
      <nav className="bg-gray-900 text-white px-6 py-3 flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <Zap className="h-6 w-6 text-blue-500" />
          <span className="text-xl font-bold">AlgoTest</span>
        </div>
        <div className="flex space-x-6">
          {['Backtest', 'Algo Trade', 'Signals', 'RA Algos', 'ClickTrade', 'Webinars'].map((tab, index) => (
            <button
              key={tab}
              className={`pb-1 ${index === 0 ? 'border-b-2 border-blue-500 text-blue-400' : 'text-gray-300 hover:text-white'}`}
            >
              {tab}
            </button>
          ))}
        </div>
        <div className="flex items-center space-x-4">
          <a href="#" className="text-gray-300 hover:text-white">Pricing</a>
          <button className="bg-blue-600 hover:bg-blue-700 px-4 py-1 rounded text-sm">
            Broker Setup
          </button>
          <div className="flex items-center space-x-2">
            <div className="h-8 w-8 rounded-full bg-gray-700 flex items-center justify-center">
              <User className="h-4 w-4" />
            </div>
            <span className="bg-gray-700 px-2 py-1 rounded text-xs">Credits: 0</span>
          </div>
        </div>
      </nav>

      {/* Strategy Type Tabs */}
      <div className="bg-white border-b">
        <div className="flex max-w-7xl mx-auto px-6">
          {[
            { name: "Weekly & Monthly Expiries", indices: ["NIFTY", "SENSEX"] },
            { name: "Monthly Only Expiry", indices: ["MIDCPNIFTY", "BANKNIFTY", "FINNIFTY", "BANKEX"] },
            { name: "Stocks — Cash / F&O", indices: ["ALL NIFTY 500 STOCKS"] },
            { name: "Delta Exchange", indices: ["BTCUSD", "ETHUSD"], newBadge: true }
          ].map((tab, index) => (
            <button
              key={tab.name}
              className={`px-4 py-3 text-sm font-medium ${
                index === 0 
                  ? 'text-blue-600 border-b-2 border-blue-600' 
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              <div className="flex items-center">
                {tab.name}
                {tab.newBadge && <span className="ml-2 bg-green-100 text-green-800 text-xs px-2 py-1 rounded">New</span>}
                {tab.name === "Delta Exchange" && <TrendingUp className="ml-1 h-4 w-4" />}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 p-6">
        <div className="max-w-7xl mx-auto">
          <form onSubmit={handleSubmit}>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Left Column - Instrument Settings and Leg Builder */}
              <div className="space-y-6">
                {/* Instrument Settings Card */}
                <div className="bg-white rounded-lg shadow p-6">
                  <h3 className="text-lg font-semibold mb-4">Instrument Settings</h3>
                  
                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Index</label>
                      <select
                        value={strategyConfig.index}
                        onChange={(e) => handleInputChange('index', e.target.value)}
                        className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                      >
                        {['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX'].map(opt => (
                          <option key={opt} value={opt}>{opt}</option>
                        ))}
                      </select>
                    </div>
                    
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Underlying From</label>
                      <div className="flex rounded-md shadow-sm">
                        <button
                          type="button"
                          className={`flex-1 py-2 px-4 text-sm font-medium rounded-l-md ${
                            true 
                              ? 'bg-blue-600 text-white' 
                              : 'bg-white text-gray-700'
                          }`}
                        >
                          Cash
                        </button>
                        <button
                          type="button"
                          className={`flex-1 py-2 px-4 text-sm font-medium rounded-r-md ${
                            false 
                              ? 'bg-blue-600 text-white' 
                              : 'bg-white text-gray-700'
                          }`}
                        >
                          Futures
                        </button>
                      </div>
                    </div>
                    
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Expiry Type</label>
                      <div className="flex rounded-md shadow-sm">
                        <button
                          type="button"
                          className={`flex-1 py-2 px-4 text-sm font-medium rounded-l-md ${
                            strategyConfig.expiry_window.includes('weekly')
                              ? 'bg-blue-600 text-white' 
                              : 'bg-white text-gray-700'
                          }`}
                          onClick={() => handleInputChange('expiry_window', 'weekly_expiry')}
                        >
                          Weekly
                        </button>
                        <button
                          type="button"
                          className={`flex-1 py-2 px-4 text-sm font-medium rounded-r-md ${
                            strategyConfig.expiry_window.includes('monthly')
                              ? 'bg-blue-600 text-white' 
                              : 'bg-white text-gray-700'
                          }`}
                          onClick={() => handleInputChange('expiry_window', 'monthly_expiry')}
                        >
                          Monthly
                        </button>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Leg Builder Card */}
                <div className="bg-white rounded-lg shadow p-6">
                  <h3 className="text-lg font-semibold mb-4">Leg Settings</h3>
                  
                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Square Off</label>
                      <div className="flex rounded-md shadow-sm">
                        <button
                          type="button"
                          className={`flex-1 py-2 px-4 text-sm font-medium rounded-l-md ${
                            true 
                              ? 'bg-blue-600 text-white' 
                              : 'bg-white text-gray-700'
                          }`}
                        >
                          Partial
                        </button>
                        <button
                          type="button"
                          className={`flex-1 py-2 px-4 text-sm font-medium rounded-r-md ${
                            false 
                              ? 'bg-blue-600 text-white' 
                              : 'bg-white text-gray-700'
                          }`}
                        >
                          Complete
                        </button>
                      </div>
                    </div>
                    
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Trail SL to Breakeven</label>
                      <div className="flex items-center">
                        <input
                          type="checkbox"
                          checked={false}
                          className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
                        />
                        <span className="ml-2 text-sm text-gray-700">All Legs</span>
                        <span className="ml-4 text-sm text-gray-700">SL Legs</span>
                      </div>
                    </div>
                    
                    <div>
                      <button
                        type="button"
                        className="w-full py-2 px-4 border border-gray-300 rounded-md text-sm font-medium text-gray-700 bg-white hover:bg-gray-50"
                      >
                        + Add Leg
                      </button>
                    </div>
                  </div>
                </div>

                {/* Strategy Presets */}
                <div className="bg-white rounded-lg shadow p-6">
                  <h3 className="text-lg font-semibold mb-4">Strategy Presets</h3>
                  
                  <div className="flex overflow-x-auto pb-2 space-x-2">
                    {presetStrategies.map((preset) => (
                      <button
                        key={preset.name}
                        type="button"
                        onClick={() => applyPreset(preset)}
                        className="flex-shrink-0 px-4 py-2 bg-gray-100 hover:bg-blue-100 rounded-md text-sm font-medium text-gray-800 border border-gray-300 min-w-max"
                      >
                        {preset.name}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* Right Column - Entry Settings and Adjustments */}
              <div className="space-y-6">
                {/* Entry Settings Card */}
                <div className="bg-white rounded-lg shadow p-6">
                  <h3 className="text-lg font-semibold mb-4">Entry Settings</h3>
                  
                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Strategy Type</label>
                      <div className="flex rounded-md shadow-sm">
                        <button
                          type="button"
                          className={`flex-1 py-2 px-4 text-sm font-medium ${
                            true 
                              ? 'bg-blue-600 text-white' 
                              : 'bg-white text-gray-700'
                          }`}
                        >
                          Intraday
                        </button>
                        <button
                          type="button"
                          className={`flex-1 py-2 px-4 text-sm font-medium ${
                            true 
                              ? 'bg-blue-600 text-white' 
                              : 'bg-white text-gray-700'
                          }`}
                        >
                          BTST
                        </button>
                        <button
                          type="button"
                          className={`flex-1 py-2 px-4 text-sm font-medium rounded-r-md ${
                            true 
                              ? 'bg-blue-600 text-white' 
                              : 'bg-white text-gray-700'
                          }`}
                        >
                          Positional
                        </button>
                      </div>
                    </div>
                    
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">Entry Time</label>
                        <input
                          type="time"
                          defaultValue="09:35"
                          className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                        />
                      </div>
                      
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">Exit Time</label>
                        <input
                          type="time"
                          defaultValue="15:15"
                          className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                        />
                      </div>
                    </div>
                    
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">No Re-entry After</label>
                      <div className="flex items-center">
                        <input
                          type="checkbox"
                          checked={false}
                          className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
                        />
                        <input
                          type="time"
                          defaultValue="09:35"
                          className="ml-2 w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                        />
                      </div>
                    </div>
                    
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Overall Momentum</label>
                      <div className="flex items-center">
                        <input
                          type="checkbox"
                          checked={false}
                          className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
                        />
                        <select className="ml-2 p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                          <option>Points (Pts)</option>
                        </select>
                        <input
                          type="number"
                          step="0.01"
                          className="ml-2 w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                        />
                      </div>
                    </div>
                  </div>
                </div>

                {/* Adjustment/Re-Entry Card */}
                <div className="bg-white rounded-lg shadow p-6">
                  <h3 className="text-lg font-semibold mb-4">Adjustment / Re-Entry</h3>
                  
                  <div className="space-y-4">
                    {[
                      { value: 0, label: "No Adjustment" },
                      { value: 1, label: "Spot Rises By X%" },
                      { value: 2, label: "Spot Falls By X%" },
                      { value: 3, label: "Spot Rises or Falls By X%" }
                    ].map((option) => (
                      <div key={option.value} className="flex items-center">
                        <input
                          type="radio"
                          id={`adjustment-${option.value}`}
                          name="spot_adjustment_type"
                          value={option.value}
                          checked={strategyConfig.spot_adjustment_type === option.value}
                          onChange={(e) => handleInputChange('spot_adjustment_type', parseInt(e.target.value))}
                          className="h-4 w-4 text-blue-600 focus:ring-blue-500"
                        />
                        <label htmlFor={`adjustment-${option.value}`} className="ml-2 block text-sm text-gray-700">
                          {option.label}
                        </label>
                        {option.value !== 0 && (
                          <input
                            type="number"
                            step="0.01"
                            value={strategyConfig.spot_adjustment}
                            onChange={(e) => handleInputChange('spot_adjustment', parseFloat(e.target.value))}
                            className="ml-2 w-20 p-1 border border-gray-300 rounded-md text-sm"
                          />
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                {/* Date Range Picker */}
                <div className="bg-white rounded-lg shadow p-6">
                  <h3 className="text-lg font-semibold mb-4">Date Range</h3>
                  
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">From Date</label>
                      <input
                        type="date"
                        value={strategyConfig.from_date}
                        onChange={(e) => handleInputChange('from_date', e.target.value)}
                        className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                      />
                    </div>
                    
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">To Date</label>
                      <input
                        type="date"
                        value={strategyConfig.to_date}
                        onChange={(e) => handleInputChange('to_date', e.target.value)}
                        className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                      />
                    </div>
                  </div>
                  
                  <button
                    type="button"
                    className="mt-2 w-full py-2 px-4 border border-gray-300 rounded-md text-sm font-medium text-gray-700 bg-white hover:bg-gray-50"
                  >
                    All Data
                  </button>
                </div>
              </div>
            </div>

            {/* Bottom Action Bar */}
            <div className="mt-6 bg-white rounded-lg shadow p-4 flex justify-between">
              <div className="flex space-x-2">
                <button
                  type="button"
                  className="px-4 py-2 bg-gray-100 hover:bg-gray-200 rounded-md text-sm font-medium text-gray-700"
                >
                  Save Strategy
                </button>
                <button
                  type="button"
                  className="px-4 py-2 bg-gray-100 hover:bg-gray-200 rounded-md text-sm font-medium text-gray-700"
                >
                  Export .algtst
                </button>
                <button
                  type="button"
                  className="px-4 py-2 bg-gray-100 hover:bg-gray-200 rounded-md text-sm font-medium text-gray-700"
                >
                  Import .algtst
                </button>
              </div>
              
              <button
                type="submit"
                className="px-6 py-2 bg-blue-600 hover:bg-blue-700 rounded-md text-sm font-medium text-white flex items-center"
              >
                Start Backtest
                <TrendingUp className="ml-2 h-4 w-4" />
              </button>
              
              <div>
                <button
                  type="button"
                  className="px-4 py-2 bg-gray-100 hover:bg-gray-200 rounded-md text-sm font-medium text-gray-700 flex items-center"
                >
                  <PrinterIcon className="h-4 w-4 mr-1" />
                  PDF
                </button>
              </div>
            </div>
          </form>
        </div>
      </div>
      
      {showResults && results && (
        <ResultsPanel 
          results={results} 
          onClose={() => setShowResults(false)} 
        />
      )}
    </div>
  );
};

// PrinterIcon component for PDF button
const PrinterIcon = ({ className }) => (
  <svg xmlns="http://www.w3.org/2000/svg" className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z" />
  </svg>
);

export default ConfigPanel;