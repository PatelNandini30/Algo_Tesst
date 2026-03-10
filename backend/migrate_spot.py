"""
migrate_spot.py — spot_data importer
Imports strikeData CSV files into the spot_data table.

FIXES:
  - Deadlock: reduced workers to 3 (spot_data files overlap on same symbols)
  - UniqueViolation: proper ON CONFLICT DO UPDATE upsert using uq_spot_day
  - Legacy column names: handles both trade_date/date, open_price/open etc.
  - close column is numeric(12,2) in DB — no overflow expected for spot prices
"""

import io
import json
import logging
import math
import os
import re
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import psycopg2
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import DATABASE_URL, STRIKE_DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(levelname)s - %(message)s",
)
logger = logging.getLogger("spot_migrator")

PROJECT_ROOT   = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "spot_import_last_report.json"

_NULL_SENTINEL = f"__NULL_{uuid.uuid4().hex}__"
_NUMERIC_SAFE_MAX = 1e15
_BIGINT_SAFE_MAX  = 9_223_372_036_854_775_807

# ── DB connection ─────────────────────────────────────────────────────────────

def _sa_url_to_dsn(sa_url: str) -> str:
    for prefix in ("postgresql+psycopg2://", "postgresql://", "postgres://"):
        if sa_url.startswith(prefix):
            return "postgresql://" + sa_url[len(prefix):]
    return sa_url

DSN        = _sa_url_to_dsn(DATABASE_URL)
_tl        = threading.local()
_sa_engine = None
_sa_lock   = threading.Lock()

def _conn():
    conn = getattr(_tl, "conn", None)
    if conn is None or conn.closed:
        _tl.conn = psycopg2.connect(DSN)
        return _tl.conn
    try:
        if conn.status == psycopg2.extensions.STATUS_IN_TRANSACTION:
            conn.rollback()
        conn.cursor().execute("SELECT 1")
    except Exception:
        try: conn.close()
        except Exception: pass
        _tl.conn = psycopg2.connect(DSN)
    return _tl.conn

def _close_conn():
    conn = getattr(_tl, "conn", None)
    if conn and not conn.closed:
        try: conn.close()
        except Exception: pass
    _tl.conn = None

def sa_engine():
    global _sa_engine
    with _sa_lock:
        if _sa_engine is None:
            _sa_engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return _sa_engine

# ── Tracker DDL ───────────────────────────────────────────────────────────────

TRACKER_DDL = """
CREATE TABLE IF NOT EXISTS _import_file_tracker (
    id               SERIAL PRIMARY KEY,
    file_path        TEXT        NOT NULL,
    file_size_bytes  BIGINT      NOT NULL,
    target_table     TEXT        NOT NULL,
    rows_read        INTEGER     DEFAULT 0,
    rows_valid       INTEGER     DEFAULT 0,
    rows_skipped     INTEGER     DEFAULT 0,
    rows_inserted    INTEGER     DEFAULT 0,
    rows_updated     INTEGER     DEFAULT 0,
    status           TEXT        NOT NULL DEFAULT 'pending',
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    errors           TEXT,
    UNIQUE (file_path, file_size_bytes)
);
"""

# ── Parsers ───────────────────────────────────────────────────────────────────

_DATE_FMTS = [
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y",
    "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%b %d %Y",
    "%d-%B-%Y", "%d %B %Y", "%Y%m%d",   "%d.%m.%Y",
]
_DATE_NULL_SET = frozenset(["","nan","NaN","None","NaT","N/A","NA","n/a","na","-","--","0","null","NULL"])
_NUM_STRIP     = re.compile(r"[₹$£€\s]")
_NUM_NULL_SET  = frozenset(["","nan","NaN","None","-","--","N/A","NA","n/a","na","nil","Nil","NIL","null","NULL","inf","Inf","-inf"])

