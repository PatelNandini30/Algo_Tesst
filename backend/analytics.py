import pandas as pd
import numpy as np
from typing import Tuple, Dict, Any

def create_summary_idx(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Create summary statistics for the backtest results
    """
    if df.empty:
        return {}

    # Ensure required columns exist
    required_cols = ['Net P&L', 'Entry Date', 'Exit Date']
    for col in required_cols:
        if col not in df.columns:
            # Try alternative names
            if col == 'Net P&L' and 'net_pnl' in df.columns:
                df = df.rename(columns={'net_pnl': 'Net P&L'})
            elif col == 'Entry Date' and 'entry_date' in df.columns:
                df = df.rename(columns={'entry_date': 'Entry Date'})
            elif col == 'Exit Date' and 'exit_date' in df.columns:
                df = df.rename(columns={'exit_date': 'Exit Date'})
    
    # Recalculate required columns if not present
    if 'Cumulative' not in df.columns:
        initial_capital = df.iloc[0]['Entry Spot'] if 'Entry Spot' in df.columns else df.iloc[0]['entry_spot']
        df['Cumulative'] = initial_capital + df['Net P&L'].cumsum()
    
    if 'Peak' not in df.columns:
        df['Peak'] = df['Cumulative'].cummax()
    
    if 'DD' not in df.columns:
        df['DD'] = np.where(df['Peak'] > df['Cumulative'], df['Cumulative'] - df['Peak'], 0)
    
    if '%DD' not in df.columns:
        df['%DD'] = np.where(df['DD'] == 0, 0, round(100 * (df['DD'] / df['Peak']), 2))
    
    # Calculate summary statistics
    total_pnl = df['Net P&L'].sum()
    count = len(df)
    
    # Wins and losses
    wins = df[df['Net P&L'] > 0]
    losses = df[df['Net P&L'] < 0]
    
    win_count = len(wins)
    loss_count = len(losses)
    
    # Calculate win percentage
    win_pct = round((win_count / count) * 100, 2) if count > 0 else 0
    
    # Calculate average win and loss
    avg_win = round(wins['Net P&L'].mean(), 2) if win_count > 0 else 0
    avg_loss = round(losses['Net P&L'].mean(), 2) if loss_count > 0 else 0
    
    # Calculate expectancy
    expectancy = 0
    if avg_loss != 0:
        expectancy = round(((avg_win/abs(avg_loss)) * win_pct - (100-win_pct)) / 100, 2)
    
    # Calculate CAGR
    initial_capital = df.iloc[0]['Entry Spot'] if 'Entry Spot' in df.columns else df.iloc[0]['entry_spot']
    start_date = df['Entry Date'].min() if 'Entry Date' in df.columns else df['entry_date'].min()
    end_date = df['Exit Date'].max() if 'Exit Date' in df.columns else df['exit_date'].max()
    n_years = (end_date - start_date).days / 365.25
    
    cagr_options = round(100 * (((total_pnl + initial_capital) / initial_capital) ** (1/n_years) - 1), 2) if n_years > 0 else 0
    
    # Calculate max drawdown
    max_dd_pct = df['%DD'].min()
    max_dd_pts = round(df['DD'].min(), 2)
    
    # Calculate CAR/MDD (Calmar ratio)
    car_mdd = round(cagr_options / abs(max_dd_pct), 2) if max_dd_pct != 0 else 0
    
    # Calculate recovery factor
    recovery_factor = round(total_pnl / abs(max_dd_pts), 2) if max_dd_pts != 0 else 0
    
    # Calculate ROI vs Spot
    total_spot_change = df['Spot P&L'].sum() if 'Spot P&L' in df.columns else 0
    roi_vs_spot = round((total_pnl / abs(total_spot_change)) * 100, 2) if total_spot_change != 0 else 0
    
    # Calculate CAGR for spot
    initial_spot = df.iloc[0]['Entry Spot'] if 'Entry Spot' in df.columns else df.iloc[0]['entry_spot']
    final_spot = df.iloc[-1]['Exit Spot'] if 'Exit Spot' in df.columns else df.iloc[-1]['exit_spot']
    cagr_spot = round(100 * (((final_spot - initial_spot) / initial_spot) / n_years), 2) if n_years > 0 else 0
    
    summary = {
        "Count": count,
        "Sum": round(total_pnl, 2),
        "Avg": round(total_pnl / count, 2) if count > 0 else 0,
        "W%": win_pct,
        "Avg(W)": avg_win,
        "L%": round(100 - win_pct, 2),
        "Avg(L)": avg_loss,
        "Expectancy": expectancy,
        "CAGR(Options)": cagr_options,
        "DD": max_dd_pct,
        "Spot Change": round(total_spot_change, 2),
        "ROI vs Spot": roi_vs_spot,
        "CAGR(Spot)": cagr_spot,
        "DD(Points)": max_dd_pts,
        "CAR/MDD": car_mdd,
        "Recovery Factor": recovery_factor
    }
    
    return summary


def getPivotTable(df: pd.DataFrame, expiry_col: str) -> pd.DataFrame:
    """
    Create a pivot table showing monthly P&L by year
    """
    if df.empty:
        return pd.DataFrame()

    df_copy = df.copy()
    
    # Convert expiry column to datetime if it's not already
    if not pd.api.types.is_datetime64_any_dtype(df_copy[expiry_col]):
        df_copy[expiry_col] = pd.to_datetime(df_copy[expiry_col])
    
    # Extract month and year
    df_copy['Month'] = df_copy[expiry_col].dt.strftime('%b')
    df_copy['Year'] = df_copy[expiry_col].dt.year
    
    # Create pivot table
    pivot = df_copy.pivot_table(
        values='Net P&L', 
        index='Year', 
        columns='Month', 
        aggfunc='sum',
        fill_value=None
    )
    
    # Ensure months are in calendar order
    month_order = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    
    # Reorder columns according to month_order
    available_months = [m for m in month_order if m in pivot.columns]
    pivot = pivot[available_months]
    
    # Add grand total column
    pivot['Grand Total'] = pivot[available_months].sum(axis=1).round(2)
    
    return pivot