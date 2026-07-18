# Yearly Expiry + 1000 Strike Gap + Cadence-Decoupled Rollover — Design

**Date:** 2026-07-17
**Status:** Approved design, pending implementation plan
**Constraint (hard) #1:** Nothing existing may break. Weekly/monthly strategy output must remain byte-identical.
**Constraint (hard) #2:** **Rust-only on the calculation path.** No Python calc, no Python fallback.
Reporting/export (WOW/MOM, Excel) may remain Python. See D9. Ref: `rust-only-no-python-fallback`.

---

## 1. Summary

Four user-facing additions to the EOD backtester:

1. **YEARLY expiry** — trade the long-dated December contract (26-Dec-2019, 31-Dec-2020, …).
2. **1000 strike gap** — a new selectable Strike Gap.
3. **Weekly/monthly rollover *within* a pinned yearly contract**, in Fresh and Fixed strike modes.
4. **WOW/MOM** made meaningful for yearly expiry.

### The core insight

Today, **roll cadence and contract expiry are the same list**. `engine_rust.py:2257` is literally
`next_exp = sorted_expiries[i+1]` / `contract_exp = sorted_expiries[i+2]`, and
`build_rollover_schedule` (`simulate.rs:408-481`) builds **one cycle per expiry**.

Yearly expiry is the first case where the user rolls on one calendar (weekly/monthly) while
holding a contract from another (December). **Splitting those two apart is the actual feature.**
The enum addition is trivial by comparison.

One list becomes three independent knobs:

| Knob | Today | With yearly |
|---|---|---|
| Contract held | = the roll list | Pinned to December, exited T-n months before |
| Roll cadence | = the roll list | Weekly or monthly, independent |
| Strike refresh | = every roll | Month-end (Fresh) / never (Fixed) |

---

## 2. Verified facts (evidence, not assumption)

### 2.1 Data — the yearly contract exists and needs no ingestion

NSE lists two long-dated series, distinguishable by contract life:

| Series | Life | Examples |
|---|---|---|
| Jun/Dec semi-annual | **1826 days (5 yrs)** | 2019-06-27, **2019-12-26**, 2020-06-25, **2020-12-31** |
| Mar/Sep quarterly | 363–370 days | 2019-03-28, 2019-09-26, 2020-03-26 |

**The December member of the 1826-day series is "the yearly".** Chain: 26-Dec-2019 → 31-Dec-2020
→ 30-Dec-2021 — exactly the user's sheet.

**These already exist as December rows in the `monthly` `expiry_calendar`** (verified:
`expiry_type='monthly', current_expiry='2019-12-26'` is present). So the yearly list is a
**filter of existing data** — no ingestion, no calendar migration, no new table.

### 2.2 The user's example is real data

