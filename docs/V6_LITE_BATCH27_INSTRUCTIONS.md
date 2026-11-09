# 第二十七批指令（Claude → Codex）：审计、日报、日志的数据量

日期：2026-10-16。工作区 `C:\Dev\V6Lite-dev`，dev 分支，基于 `v6lite-batch26`（code_sha `579d13832e3f4237`，运行 tag `v6lite-run-b26`）。

分工照旧：调查和测试设计可以并行；生产代码只由一个实施 Agent 修改；最终验证串行执行。

**先做第 5 节。** 10/16 实测发现：过夜持仓因为成交时间标记，永远通过不了核验。这部分修复单独交付为 27a，目标是周末合并。

**合并时机：** 由 Claude 和 John 决定。

**第 1–3 节不改变任何交易、保护、核验或卡片行为。** 只减少重复写入，以及改变日报和日志的呈现方式。

## 0 10/15 事实

证据：`docs/batch27_evidence/g3t_20261015_volume.md`。这是一份统计摘要，不含账户号。

10/15 这次运行从 04:16 PT 持续到 17:26 PT，约 13.2 小时，代码为 b25，只有 1 个持仓，没有任何交易。

| 项目 | 数量 |
|---|---|
| audit 表新增 | 92,761 行，payload 合计 19.5 MB |
| 其中 `ORDER_PRICE_OBSERVATION` | 46,531 行，**payload 只有 1 种**（全部相同） |
| 其中 `PROTECT_STATE` | 46,147 行，**彼此只有 `confirmed_at` 不同** |
| 其他类型合计 | 83 行 |
| 日报 `reports/2026-10-15.md` | 31,155 行，6.17 MB |
| 日报 Events 段 | 31,130 行，其中 31,124 行是 `ORDER_PRICE_OBSERVATION`，有效内容只有 6 行（5 条 TP_EVIDENCE、1 条 SCAN） |
| 日志 `v6lite_20261015.log` | 140,464 行，20.3 MB |

日志中三类行各约 4.65 万行：

- `[QUERY] channel=openOrders`
- `[QUERY] channel=positions`
- `ib_insync.wrapper position: Position(account=...)`。这一类还把完整账户号写进了日志。

b26 已经把稳态查询间隔从 1 秒改回 15 秒，单持仓的量会降到大约 1/15。但 G3-N 有 10 个标的，加上退避和多日运行，问题仍然存在。所以根因必须在本批修掉，不能只依赖查询间隔。

## 1 调查（先交调查结论，再动生产代码）

已经定位到的写入点（以你的核实为准）：

- `store.py` 的 `update_protection()`：每次更新 protections 行都会写一条 `PROTECT_STATE`。核验成功时只刷新 `confirmed_at`，也会写一条（见 `protection_engine.py` 中 `update_protection(now, …, confirmed_at=now)` 的调用）。
- `tp_evidence.py` 的 `ORDER_PRICE_OBSERVATION`：每次 openOrders 观察都会写一条，不管内容有没有变。
- `report.py` 的 `daily_report()`：把 `ORDER_PRICE_OBSERVATION` 放进 Events 的类型列表，逐条输出。
- `broker/ibkr.py`：每次查询都输出一条 `[QUERY]` INFO 日志。ib_insync 的 wrapper logger 在 INFO 级别输出每一条 position 和 portfolio 回调。

需要回答的问题：

1. **哪些代码读取这两类 audit 行。** 包括报告、恢复、A/B 空窗、TP 证据、过夜证据 a–d、`clear-anomaly`、`release`、测试辅助。逐个列出文件和行号，并附搜索方法。
   - 如果有安全判断依赖“audit 里最近一条 `PROTECT_STATE`/`ORDER_PRICE_OBSERVATION`”，说明它用的是什么字段，以及改为读取 protections/intents 状态行以后是否完全等价。
2. **全部 audit kind 的频率。** 列出每一种 kind 的写入点和触发条件，按“状态变化”和“重复观察”两类分组。除了上面两类，还有没有其他会随查询次数线性增长的 kind（例如退避期间、多持仓、过夜冻结期间）。
3. **日志各来源的稳态行数。** 按 1 个持仓和 10 个持仓分别给出每小时的行数估算。

