import React, { useState, useEffect } from 'react';
import { Play, Filter, Calendar, Settings, TrendingUp, Download, X } from 'lucide-react';
import ResultsPanel from './ResultsPanel';

const StrategyBuilder = () => {
  // State management
  const [strategies, setStrategies] = useState([]);
  const [selectedStrategy, setSelectedStrategy] = useState(null);
  const [parameters, setParameters] = useState({});
  const [filters, setFilters] = useState({
    index: 'NIFTY',
    from_date: '2019-01-01',
    to_date: '2026-01-01',
    expiry_window: 'weekly_expiry'
  });
  const [showFilters, setShowFilters] = useState(false);
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);

  // Fetch available strategies on mount
  useEffect(() => {
    fetchStrategies();
  }, []);

  const fetchStrategies = async () => {
    try {
      const response = await fetch('/api/strategies');
      if (response.ok) {
        const data = await response.json();
        setStrategies(data.strategies);
      }
    } catch (err) {
      console.error('Failed to fetch strategies:', err);
    }
  };

  // Handle strategy selection
  const handleStrategySelect = (strategy) => {
    setSelectedStrategy(strategy);
    setParameters(strategy.defaults || {});
    setError(null);
  };

  // Handle parameter change
  const handleParameterChange = (key, value) => {
    setParameters(prev => ({ ...prev, [key]: value }));
  };

  // Handle filter change
  const handleFilterChange = (key, value) => {
    setFilters(prev => ({ ...prev, [key]: value }));
  };

  // Build backtest payload
  const buildPayload = () => {
    if (!selectedStrategy) return null;

    const spotAdjustmentMap = {
      0: "None",
      1: "Rises",
      2: "Falls",
      3: "RisesOrFalls"
    };

    return {
      strategy: selectedStrategy.version,
      index: filters.index,
      date_from: filters.from_date,
      date_to: filters.to_date,
      expiry_window: filters.expiry_window,
      spot_adjustment_type: spotAdjustmentMap[parameters.spot_adjustment_type || 0],
      spot_adjustment: parameters.spot_adjustment || 1.0,
      ...parameters
    };
  };

  // Run backtest
  const runBacktest = async () => {
    const payload = buildPayload();
    if (!payload) {
      setError('Please select a strategy');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (response.ok) {
        const data = await response.json();
        setResults(data);
      } else {
        const errorData = await response.json();
        setError(errorData.detail || 'Backtest failed');
      }
    } catch (err) {
      setError('Network error. Please check if backend is running.');
      console.error('Backtest error:', err);
    } finally {
      setLoading(false);
    }
  };

  // Get available expiry windows
  const getExpiryWindows = () => {
    const monthlyOnly = ["BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"];
    if (monthlyOnly.includes(filters.index)) {
      return [
        { value: "monthly_expiry", label: "Monthly Expiry" },
        { value: "monthly_t1", label: "Monthly T+1" }
      ];
    }
    return [
      { value: "weekly_expiry", label: "Weekly Expiry" },
      { value: "weekly_t1", label: "Weekly T+1" },
      { value: "weekly_t2", label: "Weekly T+2" },
      { value: "monthly_expiry", label: "Monthly Expiry" },
      { value: "monthly_t1", label: "Monthly T+1" }
    ];
  };

  if (results) {
    return <ResultsPanel results={results} onClose={() => setResults(null)} />;
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex justify-between items-center">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">Strategy Backtest</h1>
              <p className="text-sm text-gray-500 mt-1">Select a strategy and configure parameters</p>
            </div>
            <button
              onClick={() => setShowFilters(!showFilters)}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
            >
              <Filter size={18} />
              {showFilters ? 'Hide Filters' : 'Show Filters'}
            </button>
          </div>
        </div>
      </div>

      {/* Filters Panel */}
      {showFilters && (
        <div className="bg-white border-b shadow-sm">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Index</label>
                <select
                  value={filters.index}
                  onChange={(e) => handleFilterChange('index', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                >
                  <option value="NIFTY">NIFTY</option>
                  <option value="SENSEX">SENSEX</option>
                  <option value="BANKNIFTY">BANKNIFTY</option>
                  <option value="FINNIFTY">FINNIFTY</option>
                  <option value="MIDCPNIFTY">MIDCPNIFTY</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Expiry Window</label>
                <select
                  value={filters.expiry_window}
                  onChange={(e) => handleFilterChange('expiry_window', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                >
                  {getExpiryWindows().map(opt => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">From Date</label>
                <input
                  type="date"
                  value={filters.from_date}
                  onChange={(e) => handleFilterChange('from_date', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">To Date</label>
                <input
                  type="date"
                  value={filters.to_date}
                  onChange={(e) => handleFilterChange('to_date', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Main Content */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Strategy Selection */}
          <div className="lg:col-span-1">
            <div className="bg-white rounded-lg shadow">
              <div className="p-4 border-b">
                <h2 className="text-lg font-semibold text-gray-900">Available Strategies</h2>
              </div>
              <div className="p-2 max-h-[600px] overflow-y-auto">
                {strategies.map((strategy) => (
                  <button
                    key={strategy.version}
                    onClick={() => handleStrategySelect(strategy)}
                    className={`w-full text-left p-3 rounded-lg mb-2 transition-colors ${
                      selectedStrategy?.version === strategy.version
                        ? 'bg-blue-50 border-2 border-blue-500'
                        : 'bg-gray-50 border-2 border-transparent hover:bg-gray-100'
                    }`}
                  >
                    <div className="font-medium text-gray-900">{strategy.name}</div>
                    <div className="text-xs text-gray-500 mt-1">{strategy.description}</div>
                    <div className="text-xs text-blue-600 mt-1 font-medium">{strategy.version.toUpperCase()}</div>
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Parameters Configuration */}
          <div className="lg:col-span-2">
            {selectedStrategy ? (
              <div className="bg-white rounded-lg shadow">
                <div className="p-4 border-b">
                  <h2 className="text-lg font-semibold text-gray-900">{selectedStrategy.name}</h2>
                  <p className="text-sm text-gray-500 mt-1">{selectedStrategy.description}</p>
                </div>

                <div className="p-6">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {Object.entries(selectedStrategy.parameters).map(([key, description]) => {
                      const value = parameters[key] ?? selectedStrategy.defaults[key];
                      
                      // Determine input type
                      if (typeof value === 'boolean') {
                        return (
                          <div key={key} className="col-span-2">
                            <label className="flex items-center space-x-2">
                              <input
                                type="checkbox"
                                checked={value}
                                onChange={(e) => handleParameterChange(key, e.target.checked)}
                                className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
                              />
                              <span className="text-sm font-medium text-gray-700">{description}</span>
                            </label>
                          </div>
                        );
                      }

                      if (key === 'spot_adjustment_type') {
                        return (
                          <div key={key}>
                            <label className="block text-sm font-medium text-gray-700 mb-1">
                              {description}
                            </label>
                            <select
                              value={value}
                              onChange={(e) => handleParameterChange(key, parseInt(e.target.value))}
                              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                            >
                              <option value={0}>None</option>
                              <option value={1}>Spot Rises</option>
                              <option value={2}>Spot Falls</option>
                              <option value={3}>Spot Rises or Falls</option>
                            </select>
                          </div>
                        );
                      }

                      if (key === 'expiry_window') {
                        return (
                          <div key={key}>
                            <label className="block text-sm font-medium text-gray-700 mb-1">
                              {description}
                            </label>
                            <select
                              value={value}
                              onChange={(e) => handleParameterChange(key, e.target.value)}
                              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                            >
                              {getExpiryWindows().map(opt => (
                                <option key={opt.value} value={opt.value}>{opt.label}</option>
                              ))}
                            </select>
                          </div>
                        );
                      }

                      return (
                        <div key={key}>
                          <label className="block text-sm font-medium text-gray-700 mb-1">
                            {description}
                          </label>
                          <input
                            type="number"
                            step="0.01"
                            value={value}
                            onChange={(e) => handleParameterChange(key, parseFloat(e.target.value))}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                          />
                        </div>
                      );
                    })}
                  </div>

                  {/* Error Display */}
                  {error && (
                    <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg">
                      <p className="text-sm text-red-700">{error}</p>
                    </div>
                  )}

                  {/* Run Button */}
                  <div className="mt-6 flex justify-end">
                    <button
                      onClick={runBacktest}
                      disabled={loading}
                      className={`flex items-center gap-2 px-6 py-3 rounded-lg font-medium ${
                        loading
                          ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                          : 'bg-blue-600 text-white hover:bg-blue-700'
                      }`}
                    >
                      {loading ? (
                        <>
                          <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-white"></div>
                          Running Backtest...
                        </>
                      ) : (
                        <>
                          <Play size={18} />
                          Run Backtest
                        </>
                      )}
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <div className="bg-white rounded-lg shadow p-12 text-center">
                <TrendingUp className="mx-auto h-12 w-12 text-gray-400" />
                <h3 className="mt-4 text-lg font-medium text-gray-900">No Strategy Selected</h3>
                <p className="mt-2 text-sm text-gray-500">
                  Select a strategy from the list to configure and run a backtest
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default StrategyBuilder;
