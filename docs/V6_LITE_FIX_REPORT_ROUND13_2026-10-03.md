# V6-Lite 第十三批整改与复核报告

日期：2026-10-03。依据：[第十三批指令](V6_LITE_BATCH13_INSTRUCTIONS.md)。第十二批经 Claude 复核可用于 G2-D；本批针对进入 G3 前必须补足的 P0/P1 手机告警信息。第十二批 `code_sha=b26bf09a03e41bb5`。

## 范围与边界

任务归类为**代码修复**，仅修改 V6-Lite 告警渲染和旧 V6 进程无法核实时的拒绝提示。唯一生产实施 Agent 修改 `v6lite/alerts.py`、`v6lite/approval.py`、`v6lite/__main__.py`；测试 Agent 独占测试文件；独立 Agent 只读复核。按第十二批 SHA-256 清单逐文件比较，除上述 3 个生产文件、测试文件及新增测试外，V6-Lite 文件未变。`v6lite/engine.py`、交易门控、告警触发条件/级别/重复规则、订单路径与旧 V6 文件未改。

首次写测试前、首次写生产代码前及 H4 边界修复前，Windows 只读进程检查均得到 `v6lite_process_count=0`。未连接 Gateway、未运行真实配置引擎或探针；测试中的 `run` CLI 只用 FakeBroker。未改 `.env`、`state/`、`%USERPROFILE%\.v6lite`；未 stage、commit 或输出机密。

## H1：原始告警家族与中文卡片标题

下面列出 Q-B 的 28 个来源家族及 32 个 STOP/TP1、意图 ID/标的等具体变体。表内标题是实际渲染值；32 个样例标题互不相同。

| 场景 | 原始标题样例 | 实际手机标题 |
|---|---|---|
| broker anomaly | `broker data anomaly execution_correction:EX-7` | 🟠 P1 · 券商数据事件异常 |
| fill anomaly | `fill anomaly duplicate_open_position:AAPL` | 🟠 P1 · 成交入账异常 |
| late exit fill | `late exit fill AAPL` | 🟠 P1 · 退出成交迟到 |
| disconnect | `IBKR disconnected` | 🟠 P1 · IBKR 连接断开 |
| approval | `live approval no longer valid` | 🟠 P1 · 实盘审批失效 |
| heartbeat | `IB Gateway not responding` | 🟠 P1 · IB Gateway 心跳无应答 |
| old V6 | `旧 V6 运行隔离需复核` | 🟠 P1 · 旧 V6 隔离状态异常 |
| combo | `unresolved combo order working` | 🟠 P1 · 组合挂单阻断退出 |
| held | `held order is working AAPL` | 🟠 P1 · 保留订单仍在途 |
| orphan | `orphan V6-Lite order AAPL` | 🟠 P1 · 账本外 Lite 订单在途 |
| recovery | `recovery failed` | 🟠 P1 · 恢复对账失败 |
| prior session | `unverifiable order from a prior session v6l-prior01` | 🟠 P1 · 跨交易日订单无法核实 |
| order unknown | `order state unknown v6l-unknown01` | 🟠 P1 · 订单状态不明 |
| submission unknown | `order state unknown AAPL` | 🟠 P1 · 提交回执不明 |
| reconcile | `broker/runtime mismatch` | 🟠 P1 · 持仓账本与券商不符 |
| freeze deferred | `AAPL freeze check deferred` | 🟠 P1 · 冻结核查暂缓 |
| release refused | `release refused AAPL` | 🟠 P1 · 人工释放被拒 |
| stale position price | `positions without fresh prices` | 🟠 P1 · 持仓报价过期 |
| daily loss | `daily loss stop latched` | 🟠 P1 · 当日亏损门槛触发 |
| realtime quotes | `no real-time market data` | 🟠 P1 · 实时行情订阅异常 |
| delayed quotes | `no delayed market data` | 🟠 P1 · 延迟行情订阅异常 |
| stop retry exhausted | `AAPL STOP NOT FILLED` | 🔴 P0 · 止损重试耗尽 |
| take profit retry exhausted | `AAPL TP1 NOT FILLED` | 🟠 P1 · 止盈重试耗尽 |
| stop blocked | `exit blocked AAPL STOP` | 🔴 P0 · 止损下单被拦截 |
| take profit blocked | `exit blocked AAPL TP1` | 🟠 P1 · 退出下单被拦截 |
| stop unsent | `exit refused AAPL STOP` | 🔴 P0 · 止损未发出 |
| take profit unsent | `exit refused AAPL TP1` | 🟠 P1 · 退出订单未发出 |
| stop reduced | `AAPL STOP reduced to 4` | 🟠 P1 · 止损数量缩减 |
| exit rejected | `exit rejected AAPL TP1` | 🟠 P1 · 券商拒绝退出订单 |
| shutdown query unknown | `shutdown: working orders unknown` | 🟠 P1 · 停机时挂单状态不可查 |
| shutdown unresolved | `shutdown with unresolved orders` | 🟠 P1 · 停机后仍有未结订单 |
| freeze | `V6-Lite no longer manages AAPL` | 🔴 P0 · V6-Lite 停止管理持仓 |

