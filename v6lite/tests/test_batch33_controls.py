"""B33 claim/TTL/dedup integration, through real CLI and native offline proof."""
from dataclasses import replace
from datetime import timedelta
import json
import pytest

from v6lite import __main__ as cli
from v6lite.config import TestConfig as PaperTest
from v6lite.models import Purpose
from v6lite.tests.helpers import Rig, et
from v6lite.tests import test_batch28b_overnight_refresh as prep
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _audits, _tick_until
from v6lite.tests.test_batch26_overnight_evidence import _no_network, _restart, _safety, _tp_requests, _writes
from v6lite.tests.test_batch28c_price_confirmation import NativePriceWire
from v6lite.tests.test_batch16 import _test_tp_timeout_config
from v6lite.tests.test_batch33_open_delay import OPEN, DAY, evaluate, start_tp


@pytest.fixture
def paper_opening(overnight_run, tmp_path, monkeypatch):
    def test_rig(directory, **kwargs):
        kwargs.update(client_id=132, test=PaperTest(enabled=True), state_dir=str(tmp_path/'paper-test'))
        return Rig(directory, **kwargs)
    monkeypatch.setattr(prep, 'Rig', test_rig)
    rig, pid, harness = overnight_run(entry_day=(2026,10,28), tp_day=DAY,
        entry_price=344.40, stop=340.58, tp1=345.68, tp2=346.63, tp3=347.58, client_id=132)
    wire = NativePriceWire(rig, pid, harness, monkeypatch)
    rig.at(OPEN - timedelta(seconds=5)); rig.tick()
    path, _ = _test_tp_timeout_config(tmp_path)
    monkeypatch.setattr(cli, '_now', rig.clock.now)
    return rig, pid, wire, path


def request(path):
    return cli.main(['test-tp-timeout','AAPL','--config',str(path),'--operator','Test Operator'])


def test_B33_timeout_request_waits_then_claims_once(paper_opening):
    rig, pid, wire, path = paper_opening
    rig.at(OPEN + timedelta(seconds=10)); rig.px('AAPL', 347.)
    assert request(path) == 0
    key = 'test_tp_nonmarketable_once:AAPL'; raw = rig.store.control(key)
    before = _writes(rig)
    evaluate(rig, pid); rig.tick()
    assert rig.store.control(key) == raw and _writes(rig) == before
    assert not [r for r in _audits(rig,'TEST_TP_TIMEOUT_RESULT') if r.get('phase')=='claimed']
    rig.at(OPEN + timedelta(seconds=30)); evaluate(rig, pid)
    req = start_tp(rig, pid, wire)
    assert req.purpose is Purpose.TP1 and req.limit_price > rig.broker.quote('AAPL').ask
    assert rig.store.control(key) is None
    claims = [r for r in _audits(rig,'TEST_TP_TIMEOUT_RESULT') if r.get('phase')=='claimed']
    assert len(claims) == 1 and claims[0]['intent_id'] == req.intent_id
    fact = json.loads(rig.store.control('test_tp_timeout_claim:'+req.intent_id))
    assert fact['claimed_at'] >= (OPEN + timedelta(seconds=30)).isoformat()
    _safety(rig)


def test_B33_timeout_request_expires_at_original_TTL(paper_opening):
    rig, pid, wire, path = paper_opening
    rig.cfg = replace(rig.cfg,tp_start_after_open_seconds=600); rig.engine.cfg = rig.cfg
    rig.at(OPEN + timedelta(seconds=10)); rig.px('AAPL',347.)
    assert request(path) == 0
    key='test_tp_nonmarketable_once:AAPL'; raw=rig.store.control(key)
    rig.at(OPEN + timedelta(seconds=310)); rig.engine._test_timeout_expire(rig.clock.now())
    assert rig.store.control(key) == raw, 'TTL endpoint remains valid as in b32'
    rig.advance(microseconds=1); rig.engine._test_timeout_expire(rig.clock.now())
    assert rig.store.control(key) is None
    assert any(r.get('outcome')=='refused:expired_or_future' for r in _audits(rig,'TEST_TP_TIMEOUT_RESULT'))
    assert not _tp_requests(rig)


def test_B33_claim_gate_does_not_block_existing_send(paper_opening):
    rig, pid, wire, path = paper_opening
    rig.cfg = replace(rig.cfg,tp_start_after_open_seconds=0); rig.engine.cfg = rig.cfg
    rig.at(OPEN - timedelta(seconds=2)); rig.px('AAPL',347.)
    assert request(path) == 0
    key='test_tp_nonmarketable_once:AAPL'; raw=rig.store.control(key)
    evaluate(rig,pid); rig.engine._run_queue()
    assert rig.store.protection(pid)['state']=='REDUCE_PENDING'
    rig.cfg = replace(rig.cfg,tp_start_after_open_seconds=30); rig.engine.cfg = rig.cfg
    rig.at(OPEN + timedelta(seconds=5)); wire.ack()
    _tick_until(rig,lambda:bool(_tp_requests(rig)))
    req=_tp_requests(rig)[0]
    assert req.limit_price <= rig.broker.quote('AAPL').bid
    assert rig.clock.now() < OPEN + timedelta(seconds=30)
    assert rig.store.control(key)==raw
    assert not [r for r in _audits(rig,'TEST_TP_TIMEOUT_RESULT') if r.get('phase')=='claimed']
    _safety(rig)


def test_B33_defer_audit_is_durable_per_position_and_day(paper_opening, tmp_path, monkeypatch):
    rig,pid,wire,path=paper_opening
    rig.at(OPEN+timedelta(seconds=10)); rig.px('AAPL',347.)
    evaluate(rig,pid); rig.tick()
    assert len(_audits(rig,'TP_START_DEFERRED_OPEN'))==1
    _restart(rig,tmp_path,monkeypatch,reopen=True)
    for _ in range(3): evaluate(rig,pid); rig.tick()
    assert len(_audits(rig,'TP_START_DEFERRED_OPEN'))==1
    assert not rig.store.pending_stages(pid) and not _tp_requests(rig)
    rig.engine.shutdown(grace_seconds=0)
    rig.at(et(9,30,10,day=(2026,10,30))); rig.px('AAPL',347.)
    _restart(rig,tmp_path,monkeypatch,reopen=True)
    evaluate(rig,pid); rig.tick()
    assert len(_audits(rig,'TP_START_DEFERRED_OPEN'))==2


@pytest.mark.parametrize('raw', ['-1', '1.5', '30.0', 'nan', 'inf', '-inf', 'true', '"30"'])
def test_B33_invalid_TOML_refuses_before_engine_start(tmp_path,raw):
    from v6lite.config import load_config, ConfigError
    path=tmp_path/'invalid.toml'
    path.write_text('mode="paper"\n[ibkr]\naccount="DU_TEST"\n[universe]\ntradable=["AAPL"]\nscan=["AAPL"]\n[execution]\ntp_start_after_open_seconds='+raw+'\n')
    with pytest.raises(ConfigError,match='tp_start_after_open_seconds'): load_config(path)
