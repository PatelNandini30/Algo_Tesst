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


def round_to_50(value: float) -> int:
    """Round to nearest 50"""
    return round(value / 50) * 50

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
    # Use Entry Spot as initial capital (matching analyse_bhavcopy logic)
    # Cumulative = Capital + running P&L
    if 'Entry Spot' in df.columns and not df.empty:
        initial_capital = float(df.iloc[0]['Entry Spot'])
    else:
        # Fallback: use absolute value of worst loss as proxy for capital
        if len(df[df[pnl_col] < 0]) > 0:
            initial_capital = abs(df[df[pnl_col] < 0][pnl_col].min())
        else:
            initial_capital = 100000.0  # Default capital
    
    df['Cumulative'] = initial_capital + df[pnl_col].cumsum()

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
    # AlgoTest computes CAGR purely on strategy P&L.
    # Formula:  CAGR = ((final_equity / initial_equity) ^ (1/n_years) - 1) * 100
    # where initial_equity = 1 (normalised), final_equity = 1 + total_pnl/base
    #
    # "base" = absolute value of the largest single trade loss (proxy for margin).
    # If no losses exist, use the largest single trade gain.
    # This matches AlgoTest's effective capital assumption.
    start_date = pd.to_datetime(df[entry_date_col].min())
    end_date   = pd.to_datetime(df[exit_date_col].max())
    n_years    = max((end_date - start_date).days / 365.25, 0.01)

    # Derive a sensible capital base
    if loss_count > 0:
        capital_base = abs(losses[pnl_col].min())          # worst single loss
    else:
        capital_base = abs(wins[pnl_col].max())

    capital_base = max(capital_base, 1.0)                  # safety floor

    end_value = capital_base + total_pnl
    if end_value > 0:
        cagr = round(100.0 * ((end_value / capital_base) ** (1.0 / n_years) - 1), 2)
    else:
        cagr = round(-100.0, 2)                            # total wipeout

    # ── DRAWDOWN SUMMARY ─────────────────────────────────────────────────────
    max_dd_pct = float(df['%DD'].min())                    # most negative %DD
    max_dd_pts = round(float(df['DD'].min()), 2)           # deepest rupee DD

    # Duration of overall max drawdown (calendar days from peak to trough)
    mdd_duration   = 0
    mdd_start_date = None
    mdd_end_date   = None

    if max_dd_pts < 0:
        trough_idx  = df['DD'].idxmin()
        trough_date = pd.to_datetime(df.loc[trough_idx, exit_date_col])

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

    # CAGR(Spot) - if just held the index
    if spot_chg != 0 and initial_capital > 0:
        end_value_spot = initial_capital + spot_chg
        if end_value_spot > 0:
            cagr_spot = round(100 * ((end_value_spot / initial_capital) ** (1.0 / n_years) - 1), 2)
        else:
            cagr_spot = round(-100.0, 2)
    else:
        cagr_spot = 0

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
        "cagr_options":          cagr,
        "max_dd_pct":            max_dd_pct,
        "max_dd_pts":            max_dd_pts,
        "mdd_duration_days":     mdd_duration,
        "mdd_start_date":        mdd_start_date,
        "mdd_end_date":          mdd_end_date,
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
    
    # Get all trading days before expiry
    trading_days = trading_calendar_df[
        trading_calendar_df['date'] < expiry_date
    ].sort_values('date', ascending=False)
    
    if days_before == 0:
        # Entry on expiry day itself
        return expiry_date
    
    # Validate enough trading days exist
    if len(trading_days) < days_before:
        raise ValueError(f"Not enough trading days before expiry {expiry_date}. Requested: {days_before}, Available: {len(trading_days)}")
    
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


