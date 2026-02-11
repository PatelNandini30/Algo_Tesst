@echo off
cd /d "e:\Algo_Test_Software"
python analyze_missing_data.py > missing_data_analysis.txt 2>&1
echo Analysis complete. Check missing_data_analysis.txt for results.
type missing_data_analysis.txt
pause