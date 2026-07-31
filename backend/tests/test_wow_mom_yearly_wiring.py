"""The `yearly` flag must reach every XLSX/WOW-MOM builder the optimizer uses.

WOW buckets by Expiry, except under YEARLY where it buckets by Exit Date — a
yearly leg holds ONE December contract, so bucketing by Expiry collapses the
whole run into that contract's ISO week while MOM (always by Exit Date) still
spans every year. The sweep path passed the flag; the ZIP worker, the on-demand
combo download and the merged WOW/MOM grid all dropped it, so those three
outputs showed a one-year WOW next to a multi-year MOM.

These are wiring tests on purpose: the maths was never wrong, the flag just
never arrived.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import routers.optimize as optimize  # noqa: E402


class TestZipWorkerForwardsYearly(unittest.TestCase):

    def _run(self, yearly):
        # Positional args tuple — keep in step with _build_one_xlsx's unpack.
        # Trailing element is rules_sheet (added with the v17 Rules sheet).
        args = ("/nonexistent.csv", "combo_1", {}, "combo_1", "", "",
                None, None, "NIFTYMIDCAP100", "", True, None, yearly, None)
        with mock.patch("pandas.read_csv", return_value=object()), \
             mock.patch("services.optimizer.excel_builder.build_combo_xlsx",
                        return_value=b"x") as built:
            label, blob, err = optimize._build_one_xlsx(args)
        self.assertIsNone(err)
        self.assertEqual(label, "combo_1")
        return built.call_args.kwargs

    def test_yearly_true_is_forwarded(self):
        self.assertIs(self._run(True)["yearly"], True)

    def test_yearly_false_is_forwarded(self):
        self.assertIs(self._run(False)["yearly"], False)

    def test_worker_unpacks_exactly_what_the_builder_packs(self):
        """A packed/unpacked length mismatch is how the flag got lost."""
        import inspect
        src = inspect.getsource(optimize._build_zip_blocking)
        self.assertIn("_is_yearly,", src, "ZIP builder must pack the yearly flag")
        self.assertIn(
            'str(base_payload.get("expiry_type") or "").upper() == "YEARLY"', src)


class TestMergedGridForwardsYearly(unittest.TestCase):

    def test_write_merged_wow_mom_honours_the_flag(self):
        """`yearly` on a combo dict must reach _wm_from_cleaned."""
        from services.optimizer import wow_mom
        combos = [{"title": "PE ATM | No Adj", "cleaned": [], "yearly": True}]
        with mock.patch.object(wow_mom, "_wm_from_cleaned",
                               return_value={"n_trades": 0}) as wm:
            wow_mom.write_merged_wow_mom(mock.MagicMock(), combos)
        self.assertIs(wm.call_args.kwargs["yearly"], True)

    def test_defaults_to_false_when_absent(self):
        from services.optimizer import wow_mom
        combos = [{"title": "PE ATM | No Adj", "cleaned": []}]
        with mock.patch.object(wow_mom, "_wm_from_cleaned",
                               return_value={"n_trades": 0}) as wm:
            wow_mom.write_merged_wow_mom(mock.MagicMock(), combos)
        self.assertIs(wm.call_args.kwargs["yearly"], False)


class TestDownloadPathsComputeYearly(unittest.TestCase):
    """The two remaining callers derive the flag from base_payload."""

    def test_sources_derive_the_flag(self):
        import inspect
        from services.optimizer import runner
        expr = 'str(base_payload.get("expiry_type") or "").upper() == "YEARLY"'
        for fn, name in (
            (optimize.download_combo_tradesheet_xlsx, "on-demand combo XLSX"),
            (optimize.download_wow_mom, "merged WOW/MOM"),
            (runner._prebuild_wow_mom, "WOW/MOM pre-build"),
        ):
            with self.subTest(name):
                self.assertIn(expr, inspect.getsource(fn))


class TestNoBuilderCallDropsYearly(unittest.TestCase):
    """Whole-tree guard: EVERY call into an XLSX/WOW-MOM builder passes `yearly`.

    Grepping for the function name is not enough — `runner._build_combo_xlsx_worker`
    imported it as `_build_xlsx_w`, so a name-based search reported the file clean
    while the ZIP pre-build silently shipped a one-year WOW block. This walks the
    AST, resolves import aliases, and fails on any call missing the kwarg.
    """

    TARGETS = {"build_combo_xlsx", "write_combo_xlsx", "write_combo_xlsx_patchwise",
               "write_wow_mom_combined", "wow_mom_ops", "_wm_from_cleaned"}
    SKIP_DIRS = {"tests", "tools", "native", ".git", "__pycache__", "migrations"}
    # Product packages only — same curated scope as code_version._HASH_DIRS, so
    # ad-hoc scratch scripts at the backend root can't raise false failures.
    SCAN_DIRS = ("services", "routers", "worker", "engines", "strategies")

    def test_every_call_site_passes_yearly(self):
        import ast
        base = os.path.join(os.path.dirname(__file__), "..")
        root = base
        missing = []
        walked = [(d, dn, fn) for pkg in self.SCAN_DIRS
                  for d, dn, fn in os.walk(os.path.join(base, pkg))]
        for dirpath, dirnames, filenames in walked:
            dirnames[:] = [d for d in dirnames if d not in self.SKIP_DIRS]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    with open(path, encoding="utf-8") as fh:
                        tree = ast.parse(fh.read())
                except (SyntaxError, UnicodeDecodeError):
                    continue
                alias = {}
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        for a in node.names:
                            if a.name in self.TARGETS:
                                alias[a.asname or a.name] = a.name
                    elif isinstance(node, ast.FunctionDef) and node.name in self.TARGETS:
                        alias[node.name] = node.name
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    f = node.func
                    nm = (f.id if isinstance(f, ast.Name)
                          else f.attr if isinstance(f, ast.Attribute) else None)
                    if not (alias.get(nm) or (nm in self.TARGETS)):
                        continue
                    if "yearly" not in {k.arg for k in node.keywords}:
                        rel = os.path.relpath(path, root)
                        missing.append(f"{rel}:{node.lineno} {nm}(...)")
        self.assertEqual(missing, [], "builder call(s) missing yearly=:\n  " +
                                      "\n  ".join(missing))


if __name__ == "__main__":
    unittest.main()
