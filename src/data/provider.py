"""
Data Provider - Abstraction layer for database access
"""
import sqlite3
import pandas as pd
from typing import Optional, List
from datetime import datetime
from contextlib import contextmanager


class DataProvider:
    """Provides access to market data from SQLite database"""
    
    def __init__(self, db_path: str = "bhavcopy_data.db"):
        self.db_path = db_path
    
    @contextmanager
    def get_connection(self):
        """Context manager for database connections"""
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()
    
    def get_cleaned_data(
        self,
        start_date: str,
        end_date: str,
        symbol: Optional[str] = None,
        instrument: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Get cleaned bhavcopy data for date range
        
        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            symbol: Optional symbol filter (e.g., 'NIFTY')
            instrument: Optional instrument filter (e.g., 'OPTIDX', 'FUTIDX')
        
        Returns:
            DataFrame with market data
        """
        query = """
            SELECT * FROM cleaned_csvs
            WHERE Date >= ? AND Date <= ?
        """
        params = [start_date, end_date]
        
        if symbol:
            query += " AND Symbol = ?"
            params.append(symbol)
        
        if instrument:
            query += " AND Instrument = ?"
            params.append(instrument)
        
        query += " ORDER BY Date, Symbol, StrikePrice"
        
        with self.get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=params)
        
        # Convert date columns
        df['Date'] = pd.to_datetime(df['Date'])
        df['ExpiryDate'] = pd.to_datetime(df['ExpiryDate'])
        
        return df
    
    def get_data_for_date(self, date: str, symbol: Optional[str] = None) -> pd.DataFrame:
        """Get all data for a specific date"""
        query = "SELECT * FROM cleaned_csvs WHERE Date = ?"
        params = [date]
        
        if symbol:
            query += " AND Symbol = ?"
            params.append(symbol)
        
        with self.get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=params)
        
        df['Date'] = pd.to_datetime(df['Date'])
        df['ExpiryDate'] = pd.to_datetime(df['ExpiryDate'])
        
        return df
    
    def get_strike_data(self, symbol: str) -> pd.DataFrame:
        """
        Load strike/spot price data from strikeData folder
        
        Args:
            symbol: Symbol name (e.g., 'NIFTY')
        
        Returns:
            DataFrame with Date and Close columns
        """
        try:
            df = pd.read_csv(f"./strikeData/{symbol}.csv")
            df['Date'] = pd.to_datetime(df['Date'])
            return df
        except FileNotFoundError:
            raise ValueError(f"Strike data not found for symbol: {symbol}")
    
    def get_expiry_data(self, symbol: str, expiry_type: str = "weekly") -> pd.DataFrame:
        """
        Load expiry data
        
        Args:
            symbol: Symbol name (e.g., 'NIFTY')
            expiry_type: 'weekly' or 'monthly'
        
        Returns:
            DataFrame with expiry dates
        """
        if expiry_type == "monthly":
            filename = f"./expiryData/{symbol}_Monthly.csv"
        else:
            filename = f"./expiryData/{symbol}.csv"
        
        try:
            df = pd.read_csv(filename)
            df['Previous Expiry'] = pd.to_datetime(df['Previous Expiry'])
            df['Current Expiry'] = pd.to_datetime(df['Current Expiry'])
            df['Next Expiry'] = pd.to_datetime(df['Next Expiry'])
            return df
        except FileNotFoundError:
            raise ValueError(f"Expiry data not found: {filename}")
    
    def get_filter_data(self, filter_name: str) -> pd.DataFrame:
        """
        Load filter data (e.g., base2.csv)
        
        Args:
            filter_name: Name of filter file (without .csv)
        
        Returns:
            DataFrame with filter data
        """
        try:
            df = pd.read_csv(f"./Filter/{filter_name}.csv")
            if 'Start' in df.columns:
                df['Start'] = pd.to_datetime(df['Start'])
            if 'End' in df.columns:
                df['End'] = pd.to_datetime(df['End'])
            return df
        except FileNotFoundError:
            raise ValueError(f"Filter data not found: {filter_name}")
    
    def get_trading_dates(self, start_date: str, end_date: str) -> List[str]:
        """Get list of trading dates in range"""
        query = """
            SELECT DISTINCT Date FROM cleaned_csvs
            WHERE Date >= ? AND Date <= ?
            ORDER BY Date
        """
        
        with self.get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=[start_date, end_date])
        
        return df['Date'].tolist()
    
    def get_available_symbols(self) -> List[str]:
        """Get list of available symbols"""
        query = "SELECT DISTINCT Symbol FROM cleaned_csvs ORDER BY Symbol"
        
        with self.get_connection() as conn:
            df = pd.read_sql_query(query, conn)
        
        return df['Symbol'].tolist()
