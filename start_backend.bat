@echo off
cd /d "e:\Algo_Test_Software\backend"
echo Starting backend server...
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause