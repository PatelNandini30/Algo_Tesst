from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from typing import Dict, Any, List, Optional, Tuple
import sys
import os
import pandas as pd
import numpy as np

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
from engines.generic_multi_leg import run_generic_multi_leg
from engines.generic_algotest_engine import run_algotest_backtest

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
                    trade[key] = value.strftime('%d-%m-%Y')
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
    """
    Execute a strategy using the AlgoTest engine for DTE-based positional strategies
    """
    # DEBUG: Print what params we received
    print(f"\n>>> execute_strategy received params: {list(params.keys())}")
    print(f">>> strategy_type: {params.get('strategy_type')}")
    print(f">>> entry_dte: {params.get('entry_dte')}")
    print(f">>> exit_dte: {params.get('exit_dte')}")
    
    # Check if positional strategy with entry_dte/exit_dte
    is_positional = params.get('strategy_type') == 'positional' or ('entry_dte' in params and 'exit_dte' in params)
    
    print(f">>> is_positional: {is_positional}")
    
    if is_positional:
        print("\n✓ Using AlgoTest engine (DTE-based positional strategy)\n")
        
        # Build AlgoTest params
        entry_dte = params.get('entry_dte', 2)
        exit_dte = params.get('exit_dte', 0)
        expiry_type = params.get('expiry_type', 'WEEKLY')
        
        # Build legs config
        legs_config = []
        for leg in strategy_def.legs:
            # Debug: show actual values
            # Handle enum properly by using .value if available
            if hasattr(leg.instrument, 'value'):
                instr_str = str(leg.instrument.value)
            else:
                instr_str = str(leg.instrument)
            print(f">>> DEBUG: leg.instrument = '{instr_str}'")
            print(f">>> DEBUG: leg.option_type = '{leg.option_type}'")
            
            # Check for options more robustly
            is_option = 'option' in instr_str.lower() or instr_str == 'Option'
            print(f">>> DEBUG: is_option = {is_option}")
            
            # Handle position enum
            if hasattr(leg.position, 'value'):
                position_str = str(leg.position.value).upper()
            else:
                position_str = str(leg.position).upper()
            
            leg_config = {
                'segment': 'OPTIONS' if is_option else 'FUTURES',
                'position': position_str,
                'lots': leg.lots
            }
            
            if is_option:
                # Get option_type - could be 'CE', 'PE', 'Call', 'Put', etc.
                # Handle enum properly by using .value if available
                if hasattr(leg.option_type, 'value'):
                    opt_type = str(leg.option_type.value).upper()
                else:
                    opt_type = str(leg.option_type).upper()
                
                if opt_type in ['CALL', 'CE', 'C']:
                    leg_config['option_type'] = 'CE'
                elif opt_type in ['PUT', 'PE', 'P']:
                    leg_config['option_type'] = 'PE'
                else:
                    leg_config['option_type'] = opt_type
                
                # Strike selection
                strike_sel = leg.strike_selection
                if hasattr(strike_sel, 'type'):
                    strike_type = str(strike_sel.type).upper()
                    if 'ATM' in strike_type:
                        leg_config['strike_selection'] = 'ATM'
                    elif 'OTM' in strike_type:
                        leg_config['strike_selection'] = f'OTM{int(strike_sel.value)}' if strike_sel.value else 'ATM'
                    else:
                        leg_config['strike_selection'] = 'ATM'
                else:
                    leg_config['strike_selection'] = 'ATM'
                leg_config['expiry'] = expiry_type
            
            print(f">>> DEBUG: final leg_config = {leg_config}")
            legs_config.append(leg_config)
        
        algotest_params = {
            'index': params.get('index', 'NIFTY'),
            'from_date': params.get('from_date') or params.get('date_from'),
            'to_date': params.get('to_date') or params.get('date_to'),
            'expiry_type': expiry_type,
            'entry_dte': entry_dte,
            'exit_dte': exit_dte,
            'legs': legs_config
        }
        
        try:
            return run_algotest_backtest(algotest_params)
        except Exception as e:
            print(f"⚠️ AlgoTest engine failed: {e}")
            import traceback
            traceback.print_exc()
    
    # Default to generic_multi_leg
    print("\n✓ Using generic_multi_leg engine\n")
    params["strategy"] = strategy_def
    return run_generic_multi_leg(params)


