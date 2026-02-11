# NSE Bhavcopy Database - Quick Reference Guide

## 🚀 One-Command Setup

```bash
# Complete workflow (create + ingest + audit)
python workflow.py --db bhavcopy_data.db --csv-dir ./csv_data
```

---

## 📋 Common Commands

### Database Creation

```bash
# Create new database
python bhavcopy_db_builder.py --db bhavcopy_data.db --csv-dir ./csv_data --create

# Force recreate (⚠️ DELETES existing data)
python bhavcopy_db_builder.py --db bhavcopy_data.db --csv-dir ./csv_data --create --force
```

### Data Ingestion

```bash
# Ingest all CSV files
python bhavcopy_db_builder.py --db bhavcopy_data.db --csv-dir ./csv_data

# Ingest specific year
python bhavcopy_db_builder.py --db bhavcopy_data.db --csv-dir ./csv_data/2024

# Ingest with custom pattern
python bhavcopy_db_builder.py --db bhavcopy_data.db --csv-dir ./csv_data --pattern "fo*2024*.csv"
```

### Build Auxiliary Tables

```bash
# Build expiry_data, strike_data, filter_data
python bhavcopy_db_builder.py --db bhavcopy_data.db --build-aux-tables
```

### Validation & Auditing

```bash
# Run comprehensive audit
python bhavcopy_audit.py --db bhavcopy_data.db --csv-dir ./csv_data --output audit_report.json

# Deep validation for specific date range
python deep_validator.py --db bhavcopy_data.db --csv ./path/to/file.csv --start-date 2024-01-01 --end-date 2024-01-31

# Generate remediation plan
python remediation_planner.py --audit-report audit_report.json --output remediation_plan.json
```

---

## 📊 Database Queries

### Get Database Statistics

```sql
-- Total records
SELECT COUNT(*) FROM cleaned_csvs;

-- Unique trading dates
SELECT COUNT(DISTINCT Date) FROM cleaned_csvs;

-- Unique symbols
SELECT COUNT(DISTINCT Symbol) FROM cleaned_csvs;

-- Date range
SELECT MIN(Date) as start_date, MAX(Date) as end_date FROM cleaned_csvs;

-- Records per year
SELECT strftime('%Y', Date) as year, COUNT(*) as records
FROM cleaned_csvs
GROUP BY year
ORDER BY year;
```

### Data Quality Checks

```sql
-- Check for duplicates
SELECT Date, Symbol, Instrument, ExpiryDate, StrikePrice, OptionType, COUNT(*) as count
FROM cleaned_csvs
GROUP BY Date, Symbol, Instrument, ExpiryDate, StrikePrice, OptionType
HAVING COUNT(*) > 1;

-- Check for NULL values in critical columns
SELECT 
    SUM(CASE WHEN Date IS NULL THEN 1 ELSE 0 END) as null_dates,
    SUM(CASE WHEN Symbol IS NULL THEN 1 ELSE 0 END) as null_symbols,
    SUM(CASE WHEN Close IS NULL THEN 1 ELSE 0 END) as null_close
FROM cleaned_csvs;

-- Records per trading date
SELECT Date, COUNT(*) as records
FROM cleaned_csvs
GROUP BY Date
ORDER BY Date DESC
LIMIT 10;
```

### Business Analysis Queries

```sql
-- Option chain for a symbol on a date
SELECT *
FROM cleaned_csvs
WHERE Symbol = 'NIFTY'
AND Date = '2024-01-15'
AND Instrument IN ('OPTIDX', 'OPTSTK')
ORDER BY StrikePrice, OptionType;

-- Futures data for a symbol
SELECT Date, Close, OpenInterest, Contracts
FROM cleaned_csvs
WHERE Symbol = 'BANKNIFTY'
AND Instrument = 'FUTIDX'
ORDER BY Date DESC
LIMIT 30;

-- Highest open interest options
SELECT Date, Symbol, StrikePrice, OptionType, OpenInterest
FROM cleaned_csvs
WHERE Instrument IN ('OPTIDX', 'OPTSTK')
ORDER BY OpenInterest DESC
LIMIT 100;
```

