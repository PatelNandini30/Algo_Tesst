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

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "algotest")
POSTGRES_USER = os.getenv("POSTGRES_USER", "algotest")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "algotest_password")


def _build_default_database_url() -> str:
    return f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"


ALLOW_CSV_FALLBACK = os.getenv("ALLOW_CSV_FALLBACK", "true").lower() == "true"

# Get database URL from environment (DATABASE_URL overrides component-based values)
DATABASE_URL = os.getenv("DATABASE_URL") or _build_default_database_url()
USE_POSTGRESQL = os.getenv("USE_POSTGRESQL", "true").lower() == "true"

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



def _determine_data_source() -> str:
    """Decide whether PostgreSQL or CSV is the active data source."""
    if USE_POSTGRESQL:
        if check_postgres_connection():
            return "postgres"
        if ALLOW_CSV_FALLBACK:
            print("Falling back to CSV data source because Postgres is unreachable.")
            return "csv"
        raise RuntimeError("PostgreSQL is unreachable and CSV fallback is disabled (set ALLOW_CSV_FALLBACK=true to enable).")

    if ALLOW_CSV_FALLBACK:
        print("PostgreSQL usage disabled; using CSV fallback.")
        return "csv"

    raise RuntimeError("PostgreSQL usage is disabled and CSV fallback is turned off. Enable one source.")


DATA_SOURCE = _determine_data_source()


def get_data_source() -> str:
    """Return current data source: 'postgres' or 'csv'."""
    return DATA_SOURCE
