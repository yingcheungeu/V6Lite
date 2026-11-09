# V6-Lite 重设计方案：以"个人交易系统尽快安全上线"为目标

**日期：** 2026-09-30
**作者：** Claude（原始设计与骨架）；Codex（2026-09-30 PT，依据当前仓库代码调整开发计划）
**状态：** 核心骨架已交付；Windows / Python 3.11.9 本机离线测试 **67 passed in 10.24s**。代码审查发现尚未覆盖的订单、只读、账户互斥、资金与告警边界，当前阶段为**离线整改**，不是可直接进入 Paper 真单的版本。真实 Gateway、行情权限与远程送达尚未验证。
**本文件不授权任何实盘或 Paper 下单。** 每一步启动都由 John 本人执行。

**本次调整范围：** docs-only，只更新本文件；不修改 `v6lite/` 实现、旧 V6、风险配置、`AGENTS.md`、memory 或运行状态。第 8 节是后续开发顺序与验收依据；原定 10/26 实盘日期改为通过验收后的候选日期。

---

## 0. 决策记录（2026-09-30，John 确认）

| 问题 | 选择 |
|---|---|
| 交付范围 | 设计方案 + 新核心代码骨架（复用已验证的逻辑） |
| 治理取舍 | **保留资金安全硬边界**；砍掉签名审批工件、独立存储域 checkpoint、多角色独立审查、20 session 计数仪式等流程层 |
| 首个上线形态 | 短期 Paper（约 2 周）后，$5k–$10k、最多 2 个仓位的小资金实盘 |
| 架构路线 | 新建精简核心 **V6-Lite**，只挑选已验证的逻辑复用；旧 V6 保持原样、冻结、逐步淘汰 |

第 14 节保留为待单独确认的记录模板，本次不追加到 `docs/codex_memory.md`，也不据模板解除旧 V6 的 hold 或取得 broker 操作授权。

---

## 1. 为什么要重设计

过去两次审查（9/15、9/28）看到的是同一个问题的两面：

- **安全工程非常扎实**：fill 幂等、TPSL 一次性锁、broker truth 对账、账户绑定、Protected Symbols——这些都是用真实事故换来的正确结论。
- **但系统的复杂度已经大于它要承载的风险。** `ibkr_session_manager.py` 从 9/15 的 99KB 长到 9/28 的 202KB（4,055 行），`trading_bot_v6_main.py` 超过 41 万字节，`advanced_risk_controls.py` 从 32KB 长到 150KB。RR-CP 主计划要求独立签名信任锚、独立存储域 checkpoint、11 项真实演练、20 个 clean session 和多角色独立审查，才能开始一个 **$100 日损上限** 的 Paper 验证。当前状态是 `0/20`、`no-launch hold=ACTIVE`，并且被四项只有 John 本人能提供的外部输入卡住。

结论：继续在旧结构上加固，边际收益已经很低；每一个新补丁都要在两万行耦合代码里证明自己不破坏别的东西。V6-Lite 的做法是**把硬边界保留下来，但用更少的代码、更强的结构性保证去实现它们**。

---

## 2. 设计原则

