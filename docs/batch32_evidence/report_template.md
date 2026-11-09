# V6-Lite 第三十二批修复报告

日期：2026-10-29（PT）。{{STATUS}}

基线：v6lite-batch31 / 4456af6d1e70be1daa32d5d7870f34d422373383，code_sha 8ff7091296e313f9。
指令SHA-256：b22c24d2ab99765b4332446df20b781ea300eb90b37cb7ad588575daa8ca4c64。
候选code_sha：{{CODE_SHA}}；package SHA-256：{{PACKAGE_SHA}}。
只读证据manifest SHA-256：9e56247d3a27aee3544065669e08257b1be623c9ceea916d0bb94fbb68412b7c；3文件逐项通过，见claude_evidence_verification.json。原始证据目录未修改、不纳入allowlist。

## 重复来源、确认事实与回放

b31安装SDK `ib_insync/wrapper.py:380–385` 在openOrders future存在时逐次append同一缓存Trade；其orderKey对正orderId使用(clientId,orderId)，不含permId。因此最终SDK三条可能是同一个可变对象，不能据其最终一致证明原始回调一致。b31 adapter `ibkr.py:1639–1655` 每次原生query内openOrder另存deepcopy，`:775–776`采用这个原始列表，既保留差异也保留完全重复；不是在结果里额外拼接了另一个列表。

精确可复现的序列是：startReq('openOrders')→reqAllOpenOrders→相同2890/permId/2股/344.57/PreSubmitted的openOrder三次→openOrderEnd。SDK得到同一个Trade的三次引用，raw hook保留三个独立原始快照，b31再投影三行；不需要orderStatus。每次回调分别属于修改回报、查询响应还是其他推送，以及三者真实间隔，导出没有原始包，不能还原为确定事实。

10/29导出 `audit_20261029_open_tp1_be_window.jsonl:734`记录generation4；`:737`为binding/unique复合ACK门延后；`:738`明确三条已记录字段完全相同的自有2890工作单并以real_failure冻结，`:742`在36.756ms后确认。**过夜len(own)>1失败可确认。** ACK诊断本身没有细分request binding和unique两个子项；重复肯定触发唯一性拒绝，不能由那条复合reason宣称所有未记录时间条件都已被排除。

新回放从10/28真实入场、关库/重开、10/29过夜恢复产生overnight_tp_restored开始，经第一次TP1减量→超时撤单→恢复（gen1/2），第二次TP1减量→成交（gen3），再保本gen4；没有直接写持仓、恢复标记或proof。原生三回调在同一个真实SDK request/End内进入。首回调为改价后97.366ms，End对应+105.686ms，其他两个回调间隔为明确的合成重放安排。STP原始lmt=0、显式US/Eastern时间和已知价格/数量匹配导出。orderId2890、日期和价格保留；permId、execId、orderRef使用稳定测试别名。TP1档位是合成水平，因为该导出未含positions行；两次TP1顺序与代数真实执行，不宣称每个早期超时微秒均还原。

最终规格 `open_orders_replay_spec.py` 与包内test_batch32_open_orders.py同字节；在原b31生产归档先失败：red_b31_exact_tape返回3行、仍PRICE_PENDING；候选返回1行、完整End后确认gen4，无误冻/P1/P0，后续TP2按正常流程执行。SDK最终列表同对象3引用、规范化raw结果1行均有独立断言。

## 使用方与S的核对

以下代码位置指b31，候选在adapter统一生成一份结果，不让各消费者各自挑行。

| 使用方 | b31位置及重复影响 |
|---|---|
| 过夜a–d/恢复/健康展示 | overnight_evidence.py:619/626要求唯一，:298/523显示未知或不稳定；重复误冻 |
| 完整快照ACK | protection_confirmations.py:288同intent数必须1，三行延后确认 |
| 三源proof、progress/coverage/恢复 | protection_engine.py:611/697保存原列表，:1145/1212先拒绝多行；尚未进入:1275单行S计算 |
| TP开始/同轮成交消化/resume | protection_engine.py:1730；protection_confirmations.py:78/143；tp_evidence.py:343/499均受唯一性/逐行核验影响 |
| 启动/接管/client-id检查 | engine.py:330/517进入相同proof与progress；列表条数夸大但不能获得安全启动权限 |
| working缓存/SELL容量/reconcile/entry | engine.py:1389按intent建字典，:2540/1773/2748消费；完全重复不累加，但不同orderId同intent可能覆盖，所以必须在adapter先拒绝 |
| generic cancel、freeze交接 | engine.py:1563多matches拒绝取消，:1882清单重复 |
| release双轮、shutdown | engine.py:1937/2021任何工作单都拒绝release，:3107/3155可能重复列出；DAY取消集合按intent收敛 |
| 日报与TP审计S | report.py:78、tp_evidence.py:151使用本地confirmed/pending，不求和原始own |
| 过夜flow/取消/finalTP/incident | overnight_evidence.py:710取max；protection_cancellations.py:172、final_tp.py:284、protection_incidents.py:56间接消费proof/working |