## H1–H5：发现 → 改动 → 测试

| 项 | 发现 | 改动 | 测试证据 |
|---|---|---|---|
| H1 | 第十二批把多种订单事件合并成“订单状态需核对”或“运行告警”，最紧急的止损未发出甚至没有专门标题。 | `_page_card` 按 28 个已知来源家族分派独立中文标题；STOP 与 TP1、ID 与标的入口按可操作风险区分。未知未来事件才用“未分类安全告警”，且不复制异常原文。 | 32 个变体标题唯一；P0 止损未发出在标题和正文均明确写出。 |
| H2 | 手机端丢失账本/券商股数、需卖/可卖、价格、阶段、订单号和异常代码。 | 从现有原始 `subject/body` 只提取受限格式的标的、阶段、数字、最多五个订单号及异常代码；已请求撤销的自有单与**未撤销、仍可能成交**的订单明确区分。无 ID/代码时先提示本机查证，不给无效命令；任意 broker/OS 异常文本不直接透传。 | 32 个家族/变体事实断言，冻结两笔订单、止损拒绝 20/0 股、价格、异常代码均通过；合成 URL/token 不进入远端卡片，本地英文原文保留。 |
| H3 | “必要时人工保护”省略了先确认/撤销在途单的顺序，可能引发超卖。 | “你要做什么”固定编号步骤；冻结先在 TWS 逐一核单/撤单，确认该标的无在途单**之后**才人工保护或平仓，券商 0 股且无在途单后才在本机 `release SYMBOL`。止损拒绝先写“止损未发出”与需卖/可卖，再安排 TWS 核查及人工保护。 | 冻结步骤顺序、两个未撤销订单号和最后 `release AAPL` 专项测试；未知方向 `?` 明写“方向未知”；无意图 ID 不建议执行 `resolve-intent AAPL`。卡片使用指令短写法，完整 CLI 选项见运行手册。 |
| H4 | Discord 自动截断可能切断股数、订单号或第 3 步命令。 | P0/P1 每栏按 UTF-16 计不超过 600，整卡不超过 1500；先去解释文字，再将订单/对账记录按完整条目分栏，最多显示前五个订单，超出用“另有 N 个/项”。步骤完整优先，解释性细节指向本机日志。 | 原始第十二批长度测试先失败；草案上 5 个长 ID 的 40/60 位数量及五标的对账 60 位数字再次先失败，修复后通过。独立探测冻结 5×78 字符 ID + 20/40/60 位数量均完整；五项对账 60/100 位均完整。 |
| H5 | Windows 上 Python 进程命令行读取被拒时，正式拒绝和 dry-run P1 卡片都丢失可查的 PID。 | 保持原有 fail-closed 判定，只在 `AccessDenied` 和拒绝/告警文案中保留可用 PID，并写明确认用途或关闭后重试；无 PID 明示未知，不编造。 | 模拟进程属性与迭代器拒绝访问、正式 REFUSED 发生在状态/日志/券商连接之前、dry-run P1 含 PID 和处理方法。 |

## 失败先行、独立复核与 Windows 验证

