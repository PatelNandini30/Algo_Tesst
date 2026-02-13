"""
AlgoTest Engine - Complete Backend Integration
Handles all strategy execution and backtest calculations
"""
import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple, List
from datetime import datetime
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from base import (
    get_strike_data,
    load_expiry,
    load_base2,
    load_bhavcopy,
    build_intervals,
    compute_analytics,
    build_pivot,
)


def run_backtest(strategy: str, params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """
    Main backtest execution function
    Routes to appropriate strategy engine based on strategy name
    
    Args:
        strategy: Strategy identifier (e.g., "v1", "v2", "v4", etc.)
        params: Dictionary containing all strategy parameters
        
    Returns:
        Tuple of (trades_df, summary_dict, pivot_dict)
    """
    try:
        # Import strategy engines dynamically
        if strategy.startswith("v1"):
            from engines.v1_ce_fut import run_v1
            df, summary, pivot = run_v1(params)
        elif strategy.startswith("v2"):
            from engines.v2_pe_fut import run_v2
            df, summary, pivot = run_v2(params)
        elif strategy.startswith("v3"):
            from engines.v3_strike_breach import run_v3
            df, summary, pivot = run_v3(params)
        elif strategy.startswith("v4"):
            from engines.v4_strangle import run_v4
            df, summary, pivot = run_v4(params)
        elif strategy.startswith("v5"):
            from engines.v5_protected import run_v5_call, run_v5_put
            if "call" in strategy.lower():
                df, summary, pivot = run_v5_call(params)
            else:
                df, summary, pivot = run_v5_put(params)
        elif strategy.startswith("v6"):
            from engines.v6_inverse_strangle import run_v6
            df, summary, pivot = run_v6(params)
        elif strategy.startswith("v7"):
            from engines.v7_premium import run_v7
            df, summary, pivot = run_v7(params)
        elif strategy.startswith("v8"):
            if "hsl" in strategy.lower():
                from engines.v8_hsl import run_v8_hsl
                df, summary, pivot = run_v8_hsl(params)
            else:
                from engines.v8_ce_pe_fut import run_v8
                df, summary, pivot = run_v8(params)
        elif strategy.startswith("v9"):
            from engines.v9_counter import run_v9
            df, summary, pivot = run_v9(params)
        elif strategy.startswith("v10"):
            from engines.v10_days_before_expiry import run_v10
            df, summary, pivot = run_v10(params)
        else:
            raise ValueError(f"Unknown strategy: {strategy}")
        
        return df, summary, pivot
        
    except Exception as e:
        print(f"Error in run_backtest: {str(e)}")
        raise


def format_response(df: pd.DataFrame, summary: Dict[str, Any], pivot: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format backtest results for API response
    
    Args:
        df: Trades DataFrame
        summary: Summary statistics dictionary
        pivot: Pivot table dictionary
        
    Returns:
        Formatted response dictionary
    """
    # Convert DataFrame to list of dictionaries
    trades_list = []
    if not df.empty:
        for _, row in df.iterrows():
            trade = {}
            for col in df.columns:
                value = row[col]
                # Convert timestamps to strings
                if isinstance(value, pd.Timestamp):
                    trade[col] = value.strftime('%Y-%m-%d')
                # Convert numpy types to Python types
                elif isinstance(value, (np.integer, np.floating)):
                    trade[col] = float(value) if isinstance(value, np.floating) else int(value)
                # Handle NaN values
                elif pd.isna(value):
                    trade[col] = None
                else:
                    trade[col] = value
            trades_list.append(trade)
    
    # Format summary
    formatted_summary = {}
    for key, value in summary.items():
        if isinstance(value, (np.integer, np.floating)):
            formatted_summary[key] = float(value) if isinstance(value, np.floating) else int(value)
        elif pd.isna(value):
            formatted_summary[key] = 0
        else:
            formatted_summary[key] = value
    
    return {
        "trades": trades_list,
        "summary": formatted_summary,
        "pivot": pivot
    }


def validate_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and normalize parameters
    
    Args:
        params: Raw parameters from API request
        
    Returns:
        Validated and normalized parameters
    """
    validated = params.copy()
    
    # Ensure required parameters exist
    required = ["index", "from_date", "to_date"]
    for param in required:
        if param not in validated:
            raise ValueError(f"Missing required parameter: {param}")
    
    # Convert date strings to datetime if needed
    if isinstance(validated["from_date"], str):
        validated["from_date"] = validated["from_date"]
    if isinstance(validated["to_date"], str):
        validated["to_date"] = validated["to_date"]
    
    # Set defaults for optional parameters
    defaults = {
        "call_sell_position": 0.0,
        "put_sell_position": 0.0,
        "spot_adjustment_type": 0,
        "spot_adjustment": 1.0,
        "expiry_window": "weekly_expiry",
        "protection": False,
        "protection_pct": 1.0,
        "premium_multiplier": 1.0,
        "call_premium": True,
        "put_premium": True,
        "put_strike_pct_below": 1.0,
        "max_put_spot_pct": 0.04,
        "call_hsl_pct": 100,
        "put_hsl_pct": 100,
        "pct_diff": 0.3,
        "entry_days_before_expiry": 5,
        "exit_days_before_expiry": 3,
        "option_type": "CE",
        "position_type": "Buy",
        "strike_offset": 0,
    }
    
    for key, default_value in defaults.items():
        if key not in validated:
            validated[key] = default_value
    
    return validated


def get_available_strategies() -> List[Dict[str, Any]]:
    """
    Get list of all available strategies with their metadata
    
    Returns:
        List of strategy information dictionaries
    """
    strategies = [
        {
            "id": "v1",
            "name": "CE Sell + Future Buy (V1)",
            "description": "Sell Call Option and Buy Future",
            "category": "Directional Hedge",
            "parameters": {
                "call_sell_position": {"type": "float", "default": 0.0, "description": "% OTM for call strike"},
                "spot_adjustment_type": {"type": "int", "default": 0, "description": "Adjustment type (0-3)"},
                "spot_adjustment": {"type": "float", "default": 1.0, "description": "Adjustment %"},
            }
        },
        {
            "id": "v2",
            "name": "PE Sell + Future Buy (V2)",
            "description": "Sell Put Option and Buy Future",
            "category": "Directional Hedge",
            "parameters": {
                "put_sell_position": {"type": "float", "default": 0.0, "description": "% OTM for put strike"},
                "spot_adjustment_type": {"type": "int", "default": 0, "description": "Adjustment type (0-3)"},
                "spot_adjustment": {"type": "float", "default": 1.0, "description": "Adjustment %"},
            }
        },
        {
            "id": "v4",
            "name": "Short Strangle (V4)",
            "description": "Sell Call and Put Options (no future)",
            "category": "Neutral Volatility",
            "parameters": {
                "call_sell_position": {"type": "float", "default": 0.0, "description": "% OTM for call strike"},
                "put_sell_position": {"type": "float", "default": 0.0, "description": "% OTM for put strike"},
                "spot_adjustment_type": {"type": "int", "default": 0, "description": "Adjustment type (0-3)"},
                "spot_adjustment": {"type": "float", "default": 1.0, "description": "Adjustment %"},
            }
        },
        {
            "id": "v5_call",
            "name": "Protected CE Sell (V5)",
            "description": "Sell Call with protective Call buy",
            "category": "Protected",
            "parameters": {
                "call_sell_position": {"type": "float", "default": 0.0, "description": "% OTM for call strike"},
                "protection": {"type": "bool", "default": False, "description": "Enable protection"},
                "protection_pct": {"type": "float", "default": 1.0, "description": "% OTM for protection"},
            }
        },
        {
            "id": "v5_put",
            "name": "Protected PE Sell (V5)",
            "description": "Sell Put with protective Put buy",
            "category": "Protected",
            "parameters": {
                "put_sell_position": {"type": "float", "default": 0.0, "description": "% OTM for put strike"},
                "protection": {"type": "bool", "default": False, "description": "Enable protection"},
                "protection_pct": {"type": "float", "default": 1.0, "description": "% OTM for protection"},
            }
        },
        {
            "id": "v7",
            "name": "Premium-Based Strangle (V7)",
            "description": "Sell options based on premium targets",
            "category": "Premium",
            "parameters": {
                "premium_multiplier": {"type": "float", "default": 1.0, "description": "Premium multiplier"},
                "call_premium": {"type": "bool", "default": True, "description": "Use call premium"},
                "put_premium": {"type": "bool", "default": True, "description": "Use put premium"},
            }
        },
        {
            "id": "v8",
            "name": "Hedged Bull (V8)",
            "description": "CE Sell + PE Buy + Future Buy",
            "category": "Multi-Leg Hedged",
            "parameters": {
                "call_sell_position": {"type": "float", "default": 0.0, "description": "% OTM for call strike"},
                "put_strike_pct_below": {"type": "float", "default": 1.0, "description": "% below call for put"},
            }
        },
        {
            "id": "v9",
            "name": "Counter-Expiry (V9)",
            "description": "CE Sell + PE Buy with dynamic put expiry",
            "category": "Multi-Leg Hedged",
            "parameters": {
                "call_sell_position": {"type": "float", "default": 0.0, "description": "% OTM for call strike"},
                "put_strike_pct_below": {"type": "float", "default": 1.0, "description": "% below call for put"},
                "max_put_spot_pct": {"type": "float", "default": 0.04, "description": "Max put strike % below spot"},
            }
        },
        {
            "id": "v10",
            "name": "Days Before Expiry (V10)",
            "description": "Fully dynamic entry/exit based on days before expiry",
            "category": "Time-Based",
            "parameters": {
                "entry_days_before_expiry": {"type": "int", "default": 5, "description": "Days before expiry to enter"},
                "exit_days_before_expiry": {"type": "int", "default": 3, "description": "Days before expiry to exit"},
                "option_type": {"type": "string", "default": "CE", "description": "CE or PE"},
                "position_type": {"type": "string", "default": "Buy", "description": "Buy or Sell"},
                "strike_offset": {"type": "int", "default": 0, "description": "0=ATM, +1=1 strike OTM, -1=1 strike ITM"},
                "expiry_type": {"type": "string", "default": "weekly", "description": "weekly or monthly"},
            }
        },
    ]
    
    return strategies
