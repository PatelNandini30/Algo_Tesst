"""
WOW & MOM summary — Python port of the frontend `wowMom.js` + `wowMomSheet.js`.

Produces the same Week-on-Week / Month-on-Month tables the backtest and the
optimizer per-combo tradesheets show, so all three outputs match:
  • backtest export        (frontend ResultsPanel.jsx → wowMomSheet.js)
  • optim per-combo export (frontend buildTradeExcel.js → wowMomSheet.js,
                            and backend ZIP excel_builder.py → here)
  • optim merged summary   (backend → here, one stacked block per combo)

Computation mirrors wowMom.js exactly (verified to ~1e-15 and against the
research team's hand-corrected "Rectify" sheet). The WOW drawdown CONTINUES
across blank weeks (gaps are skipped, not breaks). Total / Max DD / R/MDD are
written as computed VALUES (the codebase convention) — identical displayed
numbers to the JS formula results.
"""
from __future__ import annotations

import math
from datetime import datetime
from statistics import mode
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

WOW_RF = 0.06 / 52
MOM_RF = 0.06 / 12
WOW_NANN = 52
MOM_NANN = 12
SLOPE_CAP = 307


# ── date helpers ───────────────────────────────────────────────────────────
def _parse_date(v) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v
    if v is None or v == "":
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d",
                "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _iso_year_week(d: datetime) -> Tuple[int, int]:
    iso = d.isocalendar()
    return int(iso[0]), int(iso[1])


def _first_thu_week(year: int, month: int) -> int:
    d = datetime(year, month, 1)
    dow = d.weekday()              # Mon=0
    shift = (3 - dow) % 7
    d = datetime.fromordinal(d.toordinal() + shift)
    return _iso_year_week(d)[1]


def get_month_maps(years: List[int], n_weeks: int) -> Tuple[Dict[int, int], Dict[int, int]]:
    sw: Dict[int, int] = {}
    for m in range(1, 13):
        weeks = [_first_thu_week(y, m) for y in years] if years else [1]
        try:
            sw[m] = mode(weeks)
        except Exception:
            sw[m] = weeks[0]
    ew: Dict[int, int] = {}
    for m in range(1, 12):
        ew[m] = sw[m + 1] - 1
    ew[12] = n_weeks
    return sw, ew


# ── ratio engine (port of compute_ratios) ──────────────────────────────────
def _std(arr: List[float], mean: Optional[float] = None) -> float:
    if not arr:
        return 0.0
    m = (sum(arr) / len(arr)) if mean is None else mean
    return math.sqrt(sum((x - m) ** 2 for x in arr) / len(arr))


def compute_ratios(returns: List[float], rf: float, n_ann: int) -> Optional[Dict[str, float]]:
    if len(returns) < 3:
        return None
    R = returns
    N = len(R)
    avg = sum(R) / N
    sd = _std(R, avg)
    pos = [v for v in R if v > 0]
    neg = [v for v in R if v < 0]
    wp = len(pos) / N
    wa = (sum(pos) / len(pos)) if pos else 0.0
    lp = len(neg) / N
    la = (sum(neg) / len(neg)) if neg else 0.0
    exp = ((wp / abs(la)) * wa - lp) if la else 0.0
    sd_neg = _std(neg) if neg else 0.0
    sh = ((avg - rf) / sd) * math.sqrt(n_ann) if sd else 0.0
    so = ((avg - rf) / sd_neg) * math.sqrt(n_ann) if sd_neg else 0.0
    sqn = (avg / sd) * math.sqrt(N) if sd else 0.0

    n_s = min(N, SLOPE_CAP)
    eq = [100.0]
    for v in R:
        eq.append(eq[-1] * (1 + v))
    Y = eq[1:n_s + 1]
    X = list(range(1, n_s + 1))
    mx = sum(X) / n_s
    my = sum(Y) / n_s
    sxy = sum((X[i] - mx) * (Y[i] - my) for i in range(n_s))
    sxx = sum((X[i] - mx) ** 2 for i in range(n_s))
    slope = (sxy / sxx) if sxx else 0.0
    intercept = my - slope * mx
    sse = sum((Y[i] - (slope * X[i] + intercept)) ** 2 for i in range(n_s))
    st_err = math.sqrt(max(sse / (n_s - 2), 1e-30))
    base = (slope * math.sqrt(sxx)) / st_err
    k1 = base / math.sqrt(N)
    k2 = base / N
    k3 = (base * math.sqrt(252)) / N

    c = 1.0
    for v in R:
        c *= (1 + v)
    cg = c ** (n_ann / N) - 1
    return dict(wp=wp, wa=wa, lp=lp, la=la, exp=exp, sh=sh, so=so,
                k1=k1, k2=k2, k3=k3, sqn=sqn, cg=cg, n=N)


