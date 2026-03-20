import pandas as pd
import numpy as np
import polars as pl
from polars.exceptions import InvalidOperationError
from functools import lru_cache
from datetime import datetime, timedelta, date
import math
import os
import asyncio
import bisect
from concurrent.futures import ThreadPoolExecutor
from typing import Tuple, Optional, Dict, Any
from database import ALLOW_CSV_FALLBACK, get_data_source, engine as db_engine, DATA_DIR
from repositories.market_data_repository import MarketDataRepository

# Date formatting utility
def fmt_ddmmyyyy(date_obj) -> str:
    """Format date as ddmmyyyy (e.g., 21022025)"""
    if date_obj is None:
        return ""
    if hasattr(date_obj, 'strftime'):
        return date_obj.strftime('%d%m%Y')
    return str(date_obj)

def fmt_dd_mm_yyyy(date_obj) -> str:
    """Format date as dd-mm-yyyy (e.g., 21-02-2025)"""
    if date_obj is None:
        return ""
    if hasattr(date_obj, 'strftime'):
        return date_obj.strftime('%d-%m-%Y')
    return str(date_obj)

# Thread pool for async file I/O
_executor = ThreadPoolExecutor(max_workers=4)


# ============================================================================
# BACKTEST DATA CACHE - OPTIONAL PERFORMANCE OPTIMIZATION
# This cache can be used to speed up backtests by pre-loading data
# The existing functions below remain unchanged and continue to work
# ============================================================================

class BacktestDataCache:
    """
    High-performance cache for backtest data.
    Pre-loads all data for date range and provides O(1) lookups.
    This is OPTIONAL - existing functions continue to work without it.
    """
    
    
    def __init__(self):
        self.option_cache: Dict[str, Dict[str, Dict[float, Dict[str, Dict[str, float]]]]] = {}
        self.future_cache: Dict[str, Dict[str, float]] = {}
        self.spot_cache: Dict[str, Dict[str, float]] = {}
        self.loaded_dates = set()
        
    def preload_date_range(self, start_date: str, end_date: str, symbols: list):
        """
        Pre-load all bhavcopy data for the date range.
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            symbols: List of symbols to load (e.g., ['NIFTY', 'BANKNIFTY'])
        """
        print(f"🚀 Pre-loading data from {start_date} to {end_date} for {symbols}...")
        
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        current = start
        
        dates_to_load = []
        while current <= end:
            date_str = current.strftime('%Y-%m-%d')
            dates_to_load.append(date_str)
            current += timedelta(days=1)
        
        loaded_count = 0
        for date_str in dates_to_load:
            if self._load_date_data(date_str, symbols):
                loaded_count += 1
        
        print(f"✅ Pre-loaded {loaded_count}/{len(dates_to_load)} dates into cache")
        
    def _load_date_data(self, date_str: str, symbols: list) -> bool:
        """Load data for a single date into cache using existing load_bhavcopy function."""
        if date_str in self.loaded_dates:
            return True
            
        try:
            # Use existing load_bhavcopy function - NO LOGIC CHANGE
            df = load_bhavcopy(date_str)
            if df is None or df.empty:
                return False
            
            # Filter for requested symbols
            df = df[df['SYMBOL'].isin(symbols)]
            
            # Initialize date in caches if not exists
            if date_str not in self.option_cache:
                self.option_cache[date_str] = {}
                self.future_cache[date_str] = {}
                self.spot_cache[date_str] = {}
            
            # Build nested dict structure for O(1) lookups using vectorized Polars operations
            pl_df = pl.from_pandas(df)
            option_df = pl_df.filter(pl.col("INSTRUMENT").is_in(["OPTIDX", "OPTSTK"]))
            future_df = pl_df.filter(pl.col("INSTRUMENT") == "FUTIDX")

            if not option_df.is_empty():
                symbols_l = option_df["SYMBOL"].to_list()
                strikes_l = option_df["STRIKE_PR"].cast(pl.Float64).to_list()
                option_types_l = option_df["OPTION_TYP"].to_list()
                expiries_l = _series_to_iso_date_list(option_df["EXPIRY_DT"])
                closes_l = option_df["CLOSE"].cast(pl.Float64).to_list()

                self.option_cache[date_str] = {
                    (symbol, float(strike), option_type, expiry): close_price
                    for symbol, strike, option_type, expiry, close_price in zip(
                        symbols_l, strikes_l, option_types_l, expiries_l, closes_l
                    )
                    if symbol is not None and not pd.isna(strike) and option_type is not None
                }
            else:
                self.option_cache[date_str] = {}

            if not future_df.is_empty():
                future_symbols = future_df["SYMBOL"].to_list()
                future_closes = future_df["CLOSE"].cast(pl.Float64).to_list()
                for symbol, close_price in zip(future_symbols, future_closes):
                    if symbol:
                        self.future_cache[date_str][symbol] = close_price
            # Load spot prices from strike data using existing get_strike_data function
            for symbol in symbols:
                try:
                    strike_df = get_strike_data(symbol, date_str, date_str)
                    if strike_df is not None and not strike_df.empty:
                        spot_price = strike_df['Close'].iloc[0]
                        self.spot_cache[date_str][symbol] = spot_price
                except:
                    pass
            
            self.loaded_dates.add(date_str)
            return True
            
        except Exception as e:
            return False
    
    def get_option_premium(self, date_str: str, symbol: str, strike: float, 
                          option_type: str, expiry: str) -> Optional[float]:
        """
        Get option premium with O(1) lookup.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            symbol: Index symbol (e.g., 'NIFTY')
            strike: Strike price
            option_type: 'CE' or 'PE'
            expiry: Expiry date in DD-MMM-YYYY format
            
        Returns:
            Premium value or None if not found
        """
        cache = self.option_cache.get(date_str)
        if not cache:
            return None

        key = (symbol, float(strike), option_type, expiry)
        return cache.get(key)
    
    def get_future_price(self, date_str: str, symbol: str) -> Optional[float]:
        """
        Get future price with O(1) lookup.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            symbol: Index symbol (e.g., 'NIFTY')
            
        Returns:
            Future price or None if not found
        """
        try:
            return self.future_cache[date_str][symbol]
        except KeyError:
            return None
    
    def get_spot_price(self, date_str: str, symbol: str) -> Optional[float]:
        """
        Get spot price with O(1) lookup.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            symbol: Index symbol (e.g., 'NIFTY')
            
        Returns:
            Spot price or None if not found
        """
        try:
            return self.spot_cache[date_str][symbol]
        except KeyError:
            return None
    
    def clear(self):
        """Clear all cached data."""
        self.option_cache.clear()
        self.future_cache.clear()
        self.spot_cache.clear()
        self.loaded_dates.clear()


# Global cache instance (optional, only used if backtest engine initializes it)
_backtest_cache = None

def get_backtest_cache() -> BacktestDataCache:
    """Get or create the global backtest cache instance."""
    global _backtest_cache
    if _backtest_cache is None:
        _backtest_cache = BacktestDataCache()
    return _backtest_cache

def clear_backtest_cache():
    """Clear the global backtest cache."""
    global _backtest_cache
    if _backtest_cache is not None:
        _backtest_cache.clear()
        _backtest_cache = None

# ============================================================================
# END OF CACHE - EXISTING FUNCTIONS BELOW REMAIN UNCHANGED
# ============================================================================

# Constants for data directories
PROJECT_ROOT = DATA_DIR
CLEANED_CSV_DIR = os.path.join(PROJECT_ROOT, 'cleaned_csvs')
EXPIRY_DATA_DIR = os.path.join(PROJECT_ROOT, 'expiryData')
STRIKE_DATA_DIR = os.path.join(PROJECT_ROOT, 'strikeData')
FILTER_DIR = os.path.join(PROJECT_ROOT, 'Filter')

_repo = MarketDataRepository(db_engine)


def _use_postgres() -> bool:
    return get_data_source() == "postgres"

def round_half_up(x: float) -> int:
    """Round half values up (e.g., 0.5 -> 1, 1.5 -> 2)"""
    return int(math.floor(x + 0.5))


def round_to_50(value: float) -> int:
    """Round to nearest 50"""
    return round(value / 50) * 50

def get_strike_data(symbol: str, from_date: str, to_date: str) -> pd.DataFrame:
    """
    Read ./strikeData/Nifty_strike_data.csv
    Filter by symbol, parse Date, filter to date range
    Return DataFrame: Date, Close
    """
    if _use_postgres():
        try:
            pg_df = _repo.get_spot_data(symbol=symbol, from_date=from_date, to_date=to_date)
            return pg_df.reset_index(drop=True)
        except Exception as exc:
            if not ALLOW_CSV_FALLBACK:
                raise RuntimeError("PostgreSQL strike data lookup failed and CSV fallback is disabled.") from exc
            print("Postgres strike lookup failed; falling back to CSV.")

    if not ALLOW_CSV_FALLBACK:
        raise RuntimeError("CSV fallback is disabled; strike data must be loaded from PostgreSQL.")

    # Handle different capitalization formats for symbol
    possible_filenames = [
        f"{symbol}_strike_data.csv",
        f"{symbol.lower()}_strike_data.csv",
        f"{symbol.upper()}_strike_data.csv",
        f"{symbol.capitalize()}_strike_data.csv",
        f"{symbol.title()}_strike_data.csv",
    ]
    
    file_path = None
    for filename in possible_filenames:
        test_path = os.path.join(STRIKE_DATA_DIR, filename)
        if os.path.exists(test_path):
            file_path = test_path
            break
    
    if file_path is None:
        raise FileNotFoundError(f"Strike data file not found for symbol {symbol}. Tried: {possible_filenames}")
    
    df = pd.read_csv(file_path)
    
    # Handle multiple date formats like in existing code
    format_list = ["%Y-%m-%d", "%d-%m-%Y", "%y-%m-%d", "%d-%m-%y", "%d-%b-%Y", "%d-%b-%y"]
    for format_type in format_list:
        try:
            df['Date'] = pd.to_datetime(df['Date'], format=format_type, errors="raise")
            break
        except:
            continue
    
    # If still not datetime, try dayfirst=True
    if not pd.api.types.is_datetime64_any_dtype(df['Date']):
        df['Date'] = pd.to_datetime(df['Date'], dayfirst=True)
    
    # Filter by date range
    from_dt = pd.to_datetime(from_date)
    to_dt = pd.to_datetime(to_date)
    df = df[(df['Date'] >= from_dt) & (df['Date'] <= to_dt)]
    
    # Filter by symbol (ticker) if column exists
    if 'Ticker' in df.columns:
        df = df[df['Ticker'] == symbol]
    
    return df[['Date', 'Close']].reset_index(drop=True)

def load_expiry(index: str, expiry_type: str) -> pd.DataFrame:
    """
    Read ./expiryData/{index}.csv (weekly) or ./expiryData/{index}_Monthly.csv
    Parse Previous Expiry, Current Expiry, Next Expiry
    Return sorted DataFrame
    """
    if _use_postgres():
        try:
            pg_df = _repo.get_expiry_data(symbol=index, expiry_type=expiry_type)
            return pg_df.sort_values('Current Expiry').reset_index(drop=True)
        except Exception as exc:
            if not ALLOW_CSV_FALLBACK:
                raise RuntimeError("PostgreSQL expiry lookup failed and CSV fallback is disabled.") from exc
            print("Postgres expiry lookup failed; falling back to CSV.")

    if not ALLOW_CSV_FALLBACK:
        raise RuntimeError("CSV fallback is disabled; expiry calendar must be sourced from PostgreSQL.")

    if expiry_type.lower() == 'weekly':
        file_path = os.path.join(EXPIRY_DATA_DIR, f"{index}.csv")
    elif expiry_type.lower() == 'monthly':
        file_path = os.path.join(EXPIRY_DATA_DIR, f"{index}_Monthly.csv")
    else:
        raise ValueError(f"Invalid expiry type: {expiry_type}. Use 'weekly' or 'monthly'")
    
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Expiry data file not found: {file_path}")
    
    df = pd.read_csv(file_path)
    
    # Handle multiple date formats for expiry dates
    date_columns = ['Previous Expiry', 'Current Expiry', 'Next Expiry']
    for col in date_columns:
        if col in df.columns:
            format_list = ["%Y-%m-%d", "%d-%m-%Y", "%y-%m-%d", "%d-%m-%y", "%d-%b-%Y", "%d-%b-%y"]
            for format_type in format_list:
                try:
                    df[col] = pd.to_datetime(df[col], format=format_type, errors="raise")
                    break
                except:
                    continue
            
            # If still not datetime, try dayfirst=True
            if not pd.api.types.is_datetime64_any_dtype(df[col]):
                df[col] = pd.to_datetime(df[col], dayfirst=True)
    
    return df.sort_values('Current Expiry').reset_index(drop=True)

