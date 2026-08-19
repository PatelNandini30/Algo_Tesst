"""Corpus sweeps for the remaining gated shapes that real strategies actually use:
   mixed_expiry  — WEEKLY cadence + a MONTHLY option leg (broke job 027a9a6a)
   payload_spot  — strategy-level spot_adjustment_enabled
"""
import copy, json, sys, urllib.request
from services.optimizer import result_store as rs

src = None
for k in rs._redis().scan_iter('optim:*:meta'):
    k = k.decode() if isinstance(k, bytes) else k
    j = k.split(':')[1]
    m = rs.get_meta(j) or {}
    bp = m.get('base_payload') or {}
    if m.get('status') == 'success' and len(bp.get('legs') or []) >= 2:
        src = bp
        break
assert src, 'no usable source payload'

SPECS = [{"path": "legs[0].strike_selection.strike_type", "kind": "enum",
          "values": ["ATM", "OTM1", "ITM1", "OTM2"]},
         {"path": "legs[1].strike_selection.strike_type", "kind": "enum",
          "values": ["ATM", "ITM1", "OTM1"]}]

def submit(name, mutate):
    bp = copy.deepcopy(src)
    mutate(bp)
    body = {"base_payload": bp, "param_specs": SPECS, "method": "exhaustive",
            "objective": "total_pnl",
            "zip_naming": {"level1": f"GATE2 {name}", "level2": name, "level3": name}}
    req = urllib.request.Request("http://backend:8000/api/optimize/jobs",
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    out = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())
    print(f"  {name:14} job={out.get('job_id')} combos={out.get('total_combos')}")

def m_mixed(bp):
    bp["expiry_type"] = "WEEKLY"
    for l in bp.get("legs") or []:
        if str(l.get("segment", "OPTIONS")).upper() not in ("FUTURES", "FUTURE"):
            l["expiry"] = "MONTHLY"      # monthly option leg under a weekly cadence
            break

def m_spotadj(bp):
    bp["spot_adjustment_enabled"] = True
    bp["spot_adjustment_pct"] = 1
    bp["spot_adjustment_direction"] = "rise"
    bp["spot_adjustment_units"] = "percent"

which = sys.argv[1] if len(sys.argv) > 1 else "all"
for name, fn in (("mixed_expiry", m_mixed), ("payload_spot", m_spotadj)):
    if which in ("all", name):
        submit(name, fn)
