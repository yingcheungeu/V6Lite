# V6-Lite 第十七批报告：L3恢复持仓保护、G8、心跳与账户流修复

日期：2026-10-05 PT。开发工作区 C:\Dev\V6Lite-dev，dev；运行仓库 C:\Dev\V6Lite，main。root是唯一生产实施者；独立Agent负责测试与复核，手册Agent只提供最终文案。本轮最终验证串行：不连接TWS/Gateway、不push、不输出机密；不触碰整个 `%USERPROFILE%\.v6lite`，运行配置、数据库与main不改动。报告第1–2节保留此前第十六批迁移的历史记录，不能当作第十七批已部署。

**最终状态：code_sha=`9725a81b8e5e3e11`。L3新测试9项和原五项保护开启强等价合计14 passed；Windows默认全量连续三次全部退出0，见第6节。第十七批按用户授权提交dev并打附注tag `v6lite-batch17`；报告随该快照提交，确切commit/tag对象及报告自身SHA在交付摘要回报。交付后冻结工作树等待Claude复核；经复核通过后才另行以 `--no-ff` 合并main并完成运行仓库验证，之后才能G3-T。本报告不是G3或Live放行记录。**

## 1. 第十六批提交与 main 整合

| 项目 | 结果 |
|---|---|
| dev第十六批提交 | `3661a0984722fbadbf3060752d6ccc36b088b6df` |
| 附注tag | `v6lite-batch16`；tag对象`5549b2118c4c881743e2bcd9721312b419fa7a65`，保持不变 |
| 原main | `5bd1167f43d512d3e743d15c3980d635dc2dc57d`（BT1） |
| 最初迁移预检 | 原main与tag分叉2／3独有提交且有未跟踪文档，故未自行执行非FF操作 |
| John后续裁定原文 | “我的裁定：用合并提交（git merge --no-ff）整合，保留两边的全部历史，不要 rebase，也不要改写 main。 未跟踪的那个文件先核对哈希，再作为单独一个提交加到 main。” |
| 单独文档 | `docs/V6_LITE_L7_G3T_INSTRUCTIONS.md`；SHA-256 `9eef73cde55ba2e1dfb6187d651012031419fd74d396ca83fda3c5b98db16cd3`；8890字节。tag16没有此文件，也没有dev副本，不伪称与既有清单匹配 |
| 单独文档提交 | `374737068eb545247f013cfba817d74c9aad857e`，仅上述文档 |
| main合并HEAD | `768af43c2ce31320ee50a91d04734bbb93a10185` |
| merge双parent | `374737068eb545247f013cfba817d74c9aad857e` + `3661a0984722fbadbf3060752d6ccc36b088b6df` |
| 历史与内容 | BT1、Batch16均为新main祖先；main v6lite与tag16无diff；未rebase/reset/改tag；未push |
| 合并审计 | 47项合并暂存文件只含docs/v6lite，无运行配置、数据库、状态、大于10MiB文件。唯一URL签名命中是旧`test_batch12_v2.py:26`的短`FAKE_URL`清洗测试常量，已确认不是机密，未输出其值 |
| 合并后指纹 | `code_sha=0f03a5d90c1ccad0` |
| 运行venv默认全量1次 | **674 passed in 74.99s**，退出0；Windows，20:00 PT，非禁跑时段 |
| main工作区 | 干净；两根配置字节不变 |

第十六批提交说明正文已写明 protection_engine.py 曾在编辑中被截断、按本地命令历史重建，并通过 AST、聚焦与三次 Windows 全量。提交前报告SHA `3bc64f3f4b3871851ae4c19c2d5c872f1f3b5368de6b227d120332e724625fb0` 与75项清单均一致；仅34项第十六批任务文件。

## 2. 合并后的运行配置／只读 status

使用运行仓库自己的 `.venv` 与第十六批main源码，不加载dev。加载两根配置并执行正式 `preflight_state(dry_run=False)`；对应正式数据库不存在时，实际调用 `cmd_status`、捕获输出后仅报告NO_STATE。用会抛错的`open_store`替身保证检查不会创建Store；未检查dryrun路径，未连接券商。若数据库存在仅允许SQLite `mode=ro`，本次均未打开数据库。

| 配置 | load／formal preflight／status | client／原port | protection默认值 | 第13→第16批指纹 | 字节／状态 |
|---|---|---|---|---|---|
| v6lite.paper.toml | PASS／PASS／PASS NO_STATE | 131／4002 | trigger_unfilled=60、steady_check=15、evidence_fail=60秒；enabled=true | fd2c114561c2e1f1 → e9128f2e9aff1ddf | 文件未变、正式DB不存在、未创建 |
| v6lite.paper.delayed.toml | PASS／PASS／PASS NO_STATE | 131／4002 | 同上 | 24674932ac7ca501 → a196a246bd79f7ce | 文件未变、正式DB不存在、未创建 |

当前schema要求4。以上通过不代表现有dryrun数据库兼容或真实TWS启动成功；John自行归档该目录。延迟行情配置仍按既有规则仅允许`--dry-run`，不得以静态加载通过放行正式交易。

另外，在内存中只把这两份配置的port替换成7497后validate均PASS：账户/client/protection/state字段未变，指纹分别变为`eb96a112ead5d863`、`8dfec7383feb51e5`；未写回原配置。G3手册第3版用临时合成配置、FakeBroker与API stub另做28项边界检查（正式131、TEST132、DU身份、readonly、dryrun+1000及五个写边界），最终串行重复通过。7497已属于PAPER_PORTS，没有为端口改交易／账户校验代码。原运行配置仍4002，G3切换由John按手册改专用配置并核对TWS。

## 3. 用户提供的10/5事实（本轮未独立读取实时日志）

