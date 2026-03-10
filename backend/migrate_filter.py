"""
migrate_str.py — super_trend_segments importer
Imports Filter/STR*.csv files into super_trend_segments table.

CSV FORMAT:
  Only two columns: Start and End (date ranges)
  No trend column — DB trend column gets default value 'unknown'

FIXES:
  - trend column is NOT NULL → defaults to 'unknown'
  - ON CONFLICT DO UPDATE to handle uq_str_segment (symbol, config, start_date)
  - Handles 19+ date formats robustly
  - Auto-swaps inverted date ranges
  - Deduplication within file
"""

import io
import json
import logging
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
from database import DATABASE_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(levelname)s - %(message)s",
)
logger = logging.getLogger("str_migrator")

PROJECT_ROOT   = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FILTER_DIR     = PROJECT_ROOT / "Filter"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "str_import_last_report.json"

_NULL_SENTINEL = f"__NULL_{uuid.uuid4().hex}__"

# DB connection

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

# Date parser

_DATE_FMTS = [
    "%d-%m-%Y",  "%Y-%m-%d",  "%d/%m/%Y",  "%m/%d/%Y",
    "%Y/%m/%d",  "%d-%b-%Y",  "%d %b %Y",  "%b %d %Y",
    "%d-%B-%Y",  "%d %B %Y",  "%Y%m%d",    "%d.%m.%Y",
    "%m-%d-%Y",  "%b-%d-%Y",  "%B-%d-%Y",  "%d%m%Y",
    "%Y-%b-%d",  "%d %b, %Y", "%b %d, %Y",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
]
_DATE_NULL_SET = frozenset([
    "", "nan", "NaN", "None", "NaT", "nat", "N/A", "NA",
    "n/a", "na", "-", "--", "0", "null", "NULL", "Null",
    "0000-00-00", "00/00/0000", "00-00-0000",
])

def parse_date(s: pd.Series) -> pd.Series:
    if s is None or len(s) == 0:
        return pd.Series(dtype="object")
    cleaned = s.astype(str).str.strip().str.replace(r"\s+", " ", regex=True).str.replace(r"\t", "", regex=True)
    cleaned = cleaned.where(~cleaned.isin(_DATE_NULL_SET), other=None)
    result  = pd.Series([pd.NaT] * len(s), dtype="datetime64[ns]", index=s.index)
    for fmt in _DATE_FMTS:
        mask = result.isna() & cleaned.notna()
        if not mask.any(): break
        result[mask] = pd.to_datetime(cleaned[mask], format=fmt, errors="coerce")
    mask = result.isna() & cleaned.notna()
    if mask.any():
        result[mask] = pd.to_datetime(cleaned[mask], dayfirst=True, errors="coerce")
    bad = result.isna() & cleaned.notna()
    if bad.any():
        logger.warning("Unparseable dates -> NULL: %s", cleaned[bad].dropna().head(5).tolist())
    return result.dt.date

_START_ALIASES = ["Start","start","StartDate","Start Date","start_date","FROM","From","from_date","FROM_DATE","Start_Date","StartDt","FromDate","Begin","begin"]
_END_ALIASES   = ["End","end","EndDate","End Date","end_date","TO","To","to_date","TO_DATE","End_Date","EndDt","ToDate","TillDate","Finish","finish"]

def _find_col(df: pd.DataFrame, aliases: List[str]) -> pd.Series:
    for alias in aliases:
        if alias in df.columns: return df[alias]
    target_set = {re.sub(r"[\s_\-]", "", a).lower() for a in aliases}
    for col in df.columns:
        if re.sub(r"[\s_\-]", "", col).lower() in target_set:
            return df[col]
    return pd.Series(dtype="object")

