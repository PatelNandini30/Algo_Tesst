"""
Named strategy archetypes for parity testing.

Each archetype is a small, fast-running backtest payload that exercises one
distinct branch of the engine. Picking the right set is what makes Phase 2b
Rust port safe — every archetype that has a frozen snapshot is one we can
catch divergences on the moment we touch its Rust implementation.

Rules for adding a new archetype:
  - Short date range (1–3 months) so capture + parity tests run in seconds
  - One clear "feature" being exercised (SL, TrailSL, rollover, strangle, ...)
  - Self-contained — no external state required
  - Use ATM strike by default to avoid expiry-data sensitivity

The full payload list is the snapshot key. Changing any value invalidates
that snapshot — re-capture is required.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

INDEX = "NIFTY"
FROM_DATE = "2024-01-01"
TO_DATE = "2024-03-31"


def _leg(
    option_type: str = "CE",
    position: str = "SELL",
    strike_type: str = "ATM",
    lots: int = 1,
    expiry: str = "WEEKLY",
    **overrides: Any,
) -> Dict[str, Any]:
    leg: Dict[str, Any] = {
        "segment": "OPTIONS",
        "option_type": option_type,
        "position": position,
        "lots": lots,
        "expiry": expiry,
        "strike_interval": 50,
        "strike_selection": {"type": "strike_type", "strike_type": strike_type},
    }
    leg.update(overrides)
    return leg


def _base() -> Dict[str, Any]:
    return {
        "index": INDEX,
        "from_date": FROM_DATE,
        "to_date": TO_DATE,
        "strategy_type": "positional",
        "underlying": "cash",
        "expiry_window": "weekly_expiry",
        "entry_dte": 1,
        "exit_dte": 0,
        "slippage_pct": 0,
        "charges_enabled": False,
        "square_off_mode": "partial",
    }


# ── Archetypes ──────────────────────────────────────────────────────────────

ARCHETYPES: Dict[str, Dict[str, Any]] = {}


def _register(name: str, payload: Dict[str, Any]) -> None:
    ARCHETYPES[name] = payload


_register(
    "single_leg_ce_atm_sell",
    {**_base(), "legs": [_leg(option_type="CE", strike_type="ATM")]},
)

_register(
    "single_leg_pe_atm_sell",
    {**_base(), "legs": [_leg(option_type="PE", strike_type="ATM")]},
)

_register(
    "short_strangle_otm1",
    {
        **_base(),
        "legs": [
            _leg(option_type="CE", strike_type="OTM1"),
            _leg(option_type="PE", strike_type="OTM1"),
        ],
    },
)

_register(
    "iron_condor",
    {
        **_base(),
        "legs": [
            _leg(option_type="CE", position="SELL", strike_type="OTM1"),
            _leg(option_type="CE", position="BUY",  strike_type="OTM3"),
            _leg(option_type="PE", position="SELL", strike_type="OTM1"),
            _leg(option_type="PE", position="BUY",  strike_type="OTM3"),
        ],
    },
)

_register(
    "with_sl_and_target",
    {
        **_base(),
        "legs": [
            # Tight thresholds so SL/Target actually fires on many trades in
            # the 3-month window — without that the parity test would pass
            # even if the SL detection were broken.
            _leg(
                option_type="CE",
                strike_type="ATM",
                stopLoss={"mode": "PERCENT", "value": 30},
                targetProfit={"mode": "PERCENT", "value": 50},
            ),
        ],
    },
)

_register(
    "with_sl_aggressive",
    {
        **_base(),
        "legs": [
            _leg(
                option_type="CE",
                strike_type="ATM",
                # Very tight 10% SL → many ATM-CE-sell trades will trigger
                stopLoss={"mode": "PERCENT", "value": 10},
            ),
        ],
    },
)

_register(
    "with_target_aggressive",
    {
        **_base(),
        "legs": [
            _leg(
                option_type="CE",
                strike_type="ATM",
                # Very tight 20% target — common short-options outcome
                targetProfit={"mode": "PERCENT", "value": 20},
            ),
        ],
    },
)

_register(
    "with_trail_sl",
    {
        **_base(),
        "legs": [
            _leg(
                option_type="CE",
                strike_type="ATM",
                trailSL={"mode": "PERCENT", "trigger": 20, "move": 5},
            ),
        ],
    },
)

_register(
    "with_trail_sl_aggressive",
    {
        **_base(),
        "legs": [
            _leg(
                option_type="CE",
                strike_type="ATM",
                # Lower trigger so Trail SL engages quickly on favorable moves
                trailSL={"mode": "PERCENT", "trigger": 10, "move": 3},
            ),
        ],
    },
)

# ── Long-window variants — entry_dte=4 gives 4 trading-day holding window,
# letting SL fire mid-trade with an exit_date < expiry. THIS is what truly
# differentiates the SL path from the no-SL path; the short-window variants
# above can no-op pass even if SL detection is broken.
def _long_window_base():
    base = _base()
    base["entry_dte"] = 4
    base["exit_dte"] = 0
    return base


_register(
    "long_window_with_sl",
    {
        **_long_window_base(),
        "legs": [
            _leg(
                option_type="CE",
                strike_type="ATM",
                stopLoss={"mode": "PERCENT", "value": 25},
            ),
        ],
    },
)

_register(
    "long_window_with_target",
    {
        **_long_window_base(),
        "legs": [
            _leg(
                option_type="CE",
                strike_type="ATM",
                targetProfit={"mode": "PERCENT", "value": 30},
            ),
        ],
    },
)

_register(
    "long_window_with_trail_sl",
    {
        **_long_window_base(),
        "legs": [
            _leg(
                option_type="CE",
                strike_type="ATM",
                trailSL={"mode": "PERCENT", "trigger": 15, "move": 5},
            ),
        ],
    },
)

# Slice 10: Lazy leg. Rust orchestrator rejects payloads where reEntryOnSL
# / reEntryOnTarget has a lazyLegConfig (added during slice 6, never relaxed).
# The fallback test pins this so any future change that removes the blocker
# without porting _execute_lazy_leg fails fast. The archetype is the simplest
# possible: parent CE SELL with SL → on SL fire, lazy PE BUY ATM enters.
_register(
    "single_leg_lazy_pe_buy",
    {
        **_long_window_base(),
        "legs": [
            _leg(
                option_type="CE",
                strike_type="ATM",
                stopLoss={"mode": "PERCENT", "value": 25},
                reEntryOnSL={
                    "mode": "LAZY_LEG",
                    "count": 1,
                    "lazyLegConfig": {
                        "option_type": "PE",
                        "position": "BUY",
                        "lots": 1,
                        "expiry": "WEEKLY",
                        "strike_selection": {
                            "type": "strike_type",
                            "strike_type": "ATM",
                        },
                    },
                },
            ),
        ],
    },
)

# Slice 9: Futures leg. Rust orchestrator currently rejects FUTURES segment
# legs (no futures price cache in Rust). The parity test for this archetype
# verifies the fallback path — Rust returns None and the caller drops back
# to the Python engine. The archetype is here so any future Rust futures
# support can capture+verify against the locked Python snapshot.
_register(
    "single_leg_futures_monthly",
    {
        **_base(),
        "legs": [
            {
                "segment": "FUTURES",
                "option_type": "FUT",
                "position": "SELL",
                "lots": 1,
                "expiry": "CURRENT_MONTH",
                "fut_exit_mode": "ON_EXPIRY",
            },
        ],
    },
)

# Slice 8a: STR (super_trend) filter, 5x1. Only trades whose entry falls
# inside an STR segment are kept; the exit is clamped to the last trading day
# on or before seg_end when the scheduled exit exceeds seg_end.
# entry_dte=1 keeps trades to a 1-day window so segment-boundary clamping is
# minimal; filter_entry_mode defaults to 'dte' so min_days_mode is inactive.
_register(
    "single_leg_str_5x1",
    {
        **_base(),
        "super_trend_config": "5x1",
        "legs": [
            _leg(option_type="CE", strike_type="ATM"),
        ],
    },
)

# Slice 7a: Spot Adjustment exit trigger.
# When spot moves ±1% from entry, exit the trade on the trigger date.
# Long enough window so spot has chances to breach the threshold.
_register(
    "single_leg_spot_adjustment_both",
    {
        **_long_window_base(),
        "spot_adjustment_enabled": True,
        "spot_adjustment_direction": "both",
        "spot_adjustment_pct": 1.0,
        "spot_adjustment_units": "percent",
        "legs": [
            _leg(option_type="CE", strike_type="ATM"),
        ],
    },
)

# Slice 6: Re-entry on SL — simplest archetype.
# RE_ASAP mode means re-entry happens on the trigger date itself (close price).
# Fresh strike (default rollover_strike_mode) means strike is re-resolved at the
# re-entry date. count=1 means at most one re-entry per leg per trade.
# Long entry/exit window so SL has room to fire multiple times.
_register(
    "single_leg_reentry_sl_re_asap",
    {
        **_long_window_base(),
        "legs": [
            _leg(
                option_type="CE",
                strike_type="ATM",
                stopLoss={"mode": "PERCENT", "value": 25},
                reEntryOnSL={
                    "mode": "RE_ASAP",
                    "count": 1,
                },
            ),
        ],
    },
)

# Slice 4b: SL-with-Buffer. Gap-triggered exit with day_high/low override.
_register(
    "with_sl_buffer",
    {
        **_long_window_base(),
        "legs": [
            _leg(
                option_type="CE",
                strike_type="ATM",
                slWithBuffer={
                    "mode": "PERCENT", "value": 20, "buffer_pct": 5,
                },
            ),
        ],
    },
)

_register(
    "two_leg_with_overall_sl",
    {
        **_base(),
        "overall_sl_type": "PERCENT",
        "overall_sl_value": 2,
        "legs": [
            _leg(option_type="CE", strike_type="ATM"),
            _leg(option_type="PE", strike_type="ATM"),
        ],
    },
)

_register(
    "pct_strike_offset",
    {
        **_base(),
        "legs": [
            {
                "segment": "OPTIONS",
                "option_type": "CE",
                "position": "SELL",
                "lots": 1,
                "expiry": "WEEKLY",
                "strike_interval": 50,
                "strike_selection": {
                    "type": "pct_of_atm",
                    "value": 0.5,
                    "direction": "OTM",
                },
            }
        ],
    },
)

# ── Premium-based strike modes (slice 3) ────────────────────────────────────

_register(
    "closest_premium_ce",
    {
        **_base(),
        "legs": [
            {
                "segment": "OPTIONS",
                "option_type": "CE",
                "position": "SELL",
                "lots": 1,
                "expiry": "WEEKLY",
                "strike_interval": 50,
                "strike_selection": {
                    "type": "closest_premium",
                    "premium": 50.0,
                },
            }
        ],
    },
)

_register(
    "premium_gte_ce",
    {
        **_base(),
        "legs": [
            {
                "segment": "OPTIONS",
                "option_type": "CE",
                "position": "SELL",
                "lots": 1,
                "expiry": "WEEKLY",
                "strike_interval": 50,
                "strike_selection": {
                    "type": "premium_gte",
                    "premium": 30.0,
                },
            }
        ],
    },
)

_register(
    "premium_lte_pe",
    {
        **_base(),
        "legs": [
            {
                "segment": "OPTIONS",
                "option_type": "PE",
                "position": "SELL",
                "lots": 1,
                "expiry": "WEEKLY",
                "strike_interval": 50,
                "strike_selection": {
                    "type": "premium_lte",
                    "premium": 100.0,
                },
            }
        ],
    },
)

_register(
    "premium_range_strangle",
    {
        **_base(),
        "legs": [
            {
                "segment": "OPTIONS",
                "option_type": "CE",
                "position": "SELL",
                "lots": 1,
                "expiry": "WEEKLY",
                "strike_interval": 50,
                "strike_selection": {
                    "type": "premium_range",
                    "lower": 20.0,
                    "upper": 80.0,
                },
            },
            {
                "segment": "OPTIONS",
                "option_type": "PE",
                "position": "SELL",
                "lots": 1,
                "expiry": "WEEKLY",
                "strike_interval": 50,
                "strike_selection": {
                    "type": "premium_range",
                    "lower": 20.0,
                    "upper": 80.0,
                },
            },
        ],
    },
)

_register(
    "atm_straddle_prem_pct",
    {
        **_base(),
        "legs": [
            {
                "segment": "OPTIONS",
                "option_type": "CE",
                "position": "SELL",
                "lots": 1,
                "expiry": "WEEKLY",
                "strike_interval": 50,
                "strike_selection": {
                    "type": "atm_straddle_prem_pct",
                    "value": 25.0,  # target premium = 25% × ATM straddle
                },
            }
        ],
    },
)

_register(
    "straddle_width_ce",
    {
        **_base(),
        "legs": [
            {
                "segment": "OPTIONS",
                "option_type": "CE",
                "position": "SELL",
                "lots": 1,
                "expiry": "WEEKLY",
                "strike_interval": 50,
                "strike_selection": {
                    "type": "straddle_width",
                    "straddle_multiplier": 0.5,
                    "straddle_direction": "+",
                },
            }
        ],
    },
)


# ── Slice 9 (RE_ASAP_REV) ───────────────────────────────────────────────────
# On SL fire the re-entry uses the REVERSED position (SELL → BUY). Same
# parameters as single_leg_reentry_sl_re_asap except mode=RE_ASAP_REV.
_register(
    "reentry_re_asap_rev",
    {
        **_long_window_base(),
        "legs": [
            _leg(
                option_type="CE",
                strike_type="ATM",
                stopLoss={"mode": "PERCENT", "value": 25},
                reEntryOnSL={
                    "mode": "RE_ASAP_REV",
                    "count": 1,
                },
            ),
        ],
    },
)

# ── Slice 9b (rollover_strike_mode='fixed') ──────────────────────────────────
# filter_entry_mode='fixed' chains trades same-day starting at from_date.
# With rollover_strike_mode='fixed', all cycles in the segment reuse the
# first cycle's ATM strike (verified: all 13 trades use strike=21750).
_register(
    "rollover_fixed_strike",
    {
        **_base(),
        "filter_entry_mode": "fixed",
        "rollover_toggle": True,
        "rollover_min_days_to_expiry": 0,
        "legs": [
            _leg(
                option_type="CE",
                strike_type="ATM",
                rollover_strike_mode="fixed",
            ),
        ],
    },
)

# ── Slice RE_MOMENTUM (re-entry mode: scan for momentum signal post-SL) ───────
# After the parent leg's SL fires, scan subsequent daily closes and re-enter
# when price bounces back through the SL trigger level (momentum confirmed).
_register(
    "single_leg_reentry_sl_re_momentum",
    {
        **_long_window_base(),
        "legs": [
            _leg(
                option_type="CE",
                strike_type="ATM",
                stopLoss={"mode": "PERCENT", "value": 25},
                reEntryOnSL={
                    "mode": "RE_MOMENTUM",
                    "count": 1,
                },
            ),
        ],
    },
)

# ── Slice NEXT_WEEKLY (per-leg NEXT_WEEKLY expiry — calendar spread) ─────────
# Single CE leg trading the NEXT week's expiry contract while the schedule
# (entry/exit DTE) is anchored to the current weekly expiry cycle.
# Python engine resolves leg_options_expiry = next_exp when leg.expiry='NEXT_WEEKLY'.
_register(
    "single_leg_next_weekly",
    {
        **_base(),
        "legs": [
            _leg(option_type="CE", strike_type="ATM", expiry="NEXT_WEEKLY"),
        ],
    },
)

# ── Slice 8b (filter_entry_mode='fixed') ─────────────────────────────────────
# Like rollover_fixed_strike above but without fixed-strike locking.
# Each rollover cycle resolves a fresh ATM strike, so strikes vary.
_register(
    "filter_entry_fixed",
    {
        **_base(),
        "filter_entry_mode": "fixed",
        "rollover_toggle": True,
        "rollover_min_days_to_expiry": 0,
        "legs": [
            _leg(option_type="CE", strike_type="ATM"),
        ],
    },
)


def list_names() -> List[str]:
    return sorted(ARCHETYPES.keys())


def get(name: str) -> Dict[str, Any]:
    """Return a deep copy so callers can mutate without contaminating others."""
    if name not in ARCHETYPES:
        raise KeyError(f"unknown archetype: {name!r}; have {list_names()}")
    return deepcopy(ARCHETYPES[name])
