"""Round 13: phone pages retain the facts and ordered operator actions.

These are synthetic render-only cases; no broker connection or engine is used.
"""

from __future__ import annotations

import logging
import re

import pytest

from v6lite.alerts import Alerter, MemoryChannel, _page_card, _units
from v6lite.tests.helpers import et


# Representative subjects/bodies from each P0/P1 emission family. The values
# are synthetic; the operational wording follows the production call sites.
PAGES = [
    ("broker anomaly", "P1", "broker data anomaly execution_correction:EX-7",
     "AAPL execution EX-7 corrected; review broker truth and run `clear-anomaly`.",
     ("execution_correction:EX-7", "AAPL"), ("TWS", "clear-anomaly")),
    ("fill anomaly", "P1", "fill anomaly duplicate_open_position:AAPL",
     "New entries are blocked until the operator reviews broker truth and runs `clear-anomaly`.",
     ("duplicate_open_position:AAPL",), ("TWS", "clear-anomaly")),
    ("late exit fill", "P1", "late exit fill AAPL",
     "STOP v6l-late01 filled 3 after it was settled. Retries pulled: v6l-retry02. Check TWS for a short position.",
     ("AAPL", "STOP", "v6l-late01", "3", "v6l-retry02"), ("TWS",)),
    ("disconnect", "P1", "IBKR disconnected",
     "socket disconnected; reconnect and reconcile before new orders",
     ("IBKR",), ("IBKR", "status")),
    ("approval", "P1", "live approval no longer valid",
     "approval expired. New entries blocked; exits continue.",
     ("审批",), ("approve-live",)),
    ("heartbeat", "P1", "IB Gateway not responding",
     "2 consecutive current-time requests exceeded 30s; check Gateway API responsiveness",
     ("2", "30", "IBKR"), ("IBKR", "status")),
    ("old V6", "P1", "旧 V6 运行隔离需复核",
     "旧 V6 进程状态无法核实（PID 1234）", ("1234",), ("1234",)),
    ("combo", "P1", "unresolved combo order working",
     "v6l-combo01: every V6-Lite SELL (including stops) and every release is blocked until it is cancelled or filled in TWS",
     ("v6l-combo01",), ("TWS",)),
    ("held", "P1", "held order is working AAPL",
     "v6l-46b1 (TP1) was held as unverifiable but the broker reports it working with 6 left; those shares are reserved",
     ("AAPL", "v6l-46b1", "TP1", "6"), ("TWS",)),
    ("orphan", "P1", "orphan V6-Lite order AAPL",
     "v6l-orphan01 (Submitted, remaining 4) is unknown to this ledger. New entries are blocked. Cancel it in TWS (or restore the right state directory), then `clear-anomaly`.",
     ("AAPL", "v6l-orphan01", "4", "orphan_order:v6l-orphan01"), ("TWS", "clear-anomaly")),
    ("recovery", "P1", "recovery failed", "order evidence query timed out",
     ("恢复",), ("TWS",)),
    ("prior session", "P1", "unverifiable order from a prior session v6l-prior01",
     "AAPL TP1 10: filled 4 per today's evidence. Check the IBKR trade history, then run `resolve-intent`; a quantity that still differs freezes the symbol (flatten, then `release`). New entries stay blocked until then.",
     ("AAPL", "TP1", "10", "4", "v6l-prior01"), ("resolve-intent", "release")),
    ("order unknown", "P1", "order state unknown v6l-unknown01",
     "entries blocked until the order state is proven",
     ("v6l-unknown01",), ("TWS", "resolve-intent")),
    ("submission unknown", "P1", "order state unknown AAPL",
     "submission acknowledgement for v6l-unknown02 lost; broker may have accepted 6 shares",
     ("AAPL", "v6l-unknown02", "6"), ("TWS", "resolve-intent")),
    ("reconcile", "P1", "broker/runtime mismatch",
     "AAPL: expected 20 broker 17\nResolve with `status`, then `ignore` for unmanaged holdings. A managed position that stays different is frozen: flatten it in TWS, then `release` it.",
     ("AAPL", "20", "17"), ("status", "TWS", "release")),
    ("freeze deferred", "P1", "AAPL freeze check deferred",
     "the quantity difference persists but the broker evidence is incomplete (executions or positions unavailable); no order for it until explained",
     ("AAPL",), ("TWS",)),
    ("release refused", "P1", "release refused AAPL",
     "AAPL has 3 shares at broker or working SELL v6l-open01; resolve before release",
     ("AAPL", "3", "v6l-open01"), ("TWS", "release")),
    ("stale position price", "P1", "positions without fresh prices",
     "AAPL, MSFT: stops cannot be evaluated. Check market data.",
     ("AAPL", "MSFT"), ("IBKR",)),
    ("daily loss", "P1", "daily loss stop latched",
     "loss 160.00 >= 150.00. No new entries today; exits continue. This latch cannot be cleared until the next trading day.",
     ("160.00", "150.00"), ("status",)),
    ("realtime quotes", "P1", "no real-time market data",
     "IBKR refused real-time API data for AAPL; delayed/frozen ticks for MSFT. Those symbols get no usable quote: no entry, and open positions cannot be priced. Enable the real-time subscription and log in to Gateway again.",
     ("AAPL", "MSFT"), ("IBKR",)),
    ("delayed quotes", "P1", "no delayed market data",
     "IBKR reports no subscription for AAPL; usable delayed quotes 1/3; unusable symbols AAPL, MSFT. Check delayed API permission and Gateway market-data status.",
     ("AAPL", "MSFT", "1/3"), ("IBKR",)),
    ("stop retry exhausted", "P0", "AAPL STOP NOT FILLED",
     "20 shares still open. Manual action required in TWS.",
     ("AAPL", "STOP", "20"), ("TWS",)),
    ("take profit retry exhausted", "P1", "AAPL TP1 NOT FILLED",
     "6 shares still open. Manual action required in TWS.",
     ("AAPL", "TP1", "6"), ("TWS",)),
    ("stop blocked", "P0", "exit blocked AAPL STOP",
     "fresh broker position 17 but ledger 20; no SELL until explained",
     ("AAPL", "STOP", "17", "20"), ("TWS",)),
    ("take profit blocked", "P1", "exit blocked AAPL TP1",
     "6 to sell but only 2 not already covered by working SELL orders (v6l-work01 remaining 4). Check TWS.",
     ("AAPL", "TP1", "6", "2", "v6l-work01"), ("TWS",)),
    ("stop unsent", "P0", "exit refused AAPL STOP",
     "sell 20 > free shares 0 (broker 20, working SELL v6l-work01 remaining 20). Check the position in TWS and protect it there if needed",
     ("AAPL", "STOP", "20", "0", "v6l-work01"), ("TWS",)),
    ("take profit unsent", "P1", "exit refused AAPL TP1",
     "sell 6 > free shares 0 (broker 20, working SELL v6l-work01 remaining 20). Check the position in TWS and protect it there if needed",
     ("AAPL", "TP1", "6", "0", "v6l-work01"), ("TWS",)),
    ("stop reduced", "P1", "AAPL STOP reduced to 4",
     "10 wanted, but working SELL orders already cover 6: v6l-work01 SELL remaining 6",
     ("AAPL", "STOP", "10", "4", "6", "v6l-work01"), ("TWS",)),
    ("exit rejected", "P1", "exit rejected AAPL TP1",
     "broker rejected order v6l-exit01, 6 shares at 190.25",
     ("AAPL", "TP1", "v6l-exit01", "6", "190.25"), ("TWS",)),
    ("shutdown query unknown", "P1", "shutdown: working orders unknown",
     "could not list open orders (request timeout); check TWS for v6l- orders",
     ("挂单",), ("TWS",)),
    ("shutdown unresolved", "P1", "shutdown with unresolved orders",
     "ledger: v6l-ledger01; broker working: v6l-work01",
     ("v6l-ledger01", "v6l-work01"), ("TWS",)),
    ("freeze", "P0", "V6-Lite no longer manages AAPL",
     "AAPL: ledger 20, broker 17. The difference persisted after a fresh execution and position check. "
     "V6-Lite will not send any new order for AAPL and new entries are blocked. First, in TWS: "
     "cancel requests were sent for V6-Lite's own working orders: v6l-own01 | "
     "these orders were NOT cancelled and can still trade AAPL: v6l-46b1 SELL 6 (V6-Lite order held for the operator); "
     "v6l-orphan01 SELL 4 (V6-Lite order the ledger does not track). Confirm that no order for AAPL is working, "
     "THEN protect or flatten the position. Then run `release AAPL` once the broker shows 0 shares and no working order.",
     ("AAPL", "20", "17", "v6l-own01", "v6l-46b1", "6", "v6l-orphan01", "4"),
     ("TWS", "release AAPL")),
]