Claude已复核第十三批实时dry-run，`d4b19f1d43b25448`：07:00/10:30 PT均`WOULD_BUY META data=live gate=ok`，告警48/48送达。11:20有2103/2105重连，11:21异常currentTime之后miss涨到127并反复无响应P1，同期报价/持仓流正常；John与time.windows.com差+0.59～0.61秒，11:00–11:40无Kernel-General事件1。此证据支持按异常回报恢复，而非永久判本机时钟错误，未把本轮FakeBroker复现写成实机演练。

08:36后空仓ACCOUNT_STREAM每4分钟WARN共66次；08:00另开Paper TWS挤下Gateway，1100/约30分钟中断；07:57/09:17真实用户名会话竞争10197使10标断行情。手册写同一TWS Paper、API7497、单Paper会话及交易时段禁止另登真实用户名，Live手机独立用户名列为待办。

## 4. 发现 → 改动 → 测试

| 项目 | 发现 | 改动 | 测试与裁定 |
|---|---|---|---|
| 心跳永久时钟锁存（G3前置） | `_gateway_clock_skewed`首个异常后阻断所有后续fresh reply，异常回复被累加为漏报 | 每reply记录server与实际callback接收host时刻及有符号秒差；独立streak，每次重评估。默认N=3异常才独立P1一次，N=3正常解除/恢复；常量可调，不加配置字段/额外改变配置指纹 | 修改前monitor基线10 failed/4 passed，重现130偏差reply→129miss含127；当前单旧sample日志后恢复、±偏差、连续3异常一次、长期40/130reply不重复、3正常一次恢复、新episode、N=2、真实callback样本均通过 |
| 时钟／无响应分开 | 原P1借用IB Gateway not responding | 专用标题“Gateway 时钟偏差”，正文signed秒数、3个编号步骤；不认定本机时钟有错。及时偏差callback不制造wire miss；已有真实timeout仍保留freshness门槛 | 两次真正超过30秒无callback仍原无响应P1；旧迟到callback不能解除；旧Batch12心跳竞态回归通过。无reqId无法证明所有迟到callback归属，故不放宽已开的timeout恢复 |
| ACCOUNT_STREAM空仓WARN | 平仓静默反复WARN；仅managed空不代表所有STK空 | 只改日志级别：最近成功server STK空快照、年龄非负≤现reconcile周期（默认300s）、reconcile_ok、无OPEN/active才DEBUG；其余WARN。每个FillEvent在监控入口失效缓存，包含同tick和excluded外部成交；查询失败亦失效 | 基线confirmed-flat为WARN红；sameclock booked fill补测22pass/1红；excluded MSTR真实external BUY补测自然红；修后全部绿。managed、外部STK、unknown、过期、clockback、queryfail/active均WARN；210秒重订阅与270秒资金门控、seen_at不伪造不变。只股票快照，不声称全资产为空 |
| G8 Secure System（G3前置） | psutil预取属性在AccessDenied时填None；尚未证明Python即按name缺失拒绝 | Windows Toolhelp32无需OpenProcess提供PID→exe名，两次同名同PID证据才豁免确定非Python；未知/Python缺cmd/global枚举失败仍拒绝；不设PID或Secure System白名单 | 修改前19case为4 failed/15 passed；当前19/19通过。Windows真实Toolhelp ABI/快照可读Secure System、own PID Python、真实G8 matched_count=0；没有输出cmdline。PID消失/异名/快照异常均failclosed |
| TP确定未发 | 原P0未知卡，但程序正在自动恢复 | 独立P1“止盈单未发出，正在恢复保护”，保留B起点/30秒未恢复P0，恢复后清去重 | 原告警基线6 failed/3 pass，现9/9；无报价/明确拒绝、29/30秒、恢复后第二事件立即再告警均通过 |
| TP迟到成交减量已确认 | pending／UNKNOWN不能称成功 | 仅同代/目标剩余量获券商确认分支P1，pending P0不变 | 真实取消TP、恢复GTC、late fill→pending P0→三证据确认P1，量价/ID/ref/编号保留 |
| 缺ATR阻BUY | 无实际持仓风险，创建intent前拒绝 | 入场拒BUY P1；已成交后无法保护仍P0 | None/0/NaN自然红→绿；BUY/intent均无；旧Batch15 oracle只更新批准级别并加强拒单证据 |
| dead variable | first_crossing唯一赋值、无读使用 | 删赋值 | AST全package通过 |

生产由root修改：engine.py（监控、既有入场拒买P1、本次L3目标重读）、approval.py（G8进程名字证据）、broker/ibkr.py（只读时间回调采样）、alerts.py（专用时钟卡片）、protection_engine.py（已批准的三处告警降级/去重与删未使用变量）。L3只修复成交入账后ENTRY保护hook的目标绑定，不改Store入账/重复与RELEASED隔离，不改订单参数、三证据加量、A/B时限、资金风险门控或broker写边界。其余监控修复不改变交易决策。`maybe_sent`第16批generic UNKNOWN原本就是P1，不假称原来P0；30秒B P0不变。

实现引用：`approval.py:54,101`；`broker/ibkr.py` currentTime回调与gateway_heartbeat_samples；`engine.py` _consume_gateway_replies、_refresh_equity、_fresh_positions、_ingest_fill监控cache；`alerts.py`专用时钟P1/INFO。以下清单绑定准确文件字节，Claude复核时以当前源码行号为准。

## 5. L3裁定 → 最小修复 → 红绿证据

Claude/John裁定原文：

> Claude 裁定 L3：同意候选最小修法——在 ENTRY 成交入账后的分支里，重新读取当前 intent，以持久化的 recovery_position_id（没有则用 position_id）作为保护挂单的目标；prior 仍只用于迟到退出的判断和旧状态判断，重复成交和 RELEASED 隔离的行为不变。保留现有 L3 强断言，不得放宽。

