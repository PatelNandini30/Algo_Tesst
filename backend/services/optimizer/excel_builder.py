"""
Backend XLSX builder for optimizer tradesheets.

Replicates buildTradeExcel.js (ExcelJS) logic using openpyxl so the ZIP
endpoint can include the same Trade Sheet + Summary format without requiring
a browser/Node process.
"""
from __future__ import annotations

import io
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# ── Palette (matches buildTradeExcel.js C object) ────────────────────────────
_NAVY_BG    = "1F3864"
_SECTION_BG = "2C5F8A"
_HEADER_BG  = "34495E"
_SUB_HDR_BG = "D6E4F7"
_SUB_HDR_TX = "1F3864"
_GREEN_BG   = "D4EFDF"
_GREEN_TX   = "1E7E34"
_RED_BG     = "FDE8E8"
_RED_TX     = "C0392B"
_LABEL_BG   = "F2F6FA"
_ALT_ROW    = "F9FBFD"
_BORDER_CLR = "B0C4D8"
_WHITE      = "FFFFFF"
_DARK_TXT   = "1A1A2E"
_DARK2_TXT  = "2C3E50"
_WHITE_TXT  = "FFFFFF"

# Style caching — openpyxl deduplicates styles via __eq__/__hash__ on each
# cell assignment, which is O(n) over the style table.  Reusing the SAME
# object reference makes the identity-equality fast-path kick in and cuts
# XLSX build time from ~3s to ~0.6s per file.
_FILL_CACHE: Dict[str, PatternFill] = {}
_FONT_CACHE: Dict[tuple, Font]       = {}
_BORDER_CACHE: Dict[str, Border]     = {}

def _fill(hex_color: str) -> PatternFill:
    f = _FILL_CACHE.get(hex_color)
    if f is None:
        f = PatternFill("solid", fgColor=hex_color)
        _FILL_CACHE[hex_color] = f
    return f

def _font(bold: bool = False, size: int = 10, color: str = "000000") -> Font:
    key = (bold, size, color)
    f = _FONT_CACHE.get(key)
    if f is None:
        f = Font(bold=bold, size=size, color=color, name="Calibri")
        _FONT_CACHE[key] = f
    return f

def _border() -> Border:
    b = _BORDER_CACHE.get(_BORDER_CLR)
    if b is None:
        side = Side(style="thin", color=_BORDER_CLR)
        b = Border(top=side, left=side, bottom=side, right=side)
        _BORDER_CACHE[_BORDER_CLR] = b
    return b

_CENTER = Alignment(horizontal="center", vertical="center")
_LEFT   = Alignment(horizontal="left",   vertical="center")


def _to_num(v) -> Optional[float]:
    if isinstance(v, (int, float)) and math.isfinite(float(v)):
        return float(v)
    if v is None or v == "" or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        s = str(v).replace(",", "").replace("%", "").replace("₹", "").strip()
        f = float(s)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _parse_date(v) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v
    if not v or v == "":
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


_DATE_COLS = {
    "Entry Date", "Exit Date", "Expiry",
    "Leg Exit Date", "Lazy Entry Date", "Lazy Exit Date",
}
_TRUE_PCT_COLS = {"Spot P&L %", "CE P&L %", "PE P&L %", "%DD"}
_MAE_COLS      = {"MAE", "MFE", "Net MAE 1", "Net MAE 2", "Final MAE"}
_TRADE_COLS    = {
    "Net MAE 1", "Net MAE 2", "Final MAE",
    "Net P&L", "% P&L", "Cumulative", "Peak", "DD", "%DD",
    "Lowest NAV", "Actual Live DD",
}

_COL_WIDTHS: Dict[str, int] = {
    "Leg": 12, "Entry Date": 13, "Exit Date": 13,
    "Entry Spot": 12, "Exit Spot": 12,
    "buffer_ref_price": 12, "buffer_strike_offset": 10,
    "Re-Entry Type": 14,
    "Raw Entry Price": 12, "Entry Price": 12,
    "Raw Exit Price": 12, "Exit Price": 12,
    "MAE": 9, "MFE": 9, "Net MAE 1": 10, "Net MAE 2": 10, "Final MAE": 10,
    "Net P&L": 10, "% P&L": 8, "Cumulative": 11, "Peak": 10, "DD": 9, "%DD": 8,
    "Lowest NAV": 13, "Actual Live DD": 15,
    "Spot P&L %": 10, "CE P&L %": 10, "PE P&L %": 10,
    "Exit Reason": 14, "Expiry": 12, "STR Segment": 14, "Filter Segment": 22,
}


def _is_lazy(row: Dict) -> bool:
    v = row.get("Is Lazy Leg")
    return v is True or str(v).lower() == "true" or bool(row.get("Lazy Leg Name"))


def _get_reentry_type(row: Dict) -> str:
    if _is_lazy(row):
        return "Lazy"
    mode = str(row.get("ReEntryMode") or "").strip()
    if mode:
        return mode
    trigger = str(row.get("ReEntryTrigger") or "").strip()
    if trigger:
        return trigger
    return "Re-Entry" if row.get("ReEntryIndex") else ""


