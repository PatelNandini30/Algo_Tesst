"""
Persist optimization results to Redis with optional Parquet overflow.

Key layout:

    optim:{job_id}:meta             — JSON {status, total, done, started_at,
                                              objective, method, eta_seconds, error}
    optim:{job_id}:results          — list of compact JSON rows, one per combo
    optim:{job_id}:parquet_path     — set if results were spilled to disk

TTL: 24 hours by default.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from typing import Any, Dict, Iterable, List, Optional

import redis

logger = logging.getLogger(__name__)

# Redis connection. Historically read from REDIS_HOST/REDIS_PORT (defaulting to
# the Docker service name "redis"), while celery/memory_gate use REDIS_URL. On a
# LAN remote worker (remote-worker/) the "redis" name doesn't resolve, so if
# REDIS_HOST isn't explicitly set we parse REDIS_URL instead — one source of
# truth, and no silent "[OPTIM_STORE] Redis unavailable" on remote nodes. Main
# box is unaffected: it sets REDIS_URL=redis://redis:6379/0, which parses to the
# same host/port the old default produced.
def _resolve_redis_params():
    if os.getenv("REDIS_HOST"):
        return (
            os.getenv("REDIS_HOST"),
            int(os.getenv("REDIS_PORT", "6379")),
            int(os.getenv("REDIS_DB", "0")),
            os.getenv("REDIS_PASSWORD", None),
        )
    url = os.getenv("REDIS_URL")
    if url:
        from urllib.parse import urlparse
        u = urlparse(url)
        db_path = (u.path or "").strip("/")
        return (
            u.hostname or "redis",
            u.port or 6379,
            int(db_path) if db_path.isdigit() else 0,
            u.password,
        )
    return "redis", 6379, 0, None


REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD = _resolve_redis_params()
OPTIM_TTL = int(os.getenv("OPTIMIZE_RESULT_TTL", "86400"))
OPTIM_SPILL_THRESHOLD = int(os.getenv("OPTIMIZE_PARQUET_SPILL_AT", "10000"))
OPTIM_PARQUET_DIR = os.getenv("OPTIMIZE_PARQUET_DIR", "/data/cache/optim_results")

# ── ZIP cache ────────────────────────────────────────────────────────────────
# Shared by runner.py (pre-build) and routers/optimize.py (download) so they
# always agree on the filename. Bump ZIP_BUILDER_VERSION when the XLSX format
# changes and old ZIPs must be invalidated.
ZIP_CACHE_DIR = os.getenv("OPTIMIZE_ZIP_DIR", "/data/cache/optim_zips")
ZIP_BUILDER_VERSION = "v22"  # v22: value ramp compressed into the palest
                             #      band of the hue (mostly-negative data)
                             # v21: cool-rose sequential value ramp +
                             #      uniform 3-row margin after every table
                             # v20: MIN grids REMOVED from the merged WOW/MOM
                             #      summary (per-combo tradesheets keep them);
                             #      grid colours now reuse the sheet's theme
                             # v19: MIN-grid formatting — 2dp display (full value
                             #      kept for formulas), light->dark value ramp,
                             #      ramped headers, 2-row margins
                             # v18: Min-of-Final-MAE / Min-of-Actual-Live-DD grids
                             #      under each WOW & MOM block
                             # v17: per-combo leg-wise "Rules" first sheet added


def prune_zip_cache(max_gb: float = None) -> int:
    """Evict oldest cached ZIP/XLSX artifacts until the cache fits its budget.

    There was no eviction at all: the cache had grown to 19 GB, and a single
    60,000-combo sweep is worth ~8-11 GB of ZIP on its own, so a few large runs
    would fill the disk. Oldest-first by mtime — a job's artifacts are rebuilt
    on demand if someone downloads it again. Returns bytes freed.
    """
    if max_gb is None:
        try:
            max_gb = float(os.environ.get("OPTIM_ZIP_CACHE_MAX_GB", "30"))
        except (TypeError, ValueError):
            max_gb = 30.0
    budget = int(max_gb * (1024 ** 3))
    # AGE first, then the size budget. Size alone let the cache sit permanently
    # AT its ceiling, so every new sweep evicted the oldest artifacts — which are
    # exactly the ones someone comes back for, and a miss costs a multi-minute
    # rebuild instead of a <1s serve. Expiring by age keeps a predictable window
    # (7 days, matching the trades retention) and the budget stays as the
    # backstop for a burst of very large sweeps inside that window.
    try:
        max_age_days = float(os.environ.get("OPTIM_ZIP_CACHE_MAX_AGE_DAYS", "7"))
    except (TypeError, ValueError):
        max_age_days = 7.0
    age_cutoff = (time.time() - max_age_days * 86400) if max_age_days > 0 else None
    freed_age = 0
    try:
        entries = []
        for name in os.listdir(ZIP_CACHE_DIR):
            path = os.path.join(ZIP_CACHE_DIR, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if not os.path.isfile(path):
                continue
            if age_cutoff is not None and st.st_mtime < age_cutoff:
                try:
                    os.remove(path)
                    freed_age += st.st_size
                    continue
                except OSError:
                    pass
            entries.append((st.st_mtime, st.st_size, path))
        if freed_age:
            logger.info("[OPTIM_STORE] ZIP cache: expired %.1f GB older than %.0fd",
                        freed_age / (1024 ** 3), max_age_days)
    except OSError:
        return 0
    total = sum(e[1] for e in entries)
    if total <= budget:
        return freed_age
    freed = freed_age
    for _mtime, size, path in sorted(entries):          # oldest first
        if total - freed <= budget:
            break
        try:
            os.remove(path)
            freed += size
        except OSError:
            continue
    if freed > freed_age:
        logger.info("[OPTIM_STORE] ZIP cache pruned: freed %.1f GB over budget (%.0f GB)",
                    (freed - freed_age) / (1024 ** 3), max_gb)
    return freed


def zip_cache_path(job_id: str, patchwise: bool = False) -> str:
    """Canonical path for a job's pre-built ZIP file."""
    os.makedirs(ZIP_CACHE_DIR, exist_ok=True)
    ver = f"{ZIP_BUILDER_VERSION}-pw" if patchwise else ZIP_BUILDER_VERSION
    return os.path.join(ZIP_CACHE_DIR, f"{job_id}.{ver}.zip")


