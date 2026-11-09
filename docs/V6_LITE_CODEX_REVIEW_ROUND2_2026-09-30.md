# V6-Lite 第二轮 Windows 独立复核

日期：2026-09-30 PT。复核者：Codex。对象：第二轮整改报告及其 SHA-256 清单对应的 33 个文件。

**33/33 哈希匹配；Windows 全量 165 passed，第二轮专项 61 passed。G3 仍为 NO-GO。** 报告第 3 节的 19 个事项（含表外 R11）中，10 项 `CLOSED_WITH_EVIDENCE`、2 项 `IMPLEMENTED_VALIDATION_PENDING`、7 项 `OPEN_PRODUCT_DEFECT`。另发现正式 run 启动失败后仍会撤单、错账户库在拒绝前被迁移的问题。原报告明确没有宣称 G3/Live 放行，这一边界应保留。

本次为只读源码复核，复核结论作为 docs-only 产物保存。探针只使用 FakeBroker、FakeIB、MemoryChannel、安装的 ib_insync 对象、合成时钟、临时数据库及临时锁目录；没有连接 Gateway、读取真实运行 state 或 `.env`、签发批准、stage、commit 或 push。仅新增本文件；交付代码、两份整改报告、第一轮 Codex 复核和既有开发计划保持原样。下文“建议改动”均未实施。

## 1 交付身份与 Windows 验证

| 验证 | 结果 |
|---|---|
| 第二轮 SHA-256 清单 | 33 项全部匹配，缺失 0、差异 0；排除缓存后没有清单外的 Lite 文件 |
| 平台 / Python | `Windows-10-10.0.26300-SP0` / 仓库 venv Python 3.11.9 |
| Lite 全量 | `165 passed in 27.49s`，无失败、无跳过 |
| 第二轮专项 | `61 passed in 3.58s`，无失败、无跳过；已包含在上述 165 项内，不相加 |
| Windows 原子账户锁 | 全量覆盖 Windows 分支、跨进程互斥及持锁进程退出后释放；本批 R11 的 AccessDenied 拒绝和 PID 消失放行均通过 |
| 策略与 soak | 全量内的固定样本策略比较和生命周期 soak 均通过 |
| 旧编译缓存 | 每次验证使用新的临时 `PYTHONPYCACHEPREFIX`，未依赖交付目录内的旧缓存 |

复跑命令（两个命令均已在 Windows 执行）：

```powershell
cd C:\Dev\TradingV6
. .\venv_v6\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
$env:PYTHONPYCACHEPREFIX = Join-Path ([System.IO.Path]::GetTempPath()) ('v6lite-round2-review-' + [guid]::NewGuid().ToString('N'))
python -m pytest v6lite/tests -q -ra
python -m pytest v6lite/tests/test_review_round2.py -q -ra
```

交付与既有文档的身份锚点（复核前后相同）：

| 文件 | SHA-256 |
|---|---|
| `docs/V6_LITE_FIX_REPORT_ROUND2_2026-09-30.md` | `a4d7e15fe0ca6a5e302f711969ef9f43de9543db234360da2b9b87fc4d317cab` |
| `docs/V6_LITE_FIX_REPORT_2026-09-30.md` | `215fe5760da27b30dc4bae24048e96d39f91255014f2f2ac52b3beed19f8d26f` |
| `docs/V6_LITE_CODEX_REVIEW_2026-09-30.md` | `297b526e6df120ae585db59233491c4c2f96629e84610184fd506bec7300b426` |
| `docs/V6_LITE_REDESIGN.md` | `203bad0cf719cbe16ef6485a38c4bc729945e20a230cbea687b84c01df849e02` |

这是 Lite 全量，未运行冻结的旧 V6 全量。原报告的整改前 `55 failed / 6 passed` 和整改方连续三跑属于其历史证据；本次独立验证的是当前交付版本，没有将历史失败数或三次运行冒充本次结果。原测试中 D1 的断言确实发生政策变更，不能把“原测试都绿”解释为旧口径不变。

## 2 新输入序列的反证：发现 → 建议改动 → 测试

以下已执行的补充探针不计入 165 项。P1 项均阻塞 G3；没有发现已证实的 P0。合成日期、价格、延迟及矛盾 broker 证据用于验证程序边界，不代表已在真实 Gateway 观察到同样事件。

