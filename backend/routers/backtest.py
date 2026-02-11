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

# Request model
class BacktestRequest(BaseModel):
    strategy_version: str
    expiry_window: str = "weekly_expiry"
    spot_adjustment_type: int = 0
    spot_adjustment: float = 1.0
    call_sell_position: float = 0.0
    put_sell_position: float = 0.0
    put_strike_pct_below: float = 1.0
    protection: bool = False
    protection_pct: float = 1.0
    call_premium: bool = True
    put_premium: bool = True
    premium_multiplier: float = 1.0
    call_sell: bool = True
    put_sell: bool = True
    call_hsl_pct: int = 100
    put_hsl_pct: int = 100
    max_put_spot_pct: float = 0.04
    pct_diff: float = 0.3
    from_date: str
    to_date: str
    index: str = "NIFTY"


# Response models
class TradeRecord(BaseModel):
    entry_date: str
    exit_date: str
    entry_spot: float
    exit_spot: float
    spot_pnl: float
    call_expiry: Optional[str] = None
    call_strike: Optional[float] = None
    call_entry_price: Optional[float] = None
    call_exit_price: Optional[float] = None
    call_pnl: Optional[float] = None
    put_expiry: Optional[str] = None
    put_strike: Optional[float] = None
    put_entry_price: Optional[float] = None
    put_exit_price: Optional[float] = None
    put_pnl: Optional[float] = None
    future_expiry: Optional[str] = None
    future_entry_price: Optional[float] = None
    future_exit_price: Optional[float] = None
    future_pnl: Optional[float] = None
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
    Main backtesting endpoint
    """
    try:
        # Prepare parameters for strategy execution
        params = request.dict()
        
        # Map strategy version to the appropriate function
        strategy_functions = {
            # V1 strategies
            "v1": run_v1_main1,
            "v1_t1": run_v1_main2,
            "v1_t2": run_v1_main3,
            "v1_monthly": run_v1_main4,
            "v1_monthly_t1": run_v1_main5,
            
            # V2 strategies
            "v2": run_v2_main1,
            "v2_t1": run_v2_main2,
            "v2_t2": run_v2_main3,
            "v2_monthly": run_v2_main4,
            "v2_monthly_t1": run_v2_main5,
            
            # V3 strategies
            "v3": run_v3_main1,
            "v3_t1": run_v3_main2,
            "v3_t2": run_v3_main3,
            "v3_monthly": run_v3_main4,
            "v3_monthly_t1": run_v3_main5,
            
            # V4 strategies
            "v4": run_v4_main1,
            "v4_t1": run_v4_main2,
            
            # V5 strategies
            "v5_call": run_v5_call_main1,
            "v5_call_t1": run_v5_call_main2,
            "v5_put": run_v5_put_main1,
            "v5_put_t1": run_v5_put_main2,
            
            # V6 strategies
            "v6": run_v6_main1,
            "v6_t1": run_v6_main2,
            
            # V7 strategies
            "v7": run_v7_main1,
            "v7_t1": run_v7_main2,
            
            # V8 HSL strategies
            "v8_hsl": run_v8_hsl_main1,
            "v8_hsl_t1": run_v8_hsl_main2,
            "v8_hsl_t2": run_v8_hsl_main3,
            "v8_hsl_monthly": run_v8_hsl_main4,
            "v8_hsl_monthly_t1": run_v8_hsl_main5,
            
            # V8 CE PE FUT strategies
            "v8_ce_pe_fut": run_v8_main1,
            "v8_ce_pe_fut_t1": run_v8_main2,
            "v8_ce_pe_fut_t2": run_v8_main3,
            "v8_ce_pe_fut_monthly": run_v8_main4,
            
            # V9 strategies
            "v9": run_v9_main1,
            "v9_t1": run_v9_main2,
            "v9_t2": run_v9_main3,
            "v9_monthly": run_v9_main4,
        }
        
        if request.strategy_version not in strategy_functions:
            raise HTTPException(status_code=400, detail=f"Unknown strategy version: {request.strategy_version}")
        
        # Execute the strategy
        df, summary, pivot = strategy_functions[request.strategy_version](params)
        
        # Prepare response
        trades_list = df.to_dict('records') if not df.empty else []
        
        # Convert date columns to strings
        for trade in trades_list:
            for key, value in trade.items():
                if isinstance(value, pd.Timestamp):
                    trade[key] = value.strftime('%Y-%m-%d')
                elif pd.isna(value):
                    trade[key] = None
        
        # Determine strategy name for metadata
        strategy_names = {
            "v1": "CE Sell + Future Buy",
            "v2": "PE Sell + Future Buy",
            "v4": "Short Strangle",
            "v5_call": "Protected CE Sell",
            "v5_put": "Protected PE Sell",
            "v8_ce_pe_fut": "Hedged Bull (V8)",
            "v9": "Counter-Expiry (V9)",
        }
        
        strategy_name = strategy_names.get(request.strategy_version, request.strategy_version)
        
        response = BacktestResponse(
            status="success",
            meta={
                "strategy": strategy_name,
                "index": request.index,
                "total_trades": len(trades_list),
                "date_range": f"{request.from_date} to {request.to_date}"
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