@echo off
echo Killing any process using port 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000') do taskkill /F /PID %%a 2>nul
timeout /t 2 /nobreak >nul
echo Starting server...
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000
