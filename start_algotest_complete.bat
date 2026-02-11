@echo off
cd /d "e:\Algo_Test_Software"

echo ========================================
echo ALGOTEST COMPLETE SYSTEM STARTUP
echo ========================================
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH
    echo Please install Python 3.8 or later
    pause
    exit /b 1
)

echo ✓ Python is available
echo.

REM Check and install required packages
echo Checking required packages...
echo.

set PACKAGES=fastapi uvicorn pandas numpy streamlit plotly requests pydantic

for %%p in (%PACKAGES%) do (
    python -c "import %%p" >nul 2>&1
    if errorlevel 1 (
        echo Installing %%p...
        pip install %%p
    ) else (
        echo ✓ %%p is already installed
    )
)

echo.
echo All required packages checked/installed
echo.

REM Validate system before starting
echo Running system validation...
python validate_system.py
echo.

REM Start complete system
echo Starting AlgoTest complete system...
echo.
python start_complete_system.py

pause