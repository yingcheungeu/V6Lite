# 第二十七批第1–3节：实施前调查

日期：2026-10-17 PT。性质：代码修复任务的只读调查及设计。基线按John最新指令为 `v6lite-batch27b` / `54a893ba655c8e04ef64cfcbb6d49a2ebcdcddaa`，code_sha `d6dd37c12701ce95`；原指令的b26基线已被最新授权替代，但第3.6要求的自然红仍另在b26留证。

运行仓库27b合并由John完成，本批不再执行，也不访问运行仓库或用户状态。手册只核哈希 `768e4df71b8f7f996aa3fb81aa27ef8845f11502fcfc1429cf131118bf64de62`，不读内容、不改、不提交。指令SHA256 `3cfb4765819725d083268218c0e94db523e037ca9cc3147161af95a8a2a5875b`；统计证据SHA256 `26b1edc06f75bc2186d86615d2f57f94ea5ed6eb9f1313de7882fd505dd23bf6`。预检无v6lite进程、无不可检查Python进程、暂存区为空，代码/脚本干净，仅Claude手册有预存改动。

本报告先于任何本批生产修改交付。调查与独立测试设计并行；生产只由一名Agent修改；ROOT串行运行自然红、聚焦和最终三次全量。交易日06:00–13:30 PT禁止全量，逐次核对时间；不连接TWS/Gateway、不push。

## 1 读取点及安全边界

搜索方法：

```text
rg -n --glob '*.py' 'PROTECT_STATE|ORDER_PRICE_OBSERVATION|OBSERVATION_SUMMARY|FROM audit|JOIN audit|audit_for_day|confirmed_at' v6lite scripts
rg -n --glob '*.py' --glob '!**/tests/**' 'FROM audit|JOIN audit|audit_for_day\(' v6lite
AST枚举非tests生产模块中的.audit(now, kind, ...)；展开动态kind并记录函数/分支上下文。
```

完整机器清单见 `docs/batch27_evidence/audit_inventory.json`：98个语法调用点，展开后73种kind。搜索无发现使用目标两kind“最新一条审计”授予交易/保护/核验权限的生产代码。无需把安全读取改到别的状态源，因为它们本来就读耐久状态行或新鲜券商证据。

