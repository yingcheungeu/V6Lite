# V6-Lite 第三批整改报告：收窄恢复层（针对 Codex 第二轮复核）

日期：2026-09-30 PT。整改：Claude。待复核：Codex。
依据：`docs/V6_LITE_CODEX_REVIEW_ROUND2_2026-09-30.md`（Q1–Q8、D1–D10），以及 John 的两项决定：第三批按“收窄恢复层”重做而不是逐条打补丁；**v1 实盘不接管已有持仓（去掉 adopt）**。

**结论先行：** 第二轮复核中仍未关闭的反例（Q1–Q8），这次改为从结构上移除问题来源，而不是在原机制上再加条件。具体做了五件事：

1. 卖出数量只以 broker 的最新订单列表为准。
2. 用按订单归属的 `resync` 取代 `repair-position` 及其时间戳。
3. 删除 adopt。
4. 连亏按执行时间、在全部历史上计算。
5. 退出工作全部排队、逐项做预算检查。

全量离线测试 **190 passed**，连续 3 次结果一致。本报告**不宣称** G3 或 Live 可放行。所有结论只基于 FakeBroker、FakeIB 和临时数据库。整改期间没有连接 Gateway、下单、签发批准，也没有读取 `.env` 或 `state/`，没有 stage/commit/push，没有改动旧 V6、Codex 的复核文件或开发计划。

## 1 复现命令

```powershell
cd C:\Dev\TradingV6
. .\venv_v6\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
python -m pytest v6lite/tests -q -ra                              # 期望 190 passed
python -m pytest v6lite/tests/test_review_round3.py -q -ra        # 本批 29 项
```

## 2 结果总表

| 测试集 | 本批之前的代码（第二轮 + G2 工具） | 本批 |
|---|---|---|
| `test_review_round3.py`（29 项：Q1–Q8、D7、D10、resync 场景、交付前自审 SR3） | 28 failed / 1 passed。通过的那项是 SR3 的“不能无限循环”：排队机制是本批才引入的，所以对旧代码只起护栏作用 | 29 passed |
| v6lite 全量 | 169（第二轮 165 项 + G2 工具 4 项） | **190 passed** ×3，每次约 29 秒 |
| 退役的测试 | — | 第二轮中 8 个针对 `repair-position` 和 adopt 的测试已删除，因为这两个功能已删除。它们的场景由 resync 测试覆盖（第 5 节）。`test_engine.py` 中的 adopt 测试改为“v1 不能 adopt” |

## 3 逐项：发现 → 改动 → 测试

