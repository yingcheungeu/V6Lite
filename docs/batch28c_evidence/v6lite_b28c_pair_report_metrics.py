"""Pair final offline wire/B evidence by testcase; update only delivery docs."""
from pathlib import Path
import hashlib,json
root=Path('C:/Dev/V6Lite-dev');out=root/'docs/batch28c_evidence'
report=root/'docs/V6_LITE_FIX_REPORT_ROUND28C_2026-10-21.md'
closing=json.loads((out/'closeout.json').read_text());manifest=json.loads((out/'SHA256SUMS.json').read_text())
paths=json.loads(Path('C:/tmp/v6lite_b28c_stage_paths.json').read_text())
assert hashlib.sha256(report.read_bytes()).hexdigest()==closing['report_sha256']
paired=[]
for label in closing['full_labels']:
    evidence=json.loads((out/f'{label}_latency_measurements.json').read_text())
    for ack in evidence['ACK_to_TP_physical_wire']:
        matching=[s for s in evidence['gap_B_samples'] if
                  ack['test'].startswith(s['test_database'].split('/')[0].rstrip('0123456789'))]
        assert len(matching)==1,(label,ack['test'])
        sample=matching[0];assert len(sample['gap_B'])==1
        kind={'b28c_ACK_to_TP_wire_seconds':'openOrder → status',
              'b28c_status_first_ACK_to_TP_wire_seconds':'status → openOrder',
              'b28c_separated_ACK_to_TP_wire_seconds':'status → 0.30s pass → 0.40s openOrder'}[ack['name']]
        paired.append({'run':label,'case':kind,'test':ack['test'],'ACK_to_wire_seconds':ack['seconds'],
                       'gap_B_seconds':sample['gap_B'][0]['secs'],
                       'XML_sha256':evidence['XML_sha256'],
                       'test_database':sample['test_database'],'database_sha256':sample['database_sha256']})
assert len(paired)==9 and all(0<=r['ACK_to_wire_seconds']<=1 and 0<=r['gap_B_seconds']<=5 for r in paired)
before=closing['report_sha256']
text=report.read_text(encoding='utf-8').split('\n## 原生出站与空窗 B 读数')[0]
text=text.replace('原始输出 hash 与脱敏链保留。',
    '验证输出在采集时脱敏；后处理发生替换的文件另保留输入/输出 SHA，完整回执均保留。')
text+='\n## 原生出站与空窗 B 读数\n\n按同一测试用例配对实际 conn.sendMsg 时间与离线 SQLite 审计，单位秒。\n\n'
text+='| 全量回执 | 原生回调时序 | ACK→实际 TP wire（上限 1） | 空窗 B（上限 5） |\n|---|---|---|---|\n'
text+='\n'.join(f"| {r['run']} | {r['case']} | {r['ACK_to_wire_seconds']:.6f} | {r['gap_B_seconds']:.6f} |" for r in paired)
text+='\n\n逐用例配对、XML/数据库 SHA 见 paired_latency.json；各轮原始度量见同标签 _latency_measurements.json。未读取实盘状态库。\n'
report.write_text(text,encoding='utf-8')
new={out/'paired_latency.json':json.dumps(paired,ensure_ascii=False,indent=2).encode('utf-8'),
     out/'v6lite_b28c_pair_report_metrics.py':Path(__file__).read_bytes()}
for p,raw in new.items():
    assert not p.exists(),p
    p.write_bytes(raw)
    name=p.relative_to(root).as_posix();manifest['files'][name]=hashlib.sha256(raw).hexdigest();paths.append(name)
after=hashlib.sha256(report.read_bytes()).hexdigest()
manifest['files'][report.relative_to(root).as_posix()]=after
(out/'SHA256SUMS.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
closing.update(report_before_pairing_sha256=before,report_sha256=after,
    manifest_sha256=hashlib.sha256((out/'SHA256SUMS.json').read_bytes()).hexdigest(),paired_native_latency_verified=True)
(out/'closeout.json').write_text(json.dumps(closing,ensure_ascii=False,indent=2),encoding='utf-8')
Path('C:/tmp/v6lite_b28c_stage_paths.json').write_text(json.dumps(paths,indent=2),encoding='utf-8')
print(json.dumps({'paired_cases':len(paired),'report_sha256':after,'manifest_sha256':closing['manifest_sha256']}))
