from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional, Tuple
import sys
import os
import pandas as pd

# Add the parent directory to the path to import engines
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import strategy functions dynamically to avoid circular imports
import importlib

# Import strategy types for dynamic backtest
# First try direct import
try:
    from strategies.strategy_types import (
        InstrumentType, OptionType, PositionType, ExpiryType,
        StrikeSelectionType, StrategyDefinition, Leg, StrikeSelection,
        EntryTimeType, ExitTimeType, EntryCondition, ExitCondition,
        ReEntryMode
    )
    IMPORT_SUCCESS = True
except ImportError as e:
    print(f"Direct import failed: {e}")
    # Fallback for direct execution
    try:
        strategies_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'strategies')
        if strategies_dir not in sys.path:
            sys.path.insert(0, strategies_dir)
        from strategy_types import (
            InstrumentType, OptionType, PositionType, ExpiryType,
            StrikeSelectionType, StrategyDefinition, Leg, StrikeSelection,
            EntryTimeType, ExitTimeType, EntryCondition, ExitCondition,
            ReEntryMode
        )
        IMPORT_SUCCESS = True
        print("Fallback import successful")
    except ImportError as e2:
        print(f"Fallback import also failed: {e2}")
        IMPORT_SUCCESS = False
        # Define minimal fallback classes for error handling
        class InstrumentType:
            OPTION = "Option"
            FUTURE = "Future"
            @classmethod
            def __call__(cls, value):
                return value
        
        class OptionType:
            CE = "CE"
            PE = "PE"
            @classmethod
            def __call__(cls, value):
                return value
                
        class PositionType:
            BUY = "Buy"
            SELL = "Sell"
            @classmethod
            def __call__(cls, value):
                return value

# Import generic multi-leg engine
try:
    from engines.generic_multi_leg import run_generic_multi_leg
except ImportError:
    # Fallback for direct execution
    engines_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'engines')
    if engines_dir not in sys.path:
        sys.path.insert(0, engines_dir)
    from generic_multi_leg import run_generic_multi_leg

# Dynamically import the strategy engines
v1_module = importlib.import_module('engines.v1_ce_fut')
v2_module = importlib.import_module('engines.v2_pe_fut')
v3_module = importlib.import_module('engines.v3_strike_breach')
v4_module = importlib.import_module('engines.v4_strangle')
v5_module = importlib.import_module('engines.v5_protected')
v6_module = importlib.import_module('engines.v6_inverse_strangle')
v7_module = importlib.import_module('engines.v7_premium')
v8_hsl_module = importlib.import_module('engines.v8_hsl')
v8_ce_pe_fut_module = importlib.import_module('engines.v8_ce_pe_fut')
v9_module = importlib.import_module('engines.v9_counter')

# Get the functions
run_v1_main1 = getattr(v1_module, 'run_v1_main1')
run_v1_main2 = getattr(v1_module, 'run_v1_main2')
run_v1_main3 = getattr(v1_module, 'run_v1_main3')
run_v1_main4 = getattr(v1_module, 'run_v1_main4')
run_v1_main5 = getattr(v1_module, 'run_v1_main5')

run_v2_main1 = getattr(v2_module, 'run_v2_main1')
run_v2_main2 = getattr(v2_module, 'run_v2_main2')
run_v2_main3 = getattr(v2_module, 'run_v2_main3')
run_v2_main4 = getattr(v2_module, 'run_v2_main4')
run_v2_main5 = getattr(v2_module, 'run_v2_main5')

run_v3_main1 = getattr(v3_module, 'run_v3_main1')
run_v3_main2 = getattr(v3_module, 'run_v3_main2')
run_v3_main3 = getattr(v3_module, 'run_v3_main3')
run_v3_main4 = getattr(v3_module, 'run_v3_main4')
run_v3_main5 = getattr(v3_module, 'run_v3_main5')

run_v4_main1 = getattr(v4_module, 'run_v4_main1')
run_v4_main2 = getattr(v4_module, 'run_v4_main2')

run_v5_call_main1 = getattr(v5_module, 'run_v5_call_main1')
run_v5_call_main2 = getattr(v5_module, 'run_v5_call_main2')
run_v5_put_main1 = getattr(v5_module, 'run_v5_put_main1')
run_v5_put_main2 = getattr(v5_module, 'run_v5_put_main2')

run_v6_main1 = getattr(v6_module, 'run_v6_main1')
run_v6_main2 = getattr(v6_module, 'run_v6_main2')

run_v7_main1 = getattr(v7_module, 'run_v7_main1')
run_v7_main2 = getattr(v7_module, 'run_v7_main2')

