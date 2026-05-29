# Optimizer Master-Summary Column Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every optimizer master-summary column show values that exactly match the corresponding individual combo's tradesheet Summary sheet.

**Architecture:** Six targeted fixes spread across three backend files + one frontend file. No new files needed. Each fix is independent — backend formula bugs (base.py, metrics.py, runner.py) are corrected first, then the frontend schema is updated to add/remove columns.

**Tech Stack:** Python (base.py, metrics.py, runner.py), JavaScript (strategyParamSchema.js), unittest

---

## Files Touched

| File | What changes |
|---|---|
| `backend/base.py` | CAGR formula (NAV-based), `car_mdd` formula (divide by 100) |
| `backend/services/optimizer/metrics.py` | `roi_vs_spot` ×100, `car_mdd_live` ÷100, `per_leg_pnl` per-row %, add `total_pnl_pct` |
| `backend/services/optimizer/runner.py` | `%DD` stored as % not decimal (line ~967) |
| `frontend/src/utils/strategyParamSchema.js` | Add 4 columns, remove 3 columns from `MASTER_SUMMARY_COLUMNS` |
| `backend/tests/test_optim_metrics.py` | Update car_mdd_live test, add tests for roi_vs_spot and total_pnl_pct |

---

## Root Causes (reference)

| Column | Current | Expected | Root cause |
|---|---|---|---|
| CAGR(Options) | 8.78 | 7.94% | base.py uses rupee-base CAGR, not 100-base NAV |
| CAR/MDD Booked | 1.28 | 0.0115 | formula: `cagr/|DD%|` (both %) → should be `(cagr/100)/|DD%|` |
| ROI vs Spot | 0.7644 | 76.44 | missing ×100 in metrics.py |
| Actual Live DD | -0.0688 | -8.91% | runner.py stores `%DD` as decimal; actual live DD needs MAE data |
| Avg Actual Live DD | -0.0131 | -2.06% | same root cause |
| CAR/MDD Live | 127.62 | 0.0089 | wrong CAGR + wrong live DD + wrong formula |
| PE P&L % | 77.05 | 53.62% | denominator is `initial_spot` (first trade); should be `sum(P&L/entry_spot×100)` |
| Long Spot P&L % | 100.79 | 66.00% | same denominator issue |

---

## Task 1: Fix CAGR formula in base.py

**Files:**
- Modify: `backend/base.py:1000-1009`
- Test: `backend/tests/test_optim_metrics.py` (indirect — CAGR flows into `car_mdd_live`)

Context: `compute_analytics` already computes a 100-base NAV cumulative series (`cumulative_series[-1]` = final NAV). The correct CAGR is `(final_nav/100)^(1/n_years) - 1`. The current code uses the rupee-base formula `(initial_spot + total_pnl) / initial_spot` which gives 8.78% instead of the correct 7.94%.

- [ ] **Step 1: Replace the CAGR block in base.py**

Find (around line 1000-1009):
```python
    initial_capital = float(initial_entry_spot) if pd.notna(initial_entry_spot) else 0.0
    final_capital = initial_capital + total_pnl
    if initial_capital > 0 and final_capital > 0:
        cagr_raw = 100.0 * ((final_capital / initial_capital) ** (1.0 / n_years) - 1)
        # Cap at ±99999% — prevents astronomical blow-up when n_years is tiny
        # (e.g. date-parse error collapses range to 0.01 years → 2^100 overflow).
        cagr = round(max(-99999.0, min(99999.0, cagr_raw)), 2)
    else:
        cagr = round(-100.0, 2)
```

Replace with:
```python
    # 100-base NAV CAGR — matches the research team's formula and the frontend
    # buildTradeExcel.js computation.  cumulative_series is already 100-seeded.
    final_nav = cumulative_series[-1] if cumulative_series else 0.0
    if final_nav > 0 and n_years > 0:
        cagr_raw = 100.0 * ((final_nav / 100.0) ** (1.0 / n_years) - 1)
        cagr = round(max(-99999.0, min(99999.0, cagr_raw)), 2)
    else:
        cagr = round(-100.0, 2)
```