def _calc_trade_mae(legs: List[Dict]):
    """Replicate calcTradeMae from JS."""
    option_legs  = [l for l in legs if str(l.get("Type") or "").upper() in ("CE", "CALL", "PE", "PUT")]
    future_legs  = [l for l in legs if str(l.get("Type") or "").upper() == "FUT"]
    if not option_legs:
        return None

    def _sum_field(rows, key):
        total = 0.0
        for r in rows:
            v = _to_num(r.get(key))
            if v is None:
                return None
            total += v
        return total

    opt_mae = _sum_field(option_legs, "MAE")
    opt_mfe = _sum_field(option_legs, "MFE")
    if opt_mae is None or opt_mfe is None:
        return None

    def _rnd(v):
        return round(v * 10000) / 10000

    if future_legs:
        fut_mfe = _sum_field(future_legs, "MFE")
        fut_mae = _sum_field(future_legs, "MAE")
        if fut_mfe is None or fut_mae is None:
            return None
        nm1 = fut_mfe + opt_mae
        nm2 = opt_mfe + fut_mae
        return (_rnd(nm1), _rnd(nm2), _rnd(min(nm1, nm2)))

    buy_legs  = [l for l in option_legs if str(l.get("B/S") or "").upper() == "BUY"]
    sell_legs = [l for l in option_legs if str(l.get("B/S") or "").upper() == "SELL"]
    if buy_legs and sell_legs:
        bm = _sum_field(buy_legs,  "MAE"); bf = _sum_field(buy_legs,  "MFE")
        sm = _sum_field(sell_legs, "MAE"); sf = _sum_field(sell_legs, "MFE")
        if None in (bm, bf, sm, sf):
            return None
        nm1 = sm + bf; nm2 = sf + bm
        return (_rnd(nm1), _rnd(nm2), _rnd(min(nm1, nm2)))

    return (_rnd(opt_mae), _rnd(opt_mfe), _rnd(min(opt_mae, opt_mfe)))


def _build_key_order(rows: List[Dict]) -> List[str]:
    has_calls     = any(str(r.get("Type") or "").upper() in ("CE", "CALL")  for r in rows)
    has_puts      = any(str(r.get("Type") or "").upper() in ("PE", "PUT")   for r in rows)
    has_futures   = any(str(r.get("Type") or "").upper() == "FUT"           for r in rows)
    has_buffer    = any(
        r.get("buffer_ref_price") not in (None, "", "False", False)         for r in rows
    )
    has_spot_adj  = any(
        r.get("Raw Entry Price") not in (None, "") and
        _to_num(r.get("Raw Entry Price")) != _to_num(r.get("Entry Price"))  for r in rows
    )
    has_reentry   = any(
        r.get("ReEntryIndex") or r.get("ReEntryTrigger") or
        r.get("ReEntryMode") or _is_lazy(r)                                 for r in rows
    )
    has_str       = any(r.get("STR Segment")    not in (None, "") for r in rows)
    has_filter    = any(r.get("Filter Segment") not in (None, "") for r in rows)

    order = [
        "Trade", "Leg", "Index", "Entry Date", "Exit Date", "Expiry",
        "Entry Spot", "Exit Spot", "Spot P&L", "Spot P&L %",
        "Type", "Strike",
    ]
    if has_buffer:
        order += ["buffer_ref_price", "buffer_strike_offset"]
    order.append("B/S")
    if has_reentry:
        order.append("Re-Entry Type")
    order.append("Qty")
    if has_spot_adj:
        order.append("Raw Entry Price")
    order.append("Entry Price")
    if has_spot_adj:
        order.append("Raw Exit Price")
    order += ["Exit Price", "MAE", "MFE", "Net MAE 1", "Net MAE 2", "Final MAE"]
    if has_calls:
        order += ["CE P&L", "CE P&L %"]
    if has_puts:
        order += ["PE P&L", "PE P&L %"]
    if has_futures:
        order.append("FUT P&L")
    order += ["Net P&L", "% P&L", "Cumulative", "Peak", "DD", "%DD", "Lowest NAV", "Actual Live DD"]
    order.append("Exit Reason")
    if has_str:
        order.append("STR Segment")
    if has_filter:
        order.append("Filter Segment")

    return order, has_calls, has_puts, has_futures


def _aggregate_trades(rows: List[Dict]) -> Dict[str, Any]:
    """Return per-trade aggregates keyed by str(trade_id), mimicking JS tm dict."""
    grouped: Dict[str, List[Dict]] = {}
    for r in rows:
        k = str(r.get("Trade") or r.get("trade") or 1)
        grouped.setdefault(k, []).append(r)

    tm: Dict[str, Any] = {}
    for k, legs in grouped.items():
        main = next((l for l in legs
                     if not l.get("ReEntryIndex") and not l.get("ReEntryTrigger")
                     and not l.get("ReEntryMode") and not _is_lazy(l)), legs[0])
        spot    = _to_num(main.get("Entry Spot")) or 0.0
        raw_net = _to_num(main.get("Net P&L"))
        if raw_net is None:
            raw_net = sum(
                (_to_num(l.get("CE P&L"))  or 0) +
                (_to_num(l.get("PE P&L"))  or 0) +
                (_to_num(l.get("FUT P&L")) or 0)
                for l in legs
            )
        mae_res = _calc_trade_mae(legs)

        tm[k] = {
            "net":        raw_net,
            "pct":        (raw_net / spot * 100) if spot != 0 else 0.0,
            "netMae1":    mae_res[0] if mae_res else "",
            "netMae2":    mae_res[1] if mae_res else "",
            "finalMae":   mae_res[2] if mae_res else "",
            "cumulative": "",
            "peak":       "",
            "dd":         "",
            "pctDd":      "",
            "lowestNav":  "",
            "actualLDD":  "",
        }

    # Booked Cumulative / Peak / DD / %DD and Lowest NAV / Actual Live DD pass.
    # Iterate trades in the same CHRONOLOGICAL order used by _build_cleaned_rows
    # so cascade re-entries (higher trade_id, earlier entry date) are placed in
    # the correct time sequence.
    def _parse_entry(r: Dict):
        v = r.get("Entry Date") or r.get("entry_date") or ""
        try:
            return _parse_date(v) or datetime.max
        except Exception:
            return datetime.max

    sorted_rows = sorted(rows, key=lambda r: (
        _parse_entry(r),
        int(str(r.get("Trade") or r.get("trade") or 1)),
        int(str(r.get("Leg")   or r.get("leg")   or 1)),
    ))

    _seen: set = set()
    sorted_keys: List[str] = []
    for _r in sorted_rows:
        _k = str(_r.get("Trade") or _r.get("trade") or 1)
        if _k not in _seen and _k in tm:
            _seen.add(_k)
            sorted_keys.append(_k)

    # Recompute the booked equity curve exactly like the research-sheet
    # formulas: cumulative compounds from prior visible trade rows using
    # `% P&L`; peak is the running max; DD is blank at equity highs; %DD is the
    # Excel ratio DD / Peak.
    cumulative = 100.0
    peak = 100.0
    for k in sorted_keys:
        t = tm[k]
        pct = t["pct"] if isinstance(t["pct"], float) else 0.0
        cumulative *= (1.0 + pct / 100.0)
        peak = max(peak, cumulative)
        # When at an equity high, drawdown is 0 (not blank).  Previously this
        # was "" which rendered as an empty cell; users expect a clean 0.00.
        dd = cumulative - peak if peak > cumulative else 0.0
        t["cumulative"] = cumulative
        t["peak"] = peak
        t["dd"] = dd
        t["pctDd"] = (dd / peak) if peak != 0 else 0.0

    prev_cum = 100.0
    first_done = False
    for k in sorted_keys:
        t = tm[k]
        mae  = t["finalMae"]   if isinstance(t["finalMae"],   float) else None
        peak = t["peak"]       if isinstance(t["peak"],        float) else None
        cum  = t["cumulative"] if isinstance(t["cumulative"],  float) else None
        if mae is not None and peak is not None and peak != 0:
            if not first_done and cum is not None:
                lowest_nav = round(cum * 100) / 100
            else:
                lowest_nav = round(prev_cum * (1 + mae / 100) * 100) / 100
            actual_ldd = round((lowest_nav / peak - 1) * 10000) / 100
            t["lowestNav"] = lowest_nav
            t["actualLDD"] = actual_ldd
            first_done = True
        else:
            first_done = True
        if cum is not None:
            prev_cum = cum

    return tm, grouped


