"""Build the B25 report/manifest from immutable offline receipts; never stage or commit."""
from pathlib import Path
import argparse, hashlib, json, os
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from run_batch25_checks import ROOT, EV, guard, package_sha, sha



def checked_path(base, relative):
    assert not Path(relative).is_absolute() and '..' not in Path(relative).parts
    p=base/relative
    assert p.resolve().is_relative_to(base.resolve()) and not p.is_symlink()
    return p


def time_window(r):
    from zoneinfo import ZoneInfo
    start=datetime.fromisoformat(r['start_pt']).astimezone(ZoneInfo('America/Los_Angeles'))
    end=datetime.fromisoformat(r['end_pt']).astimezone(ZoneInfo('America/Los_Angeles'))
    assert start<=end
    if not r['full']: return
    day=start.replace(hour=0,minute=0,second=0,microsecond=0)
    while day.date()<=end.date():
        if day.weekday()<5:
            block_start=day.replace(hour=6); block_end=day.replace(hour=13,minute=30)
            assert end<=block_start or start>=block_end, 'full test overlaps trading block'
        day+=timedelta(days=1)

def latest(prefix):
    candidates=[]
    for p in EV.glob(prefix+'*.json'):
        try: r=json.loads(p.read_text(encoding='utf-8'))
        except (ValueError, OSError): continue
        if 'totals' in r and 'end_pt' in r and r.get('label','').startswith(prefix):
            candidates.append((datetime.fromisoformat(r['end_pt']),r,p))
    assert candidates, prefix
    return max(candidates,key=lambda item:item[0])[1:]


def raw_receipt(r):
    label=r['label']
    assert label and all(c.isalnum() or c=='_' for c in label), 'unsafe receipt label'
    for suffix,key in (('.txt','stdout_sha256'),('_inventory.json','inventory_sha256')):
        assert sha(EV/(label+suffix))==r[key], 'raw receipt hash mismatch: '+label+suffix
    observed=json.loads((EV/(label+'_inventory.json')).read_text(encoding='utf-8'))
    assert observed==r['inventory'], 'inventory receipt mismatch'
    if r['collect_only']:
        assert r['junit_sha256'] is None and observed['collected'] and not observed['executed']
        return
    xml=EV/(label+'.xml')
    assert sha(xml)==r['junit_sha256'], 'JUnit raw hash mismatch'
    tree=ET.parse(xml).getroot()
    totals={name:sum(int(x.attrib.get(name,0)) for x in tree.iter('testsuite')) for name in ('tests','failures','errors','skipped')}
    cases=[]
    for case in tree.iter('testcase'):
        f=case.find('failure'); e=case.find('error'); skipped=case.find('skipped')
        item=f if f is not None else e if e is not None else skipped
        cases.append({'classname':case.attrib.get('classname'),'name':case.attrib.get('name'),
                      'outcome':'failure' if f is not None else 'error' if e is not None else 'skipped' if skipped is not None else 'passed',
                      'message':item.attrib.get('message','') if item is not None else ''})
    assert totals==r['totals'] and cases==r['cases'], 'actual JUnit disagrees with receipt'
    assert len(cases)==totals['tests']>0
    assert sorted(observed['collected'])==sorted(observed['executed']) and len(observed['executed'])==totals['tests']


def valid(r, code):
    raw_receipt(r)
    assert r['source_code_sha']==code and r['worktree_code_sha']==code and not r['baseline'] and not r['collect_only']
    assert r['platform'].startswith('Windows-') and Path(r['python_executable'])==ROOT/'.venv/Scripts/python.exe'
    assert r['returncode']==0 and not r['timed_out'] and r['totals']['failures']==r['totals']['errors']==0
    assert r['v6lite_runtime_processes']==r['uninspectable_python_processes']==0
    assert r['source_unchanged'] and r['worktree_unchanged'] and r['specs_unchanged'] and r['source_trees_unchanged']
    assert r['source_trees']=={'v6lite':sha(ROOT/'v6lite'),'scripts':sha(ROOT/'scripts')}
    assert all(Path(item['path']).resolve().is_relative_to(ROOT.resolve()) and sha(Path(item['path']))==item['sha256'] for item in r['specs'])
    time_window(r)
    return r['totals']['tests']-r['totals']['skipped'],r['totals']['skipped']


