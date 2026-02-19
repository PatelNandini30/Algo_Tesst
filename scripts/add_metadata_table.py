import sqlite3
import time

# Wait a moment for any locks to clear
time.sleep(2)

conn = sqlite3.connect('bhavcopy_data.db')
cursor = conn.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS ingestion_metadata (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path TEXT UNIQUE,
        file_hash TEXT,
        ingestion_date TIMESTAMP,
        row_count INTEGER,
        status TEXT
    )
""")

conn.commit()
print("✓ ingestion_metadata table created successfully")

# Verify
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
print(f"\nAll tables in database: {', '.join(tables)}")

conn.close()
