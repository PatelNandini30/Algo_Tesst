import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

def main2_V9(spot_adjustment_type=0, spot_adjustment=1, call_sell_position=0, t_2=False,max_put_spot_pct=0.04):
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
    weekly_expiry_df['MonthYear'] = weekly_expiry_df['Current Expiry'].dt.strftime('%Y')  + "-" +  weekly_expiry_df['Current Expiry'].dt.strftime('%m')
    weekly_expiry_df['Counter'] = (weekly_expiry_df.groupby('MonthYear').cumcount() + 1)
    weekly_expiry_df.at[0, 'Counter'] = 3
    weekly_expiry_df.at[1, 'Counter'] = 4
 
    monthly_expiry_df = pd.read_csv(f"./expiryData/NIFTY_Monthly.csv")
    monthly_expiry_df['Previous Expiry'] = pd.to_datetime(monthly_expiry_df['Previous Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Current Expiry'] = pd.to_datetime(monthly_expiry_df['Current Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Next Expiry'] = pd.to_datetime(monthly_expiry_df['Next Expiry'], format='%Y-%m-%d')
    
    analysis_data = []
    first_instance = False
    for w in range(0, len(weekly_expiry_df)):
        prev_expiry = weekly_expiry_df.iloc[w]['Previous Expiry']
        curr_expiry = weekly_expiry_df.iloc[w]['Current Expiry']
        counter = weekly_expiry_df.iloc[w]['Counter']
        
        filtered_data = data_df_1[
                            (data_df_1['Date']>=prev_expiry)
                            & (data_df_1['Date']<=curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)
        
        if(len(filtered_data)<2):
            continue
        
        if not first_instance:
            filtered_data = data_df_1[
                            (data_df_1['Date']>=prev_expiry)
                            & (data_df_1['Date']<curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)
            first_instance = True
        else:
            last_date_before_expiry = data_df[
                (data_df['Date']<prev_expiry)
            ]    
            if t_2:
                if len(last_date_before_expiry)<2:
                    last_date_before_expiry = prev_expiry
                else:
                    last_date_before_expiry = last_date_before_expiry.iloc[-2]['Date']
            else:
                if last_date_before_expiry.empty:
                    last_date_before_expiry = prev_expiry
                else:
                    last_date_before_expiry = last_date_before_expiry.iloc[-1]['Date']
            
            
            filtered_data = data_df_1[
                                (data_df_1['Date']>=last_date_before_expiry)
                                & (data_df_1['Date']<curr_expiry)
                            ].sort_values(by='Date').reset_index(drop=True)

        if(len(filtered_data)<2):
            continue
        
        if t_2:
            base_ends = base2_df.loc[
                (base2_df['End'] > prev_expiry) & (base2_df['End'] <curr_expiry),
                'End'
            ]
           
            if base_ends.empty:
                filtered_data = filtered_data.iloc[:-1]
            else:
                base_end = base_ends.iloc[0]
                rows_between = data_df[
                    (data_df['Date'] >= base_end) &
                    (data_df['Date'] <= curr_expiry)
                ]
                if len(rows_between) < 2:
                    filtered_data = filtered_data.iloc[:-1]
                
        
        if(len(filtered_data)<2):
            continue

        if counter>2:
            put_month = curr_expiry.month + 1 if curr_expiry.month<12 else 1
            put_year = curr_expiry.year if curr_expiry.month<12 else curr_expiry.year + 1
            put_date = pd.Timestamp(put_year, put_month, 1)
            put_expiry = monthly_expiry_df[
                            (monthly_expiry_df['Current Expiry']>=put_date)
                            ]        
            if put_expiry.empty:
                reason = f"No Next Monthly Expiry Found For Put"
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, pd.NaT, prev_expiry, curr_expiry)
                continue
    
            put_expiry = put_expiry.iloc[0]['Current Expiry']
        else:
            put_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>=curr_expiry].iloc[0]['Current Expiry']
        
        intervals, interval_df = [], pd.DataFrame()
        
        if (spot_adjustment_type!=0):
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
                        roc = 100*(filtered_data1.iloc[t]['Close'] - entryPrice)/entryPrice
                        filtered_data1.at[t, 'Entry_Price'] = entryPrice
                        filtered_data1.at[t, 'Pct_Chg'] = roc
                    
                    if (
                        ((spot_adjustment_type==3) and (abs(roc)>=spot_adjustment))
                        or ((spot_adjustment_type==2) and ((roc<=(-spot_adjustment))))
                        or ((spot_adjustment_type==1) and (roc>=spot_adjustment))
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
        
        
        call_strike, put_strike = None, None
        
        for i in range(0, len(interval_df)):
            fromDate = interval_df.iloc[i]['From']
            toDate = interval_df.iloc[i]['To']
            
            if t_2:
                print("T-2 To T-2", end=" ")
            else:
                print("T-1 To T-1", end=" ")
            
            if(call_sell_position==0):
                print("Call ATM Sell", end=" ")
            elif(call_sell_position>0):
                print(f"Call  {call_sell_position}% OTM Sell", end=" ")
            else:
                print(f"Call  {call_sell_position}% ITM Sell", end=" ")
            
            print(f"Put ITM Sell From:{fromDate.strftime('%d-%m-%Y')} To:{toDate.strftime('%d-%m-%Y')}")
            
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            if entrySpot.empty:
                reason = f"Spot not found for {fromDate}"
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, pd.NaT, prev_expiry, curr_expiry)
                continue
            entrySpot = entrySpot.iloc[0]['Close']
            
            if i==0 or spot_adjustment_type in [2, 3] or True:
                call_strike = round_half_up((entrySpot*(1+(call_sell_position/100)))/100)*100

            exitSpot = filtered_data[filtered_data['Date']==toDate]
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None
           
            fileName1 = fromDate.strftime("%Y-%m-%d") + ".csv"
            fileName2 = toDate.strftime("%Y-%m-%d") + ".csv"
            
            try:
                bhav_df1 = pd.read_csv(f"./cleaned_csvs/{fileName1}")
            except:
                reason = f"{fileName1} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, pd.NaT, fromDate, toDate)
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
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, pd.NaT, fromDate, toDate)
                continue
            
            try:
                bhav_df2['Date'] = pd.to_datetime(bhav_df2['Date'], format='%Y-%m-%d')
            except:
                bhav_df2['Date'] = pd.to_datetime(bhav_df2['Date'], format='%d-%m-%Y')
            
            try:
                bhav_df2['ExpiryDate'] = pd.to_datetime(bhav_df2['ExpiryDate'], format='%Y-%m-%d')
            except:
                bhav_df2['ExpiryDate'] = pd.to_datetime(bhav_df2['ExpiryDate'], format='%d-%m-%Y')

            
            if call_sell_position>=0:
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
                                & (bhav_df1['StrikePrice']%100==0)
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
                                    & (bhav_df1['StrikePrice']%100==0)
                                ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)
                
            if(call_entry_data.empty):
                reason = f"No Strike Found near {call_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, pd.NaT, fromDate, toDate)
                continue

            call_strike = call_entry_data.iloc[0]['StrikePrice']
            call_entry_data = call_entry_data[(call_entry_data['StrikePrice']==call_strike)]
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
                            ]
            
            if call_entry_data.empty or call_exit_data.empty:
                reason = f"Call Exit Data missing for Strike {int(call_strike)}" if call_exit_data.empty else f"Call Entry Data missing for Strike {int(call_strike)}"
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, pd.NaT, fromDate, toDate)
                continue
            
            if(i==0 or (spot_adjustment_type in [1,3]) or True):
                put_data = bhav_df1[
                                    (bhav_df1['Instrument']=="OPTIDX")
                                    & (bhav_df1['Symbol']=="NIFTY")
                                    & (bhav_df1['OptionType']=="PE")
                                    & (
                                        (bhav_df1['ExpiryDate']==put_expiry)
                                        | (bhav_df1['ExpiryDate']==put_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==put_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['TurnOver']>0)
                                    & (bhav_df1['StrikePrice']>=entrySpot)
                                    & (bhav_df1['StrikePrice']<=(entrySpot*(1+max_put_spot_pct)))
                                    & (bhav_df1['StrikePrice']%100==0)
                                ].reset_index(drop=True).copy(deep=True)
                
                if put_data.empty:
                    reason = "No Put ITM Entry Data with Turnover>0 found"
                    createLogFile("NIFTY", reason, curr_expiry, put_expiry, pd.NaT, fromDate, toDate)
                    continue

                put_data['Strike-Spot'] = put_data['StrikePrice'] - entrySpot
                put_data['Close-Strike-Spot']  = (put_data['Strike-Spot'] - put_data['Close']).abs()
                
                put_strike = put_data[put_data['Close-Strike-Spot']==put_data['Close-Strike-Spot'].min()].iloc[0]['StrikePrice']
            
            if pd.isna(put_strike):
                reason = f"Put Strike found to be Null"
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, pd.NaT, fromDate, toDate)
                continue

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
                reason = f"No Put Exit Data for Strike {put_strike} found" if put_exit_data.empty else f"No Put Entry Data for Strike {put_strike} found"
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, pd.NaT, fromDate, toDate)
                continue
            
            call_entry_price = call_entry_data.iloc[0]['Close']
            call_exit_price = call_exit_data.iloc[0]['Close']
            call_entry_turnover = call_entry_data.iloc[0]['TurnOver']
            call_exit_turnover = call_exit_data.iloc[0]['TurnOver']
            call_net =  round(call_entry_price -  call_exit_price, 2)

            put_entry_price = put_entry_data.iloc[0]['Close']
            put_exit_price = put_exit_data.iloc[0]['Close']
            put_entry_turnover = put_entry_data.iloc[0]['TurnOver']
            put_exit_turnover = put_exit_data.iloc[0]['TurnOver']
            put_net =  round(put_entry_price -  put_exit_price, 2)
        
            total_net = round(call_net + put_net, 2)
            
            analysis_data.append({
                    "Entry Date" : fromDate,
                    "Exit Date" : toDate,
                    
                    "Entry Spot" : entrySpot,
                    "Exit Spot" : exitSpot,

                    "Call Expiry" : curr_expiry,
                    "Call Expiry Week": counter,
                    "Call Strike" : call_strike,
                    "Call EntryPrice" : call_entry_price,
                    "Call Entry Turnover": call_entry_turnover,
                    "Call ExitPrice" : call_exit_price,
                    "Call Exit Turnover" : call_exit_turnover,
                    "Call P&L" : call_net,
                    
                    "Put Expiry" : put_expiry,
                    "Put Strike" : put_strike,
                    "Put EntryPrice": put_entry_price,
                    "Put Entry Turnover": put_entry_turnover,
                    "Put ExitPrice" : put_exit_price,
                    "Put Exit Turnover" : put_exit_turnover,
                    "Put P&L": put_net,

                    "Net P&L" : total_net
                })
           
        call_strike, put_strike = None, None

    if analysis_data:
        if t_2:
            path = "./Output/Call_Weekly_Sell_Put_Monthly_ITM_Sell_Bull/Weekly/T-2 To T-2"
        else:
            path = "./Output/Call_Weekly_Sell_Put_Monthly_ITM_Sell_Bull/Weekly/T-1 To T-1"

        if call_sell_position==0:
            fileName = f"CE_ATM_Sell"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"
         
        
        fileName = fileName +f"_PE_ITM_Sell"
        
        if(spot_adjustment_type==0):
            fileName = fileName + "_NoAdjustment"
            path = path + "/Unadjusted"
        elif(spot_adjustment_type==1):
            fileName = fileName + f"_Adjustment_SpotRiseBy{spot_adjustment}%"
            path = path + "/Adjusted/Rise Only"
        elif(spot_adjustment_type==2):
            fileName = fileName + f"_Adjustment_SpotFallsBy{spot_adjustment}%"
            path = path + "/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_Adjustment_SpotRisesOrFallsBy{spot_adjustment}%"
            path = path + "/Adjusted/RiseOrFall"
        
        if t_2:
            fileName = fileName + "_Weekly_Expiry-T-2_To_T-2"
        else:
            fileName = fileName + "_Weekly_Expiry-T-1_To_T-1"

        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        if t_2:
            path = "./Output/Call_Weekly_Sell_Put_Monthly_ITM_Sell_Bull/Weekly/T-2 To T-2"
        else:
            path = "./Output/Call_Weekly_Sell_Put_Monthly_ITM_Sell_Bull/Weekly/T-1 To T-1"

        if call_sell_position==0:
            fileName = f"CE_ATM_Sell"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"
         
        
        fileName = fileName +f"_PE_ITM_Sell"
        
        if(spot_adjustment_type==0):
            fileName = fileName + "_NoAdjustment"
            path = path + "/Unadjusted"
        elif(spot_adjustment_type==1):
            fileName = fileName + f"_Adjustment_SpotRiseBy{spot_adjustment}%"
            path = path + "/Adjusted/Rise Only"
        elif(spot_adjustment_type==2):
            fileName = fileName + f"_Adjustment_SpotFallsBy{spot_adjustment}%"
            path = path + "/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_Adjustment_SpotRisesOrFallsBy{spot_adjustment}%"
            path = path + "/Adjusted/RiseOrFall"
        
        if t_2:
            fileName = fileName + "_Weekly_Expiry-T-2_To_T-2"
        else:
            fileName = fileName + "_Weekly_Expiry-T-1_To_T-1"
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()
