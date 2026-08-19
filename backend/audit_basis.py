"""For every downloaded summary: is its DD% the PATCHWISE value or not?
Compares against <job>.v22-pw-summary.json, which survives longer than Redis."""
import sys, os, json, zipfile, re
import xml.etree.ElementTree as ET
NS='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
from services.optimizer import result_store as rs

def first_row_dd(path):
    z=zipfile.ZipFile(path)
    wb=z.read('xl/workbook.xml').decode('utf8','ignore')
    names=[n for n in re.findall(r'name="([^"]+)"', wb) if not n.startswith('_xlnm')]
    if 'Optimization Summary' not in names: return None, None
    idx=names.index('Optimization Summary')+1
    ss=[''.join(t.text or '' for t in si.iter(NS+'t')) for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall(NS+'si')]
    root=ET.fromstring(z.read(f'xl/worksheets/sheet{idx}.xml'))
    rows=[]
    for row in root.iter(NS+'row'):
        if int(row.get('r'))>2: break
        cells=[]
        for c in row.findall(NS+'c'):
            v=c.find(NS+'v')
            cells.append('' if v is None else (ss[int(v.text)] if c.get('t')=='s' else v.text))
        rows.append(cells)
    if len(rows)<2: return None, None
    try: col=rows[0].index('DD %')
    except ValueError: return None, None
    try: return float(rows[1][col]), rows[1][0]
    except (ValueError, IndexError): return None, None

for job, path in [a.split('=',1) for a in sys.argv[1:]]:
    dd, sr = first_row_dd(path)
    p = rs.zip_cache_path(job, True).replace('.zip','-summary.json')
    if not os.path.isfile(p):
        print(f"  {job[:8]}  file_dd={dd}  pw_source=ABSENT -> CANNOT VERIFY"); continue
    with open(p) as fh:
        m={r['combo_id']: r['summary'] for r in (json.load(fh).get('rows') or [])}
    pw = (m.get(1) or {}).get('max_dd_pct')
    ok = dd is not None and pw is not None and abs(dd-float(pw))<1e-6
    print(f"  {job[:8]}  file_dd={dd}  patchwise={pw}  -> {'PATCHWISE OK' if ok else '*** NOT PATCHWISE ***'}")