11000 PE / expiry 2019-12-26 / date 2019-02-28 → **close = 625.00**, matching the sheet exactly.
Spot that day was 10792.50 (the sheet's "10600" is approximate; it rounds to 11000 either way).
The sheet's 550/575/400 and Fresh-13000 are **placeholders** — the sheet communicates *structure*,
not prices.

### 2.3 The 1000 gap is a liquidity requirement, not a preference

Within the 26-Dec-2019 contract across 2019:

| Strikes | Rows | Liquid rows | Liquid % |
|---|---|---|---|
| Round-1000 | 4,840 | 2,865 | **59%** |
| All others | 47,380 | 5,719 | **12%** |

Long-dated open interest only collects at round-1000 strikes. Yearly expiry and the 1000 gap are
one feature, not two.

### 2.4 Rollover does **not** currently survive yearly pinning

The user's belief that "rollover already works, just add yearly" is **half true**. The Fresh/Fixed
*strike* half works (`rollover_strike_mode`, independent of expiry). The *rolling* half does not:

| Blocker | Evidence | Effect |
|---|---|---|
| Rollover gate is WEEKLY/MONTHLY-only | `simulate.rs:1201` — `truthy && (etype == "WEEKLY" \|\| etype == "MONTHLY")` | **YEARLY silently sets `rollover_active=false`.** No error. One trade. |
| Schedule = one cycle per expiry | `simulate.rs:408-481` `build_rollover_schedule`; `engine_rust.py:1819` `while exit_date <= current_entry: target_idx += 1` | Yearly list ⇒ **one trade per year** (Dec-2019 → Dec-2020) |
| Hard expiry bump | `generic_algotest_engine.py:4807-4819` | Entry ≥ expiry force-advances the contract. **Parity reference only — not a live blocker** (§2.8, D10). Listed because the parity reference must model yearly too, or it cannot be used to check yearly. |

### 2.5 WOW collapses under yearly; MOM does not

`wow_mom.py:207-220`:

```python
e = _parse_date(t.get("Expiry"))     # WOW  → ISO week of EXPIRY
    y, w = _iso_year_week(e)
x = _parse_date(t.get("Exit Date"))  # MOM  → month of EXIT DATE
    mi = x.month - 1
```

Verified bucket collapse:

- Expiry 2019-12-26 → ISO `(2019, 52)`
- Expiry 2020-12-31 → ISO `(2020, 53)`

Every yearly trade shares one expiry ⇒ **the entire year lands in a single WOW cell (week 52)**.
MOM is Exit-Date-based and already works (Feb/Mar/Apr/May resolve correctly).

**Why WOW uses Expiry at all (and why that is correct for weekly):** for a weekly strategy the
trade *is* the weekly contract, so Expiry is its natural week identity. With a T-n exit a trade may
exit in the *previous* calendar week while still belonging to that expiry's contract; bucketing by
Expiry keeps one contract's P&L in exactly one week. `_first_thu_week` (`wow_mom.py:59`) anchors
the month banding on expiry-Thursday, confirming the intent. The principle is
**"one contract → one bucket"** — and under yearly the contract *is* the whole year. That is
precisely why it collapses.

### 2.6 The 1000 gap is nearly free — but two LIVE allowlists already break 500

`simulate.rs:523-525` is a **threshold, not a list**:

```rust
fn liquidity_walk_step(index: &str, interval: f64) -> (f64, bool) {
    if interval <= 100.0 { return (interval, false); }   // 1000 is automatically "coarse"
```

So the liquidity-shift walk needs **zero change** for 1000. But hardcoded allowlists silently
downgrade coarse gaps — **already breaking the uncommitted 500 work today**:

| Site | Code | Effect | Live? |
|---|---|---|---|
| `engine_rust.py:5632` | `if _sa_leg_interval not in (50.0, 100.0, 25.0):` | Spot-adj re-strike reverts to native gap | **YES** — inside `run_rust_engine_pipeline` (def `:3435`) |
| `multi_index_feature.py:340` | `getattr(cfg, "strike_interval", 25) or 25` | Overlay legs ignore Strike Gap entirely | **YES** |
| `generic_algotest_engine.py:790` | `return parsed if parsed in (50, 100) else fallback` | Drops 500/1000/25 → native gap | **NO — dead** (see §2.8) |

All fail **silently** — wrong strike, no error.

### 2.8 The live path is "Rust-priced, Python-orchestrated" — not Rust-only

Verified, and it contradicts the common assumption that `engine_rust.py` / `multi_index_feature.py`
/ `wow_mom.py` are already Rust-only:

| Claim | Evidence |
|---|---|
| **The legacy Python engine is dead.** | No call to `run_algotest_backtest(` exists outside `tests/` and `parity_matrix.py`. The import at `routers/backtest.py:5` is a leftover. `rust_combo_loop.py:10` states it "is already deleted". **⇒ never fix `generic_algotest_engine.py`; it is a parity reference only.** |
| **`engine_rust.py` computes strikes in Python on the live path.** | `:5632-5636` sits inside `run_rust_engine_pipeline` and calls `_compute_strike_for_leg_python`, whose docstring (`:1509`) reads *"Python mirror of simulate.rs::compute_strike_for_leg"*. |
| **`multi_index_feature.py` has a literal Python fallback.** | `:405` — `strike = _compute_strike_for_leg_python(...) if ... else round(espot/interval)*interval`, wrapped in `except Exception:` returning the same rounding. |
| **`wow_mom.py` is 100% Python compute.** | `xlsx_writer.rs:290` — *"Python computes every value; Rust only writes the styled cells"*; `xlsx_writer.rs:6` — *"WOW-MOM remain openpyxl until each passes the same gate."* Rust has **no** WOW/MOM logic. |
| **The EOD Rust path never resolves expiry kind.** | Python resolves and passes `expiry_dates: Vec<String>` in (`simulate.rs:1149`, `lib.rs:1586`). |

Architecture is self-described at `rust_combo_loop.py:11`: the live path is
*"Rust-priced, **Python-orchestrated**"*.

### 2.9 The Python mirror silently diverges from Rust at half-points

`engine_rust.py:1531` uses `round(entry_spot / interval) * interval` — Python's `round()` is
**banker's rounding**. `simulate.rs:141` uses `(spot_price / strike_interval).round()` — Rust's is
**half-away-from-zero**. They disagree whenever spot lands exactly on a half-interval:

| Spot | Gap | Python | Rust |
|---|---|---|---|
| 11025.00 | 50 | 11000 | **11050** |
| 11050.00 | 100 | 11000 | **11100** |
| 10500.00 | 1000 | 10000 | **11000** |

Real-data frequency across 6,586 NIFTY spot days: **3 days at gap 50, 2 at gap 100, 0 at gap 1000**
(0.08% overall). Rare, but real — and on those days the **Python mirror is the wrong one**. This is
an argument for deleting the mirror, not patching it.

### 2.7 The cautionary precedent

`WEEKLY_T2 = "Weekly_T2"` (`strategy_types.py:29`) is advertised at `routers/strategies.py:42` but
appears in **no resolver and no match arm**. It silently degrades to WEEKLY today. YEARLY must not
join it. This is the exact failure mode the design must prevent.

---

## 3. Decisions (confirmed with user)

| # | Decision | Value |
|---|---|---|
| D1 | Yearly contract | December member of the 1826-day series = December row of the `monthly` calendar |
| D2 | Yearly exit | **T-n months** before the December expiry. **T=0 = hold to expiry (default)**; T-1 = 26-Nov-2019. User-selectable ("dynamic"). |
| D3 | Yearly roll handoff | Exit, re-enter next December **fresh from that day's spot** (even in Fixed mode); **cadence continues unbroken** |
| D4 | Fixed scope | Fixed = fixed **within a yearly cycle**, not across the whole backtest |
| D5 | Strike refresh cadence | Fresh re-strikes at **month-end only**, regardless of roll cadence. WOW vs MOM changes the *roll* cadence, not the *strike* cadence. |
| D6 | Roll anchor | The existing **weekly/monthly expiry calendar** (monthly ⇒ 28-Mar, 25-Apr, 30-May; weekly ⇒ Thursdays). *Not* calendar month-end. |
| D7 | WOW fix scope | **Yearly-only** branch. Weekly/monthly WOW keeps bucketing by Expiry, output unchanged. |
| D8 | MOM | Unchanged — already Exit-Date-based |
| D9 | **Rust-only mandate** | **Engine/calc (strike, rollover, cadence, pricing) must be Rust. No Python calc, no Python fallback.** Reporting/export (WOW/MOM, Excel) may remain Python. |
| D10 | `generic_algotest_engine.py` | **Not touched.** Dead on the live path (§2.8); parity reference only. The `(50,100)` allowlist is left alone. |
| D11 | WOW/MOM | **No Rust port.** `wow_mom.py` stays Python; add the yearly-only bucketing branch there. Weekly/monthly output byte-identical. |
| D12 | Python strike mirrors | **Delete, don't patch** — route `engine_rust.py:5632` and `multi_index_feature.py:405` to Rust `compute_strike_for_leg`. Gated by the parity matrix (§8). *Pending final user confirmation.* |

### D9 rationale — why this split is coherent

The rule is not "no Python anywhere" — D11 keeps WOW/MOM in Python. The rule is **no Python on the
calculation path**. Strike/rollover/cadence are calculation ⇒ Rust. WOW/MOM is reporting over an
already-computed tradesheet ⇒ Python is acceptable. This matches the existing architecture and the
`rust-only-no-python-fallback` hard rule (optimizer/engine Rust-only, hard-fail not fallback).

### D12 impact — does deleting the mirrors change results?

**No, except where the mirror is currently wrong.** The ATM formula is identical
(`round(spot/iv)*iv` vs `(spot/iv).round()*iv`), so same interval ⇒ same strike. Three exceptions:

1. **Gap 500/1000 → changes.** `engine_rust.py:5632` forces the interval back to native; Rust does
   not. *This is the objective* — it is what makes 500/1000 correct on the spot-adj path.
2. **Overlay gap → changes.** `multi_index_feature.py:340` ignores the leg's gap entirely.
3. **Half-points → changes on 5 of 6,586 days (0.08%)**, per §2.9, where Python is the wrong one.

⇒ Existing 25/50/100 strategies are **identical on 6,581 of 6,586 days**; the 5 that move are a bug
fix, not a regression. Verified by the §8 parity matrix before merge, not assumed.

### D2 interaction: T-n truncates the final cadence segment

With T-1, the yearly exits **26-Nov-2019**, which falls *between* the 31-Oct and 28-Nov monthly
rolls. The final segment is therefore **truncated mid-cadence** (31-Oct → 26-Nov), then the
Dec-2020 cycle begins. This matches the user's sheet, whose last row ends at 11/26/2019 rather than
on a monthly boundary. **The T-n exit overrides the cadence boundary.**

With the T=0 default this is clean: the December *monthly* expiry **is** the yearly expiry
(26-Dec-2019), so the last segment ends naturally.

---

## 4. Reference behaviour (live data, agreed rules)

Contract 26-Dec-2019, monthly cadence, entry 28-Feb-2019. Roll dates are real monthly expiries;
prices are real closes. Fresh strike = `mround(spot, 1000)`.

| Roll date | Spot | Fixed strike | Fresh strike | 11000PE close |
|---|---|---|---|---|
| 2019-02-28 | 10792.50 | 11000 | 11000 | 625.00 |
| 2019-03-28 | 11570.00 | 11000 | **12000** | 293.90 |
| 2019-04-25 | 11641.80 | 11000 | 12000 | 290.50 |
| 2019-05-30 | 11945.90 | 11000 | 12000 | 177.90 |
| 2019-06-27 | 11841.55 | 11000 | 12000 | 121.60 |
| 2019-07-25 | 11252.15 | 11000 | **11000** | 197.90 |
| 2019-08-29 | 10948.30 | 11000 | 11000 | 291.45 |
| 2019-09-26 | 11571.20 | 11000 | **12000** | 112.85 |
| 2019-10-31 | 11877.45 | 11000 | 12000 | 64.20 |
| 2019-11-28 | 12151.15 | 11000 | 12000 | 7.85 |

**In Fixed mode the exit price equals the next entry price** (same strike, same expiry, same day).
Fixed is therefore not a trade sequence — it is **one yearly hold chopped into mark-to-market
segments for attribution**. Fresh is a genuine trade change. This distinction is what makes WOW/MOM
attribution the point of the feature.

This table is the acceptance fixture: an implementation that does not reproduce it is wrong.

---

## 5. Design

### 5.1 Yearly expiry list (new basis, derived)

Derive the yearly list **in code** from the existing monthly calendar by selecting December
expiries. Rationale: avoids the `003` migration `CHECK (expiry_type IN ('weekly','monthly'))` and
the `VARCHAR(10)` column, avoids new ingestion, and keeps one source of truth.

**Resolution rule:** for entry date `D`, the yearly contract is the first December expiry `E` such
that `E - n months >= D`. Exit anchor = `E - n months`, snapped to the nearest prior trading day.

### 5.2 Cadence decoupling (the new mechanic)

`build_rollover_schedule` keeps its shape but takes **two lists** instead of one:

- **cadence list** → weekly or monthly expiry calendar; drives entry/exit boundaries
- **contract expiry** → the pinned December; does **not** advance with the cadence

The `while exit_date <= current_entry: target_idx += 1` advance walks the **cadence** list. The
contract advances only at the yearly T-n boundary.

The final cadence segment of a yearly cycle is truncated to the T-n exit date (§3, D2 interaction).

### 5.3 Strike refresh

Reuse `rollover_strike_mode` unchanged. Add a **month-end predicate** for Fresh under yearly:
re-strike only when the roll boundary is a monthly boundary. Under monthly cadence every boundary
qualifies (so behaviour is identical to Fresh today); under weekly cadence only the monthly-expiry
boundary qualifies.

At the yearly T-n roll, clear the fixed-strike memo so the new cycle re-strikes fresh (D3/D4).

### 5.4 1000 strike gap — Rust-only (D9)

- Frontend: add `1000` to `STRIKE_INTERVAL_OPTIONS` (`StrategyBuilder.jsx:360`) and
  `strikeIntervalOptionsForIndex` (`:367-370`).
- `liquidity_walk_step` (`simulate.rs:523`): **no change** — `interval <= 100.0` is a threshold, so
  1000 is automatically coarse.
- **Do not patch the Python allowlists — delete the Python mirrors (D12).** Route
  `engine_rust.py:5632` (spot-adj) and `multi_index_feature.py:405` (overlay) to Rust
  `compute_strike_for_leg`, and delete `_compute_strike_for_leg_python` plus the
  `except Exception: round(espot/interval)*interval` fallback. Rust becomes the single source of
  truth, so the §2.9 divergence cannot recur by construction.
- `generic_algotest_engine.py:790`: **untouched** (D10 — dead path).
- Update the stale docstrings that name "500" literally (`simulate.rs:78`, `:516-522`,
  `lib.rs:334-336`).

### 5.5 WOW/MOM — Python, no Rust port (D11)

- `build_wow_mom` gains a **yearly-only** branch: bucket by **Exit Date's ISO week** instead of
  Expiry's. Roughly: `week = iso_week(exit_date) if yearly else iso_week(expiry)`.
- Weekly/monthly path untouched — guarded by a parity test asserting identical output.
- MOM: no change.
- **Explicitly not doing** the 862-line Rust port of `wow_mom.py`. It is reporting, not calculation
  (D9), and `xlsx_writer.rs:6` already tracks it as deferred work behind its own cell-identical
  gate. Bundling it here would put the numbers behind every existing optimizer output at risk for a
  ~3-line bucketing change.
- The yearly flag must reach `build_wow_mom`; it is called from `runner.py:810 _prebuild_wow_mom`
  and `excel_builder.py:2468`, so the plumbing is via `base_payload`.

Coverage check across all four combos:

| Combo | WOW populated weeks | MOM populated months |
|---|---|---|
| Yearly + weekly cadence | ~52 | 12 (≈4 segments summed per month) |
| Yearly + monthly cadence | 12 (one per month) | 12 |

---

## 6. Fail loud — non-negotiable

Every site below gets an **explicit YEARLY arm**. Never rely on `_` / `else` fallthrough.

| Site | Current behaviour | Required | Scope |
|---|---|---|---|
| `simulate.rs:1201` | `etype == "WEEKLY" \|\| etype == "MONTHLY"` | Admit YEARLY, else rollover silently dies | **LIVE — must fix** |
| `engine_rust.py:2174` | `if ... else freq="weekly"` | Explicit arm | **LIVE — must fix** |
| `base.py:3047` | empty weekly ⇒ silently monthly | Must not swallow yearly | **LIVE — must fix** |
| `base.py:3213` | raises on unknown | Keep raising; add YEARLY | **LIVE — must fix** |
| `rust_combo_loop.py:98,141` | unknown kind **silently admitted** to a loop that can't handle it | **Exclude YEARLY** until genuinely supported | **LIVE — must fix** |
| `generic_algotest_engine.py:3576` | `else:` → `'monthly'` | Explicit arm **only if** the parity reference must model yearly | Parity ref (D10) |
| `generic_algotest_engine.py:1535` | `elif expiry_raw not in (...): 'WEEKLY'` | As above | Parity ref (D10) |
| `intraday/calendar.rs:70` | `_ => expiries.first()` | **No change** | **Out of scope** — intraday (§10) |

Note `calendar.rs` is `backend/native/src/intraday/` — the orphaned intraday subsystem, not the EOD
path. It is listed only to record that it was reviewed and deliberately excluded.

The `_NEXT_EXPIRY_TYPES` set is **duplicated in 8 places** (`engine_rust.py:645`,
`rust_combo_loop.py:98`, `generic_algotest_engine.py:3565/3618/3713/4786`,
`generic_multi_leg.py:450`, `test_rust_combo_whitelist.py:106`). Of these, **4 sit in the dead
`generic_algotest_engine.py`** (D10). The live set is `engine_rust.py:645`, `rust_combo_loop.py:98`,
`generic_multi_leg.py:450`, plus the test. Consolidate to one source, or update in lockstep with a
test asserting they agree.

**Open question for the plan:** D10 says never fix `generic_algotest_engine.py` — but it is the
parity reference (`parity_matrix.py:31`). If it does not model YEARLY, **yearly cannot be
parity-checked against Python at all**, and Rust becomes self-certifying. That is arguably fine
(Rust is authoritative by D9), but it must be a conscious choice, not an accident. Resolve before
implementing.

---

## 7. Touch list

**Backend (Rust) — where the feature actually lives (D9)**
`simulate.rs:1201` (admit YEARLY to the rollover gate), `:408-481`
(`build_rollover_schedule` → two-list: cadence + pinned contract), `:523` (no change),
`compute_strike_for_leg` (now the sole strike authority once the mirrors die), stale docstrings.

**Backend (Python) — orchestration only, no calc**
`strategy_types.py:24`, `index_metadata.py:19-25` (`expiry_bases`), `base.py:3159` + `:3047`
(yearly list = December rows of the monthly calendar), `engine_rust.py:2174/2257/1819`
(orchestration) and `:5632` + `:1496` (**delete the mirror**), `multi_index_feature.py:340/405`
(**delete the mirror + bare-except fallback**), `wow_mom.py:207` (yearly bucketing branch — D11),
`rust_combo_loop.py:98`, `routers/strategies.py:42`, the 8 duplicated sets.

**Not touched** — `generic_algotest_engine.py` (D10, dead path).

Note: **the EOD Rust path never resolves expiry kind** — Python passes resolved dates in
(`simulate.rs:1149`, `lib.rs:1586`). Yearly *date lookup* therefore stays Python-side as
orchestration. Moving resolution into Rust would touch every existing expiry kind and is the single
largest threat to "nothing breaks"; explicitly **out of scope** (§10).

**Frontend** — `StrategyBuilder.jsx:98-108` (expiry options), `:360/:367-370` (gap),
new cadence + T-n controls, `strategyParamSchema.js:123` (optimizer enum),
`backtestRulesSheet.js`, `optimRulesInfo.js`.

**No DB migration.** Yearly derives from the monthly calendar; the gap is not DB-backed.

---

## 8. Testing

1. **Parity guard (the "nothing breaks" gate):** existing weekly/monthly archetypes
   (`backend/tests/parity/archetypes.py`) must produce **byte-identical** tradesheets and WOW/MOM
   before and after. This is the primary acceptance criterion.
2. **Reference fixture:** the §4 table reproduced exactly, Fixed and Fresh, monthly and weekly cadence.
3. **Yearly roll:** T=0 ends 26-Dec-2019; T-1 ends 26-Nov-2019 and truncates the final segment.
4. **WOW:** yearly run spreads across weeks (not all in week 52); weekly run unchanged.
5. **Gap:** 1000 selected ⇒ strikes are round-1000; illiquid ⇒ liquidity-shift walks the fine step
   toward ATM and reports it in "Strike Shift Reason". Add a 500 regression test for the repaired
   spot-adj and overlay paths.
6. **Mirror deletion (D12) — the gate that must pass before it merges:**
   a. Enumerate every strike-selection mode reachable at `engine_rust.py:5632` and
      `multi_index_feature.py:405`; confirm Rust covers each (see §9 risk).
   b. Run the parity matrix over existing archetypes at gaps 25/50/100 → expect **zero diffs**.
   c. Assert the 5 known half-point days (§2.9) now follow Rust, and that no *other* day moves.
   d. Assert gap 500/1000 no longer downgrades to the native gap on either path.
6. **Fail-loud:** an unknown expiry kind raises rather than degrading — a direct regression test for
   the `WEEKLY_T2` class of bug.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Silent degradation (the `WEEKLY_T2` class) | §6 explicit arms + fail-loud test |
| **Deleting the Python mirrors changes results** | Bounded and measured, not assumed: identical on 6,581/6,586 days; changes confined to gap 500/1000 (the objective) and 5 half-point days where Python is wrong (§2.9, D12). Parity matrix over existing archetypes is the merge gate. |
| Mirror deletion regresses a mode Rust lacks | The mirror covers ATM/ITM/OTM/pct_of_atm always, premium modes only when `entry_date`+`expiry`+`index` are supplied (`:1509-1517`). **Confirm Rust covers every mode the two call sites can reach before deleting** — else this becomes a functional regression, not a cleanup. |
| Repairing 500 changes existing 500 results | 500 is currently silently wrong and **uncommitted**; call the change out explicitly rather than shipping it quietly |
| 8 duplicated expiry sets drift | Consolidate, or update in lockstep with a test asserting they agree |
| Long-dated illiquidity | The 1000 gap targets the 59%-liquid round strikes; the existing liquidity-shift walk handles the remainder |
| Optimizer combo loop admits yearly it cannot run | Explicitly exclude YEARLY from `rust_combo_loop` until supported |

---

## 10. Out of scope

- Jun (semi-annual) and Mar/Sep (quarterly) long-dated expiries. The design derives December only;
  extending to other months is a later, mechanical follow-on.
- Multi-index overlay support for yearly (`multi_index_feature.py` overlay legs ignore
  `rollover_strike_mode` today — pre-existing, not addressed here beyond the mirror deletion).
- **Porting `wow_mom.py` to Rust** (D11) — reporting, not calculation; deferred behind its own
  cell-identical gate per `xlsx_writer.rs:6`.
- **Moving expiry-kind resolution into Rust** — the EOD Rust path has never done this (§2.8); doing
  it would touch every existing expiry kind and directly threatens the "nothing breaks" constraint.
- **`generic_algotest_engine.py`** (D10) — dead on the live path; parity reference only.
- Intraday (`backend/native/src/intraday/`) — separate product.