## 2 实施要求

### 2.1 审计去重

规则：**状态变化必须写；内容不变的重复观察要合并。**

- **`PROTECT_STATE`：**
  - 只刷新 `confirmed_at`、其他字段都没变时，不写 audit 行。`confirmed_at` 仍然照常写进 protections 行。
  - 任何其他字段变化时照常写，例如状态、数量、止损价、orderId/permId、空窗时间、tp_stage。写入内容与 b26 完全相同。
- **`ORDER_PRICE_OBSERVATION`：** 按 intent_id 记住上一条 payload（不含时间）。payload 中任何字段变化，或者出现以下情况时，立即写一条：
  - 本进程第一次观察到这个 intent；
  - 新的连接代；
  - error_code 或 reason 不为空。
  
  内容相同就只计数。
- **合并计数：** 被合并的重复观察，每 30 分钟最多写一条汇总行，以及在停机和连接代切换时各写一条。汇总行的 kind 定为 `OBSERVATION_SUMMARY`，payload 包含：原 kind、intent_id/position_id、次数、首次和末次时间。
- **不得合并的类型：**
  - 订单发送、修改、撤销；
  - 成交和佣金；
  - 状态迁移、ANOMALY、P0/P1/INFO 卡片对应的事件、STAGE_*、TP_EVIDENCE；
  - STARTUP/SHUTDOWN、KILL/RESUME、release/freeze；
  - IBKR_NOTICE 中的错误码。

  如果调查发现其中某一种也会高频重复，先在报告里列出来，由 Claude 裁决，不要自行合并。
- **不改历史数据：** 不删除、不改写、不迁移已有的 audit 行。不自动 VACUUM。这一条对应运行约束 B（账本不回滚）。

### 2.2 日报 Events 段

- **新结构：**
  1. 先放一张按 kind 统计的汇总表，包含次数、首次时间、末次时间；
  2. 再逐条列出重要事件：上面“不得合并”的类型，加上 PROTECT_GAP、TP_EVIDENCE、SCAN；
  3. 连续相同的行合并成一行，并标注 “×N”；
  4. `ORDER_PRICE_OBSERVATION`、`OBSERVATION_SUMMARY` 只进汇总表。
- **上限：** Events 段不超过 200 行，整份日报不超过 100 KB。超出时截断，并写明“另有 N 条，完整记录在状态库 audit 表”。P0、ANOMALY、成交不能被截断，优先保留。
- **历史日期：** 用旧的大体量数据（例如 10/15）重新生成日报时，也必须在上限以内。读取时用聚合查询，不要把全部行读进内存后再过滤。
- **账户号脱敏：** 日报表头的账户号只显示末 3 位，例如 `DU******256`。配置、状态库和启动校验仍然使用完整值。

### 2.3 日志

- `[QUERY]` 正常完成时降为 DEBUG。每 30 分钟输出一条 INFO 汇总，包含每个通道的次数、最大耗时、drained=True 的次数。查询失败、超时、退避和限速仍然是 WARNING，逐条输出。
- ib_insync 的 `wrapper` logger 设为 WARNING。position、portfolio 等回调不再进入日志文件。ib_insync 的错误和警告保留。
- **日志中不得出现完整账户号。** 启动行等位置只显示末 3 位。
- **上限：**
  - 1 个持仓稳态 13 小时：日志不超过 2,000 行。
  - 10 个持仓稳态 13 小时：日志不超过 10,000 行。

  不含 WARNING 及以上级别。

### 2.4 不放宽的地方

- 核验频率、退避、证据 a–d、`S+T+X ≤ P`、强身份门槛、卡片的种类和次数，都与 b26 完全相同。
- 去重和汇总只影响写进 audit、日报和日志的内容，不能影响内存中的证据和判断。
- 去重状态只保存在内存里。重启以后，第一次观察一定会写入。

## 3 测试

