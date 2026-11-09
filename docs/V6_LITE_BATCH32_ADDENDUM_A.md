# 第三十二批补充指令 A（Claude → Codex）：引擎停机期间在 TWS 撤掉的 GTC，release 永远无法通过

日期：2026-10-29（PT）。**与第三十二批同一个 tag 交付**（`v6lite-batch32`），优先级与 §2.1 相同。基线不变：`v6lite-batch31`。

## 0 10/29 事实

来源：Claude 只读查看的状态库，账户号已脱敏。

1. **人工处置的经过：**
   - 08:49:09 PT，John 按 Ctrl+C 停机（`SHUTDOWN source=SIGINT`，`broker_working=[]`，`unresolved=[]`）；
   - 停机之后在 TWS 撤掉 STP 2890（permId 753795445）并平仓。
   - 平仓时误卖了 100 股，随后买回 98 股。现在券商端 AAPL 为 0，没有在途订单。
2. **两次 release 都被拒绝**（15:56:02Z 和 16:14:04Z），原因都是：
   > protected GTC cancel/identity unverified; release refused
3. **保护 intent 的现状：** `v6l-protect-62b2…`，status=SENT，last_raw_status=PreSubmitted，cancel_seen_at=null，hold_reason=null。protections 行为 WORKING，confirmed_remaining=2；持仓 `pos-63072ec6…` 仍是 OPEN 2 股。
4. **代码原因：**
   - b31 `engine.py` 的 `_apply_releases` 要求保护 intent 必须是 `CANCEL_SEEN` 状态，并且原始状态为 Cancelled/ApiCancelled，也就是撤单必须被引擎亲眼看到。引擎停机期间发生的撤单，重启后永远不会补到这个状态（没有 completed orders 查询）。
   - `resolve-intent` 只接受 UNKNOWN 或被 hold 的 intent，这里也用不上。
   - 运行约束 B 禁止手动改状态库。**结果是这个持仓没有任何合规途径可以解除。**
5. **流程本身的问题：**
   - 运行约束 A 的人工处置流程写的是“停机→撤单→平仓→release”。代码实际上隐含要求“引擎运行中撤单”。
   - 引擎崩溃或断线期间，操作员也可能只能在 TWS 里撤单。所以“停机期间撤单”这条路径在实盘中也必须能处理。

## 1 修复

1. **离线撤单的终态证据：**
   - release 时，如果保护 intent 不是 CANCEL_SEEN，就主动做一次只读的 completed orders 查询（例如 `reqCompletedOrders(apiOnly=False)`，走现有的限速通道）。
   - 找到同一 account、conId、orderId、permId、orderRef 的那条记录，状态为 Cancelled/ApiCancelled，`filled` 与账本中这张保护单的已成交量一致。满足这些条件时，把这个终态作为该 intent 的撤单证据：写审计 `PROTECT_CANCEL_PROVEN_COMPLETED`，然后继续走现有 release 的其余全部检查。
   - 其余检查一律保留：两次稳定的完整快照、券商持仓为 0、没有任何在途订单、静默期、成交签名一致。
2. **找不到这样的终态记录时（completed orders 没有记录、身份不一致、状态是 Filled 或其他）：** 继续拒绝，并在卡片里写明缺的是哪一项。不允许只凭“openOrders 里没有这张单”就当作已撤单。
3. **成交核对：** 如果 completed orders 显示这张保护单有成交，或者当天的成交里有它的成交，就先按现有成交入账规则处理，不能跳过。
4. **卡片：**
   - release 被拒绝时，P1 卡片要写出具体原因，例如“保护单 2890 的撤单未被核实：completed orders 中未找到同身份的 Cancelled 记录”。现在的卡片只写“未通过新鲜券商证据检查”，操作员无从下手。
   - release 成功时，按 §2.4 使用明确的标题。

## 2 测试

1. **真实回放 10/29：**
   - 持仓 OPEN 2 股，protection 为 WORKING / SENT；
   - 停机后撤单；重启后 openOrders 中没有 2890，持仓为 0；
   - completed orders 返回同身份的 Cancelled、filled=0；当天成交包含误卖 100 股和买回 98 股（都不是本系统的 intent）。

   断言：
   - release 成功，持仓 RELEASED，冻结解除，当天入场锁定；
   - 外部成交按现有规则处理，不计入 Lite 成交。

   先在 `v6lite-batch31` 上失败，再修复。
2. **负向：**
   - completed orders 中没有这张单；
   - permId 不同；
   - 状态为 Filled；
   - 两次快照之间出现新的成交。

   断言：都拒绝，卡片写明具体原因，状态库不变。
3. **在线撤单路径：** 引擎运行中撤单，现有 CANCEL_SEEN 路径保持不变。
4. **回归：** b22–b31 的全部 release 和 resolve-intent 测试保持通过。

## 3 交付

并入第三十二批报告，单独列一节“补充指令 A”，内容包括：调查结论（SDK 是否支持 completed orders、Paper 账户是否返回、限速）、改动对照、红绿测试结果、新卡片样例。

约束与第三十二批 §5 相同。
