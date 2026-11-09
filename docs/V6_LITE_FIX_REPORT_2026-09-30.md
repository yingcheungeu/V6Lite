# V6-Lite 整改清单与测试结果（提交 Codex 独立复核）

**日期：** 2026-09-30 PT
**实现者：** Claude（John 授权：按 L1–L5 第 1、2 组实现，交 Codex 独立复核）
**复核依据：** `docs/V6_LITE_REDESIGN.md` 第 8.1 节（Codex 2026-09-30 修订版，本次**未修改**该文件）
**变更范围：** 仅 `v6lite/` 目录（代码、测试、README）+ 本报告。未修改旧 V6、`.env`、`state/`、`AGENTS.md`、`docs/codex_memory.md`、风险参数或示例配置中的任何数值；未 stage / commit / push；未连接 broker；未生成批准文件。
**结论状态：** `IMPLEMENTED_VALIDATION_PENDING` —— 离线测试全部通过，等待 Codex 独立复核与 Windows 本机复跑。**不构成 Paper 或 Live 启动授权。**

---

## 1. 如何复现

```powershell
cd C:\Dev\TradingV6
.\venv_v6\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
python -m pytest v6lite\tests -q                       # 期望：104 passed
python -m pytest v6lite\tests\test_review_fixes.py -q -rA   # 本轮 36 项回归
```

实现环境的结果（Linux、Python 3.11.15、ib_insync 0.9.86、FakeBroker/FakeIB、无 Gateway）：

| 运行 | 结果 |
|---|---|
| 全量 `v6lite/tests`（提供 legacy `market_scanner.py`） | **104 passed**，连续 3 次一致（13.6–15.0s） |
| 全量，无 legacy 扫描器文件 | 103 passed，1 skipped（策略对比测试自动跳过） |
| 本轮新增回归 `test_review_fixes.py`，**修复前**（对原骨架运行） | **35 failed，1 passed** |
| 同一文件，**修复后** | **36 passed** |
| 原有 67 项测试 | 全部保留并通过；`test_engine.py`、`test_soak.py`、`test_ibkr_adapter.py` 未改动 |

修复前唯一通过的是 `config ... daily_loss_limit_usd = nan`（原代码已拒绝 NaN），保留作为守卫。

**需要 Codex 在 Windows 上特别确认：** 账户锁在 Windows 走 `msvcrt.locking` 分支，本实现环境只执行了 Linux `fcntl.flock` 分支。`test_L2_account_lock_is_exclusive_across_processes` 在 Windows 上运行即可覆盖该分支。

---

## 2. 发现 → 修改 → 测试 对照

第 8.1 节的每一行都列在这里。"修复前"一列说明失败原因：**行为**表示原代码行为错误；**接口**表示测试依赖本轮新增的接口，原代码因缺少接口而失败（这些测试同时断言了行为）。

### L1 全路径只读隔离（P1 / 只读演练前）

