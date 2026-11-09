from pathlib import Path
import hashlib,json
root=Path('C:/Dev/V6Lite-dev');out=root/'docs/batch28c_evidence'
previous=json.loads((out/'spec_contracts_before_encoding_fix.json').read_text())
encoding=json.loads((out/'encoding_fix.json').read_text())
changed={r['path']:r for r in encoding}
for name,digest in previous.items():
    raw=(root/name).read_bytes()
    if name in changed:
        assert hashlib.sha256(b'\xef\xbb\xbf'+raw).hexdigest()==digest,name
        assert hashlib.sha256(raw).hexdigest()==changed[name]['after_sha256'],name
    else:assert hashlib.sha256(raw).hexdigest()==digest,name
receipt={'root_is_sole_production_editor':True,'parallel_agents_read_only_final_reviews':True,
 'original_spec_assertions_unchanged':True,'old_spec_encoding_only_changes':encoding,
 'raw_generation_isolation':{'red':'red_final_review','fix':'installation-bound socket/owner guards before SDK mutation; reinstall without wrapper stacking'},
 'ACK_collection':{'red_control':'red_pair_window_calibrated','old_window_ACK_to_wire_seconds':4.234,
    'native_timer_calibration':'First assertions used theoretical .40s cutoff; corrected to actual callback time and explicit .30s ordinary pass.',
    'deadline_red':'red_review_deadline','fix':'Shared bounded first-component/request/proof deadlines at ordinary and callback entry; duplicates cannot extend.'},
 'generic_P0':{'red':'red_final_review','additional_red':'red_review_deadline',
    'fix':'Only exact own GTC delta with unique original STP can supersede stale P; independent unknown BUY/SELL/missing/duplicate STP remains Contradiction/P0.'},
 'full_failure':{'receipt':'final_full_1','reason':'UTF-8 BOM rejected by pre-existing AST dependency check',
    'retained':True,'counts_toward_final_three':False,'classification':'test source encoding regression, not a timing flake'},
 'failed_attempts_overwritten':False,'timing_flake_waivers':[]}
(out/'review_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
print('Original test assertions verified unchanged; review/failed-full evidence retained')
