# V6-Lite 第三轮 Windows 独立复核

日期：2026-09-30 PT。复核者：Codex。对象：`V6_LITE_FIX_REPORT_ROUND3_2026-09-30.md` 及清单内 37 个文件。

**37/37 哈希匹配；Windows 全量 190 passed，第三轮专项 29 passed。G3 仍为 NO-GO，Live 仍须完成并验收 L7。** 第 4 节 resync 的充分性论证被反证；Q7 在默认 30 秒下修复了上一轮累计等待的反例，但其配置上界、退避和发送前决策还不能按完整声明验收。报告第 3 节 8 行中，2 行 `CLOSED_WITH_EVIDENCE`、6 行 `OPEN_PRODUCT_DEFECT`；这按整行范围裁定，不抹掉其中已修复的局部能力。

本轮按只读代码复核执行，结论作为 docs-only 产物保存。所有新增探针仅使用 FakeBroker、FakeIB、合成时钟、临时 SQLite 和临时编译缓存。没有连接 Gateway、操作真实运行 state、读取 `.env`、签发批准、stage、commit 或 push；仅新增本复核文件，交付代码、原报告、既有 Codex 复核及开发计划不变。以下建议尚未实施。

## 1 交付身份及 Windows 验证

| 项目 | 独立结果 |
|---|---|
| 第 8 节 manifest | 37 项全部匹配；缺失、哈希差异、排除缓存后的清单外 Lite 文件均为 0 |
| 环境 | `Windows-10-10.0.26300-SP0`，仓库 venv Python 3.11.9 |
| Lite 全量 | **190 passed in 38.62s**，无失败、无跳过 |
| 第三轮专项 | **29 passed in 2.68s**，无失败、无跳过，包含于上述 190 项中 |
| 缓存 | 每次运行采用新的临时 `PYTHONPYCACHEPREFIX`，不依赖旧 `__pycache__` |
| 范围 | Lite 测试含 Windows 锁、策略比较、soak 及 G2 工具离线测试；未启动 G2，也未运行旧 V6 全量 |

本次实际复跑的命令：

```powershell
cd C:\Dev\TradingV6
. .\venv_v6\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
$env:PYTHONPYCACHEPREFIX = Join-Path ([System.IO.Path]::GetTempPath()) ('v6lite-round3-review-' + [guid]::NewGuid().ToString('N'))
python -m pytest v6lite/tests -q -ra
python -m pytest v6lite/tests/test_review_round3.py -q -ra
```

关键文档 SHA-256（复核前后保持一致）：

| 文件 | SHA-256 |
|---|---|
| 第三轮整改报告 | `578497f9d813e1f65dcdd047719d20f916d9bda39f5de6351743987d46a6af65` |
| 第二轮 Codex 复核 | `e3491a53d55f52f855c5ce0d17c00815188f883df38d7469ab447853d33b8884` |
| 既有开发计划 `V6_LITE_REDESIGN.md` | `203bad0cf719cbe16ef6485a38c4bc729945e20a230cbea687b84c01df849e02` |
| G2 runbook | `3afc04b421d3a365633aa0a021a05a1755d6660a952609b4eef9f7699e74b229` |

本次没有独立重建整改前版本；报告的 `28 failed / 1 passed` 和整改方三次全量结果属于其历史证据，不冒充本次运行。删除 8 个旧功能测试属于范围变化，不能由 190 项通过推出所有旧状态迁移也兼容；迁移反例见 F6。

## 2 resync 前提的裁定

**“没有 working 订单”不足以证明 resync 可以永久忽略旧订单的全部后续 execution。** 必须分开三个命题：旧订单以后是否还会发生新撮合；已经发生的成交是否全部返回；所读取的持仓是否已经包含这些成交。即使第一个命题为真，后两个也不随之成立。

