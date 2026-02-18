import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

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
                                                        df['Put ExitPrice'].fillna(0),                              
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
                                                        df['Protective Put ExitPrice'].fillna(0),                              
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
