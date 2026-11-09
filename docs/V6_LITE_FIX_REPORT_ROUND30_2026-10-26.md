# V6-Lite 第三十批修复报告

日期：2026-10-26（PT）。三次 Windows 全量通过；准备本地 dev 提交及 annotated v6lite-batch30，交付后冻结等待复核。
基线：v6lite-batch29 / 8200445318afb3a56c76e2569c1cef851963e557；基线 code_sha 1bd6b193aedae3bc。
指令 SHA-256：97f1e552228934a2a3b851fcfef2cb64ce6af93f585b1781d5236629b8913493。
候选 code_sha：72393b62e7f08db6。
完整 package SHA-256：72393b62e7f08db6b1c0dbae6a68e97004d24f0dcfe8d1612376c8973fdebd56。

## 佣金调查：确定结论与现场证据限度

指定 exec 0000dc8f.6bef670d.01.01 的现场根因未能唯一判定。已获得的是指令中的脱敏日志摘要，未获得 10/26 原始 API 回调 DEBUG/完整日志/运行 DB，也没有访问被禁止的运行目录或连接券商。
SDK 的 commissionReport INFO 行会被现有过滤器降为 DEBUG；日志没有这类行不能证明 TWS 没推送。FILL_NOT_BOOKED commission_changed=False 只说明那一次 record_fill 重放没有新增/修改费用，既可能是 None，也可能与账本相同，不是 native 回报是否到达的证据。
源码确认 Store 能保留先于本地 fill 的佣金；连接前已接事件，重连未发现漏订阅。安装的 ib_insync 0.9.86 在没有 Trade 时，只更新 canonical Fill 的费用而不发 commissionReportEvent；若 report 先于任何 canonical Fill 则被 SDK 忽略。原生 decoder/reqId/Future/execDetailsEnd 回放已验证两种顺序。已有 executions_today 能从 canonical cache 取回费用，但原引擎只有未核实成交时间的周期查询，缺少平仓佣金主动闭环。
具体源码哈希、观察与推断边界见 investigation.json；安装 SDK 四个源文件哈希也已冻结。不能把覆盖这两种机制的修复伪称已证实 TWS 侧故障。

## 最终改动与规则边界

1. 只对已入账、已关闭当日持仓的缺佣金，主动走既有 paced executions_today 通道。精确核验 execId、orderRef、symbol、side、quantity、price、orderId、permId，补查只 record_commission；不重记成交、数量、时间，也不把查询注册为 TP/过夜授权证据。未知/不匹配行不采用。
2. 每交易日持久记录尝试时间，失败/重启也消耗同一 300 秒槽；时钟倒退保守跳过。截止实际 session.close（正常日 13:00 PT，半日遵循日历），不是测试约束的 13:30 PT。保护优先、非稳态、空窗、在途非保护订单或排队退出时不补查；_protect 前后重核。原有必须的启动/对账/时间核验请求不被这条新维护节流替代。
3. 缺佣金账务估算不再使用 sizing 的每笔至少 $2 预留。新增 risk.commission_estimate_min_usd，默认 $1.00，必须有限且>0；同标的、同方向、同数量，近 30 天最多 20 笔实际佣金取中位数并以该最低值兜底。关闭时逐 exec 固定金额、来源和样本 ID；新历史/新配置不会追溯改写旧估算。真实费用 0 或负返佣按现有 adapter 语义入账，估算不会为 0。
4. 平仓卡同时显示已回报净值、估算费用和估算净值；冷却卡列出每笔持仓 ID、净值和是否含估算。亏损阈值与冷却时长不变。已触发截止时间持久保留，负→正费用翻号只 INFO/审计、不自动解除；真实费用由盈转亏仍保留既有 R7 的保守亏损判定，未削弱门控。两条旧 L4 自动解除断言按第 30 批明确裁定更新。
5. 最终费用到账后账本/日报用实际值。同号不多发卡，翻号 INFO 一次。已写出的当日本地日报/附件在正常报告阶段静默刷新，不从回调写文件、不重发远程日报/平仓/冷却卡；成功后的新费用可立即收口，只有文件失败重试受一分钟节流。已知 OPEN/未绑定成交的当天费用也更新顶部统计，原异常保留。
6. TEST_TP_TIMEOUT_RESULT 只记录生命周期变化，正常原生流程最多 8 行；等待 status 不重复，迟到成交按 execId 区分，P0 在原处置之后加观察性审计，不改变发单/撤单/保护/止盈规则。B29 价格、最后一档禁注入、B10/B30 规则保持。
7. 日报分列 B（正常）和 B（测试注入），≤5 秒只评价正常 B。豁免必须匹配历史持久 claim、实际 sent intent、原保护/工作流/数量/测试价格；弱标记/坏 claim/不完整证据仍算正常。跨日继续按完整窗口归属，不把六秒拆成两个合规三秒。四处旧显示匹配更新了标签，数值断言保留。
8. 本机完整日报原已存在独立 Test TP timeout 段；远程十记录卡原未显示该段。没有实际截图/文件，不能断言 10/26 截图来自哪一种输出；现远程 Orders 提示测试次数及本机完整生命周期。相对 bid 原值为空的确定原因是初始未核实回调的不可变 fill audit 没随完整查询更新；报告现在连接当前已核实账本和原始发送 bid 计算，保留原审计。缺控制项可恢复唯一精确原 send；否则具体说明缺快照、无效 bid 或未核实时间。

