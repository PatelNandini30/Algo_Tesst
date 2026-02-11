import pandas as pd
import numpy as np
from functools import lru_cache
from datetime import datetime, timedelta
import math
import os
from typing import Tuple, Optional, Dict, Any

# Constants for data directories
# Hardcoded absolute paths for Windows environment
PROJECT_ROOT = r'E:\Algo_Test_Software'
CLEANED_CSV_DIR = os.path.join(PROJECT_ROOT, 'cleaned_csvs')
EXPIRY_DATA_DIR = os.path.join(PROJECT_ROOT, 'expiryData')
STRIKE_DATA_DIR = os.path.join(PROJECT_ROOT, 'strikeData')
FILTER_DIR = os.path.join(PROJECT_ROOT, 'Filter')

def round_half_up(x: float) -> int:
    """Round half values up (e.g., 0.5 -> 1, 1.5 -> 2)"""
    return int(math.floor(x + 0.5))

def get_strike_data(symbol: str, from_date: str, to_date: str) -> pd.DataFrame:
    """
    Read ./strikeData/Nifty_strike_data.csv
    Filter by symbol, parse Date, filter to date range
    Return DataFrame: Date, Close
    """
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
    
    # Filter by symbol (ticker)
    df = df[df['Ticker'] == symbol]
    
    return df[['Date', 'Close']].reset_index(drop=True)

def load_expiry(index: str, expiry_type: str) -> pd.DataFrame:
    """
    Read ./expiryData/{index}.csv (weekly) or ./expiryData/{index}_Monthly.csv
    Parse Previous Expiry, Current Expiry, Next Expiry
    Return sorted DataFrame
    """
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

def load_base2() -> pd.DataFrame:
    """
    Read ./Filter/base2.csv, parse Start and End
    Return sorted DataFrame
    """
    file_path = os.path.join(FILTER_DIR, "base2.csv")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Base2 file not found: {file_path}")
    
    df = pd.read_csv(file_path)
    
    # Handle multiple date formats for base2 dates
    date_columns = ['Start', 'End']
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
    
    return df.sort_values('Start').reset_index(drop=True)

