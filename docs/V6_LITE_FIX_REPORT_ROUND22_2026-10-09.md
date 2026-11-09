# 第二十二批整改报告：保护单 STP 与第二十一批直接 PowerShell 入口

日期：2026-10-09（指令日期）；实际 PT 验证时间见第4节。只在 `C:\Dev\V6Lite-dev` 的 dev 实施。基线 `v6lite-batch20` / `c5a39952635943dde5f1ae7f2a52da84c8327375`，原始 `code_sha=cfe16edd7a7e4d7f`。指令 SHA-256 已核对 `14206477aab33bdc242e331d88745695d0f828e8c9744d49eaa20df4ed43c18a`。同时提交第21批尚未提交的启动器材料。本报告不是运行放行；交付后冻结等待 Claude 复核，不合并、不 push。

## 0. John 的决定原文

> John 选择方案 B：券商端保护单改用止损市价单（STP），只用于保护单。入场和止盈继续使用限价单。不启用“被拒后自动重挂”。

STP在跳空或复牌时成交没有价格下限。本批不承诺最大实际损失。研究依据保留在第21批文件，未猜测 IBKR 未公开的止损限价距离上限。

## 1. 范围、分工和冻结证据

- 开始修改和最终验证前均核对 `v6lite_process_count=0`。未连接TWS/Gateway、未启动真实v6lite run/g2_probe，未读取或修改运行仓库、John配置、`%USERPROFILE%\.v6lite`。所有数据库及控制台探针使用TEMP合成fixture，未外发通知。
- root是唯一生产代码及脚本实施者；三位独立Agent分担配置/适配器、生命周期/卡片、schema/审计测试设计及最终只读复核；最终验证由root串行执行。
- Claude操作手册 `docs/V6_LITE_OPERATOR_MANUAL.md` 是已有外部修改，不编辑、不stage。全程SHA-256保持 `5d7eb6f043f4d933019f36b6cbfd28b28f8474c0b7097261f9bb77ae627133bf`；交付后仍保留此已有修改。
- 保留原LF/CRLF。Git `* -text` 下默认diff --check会把原CRLF的CR报作空白；源代码及手写文档用 `-c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol` 检查通过，不重写整文件换行。原始红测pytest XML含traceback生成的行尾空格，整暂存区diff --check因此提示这些证据行；保留原始XML字节与hash，不清洗历史证据，提交空白检查范围排除原始生成证据。

## 2. 发现 → 改动 → 测试

路径相对仓库根；行号以清单冻结源为准。测试文件均位于v6lite/tests/，名称为test_batch22_*.py；lifecycle简称对应test_batch22_protection_lifecycle.py，其余简称对应同名后缀。下表测试函数均为test_B22_前缀，完整名称见对应文件。

| 发现/要求 | 改动及代码位置 | 对应测试 |
|---|---|---|
| 首笔成交立即保护 | protection_engine.py:308 / broker/ibkr.py:791：SELL STP/GTC/outsideRth=False，auxPrice按已入账stop向下取整0.01，不设lmtPrice | protection_lifecycle:137 first_partial_fill；config_broker wire合同 |
| 部分BUY加量 | protection_engine.py:934：三证据闭合、同ref/orderId/permId修改totalQuantity | lifecycle:153 partial_entry_growth |
| 保本价 | protection.py:5 / protection_engine.py:934：改auxPrice，limit=None | lifecycle:233 TP1_fill_changes_aux |
| TP减量、恢复、末股 | 券商减量确认后才发LMT；撤单pending保留可能量；末股确认旧保护不可成交后才发TP，新旧不并存 | lifecycle:191、214、253、272，逐事件检查毛量 |
| 原生完整身份 | broker/ibkr.py:525 / protection_engine.py:532：account/client/conId/ref/orderId/permId+SELL/STP/GTC/RTH/STK/USD+auxPrice；无限价有效，未知RTH不猜False | lifecycle:168、179；snapshot_cancel:135、169 |
| 缓存撤单边界 | broker/ibkr.py:856：可信v6l-protect-前缀与wire stop/GTC取OR；重验全合同和三ID，LMT+DAY组合不可绕过，普通DAY仍可撤 | snapshot_cancel:195、204、217；cancel_namespace:72、83 |
| 重启/退休/停机/release | engine.py:1607、2649 / store.py:682、701：当前/退休强身份；正常停机保留GTC；release确切撤单+两组稳定三证据 | lifecycle:297、328、360、392、429、451；原L3强断言保持 |
| 触发不再依赖限价 | protection_engine.py:1174：RTH首穿越P1，剩余超过原时限P0，删除低于限价快捷分支 | lifecycle:539、561、581、604 |
| 反弹/部分成交/缺报价/跨日 | 保留首次穿越时间；反弹不解除、部分成交不重置；缺报价仅推进已有事件；盘外不计时、跨日新鲜观察重新计时 | trigger_incidents:111、226；lifecycle:561、581 |
| 未成交事件的投递和解除 | protection_incidents.py:108、117：WORKING不是成交，P0失败可重试、成功后30min P1；真全成解除。旧单真实cancel+退休身份+新强三证据才结束旧timer | trigger_incidents:129、163、186；trigger_replacement:24 |
| 旧STP LMT拒绝 | engine.py:247连接前检查，protection_engine.py:240 P0并阻断；不自动改价/撤单/迁移或release | lifecycle:492、503；schema_audit:318、337 |
| schema4升级 | store.py:278：单事务新增order_type，旧行标STP LMT；nullable局部重建保留原行/约束/索引/trigger；未知DDL整体回滚；新保护STP/NULL | schema_audit:239、256、287、368、390、404 |
| 旧配置键 | config.py:248、358：order_type只精确STP且进入指纹；enabled=false也拒offset并报中文删除提示；五示例更新 | config_broker默认/指纹/旧键/五示例矩阵 |
| 每笔成交和滑点 | store.py:967 / report.py:131：本笔价+同intent数量加权累计均价；exec交易日日报；重放幂等，退休/平仓后真成交不二次PnL | lifecycle:644、680；schema_audit:422、440、454、473、521、544 |
| 未确认价不猜 | pending/UNKNOWN/CANCEL或退休价位不确定时，stop/usd/bps=null、另列候选 | schema_audit:497；pending_stop_audit三场景 |
| 明确拒绝不重挂 | protection_engine.py:396：强原生身份、terminal拒绝和明确reason；P0冻结，首选同TWS限价平仓后release；正常cancel不伪报拒绝 | lifecycle:697；原batch20_protection_incident强断言更新STP |
| 仅保护STP/dry零写 | LMT入口拒PROTECT；无StopLimitOrder/旧提交方法；唯一placeOrder和cancel各只读拦截 | config_broker MKT=0/其他LMT矩阵；lifecycle:732 dry全部入口 |
| 第二十一批入口 | scripts/v6.ps1:133：自有Python共享控制台、正常/finally均等待，保存恢复PYTHONUNBUFFERED；无Out-Host长期管道。新一行.ps1模板，未装用户目录 | scripts/tests/test_batch21_entry.py 18项，PS5.1/PS7×dry/fakePaper四个真CTRL_C_EVENT；原112项门控保持 |

计时改动只影响保护监测与事件解除，未改入场/TP策略及普通LMT定价。正常串行转换继续检验 S+T+X≤P；外部卖单、迟到成交/延迟ACK造成的物理瞬时竞态不能描述为任意时刻绝无超卖，原只减不增与异常隔离保持。重复成交、RELEASED隔离、exec更正、L3迟到恢复强断言未放宽。

## 3. 先红后绿

TEMP解包精确v6lite-batch20，只叠加对应新增测试；基线原生产/测试文件未改。cfe16edd7a7e4d7f为叠加前指纹，新增测试也计入包指纹，所以不声称叠加后包仍为旧指纹。各测试hash在红绿之间不变。

