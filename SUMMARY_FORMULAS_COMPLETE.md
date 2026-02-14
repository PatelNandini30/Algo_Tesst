# COMPLETE SUMMARY CALCULATIONS & FORMULAS

## 📊 All Formulas Used in Backtest Summary Statistics

This document contains **every formula** used to calculate backtest summary statistics, extracted from the actual production code.

---

## 1. DRAWDOWN CALCULATIONS

### 1.1 Cumulative P&L
```python
# First trade cumulative
Cumulative[0] = Entry_Spot + Net_PnL[0]

# Subsequent trades
for i in range(1, len(trades)):
    Cumulative[i] = Cumulative[i-1] + Net_PnL[i]
```

**Formula:**
```
Cumulative = Previous_Cumulative + Current_Net_PnL
```

**Example:**
```
Trade 1: Entry Spot = 20000, Net P&L = +500
  → Cumulative = 20000 + 500 = 20500

Trade 2: Net P&L = +300
  → Cumulative = 20500 + 300 = 20800

Trade 3: Net P&L = -200
  → Cumulative = 20800 - 200 = 20600
```

---

### 1.2 Peak (Running Maximum)
```python
Peak = Cumulative.cummax()
```

**Formula:**
```
Peak[i] = max(Cumulative[0], Cumulative[1], ..., Cumulative[i])
```

**Explanation:**
- Peak keeps track of the highest cumulative value reached so far
- Used as reference point for drawdown calculation
- Peak never decreases, only stays same or increases

**Example:**
```
Cumulative: [20500, 20800, 20600, 21000, 20900]
Peak:       [20500, 20800, 20800, 21000, 21000]
```

---

### 1.3 Drawdown in Points (DD)
```python
DD = np.where(Peak > Cumulative, Cumulative - Peak, 0)
```

**Formula:**
```
if Peak > Cumulative:
    DD = Cumulative - Peak  (will be negative)
else:
    DD = 0
```

**Explanation:**
- Drawdown is the difference between peak and current cumulative
- Always negative or zero
- Zero when at new peak (no drawdown)

**Example:**
```
Trade 1: Cumulative = 20500, Peak = 20500
  → DD = 0 (at peak)

Trade 2: Cumulative = 20800, Peak = 20800
  → DD = 0 (new peak)

Trade 3: Cumulative = 20600, Peak = 20800
  → DD = 20600 - 20800 = -200 points
```

---

### 1.4 Percentage Drawdown (%DD)
```python
%DD = np.where(DD == 0, 0, round(100 * (DD / Peak), 2))
```

**Formula:**
```
if DD == 0:
    %DD = 0
else:
    %DD = (DD / Peak) × 100
```

**Explanation:**
- Expresses drawdown as percentage of peak value
- Always negative or zero
- Shows relative impact of drawdown

**Example:**
```
Trade 3: DD = -200, Peak = 20800
  → %DD = (-200 / 20800) × 100 = -0.96%

Trade 5: DD = -500, Peak = 21000
  → %DD = (-500 / 21000) × 100 = -2.38%
```

---

## 2. BASIC P&L CALCULATIONS

### 2.1 Net P&L
```python
Net_PnL = Call_PnL + Put_PnL + Future_PnL
```

**For Call Options (Sell):**
```python
Call_PnL = Call_Entry_Price - Call_Exit_Price
```

**For Put Options (Sell):**
```python
Put_PnL = Put_Entry_Price - Put_Exit_Price
```

**For Future (Buy):**
```python
Future_PnL = Future_Exit_Price - Future_Entry_Price
```

---

### 2.2 Spot P&L
```python
Spot_PnL = Exit_Spot - Entry_Spot
```

**Purpose:** Shows what P&L would have been if just holding spot

---

### 2.3 Net P&L vs Spot Percentage
```python
Net_PnL_Spot_Pct = (Net_PnL / Entry_Spot) × 100
```

**Example:**
```
Net P&L = 500, Entry Spot = 20000
→ Net_PnL_Spot_Pct = (500 / 20000) × 100 = 2.5%
```

---

## 3. WIN/LOSS STATISTICS

