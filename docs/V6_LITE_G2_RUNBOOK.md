# V6-Lite G2 运行手册：真实 Paper 只读验证

当前代码在独立仓库 `C:\Dev\V6Lite`，使用本仓库的 `.venv`。旧位置 `C:\Dev\TradingV6\v6lite` 已停用，仅作为周一 G2-D 的回退副本；不要同时启动两处。新仓库的 `v6lite-batch13` tag 是经复核的代码基线，`code_sha=d4b19f1d43b25448`。

适用：`docs/V6_LITE_REDESIGN.md` 第 5 节的 G2。由 John 在本机启动，预计 3–5 个交易日。
目的：用真实 Gateway 的测量结果回答两轮复核留给 G2 的问题。**G2 不产生任何订单**，也不能证明成交恢复、撤单竞态或会计路径（这些属于 G3）。

## 0 边界（任何一条不满足就不要开始）

| # | 条件 | 如何确认 |
|---|---|---|
| 1 | 旧 V6 的 main/auto runner 已停止，且不会自动重启 | 任务管理器里没有旧 V6 的 python 进程；任务计划程序里没有会启动旧 V6 的任务（或已禁用） |
| 2 | IB Gateway 登录的是 **Paper**（端口 4002，账户 DU0000000） | Gateway 窗口标题或登录页 |
| 3 | **Gateway 勾选 Read-Only API**（Configure → Settings → API → Settings → Read-Only API） | 这是 broker 侧的第二道锁：即使软件出错，Gateway 也会拒绝下单或撤单。G2 期间保持勾选，进入 G3 前再取消 |
| 4 | `v6lite.paper.toml` 已按 `v6lite\examples\paper.toml` 复制，`[alerts]` 至少配好邮件或 Discord webhook 其中一种 | 第 2 步的启动告警要能在手机上收到 |
| 5 | 记录当天使用的代码版本 | `Get-FileHash v6lite\engine.py`，与整改报告的 SHA-256 清单对照，结果写进当天记录。同一个交易日内不要中途换代码 |

G2 使用的 client ID：dry-run 引擎为 `client_id + 1000`（1131），证据探针为 `client_id + 2000`（2131）。两者都不是正式引擎的 131，也不是旧 V6 的 120。

## 1 每个交易日的步骤（太平洋时间）

```powershell
cd C:\Dev\V6Lite
. .\.venv\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
```

| 时间（PT） | 动作 | 命令 |
|---|---|---|
| 开盘前（约 6:15） | Gateway 登录后，运行开盘前探针（约 10 分钟） | `python -m v6lite.tools.g2_probe --config v6lite.paper.toml --minutes 10` |
| 6:25 前 | 启动只读演练引擎，**整个交易时段保持运行**（开一个单独的窗口） | `python -m v6lite run --config v6lite.paper.toml --dry-run` |
| 6:30–13:00 | 引擎在 ET 10:00 与 13:30（PT 7:00 与 10:30）扫描，只写 `WOULD_BUY` 等日志 | 不需要操作 |
| 盘中任选一次（至少一天） | 运行盘中探针（30 分钟，同时与 yfinance 对比） | `python -m v6lite.tools.g2_probe --config v6lite.paper.toml --minutes 30 --compare-yfinance` |
| 收盘后（13:05 以后） | 在引擎窗口按 `Ctrl+C` 退出（dry-run 退出不会撤单） | — |
| 收盘后 | 收集当天证据（见第 3 节） | — |

旧 Paper 账户上的持仓会让演练显示 `unmanaged broker holding`，入场判断因此被阻断。v1 已决定不接管已有持仓，所以对这些持仓逐个标记为仅在演练状态中忽略：

```powershell
python -m v6lite ignore NVDA --dry-run --config v6lite.paper.toml --operator John
```

（需要先运行过一次 `run --dry-run` 以创建演练状态。这条命令不会创建、也不会改动正式状态。）

## 2 要取得的证据及位置

