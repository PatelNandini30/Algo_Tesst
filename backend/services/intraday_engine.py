from __future__ import annotations

import json
import logging
import os

import pyarrow as pa

logger = logging.getLogger(__name__)

INTRADAY_DATA_DIR = os.environ.get("INTRADAY_DATA_DIR", "/data/intraday")

_TRADESHEET_SCHEMA = pa.schema([
    pa.field("date", pa.string()),
    pa.field("symbol", pa.string()),
    pa.field("expiry", pa.string()),
    pa.field("strike", pa.float64()),
    pa.field("opt_type", pa.string()),
    pa.field("action", pa.string()),
    pa.field("entry_time", pa.string()),
    pa.field("entry_price", pa.float64()),
    pa.field("exit_time", pa.string()),
    pa.field("exit_price", pa.float64()),
    pa.field("exit_reason", pa.string()),
    pa.field("quantity", pa.uint32()),
    pa.field("pnl", pa.float64()),
    pa.field("mae", pa.float64()),
    pa.field("mfe", pa.float64()),
])

_native = None


def _get_native():
    global _native
    if _native is None:
        import algotest_native as mod
        _native = mod
    return _native


def run_intraday_backtest(config: dict, *, data_dir: str | None = None) -> bytes:
    """Run intraday backtest. Returns Arrow IPC stream bytes."""
    if data_dir is None:
        data_dir = INTRADAY_DATA_DIR

    native = _get_native()
    trade_rows: list[dict] = native.run_intraday_backtest(json.dumps(config), data_dir)

    if trade_rows:
        table = pa.Table.from_pylist(trade_rows, schema=_TRADESHEET_SCHEMA)
    else:
        table = pa.table({f.name: pa.array([], type=f.type) for f in _TRADESHEET_SCHEMA},
                         schema=_TRADESHEET_SCHEMA)

    sink = pa.BufferOutputStream()
    writer = pa.ipc.new_stream(sink, table.schema)
    writer.write_table(table)
    writer.close()
    return sink.getvalue().to_pybytes()
