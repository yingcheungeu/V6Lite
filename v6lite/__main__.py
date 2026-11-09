"""V6-Lite command line.

    python -m v6lite run      --config v6lite.toml [--dry-run] [--live]
    python -m v6lite status   --config v6lite.toml
    python -m v6lite kill     --config v6lite.toml --reason "..."
    python -m v6lite resume   --config v6lite.toml --operator John --reason "..."
    python -m v6lite ignore   --config v6lite.toml SYMBOL --operator John [--dry-run]
    python -m v6lite clear-anomaly --config v6lite.toml CODE --operator John --reason "..."
    python -m v6lite approve-live  --config v6lite_live.toml --operator John --days 14 --note "..."
    python -m v6lite revoke-live   --config v6lite_live.toml
    python -m v6lite report   --config v6lite.toml [--day YYYY-MM-DD]
    python -m v6lite resolve-intent --config v6lite.toml INTENT --as cancelled --operator John --reason "..."
    python -m v6lite release  --config v6lite.toml SYMBOL --operator John --reason "..."

kill / resume / ignore / clear-anomaly / resolve-intent / release work while
`run` is active: they write to the same SQLite state, which the engine
re-reads every tick. v1 has no `adopt` and no ledger repair: holdings
V6-Lite did not open are `ignore`d; a managed position whose quantity cannot
be explained is frozen, flattened by the operator in TWS, then `release`d
(applied only when a fresh broker query shows 0 shares and no working order).
v1 runs only on fresh state: older state files are refused, never migrated.

status and report are read-only but still refuse a state file that belongs
to another account/mode.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import signal
import sys
import uuid
from datetime import date, datetime, timezone
from functools import partial
from pathlib import Path

from v6lite import approval as approval_ops
from v6lite.approval import AccountLock, GateError, check_live_gate, revoke_approval, write_approval
from v6lite.config import ConfigError, load_config
from v6lite.exits import compute_levels
from v6lite.market_calendar import trading_day
from v6lite.report import daily_report
from v6lite.engine import StartupError
from v6lite.store import Store, StoreError
from v6lite.version import code_sha

log = logging.getLogger("v6lite")
HEALTH_STALE_SECONDS = 180
DRY_RUN_CLIENT_OFFSET = 1000
TEST_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")


def _formal_role(cfg) -> str:
    return "test" if cfg.test.enabled else "formal"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def state_path(cfg, *, dry_run: bool = False) -> Path:
    base = cfg.resolved_state_dir()
    return (base / "dryrun" if dry_run else base) / "v6lite.sqlite3"


def open_store(cfg, *, dry_run: bool = False, create: bool = True) -> Store | None:
    path = state_path(cfg, dry_run=dry_run)
    if not create and not path.exists():
        return None
    return Store(path, partial(compute_levels, cfg))


def _peek_identity(path: Path) -> dict:
    """Read the binding WITHOUT opening a Store (which could migrate or
    otherwise write to someone else's state file)."""
    import sqlite3
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT key, value FROM meta WHERE key IN "
                            "('account','mode','role','schema_version')").fetchall()
    finally:
        conn.close()
    found = dict(rows)
    return {k: found.get(k) for k in ("account", "mode", "role", "schema_version")}


def open_formal_store_checked(cfg, *, missing_ok: bool = False) -> Store | None:
    """Control commands only touch an existing state file that belongs to the
    configured account + mode (a mismatched config must not flip another
    account's kill switch or dispositions, nor show another account's state
    as if it were this one)."""
    path = state_path(cfg)
    if not path.exists():
        if missing_ok:
            return None
        raise GateError(f"no V6-Lite state at {path}; run the engine first")
    peek = _peek_identity(path)
    role = _formal_role(cfg)
    if (peek["account"], peek["mode"], peek["role"] or "formal") != (cfg.account, cfg.mode, role):
        raise GateError(f"state belongs to account={peek['account']} mode={peek['mode']} "
                        f"role={peek['role']}; config says account={cfg.account} mode={cfg.mode}")
    store = open_store(cfg, create=False)
    ident = store.identity()
    if (ident["account"], ident["mode"], ident["role"] or "formal") != (cfg.account, cfg.mode, role):
        store.close()
        raise GateError(f"state belongs to account={ident['account']} mode={ident['mode']} "
                        f"role={ident['role']}; config says account={cfg.account} mode={cfg.mode}")
    return store


