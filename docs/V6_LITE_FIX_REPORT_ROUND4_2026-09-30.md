# V6-Lite 第四批整改报告：砍掉恢复层（针对 Codex 第三轮复核）

日期：2026-09-30 PT。整改：Claude。待复核：Codex。
依据：`docs/V6_LITE_CODEX_REVIEW_ROUND3_2026-09-30.md`（F1–F9，以及第 4–6 节的逐行裁定），以及 John 的两项决定：
- **P2 也修**，不作为残余风险接受。
- 第四批按**“砍范围”**处理：
  - v1 只在全新状态上运行，不迁移旧状态文件。
  - 删除 `resync`。
  - 无法解释的数量差异 → 冻结该标的 → 操作员在 TWS 平仓 → broker 显示 0 股且没有 working 订单后执行 `release`。

**结论先行：**
- F2、F4、F5、F6 的根源是 resync 与旧库迁移，这两项已整体删除，不再逐条修补。
- F1、F3、F7、F8、F9 按复核建议逐条修复。
- 交付前做了一轮对抗性自审（第 6 节），发现 1 个 P1 和 6 个 P2，均已修复并固化为测试。
- 全量离线测试 **208 passed**，连续 3 次结果一致。

本报告**不宣称** G3 或 Live 可放行。所有结论只基于 FakeBroker、FakeIB 和临时数据库。整改期间没有连接 Gateway，没有下单或签发批准，没有读取 `.env` 或 `state/`，没有执行 stage/commit/push，也没有改动旧 V6、Codex 的复核文件或开发计划。

## 1 复现命令

```powershell
cd C:\Dev\TradingV6
. .\venv_v6\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
python -m pytest v6lite/tests -q -ra                                        # 期望 208 passed
python -m pytest v6lite/tests/test_review_round4.py -q -ra                  # F1–F9 与替代场景 21 项
python -m pytest v6lite/tests/test_review_round4_selfreview.py -q -ra       # 交付前自审 12 项
```

## 2 结果总表

| 测试集 | 本批之前的代码（第三批） | 本批 |
|---|---|---|
| `test_review_round4.py` 中的 F1–F9 部分（16 项，F6 按 v1/v2/v3 参数化） | **16 failed**：F1 仍发出 SELL；F7 发出 TP1 6 股；F8 未拒绝启动，退避被提前 15 秒绕过；F9 没有预留 BAG 腿；旧库没有被拒绝；`release` 命令不存在 | 16 passed |
| `test_review_round4.py` 中的替代场景（5 项） | 依赖 `release`，第三批中不存在 | 5 passed |
| `test_review_round4_selfreview.py`（12 项） | 针对本批第一版。R1–R7 由自审代理在第一版上复现失败（8 failed），其余 4 项是自审后新增行为的回归测试 | 12 passed |
| v6lite 全量 | 190 | **208 passed** ×3，每次约 30 秒 |
| 退役的测试 | — | 共 15 个。`test_review_round3.py` 删除 14 个 resync 与迁移测试，`test_review_round2.py` 删除 1 个迁移测试，因为这些功能已删除。场景去向见第 5 节 |

## 3 逐项：发现 → 改动 → 测试

