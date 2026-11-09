# V6-Lite

Trading V6 的精简实盘核心。设计与上线计划见 `docs/V6_LITE_REDESIGN.md`。

## 安装与自检（Windows, PowerShell, 仓库根目录）

```powershell
cd C:\Dev\TradingV6
.\venv_v6\Scripts\Activate.ps1          # 已包含 ib_insync / psutil / pytest
python -m pytest v6lite\tests -q         # 期望：395 passed（含与根目录 market_scanner.py 的策略对比）
Copy-Item v6lite\examples\paper.toml v6lite.paper.toml
```

告警（至少一种；实盘必需）：

```powershell
# 邮件：在 v6lite.paper.toml 的 [alerts] 填 email_to / smtp_host / smtp_user
setx V6LITE_SMTP_PASSWORD "<Gmail 应用专用密码>"
# 或 Discord webhook（频道设置 → 集成 → Webhook）
setx V6LITE_DISCORD_WEBHOOK "https://discord.com/api/webhooks/..."
```

## 日常命令

| 用途 | 命令 |
|---|---|
| 只读演练（不下单、不撤单；独立状态目录 `dryrun\`、client ID +1000，不读写正式状态） | `python -m v6lite run --config v6lite.paper.toml --dry-run` |
| Paper 运行 | `python -m v6lite run --config v6lite.paper.toml` |
| 查看状态（可在运行中另开窗口；`engine_health` 超过 3 分钟未更新显示 STALE） | `python -m v6lite status --config v6lite.paper.toml` |
| 停止新开仓（退出照常） | `python -m v6lite kill --config v6lite.paper.toml --reason "..."` |
| 恢复 | `python -m v6lite resume --config v6lite.paper.toml --operator John --reason "..."` |
| 标记不托管的已有持仓（v1 不接管已有持仓，没有 adopt；`--dry-run` 只改演练状态） | `python -m v6lite ignore NVDA --config v6lite.paper.toml --operator John [--dry-run]` |
| 清除已人工核实的异常（`frozen:*` 不能用它清除，只能 `release`） | `python -m v6lite clear-anomaly <code> --config ... --operator John --reason "..."` |
| 人工裁定无法由 broker 证明的 UNKNOWN / 前一交易日挂起的订单（先在 TWS 交易记录中核实）。只裁定订单状态，不记成交 | `python -m v6lite resolve-intent <intent_id> --as cancelled --config ... --operator John --reason "..."` |
| 释放被冻结的持仓（见下文“冻结与释放”）。先在 TWS 把该标的平掉、撤掉所有相关订单，再执行。引擎只在最新查询显示 broker 持仓为 0（持仓推送也须为 0）、该标的没有任何 working 订单、账本没有在途订单、最近 60 秒没有成交时执行：账本持仓标记为 RELEASED，**当天停止新开仓**，计为一次亏损。若冻结期间原持仓已被迟到成交正常平掉，同一条命令只解除冻结并当天停止新开仓，不再额外计亏损；条件不满足时拒绝并发 P1 告警，可稍后重试 | `python -m v6lite release AAPL --config ... --operator John --reason "..."` |
| 日报 | `python -m v6lite report --config v6lite.paper.toml [--day 2026-10-12]` |
| 实盘批准（最长 30 天，改任何参数即失效） | `python -m v6lite approve-live --config v6lite.live.toml --operator John --days 14` |
| 实盘运行 | `python -m v6lite run --config v6lite.live.toml --live` |
| 撤销实盘批准 | `python -m v6lite revoke-live --config v6lite.live.toml` |

`Ctrl+C` 正常退出：向 broker 查询当前所有 `v6l-` 开头的 working 订单（包括 UNKNOWN 状态和账本里没有的），逐一请求撤单，并对当日订单按证据结算；前一交易日遗留、等待人工裁定的订单保持 UNKNOWN，不会被自动关闭。若仍有无法确认的订单，会发 P1 手机卡片，订单号写在本地日志；此时请到 TWS 人工核实。

broker 上存在账本不认识（或账本已判定结束）的 `v6l-` working 订单时，记为 `orphan_order` 异常并阻断新开仓，不会自动撤单或接管：到 TWS 处理后再 `clear-anomaly`。

控制命令（kill/resume/ignore/clear-anomaly/resolve-intent/release）、只读的 status/report，以及 `run` 本身，都只作用于与配置账户、模式、角色一致的状态文件；不匹配时在打开、迁移或连接 broker 之前就拒绝。启动失败时只断开连接，不会撤任何订单。v1 只在全新状态（schema 4）上运行：遇到更早版本的状态文件一律拒绝，不迁移、不改动文件；把旧状态目录整体归档后重新开始。每个账户同时只能有一个 V6-Lite 交易进程（操作系统级账户锁，位于 `%LOCALAPPDATA%\v6lite\locks\`，进程退出或崩溃时自动释放）。旧 V6 不识别这个锁，运行 V6-Lite 前必须确认旧 V6 的 main/auto runner 都已停止。

## 运行状态放在哪里

`%USERPROFILE%\.v6lite\paper\`（或 `live\`）：`v6lite.sqlite3`（全部状态与审计）、`logs\`、`reports\`（每日日报）、`live_approval.json`；只读演练使用其下的 `dryrun\` 子目录。账户锁在 `%LOCALAPPDATA%\v6lite\locks\`。仓库内不写任何运行时文件。

## 首次在旧 Paper 账户上运行

旧 V6 在 `DU0000000` 留有持仓。启动后 `status` 会显示 `reconcile: XXX unmanaged broker holding`，入场被阻断。v1 不接管已有持仓：逐个执行 `ignore`（保持不动、不管理），或者到 IBKR 账户管理里重置 Paper 账户。`MSTR` 已在 `protected` 名单中，不需要处理。

## 告警与行情状态

- 启动通知在 Discord 显示中文卡片，分为启动时间、模式、状态、监控、扫描时间、手动指令六栏；邮件收到相同内容的纯文本。状态栏包括旧 V6 进程检查结论。卡片列出的 `status`、`report`、`kill --reason`、`resume`、`ignore`、`release`、`resolve-intent`、`clear-anomaly` 是**本机 PowerShell 命令**，不能发到 Discord；`approve-live`、`revoke-live` 仅在 Live 卡片列出。完整状态 JSON 仍留在 `STARTUP` audit 与 `[STARTUP_SUMMARY]` 日志。
- P0/P1 手机卡片固定三栏：发生了什么、影响、你要做什么；英文原始告警和详细诊断仍写本地日志。每日健康卡片按扫描与 gate、下单/撤单、持仓与漂移、Gateway 应答、告警投递、行情可用率、`code_sha` 七栏显示；`reports\` 内仍保存完整 Markdown 日报。摘要中的 Gateway、告警和行情可用率仅覆盖**当前进程的当日、截至报告生成时**，进程重启前的数据不包含在内。
- 交易日从 06:00 PT 到收盘后 30 分钟（正常日 13:30 PT，提前收盘日相应提前），引擎每 60 秒向 Gateway 发一次只读、非阻塞的 `reqCurrentTime`；连续两次超过 30 秒未得到应答时发 P1 `IB Gateway not responding`，恢复后 resolve 并发 INFO。迟到回复须以服务器时间证明近期处理；每次回调记录服务器时间、本机实际接收时间及有符号秒差。单次旧时间回报只记日志；连续异常默认 3 次才发一次独立“Gateway 时钟偏差”P1，连续正常默认 3 次自动解除并发恢复（阈值常量 GATEWAY_CLOCK_SKEW_CONFIRMATIONS）。及时但时间异常的回报不伪装为无响应；真正超时仍按原规则告警，旧回报不能解除已有无响应事件。此监控在 Paper dry-run 下同样运行，不占用持仓或挂单查询通道。
- 同一条告警（标题和内容都相同）持续存在时，最多每 30 分钟重发一次（P0 为 10 分钟）；内容变化时 5 分钟后即可再发；条件消失后再出现会立即重发（断线、持仓差异、无报价、无实时行情）。
- IBKR 拒绝实时行情订阅（10089 等）或只给延迟/冻结行情时：发 P1“实时行情不可用”手机卡片，本地日志保留英文 `no real-time market data`；配置为实时行情时，延迟或冻结的报价一律不当作报价使用（不入场，持仓无法定价并告警）；报价只有在本次订阅开始（以及任何拒绝）之后收到过价格 tick（买价、卖价或成交价，不含成交量）才算有效，判断按消息顺序而不是时间戳。`status`、心跳和日报都有 `market_data` 一项；配置 `market_data_type = 3`（仅 Paper 演练）时日报标为延迟行情。

## 与旧 V6 同机运行的隔离

V6-Lite 的账户锁只由 V6-Lite 识别。运行配置里的 `foreign_lock_paths` 相对**配置文件所在目录**解析；仓库中的 `v6lite/examples/*.toml` 应先复制到仓库根目录，再按运行手册使用。若把样例直接留在 `examples/` 运行，须先按该位置改写外部锁路径。启动时还会只读检查旧 V6 的 `trading_bot_v6_main`、`auto_trading_runner`、`discord_bot_runner` Python 进程：正式运行发现进程或无法核实进程状态时拒绝启动；dry-run 发 P1 并在启动卡片列出运行中的 PID 或“无法核实”。旧 V6 自身不会检查 V6-Lite 的锁，John 仍须停用旧启动入口和计划任务，且每次运行前核实旧 V6 已停止。

外部锁路径从相对改为绝对后，配置指纹会变化；既有 Live 批准因此失效，需在复核后按正式流程重新批准。仓库根目录旧的 `trading_bot.lock` 保留，不要清理；它含旧 PID，未来 PID 被其他 Python 进程复用时仍可能保守拒绝。此时先核实实际进程，再由 John 决定如何处理该旧锁。

## 冻结与释放（v1 没有 resync）

托管持仓的 broker 数量与账本不一致、且无法由成交解释时：

1. 立即阻断全部新开仓，该标的不再发任何退出单；引擎持续刷新成交，寻找迟到的成交。
2. 差异持续 60 秒，并在强制刷新成交、重新查询 broker 持仓后仍然存在，该标的被**冻结**：发 P0“退出保护受阻”手机卡片，同时对 V6-Lite 自己在该标的上的 working 订单发撤单请求。本地 P0 日志保留英文 `V6-Lite no longer manages …` 和仍可能成交的其他订单清单（挂起的、账本不认识的 `v6l-` 单、人工单、组合单）；这些订单**不会**被自动撤销。证据不完整（成交或持仓查询失败）时先延后冻结并发 P1；再过 120 秒仍不完整就直接冻结。
3. 冻结后由操作员在 TWS 处理：先处理本地 P0 日志列出的所有订单，确认该标的没有任何 working 订单，**然后**再平仓或自行保护。
4. broker 显示 0 股、没有 working 订单后执行 `release`。冻结期间新开仓一直被阻断（`frozen_symbols`），`clear-anomaly` 不能解除冻结。
5. release 之后，操作员在 TWS 的交易不进入 V6-Lite 的盈亏；日报会列出 release 记录。release 或冻结解除之后才回报的成交，只要其订单是在那之前创建的（买入或卖出），一律只作为证据隔离记录并告警：不入账、不生成或改变持仓、不进入当日盈亏和连亏。

账户里出现空头（负持仓）时，即使该标的被 `ignore`，也视为差异并阻断新开仓（V6-Lite 只做多，空头可能是超卖造成的）；`protected` 名单中的标的除外。

## 风控口径

- 当日亏损每次都从账本重新计算：上一收盘持有的股数按昨收计价，当天买入按成交价；已报告的佣金加上尚未报告佣金的保守估计。昨收拿不到时阻断新开仓（`daily_loss_baseline_unknown`），退出照常。
- 连亏冷却从**全部**已平仓交易按平仓成交的**执行时间**重新推导（release 计为一次亏损）；佣金未到时按保守估计（IBKR Pro Fixed 规则 ×1.5，每单至少 $2）计入，真实佣金到达后自动更正。
- 任何卖单发出前先重新查询 broker 的 working 订单和持仓（`reqPositions`，不用可能滞后的缓存）：可卖股数 = broker 持仓 − 所有 working SELL 未成交量（包括账本不认识的、挂起的、人工下的，以及组合单 BAG 中的股票腿；无法识别的组合腿会阻断全部卖单和 release，并在它存在期间阻断新开仓）。broker 持仓与账本不一致时不卖。STOP 总是卖出全部剩余持仓，不足时按可卖数下单并告警，止盈不足时不发；持仓推送与最新查询不一致时按较小者计算，STOP 因此被拒时发 P0；查不到最新证据就不卖（告警后重试；按最新报价判断已触发止损时为 P0）。IBKR 的持仓和订单查询使用固定请求键：上一次超时请求的结束消息未到之前，不会发出新查询，查询结果也只在由它自己的结束消息完成时才采用；结束消息超过 60 秒未到或出现多余的结束消息时，主动断开并重连。发单前再用最新报价重新判断一次：止盈区已离开则放弃，期间触发止损则改发 STOP。
- 新开仓的最后一道检查先重新查询 broker 的 working 订单（查询失败、或存在无法识别腿的组合单，都拒绝），然后在这次查询之后重新读取：新事件、当日亏损、告警健康、实盘批准、AvailableFunds（必须来自 270 秒内仍在推送的账户流，扣除在途入场单和资金值变化之后的入场成交）。IBKR 每 3 分钟推送一次账户值；超过 210 秒没有推送时，引擎以不阻塞的方式重新订阅（每 210 秒最多一次），重新推送到达之前旧值按原时间判断。断线重连或 1101 数据丢失时，AvailableFunds 和 NetLiquidation 一起作废，等待新的推送。
- 退出判断本身不做任何阻塞调用，只把要做的事（发单、撤单、刷新成交）排进队列；队列逐项执行，每项阻塞调用之前、以及队列跑完之后都按实际时钟重新检查预算：若下一次退出检查会在等待期间到期，先做退出检查。被拒的退出 30 秒后才重试，不会空转。已声明的最坏耗时见 `IbkrBroker.worst_case_seconds`；启动时检查 `exit_check_interval_seconds` 至少比最慢的阻塞调用多 3 秒，否则拒绝启动。