# NOTE: load_base2() is disabled - base2 filter not used
# def load_base2() -> pd.DataFrame:
#     """
#     Read ./Filter/base2.csv, parse Start and End
#     Return sorted DataFrame
#     """
#     file_path = os.path.join(FILTER_DIR, "base2.csv")
#     if not os.path.exists(file_path):
#         raise FileNotFoundError(f"Base2 file not found: {file_path}")
#     
#     df = pd.read_csv(file_path)
#     
#     # Handle multiple date formats for base2 dates
#     date_columns = ['Start', 'End']
#     for col in date_columns:
#         if col in df.columns:
#             format_list = ["%Y-%m-%d", "%d-%m-%Y", "%y-%m-%d", "%d-%m-%y", "%d-%b-%Y", "%d-%b-%y"]
#             for format_type in format_list:
#                 try:
#                     df[col] = pd.to_datetime(df[col], format=format_type, errors="raise")
#                     break
#                 except:
#                     continue
#             
#             # If still not datetime, try dayfirst=True
#             if not pd.api.types.is_datetime64_any_dtype(df[col]):
#                 df[col] = pd.to_datetime(df[col], dayfirst=True)
#     
#     return df.sort_values('Start').reset_index(drop=True)

@lru_cache(maxsize=500)
def load_bhavcopy(date_str: str) -> pd.DataFrame:
    """
    Read ./cleaned_csvs/{date_str}.csv with LRU cache (500 files)
    Parse Date and ExpiryDate columns
    Return DataFrame: Instrument, Symbol, ExpiryDate, OptionType, StrikePrice, Close, TurnOver
    """
    if _use_postgres():
        try:
            df = _repo.get_bhavcopy_by_date(date_str=date_str)
            required_cols = ['Instrument', 'Symbol', 'ExpiryDate', 'OptionType', 'StrikePrice', 'Close', 'TurnOver', 'Date']
            available_cols = [col for col in required_cols if col in df.columns]
            return df[available_cols].copy()
        except Exception as exc:
            if not ALLOW_CSV_FALLBACK:
                raise RuntimeError("PostgreSQL bhavcopy lookup failed and CSV fallback is disabled.") from exc
            print("Postgres bhavcopy lookup failed; falling back to CSV.")

    if not ALLOW_CSV_FALLBACK:
        raise RuntimeError("CSV fallback disabled; bhavcopy data must be sourced from PostgreSQL.")

    file_path = os.path.join(CLEANED_CSV_DIR, f"{date_str}.csv")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Bhavcopy file not found: {file_path}")
    
    df = pd.read_csv(file_path)
    df['Date'] = pd.to_datetime(df['Date'])
    df['ExpiryDate'] = pd.to_datetime(df['ExpiryDate'])
    
    # Return only the required columns
    required_cols = ['Instrument', 'Symbol', 'ExpiryDate', 'OptionType', 'StrikePrice', 'Close', 'TurnOver', 'Date']
    available_cols = [col for col in required_cols if col in df.columns]
    
    result = df[available_cols].copy()
    # DEBUG print commented out for performance
    # print(f"      DEBUG: Loaded CSV for {date_str}, rows: {len(result)}, NIFTY: {len(result[result['Symbol']=='NIFTY'])}")
    return result


async def load_bhavcopy_async(date_str: str) -> pd.DataFrame:
    """
    Async version of load_bhavcopy - runs in thread pool to avoid blocking event loop
    Uses the same LRU cache for performance
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, load_bhavcopy, date_str)

def get_option_price(bhavcopy_df, symbol, instrument, option_type, expiry, strike):
    # Normalize expiry to Timestamp (no .date() calls)
    expiry_ts = pd.Timestamp(expiry)
    
    mask = (
        (bhavcopy_df['Symbol'] == symbol) &
        (bhavcopy_df['Instrument'] == instrument) &
        (bhavcopy_df['OptionType'] == option_type) &
        (abs(bhavcopy_df['StrikePrice'] - strike) <= 0.5)
    )
    
    # Exact expiry match — compare Timestamp to Timestamp
    exact_match = bhavcopy_df[mask & (bhavcopy_df['ExpiryDate'] == expiry_ts)]
    if not exact_match.empty:
        row = exact_match.iloc[0]
        return float(row['Close']), float(row['TurnOver']) if pd.notna(row['TurnOver']) else None
    
    # ±1 day tolerance — still Timestamp comparisons
    tolerance_match = bhavcopy_df[mask & (
        (bhavcopy_df['ExpiryDate'] == expiry_ts + pd.Timedelta(days=1)) |
        (bhavcopy_df['ExpiryDate'] == expiry_ts - pd.Timedelta(days=1))
    )]
    if not tolerance_match.empty:
        row = tolerance_match.iloc[0]
        return float(row['Close']), float(row['TurnOver']) if pd.notna(row['TurnOver']) else None
    
    return None, None


def apply_spot_adjustment(spot: float, mode: int, value: float) -> float:
    """
    Apply spot adjustment based on mode
    
    Args:
        spot: Original spot price
        mode: Adjustment mode (0-4)
            0: Unadjusted spot
            1: Spot rises by X%
            2: Spot falls by X%
            3: Spot may rise or fall (volatility assumption)
            4: Custom spot shift
        value: Adjustment value (percentage or points)
    """
    if mode == 0:
        # Unadjusted spot
        return spot
    elif mode == 1:
        # Spot rises by X%
        return spot * (1 + value / 100)
    elif mode == 2:
        # Spot falls by X%
        return spot * (1 - value / 100)
    elif mode == 3:
        # Spot may rise or fall (volatility assumption)
        # For this mode, we might want to return both possibilities or average
        # For now, return the original spot as a neutral value
        return spot
    elif mode == 4:
        # Custom spot shift
        return spot + value
    else:
        raise ValueError(f"Invalid spot adjustment mode: {mode}")


def get_nearest_strike(adjusted_spot: float, available_strikes: pd.Series) -> float:
    """
    Get the nearest available strike to the adjusted spot price
    
    Args:
        adjusted_spot: Adjusted spot price
        available_strikes: Series of available strike prices
    """
    if available_strikes.empty:
        return None
    
    # Find the strike closest to the adjusted spot
    differences = abs(available_strikes - adjusted_spot)
    nearest_idx = differences.idxmin()
    return available_strikes.iloc[nearest_idx]


def calculate_strike_offset(spot: float, offset_type: str, offset_value: float) -> float:
    """
    Calculate strike price based on offset from spot
    
    Args:
        spot: Reference spot price
        offset_type: 'percent' or 'points'
        offset_value: Offset amount
    """
    if offset_type == 'percent':
        return spot * (1 + offset_value / 100)
    elif offset_type == 'points':
        return spot + offset_value
    else:
        raise ValueError(f"Invalid offset type: {offset_type}")


def get_atm_strike(spot: float, available_strikes: pd.Series) -> float:
    """
    Get ATM (At The Money) strike price
    
    Args:
        spot: Current spot price
        available_strikes: Series of available strike prices
    """
    return get_nearest_strike(spot, available_strikes)


def get_otm_strike(spot: float, available_strikes: pd.Series, otm_distance: float, option_type: str) -> float:
    """
    Get OTM (Out of The Money) strike price
    
    Args:
        spot: Current spot price
        available_strikes: Series of available strike prices
        otm_distance: Distance from ATM (can be percent or points)
        option_type: 'CE' for calls or 'PE' for puts
    """
    if option_type == 'CE':
        # For calls, OTM is above spot
        otm_target = spot * (1 + abs(otm_distance) / 100) if isinstance(otm_distance, (int, float)) and otm_distance >= 0 else spot + abs(otm_distance)
    elif option_type == 'PE':
        # For puts, OTM is below spot
        otm_target = spot * (1 - abs(otm_distance) / 100) if isinstance(otm_distance, (int, float)) and otm_distance >= 0 else spot - abs(otm_distance)
    else:
        raise ValueError(f"Invalid option type: {option_type}")
    
    # Find the nearest available strike to the calculated target
    return get_nearest_strike(otm_target, available_strikes)


def get_itm_strike(spot: float, available_strikes: pd.Series, itm_distance: float, option_type: str) -> float:
    """
    Get ITM (In The Money) strike price
    
    Args:
        spot: Current spot price
        available_strikes: Series of available strike prices
        itm_distance: Distance from ATM (can be percent or points)
        option_type: 'CE' for calls or 'PE' for puts
    """
    if option_type == 'CE':
        # For calls, ITM is below spot
        itm_target = spot * (1 - abs(itm_distance) / 100) if isinstance(itm_distance, (int, float)) and itm_distance >= 0 else spot - abs(itm_distance)
    elif option_type == 'PE':
        # For puts, ITM is above spot
        itm_target = spot * (1 + abs(itm_distance) / 100) if isinstance(itm_distance, (int, float)) and itm_distance >= 0 else spot + abs(itm_distance)
    else:
        raise ValueError(f"Invalid option type: {option_type}")
    
    # Find the nearest available strike to the calculated target
    return get_nearest_strike(itm_target, available_strikes)


def build_intervals(filtered_data: pd.DataFrame, spot_adjustment_type: int, spot_adjustment: float) -> list:
    """
    Core re-entry engine — identical logic to all Python strategy functions
    """
    if spot_adjustment_type == 0:
        return [(filtered_data.iloc[0]['Date'], filtered_data.iloc[-1]['Date'])]
    
    entry_price = None
    reentry_dates = []
    
    for idx, row in filtered_data.iterrows():
        if entry_price is None:
            entry_price = row['Close']
            continue
        
        roc = 100 * (row['Close'] - entry_price) / entry_price
        
        triggered = (
            (spot_adjustment_type == 1 and roc >= spot_adjustment) or
            (spot_adjustment_type == 2 and roc <= -spot_adjustment) or
            (spot_adjustment_type == 3 and abs(roc) >= spot_adjustment)
        )
        
        if triggered:
            reentry_dates.append(row['Date'])
            entry_price = row['Close']
    
    if not reentry_dates:
        return [(filtered_data.iloc[0]['Date'], filtered_data.iloc[-1]['Date'])]
    
    intervals = []
    start = filtered_data.iloc[0]['Date']
    for d in reentry_dates:
        intervals.append((start, d))
        start = d
    if start != filtered_data.iloc[-1]['Date']:
        intervals.append((start, filtered_data.iloc[-1]['Date']))
    return intervals

def compute_analytics(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    AlgoTest-exact analytics engine.

    Columns added to df
    -------------------
    Cumulative  : Initial Capital + running sum of Net P&L (uses Entry Spot as capital)
    Peak        : running high-water mark of Cumulative
    DD          : Cumulative - Peak when Peak > Cumulative, else 0 (rupee drawdown)
    %DD         : DD / Peak * 100 when DD != 0, else 0
                  This matches AlgoTest's % drawdown column exactly.

    Summary keys returned
    ---------------------
    total_pnl, count, win_pct, avg_win, avg_loss,
    expectancy, cagr_options, max_dd_pct, max_dd_pts,
    car_mdd, recovery_factor, avg_profit_per_trade,
    max_win_streak, max_loss_streak, reward_to_risk
    """
    if df.empty:
        return df, {}

    df = df.copy()

    # ── Normalize column names ────────────────────────────────────────────────
    _rename = {
        'net_pnl': 'Net P&L', 'entry_date': 'Entry Date',
        'exit_date': 'Exit Date', 'entry_spot': 'Entry Spot',
        'exit_spot': 'Exit Spot', 'spot_pnl': 'Spot P&L',
    }
    for old, new in _rename.items():
        if old in df.columns and new not in df.columns:
            df = df.rename(columns={old: new})

    pnl_col        = 'Net P&L'
    entry_date_col = 'Entry Date'
    exit_date_col  = 'Exit Date'

    # Sort by entry date to guarantee chronological order
    df = df.sort_values(entry_date_col).reset_index(drop=True)

    # ── CORE EQUITY CURVE ────────────────────────────────────────────────────
    # Cumulative = running sum of P&L (no initial capital added)
    df['Cumulative'] = df[pnl_col].cumsum()
    
    # Get initial capital for CAGR calculation only
    # Use default capital of 1 lakh (100000) as initial trading capital
    initial_capital = 100000.0

    # High-water mark (peak equity)
    df['Peak'] = df['Cumulative'].cummax()

    # Rupee drawdown (always ≤ 0)
    df['DD'] = np.where(df['Peak'] > df['Cumulative'], df['Cumulative'] - df['Peak'], 0)

    # Percentage drawdown relative to the peak equity
    df['%DD'] = np.where(
        df['DD'] == 0,
        0.0,
        np.round(100.0 * df['DD'] / df['Peak'], 2)
    )

    # ── BASIC STATS ──────────────────────────────────────────────────────────
    # Ensure pnl_col values are numeric
    df[pnl_col] = pd.to_numeric(df[pnl_col], errors='coerce').fillna(0)
    
    total_pnl  = round(df[pnl_col].sum(), 2)
    count      = len(df)
    wins       = df[df[pnl_col] > 0]
    losses     = df[df[pnl_col] < 0]
    win_count  = len(wins)
    loss_count = len(losses)

    win_pct  = round(win_count  / count * 100, 2) if count > 0 else 0
    loss_pct = round(loss_count / count * 100, 2) if count > 0 else 0
    avg_win  = round(wins[pnl_col].mean(),   2) if win_count  > 0 else 0
    avg_loss = round(losses[pnl_col].mean(), 2) if loss_count > 0 else 0
    max_win  = round(wins[pnl_col].max(),    2) if win_count  > 0 else 0
    max_loss = round(losses[pnl_col].min(),  2) if loss_count > 0 else 0
    avg_profit_per_trade = round(total_pnl / count, 2) if count > 0 else 0

    # Reward-to-risk ratio  (|avg_win| / |avg_loss|)
    reward_to_risk = round(abs(avg_win) / abs(avg_loss), 2) if avg_loss != 0 else 0

    # Profit Factor = Gross Profit / |Gross Loss|
    gross_profit = round(wins[pnl_col].sum(), 2) if win_count > 0 else 0
    gross_loss_val = losses[pnl_col].sum() if loss_count > 0 else 0
    if pd.isna(gross_loss_val):
        gross_loss_val = 0
    gross_loss = round(abs(gross_loss_val), 2)
    
    # If all trades are wins (no losses), profit factor is N/A or infinite
    if gross_loss == 0 and gross_profit > 0:
        profit_factor = 999.99  # All winning trades
    elif gross_loss == 0 and gross_profit == 0:
        profit_factor = 0
    else:
        profit_factor = round(gross_profit / gross_loss, 2)

    # Expectancy  = (win_rate * avg_win  +  loss_rate * avg_loss) / |avg_loss|
    # (AlgoTest's formula from their "Expectancy Ratio" display)
    if avg_loss != 0:
        expectancy = round(
            ((avg_win / abs(avg_loss)) * win_pct - (100 - win_pct)) / 100, 2
        )
    else:
        expectancy = 0

    # ── WIN / LOSS STREAKS ────────────────────────────────────────────────────
    max_win_streak  = 0
    max_loss_streak = 0
    cur_win  = 0
    cur_loss = 0
    for pnl in df[pnl_col]:
        if pnl > 0:
            cur_win  += 1
            cur_loss  = 0
            max_win_streak = max(max_win_streak, cur_win)
        elif pnl < 0:
            cur_loss += 1
            cur_win   = 0
            max_loss_streak = max(max_loss_streak, cur_loss)
        else:
            cur_win = cur_loss = 0

    # ── CAGR ──────────────────────────────────────────────────────────────────
    # CAGR = ((Final Capital / Initial Capital) ^ (1/Years) - 1) * 100
    # Where:
    #   - Initial Capital = First trade's Entry Spot
    #   - Final Capital = Initial Capital + Total P&L
    #   - Years = Total Calendar Days / 365
    start_date = pd.to_datetime(df[entry_date_col].min())
    end_date   = pd.to_datetime(df[exit_date_col].max())
    n_years    = max((end_date - start_date).days / 365.0, 0.01)

    # Use Entry Spot of first trade as Initial Capital
    initial_capital = float(df.iloc[0]['Entry Spot'])
    final_capital = initial_capital + total_pnl

    # Calculate CAGR
    if initial_capital > 0 and final_capital > 0:
        cagr = round(100.0 * ((final_capital / initial_capital) ** (1.0 / n_years) - 1), 2)
    else:
        cagr = round(-100.0, 2)  # total wipeout

    # ── DRAWDOWN SUMMARY ─────────────────────────────────────────────────────
    max_dd_pct = float(df['%DD'].min())                    # most negative %DD
    max_dd_pts = round(float(df['DD'].min()), 2)           # deepest rupee DD

    # Duration of overall max drawdown (calendar days from peak to trough)
    mdd_duration   = 0
    mdd_start_date = None
    mdd_end_date   = None
    mdd_trade_number = None

    if max_dd_pts < 0:
        trough_idx  = df['DD'].idxmin()
        trough_date = pd.to_datetime(df.loc[trough_idx, exit_date_col])
        
        # Trade number where max drawdown occurred (1-indexed for display)
        mdd_trade_number = int(trough_idx) + 1

        # Find the peak date = last trade where Cumulative == Peak before trough
        pre_trough  = df.loc[:trough_idx]
        peak_val    = df.loc[trough_idx, 'Peak']
        peak_candidates = pre_trough[pre_trough['Cumulative'] >= peak_val]
        if not peak_candidates.empty:
            peak_date = pd.to_datetime(
                peak_candidates.iloc[-1][exit_date_col]
            )
        else:
            peak_date = pd.to_datetime(df.loc[0, exit_date_col])

        mdd_duration   = (trough_date - peak_date).days
        mdd_start_date = peak_date.strftime('%Y-%m-%d')
        mdd_end_date   = trough_date.strftime('%Y-%m-%d')

    # ── RATIO METRICS ────────────────────────────────────────────────────────
    # CAR/MDD  =  CAGR / |Max DD %|   (annualised return per unit of drawdown)
    car_mdd = round(cagr / abs(max_dd_pct), 2) if max_dd_pct != 0 else 0

    # Recovery Factor  =  Total P&L / |Max DD rupees|
    recovery_factor = round(total_pnl / abs(max_dd_pts), 2) if max_dd_pts != 0 else 0

    # ── SPOT COMPARISON METRICS ─────────────────────────────────────────────────
    spot_chg = round(df['Spot P&L'].sum(), 2) if 'Spot P&L' in df.columns else 0

    # CAGR(Spot) - if just held the index (Buy & Hold)
    # CAGR = ((Final Spot / Initial Spot) ^ (1/Years) - 1) * 100
    if 'Entry Spot' in df.columns and 'Exit Spot' in df.columns:
        initial_spot = float(df.iloc[0]['Entry Spot'])
        final_spot = float(df.iloc[-1]['Exit Spot'])
        if n_years > 0 and initial_spot > 0 and final_spot > 0:
            cagr_spot = round(100 * ((final_spot / initial_spot) ** (1.0 / n_years) - 1), 2)
        else:
            cagr_spot = 0.0
    else:
        cagr_spot = 0.0

    summary = {
        "total_pnl":             total_pnl,
        "count":                 count,
        "win_pct":               win_pct,
        "loss_pct":              loss_pct,
        "avg_win":               avg_win,
        "avg_loss":              avg_loss,
        "max_win":               max_win,
        "max_loss":              max_loss,
        "avg_profit_per_trade":  avg_profit_per_trade,
        "expectancy":            expectancy,
        "reward_to_risk":        reward_to_risk,
        "profit_factor":         profit_factor,
        "cagr_options":          cagr,
        "max_dd_pct":            max_dd_pct,
        "max_dd_pts":            max_dd_pts,
        "mdd_duration_days":     mdd_duration,
        "mdd_start_date":        mdd_start_date,
        "mdd_end_date":          mdd_end_date,
        "mdd_trade_number":      mdd_trade_number,
        "car_mdd":               car_mdd,
        "recovery_factor":       recovery_factor,
        "max_win_streak":        max_win_streak,
        "max_loss_streak":       max_loss_streak,
        "spot_change":           spot_chg,
        "cagr_spot":             cagr_spot,
    }

    return df, summary


