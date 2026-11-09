# V6-Lite 第十九批：执行模式标记、空仓资金刷新、RTH 日报与 G3-N 示例

日期：2026-10-07 PT。代码及交付材料仅在 `C:\Dev\V6Lite-dev` 的 `dev` 分支。
**最终 code_sha：`6293dd4e2377603c`；Windows 完整测试连续三次均 926 passed / 0 failed / 0 errors / 0 skipped。**
交付快照提交 dev 并打附注 tag `v6lite-batch19`；本报告不合并 main、不部署，不是 G3 或 Live 放行记录。交付后冻结，等待 Claude 复核及合并时机裁定。

## 1. 授权范围、预检与职责

- 13:43 PT 只读预检：dev，原 HEAD `a457e07e1a083731d0b14f71477d987b17443e1f`；仅操作手册未提交，index 为空。
- 写代码前 CIM 核对：dev 的 v6lite 进程为 0。当前用户允许隔离 dev 在 G3-T 期间开发，未停运行引擎。
- 手册 SHA 精确为 `ebe46bfab4b0561714cc6d08d68f7a37ded7b97f66e2ae0a8f9b78a07be092c5`。先仅提交此文件：`8811390da5cd6cd7041afdba9a6f901fc0104da3`，`Update John operator manual (Claude, docs only)`。
- root 唯一生产实施者；batch17_tests 独立卡片/日报/G3-N 测试；batch17_runbook 独立资金调查/测试/复查；batch17_review 独立六项等价覆盖及范围复查。最终验证与 git 写入由 root 串行，交付审计由另一 Agent 只读执行。
- 未访问运行工作树或 John 运行配置；未触 `%USERPROFILE%\.v6lite`；未连接 TWS/Gateway、未推送、未建立定时任务、未输出机密。worktree 的 dev commit/tag 使用共用 Git 对象库，但 main 分支及运行工作树不改。
- 全量开始时间均在交易日 13:30 PT 之后；自有 `.venv`，没有安装或变更依赖。

## 2. 发现 → 改动 → 测试

| 项 | 发现 | 改动 | 验证 |
|---|---|---|---|
| 1 | test 模式覆盖了 dry 标记；正式运行未明确说明下单权限 | 执行权限由 dry_run 优先、再 Paper/Live 的纯显示 helper 决定；六种合法模式的启动中文标题/模式栏及手机日报卡片标题均明确唯一三类标记；TEST/qty/ATR 信息保留 | render 模块 20 passed，六个 startup/daily 组合；长标题截断 edge 通过 |
| 2 | 已证实空仓仅降 DEBUG，仍 False→True 重订阅；60 秒检查、>210 秒触发，在整分钟通常约四分钟一次，与 John 所述 2100 提示对应（源码推断） | 判断 flat 前移，已核实空仓只发 reqAccountSummary 资金下载；有仓/未知继续原经典账户刷新。资金仍以实际回复取证，非请求时刻 | funds 模块 35 passed + review edges；270 秒、ready、金额+$2、预留门控未改 |
| 3 | 收盘后最后一帧 stale quotes 被当作全天 market data，和手机 RTH 可用率矛盾 | end_of_day 只取一次本日 RTH stats，同传本地/手机，共用比例与分母；瞬时健康诊断另列明确标识。CLI 无当前进程采样则 n/a，不伪造历史比率 | 100% 6/6、50% 3/6、无样本、跨日隔离均通过；手机与本地同口径 |
| 4 | John 手册已有指定更新 | 精确哈希预检后 docs-only 独立提交；未再次编辑手册 | 文件/hash/index 单项核对通过，见第 1 节 |
| 5 | 缺 G3-N 示例 | paper_g3n.toml：合成 DU0000000、131、7497、实时1、test=false、默认保护、10:00/13:30 ET、~/.v6lite/paper-g3n | load-only 验证；mkdir 被禁止，state 不展开/创建；与 Paper/G3-T raw state 路径不同 |
| 6 | 余 33 项保护开启覆盖债务 | 新增六项业务合同等价，旧六测试不删、不放宽；余 27 项列在第 7 节 | 原六保护诊断 6 failed；新等价在上批就 6 passed，属于补覆盖，非产品修复红测 |

代码证据（行号来自最终文件）：
- v6lite/report.py:131：`if dry_run: return 演练（不下单）`，否则真实下单 Paper/Live。
- v6lite/engine.py:1005：`refresh_funds if flat else refresh_account`；flat 强证据条件、资金门控不放宽。
- v6lite/broker/ibkr.py:935：新 raw reqId、单 pending、`AvailableFunds,NetLiquidation`，无阻塞/cached-summary 调用。
- v6lite/engine.py:2694：同一 `quote_stats` 传 local 和 remote；v6lite/report.py:142 共用展示。
- v6lite/engine.py:2536：此函数与上批 AST 完全一致；保护/订单/风险/账本九核心文件 Git diff 为空。

