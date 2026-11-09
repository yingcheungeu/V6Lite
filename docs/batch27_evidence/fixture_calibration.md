# 独立测试夹具校准记录

仅校准新增规格；原有27b的1848个nodeid及既有测试文件不变。初版源文件按原字节保留在initial_specs/，全部原始失败收据保留。

## 首候选69项

收据：candidate_initial_69，code_sha=b48c659be8159ae7，63 passed、6 failed、0 errors。完整四个13小时压力循环已执行。

- 四个压力用例在最后断言错误地比较confirmed_at与停机后时钟；真实停机查询使模拟时钟前进2秒。状态应保留最后一次实际确认，不能为测试让停机凭空刷新证明。校准为停机前先严格断行值等于实际最后确认时刻，停机后严格断该值不变。数量/审计次数/汇总次数/原生请求/S+T+X/账本断言不变。
- SDK warning用例在进入最后真实callback时缺少本机ib_insync Wrapper.error必需的第4参数advancedOrderRejectJson。仅补空字符串，仍用真实201原因/原始wrapper，并保留告警及脱敏断言。此TypeError不作为产品缺陷红。
- Events=201是真实产品off-by-one，200行强断言未放宽，由唯一实施者修复。

原三规格校准后再次在隔离b26与27b各运行69项：均53 failed、16 passed、0 errors、0 skipped。更新资格见qualified_red.json；首次生产修改前的双红、virtual-tree证明和原始spec哈希见qualified_red_initial.json。

## 候选原生重置反例清理

candidate_red_reset_isolation主断言确实显示新observer故障会阻止原native计数清零，但TEMP触发器只在所有断言通过后删除，导致失败时fixture teardown再次flush出错（1 failed +1 teardown error）。该收据不作为0errors合格红。

仅在候选专用新测试中用try/finally确保DROP该TEMP trigger；原generation、sent/ended/stuck/poison/owned/nonorder以及旧代End隔离的强断言原样保留。初版reset源码另存initial_specs/。后续合格复跑以candidate_red_edges_and_rulings为准，不覆盖前次收据。

独立只读review已逐字确认上述改动均为夹具校准，没有放宽预算/交易安全断言。作者说明见spec_calibration_2026-10-18.md。

## 原生握手计数前置校准

独立复核进一步发现：red_b26_equivalence、red_b27b_equivalence和candidate_equivalence_before_edges_fix中的owed-End用例，都先因native bootstrap已有positions sent/ended而在前置计数断言失败，未运行到summary计数检查。这些旧收据不能证明候选漏计失败请求；root先前的该结论已纠正。

仅校准新spec为握手后捕获sent/ended基线，后续严格核两次真实请求/两次End的增量、wire两次、原异常对象和drained一次；不清生产计数/债务、不mock完成。原源码保留initial_specs。按总调用统计的实现基于明确规格与静态发现；不逆造候选逼红。校准后重新运行隔离b26/27b红及最终候选绿，报告如实引用对应收据。
