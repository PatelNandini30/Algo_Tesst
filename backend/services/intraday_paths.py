"""Pure path arithmetic for intraday storage. No I/O."""
from datetime import date

SUPPORTED_SYMBOLS = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"})


def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if s not in SUPPORTED_SYMBOLS:
        raise ValueError(
            f"Unsupported symbol {symbol!r}; expected one of {sorted(SUPPORTED_SYMBOLS)}"
        )
    return s


def symbol_dir(root: str, symbol: str) -> str:
    return f"{root.rstrip('/')}/{_normalize_symbol(symbol)}"


def options_parquet_path(root: str, symbol: str, trade_date: date) -> str:
    return (
        f"{symbol_dir(root, symbol)}/options/"
        f"year={trade_date.year:04d}/month={trade_date.month:02d}/options.parquet"
    )


def spot_parquet_path(root: str, symbol: str, year: int) -> str:
    return f"{symbol_dir(root, symbol)}/spot/year={year:04d}/spot.parquet"


def snapshot_path(root: str, symbol: str, trade_date: date) -> str:
    return f"{symbol_dir(root, symbol)}/snapshots/{trade_date.isoformat()}.arrow"


def expiry_dim_path(root: str, symbol: str) -> str:
    return f"{symbol_dir(root, symbol)}/expiries.json"
