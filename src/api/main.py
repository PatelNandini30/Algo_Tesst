"""
FastAPI Application for NSE Options Strategy Execution
"""
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
import sqlite3
import json
import hashlib
from datetime import datetime

from src.strategies.base import StrategyInterface
from src.strategies.call_sell_future_buy import CallSellFutureBuyStrategy
from src.strategies.call_sell_future_buy_t1 import CallSellFutureBuyT1Strategy
from src.strategies.call_sell_future_buy_t2 import CallSellFutureBuyT2Strategy
from src.data.provider import DataProvider
from src.cache import get_cache
from src.monitoring import get_metrics_collector
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="NSE Options Strategy API",
    description="API for executing NSE options trading strategies with caching and async execution",
    version="2.0.0"
)

# CORS middleware for web frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize data provider
data_provider = DataProvider()

# Initialize cache (optional - will fail gracefully if Redis not available)
cache = None
try:
    cache = get_cache(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        db=int(os.getenv("REDIS_DB", 0))
    )
    logger.info("Redis cache initialized successfully")
except Exception as e:
    logger.warning(f"Redis cache not available: {e}. Running without cache.")

# Initialize metrics collector
metrics = get_metrics_collector()

# Strategy registry
STRATEGIES: Dict[str, StrategyInterface] = {
    "call_sell_future_buy_weekly": CallSellFutureBuyStrategy(),
    "call_sell_future_buy_t1": CallSellFutureBuyT1Strategy(),
    "call_sell_future_buy_t2": CallSellFutureBuyT2Strategy()
}


# Pydantic Models
class StrategyInfo(BaseModel):
    name: str
    description: str
    version: str
    parameter_schema: List[Dict[str, Any]]


class ExecuteRequest(BaseModel):
    strategy_name: str = Field(..., description="Name of strategy to execute")
    parameters: Dict[str, Any] = Field(..., description="Strategy parameters")
    user_id: str = Field(default="system", description="User ID")
    use_cache: bool = Field(default=True, description="Use cached results if available")
    async_execution: bool = Field(default=False, description="Execute asynchronously in background")


class ExecutionStatus(BaseModel):
    execution_id: int
    strategy_name: str
    status: str
    started_at: str
    completed_at: Optional[str]
    duration_ms: Optional[int]
    row_count: Optional[int]
    error_message: Optional[str]
    cached: Optional[bool] = False
    job_id: Optional[str] = None


class ExecutionResult(BaseModel):
    execution_id: int
    status: str
    data: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    row_count: int


# Database helper functions
def get_db_connection():
    """Get database connection"""
    return sqlite3.connect("bhavcopy_data.db")


def get_strategy_id(strategy_name: str) -> Optional[int]:
    """Get strategy ID from registry"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM strategy_registry WHERE name = ?", (strategy_name,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None


def register_strategy(strategy: StrategyInterface) -> int:
    """Register strategy in database"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Convert parameter schema to JSON
    param_schema = [
        {
            "name": p.name,
            "type": p.type,
            "required": p.required,
            "default": p.default,
            "min_value": p.min_value,
            "max_value": p.max_value,
            "options": p.options,
            "description": p.description
        }
        for p in strategy.get_parameter_schema()
    ]
    
    cursor.execute("""
        INSERT OR REPLACE INTO strategy_registry (name, description, version, parameter_schema)
        VALUES (?, ?, ?, ?)
    """, (
        strategy.get_name(),
        strategy.get_description(),
        strategy.get_version(),
        json.dumps(param_schema)
    ))
    
    strategy_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return strategy_id


