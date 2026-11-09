# V6-Lite 第十二批（第 2 版）整改与复核报告

日期：2026-10-03。依据：`docs/V6_LITE_BATCH12_INSTRUCTIONS.md` 第 2 版。第十一批基线 `code_sha=8033d0a7b3aa83e2`，其 54 项 SHA-256 清单在本批开工前逐项匹配。本批只改 V6-Lite 的告警、只读 Gateway 心跳、启动隔离及测试；旧 V6 代码、交易策略、订单路径和风控数值未改。

## 范围与执行边界

- 调查 Q-A、Q-B 并行，随后 Q-C、Q-D 并行补核；生产代码仅由一个实施 Agent 写入，测试仅由测试 Agent 写入，主 Agent 只写 README、运行手册和本报告，另有独立 Agent 只读复核。生产写入前和各次修补前，提升权限仅查询 `v6lite` Python 进程数量，结果均为 0；不输出命令行。
- 全部行为验证使用 FakeBroker、fake HTTP/SMTP、固定时钟与临时状态。没有用真实配置启动 `v6lite run` 或运行 `g2_probe`；少数测试以 FakeBroker 替身调用 CLI `main(["run", …])`，不连接真实 Gateway。未改 `.env`、仓库 `state/`、用户 `.v6lite`、旧 V6 文件、`AGENTS.md` 或 `docs/codex_memory.md`，未 stage/commit。没有输出 webhook、bot token 或其他凭证。
- 本批第 2 版增加 **G8 启动隔离门控**，这是“不改门控”的唯一明示例外，而且只会更严格：正式运行在连接 Gateway、创建运行状态或日志前拒绝旧 V6 并行／进程状态未知。入场风控、退出保护和 place/cancel 路径没有放宽或改变。第 2 版要求所有手机通知使用中文卡片；完整英文原始内容仍在本地日志。

## Q-A：Gateway socket 连着但无应答

第十一批 `IbkrBroker` 只订阅连接断开和错误事件；`is_connected()` 只看 socket 状态（原 `v6lite/broker/ibkr.py:267-286`）。引擎连接维护在 socket 未断且没有 link-down 事件时直接返回（原 `v6lite/engine.py:550-557`）。定期对账默认每 300 秒才尝试一次持仓查询（`v6lite/config.py:124`）；查询至多等约 15 秒，失败只使对账不健康，未形成针对“连接仍在但 Gateway 无应答”的有界 P1。固定请求通道极端情况下会在 60 秒的迟到 End 保护中断开重连，但这不是可靠的活性检测。因此第十一批没有可以保证时限的“无响应”告警。

本批每个交易日 06:00 PT 至该日收盘后 30 分钟，每 60 秒发送一次只读、非阻塞的 `reqCurrentTime`；两次请求各超过 30 秒没有有效回复后发 P1 `IB Gateway 无响应`，验证到近期服务器时间后 resolve 并发 INFO 恢复卡。ib_insync 的 `currentTime` 回复不带 reqId；实现以 UTC 回包时间及最多 10 秒的主机／服务器时钟差保守判断，明显过期回包不会解除告警，但无法在协议层完全证明回包属于哪一次请求。真实 Gateway 的回复语义和时钟偏差仍须在 G2 只读阶段验证；时钟无法证实时保守保持告警。心跳不占用 `positions`／`openOrders` 固定查询通道。

## Q-B：远程告警清单（改前正文长度）

以下是 `v6lite/engine.py` 所有 `Alerter.send`／`_alert_once` 标题族及 `v6lite/alerts.py` 自检；动态 SYMBOL、ID、异常详情会改变长度。长度是第十一批原始正文的大约量级，完整细节仍写本地日志。断线／重连的远端发送还受交易时段及在途订单条件控制。

