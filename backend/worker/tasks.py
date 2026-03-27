"""
Celery tasks for background processing.
"""
import sys
import os
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
    try:
        self.update_state(state='PROCESSING', meta={'status': 'Running AlgoTest backtest'})
        from services.algotest_job import execute_algotest_job
        result = execute_algotest_job(params)
        return _sanitize_result(result)
    except Exception as e:
        return _sanitize_result({
            'status': 'error',
            'message': str(e)
        })


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
