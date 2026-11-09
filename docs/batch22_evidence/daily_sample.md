# V6-Lite daily report 2026-10-06 (paper)

- account: DU0000000   state role: formal
- code_sha: d42320d23de79d6e
- market data: real-time (1) | n/a RTH availability (no runtime RTH samples supplied)
- last health market data (instantaneous, not RTH availability): real-time (1)

- realized P&L: -15.25   fees: 3.00   net: -18.25
- entries submitted: 1 / 4
- daily stop latched: False
- open positions: AAPL 17@113.47 stop 108.47
- 券商端保护: AAPL 截至 2026-10-06T14:00:31+00:00 最近一次券商快照确认，当前仍需核实，本地状态=WORKING P=17 S=17 类型=STP 止损价（触发后市价成交）=108.47 orderId=1002；空窗统计 A=0.000s B=0.000s；当日止损成交：AAPL execId=0000fa4e.00000002.01.01 数量=1 止损价=108.47 成交价=107.22 成交均价=107.22 滑点=1.25 USD / 115.24 bps；AAPL execId=0000fa4e.00000003.01.01 数量=2 止损价=108.47 成交价=108.97 成交均价=108.39 滑点=-1.00 USD / -46.10 bps
- kill switch: off
- open anomalies: none
- frozen symbols (operator must flatten, then `release`): none
- released positions (P&L of the operator's TWS trades is NOT in the figures above): none

## Fills
- 14:00:30Z AAPL BUY 20 @ 113.47 realized 0.00
- 14:00:31Z AAPL SELL 1 @ 107.22 realized -6.25
- 14:00:31Z AAPL SELL 2 @ 108.97 realized -9.00

## Events
- 13:50:00Z LOSS_STATE {"cooldown_until": null, "streak": 0}
- 14:00:30Z SCAN {"errors": {}, "min_score": 60, "signals": [["AAPL", 78]], "vix": 18.0}

## Operator notes

- (market context, manual actions, things to review)
