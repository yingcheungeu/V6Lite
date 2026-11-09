# V6-Lite 第七批整改报告（G2 第 1 天发现的问题）

日期：2026-10-02 PT。整改：Claude。待复核：Codex。
依据：`docs/V6_LITE_G2_DAY1_2026-10-01.md`，这是第一次在真实 Paper Gateway 上只读运行得到的证据。本批同时把第六批报告交给 Codex 做第六轮复核，两者互不依赖。

**结论先行：** G2 第 1 天发现的 5 个问题都已处理，交付前自审又修了 7 个问题（其中 6 个是本批第一版引入的，1 个早已存在）。全量离线测试 **261 passed**，连续 3 次结果一致。有一项策略问题需要 John 决定（第 4 节），本批没有改策略。本报告**不宣称** G3 或 Live 可放行。

## 1 复现命令

```powershell
cd C:\Dev\TradingV6
. .\venv_v6\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
$env:PYTHONPYCACHEPREFIX = Join-Path ([System.IO.Path]::GetTempPath()) ('v6lite-r7-' + [guid]::NewGuid().ToString('N'))
python -m pytest v6lite/tests -q -ra                                # 期望 261 passed
python -m pytest v6lite/tests/test_g2_day1.py -q -ra                # G2 第 1 天的问题，14 项
python -m pytest v6lite/tests/test_g2_day1_selfreview.py -q -ra     # 交付前自审，9 项
```

| 测试集 | 第六批代码 | 本批 |
|---|---|---|
| `test_g2_day1.py` 中第一版的 13 项 | **12 failed / 1 passed**。通过的那项是“内容变化后 5 分钟可重发”，原来的去重本来就满足，保留作护栏 | 全部通过 |
| `test_g2_day1.py` 自审后新增 1 项（冻结行情不算报价） | — | 通过 |
| `test_g2_day1_selfreview.py`（9 项） | 在第六批上无法运行：旧的阻塞式刷新在没有 socket 的真实 ib_insync 对象上会无限等待 | 9 passed |
| v6lite 全量 | 238 | **261 passed** ×3 |
| 改动的既有测试 | — | 2 处，都是行为变更后的期望更新，安全断言没有放宽：<br>• `test_review_round2.py::test_S3_quiet_account_stream_is_re_requested`：新增一行 `account_answers_refresh = True`。FakeBroker 现在和真实适配器一致，重新订阅后不会立刻显得“新鲜”；这行模拟 IBKR 随后重新推送<br>• `test_review_round4_selfreview.py`：只在上一批改了 P0 文案大小写，本批没有改动 |

## 2 逐项：发现 → 改动 → 测试

| 项 | 发现（G2 第 1 天） | 改动 | 测试 |
|---|---|---|---|
| **D1-1** 资金刷新 | AvailableFunds 每 180 秒推送一次（两次探针一致），而代码 150 秒没收到就刷新。刷新用的阻塞式 `reqAccountUpdates` 在已订阅的账户上收不到 download-end，每次等满 15 秒才超时。真实运行时，交易时段内大约每 3 分钟会卡住 15 秒，而且刷新拿不到结果 | 时间参数改为：推送周期 180 秒；超过 210 秒没有推送才刷新；超过 270 秒不再信任。<br>刷新改为**不阻塞**地退订再订阅（`client.reqAccountUpdates(False/True)`）：<br>• 旧值保留，仍按原来的时间判断是否过期；<br>• 每 210 秒最多刷新一次；<br>• 不再占用阻塞调用的时间预算。<br>1101（数据丢失）时才丢弃旧值。NetLiquidation 改为来自同一个账户流，和 AvailableFunds 一起作废，不再返回数据丢失之前的旧值（自审 R7-5） | `test_D1_1_*` 4 项；`test_R7_4`（真实 ib_insync：没有请求在等的 download-end 不会出问题，退订不会清掉已有的值）；`test_R7_5`、`test_R7_6`、`test_R7_7` |
| **D1-2** 告警刷屏 | 同一条持仓差异全天发了 117 条 P1（Alerter 原来的去重窗口是 5 分钟） | 标题和内容都不变时，最多每 30 分钟重发一次，P0 为 10 分钟；内容一变化，5 分钟后即可重发。<br>新增 `Alerter.resolve()`：断线恢复、对账正常、持仓重新有报价、实时行情恢复时清除对应记录，条件再次出现时立刻重发（自审 R7-3）。<br>只写日志的告警不会压住之后需要远程发送的告警（自审 R7-1/2） | `test_D1_2_*` 3 项；`test_R7_1`、`test_R7_2`、`test_R7_3` |
| **D1-3** 配置报错 | 配置文件不存在时打出一长串报错 | 改为一行 `CONFIG ERROR`，附上首次使用时的复制命令；TOML 格式错误也只输出一行。退出码为 2 | `test_D1_3_missing_config_is_one_clear_line` |
| **D1-4** 行情不可见 | 10 个标的全天没有报价，10089 只出现在原始日志里，没有任何告警 | **适配器**：<br>• 记录 IBKR 拒绝报价订阅的标的（354/10089/10090/10091/10167/10168/10197），只记录本系统订阅了报价的标的，VIX 这类历史数据请求不记录（自审 R7-8）；<br>• 只有拒绝**之后**收到的实时报价才能清除这条记录（自审 R7-9）；<br>• 配置为实时行情时，延迟（3/4）和冻结（2）的报价都不算报价（`quote()` 返回 None），因为它们的本地接收时间看起来是新的，价格却是旧的。<br>**引擎**：心跳时发现上述情况就发 P1 “no real-time market data”，断线期间不判断；`status`、心跳和健康快照都增加 `market_data` 一项。<br>**日报**：写明配置的行情类型（实时，或 DELAYED 仅限演练），以及当天观察到的问题；只读取当天的健康快照 | `test_D1_4_*` 5 项；`test_R7_8`、`test_R7_9` |
| **D1-5** 数据源比较 | 探针拿 90 根 IBKR 日线和约 125 根 yfinance 日线比较。EMA50 和 ADX 的结果取决于起算的历史长度，所以两边分数差异（AAPL 48 对 60 等）不能说明数据有问题 | 探针改为在**相同窗口**（同一截止日、同样根数）上比较，记录：<br>• 日期是否一致、收盘价最大差异；<br>• 每个指标的数值和分数差；<br>• 旧扫描器自己的 yfinance 60d 窗口结果。<br>摘要 md 里每个标的一行 | `test_D1_5_probe_compares_sources_on_the_same_window` |