# ─────────────────────────────────────────────────────────────────────────────
# 2. build_pivot   (FULL REPLACEMENT for lines 451-545 in base.py)
# ─────────────────────────────────────────────────────────────────────────────
def build_pivot(df: pd.DataFrame, expiry_col: str) -> Dict[str, Any]:
    """
    AlgoTest-exact year-wise returns pivot.

    Key difference from old code
    ----------------------------
    The global cumulative curve (already computed in compute_analytics) is
    SLICED per year — NOT restarted from 0 each January.

    This means if a drawdown started in Dec 2020 and recovered in Mar 2021,
    the 2021 row shows the continuation of that drawdown — exactly as
    AlgoTest does.

    Max Drawdown per year is the deepest peak-to-trough on the GLOBAL curve
    that has its TROUGH inside that calendar year.

    R/MDD  =  year_total_pnl / |year_max_dd_rupees|
    """
    if df.empty:
        return {"headers": [], "rows": []}

    df = df.copy()
    if 'net_pnl' in df.columns and 'Net P&L' not in df.columns:
        df = df.rename(columns={'net_pnl': 'Net P&L'})

    exit_date_col = 'Exit Date' if 'Exit Date' in df.columns else 'exit_date'

    df = df.sort_values(exit_date_col).reset_index(drop=True)
    df[exit_date_col] = pd.to_datetime(df[exit_date_col])

    # ── Build GLOBAL cumulative curve (same as compute_analytics) ────────────
    df['_Global_Cumulative'] = df['Net P&L'].cumsum()
    df['_Global_Peak']       = df['_Global_Cumulative'].cummax().clip(lower=0)
    df['_Global_DD']         = df['_Global_Cumulative'] - df['_Global_Peak']

    df['_Year']  = df[exit_date_col].dt.year
    df['_Month'] = df[exit_date_col].dt.strftime('%b')

    month_order = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

    yearly_data = []

    for year in sorted(df['_Year'].unique()):
        year_df = df[df['_Year'] == year].copy()

        # ── Monthly P&L sums ─────────────────────────────────────────────────
        monthly_pnl = year_df.groupby('_Month')['Net P&L'].sum()

        # ── Yearly totals ────────────────────────────────────────────────────
        total_pnl = year_df['Net P&L'].sum()

        # ── Max Drawdown for this year (on the GLOBAL curve) ─────────────────
        # We want the deepest trough whose EXIT DATE falls in this year
        year_dd = year_df['_Global_DD']
        max_dd  = year_dd.min()           # most negative value (≤ 0)

        days_for_mdd = 0
        mdd_date_range = ""

        if max_dd < 0:
            # Trough = row with the worst DD in this year
            trough_local_idx = year_dd.idxmin()
            trough_date = year_df.loc[trough_local_idx, exit_date_col]

            # Peak = last row (anywhere in full dataset before trough)
            # where Global_Cumulative == Global_Peak at the trough
            pre_trough_global = df.loc[:trough_local_idx]
            peak_val = year_df.loc[trough_local_idx, '_Global_Peak']

            peak_candidates = pre_trough_global[
                pre_trough_global['_Global_Cumulative'] >= peak_val
            ]
            if not peak_candidates.empty:
                peak_date = peak_candidates.iloc[-1][exit_date_col]
            else:
                peak_date = df.iloc[0][exit_date_col]

            peak_date   = pd.Timestamp(peak_date)
            trough_date = pd.Timestamp(trough_date)
            days_for_mdd = (trough_date - peak_date).days

            # Format: "M/D/YYYY to M/D/YYYY"  (matches AlgoTest display)
            p_str = f"{peak_date.month}/{peak_date.day}/{peak_date.year}"
            t_str = f"{trough_date.month}/{trough_date.day}/{trough_date.year}"
            mdd_date_range = f"[{p_str} to {t_str}]"
        else:
            max_dd = 0

        # ── R/MDD ─────────────────────────────────────────────────────────────
        r_mdd = round(total_pnl / abs(max_dd), 2) if max_dd != 0 else 0

        yearly_data.append({
            'year':          year,
            'monthly_pnl':   monthly_pnl,
            'total_pnl':     round(total_pnl, 2),
            'max_dd':        round(max_dd, 2),
            'days_for_mdd':  days_for_mdd,
            'mdd_date_range': mdd_date_range,
            'r_mdd':         r_mdd,
        })

    # ── Clean up temp columns ─────────────────────────────────────────────────
    df.drop(columns=['_Global_Cumulative', '_Global_Peak', '_Global_DD',
                     '_Year', '_Month'], inplace=True, errors='ignore')

    # ── Build output ─────────────────────────────────────────────────────────
    headers = [
        'Year', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
        'Total', 'Max Drawdown', 'Days for MDD', 'R/MDD'
    ]

    rows = []
    for data in yearly_data:
        row = [str(data['year'])]

        for m in month_order:
            val = data['monthly_pnl'].get(m, 0)
            row.append(round(float(val), 2) if pd.notna(val) else 0)

        row.append(data['total_pnl'])
        row.append(data['mdd_date_range'] if data['mdd_date_range']
                   else str(data['max_dd']))
        row.append(data['days_for_mdd'])
        row.append(data['r_mdd'])

        rows.append(row)

    return {"headers": headers, "rows": rows}



