"""Lot-quantity scaling: Net P&L = points x lots, applied per leg.

lot_size is NOT part of P&L - it feeds only the display Qty column.
At lots=1 every value must be byte-identical to the pre-change engine.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


import json
from pathlib import Path

import pandas as pd


class TestRustNetPnlScalesWithLots(unittest.TestCase):
    """Rust net_pnl scales by the leg's own lots; prices must not.

    Reuses the captured parity snapshot + its cache-warming helper, the same
    way tests/test_simulate_rust.py does. A synthetic spec cannot work here:
    simulate_one prices against the in-process Rust cache, which is cold in a
    bare unittest process, so every row comes back `missing`.
    """

    def setUp(self):
        try:
            import algotest_native  # type: ignore
            self.native = algotest_native
        except ImportError:
            self.skipTest("algotest_native not installed in this environment")

        from tests.test_simulate_rust import _bulk_load_for_snapshot, _trade_to_spec

        snap_path = (
            Path(__file__).parent / "parity" / "snapshots" / "single_leg_ce_atm_sell.json"
        )
        if not snap_path.exists():
            self.skipTest("snapshot single_leg_ce_atm_sell not captured yet")

        snap = json.loads(snap_path.read_text())
        self.payload = snap["payload"]
        self.trades = snap["trades"]
        self.assertGreater(len(self.trades), 0)

        try:
            _bulk_load_for_snapshot(self.payload)
        except Exception as exc:
            self.skipTest(f"could not load market data: {exc}")
        self._to_spec = _trade_to_spec

    def _run(self, lots: int) -> list:
        """Price the whole snapshot with every leg forced to `lots`."""
        specs = []
        for t in self.trades:
            spec = self._to_spec(t, self.payload)
            spec["lots"] = lots
            specs.append(spec)
        # simulate_trades_batch returns a FLAT list (the (results, bad_trades)
        # tuple belongs to the private _core fn, not the PyO3 wrapper).
        return list(self.native.simulate_trades_batch(specs))

    def _paired(self):
        one, two = self._run(1), self._run(2)
        pairs = [(a, b) for a, b in zip(one, two) if not a["missing"]]
        self.assertGreater(len(pairs), 0, "snapshot produced no priced rows")
        return pairs

    def test_net_pnl_doubles_when_lots_doubles(self):
        for a, b in self._paired():
            self.assertAlmostEqual(b["net_pnl"], a["net_pnl"] * 2, places=2)

    def test_prices_do_not_scale(self):
        for a, b in self._paired():
            self.assertEqual(b["entry_price"], a["entry_price"])
            self.assertEqual(b["exit_price"], a["exit_price"])


class TestTradesheetRecordsScale(unittest.TestCase):
    """priced_to_tradesheet_records must scale per-leg P&L by that leg's lots."""

    def _rows(self, lots_leg1: int, lots_leg2: int) -> list:
        return [
            {
                "trade_id": "1", "leg_id": 1, "index": "NIFTY",
                "entry_date": "2024-01-01", "exit_date": "2024-01-04",
                "expiry": "2024-01-04", "option_type": "CE", "strike": 21500.0,
                "position": "SELL", "entry_price": 150.0, "exit_price": 90.0,
                "entry_spot": 21500.0, "exit_spot": 21600.0,
                "lots": lots_leg1, "lot_size": 65, "net_pnl": 60.0 * lots_leg1,
            },
            {
                "trade_id": "1", "leg_id": 2, "index": "NIFTY",
                "entry_date": "2024-01-01", "exit_date": "2024-01-04",
                "expiry": "2024-01-04", "option_type": "PE", "strike": 21500.0,
                "position": "SELL", "entry_price": 130.0, "exit_price": 145.0,
                "entry_spot": 21500.0, "exit_spot": 21600.0,
                "lots": lots_leg2, "lot_size": 65, "net_pnl": -15.0 * lots_leg2,
            },
        ]

    def test_per_leg_pnl_scales_by_that_legs_lots(self):
        from services.engine_rust import priced_to_tradesheet_records

        recs = priced_to_tradesheet_records(self._rows(2, 1), {"index": "NIFTY"}, 65)

        ce = next(r for r in recs if r["Type"] == "CE")
        pe = next(r for r in recs if r["Type"] == "PE")

        # CE: (150 - 90) x 2 lots = 120 ; PE: (130 - 145) x 1 lot = -15
        self.assertAlmostEqual(ce["CE P&L"], 120.0, places=4)
        self.assertAlmostEqual(pe["PE P&L"], -15.0, places=4)

    def test_qty_is_lots_times_lot_size(self):
        from services.engine_rust import priced_to_tradesheet_records

        recs = priced_to_tradesheet_records(self._rows(2, 1), {"index": "NIFTY"}, 65)

        ce = next(r for r in recs if r["Type"] == "CE")
        pe = next(r for r in recs if r["Type"] == "PE")
        self.assertEqual(ce["Qty"], 130)   # 2 lots x 65
        self.assertEqual(pe["Qty"], 65)    # 1 lot  x 65

    def test_prices_stay_per_unit(self):
        from services.engine_rust import priced_to_tradesheet_records

        recs = priced_to_tradesheet_records(self._rows(2, 1), {"index": "NIFTY"}, 65)
        ce = next(r for r in recs if r["Type"] == "CE")
        self.assertEqual(ce["Entry Price"], 150.0)
        self.assertEqual(ce["Exit Price"], 90.0)


