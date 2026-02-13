import React, { useState } from 'react';
import { Calendar, CreditCard, User, DollarSign, TrendingUp, Settings, Zap, AlertCircle, CheckCircle } from 'lucide-react';
import ResultsPanel from './ResultsPanel';
import LegBuilder from './LegBuilder';

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

  // Legacy leg state (for backward compatibility)
  const [legacyLegs, setLegacyLegs] = useState({
    ce_sell: false,
    pe_sell: false,
    pe_buy: false,
    fut_buy: false,
    premium_mode: false,
    breach_mode: false,
    hsl_mode: false,
    protection: false
  });

  // Dynamic legs state (for new multi-leg strategies)
  const [dynamicLegs, setDynamicLegs] = useState([]);

  // Strategy mode: 'legacy' or 'dynamic'
  const [strategyMode, setStrategyMode] = useState('legacy');

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
    // Ensure numeric fields are stored as numbers
    if (field === 'spot_adjustment') {
      value = typeof value === 'number' ? value : (parseFloat(value) || 1.0);
    } else if (field === 'spot_adjustment_type') {
      value = typeof value === 'number' ? value : parseInt(value);
    }
    
    setUiState(prev => ({ ...prev, [field]: value }));
  };

  // Handle legacy leg selection
  const handleLegacyLegChange = (leg, value) => {
    setLegacyLegs(prev => ({ ...prev, [leg]: value }));
  };

  // Handle strategy mode change
  const handleStrategyModeChange = (mode) => {
    setStrategyMode(mode);
  };

  // Handle strategy parameter changes
  const handleParamChange = (param, value) => {
    // Ensure numeric parameters are stored as numbers
    const numericParams = [
      'call_sell_position', 'put_sell_position', 'put_strike_pct_below',
      'max_put_spot_pct', 'premium_multiplier', 'protection_pct',
      'call_hsl_pct', 'pct_diff'
    ];
    
    if (numericParams.includes(param)) {
      value = typeof value === 'number' ? value : (parseFloat(value) || 0);
    }
    
    setStrategyParams(prev => ({ ...prev, [param]: value }));
  };

  // Infer engine from legacy leg combination
  const inferLegacyEngine = () => {
    const { ce_sell, pe_sell, pe_buy, fut_buy, premium_mode, breach_mode, hsl_mode } = legacyLegs;

    if (hsl_mode && ce_sell && fut_buy) return "v8_hsl";
    if (breach_mode && ce_sell) return "v3_strike_breach";
    if (premium_mode && (ce_sell || pe_sell)) return "v7_premium";
    if (ce_sell && pe_buy && fut_buy) return "v8_ce_pe_fut";
    if (ce_sell && pe_sell && !fut_buy) return "v4_strangle";
    if (ce_sell && fut_buy && !pe_sell) return "v1_ce_fut";
    if (pe_sell && fut_buy && !ce_sell) return "v2_pe_fut";
    if (ce_sell && !fut_buy && !pe_sell && legacyLegs.protection) return "v5_call";
    if (pe_sell && !fut_buy && !ce_sell && legacyLegs.protection) return "v5_put";
    if (ce_sell && pe_sell && pe_buy && fut_buy) return "v9_counter";

    return null;
  };

  // Check if dynamic strategy is valid
  const isDynamicStrategyValid = () => {
    if (dynamicLegs.length === 0) return false;
    
    // Validate that at least one leg exists
    if (dynamicLegs.length === 0) return false;
    
    // Check for contradictory legs (e.g., same strike with opposite positions)
    const legTypes = dynamicLegs.map(leg => `${leg.instrument}_${leg.optionType || 'null'}_${leg.position}`);
    
    // Basic validation: ensure each leg has required properties
    for (let leg of dynamicLegs) {
      if (!leg.instrument) return false;
      if (leg.instrument === 'OPTION' && !leg.optionType) return false;
      if (!leg.position) return false;
      if (leg.quantity <= 0) return false;
      
      // Validate strike selection
      if (leg.instrument === 'OPTION') {
        if (!leg.strikeSelection || typeof leg.strikeSelection !== 'object') return false;
        if (typeof leg.strikeSelection.type !== 'string') return false;
        if (typeof leg.strikeSelection.value !== 'number') return false;
        if (typeof leg.strikeSelection.spotAdjustmentMode !== 'number') return false;
        if (typeof leg.strikeSelection.spotAdjustment !== 'number') return false;
      }
    }
    
    return true;
  };

  // Validate buy/sell logic coherence
  const validateBuySellLogic = () => {
    if (strategyMode !== 'dynamic') return true;
    
    // In dynamic mode, check for basic logic issues
    const hasBuyOption = dynamicLegs.some(leg => leg.instrument === 'OPTION' && leg.position === 'BUY');
    const hasSellOption = dynamicLegs.some(leg => leg.instrument === 'OPTION' && leg.position === 'SELL');
    const hasBuyFuture = dynamicLegs.some(leg => leg.instrument === 'FUTURE' && leg.position === 'BUY');
    const hasSellFuture = dynamicLegs.some(leg => leg.instrument === 'FUTURE' && leg.position === 'SELL');
    
    // Allow various combinations, but warn about unusual ones
    return true;
  };

  // Validate strike selection parameters
  const validateStrikeSelection = () => {
    if (strategyMode !== 'dynamic') return true;
    
    for (let leg of dynamicLegs) {
      if (leg.instrument === 'OPTION') {
        const { type, value } = leg.strikeSelection;
        
        // Validate based on strike selection type
        if (type === 'ATM') {
          // ATM should typically have value close to 0
        } else if (type === 'ITM') {
          // ITM values should be reasonable
          if (Math.abs(value) > 20) { // More than 20% ITM might be unusual
            console.warn(`Warning: ${leg.optionType} leg has unusually deep ITM value: ${value}%`);
          }
        } else if (type === 'OTM') {
          // OTM values should be reasonable
          if (Math.abs(value) > 20) { // More than 20% OTM might be unusual
            console.warn(`Warning: ${leg.optionType} leg has unusually far OTM value: ${value}%`);
          }
        }
      }
    }
    
    return true;
  };

  // Build payload for backend
  const buildPayload = () => {
    if (strategyMode === 'legacy') {
      const engine = inferLegacyEngine();
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
          base.protection = legacyLegs.protection;
          base.protection_pct = strategyParams.protection_pct;
          base.call_sell = true;
          base.put_sell = false;
          base.put_buy = true;
          base.future_buy = false;
          break;
        case "v5_put":
          base.put_sell_position = strategyParams.put_sell_position;
          base.protection = legacyLegs.protection;
          base.protection_pct = strategyParams.protection_pct;
          base.call_sell = false;
          base.put_sell = true;
          base.put_buy = true;
          base.future_buy = false;
          break;
        case "v7_premium":
          base.call_sell = legacyLegs.ce_sell;
          base.put_sell = legacyLegs.pe_sell;
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
    } else if (strategyMode === 'dynamic') {
      // For dynamic strategies, return a different payload structure
      if (!isDynamicStrategyValid()) return null;

      // Map spot adjustment type from number to string
      const spotAdjustmentMap = {
        0: "None",
        1: "Rises", 
        2: "Falls",
        3: "RisesOrFalls"
      };

      // Transform dynamic legs to backend format
      const transformedLegs = dynamicLegs.map(leg => ({
        instrument: leg.instrument,
        option_type: leg.instrument === 'OPTION' ? leg.optionType : null,
        position: leg.position,
        strike_selection: leg.strikeSelection,
        quantity: leg.quantity,
        expiry_type: leg.expiryType
      }));

      const payload = {
        name: `Dynamic Strategy (${dynamicLegs.length} legs)`,
        legs: transformedLegs,
        parameters: {},
        index: uiState.index,
        date_from: uiState.from_date,
        date_to: uiState.to_date,
        expiry_window: uiState.expiry_window,
        spot_adjustment_type: spotAdjustmentMap[uiState.spot_adjustment_type] || "None",
        spot_adjustment: uiState.spot_adjustment,
      };

      return payload;
    }

    return null;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    
    // Run validations
    if (strategyMode === 'dynamic') {
      if (!isDynamicStrategyValid()) {
        setError("Invalid strategy configuration. Please check your leg settings.");
        return;
      }
      
      if (!validateBuySellLogic()) {
        setError("Buy/Sell logic appears inconsistent. Please review your positions.");
        return;
      }
      
      if (!validateStrikeSelection()) {
        setError("Strike selection parameters appear invalid. Please review your settings.");
        return;
      }
    }
    
    const payload = buildPayload();
    if (!payload) {
      if (strategyMode === 'legacy') {
        setError("Unsupported strategy combination. Please review leg selection.");
      } else {
        setError("Invalid strategy configuration. Please check your settings.");
      }
      return;
    }

    // Type validation and sanitization
    if (typeof payload.spot_adjustment !== 'number') {
      payload.spot_adjustment = parseFloat(payload.spot_adjustment) || 1.0;
    }
    
    // Ensure all numeric fields are actually numbers
    const numericFields = [
      'call_sell_position', 'put_sell_position', 'put_strike_pct_below',
      'max_put_spot_pct', 'premium_multiplier', 'protection_pct',
      'call_hsl_pct', 'pct_diff'
    ];
    
    numericFields.forEach(field => {
      if (payload[field] !== undefined && typeof payload[field] !== 'number') {
        payload[field] = parseFloat(payload[field]) || 0;
      }
    });

    // Log payload for debugging
    console.log('Sending backtest payload:', JSON.stringify(payload, null, 2));

    setLoading(true);
    setError(null);
    
    try {
      // Determine the API endpoint based on strategy mode
      const endpoint = strategyMode === 'dynamic' ? '/api/dynamic-backtest' : '/api/backtest';
      
      const response = await fetch(endpoint, {
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
        console.error('Backend error response:', errorData);
        
        // Extract detailed validation errors if available
        if (errorData.detail && Array.isArray(errorData.detail)) {
          const fieldErrors = errorData.detail.map(err => 
            `${err.loc.join('.')}: ${err.msg}`
          ).join('; ');
          setError(`Validation error: ${fieldErrors}`);
        } else {
          setError(errorData.detail || "Backtest failed");
        }
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

  const engine = strategyMode === 'legacy' ? inferLegacyEngine() : (isDynamicStrategyValid() ? 'dynamic' : null);

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
    
    setLegacyLegs(newLegs);
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
                {/* Strategy Mode Toggle */}
                <div className="bg-white rounded-lg shadow p-6">
                  <h3 className="text-lg font-semibold mb-4">Strategy Mode</h3>
                  <div className="flex gap-4">
                    <button
                      type="button"
                      onClick={() => handleStrategyModeChange('legacy')}
                      className={`py-2 px-4 rounded-md border ${
                        strategyMode === 'legacy'
                          ? 'bg-blue-600 text-white border-blue-600'
                          : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                      }`}
                    >
                      Legacy Mode
                    </button>
                    <button
                      type="button"
                      onClick={() => handleStrategyModeChange('dynamic')}
                      className={`py-2 px-4 rounded-md border ${
                        strategyMode === 'dynamic'
                          ? 'bg-blue-600 text-white border-blue-600'
                          : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                      }`}
                    >
                      Dynamic Mode
                    </button>
                  </div>
                  <p className="text-sm text-gray-600 mt-2">
                    {strategyMode === 'legacy' 
                      ? 'Use predefined strategy combinations (v1-v9)' 
                      : 'Build custom multi-leg strategies'}
                  </p>
                </div>

                {strategyMode === 'legacy' ? (
                  /* Legacy Strategy Builder */
                  <div className="bg-white rounded-lg shadow p-6">
                    <h3 className="text-lg font-semibold mb-4">Legacy Strategy Builder</h3>
                    
                    <div className="space-y-4">
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2">Select Legs</label>
                        <div className="grid grid-cols-2 gap-2">
                          <button
                            type="button"
                            onClick={() => handleLegacyLegChange('ce_sell', !legacyLegs.ce_sell)}
                            className={`py-2 px-3 text-sm font-medium rounded-md border ${
                              legacyLegs.ce_sell 
                                ? 'bg-blue-600 text-white border-blue-600' 
                                : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                            }`}
                          >
                            CE Sell
                          </button>
                          <button
                            type="button"
                            onClick={() => handleLegacyLegChange('pe_sell', !legacyLegs.pe_sell)}
                            className={`py-2 px-3 text-sm font-medium rounded-md border ${
                              legacyLegs.pe_sell 
                                ? 'bg-blue-600 text-white border-blue-600' 
                                : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                            }`}
                          >
                            PE Sell
                          </button>
                          <button
                            type="button"
                            onClick={() => handleLegacyLegChange('pe_buy', !legacyLegs.pe_buy)}
                            className={`py-2 px-3 text-sm font-medium rounded-md border ${
                              legacyLegs.pe_buy 
                                ? 'bg-green-600 text-white border-green-600' 
                                : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                            }`}
                          >
                            PE Buy
                          </button>
                          <button
                            type="button"
                            onClick={() => handleLegacyLegChange('fut_buy', !legacyLegs.fut_buy)}
                            className={`py-2 px-3 text-sm font-medium rounded-md border ${
                              legacyLegs.fut_buy 
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
                            onClick={() => handleLegacyLegChange('premium_mode', !legacyLegs.premium_mode)}
                            className={`py-2 px-2 text-xs font-medium rounded-md border ${
                              legacyLegs.premium_mode 
                                ? 'bg-yellow-600 text-white border-yellow-600' 
                                : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                            }`}
                          >
                            Premium Mode
                          </button>
                          <button
                            type="button"
                            onClick={() => handleLegacyLegChange('breach_mode', !legacyLegs.breach_mode)}
                            className={`py-2 px-2 text-xs font-medium rounded-md border ${
                              legacyLegs.breach_mode 
                                ? 'bg-red-600 text-white border-red-600' 
                                : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                            }`}
                          >
                            Breach Mode
                          </button>
                          <button
                            type="button"
                            onClick={() => handleLegacyLegChange('hsl_mode', !legacyLegs.hsl_mode)}
                            className={`py-2 px-2 text-xs font-medium rounded-md border ${
                              legacyLegs.hsl_mode 
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
                          onClick={() => handleLegacyLegChange('protection', !legacyLegs.protection)}
                          className={`w-full py-2 px-3 text-sm font-medium rounded-md border ${
                            legacyLegs.protection 
                              ? 'bg-indigo-600 text-white border-indigo-600' 
                              : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                          }`}
                        >
                          {legacyLegs.protection ? "Protection Enabled" : "Add Protection"}
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
                ) : (
                  /* Dynamic Strategy Builder */
                  <div className="bg-white rounded-lg shadow p-6">
                    <h3 className="text-lg font-semibold mb-4">Dynamic Strategy Builder</h3>
                    <LegBuilder 
                      legs={dynamicLegs} 
                      onLegsChange={setDynamicLegs} 
                    />
                  </div>
                )}

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
                    {(legacyLegs.ce_sell || legacyLegs.pe_sell) && (
                      <div className="grid grid-cols-2 gap-4">
                        {legacyLegs.ce_sell && (
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
                        {legacyLegs.pe_sell && (
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
                    {legacyLegs.protection && (
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
                    {legacyLegs.hsl_mode && (
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