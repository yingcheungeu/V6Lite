# V6-Lite 第十五批整改报告：L7 券商端保护单

报告日期：2026-10-04；工作区：`C:\Dev\V6Lite-dev`，分支 `dev`。实施依据：[L7 裁决](V6_LITE_L7_RULING.md)与第十四批 tag `v6lite-batch14`。本批是代码和 FakeBroker 证据，不是 Live 放行；未连接 Gateway。本批未触碰 `C:\Dev\V6Lite` 的已跟踪文件或 main HEAD，也未合并或推送。

## 基线、授权与风险边界

第十四批已单独提交：commit `3c83199fef19ab191e4dbc48f70db352f2daf1e9`，提交说明 `V6-Lite batch 14: G3 Paper test mode (code_sha 03665f2bc92bf15e)`；附注 tag `v6lite-batch14` 指向该 commit。提交前核对了第十四批报告的 60 项 SHA-256 清单、暂存文件范围、机密与大文件，未发现异常。此后第十五批改动留在 `dev` 工作树，未 stage、未 commit。原运行仓库 `C:\Dev\V6Lite` 的 HEAD 仍为 `5bd1167f43d512d3e743d15c3980d635dc2dc57d`，其已跟踪文件干净；该仓库另有既存未跟踪的 `docs/V6_LITE_L7_G3T_INSTRUCTIONS.md`，本批未触碰。

John 的本轮确认原文（逐字记录）：

> John 已确认：接受裁决 2 的两个有时限、有告警的短暂空窗作为实盘 v1 的已知风险（时限按第 2 版：空窗 A 5 秒 P1 / 15 秒 P0；空窗 B 10 秒未成交即撤单恢复、30 秒内未恢复发 P0；实盘放行要求实测最长空窗 ≤ 5 秒），并同意裁决 3 的“卖出权按标的独占”运行规则。

接受风险的前提仍是 G3 Paper 实测、Claude 复核及单独 Live 放行；本报告不把本地测试当作券商保证。两个空窗分别是 BUY 成交到 GTC 保护获券商确认（A），以及保护减量获确认到 TP 完全成交或恢复保护（B）。A 的 5 秒 P1、15 秒 P0，B 的 10 秒撤 TP 并尝试恢复、30 秒 P0 均为可配置上限；运行时逐笔记录 `[PROTECT_GAP]`，每日健康报告列最大值。G3 要测得最长实际空窗 ≤5 秒，且无未处理的崩溃窗口事件，才有讨论 Live 的证据。卖出权按标的独占：Lite 托管期间其他程序和人工不得对同一标的再挂 SELL；紧急人工卖出先在 TWS 撤/减 Lite 卖单。不受托管的标的可独立交易。外部卖单若先于检测进入，瞬时毛卖量违例无法由本程序倒转；检测后冻结、只减本系统订单并 P0，不声称从未超卖。

## 发现 → 改动 → 测试