- [ ] **Step 2: Verify the change compiles**

```bash
cd /home/user/Algo_Test_Software && python -c "from base import compute_analytics; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/base.py
git commit -m "fix: use 100-base NAV CAGR formula in compute_analytics"
```

---

## Task 2: Fix car_mdd formula in base.py

**Files:**
- Modify: `backend/base.py:1035`

The current formula `cagr / abs(max_dd_pct)` uses percentage CAGR (7.94) divided by percentage DD (6.88) → ratio 1.154. The correct formula is `(cagr/100) / abs(max_dd_pct)` = decimal CAGR (0.0794) / percentage DD (6.88) = 0.0115. This matches buildTradeExcel.js line 608: `(_optCagrPctJS / 100) / Math.abs(_maxDDPctJS)`.

- [ ] **Step 1: Fix car_mdd line**

Find (line ~1035):
```python
    car_mdd = round(min(99999.0, cagr / abs(max_dd_pct)), 2) if max_dd_pct != 0 else 0
```

Replace with:
```python
    car_mdd = round(min(99999.0, (cagr / 100.0) / abs(max_dd_pct)), 4) if max_dd_pct != 0 else 0
```

Note: precision raised to 4 decimal places (0.0115 needs 4 places; 2 places rounds to 0.01).

- [ ] **Step 2: Verify**

```bash
cd /home/user/Algo_Test_Software && python -c "
from base import compute_analytics
import pandas as pd, numpy as np
df = pd.DataFrame({'Entry Date': ['01-01-2019'], 'Exit Date': ['01-01-2026'], 'Entry Spot': [11000.0], 'Exit Spot': [22000.0], 'Net P&L': [8847.35]})
_, s = compute_analytics(df)
print('car_mdd:', s['car_mdd'], '(should be ~0.01 scale, not ~1.0 scale)')
"
```
Expected: car_mdd is in the ~0.01 range.

- [ ] **Step 3: Commit**

```bash
git add backend/base.py
git commit -m "fix: car_mdd formula uses (cagr/100)/|DD%| matching frontend formula"
```

---

## Task 3: Fix %DD format in runner.py

**Files:**
- Modify: `backend/services/optimizer/runner.py:967`

The Rust fast-path optimizer stores `%DD` as a decimal fraction (e.g., -0.0688) while the Python engine stores it as percentage (e.g., -6.88). This makes `actual_live_dd_max` read decimal values when falling back to `%DD`. Fix: multiply by 100 for consistency with the Python engine convention and with `compute_analytics`.

- [ ] **Step 1: Fix the %DD line**

Find (around line 965-967):
```python
        cum.append(cumulative); pk.append(peak)
        dd.append(cumulative - peak)
        pdd.append((cumulative - peak) / peak if peak != 0 else 0.0)
```

Replace the last line with:
```python
        pdd.append(((cumulative - peak) / peak * 100) if peak != 0 else 0.0)
```

- [ ] **Step 2: Verify**

```bash
cd /home/user/Algo_Test_Software && python -c "
from services.optimizer.runner import _run_single_backtest_rust_fast
print('import ok')
"
```
Expected: `import ok` (no syntax errors)

- [ ] **Step 3: Commit**

```bash
git add backend/services/optimizer/runner.py
git commit -m "fix: store %DD as percentage in Rust optimizer path (was decimal)"
```

---

## Task 4: Fix metrics.py — roi_vs_spot, car_mdd_live, per_leg_pnl, add total_pnl_pct

**Files:**
- Modify: `backend/services/optimizer/metrics.py`
- Test: `backend/tests/test_optim_metrics.py`

Four sub-fixes in this file:

### 4a. roi_vs_spot: multiply by 100

Current: `return round(total_pnl / spot_change, 4)` → 0.7644  
Expected: 76.44 (percentage)

