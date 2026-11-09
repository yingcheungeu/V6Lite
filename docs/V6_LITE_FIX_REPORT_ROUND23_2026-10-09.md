# V6-Lite 第23批修复报告

日期：2026-10-09。范围：仅dev；离线FakeBroker/native callback doubles，未取得真实Paper成交/延迟证据。

- 基线：`v6lite-batch22a`，commit `d9f57dca5ed73f001f9c68052f5fe27b8abc933e`，code_sha `f27e144de9eb406b`。
- 本批 code_sha：`a17d647ba28c51e4`。
- 指令第2版 SHA-256：`04708b69d6b3c410b487fb440f97a98790ce47bc0ad8dd3b23d471dc0592860c`。

## 0 裁定原文

1. **止盈价带：** `exit_limit_band_pct` 默认值由 0.01 改为 **0.002**，更新全部示例配置。John 的决定原文：“0.2% + 报价 3 秒以内”。
   - 不做 0.1% / 0.3% 的独立对照实验，也不把它作为放行条件。
   - G3-T 直接用 0.002 运行并自动记录证据（第 5 节）。
   - 出现价格拒绝或 10 秒未成交，按本批的 P0 / 既有 B 规则处理，再由 Claude 和 John 重新裁定。
2. **B 空窗阈值不变：** 10 秒撤 TP，30 秒发 P0。Live 放行条件仍是“实测最长空窗 ≤ 5 秒”。
3. 研究报告第 4 节第 1–5 项**全部批准实施**。“券商取消保护单”已由第 9 条补丁完成（第 2.1 节）。新增第 1.7 项（报价新鲜度）和第 1.8 项（卡片措辞）。
4. **禁止事项：** 不启用 PMA / IBALGO，不使用 `overridePercentageConstraints`，不改 TWS 预设，不在被拒后自动改价重发。

## 1 发现 → 改动 → 测试

### 1.1 末股B起点

- 发现：三源查询后覆盖原始T0，查询耗时不计入窗口。
- 改动：保留首次持久化T0，缺少原始回报才兜底；查询完成不改写，阶段证据关联workflow。
- 测试：final_share_B_original_callback_clock_survives_slow_confirmation_and_drives_deadline；既有非末股、10/30秒回归。

### 1.2 异步TP终态恢复

- 发现：TP终态可能停留TP_ACTIVE直到超时。
- 改动：只接纳自有硬合同回报，立即进入TP_NEEDS_RESTORE，按最新持仓与完整稳定三源核验恢复；未知和迟到成交继续保留卖出容量。
- 测试：async_TP_terminal_enters_restore_immediately；foreign_TP_terminal；cancel_with_late_partial_fill；每次实际写前后容量oracle。

### 1.3 完整拒价P0

- 发现：普通未发出P1与原因截断不足以说明价格拒绝。
- 改动：专用价格拒绝P0包含发送快照、订单编号、code和完整券商原文；UTF16安全分段与ACK留证，独立事件不全局静音。
- 测试：price_rejection_real_P0_keeps_complete_original_reason_and_send_snapshot；ACK去重/未ACK补发；非终态其他code不授予恢复权限。

### 1.4 持久化暂停与明确解除

- 发现：价格拒绝后后续行情可能再次自动提交TP。
- 改动：按position与pause episode持久化阻断自动止盈；resume-tp仅排队审计，准确John请求且无hold/freeze/UNKNOWN，完整稳定三源确认S=P/T=X=0后消费，不在同tick立即重发。
- 测试：price_rejection_pause_is_durable；exact_episode_manual_request；wrong_episode；manual_resume_three_sources/foreignSELL/dry/hold/freeze/corrupt；CLI零券商写。

### 1.5 显式关闭PMA与价格一致性

- 发现：SDK默认值与工作价/cap证据缺失。
- 改动：LMT提交、STP提交与修改三条构造路径usePriceMgmtAlgo=False；一次openOrder hook与status诊断，先核硬合同再分流P0/P1并持久标的阻重发；未知cap不当零，STP cap不与触发价混比。
- 测试：native_LMT_constructor各purpose；native_STP_submit_and_same_ID_modify；openOrder_only；working/cap/ENTRY/PROTECT不一致；unknown_LMT_cap；STP_positive_cap。

### 1.6 按场景生成卡片

- 发现：共用样例数量造成持仓与未保护数字冲突。
- 改动：五个独立FakeBroker/native/Alerter场景捕获实际渲染；暂停样例分别列P0时与全仓恢复后的数量，避免同事件P0后再发低级别卡。
- 测试：render_samples.py与card_samples.json/md；实际通道ACK、各阶段容量oracle。

### 1.7 0.2%与三秒两次报价门

- 发现：保护TP使用旧年龄阈值和fallback mark/last，不符合裁定。
- 改动：默认exit_limit_band_pct=0.002、tp_max_quote_age_seconds=3（整数1–10、指纹）；减量前及发送前两次核有效新鲜bid/ask，仅新鲜bid向上取整到分；首次跳过仅纯证据counter/log，第二次走原B恢复，不价格暂停。
- 测试：3.0/3.01边界、缺失/负数/NaN/Inf/未来/naive时间、计数与分钟日志、bid取整、第二门恢复、dry全部入口零写；五示例与配置指纹。

### 1.8 取消措辞

- 发现：取消标题正文混用拒绝且系统诊断英文。
- 改动：按取消/拒绝事实写中文系统诊断；券商原文保持，完整与弱终态身份分别说明授权边界。
- 测试：actual_cancel_card_has_consistent_cancel_fact_chinese_diagnosis_and_exact_quantity；旧卡片溯源回归。

### 第5节 自动审计与日报

- 发现：缺少TP全过程时间线、工作价、完整原因与按bid衡量成交差。
- 改动：workflow关联减量、发送、回报、成交、撤单、恢复及gap关闭；exec去重与成交经济事务内审计，差值未知不填零，日报逐TP空窗/差值和报价跳过轮数。
- 测试：TP_send_status_fill_automatic_evidence_and_daily_bid_difference；迟到/重复exec；独立腐损辅助证据不回滚经济fill复现。

### 独立复核R1–R10纠正

- 发现：初版独立复核未通过：错误来源归因、辅助审计回滚、物理报价年龄、未保护TP重发阻断、损坏解除记录、日报截断、混合报价、过宽价格分类与系统英文。
- 改动：保留终态诊断但不赋予订单权限；跟踪查询ID命名空间；附属证据损坏仅差值未知；实际发送门重核原报价且明确未发出；所有TP遵守标的价格阻断；严格episode/持仓绑定；全部券商原因保全文；报价维度来自同一定价快照；明确价格规则分类；只翻译系统自造诊断。
- 测试：test_batch23_error_provenance.py 21项：候选自然21失败/0errors；22a对照19失败/2既有终态守卫通过；最终专项/全量及只读独立重审作为验收门槛。

### 追加独立边界复核

- 发现：无保护TP拒价遗漏P0/持久暂停，损坏定价快照仍被授权，release诊断截断，中文价域拒绝与Inactive事实卡不完整。
- 改动：对未开启保护的TP只增加持仓/订单绑定诊断和暂停，不创建保护row或赋予自动STP权限；最终物理发送核定价公式和raw快照一致；明确未发出不虚报send；release仅保留查询错误全文，原强放行条件不变；按真实取消/拒绝状态区分系统事实，券商原文保持。
- 测试：test_batch23_unprotected.py 8项，固定spec在候选和22a均7失败1通过0errors；最终110项专项、553项聚焦及全量连续三次通过，另由独立Agent审签封存证据。

### Broker异常全文诊断

- 发现：保护提交/修改/撤单、三源查询及release账户执行查询catch仅记录异常类型，原始原因丢失。
- 改动：仅补完整durable PROTECT_BROKER_ERROR/BROKER_QUERY_ERROR诊断并纳入日报，保留原raise、状态、重试和强release条件；普通通用卡摘要可通过审计追溯全文，价格拒绝及券商终态P0仍按专用全文卡路径。
- 测试：test_batch23_broker_diagnostics.py 6个真实catch场景在候选与22a全部行为失败/0errors；另三个未动态覆盖catch由独立静态逐行复核，不声称全9动态覆盖。

### Inactive+202事实分流回归守卫

- 发现：初版措辞仅凭Inactive写拒绝，而Inactive+202明确取消证据属于取消。
- 改动：展示分类按实际取消/拒绝证据，未知或矛盾保留待核实；不改变终态安全状态、身份权限、hold或冻结；原文原样。
- 测试：test_batch23_terminal_fact.py 1项：候选自然失败/0errors、22a对照通过；三证据3/0/3及31分钟零自动重挂守卫。

### 恢复错误detail在日报的全文保留

- 发现：真实恢复工作单查询异常完整保存于ANOMALY.detail，但日报的关键词检测漏掉detail而再截断160字。
- 改动：仅把诊断detail纳入完整报告渲染；恢复逻辑、UNKNOWN、operator hold、数量/身份、经济账本均保持。
- 测试：test_batch23_recovery_diagnostic.py有效run2在候选与22a各1行为失败/0errors；候选失败在日报全文尾标，22a失败在catch保留全文的control断言（旧截断200字）。最终110项专项完整经过UNKNOWN、hold、账本和零新增写入守卫；首轮预建计划夹具错误保留且不计门槛。

