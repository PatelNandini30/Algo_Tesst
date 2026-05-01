"""
base_fast_patch.py
==================
Monkey-patches get_option_premium_from_db and get_spot_price_from_db in base.py
so they try the O(1) fast_lookup dict FIRST, then fall back to the original DB
path if not found. Idempotent. Safe — wrapped in try/except at call site.
"""

from __future__ import annotations
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger(__name__)

_base_patched = False
_engine_patched = False
_original_get_option_premium = None
_original_get_spot_price = None
_original_check_leg_stop_loss_target = None
_original_check_overall_stop_loss_target = None


def apply_fast_lookup_patches() -> None:
    global _base_patched, _engine_patched
    global _original_get_option_premium, _original_get_spot_price
    global _original_check_leg_stop_loss_target, _original_check_overall_stop_loss_target

    import base as _base
    from services import fast_lookup as _fl
    from services import rust_fast_path as _rf

    if not hasattr(_base, "get_option_premium_from_db") or not hasattr(_base, "get_spot_price_from_db"):
        logger.debug("[FAST_PATCH] base functions not ready yet")
        return

    def _fast_get_option_premium(date, index, strike, option_type, expiry, *args, **kwargs):
        result = _fl.get_option_price_fast(
            date=date, index=index, strike=strike, opt_type=option_type, expiry=expiry,
        )
        if result is not None:
            return result
        return _original_get_option_premium(date, index, strike, option_type, expiry, *args, **kwargs)

    def _fast_get_spot_price(date, index, *args, **kwargs):
        result = _fl.get_spot_price_fast(date=date, index=index)
        if result is not None:
            return result
        return _original_get_spot_price(date, index, *args, **kwargs)

    def _fast_check_leg_stop_loss_target(
        entry_date,
        exit_date,
        expiry_date,
        entry_spot,
        legs_config,
        index,
        trading_calendar,
        square_off_mode,
        slippage_pct=0.0,
    ):
        if _original_check_leg_stop_loss_target is None:
            raise RuntimeError("Original check_leg_stop_loss_target not available")
        return _rf.check_leg_stop_loss_target_rust(
            entry_date,
            exit_date,
            expiry_date,
            entry_spot,
            legs_config,
            index,
            trading_calendar,
            square_off_mode,
            slippage_pct=slippage_pct,
            original_python_fn=_original_check_leg_stop_loss_target,
        )

    def _fast_check_overall_stop_loss_target(
        entry_date,
        exit_date,
        expiry_date,
        trade_legs,
        index,
        trading_calendar,
        sl_threshold_rs,
        tgt_threshold_rs,
        per_leg_results=None,
        overall_sl_type=None,
        overall_target_type=None,
        slippage_pct=0.0,
    ):
        if _original_check_overall_stop_loss_target is None:
            raise RuntimeError("Original check_overall_stop_loss_target not available")
        return _rf.check_overall_stop_loss_target_rust(
            entry_date,
            exit_date,
            expiry_date,
            trade_legs,
            index,
            trading_calendar,
            sl_threshold_rs,
            tgt_threshold_rs,
            per_leg_results=per_leg_results,
            overall_sl_type=overall_sl_type,
            overall_target_type=overall_target_type,
            slippage_pct=slippage_pct,
            original_python_fn=_original_check_overall_stop_loss_target,
        )

    if not _base_patched:
        _original_get_option_premium = _base.get_option_premium_from_db
        _original_get_spot_price = _base.get_spot_price_from_db
        _base.get_option_premium_from_db = _fast_get_option_premium
        _base.get_spot_price_from_db = _fast_get_spot_price
        _base_patched = True

    patch_native_scans = os.environ.get("FAST_LOOKUP_NATIVE_SCANS", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    engine_module_name = "engines.generic_algotest_engine"
    if engine_module_name in sys.modules:
        eng = sys.modules[engine_module_name]
        if hasattr(eng, "get_option_premium_from_db"):
            eng.get_option_premium_from_db = _fast_get_option_premium
        if hasattr(eng, "get_spot_price_from_db"):
            eng.get_spot_price_from_db = _fast_get_spot_price
        if not patch_native_scans:
            # Keep the proven Python SL/target/re-entry semantics. The native
            # scanner is an optional experiment and must not silently replace
            # exit logic in production.
            if _engine_patched:
                if _original_check_leg_stop_loss_target is not None and hasattr(eng, "check_leg_stop_loss_target"):
                    eng.check_leg_stop_loss_target = _original_check_leg_stop_loss_target
                if _original_check_overall_stop_loss_target is not None and hasattr(eng, "check_overall_stop_loss_target"):
                    eng.check_overall_stop_loss_target = _original_check_overall_stop_loss_target
                _engine_patched = False
            logger.info("[FAST_PATCH] price lookup patches applied; native SL/target scans disabled")
            return
        if not _engine_patched:
            if hasattr(eng, "check_leg_stop_loss_target"):
                _original_check_leg_stop_loss_target = eng.check_leg_stop_loss_target
            if hasattr(eng, "check_overall_stop_loss_target"):
                _original_check_overall_stop_loss_target = eng.check_overall_stop_loss_target
            if hasattr(eng, "check_leg_stop_loss_target") and _original_check_leg_stop_loss_target is not None:
                eng.check_leg_stop_loss_target = _fast_check_leg_stop_loss_target
            if hasattr(eng, "check_overall_stop_loss_target") and _original_check_overall_stop_loss_target is not None:
                eng.check_overall_stop_loss_target = _fast_check_overall_stop_loss_target
            _engine_patched = True

    logger.info("[FAST_PATCH] base.py fast-lookup patches applied")


def restore_original_patches() -> None:
    global _base_patched, _engine_patched
    import base as _base
    if _original_get_option_premium is not None:
        _base.get_option_premium_from_db = _original_get_option_premium
    if _original_get_spot_price is not None:
        _base.get_spot_price_from_db = _original_get_spot_price
    if "engines.generic_algotest_engine" in sys.modules:
        eng = sys.modules["engines.generic_algotest_engine"]
        if _original_check_leg_stop_loss_target is not None and hasattr(eng, "check_leg_stop_loss_target"):
            eng.check_leg_stop_loss_target = _original_check_leg_stop_loss_target
        if _original_check_overall_stop_loss_target is not None and hasattr(eng, "check_overall_stop_loss_target"):
            eng.check_overall_stop_loss_target = _original_check_overall_stop_loss_target
    _base_patched = False
    _engine_patched = False