def _parts(level: str, subject: str, body: str):
    card = _page_card(level, subject, body)
    embed = card.as_embed()
    fields = embed["fields"]
    assert [field["name"] for field in fields] == ["发生了什么", "影响", "你要做什么"]
    values = [field["value"] for field in fields]
    all_text = embed["title"] + "\n" + "\n".join(
        field["name"] + "：" + field["value"] for field in fields)
    assert all(0 < _units(value) <= 600 for value in values)
    assert _units(all_text) <= 1500
    assert all(value.strip() != "详见本机日志" for value in values)
    assert not re.search(r"https?://|(?:bot[_ -]?token|webhook)", all_text, re.I)
    assert re.search(r"(?:^|\n)1[）.)]", values[-1]), all_text
    return embed["title"], values, all_text


@pytest.mark.parametrize("family,level,subject,body,facts,actions", PAGES,
                         ids=[row[0] for row in PAGES])
def test_H1_H2_H3_H4_each_page_retains_critical_facts_and_numbered_actions(
        family, level, subject, body, facts, actions):
    title, values, all_text = _parts(level, subject, body)
    for fact in facts:
        assert fact in all_text, (family, fact, all_text)
    for action in actions:
        assert action in values[-1], (family, action, values[-1])
    if family == "stop unsent":
        assert "止损" in title and "未发出" in title
        assert "止损" in values[0] and "未发出" in values[0]