---

## 🔧 Troubleshooting

### Database Locked

```bash
# Check processes using the database
lsof bhavcopy_data.db

# Force close connections (Linux/Mac)
fuser -k bhavcopy_data.db
```

### Remove Duplicates

```sql
-- Remove duplicates (BACKUP FIRST!)
DELETE FROM cleaned_csvs
WHERE id NOT IN (
    SELECT MIN(id)
    FROM cleaned_csvs
    GROUP BY Date, Symbol, Instrument, ExpiryDate, StrikePrice, OptionType
);

-- Verify
SELECT Date, Symbol, COUNT(*) as count
FROM cleaned_csvs
GROUP BY Date, Symbol, Instrument, ExpiryDate, StrikePrice, OptionType
HAVING COUNT(*) > 1;
```

### Rebuild Tables

```bash
# Drop and rebuild auxiliary tables
sqlite3 bhavcopy_data.db "DELETE FROM expiry_data; DELETE FROM strike_data; DELETE FROM filter_data;"

python bhavcopy_db_builder.py --db bhavcopy_data.db --build-aux-tables
```

### Check Ingestion History

```sql
-- View all processed files
SELECT * FROM ingestion_metadata ORDER BY ingestion_date DESC;

-- Files processed today
SELECT file_path, row_count, status
FROM ingestion_metadata
WHERE DATE(ingestion_date) = DATE('now');

-- Failed ingestions
SELECT * FROM ingestion_metadata WHERE status = 'ERROR';
```

---

## 📁 File Structure

```
project/
├── bhavcopy_db_builder.py     # Database creation & ingestion
├── bhavcopy_audit.py           # Comprehensive validation
├── deep_validator.py           # Value-level comparison
├── remediation_planner.py      # Issue resolution planning
├── workflow.py                 # All-in-one workflow
├── requirements.txt            # Python dependencies
├── README.md                   # Full documentation
├── QUICK_REFERENCE.md          # This file
│
├── bhavcopy_data.db            # SQLite database (created)
├── bhavcopy_builder.log        # Builder logs
├── audit_report.json           # Audit results
├── remediation_plan.json       # Remediation plan
├── ingestion_results.json      # Ingestion summary
│
├── csv_data/                   # Input CSV files
│   ├── 2020/
│   ├── 2021/
│   └── ...
│
└── sql_scripts/                # Generated SQL fixes
    └── remove_duplicates_*.sql
```

---

## ⚡ Performance Tips

### Faster Ingestion

```bash
# Process files in parallel (requires GNU parallel)
ls ./csv_data/**/*.csv | parallel -j 4 python bhavcopy_db_builder.py --db bhavcopy_data.db --csv-dir {}
```

### Optimize Database

```sql
-- Vacuum database to reclaim space
VACUUM;

-- Analyze for query optimization
ANALYZE;

-- Rebuild indices
REINDEX;
```

### Memory Settings (for very large databases)

```python
# Add to builder script if needed
conn.execute("PRAGMA cache_size = -2000000")  # 2GB cache
conn.execute("PRAGMA temp_store = MEMORY")    # Use RAM for temp
```

---

## 🎯 Best Practices

1. **Always backup before major operations**
   ```bash
   cp bhavcopy_data.db bhavcopy_data.db.backup
   ```

2. **Run audit after ingestion**
   ```bash
   python bhavcopy_audit.py --db bhavcopy_data.db --csv-dir ./csv_data
   ```

3. **Use idempotent ingestion**
   - Safe to re-run
   - Skips already processed files
   - No duplicate data

4. **Monitor logs**
   ```bash
   tail -f bhavcopy_builder.log
   ```

5. **Regular integrity checks**
   ```bash
   sqlite3 bhavcopy_data.db "PRAGMA integrity_check;"
   ```

---

## 📞 Getting Help

1. Check logs: `bhavcopy_builder.log`
2. Review audit report: `audit_report.json`
3. Check remediation plan: `remediation_plan.json`
4. See full documentation: `README.md`

---

**Last Updated**: February 2026  
**Version**: 1.0.0
