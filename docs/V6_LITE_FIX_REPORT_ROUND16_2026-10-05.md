# V6-Lite 第十六批修复报告

Started 2026-10-04 PT; completed 2026-10-05 PT. 工作区 `C:\Dev\V6Lite-dev`，`dev` 分支；基线为附注 tag `v6lite-batch15`、提交 `7ea1309181db04ee5eee45e13102030828886f19`，基线 `code_sha=ed27369e65322a9b`。本批所有交易验证均使用 FakeBroker；未连接 Gateway，未修改 `C:\Dev\V6Lite`，未 stage、commit 或 push。工作期间无 V6-Lite 运行进程（写入前只输出进程计数 0）。

第十五批复核报告经逆构核对，当前提交中的 SHA-256 为 `7f48fbdb8f4cf939278cb5218655e39b26e388dcb6ccc296598e1e5bddc8f551`。历史转述的 `43a3119e…` 属中间版本；第十五批正式提交前已对报告、65 项清单及 `ed27369e65322a9b` 逐项复核，清单 65/65 一致。

## 发现 → 改动 → 测试

| 项目 | 第十五批发现 | 第十六批改动与安全边界 | 红测、绿测 |
|---|---|---|---|
| F1 自有成交 | 正常 GTC/TP 成交误走迟到或外部卖单，可能撤掉余下 GTC | PROTECT 成交同事务减少持仓与已确认/可能保护剩余，保留 `pending_qty` 的 IB `totalQuantity` 语义；自有 TP 成交同时减少持仓与自有 TP 在途上界，只有撤单恢复后的迟到 TP 才按同 ID 只减；减量后 GTC 全成时状态为 FILLED，但 B 计时和 TP 撤/恢复仍继续。 | 不可变第十五批红测；191 passed / exit 0 |
| F2 TP 确定未发 | 非末股 TP 被拒、无报价、过期报价或限价不可成交时，GTC 只覆盖部分持仓 | 确定未发才进入有证据的同 ID 恢复；三源与服务端确认后 B 以 `tp_not_sent` 闭合。`maybe_sent` 保持 UNKNOWN/不加量。 | 4 类拒发及 maybe-sent 独立用例，聚焦通过 |
| F3 触发未成交 | IB API 不使用 `Triggered` 状态，旧监测会漏 P0或误报 | 对 `Submitted/PreSubmitted` 按现价穿越触发价给一次 P1；现价低于限价或 60 秒未减量给 P0；穿越状态持久化，部分成交重置时钟，不发第二张 SELL/MKT。 | 真状态、穿越、价格与 60 秒边界用例，聚焦通过 |
| F4 查询节流 | 稳态每 tick 三源、一次超时即 P0 | WORKING 且无 pending/TP/A/B 时每 15 秒维护，单次失败只记日志，持续 60 秒才 P0；活跃窗口仍逐轮取证。冻结且相同矛盾的纯稳态证据用独立最近尝试时钟节流，未伪造 `confirmed_at`；永久 GTC 不再令普通 reconcile 每轮请求。慢稳态维护移到其他标的普通退出决策之后，紧急窗口仍优先。后续审计再发现稳态失败自置 priority 会饿死 exit/scan，已只让真实回调置优先标志；每轮最多核验一个稳态持仓且失败也轮换，每个稳态阻塞查询前预留普通退出预算。 | F6 安全 3 项、稳态失败 1 项、公平轮换 1 项均已聚焦通过 |
| F5 告警 | 保护类共用影响/动作，手机正文用 P/S/T/X/U 缩写 | 15 种卡片拥有中文数量、独立影响与 1）2）3）动作；接管前 B 独立 subject，避免与接管后 B 去重；回调失败、泛型撤单拒绝、停机孤儿 GTC 三张独立卡也显示中文未知量。迟到 TP 减量请求阶段明确“待券商确认”，同代状态与严格新快照确认后才称“已减量”；失败/UNKNOWN 不称已减量。 | 15 类模板 + 3 独立卡 + 动态外部卖单/迟到成交，两阶段用例通过；实际渲染见附录 |
| F6 保护开启回归 | 旧 Paper fixture 默认关闭保护，老测试未覆盖 L7 | `V6LITE_TEST_PROTECTION=1` 只改变测试 fixture 默认开关；保留旧场景中与 L7 互斥的预期失败，逐节点分类。冻结稳态矛盾证据按 15 秒重试且不伪造确认时间；普通退出免受另一持仓稳态慢查询阻塞，活跃 B 继续逐轮取证。人工全平后 release 必须先见精确 GTC 撤销终态、两轮订单/仓位/执行一致且过静默期；UNKNOWN/held 的保护或普通意图均拒绝。后续安全审计补出两类相反风险：同 ref 但 orderId/permId 错或缺的取消回报不得制造可信 `CANCEL_SEEN`；账户级执行取证异常须审计拒绝而不使 tick 中断。 | F6 安全 3/3、release 7/7、账户执行 6/6、稳态失败/公平各 1/1 聚焦通过；保护开启诊断已复跑，逐节点表见下 |
| G3-T 一次性 B 演练 | 正常 TP 在 Paper 很快成交，不易演练 B10 撤单 | 测试配置、Paper/client132、运行就绪 epoch、五分钟 TTL、operator、标的校验后，只对下一张 TP 一次性使用高于新鲜 ask 的 DAY/LMT 限价；控制键在发送前原子消费，B10 精确撤单后以三源确认同 ID GTC 恢复。 | 入场 3 股→GTC3→减至2→非可成交 TP1→B10 撤单→GTC3 的独立红绿测，聚焦通过 |
| 空窗证据 | 第十五批本地 A/B 闭合在慢三源前取旧时钟，会低估；重叠 partial、回补/跨日报表可能把未知当 0 | 证据完成后重取时钟；每 execId 持久化 A；旧 schema4 A 起点不丢；回补旧 BUY 标 `recovered_unverified`；日报把未闭合、历史日和跨日完整持续时间明示。慢请求中回调优先标志不丢。第十五批已通过的 disabled/dry-run 无饥饿测试保留、不重复宣称修复。 | 不可变第十五批独立 19 项：12 failed/7 passed；最终 19 项19 passed / exit 0 |

F1 旧 GTC 已被替换后才来的修正 execId，若无法验证旧身份，会触发 UNKNOWN/冻结而非盲目入账；此边界尚需 G3 券商事件核对。F4 `_working_at` 仍用于手机卡片的新鲜度判断（5 秒）和普通 SELL 容量复核（60 秒），迟到 SELL 同流程的保守减量会参考缓存但缓存过期时阻断新增风险、优先三源；不得宣称全部路径与该缓存无关。正常自有 PROTECT/TP 成交不依赖五秒工作单缓存决定撤单。

