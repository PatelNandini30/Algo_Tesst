import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

def save_hypothetical_and_summary_idx_V4(df, filename="./df_final.xlsx"):
    stats_df, total_df = create_summary_idx_V4(df)
    pivot_table, header = getPivotTable_V4(df)
    
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df['Entry Date'] = df['Entry Date'].dt.date
        df['Exit Date'] = df['Exit Date'].dt.date
        df['Put Expiry'] = df['Put Expiry'].dt.date
        df['Call Expiry'] = df['Call Expiry'].dt.date
        df.to_excel(writer, sheet_name="Hypothetical TradeSheet", index=False)

        start_row = 0
        for table in [stats_df, total_df]:
            table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row)
            start_row += len(table) + 2  
        start_row = start_row + 1

        header_df = pd.DataFrame([header])
        header_df.to_excel(writer, sheet_name="Summary", index=False, header=False, startrow=start_row)
        pivot_table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row+2)
        start_row += len(header_df) + len(pivot_table) + 3
