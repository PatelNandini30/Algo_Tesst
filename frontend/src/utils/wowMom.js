/**
 * wowMom.js — Week-on-Week (WoW) & Month-on-Month (MoM) summary computation.
 *
 * Pure (no ExcelJS / no React). Ports the research team's manual pipeline:
 *   • step-1 (wow_mom_app_step1.py) — bucket trade returns by Expiry → ISO week
 *     (WoW) and by Exit Date → calendar month (MoM).
 *   • step-3 (master_ratio_app_step3.py) — Sharpe / Sortino / K1-K3 / SQN / CAGR
 *     plus Win/Loss/Expectancy, all over the per-period (weekly / monthly)
 *     aggregated return series.
 *
 * Inputs are read off the SAME rows that become the exported "Trade Sheet":
 *   ret column  = 'Combined Net P&L %' (midcap) | '% P&L'  (NIFTY-only)  [percent]
 *   dd  column  = 'Combined %DD'       (midcap) | '%DD'     (NIFTY-only)
 *   Expiry / Exit Date = 'DD-MM-YYYY' (formatDateToDdMmYyyy output)
 *
 * Constants match the research defaults exactly:
 *   RF = 6% annual → 0.06/52 per week (WoW), 0.06/12 per month (MoM)
 *   n_ann = 52 (WoW) / 12 (MoM),  SLOPE_CAP = 307 (K-ratio equity cap)
 *
 * NOTE: the existing Trade Sheet / Summary / Patch-wise sheets and every stat on
 * them are untouched — this is an additive, independent computation.
 */

export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                       'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export const WOW_RF = 0.06 / 52;   // research default: 6% annual ÷ 52
export const MOM_RF = 0.06 / 12;   // research default: 6% annual ÷ 12
export const WOW_NANN = 52;
export const MOM_NANN = 12;
const SLOPE_CAP = 307;

// ── date helpers ──────────────────────────────────────────────────────────
/** Parse "DD-MM-YYYY" / "DD/MM/YYYY" → UTC Date, else null. */
export function parseDmy(value) {
  if (value == null || value === '') return null;
  const s = String(value).trim();
  const m = s.match(/^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$/);
  if (m) return new Date(Date.UTC(+m[3], +m[2] - 1, +m[1]));
  const dt = new Date(s);
  return Number.isNaN(dt.getTime())
    ? null
    : new Date(Date.UTC(dt.getFullYear(), dt.getMonth(), dt.getDate()));
}

/** ISO week-numbering {year, week} — matches pandas dt.isocalendar(). */
export function isoYearWeek(date) {
  const d = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const dayNum = (d.getUTCDay() + 6) % 7;        // Mon=0 … Sun=6
  d.setUTCDate(d.getUTCDate() - dayNum + 3);      // Thursday of this ISO week
  const isoYear = d.getUTCFullYear();
  const firstThu = new Date(Date.UTC(isoYear, 0, 4));
  const ftDayNum = (firstThu.getUTCDay() + 6) % 7;
  firstThu.setUTCDate(firstThu.getUTCDate() - ftDayNum + 3);
  const week = 1 + Math.round((d - firstThu) / (7 * 86400000));
  return { year: isoYear, week };
}

/** ISO week containing the 1st of (year, month) shifted to its first Thursday. */
function firstThuWeek(year, month) {
  const d = new Date(Date.UTC(year, month - 1, 1));
  const dow = (d.getUTCDay() + 6) % 7;            // Mon=0
  d.setUTCDate(d.getUTCDate() + ((3 - dow + 7) % 7));
  return isoYearWeek(d).week;
}

function mode(arr) {
  const counts = new Map();
  let best = arr[0], bestN = 0;
  for (const v of arr) {
    const c = (counts.get(v) || 0) + 1;
    counts.set(v, c);
    if (c > bestN) { bestN = c; best = v; }
  }
  return best;
}

