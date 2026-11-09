# 第十批修复指令（Claude → Codex）

日期：2026-10-03。依据：`V6_LITE_G2_DAY2_2026-10-02.md`（SHA-256 `a49e7d7b…7425882`）的复核。
分工：Claude 提问并给出指令，Codex 用多 Agent 实施。调查、测试设计可以并行；**生产代码只由一个实施 Agent 修改**；最终验证串行执行。

## 0 对第 2 天记录的复核意见

我接受这份记录的结论：第 2 天不计为完整 G2 有效日，G3 仍为 NO-GO，没有 P0。取证口径严谨：未测的项都写成了未测，没有拿空结果充当已验证。

记录暴露出的最大问题不在行情：**当前的 dry-run 在第一个拒绝点就返回了，G2 根本走不到要验证的那些代码。**

- 第九批的入场前挂单查询，dry-run 会跳过（`engine.py:1651`）。
- 资金重订阅和 270 秒门，dry-run 也跳过（`engine.py:568`，以及 `_entry_gate`）。
- 两天下来 `WOULD_BUY` 为 0，“报价 → 限价 → 定仓 → 闸门”这条路径从未在真实数据上跑通过。

所以即使明天实时行情开通，按现在的代码继续跑 G2，也只能证明“连接、扫描、零下单”。这是本批的主项 F1。

另外，前夜（10/1 22:10–22:21 PT）John 的演练进程运行期间，我写入了第八批的补丁 8A，导致“运行中途换过代码”。今后改代码的是 Codex，规则见第 3 节第 3 条。

## 1 先调查、再动手（可并行，结论写进报告）

- **Q-A dry-run 分支清单。** 列出 `engine.py` 中所有 `if self.dry_run` / `if not self.dry_run` 分支（约 20 处：199、218、274、304、364、544、568、665、765、789、814、949、1048、1279、1300、1651、1673、1686、1791 等，以实际代码为准）。逐条说明：
  - 跳过的是读操作，还是写操作（写 broker、写 intent、写计数器）；
  - F1 之后是否改为执行。

  做成表格放进报告。
- **Q-B 延迟行情演练是否可行。** 用 `market_data_type = 3` 另起一个 dry-run 配置，作为实时行情开通之前的临时补充，能否不改代码就跑起来？要说明三点：
  - 配置指纹变化后，现有演练状态会不会被拒绝；
  - 是否需要单独的 `state_dir`；
  - 第九批的报价模型在模式 3 下会把延迟价格的**接收时间**当成新鲜时间。演练可以接受这一点，但日志必须标明是延迟数据。

  只回答，不要实施，由 John 决定是否采用。
- **Q-C 模式 3 的准入。** `config.py:231` 只对 live 强制要求 `market_data_type = 1`，paper 正式运行（G3）也允许 3。请确认这一点，并评估：G3 用延迟价格交易，是否会让 G3 失去作为实盘预演的意义。我的判断是会。修复见 F2。

## 2 修复项