### 既有回归的裁定等价更新

- 发现：新增PriceObservation、禁用造价超时和真实取消/拒绝/矛盾事实与旧测试预期冲突；code0泛拒因抹除明确取消另为实际展示回归。
- 改动：仅五个旧测试文件精准更新裁定相关oracle，原身份/数量/capacity/release/零MKT/原GTC恢复断言保留；code0原取消断言不放宽，由solewriter修复展示模式。
- 测试：修前focused553=542passed/11failed/0errors；修后focused_run2同源553passed；新110spec SHA全未变。L4/F8已通过，未改它们。

### 首全量后的合法TP容量测试修正

- 发现：旧最终卖出容量测试手工TP直_send，没有新的不可变freshbid定价证据而正确被拒绝。
- 改动：仅测试改用实际TP评价/队列，合法6股TP在途后严格验证可卖14而STOP20必须被capacity拦截；不注入定价control、不绕发送门。
- 测试：首full_1唯一失败原始记录保留，不计验收；legacy_capacity目标1passed；同源110专项与553聚焦重验后重新连续full3。

## 2 分工与安全边界

- 唯一生产实施：batch20_protection_incident；独立测试：batch20_equivalents；只读独立调查/复核：batch20_time_logs；root负责串行执行、docs证据与git。
- 先核生产34项基线并在22a归档初始有效红，再授权生产修改；后来修正夹具的最终spec另在未变的22a归档重跑自然红，原始红测保留并区分时间顺序。最终验证期间各写入者暂停。
- 经济入账与核心权限门控的AST比较、release强核验分支的独立逐行复核、所有SDK构造与原因截断搜索见独立生产复核证据。release仅把80字失败预览改为全文并在两轮账户执行查询catch增加全文审计；去掉新增审计并归一化该预览叶后强核验流程与基线等价，整个方法AST已不同，不声称全方法原样。
- 未连接TWS/Gateway，不读取用户状态/归档，不改运行仓库、John配置或Claude手册；不push。
- Dev workspace contains no CODEX_DEVELOPMENT_WORKFLOW.md. Auto-review denied the attempted read of the legacy TradingV6 copy as outside this batch scope; the read did not execute. Continue using supplied AGENTS workflow rules and verified B23 instruction. No approval bypass.

## 3 红绿与Windows全量

有效红：73项，66行为断言失败、7守卫通过，0 errors；多个用例共享业务前置，不等于66个独立根因。源f27e144de9eb406b保持不变。首两轮红测夹具问题和首轮候选绿测（含未知cap、尾零显示、缺少permId的Fake回报和未推进真实tick的恢复前置问题）原始输出保留，不作为最终门槛。修正后的新测试在22a重新自然失败，才用于最终专项绿。
独立新增21项：初版候选21失败/0errors；22a对照19失败、2既有终态全文守卫通过。辅助审计旧版经济入账通过而缺新审计；候选回滚被独立捕获。与已有业务前置共享的失败不夸大为独立根因。全部原始结果保留。
追加独立8项：修复前候选与22a均7失败、1既有取消事实守卫通过，0errors；覆盖无保护TP拒价、损坏报价快照、中文价域拒绝、release完整失败原因及取消/拒绝事实一致性。新断言固定后再授权生产修复。
另6项真实broker异常诊断红测代表submit/modify/cancel、执行查询及release两轮查询。未动态覆盖的open_orders、positions_fresh与TP超时撤单catch另经独立静态逐行复核，不把6项宣称为全部9路径动态覆盖。
Inactive+202混合终态新增1个既有取消事实守卫：候选自然失败、22a对照通过；修复仅展示分类，不授予订单权限。
恢复查询全文detail日报出口另有1项候选/22a自然红；首轮误把ENTRY正常预建计划当作不应存在，改为对该计划完整等值/零物理STP写入守卫后重新自然红，首轮不计门槛。有效候选红到达真实catch入库完整而日报截断；22a有效红在更早的anomaly control全文断言失败（旧catch只保存前200字）。最终保留异常入库及日报全文，恢复逻辑和权限不变。
专项绿：110 passed，0 failed、0 errors、0 skipped；同源聚焦553 passed，另1项真实TP超卖边界回归passed。首全量1453passed/1failed/1skipped，旧手造TP缺定价证据；改用真实TP流程后重新启动连续3轮验收，未把失败算通过。

| 连续全量 | PT起止 | passed | skipped | failures/errors |
|---|---|---:|---:|---:|
| 1 | 2026-10-09T15:54:39.422503-07:00 — 2026-10-09T15:59:28.274618-07:00 | 1454 | 1 | 0 / 0 |
| 2 | 2026-10-09T15:59:29.427441-07:00 — 2026-10-09T16:04:18.647837-07:00 | 1454 | 1 | 0 / 0 |
| 3 | 2026-10-09T16:04:19.800781-07:00 — 2026-10-09T16:09:09.324869-07:00 | 1454 | 1 | 0 / 0 |

实际跳过项：v6lite.tests.test_strategy_parity.test_scoring_matches_legacy_scanner_on_identical_bars — legacy market_scanner.py not present

每轮开始前重新检查交易时间；默认v6lite/tests与scripts/tests一起执行；禁用外部pytest自动插件及缓存写入，临时合成文件均限定在dev证据目录。三轮同code_sha/用例及结果一致，串行且无重叠。

历史记录格式说明：red_22a_run5、review_red_candidate、review_red_22a三份早期红测只记录v6lite运行进程为0，未采集不可检查Python进程数或spec前后标志；不补写或把缺字段当0。原JSON以固定SHA、源码前后未变、原XML逐用例及冻结spec哈希验证。仅这三份历史红允许显式格式兼容；最终110专项、553聚焦、单项容量和连续全量三轮仍严格要求两类进程数为0及spec前后未变。封存工具的元数据筛选和历史格式修正另附证据，并纳入独立复核。

## 4 设计与保留的边界

- 自动止盈暂停按position和拒价episode持久化；resume-tp仅排队并审计，engine证据完备且全仓STP恢复后才解除。旧episode/hold/freeze/UNKNOWN/损坏控制/价差门控不能被命令清掉；不立即下TP。
- 首次旧报价只在真实止盈报价决策轮累计纯证据counter（不把未到30秒调度期的调用算作跳过），交易状态、尝试次数和保护数量不变，每分钟最多一条日志；第二次失败恢复原保护并累计原B窗口，不按价格拒绝暂停。
- positive STP cap与auxPrice属于不同物理量，单独审计；LMT cap与发送限价差异按P1/标的阻重发；未知cap保持未知。真实working aux/lmt合同差异仍按订单purpose分流，绝不跟随cap改账本。
- 旧test-tp-timeout不能再通过ask加价制造超时；原有排队control也不能覆盖新鲜bid定价。超时验证由FakeBroker持单完成，10/30秒阈值保留。
- 相同已ACK拒价事件不重复P0/P1；未ACK仅补未送达分段。Alerter独立事件直调测试只证明未全局静音，自动触发由既有回归及静态独立复核补证。
- Paper真实价域、成交速度/滑点与实盘最长空窗≤5秒仍需后续经授权演练测量；本报告的合成数据不作真实成交保证。

## 5 实际场景卡片

以下正文来自实际Alerter渲染和本地通道ACK，不手造卡片。每场景独立数量；自动暂停样例同时列P0发生时和全仓恢复后的数量，未新增同事件低级别卡。

# 第23批实际合成场景卡片

全部来自FakeBroker、本地native回调double和实际Alerter/Channel。临时合成状态位于dev；不是John账户日志。

## 保护单被券商取消：合成3股

场景完成后的测试真值（不替代卡片渲染时取得的券商证据）：{"P": 3, "S": 0, "T": 0, "X": 0, "not_covered_by_STP": 3}

```text
🔴 P0 · 券商已取消保护单
发生了什么：AAPL 持仓数量=3 股 保护单已确认=待核实（上次确认 3） 股 保护单可能=3 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实 股 可能超卖=待核实 股；券商已回报取消；完整终态身份已核验；orderRef=v6l-protect-4086a92c8f2e40eaa3f6 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；券商回报状态=Cancelled；禁止未经核实重挂；券商状态=Cancelled code=202；终态诊断与执行权限独立核验
券商原因原文：Order canceled by system: synthetic broker cancellation
影响：有效 STP 保护 S=待核实（上次确认 3） 股，未保护 P−S=待核实 股；止盈和外部卖单仅列卖出容量，不能当作止损保护。新开仓继续冻结，Lite 不自动重挂。
你要做什么：1）先按 Ctrl+C 停止 Lite；2）在同一 TWS 核对持仓和所有工作卖单，再按当前 bid 用限价单平仓或人工建立保护，避免重复卖单；3）重新核对持仓和工作单。不要点击 TWS 的重新提交/IBALGO 建议；不要自动改价重挂保护单。只有确认 0 股、无在途卖单、成交静默期和原 release 强门槛均满足后才执行 release AAPL；身份或证据不足保持拒绝，不要手改状态库、新开仓或自动重挂保护单。
```

