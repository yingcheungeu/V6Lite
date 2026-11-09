# V6-Lite 第二十八批最终交付：第 2 节

日期：2026-10-20 PT。工作区 `C:/Dev/V6Lite-dev`，分支 `dev`；基线 `v6lite-batch28a` / `a1ed23fc24d5757eed238502e2f19789ba698df7`。交付 tag `v6lite-batch28`；仅本机提交与打标，未合并、未 push、未部署。

本次完成指令第 2 节，最终 tag 同时继承已通过复核的 28a 第 1 节。28a 原报告 [V6_LITE_FIX_REPORT_ROUND28_2026-10-20.md](V6_LITE_FIX_REPORT_ROUND28_2026-10-20.md) 保持原始字节，SHA-256 `908356ef49cbd2217115e3f63b810fd396f6dfa2ed4439ddeabd9b744ebaec28`。该报告已证明意外断线退出路径至少从 b25 存在，并非 b27 回归；本次未改动该结论或恢复身份门。

最终 code_sha：`c8ac27d8026b39c7`；完整包摘要：`c8ac27d8026b39c7b201dc37148f0ce3fa6eccfed46973e79501af7a221a0069`。指令 SHA-256：`646e8209a5a8d0e9876a42a8b6f47b46ad0444a193eb9d869b731da0bb2389a6`。

## 调查与行为变化

### 账户值静默与刷新

IBKR 官方说明，账户订阅先发送全量，之后按持仓/账户值变化更新；约三分钟检查变化，并不保证未变化的 AvailableFunds 定时回调。因此，单个字段 210 秒未推送不能证明账户订阅中断。[Requesting Account Updates](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-updates/requesting-account-updates)、[Receiving Account Updates](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-updates/receiving-account-updates)

旧 `_refresh_equity` 对有仓或未证明空仓场景调用 `refresh_account()`，该方法明确发 `reqAccountUpdates(False)` 再发 True。因此旧日志本身包含我们主动取消/重订阅产生的动作，不能据此证明现场流先发生中断。官方 2100 表示账户订阅被新的账户数据请求覆盖；仅凭 2100 不能确定现场具体客户端或请求来源。[Error Codes](https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes)

现在无论有仓/空仓，日常静默探测统一复用现有非阻塞 `refresh_funds()` 账户摘要查询，每 210 秒最多一次。资金值、as_of、seen_at 已知、AccountReady 为真、seen_at 在 0–270 秒且 reconcile_ok 时仅 DEBUG；缺字段、未准备、超过 270 秒、未核验及请求失败仍 WARNING。发送请求不刷新时间戳、资金状态或任何核验证据。

现有摘要查询仍要求匹配 reqId、账户、币种、两项完整字段及 End，保留序列防倒灌、取消失败 fail-closed；超过 270 秒仍拒绝新入场。实际断线后的 `refresh_subscriptions` 仍走原恢复流程，未改 adapter、恢复三项核验、入场门、订单或保护逻辑。

账户取消订阅会停止对应 updatePortfolio 推送，故不应把它作为日常探测。权威持仓核验仍使用独立 `reqPositions` 的完整结束证据与账户/conId 校验，不依赖静默字段或旧流缓存。[Request Positions](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/positions/request-positions)、[Receive Positions](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/positions/receive-positions)

### 日报、事件顺序与汇总落盘

| 项目 | 新行为 |
|---|---|
| 行情可用率 | 保留本进程 RTH 标的样本口径；本地日报与手机卡片显示实际首个计数样本的日期、时间及 PT，不再把午后重启后的百分比误读成全日覆盖。零采样显示暂无数据，不虚构起始时间。 |
| 重启披露 | 同日多个 STARTUP 标“当天有过重启”；也识别前日启动、当天运行审计先于当天首 STARTUP 的跨夜续跑重启。昨日启动单独存在或仅有当天 CLI KILL 不标记。 |
| 重要事件 | 按 UTC 时间、首条审计 ID 稳定显示；安全优先选择与字节分配保留，主报 ≤100 KiB、Events ≤200 行、精确遗漏数、脱敏与完整附件均保留。 |
| 重复计数 | 每个 30 分钟窗口到期后，由主循环在保护/退出处理完成、无紧急保护待续的首个 tick 原子提交摘要，无需等待下一条重复事件或正常停机。断线时仍执行；多仓写失败/外层回滚不丢数，重试恰好一次。 |

