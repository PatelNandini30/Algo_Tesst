import json, urllib.request
from services.optimizer import result_store as rs
m = rs.get_meta('4472962b-1835-4ddc-9ceb-43915d0944ec') or {}
bp = m.get('base_payload'); specs = m.get('param_specs')
assert bp and specs, 'source payload missing'
assert bp.get('multi_index_mode'), 'expected multi_index_mode'
body = {"base_payload": bp, "param_specs": specs, "method": "exhaustive",
        "objective": "total_pnl",
        "zip_naming": {"level1": "MULTIINDEX PARITY CORPUS", "level2": "mi", "level3": "mi"}}
req = urllib.request.Request("http://backend:8000/api/optimize/jobs",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
print(urllib.request.urlopen(req, timeout=120).read().decode()[:200])
