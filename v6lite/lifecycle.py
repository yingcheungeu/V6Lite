"""Exit provenance and best-effort emergency paging; never queries the broker."""
from __future__ import annotations

import errno
import logging
import re
from types import SimpleNamespace

from v6lite.alerts import AlertCard, P0_RED
from v6lite.telemetry import redact_accounts

log = logging.getLogger("v6lite.lifecycle")
OPERATOR_SOURCES = {"SIGINT", "SIGBREAK", "kill", "operator"}


def transport_error(error):
    # Do not turn a local disk/SQLite OSError into a reconnect, even if the
    # broker happens to be down at the same time.
    return isinstance(error, ConnectionError) or (
        isinstance(error, OSError) and (
            getattr(error, "winerror", None) in {64, 121, 1231, 10050, 10051, 10052, 10053, 10054, 10057, 10058, 10060, 10061, 10064, 10065}
            or error.errno in {64, errno.ECONNABORTED, errno.ECONNRESET, errno.ENETDOWN,
                               errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENOTCONN, errno.EPIPE}))


def safe_reason(error, account):
    text = redact_accounts(str(error) if error is not None else "operator requested stop", account)
    text = re.sub(r"https?://[^\s]+", "<redacted-url>", text, flags=re.I)
    text = re.sub(r"(authorization[\"']?\s*[:=]\s*[\"']?)(?:bearer|basic)\s+[^\s,;\"'}]+",
                  r"\1<redacted>", text, flags=re.I)
    text = re.sub(r"(password|token|secret|api[_-]?key|webhook|authorization)([\"']?\s*[:=]\s*)"
                  r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)",
                  r"\1\2<redacted>", text, flags=re.I)
    return " ".join(text.split())


def exit_positions(engine):
    try:
        positions = list(engine.store.open_positions())
    except Exception:
        return getattr(engine, "_shutdown_positions", None) or "持仓、STP orderId 和止损价无法读取；在 TWS 核实。"
    lines = []
    for pos in positions:
        try:
            row = engine.store.protection(pos["position_id"])
            stop = row["trigger_price"] if row is not None else pos["stop"]
            order_id = row["broker_order_id"] if row is not None else None
            lines.append(f"{pos['symbol']} {pos['qty_open']:g} 股；STP orderId={order_id or '未知'}；止损价={stop:g}")
        except Exception:
            lines.append(f"{pos['symbol']}：持仓或 STP 详情无法读取，在 TWS 核实")
    return "；".join(lines) if lines else "本地未记录持仓；真实持仓与工作单仍须在 TWS 核实。"


def request_shutdown(engine, source, error=None):
    previous = getattr(engine, "_shutdown_request", None)
    # A failure during an operator shutdown is a new fatal exit. It must not
    # inherit the operator exemption, nor duplicate a previous fatal page.
    if previous and (previous["source"] not in OPERATOR_SOURCES or source in OPERATOR_SOURCES):
        return previous
    now = engine.clock.now()
    request = {"source": source, "exception_type": type(error).__name__ if error else None,
               "reason": safe_reason(error, engine.cfg.account), "at": now.isoformat()}
    engine._shutdown_request = request
    engine._shutdown_positions = exit_positions(engine)
    log.warning("[SHUTDOWN_REQUESTED] source=%s exception_type=%s reason=%s at=%s",
                source, request["exception_type"], request["reason"], request["at"])
    try:
        engine.store.audit(now, "SHUTDOWN_REQUESTED", **request)
    except Exception as exc:
        log.error("SHUTDOWN_REQUESTED persistence failed (%s)", type(exc).__name__)
    if source not in OPERATOR_SOURCES:
        send_exit_page(engine, request)
    return request


def send_exit_page(engine, request):
    """Delivery retry preserves the original fatal reason and its audit."""
    now = engine.clock.now()
    source = request["source"]
    details = exit_positions(engine)
    action = "1）在 TWS 核对真实持仓、STP 和全部工作单；2）排除退出原因后运行 ~\\v6 g3t，由 John 确认启动并核验接管。"
    body = (f"source={source}；{request['exception_type'] or 'unknown'}：{request['reason']}；"
            f"{details}；持仓仅由券商端止损保护；引擎止盈监控与告警已停止。{action}")
    card = AlertCard("🔴 P0 · V6-Lite 引擎已退出，持仓仅由券商端止损保护", P0_RED,
                     (("退出原因", f"{source} / {request['exception_type']}：{request['reason']}"),
                      ("持仓与保护（最后本地记录，未重新核实）", details),
                      ("影响", "止盈监控和心跳已停止；券商端止损状态须在 TWS 核实。"),
                      ("你要做什么", action)))
    try:
        engine._exit_alert_delivery = engine.alerter.send(now, "P0", "V6-Lite engine exited", body, card=card, dedupe=False)
    except Exception as exc:
        log.error("engine exit P0 delivery failed (%s)", type(exc).__name__)


def log_exit(engine):
    request = getattr(engine, "_shutdown_request", None)
    if request and request["source"] not in OPERATOR_SOURCES:
        log.error("!!! V6-Lite 引擎已退出 source=%s exception_type=%s reason=%s !!!",
                  request["source"], request["exception_type"], request["reason"])


def startup_exit(cfg, error, *, now, source=None, engine=None):
    """No valid Store may exist. Never open/migrate an unverified identity.

    Configuration load failures cannot use this path: without a valid config
    there is no trusted alert destination. main() still exits nonzero/ERROR.
    """
    from v6lite.alerts import Alerter, build_channels
    from v6lite.config import ConfigError
    from v6lite.approval import GateError
    if source is None:
        source = "config" if isinstance(error, (ConfigError, GateError)) else "fatal_error"
    try:
        channels = build_channels(cfg.alerts)
    except Exception:
        channels = []
    sender = Alerter(channels, synchronous=True)
    if engine is None:
        context = SimpleNamespace(cfg=cfg, store=None, clock=SimpleNamespace(now=now), alerter=sender)
    else:
        context = engine
    original = context.alerter
    context.alerter = sender
    try:
        previous = getattr(context, "_shutdown_request", None)
        if previous and previous["source"] not in OPERATOR_SOURCES:
            send_exit_page(context, previous)
        else:
            request_shutdown(context, source, error)
    finally:
        context.alerter = original
        sender.close()
        log_exit(context)