## 3 交付前自审

另起一个审查代理做了对抗性审查，确认以下问题并已修复：

- **R7-1/2（本批第一版引入）：** 开盘前只写日志的断线告警，会让 30 分钟内交易时段的真实断线不发远程告警。
- **R7-3（本批第一版引入）：** 断线、恢复之后再次断线，因为告警内容相同而被压住。
- **R7-6（本批第一版引入）：** FakeBroker 仍按旧的阻塞方式模拟刷新，测试因此看不到“刷新后资金仍过期、入场被拒”的这段时间。
- **R7-7（本批第一版引入）：** 读取资金失败时，日志变成一条误导性的变量错误。
- **R7-8 / R7-9（本批第一版引入）：** 历史数据请求的拒绝记录永远清不掉；旧的成交价会把新的订阅拒绝掩盖掉。
- **R7-5（早已存在）：** 1101 之后 NetLiquidation 仍返回数据丢失之前的值。

以上问题均已写成测试，见 `test_g2_day1_selfreview.py`。

**审查提出、本批未改：** 配置为延迟行情的 Paper 演练（`market_data_type = 3`）仍会用约 15 分钟前的价格做判断，只在状态和日报中标明 DELAYED。这是演练专用设置；Live 配置强制要求实时行情。

## 4 需要 John 决定：策略的历史窗口

旧扫描器 `market_scanner.py` 用 yfinance `period="60d"`（60 个日历日，大约 40 根日线）计算指标；V6-Lite 用 IBKR 的 90 根日线（`bars_lookback_days = 90`）。

EMA50 和 ADX 对起算长度很敏感。合成数据实测：同一组价格，取 42 根和 60 根时 ADX 为 21.9 和 20.8，给出 BUY 70；取 90 根时 ADX 为 19.7，跌破 20，变成 NEUTRAL 60。

也就是说，**即使数据完全相同，V6-Lite 和旧 V6 也可能给出不同的信号**。策略对齐测试用的是相同的输入，所以没有发现这一点。两种选择：
- **保留 90 根：** 指标更稳定，但和旧 V6 的历史表现不完全可比；
- **改为与旧扫描器一致的窗口：** 和旧 V6 一致，但 EMA50 的预热不足（不到 50 根）。

本批没有改动，等 John 决定。第 2 天起，探针会同时记录“旧扫描器自己的 60d 窗口”的结果，可以直接对比。

## 5 SHA-256 清单（44 个文件，已排除 `__pycache__`）

相对第六批的增量：

| 文件 | 增 / 删行数 |
|---|---|
| engine.py | +48 / −7 |
| broker/ibkr.py | +47 / −9 |
| tools/g2_probe.py | +47 / −9 |
| report.py | +24 / −0 |
| alerts.py | +19 / −1 |
| `__main__.py` | +8 / −0 |
| broker/fake.py | +8 / −1 |
| README.md | +7 / −2 |

