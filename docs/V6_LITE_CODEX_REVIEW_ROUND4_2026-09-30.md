# V6-Lite 第四轮 Windows 独立复核

日期：2026-09-30 PT。复核者：Codex。对象：`V6_LITE_FIX_REPORT_ROUND4_2026-09-30.md`、清单内 39 个文件及本批 G2 runbook。

**39/39 哈希匹配；Windows 全量 208 passed，第四轮两组专项分别 21 passed、12 passed。新增独立反例确认 2 个 P1、6 个 P2，G3 仍为 NO-GO。Live 还必须完成并验收 L7。** 删除 resync 和旧库迁移的方向可以接受，但“只剩 freeze → 人工平仓 → release”尚未完整兑现。

报告第 3 节的 10 行：7 行 `CLOSED_WITH_EVIDENCE`、3 行 `OPEN_PRODUCT_DEFECT`。第 6 节自审表的 11 行：7 行 `CLOSED_WITH_EVIDENCE`、4 行 `OPEN_PRODUCT_DEFECT`。这是按下文明确的修复范围裁定；局部关闭不代表整条交易路径放行。报告另列的固定请求键风险已在本地复现，不再仅列为 G2/G3 待观察项。

本轮是只读代码复核，结果作为 docs-only 产物保存。按仓库开发流程分工审查，主线程统一裁定。仅新增本文件；没有修改交付源码、仓库测试、原报告、既有 Codex 复核、开发计划或旧 V6，没有 stage/commit/push。所有新增探针使用 FakeBroker、合成时钟、临时 SQLite，或未连接的本机 ib_insync 对象；没有连接 Gateway、操作真实 state、读取 `.env`、下单或签发批准。以下建议均未实施。

## 1 交付身份与 Windows 测试

| 项目 | 独立结果 |
|---|---|
| manifest | 39 项全部匹配；缺失、哈希差异、排除缓存后的清单外 Lite 文件均为 0 |
| G2 runbook | 与报告单列 SHA-256 一致 |
| 环境 | `Windows-10-10.0.26300-SP0`，仓库 venv，Python 3.11.9 |
| Lite 全量 | **208 passed in 36.68s**；无失败、无跳过 |
| `test_review_round4.py` | **21 passed in 1.57s** |
| `test_review_round4_selfreview.py` | **12 passed in 0.83s** |
| 执行顺序 | 全量、21 项、12 项依次运行；完成后才运行各组独立探针 |
| 缓存 | 全量使用新的临时 `PYTHONPYCACHEPREFIX`；两组专项按报告命令运行；独立探针使用隔离临时缓存 |
| 范围 | Lite 全量包含 Windows 账户锁等已有回归；没有重跑冻结的旧 V6 全量，也没有执行真实 G2 |

实际测试命令（每个 PowerShell 进程均先激活 venv）：

```powershell
cd C:\Dev\TradingV6
. .\venv_v6\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
$env:PYTHONPYCACHEPREFIX = Join-Path ([System.IO.Path]::GetTempPath()) ('v6lite-round4-tests-' + [guid]::NewGuid().ToString('N'))
python -m pytest v6lite/tests -q -ra
# 以下两组在后续独立 PowerShell 进程中按报告命令执行
python -m pytest v6lite/tests/test_review_round4.py -q -ra
python -m pytest v6lite/tests/test_review_round4_selfreview.py -q -ra
```

21 和 12 项包含于 208 项，不能相加成独立覆盖数量。报告所述第三批 `16 failed`、第四批连续三次通过及自审第一版 `8 failed` 属于交付方历史证据，本轮没有重新构造那些版本。删除 15 个测试属于功能范围收缩，不能据此宣称原来的全部恢复语义仍受支持。本次正式测试没有失败重试；一个新增会计探针首次在 Windows 临时 SQLite 清理时因探针未关闭连接失败，修正探针清理后完成，产品反例未变。

关键文档 SHA-256，复核前后核对一致：

