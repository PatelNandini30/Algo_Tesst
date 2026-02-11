@echo off
cd /d "e:\Algo_Test_Software"
echo Running strike availability diagnostic...
python diagnose_strikes.py > strike_diagnostic.txt 2>&1
echo Diagnostic complete. Check strike_diagnostic.txt

echo.
echo Running 2020 strategy test...
python test_2020_strategy.py > strategy_2020.txt 2>&1
echo 2020 strategy test complete. Check strategy_2020.txt

echo.
echo Files created:
dir *.txt
pause