- 新测试最初 40 项在**第十二批生产代码**上运行：**40 failed / 0 passed**。随后加 8 项边界及审计回归；其中冻结超长订单、对账大数字在实施草案上先实际失败，再修复。H5、未知方向/意图等其余补充项按加入时的草案验证，未冒称全部在原始第十二批上运行。
- 第一次全量诊断为 **425 passed / 14 failed**：14 项均为旧测试对第十二批泛化中文标题的展示断言。测试 Agent 仅迁移 12 个旧测试文件的展示断言，保留原有测试数量、英文日志断言、页数节奏和交易安全断言。迁移后的诊断为 **439/439 passed**；最终边界测试加入后诊断为 **443/443 passed**。这些诊断不计入交付的连续三轮。
- 独立 Agent 逐项复核 28 家族/32 变体、冻结顺序、极端长度、PID 提示和文件变更范围；未发现阻断项。源码编译通过，最终聚焦测试 **251/251 passed**。
- **最终 Windows 全量测试连续三轮，均在文件冻结后串行执行：**

| 轮次 | pytest 汇总 | JUnit（`%TEMP%` 下） | JUnit tests / failed / errors / skipped |
|---|---|---|---|
| 1 | 443 passed / 50.82 秒 | `v6lite_round13_full_1.xml` | 443 / 0 / 0 / 0 |
| 2 | 443 passed / 51.34 秒 | `v6lite_round13_full_2.xml` | 443 / 0 / 0 / 0 |
| 3 | 443 passed / 51.03 秒 | `v6lite_round13_full_3.xml` | 443 / 0 / 0 / 0 |

命令使用 Windows `venv_v6\Scripts\python.exe -m pytest v6lite/tests -q -p no:cacheprovider`，启用 UTF-8、TEMP pycache，并逐轮输出 JUnit；未连 Gateway。最终 `code_sha` 在测试前后复算一致。

## 全部 P0/P1 卡片实际渲染样例

以下 32 个安全合成事件来自 `v6lite/tests/test_batch13.py` 的家族案例，经最终 `_page_card(...).as_plain_text()` 生成；是真实渲染输出，不是手写示意，不含真实账户、URL 或凭证。

### 01 · broker anomaly — `broker data anomaly execution_correction:EX-7`

```text
🟠 P1 · 券商数据事件异常
发生了什么：券商数据异常代码 execution_correction:EX-7。 标的 AAPL。
影响：券商证据需要人工核实；新入场暂停。
你要做什么：1）在 TWS 核对相关成交/订单；2）本机运行 status；3）核实后运行 clear-anomaly execution_correction:EX-7。
```

### 02 · fill anomaly — `fill anomaly duplicate_open_position:AAPL`

```text
🟠 P1 · 成交入账异常
发生了什么：成交异常代码 duplicate_open_position:AAPL。
影响：新入场暂停，账本成交需与券商核对。
你要做什么：1）在 TWS 核对成交和持仓；2）本机运行 status；3）核实后运行 clear-anomaly duplicate_open_position:AAPL。
```

### 03 · late exit fill — `late exit fill AAPL`

```text
🟠 P1 · 退出成交迟到
发生了什么：AAPL STOP 订单 v6l-late01 已结算后又成交 3 股。 已请求撤销重试单：v6l-retry02。
影响：可能超卖或形成空头；新入场暂停。
你要做什么：1）在 TWS 核对券商持仓及所有在途 SELL；2）确认/撤销冲突订单；3）若已形成空头，人工处理并核对本机 status。
```

### 04 · disconnect — `IBKR disconnected`

```text
🟠 P1 · IBKR 连接断开
发生了什么：IBKR 与 Gateway/券商连接已断开。
影响：券商状态无法验证；新入场暂停，退出需待恢复及对账。
你要做什么：1）检查 Gateway 登录和 API 连接；2）在本机运行 status；3）确认重连和对账结果。
```

### 05 · approval — `live approval no longer valid`

```text
🟠 P1 · 实盘审批失效
发生了什么：实盘运行审批无效或过期。
影响：新入场暂停；退出继续。
你要做什么：1）本机运行 status 核实原因；2）完成审批复核；3）在本机重新运行 approve-live。
```

### 06 · heartbeat — `IB Gateway not responding`

```text
🟠 P1 · IB Gateway 心跳无应答
发生了什么：Gateway 当前时间心跳连续 2 次超过 30 秒。
影响：Gateway 响应无法证实；行情与账户数据可能失联。
你要做什么：1）检查 Gateway 登录及 API 响应；2）本机运行 status；3）等待新的有效心跳解除告警。
```