def open_dry_store_checked(cfg) -> Store:
    """The rehearsal state of this account/mode (created by a first
    `run --dry-run`); never the formal one."""
    path = state_path(cfg, dry_run=True)
    if not path.exists():
        raise GateError(f"no dry-run state at {path}; start `run --dry-run` once first")
    peek = _peek_identity(path)
    if (peek["account"], peek["mode"], peek["role"]) != (cfg.account, cfg.mode, "dryrun"):
        raise GateError(f"dry-run state belongs to account={peek['account']} mode={peek['mode']} "
                        f"role={peek['role']}")
    return open_store(cfg, dry_run=True, create=False)


def setup_logging(cfg, *, dry_run: bool = False) -> None:
    log_dir = cfg.resolved_state_dir() / ("dryrun/logs" if dry_run else "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    from v6lite.telemetry import AccountFormatter
    fmt = AccountFormatter("%(asctime)s %(levelname)s %(name)s %(message)s", cfg.account)
    handlers = [logging.StreamHandler(sys.stdout),
                logging.FileHandler(log_dir / f"v6lite_{_now():%Y%m%d}.log", encoding="utf-8")]
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    from v6lite.execution_time import install_execution_log_filter
    install_execution_log_filter()
    logging.getLogger("ib_insync.wrapper").setLevel(logging.WARNING)
    for handler in handlers:
        handler.setFormatter(fmt)
        root.addHandler(handler)


def _seed_dry_run_dispositions(cfg, dry_store: Store) -> None:
    """Copy ignore decisions from the formal state (read-only) so a
    rehearsal sees the same account picture without touching real state."""
    formal = state_path(cfg)
    if not formal.exists():
        return
    import sqlite3
    conn = sqlite3.connect(f"file:{formal.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT key, value FROM controls WHERE key LIKE 'disposition:%' "
                            "AND value='ignore'").fetchall()
    finally:
        conn.close()
    now = _now()
    with dry_store.tx():
        for key, value in rows:
            dry_store.set_control(now, key, value)


def cmd_run(args, cfg) -> int:
    context = {}
    try:
        return _cmd_run(args, cfg, context)
    except KeyboardInterrupt:
        return 0  # operator interrupted preflight, before takeover
    except BaseException as exc:
        engine = context.get("engine")
        if engine is None or getattr(engine, "_shutdown_request", None) is None:
            from v6lite.lifecycle import startup_exit
            startup_exit(cfg, exc, now=_now, engine=engine)
        if isinstance(exc, SystemExit):
            raise SystemExit(1) from exc
        raise


def _cmd_run(args, cfg, context) -> int:
    from v6lite.alerts import Alerter, build_channels
    from v6lite.broker.ibkr import IbkrBroker
    from v6lite.clock import SystemClock
    from v6lite.engine import Engine

    if args.dry_run and args.live:
        raise GateError("--dry-run and --live are mutually exclusive")
    if cfg.market_data_type != 1 and not args.dry_run:
        raise ConfigError("delayed market data (market_data_type=3) requires --dry-run; "
                          "paper formal trading requires real-time data (market_data_type=1)")
    startup_sha = code_sha()
    # identity of an existing state file is checked read-only BEFORE it is
    # opened (opening can migrate it) and before any broker connection
    preflight_state(cfg, dry_run=args.dry_run)
    try:
        old_v6_pids = approval_ops.old_v6_processes()
        old_v6_check_unknown = False
    except Exception as exc:
        # Process command lines can be inaccessible on Windows. A formal
        # session cannot prove isolation and therefore must fail before any
        # state/log write or broker connection; a rehearsal pages the operator.
        # Extract only the PID from the gate's diagnostic: an OS exception may
        # include private command-line text and must not reach a remote page.
        pid_match = re.search(r"\bprocess\s+(\d+)\b", str(exc))
        unknown_pid = pid_match.group(1) if pid_match else None
        unknown_detail = (f"旧 V6 进程状态无法核实（PID {unknown_pid}）：确认 PID {unknown_pid} 的用途，"
                          "或关闭该进程后重试" if unknown_pid else
                          "旧 V6 进程状态无法核实（PID 未知）：确认无法读取的 Python 进程，或关闭后重试")
        if not args.dry_run:
            raise GateError(unknown_detail) from None
        old_v6_pids = ()
        old_v6_check_unknown = True
    if old_v6_pids and not args.dry_run:
        raise GateError("old V6 process running (PID " + ", ".join(map(str, old_v6_pids)) +
                        "); stop it before formal V6-Lite startup")
    setup_logging(cfg, dry_run=args.dry_run)
    approval = check_live_gate(cfg, live_flag=args.live, now=_now())
    lock = AccountLock(f"{cfg.account}-dryrun" if args.dry_run else cfg.account)
    lock.acquire(() if args.dry_run else cfg.foreign_lock_paths)
    store = None
    alerter = None
    engine = None
    try:
        store = open_store(cfg, dry_run=args.dry_run)
        if args.dry_run:
            _seed_dry_run_dispositions(cfg, store)
        client_id = cfg.client_id + DRY_RUN_CLIENT_OFFSET if args.dry_run else cfg.client_id
        broker = IbkrBroker(cfg, read_only=args.dry_run, client_id=client_id)
        alert_prefix = f"[V6-Lite {'TEST ' if cfg.test.enabled else ''}{cfg.mode}{' dry-run' if args.dry_run else ''}"
        if args.dry_run and cfg.market_data_type == 3:
            alert_prefix += " DELAYED"
        alerter = Alerter(build_channels(cfg.alerts), prefix=alert_prefix + "]")
        if old_v6_pids or old_v6_check_unknown:
            detail = ("旧 V6 进程运行中（PID " + ", ".join(map(str, old_v6_pids)) +
                      "）；确认这些进程用途，关闭旧 V6 后重试" if old_v6_pids else unknown_detail)
            alerter.send(_now(), "P1", "旧 V6 运行隔离需复核", detail, dedupe=False)
        checker = (lambda now: check_live_gate(cfg, live_flag=True, now=now)) if cfg.is_live else None
        engine = Engine(cfg, broker, store, alerter, SystemClock(), dry_run=args.dry_run,
                        report_dir=state_path(cfg, dry_run=args.dry_run).parent / "reports",
                         approval_checker=checker, startup_code_sha=startup_sha,
                         old_v6_pids=old_v6_pids, old_v6_check_unknown=old_v6_check_unknown)
        context["engine"] = engine
        stop = {"flag": False, "source": "SIGINT"}

        def _request_stop(signum, *_):
            stop["flag"] = True
            stop["source"] = "SIGINT" if signum == signal.SIGINT else "SIGBREAK"

        signal.signal(signal.SIGINT, _request_stop)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _request_stop)
        log.warning("V6-Lite starting mode=%s dry_run=%s account=%s client_id=%s fingerprint=%s code_sha=%s approval=%s",
                    cfg.mode, args.dry_run, cfg.account, client_id, cfg.fingerprint()[:16],
                    startup_sha, (approval or {}).get("expires_at", "n/a"))
        run_engine(engine, broker, should_stop=lambda: stop["flag"], stop_source=lambda: stop["source"])
    except Exception:
        # run_engine has already paged before safe shutdown. Runtime failures
        # must never be mislabeled as a Gateway startup refusal by main().
        if engine is None or (not engine._started and not engine.took_over):
            raise
        return 1
    finally:
        # Keep alert delivery alive until all other cleanup has succeeded.
        # A cleanup failure after Ctrl+C is an internal error, not a normal
        # operator exit, and must upgrade the provenance before closing alerts.
        cleanup_error = None
        for cleanup in ((store.close if store is not None else None), lock.release):
            if cleanup is None:
                continue
            try:
                cleanup()
            except BaseException as exc:
                cleanup_error = exc
                if engine is not None:
                    engine.request_shutdown("fatal_error", exc)
        try:
            if alerter is not None:
                alerter.close(timeout=30 if getattr(engine, "_shutdown_request", {}).get("source")
                              not in {None, "SIGINT", "SIGBREAK", "operator", "kill"} else 5)
                request = getattr(engine, "_shutdown_request", None)
                if (request and request["source"] not in {"SIGINT", "SIGBREAK", "operator", "kill"}
                        and alerter.channels and
                        alerter.delivery(getattr(engine, "_exit_alert_delivery", None)) is not True):
                    from v6lite.lifecycle import startup_exit
                    startup_exit(cfg, RuntimeError("exit P0 delivery incomplete"), now=_now,
                                 source="fatal_error", engine=engine)
        except BaseException as exc:
            cleanup_error = exc
            # close() may already have stopped the worker. A fresh synchronous
            # sender is the only reliable best-effort fallback in this case.
            from v6lite.lifecycle import startup_exit
            startup_exit(cfg, exc, now=_now, source="fatal_error", engine=engine)
        finally:
            if engine is not None:
                from v6lite.lifecycle import log_exit
                log_exit(engine)
        if cleanup_error is not None:
            raise RuntimeError("engine cleanup failed") from cleanup_error

    return 0


