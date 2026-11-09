# 第22批第9条补充修复报告（2026-10-09）

本轮只在 `C:\Dev\V6Lite-dev` 的 dev 工作树修复券商取消保护单的分类/卡片/同事件升级顺序。**离线聚焦验收已完成，新增补丁的默认全量连续3次尚未执行；本报告不是运行放行或新tag交付。** 工作树冻结，原第二十二批提交/tag/报告保持原样；不覆盖 `v6lite-batch22`。

## 1. 基线、分工和边界

- 冻结原B22：`42c77da139ce308ca133eb7e2e40beaf33ecac9d`，code_sha=`d42320d23de79d6e`；B20：`c5a39952635943dde5f1ae7f2a52da84c8327375`，code_sha=`cfe16edd7a7e4d7f`。
- 新工作树 code_sha：**`f27e144de9eb406b`**，指整个v6lite目录的路径+NUL+内容+NUL、按相对路径排序的SHA-256前16位，排除__pycache__；包含测试/示例，不仅生产源码。
- 唯一生产实施者：`batch20_protection_incident`；独立测试/旧契约对照：`batch20_equivalents`；只读独立复核：`batch20_time_logs`；root串行跑红/绿和聚焦回归、生成本报告与清单。
- 写前没有v6lite运行进程（初次预检及实施Agent再核验）；未连接TWS/Gateway，未运行run/g2_probe，未读取或修改任何User状态/归档、运行仓库、John配置、`.env`或通知凭据。测试数据库仅位于TEMP，Broker为Fake/原生回调double，连接方法和新用例socket连接有禁止调用guard。
- 第9条事实来源是John本轮消息，不是读取10/9归档。原B22 full3各1302 passed+1 skipped仅是旧冻结基线证据，**不能算本补丁的全量验收**。
- 保留Claude操作手册（SHA `5d7eb6f043f4d933019f36b6cbfd28b28f8474c0b7097261f9bb77ae627133bf`）的外部修改、B23未跟踪指令和item8调查文件；不把它们列入本补丁提交范围。暂存区为空，HEAD未改变，无commit/tag/push或main操作。

## 2. 根因：代码可复现与真实事件待证部分

John事实：10/9 07:00:35 PT入场AAPL3股@332.37；order44为SELL STP LMT，GTC，stop330.41/limit320.49，TWS系统取消；14:00:35Z泛化identity mismatch P0，14:00:39Z又A gap5.2s P1；07:18:41 PT手动卖3@333.46。John提供的原因原文（其中省略号本就来自消息，不能冒称完整IBKR日志）：

> 被系统取消：由于限价320.49偏离止损价330.41太多，我们无法接纳您的止损限价单……此外，您也可以把委托单转换为算法委托单(IBALGO)

可复现的代码原因：

1. 终态先经过工作单/原生完整合同核验，失败即进入泛化身份hold并return，拒单分类无法到达。`v6lite/engine.py:887`（`v6lite-batch22`）：`if (ev.identity_checked or ev.order_identity is not None) and not native_valid:`；`v6lite/protection_engine.py:353`（`v6lite-batch22`）：`self.store.hold_intent(now, event.intent_id, "protection native status identity mismatch")`。
2. 旧拒单路径不仅要求201/202，还要求完整native、没有hold且满足特定文本；字段清空/price reset/缺perm/已有hold或一般系统取消理由可能被漏分。`v6lite/protection_engine.py:397`（`v6lite-batch22`）：`if (event.error_code not in {201, 202} or event.raw_status not in {"Cancelled", "ApiCancelled", "Inactive"} or`。旧工作单身份不能据终态缺字段放宽，否则会赋予release/撤改单权；本修法另建display诊断，不修改原权限函数。
3. 原卡片body没有独立的券商原因字段；intent.note截到200字符也不能满足原文远端送达。`v6lite/engine.py:934`（`v6lite-batch22`）：`fields["note"] = ev.message[:200]`。
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

- 分类与缺字段hold：`v6lite/engine.py:888`：`terminal_metadata = self._protection_capture_terminal(now, ev, native_valid=native_valid)`; `v6lite/protection_cancellations.py:29`：`def _protection_terminal_tuple_matches(self, row, event):`; `v6lite/protection_cancellations.py:129`：`def _protection_hold_terminal_metadata(self, now, event):`。
- 原子取消/冻结及原审计：`v6lite/protection_cancellations.py:93`：`with self.store.tx():`; `v6lite/protection_cancellations.py:103`：`self.store.audit(now, "PROTECT_STATE", position_id=row["position_id"], state="UNKNOWN")`; `v6lite/protection_cancellations.py:117`：`self.store.audit(now, "INTENT_STATUS", intent_id=event.intent_id,`。
- 三项数字证明：`v6lite/protection_cancellations.py:145`：`def _protection_cancel_quantity_proof(self, now, row):`; `v6lite/protection_engine.py:516`：`self._protection_notice_evidence.pop(row["position_id"], None)`。
- 原文和步骤：`v6lite/protection_engine.py:114`：`"broker cancelled": "1）先按 Ctrl+C 停止 Lite；2）在同一 TWS 核对持仓和所有工作卖单，再按当前 bid 用限价单平仓或人工建立保护，避免重复卖单；3）重新核对持仓和工作单。不要点击 TWS 的重新提交/IBALGO 建议；不要自动改价重挂保护单。只有确认 0 股、无在途卖单、成交静默期和原 release 强门槛均满足后才执行 release；身份或证据不足保持拒绝，不要手改状态库、新开仓或自动重挂保护单。",`; `v6lite/protection_engine.py:277`：`if units + width > 900:`。
- ACK/不降级/恢复退休：`v6lite/protection_incidents.py:64`：`def flush(state):`; `v6lite/protection_incidents.py:115`：`state["last_notice"] = now.isoformat()`; `v6lite/protection_incidents.py:147`：`if state.get("p0_only"):`; `v6lite/protection_incidents.py:181`：`self.store.audit(now, "PROTECT_TERMINAL_EPISODE_RESOLVED", position_id=row["position_id"], reason=reason)`。

`_apply_releases`、`_protection_on_status`、`_protection_send_tp`、`_protection_modify`、`_protection_replace_after_cancel`、两种hard/preflight deadline函数与原B22 AST完全相同（serial_validation.json逐项列出）。没有item8止盈改价、价格管理算法、TWS预设变更或B空窗起点修复；第8条仍等Claude/John裁定后实施。

## 4. 串行Windows证据

Python：`3.11.9 (tags/v3.11.9:de54cf5, Apr  2 2024, 10:12:12) [MSC v.1938 64 bit (AMD64)]`；Windows：`Windows-10-10.0.26300-SP0`。所有本补丁测试仅聚焦子集，均在交易时段运行，未跑默认全量或借拆分绕过限制。源目录前后digest不变。

