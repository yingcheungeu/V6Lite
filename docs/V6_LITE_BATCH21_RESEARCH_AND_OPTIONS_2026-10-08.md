# 第21批：保护单规则调查与方案对比（决策稿）

日期：2026-10-08（PT）。本次先交第1、2项，并分析第3项；**没有实施保护单类型、偏移、配置校验或自动重挂代码**。第4项运维启动器修复单独说明于 `docs/V6_LITE_BATCH21_POWERSHELL_ENTRY_2026-10-08.md`。多Agent只读调查规则、算法与风险、Ctrl+C测试设计；root为唯一文件实施者，最终测试串行。

## 0. 已完成的第20批快照与历史裁定

第20批 dev 提交 `c5a39952635943dde5f1ae7f2a52da84c8327375`，附注tag `v6lite-batch20`，交易包 `code_sha=cfe16edd7a7e4d7f`。83项任务文件（2,513,003字节）逐项核对暂存字节，154项清单一致，未匹配实际webhook/token/private-key，单文件均小于5MiB；原报告SHA-256仍为 `8f6e63228d8040df58bbe821b87fb4106a0aec09c4e9d9096bece70f6766bb64`。保留原始pytest日志/XML字节，包括CRLF和pytest输出空白；源码/文档使用识别CRLF的空白检查通过。未合并main、未push，无关operator manual更新未提交。

John/Claude最新裁定原文：

> Claude 裁定：10/8 的历史 AAPL 状态不做强身份恢复。John 在 TWS 手动平仓并确认 0 股、无在途订单后，将整个 %USERPROFILE%\.v6lite\paper-test 改名归档为 paper-test_archive_20261008，作为证据保留；下次 G3-T 从新状态开始。Codex 不碰该目录，也不需要写任何迁移或恢复代码。

这条裁定替代第20批报告第4节中尚待裁定的历史恢复建议。**不声称John已经平仓或归档**；本轮没有打开/修改/枚举该状态目录，没有迁移或历史恢复代码，也未改运行仓库或John配置。

## 1. 规则调查：确定的事实与未知

结论：**未在公开官方文档中找到美股 STP LMT 限价距止损价的通用最大距离**。不能确定它按固定百分比、价格档位、路由/市场动态规则计算，也不能认定这次拒绝就是TWS可设置的某个值。未知，需Paper实测；Paper样本接受只能证明该组合当时接受，不能证明通用上限。

