@echo off
echo === Starting AlgoTest Dynamic Strategy Builder ===
echo.

echo Starting Backend Server...
cd /d "e:\Algo_Test_Software\backend"
start "Backend Server" cmd /k "python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

echo Waiting for backend to start...
timeout /t 5 /nobreak >nul

echo Starting Frontend Server...
cd /d "e:\Algo_Test_Software\frontend"
start "Frontend Server" cmd /k "npm run dev"

echo.
echo Servers started!
echo Frontend: http://localhost:5173
echo Backend: http://localhost:8000
echo Backend API Docs: http://localhost:8000/docs
echo.
echo Close this window to stop both servers
pause >nul