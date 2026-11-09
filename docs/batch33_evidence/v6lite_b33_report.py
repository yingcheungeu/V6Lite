"""Render the final evidence report from completed frozen receipts."""
from pathlib import Path
import hashlib,json
R=Path('C:/Dev/V6Lite-dev');O=R/'docs/batch33_evidence'
def read(n):return json.loads((O/n).read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 f=read('freeze.json');close=read('closeout.json');compile_result=read('source_compile.json')
 runs=[read(f'final_full_{i}.json') for i in (1,2,3)]
 assert all(r['returncode']==r['failures']==r['errors']==0 for r in runs)
 focused=read('focused_final_json_guard.json');broad=read('focused_b22_b33.json');red=read('red_b32_final_spec_after_json_guard.json')
 fps=read('config_fingerprints.json')
 lines=['# V6-Lite 第三十三批交付报告','',
 '日期：2026-10-31（PT，周六）。基于 `v6lite-batch32` / `ea692ddf9c142ae760cc3dfbe9c0d79d9b8b18aa`。报告文件按实际交付日期命名。',
 '',f"指令 SHA-256：`{f['instructions_sha256']}`。",
 f"冻结 code_sha：`{f['code_sha']}`；package SHA-256：`{f['package_sha256']}`。",'',
 '## 实现对照','',
 '| 文件 | 改动与边界 |','|---|---|',
 '| config.py | execution.tp_start_after_open_seconds 默认30；0关闭；严格非负int，拒绝bool/float/非有限/字符串；参与原asdict配置指纹 |',
 '| tp_start.py | 复用 session_for(trading_day(now)).open_utc，按当天日历计算期限；休市无门；仅新TP检查，持仓/交易日持久审计去重 |',
 '| engine.py | 退出决策与既有pending处理完成后、begin_stage之前检查；中文启动卡“监控”及兼容纯文本启动信息增加说明 |',
 '| protection_engine.py | WORKING且尚无tp_stage的新workflow入口再检查，早于报价通知、证据查询与减量/撤单 |',
 '| test_tp_timeout.py | 有效请求未到点不领取、不删除、不延长原5分钟TTL；既有阶段仍能按正常价格发送 |',
 '| report.py | 当日延后计数、每个持仓的预计与实际开始；同时匹配STAGE_BEGIN和真实reduce_request，覆盖恢复已有pending的入口 |',
 '| tests/test_batch33_open_delay.py、test_batch33_controls.py | 52项离线回放、边界、审计JSON及独立容量核验 |','',
 '等待不创建STAGE_BEGIN/workflow、不减STP、不计重试、不写冻结、不发卡。审计 `TP_START_DEFERRED_OPEN` 记录档位、触发价、bid/ask/last、报价时刻、交易日和预计开始时间；同持仓当天跨重启只一条。到点重新决策，不排队补做旧触发，不跳档或合并下单。',
 '', 'STOP、券商GTC STP、穿越告警、保护核验、已开始的TP撤单/恢复、断线恢复、过夜a–d核验和入场扫描/窗口沿用原路径。强身份、S+T+X≤P、运行约束A/B均未放宽。sell_limit、向上取分、新鲜报价和物理发线截止规则未修改。',
 '', '## 新配置指纹','',
 '以下为仓库内可审阅、使用合成账户的g3t/g3n模板；未访问或改动John的toml、运行仓库或用户.v6lite。真实操作配置包含账户、端口、universe等字段，不能将模板指纹冒称其现场指纹。配置源SHA与完整结果见 `batch33_evidence/config_fingerprints.json`。',
 '', '| 模式 | 配置源 | 新fingerprint |','|---|---|---|']
 for mode,v in fps.items():lines.append(f"| {mode} | {v['source']}（默认30） | `{v['config_fingerprint']}` |")
 lines += ['', '新配置依靠默认值生效，无需修改John配置。0明确关闭；正常非负大整数有效，超出datetime可表示范围时保持等待且不使保护逻辑崩溃。',
 '', '## 红绿与真实路径','',
 '最终测试规格与隔离b32归档中的新增测试同字节；归档原214文件逐一核对未改，原package code_sha仍5e94c5d34a138e7d。归档、临时SQLite和原始Claude证据不进入提交allowlist。',
 '',f"`red_b32_final_spec_after_json_guard`：{red['tests']}项，2 failed / 2 passed / 0 error / 0 skipped。TP1和末股TP3在旧版分别立即减量、立即撤STP，形成预期红证据；止损穿越/真实STP成交以及N=0的精确订单轨迹在b32通过。候选对应全部通过。",'',
 '10/29回放从10/28真实入场/成交/原GTC、正常shutdown、Store关闭重开及10/29盘前a–d恢复开始。09:30:00.828649 ET达到TP1，29.999秒无阶段、减量或新单；30.000秒可开始TP1，使用当时新鲜bid349.47的既有公式得到LMT348.78。初始报价347.54/347.82及日期来自导出；TP阈值、别名身份和后续报价是明确的合成回放输入，不声称导出提供了缺失字段或证明Paper内部撮合原因。',
 '', '末股TP3从10/27入场、10/28真实完成TP1与TP2，再shutdown/关库重开、10/29真实过夜恢复得到P=S=1；30秒前原STP持续有效，之后才撤STP，强确认后才发1股TP3。每次实际broker写入前后沿用独立经济账S+T+X≤P断言。',
 '', '其余覆盖：N=0/60、29.999/30.000秒、半日市、DST前后交易日及切换休市日、非法TOML、触发消失、越多档只TP1、到点身份/原生End/报价失败关闭、在途撤单恢复、09:30:10真实CLI请求延后领取、5分钟原TTL端点、跨重启及次日审计。盘前既有workflow测试通过N=0建立真实阶段后启用屏障来隔离验证继续执行行为，不引入生产热改配置入口。',
 '', '独立只读审查发现已有pending阶段只有早期STAGE_BEGIN时日报会漏记后续workflow；已补真实reduce_request关联与回归测试。最终复核无新增代码修改需求。',
 '', '## 聚焦与源码验证','',
 f"最终源码聚焦（本批52项及b23严格报价门）：{focused['tests']-focused['skipped']} passed / {focused['skipped']} skipped，{focused['seconds']}秒。此前候选d082e6a97b7872c6的b22–b33明确文件聚焦：{broad['tests']} passed，{broad['seconds']}秒；该候选之后又修复了下述审计JSON问题。最终源码的全部b22–b32回归包含于下表三次全量。",
 f"源码冻结于{f['frozen_at_pt']}；inventory {len(f['inventory'])}文件；{compile_result['files']}个Python源码UTF-8无BOM、AST/compile通过，不写pyc。SDK ib_insync {f['dependencies']['version']}及6个源码hash、验证工具、原始输入和操作手册hash固定。",'',
 '## Windows连续三次串行全量','', '| 回执 | 结果 | 秒 | PT开始 → 结束 |','|---|---|---:|---|']
 for r in runs:lines.append(f"| {r['label']} | {r['tests']-r['skipped']} passed / {r['skipped']} skipped / 0 fail/error | {r['seconds']} | {r['start_pt']} → {r['end_pt']} |")
 lines += ['',f"三轮全部相同源码/SDK/只读输入与相同case顺序、skip身份；case identity SHA-256 `{close['case_identity_sha256']}`。源码/依赖/输入运行前后逐项一致；日志、XML及receipt哈希可核对。全量均在周末，串行无重叠。",'']
 for s in close['skipped_cases']:lines.append('- 既有skip：`'+s[0]+'.'+s[1]+'`；'+s[2])
 lines += ['', '## 启动卡与日报样例','', '样例由实际renderer在临时测试状态生成并脱敏，完整文件为 `batch33_evidence/samples/startup_card_sample.txt` 与 `daily_report_sample.md`。', '', '```text']
 card=(O/'samples/startup_card_sample.txt').read_text(encoding='utf-8')
 lines += card.splitlines()[7:11]+['```','','```text']
 report=(O/'samples/daily_report_sample.md').read_text(encoding='utf-8')
 opening=report.split('## Opening TP deferrals / 开盘止盈延后',1)[1].split('## Commission',1)[0]
 lines+=opening.strip().splitlines()+['```','','## 全部测试回执（失败不覆盖）','', '| 回执文件 | 标签 | tests | failures | errors | skipped | rc | incomplete | source unchanged |','|---|---|---:|---:|---:|---:|---:|---|---|']
 for p in sorted(O.glob('*.json')):
  try:r=json.loads(p.read_text(encoding='utf-8'))
  except (ValueError,TypeError):continue
  if isinstance(r,dict) and 'label' in r and 'tests' in r:
   lines.append(f"| {p.name} | {r['label']} | {r['tests']} | {r['failures']} | {r['errors']} | {r['skipped']} | {r['returncode']} | {r['incomplete']} | {r['source_unchanged']} |")
 lines += ['', '首轮红回放的同步ACK观察点、盘前证据刷新、退出报价容忍120秒和恢复ACK的时机均为确定夹具校准，原回执保留；日报新增回归曾把预计30秒误当实际31秒，已改成读取真实审计时刻的断言。这些不是随机flake，未重试隐藏失败。本批未发现flake。',
 '', '首次全量d082候选在2026-10-31 16:29 PT主动停止：补充只读检查发现等待审计可将非有限报价写为JSON NaN/Infinity，导致下次JSON去重查询出错。四个负例在旧候选确定失败（red_nonfinite_quote_audit_all_paths）；现改为有限数值/null，另存原始报价字符串，不改变退出或严格报价门。修复后95项聚焦通过，重新冻结后从final_full_1开始三轮。pre_json_fix_final_full_1.json的rc非零、incomplete=true、tests=0，明确不是通过结果；未计入连续三轮，也不是随机flake。旧源码/工具归档pre_quote_json_candidate.zip、旧freeze/compile、部分stdout/inventory及中断说明均保留；归档hash见quote_json_interruption.json，归档本体和临时SQLite不进入提交。',
 '', '## SHA-256与冻结交付','', '完整SHA-256清单为 `batch33_evidence/SHA256SUMS.json`，列出精确allowlist内源码、测试、指令、报告、冻结/编译/全量回执、验证工具和脱敏样例；清单自身不自引用。', '', '| 源码/测试 | SHA-256 |','|---|---|']
 for n in ('v6lite/config.py','v6lite/engine.py','v6lite/protection_engine.py','v6lite/report.py','v6lite/test_tp_timeout.py','v6lite/tp_start.py','v6lite/tests/test_batch33_open_delay.py','v6lite/tests/test_batch33_controls.py'):lines.append(f'| {n} | `{sha(R/n)}` |')
 lines += ['', '在dev本地提交并创建annotated `v6lite-batch33`；交付工具逐项核验tag源码inventory及manifest blob。无push、部署或券商连接；不访问用户.v6lite/运行配置，不改John toml、操作手册及既存非本批文件。唯一Git共享元数据操作为本次明确授权的dev提交和tag。交付后冻结，等待Claude复核。','']
 (R/'docs/V6_LITE_FIX_REPORT_ROUND33_2026-10-31.md').write_bytes('\n'.join(lines).encode('utf-8'))
 print('Final report rendered from frozen full receipts')
if __name__=='__main__':main()
