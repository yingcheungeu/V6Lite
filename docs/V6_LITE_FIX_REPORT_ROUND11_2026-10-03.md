# V6-Lite 第十一批 F8 整改与复核报告

日期：2026-10-03（指令日期）。依据：`docs/V6_LITE_BATCH11_INSTRUCTIONS.md`。本批只处理 G2-D 延迟行情补充演练，不代表真实 Gateway 验证。

## 范围、分工和写入边界

- 开始本轮调查时，工作区已有 F8 草稿：与第十批 52 项 SHA 清单相比，`v6lite/__main__.py`、`v6lite/engine.py`、README、运行手册 4 项已变化，另有延迟样例和 F8 测试 2 个新文件。我们没有把这些草稿误称为未修改的第十批代码；本轮逐项复核并完成缺口。
- 调查和测试设计并行；本轮生产代码补丁**只由一个实施 Agent** 写入 `v6lite/engine.py`。测试 Agent 只修改 `v6lite/tests/test_batch11.py`，主 Agent 只更新 README、运行手册和本报告。生产/测试写入前均用 `Get-CimInstance Win32_Process` 核对匹配 `v6lite` 的 Python 进程为 0；遇到并行 pytest 时暂停写入，未终止未知进程。
- 未运行 `v6lite run` 或真实 `g2_probe`，未连接 Gateway；行为测试使用 FakeBroker、固定时钟和临时状态。未改 `.env`、仓库 `state/`、用户运行状态、旧 V6、`AGENTS.md` 或 `docs/codex_memory.md`；未 stage、commit 或 push，未输出凭证。未创建根目录 `v6lite.paper.delayed.toml` 或用户 G2-D 状态目录。

## 第十批基线失败证据

新增的 12 项 F8 验收测试在 TEMP 隔离的**第十批编译代码语义基线**上得到 **10 failed、2 passed、0 errors、0 skipped**，JUnit 为 `%TEMP%/v6lite-batch11-round10-semantic-baseline.xml`。隔离副本的 `engine.py` 源 SHA-256 精确匹配第十批报告的 `c10187e6e8a37144fb130ce847501ee8700a7035c32fc94f269fe02ac0751460`，其余 48 个未变的第十批 `v6lite/` 文件逐项匹配。第十批旧 `__main__.pyc` 的 magic、源长度、时间和代码对象经过核对；它没有 F8 的 ` DELAYED` 常量，代码对象与逆转 F8 前缀后的 CLI 源码语义一致。

**证据限制：**旧 `__main__.py` 的原始源码换行字节未恢复，故无法核对其第十批源码 SHA；上述结果是编译代码行为基线，不能称为全源码哈希验证。补充的“不可用报价标的 AAPL→SPY、可用数量不变”测试还在当前 F8 草稿上先得到 **1 failed**（应有第 2 条 P1，实际只有 1 条），证据为 `%TEMP%/v6lite-batch11-draft-rotation-fail.xml`，随后才由唯一实施 Agent 修复。

## F8 各项：发现 → 改动 → 测试

| 子项 | 发现 | 改动 | 测试与裁定 |
|---|---|---|---|
| **F8-1 配置样例** | `resolved_state_dir()` 对非空路径使用 `Path.expanduser()`；Windows 下展开 `~`，不展开字面量 `%USERPROFILE%`。 | `v6lite/examples/paper_delayed.toml` 与 `paper.toml` 的非注释配置只差 `market_data_type=3` 和独立 `state_dir="~/.v6lite/paper-delayed"`；未改 John 的配置。 | 新测试逐键比较 TOML 并验证 Windows 展开结果；通过。样例文件仅供 John 复制，未在根目录创建运行配置。 |
| **F8-2 标识** | 第十批 `WOULD_BUY`、告警标题及状态快照无法单独表明延迟演练。 | dry-run 类型 3 的告警前缀为 `[V6-Lite paper dry-run DELAYED]`；所有 `WOULD_BUY` 行按配置带 `data=delayed` 或 `data=live`；延迟模式的 `STARTUP_SUMMARY` 与心跳有 `data_mode="DELAYED rehearsal only"`，实时模式维持第十批字段；日报保留原 DELAYED 说明。 | FakeBroker 完整扫描确认 gate 标签且 place/cancel 为 0、intents 为空；CLI 标题、启动/心跳和实时回归测试通过。 |
| **F8-3 告警** | 原逻辑只在 `requested=live` 时告警，延迟无订阅可能静默；初稿只比较 `usable=N/total`，等数量的缺失标的轮换不会立即重报。 | 延迟类型 3 不因预期的 delayed ticks 发 “no real-time market data”；`no_subscription` 非空立即进入 P1；RTH 可用报价不足持续**超过** 120 秒进入 P1。每 tick 检查，沿用启动、开盘、盘中两小时、标的变化立即、盘外仅日志和恢复 resolve 的节奏。最终问题身份和正文都含具体不可用标的。 | 测试覆盖无订阅、适配器误报 requested=live、119/121 秒、同数量换标的、恢复再出现、实时旧行为；独立 Agent 逐路径复核通过。 |
| **F8-4 手册** | 原手册未把延迟补充演练与实时 G2 分开。 | 运行手册新增 G2-D 一节：John 复制独立配置，在新状态中重新 `ignore --dry-run` ABT/IBB/IWM/PANW，按命令启动；两套演练共用 `account-dryrun` 锁须串行，逐日保存 `code_sha`、`WOULD_BUY data=delayed gate=…`、`[QUERY]`、`[HISTORY]`、`[ACCOUNT_STREAM]`、`[SLOW_TICK]`、`[ALERT_DELIVERY]`。明确 G2-D 不计 G2 有效日，约 15 分钟旧价只检验机制。版本附录指向第十一批；README 数更新至 344。 | 独立 Agent 核对命令、隔离状态、锁及无效日说明；未执行这些操作命令。 |
| **F8-5 验收** | 第十批的 332 项测试没有 G2-D 行为覆盖。 | 增加 12 项 F8 测试，全部使用 FakeBroker/临时状态并禁止 IB 真实连接；保留第十批 29 项聚焦回归。 | 第十批编译代码语义基线 10 败 2 过；最终 F8+第十批聚焦 **41 passed**，Windows 全量连续三次 **344 passed**。 |

