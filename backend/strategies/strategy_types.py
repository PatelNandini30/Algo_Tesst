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
    spot_adjustment_mode: Optional[int] = 0  # Spot adjustment mode (0=None, 1=Rises, 2=Falls, 3=RisesOrFalls)
    spot_adjustment: Optional[float] = 0.0  # Spot adjustment percentage

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
    legs: List[Leg] = Field(min_items=1, max_items=10)
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