| 8.1 证据 | 修改 | 测试 | 修复前 |
|---|---|---|---|
| `engine.py` 已有阶段重试无 dry-run 判断 | broker 写操作只剩两个方法：`_send`（submit）和 `_request_cancel`（cancel），两者在 dry-run 下直接拒绝；已有阶段的重试路径在 dry-run 下只记日志；dry-run 不执行恢复、结算、超时撤单，也不入账成交与状态事件 | `test_L1_dry_run_never_writes_to_broker_even_with_pending_orders`：状态内含已取消的 STOP 阶段，dry-run 执行 startup + 5 次 tick + shutdown，submit/cancel 次数与阶段重试次数都不变 | 行为 |
| dry-run 与正式状态混用 | 状态文件新增 `role` 元数据（formal / dryrun），一旦绑定不可改。CLI 的 dry-run 使用 `<state_dir>\dryrun\`、client ID +1000、独立日志和日报目录，以只读方式从正式状态复制 `ignore/adopt` 决定 | `test_L1_dry_run_engine_refuses_formal_state`：dry-run 引擎打开正式状态 → `StartupError`，broker 写操作为 0 | 行为 |
| `broker/ibkr.py` 撤单无只读判断 | `IbkrBroker.cancel` 在只读会话中抛出 `BrokerError`，并拒绝撤销非 `v6l-` 前缀的订单 | `test_L1_ibkr_cancel_is_blocked_in_read_only_session` | 行为 |

### L2 身份、互斥、控制与批准（P1 / Paper 前、Live 前）

| 8.1 证据 | 修改 | 测试 | 修复前 |
|---|---|---|---|
| `approval.py` 非原子写锁、释放不核对所有者、同账户不同 `state_dir` 无互斥 | `ProcessLock` 改为 `AccountLock`：按账户加 OS 文件锁（Windows `msvcrt.locking`，其他平台 `flock`），与 `state_dir` 无关，锁目录为 `%LOCALAPPDATA%\v6lite\locks\`。获取是原子的，只有持有者能释放，进程崩溃时自动释放，不再有 PID 陈旧锁需要清理 | `test_L2_account_lock_is_exclusive_across_state_dirs`、`..._across_processes`（子进程持锁 → 失败；子进程被杀 → 可获取） | 接口 |
| 旧 V6 锁是 JSON | `_lock_pid` 同时解析 JSON `{"pid":…}` 和 "pid\n时间"；无法解析 → 失败关闭 | `test_L2_foreign_json_v6_lock_is_understood`；`test_units::test_account_lock_...`（补充了内容无法解析的用例） | 接口 |
| `__main__.py` 控制命令不校验账户/模式 | kill/resume/ignore/adopt/clear-anomaly/resolve-intent 只打开**已存在**且 account、mode、role=formal 与配置一致的状态文件；否则拒绝，不写库 | `test_L2_control_commands_refuse_mismatched_account` | 行为 |
| status 会创建状态文件 | 状态文件不存在时 status 只输出说明，不创建任何文件或目录 | `test_L2_status_without_state_creates_nothing` | 行为 |
| 批准只在启动时检查 | 引擎每个 tick 通过 `approval_checker`（CLI 中即 `check_live_gate`）重新校验批准；最终发送边界对每个 ENTRY 再校验一次。失效时阻断入场、发一次 P1，退出照常 | `test_L2_revoked_live_approval_blocks_entries_but_not_exits`（撤销后下一个 tick 阻断，`_send` 拒绝，跌破止损仍发出 STOP） | 接口 |
| 旧 V6 不识别 Lite 锁 | **未改变（设计限制）**：仍是单向检查 + 操作隔离，README 已写明必须先停旧的 main 和 auto runner | — | — |

### L3 订单终态、迟到成交与退出恢复（P1 / Paper 前）

| 8.1 证据 | 修改 | 测试 | 修复前 |
|---|---|---|---|
| 依据本地 `openTrades` 缓存缺失关闭意图 | `IbkrBroker.open_orders()` 改用 `reqAllOpenOrders()`（服务器实时查询，按 orderRef 前缀和账户过滤）；设置 `ib.RequestTimeout=15s`，超时抛异常 | `test_L3_open_orders_come_from_a_fresh_broker_query`（缓存为空、服务器有单 → 能看到） | 行为 |
| 查询之间成交 | 证据顺序固定为**先查 working 订单，再查 executions**（`_order_evidence`）：订单离开 working 列表前完成的成交，读 executions 时一定已存在 | `test_L3_settle_reads_open_orders_before_executions` | 行为 |
| 查询不完整 | 任一查询失败 → 意图标记 `UNKNOWN`（阻断入场），不进入终态；恢复失败 → 所有在途意图转 UNKNOWN，并锁定 `recovery_failed` 异常 | `test_L3_open_orders_failure_never_finalizes` | 行为 |
| 跨日 | 前一交易日创建、当前不在 working、今天的 executions 也无法证明成交的意图 → `UNKNOWN(prior_day_unverifiable)` + P1 告警，不自动重试。新增命令 `resolve-intent <id> --as cancelled`，由人工核对 TWS 交易记录后裁定，实际成交的股份随后以对账差异出现 | `test_L3_prior_day_intent_without_evidence_stays_unknown` | 行为 |
| client ID 变更 | client ID 首次运行时写入状态元数据；存在在途订单时变更 client ID → `StartupError`；无在途订单时允许变更并审计 | `test_L3_client_id_change_with_active_orders_aborts` | 行为 |
| 关机只撤 SENT/PARTIAL | shutdown 先向 broker 查询所有 `v6l-` working 订单（含 UNKNOWN 和账本没有的），全部撤销后结算；仍无法确认时发 P1 并列出订单号；working 列表查询失败本身也告警 | `test_L3_shutdown_cancels_unknown_orders_that_are_working` | 行为 |
| 迟到 ENTRY 成交给 CLOSED 仓位加量 | 仓位已 CLOSED 时，迟到的 ENTRY 成交建立**新的** OPEN 仓位（来源 `late_fill`，全新的一次性阶段，计划按成交价 + 原 ATR），并锁定 `late_entry_fill` 异常（阻断入场、P1） | `test_L3_late_entry_fill_after_close_opens_a_managed_position`（断言不存在 CLOSED 且 qty_open>0 的仓位） | 行为 |
| 迟到 SELL 与重试叠加超卖 | 退出（首次与重试）只在 broker 持仓 **等于** 账本 qty_open 时进行；不相等时不交易，立即拉取 executions（最多每 30 秒一次）并触发对账 | `test_L3_exit_waits_for_broker_truth_after_hidden_late_sell`（无回调的迟到成交被发现，不追加卖单） | 行为 |
| 部分 ENTRY/TP 撤单后按最终数量 STOP | 行为由上面两项保证；同一持仓有在途 ENTRY/TP 时，跌破止损会先请求撤单，结算后按真实剩余数量卖出 | `test_L3_late_entry_fill...`（部分入场 10 股 → 撤单 → STOP 卖 10） | — |

### L4 资金、日损、数字与费用完整性（P1 / Paper 前）

| 8.1 证据 | 修改 | 测试 | 修复前 |
|---|---|---|---|
| 余量按配置上限计算 | 剩余资金 = 定仓 equity（`min(NLV, 上限)`）− 已部署 − 在途入场名义 − `FEE_RESERVE_USD`（$2） | `test_L4_remaining_capital_uses_actual_equity`（equity $3,000、已部署 $2,800 → 新单 ≤ $198） | 行为 |
| 无可用资金约束 | 新增 `BrokerPort.available_funds()`（IBKR `AvailableFunds`，USD/BASE）；发送边界要求可用资金 ≥ 名义 + 预留，缺失即拒绝 | `test_L4_entry_blocked_when_available_funds_short` | 行为 |
| 重复 execution 跳过费用补录 | 重复 exec_id 不再重算数量与 PnL，但会补录首次缺失的手续费（审计 `COMMISSION_BACKFILLED`） | `test_L4_duplicate_execution_backfills_commission` | 行为 |
| 非整数股数被四舍五入 | 适配器不再取整，改为发出 `AnomalyEvent`；引擎锁定异常并阻断入场，该成交不入账（随后以对账差异出现） | `test_L4_non_integer_shares_raise_anomaly_instead_of_rounding` | 接口 |
| 配置整数检查后保留原值、分区不严格 | 所有整数字段必须是 int（拒绝 bool、小数），数字字段必须有限且非负，时长类有下限；顶层键和每个分区的键都有白名单，拼错或放错分区一律拒绝；`ibkr` 字段不再用 `int()` 强制转换 | `test_L4_config_numbers_and_sections_are_strict`（6 组参数） | 行为 |
| 日损基准为当日首个 RTH 标记 | 基准改为**上一个已收盘交易日的收盘价**：`sod_unrealized = Σ(prev_close − entry) × qty`，由新增的 `BrokerPort.previous_close()` 提供；取不到时每 5 分钟重试，期间阻断入场（`sod_not_marked`）。adopt 进来的持仓把自身的历史浮盈亏计入基准（`SOD_ADJUSTED` 审计；取不到收盘价则锁定异常） | `test_L4_prior_close_baseline_catches_overnight_loss`（盘中才启动也能算出当日 −$160 并锁定） | 接口 |
| 手续费晚到后的连续亏损 | 平仓时如有成交缺手续费，结果进入 `pending_result`，待手续费齐全再计入连亏；收盘报告前强制结算并审计 `TRADE_RESULT_FEES_INCOMPLETE` | `test_L4_trade_result_waits_for_commission`（毛利为正、手续费到达后净额为负 → 触发第二次亏损冷却） | 行为 |

### L5 保护循环与告警投递（P1 / Paper 前、Live 前）

| 8.1 证据 | 修改 | 测试 | 修复前 |
|---|---|---|---|
| SMTP/HTTP 同步阻塞 | 远程告警在后台线程发送（不触碰 broker 与账本）；`send()` 立即返回投递 ID，可通过 `delivery()` 查询结果；启动自检最多等待 20 秒，运行中的重测不阻塞 | `test_L5_slow_alert_channel_does_not_block_the_loop`（通道耗时 2 秒，`send` 在 0.3 秒内返回） | 行为 |
| 所有通道失败后仍算健康 | 健康 = 有通道 **且** 最近一次投递成功 **且** 24 小时内有成功；任何一次全部失败立即转为不健康，每 10 分钟非阻塞重测 | `test_L5_alert_health_reflects_the_latest_attempt` | 接口 |
| live 可关闭必需告警 | live 配置 `require_remote_in_live` 不是 True 即拒绝；live 必须有邮件或已设置的 webhook 环境变量；运行时 live 告警不健康一律阻断入场（不再看该开关） | `test_L5_live_config_cannot_disable_required_alerts` | 行为 |
| 串行历史请求期间退出检查停滞 | 每个历史数据请求带 10 秒超时；扫描时每取完一个标的就调用 `_service_protection`（处理事件 + 按周期检查退出） | `test_L5_scan_keeps_servicing_exits_between_symbol_fetches` | 接口 |
| 使用 tick 开始时的 `now` 校验报价 | 每个入场决策用当前时钟重新取时间；报价年龄超过 15 秒即不入场 | `test_L5_quote_age_is_checked_with_fresh_time_after_slow_data`（取数耗时 24 秒 → 报价过期 → 不下单） | 行为 |
| 日报先标 sent 后发送 | 状态依次为 `pending:<id>:<时间>` → 确认送达才写 `sent`；失败 15 分钟后重发；没有远程通道时标 `written_local` | `test_L5_daily_report_is_marked_sent_only_after_delivery`（首次失败 → 未标 sent → 16 分钟后重发成功，只送达一次） | 行为 |
| status 缺少运行健康信息 | 引擎每 60 秒把健康快照（连接、对账、告警、权益、阻断原因）写入状态；status 显示快照年龄，超过 180 秒标为 STALE | CLI 冒烟测试 | — |

### 其他

| 项目 | 修改 | 测试 |
|---|---|---|
| L6 策略对比无法复跑 | 新增 `test_strategy_parity.py`：用 `ast` 从仓库根目录的 `market_scanner.py` 抽取原函数（不执行 Discord、yfinance、trading_bot_v6 的导入），固定种子 1,000 组相同 K 线逐项比较 7 个字段。已做变异验证：把一个评分权重从 18 改为 17，测试即失败 | 1 passed（本地有 legacy 文件时） |
| 保护标的的外来成交误报 | 没有 intent、且属于 protected/ignored 标的的成交只记审计，不再锁定 `foreign_fill` 异常（例如在 TWS 手动交易 MSTR） | `test_foreign_fill_on_protected_symbol_is_not_an_anomaly` |

---

## 3. 请 Codex 重点复核的设计判断

1. **`held == qty_open` 退出门槛**：比原来的 `min(target, held)` 更严格。broker 持仓回报滞后时，退出会推迟一个检查周期（30 秒）。我的判断是：以短暂延迟换取绝不按有差异的数量交易。请评估止损延迟风险是否可以接受。
2. **撤单确认后的迟到成交**：以 IBKR 的语义为前提——订单已确认取消、离开 working 列表、executions 中没有记录，之后就不会再成交。如果这个前提不成立（重试已经发出后，旧单又成交），V6-Lite 只能在事后发现（`oversell` 异常 + 对账），无法事前防止。浸泡测试没有注入这种违反 broker 语义的场景。
3. **跨日 UNKNOWN 需要人工裁定**：`resolve-intent` 只允许裁定为 cancelled，由操作员承担核实责任；实际成交的股份以对账差异出现，再用 `adopt`/`ignore` 处理。请确认这个处理流程可以接受。
4. **`reqAllOpenOrders` 的范围**：它返回所有 API client 以及 TWS 的订单，这里按 orderRef 前缀和账户过滤。`reqExecutions` 仍按 client ID 过滤，所以另加了"client ID 不可在有在途订单时变更"的约束。这两个 API 在真实 Gateway 上的行为需要在 G2 阶段验证。
5. **日损基准改为上一收盘价**：盘中启动、adopt 的计入规则见 L4 表格。这一改动改变了"日损"的计量口径，但没有改变第 6 节的风险数值。
6. **告警改为后台线程**：这是 V6-Lite 第一个工作线程。它只调用 SMTP/HTTP，不访问 broker 和 SQLite，结果通过加锁的字典回传。请复核线程安全。
7. **dry-run 的 client ID +1000**：只读会话与正式会话可以同时连接，但 dry-run 看不到正式会话的 executions（本来也不应该入账）。
8. **配置严格化可能拒绝现有配置**：两个示例配置都能通过，指纹也未变（paper 示例仍为 `a5f6f2b761645dc0`）。如果 John 本机已有自定义的 `v6lite.*.toml`，需要重新加载一次确认。

---

## 4. 未完成与剩余风险（明确不在本轮范围）

- **L7 停机/隔夜保护**：未实现，需要 John 在 Live 之前决定方案。
- **execId 更正**（后缀变化）：仍会被当作新成交，随后以对账差异 + 可能的 oversell 异常暴露，需人工处理。未实现自动识别。
- **可用资金新鲜度**：`AvailableFunds` 来自流式账户数据，没有时间戳；断线时整体阻断入场，但连接期间的数据陈旧程度无法单独判断。
- **L6 未完成的部分**：Windows/DST/跨年日历固定样本、yfinance 与 IBKR 数据口径差异（留给 G2）。
- **Windows `msvcrt` 锁分支**只能在 John 的机器上验证。
- **真实 Gateway 行为全部未验证**：`reqAllOpenOrders`、`RequestTimeout`、`reqExecutions` 的 client 范围、1100/1101/1102 事件、`AvailableFunds` 字段、历史数据超时。

---

## 5. 变更统计（相对 2026-09-30 首次交付的骨架）

| 文件 | 增 / 删行 |
|---|---|
| `engine.py` | +342 / −88 |
| `__main__.py` | +150 / −48 |
| `alerts.py` | +88 / −20 |
| `approval.py` | +78 / −18 |
| `store.py` | +74 / −6 |
| `config.py` | +72 / −12 |
| `broker/ibkr.py` | +49 / −12 |
| `broker/fake.py` · `broker/base.py` · `models.py` · `risk.py` · `sizing.py` | +16/−1 · +8/−1 · +8/−1 · +5/−2 · +6/−1 |
| `README.md` | +8 / −5 |
| 测试：`helpers.py` / `test_units.py` | +8/−4 / +11/−7（适配新接口；锁测试改为 AccountLock 并增加内容无法解析用例；未放宽任何断言） |
| 新增测试 | `test_review_fixes.py`（539 行，36 项）、`test_strategy_parity.py`（109 行） |

生产代码（不含测试）由约 3,550 行增至约 4,230 行。

## 6. 文件清单（SHA-256，交付时）

```text
5119411a97c750601d0140327718354751f9959841ed070b3781b43f3b0069d4  v6lite/README.md
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
a00cfd437557ce9c510e6dbed050749a13cdef83857a11ad5945c104c0e3445e  v6lite/__main__.py
86fcdbaf9bd4b24faae7213300897906f76e8fac71fbc93f85742b209f24120d  v6lite/alerts.py
bad8fd7346ed070fe6f32ac3a929ae740751aef825c089f47730b58eefbee77e  v6lite/approval.py
2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24  v6lite/broker/__init__.py
78d3a6c451ba58a0868789102aea93cf98578c05722ebd473308c743a12a9a04  v6lite/broker/base.py
83608ab3f91e7631c2436ab0c8ba9c4c71a70846a1b540f7c2b1626a500417b8  v6lite/broker/fake.py
0013dc7bddb0f175517227bc0745bf27bf529ebbc0dda4872e29eb30eb8578eb  v6lite/broker/ibkr.py
0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1  v6lite/clock.py
631964f19e27dc3b559f7b3dba598ab10429a689f78d9d7f3e4ee5a54e1a7be0  v6lite/config.py
2ba6c853c6fba284e4d80c8b8308d5b2b34d35f88024a8dbf2e1c944d2142ff5  v6lite/engine.py
1032534d1665efed3143d97bed9f7525410772a49ee5894f2000d0bcdd2fd673  v6lite/examples/live.toml
9c8682f8fa30a91703a53994745e948bc778186688dfe843acc353a5d8df123b  v6lite/examples/paper.toml
c3b4a074f96c5a25789afb5fabf088ead3b1daddaaa1ee5477ec1399c77627e6  v6lite/exits.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
0f5087ea6a1d48b64b83757ce6ea59a899064cdd6e0779a319352557f8a845e2  v6lite/models.py
938789c437352e5b28d325a7c25a585f50003ae42f6f850cf1d2bd5794c39c15  v6lite/reconcile.py
285bb101fa7f21c2b9923e02e905e20222f1b545eef6b976d684458b21637039  v6lite/report.py
30aaea00e507e531826ef996b28c7d173f5bab82b3af0064aaa210f235d6541d  v6lite/risk.py
e5f4c3e0724a0ad9a95b2fdfe1f962254052476f3751baff6cdf64df617f8d18  v6lite/sizing.py
ef152e9145c0a34ac6ce65db434d4593dbe49532a9f3fdb7193f568b905a4470  v6lite/store.py
9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e  v6lite/strategy.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  v6lite/tests/__init__.py
aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb  v6lite/tests/conftest.py
d191f36f3e932f054b309668ba4ede3bb80565309910a828c638fcd9cccbb0e1  v6lite/tests/helpers.py
37bd846f6377899d0000f752f42dece4ebbefb28fd38a3fd56f2f270ea6a16f9  v6lite/tests/test_engine.py
2504d1fe88bb28d133258e9321e27a74ffc2659d5661e7cfd99268005e1ebcd7  v6lite/tests/test_ibkr_adapter.py
6c66d9473daa41721db659ed877fee5120f1caef5b28da83ea2491111da0f2ac  v6lite/tests/test_review_fixes.py
a5377a4f520e6c3d340cb40571f24eae1dd431be871ecfad241b0d0080424aef  v6lite/tests/test_soak.py
732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f  v6lite/tests/test_strategy_parity.py
75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42  v6lite/tests/test_units.py
```

复核时先比对这份清单，确认复核的是同一份代码。如果 Codex 复核后修改了任何文件，本清单即失效，需要在复核记录里写明新的清单。

## 7. 建议的复核输出

请按项目惯例给出 `P0/P1/P2` 分级结论，并逐条标注第 2 节每一行是 `CLOSED_WITH_EVIDENCE`、`IMPLEMENTED_VALIDATION_PENDING` 还是 `OPEN_PRODUCT_DEFECT`。第 3 节的判断如有异议，请标明是否阻塞 G2（真实 Paper 只读验证）或 G3（Paper 真单）。