| 新文件（均v6lite/tests/） | B20 failed / passed / errors | 最终绿 |
|---|---|---|
| test_batch22_config_broker.py | 23 / 61 / 0 | 84 passed |
| test_batch22_protection_lifecycle.py | 51 / 1 / 0 | 52 passed |
| test_batch22_schema_audit.py | 17 / 9 / 0 | 26 passed |
| test_batch22_pending_stop_audit.py | 3 / 0 / 0 | 3 passed |
| test_batch22_snapshot_cancel.py | 45 / 3 / 0 | 48 passed |
| test_batch22_trigger_incidents.py | 6 / 0 / 0 | 6 passed |
| test_batch22_trigger_replacement.py | 1 / 0 / 0 | 1 passed |
| test_batch22_cancel_namespace.py | 4 / 4 / 0 | 8 passed |

新增228项：B20 **150 failed / 78 passed / 0 errors**；最终聚焦 **228 passed / 0 failed / 0 errors**。既有非PROTECT LMT及SQL正约束等绿色对照刻意保留。多个生命周期红例在首个STP合同断言就失败，不能声称每个下游分支在B20均执行到；绿色阶段才执行完整转换与不变式。

复核补充的未知stop审计、RTH/STK/USD及缓存取消、持久触发事件，也在修正前STP候选自然失败；candidate日志保留。旧单退休清理首版缺调用点，trigger_final为1 failed / 6 passed；仅在新强三证据分支补调用后identity_trigger_final为15 passed，未弱化任何门槛。

开发期交易包全量1 failed / 1105 passed / 1 skipped：旧execution_time fixture仍把数值限价传给STP计划；只将fixture改None，保留账本/执行时间/交易日断言，27项聚焦通过。上述为开发红证据，不是偶发flake，未重试掩盖。最终三次见第4节。

第21批历史红绿详见其入口文件：旧Out-Host真CTRL_C_EVENT无SHUTDOWN；入口10项自然失败；首版argv两项失败后修正；130项scripts通过。其cfe指纹/未提交措辞是21批当时状态，本批统一提交这些材料，不是当前部署声明。

## 4. Windows全量连续3次（冻结源、串行）

```powershell
. C:\Dev\V6Lite-dev\.venv\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONUNBUFFERED='1'
python -u -B docs\batch22_evidence\run_final_validation.py
# 每轮实际命令：
python -u -B -m pytest v6lite/tests scripts/tests -q -p no:cacheprovider --tb=short --junitxml <该轮xml>
```

驱动检查Windows/指令/保留手册hash/AST及包和scripts哈希；每轮前后确认源不变与code_sha一致，开始/结束均在交易日06:00–13:30PT限制之外。移除真实通知/保护诊断环境变量，使用Fake、stub及TEMP。Ctrl+C只发给测试拥有并核对PID归属的控制台。

冻结 **code_sha=d42320d23de79d6e**；Python `3.11.9 (tags/v3.11.9:de54cf5, Apr  2 2024, 10:12:12) [MSC v.1938 64 bit (AMD64)]`；平台 `Windows-10-10.0.26300-SP0`。

| 轮 | 开始PT | 结束PT | passed / failed / errors / skipped | 秒 | 源一致 |
|---|---|---|---|---:|---|
| 1 | 2026-10-08T23:00:37.124286-07:00 | 2026-10-08T23:05:06.042951-07:00 | **1302 / 0 / 0 / 1** | 268.86 | 是 |
| 2 | 2026-10-08T23:05:06.112537-07:00 | 2026-10-08T23:09:33.716834-07:00 | **1302 / 0 / 0 / 1** | 267.59 | 是 |
| 3 | 2026-10-08T23:09:33.777573-07:00 | 2026-10-08T23:14:00.718036-07:00 | **1302 / 0 / 0 / 1** | 266.92 | 是 |

既有skip：v6lite.tests.test_strategy_parity.test_scoring_matches_legacy_scanner_on_identical_bars：legacy market_scanner.py not present。没有新增skip/xfail或过滤失败。

## 5. FakeBroker实际渲染样例

生成脚本使用Rig/MemoryChannel/TEMP并禁止socket连接，不发Discord，不读运行状态。止损108.47：1股107.22，累计均价107.22，滑点+1.25USD/+115.2392366553bps；2股108.97，累计均价108.3866666667，滑点−1.00USD/−46.0956946621bps。美元净滑点+0.25，日报仍逐exec展示，不用累计均价混算单笔滑点。

单笔USD=Q×(stop−fill_price)，bps=(stop−fill_price)/stop×10000；累计均价=ΣQ×price/ΣQ。SELL恶化正/改善负。未确定stop记未知并列候选；退休用旧合同价不猜替换价；按fills.trading_day归属，重放不重复列。

以下17张完整保护P0/P1均合成事件，随机订单ID/账号不是John真实信息。

### 完整卡片渲染

## gap A warning / P1

🟠 P1 · 入场后保护单尚未确认

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-128535e852564137b92c orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：保护确认仍未到达，未保护 0 股；从空窗起点累计 15 秒才升级 P0。

你要做什么：1）在 TWS 核对保护单 orderId 与剩余量；2）不要新开仓或另发卖单；3）从空窗起点累计 15 秒仍无券商确认，再按 P0 处置。

## gap A overdue / P0

🔴 P0 · 入场持仓未获券商保护

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-a9960ccd83464e888e18 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：持仓存在裸仓风险，未保护 0 股；新开仓继续冻结。

你要做什么：1）在 TWS 核对持仓与保护单是否真实存在；2）不要另下卖单；3）由 John 处理裸仓并保持新开仓冻结。

## gap B overdue / P0

🔴 P0 · 止盈后保护缺口未恢复

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-ccc4025e3a8348a9996d orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：止盈流程尚未恢复保护，止盈在途 待核实 股；超时须核实撤单。

你要做什么：1）先撤销在途 TP 并核实撤单及成交；2）不要另下卖单；3）再核对并恢复同标的保护单剩余量。

## preflight B overdue / P0

🔴 P0 · 接管前止盈超时，卖单待人工核对

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-85ea42fa313340ce8025 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：接管前连接和订单身份未验证，不能把超时 TP 当成已撤单。

你要做什么：1）如果 Lite 在 5 分钟内没有恢复连接，在 TWS 手动撤销 TP 单 待核实 并核对成交；2）不要另下卖单；3）接管后先核实 TP 已撤，再恢复保护单。

## external sell / P0

🔴 P0 · 托管标的出现外部卖单

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-53e704f3f15f47b788b5 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：外部卖单与 Lite 卖单可能叠加，可能超卖 0 股。

你要做什么：1）先在 TWS 核对外部卖单来源和成交：不需要就撤掉，需要就保留；2）保留时不要再下任何卖单，Lite 已冻结该标的，持仓未平前不再做止盈，原保护单仍可能在途；3）平仓并核实无工作单后，由 John 对账并执行 release AAPL。

## rejected / P0

🔴 P0 · 券商拒绝了保护单，该持仓目前没有券商端保护

发生了什么：AAPL 持仓数量=20 股 保护单已确认=0 股 保护单可能=0 股 止盈在途=0 股 外部卖单在途=0 股 未保护=20 股 可能超卖=待核实（可能上界0） 股；券商已明确拒绝该保护请求；orderRef=v6l-protect-b6713723e37945bc82f1 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：被拒的保护请求无效，未保护 20 股不能据此当作已覆盖。

你要做什么：1）在同一 TWS 用限价单平仓；2）确认该标的 0 股、无在途卖单，并等待成交静默期；3）在本机执行 release AAPL，身份或新鲜证据不足时保持拒绝；不要手改状态库、新开仓或自动重挂保护单。

## unknown / P0

🔴 P0 · 保护单状态无法核实

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–20） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-e762dfa8f0cc4f45a904 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：保护订单身份或状态未知，未保护 待核实（可能范围 0–20） 股仍须券商核实。

你要做什么：1）在 TWS 用 orderRef、orderId、permId 核对真实订单；2）不要另下卖单或加量；3）取得持仓、工作单、成交三项证据后再决定。

## tp not sent / P1

