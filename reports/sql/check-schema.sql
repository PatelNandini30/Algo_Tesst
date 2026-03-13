-- Quick check: What columns exist in option_data?
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'option_data'
ORDER BY ordinal_position;

-- Quick check: What indexes exist?
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE tablename = 'option_data'
ORDER BY indexname;

-- Quick check: Table size
SELECT pg_size_pretty(pg_total_relation_size('option_data')) as size;

-- Quick check: Sample data
SELECT * FROM option_data 
WHERE symbol = 'NIFTY' 
LIMIT 1;
