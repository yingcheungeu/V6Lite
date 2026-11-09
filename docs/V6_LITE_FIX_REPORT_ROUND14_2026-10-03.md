# V6-Lite 第十四批整改报告：G3 Paper 测试模式

报告日期：2026-10-03 PT；指令首页日期：2026-10-04。工作区：`C:\Dev\V6Lite-dev`，分支 `dev`（从 `v6lite-batch13` 建立）。本批不连接 Gateway、不运行真实引擎或探针、不提交或推送。`C:\Dev\V6Lite` 运行工作区不变。

## 结论与边界

测试模式只允许 Paper DU 账户，并使用单独的状态目录和 `role=test` 状态身份。`[test] enabled=true` 启动前完成配置检查，live 配置只要含 `[test]` 就拒绝。显式状态目录的末级名称须含 `test`，不可位于常见的 `paper`、`paper-delayed` 或 `dryrun` 路径；已有状态文件的角色不符会在 Gateway 连接前拒绝。任意自定义、尚未初始化且名称含 `test` 的正式 Paper 目录仍无法仅凭测试配置识别，操作员需核对两份配置的路径不同。John 本批决定的股数严格锁为 3 股；样例的 ATR 倍数为止损 0.3、TP1 0.3、TP2 0.5、TP3 0.8，倍数进入指纹。其他倍数未在本批验证，需另行复核。正式 Paper 未启用测试模式时保留第十三批的入场数量及止损止盈行为。

`test-entry SYMBOL --operator NAME` 只有在测试引擎发布 ready/epoch 后才能写入唯一控制记录，引擎最多读取一条/每 tick。请求在尝试前原子删除并审计领取，再从已完成的历史日线计算 ATR/前收价，构造候选信号并进入原有 `_try_enter` → `_send` 路径；仅跳过扫描信号的 BUY 判定。入场窗口、报价质量与漂移、持仓上限、kill、日损、工作订单、资金和费用门控仍生效。请求只在同一美东日且年龄不超过 5 分钟时有效；历史行情、订单与资金慢查询之后及最终下单边界均复核期限。启动时会在连接前清除遗留请求并轮换 epoch；正常停机、断线撤销 ready 并清除待处理请求。异常崩溃可能在状态里留下旧 ready，使 CLI 暂时接受一条请求，但下次启动会在连接前清除并审计拒绝，绝不执行。

入场数量固定 3 股且不会降股数绕过风控。原 `size_entry` 继续计算风险/仓位/订单金额/可用资本上限；容不下 3 股就拒绝。入场后以测试倍数计算止损与三个止盈价，沿原有账本写入，之后不随行情改变。每段 TP 1 股，TP3 卖余量；退出、对账、冻结、kill 与日损仍走现有路径。

告警标题含 `TEST`，中文启动卡片模式为“🧪 测试模式（仅 Paper）”并列出参数。`WOULD_BUY`、入场/出场核心日志以及本机与远端日报带 `test=1`。

**价格限制**：TP1 触发价为入场价加 0.3 ATR，但正式退出路径的 `exit_limit_band_pct=1%` 保持不变；由 TP1 触发价推得的卖出限价可能低于入场价。因此“近 TP 被触发”不等于盈利成交，G3 Paper 必须核查真实成交价、费用及账本，不把测试模式当作收益证明。L7 券商端保护单尚未实现，实盘前必须完成并复核。

## 先红后绿

首批新测试在第十三批生产代码上先执行 16 项断言，观察到 T2–T5 共 15 FAIL、正式模式回归 1 PASS。pytest 在失败 traceback 收尾陷入 cacheprovider 的 `tempfile.mkdtemp` 高 CPU，测试 Agent 终止其自有进程；**该轮没有自然退出码**，但失败断言均在生产修改前观察到。后续新增六条边界测试也先在当前修复前候选上单独红测：空白自定义目录及三种慢 broker 查询为 **4 failed、26 deselected、exit 1**；启动连接竞态与断线首 tick 为 **2 failed、30 deselected、exit 1**。独立测试 Agent 最后把完整 32 项在第十三批基线以只读导入重跑，**29 failed、3 passed、exit 1、0.65s**；开发工作区同一文件以 `-p no:cacheprovider` 执行，**32 passed、exit 0、2.18s**。

Windows 全量测试在开发工作区独立 venv、Windows Python 3.11.9 下串行执行，命令为 `python -B -m pytest v6lite/tests -q -p no:cacheprovider`（PYTHONDONTWRITEBYTECODE=1、PYTHONUTF8=1，旧扫描器路径仅供测试读取；未连接 Gateway）。三轮均自然退出码 0：第 1 轮 **475 passed in 53.77s**；第 2 轮 **475 passed in 53.58s**；第 3 轮 **475 passed in 53.11s**。

## FakeBroker 测试模式启动卡片样例

