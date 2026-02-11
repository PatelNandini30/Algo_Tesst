@echo off
cd /d "e:\Algo_Test_Software"

echo Starting Strategy Performance Dashboard...
echo ==========================================

REM Check if required packages are installed
python -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo Installing required packages...
    pip install streamlit plotly openpyxl
)

echo Launching dashboard...
echo Open your browser to view the dashboard
echo Press Ctrl+C to stop the dashboard

python -m streamlit run strategy_dashboard.py --server.port 8501

pause