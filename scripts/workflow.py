#!/usr/bin/env python3
"""
NSE Bhavcopy Complete Workflow
===============================
One-command solution to build, validate, and audit NSE bhavcopy database.

Usage:
    python workflow.py --csv-dir ./csv_data --db bhavcopy_data.db
    
Author: Senior Data Engineering Team
Version: 1.0.0
"""

import sys
import os
import subprocess
from pathlib import Path
import argparse
from datetime import datetime


class BhavcopyWorkflow:
    """Complete workflow orchestrator"""
    
    def __init__(self, db_path: str, csv_dir: str, force: bool = False):
        """
        Initialize workflow.
        
        Args:
            db_path: Path to SQLite database
            csv_dir: Directory containing CSV files
            force: Force recreate database
        """
        self.db_path = db_path
        self.csv_dir = csv_dir
        self.force = force
        self.results = {
            'started_at': datetime.now().isoformat(),
            'steps': []
        }
    
    def log_step(self, step_name: str, status: str, details: str = ""):
        """Log workflow step"""
        self.results['steps'].append({
            'step': step_name,
            'status': status,
            'details': details,
            'timestamp': datetime.now().isoformat()
        })
        
        status_symbol = "✓" if status == "SUCCESS" else "✗"
        print(f"\n{status_symbol} {step_name}: {status}")
        if details:
            print(f"  {details}")
    
    def check_dependencies(self) -> bool:
        """Check if required files and dependencies exist"""
        print("\n" + "="*80)
        print("STEP 1: CHECKING DEPENDENCIES")
        print("="*80)
        
        required_files = [
            'bhavcopy_db_builder.py',
            'bhavcopy_audit.py',
            'remediation_planner.py'
        ]
        
        missing_files = []
        for file in required_files:
            if not os.path.exists(file):
                missing_files.append(file)
                print(f"  ✗ Missing: {file}")
            else:
                print(f"  ✓ Found: {file}")
        
        if missing_files:
            self.log_step("Check Dependencies", "FAILED", 
                         f"Missing files: {', '.join(missing_files)}")
            return False
        
        # Check Python modules
        try:
            import pandas
            import numpy
            print(f"  ✓ pandas version: {pandas.__version__}")
            print(f"  ✓ numpy version: {numpy.__version__}")
        except ImportError as e:
            self.log_step("Check Dependencies", "FAILED", 
                         f"Missing Python module: {str(e)}")
            print(f"\n  Please install: pip install pandas numpy --break-system-packages")
            return False
        
        # Check CSV directory
        if not os.path.exists(self.csv_dir):
            self.log_step("Check Dependencies", "FAILED", 
                         f"CSV directory not found: {self.csv_dir}")
            return False
        
        print(f"  ✓ CSV directory: {self.csv_dir}")
        
        self.log_step("Check Dependencies", "SUCCESS")
        return True
    
    def create_database(self) -> bool:
        """Create database schema"""
        print("\n" + "="*80)
        print("STEP 2: CREATING DATABASE")
        print("="*80)
        
        cmd = [
            'python3', 'bhavcopy_db_builder.py',
            '--db', self.db_path,
            '--csv-dir', self.csv_dir,
            '--create'
        ]
        
        if self.force:
            cmd.append('--force')
            print("  ⚠️  Force mode enabled - existing database will be deleted!")
        
        try:
            result = subprocess.run(cmd, check=True, capture_output=False)
            self.log_step("Create Database", "SUCCESS")
            return True
        except subprocess.CalledProcessError as e:
            self.log_step("Create Database", "FAILED", str(e))
            return False
    
    def ingest_data(self) -> bool:
        """Ingest CSV files"""
        print("\n" + "="*80)
        print("STEP 3: INGESTING CSV DATA")
        print("="*80)
        
        # This is already done in create_database step
        # Just log it for clarity
        self.log_step("Ingest CSV Data", "SUCCESS", "Included in database creation")
        return True
    
    def build_auxiliary_tables(self) -> bool:
        """Build auxiliary tables"""
        print("\n" + "="*80)
        print("STEP 4: BUILDING AUXILIARY TABLES")
        print("="*80)
        
        cmd = [
            'python3', 'bhavcopy_db_builder.py',
            '--db', self.db_path,
            '--build-aux-tables'
        ]
        
        try:
            result = subprocess.run(cmd, check=True, capture_output=False)
            self.log_step("Build Auxiliary Tables", "SUCCESS")
            return True
        except subprocess.CalledProcessError as e:
            self.log_step("Build Auxiliary Tables", "FAILED", str(e))
            return False
    
    def run_audit(self) -> bool:
        """Run comprehensive audit"""
        print("\n" + "="*80)
        print("STEP 5: RUNNING COMPREHENSIVE AUDIT")
        print("="*80)
        
        audit_report = 'audit_report.json'
        
        cmd = [
            'python3', 'bhavcopy_audit.py',
            '--db', self.db_path,
            '--csv-dir', self.csv_dir,
            '--output', audit_report
        ]
        
        try:
            result = subprocess.run(cmd, check=False, capture_output=False)
            
            # Audit returns exit code 1 if validation fails, but that's expected
            if os.path.exists(audit_report):
                self.log_step("Run Audit", "SUCCESS", 
                             f"Report saved to: {audit_report}")
                return True
            else:
                self.log_step("Run Audit", "FAILED", "Audit report not generated")
                return False
                
        except Exception as e:
            self.log_step("Run Audit", "FAILED", str(e))
            return False
    
    def generate_remediation_plan(self) -> bool:
        """Generate remediation plan if issues found"""
        print("\n" + "="*80)
        print("STEP 6: GENERATING REMEDIATION PLAN")
        print("="*80)
        
        audit_report = 'audit_report.json'
        
        if not os.path.exists(audit_report):
            self.log_step("Generate Remediation Plan", "SKIPPED", 
                         "No audit report available")
            return True
        
        # Check if audit passed
        import json
        with open(audit_report, 'r') as f:
            audit_data = json.load(f)
        
        if audit_data.get('overall_status') == 'PASSED':
            self.log_step("Generate Remediation Plan", "SKIPPED", 
                         "Audit passed - no remediation needed")
            print("\n  🎉 Database is healthy! All validations passed.")
            return True
        
        remediation_plan = 'remediation_plan.json'
        sql_dir = './sql_scripts'
        
        cmd = [
            'python3', 'remediation_planner.py',
            '--audit-report', audit_report,
            '--output', remediation_plan,
            '--sql-dir', sql_dir
        ]
        
        try:
            result = subprocess.run(cmd, check=True, capture_output=False)
            self.log_step("Generate Remediation Plan", "SUCCESS", 
                         f"Plan saved to: {remediation_plan}")
            
            print(f"\n  ⚠️  Issues found! Review remediation plan:")
            print(f"      {remediation_plan}")
            
            return True
        except subprocess.CalledProcessError as e:
            self.log_step("Generate Remediation Plan", "FAILED", str(e))
            return False
    
    def print_summary(self):
        """Print workflow summary"""
        print("\n" + "="*80)
        print("WORKFLOW SUMMARY")
        print("="*80)
        
        success_count = sum(1 for s in self.results['steps'] if s['status'] == 'SUCCESS')
        failed_count = sum(1 for s in self.results['steps'] if s['status'] == 'FAILED')
        skipped_count = sum(1 for s in self.results['steps'] if s['status'] == 'SKIPPED')
        
        print(f"\nTotal Steps: {len(self.results['steps'])}")
        print(f"Successful: {success_count}")
        print(f"Failed: {failed_count}")
        print(f"Skipped: {skipped_count}")
        
        print("\n" + "-"*80)
        print("STEP-BY-STEP RESULTS")
        print("-"*80)
        
        for step in self.results['steps']:
            status_symbol = "✓" if step['status'] == "SUCCESS" else "✗" if step['status'] == "FAILED" else "⊙"
            print(f"{status_symbol} {step['step']}: {step['status']}")
            if step['details']:
                print(f"  {step['details']}")
        
        # Final verdict
        if failed_count > 0:
            print("\n" + "="*80)
            print("❌ WORKFLOW FAILED")
            print("="*80)
            print("Please review errors above and retry.")
        else:
            print("\n" + "="*80)
            print("✅ WORKFLOW COMPLETED SUCCESSFULLY")
            print("="*80)
            print(f"Database: {self.db_path}")
            print(f"CSV Directory: {self.csv_dir}")
    
    def run(self):
        """Execute complete workflow"""
        print("\n" + "="*80)
        print("NSE BHAVCOPY COMPLETE WORKFLOW")
        print("="*80)
        print(f"Database: {self.db_path}")
        print(f"CSV Directory: {self.csv_dir}")
        print(f"Force Mode: {self.force}")
        print(f"Started: {self.results['started_at']}")
        
        # Step 1: Check dependencies
        if not self.check_dependencies():
            self.print_summary()
            return False
        
        # Step 2: Create database
        if not self.create_database():
            self.print_summary()
            return False
        
        # Step 3: Ingest data (included in create_database)
        self.ingest_data()
        
        # Step 4: Build auxiliary tables
        if not self.build_auxiliary_tables():
            self.print_summary()
            return False
        
        # Step 5: Run audit
        self.run_audit()
        
        # Step 6: Generate remediation plan
        self.generate_remediation_plan()
        
        # Print summary
        self.print_summary()
        
        return True


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Complete workflow for NSE Bhavcopy database',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Standard workflow - create database and validate
  python workflow.py --db bhavcopy_data.db --csv-dir ./csv_data
  
  # Force recreate database (WARNING: deletes existing data)
  python workflow.py --db bhavcopy_data.db --csv-dir ./csv_data --force
  
  # Use custom CSV directory structure
  python workflow.py --db bhavcopy_data.db --csv-dir /path/to/nse/data
        """
    )
    
    parser.add_argument(
        '--db',
        required=True,
        help='Path to SQLite database file (will be created if does not exist)'
    )
    
    parser.add_argument(
        '--csv-dir',
        required=True,
        help='Directory containing CSV files (can be organized by year)'
    )
    
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force recreate database (WARNING: deletes existing data)'
    )
    
    args = parser.parse_args()
    
    # Run workflow
    workflow = BhavcopyWorkflow(
        db_path=args.db,
        csv_dir=args.csv_dir,
        force=args.force
    )
    
    success = workflow.run()
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