def _build_cleaned_rows(rows: List[Dict], key_order: List[str], tm: Dict) -> List[Dict]:
    # Sort by Entry Date first so cascade mini-trades (with NEW higher trade
    # IDs but earlier entry dates than later originals) appear interleaved
    # chronologically with the originals.  Secondary keys keep all legs of
    # the same trade grouped together.  The CSV from the engine is already
    # in this order, but we re-sort defensively in case the row dicts came
    # from any source that doesn't preserve order.
    def _parse_entry(r):
        v = r.get("Entry Date") or r.get("entry_date") or ""
        try:
            return _parse_date(v) or datetime.max
        except Exception:
            return datetime.max
    sorted_rows = sorted(rows, key=lambda r: (
        _parse_entry(r),
        int(str(r.get("Trade") or r.get("trade") or 1)),
        int(str(r.get("Leg")   or r.get("leg")   or 1)),
    ))
    # Build engine-trade-id → sequential display number (1, 2, 3, ...) based on
    # FIRST appearance in chronological order.  This is what the "Index" column
    # shows the user, so cascade trades (engine_tid=71+) get the correct
    # sequential number for their chronological position (e.g. Trade=71 enters
    # right after Trade=5, so its display Index is 6, not 71).
    _tid_to_index_no: Dict[str, int] = {}
    _seq_no = 0
    for _r in sorted_rows:
        _ek = str(_r.get("Trade") or _r.get("trade") or 1)
        if _ek not in _tid_to_index_no:
            _seq_no += 1
            _tid_to_index_no[_ek] = _seq_no
    written: set = set()
    cleaned = []
    for trade in sorted_rows:
        k     = str(trade.get("Trade") or trade.get("trade") or 1)
        first = k not in written
        if first:
            written.add(k)
        m   = tm.get(k, {})
        row = {}
        for key in key_order:
            val = ""
            if key in _TRADE_COLS:
                if not first:
                    val = ""
                elif key == "Net MAE 1":      val = m.get("netMae1", "")
                elif key == "Net MAE 2":      val = m.get("netMae2", "")
                elif key == "Final MAE":      val = m.get("finalMae", "")
                elif key == "Net P&L":        val = m.get("net", "")
                elif key == "% P&L":          val = m.get("pct", "")
                elif key == "Cumulative":     val = m.get("cumulative", "")
                elif key == "Peak":           val = m.get("peak", "")
                elif key == "DD":             val = m.get("dd", "")
                elif key == "%DD":            val = m.get("pctDd", "")
                elif key == "Lowest NAV":     val = m.get("lowestNav", "")
                elif key == "Actual Live DD": val = m.get("actualLDD", "")
            elif key == "Leg" and _is_lazy(trade):
                val = trade.get("Lazy Leg Name") or trade.get("Leg", "")
            elif key == "Re-Entry Type":
                val = _get_reentry_type(trade)
            elif key == "Trade":
                # Show user-friendly sequential trade number instead of the
                # engine's internal trade_id (which jumps to 71+ for cascade
                # mini-trades).  Same value as the "Index" column so users
                # see clean 1, 2, 3, ... in both columns.
                val = _tid_to_index_no.get(k, int(str(trade.get("Trade") or trade.get("trade") or 1)))
            elif key == "Index":
                val = _tid_to_index_no.get(k, int(str(trade.get("Trade") or trade.get("trade") or 1)))
            elif key == "Spot P&L %":
                # Spot P&L is a trade-level quantity written only on Leg 1 rows.
                # Leave Spot P&L % blank on Leg 2+ rows so the column matches
                # Net P&L's first-leg-only convention and column sums give the
                # correct trade-level total.
                spot_pnl = _to_num(trade.get("Spot P&L"))
                if spot_pnl is None:
                    val = ""
                else:
                    es2 = _to_num(trade.get("Entry Spot"))
                    val = (spot_pnl / es2) if (es2 and es2 != 0) else ""
            elif key == "CE P&L %":
                pnl = _to_num(trade.get("CE P&L"))
                es  = _to_num(trade.get("Entry Spot"))
                val = (pnl / es) if (pnl is not None and es and es != 0) else ""
            elif key == "PE P&L %":
                pnl = _to_num(trade.get("PE P&L"))
                es  = _to_num(trade.get("Entry Spot"))
                val = (pnl / es) if (pnl is not None and es and es != 0) else ""
            else:
                val = trade.get(key, "")
            if val is None or (isinstance(val, float) and math.isnan(val)):
                val = ""
            row[key] = val
        cleaned.append(row)
    return cleaned


