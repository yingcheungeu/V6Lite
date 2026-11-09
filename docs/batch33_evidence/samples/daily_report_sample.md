# V6-Lite daily report 2026-10-29 (paper)

- account: DU******001   state role: formal
- code_sha: a864ccd2f7680fba
- 本次仅渲染主报，未导出附件；完整关键事实保留在状态库 audit/fills（可用 report 命令导出）。
- market data: real-time (1) | n/a RTH availability (no runtime RTH samples supplied)
- last health market data (instantaneous, not RTH availability): real-time (1)

- realized P&L: 0.00   fees: 0.00   net: 0.00（最终佣金口径）
- entries submitted: 0 / 4
- daily stop latched: False
- open positions: AAPL 3@344.40 stop 340.58
- 券商端保护: AAPL 截至 2026-10-29T13:29:57+00:00 最近一次券商快照确认，当前仍需核实，本地状态=WORKING P=3 S=3 类型=STP 止损价（触发后市价成交）=340.58 orderId=2890；空窗统计 A=0.000s；B（正常）最大 0.000s；B（测试注入）0.000s；正常止盈 B ≤ 5 秒：合规
- kill switch: off
- open anomalies: none
- frozen symbols (operator must flatten, then `release`): none
- released positions (P&L of the operator's TWS trades is NOT in the figures above): none

## Test TP timeout / 测试止盈超时
- none

## Opening TP deferrals / 开盘止盈延后
- 开盘止盈延后：1 次
- AAPL positionId=pos-8f0ab0c078c241a59ce8 TP1 延后于 2026-10-29T13:30:10+00:00；预计 2026-10-29T13:30:30+00:00；实际开始 2026-10-29T13:30:30+00:00（TP1）

## Commission reconciliation / 佣金核对
- 当日无已关闭持仓

## Fills
- none

## Execution time verification / 成交时间核实
- AAPL execId=b28b-1020-entry.01.01：入场成交时间按收到回报时间判定为 2026-10-29 之前（未经券商查询核实）

## Events

| Kind | Count | First UTC | Last UTC |
|---|---:|---|---|
| CONNECTION_SNAPSHOT_STATUS | 1 | 2026-10-29T12:59:59+00:00 | 2026-10-29T12:59:59+00:00 |
| ORDER_PRICE_OBSERVATION | 4 | 2026-10-29T13:29:55+00:00 | 2026-10-29T13:29:56+00:00 |
| STAGE_BEGIN | 1 | 2026-10-29T13:30:30+00:00 | 2026-10-29T13:30:30+00:00 |
| STARTUP | 1 | 2026-10-29T12:59:59+00:00 | 2026-10-29T12:59:59+00:00 |
| TP_START_DEFERRED_OPEN | 1 | 2026-10-29T13:30:10+00:00 | 2026-10-29T13:30:10+00:00 |

### Important events
- 12:59:59Z CONNECTION_SNAPSHOT_STATUS {"broker_echo_lmt": 340.19000000000005, "broker_order_id": 2890, "error_code": 0, "intent_id": "v6l-protect-9162cc4bd0864a9dbdcc", "perm_id": 52890, "raw_status
- 12:59:59Z STARTUP {"account": "DU******001", "active_orders": [["AAPL", "PROTECT", "SENT"]], "alerts_healthy": true, "automatic_reconnect_stopped": false, "broker_connected": true,
- 13:30:10Z TP_START_DEFERRED_OPEN {"ask": 350.03, "ask_raw": "350.03", "bid": 349.97, "bid_raw": "349.97", "last": 350.0, "last_raw": "350.0", "last_time": null, "mark": 350.0, "position_id": "p
- 13:30:30Z STAGE_BEGIN {"position_id": "pos-8f0ab0c078c241a59ce8", "stage": "TP1", "target": 1}

## Operator notes

- (market context, manual actions, things to review)
