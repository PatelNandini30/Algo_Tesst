"""
Celery tasks for background strategy execution.
"""
from .celery_app import celery_app
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="src.jobs.tasks.execute_strategy_async")
def execute_strategy_async(self, strategy_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute a strategy asynchronously in the background.
    
    Args:
        self: Celery task instance (bound)
        strategy_name: Name of the strategy to execute
        parameters: Strategy parameters
        
    Returns:
        Dictionary with execution results
    """
    try:
        # Update task state to STARTED
        self.update_state(
            state="STARTED",
            meta={
                "strategy": strategy_name,
                "parameters": parameters,
                "status": "Initializing..."
            }
        )
        
        # Import here to avoid circular dependencies
        from src.data.provider import DataProvider
        from src.api.main import STRATEGIES
        
        # Get strategy instance
        if strategy_name not in STRATEGIES:
            raise ValueError(f"Strategy '{strategy_name}' not found")
        
        strategy = STRATEGIES[strategy_name]
        
        # Validate parameters
        is_valid, error_msg = strategy.validate_parameters(parameters)
        if not is_valid:
            raise ValueError(f"Invalid parameters: {error_msg}")
        
        # Update state to PROCESSING
        self.update_state(
            state="PROCESSING",
            meta={
                "strategy": strategy_name,
                "parameters": parameters,
                "status": "Executing strategy..."
            }
        )
        
        # Initialize data provider
        data_provider = DataProvider()
        
        # Execute strategy
        result = strategy.execute(data_provider, parameters)
        
        # Check if execution was successful
        if not result.success:
            raise Exception(result.metadata.get("error", "Strategy execution failed"))
        
        # Return success result
        return {
            "status": "SUCCESS",
            "strategy": strategy_name,
            "parameters": parameters,
            "result": {
                "data": result.data,
                "metadata": result.metadata,
                "execution_time_ms": result.execution_time_ms,
                "row_count": result.row_count
            }
        }
        
    except Exception as e:
        logger.error(f"Error executing strategy {strategy_name}: {str(e)}")
        
        # Update state to FAILURE
        self.update_state(
            state="FAILURE",
            meta={
                "strategy": strategy_name,
                "parameters": parameters,
                "error": str(e),
                "status": "Failed"
            }
        )
        
        # Re-raise to mark task as failed
        raise


@celery_app.task(name="src.jobs.tasks.cleanup_old_results")
def cleanup_old_results():
    """
    Periodic task to cleanup old execution results.
    Run this task daily to clean up old cached results.
    """
    try:
        from src.cache import get_cache
        
        cache = get_cache()
        stats = cache.get_cache_stats()
        
        logger.info(f"Cache cleanup: {stats.get('total_cached_entries', 0)} entries")
        
        # Redis TTL handles automatic cleanup, but we can log stats
        return {
            "status": "SUCCESS",
            "message": "Cache stats retrieved",
            "stats": stats
        }
        
    except Exception as e:
        logger.error(f"Error during cleanup: {str(e)}")
        return {
            "status": "FAILURE",
            "error": str(e)
        }
