# V6-Lite 第二十四批修复报告（2026-10-12）

状态：Windows 默认全量连续三次通过；本报告随 dev 快照提交，等待 Claude 复核。

## 1 基线、输入与分工

- 仅 `C:\Dev\V6Lite-dev` 的 dev；基线 `v6lite-batch23` / `bd6b8fb9ecf8a08cf9685c8eaf6a46f00be794f0`，基线 `code_sha=a17d647ba28c51e4`。
- 本批候选 `code_sha=2db6af8f04f145f6`。算法为排序后的 v6lite 相对 POSIX 路径、NUL、原始文件字节、NUL，SHA-256 前16字符；排除 `__pycache__`，包含测试与样例。
- 指令 SHA-256：`342bd728d90eb814ccec9af7beb127129b7d00d7cfea143dfa6a97df8ebd0591`。
- 脱敏回调 SHA-256：`ec08ec055af96837b0c5bd45e3ea329afd594429efdf0fbd498072e6fa265542`。
- 生产唯一实施：batch24_implement；独立测试：batch24_tests；独立只读调查/复核：batch24_investigate；root 整合并串行执行候选验收。
- 没有连接 TWS/Gateway，没有访问运行仓库、John 配置或用户 .v6lite。Claude 操作手册保持预检哈希 `8ddde6114a233241a6e9e70dfe5e861080950089e13d0eefc8b769f8ba296143`，不纳入提交。

## 2 STP echo、严格身份与回放：发现 → 改动 → 测试

发现：B23 将有限的回传 lmtPrice 复制为合同 limit_price，再将其作为 STP 身份不符。基线搜索：

```powershell
rg -n "lmtPrice|limit_price|native_price_complete|_protection_order_valid|_native_status_identity" .pytest_cache/batch24_baseline23/v6lite/broker/ibkr.py .pytest_cache/batch24_baseline23/v6lite/protection_engine.py .pytest_cache/batch24_baseline23/v6lite/protection_cancellations.py .pytest_cache/batch24_baseline23/v6lite/tp_evidence.py
```

基线位置：ibkr.py 582/855/905/1189/1224/1249（status/原生snapshot/modify/cancel/价格观察）；protection_engine.py 647–654、743/806/828（身份及减量/加量路径）；protection_cancellations.py 22/57–63；tp_evidence.py 390–406。完整调查另见 investigation.json。

改动：STP 的原始 echo 写入 `broker_echo_lmt` 审计字段；合同 limit_price 保持 None，不入账、不参与价差、数量或修改计算。account/clientId/conId/orderRef/orderId/permId/SELL/STP/GTC/outsideRth=False/STK/USD/auxPrice按分/数量继续严格核验，STP LMT 继续拒绝。PreSubmitted 作为工作单，关闭 A 空窗。FakeBroker 默认有限 echo，并覆盖 None/有限 echo 变体；不能将触发价改成回传的 340.19。

测试：真实原始 ApiPending → PendingSubmit → PreSubmitted 字段经原生 wrapper/adapter 重放；要求5秒内 WORKING、S=3、无 P0/P1、查询上限合规。快照、撤单、modify、独立价格观察及严格身份反例全部覆盖。