### Q1 / P1：孤立 SELL 和后来重新出现的 held SELL 不占用可卖数量

**发现：** [engine.py:529](C:/Dev/TradingV6/v6lite/engine.py:529) 检测孤立订单后只阻断入场；[engine.py:972](C:/Dev/TradingV6/v6lite/engine.py:972) 的退出检查和 [engine.py:1171](C:/Dev/TradingV6/v6lite/engine.py:1171) 的最终 SELL 门只看本地非 hold 意图。已 hold 的订单后来被 fresh open_orders 明确报告 working 时，[engine.py:590](C:/Dev/TradingV6/v6lite/engine.py:590) 仍直接返回，既不纳入余量，也不作为孤立订单报警。

**建议改动：** broker working 正证据必须参与最终 SELL 余量。未知归属、方向或剩余数量的订单，应阻断该标的冲突 SELL 并要求处置；保留“不自动撤掉未知保护单”的原则。hold 是证据未解，不能等价于永远不 working。

**测试 A：** 账本及 broker 均持有 20 股；broker 已有账本未知的 `v6l-lost-stop` SELL 20。对账成功发现 `orphan_order`，随后 STOP 检查仍新发 SELL 20。两单完整成交后 broker 为 **−20 股**，账本无 OPEN 仓。ENTRY 阻断和事后告警没有阻止超卖。

**测试 B：** 先按已有夹具生成合法跨日 hold；随后注入 fresh 查询重新返回该 TP1 为 working、剩余 6 股。恢复和对账已经读取该证据，持仓仍为 20；系统随后新发 STOP 20，两单成交后 broker 为 **−6 股**。这是对“后来的矛盾证据”的测试，不据此声称 DAY 单在真实券商一定会跨日工作。

### Q2 / P1：C1 在没有时钟偏差时仍重复记账

**发现：** [engine.py:200](C:/Dev/TradingV6/v6lite/engine.py:200) 取得 tick 开始时间，经 reconcile 传入 [engine.py:817](C:/Dev/TradingV6/v6lite/engine.py:817)，[store.py:690](C:/Dev/TradingV6/v6lite/store.py:690) 将它保存为 `repaired_through`。这个时间可能早于实际修复所包含的成交；换成“当前时钟”只能修掉这一局部问题，不能解决 Q3 的归属问题。

**建议改动：** 修复依据应保存所替代的 execution/intent、数量、交易日和实际取证时点；提交修复前重核 broker 证据。用可审计的成交关联处理后续原始 execution，不能只依赖 symbol 时间阈值。

**测试：** tick 开始于 14:01:09；正常账户流刷新阻塞 2 秒；14:01:11 已撤 STOP 的 10 股成交进入 broker 持仓，但暂不出现在 executions 中，操作员请求 repair。该 tick 将账本修复为 10 股，marker 却为 **14:01:09**。原成交稍后可见并重启回放，因 execution 时间较 marker 晚而再次入账：**账本无 OPEN 仓、broker 仍持有 10 股、quarantine=0、reconcile=False**。这不是报告列出的时钟偏差或同秒精度风险，而是正常阻塞调用即可触发的实现缺陷。

### Q3 / P1：repair 的整标的时间过滤吞掉不相干的成交更正

**发现：** [engine.py:294](C:/Dev/TradingV6/v6lite/engine.py:294) 的 watermark 过滤在 exec_base 更正识别之前执行。任何该 symbol 的旧时间、未知 execId 都被假定已经由 repair 解释，导致应有的 `exec_correction` 异常被降成 P2；数量对账无法发现价格错误。

**建议改动：** 更正识别必须优先；repair 只能认领有明确关联的成交。无法证明关联时保留异常和人工审核，不能以数量相等解除成本、费用及风控账本的不确定性。

**测试 A：** ENTRY 20 股价格 113.47，手工 repair 漏掉的 SELL 10；随后原 ENTRY 的 `.02` 更正将价格改为 120，数量不变。结果 `quarantine.reason=explained_by_repair`、**没有 exec_correction 异常**，ledger 和 broker 均为 10 股且数量对账通过，成本和日损仍用旧值。

**测试 B（P2，已报告风险的实证）：** marker 为 14:00:32，实际在 500ms 后发生新的 SELL 1，但注入的 execution 时间精度为秒，仍为 14:00:32。新成交被 `<= marker` 永久隔离：ledger 10、broker 9、quarantine 1，隔离本身无异常。后续对账可以发现差异，不能自动恢复这笔被过滤的成交。

