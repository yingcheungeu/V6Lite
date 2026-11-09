# 补充指令 A：原生字段能力与已裁定边界

2026-10-29 PT。旧13:31续办已按用户要求暂停；5bcb047f源码/工具/报告已完整备份，旧冻结撤出执行入口。补充A哈希9f054900e0fe9843904685696dce0017c2a68c09c8538463739e312b6cd516ce已核对。

当前ib_insync 0.9.86的decoder.completedOrder（decoder.py:1073–1287）从新Order()开始，解码account、conId、permId、orderRef、status及filledQuantity，未解码orderId/clientId，二者保留Order默认0。AST字段清单与源码hash见addendum_A_capability.json。wrapper.completedOrder每次新建Trade，返回值不合并旧缓存；orderStatus.filled=0为默认值，不是原生回报。累计成交必须读取order.filledQuantity，拒绝缺失、UNSET、NaN/Inf、负值、分数及与账本不匹配。

因此，严格要求五项都来自本次completed回报时，原生orderId缺失必须继续拒绝。不能在测试中手工给Order(orderId=2890)后冒充真实decoder可用，也不能把数据库oid回填再宣称native字段齐全。真实Paper账户是否返回该条completed记录尚未观察；本任务禁止连接券商。

用户已直接裁定：允许四项本轮原生身份＋此前强确认且已持久化orderId绑定，并附加七项必过条件；原话语义记录在scope_resolution.json。当前按此契约实现，不再等待授权；未将本地orderId回填原生值。

其余可实施设计：completed走独立固定键请求/End的旧轮债务守卫及现有wire pacing，只读不可变结果；只针对缺在线CANCEL_SEEN的保护intent查询；已完成数量大于账本先正常client执行刷新/入账，绝不从aggregate数量制造fill；外部SELL100/BUY98只作账户签名，不计Lite成交。所有证明暂存到两轮flat/无工作单/执行签名稳定、stream flat、quiet及最新本地绑定/hold检查通过后，才在Store最终release事务写PROTECT_CANCEL_PROVEN_COMPLETED，不提前写CANCEL_SEEN。

开盘裁定已收到：tp_start_after_open_seconds=30（0关闭）获同意，第三十三批实施；本批仍不实施。此决定只改变方案审批状态，不授权当前修改止损、进行中的撤单恢复或定价。

SDK能力参考：[IBKR completed orders官方文档](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/completed-orders)。本地安装的decoder/wrapper实际字段优先；文档不能证明本Paper账户已返回该条记录。
