# V6-Lite 第三十三批交付报告

日期：2026-10-31（PT，周六）。基于 `v6lite-batch32` / `ea692ddf9c142ae760cc3dfbe9c0d79d9b8b18aa`。报告文件按实际交付日期命名。

指令 SHA-256：`8f4c31c82b4ebc3c1633b1ecad362ec57b83646723c98a742b9315eed050784f`。
冻结 code_sha：`a864ccd2f7680fba`；package SHA-256：`a864ccd2f7680fba570bf4ecdbe1dcbca38dac244fedbdb663e56fd14ee9a6ae`。

## 实现对照

| 文件 | 改动与边界 |
|---|---|
| config.py | execution.tp_start_after_open_seconds 默认30；0关闭；严格非负int，拒绝bool/float/非有限/字符串；参与原asdict配置指纹 |
| tp_start.py | 复用 session_for(trading_day(now)).open_utc，按当天日历计算期限；休市无门；仅新TP检查，持仓/交易日持久审计去重 |
| engine.py | 退出决策与既有pending处理完成后、begin_stage之前检查；中文启动卡“监控”及兼容纯文本启动信息增加说明 |
| protection_engine.py | WORKING且尚无tp_stage的新workflow入口再检查，早于报价通知、证据查询与减量/撤单 |
| test_tp_timeout.py | 有效请求未到点不领取、不删除、不延长原5分钟TTL；既有阶段仍能按正常价格发送 |
| report.py | 当日延后计数、每个持仓的预计与实际开始；同时匹配STAGE_BEGIN和真实reduce_request，覆盖恢复已有pending的入口 |
| tests/test_batch33_open_delay.py、test_batch33_controls.py | 52项离线回放、边界、审计JSON及独立容量核验 |

等待不创建STAGE_BEGIN/workflow、不减STP、不计重试、不写冻结、不发卡。审计 `TP_START_DEFERRED_OPEN` 记录档位、触发价、bid/ask/last、报价时刻、交易日和预计开始时间；同持仓当天跨重启只一条。到点重新决策，不排队补做旧触发，不跳档或合并下单。

STOP、券商GTC STP、穿越告警、保护核验、已开始的TP撤单/恢复、断线恢复、过夜a–d核验和入场扫描/窗口沿用原路径。强身份、S+T+X≤P、运行约束A/B均未放宽。sell_limit、向上取分、新鲜报价和物理发线截止规则未修改。

## 新配置指纹

以下为仓库内可审阅、使用合成账户的g3t/g3n模板；未访问或改动John的toml、运行仓库或用户.v6lite。真实操作配置包含账户、端口、universe等字段，不能将模板指纹冒称其现场指纹。配置源SHA与完整结果见 `batch33_evidence/config_fingerprints.json`。

| 模式 | 配置源 | 新fingerprint |
|---|---|---|
| g3t | v6lite/examples/paper_test.toml（默认30） | `9d005b93488bf2a880732352227d859b7103f8aa2a79ea58cc2acb96696a7df7` |
| g3n | v6lite/examples/paper_g3n.toml（默认30） | `449e14cac8be3ca425e30b18f20530d2e5ca79e7819694bd3b428294ebdd5b91` |

新配置依靠默认值生效，无需修改John配置。0明确关闭；正常非负大整数有效，超出datetime可表示范围时保持等待且不使保护逻辑崩溃。

## 红绿与真实路径

最终测试规格与隔离b32归档中的新增测试同字节；归档原214文件逐一核对未改，原package code_sha仍5e94c5d34a138e7d。归档、临时SQLite和原始Claude证据不进入提交allowlist。

`red_b32_final_spec_after_json_guard`：4项，2 failed / 2 passed / 0 error / 0 skipped。TP1和末股TP3在旧版分别立即减量、立即撤STP，形成预期红证据；止损穿越/真实STP成交以及N=0的精确订单轨迹在b32通过。候选对应全部通过。

10/29回放从10/28真实入场/成交/原GTC、正常shutdown、Store关闭重开及10/29盘前a–d恢复开始。09:30:00.828649 ET达到TP1，29.999秒无阶段、减量或新单；30.000秒可开始TP1，使用当时新鲜bid349.47的既有公式得到LMT348.78。初始报价347.54/347.82及日期来自导出；TP阈值、别名身份和后续报价是明确的合成回放输入，不声称导出提供了缺失字段或证明Paper内部撮合原因。