1. **硬边界保留，流程层删除。** 资金安全规则一条不少；审批、签名、审查角色、证据仪式全部移除或降为一个本地文件。
2. **用数据库约束简化账本一致性。** SQLite 主键保证相同 `exec_id` 不重复入账、相同退出阶段不重复占用；它不能单独证明 broker 已撤单，也不能替代账户级进程互斥或防止不同意图重复卖出。
3. **一个交易进程、一个券商与账本执行循环。** 保持 broker 与持仓更新的单一所有者；行情扫描、邮件和 webhook 的等待必须有界，不能阻塞止损检查。当前同步实现需要按 L5 调整，不因精简线程而牺牲退出及时性。
4. **Broker truth 永远优先。** 状态字符串只是提示；成交只认 execution；持仓只认 broker positions；差异不自动修正，只阻断新开仓。
5. **与旧 V6 物理隔离。** V6-Lite 不 import `trading_bot_v6`，不读写旧 `state/`，使用独立 client ID（131），运行状态放在仓库外（默认 `%USERPROFILE%\.v6lite\<mode>\`），天然不触发旧测试的 artifact guard。
6. **Paper 与 Live 同一套参数。** Paper 账户 NLV 约 $1M，但 V6-Lite 用 `min(NLV, max_capital_usd)` 定仓，Paper 演练就是按 $10k 真实规模在跑。

---

## 3. 保留什么、砍掉什么

### 3.1 保留的资金安全硬边界（目标与已有实现）

下表描述设计目标与现有实现路径，**不表示所有边界已被证明**。本机 67 项测试覆盖了主要正常与异常路径；第 8.1 节记录确定缺口，第 8.2 节规定补齐它们的工作包。

| 硬边界 | 来自哪次教训 | V6-Lite 实现 |
|---|---|---|
| 同一笔成交只记一次 | CAT 220→110 | `fills.exec_id` 主键 + `INSERT OR IGNORE`，成交与持仓更新在同一事务内 |
| 每个止盈/止损阶段只触发一次 | INTC、GOOGL 重复卖出 | `exit_stages(position_id, stage)` 主键；阶段在下单前写入 |
| `Cancelled` 不等于失败 | GOOGL TP1 `Cancelled→Filled` | 状态只作提示；进入 grace 期后用 `reqExecutions` 结算；仍在 working 则继续跟踪 |
| `Submitted, filled=0` 不是终态 | 同上 | 只有 execution 决定 FILLED；超时才撤单 |
| 下单前持久化意图 | Durable Intent Ledger（A3.1） | `intents` 表先写 `NEW` 再调用 broker；崩溃后按 working orders + executions 恢复 |
| 发送结果不可证明时不得重试 | UNKNOWN ≠ 失败 | 发送异常可能标记 `UNKNOWN`；现有恢复仍会凭缺失查询结果转 `CANCELLED`，须由 L3 补齐终态证据与数量预留 |
| Broker truth 对账 | Runtime/Broker Reconciliation | 启动、每 5 分钟、每次成交后、重连后对账；任何差异阻断新开仓，不自动覆盖 |
| 不会卖出超过实际持仓 | 防止意外做空 | 最终发送边界重新读取 broker positions；数量不匹配的标的暂停退出并告警 |
| 账户绑定 | Target Account Binding | `connect(account=...)`；managed accounts 不含目标账户即拒绝启动；state 文件永久绑定一个账户+模式 |
| Protected Symbols | MSTR 长期持仓 | 配置层禁止与可交易名单重叠；对账忽略；发送边界拒绝 |
| 只用限价单 | TPSL Path B（7/17 裁决） | 适配器里根本没有市价单函数；入场 mid×(1+0.3%)，出场 bid×(1−1%) 向上取整到分 |
| 持久化计划优先 | ATR-NAN-1（7/27 裁决） | 首次成交计算并存库；现有代码在退出阶段建立前可按后续部分成交的加权成本调整价位，阶段存在后冻结；不使用新行情重算 ATR；具体部分成交规则由 L3 固定并测试 |
| 定仓失败关闭 | 1 股下限 bug | 数量 < 1 直接放弃，不向上取整 |
| Kill switch | 手动停机 | CLI 写入数据库，引擎每个 tick 重新读取；只阻断新开仓，退出照常 |
| 日损停止 | RR-CP-001 | 已实现 + 手续费 + 未实现变动 ≥ 限额即锁定；当日不可 resume |
| 连续亏损冷却 | RR-CP-003 | 2 笔亏损 → 60 分钟冷却；零盈亏不计也不重置 |
| 每日提交上限 | RR-CP-004 | 在发送前、同一事务内计数；被拒/撤单也计数 |
| 单实例 | 锁文件 | 当前仅检查指定路径后写锁文件，尚不是原子或账户级互斥；跨旧 V6 的禁止并行条件由 L2 与操作隔离共同落实 |

### 3.2 砍掉的流程层

签名审批工件与独立审查者签收、独立存储域 checkpoint 与信任锚部署、路由审批 journal 与 conId 外部权威数据摄取、Execution Confidence 评分与 readiness 证据累积器、历史 lineage 回溯 journal、20 个 clean session 的独立计数与复审、多 Agent 角色分工（Coordinator / Auditor / Reviewer）作为上线前置条件、Discord 情报融合、Research Watch、Rotation advisory、Pattern memory、Adaptive feedback。

**不是说这些没有价值**，而是它们都不是"$10k、2 个仓位、只做多、限价单"这个风险规模上线的必要条件。Discord 情报等 alpha 相关模块可以在实盘稳定之后，作为独立的信号源重新接入。

### 3.3 用什么替代了"审批"

一个轻量实盘门（`v6lite/approval.py`），三个条件同时满足才能启动 live：

1. 配置文件 `mode = "live"`（必须是 live 端口、非 DU 账户、有资金上限、配置了远程告警）；
2. 命令行显式加 `--live`；
3. `approve-live` 写出的本地批准文件：绑定账户 + **配置指纹**（行为参数改动使批准失效，当前 `state_dir` 不计入指纹）+ 过期时间（签发最长 30 天）。状态目录迁移仍须通过账户身份与互斥检查。

没有任何环境变量能把 V6-Lite 切到实盘。

当前只在 `cmd_run` 启动时检查批准；运行中到期或执行 `revoke-live` 不会阻断后续入场。配置还允许 `require_remote_in_live=false` 绕过必需告警。L2/L5 将补上最终入场前批准复核和不可旁路的 live 告警条件；失效时阻断新入场，已持仓的保护退出继续运行。

conId 的处理方式也简化了：不再要求外部权威数据，而是**首次 qualify 时把 conId 钉在本地数据库**，之后如果变化（公司行动、合约歧义）就阻断新开仓等人工确认。

---

## 4. 架构

```
                 ┌────────────── v6lite/__main__.py (CLI) ──────────────┐
                 │ run · status · kill · resume · ignore · adopt ·      │
                 │ clear-anomaly · approve-live · revoke-live · report  │
                 └──────────────┬───────────────────────────────────────┘
                                │ 一个线程：pump(1s) → tick()
                 ┌──────────────▼──────────────┐
                 │        engine.py            │
                 │ 事件→意图结算→对账→日损→     │
                 │ 退出→定时扫描入场→日报       │
                 └──┬──────┬──────┬──────┬─────┘
        strategy.py │  risk.py  exits.py  sizing.py   (纯函数)
                    │
     ┌──────────────▼───────┐     ┌──────────────────────┐
     │ store.py (SQLite WAL) │     │ broker/ibkr.py        │  唯一 placeOrder
     │ intents · fills ·     │     │ broker/fake.py (测试) │
     │ positions ·           │     └──────────────────────┘
     │ exit_stages · risk ·  │     ┌──────────────────────┐
     │ controls · audit      │     │ alerts.py            │  log + 邮件 + Discord webhook
     └───────────────────────┘     └──────────────────────┘
