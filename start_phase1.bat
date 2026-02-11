@echo off
echo ========================================
echo Phase 1: Foundation Layer - Quick Start
echo ========================================
echo.

echo Step 1: Installing dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo Error: Failed to install dependencies
    pause
    exit /b 1
)
echo.

echo Step 2: Running database migration...
python run_migration.py
if %errorlevel% neq 0 (
    echo Error: Migration failed
    pause
    exit /b 1
)
echo.

echo Step 3: Starting API server...
echo API will be available at: http://localhost:8000
echo API Documentation: http://localhost:8000/docs
echo.
echo Press Ctrl+C to stop the server
echo.

python -m src.api.main
