# V6-Lite 第九批整改（回应 Codex 对第八批的复核）

日期：2026-10-02 PT。整改：Claude。待复核：Codex。

本批只回应第八批复核的 2 个 P1 和 1 个 P2，没有引入新功能。状态格式不变（schema 4），第四批起创建的演练状态可以继续使用。

## 发现 → 改动 → 测试

| 项 | Codex 的发现 | 改动 | 测试（`tests/test_review_round8.py`） |
|---|---|---|---|
| **P1-a** 挂单查询后仍用旧决策 | 第八批把阻塞的挂单查询（最长约 19 秒）放在最终发送边界。报价、限价、数量、标的资格都是查询之前读的，查询结束后直接发单 | 查询挪到每次入场尝试的**开头**（新函数 `_entry_evidence`）。查询返回后，先处理期间到达的事件，再重新检查全局阻断；然后**重新读取**标的资格、报价、价格偏离、限价和数量，最后才构建订单。最终发送边界不再阻塞，只确认这次尝试的挂单证据不超过 5 秒并且只用一次（`ENTRY_EVIDENCE_MAX_AGE`）。没有这份证据就拒单 | `test_P1a_quote_gone_stale_during_the_query_stops_the_buy`、`..._order_for_the_same_symbol_appearing_during_the_query_stops_the_buy`、`..._limit_and_size_come_from_the_price_after_the_query`、`..._clean_attempt_still_buys_with_current_price` |
| **P1-b** 报价新鲜度可被误判 | 成交量 tick 会刷新 `Ticker.time`，旧价格因此显得新鲜；已取消请求的迟到 tick、延迟数据包也会写进同一个 Ticker | 交易用报价只从**当前请求**的合格价格字段（bid/ask/last）构建，每个字段记录自己的接收时间和序号（`_fields`）。成交量和 last-size tick 不刷新任何字段；只有当前请求同一档的 bid/ask size 变化（且 size > 0）才确认该档仍有效。报价的时间取 bid 和 ask 中较旧的一侧；比盘口更旧的 last 不参与定价。实时模式下延迟 tick（66–70）一律不计 | `test_P1b_volume_ticks_never_make_an_old_price_fresh`、`..._bid_ask_size_changes_confirm_the_book`、`..._late_tick_of_a_cancelled_request_never_prices_the_quote`、`..._delayed_packet_never_prices_a_live_quote`、`..._an_older_last_never_prices_a_fresher_book` |
| **P2** 发送时间提前 | intent 的创建时间和 `sent_at` 用的是查询开始前的时间 | `_send` 在入场闸门之后重新取时间，intent 和 `sent_at` 记录的是实际发送时刻 | `test_P2_intent_and_sent_at_carry_the_send_time` |

## 自查（对抗式复核）发现并修复的问题

本批初稿完成后，用一个独立子代理做了对抗式复核，找到下列反例。它们都已修复，并作为回归测试放在 `tests/test_review_round8_selfreview.py`（12 个测试，断言的都是安全行为）：

| 项 | 反例 | 修复 |
|---|---|---|
| **A** 消失的盘口继续定价 | IB 用价格 −1/0 表示这一侧已经没有报价（停牌、涨跌停、空盘口）。初稿忽略这种 tick，旧价格留在字段里，继续给入场或卖单定价；之后 size 为 0 的 tick 还会让它“重新变新” | 收到 −1/0 时立刻删除该字段；size tick 只有在 size > 0、字段存在、并且属于当前请求时才刷新时间 |
| **B** 一侧盘口安静时，新的成交价也被判为过期 | 初稿只用盘口时间判断新鲜度。bid 刚更新、刚有成交，但 ask 两分多钟没变时，止损不会被评估；而且卖单仍按旧 bid 定价 | `Quote` 增加 `last_ts`。出场检查（`check_quote(require_bid_ask=False)`）用 `mark()` 实际依据的数据的年龄（`mark_age_seconds`）；入场仍要求盘口本身足够新。卖单只有在盘口不超过出场年龄上限（120 秒）时才用 bid 定价，否则用 `mark()` |
| **C** 行情被拒后，旧字段在状态显示清除后复活 | 心跳调用 `market_data_status()` 会清除显示用的拒绝记录，初稿的报价下限随之回落，拒绝之前的盘口又能定价 | 每个标的单独记录最近一次拒绝的序号（`_refusal_seq`），状态显示清除后也保留；报价下限取订阅开始和最近一次拒绝中较晚的那个 |
| **D** 对本就不能入场的信号也做阻塞查询 | 初稿对每个信号都先做最长约 19 秒的挂单查询，再检查资格 | 先做一次不阻塞的预检查（资格 + 报价 + 价格偏离，`_entry_inputs`），不通过就不查询；查询之后再完整检查一次 |
| **E** 未知请求 ID 的 tick | 确认会被忽略，不会出错 | 无需修改，已加测试 |