| 级别 | 标题族 | 触发 | 原正文约长 |
|---|---|---|---|
| INFO | started；alert self-test | 启动；通道自检 | 启动约 25–45 行；自检约 55 字符 |
| INFO | IBKR reconnected；IB Gateway responding | 重连对账；心跳恢复 | 20–60 字符 |
| INFO | SYMBOL position closed；SYMBOL STAGE done；BUY SYMBOL | 平仓、退出阶段完成、买单提交 | 15–50 字符 |
| INFO | daily report DATE | 收盘后 5 分钟 | 完整 Markdown 原约 24 行以上，随事件增长 |
| P2 | loss cooldown；position released SYMBOL；scan failed | 连亏冷却、人工 release、扫描异常 | 80–200 字符或异常动态长度 |
| P2 | non-V6-Lite order on managed SYMBOL | 托管标的出现非 Lite 挂单 | 约 100 字符 |
| P1 | broker data anomaly CODE；fill anomaly CODE；late exit fill SYMBOL | 券商数据异常、成交异常、已结算退出后的迟到成交 | 动态、约 110–150 字符 |
| P1 | IBKR disconnected；live approval no longer valid；IB Gateway not responding | 断线、实盘批准失效、主动心跳连续无应答 | 约 60–110 字符 |
| P1 | 旧 V6 运行隔离需复核 | dry-run 启动时发现旧入口或无法读取进程信息（本批新增） | 约 20–60 字符 |
| P1 | unresolved combo order working；held order is working SYMBOL；orphan V6-Lite order SYMBOL | 组合单、人工保留单、账本外 Lite 挂单 | 约 130–220 字符，组合单可更长 |
| P1 | recovery failed；unverifiable order from a prior session ID；order state unknown ID/SYMBOL | 恢复证据失败、跨日悬单、意图状态不明 | 动态、约 50–300 字符 |
| P1 | broker/runtime mismatch；SYMBOL freeze check deferred；release refused SYMBOL | 对账差异、冻结证据缺失、人工释放被拒 | 约 140–200 字符；多项差异可超过 3 行 |
| P1 | positions without fresh prices；daily loss stop latched；no real-time market data；no delayed market data | 无新鲜持仓价格、当日止损门槛、行情缺失 | 约 80–350 字符 |
| P0/P1 | SYMBOL STAGE NOT FILLED；exit blocked/refused SYMBOL STAGE | STOP 为 P0，其他退出按条件为 P1；重试耗尽或最新证据拒绝卖单 | 约 70–250 字符 |
| P1 | SYMBOL STOP reduced to QTY；exit rejected SYMBOL PURPOSE | 可卖股数缩小；券商拒绝退出 | 动态、约 100 字符 |
| P1 | shutdown: working orders unknown；shutdown with unresolved orders | 退出时挂单查询失败或仍有未结订单 | 动态、约 100 字符以上 |
| P0 | V6-Lite no longer manages SYMBOL | 差异持续、冻结并转交人工保护 | 动态约 300–1900 字符以上，手机最难阅读 |

多行的旧启动、完整日报和多项对账差异，以及冻结告警的长段订单清单，是本批精简的直接原因。现在所有 Discord 远端消息均为 embed；P0/P1 固定中文三栏，INFO/P2 也有中文卡片，原始英文 `subject`／`body` 保留给日志与去重。

## Q-C：与旧 V6 的隔离现状

