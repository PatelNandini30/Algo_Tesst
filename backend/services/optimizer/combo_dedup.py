"""Collapse combos that describe the SAME strategy before they are ever run.

An exhaustive grid multiplies every swept axis, but some axes are *inert* under
other settings: with `spot_adjustment.enabled = False`, the `pct` / `direction` /
`units` values on that leg change nothing the engine does. The grid still emits
one combo per value, so the same strategy is computed many times.

Measured on a real 14,400-combo sweep (job 204117dc, 2026-08-07):
    grid            14,400 combos   (5·2·5·2 · 2·3·2 · 2·3·2)
    unique results   3,969          -> 72% of the run was recomputation

This module turns a merged payload into a fingerprint of its EFFECTIVE strategy,
so the runner can keep one combo per fingerprint and skip the rest.

SAFETY: a wrong merge silently returns another strategy's numbers, which is far
worse than being slow. So the rules here are deliberately minimal and each one
must be justified by "the engine cannot read this field in this state". Verify
any new rule with tools/combo_dedup_verify.py, which groups a completed job's
REAL results by fingerprint and fails if any group holds two different P&Ls.
"""
from __future__ import annotations


import hashlib
import json
from typing import Any, Dict

# Fields that exist only to configure spot adjustment. When adjustment is off,
# the engine never reads them (services/engine_rust.py gates every use on the
# enabled flag), so two payloads differing only here run identically.
_SPOT_ADJ_INERT_KEYS = ("pct", "direction", "units", "confirm_days",
                        "use_entry_close", "combine_mode")

# Same idea at payload level (strategy-wide spot adjustment).
_PAYLOAD_SPOT_ADJ_INERT = ("spot_adjustment_pct", "spot_adjustment_direction",
                           "spot_adjustment_units", "spot_adjustment_confirm_days",
                           "spot_adjustment_use_entry_close",
                           "spot_adjustment_combine_mode")


def _is_zero(v: Any) -> bool:
    """True only for a value that is numerically zero.

    Anything unparseable returns False — an un-merged duplicate costs time, a
    wrongly-merged pair costs correctness.
    """
    if v is None or isinstance(v, bool):
        return False
    try:
        return float(v) == 0.0
    except (TypeError, ValueError):
        return False


def _truthy(v: Any) -> bool:
    """MUST match the live engine's own truthiness test EXACTLY, not a curated
    allow-list. The engine checks `enabled` with raw Python truthiness —
    engine_rust.py:_resolve_leg_sa does `not _c.get('enabled')`, and the
    payload-level gate is `bool(payload.get('spot_adjustment_enabled'))` — under
    which ANY non-empty string, including the string 'false' itself, is truthy.
    The old allow-list ({'1','true','yes','on'}) treated 'false'/'0'/'no'/'off'
    as OFF, so combo_dedup stripped pct/direction and declared two payloads
    identical when the engine — reading the exact same string — treated the
    adjustment as ON with pct/direction as live trigger parameters, silently
    dropping one of two genuinely-different strategies from the sweep. The
    UI is documented to send this field as a string in some paths, so this is
    not a hypothetical: see backend/tests/test_combo_dedup.py's
    test_string_truthiness_is_handled ('UI sends "false"/"true" as strings').
    """
    return bool(v)


def normalise_effective(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy with inert fields neutralised, so equal strategies compare equal.

    Only removes values the engine provably cannot act on. Anything uncertain is
    LEFT IN — an un-merged duplicate costs time; a wrongly-merged pair costs
    correctness.
    """
    # SHALLOW copy + targeted copies of only what we mutate. A full deepcopy of
    # the payload measured 0.078 ms of the 0.108 ms fingerprint cost (72%) — and
    # every nested value we do not touch can be shared safely, because this
    # function never mutates them and the result is only serialised for hashing.
    # Verified to produce IDENTICAL fingerprints (same 3,969 groups on 28,800
    # real results) after the change.
    if not isinstance(payload, dict):
        return {}
    p = dict(payload)

    # Dispatch metadata (`__combo_id__`, `__optim_callback__`) can ride into the
    # merged payload via apply_combo_for_optim. It is bookkeeping, never
    # strategy — and since each combo carries a DIFFERENT id, leaving it in makes
    # every fingerprint unique and silently disables dedup entirely. Dropped here
    # as well as at the call site, because a silent no-op is the worst outcome.
    for _k in [k for k in p if str(k).startswith("__")]:
        p.pop(_k, None)

    # Strategy-level spot adjustment off -> its parameters are unreadable.
    if not _truthy(p.get("spot_adjustment_enabled")):
        for k in _PAYLOAD_SPOT_ADJ_INERT:
            p.pop(k, None)

    # Copy the legs list and only the legs/sub-dicts we actually rewrite, so the
    # caller's payload is never mutated (test_does_not_mutate_the_caller_payload).
    legs = p.get("legs")
    if isinstance(legs, list):
        p["legs"] = [dict(l) if isinstance(l, dict) else l for l in legs]

    for leg in (p.get("legs") or []):
        if not isinstance(leg, dict):
            continue
        sa = leg.get("spot_adjustment")
        if isinstance(sa, dict) and not _truthy(sa.get("enabled")):
            # Keep the flag itself (enabled=False IS meaningful); drop the rest.
            leg["spot_adjustment"] = {"enabled": False}

        # STRADDLE WIDTH: the strike is ATM ± multiplier × width, so a multiplier
        # of ZERO lands on ATM whichever direction is chosen — '+' and '-' are the
        # same strike, hence the same strategy. Verified on 980 real cases from
        # job 204117dc: every pair differing only in straddle_direction yet
        # producing an identical P&L had multiplier 0.0, and NO non-zero
        # multiplier ever did. Guarded by _is_zero so a non-zero multiplier keeps
        # its direction (there the sign genuinely picks a different strike).
        for _sel_key in ("strike_selection", "strike_selection_2"):
            sel = leg.get(_sel_key)
            if isinstance(sel, dict) and _is_zero(sel.get("straddle_multiplier")):
                sel = dict(sel)                 # copy only what we rewrite
                leg[_sel_key] = sel
                sel.pop("straddle_direction", None)
                # Canonicalise the zero itself so 0, 0.0 and "0" — the same
                # value arriving from the UI in different types — hash alike.
                # Only ever applied to a value already proven zero.
                sel["straddle_multiplier"] = 0.0
        if _is_zero(leg.get("straddle_multiplier")):
            leg.pop("straddle_direction", None)
    return p


def effective_fingerprint(payload: Dict[str, Any]) -> str:
    """Stable hash of a payload's EFFECTIVE strategy.

    Two combos with the same fingerprint produce the same tradesheet, so only one
    of them needs to run.
    """
    norm = normalise_effective(payload)
    blob = json.dumps(norm, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()
