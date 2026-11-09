# V6-Lite 第20批修复报告 — 2026-10-08

最终 code_sha：`cfe16edd7a7e4d7f`。Windows 默认全量（V6-Lite + scripts）连续三次 **1055 passed / 0 failed / 0 errors / 0 skipped**。新增129个case，最终统一B19基线 **61 failed / 68 passed / 0 errors**；最终全量全部通过。

## 1. 范围与冻结边界

本批仅 `C:\Dev\V6Lite-dev` 的 dev 工作区；基线 HEAD `abbdb4f2f7a558262a0859d5a6bf7ee859e46d2f`、code_sha `6293dd4e2377603c`。root是唯一生产代码实施者，三位独立Agent分别拥有时间/日志测试、保护事件测试、等价合同/退休GTC测试，最终全量串行。写前及冻结检查v6lite进程均为0。

未连接TWS/Gateway或联网，未stage/commit/push/合并；未修改运行仓库、John配置、已有日报或状态库。指定10/8日志只读；SQLite明确 `mode=ro` + `PRAGMA query_only=ON`，数据库及现有WAL/SHM读取前后字节哈希相同，见 `incident_state_readonly.json`。全量时间全部在10/8 13:30 PT之后。

dev已有 `docs/V6_LITE_OPERATOR_MANUAL.md` 的未提交更新不属于本批，字节保持SHA-256 `5d7eb6f043f4d933019f36b6cbfd28b28f8474c0b7097261f9bb77ae627133bf`，不纳入本批清单或提交范围。交付后本批源码和测试冻结，等待Claude复核。

## 2. 发现 → 改动 → 测试

| 项目 | 发现与证据 | 改动与边界 | 测试 |
|---|---|---|---|
| G3-N正式标的 | B19样例只有AAPL/SPY/TSLA，与正式Paper的10个标的不一致 | 仅样例universe/sectors对齐正式样例；合成账户、7497、client131、test关闭、保护默认、10:00/13:30 ET、独立paper-g3n状态目录保留 | template五项：B19 4红/1绿，最终全绿 |
| 27项保护等价 | 原本地STOP/重试合同不能直接当开启保护的GTC合同；原测试名与强断言保留，三份旧夹具按下文调整 | 第7节逐名映射；新增实际GTC成交/TP三证据/减量/撤单/损益/风险/释放覆盖，不靠强制关闭保护 | 27项全绿；其中24项在B19已绿，属于补覆盖，另外3项发现真实边界问题 |
| 6 成交时间 | 同exec回调21:58:09Z，query14:58:09Z；首先回调入账，后续同exec只补费用，日报直接读fills | 解码前保存实例级raw时间来源；明确时区→UTC；无明确TWS时区的naive值及时以接收时间暂记并持久标记；完整query、同identity/同交易日才能核准新标记行。闭仓同事务重算MAX(fills.time)，不重做经济成交/保护A计时 | 时间组27项：B19 20红/7绿，最终全绿；强查询322部分结果、跨日、旧账、重启、P&L不重记 |
| 7 日志 | wrapper逐条成交/佣金INFO共47,277行、15,412,798字节；全天119,350行、25,883,218字节 | 只将wrapper execDetails/commissionReport降DEBUG，INFO下不输出；V6的47,490行QUERY及其它Warning/INFO保留。A空窗查询频率不以降噪为由减慢 | handler/root-level/idempotence测试；保留QUERY/Warning、DEBUG可见 |
| 8 重复P0 | 07:58至14:33约80张同类P0，动态证据时间使旧body dedupe失效 | 持久事件签名忽略市价、elapsed、confirmed_at/working_at/updated_at；覆盖类UNKNOWN/拒单/A逾期归并。成功确认P0后相同事件30分钟内不再发，之后最多一次P1“仍未解决”；材料变化/确证恢复发新卡 | 成功/失败/在途/重启、早期gap P1抑制、价格/时间变化、仓位/外单变化、恢复后再次发生 |
| 9 缺permId与release | protection perm为NULL，而intent已有1099402929；现main在人工平仓后仍拒release | 完整native account/client/conId/orderRef/orderId/permId/STK/USD/type/tif/price/整股total量核验后同事务同步两表ID；状态回调不是WORKING证明。native冲突不能降格为裸ID | 60项保护事件测试内含错完整身份/小数/总量、初始化perm0等待、bare exact旧合同、实际manual flat+release；release方法AST与B19相同 |
| 10 明确拒单 | 202在提交后立即回报明确“无法接纳止损限价单”，且非Lite发起取消 | 有完整强身份且明确拒绝原因时REJECTED，可能S为0，停止重复A缺口卡，仍冻结入场；卡片明确拒单，首选TWS限价平仓再release。裸202/裸Cancelled/身份错仍UNKNOWN | 中文/英文/转义原因、code202先于及晚于终态、错误上下文/缺身份、真撤单非拒单、已拒后重启/计时、正文不冒用未知 |
| 额外 GTC迟到竞态 | 已取消旧GTC被替换后再成交，原Store抛身份异常回滚，替代S未撤，可能超卖 | 替换前持久完整退休身份；只有exact orderId/permId/conId可入账旧fill；旧fill不扣新S上界，再cached减量/撤单，闭仓仍做取消核验。相同pending total/price等待原代次确认，不重复修改。退休GTC卡片不冒充止盈 | B19原竞态+7退休边界红；独立等待代次自然红[2,3]→保持1；full/partial、重复、重启、错身份全部绿 |

## 3. 执行来源、已有账本只读核对

日志路径：`C:\Users\yu-ch\.v6lite\paper-test\logs\v6lite_20261008.log`，SHA-256 `11c3bcd4ff0ee7b80cf7b748bc6c17babfd884ca921d59216a6048be8913e131`。

- `日志:4`：`server version 176`；本地ib_insync 0.9.86。
- `日志:222`：同exec `0000e0d5.6ac87b1f.01.01` 的Execution.time为`2026-10-08 21:58:09 UTC`。
- `日志:223`：Fill.time为包接收时间`2026-10-08 14:58:10.104497 UTC`，不是已确认执行时间。
- `日志:236/:242`：同exec的查询Execution.time为`2026-10-08 14:58:09 UTC`。
- 状态库 `fills[exec_id=0000e0d5.6ac87b1f.01.01]`：AAPL BUY3@337.74，time=`2026-10-08T21:58:09+00:00`，trading_day=2026-10-08。错时已落账；不是日报二次转换。