**b31的三条STP不会直接把S=2累加成6。** 多数路径先拒绝多条，其他路径取max或本地confirmed。主要事故是有效保护证据被误判。原始others列表的自有TP数量T有重复求和风险；本批自有STP和自有TP都统一归并，容量回放实测S2+T1、P3、reserved3。外部未知订单不在本批归并范围，仍保守预留，不能声称所有外部X重复都已消除。

## 实现与安全边界

- adapter在账户/terminal过滤、价格/数量有损投影之前，按原生orderId检查整组。仅orderId/permId及强身份、全部contract字段、方向/type/tif/outsideRth、原始aux/lmt、total/filled/remaining、status均一致的自有行可归并。输入3、唯一1、duplicate_rows=2表示删除的额外行数。
- 数值精确比较不经过float或Decimal上下文舍入；int0/float0.0等值，bool不当数量。raw hook保存原始filled，非有限/分数/负值等不被旧0回退抹掉；坏3行变坏1行仍拒绝原始数量不完整。
- SDK fallback的可变Trade别名即使最后一致也不等于已保留原始证据；重复own组缺任一raw标记则整轮拒绝。唯一SDK行维持原路径；native raw singleton新增数量完整性守卫。
- 同orderId任何差异、不同orderId同intent均拒绝整轮，不返回部分结果、不发完整快照ACK。矛盾事件先定位orderId/permId/任一ref对应持仓，冻结所有已知受影响标的、保留overnight real_failure/P1和独立P0；无绑定部分记anomaly，原STP不撤改。
- duplicate事件只记OPEN_ORDERS_NORMALIZED审计、不发卡、不改证据时钟、不失效减量/final-cancel token；256项RAM幂等缓存不逐轮增加永久controls。真实矛盾使token和working时钟失效，排队事件未被处理时adapter也抛错，不能提前获得部分成功。旧socket矛盾仅审计，不用旧连接冻结新状态。
- 返回列表与owned_open_orders从同一规范化结果生成；原a–d、强身份、报价新鲜度、同socket/当前request/自己的End与S+T+X≤P未放宽。
- 不迁移或自动抹除已持久化的旧real_failure冻结，也不手改运行状态库；本批避免新误冻，既有记录仍按原操作边界复核。

## 10/27是否同因

确认：当日aux343.64已观察到、随后冻结、后来才确认；b30复合门包含len(own)>1。参考前批只读导出audit_20261027_tp1_be_window.jsonl:71–74/:101，旧manifest SHA-256 80c4d9646e2708b11b3f1c135bfbdad3a5f5beaeda22b62e02c95861d5633e00。

推断：同样的原生重复足以解释该次故障，但10/27没有own完整列表、条数、原生状态等，不能追溯确认当时len(own)>1。10/29不能补造前日缺失字段；b31证据回放的“raw state/count/duplicates未记录”限定保持。

## 开盘止盈调查和未实施方案

详见 `docs/V6_LITE_BATCH32_OPEN_TP_PROPOSAL_2026-10-29.md`；用户已同意默认30秒、0关闭的方案，明确移交第三十三批实施，本批不实施。首笔LMT346.85低于send时bid347.54，quote接收age .101538s，持续记录PreSubmitted；未发现原始拒单，202为本地主动撤单后的取消回应。现有材料不能确定Paper内部为何未撮合，不能声称该单参加Opening Cross。

20.017737s是审计恢复确认空窗：10.112079等待→1.491300撤单确认→4.364553至restore_request→4.049805完整恢复确认。18.128538已见3股STP回应，不断言券商整整20秒都少1股保护。官方Paper模拟最优档/无深度簿和Nasdaq Cross的背景与原始链接在proposal，均不替代本次缺失内部证据。

