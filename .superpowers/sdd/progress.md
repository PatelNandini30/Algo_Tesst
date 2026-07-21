# Lot-quantity scaling — progress ledger

Plan: docs/superpowers/plans/2026-07-21-lot-quantity-scaling.md
Branch: feat/lot-quantity-scaling (off e38f5f51)
MERGE_BASE for final review: e38f5f51

Task 1: code+test done (eda584ab, 9c6cbd67) — test RED as expected (native not yet rebuilt).
  Plan defects found & fixed: simulate_trades_batch returns FLAT list not tuple;
  synthetic specs can't price (cold Rust cache) -> test now uses parity snapshot.
  Real priced rows confirmed (net_pnl -91.7 at both 1 and 2 lots).
Task 1: COMPLETE (commits eda584ab..9c6cbd67, review clean — spec OK, no Critical/Important).
  Minor carried to final review: test helper duplication with test_simulate_rust.py.
Task 5: done (902dbc09) — charges recalc scales by lots; 2/2 pass, RED verified via stash-and-rerun.
Task 2: done (e6d20d35) — 3 builders scaled, 3/3 pass.
  GAP FOUND: 3 further sites overwrite net_pnl unscaled AFTER the builders:
  engine_rust.py:6806 (SLB override, LIVE), :6842 (same-day settlement), :3810 (mixed, gated).
  -> Task 2b dispatched to fix. Plan's 3-site list was incomplete.
Task 4: done (b171c0e5) — 8 sites scaled, RED->GREEN via stash/pop on real data.
  Step 8 audit: options branch :5673-5686 has NO recompute fallback (unconditional
  reads of already-scaled pnl) -> correctly left unchanged. No lots^2.
  Plan corrections: :1643 dict is lazy_leg_config (not leg_config), reused local
  lots at :1595; test needs pd.Timestamp not str (_recalc_leg_pnl calls .strftime).
Task 3: done (005c59e3) — multi_leg P&L scaled + Qty fixed to lots*lot_size.
  RED (2 != 130) -> GREEN. Qty 130/65, Net P&L 60/30 for 2-lot/1-lot legs.
Task 3: REVERTED — generic_multi_leg.py is DEAD CODE. run_generic_multi_leg's only
  caller is worker/tasks.py:41 run_backtest_task, which nothing ever enqueues
  (appears only in worker/celery.py:51 routing table; live path is run_algotest_job).
  Revert commit removes the change + test_lot_quantity_multileg.py.
  PLAN DEFECT: reachability was never checked before including it.
Task 4: REVERTED (user decision) — generic_algotest_engine.py is the PARITY REFERENCE,
  not a live path. run_algotest_backtest's only caller is tests/parity/compare.py:84.
  POST /algotest routes to execute_algotest_job (Rust), NOT run_algotest_backtest.
  PLAN DEFECT: "reachable via /algotest endpoint" was wrong.
  KNOWN CONSEQUENCE: parity harness would diverge if ever run at lots>1.

REMAINING LIVE SCOPE: Task 1 (simulate.rs), Task 2 (engine_rust.py builders),
  Task 2b (engine_rust.py override sites), Task 5 (routers/backtest.py charges).

Task 2c: done (a8944b14) — futures re-entry _re_pnl scaled + lots-derivation hardened.
Task 6 GATE: ALL PASS on real data (post-rebuild).
  - unit suites 11/11
  - three-way parity gate (5 real backtests, 1 lot): PASS -> no regression from any edit/revert
  - NIFTY straddle Q1-2024: 26 trade-legs, dates/prices/MAE/MFE IDENTICAL, NetP&L & %P&L exactly 2x, Qty 65->130
  - 2x1 ratio spread: confirms x lots, NOT lots^2
  - charges enabled: 13 rows, 2x correct
  - multi-index Q1-2025 (NIFTY x3 + MIDCPNIFTY x2): 13 NIFTY rows x3 Qty 65->195;
    3 MIDCPNIFTY rows x2 Qty 120->240 (date-versioned lot size correct)
  NOTE: multi-index OOMs if run in same process as other backtests (16GB box) - run isolated.
