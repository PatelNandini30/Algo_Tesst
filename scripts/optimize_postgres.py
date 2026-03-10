#!/usr/bin/env python3
"""
Apply PostgreSQL performance optimizations.
Run this after data import to create indexes and optimize queries.
"""
import os
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from database import engine
from sqlalchemy import text


def apply_indexes():
    """Apply performance indexes."""
    print("🔧 Applying performance indexes...")
    
    migration_file = Path(__file__).parent.parent / "migrations" / "004_add_performance_indexes.sql"
    
    if not migration_file.exists():
        print(f"❌ Migration file not found: {migration_file}")
        return False
    
    with open(migration_file) as f:
        sql = f.read()
    
    try:
        with engine.begin() as conn:
            # Split by semicolon and execute each statement
            statements = [s.strip() for s in sql.split(';') if s.strip()]
            for i, stmt in enumerate(statements, 1):
                print(f"  Executing statement {i}/{len(statements)}...")
                conn.execute(text(stmt))
        
        print("✅ Indexes created successfully!")
        return True
    except Exception as e:
        print(f"❌ Error creating indexes: {e}")
        return False


def check_index_usage():
    """Check if indexes are being used."""
    print("\n📊 Checking index usage...")
    
    query = text("""
        SELECT 
            schemaname,
            tablename,
            indexname,
            idx_scan as index_scans,
            idx_tup_read as tuples_read,
            idx_tup_fetch as tuples_fetched
        FROM pg_stat_user_indexes
        WHERE schemaname = 'public'
        ORDER BY idx_scan DESC
        LIMIT 10;
    """)
    
    try:
        with engine.begin() as conn:
            result = conn.execute(query)
            rows = result.fetchall()
            
            if rows:
                print("\nTop 10 most used indexes:")
                print(f"{'Table':<20} {'Index':<40} {'Scans':<10}")
                print("-" * 70)
                for row in rows:
                    print(f"{row[1]:<20} {row[2]:<40} {row[3]:<10}")
            else:
                print("No index usage data yet (indexes may be newly created)")
    except Exception as e:
        print(f"❌ Error checking indexes: {e}")


def vacuum_analyze():
    """Run VACUUM ANALYZE to optimize tables."""
    print("\n🧹 Running VACUUM ANALYZE...")
    
    tables = ['option_data', 'spot_data', 'expiry_calendar', 'super_trend_segments']
    
    try:
        # VACUUM can't run in a transaction
        conn = engine.raw_connection()
        conn.set_isolation_level(0)  # AUTOCOMMIT
        cursor = conn.cursor()
        
        for table in tables:
            print(f"  Analyzing {table}...")
            cursor.execute(f"VACUUM ANALYZE {table};")
        
        cursor.close()
        conn.close()
        print("✅ VACUUM ANALYZE completed!")
    except Exception as e:
        print(f"❌ Error during VACUUM: {e}")


def show_table_stats():
    """Show table statistics."""
    print("\n📈 Table statistics:")
    
    query = text("""
        SELECT 
            schemaname,
            tablename,
            pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS total_size,
            pg_size_pretty(pg_relation_size(schemaname||'.'||tablename)) AS table_size,
            pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename) - pg_relation_size(schemaname||'.'||tablename)) AS index_size
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
    """)
    
    try:
        with engine.begin() as conn:
            result = conn.execute(query)
            rows = result.fetchall()
            
            print(f"\n{'Table':<30} {'Total Size':<15} {'Table Size':<15} {'Index Size':<15}")
            print("-" * 75)
            for row in rows:
                print(f"{row[1]:<30} {row[2]:<15} {row[3]:<15} {row[4]:<15}")
    except Exception as e:
        print(f"❌ Error getting table stats: {e}")


if __name__ == "__main__":
    print("🚀 PostgreSQL Performance Optimization")
    print("=" * 50)
    
    # Apply indexes
    if apply_indexes():
        # Run VACUUM ANALYZE
        vacuum_analyze()
        
        # Show statistics
        show_table_stats()
        check_index_usage()
        
        print("\n✅ Optimization complete!")
        print("\n💡 Tips:")
        print("  - Restart your backend container to apply connection pool changes")
        print("  - Monitor query performance with: SELECT * FROM pg_stat_statements;")
        print("  - Check slow queries in PostgreSQL logs")
    else:
        print("\n❌ Optimization failed!")
        sys.exit(1)
