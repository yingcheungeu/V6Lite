# V6-Lite 第二十八批 28c 修复报告

日期：2026-10-21（PT）。Windows 三次连续全量通过；dev 本地提交/annotated tag v6lite-batch28c，等待复核。

基线：`v6lite-batch28b` / `0d9a72a90bbfe0519049dff42bce0b3e23a8f9b0` / code_sha `3edad37f5f36037b`。
指令 SHA-256：`1d4da1454256eaf1966bb6a9ea584c1a6aa7f0654187174b0db974ff329f28eb`。
候选 code_sha：`737c9ad3b19d8211`；完整 package SHA-256：`737c9ad3b19d82110cb62eb6a9fbefa6bfd2c39ef85e65662a242a617ca87c42`。

## 根因与修复

1. 保本修改每次清空 status_seen_generation，只有 orderStatus 能恢复；TWS 仅回 openOrder 时永远 PRICE_PENDING。
现接受本进程真实请求后、同 socket、精确强身份/数量/价格/工作状态的原生 openOrder；连接快照、旧时间/旧代、同刻先于物理请求的回报均不授权。
查询确认必须来自自行发起并收到原生 End 的完整工作单、持仓、client executions 和过夜 UTC 当日可见成交查询。
重启只能走完整核验。旧 BE token 的精确 pending binding 继续校验；跨日按原请求日重算历史前缀 digest，同时独立核验当前全部账本和当日资料。
不修复损坏 token，不放行 hold/UNKNOWN/人工冻结。不一致保留原 STP、P1 及独立 P0；重启仍为旧价也冻结。
确认前在 begin_stage 之前等待，每持仓/交易日最多持久记录一次“等待保本修改确认”，不反复创建/放弃 TP2。

2. 本系统 TP 部分成交可能不改变 GTC binding；TP1 全成交但持仓仍非零时，旧 working map 还含已成交 TP，旧废轮逻辑把它当额外 SELL。
现在每轮记录不可变经济 exec 集合及入账顺序，严格核对本 TP intent/position/orderId/permId、原 STP 唯一性、精确 P delta 与合法成交前缀。
时间核实不算经济变更。自身 TP 竞态只作废旧轮并重查，旧轮不能写单；额外未知成交、无效 SELL 数量、缺失/重复 STP、真实 P 矛盾继续 P0。
查询结果里已明确出现的未入账额外 SELL 也不能被自身 TP 废轮掩盖。
原有 generic binding 废轮也收紧：只有原 GTC 精确成交及合法 P 前缀、唯一原 STP、无其他成交/订单/新异常时才能静默废弃旧 P。
原 STP 与未知 BUY/SELL 同轮发生、或原 STP 缺失/重复，立即保留显式 P0；显式矛盾不再被“自身止损已平仓”早退吞掉。
原 STP 单独全成交和部分成交仍保持原有控制回归。

3. ACK 后原先连续两轮四源 blocking 查询；两种 execution 查询共用通道并在结束后冷却 1 秒，产生数秒延迟。
现在减量前完成全部证据，绑定本次 workflow、pending 代、socket、账本经济行、所有已知 SELL 与 hold/freeze/UNKNOWN。
本次请求后的原始 openOrder 合同回声和严格 working orderStatus 在任意顺序到齐后，仅一次延续本次减量授权，S 替换为实际 ACK 的剩余。
原始各源起止时间不刷新，不伪造 full_exec，不持久复用重启权限；任何异动回完整核验。
无阻塞 ACK 收集：首组件到达前最长 1 秒，首个有效组件起 1 秒，且请求起绝对不超过 2 秒、原 proof 不超过 60 秒。
重复回报不延长窗口；普通 pass 和原生 callback 入口使用同一截止判断，迟到首组件或第二组件不能复活过期授权。
0.30 秒普通 pass、0.40 秒第二组件的实际回放中，旧 0.25 秒窗口需要约 4.23 秒；现配对期间不发起自扰查询。报价新鲜度、bid 定价、S+T+X≤P、最终 SELL 容量与全部既有暂停门保留。
DAY TP 还要求原生 pacer 有立即发送容量，并在实际 conn.sendMsg 前校验原报价截止时间；不能发送时按确定未发处理、核验后恢复保护。

额外 SDK 边界：ib_insync 0.9.86 的 wrapper.openOrder 对已有 Trade 只合并部分字段，状态/账户/合约/side/tif/outsideRth 可能留旧值。
现于 wrapper 原始入口捕获不可变字段；自行查询也消费本轮原始行与自己的 End，合并缓存只作诊断。
钩子绑定安装时 socket generation；reset 重装并复用原 SDK 方法，旧闭包在缓存、事件和本轮 row 操作前直接丢弃。
执行时间连字符解析器保持不变，仍用完整成交查询核时。

## 红绿验证

