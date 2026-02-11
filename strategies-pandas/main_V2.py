import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

def main_V2(callPosition=0, adjustment_type=0, adjustment_points=150, strike_jump=50):
    data_df = getStrikeData("NIFTY")

    weekly_expiry_df = pd.read_csv(f"./expiryData/NIFTY.csv")
    weekly_expiry_df['Previous Expiry'] = pd.to_datetime(weekly_expiry_df['Previous Expiry'], format='%Y-%m-%d')
    weekly_expiry_df['Current Expiry'] = pd.to_datetime(weekly_expiry_df['Current Expiry'], format='%Y-%m-%d')
    weekly_expiry_df['Next Expiry'] = pd.to_datetime(weekly_expiry_df['Next Expiry'], format='%Y-%m-%d')
    
    monthly_expiry_df = pd.read_csv(f"./expiryData/NIFTY_Monthly.csv")
    monthly_expiry_df['Previous Expiry'] = pd.to_datetime(monthly_expiry_df['Previous Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Current Expiry'] = pd.to_datetime(monthly_expiry_df['Current Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Next Expiry'] = pd.to_datetime(monthly_expiry_df['Next Expiry'], format='%Y-%m-%d')
    
    analysis_data = []
    for w in range(0, len(weekly_expiry_df)):
        prev_expiry = weekly_expiry_df.iloc[w]['Previous Expiry']
        curr_expiry = weekly_expiry_df.iloc[w]['Current Expiry']
        next_expiry = weekly_expiry_df.iloc[w]['Next Expiry']
        
        filtered_data = data_df[
                            (data_df['Date']>=prev_expiry)
                            & (data_df['Date']<=curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)

        if len(filtered_data)<2:
            continue

        curr_monthly_expiry = monthly_expiry_df[
                                (monthly_expiry_df['Current Expiry']>=curr_expiry)
                                ].sort_values(by='Current Expiry').reset_index(drop=True)
        
        if(curr_monthly_expiry.empty):
            continue
        else:
            curr_monthly_expiry = curr_monthly_expiry.iloc[0]['Current Expiry']
        
        fut_expiry = curr_monthly_expiry        
        put_expiry = next_expiry
        
        if curr_expiry==fut_expiry:
            temp_fut_expiry = monthly_expiry_df[
                                (monthly_expiry_df['Current Expiry']>curr_monthly_expiry)
                                ].sort_values(by='Current Expiry').reset_index(drop=True).copy()
            fut_expiry = temp_fut_expiry.iloc[0]['Current Expiry']

    
        
        intervals, interval_df = [], pd.DataFrame()
        
        if adjustment_type!=0:
            filtered_data1 = filtered_data.copy(deep=True)
            filtered_data1['ReEntry'] = False 
            filtered_data1['Entry_Price'] = None
            filtered_data1['Pct_Chg'] = None
            entryPrice = None
            
            for t in range(0, len(filtered_data1)):
                if t==0:
                    entryPrice = filtered_data1.iloc[t]['Close']
                    filtered_data1.at[t, 'Entry_Price'] = entryPrice
                else:
                    if not pd.isna(entryPrice):
                        roc_point = filtered_data1.iloc[t]['Close'] - entryPrice 
                        filtered_data1.at[t, 'Entry_Price'] = entryPrice
                        filtered_data1.at[t, 'Points_Chg'] = round(roc_point, 2)
                    
                    if(
                        ((adjustment_type==1) and (roc_point>=adjustment_points))
                        or ((adjustment_type==2) and (roc_point<=(-adjustment_points)))
                        or ((adjustment_type==3) and (abs(roc_point)>=adjustment_points))
                    ):
                        filtered_data1.at[t, 'ReEntry'] = True
                        entryPrice = filtered_data1.iloc[t]['Close']
        
            filtered_data1 = filtered_data1[filtered_data1['ReEntry']==True]
            if len(filtered_data1) > 0:
                start = filtered_data.iloc[0]['Date']
                for d in filtered_data1['Date']:
                    intervals.append((start, d))
                    start = d   

                if start != filtered_data.iloc[-1]['Date']:
                    intervals.append((start, filtered_data.iloc[-1]['Date']))
            else:
                intervals.append((filtered_data.iloc[0]['Date'], filtered_data.iloc[-1]['Date']))
        else:
            intervals.append((filtered_data.iloc[0]['Date'], filtered_data.iloc[-1]['Date']))

        
        if intervals:
            interval_df = pd.DataFrame(intervals, columns=['From', 'To'])
    

        for i in range(0, len(interval_df)):
            fileName1 = fileName2 = ""
            fromDate = interval_df.iloc[i]['From']
            toDate = interval_df.iloc[i]['To']
            print(f"From:{fromDate.strftime('%d-%m-%Y')} To:{toDate.strftime('%d-%m-%Y')}")

            entrySpot = filtered_data[filtered_data['Date']==fromDate].iloc[0]['Close']
            exitSpot =  filtered_data[filtered_data['Date']==toDate].iloc[0]['Close']
            
            put_strike = round(entrySpot/strike_jump)*strike_jump
            call_strike = put_strike + (100*callPosition)
            fileName1 = fromDate.strftime("%Y-%m-%d") + ".csv"
            fileName2 = toDate.strftime("%Y-%m-%d") + ".csv"
                
            bhav_df1, bhav_df2  = pd.DataFrame(), pd.DataFrame() 
            call_entry_price, call_exit_price = None, None
            call_entry_turnover, call_exit_turnover = None, None
            put_entry_price, put_exit_price = None, None
            put_entry_turnover, put_exit_turnover = None, None
            fut_entry_price, fut_exit_price = None, None
            call_net, put_net, fut_net, total_net = None, None, None, None

            try:
                bhav_df1 = pd.read_csv(f"./cleaned_csvs/{fileName1}")
            except:
                reason = f"{fileName1} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, prev_expiry, put_expiry, fut_expiry, fromDate, toDate)
                continue

            try:
                bhav_df1['Date'] = pd.to_datetime(bhav_df1['Date'], format='%Y-%m-%d')
            except:
                bhav_df1['Date'] = pd.to_datetime(bhav_df1['Date'], format='%d-%m-%Y')
            
            try:
                bhav_df1['ExpiryDate'] = pd.to_datetime(bhav_df1['ExpiryDate'], format='%Y-%m-%d')
            except:
                bhav_df1['ExpiryDate'] = pd.to_datetime(bhav_df1['ExpiryDate'], format='%d-%m-%Y')

            try:
                bhav_df2 = pd.read_csv(f"./cleaned_csvs/{fileName2}")
            except:
                reason = f"{fileName2} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, fut_expiry, fromDate, toDate)
                continue

            try:
                bhav_df2['Date'] = pd.to_datetime(bhav_df2['Date'], format='%Y-%m-%d')
            except:
                bhav_df2['Date'] = pd.to_datetime(bhav_df2['Date'], format='%d-%m-%Y')
            
            try:
                bhav_df2['ExpiryDate'] = pd.to_datetime(bhav_df2['ExpiryDate'], format='%Y-%m-%d')
            except:
                bhav_df2['ExpiryDate'] = pd.to_datetime(bhav_df2['ExpiryDate'], format='%d-%m-%Y')

            if callPosition>=0:
                call_entry_data = bhav_df1[
                                    (bhav_df1['Instrument']=="OPTIDX")
                                    & (bhav_df1['Symbol']=="NIFTY")
                                    & (bhav_df1['OptionType']=="CE")
                                    & (
                                        (bhav_df1['ExpiryDate']==curr_expiry)
                                        | (bhav_df1['ExpiryDate']==curr_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==curr_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['StrikePrice']>=call_strike)
                                    & (bhav_df1['TurnOver']>0)
                                ].sort_values(by='StrikePrice', ascending=True).reset_index(drop=True)
            else:
                call_entry_data = bhav_df1[
                                    (bhav_df1['Instrument']=="OPTIDX")
                                    & (bhav_df1['Symbol']=="NIFTY")
                                    & (bhav_df1['OptionType']=="CE")
                                    & (
                                        (bhav_df1['ExpiryDate']==curr_expiry)
                                        | (bhav_df1['ExpiryDate']==curr_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==curr_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['StrikePrice']<=call_strike)
                                    & (bhav_df1['TurnOver']>0)
                                ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)
                
            if call_entry_data.empty:
                reason = f"Call Data for Strike Near {call_strike} with Turnover>0 not found"
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, fut_expiry, fromDate, toDate)
                continue
            
            call_strike = call_entry_data.iloc[0]['StrikePrice']
            call_entry_data = bhav_df1[
                                    (bhav_df1['Instrument']=="OPTIDX")
                                    & (bhav_df1['Symbol']=="NIFTY")
                                    & (bhav_df1['OptionType']=="CE")
                                    & (
                                        (bhav_df1['ExpiryDate']==curr_expiry)
                                        | (bhav_df1['ExpiryDate']==curr_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==curr_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['StrikePrice']==call_strike)
                                ]
            call_exit_data = bhav_df2[
                                (bhav_df2['Instrument']=="OPTIDX")
                                & (bhav_df2['Symbol']=="NIFTY")
                                & (bhav_df2['OptionType']=="CE")
                                & (
                                    (bhav_df2['ExpiryDate']==curr_expiry)
                                    | (bhav_df2['ExpiryDate']==curr_expiry-timedelta(days=1))
                                    |  (bhav_df2['ExpiryDate']==curr_expiry+timedelta(days=1))
                                )
                                & (bhav_df2['StrikePrice']==call_strike)
                            ].reset_index(drop=True)
            
            if call_entry_data.empty or call_exit_data.empty:
                if(call_entry_data.empty):
                    reason = f"Call Entry Data missing for Strike {int(call_strike)} with Expiry {curr_expiry}"
                else:
                    reason = f"Call Exit Data missing for Call Strike {int(call_strike)} with Expiry {curr_expiry}"
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, fut_expiry, fromDate, toDate)
                continue
            
            call_entry_price = call_entry_data.iloc[0]['Close']
            call_exit_price = call_exit_data.iloc[0]['Close']
            call_entry_turnover = call_entry_data.iloc[0]['TurnOver']
            call_exit_turnover = call_exit_data.iloc[0]['TurnOver']
            call_net =  round(call_entry_price -  call_exit_price, 2)
           
            put_entry_data = bhav_df1[
                                (bhav_df1['Instrument']=="OPTIDX")
                                & (bhav_df1['Symbol']=="NIFTY")
                                & (bhav_df1['OptionType']=="PE")
                                & (
                                    (bhav_df1['ExpiryDate']==put_expiry)
                                    | (bhav_df1['ExpiryDate']==put_expiry-timedelta(days=1))
                                    |  (bhav_df1['ExpiryDate']==put_expiry+timedelta(days=1))
                                )
                                & (bhav_df1['StrikePrice']>=put_strike)
                                & (bhav_df1['TurnOver']>0)
                            ].sort_values(by='StrikePrice', ascending=True).reset_index(drop=True)
            
            if put_entry_data.empty:
                reason = f"Put Data above {put_strike} with Turnover>0 not found"
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, fut_expiry, fromDate, toDate)
                continue

            put_strike = put_entry_data.iloc[0]['StrikePrice']
            put_entry_data = bhav_df1[
                                (bhav_df1['Instrument']=="OPTIDX")
                                & (bhav_df1['Symbol']=="NIFTY")
                                & (bhav_df1['OptionType']=="PE")
                                & (
                                    (bhav_df1['ExpiryDate']==put_expiry)
                                    | (bhav_df1['ExpiryDate']==put_expiry-timedelta(days=1))
                                    |  (bhav_df1['ExpiryDate']==put_expiry+timedelta(days=1))
                                )
                                & (bhav_df1['StrikePrice']==put_strike)
                            ]
            
            put_exit_data = bhav_df2[
                                (bhav_df2['Instrument']=="OPTIDX")
                                & (bhav_df2['Symbol']=="NIFTY")
                                & (bhav_df2['OptionType']=="PE")
                                & (
                                    (bhav_df2['ExpiryDate']==put_expiry)
                                    | (bhav_df2['ExpiryDate']==put_expiry-timedelta(days=1))
                                    |  (bhav_df2['ExpiryDate']==put_expiry+timedelta(days=1))
                                )
                                & (bhav_df2['StrikePrice']==put_strike)
                            ]
            
            if put_entry_data.empty or put_exit_data.empty:
                if put_entry_data.empty:
                    reason = f"Put Entry Data missing for Strike {int(put_strike)} with Expiry {put_expiry}"
                else:
                    reason = f"Put Exit Data missing for Strike {int(put_strike)} with Expiry {put_expiry}"
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, fut_expiry, fromDate, toDate)
                continue

            put_entry_price = put_entry_data.iloc[0]['Close']               
            put_exit_price = put_exit_data.iloc[0]['Close']
            put_entry_turnover = put_entry_data.iloc[0]['TurnOver']    
            put_exit_turnover = put_exit_data.iloc[0]['TurnOver']    
            put_net = round(put_exit_price - put_entry_price, 2)
            
            fut_entry_data = bhav_df1[
                                (bhav_df1['Instrument']=="FUTIDX")
                                & (bhav_df1['Symbol']=="NIFTY")
                                & (bhav_df1['ExpiryDate'].dt.month==fut_expiry.month)
                                & (bhav_df1['ExpiryDate'].dt.year==fut_expiry.year)
                            ]
            fut_exit_data = bhav_df2[
                                (bhav_df2['Instrument']=="FUTIDX")
                                & (bhav_df2['Symbol']=="NIFTY")
                                & (bhav_df2['ExpiryDate'].dt.month==fut_expiry.month)
                                & (bhav_df2['ExpiryDate'].dt.year==fut_expiry.year)
                            ]
            if fut_entry_data.empty or fut_exit_data.empty:
                if fut_entry_data.empty:
                    reason = f"Future Entry Data missing for Expiry {fut_expiry}"
                else:
                    reason = f"Future Exit Data missing for Expiry {fut_expiry}"
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, fut_expiry, fromDate, toDate)
                continue

            fut_entry_price = fut_entry_data.iloc[0]['Close']
            fut_exit_price = fut_exit_data.iloc[0]['Close']
            fut_net = round(fut_exit_price - fut_entry_price, 2)

            total_net = round(call_net + put_net + fut_net, 2)

            analysis_data.append({
                    "Entry Date" : fromDate,
                    "Exit Date" : toDate,
                    
                    "Entry Spot" : entrySpot,
                    "Exit Spot" : exitSpot,

                    "Future Expiry" : fut_expiry,
                    "Future EntryPrice": fut_entry_price,
                    "Future ExitPrice" : fut_exit_price,
                    "Future P&L": fut_net,

                    "Call Expiry" : curr_expiry,
                    "Call Strike" : call_strike,
                    "Call EntryPrice" : call_entry_price,
                    "Call Entry Turnover": call_entry_turnover,
                    "Call ExitPrice" : call_exit_price,
                    "Call Exit Turnover": call_exit_turnover,
                    "Call P&L" : call_net,

                    "Put Expiry" : put_expiry,
                    "Put Strike" : put_strike,
                    "Put EntryPrice" : put_entry_price,
                    "Put Entry Turnover": put_entry_turnover,
                    "Put ExitPrice" : put_exit_price,
                    "Put Exit Turnover": put_exit_turnover,
                    'Put P&L' : put_net,
                    
                    "Net P&L" : total_net,
                    
                })
            
    if analysis_data:
        path = "./Output/Current_Call_Next_Put_Current_Future/Weekly/Expiry To Expiry"
        
        if callPosition==0:
            fileName = f"Current_CE_ATM_Sell_Next_PE_ATM_Buy_FUT_Buy"
        elif(callPosition>0):
            fileName = f"Current_CE_{100*callPosition}OTM_Sell_Next_PE_ATM_Buy_FUT_Buy"
        else:
            fileName = f"Current_CE_{100*callPosition}ITM_Sell_Next_PE_ATM_Buy_FUT_Buy"
        
        if adjustment_type==0:
            fileName + f"_NoAdjustment"
            path = path + "/Unadjusted"
        elif adjustment_type==1:
            fileName = fileName + f"_RiseBy{adjustment_points}Points"
            path = path + "/Adjusted/Rise Only"
        elif adjustment_type==2:
            fileName = fileName + f"_FallBy{adjustment_points}Points"
            path = path + "/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_RiseOrFallBy{adjustment_points}Points"
            path = path + "/Adjusted/RiseOrFall"

        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")

    if logFile:
        path = "./Output/Current_Call_Next_Put_Current_Future/Weekly/Expiry To Expiry"
        
        if callPosition==0:
            fileName = f"Current_CE_ATM_Sell_Next_PE_ATM_Buy_FUT_Buy"
        elif(callPosition>0):
            fileName = f"Current_CE_{100*callPosition}OTM_Sell_Next_PE_ATM_Buy_FUT_Buy"
        else:
            fileName = f"Current_CE_{100*callPosition}ITM_Sell_Next_PE_ATM_Buy_FUT_Buy"
        
        if adjustment_type==0:
            fileName + f"_NoAdjustment"
            path = path + "/Unadjusted"
        elif adjustment_type==1:
            fileName = fileName + f"_RiseBy{adjustment_points}Points"
            path = path + "/Adjusted/Rise Only"
        elif adjustment_type==2:
            fileName = fileName + f"_FallBy{adjustment_points}Points"
            path = path + "/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_RiseOrFallBy{adjustment_points}Points"
            path = path + "/Adjusted/RiseOrFall"

        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()