以下由 `Rig` + `FakeBroker` + `MemoryChannel` 在合成 2026-10-06 09:50 ET 场景中实际渲染；`DU0000000` 为虚构账户，卡片里的“Gateway 已连接”仅指 FakeBroker，未连接真实 Gateway。执行正常退出码 0。

```text
🧪 TEST V6-Lite 已启动（Paper 测试模式 · 实时行情）
启动时间：2026-10-06 06:50 PT / 09:50 ET
模式：🧪 测试模式（仅 Paper） · 实时行情 · DU0000000 · qty=3，stop=0.3 ATR，TP1/2/3=0.3/0.5/0.8 ATR · 版本 03665f2b
状态：✅ 正常
Gateway 已连接 · 对账一致 · 告警通道正常 · 旧 V6：未运行
入场窗口 10:00 ET 开启
报价可用 3/3 · V6-Lite 持仓：无
监控：IB Gateway 心跳：每 60 秒，连续 2 次无应答即告警
持仓漂移：每 300 秒对账，不一致即告警
每日健康报告：13:05 PT（收盘后 5 分钟）
扫描时间：10:00 ET / 07:00 PT
手动指令（本机命令行）：这些指令只能在本机命令行执行，不能在 Discord 输入。
status 查看状态 · report 查看日报
kill --reason 停止入场 · resume 恢复
ignore 标的 · release 标的 · resolve-intent ID · clear-anomaly 代码
test-entry 标的 --operator 姓名（下个 tick 完整门控）
```

## 文件与指纹

第十三批基线 `code_sha=d4b19f1d43b25448`；第十四批最终 `code_sha=03665f2bc92bf15e`。以下共 **60** 项：`v6lite/` 全部非缓存文件（含测试及合成配置样例）、既有 G2 运行手册和本次 L7 设计文件。报告自身不列，以免自引用；无数据库、运行配置或机密文件。

