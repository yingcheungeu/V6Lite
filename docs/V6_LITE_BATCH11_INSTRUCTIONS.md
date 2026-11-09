# 第十一批修复指令（Claude → Codex）：G2-D 延迟行情补充演练

日期：2026-10-03。分工与第十批相同：调查和测试设计可以并行，生产代码只由一个实施 Agent 修改，最终验证串行执行。

## 0 第十批复核结论：通过

Claude 在沙盒里独立复核了第十批：

- 第十批报告的 52 个文件 SHA-256 逐一复算，全部一致；`code_sha` 复算结果为 `71c7210907b1327b`，与报告一致。
- 全量测试 331 passed、1 skipped（skip 的是需要根目录 `market_scanner.py` 的策略对比测试），与 Windows 上的 332 passed 一致。
- 逐行审阅了 `engine.py` 和 `__main__.py`、`alerts.py`、`report.py` 的改动，以及新增的 `version.py`。dry-run 停在 `_send` 中完整 gate 之后、`create_intent` 之前，只有这一处。确认无误。
- F3 的告警节奏：按第 2 天的情形推算，每天约 5 次（启动、开盘、之后盘中每 2 小时），原来是 22 次。符合要求。

本批没有发现需要返工的问题。

**第十批里 F8 缺失的原因在 Claude，不在 Codex。** Claude 补充了第 5 节（John 的决定和 F8）后重新写入了指令文件，但你电脑上的那份仍是第一版（SHA-256 `52c03a82…`，没有 F8）。Codex 按收到的版本执行，Q-B 也按第一版的“只回答、不实施”处理了，这是正确的。今后每次写入指令文件后，Claude 都会重新读回并核对哈希。

## 1 F8：G2-D 延迟行情补充演练（P1，G2 验证能力）

John 已决定：实时行情开通之前，先用延迟数据单独跑演练（G2-D），验证入场路径的机制。第十批的 Q-B 已经确认：现有代码允许 `paper + market_data_type=3 + --dry-run` 运行，且不会仅因指纹变化而拒绝状态。本批补上标识、告警和运行手册。

| # | 内容 |
|---|---|
| **F8-1 配置样例** | 新增 `v6lite\examples\paper_delayed.toml`：除 `market_data_type = 3` 以外与 `paper.toml` 相同，`state_dir` 指向**单独的**目录（例如 `~/.v6lite/paper-delayed`）。先确认 `resolved_state_dir` 是否展开 `~`、是否展开 `%USERPROFILE%`，样例里用确实可用的写法。不要改 John 的 `v6lite.paper.toml`。 |
| **F8-2 标识** | 延迟模式下，以下各处必须一眼可辨：<br>• 告警标题前缀为 `[V6-Lite paper dry-run DELAYED]`；<br>• 每条 `WOULD_BUY` 行带 `data=delayed`（实时模式带 `data=live`）；<br>• 心跳和 `STARTUP_SUMMARY` 增加 `"data_mode": "DELAYED rehearsal only"`；<br>• 日报保留现有的 DELAYED 说明。 |
| **F8-3 告警** | 现在的 `_check_market_data` 只在 `requested == "live"` 时发告警，所以延迟模式下即使连延迟行情也被拒，也完全不会通知。改为：<br>• 延迟模式下，延迟行情是预期状态，不发 “no real-time market data”；<br>• 以下两种情况发 P1 “no delayed market data”，沿用第十批 F3 的节奏（启动、开盘、盘中每 2 小时、标的列表变化立即发、盘外只写日志、条件消失时 resolve）：<br>  – `no_subscription` 非空；<br>  – 交易时段内，`usable_quotes` 少于 `quote_symbols`，并且已经持续超过 120 秒。 |
| **F8-4 运行手册** | 新增一节“G2-D 延迟行情补充演练”，写明：<br>• 复制配置的命令（`v6lite.paper.delayed.toml`）；<br>• 在新状态里对 ABT、IBB、IWM、PANW 重新执行 `ignore --dry-run`；<br>• 启动命令；<br>• **同一时间只能运行一套演练**（两套共用 `account-dryrun` 锁）；<br>• 每天要收集的证据：`code_sha`、`WOULD_BUY ... data=delayed gate=…`、`[QUERY]`、`[HISTORY]`、`[ACCOUNT_STREAM]`、`[SLOW_TICK]`、`[ALERT_DELIVERY]`；<br>• **G2-D 的日子不计入 G2 有效日**：价格比实际晚约 15 分钟，限价和数量只用来检查机制，不能用来评价策略。 |
| **F8-5 测试** | 先在第十批代码上失败，再修复：<br>• 延迟模式下，FakeBroker 跑完一次扫描：`WOULD_BUY` 带 `data=delayed` 和 gate 结果，place/cancel 调用为 0，intents 表为空；<br>• 告警标题带 DELAYED 前缀；<br>• 延迟模式下不发 “no real-time market data”；<br>• `no_subscription` 非空时发 P1 “no delayed market data”；<br>• 盘中无可用报价持续超过 120 秒时发 P1，不到 120 秒不发；<br>• 实时模式的行为和第十批完全一致（回归测试）。 |

## 2 约束

与第十批相同，另加两条：

- 不连接 Gateway，不运行引擎或探针，不改 `.env`、`state/`、`%USERPROFILE%\.v6lite\`、旧 V6、`AGENTS.md`、`docs/codex_memory.md`；不 stage、不 commit；不输出任何机密。写代码前，先确认没有 v6lite 进程在运行。
- **不要创建 `C:\Dev\TradingV6\v6lite.paper.delayed.toml`，也不要创建 G2-D 的状态目录**，这两步由 John 自己完成。
- 改动只限于 F8，不顺手重构。

## 3 交付

- `docs\V6_LITE_FIX_REPORT_ROUND11_2026-10-0X.md`，包括：
  - 每个子项的“发现 → 改动 → 测试”；
  - 新测试在第十批代码上的失败数；
  - Windows 全量测试结果（连续 3 次）；
  - 新的 `code_sha`；
  - 全部文件的 SHA-256 清单。
- 运行手册（含新增的 G2-D 一节）和 README 的测试数同步更新。
- 给 John 转交 Claude 的摘要，不超过 20 行。
