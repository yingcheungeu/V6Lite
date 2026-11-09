# V6-Lite Windows 独立复核与后续开发计划

复核日期：2026-09-30 PT。复核者：Codex。依据：仓库代码、整改报告与开发计划。

**Windows 全量测试通过，但 G3 的产品缺陷尚未关闭。** 本次确认只读隔离和 Windows 原子账户锁的离线实现；订单恢复、资金、日损和保护循环仍有可复现的 P1。限定为独立状态、Paper、无订单写入的 G2 可以进入真实行为验证，G2 本身尚未验收完成。Live 仍需 L7 实现与真实 Paper 验证。

源码审查和探针均为只读复核；探针仅使用 FakeBroker、FakeIB、MemoryChannel、临时数据库和临时锁目录。没有连接 Gateway、读取真实运行状态或 secrets、签发批准、stage、commit 或 push。本次只新增此复核记录，并在开发计划追加复核后的安排；交付的 32 个文件与原整改报告未改动。下文“建议改动”是待开发要求，不能当作已实现。

## 1 交付身份与 Windows 验证

| 验证 | 结果 |
|---|---|
| 原报告 SHA-256 清单 | 32 项全部匹配；忽略缓存后没有清单外的 Lite 源文件 |
| 原整改报告 SHA-256 | `215fe5760da27b30dc4bae24048e96d39f91255014f2f2ac52b3beed19f8d26f` |
| 平台与解释器 | `Windows-10-10.0.26300-SP0`；仓库 venv，Python 3.11.9 |
| 全量 Lite 测试 | `104 passed in 17.59s`；无失败、无跳过 |
| Windows 跨进程锁 | 全量测试覆盖 `msvcrt.locking`、持锁拒绝、持有进程被杀后释放 |
| 补充同时抢锁 | 两个进程同一时刻争同一账户锁：一个 acquired、一个 refused，均正常退出 |
| 策略比较 | 固定种子 1,000 组、7 个字段比较通过，没有跳过 |
| 六个控制命令错账户 | kill/resume/ignore/adopt/clear-anomaly/resolve-intent 全部返回 2，控制值未变 |
| 示例配置 | paper/live 均可加载；指纹分别 `a5f6f2b761645dc0`、`709dc549842f9772` |

复跑命令：

```powershell
cd C:\Dev\TradingV6
. .\venv_v6\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
python -m pytest v6lite/tests -q -ra
```

这是 `v6lite/tests` 的全量结果，不是旧 V6 的全量结果。补充探针不在交付的 104 项测试中；它们用于反证未覆盖的时序，下面给出输入序列和观察值，后续应固化为回归测试。涉及日期和价格的回放使用测试夹具的合成时间与行情。

## 2 P1 发现与修复验收

以下项目均阻塞 G3 的相应安全验收，不阻塞严格无订单写入的 G2。没有发现已证实的 P0。

### R1 孤立 working 订单被恢复静默忽略

**发现：** [engine.py:409](C:/Dev/TradingV6/v6lite/engine.py:409) 只遍历本地 active intents。服务器返回的 Lite working 订单若不在当前库中，不产生异常、不计入部署资金，也不阻断入场。账户锁不能避免崩溃后换状态目录遗漏订单。

**建议改动：** 对比服务器订单全集与账本；孤立订单及 client 身份不明时审计并阻断新增风险。采用有证据的认领或人工恢复流程，再恢复运行；不能只靠本地 active 集合决定是否允许更换 client ID。

**测试：** 旧目录发出未成交 AAPL ENTRY 后模拟崩溃；同账户、同 client 使用新目录 startup + tick。结果为两个 AAPL working ENTRY，`entry_blocks=[]`、`anomalies={}`、旧 intent 不在新库。补换目录、丢失账本和孤立异 client 订单回放。

### R2 跨日 UNKNOWN 被自动关闭且人工恢复流程不完整

