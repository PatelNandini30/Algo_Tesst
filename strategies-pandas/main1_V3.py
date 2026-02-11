import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

def main1_V3(call_sell_position=0, pct_diff=0.3):
    data_df = getStrikeData("NIFTY")
    base2_df = pd.read_csv("./Filter/base2.csv")
    base2_df['Start'] = pd.to_datetime(base2_df['Start'], format='%Y-%m-%d')
    base2_df['End'] = pd.to_datetime(base2_df['End'], format='%Y-%m-%d')
    base2_df = base2_df.sort_values(by=['Start', 'End']).reset_index(drop=True)
    
    mask = pd.Series(False, index=data_df.index)
    for _, row in base2_df.iterrows():
        mask |= (data_df['Date'] >= row['Start']) & (data_df['Date'] <= row['End'])
    data_df_1 = data_df[mask].reset_index(drop=True).copy(deep=True)
    
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
        call_expiry = curr_expiry
        
        curr_monthly_expiry = monthly_expiry_df[
                                (monthly_expiry_df['Current Expiry']>=curr_expiry)
                                ].sort_values(by='Current Expiry').reset_index(drop=True)
        if(curr_monthly_expiry.empty):
            continue
        
        curr_fut_expiry = curr_monthly_expiry.iloc[0]['Current Expiry']
        fut_expiry = curr_fut_expiry
        
        filtered_data = data_df_1[
                            (data_df_1['Date']>=prev_expiry)
                            & (data_df_1['Date']<=curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True).copy(deep=True)
        
        if(len(filtered_data)<2):
            continue
        
        call_strike = round_half_up((filtered_data.iloc[0]['Close']*(1 + (call_sell_position*0.01)))/100)*100
        strike_change_date = pd.NaT
        intervals = []

        for f in range(0, len(filtered_data)):
            f_row = filtered_data.iloc[f]
            curr_spot, curr_date = f_row['Close'], f_row['Date']
            target = round(call_strike * (1 + (pct_diff*0.01)), 2)
            
            if(
                (curr_spot<=target) 
                and (f!=0)
                and (f!=(len(filtered_data)-1))
            ):
                if (pd.isna(strike_change_date)):
                    intervals.append((filtered_data.iloc[0]['Date'], curr_date, call_expiry, call_strike))
                    call_strike = round_half_up((curr_spot*(1+(call_sell_position*0.01)))/100)*100
                    strike_change_date = curr_date
                    call_expiry = next_expiry
                else:
                    intervals.append((strike_change_date, curr_date, call_expiry, call_strike))
                    call_strike = round_half_up((curr_spot*(1+(call_sell_position*0.01)))/100)*100
                    strike_change_date = curr_date

            if f ==len(filtered_data)-1:
                if pd.isna(strike_change_date):
                    intervals.append((filtered_data.iloc[0]['Date'], curr_date, call_expiry, call_strike))
                elif(strike_change_date!=curr_date):
                    intervals.append((strike_change_date, curr_date, call_expiry, call_strike))
        
        if intervals:
            interval_df = pd.DataFrame(intervals, columns=['From', 'To', 'Call Expiry', 'Call Strike'])


        for i in range(0, len(interval_df)):
            i_row = interval_df.iloc[i]
            fromDate, toDate = i_row['From'], i_row['To']
            call_expiry, call_strike = i_row['Call Expiry'], i_row['Call Strike']

            if(fromDate==toDate):
                continue
            
            is_base_start = (
                                (base2_df['Start'] == fromDate)
                            ).any()
            
            if is_base_start:
                fut_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>fromDate].iloc[1]['Current Expiry']

            print(f"Call Sell Future Buy Weekly Expiry-To-Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')} CE Strike Based Adjustment")
            
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            exitSpot =  filtered_data[filtered_data['Date']==toDate] 
            
            if entrySpot.empty:
                continue
    
            entrySpot = entrySpot.iloc[0]['Close']
            
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None

 
            fileName1 = fromDate.strftime("%Y-%m-%d") + ".csv"
            fileName2 = toDate.strftime("%Y-%m-%d") + ".csv"
            bhav_df1, bhav_df2  = pd.DataFrame(), pd.DataFrame()
            call_entry_price, call_exit_price = None, None
            call_entry_turnover, call_exit_turnover = None, None
            fut_entry_price, fut_exit_price = None, None
            call_net, fut_net, total_net = None, None, None

            try:
                bhav_df1 = pd.read_csv(f"./cleaned_csvs/{fileName1}")
            except:
                reason = f"{fileName1} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, call_expiry, pd.NaT, fut_expiry, fromDate, toDate)
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
                createLogFile("NIFTY", reason, call_expiry, pd.NaT, fut_expiry, fromDate, toDate)
                continue
            
            try:
                bhav_df2['Date'] = pd.to_datetime(bhav_df2['Date'], format='%Y-%m-%d')
            except:
                bhav_df2['Date'] = pd.to_datetime(bhav_df2['Date'], format='%d-%m-%Y')
            try:
                bhav_df2['ExpiryDate'] = pd.to_datetime(bhav_df2['ExpiryDate'], format='%Y-%m-%d')
            except:
                bhav_df2['ExpiryDate'] = pd.to_datetime(bhav_df2['ExpiryDate'], format='%d-%m-%Y')
            # Call Data
            if call_sell_position>=0:
                    call_entry_data = bhav_df1[
                                    (bhav_df1['Instrument']=="OPTIDX")
                                    & (bhav_df1['Symbol']=="NIFTY")
                                    & (bhav_df1['OptionType']=="CE")
                                    & (
                                        (bhav_df1['ExpiryDate']==call_expiry)
                                        | (bhav_df1['ExpiryDate']==call_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==call_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['StrikePrice']>=call_strike)
                                    & (bhav_df1['TurnOver']>0)
                                    & (bhav_df1['StrikePrice']%100==0)
                                ].sort_values(by='StrikePrice', ascending=True).reset_index(drop=True)
            
            else:
                call_entry_data = bhav_df1[
                                    (bhav_df1['Instrument']=="OPTIDX")
                                    & (bhav_df1['Symbol']=="NIFTY")
                                    & (bhav_df1['OptionType']=="CE")
                                    & (
                                        (bhav_df1['ExpiryDate']==call_expiry)
                                        | (bhav_df1['ExpiryDate']==call_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==call_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['StrikePrice']<=call_strike)
                                    & (bhav_df1['TurnOver']>0)
                                    & (bhav_df1['StrikePrice']%100==0)
                                ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)
            
            if(call_entry_data.empty):
                reason = f"No Strike Found below {call_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, call_expiry, pd.NaT, fut_expiry, fromDate, toDate)
                continue

            call_strike = call_entry_data.iloc[0]['StrikePrice']
            call_entry_data = call_entry_data[(call_entry_data['StrikePrice']==call_strike)]
            
            call_exit_data = bhav_df2[
                                (bhav_df2['Instrument']=="OPTIDX")
                                & (bhav_df2['Symbol']=="NIFTY")
                                & (bhav_df2['OptionType']=="CE")
                                & (
                                    (bhav_df2['ExpiryDate']==call_expiry)
                                    | (bhav_df2['ExpiryDate']==call_expiry-timedelta(days=1))
                                    |  (bhav_df2['ExpiryDate']==call_expiry+timedelta(days=1))
                                )
                                & (bhav_df2['StrikePrice']==call_strike)
                            ]
            
            if call_entry_data.empty or call_exit_data.empty:
                if(call_entry_data.empty):
                    reason = f"Call Entry Data missing for Strike {int(call_strike)} with Expiry {call_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Call Exit Data missing for Call Strike {int(call_strike)} with Expiry {call_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, call_expiry, pd.NaT, fut_expiry, fromDate, toDate)
                continue
        
            call_entry_price = call_entry_data.iloc[0]['Close']
            call_exit_price = call_exit_data.iloc[0]['Close']
            call_entry_turnover = call_entry_data.iloc[0]['TurnOver']
            call_exit_turnover = call_exit_data.iloc[0]['TurnOver']
            call_net =  round(call_entry_price -  call_exit_price, 2)
        
        
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
                createLogFile("NIFTY", reason, call_expiry, pd.NaT, fut_expiry, fromDate, toDate)
                continue

            fut_entry_price = fut_entry_data.iloc[0]['Close']
            fut_exit_price = fut_exit_data.iloc[0]['Close']
            fut_net = round(fut_exit_price - fut_entry_price, 2)

            total_net = round(call_net + fut_net, 2)

            analysis_data.append({
                    "Entry Date" : fromDate,
                    "Exit Date" : toDate,
                    
                    "Entry Spot" : entrySpot,
                    "Exit Spot" : exitSpot,

                    "Future Expiry" : fut_expiry,
                    "Future EntryPrice": fut_entry_price,
                    "Future ExitPrice" : fut_exit_price,
                    "Future P&L": fut_net,

                    "Call Expiry" : call_expiry,
                    "Call Strike" : call_strike,
                    "Call EntryPrice" : call_entry_price,
                    "Call Entry Turnover": call_entry_turnover,
                    "Call ExitPrice" : call_exit_price,
                    "Call Exit Turnover" : call_exit_turnover,
                    "Call P&L" : call_net,

                    "Net P&L" : total_net,
                    
                })
                
    if analysis_data:
        path = "./Output/Call_Sell_Future_Buy/Weekly/Expiry To Expiry/Strike Adjustment"
        
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell_FUT_Buy"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell_FUT_Buy"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell_FUT_Buy"
        
        fileName = fileName + f"_Weekly_Expiry-To-Expiry(With_{pct_diff}%CE_Strike_Based_Adjustment)"
        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")

    if logFile:
        path = "./Output/Call_Sell_Future_Buy/Weekly/Expiry To Expiry/Strike Adjustment"
        
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell_FUT_Buy"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell_FUT_Buy"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell_FUT_Buy"

        fileName = fileName + f"_Weekly_Expiry-To-Expiry(With_{pct_diff}%CE_Strike_Based_Adjustment)_Log"
        os.makedirs(path, exist_ok=True)
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()