run_v8_hsl_main1 = getattr(v8_hsl_module, 'run_v8_hsl_main1')
run_v8_hsl_main2 = getattr(v8_hsl_module, 'run_v8_hsl_main2')
run_v8_hsl_main3 = getattr(v8_hsl_module, 'run_v8_hsl_main3')
run_v8_hsl_main4 = getattr(v8_hsl_module, 'run_v8_hsl_main4')
run_v8_hsl_main5 = getattr(v8_hsl_module, 'run_v8_hsl_main5')

run_v8_main1 = getattr(v8_ce_pe_fut_module, 'run_v8_main1')
run_v8_main2 = getattr(v8_ce_pe_fut_module, 'run_v8_main2')
run_v8_main3 = getattr(v8_ce_pe_fut_module, 'run_v8_main3')
run_v8_main4 = getattr(v8_ce_pe_fut_module, 'run_v8_main4')

run_v9_main1 = getattr(v9_module, 'run_v9_main1')
run_v9_main2 = getattr(v9_module, 'run_v9_main2')
run_v9_main3 = getattr(v9_module, 'run_v9_main3')
run_v9_main4 = getattr(v9_module, 'run_v9_main4')

router = APIRouter()

# Request model - AlgoTest style parameter mapping
class BacktestRequest(BaseModel):
    strategy: str  # e.g., "v1_ce_fut", "v2_pe_fut", etc.
    index: str = "NIFTY"
    date_from: str
    date_to: str
    expiry_window: str = "weekly_expiry"  # weekly_expiry, weekly_t1, weekly_t2, monthly_expiry, monthly_t1
    
    # Strike Filters
    call_sell_position: float = 0.0
    put_sell_position: float = 0.0
    put_strike_pct_below: float = 1.0
    max_put_spot_pct: float = 0.04
    premium_multiplier: float = 1.0
    call_premium: bool = True
    put_premium: bool = True
    
    # Leg Selection Filters (Engine-specific validation)
    call_sell: bool = True
    put_sell: bool = True
    call_buy: bool = False
    put_buy: bool = False
    future_buy: bool = True
    
    # Adjustment/Risk Filters
    spot_adjustment_type: str = "None"  # "None", "Rises", "Falls", "RisesOrFalls"
    spot_adjustment: float = 1.0
    call_hsl_pct: int = 100
    put_hsl_pct: int = 100
    pct_diff: float = 0.3
    
    # Protection (for V5 strategies)
    protection: bool = False
    protection_pct: float = 1.0


# Response models - TRADE SHEET COLUMN MASTER STRUCTURE
class TradeRecord(BaseModel):
    # Trade Info
    entry_date: str
    exit_date: str
    entry_spot: float
    exit_spot: float
    
    # Call Leg
    call_expiry: Optional[str] = None
    call_strike: Optional[float] = None
    call_entry_price: Optional[float] = None
    call_exit_price: Optional[float] = None
    call_pnl: Optional[float] = None
    
    # Put Leg
    put_expiry: Optional[str] = None
    put_strike: Optional[float] = None
    put_entry_price: Optional[float] = None
    put_exit_price: Optional[float] = None
    put_pnl: Optional[float] = None
    
    # Future Leg
    future_expiry: Optional[str] = None
    future_entry_price: Optional[float] = None
    future_exit_price: Optional[float] = None
    future_pnl: Optional[float] = None
    
    # Aggregates (NO FRONTEND MODIFICATION)
    spot_pnl: float
    net_pnl: float
    cumulative: float
    dd: float
    pct_dd: float


class SummaryStats(BaseModel):
    total_pnl: float
    count: int
    win_pct: float
    avg_win: float
    avg_loss: float
    expectancy: float
    cagr_options: float
    cagr_spot: float
    max_dd_pct: float
    max_dd_pts: float
    car_mdd: float
    recovery_factor: float
    roi_vs_spot: float


class PivotData(BaseModel):
    headers: List[str]
    rows: List[List[Any]]


class BacktestResponse(BaseModel):
    status: str
    meta: Dict[str, Any]
    trades: List[Dict[str, Any]]
    summary: SummaryStats
    pivot: PivotData
    log: List[Dict[str, Any]] = []


