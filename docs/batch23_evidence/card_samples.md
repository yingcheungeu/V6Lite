# 第23批实际合成场景卡片

全部来自FakeBroker、本地native回调double和实际Alerter/Channel。临时合成状态位于dev；不是John账户日志。

## 保护单被券商取消：合成3股

场景完成后的测试真值（不替代卡片渲染时取得的券商证据）：{"P": 3, "S": 0, "T": 0, "X": 0, "not_covered_by_STP": 3}

```text
🔴 P0 · 券商已取消保护单
发生了什么：AAPL 持仓数量=3 股 保护单已确认=待核实（上次确认 3） 股 保护单可能=3 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实 股 可能超卖=待核实 股；券商已回报取消；完整终态身份已核验；orderRef=v6l-protect-4086a92c8f2e40eaa3f6 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；券商回报状态=Cancelled；禁止未经核实重挂；券商状态=Cancelled code=202；终态诊断与执行权限独立核验
券商原因原文：Order canceled by system: synthetic broker cancellation
影响：有效 STP 保护 S=待核实（上次确认 3） 股，未保护 P−S=待核实 股；止盈和外部卖单仅列卖出容量，不能当作止损保护。新开仓继续冻结，Lite 不自动重挂。
你要做什么：1）先按 Ctrl+C 停止 Lite；2）在同一 TWS 核对持仓和所有工作卖单，再按当前 bid 用限价单平仓或人工建立保护，避免重复卖单；3）重新核对持仓和工作单。不要点击 TWS 的重新提交/IBALGO 建议；不要自动改价重挂保护单。只有确认 0 股、无在途卖单、成交静默期和原 release 强门槛均满足后才执行 release AAPL；身份或证据不足保持拒绝，不要手改状态库、新开仓或自动重挂保护单。
```

```text
🔴 P0 · 券商已取消保护单
发生了什么：AAPL 持仓数量=3 股 保护单已确认=0 股 保护单可能=0 股 止盈在途=0 股 外部卖单在途=0 股 未保护=3 股 可能超卖=0 股；券商已回报取消；完整终态身份已核验；orderRef=v6l-protect-4086a92c8f2e40eaa3f6 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；完整工作单快照未发现原 GTC；禁止重复提交；券商状态=Cancelled code=202；终态诊断与执行权限独立核验
券商原因原文：Order canceled by system: synthetic broker cancellation
影响：有效 STP 保护 S=0 股，未保护 P−S=3 股；止盈和外部卖单仅列卖出容量，不能当作止损保护。新开仓继续冻结，Lite 不自动重挂。
你要做什么：1）先按 Ctrl+C 停止 Lite；2）在同一 TWS 核对持仓和所有工作卖单，再按当前 bid 用限价单平仓或人工建立保护，避免重复卖单；3）重新核对持仓和工作单。不要点击 TWS 的重新提交/IBALGO 建议；不要自动改价重挂保护单。只有确认 0 股、无在途卖单、成交静默期和原 release 强门槛均满足后才执行 release AAPL；身份或证据不足保持拒绝，不要手改状态库、新开仓或自动重挂保护单。
```

## TP价格被拒：合成6股，完整原因多段

场景完成后的测试真值（不替代卡片渲染时取得的券商证据）：{"P": 6, "S": 4, "T": 0, "X": 0, "not_covered_by_STP": 2}

```text
🔴 P0 · 止盈价格被券商拒绝 · AAPL
发生了什么：AAPL TP数量=2 股；发送限价=119.41；bid=119.64 ask=119.7 行情年龄=0.0s；orderRef=v6l-c5602ab2dc74457f9a37 orderId=1003 permId=51003 broker code=201；自动止盈暂停 position=pos-cd3c4f093d71497fb9b5 stage=TP1 pause_id=tp-pause-6b9e67704989434b9b8d；保护恢复仍需完整三项证据。
券商原因原文 1/3：Order rejected - reason: Your order price is outside the allowed price range. Synthetic IBKR price-band diagnostic; not an account or a live order. 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整
影响：本持仓自动止盈已暂停；券商保护与可用卖出数量仍须核验。
你要做什么：1）核对 TWS 持仓和工作卖单；2）确认 STP 已恢复到全部持仓；3）仅在原强核验门槛满足后由 John 执行 resume-tp AAPL --position-id pos-cd3c4f093d71497fb9b5 --pause-id tp-pause-6b9e67704989434b9b8d --operator John --reason 明确核验原因。没有合法 STP 完整证据时保持暂停。不要自动改价重发或点击 IBALGO。
```

