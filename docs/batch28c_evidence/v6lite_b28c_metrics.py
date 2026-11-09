from pathlib import Path
import hashlib,json,sqlite3,sys,xml.etree.ElementTree as ET
root=Path('C:/Dev/V6Lite-dev');out=root/'docs/batch28c_evidence'
label=sys.argv[1]
result=json.loads((out/f'{label}.json').read_text())
assert result['returncode']==0 and not result['incomplete'] and result['source_unchanged']
base=Path('C:/tmp/v6lite-b28c-tests')/label
assert base.resolve().is_relative_to(Path('C:/tmp/v6lite-b28c-tests').resolve())
samples=[]
for p in base.rglob('v6lite.sqlite3'):
    with sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True) as conn:
        try:found=conn.execute("SELECT 1 FROM fills WHERE exec_id='b28c-native-tp1.01.01'").fetchone()
        except sqlite3.OperationalError:continue
        if not found:continue
        gaps=[json.loads(row[0]) for row in conn.execute("SELECT payload FROM audit WHERE kind='PROTECT_GAP'")]
        gaps=[r for r in gaps if r.get('gap_kind')=='B']
        assert gaps and all(0<=r['secs']<=5 for r in gaps)
        samples.append({'test_database':p.relative_to(base).as_posix(),
                        'database_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'gap_B':gaps})
xml=ET.parse(out/f'{label}.xml').getroot()
props=[{'test':c.get('name'),'name':prop.get('name'),'seconds':float(prop.get('value'))}
       for c in xml.iter('testcase') for prop in c.findall('properties/property')
       if prop.get('name','') in ('b28c_ACK_to_TP_wire_seconds','b28c_status_first_ACK_to_TP_wire_seconds','b28c_separated_ACK_to_TP_wire_seconds')]
assert len(props)==len(samples) and len(props) in (2,3)
assert all(0<=r['seconds']<=1 for r in props)
target=out/f'{label}_latency_measurements.json';assert not target.exists()
target.write_text(json.dumps({'label':label,'XML_sha256':hashlib.sha256((out/f'{label}.xml').read_bytes()).hexdigest(),
    'source':'actual native conn.sendMsg timestamps in XML; independent completed offline SQLite audit',
    'ACK_to_TP_physical_wire':props,'gap_B_samples':samples},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'label':label,'ACK_to_wire':[r['seconds'] for r in props],
                  'B_seconds':[g['secs'] for s in samples for g in s['gap_B']]}))
