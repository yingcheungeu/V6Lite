"""B26 actual visible-card truth: current broker proof is not a stored maximum.

All three cases use real Engine/FakeBroker/SQLite, genuine evidence snapshots
or a failed real broker query, and the actual AlertCard.as_embed renderer.
No frozen B26 spec, production function or private authority is modified.
"""
from __future__ import annotations

import re

import pytest

from v6lite.alerts import Alerter
from v6lite.broker.base import BrokerError
from v6lite.tests.test_batch26_constraint_policy import _accepted_constraints
from v6lite.tests.test_batch26_overnight_evidence import (
    _attempt_tp, _no_network, _restart, _tp_requests, _writes, seed_overnight,
)


def _visible(embed):
    return '\n'.join([embed['title'], embed.get('description', '')] +
                     [field['name'] + '：' + field['value'] for field in embed['fields']])


def _labelled_values(text, *, historical):
    if historical:
        label = r'本地(?:上次|最近)确认(?:的)?(?:工作(?:中)?)?'
        noun = r'(?:(?:STP(?:\s*保护)?|保护(?:\s*STP)?)(?:\s*S)?)?'
    else:
        label = r'当前(?:券商)?(?:工作(?:中)?)?'
        noun = r'(?:STP(?:\s*保护)?|保护(?:\s*STP)?)(?:\s*S)?'
    return re.findall(label + r'\s*' + noun + r'\s*[=:：]\s*(未知|\d+(?:\.\d+)?)\s*(?:股)?', text)


@pytest.mark.parametrize('scenario,current', [
    ('cancelled_gtc', '0'), ('stable_missing_entry', '3'), ('working_query_failed', '未知'),
])
def test_B26_card_current_working_protection_uses_fresh_broker_truth_and_labels_old_confirmation(
        seed_overnight, tmp_path, monkeypatch, scenario, current):
    rig, pid = seed_overnight()
    original = dict(rig.store.protection(pid)); ref = original['intent_id']
    assert original['confirmed_remaining'] == 3
    embeds = []
    real_send = Alerter.send

    def send(self, now, level, subject, body='', **kwargs):
        card = kwargs.get('card')
        if level == 'P1' and card is not None and '过夜' in card.title and '止盈' in card.title:
            embeds.append(card.as_embed())  # visible phone fields, not raw hidden body
        return real_send(self, now, level, subject, body, **kwargs)

    monkeypatch.setattr(Alerter, 'send', send)
    if scenario == 'cancelled_gtc':
        rig.broker.orders[ref].status = 'Cancelled'
        assert not [order for order in rig.broker.open_orders() if order.intent_id == ref]
    elif scenario == 'stable_missing_entry':
        rig.store.conn.execute('DELETE FROM fills WHERE exec_id=?', (rig._b26_entry_fill.exec_id,))
        [native] = [order for order in rig.broker.open_orders() if order.intent_id == ref]
        assert native.status == 'Submitted' and native.remaining == 3
    before = _writes(rig)
    _restart(rig, tmp_path, monkeypatch)
    failed_queries = []
    if scenario == 'working_query_failed':
        # First establish real today's healthy proof, then fail the next real
        # open-orders download. Yesterday's/local 3 and today's earlier 3 must
        # not be relabelled as current broker authority after this failure.
        assert embeds == []

        def unavailable():
            failed_queries.append(rig.clock.now())
            raise BrokerError('synthetic current working-order query unavailable')

        monkeypatch.setattr(rig.broker, 'open_orders', unavailable)
    _attempt_tp(rig, pid)
    assert len(embeds) == 1, 'the real failed/restricted path must create one visible explanation card'
    if scenario == 'working_query_failed':
        assert failed_queries
    text = _visible(embeds[0])
    assert _labelled_values(text, historical=False) == [current], text
    assert _labelled_values(text, historical=True) == ['3'], text
    _accepted_constraints(text)
    assert 'AAPL' in text and re.search(r'orderId\s*[=:：]\s*' + str(original['broker_order_id']) + r'\b', text)
    assert re.search(r'permId\s*[=:：]\s*' + str(original['perm_id']) + r'\b', text)
    assert '账本' in text and re.search(r'3\s*股', text)
    if current in {'0', '未知'}:
        impacts = '\n'.join(field['value'] for field in embeds[0]['fields'] if field['name'] == '影响')
        assert '保留现有券商止损' not in impacts, 'absent/unverified work protection is not a currently retained stop'
        assert not re.search(r'(?:仍有保护|已保护|已获保护)\s*[=:：]?\s*3\s*股', text)
        assert '；STP S=3 股；' not in text, 'an unlabelled stored S must not masquerade as current work protection'
    assert _tp_requests(rig) == [] and _writes(rig) == before
    assert rig.store.protection(pid)['confirmed_remaining'] == 3, 'do not rewrite historical reserve for presentation'
    assert rig.store.protection(pid)['intent_id'] == ref
    assert rig.store.position(pid)['qty_open'] == 3
    assert len(rig.broker.sent_protections) == 1