发现：旧`prior`是`record_fill`之前的SQLite Row。Store已原子创建恢复OPEN持仓并更新intent的`recovery_position_id`，旧Row仍指CLOSED，保护hook因此返回。修复：在`outcome.inserted`早退之后、刷新成交状态之后重读`current`；ENTRY目的和目标都使用`current`，目标为持久化recovery优先、否则position。`prior`的旧status/迟到退出分支未改，不用旧prior回退，不为未入账重复执行补发保护。只改engine此处目标绑定，Store和保护状态机未改。

| 五组用户要求 | 实际证据与强断言 | 红→绿 |
|---|---|---|
| (a) 迟到5股独立GTC | 旧10股GTC已FILLED；取消ENTRY迟到5股，独立OPEN与独立GTC=5。保护价从新持仓booked stop按3%limit计算，区别于旧持仓价格。在实际GTC提交前/后、确认后、TP减量确认后实际提交前、TP在途及成交后逐步核S+T+X≤P；无DAY STOP回退 | missing保护row自然红→PASS |
| (b) 收盘后迟到 | 时钟明确16:30ET；迟到ENTRY仍提交GTC，不套入场时段门控。PreSubmitted配合同代新鲜positions/openOrders/executions证据才WORKING，确认5股 | missing保护row自然红→PASS |
| (c) 合并仍OPEN | 已有4股GTC、旧ENTRY再迟到5股，Store把同一OPEN扩成9股。回调无三源查询、无加量；outer loop取三项新鲜证据后只same-idmodify到9，GTC总张数不增加 | 缺A起点/没调用现有hook自然红→PASS |
| (d) 已入账stop无效 | 对真实已booked恢复OPEN分别注入stop=0与0.50；既有protection_prices拒绝，P0“成交后无法建立有效保护价”，无新增GTC，status入场阻断；再跑priority仍无订单 | 缺P0自然红→PASS（2参数） |
| (e) 重启/重复 | 券商latefill先emit=False，真实Engine.startup查询回补，先PLANNED再两轮稳定三源提交；重复同exec和再次startup均不重复GTC/加股 | missing保护row自然红→PASS |

额外安全回归：实际原ENTRY的ATR=0/None，在Store建恢复仓时compute_levels失败并回滚；urgent callback保留P0“成交回调处理失败”和emergency锁，最终TSLA `_send`拒绝，既有GTC张数不变。两项在修复前本来PASS，不能列为本次修复红证据。事后把已入账atr改0却保留有效booked stop不等同于缺ATR建仓；该错误初稿故障模型已移除，未为其扩大生产变更。

实时恢复GTC尚未确认的额外测试：4/5/14/15秒分别验证不提前告警、5秒单次P1、15秒单次P0；异标TSLA真实ENTRY入口拒绝`protection_unconfirmed:AAPL`，未建intent/未发BUY。恢复收到placeOrder返回不等于获券商确认，仍需三项证据。`late_entry_fill`异常也持续阻断入场，待人工对账，不因GTC工作而自动清除。

| 原五项保护开启等价 | 场景 | 最终结果 |
|---|---|---|
| R8 stop fill during scan | 慢daily_bars中真实GTC成交，日损闩先于下一TSLA ENTRY | PASS |
| release counts as loss | 人工SELL、GTC CANCEL_SEEN、两轮三源证据、release净损/冷却 | PASS |
| R7 late commission | GTC平仓gross$3/预留$2先正，迟到fee$4转净亏，连亏/冷却 | PASS |
| L3 late ENTRY after close | 旧GTC平仓后迟到5股，新OPEN独立5股WORKING GTC，原强断言保留 | PASS |
| kill blocks entry but exits run | kill下DAY TP/GTC仍退出，新ENTRY被挡 | PASS |

既有恢复审计边界：全新恢复OPEN在record_fill时没有protection row，因此该exec没有完整的逐执行A审计行；重启回补在恢复模式先PLANNED、等待三源核对后首次提交才开始position级A计时，不能覆盖停机到查询回补前的完整空窗。本次按最小修法没有改Store审计，未声称FakeBroker能证明Live最长空窗≤5秒；Live放行仍须实测并由Claude判定。实时提交后的5/15秒计时与全局阻断已实际验证。

## 6. 串行最终验证与可复核日志

- 写代码前重新确认v6lite运行进程数量=0；所有Windows全量均在10/5 13:30PT之后。使用dev独立`.venv`、`-B`、`PYTHONDONTWRITEBYTECODE=1`、`V6LITE_TEST_PROTECTION`清空（默认全量），禁止pytest cache；测试状态/日志仅TEMP。未执行v6lite run/g2_probe或真实API连接。
- L3最终基线：在候选`6c62aae53befceba`生产字节未改情况下加入独立测试；9cases **7 failed, 2 passed in 1.32s**。七个失败覆盖a–e及实时gap；两ATR安全回归本来绿。只追加测试会改变全package code_sha，不把追加测试后的hash误称原候选hash。
- 根因最小修复后，同一冻结测试文件与原五强等价一起：**14 passed in 1.62s**；原五强等价文件未为L3削弱。
- G8基线4failed/15passed；监控基线10failed/4passed；ACCOUNT同clock补测1fail/22pass、excluded外部成交补测自然红；三降级原基线6fail/3pass，当前相关聚焦170pass/告警9pass。正式红绿日志保留如下，追加原本绿色的安全测试不冒称产品红。
- 历史未修L3默认全量：687pass/1fail；第一监控冻结`15e670d26b502641`为729pass/1fail；补external cache后的原候选`6c62aae53befceba`为730pass/1fail（76.22s）。这些是本次L3修复之前的真实结果；没有skip/xfail、放宽或删掉原L3断言来制造全绿。
- P1实际重复窗口30分钟、普通去重5分钟（P0同文10分钟）；旧test_batch17_alerts注释“15分钟”不准确，本报告澄清，测试行为为恢复清去重后新事件立即再报。

