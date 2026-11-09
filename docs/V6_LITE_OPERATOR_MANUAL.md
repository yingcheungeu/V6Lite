# V6-Lite 操作手册（John 日常版）

版本：2026-10-06 晚（PT）。写给 John 日常使用：每天怎么启动、盘中注意什么、怎么停机和交接。
- 细节与证据要求以 `docs\V6_LITE_G3_RUNBOOK_DRAFT.md`（G3 运行手册，Codex 维护）为准；两者不一致时，以 G3 运行手册为准，并告诉 Claude。
- 本手册随批次更新，过期内容直接删除，不保留旧版。

## 0 当前状态

| 项目 | 当前值 |
|---|---|
| 运行仓库 | `C:\Dev\V6Lite`，main 分支，HEAD `53896eb1`，tag `v6lite-run-b18` |
| 运行代码 | code_sha `9725a81b8e5e3e11`（启动卡片显示 `9725a81b`） |
| 启动脚本 | 已合并（`scripts\v6.cmd`）。用户目录的 `v6.cmd` 改为一行：`@"C:\Dev\V6Lite\scripts\v6.cmd" %*` |
| 行情 | 实时（NP,L1 三个网络已订阅，10/5 实测生效） |
| 连接方式 | TWS（Paper，端口 7497），不再使用 IB Gateway。10/6 全天实测稳定 |

**进度与安排**

| 日期 | 内容 | 结果 |
|---|---|---|
| 10/5 | 实时行情 dry-run（Gateway 4002，第十三批） | 有效；登录冲突造成两次断行情 |
| 10/6 | G3-T 配置 dry-run（TWS 7497，第十七批） | **通过**：TWS 连接、PID 236 误报已修、心跳 0 误报、Ctrl+C 停机完整 |
| 10/7 起 | **G3-T**：测试模式，每次 3 股，只交易 AAPL，在 Paper 账户真实下单 | 进行中 |
| G3-T 完成后 | G3-N（正式策略，Paper；配置须写两次扫描 10:00 / 13:30 ET）；之后才讨论 $5,000 实盘 | — |

## 1 三条铁律（任何时候都适用）

1. **同一个用户名只能有一个登录会话。** 引擎运行期间：
   - 不要在别处（其他电脑、手机 App、网页端）用 Paper 用户名登录 TWS；
   - 交易时段不要登录**真实账户**的手机 App、TWS 或网页端，否则 Paper 会断行情（错误 10197）；
   - 人不在电脑前时，用远程桌面连回这台电脑操作 TWS，不要另外登录。
2. **V6-Lite 管理某个标的期间，不要对这个标的另下卖单。** 紧急情况下：先在 TWS 撤掉 Lite 在该标的上的卖单，并确认已撤，再自己卖。
3. **不用市价单。** 人工下单一律用限价单；改价时修改原单，不要另下一张新单。

## 2 每天开盘前（06:00 PT 前）

1. 确认电脑时钟同步正常：运行 `w32tm /query /status`，Root Dispersion 应小于 1 秒。
2. **关闭 IB Gateway**，只用 Paper 用户名登录 TWS。
3. 核对 TWS 设置（`Global Configuration → API → Settings`）：
   - 勾选 Enable ActiveX and Socket Clients；Socket Port 为 `7497`；
   - **dry-run 的日子：Read-Only API 保持勾选**；
   - **G3 真实下单的日子：取消 Read-Only API**，收盘后再勾回去；
   - `Lock and Exit` 里的自动重启／退出时间设在收盘之后（例如 23:45）。
4. 确认没有遗留的 v6lite 进程（结果应为空）：
   ```powershell
   Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" | Where-Object { $_.CommandLine -match 'v6lite' } | Select-Object ProcessId, CommandLine
   ```

## 3 启动

### 3.1 10/6：G3-T 配置的 dry-run（不下单）

**方式 A：临时脚本**（你用户目录里的 `v6.cmd` + `v6lite-start.ps1`，永远只跑 dry-run）：
```
~\v6 g3t
```

**方式 B：原生命令**（效果相同）：
```powershell
cd C:\Dev\V6Lite; . .\.venv\Scripts\Activate.ps1; $env:PYTHONUTF8='1'; $env:V6LITE_DISCORD_WEBHOOK=[Environment]::GetEnvironmentVariable('V6LITE_DISCORD_WEBHOOK','User'); python -m v6lite run --config .\v6lite.g3t.toml --dry-run
```

