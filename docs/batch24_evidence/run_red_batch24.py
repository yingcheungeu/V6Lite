"""Run independent B24 contracts against the frozen git-archive B23 package."""
from pathlib import Path
import contextlib, hashlib, json, sys

root=Path(__file__).resolve().parents[2]
baseline=root/'.pytest_cache'/'batch24_baseline23'
sys.path.insert(0,str(baseline))
import v6lite
assert Path(v6lite.__file__).resolve().is_relative_to(baseline.resolve()), v6lite.__file__
import v6lite.tests
# Only new test modules are supplied from the actual workspace. All production
# modules and historical shared helpers remain pinned to the frozen archive.
v6lite.tests.__path__.append(str(root/'v6lite'/'tests'))
import pytest
out=root/'docs'/'batch24_evidence'
(out/'test_tmp').mkdir(parents=True,exist_ok=True)
name=sys.argv[1]
files=sys.argv[2:] or ['v6lite/tests/test_batch24_stp_echo.py']
def hashes():
    return {str(p.relative_to(baseline)):hashlib.sha256(p.read_bytes()).hexdigest() for p in baseline.glob('v6lite/**/*.py') if 'tests' not in p.parts}
receipt={'baseline_tag':'v6lite-batch23','baseline_head':'bd6b8fb9ecf8a08cf9685c8eaf6a46f00be794f0','source_file':v6lite.__file__,'code_sha':'a17d647ba28c51e4','source_sha256':hashes(),'test_files':files,'test_sha256':{name:hashlib.sha256((root/name.split('::',1)[0]).read_bytes()).hexdigest() for name in files}}
log=out/(name+'.txt')
with log.open('w',encoding='utf-8') as stream, contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
    print(json.dumps({k:v for k,v in receipt.items() if k!='source_sha256'},ensure_ascii=False))
    result=pytest.main(['-q','-p','no:cacheprovider','--import-mode=importlib','--basetemp',str(out/'test_tmp'/name),*files,'--junitxml',str(out/(name+'.xml'))])
receipt['pytest_exit_code']=result
receipt['source_sha256_after']=hashes()
assert receipt['source_sha256']==receipt['source_sha256_after']
(out/(name+'_receipt.json')).write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
print('\n'.join(log.read_text(encoding='utf-8').splitlines()[-18:]))
print('red_exit_code=',result)
