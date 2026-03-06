#!/bin/bash
# Backend startup script - waits for database and runs migrations

echo "Starting backend..."

# Wait for PostgreSQL
echo "Waiting for PostgreSQL..."
until PGPASSWORD=$POSTGRES_PASSWORD psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c '\q' 2>/dev/null; do
    echo "PostgreSQL is unavailable - sleeping"
    sleep 2
done

echo "PostgreSQL is up!"

# Run migrations if database is empty
echo "Checking database schema..."
if ! PGPASSWORD=$POSTGRES_PASSWORD psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1 FROM db_metadata LIMIT 1" 2>/dev/null; then
    echo "Database schema not found. Run migrations manually."
else
    echo "Database schema OK"
fi

# Start the application
echo "Starting uvicorn..."
exec uvicorn main:app --host 0.0.0.0 --port 8000
