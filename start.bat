@echo off
echo ================================================
echo   AlgoTest - Starting All Services
echo ================================================
echo.

echo [1/2] Starting Docker services (postgres, redis, backend, frontend)...
cd /d E:\Algo_Test_Software
docker-compose up -d

echo.
echo [2/2] Waiting for services to become healthy...
timeout /t 20 /nobreak > nul

echo.
echo ================================================
echo   All services started!
echo ================================================
echo.
echo   Frontend:  http://localhost:3000
echo   Backend:   http://localhost:8000
echo   API Docs:  http://localhost:8000/docs
echo.
echo   Network access (other PCs on same WiFi):
echo   Frontend:  http://192.168.4.34:3000
echo   Backend:   http://192.168.4.34:8000
echo.
echo ================================================
pause