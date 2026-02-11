import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

def create_summary_idx_V5_Call_protection(df):
    entrySpot = df.iloc[0]['Entry Spot']
    first_entry_date = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d').min()
    last_exit_date = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d').max()
    number_of_years = (last_exit_date - first_entry_date).days / 365.25
    groups = {
        "Total Trades"  :   df,
    }

    stats_rows = []
    for label, subset in groups.items():
        count = len(subset)  
        total_sum = subset['Net P&L'].sum() if count>0 else None
        avg = (total_sum / count).round(2) if count > 0 else None
        
        win = len(subset[subset['Net P&L']>0]) if count>0 else None
        winPct = round((win/count * 100),2) if not pd.isna(win) else None
        avg_win = subset[subset['Net P&L']>0]['Net P&L'].mean() if not pd.isna(win) else None
        avg_win_pct = round(100*(avg_win/total_sum),2) if not pd.isna(win) else None
        avg_win = round(avg_win, 2) if not pd.isna(avg_win) else None
        
        lose = len(subset[subset['Net P&L']<0]) if count>0 else None
        losePct = round((lose/count * 100),2) if not pd.isna(lose) else None
        avg_loss = subset[subset['Net P&L']<0]['Net P&L'].mean() if not pd.isna(lose) else None
        avg_loss_pct = round(100*(avg_loss/total_sum),2) if  not pd.isna(lose) else None
        avg_loss = round(avg_loss, 2) if not pd.isna(avg_loss) else None

        expectancy = round(( ((avg_win_pct / abs(avg_loss_pct) ) * winPct) - losePct)/100, 2) if not pd.isna(win) and not pd.isna(lose) else None

        if count>0 and ((total_sum + entrySpot)/entrySpot) > 0:
            cagr_options = round(
                100 * (((total_sum + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (total_sum + entrySpot) > 0 else 0
        else:
            cagr_options = 0

        dd = subset['%DD'].min().round(2) if count>0 else None
        dd_points = subset['DD'].min().round(2) if count>0 else None
        Car_MDD = round(cagr_options/abs(dd), 2)
        recovery_factor = round(total_sum/abs(dd_points), 2)

        spot_chg = subset['Spot P&L'].sum()
        roi_vs_spot = round(100*(total_sum/spot_chg), 2) if spot_chg!=0 else None
        
        if count>0 and ((spot_chg + entrySpot) / entrySpot)>0:
            cagr_spot = round(
                100 * (((spot_chg + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (spot_chg + entrySpot) > 0 else 0

        else:
            cagr_spot = 0

        stats_rows.append([
                        label, count, total_sum, avg, 
                        winPct, avg_win, losePct, avg_loss, 
                        expectancy, cagr_options, 
                        dd, spot_chg, roi_vs_spot, 
                        cagr_spot, dd_points, Car_MDD,
                        recovery_factor
        ])
        
    stats_df = pd.DataFrame(stats_rows, columns=[
                                    "Category", "Count", "Sum", "Avg", 
                                    "W%", "Avg(W)", "L%", "Avg(L)",
                                    "Expectancy", "CAGR(Options)",
                                    "DD", "Spot Change", "ROI vs Spot",
                                    "CAGR(Spot)", "DD(Points)", "CAR/MDD",
                                    "Recovery Factor"

                                ])

    
    total_df = pd.DataFrame([
        ["Spot P&L", df["Spot P&L"].sum().round(2)],
        ["CE P&L", df["Call P&L"].sum().round(2)],
        ["Protective CE P&L", df["Protective Call P&L"].sum().round(2)],
        ["CE+Protective CE P&L", df["Net P&L"].sum().round(2)],
        ["CE+Protective CE+Spot P&L", (df["Net P&L"].sum() + df["Spot P&L"].sum()).round(2)],

    ], columns=["Type", "Sum"])

    return stats_df, total_df
