@echo off
echo Starting AlgoTest Integration System
echo ==================================

echo.
echo 1. Starting Backend Server (FastAPI)...
cd backend
start "Backend Server" cmd /k "uvicorn main:app --reload --host 0.0.0.0 --port 8000"
timeout /t 3 /nobreak >nul

echo.
echo 2. Starting Frontend Server (Vite)...
cd ../frontend
start "Frontend Server" cmd /k "npm run dev"

echo.
echo 3. Opening Browser...
timeout /t 5 /nobreak >nul
start http://localhost:5173

echo.
echo ==================================
echo Backend running on: http://localhost:8000
echo Frontend running on: http://localhost:5173
echo API Docs available at: http://localhost:8000/docs
echo ==================================
echo.
echo Press any key to exit this script (servers will keep running)
pause >nul