def wow_year_drawdown(week_map: Dict[int, float], nw: int) -> Dict[str, Optional[float]]:
    """Running cumulative drawdown over present weeks; gaps are SKIPPED, not breaks."""
    vals = [(w, week_map[w]) for w in range(1, nw + 1) if week_map.get(w) is not None]
    if not vals:
        return {"maxdd": None, "start": None, "end": None}
    g_min = 0.0
    best: Tuple[Optional[int], Optional[int]] = (None, None)
    cum = 0.0
    started = False
    s_start = None
    s_min = 0.0
    s_mc = None
    for col, val in vals:
        if not started:
            if val < 0:
                started, s_start, cum, s_min, s_mc = True, col, val, val, col
        else:
            cum += val
            if cum >= 0:
                if s_min < g_min:
                    g_min, best = s_min, (s_start, s_mc)
                started, cum, s_start, s_min, s_mc = False, 0.0, None, 0.0, None
            elif cum < s_min:
                s_min, s_mc = cum, col
    if started and s_min < g_min:
        g_min, best = s_min, (s_start, s_mc)
    sc, ec = best
    if sc is None:
        return {"maxdd": None, "start": None, "end": None}
    return {"maxdd": round(g_min, 4), "start": sc, "end": ec}


# ── bucketing (port of buildWowMom) ────────────────────────────────────────
def _to_num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace(",", "").replace("%", "").replace("₹", "").strip())
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def build_wow_mom(cleaned: List[Dict], ret_field: str, dd_field: str,
                  dd_is_percent: bool, live_field: Optional[str] = None,
                  yearly: bool = False) -> Dict[str, Any]:
    wow: Dict[int, Dict[int, float]] = {}
    mom_monthly: Dict[int, Dict[int, float]] = {}
    mom_dd: Dict[int, List[float]] = {}
    # Live DD % — MIN of "Actual Live DD" (percent points → /100) per year.
    # WOW is bucketed by Expiry year, MOM by Exit year (to match each block's
    # year rows). Written between Max DD and R/MDD; R/MDD still uses Max DD.
    wow_live: Dict[int, List[float]] = {}
    mom_live: Dict[int, List[float]] = {}
    n_trades = 0

    for t in cleaned:
        raw = t.get(ret_field)
        if raw == "" or raw is None:
            continue
        ret = _to_num(raw)
        if ret is None:
            continue
        n_trades += 1
        dec = ret / 100.0

        live_dec = None
        if live_field:
            live_num = _to_num(t.get(live_field))
            if live_num is not None:
                live_dec = live_num / 100.0

        # WOW week identity.
        #
        # Normally the Expiry: for a weekly/monthly strategy the trade IS its
        # contract, so Expiry is the trade's natural week — and it deliberately
        # keeps a contract's P&L in ONE week even when a T-n exit lands in the
        # previous calendar week.
        #
        # Under YEARLY that same principle breaks it: the contract IS the whole
        # year, so every trade shares one Expiry and the entire year collapses
        # into a single cell (2019-12-26 → ISO week 52; 2020-12-31 → week 53).
        # There the roll segment is the week, so the week identity comes from the
        # Exit Date — mirroring what MOM already does below.
        _wk_src = t.get("Exit Date") if yearly else t.get("Expiry")
        e = _parse_date(_wk_src)
        if e is not None:
            y, w = _iso_year_week(e)
            wow.setdefault(y, {})
            wow[y][w] = wow[y].get(w, 0.0) + dec
            if live_dec is not None:
                wow_live.setdefault(y, []).append(live_dec)

        x = _parse_date(t.get("Exit Date"))
        if x is not None:
            y = x.year
            mi = x.month - 1
            mom_monthly.setdefault(y, {})
            mom_monthly[y][mi] = mom_monthly[y].get(mi, 0.0) + dec
            dd_raw = t.get(dd_field)
            dd_num = _to_num(dd_raw)
            if dd_num is not None:
                dd_dec = (dd_num / 100.0) if dd_is_percent else dd_num
                mom_dd.setdefault(y, []).append(dd_dec)
            if live_dec is not None:
                mom_live.setdefault(y, []).append(live_dec)

    for y in list(wow.keys()):
        for w in list(wow[y].keys()):
            if abs(wow[y][w]) <= 1e-9:
                del wow[y][w]
        if not wow[y]:
            del wow[y]

    mom: Dict[int, Dict] = {}
    for y in mom_monthly:
        months = {}
        for mi in range(12):
            v = mom_monthly[y].get(mi)
            if v is not None and abs(v) > 1e-9:
                months[MONTHS[mi]] = v
        if not months:
            continue
        total = sum(months.values())
        dd_arr = mom_dd.get(y, [])
        if dd_arr:
            maxdd = min(dd_arr)
        else:
            # running cumulative DD over the monthly series (fallback)
            cum = 0.0
            mdd = 0.0
            for mn in MONTHS:
                cum += months.get(mn, 0.0)
                if cum > 0:
                    cum = 0.0
                mdd = min(mdd, cum)
            maxdd = mdd
        live_arr = mom_live.get(y, [])
        livedd = min(live_arr) if live_arr else None
        mom[y] = {"months": months, "total": total, "maxdd": maxdd, "livedd": livedd}

    wow_live_min = {y: (min(v) if v else None) for y, v in wow_live.items()}
    wow_years = sorted(wow.keys())
    mom_years = sorted(mom.keys())
    return {"wow": wow, "mom": mom, "wow_years": wow_years,
            "mom_years": mom_years, "n_weeks": 53, "n_trades": n_trades,
            "wow_live": wow_live_min}