| 问题 | 确定的依据 | 仍未知或不能混用 |
|---|---|---|
| STP LMT参数 | API明确 `orderType=STP LMT`、`auxPrice=stop`、`lmtPrice=limit`。[官方Stop Limit](https://www.interactivebrokers.com/docs/general/order-types/stop-limit) | 该页不提供两价距离上限、分母、边界包含规则或价格档位表。 |
| TWS Percentage / Number of Ticks | 对限价相对提交时市场报价的预防性检查；SELL用bid、BUY用ask，可留空。[官方Preset说明](https://www.ibkrguides.com/traderworkstation/define-order-preset-value.htm) | 比较对象是市场报价，不是stop。改变这些设置是否消除10/8服务器拒单未知；本轮没查看或修改John设置。 |
| API override | `overridePercentageConstraints`覆盖TWS Presets预防性约束。[当前Order字段](https://www.interactivebrokers.com/docs/tws-api/ref/order) | 不能据此推断可覆盖服务器两价距离限制、交易所规则或最小跳价；不建议用未证实的override绕过此次拒绝。 |
| API Precautions | 官方有size/value警告的API绕过选项，另有独立的market-cap选项。[官方API设置](https://www.ibkrguides.com/traderworkstation/api.htm) | 它们不是“所有价格检查都可跳过”的授权。 |
| 最小跳价 | minTick/marketRule描述合法价格网格，可能随价格/交易所改变。[官方旧版价格增量说明](https://interactivebrokers.github.io/tws-api/minimum_increment.html) | 网格档位不是最大stop–limit距离档位。此旧版文档已deprecated；真实合约网格须另行验证。 |
| API美股GTC是否受检查 | 10/8已归档native回报是AAPL SELL STP LMT、GTC、API client132，且收到明确的距离拒绝reason。见 `docs/batch20_evidence/incident_log_readonly.json:14/17/19/20`，对应原日志238/249/251/252。 | 至少这个组合被检查；无法外推所有股票/路由/GTC的共同阈值。GTC有效期本身不证明免除价格检查。 |

10/8实例：stop335.77、limit325.69，差10.08；以stop为分母，实际舍入后约 **3.002055%**，配置offset为3%。原始reason明确说限价偏离止损价太多、无法接纳该止损限价单，建议更接近止损的限价或IBALGO；**reason没有公布最大允许值**。本轮只引用已冻结的脱敏证据，没有重读John日志/状态库。可复核摘录：`incident_log_readonly.json:20`（原日志252）说明“限价325.69偏离止损价335.77太多”，未给数值上限。

错误109指配置的Percentage范围检查，110指不满足最小价格增量，202指服务器取消并附reason。不能把所有202都判为拒单，也不能从202号码推算价格上限。[官方Error Codes](https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes) 应联合订单身份、终态、reason和成交证据。[官方Order Placement Considerations](https://www.interactivebrokers.com/docs/tws-api/doc/orders/place-order/order-placement-considerations)

未找到的内容明确列为：固定百分比/价格档位公式、分母、舍入与等号边界、不同路由/时段差异、override对这次拒单的作用。相关旧官方索引中的“NBBO距离”数字也不能当作stop–limit上限，本报告不将其用于配置。

## 2. IBALGO / Adaptive可行性

普通Adaptive是执行路由策略，`algoStrategy=Adaptive`与`adaptivePriority`字段本身不建立止损触发。[官方Adaptive API](https://www.interactivebrokers.com/docs/general/order-types/algorithmic-orders/ib-algorithms/adaptive-algo)

**TWS确实提供Adaptive Stop Limit**：IBALGO→Adaptive STP，先等stop触发，再进行Adaptive限价执行。官方课程明确使用DAY，Adaptive不允许GTC；IBKR在2025-09-17及2025-11-20的官方回复还说明不支持作为IB Algo bracket的Stop Limit。[官方Adaptive Stop Limit课程](https://www.interactivebrokers.com/campus/trading-lessons/adaptive-stop-limit/)

不能因此直接写成 `STP LMT + algoStrategy=Adaptive + GTC`。该API组合的支持、实际wire类型、触发前后状态/子单身份未知，需Paper实测。官方支持价格条件字段，但也不证明与Adaptive的特定组合及跨日生命周期。[官方Price Conditions](https://www.interactivebrokers.com/docs/general/order-types/order-conditioning/price-conditions)

本地引擎触价后才提交普通Adaptive单，依赖引擎在线，不能算持续券商端保护。DAY Adaptive Stop Limit日终到期不能冒充跨日GTC；若选择C，必须另行裁定日终续保、隔夜保护及其空窗，现有L7已接受的短暂A/B空窗不能自动扩展为隔夜空窗。

## 3. A/B/C方案比较（由John选择，未实施）

| 方案 | 实现改动 | 配置与强校验 | 必须Paper实测 | 最坏情况与$5,000风险 |
|---|---|---|---|---|
| A 缩小STP LMT offset | 保留GTC STP LMT、强身份、TP减量/恢复合同。复核一美分舍入、实际成交后挂单及保本修改。 | 候选0.1%、0.25%、0.5%仅作Paper校准值；不是IBKR上限。未来上限要有确认的规则/验证范围，不能直接把3%改为另一个猜值并称券商允许。 | 各标的/合法tick/3股/正式股数、首次提交/部分入场加量/保本修改、正常撤单/拒单、重启/隔夜GTC及跳空穿越limit。 | 更接近stop会收紧全部成交时的价格容许范围，也更容易在跳空后不成交；剩余股数可继续跌至很低。不能保证计划ATR风险。 |
| B 仅保护改STP（止损市价） | 新增按orderType持久化的合同，提交/修改/native快照/退休身份/无limit监测/恢复/停机分支均需覆盖。ENTRY和TP继续限价。 | 明确 `STP`、GTC、auxPrice、outsideRth及触发合同；不能用假limit或放宽原STP LMT校验。无limit的类型应拒绝不适用的offset配置，不能悄悄忽略。 | 原生/模拟stop触发、价格/停牌/恢复、部分成交、TP加减量、未知身份、拒单、断线重启、GTC跨日及人工平仓。 | 触发后成为市场单，成交价可显著低于stop；没有价格下限。跳空/停牌/深度不足可产生大滑点。[官方Stop](https://www.interactivebrokers.com/docs/general/order-types/stop) |
| C IBKR算法止损 | 先确认可用API合同，再持久化algo参数、condition/triggerMethod、TIF/expiry、触发转换及可能的子单身份；需要DAY续保设计。 | 不能只加Adaptive tag。类型、算法、触发及有效期必须完整匹配，取消/恢复/停机不得把DAY保护当普通DAY单误撤。 | 独立单与bracket分别确认支持；触发前无提前卖出、触发后身份和余量、algoPriority、日终到期/次日、引擎离线和续保空窗。 | 限价算法仍可能不成交，市价执行仍有滑点；DAY到期风险额外存在。未知API/GTC组合暂不能满足当前跨日L7要求。 |

当前代码影响面（只读）：`v6lite/config.py`默认3%、仅校验finite且0<offset<1；`v6lite/protection.py`向下舍入到一美分；`models.py`的请求/快照不含算法/触发/到期合同；`broker/ibkr.py`构造GTC StopLimitOrder；`protection_engine.py`强校验只认STP LMT/GTC及匹配stop/limit。B/C还要审计按GTC/STP LMT识别保护的泛化撤单/停机路径。不会靠放宽这些安全检查实现新类型。

### 3.1 10/8价格用于候选演示

以下是同一stop335.77下的算术，不是接受保证，也没有向券商提交：

| 配置offset | 向下舍入limit | AAPL3股@337.74若全部按此limit成交的价差损失（另加费用） |
|---:|---:|---:|
| 3%（已拒基线） | 325.69 | $36.15 |
| 0.5%（候选） | 334.09 | $10.95 |
| 0.25%（候选） | 334.93 | $8.43 |
| 0.1%（候选） | 335.43 | $6.93 |

例如0.5%的单跳空到330且不回到334.09以上时，可能一直未成交；不能把$10.95称为最大亏损。

### 3.2 $5,000规模：资本与单笔名义金额分开

令Q为股数、E入场价、S已舍入stop、N=QE为单笔名义金额、d=1−S/E、费用F。STP LMT的L=S(1−δ)−ε，0≤ε<tick。

- **仅全部按limit或更好成交的条件下**：loss≤N[d+(1−d)δ]+Qε+F；未成交/部分成交时，此条件界不限制剩余持仓的损失。
- STP若实际成交价为S(1−g)：loss=N[d+(1−d)g]+F；订单本身没有规定g的最大值。
- C须按触发后实际是限价还是市价分别使用上述条件；算法名称不产生损失上限。

例设单笔N=$5,000、d=2%，忽略tick且另加F：offset3%/0.5%/0.25%/0.1%的全部限价成交条件损失分别为$247/$124.50/$112.25/$104.90；STP在stop下5%成交时为$345+F。美股多头若一直未成交并跌至接近0，损失可接近持仓名义本金，不是计划stop金额。

若$5,000指**部署总资本C**，N不能自动取$5,000。当前sizing按min(NLV,maxcapital)、ATR预算、单仓比例、订单cap、剩余资本与费用取最小值。仅用源码默认1%计划风险、50%单仓比例，例设E=100、S=98，Q至多25、N=$2,500、计划stop风险$50；offset3%全部按limit成交的条件风险已是$123.50+F。当前qty_risk不把额外offset损失纳入计划预算；未来是否调整风险计算属于单独决策，**本轮未改**。这些是条件演算，不是John配置或成交承诺。

Paper的stop/复杂订单为模拟，主要使用盘口顶层成交；可验证接口/状态机，不能据其结果保证Live尾部滑点。[官方Paper限制](https://www.ibkrguides.com/clientportal/aboutpapertradingaccounts.htm)

## 4. 偏移上限与一次自动重挂（仅分析）

**当前没有经调查确认的通用offset上限，因此本轮不添加一个猜测数值。** A或有limit的C，待John选择后应验证原始规则及最终舍入两价，按已确认范围fail closed；单次Paper接受只作为限定合约/路由/TWS版本/时段的证据，不能外推为Live通用上限。B的STP没有limit-offset，应显式标为不适用并拒绝不相容设置，不能把旧3%配置静默带入新语义。

“明确拒绝后自动更保守参数重挂一次”仍未实现。若未来授权，至少必须：

1. 完整强身份、明确价格带reason、原单确切终态及完整成交记录；UNKNOWN、裸202、本地取消、历史缺permId均不进入。
2. 最新持仓/工作单/成交三证据，确认无其他SELL或可能剩余量，确保总可能卖出权≤实际持仓。
3. 在首次重挂前持久化一次预算与父子lineage；重启不重置次数，不以请求超时当失败再发。
4. 参数在已批准的类型/限价/时段政策内，保留入场冻结、A/B期限和P0告警，确认WORKING后才释放覆盖门控。
5. 第二次拒绝/未知/证据不足直接冻结，人工TWS限价平仓优先，release门槛不变。

缩小offset仅对允许价格更严格，却可能增大不成交概率，不能统称“整体更保守”。是否启用这条重试政策由Claude/John决定，本次没有写代码。

## 5. 决策与G3-T放行条件

待John决定：A的Paper候选offset、B是否接受无价格下限，或C是否接受重新设计DAY/隔夜生命周期；以及是否批准一次受限重挂。公开距离上限仍未知，需John另行授权的Paper实测，Codex本轮不会连接券商。

第4项启动器修复仅在dev；运行main仍保留旧版本，用户目录的一行入口未部署。不得仅复制新入口配合旧Out-Host启动器就认为Ctrl+C安全。须Claude复核并另行授权运行脚本部署，再完成实际终端键盘一次Ctrl+C验收。此决策稿不代表G3-T已放行。

## 6. 来源与交付边界

全部官方来源见 `docs/batch21_evidence/official_sources.json`，访问日期2026-10-08（PT）；Preset页可见更新2026-04-28，API设置页2025-10-08，其余未见明确更新日；legacy GitHub文档已注明deprecated。研究Agent分别复核价格规则、算法/风险、停机设计；root整合并核对官方链接。

未改v6lite交易包、配置校验、John配置、User状态、运行仓库；未stage/commit第21批、未合并/push、未联网到券商。第20批快照已按授权提交并tag。第21批脚本测试、Ctrl+C证据、文件SHA清单见运维说明与evidence目录。

## 7. 本轮验证

第21批最终scripts测试串行一次：**130 passed / 0 failed / 0 errors / 0 skipped，164.24秒**，其中包括4项真实CTRL_C_EVENT合成控制台测试。交易包没有改，未把第20批的3次全量算作本轮重跑。红证据、停机生命周期、部署前提见运维说明；文件字节清单见 `docs/batch21_evidence/sha256_manifest.json`，此清单排除自身避免自引用。第21批尚未提交或部署。
