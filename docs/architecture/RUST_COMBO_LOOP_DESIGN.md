# Rust Combo-Loop — Implementation Blueprint

**Goal:** make optimizer sweeps dramatically faster by moving the *combo loop* into Rust
(one Rust call prices + computes metrics for many combos in parallel over one shared
mmap'd cache), **without changing a single number, file, or feature that works today.**

This is a design/plan document. **No code has been changed.** It is grounded in a
full read of the current code (Python sweep flow, every per-combo output, and the Rust
engine) plus an adversarial parity review. File anchors are `file:line` in `backend/`.

---

## 0. The one-line answer to "will it break anything?"

**No — if we build it the way described here, because the design never *replaces* the
proven path; it runs a new path *beside* it and only switches over combo-by-combo after
proving byte/cell-identical output.** Three structural safety nets make that true:

1. **A feature flag `OPTIMIZE_RUST_LOOP` defaults to `0` = today's exact code.** With it off,
   the new Rust code is never even reached. This is the always-available fallback.
2. **A `shadow` mode** runs the new Rust path *alongside* the current Python path, where
   **Python stays authoritative** (the user sees only Python's output) while a differ logs
   any mismatch. We collect proof on real jobs with zero risk to real output.
3. **A `needs_python` fallback:** any strategy shape the Rust path doesn't fully own
   (spot-adjustment, midcap, re-entry, next-weekly, filters, futures…) automatically runs
   on the **existing proven Python engine** — unchanged. Rust only ever handles the simple
   shapes it can prove identical.

We flip a strategy family to "Rust-authoritative" **only after** a dual-run diff shows
**zero** differences across a test corpus. Nothing ships that isn't proven equal first.

**The one honest caveat** (call it out now): a full sweep *including the Excel downloads*
under a minute eventually needs Excel written by Rust, and two different writers can't make
**byte-identical** `.xlsx` files (the zip container/XML differ even when every cell is equal).
That last step is **opt-in, off by default, and requires your explicit sign-off** that
**cell-identical** (same sheets, same cell values, same number formats) is acceptable. Until
then, Excel stays on the current Python writer and is **byte-identical** — you still get a
big speedup, just not the absolute-fastest download step.

---

## 1. Why it's slow today (grounded)

Per combo, the worker runs (`parallel.py:131-543`):

1. `apply_combo_for_optim` (merge params) — Python
2. `_run_single_backtest` → `run_rust_engine_pipeline` — **the actual pricing is already Rust**
   (`simulate_trades_batch`, `simulate.rs:1188`, all f64), wrapped in Python orchestration
3. `_compute_mae_mfe_batch` — Python/pandas OHLC join (`runner.py:1189`)
4. `compute_optim_metrics` + `_cmetrics` **×2** (overall+patchwise) + `build_cleaned_for_combo`
   **×2** + WOW/MOM — Python/pandas
5. `append_result` → Redis write
6. `write_combo_tradesheet` (CSV) + `write_combo_xlsx` **×2** — **openpyxl, ~720 ms/combo**
   (the 546-combos-in-393s measurement is at `runner.py:745-752`)

**So the Rust backtest is already milliseconds. ~70–85% of per-combo wall-clock is Python:**
the MAE/MFE join, the twice-per-combo pandas metric passes, and above all **openpyxl Excel
writing.** On top of that, parallelism is clamped to **P=6** (not the 12 cores available)
because each forked Python worker needs ~700 MB private RAM (`get_parallelism`,
`parallel.py:60-114`), so RAM — not CPU — caps the fan-out.

---

## 2. The change — before → after

**Before:** fork P Python worker-processes; each runs a *sequential* Python combo loop; the
combo re-enters Rust ~6–10 times through the GIL, then does all metrics/Excel in Python.

**After:** one process; Python loops over *chunks* of combos; each chunk is **one Rust call**
(`run_combo_batch`) that Rayon-parallelizes across all cores on the one shared cache and
returns compact per-combo arrays. Python then does the cheap bookkeeping (Redis, progress,
files) between chunks.

```
runner.run_optimization()                                    (unchanged shell)
 ├─ _prepare_market_data(lean)   (unchanged)                 runner.py:166-378
 ├─ combos = build_sampler(...)  (unchanged)
 └─ FOR chunk in _chunk(combos, N≈64):        # Python loop, NO fork
       results = algotest_native.run_combo_batch(base_payload, chunk, shared_ctx, opts)
       FOR r in results:                       # Python, cheap
          if r.needs_python:                    # unsupported shape → proven engine
              trades_df, summary = _run_single_backtest(merged)   # existing path, unchanged
          else:
              trades_df = df_from_arrays(r.trades)   # no recompute
              summary   = r.summary
          append_result / increment_done         # unchanged Redis + progress
          write_combo_tradesheet / write_combo_xlsx  # Track 2 (see §4), byte-identical
       heartbeat()                               # between chunks → watchdog happy
 └─ finalize (ZIP / WOW-MOM / summary)  (unchanged)
```

**Why chunks, not one giant call:** it preserves everything that depends on Python running
*between* combos — live progress (`increment_done`), streaming results to Redis for live
ranking (`append_result`), watchdog heartbeats, and clean cancel (SIGTERM). Rayon still
parallelizes *within* each chunk across all cores. 1000 combos ≈ 16 chunk calls.

**The Rust entry point already has a home:** `run_optimization_batch` is a stub that raises
`NotImplementedError` today (`optimizer.rs:377-384`). The pricing kernel it needs
(`resolve_trade_specs` `simulate.rs:813`, `simulate_trades_batch` `simulate.rs:1188`) already
exists and is already f64. A Rust metrics function `batch_compute_metrics` (`optimizer.rs:355`)
also already exists (currently unused). So this is **filling in a scaffold**, not greenfield.

### Why this removes the RAM cap and can't OOM
- **One shared 2.6 GB cache**, read-only, touched by all Rayon threads — one copy, no
  per-worker Python heap.
- **Per-combo state is a few KB** (one strategy's trade rows). Nothing multiplies by core count.
- So the RAM clamp that forced P=6 disappears; the limit becomes CPU cores. Parallelism can
  rise **6 → 12** with **no OOM** (satisfies the hard "never OOM" rule).
- **No fork, no pickle, one GIL crossing per chunk** instead of ~6–10 per combo.

**Untouched by design:** the memory gate (per-job, peak RAM only drops), remote-worker
routing, per-node scoping, and download-from-remote all live *above* `run_optimization` at
the Celery-task / on-disk-file level. Rust writes the **same files to the same paths**, so the
remote download path sees identical artifacts.

---

## 3. What moves to Rust vs stays Python (ranked by parity risk)

| Calculation | Today | Plan | Why |
|---|---|---|---|
| Strike selection + leg pricing | **already Rust** | stays Rust | not new risk — f64, proven |
| MAE/MFE window scan (`runner.py:1189`) | Python/pandas | **port early** | heaviest numeric step; OHLC high/low/**settled** already in the Rust cache. **4 edge cases** (see R4). |
| `compute_analytics` (cumulative/DD/CAGR, `base.py:807`) | pandas | **port early** | deterministic sequential math; partly modeled in `optimizer.rs` already |
| `compute_optim_metrics` (`metrics.py:383`) | Python | **port (phase 3)** | Rust `batch_compute_metrics` already exists; must honor overwrite-precedence (R7) |
| `_cmetrics` overall+patchwise (`excel_builder.py:1656`) | pandas | **KEEP Python (port last, only if needed)** | authoritative headline metrics; too many edge cases (outlier Live-DD, patch resets, midcap chain) to port early |
| `build_cleaned_for_combo` (`excel_builder.py:2204`) | pandas | **KEEP Python** | display projection, high parity surface, low cost |
| WOW/MOM math (`wow_mom.py`) | Python | **do NOT port** | high parity risk (regression/Sharpe/caps), and the cost there is Excel *writing*, not the math — porting buys nothing |

**Principle:** port the cheap-to-verify, cache-reusing, high-payoff math first (sim fan-out,
MAE/MFE, analytics). Keep the edge-case-heavy authoritative metrics in Python, *fed by Rust
trade arrays* (so their pandas cost still drops without re-running the engine). Everything
stays behind the shadow-diff until proven.

---

## 4. Keeping per-combo Excel + CSV (you said you want them all) — the two-track approach

Once sim + metrics are Rust, the **only** remaining cost is file output: 1 CSV + 2 XLSX per
combo via openpyxl. If left on the latency path it becomes 100% of the wall-clock. Solution —
split into two tracks:

- **Track 1 — numbers / ranking: fully Rust, instant.** `run_combo_batch` returns every
  `summary`; the master table, objective ranking, and `/summary` endpoint are served from
  Rust output in ~1 s. This is what you *interact with*.
- **Track 2 — downloads: the ONE canonical builder (§6b), on openpyxl, byte-identical,
  decoupled.** After the §6b unification this is a *single* builder shared by backtest + optim,
  now fed by Rust's pre-computed arrays (so it stops re-deriving pandas — it just writes cells),
  built in the existing decoupled `ProcessPoolExecutor` (`_build_combo_xlsx_worker`,
  `runner.py:753`, already the live default). Same code → **same bytes**, and backtest↔optim
  identical automatically. Runs at P=12 instead of 6, and drops the recompute, so ~3–4× faster
  on the bundle → ~30–60 s for 1000 combos — and you already see ranking instantly from Track 1.

**All per-combo files are kept and remain byte-identical.** You lose nothing.

**Optional Phase 5 (off by default, needs your sign-off):** a Rust CSV writer + a
`rust_xlsxwriter` Excel writer collapse Track 2 to **~2–5 s** → full 1000-combo sweep incl.
downloads under a minute. But Rust-written `.xlsx` can only be **cell-identical**, not
**byte-identical** (fundamental — two writers differ in zip/XML/style records). This ships
only after a cell-diff harness proves every sheet/cell/format matches **and** you accept
cell-identical Excel. Default-off keeps today's exact bytes.

---

## 5. How we guarantee identical output — dual-run behind a flag

**Flag `OPTIMIZE_RUST_LOOP ∈ {0, shadow, 1}`, default `0`.**

- **`0`** — today's fork-pool path only. New code unreached. The permanent fallback.
- **`shadow`** — **Python is authoritative and produces the real output.** In parallel,
  `run_combo_batch` runs the same chunk and writes shadow artifacts to a side directory; a
  differ compares and logs. **The user only ever sees Python's output.** Risk-free evidence
  on real jobs.
- **`1`** — Rust authoritative for supported combos; Python engine still runs the
  `needs_python` combos. Promoted per strategy-family only after `shadow` is clean.

**The differ** (offline, no user impact) checks:
1. **Summary dict** — key-by-key exact (each field is rounded before storage, so exact
   equality is the target; any mismatch is a real bug, not float noise).
2. **CSV** — byte-diff, with a float-tolerant fallback to localize an offending cell.
3. **XLSX** — **cell-diff** (two openpyxl runs already differ in zip metadata, so byte-diff is
   meaningless): equal sheet names+order, equal dims, per-cell `(value, number_format)` across
   Trade Sheet / Summary / Patch-wise / WOW-MOM.
4. **Redis `row`** — full JSON schema deep-diff (`combo_columns`, `objective_value`,
   `trade_count`, `has_midcap`, `inline_finalized`, …).

**Promotion gate:** a strategy family graduates `shadow → 1` only at **zero diffs** across a
frozen **parity corpus**: fixed-DTE single-/multi-leg, rollover, no-rollover, buffer-strike,
per-leg SL, overall SL, straddle/premium modes, **empty-result, single-trade**, and (added in
later phases) spot-adj, midcap, filter_segments, re-entry, next-weekly. Same corpus is the CI
acceptance test for each phase.

---

## 6. The 5 must-do hardening items (from the adversarial review)

These are the real ways it *could* silently change a number. Each must be an explicit design
requirement, not an afterthought:

1. **The Rust `trades` column contract is the #1 parity artifact (R1).** Downstream Python
   `_cmetrics`/`_bcc`/WOW-MOM are unchanged — they produce *different numbers* the instant
   their input DataFrame differs by one column convention or one LSB. Non-obvious conventions
   the Rust arrays MUST reproduce (`engine_rust.py:2050-2075`): `Spot P&L` is set **only on
   leg 1**, else the literal empty string `""`; per-leg P&L is **recomputed from entry/exit
   prices**, not read from `net_pnl` (which holds the *trade total* on the lowest-leg row);
   `Cumulative/Peak/DD/%DD` are added by `compute_analytics` **only if the first Cumulative
   isn't already in `[90,110]`** (`base.py:100-108`). Shadow must diff the **DataFrame itself**,
   before any metric trusts it.

2. **`round_half_even` at every ported site (R2).** Rust's existing `round2`/`round4`
   (`optimizer.rs:110-112`) round **half-away-from-zero**; Python's `round()` and `f"{:.2f}"`
   are **half-to-even** (banker's). They disagree on any `…5` half-ulp (e.g. `round(0.5)` = 0
   in Python, 1 in Rust). Provide one `round_half_even` in Rust, audit every Python rounding
   site's actual rule, and **fix the existing wrong-direction `round2`/`round4`** before they're
   inherited. This also closes R8 (rounded `total_pnl` feeds the dedup fingerprint, so a 1-LSB
   difference could change *which combos survive*, not just a cell).

3. **`needs_python` is a fail-closed positive whitelist (R5).** The entire safety argument
   rests on "unsupported shape → Python." That's only safe if the gate rejects by default:
   *only* these exact leg/exit/strike shapes with these exact flags-all-false run in Rust;
   anything with an unrecognized flag falls back. Run it in shadow on a large real corpus and
   hand-audit the set Rust claims to support. Never let "unknown flag present" reach the Rust
   path. (And mirror the `apply_combo_for_optim` force-enable rules, `param_expander.py:250-268`,
   so the gate sees the *effective* post-merge config.)

4. **Size the Rayon pool from the live-optim registry, not "all cores" (R6).** `get_parallelism`
   exists because P=16 forked workers once inflated per-combo time 47×. One Rayon pool grabbing
   all 12 cores is fine for **one** optim, but **two** concurrent optims → 24 threads contending
   on the shared cache's memory bandwidth = the exact thrash we prevent today. The pool size
   must come from the same live-optim registry (`result_store.py:715-772`): `threads = cores //
   live_optim_count`, recomputed after data-load. This preserves the "2 optims share the box"
   contract and the never-OOM rule.

5. **Deterministic ordering + MAE/MFE edge cases, proven with fixtures (R3, R4).**
   - Order-dependent metrics (max-DD, outlier Live-DD, cumulative) require the **canonical sort**
     `(entry_date, int(Trade), int(Leg))` with the `datetime.max` sentinel for unparseable dates
     (`excel_builder.py:435-447`), summed **sequentially in that order** (never Rayon-reduce a
     single combo's rows). Freeze a **cascade re-entry** fixture (high engine-ID / early date) —
     a sort keyed on ID instead of date silently changes DD.
   - MAE/MFE has 4 landmines to reproduce exactly: entry-day bar **excluded** (window is
     `next_trading_day..exit`, `runner.py:1278`); **SL adverse cap** only for SL-family exit
     reasons with `exit_price>0` (`runner.py:1509-1515`); **settled-price substitution** applied
     **independently per High/Low**, and a zero with no settled price contributes nothing
     (`runner.py:1442-1496`); **expiry ±1 candidate matching** in the same order. Named fixtures:
     zero-volume expiry day, SL-capped exit, illiquid strike w/ settled fallback, expiry-shift
     day. Plus: unify the **six** MAE/MFE recompute sites (ranking df, both XLSX writers, ZIP
     prebuild, CSV endpoint) to one source, or shadow-diff each — else the downloaded CSV and the
     on-screen number can disagree.

---

## 6b. Prerequisite — unify the tradesheet + summary builder (single source of truth)

**Requirement (added 2026-07-09):** the tradesheet and all its sheets (Trade Sheet, Summary,
Patch-wise, WOW/MOM, MAE/MFE) must be generated by **ONE canonical builder**, called by BOTH
the backtest and the optimizer per-combo path — so `backtest == optim per-combo == optim
master-summary row` for the same combination is guaranteed *by construction*, not by keeping
multiple builders manually in sync. **No calculation change** — same formulas/numbers/order;
purely structural unification.

**Today there are four divergent builders** (the chaos source):
- Backtest: `engine_rust.py` → tradesheet records (backend)
- Frontend: `frontend/src/utils/buildTradeExcel.js` — a **1,656-line JS re-implementation** of
  the whole workbook for client-side downloads
- Optim per-combo XLSX: `excel_builder.py` (`write_combo_xlsx`, `_cmetrics`,
  `build_cleaned_for_combo`) + `metrics.py`
- Optim master summary: `_cmetrics`/`compute_optim_metrics` aggregated from Redis rows

They drift → the recurring "keep X in sync across N sites" bugs.

**Target shape:**
- **One canonical builder** (Python; consolidate around `excel_builder.py`) that takes a single
  strategy's trades DataFrame + config and emits the full workbook + the Summary dict.
- **Backtest and optim per-combo both call it** — identical output, feature-for-feature.
  Add to the optim per-combo whatever sheets/columns it's missing vs the backtest workbook.
- **Master summary = the stacked per-combo Summary results.** Each combo's row is exactly the
  Summary its own tradesheet produced — no separate computation. **Constraint:** aggregate from
  the per-combo builder's *computed* Summary (cached in Redis, as it already is via
  `get_all_results`/`row["summary"]`), **NOT** by re-parsing the on-disk `.xlsx` (slow, pointless).
- **Compute the summary once.** Today `write_combo_xlsx` re-derives `_cmetrics`/`build_cleaned`
  while writing (`result_store.py:519,586`) — duplicate work vs the master-summary computation.
  The single builder computes once; the XLSX Summary sheet and the master row both reuse it.
- Optional (bigger scope, flag separately): retire the frontend `buildTradeExcel.js` JS builder
  in favor of downloading the backend-generated workbook, eliminating the JS↔Python parity chore.

**Why this is a PREREQUISITE to the Rust work:** with one builder, the Rust migration feeds
Rust arrays into a single Track-2 place and backtest↔optim parity is automatic — it shrinks the
parity surface every later phase must prove. It's a pure refactor, zero calc change, low risk.

**Speed:** neutral to **slightly faster** standalone (removes the duplicate summary computation);
does not itself remove the openpyxl bottleneck (that's the Rust work); does not slow anything down
given the Redis-aggregation constraint above. Do this **before Phase 0**.

**Parity gate:** same dual-run/corpus discipline (§5) applied to the refactor — run the same
backtest and the same single-combo optim before/after unification and cell-diff both workbooks
+ the master-summary row to zero diffs. Because it's a consolidation of existing code, "before"
and "after" must be identical.

---

## 7. Phased rollout — small, shippable, reversible, riskiest last

Every phase leaves the system fully working with `OPTIMIZE_RUST_LOOP=0` and is independently
revertible.

- **Phase −1 — Unify the tradesheet/summary builder (see §6b; do this FIRST).** One canonical
  Python builder shared by backtest + optim per-combo; master summary aggregated from the
  per-combo Summary results. Pure refactor, no calculation change, cell-diffed before/after.
  This is the single-source-of-truth foundation the Rust phases build on.

- **Phase 0 — De-GIL the existing Rust primitives (no behavior change).** Extract
  `resolve_trade_specs`/`simulate_trades_batch`/SL scans into pure-Rust `*_core` fns with thin
  wrappers keeping current signatures. Pure extract-method, verified by existing golden
  snapshots. **⚠ do not "helpfully" re-introduce the reverted `pct_of_atm` directional rounding
  (R14) — it must stay mround.** Nothing observable changes.

- **Phase 1 — `run_combo_batch` (trades + analytics + MAE/MFE) for the supported subset, shadow
  only.** Fill the `NotImplementedError` stub. Gate every combo via the whitelist; Python still
  computes `_cmetrics`/WOW-MOM/CSV/XLSX from the Rust `trades`. Wire behind
  `OPTIMIZE_RUST_LOOP=shadow`; run the parity corpus. **This is where the trades-df contract,
  MAE/MFE, and analytics parity are *earned* before anyone depends on them.**

- **Phase 2 — Flip to `=1` for the supported subset; Rayon replaces the fork pool.** Python
  loops over chunks; `needs_python` combos fall back to the unchanged engine. Downloads stay
  openpyxl (Track 2, decoupled, byte-identical). Rayon pool sized from the live-optim registry
  (R6). **Delivers the compute win with byte-identical output.**

- **Phase 3 — Serve ranking/summary from Rust.** Port `compute_optim_metrics` (validate against
  the existing `batch_compute_metrics`, honor the overwrite precedence R7). Interactive latency
  → ~1 s. Still shadow-diffed.

- **Phase 4 (higher risk) — Port `_cmetrics` overall+patchwise,** only if it still bottlenecks
  after Phase 2. Reproduce outlier Live-DD, patch-reset boundaries, midcap Combined chain, the
  4-dp NIFTY recompute. Keep Python `_cmetrics` as certified fallback.

- **Phase 5 (opt-in, your sign-off) — Rust file writers.** `OPTIMIZE_RUST_XLSX=1` (default 0):
  byte-matched Rust CSV + **cell-identical** `rust_xlsxwriter`, proven by the cell-diff harness.
  Collapses downloads to seconds → sub-minute full sweep. Default-off preserves today's bytes.
  (Broaden the cell-diff to fonts/fills/merges/column-widths first if you inspect the workbooks
  visually — R9.)

- **Phase 6 (last, riskiest, ~weeks) — Port the Python-only orchestration** so those shapes
  leave `needs_python`, one sub-feature at a time, each behind shadow-diff, each reversible:
  spot-adj cascade, midcap cross-index, re-entry modes, filter_segments, next-weekly, futures.
  Until each lands, those combos keep running on the proven Python engine — **correctness is
  never at stake, only the speed of that subset.**

---

## 8. Expected speedup

Baseline: 1000 combos, common case (no midcap/spot-adj). Today ≈ minutes, dominated by
openpyxl + MAE/MFE + twice-per-combo pandas, fanned across only P=6.

| After | Compute (sim+metrics) | Download bundle | Ranking visible |
|---|---|---|---|
| **Phase 2** | ~seconds (**100–1000×** on compute; per-combo ~1–10 ms in Rust) | ~30–60 s (openpyxl, but P12 + no recompute → ~3–4×) | ~few s |
| **Phase 3** | ~1 s | ~30–60 s | **~1 s** |
| **Phase 5 (opt-in)** | ~1 s | **~2–5 s** | ~1 s |

- **Full sweep incl. downloads:** ~**5–20×** faster at Phase 2/3 (Excel still openpyxl,
  byte-identical); ~**30–60×** at Phase 5 — the honest path to "1000 combos incl. downloads
  under a minute."
- **RAM:** peak drops from `cache + P×700 MB` to `cache + O(cores × few KB)` — the clamp
  vanishes, parallelism 6 → 12, **no OOM**.

---

## 9. Bottom line

The literal ask — "one Rust call iterates all combos, Rayon-parallel, returns a compact array"
— is very achievable for the **numeric** work and makes the common-case sweep near-instant to
compute. The safety architecture is sound: `=0` leaves the proven path literally unreached;
`shadow` makes Python authoritative while gathering proof; `needs_python` keeps the stateful
engine on Python until each feature is separately ported and shadow-cleared; Track 2 keeps
Excel byte-identical.

The residual danger is **not** the architecture — it's the handful of genuinely hard parity
details (the trades-df contract, banker's rounding at *every* site, whitelist completeness, the
live-optim CPU division, MAE/MFE edge cases). With the 5 hardening items above made explicit
and a promotion gate that requires **zero cell-level diffs** across a corpus that includes
cascade re-entry, zero-volume expiry days, SL-capped exits, empty/single-trade sheets, and a
dedup-count check, the "not a single changed number" bar is achievable — phase by phase,
reversibly, with the old path always live.

**Recommended first concrete step (when you're ready to build):** Phase 0 + Phase 1 in shadow
— it changes nothing the user sees, and by the end you have hard, real-job evidence of exactly
how close (or not) the Rust path is to the Python numbers, before committing to anything.