| 问题（出处） | 证据字段 / 日志 | 判定参考 |
|---|---|---|
| broker 写调用为 0（G2 准入） | 演练状态的 audit 表中没有 `INTENT_CREATED`；日志只有 `[DRY_RUN]`；TWS/Gateway 的订单页没有来自 client 1131/2131 的订单 | 任意一笔出现就**立即停止**，保留现场 |
| `AccountReady` 的实际键名与大小写（Codex D7） | 探针 `account_stream.ready_keys_seen`、`ready_values` | 记录真实拼写（代码已不区分大小写，D7）；若出现第三种拼写，列为修复项 |
| AvailableFunds 的推送节奏（D7、S3） | `account_stream.available_funds_max_gap_seconds`、`any_value_max_gap_seconds` | 第 1 天实测 180 秒。第七批起代码为：超过 210 秒未推送时以不阻塞方式重新订阅，超过 270 秒不再信任。引擎日志出现 `[ACCOUNT_STREAM] ... re-subscribed` 时，记录它之后多久收到新的账户值（应在数秒内） |
| 本地时钟与 IBKR 的偏差（S2） | `server_minus_local_seconds` | 与代码中 30 秒的成交余量对比 |
| 每类阻塞请求的实际最长耗时（D8、R10） | `slowest_seconds_by_call`、`timings` | 探针直接调用原始 ib_insync，测的是**单次请求**：与 history ≤ 12 s、单次 query ≤ 17 s（15 s 超时 + 2 s 余量）对比。引擎适配器的 query 预算另含最多 2 s 的排空等待（共 19 s），排空等待只在上一次请求超时后出现，看引擎日志中的 `owes an End` |
| reqAllOpenOrders 能看到的范围（判断 4） | `open_orders`、`open_orders_client_ids` | 是否包含其他 client 和人工单；账户字段是否填写 |
| execution 查询范围与佣金是否已到（P2 佣金） | `reqExecutions(account)` 与 `reqExecutions(engine client)` 的条数，`commission_reported` | G2 没有新成交；若账户里有当天的人工成交，记录其佣金是否在查询时已经可见 |
| 行情类型与新鲜度 | `quotes.*.marketDataType`（1 = 实时，3 = 延迟）；`watch.worst_quote_age_seconds` | 应为 1。引擎心跳和 `status` 的 `market_data` 一项会列出 IBKR 拒绝订阅（no_subscription）和延迟行情（delayed_symbols）的标的；异常时手机发 P1“实时行情不可用”，本地日志保留英文 `no real-time market data`；两项都应为空 |
| 日线口径 | `history.*.last_is_today`、`bars` | 盘中日线是否包含当天未完成的那根 |
| IBKR 与 yfinance 信号差异（L6 数据源） | 第七批起按**相同窗口**比较（同一截止日、同样根数），记录 `same_dates`、`max_close_diff_pct`、各指标值和 `score_diff`；另列旧扫描器自己的 yfinance 60d 窗口结果 | 窗口相同时分数仍不同，按指标值找出来源（日期、复权、当天那根 bar、成交量） |
| 断线与恢复（1100/1101/1102） | 探针 `connectivity`；引擎日志中的 `IBKR disconnected` / `IBKR reconnected`、1101 之后是否恢复报价 | 如果当晚 Gateway 自动重启，第二天看日志；不要为了测试而人为断网 |
| 远程告警送达 | 手机收到引擎启动的 INFO 告警与 self-test | 记录是否收到、延迟多久 |

## 3 每天收集并交回的文件

- `%USERPROFILE%\.v6lite\paper\g2\g2_probe_*.json` 与 `.md`
- `%USERPROFILE%\.v6lite\paper\dryrun\logs\v6lite_YYYYMMDD.log`
- 演练审计导出（只读）：

```powershell
python -c "import sqlite3,os,csv; p=os.path.expandvars(r'%USERPROFILE%\.v6lite\paper\dryrun\v6lite.sqlite3'); c=sqlite3.connect('file:'+p.replace('\\','/')+'?mode=ro',uri=True); w=csv.writer(open('g2_audit.csv','w',newline='',encoding='utf-8')); w.writerows(c.execute('select id,ts,kind,payload from audit order by id'))"
```

- 当天的手工记录：代码哈希、Gateway 版本、是否收到告警、异常现象。

这些文件都在仓库之外。不要提交 `.env` 或 `state/`。

## 4 通过标准（全部满足才算 G2 通过）

1. 3 个以上交易日内，V6-Lite 的 broker 写调用为 0。
2. 每天连接、账户绑定、行情（类型 1）、历史数据、订单可见性、远程告警都有记录。
3. 第 2 节每一行都有真实测量值；需要改代码的（例如 `accountReady` 的拼写、耗时超出声明）已列为下一批修复项。
4. 至少一次盘中探针带 yfinance 对比，差异已解释。

G2 通过**不等于** G3 放行：G3 还需要当前所有复核问题（含最新一轮 Codex 复核列出的 P1/P2）关闭，并由 John 决定取消 Gateway 的 Read-Only API 设置后，再运行 10 个交易日。

