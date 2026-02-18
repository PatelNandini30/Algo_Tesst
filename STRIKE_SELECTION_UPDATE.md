# Strike Selection Enhancement - ITM/OTM Levels

## Summary
Added support for ITM1-ITM20 and OTM1-OTM20 strike selection options, matching AlgoTest functionality.

## Changes Made

### 1. Frontend (AlgoTestBacktest.jsx)
- Updated strike type dropdown to include:
  - ITM20, ITM19, ITM18... down to ITM1
  - ATM
  - OTM1, OTM2, OTM3... up to OTM20

### 2. Backend (base.py)
- `calculate_strike_from_selection()` function already supports this
- Automatically calculates correct strike based on:
  - Spot price
  - Strike interval (50 for NIFTY, 100 for BANKNIFTY)
  - Selection (ATM/ITM/OTM)
  - Option type (CE/PE)

## How It Works

### Example 1: NIFTY CE OTM2
- Spot Price: 12,350
- Strike Interval: 50
- Selection: OTM2
- Option Type: CE

**Calculation:**
1. ATM = round(12350 / 50) * 50 = 12,350
2. For CE + OTM = Higher strikes
3. Offset = 2 strikes * 50 = 100
4. **Final Strike = 12,350 + 100 = 12,450**

### Example 2: NIFTY PE ITM5
- Spot Price: 12,350
- Strike Interval: 50
- Selection: ITM5
- Option Type: PE

**Calculation:**
1. ATM = round(12350 / 50) * 50 = 12,350
2. For PE + ITM = Higher strikes (above spot)
3. Offset = 5 strikes * 50 = 250
4. **Final Strike = 12,350 + 250 = 12,600**

### Example 3: BANKNIFTY CE ITM10
- Spot Price: 45,250
- Strike Interval: 100
- Selection: ITM10
- Option Type: CE

**Calculation:**
1. ATM = round(45250 / 100) * 100 = 45,300
2. For CE + ITM = Lower strikes (below spot)
3. Offset = 10 strikes * 100 = 1,000
4. **Final Strike = 45,300 - 1,000 = 44,300**

## Strike Logic Summary

| Option Type | Selection | Direction |
|-------------|-----------|-----------|
| CE (Call)   | ITM       | LOWER strikes (below ATM) |
| CE (Call)   | OTM       | HIGHER strikes (above ATM) |
| PE (Put)    | ITM       | HIGHER strikes (above ATM) |
| PE (Put)    | OTM       | LOWER strikes (below ATM) |

## User Experience
Users can now select from 41 strike options:
- 20 ITM levels (ITM1 to ITM20)
- 1 ATM level
- 20 OTM levels (OTM1 to OTM20)

The system automatically calculates the correct strike price based on the current spot price and the selected level.