## 3. 资金快照的证据与独立复查修正

只有当前 reqId、当前配置账户、USD/BASE、有限 AvailableFunds 和有效正 NetLiquidation 两字段、15 秒单调 TTL 内 matching End 才能发布；End 后立即 cancel 该 summary，经典账户流保持原订阅。seen_at/as_of 使用字段实际回调时间；同值不重写 as_of；接收序号防止更早 summary 覆盖更晚 stream（包括同时间戳）。AccountReady=false 不能被 summary 解除。
查询本身不刷新时间；缺字段、错账号/币种/reqId、NaN/inf、超时及旧回报不伪造新鲜度。真实 reconnect/1101 的账户流重订阅仍保留。
| 独立发现 | 候选红测 | 最终修正 |
|---|---|---|
| 标记放末尾可被 Discord clip256 裁掉 | 实际 as_embed 标题丢标记 | 标记置于严重程度后、详情前 |
| 1100/2110 后台断联不一定断 API socket，清 pending 却不 cancel | 1100→1101、2110→1102 未发 cancel | 后台事件均释放 pending summary，只有物理 disconnected 不再发 cancel |
| cancel 与 disconnect 同时失败，旧 stream 可补回资金 | 旧回调把不可用资金重写成 8000 | 隔离期间拒账户回调/资金净值读取；关闭旧 socket 失败拒 reconnect；成功新握手后才发布真实接收时刻的字段 |
| 合法 0/负余额被当无效，继续保留旧 5000 | 完整请求回报 0/-100 后仍是 5000 | 有限零/负 Funds 作为新证据覆盖旧正余额，由原金额+费用门控拒 BUY |
| 新握手期间 reset 未清旧字段缓冲 | 无新字段的 1100/1101/1102 后仍复活 7000 | 每次账户 reset 清握手缓冲；仅 reset 后字段可恢复 |

候选独立 edge 红测 6 failed / 1 passed；握手代次追加红测 3 failed / 3 passed。修复后 13 edge 全绿，相关聚焦 130 passed。独立静态复查全部关闭上述发现。

## 4. 可复现的完整第十八批红 → 最终绿

从 docs-only 基线 `8811390da5cd6cd7041afdba9a6f901fc0104da3` git archive 导出 v6lite；加新测试之前复算 `9725a81b8e5e3e11`。在临时完整源码上插入最终四个新增测试文件，再跑同样的 FakeBroker/拦截适配器测试。结果 **57 failed / 17 passed / 0 errors / 0 skipped**，证明使用原上批生产，非靠旧源码函数残留推断。新测试字节哈希在 baseline_metadata.json 中；archive、log、JUnit 哈希亦保存。

| 新模块 | 上批红：fail/pass | 最终绿 |
|---|---|---|
| test_batch19.py | 17 / 3 | 20 passed |
| test_batch19_funds.py | 27 / 8 | 35 passed |
| test_batch19_review_edges.py | 13 / 0 | 13 passed |
| test_batch19_protection_equivalents.py | 0 / 6 | 6 passed |

其中六等价在原生产已通过，不宣称修产品 bug。初始编写中出现 ProtectionRequest 无 purpose 的自建夹具错误、FillEvent 参数顺序错误，都在测试中修正后重跑；不计产品红、不称为时序 flake。最终统一基线没有 fixture errors。