```text
🔴 P0 · 券商已取消保护单
发生了什么：AAPL 持仓数量=3 股 保护单已确认=0 股 保护单可能=0 股 止盈在途=0 股 外部卖单在途=0 股 未保护=3 股 可能超卖=0 股；券商已回报取消；完整终态身份已核验；orderRef=v6l-protect-4086a92c8f2e40eaa3f6 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；完整工作单快照未发现原 GTC；禁止重复提交；券商状态=Cancelled code=202；终态诊断与执行权限独立核验
券商原因原文：Order canceled by system: synthetic broker cancellation
影响：有效 STP 保护 S=0 股，未保护 P−S=3 股；止盈和外部卖单仅列卖出容量，不能当作止损保护。新开仓继续冻结，Lite 不自动重挂。
你要做什么：1）先按 Ctrl+C 停止 Lite；2）在同一 TWS 核对持仓和所有工作卖单，再按当前 bid 用限价单平仓或人工建立保护，避免重复卖单；3）重新核对持仓和工作单。不要点击 TWS 的重新提交/IBALGO 建议；不要自动改价重挂保护单。只有确认 0 股、无在途卖单、成交静默期和原 release 强门槛均满足后才执行 release AAPL；身份或证据不足保持拒绝，不要手改状态库、新开仓或自动重挂保护单。
```

## TP价格被拒：合成6股，完整原因多段

场景完成后的测试真值（不替代卡片渲染时取得的券商证据）：{"P": 6, "S": 4, "T": 0, "X": 0, "not_covered_by_STP": 2}

```text
🔴 P0 · 止盈价格被券商拒绝 · AAPL
发生了什么：AAPL TP数量=2 股；发送限价=119.41；bid=119.64 ask=119.7 行情年龄=0.0s；orderRef=v6l-c5602ab2dc74457f9a37 orderId=1003 permId=51003 broker code=201；自动止盈暂停 position=pos-cd3c4f093d71497fb9b5 stage=TP1 pause_id=tp-pause-6b9e67704989434b9b8d；保护恢复仍需完整三项证据。
券商原因原文 1/3：Order rejected - reason: Your order price is outside the allowed price range. Synthetic IBKR price-band diagnostic; not an account or a live order. 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整
影响：本持仓自动止盈已暂停；券商保护与可用卖出数量仍须核验。
你要做什么：1）核对 TWS 持仓和工作卖单；2）确认 STP 已恢复到全部持仓；3）仅在原强核验门槛满足后由 John 执行 resume-tp AAPL --position-id pos-cd3c4f093d71497fb9b5 --pause-id tp-pause-6b9e67704989434b9b8d --operator John --reason 明确核验原因。没有合法 STP 完整证据时保持暂停。不要自动改价重发或点击 IBALGO。
```

```text
🔴 P0 · 止盈价格被券商拒绝 · AAPL · 原因 2/3
发生了什么：AAPL TP数量=2 股；发送限价=119.41；bid=119.64 ask=119.7 行情年龄=0.0s；orderRef=v6l-c5602ab2dc74457f9a37 orderId=1003 permId=51003 broker code=201；自动止盈暂停 position=pos-cd3c4f093d71497fb9b5 stage=TP1 pause_id=tp-pause-6b9e67704989434b9b8d；保护恢复仍需完整三项证据。
券商原因原文 2/3：原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price const
```

```text
🔴 P0 · 止盈价格被券商拒绝 · AAPL · 原因 3/3
发生了什么：AAPL TP数量=2 股；发送限价=119.41；bid=119.64 ask=119.7 行情年龄=0.0s；orderRef=v6l-c5602ab2dc74457f9a37 orderId=1003 permId=51003 broker code=201；自动止盈暂停 position=pos-cd3c4f093d71497fb9b5 stage=TP1 pause_id=tp-pause-6b9e67704989434b9b8d；保护恢复仍需完整三项证据。
券商原因原文 3/3：raint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; 保留完整原文🍎 price constraint detail; END-B23-ORIGINAL-PRICE-REASON
```

## 自动止盈暂停：合成8股，恢复全仓STP后健康重启仍不重发

场景完成后的测试真值（不替代卡片渲染时取得的券商证据）：{"P": 8, "S": 8, "T": 0, "X": 0, "not_covered_by_STP": 0}

该P0渲染时的测试真值：{"P": 8, "S": 6, "T": 0, "X": 0, "not_covered_by_STP": 2}
恢复全仓STP及成功重启后没有另发同事件低级别暂停卡。

```text
🔴 P0 · 止盈价格被券商拒绝 · AAPL
发生了什么：AAPL TP数量=2 股；发送限价=119.41；bid=119.64 ask=119.7 行情年龄=0.0s；orderRef=v6l-c7eb1b6cc5104e5fa3f7 orderId=1003 permId=51003 broker code=201；自动止盈暂停 position=pos-2288866e43ac4e229789 stage=TP1 pause_id=tp-pause-e6cb21164eb34c3ca14c；保护恢复仍需完整三项证据。
券商原因原文 1/1：Order rejected - reason: Synthetic price outside the allowed price range.
影响：本持仓自动止盈已暂停；券商保护与可用卖出数量仍须核验。
你要做什么：1）核对 TWS 持仓和工作卖单；2）确认 STP 已恢复到全部持仓；3）仅在原强核验门槛满足后由 John 执行 resume-tp AAPL --position-id pos-2288866e43ac4e229789 --pause-id tp-pause-e6cb21164eb34c3ca14c --operator John --reason 明确核验原因。没有合法 STP 完整证据时保持暂停。不要自动改价重发或点击 IBALGO。
```

## TP工作价格不一致：合成4股

场景完成后的测试真值（不替代卡片渲染时取得的券商证据）：{"P": 4, "S": 3, "T": 1, "X": 0, "not_covered_by_STP": 1}

```text
🟠 P1 · 券商工作价格不一致 · AAPL
发生了什么：orderRef=v6l-031ce2e0101e4c2e838e 发送价=119.41 工作价=119.66 cap=119.50999999999999
影响：该标的自动重发已阻止；原账本价格保留，STP 恢复继续独立核验。
你要做什么：1）核对 TWS 原订单价格；2）由 John 处置，禁止自动跟随 cap 改价。
```

## 第二次报价检查过期：合成5股，TP未发出并恢复保护

场景完成后的测试真值（不替代卡片渲染时取得的券商证据）：{"P": 5, "S": 4, "T": 0, "X": 0, "not_covered_by_STP": 1}

```text
🟠 P1 · 止盈单未发出，正在恢复保护
发生了什么：AAPL 持仓数量=5 股 保护单已确认=4 股 保护单可能=4 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 1–5） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-c6a93f1907784fd29d93 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:01:02+00:00 工作单证据时间=2026-10-06T14:01:02+00:00；保护减量确认后 bid/ask 无效或报价超过止盈新鲜度；未发出止盈，恢复保护
影响：止盈单确定没有发出，Lite 正在按三项证据恢复保护；原 B 空窗持续计时，累计 30 秒仍未恢复则升级 P0。
你要做什么：1）目前等待 Lite 用新鲜三项证据自动恢复保护；2）不要另下卖单或手动增加保护股数；3）从 B 空窗起点累计 30 秒仍未恢复，按 P0 卡片核对 TP 和保护单。
```


## 6 John运行配置需改的行

以下仅列指令要求；未读取或修改John的配置。

- 删除旧`[protection] limit_offset_pct`（沿用第22批）。
- `v6lite.paper.toml`中`exit_limit_band_pct = 0.01`改为`exit_limit_band_pct = 0.002`。
- `v6lite.g3t.toml`没有[execution]段，使用新默认0.002和tp_max_quote_age_seconds=3，无需新增段。
- 新参数允许整数1–10并纳入指纹；示例配置及默认解析用专项验证。

## 7 SHA-256清单