| 项 | 改动 | 测试 |
|---|---|---|
| **Q1** 孤立或重新 working 的卖单不占用可卖数量（D3、D6） | 退出判断只排队，不直接发单。发送前先重新查询 broker 的 working 订单：可卖股数 = broker 持仓 − 列表中所有 working SELL 的未成交量（本系统的、挂起的、孤立的、非本系统的都算；方向未知的按 SELL 算）− 本系统已发出但尚未出现在列表里的 SELL。列表缺失或超过 60 秒不发任何 SELL，发 P1 并 30 秒后重试。STOP 不足时按可卖数下单并告警，止盈不足时不发。挂起的订单一旦出现在 working 列表中，发 P1 “held order is working”，其股数被预留；hold 不会被自动解除。适配器现在返回账户内全部 working 订单（带 `ours` 标记与方向），只有本系统订单参与恢复与撤单 | `test_Q1_orphan_working_sell_reserves_the_shares`（修复前另发 SELL 20，可卖到 −20）；`test_Q1_held_order_that_shows_up_working_again_is_reserved`（修复前 STOP 20，可到 −6）；`test_Q1_sell_capacity_needs_fresh_broker_evidence` |
| **Q2/Q3/Q4** repair 的时间戳去重失败、吞掉更正、不支持纯价格更正（D4、D5） | **删除 `repair-position` 及 `repaired_through`**，新增 `resync SYMBOL --broker-qty N`，详见第 4 节。迟到成交按**订单归属**判断，不再按时间：凡是 resync 之前的订单，其后续成交一律隔离，不入账。更正识别先于归属判断。纯价格更正同样用 resync，按 broker 均价重建 | `test_Q2_resync_then_late_visible_execution_is_not_booked_twice`、`test_Q2_resync_refused_while_the_symbol_has_working_orders`、`test_Q2_resync_freezes_new_entries_for_the_day`、`test_Q3_correction_is_detected_before_resync_attribution`、`test_Q4_price_only_correction_is_recovered_by_resync_from_broker_cost`、`test_resync_keeps_the_exit_plan_and_finished_stages` |
| **Q5** adopt 的原始成本可被改写 | 按 John 的决定，**v1 删除 adopt**：CLI 不再有 `adopt`，`set_disposition` 只接受 `ignore`，schema 2→3 迁移时删除遗留的 adopt 处置，day_pnl 中的 adopt 分支删除 | `test_Q5_adopt_is_not_available_in_v1`、`test_v1_has_no_adopt_unmanaged_holdings_stay_unmanaged` |
| **Q6** 连亏按入账顺序排列、截断为 50 笔（D1） | `closed_at` 取该持仓**全部成交中最晚的执行时间**（不是入账时间，也不是最后入账的那笔）。`closed_trades` 取全部历史（一次查询，无 LIMIT），排序依次按 closed_at、开仓时间、position_id。resync 计为一次亏损事件（D14） | `test_Q6_streak_follows_execution_time_not_arrival_time`（修复前 None，应为 11:10）；`test_Q6_streak_uses_the_full_history`（修复前 10:30，应为 11:40）；`test_SR3_closed_at_is_the_latest_execution_of_the_position` |
| **Q7** 保护检查自身的慢操作不在预算内（D8） | 退出判断（decision pass）不做阻塞调用，只把发单、撤单、刷新成交、补订阅排进有键去重的队列。队列每次只执行开始时已有的项。每项阻塞调用前调用 `_protect(kind)`，`_protect` 在处理完队列后用实际时钟再检查一次调用方的预算。被拒的退出有 30 秒退避。所有撤单（包括迟到成交触发的撤单）都走队列。shutdown 清空队列，并拒绝新的入队 | `test_Q7_queued_exit_work_is_inside_the_protection_budget`（修复前间隔 45 秒）；SR3 livelock 与 stale-TP 两项 |
| **Q8** 错账户库先被迁移，启动失败仍撤单（D10） | `run` 先用只读 URI 核对账户、模式和角色，再打开 Store、连接 broker；不匹配时直接拒绝，文件字节不变。`Engine.startup` 先绑定身份，再连接。新增 `run_engine`：启动在接管订单恢复（`took_over`）之前失败时只断开连接，不走撤单清理。状态库每次升级前自动备份为 `*.vN-backup-*` | `test_Q8_run_refuses_a_foreign_state_file_before_touching_it`、`test_Q8_failed_startup_never_cancels_working_orders`、`test_D10_migration_keeps_a_backup_of_the_version_1_file` |
| **D7** `AccountReady` 与 `accountReady` 两种拼写 | 不区分大小写匹配 | `test_D7_account_ready_key_is_matched_case_insensitively`。真实拼写和推送节奏由 G2 探针记录 |
| **D9** 撤单与迟到成交的竞态 | 新的保证来自三层：卖出前最新订单列表的数量预留（Q1）；已结算退出单出现迟到成交时撤下其余 SELL；“broker 持仓 = 账本数量”检查在判断和发送两处都做。撤单生效之前的时间窗仍无法在本地消除，列入 G3 注入验证 | 第二轮 race 两项、Q1 三项 |

## 4 `resync` 的语义（需 Codex 重点复核）

`python -m v6lite resync AAPL --broker-qty N --operator John --reason "..."`：CLI 只登记请求，引擎在下一次对账时应用。依次检查：

1. 不是 dry-run。
2. 最新查询 open orders 与 executions 均成功，且列表中**该标的没有任何 working 订单**（任何来源），账本中也没有非挂起的在途订单。
3. 取证之后重新读取 broker 持仓，数量必须正好等于 N，且为非负整数。

通过后在一个事务里完成：

- 旧持仓改为 `RESYNCED`，qty 设为 0，不计入当日 P&L 和交易结果。
- 该标的**全部**更早的订单标记 `superseded_by`，其中仍处于 active 的改为 CANCELLED。
- N > 0 时按 broker 均价新建 `resync` 持仓：止损、止盈、保本价和 tp 完成标志沿用旧持仓，已完成（DONE）的阶段一并复制。
- 写入不可改的 `resyncs` 记录并审计。

之后：被标记订单的迟到成交一律隔离；当天日停锁定（原因为 resync），当天不再新开仓；这次 resync 在连亏里计为一次亏损。

之所以可以按订单归属，而不必按时间判断：每笔 V6-Lite 成交都带 orderRef，也就是意图 ID；而 resync 要求在该标的没有任何 working 订单时才执行，所以被取代的订单之后不可能再产生新成交，只可能是已经发生、迟到回报的那些。

## 5 已删除的功能及其场景去向

| 删除 | 原测试场景 | 现由 |
|---|---|---|
| `repair-position`（含 `--intent`、`--manual`、`--day`、`repaired_through`） | 漏记 BUY 或 SELL、数量不符拒绝、跨日部分入账、TP1 重复卖出、修复后原成交重复入账 | `test_resync_after_a_missed_buy`、`test_resync_after_a_missed_sell_and_refusal_on_quantity_mismatch`、`test_resync_to_zero_closes_the_position`、`test_prior_day_partial_entry_resolved_then_resynced`、`test_resync_supersedes_a_held_intent`、`test_resync_keeps_the_exit_plan_and_finished_stages`、Q2 |
| `adopt` | 当日接管的日损参考价 | 功能已删除，Q5 |

