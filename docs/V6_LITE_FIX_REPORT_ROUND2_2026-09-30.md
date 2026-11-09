# V6-Lite 第二批整改报告（针对 Codex 独立复核 2026-09-30）

日期：2026-09-30 PT。整改：Claude。待复核：Codex。
依据：`docs/V6_LITE_CODEX_REVIEW_2026-09-30.md`（R1–R12、第 3 节 P2、第 5 节判断 1–4）。

**结论先行：** R1–R12 与第 3 节 P2 已全部按“先写复现测试、确认失败、再修复”处理。v6lite 全量离线测试 165 项通过，连续 3 次结果一致。本报告**不宣称** G3 或 Live 可放行：两者仍须 Codex 复核、真实只读 G2 和 L7。所有结论只基于 FakeBroker、FakeIB 和临时数据库。整改期间没有连接 Gateway、下单、签发批准，也没有读取 `.env` 或 `state/`，没有 stage/commit/push，也没有改动旧 V6、`AGENTS.md`、`docs/codex_memory.md` 或 Codex 的计划与复核文件。

## 1 复现命令

```powershell
cd C:\Dev\TradingV6
. .\venv_v6\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
python -m pytest v6lite/tests -q -ra                               # 期望 165 passed
python -m pytest v6lite/tests/test_review_round2.py -q -ra         # 本批新增 61 项
```

要得到“修复前”的结果：把整改前的 v6lite（即第一批报告 SHA-256 清单对应的版本）复制到临时目录，只替换 `tests/test_review_round2.py` 和 `tests/helpers.py`，再运行上面第二条命令。

## 2 结果总表

| 测试集 | 修复前 | 修复后 |
|---|---|---|
| 第一轮复现测试（按 Codex 的输入序列编写，38 项） | 37 failed / 1 passed（通过的是 R11 “旧锁 PID 已消失应放行”的对照项） | 38 passed |
| 补充覆盖测试（Codex 要求的“补 …”场景，含 schema 迁移，11 项） | 6 failed / 5 passed（通过的 5 项是行为保持类护栏） | 11 passed |
| 交付前自审发现的问题（C1–C7、S1–S3、status 只读，12 项，见第 5 节） | 整改前版本上 12 failed；C1–C7 另由审查代理在第一版修复上复现 | 12 passed |
| `test_review_round2.py` 合计 61 项 | 整改前版本：55 failed / 6 passed | 61 passed |
| v6lite 全量 | 104 passed（第一批） | **165 passed** ×3，每次约 23 秒 |
| 其中 soak（10 个随机种子，完整生命周期，broker 与账本一致、无异常） | 通过 | 通过 |
| 策略比较（1,000 组固定样本，与根目录 `market_scanner.py`） | 通过 | 通过（`strategy.py` 未改） |

失败原因逐项核对过，都是复核指出的缺陷本身，不是测试写错。复现过程中有一处测试脚本错误：R8 第一次写时 TSLA 在 10:00 就产生了信号，已更正为 11:00 才出现信号，之后的失败原因与 Codex 描述一致（STOP 以 105.91 成交、含费日损 153.20、同一次扫描仍发出 TSLA ENTRY）。

## 3 逐项：发现 → 改动 → 测试

### 订单证据与恢复（R1–R4、R12、撤单竞态）

