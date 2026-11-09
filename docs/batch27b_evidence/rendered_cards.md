# 27b 实际离线卡片

MemoryChannel 实际 AlertCard 渲染，账户已脱敏；不是手写样例。

## 🟢 INFO · 过夜持仓当日成交时间依据 · AAPL

测试：`v6lite/tests/test_batch27b_current_day_evidence.py::test_B27b_naive_1020_TP1_time_allows_BE_ACK_then_TP2_with_unchanged_unverified_ledger[default-midnight]`

```text
🟢 INFO · 过夜持仓当日成交时间依据 · AAPL
证据：AAPL：当日成交时间按当日成交查询判定为今天（精确时间未核实）；execId=b27b-1020-tp1.01.01；依据运行约束 A、B（John 明确接受的剩余风险，不是技术证明）：A 账户独占：Lite 管理的标的只由 Lite 交易，人工处置一律按停机→撤单→平仓→release；B 账本不回滚：不手动替换、恢复、编辑 .v6lite 状态库。仍不能技术排除停机期间等量手动卖出又买回，以及状态库整体回滚后恰与券商一致。
权限：仅本轮止盈/保本日归属；不是精确时间核实，也不是完整历史成交权限。
你要做什么：1）核对标的、股数和订单编号；2）保留未核时标记，等待后续精确时间查询核实。
```

## 🟠 P1 · 过夜持仓止盈核验未通过 · AAPL

测试：`v6lite/tests/test_batch27b_current_day_evidence.py::test_B27b_each_current_day_time_proof_failure_freezes_BE_TP2_and_preserves_two_share_stop[missing-exec]`

```text
🟠 P1 · 过夜持仓止盈核验未通过 · AAPL
核验结果：AAPL：账本成交时间尚未核实：本轮当日可见成交查询没有该 execId：b27b-1020-tp1.01.01；券商 P=2 股，账本=2 股，当前工作 STP S=2 股；本地上次确认 STP S=2 股；GTC orderId=1002 permId=51002；历史账本入场=b27b-1019-entry.01.01@2026-10-19；当日可见成交=未执行（a 未通过），未知项=未执行（a 未通过）；范围=client + currently API-visible account execution queries；核验时间=2026-10-20T14:01:48+00:00；依据运行约束 A、B（John 明确接受的剩余风险，不是技术证明）：A 账户独占：Lite 管理的标的只由 Lite 交易，人工处置一律按停机→撤单→平仓→release；B 账本不回滚：不手动替换、恢复、编辑 .v6lite 状态库。仍不能技术排除停机期间等量手动卖出又买回，以及状态库整体回滚后恰与券商一致。
影响：冻结本持仓新的自动止盈/保本；不自动撤改或补建保护单，当前保护按新鲜证据及独立 P0 处理。
你要做什么：1）在 TWS 核对持仓及 GTC STP；2）核对本机账本与当日可见成交；3）保留冻结，由 John 处理差异，不要盲目新增卖单。
```

## 🟠 P1 · 过夜持仓止盈核验未通过 · AAPL

测试：`v6lite/tests/test_batch27b_current_day_evidence.py::test_B27b_each_current_day_time_proof_failure_freezes_BE_TP2_and_preserves_two_share_stop[provisional-other-day]`

```text
🟠 P1 · 过夜持仓止盈核验未通过 · AAPL
核验结果：AAPL：账本成交时间或交易日不一致；券商 P=2 股，账本=2 股，当前工作 STP S=2 股；本地上次确认 STP S=2 股；GTC orderId=1002 permId=51002；历史账本入场=b27b-1019-entry.01.01@2026-10-19；当日可见成交=未执行（a 未通过），未知项=未执行（a 未通过）；范围=client + currently API-visible account execution queries；核验时间=2026-10-20T14:01:48+00:00；依据运行约束 A、B（John 明确接受的剩余风险，不是技术证明）：A 账户独占：Lite 管理的标的只由 Lite 交易，人工处置一律按停机→撤单→平仓→release；B 账本不回滚：不手动替换、恢复、编辑 .v6lite 状态库。仍不能技术排除停机期间等量手动卖出又买回，以及状态库整体回滚后恰与券商一致。
影响：冻结本持仓新的自动止盈/保本；不自动撤改或补建保护单，当前保护按新鲜证据及独立 P0 处理。
你要做什么：1）在 TWS 核对持仓及 GTC STP；2）核对本机账本与当日可见成交；3）保留冻结，由 John 处理差异，不要盲目新增卖单。
```

## 🟠 P1 · 过夜持仓止盈核验未通过 · AAPL

测试：`v6lite/tests/test_batch27b_current_day_evidence.py::test_B27b_failed_d_card_shows_actual_complete_query_count`

```text
🟠 P1 · 过夜持仓止盈核验未通过 · AAPL
核验结果：AAPL：当日可见成交无法归属或与账本经济身份不一致：b27b-unknown-0.01.01；券商 P=3 股，账本=3 股，当前工作 STP S=3 股；本地上次确认 STP S=3 股；GTC orderId=1002 permId=51002；历史账本入场=b27b-1019-entry.01.01@2026-10-19；当日可见成交=3 条，未知项=b27b-unknown-0.01.01；范围=client + currently API-visible account execution queries；核验时间=2026-10-20T14:01:04+00:00；依据运行约束 A、B（John 明确接受的剩余风险，不是技术证明）：A 账户独占：Lite 管理的标的只由 Lite 交易，人工处置一律按停机→撤单→平仓→release；B 账本不回滚：不手动替换、恢复、编辑 .v6lite 状态库。仍不能技术排除停机期间等量手动卖出又买回，以及状态库整体回滚后恰与券商一致。
影响：冻结本持仓新的自动止盈/保本；不自动撤改或补建保护单，当前保护按新鲜证据及独立 P0 处理。
你要做什么：1）在 TWS 核对持仓及 GTC STP；2）核对本机账本与当日可见成交；3）保留冻结，由 John 处理差异，不要盲目新增卖单。
```

## 🟢 INFO · 过夜持仓自动止盈恢复 · AAPL

测试：`v6lite/tests/test_batch27b_current_day_evidence.py::test_B27b_naive_1020_TP1_time_allows_BE_ACK_then_TP2_with_unchanged_unverified_ledger[default-midnight]`

```text
🟢 INFO · 过夜持仓自动止盈恢复 · AAPL
证据：AAPL：依据运行约束 A、B，历史账本 + 当日可见三项核验符合，自动止盈恢复；券商 P=3 股，账本=3 股，当前工作 STP S=3 股；本地上次确认 STP S=3 股；GTC orderId=1002 permId=51002；历史账本入场=b27b-1019-entry.01.01@2026-10-19；当日可见成交=0 条，未知项=0；入场成交时间按收到回报时间判定为 2026-10-20 之前（未经券商查询核实）；范围=client + currently API-visible account execution queries；核验时间=2026-10-20T14:01:02+00:00；依据运行约束 A、B（John 明确接受的剩余风险，不是技术证明）：A 账户独占：Lite 管理的标的只由 Lite 交易，人工处置一律按停机→撤单→平仓→release；B 账本不回滚：不手动替换、恢复、编辑 .v6lite 状态库。仍不能技术排除停机期间等量手动卖出又买回，以及状态库整体回滚后恰与券商一致。
权限：仅止盈、保本及本次止盈自身恢复；不提供完整历史成交权限。
你要做什么：1）核对标的、股数与 GTC 编号；2）观察后续止盈/保本结果；3）出现差异时保持冻结并在同一 TWS 核查。
```