| 执行 | 结果 | source code_sha | 实际PT时间 |
|---|---|---|---|
| B20 / `red_batch20_run8` | 18 passed / 24 failed / 0 errors / 0 skipped | `cfe16edd7a7e4d7f` | 2026-10-09T08:56:27.624293-07:00 — 2026-10-09T08:56:33.528452-07:00 |
| B22 / `red_batch22_run8` | 20 passed / 22 failed / 0 errors / 0 skipped | `d42320d23de79d6e` | 2026-10-09T08:56:34.632165-07:00 — 2026-10-09T08:56:40.794154-07:00 |
| candidate / `candidate_red_item9_run2` | 40 passed / 2 failed / 0 errors / 0 skipped | `614ad81340ebd0e0` | 2026-10-09T08:53:47.781354-07:00 — 2026-10-09T08:53:54.109339-07:00 |
| green / `green_item9_run5` | 42 passed / 0 failed / 0 errors / 0 skipped | `f27e144de9eb406b` | 2026-10-09T08:56:52.768043-07:00 — 2026-10-09T08:56:58.511011-07:00 |
| focused / `focused_protection_run4` | 291 passed / 0 failed / 0 errors / 0 skipped | `f27e144de9eb406b` | 2026-10-09T08:57:06.487319-07:00 — 2026-10-09T08:57:27.262502-07:00 |

红证据为git archive分别导出的B20/B22完整原包，新spec放在包外；断言实际import来自所选包，排除缓存、旧pyc和新源码混入。39版中7项新增候选边界亦先自然红才由唯一实施Agent修复；最终42版保留事件隔离/恢复序列，并新增崩溃注入和ACK后无重复卡断言；加强断言先在修前候选自然红，最终同一spec在两冻结基线上复跑红证据。全部结果有JSON/TXT/JUnit XML和sha，失败日志保留。

首次红的socket guard过早挡住Windows asyncio/eventkit本地socketpair，明确排除为产品红（invalid_red_attempt.json）。后续正常L7对照曾缺确认/扫描间隔及误设cancel intent状态，日志也保留；更正真实前置后该对照在两冻结基线均通过。31分钟独立P1原测试用通用中性卡查询任意body属测试前置错误，改显式AlertCard后仍检查真实远端送达。未隐藏或用重试冲掉产品失败；聚焦首轮10失败里标题/动作契约变化与真实ACK计时回归已逐项处理。新增原子持久段曾误调用自带BEGIN的update_protection，造成green_item9_run2的34项真实嵌套事务失败；定点改成外层事务内的两列SQL，保留rowcount检查和PROTECT_STATE审计，Store全局事务未改。focused_protection_run2剩1项是旧无原因/码0UNKNOWN场景误改为明确取消标题，只纠正其标题预期，UNKNOWN、gap及权限断言不变。独立复核随后指出本候选30分钟重复P0不满足第二十批“同一事件只发一次P0”；按第9条同时禁止低级卡的要求，加强无重复P0/P1测试并自然红后修正。还新增capture提交后模拟崩溃测试，证明身份hold必须与诊断停止态原子写入。所有中间失败均保留。

四个旧测试文件只更新本次获授权的标题、Ctrl+C/bid动作、未知→确定数量材料变化和同终态成功ACK后不再提醒的契约；不改交易状态、release、强身份或独立trigger ACK计时强断言。新spec SHA-256：`58186f1047bf79a39e85e96be7140a6cbb740ae152ce9cfd000365e5daee4341`。

**尚待完成的全量 gate：交易日13:30 PT后，串行运行 `.venv` 中的 `python -u -B docs/batch22_item9_evidence/run_item9_checks.py full`（内部每轮重新检查时段、源digest、保留文档和空暂存），默认v6lite/tests+scripts/tests连续3次。未执行前不得借旧B22的3次结果宣称本补丁通过，不提交新修复快照、不重写旧tag。**

## 5. 实际渲染样例

下列MemoryChannel内容是FakeBroker真正入场/取消callback后生成，并非手写卡片。合成止损价和orderId不应与John真实10/9混同；首次callback不阻塞查询，初张尚未具备新鲜三源，后续确定数量是材料变化，可发新的P0，之后相同状态不刷屏。

# 第22批第9条：FakeBroker实际渲染样例

全部来自合成3股入场、原生callback double和MemoryChannel，状态在TEMP；不是10/9真实账户日志。

## complete_three_sources

```text
🔴 P0 · 券商已取消保护单
发生了什么：AAPL 持仓数量=3 股 保护单已确认=0 股 保护单可能=0 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实 股 可能超卖=待核实 股；券商已明确拒绝该保护请求；orderRef=v6l-protect-a349f965860d4dfaa9e5 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=未获券商确认 工作单证据时间=2026-10-06T14:00:30+00:00；broker explicitly refused the STP request; no automatic replacement；券商状态=Cancelled code=202；取消诊断与执行权限独立核验
券商原因原文：Order canceled - reason: We cannot accept your stop order. Synthetic regulatory price check.
影响：有效 STP 保护 S=0 股，未保护 P−S=待核实 股；止盈和外部卖单仅列卖出容量，不能当作止损保护。新开仓继续冻结，Lite 不自动重挂。
你要做什么：1）先按 Ctrl+C 停止 Lite；2）在同一 TWS 核对持仓和所有工作卖单，再按当前 bid 用限价单平仓或人工建立保护，避免重复卖单；3）重新核对持仓和工作单。不要点击 TWS 的重新提交/IBALGO 建议；不要自动改价重挂保护单。只有确认 0 股、无在途卖单、成交静默期和原 release 强门槛均满足后才执行 release AAPL；身份或证据不足保持拒绝，不要手改状态库、新开仓或自动重挂保护单。
```

```text
🔴 P0 · 券商已取消保护单
发生了什么：AAPL 持仓数量=3 股 保护单已确认=0 股 保护单可能=0 股 止盈在途=0 股 外部卖单在途=0 股 未保护=3 股 可能超卖=0 股；券商已明确拒绝该保护请求；orderRef=v6l-protect-a349f965860d4dfaa9e5 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=未获券商确认 工作单证据时间=2026-10-06T14:00:30+00:00；GTC absent from complete working snapshot; no duplicate submission；券商状态=Cancelled code=202；取消诊断与执行权限独立核验
券商原因原文：Order canceled - reason: We cannot accept your stop order. Synthetic regulatory price check.
影响：有效 STP 保护 S=0 股，未保护 P−S=3 股；止盈和外部卖单仅列卖出容量，不能当作止损保护。新开仓继续冻结，Lite 不自动重挂。
你要做什么：1）先按 Ctrl+C 停止 Lite；2）在同一 TWS 核对持仓和所有工作卖单，再按当前 bid 用限价单平仓或人工建立保护，避免重复卖单；3）重新核对持仓和工作单。不要点击 TWS 的重新提交/IBALGO 建议；不要自动改价重挂保护单。只有确认 0 股、无在途卖单、成交静默期和原 release 强门槛均满足后才执行 release AAPL；身份或证据不足保持拒绝，不要手改状态库、新开仓或自动重挂保护单。
```

## terminal_metadata_missing