| 项 | 改动 | 测试（修复前 → 后） |
|---|---|---|
| **R1** 孤立 working 订单 | 新增 `_check_orphans`。broker 上 `v6l-` 开头的 working 订单，如果账本没有这笔、或账本已判它结束，就记 `orphan_order:<id>` 异常，阻断新开仓，发 P1，不自动撤单或接管。启动恢复、每次结算取证、定期对账都会检查；对账时查不到 open orders 则 `reconcile_ok=False`。更换 client ID 时，除本地 active 外，还要新查一次 broker working 订单，有订单或查询失败都拒绝启动。dry-run 只记日志、不报异常（正式订单不归演练管） | `test_R1_orphan_working_order_from_another_state_dir_blocks_entries`：修复前两笔 AAPL ENTRY；`test_R1_client_id_change_refused_while_broker_has_working_orders`：修复前未拒绝；`test_R1_dry_run_does_not_treat_formal_orders_as_orphans`：护栏 |
| **R2** 跨日 UNKNOWN 与人工恢复 | 意图表新增 `hold_reason` 列，不再靠 note 文本判断（note 会被覆盖）。统一由 `_decide` 裁定一笔意图：仍在 working → SENT/PARTIAL；成交证明已满 → FILLED；有 hold → 不动；创建于更早交易日 → 设 hold（prior_day_unverifiable）；只有当日意图才会判 CANCELLED。启动、运行中跨午夜、结算、shutdown 都走同一边界，shutdown 跳过 hold。新增 `repair-position`，用于补记已托管持仓漏掉的成交（见第 4 节 D5）；`resolve-intent` 只裁定订单状态 | `…survives_shutdown`、`…carried_across_midnight…`、`…note_overwrite_cannot_release…`：修复前均变 CANCELLED；`…missed_buy…repaired`、`…missed_sell…realized_loss`、`…repair_refused_when_broker_quantity_differs`、`…prior_day_partial_entry_resolved_then_repaired` |
| **R3** 迟到 ENTRY 分批只管第一批 | 意图表新增 `recovery_position_id`。原仓已关闭时，同一 ENTRY 的每一批迟到成交都并入同一个恢复仓（按开仓数量加权均价；已有退出阶段则计划冻结）。若同标的已有其他 OPEN 仓，成交数量并入该仓并记异常，不再丢弃 | `…two_batches_is_fully_managed`：修复前账本 5、broker 10；`…replay_after_restart_is_idempotent`；`…three_late_batches_at_different_prices` |
| **R4** 先对账时迟到 SELL 恢复停滞 | 标的数量不一致时仍禁止卖出，但会排队一次限频（30 秒）的 execution 刷新，这一步不再依赖是否允许退出 | `…mismatch_found_by_reconcile_first…`：修复前 4 次 tick、账本 20、broker 10，SELL 不变；修复后找回成交，STOP 按真实剩余 10 股继续 |
| **R12** execId 更正、费用更正 | 成交表新增 `exec_base`（IBKR execId 去掉最后一段）和 `quarantined_fills` 表。同一 base、后缀不同的新 execId 视为更正：不入账，记 `exec_correction` 异常并发 P1，重放幂等。同一 execId 的佣金值变化时覆盖旧值，并审计新旧两个值 | `…exec_id_correction_is_quarantined…`：修复前账本 40 股、无异常；`…commission_correction_replaces…`：修复前仍是旧值 |
| 判断 2 撤单确认后迟到成交 | 已结算的退出单又出现新成交时，记 `late_exit_fill` 异常，立即撤下该持仓其他在途 SELL（按修正后数量重新规划），发 P1 提示检查空头。SELL 最终边界改为：数量不得超过 broker 持仓减去其他在途 SELL 的未成交量 | `test_race_late_fill_of_cancelled_stop_cancels_the_retry`、`test_race_final_sell_boundary_counts_other_working_sells`：修复前均失败 |

### 资金与日损（R5–R8）

| 项 | 改动 | 测试 |
|---|---|---|
| **R5** 连续订单重复消费资金 | `available_funds()` 改为返回 `FundsSnapshot(value, as_of=值最后变化时间, seen_at=账户流最后推送时间, ready)`。IBKR 适配器通过 `accountValueEvent` 维护这些字段，连接前就注册回调；重连会清空，`AccountReady=false` 视为未就绪。最终入场门的检查：未就绪拒绝；`seen_at` 超过 240 秒拒绝；可用资金要先扣除在途 ENTRY 未成交部分的金额和费用估计，再扣除 `as_of - 30s` 之后的入场成交。账户流超过 150 秒没有推送时，主动重新请求（`refresh_account`）。单笔“金额 + 费用”检查保留 | `…consecutive_entries_cannot_reuse…`：修复前总额 4,538.80；`…fill_after_the_funds_snapshot…`、`…stale_or_unready…`、`…cancelled_entry_releases…`、`…adapter_funds_come_from_the_account_stream`、S2、S3 |
| **R6** 上一收盘基准 | 删除存储型基准 `sod_unrealized` 和 adopt 基准调整，改为 `risk.day_pnl` 每次从账本重新推导（口径见第 4 节 D2）。昨收按日缓存在账本里；拿不到时显示 `daily_loss_baseline_unknown` 并阻断新开仓，退出照常 | `…baseline_uses_prior_close_holdings…`：修复前不锁定（实际亏损 ≥ 200）；`…adopted_today_uses_the_conservative_reference`：修复前显示盈利；`…prior_close_unknown_blocks_entries_but_not_exits`；另有全平与重启两项 |
| **R7** 收盘强制按毛利裁定连亏 | 删除 pending 与 force 机制。连亏和冷却由 `risk.loss_state` 按平仓顺序从已平仓交易重新推导；未到的佣金按保守估计计入（见 D1），真实佣金到达后自动更正 | `…late_commission_after_close_still_counts…`：修复前冷却为 None；`…commission_reported_before_its_fill…` |
| **R8** 扫描期间的日损 | `_service_protection`（每个慢请求前后都会运行）入账后立即重算日损，最终 ENTRY 门也再算一次 | `…stop_fill_during_scan_latches…`：修复前锁定标志为 0 且发出 TSLA；`…commission_arriving_during_scan_can_latch` |

