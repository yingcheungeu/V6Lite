# V6-Lite 第六批整改报告（针对 Codex 第五轮复核）

日期：2026-10-01 PT。整改：Claude。待复核：Codex。
依据：`docs/V6_LITE_CODEX_REVIEW_ROUND5_2026-10-01.md`（R5-F1–R5-F6）。沿用 John 的既有决定：P2 也修；恢复范围只保留 freeze → 人工平仓 → release。

**结论先行：**
- R5-F1（P1）和 R5-F2 至 R5-F6（P2）全部处理：
  - R5-F5 按“保留已知腿局部预留”修正了文档；
  - R5-F6 同步了 runbook。
- 交付前对抗性自审又发现 3 个 P1 和 1 个 P2，均已修复并固化为测试。其中 2 个 P1 在第五批之前就已存在，本批一并修复。
- 全量离线测试 **238 passed**，连续 3 次结果一致。

本报告**不宣称** G3 或 Live 可放行。只使用 FakeBroker、未连接的真实 ib_insync 对象和临时数据库。没有连接 Gateway、下单或签发批准，没有读取 `.env` 或 `state/`，没有 stage/commit/push，也没有改动旧 V6、Codex 的复核文件或开发计划。

## 1 复现命令

```powershell
cd C:\Dev\TradingV6
. .\venv_v6\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
$env:PYTHONPYCACHEPREFIX = Join-Path ([System.IO.Path]::GetTempPath()) ('v6lite-r6-' + [guid]::NewGuid().ToString('N'))
python -m pytest v6lite/tests -q -ra                              # 期望 238 passed
python -m pytest v6lite/tests/test_review_round6.py -q -ra        # 本批 10 项
```

## 2 结果总表

| 测试集 | 第五批代码 | 本批 |
|---|---|---|
| `test_review_round6.py`（10 项：R5-F1–F5 共 5 项，自审 5 项） | **9 failed / 1 passed**。通过的是 R5-F5：它只固定已有的组合单策略，本批没有改这部分代码。<br>失败项的表现：<br>• 同一 tick 发出 SPY BUY 20<br>• 没有持仓行的旧订单新建 OPEN 持仓<br>• 第四批形态的状态把迟到成交入账<br>• 当日盈亏从 −131.40 变成 −33.40<br>• 自审 5 项均失败 | 10 passed |
| v6lite 全量 | 228 | **238 passed** ×3，每次约 30 秒 |
| 改动的既有测试（2 处，均为新行为下的期望更新，没有放宽安全断言） | — | • `test_review_fixes.py::test_L3_exit_waits_for_broker_truth_after_hidden_late_sell`：原断言“隐藏的迟到 SELL 之后不再发 SELL”。现在 STOP 不再被缓存差异静默；同一 tick 内先补记迟到成交，再按真实剩余 10 股发 STOP。断言改为“新发的 SELL 只能是 10 股”。<br>• `test_review_round5.py::test_R4F3_...`：异常代码统一后，也接受 `late_fill_before_flat` |

## 3 逐项：发现 → 改动 → 测试

