"""Freeze and close out offline 30 evidence; no broker, commit or tag actions."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter
import ast,hashlib,json,subprocess,sys,xml.etree.ElementTree as ET

ROOT=Path('C:/Dev/V6Lite-dev');OUT=ROOT/'docs/batch30_evidence'
REPORT=ROOT/'docs/V6_LITE_FIX_REPORT_ROUND30_2026-10-26.md'
BASE='8200445318afb3a56c76e2569c1cef851963e557'
INSTRUCTION='97f1e552228934a2a3b851fcfef2cb64ce6af93f585b1781d5236629b8913493'
ACCEPTED={'docs/V6_LITE_FIX_REPORT_ROUND29_2026-10-23.md':'8617eab4aad72cc76b7bdd68320bc5bbf334c8ab04cb1dbf2a6a4d5d0d8b26c3'}
PT=ZoneInfo('America/Los_Angeles')
sys.path.insert(0,str(ROOT))
from v6lite.telemetry import redact_accounts
from v6lite.version import code_sha
OWNED=['v6lite/commissions.py','v6lite/report_evidence.py','v6lite/config.py','v6lite/risk.py',
 'v6lite/engine.py','v6lite/report.py','v6lite/test_tp_timeout.py','v6lite/protection_engine.py',
 'v6lite/tests/test_review_fixes.py','v6lite/tests/test_batch20_protection_equivalents.py',
 'v6lite/tests/test_batch15.py','v6lite/tests/test_batch16_gap_evidence.py',
 'v6lite/tests/test_batch29_native_timeout.py','docs/V6_LITE_BATCH30_INSTRUCTIONS.md']
OWNED += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'v6lite/tests').glob('test_batch30_*.py'))]
TOOLS=['v6lite_b30_validate.py','v6lite_b30_progress.py','v6lite_b30_closeout.py','v6lite_b30_git_deliver.py']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,obj):p.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
def inventory():
 return {p.relative_to(ROOT).as_posix():sha(p) for base in ('v6lite','scripts')
  for p in sorted((ROOT/base).rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
def package_sha():
 h=hashlib.sha256()
 for p in sorted((ROOT/'v6lite').rglob('*'),key=lambda p:p.relative_to(ROOT/'v6lite').as_posix()):
  if p.is_file() and '__pycache__' not in p.parts:
   h.update(p.relative_to(ROOT/'v6lite').as_posix().encode());h.update(b'\0');h.update(p.read_bytes());h.update(b'\0')
 return h.hexdigest()
def check_base():
 assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==BASE
 assert subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()=='dev'
 assert sha(ROOT/'docs/V6_LITE_BATCH30_INSTRUCTIONS.md')==INSTRUCTION
 for name,digest in ACCEPTED.items():assert sha(ROOT/name)==digest,name
def dependencies():
 import importlib.metadata
 dist=importlib.metadata.distribution('ib_insync')
 return {'version':dist.version,'sources':{name:sha(Path(dist.locate_file('ib_insync/'+name)))
  for name in ('wrapper.py','decoder.py','client.py','ib.py')}}

def collect(before):
 import os,re
 targets=[OUT/'collection.txt',OUT/'collection.json']
 assert not any(p.exists() for p in targets),'preserve collection evidence; use explicit new freeze revision'
 env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1','PYTEST_ADDOPTS':''}
 result=subprocess.run([sys.executable,'-B','-m','pytest','v6lite/tests','scripts/tests','--collect-only','-q'],
  cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 raw=result.stdout.decode('utf-8',errors='replace');targets[0].write_bytes(redact_accounts(raw).encode('utf-8'))
 assert result.returncode==0,'collection failed: inspect collection.txt'
 nodes=[line.replace('\\','/') for line in raw.splitlines() if line.startswith(('v6lite/','scripts/')) and '::' in line]
 new=[node for node in nodes if '/test_batch30_' in node]
 assert len(nodes)==len(set(nodes))==2167+len(new) and len(new)>=51,(len(nodes),len(new))
 assert inventory()==before
 value={'tests':len(nodes),'new_tests':len(new),'baseline_tests':2167,
  'nodeids_sha256':hashlib.sha256('\n'.join(nodes).encode()).hexdigest(),
  'delivered_log_sha256':sha(targets[0]),'returncode':result.returncode,'source_unchanged':True}
 write(targets[1],value)
 return value

def receipt(label,full=False):
 r=json.loads((OUT/f'{label}.json').read_text())
 assert r['returncode']==r['failures']==r['errors']==0 and r['source_unchanged'] and not r['incomplete'],label
 assert r['tests']>0 and r['tests']>r['skipped']
 xml=ET.parse(OUT/f'{label}.xml').getroot()
 cases=list(xml.iter('testcase'))
 assert len(cases)==r['tests'] and not list(xml.iter('failure')) and not list(xml.iter('error'))
 assert sum(c.find('skipped') is not None for c in cases)==r['skipped']
 assert sha(OUT/f'{label}.xml')==r['delivered_XML_sha256']
 assert sha(OUT/f'{label}.txt')==r['delivered_log_sha256']
 if full:
  assert r['args']==['v6lite/tests','scripts/tests']
  at=datetime.fromisoformat(r['start_pt']).astimezone(PT)
  assert at>=datetime(2026,10,26,13,30,tzinfo=PT)
  assert not(at.weekday()<5 and (6,0)<=(at.hour,at.minute)<(13,30))
 return r
def report(freeze,runs):
 focused=receipt(freeze['focused_label'])
 props={p.get('name'):p.get('value') for p in ET.parse(OUT/(freeze['focused_label']+'.xml')).iter('property')}
 cards={name:props[name] for name in ('b30_closed_card','b30_cooldown_card','b30_fee_sign_change_card','b30_close_estimate_basis')}
 write(OUT/'cards_sample.json',{'source_receipt':freeze['focused_label'],'XML_sha256':focused['delivered_XML_sha256'],**cards})
 full='\n'.join(f"| {r['label']} | {r['tests']-r['skipped']} passed / {r['skipped']} skipped | {r['seconds']}s | {r['start_pt']} → {r['end_pt']} |" for r in runs)
 history=[]
 for p in sorted(OUT.glob('*.json')):
  obj=json.loads(p.read_text())
  if isinstance(obj,dict) and 'label' in obj and 'returncode' in obj:
   history.append(f"| {obj['label']} | rc={obj['returncode']} / passed={obj.get('tests',0)-obj.get('failures',0)-obj.get('errors',0)-obj.get('skipped',0)} / fail={obj.get('failures',0)} / error={obj.get('errors',0)} / skip={obj.get('skipped',0)} | incomplete={obj.get('incomplete')} / source_unchanged={obj.get('source_unchanged')} |")
 status='三次 Windows 全量通过；准备本地 dev 提交及 annotated v6lite-batch30，交付后冻结等待复核。' if runs else '源码已冻结；三次 Windows 全量尚待执行，尚未提交/tag。'
 text=f"""# V6-Lite 第三十批修复报告

