import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

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
                                                        df['Call ExitPrice'].fillna(0),                              
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