最初独立 spec 22 项在隔离 28b git archive 上 13 failed / 9 passed（夹具修正前的原始失败也保留）。
加入安全边界后 38 项在同基线上 17 failed / 21 passed，0 setup error。
真实旧版原生 wire 回放 ACK→DAY LMT 为约 4.16 秒，重现延迟；其余 live 事实沿用指令，不读取运行状态库。
原始 SDK 合并边界在修复前 18 failed / 2 passed；status→openOrder 顺序约 4.22 秒失败；未入账额外 SELL 的独立负向先失败再修复。
第一次全量 final_full_1 为 2076 passed / 1 skipped / 1 failed，唯一失败为新增测试 UTF-8 BOM 导致既有 ast.parse 检查拒绝。
仅移除三个测试文件 BOM，全部断言不变；编码前后 SHA 见 encoding_fix.json。该旧源码全量不计入最终连续三次。
最终独立复查的旧闭包、真实数量矛盾、原 STP 唯一性与过期 ACK 回放均先红再修复；回执不覆盖。
最初 0.4 秒窗口测试误用理论 timer 截止而非实际 echo 时间，已校准 Windows 时钟提前唤醒；
新增真实 0.30 秒普通 pass 后，在旧 0.25 秒函数对照 red_pair_window_calibrated 上以实际 4.234 秒失败。
全部失败保留原标签与 SHA，没有重试掩盖 flake；没有第三次时序抖动被豁免。

最终聚焦：`focused_final_3`，630 passed，200.391 秒，源码不变。
包含真实隔夜 TP1→仅 openOrder 保本→TP2→TP3 归零、10/22 重启、原生身份/状态/旧事件负向、成交竞态、原生 wire/拥塞报价和 b22–b28b 回归。
新用例持续使用独立经济/卖单容量观察；没有把 proof、status_seen_generation 或授权函数直接改成通过。

Windows 全量：

| 回执 | 结果 | 耗时 | 起止 PT |
|---|---|---|---|
| release_full_1 | 2086 passed / 1 skipped | 1138.875 s | 2026-10-21T21:04:41.942890-07:00 → 2026-10-21T21:23:40.813828-07:00 |
| release_full_2 | 2086 passed / 1 skipped | 1166.985 s | 2026-10-21T21:24:10.044619-07:00 → 2026-10-21T21:43:37.033870-07:00 |
| release_full_3 | 2086 passed / 1 skipped | 1126.453 s | 2026-10-21T21:43:52.154895-07:00 → 2026-10-21T22:02:38.610230-07:00 |

每次必须 returncode=0、完整 XML、同一 inventory 且 source_unchanged；全量在 13:30 PT 之后串行执行。
固定 inventory 共 181 文件；172 个 Python 源码 compile(source_bytes,path,exec) 通过。
跳过项为既有 legacy market_scanner.py 缺失的 parity 测试；record_property/xunit2 为报告格式警告。

## 交付与 SHA

`docs/batch28c_evidence/freeze.json` 固定源码、原指令、已接受报告、验证工具和完整 inventory。
`SHA256SUMS.json` 给出报告、源码、脱敏回执及所有交付证据 SHA-256；验证输出在采集时脱敏；后处理发生替换的文件另保留输入/输出 SHA，完整回执均保留。
仅 dev 本地提交和 annotated tag `v6lite-batch28c`。不 merge、不 push、不部署，John 本机合并；交付后冻结等待 Claude 复核。
不连接 TWS/Gateway、不发真实订单、不碰用户 .v6lite/运行仓库/John 配置；不修改或暂存操作手册。

## 原生出站与空窗 B 读数

按同一测试用例配对实际 conn.sendMsg 时间与离线 SQLite 审计，单位秒。

| 全量回执 | 原生回调时序 | ACK→实际 TP wire（上限 1） | 空窗 B（上限 5） |
|---|---|---|---|
| release_full_1 | status → openOrder | 0.093000 | 0.140000 |
| release_full_1 | openOrder → status | 0.016000 | 0.063000 |
| release_full_1 | status → 0.30s pass → 0.40s openOrder | 0.390000 | 0.468000 |
| release_full_2 | status → openOrder | 0.094000 | 0.125000 |
| release_full_2 | openOrder → status | 0.016000 | 0.078000 |
| release_full_2 | status → 0.30s pass → 0.40s openOrder | 0.422000 | 0.500000 |
| release_full_3 | status → openOrder | 0.093000 | 0.141000 |
| release_full_3 | openOrder → status | 0.015000 | 0.078000 |
| release_full_3 | status → 0.30s pass → 0.40s openOrder | 0.406000 | 0.468000 |

逐用例配对、XML/数据库 SHA 见 paired_latency.json；各轮原始度量见同标签 _latency_measurements.json。未读取实盘状态库。

提交空白检查：原始失败 TXT/XML 的报错空格保留；冻结的独立 native_latency 测试仅有末尾空白行，保持三次全量已测字节，其余空白规则通过，去掉末尾空白的 AST 相同。具体 SHA、诊断及首次提交检查失败见 commit_check_note.json / initial_commit_check_failure.json。未修改测试断言或重跑掩盖失败。
