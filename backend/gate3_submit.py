"""Corpus sweeps that ACTUALLY vary: reuse a proven job's own param_specs
(trimmed to ~12 combos) instead of inventing axes that turn out inert and get
collapsed by effective_fingerprint."""
import copy, json, sys, urllib.request
from services.optimizer import result_store as rs

SRC = '1ce330ca'                       # 637 combos, 637 with trades, median 34
r = rs._redis()
key = [k.decode() if isinstance(k, bytes) else k
       for k in r.scan_iter(f'optim:{SRC}*:meta')][0]
m = rs.get_meta(key.split(':')[1]) or {}
base, specs = m.get('base_payload'), m.get('param_specs') or []
assert base and specs, 'source payload/specs missing'

# Trim to the first axes whose product is 8..16 — real axes, small grid.
trimmed, n = [], 1
for sp in specs:
    if sp.get('kind') == 'range':
        try:
            cnt = int((float(sp['max']) - float(sp['min'])) / float(sp['step'])) + 1
        except Exception:
            cnt = 2
    else:
        cnt = max(1, len(sp.get('values') or []))
    if n * cnt > 16:
        continue
    trimmed.append(sp); n *= cnt
    if n >= 8:
        break
print(f"  using {len(trimmed)} real axes -> ~{n} combos")

def submit(name, mutate):
    bp = copy.deepcopy(base); mutate(bp)
    body = {"base_payload": bp, "param_specs": trimmed, "method": "exhaustive",
            "objective": "total_pnl",
            "zip_naming": {"level1": f"GATE3 {name}", "level2": name, "level3": name}}
    req = urllib.request.Request("http://backend:8000/api/optimize/jobs",
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    out = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())
    print(f"  {name:14} job={out.get('job_id')} combos={out.get('total_combos')}")

def m_mixed(bp):
    bp["expiry_type"] = "WEEKLY"
    for l in bp.get("legs") or []:
        if str(l.get("segment", "OPTIONS")).upper() not in ("FUTURES", "FUTURE"):
            l["expiry"] = "MONTHLY"; break

def m_spotadj(bp):
    bp.update(spot_adjustment_enabled=True, spot_adjustment_pct=1,
              spot_adjustment_direction="rise", spot_adjustment_units="percent")

which = sys.argv[1] if len(sys.argv) > 1 else "all"
for name, fn in (("mixed_expiry", m_mixed), ("payload_spot", m_spotadj)):
    if which in ("all", name):
        submit(name, fn)
