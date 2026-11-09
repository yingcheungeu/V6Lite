"""Build the Batch20 report from frozen source and completed evidence only."""
from pathlib import Path
import ast, hashlib, json, subprocess, sys, xml.etree.ElementTree as ET
sys.path.insert(0, str(Path.cwd()))
from v6lite.version import code_sha
ROOT=Path.cwd(); E=ROOT/'docs/batch20_evidence'; REPORT=ROOT/'docs/V6_LITE_FIX_REPORT_ROUND20_2026-10-08.md'
def load(name): return json.loads((E/name).read_text(encoding='utf-8'))
def loc(file,needle):
    for n,line in enumerate((ROOT/file).read_text(encoding='utf-8').splitlines(),1):
        if needle in line: return f'{file}:{n}'
    raise AssertionError((file,needle))
def suites(file):
    root=ET.parse(E/file).getroot();out={}
    for case in root.iter('testcase'):
        name=case.attrib['classname'];entry=out.setdefault(name,{'passed':0,'failed':0,'errors':0,'skipped':0})
        label='failed' if case.find('failure') is not None else 'errors' if case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
        entry[label]+=1
    return out
runs=load('windows_full_results.json'); assert len(runs)==3 and all(r['exit_code']==0 for r in runs)
sha=code_sha();assert all(r['code_sha_before']==sha==r['code_sha_after'] for r in runs)
base=suites('baseline_delivery.xml'); full=suites('windows_full_3.xml');total=sum(x['passed'] for x in full.values());assert total==1055
new_cases=sum(sum(x.values()) for x in base.values());red=sum(x['failed'] for x in base.values());green=sum(x['passed'] for x in base.values());assert (new_cases,red,green)==(129,61,68)
assert all(not sum(x[k] for k in ('failed','errors','skipped')) for x in full.values())
log=load('incident_log_readonly.json');state=load('incident_state_readonly.json');freeze=load('freeze_environment.json');assert state['bytes_unchanged']
assert hashlib.sha256((ROOT/'docs/V6_LITE_OPERATOR_MANUAL.md').read_bytes()).hexdigest()==freeze['operator_manual_sha256']=='5d7eb6f043f4d933019f36b6cbfd28b28f8474c0b7097261f9bb77ae627133bf'
tree=ast.parse((ROOT/'v6lite/tests/test_batch20_protection_equivalents.py').read_text()); mapping=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='EQUIVALENT_MAP' for x in n.targets));assert len(mapping)==27
out=f'''# V6-Lite 第20批修复报告 — 2026-10-08

最终 code_sha：`{sha}`。Windows 默认全量（V6-Lite + scripts）连续三次 **{total} passed / 0 failed / 0 errors / 0 skipped**。新增{new_cases}个case，最终统一B19基线 **{red} failed / {green} passed / 0 errors**；最终全量全部通过。

## 1. 范围与冻结边界

本批仅 `C:\\Dev\\V6Lite-dev` 的 dev 工作区；基线 HEAD `abbdb4f2f7a558262a0859d5a6bf7ee859e46d2f`、code_sha `6293dd4e2377603c`。root是唯一生产代码实施者，三位独立Agent分别拥有时间/日志测试、保护事件测试、等价合同/退休GTC测试，最终全量串行。写前及冻结检查v6lite进程均为0。

未连接TWS/Gateway或联网，未stage/commit/push/合并；未修改运行仓库、John配置、已有日报或状态库。指定10/8日志只读；SQLite明确 `mode=ro` + `PRAGMA query_only=ON`，数据库及现有WAL/SHM读取前后字节哈希相同，见 `incident_state_readonly.json`。全量时间全部在10/8 13:30 PT之后。

dev已有 `docs/V6_LITE_OPERATOR_MANUAL.md` 的未提交更新不属于本批，字节保持SHA-256 `5d7eb6f043f4d933019f36b6cbfd28b28f8474c0b7097261f9bb77ae627133bf`，不纳入本批清单或提交范围。交付后本批源码和测试冻结，等待Claude复核。

## 2. 发现 → 改动 → 测试

| 项目 | 发现与证据 | 改动与边界 | 测试 |
|---|---|---|---|
| G3-N正式标的 | B19样例只有AAPL/SPY/TSLA，与正式Paper的10个标的不一致 | 仅样例universe/sectors对齐正式样例；合成账户、7497、client131、test关闭、保护默认、10:00/13:30 ET、独立paper-g3n状态目录保留 | template五项：B19 4红/1绿，最终全绿 |
| 27项保护等价 | 原本地STOP/重试合同不能直接当开启保护的GTC合同；原测试名与强断言保留，三份旧夹具按下文调整 | 第7节逐名映射；新增实际GTC成交/TP三证据/减量/撤单/损益/风险/释放覆盖，不靠强制关闭保护 | 27项全绿；其中24项在B19已绿，属于补覆盖，另外3项发现真实边界问题 |
| 6 成交时间 | 同exec回调21:58:09Z，query14:58:09Z；首先回调入账，后续同exec只补费用，日报直接读fills | 解码前保存实例级raw时间来源；明确时区→UTC；无明确TWS时区的naive值及时以接收时间暂记并持久标记；完整query、同identity/同交易日才能核准新标记行。闭仓同事务重算MAX(fills.time)，不重做经济成交/保护A计时 | 时间组27项：B19 20红/7绿，最终全绿；强查询322部分结果、跨日、旧账、重启、P&L不重记 |
| 7 日志 | wrapper逐条成交/佣金INFO共47,277行、15,412,798字节；全天119,350行、25,883,218字节 | 只将wrapper execDetails/commissionReport降DEBUG，INFO下不输出；V6的47,490行QUERY及其它Warning/INFO保留。A空窗查询频率不以降噪为由减慢 | handler/root-level/idempotence测试；保留QUERY/Warning、DEBUG可见 |
| 8 重复P0 | 07:58至14:33约80张同类P0，动态证据时间使旧body dedupe失效 | 持久事件签名忽略市价、elapsed、confirmed_at/working_at/updated_at；覆盖类UNKNOWN/拒单/A逾期归并。成功确认P0后相同事件30分钟内不再发，之后最多一次P1“仍未解决”；材料变化/确证恢复发新卡 | 成功/失败/在途/重启、早期gap P1抑制、价格/时间变化、仓位/外单变化、恢复后再次发生 |
| 9 缺permId与release | protection perm为NULL，而intent已有1099402929；现main在人工平仓后仍拒release | 完整native account/client/conId/orderRef/orderId/permId/STK/USD/type/tif/price/整股total量核验后同事务同步两表ID；状态回调不是WORKING证明。native冲突不能降格为裸ID | 60项保护事件测试内含错完整身份/小数/总量、初始化perm0等待、bare exact旧合同、实际manual flat+release；release方法AST与B19相同 |
| 10 明确拒单 | 202在提交后立即回报明确“无法接纳止损限价单”，且非Lite发起取消 | 有完整强身份且明确拒绝原因时REJECTED，可能S为0，停止重复A缺口卡，仍冻结入场；卡片明确拒单，首选TWS限价平仓再release。裸202/裸Cancelled/身份错仍UNKNOWN | 中文/英文/转义原因、code202先于及晚于终态、错误上下文/缺身份、真撤单非拒单、已拒后重启/计时、正文不冒用未知 |
| 额外 GTC迟到竞态 | 已取消旧GTC被替换后再成交，原Store抛身份异常回滚，替代S未撤，可能超卖 | 替换前持久完整退休身份；只有exact orderId/permId/conId可入账旧fill；旧fill不扣新S上界，再cached减量/撤单，闭仓仍做取消核验。相同pending total/price等待原代次确认，不重复修改。退休GTC卡片不冒充止盈 | B19原竞态+7退休边界红；独立等待代次自然红[2,3]→保持1；full/partial、重复、重启、错身份全部绿 |

## 3. 执行来源、已有账本只读核对

日志路径：`C:\\Users\\yu-ch\\.v6lite\\paper-test\\logs\\v6lite_20261008.log`，SHA-256 `{log['sha256']}`。

- `日志:4`：`server version 176`；本地ib_insync 0.9.86。
- `日志:222`：同exec `0000e0d5.6ac87b1f.01.01` 的Execution.time为`2026-10-08 21:58:09 UTC`。
- `日志:223`：Fill.time为包接收时间`2026-10-08 14:58:10.104497 UTC`，不是已确认执行时间。
- `日志:236/:242`：同exec的查询Execution.time为`2026-10-08 14:58:09 UTC`。
- 状态库 `fills[exec_id=0000e0d5.6ac87b1f.01.01]`：AAPL BUY3@337.74，time=`2026-10-08T21:58:09+00:00`，trading_day=2026-10-08。错时已落账；不是日报二次转换。

入账链：`{loc('v6lite/broker/ibkr.py','def _fill_event')}`（Execution.time）→`{loc('v6lite/engine.py','def _ingest_fill')}`→`{loc('v6lite/store.py','def record_fill')}`。B19同exec已存在分支不改时间，因此query正确值不能修正首次错误。raw wire timeStr没有记入原日志，不能断言线上原始字符串格式或把所有naive解释为UTC；本机PT隐式时区解释可造成7小时差，但此为源码与差值支持的机制判断，不当作wire证据。

新核验：`{loc('v6lite/execution_time.py','def install_execution_clock_hooks')}`、`{loc('v6lite/broker/ibkr.py','def _complete_executions')}`、`{loc('v6lite/store.py','def _verify_execution_time')}`。只在本实例保留明确时区；不修改installed library、不全局设置TimezoneTWS、不统一减7小时。无明确zone时保护挂单与数量入账立即按接收时间进行，后续完整query核准原时刻。错误终止的reqExecutions列表不视为完整；账户级release查询同样严格，finally恢复RaiseRequestErrors。

已有旧行没有新 `execution_time_unverified` 标记，代码不会回写它；遇同exec不同时间仅留 `EXECUTION_TIME_REVIEW_REQUIRED` 审计。跨日/身份冲突也不自动调整。历史核对只应逐exec比较账户（不输出账户）、symbol/side/qty/price/orderId/permId/conId与同一exec的完整查询时间，再在独立核对报告列出旧time与正确time。不能批量减7小时，也不能用接收时间假装正确执行时间。本例正确14:58:09Z、差-7小时、数量/价格/交易日不变；本报告不改原fills、position、PnL或原日报。

可交给John/Claude的只读核对（不实例化Store、不连券商）：

```python
from pathlib import Path
import sqlite3
p = Path(r"C:\\Users\\yu-ch\\.v6lite\\paper-test\\v6lite.sqlite3")
with sqlite3.connect(p.as_uri() + "?mode=ro", uri=True) as db:
    db.execute("PRAGMA query_only=ON")
    print(db.execute("SELECT exec_id,symbol,side,quantity,price,time,trading_day FROM fills WHERE exec_id=?",
                     ("0000e0d5.6ac87b1f.01.01",)).fetchone())
```

## 4. 10/9手动平仓与release：当前限制必须保留

状态库只读：AAPL OPEN3，entry337.74；protection orderId33/conId265598/client132，state UNKNOWN，permId NULL，intent CANCEL_SEEN/Cancelled且perm1099402929。`日志:238/:249/:250/:251`有PendingSubmit→PreSubmitted→PendingCancel→Cancelled；PreSubmitted约5ms，随后完整工作单快照已无该单，因此旧snapshot路径错过perm。`日志:252`是明确Warning202限价325.69/止损335.77偏离拒单；同段没有Lite cancelOrder。

**当前运行main不会仅因TWS0股就放行release**：`{loc('v6lite/engine.py','protected GTC cancel/identity unverified')}`拒绝缺protection.permId/完整身份或无确切终态。新的native补ID：`{loc('v6lite/protection_engine.py','def _protection_adopt_status_identity')}`；native校验失败：`{loc('v6lite/engine.py','if (ev.identity_checked')}`。两表ID必须同时强核验；聊天中的1099402929、只读日志摘录或intent.permId本身都不是绕过release的权限。

John在10/9 06:35 PT后人工处置前，先在同一TWS确认实际仍3股且无任何在途SELL；若确认该保护已被券商拒绝，按限价单平仓，等待持仓推送/新查询均0、无在途订单及成交静默期。现旧状态的release预计仍拒绝，保留冻结，不用resolve-intent、clear-anomaly、手补SQL或删状态库绕过。需Claude另行裁定该历史保护的强身份恢复方式；若能取得完整native订单/合同/终态证据，再由经复核的引擎路径事务同步，不从裸permId反推。无法取得就保持拒绝，不能宣称已释放。

CLI `release`只排队请求，真正执行还依赖运行引擎的核验；本批没有运行该命令。新dev用例证明“完整native身份 + exact撤销 + 实际人工flat + 新鲜三证据/静默期”能release，不代表John旧历史行自动修复。本批不自动迁移历史状态，也未修改3%offset；实际券商价格带拒绝原因及后续政策仍须Claude/John裁定，不盲目重挂。

## 5. 告警事件口径与未关闭边界

`{loc('v6lite/protection_incidents.py','def _deliver_protection_notice')}`：成功确认P0才持久标记已送达；同事件状态不变时30分钟P1，恢复由新鲜三证据确认，签名保留真实仓位、订单身份/容量和外部卖单变化。不因市价/证据时间变化重复P0；已经P0的A事件不再发早期5秒P1，而首次尚未P0的5秒P1/15秒P0合同继续。

发送失败可重试；进程丢失在途回执时按未确认重新尝试，Discord端无幂等键，不能保证远端已收到但回执丢失的故障窗口绝对exactly-once。成功确认的持久事件跨restart不重复。

明确拒单必须含完整一致native tuple+终态+明确拒绝原因，且非本地取消流程；裸Cancelled/裸202不能假装已拒。202先于空白终态时，只从同一Trade.log取202非空拒绝原因，仍经过完整强身份与明确拒绝短语核验。价格、止损/止盈参数、A/B期限和release最终核验均未放宽。无真实TWS演练，线上timestamp格式/强终态回报/价格带行为仍须John/Claude日志确认。CLOSED后第二笔退休真实成交的安全路径已静态审查，未另加专用用例，不混称为实测。

## 6. 红绿、独立复核与Windows三次全量

最终统一baseline由 `git archive abbdb4f...` 导出TEMP，先确认原包code_sha=6293dd4e2377603c，再仅复制五个新测试文件；不把B20生产放进B19目录。最终依据为 `baseline_delivery.xml` 与 `baseline_delivery_metadata.json`，后者列出了最终五个新测试的字节SHA；目录中的 baseline_final/frozen 等是中间阶段证据，不用作本批最终计数。

| 测试模块 | B19红 | B19绿 | 最终新增case数 |
|---|---:|---:|---:|
'''
for name,counts in base.items(): out+=f"| {name.rsplit('.',1)[-1]} | {counts['failed']} | {counts['passed']} | {sum(counts.values())} |\n"
out+='''
第一批异步告警夹具误用queue.join导致等待，已按实际worker协议修正清理后再跑，不计产品红或时序flake。等价夹具的R5采用真实GTC隐藏部分成交2；F1等待真实对账周期；SR6增加滑穿限价场景以验证保护模式即时P0（原107仍由GTC执行/P1监测），不能声称原裸STOP夹具也即时P0。late-release强断言前缀纠正为先命中的late_fill_before_flat，不删除flat证据，不放宽不记账/不重开/回放幂等。

三次早期全量尝试分别因独立发现重复pending减量、卡片归因错误、原LF被误写CRLF而主动中止；对应记录留在interrupted*，均不算完成，也未吞掉/重试掩盖产品失败。原源码LF已恢复，最终diff只含任务行为。随后第一轮完整执行暴露7项失败（`regression_windows_full_1.log/xml`：7 failed、1046 passed，code_sha d468566d1afcee3f），没有把该轮计作通过：

- 两项原L3强断言（zero_stop / sub_dollar_stop）暴露真实回归：事件签名访问最小告警row缺失state导致KeyError。修复签名缺字段的读取，原L3测试完全未改；新增minimal-row回归用例。该新用例在B19为绿，产品红证据来自上述候选全量，不伪称B19自然失败。
- Batch15紧急回调夹具补齐新的强native身份元数据；0.05秒唤醒、无嵌套查询、回调内无TP、外层tick提交TP断言均保留。
- Batch15拒单标题selector改为用户指定标题，按实际held数量计算的未保护量强断言保留（该夹具20股）。
- Batch16及f5_copy的纯文案测试在实际渲染投递边界隔离新事件去重调度，保留独特标题、影响、编号步骤和中文数量断言；拒单建议补回明确“不要”禁止操作。

以上不修改触发数量与release安全门槛。最后增加“202先于空白terminal”的用例，当前候选上自然1 failed后，修复同Trade拒绝原因关联，再冻结。943项产品测试全部通过，随后如下三次完整默认全量从头连续完成，期间没有源码/测试变化；未触发第三次flake提交豁免。

独立复核：时间/日志Agent的27项通过；保护事件Agent已复核最终code202排序修复及三份旧测试夹具；27映射+退休强边界共35项由另一Agent设计复核。独立等待generation自然红再绿；root额外两项实际卡片来源红再绿。最终新增129 + 原814 V6-Lite = 943，scripts112，总1055。

```powershell
. C:\\Dev\\V6Lite-dev\\.venv\\Scripts\\Activate.ps1
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
$env:V6LITE_LEGACY_SCANNER = "C:\\Dev\\TradingV6\\market_scanner.py"
Remove-Item Env:V6LITE_TEST_PROTECTION -ErrorAction SilentlyContinue
python -u -B -m pytest v6lite/tests scripts/tests -q -p no:cacheprovider --tb=short --junitxml <evidence XML>
```

legacy scanner仅只读离线一致性测试，没有运行旧V6引擎；未跑强制开启保护的整套旧诊断，不能称旧夹具全已变成开启保护用例。

| 轮 | PT开始 → 结束 | 结果 | code_sha前/后 |
|---|---|---|---|
'''
for r in runs: out+=f"| {r['run']} | {r['start_pt']} → {r['end_pt']} | {r['tail']}，exit0 | {r['code_sha_before']} / {r['code_sha_after']} |\n"
out+='\n## 7. 剩余27项逐名对应\n\n原测试名和强断言保留，下面每个新用例显式ProtectionConfig(enabled=True)，最终全绿。附加8个退休边界不替代27映射。\n\n| 原测试 | 新等价测试 |\n|---|---|\n'
for old,new in mapping.items():out+=f'| `{old}` | `{new}` |\n'
out+='\n## 8. FakeBroker实际卡片样例\n\n前四张使用合成3股，第五张为退休GTC20→15股竞态；均只用TEMP与MemoryChannel，未发送手机通知，未使用真实账户。\n\n'+(E/'cards_samples.md').read_text(encoding='utf-8')
paths=[p for p in (ROOT/'v6lite').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
paths += [p for p in (ROOT/'scripts').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
paths += [p for p in E.rglob('*') if p.is_file() and p.name not in {'sha256_manifest.json','closeout_integrity.json','independent_final_review.json'}]
paths=sorted(set(paths),key=lambda p:p.relative_to(ROOT).as_posix());entries=[]
for p in paths:
    entries.append({'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
(E/'sha256_manifest.json').write_text(json.dumps({'code_sha':sha,'files':entries},indent=2)+'\n',encoding='utf-8')
out+=f'\n## 9. SHA-256清单\n\n共{len(entries)}项，含完整v6lite包（排除__pycache__）、scripts与本批证据；机器清单 `docs/batch20_evidence/sha256_manifest.json`。报告与清单自身、最终只读审核记录不纳入循环哈希；报告自身SHA在交付摘要单独回报。未stage/commit，未含John运行配置/数据库/机密，未包含无关手册。\n\n| 文件 | 字节 | SHA-256 |\n|---|---:|---|\n'
for e in entries: out+=f"| {e['path']} | {e['bytes']} | `{e['sha256']}` |\n"
REPORT.write_text(out,encoding='utf-8');print(json.dumps({'report':str(REPORT),'sha256':hashlib.sha256(REPORT.read_bytes()).hexdigest(),'code_sha':sha,'manifest_files':len(entries),'tests':total},ensure_ascii=False))