| 发现 / 设计要求 | 本批改动 | 离线证据 |
|---|---|---|
| 第十四批只有 DAY 退出，没有券商端 GTC 保护 | 新增 `protection.py` 与 `protection_engine.py`；按已持久化 stop 生成 SELL `STP LMT`、`GTC`、`outsideRth=False`，3% 限价偏移，股价 ≥1 美元且 0.01 tick 向下取整；STOP 路径改为监控和 P0 | 价格、字段、拒低价、触发未成交及零市价调用测试 |
| 入场成交后可能裸持仓 | BUY 发出前持久化保护计划和独立 orderRef；`execDetails` 入账后同一回调优先提交首笔保护，后续部分成交增量只在三源证据一致时提交；未确认前阻断其他入场；拒单/超时告警 | 首笔/后续成交、5/15 秒、回调本地故障、跨标的最终 BUY 门控测试 |
| TP 与保护同时满额会超卖 | 以 `P`（持仓）、`S`（保护最大可成交余量）、`T`（自有 TP 最大余量）、`X`（外部/未知 SELL 最大余量）验证 `S+T+X≤P`；先减 S 并确认，再发即时可成交的 DAY/LMT TP；同一时刻全账户只运行一项 TP 保护工作流；末股先确认旧 GTC 取消再发 TP，失败后由三证据恢复新 GTC | 全状态转换/部分成交/待确认最大量、10/30 秒、末股、迟到成交、外部单、拒改/查询超时、逐步崩溃测试 |
| 本地提交返回及单个状态回报不是券商确认 | 持久化 account/conId/clientId/orderId/permId/orderRef、修改代际与最大可能卖量；新鲜持仓、全 client 工作单和执行记录一致才可增量或补挂；未知、跨日证据不全只减不增；重启先按精确身份认领，未认领 GTC 禁止泛型撤单 | 不完整身份、跨日缺执行、改单拒绝、重启认领、孤儿 GTC 测试 |
| 停机/断线可能撤掉最后保护 | 正常停机只清 DAY 单，保留已确认 GTC；不明身份、价量或状态时 P0；kill/冻结/日损不撤 GTC；回调及硬时限回路优先于扫描/历史请求 | 停机、三个安全控制、慢请求抢占、250ms 回路与告警卡片测试 |
| 需区分 Paper 测试与正式 client | 正式 `client_id=131`、测试 `132`，状态身份分离；`[protection] enabled=true` 默认，Live 显式关闭即拒启动；所有时限、偏移纳入指纹；dry-run 仅 `WOULD_PROTECT`，三种券商写调用都封闭 | 配置/指纹、client 身份、read-only/dry-run、submit/modify/cancel 边界测试 |

实现没有 OCA、没有自动市价/追价、没有第二张程序化 STOP。已确认的保护单在单独进程停机后能否持续由真实 IBKR 保证，本地不可证明。所有新生产代码只由一个实施 Agent 修改；测试由另一 Agent 先红后绿并维护，独立 Agent 只读审查，最后的 Windows 全量测试由主 Agent 串行执行。

### 与裁决文字的具体差异及待 Claude 判断

发 BUY 时先按计划限价和 ATR 写入保护触发价/限价/orderRef；若真实成交价与限价不同，成交回调在提交首张保护之前，按实际入账后的持久化 `positions.stop` 在本地校正价格。这比裁决中“成交后只填数量”多一步本地计算，但确保保护价与已入账止损一致，回调不做阻塞式券商查询。请 Claude 判断此差异是否可接受。

启动/重连期间尚未核实 account、client 与原 GTC 身份时，持久化 B 期限到点会先 P0 告警，自动撤 TP 延至身份确认与接管后；这是为避免对未知订单误撤。事件回调不能在 ib_insync 回调内部做阻塞查询；首次保护提交走同一回调，后续需要三源证据的改单/TP 由优先队列在普通扫描和历史数据前处理。若主事件循环已进入同步慢 broker 请求，回调中的改单仍可能等待该调用完成，独立的时限回路只保证告警/触发处理不被漏掉；G3 必须测 `[PROTECT_DISPATCH]` 与 A/B 实际延迟。真实 Gateway 对部分成交改单 `totalQuantity`、跨日 `reqExecutions` 覆盖、全 client 工作单快照和 GTC 重启认领的行为尚未验证，证据不闭合就保持 UNKNOWN、阻断增量和 TP。

## 测试与独立复核

写代码前检查 Windows 无 `v6lite` 运行进程（0 个）。新 `v6lite/tests/test_batch15.py` 在未改第十四批代码的隔离基线上先运行：**89 failed、1 passed、退出码 1**；该测试文件最终 SHA-256 为 `54f1ef38edf97bc38488d4f446e66c0b22dd8229f2670df96509dd80d9eb8c42`。修复后同一新增测试 **90 passed in 6.21s、退出码 0**。单独审查的 10 项关键用例 **10 passed / 72 deselected in 1.20s、退出码 0**。旧测试适配了 FakeBroker 对 GTC/订单身份的样例断言；第一次全量暴露 10 项旧 fixture 失败，修正这些 test-only 假设后再执行最终连续三轮全量。

