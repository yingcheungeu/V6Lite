from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib,json,subprocess,sys,xml.etree.ElementTree as ET
root=Path('C:/Dev/V6Lite-dev');out=root/'docs/batch28b_evidence'
sys.path.insert(0,str(root))
from v6lite.telemetry import redact_accounts
rows=[]
classification={
 'red_batch28':'isolated unmodified v6lite-batch28; all 8 new behavior contracts fail',
 'green1':'development candidate: false alerts removed; positive cases exposed 15s phase starvation',
 'green2':'completed proof wakes pending decisions; original frozen 8 specs pass',
 'focused_overnight1':'development: 9 old expectations fail; reject overbroad request-failure suppression',
 'focused_overnight2':'explicit server failures retained; only 3 generation/day expectations superseded by B28b remain',
 'focused_overnight3':'3 obsolete generation/day P1 expectations migrated, all actual-failure safety assertions retained',
 'focused_edges1':'new budget, 15s, native End timeout/generation and core timing behavior',
 'focused_callbacks1':'original stop full/partial fill race and subsequent fresh contradiction behavior',
 'focused_order_safety1':'validation infrastructure error: basetemp parent absent; 284 setup errors, not product failures',
 'focused_order_safety2':'deterministic focused-collection cold SDK import in network-forbidden fixture; Windows event-loop socketpair, not TWS',
 'focused_order_safety3':'native SDK collection included; no network guard weakened; 304 passed',
 'focused_overnight_final':'real product regression: superseded-state exception hid existing short/other-SELL P0; must fix, old tests unchanged',
 'focused_overnight_final2':'fixed: only proven own-fill closure can supersede; known dangers retain original P0',
 'focused_order_safety4':'final source order-safety focused validation',
}
for p in sorted(out.glob('*.xml')):
    tree=ET.parse(p).getroot();suites=[tree] if tree.tag=='testsuite' else list(tree)
    counts={key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')}
    rows.append({'label':p.stem,'classification':classification.get(p.stem,'validation receipt'),**counts,
        'xml_file':p.name,'raw_xml_sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
notes={'attempts':rows,'genuine_timing_flakes':0,
       'no_third_flake_waived':True,'all_failures_preserved':True,
       'historical_contracts_migrated':['B26 generation-change','B27b generation-reset','B27b cross-midnight'],
       'core_frozen_spec_sha256':'ac645f05a291c606ef1355a81f40ddfb0877014f53821706f14e1add97798278'}
(out/'development_attempts.json').write_text(json.dumps(notes,ensure_ascii=False,indent=2),encoding='utf-8')
review={'scope':'B28b source diff plus unchanged regression safety boundaries',
 'role':'independent read-only agent; no production edits, no pytest, no broker access',
 'final_verdict':'no remaining blocking findings after known-danger guard correction; final focused tests must pass',
 'resolved_findings':['new queued refresh must service protection before every blocking source',
  'normal overnight steady and extra stale refresh must share 15s clock even after denial',
  'completed proof must wake pending decisions to avoid aligned-15s starvation',
  'own-stop fill or legitimate callback binding change invalidates old snapshot',
  'only proven own-fill closure can be excused; visible short/other-SELL P0 retained'],
 'review_limitation':'initial final read-only review missed short/other-SELL interaction; actual regression tests caught it and were not changed',
 'preserved':['a-d','S+T+X<=P','strong identity','constraints A/B','full_exec restrictions','manual holds/freezes','parser unchanged']}
(out/'independent_review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf-8')
now=datetime.now(ZoneInfo('America/Los_Angeles'))
if now.weekday()<5 and (6,0)<=(now.hour,now.minute)<(13,30):
    probe=subprocess.run([sys.executable,'-B',str(out/'v6lite_b28b_validate.py'),'guard_probe','full'],
                         cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    assert probe.returncode!=0 and b'blocked during' in probe.stdout
    (out/'full_time_guard_probe.json').write_text(json.dumps({'expected_refusal':True,'returncode':probe.returncode,
        'output':probe.stdout.decode('utf-8'),'pytest_started':False},indent=2),encoding='utf-8')
else:
    assert (out/'full_time_guard_probe.json').exists(), 'probe is forbidden outside the trading window'
print(json.dumps({'recorded_attempts':len(rows),'full_time_guard_blocked':True}))
