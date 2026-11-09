# V6-Lite 第五轮 Windows 独立复核

复核者：Codex。对象：[第五批整改报告](C:/Dev/TradingV6/docs/V6_LITE_FIX_REPORT_ROUND5_2026-10-01.md)、41 个清单文件及本批 G2 runbook。文件日期沿用交付批次 2026-10-01；实际执行时间为 2026-09-30 PT / 2026-10-01 UTC。

**41/41 哈希匹配；Windows 全量 228 passed，两组专项分别 13 passed、7 passed。确认 1 个 P1、4 个实现层面的 P2，另有 1 个 P2 文档同步问题。G3 仍为 NO-GO；Live 仍须完成并验收 L7。**

报告第 3 节八行：6 行 `CLOSED_WITH_EVIDENCE`，2 行 `OPEN_PRODUCT_DEFECT`。第 5 节五项：4 项 `CLOSED_WITH_EVIDENCE`，1 项 `OPEN_PRODUCT_DEFECT`。关闭具体原缺陷，不等于全部设计保证或真实 Gateway 行为已经验收。特别是 D11″ 的“自己的 End”仍有明确协议假设，不能表述为无条件证明。

任务按只读代码复核执行，结果作为 docs-only 产物保存。按仓库开发流程分工审查，主线程统一裁定；只新增本复核记录。未修改源码、仓库测试、原整改报告、既有复核、开发计划或旧 V6；未连接 Gateway、操作真实 state、读取 `.env`、下单、签发批准、stage、commit 或 push。下文改动均为建议，尚未实施。

## 1 交付身份与 Windows 验证

| 项目 | 独立结果 |
|---|---|
| 清单 | 41 项全部匹配；缺失、哈希差异、排除 `__pycache__` 后的清单外 Lite 文件均为 0 |
| G2 runbook | 与报告单列哈希一致 |
| 环境 | `Windows-10-10.0.26300-SP0`；仓库 venv，Python 3.11.9 |
| Lite 全量 | **228 passed in 39.58s**，无失败、无跳过 |
| `test_review_round5.py` | **13 passed in 1.99s** |
| `test_review_round5_selfreview.py` | **7 passed in 0.94s** |
| 顺序 | 全量 → 13 项 → 7 项，依次完成后再执行独立探针 |
| 隔离 | 全量、专项、独立探针均使用临时编译缓存；新增数据库和输出位于临时目录 |
| 范围 | Lite 全量包括既有 Windows 锁及离线 G2 工具回归；未重跑冻结的旧 V6 全量，未启动真实 G2 |

实际命令：

```powershell
cd C:\Dev\TradingV6
. .\venv_v6\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
$env:PYTHONPYCACHEPREFIX = Join-Path ([System.IO.Path]::GetTempPath()) ('v6lite-r5-' + [guid]::NewGuid().ToString('N'))
python -m pytest v6lite/tests -q -ra
# 专项在后续已激活 venv、设置独立临时缓存的 PowerShell 进程中串行执行
python -m pytest v6lite/tests/test_review_round5.py -q -ra
python -m pytest v6lite/tests/test_review_round5_selfreview.py -q -ra
```

13 和 7 项属于 228 项，不重复计为新增覆盖。交付报告的第四批 `13 failed`、第五批三次全量通过及自审第一版结果是交付方历史证据，本轮未重建那些源码版本。本次正式测试无失败重试。

| 关键文档 | SHA-256（复核前后不变） |
|---|---|
| 第五批整改报告 | `3439a202017094a7d4dc90b16e20331fc8b99bc0bfff432a690cf5ac871d56be` |
| 第四轮 Codex 复核 | `aca6043b96ab90e0d9415c352cac59e852877aac787463418eedfc4bf86b0c22` |
| 开发计划 | `203bad0cf719cbe16ef6485a38c4bc729945e20a230cbea687b84c01df849e02` |
| 本批 G2 runbook | `aa770b579eaba8d66e5ba0f74f4811ba0e0d72b9992acc8e06b6f1c4953c8bd7` |

