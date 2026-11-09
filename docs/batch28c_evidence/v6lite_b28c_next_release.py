from pathlib import Path
import hashlib,json,subprocess,sys,xml.etree.ElementTree as ET
root=Path('C:/Dev/V6Lite-dev');out=root/'docs/batch28c_evidence'
sys.path.insert(0,str(root));sys.path.insert(0,str(out))
from v6lite.version import code_sha
from v6lite_b28c_validate import inventory
label=sys.argv[1]
r=json.loads((out/f'{label}.json').read_text());f=json.loads((out/'freeze.json').read_text())
assert r['returncode']==r['failures']==r['errors']==0 and r['source_unchanged'] and not r['incomplete']
assert r['args']==['v6lite/tests','scripts/tests']
assert r['code_sha']==f['code_sha']==code_sha()
assert r['inventory_sha256']==f['inventory_sha256']
assert json.loads((out/f'{label}_inventory.json').read_text())==f['inventory']==inventory()
xml=ET.parse(out/f'{label}.xml').getroot()
suites=[xml] if xml.tag=='testsuite' else list(xml)
for key in ('tests','failures','errors','skipped'):
    assert sum(int(s.get(key,0)) for s in suites)==r[key],key
assert sum(len(s.findall('testcase')) for s in suites)==r['tests']
assert all(hashlib.sha256((out/n).read_bytes()).hexdigest()==h for n,h in f['validation_tools'].items())
print(json.dumps({'verified':label,'passed':r['tests']-r['skipped'],'skipped':r['skipped'],
                  'seconds':r['seconds'],'code_sha':r['code_sha'],'same_inventory':True}),flush=True)
subprocess.run([sys.executable,'-B','docs/batch28c_evidence/v6lite_b28c_metrics.py',label],cwd=root,check=True)
if len(sys.argv)>2:
    next_label=sys.argv[2]
    assert not (out/f'{next_label}.json').exists()
    subprocess.run([sys.executable,'-B','docs/batch28c_evidence/v6lite_b28c_validate.py',next_label,'full'],cwd=root,check=True)