class _EngineRustPipelineFixtureMixin:
    """Shared NIFTY 2024-Q1 market-data fixture for full-pipeline tests.

    Mirrors the setup in tests/test_engine_rust_pipeline.py — the SL-with-buffer
    override and the same-day-expiry settlement fix both live deep inside
    run_rust_engine_pipeline (post-processing passes over `final_priced`, right
    before the function returns), so there is no way to exercise them without
    driving the real pipeline against real priced data.
    """

    @classmethod
    def setUpClass(cls):
        try:
            import algotest_native  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("algotest_native not installed")
        try:
            from base import bulk_load_options
            from services.algotest_job import _build_fast_lookup_from_bulk
            bulk_load_options("NIFTY", "2024-01-01", "2024-03-31")
            _build_fast_lookup_from_bulk("NIFTY", "2024-01-01", "2024-03-31")
        except Exception as exc:
            raise unittest.SkipTest(f"could not load market data: {exc}")

        from tests.test_engine_rust_pipeline import _trading_days, _expiries, _spots
        cls._days = _trading_days("2024-01-01", "2024-03-31")
        cls._expiries = _expiries("NIFTY", "weekly", "2024-01-01", "2024-03-31")
        cls._spots = _spots("NIFTY", cls._days)

    def _run_pipeline(self, payload, lots):
        from services.engine_rust import run_rust_engine_pipeline
        p = dict(payload)
        p["legs"] = [dict(payload["legs"][0])]
        p["legs"][0]["lots"] = lots
        return run_rust_engine_pipeline(
            p,
            expiry_dates=self._expiries,
            trading_days=self._days,
            lot_size=65,
            spot_by_date=self._spots,
            square_off_mode=p.get("square_off_mode", "partial"),
        )


class TestSLBOverrideScalesWithLots(_EngineRustPipelineFixtureMixin, unittest.TestCase):
    """SL-with-Buffer post-process override (engine_rust.py ~:6806).

    This site recomputes net_pnl from the buffer-adjusted exit price AFTER
    simulate_trades_batch has already priced the trade, so it must apply the
    row's own lots itself — it doesn't inherit scaling from anywhere upstream.
    """

    def test_slb_override_net_pnl_scales_with_lots(self):
        snap_path = Path(__file__).parent / "parity" / "snapshots" / "with_sl_buffer.json"
        if not snap_path.exists():
            self.skipTest("snapshot with_sl_buffer not captured yet")
        payload = json.loads(snap_path.read_text())["payload"]

        one = self._run_pipeline(payload, 1)
        two = self._run_pipeline(payload, 2)
        self.assertIsNotNone(one)
        self.assertIsNotNone(two)

        def _slb_rows(rows):
            return {
                (r["entry_date"], r["exit_date"]): r
                for r in rows
                if "SL_WITH_BUFFER" in str(r.get("exit_reason") or "").upper()
            }

        slb_one, slb_two = _slb_rows(one), _slb_rows(two)
        self.assertGreater(len(slb_one), 0, "fixture produced no SL_WITH_BUFFER rows")
        self.assertEqual(set(slb_one), set(slb_two))
        for key, r1 in slb_one.items():
            r2 = slb_two[key]
            with self.subTest(trade=key):
                self.assertAlmostEqual(r2["net_pnl"], r1["net_pnl"] * 2, places=2)
                # Prices are per-unit — only net_pnl scales.
                self.assertEqual(r2["exit_price"], r1["exit_price"])
                self.assertEqual(r2["entry_price"], r1["entry_price"])


