"""
Celery tasks for background processing.
"""
import sys
import os
from datetime import datetime

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from worker.celery import celery_app
from database import DATABASE_URL
from sqlalchemy import create_engine, text


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