入账链：`v6lite/broker/ibkr.py:1087`（Execution.time）→`v6lite/engine.py:721`→`v6lite/store.py:687`。B19同exec已存在分支不改时间，因此query正确值不能修正首次错误。raw wire timeStr没有记入原日志，不能断言线上原始字符串格式或把所有naive解释为UTC；本机PT隐式时区解释可造成7小时差，但此为源码与差值支持的机制判断，不当作wire证据。

新核验：`v6lite/execution_time.py:21`、`v6lite/broker/ibkr.py:1011`、`v6lite/store.py:654`。只在本实例保留明确时区；不修改installed library、不全局设置TimezoneTWS、不统一减7小时。无明确zone时保护挂单与数量入账立即按接收时间进行，后续完整query核准原时刻。错误终止的reqExecutions列表不视为完整；账户级release查询同样严格，finally恢复RaiseRequestErrors。

已有旧行没有新 `execution_time_unverified` 标记，代码不会回写它；遇同exec不同时间仅留 `EXECUTION_TIME_REVIEW_REQUIRED` 审计。跨日/身份冲突也不自动调整。历史核对只应逐exec比较账户（不输出账户）、symbol/side/qty/price/orderId/permId/conId与同一exec的完整查询时间，再在独立核对报告列出旧time与正确time。不能批量减7小时，也不能用接收时间假装正确执行时间。本例正确14:58:09Z、差-7小时、数量/价格/交易日不变；本报告不改原fills、position、PnL或原日报。

可交给John/Claude的只读核对（不实例化Store、不连券商）：

```python
from pathlib import Path
import sqlite3
p = Path(r"C:\Users\yu-ch\.v6lite\paper-test\v6lite.sqlite3")
with sqlite3.connect(p.as_uri() + "?mode=ro", uri=True) as db:
    db.execute("PRAGMA query_only=ON")
    print(db.execute("SELECT exec_id,symbol,side,quantity,price,time,trading_day FROM fills WHERE exec_id=?",
                     ("0000e0d5.6ac87b1f.01.01",)).fetchone())
```

## 4. 10/9手动平仓与release：当前限制必须保留

状态库只读：AAPL OPEN3，entry337.74；protection orderId33/conId265598/client132，state UNKNOWN，permId NULL，intent CANCEL_SEEN/Cancelled且perm1099402929。`日志:238/:249/:250/:251`有PendingSubmit→PreSubmitted→PendingCancel→Cancelled；PreSubmitted约5ms，随后完整工作单快照已无该单，因此旧snapshot路径错过perm。`日志:252`是明确Warning202限价325.69/止损335.77偏离拒单；同段没有Lite cancelOrder。

**当前运行main不会仅因TWS0股就放行release**：`v6lite/engine.py:1609`拒绝缺protection.permId/完整身份或无确切终态。新的native补ID：`v6lite/protection_engine.py:357`；native校验失败：`v6lite/engine.py:876`。两表ID必须同时强核验；聊天中的1099402929、只读日志摘录或intent.permId本身都不是绕过release的权限。

John在10/9 06:35 PT后人工处置前，先在同一TWS确认实际仍3股且无任何在途SELL；若确认该保护已被券商拒绝，按限价单平仓，等待持仓推送/新查询均0、无在途订单及成交静默期。现旧状态的release预计仍拒绝，保留冻结，不用resolve-intent、clear-anomaly、手补SQL或删状态库绕过。需Claude另行裁定该历史保护的强身份恢复方式；若能取得完整native订单/合同/终态证据，再由经复核的引擎路径事务同步，不从裸permId反推。无法取得就保持拒绝，不能宣称已释放。

CLI `release`只排队请求，真正执行还依赖运行引擎的核验；本批没有运行该命令。新dev用例证明“完整native身份 + exact撤销 + 实际人工flat + 新鲜三证据/静默期”能release，不代表John旧历史行自动修复。本批不自动迁移历史状态，也未修改3%offset；实际券商价格带拒绝原因及后续政策仍须Claude/John裁定，不盲目重挂。

## 5. 告警事件口径与未关闭边界

`v6lite/protection_incidents.py:13`：成功确认P0才持久标记已送达；同事件状态不变时30分钟P1，恢复由新鲜三证据确认，签名保留真实仓位、订单身份/容量和外部卖单变化。不因市价/证据时间变化重复P0；已经P0的A事件不再发早期5秒P1，而首次尚未P0的5秒P1/15秒P0合同继续。

发送失败可重试；进程丢失在途回执时按未确认重新尝试，Discord端无幂等键，不能保证远端已收到但回执丢失的故障窗口绝对exactly-once。成功确认的持久事件跨restart不重复。

明确拒单必须含完整一致native tuple+终态+明确拒绝原因，且非本地取消流程；裸Cancelled/裸202不能假装已拒。202先于空白终态时，只从同一Trade.log取202非空拒绝原因，仍经过完整强身份与明确拒绝短语核验。价格、止损/止盈参数、A/B期限和release最终核验均未放宽。无真实TWS演练，线上timestamp格式/强终态回报/价格带行为仍须John/Claude日志确认。CLOSED后第二笔退休真实成交的安全路径已静态审查，未另加专用用例，不混称为实测。

## 6. 红绿、独立复核与Windows三次全量

最终统一baseline由 `git archive abbdb4f...` 导出TEMP，先确认原包code_sha=6293dd4e2377603c，再仅复制五个新测试文件；不把B20生产放进B19目录。最终依据为 `baseline_delivery.xml` 与 `baseline_delivery_metadata.json`，后者列出了最终五个新测试的字节SHA；目录中的 baseline_final/frozen 等是中间阶段证据，不用作本批最终计数。

| 测试模块 | B19红 | B19绿 | 最终新增case数 |
|---|---:|---:|---:|
| test_batch20_card_provenance | 2 | 0 | 2 |
| test_batch20_execution_time | 20 | 7 | 27 |
| test_batch20_protection_equivalents | 11 | 24 | 35 |
| test_batch20_protection_incident | 24 | 36 | 60 |
| test_batch20_template | 4 | 1 | 5 |

第一批异步告警夹具误用queue.join导致等待，已按实际worker协议修正清理后再跑，不计产品红或时序flake。等价夹具的R5采用真实GTC隐藏部分成交2；F1等待真实对账周期；SR6增加滑穿限价场景以验证保护模式即时P0（原107仍由GTC执行/P1监测），不能声称原裸STOP夹具也即时P0。late-release强断言前缀纠正为先命中的late_fill_before_flat，不删除flat证据，不放宽不记账/不重开/回放幂等。

