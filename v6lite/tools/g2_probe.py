"""G2 evidence probe: read-only observation of the real IB Gateway (Paper).

    python -m v6lite.tools.g2_probe --config v6lite.paper.toml [--minutes 20] [--compare-yfinance]

It answers the questions the reviews left for G2 with measurements instead of
assumptions: which account-value keys arrive (AccountReady vs accountReady),
how often AvailableFunds is pushed, how long every blocking request really
takes, which orders reqAllOpenOrders can see, whether commissions are present
for today's executions, which market-data type each quote is, how stale quotes
get, which connectivity codes (1100/1101/1102/...) occur, and how IBKR daily
bars compare with the legacy yfinance source.

Safety boundary (enforced here, re-checked by tests):
* paper configs only; the connection is opened with readonly=True
* its own client id (config client_id + 2000): never the engine's, the
  dry-run's (+1000) or legacy V6's (120)
* no order placement, cancellation or account modification call exists in this
  module; it only reads
* output goes to <state_dir>/g2/ (outside the repository)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from v6lite.config import ConfigError, LiteConfig, load_config

PROBE_CLIENT_OFFSET = 2000
REQUEST_TIMEOUT_SECONDS = 15
HISTORY_TIMEOUT_SECONDS = 10
LEGACY_CLIENT_ID = 120
SAMPLE_EVERY_SECONDS = 5
READY_KEYS = ("AccountReady", "accountReady")


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass
class Evidence:
    started_at: str
    config_fingerprint: str
    account: str
    client_id: int
    timings: list[dict] = field(default_factory=list)
    account_events: list[dict] = field(default_factory=list)
    connectivity: list[dict] = field(default_factory=list)
    findings: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class Probe:
    def __init__(self, cfg: LiteConfig, ib, *, clock=time.monotonic, sleep=None):
        if cfg.mode != "paper":
            raise ConfigError("the G2 probe only runs against a paper configuration")
        self.cfg = cfg
        self.ib = ib
        self.clock = clock
        self.sleep = sleep or (lambda s: ib.sleep(s))
        self.client_id = cfg.client_id + PROBE_CLIENT_OFFSET
        if self.client_id in {cfg.client_id, cfg.client_id + 1000, LEGACY_CLIENT_ID} or not 1 <= self.client_id <= 32767:
            raise ConfigError(f"probe client id {self.client_id} collides or is out of range")
        self.ev = Evidence(_utc().isoformat(), cfg.fingerprint()[:16], cfg.account, self.client_id)
        self._tickers: dict[str, object] = {}
        self._resubscribe_started: float | None = None
        self._resubscribe_first_funds_seconds: float | None = None

    # ------------------------------------------------------------ plumbing
    def _timed(self, name: str, fn, *args, **kwargs):
        start = self.clock()
        ok, result, error = True, None, None
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # recorded, never raised: a probe keeps observing
            ok, error = False, f"{type(exc).__name__}: {exc}"
            self.ev.errors.append(f"{name}: {error}")
        self.ev.timings.append({"call": name, "seconds": round(self.clock() - start, 3), "ok": ok,
                                **({"error": error} if error else {})})
        return result

    def _on_account_value(self, value) -> None:
        if (getattr(value, "account", "") in (self.cfg.account, "") and value.tag == "AvailableFunds"
                and self._resubscribe_started is not None and self._resubscribe_first_funds_seconds is None):
            # A callback proves a new push even when the numeric value is unchanged.
            self._resubscribe_first_funds_seconds = self.clock() - self._resubscribe_started
        self.ev.account_events.append({
            "t": _utc().isoformat(), "account": getattr(value, "account", ""), "tag": value.tag,
            "currency": getattr(value, "currency", ""),
            "value": value.value if value.tag in READY_KEYS or value.tag in {"AvailableFunds", "NetLiquidation"}
            else None})

    def _on_error(self, req_id, code, message, contract) -> None:
        self.ev.connectivity.append({"t": _utc().isoformat(), "code": code, "req_id": req_id,
                                     "message": str(message)})

    # ------------------------------------------------------------ steps
    def connect(self) -> bool:
        self.ib.RequestTimeout = REQUEST_TIMEOUT_SECONDS
        self.ib.accountValueEvent += self._on_account_value
        self.ib.errorEvent += self._on_error
        self._timed("connect", self.ib.connect, self.cfg.host, self.cfg.port, clientId=self.client_id,
                    timeout=20, readonly=True, account=self.cfg.account)
        connected = bool(self.ib.isConnected())
        self.ev.findings["connected"] = connected
        if connected:
            self.ib.reqMarketDataType(self.cfg.market_data_type)
        return connected

    def identity(self) -> None:
        accounts = self._timed("managedAccounts", self.ib.managedAccounts) or []
        server = self._timed("reqCurrentTime", self.ib.reqCurrentTime)
        local = _utc()
        self.ev.findings["managed_accounts"] = list(accounts)
        self.ev.findings["account_present"] = self.cfg.account in accounts
        if isinstance(server, datetime):
            server = server if server.tzinfo else server.replace(tzinfo=timezone.utc)
            self.ev.findings["server_minus_local_seconds"] = round((server - local).total_seconds(), 3)

    def orders_and_executions(self) -> None:
        from ib_insync import ExecutionFilter

        trades = self._timed("reqAllOpenOrders", self.ib.reqAllOpenOrders) or []
        rows = []
        for t in trades:
            ref = getattr(t.order, "orderRef", "") or ""
            rows.append({"orderRef": ref, "v6lite": ref.startswith("v6l-"), "clientId": getattr(t.order, "clientId", None),
                         "orderId": getattr(t.order, "orderId", None), "permId": getattr(t.order, "permId", None),
                         "account": getattr(t.order, "account", ""), "symbol": getattr(t.contract, "symbol", ""),
                         "action": getattr(t.order, "action", ""), "status": getattr(t.orderStatus, "status", ""),
                         "remaining": _num(getattr(t.orderStatus, "remaining", None))})
        self.ev.findings["open_orders"] = rows
        self.ev.findings["open_orders_client_ids"] = sorted({str(r["clientId"]) for r in rows})
        for label, flt in (("reqExecutions(account)", ExecutionFilter(acctCode=self.cfg.account)),
                           ("reqExecutions(engine client)", ExecutionFilter(clientId=self.cfg.client_id,
                                                                            acctCode=self.cfg.account))):
            fills = self._timed(label, self.ib.reqExecutions, flt) or []
            known = getattr(getattr(self.ib, "wrapper", None), "fills", {}) or {}
            summary = []
            for f in fills:
                ex = f.execution
                canonical = known.get(ex.execId)
                report = getattr(canonical or f, "commissionReport", None)
                summary.append({"execId": ex.execId, "orderRef": getattr(ex, "orderRef", ""),
                                "clientId": getattr(ex, "clientId", None), "symbol": getattr(f.contract, "symbol", ""),
                                "shares": _num(ex.shares), "time": str(getattr(ex, "time", "")),
                                "commission_reported": bool(report is not None and getattr(report, "execId", "")),
                                "execId_segments": ex.execId.count(".") + 1})
            self.ev.findings[label] = summary

    def history(self) -> dict[str, list]:
        from ib_insync import Index, Stock

        bars_by_symbol: dict[str, list] = {}
        today = date.today().isoformat()
        info = {}
        for symbol in self.cfg.scan_symbols:
            contract = Stock(symbol, "SMART", "USD")
            self._timed(f"qualify {symbol}", self.ib.qualifyContracts, contract)
            bars = self._timed(f"history {symbol}", self.ib.reqHistoricalData, contract, endDateTime="",
                               durationStr=f"{self.cfg.bars_lookback_days} D", barSizeSetting="1 day",
                               whatToShow="TRADES", useRTH=True, formatDate=1,
                               timeout=HISTORY_TIMEOUT_SECONDS) or []
            bars_by_symbol[symbol] = bars
            if getattr(contract, "conId", 0):
                self._tickers[symbol] = self.ib.reqMktData(contract, "", False, False)
            info[symbol] = {"bars": len(bars), "first": str(bars[0].date) if bars else None,
                            "last": str(bars[-1].date) if bars else None,
                            "last_is_today": bool(bars) and str(bars[-1].date)[:10] == today}
        vix = Index("VIX", "CBOE")
        self._timed("qualify VIX", self.ib.qualifyContracts, vix)
        vix_bars = self._timed("history VIX", self.ib.reqHistoricalData, vix, endDateTime="", durationStr="5 D",
                               barSizeSetting="1 day", whatToShow="TRADES", useRTH=True, formatDate=1,
                               timeout=HISTORY_TIMEOUT_SECONDS) or []
        info["VIX"] = {"bars": len(vix_bars), "last_close": _num(vix_bars[-1].close) if vix_bars else None}
        self.ev.findings["history"] = info
        return bars_by_symbol

    def refresh_account(self) -> None:
        def resubscribe() -> None:
            client = getattr(self.ib, "client", None)
            if client is None or not hasattr(client, "reqAccountUpdates"):
                raise RuntimeError("non-blocking account subscription unavailable")
            client.reqAccountUpdates(False, self.cfg.account)
            self._resubscribe_first_funds_seconds = None
            self._resubscribe_started = self.clock()
            client.reqAccountUpdates(True, self.cfg.account)

        self._timed("reqAccountUpdates(resubscribe)", resubscribe)

    def watch(self, minutes: float) -> None:
        """Sample quotes and the account stream for `minutes` (pumps IB events)."""
        deadline = self.clock() + minutes * 60
        worst_age: dict[str, float] = {}
        samples = 0
        while self.clock() < deadline:
            self.sleep(SAMPLE_EVERY_SECONDS)
            samples += 1
            now = _utc()
            for symbol, ticker in self._tickers.items():
                ts = getattr(ticker, "time", None)
                if isinstance(ts, datetime):
                    ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
                    worst_age[symbol] = max(worst_age.get(symbol, 0.0), (now - ts).total_seconds())
                else:
                    worst_age[symbol] = float("inf")
        self.ev.findings["watch"] = {"minutes": minutes, "samples": samples,
                                     "worst_quote_age_seconds": {k: (None if v == float("inf") else round(v, 1))
                                                                 for k, v in worst_age.items()}}

    def quotes(self) -> None:
        out = {}
        for symbol, ticker in self._tickers.items():
            out[symbol] = {"marketDataType": getattr(ticker, "marketDataType", None),
                           "bid": _num(getattr(ticker, "bid", None)), "ask": _num(getattr(ticker, "ask", None)),
                           "last": _num(getattr(ticker, "last", None)),
                           "time": str(getattr(ticker, "time", None))}
        self.ev.findings["quotes"] = out

    def summarize_account_stream(self) -> None:
        mine = [e for e in self.ev.account_events if e["account"] in (self.cfg.account, "")]
        tags = sorted({e["tag"] for e in mine})
        ready = [(e["tag"], e["value"]) for e in mine if e["tag"] in READY_KEYS]
        funds = [e for e in mine if e["tag"] == "AvailableFunds"]
        times = [datetime.fromisoformat(e["t"]) for e in funds]
        gaps = [round((b - a).total_seconds(), 1) for a, b in zip(times, times[1:])]
        any_times = [datetime.fromisoformat(e["t"]) for e in mine]
        any_gaps = [round((b - a).total_seconds(), 1) for a, b in zip(any_times, any_times[1:])]
        self.ev.findings["account_stream"] = {
            "events": len(mine), "distinct_tags": len(tags),
            "ready_keys_seen": sorted({k for k, _ in ready}), "ready_values": ready[-5:],
            "available_funds_updates": len(funds),
            "available_funds_currencies": sorted({e["currency"] for e in funds}),
            "available_funds_max_gap_seconds": max(gaps) if gaps else None,
            "any_value_max_gap_seconds": max(any_gaps) if any_gaps else None,
            "resubscribe_first_available_funds_seconds": self._resubscribe_first_funds_seconds,
        }

    def compare_sources(self, bars_by_symbol: dict[str, list]) -> None:
        """IBKR daily bars vs the legacy yfinance source, scored by the ported
        strategy on the SAME window (same last date, same number of bars):
        EMA50 and ADX depend on how much history they start from, so windows
        of different length differ even on identical prices (G2 day 1 compared
        90 IBKR bars with ~125 yfinance bars). Indicator values are recorded
        so a score difference can be traced to its component. The legacy
        scanner's own window (yfinance period=60d) is scored as well."""
        from v6lite.models import Bar
        from v6lite.strategy import score_symbol
        try:
            import yfinance as yf
        except ImportError:
            self.ev.findings["source_comparison"] = "yfinance not installed; skipped"
            return

        def parts(r) -> dict:
            return {"signal": r.signal, "score": r.score, "price": r.price, "ema9": r.ema9, "ema21": r.ema21,
                    "ema50": r.ema50, "adx": r.adx, "rsi": r.rsi, "atr": r.atr, "volume_ratio": r.volume_ratio}

        rows = {}
        for symbol, ib_bars in bars_by_symbol.items():
            ib = [Bar(str(b.date)[:10], float(b.open), float(b.high), float(b.low), float(b.close),
                      float(b.volume)) for b in ib_bars]
            try:
                hist = yf.Ticker(symbol).history(period="1y", interval="1d")
                yall = [Bar(str(i)[:10], float(r.Open), float(r.High), float(r.Low), float(r.Close),
                            float(r.Volume)) for i, r in hist.iterrows()]
                legacy = yf.Ticker(symbol).history(period="60d", interval="1d")
                yleg = [Bar(str(i)[:10], float(r.Open), float(r.High), float(r.Low), float(r.Close),
                            float(r.Volume)) for i, r in legacy.iterrows()]
            except Exception as exc:
                rows[symbol] = {"error": str(exc)}
                continue
            etf = symbol in self.cfg.etf_symbols
            last = ib[-1].day if ib else None
            yb = [b for b in yall if last is None or b.day <= last][-len(ib):] if ib else []
            a, b = score_symbol(symbol, ib, is_etf=etf), score_symbol(symbol, yb, is_etf=etf)
            c = score_symbol(symbol, yleg, is_etf=etf)
            close_diff = max((abs(x.close - y.close) / y.close for x, y in zip(ib, yb) if x.day == y.day and y.close),
                             default=None)
            rows[symbol] = {"window_bars": len(ib), "ibkr_last": last, "yf_last": yb[-1].day if yb else None,
                            "same_dates": [x.day for x in ib] == [y.day for y in yb],
                            "max_close_diff_pct": round(close_diff * 100, 3) if close_diff is not None else None,
                            "ibkr": parts(a), "yfinance_same_window": parts(b),
                            "legacy_yfinance_60d": {"bars": len(yleg), **parts(c)},
                            "same_signal": a.signal == b.signal, "score_diff": a.score - b.score}
        self.ev.findings["source_comparison"] = rows

    def run(self, minutes: float, compare: bool) -> Evidence:
        if not self.connect():
            return self.ev
        self.identity()
        self.orders_and_executions()
        bars = self.history()
        self.refresh_account()
        self.sleep(3)
        self.quotes()
        self.watch(minutes)
        self.quotes()
        self.summarize_account_stream()
        if compare:
            self.compare_sources(bars)
        slowest: dict[str, float] = {}
        for t in self.ev.timings:
            name = t["call"]
            slowest[name] = max(slowest.get(name, 0.0), t["seconds"])
        self.ev.findings["slowest_seconds_by_call"] = slowest
        return self.ev


