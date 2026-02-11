"""
Engine Strike Rounding Fixer - FINAL VERSION
==========================================
Fixes the /100)*100 bug across ALL engine files.

The bug:  round_half_up(spot / 100) * 100  → rounds to nearest 100
The fix:  round_half_up(spot / 50)  * 50   → rounds to nearest 50 (correct for NIFTY)

Run STEP 1 first (dry run), review output, then run STEP 2 (apply fixes).

STEP 1 - Dry run (safe, no changes):
    python fix_engines.py

STEP 2 - Apply fixes:
    python fix_engines.py --fix
"""

import os
import re
import sys
import shutil
import glob
from datetime import datetime

PROJECT_ROOT = r'E:\Algo_Test_Software'
ENGINES_DIR  = os.path.join(PROJECT_ROOT, 'backend', 'engines')
BACKUP_DIR   = os.path.join(ENGINES_DIR, '_backups')
DRY_RUN      = '--fix' not in sys.argv

SEP  = "=" * 65
SEP2 = "─" * 65

# ── STRIKE INTERVAL MAP PER INDEX ─────────────────────────────────────────────
# Used to generate the correct replacement for each index
STRIKE_INTERVALS = {
    'NIFTY':      50,
    'BANKNIFTY':  100,
    'FINNIFTY':   50,
    'MIDCPNIFTY': 25,
    'SENSEX':     100,
}
DEFAULT_INTERVAL = 50  # fallback


def get_interval_for_file(content: str) -> int:
    """Infer the index from file content to pick correct strike interval"""
    for index, interval in STRIKE_INTERVALS.items():
        if f'"{index}"' in content or f"'{index}'" in content:
            return interval
    return DEFAULT_INTERVAL


def fix_line(line: str, interval: int) -> tuple[bool, str]:
    """
    Detect and fix all variants of the /100)*100 strike rounding bug.
    Returns (was_fixed, new_line)

    Handles these patterns:
      round_half_up(EXPR / 100) * 100
      round_half_up(EXPR / 100) * 100  (with spaces)
    where EXPR can be:
      - entry_spot
      - entry_spot * (1 + pct/100)
      - entry_spot * (1 - pct/100)
      - any other expression
    """
    original = line

    # Pattern: round_half_up( <anything> / 100) * 100
    # We need to be careful to match the outermost division by 100
    # Strategy: find "/ 100) * 100" and replace with "/ 50) * 50"
    # BUT only when it's the strike-level rounding (not inner /100 for percentage)

    # The inner percentage calc looks like: params.get("x", 0.0)/100
    # The outer strike rounding looks like: / 100) * 100  at the end

    # Match: ) / 100) * 100  — the outer division
    # This pattern catches:  round_half_up((spot * (1 + pct/100)) / 100) * 100
    #                                                              ^^^^^^^^^^^^^^^^^

    pattern = re.compile(
        r'(round_half_up\()'          # group 1: function open
        r'(.*?)'                       # group 2: inner expression (non-greedy)
        r'(\s*/\s*100\s*\)\s*\*\s*100)'  # group 3: the bad rounding
    )

    def replacer(m):
        return f'{m.group(1)}{m.group(2)} / {interval}) * {interval}'

    new_line = pattern.sub(replacer, line)

    if new_line != original:
        return True, new_line
    return False, line


def process_file(filepath: str, dry_run: bool) -> list:
    """
    Process one engine file.
    Returns list of (lineno, original, fixed) for all changed lines.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        content = ''.join(lines)

    interval = get_interval_for_file(content)
    changes  = []

    new_lines = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('#'):
            new_lines.append(line)
            continue

        was_fixed, new_line = fix_line(line, interval)
        if was_fixed:
            changes.append((i, line.rstrip(), new_line.rstrip()))
        new_lines.append(new_line)

    if changes and not dry_run:
        # Backup
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts     = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup = os.path.join(BACKUP_DIR, f"{os.path.basename(filepath)}.{ts}.bak")
        shutil.copy2(filepath, backup)

        # Write fixed file
        with open(filepath, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

    return changes, interval


# ── MAIN ──────────────────────────────────────────────────────────────────────
print(SEP)
mode = "DRY RUN (no files changed)" if DRY_RUN else "APPLYING FIXES"
print(f"  ENGINE STRIKE ROUNDING FIXER — {mode}")
print(SEP)

engine_files = sorted(glob.glob(os.path.join(ENGINES_DIR, 'v*.py')))
print(f"\nEngine files found: {len(engine_files)}")
print(f"Engines directory : {ENGINES_DIR}\n")

total_changes = 0
files_changed = 0

for ef in engine_files:
    fname   = os.path.basename(ef)
    changes, interval = process_file(ef, dry_run=DRY_RUN)

    if changes:
        files_changed += 1
        total_changes += len(changes)
        status = "FIXED ✅" if not DRY_RUN else "NEEDS FIX 🔴"
        print(f"{SEP2}")
        print(f"  {fname}  [{status}]  (interval={interval})")
        print(f"{SEP2}")
        for lineno, orig, fixed in changes:
            print(f"\n  Line {lineno}:")
            print(f"  BEFORE: {orig.strip()}")
            print(f"  AFTER : {fixed.strip()}")
    else:
        print(f"  ✅  {fname}  — no issues found")

# ── FINAL SUMMARY ─────────────────────────────────────────────────────────────
print(f"\n{SEP}")
print(f"  SUMMARY")
print(SEP)
print(f"  Files scanned : {len(engine_files)}")
print(f"  Files with bugs: {files_changed}")
print(f"  Total bad lines: {total_changes}")

if DRY_RUN and total_changes > 0:
    print(f"""
  ── HOW TO APPLY FIXES ────────────────────────────────────
  Review the changes above, then run:

      python fix_engines.py --fix

  Backups will be saved to:
      {BACKUP_DIR}
  ──────────────────────────────────────────────────────────
""")
elif not DRY_RUN and total_changes > 0:
    print(f"""
  ✅  All fixes applied!
  Backups saved to: {BACKUP_DIR}

  Next step: restart your backend server and run the backtest again.
  You should now see trades being generated.
""")
elif total_changes == 0:
    print("""
  No automatic fixes needed — or the pattern was not matched.
  If you're still seeing "No trades", please share the full
  content of the failing engine file for manual inspection.
""")

# ── VERIFY: show all remaining strike calculations ────────────────────────────
print(f"{SEP}")
print("  ALL STRIKE CALCULATIONS AFTER FIX (verify manually)")
print(SEP)

for ef in engine_files:
    fname = os.path.basename(ef)
    with open(ef, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    strike_lines = [
        (i, l.rstrip()) for i, l in enumerate(lines, 1)
        if re.search(r'strike\s*=|atm_strike|round_half_up', l, re.IGNORECASE)
        and not l.strip().startswith('#')
    ]
    if strike_lines:
        print(f"\n  [{fname}]")
        for lineno, l in strike_lines:
            marker = "  ⚠️ " if "/100)*100" in l.replace(" ", "") else "  ✅ "
            print(f"{marker}  line {lineno}: {l.strip()}")