**启动卡片应显示**：
- 版本 `9725a81b`；
- 测试模式（🧪）、Paper 演练（不下单）、实时行情；
- **不应**再出现 PID 236 的“旧 V6 隔离”P1。

**入场窗口开放后**（07:00 PT 之后），可在另一个窗口试一次测试入场：
```powershell
cd C:\Dev\V6Lite; . .\.venv\Scripts\Activate.ps1; python -m v6lite test-entry AAPL --config .\v6lite.g3t.toml --operator John
```
dry-run 只在日志里记一笔“会买 3 股”，不会下单。

### 3.2 第十八批合并后：正式启动脚本

合并完成后，把你用户目录里的 `v6.cmd` 改成一行：
```
@"C:\Dev\V6Lite\scripts\v6.cmd" %*
```
临时的 `v6lite-start.ps1` 可以删除。

| 命令 | 作用 | 是否下单 |
|---|---|---|
| `~\v6 dry` | Paper 正式配置 dry-run | 否 |
| `~\v6 g3t-dry` | G3-T 配置 dry-run | 否 |
| `~\v6 g3t` | **G3-T 真实下单**，需输入完整的 `YES` | 是（Paper） |
| `~\v6 entry AAPL` | 测试入场一次，需输入 `YES` | 是（Paper） |
| `~\v6 status g3t` | 只读查看状态 | 否 |
| `~\v6 g3n-dry` / `~\v6 g3n` | G3-N（以后用） | 否／是 |

脚本启动前会自动检查：
- 必须在 main 上，并且有 `v6lite-run-*` tag；
- 运行代码没有被改动；
- code_sha 与 tag 一致；
- G3 模式必须是 7497 端口，client 号正确。

任何一项不对，就显示 `V6 refused: <原因>` 并拒绝启动。把原因发给 Claude，**不要绕过**。

### 3.3 10/7 G3-T 第一天：先验收 Ctrl+C 停机，再真实启动

第十八批的 Ctrl+C 停机流程还没有实测，第一次真实下单前先做一次验收（06:00–06:25 PT）：

1. 运行 `~\v6 g3t-dry`，等手机收到启动卡片。
2. 在这个窗口**只按一次** Ctrl+C，然后等待：
   - 窗口自己回到命令提示符，没有报错；
   - 如果出现“终止批处理作业 (Y/N)?”，选 **N**，并截图记录。
3. 一切正常 → 运行 `~\v6 g3t`，输入 `YES`，开始 G3-T。
4. 卡住、报错，或出现过 Y/N 提示 → 不用脚本，改用下面的原生命令启动 G3-T，并把情况告诉 Claude：
   ```powershell
   cd C:\Dev\V6Lite; . .\.venv\Scripts\Activate.ps1; $env:PYTHONUTF8='1'; $env:V6LITE_DISCORD_WEBHOOK=[Environment]::GetEnvironmentVariable('V6LITE_DISCORD_WEBHOOK','User'); python -m v6lite run --config .\v6lite.g3t.toml
   ```
   原生方式下的测试入场：
   ```powershell
   cd C:\Dev\V6Lite; . .\.venv\Scripts\Activate.ps1; python -m v6lite test-entry AAPL --config .\v6lite.g3t.toml --operator John
   ```

**G3-T 的做法**：一次只做一笔测试入场，等它走完所有出场（TP1/TP2/TP3 或止损）之后，再做下一笔。需要完成的最少场景（3 轮完整出场、2 次止损、1 次止盈超时恢复、1 次强退重启），按 G3 运行手册第 7、8 节执行；每次做之前先和 Claude 确认。

## 4 盘中收到告警怎么办

**通用原则**：先看卡片的“你要做什么”，按编号顺序做。不确定时，什么都不做，先截图发给 Claude。**任何情况下都不要另下卖单。**

| 告警 | 做什么 |
|---|---|
| 🟠 P1 入场后保护单尚未确认 | 先不动。15 秒内没确认会升级为 P0 |
| 🔴 P0 入场持仓未获券商保护 | 在 TWS 查看该标的有没有 Lite 的 STP LMT 卖单、股数是否等于持仓。有就不动；没有就截图，联系 Claude |
| 🟠 P1 价格已到止损价 | 在 TWS 观察保护单是否在成交。不要另下卖单 |
| 🔴 P0 止损触发但限价未成交 | 可以等价格回到限价以上；或在 TWS **直接修改同一张保护单的限价**。不要另下卖单 |
| 🔴 P0 止盈后保护缺口未恢复 | 先在 TWS 撤掉卡片上写的 TP 单并确认，再核对保护单股数 |
| 🔴 P0 托管标的出现外部卖单 | 查这张单是谁下的：不需要就撤掉；需要就保留，但不要再下任何卖单。Lite 已冻结该标的 |
| 🟠 P1 Gateway 时钟偏差 | 看持仓和报价是否还在更新；在更新就记录下来，收盘后告诉 Claude |
| 🟠 P1 no real-time market data（10197） | 检查是否有人在别处登录了真实账户，退出那个登录 |
| P1 IBKR disconnected（1100） | 检查是否有人在别处用 Paper 用户名登录。通常几十秒会自动恢复 |

