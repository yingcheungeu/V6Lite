"""Final batch27 sections1-3 report. Only reads development receipts and sources."""
from pathlib import Path
from datetime import datetime
import argparse,hashlib,json,re,subprocess,xml.etree.ElementTree as ET
from legacy_query_contract import expected_hashes
ROOT=Path(__file__).resolve().parents[2];EV=Path(__file__).resolve().parent
BASE='54a893ba655c8e04ef64cfcbb6d49a2ebcdcddaa'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def mask(s):return re.sub(r'\b(DFA|DU|DF|U|F|I)([0-9]{4,})\b',lambda m:m[1]+'******'+m[2][-3:],s)
def git(*a):return subprocess.check_output(['git',*a],cwd=ROOT,text=True).strip()
def tree(p):
 h=hashlib.sha256()
 for f in sorted((f for f in p.rglob('*') if f.is_file() and '__pycache__' not in f.relative_to(p).parts),key=lambda f:f.relative_to(p).as_posix()):
  h.update(f.relative_to(p).as_posix().encode());h.update(b'\0');h.update(f.read_bytes());h.update(b'\0')
 return h.hexdigest()
def main():
 assert ROOT==Path('C:/Dev/V6Lite-dev') and git('branch','--show-current')=='dev' and git('rev-parse','HEAD')==BASE
 pre=load(EV/'preflight.json');code=tree(ROOT/'v6lite');expected=expected_hashes(ROOT,pre['baseline_file_hashes'])
 assert sha(ROOT/'docs/V6_LITE_OPERATOR_MANUAL.md')==pre['manual_preserved_sha256']
 assert sha(ROOT/'docs/V6_LITE_BATCH27_INSTRUCTIONS.md')==pre['instruction_sha256']
 assert load(EV/'rulings.json')['status']=='resolved'
 legacy=[p for p in pre['baseline_file_hashes'] if p.startswith(('v6lite/tests/','scripts/'))]
 protected=[p for p in pre['baseline_file_hashes'] if p.startswith(('v6lite/examples/','v6lite/config','v6lite/risk','v6lite/sizing'))]
 assert all(sha(ROOT/p)==expected[p] for p in legacy+protected),'old tests/scripts/config changed'
 qualified=load(EV/'qualified_red.json')
 for x in qualified['runs']:
  r=load(EV/(x['label']+'.json'));assert sha(EV/(x['label']+'.json'))==x['receipt_sha256']
  assert r['totals']['failures']>0 and r['totals']['errors']==0 and r['source_code_sha']=={'b26':'579d13832e3f4237','b27b':'d6dd37c12701ce95'}[r['baseline']]
  assert all(sha(Path(s['path']))==s['sha256'] for s in r['specs'])
 ac=load(EV/'full_acceptance.json');runs=[load(EV/(n+'.json')) for n in ac['runs']]
 assert ac['code_sha']==code[:16] and len(runs)==3
 assert all(r['returncode']==0 and r['source_code_sha']==code[:16] and r['totals']['failures']==r['totals']['errors']==0 and r['source_trees_unchanged'] for r in runs)
 assert all(r['cases']==runs[0]['cases'] and r['inventory']==runs[0]['inventory'] for r in runs)
 old=load(ROOT/'docs/batch27b_evidence/public/full_1.json')
 assert len(old['inventory']['collected'])==1848 and set(old['inventory']['collected'])<=set(runs[0]['inventory']['collected'])
 assert [c for c in runs[0]['cases'] if c['outcome']=='skipped']==[c for c in old['cases'] if c['outcome']=='skipped']
 assert (EV/'pre_full_review.json').exists()
 public=EV/'public';public.mkdir(exist_ok=True);receipts=[];metrics={}
 for p in sorted(EV.glob('*.json')):
  r=load(p)
  if not isinstance(r,dict) or not all(k in r for k in ('label','inventory','totals')):continue
  name=r['label'];item=json.loads(mask(json.dumps(r,ensure_ascii=False)));original={p.name:sha(p)}
  for suffix,key in (('.xml','junit_sha256'),('_inventory.json','inventory_sha256'),('_cards.json',None),('_reports.json',None),('_query_summaries.json',None),('_attachments.json',None),('_saved_main.json',None)):
   src=EV/(name+suffix)
   if src.exists():
    original[src.name]=sha(src);dest=public/src.name;dest.write_text(mask(src.read_text(encoding='utf-8')),encoding='utf-8',newline='\n')
    if key:item[key]=sha(dest)
  for src in sorted(EV.glob(name+'_attachment_*.jsonl')):
   original[src.name]=sha(src);(public/src.name).write_text(mask(src.read_text(encoding='utf-8')),encoding='utf-8',newline='\n')
  src=EV/(name+'.txt')
  if src.exists():
   original[src.name]=sha(src)
   excerpt='\n'.join(line[:1600] for line in src.read_text(encoding='utf-8').splitlines()[:180])
   (public/(name+'_stdout_excerpt.txt')).write_text(mask(excerpt)+'\n',encoding='utf-8',newline='\n')
  item['publication_redaction']={'rule':'Synthetic account identifiers masked; complete JUnit/nodeids published. Raw stdout retained locally, capped excerpt published.','original_artifact_sha256':original}
  dump(public/p.name,item);receipts.append(r)
  xml=EV/(name+'.xml')
  if xml.exists():
   values={}
   for c in ET.parse(xml).getroot().iter('testcase'):
    props=c.find('properties')
    if props is not None:values[c.get('classname','')+'::'+c.get('name','')]={x.get('name'):x.get('value') for x in props}
   if values:metrics[name]=values
 dump(EV/'measured_metrics.json',metrics)
 sample_receipt=load(EV/'samples_final_green.json')
 assert sample_receipt['returncode']==0 and sample_receipt['source_code_sha']==code[:16]
 saved_samples=load(EV/'samples_final_green_saved_main.json')
 sample=next((x for x in saved_samples if 'B27-scale-SCAN' in x['markdown'] and '/audit-0/' in x['relative_test_source']),None);assert sample is not None
 (EV/'daily_report_first60.md').write_text('\n'.join(mask(sample['markdown']).splitlines()[:60])+'\n',encoding='utf-8',newline='\n')
 date=datetime.fromisoformat(runs[-1]['end_pt']).date().isoformat();report=ROOT/('docs/V6_LITE_FIX_REPORT_ROUND27_'+date+'.md')
 lines=['# V6-Lite 第二十七批：审计、日报、日志','',f'基线 `v6lite-batch27b` / `{BASE}`；最终code_sha `{code[:16]}`。',f'完整包SHA-256 `{code}`。','','## 调查及边界','',
 '生产修改前已交付调查 `docs/V6_LITE_BATCH27_SECTIONS123_INVESTIGATION_2026-10-17.md`；98个审计调用、73个kind及全部读取点/原文/行号见audit_inventory.json。日志来源与1/10持仓每小时估算、查询假设和现场统计差异见调查。',
 '仅dev；唯一Agent改生产，独立Agent写新测试及复核，ROOT串行执行。未连接TWS/Gateway，未读改运行仓库、John配置或用户状态；不push。27a/27b既有修复保留。',
 f'Claude手册仅核哈希 `{pre["manual_preserved_sha256"]}`，预存在的手册改动保留、不暂存。旧查询日志测试按新DEBUG契约仅替换两处级别，原文件/失败收据保留；其他{len(legacy)-1}个测试/脚本和{len(protected)}个配置/风险/示例文件逐字节不变。','','## John补充裁定（2026-10-18）','',
 '- “主报守上限，关键事件另附完整附件”。主报最终UTF-8 <=100KiB、Events <=200行；正常日报和停机补报须实际写出完整附件，主报给出引用。',
 '- “允许相同结果聚合；首次、变化和异常仍保留”。原27b逐次INFO要求据此更新；每次查询的原分类调试证据及所有查询/成交/核验行为保持。','','## 发现 → 改动 → 测试','',
 '| 发现 | 改动 | 证明 |','|---|---|---|',
 '| 重复价格及confirmed_at审计线性增长 | RAM仅压缩两种观察；状态UPDATE照常；真实字段变化/错误/新连接代首见保留 | 13h一/十持仓15s与1s压力、逐字段、错误、重启、generation、TP后续 |',
 '| 旧日报逐行载入与输出 | SQLite kind统计及连续详情分组；整份字节/Events行预算、准确遗漏数；关键原文实际附件 | 9万行<=5s和非线性Python内存；历史audit摘要不变；真实P0/关键溢出/正常及停机附件 |',
 '| QUERY与SDK逐条INFO | 成功QUERY DEBUG、30min JSON计数/max/drained；失败WARNING；wrapper WARNING | 实际SDK/wire/End、原异常对象/属性、真实owed End恢复、13h完整成交查询日志预算 |',
 '| 账号呈现在日志/日报 | 最终Formatter副本及报表脱敏；配置/记录/卡片内部不改 | account六族、child logger/traceback、原LogRecord及身份不变 |',
 '| 新observer事务/代际边界 | 多持仓summary原子写；原生清理先完成，再通知观察sink | 首候选多持仓失败重试自然红；native reset故障隔离反例 |','','## 红绿与控制','',
 '原3个冻结spec在b26及27b各69项：53自然行为失败、16原通过控制、0errors/0skip。virtual-tree证明当时工作树严格等于未改27b+仅3新spec；16控制不冒称先红。另7项精确规模/owed End在两基线各2失败5通过。',
 '既有批次回归 focused_previous_batches 为990项：981通过、9失败、0errors。9项是预算内原始长原因被截断（7项）、已入账前日成交混入次日报正文（1项）、附件目录写失败绕过实际停机日报渲染（1项）。保持旧测试字节和强断言不变，修复生产后重新验收；不是时间抖动，失败收据完整保留。',
 '首次全量full_1完整1946项：1943 passed、2 failed、1 skipped、0errors。两项均是第十批旧测试要求成功QUERY为INFO，与本批§2.3 DEBUG要求矛盾；不是产品回归/flake。只在该函数迁移两个日志级别token；原生drain/End/唯一日志/耗时≥1.65秒断言原样。legacy_contract_migration.json逐字证明其他所有字节不变，原候选checkpoint/review/source证明保存在*_2378.json。重新锁定后全量从第1次连续重跑，不计失败轮次。',
 '额外两项附件/当日诊断守卫属于独立静态发现后的通过控制，不声称先有候选自然红。其query-sourced replay只走真实Store入口，不拿它证明原生reqExecutions完整查询。该新增夹具初版复用了旧commission，合法引出第二次佣金更正，前置事件数失败；原字节保留，仅校准回放佣金为最新值，强断言不变。',
 '所有候选回归、无效夹具及校准均保留原收据；不得用缺API/collection错误作合格行为红。owed-End初版把native握手计数漏出基线，前置断言失败未到汇总；该红不作漏计统计行为证明。按握手后增量校准，重新跑隔离旧基线及候选，见fixture_calibration.md。完整证据表如下。','','| 收据 | 源码 | tests / failed / errors / skipped |','|---|---|---|']
 for r in receipts:
  z=r['totals'];lines.append(f'| {r["label"]} | {r["source_code_sha"]} | {z["tests"]} / {z["failures"]} / {z["errors"]} / {z["skipped"]} |')
 lines+=['','实测值见measured_metrics.json及完整public JUnit properties；baseline压力首次超限即停止，候选通过必须跑满13h。audit压力使用transport double+真实adapter/Engine/Store，日志压力另用installed SDK wrapper/client/原生请求字节与匹配End。',
 '10/15只有脱敏规模与样例payload；5条TP status和1条SCAN是合成标记，不声称取得现场完整payload或读取John数据库。新旧audit sink的对照核对真实卡片、a–d结论、经济表及订单调用。',
 '一个position/window仅一个OBSERVATION_SUMMARY envelope，数组分别保留kind/intent/count/first/last，13h稳定期<=30条。RAM不能授予交易权限；强制结束进程可能丢失未落盘的重复计数，历史audit/账本不迁移或改写。']
 lines+=['','## 体量实测','', '| 持仓数 | 查询秒数 | 价格audit | 状态audit | summary行 |','|---|---|---|---|---|']
 for props in metrics['samples_final_green'].values():
  if 'b27_audit_metrics' in props:
   m=json.loads(props['b27_audit_metrics']);lines.append(f'| {m["holdings"]} | {m["interval_seconds"]} | {m["target_writes"]["ORDER_PRICE_OBSERVATION"]} | {m["target_writes"]["PROTECT_STATE"]} | {m["summary_rows"]} |')
 perf=next(props for props in metrics['samples_final_green'].values() if 'b27_report_90000' in props)
 p90=json.loads(perf['b27_report_90000']);p10=json.loads(perf['b27_report_10000'])
 lines+=['','13h完整原生查询/成交高频日志源，1/10持仓与15s/1s四组均55条正常日志；原生请求/End完整数及异常逐条WARNING见JUnit，不冒称现场全天日志。其余固定日志源的估算在前置调查。', f'旧9万行日报：{p90["seconds"]:.3f}s、Python峰值{p90["python_peak_bytes"]}字节、materialized {p90["materialized_rows"]}行；一万行materialized {p10["materialized_rows"]}行，原历史audit不变。具体每次测量含full3见measured_metrics.json。']
 visible=load(EV/'visible_artifact_checks.json');saved=visible['saved_main_checked']
 lines.append(f'另逐份只读核对实际保存正文{len(saved)}份，最大{max(x["bytes"] for x in saved)}字节；完整附件{len(visible["attachments_checked"])}份，原id/ts/kind/payload与保存正文引用SHA核对通过；纯渲染捕获与最终metadata落盘分别记录，不混用。')
 lines+=['','## Windows全量连续三次','']
 for i,r in enumerate(runs,1):lines.append(f'{i}. {r["start_pt"]} → {r["end_pt"]}：{r["totals"]["tests"]-r["totals"]["skipped"]} passed，{r["totals"]["skipped"]} skipped，0failed/0errors。')
 lines+=['','完整保留27b的1848个nodeid；三次完整collection/executed nodeid与JUnit用例结果一致，源码锁一致；XML时间和耗时元数据可不同。唯一遗留skip为test_scoring_matches_legacy_scanner_on_identical_bars：legacy market_scanner.py not present。每次全量前核PT，交易日06:00–13:30禁全量；Windows JobObject截止保护、禁优化/pyc、UTF8、隔离profile/temp和机密环境。','','## 10/15 回放日报样例（实际保存，前60行）','',f'来源：`{sample["relative_test_source"]}`；原始Windows CRLF文件SHA-256 `{sample["source_sha256"]}`。展示字段统一为LF文本；原始字节与附件引用另核，完整保存正文在public/samples_final_green_saved_main.json。','','```markdown',*(EV/'daily_report_first60.md').read_text(encoding='utf-8').splitlines(),'```','','## SHA-256清单','',
 'report/manifest自身与引用两者哈希的最终独立复核证明不纳入自引用；原始stdout、cache、数据库、手册、John配置不暂存。完整JSON清单见sha256_manifest.json。','']
 names={'preflight.json','audit_inventory.json','qualified_red.json','rulings.json','acceptance_process.py','run_batch27_checks.py','sample_capture.py','build_batch27_report.py','measured_metrics.json','daily_report_first60.md','acceptance_checkpoint.json','full_acceptance.json','pre_full_review.json','source_compile.json','certify_batch27.py','legacy_query_contract.py','legacy_contract_migration.json','legacy_query_debug_contract_migration.json','legacy_query_debug_contract_migration.diff','acceptance_checkpoint_2378.json','pre_full_review_2378.json','source_compile_2378.json','visible_artifact_checks_2378.json','g3t_20261015_volume.md','review_edge_notes.md','visible_artifact_checks.json','check_visible_artifacts.py','fixture_calibration.md','qualified_red_initial.json','spec_calibration_2026-10-18.md'}
 evidence=[p for p in EV.iterdir() if p.is_file() and p.name in names]
 evidence += [p for p in (EV/'initial_specs').glob('*.py') if p.is_file()]
 fixed=[ROOT/'docs/V6_LITE_BATCH27_INSTRUCTIONS.md',ROOT/'docs/V6_LITE_BATCH27_SECTIONS123_INVESTIGATION_2026-10-17.md']
 package=[p for n in ('v6lite','scripts') for p in (ROOT/n).rglob('*') if p.is_file() and '__pycache__' not in p.relative_to(ROOT).parts]
 manifest={p.relative_to(ROOT).as_posix():sha(p) for p in sorted(set(package+fixed+evidence+list(public.iterdir())))}
 lines+=['| 文件 | SHA-256 |','|---|---|']+[f'| `{p}` | `{h}` |' for p,h in manifest.items()]
 report.write_text('\n'.join(lines)+'\n',encoding='utf-8',newline='\n')
 dump(EV/'sha256_manifest.json',{'baseline':'v6lite-batch27b','code_sha':code[:16],'full_code_sha256':code,'sha256':manifest})
 print(json.dumps({'report':str(report),'report_sha256':sha(report),'manifest_sha256':sha(EV/'sha256_manifest.json'),'items':len(manifest),'code_sha':code[:16]},ensure_ascii=False))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--final',action='store_true',required=True);p.parse_args();main()