def preflight_state(cfg, *, dry_run: bool) -> None:
    path = state_path(cfg, dry_run=dry_run)
    if not path.exists():
        return
    try:
        peek = _peek_identity(path)
    except Exception as exc:
        raise GateError(f"cannot read the identity of {path} ({exc}); refusing to open it") from exc
    role = "dryrun" if dry_run else _formal_role(cfg)
    from v6lite.store import SCHEMA_VERSION
    if peek["schema_version"] not in (None, SCHEMA_VERSION):
        raise GateError(f"state {path} is schema {peek['schema_version']}; this version runs only on fresh state "
                        f"(schema {SCHEMA_VERSION}). Archive that state directory and start fresh. Nothing was "
                        "opened, migrated or connected.")
    if peek["account"] is not None and \
            (peek["account"], peek["mode"], peek["role"] or "formal") != (cfg.account, cfg.mode, role):
        raise GateError(f"state {path} belongs to account={peek['account']} mode={peek['mode']} "
                        f"role={peek['role'] or 'formal'}; config says account={cfg.account} mode={cfg.mode} "
                        f"role={role}. Nothing was opened, migrated or connected.")


def run_engine(engine, broker, *, should_stop, stop_source=lambda: "SIGINT") -> int:
    """Own the runtime exit boundary; transport loss is recoverable.

    A failed startup before takeover only disconnects; it must never enter
    order-cancelling shutdown against an unverified account/state identity.
    """
    from v6lite.lifecycle import log_exit, transport_error
    started = False
    source, error = "SIGINT", None
    try:
        engine.startup()
        started = True
        engine._runtime_loop_active = True
        while not should_stop():
            # Each phase has the same transport boundary. In particular an
            # exception during recovery must not escape an except-body tick.
            for action in (lambda: broker.pump(1.0), engine.process_protection_priority, engine.tick):
                try:
                    action()
                except OSError as exc:
                    if not transport_error(exc):
                        raise
                    if broker.is_connected():
                        # A reset can precede the adapter's connectivity flag.
                        # Retire that socket; do not trust stale API-ready state.
                        broker.disconnect()
                    engine.connection_lost(exc)
                if not broker.is_connected():
                    engine.connection_lost()
        source = stop_source()
    except KeyboardInterrupt:
        source = "SIGINT"
    except BaseException as exc:
        source = "config" if isinstance(exc, (ConfigError, GateError, StartupError)) else "fatal_error"
        error = exc
        engine.request_shutdown(source, exc)
        if isinstance(exc, SystemExit):
            raise RuntimeError("unexpected SystemExit in engine") from exc
        raise
    finally:
        engine._runtime_loop_active = False
        engine.request_shutdown(source, error)
        try:
            if started or getattr(engine, "took_over", False):
                engine.shutdown(source=source, error=error)
            else:
                broker.disconnect()
        except BaseException as exc:
            engine.request_shutdown("fatal_error", exc)
            try:
                broker.disconnect()
            except Exception as disconnect_error:
                log.error("disconnect after shutdown failure (%s)", type(disconnect_error).__name__)
            raise
        finally:
            log_exit(engine)
    return 0