def read_csv_any(path: Path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252", "iso-8859-1"):
        for sep in (",", "\t", ";"):
            try:
                df = pd.read_csv(path, encoding=enc, sep=sep, dtype=str,
                                 keep_default_na=False, na_filter=False, skipinitialspace=True)
                df.columns = [str(c).strip() for c in df.columns]
                df = df.loc[:, ~df.columns.str.match(r"^Unnamed")]
                if len(df.columns) >= 2:
                    return df
            except Exception:
                continue
    raise ValueError(f"Unable to read CSV: {path}")

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

def _upsert_str(cur, df: pd.DataFrame) -> Tuple[int, int]:
    cols  = list(df.columns)
    stage = f"stg_str_{uuid.uuid4().hex[:12]}"
    col_defs = ", ".join([f'"{c}" TEXT' for c in cols])
    cur.execute(f'DROP TABLE IF EXISTS "{stage}"')
    cur.execute(f'CREATE TEMP TABLE "{stage}" ({col_defs}) ON COMMIT DROP')
    col_list = ", ".join([f'"{c}"' for c in cols])
    cur.copy_expert(
        f'COPY "{stage}" ({col_list}) FROM STDIN WITH (FORMAT CSV, NULL \'{_NULL_SENTINEL}\')',
        _df_to_csv_buf(df),
    )
    type_map = {"symbol":"TEXT","config":"TEXT","start_date":"DATE","end_date":"DATE","trend":"TEXT"}
    sel_list = ", ".join([f's."{c}"::{type_map.get(c,"TEXT")}' for c in cols])
    cur.execute(f"""
        INSERT INTO super_trend_segments ({col_list})
        SELECT {sel_list} FROM "{stage}" s
        ON CONFLICT (symbol, config, start_date)
        DO UPDATE SET end_date = EXCLUDED.end_date, trend = EXCLUDED.trend
    """)
    total = cur.rowcount or 0
    cur.execute(f'DROP TABLE IF EXISTS "{stage}"')
    return 0, total

def _tracker_start(path: Path) -> Optional[int]:
    try:
        with sa_engine().begin() as conn:
            return conn.execute(text(
                "INSERT INTO _import_file_tracker (file_path,file_size_bytes,target_table,status,started_at) "
                "VALUES (:fp,:sz,'super_trend_segments','running',now()) "
                "ON CONFLICT (file_path,file_size_bytes) DO UPDATE SET status='running',started_at=now(),finished_at=NULL "
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
                "UPDATE _import_file_tracker SET rows_read=:rr,rows_valid=:rv,rows_skipped=:rs,"
                "rows_inserted=:ins,rows_updated=:upd,status=:st,finished_at=now(),errors=:err WHERE id=:tid"
            ), {"rr":r.get("rows_read",0),"rv":r.get("rows_valid",0),"rs":r.get("rows_skipped",0),
                "ins":r.get("rows_inserted",0),"upd":r.get("rows_updated",0),
                "st":r.get("status","completed"),"err":"; ".join(r.get("errors",[])) or None,"tid":tid})
    except Exception as e:
        logger.warning("Tracker finish failed: %s", e)

def _should_skip(path: Path, force: bool) -> Optional[str]:
    if force: return None
    try:
        with sa_engine().begin() as conn:
            row = conn.execute(text(
                "SELECT status,rows_inserted,rows_updated FROM _import_file_tracker "
                "WHERE file_path=:fp AND file_size_bytes=:sz ORDER BY id DESC LIMIT 1"
            ), {"fp": str(path), "sz": path.stat().st_size}).fetchone()
        if row and row[0] == "completed":
            return f"already imported (ins={row[1]}, upd={row[2]})"
    except Exception:
        pass
    return None

def import_str_file(path: Path, force: bool = False) -> Dict:
    r = {"file":str(path),"table":"super_trend_segments","status":"completed","errors":[],
         "rows_read":0,"rows_valid":0,"rows_skipped":0,"rows_inserted":0,"rows_updated":0}

    skip = _should_skip(path, force)
    if skip:
        logger.info("SKIP  %s — %s", path.name, skip)
        return {**r, "status": "skipped"}

    tid = _tracker_start(path)
    logger.info("IMPORT %s -> super_trend_segments", path.name)

    try:
        raw = read_csv_any(path)
        r["rows_read"] = len(raw)
        if raw.empty:
            return r

        # Config from filename: STR5,1_5,1.csv -> "5x1"
        cfg = path.stem.replace("STR","").split("_")[0].replace(",","x")

        start_series = _find_col(raw, _START_ALIASES)
        end_series   = _find_col(raw, _END_ALIASES)

        if start_series.empty or end_series.empty:
            raise ValueError(f"Could not find Start/End columns. Found: {list(raw.columns)}")

        df = pd.DataFrame({
            "symbol":     "NIFTY",
            "config":     cfg,
            "start_date": parse_date(start_series),
            "end_date":   parse_date(end_series),
            "trend":      "unknown",  # CSV has no trend — NOT NULL constraint satisfied
        }, index=raw.index)

        # Auto-swap inverted ranges
        inverted = df["start_date"].notna() & df["end_date"].notna() & (df["end_date"] < df["start_date"])
        if inverted.any():
            logger.info("%s: %d inverted ranges auto-swapped", path.name, int(inverted.sum()))
            df.loc[inverted, ["start_date","end_date"]] = df.loc[inverted, ["end_date","start_date"]].values

        skip_mask = df["start_date"].isna() & df["end_date"].isna()
        df_valid  = df[~skip_mask].copy()
        r["rows_skipped"] = int(skip_mask.sum())

        before   = len(df_valid)
        df_valid = df_valid.drop_duplicates(subset=["symbol","config","start_date","end_date"], keep="first")
        r["rows_skipped"] += before - len(df_valid)
        r["rows_valid"] = len(df_valid)

        if df_valid.empty:
            return r

        logger.info("%s: %d valid rows (config=%s)", path.name, len(df_valid), cfg)

        conn = _conn()
        try:
            with conn.cursor() as cur:
                upd, ins = _upsert_str(cur, df_valid)
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
    finally:
        _tracker_finish(tid, r)

    return r

def run(force: bool = False, workers: int = 2) -> List[Dict]:
    files = sorted(FILTER_DIR.glob("STR*.csv"))
    if not files:
        logger.warning("No STR*.csv files found in %s", FILTER_DIR)
        return []
    logger.info("Found %d STR files: %s", len(files), [f.name for f in files])
    with sa_engine().begin() as conn:
        conn.execute(text(TRACKER_DDL))
        conn.execute(text("ALTER TABLE _import_file_tracker ADD COLUMN IF NOT EXISTS rows_skipped INTEGER DEFAULT 0"))
    results = [None] * len(files)
    done = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="str") as pool:
        fmap = {pool.submit(import_str_file, f, force): i for i, f in enumerate(files)}
        for future in as_completed(fmap):
            idx = fmap[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = {"file":str(files[idx]),"table":"super_trend_segments","status":"failed",
                                "errors":[str(e)],"rows_read":0,"rows_valid":0,"rows_skipped":0,"rows_inserted":0,"rows_updated":0}
            done += 1
            logger.info("Progress: %d / %d", done, len(files))
    return results

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="super_trend_segments importer")
    p.add_argument("--force",   action="store_true")
    p.add_argument("--workers", type=int, default=2)
    args = p.parse_args()

    results = run(force=args.force, workers=args.workers)
    completed = sum(1 for x in results if x and x.get("status") == "completed")
    skipped   = sum(1 for x in results if x and x.get("status") == "skipped")
    failed    = sum(1 for x in results if x and x.get("status") == "failed")
    inserted  = sum(x.get("rows_inserted",0) for x in results if x)
    logger.info("Summary: total=%d completed=%d skipped=%d failed=%d inserted=%d",
                len(results), completed, skipped, failed, inserted)
    if failed:
        for x in results:
            if x and x.get("status") == "failed":
                logger.warning("FAILED: %s -> %s", x.get("file"), "; ".join(x.get("errors",[])))
    report_path = DEFAULT_REPORT
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"started_at":datetime.utcnow().isoformat(),"files":results,
                                        "finished_at":datetime.utcnow().isoformat()},indent=2,default=str),encoding="utf-8")
    logger.info("Report: %s", report_path)