未改 sizing、资金预留、daily-stop day_pnl、下单价格/数量/费用门、STP/TP状态机、冷却阈值/时长、时间解析器。未连接 TWS/Gateway、不发真实订单、不改用户 .v6lite、运行仓库、John 配置或操作手册。

## 红绿、复核与时序记录

原始 b29 的 192 文件 inventory 与 tag 归档逐字节相同后，red_commission_1 的 3 例、red_report_audit_2 的 6 例全部按所需缺陷先失败；原始外部测试源码、JSON/XML/log 保留。首个 red_report_audit_1 因复制工具 OUT 路径未改而未进入测试（0 case），记录已保留并仅搬回任务新产物，不计产品红测。
后续所有失败都保留：原 send 控制缺失时 workflow 为空的 fallback 真问题、NULL position 日报和旧估算随配置漂移，都加强后先红再修；native 佣金夹具先只 drain 关闭，保护优先尚未完成，维护正确 defer，后来改用真实 Engine.tick，不改旗标/证据/门控。一次文本补丁前置匹配不唯一使修改中断，shell 仍启动了测试，失败如实保留，此后依赖步骤独立检查退出码。
focused_regression_1 为332通过/2旧B标签匹配失败，完整窗口数值断言保留修正。focused_final_1 为334通过/1旧b29 late-fill计时失败：Windows 上两个独立 RTT timer 过期后同轮执行，late_fill_at==cancel_at。首见即修夹具：在实际 late-fill 交付之后再安排剩余半 RTT 的 Cancelled，保留严格 cancel_wire<late_fill_at<cancel_at 和所有经济/SDK断言，不靠重试隐藏。详见 b29_late_timer_flake_repair.json。
red_published_report_1 和 red_refresh_edges_1 又证明已发布本机日报、60秒内新费用/停机、OPEN/未绑定费用的收口缺口后修复。具体每次结果以本目录回执为准；不将确定夹具失败或显示预期更新伪称产品回归，不放宽第三次同一时序flake必须修复或明确重定义门槛的规则。
最终聚焦 focused_final_2：339 passed / 0 skipped，262.516 秒，源码不变。
按 10/26 数据的三股离线 Engine 调度回放：毛3.84、已佣3.01、缺一笔估算1.00、已回报净值0.83/估算净值-0.17；补回1.00后最终-0.17且无额外卡；补回0.01后最终+0.82，INFO一次、原冷却保留。TP1 bid345.84/成交345.87 得+0.0300，TP2 bid346.81/成交346.81 得+0.0000。受控原生回放还验证普通B 2.063/2.051秒与注入14.256秒分列。

回放与卡片样例的资料边界：这些是离线受控夹具，不是原始现场账本或真实发送的卡片。第一笔已知为亏损，但样例的 −2.00 USD 和持仓 ID 是测试输入；入场均价 344.52 是由三笔已给卖出价及毛盈亏 3.84 反推；已回报 3.01 的逐笔分配 1.01/1.00/1.00 也是受控选择，现场逐笔费用并未提供。所有 broker/order/position 身份均为模拟。运行时估算使用实际已入账同类历史，不使用这些夹具常数。完整出处见 investigation.json 的 offline_fixture_provenance。

## Windows 连续三次串行全量

| 回执 | 结果 | 耗时 | PT起止 |
|---|---|---|---|
| final_full_1 | 2221 passed / 1 skipped | 2150.984s | 2026-10-26T15:58:02.600965-07:00 → 2026-10-26T16:33:53.591194-07:00 |
| final_full_2 | 2221 passed / 1 skipped | 2145.015s | 2026-10-26T16:34:38.122116-07:00 → 2026-10-26T17:10:23.155090-07:00 |
| final_full_3 | 2221 passed / 1 skipped | 2144.766s | 2026-10-26T17:10:58.774036-07:00 → 2026-10-26T17:46:43.547675-07:00 |

冻结 199 文件；190 个 Python compile/UTF8 AST/无BOM通过。collect-only 明确收集 2222 例（原2167 + 新55），三次完整XML已核对同一数量和case身份、rc/failure/error=0、同一inventory、source_unchanged和日志/XML哈希；2222个case身份均唯一。SDK源文件、验证工具的冻结哈希也全部相同。
全量只在交易日13:30PT以后或周末执行，实际起止/串行顺序见回执。三次均只有同一既有skip：test_scoring_matches_legacy_scanner_on_identical_bars（legacy market_scanner.py not present）；每次47条 record_property/xunit2 格式警告，完整明细保留于日志/XML。

