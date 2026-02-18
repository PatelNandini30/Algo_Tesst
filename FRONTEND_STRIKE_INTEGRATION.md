# Frontend Strike Selection Integration Guide

## Overview
This guide shows exactly how to integrate the complete strike selection system into the React frontend.

---

## 1. UI COMPONENT STRUCTURE

### Strike Selection Component

```jsx
import React, { useState } from 'react';

const StrikeSelectionInput = ({ leg, onUpdate }) => {
  const [strikeType, setStrikeType] = useState('ATM');
  const [strikeValue, setStrikeValue] = useState('');
  const [expiryType, setExpiryType] = useState('WEEKLY');
  const [minPremium, setMinPremium] = useState('');
  const [maxPremium, setMaxPremium] = useState('');

  // Update parent component when values change
  const handleUpdate = () => {
    const config = {
      strike_selection_type: strikeType,
      expiry_selection: expiryType
    };

    // Add conditional fields based on strike type
    if (strikeType === 'ITM' || strikeType === 'OTM') {
      config.strike_selection_value = parseInt(strikeValue);
    } else if (strikeType === 'PREMIUM_RANGE') {
      config.min_premium = parseFloat(minPremium);
      config.max_premium = parseFloat(maxPremium);
    } else if (strikeType === 'CLOSEST_PREMIUM') {
      config.strike_selection_value = parseFloat(strikeValue);
    }

    onUpdate(config);
  };

  return (
    <div className="strike-selection-container">
      {/* Expiry Selection */}
      <div className="form-group">
        <label>Expiry Type</label>
        <select 
          value={expiryType} 
          onChange={(e) => {
            setExpiryType(e.target.value);
            handleUpdate();
          }}
          className="form-control"
        >
          <option value="WEEKLY">Weekly</option>
          <option value="NEXT_WEEKLY">Next Weekly</option>
          <option value="MONTHLY">Monthly</option>
          <option value="NEXT_MONTHLY">Next Monthly</option>
        </select>
      </div>

      {/* Strike Type Selection */}
      <div className="form-group">
        <label>Strike Selection</label>
        <select 
          value={strikeType} 
          onChange={(e) => {
            setStrikeType(e.target.value);
            setStrikeValue(''); // Reset value when type changes
            handleUpdate();
          }}
          className="form-control"
        >
          <option value="ATM">ATM (At The Money)</option>
          <option value="ITM">ITM (In The Money)</option>
          <option value="OTM">OTM (Out of The Money)</option>
          <option value="PREMIUM_RANGE">Premium Range</option>
          <option value="CLOSEST_PREMIUM">Closest Premium</option>
        </select>
      </div>

      {/* Conditional Inputs Based on Strike Type */}
      {(strikeType === 'ITM' || strikeType === 'OTM') && (
        <div className="form-group">
          <label>Offset (1-30 strikes)</label>
          <input
            type="number"
            min="1"
            max="30"
            value={strikeValue}
            onChange={(e) => {
              setStrikeValue(e.target.value);
              handleUpdate();
            }}
            placeholder="e.g., 2 for OTM2"
            className="form-control"
          />
          <small className="form-text text-muted">
            {strikeType === 'ITM' 
              ? 'Higher value = deeper in-the-money (more expensive)'
              : 'Higher value = further out-of-the-money (cheaper)'}
          </small>
        </div>
      )}

      {strikeType === 'PREMIUM_RANGE' && (
        <>
          <div className="form-group">
            <label>Minimum Premium (₹)</label>
            <input
              type="number"
              min="0"
              step="0.5"
              value={minPremium}
              onChange={(e) => {
                setMinPremium(e.target.value);
                handleUpdate();
              }}
              placeholder="e.g., 100"
              className="form-control"
            />
          </div>
          <div className="form-group">
            <label>Maximum Premium (₹)</label>
            <input
              type="number"
              min="0"
              step="0.5"
              value={maxPremium}
              onChange={(e) => {
                setMaxPremium(e.target.value);
                handleUpdate();
              }}
              placeholder="e.g., 200"
              className="form-control"
            />
          </div>
          <small className="form-text text-muted">
            System will find strike with premium in this range, closest to ATM
          </small>
        </>
      )}

      {strikeType === 'CLOSEST_PREMIUM' && (
        <div className="form-group">
          <label>Target Premium (₹)</label>
          <input
            type="number"
            min="0"
            step="0.5"
            value={strikeValue}
            onChange={(e) => {
              setStrikeValue(e.target.value);
              handleUpdate();
            }}
            placeholder="e.g., 150"
            className="form-control"
          />
          <small className="form-text text-muted">
            System will find strike with premium closest to this value
          </small>
        </div>
      )}
    </div>
  );
};

export default StrikeSelectionInput;
```