本批沿用“当前进程 + 明确起始时间”的允许方案，没有回填无法恢复的早先进程采样，也不将中间 53 分钟缺口计为可用。重启提示依赖保留的审计，清库或完全缺失前次运行证据时无法推断。强制结束仍可能丢失尚未到期窗口的内存计数；紧急保护持续待续或主循环阻塞时，到期提交会延后，保护优先。

合成样例及手机卡片见 [report_samples.json](batch28_evidence/report_samples.json)。本批不增加新告警卡片类别；28a 的 P0/P1 样例继续见原报告。

## 连字符成交时间：只调查

官方 **返回格式**依据存在：TWS 设置页将 UTC 返回写为 `YYYYMMDD-hh:mm:ss`；操作员/交易所时区返回为空格形式并含时区文本。[Historical Date Format](https://www.interactivebrokers.com/docs/tws-api/doc/tws-settings/historical-date-format)

但该页标题及示例侧重历史数据。Execution 对象对 Time 只说明成交的服务器时间，未明确给出时区，也未明确声明该字段遵从上述设置。[The Execution Object](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/execution-details/the-execution-object)、[Execution Reference](https://www.interactivebrokers.com/docs/tws-api/ref/execution)、[Receive Execution Details](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/execution-details/receive-execution-details)

TWS 10.17 的产品操作时区说明及 API 10.18 的日期时间发送格式支持，也不能替代 Execution.time 返回值的专项保证。[TWS 10.17](https://www.ibkrguides.com/releasenotes/archive/tws-archives/2022/10.17.htm)、[API 10.18](https://www.ibkrguides.com/releasenotes/api/tws/prod-2022.htm)

结论区分事实与推断：`20261019-14:31:50` 与官方 UTC 返回约定吻合，加上指令提供的同一成交查询对照，是 UTC 的强线索；目前查到的资料不足以仅凭连字符断言所有 `execDetails.Execution.time` 必为 UTC。应由 Claude 结合 IBKR 字段确认或记录版本、时区设置、已知成交时刻的受控样本裁定。**`execution_time.py`、SDK 解析分支及成交归属规则均未改。** 官方资料核对日期为 2026-10-20。

## 红绿与回归验证

独立冻结规格 `test_batch28_section2.py` SHA-256 `9c79d611507e78d6a3d7dfb51987be97312cc2ae95c0961f7bc734ddb5ea6779`：

- 28a 归档上使用同一字节规格、仅旧 API 行为用例：15 failed、2 passed；2 个新 API 原子性控制测试明确排除，不以 missing API 充当自然红。
- 修复版本：19 passed；新增跨夜重启与保护优先级边界 6 用例，合同迁移后的最终聚焦 201 passed。
- b17/b19 旧日志/请求策略合同，以及全量发现的 b10、G2 day1/selfreview、review round2 同类合同，按用户新规则迁移；保留限频、真实回复/静默时间戳、持仓/成交/订单、拒绝入场及保护不变断言。
- 开发期失败、无效文件名调用及首次全量旧合同失败均有保留收据与解释，见 [development_attempts.json](batch28_evidence/development_attempts.json)，不计入连续三轮。没有 flaky 豁免或重试隐藏失败。
- 源码只编译检查覆盖 v6lite/scripts 的 155 个 Python 文件，无字节码落盘。最终全量包括全部 v6lite/tests 与 scripts/tests，串行、源码与测试冻结一致，覆盖既有 b22–b28a 回放及 launcher Ctrl+C 测试。

| 轮次 | 开始 PT | 结束 PT | 秒 | passed | skipped |
|---|---|---|---:|---:|---:|
| final_full_1 | 2026-10-20T19:57:50.399980-07:00 | 2026-10-20T20:11:32.402125-07:00 | 822.0 | 2002 | 1 |
| final_full_2 | 2026-10-20T20:11:32.634590-07:00 | 2026-10-20T20:25:47.529973-07:00 | 854.9 | 2002 | 1 |
| final_full_3 | 2026-10-20T20:25:47.757663-07:00 | 2026-10-20T20:39:37.905042-07:00 | 830.1 | 2002 | 1 |

三轮均为 0 failed、0 errors；Windows / Python 3.11.9；均在 10/20 收盘后执行。每轮 JSON/XML/完整日志及相同源码 inventory 保存在 `batch28_evidence/`。唯一跳过项是旧 `market_scanner.py` 不在此工作区；每轮 33 项警告为 record_property 与 xunit2 输出兼容提示，保留于日志，未屏蔽。

## 变更清单与完整性

生产代码仅根 Agent 修改 `engine.py`、`report.py`、`observations.py`、`store.py`；新规格独立设计，最终验证串行。只读复核发现的两个边界已修复并覆盖，复核无剩余阻塞项。

| 文件 | SHA-256 |
|---|---|
| `v6lite/engine.py` | `9b8f8fd330d63d5150e8383bb632365c247450d4529b49fbf27b8187dc90a5df` |
| `v6lite/report.py` | `a1bedacdc03d224aa5311972c3752750b2e142a48988ce538bc46b8c7c618896` |
| `v6lite/observations.py` | `f7d8bfdfa05c2ac5443325a34a7dbc94e883b92ea65143a0fe279c7fe4cf0552` |
| `v6lite/store.py` | `d68ee27280e441afdc2544705400d3a0b9ee57b4db1e40ab404edec36c7a4e51` |
| `v6lite/tests/test_batch10.py` | `a3b8c0e25195e039a1e0c24840d632707e8e88bcf04dfae4cddbe2411d76d16e` |
| `v6lite/tests/test_g2_day1.py` | `1824076e437aaa67642382655089b7088a817ddab13601430f4531e6adcf04fd` |
| `v6lite/tests/test_g2_day1_selfreview.py` | `04fa52c8ef8b36197d6911213a994e564efdd3ac971a6fd70951a1c05b2ba254` |
| `v6lite/tests/test_review_round2.py` | `c5deb6362b360566871eac651f98592c793f5ec14ca87a0848652b5a469528d3` |
| `v6lite/tests/test_batch17_heartbeat_account.py` | `3360d6f32602226856710372cb082a54611d2af5d6ca65433185a1a8427825ef` |
| `v6lite/tests/test_batch19_funds.py` | `28a7926c0d02d857ccd12441007a1dd509995c7baf45fcfa79e4d1dcc4478cfe` |
| `v6lite/tests/test_batch28_section2.py` | `9c79d611507e78d6a3d7dfb51987be97312cc2ae95c0961f7bc734ddb5ea6779` |
| `v6lite/tests/test_batch28_section2_review_edges.py` | `11cc3f4c50becdf3fd095d620b782aba5bab1cb0fadf0708738806c978b200ab` |

完整证据/报告哈希见 [SHA256SUMS.json](batch28_evidence/SHA256SUMS.json)，清单不包含自身及之后生成的 closeout 以避免循环。冻结及三轮验证一致性见 [closeout.json](batch28_evidence/closeout.json)；本机提交后另核对 tag 归档包与完整包摘要一致。

未连接 TWS/Gateway，未下单，未访问运行仓库、John 配置或用户运行状态目录；测试只用合成 broker 与临时 SQLite。操作手册原有未提交修改保留，未编辑或暂存；28a 已验收报告保持原哈希。本地交付后冻结，等待 Claude 复核及 John 合并。