| 方面 | 复核结论与证据 | 本批处理／残余 |
|---|---|---|
| 代码 | 生产 `v6lite` 不 import `trading_bot_v6`；策略和日历注明移植来源（`v6lite/strategy.py:1-4`, `market_calendar.py:1-6`）。只有 `v6lite/tests/test_strategy_parity.py:30,57-67` 以 AST 读根目录 `market_scanner.py` 对比，不运行旧模块。 | 未改旧 V6；同仓库仍需版本隔离。 |
| 状态与日志 | Lite 默认 `~/.v6lite/{mode}`，dry-run 用其独立子目录（`config.py:139-142`, `__main__.py:57-59,119-120`）；旧 V6 使用仓库 `state/` 与 `logs/`。 | 保持分离，FakeBroker 测试只用 TEMP。 |
| client ID | 旧 V6 为 120（`trading_bot_v6/governance/account_binding.py:17`）；Lite 正式 131、dry-run 1131、探针 2131（`v6lite/config.py:82`, `__main__.py:50`, `tools/g2_probe.py:35,76-78`）。 | 未改变。 |
| 锁与进程 | Lite 的 OS 账户锁仅 Lite 识别；旧 `foreign_lock_paths` 随 CWD 解析，dry-run 曾跳过（原 `approval.py:177-184`, `__main__.py:168-169`）。根目录 `trading_bot.lock` 39 字节、修改于 2026-04-21，所记 PID 78763 当前不存在；`state/trading_bot_v6.lock` 当前不存在。 | 路径改按配置文件目录解析；启动时只读匹配三个旧 Python 入口，正式运行发现旧进程或无法核实即 REFUSED，dry-run 发 P1 并在启动状态列 PID／未知。旧锁未删除；旧 PID 将来被其他 Python 复用仍可能保守误拒。旧 V6 之后自行启动仍无反向保护。 |
| 机密 | 不读 `.env`；Claude 初核“只读 webhook 环境变量”需修正：配置邮件时也读取 SMTP 密码环境变量（`v6lite/alerts.py`, `config.py:70-71`）。 | 不输出两者的值／地址。 |
| 共用依赖 | 同仓库、同 `venv_v6`（`v6lite/README.md:9`, `start_v6.bat`）；升级 ib_insync 等可同时影响两边。 | John 决定是否独立 venv。 |
| 版本 | `git ls-files v6lite` 为 0，暂时靠每批 SHA 清单追溯；旧 V6 还有大量此前未提交更改。 | 不 stage／commit；建议 John 建 Git 记录。 |
| 旧入口 | `start_v6.bat`、`auto_trading_runner.py`、`start_trading.sh`、`watchdog.sh`、`setup_launchd.sh` 均仍存在；当前只读枚举旧进程及相关 Windows 计划任务各为 0。 | 技术门控只覆盖 Lite 启动瞬间，John 仍须停用旧入口／任务。 |

## Q-D：扫描时区和提前收盘

`scan_times_et` 在 `v6lite/market_calendar.py:15,111-115` 以 `America/New_York` 壁钟时间转换 UTC；引擎按 ET 交易日和 slot 调度（`engine.py` 的扫描调度），与本机 Windows 时区无关。10/2 实测两次为 PT 07:00:02 与 10:30:02，即 ET 10:00 与 13:30（`docs/V6_LITE_G2_DAY2_2026-10-02.md:219,239-261`）。夏令时切换的周日 2026-11-01、2027-03-14 无交易；紧接的交易日 11/02 与 03/15，两时槽仍为 ET 10:00/13:30＝PT 07:00/10:30。2026-11-27 提前收盘 13:00 ET，日报为 13:05 ET／10:05 PT；13:30 ET 扫描时槽仍可能记录扫描，但交易日历已关闭入场窗口。G9 三项离线测试已在原调度代码直接通过，未改扫描代码。

## G1–G9：发现 → 改动 → 测试