async def run_backtest_logic(request: BacktestRequest):
    """
    Core backtest logic - shared by both endpoints
    STRICT RULES:
    - No logic changes to engines
    - No PnL recomputation
    - No cumulative calculation in frontend
    - Positional only (no intraday)
    - Future expiry = first monthly >= option expiry
    """
    try:
        # Convert frontend parameters to engine format (STRICT MAPPING)
        params = {
            # Core parameters
            "index": request.index,
            "from_date": request.date_from,
            "to_date": request.date_to,
            "expiry_window": request.expiry_window,
            
            # Strike parameters
            "call_sell_position": request.call_sell_position,
            "put_sell_position": request.put_sell_position,
            "put_strike_pct_below": request.put_strike_pct_below,
            "max_put_spot_pct": request.max_put_spot_pct,
            "premium_multiplier": request.premium_multiplier,
            "call_premium": request.call_premium,
            "put_premium": request.put_premium,
            
            # Risk parameters
            "spot_adjustment_type": request.spot_adjustment_type,
            "spot_adjustment": request.spot_adjustment,
            "call_hsl_pct": request.call_hsl_pct,
            "put_hsl_pct": request.put_hsl_pct,
            "pct_diff": request.pct_diff,
            
            # Protection (for V5)
            "protection": request.protection,
            "protection_pct": request.protection_pct,
        }
        
        # Strategy function mapping (STRICT 1:1)
        strategy_functions = {
            # GROUP 1 - Directional Hedge Engines
            "v1_ce_fut": run_v1_main1,      # CE Sell + FUT Buy
            "v1_ce_fut_t1": run_v1_main2,
            "v1_ce_fut_t2": run_v1_main3,
            "v1_ce_fut_monthly": run_v1_main4,
            "v1_ce_fut_monthly_t1": run_v1_main5,
            
            "v2_pe_fut": run_v2_main1,      # PE Sell + FUT Buy
            "v2_pe_fut_t1": run_v2_main2,
            "v2_pe_fut_t2": run_v2_main3,
            "v2_pe_fut_monthly": run_v2_main4,
            "v2_pe_fut_monthly_t1": run_v2_main5,
            
            # GROUP 2 - Neutral Volatility Engines
            "v4_strangle": run_v4_main1,    # Short Strangle
            "v4_strangle_t1": run_v4_main2,
            
            "v6_inverse_strangle": run_v6_main1,  # Inverse Strangle
            "v6_inverse_strangle_t1": run_v6_main2,
            
            # GROUP 3 - Premium Engine
            "v7_premium": run_v7_main1,     # Premium Multiplier Logic
            "v7_premium_t1": run_v7_main2,
            
            # GROUP 4 - Multi-Leg Hedged Engines
            "v8_ce_pe_fut": run_v8_main1,   # CE Sell + PE Buy + FUT Buy
            "v8_ce_pe_fut_t1": run_v8_main2,
            "v8_ce_pe_fut_t2": run_v8_main3,
            "v8_ce_pe_fut_monthly": run_v8_main4,
            
            "v8_hsl": run_v8_hsl_main1,     # CE Sell + FUT Buy with Hard Stop
            "v8_hsl_t1": run_v8_hsl_main2,
            "v8_hsl_t2": run_v8_hsl_main3,
            "v8_hsl_monthly": run_v8_hsl_main4,
            "v8_hsl_monthly_t1": run_v8_hsl_main5,
            
            "v9_counter": run_v9_main1,     # Counter-Based Expiry Engine
            "v9_counter_t1": run_v9_main2,
            "v9_counter_t2": run_v9_main3,
            "v9_counter_monthly": run_v9_main4,
            
            # V3 - Strike Breach Engine
            "v3_strike_breach": run_v3_main1,
            "v3_strike_breach_t1": run_v3_main2,
            "v3_strike_breach_t2": run_v3_main3,
            "v3_strike_breach_monthly": run_v3_main4,
            "v3_strike_breach_monthly_t1": run_v3_main5,
            
            # V5 - Protected Strategies
            "v5_call": run_v5_call_main1,   # Protected CE Sell
            "v5_call_t1": run_v5_call_main2,
            "v5_put": run_v5_put_main1,     # Protected PE Sell
            "v5_put_t1": run_v5_put_main2,
        }
        
        if request.strategy not in strategy_functions:
            raise HTTPException(status_code=400, detail=f"Unknown strategy: {request.strategy}")
        
        # LEG VALIDATION - Ensure engine compatibility
        engine_validation = {
            "v1_ce_fut": {"call_sell": True, "put_sell": False, "put_buy": False, "future_buy": True},
            "v2_pe_fut": {"call_sell": False, "put_sell": True, "put_buy": False, "future_buy": True},
            "v4_strangle": {"call_sell": True, "put_sell": True, "put_buy": False, "future_buy": False},
            "v6_inverse_strangle": {"call_sell": True, "put_sell": True, "put_buy": False, "future_buy": False},
            "v7_premium": {"call_sell": True, "put_sell": True, "put_buy": False, "future_buy": False},
            "v8_ce_pe_fut": {"call_sell": True, "put_sell": False, "put_buy": True, "future_buy": True},
            "v8_hsl": {"call_sell": True, "put_sell": False, "put_buy": False, "future_buy": True},
            "v9_counter": {"call_sell": True, "put_sell": False, "put_buy": True, "future_buy": True},
            "v3_strike_breach": {"call_sell": True, "put_sell": False, "put_buy": False, "future_buy": True},
            "v5_call": {"call_sell": True, "put_sell": False, "put_buy": True, "future_buy": False},
            "v5_put": {"call_sell": False, "put_sell": True, "put_buy": True, "future_buy": False},
        }
        
        if request.strategy in engine_validation:
            required = engine_validation[request.strategy]
            if request.call_sell != required["call_sell"] or \
               request.put_sell != required["put_sell"] or \
               request.put_buy != required["put_buy"] or \
               request.future_buy != required["future_buy"]:
                raise HTTPException(status_code=400, detail=f"Invalid leg combination for {request.strategy}")
        
        # SPOT ADJUSTMENT TYPE MAPPING
        spot_adjustment_mapping = {
            "None": 0,
            "Rises": 1,
            "Falls": 2,
            "RisesOrFalls": 3
        }
        params["spot_adjustment_type"] = spot_adjustment_mapping.get(request.spot_adjustment_type, 0)
        
        # Execute the strategy (NO LOGIC CHANGES)
        df, summary, pivot = strategy_functions[request.strategy](params)
        
        # Prepare response (EXACT ENGINE OUTPUT)
        trades_list = df.to_dict('records') if not df.empty else []
        
        # Convert date columns to strings (POSITIONAL FORMAT)
        for trade in trades_list:
            for key, value in trade.items():
                if isinstance(value, pd.Timestamp):
                    trade[key] = value.strftime('%Y-%m-%d')
                elif pd.isna(value):
                    trade[key] = None
        
        # Strategy name mapping
        strategy_names = {
            "v1_ce_fut": "CE Sell + Future Buy",
            "v2_pe_fut": "PE Sell + Future Buy",
            "v4_strangle": "Short Strangle",
            "v5_call": "Protected CE Sell",
            "v5_put": "Protected PE Sell",
            "v6_inverse_strangle": "Inverse Strangle",
            "v7_premium": "Premium Multiplier",
            "v8_ce_pe_fut": "Hedged Bull (V8)",
            "v8_hsl": "HSL Strategy (V8)",
            "v9_counter": "Counter-Expiry (V9)",
            "v3_strike_breach": "Strike Breach (V3)",
        }
        
        strategy_name = strategy_names.get(request.strategy, request.strategy)
        
        response = BacktestResponse(
            status="success",
            meta={
                "strategy": strategy_name,
                "index": request.index,
                "total_trades": len(trades_list),
                "date_range": f"{request.date_from} to {request.date_to}",
                "expiry_window": request.expiry_window,
                "parameters": {
                    "call_sell_position": request.call_sell_position,
                    "put_strike_pct_below": request.put_strike_pct_below,
                    "spot_adjustment_type": request.spot_adjustment_type,
                    "spot_adjustment": request.spot_adjustment,
                }
            },
            trades=trades_list,
            summary=SummaryStats(**summary) if summary else SummaryStats(
                total_pnl=0, count=0, win_pct=0, avg_win=0, avg_loss=0, 
                expectancy=0, cagr_options=0, cagr_spot=0, max_dd_pct=0, 
                max_dd_pts=0, car_mdd=0, recovery_factor=0, roi_vs_spot=0
            ),
            pivot=PivotData(headers=pivot.get("headers", []), rows=pivot.get("rows", [])),
            log=[]
        )
        
        return response
        
    except Exception as e:
        print(f"Error in backtest endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/backtest", response_model=BacktestResponse)
