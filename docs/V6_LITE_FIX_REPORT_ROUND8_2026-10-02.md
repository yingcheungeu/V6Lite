# V6-Lite 第八批整改报告（针对第六、七批复核）

日期：2026-10-02 PT。整改：Claude。待复核：Codex。
依据：Codex 对第六、七批的复核意见（由 John 转述）：2 个阻塞 G3 的 P1，以及若干 P2 和开放项。

**结论先行：**
- 2 个 P1 和全部 P2 均已修复，均有回归测试。
- Windows 上那次失败（时间戳相等）已从根上改为按消息顺序判断。
- 交付前自审又发现并修复了 6 个问题：3 个是第一版新引入的 BUY 边界问题，3 个是适配器的报价归属问题。
- 全量离线测试 **278 passed**，在本机连续 3 次结果一致。复核时请以 Windows 结果为准。

本报告**不宣称** G3 或 Live 可放行。G2 仍需按复核要求补齐以下几项：
- 处理 4 笔未托管持仓；
- 开通并核实实时 API 行情；
- 确认远程告警能实际送达；
- 取得两次真实扫描的证据。

## 1 复现命令

```powershell
cd C:\Dev\TradingV6
. .\venv_v6\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
$env:PYTHONPYCACHEPREFIX = Join-Path ([System.IO.Path]::GetTempPath()) ('v6lite-r8-' + [guid]::NewGuid().ToString('N'))
python -m pytest v6lite/tests -q -ra                                     # 期望 278 passed
python -m pytest v6lite/tests/test_review_round7.py -q -ra               # 本次复核的问题，10 项
python -m pytest v6lite/tests/test_review_round7_selfreview.py -q -ra    # 交付前自审，7 项
```

| 测试集 | 第七批代码 | 本批 |
|---|---|---|
| `test_review_round7.py`（10 项） | **9 failed / 1 passed**。通过的那项是“证据干净时照常买入”，保留作护栏 | 10 passed |
| `test_review_round7_selfreview.py`（7 项） | 第八批第一版上 6 项失败；第 7 项（S1）在第一版上就通过，用来固定已正确的行为 | 7 passed |
| v6lite 全量 | 261（Windows 上为 260 passed + 1 failed） | **278 passed** ×3 |

改动的既有测试（3 处，安全断言都没有放宽）：
- `test_g2_day1.py`：两项行情测试改用真实的 ib_insync Wrapper 和 Ticker，因为报价现在必须由本次订阅的价格 tick 来证明。
- `test_g2_day1_selfreview.py::test_R7_9`：改为走真正的订阅路径，并新增一条断言：已取消请求的迟到 tick 不能让报价恢复有效。这一项就是 Windows 上失败的那个测试；原先按严格的时间先后判断，现在改为按消息顺序。
- `test_review_round6.py`：旧状态拒绝运行时的报错文案从“batch-5”改为“earlier version”。

## 2 逐项：发现 → 改动 → 测试