```text
17b8628794cf5cf5e592be52009a9dbf07ff369673074d69c36b4a91103f2cec  v6lite/README.md
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
16c49dc003ecb2c83f67a952d6f6719c1a6c3aac89b2252fbb422228016e7a72  v6lite/__main__.py
ee5552b23fcfa8a7438e4f1c5615fac38e6cf90bc84b863032e59b1654b3ff05  v6lite/alerts.py
ad2ac3f67724896e2b21e83e595a10b7984630fd2a77f8df44aa859569cb945b  v6lite/approval.py
2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24  v6lite/broker/__init__.py
33b0d797a0edc5441c4439a0cb1d780639082893f9c4c9c187de254488fd9a8d  v6lite/broker/base.py
4a45f75fb2b5b1dd99175c0f7d1efb354e05476526aac957d35ecf883c1318ce  v6lite/broker/fake.py
5e80f1d0926f079f69ab41a3b23004eb07ff3b6f87b9d13a777ebc1d23473c81  v6lite/broker/ibkr.py
0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1  v6lite/clock.py
b3933c00beb213f66cbf3f533a59308b67a3e8b994e09277a57f3f5acfbd978a  v6lite/config.py
3b5d9d22f2b69976cfa954b78d6608357e6ad35d6b1738fc349ecc46c7005550  v6lite/engine.py
1032534d1665efed3143d97bed9f7525410772a49ee5894f2000d0bcdd2fd673  v6lite/examples/live.toml
9c8682f8fa30a91703a53994745e948bc778186688dfe843acc353a5d8df123b  v6lite/examples/paper.toml
bf20981fa58c4abe886bdbb95ffe290ef821d2c60fa7d236d2cedf5d0d38e4cb  v6lite/examples/paper_delayed.toml
054a3a797b7f5549d281815e1e9349df1a9a59ae9908d1787b3ca87fb0483a9f  v6lite/examples/paper_test.toml
1de7eb5637ee3c6abd1784570846d0edf8f91739ee97e2bb6e0b6651e34de093  v6lite/exits.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
596e5a6580824ec5bea72c35d057158a0078bd1ce3643fc8b6035d68de63a034  v6lite/models.py
4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1  v6lite/reconcile.py
97468dc60bc0454864dbb668c8139e2f6122c6091ae08de8cd7977878d0eae22  v6lite/report.py
abc521f671e878a0ab5690e1fa178dc872d29b5396b86eaeb5f016be1594c687  v6lite/risk.py
c9b9cc4a116a7f61d9e660dd885d280ec2a9262671f2c3c8ab5c1515724e171e  v6lite/sizing.py
fc1e688a885f6bd42dabdfc5511a2dfb41d0a14a92288fb1e1ab46a6c74b542f  v6lite/store.py
9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e  v6lite/strategy.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  v6lite/tests/__init__.py
aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb  v6lite/tests/conftest.py
fd6c76b9f1e7bd2ff59cd82b86453619f156f1c9c61885aab31f248f01f1f038  v6lite/tests/helpers.py
5b875c8df97861e22427223e7065a74bddf04a5da1c7115b431871369d177c47  v6lite/tests/test_batch10.py
fb7f26a0bd1969400f2d769a0d65f4c7d3a18f0162332fd46ca9f1ed99c260f8  v6lite/tests/test_batch11.py
3dfc69a4f811549cb32c9c186bbfed3d24f7c1c956c3c743d9b28f6996037cfc  v6lite/tests/test_batch12.py
698c6ba35df738b5b57cfc7d4b244ce0c07040070ca612147f87093ca363cc31  v6lite/tests/test_batch12_v2.py
12a3ed033079f129e7e70f610be004c498ea6f6f0283473f60b2eb80ad819140  v6lite/tests/test_batch13.py
5087c53709f4208d5dc4fbe9f74b100d50ebfb2060ccf9c9834358d2c3c7b257  v6lite/tests/test_batch14.py
bb8967a536a8d187fc9f1d742be17c0b4b90e193ab52731ac06d6bff8a08f461  v6lite/tests/test_engine.py
cacaf5b3115d4a298fe8b3fe065d66fe70277b8ee4cd5d9643f0e2fff0ff5f9d  v6lite/tests/test_g2_day1.py
fe84ef645d99f421c64f670aa7f883d16b5a24c8c15b4dd30a4aa8f035b5ea06  v6lite/tests/test_g2_day1_selfreview.py
a0db6b981992a3cf20d9f959a679c608842f4619007331bae9b26908cee3ccb7  v6lite/tests/test_g2_day2_prep.py
ba38215d7a6946d60884d0e3dac99b641e7bcde058c4893b94cb9d62b6a6fe18  v6lite/tests/test_g2_probe.py
6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a  v6lite/tests/test_ibkr_adapter.py
9d8e912ca50fbe2dae6fa855a87773bfa0fa753d2743106abadf6b0c53da9da8  v6lite/tests/test_review_fixes.py
3f1b96a33996c29e4a9ccec7a30b2b39cec812fe54cb63f6d432fda3238ab519  v6lite/tests/test_review_round2.py
b5bf4e5c513e9d2310dc6404f147b633790cff0e894546e9176d6c26390a7427  v6lite/tests/test_review_round3.py
f17f50380fd11f5ccf2e3ae250f8910928406a75d41eda87a9e8b0cfa24d16dd  v6lite/tests/test_review_round4.py
8cf2bba5d54e2e644bc016e68abfe87f0ecbe26b6e54d5a44a329c61e6dc7da8  v6lite/tests/test_review_round4_selfreview.py
3e98f52cdb35e572995448fc6e3468cf1d86e2d35ac801a33f8c5cacc67e9680  v6lite/tests/test_review_round5.py
21d218304e6b76b03348ba01033705d412bbbb4d3cc62263c6ed2ed643452ccd  v6lite/tests/test_review_round5_selfreview.py
c6eabe6e953cc75ccfa7e4fa87625a86d677ef5dd2fca9975cd101b3403d6be5  v6lite/tests/test_review_round6.py
bcb2baea638b6ba6c063aba7776aaf1137c3cd1df98297a08311cdaf899994a5  v6lite/tests/test_review_round7.py
a3589fae5996245f7246f1860875a847b6761ed9f8bab5a317ab5c7d9e5e86bf  v6lite/tests/test_review_round7_selfreview.py
c65ac1c396468aae3124ffcdc0f61b2ed483547ec1b0f47119f00950008b3736  v6lite/tests/test_review_round8.py
e927860575cb55769a910a0dfbced96b725922da6629010c894ec2d3c86b36c3  v6lite/tests/test_review_round8_selfreview.py
a5377a4f520e6c3d340cb40571f24eae1dd431be871ecfad241b0d0080424aef  v6lite/tests/test_soak.py
732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f  v6lite/tests/test_strategy_parity.py
75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42  v6lite/tests/test_units.py
91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1  v6lite/tools/__init__.py
e756a719f9f8c1cded3d1123f67fa0097b504680589319be40619bc5d7f6763e  v6lite/tools/g2_probe.py
9aa52b23a3c8c08801f9d86c46ae1090a28c78dbde5d5b4b2923347bc74ece45  v6lite/version.py
fe8874c9fab3d93924ba16e83c4d0ece950e97956f4958ae7d19709e45855a23  docs/V6_LITE_G2_RUNBOOK.md
7d9000d6e3f1bc454edfec13b1dfdf5d08ac9f727be8e60b3b5a6ed4775003ed  docs/V6_LITE_L7_DESIGN.md
```

## 交付与待复核

本批只实施 G3 测试模式。`docs/V6_LITE_L7_DESIGN.md` 是独立的第十五批设计文件；任何 L7 订单代码均未实施，须待 Claude 复核设计后再动手。本分支不合并、不打 tag。