| 项 | 改动 | 测试 |
|---|---|---|
| **R5-F1 / P1** fresh 数量差异被资格检查提前返回跳过，同一 tick 仍发 BUY | `_submit_exit` 拿到 fresh 证据后，**先**处理“fresh 持仓 ≠ 账本”：记录疑点、标记差异、安排对账并拒绝发送。之后才做证据不足的拒绝和 `_exit_still_wanted` 等资格检查（自审 A1 又把它挪到证据拒绝之前）。`_exit_still_wanted` 自己用缓存发现差异时，同样记录疑点并安排对账，不再只返回 False | `test_R5F1_fresh_difference_at_the_sell_boundary_blocks_the_same_tick_buy`；`test_SR6_A1_...` |
| **R5-F2 / P2** 没有持仓行的旧 ENTRY 绕过“已证实为平”规则 | 终点判断**提到 `record_fill` 里、任何入账之前**，并且适用于所有方向和用途的成交：订单创建时间不晚于该标的“已证实为平”的时间点时，这笔成交进入 `quarantined_fills`，并发 `late_fill_before_flat` 异常（阻断入场，安排 fresh 对账）。不写入 `fills`，不新建、增减或关闭任何持仓 | `test_R5F2_old_order_without_a_position_row_never_books_after_flat` |
| **R5-F3 / P2** 第四批 schema 4 的 release 记录没有被识别为终点 | 新增 `Store.flat_point(symbol)`，取以下两者中较晚的时间：控制项 `flat_verified:SYM`，以及 `releases` 表中该标的最晚的 release 时间（第四批已经写入）。因此第四批的状态无需迁移，也能得到终点 | `test_R5F3_batch4_state_release_record_alone_is_a_flat_point`：删除标记后，仅凭 release 记录仍正确隔离 |
| **R5-F4 / P2** “仅作证据”的成交仍进入 CLOSED 持仓的当日盈亏 | 由于在入账之前就隔离，这类成交不在 `fills` 中，也就不进入 lot、当日盈亏、费用或连亏。第五批已经写进 `fills` 的同类旧记录无法与真实成交区分，所以**启动时检测到即拒绝运行**（`legacy_evidence_fills`），要求归档状态；文件不会被改动。第五批只有演练状态，而演练状态不能 release，实际不会触发 | `test_R5F4_evidence_only_fill_never_enters_day_pnl`（前后盈亏相等）；`test_SR6_batch5_evidence_rows_make_the_state_unusable` |
| **R5-F5 / P2** 报告写“任何 BAG 阻断全账户”，代码按已知腿局部预留 | **保留现有实现，修正文档**：<br>• 已知股票腿在各自的标的上预留股数；<br>• 只要有任何一条腿无法识别，就生成 `*`，阻断全部 SELL 和 release。<br>理由：已知腿全部阻断会让与持仓无关的组合单也拦住 STOP，那本身就是一种风险。本批新增：`*` 存在期间同时阻断新开仓（自审 A3） | `test_R5F5_known_combo_legs_reserve_their_own_symbols_only`；`test_SR6_A3_...` |
| **R5-F6 / P2（文档）** runbook 预算和 G3 前提过期 | runbook 第 51 行区分两种耗时：<br>• 探针测的是原始单次请求，对照值为 query ≤ 17 秒；<br>• 适配器预算为 19 秒，多出的部分是最多 2 秒的排空等待，看日志中的 `owes an End`。<br>第 48 行和第 78 行的“第三批”改为当前口径；第 81 行把 G3 前提改为“当前所有复核问题关闭，并由 John 决定取消 Read-Only API”；附录指向第六批 | 静态核对 |

## 4 交付前自审（SR6）

另起一个审查代理，对照第五批代码做了对抗性审查。以下问题均已复现并修复：

| 编号 | 严重度 | 问题 | 处理 / 测试 |
|---|---|---|---|
| A1 | P1（R5-F1 修复留下的缺口） | 账户里有一个无法识别腿的组合单时，证据不足的拒绝发生在差异检查之前，所以已经读到的 fresh 差异被丢掉，同一 tick 仍会发 BUY | 只要 fresh 持仓已经读到，就先处理差异，再处理“证据不足”。`test_SR6_A1_fresh_difference_survives_an_evidence_refusal` |
| A3 | P1（第五批之前就存在） | 无法识别腿的组合单会阻断全部 SELL，但不阻断入场：系统可能新开一个已知无法保护的仓位 | `RuntimeFlags.unresolved_combos` 由每次最新的订单列表设置；只要它存在，`global_entry_blocks` 就阻断入场，第一次出现时发 P1。`test_SR6_A3_no_entry_while_an_unresolved_combo_blocks_every_sell` |
| A2 | P1（第五批之前就存在） | 持仓推送缓存卡在错误的值上时，判断轮和 `_exit_still_wanted` 会静默扣住 STOP，20 分钟内没有任何告警 | 缓存差异只用于记录疑点，**不再拦截 STOP**：STOP 继续进入发送边界，由 fresh 证据决定。发送边界仍取缓存和 fresh 中较小的一个，以防超卖；STOP 因此被拒时发 **P0**，说明可能是推送滞后，请在 TWS 处理。推送恢复后自动发出 STOP 20。`test_SR6_A2_stale_position_stream_never_silences_a_stop` |
| S5-1 根因 | P2（加固） | STOP 阶段的目标股数记录的是开始时的账本数量；之后补记的股数可能被留在止损线下（第五批 S5-1 的根源） | 新增 `stage_left()`：STOP 阶段总是卖出**全部剩余持仓**，只有持仓为 0 才算完成；止盈仍按自己的份额。`test_SR6_stop_sells_everything_still_open_even_if_its_target_is_stale` |
| Q1 | P2 | 第五批写进 `fills` 的“仅作证据”旧记录仍会影响费用和盈亏 | 启动时拒绝（见 R5-F4）。`test_SR6_batch5_evidence_rows_make_the_state_unusable` |

