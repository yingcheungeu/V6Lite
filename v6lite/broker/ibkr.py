"""Interactive Brokers adapter (ib_insync), single-threaded.

Design choices carried over from V6 incidents:
* ONE physical `placeOrder` call site (_place_order). LMT, or protective STP only.
* Every order carries `orderRef = intent_id`, so executions map back to the
  durable intent after a restart (reqExecutions returns orderRef).
* The connection is bound to one account (`connect(account=...)`).
* Fill truth comes from executions (execDetails / reqExecutions), never from
  the status string; a `Cancelled` status is only a hint (see engine).
* No worker thread: the engine calls `pump()`, which runs `ib.sleep()`, so all
  ib_insync callbacks execute on the engine thread and simply enqueue events.
"""

from __future__ import annotations

import logging
from v6lite.telemetry import QueryStatistics, ExecutionStatistics
import math
import re
import time
from datetime import datetime, time as day_time, timezone
from types import SimpleNamespace

from v6lite.broker.base import BrokerError
from v6lite.broker.open_orders import normalize_open_order_rows
from v6lite.broker.completed_orders import native_completed_order
from v6lite.broker.pacing import IbkrMessagePacer, query_intervals
from v6lite.protection import protection_prices
from v6lite.config import LiteConfig
from v6lite.market_calendar import ET
from v6lite.execution_time import install_execution_clock_hooks, install_execution_log_filter, safe_raw_time, raw_time_category
from v6lite.models import (
    AnomalyEvent, Bar, BrokerAck, BrokerEvent, BrokerPosition, BrokerNoticeEvent, CommissionEvent, ConnectionEvent,
    FillEvent, FundsSnapshot, OpenOrder, OrderRequest, ProtectionRequest, Purpose, Quote, Side, StatusEvent, PriceObservationEvent, OrderBookChangeEvent,
    ExecutionQueryResult, ExecutionTimeDiagnosticEvent, CompletedOrdersSnapshot,
)

log = logging.getLogger("v6lite.ibkr")
ORDER_REF_PREFIX = "v6l-"
_NO_MARKET_DATA = {354, 10089, 10090, 10091, 10167, 10168, 10197}
_PRICE_TICKS = {1, 2, 4, 66, 67, 68}          # bid, ask, last (and their delayed variants)
_LIVE_PRICE_TICKS = {1, 2, 4}                 # with real-time data configured, delayed ticks never count
_PRICE_FIELD = {1: "bid", 2: "ask", 4: "last", 66: "bid", 67: "ask", 68: "last"}
# a size change on the bid/ask of the CURRENT request confirms that price level
# is still live (volume and last-size ticks never do)
_SIZE_FIELD = {0: "bid", 3: "ask", 69: "bid", 70: "ask"}
_DELAYED_TICKS = {66, 67, 68, 69, 70}
_CONNECTIVITY_LOST = {1100, 2110}
_CONNECTIVITY_RESTORED = {1101, 1102}
REQUEST_TIMEOUT_SECONDS = 15      # every blocking ib_insync request is bounded
HISTORY_TIMEOUT_SECONDS = 10
CALL_MARGIN_SECONDS = 2           # socket / event-loop overhead on top of a timeout
# reqPositions / reqAllOpenOrders are keyed by a FIXED name in ib_insync, so
# the End of an earlier, timed-out request can finish a newer one with a
# partial (even empty) list. A new request on such a channel is only sent
# once every earlier request of that channel has ended (the socket is
# ordered: then nothing older can arrive inside the new request); waiting
# for a straggler is bounded, and a channel stuck longer is renewed by
# dropping the connection (a new socket cannot carry old messages).
CHANNEL_DRAIN_SECONDS = 2
CHANNEL_RENEW_AFTER_SECONDS = 60
_CHANNELS = {"positions": ("positionEnd", ("reqPositions",)),
             "openOrders": ("openOrderEnd", ("reqOpenOrders", "reqAllOpenOrders")),
             "completedOrders": ("completedOrdersEnd", ("reqCompletedOrders",))}
# (reqAutoOpenOrders is fire-and-forget: no End is owed for it)


class _InformationalNoticeFilter(logging.Filter):
    def filter(self, record):
        # wrapper logs before errorEvent; the adapter owns the single readable
        # 21xx diagnostic and raw audit, retaining connectivity-loss severity.
        # Order errors stay unchanged.
        return not re.match(r"^(?:Warning|Error) 21\d\d, reqId ", record.getMessage())


def _install_notice_log_filter():
    logger = logging.getLogger("ib_insync.wrapper")
    if not any(isinstance(item, _InformationalNoticeFilter) for item in logger.filters):
        logger.addFilter(_InformationalNoticeFilter())


def _identity_limit(order):
    return None if getattr(order, "orderType", "") == "STP" else _stop_limit_price(getattr(order, "lmtPrice", None))


def _broker_echo_lmt(order):
    return getattr(order, "lmtPrice", None) if getattr(order, "orderType", "") == "STP" else None


def _stop_limit_price(value):
    # ib_insync uses UNSET_DOUBLE for STP's absent lmtPrice.
    number = _clean_price(value)
    return None if number is None or number >= 1e308 else number


