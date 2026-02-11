try:
    from main import app
    print("Main app imported successfully")
except Exception as e:
    print(f"Error importing main app: {e}")

try:
    from routers import backtest
    print("Backtest router imported successfully")
except Exception as e:
    print(f"Error importing backtest router: {e}")

try:
    from routers import expiry
    print("Expiry router imported successfully")
except Exception as e:
    print(f"Error importing expiry router: {e}")

try:
    from routers import strategies
    print("Strategies router imported successfully")
except Exception as e:
    print(f"Error importing strategies router: {e}")

try:
    import uvicorn
    print("Uvicorn imported successfully")
except Exception as e:
    print(f"Error importing uvicorn: {e}")