最终冻结package文件78项，code_sha=`9725a81b8e5e3e11`；AST 73个Python源文件全部通过。每次全量前后重新核指纹相同：

| 序号 | Windows开始/结束PT | 默认全量结果 | 退出/指纹 |
|---|---|---|---|
| 1 | 2026-10-05T20:45:11.912632-07:00 → 2026-10-05T20:46:32.070041-07:00 | **740 passed in 79.44s (0:01:19)** | 0 / `9725a81b8e5e3e11` |
| 2 | 2026-10-05T20:46:32.102369-07:00 → 2026-10-05T20:47:51.335319-07:00 | **740 passed in 78.54s (0:01:18)** | 0 / `9725a81b8e5e3e11` |
| 3 | 2026-10-05T20:47:51.377434-07:00 → 2026-10-05T20:49:10.420670-07:00 | **740 passed in 78.33s (0:01:18)** | 0 / `9725a81b8e5e3e11` |

默认全量0failed并不等于其余33项旧保护开启场景已完成；本批仅五等价及专项保护测试。没有重跑保护全开诊断去掩盖剩余债务。

| 日志／证据（TEMP） | SHA-256 |
|---|---|
| v6lite-main-batch16-default-full.txt | `b56d4fe773ab9d36dbef1535eed407aff5a5743576b21776c10b6f72b833d017` |
| v6lite-main-batch16-default-full.xml | `b5509d84aa524ddcb44b12f3976584655a1cadc6cc5ec075fd03a6ade36a72b5` |
| v6lite-main-batch16-config-ro.txt | `55089464128e9c3086b3301822c0dbc0eebae463d0667d22d669786c46110674` |
| v6lite-b17-heartbeat-account-baseline.txt | `9bd6b25105719e6d3b38db28b21dd840500779c8a22b6110e6e7e497f662cc1b` |
| v6lite-b17-g8-baseline.txt | `21b2b44515e6b1d657ea00154534d223f62b4e3e9b30d901bd4dff517e78ffb6` |
| v6lite-b17-monitor-cache-baseline-corrected.txt | `1a16aa1b3b22a9cff81d0c2c1f5ffdf5c47181186d1d9effff2ce4e6815a3505` |
| v6lite-b17-excluded-fill-cache-red.txt | `a9f10c15b88b4eab8bd2b2a7bacdee99796bcfe5dc4bfc96710b5b3c97fb77a0` |
| v6lite-b17-monitor-g8-focused-final-v2.txt | `f1494af4a63efc1cfdd8d37e6a8e5a99ad0abc1f85788513f0509d2130e54904` |
| v6lite-b17-final-default-full-expanded.txt | `d845e4a741100971c98fbaefe3dd7f1f375b8c2e157118e70cfaf92e9017b203` |
| v6lite-b17-final-default-full-expanded.xml | `c7214def36b48ca51928bf65d0f84318bbdf869371a7e5db79c5a61b11d00987` |
| v6lite-b17-final-default-full-expanded-v2.txt | `923c88ad4a31b5ccd14ff4b5925ff8dc0b9335a7c9edfe4719451d0b624303f9` |
| v6lite-b17-final-default-full-expanded-v2.xml | `45f11b6e27189d74f1eeffd95d39360aab5ec9f275dc8bdd3452ef7a0492252b` |
| v6lite-b17-runbook-7497-final.txt | `8619cb8e8d881bfaccf7bf3dfe6d1cfe30335b8fbc5a89e6732a29656eb8e3c3` |
| v6lite-b17-monitor-cards.txt | `164cec04d9210d6eb0528ef173b90f70629052812e218331f632f924e760ea9f` |
| v6lite-b17-windows-g8-smoke.json | `501cfe35785a0622b0eedddb00e4598e39924563783fe78ec36e8e85c333058a` |

| v6lite-b17-l3-baseline-final-v2.txt | `51870638c3995b3c960583ac9f36fe3530f0331e9ff8b68da7531ee62e53d6d3` |
| v6lite-b17-l3-green.txt | `38e1c02e0a7d59542fe49071f5ec1dcb2ee3c1d14e8e48b2fe54b061e92519db` |
| v6lite-b17-l3-rendered-cards.md | `3e2cf8454c782176951a149895be17c7373f33428a4fb3d6f52526df666311d5` |
| v6lite-b17-default-full-l3-results.json | `48e3c01b186ad3c2c4b285658b9dd912869c27dd22439fcdb299eb247050fbed` |
| v6lite-b17-default-full-l3-1.txt | `e9310e880244d13f196f73d36d0d0d8320aecff86b8a5db8956b7829359c786f` |
| v6lite-b17-default-full-l3-1.xml | `2ccf9375d0c96cbd4eb746ce2a37c34fa34aa3193633359258a1658692b3017c` |
| v6lite-b17-default-full-l3-2.txt | `ca67a617a3a24aabf3ebe3a4ed10c9eee6b728e305a9601fb8750c56649e1f32` |
| v6lite-b17-default-full-l3-2.xml | `9f42c4cac9d47bb4115d81bb544d02abbd0664ca8d439bcd83742cd8c8ace359` |
| v6lite-b17-default-full-l3-3.txt | `e5875f0ebdb1219273186f8e0ea07fcb74ba92acf58d8fb50de7fab04b987fdf` |
| v6lite-b17-default-full-l3-3.xml | `5add81ee58ae76090dc341311a4e691adf97c4754dd94987cb1b2fc1d61bbc2b` |

## 7. 其余33项：实盘前完成

第16批38项等价覆盖债务扣除本批5项，余33项如下；本批并未实施或声称完成。本批五等价和L3专项已全绿；G3部署仍等待Claude复核，以下债务作为Live前置保留。