```

| 文件 | 行数 | 职责 |
|---|---:|---|
| `engine.py` | 778 | 主循环、订单生命周期、恢复、对账调度、退出与入场流程 |
| `store.py` | 532 | SQLite 模式、事务、一次性成交与阶段、控制开关、审计 |
| `broker/ibkr.py` | 275 | ib_insync 适配器（单线程、单一下单点、orderRef=intent_id） |
| `strategy.py` | 251 | 从 `market_scanner.py` 原样移植的指标与评分 |
| `config.py` | 242 | TOML 配置、校验、指纹 |
| `__main__.py` | 198 | CLI |
| `broker/fake.py` | 192 | 可复现 IBKR 各种异常行为的测试券商 |
| 其余 | ~1,080 | 风控、退出、定仓、对账、告警、日历、审批/锁、日报、模型 |
| **合计（含测试用 FakeBroker）** | **约 3,500** | 对比旧 V6 约 7 万行（`trading_bot_v6/` 约 6.4 万行 + 根目录入口脚本） |
| 测试 | ~1,020 | 67 个测试（含 5 组随机浸泡测试） |

---

## 5. 订单生命周期

```
NEW ──send ok──► SENT ──部分成交──► PARTIAL
 │                │  └─超时─► CANCEL_REQUESTED ─┐
 │                └─收到 Cancelled/Filled 提示─► CANCEL_SEEN (grace 5s)
 │                                              ▼
 │                        settle：reqExecutions + openOrders
 │                          ├─ 仍在 working → 回到 SENT/PARTIAL
 │                          ├─ 成交量 ≥ 数量 → FILLED
 │                          └─ 否则 → CANCELLED（可能带部分成交）
 ├─send 异常且可能已发出──► UNKNOWN（阻断入场，每 60s 尝试 settle）
 └─send 明确失败────────► REJECTED