def test_H1_every_page_family_has_distinct_title():
    titles = [(family, re.sub(r"^[🔴🟠]\s+P[01]\s*·\s*", "",
                              _page_card(level, subject, body).title))
              for family, level, subject, body, *_ in PAGES]
    assert len({title for _, title in titles}) == len(titles), titles


def test_H3_freeze_orders_before_flatten_then_release():
    _, _, subject, body, _, _ = next(row for row in PAGES if row[0] == "freeze")
    _, values, _ = _parts("P0", subject, body)
    action = values[-1]
    assert action.index("v6l-46b1") < action.index("保护或平仓")
    assert action.index("v6l-orphan01") < action.index("保护或平仓")
    assert action.index("保护或平仓") < action.index("release AAPL")
    assert "0 股" in action
    assert re.search(r"1[）.)].*2[）.)].*3[）.)]", action, re.S)


def test_H4_long_order_list_keeps_first_five_and_count_without_clipping_steps():
    orders = "; ".join(f"v6l-order{i:02d} SELL {i}" for i in range(1, 12))
    body = ("AAPL: ledger 20, broker 17. Explanation " + "x" * 2200 + ". First, in TWS: "
            "these orders were NOT cancelled and can still trade AAPL: " + orders + ". "
            "Confirm that no order for AAPL is working, THEN protect or flatten the position. "
            "Then run `release AAPL` once the broker shows 0 shares and no working order.")
    _, values, all_text = _parts("P0", "V6-Lite no longer manages AAPL", body)
    for index in range(1, 6):
        assert f"v6l-order{index:02d}" in all_text
    assert "另有 6 个" in all_text
    assert "20" in all_text and "17" in all_text
    assert "release AAPL" in values[-1]
    assert re.search(r"1[）.)].*2[）.)].*3[）.)]", values[-1], re.S)


