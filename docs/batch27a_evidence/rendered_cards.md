# 27a 实际 FakeBroker 卡片

来自隔离离线测试真实 MemoryChannel 的 AlertCard.as_embed()/as_plain_text()；账户字段已脱敏，不是重写卡片。

## 🟢 INFO · 过夜持仓自动止盈恢复 · AAPL

测试：`v6lite/tests/test_batch27a_execution_time.py::test_B27a_1013_history_reboot1019_authorizes_TP_without_rewriting_history[callback-unverified]`

```text
🟢 INFO · 过夜持仓自动止盈恢复 · AAPL
证据：AAPL：依据运行约束 A、B，历史账本 + 当日可见三项核验符合，自动止盈恢复；券商 P=3 股，账本=3 股，当前工作 STP S=3 股；本地上次确认 STP S=3 股；GTC orderId=1002 permId=51002；历史账本入场=0000e0d5.6ace9d9f.01.01@2026-10-13；当日可见成交=0 条，未知项=0；入场成交时间按收到回报时间判定为 2026-10-19 之前（未经券商查询核实）；范围=client + currently API-visible account execution queries；核验时间=2026-10-19T14:01:00+00:00；依据运行约束 A、B（John 明确接受的剩余风险，不是技术证明）：A 账户独占：Lite 管理的标的只由 Lite 交易，人工处置一律按停机→撤单→平仓→release；B 账本不回滚：不手动替换、恢复、编辑 .v6lite 状态库。仍不能技术排除停机期间等量手动卖出又买回，以及状态库整体回滚后恰与券商一致。
权限：仅止盈、保本及本次止盈自身恢复；不提供完整历史成交权限。
你要做什么：1）核对标的、股数与 GTC 编号；2）观察后续止盈/保本结果；3）出现差异时保持冻结并在同一 TWS 核查。
```

## 🟠 P1 · 过夜持仓止盈核验未通过 · AAPL

测试：`v6lite/tests/test_batch27a_execution_time.py::test_B27a_historical_callback_five_invalid_receipts_freeze_TP_keep_three_share_STP[under-one-hour]`

```text
🟠 P1 · 过夜持仓止盈核验未通过 · AAPL
核验结果：AAPL：账本成交时间尚未核实：账本成交时间尚未核实：收到时间未早于今日零点前一小时；券商 P=3 股，账本=3 股，当前工作 STP S=3 股；本地上次确认 STP S=3 股；GTC orderId=1002 permId=51002；历史账本入场=0000e0d5.6ace9d9f.01.01@2026-10-18；当日可见成交=未执行（a 未通过），未知项=未执行（a 未通过）；范围=client + currently API-visible account execution queries；核验时间=2026-10-19T14:01:00+00:00；依据运行约束 A、B（John 明确接受的剩余风险，不是技术证明）：A 账户独占：Lite 管理的标的只由 Lite 交易，人工处置一律按停机→撤单→平仓→release；B 账本不回滚：不手动替换、恢复、编辑 .v6lite 状态库。仍不能技术排除停机期间等量手动卖出又买回，以及状态库整体回滚后恰与券商一致。
影响：冻结本持仓新的自动止盈/保本；不自动撤改或补建保护单，当前保护按新鲜证据及独立 P0 处理。
你要做什么：1）在 TWS 核对持仓及 GTC STP；2）核对本机账本与当日可见成交；3）保留冻结，由 John 处理差异，不要盲目新增卖单。
```

## 🟢 持仓已关闭

测试：`v6lite/tests/test_batch27a_lifecycle.py::test_B27a_1016_Submitted_then_STP_fill_inside_query_closes_ledger_without_false_P0`

```text
🟢 持仓已关闭
标的与持仓：AAPL；positionId=pos-99ec110ffc6246e8b7c4；剩余持仓 0 股
盈亏：AAPL 毛盈亏 -0.39 USD，已回报佣金 1.00 USD，net P&L -1.39 USD；1 笔佣金未回报，佣金未计（尚非最终净盈亏）
说明：未回报佣金到账后以账本复算；风控继续使用原保守估算，未据此解除任何门控。
```

## 🟢 券商保护事件已解除 · AAPL

测试：`v6lite/tests/test_batch27a_lifecycle.py::test_B27a_fresh_healthy_overnight_stop_ends_old_evidence_incident_without_granting_full_history[verified-history]`

```text
🟢 券商保护事件已解除 · AAPL
发生了什么：当日新鲜持仓、工作单、成交查询恢复；原 STP 强身份及足额覆盖已确认，未授予完整历史权限
影响：本保护事件已解除；其他交易门控继续独立核验。
你要做什么：1）在同一 TWS 核对持仓和订单；2）不要绕过其他门控。
```

## 🟢 V6-Lite 已启动（Paper 真实下单（Paper） · 实时行情）

测试：`v6lite/tests/test_batch27a_execution_time.py::test_B27a_1013_history_reboot1019_authorizes_TP_without_rewriting_history[broker-time-verified]`

