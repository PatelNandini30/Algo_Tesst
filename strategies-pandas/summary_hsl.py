import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

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
                                                        df['Call ExitPrice'].fillna(0),                              
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
                                                        df['Put ExitPrice'].fillna(0),                              
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
