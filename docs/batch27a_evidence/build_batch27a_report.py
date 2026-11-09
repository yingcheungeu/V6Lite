"""Build reviewed 27a deliverables from real offline receipts; no broker/state access."""
from pathlib import Path
import argparse,hashlib,json,re,subprocess,ast
ROOT=Path(__file__).resolve().parents[2];EV=Path(__file__).resolve().parent
REPORT=ROOT/'docs/V6_LITE_FIX_REPORT_ROUND27A_2026-10-16.md'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,s): p.write_text(s,encoding='utf-8',newline='\n')
def dump(p,obj): write(p,json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def mask(s): return re.sub(r'\b(DU|U)([0-9]{5,})\b',lambda m:m[1]+'******'+m[2][-3:],s)
def tree(p):
 h=hashlib.sha256()
 for f in sorted((x for x in p.rglob('*') if x.is_file() and '__pycache__' not in x.relative_to(p).parts),key=lambda x:x.relative_to(p).as_posix()):
  h.update(f.relative_to(p).as_posix().encode());h.update(b'\0');h.update(f.read_bytes());h.update(b'\0')
 return h.hexdigest()
def git(*args): return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
def main():
 assert git('branch','--show-current')=='dev'
 assert git('rev-parse','HEAD')=='78a0905e0c45482cf6db7427016231b69144422d'
 pre=json.loads((EV/'preflight.json').read_text(encoding='utf-8'))
 assert sha(ROOT/'docs/V6_LITE_OPERATOR_MANUAL.md')==pre['manual_sha256_preserved_only']
 for p,h in pre['instructions'].items(): assert sha(ROOT/p)==h
 acceptance=json.loads((EV/'full_acceptance.json').read_text(encoding='utf-8'))
 runs=[json.loads((EV/(label+'.json')).read_text(encoding='utf-8')) for label in acceptance['runs']]
 code=tree(ROOT/'v6lite');assert code[:16]==acceptance['code_sha']
 assert len(runs)==3
 assert all(r['returncode']==0 and r['source_code_sha']==code[:16] and r['source_trees']['v6lite']==code and r['source_trees_unchanged'] and r['totals']['failures']==r['totals']['errors']==0 and r['totals']['skipped']==1 for r in runs)
 assert all(r['cases']==runs[0]['cases'] and r['inventory']==runs[0]['inventory'] for r in runs)
 old=json.loads((ROOT/'docs/batch26_evidence/full_1_inventory.json').read_text(encoding='utf-8'))['collected']
 nodes=runs[0]['inventory']['collected'];assert len(old)==1717 and set(old)<=set(nodes)
 assert len(nodes)==len(set(nodes))==runs[0]['totals']['tests']
 baseline=json.loads((EV/'baseline_file_hashes.json').read_text(encoding='utf-8'))
 legacy=[p for p in baseline if p.startswith(('v6lite/tests/','scripts/'))]
 assert all(sha(ROOT/p)==baseline[p] for p in legacy),'legacy tests/scripts changed'
 # Publish masked copies; never overwrite the original execution receipts.
 public=EV/'public';public.mkdir(exist_ok=True)
 receipts=[]
 for f in sorted(EV.glob('*.json')):
  obj=json.loads(f.read_text(encoding='utf-8'))
  if not isinstance(obj,dict) or not ('label' in obj and 'inventory' in obj and 'totals' in obj): continue
  label=obj['label'];original_hashes={f.name:sha(f)}
  for suffix in ('.txt','.xml','_inventory.json','_progress.json'):
   src=EV/(label+suffix)
   if src.exists():
    original_hashes[src.name]=sha(src);write(public/src.name,mask(src.read_text(encoding='utf-8')))
  item=json.loads(mask(json.dumps(obj,ensure_ascii=False)))
  for suffix,key in (('.txt','stdout_sha256'),('.xml','junit_sha256'),('_inventory.json','inventory_sha256')):
   src=public/(label+suffix)
   if src.exists(): item[key]=sha(src)
  item['publication_redaction']={'rule':'All account-shaped values including synthetic fixtures masked; original receipts retained locally and not staged.','original_artifact_sha256':original_hashes}
  dump(public/f.name,item);receipts.append((label,obj))
 # Actual rendered fake-channel cards, never a composed illustration.
 sample_files=sorted(EV.glob('samples*_cards.json'),key=lambda p:p.stat().st_mtime)
 assert sample_files,'actual FakeBroker card capture missing'
 sample_label=sample_files[-1].name.removesuffix('_cards.json')
 sample_receipt=json.loads((EV/(sample_label+'.json')).read_text(encoding='utf-8'))
 assert sample_receipt['returncode']==0 and sample_receipt['source_code_sha']==code[:16]
 cards=json.loads(sample_files[-1].read_text(encoding='utf-8'))
 interesting=[];seen=set()
 for item in cards:
  title=item['embed']['title'];text=item['plain'];key=(title,'未经券商查询核实' in text,'未执行（a 未通过）' in text,'佣金未计' in text,'前收盘' in text)
  if key in seen: continue
  if any(word in title for word in ('过夜','已关闭','已解除')) or ('前收盘' in text and '已启动' in title):
   seen.add(key);interesting.append(item)
 def priority(item):
  text=item['plain'];title=item['embed']['title']
  if '未经券商查询核实' in text: return 0
  if '未执行（a 未通过）' in text: return 1
  if '佣金未计' in text: return 2
  if '已解除' in title: return 3
  if '前收盘' in text and '已启动' in title: return 4
  return 5
 interesting.sort(key=priority)
 dump(EV/'rendered_cards.json',interesting)
 sample_md='# 27a 实际 FakeBroker 卡片\n\n来自隔离离线测试真实 MemoryChannel 的 AlertCard.as_embed()/as_plain_text()；账户字段已脱敏，不是重写卡片。\n\n'
 for item in interesting:
  sample_md+='## '+item['embed']['title']+'\n\n测试：`'+item['nodeid']+'`\n\n```text\n'+mask(item['plain'])+'\n```\n\n'
 write(EV/'rendered_cards.md',sample_md)
 changed=[p for p in git('diff','--name-only','--','v6lite').splitlines() if '/tests/' not in p]
 testfiles=sorted(p.relative_to(ROOT).as_posix() for p in (ROOT/'v6lite/tests').glob('test_batch27a*.py'))
 raw_red=[(label,r) for label,r in receipts if r['baseline'] and not r['collect_only']]
 final_red=max(raw_red,key=lambda pair:pair[1]['end_pt']);assert final_red[1]['totals']['failures']>0 and final_red[1]['totals']['errors']==0
 # AST locations reference the final package; baseline excerpts have separate pinned provenance.
 funcs=[]
 for p in changed:
  module=ast.parse((ROOT/p).read_text(encoding='utf-8'))
  for node in ast.walk(module):
   if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and (any(x in node.name for x in ('execution_time','receipt','overnight_before_tp','finish_evidence','closed_by_own_fill')) or node.name in ('_on_position_closed','startup_card','_protection_on_status')):
    funcs.append((p,node.lineno,node.name))
 lines=['# V6-Lite 第二十七批 27a 修复报告','',f'日期：2026-10-16；范围：仅第 5 节（含 5.6）；基线 `v6lite-batch26` / `78a0905e0c45482cf6db7427016231b69144422d`。',f'最终 code_sha：`{code[:16]}`；完整包 SHA-256：`{code}`。','',
 '## 指令、边界与事实来源','',
 '- John 已确认当前含 5.6 的指令版本；SHA-256 `3cfb4765819725d083268218c0e94db523e037ca9cc3147161af95a8a2a5875b`。消息中另一旧短哈希没有作为实施依据。',
 '- 10/15 统计摘要 SHA-256 `26b1edc06f75bc2186d86615d2f57f94ea5ed6eb9f1313de7882fd505dd23bf6`。先交调查 `docs/V6_LITE_BATCH27A_INVESTIGATION_2026-10-16.md`；SDK/B26逐行摘录及哈希见 `docs/batch27a_evidence/source_provenance.json`。',
 '- 仅开发工作区 dev；单一实施 Agent 修改生产，另一 Agent 写独立新测试，第三 Agent 只读复核；ROOT 串行验证。未连接 TWS/Gateway、未访问运行仓库/用户状态目录或 John 配置、未修改 Claude 手册、未 push。',
 '- 第 1–3 节审计去重、日报限量和日志分级尚未实施，将在 27a 之后继续。合并时机由 Claude 与 John 决定。','',
 '## 逐项发现 → 改动 → 测试','',
 '| 项目 | 发现 | 改动与安全边界 | 独立测试 |','|---|---|---|---|',
 '| 5.1–5.2 当天时间 | 完整保护查询只构造证据，未调用核时；非已知 execId 提前过滤 | callback 后 tick 主动完整查询，CLOSED 后仍按限速继续；已知重复仅走幂等核时；query 未确定/缺执行/失败不清标记，不重记经济 | callback ENTRY/STP、首次完整查询当场核实、失配/空/失败/uncertain、经济字段不变 |',
 '| 5.3 历史例外 | B26 标记永久阻断 a，原正向 Fake fill 没有 uncertain 标记 | 九字段严格类型、独立 conId pin/保护身份、provisional 与 fills.time 原字符串一致、严格早于今日零点 ET 减一小时；历史只读，不清标记不写 VERIFIED | 10/13→10/19 双模式、九字段逐项、JSON/NaN/naive、阈值等号与±1µs、aware offsets、b–d反证、S+T+X≤P |',
 '| 5.4 卡片与前收盘 | a 失败显示未完成；盘前原因不能凭摘要定论 | 改为未执行（a 未通过）；准确说明每5分钟重试、有效前收盘价返回才解除入场阻断；不保证开盘恢复或 STP 已有效 | 可见卡片、299/300秒真实重试/风控恢复、保护状态组合 |',
 '| 5.6(1) 误 P0 / 旧事件 | 非包装 BrokerError 首次可直接 P0；protect_incident是送达事件，F4另有持久时钟 | 已确认本系统保护成交关闭及账本净0的在途旧快照失效只审计；健康完整当日查询结束旧证据事件，不授予 full_exec；新的失败重新计时，真实短仓/外部卖单不免责 | .153 Submitted→.666急迫真实成交CLOSED→.940旧P3快照回放，恢复/新F4，CLOSED负向 |',
 '| 5.6(2) 止损成交核时 | 平仓后仍无必执行核时任务 | 与当天主动核时同一路径，平仓后不停止；持久未核实成交列入日报 | STP callback 及 CLOSED失败/空/uncertain重试 |',
 '| 5.6(3–4) 查询与阶段 | 冻结到价仍先begin_stage，再额外查询后拒绝；相同WORKING回报另有优先路径 | 在创建阶段前拒绝已知无过夜权限；每持仓每天最多一次到价冻结记录；真动作仍重新取当代证据，不延迟A/B/修改ACK | 每秒跨价61次，各通道15/30/45/60秒；native16/61/7发起计数；重复回报与矛盾payload |',
 '| 5.6(5) 费用 | -9.09=-6.09-已知1.00-缺费保守2.00，并非重复扣已知费 | 可见平仓卡显示暂计-7.09并明确1笔佣金未计、非最终净盈亏；risk.trade_net/sizing保守预留不改 | 迟到1.02费用后真实净值-8.11，数量与毛亏不变 |','',
 '运行约束 A（账户独占、人工停机→撤单→平仓→release）与 B（账本不回滚）继续是 John 接受的剩余风险，不是技术证明；历史时间例外也不排除等量人工回转或一致旧账本回滚。','',
 '## 红绿收据与夹具校准','',
 f'最终同一测试集在 B26：`{final_red[0]}`，{final_red[1]["totals"]}。全部失败与通过 nodeid、JUnit 和源哈希见 public 目录。',
 '红测隔离根目录与新增spec文件所在目录不同，pytest原生红nodeid只有 ::函数[参数]，不能声称与绿的原始完整nodeid相同。补充 baseline_case_mapping.json 以不变spec SHA和唯一函数AST恢复文件前缀，86项逐条及顺序与绿一致；旧原始收据未修改。三次全量则保留同目录原始完整nodeid一致。',
 '保留所有中间收据；曾校准三类测试夹具：旧已关闭 Store 引用、通过受监控 Lite submit 构造外部违规订单、按英文标题筛选中文卡片。另将 callback 夹具的首个完整查询明确设为 uncertain，随后才给确定查询，避免把已经成功核实时的正确行为误判。核心经济、容量和后置断言未放宽。',
 '独立静态复核发现新增启动说明不应无条件保证 STP 有效，修正文案后补守卫验证；该点未伪称已有修前自然红。复核边界的候选自然红（如适用）单独保留，不冒称都是 B26 新缺陷。','',
 '| 收据 | 基线 | tests / failures / errors / skipped |','|---|---|---|']
 for label,r in receipts:
  if r['full'] or r['collect_only']: continue
  z=r['totals'];lines.append(f'| {label} | {"B26" if r["baseline"] else r["source_code_sha"]} | {z["tests"]} / {z["failures"]} / {z["errors"]} / {z["skipped"]} |')
 lines+=['','## Windows 默认全量连续三次','',f'Python `{runs[0]["python_version"].splitlines()[0]}`；执行文件 `{runs[0]["python_executable"]}`。默认 `v6lite/tests` + `scripts/tests`，没有过滤旧测试；1717个B26 nodeid全部保留，新增{len(nodes)-1717}个。{len(legacy)}个原测试/脚本文件逐字节保留。','',
 '| 次数 | PT 开始 | PT 结束 | 结果 |','|---|---|---|---|']
 for n,r in enumerate(runs,1):
  z=r['totals'];lines.append(f'| {n} | {r["start_pt"]} | {r["end_pt"]} | {z["tests"]-z["skipped"]} passed + {z["skipped"]} skipped；0 failed / 0 errors |')
 lines+=['','唯一遗留 skip：`test_scoring_matches_legacy_scanner_on_identical_bars`，原因 `legacy market_scanner.py not present`。每次全量前重新检查 PT，并用 Windows JobObject 在下个交易日06:00前截止；优化关闭、UTF8、禁止pyc、隔离profile/temp、清除机密环境变量，所有源码及collection/executed/JUnit一致。','',
 '## 实际卡片样例','', '完整实际渲染及对应测试：`docs/batch27a_evidence/rendered_cards.md` 与 `rendered_cards.json`。以下来自真实 FakeBroker/MemoryChannel，不是手写模拟文案。','']
 for item in interesting[:8]: lines+=['```text',mask(item['plain']),'```','']
 lines+=['## 最终源码引用','']+[f'- `{p}:{n}` — `{name}`。' for p,n,name in funcs]
 lines+=['','## 仍未知 / 未宣称解决','',
 '- 10/16 具体 BrokerError 没有脱敏 full_error；合成回放证明快照竞态可产生相同类别，不证明现场一定是该原因，更不能猜 End 计数异常。',
 '- 10/13/10/16 券商查询实际原始时间字符串/时区未提供；query 若仍 uncertain 保持标记和 review，不假定一定能核实。',
 '- 前收盘价缺失的具体现场原因未知；自动恢复条件已验证，未承诺某个钟点。',
 '- 回放仅复用指令披露 execId/orderId/positionId/provisional/时间顺序；未披露的价格、permId、conId 等清楚标为合成。净盈亏测试使用披露的聚合金额，不假称新增真实成交证据。','',
 '## SHA-256 清单与暂存边界','',
 '下表覆盖整个最终 v6lite/scripts 以及本批指令、脱敏统计、调查和验收证据；report 与 manifest 本身互不纳入，避免循环。原始合成收据本机保留，发布副本统一脱敏并记录原始字节SHA；不暂存未经脱敏的原始输出、旧批临时目录、手册、配置、数据库或缓存。','']
 fixed=[ROOT/'docs/V6_LITE_BATCH27_INSTRUCTIONS.md',ROOT/'docs/batch27_evidence/g3t_20261015_volume.md',ROOT/'docs/V6_LITE_BATCH27A_INVESTIGATION_2026-10-16.md']
 allowed_names={'preflight.json','baseline_file_hashes.json','source_provenance.json','acceptance_process.py','run_batch27a_checks.py','sample_capture.py','build_batch27a_report.py','acceptance_checkpoint.json','full_acceptance.json','pre_full_review.json','final_review.json','rendered_cards.json','rendered_cards.md','fixture_calibration.md','source_compile.json','visible_card_checks.json','baseline_case_mapping.json'}
 evidence=[p for p in EV.iterdir() if p.is_file() and p.name in allowed_names]+[p for p in public.iterdir() if p.is_file()]
 package=[p for top in ('v6lite','scripts') for p in (ROOT/top).rglob('*') if p.is_file() and '__pycache__' not in p.relative_to(ROOT).parts]
 paths=package+fixed+evidence
 manifest={p.relative_to(ROOT).as_posix():sha(p) for p in sorted(set(paths))}
 lines+=['| 文件 | SHA-256 |','|---|---|']+[f'| `{p}` | `{h}` |' for p,h in manifest.items()]
 write(REPORT,'\n'.join(lines)+'\n')
 dump(EV/'sha256_manifest.json',{'code_sha':code[:16],'full_code_sha256':code,'baseline':'v6lite-batch26','status':('Windows full3 passed; final independent review receipt included' if (EV/'final_review.json').exists() else 'Windows full3 passed; final independent review required before staging'),'sha256':manifest})
 stage=sorted(set(changed+testfiles+[p.relative_to(ROOT).as_posix() for p in fixed+evidence]+[REPORT.relative_to(ROOT).as_posix(),'docs/batch27a_evidence/sha256_manifest.json']))
 dump(EV/'stage_plan.json',{'files':stage,'exclude':'manual, raw receipts, baseline/test_tmp, configs, DB/WAL/SHM, caches, unrelated docs'})
 print(json.dumps({'code_sha':code[:16],'report':str(REPORT),'report_sha256':sha(REPORT),'manifest_items':len(manifest),'manifest_sha256':sha(EV/'sha256_manifest.json'),'stage_files':len(stage)},ensure_ascii=False,indent=2))
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--final',action='store_true',required=True);parser.parse_args();main()