- `test_engine::test_breakeven_stop_after_tp1_sells_remaining`
- `test_engine::test_exit_retries_then_escalates`
- `test_review_fixes::test_L4_trade_result_uses_net_pnl_and_estimates_missing_commissions`
- `test_review_round2::test_C2_prior_day_held_sell_never_blocks_the_stop`
- `test_review_round2::test_C5_exit_orders_are_stamped_with_the_real_send_time`
- `test_review_round2::test_R3_late_entry_fill_in_two_batches_is_fully_managed`
- `test_review_round2::test_R3_late_entry_replay_after_restart_is_idempotent`
- `test_review_round2::test_R3_three_late_batches_at_different_prices`
- `test_review_round2::test_R6_baseline_uses_prior_close_holdings_not_current_quantity`
- `test_review_round2::test_R6_full_exit_while_baseline_pending_counts_from_prior_close`
- `test_review_round2::test_R6_prior_close_unknown_blocks_entries_but_not_exits`
- `test_review_round2::test_R8_commission_arriving_during_scan_can_latch`
- `test_review_round2::test_race_late_fill_of_cancelled_stop_cancels_the_retry`
- `test_review_round3::test_Q7_queued_exit_work_is_inside_the_protection_budget`
- `test_review_round3::test_SR3_blocked_exit_cannot_livelock_the_tick`
- `test_review_round3::test_SR3_stop_starts_in_the_same_pass_that_abandons_a_take_profit`
- `test_review_round4_selfreview::test_a_stop_blocked_by_missing_evidence_pages_p0`
- `test_review_round4_selfreview::test_R5_difference_found_at_the_exit_boundary_blocks_entries_at_once`
- `test_review_round4::test_F1_failed_fresh_order_query_refuses_the_sell`
- `test_review_round4::test_F7_exit_is_re_decided_with_the_latest_quote_before_sending`
- `test_review_round4::test_F8_back_off_is_honoured_when_the_queued_item_runs`
- `test_review_round4::test_missed_buy_freezes_then_flatten_and_release`
- `test_review_round4::test_price_only_correction_blocks_entries_while_exits_continue`
- `test_review_round4::test_prior_day_partial_entry_is_frozen_then_released`
- `test_review_round5_selfreview::test_late_buy_after_release_of_a_recovery_position_is_evidence_only`
- `test_review_round5_selfreview::test_refused_sell_does_not_size_a_stop_from_a_wrong_ledger`
- `test_review_round5::test_R4F1_release_needs_the_position_stream_to_agree`
- `test_review_round5::test_R4F4_frozen_position_closed_by_its_own_fills_can_be_released`
- `test_review_round5::test_R4F8_refused_take_profit_that_crossed_the_stop_pages_p0`
- `test_review_round6::test_R5F2_old_order_without_a_position_row_never_books_after_flat`
- `test_review_round6::test_R5F3_batch4_state_release_record_alone_is_a_flat_point`
- `test_review_round6::test_SR6_A2_stale_position_stream_never_silences_a_stop`
- `test_review_round7::test_P2a_earlier_versions_evidence_rows_of_released_positions_refuse_the_state`

## 8. FakeBroker实际渲染样例

FakeBroker + MemoryChannel 实际渲染，所有订单号为测试虚构值。

### TP确定未发，自动恢复P1

🟠 P1 · 止盈单未发出，正在恢复保护
发生了什么：AAPL 持仓数量=20 股 保护单已确认=14 股 保护单可能=14 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 6–20） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-af8e78ffb0b14f30ac74 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:01:02+00:00 工作单证据时间=2026-10-06T14:01:02+00:00；no quote for immediately marketable TP
影响：止盈单确定没有发出，Lite 正在按三项证据恢复保护；原 B 空窗持续计时，累计 30 秒仍未恢复则升级 P0。
你要做什么：1）目前等待 Lite 用新鲜三项证据自动恢复保护；2）不要另下卖单或手动增加保护股数；3）从 B 空窗起点累计 30 秒仍未恢复，按 P0 卡片核对 TP 和保护单。

### B空窗30秒，恢复未确认P0

🔴 P0 · 止盈后保护缺口未恢复
发生了什么：AAPL 持仓数量=20 股 保护单已确认=14 股 保护单可能=20 股 止盈在途=待核实 外部卖单在途=待核实 未保护=待核实 股 可能超卖=待核实 股；当前保护状态待核实；orderRef=v6l-protect-af8e78ffb0b14f30ac74 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:01:02+00:00 工作单证据时间=过期或缺失；B gap 30.0s
影响：止盈流程尚未恢复保护，止盈在途 待核实 股；超时须核实撤单。
你要做什么：1）先撤销在途 TP 并核实撤单及成交；2）不要另下卖单；3）再核对并恢复同标的保护单剩余量。

### 迟到TP通用退出通知P1（保留原级别）

🟠 P1 · 退出成交迟到
发生了什么：AAPL TP1 订单 v6l-4cff50640542431b9ca2 已结算后又成交 1 股。
影响：可能超卖或形成空头；新入场暂停。
你要做什么：1）在 TWS 核对券商持仓及所有在途 SELL；2）确认/撤销冲突订单；3）若已形成空头，人工处理并核对本机 status。

### 迟到TP减量请求尚未确认P0

🔴 P0 · 止盈迟到成交，保护单减量待券商确认
发生了什么：AAPL 持仓数量=19 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=5 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–14） 股 可能超卖=待核实（可能上界6） 股；当前保护状态待核实；orderRef=v6l-protect-714901d464774c6b913f orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:01:12+00:00 工作单证据时间=2026-10-06T14:01:12+00:00；迟到止盈成交后持仓=19 股，旧保护可能最多=20 股，其他在途卖单最多=5 股；不要另下卖单；同一保护单的减量请求已提交，券商确认待核实
影响：迟到止盈成交可能使保护单超额，可能超卖 待核实（可能上界6） 股；减量待券商确认。
你要做什么：1）在 TWS 核对迟到 TP 成交及当前保护单剩余量；2）不要另下卖单；3）确认 Lite 已只减保护数量并重新对账。

