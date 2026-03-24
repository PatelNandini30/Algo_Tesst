"""
Startup cache pre-warmer.
Run automatically at container startup via main.py lifespan.
Loads the most recent N years of option data + STR segments into memory
so the first backtest request hits a warm cache.
"""
import os
import logging
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_WARM_YEARS = int(os.environ.get("PREBUILD_WARM_YEARS", "2"))
_WARM_SYMBOL = os.environ.get("PREBUILD_SYMBOL", "NIFTY")


def _do_warmup():
    """Background thread: warm bulk_load + STR + trading calendar caches."""
    try:
        logger.info("[WARMUP] Starting background cache warmup...")

        # Step 0: Apply pending migrations (idempotent — safe to run every startup)
        try:
            import os
            from sqlalchemy import text
            from database import get_engine
            migration_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "migrations", "006_add_recent_data_index.sql"
            )
            if os.path.exists(migration_path):
                with open(migration_path) as f:
                    sql = f.read()
                engine = get_engine()
                with engine.begin() as conn:
                    for stmt in sql.split(';'):
                        stmt = stmt.strip()
                        if stmt and not stmt.startswith('--'):
                            try:
                                conn.execute(text(stmt + ';'))
                            except Exception:
                                pass
                logger.info("[WARMUP] Migration 006 applied (partial index).")
        except Exception as e:
            logger.warning(f"[WARMUP] Migration 006 failed (non-fatal): {e}")

        # Step 1: Warm the trading calendar (avoids 3-8s DISTINCT scan)
        try:
            from repositories.market_data_repository import MarketDataRepository
            from database import get_engine
            repo = MarketDataRepository(get_engine())
            # Load full calendar into class-level cache
            repo.get_trading_calendar(
                from_date="2008-01-01",
                to_date=datetime.now().strftime("%Y-%m-%d")
            )
            logger.info("[WARMUP] Trading calendar warmed.")
        except Exception as e:
            logger.warning(f"[WARMUP] Trading calendar warmup failed: {e}")

        # Step 2: Warm STR segments (fast — just CSV/DB read)
        try:
            from base import load_super_trend_dates
            load_super_trend_dates()
            logger.info("[WARMUP] STR segments warmed.")
        except Exception as e:
            logger.warning(f"[WARMUP] STR segment warmup failed: {e}")

        # Step 3: Warm bulk option data for recent N years
        # This is the expensive step — runs in background so it
        # does NOT block the first user request.
        try:
            from base import bulk_load_options
            to_date = datetime.now().strftime("%Y-%m-%d")
            from_date = (datetime.now() - timedelta(days=_WARM_YEARS * 365)).strftime("%Y-%m-%d")
            logger.info(
                f"[WARMUP] Bulk loading {_WARM_SYMBOL} options "
                f"{from_date} → {to_date} ({_WARM_YEARS} years)..."
            )
            result = bulk_load_options(_WARM_SYMBOL, from_date, to_date)
            logger.info(
                f"[WARMUP] Bulk load complete: "
                f"{result.get('options_rows', '?')} option rows, "
                f"{result.get('spot_rows', '?')} spot rows."
            )
        except Exception as e:
            logger.warning(f"[WARMUP] Bulk option warmup failed: {e}")

        logger.info("[WARMUP] Background warmup complete.")

    except Exception as e:
        logger.error(f"[WARMUP] Warmup thread crashed: {e}")


def start_background_warmup():
    """
    Launch warmup in a daemon thread so it does not block startup.
    The API is immediately available; warmup runs in background.
    """
    t = threading.Thread(target=_do_warmup, name="cache-warmup", daemon=True)
    t.start()
    logger.info("[WARMUP] Background warmup thread started.")
