from pathlib import Path
import hashlib,json
root=Path('C:/Dev/V6Lite-dev');out=root/'docs/batch28c_evidence'
report=root/'docs/V6_LITE_FIX_REPORT_ROUND28C_2026-10-21.md'
closing=json.loads((out/'closeout.json').read_text());manifest=json.loads((out/'SHA256SUMS.json').read_text())
assert closing['serial_full_runs']==3 and closing['source_unchanged']
rows=[]
for label in closing['full_labels']:
    record=json.loads((out/f'{label}_latency_measurements.json').read_text())
    ack=[r['seconds'] for r in record['ACK_to_TP_physical_wire']]
    gaps=[g['secs'] for s in record['gap_B_samples'] for g in s['gap_B']]
    assert len(ack)==len(gaps)==3 and max(ack)<=1 and max(gaps)<=5
    rows.append(f"| {label} | {' / '.join(f'{n:.6f}' for n in ack)} | {' / '.join(f'{n:.6f}' for n in gaps)} |")
before=hashlib.sha256(report.read_bytes()).hexdigest();assert before==closing['report_sha256']
with report.open('a',encoding='utf-8',newline='') as f:
    f.write('\n## 原生出站与空窗 B 读数\n\n以下为两种回调顺序及相隔 0.40 秒且经过普通 pass 的三组回放的实际 conn.sendMsg 时间与离线 SQLite 审计，单位秒。\n\n')
    f.write('| 全量回执 | ACK→实际 TP wire（上限 1） | 空窗 B（上限 5） |\n|---|---|---|\n'+'\n'.join(rows)+'\n')
    f.write('\n每轮独立度量及测试数据库 SHA 见同标签 `_latency_measurements.json`；未读取实盘状态库。\n')
after=hashlib.sha256(report.read_bytes()).hexdigest()
manifest['files'][report.relative_to(root).as_posix()]=after
(out/'SHA256SUMS.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
closing['report_before_measurements_sha256']=before;closing['report_sha256']=after
closing['manifest_sha256']=hashlib.sha256((out/'SHA256SUMS.json').read_bytes()).hexdigest()
closing['actual_native_latency_measurements_in_report']=True
(out/'closeout.json').write_text(json.dumps(closing,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'report_sha256':after,'manifest_sha256':closing['manifest_sha256']}))
