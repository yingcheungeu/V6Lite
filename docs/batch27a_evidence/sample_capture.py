"""Capture actual fake-channel cards; never contact a remote alert service."""
import json,re
from pathlib import Path
class Capture:
 def __init__(self,output): self.output=Path(output);self.records=[];self.nodeid=''
 def pytest_runtest_setup(self,item): self.nodeid=item.nodeid
 def install(self):
  from v6lite.alerts import MemoryChannel
  original=MemoryChannel.send
  def send_card(channel,card):
   original(channel,card.title,card.as_plain_text())
   self.records.append({'nodeid':self.nodeid,'embed':card.as_embed(),'plain':card.as_plain_text()})
  MemoryChannel.send_card=send_card
 def write(self):
  text=json.dumps(self.records,ensure_ascii=False,indent=2)
  text=re.sub(r'\b(DU|U)([0-9]{5,})\b',lambda m:m[1]+'******'+m[2][-3:],text)
  self.output.write_text(text+'\n',encoding='utf-8',newline='\n')
