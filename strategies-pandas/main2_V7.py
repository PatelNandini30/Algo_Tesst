import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

def main2_V7(spot_adjustment_type=0, spot_adjustment=1, 
        call_premium=True, put_premium=True, 
        premium_multiplier=1, 
        call_sell=True, put_sell=True,
        t_2=False
):
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
    
    analysis_data = []
    first_instance = False
    
    for w in range(0, len(weekly_expiry_df)):
        prev_expiry = weekly_expiry_df.iloc[w]['Previous Expiry']
        curr_expiry = weekly_expiry_df.iloc[w]['Current Expiry']
        
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
            data_dict = {}
            fileName1 = fileName2 = ""
            fromDate = interval_df.iloc[i]['From']
            toDate = interval_df.iloc[i]['To']
            
            if(fromDate==toDate):
                continue
            if t_2:
                print("T-2 To T-2", end = " ")
            else:
                print("T-1 To T-1", end = " ")
            if call_premium and put_premium:
                if call_sell and put_sell:
                    print(f"Call OTM and Put OTM Sell of Spot - Total Premium of Call and Put ATM*{premium_multiplier}x From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                elif call_sell:
                    print(f"Call Sell  of Spot - Total Premium of Call and Put ATM*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                else:
                    print(f"Put Sell  of Spot - Total Premium of Call and Put ATM*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            elif call_premium:
                if call_sell and put_sell:
                    print(f"Call OTM and Put OTM Sell of Spot - Total Premium of Call*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                elif call_sell:
                    print(f"Call Sell  of Spot - Total Premium of Call*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                else:
                    print(f"Put Sell  of Spot - Total Premium of Call*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            else:
                put_premium = True 
                if call_sell and put_sell:
                    print(f"Call OTM and Put OTM Sell of Spot - Total Premium of Put*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                elif call_sell:
                    print(f"Call Sell  of Spot - Total Premium of Put*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                else:
                    print(f"Put Sell  of Spot - Total Premium of Put*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            if entrySpot.empty:
                continue
            entrySpot = entrySpot.iloc[0]['Close']
            atm_strike = round_half_up(entrySpot/50)*50
            
            exitSpot =  filtered_data[filtered_data['Date']==toDate] 
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None

            fileName1 = fromDate.strftime("%Y-%m-%d") + ".csv"
            fileName2 = toDate.strftime("%Y-%m-%d") + ".csv"
            
            bhav_df1, bhav_df2  = pd.DataFrame(), pd.DataFrame()
            total_premium = 0

            try:
                bhav_df1 = pd.read_csv(f"./cleaned_csvs/{fileName1}")
            except:
                reason = f"{fileName1} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, fromDate, toDate)
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
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, fromDate, toDate)
                continue

            try:    
                bhav_df2['Date'] = pd.to_datetime(bhav_df2['Date'], format='%Y-%m-%d')
            except:
                bhav_df2['Date'] = pd.to_datetime(bhav_df2['Date'], format='%d-%m-%Y')

            try:    
                bhav_df2['ExpiryDate'] = pd.to_datetime(bhav_df2['ExpiryDate'], format='%Y-%m-%d')
            except:
                bhav_df2['ExpiryDate'] = pd.to_datetime(bhav_df2['ExpiryDate'], format='%d-%m-%Y')
            
            call_atm_data = bhav_df1[
                                    (bhav_df1['Instrument']=="OPTIDX")
                                    & (bhav_df1['Symbol']=="NIFTY")
                                    & (bhav_df1['OptionType']=="CE")
                                    & (
                                        (bhav_df1['ExpiryDate']==curr_expiry)
                                        | (bhav_df1['ExpiryDate']==curr_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==curr_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['StrikePrice']==atm_strike)
                                ].reset_index(drop=True)
            put_atm_data = bhav_df1[
                                (bhav_df1['Instrument']=="OPTIDX")
                                & (bhav_df1['Symbol']=="NIFTY")
                                & (bhav_df1['OptionType']=="PE")
                                & (
                                    (bhav_df1['ExpiryDate']==curr_expiry)
                                    | (bhav_df1['ExpiryDate']==curr_expiry-timedelta(days=1))
                                    |  (bhav_df1['ExpiryDate']==curr_expiry+timedelta(days=1))
                                )
                                & (bhav_df1['StrikePrice']==atm_strike)
                            ].reset_index(drop=True)
            
            data_dict['Entry Date'] = fromDate
            data_dict['Exit Date'] = toDate
            data_dict['Entry Spot'] = entrySpot
            data_dict['Exit Spot'] = exitSpot
            data_dict['Net P&L'] = 0
            data_dict['ATM Strike'] = atm_strike
            data_dict['Call Premium'] = call_atm_data.iloc[0]['Close']
            data_dict['Put Premium'] = put_atm_data.iloc[0]['Close']
            
            if call_premium:    
                total_premium = total_premium + call_atm_data.iloc[0]['Close']
            if put_premium:
                total_premium = total_premium + put_atm_data.iloc[0]['Close']
            
            data_dict['Total Premium'] = total_premium
        
            if total_premium==0:
                print("Issue with Total Premium")
                sys.exit()
            
            call_strike = entrySpot + (total_premium*premium_multiplier)
            call_strike = round_half_up(call_strike/50)*50
            put_strike = entrySpot - (total_premium*premium_multiplier)
            put_strike = round_half_up(put_strike/50)*50
            
            if call_sell:
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
                
                if call_entry_data.empty:
                    reason = f"Call Data above {call_strike} with Turnover>0 not found"
                    createLogFile("NIFTY", reason, curr_expiry, pd.NaT, pd.NaT, fromDate, toDate)
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
                                ].reset_index(drop=True)
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
                
                if call_exit_data.empty:
                    reason = f"Call Exit Data for Strike {call_strike} not found"
                    createLogFile("NIFTY", reason, curr_expiry, pd.NaT, pd.NaT, fromDate, toDate)
                    continue
                
                data_dict['Call Strike'] = call_strike
                data_dict['Call Expiry'] = curr_expiry
                data_dict['Call EntryPrice'] = call_entry_data.iloc[0]['Close']
                data_dict['Call Entry Turnover'] = call_entry_data.iloc[0]['TurnOver']
                data_dict['Call ExitPrice'] = call_exit_data.iloc[0]['Close']
                data_dict['Call Exit Turnover'] = call_exit_data.iloc[0]['TurnOver']
                data_dict['Call P&L'] =   data_dict['Call EntryPrice'] - data_dict['Call ExitPrice']
                data_dict['Net P&L'] = data_dict['Net P&L'] + data_dict['Call P&L']
            
            if put_sell:
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
                                ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)
                
                if put_entry_data.empty:
                    reason = f"Put Data below {put_strike} with Turnover>0 not found"
                    createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, fromDate, toDate)
                    continue
                
                put_strike = put_entry_data.iloc[0]['StrikePrice']
                
                put_entry_data = bhav_df1[
                                    (bhav_df1['Instrument']=="OPTIDX")
                                    & (bhav_df1['Symbol']=="NIFTY")
                                    & (bhav_df1['OptionType']=="PE")
                                    & (
                                        (bhav_df1['ExpiryDate']==curr_expiry)
                                        | (bhav_df1['ExpiryDate']==curr_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==curr_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['StrikePrice']==put_strike)
                                ].reset_index(drop=True)
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
                
                if put_exit_data.empty:
                    reason = f"Put Exit Data for Strike {put_strike} not found"
                    createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, fromDate, toDate)
                    continue
                
                data_dict['Put Strike'] = put_strike
                data_dict['Put Expiry'] = curr_expiry
                data_dict['Put EntryPrice'] = put_entry_data.iloc[0]['Close']
                data_dict['Put Entry Turnover'] = put_entry_data.iloc[0]['TurnOver']
                data_dict['Put ExitPrice'] = put_exit_data.iloc[0]['Close']
                data_dict['Put Exit Turnover'] = put_exit_data.iloc[0]['TurnOver']
                data_dict['Put P&L'] =   data_dict['Put EntryPrice'] - data_dict['Put ExitPrice']
                data_dict['Net P&L'] = data_dict['Net P&L'] + data_dict['Put P&L']
            

            analysis_data.append(data_dict)

    
    if analysis_data:
        analysis_df = pd.DataFrame(analysis_data)
        
        if call_sell and put_sell:
            path = "./Output/Straddle_Bull/Weekly"
            columns = [
                        'Entry Date',
                        'Exit Date',
                        'Entry Spot',
                        'Exit Spot',

                        'ATM Strike',
                        'Call Premium',
                        'Put Premium',
                        'Total Premium',

                        'Call Expiry',
                        'Call Strike',
                        'Call EntryPrice',
                        'Call Entry Turnover',
                        'Call ExitPrice',
                        'Call Exit Turnover',
                        'Call P&L',
                        
                        'Put Expiry',
                        'Put Strike',
                        'Put EntryPrice',
                        'Put Entry Turnover',
                        'Put ExitPrice',
                        'Put Exit Turnover',
                        'Put P&L',

                        'Net P&L'
                       ]
        elif call_sell:
            path = "./Output/Straddle_Call_Only_Bull/Weekly"
            columns = [
                        'Entry Date',
                        'Exit Date',
                        'Entry Spot',
                        'Exit Spot',

                        'ATM Strike',
                        'Call Premium',
                        'Put Premium',
                        'Total Premium',

                        'Call Expiry',
                        'Call Strike',
                        'Call EntryPrice',
                        'Call Entry Turnover',
                        'Call ExitPrice',
                        'Call Exit Turnover',
                        'Call P&L',
                        
                        'Net P&L'
                       ]
            
        else:
            path = "./Output/Straddle_Put_Only_Bull/Weekly"
            columns = [
                        'Entry Date',
                        'Exit Date',
                        'Entry Spot',
                        'Exit Spot',

                        'ATM Strike',
                        'Call Premium',
                        'Put Premium',
                        'Total Premium',
                        
                        'Put Expiry',
                        'Put Strike',
                        'Put EntryPrice',
                        'Put Entry Turnover',
                        'Put ExitPrice',
                        'Put Exit Turnover',
                        'Put P&L',

                        'Net P&L'
                       ]
        
        analysis_df = analysis_df[columns]

        if call_premium and put_premium:
            fileName = "CE_PE_ATM_Total_Premium"
        elif call_premium:
            fileName = "CE_ATM_Premium"
        else:
            fileName = "PE_ATM_Premium"
        
        fileName = fileName + f"_{premium_multiplier}x"
        
        if call_sell and put_sell:
            fileName = fileName + "_Call_OTM_Sell_Put_OTM_Sell"
        elif call_sell:
            fileName = fileName + "_Call_OTM_Sell"
        else:
            fileName = fileName + "_Put_OTM_Sell"
        
        if t_2:
            fileName = fileName + "_T-2_To_T-2"
            path = path + "/T-2 To T-2"
        else:
            fileName = fileName + "_T-1_To_T-1"
            path = path + "/T-1 To T-1"

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
        analysis_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")
    
    if logFile:
        log_df = pd.DataFrame(logFile)
        
        if call_sell and put_sell:
            path = "./Output/Straddle_Bull/Weekly"
        elif call_sell:
            path = "./Output/Straddle_Call_Only_Bull/Weekly"
        else:
            path = "./Output/Straddle_Put_Only_Bull/Weekly"

        if call_premium and put_premium:
            fileName = "CE_PE_ATM_Total_Premium"
        elif call_premium:
            fileName = "CE_ATM_Premium"
        else:
            fileName = "PE_ATM_Premium"
        
        fileName = fileName + f"_{premium_multiplier}x"
        

        if call_sell and put_sell:
            fileName = fileName + "_Call_OTM_Sell_Put_OTM_Sell"
        elif call_sell:
            fileName = fileName + "_Call_OTM_Sell"
        else:
            fileName = fileName + "_Put_OTM_Sell"

        if t_2:
            fileName = fileName + "_T-2_To_T-2"
            path = path + "/T-2 To T-2"
        else:
            fileName = fileName + "_T-1_To_T-1"
            path = path + "/T-1 To T-1"
        
        
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
        fileName = fileName + "_Log"
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()