@lru_cache(maxsize=500)
def load_bhavcopy(date_str: str) -> pd.DataFrame:
    """
    Read ./cleaned_csvs/{date_str}.csv with LRU cache (500 files)
    Parse Date and ExpiryDate columns
    Return DataFrame: Instrument, Symbol, ExpiryDate, OptionType, StrikePrice, Close, TurnOver
    """
    file_path = os.path.join(CLEANED_CSV_DIR, f"{date_str}.csv")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Bhavcopy file not found: {file_path}")
    
    df = pd.read_csv(file_path)
    df['Date'] = pd.to_datetime(df['Date'])
    df['ExpiryDate'] = pd.to_datetime(df['ExpiryDate'])
    
    # Return only the required columns
    required_cols = ['Instrument', 'Symbol', 'ExpiryDate', 'OptionType', 'StrikePrice', 'Close', 'TurnOver', 'Date']
    available_cols = [col for col in required_cols if col in df.columns]
    
    return df[available_cols]

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
    Translation of create_summary_idx()
    """
    if df.empty:
        return df, {}
    
    # Normalize column names to handle different naming conventions
    df = df.copy()
    if 'net_pnl' in df.columns and 'Net P&L' not in df.columns:
        df = df.rename(columns={'net_pnl': 'Net P&L'})
    if 'entry_date' in df.columns and 'Entry Date' not in df.columns:
        df = df.rename(columns={'entry_date': 'Entry Date'})
    if 'exit_date' in df.columns and 'Exit Date' not in df.columns:
        df = df.rename(columns={'exit_date': 'Exit Date'})
    if 'entry_spot' in df.columns and 'Entry Spot' not in df.columns:
        df = df.rename(columns={'entry_spot': 'Entry Spot'})
    if 'exit_spot' in df.columns and 'Exit Spot' not in df.columns:
        df = df.rename(columns={'exit_spot': 'Exit Spot'})
    if 'spot_pnl' in df.columns and 'Spot P&L' not in df.columns:
        df = df.rename(columns={'spot_pnl': 'Spot P&L'})
    
    # Add Cumulative, Peak, DD, %DD columns
    df = df.copy()
    initial_spot = df.iloc[0]['Entry Spot'] if 'Entry Spot' in df.columns else df.iloc[0]['entry_spot']
    df['Cumulative'] = initial_spot + df['Net P&L'].cumsum()
    
    df['Peak'] = df['Cumulative'].cummax()
    df['DD'] = np.where(df['Peak'] > df['Cumulative'], df['Cumulative'] - df['Peak'], 0)
    df['%DD'] = np.where(df['DD'] == 0, 0, round(100 * (df['DD'] / df['Peak']), 2))
    
    # Extract column names based on available columns
    entry_spot_col = 'Entry Spot' if 'Entry Spot' in df.columns else 'entry_spot'
    net_pnl_col = 'Net P&L' if 'Net P&L' in df.columns else 'net_pnl'
    entry_date_col = 'Entry Date' if 'Entry Date' in df.columns else 'entry_date'
    exit_date_col = 'Exit Date' if 'Exit Date' in df.columns else 'exit_date'
    
    entry_spot = df.iloc[0][entry_spot_col]
    total_pnl = df[net_pnl_col].sum()
    count = len(df)
    
    wins = df[df[net_pnl_col] > 0]
    losses = df[df[net_pnl_col] < 0]
    win_count = len(wins)
    loss_count = len(losses)
    
    win_pct = round(win_count / count * 100, 2) if count > 0 else 0
    avg_win = round(wins[net_pnl_col].mean(), 2) if win_count > 0 else 0
    avg_loss = round(losses[net_pnl_col].mean(), 2) if loss_count > 0 else 0
    
    # Calculate years for CAGR
    start_date = df[entry_date_col].min()
    end_date = df[exit_date_col].max()
    n_years = (end_date - start_date).days / 365.25
    
    cagr = round(100 * (((total_pnl + entry_spot) / entry_spot) ** (1/n_years) - 1), 2) if n_years > 0 else 0
    max_dd_pct = df['%DD'].min()
    max_dd_pts = round(df['DD'].min(), 2)
    
    expectancy = 0
    if avg_loss != 0:
        expectancy = round(((avg_win/abs(avg_loss)) * win_pct - (100-win_pct)) / 100, 2)
    
    car_mdd = round(cagr / abs(max_dd_pct), 2) if max_dd_pct != 0 else 0
    recovery_factor = round(total_pnl / abs(max_dd_pts), 2) if max_dd_pts != 0 else 0
    
    # Calculate additional metrics
    cagr_spot = round(100 * (((entry_spot + df["Spot P&L"].sum()) / entry_spot) ** (1/n_years) - 1), 2) if n_years > 0 else 0
    spot_pnl_total = df["Spot P&L"].sum()
    roi_vs_spot = round((spot_pnl_total / entry_spot) * 100, 2) if entry_spot > 0 else 0
    
    summary = {
        "total_pnl": round(total_pnl, 2),
        "count": count,
        "win_pct": win_pct,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "cagr_options": cagr,
        "cagr_spot": cagr_spot,
        "max_dd_pct": max_dd_pct,
        "max_dd_pts": max_dd_pts,
        "car_mdd": car_mdd,
        "recovery_factor": recovery_factor,
        "roi_vs_spot": roi_vs_spot
    }
    
    return df, summary

def build_pivot(df: pd.DataFrame, expiry_col: str) -> Dict[str, Any]:
    """
    Translation of getPivotTable()
    """
    if df.empty:
        return {"headers": [], "rows": []}
    
    # Normalize column names
    df = df.copy()
    if 'net_pnl' in df.columns and 'Net P&L' not in df.columns:
        df = df.rename(columns={'net_pnl': 'Net P&L'})
    
    df['Month'] = pd.to_datetime(df[expiry_col]).dt.strftime('%b')
    df['Year'] = pd.to_datetime(df[expiry_col]).dt.year
    
    pivot = df.pivot_table(values='Net P&L', index='Year', columns='Month', aggfunc='sum')
    month_order = [m for m in ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'] if m in pivot.columns]
    pivot = pivot[month_order]
    pivot['Grand Total'] = pivot[month_order].sum(axis=1).round(2)
    
    headers = ['Year'] + month_order + ['Grand Total']
    rows = [[str(year)] + [round(pivot.loc[year, m], 2) if m in pivot.columns and not pd.isna(pivot.loc[year, m]) else None for m in month_order + ['Grand Total']]
            for year in pivot.index]
    return {"headers": headers, "rows": rows}