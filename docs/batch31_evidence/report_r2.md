# V6-Lite 第三十一批修复报告

日期：2026-10-27（PT）。候选源码冻结；等待13:30 PT后三次串行Windows全量，尚未提交/tag。用户已明确接受来源不明旧冻结保留的保守规则。

基线：v6lite-batch30 / 86cbb435db0ab501d5bcb5cc242e2e1fac881555，基线code_sha 72393b62e7f08db6。
指令SHA-256：dffca92d2370d9d608bd555fca3de77ad03fa553945cbfc2a38949c0237a56cc。
候选code_sha：13c2c871fb98222c；完整package SHA-256：13c2c871fb98222c0a16db3d858e710a3f6f881a9620413965d193898e5ff526。

## 调查结论与证据边界

采用用户提供的只读 `docs/batch31_claude_evidence/`。其 SHA256SUMS.txt 的 SHA-256 为 80c4d9646e2708b11b3f1c135bfbdad3a5f5beaeda22b62e02c95861d5633e00，6 个文件逐项匹配，校验回执为 claude_evidence_verification.json。该目录未修改、未复制进交付 allowlist。下列代码行号均指原始 v6lite-batch30；候选行号不能代替基线行号。

### 10/27 与 10/26 的实际区别

| 原始审计位置 | 事实及门控意义 |
|---|---|
| audit_20261027_tp1_be_window.jsonl:65，#132138 | 13:33:22.764541 已由 reqExecutions 将 TP1 的时间验证为显式 US/Eastern，换算 UTC 13:33:21；在保本改价之前，不是只有临时回执时间。 |
| 同文件:71，#132144 | 13:33:25.780674，generation 2，PRICE_PENDING，pending_qty=2，pending_trigger=343.64，status_seen_generation=-1。 |
| 同文件:72–73，#132145–146 | 13:33:25.887999 原生 source=openOrder / cap_raw=""，107.325ms 后到达；随后 .889999 是 source=openOrders / cap_raw="0.0"，没有中间的 SDK merged source=openOrder，也没有 BE orderStatus。 |
| audit_20261026_intraday_be_confirm.jsonl:62–65，#128946–949 | 14:40:04.612206 发改价；.707292 原生 openOrder 在 95.086ms 后同回调确认 WORKING，source=live openOrder；其后才出现 SDK merged source=openOrder。 |
| audit_20261027_tp1_be_window.jsonl:99–101，#132172–174 | 13:35:02.629181 原生 openOrder 同回调确认，source=live openOrder；随后还有 merged openOrder。 |

**首条价格回报的具体拒绝门：** b30 `protection_confirmations.py:193–196` 直接跳过 `event.query_snapshot`；`broker/ibkr.py:1606` 在自己的 openOrders 请求未 End 时将原生回调标为 query snapshot，`:1620–1634` 保存本轮原生行并发出该诊断。SDK wrapper.openOrder 在 openOrders future 活跃时把结果加入查询，而不发 openOrderEvent；End 后 adapter `open_orders():750–809` 才产生 source=openOrders 的诊断，而它也不符合 live openOrder 门。上表 raw→openOrders 的相邻来源和 cap_raw 组合，在安装的 b30 SDK 正常路径中定位到这一分支；10/26 和 13:35:02 则是 raw→确认→merged openOrder 的查询外路径。

这里明确区分证据与推断：审计没有保存 query_snapshot 布尔字段，上述来源判定由 b30 代码与 SDK 行为推导；修正回放实际调用同一 native request、wrapper.openOrder 和 openOrderEnd，直接捕获 query_snapshot=True，原生行在 End 前不得确认，完整 End 后才使用自己完整 openOrders 确认。旧回放把 .887 当成查询外 live ACK，没有匹配真实审计，已纠正。禁止把未完成的查询行提前当 live 来满足测试断言。

### 13:33:27.805–.831 的冻结与 P0

