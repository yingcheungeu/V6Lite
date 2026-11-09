# V6-Lite 第二十五批修复报告（2026-10-14）

状态：Windows默认全量连续3次通过；dev快照等待Claude复核，交付后冻结。

## 1 基线、输入与职责

- 工作区仅 C:\Dev\V6Lite-dev，dev分支；基线 v6lite-batch24 / 6ebedcfd13f84e98fb284f756b939e10afbfdee1，基线code_sha=2db6af8f04f145f6。
- 最终候选code_sha=ea71abe782e4cf68。算法：按v6lite相对POSIX路径（大小写敏感）排序，路径UTF-8、NUL、原始文件字节、NUL；SHA-256前16位。排除__pycache__，包含测试和样例。
- 指令SHA-256：b6e50798d7d943d4ac203c603a44fe7214e585964d8102414847fadb6e3e6e06；回调SHA-256：60424a3e6c31ec44d0194ecf532cfc280502f06e8eee0b0bddfcdd4b60af46e1。
- 唯一生产实施Agent：batch25_implement。独立测试：batch25_tests。独立调查/复核：batch25_investigate。root负责取证整合、验红裁定和串行验收。
- 不连接TWS/Gateway；不访问运行仓库、John配置、用户.v6lite或归档；不push。操作手册只核对预检哈希fb008f61f56afccd1125a116883b59be90bc797ee0f662fb9dcf120ccb82d3f6，不改、不提交。

## 2 事实与官方依据

给定日志只有36行。可直接核实：第5–6行orderId501/permId45208044/client132，SELL STP、lmtPrice=0.0、auxPrice340.52/GTC、PreSubmitted/whyHeld=trigger；第7–8行在API ready之前出现非主动End；第23–24行同步完成后因poison而renew；第28/30/33/36行可见过早限速P1、断线、恢复失败和入场阻断。

10/13正常SHUTDOWN unresolved=[]、7次循环及60.8秒P0来自指令提供的事实；36行片段不足以独立复算这些总数。合成隔夜测试的入场成本342.53仅用于复算测试盈亏，不宣称是John历史成本。

连接End来源：