---

## 2. INTEGRATION INTO MAIN BACKTEST COMPONENT

### Update AlgoTestBacktest.jsx

```jsx
import React, { useState } from 'react';
import StrikeSelectionInput from './StrikeSelectionInput';

const AlgoTestBacktest = () => {
  const [legs, setLegs] = useState([
    {
      id: 1,
      instrument_type: 'OPTION',
      option_type: 'CE',
      position: 'SELL',
      strike_selection_type: 'ATM',
      expiry_selection: 'WEEKLY',
      lots: 1
    }
  ]);

  const updateLegStrikeConfig = (legId, strikeConfig) => {
    setLegs(legs.map(leg => 
      leg.id === legId 
        ? { ...leg, ...strikeConfig }
        : leg
    ));
  };

  const handleBacktest = async () => {
    const payload = {
      index: selectedIndex,
      from_date: fromDate,
      to_date: toDate,
      entry_time: entryTime,
      exit_time: exitTime,
      entry_dte: entryDTE,
      exit_dte: exitDTE,
      legs: legs.map(leg => ({
        instrument_type: leg.instrument_type,
        option_type: leg.option_type,
        position: leg.position,
        strike_selection_type: leg.strike_selection_type,
        strike_selection_value: leg.strike_selection_value,
        expiry_selection: leg.expiry_selection,
        min_premium: leg.min_premium,
        max_premium: leg.max_premium,
        lots: leg.lots
      }))
    };

    try {
      const response = await fetch('http://localhost:8000/api/backtest/algotest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      
      const result = await response.json();
      setResults(result);
    } catch (error) {
      console.error('Backtest failed:', error);
    }
  };

  return (
    <div className="backtest-container">
      {/* Basic Settings */}
      <div className="settings-section">
        {/* Index, dates, times, DTE inputs */}
      </div>

      {/* Legs Configuration */}
      <div className="legs-section">
        <h3>Strategy Legs</h3>
        {legs.map(leg => (
          <div key={leg.id} className="leg-card">
            <div className="leg-header">
              <h4>Leg {leg.id}</h4>
              <button onClick={() => removeLeg(leg.id)}>Remove</button>
            </div>

            {/* Instrument Type */}
            <select 
              value={leg.instrument_type}
              onChange={(e) => updateLeg(leg.id, 'instrument_type', e.target.value)}
            >
              <option value="OPTION">Option</option>
              <option value="FUTURE">Future</option>
            </select>

            {leg.instrument_type === 'OPTION' && (
              <>
                {/* Option Type */}
                <select 
                  value={leg.option_type}
                  onChange={(e) => updateLeg(leg.id, 'option_type', e.target.value)}
                >
                  <option value="CE">Call (CE)</option>
                  <option value="PE">Put (PE)</option>
                </select>

                {/* Strike Selection Component */}
                <StrikeSelectionInput
                  leg={leg}
                  onUpdate={(config) => updateLegStrikeConfig(leg.id, config)}
                />
              </>
            )}

            {/* Position */}
            <select 
              value={leg.position}
              onChange={(e) => updateLeg(leg.id, 'position', e.target.value)}
            >
              <option value="BUY">Buy</option>
              <option value="SELL">Sell</option>
            </select>

            {/* Lots */}
            <input
              type="number"
              min="1"
              value={leg.lots}
              onChange={(e) => updateLeg(leg.id, 'lots', parseInt(e.target.value))}
              placeholder="Lots"
            />
          </div>
        ))}

        <button onClick={addLeg}>Add Leg</button>
      </div>

      {/* Run Backtest */}
      <button onClick={handleBacktest} className="btn-primary">
        Run Backtest
      </button>

      {/* Results Display */}
      {results && <ResultsPanel results={results} />}
    </div>
  );
};

export default AlgoTestBacktest;
```