def allowed_manifest_path(p):
    assert p.resolve().is_relative_to(ROOT.resolve()) and not p.is_symlink()
    relative=p.relative_to(ROOT).as_posix()
    assert not any(part in {'__pycache__','.pytest_cache','test_tmp','baseline22a'} for part in p.relative_to(ROOT).parts)
    assert p.suffix.lower() not in {'.db','.sqlite','.sqlite3','.wal','.shm','.pyc','.zip'}, relative
    assert not p.name.lower().endswith(('-wal','-shm')) and p.name not in {'.env','V6_LITE_OPERATOR_MANUAL.md'}, relative
    assert not(p.parent==EV and p.suffix.lower()=='.toml'), relative
    return relative


def red_valid(record):
    raw_receipt(record);time_window(record)
    assert record['returncode']==1 and record['totals']['failures']>0 and record['totals']['errors']==0
    assert not record['full'] and not record['collect_only'] and not record['timed_out']
    assert record['source_unchanged'] and record['worktree_unchanged'] and record['specs_unchanged'] and record['source_trees_unchanged']
    assert record['v6lite_runtime_processes']==record['uninspectable_python_processes']==0

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--final',action='store_true');args=parser.parse_args()
    guard();code=package_sha(ROOT/'v6lite')
    report=ROOT/'docs/V6_LITE_FIX_REPORT_ROUND25_2026-10-14.md';manifest_path=EV/'sha256_manifest.json'
    assert not report.with_suffix('.md.staged').exists() and not manifest_path.with_suffix('.json.staged').exists()
    cp=json.loads((EV/'acceptance_checkpoint.json').read_text(encoding='utf-8'))
    assert cp['code_sha']==code
    assert all(sha(checked_path(ROOT,p))==digest for p,digest in cp['locked_files'].items())
    review=json.loads((EV/'independent_review_final.json').read_text(encoding='utf-8'))
    assert review['reviewed_code_sha']==code and not review['unresolved_substantive_findings']
    assert all(sha(checked_path(ROOT,p))==digest for p,digest in review['production_sha256'].items())
    from run_batch25_checks import BASE
    assert sha(ROOT/'scripts')==sha(BASE/'scripts')
    adjustments=cp.get('legacy_test_fixture_adjustments',{})
    assert set(adjustments)=={'v6lite/tests/test_review_round5_selfreview.py'}
    for path in (BASE/'v6lite/tests').rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts:
            current=ROOT/'v6lite/tests'/path.relative_to(BASE/'v6lite/tests')
            relative=current.relative_to(ROOT).as_posix()
            if relative in adjustments:
                item=adjustments[relative]
                assert sha(path)==item['baseline_sha256'] and sha(current)==item['candidate_sha256']
                assert sha(EV/'test_review_round5_selfreview_baseline24.py')==sha(path)
                import ast
                before=ast.parse(path.read_text(encoding='utf-8-sig'));after=ast.parse(current.read_text(encoding='utf-8-sig'))
                name='test_a_request_that_never_left_owes_no_end'
                assert [ast.dump(n,include_attributes=False) for n in before.body if getattr(n,'name',None)!=name]==[ast.dump(n,include_attributes=False) for n in after.body if getattr(n,'name',None)!=name]
                old=next(n for n in before.body if getattr(n,'name',None)==name);new=next(n for n in after.body if getattr(n,'name',None)==name)
                original_asserts=[ast.dump(n,include_attributes=False) for n in ast.walk(old) if isinstance(n,ast.Assert)]
                current_asserts=[ast.dump(n,include_attributes=False) for n in ast.walk(new) if isinstance(n,ast.Assert)]
                assert all(n in current_asserts for n in original_asserts), 'old strong assertion removed'
                old_raises=[[ast.dump(i,include_attributes=False) for i in n.items] for n in ast.walk(old) if isinstance(n,ast.With)]
                new_raises=[[ast.dump(i,include_attributes=False) for i in n.items] for n in ast.walk(new) if isinstance(n,ast.With)]
                assert all(n in new_raises for n in old_raises), 'old exception type expectation changed'
            else:
                assert sha(path)==sha(current), 'unapproved original test changed'
    greens=[]
    for filename in cp['green_receipts']:
        p=checked_path(EV,filename);r=json.loads(p.read_text(encoding='utf-8'));valid(r,code);greens.append((r,p))
    assert any(r['label'].startswith('focused') for r,p in greens)
    reds=[]
    for filename in cp['valid_red_receipts']:
        p=checked_path(EV,filename);r=json.loads(p.read_text(encoding='utf-8'));red_valid(r);reds.append((r,p))
        if r['baseline']:assert r['source_code_sha']=='2db6af8f04f145f6'
    first=json.loads((EV/'first_candidate_review.json').read_text(encoding='utf-8'))
    prior_green=json.loads((EV/'green_first_candidate.json').read_text(encoding='utf-8'));raw_receipt(prior_green)
    assert prior_green['totals']=={'tests':33,'failures':5,'errors':0,'skipped':0}
    assert prior_green['source_unchanged'] and prior_green['worktree_unchanged']
    initial_red=json.loads((EV/'red_finalspec_b24_attempt3.json').read_text(encoding='utf-8'));red_valid(initial_red)
    assert initial_red['totals']=={'tests':33,'failures':24,'errors':0,'skipped':0}
    for item in initial_red['specs']:
        saved=EV/(Path(item['path']).stem+'_red_before_seed.py')
        assert sha(saved)==item['sha256'], 'pre-production red specification bytes not retained'
    samples=json.loads((EV/'render_samples.json').read_text(encoding='utf-8'))
    assert samples['source_code_sha']==code and samples['source_unchanged'] and samples['network_connections_allowed'] is False
    assert len(samples['samples'])==2 and len(samples['quiet_scenarios'])==1
    rendered=['# Batch 25 actual Alerter card samples','','Source SHA: '+code,'']
    for sample in samples['samples']:
        assert len(sample['cards'])==1 and sample['plain_messages']
        rendered+=['## '+sample['scenario'],'']
        for message in sample['plain_messages']:
            assert message['title']==sample['cards'][0]['title'];rendered+=[message['body'],'']
    assert (EV/'render_samples.md').read_text(encoding='utf-8')=='\n'.join(rendered)
    full=[]
    if args.final:
        collection,collection_path=latest('full_collection');raw_receipt(collection);time_window(collection)
        assert collection['full'] and collection['collect_only'] and collection['returncode']==0 and not collection['baseline']
        assert collection['source_code_sha']==collection['worktree_code_sha']==code and collection['source_trees_unchanged']
        assert collection['source_trees']=={'v6lite':sha(ROOT/'v6lite'),'scripts':sha(ROOT/'scripts')}
        assert collection['specs']==[{'path':str(ROOT/f),'sha256':sha(ROOT/f)} for f in ('v6lite/tests','scripts/tests')]
        assert collection['v6lite_runtime_processes']==collection['uninspectable_python_processes']==0
        for n in range(1,4):
            r,p=latest('full_'+str(n));valid(r,code)
            assert r['full'] and not r['collect_only']
            assert sorted(r['inventory']['collected'])==sorted(collection['inventory']['collected'])
            assert r['source_trees']==collection['source_trees'] and r['specs']==collection['specs']
            full.append((r,p))
        assert datetime.fromisoformat(collection['end_pt'])<=datetime.fromisoformat(full[0][0]['start_pt'])
        assert all(datetime.fromisoformat(full[n][0]['end_pt'])<=datetime.fromisoformat(full[n+1][0]['start_pt']) for n in range(2))
        assert all(r['cases']==full[0][0]['cases'] and r['totals']==full[0][0]['totals'] for r,p in full)
        expected=1560+cp['new_spec_cases']
        assert full[0][0]['totals']['tests']==expected and full[0][0]['totals']['skipped']==1
    paths=[]
    for folder in ('v6lite','scripts'):
        paths += [p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts]
    paths += [ROOT/'docs/V6_LITE_BATCH25_INSTRUCTIONS.md']
    paths += [p for p in EV.iterdir() if p.is_file() and p.name!='sha256_manifest.json' and not p.name.endswith('.staged')]
    paths=sorted(set(paths),key=lambda p:p.relative_to(ROOT).as_posix())
    manifest={allowed_manifest_path(p):sha(p) for p in paths}
    manifest_text=json.dumps({'code_sha':code,'status':'full accepted' if args.final else 'focused accepted; full pending','sha256':manifest},ensure_ascii=False,indent=2)+'\n'
    manifest_digest=hashlib.sha256(manifest_text.encode()).hexdigest()
    status='Windows默认全量连续3次通过；dev快照等待Claude复核，交付后冻结。' if args.final else '聚焦通过；全量3次、提交及tag待13:30 PT后执行。'
    text=f'''# V6-Lite 第二十五批修复报告（2026-10-14）

状态：{status}

## 1 基线、输入与职责

- 工作区仅 C:\\Dev\\V6Lite-dev，dev分支；基线 v6lite-batch24 / 6ebedcfd13f84e98fb284f756b939e10afbfdee1，基线code_sha=2db6af8f04f145f6。
- 最终候选code_sha={code}。算法：按v6lite相对POSIX路径（大小写敏感）排序，路径UTF-8、NUL、原始文件字节、NUL；SHA-256前16位。排除__pycache__，包含测试和样例。
- 指令SHA-256：b6e50798d7d943d4ac203c603a44fe7214e585964d8102414847fadb6e3e6e06；回调SHA-256：60424a3e6c31ec44d0194ecf532cfc280502f06e8eee0b0bddfcdd4b60af46e1。
- 唯一生产实施Agent：batch25_implement。独立测试：batch25_tests。独立调查/复核：batch25_investigate。root负责取证整合、验红裁定和串行验收。
- 不连接TWS/Gateway；不访问运行仓库、John配置、用户.v6lite或归档；不push。操作手册只核对预检哈希fb008f61f56afccd1125a116883b59be90bc797ee0f662fb9dcf120ccb82d3f6，不改、不提交。

## 2 事实与官方依据

给定日志只有36行。可直接核实：第5–6行orderId501/permId45208044/client132，SELL STP、lmtPrice=0.0、auxPrice340.52/GTC、PreSubmitted/whyHeld=trigger；第7–8行在API ready之前出现非主动End；第23–24行同步完成后因poison而renew；第28/30/33/36行可见过早限速P1、断线、恢复失败和入场阻断。

10/13正常SHUTDOWN unresolved=[]、7次循环及60.8秒P0来自指令提供的事实；36行片段不足以独立复算这些总数。合成隔夜测试的入场成本342.53仅用于复算测试盈亏，不宣称是John历史成本。

连接End来源：

- [IBKR API设置](https://www.ibkrguides.com/traderworkstation/api.htm)：存在Download open orders on connection设置。实际TWS勾选状态未知，本轮未读取。
- [IBKR官方eConnect说明](https://interactivebrokers.github.io/tws-api/classIBApi_1_1EClientSocket.html)：连接时Host可提供工作单；该官方旧页面标注已弃用。不能据此声称所有版本都必然主动推送。
- [IBKR工作单接口](https://interactivebrokers.github.io/tws-api/open_orders.html)：reqOpenOrders用于同client，reqAllOpenOrders用于更广范围，openOrderEnd结束下载。
- [ib_insync源代码](https://ib-insync.readthedocs.io/_modules/ib_insync/ib.html)：本机0.9.86的connectAsync发reqPositions，非readonly发reqOpenOrders，并同步当日executions；默认启动同步并不调用reqAllOpenOrders。adapter的主动完整查询才使用reqAllOpenOrders。本机SDK没有RaiseSyncErrors参数；gather(return_exceptions=True)路径可能仅记timeout并正常返回。

本机源位置见investigation.json：ib.py1748/1760/1762/1763–64/1782/1789–90、wrapper.py392–393、client.py344–357。API-ready（nextValidId和managedAccounts就绪）先于SDK完整同步。End没有请求编号，无法凭片段绝对归属每个版本的具体来源；实现依赖当前代、真实同步阶段和已登记请求计数，仍需下一次Paper验证。

## 3 连接快照、同步边界：发现 → 改动 → 测试

发现：B24在connect前安装钩子，但将ended>sent一律poison；下一次主动查询因此断开。在已有同client GTC的启动序列上，这会把合法连接快照End误判。原FakeBroker.connect只改连接boolean；旧原生夹具的connect没有模拟pre-ready工作单/End，因此1559 passed（另1 skipped）未覆盖该序列。

改动：只豁免当前连接代、正在握手/SDK同步、第一次adapter登记openOrders之前且无欠End的连接快照End；同步结束即关闭豁免。SDK已登记的End继续正常计数，不抹平欠账；旧代回调和同步后的无法归属End继续B24拒绝。连接返回时欠End或API尚未就绪，关闭未核实socket并失败，防止SDK吞timeout伪Ready；poison仍在主动fresh查询边界拒绝并renew，不能提供完整查询权威。确定未发送的请求只在无End/同代时回滚登记；不确定发送及End后抛错不回滚。

连接回调在创建时附快照角色，延迟至同步后消费也只写独立审计；不改last_raw_status、broker IDs、status_seen_generation、confirmed_at、A/B、人工hold或价格权威。真实Fill/Commission继续入账及去重。FakeBroker默认支持openOrder→orderStatus→End→synchronized，生成快照不调用fresh getters。

测试：原生Client/Wrapper/Future与本地transport重放connect→501(STP lmt0)→PreSubmitted→End→API ready→SDK sync→主动完整三源。真实SQLite正常SHUTDOWN后close/reopen，30秒内接管原501/S3/WORKING、不重连、不另挂、无P0/P1；停机期间STOP成交的变体正确平仓、盈亏、连亏和重复回放去重。0.0、有限值、UNSET仅broker_echo_lmt审计；account/client/conId/订单编号/aux/数量等强身份继续保留。同步后非主动End、旧代End、SDK显式与吞掉timeout、未发送/不确定发送反例覆盖。

## 4 重连上限与恢复完整性：发现 → 改动 → 测试

发现：固定30秒重连没有同因窗口上限。独立复核又发现原因中的动态generation/PID/reqId/elapsed/attempt可拆分同因，以及_recover失败但positions reconcile成功时被健康早返回跳过重试。

改动：每稳定原因300秒最多2次实际自动连接，第三候选连接前锁存停止并发一次P0“连接反复重建，已停止自动重连”，保留最新原因原文和编号步骤。仅归一带明确标签的偶发编号/耗时，不抹掉真实错误码/业务数值，不合并不同原因。完整恢复可重置连续退避，不能抹掉滚动历史；300秒过期、ConnectedTrue或正常tick不能自动解除P0。

保留旧首个断线tick立即尝试；失败后等待30、60、120秒（上限120）。同因实际连接在t=0、30，t=90第三候选被挡；不同真因连续失败在t=0、30、90、210验证退避。已连接但执行恢复不完整时保持pending/阻断，30秒到期在同socket重试；这种恢复查询不算新的物理重连。纯WORKING稳态查询timeout的原门控口径不扩大。

测试：同因预算、动态字段、不同真因、成功后历史不清、ConnectedTrue后仍锁存、部分恢复29.999/30秒及物理次数均有真实行为断言。

## 5 查询P1与隔夜限制：发现 → 改动 → 测试

发现：UNKNOWN/hold/普通pacing原先可触发限速P1；正常首次确认或SDK同步也被通知。初版候选仍有1项隔夜P1：三源查询已完成，但当天executions不能覆盖昨日ENTRY，于是full_exec=False被当失败。另4项失败是status_snapshot未显示现有_entry_gate的protection_unconfirmed；真实BUY路径原已阻断，未发现实际下单绕过。

改动：查询P1只在真实核验失败并登记退避、同步完成后通知；首次A等待<=gap_a_p1_seconds不发此P1，不清A、UNKNOWN、hold或截止告警。区分“仅历史窗口缺口”和“今天所需exec缺失”：只有原单稳态WORKING、强身份完整、无pending/A/B/TP/外卖单/hold、S=P=confirmed且今天执行齐全的稳定隔夜持有不制造失败。full_exec仍False，不能借此加量、重建、TP或解除旧事件；既有retry/F4和人工hold也不清，先前真实失败后即使当前下载完成，历史仍不足时仍需按原完整证据或人工规则处理；今日缺exec、身份/数量矛盾或真实查询失败照旧。status与实际_entry_gate共用既有保护确认条件。

测试：A的0/1/4.999/5.0秒、同步pending、真正失败仅一次P1、F4 60秒P0截止继续有效；隔夜稳定原单正控及历史缺口、今日缺exec/数量矛盾等负控；旧B15跨日禁止重建和原S+T+X<=P强断言不放宽。除一个明确未发送旧夹具的契约校准外，其余原测试文件字节与B24相同；原ConnectionError类型、entered及sent=ended=0强断言逐项保留。

## 6 红绿收据与Windows验收

初次验红有配置/原生pacer/合成价格夹具错误，保留全部原始txt/xml/json，不作最终行为红依据；处置说明见root_red_initial_review.json、red_review.json和first_candidate_review.json。有效第一次生产编辑之前：33项=24自然失败/9安全对照通过/0errors。旧规格原字节另存*_red_before_seed.py。

随后独立检查纠正仅新夹具的正常SHUTDOWN顺序：先按一致Fake身份停机，再绑定下一会话合成native permId；生产6哈希未动。以最终核心规格在B24重跑仍24自然失败/9通过。初版候选33项28通过/5失败如实保留；独立复核的新反例再先在冻结初版候选失败，之后才由同一Agent修复。新增第三规格首轮有一个外部SELL负控的非法初始S3+X1>P3，在原B23写前强检查失败；只校准合成初始单量为S2+X1=P3，保留原安全钩子和断言。部分恢复成功后仍保留既有recovery_failed手动异常，测试显式完成原人工clear后再检查允许入场，生产不得自动解除。原d930第三规格及两轮红收据保留。随后47通过/1失败只是异常字典键名应为anomaly:recovery_failed，修正这一测试oracle后48通过，生产六哈希未改。扩大聚焦240项239通过/1失败来自旧未发送夹具的普通ConnectionError语义不明确；仅此函数加显式maybe_sent=False的合成双类型错误，并保留原ConnectionError、entered、sent=ended=0全部强断言和真实wire/End零调用检查。新增相同Not connected文字在真实wire写入后抛出的独立反例，必须继续dirty，证明生产不能按类型或文字猜未发送。原旧文件和新增规格字节都另存保留。未把夹具失败称产品红，也未重试隐藏产品失败；没有时序flake豁免。

Windows本机dev .venv / Python3.11.9；PYTHONUTF8=1/PYTHONDONTWRITEBYTECODE=1，禁优化，清理继承测试筛选/已知机密环境名，禁自动plugin；child HOME/USERPROFILE/APPDATA/TEMP均隔离在本批test_tmp。每次全量前检查实际PT、分支/HEAD/空暂存区/无v6lite运行及不可检查Python进程/输入哈希；Windows Job Object在下个交易时段前5秒终止本次新建验收子进程树，不触碰运行进程。

|收据|结果|PT开始 → 结束|
|---|---|---|
'''
    historical=[]
    for label in ('green_closed_candidate','focused_final_locked'):
        path=EV/(label+'.json');record=json.loads(path.read_text(encoding='utf-8'));raw_receipt(record);time_window(record)
        assert record['source_unchanged'] and record['specs_unchanged'] and record['totals']['errors']==0
        historical.append((record,path))
    for r,p in [*reds,(prior_green,EV/'green_first_candidate.json'),*historical,*greens,*full]:
        t=r['totals'];passed=t['tests']-t['failures']-t['errors']-t['skipped']
        text+=f"|{p.name} (source {r['source_code_sha']})|{passed} passed / {t['failures']} failed / {t['errors']} errors / {t['skipped']} skipped|{r['start_pt']} → {r['end_pt']}|\n"
    if not args.final:text+='|Windows默认全量1/2/3|待13:30 PT后串行验收，不以聚焦替代|未运行|\n'
    text+=f'\nB24默认总集合1560=1559 passed+1 legacy skipped；新增规格{cp["new_spec_cases"]}项，最终全量预期{1560+cp["new_spec_cases"]}项；实际collection与执行nodeid、JUnit必须逐项一致。\n'
    text+='''

## 7 独立复核、范围与后续

独立只读复核结论及六个生产文件哈希见independent_review_final.json；root串行验证，无并发生产编辑。只提交本批生产/新增测试及这一旧夹具差异、指令、已脱敏输入、本批顶层证据/报告/清单；排除Claude手册、item8研究、baseline、其他批临时archive/test_tmp、数据库/WAL/SHM、缓存、John配置和机密。提交前核对实际暂存范围、机密和大文件；附注v6lite-batch25不能覆盖旧tag。完成后冻结，等待Claude复核，不合并main。

下一次Paper重启接管仍需John验证原生TWS行为；本轮只有离线原生回放与FakeBroker，不把模拟称真实TWS实测。

## 8 实际卡片样例

'''
    text+=(EV/'render_samples.md').read_text(encoding='utf-8')+'\n'
    text+='首次A 0至5秒样例：查询限速P1=0；原A起点保留；入场继续阻断。正常隔夜接管样例：P0/P1=0，原单501/S3/WORKING；以真实引擎回放收据核验，不生成一张本应不存在的通知。\n'
    text+=f'\n## 9 SHA-256清单\n\n{len(manifest)}项；机器清单docs/batch25_evidence/sha256_manifest.json的SHA-256为`{manifest_digest}`。报告自身不进清单，避免循环，交付另报其SHA-256。\n\n|文件|SHA-256|\n|---|---|\n'
    for p,d in manifest.items():text+=f'|{p}|`{d}`|\n'
    guard();assert package_sha(ROOT/'v6lite')==code
    assert all(sha(checked_path(ROOT,p))==d for p,d in cp['locked_files'].items())
    assert all(sha(ROOT/p)==d for p,d in manifest.items())
    report_tmp=report.with_suffix('.md.staged');manifest_tmp=manifest_path.with_suffix('.json.staged')
    report_tmp.write_text(text,encoding='utf-8',newline='\n');manifest_tmp.write_text(manifest_text,encoding='utf-8',newline='\n')
    os.replace(report_tmp,report);os.replace(manifest_tmp,manifest_path)
    print(json.dumps({'status':'final' if args.final else 'full pending','code_sha':code,'manifest_items':len(manifest),'report_sha256':sha(report),'manifest_sha256':sha(manifest_path)},ensure_ascii=False))

if __name__=='__main__':main()