def cmd_status(args, cfg) -> int:
    store = open_formal_store_checked(cfg, missing_ok=True)
    if store is None:
        print(json.dumps({"mode": cfg.mode, "account": cfg.account, "state": "none",
                          "note": f"no V6-Lite state at {state_path(cfg)}"}, indent=2))
        return 0
    day = trading_day(_now()).isoformat()
    risk = store.risk_day(day)
    health_raw = store.control("health")
    health = json.loads(health_raw) if health_raw else None
    if health:
        age = (_now() - datetime.fromisoformat(health["at"])).total_seconds()
        health["age_seconds"] = round(age)
        health["freshness"] = "fresh" if age <= HEALTH_STALE_SECONDS else "STALE (engine not running?)"
    out = {
        "mode": cfg.mode,
        "identity": store.identity(),
        "config_fingerprint": cfg.fingerprint()[:16],
        "engine_health": health or "unknown (engine has not reported)",
        "kill": store.kill_state(),
        "anomalies": store.anomalies(),
        "dispositions": store.dispositions(),
        "cooldown_until": store.control("cooldown_until"),
        "loss_streak": store.control("loss_streak"),
        "risk_day": dict(risk) if risk else None,
        "realized_today": store.realized_for_day(day),
        "fees_today": store.fees_for_day(day),
        "open_positions": [dict(r) for r in store.open_positions()],
        "active_orders": [dict(r) for r in store.active_intents()],
    }
    print(json.dumps(out, indent=2, default=str))
    store.close()
    return 0


