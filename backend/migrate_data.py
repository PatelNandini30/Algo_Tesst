"""
Safe CSV -> PostgreSQL importer for this repo.

Features:
- full/per-table/per-file runs
- idempotent update+insert behavior
- invalid-row and duplicate detection
- JSON import report
- backward-compatible legacy flags
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import DATABASE_URL, CLEANED_CSV_DIR, EXPIRY_DATA_DIR, STRIKE_DATA_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("csv_migrator")

PROJECT_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FILTER_DIR = PROJECT_ROOT / "Filter"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "csv_import_last_report.json"


def read_csv_any(path: Path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception:
            continue
    raise ValueError(f"Unable to read CSV: {path}")


def parse_date(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip().replace({"": None, "nan": None, "NaN": None, "None": None})
    d = pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    if d.notna().sum() == 0:
        d = pd.to_datetime(s, format="%d-%m-%Y", errors="coerce")
    if d.notna().sum() == 0:
        d = pd.to_datetime(s, format="%d-%b-%Y", errors="coerce")
    if d.notna().sum() == 0:
        d = pd.to_datetime(s, dayfirst=True, errors="coerce")
    return d.dt.date


def parse_num(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip().str.replace(",", "", regex=False)
    s = s.replace({"": None, "nan": None, "NaN": None, "None": None})
    return pd.to_numeric(s, errors="coerce")


def to_nullable_int(s: pd.Series) -> Tuple[pd.Series, int]:
    """
    Convert numeric series to pandas nullable Int64 safely.
    Non-integer numeric values are set to NA and counted.
    """
    n = parse_num(s)
    frac_mask = n.notna() & ((n % 1) != 0)
    bad = int(frac_mask.sum())
    if bad:
        n.loc[frac_mask] = pd.NA
    return n.astype("Int64"), bad


def parse_time(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip().replace({"": None, "nan": None, "NaN": None, "None": None})
    t = pd.to_datetime(s, format="%H:%M:%S", errors="coerce")
    if t.notna().sum() == 0:
        t = pd.to_datetime(s, format="%H:%M", errors="coerce")
    if t.notna().sum() == 0:
        t = pd.to_datetime(s, errors="coerce")
    return t.dt.time


def norm_symbol(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.strip()
        .str.upper()
        .replace({"NIFTY 50": "NIFTY", "NIFTY50": "NIFTY"})
    )


def reject_missing(df: pd.DataFrame, req: List[str]) -> Tuple[pd.DataFrame, int]:
    m = pd.Series(True, index=df.index)
    for c in req:
        if c not in df.columns:
            return df.iloc[0:0], len(df)
        m = m & df[c].notna()
        if df[c].dtype == "object":
            m = m & (df[c].astype(str).str.strip() != "")
    return df[m].copy(), int((~m).sum())


def dedupe(df: pd.DataFrame, key: List[str]) -> Tuple[pd.DataFrame, int]:
    before = len(df)
    out = df.drop_duplicates(subset=key, keep="last")
    return out, before - len(out)


class Migrator:
    def __init__(self, dry_run: bool = False):
        self.engine = create_engine(DATABASE_URL)
        self.dry_run = dry_run
        self._cols_cache: Dict[str, set] = {}

    def table_columns(self, table_name: str) -> set:
        if table_name in self._cols_cache:
            return self._cols_cache[table_name]
        q = text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:t
            """
        )
        with self.engine.begin() as conn:
            cols = {r[0] for r in conn.execute(q, {"t": table_name}).fetchall()}
        self._cols_cache[table_name] = cols
        return cols

    def _legacy_col(self, cols: set, new_name: str, old_name: str) -> str:
        return new_name if new_name in cols else old_name

    def align_df_to_table(self, table_name: str, df: pd.DataFrame) -> pd.DataFrame:
        cols = self.table_columns(table_name)
        if not cols:
            return df.iloc[0:0].copy()

        if table_name == "option_data":
            date_col = self._legacy_col(cols, "trade_date", "date")
            close_col = self._legacy_col(cols, "close_price", "close")
            open_col = self._legacy_col(cols, "open_price", "open")
            high_col = self._legacy_col(cols, "high_price", "high")
            low_col = self._legacy_col(cols, "low_price", "low")
            out = pd.DataFrame()
            out[date_col] = df["trade_date"]
            out["expiry_date"] = df["expiry_date"]
            out["instrument"] = df["instrument"]
            out["symbol"] = df["symbol"]
            out["strike_price"] = df["strike_price"]
            out["option_type"] = df["option_type"]
            out[open_col] = df["open_price"]
            out[high_col] = df["high_price"]
            out[low_col] = df["low_price"]
            out[close_col] = df["close_price"]
            out["settled_price"] = df["settled_price"]
            out["contracts"] = df["contracts"]
            out["turnover"] = df["turnover"]
            out["open_interest"] = df["open_interest"]
            return out[[c for c in out.columns if c in cols]]

        if table_name == "spot_data":
            date_col = self._legacy_col(cols, "trade_date", "date")
            close_col = self._legacy_col(cols, "close_price", "close")
            open_col = self._legacy_col(cols, "open_price", "open")
            high_col = self._legacy_col(cols, "high_price", "high")
            low_col = self._legacy_col(cols, "low_price", "low")
            out = pd.DataFrame()
            out[date_col] = df["trade_date"]
            out["symbol"] = df["symbol"]
            out[open_col] = df["open_price"]
            out[high_col] = df["high_price"]
            out[low_col] = df["low_price"]
            out[close_col] = df["close_price"]
            if "volume" in cols:
                out["volume"] = df["volume"]
            if "average_price" in cols:
                out["average_price"] = df["average_price"]
            if "supertrend_1" in cols:
                out["supertrend_1"] = df["supertrend_1"]
            if "supertrend_2" in cols:
                out["supertrend_2"] = df["supertrend_2"]
            if "supertrend_3" in cols:
                out["supertrend_3"] = df["supertrend_3"]
            if "trade_time" in cols:
                out["trade_time"] = df["trade_time"]
            return out[[c for c in out.columns if c in cols]]

        return df[[c for c in df.columns if c in cols]].copy()

    def stage_df(self, conn, df: pd.DataFrame, stage_table: str):
        df.to_sql(stage_table, conn, if_exists="replace", index=False, method="multi")

    def update_insert(
        self,
        conn,
        stage: str,
        target: str,
        key_cols: List[str],
        all_cols: List[str],
        null_safe_cols: Optional[List[str]] = None,
    ) -> Tuple[int, int]:
        null_safe_cols = set(null_safe_cols or [])
        cond = []
        for c in key_cols:
            if c in null_safe_cols:
                cond.append(f"((t.{c}=s.{c}) OR (t.{c} IS NULL AND s.{c} IS NULL))")
            else:
                cond.append(f"t.{c}=s.{c}")
        where = " AND ".join(cond)

        set_cols = [c for c in all_cols if c not in key_cols]
        set_expr = ", ".join([f"{c}=s.{c}" for c in set_cols])
        upd = conn.execute(text(f"UPDATE {target} t SET {set_expr} FROM {stage} s WHERE {where}")).rowcount or 0

        ins_cols = ", ".join(all_cols)
        sel_cols = ", ".join([f"s.{c}" for c in all_cols])
        exists_cond = []
        for c in key_cols:
            if c in null_safe_cols:
                exists_cond.append(f"((t.{c}=s.{c}) OR (t.{c} IS NULL AND s.{c} IS NULL))")
            else:
                exists_cond.append(f"t.{c}=s.{c}")
        ex = " AND ".join(exists_cond)
        ins = conn.execute(
            text(
                f"INSERT INTO {target} ({ins_cols}) "
                f"SELECT {sel_cols} FROM {stage} s "
                f"WHERE NOT EXISTS (SELECT 1 FROM {target} t WHERE {ex})"
            )
        ).rowcount or 0
        return int(upd), int(ins)

    def import_option_file(self, path: Path) -> Dict:
        r = {"file": str(path), "table": "option_data", "status": "completed", "errors": []}
        raw = read_csv_any(path)
        r["rows_read"] = len(raw)
        contracts_i, bad_contracts = to_nullable_int(raw.get("Contracts", pd.Series([], dtype="object")))
        oi_i, bad_oi = to_nullable_int(raw.get("OpenInterest", pd.Series([], dtype="object")))

        df = pd.DataFrame(
            {
                "trade_date": parse_date(raw.get("Date", pd.Series([], dtype="object"))),
                "expiry_date": parse_date(raw.get("ExpiryDate", pd.Series([], dtype="object"))),
                "instrument": raw.get("Instrument", pd.Series([], dtype="object")).astype(str).str.strip().str.upper(),
                "symbol": norm_symbol(raw.get("Symbol", pd.Series([], dtype="object"))),
                "strike_price": parse_num(raw.get("StrikePrice", pd.Series([], dtype="object"))),
                "option_type": raw.get("OptionType", pd.Series([], dtype="object")).astype(str).str.strip().str.upper(),
                "open_price": parse_num(raw.get("Open", pd.Series([], dtype="object"))),
                "high_price": parse_num(raw.get("High", pd.Series([], dtype="object"))),
                "low_price": parse_num(raw.get("Low", pd.Series([], dtype="object"))),
                "close_price": parse_num(raw.get("Close", pd.Series([], dtype="object"))),
                "settled_price": parse_num(raw.get("SettledPrice", pd.Series([], dtype="object"))),
                "contracts": contracts_i,
                "turnover": parse_num(raw.get("TurnOver", pd.Series([], dtype="object"))),
                "open_interest": oi_i,
            }
        )
        df["option_type"] = df["option_type"].replace({"": None, "NAN": None, "NONE": None})
        df.loc[df["option_type"].isin(["CALL", "C"]), "option_type"] = "CE"
        df.loc[df["option_type"].isin(["PUT", "P"]), "option_type"] = "PE"
        df.loc[df["instrument"].str.startswith("FUT", na=False), "option_type"] = None

        df, rej = reject_missing(df, ["trade_date", "expiry_date", "instrument", "symbol", "strike_price", "close_price"])
        valid_inst = df["instrument"].isin(["FUTIDX", "FUTSTK", "OPTIDX", "OPTSTK"])
        bad_inst = int((~valid_inst).sum())
        df = df[valid_inst]
        df, dup = dedupe(df, ["trade_date", "symbol", "instrument", "expiry_date", "option_type", "strike_price"])
        r["rows_rejected"] = int(rej + bad_inst + bad_contracts + bad_oi)
        if bad_contracts:
            r["errors"].append(f"Contracts had non-integer values: {bad_contracts} (set to null)")
        if bad_oi:
            r["errors"].append(f"OpenInterest had non-integer values: {bad_oi} (set to null)")
        r["duplicate_rows_in_file"] = int(dup)
        r["rows_valid"] = int(len(df))
        r["rows_inserted"] = 0
        r["rows_updated"] = 0

        df_db = self.align_df_to_table("option_data", df)
        if self.dry_run or df_db.empty:
            return r
        stage = f"stg_option_{abs(hash(path.name)) % 1000000}"
        with self.engine.begin() as conn:
            self.stage_df(conn, df_db, stage)
            cols = self.table_columns("option_data")
            date_col = self._legacy_col(cols, "trade_date", "date")
            upd, ins = self.update_insert(
                conn,
                stage,
                "option_data",
                [date_col, "symbol", "instrument", "expiry_date", "option_type", "strike_price"],
                list(df_db.columns),
                ["option_type"],
            )
            conn.execute(text(f"DROP TABLE IF EXISTS {stage}"))
        r["rows_updated"], r["rows_inserted"] = upd, ins
        return r

    def import_spot_file(self, path: Path) -> Dict:
        r = {"file": str(path), "table": "spot_data", "status": "completed", "errors": []}
        raw = read_csv_any(path)
        r["rows_read"] = len(raw)
        symbol_guess = path.stem.replace("_strike_data", "").upper()
        if symbol_guess.startswith("DAILYNC"):
            symbol_guess = symbol_guess.replace("DAILYNC", "", 1)
        ticker = raw.get("Ticker", pd.Series([symbol_guess] * len(raw), dtype="object"))

        volume_i, bad_vol = to_nullable_int(raw.get("Quantity", raw.get("Volume", pd.Series([], dtype="object"))))
        df = pd.DataFrame(
            {
                "trade_date": parse_date(raw.get("Date", pd.Series([], dtype="object"))),
                "symbol": norm_symbol(ticker),
                "close_price": parse_num(raw.get("Close", pd.Series([], dtype="object"))),
                "open_price": parse_num(raw.get("Open", pd.Series([], dtype="object"))),
                "high_price": parse_num(raw.get("High", pd.Series([], dtype="object"))),
                "low_price": parse_num(raw.get("Low", pd.Series([], dtype="object"))),
                "volume": volume_i,
                "average_price": parse_num(raw.get("Average", pd.Series([], dtype="object"))),
                "supertrend_1": parse_num(raw.get("STR-1", pd.Series([], dtype="object"))),
                "supertrend_2": parse_num(raw.get("STR-2", pd.Series([], dtype="object"))),
                "supertrend_3": parse_num(raw.get("STR-3", pd.Series([], dtype="object"))),
                "trade_time": parse_time(raw.get("Time", pd.Series([], dtype="object"))),
            }
        )
        df, rej = reject_missing(df, ["trade_date", "symbol", "close_price"])
        df, dup = dedupe(df, ["trade_date", "symbol"])
        r["rows_rejected"] = int(rej + bad_vol)
        if bad_vol:
            r["errors"].append(f"Volume/Quantity had non-integer values: {bad_vol} (set to null)")
        r["duplicate_rows_in_file"] = int(dup)
        r["rows_valid"] = int(len(df))
        r["rows_inserted"] = 0
        r["rows_updated"] = 0

        df_db = self.align_df_to_table("spot_data", df)
        if self.dry_run or df_db.empty:
            return r
        stage = f"stg_spot_{abs(hash(path.name)) % 1000000}"
        with self.engine.begin() as conn:
            self.stage_df(conn, df_db, stage)
            cols = self.table_columns("spot_data")
            date_col = self._legacy_col(cols, "trade_date", "date")
            upd, ins = self.update_insert(conn, stage, "spot_data", [date_col, "symbol"], list(df_db.columns))
            conn.execute(text(f"DROP TABLE IF EXISTS {stage}"))
        r["rows_updated"], r["rows_inserted"] = upd, ins
        return r

    def import_expiry_file(self, path: Path) -> Dict:
        r = {"file": str(path), "table": "expiry_calendar", "status": "completed", "errors": []}
        raw = read_csv_any(path)
        r["rows_read"] = len(raw)
        stem = path.stem
        is_monthly = "_monthly" in stem.lower()
        symbol_guess = stem.replace("_Monthly", "").replace("_monthly", "").upper()
        symbol = raw.get("Symbol", pd.Series([symbol_guess] * len(raw), dtype="object"))
        df = pd.DataFrame(
            {
                "symbol": norm_symbol(symbol),
                "expiry_type": "monthly" if is_monthly else "weekly",
                "previous_expiry": parse_date(raw.get("Previous Expiry", pd.Series([], dtype="object"))),
                "current_expiry": parse_date(raw.get("Current Expiry", pd.Series([], dtype="object"))),
                "next_expiry": parse_date(raw.get("Next Expiry", pd.Series([], dtype="object"))),
            }
        )
        df, rej = reject_missing(df, ["symbol", "expiry_type", "current_expiry"])
        df, dup = dedupe(df, ["symbol", "expiry_type", "current_expiry"])
        r["rows_rejected"] = int(rej)
        r["duplicate_rows_in_file"] = int(dup)
        r["rows_valid"] = int(len(df))
        r["rows_inserted"] = 0
        r["rows_updated"] = 0
        if self.dry_run or df.empty:
            return r
        stage = f"stg_expiry_{abs(hash(path.name)) % 1000000}"
        with self.engine.begin() as conn:
            self.stage_df(conn, df, stage)
            upd, ins = self.update_insert(conn, stage, "expiry_calendar", ["symbol", "expiry_type", "current_expiry"], list(df.columns))
            conn.execute(text(f"DROP TABLE IF EXISTS {stage}"))
        r["rows_updated"], r["rows_inserted"] = upd, ins
        return r

    def import_holiday_file(self, path: Path) -> Dict:
        r = {"file": str(path), "table": "trading_holidays", "status": "completed", "errors": []}
        raw = read_csv_any(path)
        r["rows_read"] = len(raw)
        df = pd.DataFrame({"start_date": parse_date(raw.get("Start", pd.Series([], dtype="object"))), "end_date": parse_date(raw.get("End", pd.Series([], dtype="object"))), "reason": "data_unavailable"})
        df, rej = reject_missing(df, ["start_date", "end_date"])
        bad = (df["end_date"] < df["start_date"]).fillna(False)
        bad_cnt = int(bad.sum())
        df = df[~bad]
        df, dup = dedupe(df, ["start_date", "end_date"])
        r["rows_rejected"] = int(rej + bad_cnt)
        r["duplicate_rows_in_file"] = int(dup)
        r["rows_valid"] = int(len(df))
        r["rows_inserted"] = 0
        r["rows_updated"] = 0
        if self.dry_run or df.empty:
            return r
        stage = f"stg_holiday_{abs(hash(path.name)) % 1000000}"
        with self.engine.begin() as conn:
            self.stage_df(conn, df, stage)
            upd, ins = self.update_insert(conn, stage, "trading_holidays", ["start_date", "end_date"], list(df.columns))
            conn.execute(text(f"DROP TABLE IF EXISTS {stage}"))
        r["rows_updated"], r["rows_inserted"] = upd, ins
        return r

    def import_str_file(self, path: Path) -> Dict:
        r = {"file": str(path), "table": "super_trend_segments", "status": "completed", "errors": []}
        raw = read_csv_any(path)
        r["rows_read"] = len(raw)
        name = path.stem.replace("STR", "")
        cfg = name.split("_")[0].replace(",", "x")
        df = pd.DataFrame({"symbol": "NIFTY", "config": cfg, "start_date": parse_date(raw.get("Start", pd.Series([], dtype="object"))), "end_date": parse_date(raw.get("End", pd.Series([], dtype="object"))), "trend": None})
        df, rej = reject_missing(df, ["symbol", "config", "start_date", "end_date"])
        bad = (df["end_date"] < df["start_date"]).fillna(False)
        bad_cnt = int(bad.sum())
        df = df[~bad]
        df, dup = dedupe(df, ["symbol", "config", "start_date", "end_date"])
        r["rows_rejected"] = int(rej + bad_cnt)
        r["duplicate_rows_in_file"] = int(dup)
        r["rows_valid"] = int(len(df))
        r["rows_inserted"] = 0
        r["rows_updated"] = 0
        if self.dry_run or df.empty:
            return r
        stage = f"stg_str_{abs(hash(path.name)) % 1000000}"
        with self.engine.begin() as conn:
            self.stage_df(conn, df, stage)
            upd, ins = self.update_insert(conn, stage, "super_trend_segments", ["symbol", "config", "start_date", "end_date"], list(df.columns))
            conn.execute(text(f"DROP TABLE IF EXISTS {stage}"))
        r["rows_updated"], r["rows_inserted"] = upd, ins
        return r

    def import_output_file(self, path: Path) -> Dict:
        r = {"file": str(path), "table": "output_disabled", "status": "failed", "errors": ["Output folder import is disabled by configuration"], "rows_read": 0, "rows_valid": 0, "rows_rejected": 0, "duplicate_rows_in_file": 0, "rows_inserted": 0, "rows_updated": 0}
        return r

    def import_file(self, p: Path) -> Dict:
        ps = str(p).replace("\\", "/")
        try:
            if "/cleaned_csvs/" in ps:
                return self.import_option_file(p)
            if "/strikeData/" in ps:
                return self.import_spot_file(p)
            if "/expiryData/" in ps:
                return self.import_expiry_file(p)
            if ps.endswith("/Filter/base2.csv"):
                return self.import_holiday_file(p)
            if "/Filter/STR" in ps:
                return self.import_str_file(p)
            if "/Output/" in ps:
                return {"file": str(p), "table": "output_disabled", "status": "failed", "errors": ["Output folder import is disabled by configuration"], "rows_read": 0, "rows_valid": 0, "rows_rejected": 0, "duplicate_rows_in_file": 0, "rows_inserted": 0, "rows_updated": 0}
            return {"file": str(p), "table": "unknown", "status": "failed", "errors": ["Unsupported file path"], "rows_read": 0, "rows_valid": 0, "rows_rejected": 0, "duplicate_rows_in_file": 0, "rows_inserted": 0, "rows_updated": 0}
        except Exception as e:
            return {"file": str(p), "table": "unknown", "status": "failed", "errors": [str(e)], "rows_read": 0, "rows_valid": 0, "rows_rejected": 0, "duplicate_rows_in_file": 0, "rows_inserted": 0, "rows_updated": 0}

    def import_table(self, table_name: str, limit: Optional[int] = None) -> List[Dict]:
        if table_name == "option_data":
            files = sorted(Path(CLEANED_CSV_DIR).glob("*.csv"))
        elif table_name == "spot_data":
            files = sorted(Path(STRIKE_DATA_DIR).glob("*.csv"))
        elif table_name == "expiry_calendar":
            files = sorted(Path(EXPIRY_DATA_DIR).glob("*.csv"))
        elif table_name == "trading_holidays":
            files = [FILTER_DIR / "base2.csv"] if (FILTER_DIR / "base2.csv").exists() else []
        elif table_name == "super_trend_segments":
            files = sorted(FILTER_DIR.glob("STR*.csv"))
        else:
            raise ValueError(f"Unsupported table: {table_name}")
        if limit:
            files = files[:limit]
        return [self.import_file(f) for f in files]

    def import_all(self, limit: Optional[int] = None) -> List[Dict]:
        out = []
        for t in ["option_data", "spot_data", "expiry_calendar", "trading_holidays", "super_trend_segments"]:
            logger.info("Importing table family: %s", t)
            out.extend(self.import_table(t, limit=limit))
        return out

    def validate(self) -> Dict:
        res = {}
        with self.engine.begin() as conn:
            tables = ["option_data", "spot_data", "expiry_calendar", "trading_holidays", "super_trend_segments"]
            for t in tables:
                ok = conn.execute(text("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=:t"), {"t": t}).first()
                if not ok:
                    res[t] = {"exists": False}
                    continue
                cnt = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() or 0
                res[t] = {"exists": True, "row_count": int(cnt)}
            if res.get("option_data", {}).get("exists"):
                cols = self.table_columns("option_data")
                date_col = self._legacy_col(cols, "trade_date", "date")
                q = """
                SELECT COUNT(*) FROM (
                  SELECT {date_col},symbol,instrument,expiry_date,COALESCE(option_type,''),strike_price,COUNT(*) c
                  FROM option_data
                  GROUP BY {date_col},symbol,instrument,expiry_date,COALESCE(option_type,''),strike_price
                  HAVING COUNT(*)>1
                ) x
                """
                res["option_data"]["duplicate_key_groups"] = int(conn.execute(text(q.format(date_col=date_col))).scalar() or 0)
            if res.get("spot_data", {}).get("exists"):
                cols = self.table_columns("spot_data")
                date_col = self._legacy_col(cols, "trade_date", "date")
                q = f"SELECT COUNT(*) FROM (SELECT {date_col},symbol,COUNT(*) c FROM spot_data GROUP BY {date_col},symbol HAVING COUNT(*)>1) x"
                res["spot_data"]["duplicate_key_groups"] = int(conn.execute(text(q)).scalar() or 0)
            if res.get("expiry_calendar", {}).get("exists"):
                q = "SELECT COUNT(*) FROM (SELECT symbol,expiry_type,current_expiry,COUNT(*) c FROM expiry_calendar GROUP BY symbol,expiry_type,current_expiry HAVING COUNT(*)>1) x"
                res["expiry_calendar"]["duplicate_key_groups"] = int(conn.execute(text(q)).scalar() or 0)
        return res