| 读取/写入路径 | 文件:行号与摘录 | 结论 |
|---|---|---|
| PROTECT_STATE主写点 | `v6lite/store.py:603` `self.audit(now, "PROTECT_STATE", position_id=...)` | 每次UPDATE都写，包括仅confirmed_at刷新；状态行仍须每次照常更新 |
| 终态UNKNOWN独立写点 | `v6lite/protection_cancellations.py:107` `self.store.audit(now, "PROTECT_STATE", ... state="UNKNOWN")` | 显式状态迁移，不经重复观察合并 |
| ORDER价格主写点 | `v6lite/tp_evidence.py:413` `self.store.audit(now, "ORDER_PRICE_OBSERVATION", **payload)` | 每个可信观察都写，实际payload可完全相同 |
| 观察后的不可跳过行为 | `v6lite/tp_evidence.py:414` `if intent["purpose"] in TP_PURPOSES:`；后续TP_EVIDENCE、价格矛盾/冻结/卡片 | 仅审计sink去重，不得提前return或改变后续执行 |
| 唯一按ORDER kind读取 | `v6lite/report.py:233` Events白名单；`v6lite/store.py:1403` `SELECT * ... ORDER BY id` 后fetchall | 呈现路径全读并展开导致大日报；改为SQL聚合与有界详情读取 |
| PROTECT_STATE按kind读取 | 生产搜索无命中 | 不承担最新证据读取职责 |
| 通用审计时间间接读取 | `v6lite/report.py:43` `SELECT MAX(ts) FROM audit` | 无Engine clock、未来日期离线回放时，重复审计曾影响fallback now；须保留等价显示时钟，可结合仍更新的protections.updated_at/confirmed_at，不得借此改变交易时钟 |
| 保护状态读取 | `v6lite/store.py:518` `SELECT * FROM protections`；`v6lite/protection_engine.py:978` `row['confirmed_at']` | 15秒稳态、改价绑定/数量/强身份均仍读状态行，不改更新语义 |
| A/B空窗 | `v6lite/store.py:541` protection_gap_a；`v6lite/protection_engine.py:1801` protections；`:2090` gap_b_started_at | 不依赖两个目标audit；空窗/报警时间不动 |
| 恢复 | `v6lite/engine.py:1376` active_intents；`:1379` 新鲜_order_evidence | 不依赖目标audit |
| TP/过夜证据 | `v6lite/tp_evidence.py:96` control；`v6lite/overnight_evidence.py:83` 当前scope；`:166` fills；`:419` intents | a–d、S+T+X≤P、强身份门槛和proof绑定不动 |
| release | `v6lite/engine.py:1831` request control；`:1843` fresh openOrders；`:1889` protections/intents强身份 | 不改变核验，不查目标audit授权 |
| clear-anomaly | `v6lite/__main__.py:538`→`v6lite/store.py:423` controls | 不改异常解除 |
| 其他audit消费者 | `v6lite/report.py:79` PROTECT_GAP；`:149` PROTECT_FILL；`:171/:182` TP_EVIDENCE；`:244` STARTUP；`:291` OVERNIGHT_CURRENT_DAY_TIME_BASIS；`:331` SCAN/DRY_RUN；`v6lite/store.py:1243` ANOMALY | 保留其内容和判断。空窗fetchall及全历史TP遍历也需检查本地呈现内存；共享手机健康卡片不因本地报告限额改行为 |
| 精确kind测试读者 | `v6lite/tests/test_batch23_error_provenance.py:348/:357` 完整reason；`test_batch24_presentation.py:89` raw cap；`test_batch25_review_regressions.py:59/:68` 握手snapshot不得新增观察 | 原强断言保留；不可全局截断reason或改变snapshot权限 |

## 2 全部kind的频率/触发口径

下表为源代码触发频率，不是假装现场逐kind计数。现场只提供统计摘要，两目标以外83行的完整分布未知；不读取John状态库补数据。每个kind的所有写点、分支上下文和原始调用另见JSON。状态/操作事件也可能重复发生；仅明确授权的两个观察源可合并。