🟠 P1 · 止盈单未发出，正在恢复保护

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-e65d27e7495c41e99327 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：止盈单确定没有发出，Lite 正在按三项证据恢复保护；原 B 空窗持续计时，累计 30 秒仍未恢复则升级 P0。

你要做什么：1）目前等待 Lite 用新鲜三项证据自动恢复保护；2）不要另下卖单或手动增加保护股数；3）从 B 空窗起点累计 30 秒仍未恢复，按 P0 卡片核对 TP 和保护单。

## trigger crossing / P1

🟠 P1 · 已到止损价，券商止损市价单应在成交

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-1a232675638446d3986a orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：止损价格已触发本地监测；券商是否已触发及成交仍须核对。

你要做什么：1）在 TWS 观察同一保护单的成交及剩余量；2）不要另下卖单；3）常规交易时段累计 60 秒仍有剩余，按 P0 立即处置。

## trigger unfilled / P0

🔴 P0 · 止损已触发但未成交

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-73191e31866d48468e3a orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：止损市价单超过时限仍有剩余，可能停牌、交易暂停或券商异常；持仓仍暴露于继续下跌。

你要做什么：1）在同一 TWS 核对本卡 orderId 的成交、剩余量及是否停牌或交易暂停；2）不要另发卖单或自动重挂，先核实原 STP 状态；3）由 John 联系券商处理异常，由 John 暂停新入场并完成订单和持仓对账。

## trigger suspected / P0

🔴 P0 · 疑似触发或保护状态不明

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-7b701bd741a94d789342 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：疑似触发止损，但现有证据不足以认定券商已执行。

你要做什么：1）在 TWS 核对价格、保护单和成交；2）不要另下卖单；3）确认券商状态后由 John 处置。

## invalid price / P0

🔴 P0 · 成交后无法建立有效保护价

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-c6bbcb2648364d25b8aa orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：成交后保护价格无效，现有持仓不能凭该价格获得保护。

你要做什么：1）在 TWS 核对真实持仓和可用保护价格；2）不要另下卖单；3）由 John 人工处理并维持新开仓冻结。

## invalid entry price / P1

🟠 P1 · 无法计算保护价，买单已拦截

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-2178fb2914a44552b280 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：保护价格无法建立，买单已拦截且本次不开仓。

你要做什么：1）确认买单已被 Lite 拦截且没有券商订单；2）不要绕过门控下单；3）修复 ATR 或价格证据后重新申请。

## evidence incomplete / P0

🔴 P0 · 保护单三项证据不完整

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–20） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-c9a7208c21d74b2aa76a orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：持仓、工作单和成交三项证据不完整，不能安全增加保护数量。

你要做什么：1）在 TWS 分别核对持仓、所有工作单、成交；2）不要另下卖单或增加保护数量；3）证据齐全且一致后再解除冻结。

## shutdown incomplete / P0

🔴 P0 · 停机时保护单未核实

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–20） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-b9488f27a5544017b121 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：停机时保护单存在性未核实；重启前不能把旧快照当成仍在途。

你要做什么：1）在 TWS 确认 GTC 保护单是否保留及其剩余量；2）不要另下卖单；3）重启后完成三源核对，异常时由 John 人工处理。

## late tp fill / P1

🟠 P1 · 止盈迟到成交，保护单已减量

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=0 股 可能超卖=0 股；当前快照已核实；orderRef=v6l-protect-67a9226489224f9295da orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；止损价（触发后市价成交）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：迟到止盈成交后的保护数量已获券商确认；当前可能超卖 0 股，仍须核对其余卖单。

你要做什么：1）在 TWS 核对迟到 TP 成交及当前保护单剩余量；2）不要另下卖单；3）确认 Lite 已只减保护数量并重新对账。

## legacy type / P0

🔴 P0 · 旧类型保护单，需人工处理

发生了什么：AAPL 持仓数量=20 股 保护单已确认=20 股 保护单可能=20 股 止盈在途=0 股 外部卖单在途=0 股 未保护=待核实（可能范围 0–20） 股 可能超卖=待核实（可能上界0） 股；当前保护状态待核实；orderRef=v6l-protect-a09970d887f248b5aec1 orderId=1002 permId=51002 TP=- TP orderId=- TP剩余=待核实 股；外部订单=无券商新快照；旧合同止损价（STP LMT，仅供人工核对）=108.47；上次确认=2026-10-06T14:00:31+00:00 工作单证据时间=2026-10-06T14:00:31+00:00；FakeBroker 合成渲染样例；不是实际券商事件

影响：旧类型保护单不能作为当前 STP 保护证据；必须人工处理，全部入场保持阻断。

你要做什么：1）在同一 TWS 人工核对旧保护单与持仓；2）不要把 STP LMT 改认成 STP，不自动撤单、改价或重挂；3）由 John 处置并保留旧状态证据，未经复核保持入场冻结。

### 本地日报

```markdown
# V6-Lite daily report 2026-10-06 (paper)

- account: DU0000000   state role: formal
- code_sha: d42320d23de79d6e
- market data: real-time (1) | n/a RTH availability (no runtime RTH samples supplied)
- last health market data (instantaneous, not RTH availability): real-time (1)

- realized P&L: -15.25   fees: 3.00   net: -18.25
- entries submitted: 1 / 4
- daily stop latched: False
- open positions: AAPL 17@113.47 stop 108.47
- 券商端保护: AAPL 截至 2026-10-06T14:00:31+00:00 最近一次券商快照确认，当前仍需核实，本地状态=WORKING P=17 S=17 类型=STP 止损价（触发后市价成交）=108.47 orderId=1002；空窗统计 A=0.000s B=0.000s；当日止损成交：AAPL execId=0000fa4e.00000002.01.01 数量=1 止损价=108.47 成交价=107.22 成交均价=107.22 滑点=1.25 USD / 115.24 bps；AAPL execId=0000fa4e.00000003.01.01 数量=2 止损价=108.47 成交价=108.97 成交均价=108.39 滑点=-1.00 USD / -46.10 bps
- kill switch: off
- open anomalies: none
- frozen symbols (operator must flatten, then `release`): none
- released positions (P&L of the operator's TWS trades is NOT in the figures above): none

## Fills
- 14:00:30Z AAPL BUY 20 @ 113.47 realized 0.00
- 14:00:31Z AAPL SELL 1 @ 107.22 realized -6.25
- 14:00:31Z AAPL SELL 2 @ 108.97 realized -9.00

## Events
- 13:50:00Z LOSS_STATE {"cooldown_until": null, "streak": 0}
- 14:00:30Z SCAN {"errors": {}, "min_score": 60, "signals": [["AAPL", 78]], "vix": 18.0}

## Operator notes

- (market context, manual actions, things to review)

```

## 6. 独立复核和旧分支穷尽搜索

三位独立Agent只读最终PASS，无剩余阻断。搜索生产PY/TOML，排除tests和cache：

```powershell
rg -n 'STP LMT|StopLimitOrder|submit_stop_limit|limit_offset_pct|lmtPrice|\bMKT\b|MarketOrder' v6lite -g '*.py' -g '*.toml' -g '!**/tests/**' -g '!**/__pycache__/**'
rg -n 'placeOrder|cancelOrder|read_only|startswith\("v6l-protect-' v6lite/broker/ibkr.py
```

| 剩余引用 | 裁定 |
|---|---|
| store.py:75、288旧type默认 | 只给旧合同诚实标签，新计划显式STP；不转换价量身份 |
| store.py:823 / protection_engine.py:183、244 | 旧类型隔离、人工提示及拒绝 |
| engine.py:1259、1607、2649、2661、2695 | release拒绝旧合同；通用分类/停机识别旧GTC以免当DAY自动撤掉，不认领为有效STP |
| ibkr.py:534、537、883 | 旧stop也需账户/资产强元数据、归保护取消类别；随后严格STP拒旧type |
| ibkr.py:62、568、840、890、1173的lmtPrice | UNSET_DOUBLE规范化None、拒有限限价和强身份快照；不是卖价下限或追价 |
| config.py:358、359的offset | 仅明确拒绝旧键，无计算；5示例全STP |
| StopLimitOrder/submit_stop_limit/MarketOrder | 生产调用零；LMT和StopOrder两合同，唯一placeOrder ibkr.py:763，只读758/760；cancel只读860 |