---

## 3. VALIDATION LOGIC

### Client-Side Validation

```javascript
const validateLegConfig = (leg) => {
  const errors = [];

  // Validate strike selection
  if (leg.strike_selection_type === 'ITM' || leg.strike_selection_type === 'OTM') {
    if (!leg.strike_selection_value || leg.strike_selection_value < 1 || leg.strike_selection_value > 30) {
      errors.push('Offset must be between 1 and 30');
    }
  }

  if (leg.strike_selection_type === 'PREMIUM_RANGE') {
    if (!leg.min_premium || !leg.max_premium) {
      errors.push('Both min and max premium are required');
    }
    if (leg.min_premium >= leg.max_premium) {
      errors.push('Min premium must be less than max premium');
    }
    if (leg.min_premium < 0 || leg.max_premium < 0) {
      errors.push('Premium values must be positive');
    }
  }

  if (leg.strike_selection_type === 'CLOSEST_PREMIUM') {
    if (!leg.strike_selection_value || leg.strike_selection_value <= 0) {
      errors.push('Target premium must be positive');
    }
  }

  // Validate expiry selection
  const validExpiries = ['WEEKLY', 'NEXT_WEEKLY', 'MONTHLY', 'NEXT_MONTHLY'];
  if (!validExpiries.includes(leg.expiry_selection)) {
    errors.push('Invalid expiry selection');
  }

  return errors;
};

const validateAllLegs = (legs) => {
  const allErrors = {};
  legs.forEach(leg => {
    const errors = validateLegConfig(leg);
    if (errors.length > 0) {
      allErrors[leg.id] = errors;
    }
  });
  return allErrors;
};
```

---

## 4. EXAMPLE PAYLOADS

### Example 1: Simple OTM2 Weekly Call Sell
```json
{
  "index": "NIFTY",
  "from_date": "2024-01-01",
  "to_date": "2024-12-31",
  "entry_time": "09:20",
  "exit_time": "15:15",
  "entry_dte": 5,
  "exit_dte": 0,
  "legs": [
    {
      "instrument_type": "OPTION",
      "option_type": "CE",
      "position": "SELL",
      "strike_selection_type": "OTM",
      "strike_selection_value": 2,
      "expiry_selection": "WEEKLY",
      "lots": 1
    }
  ]
}
```

### Example 2: Iron Condor with Premium Range
```json
{
  "index": "NIFTY",
  "from_date": "2024-01-01",
  "to_date": "2024-12-31",
  "entry_time": "09:20",
  "exit_time": "15:15",
  "entry_dte": 5,
  "exit_dte": 0,
  "legs": [
    {
      "instrument_type": "OPTION",
      "option_type": "CE",
      "position": "SELL",
      "strike_selection_type": "PREMIUM_RANGE",
      "min_premium": 100,
      "max_premium": 150,
      "expiry_selection": "WEEKLY",
      "lots": 1
    },
    {
      "instrument_type": "OPTION",
      "option_type": "CE",
      "position": "BUY",
      "strike_selection_type": "OTM",
      "strike_selection_value": 5,
      "expiry_selection": "WEEKLY",
      "lots": 1
    },
    {
      "instrument_type": "OPTION",
      "option_type": "PE",
      "position": "SELL",
      "strike_selection_type": "PREMIUM_RANGE",
      "min_premium": 100,
      "max_premium": 150,
      "expiry_selection": "WEEKLY",
      "lots": 1
    },
    {
      "instrument_type": "OPTION",
      "option_type": "PE",
      "position": "BUY",
      "strike_selection_type": "OTM",
      "strike_selection_value": 5,
      "expiry_selection": "WEEKLY",
      "lots": 1
    }
  ]
}
```

