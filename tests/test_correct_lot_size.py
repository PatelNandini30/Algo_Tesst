"""
Test to verify correct lot size handling
Frontend should send lots=1 (meaning 1 lot), not lots=75 (meaning 75 lots)
Backend automatically multiplies by lot size (50 for NIFTY in 2020)
"""
import sys
sys.path.append('backend')

from engines.generic_algotest_engine import run_algotest_backtest

print("="*70)
print("TEST 1: INCORRECT - Frontend sends lots=75")
print("="*70)

# WRONG: Frontend sends 75 lots
params_wrong = {
    'index': 'NIFTY',
    'from_date': '2020-01-01',
    'to_date': '2020-01-31',
    'expiry_type': 'WEEKLY',
    'entry_dte': 2,
    'exit_dte': 0,
    'legs': [
        {
            'segment': 'OPTIONS',
            'option_type': 'CE',
            'position': 'SELL',
            'lots': 75,  # WRONG! This will be multiplied by 50 = 3750 units
            'strike_selection': 'ATM',
            'expiry': 'WEEKLY'
        }
    ]
}

df_wrong, summary_wrong, _ = run_algotest_backtest(params_wrong)

print(f"\n{'='*70}")
print("RESULTS WITH lots=75 (WRONG)")
print(f"{'='*70}")
if not df_wrong.empty:
    print(f"Trade 1 P&L: {df_wrong.iloc[0]['total_pnl']:,.2f}")
    print(f"Total P&L: {summary_wrong.get('total_pnl', 0):,.2f}")
    print(f"Calculation: (Entry - Exit) × 75 lots × 50 lot_size = (Entry - Exit) × 3750 units")
else:
    print("No trades")

print(f"\n{'='*70}")
print("TEST 2: CORRECT - Frontend sends lots=1")
print(f"{'='*70}")

# CORRECT: Frontend sends 1 lot
params_correct = {
    'index': 'NIFTY',
    'from_date': '2020-01-01',
    'to_date': '2020-01-31',
    'expiry_type': 'WEEKLY',
    'entry_dte': 2,
    'exit_dte': 0,
    'legs': [
        {
            'segment': 'OPTIONS',
            'option_type': 'CE',
            'position': 'SELL',
            'lots': 1,  # CORRECT! This will be multiplied by 50 = 50 units
            'strike_selection': 'ATM',
            'expiry': 'WEEKLY'
        }
    ]
}

df_correct, summary_correct, _ = run_algotest_backtest(params_correct)

print(f"\n{'='*70}")
print("RESULTS WITH lots=1 (CORRECT)")
print(f"{'='*70}")
if not df_correct.empty:
    print(f"Trade 1 P&L: {df_correct.iloc[0]['total_pnl']:,.2f}")
    print(f"Total P&L: {summary_correct.get('total_pnl', 0):,.2f}")
    print(f"Calculation: (Entry - Exit) × 1 lot × 50 lot_size = (Entry - Exit) × 50 units")
else:
    print("No trades")

print(f"\n{'='*70}")
print("COMPARISON")
print(f"{'='*70}")
if not df_wrong.empty and not df_correct.empty:
    wrong_pnl = df_wrong.iloc[0]['total_pnl']
    correct_pnl = df_correct.iloc[0]['total_pnl']
    ratio = wrong_pnl / correct_pnl if correct_pnl != 0 else 0
    print(f"Wrong P&L (lots=75): {wrong_pnl:,.2f}")
    print(f"Correct P&L (lots=1): {correct_pnl:,.2f}")
    print(f"Ratio: {ratio:.2f}x (should be 75x)")
    print(f"\nThe wrong calculation is {ratio:.0f} times larger!")
    print(f"\nFRONTEND FIX REQUIRED:")
    print(f"  - Change 'Total Lot' field to send 1 when user selects 1 lot")
    print(f"  - Backend will automatically multiply by lot size (50 for NIFTY)")
    print(f"  - User sees '1 lot' in UI, backend calculates with 50 units")
