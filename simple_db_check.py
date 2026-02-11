import sqlite3

# Simple test to check database accessibility
try:
    conn = sqlite3.connect('bhavcopy_data.db')
    cursor = conn.cursor()
    
    print("Database connected successfully!")
    
    # Get table names
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    
    print(f"\nTables found: {len(tables)}")
    for table in tables:
        print(f"  - {table[0]}")
    
    # Get schema for each table
    print("\n" + "="*50)
    print("TABLE SCHEMAS:")
    print("="*50)
    
    for table in tables:
        table_name = table[0]
        print(f"\n{table_name}:")
        cursor.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table_name}'")
        schema = cursor.fetchone()
        if schema and schema[0]:
            print(schema[0])
        
        # Get row count
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = cursor.fetchone()[0]
            print(f"Rows: {count:,}")
        except Exception as e:
            print(f"Could not count rows: {e}")
    
    conn.close()
    print("\nDatabase analysis complete!")
    
except Exception as e:
    print(f"Error: {e}")