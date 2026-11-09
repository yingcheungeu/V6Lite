# 第二十五批指令（Claude → Codex）：重启接管时的“非主动 openOrders End”导致无限重连

日期：2026-10-14。工作区 `C:\Dev\V6Lite-dev`，dev 分支，基于 `v6lite-batch24`（code_sha `2db6af8f04f145f6`，运行 tag `v6lite-run-b24`）。分工照旧：调查和测试设计可以并行；生产代码只由一个实施 Agent 修改；最终验证串行执行。**优先级最高：今晚复核合并，10/15 重测重启接管。**

## 0 10/14 事实

证据：`docs/batch25_evidence/g3t_20261014_restart_loop.log`，账户号已脱敏。

- **10/13 收盘时的状态：**
  - 持仓 AAPL 3 股（pos-99ec110ffc6246e8b7c4）；
  - 券商端 GTC STP 保护单：orderId 501，permId 45208044，clientId 132，止损价 340.52，状态 WORKING；
  - 正常停机，SHUTDOWN 记录里 unresolved=[]。
- **10/14 06:19:52 PT 启动 `~\v6 g3t`。** 连接后、`API connection ready` 之前，TWS 主动推送了：
  - orderId 501 的 `openOrder`：orderType=STP，**lmtPrice=0.0**，auxPrice=340.52，GTC；
  - 对应的 `orderStatus`：PreSubmitted，whyHeld=trigger；
  - 以及一个 `openOrderEnd`。
- **06:19:52.434 b24 报错：** “unsolicited openOrders End; channel poisoned”。0.9 秒后判定通道被污染，主动断开并重连（“renewing the connection”）。
- **每次重连都重复同一过程：** 06:19:52 到 06:22 之间出现了 7 次，大约每 30 秒一次，形成无限循环。
- **期间发出的卡片：**
  - P1“券商查询已限速”：06:19:53，在任何真正的核验之前；
  - 多次 P1“IBKR 连接断开”；
  - P1“recovery failed”；
  - P0“保护单三项证据不完整”（06:20:54，持续失败 60.8 秒）。
- **结论：** 只要同一 clientId 在连接前已有工作中的订单（也就是带 GTC 过夜之后的每一次重启），b24 都会进入这个循环。这正是 G3-T 要测的“重启后接管持仓”场景。10/13 早上没出问题，是因为当时没有任何未成交订单。

## 1 修复：连接握手和同步阶段的 openOrders End

1. **调查：** 列出连接时 End 的全部来源，并引用 IBKR / ib_insync 资料（只引用资料，不要猜测）：
   - TWS 自动推送同一 clientId 的未成交订单；
   - TWS 设置“连接时下载未成交的委托单”；
   - ib_insync 启动同步时自己发出的 reqOpenOrders / reqAllOpenOrders；
   - 然后说明 b24 的“非主动 End = 污染”这条规则为什么会把它们误判。
2. **新规则：**
   - 在当前连接代的握手和同步期间，到第一次由我们登记的 openOrders 请求之前，收到的 End 属于“连接快照 End”：不污染通道，不触发重连。
   - 连接快照里的 openOrder 和 orderStatus 可以记录下来，但**不能**当作新鲜的三项证据。接管仍然要等我们自己发出的、完整的、强身份的三项查询通过。
   - 同步完成之后，真正无法归属的 End 才按 b24 的规则处理。
3. **限制重连次数：** 同一个原因在 5 分钟内导致的重连超过 2 次，就停止自动重连，发一张 P0“连接反复重建，已停止自动重连”，写明原因和处置步骤。不允许再出现每 30 秒一次的无限循环。重连本身的退避按 30 秒、60 秒、120 秒递增。
4. **STP 回传限价的第三种取值：** 今天回传的是 `lmtPrice=0.0`，10/12 回传的是 340.19，另外还可能是 UNSET。确认三种取值都只记入审计（`broker_echo_lmt`），都不参与身份判断。

## 2 修复：过早发出的“券商查询已限速”P1

- 10/13 07:43:44 和 10/14 06:19:53 两次都是**误报**：第一次是券商确认还没到达，第二次是连接还在同步。
- **新规则：**
  - 处于 A 空窗内等待第一次券商确认（不超过 `gap_a_p1_seconds`）时，不发这张 P1；
  - 连接同步完成之前，不发这张 P1；
  - 只有在真正发生“核验失败并进入退避”之后才发。

## 3 测试

1. **真实回放：** 用证据文件里的原始序列构造原生回调回放：connect → 推送 openOrder（STP，lmtPrice=0.0）→ 推送 orderStatus（PreSubmitted）→ openOrderEnd → API ready → 同步完成。
   - 断言：没有重连；接管 orderId 501；30 秒内得到完整三项证据，状态为 WORKING；S=3；没有任何 P0/P1；不另下保护单。
   - 先在 `v6lite-batch24` 上失败，再修复。
2. **端到端重启接管：** 状态库里有一个 OPEN 持仓和一张 WORKING 的 GTC STP，FakeBroker 在连接时按真实顺序推送这张单，然后重启引擎。断言同上。另加一个变体：停机期间 STP 已经成交，重启后要正确认领成交并关闭持仓。
3. **重连上限：** 让同一原因持续触发，断言最多重连 2 次后停止，只发一张 P0，没有无限循环。
4. **P1 时机：** 覆盖第 2 节的两种情况。断言不发 P1；而真正发生核验失败时，P1 仍然只发一次。
5. **夹具规则：** 以后所有 FakeBroker 的连接夹具，都要支持“连接时推送同一 clientId 的未成交订单和 End”。在报告中说明为什么 b24 的 1559 项测试没有覆盖到这个场景。

## 4 交付

- 报告 `docs\V6_LITE_FIX_REPORT_ROUND25_2026-10-XX.md`，内容包括：每项的发现、改动和测试；红绿测试结果；Windows 全量连续 3 次的结果（13:30 PT 之后跑）；新卡片样例；SHA-256 清单。
- 在 dev 提交并打 tag `v6lite-batch25`，交付后冻结，等待 Claude 复核。

## 5 约束

- 交易日 06:00–13:30 PT 只跑聚焦测试；
- 不连接 TWS 或 Gateway；
- 不碰 `%USERPROFILE%\.v6lite`、运行仓库和 John 的配置；
- 不 push；不输出任何机密；
- 操作手册由 Claude 更新，Codex 不改。