1. **数据量上限：** 模拟稳态 13 小时：openOrders 和 positions 每 15 秒查询一次，另跑一个 1 秒的变体，模拟最坏的退避情况。断言：

   | 场景 | `ORDER_PRICE_OBSERVATION` | `PROTECT_STATE` | `OBSERVATION_SUMMARY` |
   |---|---|---|---|
   | 1 个持仓 | ≤ 5 行 | ≤ 5 行 | ≤ 30 行 |
   | 10 个持仓 | 按持仓数线性增长 | 按持仓数线性增长 | 按持仓数线性增长 |

   另外断言：日报 Events 段 ≤ 200 行、日报 ≤ 100 KB，日志符合第 2.3 节的上限。
2. **变化必写：**
   - 对 `ORDER_PRICE_OBSERVATION` payload 的每一个字段逐一做变化，断言当次立即写入；
   - 对 protections 除 `confirmed_at` 以外的每一个字段做同样的断言；
   - 断言 error_code 或 reason 不为空时逐条写入。
3. **10/15 回放：** 用 10/15 的真实规模构造数据（1 个持仓，openOrders 每秒 1 次，持续 13 小时，payload 与证据文件中的样例相同）。断言：
   - audit 行数下降到第 1 条的上限以内；
   - 日报符合上限，并保留 5 条 TP_EVIDENCE 和 1 条 SCAN；
   - 所有卡片、核验结果与不去重时逐条一致。
4. **旧数据日报：** 用一个预先灌了 9 万行旧格式 audit 的状态库生成日报，断言符合上限、耗时不超过 5 秒、内存不随行数线性增长。
5. **回归：** 以下测试全部保持通过：
   - b22–b26 的全部回放，包括 10/12 回传限价、10/13 止损成交、10/14 重启接管、10/15 过夜冻结和 constraint 模式；
   - 过夜证据 a–d 的正向和负向测试；
   - 停机补发日报的测试。
6. **红绿要求：** 第 1、3、4 条先在 `v6lite-batch26` 上失败，再修复。

## 4 交付

- 报告 `docs\V6_LITE_FIX_REPORT_ROUND27_2026-10-XX.md`，内容包括：
  - 调查结论：读取点清单、kind 频率表、日志来源估算；
  - 改动对照表；
  - 红绿测试结果；
  - Windows 全量连续 3 次的结果（交易日 13:30 PT 之后跑）；
  - 用 10/15 回放数据生成的日报样例（只放前 60 行）；
  - SHA-256 清单。
- 在 dev 提交并打 tag `v6lite-batch27`，交付后冻结，等待 Claude 复核。

## 5 【最高优先级，先于第 1–3 节】成交时间标记让过夜止盈永远核验不通过

**10/16 事实：** 04:21 PT 启动 b26（`overnight_tp = "constraint"`）。接管 orderId 501 正常，没有重连。04:21:56 发出 P1“过夜持仓止盈核验未通过”，原因是“账本成交时间尚未核实”。John 选择今天维持现状：只保留 STP，不入场。

**根因（Claude 读取 10/15 收盘时的状态库）：**

- 10/13 的三笔成交都带有 `execution_time_unverified:<execId>` 控制项，`source=callback`，到现在都没有清除：
  - `0000e0d5.6ace5084.01.01`：BUY，orderId 93；
  - `0000dc8f.6be1846c.01.01`：SELL STP，orderId 94；
  - `0000e0d5.6ace9d9f.01.01`：BUY，orderId 500，provisional `2026-10-13T19:12:43.329461Z`，属于当前持仓 `pos-99ec110ffc6246e8b7c4`。
- 状态库里**没有任何一条** `EXECUTION_TIME_VERIFIED` 或 `EXECUTION_TIME_REVIEW_REQUIRED`。也就是说，`store._verify_execution_time()` 在真实运行中从来没有生效过。
- `overnight_evidence._overnight_history()` 只要看到这个控制项就抛出“账本成交时间尚未核实”。过日以后，`reqExecutions` 再也查不到这笔成交，标记无法清除，所以这笔持仓在 b26 下**永远**通过不了 a。
- b26 的正向测试没有覆盖“成交来自 callback、带未核实标记”的情况。请在报告中说明为什么没覆盖到。

### 5.1 调查

