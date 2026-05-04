from __future__ import annotations

import hashlib
import json
from typing import Literal, Optional

from pydantic import BaseModel, field_validator

SUPPORTED_SYMBOLS = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"})
SLOW_PATH_STRIKE_LIMIT = 5  # |ATM_OFFSET| > 5 → slow path


class ExitCond(BaseModel):
    type: Literal["percent", "points"]
    value: float


class StrikeSelection(BaseModel):
    mode: Literal["ATM", "ATM_OFFSET"]
    value: int = 0


class LegSpec(BaseModel):
    opt_type: Literal["CE", "PE"]
    action: Literal["BUY", "SELL"]
    strike_selection: StrikeSelection
    expiry: Literal["WEEKLY", "MONTHLY", "NEXT_WEEKLY", "NEXT_MONTHLY"]
    quantity: int = 1
    sl: Optional[ExitCond] = None
    target: Optional[ExitCond] = None


class IntradayBacktestRequest(BaseModel):
    symbol: str
    date_from: str   # "YYYY-MM-DD"
    date_to: str
    entry_time: str  # "HH:MM"
    square_off_time: str = "15:15"
    legs: list[LegSpec]

    @field_validator("symbol")
    @classmethod
    def symbol_must_be_supported(cls, v: str) -> str:
        if v not in SUPPORTED_SYMBOLS:
            raise ValueError(f"symbol must be one of {sorted(SUPPORTED_SYMBOLS)}, got '{v}'")
        return v

    def requires_slow_path(self) -> bool:
        return any(
            abs(leg.strike_selection.value) > SLOW_PATH_STRIKE_LIMIT
            for leg in self.legs
        )

    def to_engine_config(self) -> dict:
        return self.model_dump(mode="json")

    def canonical_hash(self) -> str:
        """Stable 16-char hex hash for Redis cache key."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.blake2b(payload.encode(), digest_size=8).hexdigest()