| 项 | 改动 | 测试 |
|---|---|---|
| **P1 未知腿组合单出现在两次对账之间，BUY 边界不重新查挂单** | `_entry_gate` 的第一步改为重新查询 working 订单，这次查询计入阻塞预算。查询失败，或存在无法识别腿的组合单（`*`），都拒绝买入。查询结束**之后**，再重新读取：新事件、kill switch、当日亏损、告警健康、实盘批准、全部入场阻断（包括这次查询本身发现的孤立单异常），以及资金和资金时效；时间一律用查询结束后的时钟（自审 D1–D3） | `test_P1a_combo_appearing_between_reconciles_stops_the_buy`、`test_P1a_failed_working_order_query_refuses_the_buy`、`test_P1a_clean_evidence_still_buys`；自审 D1、D2、D3 |
| **P1 行情被拒后，仅成交量变化也会刷新 Ticker.time，使旧价格通过新鲜度检查** | 适配器挂接 `wrapper.priceSizeTick`（decoder 每条消息都按名字查找这个方法），只把**价格** tick 记为证据：配置实时行情时只认 1、2、4 类（买价、卖价、成交价）；配置延迟行情时另外认 66、67、68 类。成交量和挂单量 tick 都不算。各类事件都按**消息顺序号**排序，不再比较时间戳，所以 Windows 上时间戳相等也不影响判断。具体规则：<br>• 一次报价订阅以它的**请求 ID** 为准（自审 A2）；按我们自己配置的代码归属，而不是 IBKR 返回的代码写法（自审 A1）。<br>• 订阅开始时，清空复用的 Ticker 上残留的价格。<br>• `quote()` 只在订阅开始之后、且在最近一次拒绝之后收到过价格 tick 时才有效。<br>• 拒绝记录也只有之后的价格 tick 才能清除 | `test_P1b_volume_ticks_after_a_refusal_never_revive_an_old_price`（全部消息使用**同一时间戳**）、`test_P1b_resubscription_does_not_inherit_the_old_price`；自审 A1、A2、A3、S1 |
| **P2 旧证据成交检查漏掉 `late_fill_after_release`** | 1. 新成交：订单所属持仓或恢复持仓已是 RELEASED 时，在入账**之前**隔离（`late_fill_after_release`），不写入 `fills`，不计费用，也不进入盈亏。<br>2. 旧状态：`legacy_evidence_fills` 除第五批的 `late_fill_before_flat` 外，还识别早期版本记入账的 `late_fill_after_release`，以及发生在 RELEASED 持仓上的 `exit_after_close`。只要检测到，就拒绝启动，文件不做任何改动 | `test_P2a_earlier_versions_evidence_rows_of_released_positions_refuse_the_state`、`test_P2a_new_fill_of_a_released_positions_order_is_quarantined`（当日费用不变） |
| **P2 行情拒绝按代码而不是按请求识别；冻结行情不告警** | 1. 拒绝按**请求 ID** 对应到当前的报价订阅；同一标的上的历史数据请求报 354，不再被记成报价拒绝。<br>2. `market_data_status` 新增 `frozen_symbols`；在交易时段内，冻结行情和延迟行情一样发 P1 | `test_P1b_a_refusal_of_a_history_request_on_the_same_symbol_is_not_a_quote_refusal`、`test_P2b_frozen_data_in_rth_is_paged` |
| **P2 资金新鲜度使用了“任意账户字段”的时间** | `FundsSnapshot.seen_at` 改为 AvailableFunds **这一个字段**最近一次推送的时间；其他字段的推送不能让旧的资金值看起来是新的 | `test_P2b_available_funds_freshness_is_its_own_push_time` |
| **Windows 失败用例** | 原测试要求“恢复时间严格晚于拒绝时间”，Windows 上两者可以相等。按消息顺序判断后，这个问题不再存在 | 第 1 节 |

## 3 交付前自审

另起一个审查代理做对抗性审查，以下 6 项在第八批第一版上复现，均已修复：

| 编号 | 问题 |
|---|---|
| D1 | BUY 边界的新查询发现了孤立订单，已经告警“入场被阻断”，却仍然发出了 BUY |
| D2 | 查询期间设置的 kill switch 没有拦住 BUY |
| D3 | 查询期间到达的异常事件没有拦住 BUY |
| A1 | IBKR 返回的代码写法与我们的不同时（例如 `BRK.B` 对 `BRK B`），报价永远无效 |
| A2 | 已取消请求 ID 的迟到 tick 被算作新订阅的价格 |
| A3 | 配置实时行情时，延迟类 tick 被当成实时价格 |

**审查确认无误的部分：**
- 安静但仍在实时更新的股票，报价保持有效（S1）。
- 重新连接后，hook 仍然生效。
- 重放已入账的成交不会误报 RELEASED 隔离。
- `legacy_evidence_fills` 不会误伤本版本自己产生的状态。