## 6 交付前自审（SR3）

第一版第三批完成后，另起一个独立的只读审查代理，做了对抗性审查，以下均已修复并固化为测试：

| 编号 | 严重度 | 问题 | 处理 / 测试 |
|---|---|---|---|
| SR3-1 | P1 | 被拒的退出单每一轮都会重新入队，队列里的慢调用累计超过 30 秒后 `_run_queue` 永不返回，另一只股票的 STOP 永远不被重新定价，连 Ctrl+C 也无法生效 | 队列每次只执行开始时已有的项；每个 `_protect` 调用最多处理一轮队列；被拒退出退避 30 秒；STOP 替换已排队的止盈时保持原位置。`test_SR3_blocked_exit_cannot_livelock_the_tick` |
| SR3-2 | P2 | 排队的止盈在自己的取证期间被一轮判断放弃，之后仍被发出 | 取得可卖数量之后重新核对持仓、阶段、在途订单及 broker 数量是否等于账本。`test_SR3_queued_take_profit_is_not_sent_after_the_stop_took_over` |
| SR3-3 | P2 | resync 使用取证之前读到的 broker 持仓 | 取证之后重新读取。`test_SR3_resync_rechecks_the_broker_quantity_after_its_evidence` |
| SR3-4 | P2 | closed_at 取的是最后入账那笔成交的时间 | 改为最晚执行时间。`test_SR3_closed_at_is_the_latest_execution_of_the_position` |
| SR3-5 | P2 | 同一标的上的期权挂单被当作股票卖单预留（例如卖出备兑认购） | 适配器只把 STK 计入预留。`test_SR3_option_orders_...` |
| SR3-S1 | P1（推测） | ib_insync 由 openOrder 建立的 Trade 在收到 orderStatus 之前 remaining 为 0，导致外来卖单不预留任何股数 | remaining ≤ 0 时改用 totalQuantity − filled。同上测试。真实到达顺序由 G2 探针记录 |
| SR3-S3 | P2 | 止盈阶段 PENDING 且未发出时价格穿过止损，STOP 要晚一整个间隔 | 放弃止盈后在同一轮立即启动 STOP。`test_SR3_stop_starts_in_the_same_pass_...` |
| SR3-S4 | P2 | resync 到 0 会把一笔亏损从连亏中抹掉 | resync 计为一次亏损。`test_SR3_a_resync_counts_as_a_loss_for_the_streak` |

## 7 需要 Codex 裁定的设计判断（更新）

| # | 判断 |
|---|---|
| D3′ | 孤立单、外来单、挂起但仍 working 的单：不自动撤、不接管；其股数一律预留，因此 STOP 可能被压小或阻止，这时发 P1，由操作员在 TWS 处理 |
| D5′ | 用 `resync`（以 broker 为准，按订单归属隔离迟到成交，当天冻结入场并计一次亏损）取代任何形式的补记成交 |
| D6′ | hold 不阻挡退出判断；但 broker 列表中的正证据（仍在 working）优先，通过预留生效 |
| D8′ | 预算模型改为：判断不阻塞，队列逐项执行，每次阻塞调用前和队列结束后都按实际时钟复核。上界仍以每次调用不超过声明值为前提，实际耗时由 G2 测量 |
| D11 | SELL 发送的证据时效是 60 秒；证据不足时 STOP 也不发（P1 后重试），不按旧快照发 |
| D12 | 被拒退出退避 30 秒；每个 `_protect` 调用最多处理一轮队列 |
| D13 | 非 STK 挂单不参与股数预留 |
| D14 | 每次 resync 在连亏中计为一次亏损，并当天日停 |

## 8 SHA-256 清单（37 个文件，已排除 `__pycache__`）

增量（相对本批之前）：engine.py +314/−157、store.py +150/−112、`__main__.py` +78/−64、broker/ibkr.py +29/−6、risk.py +18/−20、broker/fake.py +5/−3、models.py +3/−1、reconcile.py +1/−1、README.md +9/−6；tests：新增 round3、改动 round2 和 test_engine。schema 版本：3（v1、v2 自动迁移，迁移前自动备份）。