- [IBKR API设置](https://www.ibkrguides.com/traderworkstation/api.htm)：存在Download open orders on connection设置。实际TWS勾选状态未知，本轮未读取。
- [IBKR官方eConnect说明](https://interactivebrokers.github.io/tws-api/classIBApi_1_1EClientSocket.html)：连接时Host可提供工作单；该官方旧页面标注已弃用。不能据此声称所有版本都必然主动推送。
- [IBKR工作单接口](https://interactivebrokers.github.io/tws-api/open_orders.html)：reqOpenOrders用于同client，reqAllOpenOrders用于更广范围，openOrderEnd结束下载。
- [ib_insync源代码](https://ib-insync.readthedocs.io/_modules/ib_insync/ib.html)：本机0.9.86的connectAsync发reqPositions，非readonly发reqOpenOrders，并同步当日executions；默认启动同步并不调用reqAllOpenOrders。adapter的主动完整查询才使用reqAllOpenOrders。本机SDK没有RaiseSyncErrors参数；gather(return_exceptions=True)路径可能仅记timeout并正常返回。

本机源位置见investigation.json：ib.py1748/1760/1762/1763–64/1782/1789–90、wrapper.py392–393、client.py344–357。API-ready（nextValidId和managedAccounts就绪）先于SDK完整同步。End没有请求编号，无法凭片段绝对归属每个版本的具体来源；实现依赖当前代、真实同步阶段和已登记请求计数，仍需下一次Paper验证。

## 3 连接快照、同步边界：发现 → 改动 → 测试

发现：B24在connect前安装钩子，但将ended>sent一律poison；下一次主动查询因此断开。在已有同client GTC的启动序列上，这会把合法连接快照End误判。原FakeBroker.connect只改连接boolean；旧原生夹具的connect没有模拟pre-ready工作单/End，因此1559 passed（另1 skipped）未覆盖该序列。

改动：只豁免当前连接代、正在握手/SDK同步、第一次adapter登记openOrders之前且无欠End的连接快照End；同步结束即关闭豁免。SDK已登记的End继续正常计数，不抹平欠账；旧代回调和同步后的无法归属End继续B24拒绝。连接返回时欠End或API尚未就绪，关闭未核实socket并失败，防止SDK吞timeout伪Ready；poison仍在主动fresh查询边界拒绝并renew，不能提供完整查询权威。确定未发送的请求只在无End/同代时回滚登记；不确定发送及End后抛错不回滚。

连接回调在创建时附快照角色，延迟至同步后消费也只写独立审计；不改last_raw_status、broker IDs、status_seen_generation、confirmed_at、A/B、人工hold或价格权威。真实Fill/Commission继续入账及去重。FakeBroker默认支持openOrder→orderStatus→End→synchronized，生成快照不调用fresh getters。

测试：原生Client/Wrapper/Future与本地transport重放connect→501(STP lmt0)→PreSubmitted→End→API ready→SDK sync→主动完整三源。真实SQLite正常SHUTDOWN后close/reopen，30秒内接管原501/S3/WORKING、不重连、不另挂、无P0/P1；停机期间STOP成交的变体正确平仓、盈亏、连亏和重复回放去重。0.0、有限值、UNSET仅broker_echo_lmt审计；account/client/conId/订单编号/aux/数量等强身份继续保留。同步后非主动End、旧代End、SDK显式与吞掉timeout、未发送/不确定发送反例覆盖。

## 4 重连上限与恢复完整性：发现 → 改动 → 测试

发现：固定30秒重连没有同因窗口上限。独立复核又发现原因中的动态generation/PID/reqId/elapsed/attempt可拆分同因，以及_recover失败但positions reconcile成功时被健康早返回跳过重试。

改动：每稳定原因300秒最多2次实际自动连接，第三候选连接前锁存停止并发一次P0“连接反复重建，已停止自动重连”，保留最新原因原文和编号步骤。仅归一带明确标签的偶发编号/耗时，不抹掉真实错误码/业务数值，不合并不同原因。完整恢复可重置连续退避，不能抹掉滚动历史；300秒过期、ConnectedTrue或正常tick不能自动解除P0。

保留旧首个断线tick立即尝试；失败后等待30、60、120秒（上限120）。同因实际连接在t=0、30，t=90第三候选被挡；不同真因连续失败在t=0、30、90、210验证退避。已连接但执行恢复不完整时保持pending/阻断，30秒到期在同socket重试；这种恢复查询不算新的物理重连。纯WORKING稳态查询timeout的原门控口径不扩大。

测试：同因预算、动态字段、不同真因、成功后历史不清、ConnectedTrue后仍锁存、部分恢复29.999/30秒及物理次数均有真实行为断言。

## 5 查询P1与隔夜限制：发现 → 改动 → 测试

发现：UNKNOWN/hold/普通pacing原先可触发限速P1；正常首次确认或SDK同步也被通知。初版候选仍有1项隔夜P1：三源查询已完成，但当天executions不能覆盖昨日ENTRY，于是full_exec=False被当失败。另4项失败是status_snapshot未显示现有_entry_gate的protection_unconfirmed；真实BUY路径原已阻断，未发现实际下单绕过。

改动：查询P1只在真实核验失败并登记退避、同步完成后通知；首次A等待<=gap_a_p1_seconds不发此P1，不清A、UNKNOWN、hold或截止告警。区分“仅历史窗口缺口”和“今天所需exec缺失”：只有原单稳态WORKING、强身份完整、无pending/A/B/TP/外卖单/hold、S=P=confirmed且今天执行齐全的稳定隔夜持有不制造失败。full_exec仍False，不能借此加量、重建、TP或解除旧事件；既有retry/F4和人工hold也不清，先前真实失败后即使当前下载完成，历史仍不足时仍需按原完整证据或人工规则处理；今日缺exec、身份/数量矛盾或真实查询失败照旧。status与实际_entry_gate共用既有保护确认条件。

测试：A的0/1/4.999/5.0秒、同步pending、真正失败仅一次P1、F4 60秒P0截止继续有效；隔夜稳定原单正控及历史缺口、今日缺exec/数量矛盾等负控；旧B15跨日禁止重建和原S+T+X<=P强断言不放宽。除一个明确未发送旧夹具的契约校准外，其余原测试文件字节与B24相同；原ConnectionError类型、entered及sent=ended=0强断言逐项保留。

## 6 红绿收据与Windows验收

初次验红有配置/原生pacer/合成价格夹具错误，保留全部原始txt/xml/json，不作最终行为红依据；处置说明见root_red_initial_review.json、red_review.json和first_candidate_review.json。有效第一次生产编辑之前：33项=24自然失败/9安全对照通过/0errors。旧规格原字节另存*_red_before_seed.py。

随后独立检查纠正仅新夹具的正常SHUTDOWN顺序：先按一致Fake身份停机，再绑定下一会话合成native permId；生产6哈希未动。以最终核心规格在B24重跑仍24自然失败/9通过。初版候选33项28通过/5失败如实保留；独立复核的新反例再先在冻结初版候选失败，之后才由同一Agent修复。新增第三规格首轮有一个外部SELL负控的非法初始S3+X1>P3，在原B23写前强检查失败；只校准合成初始单量为S2+X1=P3，保留原安全钩子和断言。部分恢复成功后仍保留既有recovery_failed手动异常，测试显式完成原人工clear后再检查允许入场，生产不得自动解除。原d930第三规格及两轮红收据保留。随后47通过/1失败只是异常字典键名应为anomaly:recovery_failed，修正这一测试oracle后48通过，生产六哈希未改。扩大聚焦240项239通过/1失败来自旧未发送夹具的普通ConnectionError语义不明确；仅此函数加显式maybe_sent=False的合成双类型错误，并保留原ConnectionError、entered、sent=ended=0全部强断言和真实wire/End零调用检查。新增相同Not connected文字在真实wire写入后抛出的独立反例，必须继续dirty，证明生产不能按类型或文字猜未发送。原旧文件和新增规格字节都另存保留。未把夹具失败称产品红，也未重试隐藏产品失败；没有时序flake豁免。

Windows本机dev .venv / Python3.11.9；PYTHONUTF8=1/PYTHONDONTWRITEBYTECODE=1，禁优化，清理继承测试筛选/已知机密环境名，禁自动plugin；child HOME/USERPROFILE/APPDATA/TEMP均隔离在本批test_tmp。每次全量前检查实际PT、分支/HEAD/空暂存区/无v6lite运行及不可检查Python进程/输入哈希；Windows Job Object在下个交易时段前5秒终止本次新建验收子进程树，不触碰运行进程。

|收据|结果|PT开始 → 结束|
|---|---|---|
|red_finalspec_b24_attempt3.json (source 2db6af8f04f145f6)|9 passed / 24 failed / 0 errors / 0 skipped|2026-10-14T07:08:39.679905-07:00 → 2026-10-14T07:08:43.506346-07:00|
|red_calibrated_seed_b24.json (source 2db6af8f04f145f6)|9 passed / 24 failed / 0 errors / 0 skipped|2026-10-14T07:22:14.940929-07:00 → 2026-10-14T07:22:18.737520-07:00|
|red_first_candidate_review_finalspec.json (source 7612508962288831)|7 passed / 8 failed / 0 errors / 0 skipped|2026-10-14T12:31:25.343128-07:00 → 2026-10-14T12:31:28.276747-07:00|
|red_review_b24_finalspec.json (source 2db6af8f04f145f6)|5 passed / 10 failed / 0 errors / 0 skipped|2026-10-14T12:31:29.980919-07:00 → 2026-10-14T12:31:32.743753-07:00|
|red_final_all_b24_fixture_locked.json (source 2db6af8f04f145f6)|14 passed / 35 failed / 0 errors / 0 skipped|2026-10-14T12:44:54.131900-07:00 → 2026-10-14T12:44:58.981997-07:00|
|green_first_candidate.json (source 471846ec66402fd7)|28 passed / 5 failed / 0 errors / 0 skipped|2026-10-14T07:22:20.746190-07:00 → 2026-10-14T07:22:24.278581-07:00|
|green_closed_candidate.json (source 049bd0499e453091)|47 passed / 1 failed / 0 errors / 0 skipped|2026-10-14T12:34:20.683488-07:00 → 2026-10-14T12:34:25.292517-07:00|
|focused_final_locked.json (source 579746f4f29c99eb)|239 passed / 1 failed / 0 errors / 0 skipped|2026-10-14T12:36:34.694886-07:00 → 2026-10-14T12:37:17.699230-07:00|
|green_final_acceptance.json (source ea71abe782e4cf68)|49 passed / 0 failed / 0 errors / 0 skipped|2026-10-14T12:45:00.781692-07:00 → 2026-10-14T12:45:05.231725-07:00|
|focused_final_acceptance.json (source ea71abe782e4cf68)|241 passed / 0 failed / 0 errors / 0 skipped|2026-10-14T12:45:07.014571-07:00 → 2026-10-14T12:45:50.072153-07:00|
|focused_crossday_release_acceptance.json (source ea71abe782e4cf68)|112 passed / 0 failed / 0 errors / 0 skipped|2026-10-14T12:45:51.836754-07:00 → 2026-10-14T12:46:03.951352-07:00|
|full_1.json (source ea71abe782e4cf68)|1608 passed / 0 failed / 0 errors / 1 skipped|2026-10-14T13:30:48.426564-07:00 → 2026-10-14T13:36:36.748889-07:00|
|full_2.json (source ea71abe782e4cf68)|1608 passed / 0 failed / 0 errors / 1 skipped|2026-10-14T13:36:38.536969-07:00 → 2026-10-14T13:42:26.101298-07:00|
|full_3.json (source ea71abe782e4cf68)|1608 passed / 0 failed / 0 errors / 1 skipped|2026-10-14T13:42:27.942400-07:00 → 2026-10-14T13:48:14.953677-07:00|

B24默认总集合1560=1559 passed+1 legacy skipped；新增规格49项，最终全量预期1609项；实际collection与执行nodeid、JUnit必须逐项一致。


## 7 独立复核、范围与后续

独立只读复核结论及六个生产文件哈希见independent_review_final.json；root串行验证，无并发生产编辑。只提交本批生产/新增测试及这一旧夹具差异、指令、已脱敏输入、本批顶层证据/报告/清单；排除Claude手册、item8研究、baseline、其他批临时archive/test_tmp、数据库/WAL/SHM、缓存、John配置和机密。提交前核对实际暂存范围、机密和大文件；附注v6lite-batch25不能覆盖旧tag。完成后冻结，等待Claude复核，不合并main。

下一次Paper重启接管仍需John验证原生TWS行为；本轮只有离线原生回放与FakeBroker，不把模拟称真实TWS实测。

## 8 实际卡片样例

# Batch 25 actual Alerter card samples

Source SHA: ea71abe782e4cf68

## 同一原因两次重连后停止；动态编号不重置预算；300秒后不自动解锁

🔴 P0 · 连接反复重建，已停止自动重连
发生了什么：原因：SDK synchronization incomplete; registered End missing: openOrders [generation=2 PID=9002 reqId=702 elapsed=2.125s attempt=2]；同一原因在 5 分钟内已自动重连 2 次，本次候选未再发起连接；已停止自动重连。
影响：全部入场保持阻断；当前券商端保护状态需人工核实，不能将旧缓存当作有效保护。
你要做什么：1. 按 Ctrl+C 停止 V6-Lite，等待正常 SHUTDOWN 完成；2. 在 TWS 人工核对持仓、原 GTC 保护单和全部工作单，必要时人工处置风险；3. 排除连接原因后再由 John 重启，完整核验持仓、工作单、成交三项证据后恢复。

## 同步完成后真正核验失败，重复查询只发一张P1

🟠 P1 · 券商查询已限速 · AAPL
发生了什么：同一持仓核验未通过，查询按 1、2、4、8、16、30 秒退避；等待期间不提供新的完整证据，原 UNKNOWN、人工核验 hold 和冻结继续保留。
影响：本持仓继续等待核验；其他持仓和连接心跳继续运行。
你要做什么：在 TWS 核对原保护单与持仓；保持冻结，不增加卖单或绕过核验。

首次A 0至5秒样例：查询限速P1=0；原A起点保留；入场继续阻断。正常隔夜接管样例：P0/P1=0，原单501/S3/WORKING；以真实引擎回放收据核验，不生成一张本应不存在的通知。

## 9 SHA-256清单

248项；机器清单docs/batch25_evidence/sha256_manifest.json的SHA-256为`6122892132d86d68727f64fc264fa800689e91ffa74e8261a78a5530ed15e8c2`。报告自身不进清单，避免循环，交付另报其SHA-256。

|文件|SHA-256|
|---|---|
|docs/V6_LITE_BATCH25_INSTRUCTIONS.md|`b6e50798d7d943d4ac203c603a44fe7214e585964d8102414847fadb6e3e6e06`|
|docs/batch25_evidence/acceptance_checkpoint.json|`5def53002986017c5e43c92a14dbb4e3a2f46a79e3e601df16b498f27f472953`|
|docs/batch25_evidence/acceptance_checkpoint_initial.json|`c71e1022fe81d4fff70ea2bc12ecaba9bb086c52cfbf420d411f4865a443fc0f`|
|docs/batch25_evidence/acceptance_process.py|`f96c9e873541f6d61575f9f09b25491ede1335fd24785627d06d6b3cdbdbc08d`|
|docs/batch25_evidence/baseline_receipt.json|`fbbc21442dab6f016553f0f8260ddf2c24e2ff5aef9ebab582561215c5dad4c1`|
|docs/batch25_evidence/build_batch25_report.py|`8654cb72fc18d46ff135593041abdce2535d5f4128d566d915bc71e854a7e959`|
|docs/batch25_evidence/candidate_closed_source.json|`465e988394917c1a95196eed11553ca1df157f6b4dfd1f6f284047edb2707b6c`|
|docs/batch25_evidence/candidate_final_source.json|`be9ac6aee22106555eae5dbb5d480d094ca24bf13b0056a3a39340d7f06c2a58`|
|docs/batch25_evidence/candidate_first_source.json|`9714c17962f9724a76ba4f571044200c4926046fa598a8681345d708ebc75153`|
|docs/batch25_evidence/collection_comparison.json|`f0d03e60b25cdab9f7a18873948217a4ad28da785d3aa38fccfd96211ad58afb`|
|docs/batch25_evidence/first_candidate_review.json|`6c2fafeaba0c9e9e6cdbc4038bceb100fe201be5c5ac93b8b98a1442ef9d77d7`|
|docs/batch25_evidence/focused_crossday_release_acceptance.json|`36c1d8f639aa6eea8f4855ecb6b668703383694873e82a07cbfed69fb784260f`|
|docs/batch25_evidence/focused_crossday_release_acceptance.txt|`83d551861e4d1b9a1f0b5c49f03d140704f3e1e594699727444d4dd266904f87`|
|docs/batch25_evidence/focused_crossday_release_acceptance.xml|`d888569db39b8708400d1e5d4d1d7d1f822b8cbe47339319f79749dcf0518baf`|
|docs/batch25_evidence/focused_crossday_release_acceptance_inventory.json|`f0ab537b2c78a48955c4af7ce2c49eb29f09c52726542233f2986260255f6f60`|
|docs/batch25_evidence/focused_final_acceptance.json|`986a0373534a228085c2ec53ae4f16dadafb20d9df7ce8a035e0596badd1092a`|
|docs/batch25_evidence/focused_final_acceptance.txt|`182d474aed6b148f03c0cae6703dab678e1975591c29828ed9df43aa632ad23d`|
|docs/batch25_evidence/focused_final_acceptance.xml|`adf452cd3e02d5a222c21bbc8cb07aca881e241c2c5962c1e4617c21e20e4e70`|
|docs/batch25_evidence/focused_final_acceptance_inventory.json|`4b88956e807ccc49590f574631bc2f754c7ac9a174715c4c288f4d5107af5d80`|
|docs/batch25_evidence/focused_final_locked.json|`7e01e490e79ff069d69cc30c8f90b2c1f1d4ec6766257d983995afb514959633`|
|docs/batch25_evidence/focused_final_locked.txt|`872763274c5226b66ba2393ae225a17b33c3a5994fa83c8e2cd037de5890748f`|
|docs/batch25_evidence/focused_final_locked.xml|`a6413d4c9815276a9725c4a1769c5b2da28b5e5e37267f532e5a82610300c78c`|
|docs/batch25_evidence/focused_final_locked_inventory.json|`6adae0b37277bdb112bbabd221f210048f199bf04c6ac39f3bedbc6363ecec1c`|
|docs/batch25_evidence/followup_red_review.json|`e85d53243b6fe396390f2d1e82d6036eacdf415095de0ddb6eef2067ccdc6f83`|
|docs/batch25_evidence/full_1.json|`83b7a6a6c63ec4c325a7699635b453e4bad114ee4b55fdb82e7e6659336f080b`|
|docs/batch25_evidence/full_1.txt|`b1eb94f9b49bd1949654f18f59f8db4e9739154d7a99bc95dacd0f5be7a09f04`|
|docs/batch25_evidence/full_1.xml|`4c5c59699f1ace65d09c79a58d1aad29f53bc6d10d4386c6d37e572b85528254`|
|docs/batch25_evidence/full_1_inventory.json|`7c0cae7b790248bbb0979884152a0a233ab3cdb1d34a5b0d270619a77c088abf`|
|docs/batch25_evidence/full_2.json|`76eadada91480786a1c4a0930e469de09b0b9cd11feecb20c8efa393d809aaa5`|
|docs/batch25_evidence/full_2.txt|`097ebfea0d3818b82edb00d7451f6f31471fb1576455da280dac57420ce3d52e`|
|docs/batch25_evidence/full_2.xml|`ca81f93e63971e5e0b67bc1fab2bf791c9ef2352e67715ab511cc47b4357f397`|
|docs/batch25_evidence/full_2_inventory.json|`7c0cae7b790248bbb0979884152a0a233ab3cdb1d34a5b0d270619a77c088abf`|
|docs/batch25_evidence/full_3.json|`fb6725c89dfd447df5ca7c83ab0b0b4616c93afebe253571aa3b1f5cae0f991d`|
|docs/batch25_evidence/full_3.txt|`7176a60785eee7e68a753969657e16d3bab4ec27617d61ea8527a33a587b1901`|
|docs/batch25_evidence/full_3.xml|`3ded0d0a707c6885c856d0c177f9aaa0d1b5197ec7ae4de9483fdb1d2326dfd3`|
|docs/batch25_evidence/full_3_inventory.json|`7c0cae7b790248bbb0979884152a0a233ab3cdb1d34a5b0d270619a77c088abf`|
|docs/batch25_evidence/full_acceptance_audit.json|`53a66ae32a21166119ffc21d0a6765daa45966a5e30c1cbc4a60970a6688e6ac`|
|docs/batch25_evidence/full_collection.json|`648af3c408ee685a3f8c53bac62dec4cca8c71fdc98e10cd5dca4643fa114854`|
|docs/batch25_evidence/full_collection.txt|`5637ddbe035d6fbce3ff165ca85ba4345e984fe5665610fbe46f83b9e60bd44d`|
|docs/batch25_evidence/full_collection_inventory.json|`e0a661a2de3f31f3eae3dd935fa824c929fd4eac1735cf960d9b763763006698`|
|docs/batch25_evidence/g3t_20261014_restart_loop.log|`60424a3e6c31ec44d0194ecf532cfc280502f06e8eee0b0bddfcdd4b60af46e1`|
|docs/batch25_evidence/green_closed_candidate.json|`69537dcb2c43b372b1847fccc4be197326ce6dbb0f350f74463e6749f203c3e0`|
|docs/batch25_evidence/green_closed_candidate.txt|`da87ce64de05e18fde98e0b7d669d9afcd962a0b99758b70ff8ad8fd1e866c12`|
|docs/batch25_evidence/green_closed_candidate.xml|`7f503ab035e123251758917c5450371c866003b68612f906985c934987ff49fc`|
|docs/batch25_evidence/green_closed_candidate_inventory.json|`5928c0005b89ce1b118d2924d4ae609a4d429d5d87e70dfecbdf47549b3f1e38`|
|docs/batch25_evidence/green_final_acceptance.json|`ae4ac2f08b0e342759b19d54dd562f24d8a5d50df9ab5ab614706423b740a66b`|
|docs/batch25_evidence/green_final_acceptance.txt|`69d41c84de1ad791cdde213b164721e73418d58da266649b03e573f397717bf7`|
|docs/batch25_evidence/green_final_acceptance.xml|`d519016f1a43df0061446647be5f88237eed0a205d9b58623b431ed6d233ddb9`|
|docs/batch25_evidence/green_final_acceptance_inventory.json|`fedb25aae9ae4359239741bcfdbcd6f12b50a3c5abfe06f928e635fa4b2349f4`|
|docs/batch25_evidence/green_final_locked.json|`eed089d2d9041876f924169618e62eba55c4d7eb41e562a3a12ecc8f653bcb97`|
|docs/batch25_evidence/green_final_locked.txt|`d8bbdf480f3198bd5a5268ddd097b15829a3cbaded374bc048136e8530dad835`|
|docs/batch25_evidence/green_final_locked.xml|`5321ad0d8e1fd6799e5c82faf48c220bea4a744d1a2a3b422a7453cd2cc2fa0a`|
|docs/batch25_evidence/green_final_locked_inventory.json|`5928c0005b89ce1b118d2924d4ae609a4d429d5d87e70dfecbdf47549b3f1e38`|
|docs/batch25_evidence/green_first_candidate.json|`a61d86c29a85d05d8ade8e39007ed59816ad77affc0facd9046a03322bf7ae65`|
|docs/batch25_evidence/green_first_candidate.txt|`92c6ef9f8b3d89ddd81ba92f309d5fc427f538f60b4b4762ee705a8a818c9bd6`|
|docs/batch25_evidence/green_first_candidate.xml|`2588d1fd5dd149c9c20a894dd6a9abc776efd0631475c2ba7d31fd13074ce5f9`|
|docs/batch25_evidence/green_first_candidate_inventory.json|`e8111e7cb10b9b7b7857bad60c21c4d65cb2c4b25d46c2daccfb14216bd5cab8`|
|docs/batch25_evidence/independent_full_review.json|`236405085d28bf42eaaaf5f11f2f9e1285447d0f9cf19d0172081ac1b63bc829`|
|docs/batch25_evidence/independent_review_final.json|`b113e3125eca79185622c6ed9400ef50ad5ae8372b460120d8372a84e489c2a7`|
|docs/batch25_evidence/independent_tests_final.json|`885b12b6c24a5a0a071db4fb8b0ac37cb2ab1f648a9202de7bd5c7a83422e213`|
|docs/batch25_evidence/investigation.json|`2854e8f06963bdf1a39676104973cb3545d57c0faf66d1b185ef5d95e8af2bce`|
|docs/batch25_evidence/red_batch24.json|`5790090bc01b49a835dec23196cef94d76637cce9c507ccb317f45c564decc01`|
|docs/batch25_evidence/red_batch24.txt|`235520c4bc644aff79dc0652f8a4ea55772cd02c06be8ccae2a890581a1a82c7`|
|docs/batch25_evidence/red_batch24.xml|`9f59562075d549089fae76a2fac80f0c5b5cc3b93ac3a9075539e6605cb41fd4`|
|docs/batch25_evidence/red_batch24_inventory.json|`76243d37d0ae053b19ae484f3c94b9f008dda8d3420390da0de053b7d39de65e`|
|docs/batch25_evidence/red_calibrated_seed_b24.json|`40fc3a6907abd20bd7020bad15c9db8e7e1d2d011a6252262231b2ae7df75432`|
|docs/batch25_evidence/red_calibrated_seed_b24.txt|`4b8b5f6cba9eaf64ed66b0523447def3e3e55ed202622607483bd17230ba6bb6`|
|docs/batch25_evidence/red_calibrated_seed_b24.xml|`4b2d957701241e1a64b70b15c5c1cf79e9fd5fe53dc14f39d2e799d45322b7f6`|
|docs/batch25_evidence/red_calibrated_seed_b24_inventory.json|`362cb30d946336b30a513f130e2eede239c44d382eb2249c9a4c15d20c4e5634`|
|docs/batch25_evidence/red_final_all_b24.json|`97d0ca4a6a5e79ff9d97c4baeca2191de0e29166de1515aa3b123cfc123d2483`|
|docs/batch25_evidence/red_final_all_b24.txt|`088562b2f3c10233dda7ec7a5d655b47c92c27bc0b4a23c84774655499185834`|
|docs/batch25_evidence/red_final_all_b24.xml|`d0b7a2c32bf43c0fa0fbe4664956cffbccc02ae5dc237889518e820e7d3a5ee7`|
|docs/batch25_evidence/red_final_all_b24_fixture_locked.json|`b7b4310c114c38fd89efeed5802e29d913b5d76a4ec311ef194a57e355b7da61`|
|docs/batch25_evidence/red_final_all_b24_fixture_locked.txt|`f58480fcd752b187402dc016f5c7fcc1ebdec0469b0e4fb16b1b7d781adaf18c`|
|docs/batch25_evidence/red_final_all_b24_fixture_locked.xml|`1a054d89703817bf6c0e406068526b7656783342f3473b512e544ad06b78805a`|
|docs/batch25_evidence/red_final_all_b24_fixture_locked_inventory.json|`9f2909d4b4b5a25cabd9394226cb4b21e03d5e1f1d104d15e1cacf25d867b715`|
|docs/batch25_evidence/red_final_all_b24_inventory.json|`607179d2d4c60126885913e87971adc9c9690d19fd219f696d209e86adfd34f8`|
|docs/batch25_evidence/red_finalspec_b24.json|`237770805bd06d6ac214005aafd6f1eabe11ce781690690b91a770fba3a77392`|
|docs/batch25_evidence/red_finalspec_b24.txt|`a9b5f79280ebebec2bd3ff5fd1400d51dfac6b1250b227087e780f03454562eb`|
|docs/batch25_evidence/red_finalspec_b24.xml|`8a34ba406e4524aca46ef8e6a5f67b7c1e5228e25aec26d0cc5e724cd142bfc4`|
|docs/batch25_evidence/red_finalspec_b24_attempt2.json|`69aadb1a025c8a4091d64fa0b0ee5993f074181c01fe2a8225430902917e3c51`|
|docs/batch25_evidence/red_finalspec_b24_attempt2.txt|`3dd4a8742aa0027cc62c2dfd08c6d289382e4e32e1b91ce135529faf06e971af`|
|docs/batch25_evidence/red_finalspec_b24_attempt2.xml|`fc840e18817f31a038ad41124106e98eab29a23d1e54b635dd9a2dc0b66a69b6`|
|docs/batch25_evidence/red_finalspec_b24_attempt2_inventory.json|`f8447422554dd3b482f7dba2052491fd9c2bef1b6c21be92af58d195395236ea`|
|docs/batch25_evidence/red_finalspec_b24_attempt3.json|`456ccb39c1103afb196344110ae2c46e5c82cf6ce414709fa1e1dac01b899bff`|
|docs/batch25_evidence/red_finalspec_b24_attempt3.txt|`51fdc9274d964ff8d20c3c5e956eb72b0466038be3747d4bc0a0a70c40983dca`|
|docs/batch25_evidence/red_finalspec_b24_attempt3.xml|`82853832913485986573e46d5a5b7017865c81bbaaaef05f2c2a70673bd803cd`|
|docs/batch25_evidence/red_finalspec_b24_attempt3_inventory.json|`a9ccbeca779c3585523837e83f34d6c62aa636cd0b6b322ee0aa1b06a3e429b3`|
|docs/batch25_evidence/red_finalspec_b24_inventory.json|`c902794300103dd82ea5e335f403c98683793f12b86606a1dd22eb28041e75dc`|
|docs/batch25_evidence/red_first_candidate_review.json|`8febfdb6a3f70d44875fec2a8a8c748d8d405d86b2e6771aa62c9c6d8cefe345`|
|docs/batch25_evidence/red_first_candidate_review.txt|`9d0e921f0866bea48dffe18741e91902e28bc0dfed8dde280174444a217e937b`|
|docs/batch25_evidence/red_first_candidate_review.xml|`bd43a81c087954357eeb07459d1a5106d40b5354a08a6740b188f17e6e31b525`|
|docs/batch25_evidence/red_first_candidate_review_finalspec.json|`9379cad6ea4bfa7c9c492f83df9fb12efc66dfbdb0012ba76b3479bc8186ffe0`|
|docs/batch25_evidence/red_first_candidate_review_finalspec.txt|`2d67cbdb2326b736d5734360728b448b2908c3f2008f4d4e96fac0a29c92c15b`|
|docs/batch25_evidence/red_first_candidate_review_finalspec.xml|`9eea94c5b8bdba88865a3610cb4a4b68903633bcc045532aac742ab4e4b77eaf`|
|docs/batch25_evidence/red_first_candidate_review_finalspec_inventory.json|`7cf2fdb69befce19356808d11b2f65175ee52b63929806e394c07a079be688c3`|
|docs/batch25_evidence/red_first_candidate_review_inventory.json|`7cf2fdb69befce19356808d11b2f65175ee52b63929806e394c07a079be688c3`|
|docs/batch25_evidence/red_review.json|`dadbb31001c09683aab545b8267173c154d88e0a81fd7919d881976cf7f00e55`|
|docs/batch25_evidence/red_review_b24.json|`7691058b4aace9ce9c0ca85145e223bda50cbf6def2cb9ba3930af2f629804a3`|
|docs/batch25_evidence/red_review_b24.txt|`a11bae5fa64b76e85235551351807704dcf4bacb10a19f1480dd6f3599ed46cc`|
|docs/batch25_evidence/red_review_b24.xml|`7c12c44a7e8cf49d1c0e7c133b9e4403336fe9afd94a0911e00fdb7da365d854`|
|docs/batch25_evidence/red_review_b24_finalspec.json|`b64a5343993df9ef3fc80c64f16e031ee6c428e9f94479ac0ed4f825526d6bef`|
|docs/batch25_evidence/red_review_b24_finalspec.txt|`53bab4503e56ac4244f4184d455775c392a88a0d3cde63c674d7e470d3318a0f`|
|docs/batch25_evidence/red_review_b24_finalspec.xml|`1fb63b880bb57a726496604d47ad26a8a54afbc032f7a2d2abbe992d9d638e0f`|
|docs/batch25_evidence/red_review_b24_finalspec_inventory.json|`c78e4e6fdc9a5bee3e0f47d650f7a87f31280850c194a861b0fc475c3bc28c01`|
|docs/batch25_evidence/red_review_b24_inventory.json|`c78e4e6fdc9a5bee3e0f47d650f7a87f31280850c194a861b0fc475c3bc28c01`|
|docs/batch25_evidence/render_samples.json|`ea0f797d991d9031585b6f7834ca561640d48e4f642144d40e59bac6f25c3bd6`|
|docs/batch25_evidence/render_samples.md|`4a2ce3e4f41d23cec056b83faae59cd91ed7177d35a07baee70592234df9fd79`|
|docs/batch25_evidence/render_samples.py|`4589b3b50712ce1e205944ad6314312ac407bdcfb7ea274010b532c838a8c63c`|
|docs/batch25_evidence/root_red_initial_review.json|`e89e3c8dbe8995686dfcd9efb23ae595c34dacae499b28eef332143b8089749f`|
|docs/batch25_evidence/run_batch25_checks.py|`26014b757be44e054b3eedf1e35fda15ae6a1299b7fec88f943e5087a8dac861`|
|docs/batch25_evidence/scope_precommit_review.json|`54f764e65ede218030b8d197f6dd2c8ece3f65c5b1a4bcd5fb772b9cf461835c`|
|docs/batch25_evidence/source_anchors.json|`8b28a5fff55825d95fdf19581e8add19a8ff0f041e4fdf0580c05e8c692363cd`|
|docs/batch25_evidence/test_batch25_reconnect_alerts_red_before_seed.py|`60ab5265d112a27e7cf6ada6fcac1fe9b39be272a1146b454df170dfab61bc45`|
|docs/batch25_evidence/test_batch25_restart_before_connection_error_case.py|`700733a5c19b48e905f5dc3577a77d65e59225492b4125afd765ba4c8f89c5e9`|
|docs/batch25_evidence/test_batch25_restart_red_before_seed.py|`25b86bf9f3a068e04bfa84dccffb86fa356a3b5696d904c731318cbd882b544a`|
|docs/batch25_evidence/test_batch25_review_red_before_anomaly_key_fixture.py|`6f85a548546f305e10b78b6c357afb71a60c07f3d6b5a253669c1961ff0ca88c`|
|docs/batch25_evidence/test_batch25_review_red_before_external_fixture.py|`d930fa1806241eb41bc60587890d46b7fd653c98fd4ed64a7e00a43f932329bd`|
|docs/batch25_evidence/test_review_round5_selfreview_baseline24.py|`86222acd134a13f3ab8937d1d7e8526b71bec2b98f6f70bbfc738df249d10ff5`|
|docs/batch25_evidence/tool_review.json|`bbb82725baad3ff175d239e1257367edb3e53d0dffb89497ee24797196cb77b7`|
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
|v6lite/broker/base.py|`21b95859f214f9d969f0aa1ac3cc2eb6dad865efd2b2ad76638136e5023ce593`|
|v6lite/broker/fake.py|`297d4aa54eec186f2b6843dadbaf3358876c65e3d186bcae5294f4d7d5db9def`|
|v6lite/broker/ibkr.py|`ccd7babe8badd4b7d8dec15942509f4aa5af0748ed20feaa9493476af449faab`|
|v6lite/broker/pacing.py|`08a81aff78939b7b1d6068e13650707173441cad51784ffbf870be5646672b17`|
|v6lite/clock.py|`0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1`|
|v6lite/config.py|`0318237f52b4306cf56378c8c51eb16c4f168083afe7c34d2132e21687282275`|
|v6lite/engine.py|`a197edf4c781eba435e7506a7ef2502724a95f15ac49db3db889fe650a1f6532`|
|v6lite/examples/live.toml|`c156a0d200c91931a39e90cb5279248b85376f2f59f772a54d8b657134830820`|
|v6lite/examples/paper.toml|`3dd7901f2af4dac79ff43bf4d1ff28d774b3adc590f2dfc54ee87c1058c5bad3`|
|v6lite/examples/paper_delayed.toml|`5c53d585657090a088d6d3fba98f811367e89924a106e9948d82c8f0dba29e54`|
|v6lite/examples/paper_g3n.toml|`5498230cbfe0c670a3ef3c6208327ef357c65f65e6b0b773bd5c544764b2a829`|
|v6lite/examples/paper_test.toml|`00ca15f04fe0975b0cad7ca7ad57436b62271563d7ae669147b9be0ed41bbb3b`|
|v6lite/execution_time.py|`6767aee2d823eccc2387106f803b38521bce94e71ec082fe6dd021918ee9f300`|
|v6lite/exits.py|`1de7eb5637ee3c6abd1784570846d0edf8f91739ee97e2bb6e0b6651e34de093`|
|v6lite/market_calendar.py|`1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2`|
|v6lite/models.py|`be454da959efd1aad8e2cc81ebdcb2d94ae2b0df79a22b8722ff93bb60fab473`|
|v6lite/protection.py|`fa4be7ec8e88bb2ee2c5cc77be2a6249725ff1bcd80e2865d056bba345d3f40d`|
|v6lite/protection_cancellations.py|`201be298237fb25a3af0f374d047964eeeb6f07024763c03af99ee4c6b43da8e`|
|v6lite/protection_engine.py|`3164fb157dedc662059397e61558a4dc35a11f7ddd7fb8a641c8eb0913e75439`|
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
|v6lite/tests/test_batch25_reconnect_alerts.py|`60ab5265d112a27e7cf6ada6fcac1fe9b39be272a1146b454df170dfab61bc45`|
|v6lite/tests/test_batch25_restart.py|`dd75b6fe9f3b8a416ca3f6fbf7479975264ed1c2dddd6c0fd6c899da7e134f2d`|
|v6lite/tests/test_batch25_review_regressions.py|`b882061f3259977c74b693565957e83274bed40b961c53d167661e3de840ce4c`|
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
|v6lite/tests/test_review_round5_selfreview.py|`2036e85729559e65c226bae7e8be2c8b9eeb9d561366711a5a4a3ee8f88c3882`|
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
