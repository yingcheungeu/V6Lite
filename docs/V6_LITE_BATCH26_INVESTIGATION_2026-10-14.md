# 第二十六批第1节调查结论（先于生产修改）

日期：2026-10-14 PT。仅在 C:\Dev\V6Lite-dev/dev 只读取证；基线 v6lite-batch25 / 97fa8c6d68cfd7b700c1474dc6f8a94e158fd2d2，code_sha=ea71abe782e4cf68。
指令SHA-256：dff7d8eff2edf4e2bfb18e3a63f6a018292cf2662fdcbae7c50268b8cdbee78b。
root与独立batch26_investigate交叉调查；batch26_tests独立设计；batch26_implement仅只读给方案，此刻未修改生产。

## 1 成交可回溯多久

1. 当前[IBKR Request Execution Details](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/execution-details/request-execution-details)在reqExecutions条目只保证当日午夜以来的成交。
2. 当前[ExecutionFilter protobuf](https://www.interactivebrokers.com/docs/tws-api/protobuf/execution-filter)另有lastNDays（最多7天）及specificDates（过去一周）字段。它不等于本机SDK已支持，也不保证任意历史日期可查。
3. [旧官方说明（页面已弃用）](https://interactivebrokers.github.io/tws-api/executions_commissions.html)说默认午夜以来，TWS可按Trade Log设置扩至7天，Gateway无法调整此设置。新旧页及新协议字段不能合并成“任何TWS/Gateway都能查7天”的保证。
4. 本机ib_insync版本0.9.86；objects.py:83–90只有旧的clientId/acctCode/time/symbol/secType/exchange/side七字段。ib.py:822–835的阻塞方法转交1913–1920的异步请求；client.py:634–643原样序列化这些字段，不下载或拼补历史，也不支持上述新字段。
5. **裁定：本轮只能按当前接口保证当日范围。** 本机TWS版本、Trade Log设置、实际历史可见范围未知；本轮没有连接券商。time只是筛选下界，不能扩充服务端本来不给的历史。

## 2 当日查询的账户范围

[IBKR ClientId 0 and the Master Client ID](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/client-id-0-and-the-master-client-id)明确区分：非Master连接只接收本client的订单/成交；client0可额外接收TWS/FIX；Master设为0才涵盖TWS/FIX及所有API client。不能由账户过滤字段或正常End推断已获得这些权限。

本机代码 v6lite/broker/ibkr.py:1254–1255 对executions_today显式过滤当前client；:1277–1285的account_executions_today只传账户，SDK的clientId默认0（objects.py:84）。此方法目前用于release的只读证据，并不证明当前连接的Master设置或TWS手工成交可见性。不能把其成功空列表当成全账户无交易的证明。该调查不改变既有release核验。

**第1.2节的回答：** 历史账本、当前持仓、原GTC和“完整且范围已核实”的当日成交，可以形成当前止盈/保本操作的有条件证据。但现有接口的时间及客户端范围，不能独立证明过去所有时段都没有外部成交。当前可见范围未核实时，严格实现必须继续冻结过夜止盈；不得为了让正向测试通过而将“可见结果为空”当成全账户完整。

## 3 账本到底持久化了什么

|源码引用|事实与摘录|
|---|---|
|v6lite/store.py:106–119|fills保存exec_id/intent_id/position_id/symbol/side/quantity/price/time/trading_day/broker_order_id/perm_id；不直接保存fill的account/clientId/conId。|
|v6lite/store.py:43–63|intents保存purpose、position_id、orderId/permId、hold_reason、recovery_position_id。|
|v6lite/store.py:66–93|protections保存account/client_id/con_id、GTC身份、数量、修改代与A/B空窗。|
|v6lite/store.py:340–369|数据库绑定account/mode/role/client，拒绝错误账户/角色；这不是外部防回滚锚。|
|v6lite/store.py:754–761|已有execId重放去重并补费用，不重新逐字段比较全部经济内容。新证据不能仅复用“已去重”。|
|v6lite/store.py:764–783|同stem的新execId按成交更正隔离，不自动覆盖原持仓。|
|v6lite/protection_engine.py:598–616|当前持仓account/conId和qty_open强核验。|
|v6lite/protection_engine.py:617–635|任意历史fills导致full_exec=False；今日required子集检查不能代替历史完整性。|
|v6lite/protection_engine.py:665–679|原STP/GTC的account/client/conId/orderId/permId/价格/数量/状态强身份门槛。|
|v6lite/protection_engine.py:1017–1027|b25 quiet只保稳定覆盖，不清retry/F4、不授予加量/重建/TP/release。|
|v6lite/protection_engine.py:1430–1432|止盈目前要求full_exec，因此过夜无法启动。|
|v6lite/protection_engine.py:1289–1297|现保本价分支未检查full_exec；本批应补专用证据检查，不把它描述成原已完整阻断。|

## 4 可实施的最小授权边界

- 识别过夜不能只靠“有历史fill”：需结合position.opened_at及ENTRY时间；缺失入场fill仍必须认作缺证。
- 历史每行核验非空execId、正且有限的数量/价格、时区明确的成交时间/日期、与intent的symbol/side/purpose和orderId/permId相符；ENTRY恢复持仓用持久化recovery_position_id。校验总BUY、SELL净额、qty_initial/qty_open及入场成本；缺行、孤儿、隔离更正、未核实时间、旧hold/frozen/异常一律不授予新权限。
- 同代主动、完整结束的positions/openOrders/executions；账本qty_open=券商P；原GTC强身份一致且S=P；没有冲突外部卖单、未知成交；账户可见范围及所需时间段必须明确。
- 今天返回的每条成交要逐字段匹配其真实账本/自有意图，不能仅比较execId集合；未知、identity或经济字段冲突立即冻结。
- 新权限只用于本次TP/保本；不要把通用full_exec置True，不扩大加量、通用保护重建、release或解除旧异常的权限。
- TP流程自身减量后恢复须绑定原持仓/原GTC/起始P/本次TP意图，重新核对预期自身成交，仅恢复本流程刚减掉的覆盖；不得变成通用重建权。最后一档撤GTC后未成交的恢复也必须明确属于此流程。
- 每次动作前重新核验，不能沿用上一tick/上一连接的成功证明；全过程S+T+X≤P及原强身份断言保持不变。
- 首次实际启用INFO写清证据，持久化去重；不满足则P1说明具体失败项，不能虚称已完整核验。

## 5 风险与能挡住的范围

|情形|可检测/处置|仍不能宣称证明的部分|
|---|---|---|
|停机后手工卖出1股、数量不一致|新鲜P与账本/原GTC不符；冻结，不自动修数量。|不能用旧GTC还在就声称股数没变。|
|其他client/TWS下单|可见的外部工作卖单/未知成交冻结；完整可见性未核实则继续冻结。|非Master范围的空集不能排除外部成交。|
|入场execId缺失、部分库被替换|入场/意图/数量/价格/身份关联核验失败，冻结。|不能只以SELECT返回零行的空集子集为真放行。|
|成交更正或时间异常|保留quarantine/时间审查/旧异常，不自动清理。|未被当前窗口返回的历史更正仍不可见。|
|停机跨越多个交易日/午夜|没有覆盖中间时段的可信证据则冻结。|当日下载结束不能证明中间几天没有交易。|
|整库自洽回滚且当前净股数/原GTC恰好相同|需要外部可信锚、券商statement或人工核验。|同库fills/audit/哈希一起回滚无法被同库自检可靠识别。|
|昨日先卖后买回相同数量|只有覆盖该时段的外部完整成交证据才能查清。|当前净持仓和今天零成交不足以排除。|

因此，原指令中的“历史账本+当日三项”必须明确以可信账本、已核实的券商可见范围及时间覆盖为前提。若接受标的独占/账本未回滚作为运行约束，应由Claude/John明示其风险边界，不能由实现暗中把它称为技术证明；本调查没有接受或解除这些风险。

## 6 独立测试设计与接下来的顺序

独立batch26_tests建议新增overnight_evidence及shutdown_report两份spec。过夜夹具必须真实昨日ENTRY→GTC→正常SHUTDOWN→SQLite close/reopen，今日Fake查询显式只回今日；Fake默认全历史不能造成假绿。正向覆盖ACK前不发TP、10秒撤TP确认后恢复、TP1成交后的原GTC保本；五类负向及跨scope/更正/历史缺失都保持强断言。b25接管及停机STOP两回放原文件不改。

顺带的停机补日报与上述范围裁定无依赖：应在真实日历收盘后（含半日市）、常规close+5分钟前允许补一次，重用持久化去重；收盘前/非交易日不补；dry-run早return也需覆盖。保留撤DAY、保留GTC、SHUTDOWN审计、disconnect的原相对顺序，报表失败不阻断安全停机。

本文件先交付；生产代码尚未改。跨客户端完整性与历史连续性是待Claude/John裁定的实施前提。范围未证明时不得放行；可以先独立推进shutdown测试及离线验收工具，不修改运行仓库或John配置。
