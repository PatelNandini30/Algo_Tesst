#!/usr/bin/env python3
"""
Database Utilities
==================
Common operations and maintenance tasks for NSE Bhavcopy database.

Author: Senior Data Engineering Team
Version: 1.0.0
"""

import sqlite3
import sys
import json
from datetime import datetime
from typing import Dict, List
import argparse


class DatabaseUtils:
    """Utility functions for database maintenance"""
    
    def __init__(self, db_path: str):
        """Initialize with database path"""
        self.db_path = db_path
        self.conn = None
    
    def connect(self):
        """Connect to database"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
    
    def disconnect(self):
        """Disconnect from database"""
        if self.conn:
            self.conn.close()
    
    def get_statistics(self) -> Dict:
        """Get comprehensive database statistics"""
        stats = {}
        cursor = self.conn.cursor()
        
        # cleaned_csvs stats
        cursor.execute("SELECT COUNT(*) FROM cleaned_csvs")
        stats['total_records'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT Date) FROM cleaned_csvs")
        stats['unique_dates'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT Symbol) FROM cleaned_csvs")
        stats['unique_symbols'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT MIN(Date), MAX(Date) FROM cleaned_csvs")
        min_date, max_date = cursor.fetchone()
        stats['date_range'] = {'start': min_date, 'end': max_date}
        
        # Records per year
        cursor.execute("""
            SELECT strftime('%Y', Date) as year, COUNT(*) as count
            FROM cleaned_csvs
            GROUP BY year
            ORDER BY year
        """)
        stats['records_per_year'] = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Records per instrument type
        cursor.execute("""
            SELECT Instrument, COUNT(*) as count
            FROM cleaned_csvs
            GROUP BY Instrument
            ORDER BY count DESC
        """)
        stats['records_per_instrument'] = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Top symbols by record count
        cursor.execute("""
            SELECT Symbol, COUNT(*) as count
            FROM cleaned_csvs
            GROUP BY Symbol
            ORDER BY count DESC
            LIMIT 20
        """)
        stats['top_symbols'] = {row[0]: row[1] for row in cursor.fetchall()}
        
        # expiry_data stats
        cursor.execute("SELECT COUNT(*) FROM expiry_data")
        stats['expiry_records'] = cursor.fetchone()[0]
        
        # strike_data stats
        cursor.execute("SELECT COUNT(*) FROM strike_data")
        stats['strike_records'] = cursor.fetchone()[0]
        
        # Ingestion metadata
        cursor.execute("SELECT COUNT(*) FROM ingestion_metadata WHERE status = 'SUCCESS'")
        stats['files_processed'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM ingestion_metadata WHERE status = 'ERROR'")
        stats['files_failed'] = cursor.fetchone()[0]
        
        return stats
    
    def print_statistics(self):
        """Print statistics in human-readable format"""
        stats = self.get_statistics()
        
        print("\n" + "="*80)
        print("DATABASE STATISTICS")
        print("="*80)
        
        print(f"\nMain Table (cleaned_csvs):")
        print(f"  Total Records: {stats['total_records']:,}")
        print(f"  Unique Dates: {stats['unique_dates']:,}")
        print(f"  Unique Symbols: {stats['unique_symbols']:,}")
        print(f"  Date Range: {stats['date_range']['start']} to {stats['date_range']['end']}")
        
        print(f"\nRecords per Year:")
        for year, count in sorted(stats['records_per_year'].items()):
            print(f"  {year}: {count:,}")
        
        print(f"\nRecords per Instrument Type:")
        for instrument, count in stats['records_per_instrument'].items():
            print(f"  {instrument}: {count:,}")
        
        print(f"\nTop 20 Symbols:")
        for symbol, count in stats['top_symbols'].items():
            print(f"  {symbol}: {count:,}")
        
        print(f"\nAuxiliary Tables:")
        print(f"  expiry_data records: {stats['expiry_records']:,}")
        print(f"  strike_data records: {stats['strike_records']:,}")
        
        print(f"\nIngestion History:")
        print(f"  Files processed successfully: {stats['files_processed']:,}")
        print(f"  Files failed: {stats['files_failed']:,}")
    
    def check_duplicates(self) -> List[Dict]:
        """Check for duplicate records"""
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT 
                Date, Symbol, Instrument, ExpiryDate, StrikePrice, OptionType, 
                COUNT(*) as count
            FROM cleaned_csvs
            GROUP BY Date, Symbol, Instrument, ExpiryDate, StrikePrice, OptionType
            HAVING COUNT(*) > 1
        """)
        
        duplicates = []
        for row in cursor.fetchall():
            duplicates.append({
                'Date': row[0],
                'Symbol': row[1],
                'Instrument': row[2],
                'ExpiryDate': row[3],
                'StrikePrice': row[4],
                'OptionType': row[5],
                'count': row[6]
            })
        
        return duplicates
    
    def check_null_values(self) -> Dict:
        """Check for NULL values in critical columns"""
        cursor = self.conn.cursor()
        
        columns = ['Date', 'Symbol', 'Instrument', 'Open', 'High', 'Low', 'Close']
        null_counts = {}
        
        for col in columns:
            cursor.execute(f"""
                SELECT COUNT(*) FROM cleaned_csvs WHERE {col} IS NULL
            """)
            null_counts[col] = cursor.fetchone()[0]
        
        return null_counts
    
    def get_trading_dates(self, year: int = None) -> List[str]:
        """Get list of trading dates"""
        cursor = self.conn.cursor()
        
        if year:
            cursor.execute("""
                SELECT DISTINCT Date FROM cleaned_csvs
                WHERE strftime('%Y', Date) = ?
                ORDER BY Date
            """, (str(year),))
        else:
            cursor.execute("""
                SELECT DISTINCT Date FROM cleaned_csvs
                ORDER BY Date
            """)
        
        return [row[0] for row in cursor.fetchall()]
    
    def get_symbols(self) -> List[str]:
        """Get list of unique symbols"""
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT Symbol FROM cleaned_csvs
            ORDER BY Symbol
        """)
        
        return [row[0] for row in cursor.fetchall()]
    
    def vacuum_database(self):
        """Vacuum database to reclaim space"""
        print("Running VACUUM...")
        self.conn.execute("VACUUM")
        print("✓ VACUUM complete")
    
    def analyze_database(self):
        """Analyze database for query optimization"""
        print("Running ANALYZE...")
        self.conn.execute("ANALYZE")
        print("✓ ANALYZE complete")
    
    def reindex_database(self):
        """Rebuild all indices"""
        print("Running REINDEX...")
        self.conn.execute("REINDEX")
        print("✓ REINDEX complete")
    
    def integrity_check(self) -> bool:
        """Run SQLite integrity check"""
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()[0]
        
        if result == "ok":
            print("✓ Integrity check: PASSED")
            return True
        else:
            print(f"✗ Integrity check: FAILED - {result}")
            return False
    
    def export_to_json(self, output_file: str, limit: int = None):
        """Export database statistics to JSON"""
        stats = self.get_statistics()
        
        # Add metadata
        stats['exported_at'] = datetime.now().isoformat()
        stats['database_path'] = self.db_path
        
        with open(output_file, 'w') as f:
            json.dump(stats, f, indent=2)
        
        print(f"✓ Statistics exported to: {output_file}")
    
    def list_recent_ingestions(self, limit: int = 10):
        """List recent file ingestions"""
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT file_path, ingestion_date, row_count, status
            FROM ingestion_metadata
            ORDER BY ingestion_date DESC
            LIMIT ?
        """, (limit,))
        
        print(f"\nRecent {limit} Ingestions:")
        print("-" * 80)
        
        for row in cursor.fetchall():
            print(f"File: {row[0]}")
            print(f"  Date: {row[1]}")
            print(f"  Rows: {row[2]:,}")
            print(f"  Status: {row[3]}")
            print()
    
    def get_date_range_data(self, start_date: str, end_date: str, symbol: str = None):
        """Get data for a specific date range"""
        cursor = self.conn.cursor()
        
        if symbol:
            cursor.execute("""
                SELECT * FROM cleaned_csvs
                WHERE Date >= ? AND Date <= ? AND Symbol = ?
                ORDER BY Date, Symbol
            """, (start_date, end_date, symbol))
        else:
            cursor.execute("""
                SELECT * FROM cleaned_csvs
                WHERE Date >= ? AND Date <= ?
                ORDER BY Date, Symbol
                LIMIT 1000
            """, (start_date, end_date))
        
        rows = cursor.fetchall()
        
        print(f"\nData from {start_date} to {end_date}")
        if symbol:
            print(f"Symbol: {symbol}")
        print(f"Records found: {len(rows)}")
        
        if rows:
            print("\nSample (first 5 records):")
            print("-" * 80)
            for row in rows[:5]:
                print(dict(row))


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Database utilities for NSE Bhavcopy database',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Get database statistics
  python db_utils.py --db bhavcopy_data.db --stats
  
  # Check for duplicates
  python db_utils.py --db bhavcopy_data.db --check-duplicates
  
  # Run integrity check
  python db_utils.py --db bhavcopy_data.db --integrity-check
  
  # Optimize database
  python db_utils.py --db bhavcopy_data.db --optimize
  
  # Export statistics to JSON
  python db_utils.py --db bhavcopy_data.db --export stats.json
  
  # List recent ingestions
  python db_utils.py --db bhavcopy_data.db --recent-ingestions
        """
    )
    
    parser.add_argument('--db', required=True, help='Path to SQLite database')
    
    parser.add_argument('--stats', action='store_true', 
                       help='Display database statistics')
    
    parser.add_argument('--check-duplicates', action='store_true',
                       help='Check for duplicate records')
    
    parser.add_argument('--check-nulls', action='store_true',
                       help='Check for NULL values')
    
    parser.add_argument('--integrity-check', action='store_true',
                       help='Run SQLite integrity check')
    
    parser.add_argument('--optimize', action='store_true',
                       help='Optimize database (VACUUM + ANALYZE + REINDEX)')
    
    parser.add_argument('--export', metavar='FILE',
                       help='Export statistics to JSON file')
    
    parser.add_argument('--recent-ingestions', action='store_true',
                       help='List recent file ingestions')
    
    parser.add_argument('--trading-dates', metavar='YEAR', type=int,
                       help='List trading dates for a year')
    
    parser.add_argument('--symbols', action='store_true',
                       help='List all unique symbols')
    
    args = parser.parse_args()
    
    # Create utils instance
    utils = DatabaseUtils(args.db)
    utils.connect()
    
    try:
        # Execute requested operations
        if args.stats:
            utils.print_statistics()
        
        if args.check_duplicates:
            print("\nChecking for duplicates...")
            duplicates = utils.check_duplicates()
            
            if duplicates:
                print(f"✗ Found {len(duplicates)} duplicate records:")
                for dup in duplicates[:10]:
                    print(f"  {dup}")
                if len(duplicates) > 10:
                    print(f"  ... and {len(duplicates) - 10} more")
            else:
                print("✓ No duplicates found")
        
        if args.check_nulls:
            print("\nChecking for NULL values...")
            null_counts = utils.check_null_values()
            
            has_nulls = any(count > 0 for count in null_counts.values())
            
            if has_nulls:
                print("NULL values found:")
                for col, count in null_counts.items():
                    if count > 0:
                        print(f"  {col}: {count:,}")
            else:
                print("✓ No NULL values in critical columns")
        
        if args.integrity_check:
            print("\nRunning integrity check...")
            utils.integrity_check()
        
        if args.optimize:
            print("\nOptimizing database...")
            utils.vacuum_database()
            utils.analyze_database()
            utils.reindex_database()
            print("✓ Database optimized")
        
        if args.export:
            utils.export_to_json(args.export)
        
        if args.recent_ingestions:
            utils.list_recent_ingestions()
        
        if args.trading_dates:
            dates = utils.get_trading_dates(args.trading_dates)
            print(f"\nTrading dates for {args.trading_dates}: {len(dates)}")
            for date in dates[:20]:
                print(f"  {date}")
            if len(dates) > 20:
                print(f"  ... and {len(dates) - 20} more")
        
        if args.symbols:
            symbols = utils.get_symbols()
            print(f"\nUnique symbols: {len(symbols)}")
            for symbol in symbols[:50]:
                print(f"  {symbol}")
            if len(symbols) > 50:
                print(f"  ... and {len(symbols) - 50} more")
        
        # If no arguments provided, show stats by default
        if not any([args.stats, args.check_duplicates, args.check_nulls,
                   args.integrity_check, args.optimize, args.export,
                   args.recent_ingestions, args.trading_dates, args.symbols]):
            print("No operation specified. Showing statistics...")
            utils.print_statistics()
    
    finally:
        utils.disconnect()


if __name__ == '__main__':
    main()