| 项 | 改动 | 测试 |
|---|---|---|
| **F1** fresh 查询失败时，仍用 31 秒前的空列表发出 SELL | 每次 SELL 之前必须重新查询 open orders 和持仓（`reqPositions`），任何一项失败就拒绝发送。不回退到缓存。拒绝时退避 30 秒并告警；被拒的是 STOP 时为 **P0**（见自审一节） | `test_F1_failed_fresh_order_query_refuses_the_sell` |
| **F2** resync 使用滞后的持仓，隔离旧成交后没有撤掉过量 SELL | **resync 删除**。对账和每次卖出前都用 fresh 持仓（`positions_fresh`），不用可能滞后的 `ib.positions()` 缓存。fresh 持仓与账本不一致时：该标的不发任何 SELL；新开仓立即阻断；持续刷新成交。差异持续 60 秒，并在强制刷新成交、重新读取持仓后仍存在，就冻结该标的，发 P0，同时撤下 V6-Lite 自己在该标的上的 working 订单。之后由操作员在 TWS 平仓，再执行 `release` | `test_F2_stale_position_cache_never_sizes_a_sell`、`test_F2_persistent_difference_freezes_the_symbol`、`test_F2_release_after_flatten_closes_the_ledger_position`、`test_freeze_withdraws_our_own_working_orders` |
| **F3** resync 被拒后，外层对账发布旧的健康结果并放行 ENTRY | release 取证时读到的最新持仓，无论最终执行还是拒绝，都会替换外层对账使用的持仓。另外，退出路径在同一 tick 发现差异时立即阻断入场（自审 R5） | `test_F3_refused_release_still_publishes_the_latest_positions`、`test_R5_*` |
| **F4** resync 丢失已知的 TP1 部分成交进度 | 随 resync 一并删除。release 只在 broker 为 0 股时关闭整个账本持仓，不再重建持仓，也就不存在阶段进度需要继承 | 不适用（`test_resync_command_no_longer_exists`） |
| **F5** resync 与当天日停之间存在崩溃窗口 | `Store.release_position` 在**一个事务**里完成以下全部操作：持仓标记 RELEASED、qty 置 0；该标的的挂起意图标记 CANCELLED；写入 `releases` 记录；清除冻结；当日风险行不存在时先创建，然后锁定当天日停。事务中途失败会整体回滚。重启后日停来自 `risk_days` 表，release 计入连亏来自 `releases` 表，两者都是持久化数据 | `test_F5_release_and_the_day_freeze_commit_together`（在日停写入处注入异常：持仓仍为 OPEN，没有 release 记录；成功后重新打开数据库：日停已锁定，持仓为 RELEASED）、`test_release_counts_as_a_loss_for_the_streak` |
| **F6** v2→v3 迁移没有处理旧的恢复语义（adopt、repair、closed_at） | **不再迁移**。`SCHEMA_VERSION = 4`，遇到其他版本一律拒绝。检查使用只读 URI，在建表、改日志模式之前完成，文件字节不变（自审 R1）。`preflight_state` 在打开 Store、连接 broker 之前就检查版本和身份。迁移代码、备份代码、`superseded_by`、`resyncs` 表全部删除 | `test_F6_v1_refuses_state_files_from_earlier_versions[1/2/3]`、`test_R1_real_v3_state_is_not_modified_by_the_refusal`（比对 SHA-256） |
| **F7** 短查询期间价格跌破止损，取证后仍发出 TP1 | 发送前用最新报价调用 `_redecide`：若已离开止盈区，放弃该止盈；若期间触发止损，立刻启动 STOP 阶段并改发 STOP；已 FAILED 或 DONE 的 STOP 阶段不会被重新发送（自审 R4）。STOP 超出可卖数量时压小并告警，止盈超出时不发 | `test_F7_exit_is_re_decided_with_the_latest_quote_before_sending`、`test_R4_redecide_never_sends_on_a_failed_stop_stage` |
| **F8-A** 检查周期配置与最长阻塞调用不兼容 | `startup()` 第一步检查：`exit_check_interval_seconds ≥ 最慢的单次阻塞调用 + 3 秒`，不满足就拒绝启动（此时尚未连接，也没有撤单）。IBKR 的声明值：history 12、vix 12、query 17 秒，因此最小可用周期为 20 秒，默认 30 秒 | `test_F8_check_interval_must_fit_the_largest_blocking_call` |
| **F8-B** 排队的副本绕过 30 秒退避 | `_submit_exit` 执行时先检查退避。被拒时同时删除队列中该持仓的退出项。只有在 sent/unknown/rejected 时才增加尝试次数 | `test_F8_back_off_is_honoured_when_the_queued_item_runs` |
| **F9** D13 把含股票腿的 BAG 也排除在外 | 适配器展开 BAG 组合：已知 conId 的股票腿按“组合数量 × 腿比例”计入对应标的的 working SELL，SELL 组合的腿方向相应反转。有任何无法识别的腿时，返回 `symbol="*"`、方向未知的订单；此时账户内**全部 SELL 都被拒绝**，release 也被拒绝。OPT 等其他 secType 仍不计入 | `test_F9_combo_order_stock_legs_are_reserved`、`test_F9_unresolvable_combo_leg_fails_closed` |

