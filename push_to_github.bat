@echo off
echo ========================================
echo   Push to GitHub - AlgoTest Platform
echo ========================================
echo.

REM Add all changes
echo [1/4] Adding all changes...
git add .

REM Commit with timestamp
echo [2/4] Committing changes...
set timestamp=%date:~-4,4%-%date:~-10,2%-%date:~-7,2% %time:~0,2%:%time:~3,2%:%time:~6,2%
git commit -m "Update: %timestamp%"

REM Pull latest changes (if any)
echo [3/4] Pulling latest changes...
git pull origin main --allow-unrelated-histories --no-edit

REM Push to GitHub
echo [4/4] Pushing to GitHub...
git push -u origin main

echo.
echo ========================================
echo   Push Complete!
echo ========================================
pause
