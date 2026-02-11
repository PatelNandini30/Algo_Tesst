"""
Call Sell + Future Buy Strategy - T-1 to T-1 Weekly Expiry
Converted from main2() function in analyse_bhavcopy_02-01-2026.py
"""
import pandas as pd
import numpy as np
from datetime import timedelta
from typing import Dict, Any, List
from .base import StrategyInterface, StrategyParameter, StrategyResult


class CallSellFutureBuyT1Strategy(StrategyInterface):
    """
    Call Sell + Future Buy Strategy - T-1 to T-1 Weekly Expiry
    
    Entry: T-1 day before previous weekly expiry
    Exit: T-1 day before current weekly expiry
    
    Strategy:
    - Sell Call options at calculated strike (ATM/OTM/ITM based on position)
    - Buy Future contracts for hedging
    - Optional spot adjustment for re-entry
    """
    
    def get_name(self) -> str:
        return "call_sell_future_buy_t1"
    
    def get_description(self) -> str:
        return "Call Sell + Future Buy - T-1 to T-1 Weekly Expiry"
    
    def get_version(self) -> str:
        return "1.0.0"
    
    def get_parameter_schema(self) -> List[StrategyParameter]:
        return [
            StrategyParameter(
                name="spot_adjustment_type",
                type="integer",
                required=False,
                default=0,
                min_value=0,
                max_value=3,
                description="Spot adjustment type: 0=None, 1=Rise, 2=Fall, 3=Both"
            ),
            StrategyParameter(
                name="spot_adjustment",
                type="number",
                required=False,
                default=1.0,
                min_value=0.0,
                max_value=100.0,
                description="Spot adjustment percentage threshold"
            ),
            StrategyParameter(
                name="call_sell_position",
                type="number",
                required=False,
                default=0.0,
                min_value=-50.0,
                max_value=50.0,
                description="Call strike position: 0=ATM, +ve=OTM, -ve=ITM (percentage)"
            ),
            StrategyParameter(
                name="symbol",
                type="string",
                required=False,
                default="NIFTY",
                options=["NIFTY", "BANKNIFTY", "FINNIFTY"],
                description="Index symbol to trade"
            )
        ]
    
    def validate_parameters(self, parameters: Dict[str, Any]) -> tuple[bool, str]:
        """Validate strategy parameters."""
        spot_adj_type = parameters.get("spot_adjustment_type", 0)
        if spot_adj_type not in [0, 1, 2, 3]:
            return False, "spot_adjustment_type must be 0, 1, 2, or 3"
        
        spot_adj = parameters.get("spot_adjustment", 1.0)
        if not (0 <= spot_adj <= 100):
            return False, "spot_adjustment must be between 0 and 100"
        
        call_pos = parameters.get("call_sell_position", 0.0)
        if not (-50 <= call_pos <= 50):
            return False, "call_sell_position must be between -50 and 50"
        
        return True, ""
    
    def execute(self, data_provider, parameters: Dict[str, Any]) -> StrategyResult:
        """Execute the T-1 to T-1 strategy."""
        import time
        start_time = time.time()
        
        try:
            # Extract parameters
            spot_adjustment_type = parameters.get("spot_adjustment_type", 0)
            spot_adjustment = parameters.get("spot_adjustment", 1.0)
            call_sell_position = parameters.get("call_sell_position", 0.0)
            symbol = parameters.get("symbol", "NIFTY")
            
            # Get data
            data_df = data_provider.get_strike_data(symbol)
            base2_df = data_provider.get_filter_data("base2")
            
            # Filter data by base2 periods
            mask = pd.Series(False, index=data_df.index)
            for _, row in base2_df.iterrows():
                mask |= (data_df['Date'] >= row['Start']) & (data_df['Date'] <= row['End'])
            data_df_1 = data_df[mask].reset_index(drop=True).copy(deep=True)
            
            # Get expiry data
            weekly_expiry_df = data_provider.get_expiry_data(symbol, "weekly")
            monthly_expiry_df = data_provider.get_expiry_data(symbol, "monthly")
            
            analysis_data = []
            first_instance = False
            
            # Iterate through weekly expiries
            for w in range(len(weekly_expiry_df)):
                prev_expiry = weekly_expiry_df.iloc[w]['Previous Expiry']
                curr_expiry = weekly_expiry_df.iloc[w]['Current Expiry']
                
                # Get current monthly expiry for futures
                curr_monthly_expiry = monthly_expiry_df[
                    monthly_expiry_df['Current Expiry'] >= curr_expiry
                ].sort_values(by='Current Expiry').reset_index(drop=True)
                
                if curr_monthly_expiry.empty:
                    continue
                
                curr_fut_expiry = curr_monthly_expiry.iloc[0]['Current Expiry']
                fut_expiry = curr_fut_expiry
                
                # Filter data for current period
                if not first_instance:
                    filtered_data = data_df_1[
                        (data_df_1['Date'] >= prev_expiry) &
                        (data_df_1['Date'] < curr_expiry)
                    ].sort_values(by='Date').reset_index(drop=True)
                    first_instance = True
                else:
                    # Get T-1 date before previous expiry
                    last_date_before_expiry = data_df[data_df['Date'] < prev_expiry]
                    
                    if last_date_before_expiry.empty:
                        last_date_before_expiry = prev_expiry
                    else:
                        last_date_before_expiry = last_date_before_expiry.iloc[-1]['Date']
                    
                    filtered_data = data_df_1[
                        (data_df_1['Date'] >= last_date_before_expiry) &
                        (data_df_1['Date'] < curr_expiry)
                    ].sort_values(by='Date').reset_index(drop=True)
                
                if len(filtered_data) < 2:
                    continue
                
                # Calculate intervals based on spot adjustment
                intervals = self._calculate_intervals(
                    filtered_data, spot_adjustment_type, spot_adjustment
                )
                
                if not intervals:
                    continue
                
                interval_df = pd.DataFrame(intervals, columns=['From', 'To'])
                
                # Process each interval
                for i in range(len(interval_df)):
                    fromDate = interval_df.iloc[i]['From']
                    toDate = interval_df.iloc[i]['To']
                    
                    if fromDate == toDate:
                        continue
                    
                    # Check if this is a base start date
                    is_base_start = (base2_df['Start'] == fromDate).any()
                    if is_base_start:
                        fut_expiry = monthly_expiry_df[
                            monthly_expiry_df['Current Expiry'] > fromDate
                        ].iloc[1]['Current Expiry']
                    
                    # Execute trade
                    trade_result = self._execute_trade(
                        data_provider, filtered_data, fromDate, toDate,
                        curr_expiry, fut_expiry, call_sell_position, symbol
                    )
                    
                    if trade_result:
                        analysis_data.append(trade_result)
            
            # Create result DataFrame
            if analysis_data:
                result_df = pd.DataFrame(analysis_data)
                execution_time_ms = int((time.time() - start_time) * 1000)
                
                return StrategyResult(
                    success=True,
                    data=result_df.to_dict(orient='records'),
                    metadata={
                        "strategy": self.get_name(),
                        "parameters": parameters,
                        "total_trades": len(analysis_data),
                        "symbol": symbol
                    },
                    execution_time_ms=execution_time_ms,
                    row_count=len(analysis_data)
                )
            else:
                return StrategyResult(
                    success=True,
                    data=[],
                    metadata={
                        "strategy": self.get_name(),
                        "parameters": parameters,
                        "message": "No trades generated"
                    },
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    row_count=0
                )
                
        except Exception as e:
            return StrategyResult(
                success=False,
                data=[],
                metadata={
                    "strategy": self.get_name(),
                    "error": str(e)
                },
                execution_time_ms=int((time.time() - start_time) * 1000),
                row_count=0
            )
    
    def _calculate_intervals(self, filtered_data: pd.DataFrame, 
                           spot_adjustment_type: int, 
                           spot_adjustment: float) -> List[tuple]:
        """Calculate trading intervals based on spot adjustment."""
        intervals = []
        
        if spot_adjustment_type == 0:
            # No adjustment
            intervals.append((filtered_data.iloc[0]['Date'], filtered_data.iloc[-1]['Date']))
        else:
            # Calculate re-entry points
            filtered_data1 = filtered_data.copy(deep=True)
            filtered_data1['ReEntry'] = False
            filtered_data1['Entry_Price'] = None
            filtered_data1['Pct_Chg'] = None
            entryPrice = None
            
            for t in range(len(filtered_data1)):
                if t == 0:
                    entryPrice = filtered_data1.iloc[t]['Close']
                    filtered_data1.at[t, 'Entry_Price'] = entryPrice
                else:
                    if not pd.isna(entryPrice):
                        roc = 100 * (filtered_data1.iloc[t]['Close'] - entryPrice) / entryPrice
                        filtered_data1.at[t, 'Entry_Price'] = entryPrice
                        filtered_data1.at[t, 'Pct_Chg'] = roc
                    
                    if ((spot_adjustment_type == 3 and abs(roc) >= spot_adjustment) or
                        (spot_adjustment_type == 2 and roc <= -spot_adjustment) or
                        (spot_adjustment_type == 1 and roc >= spot_adjustment)):
                        filtered_data1.at[t, 'ReEntry'] = True
                        entryPrice = filtered_data1.iloc[t]['Close']
            
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
        
        return intervals
    
    def _execute_trade(self, data_provider, filtered_data: pd.DataFrame,
                      fromDate, toDate, curr_expiry, fut_expiry,
                      call_sell_position: float, symbol: str) -> Dict[str, Any]:
        """Execute a single trade."""
        # Get entry and exit spot prices
        entrySpot = filtered_data[filtered_data['Date'] == fromDate]
        exitSpot = filtered_data[filtered_data['Date'] == toDate]
        
        if entrySpot.empty:
            return None
        
        entrySpot = entrySpot.iloc[0]['Close']
        call_strike = round((entrySpot * (1 + (call_sell_position / 100))) / 100) * 100
        
        exitSpot = exitSpot.iloc[0]['Close'] if not exitSpot.empty else None
        
        # Get bhavcopy data
        bhav_df1 = data_provider.get_data_for_date(fromDate)
        bhav_df2 = data_provider.get_data_for_date(toDate)
        
        if bhav_df1.empty or bhav_df2.empty:
            return None
        
        # Get call option data
        if call_sell_position >= 0:
            call_entry_data = bhav_df1[
                (bhav_df1['Instrument'] == "OPTIDX") &
                (bhav_df1['Symbol'] == symbol) &
                (bhav_df1['OptionType'] == "CE") &
                (bhav_df1['ExpiryDate'].isin([curr_expiry, 
                                              curr_expiry - timedelta(days=1),
                                              curr_expiry + timedelta(days=1)])) &
                (bhav_df1['StrikePrice'] >= call_strike) &
                (bhav_df1['TurnOver'] > 0) &
                (bhav_df1['StrikePrice'] % 100 == 0)
            ].sort_values(by='StrikePrice', ascending=True).reset_index(drop=True)
        else:
            call_entry_data = bhav_df1[
                (bhav_df1['Instrument'] == "OPTIDX") &
                (bhav_df1['Symbol'] == symbol) &
                (bhav_df1['OptionType'] == "CE") &
                (bhav_df1['ExpiryDate'].isin([curr_expiry,
                                              curr_expiry - timedelta(days=1),
                                              curr_expiry + timedelta(days=1)])) &
                (bhav_df1['StrikePrice'] <= call_strike) &
                (bhav_df1['TurnOver'] > 0) &
                (bhav_df1['StrikePrice'] % 100 == 0)
            ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)
        
        if call_entry_data.empty:
            return None
        
        call_strike = call_entry_data.iloc[0]['StrikePrice']
        call_entry_data = call_entry_data[call_entry_data['StrikePrice'] == call_strike]
        
        call_exit_data = bhav_df2[
            (bhav_df2['Instrument'] == "OPTIDX") &
            (bhav_df2['Symbol'] == symbol) &
            (bhav_df2['OptionType'] == "CE") &
            (bhav_df2['ExpiryDate'].isin([curr_expiry,
                                          curr_expiry - timedelta(days=1),
                                          curr_expiry + timedelta(days=1)])) &
            (bhav_df2['StrikePrice'] == call_strike)
        ]
        
        if call_entry_data.empty or call_exit_data.empty:
            return None
        
        call_entry_price = call_entry_data.iloc[0]['Close']
        call_entry_turnover = call_entry_data.iloc[0]['TurnOver']
        call_exit_price = call_exit_data.iloc[0]['Close']
        call_exit_turnover = call_exit_data.iloc[0]['TurnOver']
        call_net = round(call_entry_price - call_exit_price, 2)
        
        # Get future data
        fut_entry_data = bhav_df1[
            (bhav_df1['Instrument'] == "FUTIDX") &
            (bhav_df1['Symbol'] == symbol) &
            (bhav_df1['ExpiryDate'].dt.month == fut_expiry.month) &
            (bhav_df1['ExpiryDate'].dt.year == fut_expiry.year)
        ]
        
        fut_exit_data = bhav_df2[
            (bhav_df2['Instrument'] == "FUTIDX") &
            (bhav_df2['Symbol'] == symbol) &
            (bhav_df2['ExpiryDate'].dt.month == fut_expiry.month) &
            (bhav_df2['ExpiryDate'].dt.year == fut_expiry.year)
        ]
        
        if fut_entry_data.empty or fut_exit_data.empty:
            return None
        
        fut_entry_price = fut_entry_data.iloc[0]['Close']
        fut_exit_price = fut_exit_data.iloc[0]['Close']
        fut_net = round(fut_exit_price - fut_entry_price, 2)
        
        total_net = round(call_net + fut_net, 2)
        
        return {
            "Entry Date": fromDate,
            "Exit Date": toDate,
            "Entry Spot": entrySpot,
            "Exit Spot": exitSpot,
            "Future Expiry": fut_expiry,
            "Future EntryPrice": fut_entry_price,
            "Future ExitPrice": fut_exit_price,
            "Future P&L": fut_net,
            "Call Expiry": curr_expiry,
            "Call Strike": call_strike,
            "Call EntryPrice": call_entry_price,
            "Call Entry Turnover": call_entry_turnover,
            "Call ExitPrice": call_exit_price,
            "Call Exit Turnover": call_exit_turnover,
            "Call P&L": call_net,
            "Net P&L": total_net
        }