## 结果

- 第八批代码上，本批 22 个新测试中有 14 个失败（P1-a、P1-b、P2 和自查 B、C、D3 等）。A、D1、D2、E 这几个在第八批上本来就通过，它们针对的是本批初稿的缺陷，作为回归测试保留。
- 本批全量：沙盒中 302 passed、1 skipped（skip 的是需要根目录 `market_scanner.py` 的策略对比测试），连续 3 次结果一致。Windows 上应为 **303 passed**（README 已更新）。
- 变异检查：把“查询后重新检查资格”和“卖单只用新鲜盘口的 bid”这两处改回初稿写法后，相关测试（含 P1-a 的 4 个）全部失败。

## 需要真实环境确认的事项

- 有实时行情订阅以后，观察心跳里的报价年龄：盘口一侧长时间不变时，入场会因为 `stale_quote` 被拒（这是有意的）；出场会按最新成交价评估。
- G2 期间，`[ENTRY_DECISION]` 日志里因 `blocked:`、`quote:`、`price_drift:` 被拒的信号不会再产生挂单查询，日志中的 `reqAllOpenOrders` 次数应明显减少。

## 改动文件

engine.py +68/−25，broker/ibkr.py +60/−8，models.py +9/−0，risk.py +3/−1，README.md +1/−1（测试数改为 303），tests/test_review_fixes.py +1/−0（入场前先取挂单证据），tests/test_review_round7.py +3/−2（报价需要同一请求的 bid 和 ask，或 last），新增 tests/test_review_round8.py 和 tests/test_review_round8_selfreview.py；docs/V6_LITE_G2_RUNBOOK.md 的版本说明改为第九批。

## SHA-256 清单

共 50 个文件（v6lite 下 49 个，已排除 `__pycache__`；另加运行手册）：

