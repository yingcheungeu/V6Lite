# Batch 19 FakeBroker 卡片：六种合法模式的实际投递文本

合成账户、2026-10-06 合成交易日；仅 TEMP FakeBroker + MemoryChannel，未发送 Discord。
每组为实际启动与 _end_of_day 投递，RTH 两次 × 三标的=6/6；不曾模拟提交买单或撤单。

## paper / test=0 / dry_run=1

### 启动

```text
🟢 V6-Lite 已启动（Paper 演练（不下单） · 实时行情）
启动时间：2026-10-06 06:00 PT / 09:00 ET
模式：Paper · 演练（不下单） · 实时行情 · DU0000000 · 版本 6293dd4e
状态：✅ 正常
Gateway 已连接 · 对账一致 · 告警通道正常 · 旧 V6：未运行
入场窗口 10:00 ET 开启
报价可用 3/3 · V6-Lite 持仓：无
监控：IB Gateway 心跳：每 60 秒，连续 2 次无应答即告警
持仓漂移：每 300 秒对账，不一致即告警
每日健康报告：13:05 PT（收盘后 5 分钟）
扫描时间：13:30 ET / 10:30 PT
手动指令（本机命令行）：这些指令只能在本机命令行执行，不能在 Discord 输入。
status 查看状态 · report 查看日报
kill --reason 停止入场 · resume 恢复
ignore 标的 · release 标的 · resolve-intent ID · clear-anomaly 代码
```

### 手机日报

```text
✅ 正常 · 演练（不下单） · V6-Lite 每日健康报告 2026-10-06
扫描：计划：13:30 ET 未运行
信号：无
结果：无 WOULD_BUY；gate=不适用
下单/撤单：0 次下单 · 0 次撤单
持仓与漂移：无 · 漂移：无
券商端保护：无持仓；空窗统计 A=0.000s B=0.000s
Gateway 心跳：本进程今日截至报告时: 应答 4 · 最长已验证延迟 0 秒 · 未应答 0
告警送达：本进程今日截至报告时: 发出 2 · 失败 0
行情可用率：100% 常规交易时段可用率 · 已订阅 3 · 6/6 标的样本 · 2 次采样 (本进程今日；实时行情)
code_sha：6293dd4e2377603c
```

### 本地行情主行

```text
- market data: real-time (1) | 100% RTH availability · subscribed 3 · 6/6 symbol-samples · 2 samples (this process today; LIVE)
```

## paper / test=1 / dry_run=1

### 启动

```text
🧪 TEST V6-Lite 已启动（Paper 测试模式 · 演练（不下单） · 实时行情）
启动时间：2026-10-06 06:00 PT / 09:00 ET
模式：🧪 测试模式（仅 Paper） · 演练（不下单） · 实时行情 · DU0000000 · qty=3，stop=0.3 ATR，TP1/2/3=0.3/0.5/0.8 ATR · 版本 6293dd4e
状态：✅ 正常
Gateway 已连接 · 对账一致 · 告警通道正常 · 旧 V6：未运行
入场窗口 10:00 ET 开启
报价可用 3/3 · V6-Lite 持仓：无
监控：IB Gateway 心跳：每 60 秒，连续 2 次无应答即告警
持仓漂移：每 300 秒对账，不一致即告警
每日健康报告：13:05 PT（收盘后 5 分钟）
扫描时间：13:30 ET / 10:30 PT
手动指令（本机命令行）：这些指令只能在本机命令行执行，不能在 Discord 输入。
status 查看状态 · report 查看日报
kill --reason 停止入场 · resume 恢复
ignore 标的 · release 标的 · resolve-intent ID · clear-anomaly 代码
test-entry 标的 --operator 姓名（下个 tick 完整门控）
```

### 手机日报

```text
TEST · ✅ 正常 · 演练（不下单） · V6-Lite 每日健康报告 2026-10-06
扫描：计划：13:30 ET 未运行
信号：无
结果：无 WOULD_BUY；gate=不适用
下单/撤单：0 次下单 · 0 次撤单
持仓与漂移：无 · 漂移：无
券商端保护：无持仓；空窗统计 A=0.000s B=0.000s
Gateway 心跳：本进程今日截至报告时: 应答 4 · 最长已验证延迟 0 秒 · 未应答 0
告警送达：本进程今日截至报告时: 发出 2 · 失败 0
行情可用率：100% 常规交易时段可用率 · 已订阅 3 · 6/6 标的样本 · 2 次采样 (本进程今日；实时行情)
code_sha：6293dd4e2377603c test=1
```