### Q4 / P2：repair-position 不支持不改数量的价格更正

**发现：** [engine.py:807](C:/Dev/TradingV6/v6lite/engine.py:807) 以 `delta == 0` 直接判为“不需要修复”。D4 所述“数量或价格变化后由人工 repair-position 处理”对纯价格更正不成立；数量相等也不会令 reconcile 显示数量差异。

**建议改动：** 提供明确的成交更正/冲销恢复流程，覆盖价格、费用、已平仓交易和数量不变的修订。实施前保留更正异常，不应引导操作员仅清异常就继续。

**测试：** 原仓 20@113.47；正常隔离更正 20@120 后，请求 `broker_qty=20, price=120`。结果显示 `repair not needed`，repair fills 为 0、`entry_price` 仍为 113.47，原更正异常还在。这是恢复能力缺口，不是已发生的静默自动放行。

### Q5 / P1：D2 的 adopt 原始成本会被后续 BUY 改写

**发现：** [risk.py:82](C:/Dev/TradingV6/v6lite/risk.py:82) 用当前 `entry_price` 计算当日 adopt 的参考；[store.py:517](C:/Dev/TradingV6/v6lite/store.py:517) 在追加 BUY 时更新该均价。原始 adopt 成本并非不可变证据，日损仍可能被向上改写。

**建议改动：** 单独保留 adopt 时的数量、原始成本和发生时间。按不可变 adopt 参考及成交明细重建日损，不能从可变持仓均价反推。

**测试：** adopt 40@100、昨收 90；之后 repair BUY 40@80、费用 1，现价 87。按 D2 约定应为 `40×(87−100)+40×(87−80)−1 = −241`；程序返回 **+159**，`daily_stop_latched=0`。原单独 adopt 测试没有覆盖后续补记买入。

### Q6 / P1：D1 重算仍按到达顺序，并截断为最近 50 笔

**发现：** [store.py:594](C:/Dev/TradingV6/v6lite/store.py:594) 用入账 now 写 `closed_at`，[store.py:714](C:/Dev/TradingV6/v6lite/store.py:714) 默认仅返回最近 50 笔，[risk.py:133](C:/Dev/TradingV6/v6lite/risk.py:133) 每次从零重算。两者分别会漏掉应有冷却。

**建议改动：** 从成交证据推导实际完成平仓的时间，迟到证据进入后按执行顺序重算。风控不能复用展示用途的 LIMIT 50；采用完整必要历史或能正确处理历史费用修订的检查点。人工历史修复也须有明确的执行时间语义。

**测试 A（仅三笔）：** 真实平仓依次为 AAPL 10:00 亏、SPY 10:10 亏、TSLA 10:20 盈；SPY execution 到 10:30 才入账。程序排序成亏/盈/亏，得到 **cooldown_until=None、streak=1**；按实际顺序应保留冷却至 **11:10**。盈利单不解除已经触发的冷却，与现有算法政策一致。

**测试 B（默认 max_consecutive_losses=2）：** 历史前 49 笔亏损，第 50 笔为 0，第 51 笔亏损；第 49 笔 09:30、第 50 笔 10:00、第 51 笔 10:40。完整历史应冷却至 **11:40**；最近 50 笔从零重算只得到 **10:30**，10:40 时 `entry_blocks=[]`。零不重置连亏这一既定规则已保持，错误来自截断改变计数起点。

### Q7 / P1：R10 的预算没有包含保护检查自己消耗的慢操作

**发现：** [engine.py:240](C:/Dev/TradingV6/v6lite/engine.py:240) 在 `_in_protection` 时只 drain；[engine.py:951](C:/Dev/TradingV6/v6lite/engine.py:951) 顺序执行 exit followups，撤单又进入 `_protect`。嵌套保护被抑制，followups 消耗的时间没有使外层请求重新计算预算。

**建议改动：** 用实际时钟管理整个保护周期和待执行请求的剩余预算，包含每个 followup；每次慢操作后重新判断下一次检查期限。需要可暂停/分批的 followup 调度，不能只在外层请求前提前检查一次就承诺周期上界。

