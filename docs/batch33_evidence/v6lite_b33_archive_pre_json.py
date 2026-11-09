from pathlib import Path
import hashlib,json,sys,zipfile
R=Path('C:/Dev/V6Lite-dev');O=R/'docs/batch33_evidence';sys.path[:0]=[str(R),str(O)]
from v6lite_b33_validate import inventory
assert not (O/'.validation.lock').exists()
f=json.loads((O/'freeze.json').read_text(encoding='utf-8'));assert inventory()==f['inventory']
with zipfile.ZipFile(O/'pre_quote_json_candidate.zip','x',zipfile.ZIP_DEFLATED) as z:
 for name in f['inventory']:z.write(R/name,name)
 for name in f['validation_tools']:z.write(O/name,'validation_tools/'+name)
 z.write(O/'freeze.json','freeze.json')
for name in ('freeze.json','source_compile.json','config_fingerprints.json','run_status.json'):
 p=O/name;p.rename(O/('pre_json_fix_'+name))
for p in list(O.glob('final_full_1*')):
 if p.is_file():p.rename(p.with_name('pre_json_fix_'+p.name))
src=O/'test_tmp/final_full_1';dst=O/'test_tmp/pre_json_fix_full_1'
assert src.resolve().parent==dst.resolve().parent==(O/'test_tmp').resolve()
if src.exists():src.rename(dst)
record={'reason':'intentional stop after read-only discovery of nonfinite quote values in new defer audit; not a random test flake','old_code_sha':f['code_sha'],'partial_full_receipt':'pre_json_fix_final_full_1.json','partial_full_is_not_a_pass':True,'candidate_archive_sha256':hashlib.sha256((O/'pre_quote_json_candidate.zip').read_bytes()).hexdigest(),'new_final_sequence_required':True}
(O/'quote_json_interruption.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
print(json.dumps(record,indent=2))