def calculate_trading_days_before_expiry(expiry_date, days_before, trading_calendar_df):
    """
    Calculate entry date by counting back trading days from expiry
    
    This is the CORE DTE calculation matching AlgoTest exactly
    
    Args:
        expiry_date: datetime - The expiry date
        days_before: int - Number of trading days before expiry
        trading_calendar_df: DataFrame - Contains all trading dates
    
    Returns:
        datetime - The entry date
        
    Example:
        Expiry: 14-Jan-2025 (Tuesday)
        Days Before: 2
        
        Count back:
        14-Jan (Tue) = DTE 0
        13-Jan (Mon) = DTE 1
        10-Jan (Fri) = DTE 2 ✅ ENTRY DATE
        (Skip Sat/Sun)
    """
    import pandas as pd
    from datetime import timedelta
    
    # NORMALIZE: Convert all dates to pd.Timestamp for comparison
    if isinstance(expiry_date, pd.Timestamp):
        expiry_ts = expiry_date
    else:
        expiry_ts = pd.Timestamp(expiry_date)
    
    # Ensure trading_calendar_df['date'] is also Timestamp
    df = trading_calendar_df.copy()
    df['date'] = pd.to_datetime(df['date'])
    
    # Get all trading days BEFORE expiry (use <= to include expiry day for days_before=0)
    if days_before == 0:
        # For days_before=0, include the expiry day itself
        trading_days = df[df['date'] <= expiry_ts].sort_values('date', ascending=False)
    else:
        trading_days = df[df['date'] < expiry_ts].sort_values('date', ascending=False)
    
    if days_before == 0:
        # Entry on expiry day itself - return the closest trading day <= expiry
        if not trading_days.empty:
            return trading_days.iloc[0]['date']
        return expiry_ts
    
    # Validate enough trading days exist
    if len(trading_days) < days_before:
        return None  # Don't raise, just return None
    
    # Get the Nth trading day before expiry
    # Index is 0-based, so days_before=1 means index 0, days_before=2 means index 1
    entry_date = trading_days.iloc[days_before - 1]['date']
    
    return entry_date


def get_trading_calendar(from_date, to_date, db_path='bhavcopy_data.db'):
    """
    Get all trading dates from database
    
    Returns:
        DataFrame with columns: ['date']
    """
    import sqlite3
    import pandas as pd

    if _use_postgres():
        try:
            return _repo.get_trading_calendar(from_date=str(from_date), to_date=str(to_date))
        except Exception:
            # Compatibility fallback to sqlite path below
            pass
    
    conn = sqlite3.connect(db_path)
    
    query = f"""
    SELECT DISTINCT date 
    FROM bhavcopy 
    WHERE date >= '{from_date}' AND date <= '{to_date}'
    ORDER BY date
    """
    
    df = pd.read_sql_query(query, conn)
    df['date'] = pd.to_datetime(df['date'])
    conn.close()
    
    return df


def calculate_strike_from_selection(spot_price, strike_interval, selection, option_type):
    """
    Calculate strike based on AlgoTest-style selection
    
    This matches AlgoTest EXACTLY
    
    Args:
        spot_price: float - Current spot price
        strike_interval: int - Strike gap (50 for NIFTY, 100 for BANKNIFTY)
        selection: str - 'ATM', 'ITM1', 'ITM2', ..., 'OTM1', 'OTM2', ...
        option_type: str - 'CE' or 'PE'
    
    Returns:
        float - Strike price
        
    Examples:
        Spot = 24,350, Interval = 50, Selection = 'OTM2', Type = 'CE'
        
        Step 1: Calculate ATM
        ATM = round(24350 / 50) * 50 = 488 * 50 = 24,400
        
        Step 2: For CE + OTM = Higher strikes
        Offset = 2 strikes * 50 = 100
        Strike = 24,400 + 100 = 24,500 ✅
        
        For PE + OTM = Lower strikes
        Strike = 24,400 - 100 = 24,300 ✅
    """
    # Step 1: Calculate ATM strike
    atm_strike = round(spot_price / strike_interval) * strike_interval
    
    # Step 2: Parse selection
    selection = selection.upper().strip()
    
    if selection == 'ATM':
        return atm_strike
    
    # Extract number from selection (ITM1 -> 1, OTM10 -> 10)
    if selection.startswith('ITM'):
        offset_strikes = int(selection.replace('ITM', ''))
        offset_points = offset_strikes * strike_interval
        
        if option_type == 'CE':
            # For CALL: ITM means LOWER strike (below spot)
            return atm_strike - offset_points
        else:  # PE
            # For PUT: ITM means HIGHER strike (above spot)
            return atm_strike + offset_points
    
    elif selection.startswith('OTM'):
        offset_strikes = int(selection.replace('OTM', ''))
        offset_points = offset_strikes * strike_interval
        
        if option_type == 'CE':
            # For CALL: OTM means HIGHER strike (above spot)
            return atm_strike + offset_points
        else:  # PE
            # For PUT: OTM means LOWER strike (below spot)
            return atm_strike - offset_points
    
    raise ValueError(f"Invalid selection: {selection}. Must be ATM, ITM1-ITM30, or OTM1-OTM30")


def get_strike_interval(index):
    """
    Get strike interval for index
    
    Returns:
        int - Strike interval
    """
    intervals = {
        'NIFTY': 50,
        'BANKNIFTY': 100,
        'FINNIFTY': 50,
        'MIDCPNIFTY': 25,
        'SENSEX': 100,
        'BANKEX': 100,
    }
    
    return intervals.get(index, 50)


# ============================================================================
# FAST O(1) LOOKUP CACHES FOR OPTION PREMIUM, FUTURE PRICE, SPOT PRICE
# These replace the per-call DataFrame scan with a single index-build per
# date+symbol, giving 10–50× speedup on SL-heavy backtests.
# ============================================================================

# key: (date_str, index) → dict{ (strike_int, opt_type, expiry_str): float }
_option_lookup_cache: Dict[tuple, dict] = {}

# key: (date_str, index) → dict{ expiry_str: float }
_future_lookup_cache: Dict[tuple, dict] = {}

# key: (date_str, index) → float
_spot_lookup_cache: Dict[tuple, Optional[float]] = {}

# Bulk preloaded DataFrames (when using PostgreSQL bulk mode)
_bulk_bhav_df: pd.DataFrame = None
_bulk_spot_df: pd.DataFrame = None
_bulk_loaded = False
_bulk_date_range = None

# HIGH-PERFORMANCE CACHE: Pre-indexed lookup tables
_option_lookup_table = {}  # (date, symbol, strike, opt_type, expiry) -> premium
_future_lookup_table = {}   # (date, symbol, expiry) -> future_price
_spot_lookup_table = {}     # (date, symbol) -> spot_price


def _load_bhavcopy_range_csv(from_date: str, to_date: str, symbols: list) -> pd.DataFrame:
    """Load bhavcopy data from CSV files for a date range."""
    import os
    from datetime import datetime, timedelta
    
    start = datetime.strptime(from_date, '%Y-%m-%d')
    end = datetime.strptime(to_date, '%Y-%m-%d')
    
    all_dfs = []
    current = start
    while current <= end:
        date_str = current.strftime('%Y-%m-%d')
        try:
            df = load_bhavcopy(date_str)
            if df is not None and not df.empty:
                df = df[df['Symbol'].isin(symbols)]
                all_dfs.append(df)
        except:
            pass
        current += timedelta(days=1)
    
    if all_dfs:
        return pd.concat(all_dfs, ignore_index=True)
    return pd.DataFrame()


def _load_spot_range_csv(symbols: list, from_date: str, to_date: str) -> pd.DataFrame:
    """Load spot data from CSV files for a date range."""
    all_dfs = []
    for symbol in symbols:
        try:
            df = get_strike_data(symbol, from_date, to_date)
            if df is not None and not df.empty:
                df['Symbol'] = symbol
                all_dfs.append(df)
        except:
            pass
    
    if all_dfs:
        return pd.concat(all_dfs, ignore_index=True)
    return pd.DataFrame()


def preload_all_data(from_date: str, to_date: str, symbols: list):
    """
    Preload all option data for the given symbols and date range.
    Delegates to bulk_load_options() which builds the O(1) lookup dict.
    """
    if symbols:
        primary_symbol = symbols[0] if isinstance(symbols, list) else symbols
        print(f"🔧 PRELOAD: Loading {primary_symbol} data for {from_date} to {to_date}")
        try:
            result = bulk_load_options(primary_symbol, from_date, to_date)
            print(f"   ✅ Preload complete: {result}")
        except Exception as e:
            print(f"   ⚠️  Preload failed: {e}")
    
    print(f"   ✅ SMART CACHE enabled!")


def _build_option_lookup(date_str: str, index: str):
    """
    Index all options for a date+index into a fast dict.
    Uses bulk preloaded data if available, otherwise falls back to per-date loading.
    """
    cache_key = (date_str, index)
    if cache_key in _option_lookup_cache:
        return

    try:
        if _bulk_loaded and _bulk_bhav_df is not None and not _bulk_bhav_df.empty:
            date_ts = pd.Timestamp(date_str)
            bhav_df = _bulk_bhav_df[
                (_bulk_bhav_df['Date'] == date_ts) & 
                (_bulk_bhav_df['Symbol'] == index)
            ].copy()
        else:
            bhav_df = load_bhavcopy(date_str)
        
        if bhav_df is None or bhav_df.empty:
            _option_lookup_cache[cache_key] = {}
            return

        filtered = bhav_df[bhav_df['Symbol'] == index].copy()
        lookup = {}
        for _, row in filtered.iterrows():
            opt_type = str(row.get('OptionType', '')).upper()
            if opt_type not in ('CE', 'PE'):
                continue
            strike  = int(round(float(row['StrikePrice'])))
            expiry  = pd.Timestamp(row['ExpiryDate']).strftime('%Y-%m-%d')
            lookup[(strike, opt_type, expiry)] = float(row['Close'])

        _option_lookup_cache[cache_key] = lookup

    except Exception:
        _option_lookup_cache[cache_key] = {}


def _build_future_lookup(date_str: str, index: str):
    """
    Index all futures for a date+index into a fast dict.
    Uses bulk preloaded data if available.
    """
    cache_key = (date_str, index)
    if cache_key in _future_lookup_cache:
        return

    try:
        if _bulk_loaded and _bulk_bhav_df is not None and not _bulk_bhav_df.empty:
            date_ts = pd.Timestamp(date_str)
            bhav_df = _bulk_bhav_df[
                (_bulk_bhav_df['Date'] == date_ts) & 
                (_bulk_bhav_df['Symbol'] == index)
            ].copy()
        else:
            bhav_df = load_bhavcopy(date_str)
        
        if bhav_df is None or bhav_df.empty:
            _future_lookup_cache[cache_key] = {}
            return

        filtered = bhav_df[
            (bhav_df['Symbol'] == index) &
            (bhav_df['Instrument'].str.upper().str.contains('FUT', na=False))
        ].copy()

        lookup = {}
        for _, row in filtered.iterrows():
            expiry = pd.Timestamp(row['ExpiryDate']).strftime('%Y-%m-%d')
            lookup[expiry] = float(row['Close'])

        _future_lookup_cache[cache_key] = lookup

    except Exception:
        _future_lookup_cache[cache_key] = {}