### 本地行情主行

```text
- market data: real-time (1) | 100% RTH availability · subscribed 3 · 6/6 symbol-samples · 2 samples (this process today; LIVE)
```

## paper / test=0 / dry_run=0

### 启动

```text
🟢 V6-Lite 已启动（Paper 真实下单（Paper） · 实时行情）
启动时间：2026-10-06 06:00 PT / 09:00 ET
模式：Paper · 真实下单（Paper） · 实时行情 · DU0000000 · 版本 6293dd4e
状态：✅ 正常
Gateway 已连接 · 对账一致 · 告警通道正常 · 旧 V6：未运行
入场窗口 10:00 ET 开启
报价可用 3/3 · V6-Lite 持仓：无
监控：IB Gateway 心跳：每 60 秒，连续 2 次无应答即告警
持仓漂移：每 300 秒对账，不一致即告警
每日健康报告：13:05 PT（收盘后 5 分钟）
扫描时间：13:30 ET / 10:30 PT
手动指令（本机命令行）：这些指令只能在本机命令行执行，不能在 Discord 输入。
status 查看状态 · report 查看日报
kill --reason 停止入场 · resume 恢复
ignore 标的 · release 标的 · resolve-intent ID · clear-anomaly 代码
```

### 手机日报

```text
✅ 正常 · 真实下单（Paper） · V6-Lite 每日健康报告 2026-10-06
扫描：计划：13:30 ET 未运行
信号：无
结果：gate=未持久化（正式订单的具体门控结果请查本机完整日志）
下单/撤单：0 次下单 · 0 次撤单
持仓与漂移：无 · 漂移：无
券商端保护：无持仓；空窗统计 A=0.000s B=0.000s
Gateway 心跳：本进程今日截至报告时: 应答 4 · 最长已验证延迟 0 秒 · 未应答 0
告警送达：本进程今日截至报告时: 发出 2 · 失败 0
行情可用率：100% 常规交易时段可用率 · 已订阅 3 · 6/6 标的样本 · 2 次采样 (本进程今日；实时行情)
code_sha：6293dd4e2377603c
```

### 本地行情主行

```text
- market data: real-time (1) | 100% RTH availability · subscribed 3 · 6/6 symbol-samples · 2 samples (this process today; LIVE)
```

## paper / test=1 / dry_run=0

### 启动

```text
🧪 TEST V6-Lite 已启动（Paper 测试模式 · 真实下单（Paper） · 实时行情）
启动时间：2026-10-06 06:00 PT / 09:00 ET
模式：🧪 测试模式（仅 Paper） · 真实下单（Paper） · 实时行情 · DU0000000 · qty=3，stop=0.3 ATR，TP1/2/3=0.3/0.5/0.8 ATR · 版本 6293dd4e
状态：✅ 正常
Gateway 已连接 · 对账一致 · 告警通道正常 · 旧 V6：未运行
入场窗口 10:00 ET 开启
报价可用 3/3 · V6-Lite 持仓：无
监控：IB Gateway 心跳：每 60 秒，连续 2 次无应答即告警
持仓漂移：每 300 秒对账，不一致即告警
每日健康报告：13:05 PT（收盘后 5 分钟）
扫描时间：13:30 ET / 10:30 PT
手动指令（本机命令行）：这些指令只能在本机命令行执行，不能在 Discord 输入。
status 查看状态 · report 查看日报
kill --reason 停止入场 · resume 恢复
ignore 标的 · release 标的 · resolve-intent ID · clear-anomaly 代码
test-entry 标的 --operator 姓名（下个 tick 完整门控）
```

### 手机日报

```text
TEST · ✅ 正常 · 真实下单（Paper） · V6-Lite 每日健康报告 2026-10-06
扫描：计划：13:30 ET 未运行
信号：无
结果：gate=未持久化（正式订单的具体门控结果请查本机完整日志）
下单/撤单：0 次下单 · 0 次撤单
持仓与漂移：无 · 漂移：无
券商端保护：无持仓；空窗统计 A=0.000s B=0.000s
Gateway 心跳：本进程今日截至报告时: 应答 4 · 最长已验证延迟 0 秒 · 未应答 0
告警送达：本进程今日截至报告时: 发出 2 · 失败 0
行情可用率：100% 常规交易时段可用率 · 已订阅 3 · 6/6 标的样本 · 2 次采样 (本进程今日；实时行情)
code_sha：6293dd4e2377603c test=1
```

