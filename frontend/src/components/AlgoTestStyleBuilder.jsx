import React, { useState } from 'react';
import { Play, Calendar } from 'lucide-react';
import InstrumentSettings from './InstrumentSettings';
import EntryExitSettings from './EntryExitSettings';
import DynamicLegBuilder from './DynamicLegBuilder';
import ResultsPanel from './ResultsPanel';

const AlgoTestStyleBuilder = () => {
  // Instrument Settings
  const [instrumentSettings, setInstrumentSettings] = useState({
    index: 'NIFTY',
    underlying_type: 'Futures'
  });

  // Entry Settings
  const [entrySettings, setEntrySettings] = useState({
    strategy_type: 'Positional',
    expiry_type: 'Weekly',
    entry_time: '09:35',
    entry_days_before: 2
  });

  // Exit Settings
  const [exitSettings, setExitSettings] = useState({
    exit_time: '15:15',
    exit_days_before: 0
  });

  // Legs
  const [legs, setLegs] = useState([]);

  // Backtest Period
  const [backtestPeriod, setBacktestPeriod] = useState({
    start_date: '2020-01-01',
    end_date: '2023-12-31'
  });

  // UI State
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState(null);

  // Validation
  const validateStrategy = () => {
    const errors = [];

    // Check if at least one leg exists
    if (legs.length === 0) {
      errors.push('Please add at least one leg to your strategy');
    }

    // Check max legs
    if (legs.length > 4) {
      errors.push('Maximum 4 legs allowed');
    }

    // Validate entry/exit timing
    if (exitSettings.exit_days_before > entrySettings.entry_days_before) {
      errors.push('Exit days must be less than or equal to entry days');
    }

    // Validate date range
    if (new Date(backtestPeriod.start_date) >= new Date(backtestPeriod.end_date)) {
      errors.push('Start date must be before end date');
    }

    // Validate premium ranges
    legs.forEach((leg, index) => {
      if (leg.instrument === 'OPTION' && leg.strike_criteria.type === 'Premium Range') {
        if (leg.strike_criteria.lower_range >= leg.strike_criteria.upper_range) {
          errors.push(`Leg ${index + 1}: Lower range must be less than upper range`);
        }
      }
    });

    return errors;
  };

  // Build API Payload
  const buildPayload = () => {
    return {
      instrument_settings: instrumentSettings,
      entry_settings: entrySettings,
      exit_settings: exitSettings,
      legs: legs.map(leg => ({
        leg_number: leg.leg_number,
        instrument: leg.instrument,
        option_type: leg.instrument === 'OPTION' ? leg.option_type : null,
        position: leg.position,
        lot: leg.total_lot || 1,  // just send the count; engine handles units
        expiry: leg.expiry,
        strike_criteria: leg.instrument === 'OPTION' ? leg.strike_criteria : null
      })),
      backtest_period: backtestPeriod
    };
  };

  // Run Backtest
  const handleRunBacktest = async () => {
    // Validate
    const validationErrors = validateStrategy();
    if (validationErrors.length > 0) {
      setError(validationErrors.join('. '));
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const payload = buildPayload();
      
      const response = await fetch('/api/dynamic-backtest', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
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

  // If results exist, show results panel
  if (results) {
    return <ResultsPanel results={results} onClose={() => setResults(null)} />;
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex justify-between items-center">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">Backtest</h1>
              <p className="text-sm text-gray-500 mt-1">
                Build and test your options strategy
              </p>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-sm text-gray-600">
                {legs.length} leg{legs.length !== 1 ? 's' : ''} configured
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <div className="space-y-6">
          {/* Instrument Settings */}
          <InstrumentSettings
            settings={instrumentSettings}
            onChange={setInstrumentSettings}
          />

          {/* Entry & Exit Settings */}
          <EntryExitSettings
            entrySettings={entrySettings}
            exitSettings={exitSettings}
            onEntryChange={setEntrySettings}
            onExitChange={setExitSettings}
          />

          {/* Leg Builder */}
          <DynamicLegBuilder
            legs={legs}
            onLegsChange={setLegs}
          />

          {/* Backtest Period */}
          <div className="bg-white rounded-lg shadow p-6">
            <h3 className="text-lg font-semibold mb-4">Backtest Period</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Start Date
                </label>
                <input
                  type="date"
                  value={backtestPeriod.start_date}
                  onChange={(e) => setBacktestPeriod({ ...backtestPeriod, start_date: e.target.value })}
                  className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  End Date
                </label>
                <input
                  type="date"
                  value={backtestPeriod.end_date}
                  onChange={(e) => setBacktestPeriod({ ...backtestPeriod, end_date: e.target.value })}
                  className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
            </div>
          </div>

          {/* Error Display */}
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}

          {/* Run Backtest Button */}
          <div className="flex justify-end">
            <button
              onClick={handleRunBacktest}
              disabled={loading || legs.length === 0}
              className={`flex items-center gap-2 px-8 py-3 rounded-lg font-medium text-lg ${
                loading || legs.length === 0
                  ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                  : 'bg-green-600 text-white hover:bg-green-700 shadow-lg'
              }`}
            >
              {loading ? (
                <>
                  <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-white"></div>
                  Running Backtest...
                </>
              ) : (
                <>
                  <Play size={20} />
                  Start Backtest
                </>
              )}
            </button>
          </div>

          {/* Strategy Summary */}
          {legs.length > 0 && (
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
              <h4 className="font-medium text-blue-900 mb-2">Strategy Summary</h4>
              <div className="text-sm text-blue-800 space-y-1">
                <p>• Index: {instrumentSettings.index} ({instrumentSettings.underlying_type})</p>
                <p>• Entry: {entrySettings.entry_days_before} days before {entrySettings.expiry_type} expiry at {entrySettings.entry_time}</p>
                <p>• Exit: {exitSettings.exit_days_before} days before expiry at {exitSettings.exit_time}</p>
                <p>• Legs: {legs.length}</p>
                {legs.map((leg, i) => (
                  <p key={leg.id} className="ml-4">
                    - Leg {i + 1}: {leg.instrument === 'FUTURE' ? 'Future' : `${leg.option_type} Option`} {leg.position} ({leg.total_lot} lot)
                  </p>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default AlgoTestStyleBuilder;