Windows `C:\Dev\V6Lite-dev\.venv`，Python 3.11.9；每轮依次执行 `python -B -m pytest v6lite/tests -q -p no:cacheprovider --tb=short`，并设 `PYTHONDONTWRITEBYTECODE=1`、`PYTHONUTF8=1`；旧扫描器路径仅供测试读取。结果均自然退出码 0：

| 连续轮次 | Windows 全量结果 | 退出码 |
|---|---|---:|
| 1 | 565 passed in 56.52s | 0 |
| 2 | 565 passed in 56.68s | 0 |
| 3 | 565 passed in 56.58s | 0 |

独立复核从入口追到 `placeOrder`、同 ID 修改与 `cancelOrder`：只读/dry-run 阻断写调用；保护订单的精确身份、可能最大余量、TP 减量确认屏障及迟到成交处理符合本地不变式；泛型 GTC 撤单路径被拒。审查没有剩余本地 P0 阻断项，但**不是**真实 Paper/Live 通过。未连接 Gateway、未运行 `v6lite run` 或 `g2_probe`，无 push、merge、stage 或第十五批 commit；未改运行配置、状态目录、`.env`，未输出 token/webhook/完整账户。

## P0/P1 实际渲染样例

下面由 `FakeBroker + MemoryChannel` 在合成交易时刻渲染，订单号和 orderRef 均为合成标识；不是手机送达记录，也不表示真实 Gateway 状态。覆盖 14/14 种保护 P0/P1 卡片，另含迟到成交及每日健康栏。原始临时输出的 SHA-256 为 `3ce4c40866c43e54699112df4249b0b2c3fabc190f67cb87a0016b204cf1fe06`；样例嵌入本报告后清理临时文件。