[IBKR 的 reqAllOpenOrders 说明](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/requesting-currently-active-orders/all-submitted-orders)定义的是当前订单的一次返回，不建立后续订阅。本项目 [ibkr.py:174](C:/Dev/TradingV6/v6lite/broker/ibkr.py:174) 的 positions 调用本机 [ib_insync/ib.py:429](C:/Dev/TradingV6/venv_v6/Lib/site-packages/ib_insync/ib.py:429)，后者直接返回 wrapper 的持仓缓存。重新读取这份缓存没有取得新的同步完成标志或与 execution 对齐的代次。由这两项实现和接口事实，不能推出订单、成交、持仓是同一时点的一致快照。

F2 的主反例中，**旧 SELL 实际发生于 resync 之前**，因此不需要假设已经真正终结的订单会再次撮合。问题是旧成交尚未进入所采用的持仓快照；其回报到达后又因 superseded_by 被忽略，连风险处置一起跳过。按订单归属防止重复入账可以保留，但不能把“不入账”变成“不使证据失效、不复核当前卖单”。

## 3 新反例：发现 → 建议改动 → 测试

以下为 190 项之外的独立探针。它们检验程序在明确注入的顺序、故障和数据下的行为，不代表真实 Gateway 已出现同样时序。P1 阻塞相应 G3 安全验收；P2 应在相关功能关闭前处理或明确收窄其保证。没有发现已证实的 P0。

### F1 / P1：fresh 查询失败，仍用 31 秒前空列表发 SELL

**发现：** [engine.py:1191](C:/Dev/TradingV6/v6lite/engine.py:1191) 的 fresh open-orders 查询异常只写日志；只要旧缓存未超过 60 秒，就继续计算容量。它没有满足“当前取证失败就不发 STOP”的声明。这个窗口不需要撤单成交竞态。

**建议改动：** 发送所依赖的 fresh 查询失败时，本次发送失败关闭，并使相关缓存失效。若将来要支持缓存降级，须单独定义并证明账户内其他订单变化受控，不能仅凭缓存年龄代替本次取证成功。

**测试：** 原持有 20 股，成功查询得到空列表；随后出现外来 SELL 20，当前查询设为超时。31 秒后跌破止损，系统仍另发 STOP 20。两单完整回放后 **broker=−20**。实际输出：`cache_age=31.0, failed_fresh_query=true, new_stops=[[20,"STOP"]], broker_after_all_sells=-20.0`。原专项只覆盖缓存已超过 5 分钟的失败路径。

### F2 / P1：resync 使用滞后持仓，隔离旧成交后没有撤当前过量 SELL

**发现：** [engine.py:854](C:/Dev/TradingV6/v6lite/engine.py:854) 的订单、成交和持仓分别取证；[store.py:515](C:/Dev/TradingV6/v6lite/store.py:515) 将 superseded execution 隔离并返回 inserted=False；[engine.py:348](C:/Dev/TradingV6/v6lite/engine.py:348) 随即返回，不触发立即对账或迟到 SELL 保护。现有 [engine.py:378](C:/Dev/TradingV6/v6lite/engine.py:378) 还按旧 position_id 查待撤订单，即便恢复调用，也不足以定位 resync 新仓的卖单。

**建议改动：** 取证应证明持仓已包含相关成交，证据不足则不结束恢复状态。superseded execution 可以不重复记账，但必须触发当前 symbol/持仓代次的风险复核、证据失效及过量 SELL 处置。通过显式 lineage 关联新旧持仓，不能只查旧 position_id。

**测试主序列：** 旧 STOP 已终结，SELL 10 已发生，但执行查询暂不返回它，position 流仍缓存 20；无 working 单，操作请求 N=20 与缓存一致，resync 被接受。系统随后新发 STOP 20。旧 execution 与 position=10 到达后：账本仍 20、新 STOP 未请求撤销、`reconcile_ok=True`、anomalies 为空。新 STOP 完整成交后 **broker=−10，账本无 OPEN 仓**。

