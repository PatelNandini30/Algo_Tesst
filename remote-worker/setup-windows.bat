@echo off
setlocal

echo === AlgoTest LAN remote worker setup ===
echo.

where docker >nul 2>nul
if errorlevel 1 (
    echo ERROR: Docker was not found on PATH. Install Docker Desktop first,
    echo then make sure it's running (whale icon in the system tray) before
    echo running this script again.
    pause
    exit /b 1
)

if not exist ".env" (
    if not exist ".env.example" (
        echo ERROR: .env.example is missing from this folder.
        pause
        exit /b 1
    )
    copy .env.example .env >nul
    echo Created .env from .env.example.
    echo.
    echo IMPORTANT: open .env in Notepad now and set NODE_IP to THIS PC's own
    echo LAN IP ^(run "ipconfig" and look for IPv4 Address^), and set
    echo NODE_CONCURRENCY to how many cores to dedicate. Save the file, then
    echo run this script again.
    notepad .env
    pause
    exit /b 0
)

if not exist "algotest-worker-image.tar" (
    echo ERROR: algotest-worker-image.tar is missing from this folder.
    echo Copy it here from the main box first.
    pause
    exit /b 1
)

echo Loading the worker image ^(this can take a minute^)...
docker load -i algotest-worker-image.tar
if errorlevel 1 (
    echo ERROR: docker load failed. Is Docker Desktop running?
    pause
    exit /b 1
)

echo.
echo Starting the worker...
docker compose up -d
if errorlevel 1 (
    echo ERROR: docker compose up failed. Check the .env values above.
    pause
    exit /b 1
)

echo.
echo === Done ===
echo Check status any time with:  docker compose logs -f remote-worker
echo Stop it with:                docker compose down
pause