## 实际新卡片样例

关闭卡：
```text
#x1F7E2 持仓已关闭
标的与持仓：AAPL；positionId=pos-b6b3c783159e4c0cb492；剩余持仓 0 股
盈亏：AAPL 毛盈亏 3.84 USD；佣金已回报 3.01 + 估算 1.00 USD；已回报净值 0.83 USD；估算净值 -0.17 USD（1 笔佣金未回报）
说明：缺佣金按近期同标的同方向同数量实际佣金估算，配置最低值兜底；实际回报后复算，已触发冷却不自动解除。
```
冷却卡：
```text
#x1F7E1 亏损后冷却
发生了什么：2 consecutive losing trades; entries paused until 2026-10-26T16:52:06+00:00
positionId=b30-prior-opening-stop-loss；净值 -2.00 USD；佣金已回报/已核定
positionId=pos-b6b3c783159e4c0cb492；净值 -0.17 USD；含估算佣金 1.00 USD
判定口径：估算净值 < 0 计为亏损；缺佣金按近期同标的同方向同数量实际佣金估算，配置最低值兜底。
你要做什么：核对各持仓最终佣金；佣金补齐不会自动解除已触发的冷却。
```
翻号 INFO：
```text
#x1F7E2 佣金补齐，净值符号改变
发生了什么：AAPL positionId=pos-07b543b0b1a74e058550；佣金补齐后净值符号改变，原估算净值 -0.17，最终净值 0.82 USD；已触发冷却不自动解除，原截止 2026-10-26T16:52:06+00:00。真实费用形成新亏损时原风控继续适用。
你要做什么：核对最终账本；是否解除既有冷却由操作员复核，系统不自动解除。
```
原始 XML property、关闭估算来源见 cards_sample.json。

## 全部验证回执

| 标签 | 实际结果 | 完整性/源码 |
|---|---|---|
| final_full_1 | rc=0 / passed=2221 / fail=0 / error=0 / skip=1 | incomplete=False / source_unchanged=True |
| final_full_2 | rc=0 / passed=2221 / fail=0 / error=0 / skip=1 | incomplete=False / source_unchanged=True |
| final_full_3 | rc=0 / passed=2221 / fail=0 / error=0 / skip=1 | incomplete=False / source_unchanged=True |
| focused_final_1 | rc=1 / passed=334 / fail=1 / error=0 / skip=0 | incomplete=False / source_unchanged=True |
| focused_final_2 | rc=0 / passed=339 / fail=0 / error=0 / skip=0 | incomplete=False / source_unchanged=True |
| focused_regression_1 | rc=1 / passed=332 / fail=2 / error=0 / skip=0 | incomplete=False / source_unchanged=True |
| green_commission_1 | rc=0 / passed=3 / fail=0 / error=0 / skip=0 | incomplete=False / source_unchanged=True |
| green_native_commission_4 | rc=0 / passed=4 / fail=0 / error=0 / skip=0 | incomplete=False / source_unchanged=True |
| green_report_audit_1 | rc=1 / passed=6 / fail=1 / error=0 / skip=0 | incomplete=False / source_unchanged=True |
| green_report_native_2 | rc=1 / passed=12 / fail=2 / error=0 / skip=0 | incomplete=False / source_unchanged=True |
| green_review_native_3 | rc=1 / passed=14 / fail=2 / error=0 / skip=0 | incomplete=False / source_unchanged=True |
| red_commission_1 | rc=1 / passed=0 / fail=3 / error=0 / skip=0 | incomplete=False / source_unchanged=True |
| red_published_report_1 | rc=1 / passed=0 / fail=1 / error=0 / skip=0 | incomplete=False / source_unchanged=True |
| red_refresh_edges_1 | rc=1 / passed=0 / fail=3 / error=0 / skip=0 | incomplete=False / source_unchanged=True |
| red_report_audit_1 | rc=1 / passed=0 / fail=0 / error=0 / skip=0 | incomplete=True / source_unchanged=True |
| red_report_audit_2 | rc=1 / passed=0 / fail=6 / error=0 / skip=0 | incomplete=False / source_unchanged=True |
| red_review_edges_1 | rc=1 / passed=0 / fail=2 / error=0 / skip=0 | incomplete=False / source_unchanged=True |

## 交付

SHA256SUMS.json 列出任务源码、报告和脱敏证据。仅精确 allowlist 暂存，本地dev提交并打annotated v6lite-batch30；校验tag归档全部inventory、完整package/code_sha、报告及manifest blob哈希。交付后冻结等待复核；John本机合并，未merge/push/部署。既有独立操作手册改动不属于本批，不暂存。