```text
🔴 P0 · 止盈价格被券商拒绝 · AAPL · 原因 2/3
发生了什么：AAPL TP数量=2 股；发送限价=119.41；bid=119.64 ask=119.7 行情年龄=0.0s；orderRef=v6l-c5602ab2dc74457f9a37 orderId=1003 permId=51003 broker code=201；自动止盈暂停 position=pos-cd3c4f093d71497fb9b5 stage=TP1 pause_id=tp-pause-6b9e67704989434b9b8d；保护恢复仍需完整三项证据。
券商原因原文 2/3：原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price const
```

```text
🔴 P0 · 止盈价格被券商拒绝 · AAPL · 原因 3/3
发生了什么：AAPL TP数量=2 股；发送限价=119.41；bid=119.64 ask=119.7 行情年龄=0.0s；orderRef=v6l-c5602ab2dc74457f9a37 orderId=1003 permId=51003 broker code=201；自动止盈暂停 position=pos-cd3c4f093d71497fb9b5 stage=TP1 pause_id=tp-pause-6b9e67704989434b9b8d；保护恢复仍需完整三项证据。
券商原因原文 3/3：raint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; END-B23-ORIGINAL-PRICE-REASON
```

## 自动止盈暂停：合成8股，恢复全仓STP后健康重启仍不重发

场景完成后的测试真值（不替代卡片渲染时取得的券商证据）：{"P": 8, "S": 8, "T": 0, "X": 0, "not_covered_by_STP": 0}

该P0渲染时的测试真值：{"P": 8, "S": 6, "T": 0, "X": 0, "not_covered_by_STP": 2}
恢复全仓STP及成功重启后没有另发同事件低级别暂停卡。

```text
🔴 P0 · 止盈价格被券商拒绝 · AAPL
发生了什么：AAPL TP数量=2 股；发送限价=119.41；bid=119.64 ask=119.7 行情年龄=0.0s；orderRef=v6l-c7eb1b6cc5104e5fa3f7 orderId=1003 permId=51003 broker code=201；自动止盈暂停 position=pos-2288866e43ac4e229789 stage=TP1 pause_id=tp-pause-e6cb21164eb34c3ca14c；保护恢复仍需完整三项证据。
券商原因原文 1/1：Order rejected - reason: Synthetic price outside the allowed price range.
影响：本持仓自动止盈已暂停；券商保护与可用卖出数量仍须核验。
你要做什么：1）核对 TWS 持仓和工作卖单；2）确认 STP 已恢复到全部持仓；3）仅在原强核验门槛满足后由 John 执行 resume-tp AAPL --position-id pos-2288866e43ac4e229789 --pause-id tp-pause-e6cb21164eb34c3ca14c --operator John --reason 明确核验原因。没有合法 STP 完整证据时保持暂停。不要自动改价重发或点击 IBALGO。
```

## TP工作价格不一致：合成4股

场景完成后的测试真值（不替代卡片渲染时取得的券商证据）：{"P": 4, "S": 3, "T": 1, "X": 0, "not_covered_by_STP": 1}

```text
🟠 P1 · 券商工作价格不一致 · AAPL
发生了什么：orderRef=v6l-031ce2e0101e4c2e838e 发送价=119.41 工作价=119.66 cap=119.50999999999999
影响：该标的自动重发已阻止；原账本价格保留，STP 恢复继续独立核验。
你要做什么：1）核对 TWS 原订单价格；2）由 John 处置，禁止自动跟随 cap 改价。
```

## 第二次报价检查过期：合成5股，TP未发出并恢复保护

场景完成后的测试真值（不替代卡片渲染时取得的券商证据）：{"P": 5, "S": 4, "T": 0, "X": 0, "not_covered_by_STP": 1}

```text
🟠 P1 · 止盈单未发出，正在恢复保护
发生了什么：AAPL 持仓数量=5 股 保护单已确认=4 股 保护单可能=4 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 1–5） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-c6a93f1907784fd29d93 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:01:02+00:00 工作单证据时间=2026-10-06T14:01:02+00:00；保护减量确认后 bid/ask 无效或报价超过止盈新鲜度；未发出止盈，恢复保护
影响：止盈单确定没有发出，Lite 正在按三项证据恢复保护；原 B 空窗持续计时，累计 30 秒仍未恢复则升级 P0。
你要做什么：1）目前等待 Lite 用新鲜三项证据自动恢复保护；2）不要另下卖单或手动增加保护股数；3）从 B 空窗起点累计 30 秒仍未恢复，按 P0 卡片核对 TP 和保护单。
```