`log_20261027_excerpt.txt:21`（原日志行98）的原文“GTC STP 保护工作单强身份核验失败”唯一对应 b30 `overnight_evidence.py:512–513`：`len(own)>1 or any(not _protection_order_valid(row,o) for o in own)`。它先于后来“既有冻结”拒绝，是本轮首个实际失败的复合门。`:22`（原日志行99）的 P0 对应 b30 `protection_engine.py:1173–1182` PRICE_PENDING 的工作单不一致分支；工作证据时间为 .890999。之后 `:1222–1231` 在 full_exec=False 且没有稳定过夜/price scope 时累积 progress verification failure，退避1/2/4/8/16/30秒。已写 symbol frozen 又会在 a–d 处持续阻断后续 TP 授权。`:1269–1284` 的完整轮确认原本放在 overnight_denied 门之后，因此 pending 确认事实和 TP 权限相互牵制。

**不能将这一复合门进一步伪写成“旧 aux 不等于 pending”。** b30 `_protection_order_valid():728–742` 原本允许 confirmed 或 pending 两个 aux；`tp_evidence.py:403–411` 只有强身份 tuple 一致才会记录这些价格观察，且未见工作合同价格不一致把状态写 UNKNOWN。因此“只比较旧339.62”与证据不符。仍未记录、可能使该复合门失败的是原生工作状态、数量/filled/remaining 或重复 own 行。压缩观察按 intent 的上一 payload 去重，不保存行数；不能由两条观察排除重复行。`log` 中 modify Trade 的 PreSubmitted/2/0/2 是发送前缓存，不是 .887 原生回应，不能拿来补造缺失字段。该导出 README 也明确 DEBUG 原生字节未记录。现有资料可以定位上述具体复合判定和首次回调的来源门，不能唯一命名复合判定内部哪一个未记录的子项为假，也不能证明其后6轮每次都因同一子项失败。

13:35:02 成功是查询外的原生 live ACK，已经通过所有共同强身份/数量/工作状态以及当时的过夜请求检查；不能据此倒推此前未记录的每个子项。候选新增 PROTECT_PRICE_ACK_DEFERRED，逐项记录实际拒绝检查；新冻结审计保存原因和工作单 status/total/filled/remaining/身份，以后不再只留下宽泛错误。

### 修正回放与旧测试缺口

`test_batch31_evidence_replay.py` 从实际前日入场、shutdown、SQLite Store 关闭/重开、10/27 过夜恢复开始，真实产生 overnight_tp_restored，再经 TP1 减量/成交/BE。TP1 查询在改价前已验证时间；恢复的是 native generic Order + Stock(right='?')，使用真实 IB.placeOrder 复用恢复 Trade，仅离线 transport 替身。107.325ms 原生查询行与2ms后自己的 End/诊断匹配审计来源和 cap_raw，无 BE orderStatus；完整 End 后候选确认，再重取 a–d，TP2、止损归零及 S+T+X≤P 持续断言。真实盘的实际TP2档位345.71，测试以345.72行情触发；早期通用回放保留指令的345.72档位。身份使用稳定脱敏别名，完整身份检查仍执行。

原始日志没有记录原生 state/filled/remaining/重复行数，回放将其显式设为正常工作单的单行2/0/2；这是正向契约前提，不是新发现的现场字段，也不伪称复现了历史复合门的内部子项。该最终修正 spec 在未改生产代码的 b30 归档上先红：`red_b30_evidence_query_End_final`，完整自己 End 后仍 PRICE_PENDING；候选 `green_evidence_query_End_final` 24通过。

28b/28c 原测试并没有直接塞过夜恢复标记：`test_batch28b_overnight_refresh.py:98–127` 真实 prior-day 入场、shutdown、Store reopen。实际缺口是 `test_batch28c_price_confirmation.py:64–75` 离线 place stub 重建完整 Trade/OrderStatus，正向 openOrder 在查询外安静点到达、或等待完整三项轮完成，未断言“查询内原生行不确认，但自己完整 openOrders End 即确认”。新增回放复用真实 SDK 的 restored generic Trade，并检查这条来源差异。

先前另发现“临时回执时间 + 正常新查询清空 proof 时，过夜 live ACK 依赖未完成 scope”的独立竞态，`red_b30_gap_calibrated` 在 b30 失败，候选保留修复和覆盖。**撤回将它作为这次10/27实际事件原因的先前判断**：#132138 已在 BE 前验证时间，证据不支持这种现场根因。

