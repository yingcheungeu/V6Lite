# 27b 独立复核补充反例

首候选35项新增及850项聚焦均通过后，独立复核发现宽历史兼容分支忽略明确的已知身份矛盾：

1. 普通无time客户端查询中的历史ENTRY带不同的非空intent；
2. 同一历史执行带明确不同的正conId；
3. 今日TP1的普通客户端回报带不同正conId，但专用scoped账户查询同exec保持真身份。

第三独立spec `test_batch27b_review_edges.py` 的SHA-256为 `6f87a5a6b384b887324f0bbf76a044da4bdefcf36d93aeb07d759811ea80dc22`。它检查真实SDK转换后的FillEvent含矛盾、真实无过滤请求与专用过滤请求均发送、scoped结果完整且历史排除/今日真身份分别成立，然后才检查P1冻结、无写和保护数量；不是夹具未进入目标路径的失败。

ROOT先验证首候选8生产文件字节未变，再执行 `review_edges_red_candidate`：3 tests / 3 failures / 0 errors，三例均在缺少应有P1处失败。首候选包SHA从631670c0bf734c0b变为b74b033237942d1c，仅因为补入第三测试文件，不是生产被提前修复。

唯一实施Agent只在overnight_evidence.py的历史跳过/日期分类之前补上已知exec的明确非空intent冲突与正conId冲突检查；当前day_only也走相同检查。没有自行将query conId=None升级为人类未裁定的新硬门槛。

`green_review_fixed`：38 tests / 38 passed / 0 failed/errors；code_sha=f34b0e648a47f523。原两锁spec保持不变。后续全量以最终锁候选为准，不能拿此聚焦绿代替三次全量。

RESTORE_PENDING旧total回报目前只是静态交互点，未凭假设声称现场已复现或修复。

## 实际d计数后续

第四独立spec先在生产未变的计数候选执行 `review_count_red_candidate`：3 tests / 3 failures / 0 errors。三例均通过实际完整scope、真实0/1条数、P1、保护/账本只读后，才在卡片“未完成”处失败。唯一实施Agent将完整新鲜专用查询的实际同标的unique execId计数提前至身份冲突拒绝之前，不将失败或不完整查询造0。`green_final_candidate` 41/41通过，code_sha=5808b53c5581c6a7；原先四个spec冻结。

## 首次TP前专用查询完整性

独立复核发现，仅有历史入场、尚无今天TP1标记时，a不需要今日例外，但明确不完整专用scoped查询仍会退入legacy路径并授权减量。第五独立spec test_batch27b_scope_completeness.py（SHA256 be5f81d56679f2a99d37cad217bf014dcf658cab8dbbf07e70266fc7b0e77faa）验证真实missing-End/list-only和带匹配End但其他client坏原生时间的完整性失败；普通三项证据保持健康，无today成交。另有旧接口不具备可选scoped方法时的真实TP正向控制。

ROOT先确认overnight_evidence.py仍为b0dc3c7d56922367156f771000abeb01ea5e748b65134977f226f6f9751326f7，再串行执行 `review_scope_red_candidate`：4 tests / 3 failures / 0 errors、legacy正向1 passed。包含第五spec后包code_sha=a9d29a683de12149；尚未修改生产。三条红均通过真实原生故障和普通健康查询前置，才在应有P1缺失处失败。不是把查询错误升级成不相关P0的夹具。

唯一实施Agent最小修复只在overnight_evidence.py记录是否选择专用scoped能力，并在授权前要求其完整、新鲜、同代、显式当日UTC证明；失败时现有P1冻结，不降级legacy。旧接口无该能力时兼容不变。最终源码SHA=e51fc4146417b234cde85547ee3057a104577d7d3e3941258a18c7b5dc5eb016。`samples_final_scoped_candidate` 45 passed；`focused_final_scoped_candidate` 860 passed，均0 failed/errors且源码/spec不变；code_sha=d6dd37c12701ce95。实际卡片/日报/INFO汇总及0/0/1计数见visible_card_checks.json。五份新增spec到最终验收均冻结，原35项锁spec未改。