### 本地行情主行

```text
- market data: real-time (1) | 100% RTH availability · subscribed 3 · 6/6 symbol-samples · 2 samples (this process today; LIVE)
```

## live / test=0 / dry_run=1

### 启动

```text
🟢 V6-Lite 已启动（Live 演练（不下单） · 实时行情）
启动时间：2026-10-06 06:00 PT / 09:00 ET
模式：Live · 演练（不下单） · 实时行情 · U0000000 · 版本 6293dd4e
状态：✅ 正常
Gateway 已连接 · 对账一致 · 告警通道正常 · 旧 V6：未运行
入场窗口 10:00 ET 开启
报价可用 3/3 · V6-Lite 持仓：无
监控：IB Gateway 心跳：每 60 秒，连续 2 次无应答即告警
持仓漂移：每 300 秒对账，不一致即告警
每日健康报告：13:05 PT（收盘后 5 分钟）
扫描时间：13:30 ET / 10:30 PT
手动指令（本机命令行）：这些指令只能在本机命令行执行，不能在 Discord 输入。
status 查看状态 · report 查看日报
kill --reason 停止入场 · resume 恢复
ignore 标的 · release 标的 · resolve-intent ID · clear-anomaly 代码
approve-live 审批实盘 · revoke-live 撤销审批
```

### 手机日报

```text
✅ 正常 · 演练（不下单） · V6-Lite 每日健康报告 2026-10-06
扫描：计划：13:30 ET 未运行
信号：无
结果：无 WOULD_BUY；gate=不适用
下单/撤单：0 次下单 · 0 次撤单
持仓与漂移：无 · 漂移：无
券商端保护：无持仓；空窗统计 A=0.000s B=0.000s
Gateway 心跳：本进程今日截至报告时: 应答 4 · 最长已验证延迟 0 秒 · 未应答 0
告警送达：本进程今日截至报告时: 发出 2 · 失败 0
行情可用率：100% 常规交易时段可用率 · 已订阅 3 · 6/6 标的样本 · 2 次采样 (本进程今日；实时行情)
code_sha：6293dd4e2377603c
```

### 本地行情主行

```text
- market data: real-time (1) | 100% RTH availability · subscribed 3 · 6/6 symbol-samples · 2 samples (this process today; LIVE)
```

## live / test=0 / dry_run=0

### 启动

```text
🟢 V6-Lite 已启动（Live 真实下单（Live） · 实时行情）
启动时间：2026-10-06 06:00 PT / 09:00 ET
模式：Live · 真实下单（Live） · 实时行情 · U0000000 · 版本 6293dd4e
状态：✅ 正常
Gateway 已连接 · 对账一致 · 告警通道正常 · 旧 V6：未运行
入场窗口 10:00 ET 开启
报价可用 3/3 · V6-Lite 持仓：无
监控：IB Gateway 心跳：每 60 秒，连续 2 次无应答即告警
持仓漂移：每 300 秒对账，不一致即告警
每日健康报告：13:05 PT（收盘后 5 分钟）
扫描时间：13:30 ET / 10:30 PT
手动指令（本机命令行）：这些指令只能在本机命令行执行，不能在 Discord 输入。
status 查看状态 · report 查看日报
kill --reason 停止入场 · resume 恢复
ignore 标的 · release 标的 · resolve-intent ID · clear-anomaly 代码
approve-live 审批实盘 · revoke-live 撤销审批
```

### 手机日报

```text
✅ 正常 · 真实下单（Live） · V6-Lite 每日健康报告 2026-10-06
扫描：计划：13:30 ET 未运行
信号：无
结果：gate=未持久化（正式订单的具体门控结果请查本机完整日志）
下单/撤单：0 次下单 · 0 次撤单
持仓与漂移：无 · 漂移：无
券商端保护：无持仓；空窗统计 A=0.000s B=0.000s
Gateway 心跳：本进程今日截至报告时: 应答 4 · 最长已验证延迟 0 秒 · 未应答 0
告警送达：本进程今日截至报告时: 发出 2 · 失败 0
行情可用率：100% 常规交易时段可用率 · 已订阅 3 · 6/6 标的样本 · 2 次采样 (本进程今日；实时行情)
code_sha：6293dd4e2377603c
```

### 本地行情主行

```text
- market data: real-time (1) | 100% RTH availability · subscribed 3 · 6/6 symbol-samples · 2 samples (this process today; LIVE)
```