def convert_numpy_types(obj):
    """
    Recursively convert numpy types to Python native types for JSON serialization
    """
    import numpy as np
    
    if isinstance(obj, dict):
        return {key: convert_numpy_types(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy_types(item) for item in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, pd.Timestamp):
        return obj.strftime('%d-%m-%Y')
    elif pd.isna(obj):
        return None
    else:
        return obj


@router.post("/dynamic-backtest")
async def dynamic_backtest(request: dict):
    """
    Dynamic backtesting endpoint for multi-leg strategies
    """
     # ADD THIS - Log what frontend sends
    print("\n" + "="*70)
    print("REQUEST FROM FRONTEND:")
    import json
    print(json.dumps(request, indent=2, default=str))
    print("="*70 + "\n")
    
    # Check if imports were successful
    if not IMPORT_SUCCESS:
        raise HTTPException(status_code=500, detail="Strategy types import failed - backend not properly configured")
    
    try:
        # Import here to avoid circular imports
        from strategies.strategy_types import (
            InstrumentType, OptionType, PositionType, ExpiryType,
            StrikeSelectionType, Leg, StrikeSelection,
            EntryTimeType, ExitTimeType, EntryCondition, ExitCondition,
            ReEntryMode, StrategyDefinition
        )
        
        # Convert dict to DynamicStrategyRequest-like object
        class DynamicStrategyRequestObj:
            def __init__(self, data):
                self.name = data.get("name", "Dynamic Strategy")
                self.legs = data.get("legs", [])
                self.parameters = data.get("parameters", {})
                self.index = data.get("index", "NIFTY")
                self.date_from = data.get("date_from", data.get("from_date", ""))
                self.date_to = data.get("date_to", data.get("to_date", ""))
                self.expiry_window = data.get("expiry_window", "weekly_expiry")
                self.spot_adjustment_type = data.get("spot_adjustment_type", "None")
                self.spot_adjustment = data.get("spot_adjustment", 1.0)
                # AlgoTest specific fields
                self.entry_dte = data.get("entry_dte", 2)
                self.exit_dte = data.get("exit_dte", 0)
                self.expiry_type = data.get("expiry_type", "WEEKLY")
        
        request_obj = DynamicStrategyRequestObj(request)
        
        # Convert request to StrategyDefinition
        legs = []
        for req_leg in request_obj.legs:
            # TRANSFORMATION: Handle AlgoTest format
            # Check if this is AlgoTest format (has 'segment' field instead of 'instrument')
            if 'segment' in req_leg and 'instrument' not in req_leg:
                print(f"Detected AlgoTest format, transforming...")
                
                # Map segment to instrument
                segment_map = {"options": "Option", "futures": "Future", "option": "Option", "future": "Future"}
                option_type_map = {"call": "CE", "put": "PE", "ce": "CE", "pe": "PE"}
                position_map = {"buy": "Buy", "sell": "Sell"}
                expiry_map = {"weekly": "Weekly", "monthly": "Monthly"}
                
                segment = req_leg.get("segment", "options").lower()
                position = req_leg.get("position", "sell").lower()
                option_type_val = req_leg.get("option_type", "call").lower()
                expiry = req_leg.get("expiry", "weekly").lower()
                lots = int(req_leg.get("lot") or req_leg.get("lots") or 1)
                lots = max(1, lots)  # ensure at least 1
                
                # Transform strike selection
                strike_sel = req_leg.get("strike_selection", {})
                strike_sel_type = strike_sel.get("type", "strike_type").lower()
                
                # Determine strike selection based on type
                backend_strike_type = "ATM"
                backend_strike_value = 0.0
                premium_min = None
                premium_max = None
                
                if strike_sel_type == "strike_type":
                    # Traditional ATM/ITM/OTM selection
                    strike_type_value = strike_sel.get("strike_type", "atm").lower()
                    
                    if strike_type_value.startswith("itm"):
                        try:
                            num = int(strike_type_value.replace("itm", ""))
                            backend_strike_type = "OTM %"
                            backend_strike_value = -num * 1.0
                        except:
                            backend_strike_type = "ATM"
                            backend_strike_value = 0.0
                    elif strike_type_value.startswith("otm"):
                        try:
                            num = int(strike_type_value.replace("otm", ""))
                            backend_strike_type = "OTM %"
                            backend_strike_value = num * 1.0
                        except:
                            backend_strike_type = "ATM"
                            backend_strike_value = 0.0
                    else:
                        backend_strike_type = "ATM"
                        backend_strike_value = 0.0
                
                elif strike_sel_type == "premium_range":
                    # Premium range selection
                    backend_strike_type = "Premium Range"
                    premium_min = float(strike_sel.get("lower", 0))
                    premium_max = float(strike_sel.get("upper", 0))
                    backend_strike_value = 0.0
                
                elif strike_sel_type == "closest_premium":
                    # Closest premium selection
                    backend_strike_type = "Closest Premium"
                    backend_strike_value = float(strike_sel.get("premium", 0))
                
                elif strike_sel_type == "premium_gte":
                    # Premium >= value
                    backend_strike_type = "Premium Range"
                    premium_min = float(strike_sel.get("premium", 0))
                    premium_max = 999999.0  # Large number for upper bound
                    backend_strike_value = 0.0
                
                elif strike_sel_type == "premium_lte":
                    # Premium <= value
                    backend_strike_type = "Premium Range"
                    premium_min = 0.0
                    premium_max = float(strike_sel.get("premium", 0))
                    backend_strike_value = 0.0
                
                elif strike_sel_type == "straddle_width":
                    # Straddle width selection
                    backend_strike_type = "Straddle Width"
                    backend_strike_value = float(strike_sel.get("width", 0))
                
                elif strike_sel_type == "pct_of_atm":
                    # % of ATM selection
                    backend_strike_type = "% of ATM"
                    backend_strike_value = float(strike_sel.get("pct", 0))
                
                elif strike_sel_type == "atm_straddle_premium_pct":
                    # ATM Straddle Premium % selection
                    backend_strike_type = "% of ATM"
                    backend_strike_value = float(strike_sel.get("pct", 0))
                
                else:
                    # Default to ATM
                    backend_strike_type = "ATM"
                    backend_strike_value = 0.0
                
                # Build transformed leg
                strike_selection_dict = {
                    "type": backend_strike_type,
                    "value": backend_strike_value,
                    "spot_adjustment_mode": 0,
                    "spot_adjustment": 0.0
                }
                
                # Add premium range if applicable
                if premium_min is not None:
                    strike_selection_dict["premium_min"] = premium_min
                if premium_max is not None:
                    strike_selection_dict["premium_max"] = premium_max
                
                req_leg = {
                    "instrument": segment_map.get(segment, "Option"),
                    "position": position_map.get(position, "Sell"),
                    "lots": lots,
                    "strike_selection": strike_selection_dict,
                    "entry_condition": {
                        "type": "Days Before Expiry",
                        "days_before_expiry": request_obj.entry_dte if hasattr(request_obj, 'entry_dte') else 2
                    },
                    "exit_condition": {
                        "type": "Days Before Expiry",
                        "days_before_expiry": request_obj.exit_dte if hasattr(request_obj, 'exit_dte') else 0
                    }
                }
                
                if segment_map.get(segment) == "Option":
                    req_leg["option_type"] = option_type_map.get(option_type_val, "CE")
                    req_leg["expiry_type"] = expiry_map.get(expiry, "Weekly")
                
                print(f"Transformed to: {req_leg}")
            
            # Validate instrument type
            instrument_str = req_leg.get("instrument", "")
            if not instrument_str:
                raise HTTPException(status_code=400, detail="Missing 'instrument' field in leg")
            
            try:
                instrument = InstrumentType(instrument_str)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid instrument type: {instrument_str}")
            
            # Validate option type if instrument is Option
            option_type = None
            if instrument == InstrumentType.OPTION:
                option_type_str = req_leg.get("option_type")
                if not option_type_str:
                    raise HTTPException(status_code=400, detail="option_type is required for Option instrument")
                try:
                    option_type = OptionType(option_type_str)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Invalid option type: {option_type_str}")
            
            # Validate position type
            position_str = req_leg.get("position", "")
            if not position_str:
                raise HTTPException(status_code=400, detail="Missing 'position' field in leg")
            
            try:
                position = PositionType(position_str)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid position type: {position_str}")
            
            # Validate expiry type
            expiry_type_str = req_leg.get("expiry_type", "")
            if not expiry_type_str:
                raise HTTPException(status_code=400, detail="Missing 'expiry_type' field in leg")
            
            try:
                expiry_type = ExpiryType(expiry_type_str)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid expiry type: {expiry_type_str}")
            
            # Validate strike selection with flexible mapping
            strike_sel_data = req_leg.get("strike_selection", {})
            if not strike_sel_data:
                raise HTTPException(status_code=400, detail="Missing 'strike_selection' field in leg")
            
            # Map different formats to correct enum values
            strike_type_map = {
                "ATM": "ATM",
                "ClosestPremium": "Closest Premium",
                "PremiumRange": "Premium Range",
                "StraddleWidth": "Straddle Width",
                "%ofATM": "% of ATM",
                "Delta": "Delta",
                "StrikeType": "Strike Type",
                "OTM%": "OTM %",
                "ITM%": "ITM %",
                # Keep original values if they match exactly
                "Closest Premium": "Closest Premium",
                "Premium Range": "Premium Range",
                "Straddle Width": "Straddle Width",
                "% of ATM": "% of ATM",
                "Strike Type": "Strike Type",
                "OTM %": "OTM %",
                "ITM %": "ITM %",
            }
            
            strike_type_raw = strike_sel_data.get("type", "")
            if not strike_type_raw:
                raise HTTPException(status_code=400, detail="Missing 'type' field in strike_selection")
            
            strike_type_mapped = strike_type_map.get(strike_type_raw, strike_type_raw)
            
            try:
                strike_selection_type = StrikeSelectionType(strike_type_mapped)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid strike selection type: {strike_type_raw}. Valid types: ATM, Closest Premium, Premium Range, Straddle Width, % of ATM, Delta, Strike Type, OTM %, ITM %")
            
            strike_selection = StrikeSelection(
                type=strike_selection_type,
                value=strike_sel_data.get("value", 0.0),
                spot_adjustment_mode=strike_sel_data.get("spot_adjustment_mode", 0),
                spot_adjustment=strike_sel_data.get("spot_adjustment", 0.0)
            )
            
            # Validate entry condition with flexible mapping
            entry_cond_data = req_leg.get("entry_condition", {})
            if not entry_cond_data:
                raise HTTPException(status_code=400, detail="Missing 'entry_condition' field in leg")
            
            # Map different formats to correct enum values
            entry_type_map = {
                "DaysBeforeExpiry": "Days Before Expiry",
                "SpecificTime": "Specific Time",
                "MarketOpen": "Market Open",
                "MarketClose": "Market Close",
                # Keep original values if they match exactly
                "Days Before Expiry": "Days Before Expiry",
                "Specific Time": "Specific Time",
                "Market Open": "Market Open",
                "Market Close": "Market Close",
            }
            
            entry_type_raw = entry_cond_data.get("type", "")
            if not entry_type_raw:
                raise HTTPException(status_code=400, detail="Missing 'type' field in entry_condition")
            
            entry_type_mapped = entry_type_map.get(entry_type_raw, entry_type_raw)
            
            try:
                entry_time_type = EntryTimeType(entry_type_mapped)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid entry condition type: {entry_type_raw}. Valid types: Days Before Expiry, Specific Time, Market Open, Market Close")
            
            entry_condition = EntryCondition(
                type=entry_time_type,
                days_before_expiry=entry_cond_data.get("days_before_expiry"),
                specific_time=entry_cond_data.get("specific_time")
            )
            
            # Validate exit condition with flexible mapping
            exit_cond_data = req_leg.get("exit_condition", {})
            if not exit_cond_data:
                raise HTTPException(status_code=400, detail="Missing 'exit_condition' field in leg")
            
            # Map different formats to correct enum values
            exit_type_map = {
                "DaysBeforeExpiry": "Days Before Expiry",
                "SpecificTime": "Specific Time",
                "AtExpiry": "At Expiry",
                "StopLoss": "Stop Loss",
                "Target": "Target",
                # Keep original values if they match exactly
                "Days Before Expiry": "Days Before Expiry",
                "Specific Time": "Specific Time",
                "At Expiry": "At Expiry",
                "Stop Loss": "Stop Loss",
                "Target": "Target",
            }
            
            exit_type_raw = exit_cond_data.get("type", "")
            if not exit_type_raw:
                raise HTTPException(status_code=400, detail="Missing 'type' field in exit_condition")
            
            exit_type_mapped = exit_type_map.get(exit_type_raw, exit_type_raw)
            
            try:
                exit_time_type = ExitTimeType(exit_type_mapped)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid exit condition type: {exit_type_raw}. Valid types: Days Before Expiry, Specific Time, At Expiry, Stop Loss, Target")
            
            exit_condition = ExitCondition(
                type=exit_time_type,
                days_before_expiry=exit_cond_data.get("days_before_expiry"),
                specific_time=exit_cond_data.get("specific_time"),
                stop_loss_percent=exit_cond_data.get("stop_loss_percent"),
                target_percent=exit_cond_data.get("target_percent")
            )
            
            # Create leg with proper parameter names
            leg = Leg(
                leg_number=req_leg.get("leg_number", len(legs) + 1),
                instrument=instrument,  # Correct field name
                option_type=option_type,
                position=position,  # Correct field name
                lots=req_leg.get("lots", 1),
                expiry_type=expiry_type,
                strike_selection=strike_selection,
                entry_condition=entry_condition,
                exit_condition=exit_condition
            )
            legs.append(leg)
        
        strategy_def = StrategyDefinition(
            name=request_obj.name,
            legs=legs,
            parameters=request_obj.parameters
        )
        
        # Convert frontend parameters to engine format
        params = {
            "index": request_obj.index,
            "from_date": request_obj.date_from,
            "to_date": request_obj.date_to,
            "expiry_window": request_obj.expiry_window,
            "spot_adjustment_type": request_obj.spot_adjustment_type,
            "spot_adjustment": request_obj.spot_adjustment,
            # Add AlgoTest-style parameters
            "strategy_type": request.get("strategy_type", "positional"),
            "entry_dte": request.get("entry_dte", 2),
            "exit_dte": request.get("exit_dte", 0),
            "expiry_type": request.get("expiry_type", "WEEKLY"),
        }
        
        # SPOT ADJUSTMENT TYPE MAPPING
        spot_adjustment_mapping = {
            "None": 0,
            "Rises": 1,
            "Falls": 2,
            "RisesOrFalls": 3
        }
        params["spot_adjustment_type"] = spot_adjustment_mapping.get(request_obj.spot_adjustment_type, 0)
        
        # Execute the dynamic strategy
        with open('debug_backtest.log', 'a') as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"DEBUG: About to execute strategy\n")
        df, summary, pivot = execute_strategy(strategy_def, params)
        with open('debug_backtest.log', 'a') as f:
            f.write(f"DEBUG: Strategy executed, df shape: {df.shape if not df.empty else 'empty'}\n")
            f.write(f"DEBUG: df columns: {list(df.columns) if not df.empty else 'N/A'}\n")
        
        # Prepare response with proper column mapping for frontend
        if not df.empty:
            print(f"DEBUG: Original columns: {list(df.columns)}")
            # Rename columns to match frontend expectations (create a copy to avoid modifying original)
            column_mapping = {
                'entry_date': 'Entry Date',
                'exit_date': 'Exit Date',
                'expiry_date': 'Future Expiry',
                'entry_spot': 'Entry Spot',
                'exit_spot': 'Exit Spot',
                'spot_pnl': 'Spot P&L',
                'total_pnl': 'Net P&L',
                'cumulative_pnl': 'Cumulative',
                'exit_reason': 'Exit Reason',
                'entry_dte': 'Entry DTE',
                'exit_dte': 'Exit DTE',
                'leg1_type': 'Leg 1 Type',
                'leg1_strike': 'Leg 1 Strike',
                'leg1_position': 'Leg 1 Position',
                'leg1_entry': 'Leg 1 Entry',
                'leg1_exit': 'Leg 1 Exit',
                'leg1_pnl': 'Leg 1 P&L',
            }
            # Only rename columns that exist in the DataFrame
            existing_columns = {k: v for k, v in column_mapping.items() if k in df.columns}
            print(f"DEBUG: Renaming columns: {existing_columns}")
            df = df.rename(columns=existing_columns)
            
            # Reorder columns to match frontend expectations exactly
            # Frontend expects: Index, Entry Date, Exit Date, Type, Strike, B/S, Qty, Entry Price, Exit Price, P/L
            frontend_columns = [
                'Index', 'Entry Date', 'Exit Date', 'Type', 'Strike', 'B/S', 'Qty',
                'Entry Price', 'Exit Price', 'Entry Spot', 'Exit Spot',
                'Spot P&L', 'Future Expiry', 'Net P&L'
            ]
            # Add any extra columns that exist
            for col in df.columns:
                if col not in frontend_columns:
                    frontend_columns.append(col)
            
            # Only keep columns that exist
            frontend_columns = [c for c in frontend_columns if c in df.columns]
            df = df[frontend_columns]
            
            print(f"DEBUG: After reorder: {list(df.columns)}")
            trades_list = df.to_dict('records')
            print(f"DEBUG: First trade keys: {list(trades_list[0].keys()) if trades_list else 'No trades'}")
        else:
            trades_list = []
        
        # Convert ALL numpy types to Python native types
        trades_list = convert_numpy_types(trades_list)
        
        # Use the summary directly from the engine (already has all analytics calculated)
        # The engine's compute_analytics() already calculated everything correctly
        # Don't recalculate - just use what the engine returned
        
        # Prepare equity curve data for chart (if Cumulative column exists)
        equity_curve = []
        drawdown_data = []
        
        if not df.empty and 'Cumulative' in df.columns:
            for idx, row in df.iterrows():
                equity_curve.append({
                    "date": row['Entry Date'].strftime('%d-%m-%Y') if pd.notna(row['Entry Date']) else '',
                    "cumulative_pnl": float(row['Cumulative']),
                    "peak": float(row.get('Peak', 0))
                })
                
                drawdown_data.append({
                    "date": row['Entry Date'].strftime('%d-%m-%Y') if pd.notna(row['Entry Date']) else '',
                    "drawdown_pct": float(row.get('%DD', 0)),
                    "drawdown_pts": float(row.get('DD', 0))
                })
        
        # Use summary directly from engine - it's already correct!
        # Just convert numpy types to Python native types
        summary_mapped = {
            "total_pnl": summary.get("total_pnl", 0),
            "count": summary.get("count", 0),  # This is the CORRECT count from engine (53)
            "win_pct": summary.get("win_pct", 0),
            "loss_pct": summary.get("loss_pct", 0),
            "avg_win": summary.get("avg_win", 0),
            "avg_loss": summary.get("avg_loss", 0),
            "max_win": summary.get("max_win", 0),
            "max_loss": summary.get("max_loss", 0),
            "avg_profit_per_trade": summary.get("avg_profit_per_trade", 0),
            "expectancy": summary.get("expectancy", 0),
            "reward_to_risk": summary.get("reward_to_risk", 0),
            "cagr_options": summary.get("cagr_options", 0),
            "cagr_spot": summary.get("cagr_spot", 0),
            "max_dd_pct": summary.get("max_dd_pct", 0),
            "max_dd_pts": summary.get("max_dd_pts", 0),
            "car_mdd": summary.get("car_mdd", 0),
            "recovery_factor": summary.get("recovery_factor", 0),
            "max_win_streak": summary.get("max_win_streak", 0),
            "max_loss_streak": summary.get("max_loss_streak", 0),
            "mdd_duration_days": summary.get("mdd_duration_days", 0),
            "mdd_start_date": summary.get("mdd_start_date", ""),
            "mdd_end_date": summary.get("mdd_end_date", ""),
            "mdd_trade_number": summary.get("mdd_trade_number", None),
            "spot_change": summary.get("spot_change", 0),
        }
        summary = convert_numpy_types(summary_mapped)
        pivot = convert_numpy_types(pivot) if pivot else {"headers": [], "rows": []}
        equity_curve = convert_numpy_types(equity_curve)
        drawdown_data = convert_numpy_types(drawdown_data)
        
        # Create response manually to match expected structure
        response_data = {
            "status": "success",
            "meta": {
                "strategy": request_obj.name,
                "index": request_obj.index,
                "total_trades": len(trades_list),
                "date_range": f"{request_obj.date_from} to {request_obj.date_to}",
                "expiry_window": request_obj.expiry_window,
                "parameters": {
                    "spot_adjustment_type": request_obj.spot_adjustment_type,
                    "spot_adjustment": request_obj.spot_adjustment,
                }
            },
            "trades": trades_list,
            "summary": summary,
            "pivot": pivot,
            "equity_curve": equity_curve,
            "drawdown": drawdown_data,
            "log": []
        }
        
        # DEBUG: Log what we're sending to frontend
        print(f"\n{'='*70}")
        print(f"RESPONSE TO FRONTEND:")
        print(f"  trades array length: {len(trades_list)}")
        print(f"  summary.count: {summary.get('count', 'MISSING')}")
        print(f"  meta.total_trades: {response_data['meta']['total_trades']}")
        print(f"  First trade has Cumulative: {'Cumulative' in trades_list[0] if trades_list else 'No trades'}")
        print(f"{'='*70}\n")
            
        return response_data
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_msg = f"Error in dynamic backtest endpoint: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        raise HTTPException(status_code=500, detail=str(e))

