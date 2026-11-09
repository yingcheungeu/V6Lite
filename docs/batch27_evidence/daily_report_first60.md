# V6-Lite daily report 2026-10-15 (paper)

- account: DU******001   state role: formal
- code_sha: unknown
- 完整审计与账本附件：[2026-10-15.critical.jsonl](2026-10-15.critical.jsonl)（共 18 条事实，审计 15 条，账本 3 条；保守安全超集，并非全部为 P0）
- 附件 SHA-256：26391187d0ed4fb49abb88b35f0e2ed2fd531861c4012fb8efa7bfb220592c80
- market data: real-time (1) | n/a RTH availability · subscribed 0 · 0/0 symbol-samples · 0 samples (this process today; LIVE)
- last health market data (instantaneous, not RTH availability): real-time (1)

- realized P&L: 0.00   fees: 1.00   net: -1.00
- entries submitted: 0 / 20
- daily stop latched: False
- open positions: AAPL 3@342.53 stop 340.03
- 券商端保护: AAPL 截至 2026-10-16T00:10:14.771760+00:00 最近一次券商快照确认，当前仍需核实，本地状态=WORKING P=3 S=3 类型=STP 止损价（触发后市价成交）=340.52 orderId=1002；空窗统计 A=0.000s B=0.000s
- kill switch: off
- open anomalies: none
- frozen symbols (operator must flatten, then `release`): none
- released positions (P&L of the operator's TWS trades is NOT in the figures above): none

## Fills
- 11:16:41Z AAPL BUY 3 @ 342.53 realized 0.00

## Execution time verification / 成交时间核实
- 当日无未核实成交时间

## Events

| Kind | Count | First UTC | Last UTC |
|---|---:|---|---|
| FILL | 1 | 2026-10-15T11:16:41+00:00 | 2026-10-15T11:16:41+00:00 |
| INTENT_CREATED | 2 | 2026-10-15T11:16:41+00:00 | 2026-10-15T11:16:41+00:00 |
| INTENT_STATUS | 3 | 2026-10-15T11:16:41+00:00 | 2026-10-15T11:16:41+00:00 |
| OBSERVATION_SUMMARY | 26 | 2026-10-15T11:46:41.386847+00:00 | 2026-10-16T00:16:42+00:00 |
| ORDER_PRICE_OBSERVATION | 1 | 2026-10-15T11:16:41+00:00 | 2026-10-15T11:16:41+00:00 |
| PROTECT_PLANNED | 1 | 2026-10-15T11:16:41+00:00 | 2026-10-15T11:16:41+00:00 |
| PROTECT_STATE | 1 | 2026-10-15T11:16:41+00:00 | 2026-10-15T11:16:41+00:00 |
| SCAN | 1 | 2026-10-15T17:43:55.042553+00:00 | 2026-10-15T17:43:55.042553+00:00 |
| SHUTDOWN | 1 | 2026-10-16T00:16:42+00:00 | 2026-10-16T00:16:42+00:00 |
| TP_EVIDENCE | 5 | 2026-10-15T11:18:21.580271+00:00 | 2026-10-15T22:28:53.688588+00:00 |

### Important events
- 11:16:41Z INTENT_CREATED {"intent_id": "v6l-entry-b27-0", "limit": 342.53, "purpose": "ENTRY", "qty": 3, "side": "BUY", "symbol": "AAPL"}
- 11:16:41Z INTENT_STATUS {"intent_id": "v6l-entry-b27-0", "note": null, "status": "SENT"}
- 11:16:41Z FILL {"exec_id": "0000fa4e.00000001.01.01", "intent_id": "v6l-entry-b27-0", "price": 342.53, "qty": 3, "side": "BUY", "symbol": "AAPL"}
- 11:16:41Z INTENT_STATUS {"intent_id": "v6l-entry-b27-0", "note": null, "status": "FILLED"}
- 11:16:41Z PROTECT_PLANNED {"intent_id": "v6l-protect-163f331d4c894f33a1a8", "limit": null, "position_id": "pos-99ec110ffc6246e8b7c4", "trigger": 340.52}
- 11:16:41Z INTENT_CREATED {"intent_id": "v6l-protect-163f331d4c894f33a1a8", "limit": null, "purpose": "PROTECT", "qty": 3, "side": "SELL", "symbol": "AAPL"}
- 11:16:41Z INTENT_STATUS {"intent_id": "v6l-protect-163f331d4c894f33a1a8", "note": null, "status": "SENT"}
- 11:16:41Z PROTECT_STATE {"account": "DU******001", "broker_order_id": 1002, "client_id": 131, "con_id": 10000, "confirmed_at": "2026-10-15T11:16:41+00:00", "confirmed_remaining": 3, "pen
- 11:18:21Z TP_EVIDENCE {"P": 3, "S": 3, "T": 0, "X": 0, "ask": 342.56, "ask_raw": "342.56", "aux_price": null, "bid": 342.5, "bid_raw": "342.5", "calculated_price": null, "configured_market_data_type": 1, "error_code": 0, "gap_a_started_at": null, "gap_b_started_at": null, "intent_id": "v6l-protect-163f331d4c894f33a1a8", "marker": "B27-scale-status-100", "market_data_type": null, "mkt_cap_price": null, "overnight_tp_scope": null, "phase": "status", "position_id": "pos-99ec110ffc6246e8b7c4", "proof_at": null, "proof_started_at": null, "quantity": null, "quote_age_seconds": 0.0, "quote_time": "2026-10-15T11:18:21.580271+00:00", "reason": "", "receive_time": "2026-10-15T11:18:21.580271+00:00", "sent_price": null, "source": "synthetic-volume-status", "stage": null, "symbol": "AAPL", "three_source_complete": false, "workflow_id": null, "working_price": null}
- 14:05:59Z TP_EVIDENCE {"P": 3, "S": 3, "T": 0, "X": 0, "ask": 342.56, "ask_raw": "342.56", "aux_price": null, "bid": 342.5, "bid_raw": "342.5", "calculated_price": null, "configured_market_data_type": 1, "error_code": 0, "gap_a_started_at": null, "gap_b_started_at": null, "intent_id": "v6l-protect-163f331d4c894f33a1a8", "marker": "B27-scale-status-10100", "market_data_type": null, "mkt_cap_price": null, "overnight_tp_scope": null, "phase": "status", "position_id": "pos-99ec110ffc6246e8b7c4", "proof_at": null, "proof_started_at": null, "quantity": null, "quote_age_seconds": 0.0, "quote_time": "2026-10-15T14:05:59.607350+00:00", "reason": "", "receive_time": "2026-10-15T14:05:59.607350+00:00", "sent_price": null, "source": "synthetic-volume-status", "stage": null, "symbol": "AAPL", "three_source_complete": false, "workflow_id": null, "working_price": null}
- 16:53:37Z TP_EVIDENCE {"P": 3, "S": 3, "T": 0, "X": 0, "ask": 342.56, "ask_raw": "342.56", "aux_price": null, "bid": 342.5, "bid_raw": "342.5", "calculated_price": null, "configured_market_data_type": 1, "error_code": 0, "gap_a_started_at": null, "gap_b_started_at": null, "intent_id": "v6l-protect-163f331d4c894f33a1a8", "marker": "B27-scale-status-20100", "market_data_type": null, "mkt_cap_price": null, "overnight_tp_scope": null, "phase": "status", "position_id": "pos-99ec110ffc6246e8b7c4", "proof_at": null, "proof_started_at": null, "quantity": null, "quote_age_seconds": 0.0, "quote_time": "2026-10-15T16:53:37.634429+00:00", "reason": "", "receive_time": "2026-10-15T16:53:37.634429+00:00", "sent_price": null, "source": "synthetic-volume-status", "stage": null, "symbol": "AAPL", "three_source_complete": false, "workflow_id": null, "working_price": null}
- 19:41:15Z TP_EVIDENCE {"P": 3, "S": 3, "T": 0, "X": 0, "ask": 342.56, "ask_raw": "342.56", "aux_price": null, "bid": 342.5, "bid_raw": "342.5", "calculated_price": null, "configured_market_data_type": 1, "error_code": 0, "gap_a_started_at": null, "gap_b_started_at": null, "intent_id": "v6l-protect-163f331d4c894f33a1a8", "marker": "B27-scale-status-30100", "market_data_type": null, "mkt_cap_price": null, "overnight_tp_scope": null, "phase": "status", "position_id": "pos-99ec110ffc6246e8b7c4", "proof_at": null, "proof_started_at": null, "quantity": null, "quote_age_seconds": 0.0, "quote_time": "2026-10-15T19:41:15.661509+00:00", "reason": "", "receive_time": "2026-10-15T19:41:15.661509+00:00", "sent_price": null, "source": "synthetic-volume-status", "stage": null, "symbol": "AAPL", "three_source_complete": false, "workflow_id": null, "working_price": null}
- 22:28:53Z TP_EVIDENCE {"P": 3, "S": 3, "T": 0, "X": 0, "ask": 342.56, "ask_raw": "342.56", "aux_price": null, "bid": 342.5, "bid_raw": "342.5", "calculated_price": null, "configured_market_data_type": 1, "error_code": 0, "gap_a_started_at": null, "gap_b_started_at": null, "intent_id": "v6l-protect-163f331d4c894f33a1a8", "marker": "B27-scale-status-40100", "market_data_type": null, "mkt_cap_price": null, "overnight_tp_scope": null, "phase": "status", "position_id": "pos-99ec110ffc6246e8b7c4", "proof_at": null, "proof_started_at": null, "quantity": null, "quote_age_seconds": 0.0, "quote_time": "2026-10-15T22:28:53.688588+00:00", "reason": "", "receive_time": "2026-10-15T22:28:53.688588+00:00", "sent_price": null, "source": "synthetic-volume-status", "stage": null, "symbol": "AAPL", "three_source_complete": false, "workflow_id": null, "working_price": null}
- 00:16:42Z SHUTDOWN {"broker_working": [], "unresolved": []}
- 17:43:55Z SCAN {"marker": "B27-scale-SCAN", "no_trades": true}

## Operator notes

- (market context, manual actions, things to review)
