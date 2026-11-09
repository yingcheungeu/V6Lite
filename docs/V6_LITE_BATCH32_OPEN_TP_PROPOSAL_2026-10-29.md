# Batch32 开盘止盈调查与方案（用户已同意，第三十三批实施）

日期：2026-10-29 PT。只读证据为 `docs/batch32_claude_evidence/`，manifest SHA-256 9e56247d3a27aee3544065669e08257b1be623c9ceea916d0bb94fbb68412b7c，三文件逐项一致。以下时间均为 UTC。代码位置为 b31；本批不修改开盘 TP 启动或定价规则。

## 首笔 TP1 的可确认事实

| 导出行／ID | 时间 | 已记录事实 |
|---|---|---|
| 559／133411 | 13:30:00.828649 | 开始 TP1，目标1股 |
| 562／133414 | 13:30:01.842707 | 请求STP 3→2；bid/ask 348.06/348.36，quote_time 01.710015，age .132692s |
| 569–571／133421–423 | 13:30:02.060658 | 减量确认，P=3、S=2，B空窗开始 |
| 574–576／133426–428 | 13:30:02.073658–.074655 | SELL1 LMT346.85；bid/ask347.54/347.82，quote_time01.973117，age .101538s；配置和实际行情类型均为1 |
| 579–594／133431–446 | 13:30:02.177842–.207584 | 3398/252495231，先PendingSubmit，随后PreSubmitted；filled0/remaining1 |
| 596、602、608、614、617 | 13:30:03.879–10.945 | 持续记录PreSubmitted、未成交；bid从347.94到349.70，last observed ask349.84 |
| 619–621／133471–473 | 13:30:12.172737 | 保护TP超时触发主动撤单，note=protection_tp_timeout |
| 635–644／133487–496 | 13:30:13.662–.668 | Cancelled及CANCEL_SEEN；随后202“委托单被取消”，发生在主动撤单之后，不能当原始拒单原因 |
| 649–653／133501–505 | 13:30:18.028590–.129538 | 发restore_request，随后原STP已回报3股、PreSubmitted |
| 660–663／133512–515 | 13:30:22.078395 | 完整确认恢复3股，B审计20.017737s |

定价符合现有公式：347.54×(1−.002)=346.84492，向上取分346.85（exits.py:104；protection_engine.py:1851）。不能归因为卖出限价高于已记录bid。`quote_time` 是本机接收字段的时间，不是交易所原始生成时间；size回调也可能刷新该接收时间。此导出没有盘口size、Paper撮合器采用的盘口、完整逐笔、held/election原因或内部路由日志。

未记录到该单Submitted或FILL，不等于证明内部从未短暂改变状态。第二次TP1在40秒后以348.78提交，40.660476成交1股@349.46，B=.241238s；一次随后快速成交不能证明“等待30秒必定成交”。

## B 的拆解与限度

| 区间 | 秒 |
|---|---:|
| 减量确认→发起撤单 | 10.112079 |
| 发起撤单→CANCEL_SEEN | 1.491300 |
| CANCEL_SEEN→restore_request | 4.364553 |
| restore_request→完整确认 | 4.049805 |
| 合计 | 20.017737 |

10秒门槛来自 `gap_b_cancel_seconds`，不是45秒的普通exit fill timeout。20.017737s是系统审计的恢复确认空窗；18.128538/.129538已见券商3股回应，故不能断言券商实际整整20秒少1股保护，也不能把整段归为撤单延迟。

## 官方机制与不能确认的根因

IBKR说明Paper为模拟器，按最优档模拟成交、不访问深度簿，部分Auction等订单类型不受支持。这不足以由本机bid与限价推导本单必然成交的时刻。[IBKR Paper Trading](https://www.ibkrguides.com/advisorportal/aboutpapertradingaccount.htm)

Nasdaq Opening Cross在09:30 ET开始，合并开盘簿与连续簿形成开盘价，随后向consolidated tape发布。[Nasdaq Opening/Closing Cross FAQ](https://www.nasdaqtrader.com/content/productsservices/trading/crosses/openclose_faqs.pdf)

导出没有AAPL当天官方opening print/Cross实际完成时间，当前提交是普通DAY LMT、outsideRth=False。不能声称3398参与了开盘集合竞价；“开盘阶段Paper模拟或订单激活差异有关”仅是假设。本批结论是：限价低于已记录bid、持续记录PreSubmitted、最终按本地10秒规则撤销；内部未撮合原因仍未确定。

## 用户已裁定的方案（移交第三十三批）

拟新增 `tp_start_after_open_seconds=30`，非负整数；0明确关闭。依据当日 `session.open_utc + N`，使用既有日历处理夏令时、节假日等，不硬编码UTC13:30。

1. 仅在新TP1/TP2/TP3开始前检查；未到点不STAGE_BEGIN、不占workflow、不减STP数量、不耗重试次数。
2. 到点重新按当前触发、fresh quote、强身份、a–d、S+T+X≤P决定；触发已消失时不补做旧触发。
3. 主门放在退出决策后、创建新阶段之前，仅适用于TP_PURPOSES；新保护TP workflow入口再防绕过。
4. 不放在共同退出入口；STOP、GTC回调、穿越告警、保护核验、已有TP撤单/恢复、断线恢复均照常。
5. 不改变sell_limit公式、向上取分、报价新鲜度和物理发线deadline。到点bid改变可改变最终数值，定价规则不变。
6. 暂不采用“首笔成交之后”：当前系统没有已定义的合格首笔/Opening Cross完成证据源、交易日绑定和缺失处理。

拟验收：29.999/30.000秒边界、N=0/60、无效配置；等待期S=P无减量；09:30:05止损照常；到点stale quote或触发消失不启动；已有cancel/restore不延期。用户已于10/29同意上述边界；本批不实施或测试这个新门控，放到第三十三批。
