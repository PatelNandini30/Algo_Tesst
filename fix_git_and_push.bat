@echo off
REM Fix Git lock and push to GitHub

echo Checking for Git lock file...
if exist .git\index.lock (
    echo Removing stale Git lock file...
    timeout /t 2 /nobreak >nul
    del /f .git\index.lock 2>nul
    if exist .git\index.lock (
        echo ERROR: Could not remove lock file. Please close any Git GUI tools and try again.
        echo Or manually delete: .git\index.lock
        pause
        exit /b 1
    )
)

echo.
echo Adding all files...
git add .

echo.
echo Committing changes...
git commit -m "Initial commit: AlgoTest backtesting platform with all features"

echo.
echo Setting branch to main...
git branch -M main

echo.
echo Pushing to GitHub...
git push -u origin main

echo.
echo Done! Check your repository at: https://github.com/PatelNandini30/Algo_Tesst
pause
