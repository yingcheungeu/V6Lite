"""Offline serial pytest progress; no access to engine/broker state."""
from pathlib import Path
import json,os,time
completed=0
def write(nodeid):
 Path(os.environ['V6LITE_B33_PROGRESS']).write_text(json.dumps({'nodeid':nodeid,'completed':completed,'updated_unix':time.time()}),encoding='utf-8')
def pytest_runtest_logstart(nodeid,location):write(nodeid)
def pytest_runtest_logfinish(nodeid,location):
 global completed
 completed+=1;write(nodeid)
