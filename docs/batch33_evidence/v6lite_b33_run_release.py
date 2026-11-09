"""Serial offline release validation; stops on any failure; never commits."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib,json,os,subprocess,sys,time
R=Path('C:/Dev/V6Lite-dev');O=R/'docs/batch33_evidence'
sys.path[:0]=[str(R),str(O)]
from v6lite_b33_validate import inventory
def state(phase,**more):
 (O/'run_status.json').write_text(json.dumps({'phase':phase,'updated_pt':datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(),**more},indent=2),encoding='utf-8')
 print(phase,flush=True)
def run(script,*args):
 rc=subprocess.call([sys.executable,'-B',str(O/script),*args],cwd=R)
 if rc:
  state('stopped_on_failure',script=script,args=args,returncode=rc)
  raise SystemExit(rc)
state('waiting_for_serial_focused_completion')
while (O/'.validation.lock').exists():time.sleep(1)
focused=json.loads((O/'focused_final_json_guard.json').read_text(encoding='utf-8'))
assert focused['returncode']==focused['failures']==focused['errors']==0
assert hashlib.sha256(json.dumps(inventory(),sort_keys=True).encode()).hexdigest()==focused['inventory_sha256']
state('freeze_and_source_compile')
run('v6lite_b33_freeze.py')
for number in (1,2,3):
 state('full_suite',number=number)
 run('v6lite_b33_validate.py',f'final_full_{number}','full')
state('integrity_closeout')
run('v6lite_b33_closeout.py')
run('v6lite_b33_report.py')
state('validated_report_ready_for_final_review',no_commit_or_tag_yet=True)