### 07 · old V6 — `旧 V6 运行隔离需复核`

```text
🟠 P1 · 旧 V6 隔离状态异常
发生了什么：旧 V6 进程运行或无法核实（PID 1234）。
影响：账户隔离无法确认；正式运行拒绝启动，dry-run 不作为放行依据。
你要做什么：1）确认 PID 1234 是什么进程；2）关闭旧 V6 或该进程后重试；3）本机运行 status 核对。
```

### 08 · combo — `unresolved combo order working`

```text
🟠 P1 · 组合挂单阻断退出
发生了什么：组合挂单仍在途：v6l-combo01。
影响：所有 Lite 卖出、止损、release 与新入场受阻。
你要做什么：1）在 TWS 核对并撤销/确认组合单 v6l-combo01；2）确认无冲突挂单；3）本机运行 status 核对。
```

### 09 · held — `held order is working AAPL`

```text
🟠 P1 · 保留订单仍在途
发生了什么：AAPL TP1 保留订单 v6l-46b1 仍在途，剩余 6 股。
影响：这些股数已预留；重复卖出可能超卖。
你要做什么：1）在 TWS 核对订单 v6l-46b1 及成交；2）先确认/撤销在途单，再人工处理持仓；3）本机运行 status。
```

### 10 · orphan — `orphan V6-Lite order AAPL`

```text
🟠 P1 · 账本外 Lite 订单在途
发生了什么：AAPL 订单 v6l-orphan01 不在本账本，剩余 4 股；异常代码 orphan_order:v6l-orphan01。
影响：订单可能继续成交；新入场暂停。
你要做什么：1）在 TWS 核对并撤销订单 v6l-orphan01，或恢复正确状态目录；2）确认不再在途；3）本机运行 clear-anomaly orphan_order:v6l-orphan01。
```

### 11 · recovery — `recovery failed`

```text
🟠 P1 · 恢复对账失败
发生了什么：重启/重连后，挂单与成交证据查询失败。
影响：无法安全确认订单状态；新入场暂停。
你要做什么：1）在 TWS 核对在途订单、成交和持仓；2）检查 Gateway 连接；3）本机运行 status 并复核恢复结果。
```

### 12 · prior session — `unverifiable order from a prior session v6l-prior01`

```text
🟠 P1 · 跨交易日订单无法核实
发生了什么：AAPL TP1 订单 v6l-prior01 原定 10 股，今日证据已成交 4 股，历史成交仍需核对。
影响：订单保持人工保留；新入场暂停。
你要做什么：1）在 TWS 查订单 v6l-prior01 的跨日成交；2）核实后本机运行 resolve-intent v6l-prior01；3）若持仓仍有差异，先人工平仓，再按条件运行 release。
```

### 13 · order unknown — `order state unknown v6l-unknown01`

```text
🟠 P1 · 订单状态不明
发生了什么：订单状态无法证实：v6l-unknown01。
影响：可能已在券商成交；新入场暂停，不能凭超时重发。
你要做什么：1）在 TWS 核对订单 v6l-unknown01 及成交；2）本机运行 status 查关联意图 ID；3）证据核实后运行 resolve-intent v6l-unknown01。
```

### 14 · submission unknown — `order state unknown AAPL`

```text
🟠 P1 · 提交回执不明
发生了什么：AAPL 下单回执不明：v6l-unknown02，可能涉及 6 股。
影响：可能已在券商成交；新入场暂停，不能凭超时重发。
你要做什么：1）在 TWS 核对订单 v6l-unknown02 及成交；2）本机运行 status 查关联意图 ID；3）证据核实后运行 resolve-intent v6l-unknown02。
```

### 15 · reconcile — `broker/runtime mismatch`

```text
🟠 P1 · 持仓账本与券商不符
发生了什么：对账差异：AAPL 账本 20 股 / 券商 17 股。
影响：受影响标的新入场暂停；差异持续会冻结持仓。
你要做什么：1）本机运行 status，对照 TWS 实际持仓；2）账外持仓核实后用 ignore；3）托管持仓差异持续时先平仓，再按条件 release。
```

### 16 · freeze deferred — `AAPL freeze check deferred`

