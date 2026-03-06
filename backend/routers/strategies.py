from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Dict, Any
import sys
import os
import pandas as pd
from database import get_data_source, engine as db_engine
from repositories.market_data_repository import MarketDataRepository

router = APIRouter()
_repo = MarketDataRepository(db_engine)

class StrategyOptions(BaseModel):
    instrument_types: List[str]
    option_types: List[str]
    position_types: List[str]
    expiry_types: List[str]
    strike_selection_types: List[str]
    strike_types: List[str]
    entry_time_types: List[str]
    exit_time_types: List[str]
    re_entry_modes: List[str]
    super_trend_configs: List[str]
    indices: List[str]


class DateRangeResponse(BaseModel):
    min_date: str
    max_date: str


@router.get("/strategies", response_model=StrategyOptions)
async def get_strategy_options():
    """
    Get available strategy configuration options (dynamic, not hardcoded).
    Frontend uses these to build custom multi-leg strategies.
    """
    return StrategyOptions(
        instrument_types=["Option", "Future", "Spot"],
        option_types=["CE", "PE"],
        position_types=["Buy", "Sell"],
        expiry_types=["Weekly", "Monthly", "Weekly_T1", "Weekly_T2", "Monthly_T1"],
        strike_selection_types=[
            "ATM", "Closest Premium", "Premium Range", "PREMIUM_GTE", 
            "PREMIUM_LTE", "Straddle Width", "% of ATM", "Delta", 
            "Strike Type", "OTM %", "ITM %"
        ],
        strike_types=["ATM", "ITM", "OTM"],
        entry_time_types=["Days Before Expiry", "Specific Time", "Market Open", "Market Close"],
        exit_time_types=["Days Before Expiry", "Specific Time", "At Expiry", "Stop Loss", "Target"],
        re_entry_modes=["None", "Up Move", "Down Move", "Either Move"],
        super_trend_configs=["None", "5x1", "5x2"],
        indices=["NIFTY", "BANKNIFTY", "FINNIFTY"]
    )


@router.get("/data/dates", response_model=DateRangeResponse)
async def get_date_range(index: str = "NIFTY"):
    """
    Get min/max available dates for a given index
    """
    from datetime import datetime
    
    if get_data_source() == "postgres":
        try:
            dr = _repo.get_available_date_range()
            if dr["min_date"] and dr["max_date"]:
                return DateRangeResponse(
                    min_date=pd.to_datetime(dr["min_date"]).strftime('%Y-%m-%d'),
                    max_date=pd.to_datetime(dr["max_date"]).strftime('%Y-%m-%d')
                )
        except Exception:
            pass

    csv_dir = os.path.join(os.getcwd(), 'cleaned_csvs')
    if not os.path.exists(csv_dir):
        return DateRangeResponse(min_date="2019-01-01", max_date="2026-01-01")
    
    csv_files = [f for f in os.listdir(csv_dir) if f.endswith('.csv')]
    dates = []
    
    for filename in csv_files:
        date_str = filename.replace('.csv', '')
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            dates.append(date_obj)
        except ValueError:
            continue
    
    if not dates:
        return DateRangeResponse(min_date="2019-01-01", max_date="2026-01-01")
    
    min_date = min(dates).strftime('%Y-%m-%d')
    max_date = max(dates).strftime('%Y-%m-%d')
    
    return DateRangeResponse(min_date=min_date, max_date=max_date)
