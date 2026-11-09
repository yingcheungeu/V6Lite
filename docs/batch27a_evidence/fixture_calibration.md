# 27a 独立测试夹具校准与失败收据说明

全部原始收据留在本机；发布副本统一脱敏，映射保留原始字节 SHA-256，不冒称改过的副本就是原字节。

- red_time_b26_initial：首组成交时间自然红，29 项，13 失败；测试没有缺 API/import 错误。
- red_final_specs_b26：扩充后 66 项，35 失败。其中 conId 用例引用了 seed fixture 已关闭 Store、外部手工单通过受监控 Lite submit 构造、平仓卡片筛选漏掉中文标题，属于夹具错误，不计作产品回归。
- red_calibrated_specs_b26：校准后 67 项，32 失败；三类假红消除。
- green_first_candidate：67 项，8 失败均发生在 callback fixture 初始断言；真实保护查询已在 drain 中正确核实标记，夹具却还要求标记存在。校准为首个查询明确 uncertain、随后才提供确定时间；另加允许首次自然查询当场成功的正向。没有阻止生产的正确核时路径。
- red_query_timing_specs_b26：68 项，33 自然失败；green_query_timing_candidate 仍有4项首次主动查询未调度，是真实调度问题，由唯一实施 Agent 修复。green_scheduler_candidate 68 全绿。
- red_review_edges_candidate：18 项，11 失败；其中重复 WORKING 稳态查询 [14,29] 采用了比真实 confirmed_at 晚1秒的开始锚点，属于夹具基准错误，改为从真实 confirmed_at 起算，保持 [15,30] 且相邻15秒断言；其余身份类型、矛盾回报、OPEN/CLOSED resume 事件边界均按真实失败处理。
- 启动说明错误保证“原 STP 继续有效”由独立静态复核发现，实施者在新增守卫运行之前已修；报告只写静态发现→修正文案→守卫验证，不声称修前自然红。
- 外部 actor 的 S+X>P 是人为构造的危险初态，不声明该初态满足 Lite 不变量。Lite 新增卖单仍保留原强容量 oracle；既有保护只可减量/取消，减量目标 S+X≤P，ACK后原 oracle 继续核对。
- 所有对原测试的原始哈希都与 B26 相同，没有把旧强断言改成较弱条件，也没有隐藏/重试掉产品失败。
