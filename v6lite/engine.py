"""V6-Lite engine: one thread owns the broker and the ledger.

tick() order (every ~1s):
  1. drain broker events (fills -> exactly-once ledger, statuses -> hints)
  2. connection maintenance / reconnect + recovery
  3. equity, approval and alert-health refresh
  4. resolve in-flight intents (timeouts, cancel grace, evidence-based settlement)
  5. broker-truth reconciliation (periodic, after fills, after reconnect)
  6. daily loss evaluation (derived from the ledger vs the prior close)
  7. exit evaluation for open positions (always allowed, even when killed)
  8. scheduled scans -> entries
  9. loss-streak state, end-of-day report delivery, health snapshot

Slow broker calls are budgeted: before any blocking request the engine
checks whether the next exit check would fall due while it waits, and if so
runs the exit check first (`_protect`). The final ENTRY boundary re-reads
events, daily loss, alert health, approval and funds immediately before the
order is sent.

Broker writes exist in exactly two methods (`_send` -> submit_limit,
`_request_cancel` -> cancel). Both refuse in dry-run.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
import uuid
from collections import OrderedDict
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from v6lite.alerts import Alerter, AlertCard, INFO_GREEN, P0_RED, P1_ORANGE, P2_YELLOW
from v6lite.broker.base import BrokerError, BrokerPort
from v6lite.config import LiteConfig
from v6lite.commissions import CommissionMixin, fee_breakdown, freeze_estimate, LATCH
from v6lite.exits import breakeven_stop, buy_limit, decide_exit, sell_limit
from v6lite.market_calendar import ET, et_time_on, is_rth, is_trading_day, session_for, trading_day
from v6lite.models import (
    OpenOrdersNormalizationEvent, AnomalyEvent, BrokerNoticeEvent, CommissionEvent, ConnectionEvent, FillEvent, IntentStatus, OpenOrder, OrderRequest, Purpose,
    Side, Signal, StatusEvent, CompletedOrdersSnapshot, PriceObservationEvent, OrderBookChangeEvent, ExecutionTimeDiagnosticEvent, TERMINAL_STATUSES,
)
from v6lite.protection_engine import ProtectionMixin
from v6lite.tp_evidence import TP_PURPOSES, price_rejection
from v6lite.tp_start import TpStartMixin
from v6lite.report import execution_label
from v6lite.reconcile import reconcile
from v6lite.risk import (
    EXIT_QUOTE_AGE_MULTIPLIER, RuntimeFlags, check_quote, check_symbol_entry, daily_loss_limit, day_pnl,
    global_entry_blocks, loss_state, trade_net,
)
from v6lite.sizing import SizeResult, estimate_fee, size_entry, sizing_equity
from v6lite.store import Store, StoreError, new_id
from v6lite.release_evidence import CompletedEvidenceError, completed_cancel_proof
from v6lite.strategy import effective_min_score, generate_signals, score_symbol
from v6lite.version import code_sha

log = logging.getLogger("v6lite.engine")

_CANCEL_HINTS = {"Cancelled", "ApiCancelled", "Inactive"}
TEST_ENTRY_TTL = timedelta(minutes=5)
EQUITY_REFRESH = timedelta(seconds=60)
RECONNECT_EVERY = timedelta(seconds=30)
RECONNECT_REASON_WINDOW = timedelta(minutes=5)
RECONNECT_REASON_LIMIT = 2
UNKNOWN_RETRY = timedelta(seconds=60)
CANCEL_ACK_TIMEOUT = timedelta(seconds=60)
SCAN_LATE_LIMIT = timedelta(minutes=60)
HEARTBEAT_EVERY = timedelta(minutes=10)
HEALTH_SNAPSHOT_EVERY = timedelta(seconds=60)
ALERT_RETEST_EVERY = timedelta(minutes=10)
PRIOR_CLOSE_RETRY_EVERY = timedelta(minutes=5)
REPORT_RETRY_AFTER = timedelta(minutes=15)
FILL_REFRESH_EVERY = timedelta(seconds=30)
MAX_PRICE_DRIFT_PCT = 0.03
UNPRICED_ALERT_AFTER = timedelta(minutes=2)
# IBKR checks changed account values about every three minutes; unchanged
# AvailableFunds is not a heartbeat. Probe with an independent funds snapshot
# without cancelling account updates, regardless of holdings. The existing
# FUNDS_MAX_AGE entry gate still rejects stale evidence until a real reply.
ACCOUNT_PUSH_CYCLE = timedelta(seconds=180)
FUNDS_REFRESH_AFTER = timedelta(seconds=210)
FUNDS_MAX_AGE = timedelta(seconds=270)
# the working-order evidence of an entry is taken first; between it and the
# send nothing blocks, so it is seconds old at most
ENTRY_EVIDENCE_MAX_AGE = timedelta(seconds=5)
# IBKR execution times are server time (1 s resolution) while the funds
# change time is local receive time: fills this close to it are reserved too
FUNDS_FILL_MARGIN = timedelta(seconds=30)
EXIT_REFUSED_BACKOFF = timedelta(seconds=30)
FREEZE_AFTER = timedelta(seconds=60)        # an unexplained difference this old freezes the symbol
FREEZE_EVIDENCE_GRACE = timedelta(seconds=120)
RELEASE_QUIET = timedelta(seconds=60)       # no fill on the symbol for this long before a release
BUDGET_MARGIN_SECONDS = 3                   # check interval >= largest blocking call + this
# a SELL is only sent against a broker working-order list at most this old
WORKING_MAX_AGE = timedelta(seconds=60)
MARKET_DATA_REPEAT = timedelta(hours=2)
DELAYED_SHORTAGE_AFTER = timedelta(seconds=120)
PT = ZoneInfo("America/Los_Angeles")
GATEWAY_HEARTBEAT_EVERY = timedelta(seconds=60)
GATEWAY_HEARTBEAT_TIMEOUT = timedelta(seconds=30)
GATEWAY_HEARTBEAT_MISSES = 2
GATEWAY_HEARTBEAT_START_PT = clock_time(6, 0)
GATEWAY_HEARTBEAT_AFTER_CLOSE = timedelta(minutes=30)
GATEWAY_CLOCK_SKEW_TOLERANCE = timedelta(seconds=10)
GATEWAY_CLOCK_SKEW_CONFIRMATIONS = 3
DAILY_REPORT_AFTER_CLOSE = timedelta(minutes=5)


class StartupError(RuntimeError):
    pass


def stage_left(stage_row, qty_open: int) -> int:
    """Shares a pending exit stage still has to sell. A STOP sells EVERYTHING
    still open (its recorded target can be stale: shares booked after it began
    must never be stranded below the stop); a take-profit sells its slice."""
    if Purpose(stage_row["stage"]) is Purpose.STOP:
        return int(qty_open)
    return min(stage_row["target_qty"] - stage_row["filled_qty"], qty_open)


class Engine(TpStartMixin, CommissionMixin, ProtectionMixin):
    def __init__(self, cfg: LiteConfig, broker: BrokerPort, store: Store, alerter: Alerter, clock, *,
                 dry_run: bool = False, report_dir: Path | None = None,
                 approval_checker: Callable[[datetime], object] | None = None,
                 startup_code_sha: str | None = None,
                 old_v6_pids: tuple[int, ...] = (), old_v6_check_unknown: bool = False):
        self.cfg = cfg
        self.broker = broker
        self.store = store
        self.alerter = alerter
        # Reporting uses the same clock as protection evidence and callbacks.
        # This is a process-local reference; no runtime state is written here.
        self.store.report_clock = clock
        observation_generation = getattr(broker, "set_observation_generation_handler", None)
        if callable(observation_generation):
            observation_generation(lambda generation: self.store.observation_generation(self.clock.now(), generation))
        if cfg.test.enabled and "TEST" not in alerter.prefix:
            alerter.prefix = "[V6-Lite TEST]"
        self.clock = clock
        self.dry_run = dry_run
        self.code_sha = startup_code_sha
        self.report_dir = report_dir
        self.approval_checker = approval_checker
        self.old_v6_pids = old_v6_pids
        self.old_v6_check_unknown = old_v6_check_unknown
        self.flags = RuntimeFlags()
        self._mismatched: set[str] = set()
        self._fresh_seq = 0             # count of successful fresh position reads
        self._last_account_refresh: datetime | None = None
        self._account_stream_flat_at: datetime | None = None
        self._entry_evidence_at: datetime | None = None
        self._active_test_entry_at: datetime | None = None
        self._test_entry_epoch: str | None = None
        self._reconcile_due = True
        self._reconcile_fail_streak = 0
        self._reconcile_fail_since: datetime | None = None
        self._last_fill_at: datetime | None = None
        self._unpriced_since: datetime | None = None
        self._link_down = False
        self._last_reconcile: datetime | None = None
        self._last_equity: datetime | None = None
        self._last_exit_check: datetime | None = None
        self._last_reconnect: datetime | None = None
        self._reconnect_next_at: datetime | None = None
        self._physical_loss_wait_started: datetime | None = None
        self._runtime_loop_active = False
        self._reconnect_failures = 0
        self._reconnect_reason = ""
        self._reconnect_episode = False
        self._reconnect_history: dict[str, list[datetime]] = {}
        self._reconnect_stopped = False
        self._recovery_pending = False
        self._recovery_failure_reason = ""
        self._last_heartbeat: datetime | None = None
        self._gateway_last_request: datetime | None = None
        self._gateway_pending: list[dict] = []
        self._gateway_seen_replies = 0
        self._gateway_seen_day = None
        self._gateway_miss_streak = 0
        self._gateway_paged = False
        self._gateway_stats_day = None
        self._gateway_replies = 0
        self._gateway_misses = 0
        self._gateway_max_latency = 0.0
        self._gateway_clock_skewed = False
        self._gateway_clock_paged = False
        self._gateway_clock_skew_seconds: float | None = None
        self._gateway_clock_skew_streak = 0
        self._gateway_clock_normal_streak = 0
        self._gateway_has_prior_timeout = False
        self._quote_sample_day = None
        self._quote_first_sample_at = None
        self._quote_samples = 0
        self._quote_usable_samples = 0
        self._quote_total_samples = 0
        self._subscribed_quote_symbols: tuple[str, ...] = ()
        self._last_health: datetime | None = None
        self._last_alert_test: datetime | None = None
        self._prior_close_attempt: dict[str, datetime] = {}
        self._last_fill_refresh: datetime | None = None
        self._last_execution_time_query: datetime | None = None
        self._dry_logged: set[str] = set()
        self._md_problem: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        self._md_last_page: datetime | None = None
        self._md_rth_page_day = None
        self._md_delayed_shortage_since: datetime | None = None
        self._tick_slowest_phase = "idle"
        self._tick_slowest_seconds = 0.0
        self._started = False
        self._shutting_down = False
        self._in_protection = False
        self._exit_tick_due = False   # exit check was due when this tick began
        self._in_exit_eval = False
        # slow follow-up work (exit submissions, cancels, execution refreshes):
        # decided quickly, executed one by one, each behind its own budget check
        self._queue: "OrderedDict[str, Callable[[], None]]" = OrderedDict()
        self._processing_queue = False
        self._working: dict[str, OpenOrder] = {}
        self._working_at: datetime | None = None
        self._alerted_once: set[str] = set()
        self._exit_backoff: dict[tuple[str, str], datetime] = {}
        self._mismatch_since: dict[str, datetime] = {}
        self._fresh_held: dict[str, tuple[float, datetime]] = {}
        self._protection_notice_evidence: dict[str, dict] = {}  # complete same-round display evidence
        self._protection_verification_retry: dict[str, dict] = {}
        self._protection_query_position: str | None = None
        self._protection_evidence_attempted_at: dict[str, datetime] = {}
        self._protection_reduction_proofs: dict[str, dict] = {}
        self._protection_price_requests: dict[str, tuple] = {}
        self._protection_final_cancel_proofs: dict[str, dict] = {}
        self._overnight_tp_refresh_pending: dict[str, str] = {}
        self.took_over = False        # startup reached order recovery (only then may shutdown cancel)
        self._in_urgent_callback = False
        self._protection_busy = False
        self._protection_priority_due = False
        self._protection_steady_cursor: str | None = None
        self._protection_steady_evidence_budget = False
        self._urgent_received_mono: float | None = None
        self._protection_b_status_mono: dict[str, float] = {}
        self._recovering = False
        self._protection_emergency = False
        self._protection_emergency_at: datetime | None = None
        self._protection_deadline_fired: set[tuple[str, str, str]] = set()
        query_notice = getattr(self.broker, "set_query_throttle_handler", None)
        if query_notice is not None:
            query_notice(self._protection_query_throttled)

    @property
    def role(self) -> str:
        return "dryrun" if self.dry_run else "test" if self.cfg.test.enabled else "formal"

    @property
    def _test_tag(self) -> str:
        return " test=1" if self.cfg.test.enabled else ""

    # ================================================================ startup
    def _check_budget(self) -> None:
        """The exit-check interval must fit the largest single blocking call
        (it cannot be interrupted) plus a margin; otherwise no schedule can
        keep the promised interval."""
        interval = self.cfg.exit_check_interval_seconds
        worst = max(self.broker.worst_case_seconds(k) for k in ("history", "vix", "query"))
        if worst + BUDGET_MARGIN_SECONDS > interval:
            raise StartupError(f"exit_check_interval_seconds={interval} is shorter than the largest blocking "
                               f"broker call ({worst:g}s) + {BUDGET_MARGIN_SECONDS}s margin")

    def startup(self) -> None:
        now = self.clock.now()
        if self.code_sha is None:
            self.code_sha = code_sha()
        self._check_budget()
        legacy_types = [r for r in self.store.protections() if r["order_type"] != "STP"]
        if legacy_types:
            for row in legacy_types:
                self._protection_type_allowed(now, row)
            raise StartupError("旧类型保护单，需人工处理；no automatic conversion or broker connection")
        legacy = self.store.legacy_evidence_fills()
        if legacy:
            raise StartupError(
                f"this state holds {len(legacy)} fill(s) an earlier version booked although they were evidence "
                f"only ({', '.join(legacy[:5])}); this version cannot separate them from real trades. Archive "
                "the state directory and start fresh -- nothing was changed.")
        # the state file's identity is checked BEFORE any broker connection
        try:
            bound = self.store.bind_identity(self.cfg.account, self.cfg.mode, role=self.role,
                                             client_id=self.cfg.client_id)
        except StoreError as exc:
            raise StartupError(str(exc)) from exc
        if self.cfg.test.enabled and not self.dry_run:
            self._drop_startup_test_entries(now)
        # Only an alert-only deadline callback may run before account/client
        # verification and three-source takeover.  It has no broker-write path.
        preflight_deadlines = getattr(self.broker, "set_protection_deadline_handler", None)
        if preflight_deadlines is not None and not self.dry_run:
            preflight_deadlines(lambda: self._preflight_protection_deadlines(self.clock.now()))
        try:
            self.broker.connect()
            if not self._broker_synchronized():
                raise BrokerError("broker connection synchronization incomplete at startup")
        except Exception as exc:
            # CLI startup remains fail-closed and exits; this records the
            # same continuous backoff if a caller resumes this instance.
            self._reconnect_failure(self.clock.now(), str(exc))
            raise
        accounts = self.broker.managed_accounts()
        if self.cfg.account not in accounts:
            raise StartupError(f"configured account {self.cfg.account} not in managed accounts {accounts}")
        self._check_client_identity(now, bound)
        self.flags.broker_connected = True
        self._pin_contracts(now)
        qualify_vix = getattr(self.broker, "qualify_vix", None)
        if qualify_vix is not None:
            try:
                qualify_vix()
            except Exception as exc:
                log.warning("VIX contract not qualified at startup: %s", exc)
        symbols = sorted(set(self.cfg.tradable_symbols) | {r["symbol"] for r in self.store.open_positions()})
        self.broker.subscribe_quotes(symbols)
        self._subscribed_quote_symbols = tuple(symbols)
        self._refresh_equity(now, force=True)
        self._refresh_approval(now)
        recovered = True
        if not self.dry_run:
            self.took_over = True
            recovered = self._recover(now)
            self._protection_progress(self.clock.now())
        self._reconcile(now)
        if not recovered:
            self._recovery_pending = True
            self.flags.reconcile_ok = False
            self._reconnect_failure(self.clock.now(), self._recovery_failure_reason or
                                    "order recovery evidence incomplete at startup")
        elif self.flags.reconcile_ok:
            self._recovery_pending = False
            self._recovery_failure_reason = ""
        # Handshake callbacks are queued until account/client/conId and
        # recovery evidence have been checked.  They cannot submit a SELL
        # from an unverified startup session.
        urgent = getattr(self.broker, "set_urgent_handler", None)
        if urgent is not None and not self.dry_run:
            urgent(self._on_urgent_broker_event)
        deadlines = getattr(self.broker, "set_protection_deadline_handler", None)
        if deadlines is not None and not self.dry_run:
            deadlines(lambda: self._hard_protection_deadlines(self.clock.now()))
        for key in self.store.controls_with_prefix("close_card_pending:"):
            self._arm_close_card_deadline(self.clock.now(), key.split(":", 1)[1])
        self.flags.alerts_healthy = self.alerter.self_test(now)
        self._last_alert_test = now
        self._check_market_data(now, startup=True)
        summary = self.status_snapshot(now)
        self.store.audit(now, "STARTUP", **summary)
        log.info("[STARTUP_SUMMARY] %s", json.dumps(summary, default=str))
        self.alerter.send(now, "INFO", "started", self._startup_remote_message(now, summary),
                          card=self._startup_card(now, summary))
        if self.cfg.test.enabled and not self.dry_run:
            self._publish_test_entry_ready(self.clock.now())
        self._started = True

    def _startup_card(self, now: datetime, summary: dict) -> AlertCard:
        """Chinese six-field phone card; the STARTUP audit/log remains complete."""
        pt, et = now.astimezone(PT), now.astimezone(ET)
        if self.old_v6_check_unknown:
            old_v6 = "旧 V6：无法核实"
        elif self.old_v6_pids:
            old_v6 = "旧 V6：运行中（PID " + ", ".join(map(str, self.old_v6_pids)) + "）"
        else:
            old_v6 = "旧 V6：未运行"
        data_mode = "延迟行情" if self.cfg.market_data_type == 3 else "实时行情"
        mode = "Live" if self.cfg.is_live else "Paper"
        rehearsal = execution_label(self.cfg.mode, dry_run=self.dry_run)
        md = summary.get("market_data") or {}
        total, usable = md.get("quote_symbols", 0), md.get("usable_quotes", 0)
        normal_startup_blocks = {"outside_entry_window", "risk_day_not_initialized"}
        problems = [str(block) for block in summary.get("entry_blocks", ())
                    if block not in normal_startup_blocks]
        if not summary.get("broker_connected"):
            problems.append("IBKR 未连接")
        if not summary.get("reconcile_ok"):
            problems.append("持仓对账不一致")
        if not summary.get("alerts_healthy"):
            problems.append("告警通道异常")
        if md.get("error"):
            problems.append("行情状态无法读取")
        if total and usable < total:
            problems.append(f"报价可用 {usable}/{total}")
        if self.old_v6_pids or self.old_v6_check_unknown:
            problems.append(old_v6)
        status_lines = ["⚠️ 需关注" if problems else "✅ 正常"]
        status_lines.append(" · ".join((
            "IBKR 已连接" if summary.get("broker_connected") else "IBKR 未连接",
            "对账一致" if summary.get("reconcile_ok") else "对账异常",
            "告警通道正常" if summary.get("alerts_healthy") else "告警通道异常",
            old_v6)))
        status_lines.extend(problems[:5])
        if any(str(block).startswith("daily_loss_baseline_unknown:prior_close:")
               for block in summary.get("entry_blocks", ())):
            status_lines.append("前收盘价基准暂缺：每 5 分钟自动重试历史查询，取得有效前一交易日收盘价后恢复；"
                                "恢复时间取决于券商回报。此前新入场保持阻断；退出与原保护核验继续，"
                                "保护状态仍须独立核验。")
        if len(problems) > 5:
            status_lines.append(f"另有 {len(problems)-5} 项异常，详见日志")
        if "outside_entry_window" in summary.get("entry_blocks", ()):
            start = et_time_on(trading_day(now), self.cfg.entry_window_start_et).astimezone(ET)
            status_lines.append(f"入场窗口 {start:%H:%M} ET 开启" if now < start
                                else "当前不在入场窗口")
        status_lines.append(f"报价可用 {usable}/{total} · V6-Lite 持仓：" +
                            (", ".join(f"{p[0]} {p[1]}" for p in summary.get("open_positions") or []) or "无"))
        ignored = sorted(s for s, disposition in self.store.dispositions().items() if disposition == "ignore")
        if ignored:
            status_lines.append("已忽略的持仓：" + " ".join(ignored))
        session = session_for(trading_day(now))
        report_at = session.close_utc + DAILY_REPORT_AFTER_CLOSE if session else None
        report_time = report_at.astimezone(PT).strftime("%H:%M PT") if report_at else "下一交易日收盘后"
        scans = " · ".join(f"{slot} ET / {et_time_on(trading_day(now), slot).astimezone(PT):%H:%M} PT"
                           for slot in self.cfg.scan_times_et)
        commands = ("这些指令只能在本机命令行执行，不能在 Discord 输入。\n"
                    "status 查看状态 · report 查看日报\n"
                    "kill --reason 停止入场 · resume 恢复\n"
                    "ignore 标的 · release 标的 · resolve-intent ID · clear-anomaly 代码")
        if self.cfg.test.enabled:
            commands += "\ntest-entry 标的 --operator 姓名（下个 tick 完整门控）"
        if self.cfg.is_live:
            commands += "\napprove-live 审批实盘 · revoke-live 撤销审批"
        mode_line = f"{mode} · {rehearsal} · {data_mode} · {self.cfg.account} · 版本 {str(self.code_sha or 'unknown')[:8]}"
        if self.cfg.test.enabled:
            test = self.cfg.test
            mode_line = (f"🧪 测试模式（仅 Paper） · {rehearsal} · {data_mode} · {self.cfg.account} · "
                         f"qty={test.qty}，stop={test.stop_mult} ATR，TP1/2/3="
                         f"{test.tp1_mult}/{test.tp2_mult}/{test.tp3_mult} ATR · "
                         f"版本 {str(self.code_sha or 'unknown')[:8]}")
        return AlertCard(
            (f"🧪 TEST V6-Lite 已启动（Paper 测试模式 · {rehearsal} · {data_mode}）" if self.cfg.test.enabled else
             f"🟢 V6-Lite 已启动（{mode} {rehearsal} · {data_mode}）"), INFO_GREEN,
            (("启动时间", f"{pt:%Y-%m-%d %H:%M} PT / {et:%H:%M} ET"),
             ("模式", mode_line),
             ("状态", "\n".join(status_lines)),
             ("监控", f"IBKR 心跳：每 {int(GATEWAY_HEARTBEAT_EVERY.total_seconds())} 秒，连续 "
              f"{GATEWAY_HEARTBEAT_MISSES} 次无应答即告警\n持仓漂移：每 "
              f"{self.cfg.reconcile_interval_seconds} 秒对账，不一致即告警\n每日健康报告："
              f"{report_time}（收盘后 {int(DAILY_REPORT_AFTER_CLOSE.total_seconds()/60)} 分钟）\n"
              f"开盘后 {self.cfg.tp_start_after_open_seconds} 秒内不开始新止盈"),
             ("扫描时间", scans),
             ("手动指令（本机命令行）", commands)))

    def _startup_remote_message(self, now: datetime, summary: dict) -> str:
        """Fixed six-section phone view; the audited startup summary stays full."""
        pt, et = now.astimezone(PT), now.astimezone(ET)
        data_mode = "DELAYED" if self.cfg.market_data_type == 3 else "LIVE"
        md = summary.get("market_data") or {}
        total, usable = md.get("quote_symbols", 0), md.get("usable_quotes", 0)
        # The first tick creates today's risk row; its absence in this
        # pre-tick startup snapshot is expected, not a health incident.
        normal_startup_blocks = {"outside_entry_window", "risk_day_not_initialized"}
        problems = [b for b in summary.get("entry_blocks", ()) if b not in normal_startup_blocks]
        if total and usable < total:
            problems.append(f"quotes {usable}/{total} usable")
        parts = [f"IBKR {'connected' if summary['broker_connected'] else 'disconnected'}",
                 f"reconcile {'OK' if summary['reconcile_ok'] else 'ATTENTION'}",
                 f"alerts {'OK' if summary['alerts_healthy'] else 'ATTENTION'}"]
        status = "ATTENTION" if problems or not all((summary["broker_connected"],
                                                    summary["reconcile_ok"], summary["alerts_healthy"])) else "OK"
        lines = [
            f"Started   {pt:%a %Y-%m-%d %H:%M} PT / {et:%H:%M} ET",
            f"Mode      {'TEST · ' if self.cfg.test.enabled else ''}{self.cfg.mode.upper()} · {'DRY-RUN · ' if self.dry_run else ''}"
            f"{execution_label(self.cfg.mode, dry_run=self.dry_run)} · {data_mode} data · {self.cfg.account} · code {str(self.code_sha or 'unknown')[:8]}",
            f"Status    {status}  {' · '.join(parts)}",
        ]
        if problems:
            lines.append("          " + "; ".join(problems[:2]) +
                         (f" (+{len(problems)-2} more in status)" if len(problems) > 2 else ""))
        elif "outside_entry_window" in summary.get("entry_blocks", ()):
            start = et_time_on(trading_day(now), self.cfg.entry_window_start_et).astimezone(ET)
            when = f"until {start:%H:%M} ET" if now < start else "outside entry window"
            lines.append(f"          Entries closed {when}")
        positions = summary.get("open_positions") or []
        lines.append(f"          Quotes {usable}/{total} usable · V6-Lite positions: " +
                     (", ".join(f"{p[0]} {p[1]}" for p in positions) or "none"))
        ignored = sorted(s for s, d in self.store.dispositions().items() if d == "ignore")
        if ignored:
            lines.append("          Ignored holdings: " + " ".join(ignored))
        session = session_for(trading_day(now))
        report_at = session.close_utc + DAILY_REPORT_AFTER_CLOSE if session else None
        report_time = report_at.astimezone(PT).strftime("%H:%M PT") if report_at else "next trading close+5m"
        lines.extend([
            f"Monitors  IBKR heartbeat {int(GATEWAY_HEARTBEAT_EVERY.total_seconds())}s "
            f"(page after {GATEWAY_HEARTBEAT_MISSES} misses)",
            f"          Position drift check every {self.cfg.reconcile_interval_seconds}s (page on mismatch)",
            f"          Daily health report {report_time} (close+{int(DAILY_REPORT_AFTER_CLOSE.total_seconds()/60)}m)",
            f"          开盘后 {self.cfg.tp_start_after_open_seconds} 秒内不开始新止盈",
            "Scans     " + " · ".join(
                f"{slot} ET / {et_time_on(trading_day(now), slot).astimezone(PT):%H:%M} PT"
                for slot in self.cfg.scan_times_et),
            "Commands  status | report | kill --reason | resume",
            "          ignore SYM | release SYM | resolve-intent ID | clear-anomaly CODE",
        ])
        if self.cfg.is_live:
            lines[-1] += " | approve-live | revoke-live"
        return "```\n" + "\n".join(lines) + "\n```"

    def _check_client_identity(self, now: datetime, bound: dict) -> None:
        """reqExecutions only returns this client's executions, so orders placed
        under another client id are invisible to recovery. Changing the id is
        only safe when no order is in flight -- according to the ledger AND to
        a fresh broker query (the ledger may be incomplete)."""
        previous = bound.get("client_id")
        if self.dry_run or previous is None or int(previous) == self.cfg.client_id:
            return                   # a rehearsal never recovers or owns orders
        active = self.store.active_intents()
        if active:
            raise StartupError(
                f"client id changed {previous} -> {self.cfg.client_id} while {len(active)} order(s) are "
                "active; restart with the previous client id so their fills can be recovered")
        try:
            working = [o for o in self.broker.open_orders() if o.ours]
        except Exception as exc:
            raise StartupError(f"client id changed {previous} -> {self.cfg.client_id} and working orders "
                               f"cannot be verified ({exc}); restart with the previous client id") from exc
        if working:
            raise StartupError(
                f"client id changed {previous} -> {self.cfg.client_id} while the broker still has "
                f"{len(working)} V6-Lite working order(s) ({', '.join(o.intent_id for o in working)}); "
                "restart with the previous client id or cancel them in TWS first")
        with self.store.tx():
            self.store.set_meta("client_id", str(self.cfg.client_id))
            self.store.audit(now, "CLIENT_ID_CHANGED", previous=previous, current=self.cfg.client_id)

    def _pin_contracts(self, now: datetime) -> None:
        """conId is pinned the first time a symbol is qualified; a later
        change (corporate action, wrong listing) blocks entries."""
        for symbol in self.cfg.scan_symbols:
            con_id = self.broker.qualify(symbol)
            pinned = self.store.control(f"conid:{symbol}")
            if pinned is None:
                with self.store.tx():
                    self.store.set_control(now, f"conid:{symbol}", str(con_id))
            elif int(pinned) != con_id:
                with self.store.tx():
                    self.store.raise_anomaly(now, f"contract_changed:{symbol}",
                                             f"pinned conId {pinned} now {con_id}")

    # ================================================================ loop
    def tick(self) -> None:
        now = self.clock.now()
        started = time.monotonic()
        self._tick_slowest_phase = "idle"
        self._tick_slowest_seconds = 0.0
        # A tick whose exit check is due may run it more than once (e.g. before
        # a slow request AND after an order was settled in the same tick), so
        # a protective check inside a slow path never delays the regular one.
        self._exit_tick_due = self._exit_check_due(now)
        try:
            self._tick(now)
            # Protection callbacks and due exits take precedence over audit
            # maintenance, including a callback that wakes an early-return tick.
            if not self._protection_priority_due:
                self._tick_step("observation summaries", lambda: self.store.flush_due_observations(self.clock.now()))
        finally:
            self._exit_tick_due = False
            elapsed = time.monotonic() - started
            if elapsed > 30:
                log.info("[SLOW_TICK] secs=%.3f phase=%s", elapsed, self._tick_slowest_phase)

    def process_protection_priority(self) -> None:
        """Same-loop continuation after an urgent broker callback wakes pump.

        All fresh broker queries are outside ib_insync's callback, yet precede
        the normal tick's scans/history/account work.
        """
        if not self.cfg.protection.enabled or self.dry_run or not self._protection_priority_due:
            return
        # A status can arrive while a fresh evidence request is waiting. Do
        # not lose that flag or defer the resulting TP to a normal tick. Bound
        # the continuation so repeated callbacks cannot monopolize the loop.
        for _ in range(3):
            self._drain(self.clock.now())
            self._protection_timers(self.clock.now())
            if not self._protection_priority_due:
                return
            self._protection_progress(self.clock.now())
        if self._protection_priority_due:
            log.warning("protection priority still pending after bounded continuation")

    def _tick_step(self, phase: str, action):
        started = time.monotonic()
        try:
            return action()
        finally:
            elapsed = time.monotonic() - started
            if elapsed > self._tick_slowest_seconds:
                self._tick_slowest_seconds = elapsed
                self._tick_slowest_phase = phase

    def _tick(self, now: datetime) -> None:
        self._tick_step("events", lambda: self._drain(now))
        # A protection submit/modify may synchronously enqueue orderStatus in
        # FakeBroker (or an IB callback may have arrived while the first drain
        # ran). Process it before any history, scan or account slow request.
        self._tick_step("priority events", lambda: self._drain(self.clock.now()))
        self._tick_step("protection timers", lambda: self._protection_timers(self.clock.now()))
        # Urgent/open-gap phases retain first priority. Ordinary GTC health
        # maintenance runs after exit decisions so a slow query for AAPL does
        # not starve SPY's scheduled exit check.
        self._tick_step("protection", lambda: self._protection_progress(
            self.clock.now(), steady_budget=not self._protection_priority_due,
            phase="all" if self._protection_priority_due else "active"))
        if self.cfg.protection.enabled and not self.dry_run and \
                self._protection_priority_due and self.broker.is_connected():
            # A callback arrived inside a slow protection query; give the
            # event loop another immediate priority turn before history/scan.
            return
        self._tick_step("connection", lambda: self._maintain_connection(now))
        self.flags.alerts_healthy = self.alerter.healthy(now)
        self._refresh_approval(now)
        if not self.flags.broker_connected:
            if self.cfg.test.enabled and not self.dry_run:
                self._invalidate_test_entry_ready(now, "refused:test_entry_broker_disconnected")
            self._heartbeat(now)
            return
        self._tick_step("account", lambda: self._refresh_equity(now))
        if not self.dry_run:
            self._resolve_intents(now)
            self._tick_step("execution time verification", lambda: self._refresh_execution_times(self.clock.now()))
        if self.store.controls_with_prefix("release:"):
            self._reconcile_due = True                 # operator release waiting
        if self._reconcile_due or not self._last_reconcile or \
                now - self._last_reconcile >= timedelta(seconds=self.cfg.reconcile_interval_seconds):
            self._tick_step("reconcile", lambda: self._reconcile(now))
            if self.cfg.protection.enabled and not self.dry_run and self._protection_priority_due:
                self._tick_step("protection after reconcile",
                                lambda: self._protection_progress(self.clock.now()))
        self._tick_step("risk", lambda: self._daily_risk(self.clock.now(), fetch=True))
        if self.cfg.test.enabled and not self.dry_run and self.flags.reconcile_ok and \
                self.store.control("test_entry_ready") is None:
            self._publish_test_entry_ready(self.clock.now())
        self._tick_step("protection mark", lambda: self._monitor_protection_marks(self.clock.now()))
        self._tick_step("exit", lambda: self._evaluate_exits(self.clock.now(), regular=True))
        self._tick_step("exit queue", self._protect)
        if self._protection_priority_due:
            self._tick_step("protection priority after exits", self.process_protection_priority)
        self._tick_step("steady protection", lambda: self._protection_progress(
            self.clock.now(), steady_budget=True, phase="steady"))
        if self.cfg.protection.enabled and not self.dry_run and self._protection_priority_due:
            return  # callback during slow steady evidence; resume before scan
        self._tick_step("scan", lambda: self._run_scheduled_scans(self.clock.now()))
        if self.cfg.test.enabled:
            self._tick_step("test entry", lambda: self._run_test_entry(self.clock.now()))
        self._tick_step("exit queue", self._protect)
        self._tick_step("commission repair", lambda: self._refresh_missing_commissions(self.clock.now()))
        self._update_loss_state(self.clock.now())
        self._tick_step("report", lambda: self._end_of_day(self.clock.now()))
        self._tick_step("heartbeat", lambda: self._heartbeat(self.clock.now()))
        self._tp_consume_resume(self.clock.now())

    def _drain(self, now: datetime) -> None:
        # A broker write triggered by one status can enqueue its confirmation
        # before this event phase returns (FakeBroker), just as IB dispatches
        # a later callback. Consume bounded follow-on events in this phase so
        # a confirmed restoration is not left until the next scheduled tick.
        for _ in range(8):
            events = self.broker.drain_events()
            if not events:
                self._flush_closed_cards(self.clock.now())
                return
            for event in events:
                self._handle_event(self.clock.now(), event)
        self._protection_priority_due = True
        log.warning("[PROTECT_DISPATCH] broker event drain reached eight passes")

    def _on_urgent_broker_event(self, event) -> None:
        """execDetails/orderStatus callback path: no nested blocking IB query."""
        self._urgent_received_mono = time.monotonic()
        self._in_urgent_callback = True
        try:
            self._handle_event(self.clock.now(), event)
        except Exception as exc:
            # An execution callback can fail in local SQLite before the GTC
            # submission.  Latch the entry gate and page immediately even if
            # persistence is unavailable.  Re-raise so the adapter queues an
            # idempotent replay outside the callback.
            self._protection_emergency = True
            self._protection_emergency_at = self.clock.now()
            symbol = getattr(event, "symbol", "待核实")
            ref = getattr(event, "intent_id", "待核实") or "待核实"
            body = (f"{symbol} orderRef={ref}；成交回调本地处理失败 {type(exc).__name__}；"
                    "持仓数量、保护单剩余、止盈在途、外部卖单、未保护数量均待核实；"
                    "保护单 orderId=待核实。")
            card = AlertCard("🔴 P0 · 成交回调处理失败，券商端保护未核实", P0_RED,
                             (("发生了什么", body), ("影响", "持仓可能没有券商端保护；停止新开仓。"),
                               ("你要做什么", "1）在 TWS 核对真实持仓、成交和卖单；"
                                "2）不要新开仓，由 John 人工处置保护缺口；"
                                "3）修复后需要重启 Lite，才能恢复开仓；重启时先完成三项对账。")))
            try:
                self.alerter.send(self.clock.now(), "P0", f"protection callback failure {symbol}",
                                  body, card=card)
            except Exception:
                log.exception("P0 delivery failed after protection callback failure")
            raise
        finally:
            self._in_urgent_callback = False

    def _service_protection(self, now: datetime) -> None:
        """Book what arrived, re-evaluate the daily loss, and run the exit
        DECISION pass if it is due. Never blocks: exit decisions only queue
        their broker work."""
        if self._in_protection:
            self._drain(now)
            return
        self._in_protection = True
        try:
            self._drain(now)
            if self.flags.broker_connected and not self.dry_run:
                self._daily_risk(now, fetch=False)
            if self.flags.broker_connected and self._started and not self._shutting_down \
                    and not self._in_exit_eval:
                self._evaluate_exits(now)
        finally:
            self._in_protection = False

    def _protect(self, kind: str | None = None, *, seconds: float | None = None) -> None:
        """Call immediately BEFORE a blocking broker request (and at the end
        of a tick). If the next exit check would fall due while the request
        can still be running (its declared worst case), the decision pass runs
        now. Queued exit work is then executed item by item -- every item
        calls _protect before its own slow call -- and the budget for the
        caller's request is re-checked afterwards with the real clock, so no
        chain of slow calls can stretch the check interval."""
        cost = seconds if seconds is not None else (self.broker.worst_case_seconds(kind) if kind else 0.0)
        interval = timedelta(seconds=self.cfg.exit_check_interval_seconds)
        for round_ in range(2):     # at most one queue run per call: never an unbounded loop
            now = self.clock.now()
            if self._last_exit_check is not None and now + timedelta(seconds=cost) >= self._last_exit_check + interval:
                if not self._in_protection and not self._in_exit_eval and self._started and not self._shutting_down:
                    self._last_exit_check = None           # due now
            self._service_protection(now)
            if round_ or self._processing_queue or self._in_protection or self._in_exit_eval or not self._queue:
                return
            self._run_queue()

    def _enqueue(self, key: str, fn: Callable[[], None]) -> None:
        if self._shutting_down:
            return                  # no new broker work once shutdown has begun
        if key not in self._queue:
            self._queue[key] = fn

    def _run_queue(self) -> None:
        if self._processing_queue:
            return
        self._processing_queue = True
        try:
            # only the work that was queued when this run started; anything a
            # decision pass adds meanwhile waits for the next run (no livelock)
            for key in list(self._queue):
                fn = self._queue.pop(key, None)
                if fn is None:
                    continue
                try:
                    fn()
                except Exception:           # one failing item must not stop protection
                    log.exception("queued exit work failed")
        finally:
            self._processing_queue = False

    def _on_open_orders_normalization(self, now, event):
        import hashlib
        payload = dict(order_ids=event.order_ids, perm_ids=event.perm_ids,
            intent_ids=event.intent_ids, raw_rows=event.raw_rows,
            unique_rows=0 if event.conflicting_fields else event.raw_rows-event.duplicate_rows, duplicate_rows=event.duplicate_rows,
            conflicting_fields=event.conflicting_fields, socket_generation=event.socket_generation,
            query_request_id=event.query_request_id, observed_at=event.observed_at.isoformat())
        key = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        seen = getattr(self,'_open_orders_normalization_seen',None)
        if seen is None:
            seen = self._open_orders_normalization_seen = OrderedDict()
        if key in seen:
            return
        def remember():
            seen[key] = None
            while len(seen)>256:seen.popitem(last=False)
        current = event.socket_generation == getattr(self.broker,'query_generation',None)
        if not event.conflicting_fields or not current:
            with self.store.tx():
                self.store.audit(now,'OPEN_ORDERS_NORMALIZED' if not event.conflicting_fields else 'OPEN_ORDERS_CONFLICT_STALE',**payload)
            remember()
            return
        self._protection_invalidate_reduction(event)
        self._final_cancel_invalidate(event)
        self._working_at = None
        self._protection_priority_due = True
        rows={}
        symbols=set();unbound_refs=[]
        for ref in event.intent_ids:
            intent=self.store.intent(ref)
            if intent is not None:
                symbols.add(intent['symbol'])
                row=self.store.protection(intent['position_id']) if intent['position_id'] else None
                if row is not None:rows[row['position_id']]=row
            else:unbound_refs.append(ref)
        for row in self.store.protections():
            if row['broker_order_id'] in event.order_ids or row['perm_id'] in event.perm_ids:
                rows[row['position_id']]=row;symbols.add(row['symbol'])
        reason='openOrders 同一订单的原生回报矛盾：'+', '.join(event.conflicting_fields)
        for pid,row in rows.items():
            self._protection_notice_evidence.pop(pid,None)
            if self._overnight_position(row):self._overnight_failure(now,row,reason)
            else:self.alerter.send(now,'P1','openOrders conflict '+row['symbol'],reason)
            self.store.set_control(now,'frozen:'+row['symbol'],'openOrders native rows contradict')
            if self._overnight_position(row):
                self._overnight_record_frozen(now,row,row['tp_stage'] or Purpose.TP2)
            self._protection_page(now,'P0','unknown',row,reason+'；整轮拒绝，不撤改原 STP')
        for symbol in symbols:
            self.store.set_control(now,'frozen:'+symbol,'openOrders native rows contradict')
        if not rows or unbound_refs or symbols-{row['symbol'] for row in rows.values()}:
            self.store.raise_anomaly(now,'open_orders_conflict','自有工作单冲突尚不能绑定受管保护；停止新风险')
            self.alerter.send(now,'P1','openOrders identity conflict',reason)
            self.alerter.send(now,'P0','openOrders identity unknown',reason)
        with self.store.tx():
            self.store.audit(now,'OPEN_ORDERS_CONFLICT',**payload)
        remember()

    # ================================================================ events
    def _handle_event(self, now: datetime, event) -> None:
        if isinstance(event, OpenOrdersNormalizationEvent):
            self._on_open_orders_normalization(now, event)
            return  # duplicates are audit only; conflicts invalidate explicitly
        # Fees do not change P/S/T/X, order identity, or execution economics.
        # In particular, an urgent fee between native ACK components must not
        # revoke an otherwise unchanged reduction/final-cancel continuation.
        if not isinstance(event, CommissionEvent):
            self._protection_invalidate_reduction(event)
            self._final_cancel_invalidate(event)
        if isinstance(event, OrderBookChangeEvent):
            row = self.store.protection_for_intent(event.intent_id)
            if not event.connection_snapshot and not event.query_snapshot and (
                    row is None or not self._protection_steady(row)):
                self._protection_priority_due = True
        elif isinstance(event, AnomalyEvent):
            with self.store.tx():
                self.store.raise_anomaly(now, event.code, event.detail)
            self.alerter.send(now, "P1", f"broker data anomaly {event.code}", event.detail)
        elif isinstance(event, ConnectionEvent):
            self._on_connection(now, event)
        elif isinstance(event, BrokerNoticeEvent):
            if not self.dry_run:
                self.store.audit(now, "IBKR_NOTICE", req_id=event.req_id,
                                 error_code=event.error_code, raw_message=event.raw_message)
        elif isinstance(event, ExecutionTimeDiagnosticEvent):
            self.store.audit(now, "EXECUTION_TIME_REVIEW_REQUIRED" if event.invalid else "EXECUTION_TIME_OBSERVED",
                exec_id=event.exec_id, source=event.source, raw_time=event.raw_time,
                category=event.category, reason="native execution decoder rejected row" if event.invalid else "raw time observation only")
        elif self.dry_run:
            return  # a rehearsal never books fills or order states
        elif isinstance(event, FillEvent):
            self._ingest_fill(now, event)
        elif isinstance(event, CommissionEvent):
            with self.store.tx():
                changed = self.store.record_commission(now, event.exec_id, event.commission)
            if changed:
                self._commission_updated(now, event.exec_id)
        elif isinstance(event, StatusEvent):
            self._on_status(now, event)
        elif isinstance(event, PriceObservationEvent):
            if event.connection_snapshot:
                self.store.audit(now, "CONNECTION_SNAPSHOT_PRICE", source=event.source,
                    intent_id=event.identity.intent_id, raw_status=event.identity.status,
                    broker_echo_lmt=event.identity.broker_echo_lmt,
                    aux_price=event.identity.aux_price, error_code=event.error_code, reason=event.reason)
                return  # no TP status evidence or working-price hold authority
            if event.owned_open_orders is None:
                self._on_price_observation(now, event)
            self._protection_on_open_price(now, event)

    def _excluded_symbols(self) -> set[str]:
        ignored = {s for s, d in self.store.dispositions().items() if d == "ignore"}
        return set(self.cfg.protected_symbols) | ignored

    def _ingest_fill(self, now: datetime, fill: FillEvent) -> None:
        self._account_stream_flat_at = None  # every fill invalidates flat telemetry before early returns
        if not fill.intent_id and fill.symbol.upper() in self._excluded_symbols():
            # manual trading in protected/ignored holdings is not V6-Lite's business
            self.store.audit(now, "EXCLUDED_SYMBOL_FILL", exec_id=fill.exec_id, symbol=fill.symbol,
                             side=str(fill.side), qty=fill.quantity)
            return
        prior = self.store.intent(fill.intent_id) if fill.intent_id else None
        outcome = self.store.record_fill(now, fill, recovered=self._recovering)
        if outcome.anomaly:
            self.alerter.send(now, "P1", f"fill anomaly {outcome.anomaly}",
                              "New entries are blocked until the operator reviews broker truth "
                              "and runs `clear-anomaly`.")
        if outcome.anomaly:
            self._reconcile_due = True          # broker truth decides what is really held
        if not outcome.inserted:
            if outcome.commission_added:
                self._commission_updated(now, fill.exec_id)
            if fill.time_source == "reqExecutions":
                self._update_loss_state(now)
            log.info("[FILL_NOT_BOOKED] exec_id=%s commission_changed=%s anomaly=%s", fill.exec_id,
                     outcome.commission_added, outcome.anomaly)
            return
        self._reconcile_due = True
        if prior is not None and prior["purpose"] in TP_PURPOSES and prior["cancel_requested_at"]:
            try:
                self._test_timeout_event(now,self._tp_context(prior),"late_fill",prior["intent_id"],exec_id=fill.exec_id)
            except Exception:
                log.exception("test timeout late-fill diagnostic failed")
        self._last_fill_at = now
        if fill.time_uncertain:
            self._last_execution_time_query = None
        log.info("[FILL] %s %s %s@%s intent=%s exec=%s%s", fill.symbol, fill.side, fill.quantity, fill.price,
                 fill.intent_id or "-", fill.exec_id, self._test_tag)
        if outcome.anomaly and outcome.anomaly.startswith(("late_entry_fill", "duplicate_open_position")):
            symbol = fill.symbol.upper()
            self._enqueue(f"subscribe:{symbol}", lambda: (self._protect("query"),
                                                          self.broker.subscribe_quotes([symbol])))
        if prior is not None and prior["purpose"] in {Purpose.STOP, Purpose.TP1, Purpose.TP2, Purpose.TP3, Purpose.PROTECT} and \
                IntentStatus(prior["status"]) in {IntentStatus.CANCEL_SEEN, IntentStatus.CANCELLED,
                                                  IntentStatus.REJECTED}:
            self._on_late_exit_fill(now, prior, fill)
        if fill.intent_id:
            self._refresh_intent_fill_state(now, fill.intent_id)
        # record_fill may bind this ENTRY to a newly recovered or existing
        # OPEN position.  SQLite Rows captured before booking retain old values.
        current = self.store.intent(fill.intent_id) if fill.intent_id else None
        if current is not None and current["purpose"] == Purpose.ENTRY:
            # The first GTC submission runs before returning from execDetails;
            # all blocking evidence checks for later increases run outside it.
            target = current["recovery_position_id"] or current["position_id"]
            self._protection_on_entry_fill(now, target)
        elif prior is not None and prior["purpose"] == Purpose.PROTECT:
            self._protection_priority_due = True
            current_gtc = self.store.protection(prior["position_id"])
            if current_gtc is not None and current_gtc["intent_id"] != prior["intent_id"]:
                self.store.set_control(now, f"retired_fill_cleanup:{prior['position_id']}", fill.exec_id)
                self._protection_cleanup_retired_fill(now, current_gtc)
            if (current_gtc is not None and current_gtc["intent_id"] == prior["intent_id"] and
                    current_gtc["state"] == "FILLED" and current_gtc["confirmed_remaining"] == 0 and
                    current_gtc["pending_max_remaining"] == 0):
                self.store.delete_control(f"protect_trigger:{prior['position_id']}")
                if self._protection_closed_by_own_fill(current_gtc):
                    self._protection_finish_evidence_episode(now, current_gtc,
                        "确认的本系统保护成交已平仓；旧证据事件结束", closed=True)
                self._protection_resolve_incidents(now, current_gtc, "真实止损成交已无剩余；未成交事件解除",
                                                  groups={"trigger unfilled"})
            pos = self.store.position(prior["position_id"])
            remaining = int(pos["qty_open"]) if pos is not None and pos["status"] == "OPEN" else 0
            avg_row = self.store.conn.execute(
                "SELECT SUM(quantity*price)/SUM(quantity) AS avg_price FROM fills WHERE intent_id=?",
                (prior["intent_id"],)).fetchone()
            avg_price = float(avg_row["avg_price"] or fill.price)
            self.alerter.send(
                now, "INFO", f"止损已成交 {fill.symbol}",
                f"{fill.symbol} 本次止损成交 {fill.quantity} 股，累计成交均价 {avg_price:.2f}，"
                f"剩余持仓 {remaining} 股；保护单 {prior['intent_id']}。",
                card=AlertCard("🟢 止损已成交", INFO_GREEN,
                               (("发生了什么", f"{fill.symbol} 本次成交 {fill.quantity} 股，"
                                 f"累计均价 {avg_price:.2f}，剩余持仓 {remaining} 股。"),
                                ("订单", f"orderRef={prior['intent_id']} "
                                 f"orderId={prior['broker_order_id'] or '-'}"))),
            )
        elif prior is not None and prior["purpose"] in {Purpose.TP1, Purpose.TP2, Purpose.TP3}:
            self._protection_priority_due = True
            row_before = self.store.protection(prior["position_id"])
            active_tp = row_before is not None and row_before["tp_intent_id"] == prior["intent_id"]
            self._protection_after_tp_fill(now, prior["position_id"])
            if not active_tp or IntentStatus(prior["status"]) in {
                    IntentStatus.CANCEL_SEEN, IntentStatus.CANCELLED, IntentStatus.REJECTED}:
                # Only an already-settled TP can have been restored to full S.
                # Ordinary TP fills reduce P and its own T together, with no GTC write.
                self._protection_on_sell_fill(now, prior["position_id"])
        if outcome.closed_position_id:
            self._on_position_closed(now, outcome.closed_position_id, fill.symbol, fill.exec_id)

    def _on_late_exit_fill(self, now: datetime, intent, fill: FillEvent) -> None:
        """An execution for a SELL we had already settled (the cancel -> fill
        race). Any retry sized from the old quantity could now oversell, so
        every other working SELL of the position is pulled at once and will be
        re-planned from the corrected quantity. Entries stop until reviewed."""
        code = f"late_exit_fill:{intent['intent_id']}"
        with self.store.tx():
            self.store.raise_anomaly(now, code, f"{fill.symbol} {fill.quantity} sold by {intent['purpose']} "
                                                f"after it was settled {intent['status']}")
        pulled = []
        for other in self.store.active_intents_for_position(intent["position_id"]):
            if other["side"] == Side.SELL and other["purpose"] != Purpose.PROTECT \
                    and other["intent_id"] != intent["intent_id"] \
                    and not other["hold_reason"]:
                iid = other["intent_id"]
                self._enqueue(f"cancel:{iid}", lambda iid=iid: self._request_cancel(
                    self.clock.now(), iid, "late_fill_of_settled_exit"))
                pulled.append(iid)
        self.alerter.send(now, "P1", f"late exit fill {fill.symbol}",
                          f"{intent['purpose']} {intent['intent_id']} filled {fill.quantity} after it was settled. "
                          f"Retries pulled: {', '.join(pulled) or 'none'}. Check TWS for a short position.")

    def _on_position_closed(self, now: datetime, position_id: str, symbol: str, closing_exec_id: str) -> None:
        trade = next((t for t in self.store.closed_trades() if t["position_id"] == position_id), None)
        if trade is None:
            return
        freeze_estimate(self.store, self.cfg, now, trade)
        key = "close_card_pending:"+position_id
        with self.store.tx():
            self.store.audit(now, "TRADE_CLOSED", position_id=position_id, symbol=symbol,
                             gross=round(trade["gross"], 4), fees=round(trade["fees"], 4),
                             missing_fees=len(trade["missing"]))
            if self.store.control(key) is None and self.store.control("close_card_sent:"+position_id) is None:
                self.store.set_control(now, key, json.dumps({"position_id": position_id, "symbol": symbol,
                    "closing_exec_id": closing_exec_id, "deadline": (now+timedelta(seconds=3)).isoformat()}))
        self._update_loss_state(now)
        self._arm_close_card_deadline(now, position_id)
        self._flush_closed_cards(now)

    def _update_loss_state(self, now: datetime) -> None:
        """Publish the derived streak/cooldown (status, report) and alert once
        per new cooldown. The entry gate derives it again itself."""
        state = loss_state(self.store, self.cfg)
        until = state.cooldown_until.isoformat() if state.cooldown_until else None
        if until and self.store.control(LATCH) != until:
            with self.store.tx():
                self.store.set_control(now,LATCH,until)

        if self.store.control("cooldown_until") == until and self.store.control("loss_streak") == str(state.streak):
            return
        with self.store.tx():
            if until:
                self.store.set_control(now, "cooldown_until", until)
            else:
                self.store.delete_control("cooldown_until")
            self.store.set_control(now, "loss_streak", str(state.streak))
            self.store.audit(now, "LOSS_STATE", streak=state.streak, cooldown_until=until)
        if until and state.cooldown_until > now and self.store.control("cooldown_alerted") != until:
            with self.store.tx():
                self.store.set_control(now, "cooldown_alerted", until)
            basis = "\n".join(f"positionId={t['position_id']}；净值 {t['net']:.2f} USD；"
                + (f"含估算佣金 {t['estimated_fees']:.2f} USD" if t['estimated'] else "佣金已回报/已核定")
                for t in state.trigger_trades)
            detail = (f"{self.cfg.max_consecutive_losses} consecutive losing trades; "
                      f"entries paused until {until}\n"+basis)
            self.store.set_control(now,"cooldown_basis:"+until,json.dumps(state.trigger_trades))
            self.alerter.send(now, "P2", "loss cooldown",detail,
                card=AlertCard("🟡 亏损后冷却",P2_YELLOW,(("发生了什么",detail),
                    ("判定口径","估算净值 < 0 计为亏损；缺佣金按近期同标的同方向同数量实际佣金估算，配置最低值兜底。"),
                    ("你要做什么","核对各持仓最终佣金；佣金补齐不会自动解除已触发的冷却。"))))

    def _refresh_intent_fill_state(self, now: datetime, intent_id: str) -> None:
        intent = self.store.intent(intent_id)
        if intent is None or IntentStatus(intent["status"]) in TERMINAL_STATUSES:
            return
        filled = self.store.intent_filled_qty(intent_id)
        if filled >= intent["quantity"]:
            self._finalize_intent(now, intent_id, IntentStatus.FILLED)
        elif filled > 0 and intent["status"] in {IntentStatus.SENT, IntentStatus.NEW}:
            self.store.update_intent(now, intent_id, status=IntentStatus.PARTIAL)

    def _on_status(self, now: datetime, ev: StatusEvent) -> None:
        if self.dry_run:
            return
        intent = self.store.intent(ev.intent_id)
        if intent is None:
            return
        if ev.connection_snapshot:
            # Role was fixed when the callback was created, not when it
            # is drained after sync. Never adopt/confirm from this cache.
            identity = ev.order_identity or ev.terminal_identity
            self.store.audit(now, "CONNECTION_SNAPSHOT_STATUS", intent_id=ev.intent_id,
                raw_status=ev.raw_status, broker_order_id=ev.broker_order_id, perm_id=ev.perm_id,
                broker_echo_lmt=identity.broker_echo_lmt if identity is not None else ev.broker_echo_lmt,
                error_code=ev.error_code, reason=ev.message)
            # The audit above is an independent diagnostic hint. Even
            # last_raw_status participates in terminal/restore/release rules.
            self._protection_priority_due = bool(self.cfg.protection.enabled)
            return
        if IntentStatus(intent["status"]) in TERMINAL_STATUSES:
            if intent["purpose"] == Purpose.PROTECT:
                self._final_cancel_terminal_status(now, ev)
            # A completed old TP cannot pause a new stage/position. Retained
            # rejection records only retry their immutable unACKed payload.
            if intent["purpose"] in TP_PURPOSES and self._tp_hard_identity(intent, ev.order_identity, prices=True):
                self._tp_status_evidence(now, ev, intent)
                identity = ev.order_identity
                if (intent["cancel_requested_at"] and ev.raw_status in {"Cancelled", "ApiCancelled"} and
                        identity.status == ev.raw_status and ev.broker_order_id == identity.broker_order_id and
                        ev.perm_id == identity.perm_id and ev.filled == identity.filled and
                        ev.remaining == identity.remaining):
                    # Diagnostic only: the completed TP stays terminal and grants no restore permission.
                    self._test_timeout_event(now, self._tp_context(intent), "cancel_confirm", ev.intent_id)
                rejection = self._tp_json(f"tp_rejection:{ev.intent_id}")
                if rejection is not None:
                    self._tp_rejection_page(now, self._tp_context(intent), rejection)
            return
        if intent["purpose"] in TP_PURPOSES and not self._tp_validate_status(now, ev, intent):
            return
        native_valid = True
        if intent["purpose"] == Purpose.PROTECT:
            identity = ev.order_identity or ev.terminal_identity
            self.store.audit(now, "PROTECT_BROKER_ECHO", intent_id=ev.intent_id,
                raw_status=ev.raw_status, broker_echo_lmt=(identity.broker_echo_lmt
                if identity is not None else ev.broker_echo_lmt))
            native_valid = self._protection_adopt_status_identity(now, ev)
            intent = self.store.intent(ev.intent_id)
            if native_valid:
                row = self.store.protection_for_intent(ev.intent_id)
                if row is not None and self.store.control(f"retired_fill_cleanup:{row['position_id']}"):
                    self._protection_cleanup_retired_fill(now, row)
            terminal_metadata = self._protection_capture_terminal(now, ev, native_valid=native_valid)
            if terminal_metadata:
                self._protection_hold_terminal_metadata(now, ev)
                return
            if (ev.identity_checked or ev.order_identity is not None) and not native_valid:
                if ev.raw_status in _CANCEL_HINTS or ev.raw_status == "Filled" or ev.order_identity is not None:
                    self._protection_reject_status_identity(now, ev)
                # Initial PendingSubmit/permId=0 is not a complete identity.
                # Wait for a later strong tuple; do not permanently hold it.
                return
        if intent["purpose"] == Purpose.PROTECT and ev.raw_status in _CANCEL_HINTS:
            # orderRef alone cannot establish that the persisted GTC ended.
            # A missing or mismatched broker identity must never become a
            # CANCEL_SEEN hint that could later authorize release.
            exact = (intent["broker_order_id"] is not None and intent["perm_id"] is not None and
                     ev.broker_order_id == intent["broker_order_id"] and
                     ev.perm_id == intent["perm_id"])
            if not exact:
                row = self.store.protection_for_intent(ev.intent_id)
                self.store.hold_intent(now, ev.intent_id, "protection cancel status identity mismatch")
                if row is not None:
                    self.store.update_protection(now, row["position_id"], state="UNKNOWN")
                    self.store.set_control(now, f"frozen:{row['symbol']}",
                                           "unverified protection cancel identity")
                self.store.audit(now, "PROTECT_STATUS_IDENTITY_MISMATCH",
                                 intent_id=ev.intent_id, raw_status=ev.raw_status)
                self._protection_emergency = True
                if row is not None:
                    self._protection_page(now, "P0", "unknown",
                                          self.store.protection(row["position_id"]),
                                          "券商撤单提示的 orderId/permId 缺失或不一致；"
                                          "请在 TWS 核验原 GTC 的完整身份")
                return
        fields: dict = {"last_raw_status": ev.raw_status}
        if intent["hold_reason"]:
            # an operator hold is never moved by broker hints
            self.store.update_intent(now, ev.intent_id, **fields)
            return
        if ev.broker_order_id and not intent["broker_order_id"]:
            fields["broker_order_id"] = ev.broker_order_id
        if ev.perm_id and not intent["perm_id"]:
            fields["perm_id"] = ev.perm_id
        # A status string is only a HINT. Cancelled can be followed by Filled
        # (GOOGL TP1 incident) and Submitted+0 is never a failure. Terminal
        # hints start a grace timer; settlement is decided from evidence.
        if ev.raw_status in _CANCEL_HINTS or ev.raw_status == "Filled":
            if not intent["cancel_seen_at"]:
                fields["cancel_seen_at"] = now
            if ev.raw_status in _CANCEL_HINTS:
                fields["status"] = IntentStatus.CANCEL_SEEN
                if ev.message:
                    fields["note"] = ev.message
        self.store.update_intent(now, ev.intent_id, **fields)
        if intent["purpose"] == Purpose.PROTECT:
            self._protection_on_status(now, ev, previous_raw=intent["last_raw_status"])
        elif intent["purpose"] in {Purpose.TP1, Purpose.TP2, Purpose.TP3}:
            self._protection_on_tp_status(now, ev)

    def connection_lost(self, error=None) -> None:
        """Outer-loop transport boundary. No broker write or fresh proof here.

        ib_insync cancels sleep on globalErrorEvent before the queued native
        disconnect can reach tick(). Preserve that queue, close the entry
        gate now, and resume the existing b25 recovery after 30 seconds.
        """
        from v6lite.lifecycle import safe_reason
        now = self.clock.now()
        first = self._physical_loss_wait_started is None
        reason = safe_reason(error, self.cfg.account) if error else "API socket disconnected"
        self._on_connection(now, ConnectionEvent(False, reason))
        if first:
            self._physical_loss_wait_started = now
            self._reconnect_failures = max(1, self._reconnect_failures)
            self._reconnect_next_at = now + RECONNECT_EVERY
            self.store.audit(now, "RECONNECT_BACKOFF", reason=reason,
                             consecutive_failures=self._reconnect_failures, seconds=30)

    def _on_connection(self, now: datetime, ev: ConnectionEvent) -> None:
        if not ev.connected:
            if not self._reconnect_episode:
                self._reconnect_reason = ev.reason or "connection lost"
                self._reconnect_episode = True
            if self.cfg.test.enabled and not self.dry_run:
                self._invalidate_test_entry_ready(now, "refused:test_entry_broker_disconnected")
            if self.flags.broker_connected:
                self.alerter.send(now, "P1", "IBKR disconnected", ev.reason or "connection lost; "
                                  "new entries blocked, exits paused until reconnect + reconcile",
                                  remote=True)
            self.flags.broker_connected = False
            self.flags.reconcile_ok = False
            if self.broker.is_connected():
                # API socket is up but Gateway lost the IBKR servers (code 1100):
                # wait for 1101/1102 instead of "reconnecting" to a dead link.
                self._link_down = True
        else:
            self._link_down = False
            self._reconcile_due = True
            self.flags.reconcile_ok = False

    # ================================================================ connection
    def _broker_synchronized(self) -> bool:
        ready = getattr(self.broker, "is_synchronized", None)
        return bool(self.broker.is_connected() and (ready() if callable(ready) else True))

    @staticmethod
    def _reconnect_reason_key(reason: str) -> str:
        reason = " ".join(str(reason).split())
        # Only explicitly labelled incidental values are normalized. Error
        # codes, symbols, contract/order IDs, ports and business numbers stay.
        pattern = (r"(?<![\w])(?P<label>generation|pid|req(?:uest)?[_ ]?id|"
                   r"elapsed(?:[_ ](?:seconds?|secs?|s|ms))?|attempts?)"
                   r"(?:\s*[:=]\s*|\s+)"
                   r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
                   r"(?:\s*(?:milliseconds?|ms|seconds?|secs?|s))?(?![\w.])")
        def incidental(match):
            label = re.sub(r"[_ ]", "", match.group("label")).lower()
            label = ("reqId" if label in {"reqid", "requestid"} else
                     "elapsed" if label.startswith("elapsed") else
                     "attempt" if label.startswith("attempt") else label)
            return label + "=<incidental>"
        reason = re.sub(pattern, incidental, reason, flags=re.IGNORECASE)
        # Dynamic elapsed/count suffixes do not turn one channel fault into
        # new causes. Retain the complete latest reason for audit and paging.
        for channel in ("openOrders", "positions"):
            if reason.startswith(channel + ":") and "unsolicited End" in reason:
                return channel + ": unsolicited End"
            if reason.startswith(channel + ":") and "earlier request" in reason:
                return channel + ": earlier request never ended"
        return reason or "connection lost"

    def _prune_reconnect_history(self, now: datetime) -> None:
        self._reconnect_history = {reason: [at for at in times if now - at < RECONNECT_REASON_WINDOW]
            for reason, times in self._reconnect_history.items()}
        self._reconnect_history = {reason: times for reason, times in self._reconnect_history.items() if times}

    def _record_reconnect_attempt(self, now: datetime, reason: str) -> None:
        self._prune_reconnect_history(now)
        key = self._reconnect_reason_key(reason)
        self._reconnect_history.setdefault(key, []).append(now)
        self.store.audit(now, "RECONNECT_ATTEMPT", reason=reason, reason_key=key,
                         attempts_in_window=len(self._reconnect_history[key]), window_seconds=300)

    def _reconnect_failure(self, now: datetime, reason: str) -> None:
        self._reconnect_episode = True
        self._reconnect_reason = reason or "connection recovery failed"
        self._reconnect_failures += 1
        delay = min(120, 30 * 2 ** min(2, self._reconnect_failures - 1))
        self._reconnect_next_at = now + timedelta(seconds=delay)
        self.store.audit(now, "RECONNECT_BACKOFF", reason=self._reconnect_reason,
                         consecutive_failures=self._reconnect_failures, seconds=delay)

    def _stop_automatic_reconnect(self, now: datetime) -> None:
        if self._reconnect_stopped:
            return
        self._reconnect_stopped = True
        self.flags.broker_connected = False
        self.flags.reconcile_ok = False
        reason = self._reconnect_reason or "connection lost"
        body = (f"原因：{reason}；同一原因在 5 分钟内已自动重连 2 次，"
                "本次候选未再发起连接；已停止自动重连。")
        action = ("1. 按 Ctrl+C 停止 V6-Lite，等待正常 SHUTDOWN 完成；"
                  "2. 在 TWS 人工核对持仓、原 GTC 保护单和全部工作单，必要时人工处置风险；"
                  "3. 排除连接原因后再由 John 重启，完整核验持仓、工作单、成交三项证据后恢复。")
        card = AlertCard("🔴 P0 · 连接反复重建，已停止自动重连", P0_RED,
                         (("发生了什么", body),
                          ("影响", "全部入场保持阻断；当前券商端保护状态需人工核实，不能将旧缓存当作有效保护。"),
                          ("你要做什么", action)))
        self.store.audit(now, "RECONNECT_STOPPED", reason=reason, reason_key=self._reconnect_reason_key(reason))
        self.alerter.send(now, "P0", "connection rebuilds stopped", body + " " + action, card=card)

    def _maintain_connection(self, now: datetime) -> None:
        socket_up = self.broker.is_connected()
        if self._reconnect_stopped:
            self.flags.broker_connected = False
            self.flags.reconcile_ok = False
            return
        if not socket_up and self._runtime_loop_active:
            # A native disconnect may already have been drained inside a
            # query, or follow 1100 on a formerly live socket. Its physical
            # first-wait gate is independent of the backend-loss episode.
            self.connection_lost()
        if socket_up and not self._broker_synchronized():
            self.flags.broker_connected = False
            self.flags.reconcile_ok = False
            return  # no fresh query, reconnect, or authority during a handshake
        if (self.flags.broker_connected and socket_up and not self._link_down and
                not self._recovery_pending):
            return
        if self.cfg.test.enabled and not self.dry_run and (not socket_up or self._link_down):
            self._invalidate_test_entry_ready(now, "refused:test_entry_broker_disconnected")
        self.flags.broker_connected = False
        self.flags.reconcile_ok = False
        urgent = getattr(self.broker, "set_urgent_handler", None)
        if urgent is not None and not self.dry_run:
            urgent(None)
        deadlines = getattr(self.broker, "set_protection_deadline_handler", None)
        if deadlines is not None and not self.dry_run:
            deadlines(lambda: self._preflight_protection_deadlines(self.clock.now()))
        if socket_up and self._link_down:
            return
        if self._reconnect_next_at is not None and now < self._reconnect_next_at:
            return
        if self._last_reconnect and now - self._last_reconnect < RECONNECT_EVERY:
            return
        reason = self._reconnect_reason or getattr(self.broker, "connection_rebuild_reason", "") or "connection lost"
        self._reconnect_reason = reason
        self._prune_reconnect_history(now)
        if not socket_up and len(self._reconnect_history.get(self._reconnect_reason_key(reason), ())) >= RECONNECT_REASON_LIMIT:
            self._stop_automatic_reconnect(now)
            return
        self._last_reconnect = now
        physical_attempt = not socket_up
        try:
            if not socket_up:
                self.broker.connect()          # a fresh connection re-subscribes everything
                self._link_down = False
            else:
                # link restored on a live socket (1101 "data lost" / 1102):
                # market data and account stream must be requested again
                refresh = getattr(self.broker, "refresh_subscriptions", None)
                if refresh is not None:
                    self._protect("query")
                    refresh()
            if self.cfg.account not in self.broker.managed_accounts():
                raise BrokerError("account missing after reconnect")
            if not self._broker_synchronized():
                raise BrokerError("broker connection synchronization incomplete after reconnect")
            self.flags.broker_connected = True
            recovered = True
            if not self.dry_run:
                recovered = self._recover(now)
                self._protection_progress(self.clock.now())
            self._reconcile(now)
            if not recovered:
                self._recovery_pending = True
                self.flags.reconcile_ok = False
            if not self.broker.is_connected():
                raise BrokerError(getattr(self.broker, "connection_rebuild_reason", "") or
                                  "socket lost during reconnect recovery")
            if physical_attempt:
                self._record_reconnect_attempt(now, reason)
            if recovered and self.flags.reconcile_ok:
                self._recovery_pending = False
                self._recovery_failure_reason = ""
                self._reconnect_failures = 0
                self._reconnect_next_at = None
                self._physical_loss_wait_started = None
                self._reconnect_episode = False
                self._reconnect_reason = ""
            else:
                self._reconnect_failure(self.clock.now(), reason)
            if urgent is not None and not self.dry_run:
                urgent(self._on_urgent_broker_event)
            if deadlines is not None and not self.dry_run:
                deadlines(lambda: self._hard_protection_deadlines(self.clock.now()))
            self.alerter.resolve("P1", "IBKR disconnected")      # a new loss is paged again at once
            self.alerter.send(now, "INFO", "IBKR reconnected", f"reconcile_ok={self.flags.reconcile_ok}",
                              remote=self._disconnect_matters(now))
        except Exception as exc:
            self.flags.broker_connected = False
            failed_reason = getattr(self.broker, "connection_rebuild_reason", "") or str(exc)
            if physical_attempt:
                self._record_reconnect_attempt(now, failed_reason)
            self._reconnect_failure(self.clock.now(), failed_reason)
            log.warning("reconnect failed: %s", exc)

    def _disconnect_matters(self, now: datetime) -> bool:
        """Disconnects during RTH, or with orders in flight, page the operator;
        the nightly Gateway restart is only logged."""
        return is_rth(now) or bool(self.store.active_intents()) or bool(self.store.open_positions())

    def _refresh_equity(self, now: datetime, force: bool = False) -> None:
        if not force and self._last_equity and now - self._last_equity < EQUITY_REFRESH:
            return
        self._last_equity = now
        try:
            self.flags.equity = sizing_equity(self.cfg, self.broker.net_liquidation())
        except Exception as exc:
            log.warning("equity refresh failed: %s", exc)
            self.flags.equity = None
        if not is_rth(now):
            return
        funds = None
        try:
            funds = self.broker.available_funds()
            quiet = funds.seen_at is None or now - funds.seen_at > FUNDS_REFRESH_AFTER
        except Exception:
            quiet = True
        # Position evidence and account-value change notifications are separate
        # streams. Never infer a broken subscription merely from field silence.
        routine = (funds is not None and funds.ready and funds.value is not None and
                   funds.as_of is not None and funds.seen_at is not None and
                   timedelta(0) <= now - funds.seen_at <= FUNDS_MAX_AGE and
                   self.flags.reconcile_ok)
        refresh = getattr(self.broker, "refresh_funds", None)
        recently = self._last_account_refresh and now - self._last_account_refresh < FUNDS_REFRESH_AFTER
        if quiet and refresh is not None and not recently:
            self._last_account_refresh = now
            try:
                refresh()  # sending a request never changes freshness evidence
                logger = log.debug if routine else log.warning
                logger("[ACCOUNT_STREAM] silent since %s; funds snapshot requested%s",
                       funds.seen_at if funds else None,
                       "" if routine else "; funds/readiness evidence requires verification")
            except Exception as exc:
                log.warning("[ACCOUNT_STREAM] funds snapshot failed: %s", exc)

    def _refresh_approval(self, now: datetime) -> str | None:
        """Live approval is re-validated every tick and at the final send
        boundary: expiry or `revoke-live` blocks new entries immediately."""
        if not self.cfg.is_live:
            self.flags.approval_error = None
            return None
        if self.approval_checker is None:
            self.flags.approval_error = "no_approval_checker"
            return self.flags.approval_error
        try:
            self.approval_checker(now)
            self.flags.approval_error = None
        except Exception as exc:
            if self.flags.approval_error is None:
                self.alerter.send(now, "P1", "live approval no longer valid",
                                  f"{exc}. New entries blocked; exits continue.")
            self.flags.approval_error = str(exc)[:160] or "invalid"
        return self.flags.approval_error

    # ================================================================ recovery
    def _order_evidence(self) -> tuple[set[str], list[FillEvent]]:
        """Fresh working-order list FIRST, then executions. Any fill that
        completes an order before it leaves the working list is therefore
        already recorded when executions are read. Raises if either query
        fails -- callers must then keep the order UNKNOWN. Working orders the
        ledger does not know (or considers finished) are flagged as orphans."""
        self._protect("query")
        orders = self.broker.open_orders()
        self._note_working(self.clock.now(), orders)
        self._protect("query")
        fills = self.broker.executions_today()
        return {o.intent_id for o in orders if o.ours}, fills

    def _note_working(self, now: datetime, orders: list[OpenOrder]) -> None:
        """Every fresh working-order list is kept (share reservation for
        SELLs) and checked for orders the ledger cannot explain."""
        self._working = {o.intent_id: o for o in orders}
        self._working_at = now
        if self.cfg.protection.enabled and not self.dry_run:
            managed = {p["symbol"] for p in self.store.open_positions()}
            if any(o.symbol in managed and o.side == "SELL" and not o.ours for o in orders):
                self._protection_priority_due = True
        # a combo with an unresolved leg blocks every SELL (no stop could be
        # sent): new positions are blocked for as long as it is listed
        combos = sorted(o.intent_id for o in orders if o.symbol == "*")
        if combos and not self.flags.unresolved_combos:
            self.alerter.send(now, "P1", "unresolved combo order working",
                              f"{', '.join(combos)}: every V6-Lite SELL (including stops) and every release is "
                              "blocked, and new entries are blocked, until it is cancelled or filled in TWS")
        self.flags.unresolved_combos = combos
        self._check_orphans(now, orders)

    def _alert_once(self, now: datetime, key: str, level: str, subject: str, body: str) -> None:
        if key in self._alerted_once:
            return
        self._alerted_once.add(key)
        self.alerter.send(now, level, subject, body)

    def _check_orphans(self, now: datetime, orders: list[OpenOrder]) -> None:
        """A V6-Lite order working at the broker that this ledger does not
        track (other state dir, lost/restored state, crash between send and
        write) is invisible to sizing, funds and exits: block new risk and
        page the operator. It is never cancelled or taken over automatically."""
        managed = {r["symbol"] for r in self.store.open_positions()}
        for order in orders:
            if not order.ours:
                if order.symbol in managed:
                    self._alert_once(now, f"foreign_order:{order.intent_id}", "P2",
                                     f"non-V6-Lite order on managed {order.symbol}",
                                     f"{order.intent_id} {order.side or '?'} remaining {order.remaining:g} is working; "
                                     "its shares are reserved and V6-Lite will not sell them")
                continue
            row = self.store.intent(order.intent_id)
            if row is not None and row["hold_reason"] and IntentStatus(row["status"]) not in TERMINAL_STATUSES:
                # evidence contradicts the hold: the order IS working. Its
                # remaining shares are reserved; the operator decides.
                if row["purpose"] == Purpose.PROTECT and self._protection_order_has_p0(order.intent_id):
                    continue
                self._alert_once(now, f"held_working:{order.intent_id}", "P1",
                                 f"held order is working {order.symbol}",
                                 f"{order.intent_id} ({row['purpose']}) was held as unverifiable but the broker "
                                 f"reports it working with {order.remaining:g} left; those shares are reserved")
                continue
            if row is not None and IntentStatus(row["status"]) not in TERMINAL_STATUSES:
                continue
            if self.dry_run:
                # a rehearsal ledger never knows the formal orders; not its business
                self._dry_log(now, f"orphan:{order.intent_id}",
                              f"broker has V6-Lite order {order.intent_id} {order.symbol} (formal state)")
                continue
            state = "unknown to this ledger" if row is None else f"settled {row['status']} in this ledger"
            code = f"orphan_order:{order.intent_id}"
            if self.store.control(f"anomaly:{code}") is not None:
                continue
            with self.store.tx():
                self.store.raise_anomaly(now, code, f"{order.symbol} order {order.broker_order_id} is working at "
                                                    f"the broker but {state}")
            self.alerter.send(now, "P1", f"orphan V6-Lite order {order.symbol}",
                              f"{order.intent_id} ({order.status}, remaining {order.remaining:g}) is {state}. "
                              "New entries are blocked. Cancel it in TWS (or restore the right state "
                              "directory), then `clear-anomaly`.")

    def _recover(self, now: datetime) -> bool:
        """Rebuild order truth from the broker after start or reconnect."""
        targets = [r["intent_id"] for r in self.store.active_intents()
                   if r["purpose"] != Purpose.PROTECT]   # GTC has its own recovery path
        try:
            working, fills = self._order_evidence()
        except Exception as exc:
            self._recovery_pending = True
            self._recovery_failure_reason = str(exc)
            self.flags.reconcile_ok = False
            with self.store.tx():
                self.store.raise_anomaly(now, "recovery_failed", str(exc))
            for intent in self.store.active_intents():
                if intent["purpose"] == Purpose.PROTECT:
                    continue
                if intent["status"] != IntentStatus.UNKNOWN:
                    self.store.update_intent(now, intent["intent_id"], status=IntentStatus.UNKNOWN,
                                             note="recovery_evidence_unavailable")
            self.alerter.send(now, "P1", "recovery failed", str(exc))
            return False
        self._recovering = True
        try:
            for fill in fills:
                self._ingest_fill(now, fill)
        finally:
            self._recovering = False
        for iid in targets:
            intent = self.store.intent(iid)
            if intent is None or IntentStatus(intent["status"]) in TERMINAL_STATUSES:
                continue
            self._decide(now, intent, working, recovered=True)
        if self.cfg.protection.enabled and not self.dry_run:
            self._protection_priority_due = True
        return True

    def _decide(self, now: datetime, intent, working: set[str], *, recovered: bool = False) -> None:
        """Final state of ONE intent from evidence that covers it.

        * still working at the broker          -> SENT / PARTIAL
        * executions prove the full quantity   -> FILLED
        * operator hold                        -> untouched (operator only)
        * created in an earlier session        -> hold: today's executions
          cannot prove what happened then (also for a running engine that
          carried the intent past midnight, and at shutdown)
        * otherwise                            -> CANCELLED (keeps any partial)
        """
        iid = intent["intent_id"]
        if iid in working:
            if intent["hold_reason"]:
                return
            status = IntentStatus.PARTIAL if self.store.intent_filled_qty(iid) else IntentStatus.SENT
            self.store.update_intent(now, iid, status=status, cancel_seen_at=None,
                                     sent_at=intent["sent_at"] or now,
                                     note="recovered_working_order" if recovered else "still_working")
            return
        filled = self.store.intent_filled_qty(iid)
        if filled >= intent["quantity"]:
            self._finalize_intent(now, iid, IntentStatus.FILLED, note="recovered_filled" if recovered else None)
            return
        if intent["hold_reason"]:
            return
        if trading_day(datetime.fromisoformat(intent["created_at"])) < trading_day(now):
            if self.store.hold_intent(now, iid, "prior_day_unverifiable"):
                self.alerter.send(now, "P1", f"unverifiable order from a prior session {iid}",
                                  f"{intent['symbol']} {intent['purpose']} {intent['quantity']}: filled "
                                  f"{filled} per today's evidence. Check the IBKR trade history, then run "
                                  "`resolve-intent`; a quantity that still differs freezes the symbol (flatten, then `release`). "
                                  "New entries stay blocked until then.")
            return
        self._finalize_intent(now, iid, IntentStatus.CANCELLED, note="recovered_not_working" if recovered else None)

    # ================================================================ intents
    def _resolve_intents(self, now: datetime) -> None:
        for intent in self.store.active_intents():
            if intent["purpose"] == Purpose.PROTECT:
                continue  # a GTC protection order has no fill timeout
            iid = intent["intent_id"]
            status = IntentStatus(intent["status"])
            if intent["hold_reason"]:
                continue  # only the operator can resolve these
            seen = intent["cancel_seen_at"]
            if seen and now - datetime.fromisoformat(seen) >= timedelta(seconds=self.cfg.cancel_grace_seconds):
                self._settle(now, iid)
                continue
            if status in {IntentStatus.SENT, IntentStatus.PARTIAL} and intent["sent_at"]:
                timeout = (self.cfg.entry_fill_timeout_seconds if intent["purpose"] == Purpose.ENTRY
                           else self.cfg.exit_fill_timeout_seconds)
                if now - datetime.fromisoformat(intent["sent_at"]) >= timedelta(seconds=timeout):
                    self._request_cancel(now, iid, "fill_timeout")
            elif status is IntentStatus.CANCEL_REQUESTED and intent["cancel_requested_at"]:
                if now - datetime.fromisoformat(intent["cancel_requested_at"]) >= CANCEL_ACK_TIMEOUT:
                    self._settle(now, iid)
            elif status is IntentStatus.UNKNOWN:
                if now - datetime.fromisoformat(intent["updated_at"]) >= UNKNOWN_RETRY:
                    self._settle(now, iid)

    def _request_cancel(self, now: datetime, intent_id: str, reason: str) -> None:
        """Broker write path #2 (cancel)."""
        existing = self.store.intent(intent_id)
        if existing is not None and existing["purpose"] == Purpose.PROTECT:
            log.warning("refusing generic cancel of GTC protection %s reason=%s", intent_id, reason)
            return
        if self.dry_run:
            log.info("[DRY_RUN] would cancel %s (%s)", intent_id, reason)
            return
        if existing is None:
            try:
                self._protect("query")
                matches = [o for o in self.broker.open_orders() if o.intent_id == intent_id and o.ours]
            except Exception:
                matches = []
            if len(matches) != 1 or matches[0].tif == "GTC" or matches[0].order_type in {"STP", "STP LMT"}:
                order = matches[0] if len(matches) == 1 else None
                symbol = order.symbol if order else "待核实"
                body = (f"{symbol} orderRef={intent_id} orderId={order.broker_order_id if order else '-'} "
                        f"permId={order.perm_id if order else '-'}；持仓数量、保护单剩余、"
                        "止盈在途、外部卖单、未保护数量均待核实；"
                        "本地没有对应订单记录，泛型撤单已拒绝。")
                card = AlertCard("🔴 P0 · 未认领订单禁止泛型撤单", P0_RED,
                                 (("发生了什么", body), ("影响", "可能是仍在券商端工作的 GTC 保护单。"),
                                  ("你要做什么", "1）在 TWS 核对订单号与持仓；"
                                   "2）保留未知 GTC；3）由 John 决定人工处置并重新对账。")))
                self.alerter.send(now, "P0", f"orphan cancel refused {symbol}", body, card=card)
                return
        held = self.store.intent(intent_id)
        held = bool(held is not None and held["hold_reason"])
        try:
            self._protect("query")             # the adapter may need a server lookup
            self.broker.cancel(intent_id)
        except BrokerError as exc:
            if held:
                log.info("cancel of held %s not possible (%s); stays with the operator", intent_id, exc)
                return
            log.warning("cancel %s failed (%s); settling from evidence", intent_id, exc)
            self._settle(now, intent_id)
            return
        if held:
            return                              # an operator hold keeps its state
        intent = self.store.intent(intent_id)
        if intent is not None and IntentStatus(intent["status"]) not in TERMINAL_STATUSES:
            self.store.update_intent(now, intent_id, status=IntentStatus.CANCEL_REQUESTED,
                                     cancel_requested_at=now, note=reason)

    def _settle(self, now: datetime, intent_id: str) -> None:
        """Decide an order's final state only from complete broker evidence."""
        if self.dry_run:
            return
        try:
            working, fills = self._order_evidence()
        except Exception as exc:
            log.warning("settle %s deferred: %s", intent_id, exc)
            intent = self.store.intent(intent_id)
            if intent and intent["status"] != IntentStatus.UNKNOWN:
                self.store.update_intent(now, intent_id, status=IntentStatus.UNKNOWN,
                                         note=f"settle_failed:{str(exc)}")
                self.alerter.send(now, "P1", f"order state unknown {intent_id}",
                                  "entries blocked until the order state is proven")
            elif intent:
                self.store.update_intent(now, intent_id, note=f"settle_failed:{str(exc)}")
            return
        for fill in fills:
            self._ingest_fill(now, fill)
        intent = self.store.intent(intent_id)
        if intent is None or IntentStatus(intent["status"]) in TERMINAL_STATUSES:
            return
        self._decide(self.clock.now(), intent, working)

    def _verify_queried_execution_times(self, now, fills):
        """Forward known duplicates to Store, without new economic booking.

        Only a completed, current client query reaches this method. Unknown
        rows remain owned by the existing recovery/late-fill paths.
        """
        # Incidental protection/recovery queries may resolve a marker, but
        # an inconclusive one must not consume the first scheduled post-callback
        # verification opportunity. Only the dedicated attempt owns its retry clock.
        seen = set()
        for fill in fills or ():
            seen.add(fill.exec_id)
            if (self.store.control(f"execution_time_unverified:{fill.exec_id}") is not None and
                    self.store.conn.execute("SELECT 1 FROM fills WHERE exec_id=?", (fill.exec_id,)).fetchone()):
                self._ingest_fill(now, fill)
        for pending in self.store.conn.execute(
                "SELECT f.exec_id,f.time FROM fills f JOIN controls c "
                "ON c.key='execution_time_unverified:'||f.exec_id WHERE f.trading_day=?",
                (trading_day(now).isoformat(),)).fetchall():
            if pending["exec_id"] in seen:
                continue
            review = f"execution_time_review:{pending['exec_id']}"
            if self.store.control(review) is None:
                self.store.set_control(now, review, json.dumps({"recorded": pending["time"],
                    "reported": None, "new_unverified": True}))
                self.store.audit(now, "EXECUTION_TIME_REVIEW_REQUIRED", exec_id=pending["exec_id"],
                    recorded=pending["time"], reported=None, raw_time=None, source="reqExecutions",
                    reason="complete current-day query did not include the pending execId; no economic rebooking")

    def _refresh_execution_times(self, now):
        if self.dry_run or self._in_urgent_callback or not self.broker.is_connected():
            return
        today = trading_day(now).isoformat()
        pending = self.store.conn.execute(
            "SELECT 1 FROM fills f JOIN controls c ON c.key='execution_time_unverified:'||f.exec_id "
            "WHERE f.trading_day=? LIMIT 1", (today,)).fetchone()
        if pending is None or (self._last_execution_time_query is not None and
                now - self._last_execution_time_query < FILL_REFRESH_EVERY):
            return
        self._last_execution_time_query = now
        self._protect("query")
        try:
            fills = self.broker.executions_today()
        except Exception as exc:
            log.warning("execution time verification query deferred: %s", exc)
            return
        self._verify_queried_execution_times(self.clock.now(), fills)

    def _refresh_fills(self, now: datetime, *, force: bool = False) -> bool:
        """Pull executions without finalizing anything (late-fill discovery).
        Returns True only if a query actually succeeded now."""
        if self.dry_run:
            return False
        if not force and self._last_fill_refresh and now - self._last_fill_refresh < FILL_REFRESH_EVERY:
            return False
        self._last_fill_refresh = now
        try:
            self._protect("query")
            for fill in self.broker.executions_today():
                self._ingest_fill(self.clock.now(), fill)
            return True
        except Exception as exc:
            log.warning("execution refresh failed: %s", exc)
            return False

    def _finalize_intent(self, now: datetime, intent_id: str, final: IntentStatus, note: str | None = None) -> None:
        intent = self.store.intent(intent_id)
        if intent is None or IntentStatus(intent["status"]) in TERMINAL_STATUSES:
            return
        self.store.update_intent(now, intent_id, status=final, note=note or intent["note"])
        purpose = Purpose(intent["purpose"])
        filled = self.store.intent_filled_qty(intent_id)
        log.info("[INTENT_FINAL] %s %s %s filled=%s/%s", intent_id, intent["symbol"], final, filled,
                 intent["quantity"])
        if purpose is Purpose.ENTRY or not intent["position_id"]:
            return
        pid = intent["position_id"]
        stage = self.store.stage(pid, purpose)
        pos = self.store.position(pid)
        if stage is None or stage["status"] != "PENDING" or pos is None:
            return
        if stage_left(stage, pos["qty_open"]) <= 0:
            be = breakeven_stop(self.cfg, pos["entry_price"]) if purpose is Purpose.TP1 else None
            self.store.finish_stage(now, pid, purpose, "DONE", breakeven=be)
            if purpose in TP_PURPOSES:
                execution = self.store.conn.execute(
                    "SELECT SUM(f.quantity) AS qty,SUM(f.quantity*f.price)/SUM(f.quantity) AS avg "
                    "FROM fills f JOIN intents i ON f.intent_id=i.intent_id "
                    "WHERE i.position_id=? AND i.purpose=? AND f.side='SELL'", (pid, str(purpose))).fetchone()
                if not execution['qty']:
                    return  # no fill card for a stage settled by another exit
                quantity, average = int(execution['qty']), float(execution['avg'])
                title = f"✅ {purpose} 已成交 · {intent['symbol']} · {quantity} 股 @{average:.2f}"
                detail = f"本档累计成交 {quantity} 股，成交均价 {average:.2f}，剩余持仓 {pos['qty_open']} 股。"
                self.alerter.send(now, "INFO", f"{intent['symbol']} {purpose} done", detail,
                    card=AlertCard(title, INFO_GREEN, (("成交", detail), ("订单", intent_id))))
            else:
                self.alerter.send(now, "INFO", f"{intent['symbol']} {purpose} done", f"filled {stage['filled_qty']}")

    # ================================================================ reconcile
    def _fresh_positions(self) -> list:
        """reqPositions (server) -- remembered per symbol for the send boundary."""
        self._protect("query")
        self._account_stream_flat_at = None  # failed queries cannot leave flat evidence
        try:
            positions = self.broker.positions_fresh()
        except Exception as exc:
            if not self.dry_run:
                self.store.audit(self.clock.now(), "BROKER_QUERY_ERROR", source="positions_fresh", full_error=str(exc))
            raise
        self._fresh_seq += 1
        at = self.clock.now()
        if all(p.quantity == 0 for p in positions):
            self._account_stream_flat_at = at
        seen = {p.symbol.upper(): p.quantity for p in positions}
        for symbol in set(seen) | {r["symbol"] for r in self.store.open_positions()}:
            self._fresh_held[symbol] = (seen.get(symbol, 0.0), at)
        return positions

    def _managed_qty(self) -> dict[str, int]:
        managed: dict[str, int] = {}
        for row in self.store.open_positions():
            managed[row["symbol"]] = managed.get(row["symbol"], 0) + int(row["qty_open"])
        return managed

    def _reconcile(self, now: datetime) -> None:
        """Broker truth vs ledger, from a FRESH position query (the streamed
        cache can lag executions). A managed symbol whose difference survives
        FREEZE_AFTER and a forced execution refresh is frozen."""
        self._reconcile_due = False
        self._last_reconcile = now
        try:
            broker_positions = self._fresh_positions()
        except Exception as exc:
            self.flags.reconcile_ok = False
            self.flags.reconcile_detail = [f"positions_unavailable:{exc}"]
            return
        evidence_seq = self._fresh_seq
        if self.store.controls_with_prefix("release:"):
            latest = self._apply_releases(now)
            if latest is not None:
                broker_positions = latest       # the newest evidence, whether released or refused
                evidence_seq = self._fresh_seq
        dispositions = self.store.dispositions()
        ignored = {s for s, d in dispositions.items() if d == "ignore"}
        result = reconcile(managed=self._managed_qty(), broker=broker_positions,
                           protected=set(self.cfg.protected_symbols), ignored=ignored)
        result = self._maybe_freeze(now, result, ignored)
        orders_ok = True
        try:
            self._protect("query")
            self._note_working(self.clock.now(), self.broker.open_orders())
        except Exception as exc:
            orders_ok = False
            result.diffs.append(f"open_orders_unavailable:{str(exc)}")
        # held intents wait for the operator, they are not in flight: they must
        # not force a full reconcile every tick (a frozen symbol often has one)
        # A confirmed GTC protection order is managed by the L7 evidence
        # cadence, not a transient DAY intent requiring per-tick reconcile.
        in_flight = any(not r["hold_reason"] and r["purpose"] != Purpose.PROTECT
                        for r in self.store.active_intents())
        # a difference noted AFTER this reconcile's position read (e.g. by an
        # exit check run while the order query waited) is newer evidence than
        # `result`: it survives, keeps entries blocked and asks for a new read
        newer = {s: q for s, q in self.flags.quantity_suspect.items() if q >= evidence_seq}
        self.flags.quantity_suspect = newer
        for sym in newer:
            result.diffs.append(f"{sym}: quantity difference seen after this reconcile's position read")
        self.flags.reconcile_ok = result.ok and orders_ok and not newer
        self.flags.reconcile_detail = result.diffs
        self._mismatched = result.mismatched_symbols | self._frozen_symbols() | set(newer)
        if self.flags.reconcile_ok:
            self._reconcile_fail_streak = 0
            self.alerter.resolve("P1", "broker/runtime mismatch")
            return
        self._reconcile_fail_streak += 1
        if self._reconcile_fail_streak == 1:
            self._reconcile_fail_since = now
        # a diff can be transient right after a fill (broker position lag):
        # entries are blocked immediately, the alert fires once it persists
        persistent = now - self._reconcile_fail_since >= timedelta(seconds=10)
        recent_fill = bool(self._last_fill_at and now - self._last_fill_at < timedelta(seconds=10))
        possibly_transient = in_flight or recent_fill
        if persistent or not possibly_transient:
            self.alerter.send(now, "P1", "broker/runtime mismatch",
                              "\n".join(result.diffs) + "\nResolve with `status`, then `ignore` for unmanaged "
                              "holdings (v1 has no adopt). A managed position that stays different is frozen: "
                              "flatten it in TWS, then `release` it. Broker truth is never overwritten.")
        self._reconcile_due = possibly_transient or bool(self._mismatch_since) or bool(newer)

    def _suspect(self, symbol: str) -> None:
        """Broker and ledger quantity differ: block entries until a reconcile
        whose position read is newer than this observation."""
        self.flags.quantity_suspect[symbol] = max(self.flags.quantity_suspect.get(symbol, 0), self._fresh_seq)

    def _frozen_symbols(self) -> set[str]:
        return {k.split(":", 1)[1] for k in self.store.controls_with_prefix("frozen:")}

    def _maybe_freeze(self, now: datetime, result, ignored: set[str]):
        managed = self._managed_qty()
        frozen = self._frozen_symbols()
        # frozen symbols are the operator's now: no freeze clock, no forced
        # reconcile every tick (entries stay blocked by the freeze itself)
        diff = {s for s in result.mismatched_symbols if s in managed and s not in frozen}
        for sym in list(self._mismatch_since):
            if sym not in diff:
                del self._mismatch_since[sym]
        for sym in diff:
            self._mismatch_since.setdefault(sym, now)
        due = [s for s in diff if now - self._mismatch_since[s] >= FREEZE_AFTER]
        if not due or self.dry_run:
            return result
        # last chance: a forced execution refresh and a fresh position read.
        # Freeze only on COMPLETE fresh evidence; the fresh positions are
        # used even if the execution query failed (it may have booked the
        # missing fill before failing), but a difference that survives only
        # on partial evidence defers the freeze (entries and this symbol's
        # exits stay blocked meanwhile) up to FREEZE_EVIDENCE_GRACE.
        refreshed = self._refresh_fills(self.clock.now(), force=True)
        try:
            positions = self._fresh_positions()
        except Exception:
            positions = None
        if positions is not None:
            result = reconcile(managed=self._managed_qty(), broker=positions,
                               protected=set(self.cfg.protected_symbols), ignored=ignored)
        now = self.clock.now()
        for sym in due:
            if positions is not None and sym not in result.mismatched_symbols:
                self._mismatch_since.pop(sym, None)
                continue
            complete = refreshed and positions is not None
            if not complete and now - self._mismatch_since[sym] < FREEZE_AFTER + FREEZE_EVIDENCE_GRACE:
                self._alert_once(now, f"freeze_deferred:{sym}:{self._mismatch_since[sym]:%H%M%S}", "P1",
                                 f"{sym} freeze check deferred",
                                 "the quantity difference persists but the broker evidence is incomplete "
                                 "(executions or positions unavailable); no order for it until explained")
                continue
            detail = "; ".join(d for d in result.diffs if d.startswith(sym)) or "quantity difference"
            if not complete:
                detail += " (broker evidence still incomplete after the grace period)"
            self._freeze(now, sym, detail)
            self._mismatch_since.pop(sym, None)
        return result

    def _freeze(self, now: datetime, symbol: str, detail: str) -> None:
        with self.store.tx():
            self.store.set_control(now, f"frozen:{symbol}", json.dumps({"at": now.isoformat(), "detail": detail}))
            self.store.raise_anomaly(now, f"frozen:{symbol}", f"quantity cannot be explained: {detail}")
        active = [r for r in self.store.active_intents() if r["symbol"] == symbol]
        mine = [r["intent_id"] for r in active if not r["hold_reason"]
                and r["purpose"] != Purpose.PROTECT]
        for iid in mine:            # withdraw our own live orders: the operator must not race them in TWS
            self._enqueue(f"cancel:{iid}", lambda iid=iid: self._request_cancel(self.clock.now(), iid,
                                                                               "symbol_frozen"))
        # every OTHER order that may still trade this symbol is listed for the
        # operator (held, orphaned, foreign, combo): never cancelled here (D3)
        held = {r["intent_id"] for r in active if r["hold_reason"]}
        try:
            self._protect("query")
            orders = self.broker.open_orders()
            self._note_working(self.clock.now(), orders)
            listing_error = None
        except Exception as exc:
            orders, listing_error = [], str(exc)
        manual = []
        for order in orders:
            if order.symbol not in (symbol, "*") or order.intent_id in mine:
                continue
            if order.symbol == "*":
                kind = "combo order with an unresolved leg"
            elif order.intent_id in held:
                kind = "V6-Lite order held for the operator"
            elif order.ours:
                kind = "V6-Lite order the ledger does not track"
            else:
                kind = "non-V6-Lite order"
            manual.append(f"{order.intent_id} {order.side or '?'} {order.remaining:g} ({kind})")
        manual += [f"{iid} (held, not listed as working; verify in TWS)" for iid in sorted(held)
                   if iid not in {o.intent_id for o in orders}]
        steps = []
        if mine:
            steps.append(f"cancel requests were sent for V6-Lite's own working orders: {', '.join(mine)}")
        if manual:
            steps.append("these orders were NOT cancelled and can still trade " + symbol + ": " + "; ".join(manual))
        if listing_error:
            steps.append(f"the working-order list could not be read ({listing_error}): check every {symbol} "
                         "order in TWS")
        handoff = (" First, in TWS: " + " | ".join(steps) + ". Confirm that no order for "
                   f"{symbol} is working, THEN protect or flatten the position." if steps else
                   f" No working order for {symbol} was listed; protect or flatten the position in TWS now.")
        self.alerter.send(now, "P0", f"V6-Lite no longer manages {symbol}",
                          f"{detail}. The difference persisted after a fresh execution and position check. "
                          f"V6-Lite will not send any new order for {symbol} and new entries are blocked."
                          f"{handoff} Then run `release {symbol}` once the broker shows 0 shares and no working "
                          "order.")

    def _release_completed_cancel(self, protection):
        oid = protection['broker_order_id']
        query = getattr(self.broker, 'completed_orders', None)
        if not callable(query):
            raise CompletedEvidenceError(oid, 'query_unavailable', '缺少 completed orders 原生查询能力。')
        self._protect('query')
        try:
            snapshot = query()
        except Exception as exc:
            self.store.audit(self.clock.now(), 'BROKER_QUERY_ERROR', source='release_completed_orders',
                symbol=protection['symbol'], position_id=protection['position_id'],
                full_error=str(exc), error_type=type(exc).__name__)
            raise CompletedEvidenceError(oid, 'query_incomplete', 'completed orders 查询或原生解码未完整结束。') from exc
        # Executions, including any observed during this query, retain the
        # normal booking path. The completed aggregate never manufactures fill
        # price/time/execId and never becomes a StatusEvent/CANCEL_SEEN.
        if not self._refresh_fills(self.clock.now(), force=True):
            raise CompletedEvidenceError(oid, 'execution_refresh', '自有成交刷新或正常入账尚未完成。')
        if not isinstance(snapshot, CompletedOrdersSnapshot):
            raise CompletedEvidenceError(oid, 'query_type', 'completed orders 未返回完整原生快照。')
        if snapshot.socket_generation != getattr(self.broker, 'query_generation', None):
            raise CompletedEvidenceError(oid, 'socket_changed', '查询后的连接代次已变化，completed orders 证据失效。')
        row = self.store.protection(protection['position_id'])
        identity_fields = ('intent_id', 'account', 'client_id', 'con_id', 'broker_order_id', 'perm_id')
        if row is None or any(row[name] != protection[name] for name in identity_fields):
            raise CompletedEvidenceError(oid, 'binding_changed', '查询期间原持久化身份绑定发生变化，不从本次回报认领身份。')
        intent = self.store.intent(protection['intent_id'])
        position = self.store.position(protection['position_id'])
        if row is None or intent is None or position is None:
            raise CompletedEvidenceError(oid, 'local_missing', '保护单、intent 或持仓的持久化记录缺失。')
        return completed_cancel_proof(snapshot, row, intent, position,
            self.store.intent_filled_qty(intent['intent_id']), self.clock.now())

    def _apply_releases(self, now: datetime) -> list | None:
        """Operator `release SYMBOL` requests (after flattening in TWS).
        Applied only with FRESH evidence: executions booked, no working order
        of any origin for the symbol, fresh broker position 0, and no fill in
        the last RELEASE_QUIET. Returns the fresh positions it read (also
        when refusing), so the caller never publishes older evidence."""
        requests = self.store.controls_with_prefix("release:")
        with self.store.tx():
            for key in requests:
                self.store.delete_control(key)
        latest = None
        evidence_error = None
        orders: list[OpenOrder] = []
        if self.dry_run:
            evidence_error = "dry-run state is never released"
        else:
            try:
                self._protect("query")
                orders = self.broker.open_orders()
                self._note_working(self.clock.now(), orders)
                if not self._refresh_fills(self.clock.now(), force=True):
                    raise BrokerError("executions unavailable")
                latest = self._fresh_positions()
            except Exception as exc:
                evidence_error = f"broker evidence unavailable ({str(exc)}); retry later"
        book = {p.symbol.upper(): p.quantity for p in latest or []}
        now = self.clock.now()
        for key, raw in requests.items():
            symbol = key.split(":", 1)[1]
            try:
                if evidence_error:
                    raise StoreError(evidence_error)
                req = json.loads(raw)
                has_open = any(r["symbol"] == symbol for r in self.store.open_positions())
                frozen = self.store.control(f"frozen:{symbol}") is not None
                if not has_open and not frozen:
                    raise StoreError(f"{symbol} has no managed open position and is not frozen")
                working = [o.intent_id for o in orders if o.symbol.upper() in (symbol, "*")]
                if working:
                    raise StoreError(f"orders are still working for {symbol}: {', '.join(working)}")
                # An operator may have cancelled the managed GTC in TWS before
                # flattening. CANCEL_SEEN is not a general terminal proof: it
                # is accepted here only with exact persisted identity, no
                # operator hold, and two stable complete broker snapshots.
                release_protect_ids: set[str] = set()
                completed_proofs = []
                active_for_symbol = [r for r in self.store.active_intents()
                                     if r["symbol"] == symbol]
                active_ids = {r["intent_id"] for r in active_for_symbol}
                managed_protection_rows = []
                for protection in self.store.protections():
                    if protection["symbol"] != symbol:
                        continue
                    position = self.store.position(protection["position_id"])
                    intent = self.store.intent(protection["intent_id"])
                    if ((position is not None and position["status"] == "OPEN") or
                            protection["intent_id"] in active_ids or
                            (intent is not None and intent["hold_reason"])):
                        managed_protection_rows.append(protection)
                if has_open and self.cfg.protection.enabled and not managed_protection_rows:
                    raise StoreError("managed protection record missing; release refused")
                for protection in managed_protection_rows:
                    if protection["order_type"] != "STP":
                        self._protection_type_allowed(now, protection)
                        raise StoreError("旧类型保护单 STP LMT，需人工处理；release refused")
                    intent = self.store.intent(protection["intent_id"])
                    if intent is None or intent["hold_reason"]:
                        raise StoreError("protected GTC identity is held or missing; release refused")
                    identity_ok = (protection["order_type"] == "STP" and protection["account"] == self.cfg.account and
                                   protection["client_id"] == self.cfg.client_id and
                                   protection["con_id"] and protection["broker_order_id"] and
                                   protection["perm_id"] and
                                   intent["broker_order_id"] == protection["broker_order_id"] and
                                   intent["perm_id"] == protection["perm_id"])
                    terminal_seen = (intent["status"] == IntentStatus.CANCEL_SEEN and
                                     intent["last_raw_status"] in {"Cancelled", "ApiCancelled"} and
                                     intent["cancel_seen_at"] is not None)
                    if not identity_ok:
                        raise StoreError(f"protected GTC {protection['broker_order_id']} local identity unverified; release refused")
                    if not terminal_seen:
                        completed_proofs.append(self._release_completed_cancel(protection))
                    release_protect_ids.add(intent["intent_id"])
                if release_protect_ids:
                    if book.get(symbol, 0.0) != 0:
                        raise StoreError("broker position changed during protected release: first snapshot not flat")
                    self._protect("query")
                    first_execs = self.broker.executions_today()
                    for fill in first_execs:
                        self._ingest_fill(self.clock.now(), fill)
                    first_own_signature = sorted((e.exec_id, str(e.side), e.quantity)
                                                 for e in first_execs if e.symbol.upper() == symbol)
                    self._protect("query")
                    try:
                        first_account_execs = self.broker.account_executions_today()
                    except Exception as exc:
                        self.store.audit(self.clock.now(), "BROKER_QUERY_ERROR",
                            source="release_account_executions_today", evidence_round=1, symbol=symbol,
                            position_id=(managed_protection_rows[0]["position_id"]
                                         if len(managed_protection_rows) == 1 else None),
                            full_error=str(exc), error_type=type(exc).__name__, maybe_sent=getattr(exc, "maybe_sent", None))
                        raise StoreError(f"account execution evidence unavailable: {type(exc).__name__}") from exc
                    first_signature = sorted((e.exec_id, str(e.side), e.quantity)
                                             for e in first_account_execs if e.symbol.upper() == symbol)
                    if (not first_signature or any(not item[0] for item in first_signature) or
                            not set(first_own_signature).issubset(first_signature) or
                            not any(e.symbol.upper() == symbol and e.side == Side.SELL
                                    for e in first_account_execs)):
                        raise StoreError("protected release lacks complete execution evidence")
                    self._protect("query")
                    second_orders = self.broker.open_orders()
                    if any(o.symbol.upper() in (symbol, "*") for o in second_orders):
                        raise StoreError("protected GTC or another SELL reappeared during release")
                    if not self._refresh_fills(self.clock.now(), force=True):
                        raise StoreError("protected release execution refresh incomplete")
                    second_positions = self._fresh_positions()
                    if any(p.symbol.upper() == symbol and p.quantity != 0 for p in second_positions):
                        raise StoreError("broker position changed during protected release")
                    self._protect("query")
                    second_execs = self.broker.executions_today()
                    for fill in second_execs:
                        self._ingest_fill(self.clock.now(), fill)
                    second_own_signature = sorted((e.exec_id, str(e.side), e.quantity)
                                                  for e in second_execs if e.symbol.upper() == symbol)
                    self._protect("query")
                    try:
                        second_account_execs = self.broker.account_executions_today()
                    except Exception as exc:
                        self.store.audit(self.clock.now(), "BROKER_QUERY_ERROR",
                            source="release_account_executions_today", evidence_round=2, symbol=symbol,
                            position_id=(managed_protection_rows[0]["position_id"]
                                         if len(managed_protection_rows) == 1 else None),
                            full_error=str(exc), error_type=type(exc).__name__, maybe_sent=getattr(exc, "maybe_sent", None))
                        raise StoreError(f"account execution evidence unavailable: {type(exc).__name__}") from exc
                    second_signature = sorted((e.exec_id, str(e.side), e.quantity)
                                               for e in second_account_execs if e.symbol.upper() == symbol)
                    if (first_signature != second_signature or
                            first_own_signature != second_own_signature or
                            not set(second_own_signature).issubset(second_signature)):
                        raise StoreError("execution evidence changed during protected release")
                    orders, latest = second_orders, second_positions
                    book = {p.symbol.upper(): p.quantity for p in latest}
                    now = self.clock.now()
                    self.store.audit(now, "RELEASE_GTC_VERIFIED", symbol=symbol,
                                     protection_ids=sorted(release_protect_ids),
                                     execution_ids=[item[0] for item in second_signature])
                active_for_symbol = [r for r in self.store.active_intents() if r['symbol'] == symbol]
                if any(r["intent_id"] not in release_protect_ids for r in active_for_symbol):
                    raise StoreError(f"a ledger order for {symbol} is still in flight")
                if book.get(symbol, 0.0) != 0:
                    raise StoreError(f"broker still holds {book[symbol]:g} {symbol}: flatten it first")
                # second, independent source: the streamed position cache must
                # not contradict "flat" either (a stale cache only delays)
                cached = {p.symbol.upper(): p.quantity for p in self.broker.positions()}.get(symbol, 0.0)
                if cached != 0:
                    raise StoreError(f"the position stream still shows {cached:g} {symbol}; retry shortly")
                recent = self.store.conn.execute(
                    "SELECT MAX(time) AS t FROM fills WHERE symbol=?", (symbol,)).fetchone()["t"]
                if recent and now - datetime.fromisoformat(recent) < RELEASE_QUIET:
                    raise StoreError(f"{symbol} traded at {recent}; wait {RELEASE_QUIET.seconds}s after the last fill")
                for proof in completed_proofs:
                    if proof.snapshot.socket_generation != getattr(self.broker, 'query_generation', None):
                        raise CompletedEvidenceError(dict(proof.protection)['broker_order_id'], 'socket_changed',
                            '两次快照期间连接代次变化，completed orders 证据失效。')
                has_open = any(r['symbol'] == symbol for r in self.store.open_positions())
                if has_open:
                    self.store.release_position(now, symbol, day=trading_day(now).isoformat(),
                                                equity=self.flags.equity or 0.0, operator=req["operator"],
                                                reason=req["reason"], completed_cancel_proofs=completed_proofs)
                    body = "ledger position closed after the broker showed 0 shares; no new entries today"
                else:
                    # the frozen position was closed by its own (late) fills: the
                    # trade stands as booked (no second loss); the freeze ends
                    self.store.resolve_freeze(now, symbol, day=trading_day(now).isoformat(),
                                              equity=self.flags.equity or 0.0, operator=req["operator"],
                                              reason=req["reason"], completed_cancel_proofs=completed_proofs)
                    body = ("freeze ended: the position was already closed by booked fills and the broker shows "
                            "0 shares; the trade counts as booked, no new entries today")
                self._mismatch_since.pop(symbol, None)
                self.alerter.send(now, "P2", f"position released {symbol}",
                    f"{body}. By {req['operator']}: {req['reason']}",
                    card=AlertCard(f"✅ 已解除冻结 · {symbol} · 持仓 0 股", P2_YELLOW,
                        (("核验结果", f"{symbol}：券商持仓 0 股，工作单与成交核验通过；冻结已解除。"),
                         ("后续", "核对本机 status 与 TWS；当日不再新入场。"))))
            except (StoreError, BrokerError, TimeoutError, KeyError, ValueError, TypeError) as exc:
                with self.store.tx():
                    self.store.audit(now, "RELEASE_REFUSED", symbol=symbol, request=raw, reason=str(exc))
                explanation = getattr(exc, 'operator_reason', None)
                card = (AlertCard(f"人工释放被拒 · {symbol}", P1_ORANGE,
                    (("发生了什么", "具体原因：" + explanation), ("影响", "冻结继续，尚未释放。"),
                     ("你要做什么", f"在 TWS 核对 {symbol} 的持仓、在途单及上述缺项；补齐证据后在本机重新运行 release {symbol}。")))
                    if explanation else None)
                self.alerter.send(now, "P1", f"release refused {symbol}", str(exc), card=card)
        return latest

    # ================================================================ daily risk
    def _prior_closes(self, now: datetime, day: str, symbols: set[str], *, fetch: bool) -> dict[str, float | None]:
        """Prior-session closes, cached per day in the ledger. Fetching is a
        slow history request, so it is budgeted and retried at most every
        few minutes per symbol; the entry gate never fetches."""
        out: dict[str, float | None] = {}
        for symbol in sorted(symbols):
            cached = self.store.control(f"prior_close:{day}:{symbol}")
            if cached is not None:
                out[symbol] = float(cached)
                continue
            out[symbol] = None
            last = self._prior_close_attempt.get(symbol)
            if not fetch or (last and now - last < PRIOR_CLOSE_RETRY_EVERY):
                continue
            self._prior_close_attempt[symbol] = now
            self._protect("history")
            close = self.broker.previous_close(symbol)
            if close is not None and close > 0:
                with self.store.tx():
                    self.store.set_control(now, f"prior_close:{day}:{symbol}", repr(float(close)))
                out[symbol] = float(close)
            else:
                log.warning("prior close for %s unavailable; daily loss baseline pending", symbol)
        return out

    def _daily_risk(self, now: datetime, *, fetch: bool = False) -> None:
        day_date = trading_day(now)
        if not is_trading_day(day_date):
            return
        day = day_date.isoformat()
        if self.store.risk_day(day) is None and self.flags.equity:
            with self.store.tx():
                self.store.ensure_risk_day(day, self.flags.equity)
        row = self.store.risk_day(day)
        if row is None:
            return
        positions = self.store.positions_for_day(day)
        marks: dict[str, float | None] = {}
        exit_age = self.cfg.max_quote_age_seconds * EXIT_QUOTE_AGE_MULTIPLIER
        for pos in positions:
            quote = self.broker.quote(pos["symbol"])
            fresh = check_quote(self.cfg, quote, now, require_bid_ask=False, max_age_seconds=exit_age).allowed
            marks[pos["symbol"]] = quote.mark() if fresh else None
        prior = self._prior_closes(now, day, {p["symbol"] for p in positions}, fetch=fetch)
        result = day_pnl(self.store, day, marks=marks, prior_close=prior)
        self.flags.day_risk_error = ("prior_close:" + ",".join(result.missing_prior_close)
                                     if result.missing_prior_close else None)
        unpriced = list(result.unpriced) if is_rth(now) else []
        self.flags.unpriced_positions = unpriced
        if unpriced:
            self._unpriced_since = self._unpriced_since or now
            if now - self._unpriced_since >= UNPRICED_ALERT_AFTER:
                self.alerter.send(now, "P1", "positions without fresh prices",
                                  f"{', '.join(unpriced)}: stops cannot be evaluated. Check market data.")
        else:
            self._unpriced_since = None
            self.alerter.resolve("P1", "positions without fresh prices")
        if result.pnl is None or not is_rth(now):
            return
        loss = -result.pnl
        limit = daily_loss_limit(self.cfg, row["sod_equity"])
        if loss >= limit:
            if self.store.latch_daily_stop(now, day, f"loss {loss:.2f} >= limit {limit:.2f}"):
                self.alerter.send(now, "P1", "daily loss stop latched",
                                  f"loss {loss:.2f} >= {limit:.2f}. No new entries today; exits continue. "
                                  f"This latch cannot be cleared until the next trading day.")

    # ================================================================ exits
    def _monitor_protection_marks(self, now: datetime) -> None:
        """Observe stop crossings every tick, independently of the slower exit scan."""
        if not self.cfg.protection.enabled or self.dry_run or not is_rth(now):
            return
        max_age = self.cfg.max_quote_age_seconds * EXIT_QUOTE_AGE_MULTIPLIER
        for pos in self.store.open_positions():
            row = self.store.protection(pos["position_id"])
            if row is None:
                continue
            quote = self.broker.quote(pos["symbol"])
            if not check_quote(self.cfg, quote, now, require_bid_ask=False,
                               max_age_seconds=max_age).allowed:
                continue
            mark = quote.mark()
            if mark is not None:
                self._protection_on_mark(now, row, mark)

    def _exit_check_due(self, now: datetime) -> bool:
        return self._last_exit_check is None or self._overnight_refresh_decision_due(now) or \
            now - self._last_exit_check >= timedelta(seconds=self.cfg.exit_check_interval_seconds)

    def _evaluate_exits(self, now: datetime, *, regular: bool = False) -> None:
        """Exit DECISION pass: fast (ledger, cached positions and quotes).
        Anything that talks to the broker is queued (_protect runs it)."""
        if not is_rth(now):
            return
        if not (self._exit_check_due(now) or (regular and self._exit_tick_due)):
            return
        if self._in_exit_eval:
            return
        self._last_exit_check = now
        try:
            broker_qty = {p.symbol: p.quantity for p in self.broker.positions()}
        except Exception as exc:
            log.warning("exit check skipped, positions unavailable: %s", exc)
            return
        self._in_exit_eval = True
        try:
            for row in self.store.open_positions():
                self._evaluate_position_exit(now, row, broker_qty)
        finally:
            self._in_exit_eval = False

    def _evaluate_position_exit(self, now: datetime, row, broker_qty: dict[str, float]) -> None:
        pos = Store.view(row)
        if self.store.control(f"frozen:{pos.symbol}") is not None:
            return          # frozen: the operator handles it in TWS, then `release`
        if pos.symbol in self._mismatched:
            # never trade a symbol whose quantity does not match broker truth,
            # but keep looking for the execution that explains the difference
            # (a late fill without callback); refresh is rate-limited
            self._enqueue("refresh", lambda: self._refresh_fills(self.clock.now()))
            return
        quote = self.broker.quote(pos.symbol)
        exit_age = self.cfg.max_quote_age_seconds * EXIT_QUOTE_AGE_MULTIPLIER
        if not check_quote(self.cfg, quote, now, require_bid_ask=False, max_age_seconds=exit_age).allowed:
            return  # _daily_risk escalates positions that stay unpriced
        price = quote.mark()
        decision = decide_exit(pos, price)
        if self.cfg.protection.enabled and self._tp_paused(pos.position_id, pos.symbol):
            return
        if decision is not None and decision.stage in TP_PURPOSES and self._tp_paused(pos.position_id, pos.symbol):
            return
        if self.cfg.protection.enabled and decision is not None and decision.stage is Purpose.STOP:
            return             # broker GTC monitors STOP; no second DAY SELL
        if self.cfg.protection.enabled and decision is not None and decision.stage in TP_PURPOSES:
            protection = self.store.protection(pos.position_id)
            if self._protection_waiting_price(now, protection, decision.stage):
                return
            if protection is not None and not self._overnight_before_tp_stage(now, protection, decision.stage):
                return
        # held intents (prior-session, unverifiable) do not block the exit
        # logic; if the broker says one IS working, its shares are reserved
        # at the send boundary (_sell_capacity)
        active = [r for r in self.store.active_intents_for_position(pos.position_id)
                  if not r["hold_reason"] and r["purpose"] != Purpose.PROTECT]
        if active:
            if decision and decision.stage is Purpose.STOP:
                for intent in active:
                    if intent["purpose"] != Purpose.STOP and intent["status"] in {IntentStatus.SENT, IntentStatus.PARTIAL}:
                        iid = intent["intent_id"]
                        self._enqueue(f"cancel:{iid}", lambda iid=iid: self._request_cancel(
                            self.clock.now(), iid, "stop_supersedes_take_profit"))
            return
        # an entry order for another position on this symbol still in flight
        if any(r["symbol"] == pos.symbol and not r["hold_reason"] and r["purpose"] != Purpose.PROTECT
               for r in self.store.active_intents()):
            return
        held = broker_qty.get(pos.symbol, 0)
        if held != pos.qty_open:
            # broker and ledger disagree (e.g. a late execution without a
            # callback): learn the truth first, never trade on the difference
            log.warning("[EXIT_WAIT] %s ledger %s broker %s; refreshing executions", pos.symbol,
                        pos.qty_open, held)
            self._suspect(pos.symbol)                        # entries wait for a newer reconcile
            self._reconcile_due = True
            self._enqueue("refresh", lambda: self._refresh_fills(self.clock.now()))
            # the streamed cache can be stale; a STOP is never silenced by it:
            # it goes on to the send boundary, which takes FRESH evidence and
            # either sends (fresh == ledger) or refuses with a P0
            stop_pending = any(r["stage"] == str(Purpose.STOP) for r in self.store.pending_stages(pos.position_id))
            if not stop_pending and (decision is None or decision.stage is not Purpose.STOP):
                return
        pending = self.store.pending_stages(pos.position_id)
        if pending:
            stage_row = pending[0]
            stage = Purpose(stage_row["stage"])
            target_left = stage_left(stage_row, pos.qty_open)
            if target_left <= 0:
                be = breakeven_stop(self.cfg, pos.entry_price) if stage is Purpose.TP1 else None
                self.store.finish_stage(now, pos.position_id, stage, "DONE", breakeven=be)
                return
            if stage is not Purpose.STOP and (decision is None or decision.stage is not stage):
                # price left the take-profit zone (or the stop took over)
                if stage_row["filled_qty"] == 0:
                    self.store.conn.execute("DELETE FROM exit_stages WHERE position_id=? AND stage=?",
                                            (pos.position_id, str(stage)))
                    self.store.audit(now, "STAGE_ABANDONED", position_id=pos.position_id, stage=str(stage))
                else:
                    be = breakeven_stop(self.cfg, pos.entry_price) if stage is Purpose.TP1 else None
                    self.store.finish_stage(now, pos.position_id, stage, "DONE", breakeven=be)
                if decision is None or decision.stage is not Purpose.STOP:
                    return
                # the stop takes over in the SAME pass (no extra interval)
                row = self.store.position(pos.position_id)
                pos = Store.view(row)
                decision = decide_exit(pos, price)
                if decision is None:
                    return
                pending = None                  # fall through to start the STOP stage below
            else:
                if self.dry_run:
                    self._dry_log(now, f"retry:{pos.position_id}:{stage}",
                                  f"WOULD_RETRY {pos.symbol} {stage} {target_left}")
                    return
                if stage_row["attempts"] >= self.cfg.exit_max_retries:
                    self.store.finish_stage(now, pos.position_id, stage, "FAILED")
                    with self.store.tx():
                        self.store.raise_anomaly(now, f"exit_failed:{pos.symbol}:{stage}",
                                                 f"{target_left} shares not sold after {stage_row['attempts']} "
                                                 "attempts")
                    self.alerter.send(now, "P0" if stage is Purpose.STOP else "P1",
                                      f"{pos.symbol} {stage} NOT FILLED",
                                      f"{target_left} shares still open. Manual action required in TWS.")
                    return
                self._queue_exit(pos.position_id, stage)
                return
        if decision is None:
            return
        qty = min(decision.quantity, pos.qty_open)
        if qty <= 0:
            return
        if self._tp_start_deferred(now, pos.position_id, decision.stage,
                                   quote=quote, trigger_level=decision.trigger_level):
            return
        if self.dry_run:
            self._dry_log(now, f"exit:{pos.position_id}:{decision.stage}",
                          f"WOULD_EXIT {pos.symbol} {decision.stage} {qty} @~{price}{self._test_tag}")
            return
        if self.store.begin_stage(now, pos.position_id, decision.stage, qty):
            log.info("[EXIT_TRIGGER] %s %s price=%.2f level=%.2f qty=%s%s", pos.symbol, decision.stage,
                     price, decision.trigger_level, qty, self._test_tag)
            self._queue_exit(pos.position_id, decision.stage)

    def _queue_exit(self, position_id: str, stage: Purpose) -> None:
        if self.cfg.protection.enabled and stage is Purpose.STOP:
            return
        until = self._exit_backoff.get((position_id, str(stage)))
        if until and self.clock.now() < until:
            return                  # refused recently: retry after the back-off, not every pass
        key = f"exit:{position_id}"
        fn = lambda: self._submit_exit(position_id, stage)
        if stage is Purpose.STOP and key in self._queue:
            self._queue[key] = fn   # a STOP replaces a queued take-profit in place (order kept)
        else:
            self._enqueue(key, fn)

    def _submit_exit(self, position_id: str, stage: Purpose) -> None:
        """Queued exit. In order: honour the back-off; take FRESH broker
        evidence (working orders + positions); require broker == ledger;
        re-decide the exit with the latest quote (a stop that triggered while
        waiting replaces a queued take-profit); size against the shares no
        working SELL claims; send. Only a real broker call counts as an
        attempt; any refusal backs off and drops queued copies."""
        if self.dry_run:
            log.info("[DRY_RUN] WOULD_EXIT position=%s stage=%s; no broker or evidence write", position_id, stage)
            return
        if self.cfg.protection.enabled:
            if stage is Purpose.STOP:
                return
            self._protection_begin_tp(self.clock.now(), position_id, stage)
            return
        backoff = (position_id, str(stage))
        until = self._exit_backoff.get(backoff)
        if until and self.clock.now() < until:
            return
        if not self._exit_still_wanted(position_id, stage):
            return
        symbol = self.store.position(position_id)["symbol"]
        if stage in TP_PURPOSES and self._tp_paused(position_id, symbol):
            return
        capacity, held, detail = self._sell_capacity_fresh(symbol)
        now = self.clock.now()

        def refuse(key: str, subject: str, body: str) -> None:
            self._exit_backoff[backoff] = now + EXIT_REFUSED_BACKOFF
            self._queue.pop(f"exit:{position_id}", None)
            # judge the risk from the LATEST quote, not from the queued purpose:
            # a take-profit whose price fell through the stop while the evidence
            # was taken is now an unprotected stop (nothing is sent either way)
            latest = self._latest_exit_stage(now, position_id, stage)
            level = "P0" if latest is Purpose.STOP else "P1"    # an unprotected stop pages at once
            if latest is not stage and latest is not None:
                subject = subject.replace(f" {stage}", f" {latest}")
                key = key.replace(f":{stage}", f":{latest}")
            self._alert_once(now, key, level, subject, body)

        row = self.store.position(position_id)
        # a fresh difference is recorded BEFORE any refusal or eligibility
        # check (each could return early and lose it): it blocks entries now
        if held is not None and row is not None and row["status"] == "OPEN" and held != row["qty_open"]:
            # the fresh position contradicts the ledger (the cache may lag):
            # never size a SELL from either number; reconcile / freeze decide
            self._mismatched.add(symbol)
            self._mismatch_since.setdefault(symbol, now)
            self._suspect(symbol)                           # blocks entries in this same tick
            self._reconcile_due = True
            refuse(f"exit_blocked_qty:{position_id}:{held}", f"exit blocked {symbol} {stage}",
                   f"fresh broker position {held:g} but ledger {row['qty_open']}; no SELL until explained")
            return
        if capacity is None:
            refuse(f"exit_blocked_evidence:{position_id}:{stage}:{now:%H%M}", f"exit blocked {symbol} {stage}",
                   f"{detail}. No SELL is sent without fresh broker evidence; retrying after a back-off.")
            return
        if not self._exit_still_wanted(position_id, stage):
            return
        row = self.store.position(position_id)
        quote = self.broker.quote(symbol)
        exit_age = self.cfg.max_quote_age_seconds * EXIT_QUOTE_AGE_MULTIPLIER
        if not check_quote(self.cfg, quote, now, require_bid_ask=False, max_age_seconds=exit_age).allowed:
            return
        stage = self._redecide(now, position_id, stage, quote.mark())
        if stage is None:
            return
        backoff = (position_id, str(stage))
        row = self.store.position(position_id)
        st = self.store.stage(position_id, stage)
        target_left = stage_left(st, row["qty_open"])
        if target_left <= 0:
            return
        qty = target_left
        if qty > capacity:
            if stage is Purpose.STOP and capacity >= 1:
                self.alerter.send(now, "P1", f"{symbol} STOP reduced to {capacity}",
                                  f"{target_left} wanted, but working SELL orders already cover "
                                  f"{target_left - capacity}: {detail}")
                qty = capacity
            else:
                refuse(f"exit_blocked_capacity:{position_id}:{stage}:{capacity}", f"exit blocked {symbol} {stage}",
                       f"{target_left} to sell but only {capacity} not already covered by working SELL orders "
                       f"({detail}). Check TWS.")
                return
        # the bid only when the book itself is fresh; a book older than the
        # exit age never prices a SELL just because a newer trade made the
        # quote pass (batch 9)
        book_age = quote.age_seconds(now)
        book_fresh = quote.valid_bid_ask() and book_age is not None and book_age <= exit_age
        if stage in TP_PURPOSES:
            if not self._tp_quote_ok(now, quote):
                return
            reference = quote.bid
        else:
            reference = quote.bid if book_fresh else quote.mark()
        limit = sell_limit(reference, self.cfg.exit_limit_band_pct)
        if limit is None:
            log.warning("no exit limit for %s", symbol)
            return
        req = OrderRequest(new_id("v6l"), symbol, Side.SELL, int(qty), limit, stage, position_id)
        if stage in TP_PURPOSES and not self.dry_run:
            self.store.set_control(now, f"tp_send:{req.intent_id}", json.dumps(self._tp_pricing_snapshot(now, quote, limit)))
        result = self._send(now, req, atr=None, count_entry_day=None)
        if result in {"sent", "unknown", "rejected"}:
            self._exit_backoff.pop(backoff, None)
            self.store.bump_stage_attempt(now, position_id, stage)
        else:
            self._exit_backoff[backoff] = now + EXIT_REFUSED_BACKOFF
            self._queue.pop(f"exit:{position_id}", None)

    def _latest_exit_stage(self, now: datetime, position_id: str, stage: Purpose) -> Purpose | None:
        """What a refused SELL stands for, judged from the latest valid quote.
        PURE: nothing is begun, abandoned or sized here (the evidence that
        refused the SELL may also show the ledger is wrong); the next decision
        pass acts once evidence is complete. Falls back to the queued stage."""
        if stage is Purpose.STOP:
            return stage
        row = self.store.position(position_id)
        if row is None or row["status"] != "OPEN":
            return stage
        quote = self.broker.quote(row["symbol"])
        exit_age = self.cfg.max_quote_age_seconds * EXIT_QUOTE_AGE_MULTIPLIER
        if not check_quote(self.cfg, quote, now, require_bid_ask=False, max_age_seconds=exit_age).allowed:
            return stage
        decision = decide_exit(Store.view(row), quote.mark())
        return Purpose.STOP if decision is not None and decision.stage is Purpose.STOP else stage

    def _redecide(self, now: datetime, position_id: str, stage: Purpose, price: float | None) -> Purpose | None:
        """Final exit decision from the latest price. A STOP stays a STOP. A
        take-profit whose zone was left is abandoned; if the stop triggered
        meanwhile, the STOP stage starts and is what gets sent."""
        if stage is Purpose.STOP or price is None:
            return stage
        row = self.store.position(position_id)
        decision = decide_exit(Store.view(row), price)
        if decision is not None and decision.stage is stage:
            return stage
        st = self.store.stage(position_id, stage)
        if st["filled_qty"] == 0:
            self.store.conn.execute("DELETE FROM exit_stages WHERE position_id=? AND stage=?",
                                    (position_id, str(stage)))
            self.store.audit(now, "STAGE_ABANDONED", position_id=position_id, stage=str(stage), at_send=True)
        else:
            be = breakeven_stop(self.cfg, row["entry_price"]) if stage is Purpose.TP1 else None
            self.store.finish_stage(now, position_id, stage, "DONE", breakeven=be)
        if decision is None or decision.stage is not Purpose.STOP:
            return None
        row = self.store.position(position_id)
        qty = min(decision.quantity, row["qty_open"])
        self.store.begin_stage(now, position_id, Purpose.STOP, qty)
        st = self.store.stage(position_id, Purpose.STOP)
        if st is None or st["status"] != "PENDING":
            return None             # a FAILED/DONE stop stage is the operator's; never re-sent here
        log.info("[EXIT_TRIGGER] %s STOP at send time price=%.2f qty=%s%s", row["symbol"], price, qty,
                 self._test_tag)
        return Purpose.STOP

    def _exit_still_wanted(self, position_id: str, stage: Purpose) -> bool:
        """The queued exit is still the right thing to send, from the ledger
        and the cached broker position right now."""
        row = self.store.position(position_id)
        st = self.store.stage(position_id, stage)
        if row is None or row["status"] != "OPEN" or st is None or st["status"] != "PENDING":
            return False
        if self.store.control(f"frozen:{row['symbol']}") is not None:
            return False
        if any(r["symbol"] == row["symbol"] and not r["hold_reason"] and r["purpose"] != Purpose.PROTECT
               for r in self.store.active_intents()):
            return False
        if row["symbol"] in self._mismatched:
            return False
        try:
            held = {p.symbol: p.quantity for p in self.broker.positions()}.get(row["symbol"], 0)
        except Exception:
            return False
        if held != row["qty_open"]:
            # a difference seen here is still a difference: block entries and
            # ask for a fresh reconcile, never just drop it
            self._suspect(row["symbol"])
            self._reconcile_due = True
            if Purpose(st["stage"]) is not Purpose.STOP:
                return False            # a STOP goes on: the fresh check right after decides

        return stage_left(st, row["qty_open"]) > 0

    def _sell_capacity_fresh(self, symbol: str) -> tuple[int | None, float | None, str]:
        """FRESH evidence for a SELL: working-order list and position, both
        queried now. Any failure -> (None, None, why): no cached fallback."""
        try:
            self._protect("query")
            orders = self.broker.open_orders()
            self._note_working(self.clock.now(), orders)
            positions = self._fresh_positions()
        except Exception as exc:
            self._working_at = None                 # a failed query invalidates the old list
            return None, None, f"fresh broker query failed ({str(exc)})"
        held = {p.symbol: p.quantity for p in positions}.get(symbol, 0.0)
        capacity, detail = self._reserve(symbol, held)
        return capacity, held, detail

    def _reserve(self, symbol: str, held: float) -> tuple[int | None, str]:
        """Shares of `symbol` no working SELL claims: held - remaining of every
        listed working SELL (ours, held, orphaned, foreign, combo stock legs;
        unknown side counts as SELL) - our active SELLs not listed yet. An
        unresolvable combo order blocks every SELL."""
        reserved, parts = 0.0, []
        for order in self._working.values():
            if order.symbol == "*":
                return None, f"unexplained combo order {order.intent_id} is working; resolve it in TWS"
            if order.symbol == symbol and order.side in ("SELL", "") and order.remaining > 0:
                reserved += order.remaining
                parts.append(f"{order.intent_id} {order.remaining:g}")
        for r in self.store.active_intents():
            if r["symbol"] == symbol and r["side"] == Side.SELL and not r["hold_reason"] \
                    and r["intent_id"] not in self._working:
                left = r["quantity"] - self.store.intent_filled_qty(r["intent_id"])
                if left > 0:
                    reserved += left
                    parts.append(f"{r['intent_id']} {left} (not yet listed)")
        return int(max(0.0, held - reserved)), f"held {held:g}, reserved {reserved:g} [{'; '.join(parts) or '-'}]"

    def _sell_capacity(self, symbol: str, *, fresh: bool = False) -> tuple[int | None, str]:
        """Final-boundary check inside _send: the evidence taken by the caller
        must be recent; the smaller of the fresh and the cached position wins."""
        if fresh:
            capacity, _, detail = self._sell_capacity_fresh(symbol)
            return capacity, detail
        now = self.clock.now()
        if self._working_at is None or now - self._working_at > WORKING_MAX_AGE:
            return None, f"broker working orders unknown (last list {self._working_at})"
        fresh_held = self._fresh_held.get(symbol)
        if fresh_held is None or now - fresh_held[1] > WORKING_MAX_AGE:
            return None, "no recent fresh broker position"
        try:
            cached = {p.symbol: p.quantity for p in self.broker.positions()}.get(symbol, 0.0)
        except Exception as exc:
            return None, f"positions unavailable ({exc})"
        return self._reserve(symbol, min(cached, fresh_held[0]))

    # ================================================================ entries
    def _publish_test_entry_ready(self, now: datetime) -> None:
        self._test_entry_epoch = uuid.uuid4().hex
        with self.store.tx():
            self.store.set_control(now, "test_entry_ready", self._test_entry_epoch)

    def _invalidate_test_entry_ready(self, now: datetime, outcome: str) -> None:
        with self.store.tx():
            self.store.delete_control("test_entry_ready")
            for key in self.store.controls_with_prefix("test_entry:"):
                self.store.delete_control(key)
                self.store.audit(now, "TEST_ENTRY_RESULT", request_id=key.partition(":")[2],
                                 outcome=outcome)
            for key, raw in self.store.controls_with_prefix("test_tp_nonmarketable_once:").items():
                self.store.delete_control(key)
                try:
                    request = json.loads(raw)
                except (ValueError, TypeError):
                    request = None
                self.store.audit(now, "TEST_TP_TIMEOUT_RESULT", symbol=key.partition(":")[2],
                    request_id=request.get("request_id") if isinstance(request, dict) else None,
                    phase="refused", outcome=outcome)
        self._test_entry_epoch = None

    def _test_entry_time_refusal(self, now: datetime, requested_at: datetime | None = None) -> str | None:
        requested_at = requested_at or self._active_test_entry_at
        if requested_at is None:
            return None
        if self._test_entry_epoch is None or self.store.control("test_entry_ready") != self._test_entry_epoch:
            return "refused:test_entry_epoch_invalid"
        if requested_at.tzinfo is None or now.astimezone(ET).date() != requested_at.astimezone(ET).date():
            return "refused:test_entry_wrong_day_or_timezone"
        if not timedelta(0) <= now - requested_at <= TEST_ENTRY_TTL:
            return "refused:test_entry_expired_or_future"
        return None

    def _drop_startup_test_entries(self, now: datetime) -> None:
        """A request made while stopped must not execute on restart, even if
        it is still within the short TTL. Do this before connecting Gateway."""
        self._invalidate_test_entry_ready(now, "refused:test_entry_startup_replay")

    def _run_test_entry(self, now: datetime) -> None:
        """Claim one operator request before any broker work. Deleting the
        control first makes a crash or restart fail closed: one command can
        never submit twice. Every normal gate remains in _try_enter/_send."""
        self._test_timeout_expire(now)
        pending = self.store.controls_with_prefix("test_entry:")
        if not pending:
            return
        key = sorted(pending)[0]
        request_id = key.partition(":")[2]
        with self.store.tx():
            current = self.store.control(key)
            if current is None:
                return
            self.store.delete_control(key)
            self.store.audit(now, "TEST_ENTRY_CLAIMED", request_id=request_id)
        request = None
        try:
            request = json.loads(current)
            symbol = request["symbol"]
            if symbol not in self.cfg.tradable_symbols:
                raise ValueError("symbol is no longer tradable")
            if request.get("epoch") != self._test_entry_epoch:
                outcome = "refused:test_entry_epoch_invalid"
                candidate = None
            else:
                outcome = None
            requested_at = datetime.fromisoformat(request["at"])
            candidate = None
            if outcome is None:
                outcome = self._test_entry_time_refusal(now, requested_at)
            if outcome is None and global_entry_blocks(self.cfg, self.store, self.flags, now):
                candidate = Signal(symbol, 0, 1.0, 1.0)
            elif outcome is None:
                self._protect("history")
                bars = self.broker.daily_bars(symbol, self.cfg.bars_lookback_days)
                # IBKR can include today's unfinished daily bar. Only prior
                # completed sessions supply the ATR and reference close.
                today = trading_day(self.clock.now())
                completed = []
                for bar in bars:
                    try:
                        bar_day = date.fromisoformat(str(bar.day)[:10])
                    except ValueError:
                        bar_day = None  # synthetic FakeBroker fixtures
                    if bar_day is None or bar_day < today:
                        completed.append(bar)
                scan = score_symbol(symbol, completed, is_etf=symbol in self.cfg.etf_symbols)
                if scan.error or not all(math.isfinite(v) and v > 0 for v in (scan.price, scan.atr)):
                    outcome = f"refused:test_entry_history:{scan.error or 'invalid_price_or_atr'}"
                else:
                    candidate = Signal(symbol, scan.score, scan.price, scan.atr, tuple(scan.reasons))
            if candidate is not None:
                outcome = self._test_entry_time_refusal(self.clock.now(), requested_at)
                if outcome is None:
                    self._active_test_entry_at = requested_at
                    try:
                        outcome = self._try_enter(self.clock.now(), candidate)
                    finally:
                        self._active_test_entry_at = None
        except Exception as exc:
            log.exception("[TEST_ENTRY] request=%s failed", request_id)
            outcome = f"refused:test_entry_error:{type(exc).__name__}"
        log.info("[TEST_ENTRY] request=%s symbol=%s outcome=%s test=1", request_id,
                 request.get("symbol", "?") if isinstance(request, dict) else "?", outcome)
        self.store.audit(self.clock.now(), "TEST_ENTRY_RESULT", request_id=request_id, outcome=outcome)

    def _run_scheduled_scans(self, now: datetime) -> None:
        day_date = trading_day(now)
        if session_for(day_date) is None:
            return
        day = day_date.isoformat()
        for slot in self.cfg.scan_times_et:
            slot_dt = et_time_on(day_date, slot)
            key = f"scan:{day}:{slot}"
            if now < slot_dt or self.store.control(key):
                continue
            with self.store.tx():
                self.store.set_control(now, key, "late_skipped" if now - slot_dt > SCAN_LATE_LIMIT else "started")
            if now - slot_dt > SCAN_LATE_LIMIT:
                continue
            try:
                result = self.scan_and_enter(now)
            except Exception as exc:  # a scan failure must never kill the loop
                log.exception("scan failed")
                result = f"error:{exc}"
                self.alerter.send(self.clock.now(), "P2", "scan failed", str(exc))
            with self.store.tx():
                self.store.set_control(self.clock.now(), key, result[:200])

    def scan_and_enter(self, now: datetime) -> str:
        blocks = global_entry_blocks(self.cfg, self.store, self.flags, now)
        if blocks:
            log.info("[SCAN_SKIPPED] %s", blocks)
            return "blocked:" + ";".join(blocks)
        bars = {}
        for symbol in self.cfg.scan_symbols:
            self._protect("history")
            try:
                bars[symbol] = self.broker.daily_bars(symbol, self.cfg.bars_lookback_days)
            except Exception as exc:
                log.warning("bars %s failed: %s", symbol, exc)
        self._protect("vix")
        vix = self.broker.vix_close()
        self._protect()
        min_score = effective_min_score(vix, min_score=self.cfg.min_score,
                                        high_vix_threshold=self.cfg.high_vix_threshold,
                                        high_vix_min_score=self.cfg.high_vix_min_score)
        signals, results = generate_signals(bars, etf_symbols=self.cfg.etf_symbols, min_score=min_score)
        for res in results:
            log.info("[SCAN] %-6s %-7s score=%3s adx=%s rsi=%s vol=%s %s", res.symbol, res.signal, res.score,
                     res.adx, res.rsi, res.volume_ratio, res.error)
        self.store.audit(self.clock.now(), "SCAN", vix=vix, min_score=min_score,
                         signals=[(s.symbol, s.score) for s in signals],
                         errors={r.symbol: r.error for r in results if r.error})
        entered = []
        for sig in signals:
            if sig.symbol not in self.cfg.tradable_symbols:
                continue
            outcome = self._try_enter(self.clock.now(), sig)   # fresh time for every check
            log.info("[ENTRY_DECISION] %s score=%s -> %s%s", sig.symbol, sig.score, outcome, self._test_tag)
            if outcome == "sent":
                entered.append(sig.symbol)
            if outcome.startswith("global_block"):
                break
        return f"signals={len(signals)} entered={','.join(entered) or '-'}"

    def _entry_evidence(self) -> str | None:
        """The ONE blocking step of an entry, done FIRST: a fresh, budgeted
        working-order list. Everything the order is built from (quote, limit,
        size, eligibility) is read after it. Returns a refusal or None."""
        self._entry_evidence_at = None
        try:
            self._protect("query")
            orders = self.broker.open_orders()
            self._note_working(self.clock.now(), orders)
        except Exception as exc:
            return f"refused:working_orders_unknown:{str(exc)}"
        combos = [o.intent_id for o in orders if o.symbol == "*"]
        if combos:
            return "refused:unresolved_combo_order:" + ",".join(combos)
        self._entry_evidence_at = self.clock.now()
        return None

    def _entry_inputs(self, now: datetime, sig):
        """Symbol eligibility and a tradable quote, read from the ledger and
        the quote cache as they are NOW. Returns (refusal, None) or
        (None, (open_rows, entry_intents, mid)). Cheap and non-blocking."""
        open_rows = self.store.open_positions()
        entry_intents = [r for r in self.store.active_intents() if r["purpose"] == Purpose.ENTRY]
        sectors: dict[str, int] = {}
        for sym in [r["symbol"] for r in open_rows] + [r["symbol"] for r in entry_intents]:
            sectors[self.cfg.sector_of(sym)] = sectors.get(self.cfg.sector_of(sym), 0) + 1
        ignored = {s for s, d in self.store.dispositions().items() if d == "ignore"}
        decision = check_symbol_entry(self.cfg, self.store, symbol=sig.symbol, sector_counts=sectors,
                                      open_count=len(open_rows) + len(entry_intents), ignored=ignored)
        if not decision.allowed:
            return "blocked:" + ",".join(decision.reasons), None
        quote = self.broker.quote(sig.symbol)
        qd = check_quote(self.cfg, quote, now, require_bid_ask=True)
        if not qd.allowed:
            return "quote:" + ",".join(qd.reasons), None
        mid = quote.mid()
        if abs(mid - sig.price) / sig.price > MAX_PRICE_DRIFT_PCT:
            return f"price_drift:signal={sig.price} live={mid:.2f}", None
        return None, (open_rows, entry_intents, mid)

    def _record_dry_entry(self, now: datetime, symbol: str, quantity: int, limit: float | str,
                          risk_usd: float, gate: str) -> None:
        # One line per attempt. The day/symbol key used by _dry_log would hide
        # a changed decision at the second scheduled scan.
        data_mode = "delayed" if self.cfg.market_data_type == 3 else "live"
        message = (f"WOULD_BUY {symbol} {quantity} LMT {limit} risk=${risk_usd:.0f} "
                   f"data={data_mode} gate={gate}{self._test_tag}")
        log.info("[DRY_RUN] %s", message)
        self.store.audit(now, "DRY_RUN", message=message)

    def _entry_size(self, *, price: float, atr: float, deployed: float) -> SizeResult:
        """Use the normal risk/capital calculation as a ceiling. In test mode
        never shrink the operator's fixed quantity to fit a failing gate."""
        size = size_entry(self.cfg, equity=self.flags.equity, price=price, atr=atr,
                          deployed_notional=deployed)
        if not self.cfg.test.enabled or size.quantity < 1:
            return size
        target = self.cfg.test.qty
        if size.quantity < target:
            return SizeResult(0, "test_qty_exceeds_limits")
        return SizeResult(target, "ok", target * atr * self.cfg.test.stop_mult, target * price)

    def _dry_entry_refusal(self, now: datetime, sig, inputs, refusal: str) -> None:
        """Report a refusal without inventing a price when pre-check failed."""
        if inputs is None:
            quantity, limit, risk_usd = 0, "n/a", 0.0
        else:
            open_rows, entry_intents, mid = inputs
            limit = buy_limit(mid, self.cfg.entry_limit_band_pct)
            deployed = sum(r["qty_open"] * r["entry_price"] for r in open_rows) + \
                sum(r["quantity"] * r["limit_price"] for r in entry_intents)
            size = self._entry_size(price=limit, atr=sig.atr, deployed=deployed)
            quantity, risk_usd = size.quantity, size.risk_usd
        gate = refusal if refusal.startswith("refused:") else f"refused:{refusal}"
        self._record_dry_entry(now, sig.symbol, quantity, limit, risk_usd, gate)

    def _try_enter(self, now: datetime, sig) -> str:
        blocks = global_entry_blocks(self.cfg, self.store, self.flags, now)
        if blocks:
            refusal = "global_block:" + ";".join(blocks)
            if self.dry_run:
                self._dry_entry_refusal(now, sig, None, refusal)
            return refusal
        # cheap pre-check: a signal that cannot be entered anyway never costs
        # a blocking working-order query (up to 19 s each)
        refusal, inputs = self._entry_inputs(now, sig)
        if refusal:
            if self.dry_run:
                self._dry_entry_refusal(now, sig, None, refusal)
            return refusal
        initial_inputs = inputs
        refusal = self._entry_evidence()
        if refusal:
            if self.dry_run:
                self._dry_entry_refusal(self.clock.now(), sig, initial_inputs, refusal)
            return refusal
        # The wait is over: process events, then re-read every input after the
        # read-only working-order query, in both formal and rehearsal modes.
        now = self.clock.now()
        self._drain(now)
        blocks = global_entry_blocks(self.cfg, self.store, self.flags, now)
        if blocks:
            refusal = "global_block:" + ";".join(blocks)
            if self.dry_run:
                self._dry_entry_refusal(now, sig, initial_inputs, refusal)
            return refusal
        refusal, inputs = self._entry_inputs(now, sig)
        if refusal:
            if self.dry_run:
                self._dry_entry_refusal(now, sig, initial_inputs, refusal)
            return refusal
        open_rows, entry_intents, mid = inputs
        limit = buy_limit(mid, self.cfg.entry_limit_band_pct)
        deployed = sum(r["qty_open"] * r["entry_price"] for r in open_rows) + \
            sum(r["quantity"] * r["limit_price"] for r in entry_intents)
        size = self._entry_size(price=limit, atr=sig.atr, deployed=deployed)
        if size.quantity < 1:
            if self.dry_run:
                self._record_dry_entry(now, sig.symbol, size.quantity, limit, size.risk_usd,
                                       f"refused:{size.reason}")
            return size.reason
        req = OrderRequest(new_id("v6l"), sig.symbol, Side.BUY, size.quantity, limit, Purpose.ENTRY,
                           position_id=new_id("pos"))
        return self._send(now, req, atr=sig.atr, count_entry_day=trading_day(now).isoformat(),
                          risk_usd=size.risk_usd)

    # ================================================================ the send path
    def _send(self, now: datetime, req: OrderRequest, *, atr: float | None, count_entry_day: str | None,
              risk_usd: float = 0.0) -> str:
        try:
            return self._send_once(now, req, atr=atr, count_entry_day=count_entry_day, risk_usd=risk_usd)
        finally:
            getattr(self, "_test_timeout_sending", set()).discard(req.intent_id)

    def _send_once(self, now: datetime, req: OrderRequest, *, atr: float | None, count_entry_day: str | None,
                   risk_usd: float = 0.0) -> str:
        """Broker write path #1 (submit). Final boundary: re-reads durable
        controls, approval, funds and positions immediately before sending."""
        now = self.clock.now()                 # stamp with the real send time, not the tick start
        if self.dry_run and req.purpose is not Purpose.ENTRY:
            log.info("[DRY_RUN] would send %s %s %s %s", req.purpose, req.symbol, req.side, req.quantity)
            return "refused:dry_run"
        if req.symbol in self.cfg.protected_symbols:
            return "refused:protected_symbol"
        if req.purpose in TP_PURPOSES and self._tp_paused(req.position_id, req.symbol):
            return "refused:tp_paused_or_broker_price_mismatch"
        if self.cfg.protection.enabled and req.purpose is Purpose.STOP:
            return "refused:broker_gtc_monitors_stop"
        protection_plan = None
        if req.purpose is Purpose.ENTRY and self.cfg.protection.enabled and not self.dry_run:
            try:
                protection_plan = self._protection_plan_for_entry(req, atr)
            except Exception as exc:
                reason = type(exc).__name__
            else:
                reason = "missing ATR or valid plan" if protection_plan is None else ""
            if protection_plan is None:
                missing = {"position_id": req.position_id or "-", "symbol": req.symbol,
                           "confirmed_remaining": 0, "pending_max_remaining": 0,
                           "intent_id": "-", "broker_order_id": None, "perm_id": None,
                           "tp_intent_id": None, "trigger_price": 0.0, "limit_price": 0.0}
                self._protection_page(now, "P1", "invalid entry price", missing,
                                      f"买单已拦截，申请买入={req.quantity} 股，"
                                      f"买入限价={req.limit_price:.2f}，ATR={atr}；原因={reason}")
                return "refused:invalid_protection_price"
        if req.purpose is Purpose.ENTRY:
            refusal = self._entry_gate(now, req)
            if self.dry_run:
                self._record_dry_entry(self.clock.now(), req.symbol, req.quantity, req.limit_price,
                                       risk_usd, refusal or "ok")
                return refusal or "dry_run"
            if refusal:
                return refusal
            now = self.clock.now()             # intent and sent_at carry the time of the send itself
            refusal = self._test_entry_time_refusal(now)
            if refusal:
                return refusal
        else:
            if req.side is not Side.SELL:
                return "refused:exits_must_sell"
            # final boundary: never sell shares that the broker position does not
            # hold or that another working SELL (any origin) already claims
            capacity, detail = self._sell_capacity(req.symbol, fresh=False)
            level = "P0" if req.purpose is Purpose.STOP else "P1"     # an unsent stop pages at once
            if capacity is None:
                self._alert_once(now, f"exit_refused_unknown:{req.position_id}:{req.purpose}:{now:%H%M}", level,
                                 f"exit refused {req.symbol} {req.purpose}", f"{detail}; retrying after a back-off")
                return f"refused:working_orders_unknown:{detail}"
            if req.quantity > capacity:
                self._alert_once(now, f"exit_refused_qty:{req.position_id}:{req.purpose}:{now:%H%M}", level,
                                 f"exit refused {req.symbol} {req.purpose}",
                                 f"sell {req.quantity} > free shares {capacity} ({detail}). The smaller of the "
                                 "fresh query and the position stream is used, so a stream that lags or is stuck "
                                 "below the true position also blocks; check the position in TWS and protect it "
                                 "there if needed")
                return "refused:sell_exceeds_broker_qty"
        pricing = None
        if req.purpose in TP_PURPOSES:
            try:
                pricing = self._tp_json(f"tp_send:{req.intent_id}")
            except (ValueError, TypeError):
                pricing = None
            if not self._tp_pricing_ok(self.clock.now(), pricing, req):
                return self._tp_definitely_unsent(req, atr=atr, count_entry_day=count_entry_day,
                    reason="止盈原定价 bid/ask 已过期或无法核实；确定未提交，保留原空窗起点并恢复保护")
        if req.purpose in TP_PURPOSES:
            req, pricing = self._test_timeout_claim(self.clock.now(), req, pricing)
        self.store.create_intent(self.clock.now(), req, atr=atr, count_entry_day=count_entry_day,
                                 protection_plan=protection_plan)
        if protection_plan is not None:
            # The planned broker identity is durable before the BUY write.
            self.store.update_protection(now, req.position_id, account=self.cfg.account,
                                         client_id=self.cfg.client_id,
                                         con_id=int(self.store.control(f"conid:{req.symbol}") or 0))
        send_evidence = None
        if req.purpose in TP_PURPOSES:
            now = self.clock.now()
            if (self._tp_paused(req.position_id, req.symbol) or
                    not self._tp_pricing_ok(now, pricing, req)):
                return self._tp_definitely_unsent(req, atr=atr, count_entry_day=count_entry_day,
                    reason="止盈提交前原定价报价过期或价格复发门控已阻止；确定未提交，禁止自动改价")
            send_evidence = self._tp_record(now, self._tp_context(self.store.intent(req.intent_id)),
                "send", intent_id=req.intent_id, quantity=req.quantity, calculated_price=req.limit_price,
                sent_price=req.limit_price, pricing_snapshot=pricing, persist=False)
            if (self._tp_paused(req.position_id, req.symbol) or
                    not self._tp_pricing_ok(self.clock.now(), pricing, req)):
                return self._tp_definitely_unsent(req, atr=atr, count_entry_day=count_entry_day,
                    reason="止盈最终提交边界原定价报价已过期；确定未提交，禁止自动改价")
        try:
            ack = self.broker.submit_limit(req)
        except BrokerError as exc:
            if exc.maybe_sent:
                if send_evidence is not None:
                    self.store.audit(now, "TP_EVIDENCE", **send_evidence)
                self.store.update_intent(now, req.intent_id, status=IntentStatus.UNKNOWN, note=str(exc))
                self.alerter.send(now, "P1", f"order state unknown {req.symbol}", str(exc))
                return "unknown"
            self.store.update_intent(now, req.intent_id, status=IntentStatus.REJECTED, note=str(exc))
            if req.purpose in TP_PURPOSES:
                self._tp_record(now, self._tp_context(self.store.intent(req.intent_id)), "status",
                    intent_id=req.intent_id, error_code=0, reason=str(exc), source="submit_limit",
                    raw_status="DefinitelyUnsent", pricing_snapshot=pricing,
                    calculated_price=req.limit_price, sent_price=req.limit_price)
                if price_rejection(0, str(exc)):
                    self._tp_price_rejected(now, self.store.intent(req.intent_id), 0, str(exc))
                else:
                    self.alerter.send(now, "P1", f"exit rejected {req.symbol} {req.purpose}", str(exc))
            elif req.purpose is not Purpose.ENTRY:
                self.alerter.send(now, "P1", f"exit rejected {req.symbol} {req.purpose}", str(exc))
            return "rejected"
        if req.purpose in TP_PURPOSES:
            current = self.store.intent(req.intent_id)
            fields = {"sent_at": now}
            if current["status"] == IntentStatus.NEW and not current["hold_reason"]:
                fields["status"] = IntentStatus.SENT
            if current["broker_order_id"] is None and ack.broker_order_id:
                fields["broker_order_id"] = ack.broker_order_id
            if current["perm_id"] is None and ack.perm_id:
                fields["perm_id"] = ack.perm_id
            self.store.update_intent(now, req.intent_id, **fields)
        else:
            self.store.update_intent(now, req.intent_id, status=IntentStatus.SENT, sent_at=now,
                                     broker_order_id=ack.broker_order_id, perm_id=ack.perm_id)
        if send_evidence is not None:
            self.store.audit(now, "TP_EVIDENCE", **send_evidence)
        if req.purpose in TP_PURPOSES:
            self._test_timeout_event(self.clock.now(), self._tp_context(self.store.intent(req.intent_id)), "send", req.intent_id)
        log.info("[ORDER_SENT] %s %s %s %s LMT %.2f intent=%s%s", req.purpose, req.symbol, req.side,
                 req.quantity, req.limit_price, req.intent_id, self._test_tag)
        if req.purpose is Purpose.ENTRY:
            self.alerter.send(now, "INFO", f"BUY {req.symbol}", f"{req.quantity} LMT {req.limit_price}")
        return "sent"

    def _protection_entry_blocks(self) -> list[str]:
        # Shared with status only; this is the existing final-BUY rule.
        if not self.cfg.protection.enabled or self.dry_run:
            return []
        blocks = []
        for pos in self.store.open_positions():
            row = self.store.protection(pos["position_id"])
            if (row is None or row["state"] != "WORKING" or row["pending_qty"] is not None or
                    int(row["confirmed_remaining"]) < int(pos["qty_open"])):
                blocks.append("protection_unconfirmed:" + pos["symbol"])
        return blocks

    def _entry_gate(self, now: datetime, req: OrderRequest) -> str | None:
        """The last word before a BUY leaves the process. Everything is
        re-read here, not taken from the start of the tick: events that
        arrived during slow requests, the daily loss they imply, alert health,
        live approval, funds (minus what in-flight entries already claim)."""
        if req.side is not Side.BUY:
            return "refused:entries_are_long_only"
        if self.store.control(f"price_reissue_block:{req.symbol}") is not None:
            return "refused:broker_price_mismatch"
        if self._protection_emergency:
            return "refused:protection_callback_failure"
        protection_blocks = self._protection_entry_blocks()
        if protection_blocks:
            return "refused:" + protection_blocks[0]
        # the working-order evidence was taken at the start of THIS attempt
        # (_entry_evidence, before the quote/limit/size were read); nothing
        # blocking happened since, so it is still the newest evidence
        at = getattr(self, "_entry_evidence_at", None)
        if at is None or self.clock.now() - at > ENTRY_EVIDENCE_MAX_AGE:
            return f"refused:no_fresh_working_order_evidence:{at}"
        self._entry_evidence_at = None          # one attempt, one use
        now = self.clock.now()
        self._drain(now)
        self.flags.alerts_healthy = self.alerter.healthy(now)
        self._daily_risk(now, fetch=False)
        if self._refresh_approval(now):
            return f"refused:live_approval:{self.flags.approval_error}"
        blocks = global_entry_blocks(self.cfg, self.store, self.flags, now)
        if blocks:
            return "refused:" + ";".join(blocks)
        try:
            funds = self.broker.available_funds()
        except Exception as exc:
            return f"refused:funds_unavailable:{exc}"
        if funds is None or funds.value is None:
            return "refused:funds_unavailable"
        if not funds.ready:
            return "refused:funds_not_ready"
        if funds.seen_at is None or funds.as_of is None or now - funds.seen_at > FUNDS_MAX_AGE:
            return f"refused:funds_stale:seen_at={funds.seen_at}"
        reserved = 0.0
        for row in self.store.active_intents():
            if row["purpose"] == Purpose.ENTRY:
                left = row["quantity"] - self.store.intent_filled_qty(row["intent_id"])
                reserved += max(0, left) * row["limit_price"] + estimate_fee(left, row["limit_price"])
        for fill in self.store.entry_fills_since(funds.as_of - FUNDS_FILL_MARGIN):
            reserved += fill["quantity"] * fill["price"] + estimate_fee(fill["quantity"], fill["price"])
        needed = req.quantity * req.limit_price + estimate_fee(req.quantity, req.limit_price)
        if funds.value - reserved < needed:
            return (f"refused:insufficient_funds:available={funds.value:.2f} reserved={reserved:.2f} "
                    f"needed={needed:.2f}")
        refusal = self._test_entry_time_refusal(self.clock.now())
        if refusal:
            return refusal
        return None

    # ================================================================ shutdown / reporting
    def request_shutdown(self, source, error=None):
        from v6lite.lifecycle import request_shutdown
        return request_shutdown(self, source, error)

    def shutdown(self, grace_seconds: float = 15.0, *, source="operator", error=None) -> None:
        """Cancel DAY orders and leave confirmed GTC protection at the broker."""
        request = self.request_shutdown(source, error)
        now = self.clock.now()
        deadlines = getattr(self.broker, "set_protection_deadline_handler", None)
        if deadlines is not None:
            deadlines(None)
        self._shutting_down = True
        if self.cfg.test.enabled and not self.dry_run:
            self._invalidate_test_entry_ready(now, "refused:test_entry_shutdown")
        if self._queue:
            log.warning("shutdown drops queued broker work: %s", list(self._queue))
            self._queue.clear()     # never send a new order while going down
        if self.dry_run:
            self.store.flush_observations(now)
            self.store.audit(now, "SHUTDOWN", dry_run=True, source=request["source"])
            self.broker.disconnect()
            self._shutdown_report_after_close()
            return
        try:
            orders = self.broker.open_orders()
            self._note_working(self.clock.now(), orders)
            protect_ids = {r["intent_id"] for r in self.store.protections()}
            orphan_gtc = [o for o in orders if o.ours and o.intent_id not in protect_ids and
                          (o.tif == "GTC" or o.order_type in {"STP", "STP LMT"})]
            for order in orphan_gtc:
                body = (f"{order.symbol} orderRef={order.intent_id} orderId={order.broker_order_id or '-'} "
                        f"permId={order.perm_id or '-'} 保护单剩余={order.remaining:g} 股；"
                        "持仓数量、止盈在途、外部卖单、未保护数量均待核实；"
                        "本地没有对应保护记录，停机保留该券商卖单。")
                card = AlertCard("🔴 P0 · 停机发现未认领券商保护单", P0_RED,
                                 (("发生了什么", body), ("影响", "保护订单归属和股数需人工核实。"),
                                  ("你要做什么", "1）在 TWS 核对真实持仓与订单号；"
                                   "2）不要由 Lite 自动撤该单；3）由 John 决定处置并重新对账。")))
                self.alerter.send(now, "P0", f"orphan GTC retained {order.symbol}", body, card=card)
            working = {o.intent_id for o in orders if o.ours and o.intent_id not in protect_ids
                       and o.tif != "GTC" and o.order_type not in {"STP", "STP LMT"}}
            if self.cfg.protection.enabled:
                for row in self.store.protections():
                    pos = self.store.position(row["position_id"])
                    if pos is None or pos["status"] != "OPEN":
                        continue
                    matches = [o for o in orders if o.intent_id == row["intent_id"]]
                    if len(matches) != 1 or not self._protection_order_valid(row, matches[0]):
                        self._protection_page(now, "P0", "shutdown incomplete", row,
                                              "退出时尚未确认 GTC 身份或券商工作状态")
        except Exception as exc:
            working = None
            self.alerter.send(now, "P1", "shutdown: working orders unknown",
                              f"could not list open orders ({exc}); check TWS for v6l- orders")
        targets = set(working or ())
        targets |= {r["intent_id"] for r in self.store.active_intents()
                    if r["status"] in {IntentStatus.SENT, IntentStatus.PARTIAL}
                    and r["purpose"] != Purpose.PROTECT}
        for intent_id in sorted(targets):
            self._request_cancel(now, intent_id, "shutdown")
        waited = 0.0
        while any(r["purpose"] != Purpose.PROTECT for r in self.store.active_intents()) and waited < grace_seconds:
            self.broker.pump(1.0)
            waited += 1.0
            self._drain(self.clock.now())
        for intent in self.store.active_intents():
            if not intent["hold_reason"] and intent["purpose"] != Purpose.PROTECT:
                self._settle(self.clock.now(), intent["intent_id"])
        leftover = [r["intent_id"] for r in self.store.active_intents()
                    if r["purpose"] != Purpose.PROTECT]
        try:
            protect_ids = {r["intent_id"] for r in self.store.protections()}
            still_working = sorted(o.intent_id for o in self.broker.open_orders()
                                   if o.ours and o.intent_id not in protect_ids and
                                   o.tif != "GTC" and o.order_type not in {"STP", "STP LMT"})
        except Exception:
            still_working = ["<unknown: open orders unavailable>"]
        if leftover or still_working:
            self.alerter.send(self.clock.now(), "P1", "shutdown with unresolved orders",
                              f"ledger: {', '.join(leftover) or '-'}; broker working: "
                              f"{', '.join(still_working) or '-'}")
        self.store.flush_observations(self.clock.now())
        self.store.audit(self.clock.now(), "SHUTDOWN", source=request["source"], unresolved=leftover, broker_working=still_working)
        self.broker.disconnect()
        self._shutdown_report_after_close()

    def _shutdown_report_after_close(self) -> None:
        """Supplement a missing report only after the safe broker shutdown.

        Reporting is best effort: neither rendering nor local/remote delivery
        failures may interrupt DAY cancellation, GTC retention or disconnect.
        """
        try:
            self._end_of_day(self.clock.now(), at_shutdown=True)
        except Exception as exc:
            log.error("[SHUTDOWN_REPORT] daily report supplement failed (%s)", type(exc).__name__)

    def _end_of_day(self, now: datetime, *, at_shutdown: bool = False) -> None:
        session = session_for(trading_day(now))
        if session is None:
            return
        report_at = session.close_utc if at_shutdown else session.close_utc + DAILY_REPORT_AFTER_CLOSE
        if now < report_at:
            return
        key = f"report:{session.day.isoformat()}"
        state = self.store.control(key) or ""
        self._refresh_commission_report(now,session.day,at_shutdown=at_shutdown)
        if state.startswith("sent") or state.startswith("written_local"):
            return
        # A pending delivery already generated this day's report. Shutdown
        # supplements missing reports; only the normal scheduler may retry.
        if at_shutdown and state.startswith("pending:"):
            return
        if state.startswith("pending:"):
            _, delivery_id, queued_at = state.split(":", 2)
            if self.alerter.knows(delivery_id):
                result = self.alerter.delivery(delivery_id)
                if result is True:
                    with self.store.tx():
                        self.store.set_control(now, key, f"sent:{now.isoformat()}")
                    return
                waited = now - datetime.fromisoformat(queued_at)
                if result is None and waited < timedelta(hours=1):
                    return
                if waited < REPORT_RETRY_AFTER:
                    return
            # else: queued by a previous process -- its fate is unknowable, resend now
        from v6lite.report import daily_health_card, daily_health_report, daily_report, write_daily_report
        quote_stats = self.quote_availability_stats(session.day, include_scope=True)
        if self.report_dir:
            _,text=write_daily_report(self.store,self.cfg,session.day,self.report_dir,quotes=quote_stats)
        else:
            text=daily_report(self.store,self.cfg,session.day,quotes=quote_stats)
        remote_text = daily_health_report(
            self.store, self.cfg, session.day, status=self.status_snapshot(now),
            gateway=self.gateway_heartbeat_stats(), alerts=self.alerter.delivery_stats(session.day),
            quotes=quote_stats)
        delivery_id = self.alerter.send(now, "INFO", f"daily report {session.day.isoformat()}",
                                        remote_text, dedupe=False,
                                        card=daily_health_card(remote_text, session.day,
                                                               test_mode=self.cfg.test.enabled,
                                                               mode=self.cfg.mode, dry_run=self.dry_run))
        value = "written_local" if delivery_id is None else f"pending:{delivery_id}:{now.isoformat()}"
        if delivery_id is not None and self.alerter.delivery(delivery_id) is True:
            value = f"sent:{now.isoformat()}"
        with self.store.tx():
            self.store.set_control(now, key, value)

    def _heartbeat(self, now: datetime) -> None:
        self._gateway_heartbeat(now)
        # Inspect delayed quotes on every RTH tick so a loss crossing 120
        # seconds is noticed promptly; outside RTH use the heartbeat cadence.
        delayed_rth = self.cfg.market_data_type == 3 and is_rth(now)
        if delayed_rth:
            self._check_market_data(now)
        if not self.alerter.healthy(now) and self.alerter.channels and (
                self._last_alert_test is None or now - self._last_alert_test >= ALERT_RETEST_EVERY):
            self._last_alert_test = now
            self.alerter.self_test(now, wait=0)  # non-blocking; health updates when it lands
        if self._last_health is None or now - self._last_health >= HEALTH_SNAPSHOT_EVERY:
            self._last_health = now
            snapshot = self.status_snapshot(now)
            self._sample_rth_quotes(now, snapshot.get("market_data"))
            with self.store.tx():
                self.store.set_control(now, "health", json.dumps(
                    {"at": now.isoformat(), **snapshot}, default=str))
        if self._last_heartbeat and now - self._last_heartbeat < HEARTBEAT_EVERY:
            return
        self._last_heartbeat = now
        if not delayed_rth:
            self._check_market_data(now)
        log.info("[HEARTBEAT] %s", json.dumps(self.status_snapshot(now), default=str))
        if self._reconnect_stopped:
            card = AlertCard("🔴 P0 · 自动重连仍已停止，等待人工处理", P0_RED,
                             (("发生了什么", "引擎仍在运行；重连次数已达上限，未继续尝试连接。"),
                              ("影响", "止盈监控暂停；券商持仓和止损状态须人工核实。"),
                              ("你要做什么", "在 TWS 核实持仓与原 STP，排除原因后按 Ctrl+C，正常停机后运行 ~\\v6 g3t。")))
            self.alerter.send(now, "P0", "connection rebuilds remain stopped",
                              self._reconnect_reason, card=card, dedupe=False)


    def _sample_rth_quotes(self, now: datetime, md: dict | None) -> None:
        day = trading_day(now)
        if day != self._quote_sample_day:
            self._quote_sample_day = day
            self._quote_first_sample_at = None
            self._quote_samples = 0
            self._quote_usable_samples = 0
            self._quote_total_samples = 0
        if not is_rth(now):
            return
        # A failed status snapshot is a monitoring outage, not a missing
        # observation. Count every actually subscribed symbol as unavailable.
        total = len(self._subscribed_quote_symbols)
        usable = int(md.get("usable_quotes") or 0) if md and not md.get("error") else 0
        if total <= 0:
            return
        if self._quote_first_sample_at is None:
            self._quote_first_sample_at = now
        self._quote_samples += 1
        self._quote_total_samples += total
        self._quote_usable_samples += max(0, min(usable, total))

    def quote_availability_stats(self, day, *, include_scope=False) -> dict:
        current = day == self._quote_sample_day
        stats = {"samples": self._quote_samples if current else 0,
                 "usable": self._quote_usable_samples if current else 0,
                 "total": self._quote_total_samples if current else 0,
                 "subscribed": len(self._subscribed_quote_symbols)}
        if include_scope:
            stats["started_at"] = self._quote_first_sample_at if current else None
        return stats

    def _gateway_heartbeat(self, now: datetime) -> None:
        """Read-only wire probe. No request waits and no order/query channel use.

        The IB current-time response has no reqId. A server timestamp that is
        older than the latest probe cannot prove recovery even if its callback
        arrives just after that probe. We keep sending every minute and only
        resolve on a reply generated near a new probe and received within its
        30-second window. Clock anomalies are monitored separately: one old
        sample only logs, consecutive anomalies page once, and normal replies
        clear the clock condition without requiring a socket reconnect.
        """
        day = trading_day(now)
        if day != self._gateway_stats_day:
            self._gateway_stats_day = day
            self._gateway_replies = 0
            self._gateway_misses = 0
            self._gateway_max_latency = 0.0
            self._gateway_pending.clear()
            self._gateway_miss_streak = 0
            self._gateway_last_request = None
        replies = getattr(self.broker, "gateway_heartbeat_replies", None)
        counter = getattr(self.broker, "gateway_heartbeat_reply_count", None)
        request = getattr(self.broker, "gateway_heartbeat_request", None)
        if replies is None or counter is None or request is None:
            return
        if day != self._gateway_seen_day:
            self._gateway_seen_day = day
            self._gateway_seen_replies = counter()
        if not self.flags.broker_connected:
            self._gateway_pending.clear()
            self._gateway_miss_streak = 0
            self._gateway_last_request = None
            self._reset_gateway_clock()
            self._gateway_has_prior_timeout = False
            self._gateway_seen_replies = counter()
            return

        if counter() != self._gateway_seen_replies:
            self._consume_gateway_replies(now, replies())
        for pending in self._gateway_pending:
            if not pending["missed"] and now - pending["at"] > GATEWAY_HEARTBEAT_TIMEOUT:
                pending["missed"] = True
                self._record_gateway_miss(now)
        self._gateway_pending = [pending for pending in self._gateway_pending if not pending["missed"]]

        session = session_for(day)
        if session is None:
            return
        start = datetime.combine(day, GATEWAY_HEARTBEAT_START_PT, tzinfo=PT)
        end = session.close_utc + GATEWAY_HEARTBEAT_AFTER_CLOSE
        if not start <= now <= end:
            return
        if self._gateway_last_request and now - self._gateway_last_request < GATEWAY_HEARTBEAT_EVERY:
            return
        self._gateway_last_request = now
        try:
            request()
        except Exception as exc:
            log.warning("Gateway current-time request failed (%s)", type(exc).__name__)
            self._record_gateway_miss(now)
            return
        self._gateway_pending.append({"at": now, "missed": False})
        if counter() != self._gateway_seen_replies:
            self._consume_gateway_replies(now, replies())

    def _reset_gateway_clock(self) -> None:
        self._gateway_clock_skewed = False
        self._gateway_clock_paged = False
        self._gateway_clock_skew_streak = 0
        self._gateway_clock_normal_streak = 0
        self._gateway_clock_skew_seconds = None
        self.alerter.resolve("P1", "Gateway 时钟偏差")

    def _consume_gateway_replies(self, now: datetime, replies: tuple[datetime, ...]) -> None:
        if len(replies) < self._gateway_seen_replies:  # broker replaced its history
            self._gateway_seen_replies = len(replies)
            self._gateway_pending.clear()
            self._gateway_miss_streak = 0
            self._reset_gateway_clock()
            self._gateway_has_prior_timeout = False
            return
        start = self._gateway_seen_replies
        self._gateway_seen_replies = len(replies)
        samples_getter = getattr(self.broker, "gateway_heartbeat_samples", None)
        samples = samples_getter() if samples_getter is not None else ()
        for index, server_time in enumerate(replies[start:], start):
            self._gateway_replies += 1
            if not isinstance(server_time, datetime) or server_time.tzinfo is None:
                log.warning("[GATEWAY_TIME] invalid server timestamp; difference unavailable")
                self._gateway_clock_skew_streak = 0
                self._gateway_clock_normal_streak = 0
                continue
            received_at = now
            if index < len(samples):
                sample_server, sample_received = samples[index]
                if (sample_server == server_time and isinstance(sample_received, datetime) and
                        sample_received.tzinfo is not None):
                    received_at = sample_received
            offset = (server_time - received_at).total_seconds()
            self._gateway_clock_skew_seconds = offset
            log.info("[GATEWAY_TIME] server_time=%s host_time=%s signed_skew_seconds=%+.3f",
                     server_time.isoformat(), received_at.isoformat(), offset)
            sent = self._gateway_last_request
            server_age = received_at - server_time
            future_skew = server_age < -GATEWAY_CLOCK_SKEW_TOLERANCE
            timely = sent is not None and timedelta(0) <= received_at - sent <= GATEWAY_HEARTBEAT_TIMEOUT
            # Past stamps after a real timeout may simply be old callbacks:
            # log their observed difference, never let them prove recovery.
            skew = abs(offset) > GATEWAY_CLOCK_SKEW_TOLERANCE.total_seconds()
            clock_candidate = skew and (future_skew or
                (timely and (not self._gateway_has_prior_timeout or self._gateway_clock_skewed)))
            if clock_candidate:
                self._gateway_clock_skew_streak += 1
                self._gateway_clock_normal_streak = 0
                if self._gateway_clock_skew_streak >= GATEWAY_CLOCK_SKEW_CONFIRMATIONS:
                    self._gateway_clock_skewed = True
                    if not self._gateway_clock_paged:
                        self._gateway_clock_paged = True
                        self.alerter.send(now, "P1", "Gateway 时钟偏差",
                                          f"server_minus_host_seconds={offset:+.3f}; "
                                          f"{self._gateway_clock_skew_streak} consecutive abnormal current-time replies; "
                                          "check TWS/Gateway currentTime; this does not prove the host clock is wrong",
                                          dedupe=False)
            elif not skew:
                self._gateway_clock_skew_streak = 0
                self._gateway_clock_normal_streak += 1
                if self._gateway_clock_normal_streak >= GATEWAY_CLOCK_SKEW_CONFIRMATIONS:
                    if self._gateway_clock_paged:
                        self.alerter.resolve("P1", "Gateway 时钟偏差")
                        self.alerter.send(now, "INFO", "Gateway 时钟偏差已恢复",
                                          f"server_minus_host_seconds={offset:+.3f}; "
                                          f"{self._gateway_clock_normal_streak} consecutive normal replies",
                                          dedupe=False)
                    self._gateway_clock_skewed = False
                    self._gateway_clock_paged = False
            else:
                self._gateway_clock_skew_streak = 0
                self._gateway_clock_normal_streak = 0
            if sent is None:
                continue
            latency = received_at - sent
            past_skew = server_time < sent - GATEWAY_CLOCK_SKEW_TOLERANCE
            recovering = self._gateway_paged or self._gateway_miss_streak > 0
            if (clock_candidate and timely and not recovering and
                    (not self._gateway_has_prior_timeout or self._gateway_clock_skewed)):
                # A prompt callback with an anomalous stamp is a clock sample,
                # not silence. Keep all already-open timeout recovery guards.
                self._gateway_pending.clear()
                self._gateway_max_latency = max(self._gateway_max_latency, latency.total_seconds())
                continue
            if (latency < timedelta(0) or latency > GATEWAY_HEARTBEAT_TIMEOUT or
                    future_skew or past_skew or
                    (recovering and server_age > GATEWAY_CLOCK_SKEW_TOLERANCE) or
                    (not recovering and server_age > GATEWAY_HEARTBEAT_TIMEOUT)):
                log.info("Gateway current-time reply was late or server time not fresh; timeout recovery unchanged")
                continue
            # Evaluate this reply on its own; a past clock flag cannot poison it.
            self._gateway_pending.clear()
            self._gateway_max_latency = max(self._gateway_max_latency, latency.total_seconds())
            self._gateway_miss_streak = 0
            if self._gateway_paged:
                self._gateway_paged = False
                self.alerter.resolve("P1", "IB Gateway not responding")
                self.alerter.send(now, "INFO", "IB Gateway responding",
                                  f"fresh current-time reply received in {latency.total_seconds():.1f}s",
                                  dedupe=False)

    def _record_gateway_miss(self, now: datetime) -> None:
        self._gateway_has_prior_timeout = True
        self._gateway_misses += 1
        self._gateway_miss_streak += 1
        if self._gateway_miss_streak >= GATEWAY_HEARTBEAT_MISSES:
            self._gateway_paged = True
            self.alerter.send(now, "P1", "IB Gateway not responding",
                              f"{self._gateway_miss_streak} consecutive current-time requests exceeded "
                              f"{int(GATEWAY_HEARTBEAT_TIMEOUT.total_seconds())}s; check Gateway API responsiveness")

    def gateway_heartbeat_stats(self) -> dict:
        return {"replies": self._gateway_replies,
                "max_latency_seconds": round(self._gateway_max_latency, 1),
                "misses": self._gateway_misses,
                "clock_skew_suspected": self._gateway_clock_skewed,
                "clock_skew_seconds": self._gateway_clock_skew_seconds,
                "clock_skew_streak": self._gateway_clock_skew_streak,
                "clock_normal_streak": self._gateway_clock_normal_streak,
                "not_responding": self._gateway_paged}

    def _dry_log(self, now: datetime, key: str, message: str) -> None:
        if key in self._dry_logged:
            return
        self._dry_logged.add(key)
        log.info("[DRY_RUN] %s", message)
        self.store.audit(now, "DRY_RUN", message=message)

    def status_snapshot(self, now: datetime) -> dict:
        blocks = global_entry_blocks(self.cfg, self.store, self.flags, now) + self._protection_entry_blocks()
        return {
            "mode": self.cfg.mode,
            "dry_run": self.dry_run,
            "old_v6_pids": self.old_v6_pids,
            "old_v6_check_unknown": self.old_v6_check_unknown,
            **({"data_mode": "DELAYED rehearsal only"} if self.cfg.market_data_type == 3 else {}),
            "account": self.cfg.account,
            "config_fingerprint": self.cfg.fingerprint()[:16],
            "code_sha": self.code_sha,
            "broker_connected": self.flags.broker_connected,
            "automatic_reconnect_stopped": self._reconnect_stopped,
            "recovery_pending": self._recovery_pending,
            "reconnect_reason": self._reconnect_reason,
            "reconnect_next_at": self._reconnect_next_at.isoformat() if self._reconnect_next_at else None,
            "reconcile_ok": self.flags.reconcile_ok,
            "alerts_healthy": self.flags.alerts_healthy,
            "equity_for_sizing": self.flags.equity,
            "loss_streak": self.store.control("loss_streak"),
            "cooldown_until": self.store.control("cooldown_until"),
            "open_positions": [(r["symbol"], r["qty_open"], round(r["entry_price"], 2))
                               for r in self.store.open_positions()],
            "active_orders": [(r["symbol"], r["purpose"], r["status"]) for r in self.store.active_intents()],
            "entries_allowed": not blocks,
            "entry_blocks": blocks,
            "market_data": self._market_data_status(),
        }

    def _market_data_status(self) -> dict | None:
        status = getattr(self.broker, "market_data_status", None)
        if status is None:
            return None
        try:
            result = status()
            if isinstance(result, dict):
                result = dict(result)
                now = self.clock.now()
                symbols = self._subscribed_quote_symbols
                result["quote_symbols"] = len(symbols)
                unusable = []
                for symbol in symbols:
                    try:
                        if not check_quote(self.cfg, self.broker.quote(symbol), now,
                                           require_bid_ask=True).allowed:
                            unusable.append(symbol)
                    except Exception:
                        unusable.append(symbol)  # a failed quote is unusable
                result["usable_quotes"] = len(symbols) - len(unusable)
                if self.cfg.market_data_type == 3:
                    result["unusable_quote_symbols"] = sorted(unusable)
            return result
        except Exception as exc:             # pragma: no cover - status must never break the loop
            return {"error": str(exc)}

    def _check_market_data(self, now: datetime, *, startup: bool = False) -> None:
        """Page once at startup/open, then at most every two RTH hours."""
        if not self.flags.broker_connected:
            self._md_delayed_shortage_since = None
            return                  # tickers do not update while disconnected: nothing to judge
        md = self._market_data_status()
        if not md or md.get("error"):
            self._md_delayed_shortage_since = None
            return
        missing, delayed = md.get("no_subscription") or [], md.get("delayed_symbols") or []
        if is_rth(now):
            delayed = delayed + (md.get("frozen_symbols") or [])     # frozen data in RTH is not live
        if self.cfg.market_data_type == 1 and md.get("requested") == "live" and (missing or delayed):
            problem = (tuple(sorted(missing)), tuple(sorted(delayed)))
            changed = problem != self._md_problem
            day = trading_day(now)
            in_rth = is_rth(now)
            page = startup or (in_rth and (changed or self._md_rth_page_day != day or
                                           self._md_last_page is None or
                                           now - self._md_last_page >= MARKET_DATA_REPEAT))
            self._md_problem = problem
            if page:
                self.alerter.send(now, "P1", "no real-time market data",
                                  f"IBKR refused real-time API data for {', '.join(missing) or '-'}; delayed/frozen ticks for "
                                  f"{', '.join(delayed) or '-'}. Those symbols get no usable quote: no entry, and open "
                                  "positions cannot be priced. Enable the real-time subscription (paper: share the "
                                  "live account's subscriptions) and log in to the Gateway again.", dedupe=False)
                self._md_last_page = now
                if in_rth:
                    self._md_rth_page_day = day
            elif not in_rth:
                log.info("no real-time market data (outside RTH, local only): missing=%s delayed=%s",
                         ",".join(missing) or "-", ",".join(delayed) or "-")
        elif self.cfg.market_data_type == 1 and md.get("requested") == "live":
            self.alerter.resolve("P1", "no real-time market data")
            self._md_problem = None
            self._md_last_page = None
            self._md_rth_page_day = None
        elif self.cfg.market_data_type == 3:
            in_rth = is_rth(now)
            total = md.get("quote_symbols", 0)
            usable = md.get("usable_quotes", 0)
            unusable = tuple(md.get("unusable_quote_symbols") or ())
            shortage = in_rth and usable < total
            if shortage:
                if self._md_delayed_shortage_since is None:
                    self._md_delayed_shortage_since = now
            else:
                self._md_delayed_shortage_since = None
            prolonged_shortage = bool(shortage and self._md_delayed_shortage_since is not None
                                      and now - self._md_delayed_shortage_since > DELAYED_SHORTAGE_AFTER)
            if missing or prolonged_shortage:
                problem = (tuple(sorted(missing)),
                           unusable if prolonged_shortage else ())
                changed = problem != self._md_problem
                day = trading_day(now)
                page = startup or (in_rth and (changed or self._md_rth_page_day != day or
                                               self._md_last_page is None or
                                               now - self._md_last_page >= MARKET_DATA_REPEAT))
                self._md_problem = problem
                if page:
                    self.alerter.send(now, "P1", "no delayed market data",
                                      f"IBKR reports no subscription for {', '.join(missing) or '-'}; "
                                       f"usable delayed quotes {usable}/{total}; unusable symbols "
                                       f"{', '.join(unusable) or '-'}. Check delayed API permission "
                                      "and Gateway market-data status; entries still require valid quotes.",
                                      dedupe=False)
                    self._md_last_page = now
                    if in_rth:
                        self._md_rth_page_day = day
                elif not in_rth:
                    log.info("no delayed market data (outside RTH, local only): missing=%s usable=%s/%s",
                             ",".join(missing) or "-", usable, total)
            else:
                self.alerter.resolve("P1", "no delayed market data")
                self._md_problem = None
                self._md_last_page = None
                self._md_rth_page_day = None
