"""The G2 probe must stay read-only and must produce usable evidence."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from v6lite.config import ConfigError
from v6lite.tests.helpers import make_cfg
from v6lite.tools.g2_probe import PROBE_CLIENT_OFFSET, Probe, write_outputs

PROBE_SRC = Path(__file__).resolve().parents[1] / "tools" / "g2_probe.py"
FORBIDDEN = {"placeOrder", "cancelOrder", "reqGlobalCancel", "exerciseOptions", "replaceFA", "requestFA",
             "reqAutoOpenOrders"}


class _Event(list):
    def __iadd__(self, handler):
        self.append(handler)
        return self


class ProbeIB:
    def __init__(self):
        self.accountValueEvent = _Event()
        self.errorEvent = _Event()
        self.connected = False
        self.calls = []
        self.account_subscriptions = []
        self.blocking_account_requests = 0
        self._pending_account_values = []
        self.client = SimpleNamespace(reqAccountUpdates=self._req_account_updates)
        now = datetime.now(timezone.utc)
        ex = SimpleNamespace(execId="0000e0d5.00000001.01.01", orderRef="v6l-a", clientId=131, shares=10.0,
                             time=now)
        self._fill = SimpleNamespace(execution=ex, contract=SimpleNamespace(symbol="AAPL"),
                                     commissionReport=SimpleNamespace(execId="", commission=0.0))
        self.wrapper = SimpleNamespace(fills={ex.execId: SimpleNamespace(
            execution=ex, commissionReport=SimpleNamespace(execId=ex.execId, commission=1.0))})

    def connect(self, host, port, **kw):
        self.calls.append(("connect", kw))
        self.connected = True
        for handler in self.accountValueEvent:
            handler(SimpleNamespace(account="DU0000000", tag="AccountReady", value="true", currency=""))
            handler(SimpleNamespace(account="DU0000000", tag="AvailableFunds", value="2500", currency="USD"))

    def isConnected(self):
        return self.connected

    def reqMarketDataType(self, t):
        self.calls.append(("mdtype", t))

    def managedAccounts(self):
        return ["DU0000000"]

    def reqCurrentTime(self):
        return datetime.now(timezone.utc) + timedelta(seconds=1)

    def reqAllOpenOrders(self):
        order = SimpleNamespace(orderRef="v6l-x", clientId=131, orderId=5, permId=9, account="DU0000000",
                                action="SELL")
        return [SimpleNamespace(order=order, contract=SimpleNamespace(symbol="AAPL"),
                                orderStatus=SimpleNamespace(status="Submitted", remaining=10.0))]

    def reqExecutions(self, flt):
        return [self._fill]

    def qualifyContracts(self, contract):
        contract.conId = 1
        return [contract]

    def reqHistoricalData(self, contract, **kw):
        return [SimpleNamespace(date=f"2026-10-0{i}", open=1, high=2, low=0.5, close=1.5, volume=10)
                for i in range(1, 6)]

    def reqMktData(self, contract, *a):
        return SimpleNamespace(marketDataType=1, bid=1.0, ask=1.1, last=1.05, time=datetime.now(timezone.utc))

    def reqAccountUpdates(self, account):
        self.blocking_account_requests += 1
        raise AssertionError("probe must use non-blocking account re-subscription")

    def _req_account_updates(self, subscribe, account):
        self.account_subscriptions.append((subscribe, account))
        if subscribe:
            self._pending_account_values.append(SimpleNamespace(
                account=account, tag="AvailableFunds", value="2600", currency="USD"))

    def sleep(self, s):
        # A new callback arrives after re-subscription returns, when events
        # are pumped. The old cached value cannot count as a new push.
        pending, self._pending_account_values = self._pending_account_values, []
        for value in pending:
            for handler in self.accountValueEvent:
                handler(value)


def test_probe_is_read_only_by_construction():
    tree = ast.parse(PROBE_SRC.read_text(encoding="utf-8"))
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not (names & FORBIDDEN), names & FORBIDDEN


def test_probe_refuses_live_config(tmp_path, monkeypatch):
    monkeypatch.setenv("V6LITE_DISCORD_WEBHOOK", "https://example.invalid/hook")
    cfg = make_cfg(tmp_path, mode="live", account="U0000000", port=4001)
    with pytest.raises(ConfigError):
        Probe(cfg, ProbeIB())


def test_probe_collects_evidence_with_its_own_readonly_client(tmp_path):
    cfg = make_cfg(tmp_path)
    ib = ProbeIB()
    ticks = iter(range(0, 10_000, 5))
    ev = Probe(cfg, ib, clock=lambda: next(ticks), sleep=ib.sleep).run(minutes=0.5, compare=False)
    connect = next(kw for name, kw in ib.calls if name == "connect")
    assert connect["readonly"] is True and connect["clientId"] == cfg.client_id + PROBE_CLIENT_OFFSET
    f = ev.findings
    assert f["connected"] and f["account_present"]
    assert f["account_stream"]["ready_keys_seen"] == ["AccountReady"]
    assert f["account_stream"]["available_funds_updates"] == 2
    assert ib.blocking_account_requests == 0
    assert ib.account_subscriptions == [(False, cfg.account), (True, cfg.account)]
    latency = f["account_stream"]["resubscribe_first_available_funds_seconds"]
    assert latency is not None and latency >= 0
    assert f["open_orders"][0]["v6lite"] and f["open_orders_client_ids"] == ["131"]
    assert f["reqExecutions(account)"][0]["commission_reported"] is True      # canonical fill
    assert f["quotes"]["AAPL"]["marketDataType"] == 1
    full_names = {t["call"] for t in ev.timings}
    assert set(f["slowest_seconds_by_call"]) == full_names
    assert {"history AAPL", "history VIX", "reqExecutions(engine client)"} <= full_names
    json_path, md_path = write_outputs(ev, tmp_path / "g2")
    assert json_path.exists() and "G2 probe" in md_path.read_text(encoding="utf-8")


def test_ignore_can_target_the_dry_run_state_only(tmp_path):
    from functools import partial
    from v6lite.__main__ import main, state_path
    from v6lite.config import load_config
    from v6lite.exits import compute_levels
    from v6lite.store import Store
    state = tmp_path / "s"
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text(f"""
mode = "paper"
[ibkr]
account = "DU0000000"
port = 4002
[universe]
tradable = ["AAPL"]
[paths]
state_dir = "{state.as_posix()}"
[alerts]
email_to = "ops@example.invalid"
smtp_host = "smtp.example.invalid"
""", encoding="utf-8")
    cfg = load_config(cfg_path)
    assert main(["ignore", "NVDA", "--dry-run", "--config", str(cfg_path), "--operator", "J"]) == 2  # no state yet
    dry = Store(state_path(cfg, dry_run=True), partial(compute_levels, cfg))
    dry.bind_identity(cfg.account, cfg.mode, role="dryrun")
    dry.close()
    assert main(["ignore", "NVDA", "--dry-run", "--config", str(cfg_path), "--operator", "J"]) == 0
    dry = Store(state_path(cfg, dry_run=True), partial(compute_levels, cfg))
    assert dry.dispositions() == {"NVDA": "ignore"}
    assert not state_path(cfg).exists()                                   # formal state untouched
