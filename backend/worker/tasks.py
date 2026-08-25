"""
Celery tasks for background processing.
"""
import sys
import os
import time
import logging
from datetime import datetime
from pathlib import Path

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from worker.celery import celery_app
from services.upload_config import DATA_TYPE_METHODS
from database import DATABASE_URL
from migrate_data import Migrator
from sqlalchemy import create_engine, text
import pandas as pd

logger = logging.getLogger(__name__)


@celery_app.task(bind=True)
def run_backtest_task(self, params: dict):
    """
    Run backtest in background.
    
    Args:
        params: Backtest parameters dict
        
    Returns:
        dict with results or error
    """
    try:
        from engines.generic_multi_leg import run_generic_multi_leg
        
        # Update task state
        self.update_state(state='PROCESSING', meta={'status': 'Running backtest...'})
        
        # Run the backtest
        df, summary, pivot = run_generic_multi_leg(params)
        
        return {
            'status': 'completed',
            'trades': df.to_dict('records') if not df.empty else [],
            'summary': summary,
            'pivot': pivot
        }
    except Exception as e:
        return {
            'status': 'failed',
            'error': str(e)
        }


@celery_app.task(bind=True)
def run_algotest_job(self, params: dict):
    """Execute AlgoTest backtest via shared service."""
    # Admission gate: wait for a memory slot before the engine runs so concurrent
    # heavy jobs cannot overcommit RAM (no-OOM). Pure gating — the backtest math
    # below is unchanged. See services/memory_gate.py.
    from services import memory_gate, node_registry
    rid = self.request.id or ""
    client_ip = str((params or {}).get("_client_ip") or "unknown")
    node_id = (params or {}).get("node_id") or None
    logger.info("[BACKTEST] job %s started from ip=%s node=%s", rid[:8], client_ip, node_id or "local")
    if node_id:
        node_registry.record_job_node(rid, node_id)
    memory_gate.acquire(
        rid,
        # #2 dynamic cost: scale reservation by this backtest's date span.
        memory_gate.cost_for_job("backtest", params),
        on_wait=lambda: self.update_state(
            state='PROCESSING', meta={'status': 'queued: waiting for memory budget', 'client_ip': client_ip}
        ),
        node_id=node_id,
        kind="backtest",
    )
    try:
        self.update_state(state='PROCESSING', meta={'status': 'Running AlgoTest backtest', 'client_ip': client_ip})
        from services.algotest_job import execute_algotest_job
        result = execute_algotest_job(params)
        safe_result = _sanitize_result(result)
        if isinstance(safe_result, dict):
            safe_result["client_ip"] = client_ip
        return safe_result
    except Exception as e:
        return _sanitize_result({
            'status': 'error',
            'message': str(e),
            'client_ip': client_ip,
        })
    finally:
        memory_gate.release(rid, node_id=node_id)


