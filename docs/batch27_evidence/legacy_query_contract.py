"""Exact two-token migration of the superseded successful-query log contract."""
from pathlib import Path
import ast,hashlib,json
REL='v6lite/tests/test_batch10.py'
NAME='test_F5_query_elapsed_includes_old_end_drain'
def sha(data):return hashlib.sha256(data).hexdigest()
def verify(root):
 root=Path(root);assert root==Path('C:/Dev/V6Lite-dev')
 ev=root/'docs/batch27_evidence'
 pre=json.loads((ev/'preflight.json').read_text(encoding='utf-8'))
 raw=(root/'.pytest_cache/batch27_baseline_27b'/REL).read_bytes()
 assert sha(raw)==pre['baseline_file_hashes'][REL]
 backup=ev/'initial_specs/test_batch10_pre_debug_contract.py'
 assert backup.read_bytes()==raw,'original legacy test not preserved'
 nodes=ast.parse(raw.decode('utf-8-sig')).body
 fn=next(n for n in nodes if isinstance(n,ast.FunctionDef) and n.name==NAME)
 lines=raw.splitlines(keepends=True);start=sum(map(len,lines[:fn.lineno-1]));end=sum(map(len,lines[:fn.end_lineno]))
 block=raw[start:end];assert block.count(b'logging.INFO')==2
 expected=raw[:start]+block.replace(b'logging.INFO',b'logging.DEBUG')+raw[end:]
 current=(root/REL).read_bytes();assert current==expected,'legacy test changed outside two authorized log levels'
 return {'path':REL,'function':NAME,'original_sha256':sha(raw),'current_sha256':sha(current),'token_substitution':'logging.INFO -> logging.DEBUG','substitution_count':2,'outside_function_bytes_unchanged':True,'all_other_function_bytes_unchanged':True,'reason':'Batch27 section2.3 replaces INFO success QUERY logs with DEBUG; elapsed/drain/native End and unique record assertions unchanged'}
def expected_hashes(root,old):
 new=dict(old);new[REL]=verify(root)['current_sha256'];return new
if __name__=='__main__':
 root=Path(__file__).resolve().parents[2];r=verify(root)
 (root/'docs/batch27_evidence/legacy_contract_migration.json').write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
 print(json.dumps(r,ensure_ascii=False))