状态回报的 `StatusEvent` 目前只携带 orderRef、orderId、permId 和状态；保护取消必须和持久 orderId/permId 精确相符，缺或错即 UNKNOWN/hold/P0，`release` 继续拒绝。回报自身未携带可独立验证的 account/client/conId，这些身份仍须依靠启动连接身份及新鲜工作单核对；G3 必须保存 TWS 中原 GTC 的账户、client、conId、orderId、permId 截图与 API 日志，不能把单条 Cancelled 字符串当作服务器完整证明。

保护开启的独立全量诊断又暴露同 tick 入场门控回退：外部 SELL 在保护三源查询期间改变券商持仓，保护证据发现持仓与账本不符并发 P0，但旧 `quantity_suspect` 尚为空，另一标的可能开仓。独立五场景测试在本批候选 4 红/1 绿；修复后，对于新鲜券商持仓差、负数/小数持仓、工作 SELL 剩余负数/非有限值，立即置跨标的入场阻断；未冻结时要求新一轮对账，非法数量额外进入紧急状态。正常三源查询超时仍按 F4 60 秒口径，不混同为语义矛盾。既有冻结稳态重复矛盾节流回归和五场景一起聚焦通过；默认全量须在该改动后重跑。


The protection-on F6 diagnostic also found two ordinary TP boundary regressions. F7: a queued TP could wait through slow three-source evidence while its latest quote crossed the stop, fell below TP, disappeared, or became stale, yet still reduce the GTC. Four current-candidate parameterized tests failed naturally; the engine now rechecks a fresh quote and `decide_exit` after the slow queries and before any GTC reduction, preserving the same GTC and sending no DAY TP/MKT when the stage changed. A separate callback-at-final-quote red test showed a GTC execution changed position and protection from 20 to 19 after three-source proof but left a phantom `tp_stage=TP1`; a local generation/identity guard now checks immediately before write, so that stale proof cannot reserve the TP workflow. F8: a failed protected TP evidence query bypassed the existing ordinary-exit 30-second backoff; the new test failed naturally when two TP queries began only 15 seconds apart. The retry now waits at least 30 seconds after the failed query completes; active A/B and F4 steady evidence continue on their own priorities. F7/F8 plus the main Batch16 and configurable card-deadline copy tests passed together: 42 passed, natural exit 0 (3.93s). These are candidate-code red proofs, not claimed as immutable Batch15 red proofs.

## 测试证据

测试 Agent 在不可变 `v6lite-batch15` 归档副本上先做自然红测，未改变生产基线。当前第十五批兼容、第十六批主测试、空窗证据、F5 文案、F6 安全/release/账户执行/稳态失败/公平轮换十份聚焦文件连续一次自然退出 0：191 passed，16.22 秒。第十六批主测试在基线 31 failed/2 passed，空窗证据 12 failed/7 passed，F5 文案基线红测 25 failed；F6 安全基线 2 failed/1 passed、release 初始 3 failed/2 passed、账户执行初始 4 failed，后续精确取消身份、异常拒绝、稳态失败和公平轮换各有当前代码自然红测。各文件最终 SHA 在代码冻结后列出。L7-on hostile-broker soak 已用更新后的测试适配器完成 5 seeds×3 天，5 passed/37.45 秒，不能把先前因旧 `ProtectionRequest.side` 崩溃的探索运行算作覆盖。最终 Windows 全量连续三次由独立 Agent 串行执行，记录每次自然退出码、passed/skipped/耗时；100% 进度点阵不算成功。保护开启诊断与默认三轮分开，并将每个失败节点与旧语义、fixture 身份或产品问题对应。交易日 06:00–13:30 PT 不运行全量大计算。

Final protection-on diagnostic: 588 passed, 84 failed, 2 errors in 74.96s, natural exit 1; 86 failing nodes: 37 environment/fixture premises, 38 L7-equivalent coverage debts, 11 covered by existing L7-specific tests. These 38 are not waived or verified. Compared with the earlier 94-node exploratory run, 86 remain and no new failure node appeared. R5F1/SR6_A1 cross-symbol gate and two Windows short-timeout fixture nodes now pass. Final raw log SHA-256 C4FFD31BA10492F04687C6E3226CA0E43B1D27791EAD39CD04FBCD76F482B0E9; JUnit XML SHA-256 224F3C48B194A570F2979332DF07A9B8032B9DBE1FA1C64685E9F7CA9F893F8A.


Final Windows default full-suite verification, performed serially by an independent agent; each run naturally exited 0: RUN1 674 passed / 74.30s / SHA-256 D9C255E2F35EF4E11A22ED682355C5307A0EE26CB574F49E76E5F525EC51BAA6; RUN2 674 passed / 74.50s / SHA-256 B311286338CBFD63614C0AEEC84F9A2C405AEC17119181A605BCF536A1F37BB0; RUN3 674 passed / 74.57s / SHA-256 A03314E07B4144F5BED98EE3E3C39F85AD5B2E99676AB33ADFB449E1D067ECF4. The three raw outputs are preserved in TEMP as `v6lite-b16-final-full4-{1,2,3}.txt`.


Earlier diagnostic runs were not counted toward the final three: the first default full run exposed a legacy STOP late-fill regression and a prior-day held-intent test oracle mismatch (658 passed, 2 failed); the exploratory protection-on run had 523 passed, 92 failed, 2 errors and led to the F6 per-node triage. During a Unicode copy edit, a temporary `protection_engine.py` working file was truncated; the intended WIP source was reconstructed from local command history, then AST-checked, focused-tested and checked through actual FakeBroker Chinese card rendering before further implementation. That reconstructed file was not described as byte-identical to the damaged WIP, and no Gateway was contacted.

## G3 边界与运维

`docs/V6_LITE_G3_RUNBOOK_DRAFT.md` 已更新为第 2 版：G3-T client132/独立 `paper-test` 状态与 G3-N client131 不得并行；仅 Paper Gateway 4002 在 John 监督下取消 Read-Only API，结束后恢复；Live Gateway 持续只读。每日报告列 A/B 完整最大空窗、未闭合窗口、`[PROTECT_DISPATCH]`、`[SLOW_TICK]`、P0/P1 与手机接收时间；本地接收时间无法证明网络传输延迟，必须与 TWS/券商事件时间交叉核对。`recovered_unverified`、跨日执行范围不全或缺证据时，一律记“未证实”，不能用于 ≤5 秒放行。手册列三轮完整 3 股退出、两次止损、一次 B 超时恢复和一次强退重启的最少演练。

