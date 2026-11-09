"""Prepare/finalize offline B28c evidence. This tool never commits or touches broker state."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib, json, subprocess, sys

ROOT=Path('C:/Dev/V6Lite-dev');OUT=ROOT/'docs/batch28c_evidence'
REPORT=ROOT/'docs/V6_LITE_FIX_REPORT_ROUND28C_2026-10-21.md'
PT=ZoneInfo('America/Los_Angeles')
sys.path.insert(0,str(ROOT))
from v6lite.telemetry import redact_accounts
from v6lite.version import code_sha

OWNED=['v6lite/engine.py','v6lite/models.py','v6lite/overnight_evidence.py',
       'v6lite/protection_engine.py','v6lite/protection_confirmations.py','v6lite/tp_evidence.py',
       'v6lite/broker/ibkr.py','v6lite/broker/pacing.py']
OWNED += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'v6lite/tests').glob('test_batch28c_*.py'))]
INSTRUCTION='1d4da1454256eaf1966bb6a9ea584c1a6aa7f0654187174b0db974ff329f28eb'
TOOLS=['v6lite_b28c_validate.py','v6lite_b28c_progress.py','v6lite_b28c_closeout.py',
       'v6lite_b28c_baseline.py','v6lite_b28c_focused.py']
BASE='0d9a72a90bbfe0519049dff42bce0b3e23a8f9b0'
ACCEPTED={'docs/V6_LITE_FIX_REPORT_ROUND28B_2026-10-21.md':
          'de46035ba7de51c3cad196216c4f62bfa9a301e9959c5d8f5c0319ea05ccfe4d'}

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,obj):p.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
def inventory():
    return {p.relative_to(ROOT).as_posix():sha(p) for base in ('v6lite','scripts')
            for p in sorted((ROOT/base).rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
def package_sha():
    digest=hashlib.sha256()
    for p in sorted((ROOT/'v6lite').rglob('*'),key=lambda p:p.relative_to(ROOT/'v6lite').as_posix()):
        if p.is_file() and '__pycache__' not in p.parts:
            digest.update(p.relative_to(ROOT/'v6lite').as_posix().encode());digest.update(b'\0')
            digest.update(p.read_bytes());digest.update(b'\0')
    return digest.hexdigest()
def check_base():
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==BASE
    assert subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()=='dev'
    assert sha(ROOT/'docs/V6_LITE_BATCH28C_INSTRUCTIONS.md')==INSTRUCTION
    for name,digest in ACCEPTED.items():assert sha(ROOT/name)==digest,name

def report(freeze,runs):
    focused=json.loads((OUT/'focused_final_3.json').read_text())
    full='\n'.join(f"| {r['label']} | {r['tests']-r['skipped']} passed / {r['skipped']} skipped | {r['seconds']} s | {r['start_pt']} → {r['end_pt']} |" for r in runs)
    status='Windows 三次连续全量通过；dev 本地提交/annotated tag v6lite-batch28c，等待复核。' if runs else '源码已冻结；尚待连续三次全量，未提交/tag。'
    text=f'''# V6-Lite 第二十八批 28c 修复报告

日期：2026-10-21（PT）。{status}

基线：`v6lite-batch28b` / `{BASE}` / code_sha `3edad37f5f36037b`。
指令 SHA-256：`{INSTRUCTION}`。
候选 code_sha：`{freeze['code_sha']}`；完整 package SHA-256：`{freeze['package_sha256']}`。

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

最终聚焦：`{focused['label']}`，{focused['tests']-focused['skipped']} passed，{focused['seconds']} 秒，源码不变。
包含真实隔夜 TP1→仅 openOrder 保本→TP2→TP3 归零、10/22 重启、原生身份/状态/旧事件负向、成交竞态、原生 wire/拥塞报价和 b22–b28b 回归。
新用例持续使用独立经济/卖单容量观察；没有把 proof、status_seen_generation 或授权函数直接改成通过。

Windows 全量：

| 回执 | 结果 | 耗时 | 起止 PT |
|---|---|---|---|
{full or '| 待执行 | 三次连续全量完成前不提交/tag | — | — |'}

每次必须 returncode=0、完整 XML、同一 inventory 且 source_unchanged；全量在 13:30 PT 之后串行执行。
固定 inventory 共 {len(freeze['inventory'])} 文件；{freeze['compile_count']} 个 Python 源码 compile(source_bytes,path,exec) 通过。
跳过项为既有 legacy market_scanner.py 缺失的 parity 测试；record_property/xunit2 为报告格式警告。

## 交付与 SHA

`docs/batch28c_evidence/freeze.json` 固定源码、原指令、已接受报告、验证工具和完整 inventory。
`SHA256SUMS.json` 给出报告、源码、脱敏回执及所有交付证据 SHA-256；原始输出 hash 与脱敏链保留。
仅 dev 本地提交和 annotated tag `v6lite-batch28c`。不 merge、不 push、不部署，John 本机合并；交付后冻结等待 Claude 复核。
不连接 TWS/Gateway、不发真实订单、不碰用户 .v6lite/运行仓库/John 配置；不修改或暂存操作手册。
'''
    REPORT.write_text(text,encoding='utf-8')

def redact():
    path=OUT/'redaction_receipt.json'
    receipts=json.loads(path.read_text()) if path.exists() else {}
    for p in sorted(OUT.iterdir()):
        if p.suffix not in ('.txt','.xml','.json') or p.name in ('SHA256SUMS.json','redaction_receipt.json'):continue
        raw=p.read_bytes();text=raw.decode('utf-8');masked=redact_accounts(text)
        if masked!=text:
            p.write_bytes(masked.encode('utf-8'))
            item=receipts.setdefault(p.name,{'raw_sha256':hashlib.sha256(raw).hexdigest(),'synthetic_fixture_only':True})
            item.setdefault('steps',[]).append({'input_sha256':hashlib.sha256(raw).hexdigest(),'output_sha256':sha(p),
                'rule':'production redact_accounts DFA/DU/DF/U/F/I'})
    write(path,receipts)

mode=sys.argv[1]
check_base()
if mode=='prepare':
    before=inventory()
    count=0
    for name in before:
        if name.endswith('.py'):
            compile((ROOT/name).read_bytes(),name,'exec');count+=1
    assert before==inventory()
    freeze={'status':'candidate frozen; three serial full runs pending',
        'frozen_at_pt':datetime.now(PT).isoformat(),'base_commit':BASE,'base_tag':'v6lite-batch28b',
        'instructions_sha256':INSTRUCTION,'accepted_reports':ACCEPTED,
        'code_sha':code_sha(),'package_sha256':package_sha(),'owned_files':{p:sha(ROOT/p) for p in OWNED},
        'inventory':before,'inventory_sha256':hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
        'compile_count':count,'compile_method':'compile(source_bytes,path,exec), no writes/imports',
        'validation_tools':{name:sha(OUT/name) for name in TOOLS},
        'operator_manual_excluded':True,'full_tests_not_run_before_1330':True}
    write(OUT/'freeze.json',freeze)
    report(freeze,[])
    print(json.dumps({'code_sha':freeze['code_sha'],'compiled':count,'report':str(REPORT)}))
elif mode=='final':
    freeze=json.loads((OUT/'freeze.json').read_text())
    assert inventory()==freeze['inventory'] and code_sha()==freeze['code_sha'] and package_sha()==freeze['package_sha256']
    assert all(sha(OUT/name)==digest for name,digest in freeze['validation_tools'].items())
    labels=sys.argv[2:] or ['final_full_1','final_full_2','final_full_3']
    assert len(labels)==3
    runs=[json.loads((OUT/f'{label}.json').read_text()) for label in labels]
    assert all(r['returncode']==r['failures']==r['errors']==0 and r['source_unchanged'] and not r['incomplete'] for r in runs)
    assert len({r['inventory_sha256'] for r in runs})==1
    assert runs[0]['inventory_sha256']==freeze['inventory_sha256']
    assert len({(r['tests'],r['skipped']) for r in runs})==1
    assert all(r['args']==['v6lite/tests','scripts/tests'] for r in runs)
    assert all(datetime.fromisoformat(r['start_pt']).astimezone(PT)>=datetime(2026,10,21,13,30,tzinfo=PT) for r in runs)
    assert all(runs[i]['end_pt']<runs[i+1]['start_pt'] for i in (0,1))
    between=[json.loads(p.read_text()) for p in OUT.glob('*.json')
             if p.name not in ('freeze.json','SHA256SUMS.json','redaction_receipt.json','closeout.json')]
    assert not any(isinstance(r,dict) and r.get('args')==['v6lite/tests','scripts/tests'] and
        runs[0]['start_pt']<=r.get('start_pt','')<=runs[-1]['start_pt'] and r.get('returncode')!=0 for r in between)
    report(freeze,runs)
    redact()
    selected=[p for p in sorted(OUT.iterdir()) if p.is_file() and p.name not in ('SHA256SUMS.json','closeout.json','RESUME_AFTER_CLOSE.md')]
    files=[ROOT/p for p in OWNED]+[REPORT]+selected
    write(OUT/'SHA256SUMS.json',{'code_sha':freeze['code_sha'],'package_sha256':freeze['package_sha256'],
        'files':{p.relative_to(ROOT).as_posix():sha(p) for p in files}})
    write(OUT/'closeout.json',{'base_commit':BASE,'intended_tag':'v6lite-batch28c','code_sha':freeze['code_sha'],
        'package_sha256':freeze['package_sha256'],'report_sha256':sha(REPORT),'full_labels':labels,
        'same_inventory':True,'serial_full_runs':3,'source_unchanged':True,'all_full_after_1330_pt':True,
        'operator_manual_staged':False,'merge_push_deploy':False,'manifest_sha256':sha(OUT/'SHA256SUMS.json')})
    files += [OUT/'SHA256SUMS.json',OUT/'closeout.json']
    write(Path('C:/tmp/v6lite_b28c_stage_paths.json'),[p.relative_to(ROOT).as_posix() for p in files])
    print(json.dumps({'files':len(files),'report_sha256':sha(REPORT),'code_sha':freeze['code_sha']}))
elif mode=='redact':
    redact();print('Account masking receipts written; original hashes preserved.')
else:raise SystemExit('prepare, redact or final only')
