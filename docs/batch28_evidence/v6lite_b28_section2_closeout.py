from pathlib import Path
import hashlib,json,re,shutil,sys,xml.etree.ElementTree as ET
root=Path('C:/Dev/V6Lite-dev');out=root/'docs/batch28_evidence'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
freeze=json.loads((out/'freeze.json').read_text())
if sys.argv[1]=='review':
    review={'scope':'batch28 section2 on accepted batch28a','production_review':'passed; no blocking findings',
            'resolved_findings':['detect same-day first restart after previous-day continuous process using prior runtime audit','flush due observation summaries after protection/exit work and defer during urgent continuation'],
            'preserved_boundaries':['AccountReady and genuine funds timestamps','270-second funds entry gate','summary reqId/account/currency/End/sequence rules','actual reconnect subscription restoration','three-source takeover','critical-first event selection and exact UTF8 budgets','transactional candidate RAM publication after COMMIT'],
            'historical_contract_review':'B10/G2 day1/G2 selfreview/review round2 migrations passed read-only review; rate limits, stale timestamps, no-order writes, and genuine simulated reply timing preserved; independent adapter resubscription tests unchanged',
            'reviewer_actions':'read-only; no tests or broker access; root remains sole production implementer'}
    (out/'independent_review.json').write_text(json.dumps(review,indent=2),encoding='utf-8')
elif sys.argv[1]=='final':
    runs=[json.loads((out/f'final_full_{i}.json').read_text()) for i in (1,2,3)]
    assert all(r['returncode']==r['failures']==r['errors']==0 and r['source_unchanged'] and not r['incomplete'] for r in runs)
    assert all(r['tests']==2003 and r['skipped']==1 for r in runs)
    assert len({r['inventory_sha256'] for r in runs})==1
    assert all(runs[i]['end_pt']<runs[i+1]['start_pt'] for i in (0,1))
    for name,digest in freeze['owned_files'].items():assert sha(root/name)==digest,name
    assert sha(root/'docs/V6_LITE_BATCH28_INSTRUCTIONS.md')==freeze['instructions_sha256']
    assert sha(root/'docs/V6_LITE_FIX_REPORT_ROUND28_2026-10-20.md')==freeze['accepted_28a_report_sha256']
    notes=json.loads((out/'development_attempts.json').read_text())
    focused=json.loads((out/'final_focused.json').read_text())
    initial=json.loads((out/'release_full_1.json').read_text())
    notes['final_focused']={'classification':'final frozen focused candidate','result':str(focused['tests'])+' passed'}
    notes['release_full_1']['result']=str(initial['tests']-initial['failures']-initial['skipped'])+' passed, '+str(initial['failures'])+' failed, '+str(initial['skipped'])+' skipped'
    (out/'development_attempts.json').write_text(json.dumps(notes,ensure_ascii=False,indent=2),encoding='utf-8')
    # Preserve original raw hashes and every subsequent masking step.
    sys.path.insert(0,str(root))
    from v6lite.telemetry import redact_accounts
    receipt_path=out/'redaction_receipt.json'
    redacted=json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else {}
    for p in sorted(out.iterdir()):
        if p.suffix in ('.txt','.xml','.json') and p.name not in ('SHA256SUMS.json','closeout.json','redaction_receipt.json'):
            raw=p.read_bytes();text=raw.decode('utf-8');masked=redact_accounts(text)
            if masked!=text:
                p.write_bytes(masked.encode('utf-8'))
                previous=redacted.get(p.name,{'raw_sha256':hashlib.sha256(raw).hexdigest(),'synthetic_fixture_only':True})
                previous.setdefault('additional_steps',[]).append({'input_sha256':hashlib.sha256(raw).hexdigest(),'output_sha256':sha(p),'rule':'production redact_accounts: DFA/DU/DF/U/F/I'})
                previous['redacted_sha256']=sha(p)
                redacted[p.name]=previous
    (out/'redaction_receipt.json').write_text(json.dumps(redacted,indent=2),encoding='utf-8')
    # All source files and selected complete receipts are included; transient
    # current-test/progress snapshots are deliberately omitted from the tag.
    selected=[p for p in sorted(out.iterdir()) if p.is_file() and '_current_test' not in p.name and '_progress.json' not in p.name and p.name not in ('SHA256SUMS.json','closeout.json')]
    report=root/'docs/V6_LITE_FIX_REPORT_ROUND28_SECTION2_2026-10-20.md'
    files=[root/name for name in freeze['owned_files']]+[report]+selected
    manifest={'code_sha':freeze['code_sha'],'package_sha256':freeze['package_sha256'],
              'files':{p.relative_to(root).as_posix():sha(p) for p in files}}
    (out/'SHA256SUMS.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    closing={'scope':'batch28 section2 plus inherited accepted batch28a','baseline_commit':freeze['baseline_commit'],'intended_tag':'v6lite-batch28',
             'code_sha':freeze['code_sha'],'package_sha256':freeze['package_sha256'],'consecutive_full_runs':3,
             'same_inventory':True,'serial_runs':True,'source_unchanged':True,
             'results':[{'label':r['label'],'tests':r['tests'],'failed':r['failures'],'errors':r['errors'],'skipped':r['skipped'],'start_pt':r['start_pt'],'end_pt':r['end_pt']} for r in runs],
             'operator_manual_staged':False,'accepted_28a_report_preserved':True,
             'manifest_sha256':sha(out/'SHA256SUMS.json'),'merge_or_push_or_deployment':False,
             'post_commit_check':'Verify local annotated tag archive against package_sha256; commit identity is supplied by git, not embedded recursively in this file.'}
    (out/'closeout.json').write_text(json.dumps(closing,indent=2),encoding='utf-8')
    files+=[out/'SHA256SUMS.json',out/'closeout.json']
    Path('C:/tmp/v6lite_b28_section2_stage_paths.json').write_text(json.dumps([p.relative_to(root).as_posix() for p in files]),encoding='utf-8')
    print(json.dumps({'owned_files':len(files),'report_sha256':sha(report),'manifest_sha256':sha(out/'SHA256SUMS.json'),'code_sha':freeze['code_sha']}))