```

退出阶段（TP1/TP2/TP3/STOP）在下单前先占用 `exit_stages` 主键。阶段目标数量已卖出 → `DONE`（TP1 同时启用保本止损）；未卖出且已证明上一订单终结 → 按剩余数量和当前报价重试，最多 3 次；仍失败 → `FAILED` + 告警 + 锁定异常（STOP 为 P0，TP 为 P1）。

若 TP 或部分成交的 ENTRY 在途而价格跌破止损，现有代码先请求撤单，等待结算后才允许 STOP。这不是立即完成止损；L3 必须验证“撤单 → 补齐迟到成交 → 按真实剩余量退出”的完整序列。`UNKNOWN`、不完整订单快照、跨日缺失执行记录均不得仅凭空列表释放重试。`Ctrl+C` 撤单也必须覆盖可能已发送的 UNKNOWN，不能承诺当前实现已撤掉所有订单。

---

## 6. 首个实盘风险包络（Paper 同参数）

| 参数 | 值 | 说明 |
|---|---|---|
| 资金上限 `max_capital_usd` | $10,000 | 定仓用 `min(NLV, 上限)` |
| 单笔风险 | 1%（$100） | 数量 = $100 ÷ (2.5×ATR) |
| 单笔名义上限 | $5,000 | |
| 最大持仓数 | 2 | 同一板块最多 1 个 |
| 每日提交上限 | 4 次 | 发送前计数 |
| 日损上限 | min($150, 1.5%×当日资金) | 含未实现与手续费，锁定当日 |
| 连续亏损 | 2 笔 → 冷却 60 分钟 | |
| 可交易标的 | GOOGL AAPL IWM ABT IBB PANW META TSLA SPY AMZN | 沿用 RR-CP 已选的 10 只 |
| 扫描时间（ET） | 10:00、13:30 | 入场窗口 10:00 至收盘前 30 分钟 |
| 订单类型 | 当前入场/程序化退出仅 LMT、DAY、仅常规时段 | L7 计划增加 GTC Stop-Limit 保护，须单独通过 Paper 验证；本次未修改配置或下单代码 |

策略目标（EMA/ADX/RSI/量比评分 ≥60、VIX>30 提高到 75、ATR 止损 2.5×、TP 3/5/8×、TP1 后保本）沿用 V6 的规则。本次未修改任何参数。

**原交付记录：** 云端曾用 3,000 组随机 K 线对比 `market_scanner.scan_ticker`，报告 0 差异、563 组 BUY；仓库当前没有可重跑的该比较脚本或样本，因此本次不将此记录认定为本机复核完成。现有单元测试验证了一组 BUY 样本。L6 将保存可复现的同输入比较，并核对 yfinance 与 IBKR 的交易日、复权、回看长度、当天未完成日线及成交量口径；算法移植相同不代表不同数据源的实时信号必然相同。刻意差异仍为 VIX 缺失时提高门槛、非有限值 K 线超过 3 根时失败关闭。

---

## 7. 验证情况

原交付在云端沙箱（Python 3.11、ib_insync 0.9.86）中报告 **67 passed**。本次在 Windows、激活 `venv_v6`、Python 3.11.9 后运行 `python -m pytest v6lite/tests -q` → **67 passed in 10.24s**。测试采用 FakeBroker / FakeIB，无 Gateway 连接。已有用例覆盖：

- 身份：账户不匹配拒绝启动；state 文件永久绑定账户
- 入场：ET 扫描时间、风险定仓、限价计算、发送前计数、价格偏离 3% 放弃、每日提交上限（含被拒）
- 成交真相：重复 execution 只记一次；`Submitted/0` 不是失败；`Cancelled→Filled` 竞态（成交只通过 reqExecutions 可见）；撤单无成交正确结算；发送异常 → UNKNOWN → 在 broker 找到订单后恢复；崩溃前已写入但未发送的意图重启后正确关闭
- 退出：TP1 在用例的多次 tick 与重启后只执行一次；TP1 后保本止损卖出剩余；跌破止损时请求撤掉在途 TP；STOP 重试 3 次后 P0 升级；broker 持仓不足的测试场景拒绝卖出。查询间成交与终态后的迟到成交尚需补测
- 控制：夜间 Gateway 例行重启只记日志不发告警；kill 阻断入场但不阻断退出；日损锁定且当日不可 resume；连续亏损冷却；启用必需告警时 live 初始告警不可用阻断入场；干净状态 dry-run 不下单；关机撤掉用例中的 SENT 订单；成功路径的收盘日报只生成一次
- Broker truth：未托管持仓阻断入场，`ignore`/`adopt` 解除；Protected Symbol 不阻断也不交易；外来成交锁定异常；断线阻断、30 秒节流重连、重连后补回离线期间的成交
- 适配器：连接绑定账户/client ID；订单为 LMT/DAY/非盘外、带 orderRef；最终边界拒绝非法请求；只读会话不能下单；下单异常标记为"可能已发出"；回调正确转换；外账户 execution 被忽略
- 静态：整个包只有 1 处 `placeOrder`，没有市价单；不依赖 `trading_bot_v6`
- 浸泡测试：5 个固定随机种子 × 3 个交易日，券商随机成交/部分成交/重复回调/只在 reqExecutions 可见的成交/撤单与成交竞态/频繁断线；每个 tick 后检查不变量（无意外做空、每个持仓最多一个 active intent、每个 execution 只记一次、价位顺序正确、异常阻断入场）。本机这 5 组测试全部通过；现有生成器不能代替迟到成交、不同 client、跨日真实查询和进程崩溃的专项覆盖。
- 断线细分：Gateway 与 IBKR 服务器断开（1100，API 套接字仍在）时保持暂停，直到 1101/1102 恢复后重新对账

**本次另做的临时离线复现（不修改源码，状态仅在临时目录）：**

| 场景 | 当前实际结果 | 结论 |
|---|---|---|
| 已有 PENDING STOP，切换 dry-run 后再次 tick | FakeBroker 订单数 2 → 3，新增 STOP | 干净状态 dry-run 测试不足以证明全路径只读 |
| 同一成交先无手续费，再重放附带 $2 手续费的 execution | 成交数量未重复，但费用仍为 $0 | execution 去重与费用补录必须分开 |
| sizing equity=$3,000，已部署 $2,800，price=$100，ATR=$1 | 新订单 $1,200，总部署 $4,000 | 总资金余量仍按 $10k 配置上限计算，没有按实际较低 equity 收紧 |
| 告警先成功，下一次所有通道发送失败 | `send=False`，`healthy=True` | 24 小时成功历史不能代表当前通道可用 |

这些复现说明“67 passed”是现有测试基线，不是安全缺口已关闭。后续将复现固化成回归测试。

**尚未验证（需要 John 在修复后完成）：** 真实 IB Gateway（Paper）的订单同步、client 可见性、execution/commission 恢复；实时行情与历史数据权限；邮件/Discord webhook 实际送达。Windows 多进程锁与 CLI 集成路径也需补测试。

---

## 8. 依据仓库代码调整后的开发计划

### 8.1 当前缺口与优先级

P1 表示必须在相应 broker 阶段前解决；P2 为可并行的可观测性与比较工具改进，不作为 G2/G3 准入门槛。用于实际风控的账户、报价、日损、费用和告警判断仍归第一/第二批，不能移到 P2。下列行号对应 2026-09-30 本次评审的源文件，开发后需随实现更新。

| 优先级 / 门槛 | 代码证据与缺口 | 工作包 |
|---|---|---|
| P1 / 只读演练前 | `engine.py:509–543` 的已有阶段重试无 dry-run 判断；`broker/ibkr.py:199–205` 撤单无 read-only 判断。库的 `readonly=True` 不是禁止所有写操作的安全开关 | L1 |
| P1 / 只读演练前 | `approval.py:124–134` 非原子写锁、释放不核对所有者；同账户不同 `state_dir` 无互斥。旧 V6 锁是 JSON，Lite 按首整数解析；旧入口也不会反向检查 Lite | L2a |
| P1 / 只读演练前 | `__main__.py:42–43,174–185` 控制命令打开数据库后未检查 account/mode，可用错配配置改另一个账户的控制状态 | L2a |
| P1 / Paper 前完成离线验证 | `__main__.py:66` 仅启动检查批准，运行时撤销或过期不阻断新入场 | L2b |
| P1 / Paper 前 | `engine.py:267–284,317–344` 根据 today's executions 和本地 openTrades 缓存缺失直接关闭意图；查询不完整、跨日、改变 client ID、查询间成交时不能据此证明终态 | L3 |
| P1 / Paper 前 | `engine.py:710–724` 关机只撤 SENT/PARTIAL；UNKNOWN 在末尾被发现为 working 后直接断开，可能遗留订单 | L3 |
| P1 / Paper 前 | `store.py:411–423` 迟到 ENTRY 成交给已 CLOSED 仓位增加数量而不恢复状态；`453–455` 的 OPEN 查询看不到它。撤单后迟到 SELL 与下一次重试叠加还可能超卖 | L3 |
| P1 / Paper 前 | `sizing.py:37–39` 余量按配置上限而非实际较低 equity 计算；缺少可用资金/购买力约束。`config.py:188–193` 整数检查后保留原值，若干时长/阈值未完整校验 | L4 |
| P1 / Paper 前 | `store.py:369–374` 重复 execution 提前返回，跳过恢复时带回的费用；`broker/ibkr.py:232–246` 对非整数 shares 仅日志后四舍五入 | L4 |
| P1 / Paper 前 | `engine.py:122,598–604` 串行同步历史请求后仍用 tick 开始的 `now` 校验报价；`alerts.py:46,63` SMTP/HTTP 同步等待，阻塞退出处理 | L5 |
| P1 / Paper 前完成离线验证 | `config.py:194–199`、`risk.py:108–109` 允许关闭 live 必需告警；`alerts.py:80,106–111` 所有通道失败后健康仍保留最多 24 小时 | L5 |
| P2 / 并行改进 | `engine.py:732–742` 日报先标 sent 后发送，失败不补发；`__main__.py:99–118` status 只有持久化账本，无连接/对账/行情/告警的有效期 | L5/L6；基础送达证据归第二批，展示与自动补发不单独阻塞 |
| P2 / 并行改进 | `strategy.py` 与 `test_units.py` 尚无原 3,000 组比较的可复跑工件；真实数据源的口径差异也未验证 | L6；真实取数仍在 G2 验证，不等待比较工具 |
| P1 / Live 前 | 只有进程内、RTH 的止损检查；停机、断网和盘外仍持仓时没有 broker 端保护。查看 status 不能解除这项风险 | L7 |

### 8.2 开发工作包与最小验收

不重写已有 3,500 行核心，不恢复旧 V6 的签名、checkpoint 或 20-session 流程。根据本轮确认的优先顺序，按以下三个批次推进；L 编号保留用于追踪缺口，不再表示串行开发顺序。每批先固化具体失败序列，再做最小实现与对应回归。

| 批次 | 内容 | 阶段门槛 / 粗估工作量 |
|---|---|---|
| **第一批：开始只读连接所需的最小边界** | L1 + L2a：全路径只读、独立演练状态、CLI account/mode 校验、原子账户锁及旧入口操作隔离 | G2 前；约 1–2 个开发日 |
| **第二批：Paper 真单所需的执行与资金边界** | L3 + L4：终态/恢复/迟到成交、UNKNOWN 关机、资金余量/费用/日损；L5：告警健康、live 告警不可关闭、关键 I/O 等待预算与报价复核；L2b：运行中批准复核 | G3 前；约 4–7 个开发日。live 条件用临时状态与测试通道离线验证，不签发真实批准 |
| **第三批：停机保护** | L7：Codex 已选定 GTC Stop-Limit + OCA 数量协调，详见第 8.4 节；独立开发和 Paper 验证 | Live 前；约 3–5 个开发日，真实 broker 演练等待另计 |
| **并行：L6** | 策略比较工件、status 展示、日报自动补发/运维便利、日历样本扩展 | 约 1–2 个开发日；不阻塞 G2/G3。最低限度的安全状态和送达证据由第一/第二批的日志、审计与人工核对提供 |

第一批完成即可由 John 开始 G2，第二批可以在 G2 观察期间推进。L7 可在 Paper 期间开发；L6 进度不延后 broker 阶段。工作量基于代码审查粗估，新增复现或真实 broker 差异会更新估算，不压缩安全验收。

| 包 / 顺序 | 修改范围与交付 | 验收条件 |
|---|---|---|
| **L1：全路径只读隔离** | `engine.py`、`broker/ibkr.py`、CLI 与测试。所有 submit/cancel 共用只读边界；dry-run 使用独立演练状态，不推进正式订单/退出阶段 | 初始状态含 SENT、UNKNOWN、PENDING TP/STOP 时，startup/tick/shutdown 的 broker 写调用均为 0；不消耗正式阶段重试，不污染正式风险日和报告。读到空缓存不能关闭正式意图 |
| **L2a：身份、互斥与 CLI 控制（第一批）** | `approval.py`、`__main__.py`、`store.py` 与 Windows/CLI 测试。原子账户级锁及所有者校验；所有命令校验 account/mode | 两个进程竞争同账户，即使不同状态目录也仅一个成功；错配 CLI 不写库；坏锁/PID 不明失败关闭。明确旧 V6 的 JSON 锁、其他 cwd、自定义路径、main/auto runner 两个入口的隔离限制 |
| **L2b：运行中批准复核（第二批）** | 最终入场发送前检查 account、行为指纹、有效期和撤销状态；失败时只阻断新增风险 | 批准缺失/损坏/过期/撤销阻断入场，保护退出照常；运行中的引擎跨到期时刻与撤销后均不得新增订单。全部批准测试只用临时目录 |
| **L3：订单终态、迟到成交与退出恢复** | `engine.py`、`broker/ibkr.py`、`store.py` 与事件回放测试。完整查询后才释放订单预留；固定或校验持久化 client 身份；无法证明则保留 UNKNOWN；关机发现并撤销本系统全部 working orders | 缺失缓存、请求超时、查询之间成交、跨日重启、client 变更不产生自动重发；迟到 BUY 不形成 CLOSED+qty_open>0 的隐形仓位；迟到 SELL 与重试不超卖；部分 ENTRY/TP 撤单后按最终数量 STOP；UNKNOWN 关机不静默遗留订单，无法确认时明确告警与订单清单 |
| **L4：资金、日损、数字与费用完整性** | `sizing.py`、`risk.py`、`config.py`、`store.py`、broker 账户/成交模型与测试。部署上限按实际 equity 与配置上限收紧；约束新鲜且币种正确的可用资金；费用补录与成交去重分离；严格校验数字/配置分区 | equity=$3k、已部署$2.8k 时新增不超过剩余$200（并预留在途订单与费用）；资金缺失/陈旧则阻断；同 exec 补回费用但数量/PnL不重算；bool/小数数量/NaN/Inf/负时长/拼错或跨分区键在连接前拒绝；异常成交不得四舍五入入账或伪装为重复 |
| **L5：保护循环与告警健康（第二批）** | `engine.py`、broker 数据请求、`alerts.py`、`risk.py` 与测试。券商与账本仍由一个循环拥有；非交易 I/O 有界并让出执行；发送前重新取时与校验；记录基础送达/失败状态 | 历史请求超时或 SMTP/HTTP 慢响应时，退出检查仍满足当前配置的 30 秒周期；实际报价年龄超过当前 15 秒限制不得入场。全部告警通道失败后及时阻断 live 入场、退出照常；live 无通道或关闭必需告警均拒绝。不能把去重命中或本地生成日报当远程送达 |
| **L6：可复现策略与运维便利（并行）** | 策略比较测试/离线工具、`status`、报告、README、示例配置注释。保存固定种子或冻结样本；健康快照带更新时间和阻断原因；补日历/时区样本；日报自动补发 | 比较结果可复跑，数据源差异明确；status 无库不创建运行状态，过时快照显示 unknown/stale；日报失败后可重试/重启补发，成功后不重复。工具未完成时，G2/G3 使用日志、审计、broker 清单与人工补发记录提供证据，不等待 UI/比较工具 |
| **L7：GTC Stop-Limit 停机保护（第三批）** | 方案由 Codex 选定；代码仅落在 `v6lite/`。持久化券商保护单身份，TP/STOP 与它统一数量协调，关机保留已确认的保护单 | 必须验证保护确认、部分成交、TP 减仓、OCA 竞态、进程强退、Gateway 重启、重连认领及跳空未成交。不能双卖，保护不明时阻断新增风险；未通过真实 Paper 验证不进入 Live |

L4 还需明确日损口径：当前实现使用当日首个 RTH 未实现盈亏标记。补测隔夜持仓、盘中启动、先恢复当天成交、断线跨日、延迟手续费以及阈值恰好达到上限；无法证明基准时阻断新入场。费用晚到后的连续亏损结果需采用可审计的结算/修订规则，不能默默沿用缺费用的结果。该工作定义计量完整性，不改第 6 节的风险值。

**跨旧系统互斥的边界：** 在旧代码冻结条件下，Lite 无法保证一个之后启动且不识别 Lite 锁的旧入口自动拒绝。因此 L2 采用账户级 Lite 互斥 + 旧进程检查 + John 停用旧入口的操作隔离；无法确认排他运行则不进入 broker 阶段。不同 client ID 只避免 API 身份冲突，不证明同账户互斥。本计划不借此要求修改旧 V6。

### 8.3 阶段推进与时间安排

| 阶段 | 开始条件 / 规划时间 | 交付与通过条件 |
|---|---|---|
| **G0：本机离线基线** | 已完成，2026-09-30 PT | 67 项既有测试通过；记录第 8.1 节缺口与第 7 节复现 |
| **G1：分两批离线整改** | 下一步；第一批约 1–2、第二批约 4–7 个开发日；L6 并行且不阻塞 | 每批聚焦回归通过；第二批集成冻结后一次完整 `v6lite/tests` 通过。用行为覆盖验收，不固定未来测试数为 67；尚未完成第二批不能进入 Paper 真单 |
| **G2：真实 Paper 只读验证** | **第一批 L1+L2a 完成即可开始**；预计 3–5 个交易日，由 John 启动 | broker 写调用为 0；完成连接、账户、行情、历史数据、订单可见性与远程送达验证；演练状态独立。第二批继续开发，L6 不阻塞。此阶段不产生真实新订单，不能证明成交恢复路径 |
| **G3：Paper 真单验证** | **第一/第二批与 G2 均通过**；不等待 L6；按第 6 节原参数运行 **10 个交易日** | 累积日报、订单/成交/费用记录。缺少自动报告功能时用人工核实/补发记录验收。修复资金完整性或订单状态机后重新收集受影响证据；不压缩观察期抵消延误 |
| **G4：Go / No-Go** | G3 达标且 **L7 实现与真实 Paper 保护验证通过**；日期由通过时点确定 | John 回看记录，确认风险包络、告警、实盘批准运行时有效性、停机保护及账户排他运行，然后决定是否进入试点；L6 工具完成度不是独立放行条件 |
| **G5：小资金 Live 试点** | John 单独批准并亲自启动；沿用 $5k–$10k、最多 2 仓的原目标，具体资金由他确定 | 观察 4 周，执行第 10 节；每次风险参数变更重新批准，涉及策略/执行行为的变更先回 Paper |
| **G6：扩容评估** | G5 达标后再决定 | 每次只改一个变量；不预承诺 12 月扩容 |

第一批是 G2 的最小前置；第二批与 G2 重叠，完成后进入 G3；第三批在 Live 前独立验收；L6 全程并行。以上是规划区间，不是交付承诺；原 10/12 Paper、10/26 Go-Live 不再作为期限。**策略盈利能力尚未被证明**；此计划先证明执行和会计正确，再观察收益。

**验证范围与变更纪律：** 开发只触碰 `v6lite/`、其测试和对应文档；不导入旧 V6，不改变 `.env`、旧 `state/` 或 no-launch hold。每包运行聚焦测试，集成完成后跑一次完整 Lite 测试；不为 docs-only 调整跑旧 V6 全量测试。保留仓库三次 flaky 的 fix-before-commit 规则。本次没有 stage、commit、生成批准文件或启动任何交易进程。

### 8.4 L7 方案选择：券商托管的 GTC Stop-Limit

**Codex 的选择：GTC Stop-Limit（`STP LMT`）保护持仓，与 TP/程序化 STOP 通过 OCA 和持久化数量预留协调。** 这是后续实现方向，当前代码仍只有 LMT/DAY。理由是它沿用持久化 ATR/保本价位与限价执行约束，并让已被券商确认的保护请求在本机进程退出后继续存在；不把“程序持续运行”作为唯一止损条件。

实现与验收边界：

1. 保护价格取持久化 stop/breakeven，数量跟随实际成交与减仓；不另设更宽止损倍数，不在本次调整风险参数。保护单意图在发送前持久化，记录 orderRef/orderId/permId、OCA 组与 broker 确认。先验收部分 ENTRY 成交如何被保护，不能只覆盖全额成交后再挂单。
2. TP 与保护单不能作为两条互不知情的 SELL 路径。采用带 overfill protection 的 OCA，并在真实 Paper 中验证全仓保护与分批 TP 的不同数量、部分成交、减量和撤改行为；不能把 OCA 的比例减量直接假定为正确剩余持仓。程序化 STOP 修改/接管同一保护关系，不能额外发送一个未协调的全仓 SELL。
3. 启用 L7 后，正常关机撤销尚未完成的 ENTRY 和程序化 DAY 单，**保留已确认且数量正确的 GTC 保护单**。重启先同步、认领、对账再恢复 TP；身份或数量不明时不盲目补发。保护被拒绝、取消或状态不明则停止新增风险，并明确报告未保护数量。
4. 在 FakeBroker 回放之外，由 John 在 Paper 验证强退进程、断开本机、Gateway 重启后保护状态，TP 部分成交后数量协调，以及止损触发但限价未成交的处置。该验证是 Live 前置条件，不因 10 个 Paper 交易日已结束而省略。
5. **剩余限制必须保留：** Stop-Limit 触发后仍可能因价格跳过限价而不成交；GTC 不代表盘外可触发。首版保持现有 RTH 约束，盘外跳空及无法成交的敞口需在实盘决策时明确接受，不能承诺日损上限等于最大可能损失。若要求全天强制平仓或保证成交，需要重新讨论执行约束，不在本次计划里自动改成市价路径。

官方依据：IBKR 的 [订单类型说明](https://www.interactivebrokers.com/en/trading/ordertypes.php) 明确 Stop-Limit 存在不成交风险；[TWS API Order 字段说明](https://www.interactivebrokers.com/docs/tws-api/ref/order) 定义 GTC、outsideRth 和 OCA with-block 行为。上述具体架构为结合 Lite 代码作出的设计选择，实际账户/标的支持与恢复行为必须以 Paper 验证为准。

### 8.5 Windows 独立复核后的剩余安排

2026-09-30 在 Windows 仓库 venv 复跑 `v6lite/tests`：**104 passed in 17.59s**，无跳过；交付 32 个文件的 SHA-256 全部匹配原整改报告。Windows 原子账户锁跨进程、崩溃释放与同时争抢通过。完整逐行裁定、八项设计判断和反证见 [Codex 独立复核记录](C:/Dev/TradingV6/docs/V6_LITE_CODEX_REVIEW_2026-09-30.md)。代码和原整改报告未改动，测试通过不代表第二批已完成。

| 顺序 | 剩余工作 | 门槛与粗估 |
|---|---|---|
| 第一批收尾 | 旧锁进程 AccessDenied 不能按进程消失放行；确认旧入口停用及不会自动恢复。L1 和 Lite Windows OS 锁的离线边界已通过 | 正式运行前修失败关闭；约 0.5–1 个开发日。限定独立状态、Paper、只读的 G2 可开始真实观察，尚未验收通过 |
| 第二批先修订单 | 孤立服务器订单阻断；跨日 UNKNOWN 在 shutdown/结算也必须保留；已有 managed 差异的审计恢复；迟到 ENTRY 多批归属；先对账的迟到 SELL 仍能恢复；execId/费用更正；撤单与重试的晚到成交数量预留 | G3 前。不能把数量差异永久冻结退出或更正重复入账归为可接受的人工风险 |
| 第二批修资金与日损 | AvailableFunds 在途预留和快照就绪/时效；上一收盘实际数量与持有日证据；收盘缺费结果保留待定；扫描入账后刷新日损 | G3 前；最终 ENTRY 必须使用最新风险，固定 $2 预留须证明适用于实际费率与股数 |
| 第二批修循环与健康 | LIVE 最终发送实时复核告警健康；扫描/VIX/adopt/上一收盘等所有慢查询按下一次保护期限安排预算 | G3 前离线验收；测试实际拒单和最大检查间隔。批准运行时复核已通过 |
| 第三批 L7 | 继续第 8.4 节已选的 GTC Stop-Limit + OCA 数量协调方案，依赖修复后的订单/恢复模型 | Live 前；原 3–5 个开发日初估保留，真实 Paper 验证等待另计 |
| 并行 L6/P2 | 日报 ID 跨重启唯一、失败重要消息重试、status/report 查询身份、SMTP 端口严格化、剩余日历/真实数据源比较 | 不独立阻塞 G2/G3；关键风控证据不能用展示工具缺口豁免 |

第二批剩余修复、回归和独立复核建议预留 **6–10 个开发日**；原 4–7 日是初次范围估计，不是本次复核后的剩余承诺。真实 Gateway 行为留 G2，成交/撤单/费用路径留 G3；G2 的 3–5 个交易日与 G3 的 10 个交易日安排保留。**当前 G3/Live 均为 NO-GO**，L7 未实现；修订单与会计后须重新收集受影响证据。

---

## 9. Paper → 实盘的验收标准（10 个交易日）

- 没有任何无法解释的 broker/runtime 数量差异（对账告警只允许出现在已解释的场景，例如刚成交后的秒级延迟）
- 没有重复订单、重复成交、重复退出、意外做空或 CLOSED 却仍有数量的仓位；撤单后迟到成交不能靠事后 oversell 告警补救
- 每笔成交的手续费完整性可核实，每笔平仓有扣费后净盈亏；缺费用时显示待结算，恢复后可补录，不以 $0 冒充完整
- 至少一次自然发生的完整入场 → 退出（止损或止盈）
- 三次真实 Paper 演练全部按预期：kill 后不再入场但止损照常；盘中重启后状态完整恢复；Gateway 断开后阻断，重连后补回成交/费用并恢复行情。离线另覆盖跨日恢复、UNKNOWN 关机、部分 ENTRY/TP 撤单后 STOP、迟到成交以及告警失效
- 邮件（或 webhook）每天收到核实后的日报，发送失败有自动或人工补发记录；记录真实送达，不能仅以本地文件存在或 sent 标志验收。L6 自动化未完成不延后阶段
- 所有 UNKNOWN 均已通过 broker 证据解释，无未解决的意图或异常；出现并正确恢复的 UNKNOWN 是必要场景证据，不要求假装它从未发生
- 账户/配置/连接/对账/行情/告警/入场阻断原因有新鲜、可核实的证据；L6 status 展示未完成时使用日志、审计和 broker 清单核对，不把旧 status 输出当就绪
- live 的告警不可旁路、批准到期/撤销阻断入场且不影响退出；L7 的 GTC Stop-Limit/OCA 保护已通过真实 Paper 验证，同账户排他运行条件明确

不要求固定交易笔数，也不以 Paper 盈利作为门槛（与原项目原则一致）。

---

## 10. 实盘试点运行规则

**立即停止（`kill` 并人工检查）：** 任何 P0 告警；broker/runtime 数量差异无法在 5 分钟内解释；出现不是 V6-Lite 发出的成交；止损单连续 3 次未成交；日损锁定连续 2 天出现。

**扩容条件（4 周后评估，每次只改一个变量）：** 零资金完整性事故；实际滑点与 Paper 同量级；净盈亏扣除手续费后不为负。

**每日：** 盘前确认 Gateway 登录、连接/对账/行情/告警符合运行条件、broker 的保护单身份与剩余数量正确；优先使用新鲜健康快照，L6 未完成时核对日志/审计/broker 清单。收盘后读已送达日报并在"Operator notes"里记两句话。Kill 只阻断新开仓，不等于撤掉在途入场或把持仓平掉；异常时同时核实 broker 的真实订单与持仓。

---

## 11. 阻塞项与保留限制

**必须关闭的缺口：** dry-run 写路径、非原子锁/错配账户控制归第一批；终态证据与迟到成交、UNKNOWN 关机、低 equity 资金余量、费用补录、循环阻塞、告警健康和批准复核归第二批，按第 8 节门槛完成。L6 并行且不阻塞，证据可用最小日志/审计与人工核对提供。撤单后可能多卖不能降为“后续发现再人工处理”。Live 前必须完成 L7 保护实现与验证，查看 status 不构成保护。

保留的产品范围与需要明确处理的限制：

1. **扫描时段被阻断即作废。** 该日该 slot 不补扫；保留当前行为，L6 让跳过原因可见。
2. **手续费晚到与 execution 更正。** 费用恢复与亏损结果结算按 L4 修复；execId 更正当前会被当成新成交，L3/L4 需定义识别/冻结处置与回归，不能无声重复入账。无法自动解释时保留异常并人工核对 broker，不伪造补账。
3. **只做多、单账户、单策略。** 做空、多策略、Discord 情报、Research/Rotation/反馈模块延后，不进入本轮上线关键路径。
4. **Gateway 仍需手动登录。** 自动重启安排在收盘后；真实断线与 1100/1101/1102 恢复是否重新同步行情和订单由 G2/G3 验证，不以 FakeBroker 通过代替。
5. **行情订阅。** Live 保留实时行情要求；Paper 的延时行情只能用于取数诊断，不能代表第 9 节真实执行验收通过。不得放宽新鲜度检查来凑交易次数。
6. **日历与时区。** 已有 ET 和提前收盘单元测试；L6 补 Windows/DST、跨年、提前收盘口径的固定样本核对。遇到不确定交易日不启用入场。

---

## 12. 与旧 V6 的关系

- 旧 V6 **不删除、不修改**，继续保持 `no-launch hold`。V6-Lite 上线后，旧 V6 的 RR-CP 主计划建议正式暂停，避免两套治理并行消耗精力。
- **永远不要让两套系统同时对同一账户运行。** 目前 Lite 的锁文件检查只是单向检查，尚不能保证跨系统互斥；L2 与 G2 前先确认旧 main/auto runner 均停用且不会自动启动。旧持仓按 broker 清单逐个决定保留不托管或接管；接管会启动退出管理，不能自动批量执行 `adopt`。账户重置等操作由 John 单独决定。
- 新文件会改变 RR-CP 使用的仓库 source manifest；如果之后还要继续 RR-CP，需要重新生成 manifest。
- 日后若想从旧 V6 回收某个能力（例如 Discord 情报、Regime/StrategyGate），做法是把它改写成 V6-Lite 的一个独立信号源或过滤器，并先在 dry-run 中对比，而不是把旧模块整体 import 进来。

---

## 13. 快速上手

命令参考见 `v6lite/README.md`。README 尚描述原始骨架行为；当前开发与阶段准入以本文件第 8 节为准，尤其 dry-run 必须先通过 L1，不能直接照原上线日期启动。

---

## 14. 建议追加到 `docs/codex_memory.md` 的决策记录

以下为原设计建议模板，本次未写入 memory/AGENTS，也未把模板中的开发、风险变更或启动规则升级为新增授权。后续单独记录时应把固定上线日期替换为第 8 节验收阶段，并与 John 的实际裁决一致。

```text
## 2026-09-30 PT - V6-Lite Redesign Ruling

