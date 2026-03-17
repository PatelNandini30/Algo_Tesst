"""
Database configuration and connection management.
Optimized for high-concurrency backtesting (50+ users).

PHASE 4: Database Connection Pooling

Key optimizations:
- pool_size=50: Maintains 50 persistent connections
- max_overflow=100: Allows 100 additional connections under load
- pool_timeout=30: Fail fast if pool exhausted
- pool_pre_ping: Verify connections before use
- pool_recycle=1800: Recycle connections every 30 minutes
- statement_timeout: Prevent runaway queries
"""

import os
import logging
import threading
import pandas as pd
from typing import Optional
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import QueuePool

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "algotest")
POSTGRES_USER = os.getenv("POSTGRES_USER", "algotest")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "algotest_password")


def _build_default_database_url() -> str:
    return f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"


ALLOW_CSV_FALLBACK = os.getenv("ALLOW_CSV_FALLBACK", "true").lower() == "true"

# Get database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL") or _build_default_database_url()
USE_POSTGRESQL = os.getenv("USE_POSTGRESQL", "true").lower() == "true"

# ============================================================================
# Connection Pool Configuration (Optimized for 50+ users)
# ============================================================================

# Pool settings - reduced to prevent PostgreSQL "too many clients" errors
# PostgreSQL default max_connections = 100, so pool + overflow should be <= 15
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))           # Persistent connections
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "5"))        # Additional connections under load
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))     # Seconds to wait for connection
POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))    # Recycle connections every 30 min
STATEMENT_TIMEOUT = int(os.getenv("DB_STATEMENT_TIMEOUT", "300000"))  # 5 min query timeout


def _get_engine():
    """
    Create SQLAlchemy engine with optimized connection pooling.
    
    Configuration:
    - QueuePool: Default pool class, handles connection queue
    - pool_size: Number of connections to maintain
    - max_overflow: Additional connections when pool exhausted
    - pool_timeout: Seconds to wait for available connection
    - pool_recycle: Recycle connections to avoid stale connections
    - pool_pre_ping: Test connections before use (handles dropped connections)
    """
    return create_engine(
        DATABASE_URL,
        poolclass=QueuePool,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_timeout=POOL_TIMEOUT,
        pool_recycle=POOL_RECYCLE,
        pool_pre_ping=True,
        echo=False,
        connect_args={
            # Set statement timeout (milliseconds)
            "options": f"-c statement_timeout={STATEMENT_TIMEOUT}"
        }
    )


# Thread-safe singleton engine
_engine_lock = threading.Lock()
_engine = None


def get_engine():
    """Get singleton engine with connection pooling."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = _get_engine()
                logger.info(
                    f"[DB POOL] Initialized: pool_size={POOL_SIZE}, "
                    f"max_overflow={MAX_OVERFLOW}, pool_timeout={POOL_TIMEOUT}s"
                )
    return _engine


def reset_engine():
    """Dispose and recreate the underlying engine (used after fatal connection errors)."""
    global _engine
    with _engine_lock:
        if _engine is not None:
            try:
                _engine.dispose()
            except Exception as exc:
                logger.warning(f"[DB POOL] Failed to dispose engine: {exc}")
        _engine = _get_engine()
        logger.warning("[DB POOL] Engine reset invoked (operational error recovery)")
    return _engine


# For backwards compatibility
engine = get_engine()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ============================================================================
# Connection Pool Monitoring
# ============================================================================

def get_pool_status() -> dict:
    """Get current connection pool status."""
    pool = engine.pool
    return {
        "pool_size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
        "invalid": pool.invalidatedcount() if hasattr(pool, 'invalidatedcount') else 0
    }


def log_pool_status():
    """Log current connection pool status."""
    status = get_pool_status()
    logger.info(
        f"[DB POOL] Status: "
        f"size={status['pool_size']}, "
        f"checked_in={status['checked_in']}, "
        f"checked_out={status['checked_out']}, "
        f"overflow={status['overflow']}"
    )


@event.listens_for(engine, "checkout")
def receive_checkout(dbapi_connection, connection_record, connection_proxy):
    """Log when connection is checked out from pool."""
    logger.debug(f"[DB POOL] Checkout: connection {id(dbapi_connection)}")


@event.listens_for(engine, "checkin")
def receive_checkin(dbapi_connection, connection_record):
    """Log when connection is returned to pool."""
    logger.debug(f"[DB POOL] Checkin: connection {id(dbapi_connection)}")


# ============================================================================
# Database Session
# ============================================================================

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
        logger.warning(f"PostgreSQL connection failed: {e}")
        return False


# ============================================================================
# CSV Fallback paths
# ============================================================================

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
            logger.warning("Falling back to CSV data source because Postgres is unreachable.")
            return "csv"
        raise RuntimeError("PostgreSQL is unreachable and CSV fallback is disabled.")

    if ALLOW_CSV_FALLBACK:
        logger.info("PostgreSQL usage disabled; using CSV fallback.")
        return "csv"

    raise RuntimeError("PostgreSQL usage is disabled and CSV fallback is turned off.")


DATA_SOURCE = _determine_data_source()


def get_data_source() -> str:
    """Return current data source: 'postgres' or 'csv'."""
    return DATA_SOURCE