def flat_weekly(wow: Dict[int, Dict[int, float]]) -> List[float]:
    return [wow[y][w] for y in wow for w in wow[y]]


def flat_monthly(mom: Dict[int, Dict]) -> List[float]:
    return [mom[y]["months"][k] for y in mom for k in mom[y]["months"]]


# ── title (port of buildWowMomTitle) ───────────────────────────────────────
def _fmt_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(round(float(v), 2))


def build_wow_mom_title(config: Optional[Dict]) -> str:
    if not config:
        return "Strategy"
    legs = config.get("legs") or []
    opt_leg = None
    for l in legs:
        if l.get("segment") not in ("midcap100", "futures") and l.get("option_type"):
            opt_leg = l
            break
    if opt_leg is None and legs:
        opt_leg = legs[0]
    cepe, strike = "", ""
    if opt_leg:
        o = str(opt_leg.get("option_type") or "").lower()
        cepe = "CE" if o == "call" else "PE" if o == "put" else (o.upper() if o else "")
        criteria = opt_leg.get("strike_criteria") or "strike_type"
        if criteria == "pct_of_atm":
            pct_val = _to_num(opt_leg.get("pct_value")) or 0
            if not pct_val:
                strike = "ATM"
            else:
                moneyness = str(opt_leg.get("pct_atm_moneyness") or "OTM").upper()
                strike = f"{_fmt_num(pct_val)}% {moneyness}"
        elif criteria == "atm_straddle_prem_pct":
            strike = "STRADDLE"
        else:
            strike = str(opt_leg.get("strike_type") or "ATM").upper()
    adj = "No Adj"
    if config.get("spotAdjustmentEnabled"):
        d = config.get("spotAdjustmentDirection") or "rise"
        val = config.get("spotAdjustmentValue")
        unit = "%" if (config.get("spotAdjustmentUnits") or "percent") == "percent" else "pts"
        val_str = _fmt_num(val) if val is not None else ""
        word = "Rise or Fall" if d == "both" else "Fall" if d == "fall" else "Rise"
        adj = f"{word}{(' ' + val_str + unit) if val_str else ''}"
    left = " ".join(x for x in (cepe, strike) if x) or "Strategy"
    return f"{left} | {adj}"


# ── styling ────────────────────────────────────────────────────────────────
_NAVY_BG, _NAVY_TX = "FF1F3864", "FFFFFFFF"
_SECTION_BG = "FF2C5F8A"
_HEADER_BG, _HEADER_TX = "FF34495E", "FFFFFFFF"
_SUB_BG, _SUB_TX = "FFD6E4F7", "FF1F3864"
_GREEN_BG, _GREEN_TX = "FFD4EFDF", "FF1E7E34"
_RED_BG, _RED_TX = "FFFDE8E8", "FFC0392B"
_LABEL_BG = "FFF2F6FA"
_BORDER_CLR = "FFB0C4D8"
_BLACK = "FF000000"

PCT_FMT, RAT_FMT, K_FMT, INT_FMT = "0.00%", "0.00", "0.0000", "0"
_CENTER = Alignment(horizontal="center", vertical="center")
_LEFT = Alignment(horizontal="left", vertical="center")

STAT_LBL = ["Win %", "Win avg", "Loss %", "Loss Avg", "Expectancy",
            "No. of Trades", "Sharpe", "Sortino", "K1", "K2", "K3", "SQN", "CAGR"]
STAT_FMT = [PCT_FMT, PCT_FMT, PCT_FMT, PCT_FMT, K_FMT, INT_FMT,
            RAT_FMT, RAT_FMT, K_FMT, K_FMT, K_FMT, RAT_FMT, PCT_FMT]


def _thin(color: str = _BORDER_CLR) -> Border:
    s = Side(style="thin", color=color)
    return Border(top=s, left=s, bottom=s, right=s)


def _sign_fill(v):
    if v is None:
        return None
    return _GREEN_BG if v >= 0 else _RED_BG


def _sign_tx(v):
    if v is None:
        return _BLACK
    return _GREEN_TX if v >= 0 else _RED_TX


class _Sheet:
    """Thin cell helper bound to a worksheet (mirrors hCell/vCell in JS)."""

    def __init__(self, ws):
        self.ws = ws

    def h(self, r, c, val, *, size=9, tx=_HEADER_TX, bg=_HEADER_BG, align=None):
        cell = self.ws.cell(r, c, val)
        cell.font = Font(bold=True, size=size, color=tx, name="Calibri")
        cell.fill = PatternFill("solid", fgColor=bg)
        cell.alignment = align or _CENTER
        cell.border = _thin()
        return cell

    def v(self, r, c, val, fmt=None, *, size=9, tx=None, bg=None):
        cell = self.ws.cell(r, c)
        cell.value = "" if val is None else val
        cell.font = Font(bold=False, size=size, color=(tx or _BLACK), name="Calibri")
        if fmt and isinstance(val, (int, float)):
            cell.number_format = fmt
        cell.alignment = _CENTER
        cell.border = _thin()
        if bg:
            cell.fill = PatternFill("solid", fgColor=bg)
        return cell


