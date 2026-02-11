import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

def main3(spot_adjustment_type=0, spot_adjustment=1, call_sell_position=0):
    data_df = getStrikeData("NIFTY")
    
    base2_df = pd.read_csv("./Filter/base2.csv")
    base2_df['Start'] = pd.to_datetime(base2_df['Start'], format='%Y-%m-%d')
    base2_df['End'] = pd.to_datetime(base2_df['End'], format='%Y-%m-%d')
    base2_df = base2_df.sort_values(by=['Start', 'End']).reset_index(drop=True)
    
    mask = pd.Series(False, index=data_df.index)
    for _, row in base2_df.iterrows():
        mask |= (data_df['Date'] >= row['Start']) & (data_df['Date'] <= row['End'])
    
    data_df = data_df[mask].reset_index(drop=True)

    monthly_expiry_df = pd.read_csv(f"./expiryData/NIFTY_Monthly.csv")
    monthly_expiry_df['Previous Expiry'] = pd.to_datetime(monthly_expiry_df['Previous Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Current Expiry'] = pd.to_datetime(monthly_expiry_df['Current Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Next Expiry'] = pd.to_datetime(monthly_expiry_df['Next Expiry'], format='%Y-%m-%d')

    m, monthly_expiry_list = 0, []

    while(m<len(monthly_expiry_df)):
        pe = monthly_expiry_df.at[m, 'Previous Expiry']
        ce = monthly_expiry_df.at[m, 'Current Expiry']
        ne = monthly_expiry_df.at[m, 'Next Expiry']
        
        mask = (
            (base2_df['Start'].dt.year == ce.year) &
            (base2_df['Start'].dt.month == ce.month)
        )
        # In case End is after Current Expiry 27-02-2020 Expiry Base End 28-02-2020
        mask1 = (
            (base2_df['End'].dt.year == ce.year) &
            (base2_df['End'].dt.month == ce.month)
        ) 
        
        if mask.any() and (not mask1.any()):
            monthly_expiry_list.append((base2_df.loc[mask, 'Start'].min(), ne, pd.NaT))
            m += 2
        else:
            monthly_expiry_list.append((pe, ce, ne))
            m += 1
    
    
    monthly_expiry_df = pd.DataFrame(
                            monthly_expiry_list, 
                            columns=['Previous Expiry', 'Current Expiry', 'Next Expiry'])
    
    analysis_data = []
    
    for m in range(0, len(monthly_expiry_df)):
        prev_expiry = monthly_expiry_df.iloc[m]['Previous Expiry']
        curr_expiry = monthly_expiry_df.iloc[m]['Current Expiry']
        next_expiry = monthly_expiry_df.iloc[m]['Next Expiry']
        fut_expiry = curr_expiry
        
        filtered_data = data_df[
                            (data_df['Date']>=prev_expiry)
                            & (data_df['Date']<=curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)
    
        if(len(filtered_data)<2):
            continue
         
        intervals, interval_df = [], pd.DataFrame()
        intervals.append((filtered_data.iloc[0]['Date'], filtered_data.iloc[-1]['Date'], curr_expiry))
        
        
        # If base ends after current Expiry
        base_ends = base2_df.loc[
            (base2_df['End'] > curr_expiry) & (base2_df['End'] < next_expiry),
            'End'
        ].sort_values()
        
        if not base_ends.empty:
            intervals.append((filtered_data.iloc[-1]['Date'], base_ends.max(), next_expiry))

        interval_df = pd.DataFrame(intervals, columns=['From', 'To', 'Expiry'])
        interval_df = interval_df.drop_duplicates(subset=['From', 'To', 'Expiry']).reset_index(drop=True)
        
        new_intervals = []
        for _, row in interval_df.iterrows():
            start, end, expiry = row['From'], row['To'], row['Expiry']

            base_ends = base2_df.loc[
                (base2_df['End'] > start) & (base2_df['End'] < end),
                'End'
            ].sort_values()

            if base_ends.empty:
                # no split needed
                new_intervals.append((start, end, expiry))
            else:
                # Split into start and base_end. Skip base_end to End
                curr_start = start
                for be in base_ends:
                    new_intervals.append((curr_start, be, expiry))

        interval_df = pd.DataFrame(new_intervals, columns=['From', 'To', 'Expiry'])    
        interval_df = interval_df.drop_duplicates(subset=['From', 'To', 'Expiry']).reset_index(drop=True)

        filtered_data = data_df[
                            (data_df['Date']>=interval_df['From'].min())
                            & (data_df['Date']<=interval_df['To'].max())
                        ].sort_values(by='Date').reset_index(drop=True)

        if(len(filtered_data)<2):
            continue

        if spot_adjustment_type!=0:
            intervals = []
            for _, int_row in interval_df.iterrows():
                int_start = int_row['From']
                int_end   = int_row['To']
                expiry    = int_row['Expiry']
            
                temp_filtered_data1 = filtered_data[
                    (filtered_data['Date'] >= int_start) &
                    (filtered_data['Date'] <= int_end)
                ].reset_index(drop=True).copy(deep=True)
                
                filtered_data1 = temp_filtered_data1.copy()
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
                    start = temp_filtered_data1.iloc[0]['Date']
                    for d in filtered_data1['Date']:
                        intervals.append((start, d, expiry))
                        start = d   
                    if start != temp_filtered_data1.iloc[-1]['Date']:
                        intervals.append((start, temp_filtered_data1.iloc[-1]['Date'], expiry))       
                else:
                    intervals.append((temp_filtered_data1.iloc[0]['Date'], temp_filtered_data1.iloc[-1]['Date'], expiry))
            
            interval_df = pd.DataFrame(intervals, columns=['From', 'To', 'Expiry'])
        
       
        for i in range(0, len(interval_df)):
            fileName1 = fileName2 = ""
            fromDate = interval_df.iloc[i]['From']
            toDate = interval_df.iloc[i]['To']
            curr_expiry = fut_expiry = interval_df.iloc[i]['Expiry']
            print(f"Call Sell Future Buy Monthly Expiry To Expiry - From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            
            if(fromDate==toDate):
                continue

            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            exitSpot =  filtered_data[filtered_data['Date']==toDate] 

            if entrySpot.empty:
                continue
            
            entrySpot = entrySpot.iloc[0]['Close']
            call_strike = round((entrySpot*(1+(call_sell_position/100)))/100)*100

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
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, fut_expiry, fromDate, toDate)
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
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, fut_expiry, fromDate, toDate)
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
                reason = f"No Strike Found below {call_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, fut_expiry, fromDate, toDate)
                continue

            call_strike = call_entry_data.iloc[0]['StrikePrice']
            call_entry_data = call_entry_data[
                                (call_entry_data['StrikePrice']==call_strike)
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
                            ]
            
            if call_entry_data.empty or call_exit_data.empty:
                if(call_entry_data.empty):
                    reason = f"Call Entry Data missing for Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Call Exit Data missing for Call Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, fut_expiry, fromDate, toDate)
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
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, fut_expiry, fromDate, toDate)
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

                    "Call Expiry" : curr_expiry,
                    "Call Strike" : call_strike,
                    "Call EntryPrice" : call_entry_price,
                    "Call Entry Turnover": call_entry_turnover,
                    "Call ExitPrice" : call_exit_price,
                    "Call Exit Turnover" : call_exit_turnover,
                    "Call P&L" : call_net,

                    "Net P&L" : total_net,
                    
                })

           
    if analysis_data:
        path = "./Output/CE_Sell_Fut_Buy/Monthly/Expiry To Expiry"
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell_FUT_Buy"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell_FUT_Buy"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell_FUT_Buy"

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
        
        os.makedirs(path, exit_ok=True)
        fileName = fileName + "_Monthly_Expiry-To-Expiry"
        
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df = analyse_df.drop_duplicates(subset=['Entry Date', 'Exit Date'])
        print(f"{fileName} saved to {path}")
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)


    if logFile:
        path = "./Output/CE_Sell_Fut_Buy/Monthly/Expiry To Expiry"
        
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell_FUT_Buy"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell_FUT_Buy"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell_FUT_Buy"

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
        
        os.makedirs(path, exit_ok=True)
        fileName = fileName + "_Monthly_Expiry-To-Expiry"
        fileName = fileName + "_Log"
        
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()