## 4 冻结与释放的语义（需 Codex 重点复核）

**冻结：**
1. `_reconcile` 和卖出边界都使用 fresh 持仓。托管标的一旦出现差异，就记入 `_mismatch_since`，同时满足以下几点：
   - 新开仓立即被阻断，途径有两条：`reconcile_ok=False`，或本 tick 内的 `quantity_suspect`。
   - 该标的不发送任何退出单。
   - 持续刷新成交，每个 tick 都重新对账。
2. 差异持续 `FREEZE_AFTER`（60 秒）之后，强制刷新成交，并重新读取持仓：
   - 若差异已消失，就不冻结。只要持仓读取成功，即使成交刷新失败也采用这份持仓（自审 R3）。
   - 证据完整且差异仍在：冻结。
   - 证据不完整：推迟冻结并发 P1。超过 `FREEZE_AFTER + 120 秒` 仍不完整，也会冻结，并在告警中注明证据不完整。
3. 冻结时：
   - 写入 `frozen:SYM` 控制项和同名异常。
   - 对 V6-Lite 自己在该标的上的非挂起意图发送撤单请求。
   - 发 P0，内容包括：已请求撤单的订单号、在 TWS 平仓、然后 `release`。
4. 冻结期间：
   - `global_entry_blocks` 直接读取 `frozen:*` 控制项，与异常是否存在无关，而 `clear-anomaly` 对 `frozen:*` 会拒绝执行（自审 R2）。
   - 该标的不再参与冻结计时，也不会让每个 tick 都强制对账（自审 R7）。

**释放**（`release SYM`：CLI 只写入 `release:SYM` 控制项，由引擎应用）：
1. 按以下顺序重新取证，任何一步失败都拒绝：
   - open orders
   - 强制刷新 executions
   - fresh 持仓
2. 任一情况下拒绝：
   - 没有托管持仓
   - 该标的或 `*` 组合仍有 working 订单（任何来源）
   - 账本还有非挂起的在途意图
   - broker 数量 ≠ 0
   - 最近 60 秒内有成交
   - dry-run
3. 通过后，第 3 节 F5 列出的所有操作在一个事务内完成。拒绝时发 P1 并写审计 `RELEASE_REFUSED`。
4. 日报列出冻结中的标的和当天的 release 记录，并注明操作员在 TWS 的交易不计入 V6-Lite 的盈亏。

之所以不再需要 Codex 第 2 节所说的三个命题，是因为 release 不再对“旧订单之后还会不会成交”作任何推断，它只接受可以直接观察的终点：broker 0 股、没有 working 订单、60 秒内无成交。账本持仓被关闭，但不会新建；即使之后又出现迟到成交，`_apply_exit_fill` 对非 OPEN 的持仓也只记 `exit_after_close` 异常，不修改任何数据，并阻断入场。代价是：
- 无法解释的差异一律要求人工平仓；
- 当天停止新开仓，并计一次亏损。

在 $5k–10k、最多 2 个持仓的试点规模下，接受这个代价。

## 5 已删除的功能及其场景去向

| 删除 | 原测试场景 | 现由 |
|---|---|---|
| `resync`（含 `superseded_by`、`resyncs` 表、按订单归属的隔离） | 漏记 BUY 或 SELL、数量不符时拒绝、跨日部分入账、挂起意图、归零、保留退出计划、resync 计亏损、取证后重新读取 | `test_missed_buy_freezes_then_flatten_and_release`、`test_late_fill_found_before_the_freeze_deadline_prevents_the_freeze`、`test_prior_day_partial_entry_is_frozen_then_released`、`test_release_counts_as_a_loss_for_the_streak`、F2/F3/F5 |
| 纯价格更正的重建（原 Q4） | IBKR 更正了成交价 | 更正仍然识别并隔离，异常会阻断新开仓，退出照常（数量没变）。`test_price_only_correction_blocks_entries_while_exits_continue`。操作员核实后执行 `clear-anomaly` |
| 旧库迁移与自动备份 | v1→v3 迁移并备份 | 改为拒绝；F6 与 R1 |

