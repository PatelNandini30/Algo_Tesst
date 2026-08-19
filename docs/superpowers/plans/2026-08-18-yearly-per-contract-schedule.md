# Yearly Per-Contract Gap + Spot-Adjustment Schedule — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a yearly leg use a different strike gap + spot-adjustment trigger per December contract, switching at the leg's T-n roll, with a Strike-Shift-Reason note on the first trade of each new contract — fully opt-in, existing behaviour byte-identical.

**Architecture:** One pure resolver maps `(leg, held-December-contract) → {gap, pct}` or `None`. It is consulted at exactly two existing spec-time sites — the per-spec strike-interval read and the per-leg spot-adjustment resolution — both of which already know the spec's pinned December contract (`_pin["contract"]`). A third small hook stamps the Strike Shift Reason. When no leg carries a schedule, every hook returns the pre-existing value, so the pipeline is unchanged.

**Tech Stack:** Python 3 (backend `services/engine_rust.py`, no Rust change), React (`frontend/src/components/StrategyBuilder.jsx`), unittest (stub `algotest_native`, never touches the shared feather).

## Global Constraints

- **Never touch the shared NIFTY feather in tests** — stub `algotest_native` in `sys.modules` before importing `engine_rust`, exactly as `backend/tests/test_rel_leg_premium.py` does.
- **Opt-in / non-disruptive** — absent `yearly_contract_schedule` on every leg, all hooks return the exact prior value; a no-schedule parity test is mandatory.
- **Yearly leg only** — the schedule is read only when a leg is pinned to a December yearly contract (`_leg_is_yearly and _pin is not None`).
- **Units/direction unchanged** — a schedule row supplies only the two magnitudes; `spot_adjustment.direction` / `.units` stay the leg's existing values.
- **Run tests via** `docker compose exec -T -w /app worker-backtests python -m unittest tests.<module>` (host Python lacks pandas).
- Config field shape (verbatim):
  ```
  leg["yearly_contract_schedule"] = [ {"contract": "2023", "strike_gap": 1000, "spot_adj_pct": 1000}, ... ]
  ```

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `backend/services/engine_rust.py` | resolver + 3 hooks | modify |
| `backend/tests/test_yearly_contract_schedule.py` | unit + parity tests | create |
| `frontend/src/components/StrategyBuilder.jsx` | per-contract table UI + payload wiring | modify |

---

### Task 1: Schedule resolver + validation

**Files:**
- Modify: `backend/services/engine_rust.py` (add module-level helpers near the other yearly helpers, ~after `_build_yearly_cycles`, line ~905)
- Test: `backend/tests/test_yearly_contract_schedule.py` (create)

**Interfaces:**
- Produces:
  - `_yearly_schedule_row(leg: dict, contract_iso: str) -> Optional[dict]` — returns `{"strike_gap": float, "spot_adj_pct": float}` for the December-year of `contract_iso` (e.g. `"2023-12-30"` → year `"2023"`), or `None` if the leg has no schedule or no matching row.
  - `_validate_yearly_schedule(payload: dict) -> None` — raises `ValueError` on a malformed row (non-numeric gap/pct, gap ≤ 0, duplicate contract year).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_yearly_contract_schedule.py
import sys, types, unittest
sys.modules.setdefault("algotest_native", types.ModuleType("algotest_native"))
from services import engine_rust as E  # noqa: E402

SCHED = [
    {"contract": "2022", "strike_gap": 500,  "spot_adj_pct": 500},
    {"contract": "2023", "strike_gap": 1000, "spot_adj_pct": 1000},
]

class TestResolver(unittest.TestCase):
    def test_row_by_december_year(self):
        leg = {"yearly_contract_schedule": SCHED}
        self.assertEqual(E._yearly_schedule_row(leg, "2023-12-30"),
                         {"strike_gap": 1000.0, "spot_adj_pct": 1000.0})
        self.assertEqual(E._yearly_schedule_row(leg, "2022-12-30"),
                         {"strike_gap": 500.0, "spot_adj_pct": 500.0})
    def test_unscheduled_year_returns_none(self):
        self.assertIsNone(E._yearly_schedule_row({"yearly_contract_schedule": SCHED}, "2024-12-27"))
    def test_no_schedule_returns_none(self):
        self.assertIsNone(E._yearly_schedule_row({}, "2023-12-30"))
    def test_duplicate_year_rejected(self):
        p = {"legs": [{"yearly_contract_schedule": [
            {"contract": "2023", "strike_gap": 500, "spot_adj_pct": 500},
            {"contract": "2023", "strike_gap": 1000, "spot_adj_pct": 1000}]}]}
        with self.assertRaises(ValueError):
            E._validate_yearly_schedule(p)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it, verify it fails**

