# V6-Lite 第十批整改与复核记录

日期：2026-10-03。依据：`docs/V6_LITE_BATCH10_INSTRUCTIONS.md`。本批属于代码修复及 broker 边界工作；真实 Gateway 验证仍留在 G2，由 John 启动。本文中的测试只使用隔离的 FakeBroker、Fake IB 与临时状态。

## 执行边界和结论

- 写代码前，实施 Agent 和测试 Agent 分别用 Windows `Get-CimInstance Win32_Process` 检查 python/pythonw 命令行，匹配 `v6lite` 的运行进程均为 **0**；检查结果只报告数量，未输出命令行、环境变量或机密。
- Q-A、Q-B、Q-C 在写生产代码前并行调查。生产代码只由一个实施 Agent 修改；测试 Agent 只修改测试。独立 Agent 对 F1 的全部订单写路径作了两次只读静态复核；最终源码编译、聚焦测试和三次全量测试由主 Agent 串行执行。
- 未运行 `v6lite run` 或真实 `g2_probe`，未连接 Gateway；未改 `.env`、`state/`、用户目录中的运行状态、旧 V6、`AGENTS.md` 或 `docs/codex_memory.md`；未 stage、commit 或 push。
- **代码及离线测试验收通过；这不等于 G2 有效日或 G3 放行。** 真实行情、账户推送和 Gateway 行为仍要由 John 在 G2 只读运行中验证。

## Q-A：dry-run 分支逐项盘点

以下以第十批最终代码行号标注。`读`指 broker 查询/订阅；`写`分别标注 broker 订单写、intent/计数或本地状态写。原第九批在入场挂单查询、账户重订阅和最终 gate 前提前返回，本批仅放开这些只读验证，保留订单、退出及恢复边界。

| 最终位置 | 第九批 dry-run 分支所跳过或替代的行为 | F1 后裁定 |
|---|---|---|
| `engine.py:164` | 状态角色标为 dryrun | 保留独立角色；非 broker 操作。 |
| `engine.py:211` | startup 不接管并恢复正式订单：broker 证据读及 intent 状态写 | 保留；演练不拥有正式订单。 |
| `engine.py:221` | 启动消息标识 dry-run | 保留。 |
| `engine.py:231` | 不做 client-id 更换时的正式订单查询、状态绑定变更 | 保留；不接管其他 client 的订单。 |
| `engine.py:303` | tick 不解析既有 intent（证据读及状态写） | 保留。 |
| `engine.py:393` | 不入账 Fill/Commission/Status 事件（账本写） | 保留；连接事件及异常仍处理。 |
| `engine.py:573` | 重连不恢复正式订单 | 保留；普通对账仍执行。 |
| `engine.py:597–613` | 第九批 dry-run 不做 210 秒后的账户流重订阅（只读订阅） | **改为执行**；仅 RTH，非阻塞 `reqAccountUpdates(False/True)`，未收到新值前不刷新资金时间。 |
| `engine.py:694` | 正式订单孤儿不写本演练账本异常 | 保留，只记 dry-run 观察。 |
| `engine.py:794` | 不调用 broker cancel（订单写） | 保留；所有撤单汇入此守卫。 |
| `engine.py:818` | 不结算正式 intent（broker 证据读和账本写） | 保留。 |
| `engine.py:843` | 不拉取并入账正式执行（读加账本写） | 保留。 |
| `engine.py:978` | 不因正式持仓差异启动 freeze 取证/冻结写入 | 保留；演练无正式持仓归属。 |
| `engine.py:1077` | 不执行正式持仓 release 取证和状态变更 | 保留。 |
| `engine.py:1308` | 不继续退出重试及下单阶段 | 保留 `WOULD_RETRY`；出场不在 F1 范围。 |
| `engine.py:1329` | 不创建退出阶段/排队发送 | 保留 `WOULD_EXIT`；无演练持仓。 |
| `engine.py:1631–1646` | 第九批 `_entry_evidence` 的 dry-run 提前返回跳过 `reqAllOpenOrders`（读） | **删除提前返回**；每次通过便宜预检查的尝试都查最新挂单并处理期间事件。 |
| `engine.py:1694–1742` | 第九批 `_try_enter` 的 dry-run 提前结束，无法复核报价、限价、定仓及 gate | **删除提前返回**；对通过初检的信号完成二次输入、定仓、`_entry_gate`；所有拒绝路径逐次记录 gate。 |
| `engine.py:1751–1761` | 第九批 `_send` 在最终 gate 之前返回 | **将 ENTRY 演练停点移至完整 gate 之后、intent/计数/broker submit 之前**；非 ENTRY 仍在 `_send` 入口拒绝。 |
| `engine.py:1860` | shutdown 不撤正式订单、不接管状态 | 保留，仅演练审计与断连。 |

`_request_cancel` 是唯一引擎撤单调用点；`_send` 是唯一引擎提交调用点。其它条件表达式中的 dry-run 用于角色、启动文字或只读日志，不产生第三条订单写路径。

## Q-B：延迟行情补充演练（只回答，未实施配置）

