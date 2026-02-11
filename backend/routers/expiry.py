from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from typing import List
import sys
import os
import pandas as pd

# Add the parent directory to the path to import base
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base import load_expiry

router = APIRouter()

class ExpiryResponse(BaseModel):
    index: str
    type: str
    expiries: List[str]


@router.get("/expiry", response_model=ExpiryResponse)
async def get_expiry_dates(
    index: str = Query(..., description="Index name (e.g., NIFTY, BANKNIFTY)"),
    type: str = Query(..., description="Expiry type (weekly, monthly)")
):
    """
    Get list of expiry dates for a given index and type
    """
    try:
        # Validate type parameter
        if type.lower() not in ["weekly", "monthly"]:
            raise ValueError("Type must be either 'weekly' or 'monthly'")
        
        # Load expiry data
        expiry_df = load_expiry(index.upper(), type.lower())
        
        # Get unique expiry dates and convert to string format
        if type.lower() == "weekly":
            # Combine all expiry columns and get unique values
            all_expiries = []
            all_expiries.extend(expiry_df['Current Expiry'].dt.strftime('%Y-%m-%d').tolist())
            all_expiries.extend(expiry_df['Previous Expiry'].dt.strftime('%Y-%m-%d').tolist())
            all_expiries.extend(expiry_df['Next Expiry'].dt.strftime('%Y-%m-%d').tolist())
            expiries = sorted(list(set(all_expiries)))
        else:  # monthly
            expiries = sorted(list(set(expiry_df['Current Expiry'].dt.strftime('%Y-%m-%d').tolist())))
        
        # Remove duplicates and return
        expiries = sorted(list(set(expiries)))
        
        return ExpiryResponse(
            index=index.upper(),
            type=type.lower(),
            expiries=expiries
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))