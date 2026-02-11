@echo off
cd /d "e:\Algo_Test_Software"
echo Getting database schema information...
echo. > db_schema_info.txt
echo DATABASE SCHEMA INFORMATION >> db_schema_info.txt
echo =========================== >> db_schema_info.txt
echo. >> db_schema_info.txt

echo Tables in database: >> db_schema_info.txt
python -c "import sqlite3; conn=sqlite3.connect('bhavcopy_data.db'); cursor=conn.cursor(); cursor.execute('SELECT name FROM sqlite_master WHERE type=\"table\"'); tables=[row[0] for row in cursor.fetchall()]; print('Found', len(tables), 'tables:'); [print('  -', table) for table in tables]; conn.close()" >> db_schema_info.txt 2>&1

echo. >> db_schema_info.txt
echo Table schemas: >> db_schema_info.txt
python -c "import sqlite3; conn=sqlite3.connect('bhavcopy_data.db'); cursor=conn.cursor(); cursor.execute('SELECT name,sql FROM sqlite_master WHERE type=\"table\"'); schemas=cursor.fetchall(); [print(f'{name}:\n{sql}\n') for name,sql in schemas if sql]; conn.close()" >> db_schema_info.txt 2>&1

echo. >> db_schema_info.txt
echo Row counts: >> db_schema_info.txt
python -c "import sqlite3; conn=sqlite3.connect('bhavcopy_data.db'); cursor=conn.cursor(); cursor.execute('SELECT name FROM sqlite_master WHERE type=\"table\"'); tables=[row[0] for row in cursor.fetchall()]; [print(f'{table}:', cursor.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 'rows') for table in tables]; conn.close()" >> db_schema_info.txt 2>&1

echo Database schema information saved to db_schema_info.txt
type db_schema_info.txt
pause