### 迟到TP减量同代证据确认P1

🟠 P1 · 止盈迟到成交，保护单已减量
发生了什么：AAPL 持仓数量=19 股 保护单已确认=14 股 保护单可能=14 股 止盈在途=5 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-714901d464774c6b913f orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=108.47 限价=105.21；上次确认=2026-10-06T14:01:12+00:00 工作单证据时间=2026-10-06T14:01:12+00:00；同代订单状态和新鲜三项证据已确认同一保护单剩余=14 股；迟到止盈成交后的保护数量已获券商确认
影响：迟到止盈成交后的保护数量已获券商确认；当前可能超卖 0 股，仍须核对其余卖单。
你要做什么：1）在 TWS 核对迟到 TP 成交及当前保护单剩余量；2）不要另下卖单；3）确认 Lite 已只减保护数量并重新对账。

### 缺ATR，买单已拦截P1

🟠 P1 · 无法计算保护价，买单已拦截
发生了什么：AAPL 持仓数量=0 股 保护单已确认=0 股 保护单可能=0 股 止盈在途=待核实 外部卖单在途=待核实 未保护=待核实 股 可能超卖=待核实 股；当前保护状态待核实；orderRef=- orderId=- permId=- TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=0.00 限价=0.00；上次确认=未获券商确认 工作单证据时间=过期或缺失；买单已拦截，申请买入=3 股，买入限价=113.47，ATR=None；原因=missing ATR or valid plan
影响：保护价格无法建立，买单已拦截且本次不开仓。
你要做什么：1）确认买单已被 Lite 拦截且没有券商订单；2）不要绕过门控下单；3）修复 ATR 或价格证据后重新申请。

### 新增专用时钟告警与恢复：FakeBroker实际渲染

```text
🟠 P1 · Gateway 时钟偏差
发生了什么：连续 3 次异常 currentTime 回报；服务器时间减本机接收时间为 +120.000 秒。
影响：回报时间异常；此项告警不代表 Gateway 无响应，也不能据此认定本机时钟错误。
你要做什么：1）在同一 TWS 核对连接和时间回报；2）查看本机日志中的逐次偏差秒数；3）等待连续正常回报自动解除，不要另开 Paper 登录。

🟢 Gateway 时钟偏差已恢复
发生了什么：连续正常 currentTime 回报；最新服务器时间减本机接收时间为 +0.000 秒。
影响：时钟偏差告警已解除；其他交易门控仍按实时状态判断。
你要做什么：继续使用同一 TWS Paper 会话并观察后续心跳。
```

### L3恢复持仓的实际渲染（FakeBroker + MemoryChannel）

以下从真实恢复成交与hard deadline路径采集，未创建“恢复成功”新通知。ID与数量均为测试值。

### 🟠 P1 · 成交入账异常

```text
🟠 P1 · 成交入账异常
🟠 P1 · 成交入账异常
发生了什么：成交异常代码 late_entry_fill:v6l-268d843ffa81444c8db0。
影响：新入场暂停，账本成交需与券商核对。
你要做什么：1）在 TWS 核对成交和持仓；2）本机运行 status；3）核实后运行 clear-anomaly late_entry_fill:v6l-268d843ffa81444c8db0。
```

### 🟠 P1 · 入场后保护单尚未确认

```text
🟠 P1 · 入场后保护单尚未确认
🟠 P1 · 入场后保护单尚未确认
发生了什么：AAPL 持仓数量=5 股 保护单已确认=0 股 保护单可能=5 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–5） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-00969aa9d50e4f91841b orderId=1003 permId=51003 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=110.00 限价=106.70；上次确认=未获券商确认 工作单证据时间=2026-10-06T14:01:44+00:00；A gap 5.0s
影响：保护确认仍未到达，未保护 待核实（可能范围 0–5） 股；从空窗起点累计 15 秒才升级 P0。
你要做什么：1）在 TWS 核对保护单 orderId 与剩余量；2）不要新开仓或另发卖单；3）从空窗起点累计 15 秒仍无券商确认，再按 P0 处置。
```

### 🔴 P0 · 入场持仓未获券商保护

```text
🔴 P0 · 入场持仓未获券商保护
🔴 P0 · 入场持仓未获券商保护
发生了什么：AAPL 持仓数量=5 股 保护单已确认=0 股 保护单可能=5 股 止盈在途=待核实 外部卖单在途=待核实 未保护=待核实 股 可能超卖=待核实 股；当前保护状态待核实；orderRef=v6l-protect-00969aa9d50e4f91841b orderId=1003 permId=51003 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=110.00 限价=106.70；上次确认=未获券商确认 工作单证据时间=过期或缺失；A gap 15.0s
影响：持仓存在裸仓风险，未保护 待核实 股；新开仓继续冻结。
你要做什么：1）在 TWS 核对持仓与保护单是否真实存在；2）不要另下卖单；3）由 John 处理裸仓并保持新开仓冻结。
```

### 🔴 P0 · 成交后无法建立有效保护价

```text
🔴 P0 · 成交后无法建立有效保护价
🔴 P0 · 成交后无法建立有效保护价
发生了什么：AAPL 持仓数量=5 股 保护单已确认=0 股 保护单可能=0 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 5–5） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=unavailable orderId=- permId=- TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；触发价=0.00 限价=0.00；上次确认=未获券商确认 工作单证据时间=2026-10-06T14:01:44+00:00；ValueError
影响：成交后保护价格无效，现有持仓不能凭该价格获得保护。
你要做什么：1）在 TWS 核对真实持仓和可用保护价格；2）不要另下卖单；3）由 John 人工处理并维持新开仓冻结。
```

## 9. SHA-256清单（79项）

覆盖全部78个v6lite包文件（排除生成的__pycache__）及更新后的G3手册；报告自身不自包含，由交付摘要单独给SHA。运行配置、数据库、机密和临时日志不进入提交。

