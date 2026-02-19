#!/usr/bin/env python3
"""
Remediation Planner
===================
Analyzes audit results and generates actionable remediation plans.
Does NOT automatically fix data - only suggests actions.

Author: Senior Data Engineering Team
Version: 1.0.0
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Set, Tuple
from collections import defaultdict
from pathlib import Path


class RemediationPlanner:
    """
    Analyzes audit results and generates remediation plans.
    Provides specific, actionable steps to fix data integrity issues.
    """
    
    def __init__(self, audit_report_path: str):
        """
        Initialize remediation planner.
        
        Args:
            audit_report_path: Path to JSON audit report
        """
        self.audit_report_path = audit_report_path
        self.report = None
        self.remediation_plan = {
            'generated_at': datetime.now().isoformat(),
            'audit_report': audit_report_path,
            'severity': 'UNKNOWN',
            'recommended_actions': [],
            'sql_scripts': [],
            'manual_tasks': []
        }
    
    def load_report(self):
        """Load audit report from JSON file"""
        with open(self.audit_report_path, 'r') as f:
            self.report = json.load(f)
        
        print(f"✓ Loaded audit report from: {self.audit_report_path}")
        print(f"  Overall Status: {self.report['overall_status']}")
        print(f"  Years Audited: {self.report['summary']['total_years_audited']}")
    
    def analyze_and_plan(self):
        """Analyze audit report and generate remediation plan"""
        if not self.report:
            raise ValueError("No audit report loaded. Call load_report() first.")
        
        print("\n" + "="*80)
        print("ANALYZING AUDIT RESULTS")
        print("="*80)
        
        # Determine overall severity
        self._assess_severity()
        
        # Analyze each type of issue
        self._analyze_missing_dates()
        self._analyze_extra_dates()
        self._analyze_row_mismatches()
        self._analyze_duplicates()
        self._analyze_orphan_records()
        self._analyze_column_issues()
        
        # Generate summary
        self._generate_summary()
    
    def _assess_severity(self):
        """Assess overall severity of issues"""
        failed_years = self.report['summary']['years_failed']
        
        # Check for critical issues across all years
        has_duplicates = any(
            year['data_quality']['duplicate_keys_count'] > 0
            for year in self.report['year_details']
        )
        
        has_major_row_diff = any(
            abs(year['rows']['difference']) > year['rows']['csv_total'] * 0.1
            for year in self.report['year_details']
        )
        
        if has_duplicates:
            severity = 'CRITICAL'
        elif failed_years > 0 and has_major_row_diff:
            severity = 'HIGH'
        elif failed_years > 0:
            severity = 'MEDIUM'
        else:
            severity = 'LOW'
        
        self.remediation_plan['severity'] = severity
        print(f"Severity Assessment: {severity}")
    
    def _analyze_missing_dates(self):
        """Analyze missing dates and recommend actions"""
        print("\n→ Analyzing missing dates...")
        
        missing_dates_by_year = {}
        
        for year_detail in self.report['year_details']:
            year = year_detail['year']
            missing_count = year_detail['dates']['missing_dates_count']
            
            if missing_count > 0:
                missing_dates_by_year[year] = {
                    'count': missing_count,
                    'sample': year_detail['dates']['missing_dates_sample']
                }
        
        if missing_dates_by_year:
            print(f"  Found missing dates in {len(missing_dates_by_year)} year(s)")
            
            for year, info in missing_dates_by_year.items():
                action = {
                    'type': 'RE_INGEST',
                    'priority': 'HIGH',
                    'year': year,
                    'issue': f"Missing {info['count']} dates in database",
                    'description': f"Database is missing {info['count']} trading dates from CSV files for year {year}",
                    'sample_dates': info['sample'],
                    'action_required': f"Re-ingest CSV files for year {year}",
                    'command': f"python bhavcopy_db_builder.py --db bhavcopy_data.db --csv-dir ./csv_data/{year}"
                }
                
                self.remediation_plan['recommended_actions'].append(action)
        else:
            print("  ✓ No missing dates found")
    
    def _analyze_extra_dates(self):
        """Analyze extra dates (in DB but not in CSV)"""
        print("\n→ Analyzing extra dates...")
        
        extra_dates_by_year = {}
        
        for year_detail in self.report['year_details']:
            year = year_detail['year']
            extra_count = year_detail['dates']['extra_dates_count']
            
            if extra_count > 0:
                extra_dates_by_year[year] = {
                    'count': extra_count,
                    'sample': year_detail['dates']['extra_dates_sample']
                }
        
        if extra_dates_by_year:
            print(f"  ⚠ Found extra dates in {len(extra_dates_by_year)} year(s)")
            
            for year, info in extra_dates_by_year.items():
                action = {
                    'type': 'INVESTIGATE',
                    'priority': 'MEDIUM',
                    'year': year,
                    'issue': f"Extra {info['count']} dates in database",
                    'description': f"Database contains {info['count']} dates not found in CSV files for year {year}",
                    'sample_dates': info['sample'],
                    'action_required': "Investigate source of extra dates. May indicate corrupted CSV data or database contamination.",
                    'sql_query': f"SELECT DISTINCT Date FROM cleaned_csvs WHERE strftime('%Y', Date) = '{year}' AND Date NOT IN (SELECT DISTINCT Date FROM csv_reference)"
                }
                
                self.remediation_plan['recommended_actions'].append(action)
        else:
            print("  ✓ No extra dates found")
    
    def _analyze_row_mismatches(self):
        """Analyze row count mismatches per date"""
        print("\n→ Analyzing row count mismatches...")
        
        total_mismatches = 0
        
        for year_detail in self.report['year_details']:
            year = year_detail['year']
            mismatch_count = year_detail['rows']['mismatched_dates_count']
            
            if mismatch_count > 0:
                total_mismatches += mismatch_count
                
                action = {
                    'type': 'RE_INGEST',
                    'priority': 'HIGH',
                    'year': year,
                    'issue': f"Row count mismatch on {mismatch_count} dates",
                    'description': f"{mismatch_count} dates have different row counts between CSV and DB for year {year}",
                    'sample_mismatches': year_detail['issues']['row_mismatches_sample'],
                    'action_required': f"Re-ingest CSV files for affected dates in year {year}",
                    'notes': "This indicates incomplete or corrupted data ingestion"
                }
                
                self.remediation_plan['recommended_actions'].append(action)
        
        if total_mismatches == 0:
            print("  ✓ No row count mismatches found")
        else:
            print(f"  ⚠ Found row mismatches on {total_mismatches} total dates")
    
    def _analyze_duplicates(self):
        """Analyze duplicate business keys - CRITICAL"""
        print("\n→ Analyzing duplicate records...")
        
        duplicate_years = []
        
        for year_detail in self.report['year_details']:
            year = year_detail['year']
            dup_count = year_detail['data_quality']['duplicate_keys_count']
            
            if dup_count > 0:
                duplicate_years.append(year)
                
                # Generate SQL script to remove duplicates
                sql_script = f"""
