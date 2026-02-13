# 422 Error Diagnosis

## Test Results

### V1_CE_FUT Test: ✅ SUCCESS
- Status: 200
- Trades: 291
- Payload worked correctly

### Potential Issues Causing 422 Errors

Based on the code analysis, here are the likely causes:

## 1. Missing Required Fields

The `BacktestRequest` model requires:
- `strategy` (required)
- `index` (default: "NIFTY")
- `date_from` (required)
- `date_to` (required)
- `expiry_window` (default: "weekly_expiry")

**Frontend sends:** ✅ All required fields present

## 2. Type Mismatches

### Spot Adjustment Type
- **Backend expects:** String - "None", "Rises", "Falls", "RisesOrFalls"
- **Frontend sends:** String (mapped from number)
- **Status:** ✅ Correctly mapped

### Numeric Fields
- **Backend expects:** float/int types
- **Frontend sends:** May send strings if input not parsed
- **Potential issue:** ⚠️ Check if `parseFloat()` is called consistently

## 3. Extra Fields Not in Model

Pydantic by default allows extra fields, but if `extra = "forbid"` is set in the model config, extra fields will cause 422 errors.

**Check:** Does `BacktestRequest` have `class Config: extra = "forbid"`?

## 4. Strategy-Specific Field Requirements

Different strategies require different fields:
- v1_ce_fut: needs `call_sell_position`, `call_sell`, `future_buy`
- v4_strangle: needs `call_sell_position`, `put_sell_position`, `call_sell`, `put_sell`
- v5_*: needs `protection`, `protection_pct`
- v7_premium: needs `premium_multiplier`, `call_premium`, `put_premium`

## 5. Frontend Payload Building Issues

Looking at `ConfigPanel.jsx` line 200-300, the `buildPayload()` function:

```javascript
const base = {
  strategy: engine,
  index: uiState.index,
  date_from: uiState.from_date,
  date_to: uiState.to_date,
  spot_adjustment_type: spotAdjustmentMap[uiState.spot_adjustment_type] || "None",
  spot_adjustment: uiState.spot_adjustment,
};
```

**Potential Issues:**
1. `uiState.spot_adjustment` might be a string instead of number
2. Strategy-specific parameters might not be numbers
3. Boolean fields might be sent as strings

## Recommended Fixes

### Fix 1: Ensure Type Consistency in Frontend

In `ConfigPanel.jsx`, ensure all numeric inputs are parsed:

```javascript
// In handleUiChange
const handleUiChange = (field, value) => {
  // Parse numeric fields
  if (field === 'spot_adjustment') {
    value = parseFloat(value) || 1.0;
  }
  setUiState(prev => ({ ...prev, [field]: value }));
};
```

### Fix 2: Validate Payload Before Sending

Add validation in `handleSubmit`:

```javascript
const handleSubmit = async (e) => {
  e.preventDefault();
  
  const payload = buildPayload();
  if (!payload) {
    setError("Invalid strategy configuration");
    return;
  }
  
  // Validate types
  if (typeof payload.spot_adjustment !== 'number') {
    payload.spot_adjustment = parseFloat(payload.spot_adjustment) || 1.0;
  }
  
  // Log payload for debugging
  console.log('Sending payload:', JSON.stringify(payload, null, 2));
  
  // ... rest of submit logic
};
```

### Fix 3: Add Error Details Display

When 422 error occurs, show the validation details:

```javascript
if (!response.ok) {
  const errorData = await response.json();
  console.error('Validation error:', errorData);
  
  // Extract field-specific errors
  if (errorData.detail && Array.isArray(errorData.detail)) {
    const fieldErrors = errorData.detail.map(err => 
      `${err.loc.join('.')}: ${err.msg}`
    ).join(', ');
    setError(`Validation error: ${fieldErrors}`);
  } else {
    setError(errorData.detail || "Backtest failed");
  }
}
```

## Next Steps

1. Run the backend with detailed logging
2. Check browser console for the exact payload being sent
3. Compare with the test script that succeeded
4. Add type validation in frontend before sending
