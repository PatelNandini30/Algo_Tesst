import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base import bulk_load_options

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("prebuild_cache")


def main():
    symbol = os.getenv("CACHE_SYMBOL", "NIFTY")
    from_date = os.getenv("CACHE_FROM_DATE", "2020-01-01")
    to_date = os.getenv("CACHE_TO_DATE", "2024-12-31")

    cache_dir = os.getenv("PARQUET_CACHE_DIR", "/tmp/parquet_cache")
    os.makedirs(cache_dir, exist_ok=True)

    logger.info(f"Starting cache warm for {symbol} {from_date} -> {to_date}")
    try:
        bulk_load_options(symbol, from_date, to_date)
        logger.info("Prebuild cache run completed")
    except Exception as exc:
        logger.warning(f"Cache warm failed: {exc}")


if __name__ == "__main__":
    main()