下表256项；伴随JSON清单另外包含本报告哈希，清单自身不自引用。完整红绿/全量逐用例记录、独立审签和实际卡片JSON一并留证。

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| `docs/V6_LITE_BATCH23_INSTRUCTIONS.md` | 10964 | `04708b69d6b3c410b487fb440f97a98790ce47bc0ad8dd3b23d471dc0592860c` |
| `docs/batch23_evidence/audit_commit.py` | 7019 | `5ce4ba3d2009438f7e1632d0edd8731f4f9e8e72fabb47812de5332ca5a541aa` |
| `docs/batch23_evidence/boundary_red_22a.json` | 6615 | `56088d2daf1c09683015ac095297d1630da653b2cc0bce2dd5deb36341386c6e` |
| `docs/batch23_evidence/boundary_red_22a.txt` | 12659 | `ee78cb53d504c685e70ea5bae1a95c798daff065c89dd374696cd08627c38dbf` |
| `docs/batch23_evidence/boundary_red_22a.xml` | 11580 | `a8f53cc4d71eccb7ca528e21d0ec64c807691ec2c4a82665d23eac296590899d` |
| `docs/batch23_evidence/boundary_red_candidate.json` | 5939 | `8c217bf35fe8476eb36d2e8ca692a647525ee0f35abb6eb135b4aef26300efc3` |
| `docs/batch23_evidence/boundary_red_candidate.txt` | 13274 | `224f84ccb9f7a123784e7faede1dc2aebbb4d57810bbfd4613473d2443270e23` |
| `docs/batch23_evidence/boundary_red_candidate.xml` | 9968 | `9a188a7412013a79d4c8fa6188c8a571fff635bfe078cede83c054dcec25e2fc` |
| `docs/batch23_evidence/boundary_red_review.json` | 12080 | `4ed09730a8f03287f2aadbd22d13f47c7fdf6a567aec974ecce62f6e8a6ea90f` |
| `docs/batch23_evidence/boundary_specs/35040f97659e/test_batch23_unprotected.py` | 18187 | `35040f97659ed5adb5ffc30875b67ee63b847c9f7065a07e33f0c37292bbddb6` |
| `docs/batch23_evidence/card_samples.json` | 34987 | `fecc8f93466768eccb288dc79781b7e9f6b933a56c60a366a681eeb3596e7364` |
| `docs/batch23_evidence/card_samples.md` | 12103 | `577da35d3526d8df14d7fa52b0405dd20281e638cbfb58af67ed1bd0728fd8e5` |
| `docs/batch23_evidence/card_samples_receipt.json` | 1648 | `b277f43b893debc9c3cc7d1198ecc9a85a1f9c584c4f7a32e7a33b792ea15b86` |
| `docs/batch23_evidence/core_red_review.json` | 951 | `e188cdc5b6ebd2f6e8f97210adfec356b036fb4a55b4dfaba5a2bb63e13f3b30` |
| `docs/batch23_evidence/core_red_spec_run2.py` | 28783 | `90f93dfaa635f62c295c0de9fa5e9c75f32b79c475cacd3b32a202ccf102e6fe` |
| `docs/batch23_evidence/diagnostic_red_22a.json` | 3128 | `0cd0c0911e2046d124a8985028880c9ed31425ff99ae5707fddfe13721cf15b4` |
| `docs/batch23_evidence/diagnostic_red_22a.txt` | 11517 | `320c7735863af3cdbbf72ad66b18c30307938da8fe86e9bc5877db54466b07b1` |
| `docs/batch23_evidence/diagnostic_red_22a.xml` | 5601 | `1f76d9d23725952048f9911ff6c1294481e742d6687cc43cf1d908679878f3f3` |
| `docs/batch23_evidence/diagnostic_red_candidate.json` | 3357 | `3f858b033f3d75102a147235dec69eef32c1e159403cadb36b37b20d64d4e47c` |
| `docs/batch23_evidence/diagnostic_red_candidate.txt` | 11625 | `28bf075bc1620aadb685fed6006d451c679b1ebc5ac4bab8812ee46fddf43509` |
| `docs/batch23_evidence/diagnostic_red_candidate.xml` | 5625 | `ba1f76b8bd7ce945faaef7d41e06cc5fe9eeb0bb959bf801457a7d5345143ec5` |
| `docs/batch23_evidence/diagnostic_specs/f5bcb0a7ab3d/test_batch23_broker_diagnostics.py` | 12955 | `f5bcb0a7ab3d199123dbbbfcdc2d562c137d0626f85f5196375a1e047ff9d37e` |
| `docs/batch23_evidence/evidence_validation.py` | 2276 | `4c3395ea8dd8b3484736a10bd73a6f2a5bfa516d34e636beb3e96e138947838d` |
| `docs/batch23_evidence/fact_red_22a.json` | 1346 | `5d38c0c3e923d88e49eac5469f8112ccfcb9c3d55158a2f7ab3b67fd6eb1f198` |
| `docs/batch23_evidence/fact_red_22a.txt` | 98 | `8afe2471606046b386cbefd7224d475f9d94ddb804d914ca3a5b3472c14d8f5c` |
| `docs/batch23_evidence/fact_red_22a.xml` | 386 | `99216c0191baeedc381ec330ed0aeab58571ce76f81cbeaea3e61ff661117e32` |
| `docs/batch23_evidence/fact_red_candidate.json` | 2115 | `6b1be38c1dec3f959699301eb75c80a2f72b26387ebb6fca37f13cb0a9a0d773` |
| `docs/batch23_evidence/fact_red_candidate.txt` | 3244 | `0232d8d41f1e18dd96d0b8b303207aa4d42ccccdb7001b0ad2c2786c6bae5a88` |
| `docs/batch23_evidence/fact_red_candidate.xml` | 2264 | `85f2c71f05feb6b85af478d15399adde310200a84a5ffe8d735ee951dea808a4` |
| `docs/batch23_evidence/fact_specs/9629999e9099/test_batch23_terminal_fact.py` | 3582 | `9629999e9099ec51860f412340411bb3aba2848c218c3dbea0240c7a73bd8b0a` |
| `docs/batch23_evidence/first_candidate_review.json` | 1646 | `755c0021a8baf072f72591826f360db2cdc1b43007ac2abe5c52998c83706dc0` |
| `docs/batch23_evidence/focused.json` | 135631 | `a5835e4f1319215526f20924ace1149780bb178ee9a5d5e4bd7cc1484c605b8d` |
| `docs/batch23_evidence/focused.txt` | 19040 | `04d0211aed2e503dbbc193ebdeb242bb5d9947623e8e4145ef7cdc7fef84da71` |
| `docs/batch23_evidence/focused.xml` | 104344 | `98e4508f344c1f8842b6c29252f313999a2c85d840e745db121484a63586c7c8` |
| `docs/batch23_evidence/focused_run2.json` | 130966 | `d111e834a8be693a8efe49fb5b1c0c768a2dc7010a680c94de6707891528fc0f` |
| `docs/batch23_evidence/focused_run2.txt` | 661 | `1c53691df225dca6b1f5edf8740cd264633f19365283bf0262c2a9ed72f449dc` |
| `docs/batch23_evidence/focused_run2.xml` | 91798 | `e3ede5e0abcb38f8ecd257e233397c35d7e83e5c1e4c5300762ba66363e9a04a` |
| `docs/batch23_evidence/focused_run3.json` | 130996 | `64519d700346b6add917f2b04d94850f8a85617fafebf3be1e51a2eabcd807c4` |
| `docs/batch23_evidence/focused_run3.txt` | 661 | `4b421b3334d7220c3eaf79b0a98051ff484a11b3993e9dfec876e5de5b65976a` |
| `docs/batch23_evidence/focused_run3.xml` | 91798 | `4a66b467dbcb5846c10644acf32278311cbcd9e9d38f0e0d8828c0ffce3af986` |
| `docs/batch23_evidence/full_1.json` | 311174 | `d1615060890f4e74a7f1b5a563cd120ebc53014e0fd9d6232e6781620e9024a3` |
| `docs/batch23_evidence/full_1.txt` | 2390 | `da943138a51b730d44ace3a2f9cc77cacf27e9fa125f4fb7c6352bcf61bc5944` |
| `docs/batch23_evidence/full_1.xml` | 221828 | `2523913ffc9fb4b3046cc93ad05a1a68a31554f52058f2dade930cd21a222d05` |
| `docs/batch23_evidence/full_1_run2.json` | 311097 | `47d5b5cbe4bc6b5153fc4883f7d02dff9650da5281156a30ec1b4a29981b7437` |
| `docs/batch23_evidence/full_1_run2.txt` | 1724 | `8783ddbf097c8608bf7319583b1c7192f4d454bb6e62ab605fd9ac3e3646d36c` |
| `docs/batch23_evidence/full_1_run2.xml` | 221349 | `ce10c2228193a9ca2b601d92cc787b0386185d47b215d5138c46034b3d025f6f` |
| `docs/batch23_evidence/full_2.json` | 311092 | `9123c6d2f814a9f83668e4feb5134e9af5f646c069f11cc4ddea9e091a60f394` |
| `docs/batch23_evidence/full_2.txt` | 1724 | `99c1e6b1e66c75eb352b13f19857ccf739c6e6b60d4678987868d293e29d76f5` |
| `docs/batch23_evidence/full_2.xml` | 221349 | `69a370d43678cd4ce813b860690fae6d31d2b7d338365f3689fdda0daf0e115e` |
| `docs/batch23_evidence/full_3.json` | 311092 | `af69b173c3c3aabe6197a010b70e868f5aa1488c079474f745b94b7a762dbd10` |
| `docs/batch23_evidence/full_3.txt` | 1724 | `63f0197f9556472988fd704ce3b21559feb471ae4c3bb4c4cdbac7ac85c614d2` |
| `docs/batch23_evidence/full_3.xml` | 221349 | `1c99a1337ab28b50b5749a682423f845ee7172e31cc725b5a445555b0cc943ce` |
| `docs/batch23_evidence/full_attempt_review.json` | 661 | `bce66d311592ee09dffaaf47b731ef5833bde4b6f939bfb3a91567dbbe08a681` |
| `docs/batch23_evidence/green.json` | 19055 | `80ddad82c07ad8df43dde8edc6e2e86d60acacc7539290e073801cf2e036daac` |
| `docs/batch23_evidence/green.txt` | 18436 | `0a94b9c16c7316778bbab0fc93c5c74963bc94e3013ebc98987e040eb3e4f7ea` |
| `docs/batch23_evidence/green.xml` | 16310 | `93404734672d77e1cbf026c16d518ac8caa1ef7a44111dd197f93b8584193e59` |
| `docs/batch23_evidence/green_run2.json` | 18171 | `b281cb9cdebd534e7265af27bd729da47c628827eca48995c0087a52c8cf663f` |
| `docs/batch23_evidence/green_run2.txt` | 3655 | `4faa87deb6ffb89498512db1268234a69810f38d03efcb1a821e211c20447f86` |
| `docs/batch23_evidence/green_run2.xml` | 13569 | `076d20683a35622e5f35bbb8609b9af56ceb6ccf5b21e294cc5f5e2959e2a3bf` |
| `docs/batch23_evidence/green_run3.json` | 26306 | `1bceff2a4623992083a237e9c466ce1074715c67e3b0cd5bb0524959b4b59cd1` |
| `docs/batch23_evidence/green_run3.txt` | 7325 | `cbd806591794c41057e1c33cc1e915dd3fac4be4ed3ca2a928396064954cdebd` |
| `docs/batch23_evidence/green_run3.xml` | 23360 | `a8080ab962c85877dfe626a90138a17608ccb95b6f2a3f0bcff04390b74f9297` |
| `docs/batch23_evidence/green_run4.json` | 28194 | `4df1e666a70ac7814e32762f20bb67e6db5268d1af1e9bc592ec57feb7099664` |
| `docs/batch23_evidence/green_run4.txt` | 181 | `19ec911ffbf36b2ccfa3a79c1a6045f01c7cc4165fd0d652fd1f4c8412dd8b4f` |
| `docs/batch23_evidence/green_run4.xml` | 19487 | `d6ccbebebe8ae843d25a92ab7ec1a94f185f92df9851a6c0d56ca1956935c0da` |
| `docs/batch23_evidence/green_run5.json` | 28194 | `23dc406955e8c7cfd6491148cccf7579771daecdbf1e08aed74eb35653f2184d` |
| `docs/batch23_evidence/green_run5.txt` | 181 | `4d0e62bdf7072d241dd5bf79a183984c2a672c448c95fb18ec1e683d1ef124f8` |
| `docs/batch23_evidence/green_run5.xml` | 19487 | `c23b37d4a102c7e0a06224ee7b32dc6d5801e6e26635212c38886605284eaaca` |
| `docs/batch23_evidence/green_run6.json` | 28224 | `4d7938ec0569a83286e057f3a1f538a274b8ab5b5c2dbea306a90a4f1b064069` |
| `docs/batch23_evidence/green_run6.txt` | 181 | `5dbdb831e8407851556efad28c323974e9ff30ead148217f9f4af1fb85b2a706` |
| `docs/batch23_evidence/green_run6.xml` | 19487 | `b8a644c6561348b2a0940eda14ee48e70ddb1b576ecf3c6cb0a7d176c76d53ad` |
| `docs/batch23_evidence/implementation_summary.json` | 9631 | `789dd5f811b7f489cc79aa44299a43f551caeba2a6732cd34034ca2d6957af38` |
| `docs/batch23_evidence/independent_production_review.json` | 5346 | `0c452751a634b55a799d6e4e28acf4be4303f2a149c5ba65e3b371e71cedf3d2` |
| `docs/batch23_evidence/independent_red_review.json` | 1968 | `9dc78af1da88b1f33d11ee65aac27012874be793ae5771df22c5fe77d3515b66` |
| `docs/batch23_evidence/independent_regression_red_review.json` | 1485 | `389454bf9a8ea90b2b970889d6f5e1fac04a6b36b40cf25c55149eee42f173cb` |
| `docs/batch23_evidence/initial_production_review.json` | 3890 | `3036f2eb4960c55ec435027b0490071ebb441954de61baf68208a06bbc04cf8f` |
| `docs/batch23_evidence/invalid_red_attempt.json` | 822 | `942876ac59c981807e4f4038f2077d5000a38ce8e15de8fa3bd534d467beb7aa` |
| `docs/batch23_evidence/invalid_red_spec_attempt1.py` | 27741 | `d722587c2a07a89a3e320608d5c647d2d35efb4b375a16cd7f6a7ab5440b6509` |
| `docs/batch23_evidence/investigation_baseline.json` | 25325 | `ec901a4a27fe8d89a45134220189d3f7864dbbcfb2d2424a0e1d2df95f7044bc` |
| `docs/batch23_evidence/legacy_capacity.json` | 1388 | `765be7006ccd31cf5b0a8e9604ed54db200b231897ad45b682f7e5a2dc6f18ad` |
| `docs/batch23_evidence/legacy_capacity.txt` | 113 | `9850f671ec715ed7d26ff691b6aedf919b75a90ad45827316b3bf29d44c308d5` |
| `docs/batch23_evidence/legacy_capacity.xml` | 384 | `66ea3823fd44465ce24d2edd7b9cb7f18376be799a6d4314ef4c056b71727f47` |
| `docs/batch23_evidence/legacy_capacity_update.json` | 1018 | `ff7a8ed6a9e808dd58cbacba32497f2fd9e9301901c7b9c6684ffd9168de5d97` |
| `docs/batch23_evidence/legacy_oracle_updates.json` | 1824 | `200c74e18fceb432694002e750a4c0a13de10a4556962b52deae2eeb906340a4` |
| `docs/batch23_evidence/permission_baseline_ast.json` | 2420 | `38d6ef70ea1b3d6affb6760c9815ca50e5e412a2431d4a8f79139e15f187a9c1` |
| `docs/batch23_evidence/preflight.json` | 608 | `b3051852cbf8a7406099a6cd168cb3334d72ffdc25fba737b42ae6fca304ee3a` |
| `docs/batch23_evidence/primary_green.json` | 17950 | `106744a4d3e76bbcc2dc8103aeef581492ded240995bba7d8f79abcf7abfa0a9` |
| `docs/batch23_evidence/primary_green.txt` | 179 | `15a1ada895a2ba396a4d4b7279b356ea517892c00e6e738cc3d78596214947b6` |
| `docs/batch23_evidence/primary_green.xml` | 12523 | `8d8f0e7b30c726bf34c1c2bf1b32012ee751405d1cdb706ffafb588a1890d3ab` |
| `docs/batch23_evidence/production_baseline.json` | 3603 | `8b48a20bb596eb434016e9a2514c32d7087b441422273c533b110e9f373afba9` |
| `docs/batch23_evidence/recovery_fixture_review.json` | 601 | `91509e11fc8e5050bf4ac1f29b93d46d548358f6f12e4915f67be68d887cbb71` |
| `docs/batch23_evidence/recovery_red_22a.json` | 2107 | `e750c49dcdb03731230201c86d9bf48ce105ca52ff2631b966b2fabf6427a27d` |
| `docs/batch23_evidence/recovery_red_22a.txt` | 3223 | `7f7bfd54b97f0e1910bbd03719053d7ca64ce48fa7662613eddb166b19ef4bf7` |
| `docs/batch23_evidence/recovery_red_22a.xml` | 2187 | `a222c2d86b2f0d55912b320e816dc3d3d64468eb4df0007e05ffa190e07cc8b6` |
| `docs/batch23_evidence/recovery_red_22a_run2.json` | 2112 | `74f3bee9dfae0daad13496a1f1cc0a1fd3a5c97d4e53812e7b43fc87defdd578` |
| `docs/batch23_evidence/recovery_red_22a_run2.txt` | 3223 | `a342cf0c5ff07dcd8fb296dd4d45c35ddfb9edbba64c2f1ef24ac9e3a45acb79` |
| `docs/batch23_evidence/recovery_red_22a_run2.xml` | 2187 | `310f86e393e3c5082a797a617cb37f37cd62aead30c00dab53addfa2685f8c8f` |
| `docs/batch23_evidence/recovery_red_candidate.json` | 1568 | `50d96c08ce2b5493afdef17e854e3aab56b2a297a7ede0a2100b218ff0baf510` |
| `docs/batch23_evidence/recovery_red_candidate.txt` | 2715 | `47e417d8d13004fc90154c41dc948cd4f92ddf9887cd697ffa30722db740d55b` |
| `docs/batch23_evidence/recovery_red_candidate.xml` | 1149 | `89bcec2121a0c67139e465354a2044ab9739c478aaab64dc29381d39e1920052` |
| `docs/batch23_evidence/recovery_red_candidate_run2.json` | 1995 | `5d8212946c43ed0cd38a53032d5176ecc50db76bd4a75131a09f4ead5fcbbbc8` |
| `docs/batch23_evidence/recovery_red_candidate_run2.txt` | 3129 | `add7016aa3ae43406acd29d14ecdfb3d69681ae546f53e834260d59a92f3897d` |
| `docs/batch23_evidence/recovery_red_candidate_run2.xml` | 1936 | `2637aae7182f7fca1c087703baf4ffff8cc3f4adf0d855e8cfb9246465339885` |
| `docs/batch23_evidence/recovery_specs/ad3ea825f387/test_batch23_recovery_diagnostic.py` | 5626 | `ad3ea825f387334a0add08f8d6085f6fd9d36eb64f2e48ebafd51f4e2c30b5be` |
| `docs/batch23_evidence/recovery_specs/cf55cae52353/test_batch23_recovery_diagnostic.py` | 5277 | `cf55cae52353ef0603d4d2a2205fa5c2643b59036c213059e7cd91e4606e53c0` |
| `docs/batch23_evidence/red_22a.json` | 9178 | `f8d89cf3f1dc0184f99e9cdc82b114f7e7f2195a36eb76cd74ed18b99e2deb35` |
| `docs/batch23_evidence/red_22a.txt` | 17559 | `aa482d2d8ff3e8ecbdb7f6de299b9310f853221244dfd4f4934a8983f3496b21` |
| `docs/batch23_evidence/red_22a.xml` | 18870 | `11af4a13e1cd2f4c805597aff45dc16a80902a2fa0ac0ca8cdadb5086a63acbf` |
| `docs/batch23_evidence/red_22a_run2.json` | 12045 | `ca2fb2a6683d8858f8f4b6c8226a1b9703cba475c5e502d7802cb7b30bb7212c` |
| `docs/batch23_evidence/red_22a_run2.txt` | 19630 | `ca1e6e04f2b787628ccdc816a9c35621b2a94bb1f76f5bc3f6bf9320ae8859b0` |
| `docs/batch23_evidence/red_22a_run2.xml` | 23073 | `d46b180ad1d8c5a81dd54dcf1bbf7afef8b47e51530a350935a37b8ef04f5cd3` |
| `docs/batch23_evidence/red_22a_run3.json` | 26531 | `fe2d5899c67e2965a18f81bce60a1a4973378885b5ab12bb6cb0ebb48ab51a1d` |
| `docs/batch23_evidence/red_22a_run3.txt` | 44270 | `ab424d4c37a99be1521650f1e25c6ff64af15bc9805aa033950709d424337916` |
| `docs/batch23_evidence/red_22a_run3.xml` | 53152 | `3ca043cd63c208aa19cc526a7235383959cb2f31b22adbeffdf44d38a32e9de0` |
| `docs/batch23_evidence/red_22a_run4.json` | 26789 | `1087eca17558b716c1f21aa5d6ad9167658874acc8dc6ab3974ee78e7894f51a` |
| `docs/batch23_evidence/red_22a_run4.txt` | 44270 | `b459ddcb1dad42ebcaee2aeaef1c8642ca98341da3af59f472e37674c05da463` |
| `docs/batch23_evidence/red_22a_run4.xml` | 53152 | `a27dab045757175955b26a779c55915893a0b236bfe10528d4ed6f749dd40968` |
| `docs/batch23_evidence/red_22a_run5.json` | 26789 | `25c0710fbea655e46eae52116748b62659b8693663a840c8b6377ec331014bad` |
| `docs/batch23_evidence/red_22a_run5.txt` | 44270 | `752e10a2fe418fe0ce35b1c5b477c936bc880113aeacd7bf4d888d5e8efd057c` |
| `docs/batch23_evidence/red_22a_run5.xml` | 53152 | `831837b846661344d3dc41b46960a5a3e33e38d4cdaa461d51e4ef72bf4f1543` |
| `docs/batch23_evidence/red_specs/test_batch23_tp_evidence.py` | 28783 | `90f93dfaa635f62c295c0de9fa5e9c75f32b79c475cacd3b32a202ccf102e6fe` |
| `docs/batch23_evidence/red_specs_run1/test_batch23_contracts.py` | 40657 | `586d8ee1a08f56aaf3af851d5b7ee2d3a1c138aa0ec97909d452f8875e788aca` |
| `docs/batch23_evidence/red_specs_run1/test_batch23_tp_evidence.py` | 34624 | `1fcf3580ab9e9d03e83ccf5bdc23c6c2cfa2c1a8d714f4e7650e91d99598a96a` |
| `docs/batch23_evidence/red_specs_run2/test_batch23_contracts.py` | 41444 | `bc308587579a59f95deed0964cf76e94b31c3601bb5fd3d9211ff7a7ebe1b199` |
| `docs/batch23_evidence/red_specs_run2/test_batch23_tp_evidence.py` | 34940 | `3b4977815ab586db706f5ed708d79e7c5e0f2304ee3059f7f2f55b22b992d9c3` |
| `docs/batch23_evidence/red_specs_run3/test_batch23_contracts.py` | 41613 | `1ba0957cbcb974c23c178d24ce8a94df47a8a62e1570c6efb8f2147a3544172c` |
| `docs/batch23_evidence/red_specs_run3/test_batch23_tp_evidence.py` | 34940 | `3b4977815ab586db706f5ed708d79e7c5e0f2304ee3059f7f2f55b22b992d9c3` |
| `docs/batch23_evidence/render_samples.py` | 8030 | `e1e01821af2faa055496c3bab2d60aaac3d28aeebf7225aa68a4ddc8b30fc7cd` |
| `docs/batch23_evidence/review_red_22a.json` | 10088 | `56a6375a5d36aaa24068c8ca67e292ae104f3aacfb0d3875a1929b1b8b738838` |
| `docs/batch23_evidence/review_red_22a.txt` | 17447 | `a78c1a8a2df568961ae4b4355d9fe60d1b3d5bc4771b836c3b3d636bdc985dcb` |
| `docs/batch23_evidence/review_red_22a.xml` | 19281 | `5bb333b3d3689bf3c558c897f21052fe1c74b46d28a94870bf31fa508fbed004` |
| `docs/batch23_evidence/review_red_candidate.json` | 14899 | `5659728c6bef3094df91b066bd3998e79d5ca1f991d031ccf54013aa151f6e63` |
| `docs/batch23_evidence/review_red_candidate.txt` | 27096 | `1653d9a179b43037f8857d6114b8ca9b53d512f75b0fe50b0f03173adcf1ff3c` |
| `docs/batch23_evidence/review_red_candidate.xml` | 28548 | `61e07735a42b671bcb72c7204e186636bea6475dcf036c66f417161d2c3a0285` |
| `docs/batch23_evidence/review_specs/fcf063c69658/test_batch23_error_provenance.py` | 28123 | `fcf063c69658242b034f401e0a0a65382c53e542edbda716679962086f54345b` |
| `docs/batch23_evidence/run_batch23_checks.py` | 11604 | `c4bf2fad63385dadf12f2dc5163a93b1e217bf42f47c1f98f5e75536598b679e` |
| `docs/batch23_evidence/scope_notes.json` | 992 | `e64f7798739e171d0b93212202704c076a7fa2481442eca33e7e11b8f1f922a5` |
| `docs/batch23_evidence/seal_report.py` | 19257 | `c7988fe9a46d14a5c5375fdf1e29f8ba4baa1994087d046ad8fa8e577ab636c4` |
| `docs/batch23_evidence/sealing_schema_review.json` | 1448 | `ffec8743c66679750d85a384c3bff60a20c19b4d81bec10537ea9c563de54562` |
| `docs/batch23_evidence/test_design_review.json` | 1408 | `d437ca96a1bf251a03f8b0044f48cc9e9721de2874591976624bb0ac09ab8289` |
| `docs/batch23_evidence/windows_environment.json` | 553 | `29ccb06642c521fcc052496e0e602666169471880bb56b43c1fd0662e156b28d` |
| `scripts/tests/console_shutdown_child.py` | 3719 | `3732ac606a911fc34f4c64a631f7672d55b5e8b96eaf50c8346e9daa6518be6a` |
| `scripts/tests/console_shutdown_probe.py` | 8641 | `77bbeba33b1f4d94027cf30dddac46e845edd9e7a9d3672f20af63e918e99114` |
| `scripts/tests/test_batch21_entry.py` | 3716 | `b367e6cab0ad80d8901e103ba1459b08a0ea4555d8506959a7630963ac18e470` |
| `scripts/tests/test_v6_launchers.py` | 35142 | `fff766c38064b7ad5421eadd9639f363e06cfa5a6e6ecb1448a4c22c002b270c` |
| `scripts/v6-user.ps1` | 39 | `55978f6fdb9f50c2dc66cf5731436d675fa7b95021d8e7ac3911d2fe2825f9e8` |
| `scripts/v6.cmd` | 161 | `e6303971e31af8b57e050506ddae1c71516efc3f7c523850438fba3028fcc6a8` |
| `scripts/v6.ps1` | 16619 | `7a8a0794cee4efd5b4d151d07ff51a8ba3a0c7a7c4e814ba0073c316d8b3fb15` |
| `v6lite/README.md` | 13718 | `4ad81212d1863b4fc11dc87d8bc8c32ea272e95563095243292ed615ced31fd9` |
| `v6lite/__init__.py` | 313 | `26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39` |
| `v6lite/__main__.py` | 26950 | `1284cf8153eda67e74c6acf250796304c6a2390e7a91dabc6fce3f6f40c93a00` |
| `v6lite/alerts.py` | 53232 | `4b422fd4f2c1e2c3297de04d6193cfdd80adb4dadeee428516fdae171ff00657` |
| `v6lite/approval.py` | 12709 | `70a3c24325b95216bb307dd261b60ccff88f0f13da3e2f94d6344e92f07b41e1` |
| `v6lite/broker/__init__.py` | 96 | `2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24` |
| `v6lite/broker/base.py` | 3727 | `f6e52937d5e10cb0d1853ccfbce35cf47eceb74080ae6121a34577e3bad11e6b` |
| `v6lite/broker/fake.py` | 16149 | `04af71e38336c9ac2d6002c404e44d9d7584ce607b020e3cd52eb46010f395b7` |
| `v6lite/broker/ibkr.py` | 72293 | `57e79cc8953f21bd477cc0ae7ccd8bd34c669fee84192119288c6856f61068fc` |
| `v6lite/clock.py` | 910 | `0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1` |
| `v6lite/config.py` | 18352 | `2c8741a48ec84918af262d711b341e63cdb582793b31932b0880f83201c963a1` |
| `v6lite/engine.py` | 188344 | `9e379332fd2dfa68b54ebfbc07f9bdcfc242c9de56a29b60f4affc4b6d7fc1f0` |
| `v6lite/examples/live.toml` | 3005 | `c156a0d200c91931a39e90cb5279248b85376f2f59f772a54d8b657134830820` |
| `v6lite/examples/paper.toml` | 2897 | `3dd7901f2af4dac79ff43bf4d1ff28d774b3adc590f2dfc54ee87c1058c5bad3` |
| `v6lite/examples/paper_delayed.toml` | 2997 | `5c53d585657090a088d6d3fba98f811367e89924a106e9948d82c8f0dba29e54` |
| `v6lite/examples/paper_g3n.toml` | 1340 | `5498230cbfe0c670a3ef3c6208327ef357c65f65e6b0b773bd5c544764b2a829` |
| `v6lite/examples/paper_test.toml` | 1019 | `00ca15f04fe0975b0cad7ca7ad57436b62271563d7ae669147b9be0ed41bbb3b` |
| `v6lite/execution_time.py` | 3269 | `6767aee2d823eccc2387106f803b38521bce94e71ec082fe6dd021918ee9f300` |
| `v6lite/exits.py` | 3927 | `1de7eb5637ee3c6abd1784570846d0edf8f91739ee97e2bb6e0b6651e34de093` |
| `v6lite/market_calendar.py` | 3925 | `1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2` |
| `v6lite/models.py` | 7173 | `83b1c5395f015809ec51241f8907d8e0400141bc3a3c6d08cf1b01c3d89205f8` |
| `v6lite/protection.py` | 543 | `61fa2bdd943b2c60717706b3494fbfb95dca6f2789d2cb9c74fc82a71b2d197a` |
| `v6lite/protection_cancellations.py` | 11312 | `74724327c2ae5e67499cfbbea7bc471fa9a5d382d1ed520da577930844557c67` |
| `v6lite/protection_engine.py` | 111803 | `d58b5ed6a077ae820fbaf80bc6e99a3032ddfe8cf562316eb860d7e0c5a24e2b` |
| `v6lite/protection_incidents.py` | 13373 | `0b1d3616a3433b7a4a0049f5ffec2978a9bbb0a895965cdfb1062ea0abcf5ac0` |
| `v6lite/reconcile.py` | 2610 | `4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1` |
| `v6lite/report.py` | 27918 | `6b333c0e8239608a7525c23c23103308daefe63ff5e99ce794f6039a55eb561a` |
| `v6lite/risk.py` | 11077 | `abc521f671e878a0ab5690e1fa178dc872d29b5396b86eaeb5f016be1594c687` |
| `v6lite/sizing.py` | 3294 | `c9b9cc4a116a7f61d9e660dd885d280ec2a9262671f2c3c8ab5c1515724e171e` |
| `v6lite/store.py` | 78984 | `56cc4454b612aa96a798aa0e95a3ead3646bf0a9b5fe921733b1de3989d4c411` |
| `v6lite/strategy.py` | 8300 | `9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e` |
| `v6lite/tests/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `v6lite/tests/conftest.py` | 209 | `aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb` |
| `v6lite/tests/helpers.py` | 5415 | `0c28d019b52b61f5d0b477bd7b202c409ff6761a4b4830b70c77667a3d9787d6` |
| `v6lite/tests/test_batch10.py` | 19369 | `5b875c8df97861e22427223e7065a74bddf04a5da1c7115b431871369d177c47` |
| `v6lite/tests/test_batch11.py` | 10273 | `fb7f26a0bd1969400f2d769a0d65f4c7d3a18f0162332fd46ca9f1ed99c260f8` |
| `v6lite/tests/test_batch12.py` | 25124 | `3dfc69a4f811549cb32c9c186bbfed3d24f7c1c956c3c743d9b28f6996037cfc` |
| `v6lite/tests/test_batch12_v2.py` | 20802 | `5691a234501cd71b85e3098e0cf6da2a74f2d4f5ca3eda2797a8abe17fab9af0` |
| `v6lite/tests/test_batch13.py` | 18609 | `12a3ed033079f129e7e70f610be004c498ea6f6f0283473f60b2eb80ad819140` |
| `v6lite/tests/test_batch14.py` | 16354 | `5087c53709f4208d5dc4fbe9f74b100d50ebfb2060ccf9c9834358d2c3c7b257` |
| `v6lite/tests/test_batch15.py` | 71363 | `5f8d69e27230857b5b726e3960ba1a2cf09178cb925d2cb85ca98b209857e3d1` |
| `v6lite/tests/test_batch16.py` | 29612 | `dcd5e3932511c625596dee9dad5708d19a4c6566d860e11006458e3384b766f1` |
| `v6lite/tests/test_batch16_card_deadline_copy.py` | 1932 | `d9c3c8f080748838a81392b1a12ca03347685a1be4692ae9159471ff2cc56a51` |
| `v6lite/tests/test_batch16_f5_copy.py` | 15179 | `eca4e85d176cdb5a726bc4e5bec2f2b6bd6869ff726a6b647c16548b4ab94bb0` |
| `v6lite/tests/test_batch16_f6_account_evidence.py` | 7194 | `6d97cec645009fd6d90c223cfeb5c902866701f75c01f91a604caddd3a0e863d` |
| `v6lite/tests/test_batch16_f6_global_mismatch.py` | 2874 | `86c542de449c2adab7cae4d2bc476e5108ef7ef14d9c2f45686a2404a3b9d478` |
| `v6lite/tests/test_batch16_f6_release.py` | 10603 | `15c23d799539945057095f56bad2e8ba8a32974383eb8d778fba0f57ede2cb96` |
| `v6lite/tests/test_batch16_f6_safety.py` | 5402 | `9e2464cc21430b8b4e27dd8982b1a5aee0b554a5e0698691df3156715ad65b9c` |
| `v6lite/tests/test_batch16_f6_steady_fairness.py` | 2893 | `e7ae2a84c08b2adadb1aa9994e835b5504db581401eecbf2bfd14678e984915d` |
| `v6lite/tests/test_batch16_f6_steady_timeout.py` | 2401 | `733347eda5814cbd2a14f71a717c88274bdd51376cada2c9bceeab3fd2908c48` |
| `v6lite/tests/test_batch16_f7_price_flip.py` | 4495 | `b2f9cf68a543999d08ab0567e6e6a5663f257d2525c48430f953de8bacfbc5dd` |
| `v6lite/tests/test_batch16_f8_backoff.py` | 2126 | `af5c1b6c62872c84536b222255c9a3b4229e2d7bf4ce1c4b55b1d4a780aa3539` |
| `v6lite/tests/test_batch16_gap_evidence.py` | 26359 | `328269e2c8fa8a0547d1d16a5904a4d6cc5462f13b093b9e6c09eb87bd4be8ba` |
| `v6lite/tests/test_batch17_alerts.py` | 10542 | `dfc6a85711add46c7a6e8b966c3f75f38f2bbbc7bee1b3f827882d3d9ddba5ff` |
| `v6lite/tests/test_batch17_equivalence.py` | 9719 | `ecb49e23e9cbcd6ea2803aacb4b649d8b8794b1c79e65f266141c655efdfca8a` |
| `v6lite/tests/test_batch17_g8.py` | 5126 | `4e21318f45dc6293fc4fbdc0f1c5be7c9d845782bf405fff8c6a51504bae5875` |
| `v6lite/tests/test_batch17_heartbeat_account.py` | 20900 | `5ab7bafaddb4ab15e16c275b45061079c46c3d642b866a91ac55f85a75c3f6de` |
| `v6lite/tests/test_batch17_l3_recovery.py` | 15834 | `736891fd02d99b2337fffca283fbddf53de7d8fce8294d6a089175cbd317cd3c` |
| `v6lite/tests/test_batch19.py` | 10690 | `cefff371b7c9101fff87583517d11a8f81675f0a549cb2007c19099b3d9e239c` |
| `v6lite/tests/test_batch19_funds.py` | 19657 | `f9c1ae564a76752edcb60b3293eec5a4b2ac15657b4e34258ba814cf4a78dde4` |
| `v6lite/tests/test_batch19_protection_equivalents.py` | 13413 | `12def834c0ef3a32bf71331473a47daae9e7d2b4309347e177784421cdeec303` |
| `v6lite/tests/test_batch19_review_edges.py` | 7207 | `262a24defa92162552f14d80dfc1eb45d673e7a538a7c0ead745b7649515f7d6` |
| `v6lite/tests/test_batch20_card_provenance.py` | 1856 | `7187685c885a24587a40ce7745d3abd0601255c97414554493482cc3592cf03e` |
| `v6lite/tests/test_batch20_execution_time.py` | 20596 | `658d55ca04fdcfbc9a6f5810871eeedf9439741752fbaa7a3b70836838431242` |
| `v6lite/tests/test_batch20_protection_equivalents.py` | 51774 | `0cbd0e83af3982c6ae80e7b0871462c6649e0e25921cb4c2e5217d0502ff39a4` |
| `v6lite/tests/test_batch20_protection_incident.py` | 36148 | `ba4a10ff92e6bc195357651e624e350bf926708ee64bbbac75f5b19ca427bb56` |
| `v6lite/tests/test_batch20_template.py` | 810 | `8c6ed584a5f435fe81542df99ce8c7a1d040f342583b733fe65356af352b5230` |
| `v6lite/tests/test_batch22_cancel_namespace.py` | 3080 | `b513c130a9045147fa3f77c3afb10d1140b17207995143866523fc9de83c532f` |
| `v6lite/tests/test_batch22_config_broker.py` | 24207 | `7f29f6e653f648c6d90d3cecfa954d372cf3e4eaff257ff7e8737902cd752ab8` |
| `v6lite/tests/test_batch22_item9_broker_cancel.py` | 40215 | `97ebdeffe53f1e324dfff7d0565711f33b25db3e0006e3c0c403a5d1ddfd07fc` |
| `v6lite/tests/test_batch22_pending_stop_audit.py` | 9880 | `f0bf9fe05cee399ca62892222ef09cd069bf8dc27a7179978e7434b7235e0696` |
| `v6lite/tests/test_batch22_protection_lifecycle.py` | 36555 | `53a91c443c8c190461d34f315ebb0e68f931e4d4ac8f179f20bc1a07cd0df12d` |
| `v6lite/tests/test_batch22_schema_audit.py` | 29777 | `374ae51b92eee5ea1e48ff4ca145422f1d106b0d33cf8f94b57d6261b24c7927` |
| `v6lite/tests/test_batch22_snapshot_cancel.py` | 10065 | `6531f1b9c3398e1cdbd0c7148e610b360846ff78a11dd3e6406e2cb92aa3286b` |
| `v6lite/tests/test_batch22_trigger_incidents.py` | 11026 | `a3c1ad8c925c53ce95577de7ee707093b98ff74468ed13cda66da7e30cc8db17` |
| `v6lite/tests/test_batch22_trigger_replacement.py` | 3263 | `80c284c3bd1b41ec47c841f344ca03726c8da9569433d0dfafec805bf861a90c` |
| `v6lite/tests/test_batch23_broker_diagnostics.py` | 12955 | `f5bcb0a7ab3d199123dbbbfcdc2d562c137d0626f85f5196375a1e047ff9d37e` |
| `v6lite/tests/test_batch23_contracts.py` | 41613 | `1ba0957cbcb974c23c178d24ce8a94df47a8a62e1570c6efb8f2147a3544172c` |
| `v6lite/tests/test_batch23_error_provenance.py` | 28123 | `fcf063c69658242b034f401e0a0a65382c53e542edbda716679962086f54345b` |
| `v6lite/tests/test_batch23_recovery_diagnostic.py` | 5626 | `ad3ea825f387334a0add08f8d6085f6fd9d36eb64f2e48ebafd51f4e2c30b5be` |
| `v6lite/tests/test_batch23_terminal_fact.py` | 3582 | `9629999e9099ec51860f412340411bb3aba2848c218c3dbea0240c7a73bd8b0a` |
| `v6lite/tests/test_batch23_tp_evidence.py` | 34940 | `3b4977815ab586db706f5ed708d79e7c5e0f2304ee3059f7f2f55b22b992d9c3` |
| `v6lite/tests/test_batch23_unprotected.py` | 18187 | `35040f97659ed5adb5ffc30875b67ee63b847c9f7065a07e33f0c37292bbddb6` |
| `v6lite/tests/test_engine.py` | 17668 | `bb8967a536a8d187fc9f1d742be17c0b4b90e193ab52731ac06d6bff8a08f461` |
| `v6lite/tests/test_g2_day1.py` | 10712 | `cacaf5b3115d4a298fe8b3fe065d66fe70277b8ee4cd5d9643f0e2fff0ff5f9d` |
| `v6lite/tests/test_g2_day1_selfreview.py` | 8081 | `fe84ef645d99f421c64f670aa7f883d16b5a24c8c15b4dd30a4aa8f035b5ea06` |
| `v6lite/tests/test_g2_day2_prep.py` | 2547 | `a0db6b981992a3cf20d9f959a679c608842f4619007331bae9b26908cee3ccb7` |
| `v6lite/tests/test_g2_probe.py` | 7222 | `ba38215d7a6946d60884d0e3dac99b641e7bcde058c4893b94cb9d62b6a6fe18` |
| `v6lite/tests/test_ibkr_adapter.py` | 6081 | `6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a` |
| `v6lite/tests/test_review_fixes.py` | 22689 | `eb91ef957bfd13864c2c92a5da2cf3a29ffdc10c9f5c74fe751daa8f4caecdd3` |
| `v6lite/tests/test_review_round2.py` | 40440 | `a8fa9a8f5daca49aed8b0de1a87097f257b02048a8807e769d951f06472d6b35` |
| `v6lite/tests/test_review_round3.py` | 15213 | `b5bf4e5c513e9d2310dc6404f147b633790cff0e894546e9176d6c26390a7427` |
| `v6lite/tests/test_review_round4.py` | 18605 | `04cf584aa3b7e13842c1e7608d03e5f888b7a714ea073001ce85281fe1eb43cb` |
| `v6lite/tests/test_review_round4_selfreview.py` | 13981 | `8cf2bba5d54e2e644bc016e68abfe87f0ecbe26b6e54d5a44a329c61e6dc7da8` |
| `v6lite/tests/test_review_round5.py` | 16770 | `c45a477f2bb20f021b3d8040b3be6360d78bb8c95f0ef7ffd2ba069fdf31eeb0` |
| `v6lite/tests/test_review_round5_selfreview.py` | 12064 | `03adaac71b3ea18095732064c51f6f4741400adae738c3cfb9f840c247ec4a8e` |
| `v6lite/tests/test_review_round6.py` | 10254 | `c6eabe6e953cc75ccfa7e4fa87625a86d677ef5dd2fca9975cd101b3403d6be5` |
| `v6lite/tests/test_review_round7.py` | 9101 | `bcb2baea638b6ba6c063aba7776aaf1137c3cd1df98297a08311cdaf899994a5` |
| `v6lite/tests/test_review_round7_selfreview.py` | 6455 | `a3589fae5996245f7246f1860875a847b6761ed9f8bab5a317ab5c7d9e5e86bf` |
| `v6lite/tests/test_review_round8.py` | 7212 | `c65ac1c396468aae3124ffcdc0f61b2ed483547ec1b0f47119f00950008b3736` |
| `v6lite/tests/test_review_round8_selfreview.py` | 7315 | `e927860575cb55769a910a0dfbced96b725922da6629010c894ec2d3c86b36c3` |
| `v6lite/tests/test_soak.py` | 6157 | `d84df42bcdc3ce0e505a8f930b2752ec2721cc307fb364388be210131c4fad47` |
| `v6lite/tests/test_strategy_parity.py` | 4014 | `732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f` |
| `v6lite/tests/test_units.py` | 8846 | `75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42` |
| `v6lite/tools/__init__.py` | 34 | `91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1` |
| `v6lite/tools/g2_probe.py` | 20537 | `bb1be0098415bc624b0f6d1a31b20f832d1e481e8155b4565c7f6841aa2f2d16` |
| `v6lite/tp_evidence.py` | 28874 | `59c5607ec7fd44b79cfd33555ba360a2cdf72d0a363c36300c9e4d5899278645` |
| `v6lite/version.py` | 863 | `9aa52b23a3c8c08801f9d86c46ae1090a28c78dbde5d5b4b2923347bc74ece45` |

## 8 提交与冻结范围

任务提交仅包括本批改动的v6lite源码/示例/测试、本指令和新报告及本批证据。Claude手册与item8研究原有未提交文件保留；不包含运行配置、数据库、缓存、临时fixture目录、22a归档源码副本或baseline ZIP。提交与附注tag v6lite-batch23完成后，不再修改工作树，等待Claude复核。
