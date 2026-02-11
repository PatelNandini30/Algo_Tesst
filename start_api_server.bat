@echo off
echo Starting FastAPI Server (Phase 2)...
echo.
echo API will be available at: http://localhost:8000
echo API Documentation: http://localhost:8000/docs
echo.
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000