### 4b. car_mdd_live: divide CAGR by 100

Current: `return round(cagr / abs(live_dd_max), 4)` → 127.62  
Expected: `(cagr/100) / abs(live_dd_max)` = 0.0089 (matches frontend formula line 668)

### 4c. per_leg_pnl: per-row percentage instead of total/initial_spot

Current: `to_pct = (lambda v: round(v / initial_spot * 100, 4))` applied to totals → 77.0 (wrong)  
Expected: `sum(row_pnl / row_entry_spot * 100)` for each row → 53.62%

### 4d. Add total_pnl_pct

New key: sum of `% P&L` column (per-trade Net P&L as % of Entry Spot). Matches `pe_pnl_pct` for single-leg PE strategies.

- [ ] **Step 1: Update tests first**

In `backend/tests/test_optim_metrics.py`, update the `car_mdd_live` test (currently asserts 3.0 = 12/4, now should be 0.03 = (12/100)/4):

```python
class TestLiveDD(unittest.TestCase):
    def test_uses_lowest_nav_minus_peak(self):
        df = _trades(
            [
                {"Lowest NAV During Trade": 100, "Peak": 100},
                {"Lowest NAV During Trade": 98, "Peak": 101},  # live = -3
                {"Lowest NAV During Trade": 99, "Peak": 102},  # live = -3
            ]
        )
        out = actual_live_dd(df)
        self.assertEqual(out["actual_live_dd_max"], -3.0)
        self.assertEqual(out["actual_live_dd_avg"], -2.0)

    def test_car_mdd_live(self):
        # (cagr/100) / |dd| — cagr=12%, dd=-4 → (12/100)/4 = 0.03
        self.assertAlmostEqual(car_mdd_live({"cagr_options": 12}, -4.0), 0.03)
        self.assertEqual(car_mdd_live({"cagr_options": 12}, 0.0), 0.0)
```

Add new tests in `TestROIVsSpot`:
```python
class TestROIVsSpot(unittest.TestCase):
    def test_basic_ratio(self):
        # 50/100 * 100 = 50.0 (percentage form)
        self.assertAlmostEqual(roi_vs_spot({"total_pnl": 50, "spot_change": 100}), 50.0)

    def test_zero_spot_change(self):
        self.assertEqual(roi_vs_spot({"total_pnl": 50, "spot_change": 0}), 0.0)
```

Add new test for `total_pnl_pct` in `TestComputeBundle`:
```python
    def test_total_pnl_pct_from_pct_col(self):
        df = _trades([
            {"Entry Spot": 100, "Exit Spot": 102, "Net P&L": 1.0, "% P&L": 1.0,
             "Put P&L": 1.0, "Spot P&L": 2.0},
            {"Entry Spot": 200, "Exit Spot": 205, "Net P&L": 2.0, "% P&L": 1.0,
             "Put P&L": 2.0, "Spot P&L": 5.0},
        ])
        summary = {"total_pnl": 3, "spot_change": 7, "cagr_options": 10}
        out = compute_optim_metrics(df, summary)
        # total_pnl_pct = sum of % P&L = 1.0 + 1.0 = 2.0
        self.assertAlmostEqual(out["total_pnl_pct"], 2.0)
        # pe_pnl_pct = sum(PE/Entry*100) = 1/100*100 + 2/200*100 = 1.0 + 1.0 = 2.0
        self.assertAlmostEqual(out["pe_pnl_pct"], 2.0)
        # roi_vs_spot = 3/7*100 = 42.857...
        self.assertAlmostEqual(out["roi_vs_spot"], round(3/7*100, 4))
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/user/Algo_Test_Software && python -m unittest backend.tests.test_optim_metrics -v 2>&1 | tail -20
```
Expected: several FAIL lines.

- [ ] **Step 3: Apply all four fixes to metrics.py**

**4a — roi_vs_spot:**

Find:
```python
    return round(total_pnl / spot_change, 4)
```

Replace with:
```python
    return round(total_pnl / spot_change * 100, 4)
```