1. 结合 ib_insync 0.9.86 的源码（`wrapper.execDetails` 对实时回报和查询回报分别使用哪个时间），说明 callback 成交为什么被标记为时间未核实。附源码位置。
2. 说明 10/13 当天为什么三笔成交都没有被核实：
   - 成交之后有没有发出完整的 `executions_today()`；
   - 查询结果有没有进入 `record_fill()`，还是因为 execId 已知而在更早的地方被过滤掉；
   - 身份比对（`_fill_time_identity`）和 provisional 时间比对能不能匹配。
   
   用 10/13 的真实字段构造回放来证明结论。

### 5.2 修复：当天核实必须真的发生

- callback 成交入账之后，在查询限速允许的范围内，尽快发出一次完整的当天成交查询，用查询结果核实时间。核实成功写 `EXECUTION_TIME_VERIFIED`，失败写 `EXECUTION_TIME_REVIEW_REQUIRED`。这两条逻辑与现有代码一致。
- 当天收盘前仍未核实的成交，在日报里列出。
- 不改变成交的记账：数量、价格、盈亏、持仓都不动。

### 5.3 裁定：已过日、无法再核实时间的历史成交

这是 Claude 的裁定，John 可以在合并前否决。

在过夜证据 a 里，带有 `execution_time_unverified` 标记的**历史**成交，同时满足下面全部条件时，视为“时间足以证明属于此前交易日”：

1. 控制项能正常解析，`identity` 与 fills 行逐字段一致：execId、intent、标的、方向、数量、价格、orderId、permId、conId；
2. 控制项里的 `provisional_time` 与 fills 行的 `time` 完全相同；
3. `provisional_time` 早于**今天 00:00 ET 前 1 小时**。

**理由：** provisional 时间是收到回报的时间，实际成交只能更早。所以收到时间在今天开始之前，就证明这笔成交不属于今天。留出 1 小时，是为了覆盖本机时钟误差。

**边界：**

- 只读账本：不清除标记，不改写 time 或 trading_day，不写入任何“已核实”记录；
- 不满足任何一条，就保持 b26 的行为：P1 加冻结；
- INFO 卡片和日报里写明“入场成交时间按收到回报时间判定为 <日期> 之前（未经券商查询核实）”；
- 其余 b–d 以及运行约束 A、B 的说明不变。

**期望效果：** `0000e0d5.6ace9d9f.01.01`（provisional 为 10/13 12:12 PT）应当通过 a。这笔持仓已在 10/16 止损关闭，所以改用第 5.5 节第 1 条的回放来验证。

### 5.4 卡片和启动小问题

1. a 没通过时，卡片上“当日可见成交=未完成 条，未知项=未完成核验”容易让人误解。改为“未执行（a 未通过）”。
2. 10/16 04:21 PT 的启动卡片显示 `daily_loss_baseline_unknown:prior_close:AAPL`，10/15 同一时段启动时没有出现。请调查：
   - 盘前取不到前收盘价的原因；
   - 会不会自动恢复，什么时候恢复；
   - 对入场和风控有什么影响。
   
   如果是盘前启动的正常现象，就在卡片上写明会在什么时候自动恢复，并且不再标为“需关注”。

### 5.5 测试

1. **真实回放（正向）：** 构造 10/13 的 fills 行和控制项（字段与上面完全相同），在 10/19 重启，持仓 P=3、STP S=3。断言：
   - a 通过；
   - 发 INFO 卡片，写明按收到时间判定；
   - 到 TP1 价时按 b23 流程执行，全过程 `S+T+X ≤ P`；
   - 标记和 fills 行都没有被改写。
2. **负向，每种各一个：**
   - provisional 时间在今天 00:00 ET 前 1 小时以内；
   - provisional 时间在今天；
   - identity 有一个字段不一致；
   - provisional 时间与 fills 行的 time 不同；
   - 控制项无法解析。
   
   断言：都是 P1 加冻结，止损保护不受影响。