**审查代理提出、本批未改的问题：**
- 操作员 release 之后在 TWS 下单产生的成交，如果到达本客户端，没有对应意图，仍按 `foreign_fill` 入账并阻断入场。这与 README 中“TWS 交易不计入盈亏”的说法有出入，但属于失败关闭；按第四轮约定，V6-Lite 运行期间操作员不在托管标的上交易。
- 每一笔被隔离的成交都会发一次 P1，并各自需要执行一次 `clear-anomaly`。
- 被系统标为 `Inactive` 的订单不在 working 列表中，后来如果激活并成交，会被隔离（失败关闭，此前就存在）。

## 5 设计判断（更新）

| # | 判断 |
|---|---|
| D13‴ | 组合单（BAG）：已知股票腿按各自标的预留股数；有任何无法识别的腿时，阻断全部 SELL、release 和新开仓，并发 P1 |
| D18′ | “已证实为平”的时间点取 release 记录和解除冻结标记中较晚者。订单创建不晚于该点的任何成交，在入账之前隔离：不进入持仓、lot、盈亏、费用或连亏 |
| D21 | STOP 阶段的含义是卖出全部剩余持仓；记录的目标股数不再限制 STOP |
| D22 | 持仓推送缓存只能触发疑点和告警，不能静默扣住 STOP；最终可卖数量仍取缓存和 fresh 中的较小者，因此被拒的 STOP 一律发 P0 |
| D23 | 第五批写入 `fills` 的“仅作证据”记录会使状态被拒绝运行（检测基于审计中的异常代码），不迁移 |

D11″ 按复核意见表述为**有条件的保证**：在 IBKR 对每个请求恰好返回一个 End、且不会先发送多余 End 的前提下成立。多余 End 先于本次请求自己的 End 到达的窗口仍然存在，需要 G2 记录。

## 6 SHA-256 清单（42 个文件，已排除 `__pycache__`）

相对第五批的增量：

| 文件 | 增 / 删行数 |
|---|---|
| engine.py | +61 / −15 |
| store.py | +48 / −15 |
| risk.py | +5 / −0 |
| README.md | +3 / −3 |
| tests/test_review_fixes.py | +5 / −2 |
| tests/test_review_round5.py | +2 / −1 |

其他说明：
- 新增测试文件：`tests/test_review_round6.py`。
- Schema 仍为 4，没有新增表或列。
- `docs/V6_LITE_G2_RUNBOOK.md` 的 SHA-256：`9728ec25a49d467ffc0a7cb056a7515e780a7bcbb1ce6f7ec37c73199cc2c5bf`

