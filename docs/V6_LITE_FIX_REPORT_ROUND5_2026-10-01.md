# V6-Lite 第五批整改报告（针对 Codex 第四轮复核）

日期：2026-10-01 PT。整改：Claude。待复核：Codex。
依据：`docs/V6_LITE_CODEX_REVIEW_ROUND4_2026-09-30.md`（R4-F1–R4-F8，及第 4–6 节的逐行裁定）；John 的既有决定：“P2 也修”，以及第四批的“砍范围”（只保留 freeze → 人工平仓 → release）。

**结论先行：**
- 2 个 P1 和 6 个 P2 全部按复核建议修复。范围仍是 freeze → 人工平仓 → release，没有加回任何恢复能力。
- 交付前又做了一轮对抗性自审（第 5 节），发现 1 个 P1、4 个 P2，均已修复并固化为测试。其中 P1 是本批第一版自己引入的回归。
- 状态格式仍为 schema 4，第四批创建的状态可以继续使用。
- 全量离线测试 **228 passed**，连续 3 次结果一致。

本报告**不宣称** G3 或 Live 可放行。所有结论只基于 FakeBroker、未连接的真实 ib_insync 对象和临时数据库。整改期间没有连接 Gateway、下单或签发批准，没有读取 `.env` 或 `state/`，没有 stage/commit/push，也没有改动旧 V6、Codex 的复核文件或开发计划。

## 1 复现命令

```powershell
cd C:\Dev\TradingV6
. .\venv_v6\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
$env:PYTHONPYCACHEPREFIX = Join-Path ([System.IO.Path]::GetTempPath()) ('v6lite-r5-' + [guid]::NewGuid().ToString('N'))
python -m pytest v6lite/tests -q -ra                                        # 期望 228 passed
python -m pytest v6lite/tests/test_review_round5.py -q -ra                  # R4-F1–F8，13 项
python -m pytest v6lite/tests/test_review_round5_selfreview.py -q -ra       # 交付前自审，7 项
```

## 2 结果总表

| 测试集 | 第四批代码 | 本批 |
|---|---|---|
| `test_review_round5.py`（13 项） | **13 failed**，均为复核所述的错误行为：<br>• 超时后旧 End 让新查询返回 `[]`<br>• 同一 tick 发出 SPY BUY 20<br>• release 后迟到 BUY 生成 OPEN 持仓<br>• CLOSED 冻结无法解除<br>• P0 不列 held/孤立订单<br>• 未知 conId 的 BAG 被忽略<br>• held 冻结 60 tick 查询 60 次<br>• 拒单只报 `P1 exit blocked AAPL TP1` | 13 passed |
| `test_review_round5_selfreview.py`（7 项） | S5-1 至 S5-3 在本批第一版上失败；S5-4、S5-5 由自审代理用真实 ib_insync 对象复现 | 7 passed |
| v6lite 全量 | 208 | **228 passed** ×3，每次约 28 秒 |
| 改动的既有测试 | — | `test_review_round4_selfreview.py`：冻结 P0 文案已改，`test_freeze_withdraws_our_own_working_orders` 的断言相应从 `Cancel requests were sent` 改为小写 `cancel requests were sent`。没有删除任何测试 |

## 3 逐项：发现 → 改动 → 测试