**发现：** startup 正确设置 `UNKNOWN(prior_day_unverifiable)`，但 [engine.py:947](C:/Dev/TradingV6/v6lite/engine.py:947) shutdown 调用 `_settle`；[engine.py:495](C:/Dev/TradingV6/v6lite/engine.py:495) 未检查跨日证据，直接转 CANCELLED。此外，已有 managed 仓位的数量差异不属于 `unmanaged`，[engine.py:549](C:/Dev/TradingV6/v6lite/engine.py:549) 的 adopt 不处理它，ignore 也无法修复账本。

**建议改动：** 在最终结算边界统一保护跨日不明意图，shutdown 也必须遵守。人工 cancelled 仅裁定意图状态；历史漏记成交须有可审计的导入或恢复流程，不能把 adopt/ignore 当作通用账本修复。

**测试：** 原跨日测试后追加 `shutdown(grace_seconds=0)`，UNKNOWN 变 CANCELLED；另构造本地 10 股、broker 15 股，人工 resolve 后采用 adopt 或 ignore 均 `reconcile_ok=False`，账本仍 10 股。补 shutdown、运行中跨日、部分入账 ENTRY 和漏记 SELL 的恢复测试。

### R3 迟到 ENTRY 分批到达只管理第一批

**发现：** [store.py:441](C:/Dev/TradingV6/v6lite/store.py:441) 每批都通过原 intent 找到 CLOSED 原仓并尝试新建仓。第一批建立 OPEN 仓后，第二批触发同标的唯一约束，只记异常、不计入仓位数量。

**建议改动：** 持久保存原 ENTRY 到恢复仓的归属，后续分批成交合并进同一恢复仓；保持计划、费用与阶段的一致归属。

**测试：** 把原“一笔迟到成交”改为连续两批各 5 股：broker 10、账本 OPEN 5，出现 `late_entry_fill` 和 `duplicate_open_position`，随后 STOP 被数量差异挡住。补两批以上、不同价格、重启与重复回放。

### R4 先对账的迟到 SELL 导致退出恢复停滞

**发现：** [engine.py:683](C:/Dev/TradingV6/v6lite/engine.py:683) 在 `_mismatched` 时提前返回，绕过数量差异分支的 execution 刷新。没有 active 意图时，不再有其他路径主动恢复这些成交。

**建议改动：** 数量差异继续阻止 SELL，但必须保留独立、限频的 execution 恢复与复核路径；恢复不能依赖是否允许退出。

**测试：** 原 hidden late SELL 序列中，先令对账发现差异，随后四次 tick、每次间隔 61 秒：broker 10、ledger 20、`execution_refreshes=0`、SELL 数量未增加。原测试仅覆盖退出检查先于对账。补两种先后顺序和恢复后 STOP。

### R5 连续订单重复消费同一可用资金快照

**发现：** [engine.py:883](C:/Dev/TradingV6/v6lite/engine.py:883) 每单重新读取的仍是账户缓存，未协调在途入场预留。[ibkr.py:106](C:/Dev/TradingV6/v6lite/broker/ibkr.py:106) 也没有资金时间戳或账户就绪检查。

**建议改动：** 记录与资金快照协调的未结入场预留，避免重复消费及后续重复扣减；明确账户就绪、更新批次、陈旧阈值与重连失效规则。金额加费用的单单比较继续保留。

**测试：** AvailableFunds 固定 $2,500；不同板块两次入场均 sent，总名义 $4,538.80。补连续提交、部分成交、撤单释放、账户更新与断线重连。资金新鲜度属于原报告已承认的未完成项，应留在 G3 前，不降为仅观察风险。官方文档说明账户值更新有周期，`accountReady=false` 时可能陈旧或错误。[IBKR 账户更新说明](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-updates/receiving-account-updates)

### R6 当前持仓数量不能作为上一收盘的日损基准

**发现：** [engine.py:618](C:/Dev/TradingV6/v6lite/engine.py:618) 使用此刻 OPEN 仓的剩余数量计算历史浮盈；等待基准期间的卖出会改变基准。adopt 也没有证明股份上一收盘已存在。

**建议改动：** 持久保存或依据可靠成交证据重建上一收盘持仓数量与归属，并正确纳入当日实现盈亏、费用和新开仓。证据不足时阻断新增风险，退出继续；不能在人工清异常后假定基准已正确。

