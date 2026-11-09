# Batch 25 actual Alerter card samples

Source SHA: ea71abe782e4cf68

## 同一原因两次重连后停止；动态编号不重置预算；300秒后不自动解锁

🔴 P0 · 连接反复重建，已停止自动重连
发生了什么：原因：SDK synchronization incomplete; registered End missing: openOrders [generation=2 PID=9002 reqId=702 elapsed=2.125s attempt=2]；同一原因在 5 分钟内已自动重连 2 次，本次候选未再发起连接；已停止自动重连。
影响：全部入场保持阻断；当前券商端保护状态需人工核实，不能将旧缓存当作有效保护。
你要做什么：1. 按 Ctrl+C 停止 V6-Lite，等待正常 SHUTDOWN 完成；2. 在 TWS 人工核对持仓、原 GTC 保护单和全部工作单，必要时人工处置风险；3. 排除连接原因后再由 John 重启，完整核验持仓、工作单、成交三项证据后恢复。

## 同步完成后真正核验失败，重复查询只发一张P1

🟠 P1 · 券商查询已限速 · AAPL
发生了什么：同一持仓核验未通过，查询按 1、2、4、8、16、30 秒退避；等待期间不提供新的完整证据，原 UNKNOWN、人工核验 hold 和冻结继续保留。
影响：本持仓继续等待核验；其他持仓和连接心跳继续运行。
你要做什么：在 TWS 核对原保护单与持仓；保持冻结，不增加卖单或绕过核验。