class TestSettlementFixScalesWithLots(_EngineRustPipelineFixtureMixin, unittest.TestCase):
    """Same-day-expiry settlement substitution (engine_rust.py ~:6837-6842).

    entry_dte=0 + exit_dte=0 forces entry_date == exit_date == expiry for
    every trade, which is exactly the narrow condition this fix targets. A
    high, never-firing stopLoss is attached purely to route the payload past
    the "no risk controls" early return (:4438) so this post-process pass —
    which sits right before the pipeline's final return — actually executes.
    """

    def test_settlement_fix_net_pnl_scales_with_lots(self):
        payload = {
            "index": "NIFTY", "from_date": "2024-01-01", "to_date": "2024-03-31",
            "strategy_type": "positional", "underlying": "cash",
            "expiry_window": "weekly_expiry",
            "entry_dte": 0, "exit_dte": 0, "slippage_pct": 0,
            "charges_enabled": False, "square_off_mode": "partial",
            "legs": [{
                "segment": "OPTIONS", "option_type": "CE", "position": "SELL",
                "lots": 1, "expiry": "WEEKLY", "strike_interval": 50,
                "strike_selection": {"type": "strike_type", "strike_type": "ATM"},
                "stopLoss": {"mode": "PERCENT", "value": 500},
            }],
        }

        one = self._run_pipeline(payload, 1)
        two = self._run_pipeline(payload, 2)
        self.assertIsNotNone(one)
        self.assertIsNotNone(two)

        def _same_day_rows(rows):
            return {
                (r["entry_date"], r["exit_date"]): r
                for r in rows
                if r["entry_date"] == r["exit_date"] == r["expiry"]
            }

        same_one, same_two = _same_day_rows(one), _same_day_rows(two)
        self.assertGreater(len(same_one), 0, "fixture produced no same-day-expiry rows")
        self.assertEqual(set(same_one), set(same_two))
        for key, r1 in same_one.items():
            r2 = same_two[key]
            with self.subTest(trade=key):
                # Sanity: the settlement substitution actually fired (entry and
                # exit price collide before the fix, giving a trivial 0 == 0*2).
                self.assertNotEqual(r1["net_pnl"], 0.0)
                self.assertAlmostEqual(r2["net_pnl"], r1["net_pnl"] * 2, places=2)
                self.assertEqual(r2["exit_price"], r1["exit_price"])
                self.assertEqual(r2["entry_price"], r1["entry_price"])