def _stat_vals(m, n):
    if m:
        return [m["wp"], m["wa"], m["lp"], m["la"], m["exp"], n,
                m["sh"], m["so"], m["k1"], m["k2"], m["k3"], m["sqn"], m["cg"]]
    return [None, None, None, None, None, n, None, None, None, None, None, None, None]


def _avg(a):
    return (sum(a) / len(a)) if a else None


def _write_stat_header(S, wm, title, base_row, title_merge_cols, stat_col0, stats, cc=0):
    S.h(base_row, cc + 1, title, size=10, tx=_NAVY_TX, bg=_NAVY_BG, align=_LEFT)
    S.ws.merge_cells(start_row=base_row, start_column=cc + 1, end_row=base_row, end_column=cc + title_merge_cols)
    S.h(base_row + 1, cc + 1, "", bg=_NAVY_BG)
    S.ws.merge_cells(start_row=base_row + 1, start_column=cc + 1, end_row=base_row + 1, end_column=cc + title_merge_cols)
    vals = _stat_vals(stats, wm["n_trades"])
    for i, lbl in enumerate(STAT_LBL):
        S.h(base_row, cc + stat_col0 + i, lbl, bg=_SECTION_BG)
    for i, val in enumerate(vals):
        S.v(base_row + 1, cc + stat_col0 + i, val, STAT_FMT[i], bg=_LABEL_BG)


def _write_wow_block(S, wm, title, base_row: int, base_col: int = 1) -> int:
    """Write one WOW block at (base_row, base_col). Returns the Total-row index.

    Columns (relative to base_col): Year | W1..Wn | Total | Max DD | Live DD % | R/MDD.
    """
    cc = base_col - 1
    nw = wm["n_weeks"]
    w_tot, w_mdd, w_live, w_rmdd = 2 + nw, 3 + nw, 4 + nw, 5 + nw
    wow_live = wm.get("wow_live", {})
    sw, ew = get_month_maps(wm["wow_years"], nw)
    month_ends = set(ew.values())
    blk = Side(style="medium", color=_BLACK)

    def month_edge(cell, w):
        if w in month_ends:
            cell.border = Border(top=cell.border.top, left=cell.border.left,
                                 bottom=cell.border.bottom, right=blk)

    _write_stat_header(S, wm, title, base_row, 5, 7,
                       compute_ratios(flat_weekly(wm["wow"]), WOW_RF, WOW_NANN), cc=cc)
    w_hdr, w_month, w_data0 = base_row + 2, base_row + 3, base_row + 4

    S.h(w_hdr, cc + 1, "Year")
    for w in range(1, nw + 1):
        c = S.h(w_hdr, cc + 1 + w, f"W{w}", size=7)
        month_edge(c, w)
    for col, h in ((w_tot, "Total"), (w_mdd, "Max DD"), (w_live, "Live DD %"), (w_rmdd, "R/MDD")):
        S.h(w_hdr, cc + col, h)
        S.h(w_month, cc + col, "")
        S.ws.merge_cells(start_row=w_hdr, start_column=cc + col, end_row=w_month, end_column=cc + col)

    S.h(w_month, cc + 1, "Month", size=8, tx=_SUB_TX, bg=_SUB_BG)
    for w in range(1, nw + 1):
        c = S.h(w_month, cc + 1 + w, "", bg=_SUB_BG)
        month_edge(c, w)
    for mi, mn in enumerate(MONTHS):
        start_w = sw.get(mi + 1, 1)
        if 1 <= start_w <= nw:
            cell = S.ws.cell(w_month, cc + 1 + start_w, mn)
            cell.font = Font(bold=True, size=8, color=_SUB_TX, name="Calibri")
            cell.alignment = _CENTER

    r = w_data0
    w_tots, w_mdds, w_rmdds, w_lives = [], [], [], []
    for yr in wm["wow_years"]:
        S.h(r, cc + 1, yr, tx=_SUB_TX, bg=_LABEL_BG)
        tot, cnt = 0.0, 0
        yd = wm["wow"].get(yr, {})
        for w in range(1, nw + 1):
            val = yd.get(w)
            if val is not None:
                cell = S.v(r, cc + 1 + w, round(val, 6), PCT_FMT, size=7, bg=_sign_fill(val), tx=_sign_tx(val))
                tot += val
                cnt += 1
            else:
                cell = S.v(r, cc + 1 + w, "", PCT_FMT, size=7)
            month_edge(cell, w)
        S.v(r, cc + w_tot, round(tot, 6) if cnt else "", PCT_FMT,
            bg=_LABEL_BG, tx=_sign_tx(tot if cnt else None))

        dd = wow_year_drawdown(yd, nw)
        S.v(r, cc + w_mdd, dd["maxdd"] if dd["maxdd"] is not None else "", PCT_FMT, bg=_RED_BG, tx=_RED_TX)
        live = wow_live.get(yr)
        S.v(r, cc + w_live, round(live, 6) if live is not None else "", PCT_FMT,
            bg=_RED_BG if (live is not None and live < 0) else _LABEL_BG, tx=_sign_tx(live))
        rmdd = (tot / abs(dd["maxdd"])) if (cnt and dd["maxdd"]) else None
        S.v(r, cc + w_rmdd, round(rmdd, 2) if rmdd is not None else "", RAT_FMT,
            bg=_sign_fill(rmdd), tx=_sign_tx(rmdd))
        if cnt:
            w_tots.append(tot)
        if dd["maxdd"] is not None:
            w_mdds.append(dd["maxdd"])
        if rmdd is not None:
            w_rmdds.append(rmdd)
        if live is not None:
            w_lives.append(live)
        if dd["start"] is not None:
            for w in range(dd["start"], dd["end"] + 1):
                cell = S.ws.cell(r, cc + 1 + w)
                cell.border = Border(
                    top=blk, bottom=blk,
                    left=blk if w == dd["start"] else None,
                    right=blk if w == dd["end"] else None,
                )
        r += 1

    data_r1 = r - 1
    S.h(r, cc + 1, "Total")
    if data_r1 >= w_data0:
        t_sum = sum(w_tots)
        S.v(r, cc + w_tot, round(t_sum, 6), PCT_FMT, bg=_sign_fill(t_sum), tx=_sign_tx(t_sum))
        am = _avg(w_mdds) or 0
        S.v(r, cc + w_mdd, round(am, 6), PCT_FMT, bg=_RED_BG, tx=_RED_TX)
        al = _avg(w_lives)
        S.v(r, cc + w_live, round(al, 6) if al is not None else "", PCT_FMT,
            bg=_RED_BG if (al is not None and al < 0) else _LABEL_BG, tx=_sign_tx(al))
        ar = _avg(w_rmdds)
        S.v(r, cc + w_rmdd, round(ar or 0, 2), RAT_FMT, bg=_sign_fill(ar), tx=_sign_tx(ar))
    return r