### 保护循环与健康（R9–R10）

| 项 | 改动 | 测试 |
|---|---|---|
| **R9** LIVE 最终入口使用旧的告警健康 | 最终 ENTRY 门实时读取 `Alerter.healthy`；退出不受影响 | `…checks_alert_health_at_the_send_boundary`：修复前仍发 ENTRY；`…alert_outage_blocks_live_entries_but_never_exits` |
| **R10** 慢请求拉长退出检查间隔 | `BrokerPort.worst_case_seconds(kind)` 声明每类阻塞调用的上限（IBKR：history 12 秒、query 17 秒；VIX 在启动时已完成合约查询则为 12 秒，否则 29 秒）。每个阻塞请求前调用 `_protect(kind)`：如果下一次退出检查会在等待期间到期，就先做退出检查。覆盖扫描、VIX、昨收、adopt、订单证据（两次查询之间也检查）、成交刷新、撤单查找、账户流刷新。退出循环内的慢操作（刷新、撤 TP）移到所有持仓检查完之后执行。到期的 tick 内允许多次检查，防止保护性检查挤掉常规检查 | `…slow_requests_never_stretch…`：修复前间隔 [49, 30]，修复后全部 ≤ 31 秒；C3、C4 |

### P2

| 项 | 改动 | 测试 |
|---|---|---|
| 日报投递 ID 重启碰撞 | ID 改为“进程随机 token + 序号”。`Alerter.knows()` 不认识的 pending（来自上一个进程）立即重发 | `…delivery_ids_do_not_collide…`、`…report_pending_from_a_previous_process_is_resent` |
| 失败告警被去重吞掉 | 去重只抑制已送达或仍在途的重复消息；上次失败的消息允许重发 | `…failed_alert_is_not_swallowed…` |
| SMTP 端口 | `smtp_port` 必须是 1..65535 的整数（bool、负数、小数、nan、70000 都拒绝）；告警字段类型也做校验 | 参数化 5 项 |
| status/report 读错账户库 | 先用只读 URI 读取 meta 校验账户、模式、角色，匹配后才打开 Store，所以不匹配的文件不会被打开或迁移。日报显示绑定账户 | `…status_and_report_refuse…`、`…status_never_migrates…` |
| 固定 $2 费用预留 | 新增 `estimate_fee(qty, price)`：按 IBKR Pro Fixed（$0.005/股，最低 $1，最高成交额 1%）加卖出监管费，再 ×1.5，不低于 $2。资金检查、仓位计算余量和未到佣金估计共用这一个函数。[官方费用表](https://www.interactivebrokers.com/en/pricing/commissions-stocks.php)。这只是估计，实际账户费率须在 G2/G3 用真实佣金核对 | `…fee_reserve_scales_with_share_count` |
| 适配器佣金恢复 | `executions_today` 从 `ib.wrapper.fills[execId]`（ib_insync 的规范 Fill 对象）读取佣金；空的 CommissionReport 返回 None，不会当成 0 | `…adapter_takes_commission_from_the_canonical_fill` |

R11（旧锁 AccessDenied）：`_pid_alive` 只有确认进程已消失（NoSuchProcess；psutil 的 ZombieProcess 属于它的子类）或 PID 已被非 Python 程序复用时才放行，AccessDenied 等其他异常一律失败关闭。测试：`test_R11_*` 两项。

## 4 需要 Codex 裁定的设计判断

| # | 判断 | 理由与代价 |
|---|---|---|
| D1 | 连亏由已平仓交易推导；未到的佣金按保守估计计入 | 不会因收盘或费用迟到而永久结错；代价是毛利很小的盈利单在佣金到达前的几秒内可能先计为亏损、提前触发冷却（阻断的是新开仓，属于偏保守）。`test_L4_trade_result…` 已按这一新口径改写，见第 6 节 |
| D2 | 日损口径：上一收盘持有股数 = 当前持有 − 当日买入 + 当日卖出 − 当日 adopt，按昨收计价；当日 adopt 按 max(昨收, 成本)；当日买入按成交价；当日卖出按先进先出消耗；手续费 = 已报告 + 未到部分估计 | 不存基准，所以减仓、迟到成交、repair、重启都不会冻结错误基准。当日 adopt 取较大参考价：若股份其实是前一天买的，会多算亏损，这是有意取保守（Codex 的例子：成本 100、昨收 90、现价 95 → 计亏 200）。只在 RTH 锁定，与原行为一致 |
| D3 | 孤立订单只阻断 + P1，不自动撤、不接管 | 孤立单可能是保护性 SELL，自动撤会扩大风险；按判断 4，可见不等于有权撤销 |
| D4 | execId 更正第一版一律隔离，不自动冲销 | 数量或价格变化后，reconcile 会显示差异，由人工 `repair-position` 处理 |
| D5 | `repair-position` 的流程 | 操作员提供 broker 数量、价格、费用和可选的交易日。引擎在对账时先重查当日成交（如果漏掉的成交已经可见就正常入账，repair 显示“不需要”），broker 数量必须仍然匹配，且该标的没有非挂起的在途订单。退出阶段未完成时，SELL 修复必须用 `--intent` 指明对应的 V6-Lite 订单，这样会记入阶段、不会重卖 TP；如果不是 V6-Lite 的订单，用 `--manual`。修复后写入 `repaired_through:<symbol>` 时间戳：此后才变得可见、但执行时间早于修复时间的成交会被隔离，并发 P2 提示（C1）。**这个判断依赖时间先后**，时钟偏差或同秒成交是残余风险，兜底是 reconcile 的数量比对 |
| D6 | 挂起（hold）的前一交易日意图不再阻挡退出 | DAY 单跨日不可能仍在 working；如果它其实成交了，“持有数量 = 账本数量”的检查仍会挡住任何交易。挂起意图仍阻断新开仓，且不会被撤单请求或 broker 状态提示改动（C6） |
| D7 | 资金新鲜度参数：240 秒、150 秒主动刷新、成交 30 秒余量；没收到 `AccountReady` 视为就绪 | IBKR 文档写账户值约每 3 分钟推送一次；余量用来覆盖本地时钟与 IBKR 服务器时间（秒级）之间的偏差。**G2 须验证**：`AccountReady` 这个字段是否真的会出现，以及账户安静时的推送节奏 |
| D8 | 保护预算依赖声明的最坏耗时 | 间隔上界 = 检查间隔，前提是每次调用都不超过声明的值（history 超时 10 秒 + 2、请求超时 15 秒 + 2）。实际耗时须在 G2 记录 |
| D9 | 迟到的退出成交会撤下其余在途 SELL，并阻断新开仓 | 缩小超卖窗口，但无法消除：重试单可能在撤单生效前成交。真正的闭环在 L7（broker 端保护单 + OCA 数量） |
| D10 | 状态库 schema 1→2 原地迁移 | 自动加列；v1 中 note 含 prior_day 的 UNKNOWN 回填 hold；回填 exec_base。status/report 不会迁移别人的文件 |

## 5 交付前自审（在交给 Codex 之前）

第一版修复完成后，另起一个独立的只读审查代理，对差异做对抗性审查。C1–C7 由它在第一版修复上复现；S1–S3 是它根据推理提出、未复现的问题。以上都已修复，并固化为测试：

| 编号 | 严重度 | 问题 | 处理 |
|---|---|---|---|
| C1 | P1 | `repair-position` 之后，那笔原本漏掉的成交晚些时候变得可见，会被重复入账（账本 CLOSED，broker 仍持有 10 股，无保护） | D5：先重查成交，再加 `repaired_through` 隔离 |
| C2 | P1 | 前一交易日挂起的 SELL 意图整天挡住 STOP | D6 |
| C3 | P2 | 在退出循环内刷新成交，会拖慢后面持仓的检查（间隔 45 秒） | 慢操作移到循环之后 |
| C4 | P2 | 重启后撤单要回退到 `reqAllOpenOrders`，这次等待未计入预算 | 先查 ib_insync 的本地缓存；撤单前调用 `_protect` |
| C5 | P2 | 退出单的 `sent_at` 用的是 tick 开始时间，导致超时提前触发 | `_send` 与退出检查改用实时时钟 |
| C6 | P2 | 撤单请求或状态提示会改动挂起意图，入场阻断因此消失，也无法再 resolve | 挂起意图不受这两者影响；`resolve-intent` 接受挂起意图，且改为单条带状态条件的 UPDATE |
| C7 | P2 | resolve 之后再 repair，没有记入 TP1 阶段，TP1 会再卖一次 | `--intent` 与 `--manual` |
| S1 | P1（第一批已存在） | 1101（连接恢复但数据丢失）后没有重新订阅行情和账户流 | 链路恢复时调用 `refresh_subscriptions` |
| S2 | P2 | 本地时钟与 IBKR 时间偏差，可能导致成交被漏扣 | 30 秒余量 |
| S3 | P2 | 账户安静时资金一直被判陈旧，入场持续被拒 | 150 秒后主动重新请求 |
| 次要 | — | 日损没有计入未到佣金；resolve-intent 先检查后写入存在竞态；status 打开时可能迁移别人的库 | 均已处理 |

审查代理还确认了以下各项没有发现问题：嵌套事务、无限递归、跨日意图被自动关闭、取证期间新建的意图被误判、更正隔离与迟到路由的数量守恒、day_pnl 的空值、schema 迁移、ib_insync 0.9.86 的属性与事件名。

## 6 对原有测试的改动（均非放宽）

- `test_engine.py::test_consecutive_losses_start_cooldown`：原先直接调用已删除的 `record_trade_result`。改为写入已平仓交易，再由推导函数计算；语义不变（0 不重置，2 次亏损触发冷却）。
- `test_review_fixes.py::test_L4_trade_result_waits_for_commission`，更名为 `…uses_net_pnl_and_estimates_missing_commissions`：原先断言“佣金未到时不计亏损”，按 D1 改为“先按估计计入，真实佣金到达后更正”，并增加了“真实佣金很小 → 更正为盈利”的反向断言。
- `test_ibkr_adapter.py`：FakeIB 增加 `accountValueEvent`（适配器改为在连接前注册回调）。
- `tests/helpers.py`：新增 `seed_closed_trade`。`broker/fake.py` 的 execId 改为 IBKR 格式（原来的 `exec.0001` 会被 exec_base 规则误判为更正），并新增 `invisible_execs`、`costs`、资金快照字段。

## 7 仍未完成 / 须在真实环境验证

1. **L7**（broker 端保护单，GTC Stop-Limit + OCA）未实现，Live 前必须完成；D9 的超卖窗口要靠它闭环。
2. **G2 只读须记录**：`AccountReady` 是否出现；AvailableFunds 与 `seen_at` 的推送节奏（D7）；每类请求的实际最大耗时（D8）；`reqAllOpenOrders` 能看到的范围（判断 4）；`reqExecutions` 之后 CommissionReport 到达的时机；1101 之后重新订阅是否恢复报价；execId 更正的真实格式。
3. Codex 第 5 节判断 2 要求的“晚于重试收到旧 execution、滞后快照”：已用 FakeBroker 注入（race 两项、R4、C1），真实 Gateway 上尚未复现。
4. `repair-position` 依赖操作员从 TWS 抄录的价格和费用；`repaired_through` 依赖时间先后（D5）。
5. 当日亏损的现价使用最多 120 秒前的报价；报价更旧时标记为无报价、阻断新开仓（原有行为）。
6. Windows 上的运行结果待 Codex 复跑：原子锁分支已在第一轮复核中通过，本批没有改锁本身，只改了旧锁 PID 判定。

## 8 变更统计与 SHA-256 清单

新增/删除行数（+/−）：engine.py +493/−180、store.py +276/−87、risk.py +102/−29、`__main__.py` +97/−23、broker/ibkr.py +108/−17、broker/fake.py +22/−6、alerts.py +18/−5、sizing.py +21/−4、approval.py +12/−3、models.py +12/−0、config.py +10/−0、report.py +6/−2、broker/base.py +8/−2、README.md +11/−4。新增 tests/test_review_round2.py（1,156 行，57 个测试函数，展开后 61 项）；已有测试文件改动见第 6 节。未改动：strategy.py、exits.py、reconcile.py、market_calendar.py、clock.py、examples/*.toml（paper 指纹仍为 `a5f6f2b761645dc0`）、test_soak.py、test_strategy_parity.py、test_units.py。

```
7a59be96c8c9d463ef9f63f0805b3f584f603c098020c24a4708c9c10d5de592  v6lite/README.md
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
0c773297d5de36186b1b13f761cf42019c7967b316db3e22a78c26268a9491b0  v6lite/__main__.py
3d1f717dc462ab0c1e48821bbfe82d7232842a2ef63570741213180fe899a5b1  v6lite/alerts.py
162daa7d98a2362bc29521fcf65472c5590fb282da694b5011da934d8ffc26b5  v6lite/approval.py
2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24  v6lite/broker/__init__.py
73ed0a2f49a398d3b4d3dfa7669bb7464530b5a3b8a92da03c901db0bd7295c1  v6lite/broker/base.py
ea40962bdc9bd9015595395cc6a9c73dd750d7d7bd31575235587bb0b0e7a807  v6lite/broker/fake.py
ba7cb7dc8767940010c0423731482ca1a3c20593d46a514b4b31f5a299f57596  v6lite/broker/ibkr.py
0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1  v6lite/clock.py
e8e10582fbf82d3849bcca4662d039d910923528fc15a25da99155e3af9b00f4  v6lite/config.py
9ffe5d06f8e022fad09b00c37fd6d292d83797805d1872dc7d3a8dfa6d9a4012  v6lite/engine.py
1032534d1665efed3143d97bed9f7525410772a49ee5894f2000d0bcdd2fd673  v6lite/examples/live.toml
9c8682f8fa30a91703a53994745e948bc778186688dfe843acc353a5d8df123b  v6lite/examples/paper.toml
c3b4a074f96c5a25789afb5fabf088ead3b1daddaaa1ee5477ec1399c77627e6  v6lite/exits.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
d4f8a40d39f6180afc358312392fb79c9317177f04e3822e5d5739dd39db2ea4  v6lite/models.py
938789c437352e5b28d325a7c25a585f50003ae42f6f850cf1d2bd5794c39c15  v6lite/reconcile.py
6753b504179cbfce65344e532685c85ca9d6c1aec3d64c2ba5e223295dfe9fb0  v6lite/report.py
72b9e135cfaeca44e788fd0ceb5a2a342c15912cf71105aa5eee95045303220d  v6lite/risk.py
c7b9000919a9d7672aead2eddab786f96064946cf6084cf6b19cf9351ae70e26  v6lite/sizing.py
74a329e96a09de1f61a6b948af04e7aaf8b9d017ffd6ba85d3f0eba219a80a72  v6lite/store.py
9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e  v6lite/strategy.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  v6lite/tests/__init__.py
aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb  v6lite/tests/conftest.py
fd6c76b9f1e7bd2ff59cd82b86453619f156f1c9c61885aab31f248f01f1f038  v6lite/tests/helpers.py
6abff24924a7d86bab5681f89e6daecf341d2ce75e4065535b51989001d3c823  v6lite/tests/test_engine.py
6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a  v6lite/tests/test_ibkr_adapter.py
02f0537a763c12ad6254173078e251ff833aae4ac148fbfc45687ed69d0ce45a  v6lite/tests/test_review_fixes.py
f13bb988cffa0cc3c15f562399637cf51a564044189105cf4d904786a5df792f  v6lite/tests/test_review_round2.py
a5377a4f520e6c3d340cb40571f24eae1dd431be871ecfad241b0d0080424aef  v6lite/tests/test_soak.py
732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f  v6lite/tests/test_strategy_parity.py
75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42  v6lite/tests/test_units.py
```

（33 个文件，已排除 `__pycache__`。）

## 9 请 Codex 复核时

1. 在 Windows 上复跑第 1 节命令，核对清单。
2. 对第 3 节每一行给出 `CLOSED_WITH_EVIDENCE` / `IMPLEMENTED_VALIDATION_PENDING` / `OPEN_PRODUCT_DEFECT`，沿用第一轮的口径。
3. 对 D1–D10 逐项给出接受或推翻的意见，重点看 D1、D2、D5、D6、D9。
4. 尝试用新的输入序列反证第 5 节的修复，尤其是 C1 的时间先后假设和 R10 的预算模型。
