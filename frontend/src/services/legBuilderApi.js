const API_BASE_URL = '/api';

export const api = {
  async runBacktest(payload) {
    const response = await fetch(`${API_BASE_URL}/dynamic-backtest`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });
    
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || 'Backtest failed');
    }
    
    return response.json();
  },

  async runLegacyBacktest(payload) {
    const response = await fetch(`${API_BASE_URL}/algotest-backtest`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });
    
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || 'Backtest failed');
    }
    
    return response.json();
  },

  async getExpiryDates(index, year, month) {
    const response = await fetch(
      `${API_BASE_URL}/expiry/dates?index=${index}&year=${year}&month=${month}`
    );
    
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || 'Failed to get expiry dates');
    }
    
    return response.json();
  },

  async getAvailableDates(index) {
    const response = await fetch(`${API_BASE_URL}/data/dates?index=${index}`);
    
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || 'Failed to get available dates');
    }
    
    return response.json();
  }
};

export const createLegPayload = (config) => {
  const {
    index = 'NIFTY',
    underlying = 'cash',
    strategyType = 'positional',
    expiryBasis = 'weekly',
    entryDaysBefore = 0,
    exitDaysBefore = 0,
    squareOffMode = 'partial',
    trailSLBreakeven = false,
    trailSLTarget = 'all_legs',
    legs = [],
    overallSettings = {},
    dateFrom,
    dateTo,
  } = config;

  return {
    index,
    underlying,
    strategy_type: strategyType,
    expiry_window: expiryBasis === 'weekly' ? 'weekly_expiry' : 'monthly_expiry',
    entry_dte: entryDaysBefore,
    exit_dte: exitDaysBefore,
    square_off_mode: squareOffMode,
    trail_sl_breakeven: trailSLBreakeven,
    trail_sl_target: trailSLTarget,
    legs: legs.map(leg => ({
      ...leg,
      lot: leg.lot || leg.lots || 1,
      strike_selection: {
        type: leg.strike_criteria || leg.strikeSelection?.type || 'strike_type',
        strike_type: leg.strike_type || leg.strikeSelection?.strike_type || 'atm',
        premium: leg.premium_value || leg.strikeSelection?.premium || 0,
        lower: leg.premium_min || leg.strikeSelection?.premium_min || 0,
        upper: leg.premium_max || leg.strikeSelection?.premium_max || 0,
      },
    })),
    overall_settings: {
      stop_loss: overallSettings.stop_loss_enabled ? overallSettings.stop_loss : null,
      stop_loss_type: overallSettings.stop_loss_type || 'max_loss',
      target: overallSettings.target_enabled ? overallSettings.target : null,
      target_type: overallSettings.target_type || 'max_profit',
    },
    date_from: dateFrom,
    date_to: dateTo,
    expiry_type: expiryBasis.toUpperCase(),
  };
};

export const createDefaultLeg = () => ({
  id: Date.now(),
  segment: 'options',
  lot: 1,
  position: 'sell',
  option_type: 'call',
  expiry: 'weekly',
  strike_criteria: 'strike_type',
  strike_type: 'atm',
  premium_value: 0,
  premium_min: 0,
  premium_max: 0,
  stop_loss_enabled: false,
  stop_loss: null,
  stop_loss_type: 'pct',
  target_enabled: false,
  target: null,
  target_type: 'pct',
});

export const validateLegs = (legs, expiryBasis) => {
  const errors = [];
  
  if (legs.length === 0) {
    errors.push('Please add at least one leg');
  }
  
  if (expiryBasis === 'monthly') {
    const weeklyLegs = legs.filter(l => l.expiry === 'weekly');
    if (weeklyLegs.length > 0) {
      const legNumbers = legs
        .map((l, i) => l.expiry === 'weekly' ? i + 1 : null)
        .filter(n => n !== null);
      errors.push(`Cannot use weekly expiry legs with monthly expiry basis. Weekly legs: ${legNumbers.join(', ')}`);
    }
  }
  
  return errors;
};

export default api;
