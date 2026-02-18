import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta



def round_half_up(x):
    return math.floor(x + 0.5)

# Creates LogFile - to be called on each symbol within any function
logFile = []
def createLogFile(symbol, reason, call_expiry=None, put_expiry=None, fut_expiry=None, _from=None, _to=None):
    global logFile
    logFile.append({
        'Symbol' : symbol,
        "Call Expiry" : call_expiry,
        "Put Expiry" : put_expiry,
        "Future Expiry" : fut_expiry,
        "Reason" : reason,
        "From" : _from,
        "To" : _to
    })

   
# Selects Strike Data file based on symbol name and returns the data (empty if no data found for symbol)
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


# Current Week Call Sell 
# Next Month Put Buy
# Next to Next Month Future Buy
def main():
    putAdjustment = True
    callAdjustment = True
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
        
        curr_monthly_expiry = monthly_expiry_df[
                                (monthly_expiry_df['Current Expiry'].dt.month==curr_expiry.month)
                                & (monthly_expiry_df['Current Expiry'].dt.year==curr_expiry.year)
                                ].sort_values(by='Current Expiry').reset_index(drop=True)
        
        if(curr_monthly_expiry.empty):
            continue
        else:
            curr_monthly_expiry = curr_monthly_expiry.iloc[0]['Current Expiry']
        
        fut_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>=curr_monthly_expiry]
        if(len(fut_expiry)<3):
            continue
        else:
            fut_expiry = fut_expiry.iloc[2]['Current Expiry']
        
        put_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>=curr_monthly_expiry]
        if(len(put_expiry)<2):
            continue
        else:
            put_expiry = put_expiry.iloc[1]['Current Expiry']
        
        
        filtered_data = data_df[
                            (data_df['Date']>=prev_expiry)
                            & (data_df['Date']<=curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)

        if(
            weekly_expiry_df[
                (weekly_expiry_df['Current Expiry'].dt.month==prev_expiry.month)
                & (weekly_expiry_df['Current Expiry'].dt.year==prev_expiry.year)
            ].iloc[-1]['Current Expiry']==prev_expiry
        ):
            filtered_data = filtered_data[filtered_data['Date']>prev_expiry].reset_index(drop=True)

        if len(filtered_data)<2:
            continue

        intervals, interval_df = [], pd.DataFrame()

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
                    # roc = 100*(filtered_data1.iloc[t]['Close'] - entryPrice)/entryPrice
                    filtered_data1.at[t, 'Entry_Price'] = entryPrice
                    # filtered_data1.at[t, 'Pct_Chg'] = round(roc, 2)
                    filtered_data1.at[t, 'Points_Chg'] = round(roc_point, 2)
                    
                # if abs(roc)>=0.80:
                if(abs(roc_point)>=200):
                    filtered_data1.at[t, 'ReEntry'] = True
                    entryPrice = filtered_data1.iloc[t]['Close']
        
        filtered_data1 = filtered_data1[filtered_data1['ReEntry']==True]
        reentry_dates = []
        
        if len(filtered_data1) > 0:
            start = filtered_data.iloc[0]['Date']
            for d in filtered_data1['Date']:
                intervals.append((start, d))
                start = d   

            if start != filtered_data.iloc[-1]['Date']:
                intervals.append((start, filtered_data.iloc[-1]['Date']))
        else:
            intervals.append((filtered_data.iloc[0]['Date'], filtered_data.iloc[-1]['Date']))
        
        interval_df = pd.DataFrame(intervals, columns=['From', 'To'])
    
        for i in range(0, len(interval_df)):
            fileName1 = fileName2 = ""
            fromDate = interval_df.iloc[i]['From']
            toDate = interval_df.iloc[i]['To']
            print(f"From:{fromDate.strftime('%d-%m-%Y')} To:{toDate.strftime('%d-%m-%Y')}")
            print(f"Call:{curr_expiry.strftime('%d-%m-%Y')}, Put:{put_expiry.strftime('%d-%m-%Y')}, Future:{fut_expiry.strftime('%d-%m-%Y')}")

            entrySpot = filtered_data[filtered_data['Date']==fromDate].iloc[0]['Close']
            exitSpot =  filtered_data[filtered_data['Date']==toDate].iloc[0]['Close']
            
            if(
                (i==0) or (i>0 and putAdjustment) 
            ):
                put_strike = round(entrySpot/100)*100
            
            if(
                (i==0) or (i>0 and callAdjustment) 
            ):
                # call_strike = round(((entrySpot*0.992)/50))*50
                call_strike = round((entrySpot-200)/100)*100
            
            
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

            bhav_df1['Date'] = pd.to_datetime(bhav_df1['Date'], format='%Y-%m-%d')
            bhav_df1['ExpiryDate'] = pd.to_datetime(bhav_df1['ExpiryDate'], format='%Y-%m-%d')

            try:
                bhav_df2 = pd.read_csv(f"./cleaned_csvs/{fileName2}")
            except:
                reason = f"{fileName2} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, fut_expiry, fromDate, toDate)
                continue

            bhav_df2['Date'] = pd.to_datetime(bhav_df2['Date'], format='%Y-%m-%d')
            bhav_df2['ExpiryDate'] = pd.to_datetime(bhav_df2['ExpiryDate'], format='%Y-%m-%d')
            
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
                            ]
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
        path = "./Output/CE_Sell_PE_Next_Buy_Fut_Next_to_Next_Buy/Weekly/Expiry To Expiry"
        os.makedirs(path, exist_ok=True)
        
        fileName = "CE_Sell_PE_Buy_FUT_Buy"
        if not callAdjustment:
            fileName = fileName +"(Call Strike Unadjusted)"
        if not putAdjustment:
            fileName = fileName +"(Put Strike Unadjusted)"
        
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")

    if logFile:
        path = "./Output/CE_Sell_PE_Next_Buy_Fut_Next_to_Next_Buy/Weekly/Expiry To Expiry"
        os.makedirs(path, exist_ok=True)
        
        fileName = "CE_Sell_PE_Buy_FUT_Buy"
        if not callAdjustment:
            fileName = fileName +"(Call Strike Unadjusted)"
        if not putAdjustment:
            fileName = fileName +"(Put Strike Unadjusted)"
        fileName = fileName + "_Log"
       
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()

# # Future Buy Call Sell # #
# Weekly Expiry to Expiry
def main1(spot_adjustment_type=0, spot_adjustment=1, call_sell_position=0):
    data_df = getStrikeData("NIFTY")
    base2_df = pd.read_csv("./Filter/base2.csv")
    base2_df['Start'] = pd.to_datetime(base2_df['Start'], dayfirst=True)
    base2_df['End'] = pd.to_datetime(base2_df['End'], dayfirst=True)
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
        
        curr_monthly_expiry = monthly_expiry_df[
                                (monthly_expiry_df['Current Expiry']>=curr_expiry)
                                ].sort_values(by='Current Expiry').reset_index(drop=True)
        
        if(curr_monthly_expiry.empty):
            continue
        
        curr_fut_expiry = curr_monthly_expiry.iloc[0]['Current Expiry']
        next_fut_expiry = curr_monthly_expiry.iloc[0]['Next Expiry']
        fut_expiry = curr_fut_expiry
        
        filtered_data = data_df_1[
                            (data_df_1['Date']>=prev_expiry)
                            & (data_df_1['Date']<=curr_expiry)
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

            is_base_start = (
                                (base2_df['Start'] == fromDate)
                            ).any()
            
            if is_base_start:
                fut_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>fromDate].iloc[1]['Current Expiry']
           
            print(f"Call Sell Future Buy Expiry To Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            exitSpot =  filtered_data[filtered_data['Date']==toDate] 
            
            if entrySpot.empty:
                continue
            entrySpot = entrySpot.iloc[0]['Close']
            
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None

            call_strike = round((entrySpot*(1+(call_sell_position/100)))/100)*100

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
                createLogFile("NIFTY", reason, prev_expiry, pd.NaT, fut_expiry, fromDate, toDate)
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
                createLogFile("NIFTY", reason, prev_expiry, pd.NaT, fut_expiry, fromDate, toDate)
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
        path = "./Output/CE_Sell_Fut_Buy/Weekly/Expiry To Expiry"
        
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
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Weekly_Expiry-To-Expiry" 
        
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        path = "./Output/CE_Sell_Fut_Buy/Weekly/Expiry To Expiry"
        
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
        
        fileName = fileName + "_Weekly_Expiry-To-Expiry"
        os.makedirs(path, exist_ok=True)

        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()

# Weekly T-1 to T-1
def main2(spot_adjustment_type=0, spot_adjustment=1, call_sell_position=0, t_2=False):
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
    first_instance = False
    
    for w in range(0, len(weekly_expiry_df)):
        prev_expiry = weekly_expiry_df.iloc[w]['Previous Expiry']
        curr_expiry = weekly_expiry_df.iloc[w]['Current Expiry']
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
                            ].sort_values(by='Date').reset_index(drop=True)
        
        if(len(filtered_data)<2):
            continue

        if not first_instance:
            filtered_data = data_df_1[
                                (data_df_1['Date']>=prev_expiry)
                                & (data_df_1['Date']<curr_expiry)
                            ].sort_values(by='Date').reset_index(drop=True)
            first_instance = True
        
        elif(first_instance):
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
                        or ((spot_adjustment_type==2) and (roc<=(-spot_adjustment)))
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

            if fromDate==toDate:
                continue
    
            is_base_start = (
                                (base2_df['Start'] == fromDate)
                            ).any()
            
            if is_base_start:
                fut_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>fromDate].iloc[1]['Current Expiry']
            if t_2:
                print(f"Call Sell Future Buy T-2 to T-2 Weekly From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            else:
                print(f"Call Sell Future Buy T-1 to T-1 Weekly From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            
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
                createLogFile("NIFTY", reason, prev_expiry, pd.NaT, fut_expiry, fromDate, toDate)
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

            if (call_sell_position>=0):
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
                
            
            if call_entry_data.empty:
                reason = f"Call Data for Strike Below {call_strike} and Turnover>0 not found"
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, fut_expiry, fromDate, toDate)
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
                if(call_entry_data.empty):
                    reason = f"Call Entry Data missing for Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Call Exit Data missing for Call Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, fut_expiry, fromDate, toDate)
                continue
        
            call_entry_price = call_entry_data.iloc[0]['Close']
            call_entry_turnover = call_entry_data.iloc[0]['TurnOver']
            call_exit_price = call_exit_data.iloc[0]['Close']
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
                    "Call Entry Turnover" : call_entry_turnover,
                    "Call ExitPrice" : call_exit_price,
                    "Call Exit Turnover": call_exit_turnover,
                    "Call P&L" : call_net,

                    "Net P&L" : total_net,
                    
                })
        
      

    if analysis_data:
        if t_2:
            path = "./Output/CE_Sell_Fut_Buy/Weekly/T-2 To T-2"
        else:
            path = "./Output/CE_Sell_Fut_Buy/Weekly/T-1 To T-1"
        

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
        
        os.makedirs(path, exist_ok=True)

        if t_2:
            fileName = fileName + "_Weekly_Expiry_T-2_To_T-2"
        else:
            fileName = fileName + "_Weekly_Expiry_T-1_To_T-1"

        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")

    if logFile:
        if t_2:
            path = "./Output/CE_Sell_Fut_Buy/Weekly/T-2 To T-2"
        else:
            path = "./Output/CE_Sell_Fut_Buy/Weekly/T-1 To T-1"
        

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
        
        os.makedirs(path, exist_ok=True)
        
        if t_2:
            fileName = fileName + "_Weekly_Expiry_T-2_To_T-2"        
        else:
            fileName = fileName + "_Weekly_Expiry_T-1_To_T-1"
        

        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()

# Monthly Expiry to Expiry
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


# Monthly T-1 to T-1
def main4(spot_adjustment_type=0, spot_adjustment=1, call_sell_position=0):
    data_df = getStrikeData("NIFTY")
    data_df1 = data_df.copy(deep=True)
    
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
    # monthly_expiry_df = monthly_expiry_df[
    #                             (monthly_expiry_df['Current Expiry']>=pd.Timestamp(2019,2,1))
    #                                       ].reset_index(drop=True)
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
    
    monthly_expiry_df = pd.DataFrame(monthly_expiry_list, 
                                        columns=['Previous Expiry', 'Current Expiry', 'Next Expiry'])
    
    analysis_data, first_instance = [], False

    for w in range(0, len(monthly_expiry_df)):
        prev_expiry = monthly_expiry_df.iloc[w]['Previous Expiry']
        curr_expiry = monthly_expiry_df.iloc[w]['Current Expiry']
        next_expiry = monthly_expiry_df.iloc[w]['Next Expiry']
        fut_expiry = curr_expiry

        filtered_data = data_df[
                            (data_df['Date']>=prev_expiry)
                            & (data_df['Date']<=curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)

        if(len(filtered_data)<2):
            continue
        
        if not first_instance:
            filtered_data = data_df[
                            (data_df['Date']>=prev_expiry)
                            & (data_df['Date']<curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)
            first_instance = True
        
        elif first_instance:
            prev_date = data_df1[data_df1['Date']<prev_expiry]
            if (prev_date.empty):
                prev_date = prev_expiry
            else:
                prev_date = prev_date.iloc[-1]['Date']

            filtered_data = data_df[
                            (data_df['Date']>=prev_date)
                            & (data_df['Date']<curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)
        
        if(len(filtered_data)<2):
            continue
        
        interval, interval_df = [], pd.DataFrame()
        interval.append((filtered_data.iloc[0]['Date'], filtered_data.iloc[-1]['Date'], curr_expiry))
        interval_df = pd.DataFrame(interval, columns=['From', 'To', 'Expiry'])
        
        base_ends = base2_df.loc[
            (base2_df['End'] > curr_expiry) & (base2_df['End'] < next_expiry),
            'End'
        ].sort_values()
        #  and not valid_base.empty
        if not base_ends.empty:
            interval.append((filtered_data.iloc[-1]['Date'], base_ends.max(), next_expiry))
        
        interval_df = pd.DataFrame(interval, columns=['From', 'To', 'Expiry'])
        
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

        filtered_data = data_df[
                            (data_df['Date']>=interval_df['From'].min())
                            & (data_df['Date']<=interval_df['To'].max())
                        ].sort_values(by='Date').reset_index(drop=True)
        
        
        if(len(filtered_data)<2):
            continue

        if spot_adjustment_type!=0:
            intervals = []
            for _, int_row in interval_df.iterrows():
                temp_filtered_data1 = pd.DataFrame()
                
                int_start = int_row['From']
                int_end   = int_row['To']
                expiry    = int_row['Expiry']
            
                temp_filtered_data1 = filtered_data[
                    (filtered_data['Date'] >= int_start) &
                    (filtered_data['Date'] <= int_end)
                ].reset_index(drop=True).copy(deep=True)
                
                if(len(temp_filtered_data1)<2):
                    continue

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
            
            if fromDate==toDate:
                continue
            
            print(f"Call Sell Future Buy Monthly Expiry T-1 To T-1 - From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
    
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
            bhav_df1, bhav_df2   = pd.DataFrame(), pd.DataFrame()
            call_entry_price, call_exit_price = None, None
            call_entry_turnover, call_exit_turnover = None, None
            fut_entry_price, fut_exit_price = None, None
            call_net, fut_net, total_net = None, None, None

            try:
                bhav_df1 = pd.read_csv(f"./cleaned_csvs/{fileName1}")
            except:
                reason = f"{fileName1} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, prev_expiry, pd.NaT, curr_expiry, fromDate, toDate)
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
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, curr_expiry, fromDate, toDate)
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
                if(call_entry_data.empty):
                    reason = f"Call Entry Data missing for Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Call Exit Data missing for Call Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, curr_expiry, fromDate, toDate)
                continue
        
            call_entry_price = call_entry_data.iloc[0]['Close']
            call_entry_turnover = call_entry_data.iloc[0]['TurnOver']
            call_exit_price = call_exit_data.iloc[0]['Close']
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
                    reason = f"Future Entry Data missing for Expiry {curr_expiry}"
                else:
                    reason = f"Future Exit Data missing for Expiry {curr_expiry}"
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, curr_expiry, fromDate, toDate)
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
                    "Call Entry Turnover" : call_entry_turnover,
                    "Call ExitPrice" : call_exit_price,
                    "Call Exit Turnover": call_exit_turnover,
                    "Call P&L" : call_net,

                    "Net P&L" : total_net,
                    
                })
        
      
    if analysis_data:
        path = "./Output/CE_Sell_Fut_Buy/Monthly/T-1 To T-1"
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
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Monthly_Expiry_T-1_To_T-1"
        
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df = analyse_df.drop_duplicates(subset=['Entry Date', 'Exit Date']).reset_index(drop=True)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")

    if logFile:
        path = "./Output/CE_Sell_Fut_Buy/Monthly/T-1 To T-1"
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
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Monthly_Expiry_T-1_To_T-1"
        fileName = fileName + "_Log"

        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


# Synthetic Level and Value From Dates
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


