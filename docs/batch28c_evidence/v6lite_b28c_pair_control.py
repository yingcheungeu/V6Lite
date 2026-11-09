from pathlib import Path
import subprocess,sys
ROOT=Path('C:/Dev/V6Lite-dev');p=ROOT/'v6lite/protection_confirmations.py'
original=p.read_bytes();text=original.decode('utf-8').replace('\r\n','\n')
start=text.index('        # Collect a genuine pair')
end=text.index('\n    def _protection_reduce_ack',start)
text=text[:start]+'''        status = token.get('ack_status')
        at = status.observed_at if status is not None else token['requested_at']
        return timedelta(0) <= now-at < timedelta(seconds=.25)
''' +text[end:]
try:
    p.write_bytes(text.replace('\n','\r\n').encode('utf-8') if b'\r\n' in original else text.encode('utf-8'))
    rc=subprocess.call([sys.executable,'-B','docs/batch28c_evidence/v6lite_b28c_validate.py',
        'red_pair_window_calibrated','v6lite/tests/test_batch28c_pair_window.py'],cwd=ROOT)
finally:
    p.write_bytes(original)
raise SystemExit(rc)