| 文件 | SHA-256 |
|---|---|
| 第四批整改报告 | `15a93970cad32fb2a77461e6268395727c12766aa0befafb55972bfc844ea852` |
| 第三轮 Codex 复核 | `7bbb4e2cd97d766730b2a1cc0f6878b05c3d24870f41f36086b3ca3251b54ed1` |
| 开发计划 `V6_LITE_REDESIGN.md` | `203bad0cf719cbe16ef6485a38c4bc729945e20a230cbea687b84c01df849e02` |
| 本批 G2 runbook | `dc833d66f4b2d575c8f517a808895c0e9c1c7ac225676e0ce83c185fd8d7b41b` |

## 2 第 4 节 freeze / release 前提的裁定

接受缩减自动恢复范围：数量无法解释时停止该标的自动交易、阻断新增风险，交由操作员处理；证据完整后关闭原账本，并把不可证明的当日绩效保守处理。它确实避开了上一轮 resync 重置阶段进度、迁移旧恢复语义等问题。

但三个条件仍缺一不可：**本次零持仓和无订单证据必须完整可信；release 后任何旧成交都不能重新激活已释放持仓；原持仓已由迟到成交完整关闭时，也必须有合法终结冻结的路径。** 当前分别被 R4-F1、R4-F3、R4-F4 反证。“没有 working 单”仍不能证明旧成交已经全部被程序接收；60 秒也不能自行证明这一点。人工平仓可以成为业务终点，但程序必须正确处理这个终点之后的数据。

冻结期间的全局入场控制直接读取 `frozen:*`，不是只依赖可清除异常；已有冻结标的的自动新单入口也有检查。本轮未复现“已经持久冻结后自动新发该标的订单”。R4-F2 的 BUY 发生在冻结前的数量差异窗口。冻结不等于现存订单已经消失，R4-F5 的人工交接仍需补齐。

## 3 独立发现：发现 → 建议改动 → 测试

以下编号 `R4-F*` 为本次新增发现；整改报告第 3 节沿用的 `F1–F9` 在第 4 节单独映射。严重度依据实际影响，不把 P0 告警等级直接当成代码缺陷等级。全部是本地可复现行为，不代表真实 Gateway 已经发生同样时序。

### R4-F1 / P1：超时后的旧 End 消息能让新查询返回假空列表，并错误 release

**发现：** [ibkr.py:182](C:/Dev/TradingV6/v6lite/broker/ibkr.py:182) 与 [ibkr.py:201](C:/Dev/TradingV6/v6lite/broker/ibkr.py:201) 直接接受 `reqPositions()` / `reqAllOpenOrders()` 的列表，未隔离超时请求。已安装的 [ib_insync/ib.py:1903](C:/Dev/TradingV6/venv_v6/Lib/site-packages/ib_insync/ib.py:1903) 和 [ib.py:1922](C:/Dev/TradingV6/venv_v6/Lib/site-packages/ib_insync/ib.py:1922) 使用固定键；[wrapper.py:134](C:/Dev/TradingV6/venv_v6/Lib/site-packages/ib_insync/wrapper.py:134) 的新请求覆盖相同键，`positionEnd` / `openOrderEnd` 按该键结束当前 Future。旧请求超时不消除仍可能到达的结束消息。

[engine.py:974](C:/Dev/TradingV6/v6lite/engine.py:974) 把空列表转换成空 book，随后缺失标的按零股处理。因此“函数返回、未抛异常”不足以证明 release 使用的是完整的新快照。

**建议改动：** 超时后将该请求通道的完整性标为未知，禁止其列表供 SELL / release 取证，直到经过可证明的同步恢复。可评估带独立请求 ID 的持仓接口；无法关联代次的订单通道需要明确的失效、排空或重连恢复协议。不能只清 Future、连续重试，或把不完整空列表当零；多查一遍本身也不是证明。保留“本次查询异常不发 SELL”的现有保护。

**测试：** 使用真实安装的 ib_insync `IB` / `Wrapper` / Future 逻辑，替换底层发请求函数为本地回调调度，完全不建网络连接。第一次请求超时；第二次请求启动后先交付旧 End，再交付第二次的内容。为了快速确定事件顺序，测试 timeout 设为 0.01 秒；问题在请求关联方式，不依赖生产 timeout 恰为 15 秒。

