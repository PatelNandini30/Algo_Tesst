@echo off
REM Simple script to push to existing GitHub repository

echo Checking Git status...
git status

echo.
echo Adding all files...
git add .

echo.
echo Committing changes...
git commit -m "Update: AlgoTest backtesting platform with all features"

echo.
echo Pushing to GitHub...
git push -u origin main

echo.
echo Done! Check your repository at: https://github.com/PatelNandini30/Algo_Tesst
pause