第九批代码已允许 `paper + market_data_type=3 + --dry-run`，因此在实时订阅开通前可作为**流程补充演练**，不需额外代码。模式 1→3 会改变配置指纹，但现有 dryrun 状态启动只校验 schema、账户、模式、角色和 client_id，**不会仅因指纹改变而拒绝**；其它身份/旧证据拒绝条件仍有效。`state_dir` 不进入指纹。建议单独的 `state_dir`，避免复用当日扫描标记、disposition 和报告；新目录不会自动继承原 dryrun 的 ignore 决定，需 John 重新确认。两套 dryrun 共用 `account-dryrun` 锁，必须串行。

模式 3 的价格字段时间戳是**本地接收时间**，不是交易所价格发生时间；因此“接收年龄 0 秒”不能称为实时价格。行情状态/心跳显示 `DELAYED`，日报写明“rehearsal only, prices ~15 min old”。独立的 `WOULD_BUY` 行尚无延迟标识，阅读时须同启动摘要的 `requested=DELAYED` 对照；若 John 采用此补充演练，应在当天记录中明确标记延迟数据，**不计入实时行情 G2 验收**。本批没有创建或启动模式 3 配置。

## Q-C：模式 3 的正式准入

第九批 `config.py` 仅对 live 强制类型 1，paper 正式运行原可接受类型 3。延迟价格可在接收时被视作新鲜，故会让 G3 的入场报价和限价演练失去实时实盘预演意义。F2 现要求 `market_data_type=3` 只能与 `--dry-run` 同用；paper 正式运行在打开状态、取得账户锁或连接前即报 `CONFIG ERROR`；live 仍由配置校验要求类型 1。

## F1–F7：发现 → 改动 → 测试

| 项 | 发现 | 改动 | 聚焦测试与裁定 |
|---|---|---|---|
| **F1** | 第九批 dry-run 在挂单查询、资金刷新及最终 gate 前返回，不能验证完整入场链。初稿独立复核另发现早期拒绝未逐次记录。 | 通过初检后执行只读挂单查询、事件排空、全局阻断和输入二次检查、限价/定仓及完整 gate；ENTRY 的单一停止点位于 `create_intent` 前。初始阻断、报价/资格/偏移、数量不足、查询及最终 gate 均逐次记录 `WOULD_BUY … gate=ok/refused:…`；无报价时 `LMT n/a`。RTH 资金流 210 秒后非阻塞重订阅，270 秒过期 gate 拒绝。 | 第九批原始 6/6 失败；新增早期拒绝 5 项在第十批初稿 5/5 失败，修后 F1 共 11/11 通过。FakeBroker 的 place/cancel、intents、`INTENT_CREATED`、日内入场计数均为 0。独立 Agent 最终静态裁定所有 dry-run 订单写路径不可达。 |
| **F2** | paper 正式模式 3 可启动。 | CLI 在任何状态/连接动作前拒绝非 dry-run 的模式 3，保留模式 3 dry-run。 | 第九批 1/2 失败；修后 2/2 通过，断言拒绝时 FakeBroker 未连接/下单。 |
| **F3** | 第 2 天同一行情缺失条件重复远程告警 22 次。 | 一个 `MARKET_DATA_REPEAT=2h` 常量；启动一次、开盘一次、RTH 未变每两小时、标的变化立即、非 RTH 仅本地日志、消失 resolve；其它告警节奏不变。 | 第九批 3/5 失败；修后 5/5 通过。 |
| **F4** | 无运行代码指纹，无法区分中途换代码。 | 启动时对 `v6lite/` 所有非 `__pycache__` 文件按路径排序、以相对路径及内容计算 SHA-256 前 16 位；同一值用于启动行、STARTUP_SUMMARY、STARTUP audit 和日报。 | 第九批 2/2 失败；修后 2/2 通过；最终 `code_sha=71c7210907b1327b`。 |
| **F5** | 历史、查询排空及长 tick 缺少完整预算取证。 | INFO 单行 `[HISTORY]` 每请求记录耗时/根数/末日；`[QUERY]` 每次 positions/openOrders 包含排空等待和 `drained`；超过 30 秒的 tick 记录 `[SLOW_TICK]` 总耗时及最慢阶段。 | 第九批 4/4 失败；修后 4/4 通过，其中查询用虚拟等待证明排空时间计入。 |
| **F6** | 远程送达只有整体健康，无逐通道耗时。 | 每次 channel send 写 `[ALERT_DELIVERY] channel=… ok/fail ms=…`；通道名白名单，异常日志仅报类型，避免 URL/token 随异常文本泄露。 | 第九批 2/2 失败；修后 2/2 通过，含模拟机密 URL 的失败异常。 |
| **F7** | 探针汇总截断调用名且账户刷新阻塞；日报只显示配置类型。 | 探针保留完整调用名、以非阻塞退订/重订阅测首个**新回调**延迟；旧 ProbeIB 测试同步迁移。日报在所有报价不可用时写 `requested real-time; no usable quotes N/N`。 | 第九批 3/3 失败；修后 3/3 通过；迁移后的探针旧测试亦通过。 |