### 3.1 Total Trades
```python
Count = len(trades)
```

---

### 3.2 Winning Trades
```python
Win_Count = len(trades[trades['Net P&L'] > 0])
Win_Percentage = (Win_Count / Total_Count) × 100
```

**Example:**
```
Total Trades = 100
Winning Trades = 65
→ Win% = (65/100) × 100 = 65%
```

---

### 3.3 Losing Trades
```python
Lose_Count = len(trades[trades['Net P&L'] < 0])
Lose_Percentage = (Lose_Count / Total_Count) × 100
```

---

### 3.4 Average Win
```python
Avg_Win = trades[trades['Net P&L'] > 0]['Net P&L'].mean()
```

**Example:**
```
Winning trades P&L: [500, 300, 800, 400]
→ Avg_Win = (500 + 300 + 800 + 400) / 4 = 500
```

---

### 3.5 Average Loss
```python
Avg_Loss = trades[trades['Net P&L'] < 0]['Net P&L'].mean()
```

**Note:** This is negative (e.g., -250 means average loss of 250 points)

---

### 3.6 Average Win Percentage
```python
Avg_Win_Pct = (Avg_Win / Total_Sum) × 100
```

**Example:**
```
Avg Win = 500, Total Sum = 10000
→ Avg_Win_Pct = (500/10000) × 100 = 5%
```

---

### 3.7 Average Loss Percentage
```python
Avg_Loss_Pct = (Avg_Loss / Total_Sum) × 100
```

---

## 4. EXPECTANCY

### 4.1 Expectancy Formula
```python
Expectancy = (((Avg_Win_Pct / abs(Avg_Loss_Pct)) × Win_Pct) - Lose_Pct) / 100
```

**Breakdown:**
1. **Reward-to-Risk Ratio:** `Avg_Win_Pct / abs(Avg_Loss_Pct)`
2. **Expected Win:** `Reward_Risk_Ratio × Win_Pct`
3. **Expected Loss:** `Lose_Pct`
4. **Net Expectancy:** `(Expected_Win - Expected_Loss) / 100`

**Example:**
```
Avg_Win_Pct = 5%, Avg_Loss_Pct = -3%
Win_Pct = 60%, Lose_Pct = 40%

Reward-to-Risk = 5 / 3 = 1.67
Expected Win = 1.67 × 60 = 100.2
Net Expectancy = (100.2 - 40) / 100 = 0.602

Interpretation: On average, expect to make 0.60 units per trade
```

---

## 5. CAGR (Compound Annual Growth Rate)

### 5.1 CAGR for Options Strategy
```python
CAGR_Options = 100 × (((Total_PnL + Entry_Spot) / Entry_Spot) ** (1 / Years) - 1)
```

**Components:**
- **Final Value:** `Total_PnL + Entry_Spot`
- **Initial Value:** `Entry_Spot`
- **Growth Factor:** `Final_Value / Initial_Value`
- **Years:** `(Last_Exit_Date - First_Entry_Date) / 365.25`

**Formula Breakdown:**
```
Step 1: Calculate total growth
  Total_Growth = (Final_Value / Initial_Value)

Step 2: Annualize the growth
  Annual_Growth = Total_Growth ^ (1 / Years)

Step 3: Convert to percentage
  CAGR = (Annual_Growth - 1) × 100
```

**Example:**
```
Entry Spot = 20000
Total P&L = 5000
Years = 2.5

Final Value = 20000 + 5000 = 25000
Growth Factor = 25000 / 20000 = 1.25
Annual Growth = 1.25 ^ (1/2.5) = 1.25 ^ 0.4 = 1.0905
CAGR = (1.0905 - 1) × 100 = 9.05%
```

**Validation:**
```python
if (Total_Sum + Entry_Spot) / Entry_Spot > 0 and Years > 0:
    # Calculate CAGR
else:
    CAGR = 0  # Prevent math errors
```

---

### 5.2 CAGR for Spot
```python
CAGR_Spot = 100 × (((Spot_Change + Entry_Spot) / Entry_Spot) ** (1 / Years) - 1)
```