def cmd_resume_tp(args, store: Store) -> None:
    from v6lite.tp_evidence import valid_resume_binding
    if not all(isinstance(getattr(args, field, None), str) and getattr(args, field).strip()
               for field in ("symbol", "position_id", "pause_id", "operator", "reason")):
        raise StoreError("resume-tp requires nonempty position, symbol, pause episode, operator and reason")
    symbol = args.symbol.strip().upper()
    pid = args.position_id
    pos, row = store.position(pid), store.protection(pid)
    raw = store.control(f"tp_pause:{pid}")
    try:
        pause = json.loads(raw) if raw else None
    except (ValueError, TypeError):
        raise StoreError("TP pause is invalid; manual verification required")
    now = _now()
    request = {"symbol": symbol, "position_id": pid, "pause_id": args.pause_id,
               "operator": args.operator, "reason": args.reason, "at": now.isoformat()}
    if not valid_resume_binding(request, pause, pos, row, pid) or pos["status"] != "OPEN":
        raise StoreError("resume-tp position or pause episode does not match")
    with store.tx():
        store.set_control(now, f"resume_tp:{pid}", json.dumps(request))
        store.audit(now, "TP_RESUME_REQUESTED", **request)


def cmd_release(args, store: Store) -> None:
    """Queue a release: after the operator flattened a FROZEN (or any
    managed) position in TWS, close it in the ledger. The engine applies it
    only if a fresh broker query shows 0 shares, no working order of any
    origin for the symbol and no fill in the last minute; it then freezes
    new entries for the rest of the day, in the same transaction."""
    if not args.reason.strip() or not args.operator.strip():
        raise StoreError("release requires --operator and --reason")
    request = {"operator": args.operator, "reason": args.reason}
    now = _now()
    with store.tx():
        store.set_control(now, f"release:{args.symbol.upper()}", json.dumps(request))
        store.audit(now, "RELEASE_REQUESTED", symbol=args.symbol.upper(), **request)