| kind | 分组 | 触发/频率依据 | 写点 | 允许合并 |
|---|---|---|---|---|
| `ANOMALY` | repeated_observation | 每次 raise_anomaly；controls 重复覆盖也会写审计。 | v6lite/store.py:416 | 否 |
| `ANOMALY_CLEARED` | state_change_or_discrete_event | 人工 clear-anomaly 成功清除 controls 后。 | v6lite/store.py:430 | 否 |
| `BROKER_QUERY_ERROR` | repeated_observation | 每次真实 open_orders/positions/executions 或 release 查询失败。 | v6lite/engine.py:1636、v6lite/engine.py:1913、v6lite/engine.py:1943、v6lite/protection_engine.py:585、v6lite/protection_engine.py:595、v6lite/protection_engine.py:604 | 否 |
| `CLIENT_ID_CHANGED` | state_change_or_discrete_event | 启动发现已绑定 client_id 与当前不同且通过安全检查。 | v6lite/engine.py:515 | 否 |
| `COMMISSION_CORRECTED` | state_change_or_discrete_event | 已有佣金值发生实际变化；首次佣金仅写 commissions 行。 | v6lite/store.py:669 | 否 |
| `CONNECTION_SNAPSHOT_PRICE` | repeated_observation | 每次连接握手期间缓存价格回调；只诊断，不授予交易权限。 | v6lite/engine.py:774 | 否 |
| `CONNECTION_SNAPSHOT_STATUS` | repeated_observation | 每次连接握手期间缓存状态回调；只诊断，不更新订单事实。 | v6lite/engine.py:957 | 否 |
| `DAILY_STOP_LATCHED` | state_change_or_discrete_event | risk_days 当日止损首次从未锁存变为锁存。 | v6lite/store.py:459 | 否 |
| `DISPOSITION` | state_change_or_discrete_event | 人工设置 ignore。 | v6lite/store.py:438 | 否 |
| `DRY_RUN` | state_change_or_discrete_event | 演练决策去重键首次出现，不是每 tick 必写。 | v6lite/engine.py:2714、v6lite/engine.py:3382 | 否 |
| `EXCLUDED_SYMBOL_FILL` | repeated_observation | 受保护或 ignore 标的的每次排除成交事件。 | v6lite/engine.py:789 | 否 |
| `EXECUTION_TIME_OBSERVED` | state_change_or_discrete_event | 27b adapter 按 execId/source/category/raw/invalid 的新观测去重后发出。 | v6lite/engine.py:760 | 否 |
| `EXECUTION_TIME_REVIEW_REQUIRED` | state_change_or_discrete_event | Store/主动核时按 execId 首次 review 控制项；原生坏行诊断另按27b观测键去重。 | v6lite/engine.py:1566、v6lite/engine.py:760、v6lite/store.py:761、v6lite/store.py:729 | 否 |
| `EXECUTION_TIME_UNVERIFIED` | state_change_or_discrete_event | 新成交首次入账且时间不确定。 | v6lite/store.py:856 | 否 |
| `EXECUTION_TIME_VERIFIED` | state_change_or_discrete_event | 完整查询身份/交易日/provisional 全部匹配，清除未核时标记。 | v6lite/store.py:752 | 否 |
| `FILL` | state_change_or_discrete_event | execId 首次经济入账。 | v6lite/store.py:861 | 否 |
| `FILL_QUARANTINED` | state_change_or_discrete_event | 成交首次进入隔离证据区，不经济入账。 | v6lite/store.py:1196 | 否 |
| `FREEZE_RESOLVED` | state_change_or_discrete_event | 人工 flat 强核验后解决已关闭持仓的冻结。 | v6lite/store.py:1302 | 否 |
| `IBKR_NOTICE` | repeated_observation | 每个券商 notice 回调，错误码逐条保留。 | v6lite/engine.py:757 | 否 |
| `INTENT_CREATED` | state_change_or_discrete_event | 新 intent 在 broker 调用前持久化。 | v6lite/store.py:506 | 否 |
| `INTENT_RESOLVED_BY_OPERATOR` | state_change_or_discrete_event | 人工 resolve-intent 强制结案。 | v6lite/__main__.py:417 | 否 |
| `INTENT_STATUS` | repeated_observation | 每个带 status 参数的 update_intent，或终态 metadata 原子 hold；不要求 status 值实际变化。 | v6lite/protection_cancellations.py:121、v6lite/store.py:637 | 否 |
| `KILL` | state_change_or_discrete_event | 每次人工 kill。 | v6lite/store.py:397 | 否 |
| `LOSS_STATE` | state_change_or_discrete_event | 连亏/cooldown 记录更新。 | v6lite/engine.py:929 | 否 |
| `ORDER_PRICE_OBSERVATION` | repeated_observation | 强身份通过后的每次原生价格观察，当前无 payload 去重。 | v6lite/tp_evidence.py:413 | 仅指令限定观察情形 |
| `OVERNIGHT_CURRENT_DAY_TIME_BASIS` | state_change_or_discrete_event | 当日全部 a-d 通过后，同 position/day/经济快照摘要首次记录。 | v6lite/overnight_evidence.py:148 | 否 |
| `OVERNIGHT_TP_FROZEN` | state_change_or_discrete_event | 已到止盈价但被冻结，同 position/day 最多一次。 | v6lite/overnight_evidence.py:303 | 否 |
| `POSITION_RELEASED` | state_change_or_discrete_event | 人工 release 通过全部强核验后。 | v6lite/store.py:1231 | 否 |
| `PROTECTION_DELIVERY_BATCH_INCOMPLETE` | repeated_observation | 保护卡片不可变分片数不一致时每次 flush；可能重复。 | v6lite/protection_incidents.py:96、v6lite/protection_incidents.py:90 | 否 |
| `PROTECTION_INCIDENT_RESOLVED` | state_change_or_discrete_event | 存在旧 incident 控制项且实际结束事件。 | v6lite/protection_incidents.py:205 | 否 |
| `PROTECT_BROKER_ECHO` | repeated_observation | 非终态 intent 的每次 PROTECT 原生 orderStatus 事件。 | v6lite/engine.py:979 | 否 |
| `PROTECT_BROKER_ERROR` | repeated_observation | 每次保护单提交/修改/撤销/超时撤销的 broker 异常。 | v6lite/protection_engine.py:413、v6lite/protection_engine.py:1475、v6lite/protection_engine.py:1507、v6lite/protection_engine.py:1857 | 否 |
| `PROTECT_BROKER_REJECTED` | repeated_observation | 每次满足明确拒绝分类的 PROTECT 状态回调。 | v6lite/protection_engine.py:528 | 否 |
| `PROTECT_BROKER_TERMINAL` | state_change_or_discrete_event | 终态诊断 raw_status/code/reason/identity/cancel_class 至少一项变化。 | v6lite/protection_cancellations.py:126 | 否 |
| `PROTECT_FILL` | state_change_or_discrete_event | 当前或退休保护单成交经济入账，保留止损与滑点证据。 | v6lite/store.py:1093 | 否 |
| `PROTECT_GAP` | state_change_or_discrete_event | 真实 A/B 空窗闭合；A 可逐 execId 及旧 schema 窗口，B逐 workflow。 | v6lite/protection_engine.py:2095、v6lite/store.py:563、v6lite/store.py:578 | 否 |
| `PROTECT_IDENTITY_RETIRED` | state_change_or_discrete_event | 替换前归档已严格核对的保护单身份。 | v6lite/store.py:699 | 否 |
| `PROTECT_PLANNED` | state_change_or_discrete_event | 入场事务内或迟到成交恢复时新建保护计划。 | v6lite/store.py:530、v6lite/store.py:515 | 否 |
| `PROTECT_PREPLAN_ADJUSTED` | state_change_or_discrete_event | 入场成交后计划止损需要调整。 | v6lite/protection_engine.py:377 | 否 |
| `PROTECT_QUERY_LIMITED` | state_change_or_discrete_event | 同一失败 episode 首次查询退避说明，控制项防重复。 | v6lite/protection_engine.py:726 | 否 |
| `PROTECT_QUERY_SUPERSEDED_BY_FILL` | repeated_observation | 查询中已确认自有保护成交关闭持仓，旧失败仅留审计。 | v6lite/protection_engine.py:869 | 否 |
| `PROTECT_RETIRED_FILL` | state_change_or_discrete_event | 已入账保护成交来自退休身份，另留 lineage 审计。 | v6lite/store.py:1095 | 否 |
| `PROTECT_STATE` | repeated_observation | update_protection 每次调用；confirmed_at-only 重复观察；另终态代码直接写 UNKNOWN。 | v6lite/protection_cancellations.py:107、v6lite/store.py:603 | 仅指令限定观察情形 |
| `PROTECT_STATUS_IDENTITY_MISMATCH` | repeated_observation | 每次保护状态身份不符或取消身份缺失，伴冻结/人工核验。 | v6lite/engine.py:1012、v6lite/protection_engine.py:446 | 否 |
| `PROTECT_STATUS_IDENTITY_VERIFIED` | state_change_or_discrete_event | 本地缺 orderId/permId 时，从严格原生 tuple 首次补全。 | v6lite/protection_engine.py:484 | 否 |
| `PROTECT_TERMINAL_EPISODE_RESOLVED` | state_change_or_discrete_event | 旧 coverage 终态事件实际结束。 | v6lite/protection_incidents.py:204 | 否 |
| `PROTECT_TERMINAL_HOLD_PRESERVED` | repeated_observation | 终态 metadata 不完整且 intent 已有人工 hold；每回调可写。 | v6lite/protection_cancellations.py:142 | 否 |
| `PROTECT_TERMINAL_METADATA_INCOMPLETE` | repeated_observation | 终态 metadata 不完整且此前无 hold；每回调可写。 | v6lite/protection_cancellations.py:142 | 否 |
| `PROTECT_VERIFICATION_BACKOFF` | repeated_observation | 每次实际证据核验失败；1/2/4/8/16/30秒退避，饱和后仍逐次留证。 | v6lite/protection_engine.py:793 | 否 |
| `RECONNECT_ATTEMPT` | repeated_observation | 每次实际实体连接尝试。 | v6lite/engine.py:1108 | 否 |
| `RECONNECT_BACKOFF` | repeated_observation | 每次重连/恢复失败后的退避状态。 | v6lite/engine.py:1117 | 否 |
| `RECONNECT_STOPPED` | state_change_or_discrete_event | 达到同原因重试门槛，停止自动重连。 | v6lite/engine.py:1136 | 否 |
| `RELEASE_GTC_VERIFIED` | state_change_or_discrete_event | release 中原保护单强身份与终态证据通过。 | v6lite/engine.py:1958 | 否 |
| `RELEASE_REFUSED` | state_change_or_discrete_event | 消费 release 请求后任一核验失败。 | v6lite/engine.py:1992 | 否 |
| `RELEASE_REQUESTED` | state_change_or_discrete_event | 人工 release 请求持久化。 | v6lite/__main__.py:359 | 否 |
| `RESUME` | state_change_or_discrete_event | 人工 resume 成功，且当日 daily stop 未锁存。 | v6lite/store.py:411 | 否 |
| `SCAN` | state_change_or_discrete_event | 每个扫描批次结果。 | v6lite/engine.py:2651 | 否 |
| `SHUTDOWN` | state_change_or_discrete_event | 演练或正常停机结案；保留不可合并。 | v6lite/engine.py:3072、v6lite/engine.py:3010 | 否 |
| `STAGE_ABANDONED` | state_change_or_discrete_event | 原阶段不再适用或发送前重新判定后放弃。 | v6lite/engine.py:2379、v6lite/engine.py:2180、v6lite/protection_engine.py:1628 | 否 |
| `STAGE_BEGIN` | state_change_or_discrete_event | 首次 INSERT OR IGNORE 成功创建阶段。 | v6lite/store.py:1366 | 否 |
| `STAGE_FINISH` | state_change_or_discrete_event | 阶段实际结束，完成或取消。 | v6lite/store.py:1391 | 否 |
| `STARTUP` | state_change_or_discrete_event | 启动完成的状态快照。 | v6lite/engine.py:343 | 否 |
| `TEST_ENTRY_CLAIMED` | state_change_or_discrete_event | 测试入场请求成功 claim。 | v6lite/engine.py:2554 | 否 |
| `TEST_ENTRY_REQUESTED` | state_change_or_discrete_event | 人工创建测试入场请求。 | v6lite/__main__.py:384 | 否 |
| `TEST_ENTRY_RESULT` | state_change_or_discrete_event | 测试入场完成或就绪状态撤销时的结果。 | v6lite/engine.py:2604、v6lite/engine.py:2488 | 否 |
| `TEST_TP_TIMEOUT_RESULT` | state_change_or_discrete_event | TP 非市价超时演练请求结果。 | v6lite/engine.py:2535、v6lite/engine.py:2492 | 否 |
| `TP_AUTO_PAUSED` | state_change_or_discrete_event | 某 TP 拒价首次创建 pause 控制项。 | v6lite/tp_evidence.py:321 | 否 |
| `TP_DEFINITELY_UNSENT` | state_change_or_discrete_event | NEW TP 发送前门控证明确实未发出，持久化 REJECTED。 | v6lite/tp_evidence.py:233 | 否 |
| `TP_EVIDENCE` | repeated_observation | TP 发送/状态/减量/恢复/撤单/成交/空窗闭合；quote_skip 每次无效报价及 TP 价格观察均可高频。 | v6lite/engine.py:2921、v6lite/engine.py:2890、v6lite/store.py:976、v6lite/tp_evidence.py:163 | 否 |
| `TP_RESUMED_BY_OPERATOR` | state_change_or_discrete_event | 有效人工 resume 请求消费，全部强核验成功。 | v6lite/tp_evidence.py:501 | 否 |
| `TP_RESUME_REQUESTED` | state_change_or_discrete_event | 人工 resume-tp 请求持久化。 | v6lite/__main__.py:344 | 否 |
| `TP_SUBMIT_GATE_BLOCKED` | state_change_or_discrete_event | TP 发送前发现旧 intent 已非 NEW 或存在 hold，不得认定未发出。 | v6lite/tp_evidence.py:229 | 否 |
| `TRADE_CLOSED` | state_change_or_discrete_event | 持仓确认关闭，按旧路径记录盈亏与费用状态。 | v6lite/engine.py:910 | 否 |