配置/适配器Agent：完整身份、未知RTH、可信ref分类、centfloor和dry物理边界保持；B21tag/config/YES及确认后复核未弱化。生命周期Agent：拒绝不重挂；退休清理唯一调用在新强三证据分支，另核旧真实terminal cancel_seen、无hold及不可变完整身份/qty，local CANCELLED不能代替真实回报。Schema/审计Agent：局部DDL整体回滚，原行/索引/trigger保留；未知stop不猜，exec幂等、逐交易日日报正确。

非阻断夹具备注：test_batch15.py:246故意缺身份负例也缺currency，可能先因USD拒绝；新逐字段参数化覆盖完整身份单因素。最终三次期间没有为此修改冻结代码。

## 7. John配置、部署和实体键盘验收（本轮未执行）

John须在 `v6lite.g3t.toml` 与 `v6lite.paper.toml` 的 [protection] 中删除旧整个键值行：

```toml
limit_offset_pct = <该文件原值>
```

本轮未读两份真实配置，不猜数值；不要把尖括号占位写回。新默认order_type="STP"，John也可显式填写。enabled=false也立即CONFIG ERROR：`保护单已改为 STP，不再使用 limit_offset_pct，请从配置中删除`。指纹变化须记录，不能改account/client/状态身份绕过拒绝。旧STP LMT仅安全拒绝，归档由John执行。

G3运行手册第5版已更新STP、逐笔滑点、至少两次真实止损、部分成交/RTH、TWS7497和直接入口。Claude操作手册本轮未改。Claude复核、另行合并授权及运行tag完成后，由John自己建立用户v6.ps1：

```powershell
# 内容只有这一行；本轮仅提供scripts/v6-user.ps1模板
& 'C:\Dev\V6Lite\scripts\v6.ps1' @args
# 已有PowerShell明确调用，避免PATH命中旧cmd：
& "$env:USERPROFILE\v6.ps1" g3t-dry
& "$env:USERPROFILE\v6.ps1" status g3t
```

真实运行仍需YES/账户/端口/保护/审批门控。合并后首日开盘前按21批入口文档第4节实体验收：一次Ctrl+C，无批处理Y/N，等待Python正常退出后父PS提示符；DAY撤单确认、GTC保留；只读SQLite核对SHUTDOWN audit且unresolved为空。SHUTDOWN不是保证存在的console行，wal/shm本身不能判停机失败。离线四组真CTRL_C_EVENT合成通过不代替John真实终端/TWS订单的验收。

## 8. SHA-256清单和提交范围

覆盖完整v6lite包（排缓存）、scripts、任务文档及证据。未修改但参与fingerprint/测试的文件也列清单，**不因此stage**。只stage本批修改/新增的任务文件，排除Claude手册、venv/cache、数据库、真实配置/机密。提交前核对索引原始blob、清单、机密及大文件，仅dev提交/附注tag v6lite-batch22。具体commit在交付答复回报，不自指写入被其包含的报告。

下表不含本报告、B22manifest自身及最终独立核对收据，避免循环；JSON manifest额外加入本报告hash，条目数=下表+1。若有independent_delivery_review.json或delivery_receipt.json不回填冻结报告/manifest。

下表 **200项**；code_sha=d42320d23de79d6e。

