import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

def getPivotTable_alt(df):
    filtered_df = df[['Future Expiry', 'Net P&L']].copy(deep=True)
    header = ["Sum of Net P&L", "Total Points"]

    if filtered_df.empty:
        return pd.DataFrame(), [], pd.DataFrame(), []
    
    filtered_df['Month'] = pd.to_datetime(filtered_df['Future Expiry'], format='%Y-%m-%d').dt.strftime("%b")
    filtered_df['Year'] = pd.to_datetime(filtered_df['Future Expiry'], format='%Y-%m-%d').dt.year
    
    pivot_table = filtered_df.pivot_table(
        values = filtered_df.columns[1],  
        index = 'Year',  
        columns = 'Month', 
        aggfunc = 'sum'
    )
    month_order = [m for m in ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'] if m in pivot_table.columns]
    pivot_table = pivot_table[month_order]
    grand_total = ['Grand Total'] + [pivot_table[col].sum().round(2) for col in month_order]
    grand_total_df = pd.DataFrame([grand_total], columns=['Year'] + month_order)
    pivot_table = pd.concat([pivot_table, grand_total_df.set_index('Year')])
    pivot_table['Grand Total'] = pivot_table[month_order].sum(axis=1).round(2)
    pivot_table.reset_index(inplace=True)

    return pivot_table, header
