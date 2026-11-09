from pathlib import Path
import hashlib,json,sys
root=Path('C:/Dev/V6Lite-dev');out=root/'docs/batch28c_evidence'
receipts=[]
for p in sorted(out.glob('*.json')):
    item=json.loads(p.read_text(encoding='utf-8'))
    if isinstance(item,dict) and 'returncode' in item:
        receipts.append({k:item[k] for k in ('label','start_pt','end_pt','returncode','tests','failures','errors','skipped','source_unchanged','code_sha','inventory_sha256') if k in item}|{'receipt':p.name})
notes={'runs':receipts,'policy':'Every failed attempt retained under a unique label; no timing flake waived.',
       'fixture_corrections':[
          'Optional FakeBroker account_executions_today_scoped requires monkeypatch raising=False.',
          'Externally injected test SELL counts excluded from position-owned TP assertion; P0/no-owned-TP retained.',
          'Raw STP outside STK/USD is rejected at open_orders; test requires actual native own-End plus rejection, not later four-source completion.'
       ],'product_failures_repaired':[
          'PRICE_PENDING could not consume raw openOrder; cross-day persisted BE token also prevented confirmation.',
          'Own TP partial/exact fill invalidated P without changing STP binding or left old TP working remainder.',
          'SDK merged Trade hid raw contract/status conflicts; captured before native merge.',
          'Query callbacks woke their own repeated verification; removed unchanged own-query wakeup.',
          'Native status-first order consumed candidate too early; both ACK pair orderings now supported.',
          'Extra unbooked SELL visible in native query must remain a real contradiction, never own-TP supersession.'
       ],'external_runtime_access':False,'TWS_Gateway_connection':False,'real_orders':False,'operator_manual_modified_by_Codex':False}
(out/'development_attempts.json').write_text(json.dumps(notes,ensure_ascii=False,indent=2),encoding='utf-8')
(out/'spec_contracts.json').write_text(json.dumps({p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted((root/'v6lite/tests').glob('test_batch28c_*.py'))},indent=2),encoding='utf-8')
for name in ('metrics','evidence_notes'):
    dest=out/f'v6lite_b28c_{name}.py'
    source=Path(f'C:/tmp/v6lite_b28c_{name}.py')
    if not dest.exists():dest.write_bytes(source.read_bytes())
print('Development attempts/spec hashes preserved; frozen source unchanged')