调查结论：[IBKR STP说明](https://www.interactivebrokers.com/docs/general/order-types/stop)说明触发后为市价单；[字段说明](https://www.interactivebrokers.com/docs/tws-api/doc/orders/the-order-and-contract-objects)说明 auxPrice 用作止损触发价；[状态说明](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/order-status/understanding-order-status-message)说明 PreSubmitted 已接受并等待触发。**本例 lmtPrice=340.19000000000005、相对340.22低0.03的含义未知**，官方资料未给出这个回传值的生成规则，未猜测为限价或价格管理参数。

## 3 查询风暴、退避与生命周期：发现 → 改动 → 测试

发现：UNKNOWN 不走稳态查询间隔，每 tick 三源查询；查询本身产生的价格观察置 urgent_pending，使 pump 跳过 sleep，反馈成连续查询。原生 ib_insync 默认45条/秒。885次/分钟、10,819消息和TWS界面/人工平仓是指令提供事实，所给回调片段不足以独立复算全会话总数。

改动：原生 Client.sendMsg 层统一每滚动1秒最多10条；openOrders（含两种opcode）、positions、executions 各通道间隔>=1秒。`[ibkr]` 新增 open_orders_min_interval_seconds、positions_min_interval_seconds、executions_min_interval_seconds，默认均1，拒绝非有限或小于1；纳入配置指纹。订单写入保持顺序并优先调度，仍遵守总预算。连接代更换清除旧队列/timer，历史发送预算不伪装成新鲜证据；旧 End 不给当前查询完整性权限。

发送间隔采用每条实际发送边界，不能沿用被审计/回调耗时滞后的旧时间。

同持仓真实核验失败按1/2/4/8/16/30秒退避，UNKNOWN/hold/冻结不解除；首次GTC不受错误退避阻挡。所有需要第二轮/额外轮证据的恢复阶段，只有整个阶段通过才清失败记录；合法人工 resume 查询也共享持仓重试门槛。完整且预期的撤单 absence 不制造核验失败、不抹掉刚取得的三源数量证据。新订单/修改代/连接代和合同绑定防止旧证明复用。查询限速 P1 每持仓只发一次。

测试：原生 wire conn.sendMsg 计数，5分钟持续错误身份、通道>=1秒、全局<=10条/秒、指数退避、其他持仓及心跳公平性；断线/reset/旧timer/旧End/重入新代队列；第二轮失败和resume旁路；原有恢复、数量及 S+T+X<=P 强断言继续保留。

注意：新配置字段改变配置指纹，既有 Live 审批应按原有指纹绑定规则重新核验；未读取或改写任何审批/运行配置。Paper状态身份规则未放宽。离线模拟不能替代下一次 Paper 原生行为实测。

## 4 卡片去重与展示：发现 → 改动 → 测试

发现：保护 P0 后，同订单 held-working 又发P1；cap 显示二进制浮点尾数；TP价拒卡缺数量；TWS模式和日报显示Gateway；2108逐条wrapper日志与adapter日志重复且可能转义中文。

改动：同保护事件P0后抑制 held-P1，普通DAY held-P1保留；cap显示两位小数但原始audit不改；TP价格拒绝卡仅完整、同上下文/同代、新鲜三源证据显示确定P/S/未保护，证据不足继续待核实；可见连接/心跳/日报标签统一IBKR，机器subject/key不变。连接类通知只输出一次可读中文，原始文本保留审计，拒单原因原文不改。[官方2108说明](https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes)将其列为行情服务闲置通知。

测试：实际 Engine → Alerter → embed/plaintext 路径覆盖 cap、P6/S4/未保护2、启动/心跳/恢复/日报、2108原生wrapper重复日志与原始审计、保护P0去重和DAY P1保留。实际卡片样例附第7节。2110 仍以 WARNING 输出一次可读中文，并保留原 ConnectionEvent 断线分类/权威及原始 reason/audit；真实 wrapper 回放测试为 `test_B24_native_2110_has_one_readable_log_raw_audit_and_unchanged_loss_gate`（`v6lite/tests/test_batch24_connectivity.py`）。

## 5 红测、回归与验收真实性

B23冻结archive仅离线导入，不改基线。有效红测：red_fixture_correction_locked为74项、48自然失败/26通过/0errors；red_presentation_attempt2为8项、7自然失败/1通过/0errors。补充日报/第二轮retry/red receipts见下表和原始txt/xml/json。按修复项核对行为断言，不把自然通过的安全反例说成红测失败。

早期夹具错误和候选失败证据全部保留：green首轮78通过/4夹具失败；修夹具后82通过；扩大focused首轮751通过/36失败。失效初期red尝试不作验红依据（路径/事件循环/原生夹具错误）；root_red_*收据说明了有效版本。没有通过重试隐藏产品回归，也没有把这些聚焦结果冒充全量。

36项回归包括可见IBKR文案迁移、旧finite-echo非法oracle、两个真正恢复/数量证据问题。真实修补已独立复核。发生真实查询失败后的旧零时间重查oracle改为真实Rig时钟0/.999/1.000001秒边界，保留状态、数量、零写入和恢复强断言。原CRLF文件保留原行尾，使用单次 `git -c core.whitespace=cr-at-eol diff --check`，未修改Git配置。

扩大聚焦第二次896项为891通过/5失败（focused_attempt2，保留原始证据）：1项仍要求可见Gateway旧文案；3项用object.__new__绕过native adapter构造，缺新查询生命周期状态；1项在首次真实核验失败同一FixedClock立即要求补量。实施Agent从合成状态库mode=ro实证首失败1秒due与迟到成交同秒；独立调查Agent从代码、FakeBroker和FixedClock静态复核得到一致结论，生产恢复分支完整，无需生产改动。测试改用真正adapter constructor加本地IB stand-in，保留跨client账户过滤、ownership和不入账断言；B20在0/.999秒要求P30/S20、三源查询和写入全无，累计1.000001秒后同订单确认S30并最终平0。13生产哈希不变，101新规格字节不变；聚合code_sha因三历史测试修改改变；fe3fc54d6cd33a54中间候选为895通过/1失败（focused_attempt3）。剩余失败是驱动遗漏：0秒priority continuation已消费优先标记，后续仅_settle不会运行到期常规核验；真实主循环每轮调用engine.tick。测试在累计1.000001秒后调用真实rig.tick（不更改私有priority、时钟或retry门槛），继续要求同订单S30和最终P0。最终验收候选为2db6af8f04f145f6；所有历史失败收据均保留。这些是确定性的夹具/驱动口径失败，没有标记或豁免为时序flake。

第一次默认全量 full_1（候选62a9b82bb52ec22c）为1558项：1534 passed / 23 failed / 1 skipped / 0 errors，立即停止第2/3次。1项是真实回归：F4持续证据失败的60秒P0只在下一次查询失败时判断，新退避使59秒失败后到67秒才能再查，60秒卡片漏发。保留原B16 60秒、每tick退出/扫描和禁止MKT强断言；新增无查询截止测试先在未修生产候选自然失败。修复将持久since/paged截止检查纳入非阻塞保护deadline路径，不加查询、不置urgent、不增加failurecount或改until，并沿用原卡片去重。完整成功证据仍是唯一清理失败记录的路径。

其余22项是原生离线fixture缺API-ready：16项B20时间测试和5+1项旧fixed-key请求测试在请求前被新增连接代际检查拒绝。仅本地fixture模拟connected/API-ready，保留真实decoder/wrapper/future及所有时间/经济/身份、late-End、sent/end、dirty-channel不发、renew和stream去重断言。进一步锁定incomplete request真实进入并产生TimeoutError（原场景确为本轮失败，未假通过）；另修正两个原宽异常可能误通过的场景：partial-row+322必须真正回放并抛精确RequestError，selfreview.down必须真正进入、抛ConnectionError且sent=end=0。本地fixture不调用connect，生产断线/代际拒绝门槛未弱化。首轮失败收据保留；最终连续三次从第1次重新开始。

B23默认集合是1455项（1454 passed + 1 skipped），此前1454是通过数。首轮1558=1455+101新规格+2张finite-echo卡片参数案例；截止补丁再增加常规用例，最终集合以全量collection inventory为准。差异核对见collection_comparison.json。

为什么之前1454项没覆盖：FakeBroker和原生测试通常把请求limit_price=None照抄成回传；旧测试甚至把有限STP echo列为非法identity，没有真实 PendingSubmit/PreSubmitted回调字段序列。新增常规变体和原生回放纠正该盲点。

执行环境：Windows本机、dev .venv、Python3.11.9、pytest9.0.3、ib_insync0.9.86；PYTHONUTF8=1/PYTHONDONTWRITEBYTECODE=1，移除优化/测试筛选等继承环境，禁自动plugin；全量集合和执行nodeid逐项匹配JUnit，测试树/脚本/用例哈希前后锁定。

|收据|结果|开始/结束PT|
|---|---|---|
|B23红：red_all_finalspec_baseline23.xml|27 passed / 74 natural failures / 0 errors|冻结archive，原始receipt附目录|
|B23红：red_legacy_retry_boundary_b23.xml|34 passed / 1 natural failures / 0 errors|冻结archive，原始receipt附目录|
|B23红：red_b15_finite_echo_b23.xml|0 passed / 2 natural failures / 0 errors|冻结archive，原始receipt附目录|
|首次全量失败：full_1.json|1534 passed / 23 failed / 1 skipped / 0 errors；停止后两次|2026-10-12T14:26:57.719919-07:00 → 2026-10-12T14:32:26.751353-07:00|
|未修生产候选自然红：candidate_red_deadline.json|1 passed / 2 failures / 0 errors|2026-10-12T14:43:01.469086-07:00 → 2026-10-12T14:43:02.816368-07:00|
|green_attempt6.json|103 passed / 0 skipped / 0 failed / 0 errors|2026-10-12T14:46:12.815734-07:00 → 2026-10-12T14:46:42.438550-07:00|
|focused_attempt5.json|947 passed / 0 skipped / 0 failed / 0 errors|2026-10-12T14:46:44.195578-07:00 → 2026-10-12T14:48:21.611961-07:00|
|full_1_attempt2.json|1559 passed / 1 skipped / 0 failed / 0 errors|2026-10-12T14:53:46.063984-07:00 → 2026-10-12T14:59:18.765340-07:00|
|full_2.json|1559 passed / 1 skipped / 0 failed / 0 errors|2026-10-12T14:59:20.563437-07:00 → 2026-10-12T15:04:52.836053-07:00|
|full_3.json|1559 passed / 1 skipped / 0 failed / 0 errors|2026-10-12T15:04:54.612121-07:00 → 2026-10-12T15:10:25.631241-07:00|

每次全量启动前重新检查PT时段、无运行/不可检查的Python进程、dev/HEAD/空暂存区和保留哈希。下一交易时段前5秒截止由 Windows Job Object 终止本次验收子进程树，只处理新建验收进程；独立审阅与隐藏CREATE_NEW_CONSOLE后代超时smoke见 acceptance_process_smoke.json、independent_review_final.json。

## 6 独立复核与提交范围

独立只读复核覆盖所有本批生产文件、原生协议生命周期、严格身份、fresh三源证据、二轮/replacement/resume退避、A/B门槛及原测试断言；收据见 independent_review_final.json。root 最终验证串行，未与代码修改并行。

仅提交本批生产/测试差异、指令、脱敏回调、本批顶层证据、报告和清单。排除操作手册、item8研究、batch23临时archive、baseline解压树、test_tmp、数据库/WAL/SHM、缓存、配置与机密。暂存前另作路径、机密与大文件检查；不push、不合并main。完成 dev commit 与附注 v6lite-batch24 后冻结，等待Claude复核。

## 7 实际渲染样例

# Batch 24 actual Alerter card samples

Source SHA: 2db6af8f04f145f6

## P1 broker query retry pacing

🟠 P1 · 券商查询已限速 · AAPL
发生了什么：同一持仓核验未通过，查询按 1、2、4、8、16、30 秒退避；等待期间不提供新的完整证据，原 UNKNOWN、人工核验 hold 和冻结继续保留。
影响：本持仓继续等待核验；其他持仓和连接心跳继续运行。
你要做什么：在 TWS 核对原保护单与持仓；保持冻结，不增加卖单或绕过核验。

## P1 working price with cap 119.51

🟠 P1 · 券商工作价格不一致 · AAPL
发生了什么：orderRef=v6l-6703ac79ee3f4e27a013 发送价=119.41 工作价=119.41 cap=119.51
影响：该标的自动重发已阻止；原账本价格保留，STP 恢复继续独立核验。
你要做什么：1）核对 TWS 原订单价格；2）由 John 处置，禁止自动跟随 cap 改价。

## P0 TP price rejection: P=6 S=4 unprotected=2

🔴 P0 · 止盈价格被券商拒绝 · AAPL
发生了什么：持仓 P=6 股 已保护 S=4 股 未保护=2 股；AAPL TP数量=2 股；发送限价=119.41；bid=119.64 ask=119.7 行情年龄=0.0s；orderRef=v6l-25a045c5aab845e9b710 orderId=1003 permId=51003 broker code=201；自动止盈暂停 position=pos-48649b942eb44834af0c stage=TP1 pause_id=tp-pause-ac59ea18b89b4fc88fc2；保护恢复仍需完整三项证据。
券商原因原文 1/1：Order price is outside the allowed price range; B24 rendering synthetic reason
影响：本持仓自动止盈已暂停；券商保护与可用卖出数量仍须核验。
你要做什么：1）核对 TWS 持仓和工作卖单；2）确认 STP 已恢复到全部持仓；3）仅在原强核验门槛满足后由 John 执行 resume-tp AAPL --position-id pos-48649b942eb44834af0c --pause-id tp-pause-ac59ea18b89b4fc88fc2 --operator John --reason 明确核验原因。没有合法 STP 完整证据时保持暂停。不要自动改价重发或点击 IBALGO。

## TWS 7497 startup connected

🟢 V6-Lite 已启动（Paper 真实下单（Paper） · 实时行情）
启动时间：2026-10-06 06:00 PT / 09:00 ET
模式：Paper · 真实下单（Paper） · 实时行情 · DU0000000 · 版本 2db6af8f
状态：✅ 正常
IBKR 已连接 · 对账一致 · 告警通道正常 · 旧 V6：未运行
入场窗口 10:00 ET 开启
报价可用 3/3 · V6-Lite 持仓：无
监控：IBKR 心跳：每 60 秒，连续 2 次无应答即告警
持仓漂移：每 300 秒对账，不一致即告警
每日健康报告：13:05 PT（收盘后 5 分钟）
扫描时间：10:00 ET / 07:00 PT
手动指令（本机命令行）：这些指令只能在本机命令行执行，不能在 Discord 输入。
status 查看状态 · report 查看日报
kill --reason 停止入场 · resume 恢复
ignore 标的 · release 标的 · resolve-intent ID · clear-anomaly 代码

## TWS 7497 heartbeat missing

🟠 P1 · IBKR 心跳无应答
发生了什么：IBKR 当前时间心跳连续 2 次超过 30 秒。
影响：IBKR 响应无法证实；行情与账户数据可能失联。
你要做什么：1）检查 IBKR 登录及 API 响应；2）本机运行 status；3）等待新的有效心跳解除告警。

## TWS 7497 heartbeat recovered

🟢 IBKR 已恢复应答
发生了什么：IBKR 已重新回应当前时间心跳。
影响：心跳告警解除；其他交易门控仍按实时状态判断。
你要做什么：在本机运行 status 并核对 IBKR 状态。

## TWS 7497 daily health: IBKR heartbeat label

✅ 正常 · 演练（不下单） · V6-Lite 每日健康报告 2026-10-06
扫描：计划：10:00 ET 信号 1 · 已入场 无
信号：10:00 ET AAPL(78)
结果：WOULD_BUY AAPL gate=ok
下单/撤单：0 次下单 · 0 次撤单
持仓与漂移：无 · 漂移：无
券商端保护：无持仓；空窗统计 A=0.000s B=0.000s
IBKR 心跳：本进程今日截至报告时: 应答 2 · 最长已验证延迟 0 秒 · 未应答 0
告警送达：本进程今日截至报告时: 发出 2 · 失败 0
行情可用率：100% 常规交易时段可用率 · 已订阅 3 · 6/6 标的样本 · 2 次采样 (本进程今日；实时行情)
code_sha：2db6af8f04f145f6


## 8 SHA-256清单

299项；机器可核验清单 `docs/batch24_evidence/sha256_manifest.json` 的 SHA-256 为 `cdc14974ff073000e5f00ae740d6fc9ddf3dfd79dc25b137d15782ba445e6628`。不包含临时数据库/缓存/运行配置/操作手册；报告自身不进入清单，避免循环哈希，交付时另报。

|文件|SHA-256|
|---|---|
|docs/V6_LITE_BATCH24_INSTRUCTIONS.md|`342bd728d90eb814ccec9af7beb127129b7d00d7cfea143dfa6a97df8ebd0591`|
|docs/batch24_evidence/acceptance_checkpoint.json|`79e076544c19606e0a923ee9f48977ba10e1c9bdca437bc5f1b27dce65f304b9`|
|docs/batch24_evidence/acceptance_checkpoint_62a.json|`1340532b3aff60eeed944b30ea42a4ce99a1419ab8631f9c32133880128566ae`|
|docs/batch24_evidence/acceptance_checkpoint_full_start.json|`d9ef65408b3e3440ec4f3160e9fa69a5bc1ae85e1d318f2b46b60a894d3eb233`|
|docs/batch24_evidence/acceptance_process.py|`f96c9e873541f6d61575f9f09b25491ede1335fd24785627d06d6b3cdbdbc08d`|
|docs/batch24_evidence/acceptance_process_smoke.json|`3e4838b25463ccd2552ba9bedbe50f08a30afa54f9ec8e0e422ebc861ef9a4a8`|
|docs/batch24_evidence/baseline_receipt.json|`b4ae2ec3d9c3cf74b8c4f576ac1762ae7786215a722116d6771f14d270de8db3`|
|docs/batch24_evidence/build_batch24_report.py|`a27e9da454a9d209bd1877ef73e53d66f952b122df7b43413a0fdc2803ed3532`|
|docs/batch24_evidence/build_batch24_report_62a.py|`6438d38ba8f951cd4c523d36a9fe682ec3f25d91d4fc6dc067129129852d3438`|
|docs/batch24_evidence/build_batch24_report_full_start.py|`58039463fb96cead0cb7e470e8d4af56368eaf71413327109c9fc2b721203e3c`|
|docs/batch24_evidence/candidate_deadline_red_source.json|`046d0102d063f696255842fdead5876ea31f5f5209dc62abc86bbe562b1cdbed`|
|docs/batch24_evidence/candidate_fe3_source.json|`97411337e14b7a64bd85f7f256e9a8c559c46c5b49099fbe7c80f03880266122`|
|docs/batch24_evidence/candidate_final_source.json|`b16e4b8ea9b884702b9f16a09e709debcd743f487c7ad0365462338d850eaa37`|
|docs/batch24_evidence/candidate_final_source_62a.json|`a5c91a53ed8607a2b3b07802c0c1485b18716202ca21d7aa10f0890c07ac5c96`|
|docs/batch24_evidence/candidate_frozen_source.json|`eb5982fa8b8f940f178c2fc9d241db7df8c716d0fc4e08fc39f8002072d5a981`|
|docs/batch24_evidence/candidate_red_deadline.json|`40a5262d7d754a9e7669f23f1b0cfc3b3c425d3dc95ef084b36c5ec096a78e2a`|
|docs/batch24_evidence/candidate_red_deadline.txt|`f8f29170f84aba0ad0bb1655e576cadd8c71b677b9d01036681be9f3cc376ef5`|
|docs/batch24_evidence/candidate_red_deadline.xml|`7e43e0241e9f41a0f04a6d7ddcb2a73813369e53178e07f5dc05a45de477d274`|
|docs/batch24_evidence/candidate_red_deadline_inventory.json|`450ad62462fe108bea814ef9c01959a8e4b2dd24078fedbe95b451e4b0a7693b`|
|docs/batch24_evidence/checkpoint_review.json|`d67be89b282ea1e52c20192f2079935e31651ef86afa626ca432532916e216d7`|
|docs/batch24_evidence/checkpoint_review_62a.json|`9acefe0d9e2b1bd7871b4e3fd2957c94230742aaf7b65fbbfc705428b783d89e`|
|docs/batch24_evidence/checkpoint_review_full_start.json|`7a1d92a6a269ccc2bdff3c5f22aaba5bb2aa8eba12e9e4cd1a60032902d3f0e5`|
|docs/batch24_evidence/collection_comparison.json|`dd29eb4255c2262940ac07e87e360f7d7d8cc91330b8c65f482a7e41d1707b25`|
|docs/batch24_evidence/evidence_tool_review.json|`2395fff3c94d956d448d6ae440d5a2716e792a404fbed42dcf63a2b282bce0be`|
|docs/batch24_evidence/evidence_tool_review_62a.json|`eaa7caf6c28804c1ef92f5a0c65e7c273441e66c612c30dc8aea7a5c6b751b2d`|
|docs/batch24_evidence/evidence_tool_review_full_start.json|`4b9ff720a233dd55772d91884aa3d3d95dbdcea4b3403ffbf0138de43dd869e9`|
|docs/batch24_evidence/evidence_tool_review_initial.json|`9ad0c292b30565025fd0a51162d2f08c4014f48b1b3c5dd76b343bb21bf19091`|
|docs/batch24_evidence/focused.json|`21c65b5e986204058ffa58f38071f4fb7250979f6075fde6e1c4999c5606e2de`|
|docs/batch24_evidence/focused.txt|`87d440347eaa040f54520a7049e56c871fd2032bbebd3b95bcb383556dc3285c`|
|docs/batch24_evidence/focused.xml|`a26a555cd43095ce62dbb1df4155da17ed4c69821422186582499d8fce1a33b4`|
|docs/batch24_evidence/focused_attempt2.json|`4727983f78ad3a9dd17f07c6df034633d6f65369bd5e6852d0cbe3cd09966675`|
|docs/batch24_evidence/focused_attempt2.txt|`b068041b67416362cc89710f28886b3817b6d1349490b43e5865289970b7bb31`|
|docs/batch24_evidence/focused_attempt2.xml|`acb9d52876de0cb8f2af43dac6e1e95e736b9f5c1ae39f447f7cbffd5045562a`|
|docs/batch24_evidence/focused_attempt2_inventory.json|`bb7cf958c58d52850b61e3d8e1b6caacae80cd23ca201967419b552440b52bc6`|
|docs/batch24_evidence/focused_attempt3.json|`ce0b5f6f7294c7e85e4716e439b7b5ac4feb4760da2b2a2d69c244e764007a0b`|
|docs/batch24_evidence/focused_attempt3.txt|`5521e1b6ca1f4b9fdb0af93f10c66acbf9fe20fb8762f6afb2caf975505964e8`|
|docs/batch24_evidence/focused_attempt3.xml|`b9b19ae9e8bba98ce180ae8e6d51bcce49a971db7934b49fe907c0c4ad9d56d2`|
|docs/batch24_evidence/focused_attempt3_inventory.json|`bb7cf958c58d52850b61e3d8e1b6caacae80cd23ca201967419b552440b52bc6`|
|docs/batch24_evidence/focused_attempt4.json|`e73d4fcbc5f5aa50b6d25c4c180aa767cbf429e5a2b1d75818eef6ce33367ede`|
|docs/batch24_evidence/focused_attempt4.txt|`a5e94f08787627b6c5cca899aeb83afdff9c614f04a995b71c7e4a5003d7c9d4`|
|docs/batch24_evidence/focused_attempt4.xml|`d820abcd26f90d7b75e69d534449c961dbbc5559842d77b3dd149e9ec13af9cc`|
|docs/batch24_evidence/focused_attempt4_inventory.json|`bb7cf958c58d52850b61e3d8e1b6caacae80cd23ca201967419b552440b52bc6`|
|docs/batch24_evidence/focused_attempt5.json|`1f84cf582457fe4a9cc2e1d302661dde2ca96e01a503c053eb7944ba81b960e6`|
|docs/batch24_evidence/focused_attempt5.txt|`ecddb33550d34d71b9cb612a8cd31dd7fe52d38787685d70e0500654252fb910`|
|docs/batch24_evidence/focused_attempt5.xml|`ce1c79831b68485c1859d78e8d73edb08ea7cb25d498e4ed747a88f274f6b714`|
|docs/batch24_evidence/focused_attempt5_inventory.json|`c5d0d76e6eac273aaa9b499c9ed954ffdc99e5bee481fcc92d367c5b5bf922db`|
|docs/batch24_evidence/focused_inventory.json|`9ad77258d6810339e7d58569fbe42874b9308a95a5d8a8f301e0d2c4868a79ec`|
|docs/batch24_evidence/full_1.json|`d9a6a3b1f1b0fc61faee9561fb083a59960987e3582459282c048a645d29f597`|
|docs/batch24_evidence/full_1.txt|`92638c5eec0ae97bd6303fda10b3ca9f9770178129367be2dc8a3feb6ca1cbbb`|
|docs/batch24_evidence/full_1.xml|`fec20235fe91b7e104a700705e593aec9fe276f0b076fce5f51bab6ed8861515`|
|docs/batch24_evidence/full_1_attempt2.json|`ed4e809b53e9a18df89631b14335192dbe88e2f3d4c0b1f6db916e0674330946`|
|docs/batch24_evidence/full_1_attempt2.txt|`d8cd9effc396148d8d11a1aa0bcebb1b944bd8e9f0ac58709962d88d6e35d92a`|
|docs/batch24_evidence/full_1_attempt2.xml|`1a61932606b7c85127474d7fdb43df337da335ce8cfb53655f5a8aefda6a83cc`|
|docs/batch24_evidence/full_1_attempt2_inventory.json|`3e798547d41a45e5279bfb8916fba6189ed89c6653b810d935a2b69901aedf8b`|
|docs/batch24_evidence/full_1_inventory.json|`9a454eeab1667d603ea7e7ec436e989b0345f6adf936bbd3c8c02c59a8866f2f`|
|docs/batch24_evidence/full_2.json|`80d2b9f1a1a1adad83921ccbcaf8073c4de31b2a5092f5bd4184357016793fc6`|
|docs/batch24_evidence/full_2.txt|`4973efcbd1fed89b9bf9d31098892a5539922f7c4d9bf16017b23ccd482e2862`|
|docs/batch24_evidence/full_2.xml|`27b3ab7c56a2962ecf84c3bccc2fd165bcedfa6c7e82e4519f040c3a3c93cbe1`|
|docs/batch24_evidence/full_2_inventory.json|`3e798547d41a45e5279bfb8916fba6189ed89c6653b810d935a2b69901aedf8b`|
|docs/batch24_evidence/full_3.json|`85d72f21e087cef8840efce794077d4fcee28b5f7906fe6ab52c2904d39b67a3`|
|docs/batch24_evidence/full_3.txt|`d212471a45e2cab2801d69e1c542435b873dc71901015d3f8329f5ca07c08471`|
|docs/batch24_evidence/full_3.xml|`812e0320686268cf64074a4564f251cf919e127da1111be4185aff16adf3fbb0`|
|docs/batch24_evidence/full_3_inventory.json|`3e798547d41a45e5279bfb8916fba6189ed89c6653b810d935a2b69901aedf8b`|
|docs/batch24_evidence/full_acceptance_review.json|`d44a3f7de6219e9721238d8f6051a8c3cd3254c8f28e769a3cf3161af94d4cb0`|
|docs/batch24_evidence/full_collection.json|`9f70798bebcb87c92bb55f710dd5cfc6f118a0e136f53ee899d26460a85a6101`|
|docs/batch24_evidence/full_collection.txt|`f8411aa25b3fcec56cfa23058a231bc2b6383b348186148b822eb5c23ca1af9f`|
|docs/batch24_evidence/full_collection_attempt2.json|`674bc2b73254b5b9fd3037dd0cd639cabb51df12a41382e3aea294a1e8171ebb`|
|docs/batch24_evidence/full_collection_attempt2.txt|`58228b2e04520df8d560f0d1eec7a95363b62b0f4f59473f57abf4093447442f`|
|docs/batch24_evidence/full_collection_attempt2_inventory.json|`95319539d8012e5473974374d088f39dd54ccb1b10951110123330ac947a55d3`|
|docs/batch24_evidence/full_collection_inventory.json|`911c9e21db4f9c125b7ec1d2cdc2fbb0675ce6373d33a875b989c6f4f957b6d3`|
|docs/batch24_evidence/g3t_20261012_order63_64_callbacks.log|`ec08ec055af96837b0c5bd45e3ea329afd594429efdf0fbd498072e6fa265542`|
|docs/batch24_evidence/green.json|`31c081c4a8f812893632e4a23d03b03ff88b39ebdbeca6dc3f778f68705e6903`|
|docs/batch24_evidence/green.txt|`f7734f957b08a0caf1aa99edc3da6c807dbbee43f9d26cbdde69d77b6eb48c40`|
|docs/batch24_evidence/green.xml|`4521799dfd7cb860afd599b44d16ce85ac744575b154f54d61db1b041fc73c7e`|
|docs/batch24_evidence/green_attempt2.json|`a9f210b7abd2cbcb9afb2453d8f8548b80d8c17ccb42966afa3c565837c87629`|
|docs/batch24_evidence/green_attempt2.txt|`ea87c10311c5074e0683e317a77da73cb01e36dfcd113157489635c83030c2ef`|
|docs/batch24_evidence/green_attempt2.xml|`96c49f34c0c81d1c6faf49326ce6797f081167afb1fe7b9eaf86469dcaea217e`|
|docs/batch24_evidence/green_attempt2_inventory.json|`4632eea238c356c6612a273189411ddeba3019c9055a7f654ca96e2adbf084da`|
|docs/batch24_evidence/green_attempt3.json|`f64ac034493b36ae69f2707c2f0c8f40971f7e500f47f3327f0c577309d8f55f`|
|docs/batch24_evidence/green_attempt3.txt|`b29909e4fe01d5699507a2e071c0706abfc296c1200ce33daa65c29cd03b19f2`|
|docs/batch24_evidence/green_attempt3.xml|`665f79645a6ffffab79e19ca3764cc0cd66c17e5f224b3f019b67e1313209856`|
|docs/batch24_evidence/green_attempt3_inventory.json|`bc73618728d59c12955a801d282ebccea4bd6372cfc40247561416b5fb6f8cbb`|
|docs/batch24_evidence/green_attempt4.json|`72b3a3eda8f6752363d73f4a9c4d60506b4caa1c4fb01eefcf1b7f8768383a44`|
|docs/batch24_evidence/green_attempt4.txt|`6a0398b8fb70f31a9fea381f267b9a2091a122a9931625c281e83f9bd5e494fa`|
|docs/batch24_evidence/green_attempt4.xml|`22dd3739e996f4be00a2e7de7d7c417ab98c29463bbb1ec57cc3dbd089932156`|
|docs/batch24_evidence/green_attempt4_inventory.json|`bc73618728d59c12955a801d282ebccea4bd6372cfc40247561416b5fb6f8cbb`|
|docs/batch24_evidence/green_attempt5.json|`9a40230a2f5dc5171c8419218d8da46cfc0fbc37ecb5cb60a2561d8b9c1472e5`|
|docs/batch24_evidence/green_attempt5.txt|`858f3aa3b2c422e3f0d8e63096c3a72ac85900831be79109ca9e47106633b342`|
|docs/batch24_evidence/green_attempt5.xml|`0cccb7a3033f1023acf8ac74e46fe4afbc899e32ad8035447688d364d513ac25`|
|docs/batch24_evidence/green_attempt5_inventory.json|`bc73618728d59c12955a801d282ebccea4bd6372cfc40247561416b5fb6f8cbb`|
|docs/batch24_evidence/green_attempt6.json|`369a057c3681c5915658824eed3c3c62e88f746582ba584375107601522877dd`|
|docs/batch24_evidence/green_attempt6.txt|`21fef262d841f7e62a0d225ba4266f8d6f60234f5d6257340e89d1282e55048c`|
|docs/batch24_evidence/green_attempt6.xml|`77068d829b3b2b9b79c3029d4eec7ee7c5a485075f9a341cbd0c1095dad5b7bc`|
|docs/batch24_evidence/green_attempt6_inventory.json|`d65b796e567db6c8f68e5ffa6f5ebdeddb743d66c2f937c15e0df9ca038e05c5`|
|docs/batch24_evidence/green_full_failure_fix.json|`54d7e2342b956b47a3d5d60b183e07a40b80eacee0693380f0c584ca554b2131`|
|docs/batch24_evidence/green_full_failure_fix.txt|`3c1333e1f3c7581374a74f09dc225c62ca1f0c2cd0d8e773958947b479b318f7`|
|docs/batch24_evidence/green_full_failure_fix.xml|`f8cfe66ff281591946c377c00ddf84289ea644bc83a2a539ccecf92fe3961169`|
|docs/batch24_evidence/green_full_failure_fix_inventory.json|`251fb9212f29118ac23b762c761e0cc96e270a9e5d94b825783bf5c78e43b2b6`|
|docs/batch24_evidence/green_inventory.json|`4632eea238c356c6612a273189411ddeba3019c9055a7f654ca96e2adbf084da`|
|docs/batch24_evidence/independent_review.json|`91753f56d7fa9d1a90f2d52af05d78179190c436d341d86dd29e0eeb62a8bc10`|
|docs/batch24_evidence/independent_review_88cf.json|`11f48fd59f3851a330bc175abcd63ed1759b811b58838280095e4a1555d35f37`|
|docs/batch24_evidence/independent_review_fe3.json|`d32df097f86228d01400435daa913d220337356454d44d68409215d37c897846`|
|docs/batch24_evidence/independent_review_final.json|`2c6adf19eef51eed3494f002afbd5a5e9483168019646cf57415867a2dff3372`|
|docs/batch24_evidence/independent_review_final_62a.json|`fbe8ad79582207369b374fc90e2bdb83fa816fdf3d371dfe26e5ffb4688b49df`|
|docs/batch24_evidence/investigation.json|`2fe1de89a4baeb415a2ad41bb5f575133517837f55babe937da02432f3f879e3`|
|docs/batch24_evidence/precommit_audit.json|`47eba0dfb287617a1b267b217a546fc126f1954594abb2e429393cc6e56791b4`|
|docs/batch24_evidence/red_all_finalspec_baseline23.txt|`ca3126f622299aecd1ac23402056d0e57e212b643e92fb0efb2b6e87df518e6a`|
|docs/batch24_evidence/red_all_finalspec_baseline23.xml|`d1bd6b23a303d39644712a6fcd5ab172438208d0f8ea9c6713001a776d0a6f5c`|
|docs/batch24_evidence/red_all_finalspec_baseline23_receipt.json|`2d948b1e7d71e1eeeae5388acdc3439b2ade50f19d9e5cdcf621f06a3ff085c4`|
|docs/batch24_evidence/red_appendix_preconditions_baseline23.txt|`c0eb414e1273edc83f64d3ae11f0af9f7d248c0eaa84420ae6f3b2f7f3a4a075`|
|docs/batch24_evidence/red_appendix_preconditions_baseline23.xml|`6d5cfd489ebaa42f0fe5dce9719dd404b0e7439931c2096a6856e8d2068364ab`|
|docs/batch24_evidence/red_appendix_preconditions_baseline23_receipt.json|`ec22b0708b78ce878463af0a987cbd2ca37dc922771bf2f261c93f39b50f12ea`|
|docs/batch24_evidence/red_b15_finite_echo_b23.txt|`b0e963a927aab2b1e27e6ac82c5f4e8e97f992910c5ee93e97756a4ab771401a`|
|docs/batch24_evidence/red_b15_finite_echo_b23.xml|`c2b0d8e6f355d255b1a4eb0f9e4e9f4b77ab9982e5a4ba468c6ffd9d9a245e75`|
|docs/batch24_evidence/red_b15_finite_echo_b23_receipt.json|`1a4bf1a099e2db4321dcc2a403ab13eee2209a3168b5ffb3e0fa14a60544ab0b`|
|docs/batch24_evidence/red_daily_ibkr_label_baseline23.txt|`4b3b5f0d8275f8f284a2e67834173b64a7f833932e19710f7cdf31e846c92e33`|
|docs/batch24_evidence/red_daily_ibkr_label_baseline23.xml|`eaf4db57e79fc1ccf228a21456e80016c50708e501b943ce95c7a73b202f4525`|
|docs/batch24_evidence/red_daily_ibkr_label_baseline23_receipt.json|`fc6b373c1292b1c4b91afaa80a05e8189fad27358a317d247dc28b2e84e9d245`|
|docs/batch24_evidence/red_fixture_correction_locked.txt|`4c308a88b6490bab917ed59540efacdd6e935fbaaf840a37089ff58630e70aab`|
|docs/batch24_evidence/red_fixture_correction_locked.xml|`4c35f2fb9c9e5ca489a820990c4f965d98a0e514ff5e94a16c02be4cf30ee1cf`|
|docs/batch24_evidence/red_fixture_correction_locked_receipt.json|`0a7e55d2633e2c31b965539276ad060d9ab062dad553ee16163808b106da2626`|
|docs/batch24_evidence/red_legacy_retry_boundary_b23.json|`a55c16604293d5178f3d4008e324c5fec0999c1fe0c3fde354d1c15f5860d6c7`|
|docs/batch24_evidence/red_legacy_retry_boundary_b23.txt|`101217dd7f332d070e63e30d55c8165a1b1c20256add471359f7cf0919649b69`|
|docs/batch24_evidence/red_legacy_retry_boundary_b23.xml|`40d4715b96c37fa35d594cfc4e74ce6846e8a3717634d70a71d4b5a7cdb2051e`|
|docs/batch24_evidence/red_legacy_retry_boundary_b23_inventory.json|`25220702a361c9c0e013ef1074e38c42b295f2789602afad18c8443de45d93a6`|
|docs/batch24_evidence/red_pacing_baseline23.txt|`02ae852590f0d918196ee08e6d516fe0c9dd0b064a70f4237a85a3551b502dd5`|
|docs/batch24_evidence/red_pacing_baseline23.xml|`a42f86dc63b12acc53117510414573609c11c42fe974b71965c7fbf07f643de2`|
|docs/batch24_evidence/red_pacing_baseline23_receipt.json|`64d095a0315f0af1bcb38b8bb6bd1d43ea7f55165c40880add7327f53b85342b`|
|docs/batch24_evidence/red_pacing_lifecycle_baseline23.txt|`00d1a2c3a1d209abeec62bc8590a983637ad5ac9c4c9617625695d30e70576f9`|
|docs/batch24_evidence/red_pacing_lifecycle_baseline23.xml|`4f910f989d8673807d7a307439a5c439e2fc6357c3777d43d649d729b0f313da`|
|docs/batch24_evidence/red_pacing_lifecycle_baseline23_receipt.json|`415addde2133342352481cd52e20fd46e374ad806e74037b139015383428806f`|
|docs/batch24_evidence/red_pacing_lifecycle_finalspec.txt|`4a91098c4d4928780dc47bd0ec3cb6943ce7d88729927980069900dfa606dd9c`|
|docs/batch24_evidence/red_pacing_lifecycle_finalspec.xml|`8780da7534aa003ffc76ebbb450af4f2ed0b51230964cbeb144cd4be31ab2b60`|
|docs/batch24_evidence/red_pacing_lifecycle_finalspec_receipt.json|`03e4f93eaf087c65e443891b4d721fd36319d379183ba65f774284afaf17677b`|
|docs/batch24_evidence/red_pacing_lifecycle_locked.txt|`991a330425c1c3b4e68dc9acd60a555973593200310a6716e3e0a6db9add0cc6`|
|docs/batch24_evidence/red_pacing_lifecycle_locked.xml|`447f3be1cc8df123a771ebc3c81b81f85b6800dbfa7f351e93d281acd9c21db5`|
|docs/batch24_evidence/red_pacing_lifecycle_locked_receipt.json|`064ce9cc4a9ff16ef3c992babf872068d22ef165d5636d197a3722be7b4a4f04`|
|docs/batch24_evidence/red_presentation.json|`655152f68483b7d718579f660991568455a8375a3869a8d158f718147d371975`|
|docs/batch24_evidence/red_presentation.txt|`5af27a0dbeb51c30e99fbcae7b683a04dd7d54b6b0e457ce311a650befd42983`|
|docs/batch24_evidence/red_presentation.xml|`86aac3c3b891fc07bb994e44fb34573ac2cd4e545c0d57bd7688c37727136238`|
|docs/batch24_evidence/red_presentation_attempt2.json|`ef5d53e11717fc6dddbc342e97d6659ddd637ae1ff25720c703663a7ac4e981f`|
|docs/batch24_evidence/red_presentation_attempt2.txt|`e7a00830acfe0fe6f721a024823d7718b5f9954a04aef1e57390631abb7f2cca`|
|docs/batch24_evidence/red_presentation_attempt2.xml|`6f4a1571f5404677e1145829cf248ff1fdc4048f32c068416923c1a49e80bdb0`|
|docs/batch24_evidence/red_stp_echo_baseline23.txt|`614cd5b6d020dcef3932726e75e21c1e36000a40b8c04ee2395c2678b62e98ea`|
|docs/batch24_evidence/red_stp_echo_baseline23.xml|`06f4c2671b66b96e29d39adf53efd9ccad18334d959cb8f20ab1e009c4917d42`|
|docs/batch24_evidence/red_stp_echo_baseline23_run2.txt|`9d3d6e1fb800024309b5729d0b497b117547ab0b44552b7da0a9fb07f8623de9`|
|docs/batch24_evidence/red_stp_echo_baseline23_run2.xml|`5b976a13e25a2ae247eff78de3be01452dacd9824b793640ea5e6ba510bc19e6`|
|docs/batch24_evidence/red_stp_echo_baseline23_run2_receipt.json|`9666b9d435c58fa2b248fb998b7c0f8986d5fcfbc57e5644087684f45529edfd`|
|docs/batch24_evidence/red_stp_echo_baseline23_run3.txt|`58e792acce8f26cf5737e68124acc9477666bc07ea6c438c3ae619de40798eb7`|
|docs/batch24_evidence/red_stp_echo_baseline23_run3.xml|`90aa880d9eead548c6c445f77965408d1506e9143c023a4440bb50b978e41b72`|
|docs/batch24_evidence/red_stp_echo_baseline23_run3_receipt.json|`9666b9d435c58fa2b248fb998b7c0f8986d5fcfbc57e5644087684f45529edfd`|
|docs/batch24_evidence/red_stp_echo_receipt.json|`dee76303f073897805d8df61740a61586b6eefdd75ae4ddda741553abb725698`|
|docs/batch24_evidence/red_stp_pacing_finalspec.txt|`41326bd527ff9a74abbc6eb7fbef5531ff57d9ca31a7bd6a7c0b568ecf760c0c`|
|docs/batch24_evidence/red_stp_pacing_finalspec.xml|`1e1d4a079ee454786dc0531fb462e559cc8ebc625063664cea64cca9cda17544`|
|docs/batch24_evidence/red_stp_pacing_finalspec_receipt.json|`f09ddc10b6b146c55daf6c1c6d1b705581751b19cd376703c85b7b6a62fb230b`|
|docs/batch24_evidence/render_samples.json|`a76b80111a0a8a745dbd5643110c26b49df0f95928387f19b2e984a7be8050ca`|
|docs/batch24_evidence/render_samples.md|`fc79a8a2256656089bfa93e484c6135ab80753c02751860681f19ddfe04601fa`|
|docs/batch24_evidence/render_samples.py|`f9d92506ed7d5e173f212cb6dca873a8fd5e7d51be57acea84d6094c25786ac9`|
|docs/batch24_evidence/render_samples_62a.json|`4e7247a692c61769a97871194d247e8aae6215aaf55bfe2b240789a7cc9b749f`|
|docs/batch24_evidence/render_samples_62a.md|`d7ae9494405c27bacc1458f56b39f669230118486850fe34990de8598c176495`|
|docs/batch24_evidence/report_draft_62a.md|`36ee6910d06406a98e585b338670287898bea184bde659769d7fc1d6caca97f8`|
|docs/batch24_evidence/root_full_acceptance_review.json|`2c2e86d9f491853f1b825cec68b2655f54ba72dac669638b64c89a6c77ead86b`|
|docs/batch24_evidence/root_red_all_finalspec_review.json|`96071090bdc99a3707d049bc7686979f505f212152d5f1db3f2f31eaf6cefb4e`|
|docs/batch24_evidence/root_red_finalspec_review.json|`dde43718f73a9e59cd552eca68d1321dd3d541b45b336fb1424aec2295254b98`|
|docs/batch24_evidence/root_red_fixture_correction_review.json|`550a24f848053776fba049cd8b998940e7fd5b9095b368586b299a0319a6da4c`|
|docs/batch24_evidence/root_red_legacy_retry_review.json|`5dc90855611490b375a7142fad059d69d9ff7d066b6fa9bd225d8d21309786a5`|
|docs/batch24_evidence/root_red_lifecycle_locked_review.json|`1ad4150b469bdc6cfa380e60895467e5958ae6bf1e78545783ceeb3860bf47f8`|
|docs/batch24_evidence/root_red_lifecycle_review.json|`f8cbd2d0a93434b5b14932734ad0b39039d51d859f9f11b667d6147657845393`|
|docs/batch24_evidence/root_red_pacing_review.json|`4fc82c63a5d00abd81af9cbda08c9dfd33f9dbbfe86480c8c84fc31c53290939`|
|docs/batch24_evidence/root_red_presentation_review.json|`c45b9199cbead490a878e80f658a34970a822e88eda6d6956eb50afb5cf8dbf0`|
|docs/batch24_evidence/root_red_stp_review.json|`7088981cd93871f5a336a82a7d35944c3be956e8c39822ade8644d2041647efc`|
|docs/batch24_evidence/run_batch24_checks.py|`fe610fb9dcb79215c4d6bcdfc1e2554b1d91a81c09703196606a501e6e2a0108`|
|docs/batch24_evidence/run_red_batch24.py|`8b110d491a1d3be73e962cfd1dcb7a6af481a6ad929cf0a285676f8594944394`|
|docs/batch24_evidence/sha256_manifest_62a.json|`cffc2f41c8bb5e73f09a3022849355d18b653023439e30361fadf83a8a6a2205`|
|scripts/tests/console_shutdown_child.py|`3732ac606a911fc34f4c64a631f7672d55b5e8b96eaf50c8346e9daa6518be6a`|
|scripts/tests/console_shutdown_probe.py|`77bbeba33b1f4d94027cf30dddac46e845edd9e7a9d3672f20af63e918e99114`|
|scripts/tests/test_batch21_entry.py|`b367e6cab0ad80d8901e103ba1459b08a0ea4555d8506959a7630963ac18e470`|
|scripts/tests/test_v6_launchers.py|`fff766c38064b7ad5421eadd9639f363e06cfa5a6e6ecb1448a4c22c002b270c`|
|scripts/v6-user.ps1|`55978f6fdb9f50c2dc66cf5731436d675fa7b95021d8e7ac3911d2fe2825f9e8`|
|scripts/v6.cmd|`e6303971e31af8b57e050506ddae1c71516efc3f7c523850438fba3028fcc6a8`|
|scripts/v6.ps1|`7a8a0794cee4efd5b4d151d07ff51a8ba3a0c7a7c4e814ba0073c316d8b3fb15`|
|v6lite/README.md|`4ad81212d1863b4fc11dc87d8bc8c32ea272e95563095243292ed615ced31fd9`|
|v6lite/__init__.py|`26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39`|
|v6lite/__main__.py|`1284cf8153eda67e74c6acf250796304c6a2390e7a91dabc6fce3f6f40c93a00`|
|v6lite/alerts.py|`682d46286bf45b041c41624544ebd97807530f5f5f1868c2b6359b5db58f6940`|
|v6lite/approval.py|`70a3c24325b95216bb307dd261b60ccff88f0f13da3e2f94d6344e92f07b41e1`|
|v6lite/broker/__init__.py|`2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24`|
|v6lite/broker/base.py|`f6e52937d5e10cb0d1853ccfbce35cf47eceb74080ae6121a34577e3bad11e6b`|
|v6lite/broker/fake.py|`1a2b8040abcb629e8d73df613c5bbf662f75ca3073e303f64faac585076dc864`|
|v6lite/broker/ibkr.py|`d2bb5f7372feb598a515b9b2bde06d3cbdb90296736b08edb015716778eb09bb`|
|v6lite/broker/pacing.py|`08a81aff78939b7b1d6068e13650707173441cad51784ffbf870be5646672b17`|
|v6lite/clock.py|`0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1`|
|v6lite/config.py|`0318237f52b4306cf56378c8c51eb16c4f168083afe7c34d2132e21687282275`|
|v6lite/engine.py|`87d554c0c9e419d4d7e423abf71fab4a6d51c3a540a0a433154ebb57b2042577`|
|v6lite/examples/live.toml|`c156a0d200c91931a39e90cb5279248b85376f2f59f772a54d8b657134830820`|
|v6lite/examples/paper.toml|`3dd7901f2af4dac79ff43bf4d1ff28d774b3adc590f2dfc54ee87c1058c5bad3`|
|v6lite/examples/paper_delayed.toml|`5c53d585657090a088d6d3fba98f811367e89924a106e9948d82c8f0dba29e54`|
|v6lite/examples/paper_g3n.toml|`5498230cbfe0c670a3ef3c6208327ef357c65f65e6b0b773bd5c544764b2a829`|
|v6lite/examples/paper_test.toml|`00ca15f04fe0975b0cad7ca7ad57436b62271563d7ae669147b9be0ed41bbb3b`|
|v6lite/execution_time.py|`6767aee2d823eccc2387106f803b38521bce94e71ec082fe6dd021918ee9f300`|
|v6lite/exits.py|`1de7eb5637ee3c6abd1784570846d0edf8f91739ee97e2bb6e0b6651e34de093`|
|v6lite/market_calendar.py|`1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2`|
|v6lite/models.py|`4d8ea31dd03a64f7169f3ee08f4fb4bbeffb47f933422043766506c7f7d573a4`|
|v6lite/protection.py|`fa4be7ec8e88bb2ee2c5cc77be2a6249725ff1bcd80e2865d056bba345d3f40d`|
|v6lite/protection_cancellations.py|`201be298237fb25a3af0f374d047964eeeb6f07024763c03af99ee4c6b43da8e`|
|v6lite/protection_engine.py|`222a9cd0461690705684f742dd66a754af23f5ab5c3575d870a4494db6088fe9`|
|v6lite/protection_incidents.py|`d2bfad41ee00515fa4e9e961e30ae9d965d6b849b4541183e87451fb134cfdc4`|
|v6lite/reconcile.py|`4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1`|
|v6lite/report.py|`96b07cede3ba71749d9ee366b98b726ddbbaa8dd842ff0de0b2653c9af132f48`|
|v6lite/risk.py|`abc521f671e878a0ab5690e1fa178dc872d29b5396b86eaeb5f016be1594c687`|
|v6lite/sizing.py|`c9b9cc4a116a7f61d9e660dd885d280ec2a9262671f2c3c8ab5c1515724e171e`|
|v6lite/store.py|`56cc4454b612aa96a798aa0e95a3ead3646bf0a9b5fe921733b1de3989d4c411`|
|v6lite/strategy.py|`9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e`|
|v6lite/tests/__init__.py|`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`|
|v6lite/tests/conftest.py|`aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb`|
|v6lite/tests/helpers.py|`0c28d019b52b61f5d0b477bd7b202c409ff6761a4b4830b70c77667a3d9787d6`|
|v6lite/tests/test_batch10.py|`5b875c8df97861e22427223e7065a74bddf04a5da1c7115b431871369d177c47`|
|v6lite/tests/test_batch11.py|`fb7f26a0bd1969400f2d769a0d65f4c7d3a18f0162332fd46ca9f1ed99c260f8`|
|v6lite/tests/test_batch12.py|`3ee799aeb490ddacf9cf6960c1a58d2a5f7865f58182be205f98feb55fbc4bbb`|
|v6lite/tests/test_batch12_v2.py|`8b736302d3c595ca1c803923a2fe203ae614b2004a6b1f4e2f048b00a95cf37b`|
|v6lite/tests/test_batch13.py|`791052e685d4a4b8e798dea59b6bfcc81d3e6d606236af2ea9716452fe75d046`|
|v6lite/tests/test_batch14.py|`5087c53709f4208d5dc4fbe9f74b100d50ebfb2060ccf9c9834358d2c3c7b257`|
|v6lite/tests/test_batch15.py|`79cbe2fb90a58a477ca8da0e264fabfca0fcec01320e3cf4196f7e3e983eeaf5`|
|v6lite/tests/test_batch16.py|`dcd5e3932511c625596dee9dad5708d19a4c6566d860e11006458e3384b766f1`|
|v6lite/tests/test_batch16_card_deadline_copy.py|`d9c3c8f080748838a81392b1a12ca03347685a1be4692ae9159471ff2cc56a51`|
|v6lite/tests/test_batch16_f5_copy.py|`eca4e85d176cdb5a726bc4e5bec2f2b6bd6869ff726a6b647c16548b4ab94bb0`|
|v6lite/tests/test_batch16_f6_account_evidence.py|`731efaff8b825ee227652fd7708d581d1ce42eb638086dc007980d9df9e05710`|
|v6lite/tests/test_batch16_f6_global_mismatch.py|`86c542de449c2adab7cae4d2bc476e5108ef7ef14d9c2f45686a2404a3b9d478`|
|v6lite/tests/test_batch16_f6_release.py|`15c23d799539945057095f56bad2e8ba8a32974383eb8d778fba0f57ede2cb96`|
|v6lite/tests/test_batch16_f6_safety.py|`9e2464cc21430b8b4e27dd8982b1a5aee0b554a5e0698691df3156715ad65b9c`|
|v6lite/tests/test_batch16_f6_steady_fairness.py|`e7ae2a84c08b2adadb1aa9994e835b5504db581401eecbf2bfd14678e984915d`|
|v6lite/tests/test_batch16_f6_steady_timeout.py|`733347eda5814cbd2a14f71a717c88274bdd51376cada2c9bceeab3fd2908c48`|
|v6lite/tests/test_batch16_f7_price_flip.py|`b2f9cf68a543999d08ab0567e6e6a5663f257d2525c48430f953de8bacfbc5dd`|
|v6lite/tests/test_batch16_f8_backoff.py|`af5c1b6c62872c84536b222255c9a3b4229e2d7bf4ce1c4b55b1d4a780aa3539`|
|v6lite/tests/test_batch16_gap_evidence.py|`328269e2c8fa8a0547d1d16a5904a4d6cc5462f13b093b9e6c09eb87bd4be8ba`|
|v6lite/tests/test_batch17_alerts.py|`dfc6a85711add46c7a6e8b966c3f75f38f2bbbc7bee1b3f827882d3d9ddba5ff`|
|v6lite/tests/test_batch17_equivalence.py|`ecb49e23e9cbcd6ea2803aacb4b649d8b8794b1c79e65f266141c655efdfca8a`|
|v6lite/tests/test_batch17_g8.py|`4e21318f45dc6293fc4fbdc0f1c5be7c9d845782bf405fff8c6a51504bae5875`|
|v6lite/tests/test_batch17_heartbeat_account.py|`b825c206464649efa334daa40c7004f2f45657d6501c1330895a69c97fdf932e`|
|v6lite/tests/test_batch17_l3_recovery.py|`736891fd02d99b2337fffca283fbddf53de7d8fce8294d6a089175cbd317cd3c`|
|v6lite/tests/test_batch19.py|`cefff371b7c9101fff87583517d11a8f81675f0a549cb2007c19099b3d9e239c`|
|v6lite/tests/test_batch19_funds.py|`f9c1ae564a76752edcb60b3293eec5a4b2ac15657b4e34258ba814cf4a78dde4`|
|v6lite/tests/test_batch19_protection_equivalents.py|`12def834c0ef3a32bf71331473a47daae9e7d2b4309347e177784421cdeec303`|
|v6lite/tests/test_batch19_review_edges.py|`262a24defa92162552f14d80dfc1eb45d673e7a538a7c0ead745b7649515f7d6`|
|v6lite/tests/test_batch20_card_provenance.py|`7187685c885a24587a40ce7745d3abd0601255c97414554493482cc3592cf03e`|
|v6lite/tests/test_batch20_execution_time.py|`8a671005db02f0071dd1767a742608b61757b0882dc39cc174952d9d6d405f2a`|
|v6lite/tests/test_batch20_protection_equivalents.py|`163913342d65b7785ed15df3377d7ee90c65dfd7553b6714b9255173b11cca7a`|
|v6lite/tests/test_batch20_protection_incident.py|`50754a15d9b88b9c71d6c6a1b139cb352448ab885109c6587b4b8ab9c81570b6`|
|v6lite/tests/test_batch20_template.py|`8c6ed584a5f435fe81542df99ce8c7a1d040f342583b733fe65356af352b5230`|
|v6lite/tests/test_batch22_cancel_namespace.py|`b513c130a9045147fa3f77c3afb10d1140b17207995143866523fc9de83c532f`|
|v6lite/tests/test_batch22_config_broker.py|`7f29f6e653f648c6d90d3cecfa954d372cf3e4eaff257ff7e8737902cd752ab8`|
|v6lite/tests/test_batch22_item9_broker_cancel.py|`04e8b03d1a58770679d8594fee67d3a8ba2f4479ce322fe673dc62fed2099dc5`|
|v6lite/tests/test_batch22_pending_stop_audit.py|`f0bf9fe05cee399ca62892222ef09cd069bf8dc27a7179978e7434b7235e0696`|
|v6lite/tests/test_batch22_protection_lifecycle.py|`6917101441adb4b8981424290db03eb164d8ad15dd3d19b34e1b51e294c7eb68`|
|v6lite/tests/test_batch22_schema_audit.py|`374ae51b92eee5ea1e48ff4ca145422f1d106b0d33cf8f94b57d6261b24c7927`|
|v6lite/tests/test_batch22_snapshot_cancel.py|`69e08ccb4de46090a2315c2c0bc828e8ceee93f5d17fac689f11c067840a1d43`|
|v6lite/tests/test_batch22_trigger_incidents.py|`a3c1ad8c925c53ce95577de7ee707093b98ff74468ed13cda66da7e30cc8db17`|
|v6lite/tests/test_batch22_trigger_replacement.py|`80c284c3bd1b41ec47c841f344ca03726c8da9569433d0dfafec805bf861a90c`|
|v6lite/tests/test_batch23_broker_diagnostics.py|`f5bcb0a7ab3d199123dbbbfcdc2d562c137d0626f85f5196375a1e047ff9d37e`|
|v6lite/tests/test_batch23_contracts.py|`628182e1be0d8081eadf7dd818d39b19f9dbc7a3e5837db7e2120a22d37ebcb4`|
|v6lite/tests/test_batch23_error_provenance.py|`fcf063c69658242b034f401e0a0a65382c53e542edbda716679962086f54345b`|
|v6lite/tests/test_batch23_recovery_diagnostic.py|`ad3ea825f387334a0add08f8d6085f6fd9d36eb64f2e48ebafd51f4e2c30b5be`|
|v6lite/tests/test_batch23_terminal_fact.py|`9629999e9099ec51860f412340411bb3aba2848c218c3dbea0240c7a73bd8b0a`|
|v6lite/tests/test_batch23_tp_evidence.py|`3b4977815ab586db706f5ed708d79e7c5e0f2304ee3059f7f2f55b22b992d9c3`|
|v6lite/tests/test_batch23_unprotected.py|`35040f97659ed5adb5ffc30875b67ee63b847c9f7065a07e33f0c37292bbddb6`|
|v6lite/tests/test_batch24_connectivity.py|`8ed05fdc02598bf9433734a2785be7951840ab505160f5fc9e568e51cb6dc5d0`|
|v6lite/tests/test_batch24_daily_labels.py|`55f7f5112a393deee55e8b9865338a1205eb5240418280a0fb1e1c336b4909cf`|
|v6lite/tests/test_batch24_evidence_deadline.py|`1dccecef16d493e3f37c61d4266ac5ca21a73ebba7b04dfdd5e67901dc0bc3e7`|
|v6lite/tests/test_batch24_pacing.py|`8b425b9e3ce7488e4c163b3433f639f54c52b6839c6f676c82678c96885d40fe`|
|v6lite/tests/test_batch24_pacing_lifecycle.py|`de8db2b495562c51033f5c6b736eb3f7e1a34966bc960fc1cfadbb4da8154280`|
|v6lite/tests/test_batch24_presentation.py|`635597eb75ef8ec92b1e2d3bab29f7626d7cfe8d7dd51f045ccf1d4b49c2c1d7`|
|v6lite/tests/test_batch24_retry_second_round.py|`dcbdd83f3db5a1e0189d50c0648e63aed1148c2cb4fab70e371dd93897f11574`|
|v6lite/tests/test_batch24_stp_echo.py|`2fc7a6b36ef4b088a5dbf7b16fa018b4f05bc3c50baf13a2e59a194c25393541`|
|v6lite/tests/test_batch24_wire_latency.py|`5dbbb0b66c0fb45d6460dd646b7cb7076d8107bc9f9e66f785c309a0b969c12f`|
|v6lite/tests/test_engine.py|`bb8967a536a8d187fc9f1d742be17c0b4b90e193ab52731ac06d6bff8a08f461`|
|v6lite/tests/test_g2_day1.py|`cacaf5b3115d4a298fe8b3fe065d66fe70277b8ee4cd5d9643f0e2fff0ff5f9d`|
|v6lite/tests/test_g2_day1_selfreview.py|`fe84ef645d99f421c64f670aa7f883d16b5a24c8c15b4dd30a4aa8f035b5ea06`|
|v6lite/tests/test_g2_day2_prep.py|`a0db6b981992a3cf20d9f959a679c608842f4619007331bae9b26908cee3ccb7`|
|v6lite/tests/test_g2_probe.py|`ba38215d7a6946d60884d0e3dac99b641e7bcde058c4893b94cb9d62b6a6fe18`|
|v6lite/tests/test_ibkr_adapter.py|`6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a`|
|v6lite/tests/test_review_fixes.py|`eb91ef957bfd13864c2c92a5da2cf3a29ffdc10c9f5c74fe751daa8f4caecdd3`|
|v6lite/tests/test_review_round2.py|`a8fa9a8f5daca49aed8b0de1a87097f257b02048a8807e769d951f06472d6b35`|
|v6lite/tests/test_review_round3.py|`b5bf4e5c513e9d2310dc6404f147b633790cff0e894546e9176d6c26390a7427`|
|v6lite/tests/test_review_round4.py|`04cf584aa3b7e13842c1e7608d03e5f888b7a714ea073001ce85281fe1eb43cb`|
|v6lite/tests/test_review_round4_selfreview.py|`8cf2bba5d54e2e644bc016e68abfe87f0ecbe26b6e54d5a44a329c61e6dc7da8`|
|v6lite/tests/test_review_round5.py|`82c3cff1a3f5d8dba4b0735bf960666502d1d7ef6343c869b592a768fd81f109`|
|v6lite/tests/test_review_round5_selfreview.py|`86222acd134a13f3ab8937d1d7e8526b71bec2b98f6f70bbfc738df249d10ff5`|
|v6lite/tests/test_review_round6.py|`c6eabe6e953cc75ccfa7e4fa87625a86d677ef5dd2fca9975cd101b3403d6be5`|
|v6lite/tests/test_review_round7.py|`bcb2baea638b6ba6c063aba7776aaf1137c3cd1df98297a08311cdaf899994a5`|
|v6lite/tests/test_review_round7_selfreview.py|`a3589fae5996245f7246f1860875a847b6761ed9f8bab5a317ab5c7d9e5e86bf`|
|v6lite/tests/test_review_round8.py|`c65ac1c396468aae3124ffcdc0f61b2ed483547ec1b0f47119f00950008b3736`|
|v6lite/tests/test_review_round8_selfreview.py|`e927860575cb55769a910a0dfbced96b725922da6629010c894ec2d3c86b36c3`|
|v6lite/tests/test_soak.py|`d84df42bcdc3ce0e505a8f930b2752ec2721cc307fb364388be210131c4fad47`|
|v6lite/tests/test_strategy_parity.py|`732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f`|
|v6lite/tests/test_units.py|`75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42`|
|v6lite/tools/__init__.py|`91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1`|
|v6lite/tools/g2_probe.py|`bb1be0098415bc624b0f6d1a31b20f832d1e481e8155b4565c7f6841aa2f2d16`|
|v6lite/tp_evidence.py|`d75d3db409f3b6c3a91524c690553dee2cc098e833033ea284af0d29e0f5d2eb`|
|v6lite/version.py|`9aa52b23a3c8c08801f9d86c46ae1090a28c78dbde5d5b4b2923347bc74ece45`|