```text
🔴 P0 · 券商已取消保护单
发生了什么：AAPL 持仓数量=3 股 保护单已确认=待核实（上次确认 0） 股 保护单可能=3 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实 股 可能超卖=待核实 股；券商已回报取消；终态身份尚未获完整授权核验或原人工 hold 未解除；保持人工核验，不授予释放权限；orderRef=v6l-protect-e9407e6c38864ef6a3a0 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=未获券商确认 工作单证据时间=2026-10-06T14:00:30+00:00；券商终态诊断已关联本请求；原生合同或人工核验门控未完成，仅诊断不授予释放或撤改单权限；券商状态=Cancelled code=202；取消诊断与执行权限独立核验
券商原因原文：Order canceled - reason: We cannot accept your stop order. Synthetic regulatory price check.
影响：有效 STP 保护 S=待核实（上次确认 0） 股，未保护 P−S=待核实 股；止盈和外部卖单仅列卖出容量，不能当作止损保护。新开仓继续冻结，Lite 不自动重挂。
你要做什么：1）先按 Ctrl+C 停止 Lite；2）在同一 TWS 核对持仓和所有工作卖单，再按当前 bid 用限价单平仓或人工建立保护，避免重复卖单；3）重新核对持仓和工作单。不要点击 TWS 的重新提交/IBALGO 建议；不要自动改价重挂保护单。只有确认 0 股、无在途卖单、成交静默期和原 release 强门槛均满足后才执行 release AAPL；身份或证据不足保持拒绝，不要手改状态库、新开仓或自动重挂保护单。
```

## long_original_reason

```text
🔴 P0 · 券商已取消保护单
发生了什么：AAPL 持仓数量=3 股 保护单已确认=0 股 保护单可能=0 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实 股 可能超卖=待核实 股；券商已明确拒绝该保护请求；orderRef=v6l-protect-a26d32ee0fc44ee4bf47 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=未获券商确认 工作单证据时间=2026-10-06T14:00:30+00:00；broker explicitly refused the STP request; no automatic replacement；券商状态=Cancelled code=202；取消诊断与执行权限独立核验
券商原因原文：Order canceled - reason: We cannot accept your stop order. Synthetic broker diagnostic; no live order or account is involved. Original diagnostic text must reach the remote operator without truncation. Original diagnostic text must reach the remote operator without truncation. Original diagnostic text must reach the remote operator without truncation. Original diagnostic text must reach the remote operator without truncation. END-OF-ORIGINAL-REASON-保留原文
影响：有效 STP 保护 S=0 股，未保护 P−S=待核实 股；止盈和外部卖单仅列卖出容量，不能当作止损保护。新开仓继续冻结，Lite 不自动重挂。
你要做什么：1）先按 Ctrl+C 停止 Lite；2）在同一 TWS 核对持仓和所有工作卖单，再按当前 bid 用限价单平仓或人工建立保护，避免重复卖单；3）重新核对持仓和工作单。不要点击 TWS 的重新提交/IBALGO 建议；不要自动改价重挂保护单。只有确认 0 股、无在途卖单、成交静默期和原 release 强门槛均满足后才执行 release AAPL；身份或证据不足保持拒绝，不要手改状态库、新开仓或自动重挂保护单。
```

```text
🔴 P0 · 券商已取消保护单
发生了什么：AAPL 持仓数量=3 股 保护单已确认=0 股 保护单可能=0 股 止盈在途=0 股 外部卖单在途=0 股 未保护=3 股 可能超卖=0 股；券商已明确拒绝该保护请求；orderRef=v6l-protect-a26d32ee0fc44ee4bf47 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=未获券商确认 工作单证据时间=2026-10-06T14:00:30+00:00；GTC absent from complete working snapshot; no duplicate submission；券商状态=Cancelled code=202；取消诊断与执行权限独立核验
券商原因原文：Order canceled - reason: We cannot accept your stop order. Synthetic broker diagnostic; no live order or account is involved. Original diagnostic text must reach the remote operator without truncation. Original diagnostic text must reach the remote operator without truncation. Original diagnostic text must reach the remote operator without truncation. Original diagnostic text must reach the remote operator without truncation. END-OF-ORIGINAL-REASON-保留原文
影响：有效 STP 保护 S=0 股，未保护 P−S=3 股；止盈和外部卖单仅列卖出容量，不能当作止损保护。新开仓继续冻结，Lite 不自动重挂。
你要做什么：1）先按 Ctrl+C 停止 Lite；2）在同一 TWS 核对持仓和所有工作卖单，再按当前 bid 用限价单平仓或人工建立保护，避免重复卖单；3）重新核对持仓和工作单。不要点击 TWS 的重新提交/IBALGO 建议；不要自动改价重挂保护单。只有确认 0 股、无在途卖单、成交静默期和原 release 强门槛均满足后才执行 release AAPL；身份或证据不足保持拒绝，不要手改状态库、新开仓或自动重挂保护单。
```



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

下表199项包含整个当前v6lite包和本补丁证据，排除__pycache__、本报告、manifest自身、最终独立review收据，避免循环。JSON manifest再加入本报告自身hash；最终独立收据另行核对，不回填冻结报告。