**4b — car_mdd_live:**

Find:
```python
def car_mdd_live(summary: Dict[str, Any], live_dd_max: float) -> float:
    """`cagr_options / |Actual Live DD|`. 0 if live DD is 0."""
    if not live_dd_max:
        return 0.0
    try:
        cagr = float(summary.get("cagr_options", 0) or 0)
    except (TypeError, ValueError):
        cagr = 0.0
    return round(cagr / abs(live_dd_max), 4)
```

Replace with:
```python
def car_mdd_live(summary: Dict[str, Any], live_dd_max: float) -> float:
    """`(cagr_options/100) / |Actual Live DD|`. Matches frontend formula. 0 if live DD is 0."""
    if not live_dd_max:
        return 0.0
    try:
        cagr = float(summary.get("cagr_options", 0) or 0)
    except (TypeError, ValueError):
        cagr = 0.0
    return round((cagr / 100.0) / abs(live_dd_max), 4)
```

**4c — per_leg_pnl: per-row percentages and 4d — add total_pnl_pct:**

Find the entire `per_leg_pnl` function:
```python
def per_leg_pnl(trades: pd.DataFrame) -> Dict[str, float]:
    """
    CE P&L = sum(Call P&L).  PE P&L = sum(Put P&L).
    Long Spot P&L = sum(Spot P&L) — already a reference column built by engine.

    All `_pct` variants are expressed as percentage of initial spot,
    consistent with how `total_pnl_pct = total_pnl / initial_spot * 100` is
    computed elsewhere.
    """
    # Rust path uses "CE P&L"/"PE P&L"; Python engine uses "Call P&L"/"Put P&L".
    ce_total = _sum_col(trades, "Call P&L", "CE P&L", "call_pnl")
    pe_total = _sum_col(trades, "Put P&L", "PE P&L", "put_pnl")
    spot_total = _sum_col(trades, "Spot P&L", "spot_pnl")

    initial_spot, _ = _safe_first_last_spot(trades)
    to_pct = (lambda v: round(v / initial_spot * 100, 4)) if initial_spot > 0 else (lambda v: 0.0)

    return {
        "ce_pnl_total": round(ce_total, 2),
        "ce_pnl_pct": to_pct(ce_total),
        "pe_pnl_total": round(pe_total, 2),
        "pe_pnl_pct": to_pct(pe_total),
        "long_spot_pnl": round(spot_total, 2),
        "long_spot_pnl_pct": to_pct(spot_total),
    }
```

Replace with:
```python
def per_leg_pnl(trades: pd.DataFrame) -> Dict[str, float]:
    """
    CE P&L = sum(Call P&L).  PE P&L = sum(Put P&L).
    Long Spot P&L = sum(Spot P&L).

    All `_pct` variants are expressed as the sum of per-row (P&L / Entry Spot * 100),
    matching buildTradeExcel.js which sums per-trade percentages rather than dividing
    the total by the initial spot.
    """
    ce_col = "Call P&L" if "Call P&L" in trades.columns else ("CE P&L" if "CE P&L" in trades.columns else None)
    pe_col = "Put P&L" if "Put P&L" in trades.columns else ("PE P&L" if "PE P&L" in trades.columns else None)
    spot_col = "Spot P&L" if "Spot P&L" in trades.columns else None

    ce_total = _sum_col(trades, "Call P&L", "CE P&L", "call_pnl")
    pe_total = _sum_col(trades, "Put P&L", "PE P&L", "put_pnl")
    spot_total = _sum_col(trades, "Spot P&L", "spot_pnl")

    if "Entry Spot" in trades.columns:
        es = pd.to_numeric(
            trades["Entry Spot"].replace("", np.nan), errors="coerce"
        ).replace(0, np.nan)
    else:
        es = pd.Series(np.nan, index=trades.index)

    def _pct_sum(col_name: str | None) -> float:
        if col_name is None or col_name not in trades.columns:
            return 0.0
        vals = pd.to_numeric(trades[col_name], errors="coerce").fillna(0)
        return round(float((vals / es * 100).fillna(0).sum()), 4)

    return {
        "ce_pnl_total": round(ce_total, 2),
        "ce_pnl_pct": _pct_sum(ce_col),
        "pe_pnl_total": round(pe_total, 2),
        "pe_pnl_pct": _pct_sum(pe_col),
        "long_spot_pnl": round(spot_total, 2),
        "long_spot_pnl_pct": _pct_sum(spot_col),
    }
```