## 独立 F1 broker 写路径复核

最终 `engine.py` SHA-256 为 `c10187e6e8a37144fb130ce847501ee8700a7035c32fc94f269fe02ac0751460`。独立 Agent 从 CLI、scan、ENTRY、EXIT、恢复、动态退出队列、撤单和 shutdown 追踪：CLI `__main__.py:177,181` 同时设置 `IbkrBroker(read_only=True)` 与 `Engine(dry_run=True)`；ENTRY 在 `engine.py:1757–1761` 完成 gate 后返回，先于 `create_intent`（1784）和 `submit_limit`（1786）；EXIT 在 1751 返回；撤单在 794 返回，先于唯一 `broker.cancel`（801）。适配器又分别在 `broker/ibkr.py:587–588` 和 610–611 拒绝只读提交、撤单，物理 `placeOrder`/`cancelOrder` 只位于 602/625。未发现第三个 broker 订单写 API 或动态派发旁路。静态复核不替代 Gateway 真实只读验证。

## 测试记录（Windows，串行）

- 原第九批 49 个 `v6lite/` 非 cache 文件在加测试前、后均与第九批报告清单 49/49 相符。先冻结 `test_batch10.py`，再在原第九批代码上运行：**24 项、21 failed、3 passed、0 errors**。按项失败：F1 6、F2 1、F3 3、F4 2、F5 4、F6 2、F7 3。证据：`%TEMP%/v6lite-batch10-baseline-final.xml`。
- 独立复核后增加 5 个 F1 早期拒绝场景，在第十批初稿上先得到 **5 failed、24 deselected**（全部是缺失 WOULD_BUY 行），之后由唯一实施 Agent 修复。证据：`%TEMP%/v6lite-batch10-early-refusal-baseline.xml`。
- 源码编译通过；最终聚焦 `test_batch10.py` + `test_g2_probe.py`：**33 passed**。
- 连续三次 `python -m pytest v6lite/tests -q`：**332 passed / 0 failed / 0 skipped**，依次 **46.37 秒、45.58 秒、45.89 秒**。JUnit 证据：`%TEMP%/v6lite-batch10-full-{1,2,3}.xml`。测试使用 Windows `venv_v6`、UTF-8、临时 pycache 与 pytest cache；没有真实 Gateway 调用。
- README 已同步为 332 tests；运行手册附录指向第十批。最终未 stage/commit。根仓库其他大量既有未提交改动不属于本批，未触碰。

## 仍需真实环境确认

John 在 G2 只读阶段验证真实 Gateway 的报价、账户推送、挂单查询耗时和告警送达；日报和启动 `code_sha` 对照本清单。延迟模式 3 只可作为额外流程演练，不能代替实时行情有效日。G3 仍须单独放行；本批没有券商端保护单 L7 的实现。

## SHA-256 清单

共 52 个文件：`v6lite/` 下 51 个非 `__pycache__` 文件，加运行手册 1 个。报告自身哈希在交付摘要中单列，以免自引用。

```text
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
92dad1bf5c05029425acb09be2b817d534a27230323c49827de879cce2e6d528  v6lite/__main__.py
4ccf06d9481012b750ef7c254851217ab990556612d0822db018e19bbecd0c91  v6lite/alerts.py
162daa7d98a2362bc29521fcf65472c5590fb282da694b5011da934d8ffc26b5  v6lite/approval.py
2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24  v6lite/broker/__init__.py
722fab4b5992c737dfcfcb86c5b74c33ba633b3b3ee561eac3c4a289abe723e1  v6lite/broker/base.py
6f8e615342f5f2d4f89a6b44fc931723f8844d54049a2e5414774f528d655d37  v6lite/broker/fake.py
61fec71cbf74d4bb124585268f9293f55d2df593af8751b4a21ea6068ed0697f  v6lite/broker/ibkr.py
0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1  v6lite/clock.py
e8e10582fbf82d3849bcca4662d039d910923528fc15a25da99155e3af9b00f4  v6lite/config.py
c10187e6e8a37144fb130ce847501ee8700a7035c32fc94f269fe02ac0751460  v6lite/engine.py
1032534d1665efed3143d97bed9f7525410772a49ee5894f2000d0bcdd2fd673  v6lite/examples/live.toml
9c8682f8fa30a91703a53994745e948bc778186688dfe843acc353a5d8df123b  v6lite/examples/paper.toml
c3b4a074f96c5a25789afb5fabf088ead3b1daddaaa1ee5477ec1399c77627e6  v6lite/exits.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
596e5a6580824ec5bea72c35d057158a0078bd1ce3643fc8b6035d68de63a034  v6lite/models.py
aa109dd37894e61e6f701c05414cbb5c3cfa140785bc5ff4c6d0e79171eabb3f  v6lite/README.md
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
2a9a6d461f254c8935cdf80e0a41f04a55c3eecca8aa14e75f63436f128f2a95  docs/V6_LITE_G2_RUNBOOK.md
```
