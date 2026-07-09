"""
Celery tasks for background processing.
"""
import sys
import os
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
        return {
            'status': 'ready',
            'message': f'Worker cache warmed for {index} {from_date} to {to_date}',
            'elapsed_seconds': round(time.perf_counter() - t0, 3),
            'fast_lookup_built': fast_lookup_built,
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