**紧急停止新开仓**（不影响已有的保护单）：用启动卡片列出的 `kill` 指令。第一次用之前，先运行 `python -m v6lite kill --help` 确认参数。

**人工全部平仓的顺序**（按 G3 运行手册第 9 节）：
1. 在 TWS 撤销 Lite 在该标的上的所有卖单（GTC 保护单和 DAY 止盈单），并确认已撤；
2. 自己卖出实际剩余股数（限价）；
3. 确认持仓为 0、没有在途单；
4. 运行 `release <标的>`。

## 5 收盘后

1. **13:05 PT 之后**，在引擎窗口**只按一次** Ctrl+C，等它自己退出。不要关窗口，也不要强制结束进程。
   - 正常停机会撤掉当天的 DAY 单，**保留 GTC 保护单**。持仓过夜由券商端的保护单负责，这是设计好的。
2. G3 真实下单的日子：在 TWS 重新勾选 **Read-Only API**。
3. 在 TWS 截图：持仓、工作中的订单、当天成交。
4. 告诉 Claude“收盘了”。Claude 直接读日志和状态：
   - dry-run：`C:\Users\yu-ch\.v6lite\paper-test\dryrun\`
   - G3-T 真实运行：`C:\Users\yu-ch\.v6lite\paper-test\`
5. 收到 Claude 的复核结论之后，才进行合并、改配置、进入下一阶段。

## 6 版本更新流程（谁做什么）

| 角色 | 负责 |
|---|---|
| Codex | 只在 `C:\Dev\V6Lite-dev` 开发；每批提交到 dev 分支并打 tag，交复核 |
| Claude | 复核：哈希、code_sha、测试、代码；给出裁定和转给 Codex 的指令 |
| John | 授权合并；所有启动、停机、TWS 操作都由 John 亲自执行 |

- 合并到运行仓库 main：只在**交易日之间**（13:30 PT 之后、次日 05:30 PT 之前），由 John 发消息授权，**不设定时任务**。
- 交易日 06:00–13:30 PT，Codex 不跑全量测试等大计算。
- 不 push；不把配置、`.env`、状态库提交到 git。

## 7 不要做的事

- 不要把 webhook 地址、bot token 或 `.env` 的内容贴到任何聊天里。
- 不要删除或改名 `%USERPROFILE%\.v6lite\` 下的状态目录，除非 Claude 给出明确的命令。
- 不要在 TWS 里删除 Lite 的 GTC 保护单。确实需要时，按第 4 节的平仓顺序来。
- 不要在 `C:\Dev\V6Lite` 里改代码或放新文件。
- Discord 目前只接收告警，**不接受任何交易指令**。

## 8 已知问题与限制

| 项目 | 状态 |
|---|---|
| PID 236（Secure System）误报 | 已修，10/6 实测通过 |
| 心跳“时钟偏差”导致连续误报 | 已修，10/6 实测通过（偏差 −0.02～−1.95 秒） |
| TWS 每 4 分钟提示 “2100 API客户端已退订账户数据” | 无持仓时引擎定期重新订阅造成，不影响运行；待下一批处理 |
| 收盘后日报显示 “no usable quotes” | 显示问题（收盘后报价过期）；待下一批处理 |
| G3-T 配置每天只扫描一次（10:00 ET） | G3-T 靠 `entry` 入场，不受影响；G3-N 配置要写两次 |
| 两个短暂的未保护空窗（A：入场成交 → 保护单确认；B：保护减量 → 止盈成交） | 已接受的 v1 风险。G3 实测最长空窗必须 ≤ 5 秒才能进入实盘 |
| 冻结的标的在仍有持仓时无法解除冻结 | v1 限制：只能平仓后 `release` |
| 33 项旧测试尚未改写为“保护开启”下的等价测试 | 实盘前完成 |
| 实盘时想同时用手机 App | 需要另开用户名，实盘前处理 |
| Discord 指令（`!status` / `!kill`） | 实盘之后再评估；不做 `!buy` / `!sell` |