| 文件 | bytes | SHA-256 |
|---|---:|---|
| `docs/batch22_item9_evidence/agent_planned_control_20261009T145440Z_6652b822_attempt2_metadata.json` | 1337 | `f27486aeb35d3343762e96e9d9caec60cbbf61825df4b3fb895a3dacee19c50a` |
| `docs/batch22_item9_evidence/agent_planned_control_20261009T145440Z_6652b822_metadata.json` | 1901 | `d5a94f9774217085408b648333a63691ebede9544a80e8516cb75c23e8a93a55` |
| `docs/batch22_item9_evidence/agent_planned_control_B20_20261009T145440Z_6652b822_attempt1.log` | 836 | `44798ce172db709ad1599071c42c6b574623239c840215cfad9b14003792736e` |
| `docs/batch22_item9_evidence/agent_planned_control_B20_20261009T145440Z_6652b822_attempt1.xml` | 927 | `2e08036713426d4c5de4006e54cf8b38db590828fd9e132b72f80f9d88f62837` |
| `docs/batch22_item9_evidence/agent_planned_control_B20_20261009T145440Z_6652b822_attempt2.log` | 100 | `f04e236a140756a8601c5fee98e861ab0eeeea06c2f332062e97abc85bb7c47d` |
| `docs/batch22_item9_evidence/agent_planned_control_B20_20261009T145440Z_6652b822_attempt2.xml` | 423 | `ed57ae0688855d8647a800a28f513bac28709b006e977f66660a20a6bfb4d232` |
| `docs/batch22_item9_evidence/agent_planned_control_B22_20261009T145440Z_6652b822_attempt1.log` | 836 | `44798ce172db709ad1599071c42c6b574623239c840215cfad9b14003792736e` |
| `docs/batch22_item9_evidence/agent_planned_control_B22_20261009T145440Z_6652b822_attempt1.xml` | 927 | `24394075e3d5e560da8188a85c865a16f7f82d715f7ea702651343b1c0eb506a` |
| `docs/batch22_item9_evidence/agent_planned_control_B22_20261009T145440Z_6652b822_attempt2.log` | 100 | `f04e236a140756a8601c5fee98e861ab0eeeea06c2f332062e97abc85bb7c47d` |
| `docs/batch22_item9_evidence/agent_planned_control_B22_20261009T145440Z_6652b822_attempt2.xml` | 423 | `6c9d0cbb0ff93a7848a76607e4e7d2a39adeea3e141ac0510ddbc3530be77953` |
| `docs/batch22_item9_evidence/build_item9_report.py` | 18753 | `1a4278647a0b1579be41a49b1983ee732e647a35795a29708c0a4bc503fc8c6b` |
| `docs/batch22_item9_evidence/candidate_red_item9.json` | 10056 | `26122d735a223ef6a250550bd6a6fb5ad1755e6d67352d08a4c64af07444cb3d` |
| `docs/batch22_item9_evidence/candidate_red_item9.txt` | 15672 | `df872656b7301bdfcaa7317c91a6a7cacb54bd7025d0aae69d90c9698b8447e1` |
| `docs/batch22_item9_evidence/candidate_red_item9.xml` | 14886 | `c054211de5c2046a6bcb82e0ae08c71eab45c44662ee5191933cf7c18479b3e2` |
| `docs/batch22_item9_evidence/candidate_red_item9_run2.json` | 9306 | `aa1fdc4e56a5bb9a3ad3a32d6ebf81ddc62e5f95baa0f810eaca83fad029f888` |
| `docs/batch22_item9_evidence/candidate_red_item9_run2.txt` | 4824 | `8c1c9017de2b9c4b0942bdb68b5a76c9bcb852aef428dbbed52bb94ed87711b9` |
| `docs/batch22_item9_evidence/candidate_red_item9_run2.xml` | 10820 | `9df73b43c4757828e23ae0d165bc2a4b0ddaddb48481bceef2f7fbf8e81f095f` |
| `docs/batch22_item9_evidence/card_samples.json` | 9383 | `3c725101ea41292935236faf781596e390e28e394d91d0d7e4fd354577c753a2` |
| `docs/batch22_item9_evidence/card_samples.md` | 8757 | `9765a0665220b0594f8998e1186c608906d1385d70f9f3bf09e89cfe1046e1c4` |
| `docs/batch22_item9_evidence/focused_protection.json` | 54424 | `1d905947713f8df56028d5c3c726c44fa7a1c91d1b33b50c23d0a34f6cd6f477` |
| `docs/batch22_item9_evidence/focused_protection.txt` | 25863 | `daf514ca21d060f620d8abd2115305ba242cbe8c507d5d0e3caf229fe0eb2130` |
| `docs/batch22_item9_evidence/focused_protection.xml` | 64531 | `c8feec8b51b70e64be11a12178373ed38e234dc826ae988b46f4447aa81929aa` |
| `docs/batch22_item9_evidence/focused_protection_run2.json` | 48481 | `7729dd9a0abe1ab6510f01c0a6d1a65ee48e369d5d8c74a10212133416914c34` |
| `docs/batch22_item9_evidence/focused_protection_run2.txt` | 2658 | `299f38383691d7488f204733a0bee170e4ffb83d719f3890de33a6605455dbf5` |
| `docs/batch22_item9_evidence/focused_protection_run2.xml` | 49966 | `43e4ff411cf902c08811edec3dfd9a6358acc47d630dfba9da2d930ec8524e94` |
| `docs/batch22_item9_evidence/focused_protection_run3.json` | 48368 | `ca1cf09756453e1c146644c3cf77602fe70b92afe4a9e14f4c882b54732b56e5` |
| `docs/batch22_item9_evidence/focused_protection_run3.txt` | 421 | `02a3735878bdb5002e4e629bf388d17d4dbdc95e307e570b1da0c8e56d17dd3b` |
| `docs/batch22_item9_evidence/focused_protection_run3.xml` | 49500 | `4034621cf9150c2c88ec5823299e983bfee57f8a5e38b755cb2f6343538b4927` |
| `docs/batch22_item9_evidence/focused_protection_run4.json` | 48368 | `d7a17cb694c86c6a450319d74c55f9bb25b3255646ba8b654ece25f4c819158d` |
| `docs/batch22_item9_evidence/focused_protection_run4.txt` | 421 | `c9eae11a574387ff1118fc9bfa2741ad63e256896cc0c942de1aaebfcb3a44af` |
| `docs/batch22_item9_evidence/focused_protection_run4.xml` | 49500 | `d5c8f16af3ff3c95e75912b55fb16638a8be59bdd4c7bb5cf445eca7c1e14e1b` |
| `docs/batch22_item9_evidence/green_item9.json` | 6014 | `716f47c5832e3ef766f893cce602e0f5295367fc1f78041ec82421220047a44b` |
| `docs/batch22_item9_evidence/green_item9.txt` | 2758 | `89e0f9c18acfe29e483ee95f6e46669d0df41d4f80d9fbf140c02d6a212be55c` |
| `docs/batch22_item9_evidence/green_item9.xml` | 6398 | `c17d68aa755d2a44d9afbc80e987a8052c4f9fa8c28c1e89d6a8346b35b29db2` |
| `docs/batch22_item9_evidence/green_item9_run2.json` | 10740 | `7089189256f699ae1b574d5ba0d9d364f47b6fb74821afd58614103bdf111479` |
| `docs/batch22_item9_evidence/green_item9_run2.txt` | 66493 | `3b00520825ef2d312ce2c2ba250ad7c225c6fd9d5f21500cceb741a6cd179a7a` |
| `docs/batch22_item9_evidence/green_item9_run2.xml` | 55945 | `21b5a30b0158f7ea581473974c9d3ae919f29080fb089b99c9da88d9c8f2985b` |
| `docs/batch22_item9_evidence/green_item9_run3.json` | 8223 | `b4b201bf4bcaeda583471e52393c5ee12fa9e530dd3d29ca237f5260a52126be` |
| `docs/batch22_item9_evidence/green_item9_run3.txt` | 99 | `ca176a20756611c5944ed0df6b8f80b233a68f1b95c8ac2b6f50d633e26fcd73` |
| `docs/batch22_item9_evidence/green_item9_run3.xml` | 8200 | `bf088f6de1840cb99972c56991f29dcfe8827e88e9beaead0dcf1cecede25ab3` |
| `docs/batch22_item9_evidence/green_item9_run4.json` | 8223 | `268f4da5f1e2793e24c1e43259c32be09f7ae898f8371e98e867c326e7726718` |
| `docs/batch22_item9_evidence/green_item9_run4.txt` | 99 | `b922aa1ea2b9b20fc59e835514350bd527425557a35a352044d8d642f7be2996` |
| `docs/batch22_item9_evidence/green_item9_run4.xml` | 8200 | `f05bf6aeffd88e5364c34d7f8d2b1f4026e3ad4a78cb642e984669c8db590c66` |
| `docs/batch22_item9_evidence/green_item9_run5.json` | 8402 | `74e551f516ea0721c22352f7b59f5f5031b0177bbc10b7562336a50ea81fdc04` |
| `docs/batch22_item9_evidence/green_item9_run5.txt` | 99 | `5b8b7b0c2079f16f03a2052e2a0078fb4ddbad66d959abd836e0b32430e6d1a9` |
| `docs/batch22_item9_evidence/green_item9_run5.xml` | 8387 | `410ea92ef5fcffb590da2947ee434e9137f02f295adc17b1afcc1b9d7289b3ad` |
| `docs/batch22_item9_evidence/invalid_red_attempt.json` | 307 | `2320d5b44d81bafe6b408337cb5ed47f624e6cf4471637178d4bf93b2949acd7` |
| `docs/batch22_item9_evidence/preflight.json` | 774 | `7eda252f8823ff0625494c352cd6f2b54b1fe1eae2eae91826c913b3c64fffbe` |
| `docs/batch22_item9_evidence/red_batch20.json` | 7801 | `d68370a257085b8752a11b8d656405959170465ba6b962864a1757f4662e31aa` |
| `docs/batch22_item9_evidence/red_batch20.txt` | 74971 | `8b81bfea2b086f5f239a3586fc6ba52d313074c5b86d0ef4258d9f52b6bae022` |
| `docs/batch22_item9_evidence/red_batch20.xml` | 76263 | `9e896471ac8a504203f37d4b08efa8e7fdd4794e43b83e8a7c3ac2e1163fd496` |
| `docs/batch22_item9_evidence/red_batch20_run2.json` | 13701 | `ef8a90183a256938a1e36a791d7367c93913e688fbac3ecf7a93dc95d388ba5c` |
| `docs/batch22_item9_evidence/red_batch20_run2.txt` | 25579 | `7b385f073553b7b8e5e076389b79e9ed930fe5deeb158a33e0ac2add43a3cddb` |
| `docs/batch22_item9_evidence/red_batch20_run2.xml` | 24193 | `73afbd6a305adad59fda24629f3b2c23a263b5265b6a5e1c3f61dfa9ddfb01ae` |
| `docs/batch22_item9_evidence/red_batch20_run3.json` | 13671 | `09dd19b6fb30c4c085878442998d50b1793ae6d1d9911c5b6fe7d05f194fda2d` |
| `docs/batch22_item9_evidence/red_batch20_run3.txt` | 25572 | `fd48cb1ae2f7dc619c4037dff949db4edb26fc7d79b689eb6a2e71172718a9f2` |
| `docs/batch22_item9_evidence/red_batch20_run3.xml` | 24145 | `fff5df5e976f0266ff3b0a41fa5b2d1bd5f13221b1ae26e7a51a4965938b7870` |
| `docs/batch22_item9_evidence/red_batch20_run4.json` | 13623 | `19edaa48a035fab987af8594d00c6da8a1e2f13cb13c8b177e5628db8daccef8` |
| `docs/batch22_item9_evidence/red_batch20_run4.txt` | 25502 | `b336f4ffdcb86a7b71db68acf854acc95c17f285e9a54e8e3cf8a573dd352532` |
| `docs/batch22_item9_evidence/red_batch20_run4.xml` | 24026 | `eb9e852bd02c0d53efe3e28ae2c9e4e3065f3ea89f0d0d7cb5fd0eaf76317ad2` |
| `docs/batch22_item9_evidence/red_batch20_run5.json` | 13611 | `a56a0f76928f99d9ef73f0db1a6b8db868ff060201f147c3280a5059a1e9f675` |
| `docs/batch22_item9_evidence/red_batch20_run5.txt` | 25133 | `24fe6048c8c3b0fc1219e9e825f23e2c77125dc9eede040df0e49fa5cb49f096` |
| `docs/batch22_item9_evidence/red_batch20_run5.xml` | 23783 | `80feaf789b3174f41f12c2310d966abeabdd94a20ceadaffcaa4f75f6c77a1ac` |
| `docs/batch22_item9_evidence/red_batch20_run6.json` | 21230 | `63a97a8b57977b8cfd4b8fd276a917ce0008bfad98c4db7c714d131769d6b497` |
| `docs/batch22_item9_evidence/red_batch20_run6.txt` | 42851 | `e0cbb999dce1bba079a6b518f7d68a2fa6c12c20476f099ed5dc113f96177db4` |
| `docs/batch22_item9_evidence/red_batch20_run6.xml` | 39513 | `65e9e1e09ed9bcb0c47fc2da46599d5fd9fd6b411d6901f33671c785505a3712` |
| `docs/batch22_item9_evidence/red_batch20_run7.json` | 21593 | `d878d8f27dd571db86f7a8634949d8321a828b60455c465fdfc2aff86ae34d5a` |
| `docs/batch22_item9_evidence/red_batch20_run7.txt` | 43954 | `e338ce98f1a39db812534cb3dcab81bed4c0bf38b74649e0559c2b7112a67bdf` |
| `docs/batch22_item9_evidence/red_batch20_run7.xml` | 40041 | `76fb9c02c8fd8d66b121c2d59db90947770fac4cbb9081b40518e096d6712bef` |
| `docs/batch22_item9_evidence/red_batch20_run8.json` | 21772 | `1fe42d95d83f42fa1c5af6c0ad1d75348d59055230a2bc2ea676fc1ae1380f71` |
| `docs/batch22_item9_evidence/red_batch20_run8.txt` | 43954 | `849f68f9ebd5b1377c37e371df1eb7efc99d80a6b3e2339fd4a6b1f288fc8c92` |
| `docs/batch22_item9_evidence/red_batch20_run8.xml` | 40183 | `f4a701cb38ea6746a1f49bbc2409fd20c320388b81d345cd74aa5672f85c2177` |
| `docs/batch22_item9_evidence/red_batch22.json` | 7801 | `d17b775ebbf6ec344e2c08f1b944258a8f58c52f4cb0ca0c20a44e309b84e288` |
| `docs/batch22_item9_evidence/red_batch22.txt` | 74971 | `244242ec749b626c60895ef564f4296137996bb4d79a8af8404fcbe65bcc7deb` |
| `docs/batch22_item9_evidence/red_batch22.xml` | 76263 | `15c1accd1417022823f99fdb6262cb3122f8c9bc4b53ec19fa3343b43712415a` |
| `docs/batch22_item9_evidence/red_batch22_run2.json` | 13048 | `f747530c40a6896aa4a6b95b2ebc4c3ed7d5a616fdb6cd7ea2326e88e6bdd37b` |
| `docs/batch22_item9_evidence/red_batch22_run2.txt` | 23003 | `9a9d50e498e4d742b97bab8e11d7f2c7aedaf9b3b1b5fd80489df8d604bdf373` |
| `docs/batch22_item9_evidence/red_batch22_run2.xml` | 22395 | `240032c3359bdd46cb5d8b2a18cea45195b44b3f4802a6f4937a134a20da8f40` |
| `docs/batch22_item9_evidence/red_batch22_run3.json` | 13018 | `a8952c73eaa6d65bb73a93030606a4cd3073cc960422fb1883820c0cc1e8dd15` |
| `docs/batch22_item9_evidence/red_batch22_run3.txt` | 22996 | `7e565c7525c2916697bec0fb70a7fa59e766bc6eafab40968ac39151c705d763` |
| `docs/batch22_item9_evidence/red_batch22_run3.xml` | 22347 | `9d936b3a587bbdbeb5535646a37df79950fae5a146170bfc32dd5082bb2e3ce7` |
| `docs/batch22_item9_evidence/red_batch22_run4.json` | 12970 | `c632cf615d6c801ab47972549223680ce0a1b5623cc094d14f524825bf41b1ab` |
| `docs/batch22_item9_evidence/red_batch22_run4.txt` | 22926 | `e032b09b1a2674cbdf777015eb0c218b96217e5c7bc342471512e467f86bf10c` |
| `docs/batch22_item9_evidence/red_batch22_run4.xml` | 22228 | `0684e2379002fff8629277a008a0b431b4f05ce288d7cd7c8d151097b2d248ff` |
| `docs/batch22_item9_evidence/red_batch22_run5.json` | 12958 | `49189f6c1f69e6a288600ff8b5fd71fce931cc8eef750f873079cbcc6a9aa3d5` |
| `docs/batch22_item9_evidence/red_batch22_run5.txt` | 22557 | `3d32cd180623c7a63d730ed31469b8a4c3f8fe050b7fba0dde576b4c703a0192` |
| `docs/batch22_item9_evidence/red_batch22_run5.xml` | 21985 | `43f41afae6ffa0e2e64cf54f3f63360493c9fbc9c1a17afb854d7777a2d20b51` |
| `docs/batch22_item9_evidence/red_batch22_run6.json` | 20793 | `d452d5fb3322a9757375681320d1f9bbae43b31c665b4c6ce628cc5a62884c11` |
| `docs/batch22_item9_evidence/red_batch22_run6.txt` | 40601 | `70388a8126c1da58a7b82c9b0cb9bddb6b154aef5332bdd3c28e17944ff75a70` |
| `docs/batch22_item9_evidence/red_batch22_run6.xml` | 38147 | `2cd246f18b3df257067713aa206a259150ebadb6b1abe65a72d2eb68fbbae274` |
| `docs/batch22_item9_evidence/red_batch22_run7.json` | 21156 | `609961e07503a3c91b115c0ec2c64217f66c477d72634df71765a6711610a7b8` |
| `docs/batch22_item9_evidence/red_batch22_run7.txt` | 41712 | `f9afb64d469037039842efc6009c4a618614be8a3bd193e99758381ef5a52239` |
| `docs/batch22_item9_evidence/red_batch22_run7.xml` | 38675 | `65996c983903f54e49ddaa2d436092b8413f1874de9f975c78831f65b9f71f64` |
| `docs/batch22_item9_evidence/red_batch22_run8.json` | 21335 | `2d701285583a33c0f91b34dfa91757d0095a96de5dc14e0252df23f9c3e11b90` |
| `docs/batch22_item9_evidence/red_batch22_run8.txt` | 41712 | `b1db5d2eea4b4271c12b718724b20b245a34501cbec8c96e5545cd986f4a24f2` |
| `docs/batch22_item9_evidence/red_batch22_run8.xml` | 38817 | `06bd14f034829b3ba1628fa1c4e00e1ea187169e38194ac1efa4b0dca9f7c9d6` |
| `docs/batch22_item9_evidence/render_item9_samples.py` | 2051 | `82db11f8243fb212b09e4468ad10579ffcbe593cceecaf20b95e775a09692c2a` |
| `docs/batch22_item9_evidence/run_item9_checks.py` | 6158 | `3e5132abe62f041f3b463c934c783c4facf403d511b6e08ec32f6ba73533fd45` |
| `docs/batch22_item9_evidence/serial_validation.json` | 2038 | `36a9c2e37542362105ab2f310dd452e84d1a69aa9584dc87c5f41e7c40d82150` |
| `v6lite/README.md` | 13718 | `4ad81212d1863b4fc11dc87d8bc8c32ea272e95563095243292ed615ced31fd9` |
| `v6lite/__init__.py` | 313 | `26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39` |
| `v6lite/__main__.py` | 26616 | `320fbc71bbeab5d4dc03956687e85f0b4fd354137d8ce8173c847b788d40c8f6` |
| `v6lite/alerts.py` | 53232 | `4b422fd4f2c1e2c3297de04d6193cfdd80adb4dadeee428516fdae171ff00657` |
| `v6lite/approval.py` | 12709 | `70a3c24325b95216bb307dd261b60ccff88f0f13da3e2f94d6344e92f07b41e1` |
| `v6lite/broker/__init__.py` | 96 | `2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24` |
| `v6lite/broker/base.py` | 3727 | `f6e52937d5e10cb0d1853ccfbce35cf47eceb74080ae6121a34577e3bad11e6b` |
| `v6lite/broker/fake.py` | 16149 | `04af71e38336c9ac2d6002c404e44d9d7584ce607b020e3cd52eb46010f395b7` |
| `v6lite/broker/ibkr.py` | 67762 | `5c8383e8e5062a3184bdc8ea08f5749110a76e750d7745a56094f1f407b3cd20` |
| `v6lite/clock.py` | 910 | `0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1` |
| `v6lite/config.py` | 18144 | `576506de1ea47b4d67112fdd935ab86494f1163e6f269609c8b380ec31c35904` |
| `v6lite/engine.py` | 182003 | `ce975d7482d9d7ef5d086187c5f74dc0a4d0e01703bf22c09f60e8e871150421` |
| `v6lite/examples/live.toml` | 2975 | `cac62332293b33c72282c3f41d9c9c860dfc610bc2ff457f57178ccf52c44afd` |
| `v6lite/examples/paper.toml` | 2867 | `314e3f3310c0e2c5c5a2cf4ab831484d7d6ab5525b998fc0de59e1637b750a2a` |
| `v6lite/examples/paper_delayed.toml` | 2966 | `3f941e4d966ca6b0513ecbcaafa9a0ded7a6985aa4f442c9f4ff8968aec51289` |
| `v6lite/examples/paper_g3n.toml` | 1270 | `f61ad27cde0526c57dc6f483f7d0c2c0bdd8ff23d7c0ceb061d01646913715d4` |
| `v6lite/examples/paper_test.toml` | 949 | `0796f207da9aafbda17f73a672d068ca84b4f768184b30c2e817c27a8c3e83b6` |
| `v6lite/execution_time.py` | 3269 | `6767aee2d823eccc2387106f803b38521bce94e71ec082fe6dd021918ee9f300` |
| `v6lite/exits.py` | 3927 | `1de7eb5637ee3c6abd1784570846d0edf8f91739ee97e2bb6e0b6651e34de093` |
| `v6lite/market_calendar.py` | 3925 | `1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2` |
| `v6lite/models.py` | 6798 | `fed0d60b9352597b65f55fd0f88de840ba5ffb7bc536c3995a44ce57c4857134` |
| `v6lite/protection.py` | 543 | `61fa2bdd943b2c60717706b3494fbfb95dca6f2789d2cb9c74fc82a71b2d197a` |
| `v6lite/protection_cancellations.py` | 11312 | `74724327c2ae5e67499cfbbea7bc471fa9a5d382d1ed520da577930844557c67` |
| `v6lite/protection_engine.py` | 103194 | `8f2b9d4830a4fff93ab11f47b67e78bb991a955ccb02ce1d60d9db17b4fcbdb1` |
| `v6lite/protection_incidents.py` | 13166 | `9f79065267bb3103aed37876d2cccd399e603fef62d58db9ced25f0f8ef29a75` |
| `v6lite/reconcile.py` | 2610 | `4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1` |
| `v6lite/report.py` | 24232 | `19548b2bb72b4cb35ad21017e7506d46b50aaf2dc7874ce6e5586cea0a1a37a2` |
| `v6lite/risk.py` | 11077 | `abc521f671e878a0ab5690e1fa178dc872d29b5396b86eaeb5f016be1594c687` |
| `v6lite/sizing.py` | 3294 | `c9b9cc4a116a7f61d9e660dd885d280ec2a9262671f2c3c8ab5c1515724e171e` |
| `v6lite/store.py` | 74233 | `8875e9f10d759ab2b074f257386e6ba9c6c937121aa0ac11097deb40c3b5cd7d` |
| `v6lite/strategy.py` | 8300 | `9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e` |
| `v6lite/tests/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `v6lite/tests/conftest.py` | 209 | `aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb` |
| `v6lite/tests/helpers.py` | 5415 | `0c28d019b52b61f5d0b477bd7b202c409ff6761a4b4830b70c77667a3d9787d6` |
| `v6lite/tests/test_batch10.py` | 19369 | `5b875c8df97861e22427223e7065a74bddf04a5da1c7115b431871369d177c47` |
| `v6lite/tests/test_batch11.py` | 10273 | `fb7f26a0bd1969400f2d769a0d65f4c7d3a18f0162332fd46ca9f1ed99c260f8` |
| `v6lite/tests/test_batch12.py` | 25124 | `3dfc69a4f811549cb32c9c186bbfed3d24f7c1c956c3c743d9b28f6996037cfc` |
| `v6lite/tests/test_batch12_v2.py` | 20802 | `5691a234501cd71b85e3098e0cf6da2a74f2d4f5ca3eda2797a8abe17fab9af0` |
| `v6lite/tests/test_batch13.py` | 18609 | `12a3ed033079f129e7e70f610be004c498ea6f6f0283473f60b2eb80ad819140` |
| `v6lite/tests/test_batch14.py` | 16354 | `5087c53709f4208d5dc4fbe9f74b100d50ebfb2060ccf9c9834358d2c3c7b257` |
| `v6lite/tests/test_batch15.py` | 71363 | `5f8d69e27230857b5b726e3960ba1a2cf09178cb925d2cb85ca98b209857e3d1` |
| `v6lite/tests/test_batch16.py` | 28722 | `f5cd54b73510b37facd6202cbd414baf47754166088512d698a8685fd10bebb5` |
| `v6lite/tests/test_batch16_card_deadline_copy.py` | 1932 | `d9c3c8f080748838a81392b1a12ca03347685a1be4692ae9159471ff2cc56a51` |
| `v6lite/tests/test_batch16_f5_copy.py` | 15179 | `eca4e85d176cdb5a726bc4e5bec2f2b6bd6869ff726a6b647c16548b4ab94bb0` |
| `v6lite/tests/test_batch16_f6_account_evidence.py` | 7194 | `6d97cec645009fd6d90c223cfeb5c902866701f75c01f91a604caddd3a0e863d` |
| `v6lite/tests/test_batch16_f6_global_mismatch.py` | 2874 | `86c542de449c2adab7cae4d2bc476e5108ef7ef14d9c2f45686a2404a3b9d478` |
| `v6lite/tests/test_batch16_f6_release.py` | 10603 | `15c23d799539945057095f56bad2e8ba8a32974383eb8d778fba0f57ede2cb96` |
| `v6lite/tests/test_batch16_f6_safety.py` | 5402 | `9e2464cc21430b8b4e27dd8982b1a5aee0b554a5e0698691df3156715ad65b9c` |
| `v6lite/tests/test_batch16_f6_steady_fairness.py` | 2893 | `e7ae2a84c08b2adadb1aa9994e835b5504db581401eecbf2bfd14678e984915d` |
| `v6lite/tests/test_batch16_f6_steady_timeout.py` | 2401 | `733347eda5814cbd2a14f71a717c88274bdd51376cada2c9bceeab3fd2908c48` |
| `v6lite/tests/test_batch16_f7_price_flip.py` | 4495 | `b2f9cf68a543999d08ab0567e6e6a5663f257d2525c48430f953de8bacfbc5dd` |
| `v6lite/tests/test_batch16_f8_backoff.py` | 2126 | `af5c1b6c62872c84536b222255c9a3b4229e2d7bf4ce1c4b55b1d4a780aa3539` |
| `v6lite/tests/test_batch16_gap_evidence.py` | 26359 | `328269e2c8fa8a0547d1d16a5904a4d6cc5462f13b093b9e6c09eb87bd4be8ba` |
| `v6lite/tests/test_batch17_alerts.py` | 10542 | `dfc6a85711add46c7a6e8b966c3f75f38f2bbbc7bee1b3f827882d3d9ddba5ff` |
| `v6lite/tests/test_batch17_equivalence.py` | 9719 | `ecb49e23e9cbcd6ea2803aacb4b649d8b8794b1c79e65f266141c655efdfca8a` |
| `v6lite/tests/test_batch17_g8.py` | 5126 | `4e21318f45dc6293fc4fbdc0f1c5be7c9d845782bf405fff8c6a51504bae5875` |
| `v6lite/tests/test_batch17_heartbeat_account.py` | 20900 | `5ab7bafaddb4ab15e16c275b45061079c46c3d642b866a91ac55f85a75c3f6de` |
| `v6lite/tests/test_batch17_l3_recovery.py` | 15834 | `736891fd02d99b2337fffca283fbddf53de7d8fce8294d6a089175cbd317cd3c` |
| `v6lite/tests/test_batch19.py` | 10690 | `cefff371b7c9101fff87583517d11a8f81675f0a549cb2007c19099b3d9e239c` |
| `v6lite/tests/test_batch19_funds.py` | 19657 | `f9c1ae564a76752edcb60b3293eec5a4b2ac15657b4e34258ba814cf4a78dde4` |
| `v6lite/tests/test_batch19_protection_equivalents.py` | 13413 | `12def834c0ef3a32bf71331473a47daae9e7d2b4309347e177784421cdeec303` |
| `v6lite/tests/test_batch19_review_edges.py` | 7207 | `262a24defa92162552f14d80dfc1eb45d673e7a538a7c0ead745b7649515f7d6` |
| `v6lite/tests/test_batch20_card_provenance.py` | 1717 | `f81f95b49f2eecb7079f7b34a15248f683338603c9c099811a58ee804435e558` |
| `v6lite/tests/test_batch20_execution_time.py` | 20596 | `658d55ca04fdcfbc9a6f5810871eeedf9439741752fbaa7a3b70836838431242` |
| `v6lite/tests/test_batch20_protection_equivalents.py` | 51774 | `0cbd0e83af3982c6ae80e7b0871462c6649e0e25921cb4c2e5217d0502ff39a4` |
| `v6lite/tests/test_batch20_protection_incident.py` | 36148 | `ba4a10ff92e6bc195357651e624e350bf926708ee64bbbac75f5b19ca427bb56` |
| `v6lite/tests/test_batch20_template.py` | 810 | `8c6ed584a5f435fe81542df99ce8c7a1d040f342583b733fe65356af352b5230` |
| `v6lite/tests/test_batch22_cancel_namespace.py` | 3080 | `b513c130a9045147fa3f77c3afb10d1140b17207995143866523fc9de83c532f` |
| `v6lite/tests/test_batch22_config_broker.py` | 23367 | `6ac2fefe519abb6b01e723d274a5a4bc02b7e4422b06831cbedc563295970c2a` |
| `v6lite/tests/test_batch22_item9_broker_cancel.py` | 39479 | `58186f1047bf79a39e85e96be7140a6cbb740ae152ce9cfd000365e5daee4341` |
| `v6lite/tests/test_batch22_pending_stop_audit.py` | 9880 | `f0bf9fe05cee399ca62892222ef09cd069bf8dc27a7179978e7434b7235e0696` |
| `v6lite/tests/test_batch22_protection_lifecycle.py` | 36441 | `f41e3201b350b2604d9ced7b93c83b8857af19284c80e5d55c81ed8f9dcdaf01` |
| `v6lite/tests/test_batch22_schema_audit.py` | 29777 | `374ae51b92eee5ea1e48ff4ca145422f1d106b0d33cf8f94b57d6261b24c7927` |
| `v6lite/tests/test_batch22_snapshot_cancel.py` | 10065 | `6531f1b9c3398e1cdbd0c7148e610b360846ff78a11dd3e6406e2cb92aa3286b` |
| `v6lite/tests/test_batch22_trigger_incidents.py` | 11026 | `a3c1ad8c925c53ce95577de7ee707093b98ff74468ed13cda66da7e30cc8db17` |
| `v6lite/tests/test_batch22_trigger_replacement.py` | 3263 | `80c284c3bd1b41ec47c841f344ca03726c8da9569433d0dfafec805bf861a90c` |
| `v6lite/tests/test_engine.py` | 17668 | `bb8967a536a8d187fc9f1d742be17c0b4b90e193ab52731ac06d6bff8a08f461` |
| `v6lite/tests/test_g2_day1.py` | 10712 | `cacaf5b3115d4a298fe8b3fe065d66fe70277b8ee4cd5d9643f0e2fff0ff5f9d` |
| `v6lite/tests/test_g2_day1_selfreview.py` | 8081 | `fe84ef645d99f421c64f670aa7f883d16b5a24c8c15b4dd30a4aa8f035b5ea06` |
| `v6lite/tests/test_g2_day2_prep.py` | 2547 | `a0db6b981992a3cf20d9f959a679c608842f4619007331bae9b26908cee3ccb7` |
| `v6lite/tests/test_g2_probe.py` | 7222 | `ba38215d7a6946d60884d0e3dac99b641e7bcde058c4893b94cb9d62b6a6fe18` |
| `v6lite/tests/test_ibkr_adapter.py` | 6081 | `6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a` |
| `v6lite/tests/test_review_fixes.py` | 22689 | `eb91ef957bfd13864c2c92a5da2cf3a29ffdc10c9f5c74fe751daa8f4caecdd3` |
| `v6lite/tests/test_review_round2.py` | 38526 | `7ab32142a9a0ee2fdb54853297cf6eae619ce28de4cac4dc646a8a8035660822` |
| `v6lite/tests/test_review_round3.py` | 15213 | `b5bf4e5c513e9d2310dc6404f147b633790cff0e894546e9176d6c26390a7427` |
| `v6lite/tests/test_review_round4.py` | 18605 | `04cf584aa3b7e13842c1e7608d03e5f888b7a714ea073001ce85281fe1eb43cb` |
| `v6lite/tests/test_review_round4_selfreview.py` | 13981 | `8cf2bba5d54e2e644bc016e68abfe87f0ecbe26b6e54d5a44a329c61e6dc7da8` |
| `v6lite/tests/test_review_round5.py` | 16770 | `c45a477f2bb20f021b3d8040b3be6360d78bb8c95f0ef7ffd2ba069fdf31eeb0` |
| `v6lite/tests/test_review_round5_selfreview.py` | 12064 | `03adaac71b3ea18095732064c51f6f4741400adae738c3cfb9f840c247ec4a8e` |
| `v6lite/tests/test_review_round6.py` | 10254 | `c6eabe6e953cc75ccfa7e4fa87625a86d677ef5dd2fca9975cd101b3403d6be5` |
| `v6lite/tests/test_review_round7.py` | 9101 | `bcb2baea638b6ba6c063aba7776aaf1137c3cd1df98297a08311cdaf899994a5` |
| `v6lite/tests/test_review_round7_selfreview.py` | 6455 | `a3589fae5996245f7246f1860875a847b6761ed9f8bab5a317ab5c7d9e5e86bf` |
| `v6lite/tests/test_review_round8.py` | 7212 | `c65ac1c396468aae3124ffcdc0f61b2ed483547ec1b0f47119f00950008b3736` |
| `v6lite/tests/test_review_round8_selfreview.py` | 7315 | `e927860575cb55769a910a0dfbced96b725922da6629010c894ec2d3c86b36c3` |
| `v6lite/tests/test_soak.py` | 6157 | `d84df42bcdc3ce0e505a8f930b2752ec2721cc307fb364388be210131c4fad47` |
| `v6lite/tests/test_strategy_parity.py` | 4014 | `732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f` |
| `v6lite/tests/test_units.py` | 8846 | `75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42` |
| `v6lite/tools/__init__.py` | 34 | `91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1` |
| `v6lite/tools/g2_probe.py` | 20555 | `e756a719f9f8c1cded3d1123f67fa0097b504680589319be40619bc5d7f6763e` |
| `v6lite/version.py` | 863 | `9aa52b23a3c8c08801f9d86c46ae1090a28c78dbde5d5b4b2923347bc74ece45` |