Then in `compute_optim_metrics`, add `total_pnl_pct` after `out.update(per_leg_pnl(trades))`:

Find:
```python
    out: Dict[str, Any] = {}
    out.update(per_leg_pnl(trades))
    out["roi_vs_spot"] = roi_vs_spot(summary)
```

Replace with:
```python
    out: Dict[str, Any] = {}
    out.update(per_leg_pnl(trades))
    # total_pnl_pct: sum of per-trade Net P&L % (= sum of % P&L column)
    pct_col = "% P&L" if "% P&L" in trades.columns else None
    if pct_col:
        out["total_pnl_pct"] = round(
            float(pd.to_numeric(trades[pct_col], errors="coerce").fillna(0).sum()), 4
        )
    else:
        out["total_pnl_pct"] = 0.0
    out["roi_vs_spot"] = roi_vs_spot(summary)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/user/Algo_Test_Software && python -m unittest backend.tests.test_optim_metrics -v 2>&1 | tail -20
```
Expected: all tests PASS (no FAIL).

- [ ] **Step 5: Commit**

```bash
git add backend/services/optimizer/metrics.py backend/tests/test_optim_metrics.py
git commit -m "fix: optimizer metrics roi_vs_spot×100, car_mdd_live÷100, per-row pct, add total_pnl_pct"
```

---

## Task 5: Update MASTER_SUMMARY_COLUMNS in strategyParamSchema.js

**Files:**
- Modify: `frontend/src/utils/strategyParamSchema.js:202-242`

Changes:
- **Add** `total_pnl_pct` after `total_pnl` (Net P/L Sum %)
- **Add** `avg_win_pct` after `avg_win` (Avg. win %)
- **Add** `avg_loss_pct` after `avg_loss` (Avg. Loss %)
- **Add** `spot_change_pct` after `spot_change` (Spot Change %)
- **Remove** `max_dd_pts` (DD Points)
- **Remove** the `{ key: 'car_mdd', label: 'CAR/MDD', dup: true }` row
- **Remove** `cagr_midcap` (CAGR Midcap)

The `avg_win_pct` and `avg_loss_pct` keys already exist in `base.py`'s summary dict (lines 1095-1096). The `spot_change_pct` key already exists (line 1111). The `total_pnl_pct` key is added by the metrics.py fix in Task 4.

- [ ] **Step 1: Replace MASTER_SUMMARY_COLUMNS**