```
45520183d933baf64f9091dc829a6c10594622a0f61ac747fdf07e0731ce0f08  v6lite/README.md
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
8ca51224909f21d8651f47ffac7e8cbe23a08cda8664bb6522ad2e455db3a73c  v6lite/engine.py
1032534d1665efed3143d97bed9f7525410772a49ee5894f2000d0bcdd2fd673  v6lite/examples/live.toml
9c8682f8fa30a91703a53994745e948bc778186688dfe843acc353a5d8df123b  v6lite/examples/paper.toml
c3b4a074f96c5a25789afb5fabf088ead3b1daddaaa1ee5477ec1399c77627e6  v6lite/exits.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
172ef940ec8e5b5959d379c5520ac0f7cfbdf44f4c930a20fd01cfd2ecacadfb  v6lite/models.py
4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1  v6lite/reconcile.py
9430125381538ca15495031ae5597e19c85e9f1375c89d0d0b2d80b3414db5f8  v6lite/report.py
849ad3f3a65a53c13b4998f8d56d33a4ca35b3447d15e349502cb76c45c4ef59  v6lite/risk.py
c7b9000919a9d7672aead2eddab786f96064946cf6084cf6b19cf9351ae70e26  v6lite/sizing.py
da55718a54e8d1999b08c2ac891b06f204d4eefb67f61acca74742461fff18d1  v6lite/store.py
9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e  v6lite/strategy.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  v6lite/tests/__init__.py
aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb  v6lite/tests/conftest.py
fd6c76b9f1e7bd2ff59cd82b86453619f156f1c9c61885aab31f248f01f1f038  v6lite/tests/helpers.py
6445bac89adb5356c19d98e63e0bcd6061bba35f0f476f81269f0482037a59bf  v6lite/tests/test_engine.py
e70899902f5e8338d3b01755e1c747e0fb09322f32c283a2eac51a2f2d5ff86a  v6lite/tests/test_g2_probe.py
6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a  v6lite/tests/test_ibkr_adapter.py
d8e44fb59f5047dc9e539302544b57d82fedeb1d9fb9e56ad5ef57ab4b6a98a5  v6lite/tests/test_review_fixes.py
dd8d7b5d34db5054ac6eef7aab0ed97f16711d03634784832b300eeb468f4605  v6lite/tests/test_review_round2.py
903fca0b51ed8fee873fd5629105d248bb474de2d7aee1b9a3250adafaa6e7cc  v6lite/tests/test_review_round3.py
342c5a14b315ccec9b8da1eb6439e5a9c623f60f715df0853ed031c3ebc1892b  v6lite/tests/test_review_round4.py
fbf5d3a26b81fab54a74e728cf15154b1ce9d0f4984ad28932043c5574fee5f7  v6lite/tests/test_review_round4_selfreview.py
35d5bfac2fd8eb2c842e30e335e01def09fb7b3c8d2ac7572d6b2766db3442e3  v6lite/tests/test_review_round5.py
59a80d968d4023d9c89331730ade220b3f063b48e7f5f519a262616f21e362d8  v6lite/tests/test_review_round5_selfreview.py
f0b6f0756c954e2e260eaed31d2b2bc9147544554a306ed3e25573271202a2c6  v6lite/tests/test_review_round6.py
a5377a4f520e6c3d340cb40571f24eae1dd431be871ecfad241b0d0080424aef  v6lite/tests/test_soak.py
732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f  v6lite/tests/test_strategy_parity.py
75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42  v6lite/tests/test_units.py
91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1  v6lite/tools/__init__.py
0da843f4a9939b03f6075ff337bd5623e49c2615746358e27359eb52b1cc4ba5  v6lite/tools/g2_probe.py
```

## 7 仍未完成

1. **L7**（broker 端保护单）尚未实现，Live 前必须完成并验收。
2. **G2 须记录**：
   - 是否出现 `owes an End`、`unsolicited ... End`、`connection renewed`；
   - 持仓推送与 fresh 查询不一致的频率，以及 `exit refused ... STOP` 是否出现；
   - 12/12/17 秒（原始请求）和 19 秒（含排空）耗时的实际分布；
   - BAG 订单的字段；
   - 1101 之后能否恢复。
3. **G3 须注入验证**：冻结 → 处理列出的订单 → 人工平仓 → release 的完整流程；隔离成交的告警与处理。
4. 各项时间门槛（60/120/2/60 秒）都是工程假设，待 G2 校准。

## 8 请 Codex 复核

1. 在 Windows 上复跑第 1 节命令，核对第 6 节清单。
2. 对第 3 节和第 4 节逐项给出裁定。
3. 重点反证以下几点：
   - D18′：会不会错误隔离真实交易（订单创建早于终点、却在终点之后真实成交）；
   - D21、D22：STOP 卖出全部剩余持仓，以及取较小者的边界；
   - A3：组合单在存在期间对入场的阻断。
4. 对 D13‴、D18′、D21–D23 表态。
