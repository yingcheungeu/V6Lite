"""Build 27b report from serial offline receipts; never read runtime state."""
from pathlib import Path
from datetime import datetime
import argparse,ast,hashlib,json,re,subprocess
ROOT=Path(__file__).resolve().parents[2]; EV=Path(__file__).resolve().parent

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,text): p.write_text(text,encoding='utf-8',newline='\n')
def dump(p,obj): write(p,json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def mask(text): return re.sub(r'\b(DU|U)([0-9]{5,})\b',lambda m:m[1]+'******'+m[2][-3:],text)
def git(*args): return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
def tree(path):
 h=hashlib.sha256()
 for p in sorted((p for p in path.rglob('*') if p.is_file() and '__pycache__' not in p.relative_to(path).parts),key=lambda p:p.relative_to(path).as_posix()):
  h.update(p.relative_to(path).as_posix().encode()); h.update(b'\0'); h.update(p.read_bytes()); h.update(b'\0')
 return h.hexdigest()

def main():
 assert ROOT==Path('C:/Dev/V6Lite-dev') and git('branch','--show-current')=='dev'
 assert git('rev-parse','HEAD')=='44a85f1f7856f2761370943ef4e2daec42e870fe'
 pre=json.loads((EV/'preflight.json').read_text(encoding='utf-8'))
 assert sha(ROOT/'docs/V6_LITE_OPERATOR_MANUAL.md')==pre['manual_sha256_preserved']
 inst=ROOT/'docs/V6_LITE_BATCH27B_INSTRUCTIONS.md';assert sha(inst)==pre['instruction_sha256']
 acceptance=json.loads((EV/'full_acceptance.json').read_text(encoding='utf-8'))
 runs=[json.loads((EV/(label+'.json')).read_text(encoding='utf-8')) for label in acceptance['runs']]
 code=tree(ROOT/'v6lite');assert code[:16]==acceptance['code_sha']
 assert len(runs)==3 and all(r['returncode']==0 and r['source_code_sha']==code[:16] and r['source_trees']['v6lite']==code and r['source_trees_unchanged'] and r['specs_unchanged'] and r['totals']['failures']==r['totals']['errors']==0 and r['totals']['skipped']==1 for r in runs)
 assert all(r['inventory']==runs[0]['inventory'] and r['cases']==runs[0]['cases'] for r in runs)
 old_receipt=json.loads((ROOT/'docs/batch27a_evidence/public/full_1.json').read_text(encoding='utf-8'))
 old=old_receipt['inventory']['collected']
 old_skip=[case for case in old_receipt['cases'] if case['outcome']=='skipped']
 assert len(old_skip)==1 and old_skip[0]['name']=='test_scoring_matches_legacy_scanner_on_identical_bars' and old_skip[0]['message']=='legacy market_scanner.py not present'
 assert all([case for case in r['cases'] if case['outcome']=='skipped']==old_skip for r in runs),'skip identity/reason changed'
 nodes=runs[0]['inventory']['collected'];assert len(old)==1803 and set(old)<=set(nodes)
 assert len(nodes)==len(set(nodes))==runs[0]['totals']['tests']
 baseline=pre['baseline_file_hashes'];legacy=[p for p in baseline if p.startswith(('v6lite/tests/','scripts/'))]
 assert all(sha(ROOT/p)==baseline[p] for p in legacy),'old tests/scripts changed'
 preserved=[p for p in baseline if p.startswith(('v6lite/examples/','v6lite/config','v6lite/risk','v6lite/sizing'))]
 assert all(sha(ROOT/p)==baseline[p] for p in preserved),'config/risk/examples changed'
 qualified=json.loads((EV/'qualified_red.json').read_text(encoding='utf-8'))
 red=json.loads((EV/(qualified['label']+'.json')).read_text(encoding='utf-8'))
 assert red['baseline'] and red['source_code_sha']=='b736734f8e73edfb' and red['totals']['failures']>0 and red['totals']['errors']==0
 assert qualified['required_positive_after_tp1_failed'] and qualified['raw_time_three_categories_failed']
 assert sha(EV/(qualified['label']+'.json'))==qualified['receipt_sha256']
 assert sha(EV/(qualified['label']+'.xml'))==qualified['junit_sha256']
 assert qualified['specs']==red['specs']
 for spec in qualified['specs']:
  spec_path=Path(spec['path']);assert spec_path.resolve().is_relative_to(ROOT.resolve()) and sha(spec_path)==spec['sha256'],'qualified red spec changed'
 public=EV/'public';public.mkdir(exist_ok=True);receipts=[]
 for f in sorted(EV.glob('*.json')):
  obj=json.loads(f.read_text(encoding='utf-8'))
  if not isinstance(obj,dict) or not ('label' in obj and 'inventory' in obj and 'totals' in obj):continue
  label=obj['label'];original={f.name:sha(f)}
  for suffix in ('.txt','.xml','_inventory.json','_progress.json','_cards.json','_reports.json','_query_summaries.json'):
   src=EV/(label+suffix)
   if src.exists():original[src.name]=sha(src);write(public/src.name,mask(src.read_text(encoding='utf-8')))
  item=json.loads(mask(json.dumps(obj,ensure_ascii=False)))
  for suffix,key in (('.txt','stdout_sha256'),('.xml','junit_sha256'),('_inventory.json','inventory_sha256')):
   src=public/(label+suffix)
   if src.exists():item[key]=sha(src)
  item['publication_redaction']={'rule':'All synthetic account-shaped values masked. Original receipts preserved locally and excluded from staging.','original_artifact_sha256':original}
  dump(public/f.name,item);receipts.append((label,obj))
 samples=[]
 for f in sorted(EV.glob('samples*_cards.json'),key=lambda p:p.stat().st_mtime):
  r=json.loads((EV/(f.name.removesuffix('_cards.json')+'.json')).read_text(encoding='utf-8'))
  if not r['baseline'] and r['returncode']==0 and r['source_code_sha']==code[:16]:samples.append((f,r))
 assert samples,'actual current-candidate card capture missing'
 cards=json.loads(samples[-1][0].read_text(encoding='utf-8'));selected=[];seen=set()
 sample_prefix=samples[-1][0].name.removesuffix('_cards.json')
 actual_reports=json.loads((EV/(sample_prefix+'_reports.json')).read_text(encoding='utf-8'))
 actual_summaries=json.loads((EV/(sample_prefix+'_query_summaries.json')).read_text(encoding='utf-8'))
 assert any(item.get('level')=='INFO' and all(part in item['text'] for part in ('returned=3','zoned=1','naive=1','unparseable=1','sample=')) for item in actual_summaries),'actual complete-query three-category INFO sample missing'
 phrase='当日成交时间按当日成交查询判定为今天（精确时间未核实）'
 for item in cards:
  text=item['plain'];title=item['embed']['title']
  if not ('过夜' in title or phrase in text):continue
  key=(title,phrase in text,'账本成交时间尚未核实' in text,'当日可见成交无法归属' in text)
  if key in seen:continue
  seen.add(key);selected.append(item)
 assert any(phrase in item['plain'] for item in selected),'day-only disclosure card missing'
 selected.sort(key=lambda item:(phrase not in item['plain'],'P1' not in item['embed']['title']))
 dump(EV/'rendered_cards.json',selected)
 day_reports=[item for item in actual_reports if phrase in item['markdown']]
 assert day_reports,'actual successful daily-report disclosure missing'
 dump(EV/'rendered_daily_reports.json',day_reports)
 dump(EV/'rendered_query_summaries.json',actual_summaries)
 daily_md='# 27b 实际本地日报样例\n\n'
 for item in day_reports:daily_md+='测试：`'+item['nodeid']+'`\n\n'+mask(item['markdown'])+'\n\n'
 write(EV/'rendered_daily_reports.md',daily_md)
 md='# 27b 实际离线卡片\n\nMemoryChannel 实际 AlertCard 渲染，账户已脱敏；不是手写样例。\n\n'
 for item in selected:md+='## '+item['embed']['title']+'\n\n测试：`'+item['nodeid']+'`\n\n```text\n'+mask(item['plain'])+'\n```\n\n'
 write(EV/'rendered_cards.md',md)
 changed=[p for p in git('diff','--name-only','--','v6lite').splitlines() if '/tests/' not in p]
 newpaths=git('ls-files','--others','--exclude-standard','--','v6lite').splitlines()
 changed+= [p for p in newpaths if '/tests/' not in p and '__pycache__' not in p]
 testfiles=sorted(p.relative_to(ROOT).as_posix() for p in (ROOT/'v6lite/tests').glob('test_batch27b*.py'))
 report_date=datetime.fromisoformat(runs[-1]['end_pt']).date().isoformat()
 report=ROOT/('docs/V6_LITE_FIX_REPORT_ROUND27B_'+report_date+'.md')
 lines=['# V6-Lite 第二十七批 27b 修复报告','',f'日期：{report_date} PT；基线 `v6lite-batch27a` / `44a85f1f7856f2761370943ef4e2daec42e870fe`。',f'最终 code_sha：`{code[:16]}`；完整包 SHA-256：`{code}`。','',
 '## 范围、调查与边界','',
 '- 指令 SHA-256：`95c851973e6e5a9563f8dd7fabed4be0cc95806b5a8f89e7aebf77f25e7c37e9`。先完成调查 `docs/V6_LITE_BATCH27B_INVESTIGATION_2026-10-16.md`，本地SDK及官方依据见 source_provenance.json。',
 '- 仅在 dev 开发；唯一实施 Agent 改生产，独立 Agent 写新测试，另一 Agent 只读复核；ROOT 串行测试。27a运行仓库合并验收是本批实施前完成的单独授权任务，不是27b改动。27b未连接 TWS/Gateway，未改运行仓库、John配置/用户状态/Claude手册，未push。',
 '- 第二十七批第1–3节继续排在27b之后；本批不声称已完成旧审计、日报限量或日志重整。','',
 '## 发现 → 改动 → 测试','',
 '| 项目 | 发现 | 改动与安全边界 | 证明 |','|---|---|---|---|',
 '| 当天未核时成交 | TP1 callback留下未核时标记，历史截止条件与d的精确秒比较继续冻结BE/TP2 | 仅过夜窄例外：严格九字段/持久化合约/provisional原串、同日且不晚于核验时刻，匹配本轮d可信完整范围中的同exec经济身份；只判断属于今天，不写精确核时结果 | 实际TP1→BE修改ACK→TP2减量ACK→TP2提交，逐步容量与账本只读强断言；缺exec/价/permId/日期/完整性否定 |',
 '| 查询范围 | 原方法没有time；默认午夜不能代表Trade Log扩大后的范围 | 仅专用过夜account scoped查询发送ET00对应UTC过滤，要求真实同reqId End、同代、新鲜、起止同ET日；普通查询、恢复/release/精确核时保留原范围，普通list不能授新权限 | 原生请求字节、夏冬令时、缺End/旧范围/无范围/跨日/换代；默认与扩展历史；首次TP前明确不完整scoped拒绝与旧接口兼容控制 |',
 '| 原始时间 | decoder在wrapper之前解析，坏字符串可能丢行且泄露完整字段日志 | 捕获原始时间并脱敏，UNVERIFIED/REVIEW保留raw_time；无法解析只诊断、不造Fill、不猜时间；坏行令本次新范围证据不完整，同reqId隔离 | 三种raw实际decoder→event→audit；新观测去重、原生汇总计数与账户脱敏、嵌套请求/坏callback隔离 |',
 '| 汇总与说明 | 精确秒可能仍未知，不能仅看marker声称已查询 | 每次原生完整查询一行INFO按原始时间分类；只有实际当次a–d成功才记录当日判定说明，卡片/日报明确精确时间未核实 | 带/不带时区/无法解析计数；真实成功说明与无完整scope不声称成功的反证 |',
 '| 两处卡片 | 未核时前缀重复；d失败前未记实际数量 | 前缀一次；完整查询条数在逐行归属失败时也可见 | 实际P1卡片仅一个前缀；a已通过的d未知成交计数 |','',
 '依据运行约束 A、B仍属于 John 接受的剩余风险，不是技术证明：A账户独占，人工停机→撤单→平仓→release；B账本不回滚。仍不能技术排除停机期间等量人工回转或与券商一致的旧账本回滚。','',
 '## 红绿与夹具有效性','',
 f'合格基线红收据 `{qualified["label"]}`：{red["totals"]}。要求的TP1后失败、三类原始时间失败已由qualified_red.json逐项引用；所有原始失败收据保留。',
 '初始E2E夹具未在query pacing等待时模拟行情更新，先被3秒报价门控挡住；校准后又暴露扩展历史下旧naive回报污染d的真实边界。默认午夜与扩展回溯须分别覆盖；d文字测试隔离a已通过的情况。上述未进入目标路径的失败不替代合格自然红，详见fixture_calibration.md。','',
 '独立复核随后补充3个明确原生intent/正conId冲突反例，先在首候选自然红，再由唯一实施Agent修复；另3个真实d卡片计数反例及3个首次TP前专用查询不完整反例先自然红再修复。专用查询已有明确完整性协议时不得退入legacy兼容，无可选scoped能力的旧接口正向控制保留。原35项锁spec未改，新增反例与源码字节绑定见review_edge_notes.md及相应public收据；不把它们冒充首次27a基线的同一测试集。','',
 '| 收据 | 源码 | tests / failures / errors / skipped |','|---|---|---|']
 for label,r in receipts:
  if r['full'] or r['collect_only']:continue
  z=r['totals'];lines.append(f'| {label} | {r["source_code_sha"]} | {z["tests"]} / {z["failures"]} / {z["errors"]} / {z["skipped"]} |')
 lines+=['','## Windows 默认全量连续三次','',f'Python `{runs[0]["python_version"].splitlines()[0]}`，`{runs[0]["python_executable"]}`。默认 v6lite/tests + scripts/tests，保留全部1803个27a完整nodeid，新增{len(nodes)-1803}个。原{len(legacy)}个测试/脚本和{len(preserved)}个配置/风险/样例文件逐字节未改。','',
 '| 次数 | PT开始 | PT结束 | 结果 |','|---|---|---|---|']
 for i,r in enumerate(runs,1):
  z=r['totals'];lines.append(f'| {i} | {r["start_pt"]} | {r["end_pt"]} | {z["tests"]-z["skipped"]} passed，{z["skipped"]} skipped；0 failed / 0 errors |')
 lines+=['','唯一遗留skip：`test_scoring_matches_legacy_scanner_on_identical_bars`（legacy market_scanner.py not present）。每次全量前重核PT，交易日06:00–13:30禁全量；Windows JobObject截止保护、禁优化/pyc、UTF8、隔离profile/temp、清除机密环境变量。三次源码、collection/executed完整nodeid及JUnit相同。','',
 '## 实际卡片','', '完整样例与nodeid见 `docs/batch27b_evidence/rendered_cards.md` / rendered_cards.json；实际本地日报见 rendered_daily_reports.md/json，原生INFO汇总见 rendered_query_summaries.json。','']
 for item in selected[:5]:lines+=['```text',mask(item['plain']),'```','']
 lines+=['## 生产引用','']
 for relative in changed:
  module=ast.parse((ROOT/relative).read_text(encoding='utf-8'))
  for node in ast.walk(module):
   if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and any(s in node.name for s in ('execution','overnight','raw_time','receipt','handle_event','record_fill','daily_report')):
    lines.append(f'- `{relative}:{node.lineno}` — `{node.name}`。')
 lines+=['','## 现场未知与后续','',
 '- [官方time过滤格式](https://www.interactivebrokers.com/docs/tws-api/protobuf/execution-filter)与[旧官方Trade Log范围说明](https://interactivebrokers.github.io/tws-api/executions_commissions.html)是文档依据；不是John当前TWS/SDK组合的现场实测。显式UTC过滤若拒绝或范围不明确则P1冻结，不改TimezoneTWS/解析规则、不无范围重试授权。',
 '- 现场callback/query原始时间字符串尚未知；本批只增加诊断证据，不能宣称精确时间已核实，也不能仅以完整可见查询证明账户所有历史。10/19复核合并时机及10/20实测由Claude/John决定。','',
 '- 复核提及RESTORE_PENDING旧total回报与刷新查询清空proof之间可能有静态交互；本轮没有真实调度失败实证，未为该假设改生产或声称已解决，留待专用验证。','',
 '## SHA-256 清单','', '整个最终v6lite/scripts、本批指令/调查和发布证据。report与manifest自身排除避免循环；原始未脱敏输出、旧临时材料、手册、配置和数据库不暂存。','']
 fixed=[inst,ROOT/'docs/V6_LITE_BATCH27B_INVESTIGATION_2026-10-16.md']
 names={'preflight.json','source_provenance.json','acceptance_process.py','run_batch27b_checks.py','sample_capture.py','build_batch27b_report.py','acceptance_checkpoint.json','full_acceptance.json','pre_full_review.json','final_review.json','rendered_cards.json','rendered_cards.md','fixture_calibration.md','source_compile.json','visible_card_checks.json','qualified_red.json','baseline_case_mapping.json','review_edge_notes.md','resume_preflight.json','rendered_daily_reports.json','rendered_daily_reports.md','rendered_query_summaries.json'}
 evidence=[p for p in EV.iterdir() if p.is_file() and p.name in names]+[p for p in public.iterdir() if p.is_file()]
 package=[p for name in ('v6lite','scripts') for p in (ROOT/name).rglob('*') if p.is_file() and '__pycache__' not in p.relative_to(ROOT).parts]
 manifest={p.relative_to(ROOT).as_posix():sha(p) for p in sorted(set(package+fixed+evidence))}
 lines+=['| 文件 | SHA-256 |','|---|---|']+[f'| `{p}` | `{h}` |' for p,h in manifest.items()]
 write(report,'\n'.join(lines)+'\n')
 dump(EV/'sha256_manifest.json',{'code_sha':code[:16],'full_code_sha256':code,'baseline':'v6lite-batch27a','status':'Windows full3 passed; '+('final review included' if (EV/'final_review.json').exists() else 'final independent review pending'),'sha256':manifest})
 stage=sorted(set(changed+testfiles+[p.relative_to(ROOT).as_posix() for p in fixed+evidence]+[report.relative_to(ROOT).as_posix(),'docs/batch27b_evidence/sha256_manifest.json']))
 dump(EV/'stage_plan.json',{'files':stage,'exclude':'manual, raw receipts, old temp/archive, configs, DB/WAL/SHM, cache and unrelated docs'})
 print(json.dumps({'code_sha':code[:16],'report':str(report),'report_sha256':sha(report),'manifest_items':len(manifest),'manifest_sha256':sha(EV/'sha256_manifest.json'),'stage_files':len(stage)},ensure_ascii=False,indent=2))
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--final',action='store_true',required=True);parser.parse_args();main()
