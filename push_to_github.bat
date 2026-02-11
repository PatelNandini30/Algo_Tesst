@echo off
echo ========================================
echo   Push to GitHub - AlgoTest Platform
echo ========================================
echo.

REM Check if there are any changes
git status --short > temp_status.txt
set /p changes=<temp_status.txt
del temp_status.txt

if "%changes%"=="" (
    echo No changes to commit.
    pause
    exit /b 0
)

REM Add all changes
echo [1/5] Adding all changes...
git add .

REM Ask user for commit message
echo.
echo Enter commit message (or press Enter for auto-generated message):
set /p commit_msg="> "

REM If no message provided, generate one based on changes
if "%commit_msg%"=="" (
    echo [2/5] Generating commit message based on changes...
    
    REM Get list of changed files
    git diff --cached --name-only > changed_files.txt
    
    REM Count changes by type
    set /a backend_changes=0
    set /a frontend_changes=0
    set /a config_changes=0
    set /a doc_changes=0
    set /a other_changes=0
    
    for /f "delims=" %%f in (changed_files.txt) do (
        echo %%f | findstr /i "backend" >nul && set /a backend_changes+=1
        echo %%f | findstr /i "frontend" >nul && set /a frontend_changes+=1
        echo %%f | findstr /i ".bat .json .gitignore requirements.txt" >nul && set /a config_changes+=1
        echo %%f | findstr /i ".md" >nul && set /a doc_changes+=1
    )
    
    del changed_files.txt
    
    REM Build commit message
    set commit_msg=Update: 
    
    if %backend_changes% gtr 0 (
        set commit_msg=!commit_msg!Backend changes ^(%backend_changes% files^), 
    )
    if %frontend_changes% gtr 0 (
        set commit_msg=!commit_msg!Frontend changes ^(%frontend_changes% files^), 
    )
    if %config_changes% gtr 0 (
        set commit_msg=!commit_msg!Config updates ^(%config_changes% files^), 
    )
    if %doc_changes% gtr 0 (
        set commit_msg=!commit_msg!Documentation ^(%doc_changes% files^), 
    )
    
    REM Remove trailing comma and space
    set commit_msg=!commit_msg:~0,-2!
    
    REM Add timestamp
    set timestamp=%date:~-4,4%-%date:~-10,2%-%date:~-7,2% %time:~0,2%:%time:~3,2%
    set commit_msg=!commit_msg! [!timestamp!]
) else (
    echo [2/5] Using custom commit message...
)

REM Enable delayed expansion for variables
setlocal enabledelayedexpansion

REM Commit changes
echo [3/5] Committing changes...
git commit -m "!commit_msg!"

REM Pull latest changes (if any)
echo [4/5] Pulling latest changes...
git pull origin main --allow-unrelated-histories --no-edit

REM Push to GitHub
echo [5/5] Pushing to GitHub...
git push -u origin main

echo.
echo ========================================
echo   Push Complete!
echo   Commit: !commit_msg!
echo ========================================
pause
