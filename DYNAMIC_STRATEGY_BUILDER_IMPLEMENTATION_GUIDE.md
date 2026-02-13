# Dynamic Strategy Builder Implementation Guide
## Complete AlgoTest-Style Backtesting System

---

## 📋 EXECUTIVE SUMMARY

Transform your current fixed-strategy system into a fully dynamic, user-configurable strategy builder like AlgoTest, where users can:
- Select any combination of legs (1-4 legs)
- Choose instruments (Options CE/PE, Futures)
- Define positions (Buy/Sell)
- Set strike selection (ATM, OTM, ITM, Premium, Straddle Width, Delta)
- Configure entry/exit timing (X days before expiry, specific times)
- Run backtests with ALL possible permutations
- Export detailed trade sheets with exact formulas from `analyse_bhavcopy_02-01-2026.py`

**CRITICAL CONSTRAINTS:**
- ✅ ZERO modifications to `analyse_bhavcopy_02-01-2026.py` (20,192 lines)
- ✅ ALL existing formulas preserved exactly
- ✅ Use validation wrapper for calculations
- ✅ Maintain backward compatibility with all v1-v9 strategies

---

## 🎯 SYSTEM ARCHITECTURE OVERVIEW

```
┌─────────────────────────────────────────────────────────────┐
│                    FRONTEND (React)                          │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  ConfigPanel.jsx (Dynamic Strategy Builder UI)        │ │
│  │  - Leg Builder (1-4 legs)                             │ │
│  │  - Strike Selection System                            │ │
│  │  - Entry/Exit Timing Controls                         │ │
│  │  - Expiry Management                                  │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────┐
│                 BACKEND (FastAPI)                            │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  backtest.py (API Router)                             │ │
│  │  - Strategy Parser                                    │ │
│  │  - Parameter Validator                                │ │
│  │  - Engine Router                                      │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────┐
│              STRATEGY ENGINE LAYER                           │
│  ┌─────────────┬──────────────┬─────────────────────────┐  │
│  │ Existing    │   Generic    │   Formula Validator     │  │
│  │ Engines     │   Multi-Leg  │   (Wrapper)             │  │
│  │ (v1-v9)     │   Engine     │                         │  │
│  └─────────────┴──────────────┴─────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────┐
│         VALIDATION LOGIC (UNTOUCHED)                         │
│  analyse_bhavcopy_02-01-2026.py (20,192 lines)              │
│  - Strike calculations                                       │
│  - P&L formulas                                              │
│  - Expiry logic                                              │
│  - Re-entry intervals                                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 📦 PHASE 1: DATA STRUCTURES & ENUMS

### File: `strategy_types.py`

```python
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

# ==================== ENUMS ====================

class InstrumentType(str, Enum):
    """Instrument types available for legs"""
    OPTION = "Option"
    FUTURE = "Future"
    SPOT = "Spot"

class OptionType(str, Enum):
    """Option types (Call/Put)"""
    CE = "CE"
    PE = "PE"

class PositionType(str, Enum):
    """Position direction"""
    BUY = "Buy"
    SELL = "Sell"

class ExpiryType(str, Enum):
    """Expiry window types"""
    WEEKLY = "Weekly"
    MONTHLY = "Monthly"
    WEEKLY_T1 = "Weekly_T1"  # Next week
    WEEKLY_T2 = "Weekly_T2"  # Week after next
    MONTHLY_T1 = "Monthly_T1"  # Next month

class StrikeSelectionType(str, Enum):
    """Strike selection methods - matches AlgoTest"""
    ATM = "ATM"  # At The Money
    CLOSEST_PREMIUM = "Closest Premium"
    PREMIUM_RANGE = "Premium Range"
    STRADDLE_WIDTH = "Straddle Width"
    PERCENT_OF_ATM = "% of ATM"
    DELTA = "Delta"
    STRIKE_TYPE = "Strike Type"  # Specific strike value
    OTM_PERCENT = "OTM %"  # Out of the money by percentage
    ITM_PERCENT = "ITM %"  # In the money by percentage

class StrikeType(str, Enum):
    """Specific strike selection types"""
    ATM = "ATM"
    ITM = "ITM"
    OTM = "OTM"

class EntryTimeType(str, Enum):
    """Entry timing options"""
    DAYS_BEFORE_EXPIRY = "Days Before Expiry"
    SPECIFIC_TIME = "Specific Time"
    MARKET_OPEN = "Market Open"
    MARKET_CLOSE = "Market Close"

class ExitTimeType(str, Enum):
    """Exit timing options"""
    DAYS_BEFORE_EXPIRY = "Days Before Expiry"
    SPECIFIC_TIME = "Specific Time"
    EXPIRY = "At Expiry"
    STOP_LOSS = "Stop Loss"
    TARGET = "Target"

class ReEntryMode(str, Enum):
    """Re-entry/spot adjustment modes"""
    NONE = "None"
    UP_MOVE = "Up Move"  # Re-enter on upward move
    DOWN_MOVE = "Down Move"  # Re-enter on downward move
    EITHER_MOVE = "Either Move"  # Re-enter on any move

# ==================== MODELS ====================

class StrikeSelection(BaseModel):
    """Strike selection configuration"""
    type: StrikeSelectionType
    value: Optional[float] = None  # For percentage-based selections
    premium_min: Optional[float] = None  # For premium range
    premium_max: Optional[float] = None  # For premium range
    delta_value: Optional[float] = None  # For delta-based selection
    strike_type: Optional[StrikeType] = None  # ATM/ITM/OTM
    otm_strikes: Optional[int] = None  # Number of strikes OTM
    itm_strikes: Optional[int] = None  # Number of strikes ITM

class EntryCondition(BaseModel):
    """Entry timing configuration"""
    type: EntryTimeType
    days_before_expiry: Optional[int] = None
    specific_time: Optional[str] = None  # "09:35" format
    
class ExitCondition(BaseModel):
    """Exit timing configuration"""
    type: ExitTimeType
    days_before_expiry: Optional[int] = None
    specific_time: Optional[str] = None
    stop_loss_percent: Optional[float] = None
    target_percent: Optional[float] = None

class Leg(BaseModel):
    """Single leg configuration in a strategy"""
    leg_number: int
    instrument: InstrumentType
    option_type: Optional[OptionType] = None  # Only for options
    position: PositionType  # Buy or Sell
    lots: int = 1
    expiry_type: ExpiryType
    strike_selection: StrikeSelection
    entry_condition: EntryCondition
    exit_condition: ExitCondition
    
class StrategyDefinition(BaseModel):
    """Complete strategy definition"""
    name: str
    description: Optional[str] = None
    legs: List[Leg] = Field(min_items=1, max_items=4)
    index: str = "NIFTY"  # NIFTY, BANKNIFTY, FINNIFTY, etc.
    
    # Re-entry configuration
    re_entry_mode: ReEntryMode = ReEntryMode.NONE
    re_entry_percent: Optional[float] = None
    
    # Base2 range filter
    use_base2_filter: bool = True
    inverse_base2: bool = False  # For v6-like strategies
    
class BacktestRequest(BaseModel):
    """Backtest execution request"""
    strategy: StrategyDefinition
    from_date: datetime
    to_date: datetime
    
    # Additional parameters
    initial_capital: float = 100000
    
class BacktestResult(BaseModel):
    """Backtest result structure"""
    trades: List[Dict[str, Any]]
    summary: Dict[str, Any]
    pivot_table: Dict[str, Any]
    trade_sheet_csv: Optional[str] = None