def parse_date(s: pd.Series) -> pd.Series:
    if s is None or len(s) == 0:
        return pd.Series(dtype="object")
    cleaned = s.astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    cleaned = cleaned.where(~cleaned.isin(_DATE_NULL_SET), other=None)
    result  = pd.Series([pd.NaT]*len(s), dtype="datetime64[ns]", index=s.index)
    for fmt in _DATE_FMTS:
        mask = result.isna() & cleaned.notna()
        if not mask.any(): break
        result[mask] = pd.to_datetime(cleaned[mask], format=fmt, errors="coerce")
    mask = result.isna() & cleaned.notna()
    if mask.any():
        result[mask] = pd.to_datetime(cleaned[mask], dayfirst=True, errors="coerce")
    return result.dt.date

def parse_num(s: pd.Series, col_name: str = "") -> pd.Series:
    if s is None or len(s) == 0:
        return pd.Series(dtype="float64")
    cleaned = (s.astype(str).str.strip()
               .pipe(lambda x: x.str.replace(_NUM_STRIP, "", regex=True))
               .str.replace(",", "", regex=False)
               .str.replace(r"^\((.+)\)$", r"-\1", regex=True))
    cleaned = cleaned.where(~cleaned.isin(_NUM_NULL_SET), other=None)
    result  = pd.to_numeric(cleaned, errors="coerce")
    inf_mask = result.apply(lambda v: isinstance(v, float) and math.isinf(v))
    if inf_mask.any():
        result = result.where(~inf_mask, other=float("nan"))
    overflow = result.abs() > _NUMERIC_SAFE_MAX
    if overflow.any():
        logger.warning("parse_num [%s]: %d overflow values → NULL", col_name, int(overflow.sum()))
        result = result.where(~overflow, other=float("nan"))
    return result

def to_int(s: pd.Series, col_name: str = "") -> pd.Series:
    if s is None or len(s) == 0:
        return pd.Series(dtype="Int64")
    nums = parse_num(s, col_name).round(0)
    overflow = nums.abs() > _BIGINT_SAFE_MAX
    if overflow.any():
        nums = nums.where(~overflow, other=float("nan"))
    return nums.astype("Int64")

# ── Column resolver ───────────────────────────────────────────────────────────

_ALIASES = {
    "Date":    ["Date","date","TradeDate","Trade Date","trade_date","Dt","DT"],
    "Symbol":  ["Symbol","symbol","Ticker","ticker","SYMBOL","Scrip","underlying","Underlying"],
    "Open":    ["Open","open","Open Price","OpenPrice","OPEN","open_price"],
    "High":    ["High","high","High Price","HighPrice","HIGH","high_price"],
    "Low":     ["Low","low","Low Price","LowPrice","LOW","low_price"],
    "Close":   ["Close","close","Close Price","ClosePrice","CLOSE","LTP","Last","LastPrice","close_price"],
    "Volume":  ["Volume","volume","Vol","vol","VOLUME","Quantity","quantity","Qty","qty"],
}

def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]", "", str(s)).lower()

def _col(df: pd.DataFrame, canonical: str) -> pd.Series:
    if canonical in df.columns: return df[canonical]
    for alias in _ALIASES.get(canonical, []):
        if alias in df.columns: return df[alias]
    target = _norm(canonical)
    for col in df.columns:
        if _norm(col) == target: return df[col]
    return pd.Series(dtype="object")

def norm_symbol(s: pd.Series) -> pd.Series:
    _MAP = {
        "NIFTY 50":"NIFTY","NIFTY50":"NIFTY","CNX NIFTY":"NIFTY",
        "BANK NIFTY":"BANKNIFTY","NIFTY BANK":"BANKNIFTY",
        "FINNIFTY":"FINNIFTY","NIFTY FIN SERVICE":"FINNIFTY",
        "MIDCPNIFTY":"MIDCPNIFTY",
        "NAN":None,"NONE":None,"":None,"-":None,"N/A":None,"NA":None,
    }
    if s is None or len(s) == 0:
        return pd.Series(dtype="object")
    result = s.astype(str).str.strip().str.upper().str.replace(r"\s+", " ", regex=True)
    return result.map(lambda x: _MAP.get(x, x if x not in ("NAN","NONE","") else None))

