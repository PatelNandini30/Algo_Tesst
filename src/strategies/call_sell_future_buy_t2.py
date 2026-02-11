"""
Call Sell + Future Buy Strategy - T-2 to T-2 Weekly Expiry
Converted from main2(t_2=True) function in analyse_bhavcopy_02-01-2026.py
"""
import pandas as pd
from datetime import timedelta
from typing import Dict, Any, List
from .call_sell_future_buy_t1 import CallSellFutureBuyT1Strategy
from .base import StrategyParameter, StrategyResult


class CallSellFutureBuyT2Strategy(CallSellFutureBuyT1Strategy):
    """
    Call Sell + Future Buy Strategy - T-2 to T-2 Weekly Expiry
    
    Entry: T-2 days before previous weekly expiry
    Exit: T-2 days before current weekly expiry
    
    Inherits from T1 strategy with T-2 specific logic.
    """
    
    def get_name(self) -> str:
        return "call_sell_future_buy_t2"
    
    def get_description(self) -> str:
        return "Call Sell + Future Buy - T-2 to T-2 Weekly Expiry"
    
    def execute(self, data_provider, parameters: Dict[str, Any]) -> StrategyResult:
        """Execute the T-2 to T-2 strategy."""
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
                    # Get T-2 date before previous expiry
                    last_date_before_expiry = data_df[data_df['Date'] < prev_expiry]
                    
                    if len(last_date_before_expiry) < 2:
                        last_date_before_expiry = prev_expiry
                    else:
                        last_date_before_expiry = last_date_before_expiry.iloc[-2]['Date']
                    
                    filtered_data = data_df_1[
                        (data_df_1['Date'] >= last_date_before_expiry) &
                        (data_df_1['Date'] < curr_expiry)
                    ].sort_values(by='Date').reset_index(drop=True)
                
                if len(filtered_data) < 2:
                    continue
                
                # T-2 specific: Check base end logic
                base_ends = base2_df.loc[
                    (base2_df['End'] > prev_expiry) & (base2_df['End'] < curr_expiry),
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
