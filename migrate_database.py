#!/usr/bin/env python3
"""
Quick migration script to move data from old database (with UNIQUE constraint)
to new database (without UNIQUE constraint) for maximum performance.
"""

import sqlite3
import os
from datetime import datetime

def migrate_database():
    """Migrate data from old to new database"""
    
    old_db = "bhavcopy_data.db"
    new_db = "bhavcopy_data_new.db"
    
    if not os.path.exists(old_db):
        print(f"ERROR: {old_db} not found!")
        return
    
    print("="*80)
    print("DATABASE MIGRATION - Removing UNIQUE Constraint")
    print("="*80)
    
    # Connect to old database
    print(f"\n1. Reading data from {old_db}...")
    old_conn = sqlite3.connect(old_db)
    old_cursor = old_conn.cursor()
    
    # Get row count
    old_cursor.execute("SELECT COUNT(*) FROM cleaned_csvs")
    total_rows = old_cursor.fetchone()[0]
    print(f"   Found {total_rows:,} rows to migrate")
    
    # Get all data
    print("   Extracting data...")
    old_cursor.execute("""
        SELECT Date, ExpiryDate, Instrument, Symbol, StrikePrice, 
               OptionType, Open, High, Low, Close, SettledPrice, 
               Contracts, TurnOver, OpenInterest
        FROM cleaned_csvs
    """)
    all_data = old_cursor.fetchall()
    print(f"   [OK] Extracted {len(all_data):,} rows")
    
    # Get metadata
    print("   Extracting ingestion metadata...")
    try:
        old_cursor.execute("SELECT file_path, file_name, file_size, file_hash, ingestion_date, row_count, status FROM ingestion_metadata")
        metadata = old_cursor.fetchall()
    except sqlite3.OperationalError:
        # Old schema doesn't have file_name and file_size columns
        print("   [INFO] Old schema detected - extracting without file_name/file_size...")
        old_cursor.execute("SELECT file_path, file_hash, ingestion_date, row_count, status FROM ingestion_metadata")
        old_metadata = old_cursor.fetchall()
        # Add None for file_name and file_size
        metadata = [(path, None, None, hash, date, count, status) for path, hash, date, count, status in old_metadata]
    print(f"   [OK] Extracted {len(metadata)} metadata records")
    
    old_conn.close()
    
    # Create new database
    print(f"\n2. Creating new database {new_db}...")
    new_conn = sqlite3.connect(new_db)
    new_cursor = new_conn.cursor()
    
    # Set performance pragmas
    new_cursor.execute("PRAGMA journal_mode = WAL")
    new_cursor.execute("PRAGMA synchronous = NORMAL")
    new_cursor.execute("PRAGMA cache_size = -64000")
    new_cursor.execute("PRAGMA temp_store = MEMORY")
    new_cursor.execute("PRAGMA locking_mode = EXCLUSIVE")
    
    # Create table WITHOUT UNIQUE constraint
    print("   Creating cleaned_csvs table (NO UNIQUE constraint)...")
    new_cursor.execute("""
        CREATE TABLE cleaned_csvs (
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
    
    # Create metadata table
    print("   Creating ingestion_metadata table...")
    new_cursor.execute("""
        CREATE TABLE ingestion_metadata (
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
    
    # Create other tables
    new_cursor.execute("""
        CREATE TABLE IF NOT EXISTS expiry_data (
            Symbol TEXT PRIMARY KEY,
            Previous_Expiry DATE,
            Current_Expiry DATE,
            Next_Expiry DATE
        )
    """)
    
    new_cursor.execute("""
        CREATE TABLE IF NOT EXISTS strike_data (
            Ticker TEXT,
            Date DATE,
            Close REAL,
            PRIMARY KEY (Ticker, Date)
        )
    """)
    
    new_cursor.execute("""
        CREATE TABLE IF NOT EXISTS filter_data (
            Start DATE,
            End DATE
        )
    """)
    
    # Insert data in chunks
    print(f"\n3. Inserting {len(all_data):,} rows into new database...")
    chunk_size = 50000
    start_time = datetime.now()
    
    for i in range(0, len(all_data), chunk_size):
        chunk = all_data[i:i+chunk_size]
        new_cursor.executemany("""
            INSERT INTO cleaned_csvs 
            (Date, ExpiryDate, Instrument, Symbol, StrikePrice, 
             OptionType, Open, High, Low, Close, SettledPrice, 
             Contracts, TurnOver, OpenInterest)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, chunk)
        
        if (i + chunk_size) % 100000 == 0:
            elapsed = (datetime.now() - start_time).total_seconds()
            rate = (i + chunk_size) / elapsed if elapsed > 0 else 0
            print(f"   Inserted {i + chunk_size:,} rows ({rate:,.0f} rows/sec)...")
    
    new_conn.commit()
    duration = (datetime.now() - start_time).total_seconds()
    print(f"   [OK] Inserted all rows in {duration:.1f} seconds")
    
    # Insert metadata
    print(f"\n4. Inserting metadata...")
    new_cursor.executemany("""
        INSERT INTO ingestion_metadata 
        (file_path, file_name, file_size, file_hash, ingestion_date, row_count, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, metadata)
    new_conn.commit()
    print(f"   [OK] Inserted {len(metadata)} metadata records")
    
    # Create indices
    print(f"\n5. Creating indices...")
    new_cursor.execute("CREATE INDEX idx_cleaned_csvs_date ON cleaned_csvs(Date)")
    new_cursor.execute("CREATE INDEX idx_cleaned_csvs_symbol ON cleaned_csvs(Symbol)")
    new_cursor.execute("CREATE INDEX idx_cleaned_csvs_date_symbol ON cleaned_csvs(Date, Symbol)")
    new_cursor.execute("CREATE INDEX idx_cleaned_csvs_expiry ON cleaned_csvs(ExpiryDate)")
    new_cursor.execute("CREATE INDEX idx_strike_data_date ON strike_data(Date)")
    new_conn.commit()
    print("   [OK] All indices created")
    
    # Verify
    new_cursor.execute("SELECT COUNT(*) FROM cleaned_csvs")
    new_count = new_cursor.fetchone()[0]
    
    new_cursor.execute("SELECT COUNT(*) FROM ingestion_metadata")
    new_meta_count = new_cursor.fetchone()[0]
    
    new_conn.close()
    
    print("\n" + "="*80)
    print("MIGRATION COMPLETE")
    print("="*80)
    print(f"Old database: {total_rows:,} rows")
    print(f"New database: {new_count:,} rows")
    print(f"Metadata: {new_meta_count} files tracked")
    print(f"\nNext steps:")
    print(f"1. Backup old database: move {old_db} {old_db}.backup")
    print(f"2. Rename new database: move {new_db} {old_db}")
    print(f"3. Continue ingestion: python bhavcopy_db_builder.py --ingest --directory cleaned_csvs")
    print(f"\nExpected speed: 1000-2000 files/sec (no slowdown!)")

if __name__ == "__main__":
    migrate_database()