def _write_mom_block(S, wm, title, base_row: int, base_col: int = 1) -> int:
    """Columns (relative to base_col): Year | Jan..Dec | Total | Max DD | Live DD % | R/MDD."""
    cc = base_col - 1
    _write_stat_header(S, wm, title, base_row, 4, 6,
                       compute_ratios(flat_monthly(wm["mom"]), MOM_RF, MOM_NANN), cc=cc)
    m_hdr, m_data0 = base_row + 2, base_row + 3
    for i, h in enumerate(["Year"] + MONTHS + ["Total", "Max DD", "Live DD %", "R/MDD"]):
        S.h(m_hdr, cc + 1 + i, h, size=8)

    r = m_data0
    for yr in wm["mom_years"]:
        yd = wm["mom"].get(yr, {"months": {}, "total": None, "maxdd": None, "livedd": None})
        S.h(r, cc + 1, yr, tx=_SUB_TX, bg=_LABEL_BG)
        months = yd.get("months", {})
        for mi, mn in enumerate(MONTHS):
            val = months.get(mn)
            if val is not None:
                S.v(r, cc + 2 + mi, round(val, 6), PCT_FMT, bg=_sign_fill(val), tx=_sign_tx(val))
            else:
                S.v(r, cc + 2 + mi, "", PCT_FMT)
        tot, mdd, live = yd.get("total"), yd.get("maxdd"), yd.get("livedd")
        S.v(r, cc + 14, round(tot, 6) if tot is not None else "", PCT_FMT,
            bg=_sign_fill(tot), tx=_sign_tx(tot))
        S.v(r, cc + 15, round(mdd, 6) if mdd is not None else "", PCT_FMT, bg=_RED_BG, tx=_RED_TX)
        S.v(r, cc + 16, round(live, 6) if live is not None else "", PCT_FMT,
            bg=_RED_BG if (live is not None and live < 0) else _LABEL_BG, tx=_sign_tx(live))
        rmdd = (tot / abs(mdd)) if (tot is not None and mdd) else None
        S.v(r, cc + 17, round(rmdd, 2) if rmdd is not None else "", RAT_FMT,
            bg=_sign_fill(rmdd), tx=_sign_tx(rmdd))
        r += 1

    data_r1 = r - 1
    S.h(r, cc + 1, "Total")
    for mi in range(12):
        S.h(r, cc + 2 + mi, "")
    if data_r1 >= m_data0:
        tots = [wm["mom"].get(y, {})["total"] for y in wm["mom_years"] if wm["mom"].get(y, {}).get("total") is not None]
        mdds = [wm["mom"].get(y, {})["maxdd"] for y in wm["mom_years"] if wm["mom"].get(y, {}).get("maxdd") is not None]
        lives = [wm["mom"].get(y, {})["livedd"] for y in wm["mom_years"] if wm["mom"].get(y, {}).get("livedd") is not None]
        rmdds = []
        for y in wm["mom_years"]:
            t, d = wm["mom"].get(y, {}).get("total"), wm["mom"].get(y, {}).get("maxdd")
            if t is not None and d:
                rmdds.append(t / abs(d))
        n_sum = sum(tots)
        S.v(r, cc + 14, round(n_sum, 6), PCT_FMT, bg=_sign_fill(n_sum), tx=_sign_tx(n_sum))
        am = _avg(mdds) or 0
        S.v(r, cc + 15, round(am, 6), PCT_FMT, bg=_RED_BG, tx=_RED_TX)
        al = _avg(lives)
        S.v(r, cc + 16, round(al, 6) if al is not None else "", PCT_FMT,
            bg=_RED_BG if (al is not None and al < 0) else _LABEL_BG, tx=_sign_tx(al))
        ar = _avg(rmdds)
        S.v(r, cc + 17, round(ar or 0, 2), RAT_FMT, bg=_sign_fill(ar), tx=_sign_tx(ar))
    return r


