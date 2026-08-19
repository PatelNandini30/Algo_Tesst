"""Is a downloaded summary REALLY patchwise? Compare its Max DD column against
the stored overall vs patchwise values for the same combo."""
import sys, os, json, zipfile, re
import xml.etree.ElementTree as ET
NS='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
from services.optimizer import result_store as rs

def sheet_vals(path, want_sheet='Optimization Summary'):
    z=zipfile.ZipFile(path)
    wb=z.read('xl/workbook.xml').decode('utf8','ignore')
    names=[n for n in re.findall(r'name="([^"]+)"', wb) if not n.startswith('_xlnm')]
    idx=names.index(want_sheet)+1
    ss=[''.join(t.text or '' for t in si.iter(NS+'t')) for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall(NS+'si')]
    root=ET.fromstring(z.read(f'xl/worksheets/sheet{idx}.xml'))
    rows=[]
    for row in root.iter(NS+'row'):
        if int(row.get('r'))>3: break
        cells=[]
        for c in row.findall(NS+'c'):
            v=c.find(NS+'v')
            cells.append('' if v is None else (ss[int(v.text)] if c.get('t')=='s' else v.text))
        rows.append(cells)
    return rows

for job, path in [a.split('=',1) for a in sys.argv[1:]]:
    rows = sheet_vals(path)
    hdr = rows[0]
    try: col = hdr.index('DD %')
    except ValueError:
        col = next((i for i,h in enumerate(hdr) if 'DD %' == str(h)), None)
    file_val = float(rows[1][col]) if col is not None else None
    stored = rs.get_all_results(job)
    first = stored[0] if stored else {}
    overall = (first.get('summary') or {}).get('max_dd_pct')
    p = rs.zip_cache_path(job, True).replace('.zip','-summary.json')
    pw = None
    if os.path.isfile(p):
        with open(p) as fh:
            m={r['combo_id']: r['summary'] for r in (json.load(fh).get('rows') or [])}
        pw = (m.get(first.get('combo_id')) or {}).get('max_dd_pct')
    def close(a,b):
        try: return abs(float(a)-float(b))<1e-6
        except (TypeError,ValueError): return False
    basis = 'PATCHWISE' if close(file_val,pw) else ('OVERALL' if close(file_val,overall) else '?')
    print(f"  {job[:8]}  file={file_val}  overall={overall}  patchwise={pw}  -> {basis}")
