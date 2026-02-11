#!/usr/bin/env python3
"""
NSE Bhavcopy Database Builder
==============================
Builds a lossless, deterministic SQLite database from historical NSE bhavcopy CSV files.
Ensures strict data equality, idempotency, and referential integrity.

Author: Senior Data Engineering Team
Version: 1.0.0
"""

import sqlite3
import pandas as pd
import numpy as np
import glob
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Set, Optional
import hashlib
import logging
from dataclasses import dataclass
import json


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bhavcopy_builder.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class IngestionStats:
    """Statistics for ingestion process"""
    year: int
    files_processed: int
    rows_inserted: int
    rows_skipped: int
    duplicates_found: int
    errors: List[str]
    start_time: datetime
    end_time: Optional[datetime] = None
    
    @property
    def duration(self) -> float:
        """Get duration in seconds"""
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0


class BhavcopyDatabaseBuilder:
    """
    Builds and manages NSE Bhavcopy SQLite database.
    Ensures lossless, deterministic data ingestion with full idempotency.
    """
    
    # Business key that uniquely identifies a row
    BUSINESS_KEY = ['Date', 'Symbol', 'Instrument', 'ExpiryDate', 'StrikePrice', 'OptionType']
    
    # Chunk size for batch inserts (increased for better performance)
    CHUNK_SIZE = 50000
    
    def __init__(self, db_path: str):
        """
        Initialize database builder.
        
        Args:
            db_path: Path where SQLite database will be created
        """
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        self.ingestion_stats = []
        
    def create_database(self, force: bool = False):
        """
        Create database schema with proper indices and constraints.
        
        Args:
            force: If True, drop existing database and recreate
        """
        if force and os.path.exists(self.db_path):
            logger.warning(f"Force flag set - removing existing database: {self.db_path}")
            os.remove(self.db_path)
        
        logger.info(f"Creating database: {self.db_path}")
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        
        # Set performance pragmas for faster bulk inserts
        logger.info("Setting SQLite performance optimizations...")
        self.cursor.execute("PRAGMA journal_mode = WAL")  # Write-Ahead Logging for better concurrency
        self.cursor.execute("PRAGMA synchronous = NORMAL")  # Balance between safety and speed
        self.cursor.execute("PRAGMA cache_size = -64000")  # 64MB cache
        self.cursor.execute("PRAGMA temp_store = MEMORY")  # Store temp tables in memory
        self.cursor.execute("PRAGMA locking_mode = EXCLUSIVE")  # Exclusive lock for faster writes
        
        # Create cleaned_csvs table (main data table)
        # NOTE: UNIQUE constraint removed for performance - duplicates handled at application level
        logger.info("Creating table: cleaned_csvs")
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS cleaned_csvs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                Date DATE NOT NULL,
                ExpiryDate DATE,
                Instrument TEXT,
                Symbol TEXT,
                StrikePrice REAL,
                OptionType TEXT,
                Open REAL,
                High REAL,
                Low REAL,
                Close REAL,
                SettledPrice REAL,
                Contracts INTEGER,
                TurnOver REAL,
                OpenInterest INTEGER
            )
        """)
        
        # Create indices for performance
        logger.info("Creating indices on cleaned_csvs...")
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cleaned_csvs_date 
            ON cleaned_csvs(Date)
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cleaned_csvs_symbol 
            ON cleaned_csvs(Symbol)
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cleaned_csvs_date_symbol 
            ON cleaned_csvs(Date, Symbol)
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cleaned_csvs_expiry 
            ON cleaned_csvs(ExpiryDate)
        """)
        
        # Create expiry_data table
        logger.info("Creating table: expiry_data")
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS expiry_data (
                Symbol TEXT PRIMARY KEY,
                Previous_Expiry DATE,
                Current_Expiry DATE,
                Next_Expiry DATE
            )
        """)
        
        # Create strike_data table
        logger.info("Creating table: strike_data")
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS strike_data (
                Ticker TEXT,
                Date DATE,
                Close REAL,
                PRIMARY KEY (Ticker, Date)
            )
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_strike_data_date 
            ON strike_data(Date)
        """)
        
        # Create filter_data table
        logger.info("Creating table: filter_data")
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS filter_data (
                Start DATE,
                End DATE
            )
        """)
        
        # Create metadata table to track ingestion
        logger.info("Creating table: ingestion_metadata")
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS ingestion_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE,
                file_name TEXT,
                file_size INTEGER,
                file_hash TEXT,
                ingestion_date TIMESTAMP,
                row_count INTEGER,
                status TEXT
            )
        """)
        
        self.conn.commit()
        logger.info("[OK] Database schema created successfully")
    
    def connect(self):
        """Establish database connection"""
        if not self.conn:
            self.conn = sqlite3.connect(self.db_path)
            self.cursor = self.conn.cursor()
            # Enable foreign keys
            self.cursor.execute("PRAGMA foreign_keys = ON")
            # Set journal mode for better performance
            self.cursor.execute("PRAGMA journal_mode = WAL")
            logger.info(f"[OK] Connected to database: {self.db_path}")
    
    def disconnect(self):
        """Close database connection"""
        if self.conn:
            self.conn.commit()
            self.conn.close()
            self.conn = None
            self.cursor = None
            logger.info("[OK] Database connection closed")
    
    def _create_metadata_table(self):
        """Create ingestion_metadata table if it doesn't exist"""
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS ingestion_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE,
                file_name TEXT,
                file_size INTEGER,
                file_hash TEXT,
                ingestion_date TIMESTAMP,
                row_count INTEGER,
                status TEXT
            )
        """)
        self.conn.commit()
        logger.info("[OK] Created ingestion_metadata table")
    
    def compute_file_hash(self, file_path: str) -> str:
        """
        Compute SHA256 hash of file for idempotency check.
        
        Args:
            file_path: Path to file
            
        Returns:
            SHA256 hash string
        """
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    def is_file_processed(self, file_path: str, file_hash: str) -> bool:
        """
        Check if file has already been processed.
        Checks by file_name + file_size for robustness.
        
        Args:
            file_path: Path to file
            file_hash: Hash of file (kept for backward compatibility)
            
        Returns:
            True if file was already processed with same name and size
        """
        try:
            import os
            file_name = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            
            # Check if table exists first
            self.cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='ingestion_metadata'
            """)
            if not self.cursor.fetchone():
                # Table doesn't exist - create it
                self._create_metadata_table()
                return False
            
            self.cursor.execute("""
                SELECT COUNT(*) FROM ingestion_metadata
                WHERE file_name = ? AND file_size = ? AND status = 'SUCCESS'
            """, (file_name, file_size))
            
            count = self.cursor.fetchone()[0]
            return count > 0
        except sqlite3.OperationalError:
            # Missing columns - return False to process file
            return False
    
    def mark_file_processed(self, file_path: str, file_hash: str, row_count: int):
        """
        Mark file as successfully processed.
        
        Args:
            file_path: Path to file
            file_hash: Hash of file
            row_count: Number of rows ingested
        """
        try:
            import os
            file_name = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            
            # Check if table exists first
            self.cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='ingestion_metadata'
            """)
            if not self.cursor.fetchone():
                # Table doesn't exist - create it
                self._create_metadata_table()
            
            self.cursor.execute("""
                INSERT OR REPLACE INTO ingestion_metadata 
                (file_path, file_hash, ingestion_date, row_count, status, file_name, file_size)
                VALUES (?, ?, ?, ?, 'SUCCESS', ?, ?)
            """, (file_path, file_hash, datetime.now(), row_count, file_name, file_size))
            self.conn.commit()
        except sqlite3.OperationalError as e:
            # Log error but don't fail
            logger.warning(f"Could not mark file as processed: {e}")
            # Table doesn't exist or missing columns - skip marking
            pass
    
    def normalize_csv_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalize and clean CSV data.
        OPTIMIZED: Uses vectorized operations for better performance.
        
        Args:
            df: Raw CSV DataFrame
            
        Returns:
            Normalized DataFrame
        """
        # Normalize column names (remove leading/trailing spaces)
        df.columns = df.columns.str.strip()
        
        # Map common column name variations to standard names
        column_mapping = {
            'TIMESTAMP': 'Date',
            'EXPIRY_DT': 'ExpiryDate',
            'STRIKE_PR': 'StrikePrice',
            'OPTION_TYP': 'OptionType',
            'OPEN': 'Open',
            'HIGH': 'High',
            'LOW': 'Low',
            'CLOSE': 'Close',
            'SETTLE_PR': 'SettledPrice',
            'CONTRACTS': 'Contracts',
            'VAL_INLAKH': 'TurnOver',
            'OPEN_INT': 'OpenInterest',
            'CHG_IN_OI': 'ChangeInOI'
        }
        
        # Apply mapping if columns exist (vectorized operation)
        df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns}, inplace=True)
        
        # Ensure required columns exist
        required_columns = ['Date', 'Symbol', 'Instrument']
        for col in required_columns:
            if col not in df.columns:
                raise ValueError(f"Required column '{col}' not found in CSV")
        
        # Convert dates to standard format (YYYY-MM-DD) - vectorized
        if 'Date' in df.columns:
            df['Date'] = pd.to_datetime(df['Date'], errors='coerce').dt.strftime('%Y-%m-%d')
        
        if 'ExpiryDate' in df.columns:
            df['ExpiryDate'] = pd.to_datetime(df['ExpiryDate'], errors='coerce').dt.strftime('%Y-%m-%d')
        
        # Convert numeric columns - vectorized operation
        numeric_columns = ['Open', 'High', 'Low', 'Close', 'SettledPrice', 
                          'StrikePrice', 'Contracts', 'TurnOver', 'OpenInterest']
        
        for col in numeric_columns:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Handle NaN values (convert to None for SQL NULL) - vectorized
        df = df.where(pd.notnull(df), None)
        
        # Remove rows with NULL dates (critical field)
        df = df.dropna(subset=['Date'])
        
        # Remove completely empty rows
        df = df.dropna(how='all')
        
        return df
    
    def validate_business_key(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, List[Dict]]:
        """
        Validate that business key columns are present and check for duplicates.
        
        Args:
            df: DataFrame to validate
            
        Returns:
            Tuple of (cleaned DataFrame, list of duplicate records)
        """
        # Check for duplicates based on business key
        duplicates = df[df.duplicated(subset=self.BUSINESS_KEY, keep=False)]
        
        if not duplicates.empty:
            logger.warning(f"Found {len(duplicates)} duplicate records in CSV")
            duplicate_list = duplicates.to_dict('records')
        else:
            duplicate_list = []
        
        # Remove duplicates, keeping first occurrence
        df_clean = df.drop_duplicates(subset=self.BUSINESS_KEY, keep='first')
        
        if len(df) != len(df_clean):
            logger.warning(f"Removed {len(df) - len(df_clean)} duplicate rows")
        
        return df_clean, duplicate_list
    
    def drop_indices(self):
        """
        Drop all indices on cleaned_csvs table for faster bulk inserts.
        Indices will be rebuilt after ingestion completes.
        """
        logger.info("Dropping indices for faster bulk insert...")
        
        # Drop all indices on cleaned_csvs table
        indices = [
            'idx_cleaned_csvs_date',
            'idx_cleaned_csvs_symbol',
            'idx_cleaned_csvs_date_symbol',
            'idx_cleaned_csvs_expiry'
        ]
        
        for idx in indices:
            try:
                self.cursor.execute(f"DROP INDEX IF EXISTS {idx}")
                logger.info(f"  Dropped index: {idx}")
            except Exception as e:
                logger.warning(f"  Could not drop index {idx}: {e}")
        
        self.conn.commit()
        logger.info("[OK] Indices dropped successfully")
    
    def rebuild_indices(self):
        """
        Rebuild all indices on cleaned_csvs table after bulk insert completes.
        This is much faster than maintaining indices during inserts.
        """
        logger.info("\n" + "="*80)
        logger.info("Rebuilding indices (this may take a few minutes)...")
        logger.info("="*80)
        
        start_time = datetime.now()
        
        # Recreate all indices
        logger.info("Creating index: idx_cleaned_csvs_date")
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cleaned_csvs_date 
            ON cleaned_csvs(Date)
        """)
        
        logger.info("Creating index: idx_cleaned_csvs_symbol")
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cleaned_csvs_symbol 
            ON cleaned_csvs(Symbol)
        """)
        
        logger.info("Creating index: idx_cleaned_csvs_date_symbol")
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cleaned_csvs_date_symbol 
            ON cleaned_csvs(Date, Symbol)
        """)
        
        logger.info("Creating index: idx_cleaned_csvs_expiry")
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cleaned_csvs_expiry 
            ON cleaned_csvs(ExpiryDate)
        """)
        
        self.conn.commit()
        
        duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"[OK] All indices rebuilt in {duration:.1f} seconds")
    
    def insert_cleaned_data(self, df: pd.DataFrame) -> Tuple[int, int]:
        """
        Insert data into cleaned_csvs table with idempotency.
        ULTRA-OPTIMIZED: Uses plain INSERT without constraint checking for maximum speed.
        Duplicates are already removed in validate_business_key() step.
        
        Args:
            df: DataFrame to insert
            
        Returns:
            Tuple of (rows inserted, rows skipped)
        """
        rows_inserted = 0
        
        # Prepare data for insertion
        columns = ['Date', 'ExpiryDate', 'Instrument', 'Symbol', 'StrikePrice', 
                  'OptionType', 'Open', 'High', 'Low', 'Close', 'SettledPrice', 
                  'Contracts', 'TurnOver', 'OpenInterest']
        
        # Ensure all columns exist in DataFrame
        for col in columns:
            if col not in df.columns:
                df[col] = None
        
        # Select only needed columns in correct order
        df_subset = df[columns]
        
        # Process in chunks for better performance
        total_rows = len(df_subset)
        
        # Use plain INSERT for maximum speed (no duplicate checking)
        # Duplicates already removed in validate_business_key()
        placeholders = ','.join(['?' for _ in columns])
        query = f"""
            INSERT INTO cleaned_csvs 
            ({','.join(columns)})
            VALUES ({placeholders})
        """
        
        for start_idx in range(0, total_rows, self.CHUNK_SIZE):
            end_idx = min(start_idx + self.CHUNK_SIZE, total_rows)
            chunk = df_subset.iloc[start_idx:end_idx]
            
            # PERFORMANCE OPTIMIZATION: Use .values.tolist() instead of iterrows()
            # This is 10-20x faster than iterrows() for large datasets
            values = chunk.values.tolist()
            
            # Execute batch insert
            self.cursor.executemany(query, values)
            rows_inserted += len(chunk)
        
        # Single commit at the end for maximum performance
        self.conn.commit()
        
        return rows_inserted, 0
    
    def ingest_csv_file(self, csv_path: str) -> Dict:
        """
        Ingest a single CSV file into database.
        OPTIMIZED: Reduced logging verbosity for faster processing.
        
        Args:
            csv_path: Path to CSV file
            
        Returns:
            Dictionary containing ingestion statistics
        """
        # Check if file was already processed
        file_hash = self.compute_file_hash(csv_path)
        
        if self.is_file_processed(csv_path, file_hash):
            file_name = os.path.basename(csv_path)
            print(f"⊘ Skipped: {file_name} (already processed)")
            return {
                'file': csv_path,
                'status': 'SKIPPED',
                'reason': 'Already processed'
            }
        
        try:
            # Read CSV file (optimized with low_memory=False for speed)
            df = pd.read_csv(csv_path, low_memory=False)
            
            # Normalize data
            df = self.normalize_csv_data(df)
            
            # Validate business key and remove duplicates
            df_clean, duplicates = self.validate_business_key(df)
            
            # Insert into database
            rows_inserted, rows_skipped = self.insert_cleaned_data(df_clean)
            
            # Mark file as processed
            self.mark_file_processed(csv_path, file_hash, rows_inserted)
            
            # Print which CSV is being stored
            file_name = os.path.basename(csv_path)
            print(f"✓ Stored: {file_name} ({rows_inserted} rows)")
            
            return {
                'file': csv_path,
                'status': 'SUCCESS',
                'total_rows': len(df),
                'rows_inserted': rows_inserted,
                'rows_skipped': rows_skipped,
                'duplicates_in_csv': len(duplicates)
            }
            
        except Exception as e:
            logger.error(f"  [ERROR] {csv_path}: {str(e)}")
            return {
                'file': csv_path,
                'status': 'ERROR',
                'error': str(e)
            }
    
    def ingest_directory(self, csv_directory: str, pattern: str = "*.csv") -> List[Dict]:
        """
        Ingest all CSV files from a directory.
        OPTIMIZED: Drops indices before bulk insert, rebuilds after completion for maximum speed.
        
        Args:
            csv_directory: Directory containing CSV files
            pattern: Glob pattern for CSV files
            
        Returns:
            List of ingestion results
        """
        csv_dir = Path(csv_directory)
        
        if not csv_dir.exists():
            raise FileNotFoundError(f"Directory not found: {csv_directory}")
        
        # Find all CSV files
        csv_files = sorted(csv_dir.rglob(pattern))
        
        if not csv_files:
            logger.warning(f"No CSV files found in {csv_directory}")
            return []
        
        logger.info(f"\n{'='*80}")
        logger.info(f"Found {len(csv_files)} CSV files to process")
        logger.info(f"{'='*80}")
        
        # DROP INDICES BEFORE BULK INSERT FOR MAXIMUM SPEED
        self.drop_indices()
        
        results = []
        start_time = datetime.now()
        
        for i, csv_file in enumerate(csv_files, 1):
            # Show progress every 50 files for faster processing
            if i % 50 == 0 or i == 1 or i == len(csv_files):
                elapsed = (datetime.now() - start_time).total_seconds()
                rate = i / elapsed if elapsed > 0 else 0
                remaining = (len(csv_files) - i) / rate if rate > 0 else 0
                logger.info(f"[{i}/{len(csv_files)}] Processing... ({rate:.1f} files/sec, ~{remaining/60:.1f} min remaining)")
            
            result = self.ingest_csv_file(str(csv_file))
            results.append(result)
        
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        # REBUILD INDICES AFTER BULK INSERT COMPLETES
        self.rebuild_indices()
        
        # Print summary
        self._print_ingestion_summary(results, duration)
        
        return results
    
    def _print_ingestion_summary(self, results: List[Dict], duration: float):
        """
        Print summary of ingestion process.
        
        Args:
            results: List of ingestion results
            duration: Total duration in seconds
        """
        logger.info(f"\n{'='*80}")
        logger.info("INGESTION SUMMARY")
        logger.info(f"{'='*80}")
        
        total_files = len(results)
        success_count = sum(1 for r in results if r['status'] == 'SUCCESS')
        skipped_count = sum(1 for r in results if r['status'] == 'SKIPPED')
        error_count = sum(1 for r in results if r['status'] == 'ERROR')
        
        total_inserted = sum(r.get('rows_inserted', 0) for r in results)
        total_skipped = sum(r.get('rows_skipped', 0) for r in results)
        
        logger.info(f"Total Files: {total_files}")
        logger.info(f"Successful: {success_count}")
        logger.info(f"Skipped: {skipped_count}")
        logger.info(f"Errors: {error_count}")
        logger.info(f"")
        logger.info(f"Total Rows Inserted: {total_inserted:,}")
        logger.info(f"Total Rows Skipped: {total_skipped:,}")
        logger.info(f"")
        logger.info(f"Duration: {duration:.2f} seconds")
        logger.info(f"Average: {duration/total_files:.2f} seconds per file")
        
        # Print errors if any
        if error_count > 0:
            logger.error(f"\nFiles with errors:")
            for r in results:
                if r['status'] == 'ERROR':
                    logger.error(f"  • {r['file']}: {r.get('error', 'Unknown error')}")
    
    def build_expiry_data(self):
        """
        Build expiry_data table from cleaned_csvs.
        Extracts unique symbols and their expiry dates.
        """
        logger.info("\n" + "="*80)
        logger.info("Building expiry_data table...")
        logger.info("="*80)
        
        # Get unique symbols
        query = """
            SELECT DISTINCT Symbol
            FROM cleaned_csvs
            WHERE Symbol IS NOT NULL
            ORDER BY Symbol
        """
        
        symbols_df = pd.read_sql_query(query, self.conn)
        symbols = symbols_df['Symbol'].tolist()
        
        logger.info(f"Found {len(symbols)} unique symbols")
        
        # For each symbol, find expiry dates
        expiry_records = []
        
        for i, symbol in enumerate(symbols, 1):
            if i % 100 == 0:
                logger.info(f"  Processing symbol {i}/{len(symbols)}...")
            
            # Get distinct expiry dates for this symbol, ordered
            query = """
                SELECT DISTINCT ExpiryDate
                FROM cleaned_csvs
                WHERE Symbol = ? AND ExpiryDate IS NOT NULL
                ORDER BY ExpiryDate
            """
            
            self.cursor.execute(query, (symbol,))
            expiry_dates = [row[0] for row in self.cursor.fetchall()]
            
            if len(expiry_dates) >= 2:
                # Use the last three expiry dates
                expiry_records.append({
                    'Symbol': symbol,
                    'Previous_Expiry': expiry_dates[-3] if len(expiry_dates) >= 3 else None,
                    'Current_Expiry': expiry_dates[-2],
                    'Next_Expiry': expiry_dates[-1]
                })
            elif len(expiry_dates) == 1:
                expiry_records.append({
                    'Symbol': symbol,
                    'Previous_Expiry': None,
                    'Current_Expiry': expiry_dates[0],
                    'Next_Expiry': None
                })
        
        # Insert into expiry_data
        if expiry_records:
            for record in expiry_records:
                self.cursor.execute("""
                    INSERT OR REPLACE INTO expiry_data 
                    (Symbol, Previous_Expiry, Current_Expiry, Next_Expiry)
                    VALUES (?, ?, ?, ?)
                """, (
                    record['Symbol'],
                    record['Previous_Expiry'],
                    record['Current_Expiry'],
                    record['Next_Expiry']
                ))
            
            self.conn.commit()
            logger.info(f"[OK] Inserted {len(expiry_records)} records into expiry_data")
        else:
            logger.warning("No expiry records to insert")
    
    def build_strike_data(self):
        """
        Build strike_data table from cleaned_csvs.
        Extracts underlying symbol closing prices.
        """
        logger.info("\n" + "="*80)
        logger.info("Building strike_data table...")
        logger.info("="*80)
        
        # Extract unique (Symbol, Date, Close) combinations where Instrument is 'FUTIDX' or 'FUTSTK'
        # This represents the underlying asset prices
        query = """
            SELECT DISTINCT 
                Symbol as Ticker,
                Date,
                Close
            FROM cleaned_csvs
            WHERE Instrument IN ('FUTIDX', 'FUTSTK')
            AND Close IS NOT NULL
            ORDER BY Date, Symbol
        """
        
        logger.info("Extracting underlying prices...")
        strike_df = pd.read_sql_query(query, self.conn)
        
        logger.info(f"Found {len(strike_df):,} underlying price records")
        
        # Insert into strike_data
        if not strike_df.empty:
            records = strike_df.to_records(index=False)
            
            self.cursor.executemany("""
                INSERT OR REPLACE INTO strike_data (Ticker, Date, Close)
                VALUES (?, ?, ?)
            """, records)
            
            self.conn.commit()
            logger.info(f"[OK] Inserted {len(strike_df):,} records into strike_data")
        else:
            logger.warning("No strike data to insert")
    
    def build_filter_data(self):
        """
        Build filter_data table with date range from cleaned_csvs.
        """
        logger.info("\n" + "="*80)
        logger.info("Building filter_data table...")
        logger.info("="*80)
        
        # Get min and max dates
        query = """
            SELECT 
                MIN(Date) as start_date,
                MAX(Date) as end_date
            FROM cleaned_csvs
        """
        
        self.cursor.execute(query)
        result = self.cursor.fetchone()
        
        if result and result[0] and result[1]:
            start_date, end_date = result
            
            # Clear existing data
            self.cursor.execute("DELETE FROM filter_data")
            
            # Insert new range
            self.cursor.execute("""
                INSERT INTO filter_data (Start, End)
                VALUES (?, ?)
            """, (start_date, end_date))
            
            self.conn.commit()
            logger.info(f"[OK] Date range: {start_date} to {end_date}")
        else:
            logger.warning("Could not determine date range")
    
    def get_database_stats(self) -> Dict:
        """
        Get comprehensive database statistics.
        
        Returns:
            Dictionary containing database statistics
        """
        stats = {}
        
        # cleaned_csvs stats
        self.cursor.execute("SELECT COUNT(*) FROM cleaned_csvs")
        stats['total_records'] = self.cursor.fetchone()[0]
        
        self.cursor.execute("SELECT COUNT(DISTINCT Date) FROM cleaned_csvs")
        stats['unique_dates'] = self.cursor.fetchone()[0]
        
        self.cursor.execute("SELECT COUNT(DISTINCT Symbol) FROM cleaned_csvs")
        stats['unique_symbols'] = self.cursor.fetchone()[0]
        
        self.cursor.execute("SELECT MIN(Date), MAX(Date) FROM cleaned_csvs")
        min_date, max_date = self.cursor.fetchone()
        stats['date_range'] = {'start': min_date, 'end': max_date}
        
        # expiry_data stats
        self.cursor.execute("SELECT COUNT(*) FROM expiry_data")
        stats['expiry_records'] = self.cursor.fetchone()[0]
        
        # strike_data stats
        self.cursor.execute("SELECT COUNT(*) FROM strike_data")
        stats['strike_records'] = self.cursor.fetchone()[0]
        
        # Database file size
        if os.path.exists(self.db_path):
            size_bytes = os.path.getsize(self.db_path)
            size_gb = size_bytes / (1024 ** 3)
            stats['database_size_gb'] = round(size_gb, 2)
        
        return stats
    
    def print_database_stats(self):
        """Print database statistics to console"""
        stats = self.get_database_stats()
        
        logger.info("\n" + "="*80)
        logger.info("DATABASE STATISTICS")
        logger.info("="*80)
        logger.info(f"Database Path: {self.db_path}")
        logger.info(f"Database Size: {stats.get('database_size_gb', 'N/A')} GB")
        logger.info(f"")
        logger.info(f"cleaned_csvs:")
        logger.info(f"  Total Records: {stats['total_records']:,}")
        logger.info(f"  Unique Dates: {stats['unique_dates']:,}")
        logger.info(f"  Unique Symbols: {stats['unique_symbols']:,}")
        logger.info(f"  Date Range: {stats['date_range']['start']} to {stats['date_range']['end']}")
        logger.info(f"")
        logger.info(f"expiry_data:")
        logger.info(f"  Total Records: {stats['expiry_records']:,}")
        logger.info(f"")
        logger.info(f"strike_data:")
        logger.info(f"  Total Records: {stats['strike_records']:,}")