```
a97c7f8afb06af543d1a003d323539d0246b0641ead007bb8171c6de5145122d  v6lite/README.md
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
cb26f70f113d5e6c705f8d0804f53a437ec6d2f4b2bb14835721e2652713e0d9  v6lite/__main__.py
3d1f717dc462ab0c1e48821bbfe82d7232842a2ef63570741213180fe899a5b1  v6lite/alerts.py
162daa7d98a2362bc29521fcf65472c5590fb282da694b5011da934d8ffc26b5  v6lite/approval.py
2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24  v6lite/broker/__init__.py
73ed0a2f49a398d3b4d3dfa7669bb7464530b5a3b8a92da03c901db0bd7295c1  v6lite/broker/base.py
bcf4aa7444e44e7d1f73ee074f81ef03b988e215810ff9e61ecba7b220c0c98a  v6lite/broker/fake.py
e93aaaf7a93114f34d4f3b8e09ca4dd84f97df682d4f46ee816cab498d633e5e  v6lite/broker/ibkr.py
0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1  v6lite/clock.py
e8e10582fbf82d3849bcca4662d039d910923528fc15a25da99155e3af9b00f4  v6lite/config.py
82bcdb4bee79fa6921c80c7188e4b4f26323da42079723fd58849cef5b6e9c9a  v6lite/engine.py
1032534d1665efed3143d97bed9f7525410772a49ee5894f2000d0bcdd2fd673  v6lite/examples/live.toml
9c8682f8fa30a91703a53994745e948bc778186688dfe843acc353a5d8df123b  v6lite/examples/paper.toml
c3b4a074f96c5a25789afb5fabf088ead3b1daddaaa1ee5477ec1399c77627e6  v6lite/exits.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
172ef940ec8e5b5959d379c5520ac0f7cfbdf44f4c930a20fd01cfd2ecacadfb  v6lite/models.py
682ec5ba57eb7420f43c67197b7ed4202e31a5f5ef826a28ed825154129487d4  v6lite/reconcile.py
6753b504179cbfce65344e532685c85ca9d6c1aec3d64c2ba5e223295dfe9fb0  v6lite/report.py
660e2a05bd5fd19f5cd47101eff737299ccf78d42a5f7f86ee0cd8d3d0afad81  v6lite/risk.py
c7b9000919a9d7672aead2eddab786f96064946cf6084cf6b19cf9351ae70e26  v6lite/sizing.py
5c1230e0f363d618341e6a181a3ea04cd85487ea49327900c24e65fd51abef3e  v6lite/store.py
9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e  v6lite/strategy.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  v6lite/tests/__init__.py
aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb  v6lite/tests/conftest.py
fd6c76b9f1e7bd2ff59cd82b86453619f156f1c9c61885aab31f248f01f1f038  v6lite/tests/helpers.py
6445bac89adb5356c19d98e63e0bcd6061bba35f0f476f81269f0482037a59bf  v6lite/tests/test_engine.py
e70899902f5e8338d3b01755e1c747e0fb09322f32c283a2eac51a2f2d5ff86a  v6lite/tests/test_g2_probe.py
6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a  v6lite/tests/test_ibkr_adapter.py
02f0537a763c12ad6254173078e251ff833aae4ac148fbfc45687ed69d0ce45a  v6lite/tests/test_review_fixes.py
7aa61a1b33078abd68d503fccf6d61b76cd94b2957fa4a74b143d966bb40db7d  v6lite/tests/test_review_round2.py
0a84116ce9167b6214e976223c07a1d397242086197c2806900d4b9434e28859  v6lite/tests/test_review_round3.py
a5377a4f520e6c3d340cb40571f24eae1dd431be871ecfad241b0d0080424aef  v6lite/tests/test_soak.py
732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f  v6lite/tests/test_strategy_parity.py
75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42  v6lite/tests/test_units.py
91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1  v6lite/tools/__init__.py
0da843f4a9939b03f6075ff337bd5623e49c2615746358e27359eb52b1cc4ba5  v6lite/tools/g2_probe.py
```

## 9 仍未完成

1. **L7**（broker 端保护单）尚未实现；Live 前必须完成并验收。
2. **G2 须记录**：账户就绪字段的实际拼写与推送节奏；每类请求的实际耗时；reqAllOpenOrders 的范围，以及外来订单 openOrder 与 orderStatus 的到达顺序（SR3-S1）；成交与佣金回报的时机；1101 之后能否恢复。
3. **G3 须注入验证**：撤单确认之后的迟到成交、STOP 被压小后的剩余处理、resync 的完整操作流程。
4. 预算上界依赖声明的最坏耗时；资金时效参数（240 秒、150 秒、30 秒）仍是工程假设，待 G2 数据校准。

## 10 请 Codex 复核

1. 在 Windows 上复跑第 1 节命令，核对第 8 节清单。
2. 对第 3 节每一行和 SR3 逐项给出裁定，沿用前两轮的口径。
3. 重点反证第 4 节 resync 的前提（“没有 working 订单就不可能再有新成交”），以及第 3 节 Q7 的队列预算。
4. 对 D3′、D5′、D6′、D8′、D11–D14 表态。
