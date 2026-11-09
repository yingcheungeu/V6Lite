## 范围、裁定与剩余风险

基线 `v6lite-batch25` / `97fa8c6d68cfd7b700c1474dc6f8a94e158fd2d2`（code_sha `ea71abe782e4cf68`）。只在 `C:\Dev\V6Lite-dev` 的 `dev` 修改。第1节调查于10/14先交付，之后才修改生产；原指令SHA-256=`dff7d8eff2edf4e2bfb18e3a63f6a018292cf2662fdcbae7c50268b8cdbee78b`。调查原件SHA-256=`41f834b1b843bcbe73599ef92437b211225e5dbae5f632eb3959ca052f142805`。最新裁定原文见 `docs/V6_LITE_BATCH26_RULING_ADDENDUM_2026-10-15.md`，取代临时bool开关及前序待定表述；历史报告和失败收据原样保留。

John 确认原文：

> 运行约束 A（账户独占：Lite 管理的标的只由 Lite 交易，人工处置一律按停机→撤单→平仓→release）与 B（账本不回滚：不手动替换/恢复/编辑 .v6lite 状态库）为 John 明确接受的剩余风险，须写入报告和卡片说明，不得称为技术证明。

两项已接受风险仍为：**停机期间等量手动卖出又买回（净数量不变，只影响盈亏）；状态库被整体回滚为与券商一致的旧版本。** 程序不能技术排除它们。历史digest及流程token只绑定本次流程，不是账本未回滚或人工从未交易的技术证明。实际INFO/P1卡片均注明“依据运行约束 A、B”。不修改 Master API client ID，不将空的可见成交列表解释为全账户历史没有交易。

## 第1节调查及官方依据