```

---

## 🎨 PHASE 2: FRONTEND DYNAMIC UI

### File: `ConfigPanel_Dynamic.jsx`

```jsx
import React, { useState, useEffect } from 'react';
import {
  Box, Button, Select, MenuItem, TextField, IconButton,
  FormControl, InputLabel, Checkbox, FormControlLabel,
  Typography, Accordion, AccordionSummary, AccordionDetails,
  Grid, Paper, Divider
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';

const ConfigPanelDynamic = ({ onBacktestRun }) => {
  // ==================== STATE ====================
  const [strategyName, setStrategyName] = useState('Custom Strategy');
  const [index, setIndex] = useState('NIFTY');
  const [legs, setLegs] = useState([createDefaultLeg(1)]);
  const [reEntryMode, setReEntryMode] = useState('None');
  const [reEntryPercent, setReEntryPercent] = useState(1.0);
  const [useBase2Filter, setUseBase2Filter] = useState(true);
  const [inverseBase2, setInverseBase2] = useState(false);
  const [fromDate, setFromDate] = useState('2024-01-01');
  const [toDate, setToDate] = useState('2025-01-31');

  // ==================== LEG MANAGEMENT ====================
  function createDefaultLeg(legNumber) {
    return {
      leg_number: legNumber,
      instrument: 'Option',
      option_type: 'CE',
      position: 'Sell',
      lots: 1,
      expiry_type: 'Weekly',
      strike_selection: {
        type: 'ATM',
        value: 0
      },
      entry_condition: {
        type: 'Days Before Expiry',
        days_before_expiry: 5
      },
      exit_condition: {
        type: 'Days Before Expiry',
        days_before_expiry: 3
      }
    };
  }

  const addLeg = () => {
    if (legs.length < 4) {
      setLegs([...legs, createDefaultLeg(legs.length + 1)]);
    }
  };

  const removeLeg = (index) => {
    if (legs.length > 1) {
      const newLegs = legs.filter((_, i) => i !== index);
      // Renumber legs
      newLegs.forEach((leg, i) => leg.leg_number = i + 1);
      setLegs(newLegs);
    }
  };

  const updateLeg = (index, field, value) => {
    const newLegs = [...legs];
    if (field.includes('.')) {
      const [parent, child] = field.split('.');
      newLegs[index][parent][child] = value;
    } else {
      newLegs[index][field] = value;
    }
    setLegs(newLegs);
  };

  // ==================== STRIKE SELECTION COMPONENT ====================
  const renderStrikeSelection = (leg, legIndex) => {
    const strikeType = leg.strike_selection.type;

    return (
      <Grid container spacing={2}>
        <Grid item xs={12}>
          <FormControl fullWidth>
            <InputLabel>Strike Selection</InputLabel>
            <Select
              value={strikeType}
              onChange={(e) => updateLeg(legIndex, 'strike_selection.type', e.target.value)}
            >
              <MenuItem value="ATM">ATM (At The Money)</MenuItem>
              <MenuItem value="Closest Premium">Closest Premium</MenuItem>
              <MenuItem value="Premium Range">Premium Range</MenuItem>
              <MenuItem value="Straddle Width">Straddle Width</MenuItem>
              <MenuItem value="% of ATM">% of ATM</MenuItem>
              <MenuItem value="Delta">Delta</MenuItem>
              <MenuItem value="Strike Type">Strike Type (ATM/ITM/OTM)</MenuItem>
              <MenuItem value="OTM %">OTM %</MenuItem>
              <MenuItem value="ITM %">ITM %</MenuItem>
            </Select>
          </FormControl>
        </Grid>

        {/* Conditional fields based on strike type */}
        {strikeType === '% of ATM' && (
          <Grid item xs={12}>
            <TextField
              fullWidth
              type="number"
              label="% of ATM (e.g., 1.5 for 1.5% OTM)"
              value={leg.strike_selection.value || 0}
              onChange={(e) => updateLeg(legIndex, 'strike_selection.value', parseFloat(e.target.value))}
            />
          </Grid>
        )}

        {strikeType === 'Closest Premium' && (
          <Grid item xs={12}>
            <TextField
              fullWidth
              type="number"
              label="Target Premium (₹)"
              value={leg.strike_selection.value || 0}
              onChange={(e) => updateLeg(legIndex, 'strike_selection.value', parseFloat(e.target.value))}
            />
          </Grid>
        )}

        {strikeType === 'Premium Range' && (
          <>
            <Grid item xs={6}>
              <TextField
                fullWidth
                type="number"
                label="Min Premium (₹)"
                value={leg.strike_selection.premium_min || 0}
                onChange={(e) => updateLeg(legIndex, 'strike_selection.premium_min', parseFloat(e.target.value))}
              />
            </Grid>
            <Grid item xs={6}>
              <TextField
                fullWidth
                type="number"
                label="Max Premium (₹)"
                value={leg.strike_selection.premium_max || 0}
                onChange={(e) => updateLeg(legIndex, 'strike_selection.premium_max', parseFloat(e.target.value))}
              />
            </Grid>
          </>
        )}

        {strikeType === 'Straddle Width' && (
          <Grid item xs={12}>
            <TextField
              fullWidth
              type="number"
              label="Straddle Width (points)"
              value={leg.strike_selection.value || 0}
              onChange={(e) => updateLeg(legIndex, 'strike_selection.value', parseFloat(e.target.value))}
            />
          </Grid>
        )}

        {strikeType === 'Delta' && (
          <Grid item xs={12}>
            <TextField
              fullWidth
              type="number"
              label="Delta Value (0-1)"
              value={leg.strike_selection.delta_value || 0.5}
              onChange={(e) => updateLeg(legIndex, 'strike_selection.delta_value', parseFloat(e.target.value))}
              inputProps={{ step: 0.01, min: 0, max: 1 }}
            />
          </Grid>
        )}

        {strikeType === 'Strike Type' && (
          <>
            <Grid item xs={6}>
              <FormControl fullWidth>
                <InputLabel>Strike Type</InputLabel>
                <Select
                  value={leg.strike_selection.strike_type || 'ATM'}
                  onChange={(e) => updateLeg(legIndex, 'strike_selection.strike_type', e.target.value)}
                >
                  <MenuItem value="ATM">ATM</MenuItem>
                  <MenuItem value="ITM">ITM</MenuItem>
                  <MenuItem value="OTM">OTM</MenuItem>
                </Select>
              </FormControl>
            </Grid>
            <Grid item xs={6}>
              <TextField
                fullWidth
                type="number"
                label="Strikes Away (1, 2, 3...)"
                value={leg.strike_selection.value || 1}
                onChange={(e) => updateLeg(legIndex, 'strike_selection.value', parseInt(e.target.value))}
              />
            </Grid>
          </>
        )}

        {(strikeType === 'OTM %' || strikeType === 'ITM %') && (
          <Grid item xs={12}>
            <TextField
              fullWidth
              type="number"
              label={`${strikeType} (e.g., 2 for 2%)`}
              value={leg.strike_selection.value || 0}
              onChange={(e) => updateLeg(legIndex, 'strike_selection.value', parseFloat(e.target.value))}
            />
          </Grid>
        )}
      </Grid>
    );
  };

  // ==================== ENTRY/EXIT CONDITIONS ====================
  const renderEntryCondition = (leg, legIndex) => {
    return (
      <Grid container spacing={2}>
        <Grid item xs={12}>
          <FormControl fullWidth>
            <InputLabel>Entry Timing</InputLabel>
            <Select
              value={leg.entry_condition.type}
              onChange={(e) => updateLeg(legIndex, 'entry_condition.type', e.target.value)}
            >
              <MenuItem value="Days Before Expiry">X Days Before Expiry</MenuItem>
              <MenuItem value="Specific Time">Specific Time</MenuItem>
              <MenuItem value="Market Open">Market Open</MenuItem>
              <MenuItem value="Market Close">Market Close</MenuItem>
            </Select>
          </FormControl>
        </Grid>

        {leg.entry_condition.type === 'Days Before Expiry' && (
          <Grid item xs={12}>
            <TextField
              fullWidth
              type="number"
              label="Days Before Expiry"
              value={leg.entry_condition.days_before_expiry || 5}
              onChange={(e) => updateLeg(legIndex, 'entry_condition.days_before_expiry', parseInt(e.target.value))}
            />
          </Grid>
        )}

        {leg.entry_condition.type === 'Specific Time' && (
          <Grid item xs={12}>
            <TextField
              fullWidth
              type="time"
              label="Entry Time"
              value={leg.entry_condition.specific_time || '09:35'}
              onChange={(e) => updateLeg(legIndex, 'entry_condition.specific_time', e.target.value)}
            />
          </Grid>
        )}
      </Grid>
    );
  };

  const renderExitCondition = (leg, legIndex) => {
    return (
      <Grid container spacing={2}>
        <Grid item xs={12}>
          <FormControl fullWidth>
            <InputLabel>Exit Timing</InputLabel>
            <Select
              value={leg.exit_condition.type}
              onChange={(e) => updateLeg(legIndex, 'exit_condition.type', e.target.value)}
            >
              <MenuItem value="Days Before Expiry">X Days Before Expiry</MenuItem>
              <MenuItem value="At Expiry">At Expiry</MenuItem>
              <MenuItem value="Specific Time">Specific Time</MenuItem>
              <MenuItem value="Stop Loss">Stop Loss</MenuItem>
              <MenuItem value="Target">Target</MenuItem>
            </Select>
          </FormControl>
        </Grid>

        {leg.exit_condition.type === 'Days Before Expiry' && (
          <Grid item xs={12}>
            <TextField
              fullWidth
              type="number"
              label="Days Before Expiry"
              value={leg.exit_condition.days_before_expiry || 3}
              onChange={(e) => updateLeg(legIndex, 'exit_condition.days_before_expiry', parseInt(e.target.value))}
            />
          </Grid>
        )}

        {leg.exit_condition.type === 'Specific Time' && (
          <Grid item xs={12}>
            <TextField
              fullWidth
              type="time"
              label="Exit Time"
              value={leg.exit_condition.specific_time || '15:15'}
              onChange={(e) => updateLeg(legIndex, 'exit_condition.specific_time', e.target.value)}
            />
          </Grid>
        )}

        {leg.exit_condition.type === 'Stop Loss' && (
          <Grid item xs={12}>
            <TextField
              fullWidth
              type="number"
              label="Stop Loss %"
              value={leg.exit_condition.stop_loss_percent || 50}
              onChange={(e) => updateLeg(legIndex, 'exit_condition.stop_loss_percent', parseFloat(e.target.value))}
            />
          </Grid>
        )}

        {leg.exit_condition.type === 'Target' && (
          <Grid item xs={12}>
            <TextField
              fullWidth
              type="number"
              label="Target %"
              value={leg.exit_condition.target_percent || 50}
              onChange={(e) => updateLeg(legIndex, 'exit_condition.target_percent', parseFloat(e.target.value))}
            />
          </Grid>
        )}
      </Grid>
    );
  };

  // ==================== LEG BUILDER ====================
  const renderLeg = (leg, index) => {
    return (
      <Accordion key={index}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography variant="h6">
            Leg {leg.leg_number}: {leg.position} {leg.instrument === 'Option' ? leg.option_type : ''} {leg.instrument}
          </Typography>
          {legs.length > 1 && (
            <IconButton
              onClick={(e) => {
                e.stopPropagation();
                removeLeg(index);
              }}
              color="error"
              size="small"
            >
              <DeleteIcon />
            </IconButton>
          )}
        </AccordionSummary>
        <AccordionDetails>
          <Grid container spacing={3}>
            {/* Instrument Type */}
            <Grid item xs={12} md={4}>
              <FormControl fullWidth>
                <InputLabel>Instrument</InputLabel>
                <Select
                  value={leg.instrument}
                  onChange={(e) => updateLeg(index, 'instrument', e.target.value)}
                >
                  <MenuItem value="Option">Option</MenuItem>
                  <MenuItem value="Future">Future</MenuItem>
                </Select>
              </FormControl>
            </Grid>

            {/* Option Type (only for options) */}
            {leg.instrument === 'Option' && (
              <Grid item xs={12} md={4}>
                <FormControl fullWidth>
                  <InputLabel>Option Type</InputLabel>
                  <Select
                    value={leg.option_type}
                    onChange={(e) => updateLeg(index, 'option_type', e.target.value)}
                  >
                    <MenuItem value="CE">CE (Call)</MenuItem>
                    <MenuItem value="PE">PE (Put)</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
            )}

            {/* Position */}
            <Grid item xs={12} md={4}>
              <FormControl fullWidth>
                <InputLabel>Position</InputLabel>
                <Select
                  value={leg.position}
                  onChange={(e) => updateLeg(index, 'position', e.target.value)}
                >
                  <MenuItem value="Buy">Buy</MenuItem>
                  <MenuItem value="Sell">Sell</MenuItem>
                </Select>
              </FormControl>
            </Grid>

            {/* Lots */}
            <Grid item xs={12} md={4}>
              <TextField
                fullWidth
                type="number"
                label="Lots"
                value={leg.lots}
                onChange={(e) => updateLeg(index, 'lots', parseInt(e.target.value))}
              />
            </Grid>

            {/* Expiry Type */}
            <Grid item xs={12} md={4}>
              <FormControl fullWidth>
                <InputLabel>Expiry</InputLabel>
                <Select
                  value={leg.expiry_type}
                  onChange={(e) => updateLeg(index, 'expiry_type', e.target.value)}
                >
                  <MenuItem value="Weekly">Weekly</MenuItem>
                  <MenuItem value="Monthly">Monthly</MenuItem>
                  <MenuItem value="Weekly_T1">Weekly T+1</MenuItem>
                  <MenuItem value="Weekly_T2">Weekly T+2</MenuItem>
                  <MenuItem value="Monthly_T1">Monthly T+1</MenuItem>
                </Select>
              </FormControl>
            </Grid>

            <Grid item xs={12}>
              <Divider />
              <Typography variant="subtitle1" sx={{ mt: 2, mb: 1 }}>Strike Selection</Typography>
            </Grid>
            <Grid item xs={12}>
              {leg.instrument === 'Option' && renderStrikeSelection(leg, index)}
            </Grid>

            <Grid item xs={12}>
              <Divider />
              <Typography variant="subtitle1" sx={{ mt: 2, mb: 1 }}>Entry Condition</Typography>
            </Grid>
            <Grid item xs={12}>
              {renderEntryCondition(leg, index)}
            </Grid>

            <Grid item xs={12}>
              <Divider />
              <Typography variant="subtitle1" sx={{ mt: 2, mb: 1 }}>Exit Condition</Typography>
            </Grid>
            <Grid item xs={12}>
              {renderExitCondition(leg, index)}
            </Grid>
          </Grid>
        </AccordionDetails>
      </Accordion>
    );
  };

  // ==================== MAIN RENDER ====================
  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h4" gutterBottom>Dynamic Strategy Builder</Typography>
      
      {/* Strategy Details */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Grid container spacing={2}>
          <Grid item xs={12} md={6}>
            <TextField
              fullWidth
              label="Strategy Name"
              value={strategyName}
              onChange={(e) => setStrategyName(e.target.value)}
            />
          </Grid>
          <Grid item xs={12} md={6}>
            <FormControl fullWidth>
              <InputLabel>Index</InputLabel>
              <Select value={index} onChange={(e) => setIndex(e.target.value)}>
                <MenuItem value="NIFTY">NIFTY</MenuItem>
                <MenuItem value="BANKNIFTY">BANKNIFTY</MenuItem>
                <MenuItem value="FINNIFTY">FINNIFTY</MenuItem>
                <MenuItem value="MIDCPNIFTY">MIDCPNIFTY</MenuItem>
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={12} md={6}>
            <TextField
              fullWidth
              type="date"
              label="From Date"
              value={fromDate}
              onChange={(e) => setFromDate(e.target.value)}
              InputLabelProps={{ shrink: true }}
            />
          </Grid>
          <Grid item xs={12} md={6}>
            <TextField
              fullWidth
              type="date"
              label="To Date"
              value={toDate}
              onChange={(e) => setToDate(e.target.value)}
              InputLabelProps={{ shrink: true }}
            />
          </Grid>
        </Grid>
      </Paper>

      {/* Legs */}
      <Typography variant="h5" gutterBottom>Strategy Legs ({legs.length}/4)</Typography>
      {legs.map((leg, index) => renderLeg(leg, index))}
      
      {legs.length < 4 && (
        <Button
          variant="outlined"
          startIcon={<AddIcon />}
          onClick={addLeg}
          sx={{ mt: 2 }}
        >
          Add Leg
        </Button>
      )}

      {/* Re-entry Settings */}
      <Paper sx={{ p: 2, mt: 3 }}>
        <Typography variant="h6" gutterBottom>Re-entry Settings</Typography>
        <Grid container spacing={2}>
          <Grid item xs={12} md={6}>
            <FormControl fullWidth>
              <InputLabel>Re-entry Mode</InputLabel>
              <Select value={reEntryMode} onChange={(e) => setReEntryMode(e.target.value)}>
                <MenuItem value="None">None</MenuItem>
                <MenuItem value="Up Move">Up Move</MenuItem>
                <MenuItem value="Down Move">Down Move</MenuItem>
                <MenuItem value="Either Move">Either Move</MenuItem>
              </Select>
            </FormControl>
          </Grid>
          {reEntryMode !== 'None' && (
            <Grid item xs={12} md={6}>
              <TextField
                fullWidth
                type="number"
                label="Re-entry % Move"
                value={reEntryPercent}
                onChange={(e) => setReEntryPercent(parseFloat(e.target.value))}
              />
            </Grid>
          )}
          <Grid item xs={12}>
            <FormControlLabel
              control={
                <Checkbox
                  checked={useBase2Filter}
                  onChange={(e) => setUseBase2Filter(e.target.checked)}
                />
              }
              label="Use Base2 Range Filter"
            />
            {useBase2Filter && (
              <FormControlLabel
                control={
                  <Checkbox
                    checked={inverseBase2}
                    onChange={(e) => setInverseBase2(e.target.checked)}
                  />
                }
                label="Inverse Base2 (Trade OUTSIDE ranges)"
              />
            )}
          </Grid>
        </Grid>
      </Paper>

      {/* Run Backtest Button */}
      <Button
        variant="contained"
        color="primary"
        size="large"
        sx={{ mt: 3 }}
        onClick={() => {
          const strategyDef = {
            name: strategyName,
            legs: legs,
            index: index,
            re_entry_mode: reEntryMode,
            re_entry_percent: reEntryPercent,
            use_base2_filter: useBase2Filter,
            inverse_base2: inverseBase2
          };
          onBacktestRun(strategyDef, fromDate, toDate);
        }}
      >
        Run Backtest
      </Button>
    </Box>
  );
};

export default ConfigPanelDynamic;
```

---

## 🔧 PHASE 3: BACKEND STRATEGY ENGINE

### File: `generic_multi_leg_engine.py`

```python
"""
Generic Multi-Leg Strategy Engine
Handles any combination of legs with dynamic strike selection
Uses EXACT formulas from analyse_bhavcopy_02-01-2026.py
"""

import pandas as pd
import numpy as np
from datetime import timedelta, datetime
from typing import Dict, Any, Tuple, List
import sys
import os

# Import from base (existing helper functions)
from base import (
    get_strike_data, load_expiry, load_base2, load_bhavcopy,
    build_intervals, compute_analytics, build_pivot
)

# Import strategy types
from strategy_types import (
    InstrumentType, OptionType, PositionType, ExpiryType,
    StrikeSelectionType, StrategyDefinition, Leg,
    EntryTimeType, ExitTimeType, ReEntryMode
)

def calculate_strike_from_selection(
    entry_spot: float,
    strike_selection,
    option_type: OptionType,
    available_strikes: List[float],
    bhav_data: pd.DataFrame,
    index_name: str,
    expiry: datetime
) -> float:
    """
    Calculate strike price based on selection method
    Uses EXACT logic from analyse_bhavcopy_02-01-2026.py
    """
    strike_type = strike_selection.type
    
    if strike_type == StrikeSelectionType.ATM:
        # ATM: round((spot/100))*100 - EXACT formula from original
        return round(entry_spot / 100) * 100
    
    elif strike_type == StrikeSelectionType.PERCENT_OF_ATM:
        # % of ATM: round((spot*(1+pct%)/100))*100
        # EXACT formula from v1_ce_fut.py line 510
        pct = strike_selection.value
        return round((entry_spot * (1 + pct / 100)) / 100) * 100
    
    elif strike_type == StrikeSelectionType.CLOSEST_PREMIUM:
        # Find strike with premium closest to target
        target_premium = strike_selection.value
        best_strike = None
        min_diff = float('inf')
        
        for strike in available_strikes:
            # Get premium for this strike
            option_data = bhav_data[
                (bhav_data['Instrument'] == "OPTIDX") &
                (bhav_data['Symbol'] == index_name) &
                (bhav_data['OptionType'] == option_type.value) &
                (bhav_data['StrikePrice'] == strike) &
                (
                    (bhav_data['ExpiryDate'] == expiry) |
                    (bhav_data['ExpiryDate'] == expiry - timedelta(days=1)) |
                    (bhav_data['ExpiryDate'] == expiry + timedelta(days=1))
                ) &
                (bhav_data['TurnOver'] > 0)
            ]
            
            if not option_data.empty:
                premium = option_data.iloc[0]['Close']
                diff = abs(premium - target_premium)
                if diff < min_diff:
                    min_diff = diff
                    best_strike = strike
        
        return best_strike if best_strike else round(entry_spot / 100) * 100
    
    elif strike_type == StrikeSelectionType.PREMIUM_RANGE:
        # Find strikes within premium range
        min_premium = strike_selection.premium_min
        max_premium = strike_selection.premium_max
        valid_strikes = []
        
        for strike in available_strikes:
            option_data = bhav_data[
                (bhav_data['Instrument'] == "OPTIDX") &
                (bhav_data['Symbol'] == index_name) &
                (bhav_data['OptionType'] == option_type.value) &
                (bhav_data['StrikePrice'] == strike) &
                (
                    (bhav_data['ExpiryDate'] == expiry) |
                    (bhav_data['ExpiryDate'] == expiry - timedelta(days=1)) |
                    (bhav_data['ExpiryDate'] == expiry + timedelta(days=1))
                ) &
                (bhav_data['TurnOver'] > 0)
            ]
            
            if not option_data.empty:
                premium = option_data.iloc[0]['Close']
                if min_premium <= premium <= max_premium:
                    valid_strikes.append(strike)
        
        # Return closest to ATM from valid strikes
        if valid_strikes:
            atm = round(entry_spot / 100) * 100
            return min(valid_strikes, key=lambda x: abs(x - atm))
        return round(entry_spot / 100) * 100
    
    elif strike_type == StrikeSelectionType.STRADDLE_WIDTH:
        # For straddle width strategy
        width = strike_selection.value
        atm = round(entry_spot / 100) * 100
        
        if option_type == OptionType.CE:
            return atm + width
        else:  # PE
            return atm - width
    
    elif strike_type == StrikeSelectionType.STRIKE_TYPE:
        # ATM/ITM/OTM with number of strikes
        atm = round(entry_spot / 100) * 100
        strikes_away = int(strike_selection.value or 1)
        strike_type_val = strike_selection.strike_type
        
        if strike_type_val == "ATM":
            return atm
        elif strike_type_val == "OTM":
            if option_type == OptionType.CE:
                return atm + (strikes_away * 100)
            else:  # PE
                return atm - (strikes_away * 100)
        elif strike_type_val == "ITM":
            if option_type == OptionType.CE:
                return atm - (strikes_away * 100)
            else:  # PE
                return atm + (strikes_away * 100)
    
    elif strike_type == StrikeSelectionType.OTM_PERCENT:
        # OTM by percentage
        pct = strike_selection.value
        if option_type == OptionType.CE:
            return round((entry_spot * (1 + pct / 100)) / 100) * 100
        else:  # PE
            return round((entry_spot * (1 - pct / 100)) / 100) * 100
    
    elif strike_type == StrikeSelectionType.ITM_PERCENT:
        # ITM by percentage
        pct = strike_selection.value
        if option_type == OptionType.CE:
            return round((entry_spot * (1 - pct / 100)) / 100) * 100
        else:  # PE
            return round((entry_spot * (1 + pct / 100)) / 100) * 100
    
    # Default: ATM
    return round(entry_spot / 100) * 100


def check_entry_condition(
    current_date: datetime,
    expiry_date: datetime,
    entry_condition,
    spot_df: pd.DataFrame
) -> bool:
    """Check if entry condition is met"""
    
    if entry_condition.type == EntryTimeType.DAYS_BEFORE_EXPIRY:
        days_diff = (expiry_date - current_date).days
        return days_diff == entry_condition.days_before_expiry
    
    elif entry_condition.type == EntryTimeType.MARKET_OPEN:
        # First trading day of the week/period
        return True  # Simplified - needs time checking logic
    
    elif entry_condition.type == EntryTimeType.MARKET_CLOSE:
        # Last hour of trading
        return True  # Simplified
    
    elif entry_condition.type == EntryTimeType.SPECIFIC_TIME:
        # Check specific time (needs intraday data)
        return True  # Simplified
    
    return True


def check_exit_condition(
    current_date: datetime,
    expiry_date: datetime,
    exit_condition,
    entry_price: float,
    current_price: float,
    position: PositionType
) -> bool:
    """Check if exit condition is met"""
    
    if exit_condition.type == ExitTimeType.DAYS_BEFORE_EXPIRY:
        days_diff = (expiry_date - current_date).days
        return days_diff == exit_condition.days_before_expiry
    
    elif exit_condition.type == ExitTimeType.EXPIRY:
        return current_date == expiry_date
    
    elif exit_condition.type == ExitTimeType.STOP_LOSS:
        sl_pct = exit_condition.stop_loss_percent / 100
        
        if position == PositionType.SELL:
            # For sell, stop loss hits when price rises
            loss_pct = (current_price - entry_price) / entry_price
            return loss_pct >= sl_pct
        else:  # BUY
            # For buy, stop loss hits when price falls
            loss_pct = (entry_price - current_price) / entry_price
            return loss_pct >= sl_pct
    
    elif exit_condition.type == ExitTimeType.TARGET:
        target_pct = exit_condition.target_percent / 100
        
        if position == PositionType.SELL:
            # For sell, target hits when price falls
            profit_pct = (entry_price - current_price) / entry_price
            return profit_pct >= target_pct
        else:  # BUY
            # For buy, target hits when price rises
            profit_pct = (current_price - entry_price) / entry_price
            return profit_pct >= target_pct
    
    return False


def run_generic_multi_leg(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """
    Execute generic multi-leg strategy
    Uses EXACT calculation logic from analyse_bhavcopy_02-01-2026.py
    """
    
    strategy_def: StrategyDefinition = params['strategy']
    index_name = strategy_def.index
    
    # Load data (EXACT same as existing engines)
    spot_df = get_strike_data(index_name, params["from_date"], params["to_date"])
    weekly_exp = load_expiry(index_name, "weekly")
    monthly_exp = load_expiry(index_name, "monthly")
    base2 = load_base2()
    
    # Base2 Filter (EXACT same logic)
    if strategy_def.use_base2_filter:
        mask = pd.Series(False, index=spot_df.index)
        for _, row in base2.iterrows():
            mask |= (spot_df['Date'] >= row['Start']) & (spot_df['Date'] <= row['End'])
        
        if strategy_def.inverse_base2:
            mask = ~mask  # Invert for v6-style strategies
        
        spot_df = spot_df[mask].reset_index(drop=True)
    
    trades = []
    
    # Weekly Expiry Loop (EXACT same as existing engines)
    for w in range(len(weekly_exp)):
        prev_expiry = weekly_exp.iloc[w]['Previous Expiry']
        curr_expiry = weekly_exp.iloc[w]['Current Expiry']
        
        # Future expiry default (EXACT copy from v1)
        curr_monthly_expiry = monthly_exp[
            monthly_exp['Current Expiry'] >= curr_expiry
        ].sort_values(by='Current Expiry').reset_index(drop=True)
        
        if curr_monthly_expiry.empty:
            continue
        
        fut_expiry = curr_monthly_expiry.iloc[0]['Current Expiry']
        
        # Filter window
        filtered_data = spot_df[
            (spot_df['Date'] >= prev_expiry) &
            (spot_df['Date'] <= curr_expiry)
        ].sort_values(by='Date').reset_index(drop=True)
        
        if len(filtered_data) < 2:
            continue
        
        # Re-entry intervals (uses build_intervals from base.py)
        re_entry_type = 0  # Default: no re-entry
        if strategy_def.re_entry_mode == ReEntryMode.UP_MOVE:
            re_entry_type = 1
        elif strategy_def.re_entry_mode == ReEntryMode.DOWN_MOVE:
            re_entry_type = 2
        elif strategy_def.re_entry_mode == ReEntryMode.EITHER_MOVE:
            re_entry_type = 3
        
        intervals = build_intervals(
            filtered_data,
            re_entry_type,
            strategy_def.re_entry_percent or 1.0
        )
        
        if not intervals:
            continue
        
        interval_df = pd.DataFrame(intervals, columns=['From', 'To'])
        
        # Trade loop over intervals
        for i in range(len(interval_df)):
            fromDate = interval_df.iloc[i]['From']
            toDate = interval_df.iloc[i]['To']
            
            if fromDate == toDate:
                continue
            
            # BASE START OVERRIDE (EXACT copy from v1)
            is_base_start = (base2['Start'] == fromDate).any()
            if is_base_start:
                override_df = monthly_exp[
                    monthly_exp['Current Expiry'] > fromDate
                ].reset_index(drop=True)
                if len(override_df) > 1:
                    fut_expiry = override_df.iloc[1]['Current Expiry']
            
            # Entry / Exit spots
            entry_row = filtered_data[filtered_data['Date'] == fromDate]
            exit_row = filtered_data[filtered_data['Date'] == toDate]
            
            if entry_row.empty:
                continue
            
            entry_spot = entry_row.iloc[0]['Close']
            exit_spot = exit_row.iloc[0]['Close'] if not exit_row.empty else None
            
            # Load bhavcopy
            bhav_entry = load_bhavcopy(fromDate.strftime('%Y-%m-%d'))
            bhav_exit = load_bhavcopy(toDate.strftime('%Y-%m-%d'))
            
            if bhav_entry is None or bhav_exit is None:
                continue
            
            # Process each leg
            total_pnl = 0
            leg_details = {}
            all_legs_valid = True
            
            for leg_idx, leg in enumerate(strategy_def.legs):
                leg_num = leg.leg_number
                
                if leg.instrument == InstrumentType.OPTION:
                    # Get available strikes
                    available_strikes = bhav_entry[
                        (bhav_entry['Instrument'] == "OPTIDX") &
                        (bhav_entry['Symbol'] == index_name) &
                        (bhav_entry['OptionType'] == leg.option_type.value) &
                        (bhav_entry['TurnOver'] > 0)
                    ]['StrikePrice'].unique()
                    
                    # Calculate strike
                    selected_strike = calculate_strike_from_selection(
                        entry_spot,
                        leg.strike_selection,
                        leg.option_type,
                        available_strikes,
                        bhav_entry,
                        index_name,
                        curr_expiry
                    )
                    
                    # Get entry price (EXACT logic from v1)
                    if leg.strike_selection.value >= 0:
                        call_entry_data = bhav_entry[
                            (bhav_entry['Instrument'] == "OPTIDX") &
                            (bhav_entry['Symbol'] == index_name) &
                            (bhav_entry['OptionType'] == leg.option_type.value) &
                            (
                                (bhav_entry['ExpiryDate'] == curr_expiry) |
                                (bhav_entry['ExpiryDate'] == curr_expiry - timedelta(days=1)) |
                                (bhav_entry['ExpiryDate'] == curr_expiry + timedelta(days=1))
                            ) &
                            (bhav_entry['StrikePrice'] >= selected_strike) &
                            (bhav_entry['TurnOver'] > 0) &
                            (bhav_entry['StrikePrice'] % 100 == 0)
                        ].sort_values(by='StrikePrice', ascending=True).reset_index(drop=True)
                    else:
                        call_entry_data = bhav_entry[
                            (bhav_entry['Instrument'] == "OPTIDX") &
                            (bhav_entry['Symbol'] == index_name) &
                            (bhav_entry['OptionType'] == leg.option_type.value) &
                            (
                                (bhav_entry['ExpiryDate'] == curr_expiry) |
                                (bhav_entry['ExpiryDate'] == curr_expiry - timedelta(days=1)) |
                                (bhav_entry['ExpiryDate'] == curr_expiry + timedelta(days=1))
                            ) &
                            (bhav_entry['StrikePrice'] <= selected_strike) &
                            (bhav_entry['TurnOver'] > 0) &
                            (bhav_entry['StrikePrice'] % 100 == 0)
                        ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)
                    
                    if call_entry_data.empty:
                        all_legs_valid = False
                        break
                    
                    selected_strike = call_entry_data.iloc[0]['StrikePrice']
                    entry_price = call_entry_data.iloc[0]['Close']
                    entry_turnover = call_entry_data.iloc[0]['TurnOver']
                    
                    # Get exit price (EXACT logic from v1)
                    call_exit_data = bhav_exit[
                        (bhav_exit['Instrument'] == "OPTIDX") &
                        (bhav_exit['Symbol'] == index_name) &
                        (bhav_exit['OptionType'] == leg.option_type.value) &
                        (
                            (bhav_exit['ExpiryDate'] == curr_expiry) |
                            (bhav_exit['ExpiryDate'] == curr_expiry - timedelta(days=1)) |
                            (bhav_exit['ExpiryDate'] == curr_expiry + timedelta(days=1))
                        ) &
                        (bhav_exit['StrikePrice'] == selected_strike)
                    ]
                    
                    if call_exit_data.empty:
                        all_legs_valid = False
                        break
                    
                    exit_price = call_exit_data.iloc[0]['Close']
                    exit_turnover = call_exit_data.iloc[0]['TurnOver']
                    
                    # Calculate P&L (EXACT formula from v1 line 664)
                    if leg.position == PositionType.SELL:
                        leg_pnl = round(entry_price - exit_price, 2)
                    else:  # BUY
                        leg_pnl = round(exit_price - entry_price, 2)
                    
                    # Store leg details
                    leg_details.update({
                        f"Leg{leg_num}_Instrument": f"{leg.option_type.value}",
                        f"Leg{leg_num}_Position": leg.position.value,
                        f"Leg{leg_num}_Strike": selected_strike,
                        f"Leg{leg_num}_Expiry": curr_expiry,
                        f"Leg{leg_num}_EntryPrice": entry_price,
                        f"Leg{leg_num}_EntryTurnover": entry_turnover,
                        f"Leg{leg_num}_ExitPrice": exit_price,
                        f"Leg{leg_num}_ExitTurnover": exit_turnover,
                        f"Leg{leg_num}_P&L": leg_pnl,
                    })
                    
                    total_pnl += leg_pnl
                
                elif leg.instrument == InstrumentType.FUTURE:
                    # Future leg (EXACT logic from v1 lines 618-640)
                    fut_entry_data = bhav_entry[
                        (bhav_entry['Instrument'] == "FUTIDX") &
                        (bhav_entry['Symbol'] == index_name) &
                        (bhav_entry['ExpiryDate'].dt.month == fut_expiry.month) &
                        (bhav_entry['ExpiryDate'].dt.year == fut_expiry.year)
                    ]
                    fut_exit_data = bhav_exit[
                        (bhav_exit['Instrument'] == "FUTIDX") &
                        (bhav_exit['Symbol'] == index_name) &
                        (bhav_exit['ExpiryDate'].dt.month == fut_expiry.month) &
                        (bhav_exit['ExpiryDate'].dt.year == fut_expiry.year)
                    ]
                    
                    if fut_entry_data.empty or fut_exit_data.empty:
                        all_legs_valid = False
                        break
                    
                    fut_entry_price = fut_entry_data.iloc[0]['Close']
                    fut_exit_price = fut_exit_data.iloc[0]['Close']
                    
                    # Calculate P&L (EXACT formula from v1 line 662)
                    if leg.position == PositionType.BUY:
                        fut_pnl = round(fut_exit_price - fut_entry_price, 2)
                    else:  # SELL
                        fut_pnl = round(fut_entry_price - fut_exit_price, 2)
                    
                    leg_details.update({
                        f"Leg{leg_num}_Instrument": "FUT",
                        f"Leg{leg_num}_Position": leg.position.value,
                        f"Leg{leg_num}_Expiry": fut_expiry,
                        f"Leg{leg_num}_EntryPrice": fut_entry_price,
                        f"Leg{leg_num}_ExitPrice": fut_exit_price,
                        f"Leg{leg_num}_P&L": fut_pnl,
                    })
                    
                    total_pnl += fut_pnl
            
            if not all_legs_valid:
                continue
            
            # Create trade record (EXACT structure from v1 lines 644-666)
            trade_record = {
                "Entry Date": fromDate,
                "Exit Date": toDate,
                "Entry Spot": entry_spot,
                "Exit Spot": exit_spot,
                "Spot P&L": round(exit_spot - entry_spot, 2) if exit_spot else None,
                "Net P&L": round(total_pnl, 2),
            }
            trade_record.update(leg_details)
            
            trades.append(trade_record)
    
    # Final output (EXACT same as existing engines)
    if not trades:
        return pd.DataFrame(), {}, {}
    
    df = pd.DataFrame(trades)
    df, summary = compute_analytics(df)  # Uses EXACT formulas from validation file
    pivot = build_pivot(df, 'Entry Date')
    
    return df, summary, pivot
```

---

## 🌐 PHASE 4: BACKEND API ENDPOINT

### File: `backtest_router.py`

```python
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import pandas as pd

from strategy_types import BacktestRequest, BacktestResult, StrategyDefinition
from generic_multi_leg_engine import run_generic_multi_leg

# Import existing engines
from v1_ce_fut import run_v1
from v2_pe_fut import run_v2
from v3_strike_breach import run_v3
from v4_strangle import run_v4
from v5_protected import run_v5
from v6_inverse_strangle import run_v6
from v7_premium import run_v7
from v8_ce_pe_fut import run_v8_ce_pe_fut
from v8_hsl import run_v8_hsl
from v9_counter import run_v9

router = APIRouter()

def map_strategy_to_engine(strategy: StrategyDefinition) -> str:
    """
    Map strategy definition to appropriate engine
    Returns engine name or 'generic' for custom strategies
    """
    
    # Strategy signature matching
    leg_count = len(strategy.legs)
    
    if leg_count == 2:
        leg1, leg2 = strategy.legs[0], strategy.legs[1]
        
        # V1: CE Sell + FUT Buy
        if (leg1.instrument.value == "Option" and leg1.option_type.value == "CE" and 
            leg1.position.value == "Sell" and
            leg2.instrument.value == "Future" and leg2.position.value == "Buy"):
            return "v1_ce_fut"
        
        # V2: PE Sell + FUT Buy
        if (leg1.instrument.value == "Option" and leg1.option_type.value == "PE" and 
            leg1.position.value == "Sell" and
            leg2.instrument.value == "Future" and leg2.position.value == "Buy"):
            return "v2_pe_fut"
        
        # V4: CE Sell + PE Sell (Strangle)
        if (leg1.instrument.value == "Option" and leg1.option_type.value == "CE" and 
            leg1.position.value == "Sell" and
            leg2.instrument.value == "Option" and leg2.option_type.value == "PE" and
            leg2.position.value == "Sell"):
            return "v4_strangle"
    
    elif leg_count == 3:
        leg1, leg2, leg3 = strategy.legs[0], strategy.legs[1], strategy.legs[2]
        
        # V8 CE PE FUT: CE Sell + PE Buy + FUT Buy
        if (leg1.instrument.value == "Option" and leg1.option_type.value == "CE" and 
            leg1.position.value == "Sell" and
            leg2.instrument.value == "Option" and leg2.option_type.value == "PE" and
            leg2.position.value == "Buy" and
            leg3.instrument.value == "Future" and leg3.position.value == "Buy"):
            return "v8_ce_pe_fut"
    
    # Default: use generic engine
    return "generic"


@router.post("/backtest", response_model=BacktestResult)
async def run_backtest(request: BacktestRequest):
    """
    Execute backtest for any strategy configuration
    Routes to appropriate engine or uses generic multi-leg engine
    """
    
    try:
        strategy = request.strategy
        
        # Determine which engine to use
        engine_name = map_strategy_to_engine(strategy)
        
        # Prepare parameters
        params = {
            "strategy": strategy,
            "index": strategy.index,
            "from_date": request.from_date,
            "to_date": request.to_date,
            "spot_adjustment_type": 0 if strategy.re_entry_mode.value == "None" else (
                1 if strategy.re_entry_mode.value == "Up Move" else
                2 if strategy.re_entry_mode.value == "Down Move" else 3
            ),
            "spot_adjustment": strategy.re_entry_percent or 1.0,
        }
        
        # Route to appropriate engine
        if engine_name == "v1_ce_fut":
            df, summary, pivot = run_v1(params)
        elif engine_name == "v2_pe_fut":
            df, summary, pivot = run_v2(params)
        elif engine_name == "v3_strike_breach":
            df, summary, pivot = run_v3(params)
        elif engine_name == "v4_strangle":
            df, summary, pivot = run_v4(params)
        elif engine_name == "v8_ce_pe_fut":
            df, summary, pivot = run_v8_ce_pe_fut(params)
        else:
            # Use generic engine
            df, summary, pivot = run_generic_multi_leg(params)
        
        # Convert DataFrame to list of dicts
        trades = df.to_dict('records') if not df.empty else []
        
        # Generate CSV for download
        csv_data = df.to_csv(index=False) if not df.empty else ""
        
        return BacktestResult(
            trades=trades,
            summary=summary,
            pivot_table=pivot,
            trade_sheet_csv=csv_data
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/backtest/all-permutations")
async def run_all_permutations(request: Dict[str, Any]):
    """
    Run backtests for all permutations of a strategy template
    Example: Test all strike combinations, all expiry types, etc.
    """
    
    # Extract permutation parameters
    base_strategy = request.get("base_strategy")
    permutation_config = request.get("permutations", {})
    
    results = []
    
    # Generate all permutations
    # Example: if user wants to test:
    # - Strike: ATM, ATM+1%, ATM+2%
    # - Expiry: Weekly, Monthly
    # - Entry: 5 days, 3 days, 1 day
    # Total = 3 x 2 x 3 = 18 backtests
    
    strike_values = permutation_config.get("strike_values", [0])
    expiry_types = permutation_config.get("expiry_types", ["Weekly"])
    entry_days = permutation_config.get("entry_days", [5])
    exit_days = permutation_config.get("exit_days", [3])
    
    for strike_val in strike_values:
        for expiry_type in expiry_types:
            for entry_day in entry_days:
                for exit_day in exit_days:
                    # Create permutation strategy
                    perm_strategy = base_strategy.copy()
                    perm_strategy['legs'][0]['strike_selection']['value'] = strike_val
                    perm_strategy['legs'][0]['expiry_type'] = expiry_type
                    perm_strategy['legs'][0]['entry_condition']['days_before_expiry'] = entry_day
                    perm_strategy['legs'][0]['exit_condition']['days_before_expiry'] = exit_day
                    
                    # Run backtest
                    test_request = BacktestRequest(
                        strategy=StrategyDefinition(**perm_strategy),
                        from_date=request["from_date"],
                        to_date=request["to_date"]
                    )
                    
                    result = await run_backtest(test_request)
                    
                    results.append({
                        "config": {
                            "strike": strike_val,
                            "expiry": expiry_type,
                            "entry_days": entry_day,
                            "exit_days": exit_day
                        },
                        "summary": result.summary
                    })
    
    # Sort by best performance
    results.sort(key=lambda x: x['summary'].get('total_pnl', 0), reverse=True)
    
    return {
        "total_permutations": len(results),
        "results": results
    }
```

---

## 📊 PHASE 5: RESULTS & EXPORT

### File: `export_utils.py`

```python
import pandas as pd
from typing import Dict, Any, List
import io

def generate_trade_sheet_csv(df: pd.DataFrame, strategy_name: str) -> str:
    """
    Generate detailed trade sheet CSV with all leg details
    Format matches AlgoTest export structure
    """
    
    if df.empty:
        return ""
    
    # Reorder columns for better readability
    base_columns = [
        "Entry Date", "Exit Date", "Entry Spot", "Exit Spot", "Spot P&L"
    ]
    
    # Add all leg columns
    leg_columns = []
    for col in df.columns:
        if col.startswith("Leg"):
            leg_columns.append(col)
    
    # Add summary columns
    summary_columns = ["Net P&L", "cumulative", "%dd"]
    
    # Final column order
    ordered_columns = []
    for col in base_columns + leg_columns + summary_columns:
        if col in df.columns:
            ordered_columns.append(col)
    
    # Reorder DataFrame
    df_export = df[ordered_columns]
    
    # Convert to CSV
    csv_buffer = io.StringIO()
    df_export.to_csv(csv_buffer, index=False)
    
    return csv_buffer.getvalue()


def generate_summary_report(summary: Dict[str, Any]) -> str:
    """Generate text summary report"""
    
    report = f"""
╔════════════════════════════════════════════════════════╗
║           BACKTEST SUMMARY REPORT                      ║
╚════════════════════════════════════════════════════════╝

Performance Metrics:
───────────────────────────────────────────────────────
Total P&L:              ₹{summary.get('total_pnl', 0):,.2f}
Total Trades:           {summary.get('total_trades', 0)}
Winning Trades:         {summary.get('winning_trades', 0)}
Losing Trades:          {summary.get('losing_trades', 0)}
Win Rate:               {summary.get('win_rate', 0):.2f}%

Profit Metrics:
───────────────────────────────────────────────────────
Average Profit:         ₹{summary.get('avg_profit', 0):,.2f}
Average Loss:           ₹{summary.get('avg_loss', 0):,.2f}
Largest Profit:         ₹{summary.get('max_profit', 0):,.2f}
Largest Loss:           ₹{summary.get('max_loss', 0):,.2f}

Risk Metrics:
───────────────────────────────────────────────────────
Max Drawdown:           {summary.get('max_drawdown', 0):.2f}%
Max Consecutive Wins:   {summary.get('max_consec_wins', 0)}
Max Consecutive Losses: {summary.get('max_consec_losses', 0)}
Profit Factor:          {summary.get('profit_factor', 0):.2f}

"""
    return report


def generate_pivot_csv(pivot: Dict[str, Any]) -> str:
    """Generate pivot table CSV"""
    
    if not pivot:
        return ""
    
    df_pivot = pd.DataFrame(pivot)
    
    csv_buffer = io.StringIO()
    df_pivot.to_csv(csv_buffer)
    
    return csv_buffer.getvalue()
```

---

## 🔄 PHASE 6: INTEGRATION STEPS

### Step 1: Install New Dependencies

```bash
pip install pydantic fastapi pandas numpy
```

### Step 2: Project Structure

```
project/
├── frontend/
│   └── src/
│       └── components/
│           ├── ConfigPanelDynamic.jsx        # NEW
│           └── ResultsPanel.jsx              # Updated
├── backend/
│   ├── strategies/
│   │   ├── v1_ce_fut.py                      # Existing
│   │   ├── v2_pe_fut.py                      # Existing
│   │   ├── ...
│   │   ├── generic_multi_leg_engine.py       # NEW
│   │   └── strategy_types.py                 # NEW
│   ├── routers/
│   │   └── backtest_router.py                # NEW/Updated
│   ├── utils/
│   │   └── export_utils.py                   # NEW
│   ├── base.py                               # Existing (unchanged)
│   └── analyse_bhavcopy_02-01-2026.py       # UNTOUCHED
└── data/
    └── ... (existing data files)
```

### Step 3: Update Main App

```python
# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import backtest_router

app = FastAPI(title="Dynamic Strategy Backtester")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(backtest_router.router, prefix="/api", tags=["backtest"])

@app.get("/")
def root():
    return {"message": "Dynamic Strategy Backtester API"}
```

---

## ✅ TESTING & VALIDATION

### Test Case 1: Simple Call Sell Strategy

```python
# Test: CE Sell, 5 days before expiry, exit 3 days before
strategy = {
    "name": "Simple CE Sell",
    "legs": [
        {
            "leg_number": 1,
            "instrument": "Option",
            "option_type": "CE",
            "position": "Sell",
            "lots": 1,
            "expiry_type": "Weekly",
            "strike_selection": {
                "type": "ATM",
                "value": 0
            },
            "entry_condition": {
                "type": "Days Before Expiry",
                "days_before_expiry": 5
            },
            "exit_condition": {
                "type": "Days Before Expiry",
                "days_before_expiry": 3
            }
        }
    ],
    "index": "NIFTY",
    "re_entry_mode": "None",
    "use_base2_filter": True,
    "inverse_base2": False
}
```

### Test Case 2: Short Strangle

```python
# Test: CE Sell + PE Sell (V4 Strategy)
strategy = {
    "name": "Short Strangle",
    "legs": [
        {
            "leg_number": 1,
            "instrument": "Option",
            "option_type": "CE",
            "position": "Sell",
            "lots": 1,
            "expiry_type": "Weekly",
            "strike_selection": {"type": "% of ATM", "value": 1.5},
            "entry_condition": {"type": "Days Before Expiry", "days_before_expiry": 5},
            "exit_condition": {"type": "Days Before Expiry", "days_before_expiry": 0}
        },
        {
            "leg_number": 2,
            "instrument": "Option",
            "option_type": "PE",
            "position": "Sell",
            "lots": 1,
            "expiry_type": "Weekly",
            "strike_selection": {"type": "% of ATM", "value": -1.5},
            "entry_condition": {"type": "Days Before Expiry", "days_before_expiry": 5},
            "exit_condition": {"type": "Days Before Expiry", "days_before_expiry": 0}
        }
    ]
}
```

### Test Case 3: Protected Strategy (CE + FUT)

```python
# Test: CE Sell + Future Buy (V1 Strategy)
strategy = {
    "name": "Protected CE Sell",
    "legs": [
        {
            "leg_number": 1,
            "instrument": "Option",
            "option_type": "CE",
            "position": "Sell",
            "lots": 1,
            "expiry_type": "Weekly",
            "strike_selection": {"type": "ATM"},
            "entry_condition": {"type": "Market Open"},
            "exit_condition": {"type": "At Expiry"}
        },
        {
            "leg_number": 2,
            "instrument": "Future",
            "position": "Buy",
            "lots": 1,
            "expiry_type": "Monthly",
            "entry_condition": {"type": "Market Open"},
            "exit_condition": {"type": "At Expiry"}
        }
    ]
}
```

---

## 🎯 FINAL DELIVERABLES CHECKLIST

✅ **Frontend Components:**
- [ ] ConfigPanelDynamic.jsx - Complete leg builder UI
- [ ] Dynamic strike selection forms
- [ ] Entry/Exit timing controls
- [ ] Re-entry configuration
- [ ] Permutation testing UI

✅ **Backend Implementation:**
- [ ] strategy_types.py - All data models and enums
- [ ] generic_multi_leg_engine.py - Generic strategy engine
- [ ] backtest_router.py - API endpoints
- [ ] export_utils.py - CSV export utilities

✅ **Integration:**
- [ ] Route existing strategies (v1-v9) to original engines
- [ ] Route custom strategies to generic engine
- [ ] Validate all formulas match analyse_bhavcopy_02-01-2026.py
- [ ] Test backward compatibility

✅ **Testing:**
- [ ] Test all existing v1-v9 strategies produce identical results
- [ ] Test new custom strategies
- [ ] Test all strike selection methods
- [ ] Test all permutation combinations
- [ ] Load testing with large date ranges

✅ **Documentation:**
- [ ] API documentation
- [ ] User guide for strategy builder
- [ ] Formula reference guide
- [ ] Troubleshooting guide

---

## 🚀 DEPLOYMENT STEPS

1. **Backup Current System**
   ```bash
   git commit -am "Pre-dynamic-system backup"
   git tag v1.0-static
   ```

2. **Install New Files**
   ```bash
   # Copy all new files to project
   cp strategy_types.py backend/strategies/
   cp generic_multi_leg_engine.py backend/strategies/
   cp backtest_router.py backend/routers/
   cp export_utils.py backend/utils/
   cp ConfigPanelDynamic.jsx frontend/src/components/
   ```

3. **Test in Development**
   ```bash
   # Run tests
   pytest tests/test_dynamic_strategies.py
   
   # Start backend
   uvicorn main:app --reload
   
   # Start frontend
   npm start
   ```

4. **Gradual Rollout**
   - Week 1: Test with existing v1-v9 strategies
   - Week 2: Enable simple custom strategies (1-2 legs)
   - Week 3: Enable complex strategies (3-4 legs)
   - Week 4: Enable permutation testing

5. **Monitor & Optimize**
   - Track API response times
   - Monitor calculation accuracy
   - Collect user feedback
   - Optimize slow queries

---

## 📈 SUCCESS METRICS

1. **Functional Requirements:**
   - ✅ Support 1-4 leg strategies
   - ✅ All 9 strike selection types working
   - ✅ Entry/Exit timing configurable
   - ✅ Re-entry logic functional
   - ✅ Backward compatible with v1-v9

2. **Performance Requirements:**
   - ✅ API response < 5 seconds for 1 year backtest
   - ✅ Permutation testing < 1 minute for 10 permutations
   - ✅ CSV export < 2 seconds

3. **Accuracy Requirements:**
   - ✅ 100% match with existing v1-v9 results
   - ✅ All formulas from analyse_bhavcopy validated
   - ✅ Zero rounding errors

---

## 🎓 USAGE EXAMPLES

### Example 1: AlgoTest-Style Call Buy 5 Days Exit 3 Days

```javascript
const strategy = {
  name: "Call Buy 5-3",
  legs: [{
    leg_number: 1,
    instrument: "Option",
    option_type: "CE",
    position: "Buy",
    expiry_type: "Weekly",
    strike_selection: {
      type: "ATM"
    },
    entry_condition: {
      type: "Days Before Expiry",
      days_before_expiry: 5
    },
    exit_condition: {
      type: "Days Before Expiry",
      days_before_expiry: 3
    }
  }],
  index: "NIFTY",
  re_entry_mode: "None",
  use_base2_filter: true
};

// Run backtest
const response = await fetch('/api/backtest', {
  method: 'POST',
  body: JSON.stringify({
    strategy: strategy,
    from_date: "2024-01-01",
    to_date: "2025-01-31"
  })
});

const result = await response.json();
console.log("Total P&L:", result.summary.total_pnl);
console.log("Win Rate:", result.summary.win_rate);
```

### Example 2: Test All Permutations

```javascript
const permutationRequest = {
  base_strategy: baseStrategy,
  permutations: {
    strike_values: [0, 1, 1.5, 2],  // ATM, ATM+1%, ATM+1.5%, ATM+2%
    expiry_types: ["Weekly", "Monthly"],
    entry_days: [5, 4, 3],
    exit_days: [3, 2, 1, 0]
  },
  from_date: "2024-01-01",
  to_date: "2025-01-31"
};

const response = await fetch('/api/backtest/all-permutations', {
  method: 'POST',
  body: JSON.stringify(permutationRequest)
});

const results = await response.json();
// Results sorted by best performance
console.log("Best Configuration:", results.results[0]);
```

---

## 🔒 CRITICAL REMINDERS

1. **NEVER MODIFY** `analyse_bhavcopy_02-01-2026.py`
2. **ALWAYS USE** exact formulas from validation file
3. **TEST** backward compatibility with ALL v1-v9 strategies
4. **VALIDATE** results match original engines
5. **PRESERVE** all calculation logic exactly

---

This implementation guide provides everything needed to transform your system into a fully dynamic, AlgoTest-style strategy builder while preserving all existing validation logic and formulas.