三次早期全量尝试分别因独立发现重复pending减量、卡片归因错误、原LF被误写CRLF而主动中止；对应记录留在interrupted*，均不算完成，也未吞掉/重试掩盖产品失败。原源码LF已恢复，最终diff只含任务行为。随后第一轮完整执行暴露7项失败（`regression_windows_full_1.log/xml`：7 failed、1046 passed，code_sha d468566d1afcee3f），没有把该轮计作通过：

- 两项原L3强断言（zero_stop / sub_dollar_stop）暴露真实回归：事件签名访问最小告警row缺失state导致KeyError。修复签名缺字段的读取，原L3测试完全未改；新增minimal-row回归用例。该新用例在B19为绿，产品红证据来自上述候选全量，不伪称B19自然失败。
- Batch15紧急回调夹具补齐新的强native身份元数据；0.05秒唤醒、无嵌套查询、回调内无TP、外层tick提交TP断言均保留。
- Batch15拒单标题selector改为用户指定标题，按实际held数量计算的未保护量强断言保留（该夹具20股）。
- Batch16及f5_copy的纯文案测试在实际渲染投递边界隔离新事件去重调度，保留独特标题、影响、编号步骤和中文数量断言；拒单建议补回明确“不要”禁止操作。

以上不修改触发数量与release安全门槛。最后增加“202先于空白terminal”的用例，当前候选上自然1 failed后，修复同Trade拒绝原因关联，再冻结。943项产品测试全部通过，随后如下三次完整默认全量从头连续完成，期间没有源码/测试变化；未触发第三次flake提交豁免。

独立复核：时间/日志Agent的27项通过；保护事件Agent已复核最终code202排序修复及三份旧测试夹具；27映射+退休强边界共35项由另一Agent设计复核。独立等待generation自然红再绿；root额外两项实际卡片来源红再绿。最终新增129 + 原814 V6-Lite = 943，scripts112，总1055。

```powershell
. C:\Dev\V6Lite-dev\.venv\Scripts\Activate.ps1
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
$env:V6LITE_LEGACY_SCANNER = "C:\Dev\TradingV6\market_scanner.py"
Remove-Item Env:V6LITE_TEST_PROTECTION -ErrorAction SilentlyContinue
python -u -B -m pytest v6lite/tests scripts/tests -q -p no:cacheprovider --tb=short --junitxml <evidence XML>
```

legacy scanner仅只读离线一致性测试，没有运行旧V6引擎；未跑强制开启保护的整套旧诊断，不能称旧夹具全已变成开启保护用例。

| 轮 | PT开始 → 结束 | 结果 | code_sha前/后 |
|---|---|---|---|
| 1 | 2026-10-08T15:57:52.694127-07:00 → 2026-10-08T16:01:47.441056-07:00 | 1055 passed in 234.10s (0:03:54)，exit0 | cfe16edd7a7e4d7f / cfe16edd7a7e4d7f |
| 2 | 2026-10-08T16:01:47.532438-07:00 → 2026-10-08T16:05:42.798283-07:00 | 1055 passed in 234.01s (0:03:54)，exit0 | cfe16edd7a7e4d7f / cfe16edd7a7e4d7f |
| 3 | 2026-10-08T16:05:42.873862-07:00 → 2026-10-08T16:09:37.371414-07:00 | 1055 passed in 233.72s (0:03:53)，exit0 | cfe16edd7a7e4d7f / cfe16edd7a7e4d7f |

## 7. 剩余27项逐名对应

原测试名和强断言保留，下面每个新用例显式ProtectionConfig(enabled=True)，最终全绿。附加8个退休边界不替代27映射。

| 原测试 | 新等价测试 |
|---|---|
| `test_engine::test_breakeven_stop_after_tp1_sells_remaining` | `test_breakeven_stop_after_tp1_sells_remaining_protection_on` |
| `test_engine::test_exit_retries_then_escalates` | `test_exit_retries_then_escalates_protection_on` |
| `test_review_fixes::test_L4_trade_result_uses_net_pnl_and_estimates_missing_commissions` | `test_L4_trade_result_uses_net_pnl_and_estimates_missing_commissions_protection_on` |
| `test_review_round2::test_C2_prior_day_held_sell_never_blocks_the_stop` | `test_C2_prior_day_held_sell_never_blocks_the_stop_protection_on` |
| `test_review_round2::test_C5_exit_orders_are_stamped_with_the_real_send_time` | `test_C5_exit_orders_are_stamped_with_the_real_send_time_protection_on` |
| `test_review_round2::test_R8_commission_arriving_during_scan_can_latch` | `test_R8_commission_arriving_during_scan_can_latch_protection_on` |
| `test_review_round2::test_race_late_fill_of_cancelled_stop_cancels_the_retry` | `test_race_late_fill_of_cancelled_stop_cancels_the_retry_protection_on` |
| `test_review_round3::test_Q7_queued_exit_work_is_inside_the_protection_budget` | `test_Q7_queued_exit_work_is_inside_the_protection_budget_protection_on` |
| `test_review_round3::test_SR3_blocked_exit_cannot_livelock_the_tick` | `test_SR3_blocked_exit_cannot_livelock_the_tick_protection_on` |
| `test_review_round3::test_SR3_stop_starts_in_the_same_pass_that_abandons_a_take_profit` | `test_SR3_stop_starts_in_the_same_pass_that_abandons_a_take_profit_protection_on` |
| `test_review_round4_selfreview::test_a_stop_blocked_by_missing_evidence_pages_p0` | `test_a_stop_blocked_by_missing_evidence_pages_p0_protection_on` |
| `test_review_round4_selfreview::test_R5_difference_found_at_the_exit_boundary_blocks_entries_at_once` | `test_R5_difference_found_at_the_exit_boundary_blocks_entries_at_once_protection_on` |
| `test_review_round4::test_F1_failed_fresh_order_query_refuses_the_sell` | `test_F1_failed_fresh_order_query_refuses_the_sell_protection_on` |
| `test_review_round4::test_F7_exit_is_re_decided_with_the_latest_quote_before_sending` | `test_F7_exit_is_re_decided_with_the_latest_quote_before_sending_protection_on` |
| `test_review_round4::test_F8_back_off_is_honoured_when_the_queued_item_runs` | `test_F8_back_off_is_honoured_when_the_queued_item_runs_protection_on` |
| `test_review_round4::test_missed_buy_freezes_then_flatten_and_release` | `test_missed_buy_freezes_then_flatten_and_release_protection_on` |
| `test_review_round4::test_price_only_correction_blocks_entries_while_exits_continue` | `test_price_only_correction_blocks_entries_while_exits_continue_protection_on` |
| `test_review_round4::test_prior_day_partial_entry_is_frozen_then_released` | `test_prior_day_partial_entry_is_frozen_then_released_protection_on` |
| `test_review_round5_selfreview::test_late_buy_after_release_of_a_recovery_position_is_evidence_only` | `test_late_buy_after_release_of_a_recovery_position_is_evidence_only_protection_on` |
| `test_review_round5_selfreview::test_refused_sell_does_not_size_a_stop_from_a_wrong_ledger` | `test_refused_sell_does_not_size_a_stop_from_a_wrong_ledger_protection_on` |
| `test_review_round5::test_R4F1_release_needs_the_position_stream_to_agree` | `test_R4F1_release_needs_the_position_stream_to_agree_protection_on` |
| `test_review_round5::test_R4F4_frozen_position_closed_by_its_own_fills_can_be_released` | `test_R4F4_frozen_position_closed_by_its_own_fills_can_be_released_protection_on` |
| `test_review_round5::test_R4F8_refused_take_profit_that_crossed_the_stop_pages_p0` | `test_R4F8_refused_take_profit_that_crossed_the_stop_pages_p0_protection_on` |
| `test_review_round6::test_R5F2_old_order_without_a_position_row_never_books_after_flat` | `test_R5F2_old_order_without_a_position_row_never_books_after_flat_protection_on` |
| `test_review_round6::test_R5F3_batch4_state_release_record_alone_is_a_flat_point` | `test_R5F3_batch4_state_release_record_alone_is_a_flat_point_protection_on` |
| `test_review_round6::test_SR6_A2_stale_position_stream_never_silences_a_stop` | `test_SR6_A2_stale_position_stream_never_silences_a_stop_protection_on` |
| `test_review_round7::test_P2a_earlier_versions_evidence_rows_of_released_positions_refuse_the_state` | `test_P2a_earlier_versions_evidence_rows_of_released_positions_refuse_the_state_protection_on` |