**测试：** 两个 OPEN 仓各有 TP1 SENT；STOP 触发后，保护检查的两个撤单各耗 15 秒，外层历史请求耗 10 秒，均不超过 FakeBroker 声明的 query=15、history=10；VIX 也未超过其声明 25 秒。走真实 `scan_and_enter` 路径，SPY 相邻退出检查间隔为 **[40.0, 20.0] 秒**，配置为 30 秒。故 D8 的前提全部成立时，上界结论仍不成立，不必等待 G2 才能判定。

### Q8 / P1：正式启动拒绝后仍执行撤单；错账户库先被迁移

**发现：** [__main__.py:148](C:/Dev/TradingV6/v6lite/__main__.py:148) 直接 open_store，尚未只读校验身份就发生迁移；[engine.py:121](C:/Dev/TradingV6/v6lite/engine.py:121) 先连接 broker，再绑定状态身份。startup 抛错后，[__main__.py:176](C:/Dev/TradingV6/v6lite/__main__.py:176) 无条件 shutdown；[engine.py:1249](C:/Dev/TradingV6/v6lite/engine.py:1249) 获取 working 集合并逐笔请求撤单，不以成功接管为前提。

**建议改动：** 正式 run 应在迁移、连接、接管前完成只读身份验证。启动失败清理要区分“只建立连接”和“已经成功接管订单”；前者不能调用批量取消 working 的交易清理路径。完整迁移过程另需备份及故障恢复验证。此为新发现的边界漏洞，不声称它由第二轮修改首次引入。

**测试：** 在临时目录构造账户 DU0000000 的 schema 1 库（移除新增三列、索引和 quarantine 表）；配置为 DU0000000。用 mock 把 cmd_run 的 broker、时钟、告警、日志和锁目录全部替换为本地对象，FakeBroker 上注册未知保护 SELL `v6l-foreign-store-protection`。实际调用 cmd_run 后输出：

```text
StartupError: state bound to account=DU0000000 mode=paper; refusing account=DU0000000 mode=paper
cancel_requests = ['v6l-foreign-store-protection']
schema_version_after = '2'
store_account_after = 'DU0000000'
migrated_columns = ['hold_reason', 'recovery_position_id']
```

FakeBroker 记录的是撤单请求，不冒充券商已经撤单。拒绝启动仍发出该请求足以反证无写入清理边界。status/report 的身份保护测试仍通过；这一漏洞发生在 run，不能把两个入口的结论混为一谈。

## 3 对原报告第 3 节逐行裁定

沿用第一轮定义：`CLOSED_WITH_EVIDENCE` 仅关闭该行明确的本地缺陷，不等于整机上线；`IMPLEMENTED_VALIDATION_PENDING` 表示实现已有、关键真实行为待验证；存在确定反例则为 `OPEN_PRODUCT_DEFECT`，不能降成待观察风险。