# Bumped on its own when only the WOW/MOM grid LAYOUT changes, so those
# workbooks rebuild without invalidating (and re-running) every cached ZIP.
#   wm2 — blocks flow horizontally and wrap into bands instead of stacking.
WOW_MOM_LAYOUT_VERSION = "wm5"   # wm5: pivot sheets use the summary grid's
                                #      own slotting (adjustment across, strike down)
                                # wm4: pivot sheets tile 3-across then stack
                                # wm3: MIN pivots moved to their own
                                #      captioned per-combo sheets


def wow_mom_cache_path(job_id: str, patchwise: bool = False) -> str:
    """Canonical path for a job's pre-built WOW/MOM XLSX file."""
    os.makedirs(ZIP_CACHE_DIR, exist_ok=True)
    suffix = "-pw" if patchwise else ""
    return os.path.join(
        ZIP_CACHE_DIR,
        f"{job_id}.{ZIP_BUILDER_VERSION}-{WOW_MOM_LAYOUT_VERSION}{suffix}.xlsx",
    )


def wow_mom_parts_zip_path(job_id: str, patchwise: bool = False) -> str:
    """Path for a LARGE sweep's WOW/MOM, delivered as a ZIP of part workbooks.

    One workbook cannot hold a big sweep: the builder needs ~3,272 cells per
    combo across its four sheets, so 50,176 combos is 164M cell objects (13-20
    GB) and the worker was SIGKILLed after the sweep had already finished.
    Building in fixed-size parts keeps peak memory tied to the PART size, not
    the sweep size — measured flat at ~800 MB for 2,500-combo parts, 21 parts in
    6.4 min for a real 50,176-combo job.
    """
    os.makedirs(ZIP_CACHE_DIR, exist_ok=True)
    suffix = "-pw" if patchwise else ""
    return os.path.join(
        ZIP_CACHE_DIR,
        f"{job_id}.{ZIP_BUILDER_VERSION}-{WOW_MOM_LAYOUT_VERSION}{suffix}.parts.zip",
    )


def _stamp_xlsx_version(job_id: str) -> None:
    """Record that this job's per-combo XLSX were built by the CURRENT builder.

    Written as the sweep writes the workbooks, not at finalize. Without this the
    marker only ever appeared inside ensure_xlsx_version — so on a job's FIRST
    finalize it was always missing, the "no marker -> treat as stale" branch
    fired, and every workbook the sweep had just built was deleted. The ZIP
    fast path (assemble from pre-built files, seconds) could therefore never hit:
    it was reliably wiped moments before it was checked, and 2160 workbooks were
    rebuilt from CSV instead (~3 min). Cheap: one tiny file write per combo.
    """
    try:
        trades_dir = get_trades_dir(job_id)
        os.makedirs(trades_dir, exist_ok=True)
        marker = os.path.join(trades_dir, ".xlsx_builder_version")
        current = f"{ZIP_BUILDER_VERSION}-{WOW_MOM_LAYOUT_VERSION}"
        try:
            with open(marker) as fh:
                if fh.read().strip() == current:
                    return
        except OSError:
            pass
        with open(marker, "w") as fh:
            fh.write(current)
    except Exception:
        pass          # marker is an optimisation; never fail a combo over it


def ensure_xlsx_version(job_id: str) -> int:
    """Drop a job's per-combo XLSX when they were built by an older builder.

    Unlike the ZIP and WOW/MOM caches, these live at a bare
    `trades_dir/[patchwise/]{label}.xlsx` with NO version in the path — the ZIP
    fast-path matches them by label, so the filename can't carry one. Result: a
    formatting change shipped fine but every download kept serving the old
    workbook off disk, twice in a row. A version marker beside them gives the
    same invalidation without touching the names. Returns the count removed.
    """
    import glob
    try:
        trades_dir = get_trades_dir(job_id)
        if not os.path.isdir(trades_dir):
            return 0
        marker = os.path.join(trades_dir, ".xlsx_builder_version")
        current = f"{ZIP_BUILDER_VERSION}-{WOW_MOM_LAYOUT_VERSION}"
        try:
            with open(marker) as fh:
                if fh.read().strip() == current:
                    return 0
        except OSError:
            pass          # no marker yet → pre-versioning build, treat as stale
        removed = 0
        for path in glob.glob(os.path.join(trades_dir, "**", "*.xlsx"), recursive=True):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
        with open(marker, "w") as fh:
            fh.write(current)
        if removed:
            logger.info("[OPTIM_STORE] cleared %d stale per-combo XLSX for %s (builder %s)",
                        removed, job_id[:8], current)
        return removed
    except Exception as exc:
        logger.warning("[OPTIM_STORE] xlsx version check failed (%s): %s", job_id[:8], exc)
        return 0


def patchwise_summary_cache_path(job_id: str) -> str:
    """Canonical path for a job's pre-built patchwise summary JSON file."""
    os.makedirs(ZIP_CACHE_DIR, exist_ok=True)
    return os.path.join(ZIP_CACHE_DIR, f"{job_id}.{ZIP_BUILDER_VERSION}-pw-summary.json")


_client: Optional[redis.Redis] = None


def _redis() -> Optional[redis.Redis]:
    global _client
    if _client is not None:
        return _client
    try:
        c = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        c.ping()
        _client = c
        return c
    except Exception as e:
        logger.warning("[OPTIM_STORE] Redis unavailable: %s", e)
        return None


def _meta_key(job_id: str) -> str:
    return f"optim:{job_id}:meta"


def _results_key(job_id: str) -> str:
    return f"optim:{job_id}:results"


def _parquet_key(job_id: str) -> str:
    return f"optim:{job_id}:parquet_path"