class TestMixedFuturesOptionsScalesWithLots(_EngineRustPipelineFixtureMixin, unittest.TestCase):
    """_build_mixed_futures_options (engine_rust.py ~:3810), gated by
    MIXED_FUT_RUST but called here directly regardless of the env var.

    Uses DIFFERENT lots on the option leg vs the futures leg so a "lots^2"
    regression (re-scaling the already-summed trade-total at :3822/:3818) is
    algebraically distinguishable from correct per-leg scaling.
    """

    def _run_mixed(self, opt_lots, fut_lots):
        payload = {
            "index": "NIFTY", "from_date": "2024-01-01", "to_date": "2024-03-31",
            "strategy_type": "positional", "underlying": "cash",
            "expiry_window": "weekly_expiry",
            "entry_dte": 4, "exit_dte": 0, "slippage_pct": 0,
            "charges_enabled": False, "square_off_mode": "partial",
            "legs": [
                {
                    "segment": "OPTIONS", "option_type": "CE", "position": "SELL",
                    "lots": opt_lots, "expiry": "WEEKLY", "strike_interval": 50,
                    "strike_selection": {"type": "strike_type", "strike_type": "ATM"},
                },
                {"segment": "FUTURES", "position": "SELL", "lots": fut_lots, "expiry": "monthly"},
            ],
        }
        from services.engine_rust import _build_mixed_futures_options
        return _build_mixed_futures_options(
            payload,
            expiry_dates=self._expiries,
            trading_days=self._days,
            lot_size=65,
            spot_by_date=self._spots,
            square_off_mode="partial",
        )

    def test_mixed_builder_scales_each_leg_by_its_own_lots(self):
        base = self._run_mixed(1, 1)
        scaled = self._run_mixed(3, 2)
        if base is None or scaled is None:
            self.skipTest("mixed futures+options build unavailable (no FUTIDX cache?)")
        self.assertGreater(len(base), 0)
        self.assertEqual(len(base), len(scaled))

        def _by_trade_leg(rows):
            return {(r["trade_id"], r["leg_id"]): r for r in rows}

        base_by = _by_trade_leg(base)
        scaled_by = _by_trade_leg(scaled)
        self.assertEqual(set(base_by), set(scaled_by))

        for tid, base_rows in __import__("itertools").groupby(
            sorted(base, key=lambda r: r["trade_id"]), key=lambda r: r["trade_id"]
        ):
            base_rows = list(base_rows)
            if len(base_rows) < 2:
                continue
            # The FUT (non-first) leg is a pure per-leg value: exactly x2.
            fut_base = next(r for r in base_rows if r["option_type"] == "FUT")
            fut_scaled = scaled_by[(fut_base["trade_id"], fut_base["leg_id"])]
            with self.subTest(trade=tid, leg="fut"):
                self.assertAlmostEqual(fut_scaled["net_pnl"], fut_base["net_pnl"] * 2, places=2)

            # The first-leg (CE) row carries the TRADE TOTAL = CE-per-leg + FUT-per-leg.
            # Recover CE-per-leg at base lots by subtracting the FUT leg we just
            # verified, then check the x3-scaled total decomposes the same way —
            # this is exactly the check that catches a lots^2 double-scale on the
            # trade-total leg (it would NOT satisfy this decomposition).
            ce_base = next(r for r in base_rows if r["option_type"] == "CE")
            ce_base_per_leg = ce_base["net_pnl"] - fut_base["net_pnl"]
            ce_scaled = scaled_by[(ce_base["trade_id"], ce_base["leg_id"])]
            expected_total = ce_base_per_leg * 3 + fut_base["net_pnl"] * 2
            with self.subTest(trade=tid, leg="ce_total"):
                self.assertAlmostEqual(ce_scaled["net_pnl"], expected_total, places=2)


