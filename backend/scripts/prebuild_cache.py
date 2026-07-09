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
_WARM_BULK_OPTIONS = os.environ.get("PREBUILD_BULK_OPTIONS", "0").strip().lower() in ("1", "true", "yes", "on")

# Set once the background warmup below finishes (success OR failure — a
# crashed warmup must not leave /health reporting "not ready" forever).
# /health (main.py) gates on this so Docker's own healthcheck, and the
# frontend's "server is restarting" overlay, both stay "not healthy" until
# the container is actually ready to serve real requests correctly — not
# just the instant the HTTP process starts accepting connections.
_warmup_done = threading.Event()


def is_warmup_complete() -> bool:
    return _warmup_done.is_set()


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

        # Step 3: Optional bulk option warmup. Disabled by default because it can
        # monopolize HDD I/O for minutes and compete with real backtests.
        if _WARM_BULK_OPTIONS:
            try:
                from base import bulk_load_options
                from services.fast_lookup import build_fast_lookup
                from services.data_loader import get_bulk_options_df, get_bulk_spot_df
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
                try:
                    build_fast_lookup(get_bulk_options_df(), get_bulk_spot_df())
                    logger.info("[WARMUP] Native fast cache warmed.")
                except Exception as native_exc:
                    logger.warning(f"[WARMUP] Native fast cache warmup failed: {native_exc}")
            except Exception as e:
                logger.warning(f"[WARMUP] Bulk option warmup failed: {e}")
        else:
            logger.info("[WARMUP] Bulk option warmup skipped (PREBUILD_BULK_OPTIONS=0).")

        logger.info("[WARMUP] Background warmup complete.")

    except Exception as e:
        logger.error(f"[WARMUP] Warmup thread crashed: {e}")
    finally:
        _warmup_done.set()


def start_background_warmup():
    """
    Launch warmup in a daemon thread so it does not block startup.
    The API is immediately available; warmup runs in background.
    """
    t = threading.Thread(target=_do_warmup, name="cache-warmup", daemon=True)
    t.start()
    logger.info("[WARMUP] Background warmup thread started.")
