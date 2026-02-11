import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

def main2_V4_With_HSL(call_sell_position=0, put_sell_position=0, call_hsl_pct=100, put_hsl_pct=100, t_2=False):
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

        fromDate = filtered_data.iloc[0]['Date']
        toDate = filtered_data.iloc[-1]['Date']
        
        if t_2:
            print("T-2 To T-2", end= " ")
        else:
            print("T-1 To T-1", end= " ")
        
        print(f"Call Sell Put Sell With Call HSL {call_hsl_pct}% and Put HSL {put_hsl_pct}% From {fromDate.strftime('%Y-%m-%d')} To {toDate.strftime('%Y-%m-%d')}")
        all_dates = sorted(filtered_data['Date'].unique())
        call_entry_data, put_entry_data = pd.DataFrame(), pd.DataFrame()
        temp_dict = {}
        call_strike, put_strike = None, None
        call_flag, put_flag = False, False
       
        i = 0 
        while(i<len(all_dates)):
            curr_date = all_dates[i]
            curr_spot = filtered_data.iloc[i]['Close']
            fileName = curr_date.strftime("%Y-%m-%d") + ".csv"
            
            try:
                bhav_df = pd.read_csv(f"./cleaned_csvs/{fileName}")
            except:
                reason = f"{fileName} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, curr_date, curr_date)
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
                call_strike = round_half_up((curr_spot*(1+(call_sell_position/100)))/100)*100

                if call_sell_position>=0:
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
                    ].sort_values(by='StrikePrice', ascending=True).reset_index(drop=True)
                else:
                    call_entry_data = bhav_df[
                        (bhav_df['Instrument']=="OPTIDX")
                            & (bhav_df['Symbol']=="NIFTY")
                            & (bhav_df['OptionType']=="CE")
                            & (
                                (bhav_df['ExpiryDate']==curr_expiry)
                                | (bhav_df['ExpiryDate']==curr_expiry-timedelta(days=1))
                                |  (bhav_df['ExpiryDate']==curr_expiry+timedelta(days=1))
                            )
                            & (bhav_df['StrikePrice']<=call_strike)
                    ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)
                
                if call_entry_data.empty:
                    reason = f"Call Entry Data for Strike Near {call_strike} not found"
                    createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, curr_date, curr_date)
                    call_strike = None
                    call_flag = True
                    break
                
                call_strike = call_entry_data.iloc[0]['StrikePrice'] 
                call_entry_data = call_entry_data[call_entry_data['StrikePrice']==call_strike]
                
                temp_dict['Call Strike'] = call_strike
                temp_dict['Call Expiry'] = curr_expiry
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
                if pd.isna(temp_dict['Call ExitPrice']):
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
                    
                    if( 
                        (not call_exit_data.empty) 
                        and (call_exit_data.iloc[0]['Close']>=temp_dict['Call HSL'])
                    ):
                        temp_dict['Call Exit Date'] = curr_date
                        temp_dict['Call Exit Spot'] = curr_spot
                        temp_dict['Call ExitPrice'] = call_exit_data.iloc[0]['Close']
                        temp_dict['Call Exit Turnover'] = call_exit_data.iloc[0]['TurnOver']
                        temp_dict['Call P&L'] = round(temp_dict['Call EntryPrice'] - temp_dict['Call ExitPrice'], 2)
                        break
            i += 1
        
        if call_flag:
            continue

        if pd.isna(temp_dict['Call ExitPrice']):
            fileName = curr_date.strftime("%Y-%m-%d") + ".csv"
            bhav_df = pd.DataFrame()

            try:
                bhav_df = pd.read_csv(f"./cleaned_csvs/{fileName}")
            except:
                reason = f"{fileName} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, curr_date, curr_date)
                call_flag = True
                
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
                    call_flag = True
                    reason = f"Call Exit Data for Strike {call_strike} not found"
                    createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, curr_date, curr_date)
                else:
                    temp_dict['Call Exit Date'] = curr_date
                    temp_dict['Call Exit Spot'] = curr_spot
                    temp_dict['Call ExitPrice'] = call_exit_data.iloc[0]['Close']
                    temp_dict['Call Exit Turnover'] = call_exit_data.iloc[0]['TurnOver']
                    temp_dict['Call P&L'] = round(temp_dict['Call EntryPrice'] - temp_dict['Call ExitPrice'], 2)
            
        # /////
        i = 0
        while(i<len(all_dates)):
            curr_date = all_dates[i]
            curr_spot = filtered_data.iloc[i]['Close']
            fileName = curr_date.strftime("%Y-%m-%d") + ".csv"
            
            try:
                bhav_df = pd.read_csv(f"./cleaned_csvs/{fileName}")
            except:
                reason = f"{fileName} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, curr_date, curr_date)
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
                put_strike = round_half_up((curr_spot*(1+(put_sell_position/100)))/100)*100

                if put_sell_position>=0:
                    put_entry_data = bhav_df[
                        (bhav_df['Instrument']=="OPTIDX")
                            & (bhav_df['Symbol']=="NIFTY")
                            & (bhav_df['OptionType']=="PE")
                            & (
                                (bhav_df['ExpiryDate']==curr_expiry)
                                | (bhav_df['ExpiryDate']==curr_expiry-timedelta(days=1))
                                |  (bhav_df['ExpiryDate']==curr_expiry+timedelta(days=1))
                            )
                            & (bhav_df['StrikePrice']>=put_strike)
                    ].sort_values(by='StrikePrice', ascending=True).reset_index(drop=True)
                else:
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
                    ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)
                
                if put_entry_data.empty:
                    reason = f"Put Entry Data for Strike Near {put_strike} not found"
                    createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, curr_date, curr_date)
                    put_strike = None
                    put_flag = True
                    break
            
                put_strike = put_entry_data.iloc[0]['StrikePrice'] 
                put_entry_data = put_entry_data[put_entry_data['StrikePrice']==put_strike]
                
                temp_dict['Put Strike'] = put_strike
                temp_dict['Put Expiry'] = curr_expiry
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
                if pd.isna(temp_dict['Put ExitPrice']):
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
                  
                    if( 
                        (not put_exit_data.empty) 
                        and (put_exit_data.iloc[0]['Close']>=temp_dict['Put HSL'])
                    ):
                        temp_dict['Put Exit Date'] = curr_date
                        temp_dict['Put Exit Spot'] = curr_spot
                        temp_dict['Put ExitPrice'] = put_exit_data.iloc[0]['Close']
                        temp_dict['Put Exit Turnover'] = put_exit_data.iloc[0]['TurnOver']
                        temp_dict['Put P&L'] = round(temp_dict['Put EntryPrice'] - temp_dict['Put ExitPrice'], 2)
                        break
            i += 1
       
        if put_flag:
            continue

        if pd.isna(temp_dict['Put ExitPrice']):
            fileName = curr_date.strftime("%Y-%m-%d") + ".csv"
            bhav_df = pd.DataFrame()

            try:
                bhav_df = pd.read_csv(f"./cleaned_csvs/{fileName}")
            except:
                reason = f"{fileName} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, curr_date, curr_date)
                put_flag = True
                
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
                    put_flag = True
                    reason = f"Put Exit Data for Strike {put_strike} not found"
                    createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, curr_date, curr_date)

                else:
                    temp_dict['Put Exit Date'] = curr_date
                    temp_dict['Put Exit Spot'] = curr_spot
                    temp_dict['Put ExitPrice'] = put_exit_data.iloc[0]['Close']
                    temp_dict['Put Exit Turnover'] = put_exit_data.iloc[0]['TurnOver']
                    temp_dict['Put P&L'] = round(temp_dict['Put EntryPrice'] - temp_dict['Put ExitPrice'], 2)
        
        if call_flag or put_flag:
            continue
        
        if temp_dict:
            analysis_data.append(temp_dict)
      

    if analysis_data:
        if t_2:
            path = "./Output/Call_Sell_Put_Sell_Bull/Weekly/T-2 To T-2/HSL Adjustment"
        else:
            path = "./Output/Call_Sell_Put_Sell_Bull/Weekly/T-1 To T-1/HSL Adjustment"

        
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"
         
        if put_sell_position==0:
            fileName = fileName +f"_PE_ATM_Sell"
        elif put_sell_position>0:
            fileName = fileName +f"_PE_{put_sell_position}%_ITM_Sell"
        else:
            fileName = fileName +f"_PE_{put_sell_position}%_OTM_Sell"
        
        if t_2:
            fileName = fileName + "_Weekly_Expiry_T-2_to_T-2"
        else:
            fileName = fileName + "_Weekly_Expiry_T-1_to_T-1"
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + f"_Call_HSL_{call_hsl_pct}%_Put_HSL_{put_hsl_pct}%"
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df['Net P&L'] = analyse_df['Call P&L'] + analyse_df['Put P&L']
        analyse_df['Net P&L'] = analyse_df['Net P&L'].round(2)
        
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"
         
        if put_sell_position==0:
            fileName = fileName +f"_PE_ATM_Sell"
        elif put_sell_position>0:
            fileName = fileName +f"_PE_{put_sell_position}%_ITM_Sell"
        else:
            fileName = fileName +f"_PE_{put_sell_position}%_OTM_Sell"
        
        
        if t_2:
            fileName = fileName + "_Weekly_Expiry_T-2_to_T-2"
            path = "./Output/Call_Sell_Put_Sell_Bull/Weekly/T-2 To T-2/HSL Adjustment"
        else:
            fileName = fileName + "_Weekly_Expiry_T-1_to_T-1"
            path = "./Output/Call_Sell_Put_Sell_Bull/Weekly/T-1 To T-1/HSL Adjustment"
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + f"_Call_HSL_{call_hsl_pct}%_Put_HSL_{put_hsl_pct}%"
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()