**审查提出、本批不改的两点：**
1. 如果 IBKR 只在值变化时才推送 AvailableFunds，资金时效就靠 210 秒一次的重新订阅来维持，扫描时有可能落在资金已超过 270 秒的窗口里，导致入场被拒。这是失败关闭的方向；G2 第 1 天的实测是不论值变不变，AvailableFunds 都每 180 秒推送一次（30 分钟内 22 次），需要更多 G2 日来确认。
2. 状态里只要有一条旧版本记错的证据成交，整个状态就会被拒绝并要求归档。这是有意的，但在正式状态上会造成运维负担。目前所有旧版本都没有在正式状态上跑过 release。

## 4 对开放裁定的回应

- **D18′：** 同意复核意见。“订单创建早于平仓确认”不能证明“成交也早于确认”。这条规则的定位是**失败关闭**：把这类成交隔离并交给人工处理，P1 告警中写明“请在 TWS 核对”。报告和 README 不再把它表述为成交时间的证明。
- **D11″：** 维持有条件的保证。前提是 IBKR 对每个请求恰好返回一个 End，且不会提前发出多余的 End；需要 G2 观察。
- **历史窗口：** 按复核建议先维持 90 根，由 G2 探针在同一窗口下继续对比；是否改窗口由 John 决定。

## 5 SHA-256 清单（46 个文件，已排除 `__pycache__`）

相对第七批的增量：

| 文件 | 增 / 删行数 |
|---|---|
| broker/ibkr.py | +69 / −15 |
| store.py | +39 / −9 |
| engine.py | +22 / −2 |
| README.md | +3 / −3 |
| models.py | +1 / −1 |

- 测试：新增 `test_review_round7.py` 和 `test_review_round7_selfreview.py`；修改 `test_g2_day1.py`、`test_g2_day1_selfreview.py`、`test_review_round6.py`（原因见第 1 节）。
- Schema 仍为 4。
- `docs/V6_LITE_G2_RUNBOOK.md` 的 SHA-256：`09bd9b34545d53ade4ef4325aa35c9ae0e830f70c1cc70d7eff1ae6feecdb465`

