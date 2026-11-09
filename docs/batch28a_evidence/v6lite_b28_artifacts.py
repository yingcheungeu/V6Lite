"""Build offline investigation/cards and closeout receipts, never runtime data."""
from pathlib import Path
import ast, hashlib, json, shutil, subprocess, sys
from datetime import datetime
from zoneinfo import ZoneInfo
root=Path('C:/Dev/V6Lite-dev')
sys.path.insert(0,str(root))
out=root/'docs/batch28a_evidence'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

if sys.argv[1]=='prepare':
    data=[]
    for tag in ('v6lite-batch25','v6lite-batch26','v6lite-batch27b','v6lite-batch27'):
        item={'tag':tag,'commit':subprocess.check_output(['git','rev-parse',tag+'^{commit}'],cwd=root,text=True).strip(),'functions':{}}
        for filename,name in (('v6lite/__main__.py','run_engine'),('v6lite/broker/ibkr.py','pump')):
            source=subprocess.check_output(['git','show',tag+':'+filename],cwd=root).decode('utf-8')
            node=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.FunctionDef) and n.name==name)
            snippet='\n'.join(source.splitlines()[node.lineno-1:node.end_lineno])+'\n'
            item['functions'][name]={'path':filename,'line':node.lineno,'end_line':node.end_lineno,'sha256':hashlib.sha256(snippet.encode()).hexdigest(),'source':snippet}
        data.append(item)
    (out/'static_investigation.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    from pytest import MonkeyPatch
    from tempfile import TemporaryDirectory
    from v6lite.tests.test_batch28_lifecycle import _holding
    with TemporaryDirectory(prefix='v6lite-b28-cards-',dir='C:/tmp') as temp:
        mp=MonkeyPatch()
        rig,pid=_holding(Path(temp),mp)
        try:
            rig.engine.connection_lost(ConnectionResetError(10054,'synthetic API reset'))
            rig.engine.request_shutdown('fatal_error',RuntimeError('synthetic invariant failure'))
            cards=[{'title':title,'body':body} for title,body in rig.channel.messages]
            (out/'card_samples.json').write_text(json.dumps({'synthetic_only':True,'cards':cards},ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            rig.alerter.close();rig.store.close();mp.undo()
    notes={'previous_batches':{'status':'invalid harness setup','reason':'pytest --basetemp parent missing; 854 setup errors, not product regressions; raw receipt retained locally'},
           'previous_batches_valid':{'status':'intentionally interrupted','reason':'superseded focused candidate before final refinements; no complete pytest result'},
           'full_1':{'status':'intentionally interrupted','reason':'independent review found undelivered-fatal-page and physical-backoff edge cases; superseded before full_1_frozen; not counted as a completed full run'},
           'candidate_initial':{'status':'complete','result':'13 passed'},
           'focused_edges_initial':{'status':'complete','result':'57 passed'},
           'final_focused':{'status':'complete superseded candidate','result':'141 passed'}}
    (out/'development_attempts.json').write_text(json.dumps(notes,indent=2),encoding='utf-8')
    for src in ('v6lite_b28_validate.py','v6lite_b28_progress.py','v6lite_b28_baselines.py','v6lite_b28_artifacts.py'):
        shutil.copyfile(Path('C:/tmp')/src,out/src)
    print('investigation, synthetic cards and development receipts written')

elif sys.argv[1]=='report':
    from v6lite.version import code_sha
    assert code_sha()=='f41492ffc7cb495c'
    expected='646e8209a5a8d0e9876a42a8b6f47b46ad0444a193eb9d869b731da0bb2389a6'
    assert sha(root/'docs/V6_LITE_BATCH28_INSTRUCTIONS.md')==expected
    runs=[json.loads((out/f'release_full_{i}.json').read_text()) for i in (1,2,3)]
    assert all(r['returncode']==0 and r['failures']==r['errors']==0 and r['source_unchanged'] and not r['incomplete'] for r in runs)
    assert len({r['inventory_sha256'] for r in runs})==1
    tables='\n'.join(f"| {r['label']} | {r['start_pt']} | {r['seconds']:.1f} | {r['tests']} | {r['failures']} | {r['errors']} | {r['skipped']} |" for r in runs)
    report=f'''# V6-Lite 第二十八批 28a：意外断线恢复与退出可观测性

日期：2026-10-20 PT。交付范围为最新版指令第 1 节及对应测试，tag `v6lite-batch28a`。第 2 节留待后续批次；本报告不声称第二十八批全部完成。

基线：`v6lite-batch27` / `a5ae5945fbe8861b20616af51af10788c69f177f` / 原 code_sha `73babb393a9f86f9`。
最终 code_sha：`f41492ffc7cb495c`。指令 SHA-256：`{expected}`。

## 调查结论

这是至少从 b25 已存在的主循环异常处理遗漏，**不是 b27 引入的回归**。b25、b26、b27b、b27 的 `run_engine` 和 `IbkrBroker.pump` 函数逐字相同，原文、行号及 SHA-256 在 `batch28a_evidence/static_investigation.json`。

事故时间和现场情况来源为用户提供并核验哈希的批 28 指令，不读取运行仓库、用户状态或现场日志。本次离线回放证明退出机制；John 说明电脑和网络正常，使 TWS 一侧重置成为合理推断，但 socket 错误本身无法证明重置责任端。10/19 是否先 Ctrl+C 无旧停机来源记录，不能追溯定论。

原 b27 完整调用路径：

1. `.venv/Lib/site-packages/ib_insync/connection.py:56` `connection_lost(exc)` 把 OSError 转文本并发出 disconnected。
2. `ib_insync/client.py:365` `_onSocketDisconnected` 写 WinError/Peer closed 错误并调用 wrapper。
3. `ib_insync/wrapper.py:126` `connectionClosed()` 发 `globalErrorEvent(ConnectionError("Socket disconnect"))`。
4. `ib_insync/util.py:336` 取消正在等待的 sleep task；`:342` 重抛全局 ConnectionError。
5. `v6lite/broker/ibkr.py:612–624` 的 pump 调 `self.ib.sleep`，异常直接向外传播。
6. 原 `v6lite/__main__.py:283–288` 跳过 `engine.tick()`，直接进入 finally shutdown。
7. 原 `v6lite/engine.py:3045–3048,3070–3075` 两次 openOrders 查询失败，产生事故中的两张 shutdown P1。
8. 原 `v6lite/__main__.py:561–567` 进一步把运行时 ConnectionError 当作启动时 Gateway 不可达，返回 3，没有退出 P0/ERROR。

原生 `_on_disconnect()` 已排队 ConnectionEvent，但循环在 drain 之前离开，所以断线 P1 和 b25 重连完全未被执行。不是 b25 的重试上限被触发。

## 改动与安全边界

| 文件 | 行为 |
|---|---|
| `v6lite/__main__.py` | pump、优先保护、tick 使用同一 transport 异常边界；明确网络错误关闭旧 socket 并恢复循环；本地磁盘错误不当成网络错误。记录真实 SIGINT/SIGBREAK；非人工退出非零。清理失败升级 fatal，告警最后关闭，未送达退出 P0 通过新同步 sender 尝试补发。 |
| `v6lite/engine.py` | 独立物理断线等待门；首次 30 秒、后续失败 60/120 秒；同原因 5 分钟最多两次，超限锁止，进程和心跳继续，每 10 分钟远端提醒。断线 P1 远端发送；SHUTDOWN_REQUESTED 和 SHUTDOWN 记 source。 |
| `v6lite/lifecycle.py` | 脱敏的退出原因、时间、异常类型及来源；非操作员退出先发专用 P0，含最后本地持仓、STP orderId、止损价和 `~\\v6 g3t`。重试投递保留原始 fatal 原因及唯一 provenance；最终醒目 ERROR。 |
| 两个 `test_batch28_*.py` | 新增 32 项独立离线行为契约。 |

重连接管继续走原 b25 `_recover`/`_reconcile`，连接快照只记录，自己发出的完整三项强身份核验才允许恢复。无新下单路径、无保护身份放宽、无 GTC 撤单变更。kill 原有语义仍为阻断入场、继续退出，不擅自改成终止进程。

既有 b22–b27 测试和全部启动器、配置、风控、保护成交代码未改。第一个完整全量发现两个旧测试仍要求盘前/夜间断线只记日志，与本批无条件 P1 要求相冲突；仅迁移 test_engine.py 和 test_g2_day1_selfreview.py 的两个函数为夜间恰一张、盘前一张且 RTH 新断线再次一张，函数之外的字节完全不变，见 legacy_alert_contract_migration.json。生产代码不因这两项失败而改变。新测试的第二次失败重试时间从第一候选的 +60 秒校准为 +90 秒，以匹配新规则 30/60/120；原强断言保留。底层 b25 直接 API 测试保持其初次立即调用契约；正式 run 循环的物理断线等待在维护连接之前建立，覆盖内部先消费事件和先 1100 后掉 socket。

## 红绿与验证

最终冻结 spec SHA-256：`b1b5215a34d7fbe126170cb7621d999986b3e4fb5983c51f14852c0872df33d3`。同一个 replay 从真实已安装 SDK `connection.connection_lost(OSError(...winerror=64))`、`None`、ConnectionResetError 开始，经过 client、wrapper、globalErrorEvent、util.run 和实际 pump；所有网络连接入口被禁止。合成 AAPL 3 股 @342.60、STP 706 @338.62，模拟 11:16:51–11:17:01 PT 通知序列。

| 基线 | 结果 | 失败原因 |
|---|---|---|
| b25 `97fa8c6` | 3 failed / 0 errors | SDK Socket disconnect 逃出主循环 |
| b26 `78a0905` | 3 failed / 0 errors | 同上 |
| b27b `54a893b` | 3 failed / 0 errors | 同上 |
| b27 `a5ae594` | 3 failed / 0 errors | 同上 |
| 修复后 | 全部通过 | 首次 +30 秒恢复；三项查询后接管同一 STP；未停机、未发退出 P0、持仓与写单计数不变 |

正式红收据在 `batch28a_evidence/native_connection_lost/`。早期直接从 client 入口的红回放也保留，不拿它代替最终 native 入口证据。

生产最终冻结聚焦：145 passed / 0 errors / 0 skipped，包含 32 个新契约、b25 接管/预算、b26 安全停机、启动身份拒绝及相关旧批次。旧测试策略迁移后另有 74 passed / 0 errors / 0 skipped，覆盖新契约与两个旧测试所在文件。153 个 Python 文件在最终 f41492ffc7cb495c 上 source-only compile 通过。

Windows 10 / Python 3.11.9；最终三轮串行、均在 13:30 PT 后执行，包含 `v6lite/tests scripts/tests`：

| 收据 | 开始 PT | 秒 | tests | failures | errors | skipped |
|---|---|---:|---:|---:|---:|---:|
{tables}

三轮的源码与测试 SHA-256 inventory 完全一致，执行前后源码不变。跳过项及原因见各轮 JSON；不把跳过计作通过。b22–b27 回放（10/14 接管、10/16 止损、过夜 a–d 等）包含在每轮全量中。

开发阶段一次 pytest 临时目录父目录缺失造成 setup errors；修正测试 harness 后保留原收据。一次扩展聚焦和一次早期全量为应用复核修正主动停止，均未完成、未计入三次连续通过。第一个完整全量 full_1_frozen 为 1975 passed / 2 failed / 1 skipped，失败均是上述旧静音策略契约；完整失败收据保留，迁移后从 release_full_1 重新开始连续三轮，非时间抖动。完整说明在 development_attempts.json。没有隐去第三次 flake 或重试豁免；本次无产品测试 timing flake。

## 新卡片样例与限制

合成完整卡片见 `batch28a_evidence/card_samples.json`：P1“IBKR 连接断开”；P0“V6-Lite 引擎已退出，持仓仅由券商端止损保护”，列 AAPL 3 股、706、338.62、原始脱敏错误和重启命令。正常 Ctrl+C 记录 SIGINT，保留原 DAY 撤单/GTC 保留顺序，不发退出 P0。

远端告警为尽力交付：异步未送达或关闭故障会尝试同步补发；凭据或网络完全不可用时无法保证到达。合法配置无法加载时没有可信远端目的地，只能 ERROR + 非零；身份尚未通过或状态库已关闭时不创建/迁移/越界打开数据库，SHUTDOWN_REQUESTED 仍写 WARNING，无法写数据库审计时明确记录失败。

仅 dev 提交/tag，不 push、不部署、不连接 TWS/Gateway、不操作订单/用户状态/运行配置。原有操作手册修改保持原样，不暂存。第 2 节账户静默、日报范围、事件排序、汇总落盘和 IBKR 时间文档研究尚未实施，由后续批次处理。操作手册由 Claude 更新。

## SHA-256 清单

完整交付文件清单：`batch28a_evidence/sha256_manifest.json`。源码包 SHA-256、每轮 inventory、静态对比、红绿收据、报告和卡片均列入。清单自身不递归哈希；指令文件仅核验，不暂存。
'''
    path=root/'docs/V6_LITE_FIX_REPORT_ROUND28_2026-10-20.md'
    path.write_text(report,encoding='utf-8',newline='\n')
    print(path)
