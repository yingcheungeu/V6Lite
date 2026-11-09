# 27b 实施前调查（2026-10-16 PT）

任务：只在 dev 基于 v6lite-batch27a 实施 27b；27a 运行仓库合并验收属于此前单独授权的任务。指令 SHA-256：95c851973e6e5a9563f8dd7fabed4be0cc95806b5a8f89e7aebf77f25e7c37e9。

## 发现与最小实现边界

1. 基线 `v6lite/broker/ibkr.py:1254`、`:1285` 调用 ExecutionFilter 时没有 time。本地 SDK 的默认值为空，client 原样发出。官方旧文档说明默认午夜，但 TWS Trade Log 可改回溯范围，所以方法名 `executions_today` 本身不是范围证明。
2. 为本批窄例外补齐必要前提：显式发送“本次查询日美东零点”换算后的 UTC `YYYYMMDD-HH:mm:ss` 过滤；只有本轮原生结束、无请求错误、同代且起止同一美东交易日的完整结果携带可信范围。旧查询结果、无范围普通 list、失败/部分/跨日/换代结果不得授权。不得用无过滤重试来放行。专用 `account_executions_today_scoped()` 只供过夜证据 d；原 `executions_today()` / `account_executions_today()` 的默认范围、启动恢复、release 和第5.2节精确核时保持原样。普通结果不能仅靠方法名获得新的当天例外权限。
3. 基线 `overnight_evidence.py:108` 把所有未核时成交交给历史收到时间条件；当天 TP1 必然不满足历史截止。除 a 外，`:267` 的 d 也比较精确时间，因此只修改 a 不足。当天例外仅在严格九字段、持久化合约、provisional 原串与本轮可见成交匹配时，才允许判断“属于今天”；不宣称精确到秒已核时。
4. 不清未核时标记、不写 VERIFIED、不改 fills.time/trading_day，非过夜路径不变。其他 a–d、保护、卖出数量和运行约束 A/B 保持；A/B 是 John 接受的剩余风险，不是技术证明。
5. 原始时间须在 decoder 入口捕获。SDK 在 wrapper hook 之前解析；无法解析时会在 interpret 记录含完整字段的异常。因此诊断路径必须脱敏，只留原始时间文字及必要来源，不能伪造 Fill 或猜时间；丢弃/不完整数据不能声称完整授权。汇总依据原始字符串分类，不能依据回退后的 UTC 时间。
6. 两处文字：时间未核实前缀仅一次；d 无法归属失败时仍保留实际查询条数。卡片/日报显示按本轮当天查询判定的事实，不能仅看到 marker 就声称查询成功。

## 官方依据与限制

- [IBKR Executions and Commissions（已弃用）](https://interactivebrokers.github.io/tws-api/executions_commissions.html)：范围默认与 Trade Log 例外、execDetailsEnd 完成边界。
- [IBKR ExecutionFilter](https://www.interactivebrokers.com/docs/tws-api/protobuf/execution-filter)：time 的 at/after 语义及显式 UTC 格式。

本地 SDK 版本、文件哈希和逐行摘录见 `docs/batch27b_evidence/source_provenance.json`。不改变解析规则，不设置 IB.TimezoneTWS，不改 TWS Master API client ID。

未知，需 Paper 实测：John 当前 TWS 对显式 UTC 过滤的接受情况、现场 callback/query 原始时间字符串、可见成交历史范围。请求拒绝或不能确认范围时保持 P1 与冻结止盈，不以查不到推断未发生。

## 验证安排

独立新测试先在27a自然失败，再由唯一实施 Agent 修改生产代码；根 Agent 串行验收。正向真实完成 TP1→保本 ACK→TP2，并逐步检查 S+T+X≤P 及账本/标记只读；覆盖五类否定、原生查询范围、原始时间三类、脱敏、两处实际卡片。27a 的86项及b22–b26保留。全量三次只在允许时段运行。