```
5e9fa74f4d3047f1f3ca0551eb5dbe9d57888a71959816dc93c2c5059a4e7935  v6lite/README.md
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
2281d96e3e41d7978ffa53555d5db0dbbb159f1046923154e21fca6bce8d2da3  v6lite/__main__.py
10b60dbd110f1b02f78d946eeff65ce8cf8cc71bbf71995fea7f8f23847f6819  v6lite/alerts.py
162daa7d98a2362bc29521fcf65472c5590fb282da694b5011da934d8ffc26b5  v6lite/approval.py
2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24  v6lite/broker/__init__.py
722fab4b5992c737dfcfcb86c5b74c33ba633b3b3ee561eac3c4a289abe723e1  v6lite/broker/base.py
6f8e615342f5f2d4f89a6b44fc931723f8844d54049a2e5414774f528d655d37  v6lite/broker/fake.py
e95ae3c0f52e12db38525b6125d2dd34b66340619a33157faf8ba5d624684c94  v6lite/broker/ibkr.py
0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1  v6lite/clock.py
e8e10582fbf82d3849bcca4662d039d910923528fc15a25da99155e3af9b00f4  v6lite/config.py
2285bc56bed56f38dda542b93bef693b51593988cfa15359ac9c7624b4285143  v6lite/engine.py
1032534d1665efed3143d97bed9f7525410772a49ee5894f2000d0bcdd2fd673  v6lite/examples/live.toml
9c8682f8fa30a91703a53994745e948bc778186688dfe843acc353a5d8df123b  v6lite/examples/paper.toml
c3b4a074f96c5a25789afb5fabf088ead3b1daddaaa1ee5477ec1399c77627e6  v6lite/exits.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
9d79010208867102db7a441c7672282155eb0921a2ca18e1fe0c7679f3ca7ce6  v6lite/models.py
4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1  v6lite/reconcile.py
6cfb8cb99fb69aa52893eca262375bf426e110256fbab0d8b6c73d927561eb01  v6lite/report.py
849ad3f3a65a53c13b4998f8d56d33a4ca35b3447d15e349502cb76c45c4ef59  v6lite/risk.py
c7b9000919a9d7672aead2eddab786f96064946cf6084cf6b19cf9351ae70e26  v6lite/sizing.py
6c169be23a77746a6dd90ad0e84d7550cca15bd7038b6398f3ab40b034379506  v6lite/store.py
9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e  v6lite/strategy.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  v6lite/tests/__init__.py
aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb  v6lite/tests/conftest.py
fd6c76b9f1e7bd2ff59cd82b86453619f156f1c9c61885aab31f248f01f1f038  v6lite/tests/helpers.py
6445bac89adb5356c19d98e63e0bcd6061bba35f0f476f81269f0482037a59bf  v6lite/tests/test_engine.py
1c3eff96a4bea485c95f0c878e65b853134ab4156bbfd8dfff0483a1428dfa8d  v6lite/tests/test_g2_day1.py
d7b24b3b679a7e8455b4a4868fecc590a50784190764fde673cd08865d8df46a  v6lite/tests/test_g2_day1_selfreview.py
e70899902f5e8338d3b01755e1c747e0fb09322f32c283a2eac51a2f2d5ff86a  v6lite/tests/test_g2_probe.py
6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a  v6lite/tests/test_ibkr_adapter.py
d8e44fb59f5047dc9e539302544b57d82fedeb1d9fb9e56ad5ef57ab4b6a98a5  v6lite/tests/test_review_fixes.py
3f1b96a33996c29e4a9ccec7a30b2b39cec812fe54cb63f6d432fda3238ab519  v6lite/tests/test_review_round2.py
903fca0b51ed8fee873fd5629105d248bb474de2d7aee1b9a3250adafaa6e7cc  v6lite/tests/test_review_round3.py
342c5a14b315ccec9b8da1eb6439e5a9c623f60f715df0853ed031c3ebc1892b  v6lite/tests/test_review_round4.py
fbf5d3a26b81fab54a74e728cf15154b1ce9d0f4984ad28932043c5574fee5f7  v6lite/tests/test_review_round4_selfreview.py
35d5bfac2fd8eb2c842e30e335e01def09fb7b3c8d2ac7572d6b2766db3442e3  v6lite/tests/test_review_round5.py
59a80d968d4023d9c89331730ade220b3f063b48e7f5f519a262616f21e362d8  v6lite/tests/test_review_round5_selfreview.py
aae0d16cb7a172ecc6077dcc99b078f7b2d83e432fb0dc3811bc1c29dc2162fb  v6lite/tests/test_review_round6.py
93f8965fe4247b6b020354fa10e2b0c7fdc8ffa599aa0a898d5e21c17a7bac95  v6lite/tests/test_review_round7.py
a3589fae5996245f7246f1860875a847b6761ed9f8bab5a317ab5c7d9e5e86bf  v6lite/tests/test_review_round7_selfreview.py
a5377a4f520e6c3d340cb40571f24eae1dd431be871ecfad241b0d0080424aef  v6lite/tests/test_soak.py
732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f  v6lite/tests/test_strategy_parity.py
75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42  v6lite/tests/test_units.py
91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1  v6lite/tools/__init__.py
2d0d39e449b6e0520d079bdc80d5877d6cf66f16c5626e250f4b2d22530d5aee  v6lite/tools/g2_probe.py
```

## 6 仍未完成

1. **L7**（券商端保护单）尚未实现。
2. **G2 需要一个完整交易日**，包括以下证据：
   - 实时 API 行情：`market_data` 一项为空；
   - 远程告警确实送达；
   - 两次真实扫描；
   - 资金字段每次推送之间的间隔；
   - 两个数据源在同一窗口下的分数差异。
