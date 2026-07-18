# Algo_Test Platform — Full Structural Audit, Bottleneck Analysis & SaaS Roadmap

> **Scope:** EOD (end-of-day) Indian-options backtester + optimizer. FastAPI + Celery + PostgreSQL + Redis + a PyO3/Rust native extension over memory-mapped Arrow.
> **Method:** read-only forensic audit of the entire codebase (17 parallel analysts, every subsystem, file:line evidence). **No code was changed. No trade-calc logic is touched by anything in this document.**
> **Hard constraint honoured throughout:** *the existing system must not break.* Every recommendation is **additive / behind a flag / a new wrapping layer** — the calc frontier (the Rust `simulate` + the Python engine) stays byte-for-byte identical.
> **Date:** 2026-07-13.

---

## 0. How to read this document

This answers, in order, every question you asked:

| Your question | Section |
|---|---|
| Read the whole software, every feature (facts) | §1–§8 |
| The **feather cache getting wiped every time** — how to fix it permanently | **§9 (headline)** |
| What are the bottlenecks / how to make it faster | §10 |
| Serve **more concurrent requests without queueing or OOM** / fastest backtest+optims | §11 |
| Architectural changes to be production-ready | §12 |
| What unwanted things can be removed / how to simplify (no logic change) | §13 |
| Deep research on backtesting platforms (industry best practice) | §14 |
| Make it a **SaaS** without breaking existing | §15 |
| What can be done / what needs doing / what can be added (consolidated) | §16 |
| Best features | §1 + §8 |

---

## 1. Executive Summary — the honest state of the platform

**What this is.** A **single-box, single-tenant, trusted-LAN** EOD options backtester that has been aggressively optimized for one thing: run correct, complex Indian-index options strategies fast on a fixed **16 GB / 20-thread NVMe box**, with a **Rust + memory-mapped Arrow** hot path and a **memory admission gate** that guarantees it never OOMs. Within that scope it is competent and, in places, genuinely best-in-class.

**The three things it does exceptionally well (do not "improve" these):**
1. **Rust + Arrow-IPC `mmap` lookup shared across forked workers.** The parent builds one feather; every forked optimizer child `mmap`-shares it via OS page cache (zero per-worker deserialization). This is the correct architecture for point-in-time option chains and beats the textbook Parquet→Arrow pattern.
2. **The memory admission gate** (`services/memory_gate.py`) — a global Redis reservation system that bounds concurrent heavy-job RAM and *queues* rather than overcommits. More sophisticated than the generic "per-worker memory limit."
3. **The cross-index spot-adjustment cascade + Midcap overlay** — the most differentiated *product* feature; logic that has no equivalent in stock backtesters (see §6).

**The five things that hurt today:**
1. **The feather cache silently truncates/wipes on deploy & warm** — your headline pain, 14 distinct failure vectors, all fixable additively (§9).
2. **Throughput is capped at ~1 long + 1 short backtest + 2 optims per box** — mostly a memory-footprint problem, not a CPU one (§11).
3. **Zero auth, secrets committed to git, CORS wide open** — hard blockers for any multi-user/SaaS exposure (§12).
4. **~1.8 GB of committed Rust build artifacts + ~20 tracked junk files** churn every `git status` (§13).
5. **No observability** — `prometheus.yml` exists but there is **no `/metrics` endpoint and no Prometheus service**; the scrape config is dead (§12).

**Bottom line:** the *calc engine* is production-grade and should be frozen. The *platform around it* (auth, tenancy, observability, artifact storage, cache lifecycle, horizontal scale) is where all the work — and all the SaaS opportunity — lives. Every one of those can be added as a layer without touching a single trade number.

---

## 2. System Overview & Process Model (facts)

**Four cooperating process types**, all brokered through **one Redis** (`worker/celery.py:8`):

| Process | Queue(s) | Concurrency | RAM ceiling | Notes |
|---|---|---|---|---|
| FastAPI / granian | — | `--workers 1` (`docker-compose.yml:279`) | 6000M* | Request normalization + enqueue |
| worker-backtests | `backtests` | `--concurrency=1` (`:324`) | 5000M | Long runs (> 550 days). Owns ~3.9 GB Rust cache |
| worker-backtests-fast | `backtests_fast` | `--concurrency=1` (`:364`) | 5000M | Short runs. Separate so a 7-yr run can't block a 1-month run |
| worker-optimize | `optimize` (profile-gated) | `--concurrency=2` (`:482`) | 4500M/job | **Doc drift:** budget header says "concurrency=3" (`:54,62,527`); real `RELOAD_CHILD_CMD` sets **2** |
| worker-uploads | `uploads` | `-c 1` (`:545`) | 500M | CSV ingestion |

\* backend raised 2500→6000M on 2026-07-13 for full-history feather rebuilds; header says revert afterward.

**Memory reality:** hard limits sum to ~20,900M (default) / +13,000M with optimize — **deliberately overcommitting 14.8 GiB RAM onto 24 GB NVMe swap**. The **admission gate** (`HEAVY_MEMORY_BUDGET_MB`, local 19000) is what actually keeps the *active* working set in RAM; idle warm caches page to SSD swap and don't count.

### Request lifecycle (verified)

```
Frontend  POST /api/algotest/jobs
   ▼
FastAPI  routers/backtest.py:queue_algotest_job (767)
   ├─ maintenance gate → 503                                   (772)
   ├─ _normalize_payload_dates (71)  — SYNC SELECT MAX(date) clamp on the event loop (41)
   ├─ _normalize_request / _resolve_effective_request          (777)
   ├─ validate legs + index                                    (778-782)
   ├─ Redis result-cache SHORT-CIRCUIT                          (784-801)
   │     hit → celery_app.backend.store_result(fake SUCCESS) → return {cached}
   ├─ queue = backtests_fast if span_days ≤ 550 else backtests  (223-226)
   └─ run_algotest_job.apply_async(queue=…)                     (829)
        ▼
Celery worker  worker/tasks.py:run_algotest_job (56)
   ├─ memory_gate.acquire(cost by date span)  ← ADMISSION GATE, waits/"queued" (69)
   ├─ execute_algotest_job()  services/algotest_job.py:562
   │     ├─ cache check (599) → bulk_load_options (629)
   │     ├─ _build_fast_lookup_from_bulk (632)   ← Rust/Arrow cache, once per run
   │     ├─ _try_rust_engine (635)               ← STRATEGY RUNS HERE (run_rust_engine_pipeline)
   │     ├─ compute_analytics + build_pivot (654)
   │     └─ redis_cache.set(cache_key, result)   (985)
   └─ memory_gate.release  (finally, 94)
        ▼
Frontend polls GET /api/algotest/jobs/{id} (955) → queued/running/completed
```

**Architectural smells (facts, not opinions):**
1. **The engine can run inside the API process.** `POST /api/algotest` (`backtest.py:744-764`) executes the *entire* backtest via a module-level `ProcessPoolExecutor` (`:96`), **bypassing Celery, the two-queue split, and the memory gate** — contradicting the "backtests run in workers" invariant. This is a latent OOM/consistency hole.
2. **Fat controller.** `routers/backtest.py` (~1000 lines) holds real business logic: `_recalculate_trade_prices` (`:245-353`), midcap overlay pricing (`:908`), full XLSX workbook building (`:658-728`).
3. **Tight Celery coupling.** The cache short-circuit *fabricates* a completed task via `celery_app.backend.store_result(...state="SUCCESS")` (`:798`).
4. **Implicit queue routing.** The fast queue is **not** in `task_routes` (`celery.py:50-58`); it's chosen only by the router computing span-days — any other enqueue path silently lands on `backtests`.

---

## 3. Data Layer & Caching (facts)

**Postgres → Polars bulk-load.** One symbol/range loaded once via `base.py:bulk_load_options()` (`base.py:3578`) → `MarketDataRepository.get_options_bulk()` (`market_data_repository.py:519`): a **single** parameterised `SELECT` (the old 60-day chunk loop was removed), streamed `pd.read_sql(chunksize=150_000)` + `pd.concat`. The narrow select list is deliberate so the partial index `idx_option_data_lookup_opt` (`003_...sql:91`) serves it near index-only; **adding columns forces heap reads.**

