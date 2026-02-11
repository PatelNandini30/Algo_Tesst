@echo off
cd /d "e:\Algo_Test_Software"
python debug_strategy.py > debug_output.txt 2>&1
echo Output saved to debug_output.txt
type debug_output.txt
pause