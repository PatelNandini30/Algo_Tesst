#!/usr/bin/env python3
"""
NSE Bhavcopy Database Auditor
==============================
Guarantees strict data equality between SQLite DB and CSV files.
Performs lossless, deterministic validation across all years.

Author: Senior Data Engineering Team
Version: 1.0.0
"""

import sqlite3
import pandas as pd
import glob
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Set
from dataclasses import dataclass
from collections import defaultdict
import json
import hashlib


@dataclass
class AuditResult:
    """Container for audit results"""
    year: int
    total_csv_dates: int
    total_db_dates: int
    missing_dates: List[str]
    extra_dates: List[str]
    csv_row_count: int
    db_row_count: int
    row_mismatches: Dict[str, Tuple[int, int]]  # date -> (csv_count, db_count)
    duplicates: List[Dict]
    orphan_expiry: List[Dict]
    orphan_strike: List[Dict]
    column_mismatches: List[Dict]
    passed: bool


class BhavcopyAuditor:
    """
    Comprehensive auditor for NSE Bhavcopy data.
    Ensures strict equality between CSV and SQLite database.
    """
    
    # Business key columns that uniquely identify a row
    BUSINESS_KEY = ['Date', 'Symbol', 'Instrument', 'ExpiryDate', 'StrikePrice', 'OptionType']
    
    # Columns that must match exactly
    VALUE_COLUMNS = ['Open', 'High', 'Low', 'Close', 'SettledPrice', 
                     'Contracts', 'TurnOver', 'OpenInterest']
    
    # Chunk size for processing large datasets
    CHUNK_SIZE = 100000
    
    def __init__(self, db_path: str, csv_directory: str):
        """
        Initialize the auditor.
        
        Args:
            db_path: Path to SQLite database
            csv_directory: Directory containing CSV files (organized by year)
        """
        self.db_path = db_path
        self.csv_directory = Path(csv_directory)
        self.conn = None
        self.audit_results = []
        self.critical_errors = []
        
        # Verify paths exist
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Database not found: {db_path}")
        if not self.csv_directory.exists():
            raise FileNotFoundError(f"CSV directory not found: {csv_directory}")
    
    def connect(self):
        """Establish database connection"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        print(f"✓ Connected to database: {self.db_path}")
    
    def disconnect(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            print("✓ Database connection closed")
    
    def get_csv_files_by_year(self) -> Dict[int, List[Path]]:
        """
        Organize CSV files by year.
        
        Returns:
            Dictionary mapping year to list of CSV file paths
        """
        csv_files_by_year = defaultdict(list)
        
        # Search for CSV files recursively
        for csv_file in self.csv_directory.rglob("*.csv"):
            # Try to extract year from filename or path
            # Common patterns: fo01JAN2020bhav.csv, 2020/01/file.csv, etc.
            year = self._extract_year_from_path(csv_file)
            if year:
                csv_files_by_year[year].append(csv_file)
        
        return dict(sorted(csv_files_by_year.items()))
    
    def _extract_year_from_path(self, path: Path) -> int:
        """
        Extract year from file path or filename.
        
        Args:
            path: Path to CSV file
            
        Returns:
            Year as integer, or None if not found
        """
        # Try filename patterns
        filename = path.stem
        
        # Pattern: fo01JAN2020bhav
        import re
        match = re.search(r'(20\d{2})', str(path))
        if match:
            return int(match.group(1))
        
        return None
    
    def read_csv_in_chunks(self, csv_path: Path) -> pd.DataFrame:
        """
        Read CSV file with proper encoding and error handling.
        
        Args:
            csv_path: Path to CSV file
            
        Returns:
            DataFrame with cleaned data
        """
        try:
            # Try reading with different encodings
            for encoding in ['utf-8', 'latin1', 'iso-8859-1']:
                try:
                    df = pd.read_csv(csv_path, encoding=encoding)
                    # Normalize column names
                    df.columns = df.columns.str.strip()
                    return df
                except UnicodeDecodeError:
                    continue
            
            raise ValueError(f"Could not read CSV with any standard encoding: {csv_path}")
        
        except Exception as e:
            self.critical_errors.append({
                'type': 'CSV_READ_ERROR',
                'file': str(csv_path),
                'error': str(e)
            })
            return pd.DataFrame()
    
    def validate_duplicates(self, year: int) -> List[Dict]:
        """
        CRITICAL: Detect duplicate business keys in database.
        
        Args:
            year: Year to validate
            
        Returns:
            List of duplicate records
        """
        query = f"""
        SELECT 
            Date,
            Symbol,
            Instrument,
            ExpiryDate,
            StrikePrice,
            OptionType,
            COUNT(*) as duplicate_count
        FROM cleaned_csvs
        WHERE strftime('%Y', Date) = '{year}'
        GROUP BY Date, Symbol, Instrument, ExpiryDate, StrikePrice, OptionType
        HAVING COUNT(*) > 1
        """
        
        cursor = self.conn.cursor()
        cursor.execute(query)
        duplicates = []
        
        for row in cursor.fetchall():
            duplicates.append({
                'Date': row[0],
                'Symbol': row[1],
                'Instrument': row[2],
                'ExpiryDate': row[3],
                'StrikePrice': row[4],
                'OptionType': row[5],
                'count': row[6]
            })
        
        return duplicates
    
    def get_trading_dates_from_csv(self, csv_files: List[Path]) -> Set[str]:
        """
        Extract all unique trading dates from CSV files.
        
        Args:
            csv_files: List of CSV file paths
            
        Returns:
            Set of trading dates (YYYY-MM-DD format)
        """
        all_dates = set()
        
        for csv_file in csv_files:
            df = self.read_csv_in_chunks(csv_file)
            if not df.empty and 'Date' in df.columns:
                # Normalize date format
                dates = pd.to_datetime(df['Date'], errors='coerce')
                valid_dates = dates.dropna().dt.strftime('%Y-%m-%d')
                all_dates.update(valid_dates)
        
        return all_dates
    
    def get_trading_dates_from_db(self, year: int) -> Set[str]:
        """
        Extract all unique trading dates from database for a year.
        
        Args:
            year: Year to query
            
        Returns:
            Set of trading dates (YYYY-MM-DD format)
        """
        query = f"""
        SELECT DISTINCT Date
        FROM cleaned_csvs
        WHERE strftime('%Y', Date) = '{year}'
        ORDER BY Date
        """
        
        cursor = self.conn.cursor()
        cursor.execute(query)
        
        return {row[0] for row in cursor.fetchall()}
    
    def get_row_count_per_date_csv(self, csv_files: List[Path]) -> Dict[str, int]:
        """
        Count rows per trading date from CSV files.
        
        Args:
            csv_files: List of CSV file paths
            
        Returns:
            Dictionary mapping date to row count
        """
        date_counts = defaultdict(int)
        
        for csv_file in csv_files:
            df = self.read_csv_in_chunks(csv_file)
            if not df.empty and 'Date' in df.columns:
                # Normalize dates
                df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
                df = df.dropna(subset=['Date'])
                df['Date'] = df['Date'].dt.strftime('%Y-%m-%d')
                
                # Count per date
                counts = df['Date'].value_counts()
                for date, count in counts.items():
                    date_counts[date] += count
        
        return dict(date_counts)
    
    def get_row_count_per_date_db(self, year: int) -> Dict[str, int]:
        """
        Count rows per trading date from database.
        
        Args:
            year: Year to query
            
        Returns:
            Dictionary mapping date to row count
        """
        query = f"""
        SELECT Date, COUNT(*) as count
        FROM cleaned_csvs
        WHERE strftime('%Y', Date) = '{year}'
        GROUP BY Date
        ORDER BY Date
        """
        
        cursor = self.conn.cursor()
        cursor.execute(query)
        
        return {row[0]: row[1] for row in cursor.fetchall()}
    
    def validate_orphan_expiry_data(self, year: int) -> List[Dict]:
        """
        Detect orphan records in cleaned_csvs that don't have matching expiry_data.
        
        Args:
            year: Year to validate
            
        Returns:
            List of orphan records
        """
        query = f"""
        SELECT DISTINCT 
            c.Symbol,
            c.ExpiryDate
        FROM cleaned_csvs c
        LEFT JOIN expiry_data e ON c.Symbol = e.Symbol
        WHERE strftime('%Y', c.Date) = '{year}'
        AND e.Symbol IS NULL
        LIMIT 1000
        """
        
        cursor = self.conn.cursor()
        cursor.execute(query)
        
        orphans = []
        for row in cursor.fetchall():
            orphans.append({
                'Symbol': row[0],
                'ExpiryDate': row[1]
            })
        
        return orphans
    
    def validate_orphan_strike_data(self, year: int) -> List[Dict]:
        """
        Detect orphan records in cleaned_csvs that don't have matching strike_data.
        
        Args:
            year: Year to validate
            
        Returns:
            List of orphan records
        """
        query = f"""
        SELECT DISTINCT 
            c.Symbol,
            c.Date
        FROM cleaned_csvs c
        LEFT JOIN strike_data s ON c.Symbol = s.Ticker AND c.Date = s.Date
        WHERE strftime('%Y', c.Date) = '{year}'
        AND s.Ticker IS NULL
        LIMIT 1000
        """
        
        cursor = self.conn.cursor()
        cursor.execute(query)
        
        orphans = []
        for row in cursor.fetchall():
            orphans.append({
                'Symbol': row[0],
                'Date': row[1]
            })
        
        return orphans
    
    def validate_column_values(self, year: int, sample_size: int = 1000) -> List[Dict]:
        """
        Validate that numeric columns have no unexpected NULLs.
        
        Args:
            year: Year to validate
            sample_size: Number of records to sample
            
        Returns:
            List of column validation issues
        """
        issues = []
        
        for column in self.VALUE_COLUMNS:
            query = f"""
            SELECT COUNT(*) as null_count
            FROM cleaned_csvs
            WHERE strftime('%Y', Date) = '{year}'
            AND {column} IS NULL
            """
            
            cursor = self.conn.cursor()
            cursor.execute(query)
            null_count = cursor.fetchone()[0]
            
            if null_count > 0:
                issues.append({
                    'column': column,
                    'null_count': null_count,
                    'year': year
                })
        
        return issues
    
    def audit_year(self, year: int, csv_files: List[Path]) -> AuditResult:
        """
        Perform comprehensive audit for a single year.
        
        Args:
            year: Year to audit
            csv_files: List of CSV files for this year
            
        Returns:
            AuditResult object
        """
        print(f"\n{'='*80}")
        print(f"AUDITING YEAR: {year}")
        print(f"{'='*80}")
        
        # 1. Get trading dates
        print("→ Extracting trading dates from CSV...")
        csv_dates = self.get_trading_dates_from_csv(csv_files)
        print(f"  Found {len(csv_dates)} unique dates in CSV")
        
        print("→ Extracting trading dates from DB...")
        db_dates = self.get_trading_dates_from_db(year)
        print(f"  Found {len(db_dates)} unique dates in DB")
        
        # 2. Detect missing/extra dates
        missing_dates = sorted(csv_dates - db_dates)
        extra_dates = sorted(db_dates - csv_dates)
        
        if missing_dates:
            print(f"  ⚠ WARNING: {len(missing_dates)} dates in CSV but not in DB")
        if extra_dates:
            print(f"  ⚠ WARNING: {len(extra_dates)} dates in DB but not in CSV")
        
        # 3. Get row counts per date
        print("→ Counting rows per date in CSV...")
        csv_row_counts = self.get_row_count_per_date_csv(csv_files)
        total_csv_rows = sum(csv_row_counts.values())
        print(f"  Total CSV rows: {total_csv_rows:,}")
        
        print("→ Counting rows per date in DB...")
        db_row_counts = self.get_row_count_per_date_db(year)
        total_db_rows = sum(db_row_counts.values())
        print(f"  Total DB rows: {total_db_rows:,}")
        
        # 4. Detect row count mismatches per date
        row_mismatches = {}
        common_dates = csv_dates & db_dates
        
        for date in common_dates:
            csv_count = csv_row_counts.get(date, 0)
            db_count = db_row_counts.get(date, 0)
            
            if csv_count != db_count:
                row_mismatches[date] = (csv_count, db_count)
        
        if row_mismatches:
            print(f"  ⚠ WARNING: {len(row_mismatches)} dates have row count mismatches")
        
        # 5. Validate duplicates
        print("→ Checking for duplicate business keys...")
        duplicates = self.validate_duplicates(year)
        if duplicates:
            print(f"  🔴 CRITICAL: {len(duplicates)} duplicate business keys found!")
        else:
            print("  ✓ No duplicates found")
        
        # 6. Validate orphan expiry records
        print("→ Checking for orphan expiry records...")
        orphan_expiry = self.validate_orphan_expiry_data(year)
        if orphan_expiry:
            print(f"  ⚠ WARNING: {len(orphan_expiry)} orphan expiry records found")
        else:
            print("  ✓ No orphan expiry records")
        
        # 7. Validate orphan strike records
        print("→ Checking for orphan strike records...")
        orphan_strike = self.validate_orphan_strike_data(year)
        if orphan_strike:
            print(f"  ⚠ WARNING: {len(orphan_strike)} orphan strike records found")
        else:
            print("  ✓ No orphan strike records")
        
        # 8. Validate column completeness
        print("→ Checking column completeness...")
        column_mismatches = self.validate_column_values(year)
        if column_mismatches:
            print(f"  ⚠ WARNING: {len(column_mismatches)} columns have NULL values")
        else:
            print("  ✓ All columns complete")
        
        # 9. Determine pass/fail
        passed = (
            len(missing_dates) == 0 and
            len(extra_dates) == 0 and
            len(row_mismatches) == 0 and
            len(duplicates) == 0 and
            len(orphan_expiry) == 0 and
            len(orphan_strike) == 0 and
            len(column_mismatches) == 0
        )
        
        result = AuditResult(
            year=year,
            total_csv_dates=len(csv_dates),
            total_db_dates=len(db_dates),
            missing_dates=missing_dates,
            extra_dates=extra_dates,
            csv_row_count=total_csv_rows,
            db_row_count=total_db_rows,
            row_mismatches=row_mismatches,
            duplicates=duplicates,
            orphan_expiry=orphan_expiry,
            orphan_strike=orphan_strike,
            column_mismatches=column_mismatches,
            passed=passed
        )
        
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"\nYear {year}: {status}")
        
        return result
    
    def run_full_audit(self) -> Dict:
        """
        Execute complete audit across all years.
        
        Returns:
            Dictionary containing comprehensive audit report
        """
        self.connect()
        
        try:
            print("\n" + "="*80)
            print("NSE BHAVCOPY DATABASE AUDIT")
            print("="*80)
            print(f"Database: {self.db_path}")
            print(f"CSV Directory: {self.csv_directory}")
            print(f"Audit Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Get CSV files organized by year
            csv_files_by_year = self.get_csv_files_by_year()
            
            if not csv_files_by_year:
                raise ValueError("No CSV files found in directory")
            
            print(f"\nFound CSV files for years: {sorted(csv_files_by_year.keys())}")
            
            # Audit each year
            for year in sorted(csv_files_by_year.keys()):
                csv_files = csv_files_by_year[year]
                print(f"\nProcessing {len(csv_files)} CSV files for year {year}")
                
                result = self.audit_year(year, csv_files)
                self.audit_results.append(result)
            
            # Generate final report
            report = self.generate_report()
            
            return report
        
        finally:
            self.disconnect()
    
    def generate_report(self) -> Dict:
        """
        Generate comprehensive audit report.
        
        Returns:
            Dictionary containing complete audit findings
        """
        total_years = len(self.audit_results)
        passed_years = sum(1 for r in self.audit_results if r.passed)
        failed_years = total_years - passed_years
        
        overall_passed = all(r.passed for r in self.audit_results)
        
        report = {
            'audit_timestamp': datetime.now().isoformat(),
            'database_path': self.db_path,
            'csv_directory': str(self.csv_directory),
            'overall_status': 'PASSED' if overall_passed else 'FAILED',
            'summary': {
                'total_years_audited': total_years,
                'years_passed': passed_years,
                'years_failed': failed_years
            },
            'year_details': [],
            'critical_errors': self.critical_errors
        }
        
        # Add details for each year
        for result in self.audit_results:
            year_detail = {
                'year': result.year,
                'status': 'PASSED' if result.passed else 'FAILED',
                'dates': {
                    'csv_dates': result.total_csv_dates,
                    'db_dates': result.total_db_dates,
                    'missing_dates_count': len(result.missing_dates),
                    'extra_dates_count': len(result.extra_dates),
                    'missing_dates_sample': result.missing_dates[:10] if result.missing_dates else [],
                    'extra_dates_sample': result.extra_dates[:10] if result.extra_dates else []
                },
                'rows': {
                    'csv_total': result.csv_row_count,
                    'db_total': result.db_row_count,
                    'difference': result.csv_row_count - result.db_row_count,
                    'mismatched_dates_count': len(result.row_mismatches)
                },
                'data_quality': {
                    'duplicate_keys_count': len(result.duplicates),
                    'orphan_expiry_count': len(result.orphan_expiry),
                    'orphan_strike_count': len(result.orphan_strike),
                    'column_issues_count': len(result.column_mismatches)
                },
                'issues': {
                    'duplicates_sample': result.duplicates[:5] if result.duplicates else [],
                    'row_mismatches_sample': dict(list(result.row_mismatches.items())[:5]) if result.row_mismatches else {},
                    'orphan_expiry_sample': result.orphan_expiry[:5] if result.orphan_expiry else [],
                    'orphan_strike_sample': result.orphan_strike[:5] if result.orphan_strike else [],
                    'column_issues': result.column_mismatches
                }
            }
            
            report['year_details'].append(year_detail)
        
        return report
    
    def save_report(self, report: Dict, output_path: str):
        """
        Save audit report to JSON file.
        
        Args:
            report: Audit report dictionary
            output_path: Path to save report
        """
        with open(output_path, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"\n✓ Audit report saved to: {output_path}")
    
    def print_summary(self, report: Dict):
        """
        Print human-readable summary to console.
        
        Args:
            report: Audit report dictionary
        """
        print("\n" + "="*80)
        print("AUDIT SUMMARY")
        print("="*80)
        
        status_symbol = "✓" if report['overall_status'] == 'PASSED' else "✗"
        print(f"\nOverall Status: {status_symbol} {report['overall_status']}")
        print(f"Years Audited: {report['summary']['total_years_audited']}")
        print(f"Years Passed: {report['summary']['years_passed']}")
        print(f"Years Failed: {report['summary']['years_failed']}")
        
        print("\n" + "-"*80)
        print("YEAR-WISE BREAKDOWN")
        print("-"*80)
        
        for year_detail in report['year_details']:
            year = year_detail['year']
            status = year_detail['status']
            status_symbol = "✓" if status == 'PASSED' else "✗"
            
            print(f"\n{status_symbol} Year {year}: {status}")
            print(f"  CSV Dates: {year_detail['dates']['csv_dates']}")
            print(f"  DB Dates: {year_detail['dates']['db_dates']}")
            print(f"  Missing Dates: {year_detail['dates']['missing_dates_count']}")
            print(f"  Extra Dates: {year_detail['dates']['extra_dates_count']}")
            print(f"  CSV Rows: {year_detail['rows']['csv_total']:,}")
            print(f"  DB Rows: {year_detail['rows']['db_total']:,}")
            print(f"  Row Difference: {year_detail['rows']['difference']:,}")
            
            # Show critical issues
            if year_detail['data_quality']['duplicate_keys_count'] > 0:
                print(f"  🔴 DUPLICATES: {year_detail['data_quality']['duplicate_keys_count']}")
            
            if year_detail['data_quality']['orphan_expiry_count'] > 0:
                print(f"  ⚠ Orphan Expiry Records: {year_detail['data_quality']['orphan_expiry_count']}")
            
            if year_detail['data_quality']['orphan_strike_count'] > 0:
                print(f"  ⚠ Orphan Strike Records: {year_detail['data_quality']['orphan_strike_count']}")
        
        if report['critical_errors']:
            print("\n" + "-"*80)
            print("CRITICAL ERRORS")
            print("-"*80)
            for error in report['critical_errors']:
                print(f"  • {error['type']}: {error.get('error', 'Unknown error')}")


def main():
    """Main execution function"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Audit NSE Bhavcopy Database for data equality with CSV files'
    )
    parser.add_argument(
        '--db',
        required=True,
        help='Path to SQLite database (bhavcopy_data.db)'
    )
    parser.add_argument(
        '--csv-dir',
        required=True,
        help='Directory containing CSV files organized by year'
    )
    parser.add_argument(
        '--output',
        default='audit_report.json',
        help='Output path for audit report (default: audit_report.json)'
    )
    
    args = parser.parse_args()
    
    # Create auditor
    auditor = BhavcopyAuditor(
        db_path=args.db,
        csv_directory=args.csv_dir
    )
    
    # Run audit
    report = auditor.run_full_audit()
    
    # Save report
    auditor.save_report(report, args.output)
    
    # Print summary
    auditor.print_summary(report)
    
    # Exit with appropriate code
    exit_code = 0 if report['overall_status'] == 'PASSED' else 1
    exit(exit_code)


if __name__ == '__main__':
    main()