| 项 | 改动 | 测试 |
|---|---|---|
| **R4-F1 / P1** 超时请求迟到的 End 让新查询返回假空列表，导致错误 release | **原因：** ib_insync 的 `reqPositions` 和 `reqAllOpenOrders` 使用固定请求键，无法区分不同次请求的回报。<br>**改动：** 适配器在真实 `IB.client` 上记录这两个通道各发出了几次请求，在 `wrapper` 上记录收到了几次 End。<br>**① 先排空再发送。** 若之前还有请求没有收到 End，先等它到达（最多 2 秒）再发新请求。socket 是有序的，排空之后，旧请求的任何消息都不可能出现在新请求里面。等不到就抛出“证据不可用”，绝不返回 `[]`。<br>**② 只采用自己的结果。** 只有当新请求正好由它自己的 End 完成，结果才被采用。<br>**③ 卡住就重连。** End 拖欠超过 60 秒，或出现多余的 End，就主动断开，由引擎按原有流程重连；新 socket 的计数从 0 开始。<br>**④ 其他加固：**<br>• `cancel()` 查找订单时也走这条受保护的通道；<br>• 持仓列表按 conId 去重，后到的覆盖先到的（下载期间推送的更新原本会被重复计数）；<br>• release 另外要求持仓推送缓存也显示 0。<br>**预算：** 查询类最坏耗时改为 2 + 15 + 2 = 19 秒，因此最小检查周期为 22 秒，默认 30 秒满足 | 用真实 ib_insync 的 `IB`/`Wrapper`/Future 和一个按 FIFO 回放的假服务端：<br>• `test_R4F1_late_end_of_a_timed_out_request_never_completes_the_next_one`：第四批返回 `[]`，现在返回 AAPL 20<br>• `..._owed_end_that_never_comes_is_evidence_unavailable_not_empty`<br>• `..._open_orders_channel_is_guarded_too`<br>• `..._a_channel_stuck_too_long_renews_the_connection`<br>• `..._streamed_update_inside_a_request_does_not_double_count`<br>• `..._release_needs_the_position_stream_to_agree` |
| **R4-F2 / P1** 对账用较旧的结果覆盖刚发现的数量疑点，同一 tick 放行 BUY | `quantity_suspect` 改为“标的 → 发现时的 fresh 持仓读取序号”。对账只清除早于自己那次持仓读取的疑点。较新的疑点会：<br>• 保留下来；<br>• 让 `reconcile_ok=False`；<br>• 写进 detail；<br>• 加入 `_mismatched`；<br>• 安排下一次对账 | `test_R4F2_difference_seen_during_the_reconcile_is_not_cleared_by_it`：对账读取持仓（20）后时钟前进 15 秒、外部卖出使持仓变为 18，查询订单时的保护检查看到 18。第四批在同一 tick 发出 SPY BUY 20，现在不发 |
| **R4-F3 / P2** release 后迟到的 BUY 仍会重建持仓 | 持仓为 RELEASED 时，ENTRY 成交只保留为证据（不计入当日盈亏），并发 `late_fill_after_release` 异常，不新建、也不并入任何持仓。<br>自审 S5-2 进一步推广为：订单的**创建时间**早于该标的最近一次“已证实为平”（release 或冻结解除时 broker 为 0 股且没有 working 订单）的时间时，其迟到的买入成交一律只作证据。<br>只比较本地时间，不使用服务端成交时间，以免受时钟偏差影响 | `test_R4F3_late_buy_after_release_never_recreates_a_position`；S5-2 两项 |
| **R4-F4 / P2** 冻结后旧成交已经把持仓正常平掉，release 却永远无法解除冻结 | release 遇到“冻结中、但已没有 OPEN 持仓”的标的时，执行同样的证据检查（broker 0 股、持仓推送为 0、没有 working 订单、账本没有在途订单、60 秒内没有成交），然后调用 `Store.resolve_freeze`，在**一个事务**里完成：<br>• 清除冻结和异常；<br>• 关闭该标的剩余的 active 意图；<br>• 记录“已证实为平”的时间；<br>• 锁定当天日停。<br>该笔交易按已入账的结果计入连亏，**不写 release 记录，因此不会重复计亏损**；日报和审计中记为 `FREEZE_RESOLVED` | `test_R4F4_frozen_position_closed_by_its_own_fills_can_be_released`：冻结已解除，持仓仍为 CLOSED，`release_times()==[]`，日停为 1 |
| **R4-F5 / P2** 冻结交接没有列出仍在 working 的 held 或孤立订单 | 冻结时：<br>• 重新查询 working 订单；<br>• 对本系统的非挂起意图请求撤单（与之前相同）；<br>• P0 中逐条列出其他仍可能成交的订单（挂起的、账本不认识的 `v6l-` 单、人工单、组合单），并注明 **NOT cancelled**；<br>• 要求先确认该标的没有任何 working 订单，**然后**再平仓。<br>查询失败时，P0 要求在 TWS 中逐一核对该标的的所有订单。<br>按 D3″，这些订单仍不会被自动撤销。README 已同步 | `test_R4F5_freeze_lists_held_and_orphan_orders_that_were_not_cancelled` |
| **R4-F6 / P2** 非空 BAG 中未知 conId 的腿被直接忽略 | 本地不认识的 conId 不能当作“不是股票”的证据，改为产生 `*` 订单（阻断全部 SELL 和 release） | `test_R4F6_combo_leg_with_an_unknown_conid_fails_closed` |
| **R4-F7 / P2** 冻结标的有 held 意图时，每个 tick 都查询持仓和订单 | 判断“是否有在途订单”时只计非挂起的意图；held 意图是在等操作员处理，不再触发每个 tick 的强制对账 | `test_R4F7_frozen_symbol_with_a_held_intent_is_not_reconciled_every_tick`：60 tick 内查询 ≤ 2 次 |
| **R4-F8 / P2** 取证失败而拒发时，仍按排队时的 TP 目的报警，漏掉查询期间已触发的 STOP | 拒绝 SELL 时用最新有效报价重新判断：若已触发止损，告警升为 **P0** 并标为 STOP。此判断是**纯计算**：不启动、不放弃、不调整任何阶段的数量（见自审 S5-1）；证据完整后的下一轮判断照常启动 STOP。发送仍然失败关闭 | `test_R4F8_refused_take_profit_that_crossed_the_stop_pages_p0`：第四批为 `P1 exit blocked AAPL TP1`，现在为 P0 STOP |