Find the entire export block (lines 202-242):
```javascript
/** Master-summary 37-column layout — matches Summary_of_X.xlsx. */
export const MASTER_SUMMARY_COLUMNS = [
  { key: 'sr_no',                   label: 'Sr. No.' },
  { key: 'expiry',                  label: 'Expiry' },
  { key: 'shifting',                label: 'Shifting' },
  { key: 'put_strike_label',        label: 'Put ATM or ITM' },
  { key: 'call_strike_label',       label: 'Call ATM or ITM' },
  { key: 'spot_adjustment',         label: 'Spot Adjustment' },
  { key: 'count',                   label: 'Trades Count' },
  { key: 'total_pnl',               label: 'Net P/L Sum' },
  { key: 'avg_profit_per_trade',    label: 'Net P/L Avg.' },
  { key: 'win_pct',                 label: 'Winners %' },
  { key: 'avg_win',                 label: 'Avg. win' },
  { key: 'loss_pct',                label: 'Looser %' },
  { key: 'avg_loss',                label: 'Avg. Loss' },
  { key: 'expectancy',              label: 'Expectancy' },
  { key: 'cagr_options',            label: 'CAGR(Options)' },
  { key: 'max_dd_pct',              label: 'DD %' },
  { key: 'spot_change',             label: 'Spot Change' },
  { key: 'roi_vs_spot',             label: 'ROI vs Spot' },
  { key: 'cagr_spot',               label: 'CAGR(Spot)' },
  { key: 'max_dd_pts',              label: 'DD(Points)' },
  { key: 'car_mdd',                 label: 'CAR/MDD Booked' },
  { key: 'car_mdd',                 label: 'CAR/MDD',          dup: true },
  { key: 'cagr_midcap',             label: 'CAGR(Midcap)' },
  { key: 'max_dd_pct',              label: 'DD',               dup: true },
  { key: 'actual_live_dd_max',      label: 'Actual Live DD' },
  { key: 'actual_live_dd_avg',      label: 'Avg Actual Live DD' },
  { key: 'car_mdd_live',            label: 'CAR/MDD Live' },
  { key: 'outlier_dd_1',            label: 'Actual Live DD Without Outlier1' },
  { key: 'outlier_dd_1_avg',        label: 'Avg Actual Live DD Without Outlier1' },
  { key: 'outlier_dd_2',            label: 'Actual Live DD Without Outlier2' },
  { key: 'outlier_dd_2_avg',        label: 'Avg Actual Live DD Without Outlier2' },
  { key: 'outlier_dd_3',            label: 'Actual Live DD Without Outlier3' },
  { key: 'outlier_dd_3_avg',        label: 'Avg Actual Live DD Without Outlier3' },
  { key: 'ce_pnl_total',            label: 'CE P&L',           conditional: 'hasCE' },
  { key: 'ce_pnl_pct',              label: 'CE P&L %',         conditional: 'hasCE' },
  { key: 'pe_pnl_total',            label: 'PE P&L',           conditional: 'hasPE' },
  { key: 'pe_pnl_pct',              label: 'PE P&L %',         conditional: 'hasPE' },
  { key: 'long_spot_pnl',           label: 'Long Spot P&L',    conditional: 'hasSpot' },
  { key: 'long_spot_pnl_pct',       label: 'Long Spot P&L %',  conditional: 'hasSpot' },
];
```