def cmd_test_entry(args, cfg) -> None:
    """Queue exactly one test entry for the next engine tick. An existing,
    bound test state is required; the CLI never starts a broker connection."""
    if not cfg.test.enabled:
        raise GateError("test-entry requires enabled paper test mode")
    symbol = args.symbol.strip().upper()
    if not TEST_SYMBOL_RE.fullmatch(symbol) or symbol not in cfg.tradable_symbols:
        raise GateError("test-entry requires a configured tradable symbol")
    operator = args.operator.strip()
    if not operator:
        raise GateError("test-entry requires a nonempty --operator")
    store = open_formal_store_checked(cfg)
    try:
        now = _now()
        request_id = uuid.uuid4().hex
        with store.tx():
            epoch = store.control("test_entry_ready")
            if not epoch:
                raise GateError("test-entry requires a connected, ready test engine")
            store.set_control(now, f"test_entry:{request_id}",
                              json.dumps({"symbol": symbol, "operator": operator, "at": now.isoformat(),
                                          "epoch": epoch}))
            store.audit(now, "TEST_ENTRY_REQUESTED", request_id=request_id, symbol=symbol, operator=operator)
    finally:
        store.close()
    print(f"TEST entry requested: {symbol}; engine will evaluate it on its next tick")


def cmd_test_tp_timeout(args, cfg) -> None:
    """Queue one bounded Paper test request; this command never contacts a broker."""
    from v6lite.test_tp_timeout import PREFIX, mode_refusal, admission_refusal
    refusal = mode_refusal(cfg, dry_run=bool(getattr(args, "dry_run", False)))
    symbol, operator = args.symbol.strip().upper(), args.operator.strip()
    if refusal:
        raise GateError("test-tp-timeout " + refusal)
    if not TEST_SYMBOL_RE.fullmatch(symbol) or symbol not in cfg.tradable_symbols:
        raise GateError("test-tp-timeout requires a configured tradable symbol")
    if not operator:
        raise GateError("test-tp-timeout requires a nonempty --operator")
    store = open_formal_store_checked(cfg)
    try:
        now = _now()
        with store.tx():
            epoch = store.control("test_entry_ready")
            refusal, pos, row = admission_refusal(store, symbol)
            if str(store.identity()["client_id"]) != "132":
                refusal = "state client must be 132"
            if not epoch:
                refusal = "requires a connected ready engine epoch"
            if store.control(PREFIX+symbol) is not None:
                refusal = "pending timeout request already exists"
            request_id = uuid.uuid4().hex
            if refusal is None:
                store.set_control(now, PREFIX+symbol, json.dumps({"version":29,
                    "request_id":request_id, "symbol":symbol, "operator":operator,
                    "position_id":pos["position_id"], "protection_intent_id":row["intent_id"],
                    "at":now.isoformat(), "epoch":epoch}))
            store.audit(now, "TEST_TP_TIMEOUT_RESULT", phase="requested" if refusal is None else "refused",
                outcome="requested" if refusal is None else "refused:"+refusal,
                request_id=request_id, symbol=symbol, operator=operator)
        if refusal:
            raise GateError("test-tp-timeout " + refusal)
    finally:
        store.close()
    print(f"TEST TP timeout requested: {symbol}; expires in 5 minutes; final stage excluded")