## 4 对复核第 2 节三个条件的回应

| 条件 | 现状 |
|---|---|
| 本次零持仓和无订单的证据必须完整可信 | 固定键通道只采用由自己的 End 完成的结果；之前有请求没收到 End 时不发新请求；出现多余的 End 时重连。release 还要求持仓推送缓存也为 0。剩余风险见第 6 节第 1 条 |
| release 后任何旧成交都不能重新激活已释放的持仓 | 原持仓、恢复持仓为 RELEASED，或订单创建早于“已证实为平”的时间，这三种情况下迟到的 BUY 都只作证据并发异常。迟到的 SELL 本来就只发 `exit_after_close` 异常 |
| 原持仓已被迟到成交完整关闭时，也必须有合法的解除冻结路径 | `release` → `resolve_freeze`，证据条件相同，在一个事务内完成，不重复计亏损 |

## 5 交付前自审（SR5）

第一版完成后，另起一个只修改临时测试的审查代理，对照第四批代码做了对抗性审查：

| 编号 | 严重度 | 问题 | 处理 / 测试 |
|---|---|---|---|
| S5-1 | P1（本批第一版引入的回归） | R4-F8 的第一版在拒绝时调用 `_redecide`，直接启动 STOP 阶段，目标数量取自刚被证明有误的账本（20，broker 实际 30）。迟到的 10 股入账后，STOP 只卖 20 股，阶段即标为 DONE，剩下 10 股停在止损线下且不再告警 | 风险判断改为纯计算，不改变任何阶段。`test_refused_sell_does_not_size_a_stop_from_a_wrong_ledger`：不再创建阶段，P0 正确；最终 30 股全部卖出 |
| S5-2 | P2 | R4-F3 第一版只检查意图原来对应的持仓：已 release 的恢复持仓、已解除的冻结，仍可能被迟到 BUY 重建为新持仓 | 改为“订单创建时间早于已证实为平的时间”这一规则。两项测试 |
| S5-3 | P2 | `cancel()` 查找订单时的 `reqAllOpenOrders` 没有走受保护的通道，旧 End 会让它误判“没有 working 订单”而不发撤单 | 改走受保护的通道。`test_cancel_lookup_uses_the_guarded_channel` |
| S5-4 | P2 | 多余的 End 被截断后不再处理，通道仍被当作干净 | 视为通道已污染：下一次请求直接断开重连。`test_unsolicited_end_poisons_the_channel_and_renews_the_connection` |
| S5-5 | P2 | 请求在发出之前就失败（socket 已断）时仍被计为欠一个 End，导致之后每次都白等 2 秒 | 发送成功后才计数。`test_a_request_that_never_left_owes_no_end`。另外：`reqAutoOpenOrders` 不会有 End 回报，已从计数范围中移除 |

## 6 剩余风险（不在本批修改范围）

