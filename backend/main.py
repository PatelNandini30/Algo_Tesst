from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

# Include routers
from routers import backtest, expiry, strategies

# Create the FastAPI app
app = FastAPI(title="AlgoTest Clone API", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(backtest.router, prefix="/api", tags=["backtest"])
app.include_router(expiry.router, prefix="/api", tags=["expiry"])
app.include_router(strategies.router, prefix="/api", tags=["strategies"])

@app.get("/")
def read_root():
    return {"message": "AlgoTest Clone API is running"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}