建议默认开盘30秒，仅在新TP阶段前按session.open_utc检查；不延后STOP/GTC、现有TP取消恢复，不改sell_limit及fresh quote/发线deadline。本批没有实现门控；config.py/exits.py/market_calendar.py/protection_engine.py和退出决策方法与b31一致。

## release手机卡片

成功释放的共用发送点给显式标题：`✅ 已解除冻结 · AAPL · 持仓 0 股`。P2级别/颜色、subject与原日志body保留，operator/reason不进入卡片；RELEASED与原已CLOSED两支共用，失败仍发P1，并按补充A在卡片写出具体缺项。成功标题本身不增加查询；补充A只为缺少在线撤单证明的保护intent增加只读completed查询，release不发送订单或撤单。

```text
{{CARD}}
```

## 红绿与验证

基线归档来自指定tag，baseline31_source.json保存archive/hash与原204文件inventory；全部原文件仍逐项一致，只有新增测试文件，未覆盖b31生产代码。red_b31_real_duplicates与校准已知lmt/时区后的red_b31_exact_tape均为3行/PRICE_PENDING反例。候选还先红并修复相同非法filled和坏单条绕过；初版测试的token取点不正确，改为真实StatusFirstServer ACK组件之间的注入，保留真实proof与≤1秒发TP要求。另一次缺Purpose导入、raw hook被测试框架自动重装是确定夹具问题，回执保留，不伪称产品故障或flake。

最终聚焦：{{FOCUSED}}
源码检查：{{SOURCE_CHECKS}}

## Windows连续三次串行全量

| 标签 | 实际结果 | 秒 | PT起止 |
|---|---|---|---|
{{FULL_RUNS}}

交易日06:00–13:30 PT只跑明确文件/用例的聚焦测试，未跑全量收集。三次全量必须同一完整case与skip身份、同一源码/inventory、SDK和只读输入，rc/failure/error=0；日志与XML hash逐项核对。失败不覆盖；修复重冻结并重启连续三次；第三次同一flake不豁免。b31既有legacy scanner缺文件的skip如存在会明确记录，不作为新通过测试。

## 全部回执（失败保留）

| 标签 | 实际结果 | source_unchanged |
|---|---|---|
{{HISTORY}}

## 补充指令 A

补充指令SHA-256：9f054900e0fe9843904685696dce0017c2a68c09c8538463739e312b6cd516ce。用户暂停旧13:31续跑，要求同一v6lite-batch32交付；裁定及七个附加条件保存在scope_resolution.json。原5bcb047f候选/工具/报告完整保存在pre_addendum_candidate.zip及freeze_pre_addendum.json，未覆盖旧冻结。

**能力与边界。** 安装SDK ib_insync 0.9.86支持reqCompletedOrders(apiOnly=False)（包含TWS订单）。decoder.py:1073–1287原生字段含account/conId/permId/orderRef、状态、filledQuantity和completedTime，不含orderId/clientId；默认0不得称为券商身份。wrapper.py:395–404新建Trade，orderStatus.filled默认0并非已成交证据。真实Paper账户是否返回10/29那条终态，因禁止连接券商，尚未观察；官方completed-orders说明的当前日终态可见性不保证任意历史日期可补证。详见addendum_A_capability.md和真实SDK能力probe。

用户明确允许四项本轮原生身份＋此前强确认且已持久化的orderId绑定。本次查询不认领、更新或回填ID。必须有查询之前的confirmed_at、现存相同intent/protection绑定；原生permId为非零且完全匹配，account/conId/orderRef一致；原生clientId非零必须一致，0/缺失只记录。若原生orderId实际存在，也必须匹配。SELL/STP/GTC、STK/USD、outsideRth=false；数量与auxPrice作为一对，完整匹配已确认值或当前pending修改值，不能交叉拼接。Cancelled/ApiCancelled、原生filledQuantity等于本保护单正常已入账量、completedTime严格晚于最后确认且非未来，缺任一项拒绝。

