"""Generate report only after three serial, source-frozen Windows gates."""
import hashlib,json,sys
from pathlib import Path
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from v6lite.version import code_sha
EV=ROOT/'docs/batch22_evidence';REPORT=ROOT/'docs/V6_LITE_FIX_REPORT_ROUND22_2026-10-09.md'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 full=json.loads((EV/'full_validation.json').read_text());assert len(full['rounds'])==3
 assert code_sha()==full['code_sha'];assert all(sha(ROOT/p)==h for p,h in full['source_sha256'].items())
 assert all(r['exit_code']==0 and r['suite']['failures']=='0' and r['suite']['errors']=='0' and r['source_unchanged'] for r in full['rounds'])
 assert sha(ROOT/'docs/V6_LITE_OPERATOR_MANUAL.md')=='5d7eb6f043f4d933019f36b6cbfd28b28f8474c0b7097261f9bb77ae627133bf'
 paths=[p for d in ['v6lite','scripts'] for p in (ROOT/d).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
 paths += [ROOT/'docs'/n for n in ['V6_LITE_BATCH21_RESEARCH_AND_OPTIONS_2026-10-08.md','V6_LITE_BATCH21_POWERSHELL_ENTRY_2026-10-08.md','V6_LITE_BATCH22_INSTRUCTIONS.md','V6_LITE_G3_RUNBOOK_DRAFT.md']]
 paths += [p for d in ['batch21_evidence','batch22_evidence'] for p in (ROOT/'docs'/d).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name not in ['sha256_manifest.json','independent_delivery_review.json','delivery_receipt.json']]
 paths.append(ROOT/'docs/batch21_evidence/sha256_manifest.json')
 paths=sorted(set(paths),key=lambda p:p.relative_to(ROOT).as_posix())
 entries=[dict(path=p.relative_to(ROOT).as_posix(),bytes=p.stat().st_size,sha256=sha(p)) for p in paths]
 text=r'''# 第二十二批整改报告：保护单 STP 与第二十一批直接 PowerShell 入口

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
'''
 labels=[('config_broker','baseline_config',23,61),('protection_lifecycle','baseline_lifecycle',51,1),('schema_audit','baseline_schema_audit',17,9),('pending_stop_audit','baseline_pending_audit',3,0),('snapshot_cancel','baseline_snapshot_cancel',45,3),('trigger_incidents','baseline_trigger_incidents',6,0),('trigger_replacement','baseline_trigger_replacement',1,0),('cancel_namespace','baseline_cancel_namespace',4,4)]
 for name,label,bad,good in labels:
  s=ET.parse(EV/(label+'.xml')).getroot()[0];assert int(s.get('failures'))==bad and s.get('errors')=='0' and int(s.get('tests'))==bad+good
  text+=f'| test_batch22_{name}.py | {bad} / {good} / 0 | {bad+good} passed |\n'
 focused=json.loads((EV/'focused_complete_metadata.json').read_text());assert focused['suite']['tests']=='228' and focused['exit_code']==0
 text+=r'''
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

'''
 text+=f"冻结 **code_sha={full['code_sha']}**；Python `{full['python'].splitlines()[0]}`；平台 `{full['platform']}`。\n\n"
 text+='| 轮 | 开始PT | 结束PT | passed / failed / errors / skipped | 秒 | 源一致 |\n|---|---|---|---|---:|---|\n'
 for r in full['rounds']:
  s=r['suite'];passed=int(s['tests'])-int(s['skipped']);text+=f"| {r['round']} | {r['start_pt']} | {r['end_pt']} | **{passed} / 0 / 0 / {s['skipped']}** | {r['wall_seconds']:.2f} | 是 |\n"
 skips=[]
 for c in ET.parse(EV/'full_1.xml').iter('testcase'):
  s=c.find('skipped')
  if s is not None:skips.append(c.get('classname','')+'.'+c.get('name','')+'：'+s.get('message',''))
 text+='\n既有skip：'+'；'.join(skips)+'。没有新增skip/xfail或过滤失败。\n'
 text+=r'''
## 5. FakeBroker实际渲染样例

生成脚本使用Rig/MemoryChannel/TEMP并禁止socket连接，不发Discord，不读运行状态。止损108.47：1股107.22，累计均价107.22，滑点+1.25USD/+115.2392366553bps；2股108.97，累计均价108.3866666667，滑点−1.00USD/−46.0956946621bps。美元净滑点+0.25，日报仍逐exec展示，不用累计均价混算单笔滑点。

单笔USD=Q×(stop−fill_price)，bps=(stop−fill_price)/stop×10000；累计均价=ΣQ×price/ΣQ。SELL恶化正/改善负。未确定stop记未知并列候选；退休用旧合同价不猜替换价；按fills.trading_day归属，重放不重复列。

以下17张完整保护P0/P1均合成事件，随机订单ID/账号不是John真实信息。

'''
 text+=(EV/'cards.md').read_text(encoding='utf-8').replace('# FakeBroker 合成保护卡片（只渲染，非实际事件）','### 完整卡片渲染')+'\n'
 text+='### 本地日报\n\n```markdown\n'+(EV/'daily_sample.md').read_text(encoding='utf-8')+'\n```\n'
 text+=r'''
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

'''
 text+=f"下表 **{len(entries)}项**；code_sha={full['code_sha']}。\n\n| 文件 | bytes | SHA-256 |\n|---|---:|---|\n"
 for e in entries:text+=f"| `{e['path']}` | {e['bytes']} | `{e['sha256']}` |\n"
 REPORT.write_text(text,encoding='utf-8')
 report_entry=dict(path=REPORT.relative_to(ROOT).as_posix(),bytes=REPORT.stat().st_size,sha256=sha(REPORT))
 manifest=dict(algorithm='SHA-256',code_sha=full['code_sha'],report_table_count=len(entries),count=len(entries)+1,exclusions=['this manifest','independent_delivery_review.json','delivery_receipt.json','Claude operator manual','cache/venv','runtime configs/state/secrets'],files=entries+[report_entry])
 (EV/'sha256_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(dict(report_sha256=sha(REPORT),manifest_sha256=sha(EV/'sha256_manifest.json'),manifest_count=manifest['count'],code_sha=full['code_sha'])))
if __name__=='__main__':main()