| 报告行 | 裁定 | 发现 → 改动核对 / 待改 → 测试证据 |
|---|---|---|
| R1 孤立 working | **OPEN_PRODUCT_DEFECT** | 检测、ENTRY 阻断和 client 变更检查已实现；SELL 余量未完整纳入 broker 证据。原 R1 三项通过，Q1 可从 20 卖至 −20。 |
| R2 跨日 UNKNOWN / 人工恢复 | **OPEN_PRODUCT_DEFECT** | durable hold、跨午夜和 shutdown 保留已实现；新增 repair 的去重仍错误，hold 后新 working 证据被忽略。原 R2 各项通过，Q1/Q2 反证恢复完整性。 |
| R3 迟到 ENTRY 分批 | **CLOSED_WITH_EVIDENCE** | recovery_position_id 持久归属、并入恢复仓成立；两批、重启幂等、三批不同价格用例通过。后续 repair 的错误单列 Q2/Q3。 |
| R4 对账先发现迟到 SELL | **CLOSED_WITH_EVIDENCE** | mismatch 独立排队限频 execution 刷新成立；reconcile-first 测试通过，恢复真实剩余 10 股并继续 STOP。 |
| R12 execId / 费用更正 | **OPEN_PRODUCT_DEFECT** | 普通 exec_base 隔离及同 execId 佣金覆盖均通过；repair 过滤能绕过更正异常，且价格-only 无恢复入口。见 Q3/Q4。 |
| 判断 2 撤单后迟到成交 | **OPEN_PRODUCT_DEFECT** | 已知本地 retry 的撤单和预留通过两项 race 测试；Q1 证明预留范围仍遗漏，D9 的券商竞态也未闭环。 |
| R5 可用资金重复消费 | **IMPLEMENTED_VALIDATION_PENDING** | 在途 ENTRY 及快照后成交预留、撤单释放、陈旧/未就绪拒绝已实现，相关回归通过；账户推送、完整下载、就绪字段和时效含义仍需 G2。见 D7。 |
| R6 昨收基准 | **OPEN_PRODUCT_DEFECT** | 普通减仓、全平、缺昨收拒绝、重启回归通过；adopt 原成本仍可变，Q5 将实际 −241 算成 +159。 |
| R7 连亏 / 迟到费用 | **OPEN_PRODUCT_DEFECT** | force/pending 永久定案已移除，费用可重算；Q6 两种新序列均漏冷却。须修执行顺序及历史范围。 |
| R8 扫描期间日损 | **CLOSED_WITH_EVIDENCE** | service_protection 和最终 ENTRY 门重算已实现，成交/费用在扫描期间越线的两项通过；公式缺陷归 R6。 |
| R9 最终告警健康 | **CLOSED_WITH_EVIDENCE** | ENTRY 实时读取 Alerter.healthy，两个 LIVE 模拟用例通过，退出不受告警故障阻断。没有启动真实 LIVE。 |
| R10 慢请求保护周期 | **OPEN_PRODUCT_DEFECT** | 单层预算与部分 followup 调整已实现，原回归通过；Q7 在所有调用满足声明上限时仍得到 40 秒间隔。 |
| P2 日报 ID 重启碰撞 | **CLOSED_WITH_EVIDENCE** | 随机进程 token + 序号及 unknown pending 重发成立；两个专项通过，补充进程间 ID / knows 检查通过。 |
| P2 失败告警去重 | **CLOSED_WITH_EVIDENCE** | 仅成功/在途去重，失败后可新建投递；专项及补充失败→恢复重试通过。 |
| P2 SMTP 端口 | **CLOSED_WITH_EVIDENCE** | 拒绝 bool、越界、负数、非整数等；五项参数化检查通过。 |
| P2 status/report 错账户 | **CLOSED_WITH_EVIDENCE** | 只读 meta 预检在 Store 之前，错身份不迁移；相关专项通过。Q8 的 run 入口另列，不能据此推翻这两个命令的局部修复。 |
| P2 动态费用预留 | **IMPLEMENTED_VALIDATION_PENDING** | sizing、资金门、缺失佣金共用 estimate_fee，数量缩放通过；×1.5 和 $2 底额是估计，并非所有账户实际费用的保证上界。需 G2/G3 对账。 |
| P2 适配器佣金恢复 | **CLOSED_WITH_EVIDENCE** | canonical wrapper Fill 优先、空 CommissionReport 为 None；专项及真实安装的 ib_insync 本地对象探针通过。见下文真实迟到时机限制。 |
| R11 旧锁 AccessDenied（表外） | **CLOSED_WITH_EVIDENCE** | 不可确定 PID 存活时失败关闭、确认消失才放行；两个专项随 Windows 全量通过。 |

计数：**10 CLOSED / 2 PENDING / 7 OPEN**。另有 Q8 正式启动边界缺陷；不能因为它不在原 19 行里就从 G3 清单遗漏。

佣金恢复的关闭范围是“查到 canonical Fill 时取对佣金”。本地 ib_insync Wrapper 探针还确认：无 Trade 对象时 CommissionReport 可更新 wrapper Fill，但不发 trade 相关 commissionReportEvent；若费用晚于启动 execution 查询到达，还需证明后续确有再次读取路径。真实到账时机和最终账本可收敛性留作 G2/G3 验收，不能用当前测试声称所有迟到费用都已自动补齐。

## 4 D1–D10 逐项表态

