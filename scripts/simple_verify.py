print("=== ALGOTEST SYSTEM VERIFICATION ===")
print()

# Test core components
components = [
    ("Python", "import sys"),
    ("Pandas", "import pandas"),
    ("NumPy", "import numpy"), 
    ("SQLite3", "import sqlite3"),
    ("FastAPI", "import fastapi"),
    ("Uvicorn", "import uvicorn"),
    ("Streamlit", "import streamlit"),
    ("Plotly", "import plotly")
]

working = 0
total = len(components)

for name, import_cmd in components:
    try:
        exec(import_cmd)
        print(f"✓ {name}: OK")
        working += 1
    except Exception as e:
        print(f"✗ {name}: {e}")

print()
print("=== BACKEND COMPONENTS ===")

# Test backend components
backend_components = [
    ("Backtest Router", "from backend.routers import backtest"),
    ("Strategies Router", "from backend.routers import strategies"), 
    ("Expiry Router", "from backend.routers import expiry"),
    ("V1 Strategy", "from backend.engines.v1_ce_fut import run_v1"),
    ("Base Functions", "from backend.base import get_strike_data")
]

for name, import_cmd in backend_components:
    try:
        exec(import_cmd)
        print(f"✓ {name}: OK")
        working += 1
        total += 1
    except Exception as e:
        print(f"✗ {name}: {e}")
        total += 1

print()
print("=== ANALYSIS COMPONENTS ===")

# Test new components
analysis_components = [
    ("Strategy Analyzer", "from strategy_analyzer import StrategyPerformanceAnalyzer"),
    ("Strategy CLI", "import strategy_cli")
]

for name, import_cmd in analysis_components:
    try:
        exec(import_cmd)
        print(f"✓ {name}: OK")
        working += 1
        total += 1
    except Exception as e:
        print(f"✗ {name}: {e}")
        total += 1

print()
print("=== SUMMARY ===")
print(f"Components working: {working}/{total}")
print(f"Success rate: {working/total*100:.1f}%")

if working == total:
    print("🎉 ALL COMPONENTS WORKING!")
    print("✅ AlgoTest system is fully operational")
elif working >= total * 0.9:
    print("⚠ MOST COMPONENTS WORKING")
    print("✅ System is mostly operational")
else:
    print("❌ SOME COMPONENTS NOT WORKING")
    print("⚠ System needs attention")

print()
print("=== NEXT STEPS ===")
print("1. Run 'start_algotest_complete.bat' for full system")
print("2. Run 'python strategy_cli.py --help' for CLI usage") 
print("3. Run 'start_dashboard.bat' for web dashboard")
print("4. Access API at http://localhost:8000")