```text
positions: first_timeout=true
next_fresh=[]
actual_adapter_cache=[AAPL 20]
release_result={status: RELEASED, qty_open: 0}, releases=1

open orders: first_timeout=true
next_fresh_count=0
cached_working_sell_qty=[20]
```

持仓探针实际走过 `_apply_releases` 并提交 Store 事务，20 股未消失却已 release。订单对照证明仍有 working SELL 20 时适配器可返回空列表；该分支未进一步宣称已真实超卖。**推翻原报告“本地无法复现”的表述；客户端失败模式已证明，真实服务端出现频率仍待 G2。**

### R4-F2 / P1：对账覆盖刚发现的数量疑点，同一 tick 放行 BUY

**发现：** [engine.py:846](C:/Dev/TradingV6/v6lite/engine.py:846) 先算 `result`，随后 851 行的 `_protect("query")` 可以发现更新的数量差异并置 `quantity_suspect`。但 857–860 行仍发布旧 `result.ok` 并无条件清空疑点。此 tick 开始时若退出检查尚未到期，查询期间执行过保护检查后，后续 regular pass 可以不再重复检查，于是扫描使用错误健康状态。

**建议改动：** 只有覆盖了该疑点、且不早于其证据时刻的成功对账才能清除它。发布健康结果前对证据和状态变化进行校验；流程中出现较新差异时立即保留全局入场阻断并安排新对账，不能以较旧快照清除。

**测试：** AAPL 账本 20；tick 开始距离上次退出检查 20 秒，尚未达到 30 秒。fresh 查询取到 20，等待 15 秒期间外来成交使持仓流更新到 18。保护检查真实观察到 18 并置疑点，然后对账发布旧健康。正常扫描实际送出 SPY BUY 20：

```text
protect_observed: broker AAPL=18, quantity_suspect=[AAPL]
after_reconcile: broker=18, ledger=20, reconcile_ok=true, quantity_suspect=[]
entries: AAPL 20 (原有), SPY 20 (本 tick 新发)
```

这不是只调用风控函数的推测，而是完整 `tick()` 中发生的新增订单。报告 F3 / SR4 R5 的原反例已修好，但更晚发生的差异仍可被覆盖。

### R4-F3 / P2：release 后迟到 BUY 仍自动重建不存在的持仓

**发现：** [store.py:579](C:/Dev/TradingV6/v6lite/store.py:579) 把所有非 OPEN 的 ENTRY 都送入旧恢复逻辑，未区分 RELEASED；[store.py:590](C:/Dev/TradingV6/v6lite/store.py:590) 会创建 `source=late_fill` 持仓或并入其他 OPEN 仓。只有 SELL 在 [store.py:612](C:/Dev/TradingV6/v6lite/store.py:612) 有非 OPEN 隔离处理。

**建议改动：** 对 RELEASED 的 ENTRY 明确定义终结语义：保留成交证据与异常，触发 fresh 风险复核，但不得自动新建或并入当前仓位。区分普通 CLOSED 后的新增暴露处理，与操作员已经全额平仓并 release 的旧业务终点。

**测试：** ENTRY 已记 10；旧 BUY 5 实际早已成交但回报暂不可见；操作员将全部 15 股平掉。fresh 为零、无 working，release 成功。随后旧 BUY 5 回报出现：

```text
before: [RELEASED 0 strategy], broker=[]
after:  [RELEASED 0 strategy, OPEN 5 late_fill], broker=[]
anomaly=late_entry_fill, new_orders=[]
```

这里不假设已真正取消的余量以后又撮合。实际交易发生在人工平仓之前。未观察到新 SELL，fresh 数量检查保住了发送边界；但它制造假仓并要求再次处置，推翻 D5″ 的“不重建”完整声明。旧 SELL 对照确实保持 RELEASED、产生 `exit_after_close`，0 个新订单。

### R4-F4 / P2：冻结后旧成交已正常平仓，release 却永远无法清冻

**发现：** [engine.py:979](C:/Dev/TradingV6/v6lite/engine.py:979) 与 [store.py:688](C:/Dev/TradingV6/v6lite/store.py:688) 均要求存在 OPEN 仓。冻结期间迟到 SELL 将原仓正常关为 CLOSED 后，这个条件永远不满足；清冻仅在 release 事务中，`clear-anomaly frozen:*` 又被禁止。结果是合法完成平仓仍永久阻断新开仓。