# ── Sheet 1: Trade Sheet ──────────────────────────────────────────────────────

def _write_trade_sheet(wb: Workbook, cleaned: List[Dict], key_order: List[str]) -> None:
    ws = wb.create_sheet("Trade Sheet")
    ws.freeze_panes = "A2"

    for ci, key in enumerate(key_order, 1):
        ws.column_dimensions[get_column_letter(ci)].width = _COL_WIDTHS.get(key, 10)

    # Header
    hdr = ws.append
    hdr_row = ws.row_dimensions[1]
    hdr_row.height = 22
    for ci, key in enumerate(key_order, 1):
        cell = ws.cell(row=1, column=ci, value=key)
        cell.font      = _font(bold=True, size=10, color=_WHITE_TXT)
        cell.fill      = _fill(_HEADER_BG)
        cell.alignment = _CENTER
        cell.border    = _border()

    # Data rows
    for ri, row in enumerate(cleaned, 2):
        bg = _WHITE if ri % 2 == 0 else _ALT_ROW
        net_val = row.get("Net P&L")
        net_num = _to_num(net_val)

        ws.row_dimensions[ri].height = 18
        for ci, key in enumerate(key_order, 1):
            raw = row.get(key, "")
            cell = ws.cell(row=ri, column=ci)
            cell.border    = _border()
            cell.fill      = _fill(bg)
            cell.font      = _font(size=10)
            cell.alignment = _LEFT

            # Date columns
            if key in _DATE_COLS:
                d = _parse_date(raw) if not isinstance(raw, datetime) else raw
                if d:
                    cell.value  = d
                    cell.number_format = "DD-MMM-YYYY"
                else:
                    cell.value = raw if raw != "" else None
                continue

            # Numeric coercion
            if raw != "" and raw is not None:
                num = _to_num(raw)
                if num is not None:
                    cell.value = num
                    if key in _TRUE_PCT_COLS:
                        cell.number_format = "0.00%"
                    elif key in _MAE_COLS:
                        cell.number_format = "#,##0.0000"
                    elif num == int(num):
                        cell.number_format = "0"
                    else:
                        cell.number_format = "#,##0.00"
                    continue
            cell.value = raw if raw not in ("", None) else None

        # Color Net P&L and % P&L columns
        if net_num is not None:
            for col_key in ("Net P&L", "% P&L"):
                try:
                    ci2 = key_order.index(col_key) + 1
                    c = ws.cell(row=ri, column=ci2)
                    clr = _GREEN_TX if net_num >= 0 else _RED_TX
                    bg2 = _GREEN_BG if net_num >= 0 else _RED_BG
                    c.font = _font(bold=True, size=10, color=clr)
                    c.fill = _fill(bg2)
                except ValueError:
                    pass


# ── Sheet 2: Summary ─────────────────────────────────────────────────────────