## 8. FakeBroker实际卡片样例

前四张使用合成3股，第五张为退休GTC20→15股竞态；均只用TEMP与MemoryChannel，未发送手机通知，未使用真实账户。

### 同一保护事件首次 P0

```text
🔴 P0 · 保护单状态无法核实
发生了什么：AAPL 持仓数量=3 股 保护单已确认=3 股 保护单可能=3 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–3） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-18939d4182c048459f99 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；GTC absent from complete working snapshot; no duplicate submission
影响：保护订单身份或状态未知，未保护 待核实（可能范围 0–3） 股仍须券商核实。
你要做什么：1）在 TWS 用 orderRef、orderId、permId 核对真实订单；2）不要另下卖单或加量；3）取得持仓、工作单、成交三项证据后再决定。
```

### 30分钟仍未解决 P1

```text
🟠 P1 · 仍未解决 · 保护单状态无法核实
发生了什么：AAPL 持仓数量=3 股 保护单已确认=3 股 保护单可能=3 股 止盈在途=待核实 外部卖单在途=待核实 未保护=待核实 股 可能超卖=待核实 股；当前保护状态待核实；orderRef=v6l-protect-18939d4182c048459f99 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=过期或缺失；same condition, unchanged
影响：保护订单身份或状态未知，未保护 待核实 股仍须券商核实。
你要做什么：1）在 TWS 用 orderRef、orderId、permId 核对真实订单；2）不要另下卖单或加量；3）取得持仓、工作单、成交三项证据后再决定。
```

### 完整 native 202 明确拒单 P0

```text
🔴 P0 · 券商拒绝了保护单，该持仓目前没有券商端保护
发生了什么：AAPL 持仓数量=3 股 保护单已确认=0 股 保护单可能=0 股 止盈在途=0 股 外部卖单在途=0 股 未保护=3 股 可能超卖=待核实（可能上界0） 股；券商已明确拒绝该保护请求；orderRef=v6l-protect-6781c9fc3a154accb55d orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=未获券商确认 工作单证据时间=2026-10-06T14:00:30+00:00；broker explicitly refused the stop-limit request; no automatic replacement
影响：被拒的保护请求无效，未保护 3 股不能据此当作已覆盖。
你要做什么：1）在同一 TWS 用限价单平仓；2）确认该标的 0 股、无在途卖单，并等待成交静默期；3）在本机执行 release AAPL，身份或新鲜证据不足时保持拒绝；不要手改状态库、新开仓或自动重挂保护单。
```

### 新鲜三项证据确认恢复 INFO

```text
🟢 券商保护事件已解除 · AAPL
发生了什么：新鲜三项证据已确认保护覆盖
影响：本保护事件已解除；其他交易门控继续独立核验。
你要做什么：1）在同一 TWS 核对持仓和订单；2）不要绕过其他门控。
```

### 旧 GTC 迟到成交，替代保护待确认 P0

```text
🔴 P0 · 保护单状态无法核实
发生了什么：AAPL 持仓数量=15 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–15） 股 可能超卖=待核实（可能上界5） 股；当前保护状态待核实；orderRef=v6l-protect-851ba35129034024a5b4 orderId=1003 permId=51003 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:32+00:00 工作单证据时间=2026-10-06T14:00:32+00:00；旧 GTC 迟到成交后持仓=15 股；替代保护可能=20 股，已请求减量或撤单，待券商核实
影响：保护订单身份或状态未知，未保护 待核实（可能范围 0–15） 股仍须券商核实。
你要做什么：1）在 TWS 用 orderRef、orderId、permId 核对真实订单；2）不要另下卖单或加量；3）取得持仓、工作单、成交三项证据后再决定。
```

## 9. SHA-256清单

