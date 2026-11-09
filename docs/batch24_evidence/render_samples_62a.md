# Batch 24 actual Alerter card samples

Source SHA: 62a9b82bb52ec22c

## P1 broker query retry pacing

🟠 P1 · 券商查询已限速 · AAPL
发生了什么：同一持仓核验未通过，查询按 1、2、4、8、16、30 秒退避；等待期间不提供新的完整证据，原 UNKNOWN、人工核验 hold 和冻结继续保留。
影响：本持仓继续等待核验；其他持仓和连接心跳继续运行。
你要做什么：在 TWS 核对原保护单与持仓；保持冻结，不增加卖单或绕过核验。

## P1 working price with cap 119.51

🟠 P1 · 券商工作价格不一致 · AAPL
发生了什么：orderRef=v6l-b2782d21c7764b4a8b98 发送价=119.41 工作价=119.41 cap=119.51
影响：该标的自动重发已阻止；原账本价格保留，STP 恢复继续独立核验。
你要做什么：1）核对 TWS 原订单价格；2）由 John 处置，禁止自动跟随 cap 改价。

## P0 TP price rejection: P=6 S=4 unprotected=2

🔴 P0 · 止盈价格被券商拒绝 · AAPL
发生了什么：持仓 P=6 股 已保护 S=4 股 未保护=2 股；AAPL TP数量=2 股；发送限价=119.41；bid=119.64 ask=119.7 行情年龄=0.0s；orderRef=v6l-84bbcd56955b42cd9fe7 orderId=1003 permId=51003 broker code=201；自动止盈暂停 position=pos-521c47e6447e42c982d4 stage=TP1 pause_id=tp-pause-7b0f68477ca64558a2d2；保护恢复仍需完整三项证据。
券商原因原文 1/1：Order price is outside the allowed price range; B24 rendering synthetic reason
影响：本持仓自动止盈已暂停；券商保护与可用卖出数量仍须核验。
你要做什么：1）核对 TWS 持仓和工作卖单；2）确认 STP 已恢复到全部持仓；3）仅在原强核验门槛满足后由 John 执行 resume-tp AAPL --position-id pos-521c47e6447e42c982d4 --pause-id tp-pause-7b0f68477ca64558a2d2 --operator John --reason 明确核验原因。没有合法 STP 完整证据时保持暂停。不要自动改价重发或点击 IBALGO。

## TWS 7497 startup connected

🟢 V6-Lite 已启动（Paper 真实下单（Paper） · 实时行情）
启动时间：2026-10-06 06:00 PT / 09:00 ET
模式：Paper · 真实下单（Paper） · 实时行情 · DU0000000 · 版本 62a9b82b
状态：✅ 正常
IBKR 已连接 · 对账一致 · 告警通道正常 · 旧 V6：未运行
入场窗口 10:00 ET 开启
报价可用 3/3 · V6-Lite 持仓：无
监控：IBKR 心跳：每 60 秒，连续 2 次无应答即告警
持仓漂移：每 300 秒对账，不一致即告警
每日健康报告：13:05 PT（收盘后 5 分钟）
扫描时间：10:00 ET / 07:00 PT
手动指令（本机命令行）：这些指令只能在本机命令行执行，不能在 Discord 输入。
status 查看状态 · report 查看日报
kill --reason 停止入场 · resume 恢复
ignore 标的 · release 标的 · resolve-intent ID · clear-anomaly 代码

## TWS 7497 heartbeat missing

🟠 P1 · IBKR 心跳无应答
发生了什么：IBKR 当前时间心跳连续 2 次超过 30 秒。
影响：IBKR 响应无法证实；行情与账户数据可能失联。
你要做什么：1）检查 IBKR 登录及 API 响应；2）本机运行 status；3）等待新的有效心跳解除告警。

## TWS 7497 heartbeat recovered

🟢 IBKR 已恢复应答
发生了什么：IBKR 已重新回应当前时间心跳。
影响：心跳告警解除；其他交易门控仍按实时状态判断。
你要做什么：在本机运行 status 并核对 IBKR 状态。

## TWS 7497 daily health: IBKR heartbeat label

✅ 正常 · 演练（不下单） · V6-Lite 每日健康报告 2026-10-06
扫描：计划：10:00 ET 信号 1 · 已入场 无
信号：10:00 ET AAPL(78)
结果：WOULD_BUY AAPL gate=ok
下单/撤单：0 次下单 · 0 次撤单
持仓与漂移：无 · 漂移：无
券商端保护：无持仓；空窗统计 A=0.000s B=0.000s
IBKR 心跳：本进程今日截至报告时: 应答 2 · 最长已验证延迟 0 秒 · 未应答 0
告警送达：本进程今日截至报告时: 发出 2 · 失败 0
行情可用率：100% 常规交易时段可用率 · 已订阅 3 · 6/6 标的样本 · 2 次采样 (本进程今日；实时行情)
code_sha：62a9b82bb52ec22c