Replace with:
```javascript
/** Master-summary column layout — matches Summary_of_X.xlsx. */
export const MASTER_SUMMARY_COLUMNS = [
  { key: 'sr_no',                   label: 'Sr. No.' },
  { key: 'expiry',                  label: 'Expiry' },
  { key: 'shifting',                label: 'Shifting' },
  { key: 'put_strike_label',        label: 'Put ATM or ITM' },
  { key: 'call_strike_label',       label: 'Call ATM or ITM' },
  { key: 'spot_adjustment',         label: 'Spot Adjustment' },
  { key: 'count',                   label: 'Trades Count' },
  { key: 'total_pnl',               label: 'Net P/L Sum' },
  { key: 'total_pnl_pct',           label: 'Net P/L Sum %' },
  { key: 'avg_profit_per_trade',    label: 'Net P/L Avg.' },
  { key: 'win_pct',                 label: 'Winners %' },
  { key: 'avg_win',                 label: 'Avg. win' },
  { key: 'avg_win_pct',             label: 'Avg. win %' },
  { key: 'loss_pct',                label: 'Looser %' },
  { key: 'avg_loss',                label: 'Avg. Loss' },
  { key: 'avg_loss_pct',            label: 'Avg. Loss %' },
  { key: 'expectancy',              label: 'Expectancy' },
  { key: 'cagr_options',            label: 'CAGR(Options)' },
  { key: 'max_dd_pct',              label: 'DD %' },
  { key: 'spot_change',             label: 'Spot Change' },
  { key: 'spot_change_pct',         label: 'Spot Change %' },
  { key: 'roi_vs_spot',             label: 'ROI vs Spot' },
  { key: 'cagr_spot',               label: 'CAGR(Spot)' },
  { key: 'car_mdd',                 label: 'CAR/MDD Booked' },
  { key: 'max_dd_pct',              label: 'DD',               dup: true },
  { key: 'actual_live_dd_max',      label: 'Actual Live DD' },
  { key: 'actual_live_dd_avg',      label: 'Avg Actual Live DD' },
  { key: 'car_mdd_live',            label: 'CAR/MDD Live' },
  { key: 'outlier_dd_1',            label: 'Actual Live DD Without Outlier1' },
  { key: 'outlier_dd_1_avg',        label: 'Avg Actual Live DD Without Outlier1' },
  { key: 'outlier_dd_2',            label: 'Actual Live DD Without Outlier2' },
  { key: 'outlier_dd_2_avg',        label: 'Avg Actual Live DD Without Outlier2' },
  { key: 'outlier_dd_3',            label: 'Actual Live DD Without Outlier3' },
  { key: 'outlier_dd_3_avg',        label: 'Avg Actual Live DD Without Outlier3' },
  { key: 'ce_pnl_total',            label: 'CE P&L',           conditional: 'hasCE' },
  { key: 'ce_pnl_pct',              label: 'CE P&L %',         conditional: 'hasCE' },
  { key: 'pe_pnl_total',            label: 'PE P&L',           conditional: 'hasPE' },
  { key: 'pe_pnl_pct',              label: 'PE P&L %',         conditional: 'hasPE' },
  { key: 'long_spot_pnl',           label: 'Long Spot P&L',    conditional: 'hasSpot' },
  { key: 'long_spot_pnl_pct',       label: 'Long Spot P&L %',  conditional: 'hasSpot' },
];
```

- [ ] **Step 2: Verify the frontend still builds**

```bash
cd /home/user/Algo_Test_Software/frontend && npm run build 2>&1 | tail -20
```
Expected: build succeeds (no errors).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/utils/strategyParamSchema.js
git commit -m "feat: optimizer summary add Net P/L%, Avg win/loss%, Spot Change%; remove DD(Points), CAR/MDD dup, CAGR(Midcap)"
```

---

## Self-Review

**Spec coverage:**
- ✅ Add Net P/L Sum % → Task 4 (total_pnl_pct) + Task 5
- ✅ Add Avg. win % → Task 5 (key already in base.py summary)
- ✅ Add Avg. Loss % → Task 5 (key already in base.py summary)
- ✅ Add Spot Change % → Task 5 (key already in base.py summary)
- ✅ Remove DD(Points) → Task 5
- ✅ Remove CAR/MDD dup → Task 5
- ✅ Remove CAGR(Midcap) → Task 5
- ✅ Fix CAGR(Options) → Task 1
- ✅ Fix ROI vs Spot → Task 4a
- ✅ Fix CAR/MDD Booked → Task 2
- ✅ Fix Actual Live DD scale → Task 3 (fixes scale; true live DD needs MAE)
- ✅ Fix Avg Actual Live DD scale → Task 3
- ✅ Fix CAR/MDD Live → Task 4b
- ✅ Fix PE P&L % → Task 4c
- ✅ Fix Long Spot P&L % → Task 4c

**Placeholder scan:** All steps have explicit code. No TBDs.

**Type consistency:** `_pct_sum(col_name: str | None)` — `str | None` requires Python 3.10+. The codebase uses `from __future__ import annotations` in metrics.py (line 24) which makes this work at runtime for Python 3.8+. Safe.

**Known limitation:** "Actual Live DD" and "Avg Actual Live DD" will show the BOOKED drawdown (same as DD%) when `OPTIMIZE_SKIP_MAE_MFE=1` or the feather cache is absent. When MAE is computed (default `OPTIMIZE_SKIP_MAE_MFE=0`), they will show the true intra-trade live DD matching the individual tradesheet. This is by design — Task 3 fixes the scale so the fallback is at least in the right units.