# End of dynamic_backtest function


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
async def export_trades_post(request: dict):
    """
    Export trade sheet as CSV for dynamic strategies
    """
    from io import StringIO
    import csv
    
    # Import required classes
    from strategies.strategy_types import (
        InstrumentType, OptionType, PositionType, ExpiryType,
        StrikeSelectionType, Leg, StrikeSelection,
        EntryTimeType, ExitTimeType, EntryCondition, ExitCondition,
        ReEntryMode, StrategyDefinition
    )
    
    # Convert dict to object-like structure
    class RequestObj:
        def __init__(self, data):
            self.name = data.get("name", "Export Strategy")
            self.legs = data.get("legs", [])
            self.parameters = data.get("parameters", {})
            self.index = data.get("index", "NIFTY")
            self.date_from = data.get("date_from", "")
            self.date_to = data.get("date_to", "")
            self.expiry_window = data.get("expiry_window", "weekly_expiry")
            self.spot_adjustment_type = data.get("spot_adjustment_type", "None")
            self.spot_adjustment = data.get("spot_adjustment", 1.0)
            self.quantity = data.get("quantity", 1)
    
    request_obj = RequestObj(request)
    
    # Execute the strategy to get the trade data
    strategy_def = StrategyDefinition(
        name=request_obj.name,
        legs=[],  # We'll populate this below
        parameters=request_obj.parameters
    )
    
    # Transform dynamic legs to backend format
    legs = []
    for req_leg in request_obj.legs:
        # Validate instrument type
        try:
            instrument = InstrumentType(req_leg["instrument"])
        except ValueError:
            instrument_val = req_leg.get("instrument", "unknown")
            raise HTTPException(status_code=400, detail=f"Invalid instrument type: {instrument_val}")
        
        # Validate option type if instrument is OPTION
        option_type = None
        if instrument == InstrumentType.OPTION:
            if req_leg.get("option_type") is None:
                raise HTTPException(status_code=400, detail="option_type is required for OPTION instrument")
            try:
                option_type = OptionType(req_leg.get("option_type"))
            except ValueError:
                opt_type_val = req_leg.get("option_type", "unknown")
                raise HTTPException(status_code=400, detail=f"Invalid option type: {opt_type_val}")
        
        # Validate position type
        try:
            position = PositionType(req_leg["position"])
        except ValueError:
            position_val = req_leg.get("position", "unknown")
            raise HTTPException(status_code=400, detail=f"Invalid position type: {position_val}")
        
        # Validate expiry type
        try:
            expiry_type = ExpiryType(req_leg["expiry_type"])
        except ValueError:
            expiry_type_val = req_leg.get("expiry_type", "unknown")
            raise HTTPException(status_code=400, detail=f"Invalid expiry type: {expiry_type_val}")
        
        # Validate strike selection
        strike_sel_data = req_leg["strike_selection"]
        try:
            strike_selection_type = StrikeSelectionType(strike_sel_data["type"])
        except ValueError:
            strike_type_val = strike_sel_data.get("type", "unknown")
            raise HTTPException(status_code=400, detail=f"Invalid strike selection type: {strike_type_val}")
        
        strike_selection = StrikeSelection(
            type=strike_selection_type,
            value=strike_sel_data.get("value", 0.0),
            spot_adjustment_mode=strike_sel_data.get("spot_adjustment_mode", 0),
            spot_adjustment=strike_sel_data.get("spot_adjustment", 0.0)
        )
        
        leg = Leg(
            instrument=instrument,  # Correct field name
            option_type=option_type,
            position=position,  # Correct field name
            strike_selection=strike_selection,
            quantity=req_leg.get('quantity', 1),
            expiry_type=expiry_type
        )
        legs.append(leg)
    
    strategy_def = StrategyDefinition(
        name=request_obj.name,
        legs=legs,
        parameters=request_obj.parameters
    )
    
    # Convert frontend parameters to engine format
    params = {
        "index": request_obj.index,
        "from_date": request_obj.date_from,
        "to_date": request_obj.date_to,
        "expiry_window": request_obj.expiry_window,
        "spot_adjustment_type": request_obj.spot_adjustment_type,
        "spot_adjustment": request_obj.spot_adjustment,
    }
    
    # SPOT ADJUSTMENT TYPE MAPPING
    spot_adjustment_mapping = {
        "None": 0,
        "Rises": 1,
        "Falls": 2,
        "RisesOrFalls": 3
    }
    params["spot_adjustment_type"] = spot_adjustment_mapping.get(request_obj.spot_adjustment_type, 0)
    
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
async def export_summary_post(request: dict):
    """
    Export summary as CSV for dynamic strategies
    """
    from io import StringIO
    
    # Import required classes
    from strategies.strategy_types import (
        InstrumentType, OptionType, PositionType, ExpiryType,
        StrikeSelectionType, Leg, StrikeSelection,
        EntryTimeType, ExitTimeType, EntryCondition, ExitCondition,
        ReEntryMode, StrategyDefinition
    )
    
    # Convert dict to object-like structure
    class RequestObj:
        def __init__(self, data):
            self.name = data.get("name", "Export Strategy")
            self.legs = data.get("legs", [])
            self.parameters = data.get("parameters", {})
            self.index = data.get("index", "NIFTY")
            self.date_from = data.get("date_from", "")
            self.date_to = data.get("date_to", "")
            self.expiry_window = data.get("expiry_window", "weekly_expiry")
            self.spot_adjustment_type = data.get("spot_adjustment_type", "None")
            self.spot_adjustment = data.get("spot_adjustment", 1.0)
            self.quantity = data.get("quantity", 1)
    
    request_obj = RequestObj(request)
    
    # Execute the strategy to get the summary data
    strategy_def = StrategyDefinition(
        name=request_obj.name,
        legs=[],  # We'll populate this below
        parameters=request_obj.parameters
    )
    
    # Transform dynamic legs to backend format
    legs = []
    for req_leg in request_obj.legs:
        # Validate instrument type
        try:
            instrument = InstrumentType(req_leg["instrument"])
        except ValueError:
            instrument_val = req_leg.get("instrument", "unknown")
            raise HTTPException(status_code=400, detail=f"Invalid instrument type: {instrument_val}")
        
        # Validate option type if instrument is OPTION
        option_type = None
        if instrument == InstrumentType.OPTION:
            if req_leg.get("option_type") is None:
                raise HTTPException(status_code=400, detail="option_type is required for OPTION instrument")
            try:
                option_type = OptionType(req_leg.get("option_type"))
            except ValueError:
                opt_type_val = req_leg.get("option_type", "unknown")
                raise HTTPException(status_code=400, detail=f"Invalid option type: {opt_type_val}")
        
        # Validate position type
        try:
            position = PositionType(req_leg["position"])
        except ValueError:
            position_val = req_leg.get("position", "unknown")
            raise HTTPException(status_code=400, detail=f"Invalid position type: {position_val}")
        
        # Validate expiry type
        try:
            expiry_type = ExpiryType(req_leg["expiry_type"])
        except ValueError:
            expiry_type_val = req_leg.get("expiry_type", "unknown")
            raise HTTPException(status_code=400, detail=f"Invalid expiry type: {expiry_type_val}")
        
        # Validate strike selection
        strike_sel_data = req_leg["strike_selection"]
        try:
            strike_selection_type = StrikeSelectionType(strike_sel_data["type"])
        except ValueError:
            strike_type_val = strike_sel_data.get("type", "unknown")
            raise HTTPException(status_code=400, detail=f"Invalid strike selection type: {strike_type_val}")
        
        strike_selection = StrikeSelection(
            type=strike_selection_type,
            value=strike_sel_data.get("value", 0.0),
            spot_adjustment_mode=strike_sel_data.get("spot_adjustment_mode", 0),
            spot_adjustment=strike_sel_data.get("spot_adjustment", 0.0)
        )
        
        leg = Leg(
            instrument=instrument,  # Correct field name
            option_type=option_type,
            position=position,  # Correct field name
            strike_selection=strike_selection,
            quantity=req_leg.get('quantity', 1),
            expiry_type=expiry_type
        )
        legs.append(leg)
    
    strategy_def = StrategyDefinition(
        name=request_obj.name,
        legs=legs,
        parameters=request_obj.parameters
    )
    
    # Convert frontend parameters to engine format
    params = {
        "index": request_obj.index,
        "from_date": request_obj.date_from,
        "to_date": request_obj.date_to,
        "expiry_window": request_obj.expiry_window,
        "spot_adjustment_type": request_obj.spot_adjustment_type,
        "spot_adjustment": request_obj.spot_adjustment,
    }
    
    # SPOT ADJUSTMENT TYPE MAPPING
    spot_adjustment_mapping = {
        "None": 0,
        "Rises": 1,
        "Falls": 2,
        "RisesOrFalls": 3
    }
    params["spot_adjustment_type"] = spot_adjustment_mapping.get(request_obj.spot_adjustment_type, 0)
    
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


