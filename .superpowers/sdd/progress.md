# Lot-quantity scaling — progress ledger

Plan: docs/superpowers/plans/2026-07-21-lot-quantity-scaling.md
Branch: feat/lot-quantity-scaling (off e38f5f51)
MERGE_BASE for final review: e38f5f51

Task 1: code+test done (eda584ab, 9c6cbd67) — test RED as expected (native not yet rebuilt).
  Plan defects found & fixed: simulate_trades_batch returns FLAT list not tuple;
  synthetic specs can't price (cold Rust cache) -> test now uses parity snapshot.
  Real priced rows confirmed (net_pnl -91.7 at both 1 and 2 lots). Review pending.