## 6 交付前自审（SR4）

第四批第一版完成后，另起一个只修改临时测试的审查代理，对照 `v6lite_baseline_r4` 做了对抗性审查。以下问题都在第一版上复现了失败，均已修复：

| 编号 | 严重度 | 问题 | 处理 / 测试 |
|---|---|---|---|
| R5 | P1 | 退出路径在 tick 前半段发现 broker ≠ 账本，只标记了该标的，`reconcile_ok` 仍为 True，于是同一 tick 后面的扫描照样 BUY。R5b（由判断轮发现的差异）在第三批代码上同样存在 | 新增 `RuntimeFlags.quantity_suspect`：退出判断和卖出边界发现差异时立即写入，`global_entry_blocks` 据此阻断，下一次 fresh 对账后清空。`test_R5_*`、`test_R5b_*` |
| R3 | P2 | 强制刷新成交失败时，冻结使用的是旧的对账结果，即使同一步读到的 fresh 持仓已与账本一致，也会误冻结 | 只要持仓读取成功就重新对账；证据不完整时推迟冻结（第 4 节第 2 条）。`test_R3_no_freeze_when_fresh_evidence_matches_after_a_failed_refresh`、`test_R3_partial_evidence_defers_then_freezes_after_the_grace` |
| R2 | P2 | 冻结只能通过可清除的异常阻断入场：清除异常后恢复入场，而冻结的标的仍然没有自动止损 | 入场阻断直接读取 `frozen:*`；`clear-anomaly frozen:*` 被拒绝。`test_R2_entries_stay_blocked_while_a_symbol_is_frozen` |
| R4 | P2 | `_redecide` 忽略 `begin_stage` 的返回值，可能在已经 FAILED 的 STOP 阶段上再发一次 STOP | 重新读取阶段状态，不是 PENDING 就不发。`test_R4_*` |
| R7 | P2 | 冻结的标的让每个 tick（约 1 秒）都发起 reqPositions 与 reqAllOpenOrders | 冻结的标的不参与冻结计时。`test_R7_*`（60 个 tick 内订单查询 ≤ 2 次） |
| R1 | P2 | 拒绝旧库之前已经执行了 v4 的建表脚本，文件因此被改动；控制命令也会写入旧库 | 先用只读 URI 检查版本，再执行任何写操作；Store 本身负责拒绝，所以所有命令都受保护。`test_R1_*` |
| R6 | P2 | 日报的事件过滤仍是已删除的 `POSITION_REPAIRED`，看不到 release | 改为 `POSITION_RELEASED`、`RELEASE_REFUSED`；另列冻结标的和 release 记录。`test_R6_*` |
| R8 | P2 | README 仍写着 resync 和自动备份 | 已更新 |
| S1 | 推测 | 冻结时 V6-Lite 自己的 SELL 仍在 working，操作员在 TWS 平仓时可能与它叠加，造成空头 | 冻结时撤下自己的订单，并在 P0 中列出。`test_freeze_withdraws_our_own_working_orders` |
| S2 | 推测 | 超卖留下的空头被 `ignore` 后，入场会恢复 | 被 ignore 的标的上的空头仍记为差异（`protected` 名单中的标的除外）。`test_a_short_on_an_ignored_symbol_is_not_ignored` |
| S3 | 推测 | 无法识别的组合单阻断全部 SELL 时只发 P1 | STOP 被阻断时一律发 P0。`test_a_stop_blocked_by_missing_evidence_pages_p0` |

**自审提出、本批未修改、列为 G2/G3 验证项：**
- ib_insync 的 `reqPositions` 和 `reqAllOpenOrders` 使用固定的请求键。上一次超时请求迟到的结束消息，理论上可能提前结束新请求，导致列表不完整；缺少某个标的会被当成 0 股，release 依赖这一点。本地无法复现。
- 两次取证（订单、持仓）之间新下的外来订单看不到。
- 断线重连的 `connect(timeout=20)` 不在预算内；断线期间本来也无法发单。
- release 请求在取证之前就从控制项删除，恰好在这之间崩溃会丢失请求；此时审计里只有请求、没有结果，操作员重新执行即可。

