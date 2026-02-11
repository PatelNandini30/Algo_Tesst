from fastapi import FastAPI
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

app = FastAPI()

# Minimal backtest request model to test
class BacktestRequest(BaseModel):
    strategy: str
    index: str = "NIFTY"
    date_from: str
    date_to: str
    expiry_window: str = "weekly_expiry"
    call_sell_position: float = 0.0
    put_sell_position: float = 0.0
    put_strike_pct_below: float = 1.0
    max_put_spot_pct: float = 0.04
    premium_multiplier: float = 1.0
    call_premium: bool = True
    put_premium: bool = True
    call_sell: bool = True
    put_sell: bool = True
    put_buy: bool = False
    future_buy: bool = True
    spot_adjustment_type: str = "None"
    spot_adjustment: float = 1.0
    call_hsl_pct: int = 100
    put_hsl_pct: int = 100
    pct_diff: float = 0.3
    protection: bool = False
    protection_pct: float = 1.0

@app.post("/api/backtest")
async def backtest(request: BacktestRequest):
    print(f"Received request: {request.strategy}")
    return {"status": "success", "message": f"Valid request received for {request.strategy}"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)