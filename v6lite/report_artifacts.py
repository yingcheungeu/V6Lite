"""Complete report facts, streamed separately from the bounded main report.

The attachment is a conservative superset of safety audit events, not a claim
that the database captured every historical phone P0. Account masks are the
only change to persisted diagnostic text. Ledger facts use negative synthetic
IDs and an explicit source, separate from positive original audit IDs.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime,time,timedelta,timezone
from pathlib import Path
from v6lite.market_calendar import ET
from v6lite.telemetry import redact_accounts

# Main Events always keeps ORDER_PRICE_OBSERVATION summary-only. The
# attachment separately retains diagnostic observations, even without a TP copy.
OBSERVATION_EXCLUSION = """kind NOT IN ('ORDER_PRICE_OBSERVATION','OBSERVATION_SUMMARY')
 AND (kind!='PROTECT_STATE' OR EXISTS (
 SELECT 1 FROM json_each(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END)
 WHERE key NOT IN ('position_id','confirmed_at','updated_at')))"""

ATTACHMENT_AUDIT_FILTER = """kind!='OBSERVATION_SUMMARY'
 AND (kind!='ORDER_PRICE_OBSERVATION' OR NOT json_valid(payload)
   OR COALESCE(json_extract(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,'$.error_code'),0)!=0
   OR COALESCE(json_extract(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,'$.reason'),'')!='')
 AND (kind!='PROTECT_STATE' OR EXISTS (
 SELECT 1 FROM json_each(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END)
 WHERE key NOT IN ('position_id','confirmed_at','updated_at')))"""


def write_fact_attachment(store, cfg, day, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (day.isoformat() + '.critical.jsonl')
    temporary = path.with_suffix(path.suffix + '.tmp')
    start = datetime.combine(day,time(0),tzinfo=ET).astimezone(timezone.utc)
    end = datetime.combine(day+timedelta(days=1),time(0),tzinfo=ET).astimezone(timezone.utc)
    digest = hashlib.sha256()
    counts = {'count':0,'audit_rows':0,'ledger_rows':0}
    next_id = -1
    with temporary.open('wb') as target:
        def emit(record, source):
            record['payload'] = redact_accounts(record['payload'],cfg.account)
            encoded = (json.dumps(record,ensure_ascii=False,sort_keys=True)+'\n').encode('utf-8')
            target.write(encoded);digest.update(encoded)
            counts['count']+=1;counts['audit_rows' if source=='audit' else 'ledger_rows']+=1
        for row in store.conn.execute('SELECT id,ts,kind,payload FROM audit WHERE ts>=? AND ts<? AND '+
                ATTACHMENT_AUDIT_FILTER+' ORDER BY id',(start.isoformat(),end.isoformat())):
            emit({**dict(row),'source':'audit'},'audit')
        # Preserve facts from main sections too, including existing legacy
        # fills that may have no matching historical audit row.
        for row in store.conn.execute("SELECT f.*,c.commission,u.value AS execution_time_unverified,"
                "r.value AS execution_time_review FROM fills f "
                "LEFT JOIN commissions c ON c.exec_id=f.exec_id "
                "LEFT JOIN controls u ON u.key='execution_time_unverified:'||f.exec_id "
                "LEFT JOIN controls r ON r.key='execution_time_review:'||f.exec_id "
                "WHERE f.trading_day=? OR f.position_id IN (SELECT position_id FROM positions WHERE status='OPEN') "
                "ORDER BY f.time,f.exec_id",(day.isoformat(),)):
            emit({'id':next_id,'ts':row['time'],'kind':'LEDGER_FILL','source':'fills',
                  'payload':json.dumps(dict(row),ensure_ascii=False,sort_keys=True,default=str)},'ledger')
            next_id-=1
        for table,kind,stamp in (('positions','LEDGER_POSITION','opened_at'),
                                  ('protections','LEDGER_PROTECTION','updated_at')):
            for row in store.conn.execute('SELECT * FROM '+table+' WHERE position_id IN ('
                    "SELECT position_id FROM positions WHERE status='OPEN' UNION "
                    'SELECT position_id FROM fills WHERE trading_day=?) ORDER BY position_id',(day.isoformat(),)):
                emit({'id':next_id,'ts':row[stamp],'kind':kind,'source':table,
                      'payload':json.dumps(dict(row),ensure_ascii=False,sort_keys=True,default=str)},'ledger')
                next_id-=1
    temporary.replace(path)
    return {'filename':path.name,'sha256':digest.hexdigest(),**counts}
