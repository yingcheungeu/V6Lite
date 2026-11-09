"""F6: release needs account-wide, read-only executions across TWS clients."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from v6lite.broker.base import BrokerError
from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import ProtectionConfig
from v6lite.models import IntentStatus, Side
from v6lite.tests.helpers import make_cfg
from v6lite.tests.test_batch16_f6_release import (
    _protected_position_flattened_in_tws, _request_release, _release_refusals,
)


def _account_adapter(cfg, request_executions, monkeypatch):
    # Use the actual adapter constructor so query-generation, pacing and
    # fail-closed request state are initialized. Only the local native IB
    # object is replaced; there is no socket, connection or weak IB destructor.
    native = SimpleNamespace(RaiseRequestErrors=False,
        isConnected=lambda: True, wrapper=SimpleNamespace(fills={}))
    def complete_request(flt):
        assert native.RaiseRequestErrors is True, 'account evidence must use strict native request errors'
        return request_executions(flt)
    native.reqExecutions = complete_request
    monkeypatch.setattr('ib_insync.IB', lambda: native)
    broker = IbkrBroker(cfg, read_only=True)
    assert broker.ib is native and broker.read_only
    return broker


def test_B16_F6_account_execution_request_filters_account_not_client(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, protection=ProtectionConfig())
    calls = []
    foreign_client_fill = SimpleNamespace(
        execution=SimpleNamespace(
            acctNumber=cfg.account, clientId=909, execId="0000beef.00000001.01.01",
            shares=2, orderRef="manual-other-TWS-user", side="SLD", price=100.0,
            time=datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc), orderId=991,
            permId=88991,
        ),
        contract=SimpleNamespace(symbol="AAPL", conId=265598, secType="STK", currency="USD"),
        commissionReport=SimpleNamespace(execId="", commission=0.0),
    )

    def request(flt):
        calls.append(flt)
        return [foreign_client_fill]

    broker = _account_adapter(cfg, request, monkeypatch)
    events = broker.account_executions_today()
    assert len(calls) == 1
    assert calls[0].acctCode == cfg.account
    assert not calls[0].clientId, "clientId must not exclude another TWS user's SELL"
    assert len(events) == 1
    assert events[0].symbol == "AAPL" and events[0].side is Side.SELL
    assert events[0].intent_id == ""  # foreign order is evidence, not Lite ownership
    assert broker._events == []


def test_B16_F6_release_uses_account_sell_proof_without_booking_foreign_fill(
        tmp_path, monkeypatch):
    rig, position_id, ref, manual_exec_id = _protected_position_flattened_in_tws(tmp_path)
    rig.broker.confirm_cancel(ref)
    rig.engine._drain(rig.clock.now())
    assert rig.store.intent(ref)["status"] == IntentStatus.CANCEL_SEEN
    broker_execs = rig.broker.executions_today

    def own_client_only():
        return [fill for fill in broker_execs() if fill.exec_id != manual_exec_id]

    monkeypatch.setattr(rig.broker, "executions_today", own_client_only)
    assert all(fill.exec_id != manual_exec_id for fill in rig.broker.executions_today())
    account_execs = rig.broker.account_executions_today
    account_reads = []

    def account_read():
        rows = account_execs()
        account_reads.append(tuple(fill.exec_id for fill in rows))
        return rows

    monkeypatch.setattr(rig.broker, "account_executions_today", account_read)
    fills_before = rig.store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    _request_release(rig)
    assert len(account_reads) >= 2 and all(manual_exec_id in ids for ids in account_reads)
    assert rig.store.position(position_id)["status"] == "RELEASED"
    assert rig.store.intent(ref)["status"] == IntentStatus.CANCELLED
    assert rig.store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == fills_before
    assert rig.store.conn.execute(
        "SELECT 1 FROM fills WHERE exec_id=?", (manual_exec_id,)
    ).fetchone() is None


def test_B16_F6_account_evidence_does_not_claim_other_client_spoofed_v6l_ref(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, protection=ProtectionConfig())
    foreign = SimpleNamespace(
        execution=SimpleNamespace(
            acctNumber=cfg.account, clientId=909, execId="0000beef.00000002.01.01",
            shares=1, orderRef="v6l-spoofed-other-client", side="SLD", price=100.0,
            time=datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc), orderId=992,
            permId=88992,
        ),
        contract=SimpleNamespace(symbol="AAPL", conId=265598, secType="STK", currency="USD"),
        commissionReport=SimpleNamespace(execId="", commission=0.0),
    )
    broker = _account_adapter(cfg, lambda flt: [foreign], monkeypatch)
    evidence = broker.account_executions_today()
    assert len(evidence) == 1 and evidence[0].side is Side.SELL
    assert evidence[0].intent_id == "", "orderRef prefix cannot claim another client's trade"
    assert broker._events == []


def test_B16_F6_account_evidence_ignores_other_account_sell(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, protection=ProtectionConfig())
    foreign_account = SimpleNamespace(
        execution=SimpleNamespace(
            acctNumber="DU_OTHER_ACCOUNT", clientId=909,
            execId="0000beef.00000003.01.01", shares=20, orderRef="manual-other-account",
            side="SLD", price=100.0,
            time=datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc), orderId=993,
            permId=88993,
        ),
        contract=SimpleNamespace(symbol="AAPL", conId=265598, secType="STK", currency="USD"),
        commissionReport=SimpleNamespace(execId="", commission=0.0),
    )
    broker = _account_adapter(cfg, lambda flt: [foreign_account], monkeypatch)
    try:
        evidence = broker.account_executions_today()
    except BrokerError:
        # Refusing a mixed-account answer is also a safe, fail-closed result.
        evidence = []
    assert evidence == []
    assert broker._events == []


@pytest.mark.parametrize("failure", [BrokerError, RuntimeError],
                         ids=["BrokerError", "RuntimeError"])
def test_B16_F6_account_execution_timeout_refuses_release_without_raising(
        tmp_path, monkeypatch, failure):
    rig, position_id, ref, _ = _protected_position_flattened_in_tws(tmp_path)
    rig.broker.confirm_cancel(ref)
    rig.engine._drain(rig.clock.now())
    assert rig.store.intent(ref)["status"] == IntentStatus.CANCEL_SEEN
    cancels_before = tuple(rig.broker.cancels)
    sent_before = tuple(req.intent_id for req in rig.broker.sent)

    def timed_out():
        raise failure("synthetic account execution timeout")

    monkeypatch.setattr(rig.broker, "account_executions_today", timed_out)
    _request_release(rig)  # operator request must be consumed as a refusal, never crash tick
    assert rig.store.position(position_id)["status"] == "OPEN"
    assert rig.store.intent(ref)["status"] == IntentStatus.CANCEL_SEEN
    assert rig.store.control("frozen:AAPL") is not None
    assert _release_refusals(rig)
    assert "P1|release refused AAPL" in rig.alerter._recent
    assert any("人工释放被拒" in title and "AAPL" in body and
               "发生了什么" in body and "影响" in body and "你要做什么" in body
               for title, body in rig.channel.messages)
    assert tuple(rig.broker.cancels) == cancels_before
    assert tuple(req.intent_id for req in rig.broker.sent) == sent_before
