"""Capture real offline cards, daily output and time summaries without changing results."""
import functools,hashlib,json,logging,re
from pathlib import Path

def mask(text):
 return re.sub(r"\b(DFA|DU|DF|U|F|I)([0-9]{4,})\b",lambda m:m[1]+"******"+m[2][-3:],text)

class Capture:
 def __init__(self,output):
  self.output=Path(output);self.records=[];self.reports=[];self.summaries=[];self.attachments=[];self.nodeid=''
 def pytest_runtest_setup(self,item):self.nodeid=item.nodeid
 def pytest_runtest_teardown(self,item):
  temp=item.funcargs.get('tmp_path')
  if temp is None:return
  root=self.output.resolve().parents[2]
  temp=Path(temp).resolve()
  assert temp.is_relative_to(root/'.pytest_cache'/'batch27_tmp')
  for source in sorted(temp.rglob('*.critical.jsonl')):
   assert source.resolve().is_relative_to(temp) and not source.is_symlink()
   relative=source.relative_to(temp).as_posix()
   token=hashlib.sha256((item.nodeid+'|'+relative).encode()).hexdigest()[:16]
   dest=self.output.with_name(self.output.name.removesuffix('_cards.json')+'_attachment_'+token+'.jsonl')
   data=mask(source.read_text(encoding='utf-8'));dest.write_text(data,encoding='utf-8',newline='\n')
   self.attachments.append({'nodeid':item.nodeid,'relative_source':relative,'captured_file':dest.name,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'published_sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),'bytes':dest.stat().st_size,'lines':len(data.splitlines())})
 def install(self):
  from v6lite.alerts import MemoryChannel
  import v6lite.report as report_module
  original=MemoryChannel.send
  def send_card(channel,card):
   original(channel,card.title,card.as_plain_text())
   self.records.append({'nodeid':self.nodeid,'embed':card.as_embed(),'plain':card.as_plain_text()})
  MemoryChannel.send_card=send_card
  daily=report_module.daily_report
  @functools.wraps(daily)
  def daily_report(*args,**kwargs):
   text=daily(*args,**kwargs)
   self.reports.append({'nodeid':self.nodeid,'markdown':text})
   return text
  report_module.daily_report=daily_report
  owner=self
  class SummaryHandler(logging.Handler):
   def emit(self,record):
    text=record.getMessage()
    if any(marker in text for marker in ('[EXECUTION_TIME_SUMMARY]', '[QUERY_SUMMARY]', '[EXECUTION_TIME_AGGREGATE]')):
     owner.summaries.append({'nodeid':owner.nodeid,'level':record.levelname,'text':text})
  logging.getLogger().addHandler(SummaryHandler())
 def write(self):
  for suffix,data in [('cards',self.records),('reports',self.reports),('query_summaries',self.summaries),('attachments',self.attachments)]:
   path=self.output.with_name(self.output.name.removesuffix('_cards.json')+'_'+suffix+'.json')
   text=mask(json.dumps(data,ensure_ascii=False,indent=2))
   path.write_text(text+'\n',encoding='utf-8',newline='\n')