末股TP3从10/27入场、10/28真实完成TP1与TP2，再shutdown/关库重开、10/29真实过夜恢复得到P=S=1；30秒前原STP持续有效，之后才撤STP，强确认后才发1股TP3。每次实际broker写入前后沿用独立经济账S+T+X≤P断言。

其余覆盖：N=0/60、29.999/30.000秒、半日市、DST前后交易日及切换休市日、非法TOML、触发消失、越多档只TP1、到点身份/原生End/报价失败关闭、在途撤单恢复、09:30:10真实CLI请求延后领取、5分钟原TTL端点、跨重启及次日审计。盘前既有workflow测试通过N=0建立真实阶段后启用屏障来隔离验证继续执行行为，不引入生产热改配置入口。

独立只读审查发现已有pending阶段只有早期STAGE_BEGIN时日报会漏记后续workflow；已补真实reduce_request关联与回归测试。最终复核无新增代码修改需求。

## 聚焦与源码验证

最终源码聚焦（本批52项及b23严格报价门）：95 passed / 0 skipped，13.875秒。此前候选d082e6a97b7872c6的b22–b33明确文件聚焦：1317 passed，2026.234秒；该候选之后又修复了下述审计JSON问题。最终源码的全部b22–b32回归包含于下表三次全量。
源码冻结于2026-10-31T16:33:35.197541-07:00；inventory 217文件；207个Python源码UTF-8无BOM、AST/compile通过，不写pyc。SDK ib_insync 0.9.86及6个源码hash、验证工具、原始输入和操作手册hash固定。

## Windows连续三次串行全量

| 回执 | 结果 | 秒 | PT开始 → 结束 |
|---|---|---:|---|
| final_full_1 | 2410 passed / 1 skipped / 0 fail/error | 2308.015 | 2026-10-31T16:33:36.744887-07:00 → 2026-10-31T17:12:04.765399-07:00 |
| final_full_2 | 2410 passed / 1 skipped / 0 fail/error | 2309.984 | 2026-10-31T17:12:05.201698-07:00 → 2026-10-31T17:50:35.182359-07:00 |
| final_full_3 | 2410 passed / 1 skipped / 0 fail/error | 2306.343 | 2026-10-31T17:50:35.676149-07:00 → 2026-10-31T18:29:02.017926-07:00 |

三轮全部相同源码/SDK/只读输入与相同case顺序、skip身份；case identity SHA-256 `304a4229137f50d2a90be3e1c17dcabf5dfa8629624ce0814d1201b061d16f31`。源码/依赖/输入运行前后逐项一致；日志、XML及receipt哈希可核对。全量均在周末，串行无重叠。

- 既有skip：`v6lite.tests.test_strategy_parity.test_scoring_matches_legacy_scanner_on_identical_bars`；legacy market_scanner.py not present

## 启动卡与日报样例

样例由实际renderer在临时测试状态生成并脱敏，完整文件为 `batch33_evidence/samples/startup_card_sample.txt` 与 `daily_report_sample.md`。

```text
监控：IBKR 心跳：每 60 秒，连续 2 次无应答即告警
持仓漂移：每 300 秒对账，不一致即告警
每日健康报告：13:05 PT（收盘后 5 分钟）
开盘后 30 秒内不开始新止盈
```

```text
- 开盘止盈延后：1 次
- AAPL positionId=pos-8f0ab0c078c241a59ce8 TP1 延后于 2026-10-29T13:30:10+00:00；预计 2026-10-29T13:30:30+00:00；实际开始 2026-10-29T13:30:30+00:00（TP1）
```

## 全部测试回执（失败不覆盖）

