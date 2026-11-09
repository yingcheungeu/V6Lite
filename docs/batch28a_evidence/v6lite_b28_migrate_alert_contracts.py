import ast, hashlib, json
from pathlib import Path
root=Path('C:/Dev/V6Lite-dev')
out=root/'docs/batch28a_evidence'
changes=[('v6lite/tests/test_engine.py','test_overnight_gateway_restart_is_logged_not_paged',
'''def test_overnight_unexpected_gateway_disconnect_is_paged_once(tmp_path):
    rig = Rig(tmp_path, start=et(20, 0))
    rig.engine.startup()
    rig.tick()
    before = len(rig.channel.messages)
    rig.broker.drop_connection()
    rig.advance(seconds=1)
    rig.tick()
    assert len(rig.channel.messages) == before + 1
    title, _ = rig.channel.messages[-1]
    assert "P1" in title and "IBKR 连接断开" in title
'''),('v6lite/tests/test_g2_day1_selfreview.py','test_R7_2_engine_pre_rth_disconnect_mutes_rth_disconnect_page',
'''def test_R7_2_engine_pre_rth_and_recurring_rth_disconnect_are_both_paged(tmp_path):
    rig = Rig(tmp_path, start=et(9, 15))
    rig.start()
    rig.broker.drop_connection()                     # B28: unexpected pre-RTH loss also pages
    rig.tick()
    count = lambda: sum("P1" in title and "IBKR 连接断开" in title
                        for title, _ in rig.channel.messages)
    assert count() == 1
    rig.advance(seconds=31)
    rig.tick()                                       # reconnects
    assert rig.engine.flags.broker_connected
    rig.at(et(9, 35))                                # new RTH loss must page again
    rig.broker.drop_connection()
    rig.tick()
    assert count() == 2, "new RTH disconnect must not be muted by the pre-RTH page"
''')]
records=[]
for filename,name,newfunc in changes:
    p=root/filename
    old=p.read_bytes()
    source=old.decode('utf-8')
    node=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.FunctionDef) and n.name==name)
    lines=old.splitlines(keepends=True)
    prefix=b''.join(lines[:node.lineno-1]);suffix=b''.join(lines[node.end_lineno:])
    oldfunc=b''.join(lines[node.lineno-1:node.end_lineno])
    newline='\r\n' if b'\r\n' in oldfunc else '\n'
    changed=prefix+newfunc.replace('\n',newline).encode()+suffix
    p.write_bytes(changed)
    assert changed.startswith(prefix) and changed.endswith(suffix)
    records.append({'path':filename,'old_name':name,'before_sha256':hashlib.sha256(old).hexdigest(),'after_sha256':hashlib.sha256(changed).hexdigest(),'outside_function_unchanged':True,'old_function':oldfunc.decode(),'new_function':newfunc,'authority':'batch28 section1: all unexpected disconnects emit P1; prior pre-RTH/night log-only policy superseded'})
(out/'legacy_alert_contract_migration.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
print('migrated exactly two superseded off-hours alert assertions; all other bytes preserved')