## Windows 串行验证与独立复核

- `venv_v6` 下源码编译通过；最终 `test_batch11.py` + `test_batch10.py`：**41 passed / 2.39 秒**。
- 冻结文件后，主 Agent 串行执行三次 `python -m pytest v6lite/tests -q --tb=short -p no:cacheprovider`：**344 passed / 47.33 秒**、**344 passed / 46.70 秒**、**344 passed / 46.70 秒**，每次 0 failed、0 skipped。JUnit：`%TEMP%/v6lite-batch11-root-full-{1,2,3}.xml`。使用 Windows 虚拟环境、UTF-8 和 TEMP pycache；没有真实 Gateway 调用。
- 共享工作区更早的一次草稿全量测试出现 `test_unsolicited_end_poisons_the_channel_and_renews_the_connection` **1 次失败**（`%TEMP%/v6lite-batch11-full-3.xml`），单独复核曾通过；该失败轮未计入任何连续通过序列。`broker/ibkr.py` 与第十批 SHA 相同。本轮未出现第二次；如累计第三次，遵循 `AGENTS.md` 的 fix-before-commit 门槛。
- 独立 Agent 最终静态复核：F8-1/2/3、实时分支及 dry-run 写隔离在交付样例下通过。ENTRY 在 `engine.py` 的 dry-run gate 后、`create_intent` 前返回；EXIT、cancel 和 shutdown 亦有独立早退，`IbkrBroker.read_only` 是第二道订单写屏障。

## 保留风险与 G2-D 边界

- **P2（自定义配置）：**配置允许 `scan` 超出 `tradable`，但 startup 只订阅 tradable 与持仓；延迟行情健康统计遍历全部 scan。若只扫描的标的未订阅，会在 120 秒后持续触发 “no delayed market data” 误报。交付样例的 scan 与 tradable 相同，不受影响；后续应统一监控与订阅集合。
- G2-D 不能证明实时行情、策略质量、真实 Gateway 数据路径或 G3 订单安全。John 须自行复制样例、创建独立状态并运行。券商端保护单 L7 仍未实现，LIVE 前仍必须完成。

## 代码指纹与 SHA-256 清单

最终 `code_sha=8033d0a7b3aa83e2`。`v6lite/version.py` 按相对路径排序，将每个非 `__pycache__` 文件的路径 UTF-8、NUL、原始字节、NUL 纳入 SHA-256，取前 16 位。下列共 **54** 项：`v6lite/` 下 53 项，加运行手册 1 项。报告自身 SHA 在交付摘要单列，避免自引用。