1. **多余的 End 先于请求自己的 End 到达。** 若某个请求是被一个多余的 End 提前完成的，返回的那一刻无法分辨。下一次请求时会发现并重连，但已经返回的那份结果可能已被使用。
   - 后果有界：持仓变少会触发“数量不一致”，从而失败关闭；release 另外要求持仓推送缓存为 0。
   - 订单列表变少会少预留股数，这一路径没有第二个证据源。
   - IBKR 文档没有说明会发送多余的 End。G2 日志若出现 `unsolicited ... End`，必须交回。
2. **单次 End 丢失。** 在重连之前（最长约 60 秒，加上 30 秒重连间隔）会拒绝全部 SELL，包括 STOP，期间发 P0。这是失败关闭的代价，需要 G2 记录实际是否发生。
3. **组合单。** 只要账户里有任何 working 的组合单（BAG），就会阻断全部 STOP 和 release。本系统不交易组合单；人工下组合单需要先撤掉。
4. **订单与持仓不是原子快照。** 两次查询之间新出现的外来订单看不到，这一点沿用第四轮裁定。G3 操作规程应明确：V6-Lite 运行时，操作员不在同一账户的托管标的上下单。
5. **release 后入账的佣金。** 已 release 持仓上的成交佣金仍计入当日费用，会高估亏损，属于保守方向。

## 7 设计判断（更新）

| # | 判断 |
|---|---|
| D11″ | SELL 和 release 的证据必须来自本次查询，并且这次查询由它自己的 End 完成；通道不干净时视为证据不可用 |
| D13″ | BAG 中的未知 conId 视为未知，组合单一律阻断全部 SELL 和 release |
| D15′ | 冻结 P0 必须列出所有仍可能成交的订单，并规定先处理订单、后平仓；不确定的订单仍不自动撤销 |
| D18 | “已证实为平”（release / 冻结解除）是业务终点：在此之前创建的订单，其迟到的买入成交只作证据 |
| D19 | 冻结期间原持仓已被成交关闭时，用 `release` 解除冻结：日停，但不额外计亏损 |
| D20 | 拒单时的风险判断是纯计算：只决定告警等级，不改变阶段 |

## 8 SHA-256 清单（41 个文件，已排除 `__pycache__`）

相对第四批的增量：

| 文件 | 增 / 删行数 |
|---|---|
| broker/ibkr.py | +116 / −11 |
| engine.py | +114 / −23 |
| store.py | +56 / −3 |
| README.md | +6 / −6 |
| risk.py | +3 / −4 |
| report.py | +1 / −1 |

- 测试：新增 `test_review_round5.py`、`test_review_round5_selfreview.py`；`test_review_round4_selfreview.py` 改动 1 行。
- Schema：仍为 4，没有新增表或列；“已证实为平”的时间存放在 controls 表中。
- `docs/V6_LITE_G2_RUNBOOK.md`：`aa770b579eaba8d66e5ba0f74f4811ba0e0d72b9992acc8e06b6f1c4953c8bd7`

