"""
Call Sell + Future Buy Strategy (Weekly Expiry to Expiry)
Converted from main1() function in analyse_bhavcopy_02-01-2026.py
"""
import pandas as pd
import os
from datetime import timedelta
from typing import Dict, Any, List
import time

from src.strategies.base import StrategyInterface, StrategyParameter, StrategyResult
from src.data.provider import DataProvider


class CallSellFutureBuyStrategy(StrategyInterface):
    """
    Strategy: Sell Call Option + Buy Future
    Execution: Weekly expiry to expiry
    """
    
    def __init__(self, data_provider: DataProvider):
        self.data_provider = data_provider
        self.log_file = []
    
    def get_name(self) -> str:
        return "call_sell_future_buy_weekly"
    
    def get_description(self) -> str:
        return "Sell Call Option + Buy Future (Weekly Expiry to Expiry)"
    
    def get_version(self) -> str:
        return "1.0.0"
    
    def get_parameter_schema(self) -> List[StrategyParameter]:
        return [
            StrategyParameter(
                name="spot_adjustment_type",
                type="select",
                required=True,
                default=0,
                options=[0, 1, 2, 3],
                description="0=No Adjustment, 1=Rise Only, 2=Fall Only, 3=Rise or Fall"
            ),
            StrategyParameter(
                name="spot_adjustment",
                type="float",
                required=True,
                default=1.0,
                min_value=0.0,
                max_value=100.0,
                description="Spot adjustment percentage for re-entry"
            ),
            StrategyParameter(
                name="call_sell_position",
                type="float",
                required=True,
                default=0.0,
                min_value=-50.0,
                max_value=50.0,
                description="Call strike position: 0=ATM, +ve=OTM%, -ve=ITM%"
            ),
            StrategyParameter(
                name="symbol",
                type="str",
                required=True,
                default="NIFTY",
                description="Underlying symbol"
            )
        ]
    
    def _create_log_entry(
        self,
        symbol: str,
        reason: str,
        call_expiry: pd.Timestamp,
        put_expiry: pd.Timestamp,
        fut_expiry: pd.Timestamp,
        from_date: pd.Timestamp,
        to_date: pd.Timestamp
    ):
        """Create log entry for skipped trades"""
        self.log_file.append({
            "Symbol": symbol,
            "Reason": reason,
            "Call Expiry": call_expiry,
            "Put Expiry": put_expiry,
            "Future Expiry": fut_expiry,
            "From Date": from_date,
            "To Date": to_date
        })
    
    def execute(self, params: Dict[str, Any]) -> StrategyResult:
        """Execute the strategy"""
        start_time = time.time()
        
        # Validate parameters
        is_valid, error_msg = self.validate_parameters(params)
        if not is_valid:
            return StrategyResult(
                data=pd.DataFrame(),
                metadata={"error": error_msg},
                execution_time_ms=0,
                row_count=0,
                success=False,
                error_message=error_msg
            )
        
        # Extract parameters
        spot_adjustment_type = params['spot_adjustment_type']
        spot_adjustment = params['spot_adjustment']
        call_sell_position = params['call_sell_position']
        symbol = params['symbol']
        
        self.log_file = []
        analysis_data = []
        
        try:
            # Load required data
            data_df = self.data_provider.get_strike_data(symbol)
            base2_df = self.data_provider.get_filter_data("base2")
            base2_df = base2_df.sort_values(by=['Start', 'End']).reset_index(drop=True)
            
            # Filter data based on base2 ranges
            mask = pd.Series(False, index=data_df.index)
            for _, row in base2_df.iterrows():
                mask |= (data_df['Date'] >= row['Start']) & (data_df['Date'] <= row['End'])
            data_df_1 = data_df[mask].reset_index(drop=True).copy(deep=True)
            
            # Load expiry data
            weekly_expiry_df = self.data_provider.get_expiry_data(symbol, "weekly")
            monthly_expiry_df = self.data_provider.get_expiry_data(symbol, "monthly")
            
            # Process each weekly expiry
            for w in range(len(weekly_expiry_df)):
                prev_expiry = weekly_expiry_df.iloc[w]['Previous Expiry']
                curr_expiry = weekly_expiry_df.iloc[w]['Current Expiry']
                
                # Get future expiry (monthly)
                curr_monthly_expiry = monthly_expiry_df[
                    monthly_expiry_df['Current Expiry'] >= curr_expiry
                ].sort_values(by='Current Expiry').reset_index(drop=True)
                
                if curr_monthly_expiry.empty:
                    continue
                
                fut_expiry = curr_monthly_expiry.iloc[0]['Current Expiry']
                
                # Filter data for current expiry period
                filtered_data = data_df_1[
                    (data_df_1['Date'] >= prev_expiry) & (data_df_1['Date'] <= curr_expiry)
                ].sort_values(by='Date').reset_index(drop=True)
                
                if len(filtered_data) < 2:
                    continue
                
                # Calculate intervals based on spot adjustment
                intervals = self._calculate_intervals(
                    filtered_data,
                    spot_adjustment_type,
                    spot_adjustment
                )
                
                # Process each interval
                for from_date, to_date in intervals:
                    if from_date == to_date:
                        continue
                    
                    result = self._process_trade(
                        from_date, to_date, curr_expiry, fut_expiry,
                        filtered_data, call_sell_position, symbol
                    )
                    
                    if result:
                        analysis_data.append(result)
            
            # Create result DataFrame
            result_df = pd.DataFrame(analysis_data)
            
            execution_time = (time.time() - start_time) * 1000
            
            return StrategyResult(
                data=result_df,
                metadata={
                    "strategy": self.get_name(),
                    "version": self.get_version(),
                    "parameters": params,
                    "log_entries": len(self.log_file),
                    "trades_executed": len(analysis_data)
                },
                execution_time_ms=execution_time,
                row_count=len(result_df),
                success=True
            )
        
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            return StrategyResult(
                data=pd.DataFrame(),
                metadata={"error": str(e)},
                execution_time_ms=execution_time,
                row_count=0,
                success=False,
                error_message=str(e)
            )
    
    def _calculate_intervals(
        self,
        filtered_data: pd.DataFrame,
        spot_adjustment_type: int,
        spot_adjustment: float
    ) -> List[tuple]:
        """Calculate entry/exit intervals based on spot adjustment"""
        intervals = []
        
        if spot_adjustment_type != 0:
            filtered_data1 = filtered_data.copy(deep=True)
            filtered_data1['ReEntry'] = False
            filtered_data1['Entry_Price'] = None
            filtered_data1['Pct_Chg'] = None
            entry_price = None
            
            for t in range(len(filtered_data1)):
                if t == 0:
                    entry_price = filtered_data1.iloc[t]['Close']
                    filtered_data1.at[t, 'Entry_Price'] = entry_price
                else:
                    if not pd.isna(entry_price):
                        roc = 100 * (filtered_data1.iloc[t]['Close'] - entry_price) / entry_price
                        filtered_data1.at[t, 'Entry_Price'] = entry_price
                        filtered_data1.at[t, 'Pct_Chg'] = roc
                    
                    if (
                        (spot_adjustment_type == 3 and abs(roc) >= spot_adjustment)
                        or (spot_adjustment_type == 2 and roc <= -spot_adjustment)
                        or (spot_adjustment_type == 1 and roc >= spot_adjustment)
                    ):
                        filtered_data1.at[t, 'ReEntry'] = True
                        entry_price = filtered_data1.iloc[t]['Close']
            
            filtered_data1 = filtered_data1[filtered_data1['ReEntry'] == True]
            
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
        
        return intervals
    
    def _process_trade(
        self,
        from_date: pd.Timestamp,
        to_date: pd.Timestamp,
        curr_expiry: pd.Timestamp,
        fut_expiry: pd.Timestamp,
        filtered_data: pd.DataFrame,
        call_sell_position: float,
        symbol: str
    ) -> Dict[str, Any]:
        """Process a single trade"""
        
        # Get entry and exit spot prices
        entry_spot_data = filtered_data[filtered_data['Date'] == from_date]
        exit_spot_data = filtered_data[filtered_data['Date'] == to_date]
        
        if entry_spot_data.empty:
            return None
        
        entry_spot = entry_spot_data.iloc[0]['Close']
        exit_spot = exit_spot_data.iloc[0]['Close'] if not exit_spot_data.empty else None
        
        # Calculate call strike
        call_strike = round((entry_spot * (1 + (call_sell_position / 100))) / 100) * 100
        
        # Get bhavcopy data for entry and exit dates
        bhav_df1 = self.data_provider.get_data_for_date(from_date.strftime("%Y-%m-%d"), symbol)
        bhav_df2 = self.data_provider.get_data_for_date(to_date.strftime("%Y-%m-%d"), symbol)
        
        if bhav_df1.empty or bhav_df2.empty:
            self._create_log_entry(
                symbol, "Bhavcopy data missing", curr_expiry, pd.NaT, fut_expiry, from_date, to_date
            )
            return None
        
        # Get call option data
        call_entry_data = bhav_df1[
            (bhav_df1['Instrument'] == "OPTIDX")
            & (bhav_df1['Symbol'] == symbol)
            & (bhav_df1['OptionType'] == "CE")
            & (bhav_df1['ExpiryDate'] == curr_expiry)
            & (bhav_df1['StrikePrice'] == call_strike)
        ]
        
        call_exit_data = bhav_df2[
            (bhav_df2['Instrument'] == "OPTIDX")
            & (bhav_df2['Symbol'] == symbol)
            & (bhav_df2['OptionType'] == "CE")
            & (bhav_df2['ExpiryDate'] == curr_expiry)
            & (bhav_df2['StrikePrice'] == call_strike)
        ]
        
        if call_entry_data.empty or call_exit_data.empty:
            self._create_log_entry(
                symbol, f"Call option data missing for strike {call_strike}",
                curr_expiry, pd.NaT, fut_expiry, from_date, to_date
            )
            return None
        
        call_entry_price = call_entry_data.iloc[0]['Close']
        call_exit_price = call_exit_data.iloc[0]['Close']
        call_net = round(call_entry_price - call_exit_price, 2)
        
        # Get future data
        fut_entry_data = bhav_df1[
            (bhav_df1['Instrument'] == "FUTIDX")
            & (bhav_df1['Symbol'] == symbol)
            & (bhav_df1['ExpiryDate'].dt.month == fut_expiry.month)
            & (bhav_df1['ExpiryDate'].dt.year == fut_expiry.year)
        ]
        
        fut_exit_data = bhav_df2[
            (bhav_df2['Instrument'] == "FUTIDX")
            & (bhav_df2['Symbol'] == symbol)
            & (bhav_df2['ExpiryDate'].dt.month == fut_expiry.month)
            & (bhav_df2['ExpiryDate'].dt.year == fut_expiry.year)
        ]
        
        if fut_entry_data.empty or fut_exit_data.empty:
            self._create_log_entry(
                symbol, "Future data missing", curr_expiry, pd.NaT, fut_expiry, from_date, to_date
            )
            return None
        
        fut_entry_price = fut_entry_data.iloc[0]['Close']
        fut_exit_price = fut_exit_data.iloc[0]['Close']
        fut_net = round(fut_exit_price - fut_entry_price, 2)
        
        total_net = round(call_net + fut_net, 2)
        
        return {
            "Entry Date": from_date,
            "Exit Date": to_date,
            "Entry Spot": entry_spot,
            "Exit Spot": exit_spot,
            "Future Expiry": fut_expiry,
            "Future EntryPrice": fut_entry_price,
            "Future ExitPrice": fut_exit_price,
            "Future P&L": fut_net,
            "Call Expiry": curr_expiry,
            "Call Strike": call_strike,
            "Call EntryPrice": call_entry_price,
            "Call ExitPrice": call_exit_price,
            "Call P&L": call_net,
            "Net P&L": total_net
        }
