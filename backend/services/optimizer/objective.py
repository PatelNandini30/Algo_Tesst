"""
Resolve the user-selected objective metric for ranking optimization results.

A registered metric has:
  - a `name` (used in the API)
  - a display label (used in the UI)
  - an `extract(combined_summary)` callable
  - a `direction` ∈ {'max', 'min'} — whether higher or lower is better.

`combined_summary` is the merged dict of compute_analytics + metrics.compute_optim_metrics,
keyed on snake_case names.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass(frozen=True)
class Objective:
    name: str
    label: str
    key: str           # snake_case key in summary dict
    direction: str     # 'max' or 'min'

    def extract(self, summary: Dict[str, Any]) -> float:
        try:
            v = float(summary.get(self.key, 0) or 0)
        except (TypeError, ValueError):
            v = 0.0
        return v


_REGISTRY: Dict[str, Objective] = {}


def _register(obj: Objective) -> None:
    _REGISTRY[obj.name] = obj


# ── Default registry — covers every column in the master-summary that
# ── makes sense as a ranking objective. Keep this list in sync with
# ── frontend/src/utils/strategyParamSchema.js once built.
_register(Objective("total_pnl", "Net P&L (Sum)", "total_pnl", "max"))
_register(Objective("avg_profit_per_trade", "Net P&L (Avg)", "avg_profit_per_trade", "max"))
_register(Objective("win_pct", "Winners %", "win_pct", "max"))
_register(Objective("expectancy", "Expectancy", "expectancy", "max"))
_register(Objective("cagr_options", "CAGR (Options)", "cagr_options", "max"))
_register(Objective("car_mdd", "CAR / MDD Booked", "car_mdd", "max"))
_register(Objective("car_mdd_live", "CAR / MDD Live", "car_mdd_live", "max"))
_register(Objective("max_dd_pct", "Max DD %", "max_dd_pct", "max"))         # less negative is better
_register(Objective("actual_live_dd_max", "Actual Live DD", "actual_live_dd_max", "max"))
_register(Objective("recovery_factor", "Recovery Factor", "recovery_factor", "max"))
_register(Objective("profit_factor", "Profit Factor", "profit_factor", "max"))
_register(Objective("reward_to_risk", "Reward : Risk", "reward_to_risk", "max"))
_register(Objective("roi_vs_spot", "ROI vs Spot", "roi_vs_spot", "max"))


def list_objectives() -> List[Dict[str, str]]:
    return [
        {"name": o.name, "label": o.label, "direction": o.direction}
        for o in _REGISTRY.values()
    ]


def resolve_objective(name: Optional[str]) -> Objective:
    """Look up an objective by name. Falls back to `total_pnl`."""
    if not name:
        return _REGISTRY["total_pnl"]
    if name in _REGISTRY:
        return _REGISTRY[name]
    raise ValueError(f"Unknown objective metric: {name}")


def score_combo(combo_result: Dict[str, Any], objective: Objective) -> float:
    """Extract objective value. Caller uses this to sort/rank."""
    return objective.extract(combo_result.get("summary", {}))