| 文件 | bytes | SHA-256 |
|---|---:|---|
| `docs/V6_LITE_BATCH21_POWERSHELL_ENTRY_2026-10-08.md` | 9285 | `d1a775ead637e9623f80798e6f388ac6d9443615f5d8f2b3a7aa7b5b99b5c30f` |
| `docs/V6_LITE_BATCH21_RESEARCH_AND_OPTIONS_2026-10-08.md` | 14674 | `c9008b8d04940a88add4077fec3a1fa442c04084afa797caa9f9277ce01e4b33` |
| `docs/V6_LITE_BATCH22_INSTRUCTIONS.md` | 5802 | `14206477aab33bdc242e331d88745695d0f828e8c9744d49eaa20df4ed43c18a` |
| `docs/V6_LITE_G3_RUNBOOK_DRAFT.md` | 29085 | `6b84acb748a5f62f753bb77851195d96655b18c2a5b3070aff826bc1d91b0fbc` |
| `docs/batch21_evidence/argv_candidate_red.log` | 1383 | `ba9da70c00608ca024d32c752c271218d0f8751650d86afbf182c4470c1636a6` |
| `docs/batch21_evidence/console_shutdown_results.json` | 7268 | `00584f169ef483c65f615f25b77dfb7bbc919cf5390e19877c0eeee3516de2e7` |
| `docs/batch21_evidence/entry_focused_green.log` | 101 | `1327f658fb19a0e168bbf02b36e910b46cd1ccde871ca6ae1879b32943dbf392` |
| `docs/batch21_evidence/entry_missing_red.log` | 4230 | `7100a08d8051d28e6da35d365760b5c5ac71cbe4f63493101abaf015c1c16977` |
| `docs/batch21_evidence/fake_cancel_fixture_console.log` | 662 | `50e463104b37b53c9a3ebb015a808647f33da47da65ac779ea409a8fbfdca324` |
| `docs/batch21_evidence/fake_cancel_fixture_events.json` | 977 | `b9d7d20305d5afd8a0ba4aa2ae15995ab1bee07587c39ad23124ff22a557bb7b` |
| `docs/batch21_evidence/independent_review.json` | 1712 | `db335193027f82a771bb38b87d66ec640fe6da2ebc51643cd06df989e1bd3203` |
| `docs/batch21_evidence/official_sources.json` | 3790 | `b0f88c563d035eefbaf985e8293258d539e9128866a74a63df1f6191628b4d4c` |
| `docs/batch21_evidence/old_out_host_red_console.log` | 14 | `b066e576246da79053dfc142c35410774e995ee180713be164b9db45e61d712c` |
| `docs/batch21_evidence/old_out_host_red_events.json` | 165 | `0052d7fd170e880b0b40b1d18f8885555ab49518db09a72b8c2fe15dc8200e98` |
| `docs/batch21_evidence/old_out_host_red_prompt_events.jsonl` | 44 | `749f9fd838ebe5695319e512c65f903a74b8fed57eb88b88a27db21df504b5e1` |
| `docs/batch21_evidence/old_out_host_red_signal.json` | 183 | `d5f9a6e208153e2f65825ae980964b13acd5fbb0516a46a29ed41eda89f465a3` |
| `docs/batch21_evidence/scripts_final.log` | 195 | `96b725e98c0e73aae3a1f6c988d332a49df611fc56a2266d573a68b127ee1b53` |
| `docs/batch21_evidence/scripts_final.xml` | 18874 | `861790be4928f289177cdc2f2bfca43ecf32cca3e6d31dd8ec0957c577cf7a24` |
| `docs/batch21_evidence/sha256_manifest.json` | 4268 | `d869edb420a3103b17fb30c0364052792c375b417c7b1ad4feddabbbe3e3f667` |
| `docs/batch21_evidence/validation_metadata.json` | 1513 | `939579ef87097bf796693edd6d7578eec015a4640074db368fec6b3ed78084d5` |
| `docs/batch22_evidence/baseline_cancel_namespace.log` | 2124 | `b9a26ccefd7128b003d2db8b904673002cbe2dfdd85060b84dd653877084d64d` |
| `docs/batch22_evidence/baseline_cancel_namespace.xml` | 3035 | `90f94f85bebb8f6a6f1e0ae27eda57948df4dcd8a8b048e258a57355e56b7e67` |
| `docs/batch22_evidence/baseline_cancel_namespace_metadata.json` | 379 | `99937890b4fc2d554f828ee10feef00abda86438c1785051c422108d570db6c8` |
| `docs/batch22_evidence/baseline_config.log` | 19714 | `fe69da5df3df210f715aa5fdcd7216ea900fbbff48a0b51ad87e89d714217754` |
| `docs/batch22_evidence/baseline_config.xml` | 34066 | `e3e870d3402b502a0ee3bbd01597d1b87eaebe969dd4f575a6bd746601566098` |
| `docs/batch22_evidence/baseline_config_metadata.json` | 378 | `308aebd55bbab10294be7effbd470b126a66124e46303bc7e25a7525d75204d5` |
| `docs/batch22_evidence/baseline_lifecycle.log` | 49278 | `cafd7046d77910e63ce864998a84f573088b0d35d0b4ff2636a6b42359f34982` |
| `docs/batch22_evidence/baseline_lifecycle.xml` | 65077 | `a985fb4bd6cf1412441e1c921062309e1e16db1f571437a3aaeebd44b41c90e4` |
| `docs/batch22_evidence/baseline_lifecycle_metadata.json` | 378 | `7355e0d5c59ece339d44bc29af87af48c14b9dc3f0a37f893f8cf34cb41f347a` |
| `docs/batch22_evidence/baseline_pending_audit.log` | 5839 | `98f537c92630c5a9d2879cc66c03ed503b3cd7cd85b52c9f1b0af3c04c679d45` |
| `docs/batch22_evidence/baseline_pending_audit.xml` | 3909 | `5e36d3bf8f3829a94e115729ccf6d31bed77566c031d104d9d2b4857ffb58f3b` |
| `docs/batch22_evidence/baseline_pending_audit_metadata.json` | 411 | `159d7619d839c7164302588460d58eb7b1fbda5c6ea76e80300c2a3eb37bd184` |
| `docs/batch22_evidence/baseline_schema_audit.log` | 44417 | `5d94a58dbef324995a1f812994d3424375058b886f0f1795fb1ec21d953b7828` |
| `docs/batch22_evidence/baseline_schema_audit.xml` | 48501 | `72b226da80672649d60d7ae7e3935b5002c69a15cb6fd348013d183e35cce14e` |
| `docs/batch22_evidence/baseline_schema_audit_metadata.json` | 447 | `a9619942d21e18d5a2ec720557a4d8c469b5c50620c2f37c61900352afb93d5a` |
| `docs/batch22_evidence/baseline_snapshot_cancel.log` | 24378 | `b8949985ad6600972fbc7fc57f8c655d43dfd87268398cdd57f320ee7a173a3a` |
| `docs/batch22_evidence/baseline_snapshot_cancel.xml` | 27612 | `c9252eecace9fc30432e69a50c1a8a59152159da86af5117a2c4e6a3024b1c30` |
| `docs/batch22_evidence/baseline_snapshot_cancel_metadata.json` | 413 | `9b87234802ba595c02dd604a96b0a9f58eb841f1d6eb05e4bf72dc517a4abf8d` |
| `docs/batch22_evidence/baseline_trigger_incidents.log` | 11953 | `efb350d55b3996be5b110f0e86dace0d2b08f169e68c9e3507799fc395992585` |
| `docs/batch22_evidence/baseline_trigger_incidents.xml` | 8550 | `182ccf61aa180f68762f1d168fe5d36a848f5d657efed4534226c5514f9474e8` |
| `docs/batch22_evidence/baseline_trigger_incidents_metadata.json` | 397 | `5f2dc53a828db34dc58fab239d8b43567295a62e0cce0c4bc939df2f73072fa4` |
| `docs/batch22_evidence/baseline_trigger_replacement.log` | 3967 | `24ef602d79fa5a50c434d0d209bd8c544fd356ef178cf440109bb6fb134221e2` |
| `docs/batch22_evidence/baseline_trigger_replacement.xml` | 1969 | `ccab300c294460d82f79422d297e637cd0c2a5ad1a8f2a7811f74b58a7609c88` |
| `docs/batch22_evidence/baseline_trigger_replacement_metadata.json` | 377 | `cac80f4fc49831b2a2ea664504cf05c5910c968a9c8021e66bd38b172e7fd6e3` |
| `docs/batch22_evidence/build_report.py` | 18900 | `4dc87f1b7875927c6fc24a4b7c956816edbfaf7364e9e756c5b002444384cace` |
| `docs/batch22_evidence/candidate_cancel_namespace.log` | 2124 | `b9a26ccefd7128b003d2db8b904673002cbe2dfdd85060b84dd653877084d64d` |
| `docs/batch22_evidence/candidate_cancel_namespace.xml` | 3035 | `0df7bededbd767f1a42267e22b3c3bc47c1f0209e4b0399a20ee0221a7d5cc41` |
| `docs/batch22_evidence/candidate_cancel_namespace_metadata.json` | 399 | `a8685c6bb011c175baf8957885e27fc637237f93a0ee4580893aef6278ba197a` |
| `docs/batch22_evidence/candidate_pending_audit.log` | 5953 | `e584a5260a792ba7fc2b187fbd980cd18270f8da08aedbd7ba76c0e27bce47f0` |
| `docs/batch22_evidence/candidate_pending_audit.xml` | 4033 | `36da33b89f916b630c7ce96eb168602c608c899be97c4ce1aff6889df8276aa4` |
| `docs/batch22_evidence/candidate_pending_audit_metadata.json` | 418 | `a6d06014f7ab85c58a86733c46e57099c179e64d22387f965a52c43be5ca4942` |
| `docs/batch22_evidence/candidate_snapshot_cancel.log` | 23000 | `ba5bc14408924a762bd7fe625ea85b5e5447e32ea233fede2a8724bb7369e605` |
| `docs/batch22_evidence/candidate_snapshot_cancel.xml` | 27761 | `d779b9f0ba9ac70dcf00398409a9e230f8bad144ab3639885a778d9aba809334` |
| `docs/batch22_evidence/candidate_snapshot_cancel_metadata.json` | 438 | `94fd5642663092ae84314a68b1deb9286b23e9b949105a4cbe351558f7e1c001` |
| `docs/batch22_evidence/candidate_trigger_incidents.log` | 12058 | `8b2621ee119e968fd262ecdafcadac7469639fb3d9adc848ff5fc4d5e4d1aa83` |
| `docs/batch22_evidence/candidate_trigger_incidents.xml` | 8550 | `0ef8d0ea19febdaabebc0ef3b39cf996880b367ffa0445d624b19ef1a2739263` |
| `docs/batch22_evidence/candidate_trigger_incidents_metadata.json` | 438 | `5dbbb3f81c183a9315ace44c5f43ab8132db6d578966aff36f25a10659270551` |
| `docs/batch22_evidence/candidate_trigger_replacement.log` | 4272 | `b198b5c54a8440699f1d7aa876238866ce6217a59c8e0a53bb9bc3e3989d4d5b` |
| `docs/batch22_evidence/candidate_trigger_replacement.xml` | 2582 | `1461e6c68a1f7ec6d3dd8ecd487e17adf2a732767527457cd476327479043bfd` |
| `docs/batch22_evidence/candidate_trigger_replacement_metadata.json` | 393 | `c3129cc9200b47166c2e4a7cd7949cc3002b79f3afa0e4da3bbf2122d24f9066` |
| `docs/batch22_evidence/cards.json` | 20725 | `2ffc1f3d5fcaf020a0afd153dd40dcb8d9add763317511b1647eb6c1b0f5cee2` |
| `docs/batch22_evidence/cards.md` | 15892 | `923c93eb37bd345fe3c6afadd8e9e8a809f9144366a21f22b54516af35eefab6` |
| `docs/batch22_evidence/cards_generation.log` | 9955 | `54fdddcfea2ce2f27652d8991c1e60c82288aa305b07eca6a83198383512a85b` |
| `docs/batch22_evidence/daily_sample.md` | 1566 | `dd61ece1c0d8d5a5e4b0829687dad996fb72221ede70bcd84c58272167c20ae9` |
| `docs/batch22_evidence/development_regression.log` | 2223 | `e411dcda93190386ae8b413c47f3491364489c66b065722a4929ce82e59ee0e0` |
| `docs/batch22_evidence/development_regression.xml` | 164233 | `f09509dfaa7f97d80fd1c06f45313eb5d599a4cdc50cb7f1e62df9a145d6dc74` |
| `docs/batch22_evidence/focused_all_final.log` | 346 | `8b2306b4b1ef2a80342fe938e3ed2773f5377b6b4cad0eb58006307231e3d036` |
| `docs/batch22_evidence/focused_all_final.xml` | 36912 | `d125acf069db6e1ac537955d746a36f7527aab8924ac95d47295809dab63509b` |
| `docs/batch22_evidence/focused_complete.log` | 346 | `267ed8e829805ab507decad2c2da825d69f2a9a32375f6dbfc4a991356889b92` |
| `docs/batch22_evidence/focused_complete.xml` | 38506 | `499a6d5de261aef43a7034743975b9ec9a9f2c128f811e48eeacfcd2c8315b27` |
| `docs/batch22_evidence/focused_complete_metadata.json` | 2009 | `3ef0ff5b6562fc234c8ed83cf3fbc6e2a772b8b3a444c92a8d3bcef60777698d` |
| `docs/batch22_evidence/focused_final.log` | 264 | `3acf0d81e94a5f1f3a8540c5fbb282fc96809572923ae96c5b60e5f9dc367ee1` |
| `docs/batch22_evidence/focused_final.xml` | 31706 | `bffb3b0d61b6b2949dea434393bb5feb2727d58d18e3f24515273cf26421bf66` |
| `docs/batch22_evidence/focused_green.log` | 264 | `304850b6ad068cfab5d17acaae6d5d7a5596372d6993c8cce94597f01cb462e4` |
| `docs/batch22_evidence/focused_green.xml` | 26755 | `7ff3c54122138dc5a0603f348e2b502003a9760dd3ff6adffcbf6f5b33f1ed78` |
| `docs/batch22_evidence/focused_initial.log` | 1656 | `d3b27efd9eb4b782062d6cec78c2ac53b4aa19f4f5cc2eee65b01323c8a5cbc3` |
| `docs/batch22_evidence/focused_initial.xml` | 27827 | `3086e06b4e22d32e0e48f8bb0e96694f5bdcc1310ef125c1aebd5c20fe145b20` |
| `docs/batch22_evidence/full_1.log` | 1584 | `d11edb0693866627d3499d82140ed29ae7d67a282441f3883ca9c3adca84ac3d` |
| `docs/batch22_evidence/full_1.xml` | 193981 | `94760d963a3a7dfa4c97d9e6086e68819d13a478ff57b0df4b6ddb587ccdda70` |
| `docs/batch22_evidence/full_2.log` | 1584 | `6e7352dd90c77f6ea4ec1b2f17df0b03432d06663b937d88978cbee88c58a22b` |
| `docs/batch22_evidence/full_2.xml` | 193981 | `dbe3fc89b11fb4b3db100bac4dcaed0ec99bdb8458ebdcad46473fb4b52cd013` |
| `docs/batch22_evidence/full_3.log` | 1584 | `a75fd9823d08ab13cfc1fdbafbdad803474f73576c5a313f08a7acca99086a9e` |
| `docs/batch22_evidence/full_3.xml` | 193981 | `8388884a440efbaf57b0415c7c32dc6b7fed862402c25a601eafe761f6bf7d73` |
| `docs/batch22_evidence/full_validation.json` | 13879 | `60ca1f7be7cda31a5d5d6e2550b09e37741d5b563509c711b089650f603ee027` |
| `docs/batch22_evidence/generate_cards.py` | 3446 | `fe6122b71fdcdcfab7d78bbf664732ccd7bff3f8dacb8b8a18958992990f65d9` |
| `docs/batch22_evidence/identity_trigger_final.log` | 101 | `e848757bac0e2626c3d7e78ce45a5c4bfd3221e82555d5dd6955881f22ee2927` |
| `docs/batch22_evidence/identity_trigger_final.xml` | 2856 | `e04870f62d920071bb6abe8a3573c6633b2b801655670e110f4672bbbf444788` |
| `docs/batch22_evidence/independent_source_review.json` | 6676 | `e21e880104dc298447571acd4b3c59b29860f99ac5334cf278372c860f00610d` |
| `docs/batch22_evidence/precommit_preliminary.json` | 12669 | `9c27ad6f2927cd54faecdda185b31f8d41626fab89d62580a85a791f803380cb` |
| `docs/batch22_evidence/preflight.json` | 555 | `84db6c5efbe2e59f0519565fe42e6c52393807581c9f7b913650a026e4a7c153` |
| `docs/batch22_evidence/run_final_validation.py` | 3319 | `e5a252ed5ef978f67ad41d2989fdaab748057e6e197a609f553352b4b7a4b294` |
| `docs/batch22_evidence/secret_scan_precommit.json` | 424 | `21cb4923e5d3b63ead9238225c7e5af12e4bf1890552681fcd2073458269e821` |
| `docs/batch22_evidence/slippage_sample.json` | 1323 | `280a2c4566a38b129cd6a44dd00d73037a3a1b0dacf2e269c083b1c3e84efe7d` |
| `docs/batch22_evidence/trigger_final.log` | 4192 | `30ccc304eb3bea4053973234d4ccdc4c6535ff2ad0f64a6e3f51ef34b9fee305` |
| `docs/batch22_evidence/trigger_final.xml` | 3371 | `e39bbbb2ce7452f537960bfdb24473d0a56f827b86616073089e46736ce5cdef` |
| `scripts/tests/console_shutdown_child.py` | 3719 | `3732ac606a911fc34f4c64a631f7672d55b5e8b96eaf50c8346e9daa6518be6a` |
| `scripts/tests/console_shutdown_probe.py` | 8641 | `77bbeba33b1f4d94027cf30dddac46e845edd9e7a9d3672f20af63e918e99114` |
| `scripts/tests/test_batch21_entry.py` | 3716 | `b367e6cab0ad80d8901e103ba1459b08a0ea4555d8506959a7630963ac18e470` |
| `scripts/tests/test_v6_launchers.py` | 35142 | `fff766c38064b7ad5421eadd9639f363e06cfa5a6e6ecb1448a4c22c002b270c` |
| `scripts/v6-user.ps1` | 39 | `55978f6fdb9f50c2dc66cf5731436d675fa7b95021d8e7ac3911d2fe2825f9e8` |
| `scripts/v6.cmd` | 161 | `e6303971e31af8b57e050506ddae1c71516efc3f7c523850438fba3028fcc6a8` |
| `scripts/v6.ps1` | 16619 | `7a8a0794cee4efd5b4d151d07ff51a8ba3a0c7a7c4e814ba0073c316d8b3fb15` |
| `v6lite/README.md` | 13718 | `4ad81212d1863b4fc11dc87d8bc8c32ea272e95563095243292ed615ced31fd9` |
| `v6lite/__init__.py` | 313 | `26b92254539398e1389a76fc1ab6dc45b4ce00f2ce0226de3299c957211dad39` |
| `v6lite/__main__.py` | 26616 | `320fbc71bbeab5d4dc03956687e85f0b4fd354137d8ce8173c847b788d40c8f6` |
| `v6lite/alerts.py` | 53232 | `4b422fd4f2c1e2c3297de04d6193cfdd80adb4dadeee428516fdae171ff00657` |
| `v6lite/approval.py` | 12709 | `70a3c24325b95216bb307dd261b60ccff88f0f13da3e2f94d6344e92f07b41e1` |
| `v6lite/broker/__init__.py` | 96 | `2a048895705e5b40481afb525cc33f3abc42fea7fa6c05c357d620c17a7f9d24` |
| `v6lite/broker/base.py` | 3727 | `f6e52937d5e10cb0d1853ccfbce35cf47eceb74080ae6121a34577e3bad11e6b` |
| `v6lite/broker/fake.py` | 16149 | `04af71e38336c9ac2d6002c404e44d9d7584ce607b020e3cd52eb46010f395b7` |
| `v6lite/broker/ibkr.py` | 65257 | `7b16ee83e52d4b0ffbaaf379f8a3e4568ebb6fc457b8c82525071a7290cbe9cb` |
| `v6lite/clock.py` | 910 | `0051d302e8e88871411677fbab6d6969a07b45fed279aa4b7e135cab332f57e1` |
| `v6lite/config.py` | 18144 | `576506de1ea47b4d67112fdd935ab86494f1163e6f269609c8b380ec31c35904` |
| `v6lite/engine.py` | 181676 | `2080438840897966f60150c515dcf8832f903d00b5ebb9bb2f83ed4b6634571a` |
| `v6lite/examples/live.toml` | 2975 | `cac62332293b33c72282c3f41d9c9c860dfc610bc2ff457f57178ccf52c44afd` |
| `v6lite/examples/paper.toml` | 2867 | `314e3f3310c0e2c5c5a2cf4ab831484d7d6ab5525b998fc0de59e1637b750a2a` |
| `v6lite/examples/paper_delayed.toml` | 2966 | `3f941e4d966ca6b0513ecbcaafa9a0ded7a6985aa4f442c9f4ff8968aec51289` |
| `v6lite/examples/paper_g3n.toml` | 1270 | `f61ad27cde0526c57dc6f483f7d0c2c0bdd8ff23d7c0ceb061d01646913715d4` |
| `v6lite/examples/paper_test.toml` | 949 | `0796f207da9aafbda17f73a672d068ca84b4f768184b30c2e817c27a8c3e83b6` |
| `v6lite/execution_time.py` | 3269 | `6767aee2d823eccc2387106f803b38521bce94e71ec082fe6dd021918ee9f300` |
| `v6lite/exits.py` | 3927 | `1de7eb5637ee3c6abd1784570846d0edf8f91739ee97e2bb6e0b6651e34de093` |
| `v6lite/market_calendar.py` | 3925 | `1499dabc11bf828dc6f94b82f5bcba21de2c4e6977829c26bad25737b3b425c2` |
| `v6lite/models.py` | 6706 | `ab7dc7358f8c4c18d302370c75a94fe16cd2f86cd40075ee368cccb53ca1f5c0` |
| `v6lite/protection.py` | 543 | `61fa2bdd943b2c60717706b3494fbfb95dca6f2789d2cb9c74fc82a71b2d197a` |
| `v6lite/protection_engine.py` | 98076 | `e6f7d9585daa26717d58643b4b1ed2d0c98b721de05f9c1722aa7de0cdd8476e` |
| `v6lite/protection_incidents.py` | 8547 | `19bc55c97e91bcf05ef5afd208fa5515310c0070e3d80b67d476298cbfd9e450` |
| `v6lite/reconcile.py` | 2610 | `4c1d4fd02dda0e1fe9a1c6087471dd6cb75df314dba6004a8d977de958fc26e1` |
| `v6lite/report.py` | 24232 | `19548b2bb72b4cb35ad21017e7506d46b50aaf2dc7874ce6e5586cea0a1a37a2` |
| `v6lite/risk.py` | 11077 | `abc521f671e878a0ab5690e1fa178dc872d29b5396b86eaeb5f016be1594c687` |
| `v6lite/sizing.py` | 3294 | `c9b9cc4a116a7f61d9e660dd885d280ec2a9262671f2c3c8ab5c1515724e171e` |
| `v6lite/store.py` | 74233 | `8875e9f10d759ab2b074f257386e6ba9c6c937121aa0ac11097deb40c3b5cd7d` |
| `v6lite/strategy.py` | 8300 | `9817647ff31f7d3e2f1f11f6657086b552fe4ca09346da3338421894ce1c979e` |
| `v6lite/tests/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `v6lite/tests/conftest.py` | 209 | `aec9d5da8f1b9211fbde6a29ac59fe898732bda3c2f99ae11aa72c49ffd9b6fb` |
| `v6lite/tests/helpers.py` | 5415 | `0c28d019b52b61f5d0b477bd7b202c409ff6761a4b4830b70c77667a3d9787d6` |
| `v6lite/tests/test_batch10.py` | 19369 | `5b875c8df97861e22427223e7065a74bddf04a5da1c7115b431871369d177c47` |
| `v6lite/tests/test_batch11.py` | 10273 | `fb7f26a0bd1969400f2d769a0d65f4c7d3a18f0162332fd46ca9f1ed99c260f8` |
| `v6lite/tests/test_batch12.py` | 25124 | `3dfc69a4f811549cb32c9c186bbfed3d24f7c1c956c3c743d9b28f6996037cfc` |
| `v6lite/tests/test_batch12_v2.py` | 20802 | `5691a234501cd71b85e3098e0cf6da2a74f2d4f5ca3eda2797a8abe17fab9af0` |
| `v6lite/tests/test_batch13.py` | 18609 | `12a3ed033079f129e7e70f610be004c498ea6f6f0283473f60b2eb80ad819140` |
| `v6lite/tests/test_batch14.py` | 16354 | `5087c53709f4208d5dc4fbe9f74b100d50ebfb2060ccf9c9834358d2c3c7b257` |
| `v6lite/tests/test_batch15.py` | 71363 | `5f8d69e27230857b5b726e3960ba1a2cf09178cb925d2cb85ca98b209857e3d1` |
| `v6lite/tests/test_batch16.py` | 28722 | `f5cd54b73510b37facd6202cbd414baf47754166088512d698a8685fd10bebb5` |
| `v6lite/tests/test_batch16_card_deadline_copy.py` | 1932 | `d9c3c8f080748838a81392b1a12ca03347685a1be4692ae9159471ff2cc56a51` |
| `v6lite/tests/test_batch16_f5_copy.py` | 15179 | `eca4e85d176cdb5a726bc4e5bec2f2b6bd6869ff726a6b647c16548b4ab94bb0` |
| `v6lite/tests/test_batch16_f6_account_evidence.py` | 7194 | `6d97cec645009fd6d90c223cfeb5c902866701f75c01f91a604caddd3a0e863d` |
| `v6lite/tests/test_batch16_f6_global_mismatch.py` | 2874 | `86c542de449c2adab7cae4d2bc476e5108ef7ef14d9c2f45686a2404a3b9d478` |
| `v6lite/tests/test_batch16_f6_release.py` | 10603 | `15c23d799539945057095f56bad2e8ba8a32974383eb8d778fba0f57ede2cb96` |
| `v6lite/tests/test_batch16_f6_safety.py` | 5402 | `9e2464cc21430b8b4e27dd8982b1a5aee0b554a5e0698691df3156715ad65b9c` |
| `v6lite/tests/test_batch16_f6_steady_fairness.py` | 2893 | `e7ae2a84c08b2adadb1aa9994e835b5504db581401eecbf2bfd14678e984915d` |
| `v6lite/tests/test_batch16_f6_steady_timeout.py` | 2401 | `733347eda5814cbd2a14f71a717c88274bdd51376cada2c9bceeab3fd2908c48` |
| `v6lite/tests/test_batch16_f7_price_flip.py` | 4495 | `b2f9cf68a543999d08ab0567e6e6a5663f257d2525c48430f953de8bacfbc5dd` |
| `v6lite/tests/test_batch16_f8_backoff.py` | 2126 | `af5c1b6c62872c84536b222255c9a3b4229e2d7bf4ce1c4b55b1d4a780aa3539` |
| `v6lite/tests/test_batch16_gap_evidence.py` | 26359 | `328269e2c8fa8a0547d1d16a5904a4d6cc5462f13b093b9e6c09eb87bd4be8ba` |
| `v6lite/tests/test_batch17_alerts.py` | 10542 | `dfc6a85711add46c7a6e8b966c3f75f38f2bbbc7bee1b3f827882d3d9ddba5ff` |
| `v6lite/tests/test_batch17_equivalence.py` | 9719 | `ecb49e23e9cbcd6ea2803aacb4b649d8b8794b1c79e65f266141c655efdfca8a` |
| `v6lite/tests/test_batch17_g8.py` | 5126 | `4e21318f45dc6293fc4fbdc0f1c5be7c9d845782bf405fff8c6a51504bae5875` |
| `v6lite/tests/test_batch17_heartbeat_account.py` | 20900 | `5ab7bafaddb4ab15e16c275b45061079c46c3d642b866a91ac55f85a75c3f6de` |
| `v6lite/tests/test_batch17_l3_recovery.py` | 15834 | `736891fd02d99b2337fffca283fbddf53de7d8fce8294d6a089175cbd317cd3c` |
| `v6lite/tests/test_batch19.py` | 10690 | `cefff371b7c9101fff87583517d11a8f81675f0a549cb2007c19099b3d9e239c` |
| `v6lite/tests/test_batch19_funds.py` | 19657 | `f9c1ae564a76752edcb60b3293eec5a4b2ac15657b4e34258ba814cf4a78dde4` |
| `v6lite/tests/test_batch19_protection_equivalents.py` | 13413 | `12def834c0ef3a32bf71331473a47daae9e7d2b4309347e177784421cdeec303` |
| `v6lite/tests/test_batch19_review_edges.py` | 7207 | `262a24defa92162552f14d80dfc1eb45d673e7a538a7c0ead745b7649515f7d6` |
| `v6lite/tests/test_batch20_card_provenance.py` | 1430 | `59a6803e2e773af4a43de541dd3851f25dcf9d260c934f3f18d705b25d5db64d` |
| `v6lite/tests/test_batch20_execution_time.py` | 20596 | `658d55ca04fdcfbc9a6f5810871eeedf9439741752fbaa7a3b70836838431242` |
| `v6lite/tests/test_batch20_protection_equivalents.py` | 51774 | `0cbd0e83af3982c6ae80e7b0871462c6649e0e25921cb4c2e5217d0502ff39a4` |
| `v6lite/tests/test_batch20_protection_incident.py` | 35215 | `b36f7402ae7f9d3f14fcd4bee4b4d2a74131385ef2f6b7d4478cfb7c95018a2a` |
| `v6lite/tests/test_batch20_template.py` | 810 | `8c6ed584a5f435fe81542df99ce8c7a1d040f342583b733fe65356af352b5230` |
| `v6lite/tests/test_batch22_cancel_namespace.py` | 3080 | `b513c130a9045147fa3f77c3afb10d1140b17207995143866523fc9de83c532f` |
| `v6lite/tests/test_batch22_config_broker.py` | 23349 | `f1023d247ae9e11ae979d9341597f4779c76740d17b7602285b3cd3e8690ad93` |
| `v6lite/tests/test_batch22_pending_stop_audit.py` | 9880 | `f0bf9fe05cee399ca62892222ef09cd069bf8dc27a7179978e7434b7235e0696` |
| `v6lite/tests/test_batch22_protection_lifecycle.py` | 36441 | `bee3803db93e5e6bd235ff43f3c0cafb9729da87d6deb986fc41acd6b7594b5b` |
| `v6lite/tests/test_batch22_schema_audit.py` | 29777 | `374ae51b92eee5ea1e48ff4ca145422f1d106b0d33cf8f94b57d6261b24c7927` |
| `v6lite/tests/test_batch22_snapshot_cancel.py` | 10065 | `6531f1b9c3398e1cdbd0c7148e610b360846ff78a11dd3e6406e2cb92aa3286b` |
| `v6lite/tests/test_batch22_trigger_incidents.py` | 11026 | `a3c1ad8c925c53ce95577de7ee707093b98ff74468ed13cda66da7e30cc8db17` |
| `v6lite/tests/test_batch22_trigger_replacement.py` | 3263 | `80c284c3bd1b41ec47c841f344ca03726c8da9569433d0dfafec805bf861a90c` |
| `v6lite/tests/test_engine.py` | 17668 | `bb8967a536a8d187fc9f1d742be17c0b4b90e193ab52731ac06d6bff8a08f461` |
| `v6lite/tests/test_g2_day1.py` | 10712 | `cacaf5b3115d4a298fe8b3fe065d66fe70277b8ee4cd5d9643f0e2fff0ff5f9d` |
| `v6lite/tests/test_g2_day1_selfreview.py` | 8081 | `fe84ef645d99f421c64f670aa7f883d16b5a24c8c15b4dd30a4aa8f035b5ea06` |
| `v6lite/tests/test_g2_day2_prep.py` | 2547 | `a0db6b981992a3cf20d9f959a679c608842f4619007331bae9b26908cee3ccb7` |
| `v6lite/tests/test_g2_probe.py` | 7222 | `ba38215d7a6946d60884d0e3dac99b641e7bcde058c4893b94cb9d62b6a6fe18` |
| `v6lite/tests/test_ibkr_adapter.py` | 6081 | `6b8c8a6c6529f7118df6735e4c976a50735197cd4610650fe181e4873a0c8f3a` |
| `v6lite/tests/test_review_fixes.py` | 22689 | `eb91ef957bfd13864c2c92a5da2cf3a29ffdc10c9f5c74fe751daa8f4caecdd3` |
| `v6lite/tests/test_review_round2.py` | 38526 | `7ab32142a9a0ee2fdb54853297cf6eae619ce28de4cac4dc646a8a8035660822` |
| `v6lite/tests/test_review_round3.py` | 15213 | `b5bf4e5c513e9d2310dc6404f147b633790cff0e894546e9176d6c26390a7427` |
| `v6lite/tests/test_review_round4.py` | 18605 | `04cf584aa3b7e13842c1e7608d03e5f888b7a714ea073001ce85281fe1eb43cb` |
| `v6lite/tests/test_review_round4_selfreview.py` | 13981 | `8cf2bba5d54e2e644bc016e68abfe87f0ecbe26b6e54d5a44a329c61e6dc7da8` |
| `v6lite/tests/test_review_round5.py` | 16770 | `c45a477f2bb20f021b3d8040b3be6360d78bb8c95f0ef7ffd2ba069fdf31eeb0` |
| `v6lite/tests/test_review_round5_selfreview.py` | 12064 | `03adaac71b3ea18095732064c51f6f4741400adae738c3cfb9f840c247ec4a8e` |
| `v6lite/tests/test_review_round6.py` | 10254 | `c6eabe6e953cc75ccfa7e4fa87625a86d677ef5dd2fca9975cd101b3403d6be5` |
| `v6lite/tests/test_review_round7.py` | 9101 | `bcb2baea638b6ba6c063aba7776aaf1137c3cd1df98297a08311cdaf899994a5` |
| `v6lite/tests/test_review_round7_selfreview.py` | 6455 | `a3589fae5996245f7246f1860875a847b6761ed9f8bab5a317ab5c7d9e5e86bf` |
| `v6lite/tests/test_review_round8.py` | 7212 | `c65ac1c396468aae3124ffcdc0f61b2ed483547ec1b0f47119f00950008b3736` |
| `v6lite/tests/test_review_round8_selfreview.py` | 7315 | `e927860575cb55769a910a0dfbced96b725922da6629010c894ec2d3c86b36c3` |
| `v6lite/tests/test_soak.py` | 6157 | `d84df42bcdc3ce0e505a8f930b2752ec2721cc307fb364388be210131c4fad47` |
| `v6lite/tests/test_strategy_parity.py` | 4014 | `732d74f85d6af63461dd78678b225886b5fb89875f08f1a492efed575b4cad2f` |
| `v6lite/tests/test_units.py` | 8846 | `75560304da5605efeedaf3f069f7e5749c51f406adf69b8c9c74dfe6e76c9e42` |
| `v6lite/tools/__init__.py` | 34 | `91701b7f5f90ca88fde3fcb4f91f1891bc99c6359d8b52c23e2decaabcc5a6b1` |
| `v6lite/tools/g2_probe.py` | 20555 | `e756a719f9f8c1cded3d1123f67fa0097b504680589319be40619bc5d7f6763e` |
| `v6lite/version.py` | 863 | `9aa52b23a3c8c08801f9d86c46ae1090a28c78dbde5d5b4b2923347bc74ece45` |