| 项 | 发现 | 改动 | 测试 |
|---|---|---|---|
| G1 | 启动手机正文原为英文代码块，约六段且长行难读。 | 六栏中文 embed，动态读取模式、时间、扫描时槽、对账间隔、心跳常量、收盘日历和旧 V6 状态；邮件纯文本；完整 `STARTUP` audit／日志保留。 | 第 2 版基线失败；FakeBroker 核对 JSON 六栏、INFO 绿色、PT/ET、早收盘、邮件。 |
| G2 | socket 在线但 Gateway 无应答没有有界 P1。 | 只读 `reqCurrentTime` 非阻塞心跳，60 秒一探、两次 30 秒超时发 P1，近期回复解除；dry-run 同用，不占持仓／挂单查询通道。 | Round11 基线失败；Fake IB 覆盖正常、一次／两次遗漏、恢复、迟到回复、tick 非阻塞、时钟差。 |
| G3 | 完整 Markdown 日报直接发手机，信息密度过高；扫描异常若含换行会使卡片解析中断，分号后的错误细节也可能留在手机端。 | 本地完整文件不变，远端七栏中文健康卡含扫描与 gate、place/cancel、持仓漂移、心跳、告警、行情与 `code_sha`；远端摘要固定十条逻辑记录，扫描失败只显示“扫描失败（详见日志）”。 | FakeBroker 演练扫描、零写单、卡片 JSON／邮件；多行与分号异常各先失败再通过，完整异常留本地；正式运行 gate 的逐笔详情仍以日志为准。 |
| G4 | P0/P1 长正文可在手机折叠，部分超过 1900 字符。 | 远端固定“发生了什么／影响／你要做什么”三栏，分级颜色；INFO/P2 也用中文 embed；英文原文只进本地日志，投递异常只记状态码及异常类型。 | 基线失败；P0/P1/INFO/P2、长度上限、UTF-16 emoji、邮件和 webhook 脱敏测试通过。 |
| G5 | 延迟行情按 scan 而非实际订阅集合统计，scan 多于 tradable 时可误报。 | 行情可用率只统计启动时实际订阅的 tradable＋持仓。 | 第十一批基线失败；scan 超出 tradable 的 FakeBroker 测试通过。 |
| G6 | `engine.py`、`__main__.py` 为 CRLF，掩盖代码差异。 | 两文件先隔离统一 LF，再实施功能改动；该步换行规范化后的文本 SHA 前后相同。 | `engine.py` 原 2087 个 CRLF，SHA `accff6f06b585a9aa1ab07c17674ea93423b9f9a5e20bac4bc0b288a890e6267` → LF SHA `0c6db916…`；`__main__.py` 原 451 个 CRLF，SHA `18f3eb24e8071a6134d1bd89ee5d95d90b7fe00c3b498b94f61978921c54537b` → LF SHA `2b22286c…`；把隔离后的 `__main__.py` 转回 CRLF，SHA 与第十一批完全一致。 |
| G7 | ib_insync End 回调测试依赖计时调度，首轮 50 次在第 50 次复现失败。 | 测试改为直接调用真实 wrapper 的 unsolicited `positionEnd()`，不使用真实 sleep／call_at。 | 失败轮未计入；修复后独立 50/50，最终第 2 版再独立 50/50。 |
| G8 | foreign locks 随 CWD 漂移；旧进程可与 Lite 并行。 | 按配置目录解 foreign 路径；启动前精确检查旧 Python 入口，无法读取也保守拒绝 formal；dry-run P1 并在六栏状态显示 PID／未知。 | 第 2 版基线失败；非仓库 CWD、三入口识别、错误／空命令行、正式 REFUSED 前无 broker 连接、dry-run 告警测试通过。 |
| G9 | 需证明 ET 调度跨 DST 和早收盘。 | 只加测试，调度生产代码原样。 | 2026-11-02、2027-03-15 扫描 ET/PT 与 2026-11-27 日报时间均在基线通过。 |

G6 的 LF-only 完整 SHA 当时未留存，只保留表中前缀、CRLF 数量及规范化文本相等的复核记录。现有 `engine.py`、`__main__.py` 又包含后续功能改动，不能把当前完整哈希冒充隔离换行步骤的哈希；这一证据限制不影响最终 56 项原始字节 SHA 清单的复核。

## 失败先行、独立复核与最终 Windows 验证

- Round11 精确 54 文件哈希基线上的第 1 版 G1–G5 新测试：**10 failed／0 passed**；G7 原测试定向失败。第 2 版新测试在已具备第 1 版修补的代码上仍 **7 failed／4 passed**，另卡片限额 **1 failed**；G9 三项先行通过，webhook 脱敏旧行为一项先行通过。第 2 版测试没有声称重新执行完整 Round11 checkout：新版基线实际比 Round11 多了第 1 版功能，且失败点在 Round11 同样缺失的中文 embed／旧 V6 门控。
- 评审追加的 generic 英文泄漏／`pytest.exe` 误判 **5 failed**，UTF-16 emoji／空命令行 **2 failed**，均先在当时草稿上复现，再修正。首轮第 2 版聚焦 **46 passed**。此前一次第 2 版 Windows 全量 **351 passed／40 failed**，全是旧英文远端展示断言或已强化的 webhook 错误口径；失败轮不计入连续通过。测试迁移只把远端短卡和本地完整日志分开断言，交易门控、撤单、成交、冷却、冻结等安全断言保留。
- Windows 账户锁旧测试在迁移聚焦中出现两次失败：它 `kill()` 的是 venv 启动器，不能保证实际持锁解释器已退出。独立 Agent 只读观察到 Windows 两层 Python PID；测试改为由实际持锁解释器在 stdin 握手后执行 `os._exit(0)`，不显式解锁、不 sleep／重试，再断言 OS 立即释放，隔离及聚焦均通过。日报中文值另 **2 failed**，多行扫描错误 **1 failed**，分号错误细节 **1 failed**，均先在对应草稿复现，修复后相关聚焦最终 **52 passed**。
- 独立只读 Agent 复核 G8 检查顺序、相对路径与 LIVE 指纹、进程识别／未知保守处理、Discord UTF-16 上限和 webhook 异常日志，未发现阻断项。根锁未删，旧 V6 后启动的单向竞态和真实手机送达仍列为残余风险。
- 最终 G7 定向测试 **50/50 连续通过**，JUnit 为 `%TEMP%/v6lite_round12_g7_delivered_01.xml` 至 `_50.xml`。最终文件状态下全量三轮结果：**395 passed／50.08 秒**、**395 passed／50.18 秒**、**395 passed／49.96 秒**，每轮 0 failed／0 skipped；JUnit `%TEMP%/v6lite_round12_release_full_{1,2,3}.xml`。使用 Windows `venv_v6`、UTF-8、TEMP pycache 和禁用 pytest cache；所有最终测试串行执行，未连接 Gateway。