| 回执文件 | 标签 | tests | failures | errors | skipped | rc | incomplete | source unchanged |
|---|---|---:|---:|---:|---:|---:|---|---|
| final_full_1.json | final_full_1 | 2411 | 0 | 0 | 1 | 0 | False | True |
| final_full_2.json | final_full_2 | 2411 | 0 | 0 | 1 | 0 | False | True |
| final_full_3.json | final_full_3 | 2411 | 0 | 0 | 1 | 0 | False | True |
| focused_b22_b33.json | focused_b22_b33 | 1317 | 0 | 0 | 0 | 0 | False | True |
| focused_final_gates.json | focused_final_gates | 48 | 0 | 0 | 0 | 0 | False | True |
| focused_final_json_guard.json | focused_final_json_guard | 95 | 0 | 0 | 0 | 0 | False | True |
| focused_report_pending.json | focused_report_pending | 43 | 1 | 0 | 0 | 1 | False | True |
| green_claims_and_boundaries.json | green_claims_and_boundaries | 42 | 0 | 0 | 0 | 0 | False | True |
| green_initial.json | green_initial | 30 | 5 | 0 | 0 | 1 | False | True |
| green_preopen_proof.json | green_preopen_proof | 30 | 2 | 0 | 0 | 1 | False | True |
| pre_json_fix_final_full_1.json | final_full_1 | 0 | 0 | 0 | 0 | 4294967295 | True | True |
| red_b32_final_spec.json | red_b32_final_spec | 4 | 2 | 0 | 0 | 1 | False | True |
| red_b32_final_spec_after_json_guard.json | red_b32_final_spec_after_json_guard | 4 | 2 | 0 | 0 | 1 | False | True |
| red_b32_required.json | red_b32_required | 3 | 1 | 1 | 0 | 1 | False | True |
| red_b32_required_calibrated.json | red_b32_required_calibrated | 3 | 2 | 0 | 0 | 1 | False | True |
| red_nonfinite_quote_audit.json | red_nonfinite_quote_audit | 4 | 4 | 0 | 0 | 1 | False | True |
| red_nonfinite_quote_audit_all_paths.json | red_nonfinite_quote_audit_all_paths | 4 | 4 | 0 | 0 | 1 | False | True |

首轮红回放的同步ACK观察点、盘前证据刷新、退出报价容忍120秒和恢复ACK的时机均为确定夹具校准，原回执保留；日报新增回归曾把预计30秒误当实际31秒，已改成读取真实审计时刻的断言。这些不是随机flake，未重试隐藏失败。本批未发现flake。

首次全量d082候选在2026-10-31 16:29 PT主动停止：补充只读检查发现等待审计可将非有限报价写为JSON NaN/Infinity，使严格JSON校验失败。最终复核确认本机SQLite 3.45.1的json_extract仍接受该样本，因此不称其为已复现的本机去重查询崩溃；具体能力回执见quote_json_runtime_capability.json。四个负例在旧候选确定失败（red_nonfinite_quote_audit_all_paths）；现改为有限数值/null，另存原始报价字符串，不改变退出或严格报价门。修复后95项聚焦通过，重新冻结后从final_full_1开始三轮。pre_json_fix_final_full_1.json的rc非零、incomplete=true、tests=0，明确不是通过结果；未计入连续三轮，也不是随机flake。旧源码/工具归档pre_quote_json_candidate.zip、旧freeze/compile、部分stdout/inventory及中断说明均保留；归档hash见quote_json_interruption.json，归档本体和临时SQLite不进入提交。

## SHA-256与冻结交付

完整SHA-256清单为 `batch33_evidence/SHA256SUMS.json`，列出精确allowlist内源码、测试、指令、报告、冻结/编译/全量回执、验证工具和脱敏样例；清单自身不自引用。

| 源码/测试 | SHA-256 |
|---|---|
| v6lite/config.py | `46e70a2914daad37c0344a00191cfcb1aefc0435f2540d88828bd9c53b9f2732` |
| v6lite/engine.py | `2407efc26fd3665b3f0409c7e700d8c1afe764d58293853517a0a761a75ade89` |
| v6lite/protection_engine.py | `7848f99c7e1e716cc410fe4a9ccc981ab9e628e36b448c0f793455ce25b8e275` |
| v6lite/report.py | `44e5e08c7e3b4ba880bd40523ba1986621e48a1de1b611fb008f648b7acc8a49` |
| v6lite/test_tp_timeout.py | `1d9d160ac8bd43e6efb1fe7e9f88e2d6cebd03ed6898c40f4b3bf7659b9143ff` |
| v6lite/tp_start.py | `1cbdc15651c5193bca2c1eb8534180c9f6074f3f1ec0a517f21c1a7a5b97f0a5` |
| v6lite/tests/test_batch33_open_delay.py | `3c7a8c43f3394cfa0e5dbdb1c6484f093e652d4699c4969b4bff34ed1e479843` |
| v6lite/tests/test_batch33_controls.py | `69cbf77eab7271ca1604a34eba5a0e94ff3589fca43a07d56bad1618b9365a4a` |

在dev本地提交并创建annotated `v6lite-batch33`；交付工具逐项核验tag源码inventory及manifest blob。无push、部署或券商连接；不访问用户.v6lite/运行配置，不改John toml、操作手册及既存非本批文件。唯一Git共享元数据操作为本次明确授权的dev提交和tag。交付后冻结，等待Claude复核。