```
13949c82c4e91c52516c3d7de7ce970f30f4acf5fc9c121d0c0d8f8acf055c64  v6lite/README.md
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
152ac099b8fae21d738505f110315da400226aa246ec0a979b19e4d1a00986c1  v6lite/__main__.py
3d1f717dc462ab0c1e48821bbfe82d7232842a2ef63570741213180fe899a5b1  v6lite/alerts.py
162daa7d98a2362bc29521fcf65472c5590fb282da694b5011da934d8ffc26b5  v6lite/approval.py
2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24  v6lite/broker/__init__.py
722fab4b5992c737dfcfcb86c5b74c33ba633b3b3ee561eac3c4a289abe723e1  v6lite/broker/base.py
c9567ee2546ae30ee8841ff0d4214cd1d4903aa93241e3620d4c0298d5e1ba8a  v6lite/broker/fake.py
11ee49f23e028f5b6e944b07cc3be6d3bedf5b0d2eb76c23f3cc4f206df1519d  v6lite/broker/ibkr.py
0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1  v6lite/clock.py
e8e10582fbf82d3849bcca4662d039d910923528fc15a25da99155e3af9b00f4  v6lite/config.py
ec40efbe896ae1a98035dd1cb191e6d0aca1167c09a728b213633a3373d1c510  v6lite/engine.py
1032534d1665efed3143d97bed9f7525410772a49ee5894f2000d0bcdd2fd673  v6lite/examples/live.toml
9c8682f8fa30a91703a53994745e948bc778186688dfe843acc353a5d8df123b  v6lite/examples/paper.toml
c3b4a074f96c5a25789afb5fabf088ead3b1daddaaa1ee5477ec1399c77627e6  v6lite/exits.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
172ef940ec8e5b5959d379c5520ac0f7cfbdf44f4c930a20fd01cfd2ecacadfb  v6lite/models.py
4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1  v6lite/reconcile.py
9430125381538ca15495031ae5597e19c85e9f1375c89d0d0b2d80b3414db5f8  v6lite/report.py
79189a487e74bed38727cccda8b314ca656df0821b19717de9ecfcd779f653e4  v6lite/risk.py
c7b9000919a9d7672aead2eddab786f96064946cf6084cf6b19cf9351ae70e26  v6lite/sizing.py
862d2804bd4350ee6a19023295c1ed0d97d6d286708829f20b67c4b97b3fa2e5  v6lite/store.py
9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e  v6lite/strategy.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  v6lite/tests/__init__.py
aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb  v6lite/tests/conftest.py
fd6c76b9f1e7bd2ff59cd82b86453619f156f1c9c61885aab31f248f01f1f038  v6lite/tests/helpers.py
6445bac89adb5356c19d98e63e0bcd6061bba35f0f476f81269f0482037a59bf  v6lite/tests/test_engine.py
e70899902f5e8338d3b01755e1c747e0fb09322f32c283a2eac51a2f2d5ff86a  v6lite/tests/test_g2_probe.py
6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a  v6lite/tests/test_ibkr_adapter.py
02f0537a763c12ad6254173078e251ff833aae4ac148fbfc45687ed69d0ce45a  v6lite/tests/test_review_fixes.py
dd8d7b5d34db5054ac6eef7aab0ed97f16711d03634784832b300eeb468f4605  v6lite/tests/test_review_round2.py
903fca0b51ed8fee873fd5629105d248bb474de2d7aee1b9a3250adafaa6e7cc  v6lite/tests/test_review_round3.py
342c5a14b315ccec9b8da1eb6439e5a9c623f60f715df0853ed031c3ebc1892b  v6lite/tests/test_review_round4.py
fbf5d3a26b81fab54a74e728cf15154b1ce9d0f4984ad28932043c5574fee5f7  v6lite/tests/test_review_round4_selfreview.py
915df7596314581cdb6433f3df9eb026bd304eebca121f273b71ace49f165f9f  v6lite/tests/test_review_round5.py
59a80d968d4023d9c89331730ade220b3f063b48e7f5f519a262616f21e362d8  v6lite/tests/test_review_round5_selfreview.py
a5377a4f520e6c3d340cb40571f24eae1dd431be871ecfad241b0d0080424aef  v6lite/tests/test_soak.py
732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f  v6lite/tests/test_strategy_parity.py
75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42  v6lite/tests/test_units.py
91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1  v6lite/tools/__init__.py
0da843f4a9939b03f6075ff337bd5623e49c2615746358e27359eb52b1cc4ba5  v6lite/tools/g2_probe.py
```

## 9 仍未完成

1. **L7**（broker 端保护单）尚未实现，Live 前必须完成并验收。
2. **G2 须记录**：
   - 是否出现 `owes an End`、`unsolicited ... End`、`connection renewed`；
   - 12/12/19 秒耗时的实际分布；
   - BAG 订单的字段；
   - 账户就绪状态与资金数据的推送节奏；
   - 1101 之后能否恢复。
3. **G3 须注入验证**：
   - 冻结 → 处理列出的订单 → 人工平仓 → release（含 CLOSED 解除冻结的路径）的完整操作流程；
   - 撤单确认之后的迟到成交。
4. 60 秒冻结门槛、120 秒宽限、2 秒排空等待、60 秒重连门槛都是工程假设，待 G2 校准。

## 10 请 Codex 复核

1. 在 Windows 上复跑第 1 节命令，核对第 8 节清单。
2. 对第 3 节每一行和第 5 节逐项给出裁定。
3. 重点反证第 3 节 R4-F1 的通道协议，包括：connect 时自带的初始请求、只读连接、`reqOpenOrders` 与 `reqAllOpenOrders` 共用 End、断线与重连；以及第 4 节的三个条件。
4. 对 D11″、D13″、D15′、D18–D20 表态。