/** month → {startWeek, endWeek} using the mode first-Thursday week across years. */
export function getMonthMaps(years, nWeeks) {
  const sw = {}, ew = {};
  for (let m = 1; m <= 12; m++) {
    sw[m] = years.length ? mode(years.map(y => firstThuWeek(y, m))) : 1;
  }
  for (let m = 1; m <= 11; m++) ew[m] = sw[m + 1] - 1;
  ew[12] = nWeeks;
  return { sw, ew };
}

// ── ratio engine (port of step-3 compute_ratios) ───────────────────────────
function std(arr, mean) {
  if (!arr.length) return 0;
  const m = mean == null ? arr.reduce((a, b) => a + b, 0) / arr.length : mean;
  return Math.sqrt(arr.reduce((a, b) => a + (b - m) * (b - m), 0) / arr.length);
}

/**
 * returns: array of per-period decimal returns (weekly for WoW, monthly for MoM).
 * Returns null if < 3 periods. n = number of periods.
 */
export function computeRatios(returns, rf, nAnn) {
  if (returns.length < 3) return null;
  const R = returns;
  const N = R.length;
  const avg = R.reduce((a, b) => a + b, 0) / N;
  const sd = std(R, avg);
  const pos = R.filter(v => v > 0);
  const neg = R.filter(v => v < 0);
  const wp = pos.length / N;
  const wa = pos.length ? pos.reduce((a, b) => a + b, 0) / pos.length : 0;
  const lp = neg.length / N;
  const la = neg.length ? neg.reduce((a, b) => a + b, 0) / neg.length : 0;
  const exp = la ? (wp / Math.abs(la)) * wa - lp : 0;
  const sdNeg = neg.length ? std(neg) : 0;
  const sh = sd    ? ((avg - rf) / sd)    * Math.sqrt(nAnn) : 0;
  const so = sdNeg ? ((avg - rf) / sdNeg) * Math.sqrt(nAnn) : 0;
  const sqn = sd ? (avg / sd) * Math.sqrt(N) : 0;

  // K-ratio — slope of the (capped) equity curve vs its standard error.
  const nS = Math.min(N, SLOPE_CAP);
  const eq = [100.0];
  for (const v of R) eq.push(eq[eq.length - 1] * (1 + v));
  const Y = eq.slice(1, nS + 1);
  const X = Array.from({ length: nS }, (_, i) => i + 1);
  const mx = X.reduce((a, b) => a + b, 0) / nS;
  const my = Y.reduce((a, b) => a + b, 0) / nS;
  let sxy = 0, sxx = 0;
  for (let i = 0; i < nS; i++) { sxy += (X[i] - mx) * (Y[i] - my); sxx += (X[i] - mx) ** 2; }
  const slope = sxx ? sxy / sxx : 0;
  const intercept = my - slope * mx;
  let sse = 0;
  for (let i = 0; i < nS; i++) { const e = Y[i] - (slope * X[i] + intercept); sse += e * e; }
  const stErr = Math.sqrt(Math.max(sse / (nS - 2), 1e-30));
  const base = (slope * Math.sqrt(sxx)) / stErr;
  const k1 = base / Math.sqrt(N);
  const k2 = base / N;
  const k3 = (base * Math.sqrt(252)) / N;

  let c = 1.0;
  for (const v of R) c *= (1 + v);
  const cg = c ** (nAnn / N) - 1;

  return { wp, wa, lp, la, exp, sh, so, k1, k2, k3, sqn, cg, n: N };
}

/** Running cumulative drawdown over a return sequence (decimal). */
export function maxDd(rets) {
  let eq = 100, peak = 100, d = 0;
  for (const v of rets) {
    eq *= (1 + v);
    if (eq > peak) peak = eq;
    const dd = (eq - peak) / peak;
    if (dd < d) d = dd;
  }
  return d;
}

// ── bucketing (port of step-1 load_tradesheet) ─────────────────────────────
/**
 * Build WoW + MoM structures from exported trade rows.
 *
 * @param {Array<Object>} trades   cleaned trade rows (one per leg; trade-level
 *                                 values live on the first leg row only).
 * @param {Object} opts
 *   retField  — column with the trade %P&L (percent). Blank on non-first legs.
 *   ddField   — column with the per-trade %DD.
 *   ddIsPercent — true if ddField holds percent (midcap 'Combined %DD'); the
 *                 NIFTY '%DD' is already a decimal, so false there.
 *
 * @returns {{
 *   wow: {year:{week:decimal}}, mom: {year:{months:{},total,maxdd}},
 *   wowYears:[], momYears:[], nWeeks, nTrades
 * }}
 */