日期：2026-10-26（PT）。{status}
基线：v6lite-batch29 / {BASE}；基线 code_sha 1bd6b193aedae3bc。
指令 SHA-256：{INSTRUCTION}。
候选 code_sha：{freeze['code_sha']}。
完整 package SHA-256：{freeze['package_sha256']}。

## 佣金调查：确定结论与现场证据限度

指定 exec 0000dc8f.6bef670d.01.01 的现场根因未能唯一判定。已获得的是指令中的脱敏日志摘要，未获得 10/26 原始 API 回调 DEBUG/完整日志/运行 DB，也没有访问被禁止的运行目录或连接券商。
SDK 的 commissionReport INFO 行会被现有过滤器降为 DEBUG；日志没有这类行不能证明 TWS 没推送。FILL_NOT_BOOKED commission_changed=False 只说明那一次 record_fill 重放没有新增/修改费用，既可能是 None，也可能与账本相同，不是 native 回报是否到达的证据。
源码确认 Store 能保留先于本地 fill 的佣金；连接前已接事件，重连未发现漏订阅。安装的 ib_insync 0.9.86 在没有 Trade 时，只更新 canonical Fill 的费用而不发 commissionReportEvent；若 report 先于任何 canonical Fill 则被 SDK 忽略。原生 decoder/reqId/Future/execDetailsEnd 回放已验证两种顺序。已有 executions_today 能从 canonical cache 取回费用，但原引擎只有未核实成交时间的周期查询，缺少平仓佣金主动闭环。
具体源码哈希、观察与推断边界见 investigation.json；安装 SDK 四个源文件哈希也已冻结。不能把覆盖这两种机制的修复伪称已证实 TWS 侧故障。

## 最终改动与规则边界