def _wm_from_cleaned(cleaned: List[Dict], has_midcap: bool, yearly: bool = False) -> Dict[str, Any]:
    ret_field = "Combined Net P&L %" if has_midcap else "% P&L"
    dd_field = "Combined %DD" if has_midcap else "%DD"
    live_field = "Combined Actual Live DD" if has_midcap else "Actual Live DD"
    return build_wow_mom(cleaned, ret_field, dd_field, has_midcap,
                         live_field=live_field, yearly=yearly)


def _set_wow_widths(ws, nw, base_col: int = 1):
    cc = base_col - 1
    ws.column_dimensions[get_column_letter(cc + 1)].width = 16
    for w in range(1, nw + 1):
        ws.column_dimensions[get_column_letter(cc + 1 + w)].width = 7
    for c in (2 + nw, 3 + nw, 4 + nw, 5 + nw):
        ws.column_dimensions[get_column_letter(cc + c)].width = 8


def _set_mom_widths(ws, base_col: int = 1):
    cc = base_col - 1
    ws.column_dimensions[get_column_letter(cc + 1)].width = 8
    for c in range(2, 18):
        ws.column_dimensions[get_column_letter(cc + c)].width = 9


# ── public entry points ────────────────────────────────────────────────────
def write_wow_mom_combined(wb: Workbook, cleaned: List[Dict], has_midcap: bool,
                           title: str, yearly: bool = False) -> bool:
    """Single 'WOW & MOM Summary' sheet (WOW on top, MOM below). Mirrors JS."""
    wm = _wm_from_cleaned(cleaned, has_midcap, yearly=yearly)
    if not (wm["n_trades"] > 0):
        return False
    ws = wb.create_sheet("WOW & MOM Summary")
    ws.freeze_panes = "B1"
    S = _Sheet(ws)
    wow_total = _write_wow_block(S, wm, title, 1)
    _write_mom_block(S, wm, title, wow_total + 2)
    _set_wow_widths(ws, wm["n_weeks"])
    return True


def _rgb6(color) -> Optional[str]:
    """openpyxl color → visible 6-hex RGB (drop the alpha byte), or None."""
    v = getattr(color, "rgb", None)
    if isinstance(v, str) and len(v) == 8:
        return v[2:].upper()
    return None


def _ws_to_ops(ws) -> Dict[str, Any]:
    """Extract a fully-built openpyxl worksheet into the plain layout-ops dict the
    Rust writer (algotest_native.write_layout_sheet_xlsx) consumes. The WOW/MOM sheet
    leans on openpyxl's mutable cells (per-side medium borders, month-edge, drawdown
    boxes, partial overwrites); extracting the FINAL cell state reproduces all of it
    without re-implementing the block writers. Merge fillers + fully-default cells are
    skipped; borders become per-side style lists so Rust redraws them exactly."""
    from openpyxl.utils.cell import range_boundaries
    from openpyxl.utils import column_index_from_string

    merges: List[Tuple[int, int, int, int]] = []
    covered: set = set()
    for rng in ws.merged_cells.ranges:
        r1, c1, r2, c2 = rng.min_row, rng.min_col, rng.max_row, rng.max_col
        merges.append((r1, c1, r2, c2))
        for rr in range(r1, r2 + 1):
            for cc in range(c1, c2 + 1):
                if (rr, cc) != (r1, c1):
                    covered.add((rr, cc))

    cells: List[Dict[str, Any]] = []
    for row in ws.iter_rows():
        for cell in row:
            r, c = cell.row, cell.column
            if (r, c) in covered:
                continue
            fill_rgb = (_rgb6(cell.fill.fgColor)
                        if (cell.fill is not None and cell.fill.patternType) else None)
            b = cell.border
            sides = [getattr(getattr(b, s), "style", None)
                     for s in ("top", "left", "bottom", "right")]
            has_border = any(sides)
            val = cell.value
            if (val is None or val == "") and fill_rgb is None and not has_border:
                continue
            f = cell.font
            if not has_border:
                border: Any = False
            elif all(s == "thin" for s in sides):
                border = True
            else:
                border = sides
            nf = cell.number_format
            cells.append({
                "r": r, "c": c,
                "v": (None if val == "" else val),
                "bold": bool(f.bold),
                "size": float(f.size or 11),
                "fc": (_rgb6(f.color) or "000000"),
                "bg": fill_rgb,
                "align": ("L" if cell.alignment.horizontal == "left" else "C"),
                "border": border,
                "nfmt": (None if nf in (None, "General") else nf),
            })

    col_widths = []
    for letter, dim in ws.column_dimensions.items():
        if dim.width is None:
            continue
        lo = dim.min or column_index_from_string(letter)
        hi = dim.max or lo
        for ci in range(lo, hi + 1):
            col_widths.append((ci, float(dim.width)))
    row_heights = [(r, float(dim.height))
                   for r, dim in ws.row_dimensions.items() if dim.height is not None]

    freeze = None
    if ws.freeze_panes:
        c0, r0, *_ = range_boundaries(ws.freeze_panes)
        freeze = (r0 - 1, c0 - 1)   # openpyxl "B1" → set_freeze_panes(0, 1)

    return {"name": ws.title, "cells": cells, "merges": merges,
            "row_heights": row_heights, "col_widths": col_widths, "freeze": freeze}


