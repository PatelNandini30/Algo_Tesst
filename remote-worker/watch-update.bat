@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM  EDIT THIS ONE LINE: full path to the tar on the share.
REM  (UNC works directly, no need to map a drive.)
set "SHARE_TAR=\\192.168.4.50\share\Manan Pujara\Algo Test\algotest-worker-image.tar"
REM  How often to check, in seconds.
set "INTERVAL=60"
REM ============================================================

REM Everything below is relative to THIS .bat's folder, which must
REM also contain docker-compose.yml and .env.
cd /d "%~dp0"
set "IMAGE=algotest-backend-app:latest"
set "LOCAL_TAR=%~dp0algotest-worker-image.tar"
set "HASHFILE=%~dp0.last_tar_hash"

where docker >nul 2>nul || ( echo ERROR: docker not on PATH. Start Docker Desktop. & pause & exit /b 1 )
if not exist "%~dp0docker-compose.yml" ( echo ERROR: docker-compose.yml missing from this folder. & pause & exit /b 1 )

echo === AlgoTest tar watcher ===
echo Watching: %SHARE_TAR%
echo Every %INTERVAL%s. Leave this window open. Ctrl+C to stop.
echo.

:loop
if not exist "%SHARE_TAR%" (
    echo [%date% %time%] share not reachable - will retry
    goto wait
)

call :sha256 "%SHARE_TAR%" SRCHASH
if not defined SRCHASH ( echo [%date% %time%] could not hash share file - retry & goto wait )

set "LASTHASH="
if exist "%HASHFILE%" set /p LASTHASH=<"%HASHFILE%"

if /i "!SRCHASH!"=="!LASTHASH!" (
    echo [%date% %time%] no change (!SRCHASH:~0,12!...)
    goto wait
)

echo [%date% %time%] NEW tar detected - updating...

copy /y "%SHARE_TAR%" "%LOCAL_TAR%" >nul
if errorlevel 1 ( echo   copy failed - retry & goto wait )

REM verify the local copy is byte-identical before touching containers
call :sha256 "%LOCAL_TAR%" DSTHASH
if /i not "!SRCHASH!"=="!DSTHASH!" ( echo   copy hash mismatch - aborting this cycle & goto wait )

echo   stopping + removing old containers...
docker compose down
echo   removing old image...
docker image rm -f "%IMAGE%" 2>nul
echo   loading new image...
docker load -i "%LOCAL_TAR%"
if errorlevel 1 ( echo   docker load FAILED - not starting; will retry & goto wait )
echo   starting worker...
docker compose up -d
if errorlevel 1 ( echo   compose up FAILED - check .env & goto wait )

> "%HASHFILE%" echo !SRCHASH!
echo [%date% %time%] DONE - now running !SRCHASH:~0,12!...

:wait
timeout /t %INTERVAL% /nobreak >nul
goto loop

REM --- sha256 of %1 into variable named by %2 ---
:sha256
set "_v="
for /f "skip=1 delims=" %%H in ('certutil -hashfile "%~1" SHA256 2^>nul') do if not defined _v set "_v=%%H"
set "_v=%_v: =%"
set "%~2=%_v%"
exit /b
