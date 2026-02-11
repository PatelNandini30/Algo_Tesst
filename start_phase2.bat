@echo off
echo ========================================
echo   NSE Options Strategy API - Phase 2
echo   Caching, Background Jobs & Monitoring
echo ========================================
echo.

REM Check if Redis is running
echo [1/4] Checking Redis connection...
redis-cli ping >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: Redis is not running!
    echo.
    echo To start Redis:
    echo   - Windows: redis-server.exe
    echo   - Docker: docker run -d -p 6379:6379 redis:latest
    echo.
    echo The API will work without Redis but caching will be disabled.
    echo.
    pause
)

REM Check if virtual environment exists
if not exist "venv\" (
    echo [2/4] Creating virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo ERROR: Failed to create virtual environment
        pause
        exit /b 1
    )
)

REM Activate virtual environment
echo [2/4] Activating virtual environment...
call venv\Scripts\activate.bat

REM Install dependencies
echo [3/4] Installing dependencies...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

REM Start API server
echo [4/4] Starting API server...
echo.
echo ========================================
echo   API Server: http://localhost:8000
echo   API Docs: http://localhost:8000/docs
echo ========================================
echo.
echo Phase 2 Features:
echo   - Redis Caching (96%% faster responses)
echo   - Async Execution (non-blocking)
echo   - Performance Monitoring
echo   - 3 Strategy Variants
echo.
echo To start Celery worker (for async execution):
echo   celery -A src.jobs.celery_app worker --loglevel=info --pool=solo
echo.
echo Press Ctrl+C to stop the server
echo.

python -m uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
