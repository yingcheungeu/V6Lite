"""Seal verified B23 report/manifest. Does not stage or alter production."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib,json,subprocess,xml.etree.ElementTree as ET,sys
ROOT=Path(__file__).resolve().parents[2];EV=Path(__file__).resolve().parent
sys.path.insert(0,str(EV))
from run_batch23_checks import sha,package_sha,preserve
from evidence_validation import validate_record
preserve();code=package_sha(ROOT/'v6lite')
assert not (ROOT/'docs/V6_LITE_FIX_REPORT_ROUND23_2026-10-09.md').exists() and not (EV/'sha256_manifest.json').exists(),'final artifacts already sealed'
records=[]
for p in EV.glob('full_*.json'):
 r=json.loads(p.read_text(encoding='utf-8'))
 if 'start_pt' in r:records.append(r)
records.sort(key=lambda r:r['start_pt']);full=records[-3:]
assert len(full)==3
for index,r in enumerate(full,1):
 assert r['label'].startswith('full_'+str(index))
 validate_record(r,require_before=True)
 assert r['specs']==full[0]['specs']
 assert r['returncode']==0 and r['source_code_sha']==code and r['source_unchanged'] and r['worktree_unchanged']
 assert r['totals']['failures']==r['totals']['errors']==0
 assert sha(EV/(r['label']+'.txt'))==r['stdout_sha256'] and sha(EV/(r['label']+'.xml'))==r['junit_sha256']
 dt=datetime.fromisoformat(r['start_pt']);assert not(dt.weekday()<5 and (6,0)<=(dt.hour,dt.minute)<(13,30))
 if index>1:assert r['start_pt']>=full[index-2]['end_pt']
 assert r['cases']==full[0]['cases']
 assert r['totals']==full[0]['totals']
 greens=[]
for p in EV.glob('green*.json'):
 r=json.loads(p.read_text(encoding='utf-8'))
 if r.get('source_code_sha')==code and r.get('returncode')==0:greens.append(r)
assert greens;green=max(greens,key=lambda r:r['start_pt'])
validate_record(green,require_before=True)
assert green['totals']=={'tests':110,'failures':0,'errors':0,'skipped':0}
focused_records=[json.loads(p.read_text(encoding='utf-8')) for p in EV.glob('focused*.json')]
focused=max((r for r in focused_records if r['source_code_sha']==code and r['returncode']==0),key=lambda r:r['start_pt'])
validate_record(focused,require_before=True)
assert focused['totals']=={'tests':553,'failures':0,'errors':0,'skipped':0}
capacity_records=[json.loads(p.read_text(encoding='utf-8')) for p in EV.glob('legacy_capacity*.json')]
capacity_records=[r for r in capacity_records if 'start_pt' in r and 'source_code_sha' in r and 'returncode' in r]
capacity=max((r for r in capacity_records if r['source_code_sha']==code and r['returncode']==0),key=lambda r:r['start_pt'])
validate_record(capacity,require_before=True)
assert capacity['totals']=={'tests':1,'failures':0,'errors':0,'skipped':0}
assert capacity['select_expression']=='test_race_final_sell_boundary_counts_other_working_sells'
review=json.loads((EV/'independent_production_review.json').read_text(encoding='utf-8'))
assert review['verdict']=='PASS' and review['code_sha']==code
reds=[json.loads(p.read_text(encoding='utf-8')) for p in EV.glob('red_22a*.json')]
reds=[r for r in reds if r.get('totals')=={'tests':73,'failures':66,'errors':0,'skipped':0}]
assert reds
red=max(reds,key=lambda r:r['start_pt'])
validate_record(red,allow_legacy_process_metadata=True)
red_spec_hashes={Path(i['path']).name:i['sha256'] for i in red['specs']}
green_spec_hashes={Path(i['path']).name:i['sha256'] for i in green['specs']}
assert all(green_spec_hashes.get(name)==digest for name,digest in red_spec_hashes.items())
assert red['source_code_sha']=='f27e144de9eb406b' and red['totals']=={'tests':73,'failures':66,'errors':0,'skipped':0}
candidate_review=json.loads((EV/'review_red_candidate.json').read_text(encoding='utf-8'))
baseline_review=json.loads((EV/'review_red_22a.json').read_text(encoding='utf-8'))
validate_record(candidate_review,allow_legacy_process_metadata=True);validate_record(baseline_review,allow_legacy_process_metadata=True)
assert candidate_review['totals']=={'tests':21,'failures':21,'errors':0,'skipped':0}
assert baseline_review['totals']=={'tests':21,'failures':19,'errors':0,'skipped':0}
assert baseline_review['source_code_sha']=='f27e144de9eb406b'
assert candidate_review['specs'][0]['sha256']==green_spec_hashes['test_batch23_error_provenance.py']
assert baseline_review['specs'][0]['sha256']==green_spec_hashes['test_batch23_error_provenance.py']
boundary_candidate=json.loads((EV/'boundary_red_candidate.json').read_text(encoding='utf-8'))
boundary_baseline=json.loads((EV/'boundary_red_22a.json').read_text(encoding='utf-8'))
validate_record(boundary_candidate);validate_record(boundary_baseline)
assert boundary_candidate['totals']==boundary_baseline['totals']=={'tests':8,'failures':7,'errors':0,'skipped':0}
assert boundary_baseline['source_code_sha']=='f27e144de9eb406b'
assert boundary_candidate['specs'][0]['sha256']==boundary_baseline['specs'][0]['sha256']==green_spec_hashes['test_batch23_unprotected.py']
diagnostic_candidate=json.loads((EV/'diagnostic_red_candidate.json').read_text(encoding='utf-8'))
diagnostic_baseline=json.loads((EV/'diagnostic_red_22a.json').read_text(encoding='utf-8'))
validate_record(diagnostic_candidate,require_before=True);validate_record(diagnostic_baseline,require_before=True)
assert diagnostic_candidate['totals']==diagnostic_baseline['totals']=={'tests':6,'failures':6,'errors':0,'skipped':0}
assert diagnostic_baseline['source_code_sha']=='f27e144de9eb406b'
assert diagnostic_candidate['specs'][0]['sha256']==diagnostic_baseline['specs'][0]['sha256']==green_spec_hashes['test_batch23_broker_diagnostics.py']
fact_candidate=json.loads((EV/'fact_red_candidate.json').read_text(encoding='utf-8'))
fact_baseline=json.loads((EV/'fact_red_22a.json').read_text(encoding='utf-8'))
validate_record(fact_candidate,require_before=True);validate_record(fact_baseline,require_before=True)
assert fact_candidate['totals']=={'tests':1,'failures':1,'errors':0,'skipped':0}
assert fact_baseline['totals']=={'tests':1,'failures':0,'errors':0,'skipped':0} and fact_baseline['source_code_sha']=='f27e144de9eb406b'
assert fact_candidate['specs'][0]['sha256']==fact_baseline['specs'][0]['sha256']==green_spec_hashes['test_batch23_terminal_fact.py']
recovery_candidates=[json.loads(p.read_text(encoding='utf-8')) for p in EV.glob('recovery_red_candidate*.json')]
recovery_baselines=[json.loads(p.read_text(encoding='utf-8')) for p in EV.glob('recovery_red_22a*.json')]
recovery_candidate=max(recovery_candidates,key=lambda r:r['start_pt'])
recovery_baseline=max(recovery_baselines,key=lambda r:r['start_pt'])
assert 'the full original exception' in recovery_candidate['cases'][0]['message']
assert recovery_baseline['cases'][0]['name']=='test_B23_real_recovery_query_failure_keeps_full_ANOMALY_in_daily_and_retains_UNKNOWN_hold'
assert "assert rig.store.control('anomaly:recovery_failed') == cause" in (EV/(recovery_baseline['label']+'.txt')).read_text(encoding='utf-8')
validate_record(recovery_candidate,require_before=True);validate_record(recovery_baseline,require_before=True)
assert recovery_candidate['totals']==recovery_baseline['totals']=={'tests':1,'failures':1,'errors':0,'skipped':0}
assert recovery_baseline['source_code_sha']=='f27e144de9eb406b'
assert recovery_candidate['specs'][0]['sha256']==recovery_baseline['specs'][0]['sha256']==green_spec_hashes['test_batch23_recovery_diagnostic.py']
impl=json.loads((EV/'implementation_summary.json').read_text(encoding='utf-8'))
cards=json.loads((EV/'card_samples.json').read_text(encoding='utf-8'))
card_receipt=json.loads((EV/'card_samples_receipt.json').read_text(encoding='utf-8'))
assert len(cards)==card_receipt['scenarios']==5
assert card_receipt['code_sha']==code and card_receipt['source_unchanged'] is True
assert card_receipt['render_sha256']==sha(EV/'render_samples.py')
assert card_receipt['card_json_sha256']==sha(EV/'card_samples.json') and card_receipt['card_markdown_sha256']==sha(EV/'card_samples.md')
assert {Path(i['path']).name:i['sha256'] for i in card_receipt['specs']}==green_spec_hashes
assert [c['oracle_exchange_truth']['P'] for c in cards]==[3,6,8,4,5]
assert len({c['scenario'] for c in cards})==5
assert sum(len(c['accepted_embeds']) for c in cards)==card_receipt['actual_acknowledged_cards']
for c in cards:
 assert c['synthetic'] and c['broker_connections']==0
 assert c['messages'] and len(c['messages'])==len(c['accepted_embeds'])
 o=c['oracle_exchange_truth'];assert o['S']+o['T']+o['X']<=o['P']
 assert [m['title'] for m in c['messages']]==[e['title'] for e in c['accepted_embeds']]
for i in green['specs']:assert sha(Path(i['path']))==i['sha256']
scope_notes=json.loads((EV/'scope_notes.json').read_text(encoding='utf-8'))
source=[]
for directory in ('v6lite','scripts'):
 source += [p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.relative_to(ROOT).parts and '.pytest_cache' not in p.relative_to(ROOT).parts]
for p in source:
 assert p.suffix.lower() in {'.py','.toml','.md','.ps1','.cmd'},'unknown source artifact '+str(p)
 assert p.stat().st_size<10*1024*1024 and p.name!='.env'
 if p.suffix.lower()=='.toml':assert p.parent==ROOT/'v6lite/examples' and p.name in {'live.toml','paper.toml','paper_delayed.toml','paper_g3n.toml','paper_test.toml'}
evidence=[p for p in EV.rglob('*') if p.is_file() and not any(k in {'baseline22a','test_tmp','sample_tmp','__pycache__'} for k in p.relative_to(EV).parts) and p.suffix.lower() in {'.py','.md','.json','.txt','.xml','.log'} and p.name not in {'sha256_manifest.json','commit_plan.json','independent_final_receipt.json'}]
paths=sorted(set(source+evidence+[ROOT/'docs/V6_LITE_BATCH23_INSTRUCTIONS.md']),key=lambda p:p.relative_to(ROOT).as_posix())
for p in paths:assert p.resolve().is_relative_to(ROOT.resolve()) and p.relative_to(ROOT).as_posix()==p.resolve().relative_to(ROOT.resolve()).as_posix()
entries=[{'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in paths]
instruction=(ROOT/'docs/V6_LITE_BATCH23_INSTRUCTIONS.md').read_text(encoding='utf-8');rulings=instruction.partition('## 0 裁定（原文记入报告）')[2].partition('## 1 实施清单')[0].strip()
lines=['# V6-Lite 第23批修复报告','', '日期：2026-10-09。范围：仅dev；离线FakeBroker/native callback doubles，未取得真实Paper成交/延迟证据。','',f'- 基线：`v6lite-batch22a`，commit `d9f57dca5ed73f001f9c68052f5fe27b8abc933e`，code_sha `f27e144de9eb406b`。',f'- 本批 code_sha：`{code}`。','- 指令第2版 SHA-256：`04708b69d6b3c410b487fb440f97a98790ce47bc0ad8dd3b23d471dc0592860c`。','', '## 0 裁定原文','',rulings,'','## 1 发现 → 改动 → 测试','']
for item in impl['changes']:
 lines += ['### '+item['item'],'','- 发现：'+item['finding'],'- 改动：'+item['change'],'- 测试：'+item['tests'],'']
lines += ['## 2 分工与安全边界','','- 唯一生产实施：batch20_protection_incident；独立测试：batch20_equivalents；只读独立调查/复核：batch20_time_logs；root负责串行执行、docs证据与git。','- 先核生产34项基线并在22a归档初始有效红，再授权生产修改；后来修正夹具的最终spec另在未变的22a归档重跑自然红，原始红测保留并区分时间顺序。最终验证期间各写入者暂停。','- 经济入账与核心权限门控的AST比较、release强核验分支的独立逐行复核、所有SDK构造与原因截断搜索见独立生产复核证据。release仅把80字失败预览改为全文并在两轮账户执行查询catch增加全文审计；去掉新增审计并归一化该预览叶后强核验流程与基线等价，整个方法AST已不同，不声称全方法原样。','- 未连接TWS/Gateway，不读取用户状态/归档，不改运行仓库、John配置或Claude手册；不push。','- '+scope_notes['workflow_read_note'],'','## 3 红绿与Windows全量','','有效红：73项，66行为断言失败、7守卫通过，0 errors；多个用例共享业务前置，不等于66个独立根因。源f27e144de9eb406b保持不变。首两轮红测夹具问题和首轮候选绿测（含未知cap、尾零显示、缺少permId的Fake回报和未推进真实tick的恢复前置问题）原始输出保留，不作为最终门槛。修正后的新测试在22a重新自然失败，才用于最终专项绿。','独立新增21项：初版候选21失败/0errors；22a对照19失败、2既有终态全文守卫通过。辅助审计旧版经济入账通过而缺新审计；候选回滚被独立捕获。与已有业务前置共享的失败不夸大为独立根因。全部原始结果保留。','追加独立8项：修复前候选与22a均7失败、1既有取消事实守卫通过，0errors；覆盖无保护TP拒价、损坏报价快照、中文价域拒绝、release完整失败原因及取消/拒绝事实一致性。新断言固定后再授权生产修复。','另6项真实broker异常诊断红测代表submit/modify/cancel、执行查询及release两轮查询。未动态覆盖的open_orders、positions_fresh与TP超时撤单catch另经独立静态逐行复核，不把6项宣称为全部9路径动态覆盖。','Inactive+202混合终态新增1个既有取消事实守卫：候选自然失败、22a对照通过；修复仅展示分类，不授予订单权限。','恢复查询全文detail日报出口另有1项候选/22a自然红；首轮误把ENTRY正常预建计划当作不应存在，改为对该计划完整等值/零物理STP写入守卫后重新自然红，首轮不计门槛。有效候选红到达真实catch入库完整而日报截断；22a有效红在更早的anomaly control全文断言失败（旧catch只保存前200字）。最终保留异常入库及日报全文，恢复逻辑和权限不变。',f"专项绿：{green['totals']['tests']} passed，0 failed、0 errors、0 skipped；同源聚焦553 passed，另1项真实TP超卖边界回归passed。首全量1453passed/1failed/1skipped，旧手造TP缺定价证据；改用真实TP流程后重新启动连续3轮验收，未把失败算通过。",'','| 连续全量 | PT起止 | passed | skipped | failures/errors |','|---|---|---:|---:|---:|']
for i,r in enumerate(full,1):
 t=r['totals'];passed=t['tests']-t['failures']-t['errors']-t['skipped']
 lines.append(f"| {i} | {r['start_pt']} — {r['end_pt']} | {passed} | {t['skipped']} | 0 / 0 |")
skip_cases=[c for c in full[0]['cases'] if c['outcome']=='skipped']
lines += ['', '实际跳过项：'+ ('；'.join(c['classname']+'.'+c['name']+' — '+c['message'] for c in skip_cases) if skip_cases else '无。')]
lines += ['','每轮开始前重新检查交易时间；默认v6lite/tests与scripts/tests一起执行；禁用外部pytest自动插件及缓存写入，临时合成文件均限定在dev证据目录。三轮同code_sha/用例及结果一致，串行且无重叠。','', '历史记录格式说明：red_22a_run5、review_red_candidate、review_red_22a三份早期红测只记录v6lite运行进程为0，未采集不可检查Python进程数或spec前后标志；不补写或把缺字段当0。原JSON以固定SHA、源码前后未变、原XML逐用例及冻结spec哈希验证。仅这三份历史红允许显式格式兼容；最终110专项、553聚焦、单项容量和连续全量三轮仍严格要求两类进程数为0及spec前后未变。封存工具的元数据筛选和历史格式修正另附证据，并纳入独立复核。','','## 4 设计与保留的边界','','- 自动止盈暂停按position和拒价episode持久化；resume-tp仅排队并审计，engine证据完备且全仓STP恢复后才解除。旧episode/hold/freeze/UNKNOWN/损坏控制/价差门控不能被命令清掉；不立即下TP。','- 首次旧报价只在真实止盈报价决策轮累计纯证据counter（不把未到30秒调度期的调用算作跳过），交易状态、尝试次数和保护数量不变，每分钟最多一条日志；第二次失败恢复原保护并累计原B窗口，不按价格拒绝暂停。','- positive STP cap与auxPrice属于不同物理量，单独审计；LMT cap与发送限价差异按P1/标的阻重发；未知cap保持未知。真实working aux/lmt合同差异仍按订单purpose分流，绝不跟随cap改账本。','- 旧test-tp-timeout不能再通过ask加价制造超时；原有排队control也不能覆盖新鲜bid定价。超时验证由FakeBroker持单完成，10/30秒阈值保留。','- 相同已ACK拒价事件不重复P0/P1；未ACK仅补未送达分段。Alerter独立事件直调测试只证明未全局静音，自动触发由既有回归及静态独立复核补证。','- Paper真实价域、成交速度/滑点与实盘最长空窗≤5秒仍需后续经授权演练测量；本报告的合成数据不作真实成交保证。','','## 5 实际场景卡片','','以下正文来自实际Alerter渲染和本地通道ACK，不手造卡片。每场景独立数量；自动暂停样例同时列P0发生时和全仓恢复后的数量，未新增同事件低级别卡。','',(EV/'card_samples.md').read_text(encoding='utf-8'),'','## 6 John运行配置需改的行','','以下仅列指令要求；未读取或修改John的配置。','', '- 删除旧`[protection] limit_offset_pct`（沿用第22批）。','- `v6lite.paper.toml`中`exit_limit_band_pct = 0.01`改为`exit_limit_band_pct = 0.002`。','- `v6lite.g3t.toml`没有[execution]段，使用新默认0.002和tp_max_quote_age_seconds=3，无需新增段。','- 新参数允许整数1–10并纳入指纹；示例配置及默认解析用专项验证。','','## 7 SHA-256清单','',f'下表{len(entries)}项；伴随JSON清单另外包含本报告哈希，清单自身不自引用。完整红绿/全量逐用例记录、独立审签和实际卡片JSON一并留证。','', '| 文件 | 字节 | SHA-256 |','|---|---:|---|']
lines += [f"| `{e['path']}` | {e['bytes']} | `{e['sha256']}` |" for e in entries]
lines += ['','## 8 提交与冻结范围','','任务提交仅包括本批改动的v6lite源码/示例/测试、本指令和新报告及本批证据。Claude手册与item8研究原有未提交文件保留；不包含运行配置、数据库、缓存、临时fixture目录、22a归档源码副本或baseline ZIP。提交与附注tag v6lite-batch23完成后，不再修改工作树，等待Claude复核。']
report=ROOT/'docs/V6_LITE_FIX_REPORT_ROUND23_2026-10-09.md';report.write_text('\n'.join(lines).rstrip()+'\n',encoding='utf-8')
entries.append({'path':report.relative_to(ROOT).as_posix(),'bytes':report.stat().st_size,'sha256':sha(report)})
manifest={'batch':23,'baseline_tag':'v6lite-batch22a','baseline_code_sha':'f27e144de9eb406b','code_sha':code,'full_gate':'PASS','full_labels':[r['label'] for r in full],'entries':entries,'excluded_from_commit':['docs/batch23_evidence/baseline22a','docs/batch23_evidence/baseline22a.zip','docs/batch23_evidence/test_tmp','docs/batch23_evidence/sample_tmp','Claude operator manual','John runtime configs','item8 research (pre-existing preserved)']}
(EV/'sha256_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'code_sha':code,'manifest_entries':len(entries),'report_sha256':sha(report),'manifest_sha256':sha(EV/'sha256_manifest.json'),'full_results':[r['totals'] for r in full]},ensure_ascii=False))
