# 第三十一批指令（Claude → Codex）：过夜持仓保本修改未被确认，误报 P1/P0 并冻结 TP2

日期：2026-10-27（PT）。工作区 `C:\Dev\V6Lite-dev`，dev 分支，基于 `v6lite-batch30`（commit `86cbb435`，code_sha `72393b62e7f08db6`，运行 tag `v6lite-run-b30`，运行仓库 HEAD `6ed515f5`）。

分工照旧：调查和测试设计可以并行；生产代码只由一个实施 Agent 修改；最终验证串行执行。**优先级最高：这是进入 G3-N 之前的阻断项。**

## 0 10/27 事实

运行版本：b30，配置指纹 `2aff7a92`。来源：Claude 只读查看的 `v6lite_20261027.log` 和状态库审计（时间为 UTC），账户号已脱敏。

**持仓：** `pos-087f1be1412c4fc69d15`，AAPL 3 股 @343.47，10/26 入场。GTC STP orderId 2801，permId 731955087，止损 339.62。

**12:36:24：** 过夜自动止盈恢复，a–d 全部通过（控制项 `overnight_tp_restored:pos-087f…`）。

**13:33 TP1 和保本修改：**

| 时间 | 审计 | 内容 |
|---|---|---|
| 13:33:19.051 | STAGE_BEGIN | TP1（价格 344.91，档位 344.75） |
| 13:33:21.046 | PROTECT_MODIFY | STP 3→2 股，generation 1 |
| 13:33:21.276 | ORDER_SENT | TP1 LMT 344.03，`callback_to_tp_ms=16` |
| 13:33:22.709 | FILL | 1 股 @344.71；空窗 B 1.449 秒 |
| 13:33:23.764 | PROTECT_QUERY_SUPERSEDED_BY_TP_FILL | 28c 的竞态豁免生效 |
| 13:33:25.780 | PROTECT_STATE | 保本修改：PRICE_PENDING，pending_trigger=343.64，pending_qty=2，generation 2，`status_seen_generation=-1` |
| **13:33:25.887 / .889** | ORDER_PRICE_OBSERVATION | **openOrder 和 openOrders 都回报 aux=343.64**（距修改 107 毫秒），orderId 和 permId 与原单一致 |
| 13:33:27.805 | OVERNIGHT_TP_FROZEN | stage=TP2，同时发 P1“过夜持仓止盈核验未通过”，原文写着“GTC STP 保护工作单强身份核验失败……当前工作 STP S=未知” |
| 13:33:27.831 | P0 | “保护单状态无法核实”，原文写着“待确认保本修改的工作单身份、价格或数量不一致” |
| 13:33:29.844 | PROTECT_QUERY_LIMITED | P1“券商查询已限速” |
| 13:33:29 → 13:34:37 | PROTECT_VERIFICATION_BACKOFF | workflow=progress，failures 从 1 到 6，退避 1/2/4/8/16/30 秒。**期间每一轮 openOrder 和 openOrders 都回报 aux=343.64**，但每一轮都算失败 |
| 13:35:02.629 | PROTECT_PRICE_CONFIRMED | `source=live openOrder`，这是成交前伴随状态变化的那条 openOrder |
| 13:35:02.645 | FILL | 保本 STP 成交 2 股 @343.56（止损价 343.64，滑点 2.3 bps） |

结论：

- **券商端从 13:33:25.9 起就是正确的**（STP 2801，2 股，止损价 343.64），只是本地一直没有确认。从修改到成交的 97 秒里：发了 1 张误报 P0 和 2 张 P1；TP2 被冻结，假如价格到了 345.72 也不会执行；并且 `overnight_tp_frozen_at_price:…:2026-10-27` 会让当天剩余时间一直保持冻结。
- **对照 10/26 的当日持仓**（`pos-f22536a3…`）：保本修改在 14:40:04.612 进入 PRICE_PENDING，14:40:04.707 以 `source=live openOrder` 确认，同一条路径在 95 毫秒内完成。所以 28c 的 openOrder 确认对当日持仓有效，**对过夜恢复的持仓无效**。
- **对照 10/21 和 10/22**：10/21 的那个过夜持仓也卡在 PRICE_PENDING。10/22 的确认来源是重启后“自己的完整三项查询”（`own complete three-source round`），而不是 openOrder。28c 的过夜回放测试通过了，但真实时序下这条路径从来没有走通过。
- **另外：** 持仓关闭卡片在 13:35:02.681 发出，写着“1 笔佣金未回报”；佣金 9 毫秒之后（.690）就到了，`COMMISSION_FINALIZED` 最终净值 −1.60。

## 1 调查

1. 13:33:25.887 的 openOrder（强身份一致，aux 343.64 = pending_trigger，数量 2 = pending_qty）为什么没有确认 generation 2？请给出具体的门控、文件和行号，并说明它与 10/26 14:40:04.707 那次确认的差别在哪里。可能的方向包括：过夜恢复标记、过夜流程的授权、历史证据绑定了旧的 confirmed trigger 339.62、workflow=progress 的核验要求等。
2. 13:33:27.805 的过夜核验和 progress 核验，为什么把“aux 等于 pending”判为强身份失败？之后 6 轮查询结果都一样，为什么每轮都计为失败？13:35:02.629 那次又为什么能确认？
3. 为什么 28c 的“10/21 过夜止盈回放”测试没有发现？请指出那个测试在哪一步绕开了真实的过夜恢复路径。可能是直接构造了状态，或者没有经过 `overnight_tp_restored` 的真实流程（28b 也是同类问题）。
4. 列出所有在 PRICE_PENDING 期间直接比较 confirmed trigger 的核验点，例如过夜 a–d、coverage、progress、日报、启动接管。

