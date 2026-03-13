#!/bin/bash
# Apply index optimizations to PostgreSQL
# Run this script to speed up backtest queries

echo "=== Applying Index Optimizations ==="

# Wait for PostgreSQL to be ready
echo "Waiting for PostgreSQL..."
until PGPASSWORD=algotest_password psql -h postgres -U algotest -d algotest -c '\q' 2>/dev/null; do
  sleep 1
done
echo "PostgreSQL is ready!"

# Run the index SQL
echo "Creating indexes (this may take a few minutes on 73GB table)..."
PGPASSWORD=algotest_password psql -h postgres -U algotest -d algotest -f /data/index_optimization.sql

echo "=== Index creation complete ==="

# Verify indexes
echo "Current indexes on option_data:"
PGPASSWORD=algotest_password psql -h postgres -U algotest -d algotest -c "SELECT indexname FROM pg_indexes WHERE tablename = 'option_data' ORDER BY indexname;"

echo "=== Done ==="