**建议改动：** 为“冻结目标已被完整成交证据关闭”的状态提供受同等 broker 零股、无 working、静默条件约束的幂等终结路径。保留已正确结算的 CLOSED 交易；明确原交易亏损与 release 保守亏损的关系，避免重复计算一次交易。

**测试：** 沿用 hidden STOP 场景，部分迟到成交造成冻结，随后恢复全部成交。等待 61 秒申请 release，输出：

```text
position_status=CLOSED, broker_qty=0, working_orders=0
release_count=0, frozen_remains=true
refusal="AAPL has no managed open position"
```

这不削弱 SR4 R2 的防绕过修复；问题是受控解除路径缺失。

### R4-F5 / P2：冻结交接没有列出仍 working 的 held / 孤立订单

**发现：** [engine.py:933](C:/Dev/TradingV6/v6lite/engine.py:933) 仅选择本地 active 且非 hold 意图。fresh 列表中的 held 或孤立 `v6l-` SELL 不在撤单集合内；938–944 行也仅在该集合非空时提示先确认撤单。否则直接提示在 TWS 保护或平仓，未列出已知仍可能成交的订单。

**建议改动：** 可以保留 D3″ 对不确定订单不自动接管、不自动撤销的原则，但冻结 P0 必须列明全部已知相关 working 订单，分别说明已请求撤销和需人工处理的订单，并明确先处理冲突订单、确认后再平仓。README 对“自己的 working 全部撤下”的概括也应与实际范围一致。

**测试：** fresh 分别存在 held TP1 SELL 6、孤立 V6L SELL 20；broker 17、账本 20 持续差异导致冻结。两种情形均 `frozen=true`、新 `cancels=[]`；P0 不包含相应 working ID，也不包含平仓前确认这些订单消失的要求。未模拟操作员执行冲突交易，不宣称系统已自动超卖。普通本地非 hold 撤单的交付测试已通过。

### R4-F6 / P2：非空 BAG 中未知 conId 被直接忽略，未生成通配阻断

**发现：** [ibkr.py:240](C:/Dev/TradingV6/v6lite/broker/ibkr.py:240) 查不到股票 conId 时直接 `continue`，没有置 `unresolved=True`。因此所有腿都未知的非空 BAG 返回空列表；混合已知和未知腿也不会按声明生成 `*`。现有未知腿测试仅用了 `comboLegs=None`，没有覆盖非空但无法解析的腿。

**建议改动：** 区分“已证实不含相关股票的腿”和“未知类型/未知合约的腿”。在尚不能证明排除时，按 D13′ 返回通配阻断；若希望放行非股票组合，须有明确解析结果，不能把本地 `_contracts` 不认识视为非股票证据。

**测试：** `_contracts` 仅含已知 AAPL，BAG 带一个未映射 conId 的 SELL 腿。实际 `adapter_orders=[]`，AAPL 可卖容量仍为 20；人为平仓至零后，即使该 BAG 仍 working，release 也成功。证明承诺的“任一未知腿阻断全部 SELL 和 release”没有实现；未据此断言该未知 conId 必然代表 AAPL，故定为 P2。

### R4-F7 / P2：冻结标的有 held 意图时，仍每 tick 查询持仓与订单

**发现：** `_maybe_freeze` 已排除冻结标的计时，但 [engine.py:856](C:/Dev/TradingV6/v6lite/engine.py:856) 把 held 意图也计为 `in_flight`，871、877 行继续让差异被视为需要立即重查。长期 held 状态绕过了本轮针对冻结的降频修复。

**建议改动：** 区分仍在本系统自动执行流程中的在途意图，与冻结交接后持久等待人工处理的 held 状态；后者采用有界的健康/证据刷新节奏。不能通过跳过未知 working 股数预留来减轻查询。

**测试：** 正常前日 ENTRY 20 部分成交 10，次日变成 `prior_day_unverifiable` held；broker 15、账本 10 导致冻结。其后 60 个一秒 tick：