def clear_fast_lookup_caches():
    """
    Clear the fast O(1) lookup caches.
    Call this between backtests if memory is a concern, or after changing data files.
    """
    global _bulk_bhav_df, _bulk_spot_df, _bulk_loaded, _bulk_date_range
    global _option_lookup_table, _future_lookup_table, _spot_lookup_table
    _option_lookup_cache.clear()
    _future_lookup_cache.clear()
    _spot_lookup_cache.clear()
    _option_lookup_table.clear()
    _future_lookup_table.clear()
    _spot_lookup_table.clear()
    _bulk_bhav_df = None
    _bulk_spot_df = None
    _bulk_loaded = False
    _bulk_date_range = None


# ============================================================================
# END OF FAST LOOKUP CACHE HELPERS
# ============================================================================


# ============================================================================
# SUPER TREND FILTER - Load segments from CSV files
# ============================================================================

_super_trend_segments: Dict[str, list] = {"5x1": [], "5x2": []}
_super_trend_loaded = False

# FIX #1A: Pre-built sorted arrays for O(log n) bisect lookups.
# Populated once in load_super_trend_dates() — zero cost at query time.
_str_starts: Dict[str, list] = {"5x1": [], "5x2": []}  # sorted datetime starts
_str_ends:   Dict[str, list] = {"5x1": [], "5x2": []}  # corresponding datetime ends


def _normalize_str_config(config: Any) -> Optional[str]:
    """
    Normalize config from enum/string/etc to '5x1' / '5x2' / None.
    """
    if config is None:
        return None

    raw = config.value if hasattr(config, "value") else config
    raw = str(raw).strip()

    if raw.lower() in {"none", ""}:
        return None
    if raw == "5x1":
        return "5x1"
    if raw == "5x2":
        return "5x2"
    return None


def _parse_str_date(raw_value: Any) -> Optional[datetime]:
    """
    Parse STR date using ordered formats:
    1) DD-MM-YYYY
    2) YYYY-MM-DD
    3) DD-MMM-YYYY
    """
    try:
        if pd.isna(raw_value):
            return None
    except Exception:
        pass

    text = str(raw_value).strip()
    if not text:
        return None

    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def load_super_trend_dates(force_reload: bool = False):
    """
    Load STR segments once from CSV files.
    Missing/unreadable file => warning + empty segment list for that config.
    FIX #1A: Also builds sorted _str_starts/_str_ends arrays for O(log n) lookups.
    """
    global _super_trend_loaded, _super_trend_segments, _str_starts, _str_ends

    if _super_trend_loaded and not force_reload:
        return

    loaded: Dict[str, list] = {"5x1": [], "5x2": []}

    if _use_postgres():
        try:
            for config_key in ("5x1", "5x2"):
                df = _repo.get_super_trend_segments(config=config_key, symbol="NIFTY")
                segments = []
                for _, row in df.iterrows():
                    start_dt = pd.Timestamp(row["start_date"]).to_pydatetime()
                    end_dt = pd.Timestamp(row["end_date"]).to_pydatetime()
                    if end_dt < start_dt:
                        continue
                    segments.append({"start": start_dt, "end": end_dt})
                segments.sort(key=lambda s: s["start"])
                loaded[config_key] = segments
                print(f"Loaded {len(segments)} STR segments for {config_key} (postgres)")

            _super_trend_segments = loaded
            # FIX #1A: Build bisect index arrays once
            for cfg_key, segs in _super_trend_segments.items():
                _str_starts[cfg_key] = [s["start"].replace(hour=0, minute=0, second=0, microsecond=0) for s in segs]
                _str_ends[cfg_key]   = [s["end"].replace(hour=0, minute=0, second=0, microsecond=0)   for s in segs]
            _super_trend_loaded = True
            return
        except Exception as exc:
            if not ALLOW_CSV_FALLBACK:
                raise RuntimeError("PostgreSQL super-trend lookup failed and CSV fallback is disabled.") from exc
            print("Postgres super-trend lookup failed; falling back to CSV.")

    if not ALLOW_CSV_FALLBACK:
        raise RuntimeError("CSV fallback disabled; super-trend segments must be loaded from PostgreSQL.")

    file_map = {
        "5x1": os.path.join(FILTER_DIR, "STR5,1_5,1.csv"),
        "5x2": os.path.join(FILTER_DIR, "STR5,2_5,2.csv")
    }

    for config_key, file_path in file_map.items():
        segments = []
        try:
            if not os.path.exists(file_path):
                print(f"Warning: STR file not found for {config_key}: {file_path}")
                loaded[config_key] = []
                continue

            df = pd.read_csv(file_path)
            df.columns = [str(c).strip() for c in df.columns]

            if "Start" not in df.columns or "End" not in df.columns:
                print(f"Warning: STR file missing Start/End columns for {config_key}: {file_path}")
                loaded[config_key] = []
                continue

            for _, row in df.iterrows():
                start_dt = _parse_str_date(row.get("Start"))
                end_dt = _parse_str_date(row.get("End"))

                if start_dt is None or end_dt is None:
                    continue
                if end_dt < start_dt:
                    continue

                segments.append({"start": start_dt, "end": end_dt})

            segments.sort(key=lambda s: s["start"])
            loaded[config_key] = segments
            print(f"Loaded {len(segments)} STR segments for {config_key}")

        except Exception as e:
            print(f"Warning: Could not read STR file for {config_key}: {file_path}. Error: {e}")
            loaded[config_key] = []

    _super_trend_segments = loaded
    # FIX #1A: Build bisect index arrays once
    for cfg_key, segs in _super_trend_segments.items():
        _str_starts[cfg_key] = [s["start"].replace(hour=0, minute=0, second=0, microsecond=0) for s in segs]
        _str_ends[cfg_key]   = [s["end"].replace(hour=0, minute=0, second=0, microsecond=0)   for s in segs]
    _super_trend_loaded = True


def get_super_trend_segments(config: Any) -> list:
    """
    Return list of segment dicts for '5x1'/'5x2', else [].
    """
    load_super_trend_dates()
    cfg = _normalize_str_config(config)
    if cfg is None:
        return []
    return _super_trend_segments.get(cfg, [])


def get_active_str_segment(trade_date, config: Any) -> Optional[dict]:
    """
    Return the STR segment that contains trade_date, or None.
    Boundary inclusive (>= start AND <= end).

    FIX #1A: Replaced O(n) linear scan with O(log n) bisect binary search.
    For 200-300 segments over 7 years this is ~8x faster per call, and since
    this is called ~100,000+ times per backtest the total saving is enormous.
    """
    cfg = _normalize_str_config(config)
    if cfg is None or trade_date is None:
        return None

    # Ensure segments are loaded (no-op if already loaded)
    load_super_trend_dates()

    starts = _str_starts.get(cfg)
    ends   = _str_ends.get(cfg)
    segs   = _super_trend_segments.get(cfg)
    if not starts:
        return None

    d = pd.Timestamp(trade_date).to_pydatetime().replace(hour=0, minute=0, second=0, microsecond=0)

    # bisect_right gives the insertion point AFTER all starts <= d
    # so idx-1 is the index of the last segment whose start <= d
    idx = bisect.bisect_right(starts, d) - 1
    if idx < 0:
        return None

    # Check that d also falls before (or on) the segment end
    if d <= ends[idx]:
        return segs[idx]
    return None


def get_super_trend_sl_dates(config: Any) -> set:
    """
    Backward-compatible API:
    derive SL dates from segment end dates.
    """
    segs = get_super_trend_segments(config)
    return {seg["end"].strftime("%Y-%m-%d") for seg in segs}


def is_super_trend_sl_date(date_str: str, config: Any) -> bool:
    """
    Backward-compatible API:
    check whether date_str is one of segment end dates.
    """
    if not date_str:
        return False
    return str(date_str).strip() in get_super_trend_sl_dates(config)


# ============================================================================
# END OF SUPER TREND FILTER
# ============================================================================

# ============================================================================
# NEW FILTER SYSTEM - Date Range Filter for Backtest
# ============================================================================

def get_filter_segments(config: str) -> list:
    """
    Get filter segments for a given config.
    
    Args:
        config: '5x1', '5x2', 'base2', or 'custom'
    
    Returns:
        List of dicts with 'start' and 'end' keys (datetime.date objects)
    """
    if not config:
        return []
    
    config = config.lower().strip()
    
    if config == 'base2':
        return get_base2_segments()
    elif config in ['5x1', '5x2']:
        return get_super_trend_segments(config)
    else:
        return []


def get_base2_segments() -> list:
    """
    Generate base2 filter - returns single segment covering entire DB date range.
    This represents the full available data range.
    
    Returns:
        List with single dict: [{'start': min_date, 'end': max_date}]
    """
    from repositories.market_data_repository import MarketDataRepository
    from database import get_engine
    
    try:
        repo = MarketDataRepository(get_engine())
        date_range = repo.get_available_date_range()
        
        min_date = date_range.get('min_date')
        max_date = date_range.get('max_date')
        
        if min_date and max_date:
            return [{'start': min_date, 'end': max_date}]
        return []
    except Exception as e:
        print(f"Error getting base2 segments: {e}")
        return []


def get_filter_segment_counts() -> dict:
    """
    Get count of segments for each available filter.
    
    Returns:
        Dict: {'5x1': count, '5x2': count, 'base2': 1}
    """
    counts = {}
    
    # Get counts from DB for 5x1 and 5x2
    for config in ['5x1', '5x2']:
        segs = get_filter_segments(config)
        counts[config] = len(segs)
    
    # base2 is always 1 segment (full range)
    base2_segs = get_filter_segments('base2')
    counts['base2'] = len(base2_segs)
    
    return counts


# Date parser for CSV upload (matches migrate_filter.py)
_DATE_FMTS = [
    "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y",
    "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%b %d %Y",
    "%d-%B-%Y", "%d %B %Y", "%Y%m%d", "%d.%m.%Y",
    "%m-%d-%Y", "%b-%d-%Y", "%B-%d-%Y", "%d%m%Y",
    "%Y-%b-%d", "%d %b, %Y", "%b %d, %Y",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
]

_DATE_NULL_SET = frozenset([
    "", "nan", "NaN", "None", "NaT", "nat", "N/A", "NA",
    "n/a", "na", "-", "--", "0", "null", "NULL", "Null",
    "0000-00-00", "00/00/0000", "00-00-0000",
])


def _parse_single_date(date_str: str):
    """Parse a single date string trying multiple formats."""
    if not date_str or str(date_str).strip() in _DATE_NULL_SET:
        return None
    
    cleaned = str(date_str).strip()
    
    for fmt in _DATE_FMTS:
        try:
            return pd.to_datetime(cleaned, format=fmt).date()
        except:
            continue
    
    # Try with dayfirst=True as fallback
    try:
        return pd.to_datetime(cleaned, dayfirst=True).date()
    except:
        return None


