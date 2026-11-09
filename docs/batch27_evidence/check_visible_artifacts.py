"""Check actual captured offline artifacts; no broker or database access."""
from pathlib import Path
import hashlib,json,re
ROOT=Path(__file__).resolve().parents[2];EV=Path(__file__).resolve().parent
ACCOUNT=re.compile(r'\b(DFA|DU|DF|U|F|I)([0-9]{4,})\b')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 label='samples_final_green'
 receipt=json.loads((EV/(label+'.json')).read_text(encoding='utf-8'))
 assert receipt['returncode']==0 and receipt['totals']['tests']==98 and receipt['totals']['failures']==receipt['totals']['errors']==0
 reports=json.loads((EV/(label+'_reports.json')).read_text(encoding='utf-8'))
 assert reports and any('B27-scale-SCAN' in x['markdown'] for x in reports)
 checked=[]
 for item in reports:
  text=item['markdown'];assert len(text.encode('utf-8'))<=100*1024 and not ACCOUNT.search(text)
  events=text.split('## Events\n',1)[1] if '## Events\n' in text else ''
  events=re.split(r'(?m)^## ',events,maxsplit=1)[0]
  assert len(events.splitlines())<=200,(item['nodeid'],len(events.splitlines()))
  checked.append({'nodeid':item['nodeid'],'bytes':len(text.encode('utf-8')),'events_lines':len(events.splitlines())})
 saved=[]
 temp=ROOT/'.pytest_cache/batch27_tmp'/label/'pytest'
 for source in sorted(temp.rglob('*.md')):
  assert source.resolve().is_relative_to(temp.resolve()) and not source.is_symlink()
  text=source.read_text(encoding='utf-8')
  if not text.startswith('# V6-Lite daily report '):continue
  assert source.stat().st_size<=100*1024
  assert len(text.encode('utf-8'))<=100*1024 and not ACCOUNT.search(text)
  events=text.split('## Events\n',1)[1] if '## Events\n' in text else ''
  events=re.split(r'(?m)^## ',events,maxsplit=1)[0];assert len(events.splitlines())<=200
  sidecar=source.with_suffix('.critical.jsonl')
  assert sidecar.exists() and sidecar.name in text and sha(sidecar) in text
  saved.append({'relative_test_source':source.relative_to(temp).as_posix(),'source_sha256':sha(source),'source_line_endings':'CRLF' if b'\r\n' in source.read_bytes() else 'LF','captured_markdown_line_endings':'LF','bytes':source.stat().st_size,'events_lines':len(events.splitlines()),'sidecar_sha256':sha(sidecar),'markdown':text})
 assert saved
 (EV/(label+'_saved_main.json')).write_text(json.dumps(saved,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
 attachments=json.loads((EV/(label+'_attachments.json')).read_text(encoding='utf-8'));assert attachments
 for item in attachments:
  p=EV/item['captured_file'];assert p.parent==EV and not p.is_symlink()
  assert sha(p)==item['published_sha256'] and p.stat().st_size==item['bytes']
  text=p.read_text(encoding='utf-8');assert not ACCOUNT.search(text)
  lines=text.splitlines();assert len(lines)==item['lines']
  for line in lines:
   row=json.loads(line);assert all(k in row for k in ('id','ts','kind','payload','source'))
   assert isinstance(row['id'],int) and isinstance(row['payload'],str)
   assert row['id']>0 if row['source']=='audit' else row['id']<0
 result={'code_sha':receipt['source_code_sha'],'reports_checked':checked,'saved_main_checked':[{k:v for k,v in x.items() if k!='markdown'} for x in saved],'attachments_checked':attachments,'account_masking':True,'all_checks_passed':True,'scope':'captured synthetic dev test outputs only; no database or runtime read'}
 (EV/'visible_artifact_checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
 print(json.dumps({'pure_renders':len(checked),'saved_main':len(saved),'max_saved_main_bytes':max(x['bytes'] for x in saved),'attachments':len(attachments),'max_report_bytes':max(x['bytes'] for x in checked),'max_events_lines':max(x['events_lines'] for x in checked)}))
if __name__=='__main__':main()