def get_option_premium_from_db(date, index, strike, option_type, expiry, db_path='bhavcopy_data.db'):
    """
    Get option premium from CSV only (no database fallback)
    """
    # DEBUG: Disabled for performance
    # print(f"      DEBUG: get_option_premium_from_db called with date={date}, index={index}, strike={strike}, option_type={option_type}, expiry={expiry}")
    try:
        # Get from bhavcopy CSV
        date_str = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)
        
        bhav_df = load_bhavcopy(date_str)
        
        if bhav_df is not None and not bhav_df.empty:
            date_ts = pd.to_datetime(date)
            expiry_ts = pd.to_datetime(expiry)
            
            # Normalize option_type (handle both string and enum)
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
            
            # Debug: Disabled for performance
            # print(f"      DEBUG: Looking for Symbol={index}, OptionType={opt_match}, Strike={strike}, Expiry={expiry_ts}")
            
            # Filter by Symbol first to see what symbols exist
            symbol_matches = bhav_df[bhav_df['Symbol'] == index]
            if symbol_matches.empty:
                # print(f"      DEBUG: No matches for Symbol={index}. Available symbols: {bhav_df['Symbol'].unique()[:10]}")
                return None
            
            # print(f"      DEBUG: Found {len(symbol_matches)} rows for Symbol={index}")
            
            mask = (
                (bhav_df['Symbol'] == index) &
                (bhav_df['OptionType'].str.upper() == opt_match) &
                (abs(bhav_df['StrikePrice'] - strike) <= 1) &
                (abs(bhav_df['ExpiryDate'] - expiry_ts) <= pd.Timedelta(days=1))
            )
            
            matches = bhav_df[mask]
            if not matches.empty:
                close_price = float(matches.iloc[0]['Close'])
                # print(f"      DEBUG: SUCCESS - Found match! Close price: {close_price}")
                return close_price
            else:
                # More debug info - Disabled for performance
                # print(f"      DEBUG: FAILED - No match found.")
                # print(f"      DEBUG: Expiry dates in data for {index}: {symbol_matches['ExpiryDate'].unique()[:5]}")
                # print(f"      DEBUG: Strike prices in data for {index}: {sorted(symbol_matches['StrikePrice'].unique())[:10]}")
                # print(f"      DEBUG: Option types in data: {symbol_matches['OptionType'].unique()}")
                pass
        
        return None
    except Exception as e:
        print(f"Warning: Could not get option premium: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_future_price_from_db(date, index, expiry, db_path='bhavcopy_data.db'):
    """
    Get future price from CSV only (no database fallback)
    """
    try:
        # Get from bhavcopy CSV
        date_str = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)
        
        bhav_df = load_bhavcopy(date_str)
        
        if bhav_df is not None and not bhav_df.empty:
            date_ts = pd.to_datetime(date)
            expiry_ts = pd.to_datetime(expiry)
            
            # Look for futures - usually marked as 'FUT' in Instrument
            mask = (
                (bhav_df['Symbol'] == index) &
                (bhav_df['Instrument'].str.upper().str.contains('FUT', na=False)) &
                (abs(bhav_df['ExpiryDate'] - expiry_ts) <= pd.Timedelta(days=1))
            )
            
            matches = bhav_df[mask]
            if not matches.empty:
                return float(matches.iloc[0]['Close'])
        
        return None
    except Exception as e:
        # Warning print commented out for performance
        # print(f"Warning: Could not get future price: {e}")
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
    Get spot price - tries CSV first, then database fallback
    """
    try:
        # PRIMARY: Get from strike_data CSV
        date_str = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)
        
        spot_df = get_strike_data(index, date_str, date_str)
        
        if spot_df is not None and not spot_df.empty:
            date_ts = pd.to_datetime(date)
            exact = spot_df[spot_df['Date'] == date_ts]
            if not exact.empty:
                return float(exact.iloc[0]['Close'])
            # Get closest prior
            prior = spot_df[spot_df['Date'] <= date_ts]
            if not prior.empty:
                return float(prior.iloc[-1]['Close'])
        
        return None
    except Exception as e:
        # Fallback to database
        pass
    
    # Database fallback
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    query = """SELECT close FROM bhavcopy WHERE date = ? AND symbol = ? AND strike IS NULL AND option_type IS NULL LIMIT 1"""
    cursor.execute(query, (date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else date, index))
    result = cursor.fetchone()
    conn.close()
    return float(result[0]) if result else None


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
        3. Select the strike closest to ATM from filtered list
        4. This ensures we get liquid strikes with desired premium range
        
    Example:
        Spot = 24,350, Min = 100, Max = 200
        Available: 24300 (₹250), 24350 (₹180), 24400 (₹120), 24450 (₹80)
        In Range: 24350 (₹180), 24400 (₹120)
        Closest to ATM: 24350 ✅
    """
    # Get all strikes with premiums
    strikes_data = get_all_strikes_with_premiums(
        date, index, expiry, option_type, spot_price, strike_interval
    )
    
    if not strikes_data:
        return None
    
    # Filter by premium range
    in_range = [s for s in strikes_data if min_premium <= s['premium'] <= max_premium]
    
    if not in_range:
        return None
    
    # Calculate ATM for reference
    atm_strike = round(spot_price / strike_interval) * strike_interval
    
    # Find strike closest to ATM
    closest = min(in_range, key=lambda x: abs(x['strike'] - atm_strike))
    
    return closest['strike']


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
        return None
    
    # Find strike with premium closest to target
    closest = min(strikes_data, key=lambda x: abs(x['premium'] - target_premium))
    
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