```text
🟠 P1 · 冻结核查暂缓
发生了什么：AAPL 持仓数量差异持续，券商成交或持仓证据不完整。
影响：该标的订单暂停，暂不能判定冻结或恢复。
你要做什么：1）在 TWS 核对 AAPL 持仓与成交；2）恢复 Gateway 查询；3）本机运行 status，等待完整证据复核。
```

### 17 · release refused — `release refused AAPL`

```text
🟠 P1 · 人工释放被拒
发生了什么：AAPL 的 release 未通过新鲜券商证据检查。券商仍有 3 股。在途订单：v6l-open01。
影响：冻结继续，V6-Lite 不会重新管理该持仓。
你要做什么：1）在 TWS 核对 AAPL 持仓及订单 v6l-open01；2）确认 0 股、无在途单且满足静默期；3）本机重新运行 release AAPL。
```

### 18 · stale position price — `positions without fresh prices`

```text
🟠 P1 · 持仓报价过期
发生了什么：持仓 AAPL、MSFT 没有新鲜价格，止损无法评估。
影响：自动退出保护可能延迟。
你要做什么：1）检查 Gateway 行情权限；2）在 TWS 核对 AAPL、MSFT 的持仓保护；3）恢复报价后运行 status。
```

### 19 · daily loss — `daily loss stop latched`

```text
🟠 P1 · 当日亏损门槛触发
发生了什么：当日亏损 160.00，达到门槛 150.00。
影响：本交易日暂停新入场；退出继续，锁定到下一交易日。
你要做什么：1）本机运行 status；2）核对 report 和 TWS 成交；3）下一交易日再评估。
```

### 20 · realtime quotes — `no real-time market data`

```text
🟠 P1 · 实时行情订阅异常
发生了什么：实时行情拒绝：AAPL；仅延迟/冻结行情：MSFT。
影响：受影响标的无法取得合格实时报价；入场暂停，按价退出可能延迟。
你要做什么：1）检查 Gateway API 行情权限；2）核对实时订阅及 Paper 共享；3）重新登录并在本机运行 status。
```

### 21 · delayed quotes — `no delayed market data`

```text
🟠 P1 · 延迟行情订阅异常
发生了什么：延迟可用报价 1/3；无订阅 AAPL；不可用 AAPL、MSFT。
影响：无有效价格的标的不能入场；演练扫描/退出检查可能暂停。
你要做什么：1）检查 Gateway 延迟行情 API 权限；2）核对不可用标的；3）恢复后本机运行 status。
```

### 22 · stop retry exhausted — `AAPL STOP NOT FILLED`

```text
🔴 P0 · 止损重试耗尽
发生了什么：AAPL STOP 止损未完成，仍有 20 股未卖。
影响：自动退出重试耗尽；未卖股数仍暴露于市场风险。
你要做什么：1）在 TWS 核对 AAPL 持仓和在途卖单；2）确认无冲突订单后人工设止损或平仓；3）本机运行 status。
```

### 23 · take profit retry exhausted — `AAPL TP1 NOT FILLED`

```text
🟠 P1 · 止盈重试耗尽
发生了什么：AAPL TP1 退出未完成，仍有 6 股未卖。
影响：自动退出重试耗尽；未卖股数仍暴露于市场风险。
你要做什么：1）在 TWS 核对 AAPL 持仓和在途卖单；2）确认无冲突订单后人工处理剩余仓位；3）本机运行 status。
```

### 24 · stop blocked — `exit blocked AAPL STOP`

```text
🔴 P0 · 止损下单被拦截
发生了什么：AAPL STOP 止损订单未发出。券商 17 股 / 账本 20 股。
影响：持仓缺少本次自动退出保护；证据不足时禁止猜测数量。
你要做什么：1）在 TWS 核对 AAPL 持仓和在途单；2）确认冲突单后人工设止损或平仓；3）本机运行 status。
```

### 25 · take profit blocked — `exit blocked AAPL TP1`

```text
🟠 P1 · 退出下单被拦截
发生了什么：AAPL TP1 退出订单未发出。需卖 6 股，可卖 2 股。在途卖单：v6l-work01。
影响：持仓缺少本次自动退出保护；证据不足时禁止猜测数量。
你要做什么：1）在 TWS 核对 AAPL 持仓和在途单 v6l-work01；2）确认冲突单后人工处理持仓；3）本机运行 status。
```

