from pathlib import Path
import hashlib,json,subprocess
root=Path('C:/Dev/V6Lite-dev');out=root/'docs/batch28c_evidence'
empty=Path('C:/tmp/v6lite-b28c-empty.txt')
if not empty.exists():empty.write_bytes(b'')
assert empty.read_bytes()==b''
exclusions=[];details={}
for p in sorted(out.glob('*.txt')):
    check=subprocess.run(['git','-c','core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol',
        'diff','--no-index','--check','--',str(empty),str(p)],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    if check.stdout:
        name=p.relative_to(root).as_posix();exclusions.append(name)
        details[name]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                       'diagnostics':check.stdout.decode('utf-8',errors='replace'),
                       'reason':'Preserve exact captured failed/successful test evidence bytes; source checks remain strict.'}
note={'raw_log_whitespace_exclusions':exclusions,'details':details,
      'source_whitespace_exemptions':[],'raw_evidence_never_rewritten_to_hide_a_failure':True}
(out/'commit_check_note.json').write_text(json.dumps(note,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'raw_log_exclusions':exclusions,'source_exemptions':[]}))