export function buildWowMom(trades, { retField, ddField, ddIsPercent, liveField, yearly = false }) {
  const wow = {};                     // {year: {week: decimal}}
  const momMonthly = {};              // {year: {monthIdx0: decimal}}
  const momDd = {};                   // {year: [decimal dd, …]}
  // Live DD % — MIN of "Actual Live DD" (percent points → /100) per year. WOW by
  // Expiry year, MOM by Exit year. Written between Max DD and R/MDD.
  const wowLive = {};                 // {year: [decimal, …]}
  const momLive = {};                 // {year: [decimal, …]}
  let nTrades = 0;

  for (const t of trades) {
    const raw = t[retField];
    if (raw === '' || raw == null) continue;           // skip non-first-leg rows
    const ret = Number(raw);
    if (!Number.isFinite(ret)) continue;
    nTrades += 1;
    const dec = ret / 100;                              // percent → decimal

    let liveDec = null;
    if (liveField) {
      const lr = t[liveField];
      if (lr !== '' && lr != null && Number.isFinite(Number(lr))) liveDec = Number(lr) / 100;
    }

    // WoW — by Expiry, EXCEPT under a YEARLY basis.
    //
    // Expiry is the right week identity for weekly/monthly: the trade IS its
    // contract, and it deliberately keeps a contract's P&L in ONE week even when
    // a T-n exit lands in the previous calendar week. Under YEARLY the contract
    // is the whole year, so every trade shares one December Expiry and the year
    // collapses into a single cell (2019-12-26 -> ISO week 52). There the roll
    // segment is the week, so key on Exit Date — as MoM already does below.
    // Mirrors backend wow_mom.py build_wow_mom(yearly=...).
    const eDate = parseDmy(yearly ? t['Exit Date'] : t['Expiry']);
    if (eDate) {
      const { year, week } = isoYearWeek(eDate);
      if (!wow[year]) wow[year] = {};
      wow[year][week] = (wow[year][week] || 0) + dec;
      if (liveDec != null) (wowLive[year] = wowLive[year] || []).push(liveDec);
    }

    // MoM — by Exit Date
    const xDate = parseDmy(t['Exit Date']);
    if (xDate) {
      const y = xDate.getUTCFullYear();
      const mi = xDate.getUTCMonth();                   // 0..11
      if (!momMonthly[y]) momMonthly[y] = {};
      momMonthly[y][mi] = (momMonthly[y][mi] || 0) + dec;
      const ddRaw = t[ddField];
      if (ddRaw !== '' && ddRaw != null && Number.isFinite(Number(ddRaw))) {
        const ddDec = ddIsPercent ? Number(ddRaw) / 100 : Number(ddRaw);
        (momDd[y] = momDd[y] || []).push(ddDec);
      }
      if (liveDec != null) (momLive[y] = momLive[y] || []).push(liveDec);
    }
  }

  // Drop ~zero weekly cells (matches step-1's |v|>1e-9 filter).
  for (const y of Object.keys(wow)) {
    for (const w of Object.keys(wow[y])) {
      if (Math.abs(wow[y][w]) <= 1e-9) delete wow[y][w];
    }
    if (!Object.keys(wow[y]).length) delete wow[y];
  }

  // Assemble MoM: month dict, yearly total, yearly Max DD = MIN(%DD column).
  const mom = {};
  for (const y of Object.keys(momMonthly)) {
    const months = {};
    for (let mi = 0; mi < 12; mi++) {
      const v = momMonthly[y][mi];
      if (v != null && Math.abs(v) > 1e-9) months[MONTHS[mi]] = v;
    }
    const keys = Object.keys(months);
    if (!keys.length) continue;
    const total = keys.reduce((a, k) => a + months[k], 0);
    // Max DD = min of the %DD column for the year (research rule); fall back to
    // running cumulative DD over the monthly series only if no %DD was present.
    const ddArr = momDd[y] || [];
    const maxdd = ddArr.length
      ? Math.min(...ddArr)
      : maxDd(MONTHS.map(m => months[m] || 0));
    const liveArr = momLive[y] || [];
    const livedd = liveArr.length ? Math.min(...liveArr) : null;
    mom[+y] = { months, total, maxdd, livedd };
  }

  const wowLiveMin = {};
  for (const y of Object.keys(wowLive)) wowLiveMin[+y] = wowLive[y].length ? Math.min(...wowLive[y]) : null;

  const wowYears = Object.keys(wow).map(Number).sort((a, b) => a - b);
  const momYears = Object.keys(mom).map(Number).sort((a, b) => a - b);
  let maxWk = 52;
  for (const y of wowYears) for (const w of Object.keys(wow[y])) maxWk = Math.max(maxWk, +w);
  const nWeeks = maxWk === 53 ? 53 : 53;   // research default: always show W53

  return { wow, mom, wowYears, momYears, nWeeks, nTrades, wowLive: wowLiveMin };
}