明确不能自行合并：`TP_EVIDENCE` 的quote_skip/status可能随每轮/回调增长（tp_evidence.py:163/:248/:379/:417）；`PROTECT_BROKER_ECHO`（engine.py:979）随原生status；`IBKR_NOTICE`（engine.py:757）每条原错误码保留；`PROTECT_VERIFICATION_BACKOFF`（protection_engine.py:793）实际失败逐次，饱和30秒后仍可120次/小时/持仓；`BROKER_QUERY_ERROR`、ANOMALY、终态/拒单/卡片/经济事件均不并。它们高频时只报告，等待Claude另裁，不借本批减少事件或改变核验频率。

## 3 日志来源、估算及27b新增冲突

现场统计原证据按PT13.2小时：两种QUERY约各3,525行/小时，position也约3,525行/小时；GATEWAY_TIME约34/小时、HEARTBEAT约6/小时。这是10/15 b25实测摘要，不代表27b实测。

下表为名义压力模型：每持仓每15秒做一次proof，Q=240P次/小时；各positions请求返回账户全部P个持仓。全局one-proof/tick和原生pacing可能降低实际速率，必须以离线wire计数校准，不能把模型称为现场实测。

| 来源 | 1持仓 行/小时（13小时） | 10持仓 行/小时（13小时） | 依据 |
|---|---:|---:|---|
| 正常QUERY openOrders+positions | 480（6,240） | 4,800（62,400） | ibkr.py:505；2Q |
| SDK position | 240（3,120） | 24,000（312,000） | SDK wrapper.py:301；P×Q，未共享快照时呈二次包络 |
| EXECUTION_TIME_SUMMARY 普通 / constraint两查询 | 240/480（3,120/6,240） | 2,400/4,800（31,200/62,400） | ibkr.py:1300；每完整reqExecutions一行，未含全局约30秒刷新额外请求 |
| HEARTBEAT | 6（78） | 6（78） | engine.py:71/:3160，10分钟，账户级 |
| GATEWAY_TIME | 活跃窗口约60；13h保守≤780 | 同左 | engine.py:103/:3290，60秒；实际窗口约06:00至收盘后30分，不把全天13小时乘积称实测 |
| updatePortfolio | 无固定本地上界；10/15约19.8/小时 | 依推送和P变化，未知 | SDK wrapper.py:288；来自服务器推送，不以查询间隔假设固定上界 |