def _comparison_line(rows) -> str:
    if not isinstance(rows, dict):
        return str(rows or "not run")
    out = []
    for sym, r in rows.items():
        if "error" in r:
            out.append(f"{sym} error")
            continue
        a, b = r["ibkr"], r["yfinance_same_window"]
        flag = "" if r["same_signal"] else " SIGNAL DIFFERS"
        out.append(f"{sym} {a['signal']}/{a['score']} vs {b['signal']}/{b['score']}"
                   f"{'' if r['same_dates'] else ' (dates differ)'}{flag}")
    return "; ".join(out)


def write_outputs(ev: Evidence, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"g2_probe_{stamp}.json"
    json_path.write_text(json.dumps(ev.__dict__, indent=1, default=str), encoding="utf-8")
    f = ev.findings
    lines = [f"# G2 probe {ev.started_at}", "",
             f"- account {ev.account}, probe client {ev.client_id}, config {ev.config_fingerprint}",
             f"- connected: {f.get('connected')}, account present: {f.get('account_present')}, "
             f"server-local clock: {f.get('server_minus_local_seconds')} s",
             f"- account stream: {json.dumps(f.get('account_stream'), default=str)}",
             f"- slowest call seconds: {json.dumps(f.get('slowest_seconds_by_call'))}",
             f"- open orders visible: {len(f.get('open_orders', []))} "
             f"(clientIds {f.get('open_orders_client_ids')})",
             f"- executions (account / engine client): {len(f.get('reqExecutions(account)', []))} / "
             f"{len(f.get('reqExecutions(engine client)', []))}",
             f"- quotes: {json.dumps(f.get('quotes'), default=str)}",
             f"- worst quote age: {json.dumps(f.get('watch'), default=str)}",
             f"- connectivity codes: {sorted({c['code'] for c in ev.connectivity})}",
             f"- source comparison (same window): {_comparison_line(f.get('source_comparison'))}",
             f"- errors: {ev.errors or 'none'}", ""]
    md_path = json_path.with_suffix(".md")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="v6lite.tools.g2_probe")
    parser.add_argument("--config", required=True)
    parser.add_argument("--minutes", type=float, default=20.0, help="how long to watch quotes/account stream")
    parser.add_argument("--compare-yfinance", action="store_true")
    parser.add_argument("--out", default=None, help="output folder (default <state_dir>/g2)")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    from ib_insync import IB

    ib = IB()
    try:
        probe = Probe(cfg, ib)
    except ConfigError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    try:
        ev = probe.run(args.minutes, args.compare_yfinance)
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass
    out = Path(args.out) if args.out else cfg.resolved_state_dir() / "g2"
    json_path, md_path = write_outputs(ev, out)
    print(md_path.read_text(encoding="utf-8"))
    print(f"evidence: {json_path}")
    return 0 if ev.findings.get("connected") else 1


if __name__ == "__main__":
    raise SystemExit(main())