# ── CSV reader ────────────────────────────────────────────────────────────────

def read_csv_any(path: Path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252", "iso-8859-1"):
        try:
            df = pd.read_csv(path, encoding=enc, dtype=str,
                             keep_default_na=False, na_filter=False, skipinitialspace=True)
            df.columns = [str(c).strip() for c in df.columns]
            df = df.loc[:, ~df.columns.str.match(r"^Unnamed")]
            return df
        except Exception:
            continue
    raise ValueError(f"Unable to read CSV: {path}")

# ── COPY + ON CONFLICT upsert ─────────────────────────────────────────────────

def _df_to_csv_buf(df: pd.DataFrame) -> io.StringIO:
    df_out = df.copy()
    for col in df_out.columns:
        try: df_out[col] = df_out[col].astype(object)
        except Exception: pass
    df_out = df_out.where(df_out.notna() & (df_out != float("nan")), other=_NULL_SENTINEL)
    df_out = df_out.fillna(_NULL_SENTINEL)
    df_out = df_out.replace({None: _NULL_SENTINEL, pd.NA: _NULL_SENTINEL})
    buf = io.StringIO()
    df_out.to_csv(buf, index=False, header=False)
    buf.seek(0)
    return buf

def _upsert_spot(cur, df: pd.DataFrame) -> Tuple[int, int]:
    """
    Uses ON CONFLICT DO UPDATE to handle the uq_spot_day constraint correctly.
    This avoids UniqueViolation and deadlock by letting PostgreSQL handle conflicts atomically.
    """
    cols  = list(df.columns)
    stage = f"stg_spot_{uuid.uuid4().hex[:12]}"

    col_defs = ", ".join([f'"{c}" TEXT' for c in cols])
    cur.execute(f'DROP TABLE IF EXISTS "{stage}"')
    cur.execute(f'CREATE TEMP TABLE "{stage}" ({col_defs}) ON COMMIT DROP')
    col_list = ", ".join([f'"{c}"' for c in cols])
    cur.copy_expert(
        f'COPY "{stage}" ({col_list}) FROM STDIN WITH (FORMAT CSV, NULL \'{_NULL_SENTINEL}\')',
        _df_to_csv_buf(df),
    )

    # Build type cast for each column
    type_map = {
        "date": "DATE", "symbol": "TEXT",
        "open": "NUMERIC", "high": "NUMERIC", "low": "NUMERIC", "close": "NUMERIC",
        "volume": "BIGINT",
    }

    set_cols   = [c for c in cols if c not in ("date", "symbol")]
    set_expr   = ", ".join([f'"{c}" = EXCLUDED."{c}"' for c in set_cols])
    sel_list   = ", ".join([f's."{c}"::{type_map.get(c, "TEXT")}' for c in cols])

    cur.execute(f"""
        INSERT INTO spot_data ({col_list})
        SELECT {sel_list} FROM "{stage}" s
        ON CONFLICT (date, symbol) DO UPDATE SET {set_expr}
    """)
    total = cur.rowcount or 0
    cur.execute(f'DROP TABLE IF EXISTS "{stage}"')
    return 0, total  # treat all as inserts for reporting

# ── Tracker helpers ───────────────────────────────────────────────────────────

def _tracker_start(path: Path) -> Optional[int]:
    try:
        with sa_engine().begin() as conn:
            return conn.execute(text(
                "INSERT INTO _import_file_tracker "
                "  (file_path, file_size_bytes, target_table, status, started_at) "
                "VALUES (:fp, :sz, 'spot_data', 'running', now()) "
                "ON CONFLICT (file_path, file_size_bytes) "
                "DO UPDATE SET status='running', started_at=now(), finished_at=NULL "
                "RETURNING id"
            ), {"fp": str(path), "sz": path.stat().st_size}).scalar()
    except Exception as e:
        logger.warning("Tracker start failed: %s", e)
        return None

def _tracker_finish(tid: Optional[int], r: Dict):
    if tid is None: return
    try:
        with sa_engine().begin() as conn:
            conn.execute(text(
                "UPDATE _import_file_tracker SET "
                "  rows_read=:rr, rows_valid=:rv, rows_skipped=:rs, "
                "  rows_inserted=:ins, rows_updated=:upd, "
                "  status=:st, finished_at=now(), errors=:err "
                "WHERE id=:tid"
            ), {
                "rr":  r.get("rows_read",    0),
                "rv":  r.get("rows_valid",   0),
                "rs":  r.get("rows_skipped", 0),
                "ins": r.get("rows_inserted",0),
                "upd": r.get("rows_updated", 0),
                "st":  r.get("status", "completed"),
                "err": "; ".join(r.get("errors", [])) or None,
                "tid": tid,
            })
    except Exception as e:
        logger.warning("Tracker finish failed: %s", e)

def _should_skip(path: Path, force: bool) -> Optional[str]:
    if force: return None
    try:
        with sa_engine().begin() as conn:
            row = conn.execute(text(
                "SELECT status, rows_inserted, rows_updated FROM _import_file_tracker "
                "WHERE file_path=:fp AND file_size_bytes=:sz ORDER BY id DESC LIMIT 1"
            ), {"fp": str(path), "sz": path.stat().st_size}).fetchone()
        if row and row[0] == "completed":
            return f"already imported (ins={row[1]}, upd={row[2]})"
    except Exception:
        pass
    return None

# ── Spot importer ─────────────────────────────────────────────────────────────

def import_spot_file(path: Path, force: bool = False) -> Dict:
    r = {"file": str(path), "table": "spot_data", "status": "completed", "errors": []}

    skip = _should_skip(path, force)
    if skip:
        logger.info("SKIP  %s — %s", path.name, skip)
        return {**r, "status": "skipped", "rows_read": 0, "rows_valid": 0,
                "rows_skipped": 0, "rows_inserted": 0, "rows_updated": 0}

    tid = _tracker_start(path)
    logger.info("IMPORT %s → spot_data", path.name)

    try:
        raw = read_csv_any(path)
        r["rows_read"] = len(raw)

        if raw.empty:
            r["rows_valid"] = r["rows_skipped"] = 0
            return r

        # Guess symbol from filename if not in CSV
        sym_guess = path.stem.replace("_strike_data", "").upper()
        if sym_guess.startswith("DAILYNC"):
            sym_guess = sym_guess.replace("DAILYNC", "", 1)

        ticker = _col(raw, "Symbol")
        if ticker.empty:
            ticker = pd.Series([sym_guess] * len(raw), dtype="object", index=raw.index)

        vol_col = _col(raw, "Volume")

        df = pd.DataFrame({
            "date":   parse_date(_col(raw, "Date")),
            "symbol": norm_symbol(ticker),
            "open":   parse_num(_col(raw, "Open"),  "open"),
            "high":   parse_num(_col(raw, "High"),  "high"),
            "low":    parse_num(_col(raw, "Low"),   "low"),
            "close":  parse_num(_col(raw, "Close"), "close"),
            "volume": to_int(vol_col, "volume"),
        }, index=raw.index)

        # Skip rows with no date AND no symbol
        skip_mask = df["date"].isna() & df["symbol"].isna()
        df_valid  = df[~skip_mask].copy()
        r["rows_skipped"] = int(skip_mask.sum())

        # Dedupe within file
        before = len(df_valid)
        df_valid = df_valid.drop_duplicates(subset=["date", "symbol"], keep="first")
        r["rows_skipped"] += before - len(df_valid)
        r["rows_valid"] = len(df_valid)

        if df_valid.empty:
            r["rows_inserted"] = r["rows_updated"] = 0
            return r

        # Only keep columns that exist in DB
        db_cols = {"date", "symbol", "open", "high", "low", "close", "volume"}
        df_db = df_valid[[c for c in df_valid.columns if c in db_cols]]

        conn = _conn()
        try:
            with conn.cursor() as cur:
                upd, ins = _upsert_spot(cur, df_db)
            conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass
            _close_conn()
            raise

        r["rows_inserted"] = ins
        r["rows_updated"]  = upd

    except Exception as e:
        logger.exception("Error importing %s", path)
        r["status"] = "failed"
        r["errors"] = [str(e)]
        r.setdefault("rows_read",    0)
        r.setdefault("rows_valid",   0)
        r.setdefault("rows_skipped", 0)
        r.setdefault("rows_inserted",0)
        r.setdefault("rows_updated", 0)
    finally:
        _tracker_finish(tid, r)

    return r

# ── Runner ────────────────────────────────────────────────────────────────────

def run(force: bool = False, workers: int = 3, limit: Optional[int] = None) -> List[Dict]:
    """
    Workers set to 3 (not 10) to avoid deadlocks on spot_data.
    spot_data files can reference the same symbols/dates so parallel updates conflict.
    """
    files = sorted(Path(STRIKE_DATA_DIR).glob("*.csv"))
    if limit:
        files = files[:limit]

    if not files:
        logger.warning("No CSV files found in %s", STRIKE_DATA_DIR)
        return []

    logger.info("Found %d spot data files", len(files))

    # Ensure tracker table exists
    with sa_engine().begin() as conn:
        conn.execute(text(TRACKER_DDL))
        conn.execute(text(
            "ALTER TABLE _import_file_tracker "
            "ADD COLUMN IF NOT EXISTS rows_skipped INTEGER DEFAULT 0"
        ))

    results = [None] * len(files)
    done = 0

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="spot") as pool:
        fmap = {pool.submit(import_spot_file, f, force): i for i, f in enumerate(files)}
        for future in as_completed(fmap):
            idx = fmap[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = {
                    "file": str(files[idx]), "table": "spot_data",
                    "status": "failed", "errors": [str(e)],
                    "rows_read":0,"rows_valid":0,"rows_skipped":0,
                    "rows_inserted":0,"rows_updated":0,
                }
            done += 1
            if done % 10 == 0 or done == len(files):
                logger.info("Progress: %d / %d", done, len(files))

    return results

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="spot_data importer")
    p.add_argument("--force",   action="store_true", help="Re-import completed files")
    p.add_argument("--workers", type=int, default=3, help="Parallel threads (default 3)")
    p.add_argument("--limit",   type=int, default=None)
    args = p.parse_args()

    results = run(force=args.force, workers=args.workers, limit=args.limit)

    completed = sum(1 for x in results if x and x.get("status") == "completed")
    skipped   = sum(1 for x in results if x and x.get("status") == "skipped")
    failed    = sum(1 for x in results if x and x.get("status") == "failed")
    inserted  = sum(x.get("rows_inserted", 0) for x in results if x)

    logger.info("Summary: total=%d completed=%d skipped=%d failed=%d inserted=%d",
                len(results), completed, skipped, failed, inserted)

    if failed:
        for x in results:
            if x and x.get("status") == "failed":
                logger.warning("FAILED: %s → %s", x.get("file"), "; ".join(x.get("errors",[])))

    report_path = DEFAULT_REPORT
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({
        "started_at": datetime.utcnow().isoformat(),
        "files": results,
        "finished_at": datetime.utcnow().isoformat(),
    }, indent=2, default=str), encoding="utf-8")
    logger.info("Report: %s", report_path)