正常QUERY改DEBUG、wrapper设WARNING后，上述前两项正常INFO归零；QUERY每30分钟一个通道汇总约2行/小时（26/13h），心跳/时钟不改。**27b逐次EXECUTION_TIME_SUMMARY仅普通查询就破单持仓2,000行/13h上限**，不能只关wrapper便宣称达标。是否把相同执行时间分类汇总也并入30分钟/DEBUG，已向John提出异步裁定；未决定前不改该承诺，也不虚报日志上限通过。

账户脱敏必须在最终Stream/File handler格式化输出（包括异常栈）处理：__main__.py:231启动WARNING、engine.py:344 STARTUP_SUMMARY、:3160 HEARTBEAT/status_snapshot，以及SDK ib.py的Trade、alerts日志都可能含完整账户。root logger自己的filter不自动处理传播的子logger记录。只脱敏输出，不改cfg、状态库、事件身份、审计原值或卡片正文；因此强身份、Alerter去重及卡片次数不变。

## 4 实施设计及待裁定边界

1. **PROTECT_STATE**：同一事务先读旧行、照常更新状态行和confirmed_at/updated_at。仅除这两个时间字段外实际值不变时省该观察审计；状态/数量/价/订单/空窗/stage改变必须原payload照写。显式终态UNKNOWN写不并。内存计数/缓存须在相关事务成功后发布，回滚不得把未落盘状态当已观察。
2. **ORDER**：按intent保留上一payload，不含观察时间；首见/实际连接代变化/任一字段变化立即写；非空error/reason逐条写。仅包审计sink，后续TP、价格、保护/卡片逻辑完全执行。重启缓存为空。
3. **SUMMARY预算**：每持仓每30分钟单条envelope，`observations`中逐kind保留original_kind、intent_id/position_id、次数及first_at/last_at。两个kind分写会52行/13h而违反≤30；envelope让稳态26个周期+停机尾项约27条，无重复则不写空汇总。连接代切换另flush旧代，再清缓存；以真实query_generation为准，不把1100/1101/1102通知冒充新socket代。
4. **停机**：不抢在撤DAY/等待/settle之前增加阻断；在最终SHUTDOWN前flush，dry分支也覆盖，Store.close仅作兜底。汇总不得影响撤单、GTC保留或卡片行为；崩溃/强杀不能保证纯内存计数落盘，不伪称崩溃恢复。
5. **本地日报**：kind统计先用SQL GROUP BY/COUNT/MIN/MAX；重要详情有界/stream读取；普通观察不进Events详情，连续相同内容×N。不得在fetchall读完9万行后才过滤。保留旧表，不删改、不VACUUM。账户仅末3位。原完整reason和关键证据不能无声截断。
6. **critical超限待裁定**：若P0/ANOMALY/成交本身超200行或单payload超100KB，绝对硬上限和完整主报不可同时满足。已提出“主报守限+完整附件 / 关键事件允许主报超限 / 主报索引全文audit”三种选择。未收到裁定前不实施依赖该选择的overflow行为。
7. **执行时间INFO待裁定**：保留27b每次INFO与本批数量上限冲突，已单独询问。QUERY正常DEBUG/失败WARNING和账户输出脱敏等明确部分可以先准备红测试；不擅自变更27b原始时间证据或解析规则。