Windows 命令（dev venv 已激活，无保护诊断 flag）：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUTF8 = "1"
$env:V6LITE_LEGACY_SCANNER = "C:\Dev\TradingV6\market_scanner.py"
python -B -m pytest v6lite/tests scripts/tests -q -p no:cacheprovider --junitxml=<dev evidence XML>
```

legacy scanner 仅只读用于离线策略一致性测试；未运行旧 V6 引擎。全量包括 814 项 V6-Lite + 112 项脚本模拟测试。

| 轮 | PT 开始 → 结束 | 结果 | 指纹前/后 |
|---|---|---|---|
| 1 | 2026-10-07T14:11:10.0041636-07:00 → 2026-10-07T14:15:15.2471577-07:00 | 926 passed，退出 0，无失败/错误/跳过 | 6293dd4e2377603c / 6293dd4e2377603c |
| 2 | 2026-10-07T14:15:15.4208559-07:00 → 2026-10-07T14:19:20.2521183-07:00 | 926 passed，退出 0，无失败/错误/跳过 | 6293dd4e2377603c / 6293dd4e2377603c |
| 3 | 2026-10-07T14:19:20.4003379-07:00 → 2026-10-07T14:23:25.8139853-07:00 | 926 passed，退出 0，无失败/错误/跳过 | 6293dd4e2377603c / 6293dd4e2377603c |

日志、JUnit 与 windows_full_results.json 保存精确时间和各文件哈希；三轮连续通过后未再改生产/测试文件。

## 5. 六模式卡片标题与 FakeBroker 实际样例

以下仅合成交易日/账户，未给 Discord 实际发送。完整十二张渲染文本见 `docs/batch19_evidence/cards_samples.md` 和 JSON；不是实盘/真实 Gateway 演练证据。

| 模式 / test / dry | 启动标题 | 手机日报标题 |
|---|---|---|
| paper / 0 / 1 | 🟢 V6-Lite 已启动（Paper 演练（不下单） · 实时行情） | ✅ 正常 · 演练（不下单） · V6-Lite 每日健康报告 2026-10-06 |
| paper / 1 / 1 | 🧪 TEST V6-Lite 已启动（Paper 测试模式 · 演练（不下单） · 实时行情） | TEST · ✅ 正常 · 演练（不下单） · V6-Lite 每日健康报告 2026-10-06 |
| paper / 0 / 0 | 🟢 V6-Lite 已启动（Paper 真实下单（Paper） · 实时行情） | ✅ 正常 · 真实下单（Paper） · V6-Lite 每日健康报告 2026-10-06 |
| paper / 1 / 0 | 🧪 TEST V6-Lite 已启动（Paper 测试模式 · 真实下单（Paper） · 实时行情） | TEST · ✅ 正常 · 真实下单（Paper） · V6-Lite 每日健康报告 2026-10-06 |
| live / 0 / 1 | 🟢 V6-Lite 已启动（Live 演练（不下单） · 实时行情） | ✅ 正常 · 演练（不下单） · V6-Lite 每日健康报告 2026-10-06 |
| live / 0 / 0 | 🟢 V6-Lite 已启动（Live 真实下单（Live） · 实时行情） | ✅ 正常 · 真实下单（Live） · V6-Lite 每日健康报告 2026-10-06 |

### Paper test=1 / dry=1 的完整实际启动及日报

```text
🧪 TEST V6-Lite 已启动（Paper 测试模式 · 演练（不下单） · 实时行情）
启动时间：2026-10-06 06:00 PT / 09:00 ET
模式：🧪 测试模式（仅 Paper） · 演练（不下单） · 实时行情 · DU0000000 · qty=3，stop=0.3 ATR，TP1/2/3=0.3/0.5/0.8 ATR · 版本 6293dd4e
状态：✅ 正常
Gateway 已连接 · 对账一致 · 告警通道正常 · 旧 V6：未运行
入场窗口 10:00 ET 开启
报价可用 3/3 · V6-Lite 持仓：无
监控：IB Gateway 心跳：每 60 秒，连续 2 次无应答即告警
持仓漂移：每 300 秒对账，不一致即告警
每日健康报告：13:05 PT（收盘后 5 分钟）
扫描时间：13:30 ET / 10:30 PT
手动指令（本机命令行）：这些指令只能在本机命令行执行，不能在 Discord 输入。
status 查看状态 · report 查看日报
kill --reason 停止入场 · resume 恢复
ignore 标的 · release 标的 · resolve-intent ID · clear-anomaly 代码
test-entry 标的 --operator 姓名（下个 tick 完整门控）
```

```text
TEST · ✅ 正常 · 演练（不下单） · V6-Lite 每日健康报告 2026-10-06
扫描：计划：13:30 ET 未运行
信号：无
结果：无 WOULD_BUY；gate=不适用
下单/撤单：0 次下单 · 0 次撤单
持仓与漂移：无 · 漂移：无
券商端保护：无持仓；空窗统计 A=0.000s B=0.000s
Gateway 心跳：本进程今日截至报告时: 应答 4 · 最长已验证延迟 0 秒 · 未应答 0
告警送达：本进程今日截至报告时: 发出 2 · 失败 0
行情可用率：100% 常规交易时段可用率 · 已订阅 3 · 6/6 标的样本 · 2 次采样 (本进程今日；实时行情)
code_sha：6293dd4e2377603c test=1
```

本地 market data 主行：

```text
- market data: real-time (1) | 100% RTH availability · subscribed 3 · 6/6 symbol-samples · 2 samples (this process today; LIVE)
```

### Paper test=1 / dry=0 的完整实际启动及日报

```text
🧪 TEST V6-Lite 已启动（Paper 测试模式 · 真实下单（Paper） · 实时行情）
启动时间：2026-10-06 06:00 PT / 09:00 ET
模式：🧪 测试模式（仅 Paper） · 真实下单（Paper） · 实时行情 · DU0000000 · qty=3，stop=0.3 ATR，TP1/2/3=0.3/0.5/0.8 ATR · 版本 6293dd4e
状态：✅ 正常
Gateway 已连接 · 对账一致 · 告警通道正常 · 旧 V6：未运行
入场窗口 10:00 ET 开启
报价可用 3/3 · V6-Lite 持仓：无
监控：IB Gateway 心跳：每 60 秒，连续 2 次无应答即告警
持仓漂移：每 300 秒对账，不一致即告警
每日健康报告：13:05 PT（收盘后 5 分钟）
扫描时间：13:30 ET / 10:30 PT
手动指令（本机命令行）：这些指令只能在本机命令行执行，不能在 Discord 输入。
status 查看状态 · report 查看日报
kill --reason 停止入场 · resume 恢复
ignore 标的 · release 标的 · resolve-intent ID · clear-anomaly 代码
test-entry 标的 --operator 姓名（下个 tick 完整门控）
```

```text
TEST · ✅ 正常 · 真实下单（Paper） · V6-Lite 每日健康报告 2026-10-06
扫描：计划：13:30 ET 未运行
信号：无
结果：gate=未持久化（正式订单的具体门控结果请查本机完整日志）
下单/撤单：0 次下单 · 0 次撤单
持仓与漂移：无 · 漂移：无
券商端保护：无持仓；空窗统计 A=0.000s B=0.000s
Gateway 心跳：本进程今日截至报告时: 应答 4 · 最长已验证延迟 0 秒 · 未应答 0
告警送达：本进程今日截至报告时: 发出 2 · 失败 0
行情可用率：100% 常规交易时段可用率 · 已订阅 3 · 6/6 标的样本 · 2 次采样 (本进程今日；实时行情)
code_sha：6293dd4e2377603c test=1
```

本地 market data 主行：

```text
- market data: real-time (1) | 100% RTH availability · subscribed 3 · 6/6 symbol-samples · 2 samples (this process today; LIVE)
```

## 6. 本批完成的六项等价映射

每项明确 ProtectionConfig(enabled=True)，用真实 FakeBroker GTC 执行完成生命周期；保留数量/身份/加权价格/重启回放与原 P&L 核心断言，无第二张本地 STOP。

| 原测试 | 新等价 |
|---|---|
| `test_review_round2::test_R3_late_entry_fill_in_two_batches_is_fully_managed` | `test_R3_late_entry_fill_in_two_batches_is_fully_managed_protection_on` |
| `test_review_round2::test_R3_late_entry_replay_after_restart_is_idempotent` | `test_R3_late_entry_replay_after_restart_is_idempotent_protection_on` |
| `test_review_round2::test_R3_three_late_batches_at_different_prices` | `test_R3_three_late_batches_at_different_prices_protection_on` |
| `test_review_round2::test_R6_baseline_uses_prior_close_holdings_not_current_quantity` | `test_R6_baseline_uses_prior_close_holdings_not_current_quantity_protection_on` |
| `test_review_round2::test_R6_full_exit_while_baseline_pending_counts_from_prior_close` | `test_R6_full_exit_while_baseline_pending_counts_from_prior_close_protection_on` |
| `test_review_round2::test_R6_prior_close_unknown_blocks_entries_but_not_exits` | `test_R6_prior_close_unknown_blocks_entries_but_not_exits_protection_on` |

R6 跨日部分退出按当前 fail-closed 合同采用已有 GTC 部分成交替代旧 TP：仍 20→14 股、前收130/现价120、-201；另保留20/115/106/-181和未知前收禁BUY但GTC可退出。明确断言跨日 TP 未发、未为 TP 减保护；不声称已支持跨日 TP，也不声称这六项全面覆盖外部卖单 S+T+X 的 hostile 场景。

## 7. 剩余覆盖债务与真实演练边界

保护开启等价剩 27 项，仍是实盘前置，本批未重跑整套保护诊断，也未用默认全量掩盖这些旧夹具/等价覆盖债务：

- `test_engine::test_breakeven_stop_after_tp1_sells_remaining`
- `test_engine::test_exit_retries_then_escalates`
- `test_review_fixes::test_L4_trade_result_uses_net_pnl_and_estimates_missing_commissions`
- `test_review_round2::test_C2_prior_day_held_sell_never_blocks_the_stop`
- `test_review_round2::test_C5_exit_orders_are_stamped_with_the_real_send_time`
- `test_review_round2::test_R8_commission_arriving_during_scan_can_latch`
- `test_review_round2::test_race_late_fill_of_cancelled_stop_cancels_the_retry`
- `test_review_round3::test_Q7_queued_exit_work_is_inside_the_protection_budget`
- `test_review_round3::test_SR3_blocked_exit_cannot_livelock_the_tick`
- `test_review_round3::test_SR3_stop_starts_in_the_same_pass_that_abandons_a_take_profit`
- `test_review_round4_selfreview::test_a_stop_blocked_by_missing_evidence_pages_p0`
- `test_review_round4_selfreview::test_R5_difference_found_at_the_exit_boundary_blocks_entries_at_once`
- `test_review_round4::test_F1_failed_fresh_order_query_refuses_the_sell`
- `test_review_round4::test_F7_exit_is_re_decided_with_the_latest_quote_before_sending`
- `test_review_round4::test_F8_back_off_is_honoured_when_the_queued_item_runs`
- `test_review_round4::test_missed_buy_freezes_then_flatten_and_release`
- `test_review_round4::test_price_only_correction_blocks_entries_while_exits_continue`
- `test_review_round4::test_prior_day_partial_entry_is_frozen_then_released`
- `test_review_round5_selfreview::test_late_buy_after_release_of_a_recovery_position_is_evidence_only`
- `test_review_round5_selfreview::test_refused_sell_does_not_size_a_stop_from_a_wrong_ledger`
- `test_review_round5::test_R4F1_release_needs_the_position_stream_to_agree`
- `test_review_round5::test_R4F4_frozen_position_closed_by_its_own_fills_can_be_released`
- `test_review_round5::test_R4F8_refused_take_profit_that_crossed_the_stop_pages_p0`
- `test_review_round6::test_R5F2_old_order_without_a_position_row_never_books_after_flat`
- `test_review_round6::test_R5F3_batch4_state_release_record_alone_is_a_flat_point`
- `test_review_round6::test_SR6_A2_stale_position_stream_never_silences_a_stop`
- `test_review_round7::test_P2a_earlier_versions_evidence_rows_of_released_positions_refuse_the_state`

真实 TWS 2100 与 AccountSummary 时间/字段语义只由源码及本地 raw callback 模拟确认；John/Claude 后续 G3 日志需核对：空仓不再经典退订、资金回报实际周期、扫描前 ≤270 秒及 ready/$2/预留门控、缺回复持续阻断。不连接券商的限制下，本批不宣称完成该外部验证。

仅手机日报卡片标题加执行权限标记；本地 Markdown 主行情行改 RTH，旧本地标题格式保留。CLI 单独重开报告无该进程采样时显示 n/a；不重建历史全天可用率。

旧第十七批 flat 测试只把预期经典 refresh=1 改为 funds_refresh=1 / classic=0；持仓、未知、过期证据、新成交、查询失败强安全断言原样保留。

## 8. SHA-256 清单

共 104 项：完整 v6lite 包（排除 __pycache__）、dev scripts、指定手册、全部本批证据。机器可读清单 `docs/batch19_evidence/sha256_manifest.json`。报告与清单自身不进入此表以避免循环哈希，报告自身 SHA 在交付摘要单独回报。
每项为文件字节 SHA-256；code_sha 为排序的包相对路径、NUL、文件字节、NUL 聚合 SHA 的前16位。

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| docs/V6_LITE_OPERATOR_MANUAL.md | 11346 | `ebe46bfab4b0561714cc6d08d68f7a37ded7b97f66e2ae0a8f9b78a07be092c5` |
| docs/batch19_evidence/baseline_metadata.json | 935 | `d396385475bf2fdfbdf4d6c77ac5c53d4e81e1afffbd3586fc805aec9f54c743` |
| docs/batch19_evidence/baseline_red.log | 39652 | `c33033ec9ffc09cc7920b08ba6107df48f72fe4c5d9717a98dcc1bf5d79e80cd` |
| docs/batch19_evidence/baseline_red.xml | 45327 | `7b969e757072a227379ddf6fc89f9dc636a5e9a74f5218f20d1004a6c0e66339` |
| docs/batch19_evidence/candidate_edges_red.log | 4787 | `4214591340000410126d2aa4e03d0eff563e3592772058bc2cd1032d5e15b2d8` |
| docs/batch19_evidence/candidate_handshake_red.log | 2731 | `4b76671b723c7ac15a29fabed7b2f030034e427e0e77703e23e6be3a478bb3a4` |
| docs/batch19_evidence/cards_samples.json | 12912 | `63db7f80a6a65b065b7f8332516acb2cf2faa1338abc9a9293606e9a03913f89` |
| docs/batch19_evidence/cards_samples.md | 11477 | `3366f302287471ddc6bdfe6ff41639aabc1d6e6462e6a0a6279b56c1a2d4b346` |
| docs/batch19_evidence/legacy_six_protection_on_red.log | 10081 | `a443ac6972c350677a4f2b0091be6975f8ac5b3915fdf82bb3f984efbacea583` |
| docs/batch19_evidence/red_green_by_module.json | 1222 | `7011a82adbdc0bb263f6656d74451aa19e9145dde3b04a9ee8912a83a6df621f` |
| docs/batch19_evidence/six_equivalents_baseline_green.log | 100 | `7cdd5f55905ae2ebf8e9839e1da9f6982d6c50df704c82d6c31282ddc98a0f79` |
| docs/batch19_evidence/windows_full_1.log | 1086 | `895c5f62ef350016ddb171e8c2db899456928a8089e523793f5ac25fb034e3f1` |
| docs/batch19_evidence/windows_full_1.xml | 130701 | `6a9bb8e3ef0f442d44aac3fd6dce590c128dbe9f0183ec5bc3e09c3c602b307b` |
| docs/batch19_evidence/windows_full_2.log | 1086 | `09509d633f9313e154026186b60a991476ddede1743b80fb9c1a0da16a9d356a` |
| docs/batch19_evidence/windows_full_2.xml | 130701 | `121fbdcea4a637bf6bdbc7b691b3119ce547516ca54fe8b8eb577bd899f63baf` |
| docs/batch19_evidence/windows_full_3.log | 1086 | `98ecb0c21fd9247297075fa4983fa1a5a3cc9600e416e9b1eaeb4024c83c8bab` |
| docs/batch19_evidence/windows_full_3.xml | 130701 | `53a4a8aa7dda9e20e1909f9e708c9abc34aab4c452935b0a4ff2b8e1ba5e6cc8` |
| docs/batch19_evidence/windows_full_results.json | 1709 | `de785915da54d39e7393de8c9f8300552f1c2af5172319250c61481e4b65589d` |
| scripts/tests/test_v6_launchers.py | 35142 | `fff766c38064b7ad5421eadd9639f363e06cfa5a6e6ecb1448a4c22c002b270c` |
| scripts/v6.cmd | 161 | `e6303971e31af8b57e050506ddae1c71516efc3f7c523850438fba3028fcc6a8` |
| scripts/v6.ps1 | 15298 | `9818d068769592206e8b62bc65d4f52bc8bb98b2476c754fb04e2fba985e3d88` |
| v6lite/README.md | 13718 | `4ad81212d1863b4fc11dc87d8bc8c32ea272e95563095243292ed615ced31fd9` |
| v6lite/__init__.py | 313 | `26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39` |
| v6lite/__main__.py | 26514 | `8bd77a8d308d6f3e66995bb37be39fc5debdd964cf1395ca68cced32849ffab9` |
| v6lite/alerts.py | 53232 | `4b422fd4f2c1e2c3297de04d6193cfdd80adb4dadeee428516fdae171ff00657` |
| v6lite/approval.py | 12709 | `70a3c24325b95216bb307dd261b60ccff88f0f13da3e2f94d6344e92f07b41e1` |
| v6lite/broker/__init__.py | 96 | `2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24` |
| v6lite/broker/base.py | 3720 | `3f667784f14f386df11255c54feaff0c4aecdd183350fa609c6fa2e13dbdb625` |
| v6lite/broker/fake.py | 15532 | `fa6993db04fbf1d49cf33226dd19799b5e7aeddc41ca4416569af25b7196b0a0` |
| v6lite/broker/ibkr.py | 59596 | `576ea26f934f2db9b3cc0efb4f5e6346fb97fb1acde8ecf42a49ebd713dc68b2` |
| v6lite/clock.py | 910 | `0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1` |
| v6lite/config.py | 18236 | `b68128969b2ed7dc227d45fbafe116f122b0a1d230e8d057ef92ac61fe2e4d7d` |
| v6lite/engine.py | 179102 | `de35dc898890808066139c1ee62d0ae024d5d187d4b32d31b4a4dfb0a428cc67` |
| v6lite/examples/live.toml | 2980 | `47e605b64d9b148a38dcc2697b3266ced4494f501b980987d6917424fed7bd22` |
| v6lite/examples/paper.toml | 2872 | `8d0836ae45356ed3650c33f3c3390a304ea5e073a5f0c0ca4ebc81542409e4a1` |
| v6lite/examples/paper_delayed.toml | 2971 | `258febbcf40a112fae652f95b0e0f77b8f0151eb97101da4623c40ee75e8fc49` |
| v6lite/examples/paper_g3n.toml | 840 | `683a6882b96cce0822157574298e8a90681254af90d225544219e63dfb20aced` |
| v6lite/examples/paper_test.toml | 954 | `507dea6df5c4c36a58db1bdbda8921707a3fab9721b504be98abce72b26b23d1` |
| v6lite/exits.py | 3927 | `1de7eb5637ee3c6abd1784570846d0edf8f91739ee97e2bb6e0b6651e34de093` |
| v6lite/market_calendar.py | 3925 | `1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2` |
| v6lite/models.py | 6427 | `25937bff88ac9c7aeeb5b354d2edb6fc187f4a9ce2e1736a1596537d4a3f2506` |
| v6lite/protection.py | 1315 | `3de59ef71678bf534a66d48f4a3e4b5f49b7bac5a391f1a051cf2e0e648474e5` |
| v6lite/protection_engine.py | 85415 | `1b7bc0f5a2bd75cb47c5512bfc975f5d63645dbccd4832c1599505a55d736d7a` |
| v6lite/reconcile.py | 2610 | `4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1` |
| v6lite/report.py | 22831 | `a038828db28e2bb9887261141fd9046c72f5913940a9155ef113a18cc74c7189` |
| v6lite/risk.py | 11077 | `abc521f671e878a0ab5690e1fa178dc872d29b5396b86eaeb5f016be1594c687` |
| v6lite/sizing.py | 3294 | `c9b9cc4a116a7f61d9e660dd885d280ec2a9262671f2c3c8ab5c1515724e171e` |
| v6lite/store.py | 61022 | `a47dd6e9c1a3ed59dcff0dcc0b62ba87d27dc6cae35e0359b2c54c70d662a2ce` |
| v6lite/strategy.py | 8300 | `9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e` |
| v6lite/tests/__init__.py | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| v6lite/tests/conftest.py | 209 | `aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb` |
| v6lite/tests/helpers.py | 5415 | `0c28d019b52b61f5d0b477bd7b202c409ff6761a4b4830b70c77667a3d9787d6` |
| v6lite/tests/test_batch10.py | 19369 | `5b875c8df97861e22427223e7065a74bddf04a5da1c7115b431871369d177c47` |
| v6lite/tests/test_batch11.py | 10273 | `fb7f26a0bd1969400f2d769a0d65f4c7d3a18f0162332fd46ca9f1ed99c260f8` |
| v6lite/tests/test_batch12.py | 25124 | `3dfc69a4f811549cb32c9c186bbfed3d24f7c1c956c3c743d9b28f6996037cfc` |
| v6lite/tests/test_batch12_v2.py | 20802 | `5691a234501cd71b85e3098e0cf6da2a74f2d4f5ca3eda2797a8abe17fab9af0` |
| v6lite/tests/test_batch13.py | 18609 | `12a3ed033079f129e7e70f610be004c498ea6f6f0283473f60b2eb80ad819140` |
| v6lite/tests/test_batch14.py | 16354 | `5087c53709f4208d5dc4fbe9f74b100d50ebfb2060ccf9c9834358d2c3c7b257` |
| v6lite/tests/test_batch15.py | 69505 | `706e7a108c53708273841f7c101169b41cf75aa3f32e14223963cbc33287ba2e` |
| v6lite/tests/test_batch16.py | 27734 | `02bc3283c395ec7a6903e94fe9a024ce9b40e9da81c28bc10cccc3bce67fcdd3` |
| v6lite/tests/test_batch16_card_deadline_copy.py | 1932 | `d9c3c8f080748838a81392b1a12ca03347685a1be4692ae9159471ff2cc56a51` |
| v6lite/tests/test_batch16_f5_copy.py | 14633 | `52f746366b2b14dd7247533157c715060540b8fa45170c2af8b39f5f8dbf9a64` |
| v6lite/tests/test_batch16_f6_account_evidence.py | 7194 | `6d97cec645009fd6d90c223cfeb5c902866701f75c01f91a604caddd3a0e863d` |
| v6lite/tests/test_batch16_f6_global_mismatch.py | 2874 | `86c542de449c2adab7cae4d2bc476e5108ef7ef14d9c2f45686a2404a3b9d478` |
| v6lite/tests/test_batch16_f6_release.py | 10603 | `15c23d799539945057095f56bad2e8ba8a32974383eb8d778fba0f57ede2cb96` |
| v6lite/tests/test_batch16_f6_safety.py | 5402 | `9e2464cc21430b8b4e27dd8982b1a5aee0b554a5e0698691df3156715ad65b9c` |
| v6lite/tests/test_batch16_f6_steady_fairness.py | 2893 | `e7ae2a84c08b2adadb1aa9994e835b5504db581401eecbf2bfd14678e984915d` |
| v6lite/tests/test_batch16_f6_steady_timeout.py | 2401 | `733347eda5814cbd2a14f71a717c88274bdd51376cada2c9bceeab3fd2908c48` |
| v6lite/tests/test_batch16_f7_price_flip.py | 4495 | `b2f9cf68a543999d08ab0567e6e6a5663f257d2525c48430f953de8bacfbc5dd` |
| v6lite/tests/test_batch16_f8_backoff.py | 2126 | `af5c1b6c62872c84536b222255c9a3b4229e2d7bf4ce1c4b55b1d4a780aa3539` |
| v6lite/tests/test_batch16_gap_evidence.py | 26359 | `328269e2c8fa8a0547d1d16a5904a4d6cc5462f13b093b9e6c09eb87bd4be8ba` |
| v6lite/tests/test_batch17_alerts.py | 10542 | `dfc6a85711add46c7a6e8b966c3f75f38f2bbbc7bee1b3f827882d3d9ddba5ff` |
| v6lite/tests/test_batch17_equivalence.py | 9719 | `ecb49e23e9cbcd6ea2803aacb4b649d8b8794b1c79e65f266141c655efdfca8a` |
| v6lite/tests/test_batch17_g8.py | 5126 | `4e21318f45dc6293fc4fbdc0f1c5be7c9d845782bf405fff8c6a51504bae5875` |
| v6lite/tests/test_batch17_heartbeat_account.py | 20900 | `5ab7bafaddb4ab15e16c275b45061079c46c3d642b866a91ac55f85a75c3f6de` |
| v6lite/tests/test_batch17_l3_recovery.py | 15783 | `e965ce76adb71525b1e1d018b84af3525be13fdeabd7f3f7f557dced42d675b2` |
| v6lite/tests/test_batch19.py | 10695 | `f4ca35e62e9b4ab17b7904b783d966ac8a9782466702297f5ab98c1dc9adb4e4` |
| v6lite/tests/test_batch19_funds.py | 19657 | `f9c1ae564a76752edcb60b3293eec5a4b2ac15657b4e34258ba814cf4a78dde4` |
| v6lite/tests/test_batch19_protection_equivalents.py | 13413 | `12def834c0ef3a32bf71331473a47daae9e7d2b4309347e177784421cdeec303` |
| v6lite/tests/test_batch19_review_edges.py | 7207 | `262a24defa92162552f14d80dfc1eb45d673e7a538a7c0ead745b7649515f7d6` |
| v6lite/tests/test_engine.py | 17668 | `bb8967a536a8d187fc9f1d742be17c0b4b90e193ab52731ac06d6bff8a08f461` |
| v6lite/tests/test_g2_day1.py | 10712 | `cacaf5b3115d4a298fe8b3fe065d66fe70277b8ee4cd5d9643f0e2fff0ff5f9d` |
| v6lite/tests/test_g2_day1_selfreview.py | 8081 | `fe84ef645d99f421c64f670aa7f883d16b5a24c8c15b4dd30a4aa8f035b5ea06` |
| v6lite/tests/test_g2_day2_prep.py | 2547 | `a0db6b981992a3cf20d9f959a679c608842f4619007331bae9b26908cee3ccb7` |
| v6lite/tests/test_g2_probe.py | 7222 | `ba38215d7a6946d60884d0e3dac99b641e7bcde058c4893b94cb9d62b6a6fe18` |
| v6lite/tests/test_ibkr_adapter.py | 6081 | `6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a` |
| v6lite/tests/test_review_fixes.py | 22689 | `eb91ef957bfd13864c2c92a5da2cf3a29ffdc10c9f5c74fe751daa8f4caecdd3` |
| v6lite/tests/test_review_round2.py | 38526 | `7ab32142a9a0ee2fdb54853297cf6eae619ce28de4cac4dc646a8a8035660822` |
| v6lite/tests/test_review_round3.py | 15213 | `b5bf4e5c513e9d2310dc6404f147b633790cff0e894546e9176d6c26390a7427` |
| v6lite/tests/test_review_round4.py | 18605 | `04cf584aa3b7e13842c1e7608d03e5f888b7a714ea073001ce85281fe1eb43cb` |
| v6lite/tests/test_review_round4_selfreview.py | 13981 | `8cf2bba5d54e2e644bc016e68abfe87f0ecbe26b6e54d5a44a329c61e6dc7da8` |
| v6lite/tests/test_review_round5.py | 16770 | `c45a477f2bb20f021b3d8040b3be6360d78bb8c95f0ef7ffd2ba069fdf31eeb0` |
| v6lite/tests/test_review_round5_selfreview.py | 12064 | `03adaac71b3ea18095732064c51f6f4741400adae738c3cfb9f840c247ec4a8e` |
| v6lite/tests/test_review_round6.py | 10254 | `c6eabe6e953cc75ccfa7e4fa87625a86d677ef5dd2fca9975cd101b3403d6be5` |
| v6lite/tests/test_review_round7.py | 9101 | `bcb2baea638b6ba6c063aba7776aaf1137c3cd1df98297a08311cdaf899994a5` |
| v6lite/tests/test_review_round7_selfreview.py | 6455 | `a3589fae5996245f7246f1860875a847b6761ed9f8bab5a317ab5c7d9e5e86bf` |
| v6lite/tests/test_review_round8.py | 7212 | `c65ac1c396468aae3124ffcdc0f61b2ed483547ec1b0f47119f00950008b3736` |
| v6lite/tests/test_review_round8_selfreview.py | 7315 | `e927860575cb55769a910a0dfbced96b725922da6629010c894ec2d3c86b36c3` |
| v6lite/tests/test_soak.py | 5780 | `abb1c1b1742d30353157518676a980ea162902178945701f67aeb69b82f4cd1c` |
| v6lite/tests/test_strategy_parity.py | 4014 | `732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f` |
| v6lite/tests/test_units.py | 8846 | `75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42` |
| v6lite/tools/__init__.py | 34 | `91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1` |
| v6lite/tools/g2_probe.py | 20555 | `e756a719f9f8c1cded3d1123f67fa0097b504680589319be40619bc5d7f6763e` |
| v6lite/version.py | 863 | `9aa52b23a3c8c08801f9d86c46ae1090a28c78dbde5d5b4b2923347bc74ece45` |

## 9. 提交与冻结

手册已单独 docs-only commit（第1节）。本批提交范围仅上表中本批改变/新增的文件及报告/清单，不 stage 运行配置、数据库、用户状态、缓存、机密或大文件。
第十九批 dev commit / 附注 tag 对象及报告 SHA 在最终交付回报，避免提交后重写报告形成哈希/快照循环。未合并 main、未 push；完成后所有本批交付文件冻结，等待 Claude 复核。