## FakeBroker 实际渲染样例

以下三组来自 fake HTTP/SMTP 截获的实际消息，不接触真实 webhook、Gateway 或账户。启动／日报由一套正常 FakeBroker 演练生成；P1 来自另一套 FakeBroker 明确报告 AAPL 缺少延迟行情订阅的演练，避免在“正常”日报里人工注入异常。示例账户为测试专用 `DU0000000`，URL 不写入报告。

### 启动卡片：embed JSON

```json
{
  "title": "🟢 V6-Lite 已启动（Paper 演练（不下单） · 延迟行情）",
  "color": 3066993,
  "fields": [
    {
      "name": "启动时间",
      "value": "2026-10-06 06:50 PT / 09:50 ET",
      "inline": false
    },
    {
      "name": "模式",
      "value": "Paper · 演练（不下单） · 延迟行情 · DU0000000 · 版本 b26bf09a",
      "inline": false
    },
    {
      "name": "状态",
      "value": "✅ 正常\nGateway 已连接 · 对账一致 · 告警通道正常 · 旧 V6：未运行\n入场窗口 10:00 ET 开启\n报价可用 3/3 · V6-Lite 持仓：无",
      "inline": false
    },
    {
      "name": "监控",
      "value": "IB Gateway 心跳：每 60 秒，连续 2 次无应答即告警\n持仓漂移：每 300 秒对账，不一致即告警\n每日健康报告：13:05 PT（收盘后 5 分钟）",
      "inline": false
    },
    {
      "name": "扫描时间",
      "value": "10:00 ET / 07:00 PT",
      "inline": false
    },
    {
      "name": "手动指令（本机命令行）",
      "value": "这些指令只能在本机命令行执行，不能在 Discord 输入。\nstatus 查看状态 · report 查看日报\nkill --reason 停止入场 · resume 恢复\nignore 标的 · release 标的 · resolve-intent ID · clear-anomaly 代码",
      "inline": false
    }
  ]
}
```

### 启动卡片：邮件纯文本

```text
🟢 V6-Lite 已启动（Paper 演练（不下单） · 延迟行情）
启动时间：2026-10-06 06:50 PT / 09:50 ET
模式：Paper · 演练（不下单） · 延迟行情 · DU0000000 · 版本 b26bf09a
状态：✅ 正常
Gateway 已连接 · 对账一致 · 告警通道正常 · 旧 V6：未运行
入场窗口 10:00 ET 开启
报价可用 3/3 · V6-Lite 持仓：无
监控：IB Gateway 心跳：每 60 秒，连续 2 次无应答即告警
持仓漂移：每 300 秒对账，不一致即告警
每日健康报告：13:05 PT（收盘后 5 分钟）
扫描时间：10:00 ET / 07:00 PT
手动指令（本机命令行）：这些指令只能在本机命令行执行，不能在 Discord 输入。
status 查看状态 · report 查看日报
kill --reason 停止入场 · resume 恢复
ignore 标的 · release 标的 · resolve-intent ID · clear-anomaly 代码
```