## 修改前后

| 区域 | b30行为 | batch31行为 |
|---|---|---|
| live保本ACK | 过夜额外依赖当前scope/history可用 | 与当日共用强身份/精确pending/工作状态检查；过夜持久请求完整绑定和原历史前缀不变，ACK本身不需要新的当日查询 |
| 自发完整快照 | 没有独立 openOrders End ACK；完整三项确认又依赖过夜TP授权 | 当前自己 openOrders End 完整、同socket/当前request、发改价后启动、唯一精确工作单才记ACK；重启仍要求完整三项，后续TP再取新a–d |
| 旧aux、同数量 | 重启可能误冻，原生等待缺少30秒P1 | a–d仍完整核验；归类修改待生效，不冻、不P0；30秒后一次P1，保留旧价STP继续查询 |
| 真实矛盾 | 原冻结/P1/P0 | aux341.00、数量3、permId不同等保留原规则，STP不撤改 |
| 误冻结解除 | generic frozen长期阻断 | 仅明确price_confirmation_pending且完整订单/代数绑定、无real_failure，ACK后新完整a–d通过才自动清除；审计OVERNIGHT_TP_FREEZE_CLEARED和INFO |
| 关闭卡 | 平仓fill内立即发估算 | 只延迟通知，最多3秒等本次closing execId佣金；风险估算/TRADE_CLOSED/冷却立即执行 |
| 并发与重启 | 后到佣金赶不上卡 | 原生佣金立即本地入账，独立事件循环截止回调，无sleep或新线程；deadline持久化，重启不延长；按positionId去重 |

裁定状态：用户明确裁定：§3.4 裁定：接受保守规则。 新冻结必须写明原因。只有原因为"修改待确认"的冻结，并且在保本修改已确认、新一轮完整三项核验 a–d 通过之后，才自动解除，同时写审计并发 INFO。b30 留下的、来源不明的旧冻结保持不变，报告里写明这一点。；来源=direct human follow-up in this conversation

**按用户明确裁定执行的旧冻结边界：** b30 generic frozen 和旧 OVERNIGHT_TP_FROZEN 未保存可区分的原因。来源不明旧冻结保持原状，不自动解除、不手改运行状态库。新冻结必须记录具体 reason、reason_codes 和 failure_reasons；仅原因准确为“修改待确认”、请求完整绑定一致且无真实失败，确认后新一轮 a–d 通过才解除并写审计和 INFO。合成明确原因的正向仅证明这条新契约，不称为旧b30原因迁移；用户已接受保守规则。后来真实失败、generation变化、旧 marker 后追加“修改待确认”、跨日旧 symbol frozen 均不能重分类或解冻。

只读复核发现并修复同socket旧轮延迟事件和旧冻结后补原因两处边界：先分别2项/1项红测，再加严 sent==ended==event request、通道未poisoned以及 legacy unclassified。扩展负向包含旧socket/同步/部分/缺request/请求先于改价/过期/重复/非工作状态/数量/permId/aux，不允许借新事件绕过原强身份和数量守卫。

CLOSE_CARD_SENT表示已调用既有Alerter发送/排队接口，不是远端送达确认。持久sent标记防止正常重启重复发卡；发送/排队与sent落库之间崩溃仍有重复窗口，不承诺跨崩溃严格一次送达。

## PRICE_PENDING旧confirmed trigger使用点清单

- 通用工作单检查protection_engine `_protection_order_valid`、tp_evidence `_on_price_observation`：已支持双值，候选保留。
- overnight a–d：原始TP flow的original_identity绑定trigger，`_overnight_check_flow`/`_overnight_restore_previous_total`用于原工作流/同价恢复，不是纯BE ACK；候选不放宽这些绑定。
- progress：是否需要BE、纯价格pending精确确认、紧急同价减量和请求去重；候选将ACK移到TP权限前，旧价等待仍核验a–d。
- coverage：ProtectionIncidentMixin `_protection_resolve_if_covered`使用通用validator且要求WORKING；pending不提供新的TP/释放权限。
- 启动/接管：engine `_recover_orders`里的保护匹配使用通用validator，允许双值；重启只能在完整当前自发整轮后确认。
- 止损穿越：protection_engine `_protection_on_mark`用confirmed trigger触发诊断；成交滑点store保留confirmed/pending候选，不把未确认改价当已确认。
- 日报report、lifecycle退出卡显示confirmed trigger和本地状态，属于展示；final_tp原合同/撤单绑定正常应在PRICE_PENDING结束后消费。

