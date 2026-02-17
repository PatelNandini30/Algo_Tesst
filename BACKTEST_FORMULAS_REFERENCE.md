# Backtesting System - Complete Formula Reference

## Table of Contents
1. [Trade P&L Calculations](#trade-pnl-calculations)
2. [Performance Metrics](#performance-metrics)
3. [Risk Metrics](#risk-metrics)
4. [Statistical Metrics](#statistical-metrics)
5. [Lot Size Calculations](#lot-size-calculations)

---

## 1. Trade P&L Calculations

### Net P&L (Profit & Loss)
```
Net P&L = (Exit Price - Entry Price) × Quantity × Position Multiplier

Where:
- Position Multiplier = +1 for BUY positions
- Position Multiplier = -1 for SELL positions
- Quantity = Lot Size × Number of Lots
```

### Cumulative P&L
```
Cumulative P&L = Sum of all Net P&L up to current trade
```

### Spot P&L
```
Spot P&L = (Exit Spot Price - Entry Spot Price) × Quantity
```

---

## 2. Performance Metrics

### Total P&L
```
Total P&L = Sum of all Net P&L across all trades
```

### Win Rate (%)
```
Win Rate = (Number of Winning Trades / Total Number of Trades) × 100

Where:
- Winning Trade = Trade with Net P&L > 0
```

### Loss Rate (%)
```
Loss Rate = (Number of Losing Trades / Total Number of Trades) × 100

Where:
- Losing Trade = Trade with Net P&L < 0
```

### Average Profit Per Trade
```
Average Profit Per Trade = Total P&L / Total Number of Trades
```

### Average Win
```
Average Win = Sum of all Winning Trades P&L / Number of Winning Trades
```

### Average Loss
```
Average Loss = Sum of all Losing Trades P&L / Number of Losing Trades
```

### Max Win
```
Max Win = Maximum single trade profit across all trades
```

### Max Loss
```
Max Loss = Maximum single trade loss across all trades
```

### Reward to Risk Ratio
```
Reward to Risk = |Average Win| / |Average Loss|

Higher is better (indicates larger average wins compared to average losses)
```

### Expectancy
```
Expectancy = (Win Rate × Average Win) - (Loss Rate × |Average Loss|)

Positive expectancy indicates profitable system over long term
```

---

## 3. Risk Metrics

### Maximum Drawdown (Points)
```
Maximum Drawdown (Points) = Peak Cumulative P&L - Lowest Cumulative P&L after Peak