### 26 · stop unsent — `exit refused AAPL STOP`

```text
🔴 P0 · 止损未发出
发生了什么：AAPL STOP 止损未发出：需卖 20 股，可卖 0 股。在途卖单：v6l-work01。
影响：持仓没有本次自动退出保护；贸然重卖可能超卖。
你要做什么：1）在 TWS 核对 AAPL 持仓与在途卖单 v6l-work01；2）确认可卖股数后，必要时人工设止损；3）本机运行 status。
```

### 27 · take profit unsent — `exit refused AAPL TP1`

```text
🟠 P1 · 退出订单未发出
发生了什么：AAPL TP1 退出订单未发出：需卖 6 股，可卖 0 股。在途卖单：v6l-work01。
影响：持仓没有本次自动退出保护；贸然重卖可能超卖。
你要做什么：1）在 TWS 核对 AAPL 持仓与在途卖单 v6l-work01；2）确认可卖股数后，必要时人工处理退出；3）本机运行 status。
```

### 28 · stop reduced — `AAPL STOP reduced to 4`

```text
🟠 P1 · 止损数量缩减
发生了什么：AAPL STOP 原需卖 10 股，可卖 4 股；在途卖单覆盖 6 股。在途订单：v6l-work01。
影响：止损单只能覆盖可卖股数；重复卖出可能超卖。
你要做什么：1）在 TWS 核对 AAPL 持仓及在途卖单 v6l-work01；2）确认已预留股数的保护范围；3）必要时人工补足未保护部分。
```

### 29 · exit rejected — `exit rejected AAPL TP1`

```text
🟠 P1 · 券商拒绝退出订单
发生了什么：AAPL TP1 退出单被券商拒绝。订单 v6l-exit01。数量 6 股。价格 190.25。
影响：该次退出没有生效；持仓可能缺少保护。
你要做什么：1）在 TWS 核对 AAPL 拒单 v6l-exit01及持仓；2）确认无冲突在途单；3）必要时人工设保护。
```

### 30 · shutdown query unknown — `shutdown: working orders unknown`

```text
🟠 P1 · 停机时挂单状态不可查
发生了什么：停机时无法读取券商挂单列表。
影响：不能证明 Lite 订单已撤销，停机后仍可能成交。
你要做什么：1）在 TWS 查看全部 v6l- 在途订单；2）确认或撤销每一笔；3）核对成交与持仓后再考虑重启。
```

### 31 · shutdown unresolved — `shutdown with unresolved orders`

```text
🟠 P1 · 停机后仍有未结订单
发生了什么：券商仍在途：v6l-work01；账本未结：v6l-ledger01。
影响：订单可能在 Lite 停止后成交，数量会改变。
你要做什么：1）在 TWS 核对/撤销在途单 v6l-work01；2）核对账本未结单 v6l-ledger01与成交；3）本机运行 status 查看其余编号。
```

### 32 · freeze — `V6-Lite no longer manages AAPL`

```text
🔴 P0 · V6-Lite 停止管理持仓
发生了什么：AAPL 已冻结；V6-Lite 不再管理。账本 20 股 / 券商 17 股。自有单已请求撤销、结果未确认：v6l-own01。另有未撤销且可能成交的订单：v6l-46b1 卖 6 股、v6l-orphan01 卖 4 股。
影响：自动止损不再发送；未撤销订单仍可能成交，卖单可能导致超卖。
你要做什么：1）在 TWS 确认或撤销仍在途的 AAPL 订单（这些订单未撤销）：v6l-46b1 卖 6 股、v6l-orphan01 卖 4 股；并确认自有单撤销结果；2）确认 AAPL 没有任何在途订单之后，再人工保护或平仓；3）券商显示 0 股且无在途订单后，在本机运行 release AAPL。
```

## 边界与切换说明

Discord 的 600/1500 字符上限使任意长度输入无法全部展示：例如五项对账每项含两个 150 位数字时，卡片只容纳前三项**完整**记录并显示“另有 2 项完整数字见本机日志”，不截断半个数字；本地英文原文完整保留。正常股份数量远小于该压力输入。操作员仍须先核对 TWS 真实订单/持仓，卡片不会替代券商证据。

