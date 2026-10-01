# Operations Architecture — in-house PC server

How this box is meant to run, why each limit exists, and what to do when it
misbehaves. Every number here was **measured on this machine**, not estimated.

Audience: whoever operates the box. Read §2 before changing any limit, §5 before
any deploy, §6 when something breaks.

---

## 1. The box, and the constraint that shapes everything

```
Dell Pro Tower QCT1250 · i5-14500 (14C/20T) · 16 GB DDR5 (FIXED) · 512 GB NVMe · 24 GB swap
Ubuntu 26.04 · Docker Compose · Postgres 15 · Redis · Celery
```

**It is simultaneously a server and somebody's desktop.** Chrome, LibreOffice and
the terminal run on it while sweeps execute. That single fact drives most design
decisions below — a headless server would be tuned very differently.

**Users:** ~4 colleagues submit jobs over the LAN (192.168.4.x) plus optional
remote worker PCs. They compete for one 16 GB box. RAM, not CPU, is the scarce
resource: 20 threads, 16 GB.

---

## 2. Resource governance — five independent brakes

No single mechanism prevents overload; each covers a failure the others cannot
see. Change one only after reading why it exists.

| # | Brake | Where | Protects against |
|---|-------|-------|------------------|
| 1 | `cpus: 12.0` cgroup quota | compose, worker-optimize | Desktop lag. 8 of 20 cores are fenced off **by the kernel** — not by tuning. |
| 2 | Memory admission gate | `services/memory_gate.py` | Too many heavy jobs *admitted* at once. Over budget ⇒ job waits as "queued". |
| 3 | Live-RAM fork cap | `parallel.cap_parallelism_for_live_ram` | Forking wide when RAM is already low. Re-measured **every batch**. |
| 4 | PSI thrash brake | same function, `/proc/pressure/memory` | Swap thrash that `MemAvailable` cannot see (page cache counts as "free"). Halves width above 8% stall. |
| 5 | Worker recycling | `--max-tasks-per-child` | Slow memory accumulation in long-lived workers. |

### Measured constants (do not guess these again)

| Quantity | Measured | Notes |
|---|---|---|
| Private RAM per optimize child | **91 MB** single-index, **225 MB** multi-index | RSS looks like ~4 GB — that is the CoW-shared feather counted per process. |
| Sweep parent | ~1.5 GB private | |
| worker-optimize container, P=6 | **2.04 GiB** of a 12.7 GiB cap | |
| CPU per child | **~1 core** (605% at P=6) | *Not* 2, despite `RUST_SIM_THREADS=2`. |
| Backtest job | ~3.1 GB actual, 3,873 MB reserved | |
| Per-combo cost (440-row tradesheet) | 556 ms engine + 350 ms workbook + ~450 ms other | Engine is only ~40%. |

### Current settings and their arithmetic

```
OPTIMIZE_PARALLELISM=12          ceiling only; split_width() divides it live
OPTIMIZE_MAX_CONCURRENT=2        3rd optim waits holding NO memory
OPTIMIZE_WORKER_PRIVATE_MB=300   vs 91-225 measured → 1.3-3x margin
HEAVY_MEMORY_BUDGET_MB=12500     2 optims (4125 ea) + 1 backtest (3873) = 12,123 ✓
HEAVY_GATE_LIVE_RAM_FLOOR_MB=2500  RAM always left for the desktop
OPTIMIZE_MEM_PRESSURE_MAX_PCT=8  PSI stall % above which width halves
```

Keep `HEAVY_MEMORY_BUDGET_MB` **below** worker-optimize's 12.7 GiB cgroup limit,
or the kernel SIGKILLs a child before the gate ever fills and the gate is
decorative.

### Width is dynamic, always

`split_width()` recomputes at **every batch boundary**:

```
width = min( CEILING // (live_optims + blocked_backtests),
             (MemAvailable - FLOOR) // PRIVATE_MB,
             halve-if-PSI-stall > 8% )
```

1 optim alone → up to 12 · 2 optims → 6 each · backtest blocked → narrower, then
climbs back. Nothing is pinned.

---

## 3. Concurrency policy

**Target: 2 optims + 1 backtest concurrently.** A 3rd optim queues *before*
registering or reserving anything, so it does not shrink the running sweeps.

Queues are separate on purpose: `optimize`, `backtests` (long), `backtests_fast`
(short, `BACKTEST_FAST_QUEUE_MAX_DAYS`), `uploads`. A 7-year sweep must never
block a 1-month backtest.

---

## 4. State durability — the biggest structural gap

> **Redis runs with `--save "" --appendonly no`. It is a pure in-memory cache.**

Removing the Redis container **destroys all job state**: metadata, result rows,
download links, resume ability. Per-combo files under `/data` survive, but the
API needs the Redis meta to serve them.

This has cost real work twice in one day: two partial sweeps (~3,150 computed
combos) became unresumable after a `docker compose down`.

**Mitigation until fixed:** never `down` Redis while jobs matter.
**Fix:** `--appendonly yes`. Costs disk writes and a slightly slower shutdown;
buys job state that survives a restart. Strongly recommended before 60k runs
where a lost sweep is hours, not minutes.

Durable today: `/data` (per-combo CSV/XLSX/wm, ZIP cache), Postgres (`pgdata`),
Arrow feathers, Rust build caches — all named volumes, safe across `down`
**without** `-v`.

---

## 5. Deploy runbook

### What propagates how

| Change | Reaches production by |
|---|---|
| Backend `.py` | bind-mounted → **worker restart** (`dev_supervisor` auto-reloads when idle) |
| `worker-uploads` code | **image rebuild** — it has *no* bind mount |
| Rust (`.rs`) | maturin wheel → `algo-backend-base` → app image → recreate |
| `docker-compose.yml` env | **recreate** (`up -d`), *not* `restart` |
| Frontend | `dist` build in a node container, then `up -d frontend` |