class TestFuturesReentrySpecsScalesWithLots(unittest.TestCase):
    """FUTURES SL/target re-entry branch of _build_futures_specs (engine_rust.py
    ~:1546, the `_re_pnl` row).

    This path bypasses simulate_trades_batch entirely — run_rust_engine_pipeline
    returns _build_futures_specs' rows directly for a futures-only strategy — so
    if `_re_pnl` isn't scaled by the leg's own lots right there, nothing
    downstream fixes it.

    Synthetic and fully deterministic: the native futures-price helpers are
    monkeypatched to a fixed price path so the SL fires on a known day with
    trading days left before the scheduled exit — guaranteeing a re-entry row
    is produced, rather than depending on whichever real NIFTY sessions
    happened to move enough (which turns out NOT to leave room for a
    re-entry in the captured 2024-Q1 futures_with_reentry_sl snapshot).
    """

    # entry=100 SELL; day-2 price 120 is a +20% adverse move -> trips the 5% SL
    # on the very first scanned day, leaving 2024-01-03..01-09 free for a
    # re-entry before the 2024-01-10 scheduled exit.
    _PRICES = {
        "2024-01-01": 100.0,   # main entry
        "2024-01-02": 120.0,   # main SL day (adverse for SELL)
        "2024-01-03": 90.0,    # re-entry entry price
        "2024-01-04": 90.0,
        "2024-01-05": 90.0,
        "2024-01-08": 90.0,
        "2024-01-09": 90.0,
        "2024-01-10": 93.0,    # re-entry exit price (scheduled exit; no 2nd SL)
    }
    _TRADING_DAYS = [
        "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04",
        "2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10",
    ]
    _EXPIRY_DATES = ["2024-01-10"]

    def _run(self, lots: int) -> list:
        import unittest.mock as mock
        import services.engine_rust as er

        def _fake_fut_price(index, date, expiry):
            return self._PRICES.get(date)

        def _fake_resolve(entry_date, exit_date, symbol, position, preference="monthly"):
            return self._PRICES.get(entry_date), self._PRICES.get(exit_date), "2024-01-25"

        payload = {
            "index": "NIFTY", "entry_dte": 7, "exit_dte": 0,
            "legs": [{
                "segment": "FUTURES", "option_type": "FUT", "position": "SELL",
                "lots": lots, "expiry": "monthly", "fut_exit_mode": "ON_EXPIRY",
                "stopLoss": {"mode": "PERCENT", "value": 5},
                "reEntryOnSL": {"mode": "RE_ASAP", "count": 1},
            }],
        }
        spot_by_date = {d: 21000.0 for d in self._TRADING_DAYS}

        with mock.patch.object(er, "_fut_price", side_effect=_fake_fut_price), \
             mock.patch.object(er, "_resolve_futures_pnl_native", side_effect=_fake_resolve):
            return er._build_futures_specs(
                payload, self._EXPIRY_DATES, self._TRADING_DAYS, spot_by_date, 65, None,
            )

    def test_futures_reentry_net_pnl_scales_with_lots(self):
        one, two = self._run(1), self._run(2)
        self.assertIsNotNone(one)
        self.assertIsNotNone(two)

        # "_reentry_trigger" is only stamped on rows appended by the re-entry
        # while-loop, isolating exactly the branch under test.
        re_one = [r for r in one if "_reentry_trigger" in r]
        re_two = [r for r in two if "_reentry_trigger" in r]
        self.assertEqual(len(re_one), 1, "expected exactly one re-entry row")
        self.assertEqual(len(re_two), 1)

        r1, r2 = re_one[0], re_two[0]
        self.assertNotEqual(r1["net_pnl"], 0.0)
        self.assertAlmostEqual(r2["net_pnl"], r1["net_pnl"] * 2, places=4)
        # Prices are per-unit — only net_pnl scales.
        self.assertEqual(r2["entry_price"], r1["entry_price"])
        self.assertEqual(r2["exit_price"], r1["exit_price"])


class TestTradesheetRecordCarriesLots(unittest.TestCase):
    """Task 7a plumbing: priced_to_tradesheet_records must carry the row's own
    lots in a "lots" key (beside "Qty"), so downstream MAE/MFE scaling sites
    (algotest_job.py) can read it directly instead of re-deriving lots by
    inverting Qty / lot_size. Also confirms the key cannot leak into Excel
    output via excel_builder._build_key_order's explicit whitelist.
    """

    def _row(self, lots) -> dict:
        row = {
            "trade_id": "1", "leg_id": 1, "index": "NIFTY",
            "entry_date": "2024-01-01", "exit_date": "2024-01-04",
            "expiry": "2024-01-04", "option_type": "CE", "strike": 21500.0,
            "position": "SELL", "entry_price": 150.0, "exit_price": 90.0,
            "entry_spot": 21500.0, "exit_spot": 21600.0,
            "lot_size": 65, "net_pnl": 60.0 * (lots or 1),
        }
        if lots is not None:
            row["lots"] = lots
        return row

    def test_record_carries_lots_field(self):
        from services.engine_rust import priced_to_tradesheet_records

        recs = priced_to_tradesheet_records([self._row(3)], {"index": "NIFTY"}, 65)
        self.assertEqual(recs[0]["lots"], 3)

    def test_lots_defaults_to_1_when_missing(self):
        from services.engine_rust import priced_to_tradesheet_records

        recs = priced_to_tradesheet_records([self._row(None)], {"index": "NIFTY"}, 65)
        self.assertEqual(recs[0]["lots"], 1)

    def test_lots_key_cannot_leak_into_excel_key_order(self):
        from services.engine_rust import priced_to_tradesheet_records
        from services.optimizer.excel_builder import _build_key_order

        recs = priced_to_tradesheet_records([self._row(2)], {"index": "NIFTY"}, 65)
        self.assertIn("lots", recs[0])  # sanity: the key really is present

        order, _has_calls, _has_puts, _has_futures = _build_key_order(recs, has_midcap=False)
        self.assertNotIn("lots", order)


