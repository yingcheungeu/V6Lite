"""Serial offline Windows full-suite gate, outside the PT restricted window."""
from __future__ import annotations
import ast,hashlib,json,os,platform,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from v6lite.version import code_sha
OUT=ROOT/'docs/batch22_evidence'
EXPECTED_INSTRUCTION='14206477aab33bdc242e331d88745695d0f828e8c9744d49eaa20df4ed43c18a'
EXPECTED_MANUAL='5d7eb6f043f4d933019f36b6cbfd28b28f8474c0b7097261f9bb77ae627133bf'

def snapshot():
 paths=[p for p in (ROOT/'v6lite').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
 paths+=list((ROOT/'scripts').rglob('*.py'))+list((ROOT/'scripts').glob('v6*'))
 return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}

def outside():
 now=datetime.now(timezone.utc);local=now.astimezone(ZoneInfo('America/Los_Angeles'))
 assert not(local.weekday()<5 and (6,0)<=(local.hour,local.minute)<(13,30)), str(local)
 return now.isoformat(),local.isoformat()

def main():
 assert platform.system()=='Windows'
 assert hashlib.sha256((ROOT/'docs/V6_LITE_BATCH22_INSTRUCTIONS.md').read_bytes()).hexdigest()==EXPECTED_INSTRUCTION
 assert hashlib.sha256((ROOT/'docs/V6_LITE_OPERATOR_MANUAL.md').read_bytes()).hexdigest()==EXPECTED_MANUAL
 frozen=snapshot();fingerprint=code_sha()
 for path in (ROOT/'v6lite').rglob('*.py'):ast.parse(path.read_text(encoding='utf-8'))
 metadata=dict(code_sha=fingerprint,source_sha256=frozen,python=sys.version,platform=platform.platform(),
  command=[sys.executable,'-u','-B','-m','pytest','v6lite/tests','scripts/tests','-q','-p','no:cacheprovider','--tb=short'],rounds=[])
 (OUT/'full_validation.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
 env=os.environ.copy();env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1')
 for key in ('V6LITE_TEST_PROTECTION','V6LITE_DISCORD_WEBHOOK','DISCORD_BOT_TOKEN','SMTP_PASSWORD'):
  env.pop(key,None)
 for index in (1,2,3):
  start_utc,start_pt=outside(); assert snapshot()==frozen
  print('ROUND '+str(index)+' start_pt='+start_pt+' code_sha='+fingerprint,flush=True)
  start=time.monotonic()
  with (OUT/f'full_{index}.log').open('w',encoding='utf-8') as log:
   command=metadata['command']+['--junitxml='+str(OUT/f'full_{index}.xml')]
   result=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
  stop_utc,stop_pt=outside();suite=ET.parse(OUT/f'full_{index}.xml').getroot()[0]
  record=dict(round=index,start_utc=start_utc,start_pt=start_pt,end_utc=stop_utc,end_pt=stop_pt,
   wall_seconds=time.monotonic()-start,exit_code=result.returncode,suite=suite.attrib,
   source_unchanged=snapshot()==frozen,code_sha_after=code_sha())
  metadata['rounds'].append(record)
  (OUT/'full_validation.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
  print(json.dumps(record),flush=True)
  assert result.returncode==0 and suite.get('failures')=='0' and suite.get('errors')=='0',record
  assert record['source_unchanged'] and record['code_sha_after']==fingerprint
 print('SERIAL_FULL_THREE_PASS',flush=True)
if __name__=='__main__':main()