## 2 修复

1. **过夜持仓与当日持仓使用同一条确认规则（28c）：** 满足下面两种来源之一，就把 pending 写入 confirmed，状态回到 WORKING：
   - 同一 orderId 的 openOrder 回调；
   - 我们自己发出的完整 openOrders 快照。

   两种来源都要求：
   - 强身份一致：account、conId、clientId、orderId、permId、orderRef；
   - `aux == pending_trigger`，数量 == pending_qty；
   - 状态为工作中。

   不能用连接快照或旧的轮次来确认。
2. **PRICE_PENDING 期间的核验口径：**
   - 券商 aux 等于 pending，并且数量一致：视为一致。先按第 1 条确认，再继续进行核验。
   - 券商 aux 仍然等于旧的 confirmed trigger（修改还没生效）：视为“修改待生效”，不是失败。不冻结，不发 P1 或 P0，重新查询。
     - 超过 30 秒仍是旧值时，按现有的“保护修改未生效”规则发 P1。这时券商端仍按旧止损价保护，**不发 P0**。
   - aux 既不等于 pending 也不等于旧值、数量不一致、身份不一致：按现有规则冻结并发 P1/P0。
3. **误冻结要能自动解除：**
   - 如果当天的 `OVERNIGHT_TP_FROZEN` 唯一原因是“修改待确认”，那么在保本修改确认、并且新一轮完整三项核验 a–d 通过之后，自动解除当天的冻结。写审计 `OVERNIGHT_TP_FREEZE_CLEARED`，发 INFO，之后的止盈照常执行。
   - 真实失败造成的冻结保持现状。
   - 不得手动修改状态库（运行约束 B）。
4. **持仓关闭卡片：** 发卡片之前，最多等 3 秒，等这次平仓成交的佣金回报。等到了，按最终值发卡；超过 3 秒仍未回报，按 b30 的估算口径发卡。等待期间不能阻塞保护、止盈和查询。

不放宽 a–d、`S+T+X ≤ P`、强身份、报价新鲜度、运行约束 A 和 B 中的任何一条。

## 3 测试

1. **真实回放 10/27：**
   - 持仓必须经过真实的过夜恢复流程，从带 10/26 入场的状态库启动，产生 `overnight_tp_restored`，不能直接塞状态；
   - TP1 减量 → 成交 → 保本修改；
   - 107 毫秒后到达 openOrder 和 openOrders，aux=343.64，**没有 orderStatus**。

   断言：
   - 13:33:25.887 那条 openOrder 确认 generation 2；
   - 不写 `OVERNIGHT_TP_FROZEN`，不发 P1 或 P0；
   - 价格到 TP2（345.72）时按 b23 流程执行 TP2；之后保本或 TP3 出场，持仓归零；
   - 全过程 `S+T+X ≤ P`。

   先在 `v6lite-batch30` 上失败，再修复。
2. **修改迟到：** aux 在 20 秒内仍为 339.62，之后变成 343.64。断言：期间不冻结、不发 P1 或 P0，变成 343.64 后确认。变体：40 秒仍为 339.62，断言发 P1、不发 P0、不冻结券商端保护。
3. **负向：** aux 为 341.00；数量为 3；permId 不同。断言：都冻结，按现有规则发 P1/P0，STP 不受影响。
4. **冻结自动解除：** 先用 b30 的逻辑产生“修改待确认”导致的当日冻结，然后确认并完成 a–d。断言：冻结解除、发 INFO、TP2 能执行。真实失败造成的冻结不解除。
5. **重启：** 用带过夜 PRICE_PENDING（pending 343.64）的状态库重启，断言通过自己的完整三项核验确认，TP2 可以执行。
6. **关闭卡片：** 佣金在平仓后 9 毫秒到达，断言卡片显示最终净值；佣金超过 3 秒未到，断言按估算口径发卡；断言等待期间保护和止盈不受阻塞。
7. **回归：** b22–b30 的全部测试保持通过，包括 10/21–10/22、10/26 当日保本确认的回放。

## 4 交付

- 报告 `docs\V6_LITE_FIX_REPORT_ROUND31_2026-10-XX.md`，内容包括：
  - 调查结论（门控位置、10/26 与 10/27 的差别、28c 测试的缺口）；
  - 改动对照；
  - 红绿测试结果；
  - Windows 全量连续 3 次的结果（交易日 13:30 PT 之后跑）；
  - 新卡片样例；
  - SHA-256 清单。
- 在 dev 提交并打 tag `v6lite-batch31`，交付后冻结，等待 Claude 复核。

## 5 约束

- 交易日 06:00–13:30 PT 只跑聚焦测试；
- 不连接 TWS 或 Gateway；
- 不碰 `%USERPROFILE%\.v6lite`、运行仓库和 John 的配置；
- 不 push；不输出任何机密；证据中的账户号一律脱敏；
- 操作手册由 Claude 更新，Codex 不改。