def wow_mom_ops(cleaned: List[Dict], has_midcap: bool, title: str,
                yearly: bool = False) -> Optional[Dict[str, Any]]:
    """Rust path — build the 'WOW & MOM Summary' sheet via openpyxl (unchanged logic),
    then extract it to a layout-ops dict. Returns None when there are no trades."""
    wm = _wm_from_cleaned(cleaned, has_midcap, yearly=yearly)
    if not (wm["n_trades"] > 0):
        return None
    wb = Workbook()
    wb.remove(wb.active)
    # `yearly` MUST be forwarded: this call builds the sheet that is actually
    # emitted (the `wm` above is only used for the n_trades check). Dropping it
    # here silently rebuilt WOW with yearly=False, collapsing every yearly trade
    # into its December expiry's ISO week.
    if not write_wow_mom_combined(wb, cleaned, has_midcap, title, yearly=yearly):
        return None
    return _ws_to_ops(wb["WOW & MOM Summary"])


def _adj_split(title: str) -> Tuple[str, str]:
    """Fallback: split 'PE ATM | No Adj' into (row_label, adj_label)."""
    if " | " in title:
        left, right = title.split(" | ", 1)
        return left.strip(), right.strip()
    return title.strip(), "No Adj"


def _adj_sort_key(adj_label: str) -> Tuple[int, float]:
    """Order adjustments: No Adj, then Rise/Fall/Rise or Fall by ascending magnitude."""
    s = adj_label.strip().lower()
    if s.startswith("no adj") or s == "":
        return (0, 0.0)
    m = None
    import re as _re
    mm = _re.search(r"([\d.]+)", s)
    mag = float(mm.group(1)) if mm else 0.0
    if s.startswith("rise or fall"):
        grp = 3
    elif s.startswith("fall"):
        grp = 2
    elif s.startswith("rise"):
        grp = 1
    else:
        grp = 4
    return (grp, mag)


