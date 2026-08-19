"""96-combo sweep on 262c8e6e's REAL payload (7 filter segments + YEARLY leg +
per-leg spot adjustment) so shadow mode diffs Rust vs Python on exactly the
shapes require_rust_supported rejects."""
import json, urllib.request
from services.optimizer import result_store as rs

bp = (rs.get_meta('262c8e6e-e9f4-4759-8839-e3b8912d72b6') or {}).get('base_payload')
assert bp and bp.get('legs'), 'source payload missing'
assert bp.get('filter_segments'), 'expected filter segments'

# 4 x 4 x 2 x 3 = 96 raw combos
specs = [
    {"path": "legs[0].strike_selection.value", "kind": "range", "min": 0, "max": 1.5, "step": 0.5},
    {"path": "legs[1].strike_selection.value", "kind": "range", "min": 0, "max": 1.5, "step": 0.5},
    {"path": "legs[2].strike_selection.strike_type", "kind": "enum", "values": ["ATM", "OTM1"]},
    {"path": "legs[0].spot_adjustment.direction", "kind": "enum", "values": ["rise", "fall", "both"]},
]
body = {"base_payload": bp, "param_specs": specs, "method": "exhaustive",
        "objective": "total_pnl",
        "zip_naming": {"level1": "SHADOW PARITY TEST", "level2": "shadow", "level3": "shadow"}}
req = urllib.request.Request("http://backend:8000/api/optimize/jobs",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
print(urllib.request.urlopen(req, timeout=120).read().decode()[:200])
