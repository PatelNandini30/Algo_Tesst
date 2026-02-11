#!/usr/bin/env python3
"""
Deep Validation Module
======================
Performs granular, value-level comparison between CSV and DB.
Ensures no rounding drift, no silent overwrites, no data corruption.

Author: Senior Data Engineering Team
Version: 1.0.0
"""

import sqlite3
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime
import hashlib


@dataclass
class RowComparison:
    """Container for row-level comparison results"""
    business_key: Dict
    csv_values: Dict
    db_values: Dict
    mismatched_columns: List[str]
    is_match: bool


class DeepValidator:
    """
    Performs deep, value-level validation between CSV and database.
    Detects even subtle differences like rounding errors or type conversions.
    """
    
    BUSINESS_KEY = ['Date', 'Symbol', 'Instrument', 'ExpiryDate', 'StrikePrice', 'OptionType']
    VALUE_COLUMNS = ['Open', 'High', 'Low', 'Close', 'SettledPrice', 
                     'Contracts', 'TurnOver', 'OpenInterest']
    
    # Tolerance for floating point comparison (very strict)
    FLOAT_TOLERANCE = 1e-6
    
    def __init__(self, db_path: str):
        """
        Initialize deep validator.
        
        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self.conn = None
        self.mismatches = []
    
    def connect(self):
        """Establish database connection"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
    
    def disconnect(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
    
    def compare_date_range(self, 
                          csv_df: pd.DataFrame, 
                          start_date: str, 
                          end_date: str,
                          sample_size: Optional[int] = None) -> Dict:
        """
        Compare CSV data against database for a specific date range.
        
        Args:
            csv_df: DataFrame containing CSV data
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            sample_size: If provided, randomly sample this many rows
            
        Returns:
            Dictionary containing comparison results
        """
        # Normalize CSV data
        csv_df = self._normalize_dataframe(csv_df)
        
        # Filter by date range
        csv_df = csv_df[
            (csv_df['Date'] >= start_date) & 
            (csv_df['Date'] <= end_date)
        ]
        
        if sample_size and len(csv_df) > sample_size:
            csv_df = csv_df.sample(n=sample_size, random_state=42)
        
        # Fetch corresponding data from database
        db_df = self._fetch_db_data(start_date, end_date)
        
        # Perform row-by-row comparison
        comparison_results = self._compare_dataframes(csv_df, db_df)
        
        return comparison_results
    
    def _normalize_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalize DataFrame columns and data types.
        
        Args:
            df: Input DataFrame
            
        Returns:
            Normalized DataFrame
        """
        # Make a copy to avoid modifying original
        df = df.copy()
        
        # Normalize column names
        df.columns = df.columns.str.strip()
        
        # Convert dates to standard format
        if 'Date' in df.columns:
            df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
            df['Date'] = df['Date'].dt.strftime('%Y-%m-%d')
        
        if 'ExpiryDate' in df.columns:
            df['ExpiryDate'] = pd.to_datetime(df['ExpiryDate'], errors='coerce')
            df['ExpiryDate'] = df['ExpiryDate'].dt.strftime('%Y-%m-%d')
        
        # Convert numeric columns
        for col in self.VALUE_COLUMNS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Handle NaN values consistently
        df = df.replace({np.nan: None})
        
        return df
    
    def _fetch_db_data(self, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch data from database for date range.
        
        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            
        Returns:
            DataFrame containing database data
        """
        query = """
        SELECT 
            Date,
            Symbol,
            Instrument,
            ExpiryDate,
            StrikePrice,
            OptionType,
            Open,
            High,
            Low,
            Close,
            SettledPrice,
            Contracts,
            TurnOver,
            OpenInterest
        FROM cleaned_csvs
        WHERE Date >= ? AND Date <= ?
        ORDER BY Date, Symbol, Instrument, ExpiryDate, StrikePrice, OptionType
        """
        
        df = pd.read_sql_query(query, self.conn, params=(start_date, end_date))
        
        return self._normalize_dataframe(df)
    
    def _create_business_key_hash(self, row: pd.Series) -> str:
        """
        Create hash from business key columns.
        
        Args:
            row: DataFrame row
            
        Returns:
            Hash string
        """
        key_values = [str(row[col]) for col in self.BUSINESS_KEY if col in row.index]
        key_string = '|'.join(key_values)
        return hashlib.md5(key_string.encode()).hexdigest()
    
    def _compare_dataframes(self, csv_df: pd.DataFrame, db_df: pd.DataFrame) -> Dict:
        """
        Compare two DataFrames row by row.
        
        Args:
            csv_df: CSV DataFrame
            db_df: Database DataFrame
            
        Returns:
            Dictionary containing comparison results
        """
        # Create hash indices for fast lookup
        csv_df['_key_hash'] = csv_df.apply(self._create_business_key_hash, axis=1)
        db_df['_key_hash'] = db_df.apply(self._create_business_key_hash, axis=1)
        
        csv_keys = set(csv_df['_key_hash'])
        db_keys = set(db_df['_key_hash'])
        
        # Find missing and extra records
        missing_in_db = csv_keys - db_keys
        extra_in_db = db_keys - csv_keys
        common_keys = csv_keys & db_keys
        
        # Compare common records
        value_mismatches = []
        perfect_matches = 0
        
        for key_hash in common_keys:
            csv_row = csv_df[csv_df['_key_hash'] == key_hash].iloc[0]
            db_row = db_df[db_df['_key_hash'] == key_hash].iloc[0]
            
            comparison = self._compare_rows(csv_row, db_row)
            
            if comparison.is_match:
                perfect_matches += 1
            else:
                value_mismatches.append(comparison)
        
        results = {
            'total_csv_rows': len(csv_df),
            'total_db_rows': len(db_df),
            'common_records': len(common_keys),
            'missing_in_db': len(missing_in_db),
            'extra_in_db': len(extra_in_db),
            'perfect_matches': perfect_matches,
            'value_mismatches': len(value_mismatches),
            'mismatch_details': value_mismatches[:100],  # Limit to first 100
            'missing_records_sample': self._get_sample_records(csv_df, missing_in_db, limit=10),
            'extra_records_sample': self._get_sample_records(db_df, extra_in_db, limit=10)
        }
        
        return results
    
    def _compare_rows(self, csv_row: pd.Series, db_row: pd.Series) -> RowComparison:
        """
        Compare two rows value by value.
        
        Args:
            csv_row: Row from CSV
            db_row: Row from database
            
        Returns:
            RowComparison object
        """
        business_key = {col: csv_row[col] for col in self.BUSINESS_KEY if col in csv_row.index}
        csv_values = {}
        db_values = {}
        mismatched_columns = []
        
        for col in self.VALUE_COLUMNS:
            if col not in csv_row.index or col not in db_row.index:
                continue
            
            csv_val = csv_row[col]
            db_val = db_row[col]
            
            csv_values[col] = csv_val
            db_values[col] = db_val
            
            # Compare values with appropriate tolerance
            if not self._values_equal(csv_val, db_val):
                mismatched_columns.append(col)
        
        is_match = len(mismatched_columns) == 0
        
        return RowComparison(
            business_key=business_key,
            csv_values=csv_values,
            db_values=db_values,
            mismatched_columns=mismatched_columns,
            is_match=is_match
        )
    
    def _values_equal(self, val1, val2) -> bool:
        """
        Compare two values with appropriate tolerance.
        
        Args:
            val1: First value
            val2: Second value
            
        Returns:
            True if values are equal within tolerance
        """
        # Handle None/NULL
        if pd.isna(val1) and pd.isna(val2):
            return True
        if pd.isna(val1) or pd.isna(val2):
            return False
        
        # Handle numeric comparison with tolerance
        if isinstance(val1, (int, float)) and isinstance(val2, (int, float)):
            return abs(float(val1) - float(val2)) < self.FLOAT_TOLERANCE
        
        # String comparison
        return str(val1) == str(val2)
    
    def _get_sample_records(self, df: pd.DataFrame, key_hashes: set, limit: int = 10) -> List[Dict]:
        """
        Get sample records for given key hashes.
        
        Args:
            df: DataFrame to sample from
            key_hashes: Set of key hashes
            limit: Maximum number of samples
            
        Returns:
            List of record dictionaries
        """
        if not key_hashes:
            return []
        
        sample_hashes = list(key_hashes)[:limit]
        sample_df = df[df['_key_hash'].isin(sample_hashes)]
        
        records = []
        for _, row in sample_df.iterrows():
            record = {col: row[col] for col in self.BUSINESS_KEY if col in row.index}
            records.append(record)
        
        return records
    
    def validate_data_types(self, csv_df: pd.DataFrame) -> Dict:
        """
        Validate that data types match expectations.
        
        Args:
            csv_df: CSV DataFrame
            
        Returns:
            Dictionary containing validation results
        """
        issues = []
        
        # Expected types
        expected_types = {
            'Date': 'object',  # String date
            'Symbol': 'object',
            'Instrument': 'object',
            'ExpiryDate': 'object',
            'StrikePrice': 'float64',
            'OptionType': 'object',
            'Open': 'float64',
            'High': 'float64',
            'Low': 'float64',
            'Close': 'float64',
            'SettledPrice': 'float64',
            'Contracts': 'int64',
            'TurnOver': 'float64',
            'OpenInterest': 'int64'
        }
        
        for col, expected_type in expected_types.items():
            if col in csv_df.columns:
                actual_type = str(csv_df[col].dtype)
                
                # Check for compatible types
                if expected_type == 'float64' and actual_type not in ['float64', 'float32', 'int64', 'int32']:
                    issues.append({
                        'column': col,
                        'expected_type': expected_type,
                        'actual_type': actual_type
                    })
                elif expected_type == 'int64' and actual_type not in ['int64', 'int32']:
                    issues.append({
                        'column': col,
                        'expected_type': expected_type,
                        'actual_type': actual_type
                    })
        
        return {
            'has_issues': len(issues) > 0,
            'issues': issues
        }
    
    def compute_data_fingerprint(self, df: pd.DataFrame) -> str:
        """
        Compute a fingerprint (hash) of the entire dataset.
        Useful for quick equality checks.
        
        Args:
            df: DataFrame to fingerprint
            
        Returns:
            SHA256 hash string
        """
        # Sort by business key to ensure deterministic order
        df_sorted = df.sort_values(by=self.BUSINESS_KEY)
        
        # Convert to string representation
        data_string = df_sorted.to_csv(index=False)
        
        # Compute hash
        fingerprint = hashlib.sha256(data_string.encode()).hexdigest()
        
        return fingerprint


def run_deep_validation(db_path: str, csv_path: str, start_date: str, end_date: str):
    """
    Run deep validation on a specific date range.
    
    Args:
        db_path: Path to SQLite database
        csv_path: Path to CSV file
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
    """
    validator = DeepValidator(db_path)
    validator.connect()
    
    try:
        # Read CSV
        print(f"Reading CSV: {csv_path}")
        csv_df = pd.read_csv(csv_path)
        
        # Validate data types
        print("Validating data types...")
        type_validation = validator.validate_data_types(csv_df)
        
        if type_validation['has_issues']:
            print("⚠ Data type issues detected:")
            for issue in type_validation['issues']:
                print(f"  {issue['column']}: expected {issue['expected_type']}, got {issue['actual_type']}")
        
        # Compare data
        print(f"\nComparing data for range: {start_date} to {end_date}")
        results = validator.compare_date_range(csv_df, start_date, end_date)
        
        # Print results
        print("\n" + "="*80)
        print("DEEP VALIDATION RESULTS")
        print("="*80)
        print(f"CSV Rows: {results['total_csv_rows']}")
        print(f"DB Rows: {results['total_db_rows']}")
        print(f"Common Records: {results['common_records']}")
        print(f"Perfect Matches: {results['perfect_matches']}")
        print(f"Value Mismatches: {results['value_mismatches']}")
        print(f"Missing in DB: {results['missing_in_db']}")
        print(f"Extra in DB: {results['extra_in_db']}")
        
        if results['value_mismatches'] > 0:
            print("\nSample Mismatches:")
            for i, mismatch in enumerate(results['mismatch_details'][:5], 1):
                print(f"\n{i}. Business Key: {mismatch.business_key}")
                print(f"   Mismatched Columns: {mismatch.mismatched_columns}")
                for col in mismatch.mismatched_columns:
                    print(f"   {col}: CSV={mismatch.csv_values[col]}, DB={mismatch.db_values[col]}")
        
        # Determine pass/fail
        is_perfect = (
            results['value_mismatches'] == 0 and
            results['missing_in_db'] == 0 and
            results['extra_in_db'] == 0
        )
        
        status = "✓ PASSED" if is_perfect else "✗ FAILED"
        print(f"\nDeep Validation: {status}")
        
    finally:
        validator.disconnect()


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Deep validation of CSV vs Database')
    parser.add_argument('--db', required=True, help='Path to SQLite database')
    parser.add_argument('--csv', required=True, help='Path to CSV file')
    parser.add_argument('--start-date', required=True, help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', required=True, help='End date (YYYY-MM-DD)')
    
    args = parser.parse_args()
    
    run_deep_validation(args.db, args.csv, args.start_date, args.end_date)