### Example 3: Straddle with Closest Premium
```json
{
  "index": "BANKNIFTY",
  "from_date": "2024-01-01",
  "to_date": "2024-12-31",
  "entry_time": "09:20",
  "exit_time": "15:15",
  "entry_dte": 3,
  "exit_dte": 0,
  "legs": [
    {
      "instrument_type": "OPTION",
      "option_type": "CE",
      "position": "BUY",
      "strike_selection_type": "CLOSEST_PREMIUM",
      "strike_selection_value": 200,
      "expiry_selection": "MONTHLY",
      "lots": 1
    },
    {
      "instrument_type": "OPTION",
      "option_type": "PE",
      "position": "BUY",
      "strike_selection_type": "CLOSEST_PREMIUM",
      "strike_selection_value": 200,
      "expiry_selection": "MONTHLY",
      "lots": 1
    }
  ]
}
```

---

## 5. CSS STYLING

```css
.strike-selection-container {
  padding: 15px;
  background: #f8f9fa;
  border-radius: 8px;
  margin: 10px 0;
}

.form-group {
  margin-bottom: 15px;
}

.form-group label {
  display: block;
  font-weight: 600;
  margin-bottom: 5px;
  color: #333;
}

.form-control {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 14px;
}

.form-control:focus {
  outline: none;
  border-color: #007bff;
  box-shadow: 0 0 0 3px rgba(0, 123, 255, 0.1);
}

.form-text {
  display: block;
  margin-top: 5px;
  font-size: 12px;
}

.text-muted {
  color: #6c757d;
}

.leg-card {
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 15px;
  background: white;
}

.leg-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
  padding-bottom: 10px;
  border-bottom: 2px solid #007bff;
}

.btn-primary {
  background: #007bff;
  color: white;
  padding: 12px 24px;
  border: none;
  border-radius: 4px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
}

.btn-primary:hover {
  background: #0056b3;
}

.btn-primary:disabled {
  background: #ccc;
  cursor: not-allowed;
}
```

---

## 6. ERROR HANDLING & USER FEEDBACK

```jsx
const StrikeSelectionInput = ({ leg, onUpdate }) => {
  const [errors, setErrors] = useState([]);

  const validate = () => {
    const newErrors = [];

    if (strikeType === 'ITM' || strikeType === 'OTM') {
      if (!strikeValue || strikeValue < 1 || strikeValue > 30) {
        newErrors.push('Offset must be between 1 and 30');
      }
    }

    if (strikeType === 'PREMIUM_RANGE') {
      if (!minPremium || !maxPremium) {
        newErrors.push('Both min and max premium required');
      } else if (parseFloat(minPremium) >= parseFloat(maxPremium)) {
        newErrors.push('Min must be less than max');
      }
    }

    if (strikeType === 'CLOSEST_PREMIUM') {
      if (!strikeValue || strikeValue <= 0) {
        newErrors.push('Target premium must be positive');
      }
    }

    setErrors(newErrors);
    return newErrors.length === 0;
  };

  const handleUpdate = () => {
    if (validate()) {
      // ... update logic
    }
  };

  return (
    <div className="strike-selection-container">
      {/* ... inputs ... */}

      {/* Error Display */}
      {errors.length > 0 && (
        <div className="alert alert-danger">
          <ul>
            {errors.map((error, idx) => (
              <li key={idx}>{error}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};
```

---

## 7. TESTING CHECKLIST

### Frontend Tests
- [ ] ATM selection sends correct payload
- [ ] ITM with offset 1-30 sends correct values
- [ ] OTM with offset 1-30 sends correct values
- [ ] Premium range validation works
- [ ] Closest premium validation works
- [ ] Expiry dropdown changes update state
- [ ] Multiple legs can have different selections
- [ ] Add/remove legs works correctly
- [ ] Validation errors display properly
- [ ] Form resets when strike type changes

### Integration Tests
- [ ] Backend receives correct payload format
- [ ] Backend returns valid results
- [ ] Error responses handled gracefully
- [ ] Loading states work correctly
- [ ] Results display properly

---

## Summary

This integration provides:
1. Clean, reusable React components
2. Proper validation and error handling
3. Flexible configuration for all strike types
4. User-friendly interface with helpful hints
5. Complete payload construction for backend

The frontend is now ready to support all strike selection methods with proper trading logic!