**fast_lookup O(1) dict** (`services/fast_lookup.py`):
- `_opt_lookup[(date_str, SYMBOL, strike_key, opt_type, expiry_str)] → close`; **`strike_key = round(strike*100)`** int to dodge float-hash collisions (`:282`).
- **When the Rust extension is present, the Python dict build is skipped entirely** (`:62-71`) — lookups route to `rust_fast_path.get_option_price` over the mmap feather; the dicts stay empty. `FAST_LOOKUP_MODE=rust` **hard-fails** rather than falling back (`:78-83`). The Python fallback (~30 s / 3.7 M rows) only runs when the native ext is absent.

**The four cache layers & their two invalidation tokens:**
1. **Process-local** — `data_memory_cache.py` LRU keyed `type:SYMBOL:from:to`, **5 GB cap**, sized by `df.estimated_size()` (`:99`).
2. **Parquet on disk** — `PARQUET_CACHE_DIR` (`/data/cache/parquet`).
3. **Arrow/feather on disk** — `ALGO_RUST_CACHE_DIR` (`/data/cache/arrow`), dir per key `arrow-v2:bulk:SYM:full/{options,spot}.feather`; futures + index-OHLC feathers are separate stores keyed by `(row_count, max_date)`.
4. **Redis result cache** — `backtest_cache.py`, key = `backtest:{CACHE_VERSION}:{sym}:{from}:{to}:{sha256(payload)}`, TTL 24 h.
   - `CACHE_VERSION` = md5 of **8 calc-path source files incl. `lib.rs`**, computed **at import** (so a worker running stale `.py` keeps serving old results until restarted).
   - `data_version` = Redis counter `INCR`-ed on every market-data import, folded into both the Redis key **and** the in-memory bulk invalidation.

> `services/redis_cache.py` is a **deprecated msgpack duplicate** — dead weight (§13).

**Ranked data-layer bottlenecks:**
1. **Feather/arrow staleness** — highest-severity, recurring *silent-wrong-output* class; guards exist but are **ad-hoc per-call-site, not centralized** → §9.
2. **Cold bulk load** — one big `read_sql` + `pd.concat` of a 7-yr range; +~30 s dict build when Rust absent.
3. **`_table_columns()` re-queries `information_schema` per call** (`:71`) — extra round-trips under optimizer fan-out.
4. **Import-time `CACHE_VERSION`** → stale-worker serving until restart.
5. **5 GB process-local LRU overlaps the Polars frame + Rust mmap** for the same data — triple-residency risk on load, mitigated only by the gate.

---

## 4. Rust Native Path (PyO3) (facts)

Crate `algotest_native` (cdylib, PyO3 0.21 abi3-py311, arrow 52, rayon, `rust_xlsxwriter` 0.79) registers **~35 `#[pyfunction]`s** (`lib.rs:1903-1942`) in five groups: **Lookup/cache**, **Simulate** (`simulate.rs`), **SL/Target scan**, **Summary/metrics** (`analytics.rs` + `summary_metrics.rs` + `optim_metrics.rs` + `mae.rs`), **XLSX** (`xlsx_writer.rs`), **Optimizer** (`optimizer.rs` — `run_optimization_batch` is a **stub**, `:377`).

**Rust-authoritative:** all price/spot/OHLC/futures/index lookups (mmap Arrow), strike resolution, per-leg SL/Target/Trail batch, SL-with-buffer batch, overall SL/Target, strikes-for-date, midcap legs, and the optimizer chronological summary under `OPTIMIZE_RUST_LOOP=1`.

**Still Python (the orchestration glue, `engine_rust.py`):** trade-spec construction, expiry scheduling, filters, **the spot-adjustment/re-entry cascade**, trade-id renumbering, tradesheet record building, **futures pricing** (`_resolve_futures_pnl_native` — priced in Python), and multi-index/midcap orchestration. So *simulate* is partially Rust; the *control flow* is Python.

**Gating:** `FAST_LOOKUP_MODE` (auto/rust/0), `OPTIMIZE_RUST_LOOP` (0/shadow/1). `1` is Rust-authoritative and **hard-fails** unsupported combos (spot-adj / midcap / re-entry / next-weekly / lazy / filters / futures currently rejected).

**Duplication = the real maintenance tax.** Every ported metric exists **twice** (Python `excel_builder._cxsm` ↔ `summary_metrics.rs`; Python `optimizer/metrics` ↔ `optim_metrics.rs`; MAE/MFE, analytics, strike resolution, Trade-Sheet writer). Kept in lock-step by `tools/*_parity.py` + `tools/xlsx_celldiff`. **This duplication is also your greatest safety asset** — it's the golden-master gate for "don't break existing" (§15).

**Build/deploy:** the `.so` is **not** built by Docker. `start.sh:88-118` runs the `ghcr.io/pyo3/maturin` image to produce a wheel in `backend/prebuilt/`, keyed by an md5 of `src/*.rs` + Cargo files stored in `.rust_wheel_hash`. Docker just `pip install`s the wheel.

**Left on the table:** futures still Python-priced; the whole Python orchestration not in Rust (so `OPTIMIZE_RUST_LOOP=1` rejects those families); Summary/Patch/WOW-MOM XLSX still openpyxl (only Trade Sheet is cell-identical in Rust); `run_optimization_batch` unimplemented (the "Rust combo loop" is design-only, `RUST_COMBO_LOOP_DESIGN.md`).

---

## 5. Backtest Engine & Feature Inventory (facts)

Two engines, one live path. `engines/generic_algotest_engine.py` (**6037 lines**) is the legacy/reference Python engine; the **live path is Rust-backed** `services/engine_rust.py:run_rust_engine_pipeline` (`:3073`) + the PyO3 crate. Per project rule the live path is **Rust-only, hard-fail, no Python fallback**.

**Full feature set:**
- **Legs** (max 12): Option (CE/PE), Future (FUTIDX, **Python-priced**), Spot; per-leg `index` override → multi-index.
- **Strike selection** (Rust `StrikeSel`, `simulate.rs:166`): `Fixed` (ATM/ITM/OTM steps), **`RelToLeg`** (rel_leg / Iron-Condor wing = parent ± offset·gap, Rust-only), `PctOfAtm`, `AtmStraddlePremPct`, `StraddleWidth` (+ joint-straddle), `ClosestPremium`, `PremiumGte/Lte/Range`, synthetic future; **buffer strike** snap-to-interval.
- **Expiry:** Weekly, Monthly, Weekly_T1/T2, Monthly_T1, **next_weekly (Ek+2)**, per-leg expiries, mixed weekly+monthly legs.
- **Exits:** per-leg SL, SL-with-buffer (SLB), Target, Trail SL, at-expiry, days-before-expiry, overall SL/Target; the `INTRADAY` tag = stop touched within a daily bar (core EOD logic).
- **Rollover:** fixed, no-rollover with `no_rollover_min_days`, `rollover_min_days_to_expiry`, `FuturesRolloverConfig`.
- **Re-entry:** per-leg with reverse-position modes + momentum trigger.
- **Filters:** `filter_segments`/`filter_config`, `filter_entry_mode`, `min_days_to_entry`, filter-end last-per-patch, folder-based filter date sets.
- **Spot-adjustment cascade** (deepest logic): `spot_adjustment_enabled` + pct/direction/units, combine-mode (earliest vs confirm), **cross-index Midcap spot-adjustment** — truncate + same-day re-enter, earliest-index-wins, cascade depth capped at **250**.
- **Multi-index / multi-expiry:** opt-in NIFTY + MIDCPNIFTY, reuse-engine-per-group.
- **Midcap overlay** (`services/midcap_overlay.py`): overlay legs *ride* base trades, priced from MIDCAP100 closes (not independently tradeable), combined base-100 equity. Rust twin exists.
- **MAE/MFE** on by default, SL-adverse cap, Net-MAE cross-pairing, futures MAE. **Slippage + F&O charges** applied.

> *Wait&Trade is intraday-only — it does not exist in this EOD engine.*

