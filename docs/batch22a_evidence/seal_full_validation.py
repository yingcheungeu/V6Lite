"""Seal batch22a full-suite evidence without changing source or the item9 report."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib,json,subprocess,sys,platform,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[2]; EV=Path(__file__).resolve().parent
OLD=ROOT/'docs/batch22_item9_evidence'; REPORT=ROOT/'docs/V6_LITE_BATCH22A_FULL_VALIDATION_2026-10-09.md'
EXPECTED='f27e144de9eb406b'
SOURCE=['v6lite/models.py','v6lite/broker/ibkr.py','v6lite/engine.py','v6lite/protection_engine.py','v6lite/protection_incidents.py','v6lite/protection_cancellations.py','v6lite/tests/test_batch20_card_provenance.py','v6lite/tests/test_batch20_protection_incident.py','v6lite/tests/test_batch22_config_broker.py','v6lite/tests/test_batch22_protection_lifecycle.py','v6lite/tests/test_batch22_item9_broker_cancel.py']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True,encoding='utf-8').strip()
def code_sha():
 h=hashlib.sha256()
 for p in sorted((p for p in (ROOT/'v6lite').rglob('*') if p.is_file() and '__pycache__' not in p.parts),key=lambda p:p.relative_to(ROOT/'v6lite').as_posix()):
  h.update(p.relative_to(ROOT/'v6lite').as_posix().encode());h.update(b'\0');h.update(p.read_bytes());h.update(b'\0')
 return h.hexdigest()[:16]
def entry(p):return {'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)}
assert ROOT==Path('C:/Dev/V6Lite-dev') and git('branch','--show-current')=='dev'
assert not git('diff','--cached','--name-only') and code_sha()==EXPECTED
assert git('rev-parse','HEAD')=='42c77da139ce308ca133eb7e2e40beaf33ecac9d'
assert git('rev-parse','v6lite-batch22')=='18f44ab40bc934327c6cacd921e6717ff21c3a80'
assert sha(ROOT/'docs/V6_LITE_BATCH22_ITEM9_FIX_REPORT_2026-10-09.md')=='e0df7b68600349591b1a2c1fd3223ea413d7c3903e6b15b2625a9b1025a5eecf'
assert sha(OLD/'sha256_manifest.json')=='53c79f52d84f7c376cbc477fc91d85a4ddf5c4e532340db48fbdbaf90a3e0357'
assert sha(ROOT/'docs/V6_LITE_BATCH23_INSTRUCTIONS.md')=='04708b69d6b3c410b487fb440f97a98790ce47bc0ad8dd3b23d471dc0592860c'
original=json.loads((OLD/'sha256_manifest.json').read_text(encoding='utf-8'))
assert all(entry(ROOT/i['path'])==i for i in original['entries'])
rows=[]
for index in range(1,4):
 candidates=[json.loads(p.read_text(encoding='utf-8')) for p in EV.glob('full_'+str(index)+'*.json')]
 assert candidates, 'full run missing'
 row=max(candidates,key=lambda r:r['end_pt']); t=row['totals']
 assert row['returncode']==0 and t['errors']==t['failures']==0 and row['source_unchanged'] and row['source_code_sha']==EXPECTED
 for time in (row['start_pt'],row['end_pt']):
  dt=datetime.fromisoformat(time).astimezone(ZoneInfo('America/Los_Angeles'))
  assert not(dt.weekday()<5 and (6,0)<=(dt.hour,dt.minute)<(13,30))
 assert sha(EV/(row['label']+'.txt'))==row['stdout_sha256'] and sha(EV/(row['label']+'.xml'))==row['junit_sha256']
 xml=ET.parse(EV/(row['label']+'.xml')); suites=list(xml.getroot().iter('testsuite'))
 assert t=={k:sum(int(s.attrib.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
 assert len(list(xml.getroot().iter('testcase')))==t['tests']==len(row['cases'])
 actual=[]
 for case in xml.getroot().iter('testcase'):
  failure=case.find('failure');error=case.find('error')
  actual.append({'name':case.attrib.get('name'),'outcome':'failure' if failure is not None else 'error' if error is not None else 'skipped' if case.find('skipped') is not None else 'passed','message':failure.attrib.get('message','') if failure is not None else error.attrib.get('message','') if error is not None else ''})
 assert actual==row['cases'],'case metadata/JUnit mismatch'
 rows.append(row)
assert rows[0]['end_pt']<=rows[1]['start_pt'] and rows[1]['end_pt']<=rows[2]['start_pt']
assert all(r['totals']==rows[0]['totals'] for r in rows)
assert all(sorted(c['name'] for c in r['cases'] if c['outcome']=='skipped')==sorted(c['name'] for c in rows[0]['cases'] if c['outcome']=='skipped') for r in rows)
summary={'code_sha':EXPECTED,'parent_commit':git('rev-parse','HEAD'),'python':sys.version,'windows':platform.platform(),'runs':rows,'full_gate':'PASS: three consecutive default Windows suites','original_report_preserved':True,'original_manifest_preserved':True,'intended_annotated_tag':'v6lite-batch22a','excluded_external_work':['docs/V6_LITE_OPERATOR_MANUAL.md','docs/V6_LITE_BATCH23_INSTRUCTIONS.md','docs/V6_LITE_BATCH22_ITEM8_RESEARCH_2026-10-09.md'],'broker_connections':0,'runtime_or_user_state_access':False}
(EV/'full_validation.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
lines=['# V6-Lite batch22a 全量验收补充报告（2026-10-09）','','第9条补丁的默认全量连续3次已通过。本报告补充第9条冻结报告，不改写原报告或原200项manifest。','',f'- code_sha：`{EXPECTED}`；父提交：`{summary["parent_commit"]}`。','- 原第9条报告 SHA-256：`e0df7b68600349591b1a2c1fd3223ea413d7c3903e6b15b2625a9b1025a5eecf`。','- 原manifest SHA-256：`53c79f52d84f7c376cbc477fc91d85a4ddf5c4e532340db48fbdbaf90a3e0357`，200/200仍一致。','- 首次测试实际1344 passed+1 skipped，但冻结runner在计算目录哈希时失败，不计入gate；原始TXT/XML和initial_tooling_failure.json保留。新文档工具修目录摘要后另连续3次验收，交易源码未改。','- 默认全量：v6lite/tests + scripts/tests；使用dev .venv，-B、PYTHONUTF8=1、PYTHONDONTWRITEBYTECODE=1，禁用pytest cache和第三方插件自动加载。','- 每轮开始/结束均在交易时段限制之外；源码digest每轮前后不变；未连接券商、未读取运行仓库或User归档。','- 第9条新增42项及已有保护回归291项先前均已通过；两冻结基线红证据和全部中间失败保留。','',f'Python：`{sys.version.splitlines()[0]}`；Windows：`{platform.platform()}`。','','| 次数 | 结果 | 实际PT开始—结束 | stdout SHA-256 | JUnit SHA-256 |','|---|---|---|---|---|']
for i,r in enumerate(rows,1):
 t=r['totals']; passed=t['tests']-t['skipped']-t['errors']-t['failures']
 lines.append(f'| {i} | {passed} passed / {t["skipped"]} skipped / 0 failed / 0 errors | {r["start_pt"]} — {r["end_pt"]} | `{r["stdout_sha256"]}` | `{r["junit_sha256"]}` |')
lines+=['','## 提交范围','','仅第9条6生产文件、5测试文件、冻结第9条报告和证据目录，以及本次补充报告/证据。不包含Claude操作手册、第二十三批指令、item8研究、运行配置、数据库或缓存。旧v6lite-batch22不移动；本次将新增附注tag v6lite-batch22a。','','## SHA-256 清单','','新清单覆盖完整v6lite、全部第9条证据（包含三次全量JSON/TXT/XML和独立审签收据）、本补充报告与证据工具。排除新manifest本身和最终独立审签收据以避免循环；原manifest与冻结报告独立保留。','']
REPORT.write_text('\n'.join(lines)+'\n',encoding='utf-8')
files={p for p in (ROOT/'v6lite').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
files|={p for p in OLD.iterdir() if p.is_file()}
files|={p for p in EV.iterdir() if p.is_file() and p.name not in {'sha256_manifest.json','independent_review_receipt.json','commit_plan.json'}}
files|={ROOT/'docs/V6_LITE_BATCH22_ITEM9_FIX_REPORT_2026-10-09.md',REPORT}
manifest={'code_sha':EXPECTED,'entries':[entry(p) for p in sorted(files,key=lambda p:p.relative_to(ROOT).as_posix())],'full_gate':'PASS','excluded':['new manifest itself','commit_plan.json','new independent review receipt','runtime and User state','operator manual','Batch23 instructions','item8 research']}
(EV/'sha256_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
owned=[ROOT/p for p in SOURCE]+[ROOT/'docs/V6_LITE_BATCH22_ITEM9_FIX_REPORT_2026-10-09.md',REPORT]+list(OLD.iterdir())+[p for p in EV.iterdir() if p.name!='commit_plan.json']
plan={'intended_tag':'v6lite-batch22a','code_sha':EXPECTED,'commit_message':'V6-Lite batch 22a: broker cancellation diagnostics and P0 deduplication (code_sha f27e144de9eb406b)','stage_instruction':'git -c core.autocrlf=false add -f -- each exact allowlisted path; never add whole v6lite/docs directories; ignored logs are intentionally included', 'files':[entry(p) for p in sorted(set(owned),key=lambda p:p.relative_to(ROOT).as_posix()) if p.is_file()]}
(EV/'commit_plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'report_sha256':sha(REPORT),'manifest_sha256':sha(EV/'sha256_manifest.json'),'manifest_entries':len(manifest['entries']),'planned_files':len(plan['files']),'code_sha':EXPECTED,'runs':[r['totals'] for r in rows]},ensure_ascii=False))
