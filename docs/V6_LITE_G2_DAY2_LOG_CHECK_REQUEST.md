# 给 Codex 的指令：G2 第 2 天（2026-10-02）日志检查

发出：Claude（提问与复核）。执行：Codex（多 Agent：取证、核对、测试）。
从本轮起两方角色调换：Claude 提问题、给修复指令、复核结果；Codex 负责修代码、核对数据、跑测试。

## 0 本轮边界

本轮**只读**，只做检查和记录，不修代码。

- 不连接 Gateway，不运行 `v6lite run`、`g2_probe` 或任何会连 IBKR 的命令。
- SQLite 一律用 `mode=ro` 打开。不改 `state/`、`.env`、`%USERPROFILE%\.v6lite\` 下的任何文件，也不改旧 V6 的代码。
- 不 stage、不 commit、不 push。
- **机密**：不输出 `.env` 内容、Discord bot token 和 webhook 地址。确认环境变量时只报“存在 / 不存在”和长度。如果日志里出现任何机密，只报文件名和行号，不要引用原文。
- 发现需要改代码的问题，写进“建议修复项”，等 Claude 给出修复指令后再动手。

## 1 多 Agent 分工

5 个子 Agent 并行，全部只读，各自返回“问题编号 → 结论 → 证据（文件:行号 + 不超过 3 行的摘录）”。之后由一个整合 Agent 写记录文件，最后由一个复核 Agent **串行**重新打开每条引用的原始日志行，确认行号和摘录属实。

| Agent | 范围 | 回答问题 |
|---|---|---|
| A 版本与安全边界 | 代码版本、零下单、旧 V6、正式状态 | Q1–Q4 |
| B 行情与扫描 | 连接、行情、历史数据、两次扫描 | Q5–Q9 |
| C 账户与对账 | 账户流、资金、查询耗时、对账 | Q10–Q13 |
| D 告警与异常 | Discord、告警频率、异常、退出 | Q14–Q17 |
| E 探针与对比 | 探针 JSON、yfinance 对比、和第 1 天比较 | Q18–Q20 |

## 2 要检查的文件

- `%USERPROFILE%\.v6lite\paper\dryrun\logs\v6lite_20261002.log`（如有其他日期的文件也一并列出）
- `%USERPROFILE%\.v6lite\paper\dryrun\v6lite.sqlite3`（只读，audit 表等）
- `%USERPROFILE%\.v6lite\paper\g2\g2_probe_*`（10/2 的）
- `%USERPROFILE%\.v6lite\paper\logs\`、`%USERPROFILE%\.v6lite\paper\v6lite.sqlite3`：**正式**状态，只检查是否存在、10/2 有无改动
- `C:\Dev\TradingV6\g2_audit.csv`（注意它是 10/1 16:18 的旧导出，不能当作 10/2 的证据）
- 旧 V6 的 `C:\Dev\TradingV6\logs\`、`ibc_windows\` 中 10/2 的文件（只看有无活动）
- 对照：`docs\V6_LITE_G2_DAY1_2026-10-01.md`、`docs\V6_LITE_G2_RUNBOOK.md` 第 2 节

先说明日志时间戳是哪个时区（PT 还是 UTC），后面所有时间都标明时区。

## 3 问题

### A 版本与安全边界
- **Q1 当天跑的是哪一批代码？** 日志里没有代码哈希。请用三样东西推断：
  - 引擎启动时间（`V6-Lite starting` 那一行）；
  - 第九批写入时间（`engine.py` 等文件约在 10/1 22:35 PT 写入）；
  - 当前 `v6lite\` 全部文件的 SHA-256，与 `V6_LITE_FIX_REPORT_ROUND9_2026-10-02.md` 和 `ROUND8A` 的清单逐个对照。

  如果启动之后文件又变过，请明确写出“运行中途换过代码”。
- **Q2 broker 写调用是否为 0？** 要求：
  - audit 表里没有 `INTENT_CREATED`；
  - 日志里没有 `placeOrder` / `cancelOrder` / `sent` 类记录，只有 `[DRY_RUN]` / `WOULD_BUY`；
  - 列出 audit 表当天各 `kind` 的计数。

  任何一条不满足，就标为 P0。
- **Q3 正式状态 10/2 是否被碰过？** 正式状态的数据库和 logs 目录有没有 10/2 的写入？
- **Q4 旧 V6 当天有没有运行？** 看旧 V6 日志、IBC 日志里 10/2 的活动和使用的 client id。查不到的项写“无法从文件判断，需 John 确认”，不要猜。

### B 行情与扫描
- **Q5 连接情况**
  - 连接与断线：1100/1101/1102、2103/2104/2105/2106/2107/2108/2158 各出现几次、在什么时间。
  - 断线后报价有没有恢复，恢复用了多久。
  - 列出全部 `IBKR disconnected` / `reconnected`。
- **Q6 行情状态**
  - 每次心跳 `market_data` 里的 `no_subscription`、`delayed_symbols`、`frozen_symbols`：给出全天时间线，相同状态合并成区间。
  - 10089/354/10167 等拒绝码出现的次数和标的。
  - 这一天是否仍然没有实时行情。John 的非专业用户（Non-Pro）身份审核和 `NP,L1` 订阅都还没完成，预期是没有。
- **Q7 两次扫描**
  - ET 10:00 与 13:30 的两次扫描是否都发生，具体时间。
  - 每个标的的 `[SCAN]` 结果：score、signal、error。
  - 每个信号的 `[ENTRY_DECISION]` 结果，按拒绝原因分类计数（`global_block` / `blocked` / `quote` / `price_drift` / `dry_run`），以及有没有 `WOULD_BUY`。
- **Q8 历史数据**：每次历史请求的耗时（与 12 秒预算对比）、根数、最后一根是否是当天。有无超时或空结果。
- **Q9 第九批的报价行为（只在 Q1 确认跑的是第九批时回答）**
  - 没有实时行情时，`quote()` 应返回 None，入场因 `quote:no_quote` / `stale_quote` 被拒：请确认。
  - 有没有任何报价是由延迟 tick（66–70）或成交量 tick 构成的迹象。
  - 注意：dry-run 不执行入场前的挂单查询，所以第九批的 P1-a 在 G2 中无法验证。请写明这一点，不要把它算作已验证。

### C 账户与对账
- **Q10 AvailableFunds 推送**
  - 推送间隔：最大值、中位数。
  - `[ACCOUNT_STREAM] ... re-subscribed` 的次数，以及每次重新订阅之后多少秒收到新值。
  - 有没有因资金数据超过 270 秒而阻断。
- **Q11 查询耗时**：`reqPositions` / `reqAllOpenOrders` 每次的耗时分布和最大值（与单次 17 秒、含排空等待 19 秒对比）。有无 `owes an End`、channel 被标为不可信（poisoned）、超时。
- **Q12 对账**
  - 心跳里 `reconcile_ok` 的全天时间线。
  - `unmanaged broker holding` 列出了哪些标的，它们是否都已 `ignore`。
  - 有没有 `quantity_suspect`、freeze、未解析组合单（`*`）。
- **Q13 心跳间隔**：心跳是否每 10 分钟一次。最长间隔多少，有无因为阻塞请求导致 tick 停顿超过 30 秒。

### D 告警与异常
- **Q14 Discord**
  - 启动 self-test 与启动告警的发送结果（成功，还是 HTTP 403 / 其他错误）。
  - 心跳中 `alerts_healthy` 的时间线。
  - `V6LITE_DISCORD_WEBHOOK` 在 Windows 用户环境里是否存在（只报是否存在和长度）。
  - 手机是否收到，无法从文件判断，列为需要 John 确认的项。
- **Q15 告警频率**：每类告警（按标题）的发送次数和时间。检查重复窗口是否生效：同一条件 30 分钟内（P0 为 10 分钟内）不应重复发出。有没有刷屏。
- **Q16 异常**：全部 `Traceback`、`ERROR`、`CRITICAL`，以及按消息模板归类的 `WARNING` 计数。每类给出第一次出现的位置。
- **Q17 退出**：引擎在什么时间、以什么方式退出（Ctrl+C / 异常 / 被关闭）。账户锁有没有释放。退出时有没有撤单或其他写调用（应该没有）。

### E 探针与对比
- **Q18 探针**：10/2 是否运行了开盘前和盘中探针。如果运行了，把结果按运行手册第 2 节的表格逐行填出实测值；没运行的行写“未测”。
- **Q19 yfinance 对比**：如果有，同窗口比较时 `same_dates`、`max_close_diff_pct`、`score_diff`，以及差异来自哪个指标。
- **Q20 和第 1 天比较**：与 `V6_LITE_G2_DAY1_2026-10-01.md` 相比，哪些问题已经消失（例如 Discord 403），哪些还在，哪些是新出现的。

## 4 交付

1. 记录文件 `docs\V6_LITE_G2_DAY2_2026-10-02.md`，格式参考第 1 天的记录，内容包括：
   - 第 3 节每个问题的结论和证据；
   - 运行手册第 2 节的证据表（第 2 天）；
   - G2 第 2 天是否计入有效日：按运行手册第 4 节判断，并说明理由；
   - “需要 John 确认的项”清单；
   - “建议修复项”清单：每项写清严重度（P0/P1/P2）、根因假设、涉及的文件和函数、建议的测试。只写建议，**不改代码**。
2. 记录文件的 SHA-256。
3. 回复给 John 转交 Claude 的摘要，不超过 25 行：
   - 结论（G2 第 2 天是否有效，以及 G3 是否仍为 NO-GO）；
   - P0/P1 列表；
   - 哪些问题无法回答，原因是什么；
   - 确认本轮没有改代码、没有连 Gateway、没有 stage 或 commit。
