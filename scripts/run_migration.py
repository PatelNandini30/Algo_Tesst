"""
Database Migration Script
Applies SQL migrations to the database
"""
import sqlite3
import os


def run_migration(db_path: str = "bhavcopy_data.db", migration_file: str = "migrations/001_add_execution_tables.sql"):
    """Run database migration"""
    
    if not os.path.exists(db_path):
        print(f"Error: Database file '{db_path}' not found")
        return False
    
    if not os.path.exists(migration_file):
        print(f"Error: Migration file '{migration_file}' not found")
        return False
    
    print(f"Running migration: {migration_file}")
    print(f"Target database: {db_path}")
    
    try:
        # Read migration SQL
        with open(migration_file, 'r') as f:
            migration_sql = f.read()
        
        # Connect to database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Execute migration
        cursor.executescript(migration_sql)
        conn.commit()
        
        # Verify tables were created
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' 
            AND name IN ('strategy_registry', 'execution_runs', 'execution_results', 'parameter_cache', 'db_metadata')
        """)
        tables = cursor.fetchall()
        
        print(f"\n✓ Migration completed successfully!")
        print(f"✓ Created {len(tables)} tables:")
        for table in tables:
            print(f"  - {table[0]}")
        
        # Show schema version
        cursor.execute("SELECT value FROM db_metadata WHERE key = 'schema_version'")
        version = cursor.fetchone()
        if version:
            print(f"\n✓ Schema version: {version[0]}")
        
        conn.close()
        return True
    
    except Exception as e:
        print(f"\n✗ Migration failed: {str(e)}")
        return False


if __name__ == "__main__":
    success = run_migration()
    exit(0 if success else 1)
