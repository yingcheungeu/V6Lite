"""Build the B24 report/manifest from immutable offline receipts; never stage or commit."""
from pathlib import Path
import argparse, hashlib, json, os
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from run_batch24_checks import ROOT, EV, guard, package_sha, sha



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


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--final',action='store_true');args=parser.parse_args()
    guard();code=package_sha(ROOT/'v6lite')
    assert not (EV/'sha256_manifest.json.staged').exists() and not (ROOT/'docs/V6_LITE_FIX_REPORT_ROUND24_2026-10-12.md.staged').exists(), 'interrupted publication artifacts require review'
    checkpoint=json.loads((EV/'acceptance_checkpoint.json').read_text(encoding='utf-8'))
    assert checkpoint['code_sha']==code
    assert all(sha(checked_path(ROOT,path))==digest for path,digest in checkpoint['locked_files'].items())
    green,gp=latest('green');focused,fp=latest('focused');valid(green,code);valid(focused,code)
    first_full=json.loads((EV/'full_1.json').read_text(encoding='utf-8'))
    raw_receipt(first_full);time_window(first_full)
    assert first_full['source_code_sha']=='62a9b82bb52ec22c' and first_full['totals']=={'tests':1558,'failures':23,'errors':0,'skipped':1}
    assert first_full['source_unchanged'] and first_full['source_trees_unchanged'] and first_full['returncode']==1
    regression=checkpoint['candidate_regression_red']
    regression_red=json.loads(checked_path(EV,regression['receipt']).read_text(encoding='utf-8'))
    raw_receipt(regression_red)
    assert not regression_red['baseline'] and regression_red['source_code_sha']==regression['source_code_sha']
    assert regression_red['returncode']==1 and regression_red['totals']['failures']>0 and regression_red['totals']['errors']==0
    assert regression_red['source_unchanged'] and regression_red['worktree_unchanged'] and regression_red['specs_unchanged'] and regression_red['source_trees_unchanged']
    assert all(sha(checked_path(ROOT,Path(item['path']).relative_to(ROOT).as_posix()))==item['sha256'] for item in regression_red['specs'])
    from run_batch24_checks import BASE
    assert sha(ROOT/'v6lite/tests/test_batch16_f6_steady_timeout.py')==sha(BASE/'v6lite/tests/test_batch16_f6_steady_timeout.py'), 'original F4 deadline test changed'
    review=json.loads((EV/'independent_review_final.json').read_text(encoding='utf-8'))
    assert review['reviewed_code_sha']==code and not review['unresolved_substantive_findings']
    assert all(sha(checked_path(ROOT,path))==digest for path,digest in review['production_sha256'].items())
    full=[]
    if args.final:
        collection,collection_path=latest('full_collection')
        raw_receipt(collection)
        time_window(collection)
        assert collection['full'] and not collection['baseline'] and collection['platform'].startswith('Windows-') and not collection['timed_out']
        assert collection['worktree_code_sha']==code and collection['v6lite_runtime_processes']==collection['uninspectable_python_processes']==0
        assert Path(collection['python_executable'])==ROOT/'.venv/Scripts/python.exe'
        assert collection['collect_only'] and collection['source_code_sha']==code and collection['returncode']==0
        assert collection['source_unchanged'] and collection['worktree_unchanged'] and collection['source_trees_unchanged'] and collection['specs_unchanged']
        assert collection['source_trees']=={'v6lite':sha(ROOT/'v6lite'),'scripts':sha(ROOT/'scripts')}
        assert collection['specs']==[{'path':str(ROOT/folder),'sha256':sha(ROOT/folder)} for folder in ('v6lite/tests','scripts/tests')]
        for index in range(1,4):
            r,path=latest('full_'+str(index));valid(r,code)
            assert r['full'] and not r['collect_only']
            assert sorted(r['inventory']['collected'])==sorted(collection['inventory']['collected'])
            assert r['source_trees']==collection['source_trees'] and r['specs']==collection['specs']
            full.append((r,path))
        assert datetime.fromisoformat(collection['end_pt'])<=datetime.fromisoformat(full[0][0]['start_pt'])
        assert all(datetime.fromisoformat(full[i][0]['end_pt'])<=datetime.fromisoformat(full[i+1][0]['start_pt']) for i in range(2))
        assert all(item[0]['totals']==full[0][0]['totals'] and item[0]['cases']==full[0][0]['cases'] for item in full)
    paths=[]
    for folder in ('v6lite','scripts'):
        paths += [p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.relative_to(ROOT/folder).parts and '.pytest_cache' not in p.parts]
    paths += [ROOT/'docs/V6_LITE_BATCH24_INSTRUCTIONS.md']
    paths += [p for p in EV.iterdir() if p.is_file() and p.name!='sha256_manifest.json' and not p.name.endswith('.staged')]
    paths=sorted(set(paths),key=lambda p:p.relative_to(ROOT).as_posix())
    manifest={allowed_manifest_path(p):sha(p) for p in paths}
    manifest_path=EV/'sha256_manifest.json'
    manifest_text=json.dumps({'code_sha':code,'status':'full accepted' if args.final else 'focused accepted; full pending','sha256':manifest},ensure_ascii=False,indent=2)+'\n'
    manifest_digest=hashlib.sha256(manifest_text.encode('utf-8')).hexdigest()
    state='Windows 默认全量连续三次通过；本报告随 dev 快照提交，等待 Claude 复核。' if args.final else '聚焦通过；全量三次、提交和 tag 尚未执行。'
    text=f'''# V6-Lite 第二十四批修复报告（2026-10-12）

状态：{state}

## 1 基线、输入与分工

- 仅 `C:\\Dev\\V6Lite-dev` 的 dev；基线 `v6lite-batch23` / `bd6b8fb9ecf8a08cf9685c8eaf6a46f00be794f0`，基线 `code_sha=a17d647ba28c51e4`。
- 本批候选 `code_sha={code}`。算法为排序后的 v6lite 相对 POSIX 路径、NUL、原始文件字节、NUL，SHA-256 前16字符；排除 `__pycache__`，包含测试与样例。
- 指令 SHA-256：`342bd728d90eb814ccec9af7beb127129b7d00d7cfea143dfa6a97df8ebd0591`。
- 脱敏回调 SHA-256：`ec08ec055af96837b0c5bd45e3ea329afd594429efdf0fbd498072e6fa265542`。
- 生产唯一实施：batch24_implement；独立测试：batch24_tests；独立只读调查/复核：batch24_investigate；root 整合并串行执行候选验收。
- 没有连接 TWS/Gateway，没有访问运行仓库、John 配置或用户 .v6lite。Claude 操作手册保持预检哈希 `8ddde6114a233241a6e9e70dfe5e861080950089e13d0eefc8b769f8ba296143`，不纳入提交。

## 2 STP echo、严格身份与回放：发现 → 改动 → 测试

发现：B23 将有限的回传 lmtPrice 复制为合同 limit_price，再将其作为 STP 身份不符。基线搜索：

```powershell
rg -n "lmtPrice|limit_price|native_price_complete|_protection_order_valid|_native_status_identity" .pytest_cache/batch24_baseline23/v6lite/broker/ibkr.py .pytest_cache/batch24_baseline23/v6lite/protection_engine.py .pytest_cache/batch24_baseline23/v6lite/protection_cancellations.py .pytest_cache/batch24_baseline23/v6lite/tp_evidence.py
```

基线位置：ibkr.py 582/855/905/1189/1224/1249（status/原生snapshot/modify/cancel/价格观察）；protection_engine.py 647–654、743/806/828（身份及减量/加量路径）；protection_cancellations.py 22/57–63；tp_evidence.py 390–406。完整调查另见 investigation.json。

改动：STP 的原始 echo 写入 `broker_echo_lmt` 审计字段；合同 limit_price 保持 None，不入账、不参与价差、数量或修改计算。account/clientId/conId/orderRef/orderId/permId/SELL/STP/GTC/outsideRth=False/STK/USD/auxPrice按分/数量继续严格核验，STP LMT 继续拒绝。PreSubmitted 作为工作单，关闭 A 空窗。FakeBroker 默认有限 echo，并覆盖 None/有限 echo 变体；不能将触发价改成回传的 340.19。

测试：真实原始 ApiPending → PendingSubmit → PreSubmitted 字段经原生 wrapper/adapter 重放；要求5秒内 WORKING、S=3、无 P0/P1、查询上限合规。快照、撤单、modify、独立价格观察及严格身份反例全部覆盖。

调查结论：[IBKR STP说明](https://www.interactivebrokers.com/docs/general/order-types/stop)说明触发后为市价单；[字段说明](https://www.interactivebrokers.com/docs/tws-api/doc/orders/the-order-and-contract-objects)说明 auxPrice 用作止损触发价；[状态说明](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/order-status/understanding-order-status-message)说明 PreSubmitted 已接受并等待触发。**本例 lmtPrice=340.19000000000005、相对340.22低0.03的含义未知**，官方资料未给出这个回传值的生成规则，未猜测为限价或价格管理参数。

## 3 查询风暴、退避与生命周期：发现 → 改动 → 测试

发现：UNKNOWN 不走稳态查询间隔，每 tick 三源查询；查询本身产生的价格观察置 urgent_pending，使 pump 跳过 sleep，反馈成连续查询。原生 ib_insync 默认45条/秒。885次/分钟、10,819消息和TWS界面/人工平仓是指令提供事实，所给回调片段不足以独立复算全会话总数。

改动：原生 Client.sendMsg 层统一每滚动1秒最多10条；openOrders（含两种opcode）、positions、executions 各通道间隔>=1秒。`[ibkr]` 新增 open_orders_min_interval_seconds、positions_min_interval_seconds、executions_min_interval_seconds，默认均1，拒绝非有限或小于1；纳入配置指纹。订单写入保持顺序并优先调度，仍遵守总预算。连接代更换清除旧队列/timer，历史发送预算不伪装成新鲜证据；旧 End 不给当前查询完整性权限。

发送间隔采用每条实际发送边界，不能沿用被审计/回调耗时滞后的旧时间。

同持仓真实核验失败按1/2/4/8/16/30秒退避，UNKNOWN/hold/冻结不解除；首次GTC不受错误退避阻挡。所有需要第二轮/额外轮证据的恢复阶段，只有整个阶段通过才清失败记录；合法人工 resume 查询也共享持仓重试门槛。完整且预期的撤单 absence 不制造核验失败、不抹掉刚取得的三源数量证据。新订单/修改代/连接代和合同绑定防止旧证明复用。查询限速 P1 每持仓只发一次。

测试：原生 wire conn.sendMsg 计数，5分钟持续错误身份、通道>=1秒、全局<=10条/秒、指数退避、其他持仓及心跳公平性；断线/reset/旧timer/旧End/重入新代队列；第二轮失败和resume旁路；原有恢复、数量及 S+T+X<=P 强断言继续保留。

注意：新配置字段改变配置指纹，既有 Live 审批应按原有指纹绑定规则重新核验；未读取或改写任何审批/运行配置。Paper状态身份规则未放宽。离线模拟不能替代下一次 Paper 原生行为实测。

## 4 卡片去重与展示：发现 → 改动 → 测试

发现：保护 P0 后，同订单 held-working 又发P1；cap 显示二进制浮点尾数；TP价拒卡缺数量；TWS模式和日报显示Gateway；2108逐条wrapper日志与adapter日志重复且可能转义中文。

改动：同保护事件P0后抑制 held-P1，普通DAY held-P1保留；cap显示两位小数但原始audit不改；TP价格拒绝卡仅完整、同上下文/同代、新鲜三源证据显示确定P/S/未保护，证据不足继续待核实；可见连接/心跳/日报标签统一IBKR，机器subject/key不变。连接类通知只输出一次可读中文，原始文本保留审计，拒单原因原文不改。[官方2108说明](https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes)将其列为行情服务闲置通知。

测试：实际 Engine → Alerter → embed/plaintext 路径覆盖 cap、P6/S4/未保护2、启动/心跳/恢复/日报、2108原生wrapper重复日志与原始审计、保护P0去重和DAY P1保留。实际卡片样例附第7节。

## 5 红测、回归与验收真实性

B23冻结archive仅离线导入，不改基线。有效红测：red_fixture_correction_locked为74项、48自然失败/26通过/0errors；red_presentation_attempt2为8项、7自然失败/1通过/0errors。补充日报/第二轮retry/red receipts见下表和原始txt/xml/json。按修复项核对行为断言，不把自然通过的安全反例说成红测失败。

早期夹具错误和候选失败证据全部保留：green首轮78通过/4夹具失败；修夹具后82通过；扩大focused首轮751通过/36失败。失效初期red尝试不作验红依据（路径/事件循环/原生夹具错误）；root_red_*收据说明了有效版本。没有通过重试隐藏产品回归，也没有把这些聚焦结果冒充全量。

36项回归包括可见IBKR文案迁移、旧finite-echo非法oracle、两个真正恢复/数量证据问题。真实修补已独立复核。发生真实查询失败后的旧零时间重查oracle改为真实Rig时钟0/.999/1.000001秒边界，保留状态、数量、零写入和恢复强断言。原CRLF文件保留原行尾，使用单次 `git -c core.whitespace=cr-at-eol diff --check`，未修改Git配置。

扩大聚焦第二次896项为891通过/5失败（focused_attempt2，保留原始证据）：1项仍要求可见Gateway旧文案；3项用object.__new__绕过native adapter构造，缺新查询生命周期状态；1项在首次真实核验失败同一FixedClock立即要求补量。实施Agent从合成状态库mode=ro实证首失败1秒due与迟到成交同秒；独立调查Agent从代码、FakeBroker和FixedClock静态复核得到一致结论，生产恢复分支完整，无需生产改动。测试改用真正adapter constructor加本地IB stand-in，保留跨client账户过滤、ownership和不入账断言；B20在0/.999秒要求P30/S20、三源查询和写入全无，累计1.000001秒后同订单确认S30并最终平0。13生产哈希不变，101新规格字节不变；聚合code_sha因三历史测试修改改变；fe3fc54d6cd33a54中间候选为895通过/1失败（focused_attempt3）。剩余失败是驱动遗漏：0秒priority continuation已消费优先标记，后续仅_settle不会运行到期常规核验；真实主循环每轮调用engine.tick。测试在累计1.000001秒后调用真实rig.tick（不更改私有priority、时钟或retry门槛），继续要求同订单S30和最终P0。最终验收候选为{code}；所有历史失败收据均保留。这些是确定性的夹具/驱动口径失败，没有标记或豁免为时序flake。

第一次默认全量 full_1（候选62a9b82bb52ec22c）为1558项：1534 passed / 23 failed / 1 skipped / 0 errors，立即停止第2/3次。1项是真实回归：F4持续证据失败的60秒P0只在下一次查询失败时判断，新退避使59秒失败后到67秒才能再查，60秒卡片漏发。保留原B16 60秒、每tick退出/扫描和禁止MKT强断言；新增无查询截止测试先在未修生产候选自然失败。修复将持久since/paged截止检查纳入非阻塞保护deadline路径，不加查询、不置urgent、不增加failurecount或改until，并沿用原卡片去重。完整成功证据仍是唯一清理失败记录的路径。

其余22项是原生离线fixture缺API-ready：16项B20时间测试和5+1项旧fixed-key请求测试在请求前被新增连接代际检查拒绝。仅本地fixture模拟connected/API-ready，保留真实decoder/wrapper/future及所有时间/经济/身份、late-End、sent/end、dirty-channel不发、renew和stream去重断言。进一步锁定incomplete request真实进入并产生TimeoutError（原场景确为本轮失败，未假通过）；另修正两个原宽异常可能误通过的场景：partial-row+322必须真正回放并抛精确RequestError，selfreview.down必须真正进入、抛ConnectionError且sent=end=0。本地fixture不调用connect，生产断线/代际拒绝门槛未弱化。首轮失败收据保留；最终连续三次从第1次重新开始。

B23默认集合是1455项（1454 passed + 1 skipped），此前1454是通过数。首轮1558=1455+101新规格+2张finite-echo卡片参数案例；截止补丁再增加常规用例，最终集合以全量collection inventory为准。差异核对见collection_comparison.json。

为什么之前1454项没覆盖：FakeBroker和原生测试通常把请求limit_price=None照抄成回传；旧测试甚至把有限STP echo列为非法identity，没有真实 PendingSubmit/PreSubmitted回调字段序列。新增常规变体和原生回放纠正该盲点。

执行环境：Windows本机、dev .venv、Python3.11.9、pytest9.0.3、ib_insync0.9.86；PYTHONUTF8=1/PYTHONDONTWRITEBYTECODE=1，移除优化/测试筛选等继承环境，禁自动plugin；全量集合和执行nodeid逐项匹配JUnit，测试树/脚本/用例哈希前后锁定。

|收据|结果|开始/结束PT|
|---|---|---|
'''
    for entry in checkpoint['valid_red_receipts']:
        xml_path=checked_path(EV,entry['xml']); receipt=json.loads(checked_path(EV,entry['receipt']).read_text(encoding='utf-8'))
        tree=ET.parse(xml_path).getroot()
        totals={name:sum(int(x.attrib.get(name,0)) for x in tree.iter('testsuite')) for name in ('tests','failures','errors','skipped')}
        assert totals['failures']>0 and totals['errors']==0
        assert receipt.get('code_sha',receipt.get('source_code_sha'))=='a17d647ba28c51e4'
        if 'source_sha256' in receipt:
            from run_batch24_checks import BASE
            assert receipt['source_sha256']==receipt['source_sha256_after']
            assert all(sha(checked_path(BASE,path))==digest for path,digest in receipt['source_sha256'].items())
            assert all(sha(checked_path(ROOT,path.split('::',1)[0]))==digest for path,digest in receipt['test_sha256'].items())
        else:
            raw_receipt(receipt)
            assert receipt['baseline'] and receipt['returncode']==1 and receipt['source_unchanged'] and receipt['source_trees_unchanged'] and receipt['specs_unchanged']
            assert all(sha(Path(item['path']))==item['sha256'] and Path(item['path']).resolve().is_relative_to(ROOT.resolve()) for item in receipt['specs'])
        text+=f"|B23红：{entry['xml']}|{totals['tests']-totals['failures']-totals['skipped']} passed / {totals['failures']} natural failures / 0 errors|冻结archive，原始receipt附目录|\n"
    text+=f"|首次全量失败：full_1.json|1534 passed / 23 failed / 1 skipped / 0 errors；停止后两次|{first_full['start_pt']} → {first_full['end_pt']}|\n"
    text+=f"|未修生产候选自然红：{regression['receipt']}|{regression_red['totals']['tests']-regression_red['totals']['failures']-regression_red['totals']['skipped']} passed / {regression_red['totals']['failures']} failures / 0 errors|{regression_red['start_pt']} → {regression_red['end_pt']}|\n"
    for record,path in [(green,gp),(focused,fp),*full]:
        passed,skipped=valid(record,code)
        text+=f"|{path.name}|{passed} passed / {skipped} skipped / 0 failed / 0 errors|{record['start_pt']} → {record['end_pt']}|\n"
    if not args.final: text+='|最终全量1/2/3|未完成验收；首轮失败已保留，修复后重新串行三次|待执行|\n'
    text+='''
每次全量启动前重新检查PT时段、无运行/不可检查的Python进程、dev/HEAD/空暂存区和保留哈希。下一交易时段前5秒截止由 Windows Job Object 终止本次验收子进程树，只处理新建验收进程；独立审阅与隐藏CREATE_NEW_CONSOLE后代超时smoke见 acceptance_process_smoke.json、independent_review_final.json。

## 6 独立复核与提交范围

独立只读复核覆盖所有本批生产文件、原生协议生命周期、严格身份、fresh三源证据、二轮/replacement/resume退避、A/B门槛及原测试断言；收据见 independent_review_final.json。root 最终验证串行，未与代码修改并行。

仅提交本批生产/测试差异、指令、脱敏回调、本批顶层证据、报告和清单。排除操作手册、item8研究、batch23临时archive、baseline解压树、test_tmp、数据库/WAL/SHM、缓存、配置与机密。暂存前另作路径、机密与大文件检查；不push、不合并main。完成 dev commit 与附注 v6lite-batch24 后冻结，等待Claude复核。

## 7 实际渲染样例

'''
    samples=EV/'render_samples.md'
    assert samples.exists()
    sample_record=json.loads((EV/'render_samples.json').read_text(encoding='utf-8'))
    assert sample_record['source_code_sha']==code and sample_record['source_unchanged'] and sample_record['network_connections_allowed'] is False
    assert len(sample_record['samples'])==7 and all(item['cards'] and item['plain_messages'] for item in sample_record['samples'])
    rendered=['# Batch 24 actual Alerter card samples','','Source SHA: '+code,'']
    for sample in sample_record['samples']:
        rendered.extend(['## '+sample['scenario'],''])
        for message in sample['plain_messages']:
            assert any(card['title']==message['title'] for card in sample['cards'])
            rendered.extend([message['body'],''])
    assert samples.read_text(encoding='utf-8')=='\n'.join(rendered), 'Markdown differs from captured plaintext'
    assert 'Source SHA: '+code in samples.read_text(encoding='utf-8')
    text+=samples.read_text(encoding='utf-8')+'\n'
    text+=f'''\n## 8 SHA-256清单\n\n{len(manifest)}项；机器可核验清单 `docs/batch24_evidence/sha256_manifest.json` 的 SHA-256 为 `{manifest_digest}`。不包含临时数据库/缓存/运行配置/操作手册；报告自身不进入清单，避免循环哈希，交付时另报。\n\n|文件|SHA-256|\n|---|---|\n'''
    for path,digest in manifest.items(): text+=f'|{path}|`{digest}`|\n'
    report=ROOT/'docs/V6_LITE_FIX_REPORT_ROUND24_2026-10-12.md'
    # Finish every check before publishing accepted metadata. Publish the manifest last.
    guard()
    assert package_sha(ROOT/'v6lite')==code
    assert all(sha(checked_path(ROOT,path))==digest for path,digest in checkpoint['locked_files'].items())
    assert all(sha(ROOT/path)==digest for path,digest in manifest.items())
    report_tmp=report.with_suffix('.md.staged')
    manifest_tmp=manifest_path.with_suffix('.json.staged')
    report_tmp.write_text(text,encoding='utf-8',newline='\n')
    manifest_tmp.write_text(manifest_text,encoding='utf-8',newline='\n')
    os.replace(report_tmp,report)
    os.replace(manifest_tmp,manifest_path)
    print(json.dumps({'status':'final' if args.final else 'full pending','code_sha':code,'manifest_items':len(manifest),'report_sha256':sha(report),'manifest_sha256':sha(manifest_path)},ensure_ascii=False))

if __name__=='__main__': main()