| # | 严重度 | 内容 |
|---|---|---|
| **F1** | P1（G2 的验证能力） | **dry-run 走完所有只读步骤，只在第一个 broker 写调用之前停下。** 入场路径要依次执行：`_entry_inputs` → `_entry_evidence`（`reqAllOpenOrders` 是只读调用）→ 处理期间到达的事件 → 全局阻断 → 再次 `_entry_inputs` → 限价和定仓 → `_entry_gate` 的全部检查（证据年龄、告警健康、日内风险、审批、阻断、资金）。然后输出一行 `WOULD_BUY sym qty LMT limit risk=… gate=ok`，或 `gate=refused:<原因>`。<br>账户流方面：dry-run 在交易时段内也执行不阻塞的 `reqAccountUpdates` 重订阅（只读），让 210 秒重订阅、270 秒资金门真正运行，并输出 `[ACCOUNT_STREAM]` 日志。<br>**严禁**：创建 intent、增加当日入场计数、写 `INTENT_CREATED` 等审计记录、调用 place/cancel。演练状态里没有持仓，所以出场路径不在本项范围内，不要改动。 |
| **F2** | P2（G3 的有效性） | `market_data_type = 3` 只允许与 `--dry-run` 一起使用。paper 正式运行和 live 都必须是 1，否则在启动时报 `CONFIG ERROR`，并说明原因。 |
| **F3** | P2（告警噪音） | “no real-time market data” 第 2 天发了 22 次，其中 10 次在开盘前或收盘后。改为：<br>• 启动时发 1 次，开盘时发 1 次；<br>• 交易时段内条件不变时，每 2 小时重复一次；<br>• 交易时段外只写日志；<br>• 条件变化（标的列表变化）时立即发；条件消失时 resolve。<br>其他告警保持 30 分钟和 10 分钟的规则不变。John 如果要别的节奏可以改这个数，但必须只有一个常量。 |
| **F4** | P2（版本取证） | 启动时对 `v6lite\` 下全部非 `__pycache__` 文件（排序后）计算 SHA-256，取前 16 位作为 `code_sha`。它要写进：`V6-Lite starting` 那一行、`STARTUP_SUMMARY`、audit 的 STARTUP 记录、日报。 |
| **F5** | P2（预算取证） | 增加以下日志，全部 INFO 级、单行、简洁：<br>• `[HISTORY] sym secs bars last_date`：每个历史请求一行；<br>• `[QUERY] channel secs drained=…`：`reqPositions` / `reqAllOpenOrders` 每次一行，耗时包含排空等待；<br>• `[SLOW_TICK] secs`：单个 tick 超过 30 秒时输出，写明当时在做什么。 |
| **F6** | P2（送达记录） | 每次远程发送输出一行 `[ALERT_DELIVERY] channel ok/fail ms`。**不得**包含 URL 或 token。 |
| **F7** | P2（探针与日报） | `g2_probe` 的耗时汇总保留完整调用名。账户流的测量改为不阻塞的重订阅，并记录收到第一条新 AvailableFunds 的延迟；原来的阻塞调用如果保留，要标明“诊断用，不代表引擎行为”。日报的行情一行：所有标的都没有可用报价时，明确写“requested real-time; no usable quotes N/N”，不能只写 `real-time (1)`。 |

**不做**：记录退出信号（Ctrl+C 是已知的操作员动作，价值低）。

## 3 约束

1. 不连接 Gateway，不运行引擎或探针，不改 `.env`、`state/`、`%USERPROFILE%\.v6lite\`、旧 V6，不改 `AGENTS.md` 和 `docs/codex_memory.md`。不 stage、不 commit、不 push。
2. 不输出任何机密，包括 webhook URL、bot token 和 `.env` 内容。
3. **写任何代码文件之前，先确认没有 v6lite 进程在运行**：用 `Get-CimInstance Win32_Process` 查看 python 进程的命令行里是否含 `v6lite`。有就停下，告诉 John。
4. 每个修复项都要有聚焦的测试，并且先证明这些测试在第九批代码上失败。F1 至少要覆盖：
   - FakeBroker 的 place/cancel 调用为 0，intents 表为空，当日入场计数为 0；
   - `WOULD_BUY` 行带有 gate 结果；
   - 资金过期时输出 `gate=refused:funds_stale`；
   - 查询期间出现未解析组合单时，dry-run 被拒；
   - 在 dry-run 中，210 秒后触发重订阅。
5. 最小改动，不顺手重构。dry-run 停在写调用之前的那个边界，必须是**一处**集中判断，而不是散落在各处的提前返回。实施后由独立 Agent 复核：从入口到 place/cancel 的每条调用路径，在 dry-run 下都不可达。

## 4 交付

- `docs\V6_LITE_FIX_REPORT_ROUND10_2026-10-0X.md`，包括：
  - Q-A、Q-B、Q-C 的答案；
  - 每个修复项的“发现 → 改动 → 测试”；
  - 新测试在第九批代码上的失败数；
  - Windows 全量测试结果（连续 3 次）；
  - 全部文件的 SHA-256 清单。
- 给 John 转交 Claude 的摘要，不超过 25 行。
- 运行手册的附录更新到第十批；README 中的测试数同步更新。