async def backtest(request: BacktestRequest):
    """
    Main backtesting endpoint - AlgoTest style positional execution
    """
    return await run_backtest_logic(request)


@router.post("/algotest-backtest", response_model=BacktestResponse)
async def algotest_backtest(request: BacktestRequest):
    """
    Alias endpoint for frontend compatibility
    Routes to the same backtest logic as /backtest
    """
    return await run_backtest_logic(request)


# Import strategy functions dynamically to avoid circular imports
import importlib

# Dynamically import the strategy engines
v1_module = importlib.import_module('engines.v1_ce_fut')
v2_module = importlib.import_module('engines.v2_pe_fut')
v3_module = importlib.import_module('engines.v3_strike_breach')
v4_module = importlib.import_module('engines.v4_strangle')
v5_module = importlib.import_module('engines.v5_protected')
v6_module = importlib.import_module('engines.v6_inverse_strangle')
v7_module = importlib.import_module('engines.v7_premium')
v8_hsl_module = importlib.import_module('engines.v8_hsl')
v8_ce_pe_fut_module = importlib.import_module('engines.v8_ce_pe_fut')
v9_module = importlib.import_module('engines.v9_counter')

# Get the functions
run_v1_main1 = getattr(v1_module, 'run_v1_main1')
run_v1_main2 = getattr(v1_module, 'run_v1_main2')
run_v1_main3 = getattr(v1_module, 'run_v1_main3')
run_v1_main4 = getattr(v1_module, 'run_v1_main4')
run_v1_main5 = getattr(v1_module, 'run_v1_main5')