```text
🟢 V6-Lite 已启动（Paper 真实下单（Paper） · 实时行情）
启动时间：2026-10-19 07:01 PT / 10:01 ET
模式：Paper · 真实下单（Paper） · 实时行情 · DU******001 · 版本 b736734f
状态：⚠️ 需关注
IBKR 已连接 · 对账一致 · 告警通道正常 · 旧 V6：未运行
daily_loss_baseline_unknown:prior_close:AAPL
前收盘价基准暂缺：每 5 分钟自动重试历史查询，取得有效前一交易日收盘价后恢复；恢复时间取决于券商回报。此前新入场保持阻断；退出与原保护核验继续，保护状态仍须独立核验。
报价可用 3/3 · V6-Lite 持仓：AAPL 3
监控：IBKR 心跳：每 60 秒，连续 2 次无应答即告警
持仓漂移：每 300 秒对账，不一致即告警
每日健康报告：13:05 PT（收盘后 5 分钟）
扫描时间：10:00 ET / 07:00 PT
手动指令（本机命令行）：这些指令只能在本机命令行执行，不能在 Discord 输入。
status 查看状态 · report 查看日报
kill --reason 停止入场 · resume 恢复
ignore 标的 · release 标的 · resolve-intent ID · clear-anomaly 代码
```

## 🟢 INFO · 过夜持仓自动止盈恢复 · AAPL

测试：`v6lite/tests/test_batch27a_execution_time.py::test_B27a_1013_history_reboot1019_authorizes_TP_without_rewriting_history[broker-time-verified]`

```text
🟢 INFO · 过夜持仓自动止盈恢复 · AAPL
证据：AAPL：依据运行约束 A、B，历史账本 + 当日可见三项核验符合，自动止盈恢复；券商 P=3 股，账本=3 股，当前工作 STP S=3 股；本地上次确认 STP S=3 股；GTC orderId=1002 permId=51002；历史账本入场=0000e0d5.6ace9d9f.01.01@2026-10-13；当日可见成交=0 条，未知项=0；范围=client + currently API-visible account execution queries；核验时间=2026-10-19T14:01:00+00:00；依据运行约束 A、B（John 明确接受的剩余风险，不是技术证明）：A 账户独占：Lite 管理的标的只由 Lite 交易，人工处置一律按停机→撤单→平仓→release；B 账本不回滚：不手动替换、恢复、编辑 .v6lite 状态库。仍不能技术排除停机期间等量手动卖出又买回，以及状态库整体回滚后恰与券商一致。
权限：仅止盈、保本及本次止盈自身恢复；不提供完整历史成交权限。
你要做什么：1）核对标的、股数与 GTC 编号；2）观察后续止盈/保本结果；3）出现差异时保持冻结并在同一 TWS 核查。
```

## 🟠 P1 · 过夜持仓止盈核验未通过 · AAPL

测试：`v6lite/tests/test_batch27a_execution_time.py::test_B27a_history_time_exception_never_waives_fresh_b_c_d[unknown-today-fill-verified]`

```text
🟠 P1 · 过夜持仓止盈核验未通过 · AAPL
核验结果：AAPL：当日可见成交无法归属或与账本经济身份不一致：b27a-unknown.01.01；券商 P=3 股，账本=3 股，当前工作 STP S=3 股；本地上次确认 STP S=3 股；GTC orderId=1002 permId=51002；历史账本入场=0000e0d5.6ace9d9f.01.01@2026-10-13；当日可见成交=未完成 条，未知项=b27a-unknown.01.01；范围=client + currently API-visible account execution queries；核验时间=2026-10-19T14:01:00+00:00；依据运行约束 A、B（John 明确接受的剩余风险，不是技术证明）：A 账户独占：Lite 管理的标的只由 Lite 交易，人工处置一律按停机→撤单→平仓→release；B 账本不回滚：不手动替换、恢复、编辑 .v6lite 状态库。仍不能技术排除停机期间等量手动卖出又买回，以及状态库整体回滚后恰与券商一致。
影响：冻结本持仓新的自动止盈/保本；不自动撤改或补建保护单，当前保护按新鲜证据及独立 P0 处理。
你要做什么：1）在 TWS 核对持仓及 GTC STP；2）核对本机账本与当日可见成交；3）保留冻结，由 John 处理差异，不要盲目新增卖单。
```

## 🟢 持仓已关闭

测试：`v6lite/tests/test_batch27a_execution_time.py::test_B27a_callback_schedules_complete_today_query_and_verifies_known_exec_once[1016-STP-callback]`

```text
🟢 持仓已关闭
标的与持仓：AAPL；positionId=pos-e1b3b50d8a4b47e1b91b；剩余持仓 0 股
盈亏：AAPL 毛盈亏 -0.39 USD，已回报佣金 2.00 USD，net P&L -2.39 USD；佣金已全部回报
说明：未回报佣金到账后以账本复算；风控继续使用原保守估算，未据此解除任何门控。
```

