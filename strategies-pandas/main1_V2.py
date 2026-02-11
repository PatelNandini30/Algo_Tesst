import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

def main1_V2(spot_adjustment_type=0, spot_adjustment=1, put_sell_position=0):
    data_df = getStrikeData("NIFTY")
    
    base2_df = pd.read_csv("./Filter/base2.csv")
    base2_df['Start'] = pd.to_datetime(base2_df['Start'], format='%Y-%m-%d')
    base2_df['End'] = pd.to_datetime(base2_df['End'], format='%Y-%m-%d')
    base2_df = base2_df.sort_values(by=['Start', 'End']).reset_index(drop=True)
    
    base_list = []
    for i in range(0, len(base2_df)-1):
        base_list.append(
            {
                'Start' : base2_df.iloc[i]['End'],
                'End' : base2_df.iloc[i+1]['Start']
            }
        )
    
    if base_list:
        base2_df = pd.DataFrame(base_list)
    else:
        base2_df = pd.DataFrame()
    
    if base2_df.empty:
        sys.exit("Base Bear Phase Dataframe Empty")
   
    mask = pd.Series(False, index=data_df.index)
    for _, row in base2_df.iterrows():
        mask |= (data_df['Date'] >= row['Start']) & (data_df['Date'] <= row['End'])
    data_df = data_df[mask].reset_index(drop=True).copy(deep=True)
    

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
        
        curr_monthly_expiry = monthly_expiry_df[
                                (monthly_expiry_df['Current Expiry']>=curr_expiry)
                                ].sort_values(by='Current Expiry').reset_index(drop=True)
        
        if(curr_monthly_expiry.empty):
            continue
        
        curr_fut_expiry = curr_monthly_expiry.iloc[0]['Current Expiry']
        next_fut_expiry = curr_monthly_expiry.iloc[0]['Next Expiry']
        fut_expiry = curr_fut_expiry
        
        filtered_data = data_df[
                            (data_df['Date']>=prev_expiry)
                            & (data_df['Date']<=curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)
        
        if(len(filtered_data)<2):
            continue
        
        intervals, interval_df = [], pd.DataFrame()
        
        if spot_adjustment_type!=0:
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


        for i in range(0, len(interval_df)):
            fileName1 = fileName2 = ""
            fromDate = interval_df.iloc[i]['From']
            toDate = interval_df.iloc[i]['To']
            
            if(fromDate==toDate):
                continue

            print(f"Put Sell Future Sell Weekly Expiry to Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            exitSpot =  filtered_data[filtered_data['Date']==toDate] 
            
            if entrySpot.empty:
                continue
            
            entrySpot = entrySpot.iloc[0]['Close']
            put_strike = round((entrySpot*(1+(put_sell_position/100)))/100)*100

            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None

            
            fileName1 = fromDate.strftime("%Y-%m-%d") + ".csv"
            fileName2 = toDate.strftime("%Y-%m-%d") + ".csv"
            bhav_df1, bhav_df2  = pd.DataFrame(), pd.DataFrame()
            put_entry_price, put_exit_price = None, None
            put_entry_turnover, put_exit_turnover = None, None
            fut_entry_price, fut_exit_price = None, None
            put_net, fut_net, total_net = None, None, None

            try:
                bhav_df1 = pd.read_csv(f"./cleaned_csvs/{fileName1}")
            except:
                reason = f"{fileName1} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, fut_expiry, fromDate, toDate)
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
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, fut_expiry, fromDate, toDate)
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
            if put_sell_position>=0:
                put_entry_data = bhav_df1[
                                    (bhav_df1['Instrument']=="OPTIDX")
                                    & (bhav_df1['Symbol']=="NIFTY")
                                    & (bhav_df1['OptionType']=="PE")
                                    & (
                                        (bhav_df1['ExpiryDate']==curr_expiry)
                                        | (bhav_df1['ExpiryDate']==curr_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==curr_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['StrikePrice']>=put_strike)
                                    & (bhav_df1['TurnOver']>0)
                                    & (bhav_df1['StrikePrice']%100==0)
                                ].sort_values(by='StrikePrice', ascending=True).reset_index(drop=True)
            else:
                put_entry_data = bhav_df1[
                                    (bhav_df1['Instrument']=="OPTIDX")
                                    & (bhav_df1['Symbol']=="NIFTY")
                                    & (bhav_df1['OptionType']=="PE")
                                    & (
                                        (bhav_df1['ExpiryDate']==curr_expiry)
                                        | (bhav_df1['ExpiryDate']==curr_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==curr_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['StrikePrice']<=put_strike)
                                    & (bhav_df1['TurnOver']>0)
                                    & (bhav_df1['StrikePrice']%100==0)
                                ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)

            
            if(put_entry_data.empty):
                reason = f"No Strike Found below {put_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, fut_expiry, fromDate, toDate)
                continue

            put_strike = put_entry_data.iloc[0]['StrikePrice']
            put_entry_data = put_entry_data[(put_entry_data['StrikePrice']==put_strike)].reset_index(drop=True)
            put_exit_data = bhav_df2[
                                (bhav_df2['Instrument']=="OPTIDX")
                                & (bhav_df2['Symbol']=="NIFTY")
                                & (bhav_df2['OptionType']=="PE")
                                & (
                                    (bhav_df2['ExpiryDate']==curr_expiry)
                                    | (bhav_df2['ExpiryDate']==curr_expiry-timedelta(days=1))
                                    |  (bhav_df2['ExpiryDate']==curr_expiry+timedelta(days=1))
                                )
                                & (bhav_df2['StrikePrice']==put_strike)
                            ].reset_index(drop=True)
            
            if put_entry_data.empty or put_exit_data.empty:
                if(put_entry_data.empty):
                    reason = f"Call Entry Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Call Exit Data missing for Call Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, fut_expiry, fromDate, toDate)
                continue
        
            put_entry_price = put_entry_data.iloc[0]['Close']
            put_exit_price = put_exit_data.iloc[0]['Close']
            put_entry_turnover = put_entry_data.iloc[0]['TurnOver']
            put_exit_turnover = put_exit_data.iloc[0]['TurnOver']
            put_net =  round(put_entry_price -  put_exit_price, 2)
        
        
            fut_entry_data = bhav_df1[
                                (bhav_df1['Instrument']=="FUTIDX")
                                & (bhav_df1['Symbol']=="NIFTY")
                                & (bhav_df1['ExpiryDate'].dt.month==fut_expiry.month)
                                & (bhav_df1['ExpiryDate'].dt.year==fut_expiry.year)
                            ].reset_index(drop=True)
            fut_exit_data = bhav_df2[
                                (bhav_df2['Instrument']=="FUTIDX")
                                & (bhav_df2['Symbol']=="NIFTY")
                                & (bhav_df2['ExpiryDate'].dt.month==fut_expiry.month)
                                & (bhav_df2['ExpiryDate'].dt.year==fut_expiry.year)
                            ].reset_index(drop=True)
            
            if fut_entry_data.empty or fut_exit_data.empty:
                if fut_entry_data.empty:
                    reason = f"Future Entry Data missing for Expiry {fut_expiry}"
                else:
                    reason = f"Future Exit Data missing for Expiry {fut_expiry}"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, fut_expiry, fromDate, toDate)
                continue

            fut_entry_price = fut_entry_data.iloc[0]['Close']
            fut_exit_price = fut_exit_data.iloc[0]['Close']
            fut_net = round(fut_entry_price - fut_exit_price, 2)

            total_net = round(put_net + fut_net, 2)
       
            analysis_data.append({
                    "Entry Date" : fromDate,
                    "Exit Date" : toDate,
                    
                    "Entry Spot" : entrySpot,
                    "Exit Spot" : exitSpot,

                    "Future Expiry" : fut_expiry,
                    "Future EntryPrice": fut_entry_price,
                    "Future ExitPrice" : fut_exit_price,
                    "Future P&L": fut_net,

                    "Put Expiry" : curr_expiry,
                    "Put Strike" : put_strike,
                    "Put EntryPrice" : put_entry_price,
                    "Put Entry Turnover": put_entry_turnover,
                    "Put ExitPrice" : put_exit_price,
                    "Put Exit Turnover" : put_exit_turnover,
                    "Put P&L" : put_net,

                    "Net P&L" : total_net,
                    
                })
                
    if analysis_data:
        path = "./Output/PE_Sell_Future_Sell/Weekly/Expiry To Expiry"
        if put_sell_position==0:
            fileName = f"PE_ATM_Sell_FUT_Sell"
        elif put_sell_position>0:
            fileName = f"PE_{put_sell_position}%_ITM_Sell_FUT_Sell"
        else:
            fileName = f"PE_{put_sell_position}%_OTM_Sell_FUT_Sell"
        
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
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Weekly_Expiry-To-Expiry"
        
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")

    if logFile:
        path = "./Output/PE_Sell_Future_Sell/Weekly/Expiry To Expiry"
        if put_sell_position==0:
            fileName = f"PE_ATM_Sell_FUT_Sell"
        elif put_sell_position>0:
            fileName = f"PE_{put_sell_position}%_ITM_Sell_FUT_Sell"
        else:
            fileName = f"PE_{put_sell_position}%_OTM_Sell_FUT_Sell"
        
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
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Weekly_Expiry-To-Expiry_Log"
        
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()