**Purpose:** Compare strategy performance vs just holding spot

**Example:**
```
Entry Spot = 20000
Spot Change = 3000 (spot went from 20000 to 23000)
Years = 2.5

CAGR_Spot = 100 × ((23000/20000)^(1/2.5) - 1)
          = 100 × (1.15^0.4 - 1)
          = 100 × (1.0576 - 1)
          = 5.76%
```

---

## 6. MAXIMUM DRAWDOWN METRICS

### 6.1 Maximum Drawdown (%)
```python
Max_DD_Pct = min(%DD)
```

**Explanation:**
- Finds the worst (most negative) percentage drawdown
- Always negative or zero
- Key risk metric

**Example:**
```
%DD values: [0, -0.5%, -2.3%, -1.8%, 0, -3.5%, -2.1%]
Max_DD_Pct = -3.5%
```

---

### 6.2 Maximum Drawdown (Points)
```python
Max_DD_Points = min(DD)
```

**Example:**
```
DD values: [0, -100, -500, -300, 0, -700, -400]
Max_DD_Points = -700 points
```

---

## 7. RISK-ADJUSTED RETURNS

### 7.1 CAR/MDD (Calmar Ratio)
```python
CAR_MDD = CAGR_Options / abs(Max_DD_Pct)
```

**Purpose:** Risk-adjusted return metric

**Formula:**
```
CAR/MDD = Annual_Return / Max_Drawdown
```

**Example:**
```
CAGR = 15.2%
Max DD = -12.5%

CAR/MDD = 15.2 / 12.5 = 1.22
```

**Interpretation:**
- **> 1:** Good (return exceeds max drawdown)
- **> 2:** Excellent
- **< 1:** Risky (drawdown exceeds returns)

---

### 7.2 Recovery Factor
```python
Recovery_Factor = Total_PnL / abs(Max_DD_Points)
```

**Purpose:** How many times profit covers worst drawdown

**Example:**
```
Total P&L = 5000 points
Max DD = -700 points

Recovery_Factor = 5000 / 700 = 7.14
```

**Interpretation:**
- **Higher is better**
- Shows resilience after drawdowns
- Recovery Factor of 7.14 means profit is 7.14× the worst loss

---

## 8. ROI VS SPOT

### 8.1 ROI vs Spot Formula
```python
ROI_vs_Spot = (Total_PnL / Spot_Change) × 100
```

**Purpose:** Compare strategy P&L vs spot movement

**Example:**
```
Strategy Total P&L = 5000
Spot Change = 3000

ROI_vs_Spot = (5000 / 3000) × 100 = 166.67%
```

**Interpretation:**
- **> 100%:** Strategy outperformed spot
- **< 100%:** Spot outperformed strategy
- **166.67%:** Strategy made 66.67% more than spot

---

## 9. COMPLETE CALCULATION SEQUENCE

### Step-by-Step for Each Trade:

```python
# STEP 1: Calculate individual leg P&L
Call_PnL = Call_Entry_Price - Call_Exit_Price
Put_PnL = Put_Entry_Price - Put_Exit_Price
Future_PnL = Future_Exit_Price - Future_Entry_Price
Spot_PnL = Exit_Spot - Entry_Spot

# STEP 2: Calculate Net P&L
Net_PnL = Call_PnL + Put_PnL + Future_PnL

# STEP 3: Calculate Cumulative
if first_trade:
    Cumulative = Entry_Spot + Net_PnL
else:
    Cumulative = Previous_Cumulative + Net_PnL

# STEP 4: Calculate Peak
Peak = max(all_previous_cumulative_values_including_current)

# STEP 5: Calculate Drawdown
if Peak > Cumulative:
    DD = Cumulative - Peak  # Negative value
    %DD = (DD / Peak) × 100
else:
    DD = 0
    %DD = 0

# STEP 6: Calculate additional metrics
Net_PnL_Spot_Pct = (Net_PnL / Entry_Spot) × 100
```

---

## 10. SUMMARY STATISTICS CALCULATION

