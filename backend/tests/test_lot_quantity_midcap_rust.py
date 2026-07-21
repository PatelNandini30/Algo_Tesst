"""
Task: lot-quantity scaling — Midcap100 overlay, LIVE Rust path
(backend/native/src/lib.rs::compute_midcap_legs).

Commit b9361471 fixed the Python reference twin (services/midcap_overlay.py)
but that function is NOT the live path. The live `/midcap-overlay` endpoint
(backend/routers/backtest.py:990-998) is Rust-only with no Python fallback
(raises HTTP 503 rather than falling back) and calls
`algotest_native.compute_midcap_legs` directly. That Rust function carried
`lots` in the leg JSON but never multiplied anything by it — the same bug
already fixed on the Python side, still live in the compiled extension.

This test exercises `algotest_native.compute_midcap_legs` directly (no DB,
no Postgres, no routers/services layer): a tiny synthetic OHLC feather is
built in-process with polars/pyarrow and loaded straight into the native
INDEX_OHLC cache via `algotest_native.load_index_ohlc`, under a private
symbol name so it can never collide with real index_ohlc data (e.g.
NIFTYMIDCAP100) used by other tests in the same process. The fixture
numbers mirror backend/tests/test_lot_quantity_midcap.py's SERIES/ROW and
OHLC/OHLC_ROW fixtures so the two test files are easy to cross-check.

EXPECTED PRE-REBUILD STATE: this test is expected to FAIL until the native
extension is rebuilt from the fixed backend/native/src/lib.rs (see CLAUDE.md
— Celery/back-end processes run whatever .so was baked into the image;
`cargo check` passing does not change the already-running extension). The
failure mode pre-rebuild is "values are equal at lots=1 and lots=3/4" (no
scaling applied yet) — i.e. AssertionError on the scaling assertions, NOT an
import error, crash, or skip.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    import algotest_native as native
except ImportError:
    native = None

try:
    import polars as pl
    import pyarrow.feather as feather
except ImportError:
    pl = None
    feather = None

# Private symbol name — must never collide with real index_ohlc symbols
# (e.g. NIFTYMIDCAP100) that other tests in this process may load/expect.
SYMBOL = "TEST_LOTS_SCALE_MIDCAP"

# Same numbers as test_lot_quantity_midcap.py's SERIES + OHLC fixtures,
# merged into one OHLC series (O=H=L=C on the plain-close rows; 2019-03-28
# is shared by both fixtures and its close (18083.45) agrees in each).
_OHLC_ROWS = [
    ("2019-02-28", 16721.10, 16721.10, 16721.10, 16721.10),
    ("2019-03-05", 16850.00, 16850.00, 16850.00, 16850.00),
    ("2019-03-15", 17400.00, 17400.00, 17400.00, 17400.00),
    ("2019-03-26", 17664.6, 17818.5, 17645.0, 17806.25),   # entry day (excluded from MAE/MFE scan)
    ("2019-03-27", 17881.8, 17991.65, 17848.95, 17898.5),
    ("2019-03-28", 17954.8, 18131.6, 17929.7, 18083.45),   # exit day
]

# Leg-P&L fixture: entry 2019-02-28 close 16721.10, exit 2019-03-28 close 18083.45.
ROW = {
    "trade_id": 1,
    "entry_date": "28-02-2019",
    "exit_date": "28-03-2019",
    "nifty_pnl": -603.25,
    "nifty_pnl_pct": -5.589529766,
}

# MAE/MFE fixture: f_entry = 17806.25 * (1 + 0.5%/mo * 2/30); documented
# MFE=1.7932 / MAE=0.2231 at lots=1, hypothetical BUY 0.5%/month.
OHLC_ROW = {
    "trade_id": 1,
    "entry_date": "26-03-2019",
    "exit_date": "28-03-2019",
    "nifty_pnl": 3.25,
    "nifty_pnl_pct": 0.0283,
}


def _build_and_load_feather(tmpdir: Path) -> None:
    df = pl.DataFrame(
        {
            "Date": [r[0] for r in _OHLC_ROWS],
            "Symbol": [SYMBOL] * len(_OHLC_ROWS),
            "Open": [r[1] for r in _OHLC_ROWS],
            "High": [r[2] for r in _OHLC_ROWS],
            "Low": [r[3] for r in _OHLC_ROWS],
            "Close": [r[4] for r in _OHLC_ROWS],
        }
    ).with_columns(pl.col("Date").str.to_date())
    path = tmpdir / f"{SYMBOL}.feather"
    feather.write_feather(df.to_arrow(), path, compression="uncompressed")
    native.load_index_ohlc(str(path))


def _run(row, legs, sa=None):
    out = native.compute_midcap_legs(
        json.dumps([row]), json.dumps(legs), json.dumps(sa or {}), SYMBOL,
    )
    return json.loads(out)


@unittest.skipIf(native is None, "algotest_native extension not built")
@unittest.skipIf(pl is None or feather is None, "polars/pyarrow not available")
class TestRustLegPnlScalesByLots(unittest.TestCase):
    """Midcap leg P&L (and its % counterpart) must scale linearly by the
    leg's own lots when calling the live Rust compute_midcap_legs directly —
    not by lots^2 — and Combined Net P&L must be the SUM of the (already
    lot-scaled) NIFTY + Midcap sides, never re-multiplied again."""

    @classmethod
    def setUpClass(cls):
        if native is None or pl is None:
            return
        cls._tmp = TemporaryDirectory()
        _build_and_load_feather(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        # Deliberately do NOT call native.clear_index_ohlc(): that wipes the
        # ENTIRE process-global INDEX_OHLC cache (all symbols), which could
        # break other tests in the same run that rely on real index_ohlc
        # data already being warm (e.g. test_midcap_overlay.py's
        # TestMidcapRustPythonParity). Leaving our private SYMBOL loaded is
        # harmless — it can't collide with any real index.
        if hasattr(cls, "_tmp"):
            cls._tmp.cleanup()

    def _leg(self, lots):
        return [{"midcap_mode": "hypothetical", "position": "buy",
                  "cost_pct_per_month": 0.5, "lots": lots}]

    def test_leg_pnl_scales_linearly_not_quadratically(self):
        r1 = _run(ROW, self._leg(1))["results"][0]
        r3 = _run(ROW, self._leg(3))["results"][0]
        self.assertAlmostEqual(r3["Midcap Leg P&L"], r1["Midcap Leg P&L"] * 3, places=2)
        self.assertAlmostEqual(r3["Midcap Leg P&L %"], r1["Midcap Leg P&L %"] * 3, places=2)
        # Explicitly rule out lots^2 (would be *9, not *3).
        self.assertNotAlmostEqual(r3["Midcap Leg P&L"], r1["Midcap Leg P&L"] * 9, places=2)

    def test_lots_1_is_noop_vs_missing_lots_key(self):
        explicit = _run(ROW, self._leg(1))["results"][0]
        implicit = _run(ROW, [{"midcap_mode": "hypothetical", "position": "buy",
                                "cost_pct_per_month": 0.5}])["results"][0]
        self.assertEqual(explicit, implicit)

    def test_combined_net_pnl_is_sum_of_scaled_parts_not_double_scaled(self):
        r1 = _run(ROW, self._leg(1))["results"][0]
        r3 = _run(ROW, self._leg(3))["results"][0]
        nifty_pnl = ROW["nifty_pnl"]
        # Combined Net P&L at 3 lots must equal nifty_pnl (unchanged — NIFTY
        # side scaling lives upstream, not in this function) + 3x the 1-lot
        # Midcap leg P&L, never lots*(nifty + leg_1lot) and never lots^2.
        expected = nifty_pnl + 3 * r1["Midcap Leg P&L"]
        self.assertAlmostEqual(r3["Combined Net P&L"], expected, places=2)
        not_double_scaled = nifty_pnl * 3 + 3 * r1["Midcap Leg P&L"]
        self.assertNotAlmostEqual(r3["Combined Net P&L"], not_double_scaled, places=2)

    def test_two_legs_different_lots_combine_additively(self):
        leg_a_1lot = _run(ROW, [{"midcap_mode": "spot", "position": "buy", "lots": 1}])["results"][0]
        leg_b_1lot = _run(ROW, [{"midcap_mode": "spot", "position": "sell", "lots": 1}])["results"][0]
        combo = _run(ROW, [
            {"midcap_mode": "spot", "position": "buy", "lots": 2},
            {"midcap_mode": "spot", "position": "sell", "lots": 3},
        ])["results"][0]
        expected_leg_pnl = 2 * leg_a_1lot["Midcap Leg P&L"] + 3 * leg_b_1lot["Midcap Leg P&L"]
        self.assertAlmostEqual(combo["Midcap Leg P&L"], expected_leg_pnl, places=2)


@unittest.skipIf(native is None, "algotest_native extension not built")
@unittest.skipIf(pl is None or feather is None, "polars/pyarrow not available")
class TestRustMaeMfeScalesByLots(unittest.TestCase):
    """Midcap MAE/MFE must scale linearly by the leg's own lots so they stay
    commensurate with the already lot-scaled NIFTY MAE/MFE that
    native/src/summary_metrics.rs:399 pairs them with (nm1 = Midcap MFE +
    Sum NIFTY MAE, nm2 = Midcap MAE + Sum NIFTY MFE)."""

    @classmethod
    def setUpClass(cls):
        if native is None or pl is None:
            return
        cls._tmp = TemporaryDirectory()
        _build_and_load_feather(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "_tmp"):
            cls._tmp.cleanup()

    def _leg(self, lots):
        return [{"midcap_mode": "hypothetical", "position": "buy",
                  "cost_pct_per_month": 0.5, "lots": lots}]

    def test_mae_mfe_lots_1_matches_workbook(self):
        r = _run(OHLC_ROW, self._leg(1))["results"][0]
        self.assertAlmostEqual(r["Midcap MFE"], 1.7932, places=3)
        self.assertAlmostEqual(r["Midcap MAE"], 0.2231, places=3)

    def test_mae_mfe_scale_linearly_not_quadratically(self):
        base = _run(OHLC_ROW, self._leg(1))["results"][0]
        scaled = _run(OHLC_ROW, self._leg(4))["results"][0]
        self.assertAlmostEqual(scaled["Midcap MFE"], base["Midcap MFE"] * 4, places=3)
        self.assertAlmostEqual(scaled["Midcap MAE"], base["Midcap MAE"] * 4, places=3)
        # Explicitly rule out lots^2 (would be *16, not *4).
        self.assertNotAlmostEqual(scaled["Midcap MFE"], base["Midcap MFE"] * 16, places=3)


if __name__ == "__main__":
    unittest.main()