- 测试：新增 `test_g2_day1.py`、`test_g2_day1_selfreview.py`，`test_review_round2.py` 增加 1 行。
- Schema 仍为 4。
- `docs/V6_LITE_G2_RUNBOOK.md` 的 SHA-256：`8a25b3331c82f0dc7bc3c66fe7d111ceb4ea25d3ebafd1103911d36f823b36cf`

```
d478e97ee7c3b5800afb069ff8c8af4ded28cd8caf8c737964922ba638cdefba  v6lite/README.md
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
2281d96e3e41d7978ffa53555d5db0dbbb159f1046923154e21fca6bce8d2da3  v6lite/__main__.py
10b60dbd110f1b02f78d946eeff65ce8cf8cc71bbf71995fea7f8f23847f6819  v6lite/alerts.py
162daa7d98a2362bc29521fcf65472c5590fb282da694b5011da934d8ffc26b5  v6lite/approval.py
2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24  v6lite/broker/__init__.py
722fab4b5992c737dfcfcb86c5b74c33ba633b3b3ee561eac3c4a289abe723e1  v6lite/broker/base.py
6f8e615342f5f2d4f89a6b44fc931723f8844d54049a2e5414774f528d655d37  v6lite/broker/fake.py
164aba452090a63c90d15fe33e15280f53128c4aa4cfa6d02f9a92905c71b870  v6lite/broker/ibkr.py
0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1  v6lite/clock.py
e8e10582fbf82d3849bcca4662d039d910923528fc15a25da99155e3af9b00f4  v6lite/config.py
2745713d53b5fdd1e459b301b3543cb0a59a8850b388080af43d281d6b97075b  v6lite/engine.py
1032534d1665efed3143d97bed9f7525410772a49ee5894f2000d0bcdd2fd673  v6lite/examples/live.toml
9c8682f8fa30a91703a53994745e948bc778186688dfe843acc353a5d8df123b  v6lite/examples/paper.toml
c3b4a074f96c5a25789afb5fabf088ead3b1daddaaa1ee5477ec1399c77627e6  v6lite/exits.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
172ef940ec8e5b5959d379c5520ac0f7cfbdf44f4c930a20fd01cfd2ecacadfb  v6lite/models.py
4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1  v6lite/reconcile.py
6cfb8cb99fb69aa52893eca262375bf426e110256fbab0d8b6c73d927561eb01  v6lite/report.py
849ad3f3a65a53c13b4998f8d56d33a4ca35b3447d15e349502cb76c45c4ef59  v6lite/risk.py
c7b9000919a9d7672aead2eddab786f96064946cf6084cf6b19cf9351ae70e26  v6lite/sizing.py
da55718a54e8d1999b08c2ac891b06f204d4eefb67f61acca74742461fff18d1  v6lite/store.py
9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e  v6lite/strategy.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  v6lite/tests/__init__.py
aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb  v6lite/tests/conftest.py
fd6c76b9f1e7bd2ff59cd82b86453619f156f1c9c61885aab31f248f01f1f038  v6lite/tests/helpers.py
6445bac89adb5356c19d98e63e0bcd6061bba35f0f476f81269f0482037a59bf  v6lite/tests/test_engine.py
58ffd80d35e0c60f2d832d405ff0e08a0d27fa010e4bdec944dfedffce8c1ab3  v6lite/tests/test_g2_day1.py
d8cc6c1c1ce3871100a16ab08280a0bfaca41208f38599f04da89ca9492c4cd9  v6lite/tests/test_g2_day1_selfreview.py
e70899902f5e8338d3b01755e1c747e0fb09322f32c283a2eac51a2f2d5ff86a  v6lite/tests/test_g2_probe.py
6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a  v6lite/tests/test_ibkr_adapter.py
d8e44fb59f5047dc9e539302544b57d82fedeb1d9fb9e56ad5ef57ab4b6a98a5  v6lite/tests/test_review_fixes.py
3f1b96a33996c29e4a9ccec7a30b2b39cec812fe54cb63f6d432fda3238ab519  v6lite/tests/test_review_round2.py
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
2d0d39e449b6e0520d079bdc80d5877d6cf66f16c5626e250f4b2d22530d5aee  v6lite/tools/g2_probe.py
```

## 6 仍未完成

1. **L7**（broker 端保护单）尚未实现。
2. **G2 第 2 天起需要记录：**
   - 每次 `[ACCOUNT_STREAM] ... re-subscribed` 之后，多久收到新的账户值；
   - `market_data` 一项是否为空；
   - 相同窗口下两个数据源的 `score_diff`。
3. **第六批报告**仍在等 Codex 第六轮复核；本批可以和第六批一起复核，也可以分开。
