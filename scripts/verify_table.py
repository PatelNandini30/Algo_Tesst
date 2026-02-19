import sqlite3

conn = sqlite3.connect('bhavcopy_data.db')
cursor = conn.cursor()

# Check table structure
cursor.execute('PRAGMA table_info(ingestion_metadata)')
print('ingestion_metadata table structure:')
for row in cursor.fetchall():
    print(f'  {row[1]} ({row[2]})')

# Check if any files are already tracked
cursor.execute('SELECT COUNT(*) FROM ingestion_metadata')
count = cursor.fetchone()[0]
print(f'\nFiles currently tracked: {count}')

conn.close()