```text
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
18f3eb24e8071a6134d1bd89ee5d95d90b7fe00c3b498b94f61978921c54537b  v6lite/__main__.py
4ccf06d9481012b750ef7c254851217ab990556612d0822db018e19bbecd0c91  v6lite/alerts.py
162daa7d98a2362bc29521fcf65472c5590fb282da694b5011da934d8ffc26b5  v6lite/approval.py
2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24  v6lite/broker/__init__.py
722fab4b5992c737dfcfcb86c5b74c33ba633b3b3ee561eac3c4a289abe723e1  v6lite/broker/base.py
6f8e615342f5f2d4f89a6b44fc931723f8844d54049a2e5414774f528d655d37  v6lite/broker/fake.py
61fec71cbf74d4bb124585268f9293f55d2df593af8751b4a21ea6068ed0697f  v6lite/broker/ibkr.py
0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1  v6lite/clock.py
e8e10582fbf82d3849bcca4662d039d910923528fc15a25da99155e3af9b00f4  v6lite/config.py
accff6f06b585a9aa1ab07c17674ea93423b9f9a5e20bac4bc0b288a890e6267  v6lite/engine.py
1032534d1665efed3143d97bed9f7525410772a49ee5894f2000d0bcdd2fd673  v6lite/examples/live.toml
bf20981fa58c4abe886bdbb95ffe290ef821d2c60fa7d236d2cedf5d0d38e4cb  v6lite/examples/paper_delayed.toml
9c8682f8fa30a91703a53994745e948bc778186688dfe843acc353a5d8df123b  v6lite/examples/paper.toml
c3b4a074f96c5a25789afb5fabf088ead3b1daddaaa1ee5477ec1399c77627e6  v6lite/exits.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
596e5a6580824ec5bea72c35d057158a0078bd1ce3643fc8b6035d68de63a034  v6lite/models.py
13124c4b6d529a5476cf0718e5173add9060a7a2184a1ad54e361275fc0f72c0  v6lite/README.md
4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1  v6lite/reconcile.py
64a21d5ef5ed424f5a812dfbfbc3b076088c828f0e90560ac52884b603161a6a  v6lite/report.py
abc521f671e878a0ab5690e1fa178dc872d29b5396b86eaeb5f016be1594c687  v6lite/risk.py
c7b9000919a9d7672aead2eddab786f96064946cf6084cf6b19cf9351ae70e26  v6lite/sizing.py
6c169be23a77746a6dd90ad0e84d7550cca15bd7038b6398f3ab40b034379506  v6lite/store.py
9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e  v6lite/strategy.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  v6lite/tests/__init__.py
aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb  v6lite/tests/conftest.py
fd6c76b9f1e7bd2ff59cd82b86453619f156f1c9c61885aab31f248f01f1f038  v6lite/tests/helpers.py
557b32c63c91cab2935d30747f3f575c71c936244d718cad5c5dddcbf6b01493  v6lite/tests/test_batch10.py
a61a16a0540989290dc8e3a98091f4252f65a1d4c2973e962c02132604ec33bb  v6lite/tests/test_batch11.py
6445bac89adb5356c19d98e63e0bcd6061bba35f0f476f81269f0482037a59bf  v6lite/tests/test_engine.py
d7b24b3b679a7e8455b4a4868fecc590a50784190764fde673cd08865d8df46a  v6lite/tests/test_g2_day1_selfreview.py
1c3eff96a4bea485c95f0c878e65b853134ab4156bbfd8dfff0483a1428dfa8d  v6lite/tests/test_g2_day1.py
b36136e085f3fdb1fca27296eac7739de099c41a0f350b1a552dc1d4c5782514  v6lite/tests/test_g2_day2_prep.py
ba38215d7a6946d60884d0e3dac99b641e7bcde058c4893b94cb9d62b6a6fe18  v6lite/tests/test_g2_probe.py
6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a  v6lite/tests/test_ibkr_adapter.py
dd99af7ff61b4d081b103352289bdf2d9d8297a2c998ece9640444b612d43b80  v6lite/tests/test_review_fixes.py
3f1b96a33996c29e4a9ccec7a30b2b39cec812fe54cb63f6d432fda3238ab519  v6lite/tests/test_review_round2.py
903fca0b51ed8fee873fd5629105d248bb474de2d7aee1b9a3250adafaa6e7cc  v6lite/tests/test_review_round3.py
fbf5d3a26b81fab54a74e728cf15154b1ce9d0f4984ad28932043c5574fee5f7  v6lite/tests/test_review_round4_selfreview.py
342c5a14b315ccec9b8da1eb6439e5a9c623f60f715df0853ed031c3ebc1892b  v6lite/tests/test_review_round4.py
59a80d968d4023d9c89331730ade220b3f063b48e7f5f519a262616f21e362d8  v6lite/tests/test_review_round5_selfreview.py
35d5bfac2fd8eb2c842e30e335e01def09fb7b3c8d2ac7572d6b2766db3442e3  v6lite/tests/test_review_round5.py
aae0d16cb7a172ecc6077dcc99b078f7b2d83e432fb0dc3811bc1c29dc2162fb  v6lite/tests/test_review_round6.py
a3589fae5996245f7246f1860875a847b6761ed9f8bab5a317ab5c7d9e5e86bf  v6lite/tests/test_review_round7_selfreview.py
b095318087d7eb85b427332831323db18a42526efe7ba83ca201557c708cecc1  v6lite/tests/test_review_round7.py
e927860575cb55769a910a0dfbced96b725922da6629010c894ec2d3c86b36c3  v6lite/tests/test_review_round8_selfreview.py
c65ac1c396468aae3124ffcdc0f61b2ed483547ec1b0f47119f00950008b3736  v6lite/tests/test_review_round8.py
a5377a4f520e6c3d340cb40571f24eae1dd431be871ecfad241b0d0080424aef  v6lite/tests/test_soak.py
732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f  v6lite/tests/test_strategy_parity.py
75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42  v6lite/tests/test_units.py
91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1  v6lite/tools/__init__.py
e756a719f9f8c1cded3d1123f67fa0097b504680589319be40619bc5d7f6763e  v6lite/tools/g2_probe.py
9aa52b23a3c8c08801f9d86c46ae1090a28c78dbde5d5b4b2923347bc74ece45  v6lite/version.py
8a86429704aa2ca9a7239d90c6c320c76bef5d8c27ed167d08c7c7a08c33b700  docs/V6_LITE_G2_RUNBOOK.md
```
