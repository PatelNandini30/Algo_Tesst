"""Every combo-workbook builder call must pass `rules_sheet`.

Regression: routers/optimize.py passed it on both download paths, but
runner._build_combo_xlsx_worker (the ZIP PRE-build, which fires right after the
sweep and wins the race to the zip cache) did not. Result: every combo workbook
the user actually downloaded shipped without its leg-wise Rules tab, while the
code that "had" the feature was the path that almost never ran.

Same shape as test_wow_mom_yearly_wiring's yearly guard — and the same reason a
name grep is not enough: the worker imports the builder as `_build_xlsx_w`.
"""
import ast
import os
import unittest


class TestNoBuilderCallDropsRulesSheet(unittest.TestCase):
    # Combo-workbook builders only. WOW/MOM builders have no Rules tab.
    TARGETS = {"build_combo_xlsx", "write_combo_xlsx", "write_combo_xlsx_patchwise"}
    SKIP_DIRS = {"tests", "tools", "native", ".git", "__pycache__", "migrations"}
    SCAN_DIRS = ("services", "routers", "worker", "engines", "strategies")

    def test_every_call_site_passes_rules_sheet(self):
        base = os.path.join(os.path.dirname(__file__), "..")
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
                    if "rules_sheet" not in {k.arg for k in node.keywords}:
                        rel = os.path.relpath(path, base)
                        missing.append(f"{rel}:{node.lineno} {nm}(...)")
        self.assertEqual(missing, [], "builder call(s) missing rules_sheet=:\n  " +
                                      "\n  ".join(missing))


if __name__ == "__main__":
    unittest.main()