**测试：** 昨日 20 股成本 $113.47、上一收盘 $130、今日 $120。取 previous_close 失败后正常 TP1 卖 6 股，五分钟后恢复数据：系统日损 $100.82、日停 0，实际当日损失至少 $200。另在 11:00 adopt 当天买入 40 股成本 $100、昨收 $90、现价 $95：系统显示当天盈利 $200，实际亏 $200。补等待基准期间减仓、全平、当日新买、adopt 和重启。

### R7 收盘强制按缺费用的毛利裁定连亏

**发现：** [engine.py:264](C:/Dev/TradingV6/v6lite/engine.py:264) 在 force 时删除 pending；[engine.py:981](C:/Dev/TradingV6/v6lite/engine.py:981) 日报触发这条路径。之后费用到账不再纠正交易结果与连亏。

**建议改动：** 报表可以标费用不完整，风控结果应继续待定或可按成交时间重算；不能永久按未完整毛利清零连亏。

**测试：** 既有一次亏损，新交易毛利 +$0.20、费用缺失；force 后 streak 为 0。迟到 $1 费用使净额 −$0.80，streak 仍 0、cooldown 仍 None。补跨收盘、重启、乱序费用及多笔交易的时间顺序。

### R8 扫描期间新日损未进入最终入场门

**发现：** [engine.py:187](C:/Dev/TradingV6/v6lite/engine.py:187) `_service_protection` 入账事件后没有重算日损；最终 ENTRY 读取旧 `daily_stop_latched`。

**建议改动：** 入账后刷新风控，并在最终 ENTRY 边界用最新账本、价格和时钟验证日损；与 R6 的基准修复共同验收。

**测试：** AAPL 20 股成本 $113.47，扫描历史请求中 STOP 以合法限价 $105.91 成交；含费日损 $153.20 超过 $150，日停仍 0，同次扫描发送 TSLA ENTRY。补扫描期间成交、费用到账越过日损阈值的回放。

### R9 LIVE 最终入口消费旧告警健康

**发现：** [engine.py:166](C:/Dev/TradingV6/v6lite/engine.py:166) 只在 tick 开始缓存健康；历史扫描期间后台投递全部失败后，[engine.py:877](C:/Dev/TradingV6/v6lite/engine.py:877) 仍消费缓存。

**建议改动：** 最终 ENTRY 边界实时查询 `Alerter.healthy` 并刷新状态；退出继续。配置不可关闭告警的实现保留。

**测试：** 在历史请求中完成失败投递，`real_health=False`、`cached_health=True`，仍提交 ENTRY。补同 tick 失败与恢复、退出不受阻。实际影响 LIVE，但按既定第二批验收要求必须在 G3 前离线关闭。

### R10 历史请求仍会超过保护检查周期

**发现：** [engine.py:799](C:/Dev/TradingV6/v6lite/engine.py:799) 和 VIX 查询路径中，合约校验与历史请求可连续等待；“每标的调用服务”没有证明实际退出检查间隔。adopt 与日损基准请求也未纳入完整预算。

**建议改动：** 按下一次保护期限安排 I/O 预算或同线程异步调度，覆盖扫描、VIX、adopt、上一收盘及订单证据查询；测量实际保护检查时间。

**测试：** 三个标的各等待 8 秒，VIX 合约与历史查询合计 25 秒，实际退出检查时间为 0、49 秒，超过配置的 30 秒。原测试只统计服务函数调用次数。补所有慢请求和超时路径的最大间隔断言。

### R11 旧锁进程 AccessDenied 被当成不存在

**发现：** [approval.py:98](C:/Dev/TradingV6/v6lite/approval.py:98) 将 `psutil.AccessDenied` 与 `NoSuchProcess` 一同返回 False。不能确认旧进程状态时，正式运行仍可拿到 Lite 锁。

**建议改动：** 无法确认存活状态时失败关闭；区分进程已消失与无权查询。跨系统互斥仍需操作隔离，旧 V6 不识别 Lite 锁的限制继续保留。

**测试：** 临时 JSON 旧锁 `pid=12345`，模拟 `psutil.Process` 抛 AccessDenied；`lock_acquired=True`。补 AccessDenied、消失 PID 和不可解析锁测试。Windows Lite 原子 OS 锁本身已通过；这项是旧锁检查缺陷。dry-run 跳过旧锁，不涉及正式写单，因此不单独阻塞只读 G2。