**Complexity hotspots** (candidates for later, careful decomposition — **not** now):
1. `run_rust_engine_pipeline` — `engine_rust.py:3073 → ~5530` (**~2450 lines, single function**); spot-adjustment cascade is the densest sub-region.
2. `run_algotest_backtest` — `generic_algotest_engine.py:3283 → 6037` (**~2750 lines**).
3. `check_leg_stop_loss_target` (~470 lines), `_execute_per_leg_reentry`, `_resolve_strike` in the reference engine.

**Most-differentiated feature (your competitive moat):** the **cross-index spot-adjustment cascade combined with the Midcap overlay** — truncates a live NIFTY trade the instant *either* NIFTY or MIDCPNIFTY breaches its own directional %-move, re-enters same-day, cascades up to 250×, while riding a non-tradeable Midcap overlay into a combined equity curve. **rel_leg (Rust-only Iron-Condor leg-relative strike)** is a close second.

---

## 6. Optimizer Subsystem (facts)

**Pipeline:** `POST /api/optimize/jobs` (`routers/optimize.py:143`) → Celery `run_optimize_job` on `optimize` / `optimize@{node_id}`. Worker `memory_gate.acquire(cost_for_job("optimize", payload))` **before** the sweep → `runner.run_optimization` (`runner.py:1946`).

- **Param expansion** (`param_expander.py`): range/values/enum specs → `itertools.product`; gated params collapse when their enabling toggle sweeps falsy; `count_combinations` is an O(1) upper bound vs `MAX_COMBOS=100000`.
- **Samplers** (`samplers.py`): `ExhaustiveSampler`, `RandomSampler` (dedup, can under-deliver near grid size), `SmartSampler` (nevergrad: cma-es→CMA, pso→PSO, ga→DE; **missing nevergrad silently → RandomSampler**). Objectives are 13 entries **all `direction="max"`** — `max_dd_pct` registered "max" with a "less negative is better" comment = a footgun.
- **Parallel execution** (`parallel.py`): `run_parallel` uses **fork** — parent pre-builds the Rust feather once (lean load), tears down Python dicts, warms page cache, and children `mmap`-share the ~2.6 GB Rust AHashMap via CoW. Children reset the Redis client and **must not dispose the SQLAlchemy pool** (futex deadlock) and single-thread pyarrow. `P=1` runs in-process without forking.
- **Dynamic split:** `parallelism = solo_ceiling // max(1, live_optims)` where `solo_ceiling = OPTIMIZE_PARALLELISM = 6`: 1 optim → P=6, 2 optims → P=3 each. `get_parallelism()` clamps to `min(configured, cpu_cap, mem_cap)` — added after P=16 inflated per-combo time ~47× on 2026-07-04.
- **Result store** (`result_store.py`): Redis `optim:{id}:{meta,results,parquet}`; live-optim registry a per-node Redis hash `algotest:optim:active:{node}`.
- **Download modes:** `_download_mode_flags` resolves to exactly one of **patchwise (default)** / **overall** — only one variant is built. Finalization spills → summary_csv → **zip → wow_mom → mark_complete**, triggered by an HTTP self-call that **busy-polls up to 20 min** (`tasks.py:149-156`).

**Where OOM / stalls happen:**
- **Mem-gate orphan reservation** — a hard-killed optimize leaves a ghost reservation blocking the next job ~40 min.
- **Stuck `status=running`** — watchdog auto-cancels jobs whose `last_progress_at` froze > `OPTIMIZE_STUCK_SECONDS=9000`; a finalizer killed mid-step leaves meta frozen "running" → downloads 400 despite the ZIP on disk.

**Ranked optimizer bottlenecks:** (1) **serial single-threaded finalization** (spill/xlsx/WOW-MOM run in the parent loop — the 6 h-hang class); (2) Python-authoritative per-combo summary+MAE (the `OPTIMIZE_RUST_LOOP=1` remedy, whitelist-limited); (3) coarse `ceiling // live` split, frozen at pool-build time; (4) fork+CoW fragility; (5) the 20-min HTTP self-poll ZIP trigger.

---

## 7. Frontend (facts)

`App.jsx` (96 lines) is a thin shell polling `/health` every 2 s with a "Server is restarting…" overlay. **All logic lives in `StrategyBuilder.jsx` — 4,883 lines / 258 KB, a genuine god component**: **95 `useState`, 16 `useEffect`, zero `useReducer`/`useContext`/Redux/store** (verified). State is raw `useState` + deep prop-drilling.

