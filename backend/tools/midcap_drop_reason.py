"""Surface the REAL reason a multi-index leg is dropped.

multi_index_feature.py:1511-1513 swallows the resolver exception:

    except Exception as _sx:
        logger.debug("[MULTI_INDEX] %s strike resolve failed (%s): %s", ...)
        strike = None

and then warns with only the probe expiry, so at INFO level the cause is
invisible. This runs the real backtest with that ONE logger at DEBUG, so the
swallowed exception is printed. Nothing else is changed.

    LEGORDER_JOB=<job_id> python -m tools.midcap_drop_reason
"""
import logging
import os

from services.algotest_job import execute_algotest_job
from services.optimizer import result_store as rs


def main() -> int:
    lg = logging.getLogger("services.multi_index_feature")
    lg.setLevel(logging.DEBUG)
    h = logging.StreamHandler()
    h.setLevel(logging.DEBUG)
    h.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    lg.addHandler(h)
    lg.propagate = False

    bp = dict((rs.get_meta(os.environ["LEGORDER_JOB"]) or {}).get("base_payload") or {})
    for k in ("start_date", "date_from", "from_date"):
        if k in bp:
            bp[k] = "2024-01-01"
    for k in ("end_date", "date_to", "to_date"):
        if k in bp:
            bp[k] = "2024-06-30"
    bp.pop("filter_segments", None)
    bp.pop("str_filter", None)
    bp["filter"] = None
    bp["filter_config"] = None
    execute_algotest_job(bp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