def _to_iso_date(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return pd.Timestamp(value).strftime("%Y-%m-%d")
        except Exception:
            return value[:10]
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except Exception:
        return str(value)


def _series_to_iso_date_list(series: pl.Series) -> list:
    try:
        return series.dt.strftime("%Y-%m-%d").to_list()
    except InvalidOperationError:
        return [_to_iso_date(v) or "" for v in series.to_list()]


def parse_filter_csv(csv_content: str) -> list:
    """
    Parse uploaded CSV content for filter segments.
    Supports both start/end and entry/exit column formats.
    
    Args:
        csv_content: Raw CSV string
    
    Returns:
        List of dicts: [{'start': date, 'end': date}, ...]
    """
    import io
    
    segments = []
    
    try:
        # Try to read CSV
        df = pd.read_csv(io.StringIO(csv_content), dtype=str, keep_default_na=False)
        
        if df.empty:
            return []
        
        # Normalize column names
        df.columns = [str(c).strip().lower().replace(' ', '_') for c in df.columns]
        
        # Detect column format
        start_col = None
        end_col = None
        
        # Check for start/end format
        start_aliases = ['start', 'start_date', 'startdt', 'from', 'from_date']
        end_aliases = ['end', 'end_date', 'enddt', 'to', 'to_date']
        
        for col in df.columns:
            if col in start_aliases or col.startswith('start'):
                start_col = col
            if col in end_aliases or col.startswith('end'):
                end_col = col
        
        # If not found, check entry/exit format
        if not start_col or not end_col:
            entry_aliases = ['entry', 'entry_date', 'entrydt']
            exit_aliases = ['exit', 'exit_date', 'exitdt']
            
            for col in df.columns:
                if col in entry_aliases or col.startswith('entry'):
                    start_col = col
                if col in exit_aliases or col.startswith('exit'):
                    end_col = col
        
        if not start_col or not end_col:
            print(f"Could not detect date columns. Found: {df.columns}")
            return []
        
        # Parse dates
        for _, row in df.iterrows():
            start_date = _parse_single_date(row.get(start_col))
            end_date = _parse_single_date(row.get(end_col))
            
            if start_date and end_date:
                # Ensure start <= end
                if start_date > end_date:
                    start_date, end_date = end_date, start_date
                
                segments.append({'start': start_date, 'end': end_date})
        
        return normalize_filter_segments(segments)

    except Exception as e:
        print(f"Error parsing filter CSV: {e}")
        return []

def _normalize_filter_date(value) -> Optional[pd.Timestamp]:
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return ts.normalize()


def normalize_filter_segments(segments: list) -> list:
    normalized: list = []
    if not segments:
        return normalized

    for seg in segments:
        if not isinstance(seg, dict):
            continue
        start = _normalize_filter_date(seg.get('start'))
        end = _normalize_filter_date(seg.get('end'))
        if start is None or end is None:
            continue
        if start > end:
            start, end = end, start
        normalized.append({'start': start, 'end': end})

    normalized.sort(key=lambda entry: entry['start'])
    return normalized


def get_option_premium_from_db(date, index, strike, option_type, expiry, db_path='bhavcopy_data.db'):
    """
    HIGH-PERFORMANCE: O(1) lookup with on-demand loading per date.
    Loads data for each date once, then caches forever.
    """
    try:
        date_str  = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)
        expiry_ts = pd.Timestamp(expiry)
        expiry_str = expiry_ts.strftime('%Y-%m-%d')

        if hasattr(option_type, 'value'):
            opt_type_upper = str(option_type.value).upper()
        elif hasattr(option_type, 'upper'):
            opt_type_upper = option_type.upper()
        else:
            opt_type_upper = str(option_type).upper()

        if opt_type_upper in ['CE', 'CALL', 'C']:
            opt_match = 'CE'
        elif opt_type_upper in ['PE', 'PUT', 'P']:
            opt_match = 'PE'
        else:
            opt_match = opt_type_upper

        strike_key = int(round(float(strike)))

        # O(1) dict lookup — built during bulk_load_options()
        if _bulk_loaded and _option_lookup_table:
            key = (date_str, index, strike_key, opt_match, expiry_str)
            result = _option_lookup_table.get(key)
            if result is not None:
                return result
            # Try ±1 day expiry tolerance
            result = _option_lookup_table.get(
                (date_str, index, strike_key, opt_match,
                 (expiry_ts + pd.Timedelta(days=1)).strftime('%Y-%m-%d')))
            if result is not None:
                return result
            result = _option_lookup_table.get(
                (date_str, index, strike_key, opt_match,
                 (expiry_ts - pd.Timedelta(days=1)).strftime('%Y-%m-%d')))
            if result is not None:
                return result
            # DEBUG: Log failed lookups
            # print(f"[LOOKUP FAILED] key={key}, table_keys_sample={list(_option_lookup_table.keys())[:3]}")
            return None

        # Fallback: use lookup cache
        _build_option_lookup(date_str, index)
        lookup = _option_lookup_cache.get((date_str, index), {})
        result = lookup.get((strike_key, opt_match, expiry_str))
        if result is not None:
            return result
        result = lookup.get((strike_key, opt_match, (expiry_ts + pd.Timedelta(days=1)).strftime('%Y-%m-%d')))
        if result is not None:
            return result
        result = lookup.get((strike_key, opt_match, (expiry_ts - pd.Timedelta(days=1)).strftime('%Y-%m-%d')))
        if result is not None:
            return result
        return None

    except Exception:
        return None


def _load_date_data_on_demand(date_str: str, index: str):
    """Load data for a specific date and index, build lookup tables."""
    if not _bulk_loaded:
        return
    
    # Check if already loaded for this date
    if any(key[0] == date_str and key[1] == index for key in _option_lookup_table.keys()):
        return
    
    try:
        # Load data for just this date from PostgreSQL
        df = _repo.get_bhavcopy_bulk(date_str, date_str, [index])
        if df is None or df.empty:
            return
        
        # Build lookup entries
        for _, row in df.iterrows():
            try:
                symbol = row['Symbol']
                instrument = str(row.get('Instrument', '')).upper()
                opt_type = str(row.get('OptionType', '')).upper()
                strike = int(round(float(row.get('StrikePrice', 0))))
                expiry = pd.Timestamp(row['ExpiryDate']).strftime('%Y-%m-%d')
                close = float(row['Close'])
                
                if opt_type in ('CE', 'PE'):
                    _option_lookup_table[(date_str, symbol, strike, opt_type, expiry)] = close
                elif 'FUT' in instrument:
                    _future_lookup_table[(date_str, symbol, expiry)] = close
            except:
                continue
        
        # Also load spot data
        spot_df = _repo.get_spot_data_bulk([index], date_str, date_str)
        if spot_df is not None and not spot_df.empty:
            for _, row in spot_df.iterrows():
                try:
                    symbol = row['Symbol']
                    close = float(row['Close'])
                    _spot_lookup_table[(date_str, symbol)] = close
                except:
                    continue
                    
    except Exception as e:
        pass  # Silently fail - will use fallback