```text
frozen=true, hold=prior_day_unverifiable, mismatch_since={}
positions_fresh_calls=60, open_orders_calls=60, reconcile_due=true
```

探针保留正常退出判断，只把 FakeBroker 的 cached `positions()` 调整为无额外请求的读取，以免把 Fake 的缓存模拟计入 fresh 次数。报告 R7 的无 held 测试通过，不覆盖此路径。

### R4-F8 / P2：取证失败分支沿旧 TP 目的报警，漏掉查询期间新触发的 STOP

**发现：** [engine.py:1229](C:/Dev/TradingV6/v6lite/engine.py:1229) 按队列原 `stage` 决定告警等级。若取证发现未知 BAG，1232–1235 行立即返回；最新价格判断位于 1253 行，未执行。因此查询期间已经跌破 STOP，仍仅报 TP1/P1。

**建议改动：** 在取证不足而拒绝 SELL 时，也依据最新有效报价判定是否已处于需要 P0 的止损风险；发送仍失败关闭。不要为升级告警绕开容量或证据检查。

**测试：** AAPL=120 时 TP1 排队；订单查询一秒内价格跌至 107（stop=108.47），同时返回 `*` 组合单。实际只有 `P1 exit blocked AAPL TP1`，SELL=0，STOP 尚未创建，TP1 进入 30 秒退避、队列为空。该轮缺少应有的 P0；没有证明以后各轮永久漏报，且 SELL 仍正确拒绝，故定为 P2。

## 4 对整改报告第 3 节逐行裁定

以下 `CLOSED_WITH_EVIDENCE` 关闭具体原缺陷及所列本地修复，不把客户端协议完整性或真实运行耗时一并验收。

| 报告行 | 裁定 | 发现 → 改动 → 测试 |
|---|---|---|
| F1 fresh 查询异常仍用缓存 SELL | **CLOSED_WITH_EVIDENCE** | 异常回退已删除，本次订单/持仓查询失败即拒绝；已排队 STOP 的证据失败按 P0 处理。全量与 F1 专项通过。成功返回但列表不完整属于 R4-F1；查询中 TP 变 STOP 的告警属于 R4-F8，不由此行关闭。 |
| F2 resync 的不可信恢复与替代冻结流程 | **OPEN_PRODUCT_DEFECT** | 删除 resync、fresh 数量检查、正常冻结及非 hold 撤单已实现并通过；release 证据污染、迟到 BUY 重建、CLOSED 无法解除及交接不全仍在，见 R4-F1/F3/F4/F5。 |
| F3 最新差异必须阻断同 tick 入场 | **OPEN_PRODUCT_DEFECT** | release 拒绝后的最新 positions 已返回外层，原专项通过；但 `_protect` 期间更新的差异仍可被旧 result 清除，完整 tick 已发 SPY BUY，见 R4-F2。 |
| F4 resync 丢失 TP 部分进度 | **CLOSED_WITH_EVIDENCE** | resync 命令及重建功能删除，原 TP 进度继承问题按范围退出；删除命令专项通过。它不等于所有 late ENTRY 恢复分支均已删除，D5″ 另见 R4-F3。 |
| F5 原子 release 与日停 | **CLOSED_WITH_EVIDENCE** | 单事务涵盖 RELEASED、意图、release 记录、清冻、风险日与日停；注入异常整体回滚测试通过。补充重开数据库日停=1、release=1；一次既有亏损加 release 触发冷却，重复 release 拒绝。证据真假与事务原子性分别评估。 |
| F6 删除旧库迁移、先只读拒绝 | **CLOSED_WITH_EVIDENCE** | 版本检查先于建表/日志模式写入，旧语义不迁移；v3 形态哈希测试通过，额外版本 1/2/3/5 均拒绝且字节不变。 |
| F7 发送前重判最新价格与 STOP 阶段状态 | **CLOSED_WITH_EVIDENCE** | 成功取证后的 `_redecide` 能放弃 TP 或转 STOP，并检查 FAILED/DONE；F7 与 R4 专项通过。失败取证分支没有发送，告警最新风险另由 R4-F8 / S3 保持开放。 |
| F8-A 周期与单次阻塞预算 | **CLOSED_WITH_EVIDENCE** | 启动首步检查 `interval >= max(history,vix,query)+3`；12/12/17 对应最小 20，默认 30；不满足时未连接即拒绝，专项通过。真实调用耗时和调度开销仍为 G2 待验。 |
| F8-B 执行时退避与队列副本 | **CLOSED_WITH_EVIDENCE** | 执行入口检查退避，拒绝删除副本；仅 sent/unknown/rejected 增加尝试，专项通过。R4-F7 是对账查询频率问题，不混同为此队列退避回归。 |
| F9 BAG 股票腿及任一未知腿阻断 | **OPEN_PRODUCT_DEFECT** | 已知股票腿数量/方向、空腿列表通配测试通过；非空未知 conId 静默丢弃，见 R4-F6。 |

