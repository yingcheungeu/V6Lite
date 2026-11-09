# Batch27a 只读调查（先于生产修改）

日期：2026-10-16。分类：代码修复的只读调查阶段。基线 `v6lite-batch26` / `78a0905e0c45482cf6db7427016231b69144422d`，code_sha `579d13832e3f4237`。John 已确认以当前含 5.6 的 `3cfb4765819725d083268218c0e94db523e037ca9cc3147161af95a8a2a5875b` 版为准。只做第 5 节；第 1–3 节留到 27a 后。

以下代码行号均为 B26 基线。未读取运行仓库、用户状态目录或 John 配置，未连接券商。唯一现场资料是指令提供的成交/回调时序及脱敏 10/15 统计；没有 10/13、10/16 原始查询异常记录，不能声称完整复原实盘。

## 发现与可证边界

1. **回调时间来源。** 本地已安装 ib_insync 0.9.86：`wrapper.py:473–491` 的实时 Fill.time 用 lastTime，查询 Fill.time 用 execution.time；已知 execId 不再 emit callback，但仍 append 到查询结果。`decoder.py:409–453` 的原生 naive 时间存在主机时区隐式解释。Lite `execution_time.py:45–68` 明确拒绝无时区时间的隐式解释，改记收到回报时间并标 uncertain。只有完整查询且时间确定，才能核实；`source=reqExecutions` 本身不足以证明时间可靠。
2. **未核实的主因在消费者。** `broker/ibkr.py:1251–1275` 不按已知 execId 丢弃查询结果。`protection_engine.py:592,625–655` 下载成交后只比较 execId 集合/构建证据，不调用 `_ingest_fill`，所以频繁成功查询仍不触发时间核实。`engine.py:1517–1529` 的 `_refresh_fills` 才会将结果送入 `_ingest_fill`，但并非 callback 后的必执行周期任务。`store.py:751–760` 遇已知 execId 会先核时，不能把根因写成 Store 提前去重。原始日志缺失，无法证明 10/13 每次调用的实际次数/结果。
3. **核实条件和经济幂等。** `store.py:673–675` 使用九字段 identity；`718–749` 要求来源确定、identity 相等、provisional 与现有 fills.time 相等、真实时间仍属原 trading_day。匹配仅修正时间/closed_at 并清标记，不重记成交数量、价格、持仓或盈亏；不匹配留审计等待核对。fills 没有 con_id 列，历史放行必须把该字段与持久化订单/保护身份绑定，不能忽略。
4. **B26 漏测。** `tests/test_batch26_overnight_evidence.py:249–257` 用 FakeBroker 默认确定时间的 fill；并未覆盖真实 callback 的 uncertain 标记。已有 15 秒节流测试 `test_batch26_constraint_policy.py:153–155` 刻意把 mark 放在 TP1 以下，也遗漏冻结后反复到价的链路。
5. **历史条件仅证明此前收到回报。** 按新裁定同时严格核对九字段、provisional==fills.time，以及 provisional < 当日 00:00 ET 减 1 小时。不改历史 marker/time/trading_day，不写 EXECUTION_TIME_VERIFIED；INFO/日报必须注明“未经券商查询核实”。其余 a–d 和运行约束 A/B 不变。收到时间不是实际成交时间的技术证明。
6. **首次失败 P0 与事件生命周期是两件事。** `protection_engine.py:826–838` 对非 steady 或非 ProtectionEvidenceQueryError 直接 P0；`621` 的账本/券商快照冲突会抛原生 BrokerError。三通道传输错误在 `573–597` 被包装，故不能把现场 BrokerError 猜成 End 计数错。`protect_incident.first_at`（`protection_incidents.py:158–170`）用于送达/提醒，真正 F4 计时为 `protect_evidence_failure`（`protection_engine.py:803–824`）。全保护成交 `engine.py:823–828` 只结束 trigger-unfilled；完整恢复 `protection_incidents.py:210–217` 又要求 full_exec，过夜恒 false，因此旧 evidence 事件可能残留。
7. **2 秒查询的可复现路径。** `engine.py:2143–2146` 在过夜权限检查前先 begin_stage/EXIT_TRIGGER；`protection_engine.py:1496,1517–1526` 再额外三查询后拒绝；价格回落由 `engine.py:2099–2104` abandon，再到价会重启。此路径绕开 `protection_engine.py:913–937` 的 steady 15 秒检查。另 WORKING status 设置 priority，可产生额外路径，须用真实 SDK 回调双桩测试分辨；不能仅用体量摘要确认各路径占比。
8. **secs=0.000 不能判断有没有发请求。** `broker/ibkr.py:719` 调 reqAllOpenOrders，本地 SDK `ib.py:1903–1906` 直接发 client 请求；`broker/ibkr.py:497–498` 只是 monotonic 耗时保留三位。应通过 native transport 请求计数测试证明发起次数，不能拿 Fake 方法调用数冒充 native。
9. **冻结应先于阶段创建。** 必须在 begin_stage 前拒绝已冻结的过夜 TP；按 position_id+ETday 持久记录一次到价说明。真正开始 TP/保本修改前仍重新核验当代 a–d，不拿缓存授权。已开始的安全撤单/恢复流程不应被新门禁截断。
10. **盈亏差异是保守缺费估算。** `engine.py:884–889` 调 `risk.trade_net`（119–123），每个缺佣金成交按 `sizing.py:14,18–26` 最低预留 $2：-6.09-1.00-2.00=-9.09。实际 -8.11 与之相差 $0.98；不是重复扣已知佣金。建议卡片只给已知费用后的暂计值并明确“1 笔佣金未计”，不把该数称为最终净盈亏；风控仍保留原保守估算，避免悄然改变交易门控。
11. **盘前前收盘价暂缺。** `broker/ibkr.py:908–916` 取过去 7 日 RTH 日线，无可用 bar 或异常返回 None；`engine.py:1925–1947` 每标的每 5 分钟重试，取到后缓存，当次 `_daily_risk` 1968–1971 清 flag。`risk.py:179–180` 阻断入场，退出仍运行。没有现场历史回报，不能证明是正常盘前现象，也不能承诺开盘恢复。卡片可准确写自动重试条件，保留实际风险关注。

## 实施与测试次序

单一实施 Agent 负责全部生产文件；独立作者只写新增 test_batch27a_* 测试。ROOT 先在冻结 B26 副本运行自然红，再允许生产实施；聚焦测试、独立复核后锁候选，非交易时段串行三次全量，不更改断言掩盖失败。完整保存 collection/executed nodeid、JUnit、stdout、源文件哈希及卡片。最后只暂存本批文件，排除旧临时目录、手册、运行配置、数据库与缓存，提交并打附注 v6lite-batch27a，冻结等待 Claude。

## 仍需现场证据才能确定

- 10/16 11:55:44 PT（18:55:44Z）附近的 BROKER_QUERY_ERROR / full_error、三通道起止与回调代次；没有这些不能给 BrokerError 最终现场归因。
- 10/13 完整 reqExecutions 原始时间字符串及显式时区，以及结果 identity；无法保证券商查询一定返回可确定时间。
- 10/16 04:21 PT 的前收盘历史请求结果/异常，用来判断盘前暂缺的具体原因。

报告中的真实 execId/orderId/provisional 仅取指令；未给价格、permId、conId 等在回放中明确标为合成。不会读取禁止的状态目录补证据。
