from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import ast, hashlib, json, subprocess, sys
R=Path('C:/Dev/V6Lite-dev');O=R/'docs/batch33_evidence'
sys.path[:0]=[str(R),str(O)]
from v6lite_b33_validate import inventory, dependencies
from v6lite_b33_inputs import verify_inputs
from v6lite.config import load_config
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False),encoding='utf-8')
assert not (O/'.validation.lock').exists(),'wait for serial focused run'
assert not (O/'freeze.json').exists(),'do not overwrite a freeze'
baseline=json.loads((O/'baseline32_source.json').read_text(encoding='utf-8'))
B=O/'baseline_b32'
assert all(sha(B/n)==h for n,h in baseline['inventory'].items())
assert sha(B/'v6lite/tests/test_batch33_open_delay.py')==sha(R/'v6lite/tests/test_batch33_open_delay.py')
digest=hashlib.sha256()
for name in sorted(n for n in baseline['inventory'] if n.startswith('v6lite/')):
 digest.update(name[len('v6lite/'):].encode());digest.update(b'\0');digest.update((B/name).read_bytes());digest.update(b'\0')
assert digest.hexdigest().startswith('5e94c5d34a138e7d')
before=inventory();package=hashlib.sha256()
for name in sorted(n for n in before if n.startswith('v6lite/')):
 package.update(name[len('v6lite/'):].encode());package.update(b'\0');package.update((R/name).read_bytes());package.update(b'\0')
fps={}
for mode,name in [('g3t','paper_test.toml'),('g3n','paper_g3n.toml')]:
 p=R/'v6lite/examples'/name;cfg=load_config(p)
 fps[mode]={'source':p.relative_to(R).as_posix(),'source_sha256':sha(p),'config_fingerprint':cfg.fingerprint(),'tp_start_after_open_seconds':cfg.tp_start_after_open_seconds,'scope':'checked-in synthetic template; operator configuration not read or modified'}
dump(O/'config_fingerprints.json',fps)
tools={p.name:sha(p) for p in sorted(O.glob('*.py'))}
scope=json.loads((O/'initial_scope.json').read_text(encoding='utf-8'))
assert sha(R/'docs/V6_LITE_OPERATOR_MANUAL.md')==scope['manual_sha256']
f={'frozen_at_pt':datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(),
 'base_commit':scope['base'],'base_code_sha':digest.hexdigest()[:16],
 'instructions_sha256':scope['instructions_sha256'],'inventory':before,
 'inventory_sha256':hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
 'package_sha256':package.hexdigest(),'code_sha':package.hexdigest()[:16],
 'dependencies':dependencies(),'read_only_inputs':verify_inputs(),'validation_tools':tools,
 'config_fingerprints':fps,'operator_manual_sha256':scope['manual_sha256'],
 'no_broker_connection':True,'no_runtime_state_or_operator_config_access':True,'no_push':True}
dump(O/'freeze.json',f)
compiled=[]
for name in before:
 if not name.endswith('.py'):continue
 raw=(R/name).read_bytes();assert not raw.startswith(b'\xef\xbb\xbf'),name
 source=raw.decode('utf-8');ast.parse(source,filename=name);compile(source,name,'exec');compiled.append(name)
assert before==inventory()
subprocess.run(['git','diff','--check','--','v6lite','scripts'],cwd=R,check=True)
dump(O/'source_compile.json',{'files':len(compiled),'utf8_no_bom':True,'AST_compile':True,'pyc_written':False,'source_unchanged':True,'code_sha':f['code_sha'],'baseline_original_files_unchanged':len(baseline['inventory']),'base_package_sha256':digest.hexdigest()})
print(json.dumps({'code_sha':f['code_sha'],'package_sha256':f['package_sha256'],'compiled':len(compiled),'config_fingerprints':fps},indent=2))