[当前IBKR reqExecutions](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/execution-details/request-execution-details)保证当日午夜以来；[新ExecutionFilter协议](https://www.interactivebrokers.com/docs/tws-api/protobuf/execution-filter)虽有最多7天字段，本机ib_insync 0.9.86只有旧七字段，不发送新字段也不补齐历史。[旧官方页面](https://interactivebrokers.github.io/tws-api/executions_commissions.html)提到TWS Trade Log设置与Gateway的区别；不能合并为本机一定可查7天。实际服务端设置及历史范围未知，本轮未连接券商。

[IBKR Master/clientId文档](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/client-id-0-and-the-master-client-id)区分客户端可见范围，账户过滤成功不能证明可见所有client。按John裁定，历史由已持久化账本承担，当日核验当前API可见成交，以A、B约束为前提；不额外要求Master设置或不可取得的历史连续性。调查第4节的逐行核验仍保留。

## 发现 → 改动 → 测试

|发现|改动与源码引用|独立测试|
|---|---|---|
|临时bool不能表达最终裁定|`v6lite/config.py:88` 默认 `overnight_tp="freeze"`，`v6lite/config.py:257` 仅接受freeze/constraint，`v6lite/config.py:180` 纳入指纹；五份示例显式freeze，移除临时bool|policy默认/五示例/严格拒绝非法值；edge真实TOML省略=freeze、constraint指纹不同。|
|昨日入场不在当日下载|`v6lite/overnight_evidence.py:76` 逐行核execId、订单/恢复持仓关联、orderId/permId、方向用途、正且有限数量价格、时区/日期、BUY总数、净持仓及成本；`v6lite/overnight_evidence.py:184` 每动作消费当日同代新鲜a–d，逐字段比对每条可见成交|真实昨日ENTRY→GTC→正常停机→SQLite重开，今日查询显式不含昨日；五类指定负向、九种GTC身份、四种历史损坏、同execId经济冲突、查询失败/过期/换代/跨日。|
|成功证明不能沿用为后续写权限|`v6lite/protection_engine.py:1463`、`v6lite/protection_engine.py:1618` 重新主动查证；`v6lite/overnight_evidence.py:299` 绑定原GTC/起始P/本次TP/历史；撤单恢复保留两轮查询/原ACK门槛。通用full_exec仍False，不扩大加量、重建或release|减量ACK前不发TP；10秒撤TP确认后同GTC恢复；TP1/TP2真实成交及末股TP3恢复；正常流程每个写边界保留原独立S+T+X≤P oracle。|
|保本价缺证、BE token空集通过|`v6lite/protection_engine.py:950` 过夜BE重新查a–d/历史，同日BE须full_exec；`v6lite/overnight_evidence.py:400` 精确十键绑定和行值，仍须本代native ACK|真实TP1后同GTC保本；同日缺ENTRY下载禁止BE；真实PRICE_PENDING清空binding或删modify_generation不能确认WORKING/不能开TP2，原GTC保留。|
|过夜限制被误升P0且重复提醒|`v6lite/overnight_evidence.py:131` position_id持久键，仅一次说明；`v6lite/overnight_evidence.py:146` 写明失败项、标的/数字/订单号与A、B非证明。纯TP限制不进通用P0|改原因、超过30分钟两次、SQLite重开仍一次；无假P0。真实缺保护/身份错/欠保护/外部SELL/F4/A/B告警保持。|
|失败提前返回导致每秒查询|`v6lite/overnight_evidence.py:168` 仅当代新鲜P=账本、唯一强身份WORKING GTC S=P、无pending/A/B/其他SELL才认健康；`v6lite/protection_engine.py:950` 更新时间，复用原15秒节流，不清旧retry/F4/hold|真实每秒tick共31秒：constraint缺ENTRY/未知成交四项下载仅t=15/30各一次；freeze原三项同节奏，额外account query=0；保护不变、无写。|
|默认freeze破坏b25静默接管|freeze是正常策略，健康接管且没有TP/BE需求时不报异常；实际TP或应改BE被限制时才说明，constraint失败仍即时说明；只调整呈现，不改权限|原B25 native接管24项绿色控制；原85测试不改。新policy增强低于TP时0说明→真实尝试后恰1卡，15秒/身份/容量不变。|
|新范围分支绕过外部SELL旧冻结，或挡住安全减量确认|`v6lite/protection_engine.py:950` scope前保留persistent external_sell/P0；gross应急先于拒绝return；仅MODIFY_PENDING同价严格减量可按原本代native ACK结算下降的保守上界，随后仍拒新权限|外部撤单/重启不解除冻结；外部先造成4>P3时只准原GTC减至≤2或撤单，确认前上界不增，真实ACK后再用原oracle验证≤P；无新TP/新GTC/加量/改价。|
|自身恢复期间旧数量快照被误判|`v6lite/overnight_evidence.py:335` 仅同日本次TP token/原GTC/历史digest/同价RESTORE_PENDING识别旧total；`v6lite/protection_engine.py:450` 仅将该旧观察加入身份候选，不确认新量|两项RESTORE_PENDING自然红后修复；同GTC2→3真实ACK、开关中途freeze仍安全收尾。通用恢复/坏token不获权限。|
|收盘后在13:05日报前停机缺日报|`v6lite/engine.py:3000` 保留已完成实现；`v6lite/engine.py:3011` 使用实际日历收盘时间及sent/local/pending持久去重。撤DAY/留GTC→SHUTDOWN→disconnect→补报顺序不变|35项：正常/半日市/休市/收盘前/dry-run、重复停机/重开、原正常重试、渲染/文件失败不阻断安全停机。|

## 红绿真值、配置影响与分工

本批6份spec共105节点：shutdown35、overnight33、edge20、external2、BE token2、policy13。最终同规格B25为77失败/28通过/0错误；最终相关聚焦243项全通过，其中完整包含105新增项及B25接管、停机止损回放。85份旧测试和7份scripts原字节保留，无删测试、改skip或弱化原容量断言。

历史红收据不相加为新增覆盖。初版shutdown 35个setup error属于Windows本地asyncio socketpair夹具错误，原件保留、不算产品红；校准后15自然失败有效。B25及早期候选部分深支case在健康BE前置失败，不夸称已到经济冲突/末股恢复；最终绿色执行了后续强断言。所有中途实质回归均保留原始stdout/JUnit/完整nodeid、源码patch/module，后修再验，无flaky豁免或隐藏重试。

独立测试校准保留原件与AST证明：默认freeze不被强迫额外账户查询；外部自行超限不能禁止旧应急减量，只有该参数分支改成严格紧急写监测；默认freeze未实际尝试前应静默。`_safety`本体及其他原安全节点未改，最后真实ACK后的容量断言继续驱动发现应急ACK生产回归。原B25接管静默断言未修改。`phase1_checkpoint`和旧matrix是前序快照，最终以acceptance_checkpoint及全量收据为准。

新增键纳入配置指纹；省略与显式freeze相同，constraint不同，指纹模型与B25也有变化。是否开启由John决定。本批没有读取或修改John配置、状态库或审批，不声称真实配置已完成启动验证。五例只增加默认freeze及说明，保留原换行格式。

生产只由唯一实施Agent修改，测试由另一Agent独立设计；ROOT串行最终验证，独立Agent只读复核。原调查/裁定/中途红证据与最终收据均入清单。实际卡片通过真实Engine/Alerter/AlertCard及MemoryChannel生成；没有手机送达或真实券商测试。

## 交付边界

仅dev提交、附注tag `v6lite-batch26` 并冻结等Claude复核；不合并main、不push。合并时机由Claude/John在10/15接管测试后决定。不访问运行工作树、John运行配置、用户目录或状态库；操作手册只保留原哈希，不读内容不修改。原有手册修改、item8研究和其他批次临时目录保留且排除提交。行号与摘录见 `docs/batch26_evidence/source_anchors.json`。