### Operator Decision (John)
- Build V6-Lite (`v6lite/`) as the new slim live-trading core; legacy V6 is frozen
  under the existing no-launch hold and will be retired gradually.
- Keep capital-safety hard boundaries: broker truth, exactly-once fills, one-shot
  TPSL stages, UNKNOWN != failed, durable intents, account binding, protected
  symbols, limit-only orders, persisted exit plans, fail-closed sizing, kill switch,
  daily loss latch, loss-streak cooldown, daily submission cap, single instance.
- Remove process layers as go-live prerequisites: signed approval artifacts,
  independent checkpoint storage domains, routing-approval journal, Execution
  Confidence scoring, historical lineage journal, 20-session independent tally,
  multi-role review gates, Discord intel fusion, research/rotation/pattern modules.
- Live gate for V6-Lite = live config + `--live` flag + local approval file bound
  to account and config fingerprint (max 30 days). No environment variable can
  enable live trading.
- Go-live path: batch 1 (L1 + L2a) enables read-only broker validation;
  batch 2 (L3 + L4 + safety-critical L5 + L2b) enables 10 Paper trading days
  using the live parameters ($10k cap, 2 positions). L6 runs in parallel
  without blocking these stages. Implement and Paper-validate L7 GTC
  stop-limit/OCA protection before John's live decision. Dates follow
  acceptance, not the original fixed schedule.
  Every start of Paper or Live is executed by John.
- Codex may develop and test V6-Lite freely; it must not start V6-Lite against a
  broker, write approval files, or change risk parameters without John.
```

建议同时在 `AGENTS.md` 顶部增加一段：

```text
## V6-Lite (active development target since 2026-09-30)
New live-trading work goes into `v6lite/` (see docs/V6_LITE_REDESIGN.md).
Validate with `python -m pytest v6lite/tests -q`. Do not import trading_bot_v6
from v6lite. Legacy V6 remains frozen under the no-launch hold.
```