```text
7668de8ae4143ef53422d83ddfded05af3f5b6cd02d56dfa6a22e6e6081db690  docs/V6_LITE_G3_RUNBOOK_DRAFT.md
4ad81212d1863b4fc11dc87d8bc8c32ea272e95563095243292ed615ced31fd9  v6lite/README.md
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
8bd77a8d308d6f3e66995bb37be39fc5debdd964cf1395ca68cced32849ffab9  v6lite/__main__.py
4b422fd4f2c1e2c3297de04d6193cfdd80adb4dadeee428516fdae171ff00657  v6lite/alerts.py
70a3c24325b95216bb307dd261b60ccff88f0f13da3e2f94d6344e92f07b41e1  v6lite/approval.py
2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24  v6lite/broker/__init__.py
12bd8e7a0a4626c05e6e64123e90de062a47fe4a4d7bb5902972299bca67226c  v6lite/broker/base.py
bce0c6984d60d8aa2fbb075a0110ede72be9f546f95667b1613d07f0a358c1a5  v6lite/broker/fake.py
f75d081d485f56f9b998a0cc31e5d2c928e968750e10b52765b9bc124b693816  v6lite/broker/ibkr.py
0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1  v6lite/clock.py
b68128969b2ed7dc227d45fbafe116f122b0a1d230e8d057ef92ac61fe2e4d7d  v6lite/config.py
8bad237d6292fd24b533bd0e1e0fefb7d9e76b0cc63c1828eea3fde1c91a60ba  v6lite/engine.py
47e605b64d9b148a38dcc2697b3266ced4494f501b980987d6917424fed7bd22  v6lite/examples/live.toml
8d0836ae45356ed3650c33f3c3390a304ea5e073a5f0c0ca4ebc81542409e4a1  v6lite/examples/paper.toml
258febbcf40a112fae652f95b0e0f77b8f0151eb97101da4623c40ee75e8fc49  v6lite/examples/paper_delayed.toml
507dea6df5c4c36a58db1bdbda8921707a3fab9721b504be98abce72b26b23d1  v6lite/examples/paper_test.toml
1de7eb5637ee3c6abd1784570846d0edf8f91739ee97e2bb6e0b6651e34de093  v6lite/exits.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
25937bff88ac9c7aeeb5b354d2edb6fc187f4a9ce2e1736a1596537d4a3f2506  v6lite/models.py
3de59ef71678bf534a66d48f4a3e4b5f49b7bac5a391f1a051cf2e0e648474e5  v6lite/protection.py
1b7bc0f5a2bd75cb47c5512bfc975f5d63645dbccd4832c1599505a55d736d7a  v6lite/protection_engine.py
4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1  v6lite/reconcile.py
e6d9fe60d94229849af5b78dfe9d33bea8a8573a550e680a1d628fa5beebd523  v6lite/report.py
abc521f671e878a0ab5690e1fa178dc872d29b5396b86eaeb5f016be1594c687  v6lite/risk.py
c9b9cc4a116a7f61d9e660dd885d280ec2a9262671f2c3c8ab5c1515724e171e  v6lite/sizing.py
a47dd6e9c1a3ed59dcff0dcc0b62ba87d27dc6cae35e0359b2c54c70d662a2ce  v6lite/store.py
9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e  v6lite/strategy.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  v6lite/tests/__init__.py
aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb  v6lite/tests/conftest.py
0c28d019b52b61f5d0b477bd7b202c409ff6761a4b4830b70c77667a3d9787d6  v6lite/tests/helpers.py
5b875c8df97861e22427223e7065a74bddf04a5da1c7115b431871369d177c47  v6lite/tests/test_batch10.py
fb7f26a0bd1969400f2d769a0d65f4c7d3a18f0162332fd46ca9f1ed99c260f8  v6lite/tests/test_batch11.py
3dfc69a4f811549cb32c9c186bbfed3d24f7c1c956c3c743d9b28f6996037cfc  v6lite/tests/test_batch12.py
5691a234501cd71b85e3098e0cf6da2a74f2d4f5ca3eda2797a8abe17fab9af0  v6lite/tests/test_batch12_v2.py
12a3ed033079f129e7e70f610be004c498ea6f6f0283473f60b2eb80ad819140  v6lite/tests/test_batch13.py
5087c53709f4208d5dc4fbe9f74b100d50ebfb2060ccf9c9834358d2c3c7b257  v6lite/tests/test_batch14.py
706e7a108c53708273841f7c101169b41cf75aa3f32e14223963cbc33287ba2e  v6lite/tests/test_batch15.py
02bc3283c395ec7a6903e94fe9a024ce9b40e9da81c28bc10cccc3bce67fcdd3  v6lite/tests/test_batch16.py
d9c3c8f080748838a81392b1a12ca03347685a1be4692ae9159471ff2cc56a51  v6lite/tests/test_batch16_card_deadline_copy.py
52f746366b2b14dd7247533157c715060540b8fa45170c2af8b39f5f8dbf9a64  v6lite/tests/test_batch16_f5_copy.py
6d97cec645009fd6d90c223cfeb5c902866701f75c01f91a604caddd3a0e863d  v6lite/tests/test_batch16_f6_account_evidence.py
86c542de449c2adab7cae4d2bc476e5108ef7ef14d9c2f45686a2404a3b9d478  v6lite/tests/test_batch16_f6_global_mismatch.py
15c23d799539945057095f56bad2e8ba8a32974383eb8d778fba0f57ede2cb96  v6lite/tests/test_batch16_f6_release.py
9e2464cc21430b8b4e27dd8982b1a5aee0b554a5e0698691df3156715ad65b9c  v6lite/tests/test_batch16_f6_safety.py
e7ae2a84c08b2adadb1aa9994e835b5504db581401eecbf2bfd14678e984915d  v6lite/tests/test_batch16_f6_steady_fairness.py
733347eda5814cbd2a14f71a717c88274bdd51376cada2c9bceeab3fd2908c48  v6lite/tests/test_batch16_f6_steady_timeout.py
b2f9cf68a543999d08ab0567e6e6a5663f257d2525c48430f953de8bacfbc5dd  v6lite/tests/test_batch16_f7_price_flip.py
af5c1b6c62872c84536b222255c9a3b4229e2d7bf4ce1c4b55b1d4a780aa3539  v6lite/tests/test_batch16_f8_backoff.py
328269e2c8fa8a0547d1d16a5904a4d6cc5462f13b093b9e6c09eb87bd4be8ba  v6lite/tests/test_batch16_gap_evidence.py
dfc6a85711add46c7a6e8b966c3f75f38f2bbbc7bee1b3f827882d3d9ddba5ff  v6lite/tests/test_batch17_alerts.py
ecb49e23e9cbcd6ea2803aacb4b649d8b8794b1c79e65f266141c655efdfca8a  v6lite/tests/test_batch17_equivalence.py
4e21318f45dc6293fc4fbdc0f1c5be7c9d845782bf405fff8c6a51504bae5875  v6lite/tests/test_batch17_g8.py
3a4090ec58c265c599a072ec53f538669c7f879efdca5ce38102bed898ce4d73  v6lite/tests/test_batch17_heartbeat_account.py
e965ce76adb71525b1e1d018b84af3525be13fdeabd7f3f7f557dced42d675b2  v6lite/tests/test_batch17_l3_recovery.py
bb8967a536a8d187fc9f1d742be17c0b4b90e193ab52731ac06d6bff8a08f461  v6lite/tests/test_engine.py
cacaf5b3115d4a298fe8b3fe065d66fe70277b8ee4cd5d9643f0e2fff0ff5f9d  v6lite/tests/test_g2_day1.py
fe84ef645d99f421c64f670aa7f883d16b5a24c8c15b4dd30a4aa8f035b5ea06  v6lite/tests/test_g2_day1_selfreview.py
a0db6b981992a3cf20d9f959a679c608842f4619007331bae9b26908cee3ccb7  v6lite/tests/test_g2_day2_prep.py
ba38215d7a6946d60884d0e3dac99b641e7bcde058c4893b94cb9d62b6a6fe18  v6lite/tests/test_g2_probe.py
6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a  v6lite/tests/test_ibkr_adapter.py
eb91ef957bfd13864c2c92a5da2cf3a29ffdc10c9f5c74fe751daa8f4caecdd3  v6lite/tests/test_review_fixes.py
7ab32142a9a0ee2fdb54853297cf6eae619ce28de4cac4dc646a8a8035660822  v6lite/tests/test_review_round2.py
b5bf4e5c513e9d2310dc6404f147b633790cff0e894546e9176d6c26390a7427  v6lite/tests/test_review_round3.py
04cf584aa3b7e13842c1e7608d03e5f888b7a714ea073001ce85281fe1eb43cb  v6lite/tests/test_review_round4.py
8cf2bba5d54e2e644bc016e68abfe87f0ecbe26b6e54d5a44a329c61e6dc7da8  v6lite/tests/test_review_round4_selfreview.py
c45a477f2bb20f021b3d8040b3be6360d78bb8c95f0ef7ffd2ba069fdf31eeb0  v6lite/tests/test_review_round5.py
03adaac71b3ea18095732064c51f6f4741400adae738c3cfb9f840c247ec4a8e  v6lite/tests/test_review_round5_selfreview.py
c6eabe6e953cc75ccfa7e4fa87625a86d677ef5dd2fca9975cd101b3403d6be5  v6lite/tests/test_review_round6.py
bcb2baea638b6ba6c063aba7776aaf1137c3cd1df98297a08311cdaf899994a5  v6lite/tests/test_review_round7.py
a3589fae5996245f7246f1860875a847b6761ed9f8bab5a317ab5c7d9e5e86bf  v6lite/tests/test_review_round7_selfreview.py
c65ac1c396468aae3124ffcdc0f61b2ed483547ec1b0f47119f00950008b3736  v6lite/tests/test_review_round8.py
e927860575cb55769a910a0dfbced96b725922da6629010c894ec2d3c86b36c3  v6lite/tests/test_review_round8_selfreview.py
abb1c1b1742d30353157518676a980ea162902178945701f67aeb69b82f4cd1c  v6lite/tests/test_soak.py
732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f  v6lite/tests/test_strategy_parity.py
75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42  v6lite/tests/test_units.py
91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1  v6lite/tools/__init__.py
e756a719f9f8c1cded3d1123f67fa0097b504680589319be40619bc5d7f6763e  v6lite/tools/g2_probe.py
9aa52b23a3c8c08801f9d86c46ae1090a28c78dbde5d5b4b2923347bc74ece45  v6lite/version.py
```

## 10. 提交与冻结范围

父提交为第十六批dev `3661a0984722fbadbf3060752d6ccc36b088b6df`，本批dev提交与附注tag `v6lite-batch17`。提交前按exact path暂存：报告、G3手册、README、五个生产Python文件、批准修改的旧Batch15告警测试及五个Batch17独立测试，共14项任务文件。清单中其余文件未改；不暂存任何根运行toml、.env、state、DB、venv/cache。暂存字节与本报告manifest绑定，报告自身另核SHA；无真实机密和>10MiB文件。短FAKE_URL/文档占位值的模式命中经审查为测试或说明，不输出值。Git空白检查发现test_batch17_g8.py末行只有Windows CR行尾、无实际尾随空格；保留三次全量验证的精确字节，以`git -c core.whitespace=cr-at-eol diff --cached --check`验证通过。最终git和manifest/指纹由另一个Agent只读复核；提交和tag后不再改工作树，等待Claude复核。main不动、不push，不启动G3。
