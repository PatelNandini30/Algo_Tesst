import React, { useState } from 'react';
import { Calendar, CreditCard, User, DollarSign, TrendingUp, Settings, Zap, AlertCircle, CheckCircle } from 'lucide-react';
import ResultsPanel from './ResultsPanel';

const ConfigPanel = () => {
  // Core state
  const [uiState, setUiState] = useState({
    index: "NIFTY",
    expiry_window: "weekly_expiry",
    from_date: "2019-01-01",
    to_date: "2026-01-01",
    spot_adjustment_type: 0,
    spot_adjustment: 1.0
  });

  // Leg state
  const [legs, setLegs] = useState({
    ce_sell: false,
    pe_sell: false,
    pe_buy: false,
    fut_buy: false,
    premium_mode: false,
    breach_mode: false,
    hsl_mode: false,
    protection: false
  });

  // Strategy-specific parameters
  const [strategyParams, setStrategyParams] = useState({
    call_sell_position: 1.0,
    put_sell_position: -1.0,
    put_strike_pct_below: 1.0,
    premium_multiplier: 1.0,
    call_premium: true,
    put_premium: true,
    protection_pct: 1.0,
    call_hsl_pct: 100,
    pct_diff: 0.3,
    max_put_spot_pct: 0.04
  });

  const [showResults, setShowResults] = useState(false);
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Handle UI state changes
  const handleUiChange = (field, value) => {
    setUiState(prev => ({ ...prev, [field]: value }));
  };

  // Handle leg selection
  const handleLegChange = (leg, value) => {
    setLegs(prev => ({ ...prev, [leg]: value }));
  };

  // Handle strategy parameter changes
  const handleParamChange = (param, value) => {
    setStrategyParams(prev => ({ ...prev, [param]: value }));
  };

  // Infer engine from leg combination
  const inferEngine = () => {
    const { ce_sell, pe_sell, pe_buy, fut_buy, premium_mode, breach_mode, hsl_mode } = legs;

    if (hsl_mode && ce_sell && fut_buy) return "v8_hsl";
    if (breach_mode && ce_sell) return "v3_strike_breach";
    if (premium_mode && (ce_sell || pe_sell)) return "v7_premium";
    if (ce_sell && pe_buy && fut_buy) return "v8_ce_pe_fut";
    if (ce_sell && pe_sell && !fut_buy) return "v4_strangle";
    if (ce_sell && fut_buy && !pe_sell) return "v1_ce_fut";
    if (pe_sell && fut_buy && !ce_sell) return "v2_pe_fut";
    if (ce_sell && !fut_buy && !pe_sell && legs.protection) return "v5_call";
    if (pe_sell && !fut_buy && !ce_sell && legs.protection) return "v5_put";
    if (ce_sell && pe_sell && pe_buy && fut_buy) return "v9_counter";

    return null;
  };

  // Build payload for backend
  const buildPayload = () => {
    const engine = inferEngine();
    if (!engine) return null;

    // Map spot adjustment type from number to string
    const spotAdjustmentMap = {
      0: "None",
      1: "Rises", 
      2: "Falls",
      3: "RisesOrFalls"
    };

    const base = {
      strategy: engine,
      index: uiState.index,
      date_from: uiState.from_date,
      date_to: uiState.to_date,
      spot_adjustment_type: spotAdjustmentMap[uiState.spot_adjustment_type] || "None",
      spot_adjustment: uiState.spot_adjustment,
    };

    // Add expiry_window for engines that support it
    const expiryEngines = ["v1_ce_fut","v2_pe_fut","v3_strike_breach","v5_call","v5_put","v8_ce_pe_fut","v8_hsl"];
    if (expiryEngines.includes(engine)) {
      base.expiry_window = uiState.expiry_window;
    }

    // Engine-specific parameters
    switch(engine) {
      case "v1_ce_fut":
        base.call_sell_position = strategyParams.call_sell_position;
        base.call_sell = true;
        base.put_sell = false;
        base.put_buy = false;
        base.future_buy = true;
        break;
      case "v2_pe_fut":
        base.put_sell_position = strategyParams.put_sell_position;
        base.call_sell = false;
        base.put_sell = true;
        base.put_buy = false;
        base.future_buy = true;
        break;
      case "v3_strike_breach":
        base.call_sell_position = strategyParams.call_sell_position;
        base.pct_diff = strategyParams.pct_diff;
        base.call_sell = true;
        base.put_sell = false;
        base.put_buy = false;
        base.future_buy = true;
        break;
      case "v4_strangle":
        base.call_sell_position = strategyParams.call_sell_position;
        base.put_sell_position = strategyParams.put_sell_position;
        base.call_sell = true;
        base.put_sell = true;
        base.put_buy = false;
        base.future_buy = false;
        break;
      case "v5_call":
        base.call_sell_position = strategyParams.call_sell_position;
        base.protection = legs.protection;
        base.protection_pct = strategyParams.protection_pct;
        base.call_sell = true;
        base.put_sell = false;
        base.put_buy = true;
        base.future_buy = false;
        break;
      case "v5_put":
        base.put_sell_position = strategyParams.put_sell_position;
        base.protection = legs.protection;
        base.protection_pct = strategyParams.protection_pct;
        base.call_sell = false;
        base.put_sell = true;
        base.put_buy = true;
        base.future_buy = false;
        break;
      case "v7_premium":
        base.call_sell = legs.ce_sell;
        base.put_sell = legs.pe_sell;
        base.call_premium = strategyParams.call_premium;
        base.put_premium = strategyParams.put_premium;
        base.premium_multiplier = strategyParams.premium_multiplier;
        base.put_buy = false;
        base.future_buy = false;
        break;
      case "v8_ce_pe_fut":
        base.call_sell_position = strategyParams.call_sell_position;
        base.put_strike_pct_below = strategyParams.put_strike_pct_below;
        base.call_sell = true;
        base.put_sell = false;
        base.put_buy = true;
        base.future_buy = true;
        break;
      case "v8_hsl":
        base.call_sell_position = strategyParams.call_sell_position;
        base.call_hsl_pct = strategyParams.call_hsl_pct;
        base.call_sell = true;
        base.put_sell = false;
        base.put_buy = false;
        base.future_buy = true;
        break;
      case "v9_counter":
        base.call_sell_position = strategyParams.call_sell_position;
        base.put_strike_pct_below = strategyParams.put_strike_pct_below;
        base.max_put_spot_pct = strategyParams.max_put_spot_pct;
        base.call_sell = true;
        base.put_sell = false;
        base.put_buy = true;
        base.future_buy = true;
        break;
      default:
        return null;
    }

    return base;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    
    const engine = inferEngine();
    if (!engine) {
      setError("Unsupported strategy combination. Please review leg selection.");
      return;
    }

    const payload = buildPayload();
    if (!payload) {
      setError("Failed to build payload. Please check your configuration.");
      return;
    }

    setLoading(true);
    setError(null);
    
    try {
      const response = await fetch('/api/backtest', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      if (response.ok) {
        const data = await response.json();
        setResults(data);
        setShowResults(true);
      } else {
        const errorData = await response.json();
        setError(errorData.detail || "Backtest failed");
      }
    } catch (err) {
      setError("Network error. Please check if backend is running.");
      console.error('Error calling backtest API:', err);
    } finally {
      setLoading(false);
    }
  };

  // Get available expiry windows based on index
  const getAvailableExpiryWindows = () => {
    const monthlyOnly = ["BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"];
    if (monthlyOnly.includes(uiState.index)) {
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

  const engine = inferEngine();

  // Quick setup presets
  const applyPreset = (preset) => {
    const newLegs = { ce_sell: false, pe_sell: false, pe_buy: false, fut_buy: false, premium_mode: false, breach_mode: false, hsl_mode: false, protection: false };
    
    switch(preset) {
      case "v1":
        newLegs.ce_sell = true;
        newLegs.fut_buy = true;
        handleParamChange("call_sell_position", 1.0);
        break;
      case "v2":
        newLegs.pe_sell = true;
        newLegs.fut_buy = true;
        handleParamChange("put_sell_position", -1.0);
        break;
      case "v4":
        newLegs.ce_sell = true;
        newLegs.pe_sell = true;
        handleParamChange("call_sell_position", 1.0);
        handleParamChange("put_sell_position", -1.0);
        break;
      case "v8":
        newLegs.ce_sell = true;
        newLegs.pe_buy = true;
        newLegs.fut_buy = true;
        handleParamChange("call_sell_position", 1.0);
        handleParamChange("put_strike_pct_below", 1.0);
        break;
      default:
        break;
    }
    
    setLegs(newLegs);
  };

  return (
    <div className="flex flex-col min-h-screen">
      {/* Simple Header */}
      <nav className="bg-gray-900 text-white px-6 py-3 flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <Zap className="h-6 w-6 text-blue-500" />
          <span className="text-xl font-bold">AlgoTest Backtest</span>
        </div>
        <div className="flex items-center space-x-4">
          <div className="flex items-center space-x-2">
            <div className="h-8 w-8 rounded-full bg-gray-700 flex items-center justify-center">
              <User className="h-4 w-4" />
            </div>
            <span className="bg-gray-700 px-2 py-1 rounded text-xs">Backtest Mode</span>
          </div>
        </div>
      </nav>

      {/* Main Content */}
      <div className="flex-1 p-6">
        <div className="max-w-7xl mx-auto">
          <form onSubmit={handleSubmit}>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Left Column - Strategy Builder */}
              <div className="space-y-6">
                {/* Leg Builder Card */}
                <div className="bg-white rounded-lg shadow p-6">
                  <h3 className="text-lg font-semibold mb-4">Strategy Builder</h3>
                  
                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">Select Legs</label>
                      <div className="grid grid-cols-2 gap-2">
                        <button
                          type="button"
                          onClick={() => handleLegChange('ce_sell', !legs.ce_sell)}
                          className={`py-2 px-3 text-sm font-medium rounded-md border ${
                            legs.ce_sell 
                              ? 'bg-blue-600 text-white border-blue-600' 
                              : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                          }`}
                        >
                          CE Sell
                        </button>
                        <button
                          type="button"
                          onClick={() => handleLegChange('pe_sell', !legs.pe_sell)}
                          className={`py-2 px-3 text-sm font-medium rounded-md border ${
                            legs.pe_sell 
                              ? 'bg-blue-600 text-white border-blue-600' 
                              : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                          }`}
                        >
                          PE Sell
                        </button>
                        <button
                          type="button"
                          onClick={() => handleLegChange('pe_buy', !legs.pe_buy)}
                          className={`py-2 px-3 text-sm font-medium rounded-md border ${
                            legs.pe_buy 
                              ? 'bg-green-600 text-white border-green-600' 
                              : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                          }`}
                        >
                          PE Buy
                        </button>
                        <button
                          type="button"
                          onClick={() => handleLegChange('fut_buy', !legs.fut_buy)}
                          className={`py-2 px-3 text-sm font-medium rounded-md border ${
                            legs.fut_buy 
                              ? 'bg-purple-600 text-white border-purple-600' 
                              : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                          }`}
                        >
                          Future Buy
                        </button>
                      </div>
                    </div>

                    {/* Strategy Modes */}
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">Strategy Modes</label>
                      <div className="grid grid-cols-3 gap-2">
                        <button
                          type="button"
                          onClick={() => handleLegChange('premium_mode', !legs.premium_mode)}
                          className={`py-2 px-2 text-xs font-medium rounded-md border ${
                            legs.premium_mode 
                              ? 'bg-yellow-600 text-white border-yellow-600' 
                              : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                          }`}
                        >
                          Premium Mode
                        </button>
                        <button
                          type="button"
                          onClick={() => handleLegChange('breach_mode', !legs.breach_mode)}
                          className={`py-2 px-2 text-xs font-medium rounded-md border ${
                            legs.breach_mode 
                              ? 'bg-red-600 text-white border-red-600' 
                              : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                          }`}
                        >
                          Breach Mode
                        </button>
                        <button
                          type="button"
                          onClick={() => handleLegChange('hsl_mode', !legs.hsl_mode)}
                          className={`py-2 px-2 text-xs font-medium rounded-md border ${
                            legs.hsl_mode 
                              ? 'bg-orange-600 text-white border-orange-600' 
                              : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                          }`}
                        >
                          HSL Mode
                        </button>
                      </div>
                    </div>

                    {/* Protection Toggle */}
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">Protection</label>
                      <button
                        type="button"
                        onClick={() => handleLegChange('protection', !legs.protection)}
                        className={`w-full py-2 px-3 text-sm font-medium rounded-md border ${
                          legs.protection 
                            ? 'bg-indigo-600 text-white border-indigo-600' 
                            : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                        }`}
                      >
                        {legs.protection ? "Protection Enabled" : "Add Protection"}
                      </button>
                    </div>

                    {/* Strategy Validation */}
                    <div className={`p-3 rounded-md ${
                      engine 
                        ? 'bg-green-50 border border-green-200' 
                        : 'bg-yellow-50 border border-yellow-200'
                    }`}>
                      <div className="flex items-center">
                        {engine ? (
                          <>
                            <CheckCircle className="h-5 w-5 text-green-500 mr-2" />
                            <span className="text-sm font-medium text-green-800">
                              Engine: {engine.toUpperCase()}
                            </span>
                          </>
                        ) : (
                          <>
                            <AlertCircle className="h-5 w-5 text-yellow-500 mr-2" />
                            <span className="text-sm font-medium text-yellow-800">
                              Select a valid leg combination
                            </span>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Quick Presets */}
                <div className="bg-white rounded-lg shadow p-6">
                  <h3 className="text-lg font-semibold mb-4">Quick Presets</h3>
                  <div className="grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      onClick={() => applyPreset('v1')}
                      className="py-2 px-3 text-sm bg-gray-100 hover:bg-blue-100 rounded-md"
                    >
                      V1: CE + FUT
                    </button>
                    <button
                      type="button"
                      onClick={() => applyPreset('v2')}
                      className="py-2 px-3 text-sm bg-gray-100 hover:bg-blue-100 rounded-md"
                    >
                      V2: PE + FUT
                    </button>
                    <button
                      type="button"
                      onClick={() => applyPreset('v4')}
                      className="py-2 px-3 text-sm bg-gray-100 hover:bg-blue-100 rounded-md"
                    >
                      V4: Strangle
                    </button>
                    <button
                      type="button"
                      onClick={() => applyPreset('v8')}
                      className="py-2 px-3 text-sm bg-gray-100 hover:bg-blue-100 rounded-md"
                    >
                      V8: Hedged
                    </button>
                  </div>
                </div>
              </div>

              {/* Right Column - Parameters */}
              <div className="space-y-6">
                {/* Instrument Settings */}
                <div className="bg-white rounded-lg shadow p-6">
                  <h3 className="text-lg font-semibold mb-4">Instrument Settings</h3>
                  
                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Index</label>
                      <select
                        value={uiState.index}
                        onChange={(e) => handleUiChange('index', e.target.value)}
                        className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                      >
                        {["NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"].map(opt => (
                          <option key={opt} value={opt}>{opt}</option>
                        ))}
                      </select>
                    </div>
                    
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Expiry Window</label>
                      <select
                        value={uiState.expiry_window}
                        onChange={(e) => handleUiChange('expiry_window', e.target.value)}
                        className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                      >
                        {getAvailableExpiryWindows().map(opt => (
                          <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                </div>

                {/* Strategy Parameters */}
                <div className="bg-white rounded-lg shadow p-6">
                  <h3 className="text-lg font-semibold mb-4">Strategy Parameters</h3>
                  
                  <div className="space-y-4">
                    {/* Strike Parameters */}
                    {(legs.ce_sell || legs.pe_sell) && (
                      <div className="grid grid-cols-2 gap-4">
                        {legs.ce_sell && (
                          <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">CE Strike %</label>
                            <input
                              type="number"
                              step="0.01"
                              value={strategyParams.call_sell_position}
                              onChange={(e) => handleParamChange('call_sell_position', parseFloat(e.target.value))}
                              className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                            />
                            <p className="text-xs text-gray-500 mt-1">Positive = OTM, Negative = ITM</p>
                          </div>
                        )}
                        {legs.pe_sell && (
                          <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">PE Strike %</label>
                            <input
                              type="number"
                              step="0.01"
                              value={strategyParams.put_sell_position}
                              onChange={(e) => handleParamChange('put_sell_position', parseFloat(e.target.value))}
                              className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                            />
                            <p className="text-xs text-gray-500 mt-1">Negative = OTM, Positive = ITM</p>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Protection Parameters */}
                    {legs.protection && (
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">Protection %</label>
                        <input
                          type="number"
                          step="0.01"
                          value={strategyParams.protection_pct}
                          onChange={(e) => handleParamChange('protection_pct', parseFloat(e.target.value))}
                          className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                        />
                        <p className="text-xs text-gray-500 mt-1">Distance from sold strike</p>
                      </div>
                    )}

                    {/* HSL Parameters */}
                    {legs.hsl_mode && (
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">Hard Stop Loss %</label>
                        <input
                          type="number"
                          value={strategyParams.call_hsl_pct}
                          onChange={(e) => handleParamChange('call_hsl_pct', parseInt(e.target.value))}
                          className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                        />
                        <p className="text-xs text-gray-500 mt-1">Stop when premium increases by %</p>
                      </div>
                    )}
                  </div>
                </div>

                {/* Date Range */}
                <div className="bg-white rounded-lg shadow p-6">
                  <h3 className="text-lg font-semibold mb-4">Date Range</h3>
                  
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">From Date</label>
                      <input
                        type="date"
                        value={uiState.from_date}
                        onChange={(e) => handleUiChange('from_date', e.target.value)}
                        className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                      />
                    </div>
                    
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">To Date</label>
                      <input
                        type="date"
                        value={uiState.to_date}
                        onChange={(e) => handleUiChange('to_date', e.target.value)}
                        className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                      />
                    </div>
                  </div>
                  
                  <button
                    type="button"
                    onClick={() => {
                      handleUiChange('from_date', "2019-01-01");
                      handleUiChange('to_date', "2026-01-01");
                    }}
                    className="mt-2 w-full py-2 px-4 border border-gray-300 rounded-md text-sm font-medium text-gray-700 bg-white hover:bg-gray-50"
                  >
                    All Data
                  </button>
                </div>
              </div>
            </div>

            {/* Error Display */}
            {error && (
              <div className="mt-4 p-4 bg-red-50 border border-red-200 rounded-md">
                <div className="flex">
                  <AlertCircle className="h-5 w-5 text-red-400 mr-2" />
                  <div className="text-sm text-red-700">{error}</div>
                </div>
              </div>
            )}

            {/* Action Bar */}
            <div className="mt-6 bg-white rounded-lg shadow p-4 flex justify-between items-center">
              <div className="text-sm text-gray-600">
                {engine ? (
                  <span>Ready to run: <span className="font-medium text-blue-600">{engine.toUpperCase()}</span></span>
                ) : (
                  <span className="text-yellow-600">Select valid leg combination to enable backtest</span>
                )}
              </div>
              
              <button
                type="submit"
                disabled={!engine || loading}
                className={`px-6 py-2 rounded-md text-sm font-medium flex items-center ${
                  engine && !loading
                    ? 'bg-blue-600 hover:bg-blue-700 text-white'
                    : 'bg-gray-300 text-gray-500 cursor-not-allowed'
                }`}
              >
                {loading ? (
                  <>
                    <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white mr-2"></div>
                    Running Backtest...
                  </>
                ) : (
                  <>
                    Start Backtest
                    <TrendingUp className="ml-2 h-4 w-4" />
                  </>
                )}
              </button>
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

export default ConfigPanel;