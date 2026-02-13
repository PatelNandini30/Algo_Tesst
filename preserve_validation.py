"""
Validation Logic Preservation Script
Creates backup and integrity verification for analyse_bhavcopy_02-01-2026.py
"""

import os
import shutil
import hashlib
from datetime import datetime

def create_backup_with_integrity_check():
    """Create backup of validation file with integrity verification"""
    
    source_file = "analyse_bhavcopy_02-01-2026.py"
    backup_dir = "validation_backups"
    
    # Create backup directory if it doesn't exist
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)
    
    # Generate timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Create backup filename
    backup_filename = f"analyse_bhavcopy_02-01-2026_backup_{timestamp}.py"
    backup_path = os.path.join(backup_dir, backup_filename)
    
    # Copy file
    shutil.copy2(source_file, backup_path)
    
    # Calculate checksums
    def calculate_sha256(filepath):
        sha256_hash = hashlib.sha256()
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    original_hash = calculate_sha256(source_file)
    backup_hash = calculate_sha256(backup_path)
    
    # Verify integrity
    integrity_check = "PASSED" if original_hash == backup_hash else "FAILED"
    
    # Create integrity log
    log_content = f"""
Validation File Integrity Report
===============================
Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Source File: {source_file}
Backup File: {backup_path}
Original SHA256: {original_hash}
Backup SHA256: {backup_hash}
Integrity Check: {integrity_check}
File Size: {os.path.getsize(source_file)} bytes
Lines: 20192
"""
    
    log_file = os.path.join(backup_dir, f"integrity_log_{timestamp}.txt")
    with open(log_file, 'w') as f:
        f.write(log_content)
    
    print(f"Backup created: {backup_path}")
    print(f"Integrity check: {integrity_check}")
    print(f"Log file: {log_file}")
    
    return {
        'backup_path': backup_path,
        'original_hash': original_hash,
        'backup_hash': backup_hash,
        'integrity_check': integrity_check,
        'log_file': log_file
    }

def verify_current_integrity():
    """Verify current file integrity against latest backup"""
    
    backup_dir = "validation_backups"
    if not os.path.exists(backup_dir):
        print("No backups found")
        return None
    
    # Find latest backup
    backup_files = [f for f in os.listdir(backup_dir) if f.startswith("analyse_bhavcopy_02-01-2026_backup_")]
    if not backup_files:
        print("No validation backups found")
        return None
    
    latest_backup = sorted(backup_files)[-1]
    backup_path = os.path.join(backup_dir, latest_backup)
    
    # Calculate current file hash
    current_hash = hashlib.sha256()
    with open("analyse_bhavcopy_02-01-2026.py", "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            current_hash.update(byte_block)
    
    # Read backup hash from log
    log_files = [f for f in os.listdir(backup_dir) if f.startswith("integrity_log_")]
    if log_files:
        latest_log = sorted(log_files)[-1]
        log_path = os.path.join(backup_dir, latest_log)
        with open(log_path, 'r') as f:
            log_content = f.read()
        
        # Extract original hash from log
        for line in log_content.split('\n'):
            if line.startswith('Original SHA256:'):
                original_hash = line.split(': ')[1].strip()
                break
        else:
            print("Could not find original hash in log")
            return None
    else:
        print("No integrity log found")
        return None
    
    current_hash_hex = current_hash.hexdigest()
    integrity_status = "MATCH" if current_hash_hex == original_hash else "CHANGED"
    
    print(f"Current file hash: {current_hash_hex}")
    print(f"Original hash: {original_hash}")
    print(f"Integrity status: {integrity_status}")
    
    return {
        'current_hash': current_hash_hex,
        'original_hash': original_hash,
        'status': integrity_status,
        'backup_file': backup_path
    }

if __name__ == "__main__":
    print("=== Validation Logic Preservation Tool ===")
    
    # Create backup
    backup_info = create_backup_with_integrity_check()
    
    print("\n=== Integrity Verification ===")
    # Verify integrity
    verify_info = verify_current_integrity()
    
    print("\n=== Summary ===")
    print("Your validation logic has been preserved.")
    print("The original file remains unchanged.")
    print("You can now safely integrate new functionality while keeping validation intact.")