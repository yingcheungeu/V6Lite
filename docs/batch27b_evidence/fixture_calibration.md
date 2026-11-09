# 27b 红测夹具与有效性记录

- 首轮 `red_batch27a_initial`：34 tests，27 failures / 0 errors；其中原始时间三类、汇总和原生time过滤等行为断言已自然失败。
- 端到端多数用例却在 `_activate` 尚未提交 TP1 时解包失败，不能作为“TP1 后保本/TP2 被时间标记冻结”的有效红。
- `samples_red_positive_diagnostic` 实际 MemoryChannel 卡片证明：历史 a 已在 10/20 恢复，随后保护减量确认时模拟报价超过3秒门控，触发 P1“止盈单未发出，正在恢复保护”。原生请求 pacing 会推进 FixedClock，而夹具只在 Rig.advance 更新报价，遗漏了持续行情流。
- 校准边界：仅在新 E2E 夹具的原生 sleep 推进时模拟报价刷新；保持实际 pacing、3秒新鲜度门控和容量/账本断言。原生产和旧测试不变。
- 同时纠正原新夹具对所有请求预先按今天过滤的问题：服务器保留历史成交，仅实际time过滤请求按其UTC下界筛选；普通旧查询可回传历史。不能用夹具预过滤掩盖普通 client 的无范围语义。
- d 卡片计数测试需使 a 已通过且 d 确实因 unknown execution 拒绝；不把先被 a 拦截当作独立 d 文字复现。
- 原始收据及锁定源码SHA保留，发布副本统一脱敏。最初一次 diagnostic 命令误把 ::nodeid 当文件路径，runner在测试启动前拒绝，无测试收据；改用测试文件后成功采集，未修改代码绕过。

最终有效红、最终spec哈希及绿测结果将在报告引用真实收据，不能用以上未进入目标路径的失败代替。

第二轮 `red_batch27a_calibrated` 虽已修报价，却因服务器统一返回扩展历史，仍在TP1之前的d拒绝；它证明相关宽历史问题，但不冒充TP1后主目标红。

最终采用显式TWS服务器默认midnight模型，同时增加Trade Log7天回溯正向变体；真实wire time过滤优先于两种服务器默认，方法名不授予scope。d文字场景先使a通过，再注入3笔无法归属的今日成交；负向专用scope失败不故意破坏原核心三证，保持真正F4故障的原P0语义。

合格首次生产实施前红：red_batch27a_locked_initial，35项，11 failures / 24 passed / 0 errors。主正向确已完成TP1 callback入账，在BE pending is not None处自然失败；原始时间三类各在真实审计缺raw_time处失败，d计数确因unknown-0归属失败后仍无3条显示。证据及不变spec哈希见qualified_red.json。