def _clean_price(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int_or_none(value) -> int | None:
    number = _num(value)
    return int(number) if number is not None and number > 0 and number == int(number) else None


def _commission_of(fill) -> float | None:
    """Commission from a Fill's report, or None when the report is empty
    (execId unset) or not a finite number -- never a default 0.0."""
    report = getattr(fill, "commissionReport", None) if fill is not None else None
    if report is None or not getattr(report, "execId", ""):
        return None
    try:
        value = float(report.commission)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value < 1e9 else None


def _aware(dt) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


class IbkrBroker:
    def __init__(self, cfg: LiteConfig, *, read_only: bool = False, client_id: int | None = None):
        from ib_insync import IB  # imported lazily so tests never need a gateway

        self.cfg = cfg
        self.read_only = read_only
        self.client_id = cfg.client_id if client_id is None else client_id
        self.ib = IB()
        _install_notice_log_filter()
        self._query_last_finished: dict[str, float] = {}
        self._query_throttle_handler = None
        self._query_generation = 0
        self._fixed_hook_owner = None
        self._fixed_hook_generation = None
        self._fixed_originals = {}
        self._raw_open_order_owner = None
        self._raw_open_order_generation = None
        self._raw_open_order_original = None
        self._raw_open_order_rounds = {}
        self._raw_completed_owner = None
        self._raw_completed_generation = None
        self._raw_completed_original = None
        self._raw_completed_rounds = {}
        self._completed_invalid_rounds = set()
        self._completed_decode_counts = {}
        self._completed_decode_contexts = []
        self._completed_decoder_owner = None
        self._completed_decoder_generation = None
        self._completed_decode_original = None
        self._message_pacer = IbkrMessagePacer(cfg, self._notify_query_throttle, self._reset_channels)
        self._message_pacer.install(getattr(self.ib, "client", None))
        self._events: list[BrokerEvent] = []
        self._contracts: dict[str, object] = {}
        self._tickers: dict[str, object] = {}
        self._trades: dict[str, object] = {}
        self._wired = False
        self._vix_contract = None
        self._funds_value: float | None = None
        self._funds_changed_at: datetime | None = None
        self._account_seen_at: datetime | None = None
        self._account_ready: bool | None = None       # None = IBKR has not said
        self._netliq_value: float | None = None
        self._funds_pushed_at: datetime | None = None
        self._funds_refresh: dict | None = None
        self._funds_hooked_ib = None
        self._funds_refresh_blocked = False
        self._funds_connecting = False
        self._funds_connect_values: list[tuple[object, datetime]] = []
        self._account_sequence = 0
        self._funds_sequence = 0
        self._netliq_sequence = 0
        self._hooked_ib = None
        self._req_sent = {ch: 0 for ch in _CHANNELS}
        self._req_ended = {ch: 0 for ch in _CHANNELS}
        self._channel_stuck_since: dict[str, float] = {}
        self._channel_poisoned: set[str] = set()
        self._connection_syncing = False
        self._connection_synchronized: bool | None = None
        self._owned_open_orders_started = False
        self._connection_snapshot_ends = 0
        self.connection_rebuild_reason = ""
        # market-data evidence is ordered by a message SEQUENCE (not by clock
        # time: equal timestamps are common): subscription start, refusals of
        # that exact subscription, and PRICE ticks (bid/ask/last, never sizes
        # or volume) as they are decoded
        self._md_seq = 0
        self._sub_seq: dict[str, int] = {}
        self._price_seq: dict[str, int] = {}
        # symbol -> field -> (price, sequence, local receive time): the ONLY
        # source of trading quotes (Ticker fields can be overwritten by a late
        # tick of a cancelled request, and Ticker.time moves on volume ticks)
        self._fields: dict[str, dict[str, tuple[float, int, datetime]]] = {}
        self._refusal_seq: dict[str, int] = {}
        self._sub_req: dict[str, int | None] = {}        # symbol -> reqId of the CURRENT subscription
        self._nonorder_req_ids: set[int] = set()        # provenance survives native request End/error
        self._no_md_subscription: dict[str, tuple[str, int]] = {}
        self._gateway_reply_count = 0
        self._gateway_reply_times: list[datetime] = []
        self._gateway_reply_samples: list[tuple[datetime, datetime]] = []
        self._urgent_handler = None
        self._urgent_pending = False
        self._execution_hook_owner = None
        self._execution_queries = {}
        self._execution_contexts = []
        self._raw_execution_observed = set()
        self._query_statistics = QueryStatistics(log)
        self._execution_statistics = ExecutionStatistics(log)
        self._observation_generation_handler = None

    def set_observation_generation_handler(self, handler) -> None:
        self._observation_generation_handler = handler

    def set_query_throttle_handler(self, handler) -> None:
        self._query_throttle_handler = handler

    def _notify_query_throttle(self, channel):
        if self._query_throttle_handler is not None:
            self._query_throttle_handler(channel)

    def _wait_query(self, channel):
        self._message_pacer.install(getattr(self.ib, "client", None))
        interval = query_intervals(self.cfg)[channel]
        deadline = self._query_last_finished.get(channel, float("-inf")) + interval
        notified = False
        while time.monotonic() < deadline:
            if not notified:
                self._notify_query_throttle(channel)
                notified = True
            delay = min(0.05, deadline - time.monotonic())
            sleep = getattr(self.ib, "sleep", None)
            (sleep if callable(sleep) else time.sleep)(delay)

    def set_urgent_handler(self, handler) -> None:
        """Deliver executions/statuses before returning from ib_insync callback.

        The handler may use nonblocking placeOrder, but must not run a nested
        synchronous IB request from inside the event-loop callback.
        """
        self._urgent_handler = handler

    def set_protection_deadline_handler(self, handler) -> None:
        """Run gap deadlines on the ib_insync loop during synchronous queries.

        The callback must contain no broker query; it may use a cached order
        for a nonblocking DAY TP cancellation.  This timer remains active even
        while the outer engine waits for openOrders/positions/executions.
        """
        old = getattr(self, "_protection_deadline_handle", None)
        if old is not None:
            old.cancel()
        self._protection_deadline_handler = handler
        self._protection_deadline_handle = None
        if handler is None:
            return
        from ib_insync import util
        loop = util.getLoop()

        def run_deadline():
            self._protection_deadline_handle = None
            if self._protection_deadline_handler is not handler:
                return
            try:
                handler()
            except Exception:
                log.exception("protection deadline callback failed")
            if self._protection_deadline_handler is handler:
                self._protection_deadline_handle = loop.call_later(0.25, run_deadline)

        self._protection_deadline_handle = loop.call_later(0.25, run_deadline)

    def schedule_local_deadline(self, seconds, handler):
        """Schedule a local-only continuation on the existing IB event loop."""
        from ib_insync import util
        return util.getLoop().call_later(seconds, handler)

    def _dispatch_urgent(self, event: BrokerEvent, *, wake: bool = True) -> None:
        if wake:
            self._urgent_pending = True
        # A handshake cannot call an old session's urgent/write handler.
        # Executions remain queued for strict booking and idempotent replay.
        if self._connection_syncing:
            self._events.append(event)
            return
        if self._urgent_handler is None:
            self._events.append(event)
            return
        try:
            self._urgent_handler(event)
        except Exception:
            log.exception("urgent broker callback failed; event queued for idempotent replay")
            self._events.append(event)

    # ---------------- request channels with a fixed key ----------------
    def _ensure_channel_hooks(self) -> bool:
        """Count requests and End messages per fixed-key channel. Installed on
        the real ib_insync objects (instance attributes: the decoder looks the
        wrapper method up by name on every message). Stand-ins without a
        client/wrapper are left alone."""
        self._message_pacer.install(getattr(self.ib, "client", None))
        _install_notice_log_filter()
        install_execution_log_filter(self.cfg.account)
        self.ib._v6lite_execution_time_observer = self._execution_time_observation
        install_execution_clock_hooks(self.ib)
        self._install_execution_query_hooks()
        client, wrapper = getattr(self.ib, "client", None), getattr(self.ib, "wrapper", None)
        if client is None or wrapper is None:
            return False
        self._install_fixed_channel_hooks(client, wrapper)
        self._install_raw_open_order_hook(wrapper)
        self._install_raw_completed_hook(wrapper)
        self._install_completed_decode_guard()
        if self._hooked_ib is self.ib:
            return True
        original_start_req = getattr(wrapper, "startReq", None)
        if original_start_req is not None:
            def start_req(key, *args, _orig=original_start_req, **kwargs):
                result = _orig(key, *args, **kwargs)
                if isinstance(key, int) and not isinstance(key, bool):
                    self._nonorder_req_ids.add(key)
                return result
            wrapper.startReq = start_req
        original_current_time = getattr(wrapper, "currentTime", None)
        if original_current_time is not None:
            def current_time(*args, _orig=original_current_time):
                self._gateway_reply_count += 1
                server_time = datetime.fromtimestamp(args[0], timezone.utc)
                received_at = datetime.now(timezone.utc)
                self._gateway_reply_times.append(server_time)
                self._gateway_reply_samples.append((server_time, received_at))
                return _orig(*args)
            setattr(wrapper, "currentTime", current_time)
        original_price = getattr(wrapper, "priceSizeTick", None)
        if original_price is not None:
            def price_tick(req_id, tick_type, price, size, _orig=original_price):
                result = _orig(req_id, tick_type, price, size)
                live_only = self.cfg.market_data_type == 1
                kinds = _LIVE_PRICE_TICKS if live_only else _PRICE_TICKS
                value = _clean_price(price)
                symbol = self._subscription_symbol(req_id) if tick_type in kinds else None
                if symbol and value is None:
                    # -1 / 0 = no bid / no offer / no trade (halt, LULD, thin
                    # book): the level is GONE, never keep pricing from it
                    self._fields.get(symbol, {}).pop(_PRICE_FIELD[tick_type], None)
                elif symbol:
                    self._md_seq += 1
                    self._price_seq[symbol] = self._md_seq
                    self._fields.setdefault(symbol, {})[_PRICE_FIELD[tick_type]] = (
                        value, self._md_seq, datetime.now(timezone.utc))
                return result
            setattr(wrapper, "priceSizeTick", price_tick)
        original_size = getattr(wrapper, "tickSize", None)
        if original_size is not None:
            def size_tick(req_id, tick_type, size, _orig=original_size):
                result = _orig(req_id, tick_type, size)
                field = _SIZE_FIELD.get(tick_type)
                if field and size and size > 0 and not (self.cfg.market_data_type == 1
                                                        and tick_type in _DELAYED_TICKS):
                    symbol = self._subscription_symbol(req_id)
                    known = self._fields.get(symbol or "", {}).get(field)
                    if symbol and known and known[1] > self._quote_floor(symbol):
                        self._md_seq += 1
                        self._fields[symbol][field] = (known[0], self._md_seq, datetime.now(timezone.utc))
                return result
            setattr(wrapper, "tickSize", size_tick)
        self._hooked_ib = self.ib
        return True

    @property
    def query_generation(self):
        return self._query_generation

    def open_orders_snapshot_current(self, generation, request_id):
        """A queued completed snapshot loses authority when a new round starts."""
        return bool(generation == self._query_generation and request_id > 0 and
            "openOrders" not in self._channel_poisoned and
            self._raw_open_order_owner is getattr(self.ib, "wrapper", None) and
            self._raw_open_order_owner is not None and
            self._req_sent["openOrders"] == self._req_ended["openOrders"] == request_id)

    def _install_fixed_channel_hooks(self, client, wrapper):
        owner = (client, wrapper)
        if self._fixed_hook_owner != owner:
            self._fixed_hook_owner = owner
            self._fixed_originals = {}
            for channel, (end_name, req_names) in _CHANNELS.items():
                self._fixed_originals[end_name] = getattr(wrapper, end_name, None)
                for name in req_names:
                    self._fixed_originals[name] = getattr(client, name, None)
            self._fixed_hook_generation = None
        if self._fixed_hook_generation == self._query_generation:
            return
        generation = self._query_generation
        self._fixed_hook_generation = generation
        for channel, (end_name, req_names) in _CHANNELS.items():
            original_end = self._fixed_originals[end_name]
            if original_end is None and channel == "completedOrders":
                continue  # legacy offline stand-ins cannot provide completed evidence
            def end(*args, _orig=original_end, _ch=channel, _generation=generation):
                if (_generation != self._query_generation or
                        self._fixed_hook_owner != owner):
                    self._channel_poisoned.add(_ch)
                    return  # cannot complete a newer socket's fixed-key future
                if (_ch == "openOrders" and self._connection_syncing and
                        not self._owned_open_orders_started and
                        self._req_ended[_ch] == self._req_sent[_ch]):
                    # Current socket's automatic connection snapshot, not
                    # an End credit or a fresh adapter request. SDK requests
                    # already owed an End take the normal counted path.
                    self._connection_snapshot_ends += 1
                    log.info("[CONNECT_SNAPSHOT] openOrders End generation=%s count=%s",
                             _generation, self._connection_snapshot_ends)
                    return _orig(*args)
                self._req_ended[_ch] += 1
                self._query_last_finished[_ch] = time.monotonic()
                if self._req_ended[_ch] > self._req_sent[_ch]:
                    log.error("unsolicited %s End; channel poisoned", _ch)
                    self._req_ended[_ch] = self._req_sent[_ch]
                    self._channel_poisoned.add(_ch)
                return _orig(*args)
            setattr(wrapper, end_name, end)
            for req_name in req_names:
                original_req = self._fixed_originals[req_name]
                if original_req is None:
                    continue
                def req(*args, _orig=original_req, _ch=channel, _generation=generation, **kw):
                    if _generation != self._query_generation or self._fixed_hook_owner != owner:
                        raise BrokerError("stale socket request generation")
                    # Reserve before send: an End may reenter the native
                    # wrapper synchronously from the transport callback.
                    sent, ended = self._req_sent[_ch], self._req_ended[_ch]
                    self._req_sent[_ch] += 1
                    future = getattr(wrapper, "_futures", {}).get(_ch)
                    try:
                        result = _orig(*args, **kw)
                    except Exception as exc:
                        current = (_generation == self._query_generation and
                                   self._fixed_hook_owner == owner)
                        if (current and isinstance(exc, BrokerError) and not exc.maybe_sent and
                                self._req_sent[_ch] == sent + 1 and self._req_ended[_ch] == ended):
                            # Explicitly unsent only. A generic transport
                            # exception or End-then-error remains dirty.
                            self._req_sent[_ch] = sent
                            if future is not None and not future.done():
                                future.cancel()
                        else:
                            self._channel_poisoned.add(_ch)
                        raise
                    if _generation != self._query_generation or self._fixed_hook_owner != owner:
                        self._channel_poisoned.add(_ch)
                        raise BrokerError("socket changed while sending query")
                    return result
                setattr(client, req_name, req)

    def _reset_channels(self) -> None:
        """Invalidate fixed-key proof and callbacks on every native reset."""
        self._query_generation += 1
        self._raw_open_order_rounds.clear()
        self._raw_completed_rounds.clear()
        self._completed_invalid_rounds.clear()
        self._completed_decode_counts.clear()
        for ch in _CHANNELS:
            self._req_sent[ch] = 0
            self._req_ended[ch] = 0
        self._channel_stuck_since.clear()
        self._channel_poisoned.clear()
        self._nonorder_req_ids.clear()
        self._owned_open_orders_started = False
        client, wrapper = getattr(self.ib, "client", None), getattr(self.ib, "wrapper", None)
        if client is not None and wrapper is not None:
            self._install_fixed_channel_hooks(client, wrapper)
            self._install_raw_open_order_hook(wrapper)
            self._install_raw_completed_hook(wrapper)
            self._install_completed_decode_guard()
        # Native invalidation is complete before any presentation sink can fail.
        self._query_statistics.flush(time.monotonic())
        self._execution_statistics.flush(reset=True)
        if self._observation_generation_handler is not None:
            self._observation_generation_handler(self._query_generation)

    def _assert_query_generation(self, generation, ib):
        if generation != self._query_generation or ib is not self.ib:
            raise BrokerError("query socket generation changed; evidence unavailable")
        connected = getattr(ib, "isConnected", None)
        if callable(connected) and not connected():
            raise BrokerError("query socket disconnected; evidence unavailable")

    def _clean_request(self, channel: str, call):
        """Run a fixed-key request only on a clean channel and only accept its
        list if exactly its own End completed it. Anything else raises: the
        caller treats the evidence as unavailable (never as an empty list)."""
        self._require_synchronization()
        if channel == "openOrders":
            self._owned_open_orders_started = True
        started = time.monotonic()
        drained = False
        success = False
        generation, ib = self._query_generation, self.ib
        try:
            if not self._ensure_channel_hooks():
                self._wait_query(channel)
                try:
                    result = call()
                    self._assert_query_generation(generation, ib)
                    success = True
                    return result
                finally:
                    self._query_last_finished[channel] = time.monotonic()
            if channel in self._channel_poisoned:
                log.error("%s channel poisoned by an unsolicited End; renewing the connection", channel)
                self.connection_rebuild_reason = f"{channel}: unsolicited End seen; connection renewed"
                self.disconnect()
                raise BrokerError(self.connection_rebuild_reason)
            drained = self._req_ended[channel] < self._req_sent[channel]
            deadline = time.monotonic() + CHANNEL_DRAIN_SECONDS
            while self._req_ended[channel] < self._req_sent[channel]:
                now = time.monotonic()
                stuck = self._channel_stuck_since.setdefault(channel, now)
                if now - stuck >= CHANNEL_RENEW_AFTER_SECONDS:
                    log.error("%s channel owes an End for %.0fs; renewing the connection", channel, now - stuck)
                    self.connection_rebuild_reason = f"{channel}: earlier request never ended; connection renewed"
                    self.disconnect()
                    raise BrokerError(self.connection_rebuild_reason)
                if now >= deadline:
                    raise BrokerError(f"{channel}: an earlier request has not ended yet "
                                      f"({self._req_sent[channel] - self._req_ended[channel]} owed); evidence unavailable")
                self.ib.sleep(0.05)
                self._assert_query_generation(generation, ib)
            self._channel_stuck_since.pop(channel, None)
            before = self._req_sent[channel]
            self._wait_query(channel)
            self._assert_query_generation(generation, ib)
            try:
                result = call()               # a timeout raises; its End stays owed
            finally:
                self._query_last_finished[channel] = time.monotonic()
            self._assert_query_generation(generation, ib)
            if channel in self._channel_poisoned or self._req_sent[channel] != before + 1 \
                    or self._req_ended[channel] != self._req_sent[channel]:
                raise BrokerError(f"{channel}: request/End count mismatch (sent {self._req_sent[channel]}, "
                                  f"ended {self._req_ended[channel]}, expected {before + 1}); evidence unavailable")
            success = True
            return result
        except Exception as exc:
            log.warning("[QUERY] channel=%s failed: %s", channel, exc)
            raise
        finally:
            if channel in {"positions", "openOrders"}:
                finished = time.monotonic()
                elapsed = max(0.0, finished - started)
                if success:
                    log.debug("[QUERY] channel=%s secs=%.3f drained=%s", channel, elapsed, drained)
                self._query_statistics.completed(channel, finished, elapsed, drained, success=success)

    # ---------------- connection ----------------
    def connect(self) -> None:
        self.ib.RequestTimeout = REQUEST_TIMEOUT_SECONDS
        self._reset_account_stream()
        if self._funds_refresh_blocked:
            # Best-effort disconnect is insufficient to clear quarantine.
            # Require the old API socket to be closed before a new handshake.
            self.ib.disconnect()
            if self.ib.isConnected():
                raise BrokerError("quarantined account socket could not be closed")
        self._ensure_channel_hooks()
        self._reset_channels()                 # counted from this socket's first request on
        if not self._wired:
            # wired BEFORE connect: account values and commission reports that
            # arrive during the connect handshake must not be missed
            self.ib.execDetailsEvent += self._on_exec
            self.ib.commissionReportEvent += self._on_commission
            self.ib.orderStatusEvent += self._on_status
            if hasattr(self.ib, "openOrderEvent"):
                self.ib.openOrderEvent += self._on_open_order
            self.ib.disconnectedEvent += self._on_disconnect
            self.ib.errorEvent += self._on_error
            self.ib.accountValueEvent += self._on_account_value
            self._wired = True
        self._funds_connect_values = []
        self._funds_connecting = True
        self._connection_syncing = True
        self._connection_synchronized = False
        self._connection_snapshot_ends = 0
        try:
            self.ib.connect(self.cfg.host, self.cfg.port, clientId=self.client_id, timeout=20,
                            readonly=self.read_only, account=self.cfg.account)
            if not self.ib.isConnected():
                raise BrokerError("connection synchronization did not establish an API-ready socket")
            owed = [ch for ch in _CHANNELS if self._req_sent[ch] != self._req_ended[ch]]
            if owed:
                # ib_insync 0.9.86 can log a startup query timeout and
                # return normally. API-ready alone is not completed sync.
                raise BrokerError("SDK synchronization incomplete; registered End missing: " + ", ".join(owed))
            self._connection_synchronized = True
            self.connection_rebuild_reason = ""
        except Exception as exc:
            self._connection_synchronized = False
            self.connection_rebuild_reason = str(exc)
            self._funds_connecting = False
            self._funds_connect_values = []
            self._reset_account_stream(cancel_pending=False)
            # Close the unverified socket instead of letting a sync guard
            # trap engine recovery forever. Only a real new generation
            # may discard the old SDK debt; no End or proof is fabricated.
            self.disconnect()
            raise
        finally:
            self._connection_syncing = False
        self._funds_connecting = False
        self._funds_refresh_blocked = False
        values, self._funds_connect_values = self._funds_connect_values, []
        for value, received in values:
            self._apply_account_value(value, received)
        self.ib.reqMarketDataType(self.cfg.market_data_type)
        # re-subscribe market data after a reconnect
        symbols, self._tickers = list(self._tickers), {}
        if symbols:
            self.subscribe_quotes(symbols)

    def disconnect(self) -> None:
        self._query_statistics.flush(time.monotonic())
        self._execution_statistics.flush()
        try:
            self.ib.disconnect()
        except Exception:  # pragma: no cover - best effort
            log.exception("disconnect failed")

    def is_connected(self) -> bool:
        return bool(self.ib.isConnected())

    def is_synchronized(self) -> bool:
        return self.is_connected() and not self._connection_syncing and self._connection_synchronized is not False

    def _require_synchronization(self) -> None:
        if self._connection_syncing or self._connection_synchronized is False:
            raise BrokerError("connection synchronization incomplete; fresh evidence unavailable")

    def pump(self, seconds: float) -> None:
        # Wake the outer engine loop as soon as execDetails/orderStatus arrives
        # so a fresh confirmation query runs before scanning or historical
        # data.  No nested blocking request is made inside the callback.
        self._expire_funds_refresh()
        deadline = time.monotonic() + max(0.0, seconds)
        while not self._urgent_pending:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            self.ib.sleep(min(0.05, left))
            self._expire_funds_refresh()
        self._urgent_pending = False

    def gateway_heartbeat_request(self) -> None:
        """Fire-and-forget, read-only; never occupy the fixed-key query channels."""
        if not self.ib.isConnected():
            raise BrokerError("Gateway heartbeat socket disconnected")
        if not self._ensure_channel_hooks():
            raise BrokerError("Gateway heartbeat reply hook unavailable")
        self.ib.client.reqCurrentTime()

    def gateway_heartbeat_reply_count(self) -> int:
        return self._gateway_reply_count

    def gateway_heartbeat_replies(self) -> tuple[datetime, ...]:
        return tuple(self._gateway_reply_times)

    def gateway_heartbeat_samples(self) -> tuple[tuple[datetime, datetime], ...]:
        """Server stamp paired with the host time at its actual callback."""
        return tuple(self._gateway_reply_samples)

    # ---------------- account / portfolio ----------------
    def managed_accounts(self) -> list[str]:
        return list(self.ib.managedAccounts())

    def net_liquidation(self) -> float | None:
        """From the same account stream as AvailableFunds, so it is dropped
        together with it when the stream is reset (reconnect, 1101 data
        lost) instead of serving a value from before the loss."""
        return None if self._funds_refresh_blocked else self._netliq_value

    def _reset_account_stream(self, *, cancel_pending: bool = True) -> None:
        """A (re)connect invalidates everything learned from the old stream."""
        self._cancel_funds_refresh(send_cancel=cancel_pending)
        self._funds_connect_values = []  # no field from before this generation reset can revive
        self._funds_value = None
        self._funds_changed_at = None
        self._account_seen_at = None
        self._account_ready = None
        self._netliq_value = None
        self._funds_pushed_at = None

    def _on_account_value(self, value) -> None:
        if getattr(value, "account", self.cfg.account) != self.cfg.account:
            return
        received = datetime.now(timezone.utc)
        if self._funds_refresh_blocked:
            if self._funds_connecting:
                # A verified new socket is handshaking. Publish only after
                # successful connect, preserving each field's receive time.
                self._funds_connect_values.append((value, received))
            return
        self._apply_account_value(value, received)

    def _apply_account_value(self, value, now: datetime) -> None:
        self._account_seen_at = now
        self._account_sequence += 1
        if str(value.tag).lower() == "accountready":      # IBKR docs spell it both ways
            self._account_ready = str(value.value).strip().lower() == "true"
        elif value.tag == "NetLiquidation" and value.currency in {"USD", "BASE"}:
            self._netliq_value = _clean_price(value.value)
            self._netliq_sequence = self._account_sequence
        elif value.tag == "AvailableFunds" and value.currency in {"USD", "BASE"}:
            funds = _clean_price(value.value)
            self._funds_pushed_at = now          # freshness of THIS field, not of the stream
            self._funds_sequence = self._account_sequence
            if funds != self._funds_value or self._funds_changed_at is None:
                self._funds_value = funds
                self._funds_changed_at = now

    def available_funds(self) -> FundsSnapshot:
        """From the account-update stream (reqAccountUpdates, pushed on change
        and on IBKR's periodic cycle), with the time the value last changed and
        the time AvailableFunds ITSELF was last pushed (other account fields
        speaking do not make an old AvailableFunds fresh)."""
        self._expire_funds_refresh()
        if self._funds_refresh_blocked:
            return FundsSnapshot(None, None, None, False)
        return FundsSnapshot(self._funds_value, self._funds_changed_at, self._funds_pushed_at,
                             self._account_ready is not False)

    def worst_case_seconds(self, kind: str) -> float:
        history = HISTORY_TIMEOUT_SECONDS + CALL_MARGIN_SECONDS + self._message_pacer.queued_seconds()
        query = (CHANNEL_DRAIN_SECONDS + REQUEST_TIMEOUT_SECONDS + CALL_MARGIN_SECONDS +
                 max(query_intervals(self.cfg).values()) + self._message_pacer.queued_seconds())
        if kind == "history":       # symbol already qualified (scan symbols are pinned at startup)
            return history
        if kind == "vix":
            return history          # qualified at startup or skipped (see vix_close)
        return query

    def positions(self) -> list[BrokerPosition]:
        out = []
        for p in self.ib.positions(self.cfg.account):
            if getattr(p.contract, "secType", "") != "STK":
                continue
            out.append(BrokerPosition(p.contract.symbol.upper(), float(p.position), float(p.avgCost)))
        return out

    def positions_fresh(self) -> list[BrokerPosition]:
        """reqPositions: a fresh download from the server (bounded by
        RequestTimeout), not the streamed cache, on a clean channel. The
        subscription it opens also streams updates into a pending request,
        so one contract can appear twice: the last report wins."""
        latest: dict[object, BrokerPosition] = {}
        for p in self._clean_request("positions", self.ib.reqPositions):
            if self.cfg.protection.enabled and not getattr(p, "account", ""):
                raise BrokerError("fresh position account identity unavailable")
            if getattr(p, "account", self.cfg.account) != self.cfg.account:
                continue
            if getattr(p.contract, "secType", "") != "STK":
                continue
            if self.cfg.protection.enabled and not _int_or_none(getattr(p.contract, "conId", None)):
                raise BrokerError("fresh stock position conId unavailable")
            key = getattr(p.contract, "conId", None) or p.contract.symbol.upper()
            latest[key] = BrokerPosition(p.contract.symbol.upper(), float(p.position), float(p.avgCost),
                                         str(getattr(p, "account", "") or ""),
                                         _int_or_none(getattr(p.contract, "conId", None)))
        return list(latest.values())

    def completed_orders(self) -> CompletedOrdersSnapshot:
        """Read current native completed orders, including TWS-created orders.

        Fixed-key request debt/End and socket checks apply. An empty raw result
        is authoritative only for this request's completion, never a cache or
        a startup query. Missing native identity fields remain None.
        """
        started = self._execution_query_now()
        generation, ib = self._query_generation, self.ib
        if not self._ensure_channel_hooks():
            raise BrokerError("completedOrders native request/End hooks unavailable")
        wrapper = getattr(ib, "wrapper", None)
        if (self._raw_completed_owner is not wrapper or wrapper is None or
                not callable(self._fixed_originals.get("reqCompletedOrders")) or
                not callable(self._fixed_originals.get("completedOrdersEnd")) or
                not callable(getattr(ib, "reqCompletedOrders", None))):
            raise BrokerError("completedOrders native callback/request unavailable")
        before = self._req_sent["completedOrders"]
        self._clean_request("completedOrders", lambda: ib.reqCompletedOrders(apiOnly=False))
        self._assert_query_generation(generation, ib)
        request_id = self._req_sent["completedOrders"]
        if request_id != before + 1 or self._req_ended["completedOrders"] != request_id:
            raise BrokerError("completedOrders own request/End proof unavailable")
        if (generation, request_id) in self._completed_invalid_rounds:
            self._completed_invalid_rounds.discard((generation, request_id))
            self._raw_completed_rounds.pop((generation, request_id), None)
            self._completed_decode_counts.pop((generation, request_id), None)
            raise BrokerError("completedOrders native decode/capture incomplete; evidence unavailable")
        rows = self._raw_completed_rounds.pop((generation, request_id), [])
        decoded = self._completed_decode_counts.pop((generation, request_id), 0)
        if decoded != len(rows):
            raise BrokerError("completedOrders native callback capture incomplete; evidence unavailable")
        return CompletedOrdersSnapshot(tuple(rows), started, self._execution_query_now(),
                                       generation, request_id)

    def open_orders(self) -> list[OpenOrder]:
        """Fresh server query (reqAllOpenOrders), never the local openTrades
        cache: after a restart or client change the cache can be empty while
        orders are still working. Raises on timeout -> caller keeps UNKNOWN.
        Returns every working order of the account; orders V6-Lite did not
        create are marked ours=False (they still reserve shares)."""
        out = []
        query_started_at = self._execution_query_now()
        generation = self._query_generation
        was_syncing = self._connection_syncing
        previous_request = self._req_sent["openOrders"]
        trades = self._clean_request("openOrders", self.ib.reqAllOpenOrders)
        if self._raw_open_order_owner is not None and self._raw_open_order_owner is getattr(self.ib, "wrapper", None):
            trades = self._raw_open_order_rounds.pop((self._query_generation, self._req_sent["openOrders"]), [])
        trades, normalization = normalize_open_order_rows(trades,
            at=self._execution_query_now(), generation=self._query_generation,
            request_id=self._req_sent["openOrders"])
        conflicts = [event for event in normalization if event.conflicting_fields]
        for event in conflicts or normalization:
            self._dispatch_urgent(event, wake=False)
        if conflicts:
            raise BrokerError("own openOrders rows contradict; no partial snapshot accepted")
        for trade in trades:
            ref = trade.order.orderRef or ""
            if (getattr(trade.order, "orderType", "") in {"STP", "STP LMT"} and
                    not getattr(trade.order, "account", "")):
                raise BrokerError("protection account identity unavailable; open-order evidence incomplete")
            if getattr(trade.order, "orderType", "") in {"STP", "STP LMT"} and (
                    getattr(trade.contract, "secType", None) != "STK" or
                    getattr(trade.contract, "currency", None) != "USD"):
                raise BrokerError("protection STK/USD contract evidence incomplete or inconsistent")
            account = getattr(trade.order, "account", "") or self.cfg.account
            if account != self.cfg.account:
                continue
            st = trade.orderStatus
            if ref.startswith(ORDER_REF_PREFIX):
                self._observe_prices(trade, source="openOrders")
            if st.status in {"Cancelled", "ApiCancelled", "Inactive", "Filled"}:
                continue
            ours = ref.startswith(ORDER_REF_PREFIX)
            ident = ref if ours else f"foreign:{trade.order.permId or trade.order.orderId}"
            action = str(getattr(trade.order, "action", "") or "").upper()
            filled = _num(st.filled) or 0.0
            remaining = _num(st.remaining) or 0.0
            if remaining <= 0:
                # ib_insync builds a Trade from openOrder with remaining=0 until
                # an orderStatus arrives: fall back to the order's own quantity
                total = _num(getattr(trade.order, "totalQuantity", None)) or 0.0
                remaining = max(0.0, total - filled) if total > filled else total
            sec = getattr(trade.contract, "secType", "STK")
            if sec == "STK":
                out.append(OpenOrder(ident, trade.order.orderId, trade.contract.symbol.upper(), st.status,
                                     filled, remaining, action if action in {"BUY", "SELL"} else "", ours,
                                     _int_or_none(getattr(trade.order, "permId", None)) or
                                     _int_or_none(getattr(st, "permId", None)),
                                     _int_or_none(getattr(trade.order, "clientId", None)),
                                     _int_or_none(getattr(trade.contract, "conId", None)),
                                     str(getattr(trade.order, "orderType", "") or ""),
                                     str(getattr(trade.order, "tif", "") or ""),
                                     _clean_price(getattr(trade.order, "auxPrice", None)),
                                     _identity_limit(trade.order),
                                     getattr(trade.order, "outsideRth", None), account,
                                     _int_or_none(getattr(trade.order, "totalQuantity", None)),
                                     _broker_echo_lmt(trade.order)))
            elif sec == "BAG":
                out.extend(self._combo_stock_legs(ident, trade, st.status, remaining, action))
            else:
                # plain options/futures etc. do not claim shares by quantity
                log.info("working %s order on %s not counted as shares", sec, trade.contract.symbol)
        request_id = self._req_sent["openOrders"]
        if (self._raw_open_order_owner is not None and
                self._raw_open_order_owner is getattr(self.ib, "wrapper", None) and
                generation == self._query_generation and request_id > previous_request and
                self._req_ended["openOrders"] == request_id):
            # An immutable, complete result of our own native request. The
            # individual raw callbacks above remain partial-query observations.
            completed = self._execution_query_now()
            snapshot = tuple(out)
            for order in snapshot:
                if order.ours:
                    self._dispatch_urgent(PriceObservationEvent(order, "openOrders",
                        connection_snapshot=bool(was_syncing or self._connection_syncing),
                        observed_at=completed, socket_generation=generation, query_snapshot=True,
                        owned_open_orders=snapshot, query_started_at=query_started_at,
                        query_request_id=request_id), wake=False)
        return out

    def _combo_stock_legs(self, ident: str, trade, status: str, remaining: float, action: str) -> list[OpenOrder]:
        """A combo (BAG) can hold stock legs: each leg on a stock this
        session knows becomes a share claim (remaining x ratio, side flipped
        for a SELL combo). Any leg that cannot be resolved (unknown conId,
        unknown action, no legs) -> one '*' order of unknown side, which
        blocks SELLs on every symbol and every release until handled."""
        legs = getattr(trade.contract, "comboLegs", None) or []
        by_con = {getattr(c, "conId", None): sym for sym, c in self._contracts.items()}
        out = []
        unresolved = not legs
        for leg in legs:
            symbol = by_con.get(getattr(leg, "conId", None))
            leg_action = str(getattr(leg, "action", "") or "").upper()
            if symbol is None:
                # unknown here is not proof of "not a stock we hold": fail closed
                unresolved = True
                continue
            if leg_action not in {"BUY", "SELL"} or action not in {"BUY", "SELL"}:
                unresolved = True
                continue
            side = leg_action if action == "BUY" else ("SELL" if leg_action == "BUY" else "BUY")
            ratio = _num(getattr(leg, "ratio", None)) or 1.0
            out.append(OpenOrder(f"{ident}:{symbol}", trade.order.orderId, symbol, status, 0.0,
                                 remaining * ratio, side, False))
        if unresolved:
            out.append(OpenOrder(f"{ident}:combo", trade.order.orderId, "*", status, 0.0, remaining, "", False))
        return out

    # ---------------- contracts & data ----------------
    def qualify(self, symbol: str) -> int:
        from ib_insync import Stock

        contract = Stock(symbol, "SMART", "USD")
        qualified = self.ib.qualifyContracts(contract)
        if not qualified or not getattr(contract, "conId", 0):
            raise BrokerError(f"cannot qualify {symbol}")
        self._contracts[symbol] = contract
        return int(contract.conId)

    def subscribe_quotes(self, symbols: list[str]) -> None:
        for symbol in symbols:
            if symbol in self._tickers:
                continue
            contract = self._contracts.get(symbol)
            if contract is None:
                self.qualify(symbol)
                contract = self._contracts[symbol]
            self._md_seq += 1
            self._sub_seq[symbol] = self._md_seq
            ticker = self.ib.reqMktData(contract, "", False, False)
            req_map = getattr(getattr(self.ib, "wrapper", None), "ticker2ReqId", {}) or {}
            self._sub_req[symbol] = (req_map.get("mktData") or {}).get(ticker)
            # ib_insync reuses the Ticker of a contract: prices left over from
            # an earlier subscription must not look like this one's
            for field in ("bid", "ask", "last", "bidSize", "askSize", "lastSize"):
                if hasattr(ticker, field):
                    setattr(ticker, field, math.nan)
            self._tickers[symbol] = ticker

    def quote(self, symbol: str) -> Quote | None:
        ticker = self._tickers.get(symbol)
        if ticker is None:
            return None
        # with real-time data configured, a delayed (3/4) tick is NOT a quote:
        # its local receive time would look fresh while the price is ~15 min old
        if self.cfg.market_data_type == 1 and getattr(ticker, "marketDataType", 1) != 1:
            return None             # delayed (3/4) or frozen (2): its receive time would look fresh
        if not self._ensure_channel_hooks():          # stand-ins without the ib_insync wrapper
            return Quote(symbol, _clean_price(ticker.bid), _clean_price(ticker.ask),
                         _clean_price(ticker.last), _aware(ticker.time),
                         market_data_type=getattr(ticker, "marketDataType", None))
        # built ONLY from qualifying price fields of the current request,
        # received after it started and after any refusal, each with its own
        # receive time; the quote is as old as the fields it is priced from
        floor = self._quote_floor(symbol)
        fields = {k: v for k, v in self._fields.get(symbol, {}).items() if v[1] > floor}
        bid, ask, last = (fields.get(k) for k in ("bid", "ask", "last"))
        if bid and ask:
            ts = min(bid[2], ask[2])            # the book is as old as its older side
            if last and last[2] < ts:
                last = None         # an older trade price never prices a fresher book (mark -> mid)
        elif last:
            ts = last[2]
        else:
            return None
        return Quote(symbol, bid[0] if bid else None, ask[0] if ask else None, last[0] if last else None, ts,
                     last_ts=last[2] if last else None, market_data_type=getattr(ticker, "marketDataType", None))

    def _quote_floor(self, symbol: str) -> int:
        """Fields recorded before this sequence never price a quote: the
        start of the current subscription and the LATEST refusal of it (kept
        even after the status display clears the refusal)."""
        return max(self._sub_seq.get(symbol, 0), self._refusal_seq.get(symbol, 0))

    def _subscription_symbol(self, req_id) -> str | None:
        """Our key (as configured, e.g. 'BRK.B') for a request id, only if it
        is the CURRENT quote subscription of that key (a cancelled request
        id still maps to the reused Ticker in ib_insync)."""
        for symbol, current in self._sub_req.items():
            if current is not None and current == req_id:
                return symbol
        return None

    def _live_price(self, symbol: str) -> bool:
        """A price tick arrived after this subscription started and after any
        refusal of it (size or volume ticks do not count)."""
        refused = self._no_md_subscription.get(symbol)
        floor = max(self._sub_seq.get(symbol, 0), refused[1] if refused else 0)
        return self._price_seq.get(symbol, 0) > floor

    def market_data_status(self) -> dict:
        """What IBKR actually delivers: requested type, symbols whose ticks
        are delayed (3/4), and symbols IBKR refused for lack of an API
        market-data subscription (10089 / 354)."""
        for sym, (_, refused_seq) in list(self._no_md_subscription.items()):
            if self._price_seq.get(sym, 0) > refused_seq:      # a price of this subscription since
                self._no_md_subscription.pop(sym, None)
        delayed = sorted(s for s, t in self._tickers.items() if getattr(t, "marketDataType", 1) in (3, 4))
        frozen = sorted(s for s, t in self._tickers.items() if getattr(t, "marketDataType", 1) == 2)
        return {"requested": "live" if self.cfg.market_data_type == 1 else "DELAYED",
                "delayed_symbols": delayed, "frozen_symbols": frozen,
                "no_subscription": sorted(self._no_md_subscription)}

    def daily_bars(self, symbol: str, days: int) -> list[Bar]:
        contract = self._contracts.get(symbol)
        if contract is None:
            self.qualify(symbol)
            contract = self._contracts[symbol]
        started = time.monotonic()
        bars = None
        try:
            bars = self.ib.reqHistoricalData(contract, endDateTime="", durationStr=f"{int(days)} D",
                                             barSizeSetting="1 day", whatToShow="TRADES",
                                             useRTH=True, formatDate=1, timeout=HISTORY_TIMEOUT_SECONDS)
        finally:
            log.info("[HISTORY] %s secs=%.3f bars=%s last_date=%s", symbol,
                     time.monotonic() - started, len(bars or []), str(bars[-1].date)[:10] if bars else "-")
        return [Bar(str(b.date), float(b.open), float(b.high), float(b.low), float(b.close),
                    float(b.volume)) for b in bars or []]

    def previous_close(self, symbol: str) -> float | None:
        try:
            today = datetime.now(timezone.utc).astimezone(ET).date()
            bars = self.daily_bars(symbol, 7)
            done = [b for b in bars if str(b.day)[:10] < today.isoformat()]
            return _clean_price(done[-1].close) if done else None
        except Exception:
            log.exception("previous close for %s failed", symbol)
            return None

    def qualify_vix(self) -> None:
        """Qualified once at startup so the scan-time VIX call is a single
        bounded history request."""
        from ib_insync import Index

        contract = Index("VIX", "CBOE")
        if self.ib.qualifyContracts(contract):
            self._vix_contract = contract

    def vix_close(self) -> float | None:
        try:
            contract = self._vix_contract
            if contract is None:
                # never qualify inside a scan (it would double the blocking
                # budget); without VIX the strategy uses the strict threshold
                log.warning("VIX contract not qualified; strict score threshold applies")
                return None
            started = time.monotonic()
            bars = None
            try:
                bars = self.ib.reqHistoricalData(contract, endDateTime="", durationStr="5 D",
                                                 barSizeSetting="1 day", whatToShow="TRADES",
                                                 useRTH=True, formatDate=1, timeout=HISTORY_TIMEOUT_SECONDS)
            finally:
                log.info("[HISTORY] VIX secs=%.3f bars=%s last_date=%s",
                         time.monotonic() - started, len(bars or []), str(bars[-1].date)[:10] if bars else "-")
            return _clean_price(bars[-1].close) if bars else None
        except Exception:
            log.exception("VIX fetch failed; strategy will use the strict threshold")
            return None

    # ---------------- orders ----------------
    def _place_order(self, contract, order, intent_id: str) -> BrokerAck:
        """The only physical placeOrder site, shared by DAY and GTC paths."""
        if self.read_only:
            raise BrokerError("read-only session cannot place or modify orders")
        try:
            trade = self.ib.placeOrder(contract, order)
        except BrokerError:
            raise  # a pre-wire guard retains its definite-unsent classification
        except Exception as exc:
            raise BrokerError(f"placeOrder failed: {exc}", maybe_sent=True) from exc
        self._trades[intent_id] = trade
        return BrokerAck(trade.order.orderId or None, trade.order.permId or None)

    def submit_limit(self, req: OrderRequest) -> BrokerAck:
        """DAY LIMIT submission; no market-order API exists."""
        from ib_insync import LimitOrder

        if self.read_only:
            raise BrokerError("read-only session cannot place orders")
        if not req.intent_id.startswith(ORDER_REF_PREFIX):
            raise BrokerError("intent id must carry the V6-Lite orderRef prefix")
        if type(req.quantity) is not int or req.quantity <= 0:
            raise BrokerError("quantity must be a positive int")
        if req.purpose == Purpose.PROTECT:
            raise BrokerError("PROTECT must use typed STP submission")
        price = _clean_price(req.limit_price)
        if price is None:
            raise BrokerError("limit price must be finite and positive")
        contract = self._contracts.get(req.symbol)
        if contract is None:
            raise BrokerError(f"{req.symbol} not qualified in this session")
        order = LimitOrder(str(req.side), req.quantity, round(price, 2), tif="DAY",
                           orderRef=req.intent_id, account=self.cfg.account, outsideRth=False, usePriceMgmtAlgo=False)
        if req.wire_deadline is not None:
            from ib_insync.client import getLoop
            pacer = self._message_pacer
            now = getLoop().time()
            recent = sum(now - at < 1.0 for at in pacer.sent_times)
            if (req.purpose not in {Purpose.TP1, Purpose.TP2, Purpose.TP3} or
                    req.wire_deadline.tzinfo is None or self._execution_query_now() >= req.wire_deadline or
                    pacer.client is not self.ib.client or pacer.draining or recent >= 10 or
                    any(pacer.opcode(msg) in {"3", "4", "8", "58"} for msg in self.ib.client._msgQ)):
                raise BrokerError("TP cannot send immediately with its original fresh quote", maybe_sent=False)
            generation = self._query_generation
            def wire_guard(message):
                if (generation != self._query_generation or
                        self._execution_query_now() >= req.wire_deadline):
                    raise BrokerError("TP original quote expired before physical wire; definitely unsent", maybe_sent=False)
            prior_guard = pacer.order_guard
            if prior_guard is not None:
                raise BrokerError("another immediate TP submission owns the wire guard", maybe_sent=False)
            pacer.order_guard = wire_guard
            try:
                return self._place_order(contract, order, req.intent_id)
            finally:
                pacer.order_guard = prior_guard
        return self._place_order(contract, order, req.intent_id)

    def submit_stop(self, req: ProtectionRequest) -> BrokerAck:
        """Submit GTC SELL STP, with no lmtPrice.  BrokerAck is local, not confirmation."""
        from ib_insync import StopOrder

        if self.read_only:
            raise BrokerError("read-only session cannot place protection orders")
        if not req.intent_id.startswith(ORDER_REF_PREFIX) or type(req.quantity) is not int or req.quantity <= 0:
            raise BrokerError("invalid protection identity or quantity")
        if (req.order_type != "STP" or req.limit_price is not None or
                not _clean_price(req.trigger_price) or req.trigger_price < 1):
            raise BrokerError("protection requires STP, valid stop, and no limit price")
        contract = self._contracts.get(req.symbol)
        if contract is None:
            raise BrokerError(f"{req.symbol} not qualified in this session")
        order = StopOrder("SELL", req.quantity, protection_prices(req.trigger_price)[0],
                               tif="GTC", outsideRth=False, orderRef=req.intent_id,
                               account=self.cfg.account, usePriceMgmtAlgo=False)
        return self._place_order(contract, order, req.intent_id)

    def modify_protection(self, req: ProtectionRequest) -> BrokerAck:
        """Modify only the same client's existing order ID; unknown identity blocks."""
        from ib_insync import StopOrder

        if self.read_only:
            raise BrokerError("read-only session cannot modify protection orders")
        if not req.intent_id.startswith(ORDER_REF_PREFIX) or type(req.quantity) is not int or req.quantity <= 0:
            raise BrokerError("invalid protection identity or quantity")
        if (req.order_type != "STP" or req.limit_price is not None or
                not _clean_price(req.trigger_price) or req.trigger_price < 1):
            raise BrokerError("protection requires STP, valid stop, and no limit price")
        if not (req.broker_order_id and req.perm_id and req.con_id and
                req.client_id == self.client_id and req.account == self.cfg.account):
            raise BrokerError("protection modification lacks persisted broker identity")
        trade = self._trades.get(req.intent_id)
        if trade is None and not req.allow_lookup:
            raise BrokerError("protection not cached for urgent modification")
        if trade is None:
            trade = next((t for t in list(self.ib.openTrades()) + list(self.ib.trades())
                          if getattr(t.order, "orderRef", "") == req.intent_id), None)
        if trade is None:
            trade = next((t for t in self._clean_request("openOrders", self.ib.reqAllOpenOrders)
                          if getattr(t.order, "orderRef", "") == req.intent_id), None)
        if trade is None:
            raise BrokerError("protection order not found; refusing duplicate")
        old = trade.order
        if (getattr(old, "orderRef", "") != req.intent_id or
                getattr(old, "action", "") != "SELL" or getattr(old, "outsideRth", None) is not False or
                getattr(trade.contract, "secType", None) != "STK" or
                getattr(trade.contract, "currency", None) != "USD" or
                getattr(old, "clientId", None) != self.client_id or
                getattr(old, "orderType", "") != "STP" or
                getattr(old, "tif", "") != "GTC" or
                getattr(old, "account", "") != self.cfg.account or
                getattr(trade.contract, "symbol", "").upper() != req.symbol or
                getattr(trade.contract, "conId", None) != req.con_id or
                getattr(old, "orderId", None) != req.broker_order_id or
                getattr(old, "permId", None) != req.perm_id):
            raise BrokerError("protection identity/client mismatch; refusing modify")
        order = StopOrder("SELL", req.quantity, protection_prices(req.trigger_price)[0],
                               tif="GTC", outsideRth=False, orderRef=req.intent_id,
                               account=self.cfg.account, orderId=old.orderId,
                               clientId=self.client_id, permId=getattr(old, "permId", 0), usePriceMgmtAlgo=False)
        return self._place_order(trade.contract, order, req.intent_id)

    def cancel(self, intent_id: str, *, expected_order_id: int | None = None,
               expected_perm_id: int | None = None, expected_con_id: int | None = None,
               allow_lookup: bool = True) -> None:
        """Second (and last) broker write path; same read-only boundary."""
        if self.read_only:
            raise BrokerError("read-only session cannot cancel orders")
        if not intent_id.startswith(ORDER_REF_PREFIX):
            raise BrokerError("refusing to cancel an order V6-Lite did not create")
        trade = self._trades.get(intent_id)
        if trade is None and not allow_lookup:
            raise BrokerError("order not cached for urgent cancellation")
        if trade is None:
            # local caches first (no network wait); the server query is the
            # last resort and is budgeted by the engine as a 'query'
            trade = next((t for t in list(self.ib.openTrades()) + list(self.ib.trades())
                          if getattr(t.order, "orderRef", "") == intent_id), None)
        if trade is None:
            trade = next((t for t in self._clean_request("openOrders", self.ib.reqAllOpenOrders)
                          if t.order.orderRef == intent_id), None)
        if trade is None:
            raise BrokerError(f"no working order for {intent_id}")
        if getattr(trade.order, "orderRef", "") != intent_id:
            raise BrokerError("cancel orderRef mismatch")
        if getattr(trade.order, "clientId", None) != self.client_id:
            raise BrokerError("cannot cancel an order from another client id")
        is_gtc = (intent_id.startswith("v6l-protect-") or
                  getattr(trade.order, "tif", "") == "GTC" or
                  getattr(trade.order, "orderType", "") in {"STP", "STP LMT"})
        if is_gtc and (getattr(trade.order, "orderType", "") != "STP" or
                       getattr(trade.order, "action", "") != "SELL" or
                       getattr(trade.order, "tif", "") != "GTC" or
                       getattr(trade.order, "outsideRth", None) is not False or
                       getattr(trade.contract, "secType", None) != "STK" or
                       getattr(trade.contract, "currency", None) != "USD"):
            raise BrokerError("protection cancel wire contract mismatch; legacy type not modified")
        if is_gtc and not all(v is not None for v in
                              (expected_order_id, expected_perm_id, expected_con_id)):
            raise BrokerError("generic cancel refuses GTC protection order")
        if (is_gtc or expected_order_id is not None) and \
                getattr(trade.order, "account", None) != self.cfg.account:
            raise BrokerError("cancel account mismatch")
        if expected_order_id is not None and (getattr(trade.order, "orderId", None) != expected_order_id or
                getattr(trade.order, "permId", None) != expected_perm_id or
                getattr(trade.contract, "conId", None) != expected_con_id):
            raise BrokerError("protection cancel identity mismatch")
        self.ib.cancelOrder(trade.order)

    def refresh_subscriptions(self) -> None:
        """After IBKR error 1101 ("connectivity restored, data lost") market
        data and account subscriptions must be requested again; after 1102 it
        is harmless. Nothing here blocks: data lost means the old account
        values are dropped and the stream is re-subscribed."""
        for symbol, ticker in list(self._tickers.items()):
            try:
                self.ib.cancelMktData(ticker.contract)
            except Exception:  # pragma: no cover - best effort
                log.exception("cancelMktData %s failed", symbol)
        symbols, self._tickers = list(self._tickers), {}
        if symbols:
            self.subscribe_quotes(symbols)
        self._reset_account_stream()
        self.refresh_account()

    def _ensure_funds_hooks(self) -> None:
        """Use raw reqId callbacks: accountSummaryEvent discards request identity."""
        if self._funds_hooked_ib is self.ib:
            return
        wrapper = getattr(self.ib, "wrapper", None)
        original_value = getattr(wrapper, "accountSummary", None)
        original_end = getattr(wrapper, "accountSummaryEnd", None)
        if original_value is None or original_end is None:
            raise BrokerError("funds snapshot reply hooks unavailable")

        def value(req_id, account, tag, text, currency):
            result = original_value(req_id, account, tag, text, currency)
            self._receive_funds_summary(req_id, account, tag, text, currency)
            return result

        def end(req_id):
            result = original_end(req_id)
            self._finish_funds_summary(req_id)
            return result

        wrapper.accountSummary = value
        wrapper.accountSummaryEnd = end
        self._funds_hooked_ib = self.ib

    def _cancel_funds_refresh(self, *, send_cancel: bool = True) -> bool:
        pending, self._funds_refresh = self._funds_refresh, None
        if pending is None or not send_cancel:
            return True
        try:
            self.ib.client.cancelAccountSummary(pending["req_id"])
            return True
        except Exception:
            # Never leak subscriptions or trust a download whose subscription
            # could not be closed. A new socket is required before retrying.
            self._funds_refresh_blocked = True
            self._reset_account_stream(cancel_pending=False)
            log.warning("funds snapshot cancellation failed; renewing connection")
            self.disconnect()
            return False

    def _expire_funds_refresh(self) -> None:
        pending = self._funds_refresh
        if pending is not None and time.monotonic() >= pending["deadline"]:
            self._cancel_funds_refresh()
            log.debug("funds snapshot timed out; retained timestamps remain stale")

    def refresh_funds(self) -> None:
        """Request a single nonblocking funds download without resetting the stream.

        A new raw reqId, matching field callbacks and matching End are required.
        Cached ib_insync account summaries are never evidence of freshness.
        """
        self._expire_funds_refresh()
        if self._funds_refresh_blocked or not self.ib.isConnected():
            raise BrokerError("funds snapshot requires a healthy new connection")
        if self._funds_refresh is not None:
            return
        self._ensure_funds_hooks()
        req_id = self.ib.client.getReqId()
        self._nonorder_req_ids.add(req_id)  # raw summary does not use wrapper.startReq
        self._funds_refresh = {"req_id": req_id,
                               "deadline": time.monotonic() + REQUEST_TIMEOUT_SECONDS,
                               "fields": {}, "invalid": False}
        try:
            self.ib.client.reqAccountSummary(req_id, "All", "AvailableFunds,NetLiquidation")
        except Exception:
            self._cancel_funds_refresh()
            raise

    def _receive_funds_summary(self, req_id, account, tag, value, currency) -> None:
        self._expire_funds_refresh()
        pending = self._funds_refresh
        if (pending is None or req_id != pending["req_id"] or account != self.cfg.account
                or currency not in {"USD", "BASE"} or tag not in {"AvailableFunds", "NetLiquidation"}):
            return
        # A finite zero/negative balance is valid evidence of no spending
        # power. Never retain an older positive balance after proving this.
        number = _num(value) if tag == "AvailableFunds" else _clean_price(value)
        if number is None:
            pending["invalid"] = True
            return
        self._account_sequence += 1
        pending["fields"][tag] = (number, datetime.now(timezone.utc), self._account_sequence)

    def _finish_funds_summary(self, req_id) -> None:
        self._expire_funds_refresh()
        pending = self._funds_refresh
        if pending is None or req_id != pending["req_id"]:
            return
        # End closes this download, not the classic account stream. Rejected,
        # incomplete and timed-out downloads cannot move any funds timestamp.
        if not self._cancel_funds_refresh() or pending["invalid"]:
            return
        fields = pending["fields"]
        if "AvailableFunds" not in fields or "NetLiquidation" not in fields:
            return
        funds, received, sequence = fields["AvailableFunds"]
        if sequence > self._funds_sequence:
            if funds != self._funds_value or self._funds_changed_at is None:
                self._funds_value = funds
                self._funds_changed_at = received
            self._funds_pushed_at = received
            self._funds_sequence = sequence
        netliq, _, sequence = fields["NetLiquidation"]
        if sequence > self._netliq_sequence:
            self._netliq_value = netliq
            self._netliq_sequence = sequence
        # AccountReady is deliberately not inferred from this download.

    def refresh_account(self) -> None:
        """Re-subscribe the account stream WITHOUT waiting: G2 showed that a
        blocking reqAccountUpdates on an already subscribed account never
        gets its download-end and times out after the full RequestTimeout.
        The last known values are kept with their old timestamps (staleness
        is judged from those); fresh pushes then update them."""
        client = getattr(self.ib, "client", None)
        if client is None or not hasattr(client, "reqAccountUpdates"):
            return
        client.reqAccountUpdates(False, self.cfg.account)
        client.reqAccountUpdates(True, self.cfg.account)

    def _execution_query_now(self):
        return datetime.now(timezone.utc)

    def _execution_time_observation(self, req_id, exec_id, raw, source, category, invalid):
        sample = safe_raw_time(raw, self.cfg.account)
        query = self._execution_queries.get(req_id) if source == "reqExecutions" else None
        if query is not None and query["generation"] == self._query_generation:
            query["raw_rows"].append((category, sample, bool(invalid)))
        key = (exec_id, source, category, sample, bool(invalid))
        if key not in self._raw_execution_observed:
            self._raw_execution_observed.add(key)
            self._events.append(ExecutionTimeDiagnosticEvent(exec_id, source, sample, category, bool(invalid)))

    def _install_execution_query_hooks(self):
        client, wrapper = getattr(self.ib, "client", None), getattr(self.ib, "wrapper", None)
        if client is None or wrapper is None or self._execution_hook_owner == (client, wrapper):
            return
        original_req, original_end = getattr(client, "reqExecutions", None), getattr(wrapper, "execDetailsEnd", None)
        if not callable(original_req) or not callable(original_end):
            return
        self._execution_hook_owner = (client, wrapper)
        def request(req_id, filt):
            item = {"request_id": req_id, "generation": self._query_generation,
                    "started_at": self._execution_query_now(), "completed_at": None,
                    "time_filter": str(getattr(filt, "time", "")), "raw_rows": [], "ends": 0,
                    "has_future": req_id in getattr(wrapper, "_futures", {})}
            self._execution_queries[req_id] = item
            if self._execution_contexts:
                self._execution_contexts[-1].append(item)
            return original_req(req_id, filt)
        def end(req_id):
            item = self._execution_queries.get(req_id)
            if item is not None and item["generation"] == self._query_generation:
                item["ends"] += 1
                item["completed_at"] = self._execution_query_now()
            return original_end(req_id)
        client.reqExecutions = request
        wrapper.execDetailsEnd = end

    def _log_execution_query(self, execution_filter, result, item, raw_rows, *, day_scope, complete):
        import hashlib
        import json
        role = ("account_visible_utc" if day_scope is not None else
                "legacy_client" if getattr(execution_filter, "clientId", 0) else "legacy_account")
        filt = str(getattr(execution_filter, "time", ""))
        key = (role, getattr(execution_filter, "clientId", 0), filt, self._query_generation)
        counts = {kind: sum(row[0] == kind for row in raw_rows)
                  for kind in ("zoned", "naive", "unparseable")}
        counts["returned"] = len(raw_rows)
        semantics = []
        for fill in result:
            execution = getattr(fill, "execution", fill)
            contract = getattr(fill, "contract", None)
            semantics.append([getattr(execution, field, None) for field in (
                "execId", "orderRef", "acctNumber", "clientId", "orderId", "permId",
                "side", "shares", "price", "cumQty", "avgPrice")] +
                [getattr(contract, "conId", None), getattr(contract, "symbol", None),
                 # A naive timestamp falls back to this query's receive clock;
                 # that changing fallback is not a changed broker execution.
                 None if getattr(execution, "_v6lite_time_uncertain", False) else getattr(execution, "time", None),
                 safe_raw_time(getattr(execution, "_v6lite_raw_time", None), self.cfg.account)])
        canonical = [sorted(semantics, key=lambda row: json.dumps(row, default=str)),
                     sorted(raw_rows, key=lambda row: json.dumps(row, default=str))]
        signature = hashlib.sha256(json.dumps(canonical, default=str, sort_keys=True).encode()).hexdigest()
        abnormal = (not complete or counts["unparseable"] > 0 or any(row[2] for row in raw_rows))
        self._execution_statistics.record(role=role, key=key, signature=signature,
            now=self._execution_query_now(), monotonic=time.monotonic(), counts=counts,
            sample=next((row[1] for row in raw_rows if row[1]), "-"), abnormal=abnormal)

    def _complete_executions(self, execution_filter, *, day_scope=None):
        self._require_synchronization()
        present = hasattr(self.ib, "RaiseRequestErrors")
        prior = getattr(self.ib, "RaiseRequestErrors", False)
        if present:
            self.ib.RaiseRequestErrors = True
        generation, ib = self._query_generation, self.ib
        requests = []
        # Freeze requested scope before a callback or stand-in can mutate the
        # filter; compare it with the actual native reqExecutions arguments.
        expected_filter = str(getattr(execution_filter, "time", ""))
        self._execution_contexts.append(requests)
        try:
            self._wait_query("executions")
            self._assert_query_generation(generation, ib)
            result = self.ib.reqExecutions(execution_filter)
            self._assert_query_generation(generation, ib)
            item = requests[0] if len(requests) == 1 else None
            raw_rows = item["raw_rows"] if item is not None else []
            if day_scope is None:
                self._log_execution_query(execution_filter, result, item, raw_rows, day_scope=None,
                    complete=bool(item is not None and item["ends"] == 1 and len(raw_rows) == len(result)))
                return result  # legacy recovery/release retain their old range
            day, since = day_scope
            done = self._execution_query_now()
            started = item["started_at"] if item is not None else done
            ended = item["completed_at"] if item is not None else None
            complete = bool(item is not None and item["has_future"] and item["ends"] == 1 and
                ended is not None and item["generation"] == generation == self._query_generation and
                expected_filter == item["time_filter"] == since.strftime("%Y%m%d-%H:%M:%S") and
                started.astimezone(ET).date().isoformat() == day == ended.astimezone(ET).date().isoformat() ==
                    done.astimezone(ET).date().isoformat() and started <= ended <= done and
                len(raw_rows) == len(result) and not any(row[2] or row[0] == "unparseable" for row in raw_rows))
            scope = {"day": day, "since_utc": since, "time_filter": item["time_filter"] if item else expected_filter,
                "generation": generation, "started_at": started, "completed_at": ended or done,
                "complete": complete, "request_id": item["request_id"] if item else None,
                "role": "account_visible"}
            self._log_execution_query(execution_filter, result, item, raw_rows, day_scope=day_scope, complete=complete)
            return ExecutionQueryResult(result, scope)
        except Exception as exc:
            log.warning("[EXECUTION_TIME_SUMMARY] query failed: %s", exc)
            raise
        finally:
            self._execution_contexts.pop()
            for item in requests:
                if self._execution_queries.get(item["request_id"]) is item:
                    self._execution_queries.pop(item["request_id"], None)
            self._query_last_finished["executions"] = time.monotonic()
            if present:
                self.ib.RaiseRequestErrors = prior

    def executions_today(self) -> list[FillEvent]:
        from ib_insync import ExecutionFilter

        fills = self._complete_executions(ExecutionFilter(clientId=self.client_id,
                                                      acctCode=self.cfg.account))
        known = getattr(getattr(self.ib, "wrapper", None), "fills", {}) or {}
        out = []
        for fill in fills:
            event = self._fill_event(fill, source="reqExecutions")
            if isinstance(event, AnomalyEvent):
                self._events.append(event)
                continue
            if event is not None:
                # ib_insync hands reqExecutions a NEW Fill with an empty
                # CommissionReport for executions it has already seen; the
                # report lives on the canonical Fill in wrapper.fills.
                commission = None
                for candidate in (known.get(event.exec_id), fill):
                    commission = _commission_of(candidate)
                    if commission is not None:
                        break
                if commission is not None:
                    event = FillEvent(**{**event.__dict__, "commission": commission})
                out.append(event)
        return out

    def account_executions_today(self) -> list[FillEvent]:
        """Legacy visible account history for release; range remains unchanged."""
        from ib_insync import ExecutionFilter
        return self._account_execution_rows(self._complete_executions(ExecutionFilter(acctCode=self.cfg.account)))

    def account_executions_today_scoped(self) -> list[FillEvent]:
        """Only the overnight day predicate consumes this explicit UTC bound."""
        from ib_insync import ExecutionFilter
        now = self._execution_query_now()
        midnight = datetime.combine(now.astimezone(ET).date(), day_time(0), tzinfo=ET).astimezone(timezone.utc)
        result = self._complete_executions(ExecutionFilter(acctCode=self.cfg.account,
            time=midnight.strftime("%Y%m%d-%H:%M:%S")),
            day_scope=(now.astimezone(ET).date().isoformat(), midnight))
        return ExecutionQueryResult(self._account_execution_rows(result), result.scope)

    def _account_execution_rows(self, fills):
        out, seen = [], set()
        for fill in fills:
            ex = fill.execution
            exec_id = str(getattr(ex, "execId", "") or "")
            account = getattr(ex, "acctNumber", None)
            symbol = str(getattr(fill.contract, "symbol", "") or "").upper()
            side_code = getattr(ex, "side", None)
            quantity = _int_or_none(getattr(ex, "shares", None))
            price = _clean_price(getattr(ex, "price", None))
            occurred_at = _aware(getattr(ex, "time", None))
            if (account != self.cfg.account or not exec_id or exec_id in seen or not symbol or
                    side_code not in {"BOT", "SLD"} or quantity is None or price is None or occurred_at is None):
                raise BrokerError("incomplete account execution evidence")
            seen.add(exec_id)
            out.append(FillEvent(exec_id, "", symbol, Side.BUY if side_code == "BOT" else Side.SELL,
                quantity, price, occurred_at, _int_or_none(getattr(ex, "orderId", None)),
                _int_or_none(getattr(ex, "permId", None)), time_source="reqExecutions",
                time_uncertain=bool(getattr(ex, "_v6lite_time_uncertain", False)),
                con_id=_int_or_none(getattr(fill.contract, "conId", None)),
                raw_time=safe_raw_time(getattr(ex, "_v6lite_raw_time", None), self.cfg.account)))
        return out

    def drain_events(self) -> list[BrokerEvent]:
        out, self._events = self._events, []
        return out

    # ---------------- ib_insync callbacks (enqueue only) ----------------
    def _fill_event(self, fill, *, source="callback") -> FillEvent | AnomalyEvent | None:
        ex = fill.execution
        if getattr(ex, "acctNumber", self.cfg.account) != self.cfg.account:
            log.error("execution for foreign account ignored: %s", ex.execId)
            return None
        shares = float(ex.shares)
        if not math.isfinite(shares) or shares <= 0 or shares != int(shares):
            # never round: an unexplained quantity must stop new risk, not be "fixed"
            return AnomalyEvent(f"non_integer_execution:{ex.execId}",
                                f"{fill.contract.symbol} shares={shares} orderRef={ex.orderRef}")
        ref = ex.orderRef or ""
        return FillEvent(
            exec_id=str(ex.execId),
            intent_id=ref if ref.startswith(ORDER_REF_PREFIX) else "",
            symbol=fill.contract.symbol.upper(),
            side=Side.BUY if ex.side == "BOT" else Side.SELL,
            quantity=int(round(shares)),
            price=float(ex.price),
            time=_aware(ex.time) or datetime.now(timezone.utc),
            broker_order_id=ex.orderId or None,
            perm_id=ex.permId or None,
            time_source=source,
            time_uncertain=bool(getattr(ex, "_v6lite_time_uncertain", False)) or _aware(ex.time) is None,
            con_id=_int_or_none(getattr(fill.contract, "conId", None)),
            raw_time=safe_raw_time(getattr(ex, "_v6lite_raw_time", None), self.cfg.account),
        )

    def _on_exec(self, trade, fill) -> None:
        event = self._fill_event(fill)
        if event is not None:
            self._dispatch_urgent(event)

    def _on_commission(self, trade, fill, report) -> None:
        commission = _commission_of(SimpleNamespace(commissionReport=report))
        if commission is not None:
            self._dispatch_urgent(CommissionEvent(report.execId, commission), wake=False)

    def _status_identity(self, trade) -> OpenOrder | None:
        order, status = trade.order, trade.orderStatus
        contract = getattr(trade, "contract", None)
        if contract is None:
            return None
        oid, perm = _int_or_none(getattr(order, "orderId", None)), _int_or_none(getattr(order, "permId", None))
        client = getattr(order, "clientId", None)
        if (not oid or not perm or getattr(status, "orderId", None) != oid or
                getattr(status, "permId", None) != perm or getattr(status, "clientId", None) != client or
                getattr(contract, "secType", None) != "STK" or getattr(contract, "currency", None) != "USD"):
            return None
        filled, remaining = _num(status.filled), _num(status.remaining)
        total = _int_or_none(getattr(order, "totalQuantity", None))
        if (filled is None or remaining is None or min(filled, remaining) < 0 or
                not filled.is_integer() or not remaining.is_integer() or
                total is None or total <= 0 or filled + remaining > total):
            return None
        return OpenOrder(str(order.orderRef or ""), oid, str(contract.symbol).upper(),
                         status.status, filled, remaining, str(order.action), True, perm,
                         client, _int_or_none(getattr(contract, "conId", None)), str(order.orderType),
                         str(order.tif), _clean_price(getattr(order, "auxPrice", None)),
                         _identity_limit(order),
                         getattr(order, "outsideRth", None), str(getattr(order, "account", "")), total,
                         _broker_echo_lmt(order))

    def _terminal_status_identity(self, trade) -> OpenOrder | None:
        """Keep a terminal hard tuple even if mutable order fields were cleared.

        This separate payload never passes the strict working-order validator.
        The engine may attribute a diagnostic, but cannot grant it authority.
        """
        order, status = trade.order, trade.orderStatus
        contract = getattr(trade, "contract", None)
        if contract is None or status.status not in {"Cancelled", "ApiCancelled", "Inactive"}:
            return None
        oid, perm = _int_or_none(getattr(order, "orderId", None)), _int_or_none(getattr(order, "permId", None))
        client = getattr(order, "clientId", None)
        status_oid = _int_or_none(getattr(status, "orderId", None))
        status_perm = _int_or_none(getattr(status, "permId", None))
        status_client = getattr(status, "clientId", None)
        if ((oid and status_oid and status_oid != oid) or
                (perm and status_perm and status_perm != perm) or
                (client is not None and status_client is not None and status_client != client) or
                getattr(contract, "secType", None) not in (None, "", "STK") or
                getattr(contract, "currency", None) not in (None, "", "USD")):
            return None  # populated hard contradictions are not display attribution
        filled, remaining = _num(status.filled), _num(status.remaining)
        total_raw = _num(getattr(order, "totalQuantity", None))
        if (filled is None or remaining is None or total_raw is None or
                min(filled, remaining, total_raw) < 0 or
                not all(value.is_integer() for value in (filled, remaining, total_raw)) or
                (total_raw > 0 and filled + remaining > total_raw)):
            return None
        return OpenOrder(str(order.orderRef or ""), oid, str(contract.symbol).upper(),
                         status.status, filled, remaining, str(getattr(order, "action", "")), True, perm,
                         client, _int_or_none(getattr(contract, "conId", None)), str(getattr(order, "orderType", "")),
                         str(getattr(order, "tif", "")), _clean_price(getattr(order, "auxPrice", None)),
                         _identity_limit(order),
                         getattr(order, "outsideRth", None), str(getattr(order, "account", "")), int(total_raw),
                         _broker_echo_lmt(order))

    def _install_completed_decode_guard(self):
        decoder = getattr(getattr(self.ib, "client", None), "decoder", None)
        if decoder is None or not callable(getattr(decoder, "handlers", {}).get(101)):
            return
        generation = self._query_generation
        if self._completed_decoder_owner is decoder and self._completed_decoder_generation == generation:
            return
        original = (self._completed_decode_original if self._completed_decoder_owner is decoder
                    else decoder.handlers[101])
        self._completed_decoder_owner = decoder
        self._completed_decoder_generation = generation
        self._completed_decode_original = original
        def decode(fields):
            if generation != self._query_generation or self._completed_decoder_owner is not decoder:
                return
            current = self._req_sent["completedOrders"] > self._req_ended["completedOrders"]
            key = (generation, self._req_sent["completedOrders"])
            if current:
                self._completed_decode_counts[key] = self._completed_decode_counts.get(key, 0) + 1
                if len(self._completed_decode_counts) > 8:
                    self._completed_decode_counts.pop(next(iter(self._completed_decode_counts)))
            context = {'fields': tuple(fields), 'raw_order': None, 'generation': generation, 'key': key}
            parse = decoder.parse
            def capture_parse(obj):
                if type(obj).__name__ == 'Order':
                    context['raw_order'] = dict(vars(obj))
                return parse(obj)
            self._completed_decode_contexts.append(context)
            decoder.parse = capture_parse
            try:
                return original(fields)
            except Exception as exc:
                # SDK.interpret otherwise swallows this and can return a partial
                # successful list at End, while also logging the entire account row.
                if self._req_sent["completedOrders"] > self._req_ended["completedOrders"]:
                    self._completed_invalid_rounds.add((generation, self._req_sent["completedOrders"]))
                log.warning("[COMPLETED_DECODE_ERROR] generation=%s request=%s error_type=%s",
                    generation, self._req_sent["completedOrders"], type(exc).__name__)
                return None
            finally:
                decoder.parse = parse
                self._completed_decode_contexts.pop()
        decoder.handlers[101] = decode

    def _install_raw_completed_hook(self, wrapper):
        generation = self._query_generation
        if self._raw_completed_owner is wrapper and self._raw_completed_generation == generation:
            return
        original = (self._raw_completed_original if self._raw_completed_owner is wrapper
                    else getattr(wrapper, "completedOrder", None))
        if not callable(original):
            return
        self._raw_completed_owner = wrapper
        self._raw_completed_generation = generation
        self._raw_completed_original = original
        def completed_order(contract, order, state):
            if (generation != self._query_generation or self._raw_completed_owner is not wrapper or
                    getattr(self.ib, "wrapper", None) is not wrapper):
                return
            if self._req_sent["completedOrders"] > self._req_ended["completedOrders"]:
                round_key = (generation, self._req_sent["completedOrders"])
                context = self._completed_decode_contexts[-1] if self._completed_decode_contexts else None
                if context is None or context['key'] != round_key or context['raw_order'] is None:
                    self._completed_invalid_rounds.add(round_key)
                else:
                    self._raw_completed_rounds.setdefault(round_key, []).append(
                        native_completed_order(contract, order, state, tws_zone=getattr(self.ib, "TimezoneTWS", ""),
                            native_fields=context['fields'], raw_order=context['raw_order']))
                if len(self._raw_completed_rounds) > 8:
                    self._raw_completed_rounds.pop(next(iter(self._raw_completed_rounds)))
            # No StatusEvent or fill authority is emitted by this callback.
            return original(contract, order, state)
        wrapper.completedOrder = completed_order

    def _install_raw_open_order_hook(self, wrapper):
        """Capture broker fields before the SDK selectively merges a Trade."""
        generation = self._query_generation
        if self._raw_open_order_owner is wrapper and self._raw_open_order_generation == generation:
            return
        original = (self._raw_open_order_original if self._raw_open_order_owner is wrapper
                    else getattr(wrapper, "openOrder", None))
        if not callable(original):
            return  # limited offline adapter stand-ins have no native decoder
        self._raw_open_order_owner = wrapper
        self._raw_open_order_generation = generation
        self._raw_open_order_original = original
        def open_order(order_id, contract, order, state):
            # A saved callback from a retired decoder cannot enter the new
            # round or mutate its SDK cache, even if delivered after reset.
            if (generation != self._query_generation or self._raw_open_order_owner is not wrapper or
                    getattr(self.ib, "wrapper", None) is not wrapper):
                return
            from copy import deepcopy
            if getattr(order, "whatIf", False):
                return original(order_id, contract, order, state)
            at = self._execution_query_now()
            query = self._req_sent["openOrders"] > self._req_ended["openOrders"]
            key = wrapper.orderKey(order.clientId, order_id, order.permId)
            previous = wrapper.trades.get(key)
            native_filled = getattr(getattr(previous, "orderStatus", None), "filled", 0)
            filled = _num(native_filled) or 0.
            total = _num(order.totalQuantity) or 0.
            # Native openOrder carries totalQuantity but no filled/remaining.
            # Retain the last real native fill count conservatively; all
            # quantity confirmation still compares the booked executions.
            status = SimpleNamespace(status=str(state.status), orderId=order_id,
                permId=order.permId, clientId=order.clientId, filled=filled,
                remaining=max(0., total-filled), mktCapPrice=0.)
            snapshot = SimpleNamespace(contract=deepcopy(contract), order=deepcopy(order), orderStatus=status,
                _native_filled_raw=deepcopy(native_filled))
            if order_id != getattr(order, "orderId", order_id):
                raise BrokerError("native openOrder header/orderId mismatch")
            if query:
                round_key = (generation, self._req_sent["openOrders"])
                self._raw_open_order_rounds.setdefault(round_key, []).append(snapshot)
                if len(self._raw_open_order_rounds) > 8:
                    self._raw_open_order_rounds.pop(next(iter(self._raw_open_order_rounds)))
            echo = (str(order.orderRef or ""), _int_or_none(order_id), _int_or_none(order.permId),
                order.clientId, order.account, str(contract.symbol).upper(), _int_or_none(contract.conId),
                contract.secType, contract.currency, order.action, order.orderType, order.tif,
                order.outsideRth, _int_or_none(order.totalQuantity), _clean_price(order.auxPrice), _identity_limit(order))
            self._dispatch_urgent(OrderBookChangeEvent(str(contract.symbol).upper(), str(order.orderRef or ""),
                echo, at, generation, self._connection_syncing, query), wake=False)
            identity = self._status_identity(snapshot)
            if identity is not None and identity.intent_id.startswith(ORDER_REF_PREFIX):
                self._dispatch_urgent(PriceObservationEvent(identity, "openOrder", connection_snapshot=self._connection_syncing,
                    observed_at=at, socket_generation=generation, query_snapshot=query), wake=False)
            return original(order_id, contract, order, state)
        wrapper.openOrder = open_order

    def _on_open_order(self, trade) -> None:
        # The SDK's merged Trade is diagnostic only. Its omitted contract or
        # status fields cannot replace the raw native observation above.
        self._observe_prices(trade, source="openOrder")

    def _observe_prices(self, trade, *, source, error_code=0, reason="") -> None:
        identity = self._status_identity(trade)
        if identity is None and source in {"openOrder", "openOrders"}:
            order, contract, status = trade.order, getattr(trade, "contract", None), trade.orderStatus
            oid, perm = _int_or_none(getattr(order, "orderId", None)), _int_or_none(getattr(order, "permId", None))
            total = _int_or_none(getattr(order, "totalQuantity", None))
            filled, remaining = _num(getattr(status, "filled", 0)), _num(getattr(status, "remaining", 0))
            if (contract is None or getattr(contract, "secType", None) != "STK" or
                getattr(contract, "currency", None) != "USD" or not oid or not perm or not total or
                filled is None or remaining is None or min(filled, remaining) < 0 or
                not filled.is_integer() or not remaining.is_integer() or filled + remaining > total or
                getattr(status, "orderId", 0) not in (None, 0, oid) or
                getattr(status, "permId", 0) not in (None, 0, perm) or
                getattr(status, "clientId", 0) not in (None, 0, getattr(order, "clientId", None))):
                return
            identity = OpenOrder(str(getattr(order, "orderRef", "")), oid, str(contract.symbol).upper(),
                str(getattr(status, "status", "")), filled, remaining, str(getattr(order, "action", "")), True,
                perm, getattr(order, "clientId", None), _int_or_none(getattr(contract, "conId", None)),
                str(getattr(order, "orderType", "")), str(getattr(order, "tif", "")),
                _clean_price(getattr(order, "auxPrice", None)), _identity_limit(order),
                getattr(order, "outsideRth", None), str(getattr(order, "account", "")), total,
                _broker_echo_lmt(order))
        if identity is None or not identity.intent_id.startswith(ORDER_REF_PREFIX):
            return
        raw = getattr(trade.orderStatus, "mktCapPrice", None)
        cap = _stop_limit_price(raw)
        # Query openOrder/price callbacks must not wake their own next query.
        # The handler still audits and applies real contradictions immediately.
        query_snapshot = (source == "openOrders" or
            self._req_sent["openOrders"] > self._req_ended["openOrders"])
        self._dispatch_urgent(PriceObservationEvent(identity, source, cap, str(raw), error_code, reason,
            self._connection_syncing, None, self._query_generation,
            True), wake=False)  # merged SDK/cache observations cannot acknowledge a write

    def _on_status(self, trade, *, error_code=0, message=None) -> None:
        ref = trade.order.orderRef or ""
        if not ref.startswith(ORDER_REF_PREFIX):
            self._dispatch_urgent(OrderBookChangeEvent(str(trade.contract.symbol).upper(), ref), wake=False)
            return
        st = trade.orderStatus
        entry = trade.log[-1] if getattr(trade, "log", None) else None
        text = (getattr(entry, "message", "") if message is None else message)
        code = error_code or getattr(entry, "errorCode", 0)
        if not code and st.status in {"Cancelled", "ApiCancelled", "Inactive"}:
            # A plain terminal status can arrive AFTER this trade's 202 log.
            # Retain its reason; the engine still requires complete identity
            # and rules out local cancellations before calling it a refusal.
            prior = next((item for item in reversed(getattr(trade, "log", ()))
                          if getattr(item, "errorCode", 0) in {201, 202} and getattr(item, "message", "")), None)
            if prior is not None:
                code, text = prior.errorCode, prior.message
        self._observe_prices(trade, source="orderStatus", error_code=int(code or 0), reason=text)
        self._dispatch_urgent(StatusEvent(ref, st.status, float(st.filled), float(st.remaining),
                                          trade.order.orderId or None, st.permId or None, text,
                                          self._status_identity(trade), int(code or 0), True,
                                          self._terminal_status_identity(trade),
                                          _stop_limit_price(getattr(st, "mktCapPrice", None)),
                                          _broker_echo_lmt(trade.order), self._connection_syncing,
                                          self._execution_query_now(), self._query_generation,
                                          query_snapshot=self._req_sent["openOrders"] > self._req_ended["openOrders"]),
                              wake=not self._connection_syncing)

    def _on_disconnect(self) -> None:
        self._reset_account_stream(cancel_pending=False)
        self._events.append(ConnectionEvent(False, self.connection_rebuild_reason or "disconnected"))

    def _on_error(self, req_id, code, message, contract) -> None:
        if 2100 <= code < 2200:
            # Diagnostics precede classification: 2110 must retain this raw
            # audit and readable warning as well as its original loss event.
            raw_message = str(message)
            self._dispatch_urgent(BrokerNoticeEvent(req_id, code, raw_message), wake=False)
            displayed = re.sub(r"\\u([0-9a-fA-F]{4})",
                               lambda match: chr(int(match.group(1), 16)), raw_message)
            writer = log.warning if code in _CONNECTIVITY_LOST else log.info
            writer("IBKR %s %s (req %s)", code, displayed, req_id)
        pending = self._funds_refresh
        wrapper = getattr(self.ib, "wrapper", None)
        query_request = (req_id in self._nonorder_req_ids or self._subscription_symbol(req_id) is not None or
                         (pending is not None and req_id == pending["req_id"]) or
                         any(req_id in (getattr(wrapper, name, {}) or {}) for name in
                             ("_futures", "_reqId2Contract", "reqId2Subscriber", "reqId2Ticker")))
        if not query_request:
            for trade in tuple(self._trades.values()):
                if (getattr(trade.order, "orderId", None) == req_id and
                        (self._status_identity(trade) is not None or self._terminal_status_identity(trade) is not None) and
                        (contract is None or (getattr(contract, "conId", None) == trade.contract.conId and
                                              getattr(contract, "symbol", "") == trade.contract.symbol))):
                    # Terminal display identity retains reason, never strict
                    # quantity/cancellation/release authority.
                    self._on_status(trade, error_code=code, message=str(message))
                    break
        if pending is not None and req_id == pending["req_id"]:
            self._cancel_funds_refresh()
        if code in _CONNECTIVITY_LOST | _CONNECTIVITY_RESTORED:
            # Backend loss need not close the API socket: release a pending
            # summary here. Only physical disconnected skips cancellation.
            self._reset_account_stream()
        if code in _CONNECTIVITY_LOST:
            self._events.append(ConnectionEvent(False, f"{code}:{message}"))
        elif code in _CONNECTIVITY_RESTORED:
            self._events.append(ConnectionEvent(True, f"{code}:{message}"))
        else:
            # matched by REQUEST id to one of our live quote subscriptions; a
            # refusal of a history request on the same symbol is not one
            symbol = self._subscription_symbol(req_id)
            if code in _NO_MARKET_DATA and symbol:
                self._md_seq += 1
                self._no_md_subscription[symbol] = (f"{code}: {str(message)}", self._md_seq)
                self._refusal_seq[symbol] = self._md_seq
            if not 2100 <= code < 2200:
                log.info("IBKR %s %s (req %s)", code, str(message), req_id)