### 每日健康报告：embed JSON 与邮件纯文本

```json
{
  "title": "✅ 正常 · V6-Lite 每日健康报告 2026-10-06",
  "color": 3066993,
  "fields": [
    {
      "name": "扫描",
      "value": "计划：10:00 ET 信号 1 · 已入场 无\n信号：10:00 ET AAPL(78)\n结果：WOULD_BUY AAPL gate=ok",
      "inline": false
    },
    {
      "name": "下单/撤单",
      "value": "0 次下单 · 0 次撤单",
      "inline": false
    },
    {
      "name": "持仓与漂移",
      "value": "无 · 漂移：无",
      "inline": false
    },
    {
      "name": "Gateway 心跳",
      "value": "本进程今日截至报告时: 应答 2 · 最长已验证延迟 0 秒 · 未应答 0",
      "inline": false
    },
    {
      "name": "告警送达",
      "value": "本进程今日截至报告时: 发出 2 · 失败 0",
      "inline": false
    },
    {
      "name": "行情可用率",
      "value": "100% 常规交易时段可用率 · 已订阅 3 · 6/6 标的样本 · 2 次采样 (本进程今日；延迟行情)",
      "inline": false
    },
    {
      "name": "code_sha",
      "value": "b26bf09a03e41bb5",
      "inline": false
    }
  ]
}
```

```text
✅ 正常 · V6-Lite 每日健康报告 2026-10-06
扫描：计划：10:00 ET 信号 1 · 已入场 无
信号：10:00 ET AAPL(78)
结果：WOULD_BUY AAPL gate=ok
下单/撤单：0 次下单 · 0 次撤单
持仓与漂移：无 · 漂移：无
Gateway 心跳：本进程今日截至报告时: 应答 2 · 最长已验证延迟 0 秒 · 未应答 0
告警送达：本进程今日截至报告时: 发出 2 · 失败 0
行情可用率：100% 常规交易时段可用率 · 已订阅 3 · 6/6 标的样本 · 2 次采样 (本进程今日；延迟行情)
code_sha：b26bf09a03e41bb5
```

### P1 告警：embed JSON 与邮件纯文本

```json
{
  "title": "🟠 P1 · 延迟行情不可用",
  "color": 15965202,
  "fields": [
    {
      "name": "发生了什么",
      "value": "延迟行情缺失 AAPL。",
      "inline": false
    },
    {
      "name": "影响",
      "value": "受影响标的无法获得可用报价；演练扫描或退出检查可能暂停。",
      "inline": false
    },
    {
      "name": "你要做什么",
      "value": "检查 Gateway API 行情订阅及延迟行情状态。",
      "inline": false
    }
  ]
}
```

```text
🟠 P1 · 延迟行情不可用
发生了什么：延迟行情缺失 AAPL。
影响：受影响标的无法获得可用报价；演练扫描或退出检查可能暂停。
你要做什么：检查 Gateway API 行情订阅及延迟行情状态。
```

## John 决定的事项（本批只建议，未执行）

1. **Git 版本记录：**当前 `v6lite/` 全部未跟踪，建议先保全旧 V6 未提交工作，再由 John 决定为 Lite 建 `codex/v6lite` 分支并只暂存 `v6lite/`、本报告及运行手册，或迁移至独立仓库。人工确认后可依次运行 `git status --short`、`git switch -c codex/v6lite`、`git add -- v6lite docs/V6_LITE_G2_RUNBOOK.md docs/V6_LITE_FIX_REPORT_ROUND12_2026-10-03.md`、`git commit -m "Record reviewed V6-Lite"`；本批没有运行这些命令中的写操作。
2. **冻结旧 V6：**tag 只能指向已有 commit，不能代表现在未提交的旧 V6 文件；应先审查并保存旧版的独立快照提交，再由 John 决定 `git tag v6-frozen-2026-10-03 <snapshot-commit>`。不能把现工作区直接称为已冻结版本。
3. **停用旧入口：**先用 `Get-ScheduledTask` 查看准确任务名，再由 John 对确认属于旧 V6 的任务执行 `Disable-ScheduledTask -TaskName <准确任务名>`；另检查 `start_v6.bat`、`start_trading.sh`、`watchdog.sh`、`setup_launchd.sh` 的人工／自动启动安排。启动时检测不能阻止旧版稍后被启动。
4. **独立 venv：**如选择隔离依赖，可在审核依赖清单后用 `py -m venv C:\Dev\TradingV6-Lite-venv` 建独立环境并安装 Lite 的受控依赖；切换前须重跑本报告测试。当前仍共用 `venv_v6`。
5. **旧锁与 LIVE 批准：**根 `trading_bot.lock` 保留；若将来旧 PID 被复用而拒绝启动，先核实实际进程，由 John 决定处置。外部锁路径变绝对会改变配置指纹，现有 LIVE 批准自动失效；若进入 LIVE，需重新复核并执行 `approve-live`，且券商端 L7 保护单仍未实现，不得凭本批放行 LIVE。

