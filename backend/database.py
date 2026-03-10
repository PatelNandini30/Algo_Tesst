"""
Database configuration and connection management.
Supports both PostgreSQL and CSV-based data access.
"""
import os
import pandas as pd
from typing import Optional
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool

# Get database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://algotest:algotest_password@localhost:5432/algotest")
USE_POSTGRESQL = os.getenv("USE_POSTGRESQL", "false").lower() == "true"

# Create SQLAlchemy engine with connection pooling
engine = create_engine(
    DATABASE_URL,
    pool_size=10,  # Connection pool for better performance
    max_overflow=20,
    pool_pre_ping=True,  # Verify connections before using
    pool_recycle=3600,  # Recycle connections after 1 hour
    echo=False,
    connect_args={
        "options": "-c statement_timeout=300000"  # 5 min timeout
    }
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)


def check_postgres_connection() -> bool:
    """Check if PostgreSQL is available."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        print(f"PostgreSQL connection failed: {e}")
        return False


# CSV Fallback paths
DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__))))
CLEANED_CSV_DIR = os.path.join(DATA_DIR, "cleaned_csvs")
EXPIRY_DATA_DIR = os.path.join(DATA_DIR, "expiryData")
STRIKE_DATA_DIR = os.path.join(DATA_DIR, "strikeData")


def get_data_source() -> str:
    """Return current data source: 'postgres' or 'csv'."""
    if USE_POSTGRESQL and check_postgres_connection():
        return "postgres"
    return "csv"