| 判断 | 意见 | 条件 / 必须修改的部分 |
|---|---|---|
| **D1** 连亏重算、缺费先估计 | **接受设计方向；推翻当前实现已满足它** | 保守临时冷却可接受，真实费用须能更正。Q6 的入账时间排序和 50 笔截断必须修复；“按平仓顺序”应指真实执行顺序。 |
| **D2** 昨收及当日成交重建 | **接受主体口径；推翻当前完整性断言** | `max(昨收, adopt原成本)` 必须引用不可变原始证据。Q5 证明 repair 后基准仍会错；“不存基准”本身不保证正确。 |
| **D3** 孤立订单不自动撤/接管 | **接受保留未知保护单；推翻只阻断 ENTRY 就充分** | 必须同时阻断或预留冲突 SELL，直到最新 working 证据被解释。Q1；启动失败也不能无条件撤单，见 Q8。 |
| **D4** 更正先隔离、不自动冲销 | **接受隔离原则；推翻已有完整人工闭环** | Q3 会绕过更正 P1；Q4 不能修纯价格更正；数量 reconcile 不能检验价格。明确支持范围并补恢复流程。 |
| **D5** repair-position + 时间 marker | **推翻当前方案作为可靠去重** | Q2 无时钟偏差也能重复入账，Q3 能吞历史更正及同秒新成交。需要明确成交归属和可复核证据，单改 now 不够。 |
| **D6** hold 一律不阻挡退出 | **推翻无条件规则** | 后来 fresh working 的正证据必须优先。只有确认无冲突 working 订单时，才可释放 SELL 保留量；不能仅凭 DAY 的一般到期预期忽略矛盾证据。 |
| **D7** 240/150/30 秒及未知 ready | **有条件接受为 G2 验证参数；尚不接受为 G3 就绪证明** | 参数是工程假设。未知 ready 的放行需有成功账户下载、正确账户/币种、资金值有效和连接健康证据；核对字段大小写与推送节奏，必要时兼容两种键名。 |
| **D8** 每次调用不超声明即可保证周期 | **推翻当前上界断言** | Q7 已在满足全部单次上限时产生 40 秒间隔；先修嵌套和 followup 总预算，再在 G2 测实耗，不能只调整文案为“待真实验证”。 |
| **D9** 迟到退出后撤其他 SELL，L7 闭环 | **接受为有限缓解；推翻将全部剩余风险留到 L7/Live 的安排** | Q1 的本地漏计必须 G3 前修复。撤单生效前的窗口需另有受证据约束的重试/券商协调方案并验收；L7/OCA 不能替代本地正确计量，也不能仅凭方案名称宣称消除竞态。 |
| **D10** schema 1→2 原地迁移 | **接受已测试的迁移映射及 status/report 保护；推翻整体入口安全已完整** | 列、hold 和 exec_base 回填测试通过；run 在身份验证前迁移别人的库，见 Q8。应先预检，再迁移；备份、异常中断及重新打开的验证另补。 |

D7 有一项需准确限定的外部证据：[IBKR 字段表](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-updates/account-value-keys) 的键名是 `AccountReady`，说明文字及[账户更新说明](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-updates/receiving-account-updates) 又使用 `accountReady`。当前 [ibkr.py:142](C:/Dev/TradingV6/v6lite/broker/ibkr.py:142) 只识别前者；真实安装的 AccountValue 对象注入小写 false 时 ready=True，注入大写 false 时 ready=False。**这是已确认的兼容性缺口及 G2 取证点，不据官方文档大小写不一致断言真实 Gateway 必发小写，也不单独将其定为已证实的 P1。**