**原始证据。** 固定键completedOrders的每次请求及completedOrdersEnd都计数，SDK启动请求债务、超时、迟到End和socket代次沿用已有隔离。opcode99走现有wire全局10条/秒与open_orders_min_interval_seconds相同的completed独立查询间隔，无新配置。只有本轮原生decoder捕获的不可变结果可消费；SDK缓存、启动结果、旧row/旧decoder、部分解码结果均无新证明权限。handlers[101]实例守卫捕获SDK原本会吞掉的行解码异常，压缩日志不输出完整账户原始包，并要求解码/捕获数量相同。本轮同permId的多行必须原始全字段相同，包括完整101 tokens；任何内容、额外尾部或精度差异拒绝整组；完全一致才折叠并审计duplicate_rows。数量和auxPrice在SDK转float前捕获并精确比较，不能把小数数量或细微价差四舍五入成相等。时间解析消耗完整字符串，未知尾缀/时区、DST歧义或不存在时刻拒绝；无显式时区仅在明确TimezoneTWS下解析，绝不猜主机时区。

**release与原子性。** 只在缺少现有CANCEL_SEEN证明时查询completed。查询前后均按原规则刷新/入账自有executions；Filled终态或aggregate有成交时也不能跳过真实成交。aggregate本身不能制造execId、成交价/时间或虚构Fill。账户跨客户端成交只作完整性与两轮签名证据，外部SELL100/BUY98不传入Lite入账路径。保留两轮完整无工作单、两轮fresh持仓0、stream也0、执行签名稳定、静默期及无其他活动intent/hold检查，并明确首轮也必须flat。证明先放RAM；最终Store.release_position或resolve_freeze原有事务内，对保护/intent/position完整记录与booked量做CAS重核，然后写PROTECT_CANCEL_PROVEN_COMPLETED并完成释放、解除冻结和当天入场锁定。后续失败全部rollback；不提前伪造CANCEL_SEEN。审计明确native_order_id_absent=true、native_order_id=null、persisted_order_id=2890、persisted_perm_id=753795445、native_client_id_absent、先前确认时间、完成时间、请求/代次和匹配值。

**10/29回放与红绿。** offline_release_replay_spec.py与包内test_batch32_offline_release.py同字节。回放先真实10/28入场和过夜恢复，经历TP1超时恢复、第二次TP1与保本gen4；持仓OPEN2、STP2890/753795445由提交与强原生回报自然绑定。11:49:09ET（08:49:09PT）SIGINT停机后在TWS模拟器撤单；外部client0 SELL100/BUY98后关库重开，broker flat而持久化intent仍SENT。真实安装decoder解码completed Cancelled、filledQuantity0、缺orderId/clientId；客户端query排除外部成交，账户query包含外部两笔，真实wire过滤和字段均校准。日期、订单ID、permId和业务经过来自补充指令；账户/orderRef/execId及completed协议包/时间为明确的合成测试输入，因为没有提供真实completed原始包，不能宣称观察过Paper真实回报。

最终规格先在原b31失败：持仓仍OPEN，RELEASE_REFUSED为protected GTC cancel/identity unverified；候选RELEASED、冻结解除、当天入场锁定、外部两笔未入Lite fills，自有历史交易不被覆盖。负例覆盖无记录/permId0或错配/account/conId/ref/客户端与orderId矛盾、BUY/旧类型/DAY/outsideRth、数量与aux不匹配/原始精度、Filled/累计量不一致、缺时区/过旧或未来时间、同perm矛盾/未知原始尾部、decode坏行有End、两轮新成交、首轮非零、绑定变化及最终事务失败；除正常发现的真实自有fill必须入账外，经济表/intent/protection不变，保留审计及请求消费。真实待确认保本修改值也有正向回放。首轮红回放漏复制测试依赖、外部客户端范围未校准及卡片断言变更均保留回执，归类为确定夹具问题，不作为flake或产品故障。

拒绝卡片示例：`人工释放被拒 · AAPL`，`保护单2890的撤单未被核实：completed orders中未找到同permId的Cancelled/ApiCancelled记录`；矛盾perm、缺completedTime、签名改变等各自显示具体缺项，不拷贝任意broker异常/机密到手机卡片。成功仍使用`✅ 已解除冻结 · AAPL · 持仓 0 股`。旧在线CANCEL_SEEN路径及resolve-intent限制继续回归。

## 交付与冻结

freeze.json固定源码、SDK、工具、指令、只读输入和proposal hash；SHA256SUMS.json列任务源码、报告、方案及自有脱敏回执。精确allowlist排除原始证据目录、baseline_b31、其他历史文件与操作手册。三次通过后dev本地提交、annotated v6lite-batch32，核验tag archive全部inventory/package/code_sha及report/manifest blob。无push/merge/部署/券商连接/真实订单，不碰用户.v6lite、运行仓库和John配置。交付后冻结等待Claude复核。