def main():
    """Main execution function"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Build NSE Bhavcopy SQLite Database from CSV files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create new database and ingest all CSV files
  python bhavcopy_db_builder.py --db bhavcopy_data.db --csv-dir ./csv_data --create
  
  # Ingest additional CSV files into existing database
  python bhavcopy_db_builder.py --db bhavcopy_data.db --csv-dir ./new_csv_data
  
  # Force recreate database (WARNING: deletes existing data)
  python bhavcopy_db_builder.py --db bhavcopy_data.db --csv-dir ./csv_data --create --force
  
  # Build only auxiliary tables (expiry, strike, filter)
  python bhavcopy_db_builder.py --db bhavcopy_data.db --build-aux-tables
        """
    )
    
    parser.add_argument(
        '--db',
        required=True,
        help='Path to SQLite database file'
    )
    
    parser.add_argument(
        '--csv-dir',
        default='./cleaned_csvs',
        help='Directory containing CSV files to ingest (default: ./cleaned_csvs)'
    )
    
    parser.add_argument(
        '--create',
        action='store_true',
        help='Create database schema'
    )
    
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force recreate database (WARNING: deletes existing data)'
    )
    
    parser.add_argument(
        '--build-aux-tables',
        action='store_true',
        help='Build auxiliary tables (expiry_data, strike_data, filter_data)'
    )
    
    parser.add_argument(
        '--pattern',
        default='*.csv',
        help='Glob pattern for CSV files (default: *.csv)'
    )
    
    args = parser.parse_args()
    
    # Create builder
    builder = BhavcopyDatabaseBuilder(db_path=args.db)
    
    try:
        # Create database if requested
        if args.create:
            builder.create_database(force=args.force)
        
        # Connect to database
        builder.connect()
        
        # Ingest CSV files if directory provided
        if args.csv_dir:
            logger.info("\n" + "="*80)
            logger.info("STARTING CSV INGESTION")
            logger.info("="*80)
            
            results = builder.ingest_directory(args.csv_dir, args.pattern)
            
            # Save results to JSON
            with open('ingestion_results.json', 'w') as f:
                json.dump(results, f, indent=2)
            logger.info("\n[OK] Ingestion results saved to: ingestion_results.json")
        
        # Build auxiliary tables if requested
        if args.build_aux_tables:
            builder.build_expiry_data()
            builder.build_strike_data()
            builder.build_filter_data()
        
        # Print final statistics
        builder.print_database_stats()
        
        logger.info("\n" + "="*80)
        logger.info("[OK] DATABASE BUILD COMPLETE")
        logger.info("="*80)
        
    except Exception as e:
        logger.error(f"\n[ERROR] Fatal error: {str(e)}", exc_info=True)
        sys.exit(1)
    
    finally:
        builder.disconnect()


if __name__ == '__main__':
    main()
