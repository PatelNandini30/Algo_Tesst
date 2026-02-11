from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
import sys
import os
import pandas as pd

# Add the parent directory to the path to import engines
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


@router.post("/backtest", response_model=BacktestResponse)
async def backtest(request: BacktestRequest):
    """
    Main backtesting endpoint - AlgoTest style positional execution
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


# Additional endpoints will be added here as needed