/**
 * WOW per-year Max Drawdown + the week-range to outline. Drawdown is a running
 * cumulative over the year's PRESENT weeks (blank weeks are SKIPPED, NOT treated
 * as a break — the run keeps looking forward across gaps) that resets only when
 * the run climbs back to >= 0. The worst trough is the year's Max DD, and
 * [startWeek, endWeek] (start of that run → the trough week) is boxed in black.
 *
 * NOTE: this deliberately differs from step-2 maxdd_app's `calc_dd_range`, which
 * split on consecutive weeks and so reset at every blank — that under-counts
 * drawdowns spanning a gap (e.g. 2019 W14→W42). Verified against the research
 * team's hand-corrected "Rectify" sheet (7/7 years match).
 *
 * @param {Object} weekMap {week:int → decimal}
 * @param {number} nw      number of week columns (53)
 * @returns {{maxdd:number|null, startWeek:number|null, endWeek:number|null}}
 */
export function wowYearDrawdown(weekMap, nw) {
  const vals = [];
  for (let w = 1; w <= nw; w++) if (weekMap[w] != null) vals.push([w, weekMap[w]]);
  if (!vals.length) return { maxdd: null, startWeek: null, endWeek: null };

  // Single continuous pass over all present weeks — gaps do NOT break the run.
  let gMin = 0, best = [null, null];
  let cum = 0, started = false, sStart = null, sMin = 0, sMc = null;
  for (const [col, val] of vals) {
    if (!started) {
      if (val < 0) { started = true; sStart = col; cum = val; sMin = val; sMc = col; }
    } else {
      cum += val;
      if (cum >= 0) {
        if (sMin < gMin) { gMin = sMin; best = [sStart, sMc]; }
        started = false; cum = 0; sStart = null; sMin = 0; sMc = null;
      } else if (cum < sMin) { sMin = cum; sMc = col; }
    }
  }
  if (started && sMin < gMin) { gMin = sMin; best = [sStart, sMc]; }

  const [sc, ec] = best;
  if (sc == null) return { maxdd: null, startWeek: null, endWeek: null };
  // gMin is the cumulative at the trough (week ec) = the Max DD value.
  return { maxdd: Number(gMin.toFixed(4)), startWeek: sc, endWeek: ec };
}

/** Flat list of all per-period (weekly) decimal returns across years. */
export function flatWeekly(wow) {
  const out = [];
  for (const y of Object.keys(wow)) for (const w of Object.keys(wow[y])) out.push(wow[y][w]);
  return out;
}

/** Flat list of all per-period (monthly) decimal returns across years. */
export function flatMonthly(mom) {
  const out = [];
  for (const y of Object.keys(mom)) {
    const ms = mom[y].months;
    for (const k of Object.keys(ms)) out.push(ms[k]);
  }
  return out;
}