class TestBacktestJobMaeMfeScalesByLots(unittest.TestCase):
    """Task 7a: algotest_job.py's MAE/MFE write site (~:427-430) must scale
    MAE/MFE by the record's own lots so they land in the same
    leveraged-percentage unit as % P&L.

    Why this matters: native/src/summary_metrics.rs:336 compounds the NAV by
    % P&L (now lots-scaled) while :362 applies MAE to that same NAV as
    prev_cum * (1 + mae/100). Leaving MAE unscaled understates Live DD /
    Final MAE / Max DD by ~1/lots.

    Drives the real backtest path (_try_rust_engine) against real NIFTY
    Q1-2024 data, mirroring _EngineRustPipelineFixtureMixin's approach above —
    the MAE/MFE scaling lives inside algotest_job.py's job orchestration, not
    inside run_rust_engine_pipeline, so there is no lower-level seam to hook.
    """

    @classmethod
    def setUpClass(cls):
        try:
            import algotest_native  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("algotest_native not installed in this environment")

        snap_path = Path(__file__).parent / "parity" / "snapshots" / "single_leg_ce_atm_sell.json"
        if not snap_path.exists():
            raise unittest.SkipTest("snapshot single_leg_ce_atm_sell not captured yet")
        cls._base_payload = json.loads(snap_path.read_text())["payload"]
        cls._snap_trades = json.loads(snap_path.read_text())["trades"]

        try:
            from base import bulk_load_options
            from services.algotest_job import _build_fast_lookup_from_bulk
            bulk_load_options(
                "NIFTY", cls._base_payload["from_date"], cls._base_payload["to_date"]
            )
            _build_fast_lookup_from_bulk(
                "NIFTY", cls._base_payload["from_date"], cls._base_payload["to_date"]
            )
        except Exception as exc:
            raise unittest.SkipTest(f"could not load market data: {exc}")

    def _run(self, lots: int) -> list:
        import unittest.mock as mock
        from services.algotest_job import _try_rust_engine

        payload = json.loads(json.dumps(self._base_payload))  # deep copy
        payload["legs"][0]["lots"] = lots
        with mock.patch.dict(os.environ, {"BACKTEST_INCLUDE_MAE_MFE": "1"}):
            trades_df, _summary, _pivot = _try_rust_engine(
                payload, "NIFTY", payload["from_date"], payload["to_date"],
            )
        self.assertIsNotNone(trades_df, "backtest path returned no trades — check market data")
        self.assertFalse(trades_df.empty, "backtest path produced zero trades")
        return trades_df.to_dict("records")

    def test_mae_mfe_double_when_lots_double(self):
        one = self._run(1)
        two = self._run(2)
        self.assertEqual(len(one), len(two))
        for r1, r2 in zip(one, two):
            with self.subTest(trade=r1.get("Trade"), leg=r1.get("Leg")):
                self.assertAlmostEqual(float(r2["MAE"]), float(r1["MAE"]) * 2, places=3)
                self.assertAlmostEqual(float(r2["MFE"]), float(r1["MFE"]) * 2, places=3)

    def test_lots_1_mae_mfe_matches_pre_scaling_snapshot(self):
        """lots=1 must stay byte-identical to the snapshot captured before
        Task 7's scaling was applied."""
        snap_by_trade_leg = {
            (str(t["Trade"]), int(t["Leg"])): t for t in self._snap_trades
        }

        one = self._run(1)
        matched = 0
        for r in one:
            key = (str(r.get("Trade")), int(r.get("Leg")))
            snap_r = snap_by_trade_leg.get(key)
            if snap_r is None:
                continue
            matched += 1
            with self.subTest(trade=key):
                self.assertAlmostEqual(float(r["MAE"]), float(snap_r["MAE"]), places=3)
                self.assertAlmostEqual(float(r["MFE"]), float(snap_r["MFE"]), places=3)
        self.assertGreater(matched, 0, "no rows matched the captured snapshot by (Trade, Leg)")


