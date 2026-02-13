"""
Working backend server with all components
"""
import sys
import os

# Add backend directory to path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

print(f"Backend directory: {backend_dir}")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Import routers
from routers import backtest, expiry, strategies

# Create the FastAPI app
app = FastAPI(title="AlgoTest Clone API", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(backtest.router, prefix="/api", tags=["backtest"])
app.include_router(expiry.router, prefix="/api", tags=["expiry"])
app.include_router(strategies.router, prefix="/api", tags=["strategies"])

@app.get("/")
def read_root():
    return {"message": "AlgoTest Clone API is running"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    print("Starting full backend server...")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")