def create_execution_record(strategy_id: int, params: Dict[str, Any], user_id: str) -> int:
    """Create execution record in database"""
    params_json = json.dumps(params, sort_keys=True)
    params_hash = hashlib.md5(params_json.encode()).hexdigest()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO execution_runs (strategy_id, parameters_hash, parameters_json, status, user_id)
        VALUES (?, ?, ?, 'running', ?)
    """, (strategy_id, params_hash, params_json, user_id))
    
    execution_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return execution_id


def update_execution_record(execution_id: int, status: str, duration_ms: int, error_msg: Optional[str] = None):
    """Update execution record"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE execution_runs
        SET status = ?, completed_at = CURRENT_TIMESTAMP, duration_ms = ?, error_message = ?
        WHERE id = ?
    """, (status, duration_ms, error_msg, execution_id))
    conn.commit()
    conn.close()


def save_execution_result(execution_id: int, result_data: List[Dict], metadata: Dict, row_count: int):
    """Save execution result"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO execution_results (execution_id, result_data, row_count, metadata)
        VALUES (?, ?, ?, ?)
    """, (execution_id, json.dumps(result_data), row_count, json.dumps(metadata)))
    conn.commit()
    conn.close()


# API Endpoints
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@app.get("/api/v1/strategies", response_model=List[StrategyInfo])
async def list_strategies():
    """Get list of available strategies"""
    strategies_info = []
    
    for strategy_name, strategy in STRATEGIES.items():
        # Ensure strategy is registered
        strategy_id = get_strategy_id(strategy_name)
        if not strategy_id:
            register_strategy(strategy)
        
        param_schema = [
            {
                "name": p.name,
                "type": p.type,
                "required": p.required,
                "default": p.default,
                "min_value": p.min_value,
                "max_value": p.max_value,
                "options": p.options,
                "description": p.description
            }
            for p in strategy.get_parameter_schema()
        ]
        
        strategies_info.append(StrategyInfo(
            name=strategy.get_name(),
            description=strategy.get_description(),
            version=strategy.get_version(),
            parameter_schema=param_schema
        ))
    
    return strategies_info


@app.get("/api/v1/strategies/{strategy_name}", response_model=StrategyInfo)
async def get_strategy_details(strategy_name: str):
    """Get details of a specific strategy"""
    if strategy_name not in STRATEGIES:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_name}' not found")
    
    strategy = STRATEGIES[strategy_name]
    
    param_schema = [
        {
            "name": p.name,
            "type": p.type,
            "required": p.required,
            "default": p.default,
            "min_value": p.min_value,
            "max_value": p.max_value,
            "options": p.options,
            "description": p.description
        }
        for p in strategy.get_parameter_schema()
    ]
    
    return StrategyInfo(
        name=strategy.get_name(),
        description=strategy.get_description(),
        version=strategy.get_version(),
        parameter_schema=param_schema
    )


@app.post("/api/v1/execute", response_model=ExecutionStatus)
async def execute_strategy(request: ExecuteRequest, background_tasks: BackgroundTasks):
    """Execute a strategy with given parameters (with caching and async support)"""
    if request.strategy_name not in STRATEGIES:
        raise HTTPException(status_code=404, detail=f"Strategy '{request.strategy_name}' not found")
    
    strategy = STRATEGIES[request.strategy_name]
    
    # Validate parameters
    is_valid, error_msg = strategy.validate_parameters(request.parameters)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Invalid parameters: {error_msg}")
    
    # Check cache if enabled
    cached_result = None
    if request.use_cache and cache:
        try:
            cached_result = cache.get_cached_result(request.strategy_name, request.parameters)
            if cached_result:
                logger.info(f"Cache HIT for {request.strategy_name}")
                
                # Record metrics
                metrics.record_execution(
                    request.strategy_name,
                    cached_result.get("execution_time_ms", 0),
                    True,
                    cached=True
                )
                
                # Return cached result immediately
                return ExecutionStatus(
                    execution_id=cached_result.get("execution_id", 0),
                    strategy_name=request.strategy_name,
                    status="completed",
                    started_at=cached_result.get("started_at", datetime.now().isoformat()),
                    completed_at=cached_result.get("completed_at", datetime.now().isoformat()),
                    duration_ms=cached_result.get("execution_time_ms", 0),
                    row_count=cached_result.get("row_count", 0),
                    error_message=None,
                    cached=True
                )
        except Exception as e:
            logger.warning(f"Cache error: {e}. Proceeding without cache.")
    
    # Handle async execution
    if request.async_execution:
        try:
            from src.jobs.tasks import execute_strategy_async
            
            # Submit to Celery
            task = execute_strategy_async.delay(request.strategy_name, request.parameters)
            
            logger.info(f"Async execution started: {task.id}")
            
            return ExecutionStatus(
                execution_id=0,  # Will be set when task completes
                strategy_name=request.strategy_name,
                status="pending",
                started_at=datetime.now().isoformat(),
                completed_at=None,
                duration_ms=None,
                row_count=None,
                error_message=None,
                cached=False,
                job_id=task.id
            )
        except Exception as e:
            logger.error(f"Failed to start async execution: {e}")
            raise HTTPException(status_code=500, detail=f"Async execution failed: {str(e)}")
    
    # Synchronous execution
    # Ensure strategy is registered
    strategy_id = get_strategy_id(request.strategy_name)
    if not strategy_id:
        strategy_id = register_strategy(strategy)
    
    # Create execution record
    execution_id = create_execution_record(strategy_id, request.parameters, request.user_id)
    
    # Execute strategy
    try:
        result = strategy.execute(data_provider, request.parameters)
        
        if result.success:
            # Save result
            save_execution_result(execution_id, result.data, result.metadata, result.row_count)
            update_execution_record(execution_id, 'completed', int(result.execution_time_ms))
            
            # Cache result if enabled
            if cache and request.use_cache:
                try:
                    cache_data = {
                        "execution_id": execution_id,
                        "data": result.data,
                        "metadata": result.metadata,
                        "execution_time_ms": result.execution_time_ms,
                        "row_count": result.row_count,
                        "started_at": datetime.now().isoformat(),
                        "completed_at": datetime.now().isoformat()
                    }
                    cache.set_cached_result(request.strategy_name, request.parameters, cache_data)
                    logger.info(f"Result cached for {request.strategy_name}")
                except Exception as e:
                    logger.warning(f"Failed to cache result: {e}")
            
            # Record metrics
            metrics.record_execution(
                request.strategy_name,
                result.execution_time_ms,
                True,
                cached=False
            )
            
            return ExecutionStatus(
                execution_id=execution_id,
                strategy_name=request.strategy_name,
                status='completed',
                started_at=datetime.now().isoformat(),
                completed_at=datetime.now().isoformat(),
                duration_ms=int(result.execution_time_ms),
                row_count=result.row_count,
                error_message=None,
                cached=False
            )
        else:
            error_msg = result.metadata.get("error", "Unknown error")
            update_execution_record(execution_id, 'failed', int(result.execution_time_ms), error_msg)
            
            # Record metrics
            metrics.record_execution(
                request.strategy_name,
                result.execution_time_ms,
                False,
                cached=False,
                error=error_msg
            )
            
            return ExecutionStatus(
                execution_id=execution_id,
                strategy_name=request.strategy_name,
                status='failed',
                started_at=datetime.now().isoformat(),
                completed_at=datetime.now().isoformat(),
                duration_ms=int(result.execution_time_ms),
                row_count=0,
                error_message=error_msg,
                cached=False
            )
    
    except Exception as e:
        error_msg = str(e)
        update_execution_record(execution_id, 'failed', 0, error_msg)
        
        # Record metrics
        metrics.record_execution(
            request.strategy_name,
            0,
            False,
            cached=False,
            error=error_msg
        )
        
        raise HTTPException(status_code=500, detail=error_msg)


@app.get("/api/v1/executions/{execution_id}", response_model=ExecutionResult)
async def get_execution_result(execution_id: int):
    """Get execution status and results"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get execution info
    cursor.execute("""
        SELECT er.status, s.name, er.duration_ms, er.error_message
        FROM execution_runs er
        JOIN strategy_registry s ON er.strategy_id = s.id
        WHERE er.id = ?
    """, (execution_id,))
    
    exec_info = cursor.fetchone()
    if not exec_info:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Execution {execution_id} not found")
    
    status, strategy_name, duration_ms, error_message = exec_info
    
    # Get result data
    cursor.execute("""
        SELECT result_data, row_count, metadata
        FROM execution_results
        WHERE execution_id = ?
    """, (execution_id,))
    
    result_info = cursor.fetchone()
    conn.close()
    
    if result_info:
        result_data, row_count, metadata = result_info
        return ExecutionResult(
            execution_id=execution_id,
            status=status,
            data=json.loads(result_data),
            metadata=json.loads(metadata),
            row_count=row_count
        )
    else:
        return ExecutionResult(
            execution_id=execution_id,
            status=status,
            data=[],
            metadata={"error": error_message} if error_message else {},
            row_count=0
        )


