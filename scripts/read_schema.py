#!/usr/bin/env python3
"""
Database Schema Reader
=====================
Read and display database schema information
"""

import sqlite3
import sys

def read_database_schema(db_path):
    """Read and display database schema"""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        print("=" * 80)
        print("DATABASE SCHEMA ANALYSIS")
        print("=" * 80)
        print(f"Database: {db_path}")
        print()
        
        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = cursor.fetchall()
        
        print(f"Found {len(tables)} tables:")
        print("-" * 40)
        for table in tables:
            print(f"  {table[0]}")
        print()
        
        # Get detailed schema for each table
        for table_name in tables:
            table_name = table_name[0]
            print(f"TABLE: {table_name}")
            print("-" * 80)
            
            # Get table schema
            cursor.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table_name}'")
            schema = cursor.fetchone()
            if schema and schema[0]:
                print("Schema:")
                print(schema[0])
                print()
            
            # Get column info
            try:
                cursor.execute(f"PRAGMA table_info({table_name})")
                columns = cursor.fetchall()
                print("Columns:")
                for col in columns:
                    print(f"  {col[1]} ({col[2]}) {'' if col[3] else 'NULL'} {'' if col[4] is None else 'DEFAULT ' + str(col[4])} {'(PK)' if col[5] else ''}")
                print()
                
                # Get row count
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cursor.fetchone()[0]
                print(f"Row count: {count:,}")
                print()
                
                # Get sample data (first 3 rows)
                try:
                    cursor.execute(f"SELECT * FROM {table_name} LIMIT 3")
                    rows = cursor.fetchall()
                    if rows:
                        print("Sample data (first 3 rows):")
                        for i, row in enumerate(rows):
                            print(f"  Row {i+1}: {row}")
                        print()
                except Exception as e:
                    print(f"Could not fetch sample data: {e}")
                    print()
                    
            except Exception as e:
                print(f"Error getting table info: {e}")
                print()
        
        # Get indexes
        print("INDEXES:")
        print("-" * 40)
        cursor.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' ORDER BY tbl_name, name")
        indexes = cursor.fetchall()
        for idx in indexes:
            print(f"  {idx[0]} ON {idx[1]}")
            if idx[2]:
                print(f"    {idx[2]}")
        print()
        
        # Get views
        cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='view' ORDER BY name")
        views = cursor.fetchall()
        if views:
            print("VIEWS:")
            print("-" * 40)
            for view in views:
                print(f"  {view[0]}")
                print(f"    {view[1]}")
            print()
        
        conn.close()
        
    except Exception as e:
        print(f"Error reading database: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python read_schema.py <database_path>")
        sys.exit(1)
    
    db_path = sys.argv[1]
    read_database_schema(db_path)