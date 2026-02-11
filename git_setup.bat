@echo off
REM Git setup script for Algo_Test_Software repository

echo Initializing Git repository...
git init

echo Adding remote repository...
git remote add origin https://github.com/PatelNandini30/Algo_Tesst.git

echo Creating .gitignore file...
echo __pycache__/ > .gitignore
echo *.pyc >> .gitignore
echo *.pyo >> .gitignore
echo *.db >> .gitignore
echo *.log >> .gitignore
echo .env >> .gitignore
echo venv/ >> .gitignore
echo env/ >> .gitignore
echo node_modules/ >> .gitignore
echo .DS_Store >> .gitignore
echo Thumbs.db >> .gitignore
echo *.swp >> .gitignore
echo *.swo >> .gitignore
echo .vscode/ >> .gitignore
echo .idea/ >> .gitignore

echo Adding all files...
git add .

echo Committing files...
git commit -m "Initial commit: AlgoTest backtesting platform"

echo Pushing to GitHub...
git branch -M main
git push -u origin main

echo Done! Repository has been pushed to GitHub.
pause