def cmd_resolve_intent(args, store: Store) -> None:
    """Operator ruling for an UNKNOWN order the broker can no longer prove
    (e.g. from a previous session). Only 'cancelled' is accepted and it only
    rules on the ORDER: if the broker quantity then differs, the position is
    frozen (flatten it in TWS, then `release`); unmanaged holdings: `ignore`."""
    from v6lite.models import ACTIVE_STATUSES, IntentStatus
    now = _now()
    with store.tx():
        intent = store.intent(args.intent_id)
        if intent is None:
            raise StoreError(f"no intent {args.intent_id}")
        held = bool(intent["hold_reason"]) and intent["status"] in {str(s) for s in ACTIVE_STATUSES}
        if intent["status"] != IntentStatus.UNKNOWN and not held:
            raise StoreError(f"intent is {intent['status']}, only UNKNOWN or held intents can be resolved")
        # one statement, conditional on the state just checked: a concurrent
        # engine write (e.g. FILLED from evidence) is never overwritten
        cur = store.conn.execute(
            "UPDATE intents SET status=?, note=?, updated_at=? WHERE intent_id=? AND status=?",
            (str(IntentStatus.CANCELLED), f"operator:{args.operator}:{args.reason}"[:200], now.isoformat(),
             args.intent_id, intent["status"]))
        if cur.rowcount != 1:
            raise StoreError("intent changed while resolving; check status and retry")
        store.audit(now, "INTENT_RESOLVED_BY_OPERATOR", intent_id=args.intent_id, operator=args.operator,
                    reason=args.reason, previous=intent["status"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="v6lite")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add(name, **kw):
        p = sub.add_parser(name, **kw)
        p.add_argument("--config", required=True)
        return p

    run = add("run")
    run.add_argument("--dry-run", action="store_true", help="read-only IBKR session; log decisions only")
    run.add_argument("--live", action="store_true", help="required (with approval) for a live config")
    add("status")
    kill = add("kill")
    kill.add_argument("--reason", required=True)
    resume = add("resume")
    resume.add_argument("--operator", required=True)
    resume.add_argument("--reason", required=True)
    resume_tp = add("resume-tp")
    resume_tp.add_argument("symbol")
    resume_tp.add_argument("--position-id", required=True)
    resume_tp.add_argument("--pause-id", required=True)
    resume_tp.add_argument("--operator", required=True)
    resume_tp.add_argument("--reason", required=True)
    ignore = add("ignore")
    ignore.add_argument("symbol")
    ignore.add_argument("--operator", required=True)
    ignore.add_argument("--dry-run", action="store_true",
                        help="mark the holding in the rehearsal (dry-run) state only")
    clear = add("clear-anomaly")
    clear.add_argument("code")
    clear.add_argument("--operator", required=True)
    clear.add_argument("--reason", required=True)
    approve = add("approve-live")
    approve.add_argument("--operator", required=True)
    approve.add_argument("--days", type=int, default=14)
    approve.add_argument("--note", default="")
    add("revoke-live")
    report = add("report")
    report.add_argument("--day", default=None)
    resolve = add("resolve-intent")
    resolve.add_argument("intent_id")
    resolve.add_argument("--as", dest="as_status", choices=["cancelled"], required=True)
    resolve.add_argument("--operator", required=True)
    resolve.add_argument("--reason", required=True)
    release = add("release")
    release.add_argument("symbol")
    release.add_argument("--operator", required=True)
    release.add_argument("--reason", required=True)
    test_entry = add("test-entry")
    test_entry.add_argument("symbol")
    test_entry.add_argument("--operator", required=True)
    test_tp_timeout = add("test-tp-timeout")
    test_tp_timeout.add_argument("symbol")
    test_tp_timeout.add_argument("--operator", required=True)

    args = parser.parse_args(argv)
    from v6lite.lifecycle import safe_reason
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {safe_reason(exc, None) if args.cmd == 'run' else str(exc)}", file=sys.stderr)
        if args.cmd == "run":
            from v6lite.lifecycle import safe_reason
            log.error("!!! V6-Lite 引擎启动失败 source=config reason=%s !!!", safe_reason(exc, None))
        return 2
    except FileNotFoundError as exc:
        print(f"CONFIG ERROR: config file not found: {Path(args.config).resolve()}\n"
              "  first time: Copy-Item v6lite\\examples\\paper.toml v6lite.paper.toml (see v6lite\\README.md)",
              file=sys.stderr)
        if args.cmd == "run":
            from v6lite.lifecycle import safe_reason
            log.error("!!! V6-Lite 引擎启动失败 source=config reason=%s !!!", safe_reason(exc, None))
        return 2
    except (OSError, UnicodeDecodeError, ValueError) as exc:   # unreadable / not valid TOML
        print(f"CONFIG ERROR: cannot read {args.config}: {safe_reason(exc, None) if args.cmd == 'run' else str(exc)}", file=sys.stderr)
        if args.cmd == "run":
            from v6lite.lifecycle import safe_reason
            log.error("!!! V6-Lite 引擎启动失败 source=config reason=%s !!!", safe_reason(exc, None))
        return 2
    try:
        if args.cmd == "run":
            return cmd_run(args, cfg)
        if args.cmd == "status":
            return cmd_status(args, cfg)
        if args.cmd == "approve-live":
            path = write_approval(cfg, operator=args.operator, days=args.days, note=args.note, now=_now())
            print(f"live approval written: {path}\nfingerprint {cfg.fingerprint()}")
            return 0
        if args.cmd == "revoke-live":
            print("revoked" if revoke_approval(cfg) else "no approval present")
            return 0
        if args.cmd == "report":
            store = open_formal_store_checked(cfg)
            day = date.fromisoformat(args.day) if args.day else trading_day(_now())
            from v6lite.report import write_daily_report
            path,text=write_daily_report(store,cfg,day,state_path(cfg).parent/'reports')
            print(text)
            print(f"Report written: {path}",file=sys.stderr)
            store.close()
            return 0
        if args.cmd == "test-entry":
            cmd_test_entry(args, cfg)
            return 0
        if args.cmd == "test-tp-timeout":
            cmd_test_tp_timeout(args, cfg)
            return 0
        if args.cmd == "ignore" and args.dry_run:
            store = open_dry_store_checked(cfg)
            try:
                store.set_disposition(_now(), args.symbol, "ignore", args.operator)
            finally:
                store.close()
            print(f"{args.symbol.upper()} -> ignore in the dry-run state only")
            return 0
        store = open_formal_store_checked(cfg)
        try:
            if args.cmd == "kill":
                store.kill(_now(), args.reason)
                print("kill switch ACTIVE: new entries blocked; exits continue")
            elif args.cmd == "resume":
                store.resume(_now(), args.reason, args.operator)
                print("kill switch cleared")
            elif args.cmd == "resume-tp":
                cmd_resume_tp(args, store)
                print("TP resume queued; complete broker protection evidence is required; no immediate TP")
            elif args.cmd == "ignore":
                store.set_disposition(_now(), args.symbol, args.cmd, args.operator)
                print(f"{args.symbol.upper()} -> {args.cmd} (applied at next reconcile)")
            elif args.cmd == "clear-anomaly":
                store.clear_anomaly(_now(), args.code, args.operator, args.reason)
                print(f"anomaly {args.code} cleared")
            elif args.cmd == "resolve-intent":
                cmd_resolve_intent(args, store)
                print(f"intent {args.intent_id} resolved as cancelled by operator")
            elif args.cmd == "release":
                cmd_release(args, store)
                print(f"release of {args.symbol.upper()} queued; applied only when the broker shows 0 shares and "
                      "no working order for it")
        finally:
            store.close()
        return 0
    except ConfigError as exc:
        print(f"CONFIG ERROR: {safe_reason(exc, None) if args.cmd == 'run' else str(exc)}", file=sys.stderr)
        if args.cmd == "run":
            from v6lite.lifecycle import safe_reason
            log.error("!!! V6-Lite 引擎启动失败 source=config reason=%s !!!", safe_reason(exc, None))
        return 2
    except (GateError, StoreError, ValueError, StartupError) as exc:
        print(f"REFUSED: {safe_reason(exc, None) if args.cmd == 'run' else str(exc)}", file=sys.stderr)
        if args.cmd == "run":
            from v6lite.lifecycle import safe_reason
            log.error("!!! V6-Lite 引擎启动失败 source=config reason=%s !!!", safe_reason(exc, None))
        return 2
    except (ConnectionRefusedError, TimeoutError, ConnectionError) as exc:
        # nothing was started: the Gateway is not reachable (not running, not
        # logged in, API disabled or another port) -- one line, no traceback
        print(f"CANNOT CONNECT: IB Gateway at {cfg.host}:{cfg.port} is not reachable ({exc.__class__.__name__}).\n"
              "  Check: Gateway running and logged in to the right account; Configure > API > Settings: "
              "socket clients enabled, port matches, 127.0.0.1 trusted.", file=sys.stderr)
        if args.cmd == "run":
            from v6lite.lifecycle import safe_reason
            log.error("!!! V6-Lite 引擎启动失败 source=fatal_error reason=%s !!!", safe_reason(exc, cfg.account))
        return 3
    except Exception as exc:
        if args.cmd != "run":
            raise
        reason = safe_reason(exc.__cause__ or exc, cfg.account)
        log.error("!!! V6-Lite 引擎已退出 source=fatal_error reason=%s !!!", reason)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