-- Remove duplicate records for year {year}
-- WARNING: This will delete duplicate rows, keeping only the first occurrence

DELETE FROM cleaned_csvs
WHERE id NOT IN (
    SELECT MIN(id)
    FROM cleaned_csvs
    WHERE strftime('%Y', Date) = '{year}'
    GROUP BY Date, Symbol, Instrument, ExpiryDate, StrikePrice, OptionType
);

-- Verify duplicates are removed
SELECT 
    Date, Symbol, Instrument, ExpiryDate, StrikePrice, OptionType, COUNT(*) as count
FROM cleaned_csvs
WHERE strftime('%Y', Date) = '{year}'
GROUP BY Date, Symbol, Instrument, ExpiryDate, StrikePrice, OptionType
HAVING COUNT(*) > 1;
"""
                
                action = {
                    'type': 'CRITICAL_FIX',
                    'priority': 'CRITICAL',
                    'year': year,
                    'issue': f"{dup_count} duplicate business keys",
                    'description': f"Database contains {dup_count} duplicate records violating unique constraint for year {year}",
                    'sample_duplicates': year_detail['issues']['duplicates_sample'],
                    'action_required': "Remove duplicate records immediately",
                    'sql_script_file': f'remove_duplicates_{year}.sql',
                    'warning': "BACKUP DATABASE BEFORE RUNNING THIS SCRIPT"
                }
                
                self.remediation_plan['recommended_actions'].append(action)
                self.remediation_plan['sql_scripts'].append({
                    'filename': f'remove_duplicates_{year}.sql',
                    'content': sql_script
                })
        
        if duplicate_years:
            print(f"  🔴 CRITICAL: Found duplicates in years: {duplicate_years}")
        else:
            print("  ✓ No duplicate records found")
    
    def _analyze_orphan_records(self):
        """Analyze orphan records in referential tables"""
        print("\n→ Analyzing orphan records...")
        
        orphan_issues = []
        
        for year_detail in self.report['year_details']:
            year = year_detail['year']
            
            orphan_expiry = year_detail['data_quality']['orphan_expiry_count']
            orphan_strike = year_detail['data_quality']['orphan_strike_count']
            
            if orphan_expiry > 0:
                orphan_issues.append({
                    'year': year,
                    'type': 'expiry_data',
                    'count': orphan_expiry
                })
                
                action = {
                    'type': 'BUILD_REFERENCE',
                    'priority': 'MEDIUM',
                    'year': year,
                    'issue': f"{orphan_expiry} orphan expiry records",
                    'description': f"Found {orphan_expiry} records without matching expiry_data entries",
                    'action_required': "Rebuild expiry_data table",
                    'command': "python bhavcopy_db_builder.py --db bhavcopy_data.db --build-aux-tables"
                }
                
                self.remediation_plan['recommended_actions'].append(action)
            
            if orphan_strike > 0:
                orphan_issues.append({
                    'year': year,
                    'type': 'strike_data',
                    'count': orphan_strike
                })
                
                action = {
                    'type': 'BUILD_REFERENCE',
                    'priority': 'MEDIUM',
                    'year': year,
                    'issue': f"{orphan_strike} orphan strike records",
                    'description': f"Found {orphan_strike} records without matching strike_data entries",
                    'action_required': "Rebuild strike_data table",
                    'command': "python bhavcopy_db_builder.py --db bhavcopy_data.db --build-aux-tables"
                }
                
                self.remediation_plan['recommended_actions'].append(action)
        
        if orphan_issues:
            print(f"  ⚠ Found orphan records in {len(orphan_issues)} cases")
        else:
            print("  ✓ No orphan records found")
    
    def _analyze_column_issues(self):
        """Analyze column completeness issues"""
        print("\n→ Analyzing column issues...")
        
        column_issues = []
        
        for year_detail in self.report['year_details']:
            year = year_detail['year']
            issues = year_detail['issues']['column_issues']
            
            if issues:
                column_issues.extend(issues)
                
                for issue in issues:
                    action = {
                        'type': 'INVESTIGATE',
                        'priority': 'LOW',
                        'year': year,
                        'issue': f"NULL values in column '{issue['column']}'",
                        'description': f"Column '{issue['column']}' has {issue['null_count']} NULL values in year {year}",
                        'action_required': "Investigate if NULL values are expected or indicate missing data",
                        'sql_query': f"SELECT * FROM cleaned_csvs WHERE strftime('%Y', Date) = '{year}' AND {issue['column']} IS NULL LIMIT 100"
                    }
                    
                    self.remediation_plan['recommended_actions'].append(action)
        
        if column_issues:
            print(f"  ⚠ Found issues with {len(column_issues)} columns")
        else:
            print("  ✓ No column issues found")
    
    def _generate_summary(self):
        """Generate executive summary of remediation plan"""
        action_counts = defaultdict(int)
        
        for action in self.remediation_plan['recommended_actions']:
            action_counts[action['priority']] += 1
        
        summary = {
            'total_actions': len(self.remediation_plan['recommended_actions']),
            'by_priority': dict(action_counts),
            'requires_immediate_attention': action_counts['CRITICAL'] > 0,
            'estimated_effort': self._estimate_effort()
        }
        
        self.remediation_plan['summary'] = summary
    
    def _estimate_effort(self) -> str:
        """Estimate effort required for remediation"""
        total_actions = len(self.remediation_plan['recommended_actions'])
        
        critical_count = sum(
            1 for a in self.remediation_plan['recommended_actions']
            if a['priority'] == 'CRITICAL'
        )
        
        if critical_count > 0:
            return "HIGH - Critical issues require immediate attention"
        elif total_actions > 10:
            return "MEDIUM - Multiple issues to address"
        elif total_actions > 0:
            return "LOW - Minor issues to resolve"
        else:
            return "NONE - Database is healthy"
    
    def save_plan(self, output_path: str):
        """Save remediation plan to JSON file"""
        with open(output_path, 'w') as f:
            json.dump(self.remediation_plan, f, indent=2)
        
        print(f"\n✓ Remediation plan saved to: {output_path}")
    
    def save_sql_scripts(self, output_dir: str):
        """Save SQL scripts to separate files"""
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)
        
        for script in self.remediation_plan['sql_scripts']:
            script_path = output_path / script['filename']
            with open(script_path, 'w') as f:
                f.write(script['content'])
            
            print(f"  • Saved: {script_path}")
    
    def print_plan(self):
        """Print human-readable remediation plan"""
        print("\n" + "="*80)
        print("REMEDIATION PLAN")
        print("="*80)
        
        print(f"\nSeverity: {self.remediation_plan['severity']}")
        print(f"Total Actions: {self.remediation_plan['summary']['total_actions']}")
        print(f"Estimated Effort: {self.remediation_plan['summary']['estimated_effort']}")
        
        # Group actions by priority
        by_priority = defaultdict(list)
        for action in self.remediation_plan['recommended_actions']:
            by_priority[action['priority']].append(action)
        
        for priority in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']:
            actions = by_priority.get(priority, [])
            
            if actions:
                print(f"\n{'-'*80}")
                print(f"{priority} PRIORITY ({len(actions)} actions)")
                print(f"{'-'*80}")
                
                for i, action in enumerate(actions, 1):
                    print(f"\n{i}. [{action['type']}] {action['issue']}")
                    print(f"   Year: {action.get('year', 'N/A')}")
                    print(f"   Description: {action['description']}")
                    print(f"   Action: {action['action_required']}")
                    
                    if 'command' in action:
                        print(f"   Command: {action['command']}")
                    
                    if 'warning' in action:
                        print(f"   ⚠️  WARNING: {action['warning']}")


def main():
    """Main execution function"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Generate remediation plan from audit report'
    )
    
    parser.add_argument(
        '--audit-report',
        required=True,
        help='Path to audit report JSON file'
    )
    
    parser.add_argument(
        '--output',
        default='remediation_plan.json',
        help='Output path for remediation plan (default: remediation_plan.json)'
    )
    
    parser.add_argument(
        '--sql-dir',
        default='./sql_scripts',
        help='Directory to save SQL scripts (default: ./sql_scripts)'
    )
    
    args = parser.parse_args()
    
    # Create planner
    planner = RemediationPlanner(args.audit_report)
    
    # Load and analyze
    planner.load_report()
    planner.analyze_and_plan()
    
    # Save outputs
    planner.save_plan(args.output)
    
    if planner.remediation_plan['sql_scripts']:
        print(f"\nSaving SQL scripts to: {args.sql_dir}")
        planner.save_sql_scripts(args.sql_dir)
    
    # Print plan
    planner.print_plan()
    
    print("\n" + "="*80)
    print("✓ REMEDIATION PLAN GENERATED")
    print("="*80)


if __name__ == '__main__':
    main()