Run: `docker compose exec -T -w /app worker-backtests python -m unittest tests.test_yearly_contract_schedule -v`
Expected: FAIL — `AttributeError: module 'services.engine_rust' has no attribute '_yearly_schedule_row'`

- [ ] **Step 3: Implement the resolver + validator**

```python
def _yearly_schedule_row(leg, contract_iso):
    """{strike_gap, spot_adj_pct} for the December-YEAR of `contract_iso`, or None.

    `contract_iso` is the pinned December date (yearly_cycles[i].contract, e.g.
    '2023-12-30'); we key the schedule by its YEAR so the row is tied to the
    contract the leg holds, not a calendar boundary. None => caller keeps its
    existing gap / pct, which is the opt-in / fallback guarantee.
    """
    sched = (leg or {}).get("yearly_contract_schedule")
    if not isinstance(sched, list) or not sched or not contract_iso:
        return None
    year = str(contract_iso)[:4]
    for row in sched:
        if isinstance(row, dict) and str(row.get("contract")).strip()[:4] == year:
            try:
                g = float(row.get("strike_gap") or 0); p = float(row.get("spot_adj_pct") or 0)
            except (TypeError, ValueError):
                return None
            if g > 0 and p > 0:
                return {"strike_gap": g, "spot_adj_pct": p}
    return None


def _validate_yearly_schedule(payload):
    """Reject malformed rows at setup rather than silently ignoring them."""
    for leg in (payload.get("legs") or []):
        sched = (leg or {}).get("yearly_contract_schedule")
        if not isinstance(sched, list):
            continue
        seen = set()
        for row in sched:
            if not isinstance(row, dict):
                raise ValueError("yearly_contract_schedule rows must be objects")
            yr = str(row.get("contract")).strip()[:4]
            if not yr.isdigit():
                raise ValueError(f"yearly_contract_schedule: bad contract '{row.get('contract')}'")
            if yr in seen:
                raise ValueError(f"yearly_contract_schedule: duplicate contract year {yr}")
            seen.add(yr)
            try:
                if float(row.get("strike_gap") or 0) <= 0 or float(row.get("spot_adj_pct") or 0) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"yearly_contract_schedule: gap and spot_adj_pct must be > 0 (year {yr})")
```

- [ ] **Step 4: Run it, verify it passes**