```python
# COUNT
Total_Trades = len(all_trades)
Winning_Trades = len(trades[Net_PnL > 0])
Losing_Trades = len(trades[Net_PnL < 0])

# PERCENTAGES
Win_Pct = (Winning_Trades / Total_Trades) × 100
Lose_Pct = (Losing_Trades / Total_Trades) × 100

# AVERAGES
Total_Sum = sum(all_Net_PnL)
Avg_Trade = Total_Sum / Total_Trades
Avg_Win = mean(Net_PnL[Net_PnL > 0])
Avg_Loss = mean(Net_PnL[Net_PnL < 0])

# PERCENTAGES OF TOTAL
Avg_Win_Pct = (Avg_Win / Total_Sum) × 100
Avg_Loss_Pct = (Avg_Loss / Total_Sum) × 100

# EXPECTANCY
Expectancy = (((Avg_Win_Pct / abs(Avg_Loss_Pct)) × Win_Pct) - Lose_Pct) / 100

# TIME CALCULATION
First_Entry = min(Entry_Dates)
Last_Exit = max(Exit_Dates)
Years = (Last_Exit - First_Entry) / 365.25

# CAGR
Final_Value = Entry_Spot + Total_PnL
CAGR_Options = 100 × ((Final_Value / Entry_Spot) ^ (1/Years) - 1)

Spot_Final = Entry_Spot + Total_Spot_Change
CAGR_Spot = 100 × ((Spot_Final / Entry_Spot) ^ (1/Years) - 1)

# DRAWDOWN METRICS
Max_DD_Pct = min(all_%DD_values)
Max_DD_Points = min(all_DD_values)

# RISK-ADJUSTED RETURNS
CAR_MDD = CAGR_Options / abs(Max_DD_Pct)
Recovery_Factor = Total_PnL / abs(Max_DD_Points)

# COMPARATIVE METRICS
ROI_vs_Spot = (Total_PnL / Total_Spot_Change) × 100
```

---

## 11. EXAMPLE: COMPLETE CALCULATION

### Input Data:
```
Trade 1: Entry Spot=20000, Net P&L=+500
Trade 2: Entry Spot=20500, Net P&L=+300
Trade 3: Entry Spot=20800, Net P&L=-200
Trade 4: Entry Spot=20600, Net P&L=+700
Trade 5: Entry Spot=21300, Net P&L=-400
```

### Calculations:

**Cumulative:**
```
Trade 1: 20000 + 500 = 20500
Trade 2: 20500 + 300 = 20800
Trade 3: 20800 - 200 = 20600
Trade 4: 20600 + 700 = 21300
Trade 5: 21300 - 400 = 20900
```

**Peak:**
```
Trade 1: 20500
Trade 2: 20800 (new high)
Trade 3: 20800 (stays at peak)
Trade 4: 21300 (new high)
Trade 5: 21300 (stays at peak)
```

**Drawdown (Points):**
```
Trade 1: 0 (at peak)
Trade 2: 0 (at peak)
Trade 3: 20600 - 20800 = -200
Trade 4: 0 (at peak)
Trade 5: 20900 - 21300 = -400
```

**Drawdown (%):**
```
Trade 1: 0%
Trade 2: 0%
Trade 3: -200/20800 × 100 = -0.96%
Trade 4: 0%
Trade 5: -400/21300 × 100 = -1.88%
```

### Summary:
```
Total Trades: 5
Winning Trades: 3 (60%)
Losing Trades: 2 (40%)
Total P&L: +900
Avg Trade: +180
Max DD: -1.88%
Max DD Points: -400
```

---

## 12. KEY INSIGHTS

### Why These Formulas Matter:

1. **Drawdown** - Shows risk and volatility
2. **CAGR** - Annualized returns for fair comparison
3. **Expectancy** - Expected profit per trade
4. **CAR/MDD** - Risk-adjusted performance
5. **Recovery Factor** - Resilience metric

### Common Pitfalls:

1. **CAGR with negative values** - Always check `Final_Value > 0`
2. **Division by zero** - Validate denominators
3. **Peak calculation** - Use cummax() for efficiency
4. **Date calculations** - Use 365.25 for leap years

---

## 13. PYTHON IMPLEMENTATION