@router.post("/algotest")
async def run_algotest_backtest_endpoint(request: dict):
    """
    NEW ENDPOINT: AlgoTest-style backtest
    
    Request format:
    {
        "index": "NIFTY",
        "from_date": "2024-01-01",
        "to_date": "2024-12-31",
        "expiry_type": "WEEKLY",
        "entry_dte": 2,
        "exit_dte": 0,
        "legs": [
            {
                "segment": "OPTIONS",
                "option_type": "CE",
                "position": "SELL",
                "lots": 1,
                "strike_selection": "OTM2",
                "expiry": "WEEKLY"
            }
        ]
    }
    """
    try:
        from engines.generic_algotest_engine import run_algotest_backtest
        
        # Run backtest
        trades_df, summary, pivot = run_algotest_backtest(request)
        
        # Convert numpy types to Python types for JSON serialization
        def convert_numpy(obj):
            import numpy as np
            if obj is None:
                return None
            elif isinstance(obj, np.integer):
                return int(obj)
            elif isinstance(obj, np.floating):
                return float(obj)
            elif isinstance(obj, np.bool_):
                return bool(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, dict):
                return {str(k): convert_numpy(v) for k, v in obj.items()}
            elif isinstance(obj, (list, tuple)):
                return [convert_numpy(i) for i in obj]
            elif hasattr(obj, 'item'):  # numpy scalar
                return obj.item()
            elif hasattr(obj, 'tolist'):  # numpy array
                return obj.tolist()
            return obj
        
        # Convert to JSON
        trades_json = trades_df.to_dict('records') if not trades_df.empty else []
        trades_json = convert_numpy(trades_json)
        
        summary = convert_numpy(summary)
        pivot = convert_numpy(pivot)
        
        return {
            "status": "success",
            "trades": trades_json,
            "summary": summary,
            "pivot": pivot
        }
    
    except Exception as e:
        import traceback
        import numpy as np
        
        def clean_error(obj):
            if obj is None:
                return None
            elif isinstance(obj, np.integer):
                return int(obj)
            elif isinstance(obj, np.floating):
                return float(obj)
            elif isinstance(obj, np.bool_):
                return bool(obj)
            elif isinstance(obj, dict):
                return {str(k): clean_error(v) for k, v in obj.items()}
            elif isinstance(obj, (list, tuple)):
                return [clean_error(i) for i in obj]
            return str(obj)
        
        return {
            "status": "error",
            "message": clean_error(str(e)),
            "traceback": traceback.format_exc()
        }