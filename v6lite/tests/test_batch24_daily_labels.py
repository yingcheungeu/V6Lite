"""B24 phone daily ATTENTION aliases retain legacy machine inputs."""
from datetime import date

import pytest

from v6lite.alerts import P1_ORANGE
from v6lite.report import daily_health_card
from v6lite.tests.test_batch24_stp_echo import _no_network


@pytest.mark.parametrize('machine,visible', [
    ('Gateway disconnected', 'IBKR 断线'),
    ('Gateway clock skew', 'IBKR 时钟偏差'),
])
def test_B24_daily_ATTENTION_translates_legacy_machine_key_to_visible_IBKR(machine, visible):
    raw = '\n'.join([
        'ATTENTION: ' + machine,
        'Scans    10:00 ET started signals=2 entered=AAPL',
        'Signals  AAPL BUY 78',
        'Decisions no WOULD_BUY; gate n/a',
        'Orders   3 placed · 4 canceled',
        'Positions AAPL 3 · drift none · broker protection WORKING S=3',
        'Gateway  this process today, through report time: replies 7 · max verified 0.75s · misses 2',
        'Alerts   this process today, through report time: sent 9 · failed 0',
        'Quotes   RTH availability 5/6; LIVE subscribed symbol-samples',
        'code_sha synthetic-b24',
    ])
    card = daily_health_card(raw, date(2026, 10, 12))
    embed = card.as_embed()
    assert embed['title'].startswith('⚠️ 需关注') and embed['color'] == P1_ORANGE
    assert visible in embed['title']
    text = embed['title'] + '\n' + '\n'.join(field['name'] + ':' + field['value'] for field in embed['fields'])
    assert 'Gateway' not in text
    assert [field['name'] for field in embed['fields']] == [
        '扫描', '下单/撤单', '持仓与漂移', '券商端保护', 'IBKR 心跳', '告警送达', '行情可用率', 'code_sha']
    assert all(value in text for value in ('AAPL', '3', '4', '7', '0.75', '2', '9', '5/6', 'synthetic-b24'))
    assert raw.splitlines()[0] == 'ATTENTION: ' + machine
    assert raw.splitlines()[6].startswith('Gateway  '), 'machine record shape remains unchanged'