## G2-D 延迟行情补充演练

这是一套**独立的 Paper dry-run 机制演练**，供实时行情订阅开通前使用。由 John 在本机操作；不要与上述实时行情 G2 同时运行。两套 dry-run 共用 `account-dryrun` 锁，同一时间只能运行一套。

OPS-1 已在新仓库根目录生成 `v6lite.paper.delayed.toml`，不用再次复制样例，也不要覆盖 `v6lite.paper.toml`。周一从新位置执行：

```powershell
cd C:\Dev\V6Lite
. .\.venv\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
```

样例设置 `market_data_type = 3`，`state_dir = "~/.v6lite/paper-delayed"`；程序会展开 `~`，不会展开配置里的字面量 `%USERPROFILE%`。**配置已由 OPS-1 准备；创建 G2-D 状态目录仍由 John 在启动时完成。** 首次启动后 `Ctrl+C` 停止，再在新状态中标记既有持仓；如果该状态此前已创建，先检查现有 ignore 记录，不要重复改动。原实时演练状态中的 ignore 决定不会自动转移：

```powershell
python -m v6lite run --config v6lite.paper.delayed.toml --dry-run
# 在启动完成后按 Ctrl+C，随后逐项执行：
python -m v6lite ignore ABT --dry-run --config v6lite.paper.delayed.toml --operator John
python -m v6lite ignore IBB --dry-run --config v6lite.paper.delayed.toml --operator John
python -m v6lite ignore IWM --dry-run --config v6lite.paper.delayed.toml --operator John
python -m v6lite ignore PANW --dry-run --config v6lite.paper.delayed.toml --operator John
python -m v6lite run --config v6lite.paper.delayed.toml --dry-run
```

每天保留启动/心跳中的 `code_sha` 和 `data_mode: DELAYED rehearsal only`、`WOULD_BUY ... data=delayed gate=...`、`[QUERY]`、`[HISTORY]`、`[ACCOUNT_STREAM]`、`[SLOW_TICK]`、`[ALERT_DELIVERY]`，并记录手机 P1“延迟行情不可用”（本地日志 `no delayed market data`）是否出现及远程送达。延迟行情属于预期输入，本身不应触发实时行情缺失告警；若延迟行情也无订阅，或盘中可用报价不足持续超过 120 秒，则应收到 P1。出现任何 broker 订单或撤单调用须立即停止并保留证据。

**G2-D 的日子不计入 G2 有效日。** 价格比实际约晚 15 分钟；限价、数量与 gate 结果只用于核查机制，不能据此评价策略，也不能代替实时行情 G2 或授权 G3。

## 第十二批告警与 Gateway 应答监控

启动手机通知为中文 Discord 卡片，固定六栏：启动时间（PT/ET）、模式、状态、监控、扫描时间（PT/ET）、手动指令。邮件通道收到同内容纯文本。状态栏须看到 Gateway、对账、告警、报价及旧 V6 进程检查结论；异常详情仍以 `STARTUP` audit、`[STARTUP_SUMMARY]` 日志和 `status` 为准。卡片里的 `status`、`report`、`kill --reason`、`resume`、`ignore SYM`、`release SYM`、`resolve-intent ID`、`clear-anomaly CODE` 都是**本机命令行**指令，不能发到 Discord；`approve-live`、`revoke-live` 只在 Live 卡片列出。具体参数及使用前提见 `v6lite/README.md`。

交易日 06:00 PT 到收盘后 30 分钟（正常收盘为 13:30 PT，提前收盘跟随交易日历），引擎每 60 秒发一次不阻塞的只读 `reqCurrentTime`，包括 dry-run。连续两次超过 30 秒无应答时，手机应收到 P1 中文卡片 `IB Gateway 无响应`；回复证明服务器近期处理请求后应收到 INFO 恢复卡片。P0/P1 远程卡片固定三栏：发生了什么、影响、你要做什么；英文原文及完整诊断保留在本地日志。若本机与 Gateway 服务器时间差过大，心跳恢复判断保持保守，应核对本机时间同步和 Gateway 日志；不要仅凭 socket 显示 connected 判定 Gateway 正常。