def test_H4_astral_characters_count_two_units_and_do_not_hide_orders_or_steps():
    body = ("AAPL: expected 20 broker 17. Explanation " + "🚨" * 350 +
            ". First, in TWS: these orders were NOT cancelled and can still trade AAPL: "
            "v6l-held01 SELL 6; v6l-orphan01 SELL 4. "
            "Confirm no AAPL order is working, THEN protect or flatten. "
            "Run `release AAPL` once broker shows 0 shares and no working order.")
    _, values, all_text = _parts("P0", "V6-Lite no longer manages AAPL", body)
    assert _units(all_text) <= 1500
    assert "v6l-held01" in all_text and "v6l-orphan01" in all_text
    assert "release AAPL" in values[-1]


def test_H4_embedded_synthetic_url_and_token_are_not_transmitted():
    body = ("AAPL ledger 20 broker 17; check https://example.invalid/private/example; "
            "bot token SYNTHETIC_FAKE_SECRET_123; release AAPL")
    _, _, all_text = _parts("P0", "V6-Lite no longer manages AAPL", body)
    assert "example.invalid" not in all_text
    assert "SYNTHETIC_FAKE_SECRET_123" not in all_text


def test_H2_H3_remote_page_does_not_change_local_english_original(caplog):
    caplog.set_level(logging.ERROR)
    channel = MemoryChannel()
    alerter = Alerter([channel], synchronous=True)
    subject = "exit refused AAPL STOP"
    body = "sell 20 > free shares 0; no SELL; check the position in TWS"
    try:
        alerter.send(et(10), "P0", subject, body)
        assert subject in caplog.text and body in caplog.text
        assert len(channel.messages) == 1
        assert "止损" in channel.messages[0][1]
        assert "1）" in channel.messages[0][1]
    finally:
        alerter.close()


def test_H5_unknown_python_pid_formal_refusal_and_dryrun_page(tmp_path, monkeypatch, capsys):
    from v6lite import approval, alerts
    from v6lite.tests.test_batch10 import _fake_cli

    def unknown_process():
        raise approval.GateError("cannot verify Python process 1234 command")

    monkeypatch.setattr(approval, "old_v6_processes", unknown_process)
    cli, formal_broker = _fake_cli(tmp_path / "formal", monkeypatch)
    assert cli.main(["run", "--config", str(tmp_path / "formal" / "fake.toml")]) == 2
    stderr = capsys.readouterr().err
    assert "REFUSED" in stderr and "1234" in stderr
    assert "确认" in stderr or "关闭" in stderr or "close" in stderr.lower()
    assert not formal_broker.connected and not formal_broker.sent

    cli, dry_broker = _fake_cli(tmp_path / "dry", monkeypatch, capture={})
    channel = MemoryChannel()
    monkeypatch.setattr(alerts, "build_channels", lambda cfg: [channel])
    assert cli.main(["run", "--dry-run", "--config", str(tmp_path / "dry" / "fake.toml")]) == 0
    pages = [body for subject, body in channel.messages if "P1" in subject and "旧 V6" in subject]
    assert pages and "1234" in pages[-1]
    assert "确认" in pages[-1] or "关闭" in pages[-1]
    assert not dry_broker.sent and not dry_broker.cancels


def test_H5_psutil_access_denied_preserves_the_unreadable_pid(monkeypatch):
    """One inaccessible Windows Python process must be named in the refusal."""
    from v6lite import approval

    class InaccessiblePython:
        pid = 1234

        @property
        def info(self):
            raise approval.psutil.AccessDenied(pid=1234, name="python.exe")

    monkeypatch.setattr(approval.psutil, "process_iter",
                        lambda *args, **kwargs: iter([InaccessiblePython()]))
    with pytest.raises(approval.GateError) as caught:
        approval.old_v6_processes()
    assert "1234" in str(caught.value)