合计：**7 CLOSED_WITH_EVIDENCE，3 OPEN_PRODUCT_DEFECT**。

## 5 对整改报告第 6 节逐项裁定

| 自审项 | 裁定 | 发现 → 改动 → 测试 |
|---|---|---|
| R5 同 tick 数量疑点阻断 | **OPEN_PRODUCT_DEFECT** | 原 R5/R5b 测试通过，但对账末尾仍覆盖较新疑点并发 BUY；须修 R4-F2。 |
| R3 不完整证据延后冻结、fresh 已一致不误冻 | **CLOSED_WITH_EVIDENCE** | 刷新成交失败也采用成功返回的新持仓重新比较；60+120 秒分支已实现，两条专项通过。本地请求被旧 End 污染的真实性另见 R4-F1。 |
| R2 冻结独立阻断、不可 clear-anomaly | **CLOSED_WITH_EVIDENCE** | 风控直接读取 frozen 控制，手动清异常也不能恢复入场；专项通过。解除状态缺失另见 R4-F4。 |
| R4 不再发送 FAILED/DONE STOP | **CLOSED_WITH_EVIDENCE** | `_redecide` 重读阶段并要求 PENDING，专项通过。 |
| R7 冻结后不每 tick 强制查询 | **OPEN_PRODUCT_DEFECT** | 无 held 情况已降频；held 状态仍 60 tick/60 次持仓/60 次订单查询，见 R4-F7。 |
| R1 拒绝旧库不先改文件、所有 Store 入口受保护 | **CLOSED_WITH_EVIDENCE** | 只读版本检查在任何写库前；正式形态测试及 1/2/3/5 字节不变补充通过。 |
| R6 日报列出 release/refused/冻结 | **CLOSED_WITH_EVIDENCE** | 事件过滤已更新；专项及重开数据库后日报检查通过，保留人工 TWS 盈亏不计入的说明。 |
| R8 README 删除 resync/自动迁移备份描述 | **CLOSED_WITH_EVIDENCE** | 已反映删除及 schema 4 拒绝；静态核对。冻结订单范围/操作顺序仍需随 R4-F5 改正，不由文案清理验收交易流程。 |
| S1 冻结撤 working SELL，避免人工平仓叠加 | **OPEN_PRODUCT_DEFECT** | 本地非 hold 撤单通过；held/孤立订单仍 working 且冻结 P0 不列出、不交代先处理，见 R4-F5。 |
| S2 ignore 不隐藏空头 | **CLOSED_WITH_EVIDENCE** | AAPL −3 且 ignore 仍 `ok=false`；protected MSTR 例外保持原约定，专项与补充通过。 |
| S3 STOP 被证据/组合阻断一律 P0 | **OPEN_PRODUCT_DEFECT** | 原先已是 STOP 的分支通过；查询期间由 TP 跌破 STOP 时仍仅 P1，见 R4-F8。 |

合计：**7 CLOSED_WITH_EVIDENCE，4 OPEN_PRODUCT_DEFECT**。

对该节另列的四项未修改风险，分别裁定：

