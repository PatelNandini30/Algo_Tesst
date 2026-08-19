"""Submit ONE small sweep per still-gated shape, so each can get its own
measured parity corpus before its reject is lifted."""
import copy, json, sys, urllib.request
from services.optimizer import result_store as rs

BASE = (rs.get_meta('262c8e6e-e9f4-4759-8839-e3b8912d72b6') or {}).get('base_payload')
assert BASE and BASE.get('legs'), 'source payload missing'

SPECS = [  # 8 combos — enough to exercise, fast to run
    {"path": "legs[0].strike_selection.value", "kind": "range", "min": 0, "max": 1.0, "step": 0.5},
    {"path": "legs[2].strike_selection.strike_type", "kind": "enum", "values": ["ATM", "OTM1"]},
]

def submit(name, mutate):
    bp = copy.deepcopy(BASE)
    mutate(bp)
    body = {"base_payload": bp, "param_specs": SPECS, "method": "exhaustive",
            "objective": "total_pnl",
            "zip_naming": {"level1": f"GATE CORPUS {name}", "level2": name, "level3": name}}
    req = urllib.request.Request("http://backend:8000/api/optimize/jobs",
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    out = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())
    print(f"  {name:18} job={out.get('job_id')} combos={out.get('total_combos')}")

def m_midcap(bp):
    bp["midcap_legs"] = [{"midcap_mode": "hypothetical", "position": "buy",
                          "cost_pct_per_month": 0.5, "lots": 1}]

def m_legfilter(bp):
    segs = bp.get("filter_segments") or []
    bp["legs"][0] = {**bp["legs"][0], "filter_segments": segs[:3]}

def m_overall_sl(bp):
    bp["overall_sl_type"] = "percent"
    bp["overall_sl_value"] = 2

which = sys.argv[1] if len(sys.argv) > 1 else "all"
for name, fn in (("midcap", m_midcap), ("leg_filter", m_legfilter), ("overall_sl", m_overall_sl)):
    if which in ("all", name):
        submit(name, fn)
