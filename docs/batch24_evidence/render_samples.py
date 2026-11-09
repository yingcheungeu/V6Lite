"""Generate B24 card evidence through Rig/FakeBroker and the real Alerter.

Run only after the candidate is frozen, using the development virtualenv.
All state is disposable under this batch's evidence directory. No runtime
config or user state is loaded; all IB/socket connects are explicitly forbidden.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent


def source_sha():
    digest = hashlib.sha256()
    package = ROOT / 'v6lite'
    for path in sorted((p for p in package.rglob('*')
                        if p.is_file() and '__pycache__' not in p.relative_to(package).parts),
                       key=lambda p: p.relative_to(package).as_posix()):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()[:16]


def forbidden(*args, **kwargs):
    raise AssertionError('B24 rendering evidence must never connect to IB or a network')


async def forbidden_async(*args, **kwargs):
    forbidden(*args, **kwargs)


def capture(name, channel, expected):
    cards = [card for card in channel.accepted if expected in card['title']]
    assert cards, (name, 'real Alerter did not emit expected card', channel.accepted)
    titles = {card['title'] for card in cards}
    return {'scenario': name, 'cards': cards,
            'plain_messages': [{'title': title, 'body': body}
                               for title, body in channel.messages if title in titles]}


def query_retry_sample(tmp):
    from v6lite.tests.test_batch22_item9_broker_cancel import _card_channel
    from v6lite.tests.test_batch23_tp_evidence import _working, _writes
    rig, pid = _working(tmp)
    try:
        channel = _card_channel(rig)
        before = _writes(rig)
        rig.broker.fail_open_orders = True
        # The production progress routine consumes a real FakeBroker timeout
        # and generates its own retry/P1 card through the unchanged Alerter.
        rig.engine._protection_progress_one(rig.clock.now(), rig.store.protection(pid),
                                            rig.store.position(pid))
        assert _writes(rig) == before
        return capture('P1 broker query retry pacing', channel, '券商查询已限速')
    finally:
        rig.store.close()


def price_drift_sample(tmp):
    from v6lite.tests.test_batch22_item9_broker_cancel import _card_channel
    from v6lite.tests.test_batch23_tp_evidence import (
        _activate_tp, _deliver_status, _native_status, _working, _writes,
    )
    rig, pid = _working(tmp)
    try:
        request = _activate_tp(rig, pid)
        channel = _card_channel(rig)
        before = _writes(rig)
        adapter, trade = _native_status(rig, request.intent_id, cap=119.50999999999999)
        _deliver_status(rig, adapter, trade)
        result = capture('P1 working price with cap 119.51', channel, '券商工作价格不一致')
        text = json.dumps(result['cards'], ensure_ascii=False)
        assert 'cap=119.51' in text and '119.50999999999999' not in text
        assert _writes(rig) == before
        return result
    finally:
        rig.store.close()


def rejection_sample(tmp):
    from v6lite.tests.test_batch22_item9_broker_cancel import _card_channel
    from v6lite.tests.test_batch23_tp_evidence import _activate_tp, _terminal_tp, _working, _writes
    rig, pid = _working(tmp, qty=6)
    try:
        request = _activate_tp(rig, pid)
        rig.broker.orders[request.intent_id].status = 'Inactive'
        held, own, others, complete = rig.engine._protection_evidence(rig.store.protection(pid))
        assert held == 6 and complete and len(own) == 1 and own[0].remaining == 4 and not others
        channel = _card_channel(rig)
        before = _writes(rig)
        _terminal_tp(rig, request, reason='Order price is outside the allowed price range; B24 rendering synthetic reason')
        result = capture('P0 TP price rejection: P=6 S=4 unprotected=2', channel, '止盈价格被券商拒绝')
        assert _writes(rig) == before
        result['completed_evidence'] = {'P': held, 'S': int(own[0].remaining), 'unprotected': 2,
                                        'open_orders_positions_executions_complete': complete}
        return result
    finally:
        rig.store.close()


def tws_samples(tmp):
    from datetime import timedelta
    from v6lite.config import ProtectionConfig
    from v6lite.tests.helpers import Rig, et
    from v6lite.tests.test_batch17_heartbeat_account import _TimeReplies
    from v6lite.tests.test_batch22_item9_broker_cancel import _card_channel
    rig = Rig(tmp, start=et(9), port=7497, protection=ProtectionConfig(enabled=True))
    try:
        replies = _TimeReplies(rig)
        channel = _card_channel(rig)
        rig.start()
        startup = capture('TWS 7497 startup connected', channel, '已启动')
        channel.accepted.clear()
        channel.messages.clear()
        rig.advance(seconds=31)
        rig.engine._gateway_heartbeat(rig.clock.now())
        rig.advance(seconds=29)
        rig.engine._gateway_heartbeat(rig.clock.now())
        rig.advance(seconds=31)
        rig.engine._gateway_heartbeat(rig.clock.now())
        assert rig.engine.gateway_heartbeat_stats()['not_responding']
        outage = capture('TWS 7497 heartbeat missing', channel, '心跳无应答')
        rig.at(replies.requests[-1] + timedelta(seconds=60))
        rig.engine._gateway_heartbeat(rig.clock.now())
        replies.answer()
        rig.engine._gateway_heartbeat(rig.clock.now())
        assert not rig.engine.gateway_heartbeat_stats()['not_responding']
        recovery = capture('TWS 7497 heartbeat recovered', channel, '已恢复应答')
        for result in (startup, outage, recovery):
            text = json.dumps(result['cards'], ensure_ascii=False)
            assert 'Gateway' not in text and ('IBKR' in text or 'TWS' in text)
        assert not rig.broker.sent and not rig.broker.sent_protections
        return [startup, outage, recovery]
    finally:
        rig.store.close()


def daily_sample(tmp):
    from v6lite.config import ProtectionConfig
    from v6lite.tests.helpers import Rig, et
    from v6lite.tests.test_batch22_item9_broker_cancel import _card_channel
    rig = Rig(tmp, start=et(9, 50), port=7497, dry_run=True,
              protection=ProtectionConfig(enabled=True))
    try:
        channel = _card_channel(rig)
        rig.start()
        rig.at(et(10, 0, 30))
        rig.tick()
        rig.at(et(16, 6))
        rig.tick()
        result = capture('TWS 7497 daily health: IBKR heartbeat label', channel, '每日健康报告')
        text = json.dumps(result['cards'], ensure_ascii=False)
        assert 'IBKR 心跳' in text and 'Gateway' not in text, text
        assert '演练（不下单）' in text, text
        assert not rig.broker.sent and not rig.broker.sent_protections
        return result
    finally:
        rig.store.close()


def main():
    if sys.flags.optimize:
        raise RuntimeError('Rendering evidence requires optimization disabled')
    assert ROOT == Path('C:/Dev/V6Lite-dev')
    # Clear process-only inherited trading/remote-alert settings before loading
    # fixtures. Never print values or read any .env/operator configuration.
    for key in tuple(os.environ):
        if key.startswith('V6LITE_') or key in {'DISCORD_BOT_TOKEN', 'DISCORD_WEBHOOK', 'IBKR_PASSWORD'}:
            del os.environ[key]
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    import v6lite
    assert Path(v6lite.__file__).resolve().parent == ROOT / 'v6lite', v6lite.__file__
    # Initialize eventkit's local socketpair before prohibiting network calls.
    policy = asyncio.get_event_loop_policy()
    try:
        loop = policy.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        policy.set_event_loop(loop)
    if loop.is_closed():
        policy.set_event_loop(asyncio.new_event_loop())
    from ib_insync import IB
    import socket
    before = source_sha()
    base = EVIDENCE / 'test_tmp'
    base.mkdir(exist_ok=True)
    assert base.resolve().is_relative_to(EVIDENCE.resolve()), 'test_tmp escapes batch evidence directory'
    with tempfile.TemporaryDirectory(prefix='render_samples_', dir=base) as temporary:
        tmp = Path(temporary)
        assert tmp.resolve().is_relative_to(base.resolve()), 'temporary state escapes checked directory'
        with patch.object(socket.socket, 'connect', forbidden), \
             patch.object(socket.socket, 'connect_ex', forbidden), \
             patch.object(IB, 'connect', forbidden), \
             patch.object(IB, 'connectAsync', forbidden_async):
            samples = [query_retry_sample(tmp / 'query'), price_drift_sample(tmp / 'cap'),
                       rejection_sample(tmp / 'rejection'), *tws_samples(tmp / 'tws'),
                       daily_sample(tmp / 'daily')]
    assert source_sha() == before, 'candidate changed during render evidence generation'
    record = {'source_code_sha': before, 'source_unchanged': True, 'network_connections_allowed': False,
              'render_origin': 'real Engine -> Alerter -> AlertCard.as_embed/as_plain_text -> local memory channel',
              'samples': samples}
    (EVIDENCE / 'render_samples.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n',
                                                encoding='utf-8', newline='\n')
    markdown = ['# Batch 24 actual Alerter card samples', '', 'Source SHA: ' + before, '']
    for sample in samples:
        markdown.extend(['## ' + sample['scenario'], ''])
        for message in sample['plain_messages']:
            markdown.extend([message['body'], ''])
    (EVIDENCE / 'render_samples.md').write_text('\n'.join(markdown), encoding='utf-8', newline='\n')
    print(json.dumps({'source_code_sha': before, 'source_unchanged': True, 'scenarios': len(samples),
                      'cards': sum(len(sample['cards']) for sample in samples)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