## 指纹与 SHA-256 清单

`v6lite/version.py` 将包内非 `__pycache__` 文件按相对路径排序，逐个纳入“路径 UTF-8、NUL、原始字节、NUL”，取 SHA-256 前 16 位。最终 `code_sha=b26bf09a03e41bb5`。以下清单含 `v6lite/` 全部文件与 `docs/V6_LITE_G2_RUNBOOK.md`；报告自身 SHA 在交付摘要单列，避免自引用。

```text
17b8628794cf5cf5e592be52009a9dbf07ff369673074d69c36b4a91103f2cec  v6lite/README.md
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
42d6b67bdf7d7e0c757aff4b8c8af3753f5f15f612eafbb9d1c4d59113fede45  v6lite/__main__.py
343993a58f357ab5d47951cef238844dc064e5c3659684b55e4aa89ad7bdc32c  v6lite/alerts.py
8430c824587d4b07f0c752c46574b5e8beb39dfc5d667d07ada2b1cdb78ebee6  v6lite/approval.py
2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24  v6lite/broker/__init__.py
33b0d797a0edc5441c4439a0cb1d780639082893f9c4c9c187de254488fd9a8d  v6lite/broker/base.py
4a45f75fb2b5b1dd99175c0f7d1efb354e05476526aac957d35ecf883c1318ce  v6lite/broker/fake.py
5e80f1d0926f079f69ab41a3b23004eb07ff3b6f87b9d13a777ebc1d23473c81  v6lite/broker/ibkr.py
0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1  v6lite/clock.py
8622ac8ac008a1120355c4f435e0a5651133e67063fea00d32f77442e5af41f0  v6lite/config.py
f02311f376380a25dff7c6071b9a293ca10348eb0239ca4d3e6783f04b6e6d9b  v6lite/engine.py
1032534d1665efed3143d97bed9f7525410772a49ee5894f2000d0bcdd2fd673  v6lite/examples/live.toml
9c8682f8fa30a91703a53994745e948bc778186688dfe843acc353a5d8df123b  v6lite/examples/paper.toml
bf20981fa58c4abe886bdbb95ffe290ef821d2c60fa7d236d2cedf5d0d38e4cb  v6lite/examples/paper_delayed.toml
c3b4a074f96c5a25789afb5fabf088ead3b1daddaaa1ee5477ec1399c77627e6  v6lite/exits.py
1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2  v6lite/market_calendar.py
596e5a6580824ec5bea72c35d057158a0078bd1ce3643fc8b6035d68de63a034  v6lite/models.py
4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1  v6lite/reconcile.py
49d9a75254314a4e0cdf263f8dd91fcded56c0c74f62b15f629893450188bbdd  v6lite/report.py
abc521f671e878a0ab5690e1fa178dc872d29b5396b86eaeb5f016be1594c687  v6lite/risk.py
c7b9000919a9d7672aead2eddab786f96064946cf6084cf6b19cf9351ae70e26  v6lite/sizing.py
6c169be23a77746a6dd90ad0e84d7550cca15bd7038b6398f3ab40b034379506  v6lite/store.py
9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e  v6lite/strategy.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  v6lite/tests/__init__.py
aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb  v6lite/tests/conftest.py
fd6c76b9f1e7bd2ff59cd82b86453619f156f1c9c61885aab31f248f01f1f038  v6lite/tests/helpers.py
1ea696cb95f8ade1fbdbe8ce67911f76f34aff5afc85e69af2b9f7e9230b4495  v6lite/tests/test_batch10.py
6ba9c010bc43b2fbf54898f6e92374aabd59f0bc5f55086f8f9d328719712a49  v6lite/tests/test_batch11.py
800df6e93e9c46883ab98d2c9436a24d746d847bbed94f89688e048730cb960d  v6lite/tests/test_batch12.py
698c6ba35df738b5b57cfc7d4b244ce0c07040070ca612147f87093ca363cc31  v6lite/tests/test_batch12_v2.py
829c62e04466f37dcce0923592dc1afada1831f820a0ea74be2e6639d1274741  v6lite/tests/test_engine.py
96ca7e34f36527479bd1f47adb25df9b2c7c3a7e9c443da97144199443b2a683  v6lite/tests/test_g2_day1.py
fe84ef645d99f421c64f670aa7f883d16b5a24c8c15b4dd30a4aa8f035b5ea06  v6lite/tests/test_g2_day1_selfreview.py
a0db6b981992a3cf20d9f959a679c608842f4619007331bae9b26908cee3ccb7  v6lite/tests/test_g2_day2_prep.py
ba38215d7a6946d60884d0e3dac99b641e7bcde058c4893b94cb9d62b6a6fe18  v6lite/tests/test_g2_probe.py
6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a  v6lite/tests/test_ibkr_adapter.py
9d8e912ca50fbe2dae6fa855a87773bfa0fa753d2743106abadf6b0c53da9da8  v6lite/tests/test_review_fixes.py
3f1b96a33996c29e4a9ccec7a30b2b39cec812fe54cb63f6d432fda3238ab519  v6lite/tests/test_review_round2.py
5f477a5f5bcef87b0509c9162baadf8d12378c7b4d4bb412add5ae63855a6ec5  v6lite/tests/test_review_round3.py
0ab17715ddd682397df3783dc2b591e6bd00dd47860641030a48b5cd832ea5ba  v6lite/tests/test_review_round4.py
a0b7163388e34a71f3c165a86dabc323710def94026133ed1673eac209b69105  v6lite/tests/test_review_round4_selfreview.py
c580d6583cb5e35a53cac05e3f59e419a6b5f33adf605ef2de12f2750ddfe0fc  v6lite/tests/test_review_round5.py
087cdf74fff6745ef850c6fc2a035469f2468589d81062a9dba96f40d22b51aa  v6lite/tests/test_review_round5_selfreview.py
0f7d2093ecc47f760459560af2fb6118c696b7ddaae521c35a961d57a7e353b3  v6lite/tests/test_review_round6.py
9931b2855c7121563011e79c5786e9ce51520e4bc9e634022d5c9cdaf069be9f  v6lite/tests/test_review_round7.py
a3589fae5996245f7246f1860875a847b6761ed9f8bab5a317ab5c7d9e5e86bf  v6lite/tests/test_review_round7_selfreview.py
c65ac1c396468aae3124ffcdc0f61b2ed483547ec1b0f47119f00950008b3736  v6lite/tests/test_review_round8.py
e927860575cb55769a910a0dfbced96b725922da6629010c894ec2d3c86b36c3  v6lite/tests/test_review_round8_selfreview.py
a5377a4f520e6c3d340cb40571f24eae1dd431be871ecfad241b0d0080424aef  v6lite/tests/test_soak.py
732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f  v6lite/tests/test_strategy_parity.py
75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42  v6lite/tests/test_units.py
91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1  v6lite/tools/__init__.py
e756a719f9f8c1cded3d1123f67fa0097b504680589319be40619bc5d7f6763e  v6lite/tools/g2_probe.py
9aa52b23a3c8c08801f9d86c46ae1090a28c78dbde5d5b4b2923347bc74ece45  v6lite/version.py
fe8874c9fab3d93924ba16e83c4d0ece950e97956f4958ae7d19709e45855a23  docs/V6_LITE_G2_RUNBOOK.md
```
