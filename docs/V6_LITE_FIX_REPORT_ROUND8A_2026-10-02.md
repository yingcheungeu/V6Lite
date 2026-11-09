# V6-Lite 第八批补丁 8A（G2 第 2 天准备中发现）

日期：2026-10-02 PT。整改：Claude。待复核：Codex（可以和第八批一起复核）。

## 发现 → 改动 → 测试

| 项 | 发现（真实 Paper Gateway） | 改动 | 测试 |
|---|---|---|---|
| **D2-1** Discord 告警全部 403 | 用 PowerShell 的 `Invoke-RestMethod` 向同一个 webhook 发消息能送达，引擎每条告警都返回 `HTTP Error 403: Forbidden`，导致 `alerts_healthy=false`。Discord 前端会拒绝 Python 默认的 `Python-urllib` User-Agent | Webhook 请求改为带上明确的 User-Agent。HTTP 出错时，报错里写出 Discord 返回的原因（例如 Cloudflare 的 1010），但不写出 webhook 地址，因为地址是机密 | `test_D2_1_webhook_request_carries_an_explicit_user_agent`、`test_D2_1_http_error_shows_discords_reason_but_never_the_url` |
| **D2-2** 连不上 Gateway 时打出整段 traceback | Gateway 没有运行时，`run` 直接打印 `ConnectionRefusedError` 的 traceback | 改为只输出一行 `CANNOT CONNECT`，附上要检查的项目，退出码为 3；此时什么都没有启动 | `test_D2_2_unreachable_gateway_is_one_clear_line` |

第八批代码上，这 3 项全部失败；本批全部通过。全量 **281 passed**，连续跑 3 次结果一致。

需要在真实环境确认：重启引擎后，手机应收到 self-test 和启动通知，心跳里 `alerts_healthy` 应为 `true`。

## 改动文件

alerts.py +17/−4，`__main__.py` +7/−0，README.md +1/−1（测试数改为 281），新增 `tests/test_g2_day2_prep.py`。

## SHA-256 清单（47 个文件，已排除 `__pycache__`）

```
9a61154c313352ab3f7dc811bade2ec84cd60f54d95468eeb67fa531005bc11d  v6lite/README.md
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
d29ef169f486320d123b09b899908b2d3a6eccfabbf909e3e75d21e851bb354e  v6lite/__main__.py
2dff50958ac70abb5e6c9f0ec4ff5a3ac3ff1d49a77b23bebdaf705782d65213  v6lite/alerts.py
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
b36136e085f3fdb1fca27296eac7739de099c41a0f350b1a552dc1d4c5782514  v6lite/tests/test_g2_day2_prep.py
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