## 7 设计判断（更新，请 Codex 表态）

| # | 判断 |
|---|---|
| D3″ | 外来、孤立、挂起但仍 working 的订单：不自动撤，也不接管，股数一律预留；查询失败就失败关闭（F1）；股票组合腿计入预留（F9） |
| D5″ | 恢复层只保留 freeze → 人工平仓 → release。不重建持仓，不补记成交，不迁移旧状态 |
| D8″ | 启动时校验检查周期不短于“最长单次阻塞调用 + 3 秒”；实际耗时由 G2 测量 |
| D11′ | SELL 证据必须来自本次 fresh 查询（订单和持仓都要），不使用 60 秒内的缓存作为回退 |
| D12′ | 退避在执行时检查；被拒时删除队列中的副本 |
| D13′ | OPT 等不含股票腿的订单不计入预留；BAG 按股票腿计入；无法识别的腿阻断全部 SELL 和 release |
| D14′ | release 与当天日停在同一事务中提交，并计一次亏损 |
| D15 | 冻结：60 秒 + 完整 fresh 证据；证据不完整最多再宽限 120 秒。冻结时撤下 V6-Lite 自己的订单；冻结只能由 release 解除 |
| D16 | 被 ignore 的标的上出现空头时，不视为“他人持仓”（多头专用系统，空头可能是超卖造成的） |
| D17 | 只接受 schema 4 的全新状态；其他版本在任何写操作之前就被拒绝 |

## 8 G2 须知

G2 从本批代码开始。如果已经用第三批代码运行过 `run --dry-run`，需要先把 `%USERPROFILE%\.v6lite\paper\dryrun` 整体改名归档，否则启动会被拒绝（文件不会被改动）。runbook 的附录已更新。

## 9 SHA-256 清单（39 个文件，已排除 `__pycache__`）

相对第三批的增量：

| 文件 | 增 / 删行数 |
|---|---|
| engine.py | +284 / −108 |
| store.py | +89 / −121 |
| `__main__.py` | +35 / −30 |
| broker/ibkr.py | +50 / −10 |
| broker/fake.py | +12 / −0 |
| broker/base.py | +6 / −1 |
| risk.py | +14 / −5 |
| reconcile.py | +6 / −0 |
| report.py | +10 / −2 |
| README.md | +20 / −8 |

测试：新增 `test_review_round4.py` 和 `test_review_round4_selfreview.py`；改动 round2（删除 1 项）和 round3（删除 14 项）。
Schema 版本：4，只接受全新状态。
`docs/V6_LITE_G2_RUNBOOK.md`：`dc833d66f4b2d575c8f517a808895c0e9c1c7ac225676e0ce83c185fd8d7b41b`

