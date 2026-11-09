# V6-Lite 第三十一批修复报告

日期：2026-10-27（PT）。{{STATUS}}

基线：v6lite-batch30 / 86cbb435db0ab501d5bcb5cc242e2e1fac881555，基线code_sha 72393b62e7f08db6。
指令SHA-256：dffca92d2370d9d608bd555fca3de77ad03fa553945cbfc2a38949c0237a56cc。
候选code_sha：{{CODE_SHA}}；完整package SHA-256：{{PACKAGE_SHA}}。

## 调查结论与证据边界

已证明一个过夜专属的确认竞态。b30 `protection_confirmations.py:216–223` 在处理已发出BE的live openOrder时，再调用 `_overnight_history()`。当日持仓不经过这层检查。
过夜的TP1 execDetails若只有当天回执时间，`overnight_evidence.py:130–132` 的 `_overnight_today_receipt()` 必须取当前完整scope，`_overnight_current_scope():86–108` 又依赖 `_protection_notice_evidence`。
正常 `_protection_evidence_fresh()` 在 `protection_engine.py:601` 先清空旧proof，再依次查询工作单、持仓、成交和显式UTC当日可见成交。live openOrder恰在该轮未完成时到达，即使强身份、pending价格和数量都正确，也会因缺scope抛异常；b30回调把异常吞掉，继续PRICE_PENDING。新回放真实经过10/26入场、SQLite关闭/重开、10/27恢复、overnight_tp_restored、TP1减量/成交和BE，正常查询自己清空proof；没有直接写proof或授予权限。该反例在原b30归档上失败，在候选通过。

精确107ms正向回放另在真实SDK修改发送后交付openOrder，无orderStatus；generation 2在回调内确认。必须在服务器发送边界安排回调，旧辅助函数返回前会经过额外1秒查询等待；校准前的1.107秒断言失败原样保留。完整三项查询来源单独覆盖，查询中的openOrder行必须等自己End和整轮完成，不能冒充live回调。

不能把上述已证机制伪称已唯一复原现场所有97秒和6轮拒绝。指令提供的是脱敏摘要，b30 `tp_evidence.py:413–420` 原价格观察没有原生status、total_quantity、filled/remaining及完整强身份；仅aux相等不能证明每轮所有门槛均成立。未读取禁止的运行目录/状态库，也未连接券商。13:35:02那条openOrder已通过共同强身份/数量/工作状态/请求绑定和当时可用历史检查，但哪一项相对13:33:25首次变好，现有摘要不能唯一确定。候选补齐原生状态、数量、orderId/permId/conId/clientId、account_matches、来源、socket代数、pending代数/价格/数量和confirmed价格，不输出账户号。另加PROTECT_PRICE_ACK_DEFERRED，明确记录pending形态、请求代数/重启、请求后时钟、工作合同/数量、pending价格及持久请求绑定/历史前缀拒绝检查；ORDER_PRICE_OBSERVATION.reason仍是券商原文，不混称为本地ACK拒绝原因。

b30 progress为什么重复计失败：`protection_engine.py:1170–1181` 把过夜权限拒绝后通用validator/数量失败转为冻结与P0；`:1222–1231` 在full_exec为False且没有overnight_price_confirmation时累计verification failure；`:1269–1284` 又把快照确认置于overnight_denied门后，形成确认事实受TP授权牵制。通用validator `:728–742` 和价格观察 `tp_evidence.py:428–433` 原本已接受confirmed/pending双值，不能说所有核验只比较旧339.62。每轮是否同一原生状态/数量失败，必须有完整回调才能断定。

28c测试缺口并不是直接塞过夜状态：`test_batch28b_overnight_refresh.py:98–127` 实际完成prior-day入场、shutdown、Store reopen和恢复标记。`test_batch28c_price_confirmation.py:64–75` 的离线place stub每次重建完整Trade/OrderStatus，且正向ACK都在完整proof仍可用的安静点；未覆盖正常新查询清空proof后的live ACK。候选使用安装SDK真实IB.placeOrder复用Trade，只替换离线transport，并新增该查询交错。安装SDK ib.py/wrapper.py/client.py/decoder.py哈希记录在freeze.json。

## 修改前后

