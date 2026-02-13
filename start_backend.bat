@echo off
echo ================================================================================
echo   ALGOTEST BACKEND SERVER - STARTUP
echo ================================================================================
echo.

cd backend

echo Starting FastAPI server...
echo.
echo API will be available at:
echo   - Main API: http://localhost:8000
echo   - API Docs: http://localhost:8000/docs
echo   - Health Check: http://localhost:8000/health
echo.
echo Press CTRL+C to stop the server
echo.
echo ================================================================================
echo.

python start_server.py

pause
