# FINAL FIX - Remove UNIQUE Constraint for Maximum Speed

## Problem Identified
The UNIQUE constraint on the business key creates an **implicit index** that SQLite uses to check for duplicates on every INSERT. This implicit index:
- Cannot be dropped with DROP INDEX
- Grows with table size
- Causes massive slowdown (from 2674 files/sec to 5 files/sec)

## Solution Applied
1. **Removed UNIQUE constraint** from table definition (line ~127)
2. **Changed INSERT OR IGNORE to plain INSERT** (line ~434)
3. Duplicates are already handled by `validate_business_key()` method

## Steps to Apply Fix

### Option 1: Start Fresh (RECOMMENDED - Fastest)
```bash
# 1. Stop the current script (Ctrl+C)

# 2. Delete the old database
del bhavcopy_data.db
del bhavcopy_data.db-shm
del bhavcopy_data.db-wal

# 3. Recreate with new schema (no UNIQUE constraint)
python bhavcopy_db_builder.py --create --directory cleaned_csvs

# 4. Run ingestion - should maintain 1000+ files/sec throughout
python bhavcopy_db_builder.py --ingest --directory cleaned_csvs
```

### Option 2: Migrate Existing Data (Slower but preserves work)
```bash
# 1. Stop the current script (Ctrl+C)

# 2. Export existing data
sqlite3 bhavcopy_data.db "SELECT * FROM cleaned_csvs" > exported_data.csv

# 3. Backup old database
move bhavcopy_data.db bhavcopy_data_old.db

# 4. Create new database with fixed schema
python bhavcopy_db_builder.py --create --directory cleaned_csvs

# 5. Import old data (will be fast since no UNIQUE constraint)
# Then continue with remaining files
python bhavcopy_db_builder.py --ingest --directory cleaned_csvs
```

## Expected Performance
- **Before fix**: 2674 files/sec → 5 files/sec (535x slowdown)
- **After fix**: 1000-2000 files/sec maintained throughout entire process
- **Total time**: ~3-6 minutes for all 6362 files (vs 20+ hours with UNIQUE constraint)

## Why This Works
- No implicit index to check on every INSERT
- Plain INSERT is the fastest SQLite operation
- Duplicates already removed in Python before INSERT
- File hash tracking prevents reprocessing same files
- Indices only built once at the end (fast)

## Verification
After running, check the log:
```bash
# Should see consistent high speed throughout
Get-Content bhavcopy_builder.log | Select-String "files/sec"
```

You should see speeds like:
- [50/6362] Processing... (1500 files/sec)
- [2500/6362] Processing... (1200 files/sec)  ← No slowdown!
- [6000/6362] Processing... (1100 files/sec)