第十二批 `code_sha=b26bf09a03e41bb5` 继续用于 G2-D；本批在 Claude 复核后于两个交易日之间切换，用于进入 G3 前。真实 Gateway 行为本批未验证；L7 券商端保护单仍未实现，不据此放行 LIVE。

## 最终 `code_sha` 与 SHA-256 清单

`v6lite/version.py` 对包内非 `__pycache__` 文件按相对路径排序，以“路径 UTF-8、NUL、原始字节、NUL”纳入 SHA-256，再取前 16 位。本批 `code_sha=d4b19f1d43b25448`。以下包括 `v6lite/` 全部 56 个文件和未改的运行手册，共 57 项；报告自身 SHA-256 在交付摘要单列，避免自引用。

```text
17b8628794cf5cf5e592be52009a9dbf07ff369673074d69c36b4a91103f2cec  v6lite/README.md
26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39  v6lite/__init__.py
cc2d201e70f7bc28852cb980d906ff8fbbe426681e0760295c620106eae88549  v6lite/__main__.py
d592e1057b2ac4f0dca85574ab26498089a26cdb29a3f24051a0bbc1786bf84c  v6lite/alerts.py
ad2ac3f67724896e2b21e83e595a10b7984630fd2a77f8df44aa859569cb945b  v6lite/approval.py
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
5b875c8df97861e22427223e7065a74bddf04a5da1c7115b431871369d177c47  v6lite/tests/test_batch10.py
fb7f26a0bd1969400f2d769a0d65f4c7d3a18f0162332fd46ca9f1ed99c260f8  v6lite/tests/test_batch11.py
3dfc69a4f811549cb32c9c186bbfed3d24f7c1c956c3c743d9b28f6996037cfc  v6lite/tests/test_batch12.py
698c6ba35df738b5b57cfc7d4b244ce0c07040070ca612147f87093ca363cc31  v6lite/tests/test_batch12_v2.py
12a3ed033079f129e7e70f610be004c498ea6f6f0283473f60b2eb80ad819140  v6lite/tests/test_batch13.py
bb8967a536a8d187fc9f1d742be17c0b4b90e193ab52731ac06d6bff8a08f461  v6lite/tests/test_engine.py
cacaf5b3115d4a298fe8b3fe065d66fe70277b8ee4cd5d9643f0e2fff0ff5f9d  v6lite/tests/test_g2_day1.py
fe84ef645d99f421c64f670aa7f883d16b5a24c8c15b4dd30a4aa8f035b5ea06  v6lite/tests/test_g2_day1_selfreview.py
a0db6b981992a3cf20d9f959a679c608842f4619007331bae9b26908cee3ccb7  v6lite/tests/test_g2_day2_prep.py
ba38215d7a6946d60884d0e3dac99b641e7bcde058c4893b94cb9d62b6a6fe18  v6lite/tests/test_g2_probe.py
6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a  v6lite/tests/test_ibkr_adapter.py
9d8e912ca50fbe2dae6fa855a87773bfa0fa753d2743106abadf6b0c53da9da8  v6lite/tests/test_review_fixes.py
3f1b96a33996c29e4a9ccec7a30b2b39cec812fe54cb63f6d432fda3238ab519  v6lite/tests/test_review_round2.py
b5bf4e5c513e9d2310dc6404f147b633790cff0e894546e9176d6c26390a7427  v6lite/tests/test_review_round3.py
f17f50380fd11f5ccf2e3ae250f8910928406a75d41eda87a9e8b0cfa24d16dd  v6lite/tests/test_review_round4.py
8cf2bba5d54e2e644bc016e68abfe87f0ecbe26b6e54d5a44a329c61e6dc7da8  v6lite/tests/test_review_round4_selfreview.py
3e98f52cdb35e572995448fc6e3468cf1d86e2d35ac801a33f8c5cacc67e9680  v6lite/tests/test_review_round5.py
21d218304e6b76b03348ba01033705d412bbbb4d3cc62263c6ed2ed643452ccd  v6lite/tests/test_review_round5_selfreview.py
c6eabe6e953cc75ccfa7e4fa87625a86d677ef5dd2fca9975cd101b3403d6be5  v6lite/tests/test_review_round6.py
bcb2baea638b6ba6c063aba7776aaf1137c3cd1df98297a08311cdaf899994a5  v6lite/tests/test_review_round7.py
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