run_v2_main1 = getattr(v2_module, 'run_v2_main1')
run_v2_main2 = getattr(v2_module, 'run_v2_main2')
run_v2_main3 = getattr(v2_module, 'run_v2_main3')
run_v2_main4 = getattr(v2_module, 'run_v2_main4')
run_v2_main5 = getattr(v2_module, 'run_v2_main5')

run_v3_main1 = getattr(v3_module, 'run_v3_main1')
run_v3_main2 = getattr(v3_module, 'run_v3_main2')
run_v3_main3 = getattr(v3_module, 'run_v3_main3')
run_v3_main4 = getattr(v3_module, 'run_v3_main4')
run_v3_main5 = getattr(v3_module, 'run_v3_main5')

run_v4_main1 = getattr(v4_module, 'run_v4_main1')
run_v4_main2 = getattr(v4_module, 'run_v4_main2')

run_v5_call_main1 = getattr(v5_module, 'run_v5_call_main1')
run_v5_call_main2 = getattr(v5_module, 'run_v5_call_main2')
run_v5_put_main1 = getattr(v5_module, 'run_v5_put_main1')
run_v5_put_main2 = getattr(v5_module, 'run_v5_put_main2')

run_v6_main1 = getattr(v6_module, 'run_v6_main1')
run_v6_main2 = getattr(v6_module, 'run_v6_main2')

run_v7_main1 = getattr(v7_module, 'run_v7_main1')
run_v7_main2 = getattr(v7_module, 'run_v7_main2')
run_v7_main3 = getattr(v7_module, 'run_v7_main3')
run_v7_main4 = getattr(v7_module, 'run_v7_main4')

run_v8_hsl_main1 = getattr(v8_hsl_module, 'run_v8_hsl_main1')
run_v8_hsl_main2 = getattr(v8_hsl_module, 'run_v8_hsl_main2')
run_v8_hsl_main3 = getattr(v8_hsl_module, 'run_v8_hsl_main3')
run_v8_hsl_main4 = getattr(v8_hsl_module, 'run_v8_hsl_main4')
run_v8_hsl_main5 = getattr(v8_hsl_module, 'run_v8_hsl_main5')

run_v8_main1 = getattr(v8_ce_pe_fut_module, 'run_v8_main1')
run_v8_main2 = getattr(v8_ce_pe_fut_module, 'run_v8_main2')
run_v8_main3 = getattr(v8_ce_pe_fut_module, 'run_v8_main3')
run_v8_main4 = getattr(v8_ce_pe_fut_module, 'run_v8_main4')

run_v9_main1 = getattr(v9_module, 'run_v9_main1')
run_v9_main2 = getattr(v9_module, 'run_v9_main2')
run_v9_main3 = getattr(v9_module, 'run_v9_main3')
run_v9_main4 = getattr(v9_module, 'run_v9_main4')


class DynamicLegRequest(BaseModel):
    leg_number: int
    instrument: str  # "Option" or "Future"
    option_type: Optional[str] = None  # "CE" or "PE", required for Option
    position: str  # "Buy" or "Sell"
    lots: int = 1
    expiry_type: str = "Weekly"  # "Weekly", "Monthly", "Weekly_T1", etc.
    strike_selection: Dict[str, Any]  # Contains type, value, premium_min, premium_max, etc.
    entry_condition: Dict[str, Any]  # Contains type, days_before_expiry, specific_time
    exit_condition: Dict[str, Any]  # Contains type, days_before_expiry, stop_loss_percent, target_percent


class DynamicStrategyRequest(BaseModel):
    name: str
    legs: List[DynamicLegRequest]
    parameters: Dict[str, Any] = {}
    # Core parameters
    index: str = "NIFTY"
    date_from: str
    date_to: str
    expiry_window: str = "weekly_expiry"
    # Adjustment/Risk Filters
    spot_adjustment_type: str = "None"  # "None", "Rises", "Falls", "RisesOrFalls"
    spot_adjustment: float = 1.0


class ExportResponse(BaseModel):
    content: str
    filename: str