def totals(rows: List[Dict]) -> Dict:
    return {
        "files_total": len(rows),
        "files_failed": len([x for x in rows if x.get("status") != "completed"]),
        "rows_read": int(sum(x.get("rows_read", 0) for x in rows)),
        "rows_valid": int(sum(x.get("rows_valid", 0) for x in rows)),
        "rows_rejected": int(sum(x.get("rows_rejected", 0) for x in rows)),
        "duplicate_rows_in_file": int(sum(x.get("duplicate_rows_in_file", 0) for x in rows)),
        "rows_inserted": int(sum(x.get("rows_inserted", 0) for x in rows)),
        "rows_updated": int(sum(x.get("rows_updated", 0) for x in rows)),
    }


def parse_args():
    p = argparse.ArgumentParser(description="CSV -> PostgreSQL migration utility")
    p.add_argument("--all", action="store_true")
    p.add_argument("--table", type=str)
    p.add_argument("--file", action="append")
    p.add_argument("--validate", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--report-json", type=str, default=str(DEFAULT_REPORT))
    p.add_argument("--option-data", action="store_true")
    p.add_argument("--spot-data", action="store_true")
    p.add_argument("--expiry-data", action="store_true")
    p.add_argument("--holiday-data", action="store_true")
    p.add_argument("--str-data", action="store_true")
    return p.parse_args()


def mode(args):
    if args.option_data:
        return "table", "option_data", None
    if args.spot_data:
        return "table", "spot_data", None
    if args.expiry_data:
        return "table", "expiry_calendar", None
    if args.holiday_data:
        return "table", "trading_holidays", None
    if args.str_data:
        return "table", "super_trend_segments", None
    if args.file:
        return "file", None, args.file
    if args.table:
        return "table", args.table, None
    if args.all:
        return "all", None, None
    if args.validate:
        return "validate", None, None
    return "help", None, None


def main():
    args = parse_args()
    m, t, fs = mode(args)
    if m == "help":
        logger.info("Use --all or --table or --file or --validate")
        return
    mig = Migrator(dry_run=args.dry_run)
    rows = []
    val = {}
    if m == "all":
        rows = mig.import_all(limit=args.limit)
    elif m == "table":
        rows = mig.import_table(t, limit=args.limit)
    elif m == "file":
        for f in fs or []:
            p = Path(f)
            if not p.is_absolute():
                p = (PROJECT_ROOT / f).resolve()
            if not p.exists():
                rows.append({"file": str(p), "table": "unknown", "status": "failed", "errors": ["File does not exist"], "rows_read": 0, "rows_valid": 0, "rows_rejected": 0, "duplicate_rows_in_file": 0, "rows_inserted": 0, "rows_updated": 0})
            else:
                rows.append(mig.import_file(p))
    if args.validate or m == "validate":
        val = mig.validate()
    report = {
        "started_at": datetime.utcnow().isoformat(),
        "mode": m,
        "table_filter": t,
        "file_filter": fs,
        "dry_run": args.dry_run,
        "totals": totals(rows),
        "validation": val,
        "files": rows,
        "finished_at": datetime.utcnow().isoformat(),
    }
    rp = Path(args.report_json)
    if not rp.is_absolute():
        rp = (PROJECT_ROOT / rp).resolve()
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    logger.info("Import summary: %s", json.dumps(report["totals"]))
    if val:
        logger.info("Validation summary: %s", json.dumps(val))
    failed = [x for x in rows if x.get("status") != "completed"]
    if failed:
        logger.warning("Failed files: %s", len(failed))
        for x in failed[:20]:
            logger.warning("  %s -> %s", x.get("file"), "; ".join(x.get("errors", [])))
    logger.info("Report written: %s", rp)


if __name__ == "__main__":
    main()
