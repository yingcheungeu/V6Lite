"""Validate source scope without importing production or writing bytecode."""
from pathlib import Path
import ast,hashlib,json,subprocess
from legacy_query_contract import expected_hashes,verify
ROOT=Path(__file__).resolve().parents[2];EV=Path(__file__).resolve().parent
BASE=ROOT/'.pytest_cache/batch27_baseline_27b'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def tree(p):
 h=hashlib.sha256()
 for f in sorted((x for x in p.rglob('*') if x.is_file() and '__pycache__' not in x.relative_to(p).parts),key=lambda x:x.relative_to(p).as_posix()):
  h.update(f.relative_to(p).as_posix().encode());h.update(b'\0');h.update(f.read_bytes());h.update(b'\0')
 return h.hexdigest()
def nodes(p):
 t=ast.parse(p.read_text(encoding='utf-8-sig'));r={}
 for n in t.body:
  if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):r[n.name]=n
  elif isinstance(n,ast.ClassDef):
   for d in n.body:
    if isinstance(d,(ast.FunctionDef,ast.AsyncFunctionDef)):r[n.name+'.'+d.name]=d
 return r
def dump(n):return ast.dump(n,include_attributes=False)
def main():
 pre=json.loads((EV/'preflight.json').read_text(encoding='utf-8'))
 expected=expected_hashes(ROOT,pre['baseline_file_hashes'])
 assert sha(ROOT/'docs/V6_LITE_OPERATOR_MANUAL.md')==pre['manual_preserved_sha256']
 assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()=='54a893ba655c8e04ef64cfcbb6d49a2ebcdcddaa'
 assert all(sha(ROOT/p)==s for p,s in expected.items() if p.startswith(('v6lite/tests/','scripts/','v6lite/examples/')) or p in ('v6lite/config.py','v6lite/risk.py','v6lite/sizing.py'))
 allowed={'v6lite/engine.py':{'Engine.__init__','Engine.shutdown','Engine._end_of_day'},'v6lite/report.py':{'_protection_health','daily_report'},'v6lite/store.py':{'Store.__init__','Store.close','Store.tx','Store.update_protection'},'v6lite/tp_evidence.py':{'TpEvidenceMixin._on_price_observation'},'v6lite/__main__.py':{'setup_logging','main'},'v6lite/broker/ibkr.py':{'IbkrBroker.__init__','IbkrBroker._reset_channels','IbkrBroker._clean_request','IbkrBroker.disconnect','IbkrBroker._complete_executions'}}
 assert all(sha(ROOT/p)==s for p,s in expected.items() if p not in allowed), 'unrelated baseline source changed'
 changes={}
 for rel,ok in allowed.items():
  a,b=nodes(BASE/rel),nodes(ROOT/rel);assert set(a)<=set(b)
  changed=[k for k in a if dump(a[k])!=dump(b[k])];assert set(changed)<=ok,(rel,changed)
  changes[rel]={'existing_definitions_changed':changed,'new_definitions':sorted(set(b)-set(a)),'references':{k:b[k].lineno for k in changed}}
 for rel in ('v6lite/report.py','v6lite/alerts.py'):
  a,b=nodes(BASE/rel),nodes(ROOT/rel)
  for k in a:
   if 'daily_health' in k:assert dump(a[k])==dump(b[k])
 a,b=nodes(BASE/'v6lite/broker/ibkr.py'),nodes(ROOT/'v6lite/broker/ibkr.py')
 def complete(n):return [dump(x.value) for x in ast.walk(n) if isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='complete' for t in x.targets)]
 assert complete(a['IbkrBroker._complete_executions'])==complete(b['IbkrBroker._complete_executions'])
 files=sorted(x for name in ('v6lite','scripts') for x in (ROOT/name).rglob('*.py') if '__pycache__' not in x.relative_to(ROOT).parts)
 for p in files:compile(p.read_text(encoding='utf-8-sig'),str(p),'exec')
 full=tree(ROOT/'v6lite')
 result={'code_sha':full[:16],'full_code_sha256':full,'compiled_python_files':len(files),'method':'stdlib compile only/no imports/no pyc; AST diff against immutable27b','original_scoped_execution_complete_expression_unchanged':True,'old_tests_scripts_unchanged_except_two_debug_tokens':True,'legacy_contract_migration':verify(ROOT),'phone_report_and_card_ast_unchanged':True,'only_listed_existing_definitions_changed':True,'existing_definition_changes':changes,'compiled_files':[p.relative_to(ROOT).as_posix() for p in files]}
 (EV/'source_compile.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
 print(json.dumps({k:v for k,v in result.items() if k not in ('existing_definition_changes','compiled_files')}))
if __name__=='__main__':main()