class TestOptimizerMaeMfeScalesByLots(unittest.TestCase):
    """Task 7b: the OPTIMIZER's own MAE/MFE column-write sites in
    services/optimizer/runner.py must scale by each row's own lots, exactly
    like the backtest path (Task 7a, TestBacktestJobMaeMfeScalesByLots above):

      - site B: _compute_mae_mfe_batch's Rust-batch branch (:1281-1282),
        which writes algotest_native.compute_mae_mfe_batch's raw ratio.
      - site C: _compute_mae_mfe_batch's Python/pandas branch (:1588-1589),
        the parity-reference implementation tools/mae_parity.py diffs
        against site B row-by-row — they must scale IDENTICALLY.
      - site D: _apply_futures_mae_mfe, which OVERWRITES FUT rows with
        services.engine_rust._fut_leg_mae_mfe's raw ratio AFTER site B/C —
        it needs its own scaling or FUT rows silently lose it.

    Fully synthetic and deterministic: algotest_native.compute_mae_mfe_batch
    and _fut_leg_mae_mfe are monkeypatched to fixed RAW (unscaled) ratios, and
    the Python branch is fed a tiny in-memory OHLC frame — no market data / DB
    / native cache warm-up required. Only algotest_native's Python module
    needs to be importable (matching the other tests in this file); if it
    isn't, the whole class skips.
    """

    _ENTRY = 150.0
    _SPOT = 21500.0
    _HIGH = 160.0
    _LOW = 80.0
    _TRADING_DAYS = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]

    @classmethod
    def setUpClass(cls):
        try:
            import algotest_native  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("algotest_native not installed in this environment")
        # Raw (unscaled) ratio for a SELL leg, matching
        # _calculate_mae_mfe_from_extremes: mae=(entry-high)/spot*100,
        # mfe=(entry-low)/spot*100.
        cls._raw_mae = round((cls._ENTRY - cls._HIGH) / cls._SPOT * 100, 4)
        cls._raw_mfe = round((cls._ENTRY - cls._LOW) / cls._SPOT * 100, 4)

    def _option_row(self, lots, with_lots_key=True):
        row = {
            "Trade": "1", "Leg": 1, "Type": "CE", "Strike": 21500.0, "B/S": "SELL",
            "Entry Price": self._ENTRY, "Entry Spot": self._SPOT, "Exit Price": 90.0,
            "Entry Date": "2024-01-01", "Exit Date": "2024-01-04",
            "Expiry": "2024-01-04", "Exit Reason": "Target",
            "MAE": 0.0, "MFE": 0.0,
        }
        if with_lots_key:
            row["lots"] = lots
        return row

    def _run_site_b(self, lots, with_lots_key=True):
        """Rust-batch branch (site B) — algotest_native mocked to a fixed raw pair."""
        import unittest.mock as mock
        import algotest_native
        from services.optimizer import runner as _r

        df = pd.DataFrame([self._option_row(lots, with_lots_key)])
        raw_pairs = [(self._raw_mae, self._raw_mfe)]
        with mock.patch.dict(os.environ, {"BACKTEST_INCLUDE_MAE_MFE": "1"}), \
             mock.patch.object(algotest_native, "is_loaded", return_value=True), \
             mock.patch.object(algotest_native, "compute_mae_mfe_batch", return_value=raw_pairs), \
             mock.patch.object(_r, "_MAE_PYTHON_REF", False):
            return _r._compute_mae_mfe_batch(df, "NIFTY", self._TRADING_DAYS)

    def _run_site_c(self, lots, with_lots_key=True):
        """Python/pandas branch (site C) — fed a tiny synthetic OHLC frame."""
        import unittest.mock as mock
        from services.optimizer import runner as _r

        df = pd.DataFrame([self._option_row(lots, with_lots_key)])
        ohlc_rows = [
            {"Symbol": "NIFTY", "Date": pd.Timestamp(d),
             "ExpiryDate": pd.Timestamp("2024-01-04"),
             "OptionType": "CE", "strike_r": 21500,
             "High": self._HIGH, "Low": self._LOW}
            for d in ("2024-01-02", "2024-01-03", "2024-01-04")
        ]
        fake_ctx = {"ohlc_df_pandas": pd.DataFrame(ohlc_rows), "trading_days": self._TRADING_DAYS}
        with mock.patch.dict(os.environ, {"BACKTEST_INCLUDE_MAE_MFE": "1"}), \
             mock.patch.object(_r, "_RUST_CONTEXT", fake_ctx), \
             mock.patch.object(_r, "_MAE_PYTHON_REF", True):
            return _r._compute_mae_mfe_batch(df, "NIFTY", self._TRADING_DAYS)

    def _fut_row(self, lots, with_lots_key=True):
        row = {
            "Trade": "1", "Leg": 1, "Type": "FUT", "B/S": "SELL",
            "Entry Price": 21600.0, "Entry Spot": 21500.0, "Exit Price": 21785.95,
            "Entry Date": "2024-01-03", "Exit Date": "2024-01-04",
            "Expiry": "2024-01-25", "Exit Reason": "Expiry",
            "MAE": 0.0, "MFE": 0.0,
        }
        if with_lots_key:
            row["lots"] = lots
        return row

    def _run_site_d(self, lots, raw_mae=-0.976, raw_mfe=-0.0748, with_lots_key=True):
        """FUT overwrite (site D) — _fut_leg_mae_mfe mocked to a fixed raw pair."""
        import unittest.mock as mock
        import services.engine_rust as er
        from services.optimizer.runner import _apply_futures_mae_mfe

        df = pd.DataFrame([self._fut_row(lots, with_lots_key)])
        with mock.patch.object(er, "_fut_leg_mae_mfe", return_value=(raw_mae, raw_mfe)):
            return _apply_futures_mae_mfe(df, "NIFTY", ["2024-01-03", "2024-01-04"])

    # ── Site B: Rust batch branch ────────────────────────────────────────
    def test_site_b_rust_batch_scales_by_row_lots(self):
        one = self._run_site_b(1)
        two = self._run_site_b(2)
        self.assertAlmostEqual(float(one["MAE"].iloc[0]), self._raw_mae, places=4)
        self.assertAlmostEqual(float(one["MFE"].iloc[0]), self._raw_mfe, places=4)
        self.assertAlmostEqual(float(two["MAE"].iloc[0]), self._raw_mae * 2, places=4)
        self.assertAlmostEqual(float(two["MFE"].iloc[0]), self._raw_mfe * 2, places=4)

    # ── Site C: Python/pandas branch ─────────────────────────────────────
    def test_site_c_python_path_scales_by_row_lots(self):
        one = self._run_site_c(1)
        two = self._run_site_c(2)
        self.assertAlmostEqual(float(one["MAE"].iloc[0]), self._raw_mae, places=4)
        self.assertAlmostEqual(float(one["MFE"].iloc[0]), self._raw_mfe, places=4)
        self.assertAlmostEqual(float(two["MAE"].iloc[0]), self._raw_mae * 2, places=4)
        self.assertAlmostEqual(float(two["MFE"].iloc[0]), self._raw_mfe * 2, places=4)

    # ── B vs C parity: tools/mae_parity.py diffs these two branches row-by-row.
    def test_site_b_and_site_c_scale_identically(self):
        for lots in (1, 2, 3):
            b = self._run_site_b(lots)
            c = self._run_site_c(lots)
            with self.subTest(lots=lots):
                self.assertAlmostEqual(float(b["MAE"].iloc[0]), float(c["MAE"].iloc[0]), places=4)
                self.assertAlmostEqual(float(b["MFE"].iloc[0]), float(c["MFE"].iloc[0]), places=4)

    # ── Site D: FUT-row overwrite, runs AFTER B/C and must scale independently.
    def test_site_d_futures_overwrite_scales_by_row_lots(self):
        one = self._run_site_d(1)
        two = self._run_site_d(2)
        self.assertAlmostEqual(float(one["MAE"].iloc[0]), -0.976, places=4)
        self.assertAlmostEqual(float(one["MFE"].iloc[0]), -0.0748, places=4)
        self.assertAlmostEqual(float(two["MAE"].iloc[0]), -0.976 * 2, places=4)
        self.assertAlmostEqual(float(two["MFE"].iloc[0]), -0.0748 * 2, places=4)

    # ── Missing "lots" column (legacy CSV / multi-index combo) must default
    # to 1 (no-op), not crash and not derive lots from Qty/lot_size.
    def test_missing_lots_column_defaults_to_1_noop(self):
        b = self._run_site_b(1, with_lots_key=False)
        c = self._run_site_c(1, with_lots_key=False)
        d = self._run_site_d(1, with_lots_key=False)
        self.assertAlmostEqual(float(b["MAE"].iloc[0]), self._raw_mae, places=4)
        self.assertAlmostEqual(float(c["MAE"].iloc[0]), self._raw_mae, places=4)
        self.assertAlmostEqual(float(d["MAE"].iloc[0]), -0.976, places=4)


if __name__ == "__main__":
    unittest.main()
