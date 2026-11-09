# V6-Lite G3 Paper 运行手册草案（第 5 版，第二十二批 STP 与 PowerShell 入口）

本手册供 John 在相关批次经 Claude 复核、运行仓库 C:\Dev\V6Lite 完成获准迁移后，亲自进行真实 Paper 演练。C:\Dev\V6Lite-dev 仅用于开发，不作为 G3 启动位置。G3-T 与 G3-N 都改连同一台 TWS Paper：127.0.0.1:7497；人工核对、撤单和紧急处置在这个 TWS 会话中进行，不再与 IB Gateway 并用。本文不是 Live 放行记录；FakeBroker 测试不能证明 IBKR 的 GTC 存续、部分成交、改单确认或执行查询范围。每天开场记录 code_sha、配置指纹和每次服务器 currentTime 与本机接收时间的差值；不写 token、webhook 或完整账户信息，同一天不换代码。

## 0. 启动前的放行条件与 TWS 设置

第二十二批只在 dev 实施，等待 Claude 复核及明确合并授权。本文不声称运行仓库已部署此批；开场以运行仓库实际 HEAD、v6lite-run-* 附注 tag、code_sha 与获准报告逐项核对。旧第十七批的部署、全量测试和根配置核对属于历史证据，不替代本批验证。本轮未读取运行仓库、John 配置或真实状态，未启动 G3。

John 选择方案 B：只把保护单改为 SELL STP/GTC/outsideRth=False，不设 lmtPrice；入场和止盈仍 LMT。保护成交没有价格下限，跳空或复牌可能产生超过预算的滑点。明确拒单发 P0、冻结入场，不自动用其他参数重挂。旧 STP LMT 记录发 P0“旧类型保护单，需人工处理”，不改认、改价、撤单或自动迁移为 STP。G3-T 旧证据的归档由 John 完成，Codex 不触碰用户状态目录。

### 0.1 只使用一个 TWS Paper 会话