def init_job(
    job_id: str,
    *,
    total: int,
    method: str,
    objective: str,
    extra: Optional[Dict[str, Any]] = None,
    # RESUME: keep the rows the interrupted run already produced. Wiping them
    # (the default, correct for a fresh job) defeated resume twice over — the
    # skip-set was read back empty so every combo was recomputed, AND the first
    # run's rows vanished from the master summary and WOW/MOM, which are built
    # from this list.
    preserve_results: bool = False,
) -> None:
    r = _redis()
    if r is None:
        return
    meta = {
        "status": "running",
        "total": int(total),
        "done": 0,
        "started_at": time.time(),
        # Progress heartbeat — bumped on every combo + every finalization step.
        # The watchdog kills a job only when THIS stops advancing (a real hang),
        # never a job that is still making progress. See services/optimizer/watchdog.py.
        "last_progress_at": time.time(),
        "phase": "starting",
        "method": method,
        "objective": objective,
        "eta_seconds": None,
        "error": None,
    }
    if extra:
        meta.update(extra)
    r.setex(_meta_key(job_id), OPTIM_TTL, json.dumps(meta))
    if preserve_results:
        # Seed progress from what survived, so the UI resumes at 1632/3600
        # instead of appearing to restart, and increment_done keeps counting up.
        try:
            _kept = int(r.llen(_results_key(job_id)) or 0)
            meta["done"] = _kept
            r.setex(_meta_key(job_id), OPTIM_TTL, json.dumps(meta))
            r.set(f"optim:{job_id}:done_counter", _kept)
        except Exception:
            pass
        return
    r.delete(_results_key(job_id))
    r.delete(f"optim:{job_id}:done_counter")


def update_progress(
    job_id: str,
    *,
    done: int,
    total: Optional[int] = None,
    phase: Optional[str] = None,
) -> None:
    r = _redis()
    if r is None:
        return
    raw = r.get(_meta_key(job_id))
    if not raw:
        return
    meta = json.loads(raw)
    meta["done"] = int(done)
    meta["last_progress_at"] = time.time()
    if total is not None:
        meta["total"] = int(total)
    if phase is not None:
        meta["phase"] = phase
    started = meta.get("started_at") or time.time()
    elapsed = max(time.time() - started, 0.001)
    rate = done / elapsed if done > 0 else 0
    remaining = max(int(meta["total"]) - done, 0)
    meta["eta_seconds"] = int(remaining / rate) if rate > 0 else None
    r.setex(_meta_key(job_id), OPTIM_TTL, json.dumps(meta))


def reset_done_counter(job_id: str, value: int = 0) -> None:
    """Re-baseline the running done counter.

    Needed on RESUME: the counter persists across runs (deliberately — a resumed
    job must keep counting up, not restart at zero), but if the grid has since
    been deduplicated the old count refers to a larger grid and progress reads
    over 100%. Setting it to the unique work already done keeps done <= total.
    """
    r = _redis()
    if r is None:
        return
    try:
        r.set(f"optim:{job_id}:done_counter", int(max(0, value)))
    except Exception as exc:
        logger.debug("[OPTIM_STORE] reset_done_counter failed: %s", exc)


def increment_done(job_id: str) -> None:
    """Atomically increment the running done counter in Redis.

    Called once per completed combo from any worker. The INCR is atomic;
    the meta update is best-effort (acceptable race for progress display).
    """
    r = _redis()
    if r is None:
        return
    counter_key = f"optim:{job_id}:done_counter"
    cnt = r.incr(counter_key)
    r.expire(counter_key, OPTIM_TTL)
    raw = r.get(_meta_key(job_id))
    if not raw:
        return
    meta = json.loads(raw)
    meta["done"] = int(cnt)
    meta["phase"] = "running"
    meta["last_progress_at"] = time.time()
    started = meta.get("started_at") or time.time()
    elapsed = max(time.time() - started, 0.001)
    rate = cnt / elapsed if cnt > 0 else 0
    remaining = max(int(meta.get("total", cnt)) - cnt, 0)
    meta["eta_seconds"] = int(remaining / rate) if rate > 0 else None
    r.setex(_meta_key(job_id), OPTIM_TTL, json.dumps(meta))


def heartbeat(job_id: str, phase: Optional[str] = None) -> None:
    """Bump the job's progress heartbeat (and optionally its phase label).

    Called at every finalization step boundary (spill, summary, ZIP, WOW/MOM)
    so a job that is still moving through post-processing is NEVER seen as stuck
    by the watchdog — only a step that itself hangs longer than the stuck
    threshold freezes `last_progress_at`. Best-effort; never raises.
    """
    r = _redis()
    if r is None:
        return
    try:
        raw = r.get(_meta_key(job_id))
        if not raw:
            return
        meta = json.loads(raw)
        meta["last_progress_at"] = time.time()
        if phase is not None:
            meta["phase"] = phase
        r.setex(_meta_key(job_id), OPTIM_TTL, json.dumps(meta))
    except Exception as exc:  # pragma: no cover - heartbeat must never break a run
        logger.debug("[OPTIM_STORE] heartbeat error for %s: %s", job_id, exc)


def append_result(job_id: str, row: Dict[str, Any]) -> None:
    r = _redis()
    if r is None:
        return
    r.rpush(_results_key(job_id), json.dumps(row, default=str))
    r.expire(_results_key(job_id), OPTIM_TTL)


def mark_complete(job_id: str, *, error: Optional[str] = None) -> None:
    r = _redis()
    if r is None:
        return
    raw = r.get(_meta_key(job_id))
    if not raw:
        return
    meta = json.loads(raw)
    meta["status"] = "failed" if error else "success"
    meta["error"] = error
    meta["finished_at"] = time.time()
    r.setex(_meta_key(job_id), OPTIM_TTL, json.dumps(meta))


def get_meta(job_id: str) -> Optional[Dict[str, Any]]:
    r = _redis()
    if r is None:
        return None
    raw = r.get(_meta_key(job_id))
    if not raw:
        return None
    meta = json.loads(raw)
    # Overlay atomic running counter when job is still in progress.
    if meta.get("status") == "running":
        cnt_raw = r.get(f"optim:{job_id}:done_counter")
        if cnt_raw is not None:
            meta["done"] = int(cnt_raw)
    return meta


def list_recent_jobs(limit: int = 200) -> List[Dict[str, Any]]:
    """List every optimize job with live meta (any machine, any browser) so a
    browser that did NOT enqueue a job can still discover and auto-download it
    by job_id — see routers/optimize.py GET /optimize/jobs and
    frontend/src/components/AutoDownloadQueue.jsx's system-wide poll.

    Cheap: SCANs the `optim:*:meta` keyspace (bounded by OPTIM_TTL — jobs
    expire out of Redis on their own) rather than maintaining a separate index.
    Returns newest-first, capped at `limit`.
    """
    r = _redis()
    if r is None:
        return []
    out: List[Dict[str, Any]] = []
    try:
        for key in r.scan_iter(match="optim:*:meta", count=200):
            job_id = key.split(":")[1] if isinstance(key, str) else key.decode().split(":")[1]
            try:
                meta = get_meta(job_id)
            except Exception:
                continue
            if not meta:
                continue
            out.append({"job_id": job_id, **meta})
    except Exception as exc:
        logger.warning("[OPTIM_STORE] list_recent_jobs scan failed: %s", exc)
        return []
    out.sort(key=lambda m: m.get("started_at") or 0, reverse=True)
    return out[:limit]