@app.get("/api/v1/executions", response_model=List[ExecutionStatus])
async def list_executions(limit: int = 50, status: Optional[str] = None):
    """List recent executions"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
        SELECT er.id, s.name, er.status, er.started_at, er.completed_at, er.duration_ms, er.error_message
        FROM execution_runs er
        JOIN strategy_registry s ON er.strategy_id = s.id
    """
    
    params = []
    if status:
        query += " WHERE er.status = ?"
        params.append(status)
    
    query += " ORDER BY er.started_at DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    results = cursor.fetchall()
    conn.close()
    
    executions = []
    for row in results:
        exec_id, strategy_name, status, started_at, completed_at, duration_ms, error_msg = row
        
        # Get row count if available
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT row_count FROM execution_results WHERE execution_id = ?", (exec_id,))
        row_count_result = cursor.fetchone()
        conn.close()
        row_count = row_count_result[0] if row_count_result else None
        
        executions.append(ExecutionStatus(
            execution_id=exec_id,
            strategy_name=strategy_name,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            row_count=row_count,
            error_message=error_msg
        ))
    
    return executions


@app.get("/api/v1/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Get status of an async job"""
    try:
        from celery.result import AsyncResult
        from src.jobs.celery_app import celery_app
        
        task = AsyncResult(job_id, app=celery_app)
        
        if task.state == "PENDING":
            return {
                "job_id": job_id,
                "status": "pending",
                "message": "Task is waiting to be executed"
            }
        elif task.state == "STARTED":
            return {
                "job_id": job_id,
                "status": "running",
                "info": task.info
            }
        elif task.state == "PROCESSING":
            return {
                "job_id": job_id,
                "status": "processing",
                "info": task.info
            }
        elif task.state == "SUCCESS":
            result = task.result
            return {
                "job_id": job_id,
                "status": "completed",
                "result": result
            }
        elif task.state == "FAILURE":
            return {
                "job_id": job_id,
                "status": "failed",
                "error": str(task.info)
            }
        else:
            return {
                "job_id": job_id,
                "status": task.state.lower(),
                "info": task.info
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get job status: {str(e)}")


@app.get("/api/v1/metrics")
async def get_metrics():
    """Get performance metrics"""
    try:
        all_metrics = metrics.get_all_metrics()
        
        # Add cache stats if available
        if cache:
            try:
                cache_stats = cache.get_cache_stats()
                all_metrics["cache"] = cache_stats
            except Exception as e:
                logger.warning(f"Failed to get cache stats: {e}")
                all_metrics["cache"] = {"error": str(e)}
        else:
            all_metrics["cache"] = {"status": "disabled"}
        
        return all_metrics
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get metrics: {str(e)}")


@app.get("/api/v1/metrics/{strategy_name}")
async def get_strategy_metrics(strategy_name: str):
    """Get metrics for a specific strategy"""
    if strategy_name not in STRATEGIES:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_name}' not found")
    
    try:
        strategy_metrics = metrics.get_strategy_metrics(strategy_name)
        return {
            "strategy": strategy_name,
            "metrics": strategy_metrics
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get strategy metrics: {str(e)}")


@app.post("/api/v1/cache/invalidate")
async def invalidate_cache(strategy_name: Optional[str] = None):
    """Invalidate cache entries"""
    if not cache:
        raise HTTPException(status_code=503, detail="Cache is not available")
    
    try:
        deleted_count = cache.invalidate_cache(strategy_name=strategy_name)
        return {
            "status": "success",
            "deleted_entries": deleted_count,
            "strategy": strategy_name or "all"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to invalidate cache: {str(e)}")


@app.get("/api/v1/cache/stats")
async def get_cache_stats():
    """Get cache statistics"""
    if not cache:
        raise HTTPException(status_code=503, detail="Cache is not available")
    
    try:
        stats = cache.get_cache_stats()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get cache stats: {str(e)}")


@app.get("/api/v1/cache/health")
async def check_cache_health():
    """Check cache health"""
    if not cache:
        return {
            "status": "disabled",
            "message": "Cache is not configured"
        }
    
    try:
        health = cache.health_check()
        return health
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