S+T+X≤P、强身份、报价新鲜度、a–d、运行约束A/B均保留。确认只记券商已响应的事实，不能解除hold、UNKNOWN、人工暂停或来源不明冻结，也不能授予历史全量成交权限。

## 红绿验证与失败记录

b30生产代码从指定tag归档，baseline30_source.json记录归档hash和全部原文件hash，production_unchanged=True；只覆盖测试helper和新增测试，不覆盖生产代码。
`red_b30_calibrated` 4 failed/7 passed：40秒未生效P1和关闭卡等待为产品红测；简化安静点正向恢复/ACK原本通过，不能隐瞒。
`red_b30_gap_calibrated` 1 failed：正常查询空窗内live ACK仍为PRICE_PENDING；断言放在查询外，不把测试assert伪装成券商故障。
`red_b30_real_query_gap`保留第一次多场景红测，其中查询内部assert被包装为query error，仅确认失败有意义，那些由assert派生的P0不作为产品误报证据。
新增关闭卡9ms、3秒超时、无关exec不得提前释放、重启deadline、未完成查询中的native佣金/截止发送，以及等待时另一个持仓仍能TP和查询均覆盖。只读复查另发现即时佣金事件会触发原订单证据失效器。red_native_fee_interleave两个真实wrapper.commissionReport交错回放先失败（fingerprint不变但token消失），随后修复CommissionEvent只绕过两个订单/数量失效器；不放宽fingerprint，保留ACK→实际TP wire≤1秒及容量断言。原样证据spec保存在commission_interleave_spec.py，最终测试文件同字节。
早期夹具NameError、audit列名误用time（实际ts）、并发TP测试误用30秒默认周期/不允许的1秒启动预算、回调1.107秒和二次修改重复注入等确定错误均修正，原回执保留。focused_final_1的16个负向回调失败同为新增诊断误用只接受固定kind的压缩审计，改用普通审计按内容去重后重跑，不扩充共享压缩kind表；不是时序flake，也未通过重试豁免。旧b28c“重启旧价立即冻结”断言按本批明确新规则改成继续等待/P1，不改数量/身份/STP写入断言；两处旧关闭卡测试仅调整3秒等待时序。

最终聚焦：focused_final_3：187 passed / 0 skipped，133.031秒，源码不变。
源码检查：204文件inventory；195个Python源码compile/UTF8 AST/无BOM；预期完整测试2264例（b30的2222 + 本批42）。

## Windows连续三次串行全量

| 回执 | 结果 | 耗时 | PT起止 |
|---|---|---|---|
| pending | 尚未执行 | - | - |

交易日06:00–13:30 PT未跑全量或全量收集，期间只运行明确文件的聚焦测试。每次全量开始前检查真实PT时钟、冻结inventory、验证工具；三次必须同一完整XML case身份、同一源码、rc/failure/error=0，日志/XML SHA吻合。失败不覆盖，若修复则重冻结并重启连续三次；第三次同一候选flake不豁免。

## 新关闭卡实际样例

```text
#x1F7E2 持仓已关闭
标的与持仓：AAPL；positionId=pos-959a272a5b634750a7e5；剩余持仓 0 股
盈亏：AAPL 毛盈亏 1.42 USD；佣金 3.02 USD；最终净值 -1.60 USD（佣金已全部回报）
说明：平仓佣金最多等待 3 秒；缺佣金按近期同标的同方向同数量实际佣金估算，配置最低值兜底；实际回报后复算，已触发冷却不自动解除。
```