```text
FakeBroker + MemoryChannel actual render, synthetic identifiers only
2026-10-06 synthetic trading session; no Gateway or network
pytest_exit=0 captured=14/14
missing=[]

### 🟠 P1 · 入场后保护单尚未确认
🟠 P1 · 入场后保护单尚未确认
发生了什么：AAPL P=1 S已确认=0 S可能=1 T=0 X=0 未保护U=待核实（可能范围 0–1） 超卖额=待核实（可能上界0）；当前保护状态待核实；orderRef=v6l-protect-d265d72ee3d7489f9e46 orderId=1002 permId=51002 TP=-；触发价=108.47 限价=105.21；上次确认=未获券商确认 工作单证据时间=2026-10-06T14:00:35+00:00；A gap 5.0s
影响：AAPL 未保护数量 待核实（可能范围 0–1） 股；STP LMT 触发后也可能不成交。
你要做什么：1）在 TWS 核对真实持仓、订单号、成交和保护价；2）不要另发该标的卖单，紧急人工卖出须先协调 Lite 卖单；3）由 John 决定人工处理并记录；4）恢复前重新对账。

### 🔴 P0 · 入场持仓未获券商保护
🔴 P0 · 入场持仓未获券商保护
发生了什么：AAPL P=1 S已确认=0 S可能=1 T=待核实 X=待核实 未保护U=待核实 超卖额=待核实；当前保护状态待核实；orderRef=v6l-protect-d265d72ee3d7489f9e46 orderId=1002 permId=51002 TP=-；触发价=108.47 限价=105.21；上次确认=未获券商确认 工作单证据时间=过期或缺失；A gap 15.0s
影响：AAPL 未保护数量 待核实 股；STP LMT 触发后也可能不成交。
你要做什么：1）在 TWS 核对真实持仓、订单号、成交和保护价；2）不要另发该标的卖单，紧急人工卖出须先协调 Lite 卖单；3）由 John 决定人工处理并记录；4）恢复前重新对账。

### 🔴 P0 · 止盈后保护缺口未恢复
🔴 P0 · 止盈后保护缺口未恢复
发生了什么：AAPL P=20 S已确认=14 S可能=14 T=待核实 X=待核实 未保护U=待核实 超卖额=待核实；当前保护状态待核实；orderRef=v6l-protect-8d7f5b06bfae4800a78b orderId=1002 permId=51002 TP=v6l-2999d50f04fb4b8384c8；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:01:12+00:00 工作单证据时间=过期或缺失；B gap 30.0s
影响：AAPL 未保护数量 待核实 股；STP LMT 触发后也可能不成交。
你要做什么：1）在 TWS 核对真实持仓、订单号、成交和保护价；2）不要另发该标的卖单，紧急人工卖出须先协调 Lite 卖单；3）由 John 决定人工处理并记录；4）恢复前重新对账。

### 🔴 P0 · 托管标的出现外部卖单
🔴 P0 · 托管标的出现外部卖单
发生了什么：AAPL P=20 S已确认=20 S可能=20 T=0 X=1 未保护U=0 超卖额=1；当前快照已核实；orderRef=v6l-protect-879f185faa514620b670 orderId=1002 permId=51002 TP=-；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:32+00:00 工作单证据时间=2026-10-06T14:00:32+00:00；X=1 orders=external-sell-1
影响：AAPL 未保护数量 0 股；STP LMT 触发后也可能不成交。
你要做什么：1）在 TWS 核对真实持仓、订单号、成交和保护价；2）不要另发该标的卖单，紧急人工卖出须先协调 Lite 卖单；3）由 John 决定人工处理并记录；4）恢复前重新对账。

### 🔴 P0 · 券商拒绝保护单
🔴 P0 · 券商拒绝保护单
发生了什么：AAPL P=20 S已确认=20 S可能=20 T=0 X=0 未保护U=待核实（可能范围 0–20） 超卖额=待核实（可能上界0）；当前保护状态待核实；orderRef=v6l-protect-01c911069a21493a8d4b orderId=1002 permId=51002 TP=-；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:01:02+00:00 工作单证据时间=2026-10-06T14:01:02+00:00；BrokerError
影响：AAPL 未保护数量 待核实（可能范围 0–20） 股；STP LMT 触发后也可能不成交。
你要做什么：1）在 TWS 核对真实持仓、订单号、成交和保护价；2）不要另发该标的卖单，紧急人工卖出须先协调 Lite 卖单；3）由 John 决定人工处理并记录；4）恢复前重新对账。

### 🔴 P0 · 保护单状态无法核实
🔴 P0 · 保护单状态无法核实
发生了什么：AAPL P=20 S已确认=20 S可能=20 T=0 X=0 未保护U=待核实（可能范围 0–20） 超卖额=待核实（可能上界0）；当前保护状态待核实；orderRef=v6l-protect-2710fefdb26f4d6ab1cc orderId=1002 permId=51002 TP=-；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；broker status=Cancelled; replacement requires three fresh sources
影响：AAPL 未保护数量 待核实（可能范围 0–20） 股；STP LMT 触发后也可能不成交。
你要做什么：1）在 TWS 核对真实持仓、订单号、成交和保护价；2）不要另发该标的卖单，紧急人工卖出须先协调 Lite 卖单；3）由 John 决定人工处理并记录；4）恢复前重新对账。

### 🔴 P0 · 止损触发但限价未成交
🔴 P0 · 止损触发但限价未成交
发生了什么：AAPL P=20 S已确认=20 S可能=20 T=0 X=0 未保护U=0 超卖额=0；当前快照已核实；orderRef=v6l-protect-f321f4793edf484ba063 orderId=1002 permId=51002 TP=-；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；triggered remaining=20; mark=104.21 below limit
影响：AAPL 未保护数量 0 股；STP LMT 触发后也可能不成交。
你要做什么：1）在 TWS 核对真实持仓、订单号、成交和保护价；2）不要另发该标的卖单，紧急人工卖出须先协调 Lite 卖单；3）由 John 决定人工处理并记录；4）恢复前重新对账。

### 🔴 P0 · 疑似触发或保护状态不明
🔴 P0 · 疑似触发或保护状态不明
发生了什么：AAPL P=20 S已确认=20 S可能=20 T=0 X=0 未保护U=0 超卖额=0；当前快照已核实；orderRef=v6l-protect-b6f1e7b44eb34abebb9a orderId=1002 permId=51002 TP=-；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:01:02+00:00 工作单证据时间=2026-10-06T14:01:02+00:00；mark=107.47 <= trigger=108.47; broker trigger/fill is not proven
影响：AAPL 未保护数量 0 股；STP LMT 触发后也可能不成交。
你要做什么：1）在 TWS 核对真实持仓、订单号、成交和保护价；2）不要另发该标的卖单，紧急人工卖出须先协调 Lite 卖单；3）由 John 决定人工处理并记录；4）恢复前重新对账。

### 🔴 P0 · 股价不符合保护单要求
🔴 P0 · 股价不符合保护单要求
发生了什么：AAPL P=0 S已确认=0 S可能=0 T=待核实 X=待核实 未保护U=待核实 超卖额=待核实；当前保护状态待核实；orderRef=- orderId=- permId=- TP=-；触发价=0.00 限价=0.00；上次确认=未获券商确认 工作单证据时间=过期或缺失；BUY withheld qty=1 limit=113.47 ATR=None; missing ATR or valid plan
影响：AAPL 未保护数量 待核实 股；STP LMT 触发后也可能不成交。
你要做什么：1）在 TWS 核对真实持仓、订单号、成交和保护价；2）不要另发该标的卖单，紧急人工卖出须先协调 Lite 卖单；3）由 John 决定人工处理并记录；4）恢复前重新对账。

### 🔴 P0 · 保护单三项证据不完整
🔴 P0 · 保护单三项证据不完整
发生了什么：AAPL P=20 S已确认=14 S可能=20 T=6 X=0 未保护U=待核实（可能范围 0–14） 超卖额=待核实（可能上界6）；当前保护状态待核实；orderRef=v6l-protect-d1ba16259eb14894992f orderId=1002 permId=51002 TP=v6l-3cc33caeaf6b49f88e84；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:01:12+00:00 工作单证据时间=2026-10-06T14:01:12+00:00；three-source query failed: BrokerError
影响：AAPL 未保护数量 待核实（可能范围 0–14） 股；STP LMT 触发后也可能不成交。
你要做什么：1）在 TWS 核对真实持仓、订单号、成交和保护价；2）不要另发该标的卖单，紧急人工卖出须先协调 Lite 卖单；3）由 John 决定人工处理并记录；4）恢复前重新对账。

### 🔴 P0 · 停机时保护单未核实
🔴 P0 · 停机时保护单未核实
发生了什么：AAPL P=20 S已确认=20 S可能=20 T=0 X=0 未保护U=待核实（可能范围 0–20） 超卖额=待核实（可能上界0）；当前保护状态待核实；orderRef=v6l-protect-97dc6e6afb934b1a9fe0 orderId=1002 permId=51002 TP=-；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；GTC identity or broker working status not confirmed at shutdown
影响：AAPL 未保护数量 待核实（可能范围 0–20） 股；STP LMT 触发后也可能不成交。
你要做什么：1）在 TWS 核对真实持仓、订单号、成交和保护价；2）不要另发该标的卖单，紧急人工卖出须先协调 Lite 卖单；3）由 John 决定人工处理并记录；4）恢复前重新对账。

### 🔴 P0 · 成交回调处理失败，券商端保护未核实
🔴 P0 · 成交回调处理失败，券商端保护未核实
发生了什么：AAPL orderRef=v6l-98345b74c52045df8462；成交回调本地处理失败 OSError；P/S/T/X/U=待核实，保护单 orderId=待核实。
影响：持仓可能没有券商端保护；停止新开仓。
你要做什么：1）在 TWS 核对真实持仓、成交和卖单；2）由 John 人工处置缺口；3）修复本地状态后重新对账。

### 🔴 P0 · 未认领订单禁止泛型撤单
🔴 P0 · 未认领订单禁止泛型撤单
发生了什么：AAPL orderRef=v6l-orphan-gtc orderId=1003 permId=51003 P/S/T/X/U=待核实；本地没有对应订单记录，泛型撤单已拒绝。
影响：可能是仍在券商端工作的 GTC 保护单。
你要做什么：1）在 TWS 核对订单号与持仓；2）保留未知 GTC；3）由 John 决定人工处置并重新对账。

### 🔴 P0 · 停机发现未认领券商保护单
🔴 P0 · 停机发现未认领券商保护单
发生了什么：AAPL orderRef=v6l-orphan-gtc orderId=1003 permId=51003 remaining=1；P/S/T/X/U=待核实；本地没有对应保护记录，停机保留该券商卖单。
影响：保护订单归属和股数需人工核实。
你要做什么：1）在 TWS 核对真实持仓与订单号；2）不要由 Lite 自动撤该单；3）由 John 决定处置并重新对账。

### 迟到卖出成交 · 🔴 P0 · 托管标的出现外部卖单
🔴 P0 · 托管标的出现外部卖单
发生了什么：AAPL P=19 S已确认=20 S可能=20 T=0 X=0 未保护U=0 超卖额=1；当前快照已核实；orderRef=v6l-protect-c600273699f24eb994eb orderId=1002 permId=51002 TP=-；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；late SELL P=19 S最多=20 T+X最多=0; only decrease
影响：AAPL 未保护数量 0 股；STP LMT 触发后也可能不成交。
你要做什么：1）在 TWS 核对真实持仓、订单号、成交和保护价；2）不要另发该标的卖单，紧急人工卖出须先协调 Lite 卖单；3）由 John 决定人工处理并记录；4）恢复前重新对账。

### 每日健康报告 · 券商端保护
⚠️ 需关注：Gateway 断线
扫描：计划：10:00 ET 信号 1 · 已入场 AAPL
信号：10:00 ET AAPL(78)
结果：gate=未持久化（正式订单的具体门控结果请查本机完整日志）
下单/撤单：2 次下单 · 0 次撤单
持仓与漂移：AAPL 20 · 漂移：无
券商端保护：AAPL 截至 2026-10-06T14:00:31+00:00 最近一次券商快照确认，当前仍需核实，本地状态=WORKING P=20 S=20 触发=108.47 限价=105.21 orderId=1002；当日最大空窗 A=0.700s B=1.250s
Gateway 心跳：本进程今日截至报告时: 应答 1 · 最长已验证延迟 0 秒 · 未应答 0
告警送达：本进程今日截至报告时: 发出 1 · 失败 0
行情可用率：100% 常规交易时段可用率 · 已订阅 1 · 1/1 标的样本 · 1 次采样 (本进程今日；实时行情)
code_sha：synthetic
```