def test_H5_process_iterator_access_denied_keeps_pid_before_first_yield(monkeypatch):
    from v6lite import approval

    def unavailable(*args, **kwargs):
        raise approval.psutil.AccessDenied(pid=5678, name="python.exe")

    monkeypatch.setattr(approval.psutil, "process_iter", unavailable)
    with pytest.raises(approval.GateError) as caught:
        approval.old_v6_processes()
    assert "5678" in str(caught.value)


def test_H3_unknown_submission_without_intent_id_never_suggests_symbol_as_id():
    card = _page_card("P1", "order state unknown AAPL", "submission acknowledgement lost")
    action = card.as_embed()["fields"][-1]["value"]
    assert "status" in action and "关联意图 ID" in action
    assert "resolve-intent AAPL" not in action
    assert "resolve-intent <意图ID>" in action


def test_H2_freeze_unknown_order_side_is_not_mislabeled_as_sell():
    body = ("AAPL: expected 20 broker 17. First, in TWS: "
            "these orders were NOT cancelled and can still trade AAPL: "
            "v6l-side01 ? 4 (unresolved combo leg). "
            "Confirm that no order for AAPL is working, THEN protect or flatten the position. "
            "Then run `release AAPL` once the broker shows 0 shares and no working order.")
    _, _, all_text = _parts("P0", "V6-Lite no longer manages AAPL", body)
    assert "v6l-side01 方向未知 4 股" in all_text
    assert "v6l-side01 卖 4 股" not in all_text


@pytest.mark.parametrize("digits", [20, 40, 60])
def test_H4_five_long_order_ids_and_large_quantities_keep_all_steps(digits):
    ids = [f"v6l-{i:02d}-" + "x" * 71 for i in range(1, 6)]  # 78 chars each
    quantities = [str(10 ** (digits - 1) + i) for i in range(1, 6)]
    listing = "; ".join(f"{iid} SELL {qty} (held for operator)"
                        for iid, qty in zip(ids, quantities))
    body = ("AAPL: expected 20 broker 17. First, in TWS: "
            "these orders were NOT cancelled and can still trade AAPL: " + listing +
            ". Confirm that no order for AAPL is working, THEN protect or flatten the position. "
            "Then run `release AAPL` once the broker shows 0 shares and no working order.")
    _, values, all_text = _parts("P0", "V6-Lite no longer manages AAPL", body)
    assert all(iid in all_text for iid in ids)
    assert all(qty in all_text for qty in quantities)
    action = values[-1]
    assert re.search(r"1[）.)].*2[）.)].*3[）.)]", action, re.S)
    assert re.search(r"release AAPL[。.]?$", action)


def test_H4_reconcile_five_symbols_keep_every_large_ledger_and_broker_qty():
    symbols = ("AAPL", "MSFT", "NVDA", "TSLA", "GOOGL")
    rows = [(symbol, str(10**59 + i), str(2 * 10**59 + i))
            for i, symbol in enumerate(symbols, 1)]
    body = "\n".join(f"{symbol}: expected {ledger} broker {broker}"
                     for symbol, ledger, broker in rows)
    body += "\nResolve with `status`; compare TWS; flatten any persistent difference, then `release`."
    _, values, all_text = _parts("P1", "broker/runtime mismatch", body)
    for symbol, ledger, broker in rows:
        assert symbol in all_text
        assert ledger in all_text
        assert broker in all_text
    assert re.search(r"1[）.)].*2[）.)].*3[）.)]", values[-1], re.S)


def test_H4_stop_reduced_keeps_large_numbers_and_five_long_order_ids():
    ids = [f"v6l-{i:02d}-" + "y" * 71 for i in range(1, 6)]
    capacity, wanted, covered = [str(i * 10**59 + i) for i in (1, 2, 3)]
    body = (f"{wanted} wanted, but working SELL orders already cover {covered}: " +
            "; ".join(f"{iid} SELL remaining 4" for iid in ids))
    _, values, all_text = _parts("P1", f"AAPL STOP reduced to {capacity}", body)
    assert all(number in all_text for number in (capacity, wanted, covered))
    assert all(iid in all_text for iid in ids)
    assert re.search(r"1[）.)].*2[）.)].*3[）.)]", values[-1], re.S)
