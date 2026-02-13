# Fix for 422 Validation Error

## Problem
The frontend was receiving 422 (Unprocessable Entity) errors when submitting backtest requests to the backend API.

## Root Cause
The 422 error occurs when the request payload doesn't match the backend's Pydantic model validation requirements. Common causes:

1. **Type Mismatches**: Numeric values being sent as strings instead of numbers
2. **Missing Required Fields**: Required fields not included in the payload
3. **Invalid Field Values**: Values that don't match the expected format or constraints

## Solution Applied

### 1. Type Safety in State Management (`ConfigPanel.jsx`)

Added type validation in `handleParamChange`:
```javascript
const handleParamChange = (param, value) => {
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
```

Added type validation in `handleUiChange`:
```javascript
const handleUiChange = (field, value) => {
  if (field === 'spot_adjustment') {
    value = typeof value === 'number' ? value : (parseFloat(value) || 1.0);
  } else if (field === 'spot_adjustment_type') {
    value = typeof value === 'number' ? value : parseInt(value);
  }
  
  setUiState(prev => ({ ...prev, [field]: value }));
};
```

### 2. Payload Sanitization Before Sending

Enhanced `handleSubmit` to validate and sanitize the payload:
```javascript
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
```

### 3. Enhanced Error Reporting

Improved error handling to show detailed validation errors:
```javascript
if (errorData.detail && Array.isArray(errorData.detail)) {
  const fieldErrors = errorData.detail.map(err => 
    `${err.loc.join('.')}: ${err.msg}`
  ).join('; ');
  setError(`Validation error: ${fieldErrors}`);
} else {
  setError(errorData.detail || "Backtest failed");
}
```

### 4. Debug Logging

Added console logging to help diagnose issues:
```javascript
console.log('Sending backtest payload:', JSON.stringify(payload, null, 2));
console.error('Backend error response:', errorData);
```

## Backend Model Reference

The backend expects this structure (`BacktestRequest` in `backend/routers/backtest.py`):

```python
class BacktestRequest(BaseModel):
    strategy: str  # Required
    index: str = "NIFTY"
    date_from: str  # Required
    date_to: str  # Required
    expiry_window: str = "weekly_expiry"
    
    # Numeric parameters (must be float/int, not strings)
    call_sell_position: float = 0.0
    put_sell_position: float = 0.0
    put_strike_pct_below: float = 1.0
    max_put_spot_pct: float = 0.04
    premium_multiplier: float = 1.0
    
    # Boolean parameters
    call_premium: bool = True
    put_premium: bool = True
    call_sell: bool = True
    put_sell: bool = True
    call_buy: bool = False
    put_buy: bool = False
    future_buy: bool = True
    protection: bool = False
    
    # String parameters
    spot_adjustment_type: str = "None"  # "None", "Rises", "Falls", "RisesOrFalls"
    
    # More numeric parameters
    spot_adjustment: float = 1.0
    call_hsl_pct: int = 100
    put_hsl_pct: int = 100
    pct_diff: float = 0.3
    protection_pct: float = 1.0
```

## Testing

### Test Files Created

1. **test_422_error.py**: Python script to test API directly
2. **test_frontend_payload.html**: HTML page to test from browser
3. **diagnose_payload_mismatch.md**: Detailed diagnosis document

### How to Test

#### Option 1: Python Script
```bash
python test_422_error.py
```

#### Option 2: HTML Test Page
1. Make sure backend is running on http://localhost:8000
2. Open `test_frontend_payload.html` in a browser
3. Click the test buttons to verify each strategy

#### Option 3: Browser Console
1. Open your React app
2. Open browser DevTools (F12)
3. Go to Console tab
4. Submit a backtest
5. Check the logged payload and any errors

## Verification

After applying these fixes, you should see:

✅ **Success Case:**
```
Sending backtest payload: {
  "strategy": "v1_ce_fut",
  "index": "NIFTY",
  "date_from": "2019-01-01",
  "date_to": "2026-01-01",
  ...
}
```

❌ **If Still Getting 422:**
Check the console for detailed error like:
```
Validation error: spot_adjustment: value is not a valid float; 
call_sell_position: value is not a valid float
```

## Common Issues and Solutions

### Issue 1: "value is not a valid float"
**Cause**: String value sent instead of number
**Solution**: Ensure `parseFloat()` is called on input values

### Issue 2: "field required"
**Cause**: Missing required field (strategy, date_from, date_to)
**Solution**: Check that `buildPayload()` includes all required fields

### Issue 3: "extra fields not permitted"
**Cause**: Backend model has `extra = "forbid"` config
**Solution**: Remove extra fields from payload or update backend model

## Next Steps

1. Test the fixes in your React app
2. Check browser console for the logged payload
3. If still getting 422, check the detailed error message
4. Use the HTML test page to isolate frontend vs backend issues
5. Compare working payload from test script with frontend payload

## Files Modified

- `frontend/src/components/ConfigPanel.jsx`: Added type safety and validation
- `test_422_error.py`: Created for testing
- `test_frontend_payload.html`: Created for browser testing
- `diagnose_payload_mismatch.md`: Created for diagnosis
- `FIX_422_ERROR_SUMMARY.md`: This file
