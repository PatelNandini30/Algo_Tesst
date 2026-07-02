"""
Tradesheet helpers for the multi-index / multi-expiry feature.

Pure functions (no engine/DB state) that:
  - build a config-derived export filename for a multi-index run, and
  - compute a per-(index, expiry) P&L breakdown from merged trade rows.

These are additive helpers consumed by services.multi_index_feature; they do
not touch any existing tradesheet/excel code path.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List


def _norm(s: Any) -> str:
    return str(s or "").strip().upper()


def build_export_filename(payload: Dict[str, Any]) -> str:
    """Descriptive, config-derived base filename (no extension).

    Example: NIFTY-SELL-CE-ATM-WEEKLY+MIDCPNIFTY-BUY-FUT-MONTHLY_2024-01-01_2024-03-31
    """
    default_index = _norm(payload.get("index") or "NIFTY")
    legs = [l for l in (payload.get("legs") or []) if isinstance(l, dict)]

    parts: List[str] = []
    for leg in legs:
        seg = _norm(leg.get("segment"))
        if seg == "MIDCAP100":
            continue  # overlay leg, named by the existing Midcap path
        idx = _norm(leg.get("index") or default_index)
        pos = _norm(leg.get("position")) or "SELL"
        exp = _norm(leg.get("expiry")).replace("_", "")
        if seg == "FUTURES":
            tag = "-".join(x for x in (idx, pos, "FUT", exp) if x)
        else:  # OPTIONS
            ot = _norm(leg.get("option_type"))
            ot = "CE" if ot in ("CALL", "CE") else "PE" if ot in ("PUT", "PE") else ot
            sel = leg.get("strike_selection") or {}
            strike = _norm(sel.get("strike_type") or leg.get("strike_type") or "ATM")
            tag = "-".join(x for x in (idx, pos, ot, strike, exp) if x)
        parts.append(tag)

    # de-duplicate, preserve order
    seen = set()
    uniq = [p for p in parts if not (p in seen or seen.add(p))]
    name = "+".join(uniq) if uniq else (default_index or "multi_index")

    frm = str(payload.get("from_date") or "").strip()
    to = str(payload.get("to_date") or "").strip()
    if frm and to:
        name = f"{name}_{frm}_{to}"

    name = re.sub(r"[^A-Za-z0-9_+.\-]", "", name)
    return name or "multi_index_backtest"


def per_index_summary(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-(index, expiry) breakdown from merged trade rows.

    Sums CE/PE/FUT P&L (each per-leg correct, so summing is not double-counted)
    and counts distinct trades. Returns a list ordered by first appearance.
    """
    groups: Dict[tuple, Dict[str, Any]] = {}
    order: List[tuple] = []
    for r in records:
        key = (r.get("Group Index"), r.get("Group Expiry"))
        g = groups.get(key)
        if g is None:
            g = {"index": key[0], "expiry": key[1], "pnl": 0.0, "_trades": set()}
            groups[key] = g
            order.append(key)
        for col in ("CE P&L", "PE P&L", "FUT P&L"):
            v = r.get(col)
            if v is not None:
                try:
                    g["pnl"] += float(v)
                except (TypeError, ValueError):
                    pass
        tid = r.get("Trade")
        if tid is not None:
            g["_trades"].add(tid)

    out: List[Dict[str, Any]] = []
    for key in order:
        g = groups[key]
        out.append({
            "index": g["index"],
            "expiry": g["expiry"],
            "net_pnl": round(g["pnl"], 2),
            "trades": len(g["_trades"]),
        })
    return out