## SHA-256 清单

本清单为全部 `v6lite/` 非 `__pycache__` 文件及四份关联文档，共 65 项。包含测试和示例配置模板；不包含报告自身，避免自引用。实际运行配置、状态库、`.env` 和机密均不在清单内。新 `code_sha=ed27369e65322a9b`，其算法只对 `v6lite/` 文件按路径和内容取指纹；文档不进入 `code_sha`。

```text
0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1  v6lite/clock.py
12a3ed033079f129e7e70f610be004c498ea6f6f0283473f60b2eb80ad819140  v6lite/tests/test_batch13.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
17b8628794cf5cf5e592be52009a9dbf07ff369673074d69c36b4a91103f2cec  v6lite/README.md
1d454ac85558ba7712e73e2bcce3dfb243789b54b4f1af2e68508721f746b26a  v6lite/config.py
1de7eb5637ee3c6abd1784570846d0edf8f91739ee97e2bb6e0b6651e34de093  v6lite/exits.py
21d218304e6b76b03348ba01033705d412bbbb4d3cc62263c6ed2ed643452ccd  v6lite/tests/test_review_round5_selfreview.py
25937bff88ac9c7aeeb5b354d2edb6fc187f4a9ce2e1736a1596537d4a3f2506  v6lite/models.py
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
273025c15d0fc8a05f0fab703e1060028a2f7e8f717b184e6535c6d20a19c1c2  v6lite/examples/live.toml
2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24  v6lite/broker/__init__.py
2bc9aa068ed6978ad35c07f59779f7ce20089df393c6be23f398abfbb3d16716  v6lite/examples/paper_delayed.toml
3de59ef71678bf534a66d48f4a3e4b5f49b7bac5a391f1a051cf2e0e648474e5  v6lite/protection.py
3dfc69a4f811549cb32c9c186bbfed3d24f7c1c956c3c743d9b28f6996037cfc  v6lite/tests/test_batch12.py
3e98f52cdb35e572995448fc6e3468cf1d86e2d35ac801a33f8c5cacc67e9680  v6lite/tests/test_review_round5.py
4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1  v6lite/reconcile.py
4e8057677d8f1b4eef15578ad303476a183bda652859b16ca7558c46a789e1e3  v6lite/examples/paper_test.toml
5087c53709f4208d5dc4fbe9f74b100d50ebfb2060ccf9c9834358d2c3c7b257  v6lite/tests/test_batch14.py
5417df51fe8325b59d2633e26352eb7fcf9d501a1b37fc88815105727019c125  v6lite/__main__.py
54f1ef38edf97bc38488d4f446e66c0b22dd8229f2670df96509dd80d9eb8c42  v6lite/tests/test_batch15.py
5691a234501cd71b85e3098e0cf6da2a74f2d4f5ca3eda2797a8abe17fab9af0  v6lite/tests/test_batch12_v2.py
5b875c8df97861e22427223e7065a74bddf04a5da1c7115b431871369d177c47  v6lite/tests/test_batch10.py
69c12e2d92887ae4b54066876be8f51cffdb3d06ad37f69b26a58319af20d928  v6lite/broker/base.py
6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a  v6lite/tests/test_ibkr_adapter.py
6c628d0761fc181c89f9673de64aeb296459c7acabdf462af01bf16ad295c492  v6lite/store.py
732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f  v6lite/tests/test_strategy_parity.py
75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42  v6lite/tests/test_units.py
7ab32142a9a0ee2fdb54853297cf6eae619ce28de4cac4dc646a8a8035660822  v6lite/tests/test_review_round2.py
7d9000d6e3f1bc454edfec13b1dfdf5d08ac9f727be8e60b3b5a6ed4775003ed  docs/V6_LITE_L7_DESIGN.md
8cf2bba5d54e2e644bc016e68abfe87f0ecbe26b6e54d5a44a329c61e6dc7da8  v6lite/tests/test_review_round4_selfreview.py
8e0a5e4861b0254f5549a2bc13e8c2d1c176dd0ce0791bfa1a64f79072ca12db  v6lite/protection_engine.py
91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1  v6lite/tools/__init__.py
9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e  v6lite/strategy.py
9aa52b23a3c8c08801f9d86c46ae1090a28c78dbde5d5b4b2923347bc74ece45  v6lite/version.py
a0db6b981992a3cf20d9f959a679c608842f4619007331bae9b26908cee3ccb7  v6lite/tests/test_g2_day2_prep.py
a3589fae5996245f7246f1860875a847b6761ed9f8bab5a317ab5c7d9e5e86bf  v6lite/tests/test_review_round7_selfreview.py
a5377a4f520e6c3d340cb40571f24eae1dd431be871ecfad241b0d0080424aef  v6lite/tests/test_soak.py
aa3c59a3c8fc06e576ae23e66b7a5bbb9b6e26a805b7851001d419eaa96e7bc1  v6lite/engine.py
abc521f671e878a0ab5690e1fa178dc872d29b5396b86eaeb5f016be1594c687  v6lite/risk.py
ad2ac3f67724896e2b21e83e595a10b7984630fd2a77f8df44aa859569cb945b  v6lite/approval.py
aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb  v6lite/tests/conftest.py
b5bf4e5c513e9d2310dc6404f147b633790cff0e894546e9176d6c26390a7427  v6lite/tests/test_review_round3.py
b6f779f7888717dd95948597d952b7757e8c580efb25f9067f41e1b1948394b1  v6lite/broker/fake.py
ba38215d7a6946d60884d0e3dac99b641e7bcde058c4893b94cb9d62b6a6fe18  v6lite/tests/test_g2_probe.py
bb8967a536a8d187fc9f1d742be17c0b4b90e193ab52731ac06d6bff8a08f461  v6lite/tests/test_engine.py
bcb2baea638b6ba6c063aba7776aaf1137c3cd1df98297a08311cdaf899994a5  v6lite/tests/test_review_round7.py
bdcc2b0e7c008e17594bb1cb30887af9975781d25aea7ae2d900f71a7319d143  v6lite/tests/helpers.py
c05c4dfc7f694b3c3bc500e1453d93ad6c52137e821a82eaefc5af66676eb342  docs/V6_LITE_G3_RUNBOOK_DRAFT.md
c65ac1c396468aae3124ffcdc0f61b2ed483547ec1b0f47119f00950008b3736  v6lite/tests/test_review_round8.py
c6eabe6e953cc75ccfa7e4fa87625a86d677ef5dd2fca9975cd101b3403d6be5  v6lite/tests/test_review_round6.py
c9b9cc4a116a7f61d9e660dd885d280ec2a9262671f2c3c8ab5c1515724e171e  v6lite/sizing.py
cacaf5b3115d4a298fe8b3fe065d66fe70277b8ee4cd5d9643f0e2fff0ff5f9d  v6lite/tests/test_g2_day1.py
ceeab67239691f6c71c56f4482c372b48e2f871b2c0843174e42b8e8a373af04  v6lite/report.py
d3dbcd1258571afb17579f0ef9641e0890e55e67be82c5133c413a436504d0ca  v6lite/broker/ibkr.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  v6lite/tests/__init__.py
e756a719f9f8c1cded3d1123f67fa0097b504680589319be40619bc5d7f6763e  v6lite/tools/g2_probe.py
e927860575cb55769a910a0dfbced96b725922da6629010c894ec2d3c86b36c3  v6lite/tests/test_review_round8_selfreview.py
eb91ef957bfd13864c2c92a5da2cf3a29ffdc10c9f5c74fe751daa8f4caecdd3  v6lite/tests/test_review_fixes.py
ee5552b23fcfa8a7438e4f1c5615fac38e6cf90bc84b863032e59b1654b3ff05  v6lite/alerts.py
f0feb98e4c121618d049a5fbdfa4e0949bf8f1329b22383f8519a09f16b87c83  docs/V6_LITE_L7_RULING.md
f17f50380fd11f5ccf2e3ae250f8910928406a75d41eda87a9e8b0cfa24d16dd  v6lite/tests/test_review_round4.py
f721046830417e95804e7bff135f0c5b9ea59e70a96418f10cf642865f7e7370  v6lite/examples/paper.toml
fb7f26a0bd1969400f2d769a0d65f4c7d3a18f0162332fd46ca9f1ed99c260f8  v6lite/tests/test_batch11.py
fe84ef645d99f421c64f670aa7f883d16b5a24c8c15b4dd30a4aa8f035b5ea06  v6lite/tests/test_g2_day1_selfreview.py
fe8874c9fab3d93924ba16e83c4d0ece950e97956f4958ae7d19709e45855a23  docs/V6_LITE_G2_RUNBOOK.md
```