费用公式中的 Fixed 基础费率及所列 SEC/TAF 系数与本次查阅的[IBKR 官方费用表](https://www.interactivebrokers.com/en/pricing/commissions-stocks.php)相符；具体账户计费及其他适用费用须核对。execId 最后部分变化代表更正的识别方向符合[IBKR Execution 文档](https://www.interactivebrokers.com/docs/tws-api/ref/execution)，但这不证明项目当前的 repair 归属过滤正确。

## 5 对交付前自审 C1–C7 / S1–S3 的复核

| 原行 | 裁定 | 证据 / 限定 |
|---|---|---|
| C1 repair 后旧成交重复记账 | **OPEN_PRODUCT_DEFECT** | 原两项测试通过；Q2 无时钟偏差反例重新复现，Q3 另有误隔离。 |
| C2 held SELL 挡 STOP | **OPEN_PRODUCT_DEFECT** | 无新 working 证据时退出可恢复；Q1 证明无条件忽略 hold 的修法会超卖。 |
| C3 一仓刷新拖慢后仓 | **CLOSED_WITH_EVIDENCE** | 慢刷新移出遍历，原用例通过；不等于 R10 总周期已关闭，Q7 是后续队列的反例。 |
| C4 撤单 fallback 未预算 | **OPEN_PRODUCT_DEFECT** | 本地缓存优先已验证，单层 `_protect` 已接入；嵌套取消预算仍可失效，见 Q7。 |
| C5 sent_at 使用旧 tick 时间 | **CLOSED_WITH_EVIDENCE** | `_send` 使用实时时钟，专项通过；repair 的旧 now 是另一条路径，见 Q2。 |
| C6 状态提示覆盖 hold / resolve 竞态 | **CLOSED_WITH_EVIDENCE** | 状态与取消不改 hold，resolve 使用带条件 UPDATE；专项通过。hold 的交易含义另按 D6 修正。 |
| C7 repair 未计入 TP1 | **CLOSED_WITH_EVIDENCE** | `--intent` 阶段归属和 `--manual` 区分成立，原 TP1 不重复卖出用例通过；不关闭整个 repair 去重问题。 |
| S1 1101 后重订阅 | **IMPLEMENTED_VALIDATION_PENDING** | Fake 引擎用例及适配器 cancelMktData→reqMktData→reqAccountUpdates 调用链通过，资金流会清空；真实 1101 后恢复情况待 G2。 |
| S2 资金成交时间 30 秒余量 | **IMPLEMENTED_VALIDATION_PENDING** | 临界成交预留用例通过；30 秒是否覆盖实际快照延迟和时钟偏差待 G2，不能作为普遍上界。 |
| S3 安静账户 150 秒重请求 | **IMPLEMENTED_VALIDATION_PENDING** | 定时刷新用例通过；真实安静账户推送、刷新完整性及 ready 行为待 G2。 |
| 次要：日损缺费 | **CLOSED_WITH_EVIDENCE** | 已报告佣金加缺失部分估计已纳入公式；基准本身的错误另归 R6。 |
| 次要：resolve 检查后写入 | **CLOSED_WITH_EVIDENCE** | 单条条件 UPDATE 已实现，保留显式人工状态裁定。 |
| 次要：status 迁移错库 | **CLOSED_WITH_EVIDENCE** | 只读身份预检及错库不变测试通过；run 入口另见 Q8。 |

“未发现无限递归”不代表没有嵌套保护遗漏；本次 Q7 正是递归抑制后的预算问题。“未发现更正隔离数量守恒问题”也不能覆盖 Q3 的价格风险：数量相等仍可能成本错误。

## 6 后续开发和放行顺序

| 顺序 | 工作 | 完成证据 / 关卡 |
|---|---|---|
| 1 | 修 Q1、Q8：working SELL 联合保留量、hold 后矛盾证据、启动失败无订单写入、迁移前身份检查 | 固化 20→−20、20→−6 及拒绝启动仍撤单反例；应改为拒绝冲突提交/无撤单，错库字节及 schema 保持不变。G3 前。 |
| 2 | 修 Q2/Q3/Q4：有成交归属的人工恢复、更正优先、价格/费用修订入口 | 延迟查询、重启、同秒、无数量变化、已平仓更正均可收敛；费用、成本、阶段及数量共同一致。G3 前。 |
| 3 | 修 Q5/Q6：不可变 adopt 证据、实际平仓时间和完整风控历史 | −241 计算正确；乱序三笔冷却至 11:10，51 笔冷却至 11:40；补重启及历史费用修订。G3 前。 |
| 4 | 修 Q7：保护自身及 followup 总预算 | 两个及最大持仓数的慢撤单/刷新混合序列均满足配置期限，记录实际检查时刻；再做 G2 耗时验证。G3 前。 |
| 可并行 | 严格只读 G2：独立 dry-run 状态、只读连接、client_id +1000 | 由操作员启动；记录资金 ready/推送/下载完成、请求耗时、open-orders 范围、execution/commission 时机和 1101 恢复。此复核没有启动它。 |
| 之后 | G3 Paper 真实订单验证 | 上述 P1 关闭且 G2 证据完整后，受控验证退出重试/撤单成交竞态；不能以“留给 Live 的 L7”代替 G3 的防意外空头门槛。 |
| Live 前 | L7 broker 端保护方案及完整生命周期验收 | 既有计划中的 GTC Stop-Limit + OCA 尚未实现和实测，本轮没有放行。 |

既有开发计划文件没有改动；本节是第二轮证据对应的增量安排。先关闭明确的产品反例，再判断真实环境待验项，不能以测试数量增长替代风险门槛。