3. **当天核实：** callback 成交之后，下一次完整查询返回同一笔成交（真实时间比收到时间早），断言标记被清除，写入 `EXECUTION_TIME_VERIFIED`。另加一个查询结果不匹配的变体。
4. **夹具规则：** 以后所有过夜止盈的正向测试，都要同时覆盖“时间已核实”和“callback 未核实”两种成交。
5. **红绿要求：** 第 1、3 条先在 `v6lite-batch26` 上失败，再修复。

### 5.6 10/16 盘中新发现（一并纳入 27a）

10/16 11:55:44 PT，AAPL 跌到止损价，GTC STP orderId 501 成交 3 股 @340.50（止损价 340.52，滑点 $0.06，0.59 bps）。持仓正常关闭；15:13 PT 停机，`unresolved=[]`。以下问题都来自这一天的状态库和日志：

1. **止损成交时误发 P0。** 11:55:44.943 发出 P0“protection evidence incomplete”，内容是“持仓数量=0 股，保护单已确认=3 股……三项证据查询失败：BrokerError”。时间顺序如下：

   | 时间 | 事件 |
   |---|---|
   | .153 | 工作单回报 Submitted（STP 已触发） |
   | .666 | 成交入账，持仓 CLOSED |
   | .940 | `PROTECT_VERIFICATION_BACKOFF` failures=1 |
   | .943 | 直接发出 P0 |

   请调查并修复：
   - BrokerError 的具体原因（是否是成交和订单状态回报恰好落在查询中间，导致 End 计数不一致）；
   - 为什么第 1 次失败就直接发 P0。怀疑原因是控制项 `protect_incident:pos-99ec110ffc6246e8b7c4:evidence incomplete` 从 10/14 起一直保留（`first_at=2026-10-14T13:20:54Z`，`p0_sent=true`），新的失败沿用了旧事件的计时；
   - 要求：
     - 证据恢复完整，或者持仓已由确认的保护成交关闭（P=0、账本=0、intent FILLED）之后，旧事件必须结束，新的失败重新开始计时；
     - 持仓刚由保护单成交关闭时，一次查询失败不发 P0，只写审计；
     - 用这串回调的真实顺序做回放测试，先在 b26 上失败，再修复。
2. **成交时间仍未核实。** 这笔止损成交 `0000e0d5.6ad25378.01.01` 同样被标为 `source=callback`、时间未核实。成交之后引擎又运行了 3 个多小时，标记仍然没有清除。这是第 5.1 节的直接证据。
3. **持仓期间的查询频率。** 有持仓的时段，openOrders 每 2 秒查询一次（每分钟 30 次，04:21–11:55 PT 共约 1.35 万次）。持仓关闭之后才降到每 5 分钟 1 次。这与 b26“稳态改回 15 秒”的说法不一致。另外，`[QUERY] secs=0.000` 是否表示根本没有向券商发出请求？请说明这两点，并修正。
4. **冻结期间仍在开始和放弃止盈阶段。** `STAGE_BEGIN` 和 `STAGE_ABANDONED` 各 22 次，`EXIT_TRIGGER` 日志 20 多次。过夜止盈已经冻结时，不应该再开始止盈阶段；每个持仓每天最多记录一次“已到止盈价但冻结”。
5. **净盈亏估算。** 卡片显示“net P&L -9.09（1 笔佣金未回报，估算）”。实际为毛亏 -6.09，加上佣金 1.00 + 1.02，净亏 -8.11，估算值多扣了约 1 美元。请说明估算方法，改为按已知的同类佣金估算，或者明确写“佣金未计”。

### 5.7 交付顺序

第 5 节可以单独交付：在 dev 提交并打 tag `v6lite-batch27a`，附报告 `docs\V6_LITE_FIX_REPORT_ROUND27A_2026-10-XX.md`。目标是周末（10/17–10/18）复核并合并，10/19 周一重测过夜止盈。第 1–3 节在 27a 之后继续，最后打 `v6lite-batch27`。

## 6 约束

- 交易日 06:00–13:30 PT 只跑聚焦测试；
- 不连接 TWS 或 Gateway；
- 不碰 `%USERPROFILE%\.v6lite`、运行仓库和 John 的配置；
- 不 push；不输出任何机密；证据和报告中的账户号一律脱敏；
- 操作手册由 Claude 更新，Codex 不改。