## 2 固定请求键协议的独立裁定

### 已证实的修复

[ibkr.py:113](C:/Dev/TradingV6/v6lite/broker/ibkr.py:113) 在真实 client/wrapper 上挂计数钩子，[ibkr.py:159](C:/Dev/TradingV6/v6lite/broker/ibkr.py:159) 在新请求前排空已知欠缺的 End；等待失败时抛出证据不可用，持续欠 End 达门槛或污染触发断开。钩子安装在 connect 初始化之前，`reqOpenOrders` 与 `reqAllOpenOrders` 共用一个通道；`reqAutoOpenOrders` 不列入欠 End 计数。

本轮额外构造未连接的真实 ib_insync `IB` 对象，保留真实 `IB.connect()` → `connectAsync()` 初始化及请求/Future 逻辑，替换底层连接和不相关账户服务为本地实现。End 通过真实 decoder 的 62/53 消息处理入口进入 wrapper，确认动态挂钩确实生效，不仅是在测试中直接调用替换后的方法。

| 独立场景 | 实测 |
|---|---|
| 普通连接初始化 | positions sent/ended=1/1，openOrders=1/1；后续 fresh 得到 AAPL 20、working SELL 20 |
| 只读连接初始化 | positions=1/1，openOrders=0/0；后续只读查询仍得到 AAPL 20、working SELL 20 |
| `reqOpenOrders` 超时后调用 `reqAllOpenOrders` | 两种模式均先收齐旧 End 再查询，结果含 SELL 20；最终请求与 End 数相等 |
| 缺 End 后断开、模拟旧传输清空、重新连接 | 两种模式均先拒绝请求；新连接重新计数，fresh 得到 AAPL 20 |

没有创建网络 socket；“模拟旧传输清空”不是一次真实 Gateway 断连试验。交付测试另覆盖旧 End 迟到、永不到达、同 conId 的更新去重、缓存反驳零持仓、cancel 查询、额外 End 污染及发送前异常。**上一轮“已知超时请求的旧 End 结束下一次请求”的反例，在这些明确输入下已修复。**

### 不能扩张成无条件保证

计数能证明观察到的请求/End 数量关系，不能在协议没有 request ID 时自行证明每个 End 的来源。第五批 S5-4 测试本身承认：若额外 End 在本次真正 End 之前到达，本次可能先返回，稍后才发现污染。这个风险未被本批消除；不把“下次断开”写成“本次结果已保证完整”。