# Summary For CE+FUT
def create_summary_idx(df):
    entrySpot = df.iloc[0]['Entry Spot']
    first_entry_date = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d').min()
    last_exit_date = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d').max()
    number_of_years = (last_exit_date - first_entry_date).days / 365.25
    groups = {
        "Total Trades"  :   df,
    }
    stats_rows = []
    for label, subset in groups.items():
        count = len(subset)  
        total_sum = subset['Net P&L'].sum() if count>0 else None
        avg = (total_sum / count).round(2) if count > 0 else None
        
        win = len(subset[subset['Net P&L']>0]) if count>0 else None
        winPct = round((win/count * 100),2) if not pd.isna(win) else None
        avg_win = subset[subset['Net P&L']>0]['Net P&L'].mean() if not pd.isna(win) else None
        avg_win_pct = round(100*(avg_win/total_sum),2) if not pd.isna(win) else None
        avg_win = round(avg_win, 2) if not pd.isna(avg_win) else None
        
        lose = len(subset[subset['Net P&L']<0]) if count>0 else None
        losePct = round((lose/count * 100),2) if not pd.isna(lose) else None
        avg_loss = subset[subset['Net P&L']<0]['Net P&L'].mean() if not pd.isna(lose) else None
        avg_loss_pct = round(100*(avg_loss/total_sum),2) if  not pd.isna(lose) else None
        avg_loss = round(avg_loss, 2) if not pd.isna(avg_loss) else None

        expectancy = round(( ((avg_win_pct / abs(avg_loss_pct) ) * winPct) - losePct)/100, 2) if not pd.isna(win) and not pd.isna(lose) else None

        if count>0 and ((total_sum + entrySpot)/entrySpot) > 0:
            cagr_options = round(
                100 * (((total_sum + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (total_sum + entrySpot) > 0 else 0
        else:
            cagr_options = 0

        dd = subset['%DD'].min().round(2) if count>0 else None
        dd_points = subset['DD'].min().round(2) if count>0 else None
        Car_MDD = round(cagr_options/abs(dd), 2)
        recovery_factor = round(total_sum/abs(dd_points), 2)

        spot_chg = subset['Spot P&L'].sum()
        roi_vs_spot = round(100*(total_sum/spot_chg), 2) if spot_chg!=0 else None
        
        if count>0 and ((spot_chg + entrySpot) / entrySpot)>0:
            cagr_spot = round(
                100 * (((spot_chg + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (spot_chg + entrySpot) > 0 else 0

        else:
            cagr_spot = 0

        stats_rows.append([
                        label, count, total_sum, avg, 
                        winPct, avg_win, losePct, avg_loss, 
                        expectancy, cagr_options, 
                        dd, spot_chg, roi_vs_spot, 
                        cagr_spot, dd_points, Car_MDD,
                        recovery_factor
        ])
        
    stats_df = pd.DataFrame(stats_rows, columns=[
                                    "Category", "Count", "Sum", "Avg", 
                                    "W%", "Avg(W)", "L%", "Avg(L)",
                                    "Expectancy", "CAGR(Options)",
                                    "DD", "Spot Change", "ROI vs Spot",
                                    "CAGR(Spot)", "DD(Points)", "CAR/MDD",
                                    "Recovery Factor"

                                ])

    
    total_df = pd.DataFrame([
        ["Spot P&L", df["Spot P&L"].sum().round(2)],
        ["Fut -P&L", df["Future P&L"].sum().round(2)],
        ["CE P&L", df["Call P&L"].sum().round(2)],
        ["CE+Fut P&L", df["Net P&L"].sum().round(2)],
        ["CE+Fut+Spot P&L", (df["Net P&L"].sum() + df["Spot P&L"].sum()).round(2)],

    ], columns=["Type", "Sum"])

    return stats_df, total_df


def getPivotTable(df):
    filtered_df = df[['Future Expiry', 'Net P&L']].copy(deep=True)
    header = ["Sum of Net P&L", "Total Points"]

    if filtered_df.empty:
        return pd.DataFrame(), [], pd.DataFrame(), []
    
    filtered_df['Month'] = pd.to_datetime(filtered_df['Future Expiry'], format='%Y-%m-%d').dt.strftime("%b")
    filtered_df['Year'] = pd.to_datetime(filtered_df['Future Expiry'], format='%Y-%m-%d').dt.year
    
    pivot_table = filtered_df.pivot_table(
        values = filtered_df.columns[1],  
        index = 'Year',  
        columns = 'Month', 
        aggfunc = 'sum'
    )
    month_order = [m for m in ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'] if m in pivot_table.columns]
    pivot_table = pivot_table[month_order]
    grand_total = ['Grand Total'] + [pivot_table[col].sum().round(2) for col in month_order]
    grand_total_df = pd.DataFrame([grand_total], columns=['Year'] + month_order)
    pivot_table = pd.concat([pivot_table, grand_total_df.set_index('Year')])
    pivot_table['Grand Total'] = pivot_table[month_order].sum(axis=1).round(2)
    pivot_table.reset_index(inplace=True)

    return pivot_table, header


def save_hypothetical_and_summary_idx(df, filename="./df_final.xlsx"):
    stats_df, total_df = create_summary_idx(df)
    pivot_table, header = getPivotTable(df)
    
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df['Entry Date'] = df['Entry Date'].dt.date
        df['Exit Date'] = df['Exit Date'].dt.date
        df['Call Expiry'] = df['Call Expiry'].dt.date
        df['Future Expiry'] = df['Future Expiry'].dt.date
        df.to_excel(writer, sheet_name="Hypothetical TradeSheet", index=False)

        start_row = 0
        for table in [stats_df, total_df]:
            table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row)
            start_row += len(table) + 2  
        start_row = start_row + 1

        header_df = pd.DataFrame([header])
        header_df.to_excel(writer, sheet_name="Summary", index=False, header=False, startrow=start_row)
        pivot_table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row+2)
        start_row += len(header_df) + len(pivot_table) + 3 


def summary():
    main_path = "./Analysis/Data"
    all_folders = os.listdir(main_path)
    all_folders = [
        f for f in os.listdir(main_path)
        if os.path.isdir(os.path.join(main_path, f))
    ]

    for folder in all_folders:
        main_folders = os.listdir(os.path.join(main_path, folder))
        
        for f in main_folders:
            files = glob.glob(os.path.join(main_path, folder,f, "*.csv"), recursive=True)
            filtered_files = [
                    f for f in files 
                    if "log" not in f.lower() and "~$" not in f and "summary" not in f.lower()
                ]
            for file in filtered_files: 
                print(folder, file)
                df = pd.read_csv(file)
                try:
                    df['Future Expiry'] = pd.to_datetime(df['Future Expiry'], format='%Y-%m-%d')
                except:
                    df['Future Expiry'] = pd.to_datetime(df['Future Expiry'], format='%d-%m-%Y')

                try:
                    df['Call Expiry'] = pd.to_datetime(df['Call Expiry'], format='%Y-%m-%d')
                except:
                    df['Call Expiry'] = pd.to_datetime(df['Call Expiry'], format='%d-%m-%Y')

                try:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d')
                except:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%d-%m-%Y')
                    
                try:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d')
                except:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%d-%m-%Y')

                df = df[
                        (df['Call Expiry']>pd.Timestamp(2019,2,1))
                    ].sort_values(by=['Entry Date', 'Exit Date']).reset_index(drop=True)
                
                df['Hypothetical Call ExitPrice'] = np.where(
                                                df['Exit Date'] == df['Call Expiry'],
                                                    np.where(
                                                        df['Exit Spot'] < df['Call Strike'], 
                                                        0,                              
                                                        df['Exit Spot'] - df['Call Strike']  
                                                    ),
                                                df['Call ExitPrice']
                                            )
                df['Call P&L'] = df['Call EntryPrice'] - df['Hypothetical Call ExitPrice']
                df['Call P&L'] = df['Call P&L'].round(2)
                df.drop(columns=['Call ExitPrice'], inplace=True)
                
                df['Spot P&L'] = df['Exit Spot'] - df['Entry Spot']
                df['Net P&L'] = df['Future P&L'] + df['Call P&L']   
                df['Net P&L/Spot Pct'] = round((df['Net P&L']/df['Entry Spot'])*100, 2)
                
                df['Cumulative'] = None
                df.at[0, 'Cumulative'] = df.iloc[0]['Entry Spot'] + df.iloc[0]['Net P&L']
                for i in range(1, len(df)):
                    df.at[i, 'Cumulative'] = df.at[i-1, 'Cumulative'] + df.at[i, 'Net P&L']

                df['Peak'] = df['Cumulative'].cummax()
                df['DD'] = np.where(df['Peak']>df['Cumulative'], df['Cumulative']-df['Peak'], 0)
                df['Peak'] = df['Peak'].astype(float)
                df['DD'] = df['DD'].astype(float)
                df['%DD'] = np.where(df['DD']==0, 0, round(100*(df['DD']/df['Peak']),2))
                df['%DD'] = df['%DD'].round(2)
                
                df = df[[

                        'Entry Date', 'Exit Date', 
                        'Entry Spot', 'Exit Spot', 
                        'Spot P&L', 

                        'Future Expiry',
                        'Future EntryPrice', 'Future ExitPrice', 
                        'Future P&L',  
                        
                        'Call Expiry', 'Call Strike', 
                        'Call Entry Turnover', 'Call EntryPrice', 
                        'Call Exit Turnover', 'Hypothetical Call ExitPrice', 
                        'Call P&L', 

                        'Net P&L', 'Net P&L/Spot Pct',
                        
                        'Cumulative', 'Peak', 'DD', '%DD'
                    ]]
                file = file.split(".csv")[0]
                file = file + "_Final_Summary" + ".xlsx"
                save_hypothetical_and_summary_idx(df, file)
                

# # Short During Base2 Bear # #
# # Future Short Put Short # #
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


def main2_V2(spot_adjustment_type=0, spot_adjustment = 1, put_sell_position = 0, t_2=False):
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
    data_df_1 = data_df[mask].reset_index(drop=True).copy(deep=True)

    weekly_expiry_df = pd.read_csv(f"./expiryData/NIFTY.csv")
    weekly_expiry_df['Previous Expiry'] = pd.to_datetime(weekly_expiry_df['Previous Expiry'], format='%Y-%m-%d')
    weekly_expiry_df['Current Expiry'] = pd.to_datetime(weekly_expiry_df['Current Expiry'], format='%Y-%m-%d')
    weekly_expiry_df['Next Expiry'] = pd.to_datetime(weekly_expiry_df['Next Expiry'], format='%Y-%m-%d')
    
    monthly_expiry_df = pd.read_csv(f"./expiryData/NIFTY_Monthly.csv")
    monthly_expiry_df['Previous Expiry'] = pd.to_datetime(monthly_expiry_df['Previous Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Current Expiry'] = pd.to_datetime(monthly_expiry_df['Current Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Next Expiry'] = pd.to_datetime(monthly_expiry_df['Next Expiry'], format='%Y-%m-%d')
    
    
    first_instance, analysis_data = False, []
    for w in range(0, len(weekly_expiry_df)):
        prev_expiry = weekly_expiry_df.iloc[w]['Previous Expiry']
        curr_expiry = weekly_expiry_df.iloc[w]['Current Expiry']
    
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
                            ].sort_values(by='Date').reset_index(drop=True)
        
        if(len(filtered_data)<2):
            continue

        if not first_instance:
            filtered_data = data_df_1[
                                (data_df_1['Date']>=prev_expiry)
                                & (data_df_1['Date']<curr_expiry)
                            ].sort_values(by='Date').reset_index(drop=True)
            first_instance = True
        elif(first_instance):
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
                        or ((spot_adjustment_type==2) and (roc<=(-spot_adjustment)))
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

            if fromDate==toDate:
                continue
    
            print(f"Put Sell Future Sell Weekly T-1 to T-1 From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
    
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

            bhav_df1['Date'] = pd.to_datetime(bhav_df1['Date'], format='%Y-%m-%d')
            bhav_df1['ExpiryDate'] = pd.to_datetime(bhav_df1['ExpiryDate'], format='%Y-%m-%d')

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

            if put_entry_data.empty:
                reason = f"Put Entry Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, fut_expiry, fromDate, toDate)
                continue

            put_strike = put_entry_data.iloc[0]['StrikePrice']
            put_entry_data = put_entry_data[(put_entry_data['StrikePrice']==put_strike)]
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
                            ]
            
            if put_entry_data.empty or put_exit_data.empty:
                if(put_entry_data.empty):
                    reason = f"Put Entry Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Put Exit Data missing for Call Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, fut_expiry, fromDate, toDate)
                continue
        
            put_entry_price = put_entry_data.iloc[0]['Close']
            put_entry_turnover = put_entry_data.iloc[0]['TurnOver']
            put_exit_price = put_exit_data.iloc[0]['Close']
            put_exit_turnover = put_exit_data.iloc[0]['TurnOver']
            put_net =  round(put_entry_price -  put_exit_price, 2)
        
        
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
                    "Put Entry Turnover" : put_entry_turnover,
                    "Put ExitPrice" : put_exit_price,
                    "Put Exit Turnover": put_exit_turnover,
                    "Put P&L" : put_net,

                    "Net P&L" : total_net,
                    
                })
        
      

    if analysis_data:
        if t_2:
            path = "./Output/PE_Sell_Future_Sell/Weekly/T-2 To T-2"
        else:
            path = "./Output/PE_Sell_Future_Sell/Weekly/T-1 To T-1"
        
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
        
        

        if t_2:
            fileName = fileName + "_Weekly_Expiry_T-2_To_T-2"
        else:
            fileName = fileName + "_Weekly_Expiry_T-1_To_T-1"
   
        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")

    if logFile:
        if t_2:
            path = "./Output/PE_Sell_Future_Sell/Weekly/T-2 To T-2"
        else:
            path = "./Output/PE_Sell_Future_Sell/Weekly/T-1 To T-1"
        
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
        
        

        if t_2:
            fileName = fileName + "_Weekly_Expiry_T-2_To_T-2"
        else:
            fileName = fileName + "_Weekly_Expiry_T-1_To_T-1"
        fileName = fileName +"_Log"
        os.makedirs(path, exist_ok=True)
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


def main3_V2(spot_adjustment_type=0, spot_adjustment=1, put_sell_position=0):
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
    
    data_df = data_df[mask].reset_index(drop=True)

    monthly_expiry_df = pd.read_csv(f"./expiryData/NIFTY_Monthly.csv")
    monthly_expiry_df['Previous Expiry'] = pd.to_datetime(monthly_expiry_df['Previous Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Current Expiry'] = pd.to_datetime(monthly_expiry_df['Current Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Next Expiry'] = pd.to_datetime(monthly_expiry_df['Next Expiry'], format='%Y-%m-%d')
 

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
        interval_df = pd.DataFrame(intervals, columns=['From', 'To', 'Expiry'])

        

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
            print(f"Put Sell Future Sell Monthly Expiry To Expiry - From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            
            if(fromDate==toDate):
                continue

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

            # Put Data
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
                reason = f"No Strike Found above {put_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, fut_expiry, fromDate, toDate)
                continue

            put_strike = put_entry_data.iloc[0]['StrikePrice']
            put_entry_data = put_entry_data[
                                (put_entry_data['StrikePrice']==put_strike)
                            ]
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
                            ]
            
            if put_entry_data.empty or put_exit_data.empty:
                if(put_entry_data.empty):
                    reason = f"Put Entry Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Put Exit Data missing for Call Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
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
        path = "./Output/PE_Sell_Future_Sell/Monthly/Expiry To Expiry"
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
        
        
        fileName = fileName + "_Monthly_Expiry-To-Expiry"
        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df = analyse_df.drop_duplicates(subset=['Entry Date', 'Exit Date'])
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")

    if logFile:
        path = "./Output/PE_Sell_Future_Sell/Monthly/Expiry To Expiry"
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
        
        
        fileName = fileName + "_Monthly_Expiry-To-Expiry_Log"
        os.makedirs(path, exist_ok=True)
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


def main4_V2(spot_adjustment_type=0, spot_adjustment = 1, put_sell_position = 0):
    data_df = getStrikeData("NIFTY")
    data_df1 = data_df.copy(deep=True)
    
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
    
    data_df = data_df[mask].reset_index(drop=True)

    monthly_expiry_df = pd.read_csv(f"./expiryData/NIFTY_Monthly.csv")
    monthly_expiry_df['Previous Expiry'] = pd.to_datetime(monthly_expiry_df['Previous Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Current Expiry'] = pd.to_datetime(monthly_expiry_df['Current Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Next Expiry'] = pd.to_datetime(monthly_expiry_df['Next Expiry'], format='%Y-%m-%d')
    

    analysis_data, first_instance = [], False
    for w in range(0, len(monthly_expiry_df)):
        prev_expiry = monthly_expiry_df.iloc[w]['Previous Expiry']
        curr_expiry = monthly_expiry_df.iloc[w]['Current Expiry']
        next_expiry = monthly_expiry_df.iloc[w]['Next Expiry']
        fut_expiry = curr_expiry

        filtered_data = data_df[
                            (data_df['Date']>=prev_expiry)
                            & (data_df['Date']<=curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)

        if(len(filtered_data)<2):
            continue
        
        if not first_instance:
            filtered_data = data_df[
                            (data_df['Date']>=prev_expiry)
                            & (data_df['Date']<curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)
            first_instance = True
        
        elif first_instance:
            prev_date = data_df1[data_df1['Date']<prev_expiry]
            if (prev_date.empty):
                prev_date = prev_expiry
            else:
                prev_date = prev_date.iloc[-1]['Date']

            filtered_data = data_df[
                            (data_df['Date']>=prev_date)
                            & (data_df['Date']<curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)
        
        if(len(filtered_data)<2):
            continue
        
        
        interval, interval_df = [], pd.DataFrame()
        interval.append((filtered_data.iloc[0]['Date'], filtered_data.iloc[-1]['Date'], curr_expiry))
        interval_df = pd.DataFrame(interval, columns=['From', 'To', 'Expiry'])
       

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

        filtered_data = data_df[
                            (data_df['Date']>=interval_df['From'].min())
                            & (data_df['Date']<=interval_df['To'].max())
                        ].sort_values(by='Date').reset_index(drop=True)
        
        
        if(len(filtered_data)<2):
            continue


        if spot_adjustment_type!=0:
            intervals = []
            for _, int_row in interval_df.iterrows():
                temp_filtered_data1 = pd.DataFrame()
                
                int_start = int_row['From']
                int_end   = int_row['To']
                expiry    = int_row['Expiry']
            
                temp_filtered_data1 = filtered_data[
                    (filtered_data['Date'] >= int_start) &
                    (filtered_data['Date'] <= int_end)
                ].reset_index(drop=True).copy(deep=True)
                
                if(len(temp_filtered_data1)<2):
                    continue

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
            
            if fromDate==toDate:
                continue
            
            print(f"Put Sell Future Sell Monthly Expiry T-1 To T-1 - From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
    
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
            bhav_df1, bhav_df2   = pd.DataFrame(), pd.DataFrame()
            put_entry_price, put_exit_price = None, None
            put_entry_turnover, put_exit_turnover = None, None
            fut_entry_price, fut_exit_price = None, None
            put_net, fut_net, total_net = None, None, None

            try:
                bhav_df1 = pd.read_csv(f"./cleaned_csvs/{fileName1}")
            except:
                reason = f"{fileName1} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, curr_expiry, fromDate, toDate)
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
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, curr_expiry, fromDate, toDate)
                continue
            try:
                bhav_df2['Date'] = pd.to_datetime(bhav_df2['Date'], format='%Y-%m-%d')
            except:
                bhav_df2['Date'] = pd.to_datetime(bhav_df2['Date'], format='%d-%m-%Y')
            try:
                bhav_df2['ExpiryDate'] = pd.to_datetime(bhav_df2['ExpiryDate'], format='%Y-%m-%d')
            except:
                bhav_df2['ExpiryDate'] = pd.to_datetime(bhav_df2['ExpiryDate'], format='%d-%m-%Y')

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
                reason = f"Put Data above {put_strike} and Turnober>0 not found"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, curr_expiry, fromDate, toDate)
                continue
            
            put_strike = put_entry_data.iloc[0]['StrikePrice']
            put_entry_data = put_entry_data[(put_entry_data['StrikePrice']==put_strike)]
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
                            ]
           
            if put_entry_data.empty or put_exit_data.empty:
                if(put_entry_data.empty):
                    reason = f"Put Entry Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Put Exit Data missing for Call Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, curr_expiry, fromDate, toDate)
                continue
        
            put_entry_price = put_entry_data.iloc[0]['Close']
            put_entry_turnover = put_entry_data.iloc[0]['TurnOver']
            put_exit_price = put_exit_data.iloc[0]['Close']
            put_exit_turnover = put_exit_data.iloc[0]['TurnOver']
            put_net =  round(put_entry_price -  put_exit_price, 2)
        
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
                    reason = f"Future Entry Data missing for Expiry {curr_expiry}"
                else:
                    reason = f"Future Exit Data missing for Expiry {curr_expiry}"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, curr_expiry, fromDate, toDate)
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
                    "Put Entry Turnover" : put_entry_turnover,
                    "Put ExitPrice" : put_exit_price,
                    "Put Exit Turnover": put_exit_turnover,
                    "Put P&L" : put_net,

                    "Net P&L" : total_net,
                    
                })
        
      
    if analysis_data:
        path = "./Output/PE_Sell_Future_Sell/Monthly/T-1 To T-1"
        
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

        
        fileName = fileName + "_Monthly_Expiry_T-1_To-T-1"
        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df = analyse_df.drop_duplicates(subset=['Entry Date', 'Exit Date']).reset_index(drop=True)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        path = "./Output/PE_Sell_Future_Sell/Monthly/T-1 To T-1"
        
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

        
        fileName = fileName + "_Monthly_Expiry_T-1_To-T-1_Log"
        os.makedirs(path, exist_ok=True)
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


# Summary For PE+FUT
def create_summary_idx_V2(df):
    entrySpot = df.iloc[0]['Entry Spot']
    first_entry_date = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d').min()
    last_exit_date = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d').max()
    number_of_years = (last_exit_date - first_entry_date).days / 365.25
    groups = {
        "Total Trades"  :   df,
    }

    stats_rows = []
    for label, subset in groups.items():
        count = len(subset)  
        total_sum = subset['Net P&L'].sum() if count>0 else None
        avg = (total_sum / count).round(2) if count > 0 else None
        
        win = len(subset[subset['Net P&L']>0]) if count>0 else None
        winPct = round((win/count * 100),2) if not pd.isna(win) else None
        avg_win = subset[subset['Net P&L']>0]['Net P&L'].mean() if not pd.isna(win) else None
        avg_win_pct = round(100*(avg_win/total_sum),2) if not pd.isna(win) else None
        avg_win = round(avg_win, 2) if not pd.isna(avg_win) else None
        
        lose = len(subset[subset['Net P&L']<0]) if count>0 else None
        losePct = round((lose/count * 100),2) if not pd.isna(lose) else None
        avg_loss = subset[subset['Net P&L']<0]['Net P&L'].mean() if not pd.isna(lose) else None
        avg_loss_pct = round(100*(avg_loss/total_sum),2) if  not pd.isna(lose) else None
        avg_loss = round(avg_loss, 2) if not pd.isna(avg_loss) else None

        expectancy = round(( ((avg_win_pct / abs(avg_loss_pct) ) * winPct) - losePct)/100, 2) if not pd.isna(win) and not pd.isna(lose) else None

        if count>0 and ((total_sum + entrySpot)/entrySpot) > 0:
            cagr_options = round(
                100 * (((total_sum + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (total_sum + entrySpot) > 0 else 0
        else:
            cagr_options = 0

        dd = subset['%DD'].min().round(2) if count>0 else None
        dd_points = subset['DD'].min().round(2) if count>0 else None
        Car_MDD = round(cagr_options/abs(dd), 2)
        recovery_factor = round(total_sum/abs(dd_points), 2)

        spot_chg = subset['Spot P&L'].sum()
        roi_vs_spot = round(100*(total_sum/spot_chg), 2) if spot_chg!=0 else None
        
        if count>0 and ((spot_chg + entrySpot) / entrySpot)>0:
            cagr_spot = round(
                100 * (((spot_chg + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (spot_chg + entrySpot) > 0 else 0

        else:
            cagr_spot = 0

        stats_rows.append([
                        label, count, total_sum, avg, 
                        winPct, avg_win, losePct, avg_loss, 
                        expectancy, cagr_options, 
                        dd, spot_chg, roi_vs_spot, 
                        cagr_spot, dd_points, Car_MDD,
                        recovery_factor
        ])
        
    stats_df = pd.DataFrame(stats_rows, columns=[
                                    "Category", "Count", "Sum", "Avg", 
                                    "W%", "Avg(W)", "L%", "Avg(L)",
                                    "Expectancy", "CAGR(Options)",
                                    "DD", "Spot Change", "ROI vs Spot",
                                    "CAGR(Spot)", "DD(Points)", "CAR/MDD",
                                    "Recovery Factor"

                                ])

    
    total_df = pd.DataFrame([
        ["Spot P&L", df["Spot P&L"].sum().round(2)],
        ["Fut P&L", df["Future P&L"].sum().round(2)],
        ["PE P&L", df["Put P&L"].sum().round(2)],
        ["PE+Fut P&L", df["Net P&L"].sum().round(2)],
        ["PE+Fut+Spot P&L", (df["Net P&L"].sum() + df["Spot P&L"].sum()).round(2)],

    ], columns=["Type", "Sum"])

    return stats_df, total_df


def getPivotTable_V2(df):
    filtered_df = df[['Future Expiry', 'Net P&L']].copy(deep=True)
    header = ["Sum of Net P&L", "Total Points"]

    if filtered_df.empty:
        return pd.DataFrame(), [], pd.DataFrame(), []
    
    filtered_df['Month'] = pd.to_datetime(filtered_df['Future Expiry'], format='%Y-%m-%d').dt.strftime("%b")
    filtered_df['Year'] = pd.to_datetime(filtered_df['Future Expiry'], format='%Y-%m-%d').dt.year
    
    pivot_table = filtered_df.pivot_table(
        values = filtered_df.columns[1],  
        index = 'Year',  
        columns = 'Month', 
        aggfunc = 'sum'
    )
    
    month_order = [m for m in ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'] if m in pivot_table.columns]
    pivot_table = pivot_table[month_order]
    grand_total = ['Grand Total'] + [pivot_table[col].sum().round(2) for col in month_order]
    
    grand_total_df = pd.DataFrame([grand_total], columns=['Year'] + month_order)
    pivot_table = pd.concat([pivot_table, grand_total_df.set_index('Year')])
    pivot_table['Grand Total'] = pivot_table[month_order].sum(axis=1).round(2)
    pivot_table.reset_index(inplace=True)

    return pivot_table, header


def save_hypothetical_and_summary_idx_V2(df, filename="./df_final.xlsx"):
    stats_df, total_df = create_summary_idx_V2(df)
    pivot_table, header = getPivotTable_V2(df)
    
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df['Entry Date'] = df['Entry Date'].dt.date
        df['Exit Date'] = df['Exit Date'].dt.date
        df['Put Expiry'] = df['Put Expiry'].dt.date
        df['Future Expiry'] = df['Future Expiry'].dt.date
        df.to_excel(writer, sheet_name="Hypothetical TradeSheet", index=False)

        start_row = 0
        for table in [stats_df, total_df]:
            table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row)
            start_row += len(table) + 2  
        start_row = start_row + 1

        header_df = pd.DataFrame([header])
        header_df.to_excel(writer, sheet_name="Summary", index=False, header=False, startrow=start_row)
        pivot_table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row+2)
        start_row += len(header_df) + len(pivot_table) + 3 


def summary_V2():
    main_path = "./Analysis/Data"
    all_folders = os.listdir(main_path)
    all_folders = [
        f for f in os.listdir(main_path)
        if os.path.isdir(os.path.join(main_path, f))
    ]

    for folder in all_folders:
        main_folders = os.listdir(os.path.join(main_path, folder))
        
        for f in main_folders:
            files = glob.glob(os.path.join(main_path, folder,f, "*.csv"), recursive=True)
            filtered_files = [
                    f for f in files 
                    if "log" not in f.lower() and "~$" not in f and "summary" not in f.lower()
                ]
            for file in filtered_files: 
                print(folder, file)
                df = pd.read_csv(file)
                try:
                    df['Future Expiry'] = pd.to_datetime(df['Future Expiry'], format='%Y-%m-%d')
                except:
                    df['Future Expiry'] = pd.to_datetime(df['Future Expiry'], format='%d-%m-%Y')

                try:
                    df['Put Expiry'] = pd.to_datetime(df['Put Expiry'], format='%Y-%m-%d')
                except:
                    df['Put Expiry'] = pd.to_datetime(df['Put Expiry'], format='%d-%m-%Y')

                try:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d')
                except:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%d-%m-%Y')
                    
                try:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d')
                except:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%d-%m-%Y')

                df = df[
                        (df['Put Expiry']>pd.Timestamp(2019,2,1))
                    ].sort_values(by=['Entry Date', 'Exit Date']).reset_index(drop=True)
                
                df['Hypothetical Put ExitPrice'] = np.where(
                                                df['Exit Date'] == df['Put Expiry'],
                                                    np.where(
                                                        df['Exit Spot'] > df['Put Strike'], 
                                                        0,                              
                                                        df['Put Strike']- df['Exit Spot']   
                                                    ),
                                                df['Put ExitPrice']
                                            )
                df['Put P&L'] = df['Put EntryPrice'] - df['Hypothetical Put ExitPrice']
                df['Put P&L'] = df['Put P&L'].round(2)
                df.drop(columns=['Put ExitPrice'], inplace=True)
                
                df['Net P&L'] = df['Future P&L'] + df['Put P&L']
                df['Net P&L/Spot Pct'] = round((df['Net P&L']/df['Entry Spot'])*100, 2)
                df['Spot P&L'] = df['Entry Spot'] - df['Exit Spot']
                
                df['Cumulative'] = None
                df.at[0, 'Cumulative'] = df.iloc[0]['Entry Spot'] + df.iloc[0]['Net P&L']
                
                for i in range(1, len(df)):
                    df.at[i, 'Cumulative'] = df.at[i-1, 'Cumulative'] + df.at[i, 'Net P&L']

                df['Peak'] = df['Cumulative'].cummax()
                df['DD'] = np.where(df['Peak']>df['Cumulative'], df['Cumulative']-df['Peak'], 0)
                df['Peak'] = df['Peak'].astype(float)
                df['DD'] = df['DD'].astype(float)
                df['%DD'] = np.where(df['DD']==0, 0, round(100*(df['DD']/df['Peak']),2))
                df['%DD'] = df['%DD'].round(2)
                
                df = df[[

                        'Entry Date', 'Exit Date', 
                        'Entry Spot', 'Exit Spot', 
                        'Spot P&L', 

                        'Future Expiry',
                        'Future EntryPrice', 'Future ExitPrice', 
                        'Future P&L',  
                        
                        'Put Expiry', 'Put Strike', 
                        'Put Entry Turnover', 'Put EntryPrice', 
                        'Put Exit Turnover', 'Hypothetical Put ExitPrice', 
                        'Put P&L', 

                        'Net P&L', 'Net P&L/Spot Pct',
                        
                        'Cumulative', 'Peak', 'DD', '%DD'
                    ]]
                
                file = file.split(".csv")[0]
                file = file + "_Final_Summary" + ".xlsx"
                save_hypothetical_and_summary_idx_V2(df, file)
   


# Sell Call ITM and Buy Future
# Call Strike Based Adjustment vis-a-vis Spot (if Spot below Strike+0.3%)
# Base2 Bull
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


def main2_V3(call_sell_position=0, pct_diff=0.3, t_2=False):
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
    first_instance = False
    
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
                            ].sort_values(by='Date').reset_index(drop=True)
        
        if(len(filtered_data)<2):
            continue

        if not first_instance:
            filtered_data = data_df_1[
                                (data_df_1['Date']>=prev_expiry)
                                & (data_df_1['Date']<curr_expiry)
                            ].sort_values(by='Date').reset_index(drop=True)
            first_instance = True
        elif(first_instance):
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
        

        call_strike = round_half_up((filtered_data.iloc[0]['Close']*(1+(call_sell_position*0.01)))/100)*100
        strike_change_date = pd.NaT
        intervals = []
        
        for f in range(0, len(filtered_data)):
            f_row = filtered_data.iloc[f]
            curr_spot, curr_date = f_row['Close'], f_row['Date']
            target = round(call_strike * (1+(pct_diff*0.01)), 2)
            temp_next_expiry = weekly_expiry_df[weekly_expiry_df['Current Expiry']>=curr_date].iloc[0]['Next Expiry']
            
            if(
                (curr_spot<=target) 
                and (f!=0)
                and (f!=(len(filtered_data)-1))
            ):
                if (pd.isna(strike_change_date)):
                    intervals.append((filtered_data.iloc[0]['Date'], curr_date, call_expiry, call_strike))
                    call_strike = round_half_up((curr_spot*(1+(call_sell_position*0.01)))/100)*100
                    strike_change_date = curr_date
                    call_expiry = temp_next_expiry
                else:
                    intervals.append((strike_change_date, curr_date, call_expiry, call_strike))
                    call_strike = round_half_up((curr_spot*(1+(call_sell_position*0.01)))/100)*100
                    strike_change_date = curr_date
                    call_expiry = temp_next_expiry

            if f == len(filtered_data)-1:
                if pd.isna(strike_change_date):
                    intervals.append((filtered_data.iloc[0]['Date'], curr_date, call_expiry, call_strike))
                elif(strike_change_date!=curr_date):
                    intervals.append((strike_change_date, curr_date, call_expiry, call_strike))
        
        if intervals:
            interval_df = pd.DataFrame(intervals, columns=['From', 'To', 'Call Expiry', 'Call Strike'])


        for i in range(0, len(interval_df)):
            fileName1 = fileName2 = ""
            i_row = interval_df.iloc[i]
            fromDate, toDate = i_row['From'], i_row['To']
            call_expiry, call_strike = i_row['Call Expiry'], i_row['Call Strike']
            
            if fromDate==toDate:
                continue
    
            is_base_start = (
                                (base2_df['Start'] == fromDate)
                            ).any()
            
            if is_base_start:
                fut_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>fromDate].iloc[1]['Current Expiry']
            
            if t_2:
                print(f"Call Sell Future Buy T-2 to T-2 Weekly From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}  CE Strike Based Adjustment")
            else:
                print(f"Call Sell Future Buy T-1 to T-1 Weekly From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}  CE Strike Based Adjustment")
            
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
            
            
            if call_entry_data.empty:
                reason = f"Call Data for Strike Below {call_strike} and Turnover>0 not found"
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
                    reason = f"Call Entry Data missing for Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Call Exit Data missing for Call Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, call_expiry, pd.NaT, fut_expiry, fromDate, toDate)
                continue
        
            call_entry_price = call_entry_data.iloc[0]['Close']
            call_entry_turnover = call_entry_data.iloc[0]['TurnOver']
            call_exit_price = call_exit_data.iloc[0]['Close']
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
                    "Call Entry Turnover" : call_entry_turnover,
                    "Call ExitPrice" : call_exit_price,
                    "Call Exit Turnover": call_exit_turnover,
                    "Call P&L" : call_net,

                    "Net P&L" : total_net,        
                })
        
      
    if analysis_data:
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell_FUT_Buy"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell_FUT_Buy"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell_FUT_Buy"
        
        if t_2:
            path = "./Output/Call_Sell_Future_Buy/Weekly/T-2 To T-2/Strike Adjustment"    
            fileName = fileName + f"_Weekly_Expiry_T-2_To_T-2(With_{pct_diff}%CE_Strike_Based_Adjustment)"
        else:
            path = "./Output/Call_Sell_Future_Buy/Weekly/T-1 To T-1/Strike Adjustment"    
            fileName = fileName + f"_Weekly_Expiry_T-1_To_T-1(With_{pct_diff}%CE_Strike_Based_Adjustment)"
        
        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}/Weekly")

    if logFile:
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell_FUT_Buy"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell_FUT_Buy"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell_FUT_Buy"
        
        if t_2:
            path = "./Output/Call_Sell_Future_Buy/Weekly/T-2 To T-2/Strike Adjustment"    
            fileName = fileName + f"_Weekly_Expiry_T-2_To_T-2(With_{pct_diff}%CE_Strike_Based_Adjustment)"
        else:
            path = "./Output/Call_Sell_Future_Buy/Weekly/T-1 To T-1/Strike Adjustment"    
            fileName = fileName + f"_Weekly_Expiry_T-1_To_T-1(With_{pct_diff}%CE_Strike_Based_Adjustment)"
        
        fileName = fileName + "_Log"
        os.makedirs(path, exist_ok=True)
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


def main3_V3(call_sell_position=0, pct_diff=0.3):
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
            monthly_expiry_list.append((base2_df.loc[mask, 'Start'].min(), ne, monthly_expiry_df.at[m+1, 'Next Expiry']))
            m += 2
        else:
            monthly_expiry_list.append((pe, ce, ne))
            m += 1
    
    
    monthly_expiry_df = pd.DataFrame(
                            monthly_expiry_list, 
                            columns=['Previous Expiry', 'Current Expiry', 'Next Expiry'])
    monthly_expiry_df = monthly_expiry_df.sort_values(by='Current Expiry').reset_index(drop=True)
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
        

        # If base falls between from and to Date
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


        filtered_data = data_df[
                            (data_df['Date']>=interval_df['From'].min())
                            & (data_df['Date']<=interval_df['To'].max())
                        ].sort_values(by='Date').reset_index(drop=True)

        if(len(filtered_data)<2):
            continue
        
        
        intervals = []
        for i in range(0, len(interval_df)):
            i_df_row = interval_df.iloc[i]
            temp_curr_expiry = i_df_row['Expiry']
            temp_next_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>=temp_curr_expiry].iloc[0]['Next Expiry']

            temp_filtered_df = filtered_data[
                                    (filtered_data['Date']>=i_df_row['From'])
                                    & (filtered_data['Date']<=i_df_row['To'])
                                     ].reset_index(drop=True).copy(deep=True)
            if len(temp_filtered_df)<2:
                continue

            call_strike = round_half_up((temp_filtered_df.iloc[0]['Close']*(1+(call_sell_position*0.01)))/100)*100
            strike_change_date = pd.NaT
            
            for f in range(0, len(temp_filtered_df)):
                f_row = temp_filtered_df.iloc[f]
                curr_spot, curr_date = f_row['Close'], f_row['Date']    
                target = round(call_strike * (1+(pct_diff*0.01)), 2)
                
                if(f==0):
                    call_expiry = temp_curr_expiry
                
                if(
                    (curr_spot<=target) 
                    and (f!=0)
                    and (f!=(len(temp_filtered_df)-1))
                ):
                    if(pd.isna(strike_change_date)):
                        intervals.append((temp_filtered_df.iloc[0]['Date'], curr_date, call_expiry, call_strike))
                        call_strike = round_half_up((curr_spot*(1+(call_sell_position*0.01)))/100)*100
                        strike_change_date = curr_date
                        call_expiry = temp_next_expiry
                    else:
                        intervals.append((strike_change_date, curr_date, call_expiry, call_strike))
                        call_strike = round_half_up((curr_spot*(1+(call_sell_position*0.01)))/100)*100
                        strike_change_date = curr_date

                if f == len(temp_filtered_df)-1:
                    if pd.isna(strike_change_date):
                        intervals.append((temp_filtered_df.iloc[0]['Date'], curr_date, call_expiry, call_strike))
                    elif(strike_change_date!=curr_date):
                        intervals.append((strike_change_date, curr_date, call_expiry, call_strike))
            

        if intervals:
            interval_df = pd.DataFrame(intervals, columns=['From', 'To', 'Call Expiry', 'Call Strike'])
        
        for i in range(0, len(interval_df)):
            fileName1 = fileName2 = ""
            i_row = interval_df.iloc[i]

            fromDate, toDate = i_row['From'], i_row['To']
            if(fromDate==toDate):
                continue

            call_expiry, call_strike = i_row['Call Expiry'], i_row['Call Strike']
            fut_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>fromDate].iloc[0]['Current Expiry']
            
            if fut_expiry<toDate:
                fut_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>fromDate].iloc[0]['Next Expiry']
            
            print(f"Call Sell Future Buy Monthly Expiry To Expiry - From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')} CE Strike Based Adjustment")
            
            
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            if entrySpot.empty:
                continue  
            entrySpot = entrySpot.iloc[0]['Close']

            exitSpot =  filtered_data[filtered_data['Date']==toDate] 
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
            if call_sell_position>0:
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
            call_entry_data = call_entry_data[
                                (call_entry_data['StrikePrice']==call_strike)
                            ]
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
                    reason = f"Call Entry Data missing for Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Call Exit Data missing for Call Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
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
        path = "./Output/Call_Sell_Future_Buy/Monthly/Expiry To Expiry"
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell_FUT_Buy"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell_FUT_Buy"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell_FUT_Buy"

        fileName = fileName + f"_Monthly_Expiry-To-Expiry_With({pct_diff}%CE_Strike_Based_Adjustment)"
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df = analyse_df.drop_duplicates(subset=['Entry Date', 'Exit Date'])  
        os.makedirs(path, exist_ok=True)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}/Monthly")

    if logFile:
        path = "./Output/Call_Sell_Future_Buy/Monthly/Expiry To Expiry"
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell_FUT_Buy"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell_FUT_Buy"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell_FUT_Buy"
        
        fileName = fileName + f"_Monthly_Expiry-To-Expiry_With({pct_diff}%CE_Strike_Based_Adjustment)"
        fileName = fileName + "_Log"
        
        log_df = pd.DataFrame(logFile)
        os.makedirs(path, exist_ok=True)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


def main4_V3(call_sell_position=0, pct_diff=0.3):
    data_df = getStrikeData("NIFTY")
    data_df1 = data_df.copy(deep=True)
    
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
            monthly_expiry_list.append((base2_df.loc[mask, 'Start'].min(), ne, monthly_expiry_df.at[m+1, 'Next Expiry']))
            m += 2
        else:
            monthly_expiry_list.append((pe, ce, ne))
            m += 1
    
    monthly_expiry_df = pd.DataFrame(monthly_expiry_list, 
                                        columns=['Previous Expiry', 'Current Expiry', 'Next Expiry'])
    analysis_data, first_instance = [], False


    for w in range(0, len(monthly_expiry_df)):
        prev_expiry = monthly_expiry_df.iloc[w]['Previous Expiry']
        curr_expiry = monthly_expiry_df.iloc[w]['Current Expiry']
        next_expiry = monthly_expiry_df.iloc[w]['Next Expiry']
        fut_expiry = curr_expiry

        filtered_data = data_df[
                            (data_df['Date']>=prev_expiry)
                            & (data_df['Date']<=curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)

        if(len(filtered_data)<2):
            continue
        
        if not first_instance:
            filtered_data = data_df[
                            (data_df['Date']>=prev_expiry)
                            & (data_df['Date']<curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)
            first_instance = True
        
        elif first_instance:
            prev_date = data_df1[data_df1['Date']<prev_expiry]
            if (prev_date.empty):
                prev_date = prev_expiry
            else:
                prev_date = prev_date.iloc[-1]['Date']

            filtered_data = data_df[
                            (data_df['Date']>=prev_date)
                            & (data_df['Date']<curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)
        
        if(len(filtered_data)<2):
            continue
        
        interval, interval_df = [], pd.DataFrame()
        interval.append((filtered_data.iloc[0]['Date'], filtered_data.iloc[-1]['Date'], curr_expiry))
        interval_df = pd.DataFrame(interval, columns=['From', 'To', 'Expiry'])
        
        # If base ends falls between current and next expiry
        base_ends = base2_df.loc[
            (base2_df['End'] > curr_expiry) & (base2_df['End'] < next_expiry),
            'End'
        ].sort_values()
        if not base_ends.empty:
            interval.append((filtered_data.iloc[-1]['Date'], base_ends.max(), next_expiry))

        interval_df = pd.DataFrame(interval, columns=['From', 'To', 'Expiry'])
        
        # Check for Base End within from and to Date
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

        # Filter Data for Final
        filtered_data = data_df[
                            (data_df['Date']>=interval_df['From'].min())
                            & (data_df['Date']<=interval_df['To'].max())
                        ].sort_values(by='Date').reset_index(drop=True)
        
        
        if(len(filtered_data)<2):
            continue
        

        # CE Strike Based Adjustment
        intervals = []
        for i in range(0, len(interval_df)):
            i_df_row = interval_df.iloc[i]
            temp_curr_expiry = i_df_row['Expiry']
            # temp_next_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>=temp_curr_expiry].iloc[0]['Next Expiry']

            temp_filtered_df = filtered_data[
                                    (filtered_data['Date']>=i_df_row['From'])
                                    & (filtered_data['Date']<=i_df_row['To'])
                                     ].reset_index(drop=True).copy(deep=True)
            if len(temp_filtered_df)<2:
                continue

            call_strike = round_half_up((temp_filtered_df.iloc[0]['Close']*(1+(call_sell_position*0.01)))/100)*100
            strike_change_date = pd.NaT
            
            for f in range(0, len(temp_filtered_df)):
                f_row = temp_filtered_df.iloc[f]
                curr_spot, curr_date = f_row['Close'], f_row['Date']    
                target = round(call_strike * (1+(pct_diff*0.01)), 2)
                temp_next_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>=curr_date].iloc[0]['Next Expiry']

                if(f==0):
                    call_expiry = temp_curr_expiry
                
                if(
                    (curr_spot<=target) 
                    and (f!=0)
                    and (f!=(len(temp_filtered_df)-1))
                ):
                    if(pd.isna(strike_change_date)):
                        intervals.append((temp_filtered_df.iloc[0]['Date'], curr_date, call_expiry, call_strike))
                        call_strike = round_half_up((curr_spot*(1+(call_sell_position*0.01)))/100)*100
                        strike_change_date = curr_date
                        call_expiry = temp_next_expiry
                    else:
                        intervals.append((strike_change_date, curr_date, call_expiry, call_strike))
                        call_strike = round_half_up((curr_spot*(1+(call_sell_position*0.01)))/100)*100
                        strike_change_date = curr_date
                        call_expiry = temp_next_expiry

                if f == len(temp_filtered_df)-1:
                    if pd.isna(strike_change_date):
                        intervals.append((temp_filtered_df.iloc[0]['Date'], curr_date, call_expiry, call_strike))
                    elif(strike_change_date!=curr_date):
                        intervals.append((strike_change_date, curr_date, call_expiry, call_strike))
            

        if intervals:
            interval_df = pd.DataFrame(intervals, columns=['From', 'To', 'Call Expiry', 'Call Strike'])
    

        for i in range(0, len(interval_df)):
            fileName1 = fileName2 = ""
            i_row = interval_df.iloc[i]

            fromDate, toDate = i_row['From'], i_row['To']
            if(fromDate==toDate):
                continue

            call_expiry, call_strike = i_row['Call Expiry'], i_row['Call Strike']
            fut_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>fromDate].iloc[0]['Current Expiry']
            
            if fut_expiry<toDate:
                fut_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>fromDate].iloc[0]['Next Expiry']
            
          
            print(f"Call Sell Future Buy Monthly Expiry T-1 To T-1 - From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')} CE Strike Based Adjustment")
    
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
            bhav_df1, bhav_df2   = pd.DataFrame(), pd.DataFrame()
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
                reason = f"Call Data for Strike Below {call_strike} and TurnOver>0 not found"
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
            call_entry_turnover = call_entry_data.iloc[0]['TurnOver']
            call_exit_price = call_exit_data.iloc[0]['Close']
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
                    "Call Entry Turnover" : call_entry_turnover,
                    "Call ExitPrice" : call_exit_price,
                    "Call Exit Turnover": call_exit_turnover,
                    "Call P&L" : call_net,

                    "Net P&L" : total_net,
                    
                })
        
      
    if analysis_data:
        path = "./Output/Call_Sell_Future_Buy/Monthly/T-1 To T-1"
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell_FUT_Buy"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell_FUT_Buy"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell_FUT_Buy"

        fileName = fileName + f"_Monthly_Expiry_T-1_To_T-1(With_{pct_diff}%CE_Strike_Based_Adjustment)"
        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df = analyse_df.drop_duplicates(subset=['Entry Date', 'Exit Date']).reset_index(drop=True)
        analyse_df.to_csv(f"{path}/Monthly/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}/Monthly")

    if logFile:
        path = "./Output/Call_Sell_Future_Buy/Monthly/T-1 To T-1"
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell_FUT_Buy"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell_FUT_Buy"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell_FUT_Buy"

        fileName = fileName + f"_Monthly_Expiry_T-1_To_T-1(With_{pct_diff}%CE_Strike_Based_Adjustment)"
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/Monthly/{fileName}.csv", index=False)
        logFile.clear()


# Weekly Only
# Put ITM Sell of Weekly Expiry instead of Future Buy of Monthly Expiry
# Call Sell Put Sell
# Base 2 Bull
def main1_V4(spot_adjustment_type=0, spot_adjustment=1, call_sell_position=0, put_sell_position=0):
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

            print(f"Call Sell Put Sell Expiry To Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            if entrySpot.empty:
                continue
            entrySpot = entrySpot.iloc[0]['Close']

            exitSpot =  filtered_data[filtered_data['Date']==toDate] 
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None

            call_strike = round_half_up((entrySpot*(1+(call_sell_position/100)))/100)*100
            put_strike = round_half_up((entrySpot*(1+(put_sell_position/100)))/100)*100
            fileName1 = fromDate.strftime("%Y-%m-%d") + ".csv"
            fileName2 = toDate.strftime("%Y-%m-%d") + ".csv"

            bhav_df1, bhav_df2  = pd.DataFrame(), pd.DataFrame()
            call_entry_price, call_exit_price = None, None
            call_entry_turnover, call_exit_turnover = None, None
            put_entry_price, put_exit_price = None, None
            put_entry_turnover, put_exit_turnover = None, None
            call_net, put_net, total_net = None, None, None

            try:
                bhav_df1 = pd.read_csv(f"./cleaned_csvs/{fileName1}")
            except:
                reason = f"{fileName1} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
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
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
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
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
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
                if(call_entry_data.empty):
                    reason = f"Call Entry Data missing for Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Call Exit Data missing for Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
                continue
            if put_sell_position>=0:
                put_entry_data =  bhav_df1[
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
                put_entry_data =  bhav_df1[
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
                reason = f"No Strike Found above {put_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
                continue

            put_strike = put_entry_data.iloc[0]['StrikePrice']
            put_entry_data = put_entry_data[(put_entry_data['StrikePrice']==put_strike)]
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
                            ]
            if put_entry_data.empty or put_exit_data.empty:
                if(put_entry_data.empty):
                    reason = f"Put Entry Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Put Exit Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
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

                    "Put Expiry" : curr_expiry,
                    "Put Strike" : put_strike,
                    "Put EntryPrice": put_entry_price,
                    "Put Entry Turnover": put_entry_turnover,
                    "Put ExitPrice" : put_exit_price,
                    "Put Exit Turnover" : put_exit_turnover,
                    "Put P&L": put_net,

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
        path = "./Output/Call_Sell_Put_Sell_Bull/Weekly/Expiry To Expiry"
        
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
        
        if(spot_adjustment_type==0):
            fileName = fileName + "_NoAdjustment"
            path = path +"/Unadjusted"
        elif(spot_adjustment_type==1):
            fileName = fileName + f"_Adjustment_SpotRiseBy{spot_adjustment}%"
            path = path +"/Adjusted/Rise Only"
        elif(spot_adjustment_type==2):
            fileName = fileName + f"_Adjustment_SpotFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_Adjustment_SpotRisesOrFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/RiseOrFall"
        
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Weekly_Expiry-To-Expiry"
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        path = "./Output/Call_Sell_Put_Sell_Bull/Weekly/Expiry To Expiry"
        
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
        
        if(spot_adjustment_type==0):
            fileName = fileName + "_NoAdjustment"
            path = path +"/Unadjusted"
        elif(spot_adjustment_type==1):
            fileName = fileName + f"_Adjustment_SpotRiseBy{spot_adjustment}%"
            path = path +"/Adjusted/Rise Only"
        elif(spot_adjustment_type==2):
            fileName = fileName + f"_Adjustment_SpotFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_Adjustment_SpotRisesOrFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/RiseOrFall"
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Weekly_Expiry-To-Expiry"
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


def main2_V4(spot_adjustment_type=0, spot_adjustment=1, call_sell_position=0, put_sell_position=0, t_2=False):
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
        
        elif(first_instance):
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
                        or ((spot_adjustment_type==2) and (roc<=(-spot_adjustment)))
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

            if fromDate==toDate:
                continue
    
            if t_2:
                print(f"Call Sell Put Sell T-2 to T-2 Weekly From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            else:
                print(f"Call Sell Put Sell T-1 to T-1 Weekly From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            if entrySpot.empty:
                continue
            entrySpot = entrySpot.iloc[0]['Close']
            
            exitSpot =  filtered_data[filtered_data['Date']==toDate]
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None
            
            call_strike = round_half_up((entrySpot*(1+(call_sell_position/100)))/100)*100
            put_strike = round_half_up((entrySpot*(1+(put_sell_position/100)))/100)*100
            
            fileName1 = fromDate.strftime("%Y-%m-%d") + ".csv"
            fileName2 = toDate.strftime("%Y-%m-%d") + ".csv"
            
            bhav_df1, bhav_df2  = pd.DataFrame(), pd.DataFrame()   
            call_entry_price, call_exit_price = None, None
            call_entry_turnover, call_exit_turnover = None, None
            put_entry_price, put_exit_price = None, None
            put_entry_turnover, put_exit_turnover = None, None
            call_net, put_net, total_net = None, None, None

            try:
                bhav_df1 = pd.read_csv(f"./cleaned_csvs/{fileName1}")
            except:
                reason = f"{fileName1} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
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
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
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
                
            
            if call_entry_data.empty:
                reason = f"Call Data for Strike Below {call_strike} and Turnover>0 not found"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
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
                if(call_entry_data.empty):
                    reason = f"Call Entry Data missing for Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Call Exit Data missing for Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
                continue
        
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
            
            if put_entry_data.empty:
                reason = f"Put Data for Strike Above {put_strike} and Turnover>0 not found"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
                continue

            put_strike = put_entry_data.iloc[0]['StrikePrice']
            put_entry_data = put_entry_data[(put_entry_data['StrikePrice']==put_strike)]
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
                            ]
            
            if put_entry_data.empty or put_exit_data.empty:
                if(put_entry_data.empty):
                    reason = f"Put Entry Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Put Exit Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
                continue
        
            call_entry_price = call_entry_data.iloc[0]['Close']
            call_entry_turnover = call_entry_data.iloc[0]['TurnOver']
            call_exit_price = call_exit_data.iloc[0]['Close']
            call_exit_turnover = call_exit_data.iloc[0]['TurnOver']
            call_net =  round(call_entry_price -  call_exit_price, 2)
        
            
            put_entry_price = put_entry_data.iloc[0]['Close']
            put_entry_turnover = put_entry_data.iloc[0]['TurnOver']
            put_exit_price = put_exit_data.iloc[0]['Close']
            put_exit_turnover = put_exit_data.iloc[0]['TurnOver']
            put_net =  round(put_entry_price -  put_exit_price, 2)
        
            total_net = round(call_net + put_net, 2)

            analysis_data.append({
                    "Entry Date" : fromDate,
                    "Exit Date" : toDate,
                    
                    "Entry Spot" : entrySpot,
                    "Exit Spot" : exitSpot,

                    "Put Expiry" : curr_expiry,
                    "Put Strike" : put_strike,
                    "Put EntryPrice": put_entry_price,
                    "Put Entry Turnover" : put_entry_turnover,
                    "Put ExitPrice" : put_exit_price,
                    "Put Exit Turnover": put_exit_turnover,
                    "Put P&L": put_net,

                    "Call Expiry" : curr_expiry,
                    "Call Strike" : call_strike,
                    "Call EntryPrice" : call_entry_price,
                    "Call Entry Turnover" : call_entry_turnover,
                    "Call ExitPrice" : call_exit_price,
                    "Call Exit Turnover": call_exit_turnover,
                    "Call P&L" : call_net,

                    "Net P&L" : total_net,
                    
                })
        
      

    if analysis_data:
        if t_2:
            path = "./Output/Call_Sell_Put_Sell_Bull/Weekly/T-2 To T-2"
        else:
            path = "./Output/Call_Sell_Put_Sell_Bull/Weekly/T-1 To T-1"

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
            fileName = fileName + "_Weekly_Expiry_T-2_To_T-2"
        else:
            fileName = fileName + "_Weekly_Expiry_T-1_To_T-1"
        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")

    if logFile:
        if t_2:
            path = "./Output/Call_Sell_Put_Sell_Bull/Weekly/T-2 To T-2"
        else:
            path = "./Output/Call_Sell_Put_Sell_Bull/Weekly/T-1 To T-1"
        
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
            fileName = fileName + "_Weekly_Expiry_T-2_To_T-2"
        else:
            fileName = fileName + "_Weekly_Expiry_T-1_To_T-1"
        
        fileName = fileName + "_Log"
        os.makedirs(path, exist_ok=True)
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()

    
# Summary For CE+PE
def create_summary_idx_V4(df):
    entrySpot = df.iloc[0]['Entry Spot']
    first_entry_date = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d').min()
    last_exit_date = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d').max()
    number_of_years = (last_exit_date - first_entry_date).days / 365.25
    groups = {
        "Total Trades"  :   df,
    }

    stats_rows = []
    for label, subset in groups.items():
        count = len(subset)  
        total_sum = subset['Net P&L'].sum() if count>0 else None
        avg = (total_sum / count).round(2) if count > 0 else None
        
        win = len(subset[subset['Net P&L']>0]) if count>0 else None
        winPct = round((win/count * 100),2) if not pd.isna(win) else None
        avg_win = subset[subset['Net P&L']>0]['Net P&L'].mean() if not pd.isna(win) else None
        avg_win_pct = round(100*(avg_win/total_sum),2) if not pd.isna(win) else None
        avg_win = round(avg_win, 2) if not pd.isna(avg_win) else None
        
        lose = len(subset[subset['Net P&L']<0]) if count>0 else None
        losePct = round((lose/count * 100),2) if not pd.isna(lose) else None
        avg_loss = subset[subset['Net P&L']<0]['Net P&L'].mean() if not pd.isna(lose) else None
        avg_loss_pct = round(100*(avg_loss/total_sum),2) if  not pd.isna(lose) else None
        avg_loss = round(avg_loss, 2) if not pd.isna(avg_loss) else None

        expectancy = round(( ((avg_win_pct / abs(avg_loss_pct) ) * winPct) - losePct)/100, 2) if not pd.isna(win) and not pd.isna(lose) else None

        if count>0 and ((total_sum + entrySpot)/entrySpot) > 0:
            cagr_options = round(
                100 * (((total_sum + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (total_sum + entrySpot) > 0 else 0
        else:
            cagr_options = 0

        dd = subset['%DD'].min().round(2) if count>0 else None
        dd_points = subset['DD'].min().round(2) if count>0 else None
        Car_MDD = round(cagr_options/abs(dd), 2)
        recovery_factor = round(total_sum/abs(dd_points), 2)

        spot_chg = subset['Spot P&L'].sum()
        roi_vs_spot = round(100*(total_sum/spot_chg), 2) if spot_chg!=0 else None
        
        if count>0 and ((spot_chg + entrySpot) / entrySpot)>0:
            cagr_spot = round(
                100 * (((spot_chg + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (spot_chg + entrySpot) > 0 else 0

        else:
            cagr_spot = 0

        stats_rows.append([
                        label, count, total_sum, avg, 
                        winPct, avg_win, losePct, avg_loss, 
                        expectancy, cagr_options, 
                        dd, spot_chg, roi_vs_spot, 
                        cagr_spot, dd_points, Car_MDD,
                        recovery_factor
        ])
        
    stats_df = pd.DataFrame(stats_rows, columns=[
                                    "Category", "Count", "Sum", "Avg", 
                                    "W%", "Avg(W)", "L%", "Avg(L)",
                                    "Expectancy", "CAGR(Options)",
                                    "DD", "Spot Change", "ROI vs Spot",
                                    "CAGR(Spot)", "DD(Points)", "CAR/MDD",
                                    "Recovery Factor"

                                ])

    
    total_df = pd.DataFrame([
        ["Spot P&L", df["Spot P&L"].sum().round(2)],
        ["PE P&L", df["Put P&L"].sum().round(2)],
        ["CE P&L", df["Call P&L"].sum().round(2)],
        ["PE+CE P&L", df["Net P&L"].sum().round(2)],
        ["PE+CE+Spot P&L", (df["Net P&L"].sum() + df["Spot P&L"].sum()).round(2)],

    ], columns=["Type", "Sum"])

    return stats_df, total_df


def getPivotTable_V4(df):
    filtered_df = df[['Call Expiry', 'Net P&L']].copy(deep=True)
    header = ["Sum of Net P&L", "Total Points"]

    if filtered_df.empty:
        return pd.DataFrame(), [], pd.DataFrame(), []
    
    filtered_df['Month'] = pd.to_datetime(filtered_df['Call Expiry'], format='%Y-%m-%d').dt.strftime("%b")
    filtered_df['Year'] = pd.to_datetime(filtered_df['Call Expiry'], format='%Y-%m-%d').dt.year
    
    pivot_table = filtered_df.pivot_table(
        values = filtered_df.columns[1],  
        index = 'Year',  
        columns = 'Month', 
        aggfunc = 'sum'
    )
    
    month_order = [m for m in ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'] if m in pivot_table.columns]
    pivot_table = pivot_table[month_order]
    grand_total = ['Grand Total'] + [pivot_table[col].sum().round(2) for col in month_order]
    
    grand_total_df = pd.DataFrame([grand_total], columns=['Year'] + month_order)
    pivot_table = pd.concat([pivot_table, grand_total_df.set_index('Year')])
    pivot_table['Grand Total'] = pivot_table[month_order].sum(axis=1).round(2)
    pivot_table.reset_index(inplace=True)

    return pivot_table, header


def save_hypothetical_and_summary_idx_V4(df, filename="./df_final.xlsx"):
    stats_df, total_df = create_summary_idx_V4(df)
    pivot_table, header = getPivotTable_V4(df)
    
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df['Entry Date'] = df['Entry Date'].dt.date
        df['Exit Date'] = df['Exit Date'].dt.date
        df['Put Expiry'] = df['Put Expiry'].dt.date
        df['Call Expiry'] = df['Call Expiry'].dt.date
        df.to_excel(writer, sheet_name="Hypothetical TradeSheet", index=False)

        start_row = 0
        for table in [stats_df, total_df]:
            table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row)
            start_row += len(table) + 2  
        start_row = start_row + 1

        header_df = pd.DataFrame([header])
        header_df.to_excel(writer, sheet_name="Summary", index=False, header=False, startrow=start_row)
        pivot_table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row+2)
        start_row += len(header_df) + len(pivot_table) + 3 


def summary_V4():
    main_path = "./Analysis/Data"
    all_folders = os.listdir(main_path)
    all_folders = [
        f for f in os.listdir(main_path)
        if os.path.isdir(os.path.join(main_path, f))
    ]

    for folder in all_folders:
        main_folders = os.listdir(os.path.join(main_path, folder))
        
        for f in main_folders:
            files = glob.glob(os.path.join(main_path, folder,f, "*.csv"), recursive=True)
            filtered_files = [
                    f for f in files 
                    if "log" not in f.lower() and "~$" not in f and "summary" not in f.lower()
                ]
            for file in filtered_files: 
                print(folder, file)
                df = pd.read_csv(file)
                try:
                    df['Call Expiry'] = pd.to_datetime(df['Call Expiry'], format='%Y-%m-%d')
                except:
                    df['Call Expiry'] = pd.to_datetime(df['Call Expiry'], format='%d-%m-%Y')

                try:
                    df['Put Expiry'] = pd.to_datetime(df['Put Expiry'], format='%Y-%m-%d')
                except:
                    df['Put Expiry'] = pd.to_datetime(df['Put Expiry'], format='%d-%m-%Y')

                try:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d')
                except:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%d-%m-%Y')
                    
                try:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d')
                except:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%d-%m-%Y')

                df = df[
                        (df['Put Expiry']>pd.Timestamp(2019,2,1))
                    ].sort_values(by=['Entry Date', 'Exit Date']).reset_index(drop=True)
                
                # Bear Filter - Remove Covid Patch
                # df =df[
                #         ~((df['Entry Date']>=pd.Timestamp(2020,2,28))
                #         & (df['Entry Date']<pd.Timestamp(2021,11,30)))
                #        ].reset_index(drop=True)
              
                
                df['Hypothetical Put ExitPrice'] = np.where(
                                                df['Exit Date'] == df['Put Expiry'],
                                                    np.where(
                                                        df['Exit Spot'] > df['Put Strike'], 
                                                        df['Put ExitPrice'].fillna(0),                              
                                                        df['Put Strike']- df['Exit Spot']   
                                                    ),
                                                df['Put ExitPrice']
                                            )
                df['Hypothetical Call ExitPrice'] = np.where(
                                                df['Exit Date'] == df['Call Expiry'],
                                                    np.where(
                                                        df['Exit Spot'] < df['Call Strike'], 
                                                        df['Call ExitPrice'].fillna(0),                              
                                                        df['Exit Spot'] - df['Call Strike'] 
                                                    ),
                                                df['Call ExitPrice']
                                            )
                df['Put P&L'] = df['Put EntryPrice'] - df['Hypothetical Put ExitPrice']
                df['Call P&L'] = df['Call EntryPrice'] - df['Hypothetical Call ExitPrice']
                df.drop(columns=['Put ExitPrice', 'Call ExitPrice'], inplace=True)
                
                df['Put P&L'] = df['Put P&L'].round(2)
                df['Call P&L'] = df['Call P&L'].round(2)
                df['Net P&L'] = df['Call P&L'] + df['Put P&L']
                df['Net P&L'] = df['Net P&L'].round(2)
                df['Net P&L/Spot Pct'] = round((df['Net P&L']/df['Entry Spot'])*100, 2)
                df['Spot P&L'] = round(df['Exit Spot'] - df['Entry Spot'], 2)
                

                df['Cumulative'] = None
                df.at[0, 'Cumulative'] = df.iloc[0]['Entry Spot'] + df.iloc[0]['Net P&L']
                
                for i in range(1, len(df)):
                    df.at[i, 'Cumulative'] = df.at[i-1, 'Cumulative'] + df.at[i, 'Net P&L']

                df['Peak'] = df['Cumulative'].cummax()
                df['DD'] = np.where(df['Peak']>df['Cumulative'], df['Cumulative']-df['Peak'], 0)
                df['Peak'] = df['Peak'].astype(float)
                df['DD'] = df['DD'].astype(float)
                df['%DD'] = np.where(df['DD']==0, 0, round(100*(df['DD']/df['Peak']),2))
                df['%DD'] = df['%DD'].round(2)
                
                df = df[[

                        'Entry Date', 'Exit Date', 
                        'Entry Spot', 'Exit Spot', 
                        'Spot P&L', 

                        'Put Expiry', 'Put Strike', 
                        'Put Entry Turnover', 'Put EntryPrice', 
                        'Put Exit Turnover', 'Hypothetical Put ExitPrice', 
                        'Put P&L', 

                        'Call Expiry', 'Call Strike', 
                        'Call Entry Turnover', 'Call EntryPrice', 
                        'Call Exit Turnover', 'Hypothetical Call ExitPrice', 
                        'Call P&L', 

                        'Net P&L', 'Net P&L/Spot Pct',
                        'Cumulative', 'Peak', 'DD', '%DD'
                    ]]
                
                file = file.split(".csv")[0]
                file = file + "_Final_Summary" + ".xlsx"
                save_hypothetical_and_summary_idx_V4(df, file)
   

# PE Short During Base2 Bull
# PE Sell Only
# Expiry To Expiry
def main1_V5_Put(spot_adjustment_type=0, spot_adjustment=1, put_sell_position=0, protection=False, protection_pct=1):
    data_df = getStrikeData("NIFTY")
    base2_df = pd.read_csv("./Filter/base2.csv")
    base2_df['Start'] = pd.to_datetime(base2_df['Start'], format='%Y-%m-%d')
    base2_df['End'] = pd.to_datetime(base2_df['End'], format='%Y-%m-%d')
    base2_df = base2_df.sort_values(by=['Start', 'End']).reset_index(drop=True)
    
    # base_list = []
    # for i in range(0, len(base2_df)-1):
    #     base_list.append(
    #         {
    #             'Start' : base2_df.iloc[i]['End'],
    #             'End' : base2_df.iloc[i+1]['Start']
    #         }
    #     )
    
    # if base_list:
    #     base2_df = pd.DataFrame(base_list)
    # else:
    #     base2_df = pd.DataFrame()    

    # if base2_df.empty:
    #     sys.exit("Issue with Base File")

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
        
            if put_sell_position==0:
                if not protection:
                    print(f"Put ATM Sell Expiry To Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                else:
                    print(f"Put ATM Sell Protective Put {protection_pct}% Buy Expiry To Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            elif put_sell_position>0:
                if not protection:
                    print(f"Put {put_sell_position}% ITM Sell Expiry To Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                else:
                    print(f"Put {put_sell_position}% ITM Sell Protective Put {protection_pct}% Buy Expiry To Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            else:
                if not protection:
                    print(f"Put {put_sell_position}% OTM Sell Expiry To Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                else:
                    print(f"Put {put_sell_position}% OTM Sell Protective Put {protection_pct}% Buy Expiry To Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")

            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            if entrySpot.empty:
                continue
            entrySpot = entrySpot.iloc[0]['Close']

            exitSpot =  filtered_data[filtered_data['Date']==toDate] 
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None

            put_strike = round_half_up((entrySpot*(1+(put_sell_position/100)))/100)*100
            fileName1 = fromDate.strftime("%Y-%m-%d") + ".csv"
            fileName2 = toDate.strftime("%Y-%m-%d") + ".csv"
            
            bhav_df1, bhav_df2  = pd.DataFrame(), pd.DataFrame()
            put_entry_price, put_exit_price = None, None
            put_entry_price1, put_exit_price1 = None, None
            put_entry_turnover, put_exit_turnover = None, None
            put_entry_turnover1, put_exit_turnover1 = None, None
            put_net, put_net1, total_net = None, None, None

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


            if put_sell_position>=0:
                put_entry_data =  bhav_df1[
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
                put_entry_data =  bhav_df1[
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
                reason = f"No Strike Found above {put_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, fromDate, toDate)
                continue

            put_strike = put_entry_data.iloc[0]['StrikePrice']
            put_entry_data = put_entry_data[(put_entry_data['StrikePrice']==put_strike)]
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
                            ]
            
            if put_exit_data.empty:
                reason = f"Put Exit Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, fromDate, toDate)
                continue

            put_entry_price = put_entry_data.iloc[0]['Close']
            put_exit_price = put_exit_data.iloc[0]['Close']
            put_entry_turnover = put_entry_data.iloc[0]['TurnOver']
            put_exit_turnover = put_exit_data.iloc[0]['TurnOver']
            put_net =  round(put_entry_price -  put_exit_price, 2)
            
            total_net = put_net
            
            if protection:
                put_strike1 = round_half_up((put_strike * (1 - (protection_pct*0.01)))/100)*100
                put_entry_data1 = bhav_df1[
                                    (bhav_df1['Instrument']=="OPTIDX")
                                    & (bhav_df1['Symbol']=="NIFTY")
                                    & (bhav_df1['OptionType']=="PE")
                                    & (
                                        (bhav_df1['ExpiryDate']==curr_expiry)
                                        | (bhav_df1['ExpiryDate']==curr_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==curr_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['StrikePrice']<=put_strike1)
                                    & (bhav_df1['TurnOver']>0)
                                    & (bhav_df1['StrikePrice']%100==0)
                                ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)
                
                if put_entry_data1.empty:
                    reason = f"Protective Put Leg Entry Data For Strike Below {put_strike1} and Turnover>0 not found"
                    createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, fromDate, toDate)
                    continue

                put_strike1 = put_entry_data1.iloc[0]['StrikePrice']
                put_entry_data1 = put_entry_data1[(put_entry_data1['StrikePrice']==put_strike1)]
                put_exit_data1 = bhav_df2[
                                    (bhav_df2['Instrument']=="OPTIDX")
                                    & (bhav_df2['Symbol']=="NIFTY")
                                    & (bhav_df2['OptionType']=="PE")
                                    & (
                                        (bhav_df2['ExpiryDate']==curr_expiry)
                                        | (bhav_df2['ExpiryDate']==curr_expiry-timedelta(days=1))
                                        |  (bhav_df2['ExpiryDate']==curr_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df2['StrikePrice']==put_strike1)
                                ]
                
                if put_exit_data1.empty:
                    reason = f"Protective Put Leg Exit Data for Strike {put_strike1} not found"
                    createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, fromDate, toDate)
                    continue

                put_entry_price1 = put_entry_data1.iloc[0]['Close']
                put_entry_turnover1 = put_entry_data1.iloc[0]['TurnOver']
                put_exit_price1 = put_exit_data1.iloc[0]['Close']
                put_exit_turnover1 = put_exit_data1.iloc[0]['TurnOver']
                put_net1 = round(put_exit_price1 - put_entry_price1, 2)
                total_net += put_net1
            
            if not protection:
                analysis_data.append({
                        "Entry Date" : fromDate,
                        "Exit Date" : toDate,
                        
                        "Entry Spot" : entrySpot,
                        "Exit Spot" : exitSpot,

                        "Put Expiry" : curr_expiry,
                        "Put Strike" : put_strike,
                        "Put EntryPrice": put_entry_price,
                        "Put Entry Turnover": put_entry_turnover,
                        "Put ExitPrice" : put_exit_price,
                        "Put Exit Turnover" : put_exit_turnover,
                        "Put P&L": put_net,

                        "Net P&L" : total_net,
                        
                    })
            else:
                analysis_data.append({
                        "Entry Date" : fromDate,
                        "Exit Date" : toDate,
                        
                        "Entry Spot" : entrySpot,
                        "Exit Spot" : exitSpot,

                        "Put Expiry" : curr_expiry,
                        "Put Strike" : put_strike,
                        "Put EntryPrice": put_entry_price,
                        "Put Entry Turnover": put_entry_turnover,
                        "Put ExitPrice" : put_exit_price,
                        "Put Exit Turnover" : put_exit_turnover,
                        "Put P&L": put_net,

                        "Protective Put Strike" : put_strike1,
                        "Protective Put EntryPrice": put_entry_price1,
                        "Protective Put Entry Turnover": put_entry_turnover1,
                        "Protective Put ExitPrice" : put_exit_price1,
                        "Protective Put Exit Turnover" : put_exit_turnover1,
                        "Protective Put P&L": put_net1,
                        
                        "Net P&L" : total_net,
                        
                    })

    if analysis_data:
        if not protection:
            path = "./Output/Put_Sell/Weekly/Expiry To Expiry"
        else:
            path = "./Output/Put_Sell_Protective_Put_Buy/Weekly/Expiry To Expiry"
        
        if put_sell_position==0:
            fileName = f"PE_ATM_Sell" 
        elif put_sell_position>0:
            fileName = f"PE_{put_sell_position}%_ITM_Sell"
        else:
            fileName = f"PE_{put_sell_position}%_OTM_Sell"

        if protection:
            fileName = fileName +f"_With_Protective_Put_{protection_pct}%_Buy"
            
        if(spot_adjustment_type==0):
            fileName = fileName + "_No_Adjustment"
            path = path +"/Unadjusted"
        elif(spot_adjustment_type==1):
            fileName = fileName + f"_Adjustment_SpotRiseBy{spot_adjustment}%"
            path = path +"/Adjusted/Rise Only"
        elif(spot_adjustment_type==2):
            fileName = fileName + f"_Adjustment_SpotFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_Adjustment_SpotRisesOrFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/RiseOrFall"
        
        fileName = fileName + "_Weekly_Expiry-To-Expiry"
        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        if not protection:
            path = "./Output/Put_Sell/Weekly/Expiry To Expiry"
        else:
            path = "./Output/Put_Sell_Protective_Put_Buy/Weekly/Expiry To Expiry"
        
        if put_sell_position==0:
            fileName = f"PE_ATM_Sell" 
        elif put_sell_position>0:
            fileName = f"PE_{put_sell_position}%_ITM_Sell"
        else:
            fileName = f"PE_{put_sell_position}%_OTM_Sell"

        if protection:
            fileName = fileName +f"_With_Protective_Put_{protection_pct}%_Buy"

        if(spot_adjustment_type==0):
            fileName = fileName + "_No_Adjustment"
            path = path +"/Unadjusted"
        elif(spot_adjustment_type==1):
            fileName = fileName + f"_Adjustment_SpotRiseBy{spot_adjustment}%"
            path = path +"/Adjusted/Rise Only"
        elif(spot_adjustment_type==2):
            fileName = fileName + f"_Adjustment_SpotFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_Adjustment_SpotRisesOrFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/RiseOrFall"
        
        fileName = fileName + "_Weekly_Expiry-To-Expiry"
        fileName = fileName + "_Log"
        os.makedirs(path, exist_ok=True)
        
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()

# T-1/T-2 to T-1/T-2
def main2_V5_Put(spot_adjustment_type=0, spot_adjustment=1, put_sell_position=0, t_2=False, protection=False, protection_pct=1):
    data_df = getStrikeData("NIFTY")
    base2_df = pd.read_csv("./Filter/base2.csv")
    base2_df['Start'] = pd.to_datetime(base2_df['Start'], format='%Y-%m-%d')
    base2_df['End'] = pd.to_datetime(base2_df['End'], format='%Y-%m-%d')
    base2_df = base2_df.sort_values(by=['Start', 'End']).reset_index(drop=True)
    
    # base_list = []
    # for i in range(0, len(base2_df)-1):
    #     base_list.append(
    #         {
    #             'Start' : base2_df.iloc[i]['End'],
    #             'End' : base2_df.iloc[i+1]['Start']
    #         }
    #     )
    
    # if base_list:
    #     base2_df = pd.DataFrame(base_list)
    # else:
    #     base2_df = pd.DataFrame()    

    # if base2_df.empty:
    #     sys.exit("Issue with Base File")

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
                            & (data_df_1['Date']<curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)
        
        if(len(filtered_data)<2):
            continue
        
        if not first_instance:
            first_instance = True
        elif first_instance:
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
            fileName1 = fileName2 = ""
            fromDate = interval_df.iloc[i]['From']
            toDate = interval_df.iloc[i]['To']
            
            if(fromDate==toDate):
                continue

            if t_2:
                print("T-2 To T-2", end=" ")
            else:
                print("T-1 To T-1", end=" ")
            
            if put_sell_position==0:
                if not protection:
                    print(f"Put ATM Sell From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                else:
                    print(f"Put ATM Sell Protective Put {protection_pct}% Buy From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            elif put_sell_position>0:
                if not protection:
                    print(f"Put {put_sell_position}% ITM Sell From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                else:
                    print(f"Put {put_sell_position}% ITM Sell Protective Put {protection_pct}% Buy From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            else:
                if not protection:
                    print(f"Put {put_sell_position}% OTM Sell From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                else:
                    print(f"Put {put_sell_position}% OTM Sell Protective Put {protection_pct}% Buy From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")

            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            if entrySpot.empty:
                continue
            entrySpot = entrySpot.iloc[0]['Close']

            exitSpot =  filtered_data[filtered_data['Date']==toDate] 
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None

            put_strike = round_half_up((entrySpot*(1+(put_sell_position/100)))/100)*100
            fileName1 = fromDate.strftime("%Y-%m-%d") + ".csv"
            fileName2 = toDate.strftime("%Y-%m-%d") + ".csv"
            
            bhav_df1, bhav_df2  = pd.DataFrame(), pd.DataFrame()
            put_entry_price, put_exit_price = None, None
            put_entry_price1, put_exit_price1 = None, None
            put_entry_turnover, put_exit_turnover = None, None
            put_entry_turnover1, put_exit_turnover1 = None, None
            put_net, put_net1, total_net = None, None, None

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


            if put_sell_position>=0:
                put_entry_data =  bhav_df1[
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
                put_entry_data =  bhav_df1[
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
                reason = f"No Strike Found above {put_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, fromDate, toDate)
                continue

            put_strike = put_entry_data.iloc[0]['StrikePrice']
            put_entry_data = put_entry_data[(put_entry_data['StrikePrice']==put_strike)]
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
                            ]
            
            if put_exit_data.empty:
                reason = f"Put Exit Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, fromDate, toDate)
                continue

            put_entry_price = put_entry_data.iloc[0]['Close']
            put_exit_price = put_exit_data.iloc[0]['Close']
            put_entry_turnover = put_entry_data.iloc[0]['TurnOver']
            put_exit_turnover = put_exit_data.iloc[0]['TurnOver']
            put_net =  round(put_entry_price -  put_exit_price, 2)
        
            total_net = put_net
            
            if protection:
                put_strike1 = round_half_up((put_strike * (1 - (protection_pct*0.01)))/100)*100
                put_entry_data1 = bhav_df1[
                                    (bhav_df1['Instrument']=="OPTIDX")
                                    & (bhav_df1['Symbol']=="NIFTY")
                                    & (bhav_df1['OptionType']=="PE")
                                    & (
                                        (bhav_df1['ExpiryDate']==curr_expiry)
                                        | (bhav_df1['ExpiryDate']==curr_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==curr_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['StrikePrice']<=put_strike1)
                                    & (bhav_df1['TurnOver']>0)
                                    & (bhav_df1['StrikePrice']%100==0)
                                ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)
                
                if put_entry_data1.empty:
                    reason = f"Protective Put Leg Entry Data For Strike Below {put_strike1} and Turnover>0 not found"
                    createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, fromDate, toDate)
                    continue

                put_strike1 = put_entry_data1.iloc[0]['StrikePrice']
                put_entry_data1 = put_entry_data1[(put_entry_data1['StrikePrice']==put_strike1)]
                put_exit_data1 = bhav_df2[
                                    (bhav_df2['Instrument']=="OPTIDX")
                                    & (bhav_df2['Symbol']=="NIFTY")
                                    & (bhav_df2['OptionType']=="PE")
                                    & (
                                        (bhav_df2['ExpiryDate']==curr_expiry)
                                        | (bhav_df2['ExpiryDate']==curr_expiry-timedelta(days=1))
                                        |  (bhav_df2['ExpiryDate']==curr_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df2['StrikePrice']==put_strike1)
                                ]
                
                if put_exit_data1.empty:
                    reason = f"Protection Put Exit Data for Strike {put_strike1} not found"
                    createLogFile("NIFTY", reason, pd.NaT, curr_expiry, pd.NaT, fromDate, toDate)
                    continue

                put_entry_price1 = put_entry_data1.iloc[0]['Close']
                put_entry_turnover1 = put_entry_data1.iloc[0]['TurnOver']
                put_exit_price1 = put_exit_data1.iloc[0]['Close']
                put_exit_turnover1 = put_exit_data1.iloc[0]['TurnOver']
                put_net1 = round(put_exit_price1-put_entry_price1, 2)
                total_net += put_net1

            if not protection:
                analysis_data.append({
                        "Entry Date" : fromDate,
                        "Exit Date" : toDate,
                        
                        "Entry Spot" : entrySpot,
                        "Exit Spot" : exitSpot,

                        "Put Expiry" : curr_expiry,
                        "Put Strike" : put_strike,
                        "Put EntryPrice": put_entry_price,
                        "Put Entry Turnover": put_entry_turnover,
                        "Put ExitPrice" : put_exit_price,
                        "Put Exit Turnover" : put_exit_turnover,
                        "Put P&L": put_net,

                        "Net P&L" : total_net,
                        
                    })
            else:
                analysis_data.append({
                        "Entry Date" : fromDate,
                        "Exit Date" : toDate,
                        
                        "Entry Spot" : entrySpot,
                        "Exit Spot" : exitSpot,

                        "Put Expiry" : curr_expiry,
                        "Put Strike" : put_strike,
                        "Put EntryPrice": put_entry_price,
                        "Put Entry Turnover": put_entry_turnover,
                        "Put ExitPrice" : put_exit_price,
                        "Put Exit Turnover" : put_exit_turnover,
                        "Put P&L": put_net,

                        "Protective Put Strike" : put_strike1,
                        "Protective Put EntryPrice": put_entry_price1,
                        "Protective Put Entry Turnover": put_entry_turnover1,
                        "Protective Put ExitPrice" : put_exit_price1,
                        "Protective Put Exit Turnover" : put_exit_turnover1,
                        "Protective Put P&L": put_net1,

                        "Net P&L" : total_net,
                        
                    })  
                
    if analysis_data:
        if not protection:
            if t_2:
                path = "./Output/Put_Sell/Weekly/T-2 To T-2"
            else:
                path = "./Output/Put_Sell/Weekly.T-1 To T-1"
        else:
            if t_2:
                path = "./Output/Put_Sell_Protective_Put_Buy/Weekly/T-2 To T-2"
            else:
                path = "./Output/Put_Sell_Protective_Put_Buy/Weekly/T-1 To T-1"

        if put_sell_position==0:
            fileName = f"PE_ATM_Sell" 
        elif put_sell_position>0:
            fileName = f"PE_{put_sell_position}%_ITM_Sell"
        else:
            fileName = f"PE_{put_sell_position}%_OTM_Sell"

        if protection:
            fileName = fileName +f"_With_Protective_Put_{protection_pct}%_Buy"
        
        if(spot_adjustment_type==0):
            fileName = fileName + "_No_Adjustment"
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
        if not protection:
            if t_2:
                path = "./Output/Put_Sell/Weekly/T-2 To T-2"
            else:
                path = "./Output/Put_Sell/Weekly.T-1 To T-1"
        else:
            if t_2:
                path = "./Output/Put_Sell_Protective_Put_Buy/Weekly/T-2 To T-2"
            else:
                path = "./Output/Put_Sell_Protective_Put_Buy/Weekly/T-1 To T-1"

        if put_sell_position==0:
            fileName = f"PE_ATM_Sell" 
        elif put_sell_position>0:
            fileName = f"PE_{put_sell_position}%_ITM_Sell"
        else:
            fileName = f"PE_{put_sell_position}%_OTM_Sell"

        if protection:
            fileName = fileName +f"_With_Protective_Put_{protection_pct}%_Buy"
        

        if(spot_adjustment_type==0):
            fileName = fileName + "_No_Adjustment"
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
        
        fileName = fileName + "_Log"
        os.makedirs(path, exist_ok=True)
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()

# CE Short During Base2 Bear
# Expiry To Expiry
def main1_V5_Call(spot_adjustment_type=0, spot_adjustment=1, call_sell_position=0, protection=False, protection_pct=1):
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
        
                
            if call_sell_position==0:
                if not protection:
                    print(f"Call ATM Sell Expiry To Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                else:
                    print(f"Call ATM Sell Protective Call {protection_pct}% Buy Expiry To Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            elif call_sell_position>0:
                if not protection:
                    print(f"Call {call_sell_position}% OTM Sell Expiry To Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                else:
                    print(f"Call {call_sell_position}% OTM Sell Protective Call {protection_pct}% Buy Expiry To Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            else:
                if not protection:
                    print(f"Call {call_sell_position}% ITM Sell Expiry To Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                else:
                    print(f"Call {call_sell_position}% ITM Sell Protective Call {protection_pct}% Buy Expiry To Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
           
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            if entrySpot.empty:
                continue
            entrySpot = entrySpot.iloc[0]['Close']

            exitSpot =  filtered_data[filtered_data['Date']==toDate] 
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None

            call_strike = round_half_up((entrySpot*(1+(call_sell_position/100)))/100)*100
            fileName1 = fromDate.strftime("%Y-%m-%d") + ".csv"
            fileName2 = toDate.strftime("%Y-%m-%d") + ".csv"
            
            bhav_df1, bhav_df2  = pd.DataFrame(), pd.DataFrame()
            call_entry_price, call_exit_price = None, None
            call_entry_price1, call_exit_price1 = None, None
            call_entry_turnover, call_exit_turnover = None, None
            call_entry_turnover1, call_exit_turnover1 = None, None
            call_net, call_net1, total_net = None, None, None

            try:
                bhav_df1 = pd.read_csv(f"./cleaned_csvs/{fileName1}")
            except:
                reason = f"{fileName1} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, pd.NaT, fromDate, toDate)
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
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, pd.NaT, fromDate, toDate)
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
                call_entry_data =  bhav_df1[
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
                call_entry_data =  bhav_df1[
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
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, pd.NaT, fromDate, toDate)
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
            
            if call_exit_data.empty:
                reason = f"Call Exit Data missing for Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, pd.NaT, fromDate, toDate)
                continue

            call_entry_price = call_entry_data.iloc[0]['Close']
            call_exit_price = call_exit_data.iloc[0]['Close']
            call_entry_turnover = call_entry_data.iloc[0]['TurnOver']
            call_exit_turnover = call_exit_data.iloc[0]['TurnOver']
            call_net =  round(call_entry_price -  call_exit_price, 2)
        
            total_net = call_net
            
            if protection:
                call_strike1 = round_half_up((call_strike * (1 + (protection_pct*0.01)))/100)*100
                call_entry_data1 = bhav_df1[
                                    (bhav_df1['Instrument']=="OPTIDX")
                                    & (bhav_df1['Symbol']=="NIFTY")
                                    & (bhav_df1['OptionType']=="CE")
                                    & (
                                        (bhav_df1['ExpiryDate']==curr_expiry)
                                        | (bhav_df1['ExpiryDate']==curr_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==curr_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['StrikePrice']>=call_strike1)
                                    & (bhav_df1['TurnOver']>0)
                                    & (bhav_df1['StrikePrice']%100==0)
                                ].sort_values(by='StrikePrice', ascending=True).reset_index(drop=True)  
                
                if call_entry_data1.empty:
                    reason = f"Protective Call Leg Entry Data for Strike Above {call_strike1} not found"
                    createLogFile("NIFTY", reason, curr_expiry, pd.NaT, pd.NaT, fromDate, toDate)
                    continue

                call_strike1 = call_entry_data1.iloc[0]['StrikePrice']
                call_entry_data1 = call_entry_data1[(call_entry_data1['StrikePrice']==call_strike1)]
                call_exit_data1 = bhav_df2[
                                    (bhav_df2['Instrument']=="OPTIDX")
                                    & (bhav_df2['Symbol']=="NIFTY")
                                    & (bhav_df2['OptionType']=="CE")
                                    & (
                                        (bhav_df2['ExpiryDate']==curr_expiry)
                                        | (bhav_df2['ExpiryDate']==curr_expiry-timedelta(days=1))
                                        |  (bhav_df2['ExpiryDate']==curr_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df2['StrikePrice']==call_strike1)
                                ]
                
                if call_exit_data1.empty:
                    reason = f"Protective Call Leg Exit Data for Strike {call_strike1} not found"
                    createLogFile("NIFTY", reason, curr_expiry, pd.NaT, pd.NaT, fromDate, toDate)
                    continue
                
                call_entry_price1 = call_entry_data1.iloc[0]['Close']
                call_entry_turnover1 = call_entry_data1.iloc[0]['TurnOver']
                call_exit_price1 = call_exit_data1.iloc[0]['Close']
                call_exit_turnover1 = call_exit_data1.iloc[0]['TurnOver']
                call_net1 = round(call_exit_price1-call_entry_price1, 2)
                total_net += call_net1
            
            if not protection:
                analysis_data.append({
                        "Entry Date" : fromDate,
                        "Exit Date" : toDate,
                        
                        "Entry Spot" : entrySpot,
                        "Exit Spot" : exitSpot,

                        "Call Expiry" : curr_expiry,
                        "Call Strike" : call_strike,
                        "Call EntryPrice": call_entry_price,
                        "Call Entry Turnover": call_entry_turnover,
                        "Call ExitPrice" : call_exit_price,
                        "Call Exit Turnover" : call_exit_turnover,
                        "Call P&L": call_net,

                        "Net P&L" : total_net,            
                    })
            else:
                analysis_data.append({
                        "Entry Date" : fromDate,
                        "Exit Date" : toDate,
                        
                        "Entry Spot" : entrySpot,
                        "Exit Spot" : exitSpot,

                        "Call Expiry" : curr_expiry,
                        "Call Strike" : call_strike,
                        "Call EntryPrice": call_entry_price,
                        "Call Entry Turnover": call_entry_turnover,
                        "Call ExitPrice" : call_exit_price,
                        "Call Exit Turnover" : call_exit_turnover,
                        "Call P&L": call_net,

                        "Protective Call Strike" : call_strike1,
                        "Protective Call EntryPrice": call_entry_price1,
                        "Protective Call Entry Turnover": call_entry_turnover1,
                        "Protective Call ExitPrice" : call_exit_price1,
                        "Protective Call Exit Turnover" : call_exit_turnover1,
                        "Protective Call P&L": call_net1,

                        "Net P&L" : total_net,            
                    })

                    
    if analysis_data:
        if not protection:
            path = "./Output/Call_Sell/Weekly/Expiry To Expiry"
        else:
            path = "./Output/Call_Sell_Protective_Call_Buy/Weekly/Expiry To Expiry"
        
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell" 
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"

        if protection:
            fileName = fileName +f"_With_Protective_Call_{protection_pct}%_Buy"

        if(spot_adjustment_type==0):
            fileName = fileName + "_No_Adjustment"
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
        if not protection:
            path = "./Output/Call_Sell/Weekly/Expiry To Expiry"
        else:
            path = "./Output/Call_Sell_Protective_Call_Buy/Weekly/Expiry To Expiry"
        
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell" 
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"

        if protection:
            fileName = fileName +f"_With_Protective_Call_{protection_pct}%_Buy"


        if(spot_adjustment_type==0):
            fileName = fileName + "_No_Adjustment"
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
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()

# T-1/T-2 to T-1/T-2
def main2_V5_Call(spot_adjustment_type=0, spot_adjustment=1, call_sell_position=0, t_2=False, protection=False, protection_pct=1):
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
        
        elif(first_instance):
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
            fileName1 = fileName2 = ""
            fromDate = interval_df.iloc[i]['From']
            toDate = interval_df.iloc[i]['To']
            
            if(fromDate==toDate):
                continue
            
            if t_2:
                print("T-2 To T-2 Weekly", end= " ")
            else:
                print("T-1 To T-1 Weekly", end= " ")

            
            if call_sell_position==0:
                if not protection:
                    print(f"Call ATM Sell From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                else:
                    print(f"Call ATM Sell Protective Call {protection_pct}% Buy From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            elif call_sell_position>0:
                if not protection:
                    print(f"Call {call_sell_position}% OTM Sell From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                else:
                    print(f"Call {call_sell_position}% OTM Sell Protective Call {protection_pct}% Buy From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            else:
                if not protection:
                    print(f"Call {call_sell_position}% ITM Sell From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
                else:
                    print(f"Call {call_sell_position}% ITM Sell Protective Call {protection_pct}% Buy From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            if entrySpot.empty:
                continue
            entrySpot = entrySpot.iloc[0]['Close']

            exitSpot =  filtered_data[filtered_data['Date']==toDate] 
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None

            call_strike = round_half_up((entrySpot*(1+(call_sell_position/100)))/100)*100
            fileName1 = fromDate.strftime("%Y-%m-%d") + ".csv"
            fileName2 = toDate.strftime("%Y-%m-%d") + ".csv"
            
            bhav_df1, bhav_df2  = pd.DataFrame(), pd.DataFrame()
            call_entry_price, call_exit_price = None, None
            call_entry_price1, call_exit_price1 = None, None
            call_entry_turnover, call_exit_turnover = None, None
            call_entry_turnover1, call_exit_turnover1 = None, None
            call_net, call_net1, total_net = None, None, None

            try:
                bhav_df1 = pd.read_csv(f"./cleaned_csvs/{fileName1}")
            except:
                reason = f"{fileName1} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, pd.NaT, fromDate, toDate)
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
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, pd.NaT, fromDate, toDate)
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
                call_entry_data =  bhav_df1[
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
                call_entry_data =  bhav_df1[
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
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, pd.NaT, fromDate, toDate)
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
            
            if call_exit_data.empty:
                reason = f"Call Exit Data missing for Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, pd.NaT, pd.NaT, fromDate, toDate)
                continue

            call_entry_price = call_entry_data.iloc[0]['Close']
            call_exit_price = call_exit_data.iloc[0]['Close']
            call_entry_turnover = call_entry_data.iloc[0]['TurnOver']
            call_exit_turnover = call_exit_data.iloc[0]['TurnOver']
            call_net =  round(call_entry_price -  call_exit_price, 2)
        
            total_net = call_net

            if protection:
                call_strike1 = round_half_up((call_strike*(1+(protection_pct*0.01)))/100)*100
                call_entry_data1 =  bhav_df1[
                                    (bhav_df1['Instrument']=="OPTIDX")
                                    & (bhav_df1['Symbol']=="NIFTY")
                                    & (bhav_df1['OptionType']=="CE")
                                    & (
                                        (bhav_df1['ExpiryDate']==curr_expiry)
                                        | (bhav_df1['ExpiryDate']==curr_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==curr_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['StrikePrice']>=call_strike1)
                                    & (bhav_df1['TurnOver']>0)
                                    & (bhav_df1['StrikePrice']%100==0)
                                ].sort_values(by='StrikePrice', ascending=True).reset_index(drop=True) 
                
                if call_entry_data1.empty:
                    reason = f"Call Protective Leg Entry Data above Strike {call_strike1} and Turnover>0 not found"
                    createLogFile("NIFTY", reason, curr_expiry, pd.NaT, pd.NaT, fromDate, toDate)
                    continue

                call_strike1 = call_entry_data1.iloc[0]['StrikePrice']
                call_entry_data1 = call_entry_data1[(call_entry_data1['StrikePrice']==call_strike1)]
                call_exit_data1 = bhav_df2[
                                    (bhav_df2['Instrument']=="OPTIDX")
                                    & (bhav_df2['Symbol']=="NIFTY")
                                    & (bhav_df2['OptionType']=="CE")
                                    & (
                                        (bhav_df2['ExpiryDate']==curr_expiry)
                                        | (bhav_df2['ExpiryDate']==curr_expiry-timedelta(days=1))
                                        |  (bhav_df2['ExpiryDate']==curr_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df2['StrikePrice']==call_strike1)
                                ]
                if call_exit_data1.empty:
                    reason = f"Call Protective Leg Exit Data for Strike {call_strike1} not found"
                    createLogFile("NIFTY", reason, curr_expiry, pd.NaT, pd.NaT, fromDate, toDate)
                    continue

                call_entry_price1 = call_entry_data1.iloc[0]['Close']
                call_entry_turnover1 = call_entry_data1.iloc[0]['TurnOver']
                call_exit_price1 = call_exit_data1.iloc[0]['Close']
                call_exit_turnover1 = call_exit_data1.iloc[0]['TurnOver']
                call_net1 = round(call_exit_price1-call_entry_price1, 2)
                total_net += call_net1
            
            if not protection:
                analysis_data.append({
                        "Entry Date" : fromDate,
                        "Exit Date" : toDate,
                        
                        "Entry Spot" : entrySpot,
                        "Exit Spot" : exitSpot,

                        "Call Expiry" : curr_expiry,
                        "Call Strike" : call_strike,
                        "Call EntryPrice": call_entry_price,
                        "Call Entry Turnover": call_entry_turnover,
                        "Call ExitPrice" : call_exit_price,
                        "Call Exit Turnover" : call_exit_turnover,
                        "Call P&L": call_net,

                        "Net P&L" : total_net,
                        
                    })
            else:
                analysis_data.append({
                        "Entry Date" : fromDate,
                        "Exit Date" : toDate,
                        
                        "Entry Spot" : entrySpot,
                        "Exit Spot" : exitSpot,

                        "Call Expiry" : curr_expiry,
                        "Call Strike" : call_strike,
                        "Call EntryPrice": call_entry_price,
                        "Call Entry Turnover": call_entry_turnover,
                        "Call ExitPrice" : call_exit_price,
                        "Call Exit Turnover" : call_exit_turnover,
                        "Call P&L": call_net,

                        "Protective Call Strike" : call_strike1,
                        "Protective EntryPrice": call_entry_price1,
                        "Protective Entry Turnover": call_entry_turnover1,
                        "Protective ExitPrice" : call_exit_price1,
                        "Protective Exit Turnover" : call_exit_turnover1,
                        "Protective P&L": call_net1,


                        "Net P&L" : total_net,
                        
                    })

    if analysis_data:
        if not protection:
            if t_2:
                path = "./Output/Call_Sell/Weekly/T-2 To T-2"
            else:
                path = "./Output/Call_Sell/Weekly/T-1 To T-1"
        else:
            if t_2:
                path = "./Output/Call_Sell_Protective_Call_Buy/Weekly/T-2 To T-2"
            else:
                path = "./Output/Call_Sell_Protective_Call_Buy/Weekly/T-1 To T-1"


        if call_sell_position==0:
            fileName = f"CE_ATM_Sell" 
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"

        if protection:
            fileName = fileName +f"_With_Protective_Call_{protection_pct}%_Buy"

        if(spot_adjustment_type==0):
            fileName = fileName + "_No_Adjustment"
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
            fileName = fileName + "_Weekly_T-2_To_T-2"
        else:
            fileName = fileName + "_Weekly_T-1_To_T-1"
        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        if not protection:
            if t_2:
                path = "./Output/Call_Sell/Weekly/T-2 To T-2"
            else:
                path = "./Output/Call_Sell/Weekly/T-1 To T-1"
        else:
            if t_2:
                path = "./Output/Call_Sell_Protective_Call_Buy/Weekly/T-2 To T-2"
            else:
                path = "./Output/Call_Sell_Protective_Call_Buy/Weekly/T-1 To T-1"


        if call_sell_position==0:
            fileName = f"CE_ATM_Sell" 
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"

        if protection:
            fileName = fileName +f"_With_Protective_Call_{protection_pct}%_Buy"

        if(spot_adjustment_type==0):
            fileName = fileName + "_No_Adjustment"
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
            fileName = fileName + "_Weekly_T-2_To_T-2"
        else:
            fileName = fileName + "_Weekly_T-1_To_T-1"
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


# Summary For Single Leg Put
def create_summary_idx_V5(df):
    entrySpot = df.iloc[0]['Entry Spot']
    first_entry_date = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d').min()
    last_exit_date = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d').max()
    number_of_years = (last_exit_date - first_entry_date).days / 365.25
    groups = {
        "Total Trades"  :   df,
    }

    stats_rows = []
    for label, subset in groups.items():
        count = len(subset)  
        total_sum = subset['Net P&L'].sum() if count>0 else None
        avg = (total_sum / count).round(2) if count > 0 else None
        
        win = len(subset[subset['Net P&L']>0]) if count>0 else None
        winPct = round((win/count * 100),2) if not pd.isna(win) else None
        avg_win = subset[subset['Net P&L']>0]['Net P&L'].mean() if not pd.isna(win) else None
        avg_win_pct = round(100*(avg_win/total_sum),2) if not pd.isna(win) else None
        avg_win = round(avg_win, 2) if not pd.isna(avg_win) else None
        
        lose = len(subset[subset['Net P&L']<0]) if count>0 else None
        losePct = round((lose/count * 100),2) if not pd.isna(lose) else None
        avg_loss = subset[subset['Net P&L']<0]['Net P&L'].mean() if not pd.isna(lose) else None
        avg_loss_pct = round(100*(avg_loss/total_sum),2) if  not pd.isna(lose) else None
        avg_loss = round(avg_loss, 2) if not pd.isna(avg_loss) else None

        expectancy = round(( ((avg_win_pct / abs(avg_loss_pct) ) * winPct) - losePct)/100, 2) if not pd.isna(win) and not pd.isna(lose) else None

        if count>0 and ((total_sum + entrySpot)/entrySpot) > 0:
            cagr_options = round(
                100 * (((total_sum + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (total_sum + entrySpot) > 0 else 0
        else:
            cagr_options = 0

        dd = subset['%DD'].min().round(2) if count>0 else None
        dd_points = subset['DD'].min().round(2) if count>0 else None
        Car_MDD = round(cagr_options/abs(dd), 2)
        recovery_factor = round(total_sum/abs(dd_points), 2)

        spot_chg = subset['Spot P&L'].sum()
        roi_vs_spot = round(100*(total_sum/spot_chg), 2) if spot_chg!=0 else None
        
        if count>0 and ((spot_chg + entrySpot) / entrySpot)>0:
            cagr_spot = round(
                100 * (((spot_chg + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (spot_chg + entrySpot) > 0 else 0

        else:
            cagr_spot = 0

        stats_rows.append([
                        label, count, total_sum, avg, 
                        winPct, avg_win, losePct, avg_loss, 
                        expectancy, cagr_options, 
                        dd, spot_chg, roi_vs_spot, 
                        cagr_spot, dd_points, Car_MDD,
                        recovery_factor
        ])
        
    stats_df = pd.DataFrame(stats_rows, columns=[
                                    "Category", "Count", "Sum", "Avg", 
                                    "W%", "Avg(W)", "L%", "Avg(L)",
                                    "Expectancy", "CAGR(Options)",
                                    "DD", "Spot Change", "ROI vs Spot",
                                    "CAGR(Spot)", "DD(Points)", "CAR/MDD",
                                    "Recovery Factor"

                                ])

    
    total_df = pd.DataFrame([
        ["Spot P&L", df["Spot P&L"].sum().round(2)],
        ["PE P&L", df["Put P&L"].sum().round(2)],
        ["PE+Spot P&L", (df["Net P&L"].sum() + df["Spot P&L"].sum()).round(2)],

    ], columns=["Type", "Sum"])

    return stats_df, total_df


def getPivotTable_V5(df):
    filtered_df = df[['Put Expiry', 'Net P&L']].copy(deep=True)
    header = ["Sum of Net P&L", "Total Points"]

    if filtered_df.empty:
        return pd.DataFrame(), [], pd.DataFrame(), []
    
    filtered_df['Month'] = pd.to_datetime(filtered_df['Put Expiry'], format='%Y-%m-%d').dt.strftime("%b")
    filtered_df['Year'] = pd.to_datetime(filtered_df['Put Expiry'], format='%Y-%m-%d').dt.year
    
    pivot_table = filtered_df.pivot_table(
        values = filtered_df.columns[1],  
        index = 'Year',  
        columns = 'Month', 
        aggfunc = 'sum'
    )
    
    month_order = [m for m in ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'] if m in pivot_table.columns]
    pivot_table = pivot_table[month_order]
    grand_total = ['Grand Total'] + [pivot_table[col].sum().round(2) for col in month_order]
    
    grand_total_df = pd.DataFrame([grand_total], columns=['Year'] + month_order)
    pivot_table = pd.concat([pivot_table, grand_total_df.set_index('Year')])
    pivot_table['Grand Total'] = pivot_table[month_order].sum(axis=1).round(2)
    pivot_table.reset_index(inplace=True)

    return pivot_table, header


def save_hypothetical_and_summary_idx_V5(df, filename="./df_final.xlsx"):
    stats_df, total_df = create_summary_idx_V5(df)
    pivot_table, header = getPivotTable_V5(df)
    
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df['Entry Date'] = df['Entry Date'].dt.date
        df['Exit Date'] = df['Exit Date'].dt.date
        df['Put Expiry'] = df['Put Expiry'].dt.date
        df.to_excel(writer, sheet_name="Hypothetical TradeSheet", index=False)

        start_row = 0
        for table in [stats_df, total_df]:
            table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row)
            start_row += len(table) + 2  
        start_row = start_row + 1

        header_df = pd.DataFrame([header])
        header_df.to_excel(writer, sheet_name="Summary", index=False, header=False, startrow=start_row)
        pivot_table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row+2)
        start_row += len(header_df) + len(pivot_table) + 3 


def summary_V5():
    main_path = "./Analysis/Data"
    all_folders = os.listdir(main_path)
    all_folders = [
        f for f in os.listdir(main_path)
        if os.path.isdir(os.path.join(main_path, f))
    ]

    for folder in all_folders:
        main_folders = os.listdir(os.path.join(main_path, folder))
        
        for f in main_folders:
            files = glob.glob(os.path.join(main_path, folder,f, "*.csv"), recursive=True)
            filtered_files = [
                    f for f in files 
                    if "log" not in f.lower() and "~$" not in f and "summary" not in f.lower()
                ]
            for file in filtered_files: 
                print(folder, file)
                df = pd.read_csv(file)
               
                try:
                    df['Put Expiry'] = pd.to_datetime(df['Put Expiry'], format='%Y-%m-%d')
                except:
                    df['Put Expiry'] = pd.to_datetime(df['Put Expiry'], format='%d-%m-%Y')

                try:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d')
                except:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%d-%m-%Y')
                    
                try:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d')
                except:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%d-%m-%Y')

                df = df[
                        (df['Put Expiry']>pd.Timestamp(2019,2,1))
                    ].sort_values(by=['Entry Date', 'Exit Date']).reset_index(drop=True)
                
                df['Hypothetical Put ExitPrice'] = np.where(
                                                df['Exit Date'] == df['Put Expiry'],
                                                    np.where(
                                                        df['Exit Spot'] > df['Put Strike'], 
                                                        0,                              
                                                        df['Put Strike']- df['Exit Spot']   
                                                    ),
                                                df['Put ExitPrice']
                                            )
                df['Put P&L'] = df['Put EntryPrice'] - df['Hypothetical Put ExitPrice']
                df.drop(columns=['Put ExitPrice'], inplace=True)
                
                df['Put P&L'] = df['Put P&L'].round(2)
                df['Net P&L'] = df['Put P&L']
                df['Net P&L/Spot Pct'] = round((df['Net P&L']/df['Entry Spot'])*100, 2)
                df['Spot P&L'] = round(df['Exit Spot'] - df['Entry Spot'], 2)
                

                df['Cumulative'] = None
                df.at[0, 'Cumulative'] = df.iloc[0]['Entry Spot'] + df.iloc[0]['Net P&L']
                
                for i in range(1, len(df)):
                    df.at[i, 'Cumulative'] = df.at[i-1, 'Cumulative'] + df.at[i, 'Net P&L']

                df['Peak'] = df['Cumulative'].cummax()
                df['DD'] = np.where(df['Peak']>df['Cumulative'], df['Cumulative']-df['Peak'], 0)
                df['Peak'] = df['Peak'].astype(float)
                df['DD'] = df['DD'].astype(float)
                df['%DD'] = np.where(df['DD']==0, 0, round(100*(df['DD']/df['Peak']),2))
                df['%DD'] = df['%DD'].round(2)
                
                df = df[[

                        'Entry Date', 'Exit Date', 
                        'Entry Spot', 'Exit Spot', 
                        'Spot P&L', 

                        'Put Expiry', 'Put Strike', 
                        'Put Entry Turnover', 'Put EntryPrice', 
                        'Put Exit Turnover', 'Hypothetical Put ExitPrice', 
                        'Put P&L', 

                        'Net P&L', 'Net P&L/Spot Pct',
                        'Cumulative', 'Peak', 'DD', '%DD'
                    ]]
                
                file = file.split(".csv")[0]
                file = file + "_Final_Summary" + ".xlsx"
                save_hypothetical_and_summary_idx_V5(df, file)
   
# Summary For Single Leg Call
def create_summary_idx_V5_Call(df):
    entrySpot = df.iloc[0]['Entry Spot']
    first_entry_date = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d').min()
    last_exit_date = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d').max()
    number_of_years = (last_exit_date - first_entry_date).days / 365.25
    groups = {
        "Total Trades"  :   df,
    }

    stats_rows = []
    for label, subset in groups.items():
        count = len(subset)  
        total_sum = subset['Net P&L'].sum() if count>0 else None
        avg = (total_sum / count).round(2) if count > 0 else None
        
        win = len(subset[subset['Net P&L']>0]) if count>0 else None
        winPct = round((win/count * 100),2) if not pd.isna(win) else None
        avg_win = subset[subset['Net P&L']>0]['Net P&L'].mean() if not pd.isna(win) else None
        avg_win_pct = round(100*(avg_win/total_sum),2) if not pd.isna(win) else None
        avg_win = round(avg_win, 2) if not pd.isna(avg_win) else None
        
        lose = len(subset[subset['Net P&L']<0]) if count>0 else None
        losePct = round((lose/count * 100),2) if not pd.isna(lose) else None
        avg_loss = subset[subset['Net P&L']<0]['Net P&L'].mean() if not pd.isna(lose) else None
        avg_loss_pct = round(100*(avg_loss/total_sum),2) if  not pd.isna(lose) else None
        avg_loss = round(avg_loss, 2) if not pd.isna(avg_loss) else None

        expectancy = round(( ((avg_win_pct / abs(avg_loss_pct) ) * winPct) - losePct)/100, 2) if not pd.isna(win) and not pd.isna(lose) else None

        if count>0 and ((total_sum + entrySpot)/entrySpot) > 0:
            cagr_options = round(
                100 * (((total_sum + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (total_sum + entrySpot) > 0 else 0
        else:
            cagr_options = 0

        dd = subset['%DD'].min().round(2) if count>0 else None
        dd_points = subset['DD'].min().round(2) if count>0 else None
        Car_MDD = round(cagr_options/abs(dd), 2)
        recovery_factor = round(total_sum/abs(dd_points), 2)

        spot_chg = subset['Spot P&L'].sum()
        roi_vs_spot = round(100*(total_sum/spot_chg), 2) if spot_chg!=0 else None
        
        if count>0 and ((spot_chg + entrySpot) / entrySpot)>0:
            cagr_spot = round(
                100 * (((spot_chg + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (spot_chg + entrySpot) > 0 else 0

        else:
            cagr_spot = 0

        stats_rows.append([
                        label, count, total_sum, avg, 
                        winPct, avg_win, losePct, avg_loss, 
                        expectancy, cagr_options, 
                        dd, spot_chg, roi_vs_spot, 
                        cagr_spot, dd_points, Car_MDD,
                        recovery_factor
        ])
        
    stats_df = pd.DataFrame(stats_rows, columns=[
                                    "Category", "Count", "Sum", "Avg", 
                                    "W%", "Avg(W)", "L%", "Avg(L)",
                                    "Expectancy", "CAGR(Options)",
                                    "DD", "Spot Change", "ROI vs Spot",
                                    "CAGR(Spot)", "DD(Points)", "CAR/MDD",
                                    "Recovery Factor"

                                ])

    
    total_df = pd.DataFrame([
        ["Spot P&L", df["Spot P&L"].sum().round(2)],
        ["CE P&L", df["Net P&L"].sum().round(2)],
        ["CE+Spot P&L", (df["Net P&L"].sum() + df["Spot P&L"].sum()).round(2)],

    ], columns=["Type", "Sum"])

    return stats_df, total_df


def getPivotTable_V5_Call(df):
    filtered_df = df[['Call Expiry', 'Net P&L']].copy(deep=True)
    header = ["Sum of Net P&L", "Total Points"]

    if filtered_df.empty:
        return pd.DataFrame(), [], pd.DataFrame(), []
    
    filtered_df['Month'] = pd.to_datetime(filtered_df['Call Expiry'], format='%Y-%m-%d').dt.strftime("%b")
    filtered_df['Year'] = pd.to_datetime(filtered_df['Call Expiry'], format='%Y-%m-%d').dt.year
    
    pivot_table = filtered_df.pivot_table(
        values = filtered_df.columns[1],  
        index = 'Year',  
        columns = 'Month', 
        aggfunc = 'sum'
    )
    
    month_order = [m for m in ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'] if m in pivot_table.columns]
    pivot_table = pivot_table[month_order]
    grand_total = ['Grand Total'] + [pivot_table[col].sum().round(2) for col in month_order]
    
    grand_total_df = pd.DataFrame([grand_total], columns=['Year'] + month_order)
    pivot_table = pd.concat([pivot_table, grand_total_df.set_index('Year')])
    pivot_table['Grand Total'] = pivot_table[month_order].sum(axis=1).round(2)
    pivot_table.reset_index(inplace=True)

    return pivot_table, header


def save_hypothetical_and_summary_idx_V5_Call(df, filename="./df_final.xlsx"):
    stats_df, total_df = create_summary_idx_V5_Call(df)
    pivot_table, header = getPivotTable_V5_Call(df)
    
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df['Entry Date'] = df['Entry Date'].dt.date
        df['Exit Date'] = df['Exit Date'].dt.date
        df['Call Expiry'] = df['Call Expiry'].dt.date
        df.to_excel(writer, sheet_name="Hypothetical TradeSheet", index=False)

        start_row = 0
        for table in [stats_df, total_df]:
            table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row)
            start_row += len(table) + 2  
        start_row = start_row + 1

        header_df = pd.DataFrame([header])
        header_df.to_excel(writer, sheet_name="Summary", index=False, header=False, startrow=start_row)
        pivot_table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row+2)
        start_row += len(header_df) + len(pivot_table) + 3 


def summary_V5_Call():
    main_path = "./Analysis/Output/Weekly"
    all_folders = os.listdir(main_path)
    all_folders = [
        f for f in os.listdir(main_path)
        if os.path.isdir(os.path.join(main_path, f))
    ]
    for folder in all_folders:
        main_folders = os.listdir(os.path.join(main_path, folder))
        for f in main_folders:
            files = glob.glob(os.path.join(main_path, folder,f), recursive=True)
            filtered_files = [
                    f for f in files 
                    if "log" not in f.lower() and "~$" not in f and "summary" not in f.lower()
                ]
            for file in filtered_files: 
                print(folder, file)
                df = pd.read_csv(file)
               
                try:
                    df['Call Expiry'] = pd.to_datetime(df['Call Expiry'], format='%Y-%m-%d')
                except:
                    df['Call Expiry'] = pd.to_datetime(df['Call Expiry'], format='%d-%m-%Y')

                try:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d')
                except:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%d-%m-%Y')
                    
                try:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d')
                except:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%d-%m-%Y')

                df = df[
                        (df['Call Expiry']>pd.Timestamp(2019,2,1))
                    ].sort_values(by=['Entry Date', 'Exit Date']).reset_index(drop=True)
                
                df['Hypothetical Call ExitPrice'] = np.where(
                                                df['Exit Date'] == df['Call Expiry'],
                                                    np.where(
                                                        df['Exit Spot'] < df['Call Strike'], 
                                                        0,                              
                                                        df['Exit Spot'] - df['Call Strike'] 
                                                    ),
                                                df['Call ExitPrice']
                                            )
                df['Call P&L'] = df['Call EntryPrice'] - df['Hypothetical Call ExitPrice']
                df.drop(columns=['Call ExitPrice'], inplace=True)
                
                df['Call P&L'] = df['Call P&L'].round(2)
                df['Net P&L'] = df['Call P&L']
                df['Net P&L/Spot Pct'] = round((df['Net P&L']/df['Entry Spot'])*100, 2)
                df['Spot P&L'] = round(df['Exit Spot'] - df['Entry Spot'], 2)
                

                df['Cumulative'] = None
                df.at[0, 'Cumulative'] = df.iloc[0]['Entry Spot'] + df.iloc[0]['Net P&L']
                
                for i in range(1, len(df)):
                    df.at[i, 'Cumulative'] = df.at[i-1, 'Cumulative'] + df.at[i, 'Net P&L']

                df['Peak'] = df['Cumulative'].cummax()
                df['DD'] = np.where(df['Peak']>df['Cumulative'], df['Cumulative']-df['Peak'], 0)
                df['Peak'] = df['Peak'].astype(float)
                df['DD'] = df['DD'].astype(float)
                df['%DD'] = np.where(df['DD']==0, 0, round(100*(df['DD']/df['Peak']),2))
                df['%DD'] = df['%DD'].round(2)
                
                df = df[[

                        'Entry Date', 'Exit Date', 
                        'Entry Spot', 'Exit Spot', 
                        'Spot P&L', 

                        'Call Expiry', 'Call Strike', 
                        'Call Entry Turnover', 'Call EntryPrice', 
                        'Call Exit Turnover', 'Hypothetical Call ExitPrice', 
                        'Call P&L', 

                        'Net P&L', 'Net P&L/Spot Pct',
                        'Cumulative', 'Peak', 'DD', '%DD'
                    ]]
                
                file = file.split(".csv")[0]
                file = file + "_Final_Summary" + ".xlsx"
                save_hypothetical_and_summary_idx_V5_Call(df, file)
  

# Call Sell Put Sell - Base2 Bear
def main1_V6(spot_adjustment_type=0, spot_adjustment=1, call_sell_position=0, put_sell_position=0):
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
        sys.exit("Base File Issue.")

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

            print(f"Call Sell Put Sell Expiry To Expiry From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            if entrySpot.empty:
                continue
            entrySpot = entrySpot.iloc[0]['Close']

            exitSpot =  filtered_data[filtered_data['Date']==toDate] 
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None

            call_strike = round_half_up((entrySpot*(1+(call_sell_position/100)))/100)*100
            put_strike = round_half_up((entrySpot*(1+(put_sell_position/100)))/100)*100
            
            fileName1 = fromDate.strftime("%Y-%m-%d") + ".csv"
            fileName2 = toDate.strftime("%Y-%m-%d") + ".csv"

            bhav_df1, bhav_df2  = pd.DataFrame(), pd.DataFrame()
            call_entry_price, call_exit_price = None, None
            call_entry_turnover, call_exit_turnover = None, None
            put_entry_price, put_exit_price = None, None
            put_entry_turnover, put_exit_turnover = None, None
            call_net, put_net, total_net = None, None, None

            try:
                bhav_df1 = pd.read_csv(f"./cleaned_csvs/{fileName1}")
            except:
                reason = f"{fileName1} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
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
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
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
                reason = f"No Strike Found Near {call_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
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
                if(call_entry_data.empty):
                    reason = f"Call Entry Data missing for Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Call Exit Data missing for Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
                continue

            if put_sell_position>=0:
                put_entry_data =  bhav_df1[
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
                put_entry_data =  bhav_df1[
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
                reason = f"No Strike Found Near {put_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
                continue

            put_strike = put_entry_data.iloc[0]['StrikePrice']
            put_entry_data = put_entry_data[(put_entry_data['StrikePrice']==put_strike)]
            
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
                            ]
            
            if put_entry_data.empty or put_exit_data.empty:
                if(put_entry_data.empty):
                    reason = f"Put Entry Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Put Exit Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
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

                    "Put Expiry" : curr_expiry,
                    "Put Strike" : put_strike,
                    "Put EntryPrice": put_entry_price,
                    "Put Entry Turnover": put_entry_turnover,
                    "Put ExitPrice" : put_exit_price,
                    "Put Exit Turnover" : put_exit_turnover,
                    "Put P&L": put_net,

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
        path = "./Output/Call_Sell_Put_Sell_Bear/Weekly/Expiry To Expiry"

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
        
        fileName = fileName + "_Weekly_Expiry-To-Expiry"
        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        path = "./Output/Call_Sell_Put_Sell_Bear/Weekly/Expiry To Expiry"

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
        
        fileName = fileName + "_Weekly_Expiry-To-Expiry"
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


def main2_V6(spot_adjustment_type=0, spot_adjustment=1, call_sell_position=0, put_sell_position=0, t_2=False):
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
        sys.exit("Base File Issue.")
    
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
        
        elif(first_instance):
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
                        or ((spot_adjustment_type==2) and (roc<=(-spot_adjustment)))
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

            if fromDate==toDate:
                continue
    
            if t_2:
                print(f"Call Sell Put Sell T-2 to T-2 Weekly From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            else:
                print(f"Call Sell Put Sell T-1 to T-1 Weekly From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")
            
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            if entrySpot.empty:
                continue
            entrySpot = entrySpot.iloc[0]['Close']
            
            exitSpot =  filtered_data[filtered_data['Date']==toDate]
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None
            
            call_strike = round_half_up((entrySpot*(1+(call_sell_position/100)))/100)*100
            put_strike = round_half_up((entrySpot*(1+(put_sell_position/100)))/100)*100
            
            fileName1 = fromDate.strftime("%Y-%m-%d") + ".csv"
            fileName2 = toDate.strftime("%Y-%m-%d") + ".csv"
            
            bhav_df1, bhav_df2  = pd.DataFrame(), pd.DataFrame()   
            call_entry_price, call_exit_price = None, None
            call_entry_turnover, call_exit_turnover = None, None
            put_entry_price, put_exit_price = None, None
            put_entry_turnover, put_exit_turnover = None, None
            call_net, put_net, total_net = None, None, None

            try:
                bhav_df1 = pd.read_csv(f"./cleaned_csvs/{fileName1}")
            except:
                reason = f"{fileName1} not found in cleaned_csvs. Skipping the Trade"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
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
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
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

            
            if call_entry_data.empty:
                reason = f"Call Data for Strike Near {call_strike} and Turnover>0 not found"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
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
                if(call_entry_data.empty):
                    reason = f"Call Entry Data missing for Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Call Exit Data missing for Strike {int(call_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
                continue
        
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

            
            if put_entry_data.empty:
                reason = f"Put Data for Strike Near {put_strike} and Turnover>0 not found"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
                continue

            put_strike = put_entry_data.iloc[0]['StrikePrice']
            put_entry_data = put_entry_data[(put_entry_data['StrikePrice']==put_strike)]

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
                            ]
            
            if put_entry_data.empty or put_exit_data.empty:
                if(put_entry_data.empty):
                    reason = f"Put Entry Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                else:
                    reason = f"Put Exit Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, pd.NaT, fromDate, toDate)
                continue
        
            call_entry_price = call_entry_data.iloc[0]['Close']
            call_entry_turnover = call_entry_data.iloc[0]['TurnOver']
            call_exit_price = call_exit_data.iloc[0]['Close']
            call_exit_turnover = call_exit_data.iloc[0]['TurnOver']
            call_net =  round(call_entry_price -  call_exit_price, 2)
        
            
            put_entry_price = put_entry_data.iloc[0]['Close']
            put_entry_turnover = put_entry_data.iloc[0]['TurnOver']
            put_exit_price = put_exit_data.iloc[0]['Close']
            put_exit_turnover = put_exit_data.iloc[0]['TurnOver']
            put_net =  round(put_entry_price -  put_exit_price, 2)
        
            total_net = round(call_net + put_net, 2)

            analysis_data.append({
                    "Entry Date" : fromDate,
                    "Exit Date" : toDate,
                    
                    "Entry Spot" : entrySpot,
                    "Exit Spot" : exitSpot,

                    "Put Expiry" : curr_expiry,
                    "Put Strike" : put_strike,
                    "Put EntryPrice": put_entry_price,
                    "Put Entry Turnover" : put_entry_turnover,
                    "Put ExitPrice" : put_exit_price,
                    "Put Exit Turnover": put_exit_turnover,
                    "Put P&L": put_net,

                    "Call Expiry" : curr_expiry,
                    "Call Strike" : call_strike,
                    "Call EntryPrice" : call_entry_price,
                    "Call Entry Turnover" : call_entry_turnover,
                    "Call ExitPrice" : call_exit_price,
                    "Call Exit Turnover": call_exit_turnover,
                    "Call P&L" : call_net,

                    "Net P&L" : total_net,
                    
                })
        
      
    if analysis_data:
        if t_2:
            path = "./Output/Call_Sell_Put_Sell_Bear/Weekly/T-2 To T-2"
        else:
            path = "./Output/Call_Sell_Put_Sell_Bear/Weekly/T-1 To T-1"
        
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
            fileName = fileName + "_Weekly_Expiry_T-2_To_T-2"
        else:
            fileName = fileName + "_Weekly_Expiry_T-1_To_T-1"
        
        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")

    if logFile:
        if t_2:
            path = "./Output/Call_Sell_Put_Sell_Bear/Weekly/T-2 To T-2"
        else:
            path = "./Output/Call_Sell_Put_Sell_Bear/Weekly/T-1 To T-1"
        
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
            fileName = fileName + "_Weekly_Expiry_T-2_To_T-2"
        else:
            fileName = fileName + "_Weekly_Expiry_T-1_To_T-1"
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


# Call Short Put Long Future Long
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


# Premium Based (Call, Put, Call_Put) Call or/and Put OTM 
# Sell in Base2 Bull
def main1_V7(
        spot_adjustment_type=0, spot_adjustment=1, 
        call_premium=True, put_premium=True, 
        premium_multiplier=1, 
        call_sell=True, put_sell=True
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
            path = "./Output/Straddle_Bull/Weekly/Expiry To Expiry"
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
            path = "./Output/Straddle_Call_Only_Bull/Weekly/Expiry To Expiry"
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
            path = "./Output/Straddle_Put_Only_Bull/Weekly/Expiry To Expiry"
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
            fileName = fileName + "_Call_OTM_Sell_Put_OTM_Sell"
        elif call_sell:
            fileName = fileName + "_Call_OTM_Sell"
        else:
            fileName = fileName + "_Put_OTM_Sell"

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

# Premium Based (Call, Put, Call_Put) Call or/and Put OTM 
# Sell Base2 Bear
def main1_V8(
        spot_adjustment_type=0, spot_adjustment=1, 
        call_premium=True, put_premium=True, 
        premium_multiplier=1, 
        call_sell=True, put_sell=True
):
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
        sys.exit("Base File Issue.")

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
            path = "./Output/Straddle_Bear/Weekly/Expiry To Expiry"
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
            path = "./Output/Straddle_Call_Only_Bear/Weekly/Expiry To Expiry"
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
            path = "./Output/Straddle_Put_Only_Bear/Weekly/Expiry To Expiry"
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
            path = "./Output/Straddle_Bear/Weekly/Expiry To Expiry"
        elif call_sell:
            path = "./Output/Straddle_Call_Only_Bear/Weekly/Expiry To Expiry"
        else:
            path = "./Output/Straddle_Put_Only_Bear/Weekly/Expiry To Expiry"

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
            
        fileName = fileName + "_Log"
        os.makedirs(path, exist_ok=True)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


def main2_V8(spot_adjustment_type=0, spot_adjustment=1, 
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
        sys.exit("Base File Issue.")
        
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
            path = "./Output/Straddle_Bear/Weekly"
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
            path = "./Output/Straddle_Call_Only_Bear/Weekly"
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
            path = "./Output/Straddle_Put_Only_Bear/Weekly"
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
            path = path +"/T-2 To T-2"
        else:
            fileName = fileName + "_T-1_To_T-1"
            path = path +"/T-1 To T-2"

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
            path = "./Output/Straddle_Bear/Weekly"
        elif call_sell:
            path = "./Output/Straddle_Call_Only_Bear/Weekly"
        else:
            path = "./Output/Straddle_Put_Only_Bear/Weekly"

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
            path = path +"/T-2 To T-2"
        else:
            fileName = fileName + "_T-1_To_T-1"
            path = path +"/T-1 To T-2"

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


# V7 Premium Based; HSL Adjustment
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


def main2_V7_With_HSL(
        call_premium=True, put_premium=True, 
        premium_multiplier=1, 
        call_sell=True, put_sell=True,
        call_hsl_pct = 100, put_hsl_pct = 100,
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


        all_dates = filtered_data['Date'].unique()
        call_entry_data, put_entry_data = pd.DataFrame(), pd.DataFrame()
        temp_dict = {}
        call_strike, put_strike = None, None
        fromDate = filtered_data.iloc[0]['Date']
        toDate = filtered_data.iloc[-1]['Date']
        call_flag, put_flag = False, False

        if t_2:
            print("T-2 To T-2", end=" ")
        else:
            print("T-1 To T-1", end=" ")

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
                            i = len(all_dates)
                
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
            path = "./Output/Straddle_Bull/Weekly"
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
            path = "./Output/Straddle_Call_Only_Bull/Weekly"
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
            path = "./Output/Straddle_Put_Only_Bull/Weekly"
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
        
        if t_2:
            fileName = fileName + "_T-2_To_T-2"
            path = path + "/T-2 To T-2"
        else:
            fileName = fileName + "_T-1_To_T-1"
            path = path + "/T-1 To T-1"
        
        path = path + "/HSL Adjustment"
        os.makedirs(path, exist_ok=True)
        analysis_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")
    
    if logFile:
        log_df = pd.DataFrame(logFile)
        if call_sell and put_sell:
            path = "./Output/Straddle_Bull/Weekly"
        elif call_sell:
            path = "./Outputy/Straddle_Call_Only_Bull/Weekly"
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
            fileName = fileName + "_Call_OTM_Sell_Put_OTM_Sell" + f"_{call_hsl_pct}%_Call_HSL_{put_hsl_pct}%_Put_HSL_Adjustment"
        elif call_sell:
            fileName = fileName + "_Call_OTM_Sell" + f"_{call_hsl_pct}%_Call_HSL_Adjustment"
        else:
            fileName = fileName + "_Put_OTM_Sell"  + f"_{call_hsl_pct}%_Put_HSL_Adjustment"
        
        if t_2:
            fileName = fileName + "_T-2_To_T-2"
            path = path + "/T-2 To T-2"
        else:
            fileName = fileName + "_T-1_To_T-1"
            path = path + "/T-1 To T-1"
        
        path = path + "/HSL Adjustment"
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Log"
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


# Summary For CE+PE+FUT
def create_summary_idx_alt(df):
    entrySpot = df.iloc[0]['Entry Spot']
    first_entry_date = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d').min()
    last_exit_date = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d').max()
    number_of_years = (last_exit_date - first_entry_date).days / 365.25
    
    groups = {
        "Total Trades"  :   df,
    }
    
    stats_rows = []
    for label, subset in groups.items():
        count = len(subset)  
        total_sum = subset['Net P&L'].sum() if count>0 else None
        avg = (total_sum / count).round(2) if count > 0 else None
        
        win = len(subset[subset['Net P&L']>0]) if count>0 else None
        winPct = round((win/count * 100),2) if not pd.isna(win) else None
        avg_win = subset[subset['Net P&L']>0]['Net P&L'].mean() if not pd.isna(win) else None
        avg_win_pct = round(100*(avg_win/total_sum),2) if not pd.isna(win) else None
        avg_win = round(avg_win, 2) if not pd.isna(avg_win) else None
        
        lose = len(subset[subset['Net P&L']<0]) if count>0 else None
        losePct = round((lose/count * 100),2) if not pd.isna(lose) else None
        avg_loss = subset[subset['Net P&L']<0]['Net P&L'].mean() if not pd.isna(lose) else None
        avg_loss_pct = round(100*(avg_loss/total_sum),2) if  not pd.isna(lose) else None
        avg_loss = round(avg_loss, 2) if not pd.isna(avg_loss) else None

        expectancy = round(( ((avg_win_pct / abs(avg_loss_pct) ) * winPct) - losePct)/100, 2) if not pd.isna(win) and not pd.isna(lose) else None

        if count>0 and ((total_sum + entrySpot)/entrySpot) > 0:
            cagr_options = round(
                100 * (((total_sum + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (total_sum + entrySpot) > 0 else 0
        else:
            cagr_options = 0

        dd = subset['%DD'].min().round(2) if count>0 else None
        dd_points = subset['DD'].min().round(2) if count>0 else None
        Car_MDD = round(cagr_options/abs(dd), 2)
        recovery_factor = round(total_sum/abs(dd_points), 2)

        spot_chg = subset['Spot P&L'].sum()
        roi_vs_spot = round(100*(total_sum/spot_chg), 2) if spot_chg!=0 else None
        
        if count>0 and ((spot_chg + entrySpot) / entrySpot)>0:
            cagr_spot = round(
                100 * (((spot_chg + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (spot_chg + entrySpot) > 0 else 0

        else:
            cagr_spot = 0

        stats_rows.append([
                        label, count, total_sum, avg, 
                        winPct, avg_win, losePct, avg_loss, 
                        expectancy, cagr_options, 
                        dd, spot_chg, roi_vs_spot, 
                        cagr_spot, dd_points, Car_MDD,
                        recovery_factor
        ])
        
    stats_df = pd.DataFrame(stats_rows, columns=[
                                    "Category", "Count", "Sum", "Avg", 
                                    "W%", "Avg(W)", "L%", "Avg(L)",
                                    "Expectancy", "CAGR(Options)",
                                    "DD", "Spot Change", "ROI vs Spot",
                                    "CAGR(Spot)", "DD(Points)", "CAR/MDD",
                                    "Recovery Factor"

                                ])

    
    total_df = pd.DataFrame([
        ["Spot P&L", df["Spot P&L"].sum().round(2)],
        ["Fut P&L", df["Future P&L"].sum().round(2)],
        ["CE P&L", df["Call P&L"].sum().round(2)],
        ["PE P&L", df["Put P&L"].sum().round(2)],
        ['CE+PE P&L', round(df["Call P&L"].sum() + df['Put P&L'].sum(), 2)],
        ["CE+PE+Fut P&L", df["Net P&L"].sum().round(2)],
        ["CE+PE+Fut+Spot P&L", (df["Net P&L"].sum() + df["Spot P&L"].sum()).round(2)],
    ], columns=["Type", "Sum"])

    return stats_df, total_df


def getPivotTable_alt(df):
    filtered_df = df[['Future Expiry', 'Net P&L']].copy(deep=True)
    header = ["Sum of Net P&L", "Total Points"]

    if filtered_df.empty:
        return pd.DataFrame(), [], pd.DataFrame(), []
    
    filtered_df['Month'] = pd.to_datetime(filtered_df['Future Expiry'], format='%Y-%m-%d').dt.strftime("%b")
    filtered_df['Year'] = pd.to_datetime(filtered_df['Future Expiry'], format='%Y-%m-%d').dt.year
    
    pivot_table = filtered_df.pivot_table(
        values = filtered_df.columns[1],  
        index = 'Year',  
        columns = 'Month', 
        aggfunc = 'sum'
    )
    month_order = [m for m in ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'] if m in pivot_table.columns]
    pivot_table = pivot_table[month_order]
    grand_total = ['Grand Total'] + [pivot_table[col].sum().round(2) for col in month_order]
    grand_total_df = pd.DataFrame([grand_total], columns=['Year'] + month_order)
    pivot_table = pd.concat([pivot_table, grand_total_df.set_index('Year')])
    pivot_table['Grand Total'] = pivot_table[month_order].sum(axis=1).round(2)
    pivot_table.reset_index(inplace=True)

    return pivot_table, header


def save_hypothetical_and_summary_idx_alt(df, filename="./df_final.xlsx"):
    stats_df, total_df = create_summary_idx_alt(df)
    pivot_table, header = getPivotTable_alt(df)
    
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df['Entry Date'] = df['Entry Date'].dt.date
        df['Exit Date'] = df['Exit Date'].dt.date
        df['Call Expiry'] = df['Call Expiry'].dt.date
        df['Put Expiry'] = df['Put Expiry'].dt.date
        df['Future Expiry'] = df['Future Expiry'].dt.date
        df.to_excel(writer, sheet_name="Hypothetical TradeSheet", index=False)

        start_row = 0
        for table in [stats_df, total_df]:
            table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row)
            start_row += len(table) + 2  
        start_row = start_row + 1

        header_df = pd.DataFrame([header])
        header_df.to_excel(writer, sheet_name="Summary", index=False, header=False, startrow=start_row)
        pivot_table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row+2)
        start_row += len(header_df) + len(pivot_table) + 3 


def summary_alt():
    main_path = "./Analysis/Data"
    all_folders = os.listdir(main_path)
    all_folders = [
        f for f in os.listdir(main_path)
        if os.path.isdir(os.path.join(main_path, f))
    ]

    for folder in all_folders:
        main_folders = os.listdir(os.path.join(main_path, folder))
        
        for f in main_folders:
            files = glob.glob(os.path.join(main_path, folder,f, "*.csv"), recursive=True)
            filtered_files = [
                    f for f in files 
                    if "log" not in f.lower() and "~$" not in f and "summary" not in f.lower()
                ]
            
            for file in filtered_files: 
                print(folder, file)
                df = pd.read_csv(file)
                try:
                    df['Future Expiry'] = pd.to_datetime(df['Future Expiry'], format='%Y-%m-%d')
                except:
                    df['Future Expiry'] = pd.to_datetime(df['Future Expiry'], format='%d-%m-%Y')

                try:
                    df['Call Expiry'] = pd.to_datetime(df['Call Expiry'], format='%Y-%m-%d')
                except:
                    df['Call Expiry'] = pd.to_datetime(df['Call Expiry'], format='%d-%m-%Y')

                try:
                    df['Put Expiry'] = pd.to_datetime(df['Put Expiry'], format='%Y-%m-%d')
                except:
                    df['Put Expiry'] = pd.to_datetime(df['Put Expiry'], format='%d-%m-%Y')

                try:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d')
                except:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%d-%m-%Y')
                    
                try:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d')
                except:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%d-%m-%Y')

                df = df[
                        (df['Call Expiry']>pd.Timestamp(2019,2,1))
                    ].sort_values(by=['Entry Date', 'Exit Date']).reset_index(drop=True)
                
                df['Hypothetical Call ExitPrice'] = np.where(
                                                df['Exit Date'] == df['Call Expiry'],
                                                    np.where(
                                                        df['Exit Spot'] < df['Call Strike'], 
                                                        df['Call ExitPrice'].fillna(0),                              
                                                        df['Exit Spot'] - df['Call Strike']  
                                                    ),
                                                df['Call ExitPrice']
                                            )
                df['Call P&L'] = df['Call EntryPrice'] - df['Hypothetical Call ExitPrice']
                df['Call P&L'] = df['Call P&L'].round(2)
                df.drop(columns=['Call ExitPrice'], inplace=True)
                
                df['Hypothetical Put ExitPrice'] = np.where(
                                                df['Exit Date'] == df['Put Expiry'],
                                                    np.where(
                                                        df['Exit Spot'] > df['Put Strike'], 
                                                        0,                              
                                                        df['Put Strike'] - df['Exit Spot']  
                                                    ),
                                                df['Put ExitPrice']
                                            )
                df['Put P&L'] = df['Hypothetical Put ExitPrice'] - df['Put EntryPrice']
                df['Put P&L'] = df['Put P&L'].round(2)
                df.drop(columns=['Put ExitPrice'], inplace=True)

                df['Spot P&L'] = df['Exit Spot'] - df['Entry Spot']
                df['Net P&L'] = df['Future P&L'] + df['Call P&L']  + df['Put P&L']

                df['Net P&L/Spot Pct'] = round((df['Net P&L']/df['Entry Spot'])*100, 2)
                
                df['Cumulative'] = None
                df.at[0, 'Cumulative'] = df.iloc[0]['Entry Spot'] + df.iloc[0]['Net P&L']
                for i in range(1, len(df)):
                    df.at[i, 'Cumulative'] = df.at[i-1, 'Cumulative'] + df.at[i, 'Net P&L']

                df['Peak'] = df['Cumulative'].cummax()
                df['DD'] = np.where(df['Peak']>df['Cumulative'], df['Cumulative']-df['Peak'], 0)
                df['Peak'] = df['Peak'].astype(float)
                df['DD'] = df['DD'].astype(float)
                df['%DD'] = np.where(df['DD']==0, 0, round(100*(df['DD']/df['Peak']),2))
                df['%DD'] = df['%DD'].round(2)
                
                df = df[[

                        'Entry Date', 'Exit Date', 
                        'Entry Spot', 'Exit Spot', 
                        'Spot P&L', 

                        'Future Expiry',
                        'Future EntryPrice', 'Future ExitPrice', 
                        'Future P&L',  
                        
                        'Call Expiry', 'Call Strike', 
                        'Call Entry Turnover', 'Call EntryPrice', 
                        'Call Exit Turnover', 'Hypothetical Call ExitPrice', 
                        'Call P&L', 

                        'Put Expiry', 'Put Strike', 
                        'Put Entry Turnover', 'Put EntryPrice', 
                        'Put Exit Turnover', 'Hypothetical Put ExitPrice', 
                        'Put P&L', 

                        'Net P&L', 'Net P&L/Spot Pct',
                        
                        'Cumulative', 'Peak', 'DD', '%DD'
                    ]]
                file = file.split(".csv")[0]
                file = file + "_Final_Summary" + ".xlsx"
                save_hypothetical_and_summary_idx_alt(df, file)

# Weekly Only
# Put ITM Sell of Weekly Expiry instead of Future Buy of Monthly Expiry
# Call Sell Put Sell
# Base 2 Bull
# With Call and Put HSL
def main1_V4_With_HSL(call_sell_position=0, put_sell_position=0, call_hsl_pct=100, put_hsl_pct=100):
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
        
        fromDate = filtered_data.iloc[0]['Date']
        toDate = filtered_data.iloc[-1]['Date']
        print("Expiry To Expiry", end= " ")
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
        path = "./Output/Call_Sell_Put_Sell_Bull/Weekly/Expiry To Expiry/HSL Adjustment"
        
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
        
        fileName = fileName + "_Weekly_Expiry-To-Expiry"
        fileName = fileName + f"_Call_HSL_{call_hsl_pct}%_Put_HSL_{put_hsl_pct}%"
        os.makedirs(path, exist_ok=True)
        
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df['Net P&L'] = analyse_df['Call P&L'] + analyse_df['Put P&L']
        analyse_df['Net P&L'] = analyse_df['Net P&L'].round(2)
        
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        path = "./Output/Call_Sell_Put_Sell_Bull/Weekly/Expiry To Expiry/HSL Adjustment"
        
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
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Weekly_Expiry-To-Expiry"
        fileName = fileName + f"_Call_HSL_{call_hsl_pct}%_Put_HSL_{put_hsl_pct}%"
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


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


def main1_V4_With_HSL_Bear(call_sell_position=0, put_sell_position=0, call_hsl_pct=100, put_hsl_pct=100):
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
        sys.exit("Issue with Base File")

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
        
        fromDate = filtered_data.iloc[0]['Date']
        toDate = filtered_data.iloc[-1]['Date']
        print("Expiry To Expiry", end= " ")
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
        path = "./Output/Call_Sell_Put_Sell_Bear/Weekly/Expiry To Expiry/HSL Adjustment"
        
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
        
        fileName = fileName + "_Weekly_Expiry-To-Expiry"
        fileName = fileName + f"_Call_HSL_{call_hsl_pct}%_Put_HSL_{put_hsl_pct}%"
        os.makedirs(path, exist_ok=True)
        
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df['Net P&L'] = analyse_df['Call P&L'] + analyse_df['Put P&L']
        analyse_df['Net P&L'] = analyse_df['Net P&L'].round(2)
        
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        path = "./Output/Call_Sell_Put_Sell_Bear/Weekly/Expiry To Expiry/HSL Adjustment"
        
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
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Weekly_Expiry-To-Expiry"
        fileName = fileName + f"_Call_HSL_{call_hsl_pct}%_Put_HSL_{put_hsl_pct}%"
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


def main2_V4_With_HSL_Bear(call_sell_position=0, put_sell_position=0, call_hsl_pct=100, put_hsl_pct=100, t_2=False):
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
        sys.exit("Issue with base file")

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
            path = "./Output/Call_Sell_Put_Sell_Bear/Weekly/T-2 To T-2/HSL Adjustment"
        else:
            path = "./Output/Call_Sell_Put_Sell_Bear/Weekly/T-1 To T-1/HSL Adjustment"

        
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
            path = "./Output/Call_Sell_Put_Sell_Bear/Weekly/T-2 To T-2/HSL Adjustment"
        else:
            fileName = fileName + "_Weekly_Expiry_T-1_to_T-1"
            path = "./Output/Call_Sell_Put_Sell_Bear/Weekly/T-1 To T-1/HSL Adjustment"
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + f"_Call_HSL_{call_hsl_pct}%_Put_HSL_{put_hsl_pct}%"
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


# Future Buy Call Sell Put Buy
def main1_V8(spot_adjustment_type=0, spot_adjustment=1,call_sell_position=0, put_strike_pct_below=1):
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
        fut_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>=curr_expiry].iloc[0]['Current Expiry']
        
        filtered_data = data_df_1[
                            (data_df_1['Date']>=prev_expiry)
                            & (data_df_1['Date']<=curr_expiry)
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

            print("Weekly Expiry To Expiry", end=" ")
            if call_sell_position>=0:
                if put_strike_pct_below>=0:
                    print(f"Future Buy Call Sell Spot+{call_sell_position}% Put Buy {put_strike_pct_below}% Below Call Strike Weekly From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
                else:
                    print(f"Future Buy Call Sell Spot+{call_sell_position}% Put Buy {abs(put_strike_pct_below)}% Above Call Strike Weekly From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
            else:
                if put_strike_pct_below>=0:
                    print(f"Future Buy Call Sell Spot{call_sell_position}% Put Buy {put_strike_pct_below}% Below Call Strike Weekly From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
                else:
                    print(f"Future Buy Call Sell Spot{call_sell_position}% Put Buy {abs(put_strike_pct_below)}% Above Call Strike Weekly From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
            
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            if entrySpot.empty:
                continue
            entrySpot = entrySpot.iloc[0]['Close']

            exitSpot =  filtered_data[filtered_data['Date']==toDate] 
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None

            call_strike = round_half_up((entrySpot*(1+(call_sell_position/100)))/100)*100
            put_strike = round_half_up(call_strike * (1 - (put_strike_pct_below*0.01))/100)*100
            
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
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
                reason = f"No Call Strike Data Found near {call_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
            
            if call_exit_data.empty:
                reason = f"Call Exit Data missing for Strike {int(call_strike)}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
                continue
    
            # Put Data
            put_entry_data =  bhav_df1[
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
                reason = f"No Put Strike Data Found below {put_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
                continue

            put_strike = put_entry_data.iloc[0]['StrikePrice']
            put_entry_data = put_entry_data[(put_entry_data['StrikePrice']==put_strike)]
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
                            ]
            if put_exit_data.empty:
                reason = f"Put Exit Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
                continue

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
                reason = "Future Entry Data not found" if fut_entry_data.empty else "Future Exity Data not found"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
            put_net =  round(put_exit_price -  put_entry_price, 2)

            fut_entry_price = fut_entry_data.iloc[0]['Close']
            fut_exit_price = fut_exit_data.iloc[0]['Close']
            fut_net = round(fut_exit_price -  fut_entry_price, 2)
            
            total_net = round(call_net + put_net + fut_net, 2)
            
            analysis_data.append({
                    "Entry Date" : fromDate,
                    "Exit Date" : toDate,
                    
                    "Entry Spot" : entrySpot,
                    "Exit Spot" : exitSpot,

                    "Future Expiry": fut_expiry,
                    "Future EntryPrice": fut_entry_price,
                    "Future Exit Price": fut_exit_price,
                    "Future P&L": fut_net,

                    "Call Expiry" : curr_expiry,
                    "Call Strike" : call_strike,
                    "Call EntryPrice" : call_entry_price,
                    "Call Entry Turnover": call_entry_turnover,
                    "Call ExitPrice" : call_exit_price,
                    "Call Exit Turnover" : call_exit_turnover,
                    "Call P&L" : call_net,

                    "Put Expiry" : curr_expiry,
                    "Put Strike" : put_strike,
                    "Put EntryPrice": put_entry_price,
                    "Put Entry Turnover": put_entry_turnover,
                    "Put ExitPrice" : put_exit_price,
                    "Put Exit Turnover" : put_exit_turnover,
                    "Put P&L": put_net,


                    "Net P&L" : total_net,
                    
                })
                
    if analysis_data:
        path = "./Output/Call_Sell_Put_Buy_Future_Buy_Bull/Weekly/Expiry To Expiry"
        
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"
         
        if put_strike_pct_below>0:
            fileName = fileName + f"_PE_Strike_{put_strike_pct_below}%_Below_CE_Strike"
        elif put_strike_pct_below==0:
            fileName = fileName + f"_PE_Strike_Same_As_CE_Strike"
        else:
            fileName = fileName + f"_PE_Strike_{abs(put_strike_pct_below)}%_Above_CE_Strike"
        
        if(spot_adjustment_type==0):
            fileName = fileName + "_NoAdjustment"
            path = path +"/Unadjusted"
        elif(spot_adjustment_type==1):
            fileName = fileName + f"_Adjustment_SpotRiseBy{spot_adjustment}%"
            path = path +"/Adjusted/Rise Only"
        elif(spot_adjustment_type==2):
            fileName = fileName + f"_Adjustment_SpotFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_Adjustment_SpotRisesOrFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/RiseOrFall"
        
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Weekly_Expiry-To-Expiry"
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        path = "./Output/Call_Sell_Put_Buy_Future_Buy_Bull/Weekly/Expiry To Expiry"
        
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"
         
        if put_strike_pct_below>0:
            fileName = fileName + f"_PE_Strike_{put_strike_pct_below}%_Below_CE_Strike"
        elif put_strike_pct_below==0:
            fileName = fileName + f"_PE_Strike_Same_As_CE_Strike"
        else:
            fileName = fileName + f"_PE_Strike_{abs(put_strike_pct_below)}%_Above_CE_Strike"
        
        if(spot_adjustment_type==0):
            fileName = fileName + "_NoAdjustment"
            path = path +"/Unadjusted"
        elif(spot_adjustment_type==1):
            fileName = fileName + f"_Adjustment_SpotRiseBy{spot_adjustment}%"
            path = path +"/Adjusted/Rise Only"
        elif(spot_adjustment_type==2):
            fileName = fileName + f"_Adjustment_SpotFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_Adjustment_SpotRisesOrFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/RiseOrFall"
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Weekly_Expiry-To-Expiry"
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


def main2_V8(spot_adjustment_type=0, spot_adjustment=1,call_sell_position=0, put_strike_pct_below=1, t_2=False):
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
    first_instance = False

    for w in range(0, len(weekly_expiry_df)):
        prev_expiry = weekly_expiry_df.iloc[w]['Previous Expiry']
        curr_expiry = weekly_expiry_df.iloc[w]['Current Expiry']
        fut_expiry = monthly_expiry_df[monthly_expiry_df['Current Expiry']>=curr_expiry].iloc[0]['Current Expiry']
        
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
            fileName1 = fileName2 = ""
            fromDate = interval_df.iloc[i]['From']
            toDate = interval_df.iloc[i]['To']
            
            if(fromDate==toDate):
                continue

            if t_2:
                print("Weekly T-2 To T-2", end=" ")
            else:
                print("Weekly T-1 To T-1", end=" ")

            if call_sell_position>=0:
                if put_strike_pct_below>=0:
                    print(f"Future Buy Call Sell Spot+{call_sell_position}% Put Buy {put_strike_pct_below}% Below Call Strike From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
                else:
                    print(f"Future Buy Call Sell Spot+{call_sell_position}% Put Buy {abs(put_strike_pct_below)}% Above Call Strike From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
            else:
                if put_strike_pct_below>=0:
                    print(f"Future Buy Call Sell Spot{call_sell_position}% Put Buy {put_strike_pct_below}% Below Call Strike From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
                else:
                    print(f"Future Buy Call Sell Spot{call_sell_position}% Put Buy {abs(put_strike_pct_below)}% Above Call Strike From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
            
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            if entrySpot.empty:
                continue
            entrySpot = entrySpot.iloc[0]['Close']

            exitSpot =  filtered_data[filtered_data['Date']==toDate] 
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None

            call_strike = round_half_up((entrySpot*(1+(call_sell_position/100)))/100)*100
            put_strike = round_half_up(call_strike * (1 - (put_strike_pct_below*0.01))/100)*100
            
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
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
                reason = f"No Call Strike Data Found near {call_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
            
            if call_exit_data.empty:
                reason = f"Call Exit Data missing for Strike {int(call_strike)}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
                continue
    
            # Put Data
            put_entry_data =  bhav_df1[
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
                reason = f"No Put Strike Data Found below {put_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
                continue

            put_strike = put_entry_data.iloc[0]['StrikePrice']
            put_entry_data = put_entry_data[(put_entry_data['StrikePrice']==put_strike)]
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
                            ]
            if put_exit_data.empty:
                reason = f"Put Exit Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
                continue

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
                reason = "Future Entry Data not found" if fut_entry_data.empty else "Future Exity Data not found"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
            put_net =  round(put_exit_price -  put_entry_price, 2)

            fut_entry_price = fut_entry_data.iloc[0]['Close']
            fut_exit_price = fut_exit_data.iloc[0]['Close']
            fut_net = round(fut_exit_price -  fut_entry_price, 2)
            
            total_net = round(call_net + put_net + fut_net, 2)
            
            analysis_data.append({
                    "Entry Date" : fromDate,
                    "Exit Date" : toDate,
                    
                    "Entry Spot" : entrySpot,
                    "Exit Spot" : exitSpot,

                    "Future Expiry": fut_expiry,
                    "Future EntryPrice": fut_entry_price,
                    "Future Exit Price": fut_exit_price,
                    "Future P&L": fut_net,

                    "Call Expiry" : curr_expiry,
                    "Call Strike" : call_strike,
                    "Call EntryPrice" : call_entry_price,
                    "Call Entry Turnover": call_entry_turnover,
                    "Call ExitPrice" : call_exit_price,
                    "Call Exit Turnover" : call_exit_turnover,
                    "Call P&L" : call_net,

                    "Put Expiry" : curr_expiry,
                    "Put Strike" : put_strike,
                    "Put EntryPrice": put_entry_price,
                    "Put Entry Turnover": put_entry_turnover,
                    "Put ExitPrice" : put_exit_price,
                    "Put Exit Turnover" : put_exit_turnover,
                    "Put P&L": put_net,


                    "Net P&L" : total_net,
                    
                })
                
    if analysis_data:
        if t_2:
            path = "./Output/Call_Sell_Put_Buy_Future_Buy_Bull/Weekly/T-2 To T-2"
        else:
            path = "./Output/Call_Sell_Put_Buy_Future_Buy_Bull/Weekly/T-1 To T-1"
        
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"
         
        if put_strike_pct_below>0:
            fileName = fileName + f"_PE_Strike_{put_strike_pct_below}%_Below_CE_Strike"
        elif put_strike_pct_below==0:
            fileName = fileName + f"_PE_Strike_Same_As_CE_Strike"
        else:
            fileName = fileName + f"_PE_Strike_{abs(put_strike_pct_below)}%_Above_CE_Strike"
        
        if(spot_adjustment_type==0):
            fileName = fileName + "_NoAdjustment"
            path = path +"/Unadjusted"
        elif(spot_adjustment_type==1):
            fileName = fileName + f"_Adjustment_SpotRiseBy{spot_adjustment}%"
            path = path +"/Adjusted/Rise Only"
        elif(spot_adjustment_type==2):
            fileName = fileName + f"_Adjustment_SpotFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_Adjustment_SpotRisesOrFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/RiseOrFall"
        
        
        
        if t_2:
            fileName = fileName + "_Weekly_T-2_To_T-2"
        else:
            fileName = fileName + "_Weekly_T-1_To_T-1"

        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        if t_2:
            path = "./Output/Call_Sell_Put_Buy_Future_Buy_Bull/Weekly/T-2 To T-2"
        else:
            path = "./Output/Call_Sell_Put_Buy_Future_Buy_Bull/Weekly/T-1 To T-1"
        
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"
         
        if put_strike_pct_below>0:
            fileName = fileName + f"_PE_Strike_{put_strike_pct_below}%_Below_CE_Strike"
        elif put_strike_pct_below==0:
            fileName = fileName + f"_PE_Strike_Same_As_CE_Strike"
        else:
            fileName = fileName + f"_PE_Strike_{abs(put_strike_pct_below)}%_Above_CE_Strike"
        
        if(spot_adjustment_type==0):
            fileName = fileName + "_NoAdjustment"
            path = path +"/Unadjusted"
        elif(spot_adjustment_type==1):
            fileName = fileName + f"_Adjustment_SpotRiseBy{spot_adjustment}%"
            path = path +"/Adjusted/Rise Only"
        elif(spot_adjustment_type==2):
            fileName = fileName + f"_Adjustment_SpotFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_Adjustment_SpotRisesOrFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/RiseOrFall"
        
        
        if t_2:
            fileName = fileName + "_Weekly_T-2_To_T-2"
        else:
            fileName = fileName + "_Weekly_T-1_To_T-1"

        fileName = fileName + "_Log"
        os.makedirs(path, exist_ok=True)
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


def main3_V8(spot_adjustment_type=0, spot_adjustment=1,call_sell_position=0, put_strike_pct_below=1):
    data_df = getStrikeData("NIFTY")
    base2_df = pd.read_csv("./Filter/base2.csv")
    base2_df['Start'] = pd.to_datetime(base2_df['Start'], format='%Y-%m-%d')
    base2_df['End'] = pd.to_datetime(base2_df['End'], format='%Y-%m-%d')
    base2_df = base2_df.sort_values(by=['Start', 'End']).reset_index(drop=True)
    
    mask = pd.Series(False, index=data_df.index)
    for _, row in base2_df.iterrows():
        mask |= (data_df['Date'] >= row['Start']) & (data_df['Date'] <= row['End'])
    data_df_1 = data_df[mask].reset_index(drop=True).copy(deep=True)
    
   
    monthly_expiry_df = pd.read_csv(f"./expiryData/NIFTY_Monthly.csv")
    monthly_expiry_df['Previous Expiry'] = pd.to_datetime(monthly_expiry_df['Previous Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Current Expiry'] = pd.to_datetime(monthly_expiry_df['Current Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Next Expiry'] = pd.to_datetime(monthly_expiry_df['Next Expiry'], format='%Y-%m-%d')
    monthly_expiry_df = monthly_expiry_df[monthly_expiry_df['Current Expiry']>=pd.Timestamp(2019,2,1)].sort_values(by='Current Expiry').reset_index(drop=True)

   
    analysis_data = []
    for w in range(0, len(monthly_expiry_df)):
        prev_expiry = monthly_expiry_df.iloc[w]['Previous Expiry']
        curr_expiry = monthly_expiry_df.iloc[w]['Current Expiry']
        fut_expiry = curr_expiry
        
        filtered_data = data_df_1[
                            (data_df_1['Date']>=prev_expiry)
                            & (data_df_1['Date']<=curr_expiry)
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

            print("Monthly Expiry To Expiry", end= " ")
            
            if call_sell_position>=0:
                if put_strike_pct_below>=0:
                    print(f"Future Buy Call Sell Spot+{call_sell_position}% Put Buy {put_strike_pct_below}% Below Call Strike From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
                else:
                    print(f"Future Buy Call Sell Spot+{call_sell_position}% Put Buy {abs(put_strike_pct_below)}% Above Call Strike From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
            else:
                if put_strike_pct_below>=0:
                    print(f"Future Buy Call Sell Spot{call_sell_position}% Put Buy {put_strike_pct_below}% Below Call Strike From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
                else:
                    print(f"Future Buy Call Sell Spot{call_sell_position}% Put Buy {abs(put_strike_pct_below)}% Above Call Strike From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
            
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            if entrySpot.empty:
                continue
            entrySpot = entrySpot.iloc[0]['Close']

            exitSpot =  filtered_data[filtered_data['Date']==toDate] 
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None

            call_strike = round_half_up((entrySpot*(1+(call_sell_position/100)))/100)*100
            put_strike = round_half_up(call_strike * (1 - (put_strike_pct_below*0.01))/100)*100
            
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
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
                reason = f"No Call Strike Data Found near {call_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
            
            if call_exit_data.empty:
                reason = f"Call Exit Data missing for Strike {int(call_strike)}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
                continue
    
            # Put Data
            put_entry_data =  bhav_df1[
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
                reason = f"No Put Strike Data Found below {put_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
                continue

            put_strike = put_entry_data.iloc[0]['StrikePrice']
            put_entry_data = put_entry_data[(put_entry_data['StrikePrice']==put_strike)]
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
                            ]
            if put_exit_data.empty:
                reason = f"Put Exit Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
                continue

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
                reason = "Future Entry Data not found" if fut_entry_data.empty else "Future Exity Data not found"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
            put_net =  round(put_exit_price -  put_entry_price, 2)

            fut_entry_price = fut_entry_data.iloc[0]['Close']
            fut_exit_price = fut_exit_data.iloc[0]['Close']
            fut_net = round(fut_exit_price -  fut_entry_price, 2)
            
            total_net = round(call_net + put_net + fut_net, 2)
            
            analysis_data.append({
                    "Entry Date" : fromDate,
                    "Exit Date" : toDate,
                    
                    "Entry Spot" : entrySpot,
                    "Exit Spot" : exitSpot,

                    "Future Expiry": fut_expiry,
                    "Future EntryPrice": fut_entry_price,
                    "Future Exit Price": fut_exit_price,
                    "Future P&L": fut_net,

                    "Call Expiry" : curr_expiry,
                    "Call Strike" : call_strike,
                    "Call EntryPrice" : call_entry_price,
                    "Call Entry Turnover": call_entry_turnover,
                    "Call ExitPrice" : call_exit_price,
                    "Call Exit Turnover" : call_exit_turnover,
                    "Call P&L" : call_net,

                    "Put Expiry" : curr_expiry,
                    "Put Strike" : put_strike,
                    "Put EntryPrice": put_entry_price,
                    "Put Entry Turnover": put_entry_turnover,
                    "Put ExitPrice" : put_exit_price,
                    "Put Exit Turnover" : put_exit_turnover,
                    "Put P&L": put_net,


                    "Net P&L" : total_net,
                    
                })
                
    if analysis_data:
        path = "./Output/Call_Sell_Put_Buy_Future_Buy_Bull/Monthly/Expiry To Expiry"
        
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"
         
        if put_strike_pct_below>0:
            fileName = fileName + f"_PE_Strike_{put_strike_pct_below}%_Below_CE_Strike"
        elif put_strike_pct_below==0:
            fileName = fileName + f"_PE_Strike_Same_As_CE_Strike"
        else:
            fileName = fileName + f"_PE_Strike_{abs(put_strike_pct_below)}%_Above_CE_Strike"
        
        if(spot_adjustment_type==0):
            fileName = fileName + "_NoAdjustment"
            path = path +"/Unadjusted"
        elif(spot_adjustment_type==1):
            fileName = fileName + f"_Adjustment_SpotRiseBy{spot_adjustment}%"
            path = path +"/Adjusted/Rise Only"
        elif(spot_adjustment_type==2):
            fileName = fileName + f"_Adjustment_SpotFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_Adjustment_SpotRisesOrFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/RiseOrFall"
        
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Monthly_Expiry-To-Expiry"
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        path = "./Output/Call_Sell_Put_Buy_Future_Buy_Bull/Monthly/Expiry To Expiry"
        
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"
         
        if put_strike_pct_below>0:
            fileName = fileName + f"_PE_Strike_{put_strike_pct_below}%_Below_CE_Strike"
        elif put_strike_pct_below==0:
            fileName = fileName + f"_PE_Strike_Same_As_CE_Strike"
        else:
            fileName = fileName + f"_PE_Strike_{abs(put_strike_pct_below)}%_Above_CE_Strike"
        
        if(spot_adjustment_type==0):
            fileName = fileName + "_NoAdjustment"
            path = path +"/Unadjusted"
        elif(spot_adjustment_type==1):
            fileName = fileName + f"_Adjustment_SpotRiseBy{spot_adjustment}%"
            path = path +"/Adjusted/Rise Only"
        elif(spot_adjustment_type==2):
            fileName = fileName + f"_Adjustment_SpotFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_Adjustment_SpotRisesOrFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/RiseOrFall"
        
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Monthly_Expiry-To-Expiry"
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


def main4_V8(spot_adjustment_type=0, spot_adjustment=1,call_sell_position=0, put_strike_pct_below=1, t_2=False):
    data_df = getStrikeData("NIFTY")
    base2_df = pd.read_csv("./Filter/base2.csv")
    base2_df['Start'] = pd.to_datetime(base2_df['Start'], format='%Y-%m-%d')
    base2_df['End'] = pd.to_datetime(base2_df['End'], format='%Y-%m-%d')
    base2_df = base2_df.sort_values(by=['Start', 'End']).reset_index(drop=True)
    
    mask = pd.Series(False, index=data_df.index)
    for _, row in base2_df.iterrows():
        mask |= (data_df['Date'] >= row['Start']) & (data_df['Date'] <= row['End'])
    data_df_1 = data_df[mask].reset_index(drop=True).copy(deep=True)
    
   
    monthly_expiry_df = pd.read_csv(f"./expiryData/NIFTY_Monthly.csv")
    monthly_expiry_df['Previous Expiry'] = pd.to_datetime(monthly_expiry_df['Previous Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Current Expiry'] = pd.to_datetime(monthly_expiry_df['Current Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Next Expiry'] = pd.to_datetime(monthly_expiry_df['Next Expiry'], format='%Y-%m-%d')
    monthly_expiry_df = monthly_expiry_df[monthly_expiry_df['Current Expiry']>=pd.Timestamp(2019,2,1)].sort_values(by='Current Expiry').reset_index(drop=True)

   
    analysis_data = []
    first_instance = False
    for w in range(0, len(monthly_expiry_df)):
        prev_expiry = monthly_expiry_df.iloc[w]['Previous Expiry']
        curr_expiry = monthly_expiry_df.iloc[w]['Current Expiry']
        fut_expiry = curr_expiry
        
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
            fileName1 = fileName2 = ""
            fromDate = interval_df.iloc[i]['From']
            toDate = interval_df.iloc[i]['To']
            
            if(fromDate==toDate):
                continue

            if t_2:
                print("Monthly T-2 To T-2", end=" ")
            else:
                print("Monthly T-1 To T-1", end=" ")

            if call_sell_position>=0:
                if put_strike_pct_below>=0:
                    print(f"Future Buy Call Sell Spot+{call_sell_position}% Put Buy {put_strike_pct_below}% Below Call Strike From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
                else:
                    print(f"Future Buy Call Sell Spot+{call_sell_position}% Put Buy {abs(put_strike_pct_below)}% Above Call Strike From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
            else:
                if put_strike_pct_below>=0:
                    print(f"Future Buy Call Sell Spot{call_sell_position}% Put Buy {put_strike_pct_below}% Below Call Strike From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
                else:
                    print(f"Future Buy Call Sell Spot{call_sell_position}% Put Buy {abs(put_strike_pct_below)}% Above Call Strike From:{fromDate.strftime('%Y-%m-%d')} To:{toDate.strftime('%Y-%m-%d')}")       
            
            entrySpot = filtered_data[filtered_data['Date']==fromDate]
            if entrySpot.empty:
                continue
            entrySpot = entrySpot.iloc[0]['Close']

            exitSpot =  filtered_data[filtered_data['Date']==toDate] 
            if not exitSpot.empty:
                exitSpot = exitSpot.iloc[0]['Close']
            else:
                exitSpot = None

            call_strike = round_half_up((entrySpot*(1+(call_sell_position/100)))/100)*100
            put_strike = round_half_up(call_strike * (1 - (put_strike_pct_below*0.01))/100)*100
            
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
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
                reason = f"No Call Strike Data Found near {call_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
            
            if call_exit_data.empty:
                reason = f"Call Exit Data missing for Strike {int(call_strike)}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
                continue
    
            # Put Data
            put_entry_data =  bhav_df1[
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
                reason = f"No put Strike Data Found below {put_strike} with TurnOver>0"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
                continue

            put_strike = put_entry_data.iloc[0]['StrikePrice']
            put_entry_data = put_entry_data[(put_entry_data['StrikePrice']==put_strike)]
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
                            ]
            if put_exit_data.empty:
                reason = f"Put Exit Data missing for Strike {int(put_strike)} with Expiry {curr_expiry.strftime('%Y-%m-%d')}"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
                continue

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
                reason = "Future Entry Data not found" if fut_entry_data.empty else "Future Exity Data not found"
                createLogFile("NIFTY", reason, curr_expiry, curr_expiry, fut_expiry, fromDate, toDate)
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
            put_net =  round(put_exit_price -  put_entry_price, 2)

            fut_entry_price = fut_entry_data.iloc[0]['Close']
            fut_exit_price = fut_exit_data.iloc[0]['Close']
            fut_net = round(fut_exit_price -  fut_entry_price, 2)
            
            total_net = round(call_net + put_net + fut_net, 2)
            
            analysis_data.append({
                    "Entry Date" : fromDate,
                    "Exit Date" : toDate,
                    
                    "Entry Spot" : entrySpot,
                    "Exit Spot" : exitSpot,

                    "Future Expiry": fut_expiry,
                    "Future EntryPrice": fut_entry_price,
                    "Future Exit Price": fut_exit_price,
                    "Future P&L": fut_net,

                    "Call Expiry" : curr_expiry,
                    "Call Strike" : call_strike,
                    "Call EntryPrice" : call_entry_price,
                    "Call Entry Turnover": call_entry_turnover,
                    "Call ExitPrice" : call_exit_price,
                    "Call Exit Turnover" : call_exit_turnover,
                    "Call P&L" : call_net,

                    "Put Expiry" : curr_expiry,
                    "Put Strike" : put_strike,
                    "Put EntryPrice": put_entry_price,
                    "Put Entry Turnover": put_entry_turnover,
                    "Put ExitPrice" : put_exit_price,
                    "Put Exit Turnover" : put_exit_turnover,
                    "Put P&L": put_net,


                    "Net P&L" : total_net,
                    
                })
                
    if analysis_data:
        if t_2:
            path = "./Output/Call_Sell_Put_Buy_Future_Buy_Bull/Monthly/T-2 To T-2"
        else:
            path = "./Output/Call_Sell_Put_Buy_Future_Buy_Bull/Monthly/T-1 To T-1"
        
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"
         
        if put_strike_pct_below>0:
            fileName = fileName + f"_PE_Strike_{put_strike_pct_below}%_Below_CE_Strike"
        elif put_strike_pct_below==0:
            fileName = fileName + f"_PE_Strike_Same_As_CE_Strike"
        else:
            fileName = fileName + f"_PE_Strike_{abs(put_strike_pct_below)}%_Above_CE_Strike"
        
        if(spot_adjustment_type==0):
            fileName = fileName + "_NoAdjustment"
            path = path +"/Unadjusted"
        elif(spot_adjustment_type==1):
            fileName = fileName + f"_Adjustment_SpotRiseBy{spot_adjustment}%"
            path = path +"/Adjusted/Rise Only"
        elif(spot_adjustment_type==2):
            fileName = fileName + f"_Adjustment_SpotFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_Adjustment_SpotRisesOrFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/RiseOrFall"
        
        
        
        if t_2:
            fileName = fileName + "_Monthly_T-2_To_T-2"
        else:
            fileName = fileName + "_Monthly_T-1_To_T-1"

        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        if t_2:
            path = "./Output/Call_Sell_Put_Buy_Future_Buy_Bull/Monthly/T-2 To T-2"
        else:
            path = "./Output/Call_Sell_Put_Buy_Future_Buy_Bull/Monthly/T-1 To T-1"
        
        if call_sell_position==0:
            fileName = f"CE_ATM_Sell"
        elif call_sell_position>0:
            fileName = f"CE_{call_sell_position}%_OTM_Sell"
        else:
            fileName = f"CE_{call_sell_position}%_ITM_Sell"
         
        if put_strike_pct_below>0:
            fileName = fileName + f"_PE_Strike_{put_strike_pct_below}%_Below_CE_Strike"
        elif put_strike_pct_below==0:
            fileName = fileName + f"_PE_Strike_Same_As_CE_Strike"
        else:
            fileName = fileName + f"_PE_Strike_{abs(put_strike_pct_below)}%_Above_CE_Strike"
        
        if(spot_adjustment_type==0):
            fileName = fileName + "_NoAdjustment"
            path = path +"/Unadjusted"
        elif(spot_adjustment_type==1):
            fileName = fileName + f"_Adjustment_SpotRiseBy{spot_adjustment}%"
            path = path +"/Adjusted/Rise Only"
        elif(spot_adjustment_type==2):
            fileName = fileName + f"_Adjustment_SpotFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/Fall Only"
        else:
            fileName = fileName + f"_Adjustment_SpotRisesOrFallsBy{spot_adjustment}%"
            path = path +"/Adjusted/RiseOrFall"
        
        
        if t_2:
            fileName = fileName + "_Monthly_T-2_To_T-2"
        else:
            fileName = fileName + "_Monthly_T-1_To_T-1"

        fileName = fileName + "_Log"
        os.makedirs(path, exist_ok=True)
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()


# Summary For CE+PE with HSL
def create_summary_idx_hsl(df):
    entrySpot = df.iloc[0]['Call Entry Spot']
    first_entry_date = pd.to_datetime(df['Call Entry Date'], format='%Y-%m-%d').min()
    last_exit_date = max(df.iloc[-1]['Call Exit Date'], df.iloc[-1]['Put Exit Date'])
    number_of_years = (last_exit_date - first_entry_date).days / 365.25
    
    groups = {
        "Total Trades"  :   df,
    }
    
    stats_rows = []
    for label, subset in groups.items():
        count = len(subset)  
        total_sum = subset['Net P&L'].sum() if count>0 else None
        avg = (total_sum / count).round(2) if count > 0 else None
        
        win = len(subset[subset['Net P&L']>0]) if count>0 else None
        winPct = round((win/count * 100),2) if not pd.isna(win) else None
        avg_win = subset[subset['Net P&L']>0]['Net P&L'].mean() if not pd.isna(win) else None
        avg_win_pct = round(100*(avg_win/total_sum),2) if not pd.isna(win) else None
        avg_win = round(avg_win, 2) if not pd.isna(avg_win) else None
        
        lose = len(subset[subset['Net P&L']<0]) if count>0 else None
        losePct = round((lose/count * 100),2) if not pd.isna(lose) else None
        avg_loss = subset[subset['Net P&L']<0]['Net P&L'].mean() if not pd.isna(lose) else None
        avg_loss_pct = round(100*(avg_loss/total_sum),2) if  not pd.isna(lose) else None
        avg_loss = round(avg_loss, 2) if not pd.isna(avg_loss) else None

        expectancy = round(( ((avg_win_pct / abs(avg_loss_pct) ) * winPct) - losePct)/100, 2) if not pd.isna(win) and not pd.isna(lose) else None

        if count>0 and ((total_sum + entrySpot)/entrySpot) > 0:
            cagr_options = round(
                100 * (((total_sum + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (total_sum + entrySpot) > 0 else 0
        else:
            cagr_options = 0

        dd = subset['%DD'].min().round(2) if count>0 else None
        dd_points = subset['DD'].min().round(2) if count>0 else None
        Car_MDD = round(cagr_options/abs(dd), 2)
        recovery_factor = round(total_sum/abs(dd_points), 2)

        spot_chg = subset['Spot P&L'].sum()
        roi_vs_spot = round(100*(total_sum/spot_chg), 2) if spot_chg!=0 else None
        
        if count>0 and ((spot_chg + entrySpot) / entrySpot)>0:
            cagr_spot = round(
                100 * (((spot_chg + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (spot_chg + entrySpot) > 0 else 0

        else:
            cagr_spot = 0

        stats_rows.append([
                        label, count, total_sum, avg, 
                        winPct, avg_win, losePct, avg_loss, 
                        expectancy, cagr_options, 
                        dd, spot_chg, roi_vs_spot, 
                        cagr_spot, dd_points, Car_MDD,
                        recovery_factor
        ])
        
    stats_df = pd.DataFrame(stats_rows, columns=[
                                    "Category", "Count", "Sum", "Avg", 
                                    "W%", "Avg(W)", "L%", "Avg(L)",
                                    "Expectancy", "CAGR(Options)",
                                    "DD", "Spot Change", "ROI vs Spot",
                                    "CAGR(Spot)", "DD(Points)", "CAR/MDD",
                                    "Recovery Factor"

                                ])

    
    total_df = pd.DataFrame([
        ["Spot P&L", df["Spot P&L"].sum().round(2)],
        ["CE P&L", df["Call P&L"].sum().round(2)],
        ["PE P&L", df["Put P&L"].sum().round(2)],
        ['CE+PE P&L', round(df["Net P&L"].sum(), 2)],
        ["CE+PE+Spot P&L", (df["Net P&L"].sum() + df["Spot P&L"].sum()).round(2)],
    ], columns=["Type", "Sum"])

    return stats_df, total_df


def getPivotTable_hsl(df):
    filtered_df = df[['Call Expiry', 'Net P&L']].copy(deep=True)
    header = ["Sum of Net P&L", "Total Points"]

    if filtered_df.empty:
        return pd.DataFrame(), [], pd.DataFrame(), []
    
    filtered_df['Month'] = pd.to_datetime(filtered_df['Call Expiry'], format='%Y-%m-%d').dt.strftime("%b")
    filtered_df['Year'] = pd.to_datetime(filtered_df['Call Expiry'], format='%Y-%m-%d').dt.year
    
    pivot_table = filtered_df.pivot_table(
        values = filtered_df.columns[1],  
        index = 'Year',  
        columns = 'Month', 
        aggfunc = 'sum'
    )
    month_order = [m for m in ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'] if m in pivot_table.columns]
    pivot_table = pivot_table[month_order]
    grand_total = ['Grand Total'] + [pivot_table[col].sum().round(2) for col in month_order]
    grand_total_df = pd.DataFrame([grand_total], columns=['Year'] + month_order)
    pivot_table = pd.concat([pivot_table, grand_total_df.set_index('Year')])
    pivot_table['Grand Total'] = pivot_table[month_order].sum(axis=1).round(2)
    pivot_table.reset_index(inplace=True)

    return pivot_table, header


def save_hypothetical_and_summary_idx_hsl(df, filename="./df_final.xlsx"):
    stats_df, total_df = create_summary_idx_hsl(df)
    pivot_table, header = getPivotTable_hsl(df)
    
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df['Call Entry Date'] = df['Call Entry Date'].dt.date
        df['Call Exit Date'] = df['Call Exit Date'].dt.date
        df['Put Entry Date'] = df['Put Entry Date'].dt.date
        df['Put Exit Date'] = df['Put Exit Date'].dt.date
        df['Call Expiry'] = df['Call Expiry'].dt.date
        df['Put Expiry'] = df['Put Expiry'].dt.date
        
        df.to_excel(writer, sheet_name="Hypothetical TradeSheet", index=False)

        start_row = 0
        for table in [stats_df, total_df]:
            table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row)
            start_row += len(table) + 2  
        start_row = start_row + 1

        header_df = pd.DataFrame([header])
        header_df.to_excel(writer, sheet_name="Summary", index=False, header=False, startrow=start_row)
        pivot_table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row+2)
        start_row += len(header_df) + len(pivot_table) + 3 


def summary_hsl():
    main_path = "./Analysis/Data"
    all_folders = os.listdir(main_path)
    all_folders = [
        f for f in os.listdir(main_path)
        if os.path.isdir(os.path.join(main_path, f))
    ]

    for folder in all_folders:
        main_folders = os.listdir(os.path.join(main_path, folder))
        
        for f in main_folders:
            files = glob.glob(os.path.join(main_path, folder,f), recursive=True)
            filtered_files = [
                    f for f in files 
                    if "log" not in f.lower() and "~$" not in f and "summary" not in f.lower()
                ]
            
            for file in filtered_files: 
                print(folder, file)
                df = pd.read_csv(file)
                
                try:
                    df['Call Expiry'] = pd.to_datetime(df['Call Expiry'], format='%Y-%m-%d')
                except:
                    df['Call Expiry'] = pd.to_datetime(df['Call Expiry'], format='%d-%m-%Y')

                try:
                    df['Put Expiry'] = pd.to_datetime(df['Put Expiry'], format='%Y-%m-%d')
                except:
                    df['Put Expiry'] = pd.to_datetime(df['Put Expiry'], format='%d-%m-%Y')

                try:
                    df['Call Entry Date'] = pd.to_datetime(df['Call Entry Date'], format='%Y-%m-%d')
                except:
                    df['Call Entry Date'] = pd.to_datetime(df['Call Entry Date'], format='%d-%m-%Y')
                    
                try:
                    df['Call Exit Date'] = pd.to_datetime(df['Call Exit Date'], format='%Y-%m-%d')
                except:
                    df['Call Exit Date'] = pd.to_datetime(df['Call Exit Date'], format='%d-%m-%Y')

                try:
                    df['Put Entry Date'] = pd.to_datetime(df['Put Entry Date'], format='%Y-%m-%d')
                except:
                    df['Put Entry Date'] = pd.to_datetime(df['Put Entry Date'], format='%d-%m-%Y')
                    
                try:
                    df['Put Exit Date'] = pd.to_datetime(df['Put Exit Date'], format='%Y-%m-%d')
                except:
                    df['Put Exit Date'] = pd.to_datetime(df['Put Exit Date'], format='%d-%m-%Y')


                df = df[
                        (df['Call Expiry']>pd.Timestamp(2019,2,1))
                    ].reset_index(drop=True)
                
                df['Hypothetical Call ExitPrice'] = np.where(
                                                df['Call Exit Date'] == df['Call Expiry'],
                                                    np.where(
                                                        df['Call Exit Spot'] < df['Call Strike'], 
                                                        0,                              
                                                        df['Call Exit Spot'] - df['Call Strike']  
                                                    ),
                                                df['Call ExitPrice']
                                            )
                df['Call P&L'] = df['Call EntryPrice'] - df['Hypothetical Call ExitPrice']
                df['Call P&L'] = df['Call P&L'].round(2)
                df.drop(columns=['Call ExitPrice'], inplace=True)
                
                df['Hypothetical Put ExitPrice'] = np.where(
                                                df['Put Exit Date'] == df['Put Expiry'],
                                                    np.where(
                                                        df['Put Exit Spot'] > df['Put Strike'], 
                                                        0,                              
                                                        df['Put Strike'] - df['Put Exit Spot']  
                                                    ),
                                                df['Put ExitPrice']
                                            )
                df['Put P&L'] = df['Put EntryPrice'] - df['Hypothetical Put ExitPrice']
                df['Put P&L'] = df['Put P&L'].round(2)
                df.drop(columns=['Put ExitPrice'], inplace=True)

                df['Spot P&L'] = np.where(
                                    df['Call Exit Date']>df['Put Exit Date'], 
                                    df['Call Exit Spot'] - df['Call Entry Spot'],
                                    df['Put Exit Spot'] - df['Put Entry Spot']
                                )
                df['Net P&L'] = df['Call P&L']  + df['Put P&L']
                df['Net P&L/Spot Pct'] = round((df['Net P&L']/df['Call Entry Spot'])*100, 2)
                
                df['Cumulative'] = None
                df.at[0, 'Cumulative'] = df.iloc[0]['Call Entry Spot'] + df.iloc[0]['Net P&L']
                for i in range(1, len(df)):
                    df.at[i, 'Cumulative'] = df.at[i-1, 'Cumulative'] + df.at[i, 'Net P&L']

                df['Peak'] = df['Cumulative'].cummax()
                df['DD'] = np.where(df['Peak']>df['Cumulative'], df['Cumulative']-df['Peak'], 0)
                df['Peak'] = df['Peak'].astype(float)
                df['DD'] = df['DD'].astype(float)
                df['%DD'] = np.where(df['DD']==0, 0, round(100*(df['DD']/df['Peak']),2))
                df['%DD'] = df['%DD'].round(2)
                
                df = df[[

                        'Call Strike', 'Call Expiry', 
                        'Call Entry Date', 'Call Entry Spot', 
                        'Call EntryPrice', 'Call Entry Turnover',
                        # 'Call HSL', 
                        'Call Exit Date',
                        'Call Exit Spot', 'Hypothetical Call ExitPrice',
                        'Call Exit Turnover', 'Call P&L',

                        'Put Strike', 'Put Expiry',
                        'Put Entry Date', 'Put Entry Spot',
                        'Put EntryPrice', 'Put Entry Turnover',
                        # 'Put HSL', 
                        'Put Exit Date',
                        'Put Exit Spot', 'Hypothetical Put ExitPrice',
                        'Put Exit Turnover', 'Put P&L',
                        
                        'Spot P&L', 'Net P&L', 'Net P&L/Spot Pct',
                        'Cumulative', 'Peak', 'DD', '%DD'
                    ]]
                file = file.split(".csv")[0]
                file = file + "_Final_Summary" + ".xlsx"
                save_hypothetical_and_summary_idx_hsl(df, file)


# Call Weekly Sell 
# Put Monthly Sell
# Put Next Expiry after 2nd weekly expiry
# 0 for no Adjustment
# 1 For Upside - Put Only
# 2 For Downside - Call Only
# 3 For Both Call and Put Adjustment in Upside/Downside
def main1_V9(spot_adjustment_type=0, spot_adjustment=1, call_sell_position=0, max_put_spot_pct=0.04):
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
            
            print("Weekly Expiry To Expiry", end=" ")
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
        path = "./Output/Call_Weekly_Sell_Put_Monthly_ITM_Sell_Bull/Weekly/Expiry To Expiry"

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
        
        fileName = fileName + "_Weekly_Expiry-To-Expiry"
        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        path = "./Output/Call_Weekly_Sell_Put_Monthly_ITM_Sell_Bull/Weekly/Expiry To Expiry"

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
        
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()

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

def main3_V9(spot_adjustment_type=0, spot_adjustment=1, call_sell_position=0, max_put_spot_pct=0.04):
    data_df = getStrikeData("NIFTY")
    base2_df = pd.read_csv("./Filter/base2.csv")
    base2_df['Start'] = pd.to_datetime(base2_df['Start'], format='%Y-%m-%d')
    base2_df['End'] = pd.to_datetime(base2_df['End'], format='%Y-%m-%d')
    base2_df = base2_df.sort_values(by=['Start', 'End']).reset_index(drop=True)
    
    mask = pd.Series(False, index=data_df.index)
    for _, row in base2_df.iterrows():
        mask |= (data_df['Date'] >= row['Start']) & (data_df['Date'] <= row['End'])
    data_df_1 = data_df[mask].reset_index(drop=True).copy(deep=True)
    
    monthly_expiry_df = pd.read_csv(f"./expiryData/NIFTY_Monthly.csv")
    monthly_expiry_df['Previous Expiry'] = pd.to_datetime(monthly_expiry_df['Previous Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Current Expiry'] = pd.to_datetime(monthly_expiry_df['Current Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Next Expiry'] = pd.to_datetime(monthly_expiry_df['Next Expiry'], format='%Y-%m-%d')
    monthly_expiry_df = monthly_expiry_df[monthly_expiry_df['Current Expiry']>pd.Timestamp(2019,2,1)].reset_index(drop=True)

    analysis_data = []
    
    for w in range(0, len(monthly_expiry_df)):
        prev_expiry = monthly_expiry_df.iloc[w]['Previous Expiry']
        curr_expiry = monthly_expiry_df.iloc[w]['Current Expiry']
        next_expiry = monthly_expiry_df.iloc[w]['Next Expiry']
        put_expiry = curr_expiry
        
        filtered_data = data_df_1[
                            (data_df_1['Date']>=prev_expiry)
                            & (data_df_1['Date']<=curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)
        
        filtered_data_1 = data_df[
                            (data_df['Date']>=prev_expiry)
                            & (data_df['Date']<=curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)
        
        if(len(filtered_data)<2):
            continue
        
        intervals, interval_df = [], pd.DataFrame()
        tenthBar = filtered_data_1.iloc[-10]['Date']
        before_tenth_bar = filtered_data[filtered_data['Date']<=tenthBar].copy()
        after_tenth_bar = filtered_data[filtered_data['Date']>=tenthBar].copy()
        
        if not before_tenth_bar.empty:
            intervals.append((before_tenth_bar['Date'].min(), before_tenth_bar['Date'].max(), 0))
        if not after_tenth_bar.empty:
            intervals.append((after_tenth_bar['Date'].min(), after_tenth_bar['Date'].max(), 1))
        interval_df = pd.DataFrame(intervals, columns=['From', 'To', 'Flag'])
        
        
        intervals = []
        if (spot_adjustment_type!=0):
            for i in range(0, len(interval_df)):
                fromDate = interval_df.iloc[i]['From']
                toDate = interval_df.iloc[i]['To']
                flag = interval_df.iloc[i]['Flag']
                temp_filtered_data = filtered_data[
                                    (filtered_data['Date']>=fromDate)
                                    & (filtered_data['Date']<=toDate)
                                ].reset_index(drop=True).copy(deep=True)
                
                filtered_data1 = temp_filtered_data.copy(deep=True)
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
                    start = temp_filtered_data.iloc[0]['Date']
                    for d in filtered_data1['Date']:
                        intervals.append((start, d, flag))
                        start = d   
                    if start != temp_filtered_data.iloc[-1]['Date']:
                        intervals.append((start, temp_filtered_data.iloc[-1]['Date'], flag))       
                else:
                    intervals.append((temp_filtered_data.iloc[0]['Date'], temp_filtered_data.iloc[-1]['Date'], flag))            

        if intervals:
            interval_df = pd.DataFrame(intervals, columns=['From', 'To', 'Flag'])
            
        interval_df['FlagCount'] =  interval_df['Flag'].cumsum()
       
        call_strike, put_strike = None, None
        for i in range(0, len(interval_df)):
            fromDate = interval_df.iloc[i]['From']
            toDate = interval_df.iloc[i]['To']
            flagCount = interval_df.iloc[i]['FlagCount']

            if(fromDate>=tenthBar):
                put_expiry = next_expiry      
    
            if fromDate==toDate:
                reason = "From and To Date are Same"
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, pd.NaT, fromDate, toDate)
                continue   
            
            print("Monthly Expiry To Expiry", end=" ")
            
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
            
            if i==0 or spot_adjustment_type in [2, 3] or (flagCount==1) or True:
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
            
            if(i==0 or (spot_adjustment_type in [1,3])) or  (flagCount==1) or True:
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
        path = "./Output/Call_Monthly_Sell_Put_Monthly_ITM_Sell_Bull/Monthly/Expiry To Expiry"

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
        
        fileName = fileName + "_Monthly_Expiry-To-Expiry"
        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        path = "./Output/Call_Monthly_Sell_Put_Monthly_ITM_Sell_Bull/Monthly/Expiry To Expiry"

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
        
        fileName = fileName + "_Monthly_Expiry-To-Expiry"
        fileName = fileName + "_Log"
        os.makedirs(path, exist_ok=True)
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()

def main4_V9(spot_adjustment_type=0, spot_adjustment=1, call_sell_position=0, t_2=False, max_put_spot_pct=0.04):
    data_df = getStrikeData("NIFTY")
    base2_df = pd.read_csv("./Filter/base2.csv")
    base2_df['Start'] = pd.to_datetime(base2_df['Start'], format='%Y-%m-%d')
    base2_df['End'] = pd.to_datetime(base2_df['End'], format='%Y-%m-%d')
    base2_df = base2_df.sort_values(by=['Start', 'End']).reset_index(drop=True)
    
    mask = pd.Series(False, index=data_df.index)
    for _, row in base2_df.iterrows():
        mask |= (data_df['Date'] >= row['Start']) & (data_df['Date'] <= row['End'])
    data_df_1 = data_df[mask].reset_index(drop=True).copy(deep=True)
    
    monthly_expiry_df = pd.read_csv(f"./expiryData/NIFTY_Monthly.csv")
    monthly_expiry_df['Previous Expiry'] = pd.to_datetime(monthly_expiry_df['Previous Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Current Expiry'] = pd.to_datetime(monthly_expiry_df['Current Expiry'], format='%Y-%m-%d')
    monthly_expiry_df['Next Expiry'] = pd.to_datetime(monthly_expiry_df['Next Expiry'], format='%Y-%m-%d')
    monthly_expiry_df = monthly_expiry_df[monthly_expiry_df['Current Expiry']>pd.Timestamp(2019,2,1)].reset_index(drop=True)

    analysis_data = []
    first_instance = False
    for w in range(0, len(monthly_expiry_df)):
        prev_expiry = monthly_expiry_df.iloc[w]['Previous Expiry']
        curr_expiry = monthly_expiry_df.iloc[w]['Current Expiry']
        next_expiry = monthly_expiry_df.iloc[w]['Next Expiry']
        put_expiry = curr_expiry
        
        filtered_data = data_df_1[
                            (data_df_1['Date']>=prev_expiry)
                            & (data_df_1['Date']<=curr_expiry)
                        ].sort_values(by='Date').reset_index(drop=True)
        
        filtered_data_1 = data_df[
                            (data_df['Date']>=prev_expiry)
                            & (data_df['Date']<=curr_expiry)
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
        tenthBar = filtered_data_1.iloc[-10]['Date']
        before_tenth_bar = filtered_data[filtered_data['Date']<=tenthBar].copy()
        after_tenth_bar = filtered_data[filtered_data['Date']>=tenthBar].copy()
        
        if not before_tenth_bar.empty:
            intervals.append((before_tenth_bar['Date'].min(), before_tenth_bar['Date'].max(), 0))
        if not after_tenth_bar.empty:
            intervals.append((after_tenth_bar['Date'].min(), after_tenth_bar['Date'].max(), 1))
        
        interval_df = pd.DataFrame(intervals, columns=['From', 'To', 'Flag'])
    
        intervals = []
        if (spot_adjustment_type!=0):
            for i in range(0, len(interval_df)):
                fromDate = interval_df.iloc[i]['From']
                toDate = interval_df.iloc[i]['To']
                flag = interval_df.iloc[i]['Flag']
                temp_filtered_data = filtered_data[
                                    (filtered_data['Date']>=fromDate)
                                    & (filtered_data['Date']<=toDate)
                                ].reset_index(drop=True).copy(deep=True)
                
                filtered_data1 = temp_filtered_data.copy(deep=True)
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
                    start = temp_filtered_data.iloc[0]['Date']
                    for d in filtered_data1['Date']:
                        intervals.append((start, d, flag))
                        start = d   
                    if start != temp_filtered_data.iloc[-1]['Date']:
                        intervals.append((start, temp_filtered_data.iloc[-1]['Date'], flag))       
                else:
                    intervals.append((temp_filtered_data.iloc[0]['Date'], temp_filtered_data.iloc[-1]['Date'], flag))            

        if intervals:
            interval_df = pd.DataFrame(intervals, columns=['From', 'To', 'Flag'])
        
        interval_df['FlagCount'] =  interval_df['Flag'].cumsum()
        call_strike, put_strike = None, None
        for i in range(0, len(interval_df)):
            fromDate = interval_df.iloc[i]['From']
            toDate = interval_df.iloc[i]['To']
            flagCount = interval_df.iloc[i]['FlagCount']

            if(fromDate>=tenthBar):
                put_expiry = next_expiry      
    
            if fromDate==toDate:
                reason = "From and To Date are Same"
                createLogFile("NIFTY", reason, curr_expiry, put_expiry, pd.NaT, fromDate, toDate)
                continue   
            
            if t_2:
                print("Monthly T-2 To T-2", end=" ")
            else:
                print("Monthly T-1 To T-1", end=" ")
            
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
            
            if i==0 or spot_adjustment_type in [2, 3] or flagCount==1 or True:
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
            
            if(i==0 or (spot_adjustment_type in [1,3])  or flagCount==1) or True:
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
            path = "./Output/Call_Monthly_Sell_Put_Monthly_ITM_Sell_Bull/Monthly/T-2 To T-2"
        else:
            path = "./Output/Call_Monthly_Sell_Put_Monthly_ITM_Sell_Bull/Monthly/T-1 To T-1"

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
            fileName = fileName + "_Monthly_T-2_To_T-2"
        else:
            fileName = fileName + "_Monthly_T-1_To_T-1"

        os.makedirs(path, exist_ok=True)
        analyse_df = pd.DataFrame(analysis_data)
        analyse_df.to_csv(f"{path}/{fileName}.csv", index=False)
        print(f"{fileName} saved to {path}")


    if logFile:
        if t_2:
            path = "./Output/Call_Monthly_Sell_Put_Monthly_ITM_Sell_Bull/Monthly/T-2 To T-2"
        else:
            path = "./Output/Call_Monthly_Sell_Put_Monthly_ITM_Sell_Bull/Monthly/T-1 To T-1"

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
            fileName = fileName + "_Monthly_T-2_To_T-2"
        else:
            fileName = fileName + "_Monthly_T-1_To_T-1"
            
        os.makedirs(path, exist_ok=True)
        fileName = fileName + "_Log"
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()

def main_V3(callPosition=0, adjustment_type=0, adjustment_points=150, strike_jump=50):
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
        call_expiry = next_expiry
        put_expiry = curr_expiry
        
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
                createLogFile("NIFTY", reason, call_expiry, put_expiry, fut_expiry, fromDate, toDate)
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
                createLogFile("NIFTY", reason, call_expiry, put_expiry, fut_expiry, fromDate, toDate)
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
                                        (bhav_df1['ExpiryDate']==call_expiry)
                                        | (bhav_df1['ExpiryDate']==call_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==call_expiry+timedelta(days=1))
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
                                        (bhav_df1['ExpiryDate']==call_expiry)
                                        | (bhav_df1['ExpiryDate']==call_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==call_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['StrikePrice']<=call_strike)
                                    & (bhav_df1['TurnOver']>0)
                                ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)
                
            if call_entry_data.empty:
                reason = f"Call Data for Strike Near {call_strike} with Turnover>0 not found"
                createLogFile("NIFTY", reason, call_expiry, put_expiry, fut_expiry, fromDate, toDate)
                continue
            
            call_strike = call_entry_data.iloc[0]['StrikePrice']
            call_entry_data = bhav_df1[
                                    (bhav_df1['Instrument']=="OPTIDX")
                                    & (bhav_df1['Symbol']=="NIFTY")
                                    & (bhav_df1['OptionType']=="CE")
                                    & (
                                        (bhav_df1['ExpiryDate']==call_expiry)
                                        | (bhav_df1['ExpiryDate']==call_expiry-timedelta(days=1))
                                        |  (bhav_df1['ExpiryDate']==call_expiry+timedelta(days=1))
                                    )
                                    & (bhav_df1['StrikePrice']==call_strike)
                                ]
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
                            ].reset_index(drop=True)
            
            if call_exit_data.empty:
                reason = f"Call Exit Data missing for Call Strike {int(call_strike)} with Expiry {curr_expiry}"
                createLogFile("NIFTY", reason, call_expiry, put_expiry, fut_expiry, fromDate, toDate)
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
                createLogFile("NIFTY", reason, call_expiry, put_expiry, fut_expiry, fromDate, toDate)
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

                    "Call Expiry" : call_expiry,
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
        path = "./Output/Next_Call_Current_Put_Current_Future/Weekly/Expiry To Expiry"
        
        if callPosition==0:
            fileName = f"Next_CE_ATM_Sell_Curr_PE_ATM_Buy_FUT_Buy"
        elif(callPosition>0):
            fileName = f"Next_CE_{100*callPosition}OTM_Sell_Curr_PE_ATM_Buy_FUT_Buy"
        else:
            fileName = f"Next_CE_{100*callPosition}ITM_Sell_Curr_PE_ATM_Buy_FUT_Buy"
        
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
        path = "./Output/Next_Call_Current_Put_Current_Future/Weekly/Expiry To Expiry"
        
        if callPosition==0:
            fileName = f"Next_CE_ATM_Sell_Curr_PE_ATM_Buy_FUT_Buy"
        elif(callPosition>0):
            fileName = f"Next_CE_{100*callPosition}OTM_Sell_Curr_PE_ATM_Buy_FUT_Buy"
        else:
            fileName = f"Next_CE_{100*callPosition}ITM_Sell_Curr_PE_ATM_Buy_FUT_Buy"
        
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

        fileName = fileName + "_Log"
        os.makedirs(path, exist_ok=True)
        log_df = pd.DataFrame(logFile)
        log_df.to_csv(f"{path}/{fileName}.csv", index=False)
        logFile.clear()



# Summary for Call/Put with Protective Call/Put
def create_summary_idx_V5_protection(df):
    entrySpot = df.iloc[0]['Entry Spot']
    first_entry_date = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d').min()
    last_exit_date = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d').max()
    number_of_years = (last_exit_date - first_entry_date).days / 365.25
    groups = {
        "Total Trades"  :   df,
    }

    stats_rows = []
    for label, subset in groups.items():
        count = len(subset)  
        total_sum = subset['Net P&L'].sum() if count>0 else None
        avg = (total_sum / count).round(2) if count > 0 else None
        
        win = len(subset[subset['Net P&L']>0]) if count>0 else None
        winPct = round((win/count * 100),2) if not pd.isna(win) else None
        avg_win = subset[subset['Net P&L']>0]['Net P&L'].mean() if not pd.isna(win) else None
        avg_win_pct = round(100*(avg_win/total_sum),2) if not pd.isna(win) else None
        avg_win = round(avg_win, 2) if not pd.isna(avg_win) else None
        
        lose = len(subset[subset['Net P&L']<0]) if count>0 else None
        losePct = round((lose/count * 100),2) if not pd.isna(lose) else None
        avg_loss = subset[subset['Net P&L']<0]['Net P&L'].mean() if not pd.isna(lose) else None
        avg_loss_pct = round(100*(avg_loss/total_sum),2) if  not pd.isna(lose) else None
        avg_loss = round(avg_loss, 2) if not pd.isna(avg_loss) else None

        expectancy = round(( ((avg_win_pct / abs(avg_loss_pct) ) * winPct) - losePct)/100, 2) if not pd.isna(win) and not pd.isna(lose) else None

        if count>0 and ((total_sum + entrySpot)/entrySpot) > 0:
            cagr_options = round(
                100 * (((total_sum + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (total_sum + entrySpot) > 0 else 0
        else:
            cagr_options = 0

        dd = subset['%DD'].min().round(2) if count>0 else None
        dd_points = subset['DD'].min().round(2) if count>0 else None
        Car_MDD = round(cagr_options/abs(dd), 2)
        recovery_factor = round(total_sum/abs(dd_points), 2)

        spot_chg = subset['Spot P&L'].sum()
        roi_vs_spot = round(100*(total_sum/spot_chg), 2) if spot_chg!=0 else None
        
        if count>0 and ((spot_chg + entrySpot) / entrySpot)>0:
            cagr_spot = round(
                100 * (((spot_chg + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (spot_chg + entrySpot) > 0 else 0

        else:
            cagr_spot = 0

        stats_rows.append([
                        label, count, total_sum, avg, 
                        winPct, avg_win, losePct, avg_loss, 
                        expectancy, cagr_options, 
                        dd, spot_chg, roi_vs_spot, 
                        cagr_spot, dd_points, Car_MDD,
                        recovery_factor
        ])
        
    stats_df = pd.DataFrame(stats_rows, columns=[
                                    "Category", "Count", "Sum", "Avg", 
                                    "W%", "Avg(W)", "L%", "Avg(L)",
                                    "Expectancy", "CAGR(Options)",
                                    "DD", "Spot Change", "ROI vs Spot",
                                    "CAGR(Spot)", "DD(Points)", "CAR/MDD",
                                    "Recovery Factor"

                                ])

    
    total_df = pd.DataFrame([
        ["Spot P&L", df["Spot P&L"].sum().round(2)],
        ["PE P&L", df["Put P&L"].sum().round(2)],
        ["Protective PE P&L", df["Protective Put P&L"].sum().round(2)],
        ["PE+Protective PE P&L", df["Net P&L"].sum().round(2)],
        ["PE+Protective PE+Spot P&L", (df["Net P&L"].sum() + df["Spot P&L"].sum()).round(2)],

    ], columns=["Type", "Sum"])

    return stats_df, total_df


def getPivotTable_V5_protection(df):
    filtered_df = df[['Put Expiry', 'Net P&L']].copy(deep=True)
    header = ["Sum of Net P&L", "Total Points"]

    if filtered_df.empty:
        return pd.DataFrame(), [], pd.DataFrame(), []
    
    filtered_df['Month'] = pd.to_datetime(filtered_df['Put Expiry'], format='%Y-%m-%d').dt.strftime("%b")
    filtered_df['Year'] = pd.to_datetime(filtered_df['Put Expiry'], format='%Y-%m-%d').dt.year
    
    pivot_table = filtered_df.pivot_table(
        values = filtered_df.columns[1],  
        index = 'Year',  
        columns = 'Month', 
        aggfunc = 'sum'
    )
    
    month_order = [m for m in ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'] if m in pivot_table.columns]
    pivot_table = pivot_table[month_order]
    grand_total = ['Grand Total'] + [pivot_table[col].sum().round(2) for col in month_order]
    
    grand_total_df = pd.DataFrame([grand_total], columns=['Year'] + month_order)
    pivot_table = pd.concat([pivot_table, grand_total_df.set_index('Year')])
    pivot_table['Grand Total'] = pivot_table[month_order].sum(axis=1).round(2)
    pivot_table.reset_index(inplace=True)

    return pivot_table, header


def save_hypothetical_and_summary_idx_V5_protection(df, filename="./df_final.xlsx"):
    stats_df, total_df = create_summary_idx_V5_protection(df)
    pivot_table, header = getPivotTable_V5_protection(df)
    
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df['Entry Date'] = df['Entry Date'].dt.date
        df['Exit Date'] = df['Exit Date'].dt.date
        df['Put Expiry'] = df['Put Expiry'].dt.date
        df.to_excel(writer, sheet_name="Hypothetical TradeSheet", index=False)

        start_row = 0
        for table in [stats_df, total_df]:
            table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row)
            start_row += len(table) + 2  
        start_row = start_row + 1

        header_df = pd.DataFrame([header])
        header_df.to_excel(writer, sheet_name="Summary", index=False, header=False, startrow=start_row)
        pivot_table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row+2)
        start_row += len(header_df) + len(pivot_table) + 3 


def summary_V5_protection():
    main_path = "./Analysis/Data"
    all_folders = os.listdir(main_path)
    all_folders = [
        f for f in os.listdir(main_path)
        if os.path.isdir(os.path.join(main_path, f))
    ]

    for folder in all_folders:
        main_folders = os.listdir(os.path.join(main_path, folder))
        
        for f in main_folders:
            files = glob.glob(os.path.join(main_path, folder,f, "*.csv"), recursive=True)
            filtered_files = [
                    f for f in files 
                    if "log" not in f.lower() and "~$" not in f and "summary" not in f.lower()
                ]
            for file in filtered_files: 
                print(folder, file)
                df = pd.read_csv(file)
               
                try:
                    df['Put Expiry'] = pd.to_datetime(df['Put Expiry'], format='%Y-%m-%d')
                except:
                    df['Put Expiry'] = pd.to_datetime(df['Put Expiry'], format='%d-%m-%Y')

                try:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d')
                except:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%d-%m-%Y')
                    
                try:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d')
                except:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%d-%m-%Y')

                df = df[
                        (df['Put Expiry']>pd.Timestamp(2019,2,1))
                    ].sort_values(by=['Entry Date', 'Exit Date']).reset_index(drop=True)
                
                df['Hypothetical Put ExitPrice'] = np.where(
                                                df['Exit Date'] == df['Put Expiry'],
                                                    np.where(
                                                        df['Exit Spot'] > df['Put Strike'], 
                                                        0,                              
                                                        df['Put Strike']- df['Exit Spot']   
                                                    ),
                                                df['Put ExitPrice']
                                            )
                df['Put P&L'] = df['Put EntryPrice'] - df['Hypothetical Put ExitPrice']
                df['Put P&L'] = df['Put P&L'].round(2)
                df.drop(columns=['Put ExitPrice'], inplace=True)
                
                df['Hypothetical Protective Put ExitPrice'] = np.where(
                                                df['Exit Date'] == df['Put Expiry'],
                                                    np.where(
                                                        df['Exit Spot'] > df['Protective Put Strike'], 
                                                        0,                              
                                                        df['Protective Put Strike']- df['Exit Spot']   
                                                    ),
                                                df['Protective Put ExitPrice']
                                            )
                df['Protective Put P&L'] = df['Hypothetical Protective Put ExitPrice'] - df['Protective Put EntryPrice']
                df.drop(columns=['Protective Put ExitPrice'], inplace=True)
                df['Protective Put P&L'] = df['Protective Put P&L'].round(2)
                
                df['Net P&L'] = df['Put P&L'] + df['Protective Put P&L']
                df['Net P&L/Spot Pct'] = round((df['Net P&L']/df['Entry Spot'])*100, 2)
                df['Spot P&L'] = round(df['Exit Spot'] - df['Entry Spot'], 2)
                df['Cumulative'] = None
                df.at[0, 'Cumulative'] = df.iloc[0]['Entry Spot'] + df.iloc[0]['Net P&L']
                
                for i in range(1, len(df)):
                    df.at[i, 'Cumulative'] = df.at[i-1, 'Cumulative'] + df.at[i, 'Net P&L']

                df['Peak'] = df['Cumulative'].cummax()
                df['DD'] = np.where(df['Peak']>df['Cumulative'], df['Cumulative']-df['Peak'], 0)
                df['Peak'] = df['Peak'].astype(float)
                df['DD'] = df['DD'].astype(float)
                df['%DD'] = np.where(df['DD']==0, 0, round(100*(df['DD']/df['Peak']),2))
                df['%DD'] = df['%DD'].round(2)
                
                df = df[[

                        'Entry Date', 'Exit Date', 
                        'Entry Spot', 'Exit Spot', 
                        'Spot P&L', 

                        'Put Expiry', 'Put Strike', 
                        'Put Entry Turnover', 'Put EntryPrice', 
                        'Put Exit Turnover', 'Hypothetical Put ExitPrice', 
                        'Put P&L', 

                        'Protective Put Strike', 
                        'Protective Put Entry Turnover', 'Protective Put EntryPrice', 
                        'Protective Put Exit Turnover', 'Hypothetical Protective Put ExitPrice', 
                        'Protective Put P&L', 

                        'Net P&L', 'Net P&L/Spot Pct',
                        'Cumulative', 'Peak', 'DD', '%DD'
                    ]]
                
                file = file.split(".csv")[0]
                file = file + "_Final_Summary" + ".xlsx"
                save_hypothetical_and_summary_idx_V5_protection(df, file)
   
# Summary For Single Leg Call
def create_summary_idx_V5_Call_protection(df):
    entrySpot = df.iloc[0]['Entry Spot']
    first_entry_date = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d').min()
    last_exit_date = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d').max()
    number_of_years = (last_exit_date - first_entry_date).days / 365.25
    groups = {
        "Total Trades"  :   df,
    }

    stats_rows = []
    for label, subset in groups.items():
        count = len(subset)  
        total_sum = subset['Net P&L'].sum() if count>0 else None
        avg = (total_sum / count).round(2) if count > 0 else None
        
        win = len(subset[subset['Net P&L']>0]) if count>0 else None
        winPct = round((win/count * 100),2) if not pd.isna(win) else None
        avg_win = subset[subset['Net P&L']>0]['Net P&L'].mean() if not pd.isna(win) else None
        avg_win_pct = round(100*(avg_win/total_sum),2) if not pd.isna(win) else None
        avg_win = round(avg_win, 2) if not pd.isna(avg_win) else None
        
        lose = len(subset[subset['Net P&L']<0]) if count>0 else None
        losePct = round((lose/count * 100),2) if not pd.isna(lose) else None
        avg_loss = subset[subset['Net P&L']<0]['Net P&L'].mean() if not pd.isna(lose) else None
        avg_loss_pct = round(100*(avg_loss/total_sum),2) if  not pd.isna(lose) else None
        avg_loss = round(avg_loss, 2) if not pd.isna(avg_loss) else None

        expectancy = round(( ((avg_win_pct / abs(avg_loss_pct) ) * winPct) - losePct)/100, 2) if not pd.isna(win) and not pd.isna(lose) else None

        if count>0 and ((total_sum + entrySpot)/entrySpot) > 0:
            cagr_options = round(
                100 * (((total_sum + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (total_sum + entrySpot) > 0 else 0
        else:
            cagr_options = 0

        dd = subset['%DD'].min().round(2) if count>0 else None
        dd_points = subset['DD'].min().round(2) if count>0 else None
        Car_MDD = round(cagr_options/abs(dd), 2)
        recovery_factor = round(total_sum/abs(dd_points), 2)

        spot_chg = subset['Spot P&L'].sum()
        roi_vs_spot = round(100*(total_sum/spot_chg), 2) if spot_chg!=0 else None
        
        if count>0 and ((spot_chg + entrySpot) / entrySpot)>0:
            cagr_spot = round(
                100 * (((spot_chg + entrySpot) / entrySpot) ** (1 / number_of_years) - 1),
                2
            ) if count > 0 and number_of_years > 0 and entrySpot > 0 and (spot_chg + entrySpot) > 0 else 0

        else:
            cagr_spot = 0

        stats_rows.append([
                        label, count, total_sum, avg, 
                        winPct, avg_win, losePct, avg_loss, 
                        expectancy, cagr_options, 
                        dd, spot_chg, roi_vs_spot, 
                        cagr_spot, dd_points, Car_MDD,
                        recovery_factor
        ])
        
    stats_df = pd.DataFrame(stats_rows, columns=[
                                    "Category", "Count", "Sum", "Avg", 
                                    "W%", "Avg(W)", "L%", "Avg(L)",
                                    "Expectancy", "CAGR(Options)",
                                    "DD", "Spot Change", "ROI vs Spot",
                                    "CAGR(Spot)", "DD(Points)", "CAR/MDD",
                                    "Recovery Factor"

                                ])

    
    total_df = pd.DataFrame([
        ["Spot P&L", df["Spot P&L"].sum().round(2)],
        ["CE P&L", df["Call P&L"].sum().round(2)],
        ["Protective CE P&L", df["Protective Call P&L"].sum().round(2)],
        ["CE+Protective CE P&L", df["Net P&L"].sum().round(2)],
        ["CE+Protective CE+Spot P&L", (df["Net P&L"].sum() + df["Spot P&L"].sum()).round(2)],

    ], columns=["Type", "Sum"])

    return stats_df, total_df


def getPivotTable_V5_Call_protection(df):
    filtered_df = df[['Call Expiry', 'Net P&L']].copy(deep=True)
    header = ["Sum of Net P&L", "Total Points"]

    if filtered_df.empty:
        return pd.DataFrame(), [], pd.DataFrame(), []
    
    filtered_df['Month'] = pd.to_datetime(filtered_df['Call Expiry'], format='%Y-%m-%d').dt.strftime("%b")
    filtered_df['Year'] = pd.to_datetime(filtered_df['Call Expiry'], format='%Y-%m-%d').dt.year
    
    pivot_table = filtered_df.pivot_table(
        values = filtered_df.columns[1],  
        index = 'Year',  
        columns = 'Month', 
        aggfunc = 'sum'
    )
    
    month_order = [m for m in ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'] if m in pivot_table.columns]
    pivot_table = pivot_table[month_order]
    grand_total = ['Grand Total'] + [pivot_table[col].sum().round(2) for col in month_order]
    
    grand_total_df = pd.DataFrame([grand_total], columns=['Year'] + month_order)
    pivot_table = pd.concat([pivot_table, grand_total_df.set_index('Year')])
    pivot_table['Grand Total'] = pivot_table[month_order].sum(axis=1).round(2)
    pivot_table.reset_index(inplace=True)

    return pivot_table, header


def save_hypothetical_and_summary_idx_V5_Call_protection(df, filename="./df_final.xlsx"):
    stats_df, total_df = create_summary_idx_V5_Call_protection(df)
    pivot_table, header = getPivotTable_V5_Call_protection(df)
    
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df['Entry Date'] = df['Entry Date'].dt.date
        df['Exit Date'] = df['Exit Date'].dt.date
        df['Call Expiry'] = df['Call Expiry'].dt.date
        df.to_excel(writer, sheet_name="Hypothetical TradeSheet", index=False)

        start_row = 0
        for table in [stats_df, total_df]:
            table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row)
            start_row += len(table) + 2  
        start_row = start_row + 1

        header_df = pd.DataFrame([header])
        header_df.to_excel(writer, sheet_name="Summary", index=False, header=False, startrow=start_row)
        pivot_table.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row+2)
        start_row += len(header_df) + len(pivot_table) + 3 


def summary_V5_Call_protection():
    main_path = "./Analysis/Output/Weekly"
    all_folders = os.listdir(main_path)
    all_folders = [
        f for f in os.listdir(main_path)
        if os.path.isdir(os.path.join(main_path, f))
    ]
    for folder in all_folders:
        main_folders = os.listdir(os.path.join(main_path, folder))
        for f in main_folders:
            files = glob.glob(os.path.join(main_path, folder,f), recursive=True)
            filtered_files = [
                    f for f in files 
                    if "log" not in f.lower() and "~$" not in f and "summary" not in f.lower()
                ]
            for file in filtered_files: 
                print(folder, file)
                df = pd.read_csv(file)
               
                try:
                    df['Call Expiry'] = pd.to_datetime(df['Call Expiry'], format='%Y-%m-%d')
                except:
                    df['Call Expiry'] = pd.to_datetime(df['Call Expiry'], format='%d-%m-%Y')

                try:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%Y-%m-%d')
                except:
                    df['Entry Date'] = pd.to_datetime(df['Entry Date'], format='%d-%m-%Y')
                    
                try:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%Y-%m-%d')
                except:
                    df['Exit Date'] = pd.to_datetime(df['Exit Date'], format='%d-%m-%Y')

                df = df[
                        (df['Call Expiry']>pd.Timestamp(2019,2,1))
                    ].sort_values(by=['Entry Date', 'Exit Date']).reset_index(drop=True)
                
                df['Hypothetical Call ExitPrice'] = np.where(
                                                df['Exit Date'] == df['Call Expiry'],
                                                    np.where(
                                                        df['Exit Spot'] < df['Call Strike'], 
                                                        df['Call ExitPrice'].fillna(0),                              
                                                        df['Exit Spot'] - df['Call Strike'] 
                                                    ),
                                                df['Call ExitPrice']
                                            )
                df['Call P&L'] = df['Call EntryPrice'] - df['Hypothetical Call ExitPrice']
                df.drop(columns=['Call ExitPrice'], inplace=True)
                df['Call P&L'] = df['Call P&L'].round(2)

                df['Hypothetical Protective Call ExitPrice'] = np.where(
                                                df['Exit Date'] == df['Call Expiry'],
                                                    np.where(
                                                        df['Exit Spot'] < df['Protective Call Strike'], 
                                                        df['Protective Call ExitPrice'].fillna(0),                              
                                                        df['Exit Spot'] - df['Protective Call Strike'] 
                                                    ),
                                                df['Protective Call ExitPrice']
                                            )
                df['Protective Call P&L'] = df['Hypothetical Protective Call ExitPrice'] - df['Protective Call EntryPrice']
                df.drop(columns=['Call ExitPrice'], inplace=True)
                df['Protective Call P&L'] = df['Protective Call P&L'].round(2)
               

                
                df['Net P&L'] = df['Call P&L'] + df['Protective Call P&L']
                df['Net P&L/Spot Pct'] = round((df['Net P&L']/df['Entry Spot'])*100, 2)
                df['Spot P&L'] = round(df['Exit Spot'] - df['Entry Spot'], 2)
                
                df['Cumulative'] = None
                df.at[0, 'Cumulative'] = df.iloc[0]['Entry Spot'] + df.iloc[0]['Net P&L']
                for i in range(1, len(df)):
                    df.at[i, 'Cumulative'] = df.at[i-1, 'Cumulative'] + df.at[i, 'Net P&L']

                df['Peak'] = df['Cumulative'].cummax()
                df['DD'] = np.where(df['Peak']>df['Cumulative'], df['Cumulative']-df['Peak'], 0)
                df['Peak'] = df['Peak'].astype(float)
                df['DD'] = df['DD'].astype(float)
                df['%DD'] = np.where(df['DD']==0, 0, round(100*(df['DD']/df['Peak']),2))
                df['%DD'] = df['%DD'].round(2)
                
                 
                df = df[[

                        'Entry Date', 'Exit Date', 
                        'Entry Spot', 'Exit Spot', 
                        'Spot P&L', 

                        'Call Expiry', 'Call Strike', 
                        'Call Entry Turnover', 'Call EntryPrice', 
                        'Call Exit Turnover', 'Hypothetical Call ExitPrice', 
                        'Call P&L', 

                        'Protective Call Strike',
                        'Protective Call EntryPrice',
                        'Protective Call Entry Turnover',
                        'Hypothetical Protective Call ExitPrice',
                        "Protective Call Exit Turnover",
                        "Protective Call P&L",

                        'Net P&L', 'Net P&L/Spot Pct',
                        'Cumulative', 'Peak', 'DD', '%DD'
                    ]]
                
                file = file.split(".csv")[0]
                file = file + "_Final_Summary" + ".xlsx"
                save_hypothetical_and_summary_idx_V5_Call_protection(df, file)
  

for spot_adjustment_type in range(0, 4):
    if spot_adjustment_type==0:
        _range = [0]
    else:
        _range  = [i for i in range(1, 5)]

    # for spot_adjustment in _range:
    #     for call_sell_position in np.arange(-4, 0.5, 0.5):
    #         for put_strike_pct_below in np.arange(0.5, 2, 0.5):
    #             main1_V8(
    #                 spot_adjustment_type=spot_adjustment_type,
    #                 spot_adjustment=spot_adjustment,
    #                 call_sell_position=call_sell_position,
    #                 put_strike_pct_below=put_strike_pct_below
    #             )

main1(
    spot_adjustment_type=0,
    spot_adjustment=0,
    call_sell_position=1.0
)