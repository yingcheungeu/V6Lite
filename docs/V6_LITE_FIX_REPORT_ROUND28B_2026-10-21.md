# V6-Lite 第二十八批 28b 修复报告

日期：2026-10-21（PT）。状态：Windows 全量连续三次通过；仅交付 dev 本地提交和 annotated tag `v6lite-batch28b`，等待复核。

基线：`v6lite-batch28` / `a161c1cec3125bc9a281acc91562421f0d2378ce` / code_sha `c8ac27d8026b39c7`。
指令：`docs/V6_LITE_BATCH28B_INSTRUCTIONS.md`，SHA-256 `12e8a92cad6bb50c60b2427fef9bc302f21b3f61379239f4e5b7ff98b87a9d18`。
候选 code_sha：`3edad37f5f36037b`；完整 package SHA-256：`3edad37f5f36037b6df3e5b4c6639aee5bf32f0bd133c08825012645eaeb38fd`。

## 调查结论

`_protection_notice_evidence[pid]` 的完整字典只有 `_protection_evidence_fresh()` 一处写入。
它先移除旧缓存，依次读取工作单、持仓、client executions，再读取过夜专用
`account_visible_utc`，最后写入本轮 proof；`_overnight_authority()` 只补充范围/可见条数等派生字段。
稳态 15 秒核验确实会经 `_protection_progress_one()` 调到该路径刷新 proof。不是“启动以后不再刷新”。

根因是重入窗口：稳态查询先清缓存，在各阻塞读取前 `_protect("query")` 会服务退出检查；
价格恰在这个窗口达到 TP1 时，`_overnight_before_tp_stage()` 把尚未到齐的新证据当成失败，
调用一次性 P1 并写 `OVERNIGHT_TP_FROZEN`。卡片因此能同时显示旧/缓存 P=3 和当前 S=未知。
冻结后的持续等待还与 15 秒退出检查和稳态核验相位重合有关；完整结果须唤醒已等待的退出判断。
离线回放支持这条因果链；本次没有读取或改动 John 的运行状态库、日志和配置。

旧测试并非直接塞入一个新鲜 proof。b26 `_attempt_tp()` / `_settle()` 先同步调用
`_protection_progress(now)`，默认 `steady_budget=False, phase="all"`，然后才触发退出。
27a 继承这些 helper；27b `_at_tp2()` 也先手动 progress 再 evaluate/queue。
它们没有覆盖“两个小时真实 tick + steady_budget=True + 查询进行中跨 TP1”的正向路径。

同类门控：保本与 `_overnight_flow_authority()` 的生产调用点已经先读取新鲜完整证据；
共享 authority 的新鲜性检查现在在历史账本/当日时间范围检查之前，失效证据只等待。
`_overnight_check_flow()` 仍在新鲜 authority 内严格检查 workflow token、强身份及数量。
IBKR 连字符时间解析器保持不变，完整成交查询核时路径保持不变。

## 改动与边界

| 文件 | 改动 |
|---|---|
| `v6lite/engine.py` | 记录查询尝试时间与待刷新持仓；完整结果到齐后唤醒快退出判断。 |
| `v6lite/overnight_evidence.py` | 缺失、超过60秒、跨日、换代、绑定失效时排队补查；已有查询复用，不嵌套；结果未齐不开始阶段、不发误P1、不写冻结。 |
| `v6lite/protection_engine.py` | 共享15秒稳态/额外刷新时钟；保留每次慢查询前的保护服务预算；超时/传输失效/换代按退避与F4等待；回调换绑/自身成交闭仓的旧快照作废。 |

补查仍读取完整工作单、持仓、client executions 和过夜 `account_visible_utc`，沿用真实原生 pacing。
15 秒门只约束额外刷新与过夜稳态，ACK/fill/减量/恢复的 b23 紧急证明顺序未加等待。
明确服务器拒绝、完整新鲜范围不完整、a–d 真实矛盾仍失败并发 P1；超时不发“过夜核验未通过”P1，
持续失败仍使用现有查询退避告警与独立 60 秒 F4 P0。真实持仓/保护风险的独立 P0 保留。
查询期间回调导致持仓关闭或绑定改变时，不拿旧轮次的矛盾冻结新状态；闭仓复用既有事件结束路径。
`full_exec` 仍不由历史账本伪造，a–d、强身份、`S+T+X≤P` 和运行约束 A/B 未放宽。

