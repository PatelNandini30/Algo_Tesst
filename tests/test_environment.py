print("Testing Python environment...")
print("Current working directory:", __import__('os').getcwd())

# Test importing modules
try:
    import sys
    print("sys imported successfully")
    print("Python version:", sys.version)
except Exception as e:
    print("Error importing sys:", e)

try:
    import fastapi
    print("FastAPI imported successfully")
except Exception as e:
    print("Error importing FastAPI:", e)

try:
    import uvicorn
    print("Uvicorn imported successfully")
except Exception as e:
    print("Error importing Uvicorn:", e)

print("Test completed.")