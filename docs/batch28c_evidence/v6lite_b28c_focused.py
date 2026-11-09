from pathlib import Path
import json, subprocess, sys
root=Path('C:/Dev/V6Lite-dev')
args=[]
for label in ('focused_order_safety4','focused_overnight_final2'):
    for item in json.loads((root/f'docs/batch28b_evidence/{label}.json').read_text())['args']:
        if item not in args:args.append(item)
for p in sorted((root/'v6lite/tests').glob('test_batch28c_*.py')):
    name=p.relative_to(root).as_posix()
    if name not in args:args.append(name)
if 'v6lite/tests/test_ibkr_adapter.py' not in args:args.append('v6lite/tests/test_ibkr_adapter.py')
raise SystemExit(subprocess.call([sys.executable,'-B','docs/batch28c_evidence/v6lite_b28c_validate.py',sys.argv[1],*args],cwd=root))