### Procedure

```bash
./maintenance.sh on "Rebuilding — back in ~10 min"   # UI shows the overlay
# ... verify idle: queues empty, no live optims, gate empty ...
docker compose --profile optimize up -d --force-recreate worker-optimize
./maintenance.sh off
```

### Three traps that have bitten

1. **`docker compose down` does NOT stop `worker-optimize`** — it is behind
   `profiles: ["optimize"]`. It survives orphaned, spamming broker-connection
   tracebacks, and holds the network open. Use
   `docker compose --profile optimize down`.
2. **Restarting a worker kills in-flight jobs** — including one in its *finalize*
   phase, which no longer appears in any queue or the gate. Check
   `phase != finalizing:*` too, not just "is the queue empty".
3. **A pinned dependency can vanish from PyPI.** `granian==1.6.0` was pulled; the
   pip layer was cached so nothing noticed for months, then a rebuild failed at
   the worst moment. Run `backend/tools/check_pinned_versions.sh` before deploys.

---

## 6. Incident runbook

### Sweep stuck, workers idle, CPU ~0%
A pool child was SIGKILLed (cgroup OOM) and the parent waited forever. Now
bounded: `ar.get(timeout=OPTIMIZE_BATCH_TIMEOUT_SECONDS)` logs
`batch chunk lost` and fails that chunk instead of hanging. **Resume** fills the gap.

### Job stuck at `status=running`, download refused
The worker died mid-job. Meta freezes at `running`. Artifacts are intact:

```bash
curl -X POST .../api/optimize/jobs/<id>/resume     # dispatches only missing combos
```
Resume accepts a *stale* running job (liveness = registry + heartbeat, not status).

### Pause a sweep WITHOUT losing it
**Never use the UI Cancel button** — `DELETE` wipes meta, results *and* files.

```bash
docker compose exec worker-optimize \
  celery -A worker.celery control revoke <job_id> --terminate   # keeps everything
curl -X POST .../api/optimize/jobs/<job_id>/resume              # later
```

### Swap climbing toward full
Swap *usage* is harmless (cold pages, `so=0`). Swap **pressure** is not — check
`/proc/pressure/memory`; sustained `full avg10 > 8%` means thrash. Worker
recycling caps growth; a worker restart reclaims it instantly.

**Never run `swapoff -a` while the stack is up.** It must fault every swapped
page back into RAM; with 15 GB swapped on a 15 GB box it cannot succeed and
triggers a global OOM. It killed LibreOffice and two Chrome processes on
2026-08-06. Stop the stack first, or leave swap alone.

### "Server is restarting" with nothing restarting
Fixed: the overlay is now shown **only** when an operator sets the maintenance
flag (`./maintenance.sh on`). It is never inferred from slow health polls — a
heavy sweep starves `/health` past the client timeout, which used to look
identical to a dead backend.

### Optim fails instantly, 0/N combos
Read the error. A common one is genuine data coverage:
`no MIDCPNIFTY spot in the Rust cache for <date>` — the feather is narrower than
Postgres. Fix with `backend/rebuild_feather.py` **while idle** (it writes a
shared feather every job reads).

---

## 7. Observability — what to watch

```bash
free -m; cat /proc/pressure/memory      # RAM + real thrash signal
docker stats --no-stream                # per-container CPU/RAM
docker compose exec redis redis-cli hgetall algotest:mem_gate:local   # reservations
docker compose exec redis redis-cli hlen algotest:optim:active:local  # live optims
docker compose logs -f worker-optimize | grep -E "fork width|SUCCESS|FAILED|MISS|signal 9"
```

| Signal | Meaning |
|---|---|
| `fork width -> P=N` | width changed (only logged on change) |
| `MISS` on the ZIP fast path | **defect** — workbooks should be reused, not rebuilt |
| `batch chunk lost` | a child died; job still resumable |
| `signal 9` | cgroup OOM kill |
| PSI `full avg10 > 8%` | thrashing; the brake will halve width |

Healthy reference (measured): 3600-combo sweep ≈ 5 min at P=6; ZIP of 3600
workbooks **0.73 s**; WOW/MOM 1–2 min. A ZIP taking minutes means the fast path
missed.

---

## 8. Known scale limits

| Limit | Ceiling | Notes |
|---|---|---|
| Redis result rows | **~32,000 combos** | 2.5 KB/row after moving `wm` to disk (was 16 KB). |
| WOW/MOM sheet width | **~20,000 combos** | ~0.8 Excel columns per combo; pages onto `(2)`, `(3)` past 16,384. |
| ZIP cache disk | 30 GB budget | `prune_zip_cache()` evicts oldest after each job. |
| ZIP size | ~126–183 KB/combo | 60k combos ≈ 8–11 GB; served by `FileResponse`, streamed, never in RAM. |

**For 60k-combo runs:** enable Redis persistence first (§4), and expect the
WOW/MOM grid to page across sheets.

---

## 9. Deliberate trade-offs

- **Rust-only, no Python fallback.** Engine, lookups, metrics, MAE/MFE and the
  XLSX writer all hard-fail rather than silently computing something different.
  An unsupported shape errors loudly — that is the intent.
- **Per-combo workbooks are built during the sweep** (350 ms each) so the ZIP
  assembles in <1 s instead of rebuilding for minutes. Cost is paid in parallel;
  the saving would otherwise be paid serially.
- **The 2,500 MB desktop cushion costs throughput.** Sweeps narrow sooner than
  the hardware strictly requires. Deliberate: this is someone's desktop.
- **Cancel deletes everything; revoke keeps it.** Two different verbs for two
  different intents — see §6.
