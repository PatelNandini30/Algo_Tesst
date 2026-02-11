import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

def main1_V7_With_HSL(
    call_premium=True, put_premium=True, 
    premium_multiplier=1, 
    call_sell=True, put_sell=True,
    call_hsl_pct = 100, put_hsl_pct = 100
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
    for w in range(0, len(weekly_expiry_df)):
        prev_expiry = weekly_expiry_df.iloc[w]['Previous Expiry']
        curr_expiry = weekly_expiry_df.iloc[w]['Current Expiry']
        
        filtered_data = data_df_1[
                            (data_df_1['Date']>=prev_expiry)
                            & (data_df_1['Date']<=curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)
        
        if(len(filtered_data)<2):
            continue
        
        all_dates = filtered_data['Date'].unique()
        call_entry_data, put_entry_data = pd.DataFrame(), pd.DataFrame()
        temp_dict = {}
        call_strike, put_strike = None, None
        fromDate = filtered_data.iloc[0]['Date']
        toDate = filtered_data.iloc[-1]['Date']
        call_flag, put_flag = False, False

        if call_premium and put_premium:
            if call_sell and put_sell:
                print(f"Call OTM and Put OTM Sell of Spot +/- Total Premium of Call and Put ATM*{premium_multiplier}x From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            elif call_sell:
                print(f"Call Sell  of Spot + Total Premium of Call and Put ATM*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            else:
                print(f"Put Sell  of Spot - Total Premium of Call and Put ATM*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
        elif call_premium:
            if call_sell and put_sell:
                print(f"Call OTM and Put OTM Sell of Spot +/- Total Premium of Call*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            elif call_sell:
                print(f"Call Sell  of Spot + Total Premium of Call*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            else:
                print(f"Put Sell  of Spot - Total Premium of Call*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
        else:
            put_premium = True 
            if call_sell and put_sell:
                print(f"Call OTM and Put OTM Sell of Spot +/- Total Premium of Put*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            elif call_sell:
                print(f"Call Sell  of Spot + Total Premium of Put*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            else:
                print(f"Put Sell  of Spot - Total Premium of Put*{premium_multiplier}x  From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")

        print(f"With Call HSL of {call_hsl_pct}% and Put HSL of {put_hsl_pct}%")
        
        if call_sell:
            i = 0
            while(i<len(all_dates)):
                curr_date = all_dates[i]
                curr_spot = filtered_data.iloc[i]['Close']
                fileName = curr_date.strftime('%Y-%m-%d') + ".csv"
            
                try:
                    bhav_df = pd.read_csv(f"./cleaned_csvs/{fileName}")
                except:
                    reason = f"{fileName} not found in cleaned_csvs. Skipping the Trade"
                    createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, curr_date, curr_date)
                    i += 1
                    continue

                try:
                    bhav_df['Date'] = pd.to_datetime(bhav_df['Date'], format='%Y-%m-%d')
                except:
                    bhav_df['Date'] = pd.to_datetime(bhav_df['Date'], format='%d-%m-%Y')

                try:
                    bhav_df['ExpiryDate'] = pd.to_datetime(bhav_df['ExpiryDate'], format='%Y-%m-%d')
                except:
                    bhav_df['ExpiryDate'] = pd.to_datetime(bhav_df['ExpiryDate'], format='%d-%m-%Y')
                

                if i==0:
                    atm_strike = round_half_up(curr_spot/50)*50
                    call_atm_data = bhav_df[
                        (bhav_df['Instrument']=="OPTIDX")
                            & (bhav_df['Symbol']=="NIFTY")
                            & (bhav_df['OptionType']=="CE")
                            & (
                                (bhav_df['ExpiryDate']==curr_expiry)
                                | (bhav_df['ExpiryDate']==curr_expiry-timedelta(days=1))
                                |  (bhav_df['ExpiryDate']==curr_expiry+timedelta(days=1))
                            )
                            & (bhav_df['StrikePrice']==atm_strike)
                    ]
                
                    put_atm_data = bhav_df[
                        (bhav_df['Instrument']=="OPTIDX")
                            & (bhav_df['Symbol']=="NIFTY")
                            & (bhav_df['OptionType']=="PE")
                            & (
                                (bhav_df['ExpiryDate']==curr_expiry)
                                | (bhav_df['ExpiryDate']==curr_expiry-timedelta(days=1))
                                |  (bhav_df['ExpiryDate']==curr_expiry+timedelta(days=1))
                            )
                            & (bhav_df['StrikePrice']==atm_strike)
                    ]
                
                    if call_atm_data.empty or put_atm_data.empty:
                        reason = f"Call Data Missing for ATM Strike {atm_strike}" if call_atm_data.empty\
                            else f"Put Data Missing for ATM Strike {atm_strike}"
                        createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, curr_date, curr_date)
                        call_flag = True
                        break
                    
                    total_premium = 0
                    call_temp_premium = call_atm_data.iloc[0]['Close']
                    put_temp_premium = put_atm_data.iloc[0]['Close']

                    if (call_premium):
                        total_premium += call_temp_premium
                    if put_premium:
                        total_premium += put_temp_premium

                    call_strike = curr_spot + (total_premium*premium_multiplier)
                    call_strike = round_half_up(call_strike/50)*50
                    
                    temp_dict['ATM Strike'] = atm_strike
                    temp_dict['Call Premium'] = call_temp_premium
                    temp_dict['Put Premium'] = put_temp_premium
                    temp_dict['Total Premium'] = total_premium

                    call_entry_data = bhav_df[
                        (bhav_df['Instrument']=="OPTIDX")
                        & (bhav_df['Symbol']=="NIFTY")
                        & (bhav_df['OptionType']=="CE")
                        & (
                            (bhav_df['ExpiryDate']==curr_expiry)
                            | (bhav_df['ExpiryDate']==curr_expiry-timedelta(days=1))
                            |  (bhav_df['ExpiryDate']==curr_expiry+timedelta(days=1))
                        )
                        & (bhav_df['StrikePrice']>=call_strike)
                        & (bhav_df['TurnOver']>0)
                    ].sort_values(by='StrikePrice', ascending=True).reset_index(drop=True)
                    
                    if call_entry_data.empty:
                        reason = f"Call Data above {call_strike} Strike not found"
                        createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, curr_date, curr_date)
                        call_flag = True
                        break
                
                    call_strike = call_entry_data.iloc[0]['StrikePrice']
                    call_entry_data = call_entry_data[
                        (call_entry_data['StrikePrice']==call_strike)
                    ]
                    
                    temp_dict['Call Expiry'] = curr_expiry
                    temp_dict['Call Strike'] = call_strike
                    temp_dict['Call Entry Date'] = curr_date
                    temp_dict['Call Entry Spot'] = curr_spot
                    temp_dict['Call EntryPrice'] = call_entry_data.iloc[0]['Close']
                    temp_dict['Call Entry Turnover'] = call_entry_data.iloc[0]['TurnOver']
                    temp_dict['Call HSL'] = temp_dict['Call EntryPrice']*(1+(call_hsl_pct*0.01))

                    temp_dict['Call Exit Date'] = pd.NaT
                    temp_dict['Call Exit Spot'] = None
                    temp_dict['Call ExitPrice'] = None
                    temp_dict['Call Exit Turnover'] = None
                    temp_dict['Call P&L'] = None

                else:
                    if (pd.isna(temp_dict['Call ExitPrice'])):
                        call_exit_data =   bhav_df[
                            (bhav_df['Instrument']=="OPTIDX")
                            & (bhav_df['Symbol']=="NIFTY")
                            & (bhav_df['OptionType']=="CE")
                            & (
                                (bhav_df['ExpiryDate']==curr_expiry)
                                | (bhav_df['ExpiryDate']==curr_expiry-timedelta(days=1))
                                |  (bhav_df['ExpiryDate']==curr_expiry+timedelta(days=1))
                            )
                            & (bhav_df['StrikePrice']==call_strike)
                        ].reset_index(drop=True)
                
                        if call_exit_data.empty:
                            reason = f"Call Exit Data for Strike {call_strike} not found"
                            createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, curr_date, curr_date)
                            i += 1
                            continue

                        if call_exit_data.iloc[0]['Close']>=temp_dict['Call HSL']:
                            temp_dict['Call Exit Date'] = curr_date
                            temp_dict['Call Exit Spot'] = curr_spot
                            temp_dict['Call ExitPrice'] = call_exit_data.iloc[0]['Close']
                            temp_dict['Call Exit Turnover'] = call_exit_data.iloc[0]['TurnOver']
                            temp_dict['Call P&L'] = round(temp_dict['Call EntryPrice'] - temp_dict['Call ExitPrice'], 2)
                            break
                
                i += 1
            
            if call_flag:
                continue

            if (pd.isna(temp_dict['Call ExitPrice'])):
                temp_dict['Call Exit Date'] = curr_date
                temp_dict['Call Exit Spot'] = curr_spot
                fileName = curr_date.strftime('%Y-%m-%d') + ".csv"
                try:
                    bhav_df = pd.read_csv(f"./cleaned_csvs/{fileName}")
                except:
                    reason = f"{fileName} not found in cleaned_csvs. Skipping the Trade"
                    createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, curr_date, curr_date)
                    bhav_df = pd.DataFrame()  
                
                if len(bhav_df)>0:
                    try:
                        bhav_df['Date'] = pd.to_datetime(bhav_df['Date'], format='%Y-%m-%d')
                    except:
                        bhav_df['Date'] = pd.to_datetime(bhav_df['Date'], format='%d-%m-%Y')

                    try:
                        bhav_df['ExpiryDate'] = pd.to_datetime(bhav_df['ExpiryDate'], format='%Y-%m-%d')
                    except:
                        bhav_df['ExpiryDate'] = pd.to_datetime(bhav_df['ExpiryDate'], format='%d-%m-%Y')

                    call_exit_data = bhav_df[
                                (bhav_df['Instrument']=="OPTIDX")
                                & (bhav_df['Symbol']=="NIFTY")
                                & (bhav_df['OptionType']=="CE")
                                & (
                                    (bhav_df['ExpiryDate']==curr_expiry)
                                    | (bhav_df['ExpiryDate']==curr_expiry-timedelta(days=1))
                                    |  (bhav_df['ExpiryDate']==curr_expiry+timedelta(days=1))
                                )
                                & (bhav_df['StrikePrice']==call_strike)
                            ].reset_index(drop=True)
                    
                    if call_exit_data.empty:
                        reason = f"Call Exit Data For Strike {call_strike} not found"
                        createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, curr_date, curr_date)
                        call_flag = True
                    else:
                        temp_dict['Call ExitPrice'] = call_exit_data.iloc[0]['Close']
                        temp_dict['Call Exit Turnover'] = call_exit_data.iloc[0]['TurnOver']
                        temp_dict['Call P&L'] = round(temp_dict['Call EntryPrice'] - temp_dict['Call ExitPrice'], 2)

        if put_sell:
            i = 0
            while(i<len(all_dates)):
                curr_date = all_dates[i]
                curr_spot = filtered_data.iloc[i]['Close']
                fileName = curr_date.strftime('%Y-%m-%d') + ".csv"
            
                try:
                    bhav_df = pd.read_csv(f"./cleaned_csvs/{fileName}")
                except:
                    reason = f"{fileName} not found in cleaned_csvs. Skipping the Trade"
                    createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, curr_date, curr_date)
                    i += 1
                    continue

                try:
                    bhav_df['Date'] = pd.to_datetime(bhav_df['Date'], format='%Y-%m-%d')
                except:
                    bhav_df['Date'] = pd.to_datetime(bhav_df['Date'], format='%d-%m-%Y')

                try:
                    bhav_df['ExpiryDate'] = pd.to_datetime(bhav_df['ExpiryDate'], format='%Y-%m-%d')
                except:
                    bhav_df['ExpiryDate'] = pd.to_datetime(bhav_df['ExpiryDate'], format='%d-%m-%Y')
                

                if i==0:
                    atm_strike = round_half_up(curr_spot/50)*50
                    call_atm_data = bhav_df[
                        (bhav_df['Instrument']=="OPTIDX")
                            & (bhav_df['Symbol']=="NIFTY")
                            & (bhav_df['OptionType']=="CE")
                            & (
                                (bhav_df['ExpiryDate']==curr_expiry)
                                | (bhav_df['ExpiryDate']==curr_expiry-timedelta(days=1))
                                |  (bhav_df['ExpiryDate']==curr_expiry+timedelta(days=1))
                            )
                            & (bhav_df['StrikePrice']==atm_strike)
                    ]
                
                    put_atm_data = bhav_df[
                        (bhav_df['Instrument']=="OPTIDX")
                            & (bhav_df['Symbol']=="NIFTY")
                            & (bhav_df['OptionType']=="PE")
                            & (
                                (bhav_df['ExpiryDate']==curr_expiry)
                                | (bhav_df['ExpiryDate']==curr_expiry-timedelta(days=1))
                                |  (bhav_df['ExpiryDate']==curr_expiry+timedelta(days=1))
                            )
                            & (bhav_df['StrikePrice']==atm_strike)
                    ]
                
                    if call_atm_data.empty or put_atm_data.empty:
                        reason = f"Call Data Missing for ATM Strike {atm_strike}" if call_atm_data.empty\
                            else f"Put Data Missing for ATM Strike {atm_strike}"
                        createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, curr_date, curr_date)
                        put_flag = True
                        break
                    
                    total_premium = 0
                    call_temp_premium = call_atm_data.iloc[0]['Close']
                    put_temp_premium = put_atm_data.iloc[0]['Close']

                    if (call_premium):
                        total_premium += call_temp_premium
                    if put_premium:
                        total_premium += put_temp_premium

                    put_strike = curr_spot - (total_premium*premium_multiplier)
                    put_strike = round_half_up(put_strike/50)*50
                    
                    
                    temp_dict['ATM Strike'] = atm_strike
                    temp_dict['Call Premium'] = call_temp_premium
                    temp_dict['Put Premium'] = put_temp_premium
                    temp_dict['Total Premium'] = total_premium

                    put_entry_data = bhav_df[
                        (bhav_df['Instrument']=="OPTIDX")
                        & (bhav_df['Symbol']=="NIFTY")
                        & (bhav_df['OptionType']=="PE")
                        & (
                            (bhav_df['ExpiryDate']==curr_expiry)
                            | (bhav_df['ExpiryDate']==curr_expiry-timedelta(days=1))
                            |  (bhav_df['ExpiryDate']==curr_expiry+timedelta(days=1))
                        )
                        & (bhav_df['StrikePrice']<=put_strike)
                        & (bhav_df['TurnOver']>0)
                    ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)
                    
                    if put_entry_data.empty:
                        reason = f"Put Data below {put_strike} Strike not found"
                        createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, curr_date, curr_date)
                        put_flag = True
                        break
                        
                
                    put_strike = put_entry_data.iloc[0]['StrikePrice']
                    put_entry_data = put_entry_data[
                        (put_entry_data['StrikePrice']==put_strike)
                    ]
                    
                    temp_dict['Put Expiry'] = curr_expiry
                    temp_dict['Put Strike'] = put_strike
                    temp_dict['Put Entry Date'] = curr_date
                    temp_dict['Put Entry Spot'] = curr_spot
                    temp_dict['Put EntryPrice'] = put_entry_data.iloc[0]['Close']
                    temp_dict['Put Entry Turnover'] = put_entry_data.iloc[0]['TurnOver']
                    temp_dict['Put HSL'] = temp_dict['Put EntryPrice']*(1+(put_hsl_pct*0.01))

                    temp_dict['Put Exit Date'] = pd.NaT
                    temp_dict['Put Exit Spot'] = None
                    temp_dict['Put ExitPrice'] = None
                    temp_dict['Put Exit Turnover'] = None
                    temp_dict['Put P&L'] = None
                else:
                    if (pd.isna(temp_dict['Put ExitPrice'])):
                        put_exit_data =   bhav_df[
                            (bhav_df['Instrument']=="OPTIDX")
                            & (bhav_df['Symbol']=="NIFTY")
                            & (bhav_df['OptionType']=="PE")
                            & (
                                (bhav_df['ExpiryDate']==curr_expiry)
                                | (bhav_df['ExpiryDate']==curr_expiry-timedelta(days=1))
                                |  (bhav_df['ExpiryDate']==curr_expiry+timedelta(days=1))
                            )
                            & (bhav_df['StrikePrice']==put_strike)
                        ].reset_index(drop=True)
                        
                        if put_exit_data.empty:
                            reason = f"Put Exit Data for Strike {put_strike} not found"
                            createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, curr_date, curr_date)
                            i += 1
                            continue

                        if put_exit_data.iloc[0]['Close']>=temp_dict['Put HSL']:
                            temp_dict['Put Exit Date'] = curr_date
                            temp_dict['Put Exit Spot'] = curr_spot
                            temp_dict['Put ExitPrice'] = put_exit_data.iloc[0]['Close']
                            temp_dict['Put Exit Turnover'] = put_exit_data.iloc[0]['TurnOver']
                            temp_dict['Put P&L'] = round(temp_dict['Put EntryPrice'] - temp_dict['Put ExitPrice'], 2)
                            break
                
                i += 1

            if put_flag:
                continue

            if (pd.isna(temp_dict['Put ExitPrice'])):
                temp_dict['Put Exit Date'] = curr_date
                temp_dict['Put Exit Spot'] = curr_spot
                
                fileName = curr_date.strftime('%Y-%m-%d') + ".csv"
                try:
                    bhav_df = pd.read_csv(f"./cleaned_csvs/{fileName}")
                except:
                    reason = f"{fileName} not found in cleaned_csvs. Skipping the Trade"
                    createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, curr_date, curr_date)
                    bhav_df = pd.DataFrame()  
                
                if len(bhav_df)>0:
                    try:
                        bhav_df['Date'] = pd.to_datetime(bhav_df['Date'], format='%Y-%m-%d')
                    except:
                        bhav_df['Date'] = pd.to_datetime(bhav_df['Date'], format='%d-%m-%Y')

                    try:
                        bhav_df['ExpiryDate'] = pd.to_datetime(bhav_df['ExpiryDate'], format='%Y-%m-%d')
                    except:
                        bhav_df['ExpiryDate'] = pd.to_datetime(bhav_df['ExpiryDate'], format='%d-%m-%Y')

                    put_exit_data = bhav_df[
                                (bhav_df['Instrument']=="OPTIDX")
                                & (bhav_df['Symbol']=="NIFTY")
                                & (bhav_df['OptionType']=="PE")
                                & (
                                    (bhav_df['ExpiryDate']==curr_expiry)
                                    | (bhav_df['ExpiryDate']==curr_expiry-timedelta(days=1))
                                    |  (bhav_df['ExpiryDate']==curr_expiry+timedelta(days=1))
                                )
                                & (bhav_df['StrikePrice']==put_strike)
                            ].reset_index(drop=True)
                    if put_exit_data.empty:
                        reason = f"Put Exit Data For Strike {put_strike} not found"
                        put_flag = True
                    else:
                        temp_dict['Put ExitPrice'] = put_exit_data.iloc[0]['Close']
                        temp_dict['Put Exit Turnover'] = put_exit_data.iloc[0]['TurnOver']
                        temp_dict['Put P&L'] = round(temp_dict['Put EntryPrice'] - temp_dict['Put ExitPrice'], 2)

        if call_flag or put_flag:
            continue

        if temp_dict:
            analysis_data.append(temp_dict)

    
    if analysis_data:
        analysis_df = pd.DataFrame(analysis_data)
        if call_sell and put_sell:
            path = "./Output/Straddle_Bull/Weekly/Expiry To Expiry"
            columns = [
                        'ATM Strike',
                        'Call Premium',
                        'Put Premium',
                        'Total Premium',

                        'Call Expiry',
                        'Call Strike',
                        'Call Entry Date',
                        'Call Entry Spot',
                        'Call EntryPrice',
                        'Call Entry Turnover',
                        'Call HSL',
                        'Call Exit Date',
                        'Call Exit Spot',
                        'Call ExitPrice',
                        'Call Exit Turnover',
                        'Call P&L',
                        
                        
                        'Put Expiry',
                        'Put Strike',
                        'Put Entry Date',
                        'Put Entry Spot',
                        'Put EntryPrice',
                        'Put Entry Turnover',
                        'Put HSL',
                        'Put Exit Date',
                        'Put Exit Spot',
                        'Put ExitPrice',
                        'Put Exit Turnover',
                        'Put P&L'
                       ]
            analysis_df = analysis_df[columns]
            analysis_df['Net P&L'] = analysis_df['Call P&L'] + analysis_df['Put P&L']
        
        elif call_sell:
            path = "./Output/Straddle_Call_Only_Bull/Weekly/Expiry To Expiry"
            columns = [
                        'ATM Strike',
                        'Call Premium',
                        'Put Premium',
                        'Total Premium',

                        'Call Expiry',
                        'Call Strike',
                        'Call Entry Date',
                        'Call Entry Spot',
                        'Call EntryPrice',
                        'Call Entry Turnover',
                        'Call HSL',
                        'Call Exit Date',
                        'Call Exit Spot',
                        'Call ExitPrice',
                        'Call Exit Turnover',
                        'Call P&L',
                       ]
            analysis_df = analysis_df[columns]
            analysis_df['Net P&L'] = analysis_df['Call P&L']
        else:
            path = "./Output/Straddle_Put_Only_Bull/Weekly/Expiry To Expiry"
            columns = [
                        'ATM Strike',
                        'Call Premium',
                        'Put Premium',
                        'Total Premium',

                        'Put Expiry',
                        'Put Strike',
                        'Put Entry Date',
                        'Put Entry Spot',
                        'Put EntryPrice',
                        'Put Entry Turnover',
                        'Put HSL',
                        'Put Exit Date',
                        'Put Exit Spot',
                        'Put ExitPrice',
                        'Put Exit Turnover',
                        'Put P&L'
                       ]
            analysis_df = analysis_df[columns]
            analysis_df['Net P&L'] = analysis_df['Put P&L']
       
        if call_premium and put_premium:
            fileName = "CE_PE_ATM_Total_Premium"
        elif call_premium:
            fileName = "CE_ATM_Premium"
        else:
            fileName = "PE_ATM_Premium"
        
        fileName = fileName + f"_{premium_multiplier}x"
        
        if call_sell and put_sell:
            fileName = fileName + "_Call_OTM_Sell_Put_OTM_Sell" + f"_{call_hsl_pct}%_Call_HSL_{put_hsl_pct}%_Put_HSL_Adjustment"
        elif call_sell:
            fileName = fileName + "_Call_OTM_Sell" + f"_{call_hsl_pct}%_Call_HSL_Adjustment"
        else:
            fileName = fileName + "_Put_OTM_Sell"  + f"_{call_hsl_pct}%_Put_HSL_Adjustment"
        
        path = path + "/HSL Adjustment"
        os.makedirs(path, exist_ok=True)
        analysis_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")
    
    if logFile:
        log_df = pd.DataFrame(logFile)
        if call_sell and put_sell:
            path = "./Output/Straddle_Bull/Weekly/Expiry To Expiry"
        elif call_sell:
            path = "./Output/Straddle_Call_Only_Bull/Weekly/Expiry To Expiry"
        else:
            path = "./Output/Straddle_Put_Only_Bull/Weekly/Expiry To Expiry"

        if call_premium and put_premium:
            fileName = "CE_PE_ATM_Total_Premium"
        elif call_premium:
            fileName = "CE_ATM_Premium"
        else:
            fileName = "PE_ATM_Premium"
        
        fileName = fileName + f"_{premium_multiplier}x"
        
        if call_sell and put_sell:
            fileName = fileName + "_Call_OTM_Sell_Put_OTM_Sell" + f"_{call_hsl_pct}%_Call_HSL_{put_hsl_pct}%_Put_HSL_Adjustment"
        elif call_sell:
            fileName = fileName + "_Call_OTM_Sell" + f"_{call_hsl_pct}%_Call_HSL_Adjustment"
        else:
            fileName = fileName + "_Put_OTM_Sell"  + f"_{call_hsl_pct}%_Put_HSL_Adjustment"
        
        path = path + "/HSL Adjustment"
        fileName = fileName + "_Log"
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()