```
a0112edf9034956114f2416b25127617be48033ba656e7a809bf7c915242f244  v6lite/README.md
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
152ac099b8fae21d738505f110315da400226aa246ec0a979b19e4d1a00986c1  v6lite/__main__.py
3d1f717dc462ab0c1e48821bbfe82d7232842a2ef63570741213180fe899a5b1  v6lite/alerts.py
162daa7d98a2362bc29521fcf65472c5590fb282da694b5011da934d8ffc26b5  v6lite/approval.py
2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24  v6lite/broker/__init__.py
722fab4b5992c737dfcfcb86c5b74c33ba633b3b3ee561eac3c4a289abe723e1  v6lite/broker/base.py
c9567ee2546ae30ee8841ff0d4214cd1d4903aa93241e3620d4c0298d5e1ba8a  v6lite/broker/fake.py
44d8387e7ab8c78c218f270242c7e91bd9a49fcd0d331416f50fff75ec4f1b48  v6lite/broker/ibkr.py
0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1  v6lite/clock.py
e8e10582fbf82d3849bcca4662d039d910923528fc15a25da99155e3af9b00f4  v6lite/config.py
cab87ea0446cdb2846dcc7792bd20414d59c8e3f767a7f2039aab00fd7d38246  v6lite/engine.py
1032534d1665efed3143d97bed9f7525410772a49ee5894f2000d0bcdd2fd673  v6lite/examples/live.toml
9c8682f8fa30a91703a53994745e948bc778186688dfe843acc353a5d8df123b  v6lite/examples/paper.toml
c3b4a074f96c5a25789afb5fabf088ead3b1daddaaa1ee5477ec1399c77627e6  v6lite/exits.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
172ef940ec8e5b5959d379c5520ac0f7cfbdf44f4c930a20fd01cfd2ecacadfb  v6lite/models.py
4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1  v6lite/reconcile.py
8e3a09bb672caaad0d363c9db2859ddd2187c4c612b5c42a0e80d931af7544e0  v6lite/report.py
1cea56411a0be5a4ddb5e1ec948f763c5f6d0bccdc3abeba38e59f2c6b6568e5  v6lite/risk.py
c7b9000919a9d7672aead2eddab786f96064946cf6084cf6b19cf9351ae70e26  v6lite/sizing.py
db846e5a25c789fb2d5a5a4ef2575ae96af420c7855a7f93f408c4d6590f7e8a  v6lite/store.py
9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e  v6lite/strategy.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  v6lite/tests/__init__.py
aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb  v6lite/tests/conftest.py
fd6c76b9f1e7bd2ff59cd82b86453619f156f1c9c61885aab31f248f01f1f038  v6lite/tests/helpers.py
6445bac89adb5356c19d98e63e0bcd6061bba35f0f476f81269f0482037a59bf  v6lite/tests/test_engine.py
e70899902f5e8338d3b01755e1c747e0fb09322f32c283a2eac51a2f2d5ff86a  v6lite/tests/test_g2_probe.py
6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a  v6lite/tests/test_ibkr_adapter.py
02f0537a763c12ad6254173078e251ff833aae4ac148fbfc45687ed69d0ce45a  v6lite/tests/test_review_fixes.py
dd8d7b5d34db5054ac6eef7aab0ed97f16711d03634784832b300eeb468f4605  v6lite/tests/test_review_round2.py
903fca0b51ed8fee873fd5629105d248bb474de2d7aee1b9a3250adafaa6e7cc  v6lite/tests/test_review_round3.py
342c5a14b315ccec9b8da1eb6439e5a9c623f60f715df0853ed031c3ebc1892b  v6lite/tests/test_review_round4.py
f75412cc31ed99010ba5b1d930255a9e6d8705aaf907a812ddd0c26670a0ea6e  v6lite/tests/test_review_round4_selfreview.py
a5377a4f520e6c3d340cb40571f24eae1dd431be871ecfad241b0d0080424aef  v6lite/tests/test_soak.py
732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f  v6lite/tests/test_strategy_parity.py
75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42  v6lite/tests/test_units.py
91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1  v6lite/tools/__init__.py
0da843f4a9939b03f6075ff337bd5623e49c2615746358e27359eb52b1cc4ba5  v6lite/tools/g2_probe.py
```

## 10 仍未完成

1. **L7**（broker 端保护单）尚未实现，Live 前必须完成并验收。
2. **G2 须记录**：
   - 账户就绪字段和推送节奏；
   - 各类请求的实际耗时（是否在 12/12/17 秒以内）；
   - reqAllOpenOrders 的范围，以及 BAG 订单的实际字段；
   - reqPositions 与执行回报之间的先后关系；
   - 1101 之后能否恢复。
3. **G3 须注入验证**：
   - 冻结 → 撤单 → 人工平仓 → release 的完整操作流程；
   - 撤单确认之后的迟到成交；
   - STOP 被压小后剩余部分的处理。
4. 资金时效参数（240 秒、150 秒、30 秒）、FREEZE_AFTER 60 秒和 120 秒宽限都是工程假设，待 G2 数据校准。

## 11 请 Codex 复核

1. 在 Windows 上复跑第 1 节命令，核对第 9 节清单。
2. 对第 3 节每一行和第 6 节逐项给出裁定，沿用前几轮的口径。
3. 重点反证第 4 节：
   - 冻结前的证据要求；
   - release 的终点条件是否足以关闭账本持仓；
   - 冻结期间是否还有任何入场或自动下单的路径。
4. 对 D3″、D5″、D8″、D11′–D14′、D15–D17 表态。
