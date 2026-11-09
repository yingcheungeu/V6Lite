"""Preserve tested/source evidence bytes; narrowly check the frozen EOF blank."""
from pathlib import Path
import ast,hashlib,json,subprocess
root=Path('C:/Dev/V6Lite-dev');out=root/'docs/batch28c_evidence'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def check(config,*paths):
    return subprocess.run(['git','-c','core.whitespace='+config,'diff','--cached','--check','--',*paths],
        cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
strict='blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol'
remaining='blank-at-eol,-blank-at-eof,space-before-tab,cr-at-eol'
closing=json.loads((out/'closeout.json').read_text());manifest=json.loads((out/'SHA256SUMS.json').read_text())
stage=Path('C:/tmp/v6lite_b28c_stage_paths.json');paths=json.loads(stage.read_text())
new=[]
failure=check(strict,'.')
assert failure.returncode==2
receipt=out/'initial_commit_check_failure.json'
if receipt.exists():
    assert json.loads(receipt.read_text())['output']==failure.stdout.decode('utf-8')
else:
    receipt.write_text(json.dumps({'phase':'before commit/tag; only task files staged','returncode':failure.returncode,
        'output':failure.stdout.decode('utf-8'),'source_unchanged':True,
        'reason':'Raw captured error text whitespace plus one already-frozen test EOF blank; no test failure'},indent=2),encoding='utf-8')
new.append(receipt)
for name in ('commit','whitespace'):
    p=out/f'v6lite_b28c_{name}.py';saved=out/f'v6lite_b28c_{name}_initial.py'
    if saved.exists():assert saved.read_bytes()==p.read_bytes()
    else:saved.write_bytes(p.read_bytes())
    new.append(saved)
raw=[];details={}
for p in sorted(out.iterdir()):
    if p.suffix not in ('.txt','.xml'):continue
    name=p.relative_to(root).as_posix();result=check(strict,name)
    if result.stdout:
        assert result.returncode==2 and b'trailing whitespace' in result.stdout
        raw.append(name);details[name]={'sha256':sha(p),'diagnostics':result.stdout.decode('utf-8'),
            'reason':'Preserve exact captured test error/output bytes; no modification of evidence'}
name='v6lite/tests/test_batch28c_native_latency.py';p=root/name
freeze=json.loads((out/'freeze.json').read_text());assert sha(p)==freeze['owned_files'][name]
result=check(strict,name)
assert result.returncode==2 and b'new blank line at EOF' in result.stdout and b'trailing whitespace' not in result.stdout
assert check(remaining,name).returncode==0
source=p.read_bytes()
assert ast.dump(ast.parse(source))==ast.dump(ast.parse(source.rstrip()+b'\n'))
note={'raw_log_whitespace_exclusions':raw,'details':details,
    'source_whitespace_exemptions':[],
    'frozen_test_EOF_blank':{name:{'sha256':sha(p),'diagnostics':result.stdout.decode('utf-8'),
        'reason':'Keep the exact independently specified and three-times-tested file bytes; EOF blank only.',
        'all_other_source_whitespace_checks_pass':True,'AST_equal_without_final_blank':True}},
    'raw_evidence_never_rewritten_to_hide_a_failure':True}
(out/'commit_check_note.json').write_text(json.dumps(note,ensure_ascii=False,indent=2),encoding='utf-8')

tool=Path('C:/tmp/v6lite_b28c_commit.py');text=tool.read_text()
text=text.replace("p.endswith('.txt')", "p.endswith(('.txt','.xml'))")
old="""run('git','-c','core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol',
    'diff','--cached','--check','--','.',*[f':(exclude){p}' for p in exclusions])"""
newtext="""eof=json.loads((ROOT/note).read_text()).get('frozen_test_EOF_blank',{})
assert set(eof)=={'v6lite/tests/test_batch28c_native_latency.py'}
for name,item in eof.items():
    assert sha(ROOT/name)==item['sha256']==freeze['owned_files'][name]
    run('git','-c','core.whitespace=blank-at-eol,-blank-at-eof,space-before-tab,cr-at-eol',
        'diff','--cached','--check','--',name)
run('git','-c','core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol',
    'diff','--cached','--check','--','.',*[f':(exclude){p}' for p in [*exclusions,*eof]])"""
assert old in text;text=text.replace(old,newtext,1)
tool.write_text(text,encoding='utf-8');(out/'v6lite_b28c_commit.py').write_bytes(tool.read_bytes())
# Preserve the original scanner as evidence; final scanner is this narrow audit.
audit=out/'v6lite_b28c_final_whitespace.py';assert not audit.exists()
audit.write_bytes(Path(__file__).read_bytes());new.append(audit)
report=root/'docs/V6_LITE_FIX_REPORT_ROUND28C_2026-10-21.md'
before=sha(report)
with report.open('a',encoding='utf-8') as stream:
    stream.write('\n提交空白检查：原始失败 TXT/XML 的报错空格保留；冻结的独立 native_latency 测试仅有末尾空白行，保持三次全量已测字节，其余空白规则通过，去掉末尾空白的 AST 相同。具体 SHA、诊断及首次提交检查失败见 commit_check_note.json / initial_commit_check_failure.json。未修改测试断言或重跑掩盖失败。\n')
for p in new:
    name=p.relative_to(root).as_posix();assert name not in paths;paths.append(name)
for p in [report,out/'commit_check_note.json',out/'v6lite_b28c_commit.py',*new]:
    manifest['files'][p.relative_to(root).as_posix()]=sha(p)
(out/'SHA256SUMS.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
closing.update(report_before_whitespace_note_sha256=before,report_sha256=sha(report),
    manifest_sha256=sha(out/'SHA256SUMS.json'),frozen_source_EOF_blank_preserved=True)
(out/'closeout.json').write_text(json.dumps(closing,ensure_ascii=False,indent=2),encoding='utf-8')
stage.write_text(json.dumps(paths,indent=2),encoding='utf-8')
print(json.dumps({'raw_evidence_exclusions':len(raw),'frozen_EOF_only':list(note['frozen_test_EOF_blank']),
    'report_sha256':closing['report_sha256'],'manifest_sha256':closing['manifest_sha256']}))