**Features:** multi-leg/multi-index strategy builder, SuperTrend filter, OptimizePanel (param-sweep launcher), ResultsPanel (73 KB, recharts equity/DD; **backtest Excel now fetched server-side** — no client rebuild), **AutoDownloadQueue** (always-mounted, auto-downloads each optim's ZIP+WOW/MOM+summary, per-PC scoped, cross-tab claim via localStorage — the best-engineered piece), CsvUpload.

**Debt (facts):**
- **`utils/buildTradeExcel.js` (83 KB) is dead code** — no file imports it; superseded by the server route (still maintained though — edited Jul 13). Delete it.
- **Live cross-language duplication remains** in `utils/wowMom.js` + `wowMomSheet.js` (Sharpe/Sortino/K-ratio/SQN/CAGR ported to JS) and `optimSummaryExport.js` (client ExcelJS) — WOW/MOM + optim-summary numbers are generated in **two languages** with no shared source of truth.
- **Dead deps:** `xlsx`, `apache-arrow`, `@tanstack/react-query` — **all three zero imports**. Two Excel libs declared, only `exceljs` used.
- **Desktop-only:** no `@media`/breakpoints anywhere. `index.css` opens with a render-blocking external Google-Fonts `@import` — exactly what the SonicWALL DPI-SSL layer re-signs (can stall). Self-host the font.
- **Build pain** (per CLAUDE.md): bare `npm install` dies `SELF_SIGNED_CERT_IN_CHAIN` then the misleading "Exit handler never called!"; `dist/` root-owned → build in a node container with `NODE_TLS_REJECT_UNAUTHORIZED=0`.
- Test coverage effectively nil (`main.test.jsx` only).

---

## 8. Best Features (what to keep, protect, and market)

These are the platform's genuine strengths — **freeze them, don't "refactor" them**, and lead with them in any product pitch.

| Feature | Where | Why it's strong |
|---|---|---|
| **Rust + Arrow-IPC `mmap` lookup, shared across forked workers** | `rust_fast_path.py`, `native/src/lib.rs` | Parent builds one feather; forked children `mmap`-share it via OS page cache — zero per-worker deserialization. The correct architecture for point-in-time option chains; beats the textbook Parquet→Arrow pattern |
| **Memory admission gate** | `services/memory_gate.py` | Global Redis reservation system that *queues* rather than overcommits — guarantees no OOM on a fixed 16 GB box. More sophisticated than a per-worker memory limit |
| **Cross-index spot-adjustment cascade + Midcap overlay** | `engine_rust.py:~3552-4200`, `midcap_overlay.py` | Truncate-and-re-enter on whichever of NIFTY/MIDCPNIFTY breaches first, cascade ≤250×, combined base-100 equity. **No equivalent in stock backtesters — your moat** |
| **rel_leg (Iron-Condor leg-relative strike)** | Rust `StrikeSel::RelToLeg` | Wing = parent resolved strike ± offset·gap; correct, hard-to-replicate options primitive |
| **Two-token cache invalidation** (`CACHE_VERSION` code-hash + `data_version` data-counter) + sha256 request-hash result memoization | `backtest_cache.py` | Correct separation of "code changed" vs "data changed"; identical requests never re-run |
| **Dynamic memory-aware optimizer parallelism** | `optimizer/parallel.py` | `min(cpu_cap, mem_cap)` clamp + `solo_ceiling // live_optims` split — avoids the P=16 memory-bandwidth thrash (47× slowdown) |
| **The parity harness** (36 golden snapshots, 40+ archetypes, atol 0.01) | `tests/parity/`, `tools/*_parity.py` | A ready-made "never break the numbers" gate — also the foundation of the whole SaaS safety story (§15-D) |
| **AutoDownloadQueue** (cross-tab claim protocol, per-PC scoped) | `frontend/.../AutoDownloadQueue.jsx` | The best-engineered piece of the frontend; auto-delivers each optim artifact the instant it finishes |
| **LAN remote-worker** (IP-namespaced queues, per-node RAM budget, version-staleness guard) | `remote-worker/`, `node_registry.py` | A working hand-rolled autoscaler — the seed of true horizontal scale (§15-C) |

---

## 9. 🪶 THE FEATHER CACHE WIPE — Root Cause & Permanent Fix

> *This is the issue you called out ("the father [feather] issue … getting wiped out every time"). It gets top billing because it is a recurring, silent, wrong-output-producing incident, and it is fully fixable without touching any trade math.*

### 9.1 Where the feather lives
- **Root** = `ALGO_RUST_CACHE_DIR` → `/data/cache/arrow` (`docker-compose.yml:122`), else `<tmpdir>/algotest_arrow_cache` (`rust_fast_path.py:78-87`). **On any mkdir failure it silently falls back to the tmp dir** — a permissions blip *orphans* the whole warm cache off-volume.
- **Per-symbol dir** `arrow-v2:bulk:<SYM>:full/` with `options.feather` + `spot.feather`. Version prefix `arrow-v2` is hardcoded — a bump orphans all old dirs.
- **Range dirs** `arrow-v2:...:<from>:<to>/` (optimizer). **Sibling stores:** `futures/<SYM>.feather`, `index_ohlc/<SYM>.feather`. **Parquet layer** `/data/cache/parquet`.
- **Volume** `algo_cache:/data/cache` — the *only* thing making warm caches survive a container recreate.

### 9.2 The single writer, and why it's fragile
- **Only real writer:** `build_cache(options_df, spot_df, cache_key='bulk:SYM:full')` (via `fast_lookup.build_fast_lookup:56-62`), calling `_write_feather` → `feather.write_feather()` **directly onto the live file — no temp+rename** (`rust_fast_path.py:148`). An interrupted write leaves a half file.
- `bulk_load_options` does **not** write the feather — it calls `build_cache(None, None, …)` only to *load* one (`base.py:3666`). (This distinction is the crux: warming/backtesting a **narrow** range is what can overwrite the **full** feather.)
- The atomic `os.replace(tmp, path)` pattern **already exists** in the date-fixer `_fix_feather_date_format` (`:170-183`) but is never used on the hot path.

### 9.3 The existing guard — and its four gaps
`build_cache` narrow-load guard (`rust_fast_path.py:239-271`): if a feather pair exists and incoming is empty or **strictly inside** the existing `[min,max]`, it keeps the wider file. Gaps:
- **(a)** compares only **min/max endpoints**, not row density → an equally-wide but *sparse/holey* reload still overwrites.
- **(b)** hinges on `options_df.is_empty()` → a load returning a *few contaminating rows* passes.
- **(c)** fires only when **both** feathers already exist → a missing `spot.feather` disables it.
- **(d)** the spot-vs-options trim shrinks *options* down to spot's max → **a lagging spot import silently truncates options coverage**.

### 9.4 Complete failure-mode catalog (14 vectors)
1. **Deploy cache-warm overwrites full with narrow** — a short-range warm/backtest calls `build_cache` and pre-guard truncates `:full`.
2. **Narrow pre-existence warm** — warming a pre-launch slice (e.g. MIDCPNIFTY before Jan-2022) loads empty/partial data.
3. **`docker compose down -v`** — destroys `algo_cache`/`parquet_cache`; all feathers gone.
4. **Container recreate off-volume** — mkdir fail → writes land in `<tmpdir>`, lost on recreate.
5. **spot.feather narrower than options** — staleness check deletes **both** when `spot_max < options_max` (`base.py:3788-3799`); churns on every lagging spot import.
6. **Staleness (cache date < DB date)** — feather rejected only when a *request* triggers `_db_option_max_date > _fmax`; if warm never re-ran, the shortcut serves stale data.
7. **`data_version` not bumped on feather hit** — `bump_data_version` fires only on a genuine DB reload; a feather-cache hit skips it → Redis serves stale tradesheets.
8. **Explicit invalidation delete** — `_invalidate_stale_bulk_lookup` `rmtree`s every `arrow-v2:bulk:SYM:*` and unlinks all `*.parquet` — wipes range feathers too.
9. **Schema-driven regen deletes** — missing Open/High/Low/Contracts/SettledPrice → `options_path.unlink()`.
10. **Wrong date-format regen** — non-Date32 → rewrite or unlink.
11. **Root-owned files block rewrite** — `_write_feather` swallows write errors; a root-owned stale file can't be replaced → old/truncated data persists **silently**.
12. **Futures/index feather stale** — rebuilt only on signature change unless `force`.
13. **finish_setup skips restore** — restore skipped if the volume is non-empty (`finish_setup.sh:81`) → a truncated cache survives a "restore."
14. **Memory guard skips load** — non-`rust` mode returns without loading if est. RSS exceeds cap → no Rust cache active.

### 9.5 Permanent fix (additive, no calc change) — the "never again" plan

| # | Fix | Where | Why non-breaking |
|---|---|---|---|
| **1** | **Atomic writes**: write `path.with_suffix(".feather.tmp")` then `os.replace()` (pattern already proven at `:183`) | `_write_feather` (`rust_fast_path.py:148`) | `os.replace` is atomic on one FS; readers see old or new, never half. Signature unchanged |
| **2** | **Coverage-safe for ALL kinds**: generalize the `:247-271` guard into one `_reject_if_narrower(existing, incoming, kind)` and call it for options **and spot, futures, midcap** — keyed on min/max Date **+ row count** (fixes gaps a–c) | all writers | Advisory guard; only *refuses to shrink*, never changes numbers |
| **3** | **`manifest.json` sidecar** per pair: `{symbol, min_date, max_date, rows, cols, data_version, code_version, written_utc}`; written after `os.replace`, read before overwrite. Warm proceeds only if incoming ⊇ existing or `data_version` increased | `_write_feather` + `build_cache` | Pure metadata; absence falls back to today's date probe |
| **4** | **Staleness detection**: on `bump_data_version`, stamp the token into each affected manifest + mark `stale`; a startup + `/health/db`-style probe compares `manifest.max_date` vs `SELECT max(date)` and **refuses the shortcut / enqueues a rebuild** instead of silently serving stale | `backtest_cache.py:161`, new probe | The refuse-shortcut path already has a working DB fallback |
| **5** | **Stop root-owning cache files**: run workers as a fixed non-root `user:` (or `chown` the mount in `finish_setup.sh`) → warm/rebuild can always overwrite, removing the "rebuild requires sudo" trap | `docker-compose.yml`, `finish_setup.sh` | Same paths/volume, only ownership changes |
| **6** | **`rebuild_feather.py` = single idempotent authority**: manifest-aware, **verify-not-destroy**; add `--verify` (exits non-zero on mismatch, writes nothing); parameterize the hardcoded `/data/cache/arrow` via env; wire `--verify` into `start.sh` as a post-boot gate | `rebuild_feather.py:26,28` | `--verify` writes nothing; default rebuild is widening-only |
| **7** | **Volume hygiene**: declare `algo_cache` `external: true` (or bind mount) so `down -v` can't nuke it; keep the tar backup as restore-of-record | `docker-compose.yml:611` | External volumes attach identically; only teardown semantics change |

**"Never again" checklist:** (1) all feather writes go through one atomic `tmp→os.replace` writer; (2) every write coverage-guarded for **all** kinds, never narrowing; (3) `manifest.json` records coverage + versions; (4) `bump_data_version` marks feathers stale, health probe refuses-or-rebuilds on `max_date` mismatch; (5) cache files worker-owned (non-root), `rebuild_feather.py --verify` is the deploy gate; (6) `algo_cache` external + tar-backed.

---

## 10. Performance Bottlenecks (Ranked) — how to make it faster

"L" = single-backtest latency, "T" = concurrent throughput. **All fixes below are pure plumbing — zero calc-logic change.**

| # | Stage | Symptom | Root cause | Fix (no calc change) |
|---|---|---|---|---|
| 1 | **Optimizer fork parallelism** (`parallel.py:60-95`) | per-combo time ~47× worse at P=16 (205 s vs 4.4 s) | forked rayon pools contend for **memory bandwidth** on the shared 2.6 GB Rust cache; box is RAM/core-bound | **Already mitigated** — keep the `min(cpu_cap,mem_cap)` clamp + `RUST_SIM_THREADS`. Do **not** raise P |
| 2 | **Per-record MAE/MFE (single backtest)** (`algotest_job.py:381-419`) | one feather query **per leg row** in a Python `for rec` loop | single-backtest path never uses the Rust batch | **Route through `algotest_native.compute_mae_mfe_batch`** (already used + parity-proven at `runner.py:1251`). **Highest-ROI latency win** |
| 3 | **Double analytics recompute** (`algotest_job.py:654` *and* `:684`) | `compute_analytics`+`build_pivot` run **twice** on the same trades | first pass computed then discarded and recomputed | **Drop the first pass** / reuse its output. Pure plumbing |
| 4 | **dict↔DataFrame churn** (`:639,648,658,685`) | tradesheet round-trips `to_dict→DataFrame` **~4×** | each stage re-materializes the frame | Keep a **single DataFrame**, convert to records once at the response boundary |
| 5 | **Post-sweep XLSX** (`runner.py:855-909`) | 50–54 s for 546 combos | each combo re-reads CSV + rebuilds cleaned frame | **Already parallelized** via `ProcessPoolExecutor` + `OPTIMIZE_INLINE_FINALIZE` — keep the inline path on |
| 6 | **Per-combo Python marshalling** (`runner.py:1710-1798`) | list-of-dicts serialized across PyO3 per combo | fixed Python tax × combo count | **Column-oriented handoff** (arrays not dicts) to `compute_mae_mfe_batch`; vectorize the bridge-split |
| 7 | **Per-day spot dict build** (`:351-355`) | Python loop per trading day (~1700 for 7 yr) | LRU-cached but pays Python call/day | Batch-pull spots from the feather once (Polars select) |
| 8 | **Cache-build feather write** (`rust_fast_path.py:388`, uncompressed) | ~275 MB uncompressed write | deliberate (mmap speed > disk on NVMe) | Leave as-is |

**Highest-ROI, zero calc risk:** **#2 (batch MAE/MFE) + #3 (delete duplicate analytics)** — both reuse code that already exists and sit squarely on the latency path.

---

## 11. Concurrency, Queueing & Zero-OOM — serve many more requests, fastest, no OOM

### 11.1 Why requests queue today (two independent throttles)
1. **Per-queue Celery concurrency = 1** for both backtest queues → **at most 1 long + 1 short backtest at a time**; a second long backtest waits *regardless of free RAM*. Optimize runs `--concurrency=2`.
2. **The memory admission gate** — even with a free worker slot, the task blocks in `memory_gate.acquire()` until a RAM slot frees. Redis-Lua over `algotest:mem_gate:{node}`; grants iff `used + cost ≤ budget` (19000 local) **or** `used == 0` (oversized-single-job escape). **Cost is dynamic by date span** (backtest `2000 + 430×years` clamped [2000,5000]; optimize `2500 + 500×years` clamped [3000,4500]) — short runs reserve less, so more fit. A second live-RAM guard reads host `/proc/meminfo` but only for `optimize`. On wait: poll 3 s up to 600 s, then proceed leaning on swap.

**Orphan-reservation failure mode:** a **SIGKILL** (hard cancel / OOM / recreate mid-job) skips the `finally` release; the ghost keeps counting against budget for up to the TTL (~40 min) and stalls the next job. (All Redis errors fail **open** — a gate bug can never block work.)

### 11.2 The "2 optims + backtest" dynamic split (already built)
`register_active_optim` records the job *before* the data load; after load, `parallelism = 6 // live_optims` (1→P6, 2→P3). Feather is pre-built once and mmap-shared, so `2×4500 (optims) + 5000 (backtest) = 14000 ≤ 19000` → **a backtest always coexists with two optims**.

### 11.3 LAN remote-worker horizontal scaling (already built)
`remote-worker/` runs a Celery worker on another in-house PC, joining the main Redis + Postgres, consuming **IP-namespaced queues** `backtests@${NODE_IP}` / `optimize@${NODE_IP}`. It heartbeats into `algotest:nodes:{id}` (TTL 45 s); the mem-gate keys **per-node** so a remote job budgets against **its own** RAM (`ram_mb × 0.7`). Version guard refuses routing to a mismatched image. **This is a hand-rolled autoscaler** — the seed of true horizontal scale (§15-C).

### 11.4 Ranked plan to serve more concurrent users on 16 GB (fastest, no OOM)

1. **Result caching first (highest ROI, zero RAM).** The Redis result cache keyed by request-hash already exists; ensure **every** read path short-circuits *before* the gate so identical/repeat requests never occupy a worker slot. Pre-warm the team's common date ranges. Cheapest way to raise effective concurrency.
2. **Shrink per-job footprint** so more fit under 19000 MB. The f32 AHashMap (~2.6 GB, duplicated per optim job) dominates — push the lookup fully to Rust `mmap` of a shared Arrow file (already `FAST_LOOKUP_MODE=rust`) so **all** jobs share one page-cached copy → directly lowers `cost_for_job`, admitting more.
3. **Raise backtest concurrency once footprint drops.** With smaller costs, bump `worker-backtests-fast` to `--concurrency=2` — short runs are the bulk of traffic and a 6-month job reserves only ~2500 MB.
4. **Chunked/streaming loads** (`BULK_LOAD_CHUNK_YEARS`, `BULK_LOAD_MAX_MEMORY_MB`) so a 7-yr job's peak working set never spikes — lowers the flat ceiling that forces long jobs to run alone.
5. **Offload to LAN remote nodes by default** when a node is registered — each adds an independent RAM pool the local gate ignores.
6. **Fix the orphan-reservation gap:** on cancel/watchdog-kill, explicitly `HDEL` the mem-gate id (the map already records `job→node`); shorten the TTL — kills the ~40 min silent throughput drop.
7. **Task-level idempotency keys** on `run_algotest_job`/`run_optimize_job` so Celery's at-least-once redelivery can't double-run a sweep (the root of "stuck running" + orphan reservations).
8. **Autoscale within the CPU budget, not RAM.** RAM is the binding constraint; prefer more *short* concurrent jobs (cache + smaller footprint) over more parallel workers per job — the `get_parallelism()` clamp already caps the P=16 thrash.

---

## 12. Production Readiness, Ops & Security

Well-tuned for **single-box, single-tenant, trusted-LAN**. Several items are **hard blockers** before any untrusted network or multiple customers.

**Health/readiness:** solid. `/health` returns **503 while warming** (real readiness gate); `/health/db`, `/health/stats`, `/cache/stats` present; every service has a healthcheck. Gap: worker healthcheck is only `celery inspect ping` — can't detect a wedged/looping job.

**Observability — largely absent.** `prometheus.yml` targets `backend:8000` but there is **no Prometheus service in compose and no `/metrics` endpoint / `prometheus_client` anywhere** — the scrape config is **dead**. Logging is raw stdout (no structured/JSON, no request IDs, no aggregation, no Sentry/OTel). No alerting on OOM/swap/queue-backlog/stuck-jobs. For a box that deliberately overcommits onto swap, this is a real risk.

**Auth / tenancy — none.** Zero authN/authZ (no bearer/OAuth/API-key/jwt anywhere). Any caller reads any job's results. Ports published on `0.0.0.0` (Postgres 5432, Redis 6379, backend 8000). **Redis has no password.**

**CORS — misconfigured.** `allow_origins=["*"]` **with** `allow_credentials=True` (invalid per spec, browsers reject the combo) — the "replace in production" comment was never actioned.

**Secrets — leaked into git.** **`.env` is committed** (not in `.gitignore`) with `POSTGRES_PASSWORD=algotest_password` (same weak default is the compose fallback everywhere). SonicWALL DPI cert also tracked. No secrets manager, no rotation.

**Backups — broken as written.** `backup.sh` covers Postgres + source + CSVs + `algo_cache` → SMB, **but `PROJECT="/home/user/Algo_Test_Software"` (`:6`) ≠ the real path** → its `docker compose -f "$PROJECT/…"` / `tar -C "$PROJECT"` calls fail. No schedule, no retention, no encryption, no tested restore drill.

**Migrations:** plain SQL `001`–`009` run manually; no version table, no rollback. `migrate_data.py` does automatic `ALTER TABLE` widening on first import — a schema-drift hazard with no audit trail.

**CI/CD — none.** No workflows anywhere. Tests exist but nothing runs them. "Deploy" is `sudo ./start.sh` building `:latest` on the host.

**SPOF & rollback:** entire stack on one box. **Redis is broker + result store with persistence off** (`--save "" --appendonly no`) → a Redis restart **loses all queued jobs and status/results**. Single Postgres, no replica. Images `:latest`-only with `/app` bind-mounted → **no versioned image, no rollback path** beyond `git checkout` + restart.

**Graceful shutdown hazard:** only the optimize worker restarts safely (idle-aware `dev_supervisor`); backtest/fast/upload workers have **no drain guard**, yet `start.sh:190` runs `docker compose down` which kills in-flight jobs (stranding the ~6 GB gate reservation ~40 min).

### Prioritized checklist

**P0 — block production / any untrusted exposure**
- Remove `.env` from git history; rotate the DB password; add `.env` to `.gitignore`; adopt a secrets store.
- Add authN + per-user job scoping (§15-B).
- Fix CORS: explicit origins, drop wildcard+credentials.
- Set a Redis password; stop publishing 5432/6379 to `0.0.0.0`.
- Add Redis persistence (AOF/RDB) or an external broker — job loss on restart is unacceptable.

**P1 — operate reliably**
- Fix `backup.sh` `PROJECT` path; schedule it; test a restore drill; encrypt off-box backups.
- Real observability: `/metrics` + `prometheus_client`, add the Prometheus service, structured JSON logs, alerting on OOM/swap/queue-depth/stuck-jobs, Flower for Celery.
- Graceful drain for backtest/upload workers; automate mem-gate reservation cleanup.
- CI: run `backend/tests` + the parity gate (§15-D) + build the image on push.

**P2 — maturity**
- Versioned image tags + documented rollback (replace `:latest`).
- Migration version table / framework; stop implicit `ALTER TABLE` on import.
- Address single-host SPOF (Postgres replica or documented RPO/RTO); API rate limiting.

---

## 13. Dead Code, Cruft & Safe Simplifications (what to remove)

> *`INTRADAY` strings in `generic_algotest_engine.py` (2360–2784) and `engine_rust.py` (5425,5464) are the SL-exit **kind** tag — core EOD logic, **not** removable.*

### A. DELETE — tracked junk (all confirmed `git ls-files`)
- **~20 zero-byte typo artifacts (all tracked):** `=`, `CACHED`, `ERROR`, `[backend`, `[backend]`, `[frontend`, `[frontend]`, `[internal]`, `[intraday-api`, `[intraday-api]`, `docker`, `reading`, `resolve`, `naming`, `exporting`, `unpacking`, `transferring`, plus `kiro-cli.deb`, `kiro-cli.deb.1`, `skill.zip`, root + `backend/bhavcopy_data.db`. **Risk: none.**
- **The single biggest cruft — committed Rust build tree:** `backend/native/target/**` = **1964 tracked files, 1.8 GB**, incl. 11 `*.so`. `.gitignore` already lists `native/target/` but it was committed *before* the rule, so it stays tracked and **435 files show as M/D in the current `git status`**. Fix: `git rm -r --cached backend/native/target`. **Risk: low** (start.sh rebuilds the wheel).
- **Backend one-off debug scripts (tracked):** `verify_both.py`, `verify_filter2.py`, `verify_finalmae.py`, `verify_finalmae2.py`, `verify_fix.py`, `verify_fix2.py`, `verify_master_patchwise.py`, `verify_no_dddays.py`, `patch_repro.py`, `repro_maxdd.py`, `repro_maxdd2.py`, `_diag_check.py`; untracked `probe_midcp.py`, `parity_matrix.py`. Keep `tools/*_parity.py` + `parity_harness.py`. **Risk: low.**
- **Stray xlsx/logs:** `backend/patch_wise_test.xlsx`, `research_lownav.xlsx`; committed logs `build.log`, `setup.log`, `db_restore.log`, `start_sh.log`; arch-doc generator `gen_arch_doc.js` (90 KB) + root `package.json`/`package-lock.json` → archive under `scripts/`.
- **Duplicate `requirements.txt`:** root is **stale** (pins `uvicorn`, omits `polars`/`pyarrow`/`granian`/`orjson`); Docker uses `backend/requirements.txt` (granian). Delete root or make it a pointer.

### B. ARCHIVE — intraday leftovers (separate repo)
- `services/intraday_expiry_dim.py` (imported nowhere), `migrations/007_intraday_imports.sql`, `tests/fixtures/intraday/…` — safe delete.
- `backend/native/src/intraday/` — **still compiled** (`lib.rs:2 mod intraday;`, registers `run_intraday_backtest`) but called from no Python; removal needs 2 `lib.rs` lines + Cargo + rebuild → **Risk: medium**, hand to the removal branch.

### C. .gitignore gaps
Add `*.log`, `*.deb`, `bhavcopy_data.db`, root scratch `*.xlsx`, and the `verify_*/repro_*` pattern.

### D. Frontend removals
Delete dead `utils/buildTradeExcel.js` (83 KB) + 3 unused deps (`xlsx`, `apache-arrow`, `@tanstack/react-query`).

### E. Safe simplifications (no logic change), file:line
1. `base.py:449-477` — delete the commented-out `load_base2()` block.
2. `migrate_data.py:1533 & 1584` — dead `Filter/base2.csv` special-casing.
3. `_num` duplicated verbatim in 4 `tools/*_parity.py` + `rust_combo_loop.py:235` → hoist one copy into `parity_harness.py`.
4. `runner.py` — 41 function-local imports (e.g. `compute_xlsx_summary_metrics` 3× local) → hoist.
5. `engine_rust.py` — 18 function-local imports → hoist the hot-path ones.
6. `excel_builder.py:1897` — its own comment says the column extraction "is no longer used" → remove if unused.
7. `data_loader.py` (75,251,1116,1120,1187,1260,1336) — narrow/log the bare `except Exception:` swallows.
8. `services/redis_cache.py` — deprecated msgpack duplicate → delete.

**Highest ROI:** untrack `native/target` (kills the 435-file `git status` churn) + delete the zero-byte junk.

---

## 14. Industry Best-Practices Research & Benchmarking

**Best practices from modern platforms** (VectorBT, backtrader, Nautilus, QuantConnect LEAN, Zipline; ClickHouse/DuckDB; Celery production patterns):
1. **Two-tier engine:** vectorized (Numba/NumPy) to *scan* 50k variants fast, event-driven to *validate fills* before trusting — use both.
2. **Modular, config-swappable engine components** (LEAN swaps data feed / execution / brokerage via config).
3. **Columnar-on-disk (Parquet) → columnar-in-memory (Arrow)** as the read path, column-pruned.
4. **Embedded OLAP (DuckDB/ClickHouse)** for ad-hoc point-in-time chain queries.
5. **Queue segmentation + independently-scaled worker containers.**
6. **Idempotent tasks keyed by a content hash** (Celery is at-least-once).
7. **Fair multi-tenant queueing + per-tenant concurrency caps.**
8. **Distributed horizontal fan-out** for optimization (LEAN runs >15k backtests/day via clusters).
9. **Per-worker memory limits + Flower + error-tracker.**

**Already best-in-class here (do NOT "improve"):** the Rust+Arrow-mmap shared-across-forks lookup (matches #3 and beats it by avoiding per-worker deserialization); the layered cache + sha256 request-hash memoization (implements #6 at the *result* layer); queue segmentation (partial #5); the admission gate (more sophisticated than #9).

**What they do that we don't (gap analysis):**
- **No embedded OLAP** (DuckDB/ClickHouse) for ad-hoc/point-in-time scans — everything routes through Postgres + Polars + the Rust dict.
- **No idempotency at the *task* layer** — result caching dedupes by request hash, but at-least-once redelivery of `run_algotest_job` isn't guarded (the "stuck running" + orphan-reservation notes are symptoms).
- **No horizontal cluster fan-out** — capped to one 16 GB box; LAN remotes exist but are ad-hoc.
- **No vectorized "fast alpha-scan" tier** — the Rust engine is authoritative everywhere; no cheap approximate exploratory pass.
- **No fair per-tenant queueing; no Flower/structured monitoring.**

**Top-5 highest-ROI adoptions (ranked):**
1. **Task-level idempotency keys + orphan-reservation self-healing** — cheapest, highest reliability win; kills "stuck running / ghost reservation."
2. **DuckDB over the existing Parquet/Arrow cache** for ad-hoc + point-in-time queries — reuses assets you already write, no hot-path change.
3. **Fair per-tenant/per-optim queue accounting + Flower.**
4. **Formalize LAN remote-workers into an elastic fan-out** for optimizer combos — the only realistic path past the single-box ceiling.
5. **Optional vectorized approximate pre-screen** for optimizer grids (lowest priority — parity is the platform's hard rule).

*Sources:* BullAlert (Python backtest engines 2026), IBKR Quant (vector vs event), QuantConnect LEAN docs + lean.io, ClickHouse & DuckDB columnar benchmarks, Celery production/idempotency patterns, AlgoTest.in.

---

## 15. SaaS Transformation Roadmap — multi-tenant, without breaking existing

> **Guiding split (the whole strategy in one line):** every calc-input key — `(symbol, expiry, strike, type, date)` in `fast_lookup`, the Rust `simulate`, and the symbol/date-keyed Arrow/feather caches — **stays global and untouched**. Tenancy wraps the *identity* of caches, jobs, uploads, and result artifacts, **never the numbers.**

### 15-A. Multi-tenancy & data isolation
- **Model: shared DB + `tenant_id` on the small per-tenant tables only.** The market-data tables (`option_data`, `spot_data`, `expiry_calendar`, `trading_holidays`, `super_trend_segments`) are the crown jewels — memory-mapped once, sized to 16 GB; schema/DB-per-tenant would force one market-data copy per tenant and blow the budget. Only `backtest_runs`/legs/summary/pivot + `filter_date_sets` are tenant-owned. Postgres RLS can harden later without touching schema again.
- **Namespace everything global with one helper `tenant_ns(tid)`** → `""` for the default tenant (existing keys/files unchanged), `t:{tid}:` for new tenants:
  - Redis backtest cache key (`backtest_cache.py:316`) — but `CACHE_VERSION` + `data_version` **stay global** (they version shared market data + engine).
  - Optimizer keys (`result_store.py:112-121`). **Critical leak to fix:** `list_recent_jobs` SCANs `optim:*:meta` and `AutoDownloadQueue.jsx` polls it system-wide — **today any browser can download any tenant's job by `job_id`.** Scope the SCAN + gate downloads on ownership.
  - On-disk artifacts: `ZIP_CACHE_DIR`/`OPTIM_TRADES_DIR`/`OPTIM_PARQUET_DIR` → `{tid}/{job_id}` (default = flat path).
  - Uploads (`routers/upload.py:44`): market-data upload becomes **admin-only** (it mutates shared tables + bumps global `data_version`); per-tenant filter CSVs get `tenant_id`, never touch `option_data`.
  - **Keep global:** `algotest:nodes:*`, `algotest:optim:active:*`, `algotest:data_version`, and the Arrow/Parquet/feather symbol caches — duplicating them defeats the design.

### 15-B. Auth, public API, quotas
- **Zero auth today** (verified): CORS `*`+credentials, only "identity" a spoofable `x-forwarded-for` stamped as `payload["_client_ip"]`. Auth is a **pure edge concern** — no engine change.
- **Model:** hosted OIDC (Clerk/Auth0) for the browser UI + **hashed API keys** (`sk_live_…`) for programmatic quant access; both resolve to `(tenant_id, user_id, role, plan)`. (Avoid Supabase — a second Postgres/auth stack fights the 16 GB budget.)
- **Thin additive dependency:** new `services/auth.py:require_ctx(request)` registered globally via `include_router(..., dependencies=[Depends(require_ctx)])` — **no router body changes.** Mirror the existing IP-stamp: add `payload["_tenant_id"] = ctx.tenant_id` beside `backtest.py:827` / `optimize.py:216`; the Celery payload already flows verbatim to the worker, so tenant identity is free. Calc path never sees it.
- **RBAC** owner/analyst/viewer via `Depends(require_role(...))` on run-endpoints only. Cross-tenant read isolation = a 5-line ownership check on `get_optimize_job`/`tradesheets.zip`.
- **Per-plan quotas reuse the admission gate verbatim:** call `acquire` a *second* time against `node_id=f"tenant:{tid}"` with a plan-derived budget (free = 1×cost → physically can't hold two heavy jobs). Make `active_optim_count` tenant-scoped + a per-plan `max_concurrent_optims` → **429** for free tenants at 1. Add a Redis token-bucket rate limit in `require_ctx`; monthly compute quota = a Redis counter → **402** over cap.
- **Public API:** expose a stable `/api/v1` group (alias current `/api`), API-key security scheme in the existing `/docs` OpenAPI, `/health*` unauthenticated for probes.

### 15-C. Cloud-native horizontal scaling (keep the box, add elasticity)
You already have the primitives (remote-worker = a hand-rolled autoscaler). The one true blocker is **node-local artifacts**.
- **Make workers stateless:** introduce an `ARTIFACT_STORE` abstraction — `local` (today's paths, default, unchanged) and `s3` (MinIO on-prem / S3 cloud). Put objects at `s3://bucket/{tenant}/{job_id}/…`; `_download_base_for_job` returns a **presigned URL** instead of a node IP → the per-node `remote-api` sidecar **disappears**. XLSX bytes are byte-identical; only *where they land* changes.
- **Market-data caches stay node-local** — the mmap benefit dies on network storage. Hydrate `ALGO_RUST_CACHE_DIR` from object storage via an **init-container** before the worker starts; a cold burst node must warm before serving (or lookups hard-fail).
- **Externalize Redis** to managed Redis (drop-in `REDIS_URL` swap) — but note the split-host gotcha (`result_store`/`backtest_cache` read `REDIS_HOST`/`REDIS_PORT`, not `REDIS_URL`); set all three. Move mem-gate keys to a non-evicted DB so reservations aren't LRU-dropped mid-job.
- **Formalize the autoscaler with KEDA:** a k8s Deployment per queue class, KEDA's Redis-list scaler on queue depth (`_queue_depth` already computed); keep `--concurrency=1` backtest / `=2` optimize so one pod ≈ one gate slot; scale-to-zero the optimize Deployment (mirrors today's profile gate).
- **Hybrid topology:** the on-prem box stays a permanent pool (joins the cloud broker exactly like remote-worker does today); cloud burst pods carry the same image + warmed cache.
- **Migration order:** (1) `ARTIFACT_STORE` abstraction default `local` (no behavior change) → (2) MinIO + presigned downloads, retire `remote-api` → (3) externalize Redis → (4) warmed-cache init-container → (5) KEDA autoscaling → (6) fold on-prem box in as a hybrid pool.

### 15-D. The "do not break existing" guarantee (most important)
The codebase already contains the exact safety machinery — the discipline is to *use it as a hard gate*.
- **Freeze the calc frontier.** `code_version.py:32-34` already hashes `services/`, `worker/`, `engines/`, `strategies/`, `base.py`, and the `.so`. **Pin this hash in CI**; fail any PR that changes it unless explicitly labelled an approved-calc-change. Every SaaS feature runs *before* `_normalize_request` or *after* `execute_algotest_job` returns — never inside the engine.
- **Golden-master regression gate already exists.** `tests/parity/` freezes `(trades, summary, pivot)` per archetype (36 snapshots; 40+ archetypes covering SL/TrailSL/buffer/re-entry/spot-adj/futures/all strike modes/STR/rollover) and diffs at `atol=0.01`. Make it **blocking in CI** with `PARITY_REQUIRE_DATA=1` (missing data fails, not skips). Add `parity_harness.py` (backtest↔optimizer differ) as a second gate. **Any SaaS PR that produces one diff line is rejected.**
- **Contract tests on payload normalization** — the gateway must yield a **byte-identical** normalized dict to the pre-SaaS path (test the `dd/mm/yyyy`, `date_from`/`from_date` variants through both paths).
- **Blue-green/canary using existing node routing** — deploy the multi-tenant gateway as a **new node/queue**; the current single-tenant queues keep serving unchanged; the `code_version` 409 staleness guard means a stale SaaS image can never silently run different calc code.
- **Safe worker draining** — add `task_acks_late=True` + `task_reject_on_worker_lost=True` (additive) so an in-flight job re-queues on graceful shutdown; drain via SIGTERM warm-shutdown, never SIGKILL (avoids the orphan reservation).
- **DB migrations additive + nullable** — `010_add_tenant_id.sql` adds `tenant_id DEFAULT 'default'` to execution/result tables only; `option_data` untouched; don't tighten `NOT NULL` until a green parity run.
- **Rollback is trivial** — every change is a flag / new queue / nullable column; flip the gateway off, route tenants back to `backtests`.

**Phased rollout:**
- **Phase 0 — Wrap, change nothing.** Capture N snapshots on the frozen engine; make parity gates blocking; pin `code_version`. **Zero behavior change.**
- **Phase 1 — Tenant context layer** around `_normalize_request`; contract tests prove byte-identical dicts.
- **Phase 2 — Storage adapter.** Namespace Redis/cache-dirs/`result_store` by tenant; `'default'` = today's exact keys.
- **Phase 3 — DB additive columns.** `tenant_id` nullable+default, backfill `'default'`; `option_data` untouched.
- **Phase 4 — Blue-green/canary.** SaaS gateway as a new node/queue; canary one tenant; single-tenant untouched.
- **Phase 5 — Enforce.** Only after full backfill + still-green parity: tighten `NOT NULL`, retire the default tenant. Engine never touched throughout.

---

## 16. Consolidated Roadmap — Do / Need-to-do / Can-add

Legend: **Touches calc? = NO for everything here** (that's the rule). Risk = R, Effort = E (S/M/L).

### 16.1 NOW — reliability & hygiene (do this week, R:low)
| Item | E | Why |
|---|---|---|
| `git rm -r --cached backend/native/target`; delete ~20 zero-byte junk files; fix `.gitignore` (§13) | S | Kills 435-file `git status` churn, ~1.8 GB |
| Delete dead scratch scripts + dead `redis_cache.py` + dead `buildTradeExcel.js` + 3 unused frontend deps (§13) | S | Smaller, clearer repo; faster installs |
| Fix `backup.sh` PROJECT path + schedule + test restore (§12) | S | Backups are currently broken |
| Batch single-backtest MAE/MFE (#2) + delete double analytics pass (#3) (§10) | S | Biggest latency win, reuses existing code |
| Fix optimize `concurrency` doc drift (2 vs 3) (§2) | S | Config truthfulness |

### 16.2 NEXT — the feather fix + throughput (R:low–med)
| Item | E | Section |
|---|---|---|
| **Feather: atomic writes + coverage guard for all kinds + manifest + staleness probe + non-root files + `rebuild_feather --verify` gate + external volume** | M | **§9.5** |
| Result-cache short-circuit before the gate + pre-warm common ranges | S | §11.4 #1 |
| Task-level idempotency keys + orphan-reservation `HDEL` on cancel/kill | M | §11.4 #6-7 |
| Bump `worker-backtests-fast` to `--concurrency=2` after footprint shrink | S | §11.4 #3 |
| Redis persistence (AOF/RDB) so a restart doesn't lose jobs | S | §12 P0 |

### 16.3 PRODUCTION-READY (R:med, mostly P0/P1 §12)
| Item | E |
|---|---|
| Remove `.env` from history, rotate password, secrets store | M |
| Auth layer (`require_ctx`) + RBAC + per-tenant job scoping (§15-B) | M |
| Fix CORS to explicit origins; Redis password; stop `0.0.0.0` port publish | S |
| Observability: `/metrics` + Prometheus service + structured logs + Flower + alerts (§12) | M |
| CI: parity gate + tests + image build on push; versioned image tags | M |
| Graceful drain for backtest/upload workers | M |

### 16.4 SaaS / SCALE (R:med–high, §15) — build in the phase order above
| Item | E |
|---|---|
| Phase 0 parity/`code_version` freeze gate | S |
| Tenant context + storage namespacing + additive `tenant_id` migration | M |
| `ARTIFACT_STORE` abstraction → MinIO/S3 + presigned downloads (retire remote-api sidecar) | M |
| Externalize Redis; warmed-cache init-container; KEDA queue-depth autoscaling | L |
| Fold on-prem box in as a hybrid worker pool | M |

### 16.5 CAN-ADD — new product capability (R:low–med)
| Item | Value |
|---|---|
| **DuckDB over the existing Parquet/Arrow cache** for ad-hoc point-in-time chain queries | Big analyst UX win, no hot-path change |
| Broaden `OPTIMIZE_RUST_LOOP=1` whitelist family-by-family via shadow-diff | Faster optims, less Python tax |
| Finish the Rust XLSX writer (Summary/Patch/WOW-MOM) | Removes openpyxl bottleneck + JS/Python drift |
| Move WOW/MOM + optim-summary Excel fully server-side | Kills the last cross-language calc duplication (§7) |
| Frontend: split `StrategyBuilder`, add a store, responsive breakpoints, self-host fonts | Maintainability + mobile + dodge DPI-SSL |
| Optional vectorized approximate pre-screen for optimizer grids | Prune dead combos before the authoritative pass |

---

## 17. Appendix — key facts to keep handy

**Hardware:** Dell QCT1250, i5-14500 (14C/20T), **16 GB RAM (fixed)**, KIOXIA 512 GB NVMe SSD, **24 GB SSD swap** (the overcommit cushion). Ubuntu 26.04.

**Memory budget:** per-container ceilings sum to ~20,900M default (+13,000M optimize) vs ~15,160M host → deliberate overcommit onto swap; the **admission gate** (`HEAVY_MEMORY_BUDGET_MB`, local 19000) bounds *active* heavy work so RAM+swap can't be exhausted.

**Key env flags:** `FAST_LOOKUP_MODE` (auto/rust/python), `OPTIMIZE_RUST_LOOP` (0/shadow/1), `OPTIMIZE_PARALLELISM` (6), `BACKTEST_FAST_QUEUE_MAX_DAYS` (550), `HEAVY_MEMORY_GATE`, `HEAVY_MEMORY_BUDGET_MB`, `HEAVY_RESERVATION_TTL_SECONDS` (~2400), `OPTIMIZE_STUCK_SECONDS` (9000), `BACKTEST_INCLUDE_MAE_MFE`, `ENGINE_BACKEND`, `ALGO_RUST_CACHE_DIR`, `PARQUET_CACHE_DIR`, `BULK_LOAD_CHUNK_YEARS`, `RUST_SIM_THREADS`.

**The golden invariants (never violate):**
- Rust-only lookups; hard-fail, no Python fallback in the live path.
- Never change trade-calc numeric logic; the parity harness is the arbiter.
- Never restart workers with jobs running (strands the mem-gate reservation ~40 min).
- Backtest tradesheet == optim per-combo == optim master, identical every metric.

**Files that are the whole ballgame:** `services/engine_rust.py` (live path), `backend/native/src/{simulate,lib}.rs` (Rust engine), `base.py` (data + cache), `services/memory_gate.py` (no-OOM), `services/optimizer/{runner,parallel}.py` (sweeps), `services/rust_fast_path.py` (feather), `tests/parity/` (the safety net).

---

*Prepared read-only. No source files were modified. Every recommendation is additive and calc-neutral by construction; the parity harness in `backend/tests/parity/` is the mechanical proof of that promise.*