| 区域 | b30行为 | batch31行为 |
|---|---|---|
| live保本ACK | 过夜额外依赖当前scope/history可用 | 与当日共用强身份/精确pending/工作状态检查；过夜持久请求完整绑定和原历史前缀不变，ACK本身不需要新的当日查询 |
| 自发完整快照 | 先过夜TP授权，再确认pending | 完成当前自发整轮且binding未变后先记ACK，丢弃旧proof；后续TP再取新完整a–d |
| 旧aux、同数量 | 重启可能误冻，原生等待缺少30秒P1 | a–d仍完整核验；归类修改待生效，不冻、不P0；30秒后一次P1，保留旧价STP继续查询 |
| 真实矛盾 | 原冻结/P1/P0 | aux341.00、数量3、permId不同等保留原规则，STP不撤改 |
| 误冻结解除 | generic frozen长期阻断 | 仅明确price_confirmation_pending且完整订单/代数绑定、无real_failure，ACK后新完整a–d通过才自动清除；审计OVERNIGHT_TP_FREEZE_CLEARED和INFO |
| 关闭卡 | 平仓fill内立即发估算 | 只延迟通知，最多3秒等本次closing execId佣金；风险估算/TRADE_CLOSED/冷却立即执行 |
| 并发与重启 | 后到佣金赶不上卡 | 原生佣金立即本地入账，独立事件循环截止回调，无sleep或新线程；deadline持久化，重启不延长；按positionId去重 |

**初次冻结时尚未满足的验收项（原指令§3.4）：** 正向自动解除测试通过直接调用明确原因来建立新provenance；生产中没有产生该精确原因的现存调用点，因此这仅证明明确原因的解除契约，未证明真实b30冻结升级迁移。未拿到可区分原因的历史证据或用户对保守规则的裁定前，不将整批标为验收完成、不提交/tag。

裁定状态：{{SCOPE_RESOLUTION}}

**历史冻结的保守边界：** b30 `frozen= pending BE broker evidence mismatch` 同时用于缺单、真实身份/数量矛盾和重启价格不符；旧OVERNIGHT_TP_FROZEN只有通用reason，P0 signature也未保存当时own完整tuple。没有当时明确原因的旧记录，无法证明“唯一原因是待确认”，因此保持冻结。不能仅凭后来健康就把历史真实失败解冻；未手动改状态库。已知单一待确认原因的回放会自动解除；真实失败、后来追加真实失败、错误generation、缺原因的旧记录均不解除。这个边界需Claude复核，不能把合成明确原因记录说成现场b30本已具备该证据。

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

最终聚焦：{{FOCUSED}}
源码检查：{{SOURCE_CHECKS}}

## Windows连续三次串行全量

| 回执 | 结果 | 耗时 | PT起止 |
|---|---|---|---|
{{FULL_RUNS}}

交易日06:00–13:30 PT未跑全量或全量收集，期间只运行明确文件的聚焦测试。每次全量开始前检查真实PT时钟、冻结inventory、验证工具；三次必须同一完整XML case身份、同一源码、rc/failure/error=0，日志/XML SHA吻合。失败不覆盖，若修复则重冻结并重启连续三次；第三次同一候选flake不豁免。

## 新关闭卡实际样例

```text
{{CARD}}
```

AAPL 3@343.47，TP1 1@344.71，保本STP 2@343.56，毛1.42、佣金3.02、最终−1.60；最后佣金在9ms后通过真实wrapper.commissionReport进入引擎。原XML property和样例来源hash见cards_sample.json。

## 全部验证回执

| 标签 | 实际结果 | source_unchanged |
|---|---|---|
{{HISTORY}}

## 交付与SHA-256

freeze.json固定完整源码、SDK、工具和指令hash；最终SHA256SUMS.json列出任务源码、报告和脱敏证据。只允许精确task allowlist暂存，排除baseline_b30目录、历史临时证据、运行数据和既有操作手册变更。dev本地提交并打annotated v6lite-batch31后验证tag归档inventory/package/code_sha、报告和manifest blob；交付后冻结等待Claude复核。

没有push、merge、部署、连接TWS/Gateway、发真实订单、触碰用户.v6lite/运行仓库/John配置；操作手册未编辑或暂存。
