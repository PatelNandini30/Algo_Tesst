@echo off
cls
echo ========================================
echo AlgoTest Clone - Complete Setup
echo ========================================
echo.

echo Stopping any existing servers...
taskkill /f /im python.exe /fi "WINDOWTITLE eq *uvicorn*" 2>nul
taskkill /f /im node.exe 2>nul

echo.
echo Starting Backend Server...
cd /d "E:\Algo_Test_Software\backend"
start "Backend Server" cmd /k "python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

echo.
echo Waiting for backend to start...
timeout /t 5 /nobreak >nul

echo.
echo Starting Frontend Server...
cd /d "E:\Algo_Test_Software\frontend"
if not exist "node_modules" (
    echo Installing frontend dependencies...
    npm install
)
start "Frontend Server" cmd /k "npm run dev"

echo.
echo ========================================
echo Servers Started Successfully!
echo ========================================
echo Backend API:  http://localhost:8000
echo Frontend App: http://localhost:3000
echo.
echo Press any key to close this window...
pause >nul