| 风险 | 裁定与下一步 |
|---|---|
| 固定请求键、迟到 End | **OPEN_PRODUCT_DEFECT / P1**。本地真实 ib_insync 回放已复现假空快照和错误 release，须进入修复清单，不能仅等 G2 观察。 |
| 订单取证与持仓取证之间新增外来单 | **IMPLEMENTED_VALIDATION_PENDING**（仅表示已有取证流程，未证明原子性）。两次请求不是原子快照；G3 操作规程需要明确谁可同时交易、何时重新取证。不得把 `fresh` 名称视为并发保证。 |
| reconnect 20 秒不在预算 | **IMPLEMENTED_VALIDATION_PENDING**。启动预算本地实现可以关闭，但只覆盖所声明请求；G2 须测断连/恢复、调度、告警与恢复前证据失效，不能推导全天无条件 30 秒止损保证。 |
| release 请求取证前删除、崩溃丢请求 | **已知人工重试行为，结果完整性待验证**。若接受有审计请求而无结果时由操作员重试，应在操作说明明确；本轮未作新崩溃注入，不称 CLOSED，也不把请求丢失误说成半事务 release。 |

## 6 第 7 节十项设计判断

| 判断 | 表态 | 成立范围与未完成项 |
|---|---|---|
| D3″ 外来/孤立/held 不接管、不自动撤，股数预留 | **接受原则，当前实现未完整通过** | 交易期间保守预留合理；未知 BAG 的遗漏、超时请求完整性及冻结交接需修 R4-F1/F5/F6。不要求为闭项擅自撤未知归属订单。 |
| D5″ 只保留 freeze → 人工平仓 → release | **接受范围收缩，当前未兑现** | resync/迁移已删除；RELEASED 迟到 BUY 仍重建，CLOSED 冻结没有出口，见 R4-F3/F4。 |
| D8″ 周期不短于最长单次阻塞+3 | **接受本地预算条件，真实时效待 G2** | 启动检查与各调用前 `_protect` 配合，不再依靠一个不足以容纳请求的配置。它是必要约束，不是含网络/重连/处理开销的硬实时证明。 |
| D11′ SELL 使用本次 fresh 订单和持仓、异常不回退 | **接受原则，当前证据完整性不通过** | 删除缓存回退正确；还必须证明本次完成消息和列表属于本次完整请求，R4-F1 未解决。 |
| D12′ 执行时退避、拒绝删除副本 | **接受，本地通过** | 已按正确位置检查，尝试计数也区分真实发送结果；正式回归通过。 |
| D13′ OPT 等排除、BAG 股票腿预留、未知腿全阻断 | **接受分类原则，当前不通过** | 非股票已知腿可排除；未知 conId 不能直接视为非股票。R4-F6 是明确实现缺口。 |
| D14′ release、日停同事务，计一次亏损 | **接受，本地通过** | 事务回滚、重启与连亏推导有证据。补充 CLOSED 冻结终结时必须定义不重复计亏损，见 R4-F4。 |
| D15 60 秒完整/再宽限120秒冻结，撤自己的单，只能release解冻 | **接受保守冻结方向，流程未完整通过** | 阈值作为工程假设待 G2 校准；不确定订单的人工交接、已 CLOSED 的清冻出口、held 查询频率分别见 R4-F5/F4/F7。不能用直接删 frozen 控制代替正式解除。 |
| D16 ignore 空头仍阻断 | **接受，本地通过** | 与多头专用系统的风险定义一致；protected 名单仍按已明确的观察范围例外处理。 |
| D17 schema4、旧版本写入前拒绝 | **接受，本地通过** | 新部署创建 v4、正常重启继续读取自己的 v4；不迁移旧 schema。版本拒绝与文件字节不变已有证据；不是要求每次启动丢弃 v4 历史。 |

冻结的 60/180 秒应表述为评估门槛，不能称为严格墙钟完成上限。[engine.py:902](C:/Dev/TradingV6/v6lite/engine.py:902) 先进行成交和持仓取证，之后才读取实时时钟并比较宽限；两次 query、前置对账和保护队列都会增加等待。G2 应分别记录检查间隔、冻结取证到提交的耗时、真正 STOP 发出延迟，不能只测单请求后推导整条动作时限。

## 7 修复顺序与放行边界

