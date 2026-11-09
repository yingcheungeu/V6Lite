"""Read-only test progress, kept outside the frozen source and delivery files."""
from pathlib import Path
import json,os,time

def pytest_runtest_logstart(nodeid, location):
    Path(os.environ['V6LITE_B28D_PROGRESS']).write_text(
        json.dumps({'nodeid':nodeid,'started_unix':time.time()}),encoding='utf-8')