同 socket 有序传输，也不单独证明服务端会给每次请求恰好一个 End，或不会先发送其他来源的同类 End。IBKR 将 `positionEnd` 描述为请求位置数据的结束标志，见 [官方 API 说明](https://www.interactivebrokers.com/en/?f=%2Fen%2Fgeneral%2Fapi-notes-969.php)；`reqAllOpenOrders` 返回当前订单的一次列表，见 [官方接口说明](https://interactivebrokers.github.io/tws-api/classIBApi_1_1EClient.html)。本轮没有从这些资料得到异常情况下请求/End 一一对应的强保证，也没有证明真实 Gateway 会产生额外 End。**协议正常输入下的修复成立，异常协议假设与真实耗时仍需 G2 证据。**

release 的持仓缓存检查有价值，但不是完全独立的第二证据源：本机 [wrapper.py:291](C:/Dev/TradingV6/venv_v6/Lib/site-packages/ib_insync/wrapper.py:291) 的同一个 position 回调同时更新缓存并追加请求列表。它能拒绝“fresh 空、缓存仍 20”的矛盾，不能把“两者都为空”升级成任何异常时序下的零股证明。

## 3 新发现：发现 → 建议改动 → 测试

编号 `R5-F*` 是本轮发现；报告中的 `R4-F*` 在第 4 节映射。P1/P2 为代码或文档缺陷等级，与 P0 告警等级分开。临时探针使用合成行情、时钟和状态，不代表真实券商已发生同样事件。

### R5-F1 / P1：fresh 数量差异被资格早退跳过，同 tick 仍发 BUY

**发现：** [engine.py:1310](C:/Dev/TradingV6/v6lite/engine.py:1310) 在处理刚取得的 `held != ledger` 前再次调用 `_exit_still_wanted`；[engine.py:1422](C:/Dev/TradingV6/v6lite/engine.py:1422) 读缓存也看到差异后直接返回 False。因此 1313–1319 的 `_suspect`、安排对账及拒绝告警均未执行。新的读取序号机制无法保护从未记录下来的疑点。

**建议改动：** 先处理已经获得的 fresh 数量差异，再检查其他发送资格。缓存资格门本身发现数量差异时也应记录疑点、阻断入场并安排 fresh 对账；继续拒绝 SELL，不以旧账本强行下单。

**测试：** AAPL 账本 20、行情 120，TP 排队；订单查询一秒期间外来 SELL 2，使缓存和随后 fresh 都为 18。完整 tick 输出：

```text
fresh_reads=[[AAPL,18]]
truth=18, ledger=20
reconcile_ok=true, quantity_suspect={}, reconcile_due=false
entries=[[AAPL,20],[SPY,20]]
```

SPY BUY 20 是该 tick 正常扫描实际提交的新订单，AAPL ENTRY 是原持仓。SELL 自身正确被拒绝，但全局新增风险没有被挡住。这与上一轮“已记录疑点被覆盖”不同：本次差异根本没有记录。报告 R4-F2 的原时序对照现在正确保留疑点并拒绝新 BUY，应认可该局部修复。

### R5-F2 / P2：原来没有持仓行的旧 ENTRY 绕过“已证实为空仓”规则

**发现：** [store.py:566](C:/Dev/TradingV6/v6lite/store.py:566) 在 `pos is None` 时先创建/合并持仓并返回；`flat_verified` 检查直到 [store.py:595](C:/Dev/TradingV6/v6lite/store.py:595) 才执行。已撤订单此前从未收到首笔成交回报，就会走这条入口，违反 D18 对所有旧订单的范围。

**建议改动：** 将业务终点判断置于任何建仓、加仓、恢复路由之前；对只有意图而没有 position 行的旧订单也适用。保留成交证据、异常及真实 broker 风险复核，不自动制造托管持仓。

**测试：** 09:40 创建旧 ENTRY，09:41 已撤且从未入账；同标的另一仓 10:00 已 release，记录 `flat_verified=10:00`。旧 BUY 5 的执行时间在撤单确认前，接收时间在 release 后。Store 实际新建 `OPEN 5 / pos-never-booked`，返回 `anomaly=None`，异常集合为空。

此探针验证 Store 的实际路由，不宣称完整引擎没有其他迟到成交告警，也没有据此声称新 SELL 已被发出。

### R5-F3 / P2：第四批 schema 4 的已 release 恢复仓仍可被迟到 BUY 重建

**发现：** 第四批 release 有持久 `releases` 记录，却不会写本批新增的 `flat_verified` 控制项。新 Store 接受同版本状态，但没有从既有 release 证据获得终点；当意图原仓为 CLOSED、恢复仓已 RELEASED 时，[store.py:605](C:/Dev/TradingV6/v6lite/store.py:605) 只寻找 OPEN 恢复仓，最终另建仓。schema 数字不变不等于恢复语义自动兼容。

**建议改动：** 从已有、可信的 release 记录识别相关业务终点，或显式拒绝/隔离无法支持的旧状态。不能无条件宣称第四批所有 schema 4 状态都可沿用；纯 dry-run 状态与发生过交易/release 的正式状态应区分。

**测试：** 按第四批持久形态合成临时 schema 4：保留原 CLOSED、恢复 RELEASED 和一条 release，只移除第四批不会写入的 `flat_verified`；关闭并重开 Store。新代码接受该库，`flat_at=None`。随后旧 ENTRY 的迟到 BUY 3 新建 `OPEN 3`。

**这是按旧持久形态合成的兼容性探针；未读取真实旧数据库，也未执行第四批源码。** 迟到成交发生时间早于撤单确认，反例不依赖已终结订单再次撮合。

### R5-F4 / P2：“仅作证据”的成交仍进入 CLOSED 仓的当日盈亏

**发现：** [store.py:505](C:/Dev/TradingV6/v6lite/store.py:505) 先把 fill 关联到原持仓写入普通 fills；后续命中 `late_fill_before_flat` 只阻止重建仓位，没有隔离会计用途。[store.py:801](C:/Dev/TradingV6/v6lite/store.py:801) 仍选中相关 CLOSED 仓，[risk.py:66](C:/Dev/TradingV6/v6lite/risk.py:66) 按这些 BUY 构造 lot，并把不存在的剩余 lot 盯市。

**建议改动：** 给终点后只作证据的成交明确核算分类，并从托管数量、lot 盈亏等计算中排除。保留原始审计；佣金是否按报告所述保守计费应单独定义，不能以佣金政策解释 BUY 本金造成的虚假盈亏。

**测试：** 正常 CLOSED 仓已实现毛利 $200；冻结解除并记录终点。之后旧 BUY 5@$90 命中 `late_fill_before_flat`，没有新 OPEN；当前 mark=$110 时：

```text
day_pnl_before=200, day_pnl_after=300
closed_realized_gross=200, open_positions=0
daily_stop_latched=1
```

当日盈亏多出 $100；日停仍锁定，**未证明日停绕过**。这不是报告剩余风险第 5 条的迟到佣金，也不是单纯保守高估亏损。

### R5-F5 / P2：报告称任何 BAG 全账户阻断，代码仍按已知腿局部预留

**发现：** [ibkr.py:337](C:/Dev/TradingV6/v6lite/broker/ibkr.py:337) 仅在 `unresolved=True` 时追加 `*`。腿全部已知时只返回逐 symbol 的股数。报告第 6 节第 3 条和 D13″ 却写成“任何 working BAG 阻断全部 SELL/STOP 和 release”。

**建议改动：** 统一正式策略、实现和测试。若采用报告提出的全账户阻断，所有 BAG 都须产生相应阻断；若保留已知腿局部预留，则应明确修订这一设计判断和剩余风险说明，不能声称已实现全账户阻断。

**测试：** working BAG 为已知 TSLA SELL 5 + SPY BUY 5，适配器不返回 `*`。端到端结果：AAPL STOP 20 实际提交；另一个平仓场景中 AAPL release 成功。把其中 conId 改成未知后，`*` 出现，AAPL STOP 被 P0 拒绝，release_count=0。

未知腿原缺陷确实修好了；这里是策略/交付声明不一致，**没有证明已知股票腿正常预留本身造成超卖**。

### R5-F6 / P2（文档）：G2 runbook 仍引用旧预算和第三批放行条件

**发现：** 本批 runbook 附录已指向第五批，但 [第 51 行](C:/Dev/TradingV6/docs/V6_LITE_G2_RUNBOOK.md:51) 仍要求 query ≤17 秒，[第 81 行](C:/Dev/TradingV6/docs/V6_LITE_G2_RUNBOOK.md:81) 仍把“第三批修复通过 Codex 复核”作为 G3 前提。当前适配器声明 query=2+15+2=19 秒；第五批尚有未关闭问题。这些操作验收口径不应同时存在。

**建议改动：** 同步为第五批实际预算和当前复核门槛，更新过期的第三批措辞。区分原始 IB 请求耗时与适配器排空+请求耗时；G2 探针直接调用原始 IB，不能仅用它的 timings 证明包含排空等待的适配器预算。

**测试：** 静态比对 runbook 的 51/78/81/85 行与 [ibkr.py:263](C:/Dev/TradingV6/v6lite/broker/ibkr.py:263)、[g2_probe.py:132](C:/Dev/TradingV6/v6lite/tools/g2_probe.py:132)。没有运行 Gateway，也没有按旧文档改变 Read-Only API 设置。

## 4 对报告第 3 节八行逐项裁定

| 报告项 | 裁定 | 发现 → 改动 → 测试 |
|---|---|---|
| R4-F1 旧 End 污染下一查询 | **CLOSED_WITH_EVIDENCE（已知超时/迟到 End 范围）** | 已先排空再发送、计数校验、断开重连、cancel 共用守卫及 conId 去重。13/7 专项通过；额外真实 IB 初始化、只读、共用 End、模拟重连通过。额外 End 的残余假设未关闭，见第 2 节；不据此验收 D11″ 的绝对保证。 |
| R4-F2 数量疑点被旧对账清除 | **OPEN_PRODUCT_DEFECT** | 原覆盖时序已通过新序号机制修复；但 fresh 差异可在资格检查早退时从未写疑点，同 tick 实际新 BUY，见 R5-F1。 |
| R4-F3 release 后旧 BUY 不得重建、只作证据 | **OPEN_PRODUCT_DEFECT** | 原仓 RELEASED 路径通过，推广后的全部旧 ENTRY 与证据会计不完整；无 position、旧 schema4 恢复仓及日盈亏反例见 R5-F2/F3/F4。 |
| R4-F4 CLOSED 冻结解除 | **CLOSED_WITH_EVIDENCE** | release 的 fresh/缓存/订单/静默门后可进入 `resolve_freeze`；注入事务中异常完整回滚，成功后重启 CLOSED 保留、日停=1、release_records=0、只计原交易一次。 |
| R4-F5 冻结订单人工交接 | **CLOSED_WITH_EVIDENCE** | held/孤立/人工/未知 BAG 同时出现的额外探针均列出且标明 NOT cancelled；先确认无 working、THEN 处理持仓。查询失败要求 TWS 逐单确认；不擅自撤不确定订单。 |
| R4-F6 未知 conId 被忽略 | **CLOSED_WITH_EVIDENCE** | 未知 conId 现在产生 `*`；额外适配器→引擎验证 STOP 无新单且 P0、release 拒绝。D13″ 扩张为全部 BAG 的声明另见 R5-F5。 |
| R4-F7 held 冻结高频查询 | **CLOSED_WITH_EVIDENCE** | in_flight 已排除 hold；正常跨日前日部分 ENTRY 冻结场景，60 个一秒 tick 内 fresh positions=0、open_orders=0，未再因 held 强制对账。 |
| R4-F8 拒发时重新判断 STOP 告警 | **CLOSED_WITH_EVIDENCE（实际进入 refuse 分支）** | 最新有效报价使拒单告警升 P0，判断纯计算；独立复验没有创建 STOP 或发 SELL。资格早退不进入该 helper 的遗漏，与 R5-F1 同根，不重复计缺陷。 |

合计：**6 CLOSED_WITH_EVIDENCE，2 OPEN_PRODUCT_DEFECT**。

## 5 对报告第 5 节自审逐项裁定

| 自审项 | 裁定 | 发现 → 改动 → 测试 |
|---|---|---|
| S5-1 拒单用错误账本创建 STOP | **CLOSED_WITH_EVIDENCE** | `_latest_exit_stage` 只读/计算；拒绝后 STOP stage=None、P0 正确、无 SELL。补记迟到 10 股后独立复验发 STOP 30，全部平仓，剩余=0。 |
| S5-2 恢复仓/解除冻结后的旧 BUY | **OPEN_PRODUCT_DEFECT** | 两条交付测试通过；终点检查位置太晚、旧 schema4 缺标记及证据核算仍有缺口，见 R5-F2/F3/F4。 |
| S5-3 cancel 查找订单绕过通道 | **CLOSED_WITH_EVIDENCE** | 最后服务器查询改用 `_clean_request`；交付真实 ib_insync 旧 End 回放后正确发起本地模拟 cancel。仅在已有交易缓存找不到时才走查询符合现有设计。 |
| S5-4 额外 End 后通道仍当干净 | **CLOSED_WITH_EVIDENCE（检测之后）** | 超额 End 标记 poisoned，下一请求断开，专项通过。已经被提前 End 完成的那份结果不能追回，原报告也承认该残余，见第 2 节。 |
| S5-5 发送前失败仍欠 End | **CLOSED_WITH_EVIDENCE** | 底层发送调用成功返回后才加计数；连接前异常 sent/ended=0/0，专项通过；自动订单订阅不计 End。这不是对网络对端实际接收的确认。 |

合计：**4 CLOSED_WITH_EVIDENCE，1 OPEN_PRODUCT_DEFECT**。

## 6 第 4 节三个条件及六项设计判断

| 三个条件 | 裁定 |
|---|---|
| 本次零持仓、无订单证据完整可信 | **已知超时串扰本地修复；实际协议完整性 IMPLEMENTED_VALIDATION_PENDING。** 额外 End、跨查询外来单不能由计数或缓存年龄排除；缓存是同源交叉检查。 |
| 业务终点后旧成交不得激活持仓 | **OPEN_PRODUCT_DEFECT。** R5-F2/F3 仍会新建；R5-F4 即使没有新建也会污染会计。 |
| 原仓已被完整成交关闭后可解除冻结 | **CLOSED_WITH_EVIDENCE（本地流程）。** 同样门控、事务日停、不额外计一次 release 亏损成立；真实操作链仍需 G3 验收。 |

| 判断 | 表态 | 依据与边界 |
|---|---|---|
| D11″ 本次查询必须由自己的 End 完成，脏通道不可用 | **接受目标；只接受有条件实现，真实验证待完成。** | 已知历史欠 End 可以排空，初始化和共用通道有本地证据；无 request ID 时计数不是来源证明，额外 End 先到的窗口仍在。把协议前提写清，不能写成无条件保证。 |
| D13″ 未知 conId 失败关闭，全部 BAG 阻断 | **接受未知腿原则；当前“全部 BAG”完成结论推翻。** | 全账户阻断可作为不交易组合单的保守范围选择，但实现仍是已知腿局部预留。按选定策略同步实现与测试后再验收。 |
| D15′ 冻结列全订单、先订单后持仓、不自动撤不确定订单 | **接受，本地通过。** | 同时多来源订单及查询失败对照通过；列表只是查询时所见，真实 TWS 确认仍不可省略。 |
| D18 已证实为空仓是终点，旧订单迟到 BUY 只作证据 | **接受业务终点原则；当前不完整。** | 创建时间早于终点可用于程序的保守路由，不是对券商未来成交的证明。须覆盖无持仓意图、旧状态，并把证据与 lot 核算隔离。 |
| D19 CLOSED 冻结经 release 解除、日停、不重复计亏损 | **接受，本地通过。** | 回滚及重启均验证；原 CLOSED 交易保留，release_records=0，连亏仍只有原交易事件。 |
| D20 拒单风险判断纯计算 | **接受，本地通过。** | helper 无阶段写入；30 股完整退出复验通过。资格早退漏记差异另由 R5-F1 修复，不能通过重加副作用解决。 |

## 7 剩余风险、修复顺序与门槛

对报告第 6 节五条风险：

1. **额外 End 提前结束当前请求：保留未完成。** 已有本地测试证明当前程序不能即时识别所有这种输入；真实发生条件/频率未知，不伪装成已验收完整性。订单列表没有第二份独立来源，持仓缓存也不能作为绝对证明。D11″ 只能有条件接受。
2. **缺 End 期间拒绝 SELL：接受失败关闭方向，时效待验证。** 2 秒等待、60 秒重连门槛、30 秒引擎重连节奏是工程设置，不是全链路完成上限。G2 分开记录请求、排空、重连和恢复后取证耗时。
3. **所有 BAG 阻断：与当前实现不符。** 见 R5-F5，不能作为已存在的运行限制交给操作员。
4. **订单/持仓非原子快照：保留边界。** 操作员不与系统同时交易托管标的是必要操作约束之一；冻结时人工接管须先确认相关订单。只读 G2 不能证明 G3 并发操作安全。
5. **终点后佣金计费：可作为单独保守口径。** 它不能解释 R5-F4 的虚假正收益。必须分别验证佣金、证据成交、仓位 lot 三者的处理。

建议按以下顺序闭项：

1. **先修 P1 R5-F1。** 将已有 fresh 差异记录/阻断放在资格早退前，固化完整 tick 的新 BUY 反例。
2. **一起完成终点路由与会计的三个 P2。** R5-F2/F3/F4 需要一致规则：所有旧 ENTRY 入口、第四批持久状态、证据成交对数量和日盈亏的用途。保持无自动 resync、无未经核验的持仓接管。
3. **同步 BAG 和 G2 文档两项 P2。** 不把交付声明与代码不一致留给操作员临场判断。
4. **重新复核后再讨论 G3。** 本轮 G3 为 NO-GO。只读 G2 可继续作为操作员监督下的证据采集，但本轮没有启动，也没有批准解除 Gateway Read-Only API。
5. **L7 仍是 Live 的独立必需项。** 本批没有实现券商端保护单，进程内 STOP、冻结通知和人工接管不能替代这一验收。

## 8 临时复现材料与完整性收尾

以下探针不是交付的 41 文件，也未加入仓库测试；缺陷探针对“当前错误结果”的断言通过，不表示产品安全通过。修复时应把期望改为正确行为。调度脚本使用固定的临时子目录，复跑应复制到新目录，避免重复使用已有探针数据库。

| 材料 | SHA-256 |
|---|---|
| [真实 IB 初始化/共用 End/模拟重连](C:/Users/yu-ch/AppData/Local/Temp/v6lite_round5_channel_probe.py) | `9e1d881730f312718cee8e398381363443cf571f203ab7a3e043e6ba24869755` |
| [资格早退 P1](C:/Users/yu-ch/AppData/Local/Temp/v6lite-r5-suspect-e9905aa807ef47db94a8028d4d4a9229/probe_r5_exit_suspect.py) | `47bf132f0e171b0e2489bd71db476305cf606b245f9c706d701b836dbb17b11a` |
| [疑点序号/held 节流正向复验](C:/Users/yu-ch/AppData/Local/Temp/v6lite-r5-suspect-e9905aa807ef47db94a8028d4d4a9229/probe_r5_regression_closures.py) | `97182279ef1cfddd25e7f6671db8c981a996b734b4abe39d3b46cbc047f9dd75` |
| [拒单纯计算/30 股 STOP](C:/Users/yu-ch/AppData/Local/Temp/v6lite-r5-suspect-e9905aa807ef47db94a8028d4d4a9229/probe_r5_refusal_pure_30.py) | `74f90089c594ab3db954800c87bba3ec062d8bc5a4c295033f71cf0d1b5f2166` |
| [终点路由/旧状态/盈亏/事务](C:/Users/yu-ch/AppData/Local/Temp/v6lite-r5-accounting-9391ee6b70934637bc2bd64be74e4222/round5_accounting_probe.py) | `8c50455a7f4ba749cbf165794bdf6dbc9ffe34e413f7b696f7b1bf1415df00ed` |
| [BAG 与冻结交接](C:/Users/yu-ch/AppData/Local/Temp/v6lite_round5_bag_handoff_probe.py) | `a63d9f3bdd3165f8b3b01712bd2930dc3e0453831baeed8a3a50178e55fcccd8` |

上述六份探针均执行完成、退出码为 0。会计反例最后把成交发生时间收紧到撤单确认之前，接收仍在 release 之后，结果不变；不依赖“订单取消后重新撮合”的假设。临时目录可能被系统清理，关键事件顺序与数值已记录在正文。

收尾核对：41 个清单文件、G2 runbook，以及复核前已有 11 份 `V6_LITE*.md` 均保持原字节；暂存区为空。原有未提交改动保留，本轮唯一新增仓库产物为本复核记录。
