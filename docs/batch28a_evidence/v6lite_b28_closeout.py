from pathlib import Path
import hashlib,json,shutil,subprocess,sys,xml.etree.ElementTree as ET
root=Path('C:/Dev/V6Lite-dev')
out=root/'docs/batch28a_evidence'
sys.path.insert(0,str(root))
from v6lite.version import code_sha

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

if sys.argv[1]=='prepare':
    review={'scope':'batch28a section1','production_review':'passed; no blocking findings',
            'resolved_findings':['SDK globalErrorEvent bypassing main-loop recovery','first physical backoff after prior backend-loss episode or already drained event','fatal page queue lost when alert worker closes/fails','preflight/config/cleanup/SystemExit(0) exit reporting','quoted-secret and Authorization redaction'],
            'test_policy_review':'passed; the new unconditional P1 rule supersedes night/pre-RTH muting; strict one-page and new-episode two-page counts; other function bytes unchanged',
            'reviewer_actions':'read-only; no tests executed by reviewer','final_code_sha':'f41492ffc7cb495c',
            'limitations':['no valid config means no trusted remote route','no valid Store means no unsafe DB creation/migration/audit writes','remote alert delivery is best effort','no real TWS/broker connection or order validation']}
    (out/'independent_review.json').write_text(json.dumps(review,indent=2),encoding='utf-8')
    original=out/'previous_batches.txt'
    if original.exists():
        text=original.read_text(encoding='utf-8')
        excerpt=text[:2600]+'\n... repeated setup errors omitted from this excerpt ...\n'+text[-400:]
        (out/'invalid_harness_excerpt.txt').write_text(excerpt,encoding='utf-8')
        meta={'raw_log_path':str(original),'raw_log_sha256':sha(original),'qualified_product_result':False,'reason':'missing pytest basetemp parent; 15 passed, 854 setup errors'}
        (out/'invalid_harness_receipt.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
    shutil.copyfile(Path('C:/tmp')/'v6lite_b28_closeout.py',out/'v6lite_b28_closeout.py')
    print('review notes and invalid-harness excerpt prepared')

elif sys.argv[1]=='final':
    assert code_sha()=='f41492ffc7cb495c'
    assert sha(root/'docs/V6_LITE_OPERATOR_MANUAL.md')=='9f81da63e8b5cb5a9c22110f0665cf8b8c8f9d39873e612ab32d955aa62199fa'
    runs=[json.loads((out/f'release_full_{i}.json').read_text()) for i in (1,2,3)]
    assert all(r['returncode']==0 and r['tests']==1978 and r['failures']==r['errors']==0 and r['skipped']==1 and r['source_unchanged'] and not r['incomplete'] for r in runs)
    assert len({r['inventory_sha256'] for r in runs})==1
    assert all(runs[i]['end_pt'] < runs[i+1]['start_pt'] for i in (0,1))
    subprocess.run([sys.executable,'-B','C:/tmp/v6lite_b28_artifacts.py','report'],cwd=root,check=True)
    selected=['freeze.json','static_investigation.json','card_samples.json','independent_review.json','development_attempts.json','legacy_alert_contract_migration.json','invalid_harness_excerpt.txt','invalid_harness_receipt.json']
    selected += [f'{label}.{suffix}' for label in ('focused_frozen','policy_focused','full_1_frozen','release_full_1','release_full_2','release_full_3') for suffix in ('json','txt','xml')]
    selected += [f'{label}_inventory.json' for label in ('focused_frozen','policy_focused','full_1_frozen','release_full_1','release_full_2','release_full_3')]
    selected += ['v6lite_b28_validate.py','v6lite_b28_progress.py','v6lite_b28_baselines.py','v6lite_b28_artifacts.py','v6lite_b28_migrate_alert_contracts.py','v6lite_b28_closeout.py']
    files=[root/'v6lite/__main__.py',root/'v6lite/engine.py',root/'v6lite/lifecycle.py',root/'v6lite/tests/test_batch28_lifecycle.py',root/'v6lite/tests/test_batch28_exit_edges.py',root/'v6lite/tests/test_engine.py',root/'v6lite/tests/test_g2_day1_selfreview.py',root/'docs/V6_LITE_FIX_REPORT_ROUND28_2026-10-20.md']
    files += [out/name for name in selected]
    files += sorted((out/'native_connection_lost').glob('*.json'))+sorted((out/'native_connection_lost').glob('*.xml'))+sorted((out/'native_connection_lost').glob('*.txt'))
    assert all(p.is_file() for p in files)
    manifest={str(p.relative_to(root)).replace('\\','/'):sha(p) for p in files}
    manifest_path=out/'sha256_manifest.json'
    manifest_path.write_text(json.dumps({'scope':'batch28a section1','code_sha':code_sha(),'package_sha256':json.loads((out/'freeze.json').read_text())['package_sha256'],'files':manifest},ensure_ascii=False,indent=2),encoding='utf-8')
    files.append(manifest_path)
    (Path('C:/tmp')/'v6lite_b28_stage_paths.json').write_text(json.dumps([str(p.relative_to(root)).replace('\\','/') for p in files]),encoding='utf-8')
    print(json.dumps({'files':len(files),'code_sha':code_sha(),'full_runs':[{'label':r['label'],'tests':r['tests'],'skipped':r['skipped'],'seconds':r['seconds']} for r in runs]},indent=2))