`overnight_tp_failure:<pid>` 只去重卡片；`overnight_tp_frozen_at_price:<pid>:<day>` 只去重当天到价审计。
两者都不作为下单权限门，不用删旧值或手改运行库。10/22 临时测试库实际关闭/重开后，保留10/21旧值，
重新查询并完成 TP1/保本。人工 frozen/hold/tp_pause 和真实异常继续有效。

## 验证

三份独立 spec 共15用例：原始8例先在 b28 的独立 `git archive` 上 **8 failed**，再在本候选 **8 passed**，
同一 spec SHA-256 `ac645f05a291c606ef1355a81f40ddfb0877014f53821706f14e1add97798278` 未更改。
真实回放包含05:59:59启动、INFO恢复、两小时真实tick、≥300轮稳态查询、查询中到TP1，
未注入proof。实际完成 STP706 3→2、TP1卖1成交、原STP两股保本上移，边界容量持续验证。
三类负向必须实际收到本轮P矛盾/未知exec/非工作STP，不能以旧stale P1误绿。
补充用例覆盖四源前保护预算、不完整范围后的15秒限速、真实native End超时与恢复/F4、
真实End回调换代、查询中STP全部/部分成交及随后新鲜未知成交真失败。

3个旧参数断言按本指令迁移：b26查询换代、b27b generation-reset/cross-midnight，
从“立即过夜P1”改为等待并撤销旧权限；禁止订单/保本修改、账本与未核时标记不变、
完整新鲜范围的失败断言均保留。普通query拒绝与不完整scope仍通过原负测。

Windows Python 3.11.9 聚焦验证（生产和测试源码全程不变）：

| 回执 | 结果 | 耗时 | 开始PT |
|---|---|---|---|
| focused_order_safety4 | 304 passed / 0 skipped | 69.766 s | 2026-10-21T09:29:05.945637-07:00 |
| focused_overnight_final2 | 263 passed / 0 skipped | 60.719 s | 2026-10-21T09:27:11.648207-07:00 |

Windows 全量：

| 回执 | 结果 | 耗时 | 起止PT |
|---|---|---|---|
| final_full_1 | 2017 passed / 1 skipped | 941.844 s | 2026-10-21T13:35:59.097259-07:00 → 2026-10-21T13:51:40.935359-07:00 |
| final_full_2 | 2017 passed / 1 skipped | 962.969 s | 2026-10-21T13:52:00.493197-07:00 → 2026-10-21T14:08:03.480154-07:00 |
| final_full_3 | 2017 passed / 1 skipped | 970.25 s | 2026-10-21T14:08:15.277200-07:00 → 2026-10-21T14:24:25.539782-07:00 |

完整开发失败记录见 `docs/batch28b_evidence/development_attempts.json`。
包括第一版修复暴露的相位饥饿、3个旧契约迁移，以及验证工具临时父目录缺失（284 setup errors）
和选取旧测试单独冷导入SDK时被网络禁用夹具拦截（1失败）。后者是本机event-loop socketpair初始化，
未进入业务路径；补齐实际native SDK回放的聚焦集合后通过，未删除/弱化网络禁用断言。
没有确认的时序抖动被重试或豁免。聚焦中的 `record_property/xunit2` warning 是报告格式警告。

## SHA与交付范围

`docs/batch28b_evidence/freeze.json` 固定8个任务源码/测试文件、全部v6lite/scripts inventory、
原指令、已接受b28/b28a报告与源码编译结果。最终 `SHA256SUMS.json` 列出报告和原始/脱敏回执文件。
源文件保持原换行；`redaction_receipt.json` 保留原输出SHA与账号前缀 DFA/DU/DF/U/F/I 脱敏链。
本批只改dev源码、离线测试与本报告/证据。不连接TWS/Gateway、不触碰运行库/用户.v6lite/John配置，
不修改/暂存操作手册，不merge、不push、不部署。John本机合并；交付后冻结等待复核。