```text
fill_preceded_resync=true
ledger=[[20,"resync"]], broker=[["AAPL",10.0]]
new_stop_qty=20, new_stop_cancel_requested=false
anomalies=[], reconcile_ok=true
最终：ledger=[], broker=[["AAPL",-10.0]]
```

这反证的是“订单归属保证成交已被新快照包含”，并非主张券商已真正取消的余量必会再次成交。把该回报单纯视为已解释的历史数据，恰好绕开了已实现的迟到成交保护。

### F3 / P1：resync 拒绝后，外层对账发布旧健康并放行 ENTRY

**发现：** [engine.py:793](C:/Dev/TradingV6/v6lite/engine.py:793) 先读取 positions=20；resync 内重读 18 并拒绝请求，但 [engine.py:798](C:/Dev/TradingV6/v6lite/engine.py:798) 仅在 resync 实际应用时重新对账。拒绝时继续用旧 20，覆盖为 `reconcile_ok=True`。

**建议改动：** resync 取证后，无论成功还是拒绝，外层必须使用最新位置证据；流程中已经发现的数量差异应立即阻断 ENTRY。可返回明确的取证结果而不是仅返回 applied 布尔值。

**测试：** executions 取证期间 broker 从 20 变成 18。请求 20 被正确拒绝，resync 记录为 0；账本 20、broker 18，但对账显示成功、无 mismatch。随后调用正常 `_try_enter` 输入合法 SPY 信号，返回 **sent，提交 BUY 9**。SR3-3 的“局部重读”已实现，端到端健康结论仍不正确。

### F4 / P2：resync 丢失已知 TP1 部分成交进度

**发现：** [store.py:726](C:/Dev/TradingV6/v6lite/store.py:726) 将 qty_initial 重置为当前持仓，[store.py:729](C:/Dev/TradingV6/v6lite/store.py:729) 只复制 DONE 阶段，PENDING 阶段的已成交量被丢弃。

**建议改动：** 保留可证明的阶段目标和已成交量。若 resync 的产品语义确实要重新规划止盈，必须明确宣布目标重置并使操作员能够审核；不能同时声称原退出计划连续保持。不确定的止盈进度可冻结等待处理，STOP 保护继续。

**测试：** 原 20 股，TP1 目标 6，已成交 3 后撤掉剩余并结算；resync 到 17 股。新 TP1 发 5 股，**原已成交 3 + 新计划 5 = 8，超过原目标 6**。这不是超出总持仓的空头反例，严重度与 F2 分开。

### F5 / P1：resync 与当天日停之间存在崩溃窗口

**发现：** [store.py:688](C:/Dev/TradingV6/v6lite/store.py:688) 的 resync_position 提交持仓、归属及记录后返回；[engine.py:898](C:/Dev/TradingV6/v6lite/engine.py:898) 才另行锁定日停。重启不从 resync 记录重建当日禁入约束。

**建议改动：** resync 替换、审计记录、订单归属和对应交易日的入场冻结在同一事务中提交；启动再检查这一持久化不变量。计一次亏损不能替代日停，因为单次亏损未必达到冷却阈值。

**测试：** 在 resync 已提交、latch_daily_stop 写入前注入进程中断，然后重新打开 Store、startup 和 tick。结果 **resyncs=1、daily_stop=0、streak=1、cooldown=None、entry_blocks=[]**。D14 的当天冻结保证被绕过。

### F6 / P1：支持的 v2→v3 迁移没有处理旧恢复语义

**发现：** [store.py:221](C:/Dev/TradingV6/v6lite/store.py:221) 只加 superseded_by 并删除 adopt disposition、repair 请求和水印，未处理已有 adopted 仓、旧 repair 合成成交或旧入账式 closed_at。新功能删除不等于旧状态失效已安全处理。

