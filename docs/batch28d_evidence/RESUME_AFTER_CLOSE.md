# 28d 收盘后续办

仅操作 C:/Dev/V6Lite-dev 的 dev。基线 v6lite-batch28c / a95f31f764e112bff50b922b43467bb7aeb44530。
指令 docs/V6_LITE_BATCH28D_INSTRUCTIONS.md SHA-256 3ba0dceebe73eab3b0020a76e4214061fc68c98839683cee542c822a30115dd8。

冻结候选 code_sha fb68fbc9c751d214，完整 package SHA-256 fb68fbc9c751d214b9ff40abfe9a3d3cef052d6cc49e1022943136f2c0d13e20。
源码 inventory SHA-256 e7eb42453f3c4bfa66c26673c4375223533a16e9f9ba5e20d39026fac4913be9；完整文件集合见 freeze.json。
最终聚焦 focused_final_2：655 passed，源码不变；179 Python 源码 compile、UTF8 ast.parse、无BOM通过。
前轮 focused_final_1 的653项通过；复核新增恢复反例 red_restore_contradiction_1 两项先失败，修复后 green_restore_contradiction_1 两项通过，再跑当前655项。
已保留所有红测、首轮NameError修复记录、原始输出规范化/脱敏前后SHA与完整XML；没有第三次flake被豁免。
三次本批全量尚未运行，尚未提交或打 tag。

## 时间与串行门

先用真实时钟确认 America/Los_Angeles 已到2026-10-22 13:30之后，且不在任何交易日06:00–13:30。
每次全量前复核时间。激活 ./.venv/Scripts/Activate.ps1，PYTHONUTF8=1，使用 python -B。
核对 dev HEAD、指令、freeze.json 所有源码和验证工具 SHA；若外部修改冻结源码，停止并报告具体差异。
只运行一个测试进程；已有本批验证仍运行就先等结束。不要并行，也不要覆盖任何完整或部分回执。

依次执行并逐次等结束、检查 returncode/source_unchanged/同一inventory/完整XML：

1. python -B docs/batch28d_evidence/v6lite_b28d_validate.py final_full_1 full
2. python -B docs/batch28d_evidence/v6lite_b28d_validate.py final_full_2 full
3. python -B docs/batch28d_evidence/v6lite_b28d_validate.py final_full_3 full

若标签任何产物已存在，用新唯一标签保留旧失败/中断。真回归先修、聚焦红绿、重新冻结并从新的第1次开始连续三次；不重试掩盖时序flake。
同一测试第三次flake必须修复或用户明确重定义门槛。全量完整XML的testcase计数、failed/errors/skipped以及日志/XML SHA都必须核实。

## 本地交付

三次通过后：python -B docs/batch28d_evidence/v6lite_b28d_closeout.py final
若用了新标签，把实际连续三次标签作为 final 后三个参数。
该工具生成实际全量结果报告、脱敏清单与 C:/tmp/v6lite_b28d_stage_paths.json。
核对后：python -B docs/batch28d_evidence/v6lite_b28d_git_deliver.py commit
只暂存上述JSON任务清单；若有外来暂存，不替用户unstage，先报告。
提交 dev、annotated v6lite-batch28d；tag存在不得覆盖。若commit成功但tag阶段中断，核对实际Git状态后继续，禁止重复提交或强制覆盖。
交付工具核验tag归档完整inventory/package SHA、code_sha、报告和manifest blob哈希，结果在 C:/tmp/v6lite_b28d_delivery.json。
实际交付后冻结等待Claude复核，并停止当前定时续办。

John本机合并；不merge、不push、不部署。不连接TWS/Gateway，不发真实订单，不碰用户.v6lite、运行仓库、John配置或Claude操作手册。
已有 docs/V6_LITE_OPERATOR_MANUAL.md 的他人修改保持原样，不暂存。

应用自动续办 ID：v6-lite-28d；已安排2026-10-22 13:31 PT启动，交付后停止。
