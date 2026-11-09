import json,os,time
from pathlib import Path

def pytest_runtest_logstart(nodeid, location):
    Path(os.environ['V6LITE_B28_PROGRESS']).write_text(json.dumps({'nodeid':nodeid,'started_unix':time.time()}),encoding='utf-8')
