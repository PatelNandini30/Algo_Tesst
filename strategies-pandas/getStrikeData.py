import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

def getStrikeData(symbol):
    if symbol in ['NIFTY']:
        fileName = "Nifty_strike_data.csv"
    elif symbol in ['BANKNIFTY', 'MIDCPNIFTY']:
        fileName = "Index_strike_data.csv"
    else:
        fileName = "Nifty 50_strike_data.csv"
        
    try:
        df = pd.read_csv(f"./strikeData/{fileName}") 
    except:
        print(f"{fileName} not found in strikeData folder")
        return pd.DataFrame()
    
    for col in ['Ticker', 'Date', 'Close']:
        if col not in df.columns:
            print(f"Column:{col} missing in {fileName}")
            return pd.DataFrame()


    format_list = ["%Y-%m-%d", "%d-%m-%Y", "%y-%m-%d", "%d-%m-%y", "%d-%b-%Y", "%d-%b-%y"]
    for format_type in format_list:
        try:
            df['Date'] = pd.to_datetime(df['Date'], format=format_type, errors="raise")
            break
        except:
            continue

    if not pd.api.types.is_datetime64_any_dtype(df['Date']):
        print("Could not convert Date column into Datetime Format.")
        return pd.DataFrame()
    
    df  =   df[(df['Ticker']==symbol)]\
            .drop_duplicates(subset=['Date', 'Ticker'], keep='last')\
            .sort_values(by='Date')\
            .reset_index(drop=True)
         
    return df