AAPL 3@343.47，TP1 1@344.71，保本STP 2@343.56，毛1.42、佣金3.02、最终−1.60；最后佣金在9ms后通过真实wrapper.commissionReport进入引擎。原XML property和样例来源hash见cards_sample.json。

## 全部验证回执

| 标签 | 实际结果 | source_unchanged |
|---|---|---|
| focused_final_1 | rc=1, passed=154, fail=16, error=0, skip=0 | True |
| focused_final_2 | rc=0, passed=170, fail=0, error=0, skip=0 | True |
| focused_final_3 | rc=0, passed=187, fail=0, error=0, skip=0 | True |
| focused_regression_1 | rc=0, passed=413, fail=0, error=0, skip=0 | True |
| green_ack_diagnostics | rc=0, passed=43, fail=0, error=0, skip=0 | True |
| green_core_calibrated | rc=1, passed=27, fail=1, error=0, skip=0 | True |
| green_core_complete | rc=1, passed=28, fail=1, error=0, skip=0 | True |
| green_core_edges | rc=1, passed=27, fail=1, error=0, skip=0 | True |
| green_evidence_query_End_1 | rc=1, passed=33, fail=4, error=0, skip=0 | True |
| green_evidence_query_End_2 | rc=1, passed=44, fail=1, error=0, skip=0 | True |
| green_evidence_query_End_final | rc=0, passed=24, fail=0, error=0, skip=0 | True |
| green_initial | rc=0, passed=17, fail=0, error=0, skip=0 | True |
| green_parallel_TP_and_core | rc=0, passed=29, fail=0, error=0, skip=0 | True |
| green_pending_and_raw | rc=1, passed=70, fail=1, error=0, skip=0 | True |
| green_precise_107ms | rc=1, passed=31, fail=1, error=0, skip=0 | True |
| green_safety_edges | rc=1, passed=30, fail=2, error=0, skip=0 | True |
| green_snapshot_guards_expanded | rc=0, passed=32, fail=0, error=0, skip=0 | True |
| green_snapshot_round_and_legacy | rc=0, passed=20, fail=0, error=0, skip=0 | True |
| pending_freeze_edges_before_sql_repair | rc=1, passed=11, fail=1, error=0, skip=0 | True |
| red_b30_calibrated | rc=1, passed=7, fail=4, error=0, skip=0 | True |
| red_b30_evidence_query_End_1 | rc=1, passed=0, fail=0, error=1, skip=0 | True |
| red_b30_evidence_query_End_2 | rc=1, passed=0, fail=1, error=0, skip=0 | True |
| red_b30_evidence_query_End_final | rc=1, passed=0, fail=1, error=0, skip=0 | True |
| red_b30_gap_calibrated | rc=1, passed=0, fail=1, error=0, skip=0 | True |
| red_b30_initial | rc=1, passed=0, fail=3, error=8, skip=0 | True |
| red_b30_real_query_gap | rc=1, passed=2, fail=6, error=0, skip=0 | True |
| red_legacy_freeze_later_cause | rc=1, passed=0, fail=1, error=0, skip=0 | True |
| red_native_fee_interleave | rc=1, passed=0, fail=2, error=0, skip=0 | True |
| red_snapshot_newer_round | rc=1, passed=0, fail=0, error=2, skip=0 | True |
| red_snapshot_newer_round_calibrated | rc=1, passed=0, fail=2, error=0, skip=0 | True |
| red_snapshot_poisoned_End | rc=1, passed=0, fail=1, error=0, skip=0 | True |

## 交付与SHA-256

freeze.json固定完整源码、SDK、工具和指令hash；最终SHA256SUMS.json列出任务源码、报告和脱敏证据。只允许精确task allowlist暂存，排除baseline_b30目录、只读batch31_claude_evidence目录、历史临时证据、运行数据和既有操作手册变更。原始输入哈希另行核验，引用不代表加入allowlist。dev本地提交并打annotated v6lite-batch31后验证tag归档inventory/package/code_sha、报告和manifest blob；交付后冻结等待Claude复核。

没有push、merge、部署、连接TWS/Gateway、发真实订单、触碰用户.v6lite/运行仓库/John配置；操作手册未编辑或暂存。
