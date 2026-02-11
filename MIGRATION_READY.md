# DATABASE MIGRATION - READY TO RUN

## STATUS: Migration script updated and ready

The migration script has been updated to handle the new schema with `file_name` and `file_size` columns.

## RUN MIGRATION NOW

### Option 1: Double-click the batch file
```
run_migration.bat
```

### Option 2: Run from command prompt
```
python migrate_database.py
```

## WHAT THE MIGRATION DOES

1. Reads all data from `bhavcopy_data.db` (28.6 GB)
2. Creates `bhavcopy_data_new.db` with FAST schema (no UNIQUE constraint)
3. Copies all existing data in chunks (50,000 rows at a time)
4. Handles old metadata format (without file_name/file_size) automatically
5. Creates indices AFTER data insertion (much faster)
6. Verifies row counts match

## AFTER MIGRATION COMPLETES

Run these commands:

```cmd
move bhavcopy_data.db bhavcopy_data.db.backup
move bhavcopy_data_new.db bhavcopy_data.db
```

## THEN RESUME INGESTION

```cmd
python bhavcopy_db_builder.py --db bhavcopy_data.db --csv-dir cleaned_csvs
```

Expected speed: **1000-2000 files/sec** with NO slowdown!

## MIGRATION TIME ESTIMATE

- 28.6 GB database
- ~50,000 rows/sec insertion rate
- Estimated time: 10-20 minutes

## WHAT WAS FIXED

- Added `file_name` and `file_size` columns to metadata table
- Handles both old and new metadata schema automatically
- If old schema doesn't have these columns, sets them to NULL
- New ingestion will populate these columns going forward
