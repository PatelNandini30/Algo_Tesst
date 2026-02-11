import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

def synthetic_data():
    df = pd.read_excel("./Current_CE_Sell_Next_PE_ATM_Buy_FUT_Buy_RiseOrFallBy150Points_Dates.xlsx")
    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d')
    df['Expiry'] = pd.to_datetime(df['Expiry'], format='%Y-%m-%d')
    data_df = getStrikeData("NIFTY")
    data_list = []

    for i in range(0, len(df)):
        row = df.iloc[i]
        entryDate, curr_expiry = row['Entry Date'], row['Expiry']
        fileName1 = entryDate.strftime("%Y-%m-%d") + ".csv"       
        
        entrySpot = data_df[(data_df['Date']==entryDate)]
        if entrySpot.empty:
            reason = f"Spot Missing for {entryDate}"
            createLogFile("NIFTY", reason, pd.NAT, pd.NaT, curr_expiry, entryDate, pd.NaT)
            continue
        
        try:
            bhav_df1 = pd.read_csv(f"./cleaned_csvs/{fileName1}")
        except:
            reason = f"{fileName1} not found in cleaned_csvs. Skipping the Trade"
            createLogFile("NIFTY", reason, pd.NAT, pd.NaT, curr_expiry, entryDate, pd.NaT)
            continue

        bhav_df1['Date'] = pd.to_datetime(bhav_df1['Date'], format='%Y-%m-%d')
        bhav_df1['ExpiryDate'] = pd.to_datetime(bhav_df1['ExpiryDate'], format='%Y-%m-%d')
        entrySpot = entrySpot.iloc[0]['Close']
        atm_strike = round(entrySpot/50)*50
        
        call_entry_data = bhav_df1[
                                (bhav_df1['Instrument']=="OPTIDX")
                                & (bhav_df1['Symbol']=="NIFTY")
                                & (bhav_df1['OptionType']=="CE")
                                & (
                                    (bhav_df1['ExpiryDate']==curr_expiry)
                                    | (bhav_df1['ExpiryDate']==curr_expiry-timedelta(days=1))
                                    |  (bhav_df1['ExpiryDate']==curr_expiry+timedelta(days=1))
                                )
                                & (bhav_df1['StrikePrice']==atm_strike)
                            ].reset_index(drop=True).copy()
        put_entry_data = bhav_df1[
                                (bhav_df1['Instrument']=="OPTIDX")
                                & (bhav_df1['Symbol']=="NIFTY")
                                & (bhav_df1['OptionType']=="PE")
                                & (
                                    (bhav_df1['ExpiryDate']==curr_expiry)
                                    | (bhav_df1['ExpiryDate']==curr_expiry-timedelta(days=1))
                                    |  (bhav_df1['ExpiryDate']==curr_expiry+timedelta(days=1))
                                )
                                & (bhav_df1['StrikePrice']==atm_strike)
                            ].reset_index(drop=True).copy()
        
        if(call_entry_data.empty or put_entry_data.empty):
            reason = f"Call Data Missing for Strike:{atm_strike}" if call_entry_data.empty \
                    else f"Put Data Missing for Strike:{atm_strike}"
            createLogFile("NIFTY", reason, pd.NAT, pd.NaT, curr_expiry, entryDate, pd.NaT)
            continue

        call_premium = call_entry_data.iloc[0]['Close']
        put_premium = put_entry_data.iloc[0]['Close']
        
        data_list.append({
            'Entry Date' : entryDate,
            'Entry Spot' : entrySpot,
            'ATM Strike' : atm_strike,
            'Call Premium' : call_premium,
            'Put Premium' : put_premium,
            'Synthetic Value' : (call_premium-put_premium)+atm_strike,
            'Synthetic Point' : round((call_premium-put_premium)+atm_strike-entrySpot, 2),
        })
    
    if data_list:
        data_df = pd.DataFrame(data_list)
        data_df.to_csv("./Current_CE_Sell_Next_PE_ATM_Buy_FUT_Buy_RiseOrFallBy150Points_Synthetic_Data.csv", index=False)

    if logFile:
        log_df = pd.DataFrame(logFile)
        log_df.to_csv("./Current_CE_Sell_Next_PE_ATM_Buy_FUT_Buy_RiseOrFallBy150Points_Synthetic_Data_Log.csv", index=False)
        logFile.clear()