## 5 独立红绿和验收计划

- 三份独立新spec：观察审计、日报体量、日志体量。通过原有公共接口做自然失败，分别在27b当前基线和指令要求b26隔离副本跑红；不能用缺新API的AttributeError或collection error充行为红。
- 13h×1/10持仓×15s/1s固定时钟，原生回报/真实Store路径，1s是显式压力输入，不改生产15秒调度或pacing。基线首次超过硬上限即可断言失败保留收据；候选必须完整跑完13小时并核精确合并次数。
- 压力主体可用真实SQLite内存连接避免数十万次磁盘fsync；另有文件库重启/停机/历史9万行测试验证持久性。构造数据按提供的10/15规模和两观察payload，不冒充完整真实账本；5条TP_EVIDENCE和SCAN以合成事件留存。
- 逐字段变化必写，错误逐条、真实代切换、重启首见、停机flush、事务回滚；卡片/核验/订单动作/账本一致、S+T+X≤P；共享手机健康卡片保持原行为。
- 旧9万行日报≤5秒、SQLtrace/row factory/tracemalloc证明Python内存不随行数线性增长；任何overflow断言待John裁定后补齐，不用skip掩盖。
- 基线原测试保持；b22–b26、27a/b、a–d和停机补日报聚焦全过后，冻结候选、Windows默认全量连续三次0failed/errors、完整nodeids/JUnit/唯一遗留skip核对、独立复核与SHA清单，最后dev提交附注tag `v6lite-batch27`，等待Claude。