@celery_app.task(bind=True)
def run_optimize_job(self, spec: dict):
    """Execute an optimization sweep.

    Args:
        spec: dict with keys
            base_payload, param_specs, method, sample_n, objective,
            algorithm, seed
    """
    # Admission gate (no-OOM): reserve the optimize memory budget before the
    # sweep starts; if busy the job waits (queued) instead of overcommitting RAM.
    from services import memory_gate, node_registry
    rid = self.request.id or ""
    client_ip = str((spec or {}).get("client_ip") or "unknown")
    node_id = (spec or {}).get("node_id") or None
    logger.info("[OPTIM] job %s started from ip=%s node=%s", rid[:8], client_ip, node_id or "local")
    if node_id:
        node_registry.record_job_node(rid, node_id)
    # Register as live BEFORE the gate, not after it. A running sweep re-splits its
    # pool from this same registry, so a job that only appears once admitted is
    # invisible to the job holding the memory: A stays at P=6 and frees nothing,
    # while B waits on RAM only A's downshift can release — each waiting on the
    # other. Counting the waiter makes A drop to P=3 at its next batch, and that
    # is what frees the RAM B needs. run_optimization re-registers (idempotent);
    # it and the finally below unregister, and the registry self-expires
    # (_ACTIVE_STALE_SEC), so an abandoned wait can't pin the divisor.
    #
    # ...but only for the optims that are actually ALLOWED to run concurrently.
    # OPTIMIZE_MAX_CONCURRENT (default 2) caps how many share the box. Beyond
    # that a job waits HERE — before registering and before reserving any memory
    # — so it is completely inert: it holds no budget and does not appear in the
    # divisor, leaving the running sweeps at their full width. Registering every
    # arrival made a 3rd request shrink both running jobs to P=1 (observed:
    # "live_optims=3" with 4 GB free), which is the opposite of what queueing is
    # for. The two admitted jobs still register before the gate, so the
    # cooperative downshift that frees RAM between them is unchanged.
    try:
        _max_optims = max(1, int(os.environ.get("OPTIMIZE_MAX_CONCURRENT", "2")))
    except (TypeError, ValueError):
        _max_optims = 2
    try:
        from services.optimizer import result_store as _rs_gate
        if _rs_gate.active_optim_count(node_id) >= _max_optims:
            # Too many optims already running — put this task back in the Redis
            # queue and exit immediately. The job holds ZERO memory while waiting
            # (it hasn't loaded any data yet) and will be retried after a delay.
            # This is strictly better than the old spin-wait (time.sleep loop
            # inside the worker): the old loop held a Celery worker slot and kept
            # the job memory-resident even though it was doing nothing.
            logger.info(
                "[OPTIM] job %s requeued — %d/%d optimizer slots in use (ip=%s)",
                rid[:8], _rs_gate.active_optim_count(node_id), _max_optims, client_ip,
            )
            self.update_state(
                state='PROCESSING',
                meta={'status': f'queued: {_max_optims} optimizations already running',
                      'client_ip': client_ip},
            )
            # countdown=30 keeps responsiveness; max_retries=720 = 6h patience.
            raise self.retry(countdown=30, max_retries=720)
        _rs_gate.register_active_optim(rid, node_id)
    except self.MaxRetriesExceededError:
        return _sanitize_result({'status': 'error', 'message': 'timed out waiting for optimizer slot', 'client_ip': client_ip})
    except Exception as _gate_exc:
        from celery.exceptions import Retry as _CeleryRetry
        if isinstance(_gate_exc, _CeleryRetry):
            raise  # let Celery handle the requeue
        logger.warning("[OPTIM] slot-gate error (%s) — proceeding anyway", _gate_exc)
    memory_gate.acquire(
        rid,
        # #2 dynamic cost: scale reservation by this optim's date span.
        memory_gate.cost_for_job("optimize", (spec or {}).get("base_payload")),
        on_wait=lambda: self.update_state(
            state='PROCESSING', meta={'status': 'queued: waiting for memory budget', 'client_ip': client_ip}
        ),
        node_id=node_id,
        kind="optimize",
    )
    try:
        from services.optimizer.runner import run_optimization
        self.update_state(state='PROCESSING', meta={'status': 'Starting optimization', 'client_ip': client_ip})
        result = run_optimization(
            job_id=self.request.id,
            base_payload=spec.get('base_payload') or {},
            client_ip=client_ip,
            param_specs=spec.get('param_specs') or [],
            method=spec.get('method') or 'exhaustive',
            sample_n=spec.get('sample_n'),
            objective=spec.get('objective') or 'total_pnl',
            algorithm=spec.get('algorithm'),
            seed=spec.get('seed'),
            parallelism=spec.get('parallelism'),
            zip_naming=spec.get('zip_naming'),
            auto_download=bool(spec.get('auto_download')),
            node_id=node_id,
            resume=bool(spec.get('resume')),
        )
        # Pre-build the tradesheets ZIP and wait for it to finish so the
        # user gets an instant download instead of a progress bar.
        try:
            import time as _time
            import requests as _req
            _backend_base = os.environ.get("BACKEND_BASE_URL", "http://backend:8000")
            _url = f'{_backend_base}/api/optimize/jobs/{self.request.id}/tradesheets.zip'
            _req.get(_url, timeout=10)          # trigger the build (returns 202)
            _deadline = _time.time() + 20 * 60  # wait up to 20 minutes
            while _time.time() < _deadline:
                _time.sleep(3)
                _r = _req.get(_url, timeout=10)
                if _r.status_code == 200:
                    break                       # ZIP is ready
        except Exception:
            pass  # Non-critical — ZIP will still build on-demand if this fails
        safe_result = _sanitize_result(result)
        if isinstance(safe_result, dict):
            safe_result["client_ip"] = client_ip
        return safe_result
    except Exception as e:
        return _sanitize_result({'status': 'error', 'message': str(e), 'client_ip': client_ip})
    finally:
        memory_gate.release(rid, node_id=node_id)
        # Mirror of the pre-gate register above: a job that failed or was revoked
        # while still WAITING never reached run_optimization's own unregister, and
        # a stale entry would keep every other sweep throttled to a narrower pool.
        try:
            _rs_gate.unregister_active_optim(rid, node_id)
        except Exception:
            pass


