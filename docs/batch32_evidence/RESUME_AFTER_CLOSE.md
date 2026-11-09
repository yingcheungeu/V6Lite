# Batch32＋补充指令 A：R2 收盘验收续办

用户已裁定四项原生身份＋此前强确认的持久化orderId，并附加七项必过条件，原文见scope_resolution.json。无需再次询问该授权。旧13:31自动续跑仍PAUSED，不能运行5bcb047f旧候选。

R2已冻结，基线v6lite-batch31 / 4456af6d1e70be1daa32d5d7870f34d422373383。最终聚焦focused_addendum_final_2：604passed、0fail/error/skip；source与SDK不变。两个最终回放规格在原b31先失败（red_b31_final_b32_and_A）。

- code_sha：5e94c5d34a138e7d
- package SHA-256：5e94c5d34a138e7df06ec15ac57a1f46958185ed0c7cb9f862e845b75b0c984f
- inventory SHA-256：9e6fd903fbab7f2f12a3fe18a63dd360b4f4fc0c06b579e2f82d2b169d364639
- 预期完整XML：2359（b31 2265＋新94）；既有legacy scanner skip必须如实保留。
- 编译/UTF8 AST/无BOM：204个Python；全部源码保持freeze.json的inventory。

使用真实时钟核验PT>=2026-10-29 13:30，且不在任何交易日06:00–13:30。只操作C:/Dev/V6Lite-dev，激活.venv，三次必须串行、逐次核验完整收据后才进入下一次：

    python -B docs/batch32_evidence/v6lite_b32_validate.py final_full_1 full
    python -B docs/batch32_evidence/v6lite_b32_validate.py final_full_2 full
    python -B docs/batch32_evidence/v6lite_b32_validate.py final_full_3 full

若已有部分产物先读状态，不覆盖；更换新标签时closeout传入确切三label。每次要求rc/failure/error=0、XML完整2359例、同一case/skip集合、同一code/inventory、SDK及两份只读证据哈希一致、source_unchanged/read_only_inputs_unchanged/dependencies_unchanged为true，核验日志/XML哈希。保留所有失败；若修复，备份旧freeze/报告/工具、重新聚焦/冻结并从头连续三次。第三次同一flake不得重试豁免。

三次通过之后：

    python -B docs/batch32_evidence/v6lite_b32_closeout.py final final_full_1 final_full_2 final_full_3
    python -B docs/batch32_evidence/v6lite_b32_git_deliver.py commit

精确allowlist、本地dev提交、annotated v6lite-batch32，核验tag归档inventory/package/code_sha/report/manifest blob及C:/tmp/v6lite_b32_delivery.json。已有tag或部分提交先核对，禁止覆盖tag/重复提交/unstage外来文件。检查所有历史收据均登记artifact_ownership.json。

原始docs/batch32_claude_evidence和docs/batch31_claude_evidence严格只读且禁止纳入allowlist。不得连接TWS/Gateway、真实订单、push/merge/部署、修改用户.v6lite/运行仓库/John配置/操作手册。开盘TP30秒已获同意但只在第三十三批实施。本批交付后冻结等待Claude复核并停止。

## 当前验收进展

final_full_1已完成：2358 passed、1既有skip、0fail/error，13:30:30–14:08:44 PT；2359唯一case集合SHA-256 1dee26217d2773cc49d26d26c6f880638445dcedbb79823f6334fa81dc3dd2f6，完整XML/日志/source/SDK/两份证据及工具哈希全部复核。final_full_2已启动；继续前先检查C:/tmp/v6lite-b32-validation.lock及现有收据，禁止覆盖或并行。第三轮尚未启动，提交/tag尚未执行。

final_full_2已完成：2358 passed、1既有skip、0fail/error，14:09:09–14:47:18 PT；与第一轮2359 case/skip完全一致，source/SDK/工具/两份证据与日志/XML哈希全部复核。接下来final_full_3，禁止覆盖已完成收据；三次通过前不提交/tag。

## 三轮全部验收完成，准备交付冻结

final_full_3：2358 passed、1既有skip、0fail/error，14:47:35–15:25:48 PT。三轮均2359个唯一case，case/skip完全一致，所有源码、SDK、工具、只读证据、日志/XML哈希已逐次核验。接下来仅执行closeout final及dev本地commit/annotated v6lite-batch32；若tag已存在只核验，禁止重复提交或覆盖tag。交付后停止并冻结，等待Claude复核；旧13:31任务继续PAUSED，不再启动测试。