共154项，含完整v6lite包（排除__pycache__）、scripts与本批证据；机器清单 `docs/batch20_evidence/sha256_manifest.json`。报告与清单自身、最终只读审核记录不纳入循环哈希；报告自身SHA在交付摘要单独回报。未stage/commit，未含John运行配置/数据库/机密，未包含无关手册。

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| docs/batch20_evidence/baseline_counts.json | 766 | `f37d8fc2cf6f6fc378c4481b60352bc9a2ad8769f9c883c65589e9d37a373a05` |
| docs/batch20_evidence/baseline_delivery.log | 84807 | `69f5641584ccd216779b116b40c973bc43027a741ffe56ece3c404f142b55bb2` |
| docs/batch20_evidence/baseline_delivery.xml | 81329 | `8b668c373e3ef98df4b7bb0a85586cfa3c695caf51e5fb178afd473e57099426` |
| docs/batch20_evidence/baseline_delivery_metadata.json | 1014 | `98161e6afe3f813b1919781196b22f8cf54ec45d02ce1d89297f83c8448b524c` |
| docs/batch20_evidence/baseline_final.log | 78397 | `ee5a6bc3d26ed91232226cb9e9bd380b5b45b01d614c6320f8bbb918c91ae4b8` |
| docs/batch20_evidence/baseline_final.xml | 76897 | `f610a10aaa12427911f7409eb502909d9aad038750a4ac493cc02f437cbd0225` |
| docs/batch20_evidence/baseline_final_metadata.json | 913 | `7a08e4b6045769da4aae2d3d5d457e7c6ceef6faf133873986e2c960d5e49bd7` |
| docs/batch20_evidence/baseline_frozen.log | 80117 | `aa10c83f0c24c2e09ae478988fcb8a701a3a0f35f57197f55da16d02e92b95d3` |
| docs/batch20_evidence/baseline_frozen.xml | 77938 | `40dd46e4e018d6689d20302063f987950eb94122c0bd1f2afb08fb9f21c7c37a` |
| docs/batch20_evidence/baseline_frozen_metadata.json | 913 | `95a3679d751548b70f9b0b4ab15e458c415c8db8f8e8de92ac6140d8b7fb30a5` |
| docs/batch20_evidence/baseline_red.log | 57 | `6d858582bd728b1847ab22b016736bf649e04d3da4aa7d06302d1e3838ed8268` |
| docs/batch20_evidence/baseline_red.xml | 95128 | `98c74fb8985346d2c6d38bcccb5080e26cdd116a7caa5202225627f71cdb9920` |
| docs/batch20_evidence/baseline_red_verbose.log | 110447 | `9b212b6f680d01bb688a23302be89fa33f9756f741336d24d56c61fc167a2c47` |
| docs/batch20_evidence/build_report.py | 19205 | `42bbb20c7626d8fdc5da987e9ed0e2e29636203c4783251a9f1fd45f020e2a95` |
| docs/batch20_evidence/candidate_focused.log | 17779 | `506bcfdb2313267f399bcbe7f1eb761b660495c04cc0c1acb246e3bd97c09bd4` |
| docs/batch20_evidence/candidate_focused.xml | 23740 | `510f4d0db8107c48a9056fe392e18b45a83d3975b0f2b1dc64c977f0d51b8c67` |
| docs/batch20_evidence/card_provenance_red.log | 6327 | `4b7c61cfebe924fc16f0d1f420e815a69b441c240b56c05d2a484cd301a84bcd` |
| docs/batch20_evidence/card_provenance_red.xml | 3963 | `e5962b42264352f51ebb8cc023173675bb8aeb9ca3fe00562b4a6be2efb89c9d` |
| docs/batch20_evidence/cards_samples.json | 4405 | `caefd8d0f08d765ffc191eb5fff34ec2336302b9948daa5176c3532cbfdaf02f` |
| docs/batch20_evidence/cards_samples.md | 4266 | `cceae460b1233818d380486bd53d4815f36131858d628d105984a6b9e9229f0a` |
| docs/batch20_evidence/focused_all.log | 183 | `efc73e0566b3daadd62a9fde36ec8c47ea8cd8d6d1c5541f55fd04a9aadc4775` |
| docs/batch20_evidence/focused_all.xml | 21186 | `db759f2cb41ba5039e5e6ae0e0c9959db4fea529a43e0f28a8e14759f3df28ca` |
| docs/batch20_evidence/focused_delivery.log | 183 | `228ac6d15d77b3da8599c4dd8976ef961f2397960b4618002c0781dad68fa8a7` |
| docs/batch20_evidence/focused_delivery.xml | 22711 | `407668405f0772819d27a71a5e9a84cb684636ee836825310ecc2940c3a0aef0` |
| docs/batch20_evidence/focused_final.log | 183 | `97272eb766b0bb455300a610983f3de93723b8eafd5b56651f8720c14e05d58f` |
| docs/batch20_evidence/focused_final.xml | 19971 | `0bd867068f506dd58549abca9fd4d9bd9134534d553fa3511386f9a491938e60` |
| docs/batch20_evidence/focused_frozen.log | 183 | `1ba0715f46d1ca98ed14b684e2b5b691cae2adbaa7b2bdb7cd41d8b968b1d7fc` |
| docs/batch20_evidence/focused_frozen.xml | 22407 | `e7cf615a2647ac2bded7b85257515f376ac33c5008c9073ec3ace46cfdca837a` |
| docs/batch20_evidence/focused_green.log | 101 | `bef90734c79af4fe81f25a990fd935788901486be11437d076c1a4da7c6dd8b0` |
| docs/batch20_evidence/focused_review.log | 3803 | `df4e923de37b05953534d56b47f4b437862a03b300311c2bc5b4dee0d649baa0` |
| docs/batch20_evidence/focused_review.xml | 21056 | `163aa9e3364298e19f8ab5bfcce38689590ceca188167d7e9463461a8cbbb15c` |
| docs/batch20_evidence/freeze_environment.json | 792 | `16b1bf40d8c9551d1962150e870cb9c4560dcdc5baf2ff249f703c6b0a88e318` |
| docs/batch20_evidence/incident_log_readonly.json | 11339 | `af7456e51324467c7605991427b91102cd518761916c802b1330442b4e292216` |
| docs/batch20_evidence/incident_state_readonly.json | 2265 | `21722a101e2d45deb869752d89d79cc2b0e85a8086a697d0f5ab03d580ca7090` |
| docs/batch20_evidence/independent_static_review.json | 739 | `674949007a7c1608ff78e5b27d56e4ef38c809438af8cfe85508b9c8a80a86b4` |
| docs/batch20_evidence/interrupted_card_run_note.txt | 319 | `bacafa7feba5e403d7f584aded39aea1c0caccb1b78baad3f6171e67bbcd8493` |
| docs/batch20_evidence/interrupted_card_windows_full_1.log | 1070 | `a4ae302cf15629658bd5f60453e51d7c70798282918005937d7b13bd0e003c1a` |
| docs/batch20_evidence/interrupted_card_windows_full_results.json | 285 | `d4eb2e45a82d55c4f631c724aa18e95667dded7ec18323d4bfc474f0960ec422` |
| docs/batch20_evidence/interrupted_lineend_run_note.txt | 276 | `912b79003a57b774c9d2fb4c51fa80acfc5a91f91356aa43d1dfed383ebe0993` |
| docs/batch20_evidence/interrupted_lineend_windows_full_1.log | 1024 | `90b5eb4b4e54f477f053fadba09ddd69d66196ef0a3d80b1009632c022f1eb3c` |
| docs/batch20_evidence/interrupted_lineend_windows_full_results.json | 320 | `842c7fe8df6c5121006837f6caff1d349816d93187f66060256c84616497eb6a` |
| docs/batch20_evidence/interrupted_run_note.txt | 340 | `be27af3ff612c2403229666f475b61dad69bb9eea0409a71367676d1596f8221` |
| docs/batch20_evidence/interrupted_windows_full_1.log | 1077 | `d2abe35e2c56933b007fd648fb9547fb81fcaa9dceafdeed0007a3a1ef6943f2` |
| docs/batch20_evidence/interrupted_windows_full_results.json | 292 | `a854feac0918fc592ebedacab2560772217d0ca4358fb0bff64b908828f900df` |
| docs/batch20_evidence/product_frozen.log | 1166 | `86965cb584420a473cc24c32e0c235a2cf576fada85f455e8f6105e286e06e23` |
| docs/batch20_evidence/product_frozen.xml | 136620 | `cd2cbfd13733a80aac24bd5b38273afb89851b5f8f20211152a1347479d829c9` |
| docs/batch20_evidence/regression_windows_full_1.log | 37978 | `4250978e8eabef4d149b8a959016f10d399d9382ce0415703dac2e7384a1dd1b` |
| docs/batch20_evidence/regression_windows_full_1.xml | 174552 | `64d1b5751ff7be276b43e2e0425d5233996d6002c8d9fd5842720aabcf03d2fa` |
| docs/batch20_evidence/regression_windows_full_results.json | 301 | `f09bc0b18040c39780ab84bccd4a5bb7437a102bd40136bcbb673c85052a872a` |
| docs/batch20_evidence/render_cards.log | 3863 | `de316c2bb511e2ba5f4088c7b2e41834f861b26cd9336e56da65c3bd78655f85` |
| docs/batch20_evidence/render_cards.py | 3821 | `6863c41b0eeeadfc0602a131dc6bdd93103594ceca49d6d8fced967a7508e9de` |
| docs/batch20_evidence/retired_and_adapter.log | 17201 | `02a584c26da7e0107da5f3e0c4af6df32deb98799ec7eb675362f088fe6eb18c` |
| docs/batch20_evidence/retired_and_adapter.xml | 9589 | `b1e1477531b59bd7d9b7bb5e09e566b26cb8ba3f45417e7031458f66092bfdbe` |
| docs/batch20_evidence/run_validation.py | 4296 | `1ab2901ffb000a1a04a6486f60a4de583f104b3b9888ec1d03931dec86efb157` |
| docs/batch20_evidence/windows_full_1.log | 1249 | `9a07e33268ee28f2e5f7136b1c5058f91cc3a40b8ade99a935736e9238b6be29` |
| docs/batch20_evidence/windows_full_1.xml | 152464 | `3ae86ebcc03e878507f3519416738a5176f009986884e6608dd5523d33cf1e54` |
| docs/batch20_evidence/windows_full_2.log | 1249 | `61fc7df756d97f2c3dacb83a5eba57b1cf6356e6fa0ab8e6c544f5bfbdc59aed` |
| docs/batch20_evidence/windows_full_2.xml | 152464 | `c1146e67604d94f3b83842b918b64b1bf0cf86c0f0f51cdd838d89aaff9720ac` |
| docs/batch20_evidence/windows_full_3.log | 1249 | `99b3b8db79ffdf4f46e12057a7e26d7b78b38d27e244844408ba0042bf35bb6b` |
| docs/batch20_evidence/windows_full_3.xml | 152464 | `97086dd1223ba7618903284cc25e5022c3077feb5c981cd8ac2c6b1b9b993b15` |
| docs/batch20_evidence/windows_full_results.json | 834 | `86202567079cae6f055a457b6556d2c5f9b59369b7057ac94469f9d93c52d494` |
| scripts/tests/test_v6_launchers.py | 35142 | `fff766c38064b7ad5421eadd9639f363e06cfa5a6e6ecb1448a4c22c002b270c` |
| scripts/v6.cmd | 161 | `e6303971e31af8b57e050506ddae1c71516efc3f7c523850438fba3028fcc6a8` |
| scripts/v6.ps1 | 15298 | `9818d068769592206e8b62bc65d4f52bc8bb98b2476c754fb04e2fba985e3d88` |
| v6lite/README.md | 13718 | `4ad81212d1863b4fc11dc87d8bc8c32ea272e95563095243292ed615ced31fd9` |
| v6lite/__init__.py | 313 | `26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39` |
| v6lite/__main__.py | 26616 | `320fbc71bbeab5d4dc03956687e85f0b4fd354137d8ce8173c847b788d40c8f6` |
| v6lite/alerts.py | 53232 | `4b422fd4f2c1e2c3297de04d6193cfdd80adb4dadeee428516fdae171ff00657` |
| v6lite/approval.py | 12709 | `70a3c24325b95216bb307dd261b60ccff88f0f13da3e2f94d6344e92f07b41e1` |
| v6lite/broker/__init__.py | 96 | `2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24` |
| v6lite/broker/base.py | 3720 | `3f667784f14f386df11255c54feaff0c4aecdd183350fa609c6fa2e13dbdb625` |
| v6lite/broker/fake.py | 15627 | `adb982157c7571278c15e5486e2738eda66965902ab5d1f41d0f1186ca62a0dd` |
| v6lite/broker/ibkr.py | 63295 | `fa240f7c06a1f0532157ffe21c315e35bc5d89ee75555eb74c5a07c3c2b44f76` |
| v6lite/clock.py | 910 | `0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1` |
| v6lite/config.py | 18236 | `b68128969b2ed7dc227d45fbafe116f122b0a1d230e8d057ef92ac61fe2e4d7d` |
| v6lite/engine.py | 180482 | `2dee996edb7733862206107477004e0f9718dcabe21eb98f3542a70a6512b7fc` |
| v6lite/examples/live.toml | 2980 | `47e605b64d9b148a38dcc2697b3266ced4494f501b980987d6917424fed7bd22` |
| v6lite/examples/paper.toml | 2872 | `8d0836ae45356ed3650c33f3c3390a304ea5e073a5f0c0ca4ebc81542409e4a1` |
| v6lite/examples/paper_delayed.toml | 2971 | `258febbcf40a112fae652f95b0e0f77b8f0151eb97101da4623c40ee75e8fc49` |
| v6lite/examples/paper_g3n.toml | 1275 | `ad3e6bc61f68349fb7dd489cacb4e3dbcab8c9d8c29c61966302843f865ed8ba` |
| v6lite/examples/paper_test.toml | 954 | `507dea6df5c4c36a58db1bdbda8921707a3fab9721b504be98abce72b26b23d1` |
| v6lite/execution_time.py | 3269 | `6767aee2d823eccc2387106f803b38521bce94e71ec082fe6dd021918ee9f300` |
| v6lite/exits.py | 3927 | `1de7eb5637ee3c6abd1784570846d0edf8f91739ee97e2bb6e0b6651e34de093` |
| v6lite/market_calendar.py | 3925 | `1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2` |
| v6lite/models.py | 6657 | `38c5a8d3fba7694c5e7f7375edc5c2a90eff8d1f2ade31f2c100277279c5379e` |
| v6lite/protection.py | 1315 | `3de59ef71678bf534a66d48f4a3e4b5f49b7bac5a391f1a051cf2e0e648474e5` |
| v6lite/protection_engine.py | 95345 | `201f6fe844a26d0ed5b56e513b8751906ed28dcdc3c37280bd1ecd17ce4acaa9` |
| v6lite/protection_incidents.py | 6422 | `94b27d9c5322ad60a32d9454ae15745a495ae36a5b6150fc8e2e2cc60447c6dc` |
| v6lite/reconcile.py | 2610 | `4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1` |
| v6lite/report.py | 22831 | `a038828db28e2bb9887261141fd9046c72f5913940a9155ef113a18cc74c7189` |
| v6lite/risk.py | 11077 | `abc521f671e878a0ab5690e1fa178dc872d29b5396b86eaeb5f016be1594c687` |
| v6lite/sizing.py | 3294 | `c9b9cc4a116a7f61d9e660dd885d280ec2a9262671f2c3c8ab5c1515724e171e` |
| v6lite/store.py | 66766 | `70c23eae24a500bc3cb89c17889804e4071d2150ef7e7afe14341bc04288fe67` |
| v6lite/strategy.py | 8300 | `9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e` |
| v6lite/tests/__init__.py | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| v6lite/tests/conftest.py | 209 | `aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb` |
| v6lite/tests/helpers.py | 5415 | `0c28d019b52b61f5d0b477bd7b202c409ff6761a4b4830b70c77667a3d9787d6` |
| v6lite/tests/test_batch10.py | 19369 | `5b875c8df97861e22427223e7065a74bddf04a5da1c7115b431871369d177c47` |
| v6lite/tests/test_batch11.py | 10273 | `fb7f26a0bd1969400f2d769a0d65f4c7d3a18f0162332fd46ca9f1ed99c260f8` |
| v6lite/tests/test_batch12.py | 25124 | `3dfc69a4f811549cb32c9c186bbfed3d24f7c1c956c3c743d9b28f6996037cfc` |
| v6lite/tests/test_batch12_v2.py | 20802 | `5691a234501cd71b85e3098e0cf6da2a74f2d4f5ca3eda2797a8abe17fab9af0` |
| v6lite/tests/test_batch13.py | 18609 | `12a3ed033079f129e7e70f610be004c498ea6f6f0283473f60b2eb80ad819140` |
| v6lite/tests/test_batch14.py | 16354 | `5087c53709f4208d5dc4fbe9f74b100d50ebfb2060ccf9c9834358d2c3c7b257` |
| v6lite/tests/test_batch15.py | 70549 | `e9cdfcbadbacd8e59844a97cce327920e83e07d95d4a893ff86c27b8e7de467e` |
| v6lite/tests/test_batch16.py | 28103 | `39782b64a8c18ccbb933210b4152135b0b1b0755585716fbb4a2e5516737e081` |
| v6lite/tests/test_batch16_card_deadline_copy.py | 1932 | `d9c3c8f080748838a81392b1a12ca03347685a1be4692ae9159471ff2cc56a51` |
| v6lite/tests/test_batch16_f5_copy.py | 15127 | `c8ac0d13f2a76ebf369ead1e369091e4c8498cf5f0e9ec64f85b0ab784c51905` |
| v6lite/tests/test_batch16_f6_account_evidence.py | 7194 | `6d97cec645009fd6d90c223cfeb5c902866701f75c01f91a604caddd3a0e863d` |
| v6lite/tests/test_batch16_f6_global_mismatch.py | 2874 | `86c542de449c2adab7cae4d2bc476e5108ef7ef14d9c2f45686a2404a3b9d478` |
| v6lite/tests/test_batch16_f6_release.py | 10603 | `15c23d799539945057095f56bad2e8ba8a32974383eb8d778fba0f57ede2cb96` |
| v6lite/tests/test_batch16_f6_safety.py | 5402 | `9e2464cc21430b8b4e27dd8982b1a5aee0b554a5e0698691df3156715ad65b9c` |
| v6lite/tests/test_batch16_f6_steady_fairness.py | 2893 | `e7ae2a84c08b2adadb1aa9994e835b5504db581401eecbf2bfd14678e984915d` |
| v6lite/tests/test_batch16_f6_steady_timeout.py | 2401 | `733347eda5814cbd2a14f71a717c88274bdd51376cada2c9bceeab3fd2908c48` |
| v6lite/tests/test_batch16_f7_price_flip.py | 4495 | `b2f9cf68a543999d08ab0567e6e6a5663f257d2525c48430f953de8bacfbc5dd` |
| v6lite/tests/test_batch16_f8_backoff.py | 2126 | `af5c1b6c62872c84536b222255c9a3b4229e2d7bf4ce1c4b55b1d4a780aa3539` |
| v6lite/tests/test_batch16_gap_evidence.py | 26359 | `328269e2c8fa8a0547d1d16a5904a4d6cc5462f13b093b9e6c09eb87bd4be8ba` |
| v6lite/tests/test_batch17_alerts.py | 10542 | `dfc6a85711add46c7a6e8b966c3f75f38f2bbbc7bee1b3f827882d3d9ddba5ff` |
| v6lite/tests/test_batch17_equivalence.py | 9719 | `ecb49e23e9cbcd6ea2803aacb4b649d8b8794b1c79e65f266141c655efdfca8a` |
| v6lite/tests/test_batch17_g8.py | 5126 | `4e21318f45dc6293fc4fbdc0f1c5be7c9d845782bf405fff8c6a51504bae5875` |
| v6lite/tests/test_batch17_heartbeat_account.py | 20900 | `5ab7bafaddb4ab15e16c275b45061079c46c3d642b866a91ac55f85a75c3f6de` |
| v6lite/tests/test_batch17_l3_recovery.py | 15783 | `e965ce76adb71525b1e1d018b84af3525be13fdeabd7f3f7f557dced42d675b2` |
| v6lite/tests/test_batch19.py | 10695 | `f4ca35e62e9b4ab17b7904b783d966ac8a9782466702297f5ab98c1dc9adb4e4` |
| v6lite/tests/test_batch19_funds.py | 19657 | `f9c1ae564a76752edcb60b3293eec5a4b2ac15657b4e34258ba814cf4a78dde4` |
| v6lite/tests/test_batch19_protection_equivalents.py | 13413 | `12def834c0ef3a32bf71331473a47daae9e7d2b4309347e177784421cdeec303` |
| v6lite/tests/test_batch19_review_edges.py | 7207 | `262a24defa92162552f14d80dfc1eb45d673e7a538a7c0ead745b7649515f7d6` |
| v6lite/tests/test_batch20_card_provenance.py | 1430 | `59a6803e2e773af4a43de541dd3851f25dcf9d260c934f3f18d705b25d5db64d` |
| v6lite/tests/test_batch20_execution_time.py | 20598 | `a50be1979410bc14864a7eead9575a7fcdd99530f36deabb772dc3bce221b64f` |
| v6lite/tests/test_batch20_protection_equivalents.py | 51412 | `6433d0872bdd05ad1871914732c07c76925cbf430b908a2de63e3ac3b0f22504` |
| v6lite/tests/test_batch20_protection_incident.py | 35319 | `0575945a04620e673c7e309c8e91d71d4a956293fe7541b9d469db697d034f5c` |
| v6lite/tests/test_batch20_template.py | 810 | `8c6ed584a5f435fe81542df99ce8c7a1d040f342583b733fe65356af352b5230` |
| v6lite/tests/test_engine.py | 17668 | `bb8967a536a8d187fc9f1d742be17c0b4b90e193ab52731ac06d6bff8a08f461` |
| v6lite/tests/test_g2_day1.py | 10712 | `cacaf5b3115d4a298fe8b3fe065d66fe70277b8ee4cd5d9643f0e2fff0ff5f9d` |
| v6lite/tests/test_g2_day1_selfreview.py | 8081 | `fe84ef645d99f421c64f670aa7f883d16b5a24c8c15b4dd30a4aa8f035b5ea06` |
| v6lite/tests/test_g2_day2_prep.py | 2547 | `a0db6b981992a3cf20d9f959a679c608842f4619007331bae9b26908cee3ccb7` |
| v6lite/tests/test_g2_probe.py | 7222 | `ba38215d7a6946d60884d0e3dac99b641e7bcde058c4893b94cb9d62b6a6fe18` |
| v6lite/tests/test_ibkr_adapter.py | 6081 | `6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a` |
| v6lite/tests/test_review_fixes.py | 22689 | `eb91ef957bfd13864c2c92a5da2cf3a29ffdc10c9f5c74fe751daa8f4caecdd3` |
| v6lite/tests/test_review_round2.py | 38526 | `7ab32142a9a0ee2fdb54853297cf6eae619ce28de4cac4dc646a8a8035660822` |
| v6lite/tests/test_review_round3.py | 15213 | `b5bf4e5c513e9d2310dc6404f147b633790cff0e894546e9176d6c26390a7427` |
| v6lite/tests/test_review_round4.py | 18605 | `04cf584aa3b7e13842c1e7608d03e5f888b7a714ea073001ce85281fe1eb43cb` |
| v6lite/tests/test_review_round4_selfreview.py | 13981 | `8cf2bba5d54e2e644bc016e68abfe87f0ecbe26b6e54d5a44a329c61e6dc7da8` |
| v6lite/tests/test_review_round5.py | 16770 | `c45a477f2bb20f021b3d8040b3be6360d78bb8c95f0ef7ffd2ba069fdf31eeb0` |
| v6lite/tests/test_review_round5_selfreview.py | 12064 | `03adaac71b3ea18095732064c51f6f4741400adae738c3cfb9f840c247ec4a8e` |
| v6lite/tests/test_review_round6.py | 10254 | `c6eabe6e953cc75ccfa7e4fa87625a86d677ef5dd2fca9975cd101b3403d6be5` |
| v6lite/tests/test_review_round7.py | 9101 | `bcb2baea638b6ba6c063aba7776aaf1137c3cd1df98297a08311cdaf899994a5` |
| v6lite/tests/test_review_round7_selfreview.py | 6455 | `a3589fae5996245f7246f1860875a847b6761ed9f8bab5a317ab5c7d9e5e86bf` |
| v6lite/tests/test_review_round8.py | 7212 | `c65ac1c396468aae3124ffcdc0f61b2ed483547ec1b0f47119f00950008b3736` |
| v6lite/tests/test_review_round8_selfreview.py | 7315 | `e927860575cb55769a910a0dfbced96b725922da6629010c894ec2d3c86b36c3` |
| v6lite/tests/test_soak.py | 5780 | `abb1c1b1742d30353157518676a980ea162902178945701f67aeb69b82f4cd1c` |
| v6lite/tests/test_strategy_parity.py | 4014 | `732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f` |
| v6lite/tests/test_units.py | 8846 | `75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42` |
| v6lite/tools/__init__.py | 34 | `91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1` |
| v6lite/tools/g2_probe.py | 20555 | `e756a719f9f8c1cded3d1123f67fa0097b504680589319be40619bc5d7f6763e` |
| v6lite/version.py | 863 | `9aa52b23a3c8c08801f9d86c46ae1090a28c78dbde5d5b4b2923347bc74ece45` |