@celery_app.task(bind=True)
def warm_backtest_cache_task(self, params: dict):
    """Warm bulk and native lookup caches inside the backtest worker process."""
    try:
        import time

        t0 = time.perf_counter()
        index = (params or {}).get('index') or (params or {}).get('symbol') or 'NIFTY'
        from_date = (params or {}).get('from_date') or (params or {}).get('date_from')
        to_date = (params or {}).get('to_date') or (params or {}).get('date_to')
        if not from_date or not to_date:
            return {'status': 'skipped', 'message': 'Missing from_date or to_date'}

        from base import bulk_load_options
        from services.algotest_job import (
            _build_fast_lookup_from_bulk,
            _normalize_cache_date,
            _should_build_fast_lookup,
        )

        from_date = _normalize_cache_date(from_date)
        to_date = _normalize_cache_date(to_date)

        # Do not spend a full option-table load on symbols that cannot produce
        # a usable backtest.  Several equities have option rows but no
        # underlying spot series or expiry calendar; warming them only creates
        # a large DB read followed by a guaranteed missing-spot.feather error.
        try:
            from repositories.market_data_repository import MarketDataRepository
            from database import engine as _db_engine
            _repo = MarketDataRepository(_db_engine)
            _spot = _repo.get_spot_data(index, from_date, to_date)
            _expiry = _repo.get_expiry_data(index, "weekly")
            if _spot is None or _spot.empty or _expiry is None or _expiry.empty:
                return {
                    'status': 'skipped',
                    'message': f'Skipping cache warm for {index}: spot or expiry data is unavailable',
                    'stats': {'options_rows': 0, 'spot_rows': len(_spot) if _spot is not None else 0,
                              'expiry_rows': len(_expiry) if _expiry is not None else 0},
                }
        except Exception as exc:
            # A preflight failure must not turn into a large speculative DB
            # load. Real backtests perform their normal authoritative loading.
            logger.warning('[WARM] preflight failed for %s; skipping warm: %s', index, exc)
            return {'status': 'skipped', 'message': f'Warm preflight unavailable for {index}'}

        self.update_state(state='PROCESSING', meta={'status': 'Warming worker data cache'})
        stats = bulk_load_options(index, from_date, to_date)
        if isinstance(stats, dict) and (stats.get('options_rows') or 0) > 0:
            # A genuine DB reload happened (not just a feather-cache hit) — bump
            # the data-version token so the Redis result cache stops serving
            # tradesheets computed against the pre-warm data.
            try:
                from services.backtest_cache import bump_data_version
                bump_data_version()
            except Exception:
                pass
        fast_lookup_built = False
        _rust_active = False
        try:
            from services import rust_fast_path as _rf
            _rust_active = _rf.is_available() and _rf._loaded_cache_key is not None
        except Exception:
            pass
        if _rust_active or _should_build_fast_lookup(params or {}, from_date, to_date):
            self.update_state(state='PROCESSING', meta={'status': 'Warming worker lookup cache'})
            _build_fast_lookup_from_bulk(index, from_date, to_date)
            fast_lookup_built = True
        # Keep the FUTIDX feather (Rust futures pricing source for mixed
        # options+futures strategies) fresh alongside the options cache. This
        # rebuilds the feather from the DB only when the DB signature changed
        # (row count / max date) — a no-op otherwise. Best-effort; a failure
        # here must never block the options cache warm.
        futures_feather_ready = False
        try:
            from services.futures_cache_store import ensure_futures_loaded
            futures_feather_ready = bool(ensure_futures_loaded(index))
        except Exception:
            pass
        return {
            'status': 'ready',
            'message': f'Worker cache warmed for {index} {from_date} to {to_date}',
            'elapsed_seconds': round(time.perf_counter() - t0, 3),
            'fast_lookup_built': fast_lookup_built,
            'futures_feather_ready': futures_feather_ready,
            'stats': stats,
        }
    except Exception as e:
        return {
            'status': 'error',
            'message': str(e),
        }


def _sanitize_result(value):
    """Convert Celery result to JSON-safe structure."""
    import pandas as pd
    import numpy as np
    
    try:
        if isinstance(value, pd.DataFrame):
            return value.to_dict('records')
        if isinstance(value, pd.Series):
            return value.to_dict()
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, (np.integer, np.floating)):
            return value.item()
        
        if isinstance(value, dict):
            return {k: _sanitize_result(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_sanitize_result(item) for item in value]
        
        return value
    except Exception:
        return value


@celery_app.task(bind=True)
def load_data_task(self, data_type: str, source: str):
    """
    Load data from CSV to PostgreSQL in background.
    
    Args:
        data_type: 'option', 'spot', or 'expiry'
        source: Source path
        
    Returns:
        dict with status
    """
    try:
        from migrate_data import migrate_option_data, migrate_spot_data, migrate_expiry_data
        
        self.update_state(state='PROCESSING', meta={'status': f'Loading {data_type} data...'})
        
        if data_type == 'option':
            migrate_option_data()
        elif data_type == 'spot':
            migrate_spot_data()
        elif data_type == 'expiry':
            migrate_expiry_data()
        
        return {'status': 'completed', 'data_type': data_type}
    except Exception as e:
        return {'status': 'failed', 'error': str(e)}


@celery_app.task(bind=True)
def migrate_csv_task(self, temp_path: str, data_type: str, force: bool = False):
    """Migrate an uploaded CSV via Migrator and delete the temp file."""
    normalized = data_type.strip().lower()
    method_name = DATA_TYPE_METHODS.get(normalized)
    if method_name is None:
        raise ValueError(f"Unknown data type for migration: {data_type}")

    self.update_state(state='PROCESSING', meta={'status': f'Migrating {normalized}', 'progress': 0})
    migrator = Migrator(force=force)
    import_fn = getattr(migrator, method_name)

    try:
        result = import_fn(Path(temp_path))
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    if result is None:
        result = {}
    result.setdefault('status', 'completed')
    return result


@celery_app.task
def health_check():
    """Simple health check task."""
    try:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {'status': 'healthy', 'database': 'connected'}
    except Exception as e:
        return {'status': 'unhealthy', 'error': str(e)}