外部 SELL 一经发现冻结该标的；若人工决定保留外部单，Lite 在未平仓期间不再做止盈，原 GTC 仍需核实。`release` 只允许券商确认为 0 股、无工作单、精确 GTC 取消终态及两轮稳定证据后执行；UNKNOWN/held 意图即使当前快照无单也不能自动释放。冻仓不是普通持仓继续自动交易的入口。人工全平先撤 Lite 卖单并等确认，再由 John 卖实际余股，最后三源对账并 `release`。客户端专属 `executions_today()` 对 TWS 另一 client 的手动 SELL 可能不可见；新增只读 `account_executions_today()` 使用账户过滤、不限制 clientId，跨客户端执行一律不主张 Lite 所有权、不入账。release 两轮同时比对自有与账户级执行签名，要求账户级包含自有执行且有 SELL。IBKR 的 [ExecutionFilter 说明](https://interactivebrokers.github.io/tws-api/executions_commissions.html) 支持账户/客户端过滤；真实 Gateway 对另一 TWS 用户的可见性和历史范围仍须 G3 亲测，不能从 API 形状或 FakeBroker 结果推断。若账户级证据不可得，安全拒绝 release。

## 文件和卡片附录

最终报告列出本批全部任务文件的 SHA-256，包含本指令、新增测试、更新测试、手册与生产代码；报告自己的 SHA 单独回报，避免自引用。保护类实际渲染样例覆盖 `_protection_page` 的 14 个可达 kind，字典保留但当前无调用的 `trigger suspected` 单列模板样例，再列回调本地写失败、未认领 GTC 泛型撤单拒绝、停机发现未认领 GTC 三张独立卡。所有样例由 FakeBroker+MemoryChannel 生成，订单号为测试虚构值，不代表 Gateway 已实测。




## F6 protection-on: 86-node classification

Source: `v6lite-b16-final-env-on-4-node-triage.csv`, SHA-256 C0D45F8A199C4053B7D2C3A6E996A01CF3C878463EC2EBFCD22225C4F6630D6D. The 86 node IDs were independently compared with the final env-on-6 JUnit XML: no new or cleared IDs. The table records every final failure; L7-equivalent coverage debts remain open. The 22 nonlegacy detailed diagnoses come from `v6lite-b16-env-on-nonlegacy22-root-causes.csv` (SHA-256 FAA04A8967C3F57198967BA2864E7459E894DB03C134E8412FF5AE8BC9198E7B). OLD_FIXTURE and OLD_ORACLE identify invalid old premises; OLD_ORACLE_PLUS_PRODUCT retains the product-side test requirement.

| Node | Category | Assessment | Precise cause and next evidence |
|---|---|---|---|
| test_batch14::test_T3_fixed_quantity_does_not_shrink_to_circumvent_order_cap | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T3_fixed_quantity_still_obeys_available_funds | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T3_fixed_three_shares_and_persisted_test_levels | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T4_disconnected_next_tick_consumes_request_before_reconnect | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T4_future_dated_request_is_consumed_without_order | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T4_manual_entry_still_obeys_daily_loss_latch | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T4_manual_entry_still_obeys_kill | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T4_one_control_is_consumed_once_on_next_tick | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T4_request_during_startup_connect_cannot_execute_first_tick | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T4_restart_discards_crash_replay_before_broker_connect | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T4_slow_final_broker_step_cannot_submit_after_request_TTL[available_funds] | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T4_slow_final_broker_step_cannot_submit_after_request_TTL[open_orders] | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T4_slow_history_cannot_submit_after_request_TTL | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T4_stale_or_next_day_request_is_consumed_without_order[tick_time0] | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T4_stale_or_next_day_request_is_consumed_without_order[tick_time1] | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T4_unstarted_test_state_refuses_request | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T5_P0_P1_alert_titles_have_TEST_prefix | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T5_startup_card_and_report_distinguish_test_mode | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch14::test_T5_WOULD_BUY_and_entry_exit_logs_carry_test_flag | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_batch16_gap_evidence::test_L7_disabled_protection_priority_cannot_starve_normal_scan_and_entry | FIXTURE_SWITCH | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_engine::test_breakeven_stop_after_tp1_sells_remaining | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_engine::test_daily_loss_latch_cannot_be_resumed_same_day | FIXTURE_ORDER_ID | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_engine::test_exit_retries_then_escalates | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_engine::test_kill_blocks_entries_but_exits_still_run | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_review_fixes::test_L1_dry_run_engine_refuses_formal_state | FIXTURE_ORDER_ID | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_review_fixes::test_L1_dry_run_never_writes_to_broker_even_with_pending_orders | FIXTURE_ORDER_ID | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_review_fixes::test_L3_client_id_change_with_active_orders_aborts | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_review_fixes::test_L3_exit_waits_for_broker_truth_after_hidden_late_sell | FIXTURE_ORDER_ID | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_review_fixes::test_L3_late_entry_fill_after_close_opens_a_managed_position | LEDGER_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | 价107在L7由GTC止损处理，sent[-1]仍是ENTRY，测试再fill ENTRY而非STOP，持仓未关闭，首个open_positions空断言不成立; 用真实PROTECT成交关闭原持仓后再注入迟到ENTRY；验证新持仓与GTC重建 |
| test_review_fixes::test_L4_trade_result_uses_net_pnl_and_estimates_missing_commissions | LEDGER_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | 循环假设TP立即出现在broker.sent且可逐次fill，L7先同ID减GTC并等状态确认再发DAY TP，旧循环没有完成卖出，故未闭仓; 以确认减量和真实TP fills走完三阶段后核费用估算/补报 |
| test_review_round2::test_C2_prior_day_held_sell_never_blocks_the_stop | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_review_round2::test_C5_exit_orders_are_stamped_with_the_real_send_time | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_review_round2::test_R1_client_id_change_refused_while_broker_has_working_orders | FIXTURE_IDENTITY | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_review_round2::test_R3_late_entry_fill_in_two_batches_is_fully_managed | LEDGER_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | 共用_closed_after_partial_entry取sent[-1]当STOP；L7此处仍是ENTRY，helper未先关原持仓，后续迟到两批前提不成立; 用PROTECT真实fill先CLOSED，再测两批迟到ENTRY |
| test_review_round2::test_R3_late_entry_replay_after_restart_is_idempotent | LEDGER_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | 同一_closed_after_partial_entry helper未关闭原持仓，重启回放不是已平仓后的迟到ENTRY; 重建L7已平仓fixture后验证重启去重 |
| test_review_round2::test_R3_three_late_batches_at_different_prices | LEDGER_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | 同一helper误填ENTRY而非STOP，三批不是平仓后新增批次; 真实PROTECT平仓后按3/3/4复核数量和加权价 |
| test_review_round2::test_R4_mismatch_found_by_reconcile_first_still_recovers_executions | FIXTURE_ORDER_ID | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_review_round2::test_R6_baseline_uses_prior_close_holdings_not_current_quantity | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_review_round2::test_R6_full_exit_while_baseline_pending_counts_from_prior_close | LEDGER_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | 跨日价107触发券商GTC而不创建本地STOP，sent[-1].purpose仍ENTRY，测试在填单前即失败; 用已有GTC执行回报平仓后核前收盘未到/到达的日损闩 |
| test_review_round2::test_R6_prior_close_unknown_blocks_entries_but_not_exits | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_review_round2::test_R7_commission_reported_before_its_fill_is_used | FIXTURE_ORDER_ID | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_review_round2::test_R7_late_commission_after_close_still_counts_the_loss | LEDGER_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | 旧立即TP循环默认DAY SELL独立发单并闭仓，L7先减GTC/等券商确认，未闭仓故后续佣金损失前提不成立; L7同ID减量确认和TP成交闭仓后再注入迟报佣金 |
| test_review_round2::test_R8_commission_arriving_during_scan_can_latch | LEDGER_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | 扫描回调在calls2把sent[-1]当STOP并fill，L7 STOP不在sent列表而在sent_protections，未产生目标SELL费用/闭仓，闩为0; 扫描中注入真实PROTECT fill与CommissionEvent再核同tick入口闩 |
| test_review_round2::test_R8_stop_fill_during_scan_latches_before_the_next_entry | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_review_round2::test_race_final_sell_boundary_counts_other_working_sells | SAFETY_ORACLE_REVIEW | 已覆盖（L7专项等价测试） | test_batch15::test_L7_urgent_late_sell_counts_other_working_sell_and_pending_protection_max; test_batch15::test_L7_gross_sell_invariant_through_confirm_pending_partial_and_cancel |
| test_review_round2::test_race_late_fill_of_cancelled_stop_cancels_the_retry | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_review_round3::test_Q1_held_order_that_shows_up_working_again_is_reserved | SAFETY_ORACLE_REVIEW | 已覆盖（L7专项等价测试） | test_batch15::test_L7_progress_gross_sell_uses_pending_max_not_only_fresh_gtc_remaining |
| test_review_round3::test_Q1_orphan_working_sell_reserves_the_shares | SAFETY_ORACLE_REVIEW | 已覆盖（L7专项等价测试） | test_batch15::test_L7_external_sell_is_detected_frozen_and_reduced_after_detection |
| test_review_round3::test_Q1_sell_capacity_needs_fresh_broker_evidence | SAFETY_ORACLE_REVIEW | 已覆盖（L7专项等价测试） | test_batch15::test_L7_gross_sell_invariant_through_confirm_pending_partial_and_cancel |
| test_review_round3::test_Q7_queued_exit_work_is_inside_the_protection_budget | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_review_round3::test_SR3_blocked_exit_cannot_livelock_the_tick | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_review_round3::test_SR3_stop_starts_in_the_same_pass_that_abandons_a_take_profit | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_review_round4_selfreview::test_a_stop_blocked_by_missing_evidence_pages_p0 | SAFETY_ORACLE_REVIEW | 仍待L7等价测试 / OLD_ORACLE | 旧断言期待本地STOP被拒和同名P0；L7保留GTC，稳态取证失败走F4重试及60秒P0，单次query失败不是本地STOP下单; 核GTC保留、无额外DAY SELL、连续失败60秒P0 |
| test_review_round4_selfreview::test_R3_no_freeze_when_fresh_evidence_matches_after_a_failed_refresh | FIXTURE_ORDER_ID | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_review_round4_selfreview::test_R5_difference_found_at_the_exit_boundary_blocks_entries_at_once | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_review_round4_selfreview::test_R6_daily_report_shows_the_release | LEDGER_REVIEW | 已覆盖（L7专项等价测试） | test_batch16_f6_release::test_B16_F6_release_refuses_working_GTC_then_accepts_verified_cancelled_flat |
| test_review_round4_selfreview::test_R7_a_frozen_symbol_does_not_force_a_reconcile_every_tick | POTENTIAL_PRODUCT_QUERY_STORM | 已覆盖（L7专项等价测试） | test_batch16_f6_safety::test_B16_F6_frozen_steady_contradiction_rechecks_at_bounded_cadence |
| test_review_round4::test_F1_failed_fresh_order_query_refuses_the_sell | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_review_round4::test_F2_persistent_difference_freezes_the_symbol | FIXTURE_ORDER_ID | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_review_round4::test_F2_release_after_flatten_closes_the_ledger_position | FIXTURE_ORDER_ID | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_review_round4::test_F2_stale_position_cache_never_sizes_a_sell | FIXTURE_ORDER_ID | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_review_round4::test_F7_exit_is_re_decided_with_the_latest_quote_before_sending | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_review_round4::test_F8_back_off_is_honoured_when_the_queued_item_runs | POTENTIAL_PRODUCT_SCHEDULING | 仍待L7等价测试 / OLD_ORACLE_PLUS_PRODUCT | 旧价107低于stop108.47，env-on的14:01:18 open_orders是F4保护稳态重试，旧测试把所有查询误算STOP退避；另真实TP120取证失败会15秒内再试，确为F8新缺陷; test_batch16_f8_backoff.py要求TP失败结束后30秒，F4稳态单独可重试；修后绿证 |
| test_review_round4::test_late_fill_found_before_the_freeze_deadline_prevents_the_freeze | FIXTURE_ORDER_ID | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_review_round4::test_missed_buy_freezes_then_flatten_and_release | LEDGER_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | 外部持仓数直接改为flat但原GTC仍工作，release按在途保护单拒绝，故持仓未RELEASED; 先精确取消GTC、同代终态和两轮账户级三源证据，再释放 |
| test_review_round4::test_price_only_correction_blocks_entries_while_exits_continue | SAFETY_ORACLE_REVIEW | 仍待L7等价测试 / OLD_ORACLE | 修正异常/开仓门控断言已过，末尾仍要求本地STOP在broker.sent；L7由既有GTC止损，无第二卖单; 保持纠错后全局门控与GTC身份/剩余有效，无额外DAY SELL |
| test_review_round4::test_prior_day_partial_entry_is_frozen_then_released | LEDGER_REVIEW | 仍待L7等价测试 / L7_FIXTURE_INCOMPLETE | fixture清空FakeBroker订单抹掉GTC但留下活跃PROTECT意图；人工只resolve ENTRY，没有精确GTC Cancelled/账户成交证据，所以第二次release仍拒; 为L7建可验证GTC取消及账户级执行证据；当前默认保护关新版测试已两阶段绿 |
| test_review_round4::test_release_counts_as_a_loss_for_the_streak | LEDGER_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | 直接把broker持仓改flat却未撤工作GTC/证明执行，release拒绝且无释放损失事件，streak仍1; 完成精确GTC取消和账户证据后测RELEASED损失进入连败 |
| test_review_round5_selfreview::test_late_buy_after_a_resolved_freeze_is_evidence_only | FIXTURE_ORDER_ID | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_review_round5_selfreview::test_late_buy_after_release_of_a_recovery_position_is_evidence_only | LEDGER_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | 价107不生成本地STOP，sent[-1]仍ENTRY，测试在首个STOP目的断言即失败; 真实PROTECT fill平仓后构造迟到BUY与已核实release |
| test_review_round5_selfreview::test_refused_sell_does_not_size_a_stop_from_a_wrong_ledger | SAFETY_ORACLE_REVIEW | 仍待L7等价测试 / OLD_ORACLE | 测试要求STOP被拒P0和后续程序化STOP卖完；L7旧STOP路径不运行，保护GTC及三源差异P0/冻结是等价风险控制; 用L7 broker P30 ledger20不一致验证门控、GTC仅减不增及无额外卖单 |
| test_review_round5::test_R4F1_release_needs_the_position_stream_to_agree | LEDGER_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | release确实拒绝且保留持仓，但先发现仍工作GTC，用户卡片给在途订单而非旧测试预期的stream20文字；检查顺序变了; 先经精确取消/三源核验排除GTC，再注入流缓存20验证stream二次门控 |
| test_review_round5::test_R4F3_late_buy_after_release_never_recreates_a_position | LEDGER_REVIEW | 已覆盖（L7专项等价测试） | test_batch16_f6_release::test_B16_F6_release_refuses_working_GTC_then_accepts_verified_cancelled_flat |
| test_review_round5::test_R4F4_frozen_position_closed_by_its_own_fills_can_be_released | LEDGER_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | 价107不生成本地STOP，sent[-1]仍ENTRY，测试在目的断言即失败，未到冻结/释放流程; 真实GTC成交且回报迟到后核CLOSED+frozen的resolve_freeze |
| test_review_round5::test_R4F5_freeze_lists_held_and_orphan_orders_that_were_not_cancelled | CANCEL_SAFETY_REVIEW | 已覆盖（L7专项等价测试） | test_batch15::test_L7_orphan_gtc_cannot_be_generically_cancelled_without_exact_ledger_identity; test_batch15::test_L7_take_profit_not_filled_by_ten_seconds_is_cancelled_then_reprotected |
| test_review_round5::test_R4F7_frozen_symbol_with_a_held_intent_is_not_reconciled_every_tick | POTENTIAL_PRODUCT_QUERY_STORM | 已覆盖（L7专项等价测试） | test_batch16_f6_safety::test_B16_F6_active_B_cancel_pending_keeps_per_tick_evidence; test_batch16_f6_safety::test_B16_F6_frozen_steady_contradiction_rechecks_at_bounded_cadence |
| test_review_round5::test_R4F8_refused_take_profit_that_crossed_the_stop_pages_p0 | SAFETY_ORACLE_REVIEW | 仍待L7等价测试 / OLD_ORACLE_PLUS_PRODUCT | 旧失败是找本地止损下单被拦截P0而L7产生外部combo卖单P0；独立F7等价场景证明慢三源内TP120→STOP107仍减GTC，是真安全缺口; test_batch16_f7_price_flip.py四参数红测，要求最新quote重新判决且不减GTC/发TP |
| test_review_round6::test_R5F2_old_order_without_a_position_row_never_books_after_flat | LEDGER_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | 直接external_positions置flat后release遇工作GTC拒绝，首个open_positions空断言失败，后续旧订单隔离前提未建立; 精确取消并核实GTC及账户执行后release，再测旧未建position意图迟到fill隔离 |
| test_review_round6::test_R5F3_batch4_state_release_record_alone_is_a_flat_point | LEDGER_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | 同样未撤GTC，release记录未成功建立flat_point，删除flat_verified后迟到fill不应被当作释放后证据; 先完成L7核证release并证明flat_point，再重现旧版本记录缺flat标记 |
| test_review_round6::test_R5F4_evidence_only_fill_never_enters_day_pnl | FIXTURE_ORDER_ID | 已覆盖（L7专项等价测试） | test_batch16_f6_release::test_B16_F6_release_refuses_working_GTC_then_accepts_verified_cancelled_flat |
| test_review_round6::test_SR6_A2_stale_position_stream_never_silences_a_stop | LEGACY_ORDER_FLOW | 仍待L7等价测试 | 旧路径断言与 GTC+DAY TP 共存语义不等价，尚无逐节点一对一新断言 |
| test_review_round6::test_SR6_stop_sells_everything_still_open_even_if_its_target_is_stale | FIXTURE_ORDER_ID | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_review_round7::test_P2a_earlier_versions_evidence_rows_of_released_positions_refuse_the_state | RELEASE_EVIDENCE_REVIEW | 仍待L7等价测试 / OLD_FIXTURE | rig_released fixture直接设broker flat但GTC仍工作，setup要求RELEASED即失败，未执行历史证据行场景; 建已核实释放的L7 fixture后再插入旧版错误证据行 |
| test_review_round7::test_P2a_new_fill_of_a_released_positions_order_is_quarantined | RELEASE_EVIDENCE_REVIEW | 已覆盖（L7专项等价测试） | test_batch16_f6_release::test_B16_F6_release_refuses_working_GTC_then_accepts_verified_cancelled_flat |
| test_review_round8_selfreview::test_B3_sell_is_priced_from_the_trade_when_the_book_is_stale | FIXTURE_ORDER_ID | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |
| test_review_round8_selfreview::test_B4_sell_uses_the_bid_of_a_fresh_book | FIXTURE_ORDER_ID | 环境/夹具前提 | 旧测试夹具的 clientId/orderId/保护开关前提与 L7 运行规则不符；旧断言不能直接作为产品回归证据 |

Six old-order-flow failures need explicit L7 equivalents rather than a blanket expected-failure label: `test_review_round2::test_R8_stop_fill_during_scan_latches_before_the_next_entry` (loss latch during scan), `test_review_round3::test_Q7_queued_exit_work_is_inside_the_protection_budget` (two-position scheduling), `test_review_round3::test_SR3_blocked_exit_cannot_livelock_the_tick` (external SELL and the other symbol), `test_review_round4_selfreview::test_R5_difference_found_at_the_exit_boundary_blocks_entries_at_once` (same-tick cross-symbol BUY gate), `test_review_round4::test_F7_exit_is_re_decided_with_the_latest_quote_before_sending` (new protected F7 repro fixed), and `test_review_round6::test_SR6_A2_stale_position_stream_never_silences_a_stop` (broker-side stop alert with stale stream). F7 is covered by its new protected test; the other listed L7 equivalents remain coverage debt until separately tested.

## FakeBroker rendered card samples

# Batch16 FakeBroker + MemoryChannel rendered cards

Template scenarios are synthetic fixtures; no Gateway behavior is claimed.

### gap A warning

🟠 P1 · 入场后保护单尚未确认
发生了什么：AAPL 持仓数量=20 股 保护单已确认=0 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–20） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-c5303f171e294c5480e8 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=未获券商确认 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker synthetic evidence; no Gateway connection
影响：保护确认仍未到达，未保护 待核实（可能范围 0–20） 股；从空窗起点累计 15 秒才升级 P0。
你要做什么：1）在 TWS 核对保护单 orderId 与剩余量；2）不要新开仓或另发卖单；3）从空窗起点累计 15 秒仍无券商确认，再按 P0 处置。

### gap A overdue

🔴 P0 · 入场持仓未获券商保护
发生了什么：AAPL 持仓数量=20 股 保护单已确认=0 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–20） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-7d57df12bdfb4789bc1a orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=未获券商确认 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker synthetic evidence; no Gateway connection
影响：持仓存在裸仓风险，未保护 待核实（可能范围 0–20） 股；新开仓继续冻结。
你要做什么：1）在 TWS 核对持仓与保护单是否真实存在；2）不要另下卖单；3）由 John 处理裸仓并保持新开仓冻结。

### gap B overdue

🔴 P0 · 止盈后保护缺口未恢复
发生了什么：AAPL 持仓数量=20 股 保护单已确认=14 股 保护单可能=14 股 止盈在途=6 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–14） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-b105eda1777e4d72ab9f orderId=1002 permId=51002 TP=v6l-8d9d2472fbec458b803f TP orderId=1003 TP剩余=6 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:01:02+00:00 工作单证据时间=2026-10-06T14:01:02+00:00；FakeBroker synthetic evidence; no Gateway connection
影响：止盈流程尚未恢复保护，止盈在途 6 股；超时须核实撤单。
你要做什么：1）先撤销在途 TP 并核实撤单及成交；2）不要另下卖单；3）再核对并恢复同标的保护单剩余量。

### preflight B overdue

🔴 P0 · 接管前止盈超时，卖单待人工核对
发生了什么：AAPL 持仓数量=20 股 保护单已确认=14 股 保护单可能=14 股 止盈在途=6 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–14） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-dcb71088e36c4f03bb72 orderId=1002 permId=51002 TP=v6l-e8cd5b70c57b458b8199 TP orderId=1003 TP剩余=6 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:01:02+00:00 工作单证据时间=2026-10-06T14:01:02+00:00；FakeBroker synthetic evidence; no Gateway connection
影响：接管前连接和订单身份未验证，不能把超时 TP 当成已撤单。
你要做什么：1）如果 Lite 在 5 分钟内没有恢复连接，在 TWS 手动撤销 TP 单 1003 并核对成交；2）不要另下卖单；3）接管后先核实 TP 已撤，再恢复保护单。

### external sell

🔴 P0 · 托管标的出现外部卖单
发生了什么：AAPL 持仓数量=20 股 保护单已确认=19 股 保护单可能=19 股 止盈在途=0 股 外部卖单在途=1 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-7eec015072604e74a565 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=foreign-card-sell orderId=1003 1 股；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker synthetic evidence; no Gateway connection
影响：外部卖单与 Lite 卖单可能叠加，可能超卖 0 股。
你要做什么：1）先在 TWS 核对外部卖单来源和成交：不需要就撤掉，需要就保留；2）保留时不要再下任何卖单，Lite 已冻结该标的，持仓未平前不再做止盈，原保护单仍可能在途；3）平仓并核实无工作单后，由 John 对账并执行 release AAPL。

### rejected

🔴 P0 · 券商拒绝保护单
发生了什么：AAPL 持仓数量=20 股 保护单已确认=0 股 保护单可能=0 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 20–20） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-d9d82f64c9494270b905 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker synthetic evidence; no Gateway connection
影响：被拒的保护请求无效，未保护 待核实（可能范围 20–20） 股不能据此当作已覆盖。
你要做什么：1）在 TWS 核对保护单拒绝原因及真实持仓；2）不要另下卖单或假定旧保护仍有效；3）由 John 处置未保护股数并重新对账。

### unknown

🔴 P0 · 保护单状态无法核实
发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–20） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-b63590cc03a74656abae orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker synthetic evidence; no Gateway connection
影响：保护订单身份或状态未知，未保护 待核实（可能范围 0–20） 股仍须券商核实。
你要做什么：1）在 TWS 用 orderRef、orderId、permId 核对真实订单；2）不要另下卖单或加量；3）取得持仓、工作单、成交三项证据后再决定。

### trigger crossing

🟠 P1 · 价格已到止损价，券商保护单应在执行
发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-4521327c792a46eeb393 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；现价=108.47，已到止损触发价=108.47；保护单剩余=20 股，券商成交尚待核实
影响：止损价格已触发本地监测；券商是否已触发及成交仍须核对。
你要做什么：1）在 TWS 观察同一保护单的成交及剩余量；2）不要另下卖单；3）若价格低于限价或累计 60 秒未减量，按 P0 立即处置。

### trigger unfilled

🔴 P0 · 止损触发但限价未成交
发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-2c306424052946f2a2e2 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；现价=105.20 低于保护限价=105.21；保护单剩余=20 股
影响：止损限价可能已触发但未成交，持仓仍可能暴露于继续下跌。
你要做什么：1）在 TWS 核对本卡 orderId 的保护单剩余量及触发状态；2）可等价格回到限价以上，或在 TWS 直接修改同一 orderId 的限价并核实回报；3）不要另下一张卖单，直到持仓与订单重新对账。

### trigger suspected

🟠 P1 · 疑似触发或保护状态不明
发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-41a5b642500c475e866a orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker synthetic evidence; no Gateway connection
影响：疑似触发止损，但现有证据不足以认定券商已执行。
你要做什么：1）在 TWS 核对价格、保护单和成交；2）不要另下卖单；3）确认券商状态后由 John 处置。

### invalid price

🔴 P0 · 成交后无法建立有效保护价
发生了什么：AAPL 持仓数量=20 股 保护单已确认=0 股 保护单可能=0 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 20–20） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-1a12149aa4eb4f5dbf3d orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker synthetic evidence; no Gateway connection
影响：成交后保护价格无效，现有持仓不能凭该价格获得保护。
你要做什么：1）在 TWS 核对真实持仓和可用保护价格；2）不要另下卖单；3）由 John 人工处理并维持新开仓冻结。

### invalid entry price

🔴 P0 · 无法计算保护价，买单已拦截
发生了什么：SPY 持仓数量=0 股 保护单已确认=0 股 保护单可能=0 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–0） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=- orderId=- permId=- TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=0.00 限价=0.00；上次确认=未获券商确认 工作单证据时间=2026-10-06T14:00:31+00:00；买单已拦截，申请买入=1 股，买入限价=500.00，ATR=None；原因=missing ATR or valid plan
影响：保护价格无法建立，买单已拦截且本次不开仓。
你要做什么：1）确认买单已被 Lite 拦截且没有券商订单；2）不要绕过门控下单；3）修复 ATR 或价格证据后重新申请。

### evidence incomplete

🔴 P0 · 保护单三项证据不完整
发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–20） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-3dd7819a663d4c9e9727 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker synthetic evidence; no Gateway connection
影响：持仓、工作单和成交三项证据不完整，不能安全增加保护数量。
你要做什么：1）在 TWS 分别核对持仓、所有工作单、成交；2）不要另下卖单或增加保护数量；3）证据齐全且一致后再解除冻结。

### shutdown incomplete

🔴 P0 · 停机时保护单未核实
发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–20） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-ac88607e327540b4a1c8 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker synthetic evidence; no Gateway connection
影响：停机时保护单存在性未核实；重启前不能把旧快照当成仍在途。
你要做什么：1）在 TWS 确认 GTC 保护单是否保留及其剩余量；2）不要另下卖单；3）重启后完成三源核对，异常时由 John 人工处理。

### late tp fill — request pending

🔴 P0 · 止盈迟到成交，保护单减量待券商确认
发生了什么：AAPL 持仓数量=19 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=5 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–14） 股 可能超卖=待核实（可能上界6） 股；当前保护状态待核实；orderRef=v6l-protect-1378fc94aeb941e08b0e orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:01:12+00:00 工作单证据时间=2026-10-06T14:01:12+00:00；迟到止盈成交后持仓=19 股，旧保护可能最多=20 股，其他在途卖单最多=5 股；不要另下卖单；同一保护单的减量请求已提交，券商确认待核实
影响：迟到止盈成交可能使保护单超额，可能超卖 待核实（可能上界6） 股；减量待券商确认。
你要做什么：1）在 TWS 核对迟到 TP 成交及当前保护单剩余量；2）不要另下卖单；3）确认 Lite 已只减保护数量并重新对账。

### late tp fill — broker confirmed

🔴 P0 · 止盈迟到成交，保护单已减量
发生了什么：AAPL 持仓数量=19 股 保护单已确认=14 股 保护单可能=14 股 止盈在途=5 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-1378fc94aeb941e08b0e orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:01:12+00:00 工作单证据时间=2026-10-06T14:01:12+00:00；同代订单状态和新鲜三项证据已确认同一保护单剩余=14 股；迟到止盈成交后的保护数量已获券商确认
影响：迟到止盈成交后的保护数量已获券商确认；当前可能超卖 0 股，仍须核对其余卖单。
你要做什么：1）在 TWS 核对迟到 TP 成交及当前保护单剩余量；2）不要另下卖单；3）确认 Lite 已只减保护数量并重新对账。

### callback failure

🔴 P0 · 成交回调处理失败，券商端保护未核实
发生了什么：AAPL orderRef=v6l-928d496fe5564da085ac；成交回调本地处理失败 OSError；持仓数量、保护单剩余、止盈在途、外部卖单、未保护数量均待核实；保护单 orderId=待核实。
影响：持仓可能没有券商端保护；停止新开仓。
你要做什么：1）在 TWS 核对真实持仓、成交和卖单；2）不要新开仓，由 John 人工处置保护缺口；3）修复后需要重启 Lite，才能恢复开仓；重启时先完成三项对账。

### direct_cancel

🔴 P0 · 未认领订单禁止泛型撤单
发生了什么：AAPL orderRef=v6l-orphan-card orderId=1003 permId=51003；持仓数量、保护单剩余、止盈在途、外部卖单、未保护数量均待核实；本地没有对应订单记录，泛型撤单已拒绝。
影响：可能是仍在券商端工作的 GTC 保护单。
你要做什么：1）在 TWS 核对订单号与持仓；2）保留未知 GTC；3）由 John 决定人工处置并重新对账。

### shutdown

🔴 P0 · 停机发现未认领券商保护单
发生了什么：AAPL orderRef=v6l-orphan-card orderId=1003 permId=51003 保护单剩余=1 股；持仓数量、止盈在途、外部卖单、未保护数量均待核实；本地没有对应保护记录，停机保留该券商卖单。
影响：保护订单归属和股数需人工核实。
你要做什么：1）在 TWS 核对真实持仓与订单号；2）不要由 Lite 自动撤该单；3）由 John 决定处置并重新对账。

## Final code and SHA-256 manifest

`code_sha=0f03a5d90c1ccad0`. This 75-item manifest covers every package file plus the Batch16 instruction and G3 runbook; the report itself is excluded to avoid a self-reference and is reported separately. Git status before the report is separately scoped to task-owned paths; none staged or committed.

```text
007750716250c799a67b318ea14086e210a3cb9d080f9894477fcd80a0763612  docs/V6_LITE_BATCH16_INSTRUCTIONS.md
dbffe447d75624996c2981161acb283a4093fad600ad2fdd1e801372a4d7c3bd  docs/V6_LITE_G3_RUNBOOK_DRAFT.md
17b8628794cf5cf5e592be52009a9dbf07ff369673074d69c36b4a91103f2cec  v6lite/README.md
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
8bd77a8d308d6f3e66995bb37be39fc5debdd964cf1395ca68cced32849ffab9  v6lite/__main__.py
ee5552b23fcfa8a7438e4f1c5615fac38e6cf90bc84b863032e59b1654b3ff05  v6lite/alerts.py
ad2ac3f67724896e2b21e83e595a10b7984630fd2a77f8df44aa859569cb945b  v6lite/approval.py
2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24  v6lite/broker/__init__.py
12bd8e7a0a4626c05e6e64123e90de062a47fe4a4d7bb5902972299bca67226c  v6lite/broker/base.py
bce0c6984d60d8aa2fbb075a0110ede72be9f546f95667b1613d07f0a358c1a5  v6lite/broker/fake.py
9c7cab2ee09a3343377f7e46f1be35dfe20caaf2231e015e0bd7aa305a569faf  v6lite/broker/ibkr.py
0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1  v6lite/clock.py
b68128969b2ed7dc227d45fbafe116f122b0a1d230e8d057ef92ac61fe2e4d7d  v6lite/config.py
5ed23b163af3fcaead5545740130cea0b8e0253e5141a82a8ed428769d7c03da  v6lite/engine.py
47e605b64d9b148a38dcc2697b3266ced4494f501b980987d6917424fed7bd22  v6lite/examples/live.toml
8d0836ae45356ed3650c33f3c3390a304ea5e073a5f0c0ca4ebc81542409e4a1  v6lite/examples/paper.toml
258febbcf40a112fae652f95b0e0f77b8f0151eb97101da4623c40ee75e8fc49  v6lite/examples/paper_delayed.toml
507dea6df5c4c36a58db1bdbda8921707a3fab9721b504be98abce72b26b23d1  v6lite/examples/paper_test.toml
1de7eb5637ee3c6abd1784570846d0edf8f91739ee97e2bb6e0b6651e34de093  v6lite/exits.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
25937bff88ac9c7aeeb5b354d2edb6fc187f4a9ce2e1736a1596537d4a3f2506  v6lite/models.py
3de59ef71678bf534a66d48f4a3e4b5f49b7bac5a391f1a051cf2e0e648474e5  v6lite/protection.py
4a7a2b807b025b774386d533bf82d276b0351f7cc7738cc8245d1e0c4d1042c5  v6lite/protection_engine.py
4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1  v6lite/reconcile.py
e6d9fe60d94229849af5b78dfe9d33bea8a8573a550e680a1d628fa5beebd523  v6lite/report.py
abc521f671e878a0ab5690e1fa178dc872d29b5396b86eaeb5f016be1594c687  v6lite/risk.py
c9b9cc4a116a7f61d9e660dd885d280ec2a9262671f2c3c8ab5c1515724e171e  v6lite/sizing.py
a47dd6e9c1a3ed59dcff0dcc0b62ba87d27dc6cae35e0359b2c54c70d662a2ce  v6lite/store.py
9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e  v6lite/strategy.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  v6lite/tests/__init__.py
aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb  v6lite/tests/conftest.py
0c28d019b52b61f5d0b477bd7b202c409ff6761a4b4830b70c77667a3d9787d6  v6lite/tests/helpers.py
5b875c8df97861e22427223e7065a74bddf04a5da1c7115b431871369d177c47  v6lite/tests/test_batch10.py
fb7f26a0bd1969400f2d769a0d65f4c7d3a18f0162332fd46ca9f1ed99c260f8  v6lite/tests/test_batch11.py
3dfc69a4f811549cb32c9c186bbfed3d24f7c1c956c3c743d9b28f6996037cfc  v6lite/tests/test_batch12.py
5691a234501cd71b85e3098e0cf6da2a74f2d4f5ca3eda2797a8abe17fab9af0  v6lite/tests/test_batch12_v2.py
12a3ed033079f129e7e70f610be004c498ea6f6f0283473f60b2eb80ad819140  v6lite/tests/test_batch13.py
5087c53709f4208d5dc4fbe9f74b100d50ebfb2060ccf9c9834358d2c3c7b257  v6lite/tests/test_batch14.py
b13bf6c617ebb288140b9747a868b791dc8bd330f3f8b130ff73b6ac5a5eb3fa  v6lite/tests/test_batch15.py
02bc3283c395ec7a6903e94fe9a024ce9b40e9da81c28bc10cccc3bce67fcdd3  v6lite/tests/test_batch16.py
d9c3c8f080748838a81392b1a12ca03347685a1be4692ae9159471ff2cc56a51  v6lite/tests/test_batch16_card_deadline_copy.py
52f746366b2b14dd7247533157c715060540b8fa45170c2af8b39f5f8dbf9a64  v6lite/tests/test_batch16_f5_copy.py
6d97cec645009fd6d90c223cfeb5c902866701f75c01f91a604caddd3a0e863d  v6lite/tests/test_batch16_f6_account_evidence.py
86c542de449c2adab7cae4d2bc476e5108ef7ef14d9c2f45686a2404a3b9d478  v6lite/tests/test_batch16_f6_global_mismatch.py
15c23d799539945057095f56bad2e8ba8a32974383eb8d778fba0f57ede2cb96  v6lite/tests/test_batch16_f6_release.py
9e2464cc21430b8b4e27dd8982b1a5aee0b554a5e0698691df3156715ad65b9c  v6lite/tests/test_batch16_f6_safety.py
e7ae2a84c08b2adadb1aa9994e835b5504db581401eecbf2bfd14678e984915d  v6lite/tests/test_batch16_f6_steady_fairness.py
733347eda5814cbd2a14f71a717c88274bdd51376cada2c9bceeab3fd2908c48  v6lite/tests/test_batch16_f6_steady_timeout.py
b2f9cf68a543999d08ab0567e6e6a5663f257d2525c48430f953de8bacfbc5dd  v6lite/tests/test_batch16_f7_price_flip.py
af5c1b6c62872c84536b222255c9a3b4229e2d7bf4ce1c4b55b1d4a780aa3539  v6lite/tests/test_batch16_f8_backoff.py
328269e2c8fa8a0547d1d16a5904a4d6cc5462f13b093b9e6c09eb87bd4be8ba  v6lite/tests/test_batch16_gap_evidence.py
bb8967a536a8d187fc9f1d742be17c0b4b90e193ab52731ac06d6bff8a08f461  v6lite/tests/test_engine.py
cacaf5b3115d4a298fe8b3fe065d66fe70277b8ee4cd5d9643f0e2fff0ff5f9d  v6lite/tests/test_g2_day1.py
fe84ef645d99f421c64f670aa7f883d16b5a24c8c15b4dd30a4aa8f035b5ea06  v6lite/tests/test_g2_day1_selfreview.py
a0db6b981992a3cf20d9f959a679c608842f4619007331bae9b26908cee3ccb7  v6lite/tests/test_g2_day2_prep.py
ba38215d7a6946d60884d0e3dac99b641e7bcde058c4893b94cb9d62b6a6fe18  v6lite/tests/test_g2_probe.py
6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a  v6lite/tests/test_ibkr_adapter.py
eb91ef957bfd13864c2c92a5da2cf3a29ffdc10c9f5c74fe751daa8f4caecdd3  v6lite/tests/test_review_fixes.py
7ab32142a9a0ee2fdb54853297cf6eae619ce28de4cac4dc646a8a8035660822  v6lite/tests/test_review_round2.py
b5bf4e5c513e9d2310dc6404f147b633790cff0e894546e9176d6c26390a7427  v6lite/tests/test_review_round3.py
04cf584aa3b7e13842c1e7608d03e5f888b7a714ea073001ce85281fe1eb43cb  v6lite/tests/test_review_round4.py
8cf2bba5d54e2e644bc016e68abfe87f0ecbe26b6e54d5a44a329c61e6dc7da8  v6lite/tests/test_review_round4_selfreview.py
c45a477f2bb20f021b3d8040b3be6360d78bb8c95f0ef7ffd2ba069fdf31eeb0  v6lite/tests/test_review_round5.py
03adaac71b3ea18095732064c51f6f4741400adae738c3cfb9f840c247ec4a8e  v6lite/tests/test_review_round5_selfreview.py
c6eabe6e953cc75ccfa7e4fa87625a86d677ef5dd2fca9975cd101b3403d6be5  v6lite/tests/test_review_round6.py
bcb2baea638b6ba6c063aba7776aaf1137c3cd1df98297a08311cdaf899994a5  v6lite/tests/test_review_round7.py
a3589fae5996245f7246f1860875a847b6761ed9f8bab5a317ab5c7d9e5e86bf  v6lite/tests/test_review_round7_selfreview.py
c65ac1c396468aae3124ffcdc0f61b2ed483547ec1b0f47119f00950008b3736  v6lite/tests/test_review_round8.py
e927860575cb55769a910a0dfbced96b725922da6629010c894ec2d3c86b36c3  v6lite/tests/test_review_round8_selfreview.py
abb1c1b1742d30353157518676a980ea162902178945701f67aeb69b82f4cd1c  v6lite/tests/test_soak.py
732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f  v6lite/tests/test_strategy_parity.py
75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42  v6lite/tests/test_units.py
91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1  v6lite/tools/__init__.py
e756a719f9f8c1cded3d1123f67fa0097b504680589319be40619bc5d7f6763e  v6lite/tools/g2_probe.py
9aa52b23a3c8c08801f9d86c46ae1090a28c78dbde5d5b4b2923347bc74ece45  v6lite/version.py
```