### R12 execId 与费用更正尚未处理

**发现：** [store.py:385](C:/Dev/TradingV6/v6lite/store.py:385) 仅按完整 execId 去重；更正后缀被当新成交，账本增量重复。已有 commission 更正被 `INSERT OR IGNORE` 忽略。原报告已列 execId 更正风险，本次确认它会影响数量和风控，不能以事后人工发现代替 G3 闭环。

**建议改动：** 识别更正关系，审计原值与新值，替换或冲销其数量、价格、费用和风控结果；未支持的更正先识别、隔离并失败关闭，不能先当普通新成交入账。

**测试：** 同一 ENTRY 先接收 `0000.0000.0001.01` 的 20 股，再接收 `.02` 价格更正：账本变 40 股，入账时 `anomalies={}`。同一 execId 费用 $5 更正为 $9，库仍为 $5。官方定义明确更正只改变最后句点后的数字。[IBKR Execution 字段](https://www.interactivebrokers.com/docs/tws-api/ref/execution)

## 3 P2 缺口与待验证项

| 项目 | 发现 → 建议改动 → 已执行测试 |
|---|---|
| 日报投递 ID 重启碰撞 | ID 每进程从 `a1` 重置；持久 `pending:a1` 可被新进程自检 `a1` 成功误标 sent。使用进程唯一 ID，旧 pending 转待重试。探针只有自检消息、日报却已 sent；日报行为未关闭。 |
| 失败重要告警被去重 | 首次 P1 发送失败，1 秒后重试被 dedupe；随后自检恢复健康，原 P1 未送达。区分成功、pending、失败，保留失败重试。补充探针已复现。 |
| SMTP 端口配置未严格检查 | 临时 TOML 的 `smtp_port=true/-5/2.5/nan` 均可加载。补 AlertConfig 数值校验；有效示例配置仍可加载。 |
| status/report 读取错账户库 | 六个写控制已拒绝错账户，但 status/report 仍返回 0；report 不展示绑定账户。统一只读身份校验与账户标识。临时库探针已确认；这不是控制值被修改的证据。 |
| 固定 $2 费用预留 | 可作为当前配置下待证明的界限，不能称通用足额。依据实际账户费率、允许股数和交易费用验证上界；$2 不能代替资金预留或新鲜度。IBKR Pro Fixed 的每股费用可以令较大股数订单超过 $2。[官方费用表](https://www.interactivebrokers.com/en/pricing/commissions-stocks.php) |
| 真实 adapter 费用恢复 | 本机 ib_insync `Wrapper.execDetails` 查询重复成交会新建空 CommissionReport 的 Fill，费用通常更新缓存对象；Lite 只取查询返回对象的 report。连接后才注册回调，也需验证启动期间费用恢复。当前为实现检查发现的疑点，尚无真实 Gateway 反证；需适配器专项测试及 G2 观察。 |

这些 P2 工具缺口不单独阻塞 G2/G3。实际用于订单与风控的费用、资金和日损缺陷仍按上节 P1 处理。

## 4 原整改报告第 2 节逐行裁定

`CLOSED_WITH_EVIDENCE` 只关闭该行明确声称的局部行为，不表示整包或 broker 阶段已通过。`IMPLEMENTED_VALIDATION_PENDING` 表示实现存在但尚欠运行证据；`OPEN_PRODUCT_DEFECT` 表示有反证或依赖的安全闭环已被推翻。

| 行 | 状态 | 发现 → 当前改动 → 测试及限制 |
|---|---|---|
| L1 已有阶段重试只读 | CLOSED_WITH_EVIDENCE | submit/cancel 最终边界均拒绝 dry-run，恢复、超时、事件入账跳过；Windows 回归通过。现测试只实际构造 pending STOP，注释中的 SENT/UNKNOWN 组合仍值得补。 |
| L1 正式状态混用 | CLOSED_WITH_EVIDENCE | role 固定、独立目录/日志/报告、只读复制 dispositions；正式库交给 dry-run 被拒绝。 |
| L1 只读撤单边界 | CLOSED_WITH_EVIDENCE | 适配器在调用物理 cancel 前拒绝只读及非 Lite 前缀；回归通过。 |
| L2 原子账户锁 | CLOSED_WITH_EVIDENCE | 账户级 OS 锁与 state_dir 无关；Windows 跨进程、崩溃释放及同时争抢通过。适用于当前用户的共同锁目录，不宣称跨 Windows 用户全局互斥。 |
| L2 旧 JSON 锁 | OPEN_PRODUCT_DEFECT P1 | JSON/旧文本/坏内容解析已实现并通过；AccessDenied 仍放行，见 R11。 |
| L2 控制命令身份 | CLOSED_WITH_EVIDENCE | 已存在库的 account/mode/formal 校验；六个错账户控制命令均拒绝、值未变。查询命令的 P2 缺口另列。 |
| L2 无库 status | CLOSED_WITH_EVIDENCE | 无库不创建目录/状态；回归通过。 |
| L2 运行中批准 | CLOSED_WITH_EVIDENCE | tick 与最终 ENTRY 均复核；撤销后阻断入场，STOP 继续，回归通过。 |
| L2 旧 V6 不识别 Lite 锁 | IMPLEMENTED_VALIDATION_PENDING | 产品限制未改变；需停用旧入口及自动重启并留操作证据。README 说明不能当成自动互斥已实现。 |
| L3 新鲜 working 查询 | IMPLEMENTED_VALIDATION_PENDING | 已使用 reqAllOpenOrders、15 秒请求超时和账户/前缀筛选；FakeIB 回归通过，真实范围和超时待 G2。 |
| L3 working 后查 execution | CLOSED_WITH_EVIDENCE | 调用顺序测试通过；只确认顺序，推翻“顺序必然证明完整成交”的额外断言，见第 5 节判断 2。 |
| L3 查询失败保留 UNKNOWN | CLOSED_WITH_EVIDENCE | 失败分支不裁定终态、恢复失败锁异常；现回归验证 working 查询失败。execution 查询失败专项覆盖仍应补。 |
| L3 跨日 UNKNOWN 人工裁定 | OPEN_PRODUCT_DEFECT P1 | startup 改善成立，shutdown 自动关闭及 managed 差异恢复不完整，见 R2。 |
| L3 client ID 变更约束 | OPEN_PRODUCT_DEFECT P1 | 本地有 active 时拒绝的测试通过；孤立服务器订单可被遗漏，约束不完整，见 R1。 |
| L3 shutdown 撤 UNKNOWN/孤立订单 | OPEN_PRODUCT_DEFECT P1 | 查询并请求撤单范围扩大，但绕过跨日人工边界；可见不等于有权撤销，见 R2 与判断 4。 |
| L3 CLOSED 后迟到 ENTRY | OPEN_PRODUCT_DEFECT P1 | 一批新建管理仓测试通过；多批只计第一批，见 R3。 |
| L3 迟到 SELL 与重试数量 | OPEN_PRODUCT_DEFECT P1 | 先退出检查的回归通过；先对账会阻断执行恢复，见 R4。 |
| L3 部分 ENTRY/TP 后真实数量 STOP | OPEN_PRODUCT_DEFECT P1 | 基本撤单流程存在；依赖 R3/R4 闭环，当前不能保证所有真实剩余量可被管理退出。 |
| L4 实际 equity 余量 | CLOSED_WITH_EVIDENCE | min(NLV, 上限) 扣已部署/在途/预留正确；低 equity 回归通过。 |
| L4 AvailableFunds 约束 | OPEN_PRODUCT_DEFECT P1 | 单单金额门存在；连续订单重复消费及新鲜度未闭环，见 R5。 |
| L4 重复 execution 补首次费用 | CLOSED_WITH_EVIDENCE | 相同 execId 不重复数量，首次缺费可补；回归通过。更正与真实适配器疑点另列。 |
| L4 非整数成交不取整 | CLOSED_WITH_EVIDENCE | 适配器发异常、不入账并阻断；FakeIB 回归通过。 |
| L4 严格配置 | OPEN_PRODUCT_DEFECT P2 | LiteConfig 与键分区检查通过；AlertConfig.smtp_port 没有覆盖，见第 3 节。 |
| L4 上一收盘日损基准 | OPEN_PRODUCT_DEFECT P1 | 简单隔夜样例通过；减仓后基准和当天 adopt 被反证，见 R6。 |
| L4 待费用连亏结果 | OPEN_PRODUCT_DEFECT P1 | 正常补费等待通过；收盘 force 永久漏掉后续净亏，见 R7。 |
| L5 后台 SMTP/HTTP | CLOSED_WITH_EVIDENCE | worker 只投递、不访问 broker/Store；慢通道测试通过，补充 100 条投递均成功且 ID 唯一。 |
| L5 最近结果告警健康函数 | CLOSED_WITH_EVIDENCE | 最近一次全部失败立即返回不健康，回归通过；主循环缓存消费缺陷见 R9。 |
| L5 LIVE 告警必需且失效阻断 | OPEN_PRODUCT_DEFECT P1 | 配置不可关闭已实现；同 tick 失败后仍 ENTRY，见 R9。 |
| L5 历史请求期间保护 | OPEN_PRODUCT_DEFECT P1 | 服务调用存在，但检查间隔 49 秒及日损刷新不足，见 R8/R10。 |
| L5 入场用新时间检报价 | CLOSED_WITH_EVIDENCE | 每个决策用当前时钟；24 秒慢取数导致陈旧报价被拒绝，回归通过。 |
| L5 日报确认投递再标 sent | OPEN_PRODUCT_DEFECT P2 | 同进程失败重试通过；重启投递 ID 碰撞可误标 sent，见第 3 节。 |
| L5 status 健康快照与 STALE | CLOSED_WITH_EVIDENCE | 临时库 CLI 冒烟 age=300 标 STALE，连接/对账/告警/equity/阻断字段齐全；告警缓存准确性受 R9 影响。 |
| L6 策略比较复跑 | CLOSED_WITH_EVIDENCE | Windows 的 1,000 组固定样本比较通过；不关闭真实数据源与剩余日历差异。 |
| protected/ignored 外来成交 | CLOSED_WITH_EVIDENCE | 无 intent 的保护/忽略标的只记审计；回归通过。 |

共 34 行：18 行 CLOSED_WITH_EVIDENCE，14 行 OPEN_PRODUCT_DEFECT，2 行 IMPLEMENTED_VALIDATION_PENDING。行数不等于独立缺陷数，同一根因可影响多行。

## 5 八项设计判断

| 判断 | 裁定 | G2 与 G3 的影响 |
|---|---|---|
| 1 held 等于 qty_open | 接受“数量不明先不卖”的门槛；拒绝“最多延迟一次 30 秒”的结论，R4 能永久停滞。最终 SELL 边界也应复核完整数量与在途预留。 | 不阻塞只读 G2；R4 阻塞 G3。 |
| 2 撤单确认后不会再有旧成交 | 推翻“确认取消及查询顺序就证明后续绝无旧成交报告”的推论。Cancelled 确认的是剩余量取消；回报到达时间与交易发生时间不同，两个查询不是有文档保证的原子快照。 | G2 检查可见性与顺序；G3 前必须注入晚于重试收到旧 execution、滞后快照及更正。当前不能关闭防超卖证明。 |
| 3 UNKNOWN 只可人工 cancelled | 保守方向接受；现实现与恢复流程不接受。shutdown 绕过裁定，adopt/ignore 无法修复已有 managed 数量差异。 | 不阻塞只读 G2；R2 阻塞 G3。 |
| 4 reqAllOpenOrders 范围 | 全账户快照加账户/前缀筛选合理；是一次性查询，不是订阅。可见订单不等于当前 client 有权撤销；孤立服务器订单不能忽略。 | 真实 API/TWS/不同 client 范围留 G2；R1 阻塞 G3。 |
| 5 上一收盘日损口径 | 口径方向接受；实现推翻。须有上一收盘持仓归属和数量，不能直接用当前剩余持仓或未知持有日的 adopt。风险数值保持。 | 不阻塞只读 G2；R6/R8 阻塞 G3。 |
| 6 告警后台线程 | 接受。结果字典/序号受锁保护，worker 不触碰 broker 或 SQLite；100 条压力投递通过。投递生命周期和健康缓存仍需修。 | 线程方案本身不阻塞；R9/R10 仍按第二批验收。 |
| 7 dry-run 独立状态和 client 加 1000 | 接受独立目录、只读适配器、正式成交不入演练账的边界。加 1000 是分配约定，不能保证与其他客户端绝无冲突；角色绑定不能替代 Gateway Read-Only 配置。 | L1 离线通过；G2 记录正式/演练 client ID 并验证隔离，演练不得下单/撤单。 |
| 8 配置严格化 | 方向接受；两个示例通过且 paper 指纹不变。自定义配置需重载；SMTP 端口严格化未完成。 | 有效示例不受阻；自定义配置适配及 P2 端口校验待办。 |

判断 2 的券商事实依据为 [IBKR 订单状态定义](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/order-status/understanding-order-status-message)；“这不足以证明跨查询原子一致性”是复核推论，没有声称已经在真实 Gateway 复现取消确认后的新成交。判断 4 依据 [所有当前订单的一次性查询](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/requesting-currently-active-orders/all-submitted-orders) 和 [原 client 才能撤销个别订单](https://www.interactivebrokers.com/docs/tws-api/doc/orders/cancelling-an-order/cancel-individual-order)。跨日查询限制见 [当日 execution 查询](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/execution-details/request-execution-details)。

金额加 $2 不是报告第 3 节独立编号，但也已复核：名义加费用的门槛方向合理，足额性须用实际费率与股数证明；不关闭 R5 的在途资金或陈旧快照问题。

## 6 开发顺序与阶段裁定

1. **第一批收尾与 G2：** L1 及 Windows Lite OS 锁离线通过；补 R11 失败关闭。确认旧 main/auto runner 停用且不会自动恢复，使用独立 Paper dry-run 状态与只读会话，由操作者启动 G2。代码层面没有发现必须先修第二批才能做严格只读观察的缺陷；真实 G2 行为尚未验证。
2. **第二批优先修订单证据与恢复：** R1–R4、R12，以及撤单/迟到成交竞态。先保证孤立订单、跨日证据、迟到分批及更正不被错误裁定或重复入账，再验收部分 ENTRY/TP 到 STOP 的数量协调。
3. **第二批修资金与日损：** R5–R8。资金预留与更新批次协调，冻结或重建上一收盘数量，待费用结果不永久按毛利结算，并在最终 ENTRY 使用最新风险。
4. **第二批修保护循环与健康：** R9–R10。批准每 tick 与最终发送复核已经通过；健康在发送边界复核，所有慢请求按保护期限预算。新增回归应检验真实拒单和检查间隔，不能只检验函数调用次数。
5. **Live 前 L7：** 继续开发计划已选的 GTC Stop-Limit + OCA 数量协调方案。原报告“待 John 选择”与最新计划不一致，方案已选，实现与真实 Paper 验收尚未完成。L7 与上述恢复/数量模型共用证据，不先扩大未修复的 SELL 路径。
6. **并行 L6/P2：** 日报重启重试、失败消息重发、查询身份、SMTP 配置、剩余时区日历和数据源比较。工具不完成时按原计划提供人工核验材料，不延后 G2/G3；关键风控证据仍必须正确。

估算为代码审查后的粗估：第一批收尾约 0.5–1 个开发日；第二批剩余修复、回归和独立复核建议预留 6–10 个开发日，替代把原 4–7 日视为剩余工作承诺；L7 保留 3–5 个开发日初估，随数量模型修复结果调整。真实 broker 验证等待另计，G2 预计 3–5 个交易日，G3 仍需 10 个交易日。修订单/会计后重新收集受影响证据，不以赶固定上线日期压缩验收。

**本次裁定：** Windows 离线测试完成；G2 可进入限定只读验证但未通过验收；G3 为 NO-GO；Live 为 NO-GO。交付文件 SHA-256 清单仍有效，后续代码修复必须另出新清单及“发现 → 改动 → 测试”证据。