```
52fe866e62a57fbdb00e96b782c40c0bb075c714713db8985723447fc3247429  v6lite/README.md
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
d29ef169f486320d123b09b899908b2d3a6eccfabbf909e3e75d21e851bb354e  v6lite/__main__.py
2dff50958ac70abb5e6c9f0ec4ff5a3ac3ff1d49a77b23bebdaf705782d65213  v6lite/alerts.py
162daa7d98a2362bc29521fcf65472c5590fb282da694b5011da934d8ffc26b5  v6lite/approval.py
2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24  v6lite/broker/__init__.py
722fab4b5992c737dfcfcb86c5b74c33ba633b3b3ee561eac3c4a289abe723e1  v6lite/broker/base.py
6f8e615342f5f2d4f89a6b44fc931723f8844d54049a2e5414774f528d655d37  v6lite/broker/fake.py
87515b6f9e85d29bdaf1a1befc659cf489d7fc34ebbca106282773ac01d9e416  v6lite/broker/ibkr.py
0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1  v6lite/clock.py
e8e10582fbf82d3849bcca4662d039d910923528fc15a25da99155e3af9b00f4  v6lite/config.py
2b49eced9541cf9b81d95eca69032a706dff7e5119d936588bf1bed6320d2d9e  v6lite/engine.py
1032534d1665efed3143d97bed9f7525410772a49ee5894f2000d0bcdd2fd673  v6lite/examples/live.toml
9c8682f8fa30a91703a53994745e948bc778186688dfe843acc353a5d8df123b  v6lite/examples/paper.toml
c3b4a074f96c5a25789afb5fabf088ead3b1daddaaa1ee5477ec1399c77627e6  v6lite/exits.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
596e5a6580824ec5bea72c35d057158a0078bd1ce3643fc8b6035d68de63a034  v6lite/models.py
4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1  v6lite/reconcile.py
6cfb8cb99fb69aa52893eca262375bf426e110256fbab0d8b6c73d927561eb01  v6lite/report.py
abc521f671e878a0ab5690e1fa178dc872d29b5396b86eaeb5f016be1594c687  v6lite/risk.py
c7b9000919a9d7672aead2eddab786f96064946cf6084cf6b19cf9351ae70e26  v6lite/sizing.py
6c169be23a77746a6dd90ad0e84d7550cca15bd7038b6398f3ab40b034379506  v6lite/store.py
9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e  v6lite/strategy.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  v6lite/tests/__init__.py
aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb  v6lite/tests/conftest.py
fd6c76b9f1e7bd2ff59cd82b86453619f156f1c9c61885aab31f248f01f1f038  v6lite/tests/helpers.py
6445bac89adb5356c19d98e63e0bcd6061bba35f0f476f81269f0482037a59bf  v6lite/tests/test_engine.py
1c3eff96a4bea485c95f0c878e65b853134ab4156bbfd8dfff0483a1428dfa8d  v6lite/tests/test_g2_day1.py
d7b24b3b679a7e8455b4a4868fecc590a50784190764fde673cd08865d8df46a  v6lite/tests/test_g2_day1_selfreview.py
b36136e085f3fdb1fca27296eac7739de099c41a0f350b1a552dc1d4c5782514  v6lite/tests/test_g2_day2_prep.py
e70899902f5e8338d3b01755e1c747e0fb09322f32c283a2eac51a2f2d5ff86a  v6lite/tests/test_g2_probe.py
6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a  v6lite/tests/test_ibkr_adapter.py
dd99af7ff61b4d081b103352289bdf2d9d8297a2c998ece9640444b612d43b80  v6lite/tests/test_review_fixes.py
3f1b96a33996c29e4a9ccec7a30b2b39cec812fe54cb63f6d432fda3238ab519  v6lite/tests/test_review_round2.py
903fca0b51ed8fee873fd5629105d248bb474de2d7aee1b9a3250adafaa6e7cc  v6lite/tests/test_review_round3.py
342c5a14b315ccec9b8da1eb6439e5a9c623f60f715df0853ed031c3ebc1892b  v6lite/tests/test_review_round4.py
fbf5d3a26b81fab54a74e728cf15154b1ce9d0f4984ad28932043c5574fee5f7  v6lite/tests/test_review_round4_selfreview.py
35d5bfac2fd8eb2c842e30e335e01def09fb7b3c8d2ac7572d6b2766db3442e3  v6lite/tests/test_review_round5.py
59a80d968d4023d9c89331730ade220b3f063b48e7f5f519a262616f21e362d8  v6lite/tests/test_review_round5_selfreview.py
aae0d16cb7a172ecc6077dcc99b078f7b2d83e432fb0dc3811bc1c29dc2162fb  v6lite/tests/test_review_round6.py
b095318087d7eb85b427332831323db18a42526efe7ba83ca201557c708cecc1  v6lite/tests/test_review_round7.py
a3589fae5996245f7246f1860875a847b6761ed9f8bab5a317ab5c7d9e5e86bf  v6lite/tests/test_review_round7_selfreview.py
c65ac1c396468aae3124ffcdc0f61b2ed483547ec1b0f47119f00950008b3736  v6lite/tests/test_review_round8.py
e927860575cb55769a910a0dfbced96b725922da6629010c894ec2d3c86b36c3  v6lite/tests/test_review_round8_selfreview.py
a5377a4f520e6c3d340cb40571f24eae1dd431be871ecfad241b0d0080424aef  v6lite/tests/test_soak.py
732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f  v6lite/tests/test_strategy_parity.py
75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42  v6lite/tests/test_units.py
91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1  v6lite/tools/__init__.py
2d0d39e449b6e0520d079bdc80d5877d6cf66f16c5626e250f4b2d22530d5aee  v6lite/tools/g2_probe.py
0bac7b48a692273b55cc859d404f406cb6c4f3d9e2b3ecce2c8eafc471c63fa5  docs/V6_LITE_G2_RUNBOOK.md
```