def execute_strategy(strategy_def: StrategyDefinition, params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """Execute a strategy based on its definition"""
    # Add strategy definition to params
    params["strategy"] = strategy_def
    
    # For now, route to generic multi-leg engine
    # In future, can add routing logic to specific engines for compatible strategies
    return run_generic_multi_leg(params)


@router.post("/dynamic-backtest", response_model=BacktestResponse)
async def dynamic_backtest(request: DynamicStrategyRequest):
    """
    Dynamic backtesting endpoint for multi-leg strategies
    """
    # Check if imports were successful
    if not IMPORT_SUCCESS:
        raise HTTPException(status_code=500, detail="Strategy types import failed - backend not properly configured")
    
    try:
        # Convert request to StrategyDefinition
        legs = []
        for req_leg in request.legs:
            # Validate instrument type
            try:
                instrument = InstrumentType(req_leg.instrument)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid instrument type: {req_leg.instrument}")
            
            # Validate option type if instrument is Option
            option_type = None
            if instrument == InstrumentType.OPTION:
                if req_leg.option_type is None:
                    raise HTTPException(status_code=400, detail="option_type is required for Option instrument")
                try:
                    option_type = OptionType(req_leg.option_type)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Invalid option type: {req_leg.option_type}")
            
            # Validate position type
            try:
                position = PositionType(req_leg.position)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid position type: {req_leg.position}")
            
            # Validate expiry type
            try:
                expiry_type = ExpiryType(req_leg.expiry_type)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid expiry type: {req_leg.expiry_type}")
            
            # Validate strike selection
            strike_sel_data = req_leg.strike_selection
            try:
                strike_selection_type = StrikeSelectionType(strike_sel_data["type"])
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid strike selection type: {strike_sel_data['type']}")
            
            strike_selection = StrikeSelection(
                type=strike_selection_type,
                value=strike_sel_data.get("value"),
                premium_min=strike_sel_data.get("premium_min"),
                premium_max=strike_sel_data.get("premium_max"),
                delta_value=strike_sel_data.get("delta_value"),
                strike_type=strike_sel_data.get("strike_type"),
                otm_strikes=strike_sel_data.get("otm_strikes"),
                itm_strikes=strike_sel_data.get("itm_strikes")
            )
            
            # Validate entry condition
            entry_cond_data = req_leg.entry_condition
            try:
                entry_time_type = EntryTimeType(entry_cond_data["type"])
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid entry condition type: {entry_cond_data['type']}")
            
            entry_condition = EntryCondition(
                type=entry_time_type,
                days_before_expiry=entry_cond_data.get("days_before_expiry"),
                specific_time=entry_cond_data.get("specific_time")
            )
            
            # Validate exit condition
            exit_cond_data = req_leg.exit_condition
            try:
                exit_time_type = ExitTimeType(exit_cond_data["type"])
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid exit condition type: {exit_cond_data['type']}")
            
            exit_condition = ExitCondition(
                type=exit_time_type,
                days_before_expiry=exit_cond_data.get("days_before_expiry"),
                specific_time=exit_cond_data.get("specific_time"),
                stop_loss_percent=exit_cond_data.get("stop_loss_percent"),
                target_percent=exit_cond_data.get("target_percent")
            )
            
            leg = Leg(
                leg_number=req_leg.leg_number,
                instrument=instrument,
                option_type=option_type,
                position=position,
                lots=req_leg.lots,
                expiry_type=expiry_type,
                strike_selection=strike_selection,
                entry_condition=entry_condition,
                exit_condition=exit_condition
            )
            legs.append(leg)
        
        if not legs:
            raise HTTPException(status_code=400, detail="Strategy must have at least one leg")
        
        # Extract re-entry and base2 settings from parameters
        re_entry_mode = request.parameters.get("re_entry_mode", "None")
        re_entry_percent = request.parameters.get("re_entry_percent", 1.0)
        use_base2_filter = request.parameters.get("use_base2_filter", True)
        inverse_base2 = request.parameters.get("inverse_base2", False)
        
        strategy_def = StrategyDefinition(
            name=request.name,
            legs=legs,
            index=request.index,
            re_entry_mode=ReEntryMode(re_entry_mode),
            re_entry_percent=re_entry_percent,
            use_base2_filter=use_base2_filter,
            inverse_base2=inverse_base2
        )
        
        # Convert frontend parameters to engine format
        params = {
            "index": request.index,
            "from_date": request.date_from,
            "to_date": request.date_to,
            "expiry_window": request.expiry_window,
            "spot_adjustment_type": request.spot_adjustment_type,
            "spot_adjustment": request.spot_adjustment,
        }
        
        # SPOT ADJUSTMENT TYPE MAPPING
        spot_adjustment_mapping = {
            "None": 0,
            "Rises": 1,
            "Falls": 2,
            "RisesOrFalls": 3
        }
        params["spot_adjustment_type"] = spot_adjustment_mapping.get(request.spot_adjustment_type, 0)
        
        # Execute the dynamic strategy
        df, summary, pivot = execute_strategy(strategy_def, params)
        
        # Prepare response (EXACT ENGINE OUTPUT)
        trades_list = df.to_dict('records') if not df.empty else []
        
        # Convert date columns to strings (POSITIONAL FORMAT)
        for trade in trades_list:
            for key, value in trade.items():
                if isinstance(value, pd.Timestamp):
                    trade[key] = value.strftime('%Y-%m-%d')
                elif pd.isna(value):
                    trade[key] = None
        
        response = BacktestResponse(
            status="success",
            meta={
                "strategy": request.name,
                "index": request.index,
                "total_trades": len(trades_list),
                "date_range": f"{request.date_from} to {request.date_to}",
                "expiry_window": request.expiry_window,
                "parameters": {
                    "spot_adjustment_type": request.spot_adjustment_type,
                    "spot_adjustment": request.spot_adjustment,
                }
            },
            trades=trades_list,
            summary=SummaryStats(**summary) if summary else SummaryStats(
                total_pnl=0, count=0, win_pct=0, avg_win=0, avg_loss=0, 
                expectancy=0, cagr_options=0, cagr_spot=0, max_dd_pct=0, 
                max_dd_pts=0, car_mdd=0, recovery_factor=0, roi_vs_spot=0
            ),
            pivot=PivotData(headers=pivot.get("headers", []), rows=pivot.get("rows", [])),
            log=[]
        )
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in dynamic backtest endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export/trades")
async def export_trades(strategy_id: str):
    """
    Export trade sheet as CSV
    """
    # This is a placeholder - in a real implementation, you would retrieve
    # the trade data based on strategy_id and return it as CSV
    content = "Trade Date,Strategy Name,Leg Type,Strike,Entry Premium,Exit Premium,Quantity,P&L,Running Equity\n"
    content += "2023-01-01,Sample Strategy,CE SELL,18000,200.5,180.2,1,-20.3,10000\n"
    
    response = Response(content=content)
    response.headers["Content-Disposition"] = f"attachment; filename=trade_sheet_{strategy_id}.csv"
    response.headers["Content-Type"] = "text/csv"
    return response


@router.get("/export/summary")
async def export_summary(strategy_id: str):
    """
    Export summary as CSV
    """
    # This is a placeholder - in a real implementation, you would retrieve
    # the summary data based on strategy_id and return it as CSV
    content = "Metric,Value\n"
    content += "Total P&L,5000.00\n"
    content += "CAGR,15.25\n"
    content += "Max Drawdown,-12.34\n"
    content += "CAR/MDD,1.24\n"
    content += "Win Rate,65.43\n"
    content += "Total Trades,156\n"
    
    response = Response(content=content)
    response.headers["Content-Disposition"] = f"attachment; filename=summary_{strategy_id}.csv"
    response.headers["Content-Type"] = "text/csv"
    return response


@router.post("/export/trades")
async def export_trades_post(request: DynamicStrategyRequest):
    """
    Export trade sheet as CSV for dynamic strategies
    """
    from io import StringIO
    import csv
    
    # Execute the strategy to get the trade data
    strategy_def = StrategyDefinition(
        name=request.name,
        legs=[],  # We'll populate this below
        parameters=request.parameters
    )
    
    # Transform dynamic legs to backend format
    legs = []
    for req_leg in request.legs:
        # Validate instrument type
        try:
            instrument = InstrumentType(req_leg.instrument)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid instrument type: {req_leg.instrument}")
        
        # Validate option type if instrument is OPTION
        option_type = None
        if instrument == InstrumentType.OPTION:
            if req_leg.option_type is None:
                raise HTTPException(status_code=400, detail="option_type is required for OPTION instrument")
            try:
                option_type = OptionType(req_leg.option_type)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid option type: {req_leg.option_type}")
        
        # Validate position type
        try:
            position = PositionType(req_leg.position)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid position type: {req_leg.position}")
        
        # Validate expiry type
        try:
            expiry_type = ExpiryType(req_leg.expiry_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid expiry type: {req_leg.expiry_type}")
        
        # Validate strike selection
        strike_sel_data = req_leg.strike_selection
        try:
            strike_selection_type = StrikeSelectionType(strike_sel_data["type"])
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid strike selection type: {strike_sel_data['type']}")
        
        strike_selection = StrikeSelection(
            type=strike_selection_type,
            value=strike_sel_data.get("value", 0.0),
            spot_adjustment_mode=strike_sel_data.get("spot_adjustment_mode", 0),
            spot_adjustment=strike_sel_data.get("spot_adjustment", 0.0)
        )
        
        leg = Leg(
            instrument=instrument,
            option_type=option_type,
            position=position,
            strike_selection=strike_selection,
            quantity=req_leg.quantity,
            expiry_type=expiry_type
        )
        legs.append(leg)
    
    strategy_def = StrategyDefinition(
        name=request.name,
        legs=legs,
        parameters=request.parameters
    )
    
    # Convert frontend parameters to engine format
    params = {
        "index": request.index,
        "from_date": request.date_from,
        "to_date": request.date_to,
        "expiry_window": request.expiry_window,
        "spot_adjustment_type": request.spot_adjustment_type,
        "spot_adjustment": request.spot_adjustment,
    }
    
    # SPOT ADJUSTMENT TYPE MAPPING
    spot_adjustment_mapping = {
        "None": 0,
        "Rises": 1,
        "Falls": 2,
        "RisesOrFalls": 3
    }
    params["spot_adjustment_type"] = spot_adjustment_mapping.get(request.spot_adjustment_type, 0)
    
    # Execute the dynamic strategy
    df, summary, pivot = execute_strategy(strategy_def, params)
    
    # Generate trade sheet using analytics function
    from ..analytics import generate_trade_sheet
    trade_sheet_df = generate_trade_sheet(df)
    
    # Convert to CSV string
    csv_buffer = StringIO()
    trade_sheet_df.to_csv(csv_buffer, index=False)
    csv_content = csv_buffer.getvalue()
    
    response = Response(content=csv_content)
    response.headers["Content-Disposition"] = f"attachment; filename=trade_sheet_{strategy_def.name}.csv"
    response.headers["Content-Type"] = "text/csv"
    return response


@router.post("/export/summary")
async def export_summary_post(request: DynamicStrategyRequest):
    """
    Export summary as CSV for dynamic strategies
    """
    from io import StringIO
    
    # Execute the strategy to get the summary data
    strategy_def = StrategyDefinition(
        name=request.name,
        legs=[],  # We'll populate this below
        parameters=request.parameters
    )
    
    # Transform dynamic legs to backend format
    legs = []
    for req_leg in request.legs:
        # Validate instrument type
        try:
            instrument = InstrumentType(req_leg.instrument)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid instrument type: {req_leg.instrument}")
        
        # Validate option type if instrument is OPTION
        option_type = None
        if instrument == InstrumentType.OPTION:
            if req_leg.option_type is None:
                raise HTTPException(status_code=400, detail="option_type is required for OPTION instrument")
            try:
                option_type = OptionType(req_leg.option_type)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid option type: {req_leg.option_type}")
        
        # Validate position type
        try:
            position = PositionType(req_leg.position)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid position type: {req_leg.position}")
        
        # Validate expiry type
        try:
            expiry_type = ExpiryType(req_leg.expiry_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid expiry type: {req_leg.expiry_type}")
        
        # Validate strike selection
        strike_sel_data = req_leg.strike_selection
        try:
            strike_selection_type = StrikeSelectionType(strike_sel_data["type"])
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid strike selection type: {strike_sel_data['type']}")
        
        strike_selection = StrikeSelection(
            type=strike_selection_type,
            value=strike_sel_data.get("value", 0.0),
            spot_adjustment_mode=strike_sel_data.get("spot_adjustment_mode", 0),
            spot_adjustment=strike_sel_data.get("spot_adjustment", 0.0)
        )
        
        leg = Leg(
            instrument=instrument,
            option_type=option_type,
            position=position,
            strike_selection=strike_selection,
            quantity=req_leg.quantity,
            expiry_type=expiry_type
        )
        legs.append(leg)
    
    strategy_def = StrategyDefinition(
        name=request.name,
        legs=legs,
        parameters=request.parameters
    )
    
    # Convert frontend parameters to engine format
    params = {
        "index": request.index,
        "from_date": request.date_from,
        "to_date": request.date_to,
        "expiry_window": request.expiry_window,
        "spot_adjustment_type": request.spot_adjustment_type,
        "spot_adjustment": request.spot_adjustment,
    }
    
    # SPOT ADJUSTMENT TYPE MAPPING
    spot_adjustment_mapping = {
        "None": 0,
        "Rises": 1,
        "Falls": 2,
        "RisesOrFalls": 3
    }
    params["spot_adjustment_type"] = spot_adjustment_mapping.get(request.spot_adjustment_type, 0)
    
    # Execute the dynamic strategy
    df, summary, pivot = execute_strategy(strategy_def, params)
    
    # Generate summary report using analytics function
    from ..analytics import generate_summary_report
    summary_df = generate_summary_report(df)
    
    # Convert to CSV string
    csv_buffer = StringIO()
    summary_df.to_csv(csv_buffer, index=False)
    csv_content = csv_buffer.getvalue()
    
    response = Response(content=csv_content)
    response.headers["Content-Disposition"] = f"attachment; filename=summary_{strategy_def.name}.csv"
    response.headers["Content-Type"] = "text/csv"
    return response