def write_merged_wow_mom(wb: Workbook, combos: List[Dict]) -> bool:
    """
    Two sheets ('WOW Summary' + 'MOM Summary') laid out as a 2D grid:
      • columns  = spot-adjustment (No Adj, Rise, Fall, Rise or Fall) left→right
      • rows     = each distinct strike / expiry / shift signature top→bottom

    combos: [{title, cleaned, has_midcap, adj_key, adj_label, row_key, row_sort}, ...].
    adj_key/adj_label/row_key are optional — when absent they are derived from the
    'strike | adjustment' title so the layout still works. Per-block content and
    calculations are byte-identical to the per-combo tradesheet; only positioning
    changes.
    """
    # Build each combo's wm + resolve grid axes.
    def _intkeys(o):
        # A stored wm (JSON round-trip through Redis) has its integer year/week
        # dict keys turned into strings; restore them so year lookups match the
        # int values in wow_years/mom_years.
        if isinstance(o, dict):
            return {(int(k) if isinstance(k, str) and k.lstrip("-").isdigit() else k): _intkeys(v)
                    for k, v in o.items()}
        if isinstance(o, list):
            return [_intkeys(x) for x in o]
        return o

    items = []
    for c in combos:
        # Use the pre-computed wm (built inline in the worker during the sweep)
        # when present; otherwise derive it from the cleaned rows (legacy path).
        _pre = c.get("wm")
        wm = _intkeys(_pre) if _pre else _wm_from_cleaned(
            c["cleaned"], c.get("has_midcap", False), yearly=bool(c.get("yearly", False)))
        if not (wm["n_trades"] > 0):
            continue
        title = c.get("title") or "Strategy"
        row_lbl, adj_lbl = _adj_split(title)
        adj_label = c.get("adj_label") or adj_lbl
        adj_key = c.get("adj_key") or adj_label
        row_key = c.get("row_key") or row_lbl
        row_sort = c.get("row_sort")
        items.append({
            "wm": wm, "title": title,
            "adj_key": adj_key, "adj_label": adj_label,
            "row_key": row_key, "row_sort": row_sort,
        })
    if not items:
        return False

    # Disambiguate combos that collapse to the SAME (row_key, adj_key) grid cell.
    # This happens for a spread whose two legs are the same option type (e.g.
    # PE Sell + PE Buy): the strike display can only show one leg, so several
    # combos that differ only by the other leg's strike share an identical
    # (row, adjustment) position. Without this they would all write into the same
    # block and the 2nd+ would hit an already-merged header cell → crash
    # ("'MergedCell' object attribute 'value' is read-only"). Each extra combo at
    # an already-taken cell is pushed to its own stacked sub-row (and its title
    # tagged) so no two blocks overlap. Jobs with NO collisions are untouched —
    # the loop makes zero changes, so their output is byte-for-byte identical.
    _cell_count: Dict[Tuple[str, str], int] = {}
    for it in items:
        cell = (it["row_key"], it["adj_key"])
        n = _cell_count.get(cell, 0)
        _cell_count[cell] = n + 1
        if n > 0:
            it["row_key"] = f'{it["row_key"]} ({n + 1})'
            it["title"] = f'{it["title"]} ({n + 1})'

    # Column axis: unique adjustments, ordered No Adj → Rise → Fall → Rise or Fall.
    adj_seen: Dict[str, str] = {}   # adj_key → adj_label
    for it in items:
        adj_seen.setdefault(it["adj_key"], it["adj_label"])
    adj_keys = sorted(adj_seen.keys(), key=lambda k: (_adj_sort_key(adj_seen[k]), k))
    adj_index = {k: i for i, k in enumerate(adj_keys)}

    # Row axis: unique row signatures, first-seen order (optionally by row_sort).
    row_order: List[str] = []
    row_meta: Dict[str, Any] = {}
    for i, it in enumerate(items):
        rk = it["row_key"]
        if rk not in row_meta:
            row_meta[rk] = {"first": i, "sort": it["row_sort"]}
            row_order.append(rk)
    row_order.sort(key=lambda rk: (
        row_meta[rk]["sort"] if row_meta[rk]["sort"] is not None else 1e18,
        row_meta[rk]["first"],
    ))
    row_index = {rk: i for i, rk in enumerate(row_order)}

    # Unify year axes across ALL combos so every block has identical dimensions
    # (blank where a combo has no trades that year) → grid rows/cols stay aligned.
    all_wow_years = sorted({y for it in items for y in it["wm"]["wow_years"]})
    all_mom_years = sorted({y for it in items for y in it["wm"]["mom_years"]})
    for it in items:
        it["wm"]["wow_years"] = list(all_wow_years)
        it["wm"]["mom_years"] = list(all_mom_years)

    nw = items[0]["wm"]["n_weeks"]
    n_adj = len(adj_keys)
    ny_w = len(all_wow_years)
    ny_m = len(all_mom_years)

    # ── WOW Summary grid ────────────────────────────────────────────────────
    Wb = 5 + nw                # block width (Year + weeks + Total+MaxDD+Live+RMDD)
    Gw = 2                     # column gap between adjustment sections
    Hw = 5 + ny_w              # block height (4 header rows + years + total)
    Gr = 2                     # row gap between strike sections
    ws_w = wb.create_sheet("WOW Summary")
    ws_w.freeze_panes = "B1"
    Sw = _Sheet(ws_w)
    for it in items:
        ri = row_index[it["row_key"]]
        ai = adj_index[it["adj_key"]]
        base_row = 1 + ri * (Hw + Gr)
        base_col = 1 + ai * (Wb + Gw)
        _write_wow_block(Sw, it["wm"], it["title"], base_row, base_col)
    for ai in range(n_adj):
        _set_wow_widths(ws_w, nw, 1 + ai * (Wb + Gw))

    # ── MOM Summary grid ────────────────────────────────────────────────────
    Mb = 18                    # block width (Year + 12 months + Total+MaxDD+Live+RMDD, stats to col 18)
    Gm = 2                     # column gap between adjustment sections (matches Gw)
    Hm = 4 + ny_m
    ws_m = wb.create_sheet("MOM Summary")
    ws_m.freeze_panes = "B1"
    Sm = _Sheet(ws_m)
    for it in items:
        ri = row_index[it["row_key"]]
        ai = adj_index[it["adj_key"]]
        base_row = 1 + ri * (Hm + Gr)
        base_col = 1 + ai * (Mb + Gm)
        _write_mom_block(Sm, it["wm"], it["title"], base_row, base_col)
    for ai in range(n_adj):
        _set_mom_widths(ws_m, 1 + ai * (Mb + Gm))
    return True
