"""Freeze and close out offline 28d evidence; no broker, commit or tag actions."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter
import ast,hashlib,json,subprocess,sys,xml.etree.ElementTree as ET

ROOT=Path('C:/Dev/V6Lite-dev');OUT=ROOT/'docs/batch28d_evidence'
REPORT=ROOT/'docs/V6_LITE_FIX_REPORT_ROUND28D_2026-10-22.md'
BASE='a95f31f764e112bff50b922b43467bb7aeb44530'
INSTRUCTION='3ba0dceebe73eab3b0020a76e4214061fc68c98839683cee542c822a30115dd8'
ACCEPTED={'docs/V6_LITE_FIX_REPORT_ROUND28C_2026-10-21.md':
 '46362c47022536cd118f1045fc1818bcf98553468eaa9dc93542cb31efd53841'}
PT=ZoneInfo('America/Los_Angeles')
sys.path.insert(0,str(ROOT))
from v6lite.telemetry import redact_accounts
from v6lite.version import code_sha
OWNED=['v6lite/engine.py','v6lite/models.py','v6lite/protection_engine.py',
 'v6lite/protection_confirmations.py','v6lite/broker/ibkr.py','v6lite/final_tp.py',
 'docs/V6_LITE_BATCH28D_INSTRUCTIONS.md']
OWNED += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'v6lite/tests').glob('test_batch28d_*.py'))]
TOOLS=['v6lite_b28d_validate.py','v6lite_b28d_progress.py','v6lite_b28d_closeout.py','v6lite_b28d_git_deliver.py']
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
 assert sha(ROOT/'docs/V6_LITE_BATCH28D_INSTRUCTIONS.md')==INSTRUCTION
 for name,digest in ACCEPTED.items():assert sha(ROOT/name)==digest,name
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
  assert at>=datetime(2026,10,22,13,30,tzinfo=PT)
  assert not(at.weekday()<5 and (6,0)<=(at.hour,at.minute)<(13,30))
 return r
def report(freeze,runs):
 focused=receipt(freeze['focused_label'])
 props={p.get('name'):p.get('value') for p in ET.parse(OUT/(freeze['focused_label']+'.xml')).iter('property')}
 latency=props.get('b28d_cancel_ACK_to_TP3_wire_seconds','see native replay receipt')
 gap=props.get('b28d_final_B_wire_seconds','see native replay receipt')
 full='\n'.join(f"| {r['label']} | {r['tests']-r['skipped']} passed / {r['skipped']} skipped | {r['seconds']} s | {r['start_pt']} → {r['end_pt']} |" for r in runs)
 status='三次 Windows 全量通过，准备 dev 本地提交与 annotated tag，交付后冻结待复核。' if runs else '源码冻结；三次 Windows 全量尚未执行，未提交/tag。'
 text=f'''# V6-Lite 第二十八批 28d 修复报告

日期：2026-10-22（PT）。{status}

基线：`v6lite-batch28c` / `{BASE}` / code_sha `737c9ad3b19d8211`。
指令 SHA-256：`{INSTRUCTION}`。
候选 code_sha：`{freeze['code_sha']}`；完整 package SHA-256：`{freeze['package_sha256']}`。

## 调查与改动

1. 最后一股撤单原路径在 Cancelled 后先完成一轮四源查询，再做第二轮确认原 GTC 缺席，最后在发 TP 前再查一轮。
client executions 与当日账户可见 executions 共用带结束后 1 秒冷却的查询通道，多轮累计数秒。
28c 真实原生 SDK 回放 measured Cancelled→实际 conn.sendMsg 为 8.469 秒，重现指令中的约 8 秒等待。
现在撤单前保留当前完整证据，绑定 workflow、原 STP 强身份/数量、socket、修改代、账本经济行、所有已知 SELL 和 hold/freeze/UNKNOWN。
本进程物理撤单后、同 socket、同强身份、未发生额外经济/订单变化且原因只为正常取消的原生 Cancelled/ApiCancelled 可一次性延续授权，直接发最后一档 TP。
不会刷新原查询的时间、范围或 full_exec；最多等请求后 2 秒且原 proof 不超过 60 秒。
连接快照、自行查询回报、旧 socket/旧时间、不完整身份、10148 等撤单错误均不能充当快速确认。
无 ACK/授权失效继续原完整核验与恢复路径。b23 报价新鲜度、bid 限价、S+T+X≤P、暂停门、实际发送容量和 wire deadline 保留。

2. 新增持久事实只记本系统自行撤单请求、严格确认、绑定的最后一档 TP 及完整入账后不可变经济行 digest。
旧完整查询与本系统最终 TP 成交交错时，只在净账本 P=0、原 STP 自行取消已确认、绑定 TP 完整成交、无其他卖单/未知成交/异常/hold 下废弃旧轮并审计。
明确的 ProtectionEvidenceContradiction 仍不适用 blanket 豁免；未知 SELL、未知成交、非零净账本、原 STP 矛盾回报保留 P0。
终态原 STP 的矛盾检测依据有效的已确认自行撤单收据，不再要求已生成 TP，也不局限于 TP_ACTIVE。
报价失效确定未发、拒单和恢复阶段的新原生矛盾同样 hold/UNKNOWN/P0；在确认之后开始的完整四源查询再次查到原单工作中，也在恢复决策之前冻结，禁止修改该已取消旧 ID。
旧查询、旧 socket、连接/查询快照和精确重复取消仍保留原边界；正常新 STP 使用新身份，不匹配原撤单收据。
正常最后 TP 平仓后 protection 进入 CANCELLED，S=0、清除 TP/B 绑定；不伪称 STP FILLED，也不在 P=0 后重新挂保护。

3. TP 成交通知使用明确标题：例如 `✅ TP2 已成交 · AAPL · 1 股 @346.33`。
数量和均价来自该 position/stage 已入账的真实 SELL fills；沿用原成交去重键，不为无实际 TP 成交的阶段生成假卡片。

## 红绿与边界验证

首轮 `red_batch28c_initial` 为 17 项、14 failed / 3 passed，保留原始失败及输出/脱敏哈希。
其中 native8 的 BE 准备阶段曾将 spontaneous openOrder 与在途 own query 重复混入同轮；两项 closed-negative 还存在测试期望命名或注入前置问题。
只校准服务器准备顺序、native foreign ID 匹配与矛盾回报注入，未降低 TP3 延迟/容量/告警门槛；前后 SHA 见 fixture_calibration.json。
校准单例在仍为 28c 的生产源码上完整走完 TP1→仅 openOrder BE→TP2→取消→TP3，实际延迟 8.469 秒而失败，并出现平仓后误报 P0。
校准完整17项为7个目标失败、10通过；额外 query-snapshot 原生回放也先红，证明旧 StatusEvent 缺创建时查询角色。
首轮实施 `green_core_1` 因新入口漏引用 TP_PURPOSES 而10失败，尚未发出最终撤单；改为既有 Purpose 显式集合后 `green_core_2` 10通过。原失败及修复前后 SHA 保留于 fix_reference.json。
首轮聚焦 `focused_final_1` 653通过后，独立只读复核发现撤单已确认但 TP 确定未发时的恢复阶段告警缺口。
新增真实自发回报与完整自身查询两个反例，`red_restore_contradiction_1` 两项均因缺少 P0 失败（旧路径只出现 P1）；补齐收据级矛盾检测后 `green_restore_contradiction_1` 两项通过。断言未修改，修复前后 SHA 见 fix_restore_contradiction.json。
全部红测、后续真实失败均保留唯一标签，不覆盖，不把夹具失败伪称产品回归；详见各 JSON/XML/txt 回执。

最终聚焦 `{focused['label']}`：{focused['tests']-focused['skipped']} passed / {focused['skipped']} skipped，{focused['seconds']} 秒，source_unchanged=true。
同组原生回放 Cancelled→实际 TP3 wire 为 {latency} 秒，B 为 {gap} 秒，分别满足 ≤1 秒、≤5 秒。
包含真实原生 request/Future/End/conn.sendMsg、独立经济容量观察、旧 P1 与 TP3 fill/positionEnd 交错、正常回调闭仓、未知卖单/成交、非零账本、取消拒绝/超时/旧socket、明确 TP1/TP2/TP3 标题及 b22–b28c 回归。
没有替换授权函数、直接写 status_seen_generation、手工改 SQLite 经济账本或伪造查询成功。

Windows 全量：

| 回执 | 结果 | 耗时 | 起止 PT |
|---|---|---|---|
{full or '| 待执行 | 三次连续通过前不提交/tag | — | — |'}

每次串行、returncode=0、完整 XML、同一 inventory、source_unchanged=true；均在 10/22 13:30 PT 之后启动。
冻结 inventory {len(freeze['inventory'])} 个文件；{freeze['compile_count']} 个 Python 源码 compile、UTF8 ast.parse 及无 BOM 检查通过。
既有 legacy market_scanner.py 缺失的 parity 测试可继续 skip；record_property/xunit2 是报告格式警告。
第三次同一时序 flake 必须修复或由用户明确重定义门槛，不能重试掩盖。

## 交付与边界

freeze.json 固定源码、工具、指令、已接受报告与完整 inventory；SHA256SUMS.json 包含源码、报告及脱敏证据。
只暂存 C:/tmp/v6lite_b28d_stage_paths.json 所列任务文件；本地 dev commit + annotated tag `v6lite-batch28d`。
John 本机合并；不 merge、不 push、不部署，不连接 TWS/Gateway、不发真实订单，不碰用户 .v6lite/运行仓库/John 配置。
Claude 操作手册的既有改动不修改、不暂存。交付后冻结等待 Claude 复核。
'''
 REPORT.write_text(text,encoding='utf-8')
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
 assert focused['inventory_sha256']==hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest()
 freeze={'status':'candidate frozen; three serial full runs pending','frozen_at_pt':datetime.now(PT).isoformat(),
  'base_commit':BASE,'base_tag':'v6lite-batch28c','instructions_sha256':INSTRUCTION,'accepted_reports':ACCEPTED,
  'focused_label':focused['label'],'code_sha':code_sha(),'package_sha256':package_sha(),
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
 labels=sys.argv[2:] or ['final_full_1','final_full_2','final_full_3'];assert len(labels)==3
 runs=[receipt(label,True) for label in labels]
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
 write(OUT/'closeout.json',{'base_commit':BASE,'intended_tag':'v6lite-batch28d','code_sha':freeze['code_sha'],
  'package_sha256':freeze['package_sha256'],'report_sha256':sha(REPORT),'full_labels':labels,
  'same_inventory':True,'serial_full_runs':3,'source_unchanged':True,'all_full_after_1330_pt':True,
  'operator_manual_staged':False,'merge_push_deploy':False,'manifest_sha256':sha(OUT/'SHA256SUMS.json')})
 files += [OUT/'SHA256SUMS.json',OUT/'closeout.json']
 write(Path('C:/tmp/v6lite_b28d_stage_paths.json'),[p.relative_to(ROOT).as_posix() for p in files])
 print(json.dumps({'files':len(files),'report_sha256':sha(REPORT),'code_sha':freeze['code_sha']}))
elif mode=='redact':redact()
else:raise SystemExit('prepare <focused-label>, final [three-full-labels], or redact only')