Run: `docker compose exec -T -w /app worker-backtests python -m unittest tests.test_yearly_contract_schedule -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Wire the validator into the pipeline entry**

In `run_rust_engine_pipeline`, right after the existing `_assert_known_strike_modes` / `_relprem` validation block (search for `_validate_relprem(payload`), add:

```python
    _validate_yearly_schedule(payload)
```

- [ ] **Step 6: Commit**

```bash
git add backend/services/engine_rust.py backend/tests/test_yearly_contract_schedule.py
git commit -m "feat(yearly): per-contract gap/spot-adj schedule resolver + validation"
```

---

### Task 2: Strike-gap override at spec build

**Files:**
- Modify: `backend/services/engine_rust.py:2879-2886` (the `_leg_iv_raw` / `leg_interval` block in `_build_fixed_entry_specs`)
- Modify: the sibling DTE builder gap read (`engine_rust.py:3369`, same `_leg_iv_raw` pattern)
- Test: `backend/tests/test_yearly_contract_schedule.py`

**Interfaces:**
- Consumes: `_yearly_schedule_row` (Task 1), `_pin["contract"]` (the held December, in scope at both sites), `interval` (the strategy default already computed above each site).
- Produces: the yearly-leg spec's `strike_interval` equals the scheduled gap for its contract; unscheduled/absent → unchanged.

- [ ] **Step 1: Write the failing test** — a `_build_fixed_entry_specs`-level test that drives a yearly leg with `SCHED` and asserts the produced spec for a Dec-2023 entry carries `strike_interval == 1000` and a Dec-2022 entry carries `500`. Model the harness on `test_rel_leg_premium.py::_run` (stub `algotest_native.get_strikes_for_date` / `get_option_price`), building a 2-cycle `yearly_cycles` + `sorted_expiries` so `_pin["contract"]` resolves. (Full harness code: mirror `_specs`/`_payload` in `test_rel_leg_premium.py`, adding `expiry_type="YEARLY"`, `rollover_cadence="weekly"`, and `yearly_cycles=[{"contract":"2022-12-30",...},{"contract":"2023-12-30",...}]`.)

- [ ] **Step 2: Run it, verify it fails** (spec shows the leg's plain `strike_interval`, not the scheduled gap)

- [ ] **Step 3: Implement — override the interval when a schedule row exists**

Replace the block at `engine_rust.py:2882-2886`:

```python
                _leg_iv_raw = leg.get("strike_interval")
                if _leg_is_yearly and _pin is not None:
                    _yr_row = _yearly_schedule_row(leg, _pin["contract"])
                    if _yr_row is not None:
                        _leg_iv_raw = _yr_row["strike_gap"]
                try:
                    leg_interval = float(_leg_iv_raw) if _leg_iv_raw else interval
                except (TypeError, ValueError):
                    leg_interval = interval
```

Apply the identical 3-line insertion at the DTE builder's gap read (`engine_rust.py:3369`), where `_leg_is_yearly` / the pinned contract are likewise in scope (confirm the local variable name for the pin at that site before editing).

- [ ] **Step 4: Run it, verify it passes**

- [ ] **Step 5: Commit**

```bash
git add backend/services/engine_rust.py backend/tests/test_yearly_contract_schedule.py
git commit -m "feat(yearly): strike gap follows per-contract schedule"
```

---

### Task 3: Spot-adjustment trigger override per contract

**Files:**
- Modify: `backend/services/engine_rust.py` — the per-trade spot-adjustment trigger resolution. `_resolve_leg_sa` (line 6572) returns a leg-level dict; the cascade calls `_compute_spot_adjustment_trigger(..., pct, units, ...)` per trade. Thread the held contract into the pct.
- Test: `backend/tests/test_yearly_contract_schedule.py`

**Interfaces:**
- Consumes: `_yearly_schedule_row`, the per-trade held contract (the trade's yearly-leg expiry), the leg's base `_resolve_leg_sa` dict.
- Produces: for a yearly leg with a schedule, the trigger used for a trade equals its contract's `spot_adj_pct`; direction/units unchanged; unscheduled → the leg's base pct.

- [ ] **Step 1: Write the failing test** — drive the trigger scan for a yearly leg holding Dec-2022 (expect fire at entry_spot+500) vs Dec-2023 (fire at entry_spot+1000), asserting on the resolved `pct` passed to `_compute_spot_adjustment_trigger`. If direct assertion on the internal call is awkward, assert on the resulting truncation date given a synthetic `spot_by_date` path that crosses +500 but not +1000.

- [ ] **Step 2: Run it, verify it fails** (both contracts use the single base pct)

- [ ] **Step 3: Implement — resolve pct per trade from the held contract**

At the site where the leg's spot-adj `pct` is chosen for a given trade (inside the cascade loop, where the trade's yearly-leg expiry / `_pin`-equivalent contract is known), override:

```python
            _sa_pct = _base_leg_sa["pct"]  # from _resolve_leg_sa
            if _leg_is_yearly:
                _yr_row = _yearly_schedule_row(_leg_src, _trade_contract_iso)
                if _yr_row is not None:
                    _sa_pct = _yr_row["spot_adj_pct"]
```

Then pass `_sa_pct` (not the base pct) into `_compute_spot_adjustment_trigger`. Locate the exact cascade variable holding the trade's yearly contract (the per-trade expiry) before editing; if none is directly available, derive it from the trade's yearly-leg spec expiry (same `[:4]` December-year key the resolver uses).

- [ ] **Step 4: Run it, verify it passes**

- [ ] **Step 5: Commit**

```bash
git add backend/services/engine_rust.py backend/tests/test_yearly_contract_schedule.py
git commit -m "feat(yearly): spot-adjustment trigger follows per-contract schedule"
```

---

### Task 4: Strike Shift Reason on the first trade of a new contract

**Files:**
- Modify: `backend/services/engine_rust.py` — Strike Shift Reason assembly (`:4414`)
- Test: `backend/tests/test_yearly_contract_schedule.py`

**Interfaces:**
- Consumes: the yearly-leg trades in entry order, each trade's held contract, `_yearly_schedule_row`.
- Produces: the first trade of each scheduled contract whose values differ from the prior contract carries `YEARLY_ROLL → Dec-<year> (gap <g>, adj <p>)`, joined with `" + "` to any existing reason; no other trade carries it.

- [ ] **Step 1: Write the failing test** — a 2-contract run; assert the first Dec-2023 trade's `Strike Shift Reason` contains `YEARLY_ROLL → Dec-2023 (gap 1000, adj 1000)`, a later Dec-2023 trade does not, and an unscheduled year is blank.

- [ ] **Step 2: Run it, verify it fails**

- [ ] **Step 3: Implement — track the last-seen scheduled contract per yearly leg and stamp on change**

Add a small post-pass over the priced rows (after the tradesheet rows exist, before returning) keyed per yearly leg: walk trades in entry order, remember the last contract year that produced a schedule row; when the current trade's contract year changes AND has a row, append the `YEARLY_ROLL → …` token to that trade's `Strike Shift Reason` via the existing `" + "` join helper. Skip when `_yearly_schedule_row` is `None` (unscheduled year → no note).

- [ ] **Step 4: Run it, verify it passes**

- [ ] **Step 5: Commit**

```bash
git add backend/services/engine_rust.py backend/tests/test_yearly_contract_schedule.py
git commit -m "feat(yearly): stamp YEARLY_ROLL reason on first trade of new contract"
```

---

### Task 5: No-schedule parity guard

**Files:**
- Test: `backend/tests/test_yearly_contract_schedule.py`

- [ ] **Step 1: Write the parity test** — run the same yearly config **twice**, once with `yearly_contract_schedule` removed from every leg and once with it absent by construction, and assert the produced spec list (strikes, intervals, expiries) and any Strike Shift Reason are **identical** to a baseline captured with the resolver forced to return `None`. This proves opt-in non-disruption.

- [ ] **Step 2: Run it, verify it passes** (all hooks are no-ops without a schedule)

- [ ] **Step 3: Run the whole RELPREM + yearly suites to confirm nothing regressed**

Run: `docker compose exec -T -w /app worker-backtests python -m unittest tests.test_rel_leg_premium tests.test_yearly_contract_schedule -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_yearly_contract_schedule.py
git commit -m "test(yearly): opt-in parity guard for per-contract schedule"
```

---

### Task 6: Frontend — per-contract table on the yearly leg

**Files:**
- Modify: `frontend/src/components/StrategyBuilder.jsx` — render an editable `Contract (Dec-YYYY) | Strike gap | Spot-adj` table under a leg **only when its expiry is Yearly**; map it into the payload as `leg.yearly_contract_schedule` in `buildPayload` (near the existing per-leg field mapping, e.g. `lots: l.lot || 1`).
- Build: node-container build + `docker compose up -d --build frontend` (per CLAUDE.md; vite `outDir=build`).

**Interfaces:**
- Consumes: the leg's expiry selector state (Yearly).
- Produces: `basePayload.legs[i].yearly_contract_schedule = [{contract, strike_gap, spot_adj_pct}]`; empty table → field omitted (so backend stays on the fallback path).

- [ ] **Step 1: Add the table + state** — a repeatable row (add/remove) shown when `leg.expiry === 'yearly'`; three inputs per row (contract year, gap, spot-adj). Follow the existing per-leg control styling.

- [ ] **Step 2: Map into buildPayload** — emit `yearly_contract_schedule` only when the leg is yearly and has ≥1 complete row; omit otherwise.

- [ ] **Step 3: Build + publish the frontend**

```bash
docker run --rm -e NODE_TLS_REJECT_UNAUTHORIZED=0 -v "$PWD/frontend":/src node:22-bookworm-slim \
  sh -c 'npm config set strict-ssl false; mkdir -p /b && cp -r /src/. /b/ && cd /b && \
    rm -rf node_modules build dist && npm install --no-audit --no-fund && npm run build && \
    rm -rf /src/build && cp -r /b/build /src/build'
docker compose up -d --build frontend
```

- [ ] **Step 4: Manual check** — in the UI, a yearly leg shows the table; a weekly leg does not; a filled row round-trips into the submitted payload (verify via `docker compose logs backend | grep 'queued job'` then inspect the stored request or a debug echo).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/StrategyBuilder.jsx frontend/build
git commit -m "feat(ui): per-contract gap/spot-adj table on the yearly leg"
```

---

## Self-Review

- **Spec coverage:** scope/opt-in (Tasks 1,5) · config table (Task 1,6) · row-applies-by-held-contract (Tasks 1–3) · gap switch (Task 2) · trigger switch (Task 3) · fallback (Tasks 1,5) · Excel reason on first trade (Task 4) · UI (Task 6) · optimizer out-of-scope (not planned). ✔ every spec section maps to a task.
- **Placeholder scan:** Tasks 2–4 describe the engine edits with exact line anchors and the transform, but require the implementer to confirm one local variable name (the per-trade pin/contract) at the DTE and cascade sites before editing — this is a real "read the two lines first" instruction, not a TODO. All resolver/validator/test code is concrete.
- **Type consistency:** `_yearly_schedule_row(leg, contract_iso) -> {"strike_gap","spot_adj_pct"}` used identically in Tasks 2, 3, 4; field names match the config shape in Global Constraints.