**建议改动：** 对含这些历史的旧库执行显式迁移审核或拒绝自动运行；保留原始证据。能从真实 fills 证明的平仓执行时间应回填，不能证明的恢复历史进入保守人工流程。不得简单删除仓位、旧防重信息或保护状态。如果产品只接受全新空库，应明确拒绝这些旧库，不能继续宣称无条件自动兼容迁移。

**测试（均构造缺少 superseded_by/resyncs 的临时 v2 形态库再实际升级）：**

| v2 数据 | v3 实际结果 | 影响 |
|---|---|---|
| 当日 adopted 40@100、昨收90、现价95 | 仍 OPEN/adopted，日损益 **+200**，原保守语义应 −200；日停0，entry_blocks=[] | 旧 adopt 仍被管理，但日损语义静默变化 |
| ENTRY 已入账10，repair 替代漏记 BUY10；实际持仓20 | 删除水印且订单 superseded=null；真实旧 BUY10 到达后 **ledger30 / broker20，inserted=true，anomalies={}** | 旧防重证据消失 |
| 真实平仓亏10:00、亏10:10、盈10:20；第二笔10:30才入账 | 旧 closed_at 未回填，排序仍亏/盈/亏，**cooldown=None，应11:10** | Q6 新写入修复未覆盖历史 |

这些是包含相应历史的升级路径问题，不断言用户的真实库一定含有这些记录；本次没有读取真实状态。

### F7 / P1：短查询期间跌破止损，取证后仍发送 TP1

**发现：** [engine.py:1164](C:/Dev/TradingV6/v6lite/engine.py:1164) 的 `_exit_still_wanted` 检查阶段、数量和在途意图，但不根据最新报价重新判断 STOP 优先级。等待期间若尚未触发下一次周期性 decision pass，PENDING TP1 仍被视为有效。

**建议改动：** 发出退出单前，用最新有效报价重判退出原因、阶段和数量；STOP 已触发时不得继续发送排队的止盈。周期性检查与最终发送校验应各自成立。

**测试：** 单仓 TP1 已排队，唯一 open_orders 查询耗时 15 秒，其间价格从 120 降到 107，低于 stop=108.47。查询返回后系统发出 **TP1 6 股，LMT105.91**，而不是 STOP 20。默认周期30、query实际15符合声明，不会强制触发额外 decision pass。原 SR3-2 测试依赖第二仓取证恰好触发新判断，未覆盖此序列。

### F8 / P2：预算配置不兼容，排队副本还能绕过 30 秒退避

**发现 A：** [config.py:53](C:/Dev/TradingV6/v6lite/config.py:53) 允许 exit_check_interval_seconds 最小为 1；[engine.py:270](C:/Dev/TradingV6/v6lite/engine.py:270) 无法在不可中断的单次阻塞期间再检查。因此仅有“实际耗时不超过声明”并不足以保证任意配置周期。

**测试 A：** 合法 interval=10，query 声明15、实际15，检查间隔 **[0,15]**，超过10。默认30下上一轮的累计等待反例已修复，这个新反例针对可配置边界，不能混为旧40秒问题仍在。

**建议 A：** 启动时验证可选阻塞操作的最大预算不超过检查周期，并留处理余量；或者采用能中断/异步服务保护的调用模型。G2 仍须验证实际调用耗时确实符合声明。

**发现 B：** [engine.py:1101](C:/Dev/TradingV6/v6lite/engine.py:1101) 仅在入队时检查 backoff；工作项执行中，保护判断可将同 key 再入队。失败设置 backoff 后残留副本仍在，而 [engine.py:1111](C:/Dev/TradingV6/v6lite/engine.py:1111) 执行时不重查。

**测试 B：** 两仓 STOP，两个失败查询各耗15秒；拒绝后的残留项下一 tick 就再次取证。记录的退避截止 **14:02:02**，实际下一次查询开始 **14:01:33**，比截止早29秒。有限队列已经避免原无限循环，但“拒绝后30秒不重试”仍不成立。

**建议 B：** 执行时再次检查 backoff，并在拒绝时失效/删除同阶段残留项；保证 STOP 替代旧 TP 时仍可按明确优先级调度。

