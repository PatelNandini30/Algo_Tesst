"""Pure: compute ATM strike per minute from spot close and a known strike list."""
from typing import Iterable, List
import polars as pl

from backend.services.intraday_snapshot.format import MINUTES_PER_DAY


def atm_per_minute(
    spot_df: pl.DataFrame,
    strikes_x100: Iterable[int],
    *,
    expected_minutes: int = MINUTES_PER_DAY,
) -> List[int]:
    """Return list of length `expected_minutes` of ATM strikes (×100).
    Tie-break: lower strike wins (`abs(diff)` then `strike_x100`)."""
    strikes = sorted(set(int(s) for s in strikes_x100))
    if not strikes:
        raise ValueError("strikes_x100 must be non-empty")

    closes = spot_df.sort(by="ts_min")["close_x100"].to_list()
    out: List[int] = []
    for c in closes:
        best = min(strikes, key=lambda s: (abs(s - c), s))
        out.append(best)

    # Pad short sessions with the last-observed ATM
    if not out:
        out.append(strikes[len(strikes) // 2])
    while len(out) < expected_minutes:
        out.append(out[-1])
    return out[:expected_minutes]