Complete implementation ready to use:

```python
import pandas as pd
import numpy as np

def calculate_all_metrics(df):
    """
    Calculate all backtest metrics
    
    Args:
        df: DataFrame with columns [Entry Date, Exit Date, Entry Spot, 
                                     Exit Spot, Net P&L, etc.]
    
    Returns:
        dict: All calculated metrics
    """
    # Basic metrics
    entry_spot = df.iloc[0]['Entry Spot']
    total_trades = len(df)
    total_pnl = df['Net P&L'].sum()
    spot_change = df['Spot P&L'].sum()
    
    # Cumulative and Drawdown
    df['Cumulative'] = (entry_spot + df['Net P&L'].cumsum())
    df['Peak'] = df['Cumulative'].cummax()
    df['DD'] = np.where(df['Peak'] > df['Cumulative'], 
                        df['Cumulative'] - df['Peak'], 0)
    df['%DD'] = np.where(df['DD'] == 0, 0, 
                         (df['DD'] / df['Peak']) * 100)
    
    # Win/Loss statistics
    wins = df[df['Net P&L'] > 0]
    losses = df[df['Net P&L'] < 0]
    
    win_count = len(wins)
    loss_count = len(losses)
    win_pct = (win_count / total_trades) * 100
    loss_pct = (loss_count / total_trades) * 100
    
    avg_win = wins['Net P&L'].mean() if win_count > 0 else 0
    avg_loss = losses['Net P&L'].mean() if loss_count > 0 else 0
    
    avg_win_pct = (avg_win / total_pnl) * 100 if total_pnl != 0 else 0
    avg_loss_pct = (avg_loss / total_pnl) * 100 if total_pnl != 0 else 0
    
    # Expectancy
    if avg_loss_pct != 0:
        expectancy = (((avg_win_pct / abs(avg_loss_pct)) * win_pct) - loss_pct) / 100
    else:
        expectancy = 0
    
    # Time calculations
    first_entry = pd.to_datetime(df['Entry Date']).min()
    last_exit = pd.to_datetime(df['Exit Date']).max()
    years = (last_exit - first_entry).days / 365.25
    
    # CAGR
    if years > 0 and entry_spot > 0 and (total_pnl + entry_spot) > 0:
        cagr_options = (((total_pnl + entry_spot) / entry_spot) ** (1 / years) - 1) * 100
    else:
        cagr_options = 0
    
    if years > 0 and entry_spot > 0 and (spot_change + entry_spot) > 0:
        cagr_spot = (((spot_change + entry_spot) / entry_spot) ** (1 / years) - 1) * 100
    else:
        cagr_spot = 0
    
    # Drawdown metrics
    max_dd_pct = df['%DD'].min()
    max_dd_points = df['DD'].min()
    
    # Risk-adjusted returns
    car_mdd = cagr_options / abs(max_dd_pct) if max_dd_pct != 0 else 0
    recovery_factor = total_pnl / abs(max_dd_points) if max_dd_points != 0 else 0
    
    # ROI vs Spot
    roi_vs_spot = (total_pnl / spot_change) * 100 if spot_change != 0 else 0
    
    return {
        'total_trades': total_trades,
        'total_pnl': total_pnl,
        'win_pct': win_pct,
        'loss_pct': loss_pct,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'expectancy': expectancy,
        'cagr_options': cagr_options,
        'cagr_spot': cagr_spot,
        'max_dd_pct': max_dd_pct,
        'max_dd_points': max_dd_points,
        'car_mdd': car_mdd,
        'recovery_factor': recovery_factor,
        'roi_vs_spot': roi_vs_spot
    }
```

---

## 14. VALIDATION CHECKLIST

✅ **Drawdown** - Should be ≤ 0, never positive
✅ **Peak** - Should always increase or stay same, never decrease
✅ **Cumulative** - Should match manual addition of P&L
✅ **CAGR** - Should be reasonable (typically -50% to +100%)
✅ **Win%** - Should be between 0-100%
✅ **Expectancy** - Can be negative, positive, or zero

---

**END OF FORMULAS DOCUMENT**