1. 只对已入账、已关闭当日持仓的缺佣金，主动走既有 paced executions_today 通道。精确核验 execId、orderRef、symbol、side、quantity、price、orderId、permId，补查只 record_commission；不重记成交、数量、时间，也不把查询注册为 TP/过夜授权证据。未知/不匹配行不采用。
2. 每交易日持久记录尝试时间，失败/重启也消耗同一 300 秒槽；时钟倒退保守跳过。截止实际 session.close（正常日 13:00 PT，半日遵循日历），不是测试约束的 13:30 PT。保护优先、非稳态、空窗、在途非保护订单或排队退出时不补查；_protect 前后重核。原有必须的启动/对账/时间核验请求不被这条新维护节流替代。
3. 缺佣金账务估算不再使用 sizing 的每笔至少 $2 预留。新增 risk.commission_estimate_min_usd，默认 $1.00，必须有限且>0；同标的、同方向、同数量，近 30 天最多 20 笔实际佣金取中位数并以该最低值兜底。关闭时逐 exec 固定金额、来源和样本 ID；新历史/新配置不会追溯改写旧估算。真实费用 0 或负返佣按现有 adapter 语义入账，估算不会为 0。
4. 平仓卡同时显示已回报净值、估算费用和估算净值；冷却卡列出每笔持仓 ID、净值和是否含估算。亏损阈值与冷却时长不变。已触发截止时间持久保留，负→正费用翻号只 INFO/审计、不自动解除；真实费用由盈转亏仍保留既有 R7 的保守亏损判定，未削弱门控。两条旧 L4 自动解除断言按第 30 批明确裁定更新。
5. 最终费用到账后账本/日报用实际值。同号不多发卡，翻号 INFO 一次。已写出的当日本地日报/附件在正常报告阶段静默刷新，不从回调写文件、不重发远程日报/平仓/冷却卡；成功后的新费用可立即收口，只有文件失败重试受一分钟节流。已知 OPEN/未绑定成交的当天费用也更新顶部统计，原异常保留。
6. TEST_TP_TIMEOUT_RESULT 只记录生命周期变化，正常原生流程最多 8 行；等待 status 不重复，迟到成交按 execId 区分，P0 在原处置之后加观察性审计，不改变发单/撤单/保护/止盈规则。B29 价格、最后一档禁注入、B10/B30 规则保持。
7. 日报分列 B（正常）和 B（测试注入），≤5 秒只评价正常 B。豁免必须匹配历史持久 claim、实际 sent intent、原保护/工作流/数量/测试价格；弱标记/坏 claim/不完整证据仍算正常。跨日继续按完整窗口归属，不把六秒拆成两个合规三秒。四处旧显示匹配更新了标签，数值断言保留。
8. 本机完整日报原已存在独立 Test TP timeout 段；远程十记录卡原未显示该段。没有实际截图/文件，不能断言 10/26 截图来自哪一种输出；现远程 Orders 提示测试次数及本机完整生命周期。相对 bid 原值为空的确定原因是初始未核实回调的不可变 fill audit 没随完整查询更新；报告现在连接当前已核实账本和原始发送 bid 计算，保留原审计。缺控制项可恢复唯一精确原 send；否则具体说明缺快照、无效 bid 或未核实时间。

未改 sizing、资金预留、daily-stop day_pnl、下单价格/数量/费用门、STP/TP状态机、冷却阈值/时长、时间解析器。未连接 TWS/Gateway、不发真实订单、不改用户 .v6lite、运行仓库、John 配置或操作手册。

## 红绿、复核与时序记录

原始 b29 的 192 文件 inventory 与 tag 归档逐字节相同后，red_commission_1 的 3 例、red_report_audit_2 的 6 例全部按所需缺陷先失败；原始外部测试源码、JSON/XML/log 保留。首个 red_report_audit_1 因复制工具 OUT 路径未改而未进入测试（0 case），记录已保留并仅搬回任务新产物，不计产品红测。
后续所有失败都保留：原 send 控制缺失时 workflow 为空的 fallback 真问题、NULL position 日报和旧估算随配置漂移，都加强后先红再修；native 佣金夹具先只 drain 关闭，保护优先尚未完成，维护正确 defer，后来改用真实 Engine.tick，不改旗标/证据/门控。一次文本补丁前置匹配不唯一使修改中断，shell 仍启动了测试，失败如实保留，此后依赖步骤独立检查退出码。
focused_regression_1 为332通过/2旧B标签匹配失败，完整窗口数值断言保留修正。focused_final_1 为334通过/1旧b29 late-fill计时失败：Windows 上两个独立 RTT timer 过期后同轮执行，late_fill_at==cancel_at。首见即修夹具：在实际 late-fill 交付之后再安排剩余半 RTT 的 Cancelled，保留严格 cancel_wire<late_fill_at<cancel_at 和所有经济/SDK断言，不靠重试隐藏。详见 b29_late_timer_flake_repair.json。
red_published_report_1 和 red_refresh_edges_1 又证明已发布本机日报、60秒内新费用/停机、OPEN/未绑定费用的收口缺口后修复。具体每次结果以本目录回执为准；不将确定夹具失败或显示预期更新伪称产品回归，不放宽第三次同一时序flake必须修复或明确重定义门槛的规则。
最终聚焦 {focused['label']}：{focused['tests']-focused['skipped']} passed / {focused['skipped']} skipped，{focused['seconds']} 秒，源码不变。
按 10/26 数据的三股离线 Engine 调度回放：毛3.84、已佣3.01、缺一笔估算1.00、已回报净值0.83/估算净值-0.17；补回1.00后最终-0.17且无额外卡；补回0.01后最终+0.82，INFO一次、原冷却保留。TP1 bid345.84/成交345.87 得+0.0300，TP2 bid346.81/成交346.81 得+0.0000。受控原生回放还验证普通B 2.063/2.051秒与注入14.256秒分列。