1. **先修 R4-F1、R4-F2 两个 P1。** 分别解决不完整快照被当有效证据，以及新差异被旧健康结果覆盖。固化固定键 timeout/late-End 和完整 tick 新 BUY 两个反例。
2. **同批完成六个 P2。** R4-F3/F4 使 release 成为完整、幂等的业务终点；R4-F5 补人工交接；R4-F6 补未知腿；R4-F7 控制冻结后的查询频率；R4-F8 在拒单时仍评估最新 STOP 风险。沿用交付报告记载的“P2 也修”，不将其静默豁免为已接受风险。
3. **修复后再进行相应 G3 验收。** 真实 G2 可由操作员按 runbook 启动只读采证；本复核没有启动，也没有签发任何交易放行。G2 需关注完整快照/End 关联、跨请求外来单、BAG 字段、账户就绪和资金时效、12/12/17 秒实际分布、1101/重连。读到一次成功不能替代失败时序测试。
4. **L7 仍是 Live 必须完成的独立门槛。** 冻结通知、进程内 STOP 和人工接管都不能算作已实现券商端保护单。

官方接口资料只作为边界依据：`reqAllOpenOrders` 是当前订单的一次返回，不建立后续订阅，见 [IBKR 当前订单说明](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/requesting-currently-active-orders/all-submitted-orders)。IBKR 的 [Positions 接口说明](https://interactivebrokers.github.io/tws-api/positions.html)描述初始持仓返回和 End、随后持仓变化推送；该旧版官方页面不作为当前 Gateway 时序保证。**从这些接口语义与本地实现可以推断：分次 fresh 调用不自动构成同一时点原子快照。** R4-F1 的客户端行为本身由本机库执行证明，而非从网页猜测。

## 8 复现材料与完整性收尾

独立探针保存在临时目录，不加入交付的 39 文件或仓库测试。以下哈希用于识别本次审查脚本；临时目录可能被系统清理，关键事件序列和输出已写入上文。

| 探针 | 位置 | SHA-256 |
|---|---|---|
| 固定键持仓/订单、未知 BAG | [boundary probe](C:/Users/yu-ch/AppData/Local/Temp/v6lite_round4_boundary_probe.py) | `0d05fe52a39c0a5d0c8d09443b6a9a023e58daaa510f07179370d24ecb6116df` |
| release 迟到 BUY/SELL、held/孤立交接 | [release probe](C:/Users/yu-ch/AppData/Local/Temp/v6lite_round4_release_probe.py) | `7a5fa2efa15a8b8a7e0600c76cb27bd8bb15029ed4af8921fb2ff993909e1b9c` |
| CLOSED 清冻、日停连亏重启、版本拒绝、空头 | [accounting probe](C:/Users/yu-ch/AppData/Local/Temp/v6lite-r4-accounting-bdcbffde79e843f18b3c3e77091ff21f/round4_accounting_probe.py) | `650a00df8f752d7a0d6ef84e2914b30d733a409de37aa24913be7e0e755ffa5d` |
| 较新疑点被覆盖、held 冻结查询频率 | [scheduling probe](C:/Users/yu-ch/AppData/Local/Temp/v6lite-r4-boundaries-03e11faf1f7446e492c6efb62ac9e9a2/probe_r4_boundaries.py) | `3478a9860445f7643728a4b76dad7288808564db5cbb491f716a9e3727ef9ec8` |
| 拒单告警未重判最新 STOP | [alert probe](C:/Users/yu-ch/AppData/Local/Temp/v6lite-r4-boundaries-03e11faf1f7446e492c6efb62ac9e9a2/probe_s3_latest_quote.py) | `3e54655a5cd254729b17b3f6215c7e97f2f300ca67988a4cb77761c8d70c0841` |

这些探针针对当前有缺陷的行为作断言，运行完成不是“安全测试已通过”。将其转成产品回归时，应断言修复后的拒绝/保留疑点/终结语义，而不是照抄当前错误结果。调度脚本使用固定的临时子目录，复跑时应换全新的目录，避免重复使用探针数据库。

收尾再次核对 39 项 manifest、G2 runbook 和复核前的 9 份 `V6_LITE*.md` 基线；它们保持原字节。仓库原有大量未提交改动保留；本轮唯一新增仓库产物是本独立复核文件。
