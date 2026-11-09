"""Capture real offline cards, daily output and time summaries without changing results."""
import functools,json,logging,re
from pathlib import Path

def mask(text):
 return re.sub(r"\b(DU|U)([0-9]{5,})\b",lambda m:m[1]+"******"+m[2][-3:],text)

class Capture:
 def __init__(self,output):
  self.output=Path(output);self.records=[];self.reports=[];self.summaries=[];self.nodeid=''
 def pytest_runtest_setup(self,item):self.nodeid=item.nodeid
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
    if '[EXECUTION_TIME_SUMMARY]' in text:
     owner.summaries.append({'nodeid':owner.nodeid,'level':record.levelname,'text':text})
  logging.getLogger().addHandler(SummaryHandler())
 def write(self):
  for suffix,data in [('cards',self.records),('reports',self.reports),('query_summaries',self.summaries)]:
   path=self.output.with_name(self.output.name.removesuffix('_cards.json')+'_'+suffix+'.json')
   text=mask(json.dumps(data,ensure_ascii=False,indent=2))
   path.write_text(text+'\n',encoding='utf-8',newline='\n')