收盘后 5 分钟（提前收盘日同步提前）发送中文健康卡片：扫描时刻、信号、`WOULD_BUY` 与 gate、下单/撤单次数、持仓漂移、心跳统计、告警送达、行情可用率及 `code_sha`。行情可用率按交易时段每分钟健康快照中的**实际订阅标的**计算，不用收盘后已过期的瞬时报价。完整 Markdown 日报仍保存在运行状态目录的 `reports\`。心跳、告警和行情样本只覆盖**当前进程在该交易日、截至报告生成时**；进程中途重启会丢失更早的计数，核对全天情况时还须查日志。对正式运行中只记于日志的逐笔 gate 结果，也以完整日志为准。

同机旧 V6 检查是单向启动门控：配置的 `foreign_lock_paths` 相对**配置文件目录**解析，正式运行若发现旧 V6 Python 入口在运行、或无法核实进程列表，会在连接 Gateway 之前 `REFUSED`；dry-run 会发 P1 卡片并在启动状态栏标记运行中的 PID 或“无法核实”。旧 V6 以后仍可自行启动，因此 John 须停用旧启动入口、核对 Windows 计划任务，并在每天启动前确认旧 V6 已停止。根目录 4/21 的 `trading_bot.lock` 保留，不能擅自删除；其 PID 将来若被其他 Python 进程复用，旧锁检查可能保守误拒，需人工核实。样例 TOML 需先复制到仓库根目录使用；原位运行样例前必须按其目录位置修正外部锁路径。路径绝对化改变配置指纹，既有 Live 批准会失效，须在复核后重新批准。

扫描时刻始终按 `America/New_York` 解释：夏令时切换后的 2026-11-02 和 2027-03-15，10:00/13:30 ET 对应 07:00/10:30 PT。2026-11-27 提前收盘 13:00 ET，健康报告为 10:05 PT；13:30 ET 的第二个扫描时槽仍可能记录扫描结果，但入场窗口已由交易日历关闭。

## 每批交付流程

1. Codex 只在 `C:\Dev\V6Lite` 的开发工作区改代码，不直接覆盖正在使用的版本。每批继续交付 SHA-256 清单与 `code_sha`。
2. Claude 复核通过、John 同意切换后，由 Codex 在独立仓库 `git commit`，并打 `v6lite-batchNN` tag；不要在交易日中途切换。
3. 回到已复核版本时，先停进程并保留当天证据，再由操作员审核目标 tag，执行 `git checkout v6lite-batchNN`。切换后重新核对 `code_sha` 与配置指纹；LIVE 审批如失效，须重新走审批。
4. 不推送远程。是否创建私有远程仓库，由 John 另行决定。OPS-1 未改旧 V6 的 Python 代码；旧位置仅留作回退。
5. 两份根目录运行配置都通过 `.gitignore` 排除，`foreign_lock_paths` 指向旧仓库 `C:/Dev/TradingV6` 的锁文件绝对路径，搬家后仍检查旧 V6。不要把配置、`.env` 或运行状态纳入提交。
6. 盘中探针如确需 `--compare-yfinance`，先在本仓库 venv 中执行 `python -m pip install -r .\requirements-probe.txt`；一般 G2-D 启动不需安装这组可选依赖。

## 附：代码版本

周一使用经 Claude 复核的第十三批：`v6lite-batch13`，`code_sha=d4b19f1d43b25448`，详见 `docs/V6_LITE_FIX_REPORT_ROUND13_2026-10-03.md`。每天开始前，用**实际在用版本**的 SHA-256 清单核对 `v6lite\engine.py` 等文件，并把结果写进当天记录。G2 期间只能在两个交易日之间切换代码，并在记录里注明从哪天起换成了哪个版本。

第五至第十三批的状态格式与第四批相同（schema 4），第四批起创建的演练状态可以继续使用（第四批写下的 release 记录会被正确识别为“已证实为平”的时间点）。第三批或更早创建的状态会被拒绝（文件不会被改动），需要先把旧的演练目录整体改名归档：

```powershell
Rename-Item "$env:USERPROFILE\.v6lite\paper\dryrun" "dryrun_batch3_archive"
```

归档后需要重新执行第 1 节的 `ignore ... --dry-run`（先运行一次 `run --dry-run` 以创建新的演练状态）。如果 `%USERPROFILE%\.v6lite\paper\v6lite.sqlite3`（正式状态）是第三批或更早创建的，进入 G3 前同样整体归档。

第五批起，引擎日志中出现 `owes an End`、`unsolicited ... End` 或 `connection renewed` 时，请把当天日志一并交回：这是第五批针对 IBKR 固定请求键加的保护，真实出现频率需要 G2 记录。
