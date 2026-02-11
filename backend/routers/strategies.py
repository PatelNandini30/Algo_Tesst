from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Dict, Any
import sys
import os

router = APIRouter()

class StrategyInfo(BaseModel):
    name: str
    version: str
    description: str
    parameters: Dict[str, Any]
    defaults: Dict[str, Any]


class StrategiesResponse(BaseModel):
    strategies: List[StrategyInfo]


@router.get("/strategies", response_model=StrategiesResponse)
async def get_strategies():
    """
    Get list of all supported strategies with their parameters and defaults
    """
    strategies = [
        StrategyInfo(
            name="CE Sell + Future Buy (V1)",
            version="v1",
            description="Sell Call Option and Buy Future",
            parameters={
                "call_sell_position": "Percentage OTM for call strike",
                "spot_adjustment_type": "Type of spot adjustment (0=none, 1=rise, 2=fall, 3=both)",
                "spot_adjustment": "Adjustment percentage threshold",
                "expiry_window": "Expiry window type"
            },
            defaults={
                "call_sell_position": 0.0,
                "spot_adjustment_type": 0,
                "spot_adjustment": 1.0,
                "expiry_window": "weekly_expiry"
            }
        ),
        StrategyInfo(
            name="PE Sell + Future Buy (V2)",
            version="v2",
            description="Sell Put Option and Buy Future",
            parameters={
                "put_sell_position": "Percentage OTM for put strike",
                "spot_adjustment_type": "Type of spot adjustment (0=none, 1=rise, 2=fall, 3=both)",
                "spot_adjustment": "Adjustment percentage threshold",
                "expiry_window": "Expiry window type"
            },
            defaults={
                "put_sell_position": 0.0,
                "spot_adjustment_type": 0,
                "spot_adjustment": 1.0,
                "expiry_window": "weekly_expiry"
            }
        ),
        StrategyInfo(
            name="Short Strangle (V4)",
            version="v4",
            description="Sell Call and Put Options (no future)",
            parameters={
                "call_sell_position": "Percentage OTM for call strike",
                "put_sell_position": "Percentage OTM for put strike",
                "spot_adjustment_type": "Type of spot adjustment (0=none, 1=rise, 2=fall, 3=both)",
                "spot_adjustment": "Adjustment percentage threshold"
            },
            defaults={
                "call_sell_position": 0.0,
                "put_sell_position": 0.0,
                "spot_adjustment_type": 0,
                "spot_adjustment": 1.0
            }
        ),
        StrategyInfo(
            name="Protected CE Sell (V5 Call)",
            version="v5_call",
            description="Sell Call with protective Call buy",
            parameters={
                "call_sell_position": "Percentage OTM for call strike",
                "protection": "Enable protective leg",
                "protection_pct": "Percentage OTM for protective leg",
                "spot_adjustment_type": "Type of spot adjustment (0=none, 1=rise, 2=fall, 3=both)",
                "spot_adjustment": "Adjustment percentage threshold",
                "expiry_window": "Expiry window type"
            },
            defaults={
                "call_sell_position": 0.0,
                "protection": False,
                "protection_pct": 1.0,
                "spot_adjustment_type": 0,
                "spot_adjustment": 1.0,
                "expiry_window": "weekly_expiry"
            }
        ),
        StrategyInfo(
            name="Protected PE Sell (V5 Put)",
            version="v5_put",
            description="Sell Put with protective Put buy",
            parameters={
                "put_sell_position": "Percentage OTM for put strike",
                "protection": "Enable protective leg",
                "protection_pct": "Percentage OTM for protective leg",
                "spot_adjustment_type": "Type of spot adjustment (0=none, 1=rise, 2=fall, 3=both)",
                "spot_adjustment": "Adjustment percentage threshold",
                "expiry_window": "Expiry window type"
            },
            defaults={
                "put_sell_position": 0.0,
                "protection": False,
                "protection_pct": 1.0,
                "spot_adjustment_type": 0,
                "spot_adjustment": 1.0,
                "expiry_window": "weekly_expiry"
            }
        ),
        StrategyInfo(
            name="Premium-Based Strangle (V7)",
            version="v7",
            description="Sell options based on premium targets",
            parameters={
                "call_premium": "Use ATM call premium for target",
                "put_premium": "Use ATM put premium for target",
                "premium_multiplier": "Multiplier for premium target",
                "call_sell": "Include call sell leg",
                "put_sell": "Include put sell leg",
                "spot_adjustment_type": "Type of spot adjustment (0=none, 1=rise, 2=fall, 3=both)",
                "spot_adjustment": "Adjustment percentage threshold"
            },
            defaults={
                "call_premium": True,
                "put_premium": True,
                "premium_multiplier": 1.0,
                "call_sell": True,
                "put_sell": True,
                "spot_adjustment_type": 0,
                "spot_adjustment": 1.0
            }
        ),
        StrategyInfo(
            name="Hedged Bull (V8)",
            version="v8_ce_pe_fut",
            description="CE Sell + PE Buy + Future Buy",
            parameters={
                "call_sell_position": "Percentage OTM for call strike",
                "put_strike_pct_below": "Percentage below call for put strike",
                "spot_adjustment_type": "Type of spot adjustment (0=none, 1=rise, 2=fall, 3=both)",
                "spot_adjustment": "Adjustment percentage threshold",
                "expiry_window": "Expiry window type"
            },
            defaults={
                "call_sell_position": 0.0,
                "put_strike_pct_below": 1.0,
                "spot_adjustment_type": 0,
                "spot_adjustment": 1.0,
                "expiry_window": "weekly_expiry"
            }
        ),
        StrategyInfo(
            name="Counter-Expiry (V9)",
            version="v9",
            description="CE Sell + PE Buy with dynamic put expiry",
            parameters={
                "call_sell_position": "Percentage OTM for call strike",
                "put_strike_pct_below": "Percentage below call for put strike",
                "max_put_spot_pct": "Maximum put strike percentage below spot",
                "spot_adjustment_type": "Type of spot adjustment (0=none, 1=rise, 2=fall, 3=both)",
                "spot_adjustment": "Adjustment percentage threshold"
            },
            defaults={
                "call_sell_position": 0.0,
                "put_strike_pct_below": 1.0,
                "max_put_spot_pct": 0.04,
                "spot_adjustment_type": 0,
                "spot_adjustment": 1.0
            }
        )
    ]
    
    return StrategiesResponse(strategies=strategies)


@router.get("/data/dates")
async def get_date_range(index: str = "NIFTY"):
    """
    Get min/max available dates for a given index
    """
    import pandas as pd
    from datetime import datetime
    import os
    from base import get_strike_data
    
    try:
        # Get all CSV files in cleaned_csvs directory
        csv_dir = os.path.join(os.getcwd(), 'cleaned_csvs')
        if not os.path.exists(csv_dir):
            return {"min_date": None, "max_date": None}
        
        # Get all CSV files and extract dates from filenames
        csv_files = [f for f in os.listdir(csv_dir) if f.endswith('.csv')]
        dates = []
        
        for filename in csv_files:
            date_str = filename.replace('.csv', '')
            try:
                # Validate date format
                date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                dates.append(date_obj)
            except ValueError:
                # Skip invalid date formats
                continue
        
        if not dates:
            return {"min_date": None, "max_date": None}
        
        min_date = min(dates).strftime('%Y-%m-%d')
        max_date = max(dates).strftime('%Y-%m-%d')
        
        return {"min_date": min_date, "max_date": max_date}
        
    except Exception as e:
        return {"min_date": None, "max_date": None}