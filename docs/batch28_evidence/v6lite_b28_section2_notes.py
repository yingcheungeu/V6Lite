from pathlib import Path
import hashlib,json,subprocess,ast
from datetime import datetime
from zoneinfo import ZoneInfo
root=Path('C:/Dev/V6Lite-dev');out=root/'docs/batch28_evidence'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
package=root/'v6lite';digest=hashlib.sha256()
for p in sorted((p for p in package.rglob('*') if p.is_file() and '__pycache__' not in p.relative_to(package).parts),key=lambda p:p.relative_to(package).as_posix()):
    digest.update(p.relative_to(package).as_posix().encode());digest.update(b'\0');digest.update(p.read_bytes());digest.update(b'\0')
owned=['v6lite/engine.py','v6lite/report.py','v6lite/observations.py','v6lite/store.py','v6lite/tests/test_batch10.py',
       'v6lite/tests/test_g2_day1.py','v6lite/tests/test_g2_day1_selfreview.py','v6lite/tests/test_review_round2.py',
       'v6lite/tests/test_batch17_heartbeat_account.py','v6lite/tests/test_batch19_funds.py',
       'v6lite/tests/test_batch28_section2.py','v6lite/tests/test_batch28_section2_review_edges.py']
record={'baseline_tag':'v6lite-batch28a','baseline_commit':subprocess.check_output(['git','rev-parse','v6lite-batch28a^{commit}'],cwd=root,text=True).strip(),
        'code_sha':digest.hexdigest()[:16],'package_sha256':digest.hexdigest(),'owned_files':{p:sha(root/p) for p in owned},
        'instructions_sha256':sha(root/'docs/V6_LITE_BATCH28_INSTRUCTIONS.md'),
        'accepted_28a_report_sha256':sha(root/'docs/V6_LITE_FIX_REPORT_ROUND28_2026-10-20.md'),
        'operator_manual_observed_sha256':sha(root/'docs/V6_LITE_OPERATOR_MANUAL.md'),
        'operator_manual_owned':False,'compiled_sources':155,'source_compile':'all v6lite/scripts .py compiled from bytes without writing bytecode',
        'recorded_pt':datetime.now(ZoneInfo('America/Los_Angeles')).isoformat()}
(out/'freeze.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
notes={
 'focused_legacy_initial':{'classification':'harness invocation error','result':'0 tests; nonexistent test_batch27.py; corrected file list; not a product failure'},
 'focused_legacy_initial_actual':{'classification':'superseded contracts','result':'9 failed, 49 passed','reason':'B17 required held/invalid-flat silence to WARNING and resubscribe; user now asks to treat unchanged field silence as normal. Position/fill/order/freshness assertions retained.'},
 'focused_legacy_migrated':{'classification':'superseded contracts','result':'7 failed, 106 passed','reason':'B19 explicitly required classic resubscription for 7 non-flat/unknown cases. Migrated only request selection/count, preserving stale timestamps.'},
 'baseline_28a':{'classification':'intentional frozen-spec natural red','result':'15 failed, 2 passed, 2 candidate controls excluded','spec_sha256':'9c79d611507e78d6a3d7dfb51987be97312cc2ae95c0961f7bc734ddb5ea6779'},
 'new_spec_candidate':{'classification':'passed candidate','result':'19 passed'},
 'focused_final':{'classification':'passed before two review edge fixes','result':'132 passed'},
 'focused_review_edges':{'classification':'final frozen focused candidate','result':'97 passed'},
 'release_full_1':{'classification':'complete superseded candidate; not part of three consecutive passes','reason':'B10, G2 day1, G2 selfreview and review-round2 tests still required classic resubscription. Migrated request/count/log assertions and request stub only; no-write, rate-limit, genuine-reply timestamp and stale timestamp assertions retained.'},
 'flaky_gate':{'occurrences':0,'waivers':0,'reason':'All development failures explained by deliberate baseline reds, invalid path or superseded contracts; no intermittent test failure observed.'}}
(out/'development_attempts.json').write_text(json.dumps(notes,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'code_sha':record['code_sha'],'package_sha256':record['package_sha256']}))