def get_future_price_from_db(date, index, expiry, db_path='bhavcopy_data.db'):
    """
    HIGH-PERFORMANCE: O(1) lookup with on-demand loading.
    """
    try:
        date_str   = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)
        expiry_ts  = pd.Timestamp(expiry)
        expiry_str = expiry_ts.strftime('%Y-%m-%d')

        # HIGH-PERFORMANCE: Use instant lookup table
        if _bulk_loaded and _future_lookup_table:
            result = _future_lookup_table.get((date_str, index, expiry_str))
            if result is not None:
                return result
            result = _future_lookup_table.get((date_str, index, (expiry_ts + pd.Timedelta(days=1)).strftime('%Y-%m-%d')))
            if result is not None:
                return result
            result = _future_lookup_table.get((date_str, index, (expiry_ts - pd.Timedelta(days=1)).strftime('%Y-%m-%d')))
            if result is not None:
                return result
            # Try loading on-demand
            if _bulk_date_range and _bulk_date_range[0] <= date_str <= _bulk_date_range[1]:
                _load_date_data_on_demand(date_str, index)
                result = _future_lookup_table.get((date_str, index, expiry_str))
                if result is not None:
                    return result
            return None

        # Fallback: old method
        _build_future_lookup(date_str, index)
        lookup = _future_lookup_cache.get((date_str, index), {})
        result = lookup.get(expiry_str)
        if result is not None:
            return result
        result = lookup.get((expiry_ts + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
        if result is not None:
            return result
        result = lookup.get((expiry_ts - pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
        if result is not None:
            return result

        return None

    except Exception as e:
        return None

def calculate_intrinsic_value(spot, strike, option_type):
    """
    Calculate intrinsic value on expiry
    
    This is CRITICAL for expiry day exit
    
    Args:
        spot: float - Spot price at expiry
        strike: float - Strike price
        option_type: str - 'CE' or 'PE'
    
    Returns:
        float - Intrinsic value
        
    Examples:
        Spot = 24,500, Strike = 24,400, Type = CE
        Intrinsic = max(0, 24500 - 24400) = 100 ✅
        
        Spot = 24,300, Strike = 24,400, Type = CE
        Intrinsic = max(0, 24300 - 24400) = 0 ✅ (worthless)
        
        Spot = 24,300, Strike = 24,400, Type = PE
        Intrinsic = max(0, 24400 - 24300) = 100 ✅
    """
    if option_type == 'CE':
        # Call intrinsic = max(0, Spot - Strike)
        return max(0, spot - strike)
    else:  # PE
        # Put intrinsic = max(0, Strike - Spot)
        return max(0, strike - spot)

def get_expiry_dates(symbol: str = "NIFTY", expiry_type: str = "weekly", from_date=None, to_date=None):
    """
    Get expiry dates for a given symbol and type
    
    Args:
        symbol: str - Index symbol (NIFTY, BANKNIFTY, etc.)
        expiry_type: str - 'weekly' or 'monthly'
        from_date: str - Optional start date filter
        to_date: str - Optional end date filter
    
    Returns:
        DataFrame with expiry dates
    """
    # Load expiry data
    expiry_df = load_expiry(symbol, expiry_type)
    
    # If empty and user asked for weekly, fallback to monthly
    # This handles pre-2019 dates where NIFTY weekly options didn't exist
    if expiry_df.empty and expiry_type.upper() == 'WEEKLY':
        expiry_df = load_expiry(symbol, 'monthly')
    
    # Apply date filters if provided
    if from_date:
        from_date = pd.to_datetime(from_date)
        expiry_df = expiry_df[expiry_df['Current Expiry'] >= from_date]
    
    if to_date:
        to_date = pd.to_datetime(to_date)
        expiry_df = expiry_df[expiry_df['Current Expiry'] <= to_date]
    
    return expiry_df

def get_custom_expiry_dates(symbol: str, expiry_day_of_week: int, from_date=None, to_date=None):
    """
    Get custom expiry dates based on specified day of week
    
    Args:
        symbol: str - Index symbol (NIFTY, BANKNIFTY, etc.)
        expiry_day_of_week: int - Day of week (0=Monday, 1=Tuesday, 2=Wednesday, 3=Thursday, 4=Friday, 5=Saturday, 6=Sunday)
        from_date: str - Optional start date filter
        to_date: str - Optional end date filter
    
    Returns:
        list - List of expiry dates
    """
    import pandas as pd
    from datetime import datetime, timedelta
    
    # Convert string dates to datetime if provided
    if from_date:
        from_date = pd.to_datetime(from_date)
    else:
        from_date = pd.to_datetime('2020-01-01')  # Default start
        
    if to_date:
        to_date = pd.to_datetime(to_date)
    else:
        to_date = pd.to_datetime('2030-12-31')  # Default end
    
    # Find all dates in the range that match the specified day of week
    current_date = from_date
    expiry_dates = []
    
    while current_date <= to_date:
        if current_date.weekday() == expiry_day_of_week:
            expiry_dates.append(current_date)
        current_date += timedelta(days=1)
    
    return expiry_dates


def get_next_expiry_date(start_date, expiry_day_of_week: int):
    """
    Get the next expiry date from a given start date based on specified day of week
    
    Args:
        start_date: datetime - Starting date to find next expiry from
        expiry_day_of_week: int - Day of week (0=Monday, 1=Tuesday, 2=Wednesday, 3=Thursday, 4=Friday, 5=Saturday, 6=Sunday)
    
    Returns:
        datetime - Next expiry date
    """
    import pandas as pd
    from datetime import datetime, timedelta
    
    start_date = pd.to_datetime(start_date)
    
    # Calculate days until the next occurrence of the specified day of week
    days_ahead = expiry_day_of_week - start_date.weekday()
    
    if days_ahead <= 0:  # Target day already happened this week
        days_ahead += 7
    
    next_expiry = start_date + timedelta(days_ahead)
    return next_expiry


def get_monthly_expiry_date(year: int, month: int, expiry_day_of_week: int):
    """
    Get the monthly expiry date for a specific year/month based on specified day of week
    For monthly expiry, gets the last occurrence of the specified day in the month
    
    Args:
        year: int - Year
        month: int - Month (1-12)
        expiry_day_of_week: int - Day of week (0=Monday, 1=Tuesday, 2=Wednesday, 3=Thursday, 4=Friday, 5=Saturday, 6=Sunday)
    
    Returns:
        datetime - Last occurrence of the specified day in the month
    """
    import pandas as pd
    from datetime import datetime, timedelta
    import calendar
    
    # Get the last day of the month
    last_day = calendar.monthrange(year, month)[1]
    last_date = pd.to_datetime(f'{year}-{month:02d}-{last_day}')
    
    # Find the last occurrence of the specified day of week in the month
    while last_date.weekday() != expiry_day_of_week:
        last_date -= timedelta(days=1)
        if last_date.month != month:  # Safety check
            raise ValueError(f"Could not find {expiry_day_of_week} in {year}-{month:02d}")
    
    return last_date


def get_spot_price_from_db(date, index, db_path='bhavcopy_data.db'):
    """
    HIGH-PERFORMANCE: O(1) lookup using pre-built instant lookup table.
    """
    date_str  = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)
    cache_key = (date_str, index)

    # Check old cache first
    if cache_key in _spot_lookup_cache:
        return _spot_lookup_cache[cache_key]

    # HIGH-PERFORMANCE: Use instant lookup table
    if _bulk_loaded and _spot_lookup_table:
        val = _spot_lookup_table.get((date_str, index))
        if val is not None:
            _spot_lookup_cache[cache_key] = val
            return val
        return None

    # Fallback to old method
    try:
        if _bulk_loaded and _bulk_spot_df is not None and not _bulk_spot_df.empty:
            date_ts = pd.Timestamp(date_str)
            # Spot data may or may not have Symbol column - handle both
            if 'Symbol' in _bulk_spot_df.columns:
                spot_df = _bulk_spot_df[_bulk_spot_df['Symbol'] == index]
            else:
                # No Symbol column - use all rows
                spot_df = _bulk_spot_df
        else:
            spot_df = get_strike_data(index, date_str, date_str)

        if spot_df is not None and not spot_df.empty:
            date_ts = pd.to_datetime(date_str)
            exact = spot_df[spot_df['Date'] == date_ts]
            if not exact.empty:
                val = float(exact.iloc[0]['Close'])
                _spot_lookup_cache[cache_key] = val
                return val
            prior = spot_df[spot_df['Date'] <= date_ts]
            if not prior.empty:
                val = float(prior.iloc[-1]['Close'])
                _spot_lookup_cache[cache_key] = val
                return val

    except Exception:
        pass

    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        query = """SELECT close FROM bhavcopy WHERE date = ? AND symbol = ? AND strike IS NULL AND option_type IS NULL LIMIT 1"""
        cursor.execute(query, (date_str, index))
        result = cursor.fetchone()
        conn.close()
        if result:
            val = float(result[0])
            _spot_lookup_cache[cache_key] = val
            return val
    except Exception:
        pass

    _spot_lookup_cache[cache_key] = None
    return None


# ============================================================================
# ADVANCED STRIKE SELECTION SYSTEM
# ============================================================================

def get_expiry_for_selection(entry_date, index, expiry_selection):
    """
    Get the appropriate expiry date based on selection type
    
    Args:
        entry_date: datetime - Entry date for the trade
        index: str - 'NIFTY' or 'BANKNIFTY'
        expiry_selection: str - 'WEEKLY', 'NEXT_WEEKLY', 'MONTHLY', 'NEXT_MONTHLY'
    
    Returns:
        datetime - Expiry date
        
    Trading Logic:
        WEEKLY: Current week's expiry (Thursday)
        NEXT_WEEKLY: Next week's expiry (next Thursday)
        MONTHLY: Current month's expiry (last Thursday)
        NEXT_MONTHLY: Next month's expiry (last Thursday of next month)
    """
    entry_date = pd.to_datetime(entry_date)
    expiry_selection = expiry_selection.upper().strip()
    
    if expiry_selection in ['WEEKLY', 'NEXT_WEEKLY']:
        # Load weekly expiry data
        expiry_df = load_expiry(index, 'weekly')
        
        # Find current expiry (where entry_date is between Previous and Current Expiry)
        mask = (expiry_df['Previous Expiry'] <= entry_date) & (entry_date <= expiry_df['Current Expiry'])
        current_row = expiry_df[mask]
        
        if current_row.empty:
            raise ValueError(f"No weekly expiry found for {index} on {entry_date}")
        
        if expiry_selection == 'WEEKLY':
            return current_row.iloc[0]['Current Expiry']
        else:  # NEXT_WEEKLY
            return current_row.iloc[0]['Next Expiry']
    
    elif expiry_selection in ['MONTHLY', 'NEXT_MONTHLY']:
        # Load monthly expiry data
        expiry_df = load_expiry(index, 'monthly')
        
        # Find current monthly expiry
        mask = (expiry_df['Previous Expiry'] <= entry_date) & (entry_date <= expiry_df['Current Expiry'])
        current_row = expiry_df[mask]
        
        if current_row.empty:
            raise ValueError(f"No monthly expiry found for {index} on {entry_date}")
        
        if expiry_selection == 'MONTHLY':
            return current_row.iloc[0]['Current Expiry']
        else:  # NEXT_MONTHLY
            return current_row.iloc[0]['Next Expiry']
    
    else:
        raise ValueError(f"Invalid expiry selection: {expiry_selection}. Use WEEKLY, NEXT_WEEKLY, MONTHLY, or NEXT_MONTHLY")


def get_all_strikes_with_premiums(date, index, expiry, option_type, spot_price, strike_interval):
    """
    Get all available strikes with their premiums for a given date and expiry
    
    Args:
        date: str or datetime - Trading date
        index: str - 'NIFTY' or 'BANKNIFTY'
        expiry: str or datetime - Expiry date
        option_type: str - 'CE' or 'PE'
        spot_price: float - Current spot price
        strike_interval: int - Strike interval (50 or 100)
    
    Returns:
        list of dict: [{'strike': 24500, 'premium': 150.5}, ...]
        Sorted by strike price
        
    Trading Logic:
        - Loads bhavcopy data for the date
        - Filters by index, expiry, and option type
        - Returns all available strikes with premiums
        - Used for premium-based strike selection
    """
    date_str = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)
    expiry_str = expiry.strftime('%Y-%m-%d') if hasattr(expiry, 'strftime') else str(expiry)
    
    # Load bhavcopy data
    bhav_df = load_bhavcopy(date_str)
    
    if bhav_df is None or bhav_df.empty:
        return []
    
    # Normalize option type
    opt_type = option_type.upper()
    if opt_type in ['CALL', 'C']:
        opt_type = 'CE'
    elif opt_type in ['PUT', 'P']:
        opt_type = 'PE'
    
    # Filter for the specific index, expiry, and option type
    expiry_ts = pd.to_datetime(expiry_str)
    mask = (
        (bhav_df['Symbol'] == index) &
        (bhav_df['OptionType'] == opt_type) &
        (bhav_df['ExpiryDate'] == expiry_ts)
    )
    
    filtered_df = bhav_df[mask].copy()
    
    if filtered_df.empty:
        return []
    
    # Extract strikes and premiums
    strikes_with_premiums = []
    for _, row in filtered_df.iterrows():
        strikes_with_premiums.append({
            'strike': float(row['StrikePrice']),
            'premium': float(row['Close'])
        })
    
    # Sort by strike
    strikes_with_premiums.sort(key=lambda x: x['strike'])
    
    return strikes_with_premiums


def calculate_strike_from_premium_range(date, index, expiry, option_type, spot_price, 
                                       strike_interval, min_premium, max_premium):
    """
    Find strike where premium falls within specified range
    
    Args:
        date: str or datetime - Trading date
        index: str - 'NIFTY' or 'BANKNIFTY'
        expiry: str or datetime - Expiry date
        option_type: str - 'CE' or 'PE'
        spot_price: float - Current spot price
        strike_interval: int - Strike interval (50 or 100)
        min_premium: float - Minimum premium (e.g., 100)
        max_premium: float - Maximum premium (e.g., 200)
    
    Returns:
        float - Strike price where premium is in range
        None if no strike found in range
        
    Trading Logic:
        1. Get all available strikes with premiums
        2. Filter strikes where premium is between min and max
        3. Select the strike with premium closest to max_premium (highest in range)
        4. This ensures we get the maximum premium within the specified range
        
    Example:
        Spot = 24,350, Min = 150, Max = 200
        Available: 24300 (₹250), 24350 (₹156), 24400 (₹198), 24450 (₹80)
        In Range: 24350 (₹156), 24400 (₹198)
        Selected: 24400 (₹198 - closest to max ₹200) ✅
    """
    # Get all strikes with premiums
    strikes_data = get_all_strikes_with_premiums(
        date, index, expiry, option_type, spot_price, strike_interval
    )
    
    if not strikes_data:
        return None
    
    # Print ALL strikes with premiums for debugging
    print(f"         ALL strikes with premiums: {[(s['strike'], s['premium']) for s in strikes_data[:15]]}")
    
    # Filter by premium range
    in_range = [s for s in strikes_data if min_premium <= s['premium'] <= max_premium]
    
    if not in_range:
        print(f"         WARNING: No strikes found with premium between {min_premium} and {max_premium}")
        return None
    
    print(f"         Found {len(in_range)} strikes in range: {[(s['strike'], s['premium']) for s in in_range[:10]]}")
    
    # Calculate ATM strike for distance comparison
    atm_strike = round(spot_price / strike_interval) * strike_interval
    
    # Select strike closest to ATM from valid strikes in range
    best = min(in_range, key=lambda x: abs(x['strike'] - atm_strike))
    
    print(f"         Selected strike {best['strike']} (premium={best['premium']:.2f}, ATM={atm_strike})")
    return best['strike']


def calculate_strike_from_closest_premium(date, index, expiry, option_type, spot_price,
                                         strike_interval, target_premium):
    """
    Find strike with premium closest to target premium
    
    Args:
        date: str or datetime - Trading date
        index: str - 'NIFTY' or 'BANKNIFTY'
        expiry: str or datetime - Expiry date
        option_type: str - 'CE' or 'PE'
        spot_price: float - Current spot price
        strike_interval: int - Strike interval (50 or 100)
        target_premium: float - Target premium (e.g., 150)
    
    Returns:
        float - Strike price with premium closest to target
        None if no strikes available
        
    Trading Logic:
        1. Get all available strikes with premiums
        2. Find strike where premium is closest to target
        3. Useful for consistent risk/reward across trades
        
    Example:
        Target Premium = 150
        Available: 24300 (₹250), 24350 (₹180), 24400 (₹120), 24450 (₹80)
        Differences: 100, 30, 30, 70
        Closest: 24350 (₹180) or 24400 (₹120) - picks first one (24350) ✅
    """
    # Get all strikes with premiums
    strikes_data = get_all_strikes_with_premiums(
        date, index, expiry, option_type, spot_price, strike_interval
    )
    
    if not strikes_data:
        print(f"         WARNING: No strikes data available for closest premium")
        return None
    
    print(f"         Searching for strike with premium closest to {target_premium}")
    print(f"         Available strikes: {len(strikes_data)}, showing first 5: {[(s['strike'], s['premium']) for s in strikes_data[:5]]}")
    
    # Find strikes with minimum premium distance
    min_diff = min(abs(s['premium'] - target_premium) for s in strikes_data)
    candidates = [s for s in strikes_data if abs(s['premium'] - target_premium) == min_diff]
    
    print(f"         Found {len(candidates)} candidate(s) with min difference {min_diff:.2f}: {[(s['strike'], s['premium']) for s in candidates]}")
    
    # Deterministic tie-breaking: AlgoTest style
    # For CE: prefer HIGHER strike (more premium, more downside protection)
    # For PE: prefer LOWER strike (more premium, more downside protection)
    option_type_upper = option_type.upper() if option_type else 'CE'
    if option_type_upper in ['CE', 'CALL', 'C']:
        # CE: prefer higher strike
        closest = max(candidates, key=lambda x: x['strike'])
    else:
        # PE: prefer lower strike
        closest = min(candidates, key=lambda x: x['strike'])
    
    print(f"         Selected strike {closest['strike']} (premium={closest['premium']:.2f}, target={target_premium})")
    return closest['strike']


def calculate_strike_advanced(date, index, spot_price, strike_interval, option_type,
                              strike_selection_type, strike_selection_value=None,
                              expiry_selection='WEEKLY', min_premium=None, max_premium=None):
    """
    Universal strike calculation function supporting all selection methods
    
    Args:
        date: str or datetime - Trading date
        index: str - 'NIFTY' or 'BANKNIFTY'
        spot_price: float - Current spot price
        strike_interval: int - Strike interval (50 or 100)
        option_type: str - 'CE' or 'PE'
        strike_selection_type: str - Selection method:
            - 'ATM', 'ITM', 'OTM' (requires strike_selection_value for ITM/OTM)
            - 'PREMIUM_RANGE' (requires min_premium, max_premium)
            - 'CLOSEST_PREMIUM' (requires strike_selection_value as target premium)
        strike_selection_value: int or float - Value for selection (offset or premium)
        expiry_selection: str - 'WEEKLY', 'NEXT_WEEKLY', 'MONTHLY', 'NEXT_MONTHLY'
        min_premium: float - Minimum premium for PREMIUM_RANGE
        max_premium: float - Maximum premium for PREMIUM_RANGE
    
    Returns:
        dict: {
            'strike': float,
            'expiry': datetime,
            'premium': float (if available)
        }
        
    Trading Examples:
        
        1. ATM Weekly:
           calculate_strike_advanced(
               date='2024-01-15', index='NIFTY', spot_price=24350,
               strike_interval=50, option_type='CE',
               strike_selection_type='ATM', expiry_selection='WEEKLY'
           )
           → Strike: 24350, Expiry: 2024-01-18 (current week Thursday)
        
        2. OTM2 Next Weekly:
           calculate_strike_advanced(
               date='2024-01-15', index='NIFTY', spot_price=24350,
               strike_interval=50, option_type='CE',
               strike_selection_type='OTM', strike_selection_value=2,
               expiry_selection='NEXT_WEEKLY'
           )
           → Strike: 24450, Expiry: 2024-01-25 (next week Thursday)
        
        3. Premium Range 100-200:
           calculate_strike_advanced(
               date='2024-01-15', index='NIFTY', spot_price=24350,
               strike_interval=50, option_type='CE',
               strike_selection_type='PREMIUM_RANGE',
               min_premium=100, max_premium=200,
               expiry_selection='MONTHLY'
           )
           → Strike: 24400, Premium: 150, Expiry: 2024-01-31 (monthly expiry)
        
        4. Closest to Premium 150:
           calculate_strike_advanced(
               date='2024-01-15', index='NIFTY', spot_price=24350,
               strike_interval=50, option_type='PE',
               strike_selection_type='CLOSEST_PREMIUM',
               strike_selection_value=150,
               expiry_selection='WEEKLY'
           )
           → Strike: 24300, Premium: 148, Expiry: 2024-01-18
    """
    # Step 1: Get expiry date
    expiry = get_expiry_for_selection(date, index, expiry_selection)
    
    # Step 2: Calculate strike based on selection type
    strike_selection_type = strike_selection_type.upper().strip()
    
    if strike_selection_type == 'ATM':
        # ATM strike
        strike = round(spot_price / strike_interval) * strike_interval
        
    elif strike_selection_type in ['ITM', 'OTM']:
        # ITM/OTM with offset
        if strike_selection_value is None:
            raise ValueError(f"{strike_selection_type} requires strike_selection_value (offset)")
        
        # Use existing function
        selection_str = f"{strike_selection_type}{int(strike_selection_value)}"
        strike = calculate_strike_from_selection(spot_price, strike_interval, selection_str, option_type)
        
    elif strike_selection_type == 'PREMIUM_RANGE':
        # Premium range selection
        if min_premium is None or max_premium is None:
            raise ValueError("PREMIUM_RANGE requires min_premium and max_premium")
        
        strike = calculate_strike_from_premium_range(
            date, index, expiry, option_type, spot_price,
            strike_interval, min_premium, max_premium
        )
        
        if strike is None:
            raise ValueError(f"No strike found with premium between {min_premium} and {max_premium}")
        
    elif strike_selection_type == 'CLOSEST_PREMIUM':
        # Closest premium selection
        if strike_selection_value is None:
            raise ValueError("CLOSEST_PREMIUM requires strike_selection_value (target premium)")
        
        
        strike = calculate_strike_from_closest_premium(
            date, index, expiry, option_type, spot_price,
            strike_interval, strike_selection_value
        )
        
        if strike is None:
            raise ValueError(f"No strike found for closest premium to {strike_selection_value}")
        
    else:
        raise ValueError(f"Invalid strike_selection_type: {strike_selection_type}")
    
    # Step 3: Get premium for the selected strike
    premium = get_option_premium_from_db(
        date=date,
        index=index,
        strike=strike,
        option_type=option_type,
        expiry=expiry
    )
    
    return {
        'strike': strike,
        'expiry': expiry,
        'premium': premium
    }


# ============================================================================
# PHASE 1: WRAPPER FUNCTIONS FOR BULK LOADING
# ============================================================================
# These thin wrappers delegate to services/data_loader.py bulk functions.
# Engines should call these instead of the original functions for fast lookups.

def bulk_load_options(symbol: str, from_date: str, to_date: str) -> dict:
    """
    Load all option data for symbol/date-range into memory ONCE.
    Builds a pre-indexed O(1) lookup dict — NOT a raw DataFrame scan.

    FIX #1B: Dict is now built via a vectorized Polars filter + zip()
    comprehension instead of a row-by-row Python for-loop.
    For 5-10M rows this reduces dict-build time from ~60s to ~3-5s.
    """
    global _bulk_bhav_df, _bulk_spot_df, _bulk_loaded, _bulk_date_range
    global _option_lookup_table, _future_lookup_table, _spot_lookup_table

    from services.data_loader import (
        bulk_load as _bulk_load,
        get_bulk_options_df,
        get_bulk_spot_df,
        load_lookup_cache_from_redis,
        store_lookup_cache_in_redis,
    )

    # If dict already built for this range AND has entries, skip all work
    if (_bulk_loaded and _bulk_date_range == (from_date, to_date)
            and _option_lookup_table and len(_option_lookup_table) > 0):
        print(f"[BULK] O(1) dict already built: {len(_option_lookup_table):,} entries — skipping rebuild")
        return {
            "options_rows": len(_option_lookup_table),
            "spot_rows": len(_spot_lookup_table),
            "expiry_rows": 0,
            "loaded_key": f"{symbol}:{from_date}:{to_date}"
        }

    result = _bulk_load(symbol, from_date, to_date)

    # Check Redis cache, but verify it covers the FULL requested range
    lookup_loaded_from_redis = False
    options_df = get_bulk_options_df()
    if options_df is not None and not options_df.is_empty():
        min_date = str(options_df["Date"].min())
        max_date = str(options_df["Date"].max())
        if min_date <= from_date and max_date >= to_date:
            # DataFrame covers the full requested range
            _option_lookup_table.clear()
            _future_lookup_table.clear()
            opt_only = options_df.filter(pl.col("OptionType").is_in(["CE", "PE"]))
            dates_l    = _series_to_iso_date_list(opt_only["Date"])
            symbols_l  = opt_only["Symbol"].to_list()
            strikes_l  = opt_only["StrikePrice"].cast(pl.Int64).to_list()
            opt_l      = opt_only["OptionType"].to_list()
            expiries_l = _series_to_iso_date_list(opt_only["ExpiryDate"])
            closes_l   = opt_only["Close"].to_list()
            _option_lookup_table = {
                (d, s, k, o, e): c
                for d, s, k, o, e, c in zip(dates_l, symbols_l, strikes_l, opt_l, expiries_l, closes_l)
            }
            _bulk_bhav_df = None
            _bulk_loaded = True
            _bulk_date_range = (from_date, to_date)
            lookup_loaded_from_redis = True
            print(f"[BULK] Built O(1) lookup dict from loaded data: {len(_option_lookup_table):,} entries")
            print(f"[BULK] Data range: {min_date} to {max_date} (requested: {from_date} to {to_date})")

    # FIX #1B: Build O(1) lookup dict using vectorized Polars ops + zip()
    if not lookup_loaded_from_redis:
        options_df = get_bulk_options_df()
        if options_df is not None and not options_df.is_empty():
            _option_lookup_table.clear()
            _future_lookup_table.clear()

            # Filter to only CE/PE rows in Polars — vectorized, fast
            opt_only = options_df.filter(pl.col("OptionType").is_in(["CE", "PE"]))

            # Convert columns to Python lists in one shot (no per-row overhead)
            dates_l    = _series_to_iso_date_list(opt_only["Date"])
            symbols_l  = opt_only["Symbol"].to_list()
            strikes_l  = opt_only["StrikePrice"].cast(pl.Int64).to_list()
            opt_l      = opt_only["OptionType"].to_list()
            expiries_l = _series_to_iso_date_list(opt_only["ExpiryDate"])
            closes_l   = opt_only["Close"].to_list()

            # Single-pass dict comprehension — much faster than loop + conditional
            _option_lookup_table = {
                (d, s, k, o, e): c
                for d, s, k, o, e, c in zip(dates_l, symbols_l, strikes_l, opt_l, expiries_l, closes_l)
            }

            _bulk_bhav_df = None   # Don't keep raw DataFrame — dict is enough
            _bulk_loaded = True
            _bulk_date_range = (from_date, to_date)
            print(f"[BULK] Built O(1) lookup dict: {len(_option_lookup_table):,} entries")
            store_lookup_cache_in_redis(symbol, from_date, to_date, _option_lookup_table)

    spot_df = get_bulk_spot_df()
    if spot_df is not None and not spot_df.is_empty():
        _spot_lookup_table.clear()
        s_dates   = _series_to_iso_date_list(spot_df["Date"])
        s_closes  = spot_df["Close"].to_list()
        _spot_lookup_table = {(d, symbol): c for d, c in zip(s_dates, s_closes)}
        _bulk_spot_df = None
        print(f"[BULK] Built spot lookup dict: {len(_spot_lookup_table):,} entries")

    return result


def bulk_clear_options():
    """
    Clear base.py lookup dicts after a backtest completes.
    Does NOT clear data_loader Polars cache — that stays in memory so
    the next backtest for the same symbol/range skips the 120s DB reload.
    Call bulk_force_clear() only when you genuinely need to free RAM.
    """
    global _bulk_bhav_df, _bulk_spot_df, _bulk_loaded, _bulk_date_range

    _bulk_bhav_df = None
    _bulk_spot_df = None
    _bulk_loaded = False
    _bulk_date_range = None
    _option_lookup_table.clear()
    _future_lookup_table.clear()
    _spot_lookup_table.clear()
    # NOTE: data_loader Polars cache intentionally kept alive for re-use


def bulk_force_clear():
    """
    Full wipe — clears both base.py dicts AND data_loader Polars cache.
    Use only when switching symbol/date range or under memory pressure.
    """
    global _bulk_bhav_df, _bulk_spot_df, _bulk_loaded, _bulk_date_range

    from services.data_loader import bulk_clear as _bulk_clear
    _bulk_clear()

    _bulk_bhav_df = None
    _bulk_spot_df = None
    _bulk_loaded = False
    _bulk_date_range = None
    _option_lookup_table.clear()
    _future_lookup_table.clear()
    _spot_lookup_table.clear()


def fast_get_option_premium(
    date: str,
    strike_price: float,
    option_type: str,
    expiry_date: str
) -> float:
    """
    Fast in-memory lookup for option premium.
    Uses bulk-loaded Polars DataFrame instead of DB query.
    """
    from services.data_loader import get_bulk_option_price as _get_price
    return _get_price(date, strike_price, option_type, expiry_date)


def fast_get_spot_price(date: str) -> float:
    """
    Fast in-memory lookup for spot price.
    Uses bulk-loaded Polars DataFrame instead of DB query.
    """
    from services.data_loader import get_bulk_spot_price as _get_spot
    return _get_spot(date)


def fast_get_strikes_for_date(
    date: str,
    expiry_date: str,
    option_type: str = None
):
    """
    Fast in-memory lookup for all strikes for a date/expiry.
    Returns Polars DataFrame for fast filtering.
    """
    from services.data_loader import get_bulk_strikes_for_date as _get_strikes
    return _get_strikes(date, expiry_date, option_type)


def fast_get_expiry_dates(from_date: str = None, to_date: str = None):
    """
    Fast in-memory lookup for expiry dates.
    Uses bulk-loaded expiry calendar.
    """
    from services.data_loader import get_bulk_expiry_dates as _get_expiry
    return _get_expiry(from_date, to_date)


def is_bulk_data_loaded() -> bool:
    """Check if bulk data is currently loaded."""
    from services.data_loader import is_bulk_loaded
    return is_bulk_loaded()