def _combo_fingerprint(row: Dict[str, Any]) -> str:
    """Stable hash of the result row's combo_label + summary PnL.

    Deduplication key: two rows are duplicates when they have the same
    combo_label (same human-readable strategy) AND identical total_pnl
    (same engine outcome).  Using both fields ensures:
      - Parameters swept with no engine effect (e.g. strike_type in
        pct_of_atm mode) are collapsed — same label, same result.
      - Different parameter values that happen to share a legacy label
        due to abs() rounding are NOT collapsed — same label, different result.
    """
    label = row.get("combo_label") or str(row.get("combo_id"))
    pnl = row.get("summary", {}).get("total_pnl", None)
    return f"{label}|{pnl}"


def _dedupe_by_label(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        key = _combo_fingerprint(row)
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def get_results(
    job_id: str,
    *,
    offset: int = 0,
    limit: int = 100,
    sort_key: Optional[str] = None,
    descending: bool = True,
    patchwise: bool = True,
) -> Dict[str, Any]:
    """Return {"rows": [...], "total": <unique_count>} for the results page.

    patchwise (default) overlays each row's summary_pw onto its summary BEFORE
    sorting, so the on-screen table both ranks and displays on the same basis the
    downloads use. Overlaying after the sort would rank on overall and show
    patchwise — the table's "best" combo would not be the best one shown.
    `pw_missing` reports rows that had no patchwise metrics and kept overall.
    """
    r = _redis()
    if r is None:
        return {"rows": [], "total": 0, "pw_missing": 0}
    raw = r.lrange(_results_key(job_id), 0, -1)
    rows = _dedupe_by_label([json.loads(x) for x in raw])
    total = len(rows)
    pw_missing = 0
    if patchwise:
        merged = []
        for row in rows:
            pw = row.get("summary_pw")
            if pw:
                merged.append({**row, "summary": {**(row.get("summary") or {}), **pw}})
            else:
                pw_missing += 1
                merged.append(row)
        rows = merged
    sort_rows(rows, sort_key, descending)
    from services.optimizer.summary_workbook import compute_leg_presence
    # Computed over the FULL set, not the page being returned — otherwise a
    # paginated table would gain/lose conditional columns (Midcap, FUT) as the
    # user pages, since which legs appear can differ page to page.
    leg_presence = compute_leg_presence(rows)
    return {
        "rows": rows[offset : offset + limit],
        "total": total,
        "pw_missing": pw_missing,
        "leg_presence": leg_presence,
    }


def sort_rows(rows: List[Dict[str, Any]], sort_key: Optional[str], descending: bool = True) -> None:
    """In-place sort by a summary-dict key. Shared by get_results and the POST
    summary.xlsx export path so the two never implement sorting twice."""
    if not sort_key:
        return

    def _k(row):
        try:
            return float(row.get("summary", {}).get(sort_key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    rows.sort(key=_k, reverse=descending)


def get_all_results(job_id: str) -> List[Dict[str, Any]]:
    r = _redis()
    if r is None:
        return []
    raw = r.lrange(_results_key(job_id), 0, -1)
    return _dedupe_by_label([json.loads(x) for x in raw])


def get_all_results_raw(job_id: str) -> List[Dict[str, Any]]:
    """Every result row, in insertion order, WITHOUT `_dedupe_by_label`.

    Collapsing (combo_label, total_pnl) duplicates is right for the results
    table, but wrong for anything that has to line up against the per-combo
    artifacts on disk: the sweep wrote a tradesheet CSV for EVERY combo, so a
    deduped read leaves the dropped combos' CSVs with no metadata at all. The
    WOW/MOM grid then fell back to raw filenames for those, which also flipped
    their adj_key from "NoAdjustment" to "No Adj" — splitting one adjustment
    into two misaligned column groups. Use this wherever combo_label_safe is
    the join key.
    """
    r = _redis()
    if r is None:
        return []
    return [json.loads(x) for x in r.lrange(_results_key(job_id), 0, -1)]


def get_combo_by_id(job_id: str, combo_id: int) -> Optional[Dict[str, Any]]:
    """
    Return the result row for a specific combo_id (1-indexed integer).
    combo_id is stored as insertion order (done + 1), so row is at index combo_id - 1.
    Falls back to a full scan if the index miss (e.g. due to sorted view).
    """
    r = _redis()
    if r is None:
        return None
    # Fast path: combo_id is 1-indexed insertion order
    raw = r.lindex(_results_key(job_id), combo_id - 1)
    if raw:
        row = json.loads(raw)
        if row.get("combo_id") == combo_id:
            return row
    # Slow fallback: linear scan (handles edge cases)
    all_raw = r.lrange(_results_key(job_id), 0, -1)
    for item in all_raw:
        row = json.loads(item)
        if row.get("combo_id") == combo_id:
            return row
    return None


def maybe_spill_to_parquet(job_id: str) -> Optional[str]:
    """If results > OPTIM_SPILL_THRESHOLD, write Parquet and return path."""
    r = _redis()
    if r is None:
        return None
    n = r.llen(_results_key(job_id))
    if n < OPTIM_SPILL_THRESHOLD:
        return None
    try:
        import pandas as pd

        rows = get_all_results(job_id)
        flat = []
        for row in rows:
            flat_row = {**row.get("combo", {}), **(row.get("summary") or {})}
            flat_row["combo_label"] = row.get("combo_label")
            flat.append(flat_row)
        df = pd.DataFrame(flat)
        os.makedirs(OPTIM_PARQUET_DIR, exist_ok=True)
        path = os.path.join(OPTIM_PARQUET_DIR, f"{job_id}.parquet")
        df.to_parquet(path, index=False)
        r.setex(_parquet_key(job_id), OPTIM_TTL, path)
        return path
    except Exception as e:
        logger.warning("[OPTIM_STORE] parquet spill failed: %s", e)
        return None


def update_result_summaries(
    job_id: str,
    corrected_by_label: Dict[str, Any],
) -> None:
    """Merge corrected metrics into stored result rows, keyed by combo_label_safe."""
    r = _redis()
    if r is None or not corrected_by_label:
        return
    try:
        raw_list = r.lrange(_results_key(job_id), 0, -1)
        if not raw_list:
            return
        updated = []
        for raw in raw_list:
            row = json.loads(raw)
            label = row.get("combo_label_safe", "")
            if label in corrected_by_label:
                row["summary"] = {**(row.get("summary") or {}), **corrected_by_label[label]}
            updated.append(json.dumps(row, default=str))
        pipe = r.pipeline()
        pipe.delete(_results_key(job_id))
        for item in updated:
            pipe.rpush(_results_key(job_id), item)
        pipe.expire(_results_key(job_id), OPTIM_TTL)
        pipe.execute()
        logger.info("[OPTIM_STORE] Updated summaries for %d combos in job %s", len(corrected_by_label), job_id[:8])
    except Exception as e:
        logger.warning("[OPTIM_STORE] update_result_summaries failed: %s", e)


def delete_job(job_id: str) -> None:
    r = _redis()
    if r is None:
        return
    r.delete(_meta_key(job_id), _results_key(job_id), _parquet_key(job_id))


# ── On-disk tradesheet storage ───────────────────────────────────────────────

OPTIM_TRADES_DIR = os.getenv("OPTIMIZE_TRADES_DIR", "/data/cache/optim_trades")


def get_trades_dir(job_id: str) -> str:
    return os.path.join(OPTIM_TRADES_DIR, job_id)


def mark_job_access(job_id: str) -> None:
    """Refresh a job's idle timer whenever the user downloads any of its files.

    prune_trades_dir() and the disk-cleanup cron reap a job by the NEWEST mtime
    in its trades dir. Without this, a job is deleted N days after it was
    CREATED even if the user re-downloaded it today. Touch a `.accessed` marker
    (never the served artifacts — their mtime feeds the resume ETag in
    _file_response) so retention becomes idle-based, not age-based.
    Best-effort: a download must never fail over a bookkeeping touch.
    """
    d = get_trades_dir(job_id)
    if not os.path.isdir(d):
        return  # 0-trade job: nothing on disk to keep alive
    try:
        with open(os.path.join(d, ".accessed"), "w"):
            pass
    except OSError:
        pass


def prune_trades_dir(max_age_days: float = None) -> int:
    """Delete per-combo trade artifacts for jobs older than the retention window.

    This directory had NO eviction of any kind, unlike the ZIP cache which at
    least has a size budget — it reached 67.9 GB across 2,572 jobs and was the
    single largest consumer of the disk (86% full), which is a slow way to break
    every sweep at once. Age-based, whole-job: a job's per-combo CSVs are only
    useful together.

    Deliberately generous at 7 days, and applied per job's NEWEST file, because
    these are also what a ZIP is rebuilt from when its cached archive is gone —
    delete both and a sweep nobody downloaded is unrecoverable. A running job is
    never touched. Returns bytes freed.
    """
    try:
        days = float(max_age_days if max_age_days is not None
                     else os.environ.get("OPTIM_TRADES_MAX_AGE_DAYS", "7"))
    except (TypeError, ValueError):
        days = 7.0
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400

    running: set = set()
    r = _redis()
    if r is not None:
        try:
            for key in r.scan_iter("optim:*:meta"):
                raw = r.get(key)
                if not raw:
                    continue
                if (json.loads(raw) or {}).get("status") == "running":
                    # _redis() sets decode_responses=True, so scan_iter yields
                    # str, not bytes — an unconditional .decode() raised
                    # AttributeError here and the except below swallowed it as
                    # "cannot prove what is live", skipping every prune.
                    if isinstance(key, bytes):
                        key = key.decode()
                    running.add(key.split(":")[1])
        except Exception as exc:
            # Cannot prove what is live -> prune nothing. Deleting a running
            # job's trades mid-sweep loses combos already computed.
            logger.warning("[OPTIM_STORE] trades prune skipped (no job state): %s", exc)
            return 0

    freed = 0
    removed = 0
    try:
        names = os.listdir(OPTIM_TRADES_DIR)
    except OSError:
        return 0
    for job_id in names:
        path = os.path.join(OPTIM_TRADES_DIR, job_id)
        if not os.path.isdir(path) or job_id in running:
            continue
        newest = 0.0
        size = 0
        try:
            for root, _dirs, files in os.walk(path):
                for name in files:
                    try:
                        st = os.stat(os.path.join(root, name))
                    except OSError:
                        continue
                    size += st.st_size
                    if st.st_mtime > newest:
                        newest = st.st_mtime
        except OSError:
            continue
        if newest and newest < cutoff:
            try:
                shutil.rmtree(path)
                freed += size
                removed += 1
            except OSError:
                continue
    if removed:
        logger.info("[OPTIM_STORE] trades pruned: %d jobs older than %.0fd, freed %.1f GB",
                    removed, days, freed / (1024 ** 3))
    return freed


# ── Per-combo WOW/MOM payload: on disk, never in Redis ───────────────────────
# Measured on a real sweep: a stored result row is 16.0 KB, of which wm_pw alone
# is 13.5 KB (84.5%). Keeping it in the Redis row costs ~16 KB/combo, so a
# 60,000-combo sweep would need ~985 MB against a 500 MB maxmemory and die
# mid-run. On disk (gzipped) the row falls back to ~2.5 KB, i.e. ~150 MB at
# 60k, while _prebuild_wow_mom still avoids the slow CSV rebuild.

def _wm_path(job_id: str, combo_label_safe: str) -> str:
    return os.path.join(get_trades_dir(job_id), "wm", f"{combo_label_safe}.json.gz")


def write_combo_wm(job_id: str, combo_label_safe: str, wm_overall, wm_patchwise) -> bool:
    """Persist a combo's WOW/MOM data. Returns True if anything was written."""
    if wm_overall is None and wm_patchwise is None:
        return False
    import gzip
    import json as _json
    try:
        path = _wm_path(job_id, combo_label_safe)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {"wm_overall": wm_overall, "wm_pw": wm_patchwise}
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as fh:
            _json.dump(payload, fh)
        return True
    except Exception as exc:
        logger.warning("[OPTIM_STORE] wm write failed (%s): %s", combo_label_safe, exc)
        return False


def read_combo_wm(job_id: str, combo_label_safe: str, patchwise: bool):
    """Load a combo's stored WOW/MOM data for one variant, or None if absent."""
    import gzip
    import json as _json
    try:
        with gzip.open(_wm_path(job_id, combo_label_safe), "rt", encoding="utf-8") as fh:
            return (_json.load(fh) or {}).get("wm_pw" if patchwise else "wm_overall")
    except (OSError, ValueError):
        return None


def write_combo_tradesheet(
    job_id: str,
    combo_label_safe: str,
    trades_df: Any,
) -> None:
    """Write a single combo's tradesheet DataFrame to CSV on disk."""
    if trades_df is None:
        return
    try:
        if hasattr(trades_df, "empty") and trades_df.empty:
            return
        dirpath = get_trades_dir(job_id)
        os.makedirs(dirpath, exist_ok=True)
        path = os.path.join(dirpath, f"{combo_label_safe}.csv")
        trades_df.to_csv(path, index=False)
    except Exception as exc:
        logger.warning("[OPTIM_STORE] tradesheet write failed (%s): %s", combo_label_safe, exc)


# ── xlsx-write phase probes (OPTIM_PROFILE=1) ────────────────────────────────
# The sweep profiler attributes ~48% of every combo to "xlsx_write", but that
# bucket wraps THREE things: the deferred MAE/MFE enrichment (skipped in the
# combo loop via OPTIMIZE_SKIP_MAE_MFE and paid here), the workbook build, and
# the disk write. build_combo_xlsx measured ~64ms in isolation against 830ms in
# place, so the split must be measured before anything is changed.
_XPROF = os.environ.get("OPTIM_PROFILE", "0").strip().lower() in ("1", "true", "yes")
_xacc: Dict[str, float] = {}
_xn = 0


def _xnow() -> float:
    return time.perf_counter()


def _xmark(bucket: str, t0: float) -> float:
    global _xn
    if not _XPROF:
        return t0
    _xacc[bucket] = _xacc.get(bucket, 0.0) + (time.perf_counter() - t0)
    if bucket == "xlsx_disk":
        _xn += 1
        if _xn % max(1, int(os.environ.get("OPTIM_PROFILE_EVERY", "25"))) == 0:
            tot = sum(_xacc.values()) or 1e-9
            logger.info(
                "[XLSX_PROFILE] pid=%d n=%d avg=%.0fms | %s", os.getpid(), _xn,
                tot / _xn * 1000,
                " ".join(f"{k}={v / _xn * 1000:.0f}ms/{100 * v / tot:.0f}%"
                         for k, v in sorted(_xacc.items(), key=lambda kv: -kv[1])),
            )
    return time.perf_counter()


def write_combo_xlsx(
    job_id: str,
    combo_label_safe: str,
    trades_df: Any,
    summary: Dict[str, Any],
    combo_label: str = "",
    from_date: str = "",
    to_date: str = "",
    index_str: str = "",
    trading_days: Optional[List] = None,
    midcap_legs=None,
    midcap_spot_adjustment=None,
    midcap_symbol: str = "NIFTYMIDCAP100",
    filter_name: str = "",
    filter_segments=None,
    yearly: bool = False,
    rules_sheet=None,
) -> None:
    """Write a single combo's XLSX tradesheet to disk (called per-combo during execution).

    Always enriches MAE/MFE before building the XLSX regardless of
    OPTIMIZE_SKIP_MAE_MFE — that flag only controls the ranking metrics,
    not the download tradesheet.
    """
    if trades_df is None:
        return
    try:
        if hasattr(trades_df, "empty") and trades_df.empty:
            return

        # Enrich MAE/MFE for the download tradesheet.  The optimizer skips this
        # during combo execution (OPTIMIZE_SKIP_MAE_MFE=1) for speed, so we do
        # it here using the feather that's already on disk.
        #
        # SKIP for multi-index combos ("Group Index" column present): those
        # trades already have CORRECT per-leg MAE/MFE from
        # run_multi_index_feature (each leg priced against its OWN index).
        # _compute_mae_mfe_batch takes one blanket `index_str` (the strategy's
        # BASE index) for every row — for a MIDCPNIFTY futures leg on a NIFTY
        # strategy this OVERWRITES the correct value with one computed against
        # NIFTY's own high/low, producing the -85%+ scale-mismatch garbage
        # (MIDCPNIFTY entry ~12,700 vs NIFTY high/low ~23,800). See
        # [[multi-index-fut-mae-mfe-scale-bug]].
        _is_multi_index = hasattr(trades_df, "columns") and "Group Index" in trades_df.columns
        if index_str and trading_days and not _is_multi_index:
            try:
                from services.optimizer.runner import (
                    _compute_mae_mfe_batch,
                    _compute_live_dd_from_mae,
                )
                import pandas as _pd
                _xp = _xnow()
                enriched = _compute_mae_mfe_batch(trades_df, index_str, trading_days)
                if "Trade" in enriched.columns:
                    _pr = enriched.drop_duplicates(subset=["Trade"], keep="first")
                    _agg = _pr[["Trade"]].copy()
                    for _c in ("Cumulative", "Peak", "DD", "%DD", "Net P&L"):
                        if _c in _pr.columns:
                            _agg[_c] = _pr[_c].values
                    enriched = _compute_live_dd_from_mae(enriched, _agg)
                trades_df = enriched
                _xp = _xmark("mae_enrich", _xp)
            except Exception as _mae_exc:
                logger.warning("[OPTIM_STORE] MAE/MFE enrich FAILED (%s): %s", combo_label_safe, _mae_exc)

        from services.optimizer.excel_builder import build_combo_xlsx
        _xp = _xnow()
        xlsx_bytes = build_combo_xlsx(
            trades_df,
            summary,
            combo_label=combo_label,
            from_date=from_date,
            to_date=to_date,
            midcap_legs=midcap_legs,
            midcap_spot_adjustment=midcap_spot_adjustment,
            midcap_symbol=midcap_symbol,
            # Without this the inline workbook had NO "Patch wise" sheet while the
            # finalize rebuild (which passes zip_naming level1) did — so the ZIP
            # fast path would have shipped a workbook one sheet short of the
            # rebuild it replaces. Same source both sides now.
            filter_name=filter_name,
            filter_segments=filter_segments,
            yearly=yearly,
            rules_sheet=rules_sheet,
        )
        dirpath = get_trades_dir(job_id)
        os.makedirs(dirpath, exist_ok=True)
        path = os.path.join(dirpath, f"{combo_label_safe}.xlsx")
        _xp = _xmark("xlsx_build", _xp)
        with open(path, "wb") as fh:
            fh.write(xlsx_bytes)
        _stamp_xlsx_version(job_id)
        _xmark("xlsx_disk", _xp)
    except Exception as exc:
        logger.warning("[OPTIM_STORE] xlsx write failed (%s): %s", combo_label_safe, exc)


def write_combo_xlsx_patchwise(
    job_id: str,
    combo_label_safe: str,
    trades_df: Any,
    summary: Dict[str, Any],
    combo_label: str = "",
    from_date: str = "",
    to_date: str = "",
    index_str: str = "",
    trading_days: Optional[List] = None,
    midcap_legs=None,
    midcap_spot_adjustment=None,
    midcap_symbol: str = "NIFTYMIDCAP100",
    filter_name: str = "",
    filter_segments=None,
    yearly: bool = False,
    rules_sheet=None,
) -> None:
    """Write a combo's PATCHWISE XLSX tradesheet directly from trades_df during
    the run — same builder the finalization/download uses, just fed the in-memory
    trades instead of re-reading the CSV. Written to a `patchwise/` subdir so the
    overall fast-path (which globs top-level *.xlsx) never picks it up. This lets
    the patchwise ZIP be assembled by just zipping pre-built files (seconds)
    instead of rebuilding every combo's XLSX from CSV at download time."""
    if trades_df is None:
        return
    try:
        if hasattr(trades_df, "empty") and trades_df.empty:
            return
        # Enrich MAE/MFE identically to the overall write (same code path).
        # SKIP for multi-index combos — see the identical guard + explanation
        # in write_combo_xlsx above ([[multi-index-fut-mae-mfe-scale-bug]]).
        _is_multi_index = hasattr(trades_df, "columns") and "Group Index" in trades_df.columns
        if index_str and trading_days and not _is_multi_index:
            try:
                from services.optimizer.runner import (
                    _compute_mae_mfe_batch,
                    _compute_live_dd_from_mae,
                )
                _xp = _xnow()
                enriched = _compute_mae_mfe_batch(trades_df, index_str, trading_days)
                if "Trade" in enriched.columns:
                    _pr = enriched.drop_duplicates(subset=["Trade"], keep="first")
                    _agg = _pr[["Trade"]].copy()
                    for _c in ("Cumulative", "Peak", "DD", "%DD", "Net P&L"):
                        if _c in _pr.columns:
                            _agg[_c] = _pr[_c].values
                    enriched = _compute_live_dd_from_mae(enriched, _agg)
                trades_df = enriched
                _xp = _xmark("mae_enrich", _xp)
            except Exception as _mae_exc:
                logger.warning("[OPTIM_STORE] pw MAE/MFE enrich FAILED (%s): %s", combo_label_safe, _mae_exc)

        from services.optimizer.excel_builder import build_combo_xlsx
        _xp = _xnow()
        xlsx_bytes = build_combo_xlsx(
            trades_df,
            summary,
            combo_label=combo_label,
            from_date=from_date,
            to_date=to_date,
            midcap_legs=midcap_legs,
            midcap_spot_adjustment=midcap_spot_adjustment,
            midcap_symbol=midcap_symbol,
            filter_name=filter_name,
            patchwise=True,
            filter_segments=filter_segments,
            yearly=yearly,
            rules_sheet=rules_sheet,
        )
        dirpath = os.path.join(get_trades_dir(job_id), "patchwise")
        os.makedirs(dirpath, exist_ok=True)
        path = os.path.join(dirpath, f"{combo_label_safe}.xlsx")
        _xp = _xmark("xlsx_build", _xp)
        with open(path, "wb") as fh:
            fh.write(xlsx_bytes)
        _stamp_xlsx_version(job_id)
        _xmark("xlsx_disk", _xp)
    except Exception as exc:
        logger.warning("[OPTIM_STORE] pw xlsx write failed (%s): %s", combo_label_safe, exc)


def write_summary_csv(job_id: str, rows: List[Dict[str, Any]]) -> None:
    """Write master summary CSV (one row per combo) to disk."""
    if not rows:
        return
    try:
        import pandas as pd

        flat = []
        for row in rows:
            flat_row = {
                "combo_id": row.get("combo_id"),
                "combo_label": row.get("combo_label"),
            }
            flat_row.update(row.get("summary") or {})
            flat.append(flat_row)
        df = pd.DataFrame(flat)
        dirpath = get_trades_dir(job_id)
        os.makedirs(dirpath, exist_ok=True)
        df.to_csv(os.path.join(dirpath, "summary.csv"), index=False)
    except Exception as exc:
        logger.warning("[OPTIM_STORE] summary CSV write failed: %s", exc)


def write_run_config(
    job_id: str,
    method: str,
    objective: str,
    param_specs: list,
    base_payload: dict,
    *,
    sample_n: int | None = None,
    algorithm: str | None = None,
    total_combos: int | None = None,
) -> None:
    """Write run_config.csv to the job's trades directory."""
    try:
        import csv
        dirpath = get_trades_dir(job_id)
        os.makedirs(dirpath, exist_ok=True)
        path = os.path.join(dirpath, "run_config.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            # Run-level metadata block
            w.writerow(["# Run Configuration"])
            w.writerow(["Method", method])
            w.writerow(["Objective", objective])
            w.writerow(["Total Combinations", total_combos or ""])
            if sample_n is not None:
                w.writerow(["Sample N", sample_n])
            if algorithm:
                w.writerow(["Algorithm", algorithm])
            from_date = base_payload.get("from_date") or base_payload.get("date_from", "")
            to_date = base_payload.get("to_date") or base_payload.get("date_to", "")
            w.writerow(["From Date", from_date])
            w.writerow(["To Date", to_date])
            w.writerow(["Symbol", base_payload.get("symbol", "")])
            w.writerow([])
            # Parameter sweep specs
            w.writerow(["# Parameter Specs"])
            w.writerow(["Parameter", "Min", "Max", "Step", "Values", "Type"])
            for spec in param_specs:
                ptype = spec.get("type", "range")
                w.writerow([
                    spec.get("path", spec.get("label", "")),
                    spec.get("min", ""),
                    spec.get("max", ""),
                    spec.get("step", ""),
                    "|".join(str(v) for v in spec.get("values", [])) if spec.get("values") else "",
                    ptype,
                ])
    except Exception as exc:
        logger.warning("[OPTIM_STORE] run_config write failed: %s", exc)


def delete_job_trades(job_id: str) -> None:
    """Remove the on-disk tradesheet directory for a job."""
    import shutil

    dirpath = get_trades_dir(job_id)
    if os.path.isdir(dirpath):
        try:
            shutil.rmtree(dirpath, ignore_errors=True)
        except Exception as exc:
            logger.warning("[OPTIM_STORE] trade dir delete failed: %s", exc)


# ── Live-optim registry (drives DYNAMIC worker/memory allocation) ──────────
# How many optimize jobs are CURRENTLY in their compute phase, box-wide. The
# parallel runner divides the "solo" (full-box) parallelism by this count, so:
#   1 optim live  -> full box  (fast single run)
#   2 optims live -> half each (both fit, no OOM, no CPU thrash)
# Stored as a Redis hash {job_id: heartbeat_epoch}; entries older than
# _ACTIVE_STALE_SEC (a crashed/killed job that never deregistered) are pruned
# on read so they can't wedge the divisor high forever.
_ACTIVE_KEY = "algotest:optim:active"       # legacy global key (kept for cleanup)
_ACTIVE_KEY_PREFIX = "algotest:optim:active:"  # PER-NODE: :{node_id|local}
_ACTIVE_STALE_SEC = 5 * 60  # 5min — safe because runner.py refreshes (touch_active_optim)
# every ~60s while a job is genuinely alive, regardless of how long the whole
# sweep takes; a job only goes stale here if its process actually died (crash,
# SIGKILL, container restart) and stopped refreshing. Previously 3h with NO
# heartbeat refresh at all, so a single crashed/killed job could throttle every
# other concurrent optim on this node to a fraction of its normal parallelism
# for up to 3 hours.


def _active_key(node_id: Optional[str]) -> str:
    # Per-NODE live-optim set. The dynamic parallelism divisor must count only
    # optims running on the SAME box, never optims on other LAN nodes (their
    # CPU/RAM is unrelated to this box's free capacity). Default "local" = this box.
    return _ACTIVE_KEY_PREFIX + (node_id or "local")


def register_active_optim(job_id: str, node_id: Optional[str] = None) -> int:
    """Mark this job as computing NOW on `node_id` (default: local, this box) and
    return the live optim count for THAT node (incl. self). Best-effort: if Redis
    is unavailable, returns 1 so the caller falls back to full solo parallelism
    (safe — a single job never over-allocates)."""
    r = _redis()
    if r is None:
        return 1
    try:
        now = time.time()
        key = _active_key(node_id)
        r.hset(key, job_id, now)
        # Prune stale entries (crashed jobs) before counting.
        stale = []
        for jid, ts in (r.hgetall(key) or {}).items():
            try:
                if now - float(ts) > _ACTIVE_STALE_SEC:
                    stale.append(jid)
            except (TypeError, ValueError):
                stale.append(jid)
        if stale:
            r.hdel(key, *stale)
        return max(1, r.hlen(key))
    except Exception as exc:
        logger.warning("[OPTIM_STORE] register_active_optim failed: %s", exc)
        return 1


def touch_active_optim(job_id: str, node_id: Optional[str] = None) -> None:
    """Refresh this job's last-seen timestamp in the live-optim registry, without
    the prune-scan/count overhead of register_active_optim. Call periodically
    (e.g. every ~60s from a heartbeat thread) while a job is genuinely still
    computing, so _ACTIVE_STALE_SEC can be kept short — a job that misses its
    heartbeats (crashed, killed, container restarted) goes stale quickly instead
    of throttling other jobs' parallelism for hours."""
    r = _redis()
    if r is None:
        return
    try:
        # REFRESH ONLY — never (re)create. A plain HSET resurrects an entry that
        # was deliberately removed: a job killed before its cleanup ran can leave
        # an orphaned heartbeat thread, and that thread's touch put the dead job
        # straight back into the registry ~60s after every removal. The ghost then
        # counted as a live claimant forever, halving the fork width of the jobs
        # that ARE running (observed: a real sweep pinned at P=6 instead of 12,
        # 2.45 combos/s instead of 3.75). HEXISTS-guarded, so a genuine job (which
        # register_active_optim created) still refreshes normally.
        key = _active_key(node_id)
        if r.hexists(key, job_id):
            r.hset(key, job_id, time.time())
    except Exception as exc:
        logger.debug("[OPTIM_STORE] touch_active_optim failed: %s", exc)


def is_active_optim(job_id: str, node_id: Optional[str] = None) -> bool:
    """Is this job currently registered as a LIVE optim on `node_id`?

    Lets callers tell a genuinely-running job from one frozen at
    status=running by a crash or worker restart — the registry entry is
    refreshed by a 60s heartbeat and self-expires (_ACTIVE_STALE_SEC), so a
    dead job stops being 'active' on its own. Errors return False: treating an
    unreachable registry as 'not running' is the safe direction here, since the
    alternative blocks recovery of a job nobody can restart.
    """
    try:
        r = _redis()
        if r is None:
            return False
        return bool(r.hexists(_active_key(node_id), job_id))
    except Exception:
        return False


def unregister_active_optim(job_id: str, node_id: Optional[str] = None) -> None:
    """Remove this job from its node's live set once its compute phase ends."""
    r = _redis()
    if r is None:
        return
    try:
        r.hdel(_active_key(node_id), job_id)
    except Exception as exc:
        logger.warning("[OPTIM_STORE] unregister_active_optim failed: %s", exc)


def active_optim_count(node_id: Optional[str] = None) -> int:
    """Current live optim count for `node_id` (default: local), pruned."""
    r = _redis()
    if r is None:
        return 0
    try:
        now = time.time()
        cnt = 0
        for _jid, ts in (r.hgetall(_active_key(node_id)) or {}).items():
            try:
                if now - float(ts) <= _ACTIVE_STALE_SEC:
                    cnt += 1
            except (TypeError, ValueError):
                pass
        return cnt
    except Exception:
        return 0