def _write_summary_sheet(
    wb: Workbook,
    cleaned: List[Dict],
    summary: Dict[str, Any],
    tm: Dict,
    combo_label: str,
    from_date: str,
    to_date: str,
    has_calls: bool,
    has_puts: bool,
    has_futures: bool,
) -> None:
    ws = wb.create_sheet("Summary")
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 30
    ws.column_dimensions["E"].width = 20

    S = summary or {}
    row = [1]

    def _merge(r, c1="A", c2="E"):
        ws.merge_cells(f"{c1}{r}:{c2}{r}")

    def _title(text, r, bg=_NAVY_BG):
        _merge(r)
        c = ws.cell(row=r, column=1, value=text)
        c.font      = _font(bold=True, size=13, color=_WHITE_TXT)
        c.fill      = _fill(bg)
        c.alignment = _CENTER
        ws.row_dimensions[r].height = 26

    def _section(text, r):
        _merge(r)
        c = ws.cell(row=r, column=1, value="  " + text)
        c.font      = _font(bold=True, size=11, color=_WHITE_TXT)
        c.fill      = _fill(_SECTION_BG)
        c.alignment = _LEFT
        ws.row_dimensions[r].height = 20

    def _kv(label, value, r, col="A", alt=False, val_color=None):
        col_idx = ord(col.upper()) - ord("A") + 1
        lc = ws.cell(row=r, column=col_idx, value=label)
        vc = ws.cell(row=r, column=col_idx + 1, value=value)
        lc.font = _font(bold=True, size=10, color=_DARK2_TXT)
        lc.fill = _fill(_ALT_ROW if alt else _LABEL_BG)
        lc.alignment = _LEFT
        lc.border    = _border()
        num = _to_num(str(value or "").replace("+", "").replace("%", "").replace("₹", ""))
        auto_color = val_color or (_GREEN_TX if (num is not None and num >= 0) else _RED_TX if (num is not None and num < 0) else _DARK_TXT)
        vc.font = _font(bold=True, size=10, color=auto_color)
        vc.fill = _fill(_ALT_ROW if alt else _WHITE)
        vc.alignment = _LEFT
        vc.border    = _border()
        ws.row_dimensions[r].height = 18

    # ── Compute stats from cleaned rows (mirrors JS) ──────────────────────────
    sum_pct = 0.0; sum_pos_pct = 0.0; sum_neg_pct = 0.0
    win_cnt = 0;   loss_cnt = 0;      total_cnt = 0
    sum_net = 0.0; max_net = -math.inf; min_net = math.inf
    final_cum = 100.0; spot_cum = 100.0
    min_entry_ms = None; max_exit_ms = None
    spot_sum_gated = 0.0
    ce_sum = 0.0; pe_sum = 0.0; fut_sum = 0.0
    ce_pct = 0.0; pe_pct = 0.0; spot_pct = 0.0

    def _parse_date_ms(v):
        d = _parse_date(v)
        return d.timestamp() * 1000 if d else None

    for t in cleaned:
        ce  = _to_num(t.get("CE P&L"));   ce_sum  += ce  if ce  is not None else 0
        pe  = _to_num(t.get("PE P&L"));   pe_sum  += pe  if pe  is not None else 0
        fu  = _to_num(t.get("FUT P&L"));  fut_sum += fu  if fu  is not None else 0
        cep = _to_num(t.get("CE P&L %")); ce_pct  += cep if cep is not None else 0
        pep = _to_num(t.get("PE P&L %")); pe_pct  += pep if pep is not None else 0
        spp = _to_num(t.get("Spot P&L %")); spot_pct += spp if spp is not None else 0

    for t in cleaned:
        p = _to_num(t.get("% P&L")); n = _to_num(t.get("Net P&L"))
        if p is not None and math.isfinite(p):
            sum_pct += p; total_cnt += 1
            if p > 0:  sum_pos_pct += p; win_cnt  += 1
            elif p < 0: sum_neg_pct += p; loss_cnt += 1
        if n is not None and math.isfinite(n):
            sum_net += n
            if n > max_net: max_net = n
            if n < min_net: min_net = n
            sp = _to_num(t.get("Spot P&L"))
            if sp is not None: spot_sum_gated += sp
        cum = _to_num(t.get("Cumulative"))
        if cum is not None and math.isfinite(cum): final_cum = cum
        es = _to_num(t.get("Entry Spot")); xs = _to_num(t.get("Exit Spot"))
        if n is not None and math.isfinite(n) and es and xs and es > 0:
            spot_cum *= (xs / es)
        ed = _parse_date_ms(t.get("Entry Date")); xd = _parse_date_ms(t.get("Exit Date"))
        if ed and (min_entry_ms is None or ed < min_entry_ms): min_entry_ms = ed
        if xd and (max_exit_ms  is None or xd > max_exit_ms):  max_exit_ms  = xd

    if not math.isfinite(max_net): max_net = 0.0
    if not math.isfinite(min_net): min_net = 0.0

    avg_win_pct   = (sum_pos_pct / win_cnt)   if win_cnt  > 0 else 0.0
    avg_loss_pct  = (sum_neg_pct / loss_cnt)  if loss_cnt > 0 else 0.0
    win_rate      = (win_cnt  / total_cnt * 100) if total_cnt > 0 else 0.0
    loss_rate     = (loss_cnt / total_cnt * 100) if total_cnt > 0 else 0.0
    avg_net       = (sum_net  / total_cnt)       if total_cnt > 0 else 0.0
    if avg_loss_pct != 0:
        expectancy = (
            ((win_rate / 100) * avg_win_pct - (loss_rate / 100) * abs(avg_loss_pct))
            / abs(avg_loss_pct)
        )
    else:
        expectancy = 0.0
    years = (
        (max_exit_ms - min_entry_ms) / (365.25 * 86400 * 1000)
        if (min_entry_ms is not None and max_exit_ms is not None) else 0.0
    )
    opt_cagr  = (math.pow(final_cum / 100, 1 / years) - 1) * 100 if years > 0 and final_cum > 0 else 0.0
    spot_cagr = (math.pow(spot_cum / 100,  1 / years) - 1) * 100 if years > 0 and spot_cum  > 0 else 0.0
    max_dd_pct   = _to_num(S.get("max_dd_pct")) or 0.0
    max_dd_pts   = _to_num(S.get("max_dd_pts")) or 0.0
    car_mdd      = (opt_cagr / 100) / abs(max_dd_pct) if max_dd_pct != 0 else 0.0
    max_win_str  = _to_num(S.get("max_win_streak"))  or 0
    max_loss_str = _to_num(S.get("max_loss_streak")) or 0
    mdd_start    = S.get("mdd_start_date") or ""
    mdd_end      = S.get("mdd_end_date")   or ""
    mdd_dur      = _to_num(S.get("mdd_duration_days")) or ""

    opt_sum = (
        (ce_sum + pe_sum)   if (has_calls and has_puts) else
        pe_sum              if has_puts else
        ce_sum              if has_calls else
        fut_sum             if has_futures else sum_net
    )
    roi_pct = (opt_sum / spot_sum_gated * 100) if spot_sum_gated != 0 else 0.0

    # Live DD outlier analysis — iterate trades chronologically (same order as
    # the cleaned rows / Live DD pass above) so cascade trades are placed in
    # the right time sequence for the outlier-stripped DD computation.
    trade_pairs = []
    _seen2: set = set()
    _chron_keys: List[str] = []
    for _cr in cleaned:
        _k = str(_cr.get("Trade") or _cr.get("trade") or 1)
        if _k not in _seen2 and _k in tm:
            _seen2.add(_k)
            _chron_keys.append(_k)
    # Fallback to integer ordering for any trades not represented in cleaned
    # (shouldn't normally happen, but keeps logic safe).
    for _k in tm.keys():
        if _k not in _seen2:
            _seen2.add(_k); _chron_keys.append(_k)
    for k in _chron_keys:
        t2 = tm[k]
        pct_v = t2.get("pct");    pct_v = pct_v if isinstance(pct_v, float) and math.isfinite(pct_v) else None
        ldd_v = t2.get("actualLDD"); ldd_v = ldd_v if isinstance(ldd_v, float) and math.isfinite(ldd_v) else None
        if pct_v is not None:
            trade_pairs.append({"pct": pct_v, "ldd": ldd_v, "idx": len(trade_pairs)})

    n_trades  = len(trade_pairs)
    by_pct_desc = sorted(trade_pairs, key=lambda x: -x["pct"])

    _p1  = by_pct_desc[0]["pct"] if n_trades > 0 else 0.0
    _p2  = _p1 + by_pct_desc[1]["pct"] if n_trades > 1 else _p1
    _p3  = _p2 + by_pct_desc[2]["pct"] if n_trades > 2 else _p2
    _n1  = by_pct_desc[n_trades - 1]["pct"] if n_trades > 0 else 0.0
    _n2  = _n1 + by_pct_desc[n_trades - 2]["pct"] if n_trades > 1 else _n1
    _n3  = _n2 + by_pct_desc[n_trades - 3]["pct"] if n_trades > 2 else _n2
    total_pct_sum = sum(p["pct"] for p in trade_pairs)
    pct_no_o1 = total_pct_sum - _p1 - _n1
    pct_no_o2 = total_pct_sum - _p2 - _n2
    pct_no_o3 = total_pct_sum - _p3 - _n3

    def _ldd_exc_stats(exc_top, exc_bot):
        exc_idx = {
            *[p["idx"] for p in by_pct_desc[:exc_top]],
            *[p["idx"] for p in by_pct_desc[max(0, n_trades - exc_bot):]],
        }
        filtered = [p for p in trade_pairs if p["idx"] not in exc_idx and p["ldd"] is not None]
        if not filtered:
            return (0.0, 0.0)
        ldds = [p["ldd"] for p in filtered]
        return (round(min(ldds), 2), round(sum(ldds) / len(ldds), 2))

    all_ldds   = [p["ldd"] for p in trade_pairs if p["ldd"] is not None]
    live_dd_min = round(min(all_ldds), 2) if all_ldds else 0.0
    live_dd_avg = round(sum(all_ldds) / len(all_ldds), 2) if all_ldds else 0.0
    ldd_no_o1  = _ldd_exc_stats(1, 1)
    ldd_no_o2  = _ldd_exc_stats(2, 2)
    ldd_no_o3  = _ldd_exc_stats(3, 3)
    car_mdd_live = (opt_cagr / 100) / abs(live_dd_min) if live_dd_min != 0 else 0.0

    def _fmt_pct(v, signed=True):
        prefix = "+" if (signed and v >= 0) else ""
        return f"{prefix}{float(v):.2f}%"

    def _fmt_cur(v):
        return f"₹{float(v):,.2f}"

    # ── Row 1: Title ───────────────────────────────────────────────────────────
    _title("  BACKTEST SUMMARY REPORT", 1)

    # ── Row 2: Subtitle ───────────────────────────────────────────────────────
    _merge(2)
    parts = []
    if combo_label: parts.append(combo_label)
    if from_date or to_date:
        parts.append(f"{from_date or ''}{' → ' if from_date and to_date else ''}{to_date or ''}")
    parts.append(f"Generated: {datetime.now().strftime('%d %b %Y')}")
    c2 = ws.cell(row=2, column=1, value="   ·   ".join(parts))
    c2.font = _font(size=10, color="555555"); c2.alignment = _CENTER
    c2.fill = _fill(_SUB_HDR_BG)
    ws.row_dimensions[2].height = 16

    r = 4
    # ── SECTION 1: Performance Overview ──────────────────────────────────────
    _section("PERFORMANCE OVERVIEW", r); r += 1

    _kv("Overall Profit", _fmt_pct(sum_pct), r, "A", False, _GREEN_TX if sum_pct >= 0 else _RED_TX)
    _kv("No. of Trades",  int(total_cnt),   r, "D", False, _DARK_TXT); r += 1

    _kv("Win %",  f"{win_rate:.2f}%",  r, "A", True, _GREEN_TX)
    _kv("Loss %", f"{loss_rate:.2f}%", r, "D", True, _RED_TX); r += 1

    _kv("Avg Profit on Winners", f"{avg_win_pct:.2f}%",  r, "A", False, _GREEN_TX)
    _kv("Avg Loss on Losers",    f"{avg_loss_pct:.2f}%", r, "D", False, _RED_TX); r += 1

    sign = "+" if avg_net >= 0 else ""
    _kv("Avg Profit per Trade",  f"{sign}{avg_net:.2f}", r, "A", True, _GREEN_TX if avg_net >= 0 else _RED_TX)
    _kv("Expectancy Ratio",      f"{expectancy:.4f}",    r, "D", True, _GREEN_TX if expectancy >= 0 else _RED_TX); r += 1

    _kv("Max Profit (Single Trade)", _fmt_cur(max_net), r, "A", False, _GREEN_TX)
    _kv("Max Loss (Single Trade)",   _fmt_cur(min_net), r, "D", False, _RED_TX); r += 1

    _kv("CAGR (Options)", _fmt_pct(opt_cagr),  r, "A", True, _GREEN_TX if opt_cagr  >= 0 else _RED_TX)
    _kv("CAGR (Spot)",    _fmt_pct(spot_cagr), r, "D", True, _GREEN_TX if spot_cagr >= 0 else _RED_TX); r += 1

    r += 1  # blank

    # ROI vs Spot table
    def _hdr_cell(col, txt, rn):
        ci = ord(col.upper()) - ord("A") + 1
        c = ws.cell(row=rn, column=ci, value=txt)
        c.font = _font(bold=True, size=10, color=_WHITE_TXT)
        c.fill = _fill(_HEADER_BG)
        c.alignment = _CENTER; c.border = _border()

    _hdr_cell("A", "Type", r); _hdr_cell("B", "Sum", r); _hdr_cell("C", "%", r)
    ws.merge_cells(f"D{r}:E{r}")
    _hdr_cell("D", "ROI vs Spot", r)
    ws.row_dimensions[r].height = 20
    spot_row = r; r += 1

    ws.merge_cells(f"D{spot_row + 1}:E{spot_row + 1}")
    roi_c = ws.cell(row=spot_row + 1, column=4, value=_fmt_pct(roi_pct))
    roi_c.font = _font(bold=True, size=11, color=_GREEN_TX if roi_pct >= 0 else _RED_TX)
    roi_c.fill = _fill(_WHITE); roi_c.alignment = _CENTER; roi_c.border = _border()

    def _type_row(label, value, pct_val):
        lc = ws.cell(row=r, column=1, value=label)
        vc = ws.cell(row=r, column=2, value=f"{float(value):,.2f}")
        lc.font = _font(bold=True, size=10, color=_DARK2_TXT); lc.fill = _fill(_LABEL_BG)
        lc.alignment = _LEFT; lc.border = _border()
        vc.font = _font(bold=True, size=10, color=_GREEN_TX if value >= 0 else _RED_TX)
        vc.fill = _fill(_WHITE); vc.alignment = _LEFT; vc.border = _border()
        if pct_val is not None:
            sign2 = "+" if pct_val >= 0 else ""
            pc = ws.cell(row=r, column=3, value=f"{sign2}{float(pct_val):.2f}%")
            pc.font = _font(bold=True, size=10, color=_GREEN_TX if pct_val >= 0 else _RED_TX)
            pc.fill = _fill(_WHITE); pc.alignment = _LEFT; pc.border = _border()
        ws.row_dimensions[r].height = 18

    # Use backend summary for Spot P&L sum and Spot P&L %.  After the engine
    # fix that puts Spot P&L only on first-leg rows, the local sums match
    # backend; but reading from `summary.*` keeps all three Excel builders
    # consistent (single source of truth, set in base.compute_analytics).
    _spot_sum_summary = _to_num(S.get("spot_change"))
    if _spot_sum_summary is None: _spot_sum_summary = spot_sum_gated
    _spot_pct_summary = _to_num(S.get("spot_change_pct"))
    if _spot_pct_summary is None: _spot_pct_summary = spot_pct * 100
    _type_row("Spot P&L", _spot_sum_summary, _spot_pct_summary); r += 1
    if has_calls:   _type_row("CE P&L",        ce_sum,             ce_pct * 100);           r += 1
    if has_puts:    _type_row("PE P&L",         pe_sum,             pe_pct * 100);           r += 1
    if has_futures: _type_row("FUT P&L",        fut_sum,            None);                   r += 1
    if has_calls and has_puts:
        _type_row("CE + PE P&L", ce_sum + pe_sum, (ce_pct + pe_pct) * 100); r += 1
    _type_row("Net P&L", sum_net, sum_pct); r += 1

    r += 1

    # ── SECTION 2: Risk Metrics ───────────────────────────────────────────────
    _section("RISK METRICS", r); r += 1
    _kv("Max Drawdown",  f"{max_dd_pct:.2f}%", r, "A", False, _RED_TX)
    _kv("Max DD Days",   str(mdd_dur or "—"),  r, "D", False, _RED_TX); r += 1

    dd_period = f"{mdd_start}  →  {mdd_end}" if (mdd_start and mdd_end) else "—"
    ws.merge_cells(f"A{r}:E{r}")
    ddc = ws.cell(row=r, column=1, value=f"Drawdown Period:  {dd_period}")
    ddc.font = _font(bold=True, size=10, color=_RED_TX)
    ddc.fill = _fill(_RED_BG); ddc.alignment = _CENTER; ddc.border = _border()
    ws.row_dimensions[r].height = 18; r += 1

    _kv("Return / MaxDD", f"{car_mdd:.4f}", r, "A", True, _GREEN_TX if car_mdd >= 0 else _RED_TX); r += 1

    r += 1

    # ── SECTION 3: Consistency & Streaks ─────────────────────────────────────
    _section("CONSISTENCY & STREAKS", r); r += 1
    _kv("Max Win Streak",    f"{int(max_win_str)} trades",  r, "A", False, _GREEN_TX)
    _kv("Max Losing Streak", f"{int(max_loss_str)} trades", r, "D", False, _RED_TX); r += 1
    r += 1

    # ── SECTION 4: Monthly Returns ────────────────────────────────────────────
    _section("MONTHLY RETURNS (₹ Net P&L)", r); r += 1

    MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    mth_hdr = ["Year", *MONTHS, "Total", "Max DD", "DD Days", "R/MDD"]

    # Set wider column widths for month table
    for ci in range(1, len(mth_hdr) + 1):
        col_key = get_column_letter(ci)
        if ci == 1: ws.column_dimensions[col_key].width = 8
        elif ci <= 13: ws.column_dimensions[col_key].width = 9
        elif ci == 14: ws.column_dimensions[col_key].width = 10
        elif ci == 15: ws.column_dimensions[col_key].width = 18
        else: ws.column_dimensions[col_key].width = 10

    by_ym:     Dict[str, List[float]] = {}
    by_ym_pct: Dict[str, List[float]] = {}
    by_yr_max_dd: Dict[str, float] = {}

    def _ym(v):
        d = _parse_date(v)
        if not d: return None
        return str(d.year), d.month - 1

    for t in cleaned:
        net_v = _to_num(t.get("Net P&L"))
        if net_v is None: continue
        spot_v = _to_num(t.get("Entry Spot")) or 0.0
        pct_v  = (net_v / spot_v * 100) if spot_v > 0 else 0.0
        ym = _ym(t.get("Exit Date"))
        if not ym: continue
        yr, mi = ym
        by_ym.setdefault(yr, [0.0]*12)[mi]     += net_v
        by_ym_pct.setdefault(yr, [0.0]*12)[mi] += pct_v
        dd_v = _to_num(t.get("%DD"))
        if dd_v is not None:
            if yr not in by_yr_max_dd or dd_v < by_yr_max_dd[yr]:
                by_yr_max_dd[yr] = dd_v

    def _render_mth_rows(data_map, is_pct):
        nonlocal r
        for yi, (yr, mos) in enumerate(sorted(data_map.items())):
            total = sum(mos)
            max_dd_yr = by_yr_max_dd.get(yr)
            r_mdd = (abs(total) / abs(max_dd_yr)) if (max_dd_yr and max_dd_yr != 0 and total != 0) else ""
            row_data = [yr, *[round(v, 2) for v in mos], round(total, 2),
                        round(max_dd_yr, 2) if max_dd_yr is not None else "", "", r_mdd]
            ws.row_dimensions[r].height = 18
            for ci2, val in enumerate(row_data, 1):
                c = ws.cell(row=r, column=ci2, value=val)
                is_val  = 2 <= ci2 <= 13
                is_tot  = ci2 == 14
                if is_pct and (is_val or is_tot) and isinstance(val, (int, float)):
                    c.value = val / 100
                    c.number_format = "0.00%"
                    num_v = val
                else:
                    num_v = val if isinstance(val, (int, float)) else None
                if (is_val or is_tot) and num_v is not None and num_v != 0:
                    c.font = _font(bold=True, size=10, color=_GREEN_TX if num_v >= 0 else _RED_TX)
                    c.fill = _fill(_GREEN_BG if num_v >= 0 else _RED_BG)
                elif ci2 == 1:
                    c.font = _font(bold=True, size=10, color=_SUB_HDR_TX)
                    c.fill = _fill(_SUB_HDR_BG)
                else:
                    c.font = _font(size=10)
                    c.fill = _fill(_WHITE if yi % 2 == 0 else _ALT_ROW)
                c.alignment = _CENTER; c.border = _border()
            r += 1

    # Month header
    ws.row_dimensions[r].height = 20
    for ci2, h in enumerate(mth_hdr, 1):
        c = ws.cell(row=r, column=ci2, value=h)
        c.font = _font(bold=True, size=10, color=_WHITE_TXT)
        c.fill = _fill(_HEADER_BG); c.alignment = _CENTER; c.border = _border()
    r += 1
    _render_mth_rows(by_ym, False)

    r += 1
    _section("MONTHLY RETURNS (% Net P&L)", r); r += 1
    ws.row_dimensions[r].height = 20
    for ci2, h in enumerate(mth_hdr, 1):
        c = ws.cell(row=r, column=ci2, value=h)
        c.font = _font(bold=True, size=10, color=_WHITE_TXT)
        c.fill = _fill(_HEADER_BG); c.alignment = _CENTER; c.border = _border()
    r += 1
    _render_mth_rows(by_ym_pct, True)

    r += 1

    # ── SECTION 5: Live DD & Outlier Analysis ────────────────────────────────
    _section("LIVE DD & OUTLIER ANALYSIS", r); r += 1
    _kv("Actual Live DD (min)", f"{live_dd_min:.2f}%", r, "A", False, _RED_TX)
    _kv("Avg Actual Live DD",   f"{live_dd_avg:.2f}%", r, "D", False, _RED_TX); r += 1
    _kv("CAR/MDD (Booked)",     f"{car_mdd:.4f}",       r, "A", True,  _GREEN_TX if car_mdd     >= 0 else _RED_TX)
    _kv("CAR/MDD Live",         f"{car_mdd_live:.4f}",  r, "D", True,  _GREEN_TX if car_mdd_live >= 0 else _RED_TX); r += 1

    r += 1
    _kv("+ve Outlier 1", _fmt_pct(_p1), r, "A", False, _GREEN_TX)
    _kv("-ve Outlier 1", _fmt_pct(_n1), r, "D", False, _RED_TX); r += 1
    _kv("Actual Live DD Without Outlier 1",     f"{ldd_no_o1[0]:.2f}%", r, "A", True, _RED_TX)
    _kv("Avg Actual Live DD Without Outlier 1", f"{ldd_no_o1[1]:.2f}%", r, "D", True, _RED_TX); r += 1
    _kv("+ve Outlier 2", _fmt_pct(_p2), r, "A", False, _GREEN_TX)
    _kv("-ve Outlier 2", _fmt_pct(_n2), r, "D", False, _RED_TX); r += 1
    _kv("Actual Live DD Without Outlier 2",     f"{ldd_no_o2[0]:.2f}%", r, "A", True, _RED_TX)
    _kv("Avg Actual Live DD Without Outlier 2", f"{ldd_no_o2[1]:.2f}%", r, "D", True, _RED_TX); r += 1
    _kv("+ve Outlier 3", _fmt_pct(_p3), r, "A", False, _GREEN_TX)
    _kv("-ve Outlier 3", _fmt_pct(_n3), r, "D", False, _RED_TX); r += 1
    _kv("Actual Live DD Without Outlier 3",     f"{ldd_no_o3[0]:.2f}%", r, "A", True, _RED_TX)
    _kv("Avg Actual Live DD Without Outlier 3", f"{ldd_no_o3[1]:.2f}%", r, "D", True, _RED_TX); r += 1

    r += 1
    for si, (label, val) in enumerate([
        ("CE + PE + P&L % Without Top 1 Outliers", pct_no_o1),
        ("CE + PE + P&L % Without Top 2 Outliers", pct_no_o2),
        ("CE + PE + P&L % Without Top 3 Outliers", pct_no_o3),
    ]):
        ws.merge_cells(f"A{r}:D{r}")
        lc = ws.cell(row=r, column=1, value=label)
        lc.font = _font(bold=True, size=10, color=_DARK2_TXT)
        lc.fill = _fill(_LABEL_BG if si % 2 == 0 else _ALT_ROW)
        lc.alignment = _LEFT; lc.border = _border()
        vc = ws.cell(row=r, column=5, value=_fmt_pct(val))
        vc.font = _font(bold=True, size=10, color=_GREEN_TX if val >= 0 else _RED_TX)
        vc.fill = _fill(_WHITE if si % 2 == 0 else _ALT_ROW)
        vc.alignment = _CENTER; vc.border = _border()
        ws.row_dimensions[r].height = 18; r += 1


# ── Public API ────────────────────────────────────────────────────────────────

def build_combo_xlsx(
    trades_df: pd.DataFrame,
    summary: Dict[str, Any],
    combo_label: str = "",
    from_date: str = "",
    to_date: str = "",
) -> bytes:
    """
    Build a complete XLSX workbook (Trade Sheet + Summary) from a trades DataFrame
    and a summary dict. Returns raw bytes suitable for embedding in a ZIP.
    """
    if trades_df is None or (hasattr(trades_df, "empty") and trades_df.empty):
        rows: List[Dict] = []
    else:
        rows = trades_df.where(trades_df.notna(), None).to_dict("records")

    wb = Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    key_order, has_calls, has_puts, has_futures = _build_key_order(rows)
    tm, _grouped = _aggregate_trades(rows)
    cleaned = _build_cleaned_rows(rows, key_order, tm)

    _write_trade_sheet(wb, cleaned, key_order)
    _write_summary_sheet(
        wb, cleaned, summary, tm,
        combo_label, from_date, to_date,
        has_calls, has_puts, has_futures,
    )

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
