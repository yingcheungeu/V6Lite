"""Assemble immutable supplemental report and hashes after serial offline checks."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import ast,hashlib,json,subprocess,platform,sys
ROOT=Path(__file__).resolve().parents[2]; EV=Path(__file__).resolve().parent
REPORT=ROOT/'docs/V6_LITE_BATCH22_ITEM9_FIX_REPORT_2026-10-09.md'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def package_sha():
 h=hashlib.sha256()
 for p in sorted((p for p in (ROOT/'v6lite').rglob('*') if p.is_file() and '__pycache__' not in p.parts),key=lambda p:p.relative_to(ROOT/'v6lite').as_posix()):
  h.update(p.relative_to(ROOT/'v6lite').as_posix().encode());h.update(b'\0');h.update(p.read_bytes());h.update(b'\0')
 return h.hexdigest()[:16]
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True,encoding='utf-8').strip()
def newest(prefix):
 rows=[json.loads(p.read_text(encoding='utf-8')) for p in EV.glob(prefix+'*.json')]
 return max(rows,key=lambda row:row['end_pt'])
def cite(file,needle,tag=None):
 s=git('show',tag+':'+file) if tag else (ROOT/file).read_text(encoding='utf-8')
 for n,line in enumerate(s.splitlines(),1):
  if needle in line:return f'`{file}:{n}`'+(f'（`{tag}`）' if tag else '')+'：`'+line.strip()+'`'
 raise AssertionError((file,needle))
assert git('branch','--show-current')=='dev' and not git('diff','--cached','--name-only')
code_sha=package_sha()
checks={key:newest(prefix) for key,prefix in [('B20','red_batch20'),('B22','red_batch22'),('candidate','candidate_red_item9'),('green','green_item9'),('focused','focused_protection')]}
for name in ('green','focused'):
 assert checks[name]['returncode']==0 and checks[name]['totals']['errors']==checks[name]['totals']['failures']==0
 assert checks[name]['source_code_sha']==code_sha and checks[name]['source_unchanged']
spec=ROOT/'v6lite/tests/test_batch22_item9_broker_cancel.py'
assert checks['green']['spec_sha256']==sha(spec)==checks['B20']['spec_sha256']==checks['B22']['spec_sha256']
assert platform.system()=='Windows'
functions={'v6lite/engine.py':['_apply_releases'],'v6lite/protection_engine.py':['_protection_on_status','_protection_send_tp','_protection_modify','_protection_replace_after_cancel','_hard_protection_deadlines','_preflight_protection_deadlines','_protection_order_valid','_protection_native_quantity_valid','_protection_explicit_refusal'],'v6lite/broker/ibkr.py':['_status_identity']}
asts=[]
for file,names in functions.items():
 def funcs(text):return {n.name:ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(text)) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
 a,b=funcs(git('show','v6lite-batch22:'+file)),funcs((ROOT/file).read_text(encoding='utf-8'))
 for name in names:asts.append({'file':file,'function':name,'unchanged':a[name]==b[name]})
assert all(r['unchanged'] for r in asts)
syntax=list((ROOT/'v6lite').rglob('*.py'))
for p in syntax:ast.parse(p.read_text(encoding='utf-8-sig'))
verified={'checked_pt':datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(),'head':git('rev-parse','HEAD'),'branch':'dev','code_sha':code_sha,'index_empty':True,'baseline_tag_object':git('rev-parse','v6lite-batch22'),'windows':platform.platform(),'python':sys.version,'ast_files':len(syntax),'unchanged_functions':asts,'full_three_runs_for_this_patch':'NOT RUN: prohibited weekday 06:00-13:30 PT','archive_access':False,'runtime_access':False}
(EV/'serial_validation.json').write_text(json.dumps(verified,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
files=sorted({p for p in (ROOT/'v6lite').rglob('*') if p.is_file() and '__pycache__' not in p.parts}|{p for p in EV.iterdir() if p.is_file() and p.name not in {'sha256_manifest.json','independent_review_receipt.json'}},key=lambda p:p.relative_to(ROOT).as_posix())
items=[{'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in files]
table='\n'.join('| `'+r['path']+'` | '+str(r['bytes'])+' | `'+r['sha256']+'` |' for r in items)
result_lines=[]
for name,row in checks.items():
 t=row['totals']; passed=t['tests']-t['failures']-t['errors']-t['skipped']
 result_lines.append(f"| {name} / `{row['label']}` | {passed} passed / {t['failures']} failed / {t['errors']} errors / {t['skipped']} skipped | `{row['source_code_sha']}` | {row['start_pt']} — {row['end_pt']} |")
samples=(EV/'card_samples.md').read_text(encoding='utf-8')
report=f'''# 第22批第9条补充修复报告（2026-10-09）

本轮只在 `{ROOT}` 的 dev 工作树修复券商取消保护单的分类/卡片/同事件升级顺序。**离线聚焦验收已完成，新增补丁的默认全量连续3次尚未执行；本报告不是运行放行或新tag交付。** 工作树冻结，原第二十二批提交/tag/报告保持原样；不覆盖 `v6lite-batch22`。

## 1. 基线、分工和边界

- 冻结原B22：`{git('rev-parse','v6lite-batch22^{commit}')}`，code_sha=`d42320d23de79d6e`；B20：`{git('rev-parse','v6lite-batch20^{commit}')}`，code_sha=`cfe16edd7a7e4d7f`。
- 新工作树 code_sha：**`{code_sha}`**，指整个v6lite目录的路径+NUL+内容+NUL、按相对路径排序的SHA-256前16位，排除__pycache__；包含测试/示例，不仅生产源码。
- 唯一生产实施者：`batch20_protection_incident`；独立测试/旧契约对照：`batch20_equivalents`；只读独立复核：`batch20_time_logs`；root串行跑红/绿和聚焦回归、生成本报告与清单。
- 写前没有v6lite运行进程（初次预检及实施Agent再核验）；未连接TWS/Gateway，未运行run/g2_probe，未读取或修改任何User状态/归档、运行仓库、John配置、`.env`或通知凭据。测试数据库仅位于TEMP，Broker为Fake/原生回调double，连接方法和新用例socket连接有禁止调用guard。
- 第9条事实来源是John本轮消息，不是读取10/9归档。原B22 full3各1302 passed+1 skipped仅是旧冻结基线证据，**不能算本补丁的全量验收**。
- 保留Claude操作手册（SHA `5d7eb6f043f4d933019f36b6cbfd28b28f8474c0b7097261f9bb77ae627133bf`）的外部修改、B23未跟踪指令和item8调查文件；不把它们列入本补丁提交范围。暂存区为空，HEAD未改变，无commit/tag/push或main操作。

## 2. 根因：代码可复现与真实事件待证部分

John事实：10/9 07:00:35 PT入场AAPL3股@332.37；order44为SELL STP LMT，GTC，stop330.41/limit320.49，TWS系统取消；14:00:35Z泛化identity mismatch P0，14:00:39Z又A gap5.2s P1；07:18:41 PT手动卖3@333.46。John提供的原因原文（其中省略号本就来自消息，不能冒称完整IBKR日志）：

> 被系统取消：由于限价320.49偏离止损价330.41太多，我们无法接纳您的止损限价单……此外，您也可以把委托单转换为算法委托单(IBALGO)

可复现的代码原因：

1. 终态先经过工作单/原生完整合同核验，失败即进入泛化身份hold并return，拒单分类无法到达。{cite('v6lite/engine.py','if (ev.identity_checked or ev.order_identity is not None) and not native_valid:', 'v6lite-batch22')}；{cite('v6lite/protection_engine.py','self.store.hold_intent(now, event.intent_id, "protection native status identity mismatch")','v6lite-batch22')}。
2. 旧拒单路径不仅要求201/202，还要求完整native、没有hold且满足特定文本；字段清空/price reset/缺perm/已有hold或一般系统取消理由可能被漏分。{cite('v6lite/protection_engine.py','if (event.error_code not in {201, 202}','v6lite-batch22')}。旧工作单身份不能据终态缺字段放宽，否则会赋予release/撤改单权；本修法另建display诊断，不修改原权限函数。
3. 原卡片body没有独立的券商原因字段；intent.note截到200字符也不能满足原文远端送达。{cite('v6lite/engine.py','fields["note"] = ev.message[:200]','v6lite-batch22')}。
4. 原B22已有一些A gap低级别抑制，不能仅凭John两张卡就断言运行中用的就是B22或锁存字段为何失效；真实10/9运行code_sha、身份失败具体字段、202/status先后及通知ACK仍待John提供下面第7节日志。离线证据证明上述可发生路径，不伪称已完整回放真实状态库。

## 3. 逐项「发现 → 改动 → 测试」

| 项 | 发现 | 改动/权限边界 | 独立测试 |
|---|---|---|---|
| 取消被归为identity mismatch | 缺mutable metadata、缺perm及立即取消无prior binding被旧严格identity挡住 | 保存独立terminal诊断；关联已知PROTECT请求的缺字段终态显示取消+原文，UNKNOWN/manual hold与possible数量不变，不产生CANCEL_SEEN或release权；非空外国硬身份冲突仍拒绝归属 | cleared/prices-reset 6参数、immediate2、cleared→完整tuple重放、12身份/release负对照 |
| 非特定stop文本的取消没冻结 | 原拒单只识别部分stop文本/code | 非主动终态取消事实与冻结/UNKNOWN、必要身份hold一并原子持久化，已有hold不清除，普通原因和其它码也P0；正常Lite主动撤单与末股TP路径保持原样；无自动重挂 | broad3、重启重放、完整TP1→TP2→主动202撤GTC→TP3→CLOSED对照 |
| 原因/数量/操作步骤不完整 | 原文可能丢失或裁剪；新鲜working一项不能证明完整数量 | 原文独立900 UTF16单位分段、每段含事件关联；只有取消之后同轮完整新鲜三源+强身份才显示P/S/P−S确定股数，T/X只卖出容量不当作止损覆盖；Ctrl+C→bid限价平仓或人工保护→确认持仓工作单；明确“不要点击 TWS 的重新提交/IBALGO 建议” | >200原文2、>1800UTF16原文、多卡ACK2、action原句、complete2、三源失败2 |
| P0后同事件又P1 | A gap/穿越/30分钟提醒会冲突或丢最高级别 | 同取消事件P0尝试/排队/ACK后不降级；成功ACK且状态未变不再发P0或P1，事件记录不删除；失败重试、材料变化、新事件独立处理；其它事件30分钟P1及恢复INFO保留。覆盖真正解除时结束取消display episode，审计不丢 | 5秒/trigger2、31min独立P1/INFO、重放重启、真实三源恢复后新的穿越P1、原独立trigger ACK/提醒回归 |
| 崩溃窗口 | notice/停止态先提交、身份hold后提交，旧CANCEL_SEEN可能在中途崩溃后残留 | 必要hold与诊断停止态同事务，保留原INTENT_STATUS/PROTECT_STATE审计，不改变release核验或Store事务 | 有效CANCEL_SEEN→冲突终态→capture后模拟崩溃，持久hold及重启release阻断 |
| 部分送达与提醒起点回归 | 第一尝试失败却按失败时间起算，或长原因已ACK段重发/丢尾段 | 固定待送批次内容/段数，失败只重试未ACK段；独立事件30分钟P1按最终成功ACK时刻计时；取消事件ACK后无定时提醒；原因只附同一个coverage终态事件 | 首卡失败/第二卡失败、未ACK原因与真实外部SELL独立卡、既有trigger_failed_P0_retries_actual_delivery_then_30_minute_P1保留原强断言 |

新源码定位（引用行号随本补丁冻结）：

- 分类与缺字段hold：{cite('v6lite/engine.py','terminal_metadata = self._protection_capture_terminal')}; {cite('v6lite/protection_cancellations.py','def _protection_terminal_tuple_matches')}; {cite('v6lite/protection_cancellations.py','def _protection_hold_terminal_metadata')}。
- 原子取消/冻结及原审计：{cite('v6lite/protection_cancellations.py','with self.store.tx():')}; {cite('v6lite/protection_cancellations.py','self.store.audit(now, "PROTECT_STATE"')}; {cite('v6lite/protection_cancellations.py','self.store.audit(now, "INTENT_STATUS"')}。
- 三项数字证明：{cite('v6lite/protection_cancellations.py','def _protection_cancel_quantity_proof')}; {cite('v6lite/protection_engine.py','self._protection_notice_evidence.pop')}。
- 原文和步骤：{cite('v6lite/protection_engine.py','不要点击 TWS 的重新提交/IBALGO 建议')}; {cite('v6lite/protection_engine.py','if units + width > 900:')}。
- ACK/不降级/恢复退休：{cite('v6lite/protection_incidents.py','def flush(state):')}; {cite('v6lite/protection_incidents.py','state["last_notice"] = now.isoformat()')}; {cite('v6lite/protection_incidents.py','if state.get("p0_only"):')}; {cite('v6lite/protection_incidents.py','"PROTECT_TERMINAL_EPISODE_RESOLVED"')}。

`_apply_releases`、`_protection_on_status`、`_protection_send_tp`、`_protection_modify`、`_protection_replace_after_cancel`、两种hard/preflight deadline函数与原B22 AST完全相同（serial_validation.json逐项列出）。没有item8止盈改价、价格管理算法、TWS预设变更或B空窗起点修复；第8条仍等Claude/John裁定后实施。

## 4. 串行Windows证据

Python：`{sys.version.splitlines()[0]}`；Windows：`{platform.platform()}`。所有本补丁测试仅聚焦子集，均在交易时段运行，未跑默认全量或借拆分绕过限制。源目录前后digest不变。

| 执行 | 结果 | source code_sha | 实际PT时间 |
|---|---|---|---|
{chr(10).join(result_lines)}

红证据为git archive分别导出的B20/B22完整原包，新spec放在包外；断言实际import来自所选包，排除缓存、旧pyc和新源码混入。39版中7项新增候选边界亦先自然红才由唯一实施Agent修复；最终{checks["green"]["totals"]["tests"]}版保留事件隔离/恢复序列，并新增崩溃注入和ACK后无重复卡断言；加强断言先在修前候选自然红，最终同一spec在两冻结基线上复跑红证据。全部结果有JSON/TXT/JUnit XML和sha，失败日志保留。

首次红的socket guard过早挡住Windows asyncio/eventkit本地socketpair，明确排除为产品红（invalid_red_attempt.json）。后续正常L7对照曾缺确认/扫描间隔及误设cancel intent状态，日志也保留；更正真实前置后该对照在两冻结基线均通过。31分钟独立P1原测试用通用中性卡查询任意body属测试前置错误，改显式AlertCard后仍检查真实远端送达。未隐藏或用重试冲掉产品失败；聚焦首轮10失败里标题/动作契约变化与真实ACK计时回归已逐项处理。新增原子持久段曾误调用自带BEGIN的update_protection，造成green_item9_run2的34项真实嵌套事务失败；定点改成外层事务内的两列SQL，保留rowcount检查和PROTECT_STATE审计，Store全局事务未改。focused_protection_run2剩1项是旧无原因/码0UNKNOWN场景误改为明确取消标题，只纠正其标题预期，UNKNOWN、gap及权限断言不变。独立复核随后指出本候选30分钟重复P0不满足第二十批“同一事件只发一次P0”；按第9条同时禁止低级卡的要求，加强无重复P0/P1测试并自然红后修正。还新增capture提交后模拟崩溃测试，证明身份hold必须与诊断停止态原子写入。所有中间失败均保留。

四个旧测试文件只更新本次获授权的标题、Ctrl+C/bid动作、未知→确定数量材料变化和同终态成功ACK后不再提醒的契约；不改交易状态、release、强身份或独立trigger ACK计时强断言。新spec SHA-256：`{sha(spec)}`。

**尚待完成的全量 gate：交易日13:30 PT后，串行运行 `.venv` 中的 `python -u -B docs/batch22_item9_evidence/run_item9_checks.py full`（内部每轮重新检查时段、源digest、保留文档和空暂存），默认v6lite/tests+scripts/tests连续3次。未执行前不得借旧B22的3次结果宣称本补丁通过，不提交新修复快照、不重写旧tag。**

## 5. 实际渲染样例

下列MemoryChannel内容是FakeBroker真正入场/取消callback后生成，并非手写卡片。合成止损价和orderId不应与John真实10/9混同；首次callback不阻塞查询，初张尚未具备新鲜三源，后续确定数量是材料变化，可发新的P0，之后相同状态不刷屏。

{samples}

## 6. 本补丁与保留基线

生产范围：v6lite/models.py、broker/ibkr.py、engine.py、protection_engine.py、protection_incidents.py及新protection_cancellations.py。测试范围：新item9 spec及四个旧契约文件。报告/runner/样例在docs/batch22_item9_evidence。

原Round22报告SHA保持`cbd40da3fe3dd2cc8f6e3e1b114d4f9e2e38cbd48799ac02ee3c44b6fe978240`；原B22 manifest SHA保持`208ef222f7a3cb1aae2c90e40a81aefb8638fa5cf4c5a465b4c3a0ebe9f55187`；旧tag指向不改。旧报告清单针对旧tag，本补丁另有下面清单，不冒称旧201项对新工作树全部一致。

## 7. John需提供的真实日志片段（Codex不读归档）

1. 当天启动版本/code_sha、启动/重启时间与模式；不要附账户或通知凭据。
2. 07:00:35 PT /14:00:35Z entry execDetails入账、PROTECT计划、PROTECT_SENT/dispatch，order44对应orderRef。
3. order44的openOrder/orderStatus原始字段及时间：orderId/permId/clientId/conId/type/tif/outsideRth/auxPrice/lmtPrice/total/filled/remaining；account用一致的脱敏代号，保留是否匹配信息。
4. 全部201/202及reqId、未经截断的原因、status先后；如有Lite主动cancel request也附上。
5. 14:00:35Z P0和14:00:39Z P1完整正文、事件键、通知排队与成功/失败ACK；A gap起点及elapsed记录。
6. 同段三项QUERY开始/结束/失败/超时和结果：positions、全部客户端working orders、executions。
7. Ctrl+C/SHUTDOWN和07:18:41手动平仓execDetails、之后position/working订单；无需给数据库文件。

真实Paper仍需确认：permId=0立即拒绝的具体字段、错误与status先后、STP取消真实原因格式、全部客户端工作单可见性、迟到成交和通知重启重试。禁止自动转IBALGO/自动改价，不修改或迁移归档。

## 8. SHA-256清单

下表{len(items)}项包含整个当前v6lite包和本补丁证据，排除__pycache__、本报告、manifest自身、最终独立review收据，避免循环。JSON manifest再加入本报告自身hash；最终独立收据另行核对，不回填冻结报告。

| 文件 | bytes | SHA-256 |
|---|---:|---|
{table}
'''
REPORT.write_text(report,encoding='utf-8',newline='\n')
items.append({'path':REPORT.relative_to(ROOT).as_posix(),'bytes':REPORT.stat().st_size,'sha256':sha(REPORT)})
manifest={'code_sha':code_sha,'head':verified['head'],'entries':items,'excluded':['__pycache__','docs/batch22_item9_evidence/sha256_manifest.json','docs/batch22_item9_evidence/independent_review_receipt.json','Claude operator manual','B23 instructions','item8 research','runtime and user state'],'full_gate':'pending after 13:30 PT'}
(EV/'sha256_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'report':str(REPORT),'report_sha256':sha(REPORT),'manifest_entries':len(items),'manifest_sha256':sha(EV/'sha256_manifest.json'),'code_sha':code_sha},ensure_ascii=False))
