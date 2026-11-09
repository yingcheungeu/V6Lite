from pathlib import Path
from datetime import timedelta
from tempfile import TemporaryDirectory
import json,sys
root=Path('C:/Dev/V6Lite-dev');out=root/'docs/batch28_evidence'
sys.path.insert(0,str(root))
from v6lite.tests.helpers import et
from v6lite.tests.test_batch28_section2 import DAY,_reporting,_finish_report,_market_lines
with TemporaryDirectory(prefix='v6lite-b28-section2-samples-',dir='C:/tmp') as directory:
    start=et(15,9,57,day=DAY)
    with _reporting(Path(directory),start=start) as rig:
        rig.store.audit(et(9,32,day=DAY),'STARTUP',code_sha='synthetic-previous-process')
        rig.engine._heartbeat(start)
        rig.at(start+timedelta(minutes=1));rig.engine._heartbeat(rig.clock.now())
        pair=_finish_report(rig)
        lines=_market_lines(pair)
        data={'synthetic_only':True,'local_market_data_line':lines[0],
              'phone_market_data_line':lines[1],'daily_health_phone_body':pair[1],
              'sample_scope':'current process RTH since first counted sample, not all-day coverage',
              'unchanged_funds_log_sample':'DEBUG [ACCOUNT_STREAM] silent since <last actual AvailableFunds callback>; funds snapshot requested',
              'stale_or_unverified_log_sample':'WARNING [ACCOUNT_STREAM] silent since <last actual callback>; funds snapshot requested; funds/readiness evidence requires verification'}
        (out/'report_samples.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
print('synthetic local/phone report samples written; no external connection')