## Windows 连续三次串行全量

| 回执 | 结果 | 耗时 | PT起止 |
|---|---|---|---|
{full or '| pending | 尚未执行 | - | - |'}

冻结 {len(freeze['inventory'])} 文件；{freeze['compile_count']} 个 Python compile/UTF8 AST/无BOM通过。collect-only 明确收集 {freeze['expected_test_count']} 例（原2167 + 新{freeze['new_test_count']}），三次完整XML必须同一数量和case身份、rc/failure/error=0、同一inventory、source_unchanged和日志/XML哈希。SDK源文件、验证工具也不变化。
全量只在交易日13:30PT以后或周末执行，实际起止/串行顺序见回执。legacy scanner 缺失的既有skip与 record_property/xunit2 格式警告如存在，逐项以XML实际记录为准。

## 实际新卡片样例

关闭卡：
```text
{cards['b30_closed_card']}
```
冷却卡：
```text
{cards['b30_cooldown_card']}
```
翻号 INFO：
```text
{cards['b30_fee_sign_change_card']}
```
原始 XML property、关闭估算来源见 cards_sample.json。

## 全部验证回执

| 标签 | 实际结果 | 完整性/源码 |
|---|---|---|
{chr(10).join(history)}

## 交付

SHA256SUMS.json 列出任务源码、报告和脱敏证据。仅精确 allowlist 暂存，本地dev提交并打annotated v6lite-batch30；校验tag归档全部inventory、完整package/code_sha、报告及manifest blob哈希。交付后冻结等待复核；John本机合并，未merge/push/部署。既有独立操作手册改动不属于本批，不暂存。
"""
 REPORT.write_bytes(text.encode('utf-8'))

def redact():
 path=OUT/'redaction_receipt.json';records=json.loads(path.read_text()) if path.exists() else {}
 for p in sorted(OUT.iterdir()):
  if p.suffix not in ('.txt','.xml','.json') or p.name in ('SHA256SUMS.json','redaction_receipt.json'):continue
  raw=p.read_bytes();masked=redact_accounts(raw.decode('utf-8'))
  if masked!=raw.decode('utf-8'):
   p.write_bytes(masked.encode('utf-8'))
   records.setdefault(p.name,[]).append({'input_sha256':hashlib.sha256(raw).hexdigest(),'output_sha256':sha(p),'rule':'redact_accounts','synthetic_fixture_only':True})
 write(path,records)

mode=sys.argv[1];check_base()
if mode=='prepare':
 assert not (OUT/'freeze.json').exists(),'preserve old freeze, use explicit re-freeze provenance if source changes'
 focused=receipt(sys.argv[2]);before=inventory();count=0
 for name in before:
  if name.endswith('.py'):
   raw=(ROOT/name).read_bytes();assert not raw.startswith(b'\xef\xbb\xbf'),name
   compile(raw,name,'exec');ast.parse(raw.decode('utf-8'),filename=name);count+=1
 assert before==inventory()
 baseline=json.loads((OUT/'baseline.json').read_text())['package_inventory']
 assert not {p for p,h in before.items() if baseline.get(p)!=h and p not in OWNED},'unowned source changes'
 collection=collect(before)
 assert focused['inventory_sha256']==hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest()
 freeze={'status':'candidate frozen; three serial full runs pending','frozen_at_pt':datetime.now(PT).isoformat(),
  'base_commit':BASE,'base_tag':'v6lite-batch29','instructions_sha256':INSTRUCTION,'accepted_reports':ACCEPTED,
  'focused_label':focused['label'],'expected_test_count':collection['tests'],'new_test_count':collection['new_tests'],'dependencies':dependencies(),'code_sha':code_sha(),'package_sha256':package_sha(),
  'owned_files':{p:sha(ROOT/p) for p in OWNED},'inventory':before,
  'inventory_sha256':hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
  'compile_count':count,'compile_method':'compile(source_bytes,path,exec) + UTF8 ast.parse + no BOM; no writes/imports',
  'validation_tools':{n:sha(OUT/n) for n in TOOLS},'operator_manual_excluded':True,'full_tests_not_run_before_1330':True}
 write(OUT/'freeze.json',freeze);report(freeze,[])
 print(json.dumps({k:freeze[k] for k in ('code_sha','package_sha256','compile_count','inventory_sha256')}))
elif mode=='final':
 freeze=json.loads((OUT/'freeze.json').read_text())
 assert inventory()==freeze['inventory'] and code_sha()==freeze['code_sha'] and package_sha()==freeze['package_sha256']
 assert all(sha(OUT/n)==h for n,h in freeze['validation_tools'].items())
 assert dependencies()==freeze['dependencies'],'installed native SDK changed after freeze'
 labels=sys.argv[2:] or ['final_full_1','final_full_2','final_full_3'];assert len(labels)==3
 runs=[receipt(label,True) for label in labels]
 assert all(r['platform'].startswith('Windows-') and r['tests']==freeze['expected_test_count'] and r['code_sha']==freeze['code_sha'] for r in runs)
 assert all(datetime.fromisoformat(r['start_pt'])>=datetime.fromisoformat(freeze['frozen_at_pt']) for r in runs)
 assert all(json.loads((OUT/(r['label']+'_inventory.json')).read_text())==freeze['inventory'] for r in runs)
 identities=[sorted((c.get('classname'),c.get('name')) for c in ET.parse(OUT/(r['label']+'.xml')).iter('testcase')) for r in runs]
 assert identities[0]==identities[1]==identities[2]
 assert all(r['inventory_sha256']==freeze['inventory_sha256'] for r in runs)
 assert len({(r['tests'],r['skipped']) for r in runs})==1
 assert all(runs[i]['end_pt']<runs[i+1]['start_pt'] for i in (0,1))
 candidate_failures=Counter()
 for p in OUT.glob('*.json'):
  r=json.loads(p.read_text())
  if isinstance(r,dict) and r.get('args')==['v6lite/tests','scripts/tests'] and runs[0]['start_pt']<=r.get('start_pt','')<=runs[-1]['start_pt']:
   assert r.get('returncode')==0,'intervening full failure cannot be hidden'
  if isinstance(r,dict) and r.get('args')==['v6lite/tests','scripts/tests'] and r.get('inventory_sha256')==freeze['inventory_sha256']:
   candidate_failures.update((c['classname'],c['name']) for c in r.get('failed_cases',[]) if c['kind'] in ('failure','error'))
 assert not any(n>=3 for n in candidate_failures.values()),'same test failed three full runs on this unchanged candidate: repair and refreeze, or obtain an explicit operator gate redefinition'
 report(freeze,runs);redact()
 selected=[p for p in sorted(OUT.iterdir()) if p.is_file() and p.name not in ('SHA256SUMS.json','closeout.json')]
 files=[ROOT/p for p in OWNED]+[REPORT]+selected
 write(OUT/'SHA256SUMS.json',{'code_sha':freeze['code_sha'],'package_sha256':freeze['package_sha256'],
  'files':{p.relative_to(ROOT).as_posix():sha(p) for p in files}})
 write(OUT/'closeout.json',{'base_commit':BASE,'intended_tag':'v6lite-batch30','code_sha':freeze['code_sha'],
  'package_sha256':freeze['package_sha256'],'report_sha256':sha(REPORT),'full_labels':labels,
  'same_inventory':True,'serial_full_runs':3,'source_unchanged':True,'all_full_after_1330_pt':True,
  'operator_manual_staged':False,'merge_push_deploy':False,'manifest_sha256':sha(OUT/'SHA256SUMS.json')})
 files += [OUT/'SHA256SUMS.json',OUT/'closeout.json']
 write(Path('C:/tmp/v6lite_b30_stage_paths.json'),[p.relative_to(ROOT).as_posix() for p in files])
 print(json.dumps({'files':len(files),'report_sha256':sha(REPORT),'code_sha':freeze['code_sha']}))
elif mode=='redact':redact()
else:raise SystemExit('prepare <focused-label>, final [three-full-labels], or redact only')