### F9 / P1：D13 将含股票腿的 BAG 也忽略

**发现：** [ibkr.py:198](C:/Dev/TradingV6/v6lite/broker/ibkr.py:198) 一概过滤 secType!=STK。普通期权订单不应按股票股数直接预留，但非 STK 并不等于不影响股票持仓：BAG 可以包含股票买卖腿。

**建议改动：** 对组合订单按合约腿识别股票占用；不支持解析时，明确标记存在无法解释的组合并失败关闭相关交易。实现组合策略本身不是必要条件，但不能静默把该风险当作零。

**测试：** FakeIB 返回账户内 working BAG，20 份组合含每份 SELL 1 AAPL / BUY 1 TSLA；AAPL 持有20。实际适配器返回 **0 个订单**，引擎另发 **STOP20**，忽略了组合的 AAPL20 卖出占用。该探针证实过滤和发送行为，未冒充真实券商完成组合成交。

[IBKR 官方复杂订单课程](https://www.interactivebrokers.com/campus/trading-lessons/python-complex-orders/)明确以买 TSLA、卖 AAPL 的股票组合说明 BAG；因此这不是根据名称猜测产品类型。D13 可收窄为“已确认不含股票腿的订单不直接按股数预留”，不能保留原来的全称判断。

## 4 报告第 3 节逐行裁定

沿用前两轮口径：本地明确反例为 `OPEN_PRODUCT_DEFECT`；真实接口时机未验属 `IMPLEMENTED_VALIDATION_PENDING`；`CLOSED_WITH_EVIDENCE` 仅关闭该行明确范围，不代表 G3 或 Live 放行。

| 原报告行 | 裁定 | 发现 → 改动核对 / 待改 → 测试 |
|---|---|---|
| Q1 孤立/held/外来 SELL 容量 | **OPEN_PRODUCT_DEFECT** | 原孤立和 held 反例已经通过 broker 列表预留修复；F1 查询失败回退可卖至−20，F9 又漏掉股票组合腿。须完善证据失败和订单覆盖范围。 |
| Q2/Q3/Q4 resync 合并行 | **OPEN_PRODUCT_DEFECT** | 原时间水印删除、更正优先、开放仓纯价格重建均已实现；F2/F3/F4/F5/F6 证明恢复整体仍不完整。原专项通过不能关闭这些新路径。 |
| Q5 删除 adopt | **OPEN_PRODUCT_DEFECT** | 新 CLI/disposition 已删除，相关测试通过；旧 adopted OPEN 仓随支持的迁移仍继续运行且损益口径改变，见 F6。 |
| Q6 全历史执行时间连亏 | **OPEN_PRODUCT_DEFECT** | 新入账用 MAX(time)、取消 LIMIT 正确且通过原反例；旧库的 closed_at 未回填，F6 仍漏冷却。 |
| Q7 排队及逐项预算 | **OPEN_PRODUCT_DEFECT** | 默认30秒下原累计等待反例已通过；队列有界。整行包含的普遍上界和30秒退避仍被 F8 反证，发送时决策另见 F7。此行 OPEN 不表示旧预算修复没有作用。 |
| Q8 错库预检 / 启动失败撤单 | **CLOSED_WITH_EVIDENCE** | preflight_state 在 Store/连接前；startup 先身份绑定；接管前失败只断开。错库字节不变、无撤单、生成迁移备份三项通过。数据语义兼容缺口独立归 F6。 |
| D7 ready 字段两种拼写 | **CLOSED_WITH_EVIDENCE** | case-insensitive 比较已实现，专项通过。关闭的是大小写兼容；240/150/30秒参数、完整账户下载和真实推送节奏仍待 G2。 |
| D9 撤单与迟到成交竞态 | **OPEN_PRODUCT_DEFECT** | 普通已知本地订单 race 测试通过；resync 的 superseded 分支绕过风险处理，F2 造成−10，属于当前软件漏洞。券商撤单生效前窗口另外留作 G3 验收。 |

总计 **2 CLOSED / 6 OPEN**。这里没有把其他章节的 G2 待验事项硬塞进大小写修复行，以免混淆“实现修复”和“实际环境验收”。

Q2/Q3/Q4 的局部能力再分开确认：**Q3 更正优先为 CLOSED_WITH_EVIDENCE**，包括“原 execution 已隔离、随后其更正到达”的补充探针，正确产生 exec_correction 异常；**Q4 开放持仓且数量不变的纯价格重建能力为 CLOSED_WITH_EVIDENCE**。这两点仍受 resync 整体取证缺陷约束，不等于恢复流程获准使用。

## 5 报告 SR3 逐项裁定

| 编号 | 裁定 | 说明 |
|---|---|---|
| SR3-1 队列 livelock / 拒绝退避 | **OPEN_PRODUCT_DEFECT** | 有界返回部分已关闭：只跑初始键，每次 protect 最多一轮，原测试通过。但同一行的30秒退避仍被 F8-B 推翻，剩余为P2；没有重新复现无限循环。 |
| SR3-2 过期排队 TP | **OPEN_PRODUCT_DEFECT** | 已变化的 stage 会重查，但查询内新报价未必触发阶段变化；F7 单仓反例仍发 TP1 6。 |
| SR3-3 取证后持仓重读 | **OPEN_PRODUCT_DEFECT** | 局部重读确会拒绝20→18；外层继续用20发布健康并发 BUY9，见 F3。 |
| SR3-4 最晚执行时间 | **CLOSED_WITH_EVIDENCE** | 新平仓使用全部相关成交的 MAX(time)，用例通过；旧 closed_at 迁移问题归 F6/Q6。 |
| SR3-5 普通期权不按股数预留 | **CLOSED_WITH_EVIDENCE** | OPT 对照通过；只关闭该具体错误，不接受由它推广出的 D13“所有非STK均无股票占用”。 |
| SR3-S1 remaining=0 fallback | **CLOSED_WITH_EVIDENCE** | 本地 totalQuantity−filled 回退测试通过；实际 openOrder/orderStatus 到达顺序仍待 G2 记录。 |
| SR3-S3 放弃 TP 同轮启动 STOP | **CLOSED_WITH_EVIDENCE** | decision pass 已看到 STOP 条件时同轮接续，专项通过；F7 是发送前行情变化的另一条路径。 |
| SR3-S4 resync 算一次亏损 | **CLOSED_WITH_EVIDENCE** | 正常完整提交的 managed resync 会进入连亏负事件，测试通过；这不能替代 F5 所需的原子日停。 |

本节按八项原始行范围：**5 CLOSED / 3 OPEN**。真实 G2 时序仍单独待验。

## 6 第 7 节八项设计判断

| 判断 | 意见 | 必要条件或修改 |
|---|---|---|
| **D3′** 外来/孤立/held 预留，不自动撤 | **接受原则，当前实现未完成** | 列表中正证据优先、STOP可压小并报警的方向合理。当前查询失败应失败关闭；不能漏掉股票组合腿。F1/F9。 |
| **D5′** resync 替代补记 | **保留收窄恢复方向，推翻当前充分性论证** | 无working不证明持仓已包含旧成交；隔离不能吞风险处理。还需一致取证、持仓代次关联、部分阶段语义和原子日停。F2–F6。 |
| **D6′** hold 不阻挡判断，working 优先 | **有条件接受** | 在当前成功且覆盖完整的订单证据下，原 held 超卖反例已修。hold 不代表可以在取证失败时忽略可能存在的工作量；不得用 F1 的降级缓存保证最终发送。 |
| **D8′** 非阻塞判断、逐项预算 | **接受调度方向，推翻仅凭单次声明即可保证任意周期的说法** | 默认30秒的原反例已修；还必须保证最大不可中断请求不大于配置周期，并验证实际耗时。检测频率合格也不能代替最终行情/阶段重判。F7/F8-A。 |
| **D11** 60秒证据时效，证据不足不发 STOP | **接受不足即拒绝；不接受当前回退实现作为保证** | 60秒只是上限参数，不等于当前查询失败后仍可用旧列表。F1已超卖；真实快照变化和更新范围待 G2。 |
| **D12** 30秒退避、每 protect 最多一轮 | **接受有限轮次；退避实现推翻** | 有界队列成立，但已有副本在执行边界不检查 backoff，F8-B 早29秒重试。 |
| **D13** 非 STK 不参与股数预留 | **推翻全称判断** | OPT不能直接按股数算；BAG却可含股票卖出腿。识别股票腿或对无法解释的组合失败关闭，F9。 |
| **D14** 每次 managed resync 算亏损、当天日停 | **接受保守政策，推翻当前持久化保证** | 正常路径计亏成立；resync与日停须同一事务并有重启校验。一次亏损本身不会必然触发冷却，F5。 |

## 7 下一轮应关闭的范围与关卡

1. **先收紧证据和发送边界：** fresh 查询失败拒绝本次 SELL；覆盖股票组合腿或明确拒绝无法解释的组合；发送前重新判断 STOP 优先级。
2. **恢复流程先保证风险状态：** 隔离成交仍使当前 symbol 风险证据失效并处置过量卖单；成功和拒绝都传播最新持仓；新旧仓通过 lineage 关联；resync与当天日停原子提交。
3. **明确历史和阶段政策：** 旧 adopt/repair/closed_at 迁移处理或显式拒绝；保存已证明的 TP 部分进度，不能无说明重置目标。
4. **完成调度边界：** 校验检查周期与最长阻塞预算兼容；执行前复核退避，清理重复队列项。

上述 P1 关闭并通过相应反例后，才进入相应 G3 放行判断。严格只读、独立 dry-run 状态的 G2 可用于收集账户流、订单范围、持仓/成交时序和实际耗时证据，本次未启动。G2 观测不能替代已确认的本地缺陷修复。**L7 未实现，Live 前必须完成并验收**；它不替代上述本地数量、恢复和风控约束。

既有开发计划没有改写；本节给出第三轮证据对应的增量顺序。

## 8 本机临时复现材料

以下脚本均只建立临时 Fake 状态；执行完数据库清理。路径位于本机临时目录，后续应将必要反例固化为仓库回归测试，而不能将临时文件视为长期测试资产。

| 脚本 | 覆盖 |
|---|---|
| [probe_sell_evidence.py](C:/Users/yu-ch/AppData/Local/Temp/v6lite-round3-main-4819eed20d6142e6a31fb860cbd9925f/probe_sell_evidence.py) | F1查询失败回退、F9股票组合遗漏 |
| [v6lite_round3_resync_probe.py](C:/Users/yu-ch/AppData/Local/Temp/v6lite_round3_resync_probe.py) | F2/F3/F4及更正优先对照；[运行结果](C:/Users/yu-ch/AppData/Local/Temp/v6lite_round3_resync_probe_results.txt) |
| [round3_accounting_probe.py](C:/Users/yu-ch/AppData/Local/Temp/v6lite-r3-accounting-2151dd2ee9674cf49d282ef8c2424354/round3_accounting_probe.py) | F5原子日停、F6三项迁移 |
| [probe_q7_sr3_d12.py](C:/Users/yu-ch/AppData/Local/Temp/v6lite-r3-queue-review-kepfeh0_/probe_q7_sr3_d12.py) | F7短查询行情变化、F8退避及短周期配置 |

运行任何一项前启用仓库 venv、设置 UTF-8 和新的临时 PYTHONPYCACHEPREFIX，再把上述绝对脚本路径传给 python。补充探针结果不计入190项，也没有改动交付测试来获得通过结果。
