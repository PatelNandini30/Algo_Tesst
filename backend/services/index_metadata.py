"""Index metadata shared by AlgoTest request validation and calculations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class IndexConfig:
    symbol: str
    enabled: bool
    expiry_bases: tuple[str, ...]
    strike_interval: int


INDEX_CONFIGS = {
    "NIFTY": IndexConfig("NIFTY", True, ("WEEKLY", "MONTHLY", "NEXT_WEEKLY", "NEXT_MONTHLY"), 50),
    "BANKNIFTY": IndexConfig("BANKNIFTY", True, ("MONTHLY", "NEXT_MONTHLY"), 100),
    "MIDCPNIFTY": IndexConfig("MIDCPNIFTY", True, ("WEEKLY", "MONTHLY", "NEXT_WEEKLY", "NEXT_MONTHLY"), 25),
    "SENSEX": IndexConfig("SENSEX", False, ("WEEKLY", "MONTHLY", "NEXT_WEEKLY", "NEXT_MONTHLY"), 100),
}

WEEKLY_EXPIRIES = {"WEEKLY", "NEXT_WEEKLY", "WEEKLY_T1"}


def normalize_index(symbol: str | None) -> str:
    return str(symbol or "NIFTY").strip().upper()


def normalize_expiry(expiry: str | None) -> str:
    return str(expiry or "WEEKLY").strip().upper()


def get_index_config(symbol: str | None) -> IndexConfig | None:
    return INDEX_CONFIGS.get(normalize_index(symbol))


def _iter_leg_expiries(legs: Iterable[dict]) -> Iterable[tuple[int, str]]:
    for idx, leg in enumerate(legs or [], start=1):
        if not isinstance(leg, dict):
            continue
        segment = str(leg.get("segment") or "").upper()
        if segment == "FUTURES":
            expiry = leg.get("expiry") or "MONTHLY"
        else:
            expiry = leg.get("expiry") or "WEEKLY"
        yield idx, normalize_expiry(expiry)
        for key in ("reEntryOnSL", "reEntryOnTarget"):
            reentry = leg.get(key) or {}
            if not isinstance(reentry, dict):
                continue
            lazy_leg = reentry.get("lazyLegConfig")
            if isinstance(lazy_leg, dict):
                lazy_expiry = lazy_leg.get("expiry") or "WEEKLY"
                yield idx, normalize_expiry(lazy_expiry)


def validate_index_payload(payload: dict) -> None:
    symbol = normalize_index((payload or {}).get("index"))
    config = get_index_config(symbol)
    if config is None:
        raise ValueError(f"{symbol} is not configured for AlgoTest backtesting.")
    if not config.enabled:
        raise ValueError(f"{symbol} backtest data is not available. Import option quotes and expiry calendar before running this index.")

    expiry_type = normalize_expiry((payload or {}).get("expiry_type"))
    monthly_only = not any(expiry.startswith("WEEKLY") or expiry == "NEXT_WEEKLY" for expiry in config.expiry_bases)
    if monthly_only and expiry_type in WEEKLY_EXPIRIES:
        raise ValueError(f"{symbol} is monthly-only. Weekly expiry is not available.")
    if expiry_type not in config.expiry_bases:
        allowed = ", ".join(config.expiry_bases)
        raise ValueError(f"{symbol} supports {allowed} expiry only; received {expiry_type}.")

    if monthly_only:
        for leg_no, expiry in _iter_leg_expiries((payload or {}).get("legs") or []):
            if expiry in WEEKLY_EXPIRIES:
                raise ValueError(f"{symbol} is monthly-only. Leg {leg_no} cannot use {expiry} expiry.")


def get_lot_size_for_index(symbol: str | None, entry_date) -> int:
    index = normalize_index(symbol)
    d = pd.Timestamp(entry_date)

    if index == "NIFTY":
        return 65

    if index == "BANKNIFTY":
        if d < pd.Timestamp("2010-10-01"):
            return 50
        if d < pd.Timestamp("2015-10-29"):
            return 25
        if d < pd.Timestamp("2019-11-01"):
            return 20
        if d < pd.Timestamp("2023-07-01"):
            return 25
        if d < pd.Timestamp("2024-11-20"):
            return 15
        if d < pd.Timestamp("2025-07-01"):
            return 30
        if d < pd.Timestamp("2026-01-01"):
            return 35
        return 30

    if index == "MIDCPNIFTY":
        if d < pd.Timestamp("2024-11-20"):
            return 75
        if d < pd.Timestamp("2025-07-01"):
            return 120
        if d < pd.Timestamp("2026-01-01"):
            return 140
        return 120

    if index == "FINNIFTY":
        return 65 if d < pd.Timestamp("2026-01-01") else 60
    if index == "SENSEX":
        return 20
    if index == "BANKEX":
        return 30
    return 1