1. John 登录 TWS 的 Paper Trading 会话，确认显示 DU Paper 账户；引擎和人工都使用这一个 TWS。不要同时启动或登录同一 Paper 用户名的 IB Gateway、另一台 TWS、手机 App 或网页端；运行期间不得在其他地方登录 Paper 用户名。
2. 在 TWS `Global Configuration → API → Settings` 勾选 `Enable ActiveX and Socket Clients`，将 `Socket Port` 设置为 **7497**。限制本机连接；界面有 `Allow connections from localhost only` 时保持勾选，否则在 `Trusted IP Addresses` 中确认本机 `127.0.0.1`。保存设置，再由 John 核对 API 端口。TWS Paper 默认端口是 7497，API 客户端端口必须与设置一致。[IBKR 的 TWS API 设置说明](https://www.interactivebrokers.com/campus/trading-lessons/installing-configuring-tws-for-the-api/)、[TWS API 设置页](https://www.ibkrguides.com/traderworkstation/api.htm)
3. 只读演练时保持 TWS 的 `Read-Only API` 勾选，并使用 `--dry-run`；程序还会强制 `read_only=True`，实际 client ID 为配置值加 1000（G3-T 1132、G3-N 1131），所有 place/modify/cancel 写边界拒绝执行。**只有正式 G3 Paper 交易获准并完成账户/端口复核后**，John 才取消该 TWS 的 `Read-Only API` 勾选并保存，使 BUY、DAY TP 和 GTC 保护单可提交。该开关会阻止 API 订单，不能把软件 dry-run 边界当作取消开关的理由。[IBKR 的只读 API 说明](https://www.interactivebrokers.com/campus/trading-lessons/installing-configuring-tws-for-the-api/)
4. 交易时段（本项目 06:00–13:30 PT）不得在手机 App、TWS 或网页端登录真实账户用户名，避免与 Paper 的实时行情共享会话竞争。此规则也适用于“只想看持仓”的登录；需要看交易时直接查看当前 Paper TWS 和 Discord。IBKR 对 10197 的说明是 Paper 与 Live 竞争实时行情会话，Live 优先；同一用户名也只允许一个活跃 brokerage session。[10197 官方说明](https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes)、[IBKR 会话说明](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/)

10/5 证据由 John／Claude 提供，本轮未读取或复核原始日志：第十三批实时 dry-run（code_sha=d4b19f1d43b25448）07:00／10:30 PT 两次扫描均 `WOULD_BUY META data=live gate=ok`，告警 48/48 送达；08:00 另开 Paper TWS 挤下 Gateway，出现 1100 与约 30 分钟中断；07:57、09:17 出现 10197，10 个标的断行情。上述通过结论针对该 dry-run，不是第十六／十七批或 G3 真单通过记录。

### 0.2 7497 的离线边界核对与配置变更

John 只修改仓库外的 G3 专用配置 `[ibkr].port` 为 7497，并确认 TWS 设置相同；不把测试模式配置写入正式 Paper 状态目录。本批 G3-T/G3-N 示例为 7497；其他 Paper 示例复制后由 John 核对端口。G3-T 仍要求 DU Paper、client_id=132、独立 `paper-test` 状态目录；G3-N 仍为 DU Paper、client_id=131、测试模式关闭。端口改变会改变配置指纹，开场记录新指纹；不改账户、client ID 或旧状态身份来消除拒绝。既有状态若被身份、schema 或工作单检查拒绝，停下交复核，不删除／重建数据库。

第十七批用临时合成配置、FakeBroker 和完全替代网络的 API stub 做 28 项离线检查，均通过（以下为已复核的第十七批证据）：4002→7497 的唯一区别是端口与指纹；131／132 规则、DU 校验、错账户拒绝和错误 Live 端口拒绝不变；formal／dry-run 的 host、port、account、client ID 与 readonly 参数准确传递；两种 dry-run 的 DAY、GTC、改单及撤单写边界均在物理调用前拒绝。7497 已包含在 `config.py` 的 PAPER_PORTS，CLI 与 broker adapter 不按 4002 特判，因此端口迁移无需修改生产代码。上述第十七批离线端口验证没有连接 TWS／Gateway，也没有读取或修改两份根目录运行配置、真实状态或 John 的 dryrun 目录；真实 TWS 的账户可见性、权限、行情和保护证据仍由 John 实测。

### 0.3 本轮新增健康验收

- 心跳：重连后一次旧 currentTime 回报只记日志及实际服务器／本机差值，不发 P1；每次回报重新判定，连续 3 次异常才发一次独立的“Gateway 时钟偏差”，连续 3 次正常后自动清除并发恢复。同一偏差问题不每 5 分钟重复，不伪装为“IB Gateway not responding”。真正无回报仍按原无响应门槛告警。标题中的 Gateway 是现有健康通道名称，在 G3 也适用于 TWS API 端点。
- John 提供的本机校时差 +0.59～0.61 秒、11:00–11:40 无 Kernel-General 事件 1，仅作为异常回报背景；不能凭单次异常样本断言本机时钟错误。验收须包含 2103／2105 重连后一次旧时间、随后正常，以及持续偏差／完全无回报三个场景。
- ACCOUNT_STREAM：本次仅在最近 300 秒内的券商 STK 股票持仓快照为空、reconcile_ok=true、本地无 OPEN 持仓和 active 意图、且没有新的成交使快照失效时，将正常账户流静默的重复日志降为 DEBUG。此证据只证明所核对的股票持仓为空，不证明账户全部资产为空；有持仓、未知、快照过期或其他条件不满足时，保留 WARN 与原处置。第十九批已将可核实空仓的资金更新改为非重叠账户资金快照，不做退订再订阅；有持仓或未知时保持原流重订阅。270 秒资金新鲜度门控不变，账户资金缺失、过期或无法核实时仍阻止入场，也不把未知持仓当作空仓。每日交付材料由 John 附股票持仓证据时间和静默处理记录（不属于自动日报字段）。不得通过关闭持仓保护查询掩盖告警。

## 1. 测试模式启动和停机

1. 确认旧 V6、其他 V6-Lite 实例及自动启动任务均停止；TWS 中托管标的无外部 SELL。由 John 核对手机 P0/P1、实时行情、DU Paper 账户及 TWS 端口 7497，落实第 0 节的单会话登录规则。仅在第 0 节放行条件满足后，由 John 在当前 Paper TWS 的 API 设置中取消 Read-Only API 勾选并保存，复核账户/端口后才允许 G3 Paper 写单。Live API 在单独放行前保持只读。
2. G3-T 在仓库外复制 v6lite/examples/paper_test.toml 成专用配置：独立状态目录 C:\Users\yu-ch\.v6lite\paper-test\、port=7497、client_id=132、test.enabled=true、qty=3、四倍数 0.3/0.3/0.5/0.8。检查 protection.enabled=true、order_type="STP"、删除 limit_offset_pct；A/B 时限、trigger_unfilled_p0_seconds=60、steady_check_seconds=15、evidence_fail_p0_seconds=60。G3-N 用独立 Paper 正式目录、port=7497、client_id=131、test.enabled=false；两段状态目录不得互认。
3. 第二十二批经 Claude 复核通过、获准合并到 main 并完成运行仓库校验后，由 John 从运行仓库 `C:\Dev\V6Lite` 激活其独立 venv，启动 `python -m v6lite run --config <仓库外的测试配置>`。手机启动卡片核对 `TEST`、Paper 模式及正确 Paper 账户；交给 Claude 的复核材料使用脱敏账户标识；已有托管持仓的 GTC 状态以 TWS 工作单及“券商端保护”健康栏核对。本地 `STARTUP` 审计、日志及 TWS 状态逐项核对。任何异常先停止新增风险，保留日志和订单截图。
4. 在引擎已显示测试入场就绪、实时行情和门控健康、入场窗口开放时，由 John 在另一 PowerShell 执行 `python -m v6lite test-entry <标的> --config <同一测试配置> --operator John`。一次只给一个标的、一次请求；确认实际 BUY 只有 3 股，测试倍数生效。记录命令时间、请求结果、订单 ID、每笔成交、保护单及 TP 的 `orderRef/permId`。
5. 原生 Python 正常停机用引擎窗口 `Ctrl+C` 一次；用本批直接 PowerShell 入口须先完成第11.1节人工停机验收，不再用 v6.cmd 启动引擎，不把批处理返回提示符当作 Python 已完成停机。停机后核对 DAY ENTRY/TP 的撤单与迟到成交，**已确认且数量正确的 GTC STP 应保留**；不以进程退出或本地 `PendingCancel` 作为服务器确认。重启先核对它认领原 `orderRef+permId`，没有重复保护单。若状态不明，在 TWS 核对并由 John 判断，勿重复启动或手工加卖单。

## 2. 每笔订单的账和两处空窗

每个事件留同一时基的时间戳（至少毫秒）、账户脱敏标识、symbol/conId、`orderRef/orderId/permId/clientId`、broker 状态、持仓 `P`、保护可成交剩余量 `S`、全部自有 TP 剩余量 `T`、外部/未知卖单剩余量 `X`、未保护股数 `U=max(0,P-S-T-X)`。截取 TWS 工作单、持仓、执行记录以及引擎 audit/`[PROTECT_GAP]` 日志；字段缺失应写“未知”，不可填 0。每一步核对毛量 `S+T+X≤P`；外部单造成的瞬间违例也如实记入。

| 空窗 | 开始与结束 | 必须核对的自动处理 | G3 记录 |
|---|---|---|---|
| A | 一批 BUY 的 `execDetails` 成交时刻 → 该批实际持仓被券商确认纳入 GTC 保护单的时刻；部分成交每批单独计时 | 同一事件处理流程优先发保护，未确认前阻断新入场；5 秒 P1、15 秒 P0；超时/拒单保持未知并报实际未保护股数 | 每批 `A_ms`、`[PROTECT_GAP] kind=A secs=…`、告警发出/手机收到时间、崩溃/重启结果 |
| B | 保护单减量获服务器确认的时刻 → 对应 TP 完全成交；若超时撤单，则到券商确认剩余实际持仓已恢复完整保护为止 | 确认减量后立即发可立即成交的 bid×(1−1%) DAY 限价单；10 秒仍未全成则撤剩余，核实撤单及成交后按实际持仓恢复保护；30 秒仍未恢复发 P0 | 每笔 `B_ms`、TP 提交/部分成交/撤单、恢复确认、`[PROTECT_GAP] kind=B secs=…` 及手机告警 |

PROTECT_GAP 记录本地 execDetails 接收至券商证据完成的 A/B 时长；PROTECT_DISPATCH 记录回调到保护/TP 实际提交的延迟，单调时钟辅助排查进程调度。跨重启或回补成交必须以 TWS/券商事件时间、执行记录及系统日志交叉核对；recovered_unverified 或来源冲突标为“未证实”，不可用短本地窗口放行。日报逐持仓列保护存在性、数量、止损价、每笔成交价/均价/美元及 bps 滑点和 A/B 完整最大时长，另列正在进行及跨日窗口。缺失起点/终点或跨日执行范围时写“未知”，不得填 0。实盘放行要求全部已测 A/B 最长空窗 ≤5 秒、没有未处理崩溃窗口，且关键场景全部留证；Paper 达标仍须首笔 Live 复核。

IBKR 的 [订单修改说明](https://interactivebrokers.github.io/tws-api/modifying_orders.html)要求使用原订单 ID 与同一 TWS 用户/client ID；[开放订单说明](https://interactivebrokers.github.io/tws-api/open_orders.html)说明 `reqAllOpenOrders` 是一次性快照、不会绑定后续更新；[订单提交说明](https://interactivebrokers.github.io/tws-api/order_submission.html)提示 `orderStatus` 可能重复且部分变化没有回调。因此本地 `placeOrder` 返回或单条状态回调都不算确认，须连同新鲜工作单、持仓和执行证据核对。

## 3. 八项实测清单：止损触发后的成交与滑点记录

| 场景 | 操作与通过证据 |
|---|---|
| 强退与重启 | 在小额 Paper 持仓及 GTC 确认后强退一次，并专门观察部分 BUY→保护确认窗口；TWS 仍显示原 `permId/orderRef`、数量和价位，重启认领且不重复挂单。若无法复现短窗，明确记为未测。 |
| 本机断网 | John 监督下断开并恢复网络；对照 TWS/券商端 GTC 与实际成交、恢复后的执行回补。不明即 P0，不因本地工作单缓存为空而补挂。 |
| TWS Paper 重启 | 保留 GTC 身份/价量和新会话认领证据；分别记录同 client 修改是否成功、其他 client 是否只能查询。 |
| TP 部分成交及撤单竞态 | 逐步记录先减 S、确认、发 T、部分成交、10 秒超时撤单、撤单后的迟到成交和保护恢复；每步记录 `P,S,T,X` 及订单状态，不以 `PendingCancel` 当确认。 |
| 止损触发后的成交与滑点记录 | 至少两次实际 STP 成交：逐 execId 记录止损价、成交价、累计成交均价、Q×(止损价−成交价) USD 和 (止损价−成交价)/止损价×10000 bps；改善成交为负滑点。确认日报与 PROTECT_FILL 一致。超过配置时限仍有剩余才发 P0，部分成交不重置首次穿越的计时；跨交易日重新计时，不把隔夜计入 RTH 超时。低价本身不立刻 P0；MKT、自动追价、拒单后重挂及同一时刻第二张仍可成交的 STOP 均为 0。未能重现跳空/停牌记未测，价格没有下限。 |
| 盘后至次日 | 收盘后、次日开盘前后核对 `outsideRth=False` 的 GTC 是否仍在、价量是否因公司行动变化、盘外并不保证止损成交。 |
| 入场部分成交及 bracket 附加实验 | 记录每笔 BUY 部分成交与 A 空窗。另按第 4 节单独测 parent/child bracket 的子单激活时间、数量和强退后状态；该附加实验不阻塞 v1。 |
| GTC 取消、改单拒绝、外部卖单 | 分别验证 P0、未保护股数、只减不增、不重复挂单。外部卖单产生的瞬时超卖必须如实记入；不得把事后减量写成从未超卖。 |

另核对 `reqExecutions` 在当日及跨日、同 client 与其他 client 的覆盖范围；拿不到足够的跨日成交证据时，保护只减不增，不自动补挂。确认低于 1 美元／缺 ATR 的入场保护方案不成立时 BUY 被拦截并发 P1；已有持仓的保护价格异常、保护未确认或未保护股数不明仍发 P0；确认止损价以 0.01 tick 向下取整、`STP/GTC/outsideRth=False`、不设 lmtPrice、client 132 与测试状态绑定，正式 131 不会互认。每类 P0/P1 卡片应保留标的、数量、价位、订单号与编号步骤。

对已部分成交的保护单，单独实测修改 `totalQuantity` 后券商回报的**剩余可卖量**与累计 executions；不从改单请求量自行推断剩余量。若 TWS、工作单和执行记录不能闭合，停新增卖量、记为 UNKNOWN 并报 P0。

三股测试模式的末股 TP 必须单独留证：最后一股不能把 GTC 修改为零；先确认旧 GTC 真正撤销，再发末股 DAY TP。末股 TP 若被拒、10 秒未全成或进程在两步之间崩溃，核对迟到成交、实际剩余持仓以及新 GTC 的 `orderRef/permId`；只有旧单已被证实不再可成交、三证据稳定时才补挂，新旧两张保护单不可并存。B 空窗延续到末股成交或剩余持仓重新获券商确认保护。

## 4. Bracket 附加实验

只在独立、无 V6-Lite 托管持仓且无工作 SELL 的 Paper 标的上，由 John 在 TWS 做 parent BUY + child STP SELL 附加实验（需单独获准）；不要让实验单与 V6-Lite 的自动卖单同时存在。记录 parent/child 的 `orderId/parentId/permId/transmit`、每次部分 BUY 成交后的 child 激活时点、child 数量是否只覆盖实际持股、未满额时是否会多卖、进程强退及 TWS Paper 重启后的服务器端状态。实验结束撤/平之前先核实两张订单和所有成交；未经 Paper 证据及另一次设计裁决，不把 bracket 接入 v1 或当作空窗 A 已消除。

## 5. 实盘按标的独占卖出规则

V6-Lite 管理某标的持仓期间，任何其他程序或人工都不得对**该标的**下卖单。紧急人工卖出时，先在 TWS 确认撤掉或减掉 V6-Lite 该标的卖单，再由 John 自行下单；操作后立即重新核对持仓、在途单和成交。不受 V6-Lite 管理的标的（如受保护的 MSTR）仍可人工交易。发现托管标的的外部卖单，冻结该标的与新入场、按可核验证据只减本系统保护量并发 P0；客户端事后检测不能保证外部并发发生前的不变式。

每日日志、TWS 截图及订单/执行导出均保存在仓库外的 G3 证据目录。不要提交运行配置、账户明文、token、webhook 或 SQLite 状态；复核材料只给脱敏摘要、`code_sha`、配置指纹和证据文件哈希。

## 6. G3-T 与 G3-N 分段操作

G3-T 固定 Paper 测试配置、client 132 和状态目录 C:\Users\yu-ch\.v6lite\paper-test\；仅在第二十二批经 Claude 复核通过、获准合并到 main 并完成运行仓库校验后，由 John 从运行仓库 C:\Dev\V6Lite 的独立 venv 启动。G3-T 停机、核实 DAY 单和 GTC、归档证据后，才进入 G3-N：Paper 正式配置、client 131、独立状态目录、测试模式关闭。同一 Paper 账户绝不同时运行两段；两端会把对方的 SELL 视为外部单并冻结。切换前在 TWS 核对全部持仓、工作单、成交和原 client 身份，不以关闭进程作为撤单确认。进入 G3-N 前还须核对第十七批最终报告中五项保护开启等价回归及 L3 五种补测全部通过。

顺序是：第 0 节前置全部通过后，John 核对当前 TWS Paper 的 127.0.0.1:7497、DU 账户与 Read-Only API 已取消勾选，启动一个进程，确认手机启动通知。人工核对和紧急处置在同一个 TWS 中完成，不另开 Gateway 或第二个登录。结束时停机、保存券商证据并重新勾选当前 Paper TWS 的 Read-Only API，复核设置生效。Live API 始终保持 Read-Only API 勾选，直到单独的 Live 放行决定。Codex 的离线测试和本文均不连接 TWS／Gateway。John 自行归档 %USERPROFILE%\.v6lite\paper\dryrun；本轮 Codex 不读取、写入、枚举、改名或归档整个 %USERPROFILE%\.v6lite 目录及其内容。

## 7. 每日通过标准与交给 Claude 的材料

从 C:\Users\yu-ch\.v6lite\paper-test\ 的日志、SQLite 状态和报告提取，并与 TWS 脱敏持仓/工作单/执行导出交叉核对；G3-N 改用其独立正式目录。每天提交以下证据：

1. 实际 code_sha、配置指纹、运行时段、TWS Paper 账户脱敏标识、端口/client ID、Read-Only API 状态与时钟差；不要附配置原文和机密。
2. 每笔 PROTECT_GAP kind=A/B 的完整时长、execId/订单身份、是否 recovered_unverified、最大值和所有未闭合/跨日窗口；每笔 PROTECT_DISPATCH 的回调到保护/TP 提交延迟及最大值；全部 SLOW_TICK 的时长和阶段。日志缺行或与 TWS 矛盾则记“未证实”。
3. 当日每条 P0/P1 的标题、时间、symbol、orderRef/orderId/permId、P/S/T/X/U、手机接收时间、John 动作和关闭证据；告警沉默视为未通过。
4. 每个托管标的的持仓/全部 SELL/执行三源快照，逐状态检查 S+T+X≤P，保护单仍为 STP/GTC/outsideRth=False 且无 lmtPrice，没有 MKT 或第二张自动 STOP。
5. 逐日结论写“通过 / 未通过 / 未证实”。仅全部必测场景留证、A/B 完整最大时长 ≤5 秒、无未结 P0/UNKNOWN/崩溃窗口、手机告警健康才可记“通过”。不能只见日报 0.000s 就推断没有空窗。

G3-T 最少完成三轮完整 3 股入场→TP1→TP2→TP3、两次止损成交、一次 B 超时撤 TP 并恢复、一次强退重启认领。次数覆盖三段退出、STP 成交/滑点与未成交超时、恢复竞态；未能重现的项记“未测”，不以其他场景替代。

## 8. 一次性 B 超时 Paper 测试（第二十九批，复核与合并后使用）

仅用于 G3-T：Paper、test.enabled=true、client 132、非 dry-run。先确认引擎就绪、该标的有 Lite 持仓、同一张 STP 已确认覆盖全部持仓、当前没有进行中的止盈阶段。John 在另一交互 PowerShell 执行：

    ~\v6 tptimeout AAPL

入口固定选择 G3-T 配置，复用 entry 的 Git/tag、配置、标的、交互和 webhook 校验，必须输入完整大写 `YES`。其他模式及额外参数不受理。等价的原生命令为：

    python -m v6lite test-tp-timeout AAPL --operator John --config <仓库外的 G3-T 配置>

原生命令只排队请求，不连接券商。标的必须在配置中、operator 非空、运行引擎的就绪 epoch 有效；没有持仓、STP 未确认或已有止盈进行中均拒绝并说明原因。同一标的只允许一个待用请求，有效期五分钟；过期、重启、断线、跨交易日作废。不要为同一标的重复执行命令。

下一张通过全部正常门控的非最后一档 TP，在同一张 GTC 减量获得券商确认后、实际发单前原子领取请求。仅这张 DAY/LMT 的限价取新鲜 ask × 1.01，向上取到分，数量不变；领取先持久化，崩溃或重放不能再次触发。最后一档需要撤掉 STP，禁止注入；即使最后一股仍是 TP1 也拒绝领取并作废请求、写审计，正常止盈继续。无请求的 TP 和注入恢复后的重试均沿用三秒内新鲜 bid × (1 − 0.2%)，不能绕过身份、报价、费用、暂停或 S+T+X≤P 门控，不能生成 MKT 或额外卖单。

John 留证：减量确认 → ask/测试限价/TP orderId → B 起点累计 10 秒撤 TP → 券商 Cancelled 及全部实际成交 → 同一 GTC 恢复实际剩余持仓并获确认 → B 关闭。预期 INFO 标题为“测试：止盈超时注入已触发，正在撤单并恢复保护”；该测试超时本身不发 P1，真正的撤单/恢复失败仍按原规则发 P0，累计超过 30 秒未恢复也发 P0。测试价格可能随后被市场触及；撤单请求后的迟到成交按实际入账，保护只恢复到成交后的剩余持仓，不重复卖出。

零成交且保护恢复后，这一档仍未完成；稍后价格再次满足条件，应按正常 bid 定价重试这一档。若已真实成交，则以账本和实际剩余持仓为准。保存 `TEST_TP_TIMEOUT_RESULT` 中请求/领取、ask 与限价/orderId、撤单请求/确认、恢复确认/恢复后的 S、完整空窗 B 时长，并与日报“测试止盈超时”专节和 TWS 脱敏证据交叉核对。本场景有意触发 B10，不把它伪称正常止盈 ≤5 秒；实测结果单独交 Claude 复核。

## 9. 紧急处置卡

收到 P0/P1，John 先按卡片 orderRef/orderId/permId 在 TWS 核对持仓 P、保护 S、在途 TP T、外部 SELL X 和成交。止盈单确定未发出且正在自动恢复、迟到 TP 成交后保护减量已获券商确认、缺 ATR 导致 BUY 被拦截均为 P1；减量待确认／身份不明仍是 P0，保护恢复超过原 30 秒门槛仍升 P0。P1 不代表可跳过核对或继续重复下单。A 超时/拒单/证据不全：停止新入场，核对裸仓，勿盲目补挂。B 超时：先核实 TP 撤单和迟到成交，再核实保护恢复；接管前若 Lite 五分钟仍未恢复，John 在 TWS 手动撤 TP 并记录，不另下 SELL。外部 SELL：查来源，不需要的外部单撤掉；保留时 Lite 冻结该标的，持仓未平不能用 release 解除冻结。止损价格穿越：观察原 GTC 的成交和剩余；常规交易时段从首次穿越起 60 秒仍有剩余时发 P0，可能停牌/交易暂停；在 TWS 核对原单和所有成交，不自动重挂、不另下第二张卖单，由 John 联系券商处置。UNKNOWN/停机未核实：只减不增，保持 GTC 直至三源证据闭合。

人工全部平仓的顺序：先在 TWS 撤销并确认 Lite 该标的所有 SELL（GTC 和 DAY TP）及迟到成交 → John 自行卖出实际剩余持仓 → 三源确认持仓 0 且无工作单 → 执行 python -m v6lite release SYMBOL --operator John --reason <原因> --config <同一配置>。release 另以只读账户级执行查询核对 John 的跨 client 手工 SELL，两次账户执行集合必须稳定且包含本 client 执行；该查询不把外部成交写进 Lite 账本。G3 必须用 TWS 实际验证账户级查询对另一用户/client 的执行可见性及历史范围；若不可见或证据不完整，release 安全拒绝，停止后续自动交易并交 Claude 复核。任一步不明则暂停下单、记录 P0，不把本地 PendingCancel 当券商确认。

## 10. Paper 与首笔 Live 的边界

Paper 的撮合、部分成交、成交速度与 GTC 跨 TWS 重启／交易所行为可能不同于 Live。G3 通过只证明已观察的 Paper 行为；首次 Live 交易前仍须独立放行，首笔 Live 成交后重新测量 A/B 空窗。任何未证实回补、跨日或迟到成交均不得按 ≤5 秒通过。

实盘前待办：为手机监控另开独立用户名，由 John 与 IBKR 核实账户权限、只读权限、行情订阅／共享限制及会话并存后，再做不会抢占引擎会话的验证；该项尚未完成。G3 不以现有真实用户名登录手机来代替此项。


## 11. Windows PowerShell 入口（第二十一、二十二批，复核后再部署）

John 合并后在用户目录新建 `v6.ps1`，唯一一行：

```powershell
& 'C:\Dev\V6Lite\scripts\v6.ps1' @args
```

本批只提供 `scripts/v6-user.ps1` 的同字节模板，不安装用户入口、不改 profile、运行配置或运行仓库。不要用旧 `v6.cmd` 启动引擎，避免 Ctrl+C 的 Terminate batch job (Y/N)。从交互 PowerShell 直接调用上述 .ps1，或直接调用 `C:\Dev\V6Lite\scripts\v6.ps1`。

| 调用（交互 PowerShell） | 行为 |
|---|---|
| `& .\v6.ps1 dry` | Paper 配置，演练不下单 |
| `& .\v6.ps1 g3t-dry` / `g3n-dry` | 相应配置，演练不下单，7497 |
| `& .\v6.ps1 g3t` / `g3n` | 相应配置真实 Paper 下单，7497，完整 YES 才继续 |
| `& .\v6.ps1 entry AAPL` | 只用 G3-T test-entry --operator John |
| `& .\v6.ps1 status g3t` | 对应状态只读 status |
| `& .\v6.ps1 g3t -WhatIf` | 仅执行计划，无启动、状态或网络连接 |

启动检查 main、HEAD 附注 v6lite-run-* tag、包内无已/未暂存及未跟踪改动、tag/current 包指纹一致；打印 HEAD/tag/code_sha。固定仓库 .venv Python、根工作目录，从 User 环境载入 webhook，只报存在与长度。非 dry 必须 YES。非 Capture 引擎分支改为共享控制台 Diagnostics.Process，正确引用 Windows 参数、等待 Python 退出，finally 再等其退出；不再经 Out-Host 管道。PYTHONDONTWRITEBYTECODE/PYTHONUNBUFFERED/密钥环境退出后恢复。Capture/status 与订单门控保持原路径。

John 自己在 `v6lite.g3t.toml` 与 `v6lite.paper.toml` 的 `[protection]` 删除 `limit_offset_pct = <原值>`；可显式新增 `order_type = "STP"`，省略时默认 STP。本批未读取这两份运行配置，不声称实际原值。出现 limit_offset_pct 就明确 CONFIG ERROR，即使 protection.enabled=false 也不忽略。G3 必须另核对 protection.enabled=true。修改会改变配置指纹，由 John 记录；不要修改状态身份来消除拒绝。

## 11.1 合并后第一天开盘前的 Ctrl+C 人工验收

第二十一批已在 owned 隐藏控制台/TEMP FakeBroker 合成链路验证 PowerShell 5.1/7：单次 CTRL_C_EVENT 后 run_engine 完成正常 Engine.shutdown、审计/资源关闭、Python 退出之后 PowerShell 才返回。它不是物理键盘、John 窗口或真实券商验收；详情见 `docs/V6_LITE_BATCH21_POWERSHELL_ENTRY_2026-10-08.md`。

1. Claude 复核、获准合并部署后，John 在交互 PowerShell 用直接 .ps1 入口启动已获准 dry-run；保持 TWS Read-Only API 与所有原安全条件。记录启动 HEAD/tag/code_sha/PID 与通知中的“演练（不下单）”。本批 Codex 不执行此真实步骤。
2. 就绪后在同一窗口按 Ctrl+C 一次。不得出现 Terminate batch job (Y/N)，不得关窗口、二次中断或强杀。等待正常停机；PowerShell 提示符不能先于 Python 正常退出。如果包装器提前返回、提示 Y/N、资源仍占用或日志缺失，验收失败并停止 G3。
3. 正常停机的权威判定是本次 SQLite audit kind=`SHUTDOWN`（只读 mode=ro 核对），并联合随后 Python PID 已结束；当前生产不保证有一行 console `[SHUTDOWN]`，不要用不存在的日志作门槛、状态/账户锁正常释放。日志/审计须属于本次会话，不能用历史记录；单看 wal/shm 文件存在与否不能判定停机是否完成。dry-run place/modify/cancel 都必须为 0。
4. 将来获准 Paper 实单正常停机还须在同一 TWS 人工核对 DAY ENTRY/TP 已完成撤单及迟到成交，已确认且数量正确的 GTC STP 保留；本地撤单请求或 Python 退出不等于券商确认。保存控制台、日志、审计和脱敏订单证据，交 Claude。最低 G3-T 场景仍须两次止损成交。

旧状态发现 STP LMT：保持拒绝，由 John 人工处置/归档证据；本批不提供历史迁移或强身份恢复。运行时遇到明确拒单：首选在同一 TWS 用限价单平仓、确认零持仓和无在途 SELL及静默期，再执行 release；身份、类型或三项新鲜证据不足时 release 仍拒绝，不手改状态库。
